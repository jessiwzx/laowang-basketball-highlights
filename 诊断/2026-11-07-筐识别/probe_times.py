# -*- coding: utf-8 -*-
"""在指定时刻抽帧，验证配准值是"真的跟随"还是"跑偏"。"""
import bisect
import json
import os
import sys

import cv2
import numpy as np

PROJ = r"D:/claude code/视频剪辑YOLO"
WORK = os.path.join(PROJ, "work")


def load(name):
    base = os.path.join(WORK, name)
    fs = ["calibration.json"] + sorted(
        f for f in os.listdir(base)
        if f.startswith("calibration_seg") and f.endswith(".json"))
    cals = sorted([json.load(open(os.path.join(base, f), encoding="utf-8"))
                   for f in fs], key=lambda c: float(c.get("calib_t") or 0))
    pos = json.load(open(os.path.join(base, "positions.json"), encoding="utf-8"))
    segs = []
    for i, c in enumerate(cals):
        t0 = float(c.get("calib_t") or 0)
        t1 = float(cals[i + 1]["calib_t"]) if i + 1 < len(cals) else 1e9
        sh = next((s.get("shift") or [] for s in pos["segments"]
                   if abs(float(s.get("t0") or 0) - t0) < 0.5), [])
        segs.append({"t0": t0, "t1": t1,
                     "rect": [float(v) for v in c["hoop_rect"]], "shift": sh})
    return segs, pos.get("video")


def shift_at(sh, t):
    if not sh:
        return 0.0, 0.0
    ts = [x[0] for x in sh]
    i = bisect.bisect_left(ts, t)
    if i <= 0:
        return float(sh[0][1]), float(sh[0][2])
    if i >= len(sh):
        return float(sh[-1][1]), float(sh[-1][2])
    a, b = sh[i - 1], sh[i]
    dt = (b[0] - a[0]) or 1.0
    k = (t - a[0]) / dt
    return a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k


def main(name, times):
    from ultralytics import YOLO
    model = YOLO(os.path.join(PROJ, "bbh", "weights", "best.pt"))
    segs, video = load(name)
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    print(f"=== {name} ===")
    print(f"{'t(min)':>8} {'段':>3} {'检出':>5} {'conf':>5} "
          f"{'shift':>14} {'补偿后dx':>9} {'未补偿dx':>9}  判定")
    for t in times:
        sg = next((s for s in segs if s["t0"] <= t < s["t1"]), None)
        if sg is None:
            print(f"{t/60:8.1f}  无段"); continue
        hx, hy, hw, hh = sg["rect"]
        cx, cy = hx + hw / 2, hy + hh / 2
        sdx, sdy = shift_at(sg["shift"], t)
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(t * fps)))
        frame = cap.retrieve()[1] if cap.grab() else None
        if frame is None:
            print(f"{t/60:8.1f}  取帧失败"); continue
        res = model.predict(frame, device=0, quantize=16, imgsz=1024, conf=0.05,
                            iou=0.7, classes=[1], verbose=False)[0]
        b = res.boxes
        if b is None or len(b) == 0:
            print(f"{t/60:8.1f}  {segs.index(sg)+1:>2}  NO")
            continue
        cf = b.conf.cpu().numpy()
        i = int(np.argmax(cf))
        x1, y1, x2, y2 = b.xyxy.cpu().numpy()[i]
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        dx_c = mx - (cx + sdx)
        dx_r = mx - cx
        verdict = "配准对" if abs(dx_c) < abs(dx_r) else "配准错（真实位置≠标定+shift）"
        print(f"{t/60:8.1f}  {segs.index(sg)+1:>2}  {'Y':>5} {cf[i]:5.2f} "
              f"({sdx:+7.1f},{sdy:+6.1f}) {dx_c:+9.1f} {dx_r:+9.1f}px  {verdict}")
    cap.release()


if __name__ == "__main__":
    nm = sys.argv[1]
    ts = [float(x) * 60 for x in sys.argv[2:]]
    main(nm, ts)
