# -*- coding: utf-8 -*-
"""Phase 0 对拍数据门（R-017 溯源完全取代 HSV）——只读诊断，不改任何生产代码。

A 臂=现状 HSV（直接读 work/<场>/shooters.json，不重跑）；
B 臂=YOLO（best.pt@1024, classes=[0], conf 0.05 一次收全量 → 0.05/0.15/0.25
三档门离线套用）→ 门控候选 → 现有 trace_release 几何链（含 YOLO 版
rescanner，门控逻辑照抄 _rescan_release，帧从缓存就近取）。

对比口径：档位迁移矩阵 / spot_bad / 间隔离群 / low（真错率）/
双臂 release 一致率（|Δt|<=0.3s 且距离 <=1.5x筐宽）/ 灾难红线检查
（单场 low>5%、系统性同点聚集）。truth 人名标签无法自动比对命中，
产出分歧球抽检清单供人工裁决（方法学修正见 Phase0结论.md）。

面积门修正（跑前核对常量，2026-10-16）：A2 扫描口径 = [MIN_AREA=9px²,
0.5x筐宽²]（同 HSV），方案初稿的 0.10~0.35x筐宽² 是 GIF 近景口径，弃用。
"""
import json
import math
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from bbh.models import Calibration, CalibSegment  # noqa: E402
from bbh.pipeline import _normalize_segments  # noqa: E402
from bbh.detector import MIN_AREA, MAX_AREA_RATIO, RIM_Y_RATIO, BAND_RATIO  # noqa: E402
from bbh.shooter import (  # noqa: E402
    SCAN_WINDOW_S, SCAN_STEP, SCAN_MAX_BLOBS,
    RESCAN_DT_S, RESCAN_N, RESCAN_X_BAND_W, RESCAN_Y_ABOVE_W,
    RESCAN_V_TOL, RESCAN_MIN_AREA, RESCAN_MAX_AREA, RESCAN_MIN_RISE_PX,
    SPOT_EDGE_MARGIN, BallPos, spot_bad_for, trace_release,
    _seg_for_t, _detect_source, _scaled_rect,
)

WORK = Path(r"D:\claude code\视频剪辑\work")
OUT = Path(__file__).resolve().parent
FIELDS = ["2026-08-21_1", "2026-08-22_1", "2026-08-28_1", "2026-08-29_3",
          "2026-09-04_1", "2026-09-11_3", "2026-09-12_1", "2026-09-19_1",
          "2026-09-24_1", "2026-10-10_1"]
CONF_GATES = [0.05, 0.15, 0.25]
SNAP_TOL_S = 0.09          # rescanner 就近取帧容差（缓存网格 ~0.067s）
AREA_LO, AREA_HI = 0.10, 0.35   # 仅作记录：弃用（见文件头），实际用 MIN_AREA/0.5


def parse_key(key: str) -> float:
    m, s = key.split(":")
    return int(m) * 60 + float(s)


def load_segs(work_dir: Path) -> list:
    def mk(d):
        return Calibration(hoop_rect=tuple(d["hoop_rect"]),
                           hsv_low=tuple(d["hsv_low"]),
                           hsv_high=tuple(d["hsv_high"]),
                           frame_size=tuple(d["frame_size"]),
                           ref_image=d.get("ref_image"),
                           ref_hash=d.get("ref_hash"),
                           calib_t=d.get("calib_t", 0.0))
    c0 = json.loads((work_dir / "calibration.json").read_text(encoding="utf-8"))
    cb = mk(c0)
    segs = [CalibSegment(t0=cb.calib_t or 0.0, calib=cb)]
    for n in range(2, 30):
        p = work_dir / f"calibration_seg{n}.json"
        if not p.exists():
            break
        c2 = mk(json.loads(p.read_text(encoding="utf-8")))
        segs.append(CalibSegment(t0=c2.calib_t, calib=c2))
    return _normalize_segments(segs, None)


def yolo_model():
    from ultralytics import YOLO
    global INFER_KW
    w = str(ROOT / "bbh" / "weights" / "best.pt")
    m = YOLO(w)
    INFER_KW = dict(imgsz=1024, conf=0.05, iou=0.7, classes=[0],
                    device=0, verbose=False)
    try:
        m.predict(np.zeros((64, 64, 3), np.uint8), quantize=16, **INFER_KW)
        INFER_KW["quantize"] = 16      # FP16（与生产同口径）
    except Exception:
        pass                            # quantize 不被接受则默认精度
    return m


INFER_KW = {}


def infer(model, frame):
    r = model.predict(frame, **INFER_KW)[0]
    return [(float(b.xyxy[0][0]), float(b.xyxy[0][1]),
             float(b.xyxy[0][2]), float(b.xyxy[0][3]),
             float(b.conf[0])) for b in r.boxes]


