# -*- coding: utf-8 -*-
r"""YOLO 检球：本项目**默认**的检出层实现（同签名对齐 `bbh.detector.detect_positions`）。

来历
----
本文件由「自训 YOLO 替换 HSV 颜色检球」专项的适配器收编而来，已改成**包内
相对导入**、权重随包内置（`bbh/weights/best.pt`），**不依赖任何外部工程**。

契约（逐一对齐 `bbh.detector.detect_positions`，下游判定器一行不改）
------------------------------------------------------------------
- 入参：`(video_path, calib, step=None, progress_cb=None, resume_frame=0,
  on_detect=None, motion=None, end_frame=None, hoop_out=None)`
- 出参：`(list[BallPos], meta)`；meta 键 `fps / duration / frame_count /
  processed_frames / video_size / pts_clock`（另附若干诊断键，下游只读白名单）
- 坐标：**标定参考帧坐标系** = 图片像素坐标 − 该时刻镜头位移
- 时钟：容器 PTS 优先，读不到回退 帧号/fps（与 HSV 实现同一套 use_pts 判据）
- `BallPos.area` = 等面积圆估算 `π/4 · bw · bh`（篮球近圆，与 HSV 的轮廓面积
  量级可比）
- `hoop_out`（v21）：筐锚点采样容器，形如 `[[t,cx,cy,w,h,conf], ...]`（画面
  像素坐标）。同一次整帧推理顺带解析 class 1，**零额外推理成本**；采样结果
  由 `bbh.hoop_track.build_correction` 转成低频校正项（见该模块注释）。

与 HSV 的语义差异只有一处：面积口径（上式）；下游阈值按量级使用，可比。

调参
----
默认值即「可用版本」（conf 0.03 + step 1 + 整帧 1024），可用环境变量覆盖：
`BBH_YOLO_WEIGHTS / BBH_YOLO_IMGSZ / BBH_YOLO_CONF / BBH_YOLO_STEP /
BBH_YOLO_DEVICE / BBH_YOLO_CROP`。也可直接 `configure(**kw)`。
筐锚点单开关：`BBH_HOOP=0`（关掉后 `classes` 只留球类，逐条回 v20）。
"""
import math
import os

import cv2

from . import hoop_track
from .detector import (BAND_RATIO, RIM_Y_RATIO, STEP, VideoOpenError,
                       _scaled_rect, roi_rect, safe_fps)
from .models import BallPos
from .motion import SAMPLE_EVERY

_DEFAULT_WEIGHTS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "weights", "best.pt")


