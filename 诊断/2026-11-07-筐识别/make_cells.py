# -*- coding: utf-8 -*-
"""每段取一个代表帧，放大画：红=该段标定框+shift（应有位置），绿=YOLO 检出。"""
import json
import os

import cv2
import numpy as np

PROJ = r"D:/claude code/视频剪辑YOLO"
WORK = os.path.join(PROJ, "work")
OUT = os.path.join(PROJ, "诊断", "2026-11-07-筐识别")
RES = json.load(open(os.path.join(OUT, "hoop_multi_result.json"), encoding="utf-8"))

PAD_X, PAD_Y, ZOOM = 110, 70, 2.6
COLS = 3
CELL_W, CELL_H = int(PAD_X * 2 * ZOOM), int(PAD_Y * 2 * ZOOM) + 24


def wr(p, img):
    ok, b = cv2.imencode(".png", img)
    b.tofile(p)


def shift_at(sh, t):
    import bisect
    if not sh:
        return 0.0, 0.0
    ts = [x[0] for x in sh]
    i = bisect.bisect_left(ts, t)
    i = max(1, min(i, len(sh) - 1))
    a, b = sh[i - 1], sh[i]
    dt = (b[0] - a[0]) or 1.0
    k = (t - a[0]) / dt
    return a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k


def seg_info(name):
    base = os.path.join(WORK, name)
    fs = ["calibration.json"] + sorted(
        f for f in os.listdir(base)
        if f.startswith("calibration_seg") and f.endswith(".json"))
    cals = [json.load(open(os.path.join(base, f), encoding="utf-8")) for f in fs]
    cals.sort(key=lambda c: float(c.get("calib_t") or 0))
    pos = json.load(open(os.path.join(base, "positions.json"), encoding="utf-8"))
    out = []
    for i, c in enumerate(cals):
        t0 = float(c.get("calib_t") or 0)
        sh = []
        for s in pos.get("segments") or []:
            if abs(float(s.get("t0") or 0) - t0) < 0.5:
                sh = s.get("shift") or []
                break
        out.append({"t0": t0, "rect": [float(v) for v in c["hoop_rect"]], "shift": sh})
    return out, pos.get("video")


def main(names):
    from ultralytics import YOLO
    model = YOLO(os.path.join(PROJ, "bbh", "weights", "best.pt"))
    cells = []
    for name in names:
        segs, video = seg_info(name)
        rows = RES[name]
        cap = cv2.VideoCapture(video)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        for si, sg in enumerate(segs, 1):
            ss = [r for r in rows if r["seg"] == si and r["conf"] is not None]
            if not ss:
                continue
            r0 = ss[len(ss) // 2]
            fi = int(round(r0["t"] * fps))
            cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
            frame = cap.retrieve()[1] if cap.grab() else None
            if frame is None:
                continue
            hx, hy, hw, hh = sg["rect"]
            sdx, sdy = shift_at(sg["shift"], r0["t"])
            ex, ey = hx + hw / 2 + sdx, hy + hh / 2 + sdy
            x0 = max(0, min(int(ex - PAD_X), frame.shape[1] - PAD_X * 2))
            y0 = max(0, min(int(ey - PAD_Y), frame.shape[0] - PAD_Y * 2))
            crop = frame[y0:y0 + PAD_Y * 2, x0:x0 + PAD_X * 2]
            big = cv2.resize(crop, (CELL_W, int(PAD_Y * 2 * ZOOM)),
                             interpolation=cv2.INTER_CUBIC)

            def m(x, y):
                return (int(round((x - x0) * ZOOM)), int(round((y - y0) * ZOOM)))

            cv2.rectangle(big, m(ex - hw / 2, ey - hh / 2),
                          m(ex + hw / 2, ey + hh / 2), (50, 50, 235), 1)
            dx, dy = r0["dx"], r0["dy"]
            bx1 = ex + dx - r0["w"] / 2
            by1 = ey + dy - r0["h"] / 2
            cv2.rectangle(big, m(bx1, by1), m(bx1 + r0["w"], by1 + r0["h"]),
                          (80, 235, 90), 2)
            canvas = np.full((CELL_H, CELL_W, 3), 18, np.uint8)
            canvas[24:24 + big.shape[0], :] = big
            txt = (f"{name[-5:]} SEG{si}  t={r0['t']/60:.0f}m  conf={r0['conf']:.2f}")
            cv2.putText(canvas, txt, (5, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                        (235, 235, 235), 1)
            cv2.putText(canvas, f"dev {dx:+.1f},{dy:+.1f}px ({abs(dx)/hw:.2f}W)",
                        (5, CELL_H - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                        (150, 200, 255), 1)
            cells.append(canvas)
        cap.release()

    rows_n = (len(cells) + COLS - 1) // COLS
    sheet = np.full((CELL_H * rows_n, CELL_W * COLS, 3), 18, np.uint8)
    for i, c in enumerate(cells):
        r, cc = divmod(i, COLS)
        sheet[r * CELL_H:(r + 1) * CELL_H, cc * CELL_W:(cc + 1) * CELL_W] = c
    p = os.path.join(OUT, "hoop_multi_代表帧.png")
    wr(p, sheet)
    print("→", p, sheet.shape)


if __name__ == "__main__":
    main(["2026-10-17_1", "2026-10-23_1"])
