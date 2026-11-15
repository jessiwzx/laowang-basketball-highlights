# -*- coding: utf-8 -*-
r"""耗时基准：全帧检球（YOLO/HSV）在「一个进球窗口」上的实际成本。

只读源视频与 work/；不写项目文件。
用法：python cost_bench.py [t_goal] [窗口秒数]
"""
import json
import os
import sys
import time

import cv2
import numpy as np

ROOT = r'D:/claude code/视频剪辑YOLO'
sys.path.insert(0, ROOT)
from bbh.detector import ball_blobs, MAX_AREA_RATIO  # noqa: E402

WORK = os.path.join(ROOT, 'work', '2025-06-28_1')


def load(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def main(t_goal=21.33, win=2.53):
    cal = load(os.path.join(WORK, 'calibration.json'))
    pos = load(os.path.join(WORK, 'positions.json'))
    video = pos['video']
    hx, hy, hw, hh = cal['hoop_rect']
    hsv_lo = np.array(cal['hsv_low'], np.uint8)
    hsv_hi = np.array(cal['hsv_high'], np.uint8)
    max_area = hw * hw * MAX_AREA_RATIO

    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    t0 = max(0.0, t_goal - win)
    f0, f1 = int(round(t0 * fps)), int(round(t_goal * fps))

    print(f'窗口 {t0:.2f}~{t_goal:.2f}s  帧 {f0}~{f1}  共 {f1 - f0 + 1} 帧'
          f'  fps {fps:.2f}')
    print(f'画面 {cal["frame_size"][0]}x{cal["frame_size"][1]}')

    # 1) 光读帧
    cap.set(cv2.CAP_PROP_POS_FRAMES, f0)
    frames = []
    t = time.perf_counter()
    for _ in range(f1 - f0 + 1):
        ok, fr = cap.read()
        if not ok:
            break
        frames.append(fr)
    t_read = time.perf_counter() - t
    print(f'\n[1] 解码 {len(frames)} 帧: {t_read:.2f}s'
          f'  ({1000 * t_read / max(1, len(frames)):.1f} ms/帧)')

    # 2) HSV 全帧
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    t = time.perf_counter()
    hsv_hits = 0
    for fr in frames:
        mask = cv2.inRange(cv2.cvtColor(fr, cv2.COLOR_BGR2HSV), hsv_lo, hsv_hi)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        hsv_hits += len(ball_blobs(mask, max_area))
    t_hsv = time.perf_counter() - t
    print(f'[2] HSV 全帧 {len(frames)} 帧: {t_hsv:.2f}s'
          f'  ({1000 * t_hsv / max(1, len(frames)):.1f} ms/帧, 候选 {hsv_hits})')

    # 3) YOLO 全帧（逐帧，不同 imgsz）
    from ultralytics import YOLO
    import torch
    model = YOLO(os.path.join(ROOT, 'bbh', 'weights', 'best.pt'))
    dev = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f'\n[3] YOLO 全帧（device={dev}）')
    for imgsz in (640, 1024):
        # 预热
        model.predict(frames[0], imgsz=imgsz, conf=0.05, classes=[0],
                      verbose=False)
        t = time.perf_counter()
        n = 0
        for fr in frames:
            r = model.predict(fr, imgsz=imgsz, conf=0.05, classes=[0],
                              verbose=False)[0]
            n += len(r.boxes)
        dt = time.perf_counter() - t
        speed = (len(frames) / fps) / dt if dt else 0
        print(f'    imgsz={imgsz}: {dt:.2f}s / {len(frames)} 帧 ='
              f' {1000 * dt / max(1, len(frames)):.0f} ms/帧'
              f'  ({speed:.1f}x 实时)  候选 {n}')

    cap.release()
    print('\n—— 外推 ——')
    print(f'  单颗球（每帧检）≈ {dt:.2f}s（imgsz=1024 结果见上）')


if __name__ == '__main__':
    g = float(sys.argv[1]) if len(sys.argv) > 1 else 21.33
    w = float(sys.argv[2]) if len(sys.argv) > 2 else 2.53
    main(g, w)
