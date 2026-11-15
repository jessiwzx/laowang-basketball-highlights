"""标定：从用户点击计算球 HSV 范围与筐口矩形，生成预览叠加图。"""
import glob
import json
import math
import os

import cv2
import numpy as np

from .detector import (CIRC_FILL_MIN, MAX_AREA_RATIO, MIN_AREA, _scaled_rect,
                       ball_blobs, roi_rect)
from .goal_decider import RIM_Y_RATIO
from .models import CalibSegment, Calibration, ref_image_path

HSV_MARGIN = (4, 40, 40)      # 点击像素向四周放宽的余量
# 筐口矩形在点击点左右两侧的放宽比例。旧 15% 时代筐口两端只对应
# cross_off ±0.38，稀释了 goal_decider 几何阈值的语义（两端应为 ≈±0.45）；
# 2026-08-19 误判调优收紧至 5%（既有缓存须重新标定才生效）。
H_MARGIN_RATIO = 0.05


def hsv_range_from_clicks(frame: np.ndarray, clicks: list[tuple[int, int]]):
    """clicks: [(x, y)]（帧坐标）。返回覆盖所有点击像素±余量的 HSV 上下界。"""
    h_img, w_img = frame.shape[:2]
    for x, y in clicks:
        if not (0 <= x < w_img and 0 <= y < h_img):
            raise ValueError(f"点击坐标越界: ({x}, {y})，帧尺寸 ({w_img}, {h_img})")
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    samples = [hsv[y, x] for x, y in clicks]
    low = [max(0, int(min(s[i] for s in samples)) - HSV_MARGIN[i]) for i in range(3)]
    high = [min(u, int(max(s[i] for s in samples)) + HSV_MARGIN[i])
            for i, u in enumerate((179, 255, 255))]
    return tuple(low), tuple(high)


def hoop_rect_from_rim_points(p1: tuple[float, float], p2: tuple[float, float]):
    """筐口两端点击 → 筐口矩形 (x, y, w, h)。y 取两点均值，矩形上下延伸。"""
    x1, x2 = sorted((p1[0], p2[0]))
    if x2 - x1 <= 0:
        raise ValueError(f"筐口两端点击的 x 坐标几乎相同: {p1[0]}, {p2[0]}")
    rim_y = (p1[1] + p2[1]) / 2
    w_span = x2 - x1
    pad = w_span * H_MARGIN_RATIO
    x = x1 - pad
    w = w_span + 2 * pad
    h = w * 0.4
    y = rim_y - RIM_Y_RATIO * h   # 使 rim_y 落在矩形 RIM_Y_RATIO 高度处（与 goal_decider 一致）
    return (round(x), round(y), round(w), round(h))


