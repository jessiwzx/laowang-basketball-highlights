"""流式检测：筐口 ROI 内颜色分割找球，输出位置序列（内存流，不落盘）。

球块判据三层：HSV 颜色 → 面积上下限 → 形状（占最小外接圆比例，篮球近圆；
篮网/手臂/栏杆等细长或不规则色块被排除）。"""
import math
import time

import cv2
import numpy as np

from .models import BallPos, Calibration
from .motion import SAMPLE_EVERY

STEP = 2                # 每 2 帧处理 1 帧
# 进度上报节拍（2026-11-17）：首次**立即上报**，之后「≥REPORT_FRAMES 帧 或
# ≥REPORT_SEC 秒」先到先报。旧的 `fi % 300 == 0` 有两个副作用：①前 300 帧
# 界面恒显「0/0 帧 (0%)」，用户以为卡死（远端实测干等 10 分钟）；②取消检查
# 搭在同一节拍上（pipeline.on_progress），慢机器上点取消要等 300 帧才响应。
# 回调只读、不参与判定——改节拍对检出结果零影响。两个检测器共用这两个值。
REPORT_FRAMES = 30
REPORT_SEC = 2.0
# ROI 倍率（相对筐口宽 w/高 h）。2026-08-19 从 UP=2/DOWN=3/SIDE=1 扩大——
# 旧值下 1080p 里 ROI 仅 174×115px（约画面 1%），标定时抽一帧球几乎总在
# 框外（0 命中提示逼用户反复跳时间点找球贴筐的帧）。扩大后覆盖篮下禁区，
# 顺带让球在弧顶/落地的轨迹点进框（上方证据更全、置信更稳）。
# 误报不随之变多的依据：进球判定靠"穿越筐口平面"，穿越点必在筐口正附近
# （原 ROI 内），远处新增点只补充证据、不产生新穿越；静止链/下落校验/
# 几何门槛照常兜底。
ROI_UP = 5.0            # ROI 上方延伸（相对筐高，含球飞行弧顶）
ROI_DOWN = 8.0          # ROI 下方延伸（含篮网与落地反弹）
ROI_SIDE = 2.5          # ROI 左右延伸（覆盖篮下禁区）
RIM_Y_RATIO = 0.35      # 与 goal_decider 保持一致
BAND_RATIO = 0.25
MIN_AREA = 9            # 球最小像素面积（3x3 起）
MAX_AREA_RATIO = 0.5    # 球最大面积（相对筐宽平方）
CIRC_FILL_MIN = 0.45    # 占最小外接圆比例下限（圆≈1；细长条/网<0.3）


class VideoOpenError(Exception):
    pass


def safe_fps(fps: float) -> float:
    """CAP_PROP_FPS 异常值（0/负数——实测不可读容器返回 -1.0，为真值骗过 or）
    统一回退 30.0，避免脏帧率贯穿时长/续跑换算。"""
    return fps if fps > 0 else 30.0


def _scaled_rect(calib: Calibration, frame_w: int, frame_h: int):
    """标定分辨率 ≠ 视频分辨率时按比例缩放筐口矩形。"""
    x, y, w, h = calib.hoop_rect
    cw, ch = calib.frame_size
    if (cw, ch) == (frame_w, frame_h):
        return x, y, w, h
    sx, sy = frame_w / cw, frame_h / ch
    return round(x * sx), round(y * sy), round(w * sx), round(h * sy)


