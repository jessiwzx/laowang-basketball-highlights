# tests/test_delete_events.py
"""剪辑台「🗑 删除此球」行为级：deleted.json sidecar（非破坏可恢复）+
级联出手人侧（归人组移出/两面墙过滤）+ 删除后 _raw_time_keys 键对齐
（offset≠0 时键仍为原始检测时刻——直接删行会让对齐掉进显示时间毒化
sidecar 键，selection/tag_assignments 全作废）。"""
import json

from bbh import _gr_compat as gr
import pytest

from bbh import manual_groups as manual


def _events_fixture(monkeypatch, tmp_path):
    """真 events.json + 真 _raw_time_keys（键对齐不变量靠真路径保证，
    不 monkeypatch）。事件 60/100/300s → 键 01:00.0/01:40.0/05:00.0。"""
    from bbh import app
    monkeypatch.setattr(app, "_work_dir_for", lambda vp: str(tmp_path))
    (tmp_path / "events.json").write_text(json.dumps({
        "video": "v.mp4", "duration": 600.0, "warning": None,
        "events": [{"t": 60.0, "confidence": 0.9, "status": "high"},
                   {"t": 100.0, "confidence": 0.8, "status": "review"},
                   {"t": 300.0, "confidence": 0.7, "status": "low"}]}),
        encoding="utf-8")
    return app


def test_delete_event_行为级与级联出手人(tmp_path, monkeypatch):
    """删行 → 行消失 + deleted.json 记键 + manual_groups 移出 +
    selection/tag_assignments 缩行重存（被删键不复存在）。"""
    app = _events_fixture(monkeypatch, tmp_path)
    manual.assign(str(tmp_path), "老陈", ["01:00.0"])
    (tmp_path / "selection.json").write_text(
        json.dumps(["01:00.0", "01:40.0"]), encoding="utf-8")
    (tmp_path / "tag_assignments.json").write_text(
        json.dumps({"01:00.0": "老陈"}), encoding="utf-8")
    _dur, rows = app.load_events_table("v.mp4")
    assert len(rows) == 3 and rows[0][0] == 1 and rows[1][0] == 1

    msg, rows2 = app.delete_event("v.mp4", rows, 0)
    assert "01:00.0" in msg and "恢复" in msg          # 可恢复入口要提示
    assert len(rows2) == 2 and rows2[0][1] == "01:40.0"
    assert json.loads((tmp_path / "deleted.json").read_text(
        encoding="utf-8")) == ["01:00.0"]              # 非破坏记键
    assert manual.load(str(tmp_path)) == {}            # 级联出手人侧
    assert json.loads((tmp_path / "selection.json").read_text(
        encoding="utf-8")) == ["01:40.0"]              # 被删键清出勾选
    assert json.loads((tmp_path / "tag_assignments.json").read_text(
        encoding="utf-8")) == {}                       # 标签分配同款


def test_delete_event_未选行拒绝(tmp_path, monkeypatch):
    app = _events_fixture(monkeypatch, tmp_path)
    _dur, rows = app.load_events_table("v.mp4")
    with pytest.raises(gr.Error):
        app.delete_event("v.mp4", rows, None)          # 没点行
    with pytest.raises(gr.Error):
        app.delete_event("v.mp4", rows, 99)            # 行号越界
    assert not (tmp_path / "deleted.json").exists()    # 拒绝时不落盘


def test_delete_event_重复删除幂等(tmp_path, monkeypatch):
    app = _events_fixture(monkeypatch, tmp_path)
    _dur, rows = app.load_events_table("v.mp4")
    app.delete_event("v.mp4", rows, 1)
    _dur2, rows_b = app.load_events_table("v.mp4")     # 重载=过滤视图
    assert [r[1] for r in rows_b] == ["01:00.0", "05:00.0"]
    app.delete_event("v.mp4", rows_b, 1)               # 再删别的球
    assert json.loads((tmp_path / "deleted.json").read_text(
        encoding="utf-8")) == ["01:40.0", "05:00.0"]   # 追加不覆盖


