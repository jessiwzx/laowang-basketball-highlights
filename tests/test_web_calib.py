"""标定向导：抽帧出 PNG、点选累积、生成标定落盘、微调、多段管理。"""
import threading

import pytest
from fastapi.testclient import TestClient

from bbh.web import state
from bbh.web.app import create_app

client = TestClient(create_app())


@pytest.fixture()
def env(sample_video, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "work").mkdir(exist_ok=True)
    state.SESSION.video_path = sample_video
    client.post("/api/session", json={"videoPath": sample_video})
    yield tmp_path
    state.SESSION.video_path = ""
    state.SESSION.frame_rgb = None
    state.SESSION.points = []


def test_state_reports_nothing_calibrated_yet(env):
    j = client.get("/api/calib/state").json()
    assert j["ready"] is False and j["segments"] == []
    assert j["points"] == [] and j["canBuild"] is False
    assert "筐口左端" in j["hint"]          # click_hint 的原文，别改写


def test_frame_endpoint_returns_png(env):
    r = client.get("/api/calib/frame.png", params={"t": 1.0})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    # 内存图同 URL 内容会变（换视频/重算）→ 必须禁缓存，否则显示上一支视频的帧
    assert r.headers["cache-control"] == "no-store"
    assert state.SESSION.frame_rgb is not None
    assert state.SESSION.frame_rgb.ndim == 3


def test_concurrent_frame_requests_do_not_cross_frames(env, monkeypatch):
    """两条并发抽帧（不同 t）各自回包**自己那个 t 的图**。

    `frame.png` 在第 88 行写 `s.frame_png`、第 97 行又**读回同一个属性**回包：
    两个并发请求在这两行之间交错时，先到者的回包内容变成后到者的帧。

    这里把交错变成**确定的**：先到者写完 `frame_png` 就停在紧随其后的
    `s.points = []` 上（:89，仍在 88↔97 之间），等后到者也写完 `frame_png`
    再放行——释放后先到者回包读到的必然是后到者的帧。回包改读局部量后，
    同样的交错下两条都回自己的帧。
    """
    import numpy as np
    import bbh.app as A
    from bbh.web import api_calib, state

    colors = {1.0: (255, 0, 0), 3.0: (0, 0, 255)}

    def fake_extract(vp, t):
        rgb = np.zeros((4, 4, 3), np.uint8)
        rgb[:, :] = colors[float(t)]
        return rgb, [], "", None, ""

    monkeypatch.setattr(A, "extract_frame_at", fake_extract)

    both_assigned = threading.Event()
    counter = {"n": 0}
    guard = threading.Lock()
    real_setattr = state.Session.__setattr__

    def hooked(self, name, value):
        real_setattr(self, name, value)
        if name != "points":        # :89 是第一处「写完 frame_png 之后」的赋值
            return
        with guard:
            counter["n"] += 1
            mine = counter["n"]
        if mine == 1:
            both_assigned.wait(1.0)     # 停到后到者也写完 frame_png
        else:
            both_assigned.set()

    monkeypatch.setattr(state.Session, "__setattr__", hooked)
    got = {}

    def go(t):
        got[t] = client.get("/api/calib/frame.png", params={"t": t})

    ts = (1.0, 3.0)
    threads = [threading.Thread(target=go, args=(t,)) for t in ts]
    try:
        for th in threads:
            th.start()
        for th in threads:
            th.join(20)
        assert set(got) == set(ts)                 # 两条都真跑完了
        pngs = {}
        for t, rgb in colors.items():
            r = got[t]
            assert r.status_code == 200, r.text
            assert r.headers["content-type"] == "image/png"
            pngs[t] = r.content
        for t, rgb in colors.items():
            want = api_calib._encode(np.full((4, 4, 3), rgb, np.uint8))
            assert pngs[t] == want
        # 两张图确实不同（否则这条用例对「串帧」没有鉴别力）
        assert pngs[1.0] != pngs[3.0]
    finally:
        both_assigned.set()
        state.SESSION.points = []


