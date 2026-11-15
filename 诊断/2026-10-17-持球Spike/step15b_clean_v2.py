# -*- coding: utf-8 -*-
"""Step 1.5b（R-018）：精修版清洗。
先还原 step15 全部 rejected，再用新规则重分：
  R2 静物剔除（不变）——+4s 同位置同色 blob；
  R1' 头区∧暗色——球心在 person 框顶部 25% 内 **且** (V中位<115 或 S中位<150)。
    依据：真球亮橙（V≥122, S≥164），头/发暗（V≈105, S≈140）。
"""
import json
import math
import shutil
from pathlib import Path

import cv2
import numpy as np
from ultralytics import YOLO

REPO = Path(r"D:/claude code/yolo")
WORK = Path(r"D:/claude code/视频剪辑/work")
SRC = Path(r"D:/claude code/1视频获取/归档")
HELD = REPO / "preannot" / "held"
IMG = HELD / "images"
LAB = HELD / "labels"
REJ = HELD / "rejected"
FIELDS = ["2026-09-12_1", "2026-09-19_1"]
FPS = 30.0
HEAD_ZONE = 0.25
STATIC_TOL = 8.0
V_MIN = 115
S_MIN = 150

person = YOLO(str(Path(r"D:/claude code/视频剪辑YOLO/bbh/weights/yolov8n.pt")))


def restore_all():
    n = 0
    for p in REJ.glob("*.jpg"):
        shutil.move(str(p), str(IMG / p.name))
        shutil.move(str(REJ / (p.stem + ".txt")), str(LAB / (p.stem + ".txt")))
        n += 1
    return n


def hsv_stats(img, calib, hoop_w):
    """返回 (最大blob中心, V中位, S中位)。"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(calib["hsv_low"], np.uint8),
                       np.array(calib["hsv_high"], np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(mask)
    best = max(range(1, n), key=lambda i: stats[i, 4], default=None)
    if best is None:
        return None, -1, -1
    a = int(stats[best, 4]); cx, cy = cent[best]
    r = max((a / math.pi) ** 0.5 * 1.6, 4.0)
    H, W = img.shape[:2]
    x0, y0 = max(int(cx - r), 0), max(int(cy - r), 0)
    x1, y1 = min(int(cx + r) + 1, W), min(int(cy + r) + 1, H)
    m = mask[y0:y1, x0:x1] > 0
    patch = hsv[y0:y1, x0:x1]
    if not m.any():
        return (cx, cy), -1, -1
    return (cx, cy), int(np.median(patch[..., 2][m])), int(np.median(patch[..., 1][m]))


def main():
    n_restored = restore_all()
    print(f"已还原 {n_restored} 张被拒样本", flush=True)
    kept = rej = r1 = r2 = 0
    for field in FIELDS:
        calib = json.loads((WORK / field / "calibration.json").read_text(encoding="utf-8"))
        sh = json.loads((WORK / field / "shooters.json").read_text(encoding="utf-8"))
        rels = sorted({t["release_t"] for t in sh["traces"].values() if t.get("release_t")})
        f2rel = {}
        for rel in rels:
            for fi in range(int((rel - 1.2) * FPS), int((rel - 0.1) * FPS) + 1, 2):
                f2rel[fi] = rel
        cap = cv2.VideoCapture(str(SRC / f"{field}.mp4"))
        fw = int(cap.get(3)); fh = int(cap.get(4))
        hoop_w = calib["hoop_rect"][2] * (fw / calib["frame_size"][0])
        static_pt = {}
        for rel in rels:
            cap.set(cv2.CAP_PROP_POS_MSEC, (rel + 4.0) * 1000.0)
            ok, fr = cap.read()
            if ok:
                static_pt[rel] = hsv_blob_center(fr, calib, hoop_w)
        kf = rf = 0
        for lf in sorted(LAB.glob(f"{field}__*.txt")):
            fi = int(lf.name.split("__f")[1].split(".")[0])
            rel = f2rel.get(fi)
            img = cv2.imdecode(np.fromfile(str(IMG / (lf.stem + ".jpg")), np.uint8),
                               cv2.IMREAD_COLOR)
            if img is None or rel is None:
                continue
            (cx, cy), vmed, smed = hsv_stats(img, calib, hoop_w)
            if cx is None:
                continue
            why = None
            sp = static_pt.get(rel)
            if sp is not None and math.hypot(sp[0] - cx, sp[1] - cy) <= STATIC_TOL:
                why = "static"; r2 += 1
            if why is None and (vmed < V_MIN or smed < S_MIN):
                pr = person(img, classes=[0], conf=0.3, imgsz=640, verbose=False)[0]
                for b in pr.boxes:
                    x0, y0, x1, y1 = map(float, b.xyxy[0])
                    if x0 <= cx <= x1 and cy <= y0 + (y1 - y0) * HEAD_ZONE:
                        why = "head_dark"; r1 += 1
                        break
            if why:
                shutil.move(str(IMG / (lf.stem + ".jpg")), str(REJ / (lf.stem + ".jpg")))
                shutil.move(str(lf), str(REJ / lf.name))
                rej += 1; rf += 1
            else:
                kept += 1; kf += 1
        cap.release()
        print(f"[{field}] kept={kf} rejected={rf}（头区暗色 {r1} 累计 / 静物 {r2} 累计）",
              flush=True)
    print(f"\n=== 精修清洗完成：保留 {kept} / 剔除 {rej}（头区暗色 {r1} + 静物 {r2}）===",
          flush=True)


def hsv_blob_center(frame, calib, hoop_w):
    c, _, _ = hsv_stats(frame, calib, hoop_w)
    return c


if __name__ == "__main__":
    main()
