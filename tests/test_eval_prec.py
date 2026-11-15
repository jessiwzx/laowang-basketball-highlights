"""查准回归集 & 派生真值的口径保护。

2026-11-10 起 `truth/` 里有两种真值：
  · 原始标注（老七场 + 10-10_1）——truth 来自 HSV 检出表，异源 → 能算查全
  · 派生真值（selection）——truth ⊆ events → 漏检恒 0 → 只能算查准 / 覆盖率

这些测试守三件事：
1. 两种口径的聚合函数不被混用（`PREC_ONLY` 与 `VIDEOS` 永不重叠）；
2. `prec_one` 把「真球没进 high」拆成 review / low 两条可见去向，不漏算；
3. `derive_truth` 永远不覆盖原始标注，且对齐体检不过时不写文件。
"""
import json

import pytest

from tools import derive_truth as D
from tools import eval_lib as E


# ------------------------------------------------------------------ 口径隔离
def test_两种测试集不重叠():
    assert not (set(E.VIDEOS) & E.PREC_ONLY)
    assert set(E.VIDEOS_PREC) == E.PREC_ONLY


def test_派生场次的划分标为prec():
    for v in E.VIDEOS_PREC:
        assert E.SPLIT[v] == "prec"


# ------------------------------------------------------------------ prec 指标
def test_prec_one_把真球去向拆开():
    rows = [(100.0, "high"), (200.0, "high"), (300.0, "review"),
            (400.0, "low"), (500.0, "high")]
    goals = [100.0, 300.0, 400.0]
    m = E.prec_one(goals, rows, 0.2)
    assert m["high_total"] == 3
    assert m["auto_hit"] == 1        # 只有 100 落进 high
    assert m["false_green"] == 2     # 200 / 500 是 high 但用户没勾
    assert m["review_hit"] == 1      # 300 在 review 里捞回
    assert m["low_hit"] == 1         # 400 在 low 里捞回
    assert m["manual"] == 2          # 需要人工捞的真球
    assert m["miss"] == 0            # 派生真值下应恒 0
    assert m["precision"] == pytest.approx(1 / 3)
    assert m["auto_cover"] == pytest.approx(1 / 3)


def test_prec_agg_汇总与漏检自检():
    ok = E.prec_one([10.0], [(10.0, "high")], 0.2)
    a = E.prec_agg({"x": ok}, ["x"])
    assert a["goals"] == 1 and a["auto_hit"] == 1
    assert a["anchor_bad"] is False

    # 真值点认不回任何行 = 派生错位（selection 与 events 不是同一版）
    bad = E.prec_one([10.0], [], 0.2)
    b = E.prec_agg({"x": bad}, ["x"])
    assert b["anchor_bad"] is True


def test_prec_agg_缺场次跳过():
    m = E.prec_one([10.0], [(10.0, "high")], 0.2)
    a = E.prec_agg({"x": m}, ["x", "missing"])
    assert a["goals"] == 1


# ------------------------------------------------------------------ 时间解析
def test_parse_ts三种形态():
    assert D.parse_ts("02:56.5") == pytest.approx(176.5)
    assert D.parse_ts("1:02:03.4") == pytest.approx(3723.4)
    assert D.parse_ts(12.5) == pytest.approx(12.5)


def test_selection_goals兼容字符串与字典(tmp_path):
    p = tmp_path / "sel.json"
    p.write_text(json.dumps(["00:10.5", {"t": "01:00.0"},
                             "00:10.5", "坏值"]), encoding="utf-8")
    goals, bad = D.selection_goals(str(p))
    assert goals == [10.5, 60.0]     # 去重 + 排序
    assert bad == 1


def test_anchor_check比例():
    hit, rate = D.anchor_check([10.0, 60.0], [10.05, 60.5])
    assert hit == 1
    assert rate == pytest.approx(0.5)


# ------------------------------------------------------------------ 派生写入
def _make_case(tmp_path, monkeypatch, goals=("00:10.0",), events=(10.0,)):
    work = tmp_path / "work"
    truth = tmp_path / "truth"
    d = work / "2026-01-01_1"
    d.mkdir(parents=True)
    (d / "selection.json").write_text(json.dumps(list(goals)),
                                     encoding="utf-8")
    (d / "events.json").write_text(json.dumps({
        "video": "src.mp4", "duration": 100.0, "logic_ver": 22,
        "calib_key": [[0, 0, 10, 10]],
        "events": [{"t": t, "status": "high"} for t in events]}),
        encoding="utf-8")
    truth.mkdir()
    monkeypatch.setattr(D, "WORK", str(work))
    monkeypatch.setattr(D, "TRUTH", str(truth))
    return truth / "2026-01-01_1.json"


