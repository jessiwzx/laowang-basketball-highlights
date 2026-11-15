"""行写操作：勾选/标签落盘、删除非破坏可恢复、漏检补记、标签库。"""
import json

import pytest
from fastapi.testclient import TestClient

from bbh.models import Calibration, GoalEvent, save_events
from bbh.web import state
from bbh.web.app import create_app

from test_web_session import NAME, _make_result      # 复用同一份假结果

client = TestClient(create_app())


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _make_result(tmp_path, n=3)
    state.SESSION.video_path = ""
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    yield tmp_path
    state.SESSION.video_path = ""


def _read(tmp_path, name):
    return json.loads((tmp_path / "work" / NAME / name).read_text("utf-8"))


def _make_times(tmp_path, times):
    """指定检测时刻的结果（键 = _fmt_t(t)），用于钉进位边界这类键。"""
    work = tmp_path / "work" / NAME
    work.mkdir(parents=True, exist_ok=True)
    evs = [GoalEvent(t=t, confidence=0.90, status="high") for t in times]
    save_events(str(work / "events.json"), video=str(tmp_path / f"{NAME}.mp4"),
                duration=480.0, events=evs, warning=None,
                calib_key=[1, 2, 3], logic_ver=1)
    Calibration(hoop_rect=(300, 140, 80, 40), hsv_low=(0, 80, 120),
                hsv_high=(25, 255, 255),
                frame_size=(1920, 1080)).save(str(work / "calibration.json"))


def test_selection_patch_writes_selection_json(env):
    r = client.patch("/api/events/0:12.1", json={"selection": True})
    assert r.status_code == 200
    assert _read(env, "selection.json") == ["00:12.1"]
    row = [x for x in r.json()["rows"] if x["key"] == "00:12.1"][0]
    assert row["selection"] is True
    # 再取消
    client.patch("/api/events/0:12.1", json={"selection": False})
    assert _read(env, "selection.json") == []


def test_unknown_key_is_404(env):
    assert client.patch("/api/events/9:99.9",
                        json={"selection": True}).status_code == 404


def test_tag_patch_writes_assignments_and_autochecks(env):
    r = client.patch("/api/events/0:12.1", json={"tag": "老杨"})
    assert r.status_code == 200
    assert _read(env, "tag_assignments.json") == {"00:12.1": "老杨"}
    row = [x for x in r.json()["rows"] if x["key"] == "00:12.1"][0]
    assert row["tag"] == "老杨"
    assert row["selection"] is True          # 打标签 = 认定进球，自动勾选
    assert _read(env, "selection.json") == ["00:12.1"]


def test_blank_tag_clears_it_without_unchecking(env):
    client.patch("/api/events/0:12.1", json={"tag": "老杨"})
    r = client.patch("/api/events/0:12.1", json={"tag": ""})
    row = [x for x in r.json()["rows"] if x["key"] == "00:12.1"][0]
    assert row["tag"] == ""
    assert row["selection"] is True          # 去标 ≠ 不进集锦
    assert _read(env, "tag_assignments.json") == {}   # 盘上也要真删掉这个键


def test_stale_key_with_second_over_59_is_404_not_a_rewrite(tmp_path,
                                                            monkeypatch):
    """秒 ≥ 60 的写法**不是**同一时刻的另一种写法——它是 _fmt_t 的进位边界。

    `0:60` 会被读成 60 秒，归一后落到 `01:00.0`（t=60.0 那条物理上不同的
    球）；`1:70.0` → `02:10.0` 同理。重跑检测后旧面板残留的键（如 t=59.96
    的规范键 `00:60.0`）本该 404（与 delete 路径「容忍陈旧键 = 404」同一
    语义），若被归一就会**改中另一条球**——把不该进集锦的球勾上/打错标签。
    必须 404 且一个字节都不落盘。"""
    monkeypatch.chdir(tmp_path)
    _make_times(tmp_path, [12.1, 60.0, 130.0])
    state.SESSION.video_path = ""
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    try:
        for bad in ("0:60", "1:70.0", "12", "0:inf", "abc", "０:12.1", "  "):
            assert client.patch(f"/api/events/{bad}",
                                json={"selection": True}).status_code == 404
        assert not (tmp_path / "work" / NAME / "selection.json").exists()
        # 窄门只管「不在 keys 里的输入」：进位键本身是规范键，走精确命中
        r = client.patch("/api/events/01:00.0", json={"selection": True})
        assert r.status_code == 200
        assert _read(tmp_path, "selection.json") == ["01:00.0"]
    finally:
        state.SESSION.video_path = ""


def test_delete_removes_row_but_keeps_it_restorable(env):
    r = client.request("DELETE", "/api/events", json={"keys": ["00:12.1"]})
    assert r.status_code == 200
    assert [x["key"] for x in r.json()["rows"]] == ["01:12.1", "02:12.1"]
    assert _read(env, "deleted.json") == ["00:12.1"]
    r2 = client.post("/api/events/restore")
    assert r2.status_code == 200
    assert len(r2.json()["rows"]) == 3
    assert _read(env, "deleted.json") == []


