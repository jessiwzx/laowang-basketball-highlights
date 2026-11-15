# tests/test_manual_groups.py
"""manual_groups.py 单测 + app.assign_person 行为级（tmp work 目录+monkeypatch）。"""
import json

import pytest

from bbh import manual_groups as manual   # plan 写 from bbh import manual——
# 实际模块文件名 manual_groups.py（与 plan Files/commit 清单一致），别名保持用例原文


def test_归人与改判语义(tmp_path):
    manual.assign(str(tmp_path), "老陈", ["01:00.0", "01:40.0"])
    manual.assign(str(tmp_path), "小左", ["02:00.0"])
    g = manual.assign(str(tmp_path), "小左", ["01:40.0", "02:00.0"])  # 改判
    assert g == {"老陈": ["01:00.0"], "小左": ["01:40.0", "02:00.0"]}
    g2 = manual.assign(str(tmp_path), "小左", ["01:40.0"])   # 去重幂等
    assert g2["小左"] == ["01:40.0", "02:00.0"]
    assert manual.assign(str(tmp_path), "", ["03:00.0"]) == g2  # 空人名不落盘
    assert manual.assign(str(tmp_path), "老陈", []) == g2        # 空keys不落盘


def test_移出与容错(tmp_path):
    manual.assign(str(tmp_path), "老陈", ["01:00.0", "01:40.0"])
    g = manual.unassign(str(tmp_path), ["01:00.0"])
    assert g == {"老陈": ["01:40.0"]}
    assert manual.unassign(str(tmp_path), ["09:99.9"]) == g   # 不在组=无操作
    assert manual.unassign(str(tmp_path), ["01:40.0"]) == {}  # 空组清理
    (tmp_path / "manual_groups.json").write_text("{坏json", encoding="utf-8")
    assert manual.load(str(tmp_path)) == {}                   # 损坏容错
    assert manual.unassign(str(tmp_path), []) == {}


def test_损坏文件先备份再重建(tmp_path):
    """spec：损坏→备份后重建。损坏原文挪 .corrupt.bak 供人工找回，
    缺失（FileNotFoundError 也走 OSError）不产 .bak——纯缺失≠损坏。"""
    bad = "{坏json，不是有效的 JSON"
    (tmp_path / "manual_groups.json").write_text(bad, encoding="utf-8")
    assert manual.load(str(tmp_path)) == {}
    bak = tmp_path / "manual_groups.json.corrupt.bak"
    assert bak.exists() and bak.read_text(encoding="utf-8") == bad  # 原文备份
    assert not (tmp_path / "manual_groups.json").exists()  # 已挪走，重建从空开始
    manual.assign(str(tmp_path), "老陈", ["01:00.0"])       # 重建路径可正常写回
    assert manual.load(str(tmp_path)) == {"老陈": ["01:00.0"]}
    assert manual.load(str(tmp_path / "无此目录")) == {}   # 缺失不抛也不产 .bak
    assert not (tmp_path / "无此目录").exists()


def test_身份与特征版本解耦(tmp_path):
    """manual_groups.json 只挂人名+球 key，不含任何版本字段——
    特征重算/FEAT_VERSION 变化不作废（spec：手动标签是事实不是缓存）。"""
    manual.assign(str(tmp_path), "老陈", ["01:00.0"])
    raw = (tmp_path / "manual_groups.json").read_text(encoding="utf-8")
    assert json.loads(raw) == {"老陈": ["01:00.0"]}
    assert "feat_ver" not in raw and "calib" not in raw


