# -*- coding: utf-8 -*-
r"""阶段 0b：验证归因结论（只读）。

验三件事：
  1) 77 条网分差是否**全部**是 review_reason=="b2"（B2 注入行 conf 不含网分，
     net_motion.net_term 对 b2 返回 0；replay_l0.py 漏了这个例外）
  2) 全库是否存在 net_post 落在 5.0 边界（两位小数存成 5.00，实际 4.995）
     造成阈值翻转 —— 若有，会和 b2 混在一起
  3) 加上 b2 例外后，残差是否只剩「三位小数边界」那 76 条；
     并检查这些行是否影响聚合指标（能否复现 620/193/166/0）
"""
import json
import os
import sys
from collections import Counter

ROOT = r"D:\claude code\视频剪辑YOLO"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from attr_l0 import (VIDEOS, OLD, NEW, load_kept, side, pos, net_term,  # noqa
                     HIGH_CONF, MIN_CONF, FULL, SHORT)

import eval_sel as ESEL                                                  # noqa: E402


def replay(e, b2_fix=True):
    g = round(0.3 + side(e["above_n"], e["above_span"])
              + side(e["below_n"], e["below_span"]) + pos(e["cross_off"]), 2)
    nt = net_term(e)
    if b2_fix and e.get("review_reason") == "b2":
        nt = 0.0
    low = e.get("low_reason")
    if low and "证据点太少" in str(low) and g >= MIN_CONF:
        low = None
    conf = round(g + nt, 2)
    if low is not None:
        st = "low"
    elif e.get("review_reason") is not None or e.get("pop_back") is not None:
        st = "review"
    else:
        st = "high" if conf >= HIGH_CONF else "review" if conf >= MIN_CONF else "low"
    return conf, st


def main():
    print("=" * 70)
    print("【1】Δ∈{±0.30, ±0.26} 的行是否全部 b2")
    bad = []
    for v in VIDEOS:
        for e in load_kept(v):
            conf, _ = replay(e, b2_fix=False)
            d = round(e["confidence"] - conf, 4)
            if abs(abs(d) - 0.30) < 1e-9 or abs(abs(d) - 0.26) < 1e-9:
                bad.append((v, e["t"], d, e.get("review_reason")))
    c = Counter(str(r[3]) for r in bad)
    print(f"  共 {len(bad)} 条，review_reason 分布：{dict(c)}")

    print("\n【2】net_post 阈值边界行（全库）")
    n_exact = n_near = 0
    for v in VIDEOS:
        for e in load_kept(v):
            p = e.get("net_post")
            if p is None:
                continue
            if p == 5.0:
                n_exact += 1
            elif abs(p - 5.0) <= 0.005:
                n_near += 1
    print(f"  net_post == 5.0 的行 {n_exact} 条；5.0±0.005 内非等值 {n_near} 条")

    print("\n【3】加 b2 例外后残差")
    resid = []
    for v in VIDEOS:
        for e in load_kept(v):
            conf, st = replay(e, True)
            d = round(e["confidence"] - conf, 4)
            if abs(d) > 0.011 or e["status"] != st:
                resid.append((v, e, d, conf, st))
    print(f"  残差 {len(resid)} 条（原 153）")
    print("  Δ 分布：", dict(Counter(round(r[2], 2) for r in resid)))
    print("  状态翻转：", dict(Counter(f"{r[1]['status']}→{r[4]}" for r in resid)))

    # 这些残差里有没有命中真值的行 / 假绿行
    hit = 0
    green = 0
    for v, e, d, conf, st in resid:
        goals, _ = ESEL.load_selection(v)
        if not goals:
            continue
        near = [g for g in goals if abs(float(g if not isinstance(g, dict)
                                           else g.get("t", 0)) - e["t"]) <= ESEL.SEL_TOL]
        if near:
            hit += 1
            if st in ("high", "review"):
                green += 1
    print(f"  其中与 selection 真球重合 {hit} 条")

    print("\n【4】用它复算基线（b2 例外已加）")
    per = {}
    for v in VIDEOS:
        events = load_kept(v)
        rows = [(e["t"], replay(e, True)[1]) for e in events]
        goals, _ = ESEL.load_selection(v)
        if not goals:
            continue
        per[v] = ESEL.sel_one(goals, rows, ESEL.SEL_TOL)
    for tag, vids in (("老六", OLD), ("新六", NEW), ("合计", VIDEOS)):
        a = ESEL.sel_agg(per, vids)
        print(f"  {tag}: 真值 {a['sel_n']} 自动档 {a['high_total']} 命中 "
              f"{a['auto_hit']} 假绿 {a['false_green']} 降级 {a['degraded']} "
              f"无行 {a['missing']} 查准 {a['precision']:.2%} "
              f"覆盖 {a['auto_cover']:.2%} 恒等式坏 {a['identity_bad']}")


if __name__ == "__main__":
    main()
