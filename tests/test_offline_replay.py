# -*- coding: utf-8 -*-
"""L0 离线重算器的口径保护。

2026-11-14 建：`tools/offline_replay.py` 用 events.json 里已有的评分自变量
重算 conf/status，用来扫描「打分形状 / 阈值 / 网分权重 / 基于时刻的规则」。
它必须满足两条硬约束，否则任何变体结论都是假象：

1. **复现 100%**：用当前常量重算，conf 与 status 必须与文件逐条一致。
   阶段 0 归因（诊断/2026-11-14-优化候选/阶段0归因结论.md）查实，历史 153 条
   不一致全部来自两处：B2 注入行 conf 不含网分、以及 span/cross_off 存 3 位
   小数在档位边界上丢方向（可用存储的 confidence 反解回档位）。
2. **口径不漂移**：12 场 3773 条、三组基线（用户口径新六 / 官方老六 /
   A 规则对照）必须逐位对上。

打分公式（bbh/goal_decider.py + net_motion.py）：
    geom = 0.3 + 侧分(上) + 侧分(下) + 位置分
    侧分：n≥2 且 span≥0.2 → 0.20；n≥2 → 0.14；n==1 → 0.05；n==0 → 0
    位置分：|off| ≤0.1/0.2/0.3/0.4/0.5 → 0.20/0.17/0.14/0.10/0.06，否则 0
    conf = round(geom, 2) + 网分（静止 −0.30 / 非局部 −0.26 / 其他 0）
"""
import os

import pytest

from tools import offline_replay as R


def _ev(**kw):
    """一条干净的真球形态：0.3+0.20+0.20+0.20 = 0.90。"""
    e = {
        "t": 100.0, "confidence": 0.90, "status": "high",
        "low_reason": None, "cross_off": 0.05,
        "above_n": 5, "below_n": 5,
        "above_span": 0.40, "below_span": 0.40,
        "pop_back": None, "review_reason": None,
        "net_post": 12.0, "net_local": True,
    }
    e.update(kw)
    return e


# ------------------------------------------------------------------ 反解
def test_无歧义行反解后原样复现():
    ne = R.resolve(_ev())
    assert ne is not None
    assert (ne["above_span"], ne["below_span"]) == (0.40, 0.40)
    assert ne["cross_off"] == 0.05
    assert R.score(ne, R.var()) == (0.90, "high")


def test_侧分跨度边界按存储conf反解到短档():
    # 真实 span 0.1998 被存成 0.200：存储 conf 已经记录了「走了短档」
    # （v23 起短档 = 0.17；此用例按当前 BASE 取值构造，旧口径请配 --short 0.14）
    e = _ev(above_span=0.200, below_span=0.40,
            confidence=round(0.3 + R.BASE["short"] + 0.20 + 0.20, 2))
    ne = R.resolve(e)
    assert ne["above_span"] < R_CONT()          # 被挪到档位内侧
    assert ne["below_span"] == 0.40
    assert R.score(ne, R.var()) == (e["confidence"], "high")


def test_侧分边界反解到满档时同样正确():
    e = _ev(above_span=0.200, confidence=0.90)   # 真实 ≥0.2
    ne = R.resolve(e)
    assert ne["above_span"] > R_CONT()
    assert R.score(ne, R.var()) == (0.90, "high")


def test_位置分档位边界按存储conf反解():
    # 真实 |off| 0.2004 被存成 0.200 → 实际走的是 0.3 档（0.14）而非 0.2 档（0.17）
    e = _ev(cross_off=0.200, confidence=0.84)
    ne = R.resolve(e)
    assert abs(ne["cross_off"]) > 0.2
    assert R.score(ne, R.var()) == (0.84, "high")


def test_b2注入行网分不参与打分():
    # b2 行 conf 不含网分（net_motion.net_term 返 0），漏了会凭空多扣 0.30
    e = _ev(review_reason="b2", above_n=0, above_span=0.0,
            net_post=2.0, net_local=False, confidence=0.70, status="review")
    ne = R.resolve(e)
    assert R.score(ne, R.var()) == (0.70, "review")


def test_网静止扣满额():
    e = _ev(cross_off=0.25, net_post=2.0, net_local=False, confidence=0.54)
    ne = R.resolve(e)
    assert R.score(ne, R.var()) == (0.54, "review")      # 0.84 − 0.30


def test_网非局部扣0_26():
    e = _ev(cross_off=0.25, net_post=12.0, net_local=False, confidence=0.58)
    ne = R.resolve(e)
    assert R.score(ne, R.var()) == (0.58, "review")      # 0.84 − 0.26


def test_网测不到时不扣分():
    e = _ev(net_post=None, net_local=None, confidence=0.90)
    ne = R.resolve(e)
    assert R.score(ne, R.var()) == (0.90, "high")


def test_低置信原因可被新的min_conf解除():
    e = _ev(above_n=1, below_n=1, above_span=0.0, below_span=0.0,
            cross_off=0.45, confidence=0.46, status="low",
            low_reason="证据点太少（置信度不足 0.50）")
    ne = R.resolve(e)
    assert R.score(ne, R.var()) == (0.46, "low")
    # 门槛降到 0.40 → 「证据点太少」不再成立，按净值重分箱
    assert R.score(ne, R.var(min_conf=0.40))[1] == "review"


def test_硬门review不被阈值拉高():
    e = _ev(review_reason="entity", confidence=0.90, status="review")
    ne = R.resolve(e)
    assert R.score(ne, R.var()) == (0.90, "review")


# ------------------------------------------------------------------ A 规则
def test_A规则按原始序列间隔降档():
    p = R.var(gap=3.0, gap_src="raw")
    rows = [(10.0, "high"), (11.5, "high"), (20.0, "high")]
    gaps = {10.0: 1e9, 11.5: 1.5, 20.0: 8.5}
    out = R.apply_gap(rows, p, gaps)
    assert [s for _t, s in out] == ["high", "review", "high"]


def test_A规则关闭时原样返回():
    rows = [(10.0, "high"), (11.5, "high")]
    assert R.apply_gap(rows, R.var()) == rows


# ------------------------------------------------------------------ 口径常量
def R_CONT():
    from bbh.goal_decider import CONT_SPAN_SEC
    return CONT_SPAN_SEC


def test_口径场次清单不重复不遗漏():
    assert len(R.OLD) == 6 and len(R.NEW) == 6
    assert not (set(R.OLD) & set(R.NEW))
    assert R.ALL == R.OLD + R.NEW


def test_基线参数取自生产常量():
    from bbh.goal_decider import HIGH_CONF, MIN_CONF, CONT_SPAN_SEC
    from bbh.net_motion import NET_STATIC_TH
    b = R.var()
    assert b["high_conf"] == HIGH_CONF
    assert b["min_conf"] == MIN_CONF
    assert b["cont"] == CONT_SPAN_SEC
    assert b["net_th"] == NET_STATIC_TH
    assert b["gap"] == 0.0            # 默认不启 A 规则
    assert b["gap_src"] == "raw"      # 间隔量在未剔 deleted 的原始序列上（复刻历史口径）


@pytest.mark.skipif(
    not os.path.exists(os.path.join(R.LOCAL_WORK, R.NEW[0], "events.json")),
    reason="本机缺少新六场 events 产物")
def test_复现闸门通过():
    """全量闸门（3773 条）。数据不在本机时跳过。"""
    ck = R.check()
    assert ck["ok"], (f"conf 差 {ck['conf_bad']} / status 差 {ck['status_bad']} / "
                      f"反解失败 {ck['unrecoverable']}")
