# -*- coding: utf-8 -*-
"""验证 A2：定位镜头真实突变的帧对，再判断位移场是"全局平移"还是"摇镜视差"。

思路：shift 采样 5s 一条，突变可能只有 1~2 秒。先从 shift 表找相邻采样
跳变最大的窗口，再在该窗口内逐帧算帧间位移，锁定真正突变的那一对帧，
最后做九宫格位移场分析：
  - 各格位移极差 < 2px  → 全局平移（Δ_hoop 可直接用于全部球点位）
  - 各格位移极差 ≥ 10px → 摇镜/视差（只能做低频漂移校正）

另统计突变是"瞬时"还是"斜坡"：逐帧 dy 曲线能看出 5s 内摊开了多少帧。
"""
import json
import os

import cv2
import numpy as np

def load(name):
    for p in (f"work/{name}/positions.json",
              rf"D:/claude code/视频剪辑/work/{name}/positions.json"):
        if os.path.exists(p):
            return json.load(open(p, encoding="utf-8"))
    return None


def segs_of(doc):
    s = doc.get("segments")
    if s is not None:
        return s
    return [{"t0": 0.0, "t1": None, "shift": doc.get("shift") or [],
             "calib": doc.get("calib")}]


def jump_window(sh, topk=2):
    """shift 表里相邻采样跳变最大的若干窗口 → [(t_a, t_b, ddx, ddy)]。"""
    out = []
    if len(sh) < 3:
        return out
    a = np.array(sh, float)
    j = np.hypot(np.diff(a[:, 1]), np.diff(a[:, 2]))
    for i in np.argsort(j)[::-1][:topk]:
        out.append((float(a[i, 0]), float(a[i + 1, 0]),
                    float(a[i + 1, 1] - a[i, 1]), float(a[i + 1, 2] - a[i, 2])))
    return out


def orb_pair(f0, f1):
    g0 = cv2.cvtColor(f0, cv2.COLOR_BGR2GRAY)
    g1 = cv2.cvtColor(f1, cv2.COLOR_BGR2GRAY)
    orb = cv2.ORB_create(2500)
    k0, d0 = orb.detectAndCompute(g0, None)
    k1, d1 = orb.detectAndCompute(g1, None)
    if d0 is None or d1 is None or len(k0) < 40 or len(k1) < 40:
        return None
    bf = cv2.BFMatcher(cv2.NORM_HAMMING)
    raw = bf.knnMatch(d0, d1, k=2)
    good = [m[0] for m in raw if len(m) == 2 and m[0].distance < 0.75 * m[1].distance]
    if len(good) < 25:
        return None
    return k0, k1, good, g0.shape


def analyze(k0, k1, good, shape):
    H, W = shape
    rows = []
    for m in good:
        x, y = k0[m.queryIdx].pt
        rows.append((x, y, k1[m.trainIdx].pt[0] - x, k1[m.trainIdx].pt[1] - y))
    a = np.array(rows, float)
    cells = {}
    for x, y, dx, dy in a:
        key = (min(2, int(y / H * 3)), min(2, int(x / W * 3)))
        cells.setdefault(key, []).append((dx, dy))
    stat = {k: (float(np.median([p[0] for p in v])),
                float(np.median([p[1] for p in v])), len(v))
            for k, v in cells.items() if len(v) >= 6}
    if len(stat) < 3:
        return None
    dxs = np.array([v[0] for v in stat.values()])
    dys = np.array([v[1] for v in stat.values()])
    # 线性拟合 dy~y、dx~x（旋转会在斜率上体现）
    ys = np.array([(k[0] + 0.5) * H / 3 for k in stat])
    xs = np.array([(k[1] + 0.5) * W / 3 for k in stat])
    ky = np.polyfit(ys, dys, 1)[0] if len(stat) >= 3 else 0.0
    kx = np.polyfit(xs, dxs, 1)[0] if len(stat) >= 3 else 0.0
    return {"n_match": len(good), "n_cell": len(stat),
            "dx_med": float(np.median(dxs)), "dy_med": float(np.median(dys)),
            "dx_spread": float(dxs.max() - dxs.min()),
            "dy_spread": float(dys.max() - dys.min()),
            "slope_dy_per_y": float(ky), "slope_dx_per_x": float(kx),
            "cells": {f"{k[0]},{k[1]}": [round(v[0], 1), round(v[1], 1), v[2]]
                      for k, v in sorted(stat.items())}}


