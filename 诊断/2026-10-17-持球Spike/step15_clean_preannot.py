# -*- coding: utf-8 -*-
"""Step 1.5（R-018）：持球段预标注自动清洗。
规则：
  R1 头区剔除——球心落在任一 COCO person 框顶部 25% 内 → 判为头/脸误标；
  R2 静物剔除——出手后 +4s 帧同位置（±8px）仍有 HSV 同色 blob → 判为地上静球。
被拒样本移入 preannot/held/rejected/（不删除，可复查）。
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
REJ = HELD / "rejected"
REJ.mkdir(exist_ok=True)
FIELDS = ["2026-09-12_1", "2026-09-19_1"]
FPS = 30.0
HEAD_ZONE = 0.25
STATIC_TOL = 8.0

person = YOLO(str(Path(r"D:/claude code/视频剪辑YOLO/bbh/weights/yolov8n.pt")))


def hsv_blob(frame, calib, hoop_w):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(calib["hsv_low"], np.uint8),
                       np.array(calib["hsv_high"], np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(mask)
    best = None
    for i in range(1, n):
        a = int(stats[i, cv2.CC_STAT_AREA])
        if a < 9 or a > 0.5 * hoop_w * hoop_w:
            continue
        if best is None or a > best[0]:
            best = (a, cent[i])
    return best[1] if best else None


def main():
    kept = rejected = 0
    r1 = r2 = 0
    for field in FIELDS:
        calib = json.loads((WORK / field / "calibration.json").read_text(encoding="utf-8"))
        sh = json.loads((WORK / field / "shooters.json").read_text(encoding="utf-8"))
        rels = sorted({t["release_t"] for t in sh["traces"].values() if t.get("release_t")})
        # frame -> rel
        f2rel = {}
        for rel in rels:
            for fi in range(int((rel - 1.2) * FPS), int((rel - 0.1) * FPS) + 1, 2):
                f2rel[fi] = rel
        cap = cv2.VideoCapture(str(SRC / f"{field}.mp4"))
        fw = int(cap.get(3)); fh = int(cap.get(4))
        hoop_w = calib["hoop_rect"][2] * (fw / calib["frame_size"][0])
        # 每个 rel 的 +4s 静物 blob 位置
        static_pt = {}
        for rel in rels:
            cap.set(cv2.CAP_PROP_POS_MSEC, (rel + 4.0) * 1000.0)
            ok, fr = cap.read()
            if ok:
                static_pt[rel] = hsv_blob(fr, calib, hoop_w)
        for lf in sorted((HELD / "labels").glob(f"{field}__*.txt")):
            fi = int(lf.name.split("__f")[1].split(".")[0])
            rel = f2rel.get(fi)
            img = cv2.imdecode(np.fromfile(str(HELD / "images" / (lf.stem + ".jpg")),
                                           np.uint8), cv2.IMREAD_COLOR)
            if img is None or rel is None:
                continue
            l = lf.read_text(encoding="utf-8").split()[1:]
            cx, cy = float(l[0]) * fw, float(l[1]) * fh
            why = None
            # R2 静物
            sp = static_pt.get(rel)
            if sp is not None and math.hypot(sp[0] - cx, sp[1] - cy) <= STATIC_TOL:
                why = "static"; r2 += 1
            # R1 头区
            if why is None:
                pr = person(img, classes=[0], conf=0.3, imgsz=640, verbose=False)[0]
                for b in pr.boxes:
                    x0, y0, x1, y1 = map(float, b.xyxy[0])
                    if x0 <= cx <= x1 and cy <= y0 + (y1 - y0) * HEAD_ZONE:
                        why = "head"; r1 += 1
                        break
            if why:
                shutil.move(str(HELD / "images" / (lf.stem + ".jpg")), str(REJ / (lf.stem + ".jpg")))
                shutil.move(str(lf), str(REJ / lf.name))
                rejected += 1
            else:
                kept += 1
        cap.release()
        print(f"[{field}] 进度：kept={kept} rejected={rejected} (head={r1} static={r2})",
              flush=True)
    print(f"\n=== 清洗完成：保留 {kept} / 剔除 {rejected}（头区 {r1} + 静物 {r2}）===", flush=True)


if __name__ == "__main__":
    main()