def test_frame_failure_surfaces_as_400(env, monkeypatch):
    """抽帧失败（编码解不了/超时长）→ 抽帧函数抛 gr.Error → 400 + 原文。
    这里换成确定性桩，不去赌 ffmpeg 对超时长 t 的行为。"""
    from bbh import _gr_compat as gr
    import bbh.app as A
    monkeypatch.setattr(A, "extract_frame_at",
                        lambda vp, t: (_ for _ in ()).throw(
                            gr.Error("在 999999 秒处取帧失败。诊断：…")))
    r = client.get("/api/calib/frame.png", params={"t": 999999})
    assert r.status_code == 400
    assert "取帧失败" in r.json()["error"]


def test_points_accumulate_and_hint_advances(env):
    client.get("/api/calib/frame.png", params={"t": 1.0})
    a = client.post("/api/calib/point", json={"x": 10, "y": 20}).json()
    assert a["points"] == [[10.0, 20.0]] and a["canBuild"] is False
    assert "筐口右端" in a["hint"]
    b = client.post("/api/calib/point", json={"x": 30, "y": 20}).json()
    assert "球" in b["hint"] and b["canBuild"] is False
    c = client.post("/api/calib/point", json={"x": 20, "y": 20}).json()
    assert len(c["points"]) == 3 and c["canBuild"] is True


def test_clear_points(env):
    client.post("/api/calib/point", json={"x": 1, "y": 2})
    j = client.request("DELETE", "/api/calib/point").json()
    assert j["points"] == [] and "筐口左端" in j["hint"]


def test_build_writes_calibration_and_returns_preview(env):
    client.get("/api/calib/frame.png", params={"t": 1.0})
    for x, y in ((10, 20), (30, 20), (20, 20)):      # 筐口左/右 + 球
        client.post("/api/calib/point", json={"x": x, "y": y})
    r = client.post("/api/calib/build", json={})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["msg"] and "标定" in j["msg"]
    assert j["previewUrl"].startswith("/api/calib/preview.png?v=")
    assert j["ready"] is True
    work = list((env / "work").iterdir())[0]
    assert (work / "calibration.json").is_file()
    assert (work / "calibration_ref.png").is_file()  # 参考帧随标定存档（REF_IMG_FILE）
    prev = client.get("/api/calib/preview.png")
    assert prev.status_code == 200 and prev.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert prev.headers["cache-control"] == "no-store"   # 同帧：内容随会话变，禁缓存


def test_preview_404_before_build(env):
    r = client.get("/api/calib/preview.png")
    assert r.status_code == 404
    # 钉住「404 来自 preview_png 自己」：只断 404 的话，删掉 api_calib 注册行、
    # 由挂 "/" 的 StaticFiles 兜底回 404 时这条照样绿（兜底 body 也是 404）
    assert "还没有可看的预览" in r.json()["error"]


def test_build_with_too_few_points_is_400(env):
    client.get("/api/calib/frame.png", params={"t": 1.0})
    client.post("/api/calib/point", json={"x": 10, "y": 20})
    r = client.post("/api/calib/build", json={})
    assert r.status_code == 400
    assert "3" in r.json()["error"]


def test_build_without_frame_is_400(env):
    r = client.post("/api/calib/build", json={})
    assert r.status_code == 400
    assert "参考视频" in r.json()["error"] or "抽帧" in r.json()["error"]


def test_segment_requires_t_after_main(env):
    client.get("/api/calib/frame.png", params={"t": 1.0})
    for x, y in ((10, 20), (30, 20), (20, 20)):
        client.post("/api/calib/point", json={"x": x, "y": y})
    r = client.post("/api/calib/build", json={"segT": 300.0})   # 还没主标定
    assert r.status_code == 400
    assert "主标定" in r.json()["error"]


