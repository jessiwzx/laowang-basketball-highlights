import json
from bbh.models import Calibration, BallPos, GoalEvent, ClipSettings


def test_calibration_roundtrip(tmp_path):
    calib = Calibration(hoop_rect=(300, 140, 80, 40),
                        hsv_low=(0, 80, 120), hsv_high=(25, 255, 255),
                        frame_size=(640, 360))
    p = tmp_path / "calibration.json"
    calib.save(p)
    loaded = Calibration.load(p)
    assert loaded.hoop_rect == (300, 140, 80, 40)
    assert loaded.hsv_low == (0, 80, 120)
    assert loaded.frame_size == (640, 360)


def test_goal_event_status_layering():
    assert GoalEvent(t=10.0, confidence=0.9, status="high").status == "high"
    assert GoalEvent(t=20.0, confidence=0.55, status="review").status == "review"


def test_clip_settings_default():
    s = ClipSettings()
    assert (s.before, s.after) == (15.0, 5.0)


def test_events_json_roundtrip(tmp_path):
    from bbh.models import save_events, load_events
    p = tmp_path / "events.json"
    save_events(p, video="a.mp4", duration=3600.0,
                events=[GoalEvent(t=100.0, confidence=0.9, status="high")],
                warning=None)
    data = load_events(p)
    assert data["duration"] == 3600.0
    assert data["events"][0].t == 100.0
    assert data["warning"] is None


def test_goal_event_判定依据字段往返(tmp_path):
    e = GoalEvent(t=100.0, confidence=0.84, status="high", cross_off=-0.25,
                  above_n=3, below_n=2, above_span=0.25, below_span=0.1,
                  pop_back=0.4)
    d = e.to_dict()
    assert d["cross_off"] == -0.25 and d["pop_back"] == 0.4
    e2 = GoalEvent.from_dict(d)
    assert (e2.cross_off, e2.above_n, e2.pop_back) == (-0.25, 3, 0.4)


def test_goal_event_旧版json缺依据键不炸():
    """升级前生成的 events.json 没有依据字段 → 全部 None（UI 提示旧版未记录）。"""
    e = GoalEvent.from_dict({"t": 10.0, "confidence": 0.7, "status": "high"})
    assert (e.cross_off, e.above_n, e.below_n,
            e.above_span, e.below_span, e.pop_back) == (None,) * 6


def test_load_events_按时间升序返回(tmp_path):
    """2026-10-11 用户裁定：剪辑台「全部」按时间升序——B2 注入行（v16 起
    追加表尾）等存储乱序在读入层统一归位。写档顺序不动（重放对账按多重集）；
    标签/勾选/归人 sidecar 全按 t 键寻址，与行序无关。"""
    from bbh.models import load_events, save_events
    p = tmp_path / "events.json"
    save_events(p, video="a.mp4", duration=3600.0,
                events=[GoalEvent(t=200.0, confidence=0.9, status="high"),
                        GoalEvent(t=95.0, confidence=0.6, status="review"),
                        GoalEvent(t=95.0, confidence=0.7, status="high"),
                        GoalEvent(t=120.0, confidence=0.3, status="low")],
                warning=None)
    evs = load_events(p)["events"]
    assert [e.t for e in evs] == [95.0, 95.0, 120.0, 200.0]   # 同 t 稳定保序


def test_atomic_write_no_tmp_leftover(tmp_path):
    from bbh.models import save_events, load_events
    p = tmp_path / "events.json"
    save_events(p, video="a.mp4", duration=60.0,
                events=[GoalEvent(t=10.0, confidence=0.8, status="high")],
                warning=None)
    assert list(tmp_path.glob("*.tmp")) == []
    data = load_events(p)
    assert data["events"][0].t == 10.0