def test_assign_person_双写与护栏(tmp_path, monkeypatch):
    from bbh import app
    monkeypatch.setattr(app, "_work_dir_for", lambda vp: str(tmp_path))
    monkeypatch.setattr(app, "_raw_time_keys",
                        lambda vp, rows: [r[1] for r in rows])
    (tmp_path / "shooters.json").write_text(json.dumps(
        {"video": "v.mp4", "ver": 3, "traces": {
            "01:00.0": {"thumbs": [], "status": "high"},
            "01:02.0": {"thumbs": [], "status": "high"},
            "05:00.0": {"thumbs": [], "status": "high"}}}), encoding="utf-8")
    rows = [[1, "01:00.0", "high", "0.9", 5, 2, ""],
            [1, "01:02.0", "high", "0.9", 6, 2, ""],
            [0, "05:00.0", "high", "0.9", 7, 2, ""]]
    msg, rows2, html, choices = app.assign_person(
        "v.mp4", rows, "老陈", extra_keys=["01:00.0", "01:02.0"])
    assert "护栏" in msg and "01:00.0" in msg        # 2.0s < 2.5s → 警示
    assert manual.load(str(tmp_path)) == {"老陈": ["01:00.0", "01:02.0"]}
    raw = (tmp_path / app.ASSIGN_FILE).read_text(encoding="utf-8")
    assert "老陈" in raw and "01:00.0" in raw and "01:02.0" in raw  # 双写
    assert rows2[0][6] == "老陈" and rows2[1][6] == "老陈"
    assert rows2[2][6] == ""                          # 未点选不动
    assert rows2[0][0] == 1 and rows2[1][0] == 1      # 勾选=核对标记，归人不清
    assert "老陈" in choices and "手动归人" in html


def test_assign_person_单球增量护栏查整组(tmp_path, monkeypatch):
    """护栏不能只查本次归人：老陈已有 01:00.0，再单点 01:02.0 归同一人，
    违例跨在"旧组+新球"之间——只查 sel 的实现永不触发（终审修复 2）。"""
    from bbh import app
    monkeypatch.setattr(app, "_work_dir_for", lambda vp: str(tmp_path))
    monkeypatch.setattr(app, "_raw_time_keys",
                        lambda vp, rows: [r[1] for r in rows])
    (tmp_path / "shooters.json").write_text(json.dumps(
        {"video": "v.mp4", "ver": 3, "traces": {
            "01:00.0": {"thumbs": [], "status": "high"},
            "01:02.0": {"thumbs": [], "status": "high"}}}), encoding="utf-8")
    rows0 = [[1, "01:00.0", "high", "0.9", 5, 2, ""]]
    msg0, *_ = app.assign_person("v.mp4", rows0, "老陈",
                                 extra_keys=["01:00.0"])  # 首球单归：无违例
    assert "护栏" not in msg0
    rows1 = [[0, "01:00.0", "high", "0.9", 5, 2, "老陈"],
             [1, "01:02.0", "high", "0.9", 6, 2, ""]]
    msg1, *_ = app.assign_person("v.mp4", rows1, "老陈",
                                 extra_keys=["01:02.0"])  # 增量 2.0s<2.5s
    assert "护栏" in msg1 and "2.0s" in msg1 and "01:00.0" in msg1
    assert manual.load(str(tmp_path)) == {"老陈": ["01:02.0", "01:00.0"]}


def test_manual_payload_ui_下拉gr_update封装(tmp_path, monkeypatch):
    """布线层封皮：person_dd 是 gr.Dropdown，choices 裸 list 会被
    postprocess 成 value（gradio 5.50 实测）——标签库选人失效+毒化输入框。
    封皮必须发 gr.update(choices=...)；核心 render_manual_payload 保持纯 list
    （assign/unassign 的 *展开仍是裸 choices，现有测试口径不毒化）。"""
    from bbh import app
    monkeypatch.setattr(app, "_work_dir_for", lambda vp: str(tmp_path))
    monkeypatch.setattr(app, "_raw_time_keys",
                        lambda vp, rows: [r[1] for r in rows])
    monkeypatch.setattr(app, "_load_tags", lambda: ["标签库甲"])  # 隔离真 tags.json
    manual.assign(str(tmp_path), "老陈", ["01:00.0"])
    rows = [[1, "01:00.0", "high", "0.9", 5, 2, ""]]
    ui_html, upd = app._manual_payload_ui("v.mp4", rows)
    assert isinstance(upd, dict)                       # gr.update 是 dict 子类
    assert "老陈" in upd["choices"] and "标签库甲" in upd["choices"]
    core_html, choices = app.render_manual_payload("v.mp4", rows)
    assert isinstance(choices, list) and "老陈" in choices   # 核心不毒化
    assert ui_html == core_html


