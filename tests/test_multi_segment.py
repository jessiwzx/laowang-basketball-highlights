# -*- coding: utf-8 -*-
"""A2 多段标定：视频中途挪镜头后按时间段各用各的标定几何。

场景对齐 09-04_1 取证：镜头物理挪位 → 视差 → 全局平移救不了旧参考帧；
唯一正解=移动后补一段标定（人工锚定，符合「程序粗标+人工核对」哲学）。
铁律：单段（t0=0）行为与旧版逐字节一致——四场 truth 缓存/重放全靠它。
"""
import json
import os

import cv2
import numpy as np
import pytest

from bbh.models import CalibSegment, Calibration, calib_key, load_events, segments_key
from tests.conftest import W, H, FPS

# 合成两段场景：前半筐在 A=(300,140)，后半筐挪到 B=(200,140)（镜头左移）。
# 球 1（帧 60~90）穿 A 筐（事件 ~2.5s）；球 2（帧 180~210）穿 B 筐（事件
# ~6.5s）——两球间隔必须 >COOLDOWN_SEC(3.0s)，否则第二颗被判定器自身
# 的段内冷却吞掉（与分段无关，单段同样吞）。
# 单用 A 标定：球 2 穿越点 off=(240-340)/80=-1.25 → 几何门槛拦下（low）；
# 分段后 [A@0, B@90帧/3.0s] 两球各自在自己的几何里判 high。
HOOP_A = (300, 140, 80, 40)
HOOP_B = (200, 140, 80, 40)
HSV = ((0, 80, 120), (25, 255, 255))
BOUNDARY_F = 90                       # 段边界：帧 90 = 3.0s
N2 = 270                              # 视频总帧数（9s：球 2 要离球 1 足够远）


def _ball_xy(fi):
    if 60 <= fi <= 90:                # 球 1：穿 A 筐（cx=340）
        return 340, int(100 + 120 * (fi - 60) / 30.0)
    if 180 <= fi <= 210:              # 球 2：穿 B 筐（cx=240）
        return 240, int(100 + 120 * (fi - 180) / 30.0)
    return None


def make_two_goal_video(path):
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    for i in range(N2):
        img = np.full((H, W, 3), (40, 44, 50), np.uint8)
        cv2.line(img, (300, 154), (380, 154), (180, 180, 180), 3)   # 旧筐（前半）
        cv2.line(img, (200, 154), (280, 154), (180, 180, 180), 3)   # 新筐（后半）
        pos = _ball_xy(i)
        if pos:
            cv2.circle(img, pos, 11, (20, 100, 245), -1)
        # 各球穿网后网摆（v12 网证据：网静止会把 high 降 review）
        if 88 <= i <= 112:
            sway = int(14 * np.sin((i - 88) / 4.0))
            cv2.line(img, (330 + sway, 158), (330 + sway, 216), (235, 235, 235), 2)
            cv2.line(img, (352 - sway, 158), (352 - sway, 216), (235, 235, 235), 2)
        if 208 <= i <= 232:
            sway = int(14 * np.sin((i - 208) / 4.0))
            cv2.line(img, (230 + sway, 158), (230 + sway, 216), (235, 235, 235), 2)
            cv2.line(img, (252 - sway, 158), (252 - sway, 216), (235, 235, 235), 2)
        vw.write(img)
    vw.release()
    return path


@pytest.fixture(scope="module")
def two_goal_video(tmp_path_factory):
    return make_two_goal_video(
        str(tmp_path_factory.mktemp("vid2") / "two_goal.mp4"))


@pytest.fixture()
def calib_a():
    return Calibration(hoop_rect=HOOP_A, hsv_low=HSV[0], hsv_high=HSV[1],
                       frame_size=(W, H))


@pytest.fixture()
def calib_b():
    return Calibration(hoop_rect=HOOP_B, hsv_low=HSV[0], hsv_high=HSV[1],
                       frame_size=(W, H))


def _segs(a, b):
    return [CalibSegment(0.0, a), CalibSegment(BOUNDARY_F / FPS, b)]


# ---------- models：指纹约束 ----------

def test_单段指纹与旧calib_key逐字节一致(calib_a):
    """存量缓存（四场 truth 的 positions/events、shooters/features）都持
    旧单段键——segments_key 必须原样吐回，否则全部身份不符触发重检。"""
    assert segments_key([CalibSegment(0.0, calib_a)]) == calib_key(calib_a)
    assert segments_key([CalibSegment(0, calib_a)]) == calib_key(calib_a)


