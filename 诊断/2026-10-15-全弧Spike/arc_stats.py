# -*- coding: utf-8 -*-
r"""弧线普遍性统计：多颗球跑全帧 YOLO，看多少颗能追出可信弧。

用法：python arc_stats.py [N]
"""
import json
import os
import sys

import cv2
import numpy as np

ROOT = r'D:/claude code/视频剪辑YOLO'
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, '诊断', '2026-10-15-全弧Spike'))
from fulltrack_spike import scan_goal, WORK  # noqa: E402

from bbh.detector import MAX_AREA_RATIO  # noqa: E402


def load(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def chain_score(dets):
    """高置信点(conf>=0.3)的简单连贯性：点数、跨度、x 单调性。"""
    pts = dets
    if len(pts) < 4:
        return len(pts), 0, 0.0, 0.0
    pts = sorted(pts, key=lambda p: p[0])
    xs = [p[1] for p in pts]
    ys = [p[2] for p in pts]
    # x 单调比例
    dx = np.diff(xs)
    mono = float(np.mean(dx < 0)) if len(dx) else 0.0
    return len(pts), (max(xs) - min(xs)), mono, (max(ys) - min(ys))


def main(n):
    cal = load(os.path.join(WORK, 'calibration.json'))
    pos = load(os.path.join(WORK, 'positions.json'))
    evs = load(os.path.join(WORK, 'events.json'))['events']
    hx, hy, hw, hh = cal['hoop_rect']
    hsv_lo = np.array(cal['hsv_low'], np.uint8)
    hsv_hi = np.array(cal['hsv_high'], np.uint8)
    max_area = hw * hw * MAX_AREA_RATIO

    from ultralytics import YOLO
    model = YOLO(os.path.join(ROOT, 'bbh', 'weights', 'best.pt'))
    cap = cv2.VideoCapture(pos['video'])
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    goals = [e['t'] for e in evs][:n]
    ok = 0
    print(' t(goal)  高置信点  最大conf  x跨度  y跨度  x单调%  判定')
    for g in goals:
        rows = scan_goal(model, cap, (hx, hy, hw, hh), hsv_lo, hsv_hi,
                         max_area, fps, g)
        hi = []
        for r in rows:
            for bb, cf in r['yolo']:
                if cf >= 0.3:
                    x1, y1, x2, y2 = bb
                    hi.append((r['t'], (x1 + x2) / 2, (y1 + y2) / 2, cf))
        npts, xspan, mono, yspan = chain_score(hi)
        mxc = max((p[3] for p in hi), default=0.0)
        good = npts >= 6 and xspan >= 40 and mono >= 0.6
        ok += good
        print(' %7.1f  %8d  %7.2f  %5.0f  %5.0f  %6.0f%%  %s'
              % (g, npts, mxc, xspan, yspan, mono * 100,
                 '✅可用弧' if good else '—'))
    cap.release()
    print('\n可用弧: %d/%d (%.0f%%)' % (ok, len(goals), 100 * ok / len(goals)))


if __name__ == '__main__':
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 12)
