# -*- coding: utf-8 -*-
"""下游五场 KPI：v20（关锚点）vs v21（开锚点）

下游五场没有 truth/<场>.json，但都有剪辑台 selection.json——按 R-020 Step 0
的既定口径（truth ≡ selection.json，2026-10-17 已认定「缺 truth 的场可直接导出」）
可直接派生真值。真值 = 最终成片里的每一个球。

指标口径完全复用上游 tools/evaluate.py（match_report / auto_metrics），
只把 truth 来源换成本地派生的：
    python kpi_field.py 2026-10-17_1 [2026-10-23_1 ...]
    python kpi_field.py --arm events_A.json --arm events_C.json 2026-10-17_1

只读，不写任何生产文件。
"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
UP = r"D:/claude code/视频剪辑"
sys.path.insert(0, os.path.join(UP, "tools"))
sys.path.insert(0, _ROOT)

import evaluate as EV                                             # noqa: E402

WORK = os.path.join(_ROOT, "work")


def rd(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def parse_ts(s):
    """'mm:ss.s' / 'h:mm:ss.s' → 秒。"""
    v = 0.0
    for p in str(s).strip().split(":"):
        v = v * 60 + float(p)
    return v


def truth_of(name):
    """selection.json → 真值秒列表（剪辑台最终保留的每个球）。"""
    sel = rd(os.path.join(WORK, name, "selection.json"))
    out = []
    for item in sel:
        try:
            out.append(round(parse_ts(item.get("t") if isinstance(item, dict)
                                      else item), 3))
        except (TypeError, ValueError):
            pass
    return sorted(set(out))


def metrics(name, events_file, tolerance=EV.TOLERANCE):
    """单臂单场四数（与官方裁判同口径）。"""
    ev_path = os.path.join(WORK, name, events_file)
    if not os.path.exists(ev_path):
        return None
    doc = rd(ev_path)
    events = doc["events"]
    deleted = EV._load_deleted_seconds(ev_path)
    inter = EV.INTERMISSION_GOALS.get(name, [])
    drop = EV._deleted_exclusions([e["t"] for e in events], deleted + inter)
    kept = [e for i, e in enumerate(events) if i not in drop]
    detected = [(e["t"], e["status"]) for e in kept]
    goals = truth_of(name)
    rep = EV.match_report(goals, detected, tolerance=tolerance)
    high_total = sum(1 for _t, s in detected if s == "high")
    m = EV.auto_metrics(rep, high_total)
    return {
        "logic": doc.get("logic_ver"),
        "goals": len(goals),
        "rows": len(detected),
        "high": high_total,
        "review": sum(1 for _t, s in detected if s == "review"),
        "low": sum(1 for _t, s in detected if s == "low"),
        "auto_hit": m["自动档命中"],
        "false_green": m["假绿"],
        "precision": m["查准"],
        "recall": m["查全"],
        "review_hit": rep["待确认中"],
        "low_hit": rep["命中低置信"],
        "miss": rep["漏检"],
        "noise_low": rep["低噪"],
    }


def line(tag, m):
    if not m:
        print(f"  {tag:<16} —")
        return
    print(f"  {tag:<16} logic v{m['logic']:<3} 行 {m['rows']:>4}"
          f"  high {m['high']:>4} / review {m['review']:>4} / low {m['low']:>4}"
          f" | 真值 {m['goals']:>4}  命中 {m['auto_hit']:>4}"
          f"  假绿 {m['false_green']:>4}  漏检 {m['miss']:>4}"
          f" | 查准 {m['precision'] * 100:5.2f}%  查全 {m['recall'] * 100:5.2f}%")


def main(argv):
    arms = ["events_A.json", "events_C.json"]
    names = []
    i = 0
    while i < len(argv):
        if argv[i] == "--arm":
            i += 1
            arms = argv[i].split(",")
        else:
            names.append(argv[i])
        i += 1
    if not names:
        names = sorted(os.path.basename(os.path.dirname(p)) for p in
                       __import__("glob").glob(os.path.join(WORK, "*",
                                                            "selection.json")))
    for n in names:
        print(f"=== {n}")
        for a in arms:
            line(os.path.basename(a).replace("events_", "").replace(".json", ""),
                 metrics(n, a))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
