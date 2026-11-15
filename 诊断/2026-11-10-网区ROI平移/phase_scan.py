# -*- coding: utf-8 -*-
r"""11-07_1 差场归因最后一环：测网**相位**扫描。

背景
----
`诊断/2026-11-10-网区ROI平移/结论.md` 已排除「标定偏 → ROI 偏出网」，
把 11-07 的弱网信号归到「筐太小（网区面积 0.54 倍）」。但还有一条没排除：

生产的三窗是 `NET_POST_OFFS = (0.45, 0.70, 0.95)`（相对事件时刻 t），取三窗最大值。
如果 11-07 的网摆动比 11-06 滞后（球速更慢 / 判定时刻更早），三个窗可能**全部
错过摆动峰** —— 现象与「网太小」几乎一模一样。

做法
----
不测三窗，改测**完整曲线**：事件后 0.15s 到 1.65s，每 0.10s 起一个 5 帧窗
（外加生产的 0.70s 精确对齐），共 17 个相位。每个 (事件, 相位) 调生产函数
`net_motion._window_scores`，参数一个不改。

给出三组对比：
  prod   = max(post@0.45, post@0.70, post@0.95)   ← 现在生产就是这么取的
  curve  = max over 全部 17 个相位                  ← 如果改成「滑窗取峰」能拿到多少
  best   每条事件自己的峰落在哪个相位（看是否系统性滞后）

判定：
  curve 把 ≥TH 比例从 57% 拉回 85%+ → 是取峰问题，有救（要改 net_motion）
  curve 仍在 57% 附近               → 确认网太小，算法层结案

只读：不改生产代码、不写 work、不动 sidecar。
用法：.venv\Scripts\python 诊断\2026-11-10-网区ROI平移\phase_scan.py
"""
import json
import os
import statistics as st
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
OUT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import bbh.net_motion as NM                                          # noqa: E402
from bbh.models import Calibration                                   # noqa: E402
from roi_shift import (ARCHIVE, WORK, load_goals, rd, true_rows, wr)  # noqa: E402

SCENES = ["2026-11-06_1", "2026-11-07_1"]
N_EVENTS = 40
SEED_STEP = 3

# 0.15 ~ 1.65 每 0.10 一格，再补上生产的 0.70（不在 0.10 网格上）
GRID = sorted(set([round(0.15 + 0.10 * k, 2) for k in range(16)]
                  + list(NM.NET_POST_OFFS)))
PROD = tuple(NM.NET_POST_OFFS)


def scan_scene(v):
    calib = Calibration.load(os.path.join(WORK, v, "calibration.json"))
    pos = rd(os.path.join(WORK, v, "positions.json"))
    ev = rd(os.path.join(WORK, v, "events.json"))["events"]
    goals, gsrc = load_goals(v)
    rows = true_rows(ev, goals)
    rows.sort(key=lambda e: e["t"])
    pick = rows[::SEED_STEP][:N_EVENTS]
    shift = pos.get("shift") or []
    video = os.path.join(ARCHIVE, v + ".mp4")
    if not os.path.exists(video):
        video = pos.get("video") or video

    cap = NM._open_cap(video, None)
    if cap is None:
        raise RuntimeError("打不开视频：" + video)
    curves = []
    try:
        for i, e in enumerate(pick):
            t = e["t"]
            pts = {}
            for off in GRID:
                g = NM._window_scores(cap, t + off, calib, shift)
                if g and g[0] is not None:
                    pts[off] = {"post": round(g[0], 2),
                                "ctrl": None if g[1] is None else round(g[1], 2),
                                "local": bool(g[2])}
            if pts:
                curves.append({"t": round(t, 2), "pts": pts})
            if (i + 1) % 10 == 0:
                print("    %s %d/%d" % (v, i + 1, len(pick)))
    finally:
        cap.release()
    return {"calib": list(calib.hoop_rect), "goals_src": gsrc,
            "picked": len(pick), "curves": curves}


