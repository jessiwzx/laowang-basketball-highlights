# -*- coding: utf-8 -*-
r"""13 条真漏明细：把「漏检 94」里距离 > 0.5s 的那些单独列出来。

判据
----
对每条未被认领的真值点 g（官方 0.2s 口径），找**最近的**事件行（不限容差）：
  - 距离 <= 0.5s  → 事件在，只是时刻偏了（测量抖动）
  - 距离 >  0.5s  → 场上根本没有对应行 = **真漏**

「真漏」再分三档，看差在多远：
  R1 0.5~1.0s   很近但没配上 → 可能是 3s 冷却吞掉了、或行被判成别的球
  R2 1.0~3.0s   邻域内有行但不同源
  R3 > 3.0s     整场都没抓到

只读。产物写本目录。
"""
import bisect
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
OUT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "诊断", "2026-11-10-判定层新证据"))

from new_evidence import OLD_ROOT, rd                                    # noqa: E402
import evaluate as EV                                                    # noqa: E402

V6 = ["2026-08-22_1", "2026-08-28_1", "2026-08-29_3",
      "2026-09-04_1", "2026-09-11_3", "2026-09-12_1"]
TOL = 0.2
TRUE_MISS_SEC = 0.5


def nearest(ev_ts, g):
    """返回 (最近事件时刻, 带符号距离, 事件下标)。"""
    k = bisect.bisect_left(ev_ts, g)
    best = None
    for j in (k - 1, k, k + 1):
        if 0 <= j < len(ev_ts):
            d = ev_ts[j] - g
            if best is None or abs(d) < abs(best[1]):
                best = (ev_ts[j], d, j)
    return best


def tier_of(e):
    """事件档位：high / review / low。"""
    s = e.get("status") or e.get("tier") or ""
    return s


def main():
    rows = []
    for v in V6:
        ev_doc = rd(os.path.join(OLD_ROOT, v, "events.json"))
        events = ev_doc["events"]
        # ⚠ events.json 的行**不是按时间排序**的，必须先排再 bisect
        order = sorted(range(len(events)), key=lambda i: events[i]["t"])
        events = [events[i] for i in order]
        ev_ts = [e["t"] for e in events]
        goals = sorted(rd(os.path.join(ROOT, "truth", v + ".json"))["goals"])
        # 1:1 最近优先认领（与 ealuate.py 同口径）
        pairs = sorted((abs(e["t"] - g), ei, gi)
                       for ei, e in enumerate(events)
                       for gi, g in enumerate(goals) if abs(e["t"] - g) <= TOL)
        used_e, used_g = set(), set()
        for _d, ei, gi in pairs:
            if ei in used_e or gi in used_g:
                continue
            used_e.add(ei)
            used_g.add(gi)
        miss = [g for gi, g in enumerate(goals) if gi not in used_g]

        for g in miss:
            nt, d, j = nearest(ev_ts, g)
            e = events[j]
            rec = {
                "video": v, "t": round(g, 2),
                "nearest_t": round(nt, 2), "delta": round(d, 3),
                "abs_delta": round(abs(d), 3),
                "tier": tier_of(e),
                "conf": e.get("conf"),
                "reason": e.get("review_reason") or e.get("low_reason") or "",
            }
            if abs(d) > 3.0:
                rec["bucket"] = "R3 >3s"
            elif abs(d) > 1.0:
                rec["bucket"] = "R2 1~3s"
            elif abs(d) > TRUE_MISS_SEC:
                rec["bucket"] = "R1 0.5~1s"
            else:
                rec["bucket"] = "抖动 ≤0.5s"
            rows.append(rec)

    tm = [r for r in rows if r["bucket"] != "抖动 ≤0.5s"]
    tm.sort(key=lambda r: (r["video"], r["t"]))

    L = []
    A = L.append
    A("# 真漏明细（>0.5s，%d 条 / 全部漏检 %d 条）" % (len(tm), len(rows)))
    A("")
    cnt = Counter(r["bucket"] for r in rows)
    A("## 分档")
    A("")
    A("| 档 | 条数 | 含义 |")
    A("|---|---|---|")
    A("| 抖动 ≤0.5s | %d | 事件在，只是时刻偏了 |" % cnt["抖动 ≤0.5s"])
    A("| R1 0.5~1s | %d | 很近却没配上（冷却/被别的球认领） |" % cnt["R1 0.5~1s"])
    A("| R2 1~3s | %d | 邻域有行但不同源 |" % cnt["R2 1~3s"])
    A("| R3 >3s | %d | 整场没抓到 |" % cnt["R3 >3s"])
    A("")
    A("## 逐条")
    A("")
    A("| # | 场 | 真值 t | 最近事件 t | 偏差 | 档 | 事件档位 | conf | 原因 |")
    A("|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(tm, 1):
        A("| %d | %s | %.2f | %.2f | %+.3f | %s | %s | %s | %s |"
          % (i, r["video"][5:], r["t"], r["nearest_t"], r["delta"],
             r["bucket"], r["tier"],
             ("%.2f" % r["conf"]) if isinstance(r["conf"], (int, float)) else "-",
             r["reason"] or "-"))
    A("")
    A("## 按场")
    A("")
    byv = Counter(r["video"] for r in tm)
    for v in V6:
        A("- %s：**%d** 条" % (v, byv[v]))
    A("")
    A("## 最近事件档位分布")
    A("")
    for k, c in Counter(r["tier"] or "(空)" for r in tm).most_common():
        A("- %s：%d" % (k, c))
    A("")

    md = "\n".join(L)
    with open(os.path.join(OUT, "真漏明细.md"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write(md)
    with open(os.path.join(OUT, "真漏明细.json"), "w", encoding="utf-8",
              newline="\n") as f:
        json.dump({"rows": rows, "true_miss": tm}, f, ensure_ascii=False,
                  indent=1)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
