# -*- coding: utf-8 -*-
r"""假绿对照：早峰到底是「真网动」还是「球掠过筐口」。

为什么需要这一步
----------------
`phase_scan.py` 发现两场的网区帧差曲线都是**单调衰减**，最强信号在最早的 0.15s，
而生产的三窗 `(0.45, 0.70, 0.95)` 全落在尾巴上。11-07 若改成「全曲线取峰」，
`post≥5` 的比例从 57.5% 跳到 97.5%。

但用 `positions.json` 的球心+球半径算重叠，0.15s 时球已经**在网区下沿之外**
（重叠中位 0.5% / 0.0%）—— 所以不是「球挡住了 ROI」。剩下两条互斥解释：

  H1 早窗是**真网动**（球刚进网、网被撑开的最大形变）→ 假绿应该**低** → 早窗有区分度
  H2 早窗是**球掠过筐口**（运动模糊/压缩块，球就在下沿外半个筐高）→ 假绿**同样高**
     → 改取峰等于把网分区分度清零

判定
----
对**假绿**（不在 selection 里、且档位 high 的行 —— 只有它们真的跟真球抢位置）跑
同一套 17 相位曲线，算每个相位上「真球 vs 假绿」的 AUC：

  AUC(0.15) 明显高于 AUC(0.45) → 早窗区分度更好 → 值得改 net_motion
  AUC(0.15) 反而更低          → 早窗是球掠过       → 死了，维持现状

真球样本与 `phase_scan.py` 完全同一批（`rows[::3][:40]`）；假绿取全部 high 档
（11-07 只有 30 条，全取，不用抽）。

只读：不改生产代码、不写 work、不动 sidecar。
用法：.venv\Scripts\python 诊断\2026-11-10-网区ROI平移\phase_ctrl.py
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
sys.path.insert(0, OUT)

import bbh.net_motion as NM                                          # noqa: E402
from bbh.models import Calibration                                   # noqa: E402
from phase_scan import GRID, PROD, N_EVENTS, SEED_STEP               # noqa: E402
from roi_shift import ARCHIVE, WORK, load_goals, rd, true_rows, wr   # noqa: E402

SCENES = ["2026-11-06_1", "2026-11-07_1"]
FP_STATUS = ("high",)          # 假绿只取 high：只有它们真的跟真球抢位置


def fp_rows(events, goals):
    """假绿行 = 没被真值认领、且档位在 FP_STATUS 里的行。"""
    tr = true_rows(events, goals)
    tri = set(id(e) for e in tr)
    return [e for e in events if id(e) not in tri and e["status"] in FP_STATUS]


def measure(v, rows):
    """对给定事件行跑 17 相位曲线 → {t: {off: post}}。"""
    calib = Calibration.load(os.path.join(WORK, v, "calibration.json"))
    pos = rd(os.path.join(WORK, v, "positions.json"))
    shift = pos.get("shift") or []
    video = os.path.join(ARCHIVE, v + ".mp4")
    if not os.path.exists(video):
        video = pos.get("video") or video
    cap = NM._open_cap(video, None)
    if cap is None:
        raise RuntimeError("打不开视频：" + video)
    out = []
    try:
        for i, e in enumerate(rows):
            t = e["t"]
            pts = {}
            for off in GRID:
                g = NM._window_scores(cap, t + off, calib, shift)
                if g and g[0] is not None:
                    pts[off] = round(g[0], 2)
            if pts:
                out.append({"t": round(t, 2), "pts": pts})
            if (i + 1) % 15 == 0:
                print("    %s %d/%d" % (v, i + 1, len(rows)))
    finally:
        cap.release()
    return out


def auc(pos, neg):
    """P(真球分 > 假绿分)，Mann-Whitney，并列记 0.5。"""
    if not pos or not neg:
        return None
    s = 0.0
    for a in pos:
        for b in neg:
            s += 1.0 if a > b else (0.5 if a == b else 0.0)
    return s / (len(pos) * len(neg))


def curve_vals(curves, off=None, mode="max"):
    """每条曲线取一个数：某一相位 / 全曲线最大 / 生产三窗最大。"""
    vals = []
    for c in curves:
        p = c["pts"]
        if off is not None:
            if off in p:
                vals.append(p[off])
        elif mode == "prod":
            v = [p[o] for o in PROD if o in p]
            if v:
                vals.append(max(v))
        else:
            if p:
                vals.append(max(p.values()))
    return vals


def main():
    rep = {"th": NM.NET_STATIC_TH, "grid": GRID, "prod_offs": list(PROD),
           "fp_status": list(FP_STATUS), "scenes": {}}
    for v in SCENES:
        ev = rd(os.path.join(WORK, v, "events.json"))["events"]
        goals, _src = load_goals(v)
        tr = true_rows(ev, goals)
        tr.sort(key=lambda e: e["t"])
        tpick = tr[::SEED_STEP][:N_EVENTS]
        fpick = fp_rows(ev, goals)
        fpick.sort(key=lambda e: e["t"])

        print("  %s 真球 %d 条 / 假绿 %d 条 (%s)"
              % (v, len(tpick), len(fpick),
                 dict(Counter(e["status"] for e in fpick))))
        ct = measure(v, tpick)
        cf = measure(v, fpick)

        per = {}
        for off in GRID:
            a = curve_vals(ct, off=off)
            b = curve_vals(cf, off=off)
            per["%.2f" % off] = {
                "tp_n": len(a), "fp_n": len(b),
                "tp_p50": round(st.median(a), 2) if a else None,
                "fp_p50": round(st.median(b), 2) if b else None,
                "auc": (round(auc(a, b), 3) if a and b else None),
            }
        a_prod, b_prod = curve_vals(ct, mode="prod"), curve_vals(cf, mode="prod")
        a_cv, b_cv = curve_vals(ct), curve_vals(cf)
        rep["scenes"][v] = {
            "tp_n": len(tpick), "fp_n": len(fpick),
            "per_phase": per,
            "prod": {"tp_p50": round(st.median(a_prod), 2),
                     "fp_p50": round(st.median(b_prod), 2),
                     "auc": round(auc(a_prod, b_prod), 3)},
            "curve": {"tp_p50": round(st.median(a_cv), 2),
                      "fp_p50": round(st.median(b_cv), 2),
                      "auc": round(auc(a_cv, b_cv), 3)},
        }
        r = rep["scenes"][v]
        print("    prod  AUC=%.3f (真 %.2f / 假 %.2f)   curve AUC=%.3f (真 %.2f / 假 %.2f)"
              % (r["prod"]["auc"], r["prod"]["tp_p50"], r["prod"]["fp_p50"],
                 r["curve"]["auc"], r["curve"]["tp_p50"], r["curve"]["fp_p50"]))
    wr(os.path.join(OUT, "phase_ctrl.json"), rep)

    # ---------------- 出表 ----------------
    L = []
    A = L.append
    A("# 假绿对照：早峰是真网动还是球掠过筐口")
    A("")
    A("假绿 = 未被真值认领且 `status=high` 的行（只有它们真的跟真球抢位置）。")
    A("真球样本与 `phase_scan.py` 同一批。AUC = P(真球分 > 假绿分)，0.5=无区分度。")
    A("")
    A("## 逐相位")
    A("")
    A("| 场 | 相位 | 真球 post 中位 | 假绿 post 中位 | 差值 | **AUC** |")
    A("|---|---|---|---|---|---|")
    for v in SCENES:
        for off in GRID:
            d = rep["scenes"][v]["per_phase"]["%.2f" % off]
            if d["tp_p50"] is None or d["fp_p50"] is None:
                continue
            mark = "`*`" if off in PROD else ""
            A("| %s | %.2f%s | %.2f | %.2f | %+.2f | **%.3f** |"
              % (v[5:], off, mark, d["tp_p50"], d["fp_p50"],
                 d["tp_p50"] - d["fp_p50"], d["auc"]))
    A("")
    A("## 三种取法的总分（决定性对比）")
    A("")
    A("| 场 | 取法 | 真球 post 中位 | 假绿 post 中位 | 差值 | **AUC** |")
    A("|---|---|---|---|---|---|")
    for v in SCENES:
        r = rep["scenes"][v]
        for key, label in (("prod", "三窗取最大（现状）"), ("curve", "全曲线取峰")):
            s = r[key]
            A("| %s | %s | %.2f | %.2f | %+.2f | **%.3f** |"
              % (v[5:], label, s["tp_p50"], s["fp_p50"],
                 s["tp_p50"] - s["fp_p50"], s["auc"]))
    A("")
    md = "\n".join(L)
    with open(os.path.join(OUT, "phase_ctrl.md"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write(md)
    print()
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