def hsv_zero_diagnosis(frame: np.ndarray, calib: Calibration) -> str:
    """ROI 0 命中时逐层定位：颜色通道（H/S/V）→ 形态学清洗 → 面积/形状过滤。
    返回给用户看的一两句诊断。"""
    h_img, w_img = frame.shape[:2]
    rx, ry, rw, rh = roi_rect(calib, w_img, h_img)
    roi = frame[ry:ry + rh, rx:rx + rw]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(calib.hsv_low), np.array(calib.hsv_high))
    raw = int((mask > 0).sum())
    lo, hi = calib.hsv_low, calib.hsv_high
    h, s, v = hsv.reshape(-1, 3).T
    mh = (h >= lo[0]) & (h <= hi[0])
    if raw == 0:
        if not mh.any():
            return (f"ROI 内没有任何像素的色相 H 落在 [{lo[0]}, {hi[0]}]——"
                    "球色不在 H 范围，滑块解决不了，请在参考帧重新点球后「生成标定」。")
        sub_s, sub_v = s[mh], v[mh]
        blockers = []
        if not (mh & (s >= lo[1])).any():
            blockers.append(f"S 下限 {lo[1]} 高于这些像素的最大 S {int(sub_s.max())}")
        if not (mh & (v >= lo[2])).any():
            blockers.append(f"V 下限 {lo[2]} 高于这些像素的最小 V {int(sub_v.min())}")
        return (f"H 范围内有 {int(mh.sum())} 像素，但卡在：" + "；".join(blockers)
                + "。往左调对应滑块，或重新「生成标定」。")
    # 颜色有命中但清洗后没有球块 → 面积/形状层的问题
    opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                              cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    contours, _ = cv2.findContours(opened, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return (f"颜色命中 {raw} 像素，但形态学清洗后没有连通块——色块太碎/太小，"
                "球可能太小或被遮挡，建议换更近、球更清晰的画面。")
    _, _, hw, _ = _scaled_rect(calib, w_img, h_img)
    c_max = max(contours, key=cv2.contourArea)
    a_max = cv2.contourArea(c_max)
    (_, _), r = cv2.minEnclosingCircle(c_max)
    fill = a_max / (math.pi * r * r) if r > 0 else 0.0
    if a_max < MIN_AREA:
        return (f"颜色命中 {raw} 像素，最大块面积 {a_max:.0f} 低于最小 {MIN_AREA}——"
                "球太小或被遮挡，建议换更近、球更清晰的画面。")
    if fill < CIRC_FILL_MIN:
        return (f"颜色命中 {raw} 像素，最大块面积 {a_max:.0f} 但占外接圆仅 {fill:.2f}"
                f"（需 ≥{CIRC_FILL_MIN}）——是细长/不规则块（网/手臂），不像球。"
                "黄框（筐口区域）内可能没有球，请换一帧再试。")
    if a_max > hw * hw * MAX_AREA_RATIO:
        return (f"颜色命中 {raw} 像素，最大块面积 {a_max:.0f} 超过上限"
                f"（{hw * hw * MAX_AREA_RATIO:.0f}）——点击的筐口可能偏小，"
                "或黄框内有大片与球同色的物体。")
    return f"颜色命中 {raw} 像素，最大块（面积 {a_max:.0f}）未通过过滤。"


def _detect_like_mask(frame: np.ndarray, calib: Calibration):
    """与 detect_positions 完全同路的球块提取：ROI 切片 → HSV → 形态学开 →
    面积/形状过滤。返回 (rx, ry, rw, rh, 清洗后的球块 mask)。
    预览与命中计数都用它，所见即所查（篮网等细长块不会显示为绿）。"""
    h_img, w_img = frame.shape[:2]
    rx, ry, rw, rh = roi_rect(calib, w_img, h_img)
    roi = frame[ry:ry + rh, rx:rx + rw]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(calib.hsv_low), np.array(calib.hsv_high))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    _, _, hw, _ = _scaled_rect(calib, w_img, h_img)
    clean = np.zeros_like(mask)
    for _a, c, _cx, _cy in ball_blobs(mask, hw * hw * MAX_AREA_RATIO):
        cv2.drawContours(clean, [c], -1, 255, -1)
    return rx, ry, rw, rh, clean


def count_roi_hits(frame: np.ndarray, calib: Calibration) -> int:
    """当前设置在 ROI 内识别为球的像素数（与检测同路：颜色+面积+形状全过）。
    预览反馈用：0 = 连球都没识别到（阈值过严）。"""
    _, _, _, _, clean = _detect_like_mask(frame, calib)
    return int((clean > 0).sum())


