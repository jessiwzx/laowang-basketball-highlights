# -*- coding: utf-8 -*-
r"""全弧 Spike：进球窗口「全帧」检球（自训 YOLO vs HSV）→ 画候选 → 判能否重建全弧。

只读 work/ 与源视频；产物写 out_dir，不动项目任何文件。
用法：python fulltrack_spike.py 16.07 21.33 26.9
"""
import json
import os
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw

ROOT = r'D:/claude code/视频剪辑YOLO'
sys.path.insert(0, ROOT)
from bbh.detector import ball_blobs, MAX_AREA_RATIO  # noqa: E402

WORK = os.path.join(ROOT, 'work', '2025-06-28_1')
OUT = os.path.join(ROOT, '诊断', '2026-10-15-全弧Spike')
WINDOW = 6.0      # 进球前回看秒数
STEP = 2          # 每 2 帧取 1 帧
CONF = 0.05       # YOLO 阈值（检测器默认 0.03，这里略抬）


def load(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def scan_goal(model, cap, hoop, hsv_lo, hsv_hi, max_area, fps, t_goal):
    """回扫 [t_goal-WINDOW, t_goal] 的全帧候选。返回 rows。"""
    t0 = max(0.0, t_goal - WINDOW)
    f0, f1 = int(round(t0 * fps)), int(round(t_goal * fps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, f0)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    rows, fi = [], f0
    while fi <= f1:
        ok, frame = cap.read()
        if not ok:
            break
        if (fi - f0) % STEP == 0:
            r = model.predict(frame, imgsz=1024, conf=CONF, iou=0.7,
                              classes=[0], verbose=False)[0]
            yb = [(list(map(float, b.xyxy[0].tolist())), float(b.conf))
                  for b in r.boxes]
            mask = cv2.inRange(cv2.cvtColor(frame, cv2.COLOR_BGR2HSV),
                               hsv_lo, hsv_hi)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
            hb = [(float(cx), float(cy), float(a))
                  for a, _c, cx, cy in ball_blobs(mask, max_area)]
            rows.append({'fi': fi, 't': round(fi / fps, 3), 'yolo': yb,
                         'hsv': hb, 'frame': frame})
        fi += 1
    return rows


def draw_frame(frame, hoop, row):
    img = frame.copy()
    hx, hy, hw, hh = hoop
    cv2.rectangle(img, (hx, hy), (hx + hw, hy + hh), (0, 0, 255), 2)   # 筐口=红
    for bb, cf in row['yolo']:
        x1, y1, x2, y2 = map(int, bb)
        cv2.rectangle(img, (x1, y1), (x2, y2), (255, 60, 0), 1)        # YOLO=蓝
        cv2.putText(img, f'{cf:.2f}', (x1, max(10, y1 - 3)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (255, 60, 0), 1)
    for cx, cy, _a in row['hsv']:
        cv2.circle(img, (int(cx), int(cy)), 7, (0, 220, 0), 1)          # HSV=绿
    return img


def montage(rows, hoop, cols=3, cell_w=560):
    if not rows:
        return None
    step = max(1, len(rows) // 12)
    picks = rows[::step][:12]
    fh = int(picks[0]['frame'].shape[0] * cell_w / picks[0]['frame'].shape[1])
    lab = 20
    nrow = (len(picks) + cols - 1) // cols
    canvas = Image.new('RGB', (cols * cell_w, nrow * (fh + lab)), (18, 18, 18))
    d = ImageDraw.Draw(canvas)
    for i, r in enumerate(picks):
        im = draw_frame(r['frame'], hoop, r)
        pil = Image.fromarray(cv2.cvtColor(cv2.resize(im, (cell_w, fh)),
                                            cv2.COLOR_BGR2RGB))
        x, y = (i % cols) * cell_w, (i // cols) * (fh + lab)
        canvas.paste(pil, (x, y))
        d.text((x + 4, y + fh + 3),
               f"t={r['t']:.2f}s  YOLO {len(r['yolo'])}  HSV {len(r['hsv'])}",
               fill=(210, 210, 210))
    return canvas


def scatter(rows, hoop, fw, fh):
    """把整窗所有候选中心画在一帧底上（YOLO=蓝 HSV=绿 筐口=红），看轨迹分布。"""
    base = np.full((fh, fw, 3), 24, np.uint8)
    hx, hy, hw, hh = hoop
    cv2.rectangle(base, (hx, hy), (hx + hw, hy + hh), (0, 0, 255), 2)
    for r in rows:
        for bb, _ in r['yolo']:
            x1, y1, x2, y2 = map(int, bb)
            cv2.rectangle(base, (x1, y1), (x2, y2), (255, 60, 0), 1)
        for cx, cy, _a in r['hsv']:
            cv2.circle(base, (int(cx), int(cy)), 3, (0, 220, 0), -1)
    return base


def main(goals):
    cal = load(os.path.join(WORK, 'calibration.json'))
    pos = load(os.path.join(WORK, 'positions.json'))
    video = pos['video']
    hx, hy, hw, hh = cal['hoop_rect']
    fw, fh = cal['frame_size']
    hsv_lo = np.array(cal['hsv_low'], np.uint8)
    hsv_hi = np.array(cal['hsv_high'], np.uint8)
    max_area = hw * hw * MAX_AREA_RATIO

    from ultralytics import YOLO
    model = YOLO(os.path.join(ROOT, 'bbh', 'weights', 'best.pt'))
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    print(f'视频 {video}\n画面 {fw}x{fh} fps {fps:.2f} 筐口 {hw}x{hh} '
          f'max_area {max_area:.0f}')

    for g in goals:
        rows = scan_goal(model, cap, (hx, hy, hw, hh), hsv_lo, hsv_hi,
                         max_area, fps, g)
        ny = [len(r['yolo']) for r in rows]
        nh = [len(r['hsv']) for r in rows]
        print(f'\n== 进球 t={g}s == 采样 {len(rows)} 帧')
        print(f'  YOLO: 每帧候选 {min(ny) if ny else 0}~{max(ny) if ny else 0}'
              f'（合计 {sum(ny)}）')
        print(f'  HSV : 每帧候选 {min(nh) if nh else 0}~{max(nh) if nh else 0}'
              f'（合计 {sum(nh)}）')
        m = montage(rows, (hx, hy, hw, hh))
        if m:
            m.save(os.path.join(OUT, f'track_t{g:.1f}_montage.png'))
        sc = scatter(rows, (hx, hy, hw, hh), fw, fh)
        cv2.imwrite(os.path.join(OUT, f'track_t{g:.1f}_scatter.png'), sc)
        # 候选明细（供后续建链）
        with open(os.path.join(OUT, f'track_t{g:.1f}.json'), 'w',
                  encoding='utf-8') as f:
            json.dump([{'fi': r['fi'], 't': r['t'], 'yolo': r['yolo'],
                        'hsv': r['hsv']} for r in rows], f, ensure_ascii=False)
    cap.release()
    print('\n产物 →', OUT)


if __name__ == '__main__':
    gs = [float(x) for x in sys.argv[1:]] or [16.07, 21.33, 26.9]
    main(gs)
