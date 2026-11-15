# -*- coding: utf-8 -*-
"""用户标注口径（第 5 套）的口径保护。

2026-11-14 起新增第 5 套：真值改用剪辑台 `selection.json`（用户逐条勾过的球单），
不再用 truth 行锚定表。理由是两套标签只重叠 17%——查准一直在衡量「与 HSV 一致」，
不是「用户满意不满意」。

这些测试守四件事：
1. 场次清单与既有口径的边界（哪些场允许重叠、哪些不允许混算）；
2. `sel_one` 的四个去向互斥且穷尽（恒等式必须平）；
3. 1:1 配对语义（同一行不能被两颗球认领、同距优先 high）；
4. 容差与聚合语义（比率用合计重算，不平均各场比率）。
"""
import json
import os

import pytest

from tools import eval_lib as E
from tools import eval_sel as S


# ------------------------------------------------------------------ 口径边界
def test_场次清单由老六加新六构成():
    assert S.VIDEOS_SEL == S.VIDEOS_SEL_OLD + S.VIDEOS_SEL_NEW
    assert len(S.VIDEOS_SEL_OLD) == 6
    assert len(S.VIDEOS_SEL_NEW) == 6
    assert set(S.VIDEOS_SEL) == S.SEL_ONLY


def test_新六场与查准回归集同源():
    # 两套都用 selection 派生，场次必须一致（否则口径漂移没人发现）
    assert S.VIDEOS_SEL_NEW == E.VIDEOS_PREC


def test_崩场不纳入():
    assert "2026-09-19_1" not in S.SEL_ONLY


def test_老组新组划分():
    for v in S.VIDEOS_SEL_OLD:
        assert S.group_of(v) == "old"
    for v in S.VIDEOS_SEL_NEW:
        assert S.group_of(v) == "new"


def test_分组覆盖全部场次且不重不漏():
    flat = [v for _lbl, vids in S.groups()[:-1] for v in vids]
    assert sorted(flat) == sorted(S.VIDEOS_SEL)
    assert S.groups()[-1][1] == S.VIDEOS_SEL


# ------------------------------------------------------------------ sel_one
def test_四个去向互斥且穷尽():
    rows = [(100.0, "high"), (200.0, "review"), (300.0, "low"),
            (400.0, "high"), (500.0, "high")]
    goals = [100.0, 200.0, 900.0]          # 命中 / 降级 / 无行
    m = S.sel_one(goals, rows)
    assert m["auto_hit"] == 1
    assert m["degraded"] == 1
    assert m["missing"] == 1
    assert m["auto_hit"] + m["degraded"] + m["missing"] == m["sel_n"] == 3


def test_假绿是自动档里没被选中的():
    rows = [(100.0, "high"), (200.0, "high"), (300.0, "high")]
    m = S.sel_one([100.0], rows)
    assert m["high_total"] == 3
    assert m["false_green"] == 2
    assert m["auto_hit"] + m["false_green"] == m["high_total"]


def test_查准与覆盖率分母不同():
    rows = [(100.0, "high"), (200.0, "high")]
    m = S.sel_one([100.0, 900.0], rows)
    assert m["precision"] == pytest.approx(0.5)       # 1/2 自动档
    assert m["auto_cover"] == pytest.approx(0.5)      # 1/2 真球


def test_空输入不炸且不除零():
    m = S.sel_one([], [])
    assert m["precision"] == 0.0 and m["auto_cover"] == 0.0
    assert m["sel_n"] == 0


# ------------------------------------------------------------------ 配对语义
def test_同一行不被两颗球认领():
    # 两个 goal 都在 0.2 内指向同一 high 行 → 只有一个算命中
    rows = [(100.0, "high")]
    m = S.sel_one([99.9, 100.1], rows)
    assert m["auto_hit"] == 1
    assert m["auto_hit"] + m["degraded"] + m["missing"] == 2


def test_同距时high优先():
    # goal 到 high 与 review 等距 → 应认领 high（RANK 升序）
    rows = [(100.0, "review"), (100.4, "high")]
    used_g, used_r = S.sel_match([100.2], rows, tol=0.2)
    assert list(used_r) == [1]                 # high 那一行


