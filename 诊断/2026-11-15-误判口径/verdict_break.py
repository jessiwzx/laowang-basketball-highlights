# -*- coding: utf-8 -*-
"""误判口径拆解（只读）：把「没勾」拆成「明确 not_goal」与「未标注」。

用户 2026-11-15 纠正：selection.json 是"选进成片的球单"，**没勾 ≠ 不是进球**
（比赛间隙随便投篮的进球也可能没勾）。所以：
  误判（严格） = 系统判 high 且用户明确标了 not_goal
  未标注       = 系统判 high 但既没勾也没标 → 既不能算对也不能算错

产出三张表：① 误判 171 条的成分 ② A 规则 44 条降档的成分 ③ v24 面积门 33 条。
"""
import json
import os
import sys
from collections import Counter

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import offline_replay as R          # noqa: E402
import eval_sel as ES               # noqa: E402

NEW = list(R.NEW)
ROOTS = [R.DEFAULT_OLD_DEL, R.LOCAL_WORK]
TOL = 0.2


# ---------------------------------------------------------------- 标注读取
def verdicts_of(v):
    p = os.path.join(ROOT, "work", v, "feedback.json")
    if not os.path.exists(p):
        return []
    d = json.load(open(p, encoding="utf-8"))
    return d.get("verdicts") or []


VCACHE = {v: verdicts_of(v) for v in NEW}


def lab_of(v, t):
    """→ 'not_goal' / 'goal' / None（未标注）"""
    best = None
    for it in VCACHE.get(v, []):
        try:
            tt = float(it.get("t"))
        except (TypeError, ValueError):
            continue
        if abs(tt - t) <= TOL and (best is None or abs(tt - t) < abs(best[0] - t)):
            best = (tt, it.get("verdict"))
    return best[1] if best else None


# ---------------------------------------------------------------- 行读取
def rows_of(p, cache=None):
    cache = {} if cache is None else cache
    out = {}
    for v in NEW:
        R.BASE["short"] = R._short_for(v, R.DEFAULT_OLD_ARM, R.DEFAULT_OLD_DEL)
        rows, _bad = R.replay(v, p, cache, R.DEFAULT_OLD_ARM, R.DEFAULT_OLD_DEL)
        out[v] = rows
    return out


def tag(rows, goals):
    """每行 → hit / false_green / degraded / other"""
    if not goals:
        return {}
    _ug, ur = ES.sel_match(goals, rows, ES.SEL_TOL)
    out = {}
    for ri, (t, s) in enumerate(rows):
        if s == "high":
            out[ri] = "hit" if ri in ur else "false_green"
        else:
            out[ri] = "degraded" if ri in ur else "other"
    return out


def tally(items, name, width=0):
    c = Counter(lab_of(v, t) or "未标注" for v, t in items)
    n = len(items)
    print(f"  {name:<{width}} n={n:>4}  "
          f"not_goal={c['not_goal']:>4}  goal={c['goal']:>3}  "
          f"未标注={c['未标注']:>4}"
          + (f"   (标注密度 {100*(n-c['未标注'])/n:.0f}%)" if n else ""))
    return c, n


# ================================================================ 主流程
print("=" * 78)
print("误判口径拆解（新六场 · 用户 feedback 容差 0.2s）")
print("=" * 78)

base = rows_of(R.var())
G = {}
for v in NEW:
    g, p = ES.load_selection(v, ROOTS)
    G[v] = g

T = {}
for v in NEW:
    T[v] = tag(base[v], G[v])

# ---------------------------------------------------------------- 表 1
print("\n【表 1】v24 基线每类的标注成分")
print("-" * 78)
for cls, label in (("hit", "命中（high 且勾了）"),
                   ("false_green", "误判（high 未勾）"),
                   ("degraded", "漏判（低档且勾了）")):
    items = [(v, base[v][ri][0]) for v in NEW
             for ri, c in T[v].items() if c == cls]
    tally(items, label, 28)

fg_items = [(v, base[v][ri][0]) for v in NEW
            for ri, c in T[v].items() if c == "false_green"]
c_fg, n_fg = tally(fg_items, "误判合计", 28)

# 「没勾但明确标 goal」——这种情况应该几乎不存在（勾与 goal 一致）
odd = [(v, base[v][ri][0]) for v in NEW
       for ri, c in T[v].items() if c == "false_green" and lab_of(v, base[v][ri][0]) == "goal"]
print(f"\n  注意：误判里被标成 goal 的 {len(odd)} 条"
      f"{'（若有，说明勾选单与 feedback 打架）' if odd else '（勾选单与 feedback 自洽 ✓）'}")

