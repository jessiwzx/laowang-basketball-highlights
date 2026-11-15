"""出手人墙：两墙数据、归人/移出、护栏、排序模式。"""
import json

import pytest
from fastapi.testclient import TestClient

from bbh.models import GoalEvent, load_events, save_events
from bbh.web import state
from bbh.web.app import create_app

from test_web_session import NAME, _make_result

client = TestClient(create_app())


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _make_result(tmp_path, n=3)
    work = tmp_path / "work" / NAME
    # 回扫产物：3 球各一张缩略图 + traces（墙只显示缩略图存在的球）
    (work / "shots").mkdir(exist_ok=True)
    traces = {}
    for i, key in enumerate(("00:12.1", "01:12.1", "02:12.1")):
        p = work / "shots" / f"{i}.jpg"
        p.write_bytes(b"\xff\xd8\xff\xe0jpg")
        traces[key] = {"status": "ok", "release_t": 12.1 + i * 60,
                       "thumbs": [f"shots/{i}.jpg"]}
    (work / "shooters.json").write_text(
        json.dumps({"traces": traces}, ensure_ascii=False), encoding="utf-8")
    state.SESSION.video_path = ""
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    yield tmp_path
    state.SESSION.video_path = ""


def test_wall_lists_all_scanned_balls_unassigned(env):
    j = client.get("/api/wall").json()
    assert [x["key"] for x in j["unassigned"]] == ["00:12.1", "01:12.1", "02:12.1"]
    assert j["assigned"] == []
    assert j["people"] == []
    assert j["unassigned"][0]["thumb"].startswith("/media/work?path=")
    assert j["sorts"] == ["时间", "外观相似", "出手点"]
    assert j["minGapS"] > 0               # 护栏阈值随数据下发，前端照实显示


def test_unknown_sort_falls_back_to_time(env):
    assert client.get("/api/wall", params={"sort": "乱写"}).status_code == 200


def test_assign_moves_ball_to_person_and_writes_groups(env):
    r = client.post("/api/wall/assign",
                    json={"person": "老杨", "keys": ["00:12.1", "01:12.1"]})
    assert r.status_code == 200
    j = r.json()
    assert j["people"] == ["老杨"]
    grp = [g for g in j["assigned"] if g["person"] == "老杨"][0]
    assert [x["key"] for x in grp["items"]] == ["00:12.1", "01:12.1"]
    assert [x["key"] for x in j["unassigned"]] == ["02:12.1"]
    assert sorted(j["manual_groups"]["老杨"]) == ["00:12.1", "01:12.1"]
    # 归人时新输入的名字**不进全局标签库**，但必须进归人下拉候选
    # （否则再想把这球归给同一个人就选不到了）
    assert "老杨" in j["personChoices"]
    assert j["msg"].startswith("✅ 2 球 → 老杨")


def test_guard_silent_for_far_apart_goals(env):
    """夹具相邻两球差 60s ≫ MIN_GOAL_GAP_S(2.5s)——同人完全可能，不警示。"""
    client.post("/api/wall/assign", json={"person": "老杨", "keys": ["00:12.1"]})
    r = client.post("/api/wall/assign",
                    json={"person": "老杨", "keys": ["01:12.1"]})
    assert "护栏" not in r.json()["msg"]


def test_guard_triggers_on_close_pair(env):
    """00:12.1 与 00:12.5 相隔 0.4s < 2.5s——同一人物理上不太可能，出护栏警示。
    （护栏警示不阻断：两球照样都归到老杨名下。）"""
    work = env / "work" / NAME
    traces = json.loads((work / "shooters.json").read_text("utf-8"))
    p = work / "shots" / "close.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0jpg")
    traces["traces"]["00:12.5"] = {"status": "ok", "release_t": 12.5,
                                  "thumbs": ["shots/close.jpg"]}
    (work / "shooters.json").write_text(json.dumps(traces, ensure_ascii=False),
                                        encoding="utf-8")
    client.post("/api/wall/assign", json={"person": "老杨", "keys": ["00:12.1"]})
    r = client.post("/api/wall/assign", json={"person": "老杨", "keys": ["00:12.5"]})
    assert "护栏" in r.json()["msg"] and "0.4s" in r.json()["msg"]
    # 本次点选 1 球 → 计数就是 1（brief 原文写「2 球」是把该人**整组**球数
    # 当成了本次点选数；msg 逐字来自 app.assign_person 的 `len(sel)`，
    # sel=本次点选。api 层不自己拼 msg——拼了就是第二个文案来源）。
    # 护栏查的是整组（违例跨在旧球 00:12.1 与新球 00:12.5 之间），这才是
    # 本用例的要点：单球增量也触发。
    assert r.json()["msg"].startswith("✅ 1 球 → 老杨")   # 警示在后，归人照做