def test_restore_only_the_given_keys(env):
    """Ctrl+Z 撤销一次删除 = 只恢复那一条；全量恢复会连带复活用户早先
    删掉的球（且它们的勾选/标签不会回滚）。"""
    client.request("DELETE", "/api/events",
                   json={"keys": ["00:12.1", "01:12.1"]})
    r = client.post("/api/events/restore", json={"keys": ["00:12.1"]})
    assert r.status_code == 200
    assert [x["key"] for x in r.json()["rows"]] == ["00:12.1", "02:12.1"]
    assert _read(env, "deleted.json") == ["01:12.1"]
    assert "已恢复 1 条" in r.json()["msg"]


def test_restore_without_keys_still_restores_everything(env):
    """不传 keys = 原全量语义（「⋯ ↩ 恢复已删」按钮走这条）。"""
    client.request("DELETE", "/api/events",
                   json={"keys": ["00:12.1", "01:12.1"]})
    r = client.post("/api/events/restore", json={})
    assert len(r.json()["rows"]) == 3
    assert _read(env, "deleted.json") == []


def test_restore_a_key_that_is_not_deleted_is_400(env):
    client.request("DELETE", "/api/events", json={"keys": ["01:12.1"]})
    r = client.post("/api/events/restore", json={"keys": ["00:12.1"]})
    assert r.status_code == 400
    assert "不在已删清单里" in r.json()["error"]
    assert _read(env, "deleted.json") == ["01:12.1"]      # 未动盘


def test_delete_stale_key_does_not_break_the_batch(env):
    """批量宁丢失配键不中断整批（重跑检测/换视频后旧面板残留的键）——
    混进一个不存在的键，其余照删，不报错。"""
    r = client.request("DELETE", "/api/events",
                       json={"keys": ["99:99.9", "00:12.1"]})
    assert r.status_code == 200
    assert _read(env, "deleted.json") == ["00:12.1"]


def test_delete_with_no_keys_is_400(env):
    r = client.request("DELETE", "/api/events", json={"keys": []})
    assert r.status_code == 400
    assert "勾选" in r.json()["error"]


def test_events_alias_matches_state(env):
    assert client.get("/api/events").json()["rows"] == \
        client.get("/api/state").json()["rows"]


def test_miss_records_and_summarises(env):
    r = client.post("/api/events/miss", json={"text": "756"})
    assert r.status_code == 200
    fb = _read(env, "feedback.json")
    assert fb["misses"][0]["t"] == 756.0
    assert r.json()["msg"].startswith("✓ 已记录漏检")


def test_miss_with_garbage_time_is_400(env):
    r = client.post("/api/events/miss", json={"text": "abc"})
    assert r.status_code == 400
    assert "看不懂时刻" in r.json()["error"]


def test_new_tag_enters_library(env):
    r = client.post("/api/tags", json={"name": "新来的"})
    assert r.status_code == 200
    assert "新来的" in r.json()["tags"]
    # 写侧契约：写完回「权威行 + 页脚汇总」（state_payload），与其余 12 个写
    # 端点同形。只回 {"tags": …} 也能让上面两条过——这条钉住形状，
    # 退回窄回包就红。
    assert {"rows", "summaryMd", "goalStatsMd", "calib"} <= set(r.json())
    assert client.post("/api/tags", json={"name": "新来的"}).json()["tags"].count(
        "新来的") == 1          # 重名不重复加


def test_blank_tag_name_is_400(env):
    assert client.post("/api/tags", json={"name": "  "}).status_code == 400


def test_delete_msg_shows_calibrated_times_but_stores_raw_keys(env):
    """删除 toast 的时刻吃显示偏移（用户 2026-09-19 裁定的「三处文案时间基」其一）：
    msg 是用户视角（app.py 的既有契约），deleted.json 仍是原始键——两层时间各司其职。"""
    r = client.request("DELETE", "/api/events",
                       json={"keys": ["00:12.1"], "offset": 1.5})
    assert r.status_code == 200
    assert "00:13.6" in r.json()["msg"]            # 12.1 + 1.5（显示值）
    assert "00:12.1" not in r.json()["msg"]        # 原始键不进文案
    assert _read(env, "deleted.json") == ["00:12.1"]   # 落盘仍原始键


def test_delete_nonfinite_offset_is_treated_as_absent(env):
    """E1 G2g 同款：DELETE /events 的 offset 收口（旧行为 ±inf → 500，无半写状态）。"""
    # 裸 JSON 文本送非有限值（httpx 的 json= 是 allow_nan=False，见 marks 同款）
    body = json.dumps({"keys": ["00:12.1"], "offset": float("inf")})
    r = client.request("DELETE", "/api/events", content=body,
                       headers={"Content-Type": "application/json"})
    assert r.status_code == 200, "±inf 不该 500（当没传）"
    assert _read(env, "deleted.json") == ["00:12.1"], "键按原始时刻落 deleted.json"
    assert "00:00.0" not in r.json()["msg"]
    body2 = json.dumps({"keys": ["01:12.1"], "offset": float("nan")})
    r2 = client.request("DELETE", "/api/events", content=body2,
                        headers={"Content-Type": "application/json"})
    assert r2.status_code == 200
    assert _read(env, "deleted.json") == ["00:12.1", "01:12.1"], "NaN 同样当没传"