def _env(name, cast, default):
    v = os.environ.get(name)
    if v is None or str(v).strip() == "":
        return default
    try:
        return cast(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------- 可调参数
CFG = {
    "weights": _env("BBH_YOLO_WEIGHTS", str, _DEFAULT_WEIGHTS),
    "imgsz": _env("BBH_YOLO_IMGSZ", int, 1024),
    "conf": _env("BBH_YOLO_CONF", float, 0.03),   # 小球召回优先（可用版本取 0.03）
    "iou": 0.7,
    "ball_class": 0,      # dataset.yaml: 0=ball 1=hoop
    # 筐锚点（bbh/hoop_track.py）：同一次整帧推理顺带取 hoop 类，采样后交
    # 管线的后置校正项计算。零额外推理成本（模型本来就在整帧上跑）。
    "hoop_class": 1,
    "hoop_track": _env("BBH_HOOP", lambda v: str(v).strip().lower()
                       not in ("0", "false", "off", "no"), hoop_track.HOOP_ENABLE),
    # 精度：16=FP16（原 half=True 的等价写法；ultralytics 8.4 起 half/int8 已并入
    # 统一的 quantize，传 half 会每帧打一条弃用警告）。None=FP32，8=INT8。
    "quantize": _env("BBH_YOLO_QUANTIZE", int, 16),
    "device": _env("BBH_YOLO_DEVICE", int, 0),
    "crop": _env("BBH_YOLO_CROP", str, "full"),    # "full"=整帧（训练即此构图）| "roi"=筐周 ROI
    "crop_imgsz": 640,    # crop="roi" 时的 imgsz
    "band_filter": False,  # 兼容 HSV 的"筐身条带忽略"——YOLO 不需要
    # 采样步长：1 = 每帧都看（缓解判定器「同侧 ≥2 点且跨度 ≥0.2s」证据不足降档）
    "step": _env("BBH_YOLO_STEP", int, 1),
}

_MODEL = None
_MODEL_KEY = None


def configure(**kw):
    """覆盖检测参数（未给的保持原值）。weights 变化时模型自动重载。"""
    unknown = set(kw) - set(CFG)
    if unknown:
        raise KeyError(f"未知参数：{sorted(unknown)}")
    CFG.update(kw)


def _model():
    global _MODEL, _MODEL_KEY
    from ultralytics import YOLO      # 懒导入：不跑 YOLO 的流程无需装 torch
    key = (CFG["weights"], CFG["quantize"], CFG["device"])
    if _MODEL is None or key != _MODEL_KEY:
        w = CFG["weights"]
        if not os.path.exists(w):
            raise FileNotFoundError(f"YOLO 权重不存在：{w}")
        _MODEL = YOLO(w)
        _MODEL_KEY = key
    return _MODEL


# ------------------------------------------------------------------ 主流程
def detect_positions(video_path: str, calib, step: int | None = None,
                     progress_cb=None, resume_frame: int = 0, on_detect=None,
                     motion=None, end_frame=None, hoop_out: list | None = None):
    """返回 (positions, meta)。语义见模块 docstring。

    hoop_out：筐锚点采样的外部容器（断点续跑由检查点预置——采样边检边 append，
    检查点存的就是全量，续跑不丢前半段）。None=内部自建。"""
    if step is None:
        step = int(CFG.get("step") or STEP)
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise VideoOpenError(f"无法打开视频: {video_path}")
    fps = safe_fps(cap.get(cv2.CAP_PROP_FPS))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))

    model = _model()
    crop = CFG["crop"]
    imgsz = CFG["crop_imgsz"] if crop == "roi" else CFG["imgsz"]
    # 筐口几何（仅 band_filter 用得到；判定几何全部在下游）
    hx, hy, hw, hh = _scaled_rect(calib, frame_w, frame_h)
    rim_y = hy + hh * RIM_Y_RATIO
    band = hh * BAND_RATIO
    rx, ry, rw, rh = roi_rect(calib, frame_w, frame_h) if crop == "roi" else (0, 0, 0, 0)

    positions = []
    n_empty = 0        # "无球检出"的帧数（诊断：真漏检 vs 真无球）
    n_multi = 0        # 单帧多球候选（诊断）
    fi = resume_frame

    # 筐采样：一次采样器贯穿本段（续跑的新采样器从空开始，采样点天然稀疏，
    # 缺头几处只让插值区间变长）
    hoop = (hoop_track.HoopSampler(CFG["hoop_class"], storage=hoop_out,
                                  ref_size=(hw, hh))
            if CFG.get("hoop_track") else None)

    pred_kw = {"imgsz": imgsz, "conf": CFG["conf"], "iou": CFG["iou"],
               "classes": ([CFG["ball_class"], CFG["hoop_class"]]
                           if hoop is not None else [CFG["ball_class"]]),
               "verbose": False,
               "device": CFG["device"], "quantize": CFG["quantize"]}
    use_pts = None
    try:
        if resume_frame > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, resume_frame)
        while True:
            if end_frame is not None and fi >= end_frame:
                break
            if not cap.grab():
                break
            if fi % step == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    break
                msec = cap.get(cv2.CAP_PROP_POS_MSEC)
                if use_pts is None and fi > resume_frame:
                    use_pts = msec > 0
                t = msec / 1000.0 if use_pts else fi / fps

                dx = dy = 0.0
                if motion is not None:
                    if fi % SAMPLE_EVERY == 0:
                        motion.update(frame, t)      # 冻结位移表时是 no-op
                    dx, dy = motion.shift(t)

                off_x, off_y, src = 0, 0, frame
                if crop == "roi":
                    # ROI 随位移平移 + 越界裁剪（与 HSV 实现同款保护）
                    sx0, sy0 = int(round(rx + dx)), int(round(ry + dy))
                    sx1, sy1 = sx0 + rw, sy0 + rh
                    if sx0 < 0:
                        sx1 -= sx0
                        sx0 = 0
                    if sy0 < 0:
                        sy1 -= sy0
                        sy0 = 0
                    if sx1 > frame_w:
                        sx0 -= sx1 - frame_w
                        sx1 = frame_w
                    if sy1 > frame_h:
                        sy0 -= sy1 - frame_h
                        sy1 = frame_h
                    if sx1 - sx0 <= 0 or sy1 - sy0 <= 0:
                        fi += 1
                        continue
                    src = frame[sy0:sy1, sx0:sx1]
                    off_x, off_y = sx0, sy0

                res = model.predict(src, **pred_kw)[0]
                if hoop is not None:
                    hoop.feed(res, t, fi, off_x, off_y)   # 筐位锚点（画面像素）
                best = _best_ball(res, CFG["ball_class"])
                if best is None:
                    n_empty += 1
                else:
                    (bx1, by1, bx2, by2), _conf, extra = best
                    if extra:
                        n_multi += 1
                    bw, bh = bx2 - bx1, by2 - by1
                    if bw > 0 and bh > 0:
                        cx = off_x + (bx1 + bx2) / 2.0
                        cy = off_y + (by1 + by2) / 2.0
                        x = cx - dx          # 图片像素 → 标定参考帧坐标系
                        y = cy - dy
                        area = math.pi / 4.0 * bw * bh   # 等面积圆估算
                        if (not CFG["band_filter"]) or abs(y - rim_y) >= band:
                            bp = BallPos(t=t, x=x, y=y, area=area, fi=fi)
                            positions.append(bp)
                            if on_detect:
                                on_detect(bp)
            fi += 1
            if progress_cb and fi % 300 == 0:
                progress_cb(fi, total)
    finally:
        cap.release()

    meta = {"fps": fps, "duration": total / fps, "frame_count": total,
            "processed_frames": fi, "video_size": (frame_w, frame_h),
            "pts_clock": use_pts,
            "detector": "yolo", "weights": os.path.basename(CFG["weights"]),
            "imgsz": imgsz, "conf": CFG["conf"], "crop": crop,
            "n_detected": len(positions), "n_empty_frames": n_empty,
            "n_multi_candidate": n_multi,
            # 筐采样（画面像素坐标 [t,cx,cy,w,h,conf]）——管线据此算后置校正
            # 项后即从 meta 摘走（多段各段自存，顶层 meta 只留总计数）
            "hoop_samples": hoop.samples if hoop is not None else [],
            "n_hoop_samples": len(hoop.samples) if hoop is not None else 0}
    return positions, meta


