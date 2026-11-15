# -*- coding: utf-8 -*-
r"""12 场相位扫描：早窗（0.15 / 0.25s）是否普遍优于生产三窗（0.45 / 0.70 / 0.95s）。

为什么需要这步
--------------
`phase_scan.py` + `phase_ctrl.py` 在两场（11-06 / 11-07）上发现：
  · 网区帧差曲线单调衰减，真峰在事件后 0.15s，生产三窗全落在尾巴上；
  · 「全曲线取峰」是错的（假绿同步抬高，AUC 反而降）；
  · 「固定早窗」两场 AUC 都更好（11-06 .745/.759 vs 现状 .671；11-07 .679 vs .469）。

但样本只有 40 真球 / 30 假绿，AUC 标准误 0.06~0.07，且 11-06 的 +0.088 仅
1.2 个标准误 → **不显著**。所以本脚本把同一套测量铺到全部可用场次，得到
「场 × 相位」的 AUC 矩阵，看早窗优势是**普遍规律**还是**单场现象**。

判定口径
--------
AUC = P(真球 post > 假绿 post)，Mann-Whitney 并列记 0.5，0.5 = 无区分度。
逐场比较「早窗最优相位」与「生产三窗取最大」，再按场做符号检验计数。
  · 多数场早窗更好 → 值得出方案（改窗口 + 重标 NET_STATIC_TH / NET_SCORE_STATIC）
  · 只有个别场好    → 单场现象，维持现状

样本
----
真球 = 真值配对上的事件行，`sorted(t)[::SEED_STEP][:N_EVENTS]`；
假绿 = 未被真值认领、且 `status=high` 的行（只有它们真的跟真球抢位置），全取，上限 N_FP。

只读：不改生产代码、不写 work、不动 sidecar。
用法：
  .venv\Scripts\python 诊断\2026-11-10-网区ROI平移\phase_sweep12.py --dry   # 只盘点
  .venv\Scripts\python 诊断\2026-11-10-网区ROI平移\phase_sweep12.py         # 全量
"""
import argparse
import json
import os
import statistics as st
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
OUT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, OUT)

import bbh.net_motion as NM                                          # noqa: E402
from bbh.models import Calibration                                   # noqa: E402
from roi_shift import WORK, load_goals, rd, true_rows, wr            # noqa: E402

OLD_ROOT = r"D:\claude code\yolo\eval_out\v20_c03_s1"

# 官方 12 场（老六场 + 新六场），11-07 只做监测不入统计
SCENES = [
    "2026-08-22_1", "2026-08-28_1", "2026-08-29_3",
    "2026-09-04_1", "2026-09-11_3", "2026-09-12_1",
    "2026-10-17_1", "2026-10-23_1", "2026-10-24_1",
    "2026-10-30_1", "2026-10-31_1", "2026-11-06_1",
]

GRID = (0.15, 0.25, 0.35, 0.45, 0.60, 0.70, 0.85, 0.95)   # 含生产三窗
PROD = tuple(NM.NET_POST_OFFS)                              # (0.45, 0.70, 0.95)
EARLY = (0.15, 0.25, 0.35)                                  # 候选早窗
N_EVENTS = 40
N_FP = 40
SEED_STEP = 3


def scene_root(v):
    """产物根：优先本仓 work，否则老场 eval_out。"""
    if os.path.exists(os.path.join(WORK, v, "events.json")):
        return WORK, "work"
    p = os.path.join(OLD_ROOT, v)
    if os.path.exists(os.path.join(p, "events.json")):
        return OLD_ROOT, "eval_out"
    return None, None


def video_of(v, pos):
    """视频路径：positions.video 优先，其次归档目录。"""
    cands = []
    if pos.get("video"):
        cands.append(pos["video"])
    cands.append(os.path.join(r"D:\claude code\1视频获取\归档", v + ".mp4"))
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def inventory(v):
    """盘点一场：产物 / 视频 / 标定 / 样本量。不读视频。"""
    root, tag = scene_root(v)
    rec = {"video": v, "root_tag": tag, "ok": False}
    if root is None:
        rec["why"] = "无产物（events.json 缺失）"
        return rec
    dp = os.path.join(root, v)
    rec["dir"] = dp
    pos = rd(os.path.join(dp, "positions.json"))
    vid = video_of(v, pos)
    rec["video_path"] = vid
    if not vid:
        rec["why"] = "源视频不在本机"
        return rec
    cp = os.path.join(dp, "calibration.json")
    rec["calib_file"] = os.path.exists(cp)
    rec["calib_inline"] = bool(pos.get("calib"))
    if not (rec["calib_file"] or rec["calib_inline"]):
        rec["why"] = "无标定（calibration.json 与 positions.calib 均缺）"
        return rec
    ev = rd(os.path.join(dp, "events.json"))["events"]
    goals, src = load_goals(v)
    rec["truth_src"] = src
    rec["n_goals"] = len(goals)
    rec["n_events"] = len(ev)
    tr = true_rows(ev, goals)
    tr.sort(key=lambda e: e["t"])
    rec["n_true_rows"] = len(tr)
    tri = set(id(e) for e in tr)
    fpH = [e for e in ev if id(e) not in tri and e["status"] == "high"]
    fpA = [e for e in ev if id(e) not in tri]
    rec["n_fp_high"] = len(fpH)
    rec["n_fp_all"] = len(fpA)
    rec["ok"] = bool(tr) and bool(fpH)
    if not tr:
        rec["why"] = "无真球行"
    elif not fpH:
        rec["why"] = "无 high 假绿（无法算 AUC）"
    return rec