def roi_rect(calib: Calibration, frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
    """筐口外扩的检测 ROI (x, y, w, h)；退化（不在画面内）抛 ValueError。
    calibration.overlay_preview 用同一函数，保证预览所见即检测所查。"""
    hx, hy, hw, hh = _scaled_rect(calib, frame_w, frame_h)
    rx = max(0, int(hx - hw * ROI_SIDE))
    ry = max(0, int(hy - hh * ROI_UP))
    rw = min(frame_w - rx, int(hw * (1 + 2 * ROI_SIDE)))
    rh = min(frame_h - ry, int(hh * (ROI_UP + ROI_DOWN)))
    if rw <= 0 or rh <= 0:
        raise ValueError("筐口矩形不在画面内，标定分辨率与视频不符")
    return rx, ry, rw, rh


def ball_blobs(mask: np.ndarray, max_area: float) -> list[tuple[float, np.ndarray,
                                                               float, float]]:
    """清洗后的 ROI mask 里找"像球"的连通块 → [(面积, 轮廓, cx, cy)]（ROI 局部坐标）。
    判据：MIN_AREA ≤ 面积 ≤ max_area，且占最小外接圆比例 ≥ CIRC_FILL_MIN
    ——篮球近圆（比例≈1），篮网/手臂/栏杆等细长或不规则块（<0.3）被排除。
    calibration 的预览/命中计数同用此函数，保证所见即所查。"""
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in contours:
        a = cv2.contourArea(c)
        if not (MIN_AREA <= a <= max_area):
            continue
        (_, _), r = cv2.minEnclosingCircle(c)
        if r <= 0 or a / (math.pi * r * r) < CIRC_FILL_MIN:
            continue
        m = cv2.moments(c)
        if m["m00"] == 0:
            continue
        out.append((a, c, m["m10"] / m["m00"], m["m01"] / m["m00"]))
    return out


def detect_positions(video_path: str, calib: Calibration, step: int = STEP,
                     progress_cb=None, resume_frame: int = 0, on_detect=None,
                     motion=None, end_frame=None, hoop_out: list | None = None):
    """返回 (positions, meta)。meta: fps/duration/frame_count/processed_frames/video_size。
    on_detect(ballpos)：每检出一个点实时回调（检查点续跑需中途持久化检出）。
    motion：bbh.motion.MotionTracker（标定参考帧配准）。传入后检测 ROI 随
    镜头位移平移（被碰/漂移不再把筐口推出检测区），输出坐标已减去位移
    （回到标定坐标系，判定器全程用同一套标定几何）；None=不做位移校正。
    end_frame：处理到该帧号（不含）为止——多段标定的段尾边界（段 i 扫
    [t0,t1) 由 resume_frame+end_frame 拼出）；None=到片尾（旧行为）。
    hoop_out：筐锚点采样容器——**本实现不用**（HSV 链没有 hoop 类），保留
    参数只为与 bbh.detector_yolo.detect_positions 签名对齐，切换检出器时
    管线调用不必分叉。"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise VideoOpenError(f"无法打开视频: {video_path}")
    fps = safe_fps(cap.get(cv2.CAP_PROP_FPS))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))

    rx, ry, rw, rh = roi_rect(calib, frame_w, frame_h)
    hx, hy, hw, hh = _scaled_rect(calib, frame_w, frame_h)
    rim_y = hy + hh * RIM_Y_RATIO
    band = hh * BAND_RATIO
    max_area = hw * hw * MAX_AREA_RATIO

    try:
        if resume_frame > 0:
            cap.set(cv2.CAP_PROP_POS_FRAMES, resume_frame)

        positions: list[BallPos] = []
        hsv_lo = np.array(calib.hsv_low, np.uint8)
        hsv_hi = np.array(calib.hsv_high, np.uint8)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        fi = resume_frame
        use_pts = None    # None=未判定（PTS=0 的首帧无法区分"正常"与"后端不支持"）
        if progress_cb:                  # 进循环前先报一次：界面立刻拿到总帧数
            progress_cb(fi, total)
        last_rep_fi, last_rep_t = fi, time.monotonic()
        while True:
            if end_frame is not None and fi >= end_frame:
                break
            if not cap.grab():
                break
            if fi % step == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    break
                # 帧时间取容器 PTS（剪辑 ffmpeg -ss 也按 PTS 定位，两钟
                # 必须同源）。可变帧率视频的 帧号/fps 与 PTS 实测最多差
                # ~3s（stodownload 系列前半段 +2.7~3.0s）——旧钟导致标红
                # 偏早、进球被挤出切片末尾。后端读不出 PTS（恒 0）时
                # 整体回退 帧号/fps，行为同旧版。
                msec = cap.get(cv2.CAP_PROP_POS_MSEC)
                if use_pts is None and fi > resume_frame:
                    use_pts = msec > 0
                t = msec / 1000.0 if use_pts else fi / fps
                dx = dy = 0.0
                if motion is not None:
                    if fi % SAMPLE_EVERY == 0:
                        motion.update(frame, t)
                    dx, dy = motion.shift(t)
                # ROI 随位移平移，越界裁剪（漂移把 ROI 推出画面时保住交集）
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
                if sx1 - sx0 > 0 and sy1 - sy0 > 0:
                    roi = frame[sy0:sy1, sx0:sx1]
                    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
                    mask = cv2.inRange(hsv, hsv_lo, hsv_hi)
                    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
                    blobs = ball_blobs(mask, max_area)
                    if blobs:
                        a, c, bx, by = max(blobs, key=lambda b: b[0])
                        # ROI 局部坐标 → 全图实测坐标 − 位移 = 标定坐标系
                        x = sx0 + bx - dx
                        y = sy0 + by - dy
                        if abs(y - rim_y) >= band:  # 筐身条带内的静态橙色忽略
                            bp = BallPos(t=t, x=x, y=y, area=a, fi=fi)
                            positions.append(bp)
                            if on_detect:
                                on_detect(bp)
            fi += 1
            if progress_cb:
                now = time.monotonic()
                if (fi - last_rep_fi >= REPORT_FRAMES
                        or now - last_rep_t >= REPORT_SEC):
                    progress_cb(fi, total)
                    last_rep_fi, last_rep_t = fi, now
    finally:
        cap.release()
    meta = {"fps": fps, "duration": total / fps, "frame_count": total,
            "processed_frames": fi, "video_size": (frame_w, frame_h),
            # True=PTS 计时；False=实测后端读不出（才警告）；None=全程无检出，
            # 无从判定（此时另有"未检测到球"警告，无需重复提示）
            "pts_clock": use_pts}
    return positions, meta
