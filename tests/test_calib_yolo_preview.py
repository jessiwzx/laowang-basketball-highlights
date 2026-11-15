"""R-013 收尾：标定预览真检测器化的三处契约。

覆盖（都不加载 torch——`detector_yolo` 模块级不 import ultralytics，
`_model()` 里才懒导入，所以 patch `_model` 足够）：
1. `calibration.yolo_overlay` 的绘制与计数：绿框落在检出坐标、筐口蓝框与
   筐面红线的位置、0 检出时仍画几何、原帧不被就地改写。
2. `app.build_calibration` 的分流：NAME=yolo 走 yolo_overlay、NAME=hsv
   仍走旧 overlay_preview（保护对照路径不被顺手删）。
3. 宽色域（S<60）警示在 YOLO 模式下仍出、且措辞按「只影响溯源/跟球」说，
   HSV 模式保持旧措辞。

为什么要有第 2、3 条：conftest 把 BBH_DETECTOR 固定成 hsv，不 patch NAME
就永远只跑旧分支——新代码等于零覆盖（R-013 代码 2026-10-15 写完、到
2026-10-18 收尾时确实一条断言都没有）。
"""
import cv2
import numpy as np

import bbh.app as app_module
from bbh import calibration
from bbh import detector_yolo
from bbh.goal_decider import RIM_Y_RATIO
from bbh.models import Calibration

H, W = 360, 640
PTS = [(300.0, 154.0), (380.0, 154.0), (340.0, 220.0)]   # 筐口左/右端 + 球


def _frame_with_gray_ball():
    """灰球帧：球心 S≈0 ⇒ 触发「色域饱和度下限偏低」警示（S<60）。"""
    frame = np.full((H, W, 3), (50, 44, 40), np.uint8)
    cv2.line(frame, (300, 154), (380, 154), (180, 180, 180), 3)
    cv2.circle(frame, (340, 220), 8, (128, 128, 128), -1)
    return frame


def _frame_with_orange_ball():
    """橙球帧：S≈234 ⇒ 不触发警示，用于测纯分流。"""
    frame = np.full((H, W, 3), (50, 44, 40), np.uint8)
    cv2.line(frame, (300, 154), (380, 154), (180, 180, 180), 3)
    cv2.circle(frame, (340, 220), 8, (245, 100, 20), -1)
    return frame


class _Arr:
    """模拟 torch.Tensor 的 `.cpu().numpy()` 链。"""

    def __init__(self, a):
        self._a = np.asarray(a, dtype=float)

    def cpu(self):
        return self

    def numpy(self):
        return self._a


class _Boxes:
    def __init__(self, xyxy, conf):
        self.xyxy = _Arr(xyxy)
        self.conf = _Arr(conf)

    def __len__(self):
        return len(self.conf._a)


class _FakeModel:
    """假 ultralytics 模型：predict 直接回放给定 boxes。"""

    def __init__(self, boxes):
        self._boxes = boxes
        self.calls = 0

    def predict(self, *a, **k):
        self.calls += 1
        res = type("R", (), {})()
        res.boxes = self._boxes
        return [res]


def _install_fake_model(monkeypatch, boxes):
    model = _FakeModel(boxes)
    monkeypatch.setattr(detector_yolo, "_model", lambda: model)
    return model


def _calib(hoop_rect=(300, 140, 80, 32)):
    return Calibration(hoop_rect=hoop_rect, hsv_low=(0, 40, 40),
                       hsv_high=(30, 255, 255), frame_size=(W, H))


