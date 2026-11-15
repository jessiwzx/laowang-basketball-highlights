"""127 条降级的特征归因（只读，不改任何产物）。

降级 = 用户 selection 勾了「这是进球」，但系统只给 review/low（要人工补勾）。
本脚本把新六场这 127 条逐条摊开，与「命中 659」「假绿 204」做分布对比，
找下一个「短档 0.14→0.17」式的系统性低估。

口径与 tools/eval_sel.py 完全一致：复用 E.load_rows 的剔除规则 + sel_match 的
1:1 贪心配对，不另立一套。
"""
import json
import os
import statistics as st
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)

import eval_lib as E          # noqa: E402
import eval_sel as ESEL       # noqa: E402
import evaluate as ev         # noqa: E402  （与 eval_lib 同一个剔除口径）

OUT = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(ROOT, "work")
UP = r"D:\claude code\视频剪辑\work"
FEATS = ("confidence", "above_n", "above_span", "below_n", "below_span",
         "cross_off", "net_post", "net_ctrl", "net_local", "descent_px",
         "below_big", "pop_back")


def kept_events(video):
    """→ events 列表（已按 deleted + 间隙球剔除），保留全部特征字段。"""
    path = os.path.join(WORK, video, "events.json")
    doc = E.load_events(path)
    deleted = ev._load_deleted_seconds(path)
    inter = ev.INTERMISSION_GOALS.get(video, [])
    if deleted or inter:
        drop = ev._deleted_exclusions([e["t"] for e in doc],
                                      deleted + inter)
        doc = [e for i, e in enumerate(doc) if i not in drop]
    return doc


def rows_of(events):
    return [(e["t"], e["status"]) for e in events]


def collect():
    deg, hit, fg = [], [], []
    for v in ESEL.VIDEOS_SEL_NEW:
        evs = kept_events(v)
        rows = rows_of(evs)
        goals, _p = ESEL.load_selection(v, [WORK, UP])
        if not goals:
            print(f"  skip {v}: 无 selection")
            continue
        used_g, used_r = ESEL.sel_match(goals, rows, ESEL.SEL_TOL)
        for ri, (t, s) in enumerate(rows):
            rec = dict(evs[ri])
            rec["video"] = v
            if ri in used_r:
                (hit if s == "high" else deg).append(rec)
            elif s == "high":
                fg.append(rec)          # 自动剪了但用户没勾
    return deg, hit, fg


def dist(recs, key):
    vals = [r.get(key) for r in recs if r.get(key) is not None]
    if not vals:
        return None
    if isinstance(vals[0], bool):
        return f"{sum(vals)}/{len(vals)} ({sum(vals)/len(vals):.0%})"
    if all(isinstance(x, (int, float)) for x in vals):
        return (f"p25 {st.quantiles(vals, n=4)[0]:.2f} / "
                f"p50 {st.median(vals):.2f} / "
                f"p75 {st.quantiles(vals, n=4)[2]:.2f}")
    return str(Counter(vals).most_common(1))


def reason_of(r):
    return r.get("low_reason") or r.get("review_reason") or "(无理由)"


def main():
    deg, hit, fg = collect()
    print(f"降级(勾了只给 review/low) {len(deg)}  命中(勾了且 high) {len(hit)}  "
          f"假绿(high 未勾) {len(fg)}")
    assert len(deg) == 127, f"降级条数 {len(deg)} ≠ 127，口径不一致"

    print("\n=== 1 分场 ===")
    c = Counter(r["video"] for r in deg)
    for v in ESEL.VIDEOS_SEL_NEW:
        n = sum(1 for r in hit if r["video"] == v)
        print(f"  {v:<14} 降级 {c[v]:>3}   同场命中 {n:>3}")

    print("\n=== 2 状态与理由 ===")
    print("  status:", dict(Counter(r["status"] for r in deg)))
    print("  理由:", dict(Counter(reason_of(r) for r in deg).most_common()))

    print("\n=== 3 离自动线(0.60)还差多少 ===")
    gaps = [round(0.60 - r["confidence"], 3) for r in deg]
    b = Counter()
    for g in gaps:
        if g <= 0:
            b["已≥0.60(被结构规则压着)"] += 1
        elif g <= 0.01:
            b["差 ≤0.01"] += 1
        elif g <= 0.03:
            b["差 0.01~0.03"] += 1
        elif g <= 0.06:
            b["差 0.03~0.06"] += 1
        elif g <= 0.10:
            b["差 0.06~0.10"] += 1
        else:
            b["差 >0.10"] += 1
    for k, n in b.most_common():
        print(f"  {k:<24} {n:>3}")

    print("\n=== 4 特征对比（降级 / 命中 / 假绿）===")
    print(f"  {'特征':<12}{'降级':<34}{'命中':<34}{'假绿'}")
    for f in FEATS:
        a, bb, cc = dist(deg, f), dist(hit, f), dist(fg, f)
        print(f"  {f:<12}{str(a):<34}{str(bb):<34}{cc}")

    print("\n=== 5 降级里 above_span<0.2 的占比（v23 已补过的特征）===")
    for nm, grp in (("降级", deg), ("命中", hit), ("假绿", fg)):
        n = sum(1 for r in grp if r["above_span"] < 0.2)
        print(f"  {nm:<6} {n:>4}/{len(grp):<5} = {n/len(grp):.0%}")

    json.dump(deg, open(os.path.join(OUT, "degraded_127.json"), "w",
                        encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n明细 → degraded_127.json（{len(deg)} 条）")


if __name__ == "__main__":
    main()
