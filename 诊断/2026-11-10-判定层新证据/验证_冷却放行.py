# -*- coding: utf-8 -*-
r"""验证 v20 冷却放行判据：放行行的「接近轨迹强度」能否分开真补篮与同球重复。

背景（2026-11-10 实测）
----------------------
v20（`NEWBALL_ENABLE=True`）相对 v19 新增 84 行，其中 59 条 high：
**真进球 5 条 / 假绿 54 条**（真假比 1:11）。这正是查准 66.18% → 63.33% 的
全部来源。用户 2026-11-01 已裁定要这个收益方向（漏检 −4、补篮可见），所以
不能回滚，只能**收紧放行判据**。

`goal_decider._new_ball_since` 现在的判据是：上方点 ≥2、跨度 ≥0.2s、
且（含高处点 y < rim−2×hh 或 侧移点 |x−cx| > 1.3×hw）。只要"任一"，很松。

本脚本对 84 条新增行重算四个强度量，看 5 条真球是否在分布上分开：
  up_n      区间内筐上方点数（横向带内、rim−band 以上）
  up_span   这些点的时间跨度
  top_h     区间内最高点相对筐口的高度（筐高倍数）
  side_w    区间内最大横向偏移（筐宽倍数）

只读；不改生产代码。
"""
import bisect
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
OUT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from new_evidence import (OLD_ROOT, load_points, seg_at, rd, wr)   # noqa: E402

V6 = ["2026-08-22_1", "2026-08-28_1", "2026-08-29_3",
      "2026-09-04_1", "2026-09-11_3", "2026-09-12_1"]

RIM_Y_RATIO, BAND_RATIO, X_MARGIN = 0.35, 0.25, 0.2
EDGE_SEC = 0.02


def label(gs, es, tol=0.2):
    pairs = sorted((abs(e["t"] - g), ei, gi) for ei, e in enumerate(es)
                   for gi, g in enumerate(gs) if abs(e["t"] - g) <= tol)
    ue, ug = set(), set()
    for _d, ei, gi in pairs:
        if ei in ue or gi in ug:
            continue
        ue.add(ei)
        ug.add(gi)
    return [i in ue for i in range(len(es))]


def strength(pts, ts, t_from, t_to, rect):
    """区间 [t_from+edge, t_to−edge] 内的接近轨迹强度。"""
    hx, hy, hw, hh = rect
    rim = hy + hh * RIM_Y_RATIO
    band = hh * BAND_RATIO
    x_lo, x_hi = hx - hw * X_MARGIN, hx + hw * (1 + X_MARGIN)
    cx = hx + hw / 2.0
    lo = bisect.bisect_right(ts, t_from + EDGE_SEC)
    hi = bisect.bisect_left(ts, t_to - EDGE_SEC)
    up, side, top = [], 0.0, 0.0
    for p in pts[lo:hi]:
        side = max(side, abs(p[1] - cx) / hw)
        top = max(top, (rim - p[2]) / hh)
        if x_lo <= p[1] <= x_hi and p[2] < rim - band:
            up.append(p)
    span = (up[-1][0] - up[0][0]) if len(up) >= 2 else 0.0
    return {"up_n": len(up), "up_span": round(span, 3),
            "top_h": round(top, 3), "side_w": round(side, 3)}


def main():
    rows = []
    for v in V6:
        ev_p = os.path.join(OLD_ROOT, v, "events.json")
        ps_p = os.path.join(OLD_ROOT, v, "positions.json")
        v19 = rd(os.path.join(ROOT, "..", "yolo", "eval_out",
                              "v19_c03_s1", v, "events.json"))["events"]
        v20 = rd(ev_p)["events"]
        ps_doc = rd(ps_p)
        pts, segs = load_points(ps_doc, rd(ev_p))
        ts = [p[0] for p in pts]
        gs = sorted(rd(os.path.join(ROOT, "truth", v + ".json"))["goals"])
        lab = label(gs, v20)
        aset = {round(e["t"], 3) for e in v19}
        for i, e in enumerate(v20):
            if round(e["t"], 3) in aset:
                continue
            prev_t = v20[i - 1]["t"] if i > 0 else None
            rect = seg_at(segs, e["t"])
            s = strength(pts, ts, prev_t if prev_t is not None else e["t"] - 3.0,
                         e["t"], rect)
            rows.append({"video": v, "t": round(e["t"], 3),
                         "status": e["status"], "is_goal": lab[i],
                         "prev_gap": round(e["t"] - prev_t, 3) if prev_t is not None else None,
                         "conf": e.get("confidence"), **s})
    wr(os.path.join(OUT, "cooldown_new_rows.json"), {"rows": rows})

    highs = [r for r in rows if r["status"] == "high"]
    print("新增行 %d，其中 high %d（真 %d / 假 %d）"
          % (len(rows), len(highs), sum(1 for r in highs if r["is_goal"]),
             sum(1 for r in highs if not r["is_goal"])))
    print()
    print("| 判据强度 | 真球(n=%d) | 假绿(n=%d) |"
          % (sum(1 for r in highs if r["is_goal"]),
             sum(1 for r in highs if not r["is_goal"])))
    print("|---|---|---|")
    for k in ("up_n", "up_span", "top_h", "side_w", "prev_gap"):
        g = sorted(r[k] for r in highs if r["is_goal"] and r[k] is not None)
        f = sorted(r[k] for r in highs if not r["is_goal"] and r[k] is not None)
        def q(a, p):
            if not a:
                return "—"
            return a[min(len(a) - 1, int(p * len(a)))]
        print("| `%s` | 中位 %s / 最大 %s | 中位 %s / 最大 %s |"
              % (k, q(g, 0.5), g[-1] if g else "—", q(f, 0.5), f[-1] if f else "—"))
    print()
    # 逐条列出，便于人工判断
    print("| 场 | t | 真? | conf | up_n | span | top_h | side_w | prev_gap |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in highs:
        print("| %s | %.1f | %s | %s | %d | %.2f | %.2f | %.2f | %s |"
              % (r["video"][5:], r["t"], "真" if r["is_goal"] else "假",
                 r["conf"], r["up_n"], r["up_span"], r["top_h"], r["side_w"],
                 r["prev_gap"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
