import cv2
import numpy as np
import pytest
from bbh.calibration import (count_roi_hits, hsv_range_from_clicks,
                             hsv_zero_diagnosis, hoop_rect_from_rim_points,
                             overlay_preview)

# 与 conftest 合成视频一致的"篮球橙" BGR(20,100,245)
BALL_BGR = (20, 100, 245)


def test_hsv_range_from_clicks_covers_ball_color():
    frame = np.zeros((360, 640, 3), np.uint8)
    frame[100:110, 300:310] = BALL_BGR
    low, high = hsv_range_from_clicks(frame, clicks=[(305, 105)])
    hsv = cv2.cvtColor(np.uint8([[BALL_BGR]]), cv2.COLOR_BGR2HSV)[0][0]
    assert all(low[i] <= hsv[i] <= high[i] for i in range(3))


def test_hsv_range_clamped_to_valid():
    frame = np.full((360, 640, 3), (255, 255, 255), np.uint8)
    low, high = hsv_range_from_clicks(frame, clicks=[(10, 10)])
    assert 0 <= low[0] <= high[0] <= 179
    assert 0 <= low[1] <= high[1] <= 255
    assert 0 <= low[2] <= high[2] <= 255


def test_hsv_range_rejects_out_of_frame_clicks():
    frame = np.zeros((360, 640, 3), np.uint8)
    for bad in [(-5, 100), (640, 100), (300, -1), (300, 360)]:
        with pytest.raises(ValueError):
            hsv_range_from_clicks(frame, clicks=[bad])


def test_hoop_rect_rejects_degenerate_rim_points():
    with pytest.raises(ValueError):
        hoop_rect_from_rim_points((340, 150), (340, 158))  # 两点 x 相同


def test_hoop_rect_from_two_rim_clicks():
    rect = hoop_rect_from_rim_points((300, 150), (380, 158))
    x, y, w, h = rect
    assert x < 300 and x + w > 380          # 左右留边
    assert y < 154 and y + h > 154          # 覆盖筐口平面 154 附近
    assert w > 80


def test_overlay_preview_画出筐框且球区域高亮():
    frame = np.zeros((360, 640, 3), np.uint8)
    frame[100:110, 300:310] = BALL_BGR
    frame[100:110, 30:40] = BALL_BGR        # ROI 外（x=30 < 220）的同色块
    frame[175:240, 332:337] = BALL_BGR      # ROI 内篮网样细长块（面积大但非圆形）
    from bbh.models import Calibration
    calib = Calibration(hoop_rect=(300, 140, 80, 40),
                        hsv_low=(0, 80, 120), hsv_high=(25, 255, 255),
                        frame_size=(640, 360))
    out = overlay_preview(frame, calib)
    assert out.shape[:2] == frame.shape[:2]
    assert out[105, 305].tolist() == [0, 255, 0]          # ROI 内球位置被染绿
    assert out[105, 35].tolist() == list(BALL_BGR)        # ROI 外同色块保留原色（检测不看）
    assert out[200, 334].tolist() == list(BALL_BGR)       # 细长块保留原色（形状过滤排除）


def test_count_roi_hits随HSV范围变化():
    from bbh.models import Calibration
    frame = np.zeros((360, 640, 3), np.uint8)
    frame[100:110, 300:310] = BALL_BGR                  # ROI 内的球
    calib = Calibration(hoop_rect=(300, 140, 80, 40),
                        hsv_low=(0, 80, 120), hsv_high=(25, 255, 255),
                        frame_size=(640, 360))
    n_wide = count_roi_hits(frame, calib)               # 含球
    assert 30 <= n_wide <= 130                          # 10x10 色块（开运算后略缩）
    calib.hsv_low = (0, 250, 120)                       # S 下限越过球的 S≈234
    assert count_roi_hits(frame, calib) == 0            # 球也被滤掉 → 0


def test_hsv_zero_diagnosis指出卡死的通道():
    from bbh.models import Calibration
    frame = np.zeros((360, 640, 3), np.uint8)
    frame[100:110, 300:310] = BALL_BGR          # 球 H≈11 S≈234 V=245
    base = {"hoop_rect": (300, 140, 80, 40), "frame_size": (640, 360)}
    # H 范围完全不含球色 → 提示重新生成标定
    calib = Calibration(hsv_low=(60, 0, 0), hsv_high=(100, 255, 255), **base)
    msg = hsv_zero_diagnosis(frame, calib)
    assert "H" in msg and "生成标定" in msg
    # S 下限高于球的最大 S → 指出 S 滑块
    calib = Calibration(hsv_low=(0, 250, 0), hsv_high=(25, 255, 255), **base)
    assert "S 下限" in hsv_zero_diagnosis(frame, calib)
    # V 下限高于球的 V → 指出 V 滑块
    calib = Calibration(hsv_low=(0, 0, 250), hsv_high=(25, 255, 255), **base)
    assert "V 下限" in hsv_zero_diagnosis(frame, calib)


def test_hsv_zero_diagnosis颜色命中但形状不过():
    """颜色范围很宽（含球与细长条），清洗后只剩细长块 → 提示不像球/换帧。"""
    from bbh.models import Calibration
    frame = np.zeros((360, 640, 3), np.uint8)
    frame[175:240, 332:337] = BALL_BGR           # ROI 内细长块，无球
    calib = Calibration(hoop_rect=(300, 140, 80, 40),
                        hsv_low=(0, 60, 100), hsv_high=(25, 255, 255),
                        frame_size=(640, 360))   # V 下限排除黑色背景
    msg = hsv_zero_diagnosis(frame, calib)
    assert "颜色命中" in msg and "细长" in msg


def test_hsv_zero_diagnosis颜色命中但块太碎():
    """只有 2x2 小色点，形态学清洗后无连通块 → 提示太碎/太小。"""
    from bbh.models import Calibration
    frame = np.zeros((360, 640, 3), np.uint8)
    frame[100:102, 300:302] = BALL_BGR
    calib = Calibration(hoop_rect=(300, 140, 80, 40),
                        hsv_low=(0, 60, 100), hsv_high=(25, 255, 255),
                        frame_size=(640, 360))   # V 下限排除黑色背景
    msg = hsv_zero_diagnosis(frame, calib)
    assert "太小" in msg or "太碎" in msg
