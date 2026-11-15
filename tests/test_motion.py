"""镜头位移配准（bbh.motion + detector 位移校正端到端）。"""
import cv2
import numpy as np
import pytest

from bbh.detector import detect_positions
from bbh.motion import MotionTracker
from tests.conftest import BALL_BGR, FPS

# 漂移端到端场景参数（其余复用 conftest 的场景设定：rim_y=154、球 r=11）
DRIFT_FRAME = 100    # 第 100 帧起全场景右移 +12px（模拟镜头被碰）
DRIFT_PX = 12
N_FRAMES = 240       # 第二段下落（170–200）须在漂移之后


def textured(h=360, w=640, seed=7):
    """固定种子随机几何纹理帧。ORB 靠角点匹配，纯色背景提不出特征；
    用实心矩形/线段（编码后仍保留边缘）而非细噪声（被压缩抹平）。"""
    rng = np.random.RandomState(seed)
    img = np.full((h, w, 3), 60, np.uint8)
    for _ in range(160):
        x0, y0 = rng.randint(0, w - 60), rng.randint(0, h - 60)
        bw, bh = rng.randint(8, 60), rng.randint(6, 30)
        g = int(rng.randint(30, 220))
        if rng.rand() < 0.5:
            cv2.rectangle(img, (x0, y0), (x0 + bw, y0 + bh), (g, g, g), -1)
        else:
            cv2.line(img, (x0, y0), (x0 + bw, y0 + bh), (g, g, g), 2)
    return img


def shifted(img, dx, dy):
    """整帧平移 dx/dy（内容向右/向下为正）。大画布裁剪实现——np.roll 会
    环绕，移出的内容从对侧卷回来是错的。"""
    h, w = img.shape[:2]
    ox, oy = abs(dx) + 1, abs(dy) + 1
    canvas = np.full((h + 2 * oy, w + 2 * ox, 3), 60, np.uint8)
    canvas[oy:oy + h, ox:ox + w] = img
    return canvas[oy - dy:oy - dy + h, ox - dx:ox - dx + w]


class TestMotionTracker:

    def test_平移帧测出位移(self):
        ref = textured()
        tr = MotionTracker(ref)
        assert tr.update(shifted(ref, 8, -5), t=0.0) is True
        dx, dy = tr.shift(0.0)
        assert abs(dx - 8) <= 2      # 降采样匹配精度 ~2px（设计容差）
        assert abs(dy + 5) <= 2

    def test_无纹理帧不采样保持上次(self):
        tr = MotionTracker(textured())
        blank = np.full((360, 640, 3), 128, np.uint8)
        assert tr.update(blank, t=0.0) is False      # 提不出特征 → 不采样
        assert tr.samples == []
        assert tr.shift(0.0) == (0.0, 0.0)           # 无样本不校正

        good = MotionTracker(textured())
        good.update(shifted(textured(), 4, 0), t=0.0)
        assert good.update(blank, t=5.0) is False    # 中途遮挡/模糊
        assert len(good.samples) == 1                # 样本表不变
        dx, dy = good.shift(5.0)                     # 沿用上次位移
        assert abs(dx - 4) <= 2 and abs(dy) <= 2

    def test_位移插值与两端保持(self):
        tr = MotionTracker(textured(),
                           samples=[(1.0, 10.0, 0.0), (3.0, 20.0, -4.0)])
        assert tr.shift(0.5) == (10.0, 0.0)          # 首样本前按首样本
        assert tr.shift(2.0) == (15.0, -2.0)         # 样本间线性插值
        assert tr.shift(9.0) == (20.0, -4.0)         # 末样本后保持

    def test_参考帧与检测帧分辨率不同(self):
        """参考帧 1280 宽、检测帧 640 宽：两者都降采样到 640 匹配，
        位移按检测帧原生像素报告（1280 尺度的 16px → 640 尺度的 8px）。"""
        big = textured(w=1280, h=720)
        tr = MotionTracker(big)
        moved = cv2.resize(shifted(big, 16, -10), None, fx=0.5, fy=0.5)
        assert tr.update(moved, t=0.0) is True
        dx, dy = tr.shift(0.0)
        assert abs(dx - 8) <= 2
        assert abs(dy + 5) <= 2


def _drift_ball_xy(fi):
    """球在场景坐标系里的位置：两段下落（60–90 / 170–200），x 恒 340。"""
    for lo in (60, 170):
        if lo <= fi <= lo + 30:
            k = (fi - lo) / 30.0
            return 340, int(100 + 120 * k)
    return None


def make_drift_video(path):
    """固定机位中途被碰：第 100 帧起全场景（背景+筐+球）右移 12px。
    画面坐标系里第二段球在 x=352，第一段 x=340——不校正则两段检出分裂。"""
    bg = textured()
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"),
                         FPS, (640, 360))
    for i in range(N_FRAMES):
        img = bg.copy()
        cv2.line(img, (300, 154), (380, 154), (180, 180, 180), 3)
        pos = _drift_ball_xy(i)
        if pos:
            cv2.circle(img, pos, 11, BALL_BGR, -1)
        if i >= DRIFT_FRAME:
            img = shifted(img, DRIFT_PX, 0)
        vw.write(img)
    vw.release()
    return path


@pytest.fixture(scope="module")
def drift_video(tmp_path_factory):
    return make_drift_video(
        str(tmp_path_factory.mktemp("drift") / "drift.mp4"))


def _median_x(positions, lo, hi):
    xs = [p.x for p in positions if lo <= p.fi <= hi]
    assert len(xs) >= 5                     # 段内检出太少说明 fixture 坏了
    return float(np.median(xs))


def test_漂移视频检出坐标已校正(drift_video, sample_calibration, monkeypatch):
    """detector 端到端：无配准 → 第二段检出跟镜头漂到 x≈352；带参考帧
    配准 → 两段都回到标定坐标系 x≈340（标定几何对全程有效）。采样加密到
    每 30 帧，使 100 帧处的阶跃在两样本间、两段下落各自拿到恒定位移。"""
    import bbh.detector as det
    monkeypatch.setattr(det, "SAMPLE_EVERY", 30)

    raw, _ = detect_positions(drift_video, sample_calibration)
    assert abs(_median_x(raw, 60, 90) - 340) <= 2     # 漂移前：本来就准
    assert abs(_median_x(raw, 170, 200) - 352) <= 2   # 漂移后：跟镜头偏移

    ref = textured()
    cv2.line(ref, (300, 154), (380, 154), (180, 180, 180), 3)  # 标定那帧
    corrected, _ = detect_positions(drift_video, sample_calibration,
                                    motion=MotionTracker(ref))
    assert abs(_median_x(corrected, 60, 90) - 340) <= 2
    assert abs(_median_x(corrected, 170, 200) - 340) <= 2   # 折回标定系