def build_field_cache(field: str, model, limit: int = 0):
    """整场一次通读：所有球窗（6s, 隔2帧）的时间并集逐帧解码，
    命中帧跑 YOLO，缓存 {t: [(x0,y0,x1,y1,conf)...]}。"""
    work_dir = WORK / field
    sh = json.loads((work_dir / "shooters.json").read_text(encoding="utf-8"))
    events = sorted(parse_key(k) for k in sh["traces"])
    if limit > 0:
        events = events[:limit]
    src = _detect_source(sh["video"], str(work_dir))
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise RuntimeError(f"{field}: 打不开视频 {src}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    fps = fps if 10 < fps < 240 else 30.0

    need = []                                   # 排序的时刻表
    for t in events:
        t0 = max(0.0, t - SCAN_WINDOW_S)
        k = 0
        while True:
            tt = t0 + k * SCAN_STEP / fps
            if tt >= t - 1e-6:
                break
            need.append(tt)
            k += 1
    need.sort()
    print(f"[{field}] 事件 {len(events)} 球，并集帧 {len(need)}，"
          f"视频 {src}", flush=True)

    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, need[0] - 0.5) * 1000.0)
    use_pts = None
    fi = 0
    t_start = max(0.0, need[0] - 0.5)
    cache = {}
    pi = 0                                      # need 指针
    tol = 0.5 / fps
    while pi < len(need):
        if not cap.grab():
            break
        msec = cap.get(cv2.CAP_PROP_POS_MSEC)
        if use_pts is None and fi > 0:
            use_pts = msec > 0
        t = msec / 1000.0 if use_pts else t_start + fi / fps
        fi += 1
        if t < need[pi] - tol:
            continue
        # 吞掉被跳过（±半帧内无实帧）的时刻
        while pi < len(need) and need[pi] < t - tol:
            pi += 1
        if pi >= len(need):
            break
        if abs(t - need[pi]) <= tol:
            ok, frame = cap.retrieve()
            if ok:
                cache[round(t, 3)] = infer(model, frame)
            pi += 1
    cap.release()
    times = np.array(sorted(cache.keys()))
    print(f"[{field}] 缓存帧 {len(cache)}/{len(need)}（缺失 "
          f"{len(need) - len(cache)}）", flush=True)
    return events, fps, times, cache


def nearest(cache_times: np.ndarray, tt: float):
    if len(cache_times) == 0:
        return None
    i = int(np.searchsorted(cache_times, tt))
    best, bd = None, 1e9
    for j in (i - 1, i):
        if 0 <= j < len(cache_times):
            d = abs(cache_times[j] - tt)
            if d < bd:
                best, bd = cache_times[j], d
    return best if bd <= SNAP_TOL_S else None


def make_rescanner(cache, cache_times, sc, size, gate):
    """照抄 _rescan_release 门控（x 带/三连等速/y 单调/面积 40~4000），
    帧从 YOLO 缓存就近取（SNAP_TOL_S）。"""
    fw, fh = size
    hx, hy, hw, hh = _scaled_rect(sc, fw, fh)
    rim_y = hy + hh * RIM_Y_RATIO
    m = SPOT_EDGE_MARGIN * fh / 720.0
    max_a = hw * hw * MAX_AREA_RATIO

    def rescanner(t_rel, vx, x_ref, t_ref):
        frames = []
        for i in range(RESCAN_N):
            tt = t_rel + i * RESCAN_DT_S
            tk = nearest(cache_times, tt)
            if tk is None:
                continue
            dets = cache[tk]
            x_c = x_ref + vx * (tk - t_ref)
            blobs = []
            for (x0, y0, x1, y1, cf) in dets:
                if cf < gate:
                    continue
                cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
                a = (x1 - x0) * (y1 - y0)
                if not (RESCAN_MIN_AREA <= a <= RESCAN_MAX_AREA):
                    continue
                if abs(cx - x_c) > RESCAN_X_BAND_W * hw:
                    continue
                if not (rim_y - RESCAN_Y_ABOVE_W * hw <= cy <= fh - m):
                    continue
                blobs.append((cx, cy))
            if blobs:
                frames.append((tk, blobs))
        best = None
        for i in range(len(frames) - 2):
            (ta, ba), (tb, bb), (tc, bc) = frames[i], frames[i + 1], frames[i + 2]
            for xa, ya in ba:
                for xb, yb in bb:
                    v1 = (xb - xa) / (tb - ta)
                    if abs(v1 - vx) >= RESCAN_V_TOL:
                        continue
                    for xc, yc in bc:
                        if (abs((xc - xb) / (tc - tb) - vx) >= RESCAN_V_TOL
                                or not (ya >= yb >= yc
                                        and ya - yc >= RESCAN_MIN_RISE_PX)):
                            continue
                        score = (abs(v1 - vx)
                                 + abs((xc - xb) / (tc - tb) - vx))
                        if best is None or score < best[0]:
                            best = (score, xa, ya)
        return None if best is None else (best[1], best[2])
    return rescanner


