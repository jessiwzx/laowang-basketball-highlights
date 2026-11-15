# -*- coding: utf-8 -*-
r"""放大验证：把 YOLO 检出的弧画在帧上并放大，确认框的是球不是筐/网。

用法：python zoom_check.py
"""
import json
import os
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw

ROOT = r'D:/claude code/视频剪辑YOLO'
sys.path.insert(0, ROOT)
WORK = os.path.join(ROOT, 'work', '2025-06-28_1')
OUT = os.path.join(ROOT, '诊断', '2026-10-15-全弧Spike')


def load(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def main():
    cal = load(os.path.join(WORK, 'calibration.json'))
    pos = load(os.path.join(WORK, 'positions.json'))
    rows = load(os.path.join(OUT, 'track_t21.3.json'))
    hx, hy, hw, hh = cal['hoop_rect']
    cap = cv2.VideoCapture(pos['video'])
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    # 只取高置信那一段弧（t 17.0~18.1）
    arc = []
    for r in rows:
        if 16.9 <= r['t'] <= 18.2:
            for bb, cf in r['yolo']:
                if cf >= 0.3:
                    x1, y1, x2, y2 = bb
                    arc.append(((x1 + x2) / 2, (y1 + y2) / 2, r['t'], cf))
    print('高置信弧点:', len(arc))

    crops = []
    for t in (17.13, 17.40, 17.87, 18.07):
        fi = int(round(t * fps))
        cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
        ok, fr = cap.read()
        if not ok:
            continue
        x0, y0, x1c, y1c = 600, 240, 920, 400
        crop = fr[y0:y1c, x0:x1c].copy()
        # 画全弧折线（映射进裁剪坐标）
        pts = np.array([[int(px) - x0, int(py) - y0]
                        for px, py, _t, _c in arc], np.int32)
        if len(pts) > 1:
            cv2.polylines(crop, [pts], False, (0, 255, 255), 1)
        for px, py, tt, cf in arc:
            c = (255, 60, 0) if abs(tt - t) > 0.05 else (0, 0, 255)
            cv2.circle(crop, (int(px) - x0, int(py) - y0), 3, c, -1)
        cv2.rectangle(crop, (hx - x0, hy - y0), (hx + hw - x0, hy + hh - y0),
                      (0, 140, 255), 2)
        crop = cv2.resize(crop, None, fx=3.0, fy=3.0,
                          interpolation=cv2.INTER_NEAREST)
        cv2.putText(crop, f't={t:.2f}s', (10, 34),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
        crops.append(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
    cap.release()
    if not crops:
        print('无帧')
        return
    h = max(c.shape[0] for c in crops)
    canvas = np.full((h * len(crops) + 6 * (len(crops) - 1),
                      crops[0].shape[1], 3), 20, np.uint8)
    y = 0
    for c in crops:
        canvas[y:y + c.shape[0]] = c
        y += c.shape[0] + 6
    Image.fromarray(canvas).save(os.path.join(OUT, 'zoom_arc_check.png'))
    print('→', os.path.join(OUT, 'zoom_arc_check.png'))


if __name__ == '__main__':
    main()