def test_unassign_person_行为级(tmp_path, monkeypatch):
    """移出：组里无该人无该键、msg 提示移出、**标签（r[6]）一并清掉**
    （2026-10-11 用户裁定，见 test_unassign_person_同时清标签）、
    勾选（核对标记）不动、不落 selection.json。"""
    from bbh import app
    monkeypatch.setattr(app, "_work_dir_for", lambda vp: str(tmp_path))
    monkeypatch.setattr(app, "_raw_time_keys",
                        lambda vp, rows: [r[1] for r in rows])
    (tmp_path / "shooters.json").write_text(json.dumps(
        {"video": "v.mp4", "ver": 3, "traces": {
            "01:00.0": {"thumbs": [], "status": "high"}}}), encoding="utf-8")
    manual.assign(str(tmp_path), "老陈", ["01:00.0"])
    rows = [[1, "01:00.0", "high", "0.9", 5, 2, "老陈"]]
    msg, rows2, html, choices = app.unassign_person(
        "v.mp4", rows, extra_keys=["01:00.0"])
    assert "移出" in msg
    assert manual.load(str(tmp_path)) == {}            # 无该人无该键
    assert rows2[0][6] == ""                           # 标签一并清掉
    assert rows2[0][0] == 1                            # 勾选（核对标记）未动
    assert not (tmp_path / "selection.json").exists()  # 不碰勾选持久化
    assert "手动归人" in html


# ---------- 未归人缩略图墙（Phase 2 补充：排序不定身份，多选并入归人） ----------

def _wall_fixture(tmp_path, monkeypatch):
    from bbh import app
    monkeypatch.setattr(app, "_work_dir_for", lambda vp: str(tmp_path))
    monkeypatch.setattr(app, "_raw_time_keys",
                        lambda vp, rows: [r[1] for r in rows])
    monkeypatch.setattr(app, "_load_calib_or_none",
                        lambda vp="": None)      # 无标定→外观退化
    th = tmp_path / "shooters"
    th.mkdir(exist_ok=True)

    def mk(name):
        (th / name).write_bytes(b"\xff\xd8fake")
        return [f"shooters/{name}"]

    (tmp_path / "shooters.json").write_text(json.dumps(
        {"video": "v.mp4", "ver": 3, "traces": {
            "32:00.0": {"thumbs": mk("a.jpg"), "release_xy": [900, 100]},
            "07:00.0": {"thumbs": mk("b.jpg"), "release_xy": [100, 300]},
            "110:00.0": {"thumbs": mk("c.jpg"), "release_xy": [100, 200]},
            "09:00.0": {"thumbs": [], "release_xy": [1, 1]},
            "05:00.0": {"thumbs": ["shooters/missing.jpg"]}}}), encoding="utf-8")
    return app


def test_wall_payload_未归人球按模式排序(tmp_path, monkeypatch):
    app = _wall_fixture(tmp_path, monkeypatch)
    manual.assign(str(tmp_path), "甲", ["07:00.0"])
    got = app.wall_payload("v.mp4", "时间")
    # 剔已归人（07）、无缩略图（09）、文件缺失（05）；解析时刻序非字典序
    assert [k for *_, k in got] == ["32:00.0", "110:00.0"]
    assert all(str(p).startswith(str(tmp_path)) for p, _, _ in got)
    assert app.wall_payload("v.mp4", "出手点") == [
        (str(tmp_path / "shooters" / "c.jpg"), "110:00.0", "110:00.0"),
        (str(tmp_path / "shooters" / "a.jpg"), "32:00.0", "32:00.0")]  # x=100 在前
    assert app.wall_payload("v.mp4", "外观相似") == got          # 无特征→时间序
    assert app.wall_payload("v.mp4", "不认识的模式") == got      # 未知模式兜底时间序


def test_wall_payload_定位失效球caption带警示(tmp_path, monkeypatch):
    """A 类修复：spot=bad（出手点筐平面以上）的球 caption 追加 ⚠——
    caption 是展示字段，第三位 key 恒干净（点选/归人用它，防 49a2817
    布线事故同款毒化）。"""
    app = _wall_fixture(tmp_path, monkeypatch)
    import json as _json
    p = tmp_path / "shooters.json"
    data = _json.loads(p.read_text(encoding="utf-8"))
    (tmp_path / "shooters" / "d.jpg").write_bytes(b"\xff\xd8fake")
    data["traces"]["20:00.0"] = {"thumbs": ["shooters/d.jpg"],
                                 "release_xy": [500, 100], "spot": "bad"}
    data["traces"]["32:00.0"]["spot"] = "ok"
    data["traces"]["110:00.0"]["spot"] = "ok"
    p.write_text(_json.dumps(data), encoding="utf-8")
    manual.assign(str(tmp_path), "甲", ["07:00.0"])          # 剔已归人
    got = app.wall_payload("v.mp4", "时间")
    assert [(k, c) for _, c, k in got] == [
        ("20:00.0", "20:00.0·⚠定位失效"), ("32:00.0", "32:00.0"),
        ("110:00.0", "110:00.0")]        # 解析时刻序；仅 bad 球带文字警示
        # 2026-09-06 用户报「横图不确定是不是报错」——光秃秃 ⚠ 语义不明，
        # caption 文字化「⚠定位失效」直说这是兜底全场图不是程序坏了