def calib_of(dp, v, pos):
    cp = os.path.join(dp, "calibration.json")
    if os.path.exists(cp):
        return Calibration.load(cp)
    raise RuntimeError("本脚本只处理有 calibration.json 的场：" + v)


def measure(dp, v, rows):
    """对给定事件行跑相位曲线 → [{t, pts:{off: post}}]。"""
    pos = rd(os.path.join(dp, "positions.json"))
    calib = calib_of(dp, v, pos)
    shift = pos.get("shift") or []
    vid = video_of(v, pos)
    cap = NM._open_cap(vid, None)
    if cap is None:
        raise RuntimeError("打不开视频：" + vid)
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
            if (i + 1) % 20 == 0:
                print("      %s %d/%d" % (v, i + 1, len(rows)), flush=True)
    finally:
        cap.release()
    return out


def auc(pos, neg):
    if not pos or not neg:
        return None
    s = 0.0
    for a in pos:
        for b in neg:
            s += 1.0 if a > b else (0.5 if a == b else 0.0)
    return s / (len(pos) * len(neg))


def vals(curves, off=None, mode=None):
    out = []
    for c in curves:
        p = c["pts"]
        if off is not None:
            if off in p:
                out.append(p[off])
        elif mode == "prod":
            x = [p[o] for o in PROD if o in p]
            if x:
                out.append(max(x))
        elif p:
            out.append(max(p.values()))
    return out