def test_多段指纹含段起点(calib_a, calib_b):
    k = segments_key(_segs(calib_a, calib_b))
    assert k == [[0.0, calib_key(calib_a)], [3.0, calib_key(calib_b)]]


def test_同标定不同起点指纹不同(calib_b):
    """段边界变了=分段语义变了，事件结果可能不同，指纹必须区分。"""
    assert segments_key([CalibSegment(0.0, calib_b),
                         CalibSegment(3.0, calib_b)]) != \
           segments_key([CalibSegment(0.0, calib_b),
                         CalibSegment(4.0, calib_b)])


# ---------- detector：段尾边界 ----------

def test_end_frame分段扫描拼回全量(two_goal_video, calib_a):
    """[0,end)+[end,片尾) 两趟 == 一趟全量——多段扫描的坐标/时间口径
    与旧检测循环完全同源。"""
    from bbh.detector import detect_positions
    full, _ = detect_positions(two_goal_video, calib_a, step=2)
    head, _ = detect_positions(two_goal_video, calib_a, step=2,
                               end_frame=BOUNDARY_F)
    tail, _ = detect_positions(two_goal_video, calib_a, step=2,
                               resume_frame=BOUNDARY_F)
    assert [(p.t, p.x, p.y) for p in head + tail] == \
           [(p.t, p.x, p.y) for p in full]
    assert all(p.fi < BOUNDARY_F for p in head)
    assert all(p.fi >= BOUNDARY_F for p in tail)


# ---------- pipeline：多段编排 ----------

def test_单用A标定球2完全丢失(two_goal_video, calib_a, tmp_path):
    """基线：不分段时球 2（x=240）在 A 筐横向容差（X_MARGIN）外，被判定器
    分类阶段直接滤掉——连 low 候选都不形成（比规则 A 更早的门槛）。这正是
    09-04_1 重标后片头段 24 事件/3 high 的病况：后半场的球在旧几何里蒸发。"""
    from bbh.pipeline import run_detection
    p, _ = run_detection(two_goal_video, calib_a, work_dir=str(tmp_path))
    evs = load_events(p)["events"]
    assert len(evs) == 1
    assert evs[0].status == "high"          # 只有球 1


def test_多段标定两球各自判high(two_goal_video, calib_a, calib_b, tmp_path):
    """A@0 + B@3.0s：球 1 在段 1、球 2 在段 2，各自几何里都是好进球。"""
    from bbh.pipeline import run_detection
    p, _ = run_detection(two_goal_video, _segs(calib_a, calib_b),
                         work_dir=str(tmp_path))
    evs = load_events(p)["events"]
    assert len(evs) == 2
    assert [e.status for e in evs] == ["high", "high"]
    assert evs[0].t < 3.0 < evs[1].t


def test_多段positionsjson按段存块(two_goal_video, calib_a, calib_b, tmp_path):
    from bbh.pipeline import run_detection
    run_detection(two_goal_video, _segs(calib_a, calib_b), work_dir=str(tmp_path))
    doc = json.loads((tmp_path / "positions.json").read_text(encoding="utf-8"))
    assert [b["t0"] for b in doc["segments"]] == [0.0, 3.0]


def test_单段仍写旧扁平格式(two_goal_video, calib_a, tmp_path):
    """单段 positions.json 格式不变（存量工具/测试断言直接读顶层键）。"""
    from bbh.pipeline import run_detection
    run_detection(two_goal_video, calib_a, work_dir=str(tmp_path))
    doc = json.loads((tmp_path / "positions.json").read_text(encoding="utf-8"))
    assert "calib" in doc and "shift" in doc and "positions" in doc
    assert "segments" not in doc