def test_两墙与面板优先动图缺则退静态(tmp_path, monkeypatch):
    """动图（shooter._gif_backfill 产物）存在 → 未归人墙/已归人调整墙/归人
    面板优先用它；未生成退回静态 thumbs——文件存在性即状态，caption/key
    不变（点选/归人布线零改动）。"""
    app = _wall_fixture(tmp_path, monkeypatch)
    manual.assign(str(tmp_path), "甲", ["32:00.0"])
    # gif 文件名由 key 派生（shooter.gif_rel），与 thumbs 里的 a.jpg 无关
    (tmp_path / "shooters" / "32-00.0.gif").write_bytes(b"GIF89a-fake")
    # 未归人墙：32 已归人出局；07/110 无 gif → 静态（原有行为不动）
    assert app.wall_payload("v.mp4", "时间") == [
        (str(tmp_path / "shooters" / "b.jpg"), "07:00.0", "07:00.0"),
        (str(tmp_path / "shooters" / "c.jpg"), "110:00.0", "110:00.0")]
    # 调整墙/归人面板：32 有 gif → 动图
    assert app.assigned_wall_payload("v.mp4") == [
        (str(tmp_path / "shooters" / "32-00.0.gif"), "甲·32:00.0", "32:00.0")]
    html, _ = app.render_manual_payload("v.mp4", [])   # rows 仅保接口兼容
    assert "32-00.0.gif" in html


def test_assign_person_墙上点选是唯一来源勾选不并入(tmp_path, monkeypatch):
    """2026-09-06 误伤复盘：归人曾把表格勾选并入集合——但表格勾选的语义是
    "人工核对确认进球"（用户定案），与出手人无关；已勾选的别人球会被一并抢
    走改判。归人只认墙上点选（extra_keys），勾选行 r[0] 既不并入也不清空。"""
    app = _wall_fixture(tmp_path, monkeypatch)
    manual.assign(str(tmp_path), "老王", ["01:00.0"])      # 勾选里混着老王的球
    rows = [[1, "01:00.0", "high", "0.9", 5, 2, "老王"],
            [1, "05:00.0", "high", "0.9", 7, 2, ""]]
    msg, rows2, html, choices = app.assign_person(
        "v.mp4", rows, "老陈", extra_keys=["05:00.0", "05:00.0"])  # 去重
    assert "1 球" in msg
    assert manual.load(str(tmp_path)) == {
        "老王": ["01:00.0"], "老陈": ["05:00.0"]}          # 老王未被抢
    assert rows2[1][6] == "老陈" and rows2[0][6] == "老王"
    assert rows2[0][0] == 1 and rows2[1][0] == 1          # 勾选原样保留
    with pytest.raises(Exception):                        # 墙上没点 → 拒绝
        app.assign_person("v.mp4", rows, "老陈")


def test_unassign_person_墙上点选是唯一来源勾选不并入(tmp_path, monkeypatch):
    """移出只认墙上点选（extra_keys）：同表里没点选的球，标签与归人都不动
    ——勾选行 r[0] 既不并入也不清空（勾选=核对标记，与出手人无关）。"""
    app = _wall_fixture(tmp_path, monkeypatch)
    manual.assign(str(tmp_path), "老陈", ["01:00.0", "05:00.0"])
    rows = [[1, "01:00.0", "high", "0.9", 5, 2, "老陈"],
            [1, "05:00.0", "high", "0.9", 7, 2, "老陈"]]
    msg, rows2, *_ = app.unassign_person("v.mp4", rows,
                                         extra_keys=["05:00.0"])
    assert manual.load(str(tmp_path)) == {"老陈": ["01:00.0"]}
    assert rows2[0][6] == "老陈"                          # 未点选的标签不动
    assert rows2[1][6] == ""                              # 点选的标签一并清
    assert rows2[0][0] == 1 and rows2[1][0] == 1          # 勾选原样保留
    with pytest.raises(Exception):
        app.unassign_person("v.mp4", rows)