def run_one(dp, v, inv):
    ev = rd(os.path.join(dp, "events.json"))["events"]
    goals, _ = load_goals(v)
    tr = true_rows(ev, goals)
    tr.sort(key=lambda e: e["t"])
    tpick = tr[::SEED_STEP][:N_EVENTS]
    tri = set(id(e) for e in tr)
    fpick = [e for e in ev if id(e) not in tri and e["status"] == "high"]
    fpick.sort(key=lambda e: e["t"])
    fpick = fpick[:N_FP]

    print("  %s  真球 %d / 假绿 %d" % (v, len(tpick), len(fpick)), flush=True)
    ct = measure(dp, v, tpick)
    cf = measure(dp, v, fpick)

    per = {}
    for off in GRID:
        a, b = vals(ct, off=off), vals(cf, off=off)
        per["%.2f" % off] = {
            "tp_n": len(a), "fp_n": len(b),
            "tp_p50": round(st.median(a), 2) if a else None,
            "fp_p50": round(st.median(b), 2) if b else None,
            "ge_th_tp": round(100.0 * sum(1 for x in a if x >= NM.NET_STATIC_TH) / len(a), 1) if a else None,
            "ge_th_fp": round(100.0 * sum(1 for x in b if x >= NM.NET_STATIC_TH) / len(b), 1) if b else None,
            "auc": round(auc(a, b), 3) if (a and b) else None,
        }
    a_p, b_p = vals(ct, mode="prod"), vals(cf, mode="prod")
    prod = {"tp_p50": round(st.median(a_p), 2) if a_p else None,
            "fp_p50": round(st.median(b_p), 2) if b_p else None,
            "auc": round(auc(a_p, b_p), 3) if (a_p and b_p) else None}
    # 早窗最优（在 EARLY 里挑 AUC 最大的）
    best = max((o for o in EARLY if per["%.2f" % o]["auc"] is not None),
               key=lambda o: per["%.2f" % o]["auc"], default=None)
    return {"tp_n": len(tpick), "fp_n": len(fpick), "per_phase": per,
            "prod": prod, "best_early": best,
            "best_early_auc": per["%.2f" % best]["auc"] if best else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只盘点，不读视频")
    a = ap.parse_args()

    inv = {v: inventory(v) for v in SCENES}
    print("== 盘点 ==")
    print("| 场 | 产物 | 视频 | 标定 | 真值 | 真球行 | high假绿 | 可用 |")
    print("|---|---|---|---|---|---|---|---|")
    for v in SCENES:
        r = inv[v]
        print("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            v[5:], r.get("root_tag") or "-",
            "有" if r.get("video_path") else "缺",
            ("文件" if r.get("calib_file") else ("内联" if r.get("calib_inline") else "缺")) if r.get("root_tag") else "-",
            r.get("truth_src") or "-", r.get("n_true_rows") or "-",
            r.get("n_fp_high") or "-", ("OK" if r.get("ok") else "跳过：" + r.get("why", ""))))

    usable = [v for v in SCENES if inv[v].get("ok")]
    print("\n可用 %d / %d 场：%s" % (len(usable), len(SCENES), ", ".join(usable)))
    if a.dry:
        wr(os.path.join(OUT, "phase_sweep12_inventory.json"),
           {"scenes": inv, "usable": usable})
        return 0

    rep = {"th": NM.NET_STATIC_TH, "grid": list(GRID), "prod_offs": list(PROD),
           "early": list(EARLY), "scenes": {}, "skipped": {},
           "inventory": inv}
    for v in SCENES:
        if not inv[v].get("ok"):
            rep["skipped"][v] = inv[v].get("why", "")
            continue
        rep["scenes"][v] = run_one(inv[v]["dir"], v, inv[v])
        r = rep["scenes"][v]
        print("    prod AUC=%.3f  best_early=%.2f AUC=%.3f"
              % (r["prod"]["auc"], r["best_early"] or -1, r["best_early_auc"] or -1),
              flush=True)
    wr(os.path.join(OUT, "phase_sweep12.json"), rep)

    # ---------------- 出表 ----------------
    L = []
    A = L.append
    A("# 相位扫描：早窗是否普遍优于生产三窗（12 场计划，%d 场可跑）"
      % len(rep["scenes"]))
    A("")
    A("老六场源视频已不在本机（归档只保留 10 月后），实际可跑新六场。")
    A("")
    A("AUC = P(真球 post > 假绿 post)，0.5 = 无区分度。假绿 = 未认领的 `status=high` 行。")
    A("`*` = 生产三窗 `NET_POST_OFFS`。每个场真球/假绿各题头列显示样本量。")
    A("")
    A("## 1 逐场 × 相位 AUC")
    A("")
    hdr = "| 场 | n真/假 | " + " | ".join(
        ("%.2f" % o) + ("`*`" if o in PROD else "") for o in GRID) + " | 三窗取最大 |"
    A(hdr)
    A("|---" * (len(GRID) + 3) + "|")
    for v in SCENES:
        if v not in rep["scenes"]:
            continue
        r = rep["scenes"][v]
        cells = []
        for o in GRID:
            d = r["per_phase"]["%.2f" % o]
            cells.append("-" if d["auc"] is None else "%.3f" % d["auc"])
        A("| %s | %d/%d | %s | **%.3f** |" % (
            v[5:], r["tp_n"], r["fp_n"], " | ".join(cells), r["prod"]["auc"]))
    A("")
    A("## 2 早窗 vs 现状（符号检验）")
    A("")
    A("| 场 | 现状三窗 AUC | 早窗最优相位 | 早窗 AUC | 差 | 谁更好 |")
    A("|---|---|---|---|---|---|")
    win = {"early": 0, "prod": 0, "tie": 0}
    for v in SCENES:
        if v not in rep["scenes"]:
            continue
        r = rep["scenes"][v]
        d = r["best_early_auc"] - r["prod"]["auc"]
        who = "早窗" if d > 0.005 else ("现状" if d < -0.005 else "持平")
        win["early" if d > 0.005 else ("prod" if d < -0.005 else "tie")] += 1
        A("| %s | %.3f | %.2f | %.3f | %+.3f | %s |" % (
            v[5:], r["prod"]["auc"], r["best_early"], r["best_early_auc"], d, who))
    A("")
    A("**合计：早窗更好 %d 场 / 现状更好 %d 场 / 持平 %d 场**"
      % (win["early"], win["prod"], win["tie"]))
    A("")
    A("## 3 绝对相位胜出计数（不挑最优，看规律性）")
    A("")
    A("| 相位 | 全场 AUC 中位 | 优于现状的场数 | 优于 0.5 的场数 |")
    A("|---|---|---|---|")
    for o in GRID:
        a_ = [rep["scenes"][v]["per_phase"]["%.2f" % o]["auc"]
              for v in rep["scenes"]
              if rep["scenes"][v]["per_phase"]["%.2f" % o]["auc"] is not None]
        p_ = [rep["scenes"][v]["prod"]["auc"] for v in rep["scenes"]]
        if not a_:
            continue
        better = sum(1 for v in rep["scenes"]
                     if rep["scenes"][v]["per_phase"]["%.2f" % o]["auc"] is not None
                     and rep["scenes"][v]["per_phase"]["%.2f" % o]["auc"] > rep["scenes"][v]["prod"]["auc"])
        above = sum(1 for x in a_ if x > 0.5)
        A("| %.2f%s | %.3f | %d/%d | %d/%d |" % (
            o, "`*`" if o in PROD else "", st.median(a_), better, len(p_), above, len(a_)))
    A("")
    if rep["skipped"]:
        A("## 4 跳过场次")
        A("")
        for v, why in rep["skipped"].items():
            A("- `%s` —— %s" % (v, why))
        A("")
    md = "\n".join(L)
    with open(os.path.join(OUT, "phase_sweep12.md"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write(md)
    print()
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