def test_补头段复用尾段缓存只扫前段(two_goal_video, calib_a, calib_b, tmp_path,
                                    monkeypatch):
    """09-04_1 实况：先用 B 标定整片跑过（旧扁平存档），事后补 A 头段——
    B 段 95 分钟缓存必须按标定指纹匹配复用（裁剪到段窗），只重扫头段。"""
    import bbh.pipeline as pl
    from bbh.pipeline import run_detection
    # 第一步：B 单段全片（旧扁平格式落盘）
    run_detection(two_goal_video, calib_b, work_dir=str(tmp_path))
    # 第二步：补 A 头段 → 段 [A@0, B@3.0]
    calls = []
    orig = pl.detect_positions

    def spy(video, calib, *a, **k):
        calls.append((k.get("resume_frame"), k.get("end_frame"),
                      calib.hoop_rect))
        return orig(video, calib, *a, **k)

    monkeypatch.setattr(pl, "detect_positions", spy)
    p, _ = run_detection(two_goal_video, _segs(calib_a, calib_b),
                         work_dir=str(tmp_path))
    # 只扫了头段：resume=0、几何=A；扫描窗按 DRIFT_PAD(5s) 向后外扩到
    # (3.0+5.0)s*30fps=240 帧（时间域裁剪保证边界不丢不重，帧号域只是近似）
    assert calls == [(0, 240, HOOP_A)]
    evs = load_events(p)["events"]
    assert [e.status for e in evs] == ["high", "high"]   # 两球都在


def test_多段全缓存重放不触碰检测器(two_goal_video, calib_a, calib_b, tmp_path,
                                    monkeypatch):
    import bbh.pipeline as pl
    from bbh.pipeline import run_detection
    segs = _segs(calib_a, calib_b)
    run_detection(two_goal_video, segs, work_dir=str(tmp_path))
    # 判定器升级 → 只重放。必须走 monkeypatch：裸赋值会永久改模块属性，
    # 泄漏给本文件之后的所有用例（按文件名序 test_multi_segment 先跑，
    # 排在后面的用例会拿到被污染的 LOGIC_VERSION）。
    monkeypatch.setattr(pl, "LOGIC_VERSION", pl.LOGIC_VERSION + 1)

    def no_detect(*a, **k):
        raise AssertionError("段块全命中缓存，检测器不应被调用")
    monkeypatch.setattr(pl, "detect_positions", no_detect)
    p, _ = run_detection(two_goal_video, segs, work_dir=str(tmp_path))
    assert len(load_events(p)["events"]) == 2


def test_段边界跨段事件冷却去重(calib_a, calib_b):
    """跨段相邻事件 <COOLDOWN_SEC：弃后保前（同判定器单段内冷却语义；
    段边界两侧的旧几何垃圾/重叠检出不该变成一对近重复行）。"""
    from bbh.goal_decider import COOLDOWN_SEC
    from bbh.models import GoalEvent
    from bbh.pipeline import _merge_segment_events
    a = GoalEvent(t=2.90, confidence=0.9, status="high")
    b = GoalEvent(t=3.10, confidence=0.85, status="high")   # 跨界 0.2s
    c = GoalEvent(t=3.10 + COOLDOWN_SEC, confidence=0.8, status="high")
    merged = _merge_segment_events([[a], [b, c]], [0.0, 3.0])
    assert [e.t for e in merged] == [2.90, c.t]              # b 被冷却掉


def test_段边界移动后旧段尾不再报警告(two_goal_video, calib_a, calib_b, tmp_path):
    """A1 覆盖孔洞按段窗算：A 段扫描止于 3.0s（它的窗就到那），不得把
    「段尾之后没采样」误报成校正失效。"""
    from bbh.pipeline import run_detection
    _, warning = run_detection(two_goal_video, _segs(calib_a, calib_b),
                               work_dir=str(tmp_path))
    assert not warning or "未覆盖时段" not in warning


# ---------- app：段文件管理（标定设置页） ----------

import bbh.app as app_module                      # noqa: E402 —— app 层测试用
from bbh import _gr_compat as gr                  # noqa: E402 —— Error 断言用


@pytest.fixture
def cwd(tmp_path, monkeypatch):
    """隔离手法=chdir(tmp)：走生产真实 _work_dir_for（work/<basename> 相对
    CWD）——段文件全部落在 tmp 的 work/ 下，摸不到真仓库根。"""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _vid(tmp_path):
    return str(tmp_path / "v.mp4")


def _rgb_frame():
    """标定页同款 RGB 参考帧（灰筐线 + 橙球）。"""
    img = np.full((H, W, 3), (50, 44, 40), np.uint8)
    cv2.line(img, (300, 154), (380, 154), (180, 180, 180), 3)
    cv2.circle(img, (340, 220), 8, (245, 100, 20), -1)
    return img


_PTS = [(300.0, 154.0), (380.0, 154.0), (340.0, 220.0)]