def test_segment_build_and_delete(env):
    client.get("/api/calib/frame.png", params={"t": 1.0})
    for x, y in ((10, 20), (30, 20), (20, 20)):
        client.post("/api/calib/point", json={"x": x, "y": y})
    client.post("/api/calib/build", json={})                    # 主标定
    seg = client.post("/api/calib/build", json={"segT": 300.0}).json()
    assert "新段已存" in seg["msg"]
    # 冻结的 _seg_list_md 文案是「- 段 2：5:00 起」（段与序号间带一个空格），
    # brief 原文的 "段2" 直通匹配不上——按裁定改字面量，实现不动
    assert len(seg["segments"]) == 1 and "段 2" in seg["segListMd"]
    d = client.request("DELETE", "/api/calib/segments/2").json()
    assert d["segments"] == [] and "已删除" in d["msg"]


def test_delete_main_segment_is_400(env):
    client.get("/api/calib/frame.png", params={"t": 1.0})
    for x, y in ((10, 20), (30, 20), (20, 20)):
        client.post("/api/calib/point", json={"x": x, "y": y})
    client.post("/api/calib/build", json={})
    r = client.request("DELETE", "/api/calib/segments/1")
    assert r.status_code == 400


def test_refine_changes_s_low_and_reports_hits(env):
    client.get("/api/calib/frame.png", params={"t": 1.0})
    # 前置：refine_hsv 首查标定档在不在（旧界面同款守卫「请先点击生成标定，再微调」），
    # 真实用户流恒是 build → refine。无档那条路由 test_refine_before_build_is_400 钉住。
    for x, y in ((10, 20), (30, 20), (20, 20)):
        client.post("/api/calib/point", json={"x": x, "y": y})
    client.post("/api/calib/build", json={})
    before_png = state.SESSION.preview_png           # build 那份预览缓冲
    r = client.post("/api/calib/refine", json={"sLow": 90, "vLow": 100})
    assert r.status_code == 200
    assert "S 下限 90" in r.json()["msg"]
    assert r.json()["previewUrl"].startswith("/api/calib/preview.png?v=")
    # refine 真的换掉了会话里的预览缓冲（变异「refine 不写 s.preview_png」在此变红——
    # 只有 URL 前缀断言时它 15 条全绿）。
    # 不能改断言「两字节不同」：本夹具三点落在帧左上角、ROI 内 0 命中，build 与 refine
    # 走的是同两条 0 命中分支，叠加图只画绿像素+黄/蓝/红三个框（几何同源），两张图
    # 逐像素相同——实测 md5 均 485ee22f…、均 4576 字节。要字节不同就得把点挪到球上，
    # 那依赖合成视频的像素位置，脆弱，故钉「缓冲被换新」这个可观察量。
    assert state.SESSION.preview_png is not before_png
    # refine 若误写 `s.preview_png = _encode(s.frame_rgb)`（拿原始帧冒充叠加
    # 图）上面两条全绿照过——预览缓冲换了新、也确实是「这一帧」的 PNG。
    # 本用例已抽过帧（frame_rgb 非 None），所以叠加图与原始帧必然不同字节。
    # 先断 frame_png 非 None：两个都是 None 时 `!=` 恒假（那才是真红），
    # 缺了这一行则「没抽到帧」这种退化会伪装成绿。
    assert state.SESSION.frame_png is not None
    assert state.SESSION.preview_png != state.SESSION.frame_png
    assert client.get("/api/calib/preview.png").content == state.SESSION.preview_png


def test_refine_before_build_is_400(env):
    client.get("/api/calib/frame.png", params={"t": 1.0})
    r = client.post("/api/calib/refine", json={"sLow": 90, "vLow": 100})
    assert r.status_code == 400
    assert "生成标定" in r.json()["error"]


def test_existing_reports_saved_calibration(env):
    client.get("/api/calib/frame.png", params={"t": 1.0})
    for x, y in ((10, 20), (30, 20), (20, 20)):
        client.post("/api/calib/point", json={"x": x, "y": y})
    client.post("/api/calib/build", json={})
    j = client.get("/api/calib/existing").json()
    assert j["ready"] is True
    assert j["calibration"]["hsvLow"][1] >= 0
    assert j["files"] and "calibration.json" in j["files"]
