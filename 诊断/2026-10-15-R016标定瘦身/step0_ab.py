# -*- coding: utf-8 -*-
"""Step 0 只读对拍（R-016 标定向导瘦身 · 改码前数据门）

同一批出手窗口、同一套 shooter 算法（只调用、不修改），唯一变量是 HSV 色域种子：
  A  = 该场点选标定的 hsv_low/high（现状）
  D1 = 候选默认色域（收敛）：low=[0, 88, 60], high=[20, 235, 220]
  D2 = 候选默认色域（放宽）：low=[0, 88, 60], high=[20, 255, 255]

指标（每球）：检出候选点数 n_pts、静态占比、trace status、spot ok/bad、
release_t、release_xy、track_n。聚合对比 A 基线（并与线上存档 shooters.json
对齐作 sanity anchor）。不劣化（low/spot_bad 不增、release_t 基本一致）才放行改码。
"""
import json, os, sys, time
from dataclasses import replace

ROOT = r"D:/claude code/视频剪辑YOLO"
sys.path.insert(0, ROOT)

from bbh.models import Calibration
from bbh.shooter import (scan_release_window, trace_release, spot_bad_for,
                         _rescan_release, _scaled_rect, RIM_Y_RATIO, time_key)

GAMES = [
    ("09-04_1", r"D:/claude code/视频剪辑/work/2026-09-04_1"),  # 点选最宽/最差场
    ("10-10_1", r"D:/claude code/视频剪辑/work/2026-10-10_1"),  # 最近正常场
    ("06-28_1", os.path.join(ROOT, "work", "2025-06-28_1")),     # H 偏移最大(8~17)
]
D1 = ((0, 88, 60), (20, 235, 220))
D2 = ((0, 88, 60), (20, 255, 255))
N_PER_GAME = 14
OUT = os.path.join(ROOT, "诊断", "2026-10-15-R016标定瘦身")
os.makedirs(OUT, exist_ok=True)


def load_events(work):
    ev = json.load(open(os.path.join(work, "events.json"), encoding="utf-8"))
    evs = ev.get("events") or ev.get("rows") or ev
    return {time_key(e["t"]): e["t"] for e in evs}


def sample_keys(traces, n):
    """分层抽样：优先故障形态（spot_bad/low/extrap），其余补正常 high。"""
    bad = [k for k, v in traces.items() if v.get("spot") == "bad"]
    low = [k for k, v in traces.items() if v.get("status") == "low" and k not in bad]
    ex = [k for k, v in traces.items() if v.get("extrap") and k not in bad and k not in low]
    rv = [k for k, v in traces.items()
          if v.get("status") == "review" and k not in bad and k not in low and k not in ex]
    hi = [k for k, v in traces.items()
          if v.get("status") == "high" and v.get("spot") != "bad" and not v.get("extrap")]
    pick, quota = [], [5, 3, 3, 2]
    for pool, q in zip([bad, low, ex, rv], quota):
        for k in pool[:q]:
            if k not in pick:
                pick.append(k)
    for k in hi:
        if len(pick) >= n:
            break
        if k not in pick:
            pick.append(k)
    return pick[:n]


def static_frac(pts):
    """静态占比：8px 网格最大桶占比（顶棚/静物劫持的体感指标）。"""
    if not pts:
        return 0.0
    grid = {}
    for p in pts:
        c = (int(p.x) // 8, int(p.y) // 8)
        grid[c] = grid.get(c, 0) + 1
    return max(grid.values()) / len(pts)


def run_one(video, sc, t_goal):
    pts, size = scan_release_window(video, sc, t_goal, None)
    hx, hy, hw, hh = _scaled_rect(sc, size[0], size[1])
    hoop_c = (hx + hw / 2, hy + hh * RIM_Y_RATIO)
    tr = trace_release(pts, t_goal, hw, hoop_c, frame_size=size,
                       rescanner=lambda tr_, vx, xr, tref: _rescan_release(
                           video, sc, None, tr_, vx, xr, tref))
    return {
        "n_pts": len(pts), "static_frac": round(static_frac(pts), 3),
        "status": tr.status, "spot": "bad" if spot_bad_for(tr, sc, *size) else "ok",
        "release_t": tr.release_t,
        "release_xy": [round(v, 1) for v in tr.release_xy] if tr.release_xy else None,
        "track_n": tr.track_n, "extrap": bool(tr.extrap),
    }


def main():
    report = {}
    for name, work in GAMES:
        calib_d = json.load(open(os.path.join(work, "calibration.json"), encoding="utf-8"))
        calib = Calibration(**calib_d)
        video = json.load(open(os.path.join(work, "shooters.json"),
                               encoding="utf-8"))["video"]
        stored = json.load(open(os.path.join(work, "shooters.json"),
                                encoding="utf-8"))["traces"]
        ev_t = load_events(work)
        keys = [k for k in sample_keys(stored, N_PER_GAME) if k in ev_t]
        rows = []
        t_start = time.time()
        for k in keys:
            t_goal = ev_t[k]
            A = run_one(video, calib, t_goal)
            D1r = run_one(video, replace(calib, hsv_low=list(D1[0]), hsv_high=list(D1[1])), t_goal)
            D2r = run_one(video, replace(calib, hsv_low=list(D2[0]), hsv_high=list(D2[1])), t_goal)
            rows.append({"key": k, "stored": {f: stored[k].get(f) for f in
                          ("status", "spot", "extrap")},
                         "A": A, "D1": D1r, "D2": D2r})
            print(f"  {name} {k}: A={A['status']}/{A['spot']}(n={A['n_pts']}) "
                  f"D1={D1r['status']}/{D1r['spot']}(n={D1r['n_pts']}) "
                  f"D2={D2r['status']}/{D2r['spot']}(n={D2r['n_pts']})", flush=True)
        # 聚合
        def agg(rows_, tag):
            rs = [r[tag] for r in rows_]
            low = sum(1 for r in rs if r["status"] == "low")
            bad = sum(1 for r in rs if r["spot"] == "bad")
            npts = sum(r["n_pts"] for r in rs)
            sfr = sum(r["static_frac"] for r in rs) / max(1, len(rs))
            return {"n": len(rs), "low": low, "spot_bad": bad,
                    "n_pts_sum": npts, "static_frac_avg": round(sfr, 3)}
        rt_delta = []
        for r in rows:
            if r["A"]["release_t"] is not None and r["D1"]["release_t"] is not None:
                rt_delta.append(abs(r["A"]["release_t"] - r["D1"]["release_t"]))
        report[name] = {
            "hsv_point_selected": [calib.hsv_low, calib.hsv_high],
            "n_sampled": len(rows), "secs": round(time.time() - t_start, 1),
            "agg": {"A": agg(rows, "A"), "D1": agg(rows, "D1"), "D2": agg(rows, "D2")},
            "A_vs_D1_release_t_absdelta": {
                "max": round(max(rt_delta), 3) if rt_delta else None,
                "mean": round(sum(rt_delta) / len(rt_delta), 3) if rt_delta else None},
            "rows": rows,
        }
        print(f"[{name}] A={report[name]['agg']['A']} D1={report[name]['agg']['D1']} "
              f"D2={report[name]['agg']['D2']}", flush=True)
    json.dump(report, open(os.path.join(OUT, "step0_对比.json"), "w",
                           encoding="utf-8"), ensure_ascii=False, indent=1)
    print("saved ->", os.path.join(OUT, "step0_对比.json"))


if __name__ == "__main__":
    main()