def test_wall_toggle_点选切换与清单(tmp_path, monkeypatch):
    import types
    app = _wall_fixture(tmp_path, monkeypatch)
    order = ["32:00.0", "110:00.0"]
    html1, sel1 = app._wall_toggle(order, [], types.SimpleNamespace(index=1))
    assert sel1 == ["110:00.0"]
    assert "1 球" in html1 and "110:00.0" in html1
    html2, sel2 = app._wall_toggle(order, sel1, types.SimpleNamespace(index=0))
    assert sel2 == ["110:00.0", "32:00.0"] and "2 球" in html2
    html3, sel3 = app._wall_toggle(order, sel2, types.SimpleNamespace(index=1))
    assert sel3 == ["32:00.0"]                        # 再点同一球=取消
    _, sel4 = app._wall_toggle(order, sel3, types.SimpleNamespace(index=99))
    assert sel4 == sel3                               # 越界点击不炸不变
    assert "尚未点选" in app._wall_sel_html([])


def test_wall_toggle_状态污染成字符串不散成字符(tmp_path, monkeypatch):
    """布线事故复盘（49a2817 后续修复）：select 输出顺序接反过，State
    里进过 HTML 字符串——list('...') 会把整段 HTML 散成单字符 key 毒化
    归人。State 从浏览器裸往返，这里必须是类型防御点。"""
    import types
    app = _wall_fixture(tmp_path, monkeypatch)
    order = ["32:00.0", "110:00.0"]
    _, sel = app._wall_toggle(order, "<div>已选 1 球：32:00.0</div>",
                              types.SimpleNamespace(index=1))
    assert sel == ["110:00.0"]                        # 污染旧值整体丢弃


# ---------- 勾选=核对标记，与归人完全解耦（2026-09-06 用户定案） ----------

def _rows_fixture(monkeypatch, tmp_path):
    from bbh import app
    monkeypatch.setattr(app, "_work_dir_for", lambda vp: str(tmp_path))
    monkeypatch.setattr(app, "_raw_time_keys",
                        lambda vp, rows: [r[1] for r in rows])
    return app


def test_assign_person_勾选是核对标记归人不并不清不落盘(tmp_path, monkeypatch):
    """用户定案：剪辑台勾选=「人工核对确实是进球」，判断是否进球和出手人
    无关——归人有调整时勾选也不该清空。归人不清 r[0]、不写 selection.json，
    续归别人靠"墙上点选是唯一来源"防抢（见并入测试），不再靠清勾选。"""
    app = _rows_fixture(monkeypatch, tmp_path)
    rows0 = [[1, "01:00.0", "high", "0.9", 5, 2, ""],
             [1, "01:02.0", "high", "0.9", 6, 2, ""],
             [0, "05:00.0", "high", "0.9", 7, 2, ""]]
    msg, rows2, *_ = app.assign_person(
        "v.mp4", rows0, "老王", extra_keys=["01:00.0", "01:02.0"])
    assert "2 球" in msg
    assert rows2[0][0] == 1 and rows2[1][0] == 1      # 勾选原样保留
    assert not (tmp_path / "selection.json").exists()  # 不碰勾选持久化
    # 续归另一人（勾选残留 3 个核对标记）只带走墙上点选的球
    app.assign_person("v.mp4", rows2, "老陈", extra_keys=["05:00.0"])
    assert manual.load(str(tmp_path)) == {
        "老王": ["01:00.0", "01:02.0"], "老陈": ["05:00.0"]}


def test_unassign_person_移出不动勾选(tmp_path, monkeypatch):
    """移出同款：核对勾选与归人两条数据各管各的——勾选不动
    （2026-10-11 起标签**会**跟着清，见下一条测试）。"""
    app = _rows_fixture(monkeypatch, tmp_path)
    manual.assign(str(tmp_path), "老王", ["01:00.0"])
    rows = [[1, "01:00.0", "high", "0.9", 5, 2, "老王"]]
    msg, rows2, *_ = app.unassign_person("v.mp4", rows,
                                         extra_keys=["01:00.0"])
    assert "移出" in msg
    assert rows2[0][0] == 1
    assert not (tmp_path / "selection.json").exists()