def run(name):
    doc = load(name)
    if not doc:
        print(name, "无 positions")
        return {}
    v = doc.get("video")
    if not v or not os.path.exists(v):
        print(name, "视频缺失")
        return {}
    fps = (doc.get("meta") or {}).get("fps") or 30.0
    cap = cv2.VideoCapture(v)
    print(f"\n=== {name} fps={fps:.2f} 视频={'OK'} ===")
    report = {}
    for si, s in enumerate(segs_of(doc)):
        sh = s.get("shift") or []
        wins = jump_window(sh, topk=2)
        if not wins:
            print(f"  seg{si}: shift 采样 {len(sh)}，无法定位突变")
            continue
        best = None
        for (ta, tb, ddx, ddy) in wins:
            print(f"  seg{si} 候选窗口 {ta/60:6.2f}~{tb/60:6.2f}min"
                  f" 表跳变 dx{ddx:+7.1f} dy{ddy:+7.1f}")
            f0i = int(round(ta * fps))
            f1i = int(round(tb * fps))
            if f1i - f0i < 2 or f1i - f0i > int(fps * 12):
                continue
            cap.set(cv2.CAP_PROP_POS_FRAMES, f0i)
            prev = cap.read()[1]
            curve = []
            biggest = None
            for fi in range(f0i + 1, f1i + 1):
                ok, cur = cap.read()
                if not ok or prev is None:
                    break
                r = orb_pair(prev, cur)
                if r:
                    st = analyze(*r)
                    if st:
                        mag = abs(st["dy_med"]) + abs(st["dx_med"])
                        curve.append((fi, st["dx_med"], st["dy_med"], mag, st))
                        if biggest is None or mag > biggest[3]:
                            biggest = (fi, st["dx_med"], st["dy_med"], mag, st)
                prev = cur
            if not biggest:
                continue
            fi, dxm, dym, mag, st = biggest
            nz = sum(1 for c in curve if c[3] > 2.0)
            print(f"    帧扫描 {len(curve)} 对，|位移|>2px 的有 {nz} 对"
                  f" → 峰值在 f{fi} (t={fi/fps/60:.2f}min)"
                  f" dx{dxm:+6.1f} dy{dym:+6.1f}")
            print(f"    九宫格位移极差: dx {st['dx_spread']:.1f}px  dy {st['dy_spread']:.1f}px"
                  f" | 斜率 dy/y {st['slope_dy_per_y']:+.4f} dx/x {st['slope_dx_per_x']:+.4f}")
            if best is None or mag > best["mag"]:
                best = {"seg": si, "frame": fi, "t_min": round(fi / fps / 60, 2),
                        "dx": round(dxm, 1), "dy": round(dym, 1), "mag": round(mag, 1),
                        "dx_spread": round(st["dx_spread"], 1),
                        "dy_spread": round(st["dy_spread"], 1),
                        "slope_dy_per_y": round(st["slope_dy_per_y"], 4),
                        "slope_dx_per_x": round(st["slope_dx_per_x"], 4),
                        "cells": st["cells"],
                        "n_frame_pairs": len(curve), "n_moved": nz}
        if best:
            report[f"seg{si}"] = best
    cap.release()
    return report


if __name__ == "__main__":
    out = {}
    for n in ("2026-10-17_1", "2026-10-23_1", "2026-10-30_1", "2026-10-31_1"):
        r = run(n)
        if r:
            out[n] = r
    p = "诊断/2026-11-07-筐识别/motion_field2_result.json"
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\n已写", p)
