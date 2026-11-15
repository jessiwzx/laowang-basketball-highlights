"""长任务：守卫同步 400、job 进度轮询、同类单飞 409、生成结果。"""
import time
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from bbh.web import state
from bbh.web.app import create_app

from test_web_session import NAME, _make_result

client = TestClient(create_app())


def _wait(job_id, timeout=20.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["done"]:
            return j
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} 超时未完成")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """chdir 到 tmp + 复位会话与模块级检测状态。

    `_state` 是 bbh.app 的模块级全局，跑完不还原就会串到下一条用例：残留的
    `error` / `cancelled` / `done_msg` 会顶掉后续 `_refresh_status()` 的分支
    （实测：本文件跑完后 test_app.py::test_刷新状态取消中文案 被残留的
    "本视频的标定缺失或不可读" 顶成「❌ 出错」而红）。快照法同
    tests/test_app.py 的 app_state；`_cancel_evt` 同理——残留置位会把 running
    分支顶成「正在取消」。
    """
    import bbh.app as A
    monkeypatch.chdir(tmp_path)
    saved = dict(A._state)
    A._cancel_evt.clear()
    state.SESSION.video_path = ""
    yield tmp_path
    state.SESSION.video_path = ""
    A._state.clear()
    A._state.update(saved)


def test_detect_without_calibration_is_400_before_any_job_starts(env):
    r = client.post("/api/session", json={"videoPath": "D:/videos/nocal.mp4"})
    assert r.status_code == 200
    r = client.post("/api/detect")
    assert r.status_code == 400
    assert "标定" in r.json()["error"]


def test_detect_without_session_is_400(env):
    assert client.post("/api/detect").status_code == 400


def test_gen_without_selection_is_400(env):
    _make_result(env, n=2)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    r = client.post("/api/gen", json={"vertical": False, "items": []})
    assert r.status_code == 400
    assert "至少勾选一项" in r.json()["error"]


def test_gen_choices_lists_collection_and_tags(env):
    _make_result(env, n=2)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    j = client.get("/api/gen/choices?vertical=true").json()
    assert j["choices"][0].startswith("视频合集")
    assert j["default"] == [j["choices"][0]]
    assert "📱" in j["stats"]          # vertical=true 时是竖屏运镜行


def test_gen_choices_without_vertical_has_neutral_stats(env):
    """不传 vertical（新界面打开弹窗时还**没选**画幅）→ 统计行不能带画幅
    前缀，否则等于变相默认，与「画幅强制必选」自相矛盾（2026-10-21）。"""
    _make_result(env, n=2)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    j = client.get("/api/gen/choices").json()
    assert j["vertical"] is None
    for bad in ("📱", "🎬", "竖屏", "横屏"):
        assert bad not in j["stats"]
    assert "📊" in j["stats"]          # build_goal_stats 自带的前缀仍在


def test_gen_choices_single_output_swaps_collection_label(env):
    """单条模式：合集项文案换成「逐球各出一条」，并回 allLabel 供前端切模式
    时重映射勾选（合集项的展示串就是勾选值本身，两模式不同 ⇒ 硬编码会丢）。"""
    _make_result(env, n=2)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    reel = client.get("/api/gen/choices?output=reel").json()
    single = client.get("/api/gen/choices?output=single").json()
    assert reel["allLabel"] == "视频合集（全部勾选的进球）"
    assert single["allLabel"] == "全部勾选的进球（逐球各出一条）"
    # 单条清单头部是「★ 精选」（2026-10-23），合集项退到第 2 位；一份没打星
    # ⇒ default 必须是**空数组**，绝不能预选合集（否则点一下就是全部球各出
    # 一条，一次发出场次总数个文件）。
    import bbh.app as A
    assert single["choices"][0] == A.GEN_STAR_LABEL
    assert single["choices"][1] == single["allLabel"]
    assert single["starred"] == 0 and single["default"] == []
    assert "★ 已精选 0 球" in single["stats"]
    assert reel["choices"][0] == reel["allLabel"]
    # 两套文案都必须被认成「合集项」，否则勾了合集会被当标签名筛成 0 行
    assert A._is_all_item(reel["allLabel"]) and A._is_all_item(single["allLabel"])


def test_job_endpoint_404_for_unknown_id(env):
    assert client.get("/api/jobs/nope").status_code == 404


def test_scan_requires_detection_results(env, tmp_path, monkeypatch):
    """有标定但没检测结果 → 回扫守卫报「尚无检测结果」→ 400。

    这条同时验证「守卫在起 job 之前同步跑」：拿到 400 而不是 jobId。
    """
    from bbh.models import Calibration
    work = tmp_path / "work" / "raw"
    work.mkdir(parents=True)
    Calibration(hoop_rect=(1, 2, 3, 4), hsv_low=(0, 80, 120),
                hsv_high=(25, 255, 255), frame_size=(1920, 1080)
                ).save(str(work / "calibration.json"))
    client.post("/api/session", json={"videoPath": "D:/videos/raw.mp4"})
    r = client.post("/api/wall/scan")
    assert r.status_code == 400
    assert "尚无检测结果" in r.json()["error"]


def test_scan_returns_job_id_then_finishes(env, monkeypatch):
    """用假生成器替掉真回扫：验证 job 接线（进度转发 + 末次结果），
    不跑真视频（真回扫要几分钟）。"""
    import bbh.app as A
    _make_result(env, n=2)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})

    def fake_trace(video_path):
        yield "⏳ 正在回扫 1/2…", None
        yield "⏳ 正在回扫 2/2…", None
        yield "✅ 回扫完成：2 球", None

    monkeypatch.setattr(A, "run_shooter_tracing", fake_trace)
    r = client.post("/api/wall/scan")
    assert r.status_code == 200, r.text
    j = _wait(r.json()["jobId"])
    assert j["state"] == "done" and j["pct"] == 100
    # found 恒 None：三类任务都没人 report(found=…)——_state 里根本没有球数
    # 字段，_poll_detect 也不上报。前端要球数读 /api/state 的 rows。
    assert j["found"] is None
    res = client.get(f"/api/jobs/{j['id']}/result").json()
    assert res["msg"] == "✅ 回扫完成：2 球"      # /jobs/{id} 只报进度，结果在 /result
    assert res["kind"] == "wall_scan"
    assert "rows" in res                         # 回扫完行数据一起刷（出手人列变了）


def test_single_flight_conflict_is_409(env, monkeypatch):
    import bbh.app as A
    import threading
    _make_result(env, n=1)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    gate = threading.Event()

    def slow_trace(video_path):
        gate.wait(5)
        yield "✅ 完成", None

    monkeypatch.setattr(A, "run_shooter_tracing", slow_trace)
    first = client.post("/api/wall/scan")
    assert first.status_code == 200
    second = client.post("/api/wall/scan")
    assert second.status_code == 409
    assert "已在跑" in second.json()["error"]
    gate.set()


def test_gen_job_reports_finished_videos(env, monkeypatch):
    """用假生成器替掉真生成：验 job 接线（进度转发 + 末次结果解析）。
    真生成要跑完整视频渲染，几分钟起步。"""
    import bbh.app as A
    _make_result(env, n=2)
    (env / "work" / NAME / "highlight.mp4").write_bytes(b"\x00" * 64)

    def fake_gen(video_path, rows, sel, vertical=True, output="reel",
                 on_progress=None):
        yield (None, None, None, None, "⏳ 正在生成（1/1）：视频合集…", None)
        yield ("work/" + NAME + "/highlight.mp4", None, None, None,
               "✅ 已生成 1 个视频（预览位播放第一个，全部在 work/<视频名>/ 下）：\n"
               "· 视频合集（全部勾选的进球） → highlight.mp4", None)

    monkeypatch.setattr(A, "gen_selected", fake_gen)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    r = client.post("/api/gen", json={"vertical": False,
                                      "items": ["视频合集（全部勾选的进球）"]})
    assert r.status_code == 200, r.text
    j = _wait(r.json()["jobId"])
    assert j["state"] == "done"
    res = client.get(f"/api/jobs/{j['id']}/result").json()
    assert res["vertical"] is False
    assert res["videos"][0]["item"] == "视频合集（全部勾选的进球）"
    assert res["videos"][0]["name"] == "highlight.mp4"
    assert res["videos"][0]["url"].startswith("/media/work?path=")
    assert "&v=" in res["videos"][0]["url"]        # 带 mtime 击穿缓存
    assert res["firstUrl"] == res["videos"][0]["url"]


def test_gen_selected_real_summary_feeds_result_videos(env, monkeypatch):
    """真跑 `gen_selected`（**只**替身 generate_highlight，让它产出真实文件）：
    钉住 `bbh/app.py:1326` 的结果清单格式 ↔ `_gen_result` 逐行解析这条静默耦合。

    本文件其余 gen 用例全用**手写 summary 行**的假生成器——那正是掩盖这条
    耦合的原因：真链路上 outs 与清单的拼装（app.py:1326 的
    `· {item} → {basename}`）一旦改格式（哪怕只把 ` → ` 换成 `：`），
    `_gen_result` 每行都进不了 os.path.exists 分支 ⇒ videos: []、
    firstUrl: null，而 summaryMd 照旧含那些行——界面「生成成功、清单为空」。
    """
    import os

    import bbh.app as A
    _make_result(env, n=2)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    # 真用户流：给一条球打标签（set_row_tag 顺手自动勾选）→ 标签项才有片子
    assert client.patch("/api/events/00:12.1",
                        json={"tag": "老杨"}).status_code == 200
    written = []

    def fake_highlight(video_path, rows, tag_filter="", vertical=True,
                       starred_only=False):
        """替身只负责「产出一个真实文件」，返回形状与真管线一致
        （os.path.join(work_dir, out_name)，带目录的相对路径）。
        命名与 bbh/app.py::generate_highlight 同规则（2026-10-18 起带画幅；
        2026-10-24 起 starred_only 产出 -精选 后缀）。
        签名必须跟真函数对齐——`starred_only` 漏了这个形参，gen_selected 传参
        就 TypeError，而它只 `except gr.Error` ⇒ 整个生成器被杀死、`written`
        空列表（这次改签名时真踩了一脚）。"""
        aspect = "竖" if vertical else "横"
        if starred_only:
            name = f"highlight-{aspect}-精选.mp4"
        else:
            name = (f"highlight-{aspect}.mp4" if not tag_filter
                    else f"highlight-{aspect}-{tag_filter}.mp4")
        p = os.path.join(A._work_dir_for(video_path), name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(b"\x00" * 64)
        written.append(name)
        return p

    monkeypatch.setattr(A, "generate_highlight", fake_highlight)
    r = client.post("/api/gen", json={"vertical": False,
                                      "items": [A.GEN_ALL_LABEL, "老杨"]})
    assert r.status_code == 200, r.text
    res = client.get(f"/api/jobs/{_wait(r.json()['jobId'])['id']}/result").json()
    # 1) 真拼装出来的清单被 _gen_result 解析成成品（变异：1326 的分隔符改
    #    `：` → 这里 videos == [] 立刻红）
    assert written == ["highlight-横.mp4", "highlight-横-老杨.mp4"]
    assert [v["name"] for v in res["videos"]] == written
    assert [v["item"] for v in res["videos"]] == [A.GEN_ALL_LABEL, "老杨"]
    # 2) url/首播位也走真存在性判据（_media_url 对不存在的路径回 None）；
    #    url 里的路径是百分号编码的（标签名带中文），按解码后比对
    for v in res["videos"]:
        assert v["url"].startswith("/media/work?path=") and "&v=" in v["url"]
        assert v["name"] in urllib.parse.unquote(v["url"])
    assert res["firstUrl"] == res["videos"][0]["url"]
    assert f"· {A.GEN_ALL_LABEL} → highlight-横.mp4" in res["summaryMd"]


def test_gen_single_real_chain_one_file_per_ball(env, monkeypatch):
    """真跑 `gen_selected(output="single")`（只替身竖屏渲染，产出真实文件）：
    钉住三件事——①每球一个文件；②跨勾选项按时刻去重（合集+标签不得重复
    渲染）；③清单行格式仍能被 `_gen_result` 反解成成品（单条模式没另起一套
    摘要格式，复用集锦那一条隐式契约）。"""
    import os

    import bbh.app as A
    _make_result(env, n=2)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    # 打标签顺手自动勾选 → 两行都进勾选集
    assert client.patch("/api/events/00:12.1",
                        json={"tag": "老杨"}).status_code == 200
    assert client.patch("/api/events/01:12.1",
                        json={"tag": "小马"}).status_code == 200
    written = []

    def fake_vertical(video_path, goals, work_dir, out_name, cfg=None):
        assert len(goals) == 1, "单条模式每次只能传一个球"
        p = os.path.join(work_dir, out_name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(b"\x00" * 64)
        written.append(out_name)
        return p

    monkeypatch.setattr(A.vrender, "build_vertical", fake_vertical)
    r = client.post("/api/gen", json={"vertical": True, "output": "single",
                                      "items": [A.GEN_ALL_LABEL_SINGLE, "老杨"]})
    assert r.status_code == 200, r.text
    res = client.get(f"/api/jobs/{_wait(r.json()['jobId'])['id']}/result").json()

    assert res["output"] == "single"
    assert written == ["single-竖-老杨-00m12s1.mp4",
                       "single-竖-小马-01m12s1.mp4"], written     # 去重：不是 3 个
    assert [v["name"] for v in res["videos"]] == written
    assert [v["item"] for v in res["videos"]] == ["老杨 00:12.1", "小马 01:12.1"]
    for v in res["videos"]:
        assert v["url"].startswith("/media/work?path=") and "&v=" in v["url"]
    assert res["firstUrl"] == res["videos"][0]["url"]
    assert "已生成 2 个单条视频" in res["summaryMd"]


def test_gen_hook4_batches_every_three_balls(env, monkeypatch):
    """`output="hook4"`（25s 精选）：勾 4 球 ⇒ **2 条**（3 球 + 1 球），不是 4 条。

    同时钉住三件容易悄悄坏掉的事：
    ① 球池按时刻排序后切块——先按勾选项迭代再合并的话，四个球会全塞进同一
       条，另一条根本不会出现；
    ② 穿网锚点拿不到时退回 `event_t − 0.17`（不是 0，也不是不标注）；
    ③ **横竖分派**（2026-11-01 起）：`vertical=true` 走
       `build_hook_reel_vertical`（vrender 跟球运镜），文件名 `hook4-竖-*`；
       横版仍走 `build_hook_reel`，文件名 `hook4-横-*`。两条路的文件名必须
       区分开，否则同场先出横版再出竖版会互相覆盖。
    """
    import os

    import bbh.app as A
    _make_result(env, n=4)
    # 用**真实存在**的路径：hook4 开工先查源文件在不在（早于 ffmpeg 报错，
    # 文案才看得懂），而其余用例那种不存在的 D:/videos/... 会在这里被拦。
    src = env / f"{NAME}.mp4"
    src.write_bytes(b"")
    client.post("/api/session", json={"videoPath": str(src)})
    for k in ("00:12.1", "01:12.1", "02:12.1", "03:12.1"):
        assert client.patch(f"/api/events/{k}",
                            json={"tag": "老杨"}).status_code == 200

    calls = []

    def fake_build(src, hits, work_dir, out_name, **kw):
        p = os.path.join(work_dir, out_name)
        with open(p, "wb") as f:
            f.write(b"\x00" * 64)
        calls.append((out_name, list(hits)))
        return p

    monkeypatch.setattr(A.repack, "build_hook_reel", fake_build)
    # 竖版会真的调 vrender 逐帧渲染（要真视频），单测里同样打桩
    monkeypatch.setattr(A.repack, "build_hook_reel_vertical", fake_build)
    # 扫描一律失败 → 全部走兜底偏移（真实的扫筐口要真视频，单测里跑不了）
    monkeypatch.setattr(A.repack, "find_hit_time",
                        lambda *a, **k: (None, {"why": "测试"}))

    r = client.post("/api/gen", json={"vertical": True, "output": "hook4",
                                      "items": [A.GEN_ALL_LABEL]})
    assert r.status_code == 200, r.text
    res = client.get(f"/api/jobs/{_wait(r.json()['jobId'])['id']}/result").json()

    assert res["output"] == "hook4"
    assert res["vertical"] is True, "hook4 竖版：vertical 不该再被改写成横屏"
    assert [c[0] for c in calls] == ["hook4-竖-01.mp4", "hook4-竖-02.mp4"]
    # 12.1/72.1/132.1 一条，192.1 单独一条；锚点 = t - 0.17（兜底）
    assert calls[0][1] == pytest.approx([11.93, 71.93, 131.93])
    assert calls[1][1] == pytest.approx([191.93])
    assert [v["name"] for v in res["videos"]] == ["hook4-竖-01.mp4", "hook4-竖-02.mp4"]
    assert res["videos"][0]["item"].startswith("25s精选 01｜")
    # 偏移标注按条给：第 1 条 3 球全兜底、第 2 条 1 球兜底
    assert "（3 球用默认偏移）" in res["summaryMd"]
    assert "（1 球用默认偏移）" in res["summaryMd"]
    assert "已生成 2 个视频" in res["summaryMd"]


def test_gen_hook4_scan_hit_wins_over_fallback(env, monkeypatch):
    """扫到穿网锚点时**优先用它**、item 里也不该出现「默认偏移」标注
    （那条标注是给兜底的：全都标等于没标）。"""
    import os

    import bbh.app as A
    _make_result(env, n=3)
    src = env / f"{NAME}.mp4"
    src.write_bytes(b"")
    client.post("/api/session", json={"videoPath": str(src)})
    for k in ("00:12.1", "01:12.1", "02:12.1"):
        client.patch(f"/api/events/{k}", json={"tag": "小马"})

    seen = []

    def fake_build(src, hits, work_dir, out_name, **kw):
        p = os.path.join(work_dir, out_name)
        with open(p, "wb") as f:
            f.write(b"\x00" * 64)
        seen.append(list(hits))
        return p

    monkeypatch.setattr(A.repack, "build_hook_reel", fake_build)
    # 每个球「扫到」的锚点都比 event_t 早 0.4s，与兜底的 0.17 明显不同
    monkeypatch.setattr(A.repack, "find_hit_time",
                        lambda src, rect, lo, hi, t, **k: (t - 0.4, {"by": "scan"}))

    r = client.post("/api/gen", json={"vertical": False, "output": "hook4",
                                      "items": [A.GEN_ALL_LABEL]})
    assert r.status_code == 200, r.text
    res = client.get(f"/api/jobs/{_wait(r.json()['jobId'])['id']}/result").json()

    assert seen == [pytest.approx([11.7, 71.7, 131.7])], seen
    assert "默认偏移" not in res["summaryMd"]
    assert len(res["videos"]) == 1


def test_gen_choices_hook4_shares_reel_labels(env):
    """hook4 与 reel 共用同一套合集/精选文案（两者都是"合成一条"的语义）。

    **不允许给 hook4 新增第三串**：`_ALL_ITEMS` / `_STAR_ITEMS` 少登记一个就
    会被当成标签名去筛 ⇒ 0 行 ⇒ 静默不出片，而且不报错。
    """
    _make_result(env, n=2)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    reel = client.get("/api/gen/choices?output=reel").json()
    hook = client.get("/api/gen/choices?output=hook4&vertical=false").json()
    assert hook["output"] == "hook4"          # 原样回，前端才感知得到模式变了
    assert hook["allLabel"] == reel["allLabel"]
    assert hook["starLabel"] == reel["starLabel"]
    assert hook["choices"] == reel["choices"]


def test_gen_rejects_unknown_output_before_starting_job(env):
    """output 用 Literal 校验：拼错模式名在**起 job 之前**就被拦掉。"""
    _make_result(env, n=2)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    r = client.post("/api/gen", json={"output": "singel",
                                      "items": ["视频合集（全部勾选的进球）"]})
    # 本项目的模型校验错误由统一处理器转成 400（不是 FastAPI 默认 422）
    assert r.status_code in (400, 422), r.text
    assert "output" in r.json()["error"]


def test_gen_result_keeps_skipped_items_visible(env, monkeypatch):
    """全部跳过时 [0] 是 gr.update 而非路径、[4] 是 ❌ 文案——不假装成功，
    也不让前端去点一个不存在的成品。"""
    import bbh.app as A
    _make_result(env, n=2)

    def fake_gen(video_path, rows, sel, vertical=True, output="reel",
                 on_progress=None):
        yield (None, None, None, None,
               "❌ 没有生成任何视频——视频合集：没有勾选的进球（跳过）", None)

    monkeypatch.setattr(A, "gen_selected", fake_gen)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    r = client.post("/api/gen", json={"vertical": True, "items": ["视频合集"]})
    assert r.status_code == 200, r.text
    res = client.get(f"/api/jobs/{_wait(r.json()['jobId'])['id']}/result").json()
    assert res["videos"] == [] and res["firstUrl"] is None
    assert "没有生成任何视频" in res["summaryMd"]


def test_detect_job_polls_module_state(env, monkeypatch):
    """假 start_detection：只改模块级 _state 标志，验 job 接线（进度转发 +
    末次文案取自 done_msg + 结果里带行数据）。真检测要跑几分钟。"""
    import bbh.app as A
    _make_result(env, n=1)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})

    def fake_start(vp):
        A._state.update(running=False, done=True, done_msg="✅ 检测完成：2 球",
                        error=None, cancelled=False)
        return "检测中…"

    monkeypatch.setattr(A, "start_detection", fake_start)
    r = client.post("/api/detect")
    assert r.status_code == 200, r.text
    j = _wait(r.json()["jobId"])
    assert j["state"] == "done"
    res = client.get(f"/api/jobs/{j['id']}/result").json()
    assert res["msg"] == "✅ 检测完成：2 球"
    assert res["kind"] == "detect"
    assert "rows" in res


def test_detect_job_error_surfaces_via_result_endpoint(env, monkeypatch):
    """检测线程报错 → job 变 error → /result 400 且文案透传（不是 200 假成功）。"""
    import bbh.app as A
    _make_result(env, n=1)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})

    def fake_start(vp):
        A._state.update(running=False, done=False, error="本视频的标定缺失或不可读",
                        cancelled=False)
        return "检测中…"

    monkeypatch.setattr(A, "start_detection", fake_start)
    r = client.post("/api/detect")
    assert r.status_code == 200, r.text
    jid = r.json()["jobId"]
    _wait(jid)
    res = client.get(f"/api/jobs/{jid}/result")
    assert res.status_code == 400
    assert "标定缺失" in res.json()["error"]


def test_detect_job_cancelled_surfaces_as_done_with_flag(env, monkeypatch):
    """用户取消不是失败：job 落 done（不是 error），结果里带 cancelled 旗标
    与「已取消」文案——取消与出错的语义分家是这套 job 设计最要紧的一条。"""
    import bbh.app as A
    _make_result(env, n=1)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})

    def fake_start(vp):
        A._state.update(running=False, done=False, error=None, cancelled=True)
        return "检测中…"

    monkeypatch.setattr(A, "start_detection", fake_start)
    r = client.post("/api/detect")
    assert r.status_code == 200, r.text
    jid = r.json()["jobId"]
    assert _wait(jid)["state"] == "done"          # 取消不是 error
    res = client.get(f"/api/jobs/{jid}/result").json()
    assert res["cancelled"] is True and "已取消" in res["msg"]
    assert "rows" in res


def test_gen_rejects_bare_string_items_without_starting_job(env):
    """items 传字符串必须在起 job 之前 400（裸 dict 下会逐字符迭代成 6 个
    垃圾项，还真起一个 gen job 去跑）。"""
    from bbh.web.jobs import REGISTRY
    _make_result(env, n=2)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    r = client.post("/api/gen", json={"items": "视频合集"})
    assert r.status_code == 400
    assert not REGISTRY.running_kind("gen")      # 没起 job（用 falsy 判，别写 `is False`）


def test_gen_job_reports_generator_progress_message_midway(env, monkeypatch):
    """卡住生成器，在卡住期间读 job：中途文案必须取自 yield 的 [4]——
    这一位取错（比如 0），用户在整个几分钟等待里看到的就是一条磁盘路径。"""
    import bbh.app as A
    import threading
    _make_result(env, n=1)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    gate = threading.Event()

    def gated_gen(video_path, rows, sel, vertical=True, output="reel",
                  on_progress=None):
        yield (None, None, None, None, "⏳ 正在生成（1/1）：视频合集…", None)
        gate.wait(10)
        yield (None, None, None, None, "✅ 已生成 0 个视频", None)

    monkeypatch.setattr(A, "gen_selected", gated_gen)
    r = client.post("/api/gen", json={"vertical": False, "items": ["视频合集"]})
    assert r.status_code == 200, r.text
    jid = r.json()["jobId"]
    try:
        seen = None
        for _ in range(50):
            j = client.get(f"/api/jobs/{jid}").json()
            if j.get("msg") and "正在生成" in str(j.get("msg")):
                seen = j["msg"]
                break
            time.sleep(0.1)
        assert seen == "⏳ 正在生成（1/1）：视频合集…"     # [4] 不是别的位
    finally:
        gate.set()
        _wait(jid)          # 放行并等收尾：别把 gen 槽位留给下一条用例


def test_scan_job_reports_generator_progress_message_midway(env, monkeypatch):
    """同上一条的 wall_scan 版：中途文案取 yield 的 [0]（回扫的 0↔1 取错就
    中途进度全丢）。"""
    import bbh.app as A
    import threading
    _make_result(env, n=1)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    gate = threading.Event()

    def gated_trace(video_path):
        yield "⏳ 正在回扫 1/2…", None
        gate.wait(10)
        yield "✅ 回扫完成", None

    monkeypatch.setattr(A, "run_shooter_tracing", gated_trace)
    r = client.post("/api/wall/scan")
    assert r.status_code == 200, r.text
    jid = r.json()["jobId"]
    try:
        seen = None
        for _ in range(50):
            j = client.get(f"/api/jobs/{jid}").json()
            if j.get("msg") and "正在回扫" in str(j.get("msg")):
                seen = j["msg"]
                break
            time.sleep(0.1)
        assert seen == "⏳ 正在回扫 1/2…"                # [0] 不是别的位
    finally:
        gate.set()
        _wait(jid)


def test_job_result_is_409_while_still_running(env, monkeypatch):
    """没跑完就来取结果 → 409「还在跑」，而不是拿一份半截 payload 当成品。"""
    import bbh.app as A
    import threading
    _make_result(env, n=1)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    gate = threading.Event()

    def gated_trace(video_path):
        gate.wait(10)
        yield "✅ 完成", None

    monkeypatch.setattr(A, "run_shooter_tracing", gated_trace)
    r = client.post("/api/wall/scan")
    assert r.status_code == 200, r.text
    jid = r.json()["jobId"]
    try:
        got = client.get(f"/api/jobs/{jid}/result")
        assert got.status_code == 409
        assert "还在跑" in got.json()["error"]
    finally:
        gate.set()
        _wait(jid)


def test_gen_result_disambiguates_arrow_inside_tag_name(env, monkeypatch):
    """标签名自身含 ` → `（POST /api/tags 零校验、safe_filename 不换箭头）时，
    一行结果清单里就有两个分隔符——按「右半边在 work 下真实存在」从右往左
    消歧；右半边都不是文件的行（跳过 / 提示）整行丢弃，不产生幻觉成品。"""
    import bbh.app as A
    _make_result(env, n=2)
    # 刻意用不带箭头的文件名：歧义出在标签名上，不必再造 Unicode 文件名
    (env / "work" / NAME / "highlight-AB.mp4").write_bytes(b"\x00" * 64)

    def fake_gen(video_path, rows, sel, vertical=True, output="reel",
                 on_progress=None):
        yield (None, None, None, None,
               "✅ 已生成 1 个视频（预览位播放第一个，全部在 work/<视频名>/ 下）：\n"
               "· A → B → highlight-AB.mp4\n"
               "· C → D：'勾选的进球里没有标签为「C → D」的"
               "（先在表格里打标签）'（跳过）", None)

    monkeypatch.setattr(A, "gen_selected", fake_gen)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    r = client.post("/api/gen", json={"vertical": False,
                                      "items": ["A → B", "C → D"]})
    assert r.status_code == 200, r.text
    res = client.get(f"/api/jobs/{_wait(r.json()['jobId'])['id']}/result").json()
    # 1) 歧义正例：item 是完整标签名，不是被第一个分隔符切开的 "A"
    assert len(res["videos"]) == 1
    v = res["videos"][0]
    assert v["item"] == "A → B" and v["name"] == "highlight-AB.mp4"
    assert v["url"] and "highlight-AB.mp4" in v["url"]
    # 2) 无幻觉条目：跳过行的右半边都不是文件 → 整行丢弃
    assert not any("C" in x["item"] for x in res["videos"])


# ---------- 会话级行状态进生成（终审 A-1/A-2）----------

def test_gen_applies_session_durs_and_offset_to_rows(env, monkeypatch):
    """用户在界面上设的「前后秒」与「时刻校准」必须进生成（A-1/A-2）。

    机制（终审已复核实锤）：`api_gen.gen()` 自己 `A.load_events_table(vp)` 重算行
    ⇒ 不叠会话级行状态的话，`row[4]/row[5]` 恒为常量默认
    `DEFAULT_BEFORE_S=5.0`/`DEFAULT_AFTER_S=2.0`（`bbh/app.py:42-43`、`:804`）、
    `row[1]`（切点）恒为未校准的检测时刻，而 `generate_highlight` 真的拿这三列去切
    （`bbh/app.py:1992` 的 `picked.append((_parse_t(row[1]), row[4], row[5], row[2]))`）
    ⇒ 预览按你设的 8/3 切、成品按 5/2 切，校准后预览移了、成品没移，且无任何提示。

    旧界面不是这样：`apply_all_rows`（`app.py:1167-1181`）把 b/a 写回**所有行**的
    `r[4]/r[5]`，其输出就是 `rows_state` 并被生成按钮带进去；偏移则由
    `_reload_ui` → `load_events_table(vp, offset)` 写进 `rows[1]`。

    ⚠ **这条测试在改前的树上必须是红的**（把 `api_gen.gen()` 里
    `rows = _apply_session_rows(rows, body)` 那一行摘掉，①② 红、③ **仍绿**）。
    ③ 断的是 `row[0]/[2]/[3]/[6]` 这四列**没被写坏**，
    与「叠不叠会话级行状态」无关：那一行只动 `row[1]/row[4]/row[5]`，摘掉后
    这几列就是盘上重算出来的原值，本来就该绿——③ 若变红，才说明守卫被写漏了。
    """
    import bbh.app as A
    _make_result(env, n=3)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    # 给第一行打标签（set_row_tag 顺手自动勾选）：勾选列与标签列都得原样带过去
    assert client.patch("/api/events/00:12.1", json={"tag": "老杨"}).status_code == 200
    seen = {}

    def fake_gen(video_path, rows, sel, vertical=True, output="reel",
                 on_progress=None):
        seen["rows"] = [list(r) for r in rows]
        yield (None, None, None, None, "✅ 已生成 0 个视频（跳过）", None)

    monkeypatch.setattr(A, "gen_selected", fake_gen)
    r = client.post("/api/gen", json={
        "vertical": True, "items": ["视频合集"],
        "before": 8, "after": 3,               # 「应用到全部」的会话级值
        "durs": {"00:12.1": [9, 3.5]},         # 单行改动（键 = 原始时刻串）
        "offset": 1.5,                         # 时刻校准
    })
    assert r.status_code == 200, r.text
    _wait(r.json()["jobId"])
    rows = seen["rows"]

    # ① 前后秒：单行 durs > 全局 before/after > 服务端行里原值（三段优先级）
    assert (rows[0][4], rows[0][5]) == (9.0, 3.5), rows[0]
    assert (rows[1][4], rows[1][5]) == (8.0, 3.0), rows[1]
    assert (rows[2][4], rows[2][5]) == (8.0, 3.0), rows[2]

    # ② 偏移平移切点 `row[1]`（夹负 max(0.0, …) 见实现；这里都是正数）
    assert [x[1] for x in rows] == ["00:13.6", "01:13.6", "02:13.6"], rows

    # ③ 行键（原始时刻串）没变：它在 `_apply_session_rows` 里**只用来查 durs**，
    #    查完才整体平移 `row[1]`；其余列一个字节不动。①② 里 `durs` 能按
    #    `00:12.1`（**原始**串）命中，本身就是「键在平移之前读完」的证据——
    #    先平移再查表的话 `00:12.1` 查不到，第 0 行会落回全局的 8/3 而红。
    assert [x[0] for x in rows] == [1, 0, 0]                    # 勾选（第 1 行打标顺带勾上）
    assert [x[2] for x in rows] == ["high", "review", "low"]     # 档位
    assert [x[3] for x in rows] == ["0.90", "0.80", "0.70"]      # 置信（字符串，别被当数字动）
    assert [x[6] for x in rows] == ["老杨", "", ""]              # 标签


def test_gen_ignores_non_finite_session_values(env, monkeypatch):
    """非有限值（NaN / ±inf）一律「忽略这次传入、保留行里原值」——**绝不**退回
    默认 5/2（与前端 `finiteDur` 的裁定同义：会话级前后秒作用于所有行，静默跳回
    默认会毁掉用户特意设过的值）。偏移非有限 = 没传 = 不平移。

    JSON 里 NaN/Infinity 是非标准字面量，Python 的 `json.loads`（服务端那一侧）
    默认收它；而 httpx 的 `json=` 走 `json.dumps(allow_nan=False)` 会先在本机炸
    （实测 `ValueError: Out of range float values are not JSON compliant: nan`），
    所以这里发**裸文本体**——那才是「非法值真的到达服务端」的那条路。

    ⚠ 本用例**故意混一个有限值**（`before: 8`）：全非有限的话，「守卫挡住了」与
    「调用被摘掉了」在断言上长得一模一样（行里原值恰好就是 5.0/2.0）⇒ 这条用例
    会在坏树上假绿。混进来之后，`8.0` 落到 row[4] 是「叠加上去这件事真发生了」的
    证人；`after: Infinity` 与 durs 里的 NaN 则证「非有限的那几个被忽略、行里那份
    留着」。「不退回默认 5/2」由下面 `..._shape_guards_...` 那条单测钉（那里行值
    是 6.5/1.5，与默认值不同，退默认会立刻看得出来）。
    """
    import bbh.app as A
    _make_result(env, n=2)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    seen = {}

    def fake_gen(video_path, rows, sel, vertical=True, output="reel",
                 on_progress=None):
        seen["rows"] = [list(r) for r in rows]
        yield (None, None, None, None, "✅ 已生成 0 个视频（跳过）", None)

    monkeypatch.setattr(A, "gen_selected", fake_gen)
    r = client.post("/api/gen",
                    content=('{"vertical": false, "items": ["视频合集"],'
                             ' "before": 8, "after": Infinity,'
                             ' "durs": {"00:12.1": [NaN, -Infinity]},'
                             ' "offset": NaN}').encode("utf-8"),
                    headers={"content-type": "application/json"})
    assert r.status_code == 200, r.text
    _wait(r.json()["jobId"])
    rows = seen["rows"]
    # before=8 生效（叠加真的跑了）；after=Infinity 与 durs 的 NaN 全被忽略，
    # 行里那份 2.0 留着；offset=NaN = 没传 ⇒ 时刻一个都不动
    assert [(x[4], x[5]) for x in rows] == [(8.0, 2.0), (8.0, 2.0)], rows
    assert [x[1] for x in rows] == ["00:12.1", "01:12.1"], rows


def test_apply_session_rows_shape_guards_keep_row_values():
    """`_apply_session_rows` 的守卫逐条单测（不走 HTTP：pydantic 会把字符串直接
    拦成 422，而这些形状正是「前端某天把坏值发过来」时要挡住的）。

    传一个**鸭子类型的 body**（函数只读 `durs`/`before`/`after`/`offset` 四个属性）
    就能覆盖：非数字字符串、`None`、长度不对的 durs 值、非 dict 的 durs、
    负偏移的夹零。全部要求：**保留行里原值，不退默认、不抛异常**。
    """
    from types import SimpleNamespace

    from bbh.web.api_gen import _apply_session_rows
    rows = [["", "00:12.1", "high", "0.90", 5.0, 2.0, "老杨"],
            ["", "00:03.0", "review", "0.60", 6.5, 1.5, ""]]

    def body(**kw):
        return SimpleNamespace(**{"before": None, "after": None,
                                  "durs": None, "offset": None, **kw})

    # 非有限 / 非数字的全局值 + 形状不对的 durs：全保留原值
    out = _apply_session_rows(rows, body(before=float("nan"), after="3",
                                         durs={"00:12.1": ["8", None],
                                               "01:12.1": [1]}))
    assert [(x[4], x[5]) for x in out] == [(5.0, 2.0), (6.5, 1.5)], out
    # 非 dict 的 durs（前端发了个数组过来）同样当没传，而不是抛 TypeError
    out = _apply_session_rows(rows, body(before=7, durs=["00:12.1"]))
    assert [(x[4], x[5]) for x in out] == [(7, 2.0), (7, 1.5)], out
    # offset 为 0 / None / NaN 都不平移（0 平移是恒等，别写一堆 00:12.1 → 00:12.1）
    for off in (0, None, float("nan"), "1.5"):
        assert [x[1] for x in _apply_session_rows(rows, body(offset=off))] \
            == ["00:12.1", "00:03.0"]
    # 负偏移夹到 0（与 load_events_table 里 `max(0.0, e.t + offset)` 逐字同义）：
    # 12.1-9=3.1 照常平移，3.0-9=-6 被夹成 00:00.0
    got = _apply_session_rows(rows, body(offset=-9))
    assert [x[1] for x in got] == ["00:03.1", "00:00.0"], got
    # 组合：负偏移下 durs 的键仍**先于平移**读完——平移后第 0 行已是 "00:03.1"，
    # 若键是平移后才读的，"00:12.1" 这个 durs 键永远查不到；上面的正偏移用例
    # （test_gen_applies_…②③）盖的是「先读键」本身，这里钉它在负偏移下不翻。
    # （durs 值必须用有限数字：`"8"` 这类字符串会被 _finite 当非有限忽略，
    # 命不命中结果一样，钉不住「命中」。）
    got = _apply_session_rows(rows, body(durs={"00:12.1": [9, 3.5]}, offset=-9))
    assert (got[0][1], got[0][4], got[0][5]) == ("00:03.1", 9.0, 3.5), got[0]
    assert got[1][1] == "00:00.0" and (got[1][4], got[1][5]) == (6.5, 1.5), got[1]
    assert [(x[0], x[2], x[3], x[6]) for x in got] \
        == [("", "high", "0.90", "老杨"), ("", "review", "0.60", "")], got
    # 入参不被就地改写（copy-on-write：行是新的 list，原行一个字节不动）
    assert rows == [["", "00:12.1", "high", "0.90", 5.0, 2.0, "老杨"],
                    ["", "00:03.0", "review", "0.60", 6.5, 1.5, ""]]


# ---------- ★ 精选（2026-10-23） ----------

def test_patch_starred_autochecks_and_persists_both_sidecars(env):
    """点一次星就够：后端必须**自动补勾进片**，并把两张 sidecar 都落盘。
    只写一张表的话，重载后会出现「星在、勾选不在」——读盘纠偏会把它抹掉，
    用户看到星白白丢了。"""
    import json
    import os

    import bbh.app as A
    _make_result(env, n=2)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    j = client.patch("/api/events/00:12.1", json={"starred": True}).json()
    row = next(r for r in j["rows"] if r["key"] == "00:12.1")
    assert row["starred"] is True and row["selection"] is True
    work = os.path.join("work", NAME)
    assert json.load(open(os.path.join(work, A.STARRED_FILE),
                          encoding="utf-8")) == ["00:12.1"]
    assert json.load(open(os.path.join(work, A.SELECTION_FILE),
                          encoding="utf-8")) == ["00:12.1"]


def test_uncheck_clears_star(env):
    """撤销进片必须同时清星：否则「精选了但没进片」⇒ 单条发出一个集锦里
    根本没有的片段。这条是三个维护点里最容易漏的。"""
    import bbh.app as A
    _make_result(env, n=2)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    client.patch("/api/events/00:12.1", json={"starred": True})
    j = client.patch("/api/events/00:12.1", json={"selection": False}).json()
    row = next(r for r in j["rows"] if r["key"] == "00:12.1")
    assert row["selection"] is False and row["starred"] is False
    assert A.star_count([[0, "00:12.1", "high", "0.90", 5, 2, "", 0]]) == 0


def test_gen_choices_single_defaults_to_star_when_starred(env):
    """打星后单条清单：精选项置顶 + **默认只勾它**；集锦模式也有精选项
    （2026-10-24 起「一条集锦」也支持只出打星的球），但默认勾选不变。"""
    import bbh.app as A
    _make_result(env, n=2)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    client.patch("/api/events/00:12.1", json={"starred": True})
    j = client.get("/api/gen/choices?output=single").json()
    assert j["starred"] == 1
    assert j["choices"][0] == A.GEN_STAR_LABEL
    assert j["default"] == [A.GEN_STAR_LABEL]
    assert j["starLabel"] == A.GEN_STAR_LABEL
    assert "★ 已精选 1 球" in j["stats"]
    # 集锦模式：精选项置顶（文案是"合成一条"那串），但 **default 仍是合集**
    # ——集锦的主路径没变，预选精选会让每次出集锦都多取消一次。
    r = client.get("/api/gen/choices?output=reel").json()
    assert r["choices"][0] == A.GEN_STAR_LABEL_REEL
    assert A.GEN_STAR_LABEL not in r["choices"]      # 单条那串不许串到集锦里
    assert r["starLabel"] == A.GEN_STAR_LABEL_REEL
    assert r["default"][0] == r["allLabel"] and r["starred"] == 1
    assert "★ 已精选 1 球" in r["stats"]


def test_gen_single_star_item_outputs_only_starred(env, monkeypatch):
    """真跑 gen_selected(output=single) + 精选项：三颗球只打一颗星 ⇒ 只出 1 条，
    且结果清单能被 _gen_result 反解（复用集锦那一条隐式契约，不另起格式）。"""
    import os

    import bbh.app as A
    _make_result(env, n=3)                     # 12.1 / 72.1 / 132.1
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    client.patch("/api/events/01:12.1", json={"starred": True})
    written = []

    def fake_v(video_path, goals, work_dir, out_name, cfg=None):
        p = os.path.join(work_dir, out_name)
        with open(p, "wb") as f:
            f.write(b"\x00" * 64)
        written.append(out_name)
        return p

    monkeypatch.setattr(A.vrender, "build_vertical", fake_v)
    r = client.post("/api/gen", json={"vertical": True, "output": "single",
                                      "items": [A.GEN_STAR_LABEL]})
    assert r.status_code == 200, r.text
    res = client.get(f"/api/jobs/{_wait(r.json()['jobId'])['id']}/result").json()
    assert len(res["videos"]) == 1, res["videos"]
    assert res["videos"][0]["name"] == written[0]
    assert res["videos"][0]["name"].startswith("single-竖-")
    assert "01:12.1" in res["summaryMd"] or "01m12s" in res["videos"][0]["name"]
