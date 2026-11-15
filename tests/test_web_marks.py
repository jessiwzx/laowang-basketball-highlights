"""裁决反馈与误标标记：各写各的 sidecar，互不干扰。"""
import json

import pytest
from fastapi.testclient import TestClient

from bbh.web import state
from bbh.web.app import create_app

from test_web_session import NAME, _make_result

client = TestClient(create_app())


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _make_result(tmp_path, n=3)
    state.SESSION.video_path = ""
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    yield tmp_path
    state.SESSION.video_path = ""


def _work(env, name):
    p = env / "work" / NAME / name
    return json.loads(p.read_text("utf-8")) if p.exists() else None


def test_not_goal_verdict_writes_feedback_with_evidence(env):
    r = client.post("/api/feedback/0:12.1", json={"verdict": "not_goal"})
    assert r.status_code == 200
    fb = _work(env, "feedback.json")
    assert fb["verdicts"][0]["verdict"] == "not_goal"
    assert fb["verdicts"][0]["t"] == pytest.approx(12.1)
    ev = fb["verdicts"][0]["evidence"]
    assert ev["cross_off"] == 0.02 and ev["above_n"] == 3   # 证据快照防重跑覆盖
    assert "已标记" in r.json()["msg"]


def test_same_verdict_twice_cancels(env):
    client.post("/api/feedback/0:12.1", json={"verdict": "not_goal"})
    r = client.post("/api/feedback/0:12.1", json={"verdict": "not_goal"})
    assert _work(env, "feedback.json")["verdicts"] == []
    assert "已取消" in r.json()["msg"]


def test_switching_verdict_overwrites(env):
    client.post("/api/feedback/0:12.1", json={"verdict": "not_goal"})
    r = client.post("/api/feedback/0:12.1", json={"verdict": "goal"})
    fid = _work(env, "feedback.json")["verdicts"]
    assert len(fid) == 1 and fid[0]["verdict"] == "goal"
    assert "已改判" in r.json()["msg"]


def test_bad_verdict_word_is_400(env):
    r = client.post("/api/feedback/0:12.1", json={"verdict": "maybe"})
    assert r.status_code == 400
    assert "verdict" in r.json()["error"]


def test_verdict_on_unknown_key_is_404(env):
    assert client.post("/api/feedback/9:99.9",
                        json={"verdict": "goal"}).status_code == 404


def test_feedback_summary_counts_reach_the_payload(env):
    client.post("/api/feedback/0:12.1", json={"verdict": "not_goal"})
    client.post("/api/feedback/1:12.1", json={"verdict": "goal"})
    fb = client.get("/api/state").json()["feedback"]
    assert fb["goal"] == 1 and fb["no"] == 1 and fb["miss"] == 0
    assert "已裁决" in fb["summaryMd"]


def test_thumb_flag_toggles_and_is_independent(env):
    r = client.post("/api/flags/0:12.1", json={"flag": "thumb"})
    assert r.status_code == 200
    assert _work(env, "thumb_flags.json") == ["00:12.1"]
    assert _work(env, "cross_flags.json") is None       # 互不干扰
    assert _work(env, "feedback.json") is None          # 误标 ≠ 裁决
    assert r.json()["flags"]["thumb"] == ["00:12.1"]
    # 清单里的键逐字来自 models.time_key（"00:12.1"，分钟补零）——与本测试
    # 上面 thumb_flags.json 的内容同源：msg 是 app._thumb_flag_md 的原文，
    # api 层只转发不重排（brief 这里写成 "0:12.1" 是手写笔误，见报告）
    assert r.json()["msg"] == "⚠ 已标记 1 张缩略图有误：00:12.1"
    r2 = client.post("/api/flags/0:12.1", json={"flag": "thumb"})   # 再点=取消
    assert _work(env, "thumb_flags.json") == []
    assert r2.json()["flags"]["thumb"] == []
    assert r2.json()["msg"] == "⚠ 缩略图有误"           # 清单空了 → 回按钮名


def test_cross_flag_uses_its_own_file(env):
    client.post("/api/flags/1:12.1", json={"flag": "cross"})
    assert _work(env, "cross_flags.json") == ["01:12.1"]
    assert client.get("/api/state").json()["flags"]["cross"] == ["01:12.1"]


def test_unknown_flag_name_is_400(env):
    r = client.post("/api/flags/0:12.1", json={"flag": "wat"})
    assert r.status_code == 400
    assert "flag" in r.json()["error"]


def test_verdict_msg_uses_display_offset_and_keeps_t_str_raw(env):
    """裁决 toast 的时刻吃显示偏移（三处文案时间基其二）；feedback.json 的
    t_str/t 仍原始（存储键 + 匹配容差，不能就地替换——用户裁定）。"""
    r = client.post("/api/feedback/0:12.1",
                    json={"verdict": "goal", "offset": 1.5})
    assert r.status_code == 200
    assert "00:13.6" in r.json()["msg"]
    fb = _work(env, "feedback.json")
    assert fb["verdicts"][0]["t_str"] == "00:12.1"
    assert fb["verdicts"][0]["t"] == pytest.approx(12.1)
    # 改判句同样吃偏移；负偏移夹 0（与 api_gen._apply_session_rows 的平移同义）
    r2 = client.post("/api/feedback/0:12.1",
                     json={"verdict": "not_goal", "offset": -20})
    assert "00:00.0" in r2.json()["msg"]
    fb2 = _work(env, "feedback.json")
    assert fb2["verdicts"][0]["t_str"] == "00:12.1"
    assert r2.json()["verdicts"]["00:12.1"] == "not_goal"   # 行键命中不受偏移影响


def test_verdict_nonfinite_offset_is_treated_as_absent(env):
    """E1 G2g：非有限 offset 一律当没传（api_gen._finite 冻结口径）。
    重点断 NaN 那半的 msg——旧行为是 200 + 成功文案里藏 00:00.0（比 500 隐蔽）。
    字符串形状在 float|None 的 pydantic 门口就 422，不是 _finite 的可达输入（R9）。"""
    for bad in (float("nan"), float("inf"), float("-inf"), None):
        # httpx 的 json= 走 allow_nan=False——NaN/±inf 在测试进程里就 ValueError
        # 出不去；非有限值只能用裸 JSON 文本送（服务端 json.loads 认 NaN/
        # Infinity 令牌，pydantic python 模式对 float|None 照单全收）。
        body = json.dumps({"verdict": "goal", "offset": bad})
        r = client.post("/api/feedback/0:12.1", content=body,
                        headers={"Content-Type": "application/json"})
        assert r.status_code == 200, f"offset={bad!r} 不该 5xx"
        assert "00:12.1" in r.json()["msg"], f"offset={bad!r} 的 msg 应显示原时刻"
        assert "00:00.0" not in r.json()["msg"], f"offset={bad!r} 的 msg 不许藏 00:00.0"
