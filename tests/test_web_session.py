"""会话：空路径拒绝、新视频提示、已有结果载入、行结构字段齐全。"""
import json
import os

import pytest
from fastapi.testclient import TestClient

from bbh.models import Calibration, GoalEvent, save_events
from bbh.web import state
from bbh.web.app import create_app

client = TestClient(create_app())

NAME = "2026-09-11_3"


def _make_result(tmp_path, n=3):
    """造一份检测结果 + 标定（形状与真产物一致，字段名对齐 events.json）。"""
    work = tmp_path / "work" / NAME
    work.mkdir(parents=True)
    evs = [GoalEvent(
        t=12.1 + i * 60, confidence=0.90 - i * 0.10,
        status=("high", "review", "low")[i % 3],
        cross_off=0.02, above_n=3, above_span=0.6, below_n=2, below_span=0.4,
        descent_px=18.0, below_big=2, net_post=12.5, net_ctrl=3.1,
        net_local=True) for i in range(n)]
    save_events(str(work / "events.json"), video=str(tmp_path / f"{NAME}.mp4"),
                duration=480.0, events=evs, warning=None,
                calib_key=[1, 2, 3], logic_ver=1)
    Calibration(hoop_rect=(300, 140, 80, 40), hsv_low=(0, 80, 120),
                hsv_high=(25, 255, 255),
                frame_size=(1920, 1080)).save(str(work / "calibration.json"))


def _make_bare_result(tmp_path, n=1):
    """只有 t/conf/status 的结果（证据字段全 None，旧版 events.json 的形状），
    且不落 shooters.json——真数据里这种行是大头（复审实测：2026-08-29_3
    434/434 行全 null、2026-09-11_3 175/366 行 net 为 null）。"""
    work = tmp_path / "work" / NAME
    work.mkdir(parents=True)
    evs = [GoalEvent(t=12.1 + i * 60, confidence=0.42 - i * 0.01,
                     status="low") for i in range(n)]
    save_events(str(work / "events.json"), video=str(tmp_path / f"{NAME}.mp4"),
                duration=480.0, events=evs, warning=None,
                calib_key=[1, 2, 3], logic_ver=1)
    Calibration(hoop_rect=(300, 140, 80, 40), hsv_low=(0, 80, 120),
                hsv_high=(25, 255, 255),
                frame_size=(1920, 1080)).save(str(work / "calibration.json"))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state.SESSION.video_path = ""
    yield tmp_path
    state.SESSION.video_path = ""


def test_blank_path_is_rejected(env):
    r = client.post("/api/session", json={"videoPath": "   "})
    assert r.status_code == 400
    assert "请先填" in r.json()["error"]


def test_new_video_without_calibration_hints_calibration(env):
    r = client.post("/api/session", json={"videoPath": "D:/videos/nope.mp4"})
    assert r.status_code == 200
    j = r.json()
    assert j["loaded"] is False and j["rows"] == []
    assert "标定" in j["hint"]


def test_loads_existing_events_and_shapes_rows(env):
    _make_result(env)
    r = client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    assert r.status_code == 200
    j = r.json()
    assert j["loaded"] is True
    assert len(j["rows"]) == 3
    assert j["duration"] == 480.0
    assert "共 3 条" in j["summaryMd"]
    row = j["rows"][0]
    assert row["key"] == "00:12.1"          # 键 = 原始检测时刻
    assert row["t"] == "00:12.1"
    assert row["status"] == "high"
    assert row["conf"] == pytest.approx(0.90)
    assert row["selection"] is False       # 没存过 selection.json = 全不勾
    assert row["before"] == 5 and row["after"] == 2
    assert row["ev"]["crossOff"] == 0.02 and row["ev"]["aboveN"] == 3
    assert row["net"] == {"post": 12.5, "ctrl": 3.1, "local": True}
    assert row["terms"]["base"] == 0.30
    assert "判定依据" in row["reason"]


def test_rows_without_evidence_are_null_not_crash(env):
    """null 是契约的一部分：前端必须按可空消费。

    行结构示例画的是对象（row.net.post），但真数据里 ev/net/terms/shooter
    大面积是 null（老结果没记依据字段、该球没溯源、网信号没测）。null 就是
    「这条证据不存在」的正确表达——照示例写 row.net.post 会在真实视频上直接
    炸。这里把可空性钉死：四个证据块为 null，同时 UI 必需的基础字段仍在。"""
    _make_bare_result(env)
    r = client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    assert r.status_code == 200
    j = r.json()
    assert j["loaded"] is True and len(j["rows"]) == 1
    row = j["rows"][0]
    assert row["ev"] is None
    assert row["net"] is None
    assert row["terms"] is None
    assert row["shooter"] is None
    # 基础字段不因证据缺失而消失，类型也不变（前端列渲染全靠它们）
    assert row["key"] == "00:12.1" and row["t"] == "00:12.1"
    assert row["status"] == "low"
    assert isinstance(row["conf"], float) and row["conf"] == pytest.approx(0.42)
    assert row["selection"] is False
    assert row["before"] == 5 and row["after"] == 2
    assert row["tag"] == ""
    assert isinstance(row["reason"], str) and row["reason"]


