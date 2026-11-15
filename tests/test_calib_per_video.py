# -*- coding: utf-8 -*-
"""标定随视频走（2026-09-13）：work/<视频名>/ 各存各的——换视频不再串标定，
新视频必须重新标定；仓库根的旧全局标定按 events.json 指纹认领迁移。

隔离手法=chdir(tmp)：走生产真实 _work_dir_for（work/<basename> 相对 CWD），
不桩路径函数——迁移操作的根相对路径也一并落 tmp。"""
import json
import os

import cv2
from bbh import _gr_compat as gr
import numpy as np
import pytest

import bbh.app as app_module
from bbh.models import CalibSegment, Calibration, segments_key


def _rgb_frame():
    """标定页同款 RGB 参考帧（灰筐线 + 橙球）。"""
    img = np.full((360, 640, 3), (50, 44, 40), np.uint8)
    cv2.line(img, (300, 154), (380, 154), (180, 180, 180), 3)
    cv2.circle(img, (340, 220), 8, (245, 100, 20), -1)
    return img


_PTS = [(300.0, 154.0), (380.0, 154.0), (340.0, 220.0)]


@pytest.fixture
def cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _vid(tmp_path, name):
    return str(tmp_path / (name + ".mp4"))


# ---------- 存储与隔离 ----------

def test_标定存进视频自己的work目录根不落盘(cwd):
    app_module.build_calibration(_PTS, _rgb_frame(), _vid(cwd, "a"))
    assert os.path.exists(os.path.join("work", "a", "calibration.json"))
    assert os.path.exists(os.path.join("work", "a", "calibration_ref.png"))
    assert not os.path.exists("calibration.json")      # 仓库根（CWD）不再被写


def test_load_segments按视频隔离(cwd):
    a, b = _vid(cwd, "a"), _vid(cwd, "b")
    app_module.build_calibration(_PTS, _rgb_frame(), a)
    segs = app_module.load_segments(a)
    assert [s.t0 for s in segs] == [0.0]
    assert app_module.load_segments(b) == []           # b 未标定
    assert app_module._calib_ready(a) is True
    assert app_module._calib_ready(b) is False


def test_段标定与段列表按视频隔离(cwd):
    a, b = _vid(cwd, "a"), _vid(cwd, "b")
    frame = _rgb_frame()
    app_module.build_calibration(_PTS, frame, a)
    app_module.build_segment_calibration(_PTS, frame, 600.0, a)
    assert os.path.exists(os.path.join("work", "a", "calibration_seg2.json"))
    assert os.path.exists(os.path.join("work", "a", "calibration_ref_seg2.png"))
    assert [s.t0 for s in app_module.load_segments(a)] == [0.0, 600.0]
    assert "10:00 起" in app_module._seg_list_md(a)
    assert app_module._seg_list_md(b) == ""            # 段列表不串台
    assert app_module._seg_choices(b) == []


def test_微调只改本视频的标定(cwd):
    a, b = _vid(cwd, "a"), _vid(cwd, "b")
    frame = _rgb_frame()
    app_module.build_calibration(_PTS, frame, a)
    app_module.build_calibration(_PTS, frame, b)
    _, msg = app_module.refine_hsv(frame, 100, 100, a)
    assert "已保存" in msg
    ca = Calibration.load(os.path.join("work", "a", "calibration.json"))
    cb = Calibration.load(os.path.join("work", "b", "calibration.json"))
    assert ca.hsv_low[1] == 100
    assert cb.hsv_low[1] != 100                         # b 的档没被碰


def test_未标定视频微调报错(cwd):
    a, b = _vid(cwd, "a"), _vid(cwd, "b")
    app_module.build_calibration(_PTS, _rgb_frame(), a)
    with pytest.raises(gr.Error):                       # b 没标定不许微调
        app_module.refine_hsv(_rgb_frame(), 100, 100, b)