def test_load_segments单主标定即单段t0为0(cwd):
    app_module.build_calibration(_PTS, _rgb_frame(), _vid(cwd))
    segs = app_module.load_segments(_vid(cwd))
    assert [s.t0 for s in segs] == [0.0]
    assert segs[0].ref_image is not None       # 主段参考帧路径已解析
    assert segments_key(segs) == calib_key(segs[0].calib)   # 单段=旧指纹铁律


def test_补充段文件按t0排序发现(cwd):
    app_module.build_calibration(_PTS, _rgb_frame(), _vid(cwd))
    frame = _rgb_frame()
    app_module.build_segment_calibration(_PTS, frame, 600.0, _vid(cwd))
    app_module.build_segment_calibration(_PTS, frame, 1550.0, _vid(cwd))
    # 手工档防御：乱序直写一个更早 t0 的段文件（UI 路径强制追加序，
    # 但文件可手改）——发现层仍必须按 t0 升序
    Calibration(hoop_rect=HOOP_B, hsv_low=HSV[0], hsv_high=HSV[1],
                frame_size=(W, H), calib_t=300.0
                ).save(os.path.join("work", "v", "calibration_seg9.json"))
    segs = app_module.load_segments(_vid(cwd))
    assert [s.t0 for s in segs] == [0.0, 300.0, 600.0, 1550.0]
    # 各段参考帧真实存在（save_ref_image imencode+tofile，中文/绝对路径都稳）
    assert all(os.path.exists(s.ref_image) for s in segs if s.ref_image)
    md = app_module._seg_list_md(_vid(cwd))
    assert "10:00 起" in md and "25:50 起" in md


def test_build_segment_calibration起点校验(cwd):
    frame = _rgb_frame()
    with pytest.raises(gr.Error):              # t0 必须 >0（0:00 起归主标定管）
        app_module.build_segment_calibration(_PTS, frame, 0, _vid(cwd))
    with pytest.raises(gr.Error):              # 没有主标定不许直接补段
        app_module.build_segment_calibration(_PTS, frame, 100.0, _vid(cwd))
    app_module.build_calibration(_PTS, frame, _vid(cwd))
    app_module.build_segment_calibration(_PTS, frame, 600.0, _vid(cwd))
    with pytest.raises(gr.Error):              # 新段必须晚于现有最后一段
        app_module.build_segment_calibration(_PTS, frame, 300.0, _vid(cwd))


def test_删段移除文件与参考帧(cwd):
    frame = _rgb_frame()
    app_module.build_calibration(_PTS, frame, _vid(cwd))
    _, _, _, _ = app_module.build_segment_calibration(_PTS, frame, 1550.0,
                                                      _vid(cwd))
    seg_file = os.path.join("work", "v", "calibration_seg2.json")
    ref_file = os.path.join("work", "v", "calibration_ref_seg2.png")
    assert os.path.exists(seg_file) and os.path.exists(ref_file)

    md, dd, msg = app_module.del_segment("段2（25:50 起）", _vid(cwd))

    assert "已删除" in msg and "25:50" in msg
    assert not os.path.exists(seg_file) and not os.path.exists(ref_file)
    assert app_module.load_segments(_vid(cwd)).__len__() == 1
    assert dd["choices"] == []
    with pytest.raises(gr.Error):              # 没补充段可删
        app_module.del_segment("段2（25:50 起）", _vid(cwd))


def test_build_segment_calibration宽色域提示(cwd):
    """段标定同样提示宽色域（09-04_1 段1 即段标定踩的坑：远景 S=37）。"""
    app_module.build_calibration(_PTS, _rgb_frame(), _vid(cwd))  # 主标定（正常球）
    frame = np.full((H, W, 3), (50, 44, 40), np.uint8)
    cv2.line(frame, (300, 154), (380, 154), (180, 180, 180), 3)
    cv2.circle(frame, (340, 220), 8, (245, 210, 197), -1)   # 低饱橙 S≈50
    _, msg, _, _ = app_module.build_segment_calibration(_PTS, frame, 600.0,
                                                        _vid(cwd))
    assert "饱和度下限偏低" in msg
    _, msg, _, _ = app_module.build_segment_calibration(_PTS, _rgb_frame(),
                                                        900.0, _vid(cwd))
    assert "饱和度下限偏低" not in msg