def overlay_preview(frame: np.ndarray, calib: Calibration) -> np.ndarray:
    """叠加图：**检测 ROI 内**、且通过检测全部过滤（颜色/面积/形状）的球块
    染成绿色；画出 ROI 边界（黄）、筐口矩形（蓝）与筐口平面线（红）。
    预览与检测共用 roi_rect + ball_blobs，所见即所查——篮网/手臂等细长
    橙色块不会被染色，也不会被检测当成球。"""
    rx, ry, rw, rh, clean = _detect_like_mask(frame, calib)
    out = frame.copy()
    out[ry:ry + rh, rx:rx + rw][clean > 0] = (0, 255, 0)
    cv2.rectangle(out, (rx, ry), (rx + rw, ry + rh), (0, 255, 255), 1)
    x, y, w, h = calib.hoop_rect
    cv2.rectangle(out, (x, y), (x + w, y + h), (255, 0, 0), 2)
    rim_y = int(y + h * RIM_Y_RATIO)
    cv2.line(out, (x, rim_y), (x + w, rim_y), (0, 0, 255), 2)
    return out


def yolo_overlay(frame: np.ndarray, calib: Calibration) -> tuple:
    """YOLO 检出预览（R-013）：整帧跑与剪辑台**同一**检测器、同一参数
    （自训 best.pt、classes=[0]、imgsz/conf 等全按 detector_yolo.CFG），
    画绿框（检出的球，含置信度）+ 筐口矩形（蓝）+ 筐口平面线（红）。
    返回 (叠加图, 检出球数)。HSV 对照模式（BBH_DETECTOR=hsv）不走这里
    （调用方按 detector_switch.NAME 分流到 overlay_preview）。

    ⚠ 前提：**仅当 `detector_yolo.CFG["crop"] == "full"`（默认）时与剪辑台
    完全同参**。若把 BBH_YOLO_CROP 设成 "roi"，生产端会裁筐周 ROI、并用
    crop_imgsz=640 推理，而这里仍整帧 + CFG["imgsz"] ⇒ 预览只是近似；
    要补「ROI 裁剪 + 框坐标映回全帧」才配得上「所见即所得」。该开关全仓
    无人使用（R-013 收尾时确认），故只声明前提、不实现（记 docs/需求池.md）。"""
    from .detector_yolo import CFG, _model      # 懒导入：标定页不拖 torch
    res = _model().predict(frame, imgsz=CFG["imgsz"], conf=CFG["conf"],
                           iou=CFG["iou"], classes=[CFG["ball_class"]],
                           verbose=False, device=CFG["device"],
                           quantize=CFG["quantize"])[0]
    out = frame.copy()
    n = 0
    boxes = getattr(res, "boxes", None)
    if boxes is not None and len(boxes):
        for xyxy, conf in zip(boxes.xyxy.cpu().numpy(), boxes.conf.cpu().numpy()):
            x1, y1, x2, y2 = (int(v) for v in xyxy)
            cv2.rectangle(out, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cv2.putText(out, f"{conf:.2f}", (x1, max(14, y1 - 4)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)
            n += 1
    x, y, w, h = calib.hoop_rect
    cv2.rectangle(out, (x, y), (x + w, y + h), (255, 0, 0), 2)
    rim_y = int(y + h * RIM_Y_RATIO)
    cv2.line(out, (x, rim_y), (x + w, rim_y), (0, 0, 255), 2)
    return out, n


_SEG_GLOB = "calibration_seg*.json"


def load_segments_dir(work_dir: str) -> list:
    """work 目录 → CalibSegment 列表（t0 升序）。从 app.load_segments 迁移
    （vrender 需要、不能反向 import app/gradio），行为零变化：主标定缺失/
    损坏 → []；补充段个别损坏跳过；calib_t=0 的补充段不收（主标定即段 0）。"""
    base_p = os.path.join(work_dir, "calibration.json")
    try:
        base = Calibration.load(base_p)
    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        return []
    segs = [CalibSegment(0.0, base, ref_image_path(base_p, base))]
    for p in sorted(glob.glob(os.path.join(work_dir, _SEG_GLOB))):
        try:
            c = Calibration.load(p)
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            continue
        if c.calib_t > 0:
            segs.append(CalibSegment(c.calib_t, c, ref_image_path(p, c)))
    segs.sort(key=lambda s: s.t0)
    return segs
