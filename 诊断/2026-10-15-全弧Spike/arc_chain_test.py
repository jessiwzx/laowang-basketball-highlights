# -*- coding: utf-8 -*-
r"""全弧链重建小测：给定进球 t，全帧 YOLO 检球 → 贪心建链 → 看能否覆盖 [出手, 进球]。

只读；产物写 OUT。用法：python arc_chain_test.py 458.2 473.7 577.0
"""
import json
import os
import sys

import cv2
import numpy as np

ROOT = r'D:/claude code/视频剪辑YOLO'
sys.path.insert(0, ROOT)

W = os.path.join(ROOT, 'work', '2025-06-28_1')
OUT = os.path.join(ROOT, '诊断', '2026-10-15-全弧Spike')
WINDOW = 6.0
CONF = 0.05
IMG = 1024


def load(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def pk(k):
    p = k.split(':')
    return int(p[0]) * 60 + float(p[1]) if len(p) == 2 else float(p[0])


def chain_from(dets, hoop_c, hoop_w, t_goal):
    """从进球帧往回想：每步挑最近且在门内的候选。dets=[(t,cx,cy,conf)]"""
    dets = sorted(dets, key=lambda d: -d[0])
    if not dets:
        return []
    near = [d for d in dets if abs(d[0] - t_goal) <= 0.35]
    if not near:
        return []
    start = min(near, key=lambda d: (d[1] - hoop_c[0]) ** 2
                + (d[2] - hoop_c[1]) ** 2)
    chain = [start]
    cur = start
    gate = max(3.0 * hoop_w, 60.0)
    for d in dets:
        if d[0] >= cur[0]:
            continue
        dt = cur[0] - d[0]
        if dt > 0.5:
            break
        dist = ((d[1] - cur[1]) ** 2 + (d[2] - cur[2]) ** 2) ** 0.5
        if dist <= gate:
            chain.append(d)
            cur = d
    return chain


def main(goals):
    cal = load(os.path.join(W, 'calibration.json'))
    pos = load(os.path.join(W, 'positions.json'))
    sj = load(os.path.join(W, 'shooters.json'))
    tr = sj['traces']
    by_t = {round(pk(k), 2): v for k, v in tr.items()}

    hx, hy, hw, hh = cal['hoop_rect']
    hoop_c = (hx + hw / 2.0, hy + hh / 2.0)
    video = pos['video']
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    from ultralytics import YOLO
    model = YOLO(os.path.join(ROOT, 'bbh', 'weights', 'best.pt'))

    print(f'视频 {os.path.basename(video)}  fps {fps:.2f}  '
          f'筐口 {hw}x{hh} @ ({hx},{hy})')
    print(f'{"进球t":>8} {"出手t(现有)":>11} {"Δ出手":>7} {"链长":>5} '
          f'{"跨度s":>7} {"链尾距筐":>8} {"覆盖飞行%":>9}')
    rows_all = []
    for g in goals:
        t0 = max(0.0, g - WINDOW)
        f0, f1 = int(round(t0 * fps)), int(round(g * fps))
        cap.set(cv2.CAP_PROP_POS_FRAMES, f0)
        dets, per = [], []
        fi = f0
        while fi <= f1:
            ok, fr = cap.read()
            if not ok:
                break
            r = model.predict(fr, imgsz=IMG, conf=CONF, classes=[0],
                              verbose=False)[0]
            n = 0
            for b in r.boxes:
                x1, y1, x2, y2 = map(float, b.xyxy[0].tolist())
                dets.append((fi / fps, (x1 + x2) / 2, (y1 + y2) / 2,
                             float(b.conf)))
                n += 1
            per.append((fi / fps, n))
            fi += 1

        ch = chain_from(dets, hoop_c, hw, g)
        v = by_t.get(round(g, 1))
        rel = v['release_t'] if v and v.get('release_t') else None
        if ch:
            span = ch[0][0] - ch[-1][0]
            tail_d = ((ch[-1][1] - hoop_c[0]) ** 2
                      + (ch[-1][2] - hoop_c[1]) ** 2) ** 0.5
            cov = (100.0 * len(ch) / max(1, int(round(
                (ch[0][0] - ch[-1][0]) * fps) + 1)))
        else:
            span, tail_d, cov = 0, float('nan'), 0
        print(f'{g:>8.1f} {rel if rel else -1:>11.2f} '
              f'{(g - rel) if rel else -1:>7.2f} {len(ch):>5} {span:>7.2f} '
              f'{tail_d:>8.1f} {cov:>9.1f}')
        rows_all.append({'t': g, 'chain': ch, 'release_t': rel,
                         'per': per})

    cap.release()
    with open(os.path.join(OUT, 'chain_test.json'), 'w',
              encoding='utf-8') as f:
        json.dump(rows_all, f, ensure_ascii=False)


if __name__ == '__main__':
    main([float(x) for x in sys.argv[1:]] or [458.2, 473.7, 577.0])
