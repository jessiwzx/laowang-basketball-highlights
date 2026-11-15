import pytest

from bbh.detector import detect_positions, safe_fps, VideoOpenError
from bbh.goal_decider import decide_goals
from tests.conftest import GOAL_CROSS_FRAME


def test_打不开的视频报错(sample_calibration):
    with pytest.raises(VideoOpenError):
        detect_positions("no_such_file.mp4", sample_calibration)


def test_ROI覆盖篮下禁区并边缘钳位(sample_calibration):
    """2026-08-19 扩大：旧 ROI 仅 3 筐宽×5 筐高（1080p 实测 174×115px，
    标定时抽一帧球几乎总在框外）。现应为 6 筐宽（SIDE=2.5）×13 筐高
    （UP=5/DOWN=8），筐口居中；fixture 帧较矮时上下越界钳在帧内。"""
    from bbh.detector import roi_rect
    rx, ry, rw, rh = roi_rect(sample_calibration, 640, 360)
    assert (rx, rw) == (100, 480)          # 300−80×2.5，宽 80×6
    assert (ry, rh) == (0, 360)            # 上沿越界钳到帧顶、整高
    # 真实标定量级（stodownload 1080p，筐宽 58px）→ ROI 348×299
    from bbh.models import Calibration
    real = Calibration(hoop_rect=(1017, 442, 58, 23), hsv_low=(1, 98, 96),
                       hsv_high=(12, 201, 179), frame_size=(1920, 1080))
    rx, ry, rw, rh = roi_rect(real, 1920, 1080)
    assert (rx, ry, rw, rh) == (872, 327, 348, 299)


@pytest.mark.parametrize("raw,expect", [(-1.0, 30.0), (0.0, 30.0),
                                        (29.97, 29.97), (0.5, 0.5)])
def test_safe_fps回退异常值(raw, expect):
    """CAP_PROP_FPS 不可读时实测返回 -1.0（真值，骗过 or 回退）。"""
    assert safe_fps(raw) == expect


def test_合成视频检测出球并判定进球(sample_video, sample_calibration):
    positions, meta = detect_positions(sample_video, sample_calibration, step=2)
    assert meta["fps"] == pytest.approx(30.0)
    assert len(positions) > 5                          # 有球检出
    assert all(p.area > 0 for p in positions)
    events = decide_goals(positions, sample_calibration)
    assert len(events) == 1
    assert events[0].status == "high"
    assert events[0].t == pytest.approx(GOAL_CROSS_FRAME / 30.0, abs=0.3)


def test_帧时间用容器PTS并与帧号一致(sample_video, sample_calibration):
    """v4 起帧时间取容器 PTS（CAP_PROP_POS_MSEC，与 ffmpeg -ss 剪辑同钟，
    可变帧率视频两钟最多差 ~3s）；恒定帧率合成视频上应与 帧号/fps 吻合，
    且 meta 报告 pts_clock=True、检出点带帧号 fi。"""
    positions, meta = detect_positions(sample_video, sample_calibration, step=2)
    assert meta["pts_clock"] is True
    assert positions and all(p.fi >= 0 for p in positions)
    assert all(abs(p.t - p.fi / 30.0) < 0.05 for p in positions)


def test_分辨率不同时自动缩放筐口矩形(sample_video, sample_calibration):
    """标定 frame_size 标成 1280x720，筐口矩形按比例给——检测应把坐标换算回视频
    实际分辨率后再判定（decide_goals 仍用原分辨率标定）。"""
    from bbh.models import Calibration
    calib2 = Calibration(hoop_rect=(600, 280, 160, 80),
                         hsv_low=sample_calibration.hsv_low,
                         hsv_high=sample_calibration.hsv_high,
                         frame_size=(1280, 720))
    positions, meta = detect_positions(sample_video, calib2, step=2)
    events = decide_goals(positions, sample_calibration)  # 判定用原分辨率标定
    assert len(events) == 1


def test_resume续跑与全量结果一致(sample_video, sample_calibration):
    """前 90 帧全量结果 + resume_frame=90 的后半段，应与一次全量跑完全一致。"""
    positions_full, _ = detect_positions(sample_video, sample_calibration, step=2)
    positions_tail, _ = detect_positions(sample_video, sample_calibration, step=2,
                                         resume_frame=90)
    head = [p for p in positions_full if p.t < 90 / 30.0]

    def keys(ps):
        return [(p.t, p.x, p.y) for p in ps]

    assert keys(head + positions_tail) == keys(positions_full)
    assert positions_tail  # 后半段确实有输出（帧 90 球仍在画面）


def test_on_detect回调与返回值等价(sample_video, sample_calibration):
    """on_detect 逐点实时回调（检查点续跑的中途持久化通道），顺序与内容
    应与最终返回的 positions 完全一致。"""
    streamed = []
    positions, _ = detect_positions(sample_video, sample_calibration, step=2,
                                    on_detect=streamed.append)
    assert [(p.t, p.x, p.y, p.area) for p in streamed] == \
           [(p.t, p.x, p.y, p.area) for p in positions]


def test_筐口矩形不在画面内报清晰错误(sample_video, sample_calibration):
    """标定分辨率缩放后筐口跑出画面 → ValueError 而非 cv2.error。"""
    from bbh.models import Calibration
    calib3 = Calibration(hoop_rect=(2600, 1400, 160, 80),
                         hsv_low=sample_calibration.hsv_low,
                         hsv_high=sample_calibration.hsv_high,
                         frame_size=(1280, 720))   # 缩放后 x≈1300 > 640
    with pytest.raises(ValueError, match="筐口矩形不在画面内"):
        detect_positions(sample_video, calib3, step=2)


def test_细长橙色干扰块不抢球检出(tmp_path, sample_calibration):
    """篮网/栏杆样细长色块：面积可能大于球，但占外接圆比例低——形状过滤
    后不抢"最大色块"，进球仍由真球产生（无形状过滤时竖条每帧胜出、零检出）。"""
    import cv2
    import numpy as np
    w, h = 640, 360
    path = str(tmp_path / "net.mp4")
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 30, (w, h))
    for i in range(180):
        img = np.full((h, w, 3), (40, 44, 50), np.uint8)
        cv2.line(img, (300, 154), (380, 154), (180, 180, 180), 3)
        cv2.rectangle(img, (352, 168), (357, 254), (20, 100, 245), -1)  # 竖条 5x86（面积>球）
        if 60 <= i <= 90:                                               # 球下落
            k = (i - 60) / 30.0
            # r=11（球体量级，判定器实体校验要求，同 conftest）
            cv2.circle(img, (340, int(100 + 120 * k)), 11, (20, 100, 245), -1)
        vw.write(img)
    vw.release()
    positions, _ = detect_positions(path, sample_calibration, step=2)
    assert positions and all(p.x < 350 for p in positions)   # 只有球（x≈340），竖条（x≈354）无
    events = decide_goals(positions, sample_calibration)
    assert len(events) == 1 and events[0].status == "high"