# ---------------------------------------------------------------- 1. 绘制
def test_yolo_overlay_画绿框计数并保留筐口几何(monkeypatch):
    boxes = _Boxes([[100, 100, 140, 140]], [0.87])
    model = _install_fake_model(monkeypatch, boxes)
    frame = np.zeros((H, W, 3), np.uint8)
    calib = _calib()

    out, n = calibration.yolo_overlay(frame, calib)

    assert n == 1 and model.calls == 1
    assert out.shape == frame.shape and out is not frame
    assert not frame.any(), "预览不能就地改写传入帧（调用方还要拿它存档）"
    # 绿框：矩形上边沿（thickness=2 → y=100、101 两行都是绿）
    assert out[100, 120].tolist() == [0, 255, 0]
    # 蓝框：筐口矩形左边沿
    assert out[140, 300].tolist() == [255, 0, 0]
    # 红线：筐口平面（rim_y = y + h·RIM_Y_RATIO）
    rim_y = round(140 + 32 * RIM_Y_RATIO)
    assert out[rim_y, 340].tolist() == [0, 0, 255]


def test_yolo_overlay_零检出仍画筐口且计数为0(monkeypatch):
    _install_fake_model(monkeypatch, _Boxes(np.empty((0, 4)), np.empty((0,))))
    frame = np.zeros((H, W, 3), np.uint8)

    out, n = calibration.yolo_overlay(frame, _calib())

    assert n == 0
    assert out[140, 300].tolist() == [255, 0, 0]     # 几何照画，用户仍能核对标定
    assert not out[100:140, 100:140].any()           # 没有假绿框


# ---------------------------------------------------------------- 2. 分流
def test_build_calibration_yolo模式走真检测器(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    frame = _frame_with_orange_ball()
    seen = {}

    def fake_overlay(fr, calib):
        seen["yolo"] = True
        return np.zeros((fr.shape[0], fr.shape[1], 3), np.uint8), 3

    def boom(*a, **k):
        raise AssertionError("YOLO 模式不该走 HSV 预览（overlay_preview）")

    monkeypatch.setattr(calibration, "yolo_overlay", fake_overlay)
    monkeypatch.setattr(calibration, "overlay_preview", boom)
    monkeypatch.setattr(app_module.detector_switch, "NAME", "yolo")

    preview, msg, s0, v0 = app_module.build_calibration(
        PTS, frame, str(tmp_path / "v.mp4"))

    assert seen.get("yolo") is True
    assert "本帧检出 3 个" in msg and "绿框=YOLO 检出的球" in msg
    assert preview.shape == (H, W, 3)
    saved = Calibration.load(str(tmp_path / "work" / "v" / "calibration.json"))
    assert (s0, v0) == (saved.hsv_low[1], saved.hsv_low[2])   # 滑块初值对齐落盘值


def test_build_calibration_HSV模式仍走旧预览(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def boom(*a, **k):
        raise AssertionError("HSV 模式不该调 yolo_overlay")

    monkeypatch.setattr(calibration, "yolo_overlay", boom)
    monkeypatch.setattr(app_module.detector_switch, "NAME", "hsv")

    _preview, msg, _s0, _v0 = app_module.build_calibration(
        PTS, _frame_with_orange_ball(), str(tmp_path / "v.mp4"))

    assert "ROI 内命中" in msg and "绿=球" in msg


# ---------------------------------------------------------------- 3. S 警示
def test_YOLO模式_S偏低警示仍在且说清与找球无关(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(calibration, "yolo_overlay",
                        lambda fr, calib: (np.zeros(fr.shape, np.uint8), 0))
    monkeypatch.setattr(app_module.detector_switch, "NAME", "yolo")

    _p, msg, s0, _v = app_module.build_calibration(
        PTS, _frame_with_gray_ball(), str(tmp_path / "v.mp4"))

    assert s0 < 60                                   # 前提：确实触发了预警
    assert "饱和度下限偏低" in msg
    assert "竖版跟球" in msg and "与 YOLO 找球无关" in msg
    assert "检测不受影响" not in msg                 # 旧措辞会误导 YOLO 用户


def test_HSV模式_S偏低警示保持旧措辞(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(app_module.detector_switch, "NAME", "hsv")

    _p, msg, s0, _v = app_module.build_calibration(
        PTS, _frame_with_gray_ball(), str(tmp_path / "v.mp4"))

    assert s0 < 60
    assert "饱和度下限偏低" in msg and "检测不受影响" in msg