# ---------------------------------------------------------------- 表 2
print("\n【表 2】A 规则（prev_gap<3.0s 后一条降档）动了哪些行")
print("-" * 78)
gap = rows_of(R.var(gap=3.0))
changed = []          # (v, t, 原分类, 新分类, 标注)
for v in NEW:
    b = {round(t, 2): (ri, s) for ri, (t, s) in enumerate(base[v])}
    for t2, s2 in gap[v]:
        t, s = b.get(round(t2, 2), (None, None))
        if s != s2:
            changed.append((v, t2, T[v].get(t, "?"),
                            "review" if s2 != "high" else "high",
                            lab_of(v, t2)))
print(f"  共改档 {len(changed)} 行")
c_ch = Counter((c[2], c[4] or "未标注") for c in changed)
for (src, vd), n in sorted(c_ch.items()):
    print(f"    {src:<12} · {vd:<9} {n:>3}")

print("\n  其中「原分类=误判」的那些（这是 A 规则声称的收益）：")
tally([(c[0], c[1]) for c in changed if c[2] == "false_green"], "A 降掉的误判", 18)
print("  其中「原分类=命中」的那些（这是 A 规则付出的代价）：")
tally([(c[0], c[1]) for c in changed if c[2] == "hit"], "A 降掉的命中", 18)

# ---------------------------------------------------------------- 表 3
print("\n【表 3】v24 面积门 33 条（review_reason == 'area'）")
print("-" * 78)
ev_all = {}
for v in NEW:
    d = json.load(open(os.path.join(ROOT, "work", v, "events.json"), encoding="utf-8"))
    ev_all[v] = d["events"]
area_items = [(v, e["t"]) for v in NEW for e in ev_all[v]
              if e.get("review_reason") == "area"]
c_area, n_area = tally(area_items, "面积门降档", 18)

# ---------------------------------------------------------------- 表 4
print("\n【表 4】换成「严格误判」后，查准的两个说法")
print("-" * 78)
n_hit = sum(1 for v in NEW for c in T[v].values() if c == "hit")
n_fg = len(fg_items)
n_ng = c_fg["not_goal"]
n_un = c_fg["未标注"]
print(f"  勾选口径（现用）  命中 {n_hit} / 自动档 {n_hit + n_fg}"
      f" = {100*n_hit/(n_hit+n_fg):.2f}%   未标注 {n_un} 条被当成错")
print(f"  严格口径（新）    命中 {n_hit} / (命中+明确误判 {n_hit + n_ng})"
      f" = {100*n_hit/(n_hit+n_ng):.2f}%   分母剔掉 {n_un} 条未标注")
print(f"\n  ⇒ 未标注占比 {100*n_un/n_fg:.0f}%（{n_un}/{n_fg}），"
      f"这部分之前被一律算作「剪错」")

# 若 A 规则落地，两种口径下的差异
print("\n【表 5】A 规则落地后（两种口径各看一遍）")
print("-" * 78)
hit_down = sum(1 for c in changed if c[2] == "hit")
fg_down = sum(1 for c in changed if c[2] == "false_green")
fg_ng_down = sum(1 for c in changed if c[2] == "false_green" and c[4] == "not_goal")
h2, f2 = n_hit - hit_down, n_fg - fg_down
print(f"  勾选口径  命中 {h2} / 自动档 {h2 + f2} = {100*h2/(h2+f2):.2f}%"
      f"   （基线 {100*n_hit/(n_hit+n_fg):.2f}%）")
h3, f3 = n_hit - hit_down, n_ng - fg_ng_down
print(f"  严格口径  命中 {h3} / (命中+明确误判 {h3 + f3}) = {100*h3/(h3+f3):.2f}%"
      f"   （基线 {100*n_hit/(n_hit+n_ng):.2f}%）")
print(f"  A 降掉的 {fg_down} 条误判里，明确 not_goal 只有 {fg_ng_down} 条"
      f"（{100*fg_ng_down/fg_down:.0f}%）")
print(f"  A 降掉的 {hit_down} 条命中里，勾了=确定真球 {hit_down} 条"
      f"（勾选是强证据，无需 feedback）")

# 逐场：A 规则收益的可信度
print("\n【表 6】逐场：A 降掉的误判里有多少是本场明确 not_goal")
print("-" * 78)
print(f"  {'场':<14}{'降误判':>8}{'其中 not_goal':>14}{'降命中':>8}")
for v in NEW:
    cs = [c for c in changed if c[0] == v]
    print(f"  {v:<14}{sum(1 for c in cs if c[2]=='false_green'):>8}"
          f"{sum(1 for c in cs if c[2]=='false_green' and c[4]=='not_goal'):>14}"
          f"{sum(1 for c in cs if c[2]=='hit'):>8}")