def test_距离升序全局最近先认领():
    # 球 A 离 review 更近，球 B 只能吃 high → 不应让 A 抢 high
    rows = [(100.0, "high"), (100.15, "review")]
    goals = [100.16, 100.02]
    m = S.sel_one(goals, rows, tol=0.3)
    assert m["auto_hit"] == 1 and m["degraded"] == 1


def test_容差边界():
    rows = [(100.0, "high")]
    assert S.sel_one([100.2], rows, tol=0.2)["auto_hit"] == 1     # 边界含
    assert S.sel_one([100.21], rows, tol=0.2)["auto_hit"] == 0    # 超出不算


def test_默认容差是02():
    assert S.SEL_TOL == 0.2


# ------------------------------------------------------------------ 聚合语义
def test_聚合用合计重算比率():
    per = {
        "a": {"sel_n": 10, "rows": 12, "high_total": 12, "auto_hit": 10,
              "false_green": 2, "degraded": 0, "missing": 0},
        "b": {"sel_n": 90, "rows": 100, "high_total": 100, "auto_hit": 45,
              "false_green": 55, "degraded": 45, "missing": 0},
    }
    a = S.sel_agg(per, ["a", "b"])
    assert a["auto_hit"] == 55 and a["high_total"] == 112
    # 合计查准 = 55/112，绝不能是 (0.833+0.45)/2
    assert a["precision"] == pytest.approx(55 / 112)
    assert a["precision"] != pytest.approx((10 / 12 + 45 / 100) / 2)
    assert a["manual"] == a["degraded"] + a["missing"]
    assert a["identity_bad"] is False


def test_聚合缺场次跳过():
    per = {"a": {"sel_n": 1, "rows": 1, "high_total": 1, "auto_hit": 1,
                 "false_green": 0, "degraded": 0, "missing": 0}}
    a = S.sel_agg(per, ["a", "不存在的场"])
    assert a["sel_n"] == 1 and a["identity_bad"] is False


def test_恒等式破坏能被自检抓到():
    per = {"a": {"sel_n": 5, "rows": 1, "high_total": 1, "auto_hit": 1,
                 "false_green": 0, "degraded": 0, "missing": 0}}   # 少算 4 条
    assert S.sel_agg(per, ["a"])["identity_bad"] is True


# ------------------------------------------------------------------ 读 selection
def test_读不到selection返回空(tmp_path):
    goals, src = S.load_selection("2026-01-01_1", [str(tmp_path)])
    assert goals == [] and src is None


def test_读selection兼容两种格式(tmp_path):
    d = tmp_path / "2026-01-01_1"
    d.mkdir()
    (d / "selection.json").write_text(
        json.dumps([12.5, {"t": "0:20.0"}], ensure_ascii=False), encoding="utf-8")
    goals, src = S.load_selection("2026-01-01_1", [str(tmp_path)])
    assert goals == [12.5, 20.0]
    assert src and src.endswith("selection.json")


def test_多根按序找(tmp_path):
    r1, r2 = tmp_path / "r1", tmp_path / "r2"
    (r2 / "2026-01-01_1").mkdir(parents=True)
    (r2 / "2026-01-01_1" / "selection.json").write_text("[1.0]")
    goals, src = S.load_selection("2026-01-01_1", [str(r1), str(r2)])
    assert goals == [1.0] and str(r2) in src


def test_本仓work在候选根里():
    assert os.path.join(S.PROJECT_ROOT, "work") in S.sel_roots()