def test_state_endpoint_returns_same_payload(env):
    _make_result(env)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    j = client.get("/api/state").json()
    assert j["loaded"] is True and len(j["rows"]) == 3
    assert j["calib"]["ready"] is True
    assert j["flags"] == {"thumb": [], "cross": []}
    assert j["feedback"]["goal"] == 0 and j["feedback"]["miss"] == 0


def test_state_without_session_says_fill_path(env):
    j = client.get("/api/state").json()
    assert j["loaded"] is False and "路径" in j["hint"]


def test_last_path_is_remembered_in_ui_prefs(env):
    client.post("/api/session", json={"videoPath": "D:/videos/a.mp4"})
    assert json.loads((env / "ui_prefs.json").read_text("utf-8"))["lastVideo"] \
        == "D:/videos/a.mp4"
    assert client.get("/api/state").json()["lastVideo"] == "D:/videos/a.mp4"


def test_broken_prefs_file_is_repaired_not_500(env):
    """ui_prefs.json 被手改/被别的程序写坏成 42（非 dict）时，
    prefs["lastVideo"] 会抛 TypeError 冒成 500——每一步开视频都炸。
    必须当空档处理并重写成合法 dict（偏好文件坏不该挡住开视频）。"""
    (env / "ui_prefs.json").write_text("42", encoding="utf-8")
    r = client.post("/api/session", json={"videoPath": "D:/videos/a.mp4"})
    assert r.status_code == 200
    data = json.loads((env / "ui_prefs.json").read_text("utf-8"))
    assert isinstance(data, dict) and data["lastVideo"] == "D:/videos/a.mp4"
    assert client.get("/api/state").json()["lastVideo"] == "D:/videos/a.mp4"


def test_malformed_body_is_400_with_error_key_not_422_detail(env):
    """失败恒为 {"error": 中文}——FastAPI 默认的 422 {"detail": [...]}
    被全局 handler 翻成 400 同款（前端只认 error 字段）。"""
    r = client.post("/api/session", json={"videoPath": 12345})
    assert r.status_code == 400
    assert "error" in r.json() and "detail" not in r.json()


def test_verdicts_map_is_empty_in_the_base_payload():
    """空会话也要有 verdicts 键：前端无条件读它，缺键会渲染成 undefined。"""
    j = client.get("/api/state").json()
    assert j["verdicts"] == {}


def test_verdicts_map_is_per_row(env):
    # 本模块的 env 只负责 chdir + 复位会话（既有用例各自调 _make_result 造结果，
    # 那边 mkdir 不带 exist_ok，env 里造会撞 FileExistsError）——裁决要真行，
    # 这两句自己来，与下面 t_str 用例同款自给自足。
    _make_result(env)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    client.post("/api/feedback/0:12.1", json={"verdict": "not_goal"})
    client.post("/api/feedback/1:12.1", json={"verdict": "goal"})
    j = client.get("/api/state").json()
    assert j["verdicts"] == {"00:12.1": "not_goal", "01:12.1": "goal"}
    # 汇总结数照旧（两处同源，不许分叉）
    assert j["feedback"]["goal"] == 1 and j["feedback"]["no"] == 1
    # 取消裁决 → 该行不再亮灯
    client.post("/api/feedback/0:12.1", json={"verdict": "not_goal"})
    assert client.get("/api/state").json()["verdicts"] == {"01:12.1": "goal"}


def test_verdicts_key_is_the_recorded_string_not_a_recompute(tmp_path,
                                                             monkeypatch):
    """进位边界：t=59.96 的行键是 "00:60.0"（models.time_key 的产物），
    而 60.0 那行的键是 "01:00.0"——两颗不同的球。
    映射必须用记录里的 t_str，不能 _fmt_t(t) 复算：复算会把 59.96 那行
    算成 "01:00.0"，于是 59.96 永不亮灯、60.0 被误亮。"""
    from test_web_events import _make_times
    monkeypatch.chdir(tmp_path)
    _make_times(tmp_path, [59.96, 60.0])
    state.SESSION.video_path = ""
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    assert [r["key"] for r in client.get("/api/state").json()["rows"]] \
        == ["00:60.0", "01:00.0"]
    r = client.post("/api/feedback/00:60.0", json={"verdict": "goal"})
    assert r.status_code == 200
    assert client.get("/api/state").json()["verdicts"] == {"00:60.0": "goal"}