def test_unassign_person_同时清标签(tmp_path, monkeypatch):
    """2026-10-11 用户裁定「移出时同时清标签」：归人的两份账必须对称。
    assign_person 是双写（manual_groups + tag_assignments），unassign_person
    只移组不清标签的话，墙上显示「未归人」而剪辑台还贴着人名——而**按人集锦
    是按标签导出的**（app.py 里 highlight-<标签>.mp4 走 tag_assignments，
    不看 manual_groups）⇒ 移出去的球还会被收进那个人的集锦里，两本账各说
    各话。只清点选的球，同表其余球的标签不动；勾选（核对标记）照旧不动。"""
    app = _rows_fixture(monkeypatch, tmp_path)
    manual.assign(str(tmp_path), "老王", ["01:00.0", "05:00.0"])
    rows = [[1, "01:00.0", "high", "0.9", 5, 2, "老王"],
            [0, "05:00.0", "high", "0.9", 7, 2, "老王"]]
    msg, rows2, *_ = app.unassign_person("v.mp4", rows,
                                         extra_keys=["01:00.0"])
    assert "移出" in msg
    assert manual.load(str(tmp_path)) == {"老王": ["05:00.0"]}   # 只移点选的
    assert rows2[0][6] == ""                                     # 标签一并清
    assert rows2[1][6] == "老王"                                 # 没点选的不动
    assert rows2[0][0] == 1 and rows2[1][0] == 0                 # 勾选不动
    # 真落盘（不是只改内存里那行）：tag_assignments.json 里该键已消失
    assert json.loads((tmp_path / "tag_assignments.json").read_text(
        encoding="utf-8")) == {"05:00.0": "老王"}


# ---------- 已归人调整墙（2026-09-06：结果模块二次多选改判/移出） ----------

def test_assigned_wall_payload_已归人调整墙(tmp_path, monkeypatch):
    """问题 3：已归人的球也要能多选改判/移出——墙只收已归人球，
    人名序+组内解析时刻序，caption="人名·key"（key 仍是第三位恒干净，
    spot=bad 追加 ⚠ 同未归人墙）。"""
    app = _wall_fixture(tmp_path, monkeypatch)
    manual.assign(str(tmp_path), "甲", ["32:00.0", "07:00.0"])
    manual.assign(str(tmp_path), "乙", ["110:00.0"])
    import json as _json
    p = tmp_path / "shooters.json"
    data = _json.loads(p.read_text(encoding="utf-8"))
    data["traces"]["32:00.0"]["spot"] = "bad"          # 已归人也有定位失效球
    p.write_text(_json.dumps(data), encoding="utf-8")
    got = app.assigned_wall_payload("v.mp4")
    assert [(c, k) for _, c, k in got] == [
        ("乙·110:00.0", "110:00.0"),                   # 人名=码点序（乙<甲，
        ("甲·07:00.0", "07:00.0"),                     # 与归人面板 sorted 同款）
        ("甲·32:00.0·⚠定位失效", "32:00.0")]           # 组内解析时刻序
    assert all(str(x).startswith(str(tmp_path)) for x, _, _ in got)


# ---------- 表格改标签 = 自动改判（2026-09-06：标签↔归人互通） ----------

def test_表格改标签自动改判(tmp_path, monkeypatch):
    """已归人的球在表格改人名标签 = 改判：manual_groups 跟着搬到新人
    （自动移出旧人）。两处存储不再各说各话。"""
    app = _rows_fixture(monkeypatch, tmp_path)
    manual.assign(str(tmp_path), "老王", ["01:00.0"])
    rows_old = [[1, "01:00.0", "high", "0.9", 5, 2, "老王"],
                [0, "05:00.0", "high", "0.9", 7, 2, ""]]
    proj = [[1, "01:00.0", "🟢", "老陈"],           # 标签 老王 → 老陈
            [0, "05:00.0", "🟢", ""]]
    synced = app.on_table_edit("v.mp4", rows_old, proj)
    assert synced[0][6] == "老陈"                     # 标签照常写回
    assert manual.load(str(tmp_path)) == {"老陈": ["01:00.0"]}  # 改判