def test_load_events_table_过滤已删(tmp_path, monkeypatch):
    app = _events_fixture(monkeypatch, tmp_path)
    (tmp_path / "deleted.json").write_text(
        json.dumps(["01:40.0"]), encoding="utf-8")
    dur, rows = app.load_events_table("v.mp4")
    assert dur == 600.0                                # 时长不受删行影响
    assert [r[1] for r in rows] == ["01:00.0", "05:00.0"]
    assert [r[2] for r in rows] == ["high", "low"]     # 行内容仍按事件对齐


def test_删除后键对齐_偏移非零(tmp_path, monkeypatch):
    """offset≠0 的核心回归：显示时间已偏移，删行后 sidecar 键仍必须是
    原始检测时刻——对齐掉进显示时间会让勾选/标签在重载后全部作废。"""
    app = _events_fixture(monkeypatch, tmp_path)
    _dur, rows = app.load_events_table("v.mp4", offset=10.0)
    assert rows[0][1] == "01:10.0"                     # 显示层偏移了 10s
    _msg, rows2 = app.delete_event("v.mp4", rows, 0)
    assert json.loads((tmp_path / "deleted.json").read_text(
        encoding="utf-8")) == ["01:00.0"]              # 键=原始时刻非 01:10.0
    _dur3, rows3 = app.load_events_table("v.mp4", offset=10.0)
    rows3[0][0] = 1                                    # 勾上 01:40.0（显示 01:50.0）
    app._save_selection("v.mp4", rows3)
    assert json.loads((tmp_path / "selection.json").read_text(
        encoding="utf-8")) == ["01:40.0"]              # 存的是原始键


def test_墙过滤已删球(tmp_path, monkeypatch):
    """出手人 tab 联动（用户需求：剪辑台删掉，出手人里也对应删掉）：
    未归人墙/已归人调整墙都不再显示已删球（traces 视图过滤，引擎不动）。"""
    app = _events_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "_load_calib_or_none", lambda vp="": None)
    th = tmp_path / "shooters"
    th.mkdir()
    for name in ("a.jpg", "b.jpg", "e.jpg"):
        (th / name).write_bytes(b"\xff\xd8fake")
    (tmp_path / "shooters.json").write_text(json.dumps(
        {"video": "v.mp4", "ver": 3, "traces": {
            "32:00.0": {"thumbs": ["shooters/a.jpg"]},
            "07:00.0": {"thumbs": ["shooters/b.jpg"]},
            "05:00.0": {"thumbs": ["shooters/e.jpg"]}}}), encoding="utf-8")
    manual.assign(str(tmp_path), "甲", ["32:00.0", "07:00.0"])
    (tmp_path / "deleted.json").write_text(
        json.dumps(["07:00.0"]), encoding="utf-8")
    assert [k for *_, k in app.wall_payload("v.mp4", "时间")] == [
        "05:00.0"]          # 未归人墙剔已删（07 不在；32 已归人不属这墙）
    assert [k for _, _, k in app.assigned_wall_payload("v.mp4")] == [
        "32:00.0"]         # 调整墙剔已删（甲组 32 留、07 消失）


def test_restore_deleted_行为级(tmp_path, monkeypatch):
    app = _events_fixture(monkeypatch, tmp_path)
    with pytest.raises(gr.Error):
        app.restore_deleted("v.mp4")                   # 无已删（文件缺失）
    _dur, rows = app.load_events_table("v.mp4")
    app.delete_event("v.mp4", rows, 2)
    got = app.restore_deleted("v.mp4")
    assert got == ["05:00.0"]
    assert json.loads((tmp_path / "deleted.json").read_text(
        encoding="utf-8")) == []                       # 清空=全恢复
    _dur2, rows2 = app.load_events_table("v.mp4")
    assert [r[1] for r in rows2] == ["01:00.0", "01:40.0", "05:00.0"]
    assert rows2[2][0] == 0                            # 恢复后未勾选（核对重来）
    with pytest.raises(gr.Error):
        app.restore_deleted("v.mp4")                   # 已清空再恢复=拒绝