# ---------- A10「打开数据目录」 ----------
#
# 后端只做一件事：在资源管理器里打开**工作区根**。四条测试里有三条替换掉
# `_reveal`（不真弹窗），所以最后一条专门钉住「真的去调了 os.startfile」——
# 否则把 `_reveal` 改成空函数，前三条照样全绿，而按钮什么都不做。

def test_open_data_dir_reveals_the_workspace_root(env, monkeypatch, tmp_path):
    """打开的是**工作区根**：入口 prepare_cwd() 切过去、`work/` 与
    `tags.json` 所在的那个目录（非冻结＝仓库根，冻结＝`<exe>/data`）。"""
    import bbh.web.api_session as M
    got = []
    monkeypatch.setattr(M, "_reveal", got.append, raising=False)
    r = client.post("/api/session/open-data-dir")
    assert r.status_code == 200, r.text
    assert got == [os.path.realpath(str(tmp_path))]


def test_open_data_dir_ignores_any_path_supplied_by_the_client(env, monkeypatch,
                                                              tmp_path):
    """⛔ 不接受请求里的任意路径（防越权）：查询串里的 `?path=`、body 里的
    `path` 都不理，打开的永远是工作区根（与 `media.py` 同一条收束思路）。"""
    import bbh.web.api_session as M
    got = []
    monkeypatch.setattr(M, "_reveal", got.append, raising=False)
    r1 = client.post("/api/session/open-data-dir",
                     params={"path": r"C:\Windows"})
    r2 = client.post("/api/session/open-data-dir",
                     json={"path": r"C:\Windows"})
    assert (r1.status_code, r2.status_code) == (200, 200), (r1.text, r2.text)
    assert got == [os.path.realpath(str(tmp_path))] * 2
    # 明写一句：拿到的路径里不含客户端给的那段
    assert not any("Windows" in p for p in got)


def test_open_data_dir_failure_returns_the_error_contract(env, monkeypatch):
    """打不开 ⇒ 400，且响应体**只有** `error` 一个键（前端只认它）。"""
    import bbh.web.api_session as M
    monkeypatch.setattr(M, "_reveal", lambda p: (_ for _ in ()).throw(
        RuntimeError("没有资源管理器")), raising=False)
    r = client.post("/api/session/open-data-dir")
    assert r.status_code == 400
    assert list(r.json()) == ["error"]
    assert "打开数据目录失败" in r.json()["error"]


@pytest.mark.skipif(not hasattr(os, "startfile"), reason="只有 Windows 能弹资源管理器")
def test_reveal_actually_calls_startfile(monkeypatch, tmp_path):
    """默认实现**真的**去开资源管理器（不是占位）：`_reveal` 调
    `os.startfile(path)`。前三条都替换了 `_reveal`，只有这条钉住它本体。"""
    import bbh.web.api_session as M
    called = []
    monkeypatch.setattr(os, "startfile", called.append)
    M._reveal(str(tmp_path))
    assert called == [str(tmp_path)]


# ---------- 依据卡算式分项（v18 网分项 / legacy / B2 注入行口径） ----------

def _term_row(**kw):
    """算式对账行：cross_off=0 → 位置分满 0.20，above/below 全空 → 两侧 0。"""
    base = dict(t=10.0, confidence=0.30, status="review", cross_off=0.0)
    base.update(kw)
    return GoalEvent(**base)


def test_terms_v18行_net入算式_对账_legacy_false():
    import bbh.web.api_session as M
    t = M._terms(_term_row(confidence=0.20, net_post=2.0, net_local=True))
    assert t["net"] == -0.30                       # v19 静止档：0.30+0+0+0.20−0.30=0.20
    assert t["legacy"] is False


def test_terms_旧档行_conf不含网分_legacy_true():
    import bbh.web.api_session as M
    t = M._terms(_term_row(confidence=0.75, net_post=2.0, net_local=True))
    assert t["net"] == -0.30 and t["legacy"] is True   # 0.75 ≠ 0.20：≤v17 旧档


def test_terms_B2注入行_网分项按0_不误挂legacy():
    import bbh.web.api_session as M
    # 审阅修复（Important 1）：九场 132/240 条 b2 行实测「静止」，若按实测
    # 入算式则和式≠conf → 误弹「重跑检测后一致」——对注入行这是假话（重跑
    # 结果相同，分按 0 是 spec §3.5 设计）。
    t = M._terms(_term_row(confidence=0.50, review_reason="b2",
                           net_post=2.0, net_local=True))
    assert t["net"] == 0.0
    assert t["legacy"] is False
