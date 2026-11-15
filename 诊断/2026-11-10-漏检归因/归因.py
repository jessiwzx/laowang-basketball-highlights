# -*- coding: utf-8 -*-
r"""漏检归因：查全 82.9% 缺的 132 球里，94 条「表里连行都没有」到底缺在哪一环。

口径
----
「漏检」= 某个真值进球在 0.2s 内没有任何事件行认领（官方裁判口径）。它在整条
链路里可能是四个环节断的，本脚本用 `positions.json` 把它们分开：

  A  无检测点      ±1.2s 的筐邻域里连一个检出点都没有 → **检测器漏检**
                   （球太小/被遮挡/逆光，YOLO 没框到）
  B1 只有上方点    有上方点、筐口下方没有任何点 → **穿筐后丢失**
                   （球进网后检不出，配对无从发生）
  B2 只有下方点    有下方点、上方一个点都没有 → **接近段丢失**
                   （球从画面外/篮板上方来，或上方点被同帧大块截胡）
  C  被冷却吞      邻近 3s 内已有事件行，真值点在「冷却窗」里 → **冷却误杀**
                   （v20 已经用身份判据放宽过，剩下的是真被吞）
  D  成对失败      上下都有点，但没凑成一次穿越 → 时间窗/横向容差/规则A
                   （点间隔 > 0.7s，或 x 出横向带，或球心出筐口两端）

只读。不改生产代码。
"""
import bisect
import json
import os
import sys
from statistics import median

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
OUT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "诊断", "2026-11-10-判定层新证据"))

from new_evidence import (OLD_ROOT, load_points, seg_at, rd, wr)   # noqa: E402

V6 = ["2026-08-22_1", "2026-08-28_1", "2026-08-29_3",
      "2026-09-04_1", "2026-09-11_3", "2026-09-12_1"]

RIM_Y_RATIO, BAND_RATIO, X_MARGIN = 0.35, 0.25, 0.2
MAX_CROSS_SEC = 0.7
COOLDOWN_SEC = 3.0
TOL = 0.2
NEAR_SEC = 1.2


def unmatched_goals(goals, events, tol=TOL):
    """1:1 最近优先认领后，没被任何行认领的真值点。"""
    pairs = sorted((abs(e["t"] - g), ei, gi) for ei, e in enumerate(events)
                   for gi, g in enumerate(goals) if abs(e["t"] - g) <= tol)
    used_e, used_g = set(), set()
    for _d, ei, gi in pairs:
        if ei in used_e or gi in used_g:
            continue
        used_e.add(ei)
        used_g.add(gi)
    return [g for gi, g in enumerate(goals) if gi not in used_g]


def classify(pts, ts, g, rect, ev_ts):
    """单个漏检时刻 → (类别, 证据 dict)。"""
    hx, hy, hw, hh = rect
    rim = hy + hh * RIM_Y_RATIO
    band = hh * BAND_RATIO
    x_lo, x_hi = hx - hw * X_MARGIN, hx + hw * (1 + X_MARGIN)
    cx = hx + hw / 2.0
    lo = bisect.bisect_left(ts, g - NEAR_SEC)
    hi = bisect.bisect_right(ts, g + NEAR_SEC)
    near = pts[lo:hi]

    up, down, bandp, wide = [], [], 0, 0
    for p in near:
        in_x = x_lo <= p[1] <= x_hi
        if in_x:
            wide += 1
            if abs(p[2] - rim) < band:
                bandp += 1
            elif p[2] < rim:
                up.append(p)
            else:
                down.append(p)

    ev = {"up": len(up), "down": len(down), "band": bandp, "near": len(near),
          "in_x": wide}
    # 最近事件间隔（判冷却）
    k = bisect.bisect_left(ev_ts, g)
    gaps = []
    for j in (k - 1, k):
        if 0 <= j < len(ev_ts):
            gaps.append(abs(ev_ts[j] - g))
    ev["ev_gap"] = round(min(gaps), 3) if gaps else None

    if ev["near"] == 0:
        return "A 无检测点", ev
    if not up and not down:
        return "A 无检测点", ev          # 只有筐带里的零星点，凑不出穿越
    if up and not down:
        return "B1 只有上方点", ev
    if down and not up:
        return "B2 只有下方点", ev
    # 上下都有 → 看为什么没成对
    # 最近的「上方点 → 其后第一个下方点」间隔
    up_ts = [p[0] for p in up]
    ok_pair = False
    for p in up:
        nxt = next((q for q in down if q[0] > p[0]), None)
        if nxt is not None and nxt[0] - p[0] <= MAX_CROSS_SEC:
            ok_pair = True
            break
    if ok_pair:
        return "D 成对失败", ev
    if ev["ev_gap"] is not None and ev["ev_gap"] < COOLDOWN_SEC:
        return "C 被冷却吞", ev
    return "D 成对失败", ev