def test_没给视频生成标定报错(cwd):
    with pytest.raises(gr.Error):
        app_module.build_calibration(_PTS, _rgb_frame(), "")
    with pytest.raises(gr.Error):
        app_module.refine_hsv(_rgb_frame(), 100, 100, "")


# ---------- 一次性迁移：根目录旧全局标定按指纹认领 ----------

def _mk_root_set():
    """仓库根造一套旧全局标定（主 + 段2@600），返回其段组合指纹。
    参考帧内容任意——认领比对只看指纹（含 ref_hash 字符串）。"""
    Calibration(hoop_rect=(300, 140, 90, 28), hsv_low=(0, 100, 80),
                hsv_high=(20, 255, 255), frame_size=(640, 360),
                ref_image="calibration_ref.png", ref_hash="h1"
                ).save("calibration.json")
    Calibration(hoop_rect=(310, 150, 90, 28), hsv_low=(0, 100, 80),
                hsv_high=(20, 255, 255), frame_size=(640, 360),
                ref_image="calibration_ref_seg2.png", ref_hash="h2",
                calib_t=600.0
                ).save("calibration_seg2.json")
    for p in ("calibration_ref.png", "calibration_ref_seg2.png"):
        with open(p, "wb") as f:
            f.write(b"ref")
    base = Calibration.load("calibration.json")
    s2 = Calibration.load("calibration_seg2.json")
    return segments_key([CalibSegment(0.0, base, None),
                         CalibSegment(600.0, s2, None)])


def _mk_events(work_dir, key):
    os.makedirs(work_dir, exist_ok=True)
    with open(os.path.join(work_dir, "events.json"), "w",
              encoding="utf-8") as f:
        json.dump({"video": "x.mp4", "duration": 100.0, "events": [],
                   "warning": None, "calib_key": key, "logic_ver": 1}, f)


def test_迁移指纹认领搬运(cwd):
    """根旧标定被 events.json 指纹认领 → 整套搬进该视频 work 目录；
    搬完按新口径读段指纹与认领键逐字节相同（缓存继续有效）；幂等。"""
    key = _mk_root_set()
    _mk_events(os.path.join("work", "x"), key)

    notes = app_module.migrate_root_calibrations()

    assert os.path.exists(os.path.join("work", "x", "calibration.json"))
    assert os.path.exists(os.path.join("work", "x", "calibration_seg2.json"))
    assert os.path.exists(os.path.join("work", "x", "calibration_ref.png"))
    assert os.path.exists(os.path.join("work", "x", "calibration_ref_seg2.png"))
    assert not os.path.exists("calibration.json")       # 根清干净
    assert notes and "x" in notes[0]
    assert segments_key(app_module.load_segments(_vid(cwd, "x"))) == key
    assert app_module.migrate_root_calibrations() == []  # 幂等


def test_迁移多视频同指纹各留一份(cwd):
    key = _mk_root_set()
    _mk_events(os.path.join("work", "x"), key)
    _mk_events(os.path.join("work", "y"), key)

    notes = app_module.migrate_root_calibrations()

    assert os.path.exists(os.path.join("work", "x", "calibration.json"))
    assert os.path.exists(os.path.join("work", "y", "calibration.json"))
    assert not os.path.exists("calibration.json")
    assert len(notes) == 2


def test_迁移无人认领原地不动(cwd):
    """标定后从未检测过（无指纹可认领）→ 不猜归属，留在原地交用户归置。"""
    _mk_root_set()
    _mk_events(os.path.join("work", "x"), [[0.0, "别的指纹"]])

    notes = app_module.migrate_root_calibrations()

    assert os.path.exists("calibration.json")           # 原地不动
    assert not os.path.exists(os.path.join("work", "x", "calibration.json"))
    assert notes and "无人认领" in notes[0]


def test_迁移根无旧文件直接空(cwd):
    assert app_module.migrate_root_calibrations() == []