def test_evaluate_已删球不计误报(tmp_path):
    """工具级：evaluate.py 认 deleted.json——已删球是策展出局（随意投篮
    不进集锦），不是检测错误，不能算假绿/误报。用户在 08-29_3 边删边标，
    固化第四测试集前必须有这条口径，否则删除会打脏检测器精度指标。"""
    import pathlib
    import subprocess
    import sys
    work = tmp_path / "w"
    work.mkdir()
    (work / "events.json").write_text(json.dumps(
        {"video": "v", "duration": 300.0,
         "events": [{"t": 60.0, "status": "high", "confidence": 0.9},
                    {"t": 100.0, "status": "high", "confidence": 0.9},
                    {"t": 200.0, "status": "review", "confidence": 0.6}]}),
        encoding="utf-8")
    (work / "deleted.json").write_text(
        json.dumps(["01:40.0"]), encoding="utf-8")     # 100s = 01:40.0
    truth = tmp_path / "truth.json"
    truth.write_text(json.dumps(
        {"complete": True, "goals": [60.0], "tolerance": 0.2}),
        encoding="utf-8")
    repo = pathlib.Path(__file__).resolve().parents[1]
    r = subprocess.run(
        [sys.executable, "-X", "utf8", str(repo / "tools" / "evaluate.py"),
         str(work / "events.json"), str(truth)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(repo))
    assert "已删出局: 1" in r.stdout                    # 剔除透明可审计
    assert "假绿: 0/1" in r.stdout                      # 被删的 100s 不算假绿
    assert r.returncode == 0                           # 验收门通过


# ---------- 「🗑 批量删除」（2026-09-06：比赛间隙随意投篮常成串，逐球点
# 删太碎）。设计约束：表格「选中」勾选语义=人工核对确认进球（44754f5
# 定案），不能挪用做删除选择——多选放删除对话框内的 CheckboxGroup，
# 选择动作完全在删除语境内，零语义污染。

def test_delete_events_批量核心(tmp_path, monkeypatch):
    """一次删 2 键：行剔除 + deleted.json 记两键 + 归人级联移出 +
    勾选/标签按剩余行重存。msg 用显示时间（用户视角），存储用原始键。"""
    app = _events_fixture(monkeypatch, tmp_path)
    manual.assign(str(tmp_path), "甲", ["05:00.0"])
    (tmp_path / "selection.json").write_text(
        json.dumps(["01:00.0", "01:40.0", "05:00.0"]), encoding="utf-8")
    (tmp_path / "tag_assignments.json").write_text(
        json.dumps({"01:00.0": "老陈", "05:00.0": "甲"}), encoding="utf-8")
    _dur, rows = app.load_events_table("v.mp4", offset=10.0)   # 显示=键+10s
    msg, rows2 = app.delete_events("v.mp4", rows, ["01:00.0", "05:00.0"])
    assert [r[1] for r in rows2] == ["01:50.0"]                # 只剩中间球
    assert json.loads((tmp_path / "deleted.json").read_text(
        encoding="utf-8")) == ["01:00.0", "05:00.0"]           # 键=原始时刻
    assert manual.load(str(tmp_path)) == {}                    # 甲组被整组移出
    assert json.loads((tmp_path / "selection.json").read_text(
        encoding="utf-8")) == ["01:40.0"]
    assert json.loads((tmp_path / "tag_assignments.json").read_text(
        encoding="utf-8")) == {}
    assert "01:10.0" in msg and "05:10.0" in msg               # msg 显示时间
    assert "2" in msg and "恢复" in msg


def test_delete_events_空与全失配拒绝(tmp_path, monkeypatch):
    """没勾就确认 / 勾的键全不在当前视图（换视频/重跑后旧面板残留）→
    拒绝且不落盘——防「空删」静默成功把用户搞糊涂。"""
    app = _events_fixture(monkeypatch, tmp_path)
    _dur, rows = app.load_events_table("v.mp4")
    with pytest.raises(gr.Error):
        app.delete_events("v.mp4", rows, [])
    with pytest.raises(gr.Error):
        app.delete_events("v.mp4", rows, ["99:00.0", "88:00.0"])
    assert not (tmp_path / "deleted.json").exists()


def test_delete_events_混入失配键容错(tmp_path, monkeypatch):
    """混入当前视图不存在的键（State 过期）→ 只删还在的，不炸——
    批量场景宁丢失配键不中断整批。"""
    app = _events_fixture(monkeypatch, tmp_path)
    _dur, rows = app.load_events_table("v.mp4")
    msg, rows2 = app.delete_events("v.mp4", rows, ["01:40.0", "99:00.0"])
    assert [r[1] for r in rows2] == ["01:00.0", "05:00.0"]
    assert json.loads((tmp_path / "deleted.json").read_text(
        encoding="utf-8")) == ["01:40.0"]


def test_delete_event_单删走批量路径(tmp_path, monkeypatch):
    """单删（点行→🗑）重构为 delete_events([key])——单一实现防双路径
    漂移；msg 统一批量文案。"""
    app = _events_fixture(monkeypatch, tmp_path)
    _dur, rows = app.load_events_table("v.mp4")
    msg, rows2 = app.delete_event("v.mp4", rows, 2)
    assert [r[1] for r in rows2] == ["01:00.0", "01:40.0"]
    assert json.loads((tmp_path / "deleted.json").read_text(
        encoding="utf-8")) == ["05:00.0"]
    assert "05:00.0" in msg and "恢复" in msg


def test_bulk_delete_choices_显示名与键对(tmp_path, monkeypatch):
    """面板 choices：显示名=显示时间｜档位 emoji｜标签（用户视角），
    value=原始时刻键（删除对齐口径）——两层时间各司其职。"""
    app = _events_fixture(monkeypatch, tmp_path)
    (tmp_path / "tag_assignments.json").write_text(
        json.dumps({"01:40.0": "老陈"}), encoding="utf-8")
    _dur, rows = app.load_events_table("v.mp4", offset=10.0)
    ch = app.bulk_delete_choices("v.mp4", rows)
    assert [(lbl, key) for lbl, key in ch] == [
        ("01:10.0｜🟢 高", "01:00.0"),
        ("01:50.0｜🟡 待｜老陈", "01:40.0"),
        ("05:10.0｜⚪ 低", "05:00.0")]


def test_bulk_delete_choices_空表安全(tmp_path, monkeypatch):
    """没载入视频/表格空 → []，面板开也只是空清单不炸。"""
    app = _events_fixture(monkeypatch, tmp_path)
    assert app.bulk_delete_choices("v.mp4", []) == []


# ---------- 「⚠ 缩略图有误」标记（2026-09-06：核对时遇到的坏缩略图
# 逐个标记，标注完所有视频后统一读 json 看图归因——四类缺陷同款流程） ----------

def test_toggle_thumb_flag_标记与取消(tmp_path, monkeypatch):
    """点行→标记：键（原始检测时刻）入 thumb_flags.json，按钮转「已标记」
    款；再点=取消。计数 md 常显清单，标完可直接拿 json 分析。"""
    app = _events_fixture(monkeypatch, tmp_path)
    _dur, rows = app.load_events_table("v.mp4", offset=10.0)
    btn, md = app.toggle_thumb_flag("v.mp4", rows, 0)     # 显示 01:10.0
    assert btn.startswith("✅") and "01:00.0" in md       # 存的是原始键
    assert json.loads((tmp_path / "thumb_flags.json").read_text(
        encoding="utf-8")) == ["01:00.0"]
    btn2, md2 = app.toggle_thumb_flag("v.mp4", rows, 0)   # 再点=取消
    assert btn2.startswith("⚠") and md2 == ""
    assert json.loads((tmp_path / "thumb_flags.json").read_text(
        encoding="utf-8")) == []


def test_toggle_thumb_flag_未选行拒绝(tmp_path, monkeypatch):
    app = _events_fixture(monkeypatch, tmp_path)
    _dur, rows = app.load_events_table("v.mp4")
    with pytest.raises(gr.Error):
        app.toggle_thumb_flag("v.mp4", rows, None)
    assert not (tmp_path / "thumb_flags.json").exists()   # 拒绝不落盘


def test_toggle_thumb_flag_跨球累积与删球解耦(tmp_path, monkeypatch):
    """多球各自标记累积；删除被标球不清理标记（标记是分析样本，
    球删了样本仍要能对上原图看）。"""
    app = _events_fixture(monkeypatch, tmp_path)
    _dur, rows = app.load_events_table("v.mp4")
    app.toggle_thumb_flag("v.mp4", rows, 0)
    _btn, md = app.toggle_thumb_flag("v.mp4", rows, 2)
    assert json.loads((tmp_path / "thumb_flags.json").read_text(
        encoding="utf-8")) == ["01:00.0", "05:00.0"]
    app.delete_event("v.mp4", rows, 2)                    # 删 05:00.0
    assert json.loads((tmp_path / "thumb_flags.json").read_text(
        encoding="utf-8")) == ["01:00.0", "05:00.0"]      # 标记留着


def test_preview_row_click_按钮态跟随当前行(tmp_path, monkeypatch):
    """点行刷新两个标记按钮文案（该球已标→「已标记」款）+计数常显——
    核对工作流里点行是看缩略图的必经动作，标记态零额外操作即对齐。"""
    app = _events_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(app, "_preview_and_reason",
                        lambda vp, r, i, b, a: ("<video", ""))
    monkeypatch.setattr(app, "_tag_choices", lambda vp: [])
    _dur, rows = app.load_events_table("v.mp4")
    app.toggle_thumb_flag("v.mp4", rows, 1)               # 标第 2 行
    app.toggle_cross_flag("v.mp4", rows, 1)               # 同球也标十字
    evt = gr.SelectData(None, {"index": [1], "value": None})
    out = app.preview_row_click("v.mp4", rows, evt)
    assert out[5].startswith("✅") and "1 张" in out[6]   # 已标球（按钮+计数）
    assert out[7].startswith("✅") and "1 个" in out[8]   # 十字按钮态+计数
    evt0 = gr.SelectData(None, {"index": [0], "value": None})
    out0 = app.preview_row_click("v.mp4", rows, evt0)
    assert out0[5].startswith("⚠")                          # 未标球
    assert out0[7].startswith("✚")                          # 未标十字


# ---------- 「✚ 十字有误」标记（2026-09-06：与缩略图有误分开采样本——
# 用户定语义：缩略图有误=图片内容上未出现真实出手人（裁剪/画面问题），
# 十字有误=球的十字标记（出手点红十字）位置错（溯源定位问题）。归因
# 路径完全不同，故两类独立 sidecar，一球可同时标两类的。 ----------

def test_toggle_cross_flag_标记与取消(tmp_path, monkeypatch):
    """点行→标记：键（原始检测时刻）入 cross_flags.json，按钮转「已标记」
    款；再点=取消。计数 md 常显清单，标完可直接拿 json 分析归因。"""
    app = _events_fixture(monkeypatch, tmp_path)
    _dur, rows = app.load_events_table("v.mp4", offset=10.0)
    btn, md = app.toggle_cross_flag("v.mp4", rows, 0)     # 显示 01:10.0
    assert btn.startswith("✅") and "01:00.0" in md       # 存的是原始键
    assert json.loads((tmp_path / "cross_flags.json").read_text(
        encoding="utf-8")) == ["01:00.0"]
    btn2, md2 = app.toggle_cross_flag("v.mp4", rows, 0)   # 再点=取消
    assert btn2.startswith("✚") and md2 == ""
    assert json.loads((tmp_path / "cross_flags.json").read_text(
        encoding="utf-8")) == []


def test_toggle_cross_flag_未选行拒绝(tmp_path, monkeypatch):
    app = _events_fixture(monkeypatch, tmp_path)
    _dur, rows = app.load_events_table("v.mp4")
    with pytest.raises(gr.Error):
        app.toggle_cross_flag("v.mp4", rows, None)
    assert not (tmp_path / "cross_flags.json").exists()   # 拒绝不落盘


def test_两类标记独立并存(tmp_path, monkeypatch):
    """同一球既没露人又十字错 → 两类可同标互不干扰——取消其一不动
    另一类（分析时按文件分开归因）。"""
    app = _events_fixture(monkeypatch, tmp_path)
    _dur, rows = app.load_events_table("v.mp4")
    app.toggle_thumb_flag("v.mp4", rows, 1)
    app.toggle_cross_flag("v.mp4", rows, 1)
    assert json.loads((tmp_path / "thumb_flags.json").read_text(
        encoding="utf-8")) == ["01:40.0"]
    assert json.loads((tmp_path / "cross_flags.json").read_text(
        encoding="utf-8")) == ["01:40.0"]
    app.toggle_thumb_flag("v.mp4", rows, 1)               # 只取消缩略图标记
    assert json.loads((tmp_path / "thumb_flags.json").read_text(
        encoding="utf-8")) == []
    assert json.loads((tmp_path / "cross_flags.json").read_text(
        encoding="utf-8")) == ["01:40.0"]                 # 十字标记不受影响
