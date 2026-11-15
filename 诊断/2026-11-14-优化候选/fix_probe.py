# -*- coding: utf-8 -*-
r"""阶段 0c：验证「反解档位 + 微调存储值」能否 100% 复现（只读，不动 replay_l0.py）。

思路：
  三位小数存储让 span / cross_off 在档位边界上丢失方向，但**存储的
  confidence 本身保留了方向**——geom = round(conf − net, 2) 是精确的
  （两项都是 2 位小数），而 geom = 0.3 + 侧分×2 + 位置分，分量全来自
  离散集合 → 可反解出真实档位。

  反解出档位后把存储值往档位内侧挪 0.0005（span 0.200→0.1995 表示真实
  <0.2），下游任何阈值比较都天然正确；只有阈值恰好落在 ±0.0005 内才会
  再歧义（可忽略，会单独计数）。

验收：3773 条 conf / status 全部一致 + 新六场聚合复现 620/193/166/0。
"""
import os
import sys
from collections import Counter
from itertools import product

ROOT = r"D:\claude code\视频剪辑YOLO"
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from attr_l0 import (VIDEOS, OLD, NEW, load_kept, net_term,        # noqa: E402
                     HIGH_CONF, MIN_CONF, CONT_SPAN_SEC, POS_TIERS)

import eval_sel as ESEL                                            # noqa: E402

POS_CANDS = [sc for _lim, sc in POS_TIERS] + [0.0]      # 0.20/0.17/0.14/0.10/0.06/0
TOL = 5e-4                                              # 三位小数半宽


def side_cands(n):
    if n == 0:
        return [0.0]
    if n == 1:
        return [0.05]
    return [0.14, 0.20]


def _naive_sides(e):
    def sd(n, s):
        return 0.20 if (n >= 2 and s >= CONT_SPAN_SEC) else \
               0.14 if n >= 2 else 0.05 if n == 1 else 0.0
    return sd(e["above_n"], e["above_span"]), sd(e["below_n"], e["below_span"])


def recover(e):
    """从存储 conf 反解几何分量。

    只对**真正有歧义**的维度（存储值距档位界 ≤TOL）枚举，其余维度由存储值
    锁定——否则 0.14+0.20 与 0.20+0.14 之类的和相同会造出伪解。
    → (sa, sb, sp, 候选数)
    """
    geom = round(e["confidence"] - (0.0 if e.get("review_reason") == "b2"
                                    else net_term(e)), 2)
    na, nb = _naive_sides(e)
    a = abs(e["cross_off"])
    amb_a = e["above_n"] >= 2 and abs(e["above_span"] - CONT_SPAN_SEC) <= TOL
    amb_b = e["below_n"] >= 2 and abs(e["below_span"] - CONT_SPAN_SEC) <= TOL
    amb_p = any(abs(a - lim) <= TOL for lim, _ in POS_TIERS)
    ca = side_cands(e["above_n"]) if amb_a else [na]
    cb = side_cands(e["below_n"]) if amb_b else [nb]
    cp = POS_CANDS if amb_p else [next((sc for lim, sc in POS_TIERS if a <= lim), 0.0)]
    hits = [(sa, sb, sp) for sa, sb, sp in product(ca, cb, cp)
            if abs(0.3 + sa + sb + sp - geom) < 1e-9]
    if not hits:
        return None
    hits.sort(key=lambda h: sum(abs(x - y) for x, y in zip(h, (na, nb, cp[0]))))
    return hits[0] + (len(hits),)


def nudge(e):
    """按反解档位把存储 span / cross_off 挪进档位内侧 → 新的事件副本。"""
    rec = recover(e)
    if rec is None:
        return None, 0
    sa, sb, sp, nm = rec
    out = dict(e)

    def fix_span(n, span, s):
        if n < 2 or abs(span - CONT_SPAN_SEC) > TOL:
            return span
        return CONT_SPAN_SEC + TOL if s >= 0.20 else CONT_SPAN_SEC - TOL

    out["above_span"] = fix_span(e["above_n"], e["above_span"], sa)
    out["below_span"] = fix_span(e["below_n"], e["below_span"], sb)
    a = abs(e["cross_off"])
    for lim, sc in POS_TIERS:
        if abs(a - lim) <= TOL:
            a = lim - TOL if sc == sp else lim + TOL
            break
    out["cross_off"] = a if e["cross_off"] >= 0 else -a
    return out, nm


def replay(e):
    def sd(n, s):
        return 0.20 if (n >= 2 and s >= CONT_SPAN_SEC) else \
               0.14 if n >= 2 else 0.05 if n == 1 else 0.0
    sp = next((sc for lim, sc in POS_TIERS if abs(e["cross_off"]) <= lim), 0.0)
    g = round(0.3 + sd(e["above_n"], e["above_span"])
              + sd(e["below_n"], e["below_span"]) + sp, 2)
    nt = net_term(e)
    if e.get("review_reason") == "b2":
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
    tot = bad_c = bad_s = unrec = multi = 0
    for v in VIDEOS:
        for e in load_kept(v):
            tot += 1
            ne, nm = nudge(e)
            if ne is None:
                unrec += 1
                continue
            if nm > 1:
                multi += 1
            conf, st = replay(ne)
            if abs(conf - e["confidence"]) > 1e-9:
                bad_c += 1
                if bad_c <= 8:
                    print(f"  conf 差 {v} t={e['t']:.2f} stored={e['confidence']} "
                          f"got={conf}")
            if st != e["status"]:
                bad_s += 1
                if bad_s <= 8:
                    print(f"  status 差 {v} t={e['t']:.2f} {e['status']}→{st}")
    print(f"\n总 {tot} 条：conf 不一致 {bad_c} / status 不一致 {bad_s} / "
          f"反解失败 {unrec} / 多解 {multi}")

    per = {}
    for v in VIDEOS:
        rows = []
        for e in load_kept(v):
            ne, _n = nudge(e)
            if ne is None:
                rows.append((e["t"], e["status"]))
                continue
            rows.append((e["t"], replay(ne)[1]))
        goals, _ = ESEL.load_selection(v)
        if not goals:
            continue
        per[v] = ESEL.sel_one(goals, rows, ESEL.SEL_TOL)
    for tag, vids in (("老六", OLD), ("新六", NEW)):
        if not any(v in per for v in vids):
            print(f"  {tag}: 无 selection（需 --sel-root）")
            continue
        a = ESEL.sel_agg(per, vids)
        print(f"  {tag}: 真值 {a['sel_n']} 自动档 {a['high_total']} 命中 {a['auto_hit']} "
              f"假绿 {a['false_green']} 降级 {a['degraded']} 无行 {a['missing']} "
              f"查准 {a['precision']:.2%} 覆盖 {a['auto_cover']:.2%}")


if __name__ == "__main__":
    main()