def agg(curves):
    """曲线 → 三组指标。"""
    prod_posts, curve_posts = [], []
    prod_local, curve_local = [], []
    best_off = []
    for c in curves:
        p = c["pts"]
        pv = [p[o]["post"] for o in PROD if o in p]
        if pv:
            prod_posts.append(max(pv))
            prod_local.append(any(p[o]["local"] for o in PROD if o in p))
        cv = [d["post"] for d in p.values()]
        if cv:
            curve_posts.append(max(cv))
            curve_local.append(any(d["local"] for d in p.values()))
            best_off.append(max(p, key=lambda o: p[o]["post"]))
    th = NM.NET_STATIC_TH
    out = {}
    for name, posts, locs in (("prod", prod_posts, prod_local),
                              ("curve", curve_posts, curve_local)):
        if not posts:
            continue
        s = sorted(posts)
        out[name] = {
            "n": len(posts),
            "p50": round(st.median(posts), 2),
            "p25": round(s[len(s) // 4], 2),
            "p75": round(s[3 * len(s) // 4], 2),
            "ge_th": round(100.0 * sum(1 for x in posts if x >= th) / len(posts), 1),
            "local": round(100.0 * sum(1 for x in locs if x) / len(locs), 1),
        }
    out["best_off_hist"] = dict(Counter(best_off))
    out["best_off_p50"] = round(st.median(best_off), 2) if best_off else None
    return out


def main():
    rep = {"th": NM.NET_STATIC_TH, "grid": GRID, "prod_offs": list(PROD),
           "scenes": {}}
    for v in SCENES:
        print("  scanning %s ..." % v)
        sc = scan_scene(v)
        sc["agg"] = agg(sc["curves"])
        rep["scenes"][v] = sc
        a = sc["agg"]
        print("    prod  p50=%-6s >=TH=%-5s local=%-5s | curve p50=%-6s >=TH=%-5s "
              "local=%-5s | best_off p50=%s"
              % (a["prod"]["p50"], a["prod"]["ge_th"], a["prod"]["local"],
                 a["curve"]["p50"], a["curve"]["ge_th"], a["curve"]["local"],
                 a["best_off_p50"]))
    wr(os.path.join(OUT, "phase_scan.json"), rep)

    # ---------------- 出表 ----------------
    L = []
    A = L.append
    A("# 测网相位扫描：11-07_1 vs 11-06_1")
    A("")
    A("每场 %d 条真球事件行；相位 = 相对事件时刻的窗起点，扫 %.2f ~ %.2fs。"
      % (N_EVENTS, GRID[0], GRID[-1]))
    A("")
    A("## 三窗取最大（现状） vs 全曲线取峰（假设改动）")
    A("")
    A("| 场 | 取法 | n | post p25 | post 中位 | post p75 | ≥%.1f 比例 | local 通过 |"
      % NM.NET_STATIC_TH)
    A("|---|---|---|---|---|---|---|---|")
    for v in SCENES:
        a = rep["scenes"][v]["agg"]
        for key, label in (("prod", "三窗取最大（现状）"), ("curve", "全曲线取峰")):
            s = a.get(key)
            if not s:
                continue
            A("| %s | %s | %d | %.2f | %.2f | %.2f | %.1f%% | %.1f%% |"
              % (v[5:], label, s["n"], s["p25"], s["p50"], s["p75"],
                 s["ge_th"], s["local"]))
    A("")
    A("## 逐相位的 post 中位（看峰落在哪）")
    A("")
    A("| 场 | " + " | ".join("%.2f" % o for o in GRID) + " |")
    A("|---|" + "---|" * len(GRID))
    for v in SCENES:
        per = {}
        for c in rep["scenes"][v]["curves"]:
            for o, d in c["pts"].items():
                per.setdefault(o, []).append(d["post"])
        cells = []
        for o in GRID:
            vals = per.get(o) or []
            mark = "*" if o in PROD else ""
            cells.append(("%.1f%s" % (st.median(vals), mark)) if vals else "-")
        A("| %s | %s |" % (v[5:], " | ".join(cells)))
    A("")
    A("`*` = 生产当前用的三个窗口（%.2f / %.2f / %.2f）。"
      % PROD)
    A("")
    A("## 每条事件自己的峰落在哪个相位")
    A("")
    A("| 场 | 相位中位 | 分布 |")
    A("|---|---|---|")
    for v in SCENES:
        a = rep["scenes"][v]["agg"]
        hist = ", ".join("%s→%d" % (k, c)
                         for k, c in sorted(a["best_off_hist"].items()))
        A("| %s | **%.2fs** | %s |" % (v[5:], a["best_off_p50"], hist))
    A("")
    A("## 判读")
    A("")
    for v in SCENES:
        a = rep["scenes"][v]["agg"]
        A("- **%s**：三窗取最大 → ≥TH %.1f%%，全曲线取峰 → ≥TH %.1f%%（%+.1f 个点）；"
          "post 中位 %.2f → %.2f；逐事件峰相位中位 **%.2fs**"
          % (v[5:], a["prod"]["ge_th"], a["curve"]["ge_th"],
             a["curve"]["ge_th"] - a["prod"]["ge_th"],
             a["prod"]["p50"], a["curve"]["p50"], a["best_off_p50"]))
    A("")
    md = "\n".join(L)
    with open(os.path.join(OUT, "phase_scan.md"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write(md)
    print()
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