def test_assign_without_keys_is_400(env):
    r = client.post("/api/wall/assign", json={"person": "老杨", "keys": []})
    assert r.status_code == 400
    assert "墙上点选" in r.json()["error"]


def test_assign_with_blank_person_is_400(env):
    r = client.post("/api/wall/assign", json={"person": "  ", "keys": ["00:12.1"]})
    assert r.status_code == 400
    assert "人名" in r.json()["error"]


def test_unassign_returns_ball_to_pool(env):
    client.post("/api/wall/assign", json={"person": "老杨", "keys": ["00:12.1"]})
    r = client.post("/api/wall/unassign", json={"keys": ["00:12.1"]})
    assert r.status_code == 200
    j = r.json()
    assert j["assigned"] == [] and len(j["unassigned"]) == 3
    assert j["manual_groups"] == {}


def test_unassign_clears_tag_and_keeps_selection(env):
    """移出归人：标签跟着清（归人是双写，assign 写标签、unassign 就得撤标签，
    否则按人集锦还按 tag 收这颗球），**核对勾选不动**（勾选=进球核对标记，
    与出手人无关）。"""
    client.patch("/api/events/0:12.1", json={"tag": "老杨"})     # 打标签=自动勾选
    client.post("/api/wall/assign", json={"person": "老杨", "keys": ["00:12.1"]})
    client.post("/api/wall/unassign", json={"keys": ["00:12.1"]})
    j = client.get("/api/state").json()
    row = [x for x in j["rows"] if x["key"] == "00:12.1"][0]
    assert row["tag"] == "" and row["selection"] is True


def test_guard_endpoint_reports_group_and_violations(env):
    client.post("/api/wall/assign", json={"person": "老杨", "keys": ["00:12.1"]})
    j = client.get("/api/wall/guard", params={"person": "老杨"}).json()
    assert j["person"] == "老杨" and j["n"] == 1 and j["keys"] == ["00:12.1"]
    assert j["violations"] == []
    assert j["msg"] == ""              # 无违例 = 无话可说，前端不弹


def test_guard_on_unknown_person_is_empty(env):
    j = client.get("/api/wall/guard", params={"person": "查无此人"}).json()
    assert j["n"] == 0 and j["keys"] == [] and j["violations"] == []


# ---------- Fix round 1：输入校验与键归属 ----------

def _add_ball(env, key, t, rel="shots/close.jpg", event=True):
    """补一颗球：trace（两墙只认 trace 里的图）+ 一条 events 行。

    「墙上看得见」与「在当前 events 行里」是两码事——默认两样都给，
    让排序/护栏类用例不依赖键归属判据取哪一个；只给 trace 的那条路
    由 test_wall_visible_ball_without_event_row_is_assignable 专钉。"""
    work = env / "work" / NAME
    (work / rel).write_bytes(b"\xff\xd8\xff\xe0jpg")
    data = json.loads((work / "shooters.json").read_text("utf-8"))
    data["traces"][key] = {"status": "ok", "release_t": t, "thumbs": [rel]}
    (work / "shooters.json").write_text(json.dumps(data, ensure_ascii=False),
                                        encoding="utf-8")
    if not event:
        return
    p = str(work / "events.json")
    d = load_events(p)
    evs = sorted(d["events"] + [GoalEvent(t=t, confidence=0.42, status="low")],
                 key=lambda e: e.t)
    save_events(p, video=d["video"], duration=d["duration"], events=evs,
                warning=d["warning"], calib_key=d["calib_key"],
                logic_ver=d["logic_ver"], motion=d["motion"])