def test_表格清空标签移出归人(tmp_path, monkeypatch):
    """已归人的球清空标签 = 移出归人（球回未归人墙）。"""
    app = _rows_fixture(monkeypatch, tmp_path)
    manual.assign(str(tmp_path), "老王", ["01:00.0", "05:00.0"])
    rows_old = [[1, "01:00.0", "high", "0.9", 5, 2, "老王"],
                [0, "05:00.0", "high", "0.9", 7, 2, "老王"]]
    proj = [[1, "01:00.0", "🟢", ""],                # 清标签
            [0, "05:00.0", "🟢", "老王"]]
    app.on_table_edit("v.mp4", rows_old, proj)
    assert manual.load(str(tmp_path)) == {"老王": ["05:00.0"]}


def test_未归人球打标签即归人(tmp_path, monkeypatch):
    """2026-10-11 用户裁定（「剪辑台归人打标签后，出手人墙并未同步」）：
    未归人的球打人名标签 = 直接建组归人，墙切页即见——09-06 版「标签只在
    球已归人时反向改判」的单向设计被推翻（归人标签库全是人名，打标即认定
    该人进球，没有第二个语义）。"""
    app = _rows_fixture(monkeypatch, tmp_path)
    rows_old = [[0, "05:00.0", "high", "0.9", 7, 2, ""]]
    proj = [[0, "05:00.0", "🟢", "老陈"]]
    app.on_table_edit("v.mp4", rows_old, proj)
    assert manual.load(str(tmp_path)) == {"老陈": ["05:00.0"]}


def test_标签下拉改判同款(tmp_path, monkeypatch):
    """set_row_tag（点行后标签下拉）与表格手输标签同一改判语义。"""
    app = _rows_fixture(monkeypatch, tmp_path)
    manual.assign(str(tmp_path), "老王", ["01:00.0"])
    rows = [[1, "01:00.0", "high", "0.9", 5, 2, "老王"]]
    app.set_row_tag("v.mp4", rows, 0, "老陈")
    assert manual.load(str(tmp_path)) == {"老陈": ["01:00.0"]}


# ---------- 两页人名下拉对齐（2026-09-06：剪辑台没有归人新名「年轻人」，
# 顺序也不同——出手人侧是 归人名∪标签库 码点序，剪辑台是纯标签库文件序） ----------

def test_tag_choices_归人名并入且码点序(tmp_path, monkeypatch):
    """_tag_choices = 人名唯一源：标签库 ∪ 已归人人名，sorted 码点序——
    归人时输入的新名（没进全局标签库）也要在剪辑台能选到。"""
    app = _wall_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(app, "TAGS_FILE", str(tmp_path / "tags.json"))
    app._save_tags(["老王", "阿林"])
    manual.assign(str(tmp_path), "年轻人", ["07:00.0"])   # 只进 manual_groups
    assert app._tag_choices("v.mp4") == sorted(["老王", "阿林", "年轻人"])


def test_render_manual_payload_人名choices同源(tmp_path, monkeypatch):
    """出手人侧 choices 走 _tag_choices 同一实现（防两处再漂移）。"""
    app = _wall_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(app, "TAGS_FILE", str(tmp_path / "tags.json"))
    app._save_tags(["老王"])
    manual.assign(str(tmp_path), "年轻人", ["07:00.0"])
    _, choices = app.render_manual_payload("v.mp4", [])
    assert choices == sorted(["老王", "年轻人"])


def test_preview_row_click_下拉choices含归人名(tmp_path, monkeypatch):
    """剪辑台痛点路径：点行回显标签时下拉 choices 也要刷新为 union 款——
    出手人 tab 归过的新名，切回剪辑台点行即可选。"""
    from bbh import _gr_compat as gr
    app = _wall_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(app, "TAGS_FILE", str(tmp_path / "tags.json"))
    app._save_tags(["老王"])
    manual.assign(str(tmp_path), "年轻人", ["07:00.0"])
    rows = [[1, "07:00.0", "high", "0.9", 15, 5, ""]]
    monkeypatch.setattr(app, "_preview_and_reason",
                        lambda vp, r, i, b, a: ("<video", ""))  # 无真视频
    evt = gr.SelectData(None, {"index": [0], "value": None})
    out = app.preview_row_click("v.mp4", rows, evt)
    assert out[4]["choices"] == sorted(["老王", "年轻人"])
    assert out[4]["value"] is None                      # 该行无标签回显空
