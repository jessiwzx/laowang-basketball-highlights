# -*- coding: utf-8 -*-
"""131 的溯源（只读）：把「未标注」逐层拆开，确认它不是统计噪声。

链条：
  自动档 830 = 系统判 high 的行
  命中   659 = 其中与 selection 勾选单配对成功的
  误判   171 = 830 − 659
  其中   40  = feedback.json 里 ±0.2s 内找到 verdict == "not_goal"
  剩下  131  = 剩下的（本文要查清：是「没匹配到任何标记」还是「匹配到别的值」）
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


def load_fb(v):
    p = os.path.join(ROOT, "work", v, "feedback.json")
    if not os.path.exists(p):
        return None
    return json.load(open(p, encoding="utf-8"))


FB = {v: load_fb(v) for v in NEW}

print("=" * 76)
print("【0】feedback.json 里到底有哪些判定值")
print("=" * 76)
allv = Counter()
for v in NEW:
    d = FB[v] or {}
    vs = d.get("verdicts") or []
    for it in vs:
        allv[str(it.get("verdict"))] += 1
    print(f"  {v:<14} verdicts {len(vs):>3} 条   misses {len(d.get('misses') or []):>2} 条"
          f"   键 {sorted(d.keys())}")
print(f"\n  全库 verdict 取值分布：{dict(allv)}")

# ---------------------------------------------------------------- 行读取
cache = {}
T = {}
rows_all = {}
for v in NEW:
    R.BASE["short"] = R._short_for(v, R.DEFAULT_OLD_ARM, R.DEFAULT_OLD_DEL)
    rows, bad = R.replay(v, R.var(), cache, R.DEFAULT_OLD_ARM, R.DEFAULT_OLD_DEL)
    rows_all[v] = rows
    goals, _ = ES.load_selection(v, ROOTS)
    _ug, ur = ES.sel_match(goals, rows, ES.SEL_TOL)
    T[v] = {ri: ("hit" if ri in ur else "false_green")
            for ri, (t, s) in enumerate(rows) if s == "high"}

print("\n" + "=" * 76)
print("【1】第一层：自动档怎么变成 171")
print("=" * 76)
n_high = sum(len(T[v]) for v in NEW)
n_hit = sum(1 for v in NEW for c in T[v].values() if c == "hit")
n_fg = n_high - n_hit
print(f"  系统判 high 的行        {n_high}")
print(f"  − 与勾选单配上的（命中）  {n_hit}")
print(f"  = high 但没勾（旧称误判） {n_fg}")
for v in NEW:
    h = len(T[v]); hit = sum(1 for c in T[v].values() if c == "hit")
    print(f"      {v:<14} high {h:>3}  命中 {hit:>3}  未勾 {h-hit:>3}")

# ---------------------------------------------------------------- 第二层
print("\n" + "=" * 76)
print("【2】第二层：171 条按 feedback 的匹配结果拆开")
print("=" * 76)


def match(v, t):
    """→ (是否匹配到, verdict 值, 时间差)"""
    best = None
    for it in (FB[v] or {}).get("verdicts") or []:
        try:
            tt = float(it.get("t"))
        except (TypeError, ValueError):
            continue
        d = abs(tt - t)
        if d <= TOL and (best is None or d < best[0]):
            best = (d, it.get("verdict"))
    return best


buckets = Counter()
detail = {v: Counter() for v in NEW}
for v in NEW:
    for ri, c in T[v].items():
        if c != "false_green":
            continue
        t = rows_all[v][ri][0]
        m = match(v, t)
        if m is None:
            k = "无任何标记"
        else:
            k = f"标记={m[1]}"
        buckets[k] += 1
        detail[v][k] += 1

for k, n in buckets.most_common():
    print(f"  {k:<18} {n:>4}")
print("\n  逐场：")
for v in NEW:
    d = detail[v]
    print(f"    {v:<14} " + "  ".join(f"{k}={n}" for k, n in sorted(d.items())))

# ---------------------------------------------------------------- 第三层
print("\n" + "=" * 76)
print("【3】关键核实：这 131 条真的「没有任何标记」吗")
print("=" * 76)
none_items = []
for v in NEW:
    for ri, c in T[v].items():
        if c != "false_green":
            continue
        t = rows_all[v][ri][0]
        if match(v, t) is None:
            none_items.append((v, t))
print(f"  完全没标记的行：{len(none_items)} 条")
print(f"  这些场的 verdicts 总条数：{sum(len((FB[v] or {}).get('verdicts') or []) for v in NEW)}")
print(f"  → 换句话说：你这 6 场一共标了 "
      f"{sum(len((FB[v] or {}).get('verdicts') or []) for v in NEW)} 条裁决，"
      f"其中 {len(none_items)} 条 high 行一条都没碰到")

# 距离分布：那些 40 条离 0.2s 多远？判断匹配是否勉强
print("\n  「匹配上 not_goal」的 40 条，时间差分布（越接近 0 越可信）：")
ds = []
for v in NEW:
    for ri, c in T[v].items():
        if c != "false_green":
            continue
        t = rows_all[v][ri][0]
        m = match(v, t)
        if m and m[1] == "not_goal":
            ds.append(m[0])
ds.sort()
if ds:
    print(f"    n={len(ds)}  min={ds[0]:.4f}  中位={ds[len(ds)//2]:.4f}  max={ds[-1]:.4f}")
    print(f"    >0.15s 的（勉强匹配）：{sum(1 for d in ds if d > 0.15)} 条")

# 反向：feedback 里的 not_goal 有没有没被用上的
print("\n  反向核实：你标的 not_goal 总共多少条、被用上多少条")
tot_ng = 0
used = set()
for v in NEW:
    for it in (FB[v] or {}).get("verdicts") or []:
        if it.get("verdict") == "not_goal":
            tot_ng += 1
            t = float(it["t"])
            for ri, c in T[v].items():
                if c == "false_green" and abs(rows_all[v][ri][0] - t) <= TOL:
                    used.add((v, round(t, 2)))
                    break
print(f"    你标 not_goal 共 {tot_ng} 条；落在「high 未勾」行上被计入的 {len(used)} 条")
print(f"    剩下 {tot_ng - len(used)} 条落在命中行 / 低档行 / 已删球上 —— 不参与误判统计")