def _set_spot_xy(env, x_by_key):
    """给 traces 补 release_xy，让「出手点」序与时间序**相反**——否则排序
    用例恒同序，断言等于没有鉴别力。"""
    work = env / "work" / NAME
    data = json.loads((work / "shooters.json").read_text("utf-8"))
    for key, x in x_by_key.items():
        data["traces"][key]["release_xy"] = [x, 200.0]
    (work / "shooters.json").write_text(json.dumps(data, ensure_ascii=False),
                                        encoding="utf-8")


def test_assign_with_unknown_key_writes_no_ghost(env):
    """墙上没有的键必须被丢弃：manual.assign 只过滤「非空字符串」，裸透传
    会把 99:99.9 落成 {"老杨": ["99:99.9"]}——墙上没这项、用户点不到也
    撤不掉，只能手改 sidecar。丢弃后空表 → 原函数抛 gr.Error → 400。"""
    r = client.post("/api/wall/assign",
                    json={"person": "老杨", "keys": ["99:99.9"]})
    assert r.status_code == 400
    assert "墙上点选" in r.json()["error"]          # 原函数文案，逐字未动
    assert not (env / "work" / NAME / "manual_groups.json").exists()
    j = client.get("/api/wall").json()
    assert j["people"] == [] and j["assigned"] == []
    assert j["personChoices"] == []                 # 下拉候选也未被污染


def test_assign_keeps_only_keys_of_this_video(env):
    """混合输入：只有墙上真有的键落盘，msg 计的是过滤后的球数。"""
    r = client.post("/api/wall/assign",
                    json={"person": "老杨", "keys": ["00:12.1", "99:99.9"]})
    assert r.status_code == 200
    j = r.json()
    assert sorted(j["manual_groups"]["老杨"]) == ["00:12.1"]
    assert j["msg"].startswith("✅ 1 球 → 老杨")
    assert [x["key"] for x in j["unassigned"]] == ["01:12.1", "02:12.1"]


def test_unassign_with_unknown_key_is_400_and_writes_nothing(env):
    """移出同样求交：失配键不落到 manual.assign/unassign 上，原有归人不动。"""
    client.post("/api/wall/assign", json={"person": "老杨", "keys": ["00:12.1"]})
    r = client.post("/api/wall/unassign", json={"keys": ["99:99.9"]})
    assert r.status_code == 400
    assert "墙上点选" in r.json()["error"]
    assert client.get("/api/wall").json()["manual_groups"] == {"老杨": ["00:12.1"]}


def test_string_keys_body_is_400_and_writes_nothing(env):
    """keys 传字符串（不是数组）：裸 dict 时会按**字符**落盘
    （{"老杨": ["0", ":", "1", ...]}），随后 gap_violations 里 _goal_t("0")
    抛 ValueError → 500——而 500 响应时盘**已经**被写脏。模型校验挡在写盘前。"""
    work = env / "work" / NAME
    r = client.post("/api/wall/assign", json={"person": "老杨", "keys": "00:12.1"})
    assert r.status_code == 400
    assert "error" in r.json() and "detail" not in r.json()
    assert not (work / "manual_groups.json").exists()

    client.post("/api/wall/assign", json={"person": "老杨", "keys": ["00:12.1"]})
    before = (work / "manual_groups.json").read_text("utf-8")
    r2 = client.post("/api/wall/unassign", json={"keys": "00:12.1"})
    assert r2.status_code == 400
    assert (work / "manual_groups.json").read_text("utf-8") == before
    # keys 是数字数组也一样是畸形体（不是 500）
    assert client.post("/api/wall/assign",
                       json={"person": "老杨", "keys": [12.1]}).status_code == 400
    # person 非字符串（int）：裸 dict 会在 app.assign_person 的
    # `(person or "").strip()` 上 AttributeError → 500。畸形请求一律 400。
    assert client.post("/api/wall/assign",
                       json={"person": 123, "keys": ["00:12.1"]}).status_code == 400