def test_派生真值写入字段完整(tmp_path, monkeypatch):
    out = _make_case(tmp_path, monkeypatch)
    r = D.build("2026-01-01_1")
    assert r["ok"] and r["wrote"]
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["truth_kind"] == "selection"
    assert doc["complete"] is True
    assert doc["tolerance"] == 0.2
    assert doc["goals"] == [10.0]
    assert doc["anchor_check"]["rate"] == pytest.approx(1.0)
    assert "查全" in doc["note"]     # 口径警告必须写进产物


def test_已存在派生真值需force(tmp_path, monkeypatch):
    out = _make_case(tmp_path, monkeypatch)
    assert D.build("2026-01-01_1")["wrote"]
    again = D.build("2026-01-01_1")
    assert again["ok"] is False and again["skip"]
    forced = D.build("2026-01-01_1", force=True)
    assert forced["ok"] and forced["wrote"]
    assert out.exists()


def test_原始标注永远拒绝覆盖(tmp_path, monkeypatch):
    out = _make_case(tmp_path, monkeypatch)
    out.write_text(json.dumps({"video": "2026-01-01_1", "complete": True,
                               "goals": [10.0]}), encoding="utf-8")
    before = out.read_text(encoding="utf-8")
    r = D.build("2026-01-01_1", force=True)   # 即使 --force 也不行
    assert r["ok"] is False and "原始标注" in r["err"]
    assert out.read_text(encoding="utf-8") == before


def test_对齐体检不过不写文件(tmp_path, monkeypatch):
    # selection 说 99s，events 只有 10s → 认回率 0，说明不是同一版
    out = _make_case(tmp_path, monkeypatch, goals=("01:39.0",), events=(10.0,))
    r = D.build("2026-01-01_1")
    assert r["ok"] is False and "对齐体检" in r["err"]
    assert not out.exists()


def test_dry_run不写文件(tmp_path, monkeypatch):
    out = _make_case(tmp_path, monkeypatch)
    r = D.build("2026-01-01_1", dry_run=True)
    assert r["ok"] and r["wrote"] is False
    assert not out.exists()


# ------------------------------------------------------------------ 补充真球
# 2026-11-11 用户裁定：v20 新增的 48 条 high 行直接认定为真球，存在
# `truth/extra_goals.json` 里由 load_truth 合并。守三件事：默认并入、
# 可关掉、补丁表缺失不能炸。
def _make_truth(tmp_path, video="2026-01-01_1", goals=(10.0, 20.0), extra=None):
    tdir = tmp_path / "truth"
    tdir.mkdir()
    (tdir / (video + ".json")).write_text(json.dumps(
        {"goals": list(goals), "complete": True, "tolerance": 0.2},
        ensure_ascii=False), encoding="utf-8")
    if extra is not None:
        (tdir / "extra_goals.json").write_text(json.dumps(
            {"source": "test", "goals": {video: list(extra)}},
            ensure_ascii=False), encoding="utf-8")
    return str(tmp_path)     # truth_root 语义是「项目根」，truth/ 在其下


def test_补充真球默认并入(tmp_path):
    root = _make_truth(tmp_path, extra=[30.0, 40.0])
    goals, tol = E.load_truth("2026-01-01_1", truth_root=root)
    assert goals == [10.0, 20.0, 30.0, 40.0]
    assert tol == 0.2


def test_补充真球可关闭(tmp_path):
    root = _make_truth(tmp_path, extra=[30.0])
    goals, _tol = E.load_truth("2026-01-01_1", truth_root=root, with_extra=False)
    assert goals == [10.0, 20.0]


def test_无补丁表时照常读原始真值(tmp_path):
    root = _make_truth(tmp_path)                 # 不写 extra_goals.json
    goals, _tol = E.load_truth("2026-01-01_1", truth_root=root)
    assert goals == [10.0, 20.0]