def pts_for_event(e_t, sc, size, cache_times, cache, gate, hoop_w):
    fw, fh = size
    hx, hy, hw, hh = _scaled_rect(sc, fw, fh)
    rim_y = hy + hh * RIM_Y_RATIO
    band = hh * BAND_RATIO
    max_a = hw * hw * MAX_AREA_RATIO
    lo_t, hi_t = e_t - SCAN_WINDOW_S, e_t
    i0 = int(np.searchsorted(cache_times, lo_t))
    out = []
    for tk in cache_times[i0:]:
        if tk >= hi_t:
            break
        cands = []
        for (x0, y0, x1, y1, cf) in cache[tk]:
            if cf < gate:
                continue
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            a = (x1 - x0) * (y1 - y0)
            if a < MIN_AREA or a > max_a:
                continue
            if abs(cy - rim_y) < band:
                continue
            cands.append((cf, cx, cy, a))
        cands.sort(reverse=True)                 # conf 优先，与 HSV 面积序不同
        for cf, cx, cy, a in cands[:SCAN_MAX_BLOBS]:
            out.append(BallPos(t=tk, x=cx, y=cy, area=a, fi=0))
    return out


def run_field(field: str, model, limit: int = 0):
    t_start = time.time()
    work_dir = WORK / field
    sh = json.loads((work_dir / "shooters.json").read_text(encoding="utf-8"))
    segs = load_segs(work_dir)
    events, fps, cache_times, cache = build_field_cache(field, model, limit)
    # 视频帧尺寸：从缓存任一帧取不到，直接读 cap 元数据
    src = _detect_source(sh["video"], str(work_dir))
    cap0 = cv2.VideoCapture(src)
    fw = int(cap0.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap0.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap0.release()
    size = (fw, fh)

    keys_by_t = {parse_key(k): k for k in sh["traces"]}
    if limit > 0:
        events = events[:limit]
        print(f"[{field}] ⚠ LIMIT={limit} 冒烟模式", flush=True)
    hw_by_key = {}
    for e_t in events:
        sc0 = _seg_for_t(segs, e_t).calib
        _, _, hw0, _ = _scaled_rect(sc0, fw, fh)
        hw_by_key[keys_by_t[e_t]] = hw0
    results = {}      # gate -> {key: {...}}
    for gate in CONF_GATES:
        results[gate] = {}
    for n, e_t in enumerate(events, 1):
        key = keys_by_t[e_t]
        sc = _seg_for_t(segs, e_t).calib
        hx, hy, hoop_w, hh = _scaled_rect(sc, fw, fh)
        hoop_c = (hx + hoop_w / 2.0, hy + hh * RIM_Y_RATIO)
        for gate in CONF_GATES:
            resc = make_rescanner(cache, cache_times, sc, size, gate)
            pts = pts_for_event(e_t, sc, size, cache_times, cache,
                                gate, hoop_w)
            tr = trace_release(pts, e_t, hoop_w, hoop_c, frame_size=size,
                               rescanner=resc)
            iv = (e_t - tr.release_t
                  if tr.release_t is not None else None)
            results[gate][key] = {
                "status": tr.status,
                "release_t": tr.release_t,
                "release_xy": (list(tr.release_xy)
                               if tr.release_xy else None),
                "extrap": bool(tr.extrap),
                "spot": ("bad" if spot_bad_for(tr, sc, fw, fh) else "ok"),
                "iv": iv,
            }
        if n % 25 == 0:
            print(f"[{field}] {n}/{len(events)} 球 "
                  f"({time.time()-t_start:.0f}s)", flush=True)

    used = set(results[CONF_GATES[0]].keys())
    base = {}
    for k in used:
        t = sh["traces"][k]
        e_t = parse_key(k)
        iv = (e_t - t["release_t"]) if t.get("release_t") is not None else None
        base[k] = {"status": t.get("status"), "spot": t.get("spot"),
                   "release_t": t.get("release_t"),
                   "release_xy": t.get("release_xy"), "iv": iv,
                   "extrap": bool(t.get("extrap"))}

    def err_stats(tr):
        n = len(tr)
        low = sum(1 for v in tr.values() if v["status"] == "low")
        spot = sum(1 for v in tr.values()
                   if v["status"] != "low" and v["spot"] == "bad")
        iv_out = sum(1 for v in tr.values()
                     if v["status"] != "low" and v["spot"] != "bad"
                     and (v["iv"] is None or v["iv"] < 0.3 or v["iv"] > 4.0))
        high = sum(1 for v in tr.values() if v["status"] == "high")
        return {"n": n, "low": low, "spot": spot, "iv_out": iv_out,
                "err_pct": round(100 * (low + spot + iv_out) / max(n, 1), 2),
                "high_pct": round(100 * high / max(n, 1), 2)}

    summary = {"field": field, "base": err_stats(base)}
    for gate in CONF_GATES:
        summary[f"g{gate}"] = err_stats(results[gate])
        # 迁移矩阵 vs 基线
        mat = {}
        for k in base:
            a, b = base[k]["status"], results[gate][k]["status"]
            mat[f"{a}->{b}"] = mat.get(f"{a}->{b}", 0) + 1
        summary[f"g{gate}"]["matrix"] = mat
        # 双臂一致率（基线 release vs YOLO release）
        agree = disagree = 0
        disp = []
        for k in base:
            rb, ry = base[k]["release_xy"], results[gate][k]["release_xy"]
            tb, ty = base[k]["release_t"], results[gate][k]["release_t"]
            if rb is None or ry is None:
                continue
            d = math.hypot(rb[0] - ry[0], rb[1] - ry[1])
            if abs((tb or 0) - (ty or 0)) <= 0.3 and d <= 1.5 * hw_by_key.get(k, 50):
                agree += 1
            else:
                disagree += 1
                if len(disp) < 400:
                    disp.append({"key": k, "d": round(d, 1),
                                 "base": rb, "yolo": ry,
                                 "status_b": base[k]["status"],
                                 "status_y": results[gate][k]["status"]})
        both = agree + disagree
        summary[f"g{gate}"]["agree_pct"] = (round(100 * agree / both, 2)
                                            if both else None)
        summary[f"g{gate}"]["disagree_sample"] = disp[:60]
        # 灾难红线：单场 low 率 + 同点聚集
        low_pct = 100 * summary[f"g{gate}"]["low"] / max(len(base), 1)
        summary[f"g{gate}"]["low_pct"] = round(low_pct, 2)
        cells = {}
        for v in results[gate].values():
            if v["release_xy"]:
                c = (int(v["release_xy"][0] // 40), int(v["release_xy"][1] // 40))
                cells[c] = cells.get(c, 0) + 1
        top = max(cells.values()) if cells else 0
        summary[f"g{gate}"]["max_cell_pct"] = round(100 * top / max(len(base), 1), 2)

    (OUT / f"phase0_{field}{'_smoke' if limit else ''}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[{field}] 完成 {time.time()-t_start:.0f}s → "
          f"base 真错 {summary['base']['err_pct']}% / "
          f"g0.05 {summary['g0.05']['err_pct']}% / "
          f"g0.15 {summary['g0.15']['err_pct']}% / "
          f"g0.25 {summary['g0.25']['err_pct']}%", flush=True)
    return summary


def main():
    args = sys.argv[1:]
    only = [a for a in args if not a.startswith("--")] or FIELDS
    limit = int(os.environ.get("PHASE0_LIMIT", "0"))
    model = yolo_model()
    allsum = {}
    for f in only:
        outp = OUT / f"phase0_{f}.json"
        if outp.exists() and not limit:
            print(f"[{f}] 已有结果，跳过", flush=True)
            allsum[f] = json.loads(outp.read_text(encoding="utf-8"))
            continue
        try:
            allsum[f] = run_field(f, model, limit)
        except Exception as ex:  # noqa: BLE001
            print(f"[{f}] 失败：{ex}", flush=True)
            import traceback
            traceback.print_exc()
    # 总表
    tot = {}
    for tag in ["base", "g0.05", "g0.15", "g0.25"]:
        n = sum(s[tag]["n"] for s in allsum.values() if tag in s)
        if not n:
            continue
        low = sum(s[tag]["low"] for s in allsum.values() if tag in s)
        spot = sum(s[tag]["spot"] for s in allsum.values() if tag in s)
        iv = sum(s[tag]["iv_out"] for s in allsum.values() if tag in s)
        high = sum(round(s[tag]["high_pct"] / 100 * s[tag]["n"])
                   for s in allsum.values() if tag in s)
        tot[tag] = {"n": n, "low": low, "spot": spot, "iv_out": iv,
                    "err_pct": round(100 * (low + spot + iv) / n, 2),
                    "high_pct": round(100 * high / n, 2)}
    if limit:
        print(f"⚠ LIMIT={limit} 冒烟运行，不写总表", flush=True)
        return
    print("\n=== Phase 0 总表 ===", flush=True)
    print(json.dumps(tot, ensure_ascii=False, indent=1), flush=True)
    (OUT / "phase0_总表.json").write_text(
        json.dumps({"fields": allsum, "total": tot},
                   ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
