# -*- coding: utf-8 -*-
"""判定 YOLO 远景检出是"静态误检"还是"移动真球"：统计每窗口检出中心唯一位置数。"""
import glob
import json
import os
import sys

import cv2

sys.path.insert(0, r"D:/claude code/视频剪辑YOLO")
from ultralytics import YOLO  # noqa: E402

WORK = r"D:/claude code/视频剪辑/work"


def pk(k):
    m, s = k.split(":")
    return int(m) * 60 + float(s)


def main():
    kinds = set((sys.argv[1] if len(sys.argv) > 1 else "iv").split(","))
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 15
    cases = []
    for d in sorted(glob.glob(os.path.join(WORK, "*"))):
        sj = os.path.join(d, "shooters.json")
        if not os.path.isfile(sj):
            continue
        data = json.load(open(sj, encoding="utf-8"))
        for k, v in (data.get("traces") or {}).items():
            rt = v.get("release_t")
            iv = (pk(k) - rt) if isinstance(rt, (int, float)) else None
            if v.get("spot") == "bad" and "spot" in kinds:
                cases.append((os.path.basename(d), data["video"], k, "spot"))
            elif isinstance(iv, (int, float)) and not (0.3 <= iv <= 4.0) and "iv" in kinds:
                cases.append((os.path.basename(d), data["video"], k, f"iv{iv:.1f}"))
            elif v.get("status") == "low" and "low" in kinds:
                cases.append((os.path.basename(d), data["video"], k, "low"))
    cases = cases[:limit]
    model = YOLO(r"D:/claude code/视频剪辑YOLO/bbh/weights/best.pt")
    print(f"{'场次':<13}{'键':>9}{'类型':>7}{'帧':>5}{'检出帧':>7}{'唯一位置':>9}{'判定':>10}")
    for vname, vpath, k, tag in cases:
        cap = cv2.VideoCapture(vpath)
        fps = cap.get(5) or 30
        f0 = max(0, int((pk(k) - 6) * fps))
        f1 = int(pk(k) * fps)
        step = max(1, int(fps / 7.5))
        centers = []
        n = 0
        for fi in range(f0, f1, step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
            ok, fr = cap.read()
            if not ok:
                continue
            n += 1
            r = model.predict(fr, imgsz=1024, conf=0.10, iou=0.7,
                              classes=[0], verbose=False)[0]
            for b in r.boxes:
                x1, y1, x2, y2 = b.xyxy[0].tolist()
                centers.append((round((x1 + x2) / 2 / 15), round((y1 + y2) / 2 / 15)))
        cap.release()
        uniq = len(set(centers))
        verdict = "静态误检" if uniq <= 2 and len(centers) > 3 else (
            "移动轨迹" if uniq >= 4 else "零星")
        print(f"{vname:<13}{k:>9}{tag:>7}{n:>5}{len(centers):>7}{uniq:>9}{verdict:>10}")


if __name__ == "__main__":
    main()
