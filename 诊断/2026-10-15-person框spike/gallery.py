# -*- coding: utf-8 -*-
"""从 probe_stats_v2.json 回放生成原始分辨率画廊（不重跑模型）。

每球一张 JPG：person 框裁剪（原生分辨率）+ 黄十字出手点 + 顶部信息条
（行号/场次/键/归属/难例标记）。文件名带行号，用户点名坏行用。
"""
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from person_probe_v2 import (CROP_PAD, FRAME_OFFSETS, ORIG_WORK, VIDEO_DIR,
                             attribute, merge_fragments)

try:
    os.makedirs("gallery", exist_ok=True)
    stats = json.load(open("probe_stats_v2.json", encoding="utf-8"))
    # release_t 在 stats 里；shooters.json 拿 release_xy
    traces = {}
    for s in stats:
        v = s["video"]
        if v not in traces:
            traces[v] = json.load(open(os.path.join(ORIG_WORK, v, "shooters.json"),
                                       encoding="utf-8"))["traces"]
    caps = {}
    n = 0
    for i, s in enumerate(stats, 1):
        v, key = s["video"], s["key"]
        tr = traces[v][key]
        px, py = tr["release_xy"]
        # 回放选择逻辑：偏好序（frames 顺序）+ prio 最高
        best = None
        for fi, f in enumerate(s["frames"]):
            boxes = merge_fragments([tuple(b) for b in f["boxes_raw"]])
            hit, box, dist = attribute(px, py, boxes)
            prio = {"in": 3, "expand": 2, "nearest": 1, "miss": 0}[hit]
            if best is None or fi < best[0] or (fi == best[0] and prio > best[1]):
                best = (fi, prio, f["off"], hit, box)
        _, _, off, hit, box = best
        if hit == "miss":
            continue
        if v not in caps:
            caps[v] = cv2.VideoCapture(os.path.join(VIDEO_DIR, v + ".mp4"))
        caps[v].set(cv2.CAP_PROP_POS_MSEC, (tr["release_t"] + off) * 1000.0)
        ok, fr = caps[v].read()
        if not ok:
            continue
        H, W = fr.shape[:2]
        x1, y1, x2, y2 = box[:4]
        w, h = x2 - x1, y2 - y1
        cx1, cy1 = max(0, x1 - w * CROP_PAD), max(0, y1 - h * CROP_PAD)
        cx2, cy2 = min(W, x2 + w * CROP_PAD), min(H, y2 + h * CROP_PAD)
        crop = fr[int(cy1):int(cy2), int(cx1):int(cx2)].copy()
        rx, ry = px - cx1, py - cy1
        rx = max(0, min(crop.shape[1] - 1, int(rx)))
        ry = max(0, min(crop.shape[0] - 1, int(ry)))
        cv2.drawMarker(crop, (rx, ry), (0, 215, 255), cv2.MARKER_CROSS, 30, 3)
        bar = np.zeros((46, crop.shape[1], 3), np.uint8)
        txt = f"#{i:02d} {v[5:]} {key} [{s['kind']}] {hit} h/w={s.get('chosen_box_hw')}"
        cv2.putText(bar, txt, (10, 31), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                    (255, 210, 80), 2, cv2.LINE_AA)
        out = np.vstack([bar, crop])
        fn = f"gallery/{i:02d}_{v[5:]}_{key.replace(':', '-')}.jpg"
        cv2.imwrite(fn, out, [cv2.IMWRITE_JPEG_QUALITY, 92])
        n += 1
    for c in caps.values():
        c.release()
    print(f"saved {n} gallery images -> 诊断/2026-10-15-person框spike/gallery/")
except Exception:
    import traceback
    traceback.print_exc()
    raise
