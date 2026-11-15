# -*- coding: utf-8 -*-
"""P0：把 YOLO 认出的篮筐（class 1）画出来，与人工标定筐框对照。

只读：不写项目存档，产物只落本目录。纯展示用途。
"""
import json
import os
import sys

import cv2
import numpy as np

PROJ = r"D:/claude code/视频剪辑YOLO"
WORK = r"D:/claude code/视频剪辑/work"
ARCH = r"D:/claude code/1视频获取/归档"
OUT = os.path.join(PROJ, "诊断", "2026-11-07-筐识别")

PAD_X, PAD_Y, ZOOM = 120, 80, 1.5     # 以标定筐心为中心的裁剪窗
COLS, ROWS = 3, 4
CELL_W, CELL_H = int(PAD_X * 2 * ZOOM), int(PAD_Y * 2 * ZOOM)


def imread(p):
    buf = np.fromfile(p, np.uint8)
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def imwrite(p, img):
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise OSError(p)
    buf.tofile(p)


def pick(model, frame):
    """conf 最高的 hoop 框 → (x1,y1,x2,y2,conf) 或 None。"""
    kw = dict(imgsz=1024, conf=0.05, iou=0.7, classes=[1], verbose=False)
    try:
        res = model.predict(frame, device=0, quantize=16, **kw)[0]
    except Exception:
        res = model.predict(frame, device="cpu", **kw)[0]
    boxes = res.boxes
    if boxes is None or len(boxes) == 0:
        return None
    confs = boxes.conf.cpu().numpy()
    i = int(np.argmax(confs))
    x1, y1, x2, y2 = boxes.xyxy.cpu().numpy()[i]
    return float(x1), float(y1), float(x2), float(y2), float(confs[i])


def run(name):
    c = json.load(open(os.path.join(WORK, name, "calibration.json"), encoding="utf-8"))
    hx, hy, hw, hh = [float(v) for v in c["hoop_rect"]]
    cx, cy = hx + hw / 2.0, hy + hh / 2.0
    video = os.path.join(ARCH, name + ".mp4")

    from ultralytics import YOLO
    model = YOLO(os.path.join(PROJ, "bbh", "weights", "best.pt"))

    cap = cv2.VideoCapture(video)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    dur = total / fps

    n = COLS * ROWS
    idx = [int(total * (0.03 + 0.94 * i / (n - 1))) for i in range(n)]

    cells, rows, hit = [], [], 0
    print(f"\n=== {name}  {dur/60:.1f}min  筐 {hw:.0f}x{hh:.0f}px ===")
    for k, fi in enumerate(idx):
        cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
        ok, frame = cap.retrieve() if cap.grab() else (False, None)
        if not ok:
            continue
        t = fi / fps
        d = pick(model, frame)
        # 裁剪窗固定锚在人工标定筐心
        x0 = int(round(cx - PAD_X))
        y0 = int(round(cy - PAD_Y))
        x0 = max(0, min(x0, frame.shape[1] - PAD_X * 2))
        y0 = max(0, min(y0, frame.shape[0] - PAD_Y * 2))
        crop = frame[y0:y0 + PAD_Y * 2, x0:x0 + PAD_X * 2]
        big = cv2.resize(crop, (CELL_W, CELL_H), interpolation=cv2.INTER_CUBIC)

        def m(x, y):
            return int(round((x - x0) * ZOOM)), int(round((y - y0) * ZOOM))

        # 人工标定筐（红）
        cv2.rectangle(big, m(hx, hy), m(hx + hw, hy + hh), (60, 60, 230), 1)
        # 模型检出筐（绿）
        if d is not None:
            hit += 1
            x1, y1, x2, y2, cf = d
            cv2.rectangle(big, m(x1, y1), m(x2, y2), (80, 220, 90), 1)
            dx = (x1 + x2) / 2.0 - cx
            dy = (y1 + y2) / 2.0 - cy
            tag = f"t={t/60:.1f}m conf={cf:.2f}"
            sub = f"dx={dx:+.1f} dy={dy:+.1f}px ({abs(dx)/hw:.2f}W)"
            rows.append((t, cf, dx, dy, (x2 - x1), (y2 - y1)))
        else:
            tag = f"t={t/60:.1f}m  NO HOOP"
            sub = "-"
            cv2.putText(big, "no detection", (8, CELL_H - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (80, 80, 230), 1)
        cv2.rectangle(big, (0, 0), (CELL_W, 34), (28, 28, 28), -1)
        cv2.putText(big, tag, (6, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (235, 235, 235), 1)
        cv2.putText(big, sub, (6, 29), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (150, 200, 255), 1)
        cells.append(big)

    cap.release()
    sheet = np.full((CELL_H * ROWS, CELL_W * COLS, 3), 22, np.uint8)
    for i, cell in enumerate(cells):
        r, cc = divmod(i, COLS)
        sheet[r * CELL_H:(r + 1) * CELL_H, cc * CELL_W:(cc + 1) * CELL_W] = cell
    path = os.path.join(OUT, f"hoop_{name}.png")
    imwrite(path, sheet)

    print(f"  检出 {hit}/{len(cells)} 帧")
    if rows:
        dxs = np.array([abs(r[2]) for r in rows])
        dys = np.array([abs(r[3]) for r in rows])
        ws = np.array([r[4] for r in rows])
        hs = np.array([r[5] for r in rows])
        print(f"  中心偏差 |dx| 中位 {np.median(dxs):.1f}px / 最大 {dxs.max():.1f}px"
              f"  = {np.median(dxs)/hw:.2f}W / {dxs.max()/hw:.2f}W")
        print(f"  中心偏差 |dy| 中位 {np.median(dys):.1f}px / 最大 {dys.max():.1f}px")
        print(f"  检出框尺寸 中位 {np.median(ws):.0f}x{np.median(hs):.0f}px"
              f"（标定 {hw:.0f}x{hh:.0f}）")
        print(f"  conf 中位 {np.median([r[1] for r in rows]):.2f}"
              f" / min {min(r[1] for r in rows):.2f}")
    print(f"  → {path}")
    return rows


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for nm in (sys.argv[1:] or ["2026-10-10_1", "2026-10-16_1"]):
        run(nm)
