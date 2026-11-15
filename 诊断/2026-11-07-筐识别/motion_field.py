# -*- coding: utf-8 -*-
"""验证 A：镜头位移是否"全局平移"——决定 hoop 位移能否直接用于全部球点位。

做法：在指定时刻取间隔 N 帧的两帧，ORB 匹配，把匹配点按画面九宫格分箱，
比较各箱位移中位数。若各箱差异 < 2px → 全局平移假设成立（Δ_hoop 可直接用）；
若差异达数十像素 → 摇镜/视差，只能用低频漂移校正项。

只读：打开视频抽帧，不写项目文件，结果落 诊断/ 目录。
"""
import json
import os
import sys

import cv2
import numpy as np

ARCH = r"D:/claude code/1视频获取/归档"


def load_video(name):
    for p in (f"work/{name}/positions.json",
              rf"D:/claude code/视频剪辑/work/{name}/positions.json"):
        if os.path.exists(p):
            d = json.load(open(p, encoding="utf-8"))
            v = d.get("video")
            if v and os.path.exists(v):
                return v, d
    return None, None


def grab(cap, t_sec, fps):
    fi = int(round(t_sec * fps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
    ok, fr = cap.read()
    return fr if ok else None


def field(f0, f1, gap_label):
    g0 = cv2.cvtColor(f0, cv2.COLOR_BGR2GRAY)
    g1 = cv2.cvtColor(f1, cv2.COLOR_BGR2GRAY)
    orb = cv2.ORB_create(3000)
    k0, d0 = orb.detectAndCompute(g0, None)
    k1, d1 = orb.detectAndCompute(g1, None)
    if d0 is None or d1 is None or len(k0) < 30 or len(k1) < 30:
        return None
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
    raw = bf.knnMatch(d0, d1, k=2)
    good = []
    for m in raw:
        if len(m) == 2 and m[0].distance < 0.75 * m[1].distance:
            good.append(m[0])
    if len(good) < 20:
        return None
    H, W = g0.shape
    pts = np.array([[k0[m.queryIdx].pt[0], k0[m.queryIdx].pt[1],
                     k1[m.trainIdx].pt[0] - k0[m.queryIdx].pt[0],
                     k1[m.trainIdx].pt[1] - k0[m.queryIdx].pt[1]]
                    for m in good], float)
    # 三分网格
    cells = {}
    for x, y, dx, dy in pts:
        cx = min(2, int(x / W * 3))
        cy = min(2, int(y / H * 3))
        cells.setdefault((cy, cx), []).append((dx, dy))
    stat = {}
    for k, v in cells.items():
        if len(v) < 8:
            continue
        a = np.array(v)
        stat[k] = (np.median(a[:, 0]), np.median(a[:, 1]), len(v))
    if len(stat) < 3:
        return None
    mdx = np.array([v[0] for v in stat.values()])
    mdy = np.array([v[1] for v in stat.values()])
    return {"label": gap_label, "n_match": len(good), "n_cell": len(stat),
            "dx_med": float(np.median(mdx)), "dy_med": float(np.median(mdy)),
            "dx_spread": float(mdx.max() - mdx.min()),
            "dy_spread": float(mdy.max() - mdy.min()),
            "cells": {f"{k[0]},{k[1]}": [round(v[0], 1), round(v[1], 1), v[2]]
                      for k, v in sorted(stat.items())}}


def run(name, times, gaps=(5,)):
    v, doc = load_video(name)
    if not v:
        print(f"{name}: 视频缺失，跳过")
        return []
    fps = (doc.get("meta") or {}).get("fps") or 24.0
    cap = cv2.VideoCapture(v)
    print(f"=== {name}  fps={fps:.2f} ===")
    out = []
    for t in times:
        for gap in gaps:
            f0 = grab(cap, t, fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)) + gap)
            ok, f1 = cap.read()
            if f0 is None or not ok:
                continue
            r = field(f0, f1, f"t={t/60:.1f}min gap={gap}f")
            if r:
                out.append(r)
                print(f"  {t/60:7.2f}min gap={gap}f 匹配{r['n_match']:>5} 格{r['n_cell']}"
                      f" | dx中位{r['dx_med']:+7.1f} 极差{r['dx_spread']:6.1f}"
                      f" | dy中位{r['dy_med']:+7.1f} 极差{r['dy_spread']:6.1f}")
            else:
                print(f"  {t/60:7.2f}min gap={gap}f  匹配不足")
    cap.release()
    return out


if __name__ == "__main__":
    targets = {
        # 10-17: 101.6min 与 109.0min 是两次补段边界（已知 dy +75 / −50）
        "2026-10-17_1": [100.9 * 60, 101.3 * 60, 101.6 * 60, 102.0 * 60,
                         108.6 * 60, 109.0 * 60, 109.4 * 60,
                         60.0 * 60],          # 60min 作静止对照
        "2026-10-23_1": [23.5 * 60, 79.9 * 60, 80.2 * 60, 30.0 * 60],
    }
    res = {}
    for n, ts in targets.items():
        res[n] = run(n, ts)
    out = "诊断/2026-11-07-筐识别/motion_field_result.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print("已写", out)