def test_post_echoes_sort_and_wall_order_follows_it(env):
    """写端点回包要回显并沿用当前排序：前端「归人后就地替换整墙」若拿时间序
    顶替出手点序的墙，排序会被静默打回时间序（旧界面保留下拉当前值）。"""
    _set_spot_xy(env, {"00:12.1": 500.0, "01:12.1": 300.0, "02:12.1": 100.0})
    j = client.post("/api/wall/assign",
                    json={"person": "老杨", "keys": ["00:12.1"],
                          "sort": "出手点"}).json()
    assert j["sort"] == "出手点"
    # 00 已归人、剩下两球按出手点 x 升序（时间序会是反的）
    assert [x["key"] for x in j["unassigned"]] == ["02:12.1", "01:12.1"]
    u = client.post("/api/wall/unassign",
                    json={"keys": ["00:12.1"], "sort": "出手点"}).json()
    assert u["sort"] == "出手点"
    assert [x["key"] for x in u["unassigned"]] == ["02:12.1", "01:12.1", "00:12.1"]


def test_unknown_sort_really_falls_back_to_time_order(env):
    """未知 sort：不只 200——回包 sort 回「时间」，且顺序**真是**时间序
    （同数据下出手点序与它相反，证明这条断言有鉴别力）。"""
    _set_spot_xy(env, {"00:12.1": 500.0, "01:12.1": 300.0, "02:12.1": 100.0})
    j = client.get("/api/wall", params={"sort": "乱写"}).json()
    assert j["sort"] == "时间"
    assert [x["key"] for x in j["unassigned"]] == ["00:12.1", "01:12.1", "02:12.1"]
    j2 = client.get("/api/wall", params={"sort": "出手点"}).json()
    assert j2["sort"] == "出手点"
    assert [x["key"] for x in j2["unassigned"]] == ["02:12.1", "01:12.1", "00:12.1"]


def test_guard_keys_keep_storage_order_and_violations_sorted_by_time(env):
    """guard 的 keys = manual_groups.json 的**存储序**（本次点选在前，非时刻
    序）；violations 由 gap_violations 内部按解析时刻排序 → a 恒早于 b。"""
    _add_ball(env, "00:12.5", 12.5)
    # 一次点选两球，点选序 = 晚→早（与时刻序相反）
    r = client.post("/api/wall/assign",
                    json={"person": "老杨", "keys": ["00:12.5", "00:12.1"]})
    assert r.status_code == 200
    j = client.get("/api/wall/guard", params={"person": "老杨"}).json()
    assert j["keys"] == ["00:12.5", "00:12.1"]      # 存储序 = 点选序
    assert j["n"] == 2
    assert j["violations"] == [{"a": "00:12.1", "b": "00:12.5", "gapS": 0.4}]


def test_wall_visible_ball_without_event_row_is_assignable(env):
    """键归属判据 = 「墙上可见」而非「在当前 events 行里」。

    两墙的候选集来自 traces（有图、未删），**不看** events 行：重跑检测后
    旧 trace 仍在、或 trace 与 events 不同步时，那些球照样挂在墙上、用户
    照样点得到。点得到的就必须归得进、也移得出——否则界面上撤不掉（只能
    手改 sidecar），正是本次加键过滤要消灭的症状。旧界面就是这个口径
    （点得到的就能归）。"""
    _add_ball(env, "00:12.5", 12.5, event=False)     # 只有 trace，没有 events 行
    r = client.post("/api/wall/assign", json={"person": "老杨", "keys": ["00:12.5"]})
    assert r.status_code == 200, r.text
    assert r.json()["msg"].startswith("✅ 1 球 → 老杨")
    u = client.post("/api/wall/unassign", json={"keys": ["00:12.5"]})
    assert u.status_code == 200, u.text
    assert u.json()["manual_groups"] == {}