# ------------------------------------------------------------------ 严格口径
class Test严格口径:
    """2026-11-15 用户确认：没勾 ≠ 不是进球。

    「两场比赛之间大家随便投篮的球」本来是进球，用户不勾它、也不标 not_goal，
    没有任何标注价值，应从测试集删去。所以：
        明确误判 = 没勾 且 标了 not_goal
        间隙球   = 没勾 且 没标（或标 goal）
    """

    rows = [(100.0, "high"), (200.0, "high"), (300.0, "high"),
            (400.0, "review")]

    def test_未勾且标not_goal算明确误判(self):
        vd = [(200.0, "not_goal")]
        m = S.sel_one_strict([100.0], self.rows, verdicts=vd)
        assert m["auto_hit"] == 1
        assert m["false_ng"] == 1          # 200 判错
        assert m["filler"] == 1            # 300 无标 → 间隙球
        assert m["high_eff"] == 2

    def test_未勾且无标记算间隙球剔除(self):
        m = S.sel_one_strict([100.0], self.rows, verdicts=[])
        assert m["filler"] == 2            # 200 / 300 全剔
        assert m["false_ng"] == 0
        assert m["high_eff"] == 1
        assert m["precision_strict"] == pytest.approx(1.0)

    def test_标goal的未勾行也剔除(self):
        # 「是进球但我不剪」→ 同样是间隙球，不是误判
        vd = [(200.0, "goal"), (300.0, "goal")]
        m = S.sel_one_strict([100.0], self.rows, verdicts=vd)
        assert m["filler"] == 2 and m["false_ng"] == 0

    def test_没有feedback时退回勾选口径(self):
        m = S.sel_one_strict([100.0], self.rows, verdicts=None)
        assert m["has_fb"] is False
        assert m["filler"] == 0
        assert m["high_eff"] == m["high_total"] == 3
        assert m["false_ng"] == m["false_green"] == 2
        assert m["precision_strict"] == pytest.approx(m["precision"])

    def test_有feedback但一条都没标(self):
        # 空 list ≠ None：说明文件存在、只是没标 → 全部剔除（不是退回勾选口径）
        m = S.sel_one_strict([100.0], self.rows, verdicts=[])
        assert m["has_fb"] is True
        assert m["filler"] == 2

    def test_只对high行剔除低档不受影响(self):
        vd = [(400.0, "not_goal")]         # review 行不参与
        m = S.sel_one_strict([], self.rows, verdicts=vd)
        assert m["filler"] == 3            # 三条 high 全剔
        assert m["false_ng"] == 0
        assert m["degraded"] == 0          # review 没人勾

    def test_恒等式(self):
        vd = [(200.0, "not_goal"), (300.0, "goal")]
        m = S.sel_one_strict([100.0], self.rows, verdicts=vd)
        assert m["high_eff"] == m["auto_hit"] + m["false_ng"]
        assert m["high_total"] == m["high_eff"] + m["filler"]

    def test_匹配取最近的一条(self):
        # 同一条行在 0.2s 内有两个标记（不可能出现，但要取最近的）
        vd = [(199.85, "goal"), (200.1, "not_goal")]   # 距 200.0 分别 0.15 / 0.10
        m = S.sel_one_strict([100.0], self.rows, verdicts=vd)
        assert m["false_ng"] == 1          # 取更近的 not_goal

    def test_容差外的标记不算(self):
        vd = [(200.5, "not_goal")]         # 差 0.5s > 0.2
        m = S.sel_one_strict([100.0], self.rows, verdicts=vd)
        assert m["filler"] == 2 and m["false_ng"] == 0

    def test_聚合与自检(self):
        per = {
            "a": S.sel_one_strict([100.0], self.rows, verdicts=[(200.0, "not_goal")]),
            "b": S.sel_one_strict([100.0], self.rows, verdicts=[]),
        }
        a = S.sel_agg_strict(per, ["a", "b"])
        assert a["high_total"] == 6
        assert a["filler"] == 3            # a 1 + b 2
        assert a["false_ng"] == 1
        assert a["high_eff"] == 3
        assert a["precision_strict"] == pytest.approx(2 / 3)
        assert a["strict_identity_bad"] is False
        assert a["no_fb"] == []

    def test_聚合标出没有feedback的场(self):
        per = {"a": S.sel_one_strict([100.0], self.rows, verdicts=None)}
        a = S.sel_agg_strict(per, ["a"])
        assert a["no_fb"] == ["a"]


def test_读verdicts兼容缺字段与坏值(tmp_path):
    d = tmp_path / "2026-01-01_1"
    d.mkdir()
    (d / "feedback.json").write_text(json.dumps({
        "verdicts": [{"t": 1.5, "verdict": "not_goal"},
                     {"t": 3.0, "verdict": "goal"},
                     {"t": "x", "verdict": "not_goal"},   # 坏时刻 → 跳过
                     {"verdict": "not_goal"}],            # 缺时刻 → 跳过
        "misses": []}, ensure_ascii=False))
    vd, p = S.load_verdicts("2026-01-01_1", [str(tmp_path)])
    assert vd == [(1.5, "not_goal"), (3.0, "goal")]
    assert p and p.endswith("feedback.json")


def test_没有feedback文件返回空(tmp_path):
    vd, p = S.load_verdicts("2026-01-01_1", [str(tmp_path)])
    assert vd == [] and p is None
