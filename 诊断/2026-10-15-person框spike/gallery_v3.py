# -*- coding: utf-8 -*-
"""v3 修复对照图（throwaway）：只给换过框的行出图。

红框 = v2 选中（认错人的框）；绿框 = v3 选中（球锚修复后）；
橙圈 = HSV 球块；黄十字 = 出手点。产出 gallery_v3/。
"""
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from person_probe_v2 import ORIG_WORK, VIDEO_DIR, attribute, merge_fragments
from probe_v3_replay import BALL_ANCHOR_TOL, attribute_v3
from verify_shooter2 import hsv_blobs, iou

ROWS = [12, 24]          # v3 相对 v2 换框（原认错人）的行


def calib_for(v, rt):
    pj = json.load(open(os.path.join(ORIG_WORK, v, "positions.json"),
                        encoding="utf-8"))
    src = ([(g["t0"], g["t1"], g["calib"]) for g in pj["segments"]]
           if "segments" in pj else [(0.0, None, pj["calib"])])
    out = []
    for t0, t1, c in src:
        lo = list(c[1])
        if c[2][1] > 88:
            lo[1] = max(lo[1], 88)
        out.append((t0, t1, np.array(lo, np.uint8), np.array(c[2], np.uint8)))
    for t0, t1, lo, hi in out:
        if rt >= t0 and (t1 is None or rt < t1):
            return lo, hi
    return out[-1][2], out[-1][3]


def best_frame(s, px, py):
    best = None
    for fi, f in enumerate(s["frames"]):
        boxes = merge_fragments([tuple(b) for b in f["boxes_raw"]])
        hit, _, _ = attribute(px, py, boxes)
        prio = {"in": 3, "expand": 2, "nearest": 1, "miss": 0}[hit]
        if best is None or fi < best[0] or (fi == best[0] and prio > best[1]):
            best = (fi, prio, f["off"], boxes)
    return best[2], best[3]


def main():
    stats = json.load(open("probe_stats_v2.json", encoding="utf-8"))
    os.makedirs("gallery_v3", exist_ok=True)
    for i in ROWS:
        s = stats[i - 1]
        v, key = s["video"], s["key"]
        tr = json.load(open(os.path.join(ORIG_WORK, v, "shooters.json"),
                            encoding="utf-8"))["traces"][key]
        rt, (px, py) = tr["release_t"], tr["release_xy"]
        off, boxes = best_frame(s, px, py)
        _, chosen2 = attribute(px, py, boxes)[:2] if False else (None, None)
        hit2, chosen2, _ = attribute(px, py, boxes)
        lo, hi = calib_for(v, rt)
        cap = cv2.VideoCapture(os.path.join(VIDEO_DIR, v + ".mp4"))
        ball = None
        for dt in (0.0, -0.2):
            cap.set(cv2.CAP_PROP_POS_MSEC, (rt + off + dt) * 1000.0)
            ok, fr0 = cap.read()
            if not ok:
                continue
            for cx, cy, a in hsv_blobs(fr0, lo, hi):
                d = ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5
                if d <= BALL_ANCHOR_TOL and (ball is None or d < ball[0]):
                    ball = (d, cx, cy, fr0.copy())
        cap.release()
        _, bx, by, fr = ball
        _, chosen3 = attribute_v3((bx, by), px, py, boxes)
        # 大全景裁剪（两人都拍到）：以两框并集 + 出手点外扩
        X1 = min(chosen2[0], chosen3[0], bx) - 160
        Y1 = min(chosen2[1], chosen3[1], by) - 120
        X2 = max(chosen2[2], chosen3[2], bx) + 160
        Y2 = max(chosen2[3], chosen3[3], by) + 120
        H, W = fr.shape[:2]
        X1, Y1, X2, Y2 = max(0, int(X1)), max(0, int(Y1)), min(W, int(X2)), min(H, int(Y2))
        crop = fr[Y1:Y2, X1:X2].copy()
        cv2.rectangle(crop, (int(chosen2[0]) - X1, int(chosen2[1]) - Y1),
                      (int(chosen2[2]) - X1, int(chosen2[3]) - Y1), (0, 0, 255), 3)
        cv2.rectangle(crop, (int(chosen3[0]) - X1, int(chosen3[1]) - Y1),
                      (int(chosen3[2]) - X1, int(chosen3[3]) - Y1), (0, 220, 0), 3)
        cv2.circle(crop, (int(bx) - X1, int(by) - Y1), 22, (0, 140, 255), 3)
        cv2.drawMarker(crop, (int(px) - X1, int(py) - Y1), (0, 215, 255),
                       cv2.MARKER_CROSS, 24, 2)
        bar = np.zeros((46, crop.shape[1], 3), np.uint8)
        cv2.putText(bar, f"#{i:02d} {v[5:]} {key}  RED=v2(wrong) GREEN=v3(ball-anchored)"
                    f"  iou={iou(chosen2, chosen3):.2f}",
                    (10, 31), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 210, 80), 2)
        cv2.imwrite(f"gallery_v3/{i:02d}_{v[5:]}_{key.replace(':', '-')}.jpg",
                    np.vstack([bar, crop]), [cv2.IMWRITE_JPEG_QUALITY, 92])
        print(f"saved gallery_v3/{i:02d}  ball d={ball[0]:.1f}")


if __name__ == "__main__":
    main()