def _best_ball(res, ball_class: int | None = None):
    """单帧结果 → ((x1,y1,x2,y2), conf, n_extra) 或 None（最高置信度那颗）。

    ball_class 非 None 时只在该类内选——启用筐锚点后同一帧还带 class 1 的框，
    不过滤会把筐当球（面积/位置全错）。None=旧行为（不分类别，仅兼容旧调用）。"""
    boxes = getattr(res, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return None
    xyxy = boxes.xyxy.cpu().numpy()
    conf = boxes.conf.cpu().numpy()
    n = len(boxes)
    if ball_class is None:
        k = int(conf.argmax())
        return (tuple(float(v) for v in xyxy[k]), float(conf[k]), n - 1)
    cls = boxes.cls.cpu().numpy().astype(int)
    idx = [i for i in range(n) if cls[i] == int(ball_class)]
    if not idx:
        return None
    k = max(idx, key=lambda i: conf[i])
    return (tuple(float(v) for v in xyxy[k]), float(conf[k]), len(idx) - 1)


def hoop_center_of(frame_bgr, calib_rect=None, conf_min: float | None = None):
    """单张图（标定参考帧）跑一次推理取筐框 → (cx, cy, w, h, conf) 或 None。

    用作筐锚点的**绝对基准**：检测期间采到的筐心减去它，可抵消「模型框中心
    vs 用户手点框中心」的固定偏置（实测 6.9~8.2px，段内恒定）。每个场次
    只需一次（结果随 positions.json 落盘，重放不再跑推理）。
    calib_rect：标定筐口 (x,y,w,h)——给了就按尺寸最接近选筐（同帧可能有
    第二个"像筐"的东西）。"""
    if frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
        return None
    res = _model().predict(
        frame_bgr, imgsz=CFG["imgsz"], conf=CFG["conf"], iou=CFG["iou"],
        classes=[CFG["hoop_class"]], verbose=False,
        device=CFG["device"], quantize=CFG["quantize"])[0]
    ref_size = (calib_rect[2], calib_rect[3]) if calib_rect else None
    got = hoop_track.pick_hoop(res, CFG["hoop_class"],
                               hoop_track.CONF_MIN if conf_min is None
                               else conf_min, ref_size)
    return None if got is None else tuple(round(v, 2) for v in got)


def install(pipeline_module=None):
    """把本实现挂到 `bbh.pipeline.detect_positions`（可选；默认走 detector_switch）。"""
    if pipeline_module is None:
        import bbh.pipeline as pipeline_module
    pipeline_module.detect_positions = detect_positions
    return pipeline_module