def main():
    rows = []
    for v in V6:
        ev_p = os.path.join(OLD_ROOT, v, "events.json")
        ps_p = os.path.join(OLD_ROOT, v, "positions.json")
        ev_doc = rd(ev_p)
        ps_doc = rd(ps_p)
        events = ev_doc["events"]
        pts, segs = load_points(ps_doc, ev_doc)
        ts = [p[0] for p in pts]
        ev_ts = [e["t"] for e in events]
        goals = sorted(rd(os.path.join(ROOT, "truth", v + ".json"))["goals"])
        miss = unmatched_goals(goals, events)
        for g in miss:
            rect = seg_at(segs, g)
            cat, ev = classify(pts, ts, g, rect, ev_ts)
            rows.append({"video": v, "t": round(g, 2), "cat": cat, **ev,
                         "rect": list(rect)})
        print("  %-14s 漏检 %2d / 真值 %3d" % (v, len(miss), len(goals)))

    wr(os.path.join(OUT, "attrib.json"), {"rows": rows})

    from collections import Counter, defaultdict
    cnt = Counter(r["cat"] for r in rows)
    n = len(rows)
    L = []
    A = L.append
    A("# 漏检归因（六场 %d 条）" % n)
    A("")
    A("| 类别 | 条数 | 占比 | 含义 |")
    A("|---|---|---|---|")
    meaning = {
        "A 无检测点": "筐邻域 ±1.2s 连点都没有 → 检测器漏检",
        "B1 只有上方点": "有接近段、穿筐后无点 → 穿筐后检出丢失",
        "B2 只有下方点": "有下方点、无接近段 → 上方轨迹丢失",
        "C 被冷却吞": "真值点落在已有行的 3s 冷却窗内 → 冷却误杀",
        "D 成对失败": "上下都有点却没凑成穿越 → 时间窗/横向容差",
    }
    for k, c in cnt.most_common():
        A("| %s | %d | %.1f%% | %s |" % (k, c, 100 * c / n, meaning.get(k, "")))
    A("")

    # 逐场
    A("## 逐场")
    A("")
    cats = list(meaning)
    A("| 场 | 漏检 | " + " | ".join(c.split()[0] for c in cats) + " |")
    A("|---|---|" + "---|" * len(cats))
    byv = defaultdict(Counter)
    for r in rows:
        byv[r["video"]][r["cat"]] += 1
    for v in V6:
        A("| %s | %d | %s |" % (v, sum(byv[v].values()),
                               " | ".join(str(byv[v].get(c, 0)) for c in cats)))
    A("")

    # 关键量化
    A("## 各类的量化证据")
    A("")
    for k in cats:
        sub = [r for r in rows if r["cat"] == k]
        if not sub:
            continue
        A("- **%s**（%d 条）：±1.2s 内筐带检出中位 **%s** 个、横向带内点中位 **%s** 个、"
          "最近事件间隔中位 **%s s**"
          % (k, len(sub),
             median([r["near"] for r in sub]),
             median([r["in_x"] for r in sub]),
             ("%.2f" % median([r["ev_gap"] for r in sub
                               if r["ev_gap"] is not None]))
             if any(r["ev_gap"] is not None for r in sub) else "—"))
    A("")

    # 样例
    A("## 样例（各类前 5 条，供人工核）")
    A("")
    A("| 场 | t | 类别 | 近点数 | 上方 | 下方 | 筐带 | 事件间隔 |")
    A("|---|---|---|---|---|---|---|---|")
    for k in cats:
        for r in [x for x in rows if x["cat"] == k][:5]:
            A("| %s | %.1f | %s | %d | %d | %d | %d | %s |"
              % (r["video"][5:], r["t"], r["cat"], r["near"], r["up"],
                 r["down"], r["band"], r["ev_gap"]))
    A("")

    md = "\n".join(L)
    with open(os.path.join(OUT, "结论.md"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write(md)
    print()
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
