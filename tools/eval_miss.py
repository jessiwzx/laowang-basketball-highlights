"""第 6 套口径：用户手动补录的漏检（`feedback.json.misses`）。

背景：界面上「漏检 m:ss」的记录只写进 `feedback.misses`（裁定 R6：不产生表格行），
因此永远进不了 selection → 进不了 truth → 五套口径一个都不读它。用户补的漏检
等于写进了一个没人读的文件（2026-11-14 核实：11-06 补的 18:12 / 55:17 在 truth
里 ±0.5s 零命中）。

本工具把这一源接进评测，回答唯一一个五套口径都答不了的问题：
**「系统到底有没有完全没报出来的球」**（新场 truth ⊆ events，查全恒 100% 是结构性假象）。

判定分三档（关键：补录时刻是用户凭画面记的，精度不足以直接当「无行」证据——
实测 11-06 两条 Δ = −0.9s / −2.0s）：

    命中档   Δ ≤ 0.5s   该时刻附近有行，读它的 status
    时刻偏   0.5 < Δ ≤ 3s  有行但差得远，可能同一球也可能不是
    无行     Δ > 3s     真·没进列表

用法：
    python tools/eval_miss.py                # 全表
    python tools/eval_miss.py --json out.json
"""
import argparse
import json
import os
import sys
from collections import Counter

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(PROJECT_ROOT, "tools") not in sys.path:
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "tools"))
sys.path.insert(0, PROJECT_ROOT)

import eval_lib as E                                    # noqa: E402

# events 根目录候选（按优先级）：新场 → 老场 v20 冻结快照 → 上游 work
EV_ROOTS = [
    os.path.join(PROJECT_ROOT, "work"),
    r"D:\claude code\yolo\eval_out\v20_c03_s1",
    r"D:\claude code\视频剪辑\work",
]
FB_ROOTS = [
    os.path.join(PROJECT_ROOT, "work"),
    r"D:\claude code\视频剪辑\work",
]
TOL_HIT = 0.5
TOL_EDGE = 3.0


def find_events(video):
    for r in EV_ROOTS:
        p = os.path.join(r, video, "events.json")
        if os.path.exists(p):
            return p
    return None


def find_feedback(video):
    for r in FB_ROOTS:
        p = os.path.join(r, video, "feedback.json")
        if os.path.exists(p):
            return p
    return None


def videos():
    out = set()
    for r in FB_ROOTS:
        if not os.path.isdir(r):
            continue
        for name in os.listdir(r):
            if os.path.exists(os.path.join(r, name, "feedback.json")):
                out.add(name)
    return sorted(out)


def classify(video):
    """→ [{t, note, marked_at, delta, status, verdict}]"""
    fb = find_feedback(video)
    if not fb:
        return []
    misses = json.load(open(fb, encoding="utf-8")).get("misses") or []
    if not misses:
        return []
    evp = find_events(video)
    events = E.load_events(evp) if evp else []
    rows = []
    for m in misses:
        t = float(m["t"])
        rec = {"t": t, "note": m.get("note", ""),
               "marked_at": m.get("marked_at", ""),
               "delta": None, "status": None, "verdict": "无产物"}
        if events:
            near = min(events, key=lambda e: abs(e["t"] - t))
            d = near["t"] - t
            rec["delta"] = d
            rec["status"] = near["status"]
            ad = abs(d)
            rec["verdict"] = ("命中档" if ad <= TOL_HIT
                              else "时刻偏" if ad <= TOL_EDGE else "无行")
        rec["video"] = video
        rows.append(rec)
    return rows


def report(all_rows, show=True):
    if show:
        print(f"{'场次':<14}{'补录时刻':>9}{'Δ':>8}{'状态':>9}"
              f"{'判定':>8}  标注日期")
        print("-" * 74)
        for r in all_rows:
            d = f"{r['delta']:+.1f}s" if r["delta"] is not None else "-"
            print(f"{r['video']:<14}{r['t']:>9.1f}{d:>8}"
                  f"{str(r['status'] or '-'):>9}{r['verdict']:>8}  "
                  f"{r['marked_at'][:10]}")
        print("-" * 74)

    v = Counter(r["verdict"] for r in all_rows)
    s = Counter((r["verdict"], r["status"]) for r in all_rows
                if r["status"])
    print(f"\n合计 {len(all_rows)} 条手动补录")
    for k in ("命中档", "时刻偏", "无行", "无产物"):
        if v[k]:
            print(f"  {k:<6}{v[k]:>4}")
            if k in ("命中档", "时刻偏"):
                sub = {st: n for (kk, st), n in s.items() if kk == k}
                print(f"         状态分布 {sub}")
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", help="导出逐条明细")
    args = ap.parse_args()
    all_rows = []
    for v in videos():
        all_rows.extend(classify(v))
    report(all_rows)
    if args.json:
        json.dump(all_rows, open(args.json, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"\n明细 → {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
