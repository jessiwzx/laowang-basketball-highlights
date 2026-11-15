# -*- coding: utf-8 -*-
"""生产链路自测（throwaway）：36 球全走 shooter.crop_shooter 真跑。

与 spike 探针的区别：这次调的是**生产函数**（bbh/shooter.crop_shooter +
bbh/person 全链，真权重 bbh/weights/yolov8n.pt），验证打包路径/懒加载/
帧偏好/spot 兜底/裁剪几何/归属，产品 = prod_out/shooters/*_a.jpg。

打分（同 verify_shooter2 物理判据，独立于生产逻辑）：
  首选帧上 HSV 球块（出手点 60px 内最近）落在哪个合并 person 框
  → 持球框 vs 生产选中框 IoU>0.5 → ok；别的框 → WRONG。
对照 spike v3 结论：35/36 ok（0 WRONG）+ 1 no_ball 低置信。
"""
import json
import os
import shutil
import sys

sys.path.insert(0, r"D:\claude code\视频剪辑YOLO")
os.environ["BBH_PERSON"] = "on"

import cv2
import numpy as np

from bbh import person, shooter
from bbh.models import Calibration

ORIG_WORK = r"D:\claude code\视频剪辑\work"
VIDEO_DIR = r"D:\claude code\1视频获取\归档"
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "prod_out")


def calibs_of(pj):
    src = ([(g["t0"], g["t1"], g["calib"]) for g in pj["segments"]]
           if "segments" in pj else [(0.0, None, pj["calib"])])
    out = []
    for t0, t1, c in src:
        out.append((t0, t1, Calibration(hoop_rect=tuple(c[0]),
                                        hsv_low=tuple(c[1]),
                                        hsv_high=tuple(c[2]),
                                        frame_size=tuple(c[3]))))
    return out


def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / max(ua, 1e-6)


def main():
    stats = json.load(open(os.path.join(HERE, "probe_stats_v2.json"),
                           encoding="utf-8"))
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(os.path.join(OUT, "bad"), exist_ok=True)
    pj_cache, tr_cache = {}, {}
    rows = []
    for i, s in enumerate(stats, 1):
        v, key = s["video"], s["key"]
        if v not in pj_cache:
            pj_cache[v] = json.load(open(os.path.join(ORIG_WORK, v,
                                                      "positions.json"),
                                         encoding="utf-8"))
            tr_cache[v] = json.load(open(os.path.join(ORIG_WORK, v,
                                                      "shooters.json"),
                                         encoding="utf-8"))["traces"]
        tdoc = tr_cache[v][key]
        rt, xy = tdoc["release_t"], tuple(tdoc["release_xy"])
        calibs = calibs_of(pj_cache[v])
        sc = None
        for t0, t1, c in calibs:
            if rt >= t0 and (t1 is None or rt < t1):
                sc = c
                break
        if sc is None:
            sc = calibs[-1][2]
        # ---- 生产链路：真跑 crop_shooter（缩略图落 prod_out）----
        tr = shooter.TraceResult(status=tdoc.get("status", "low"),
                                 release_t=rt, release_xy=xy)
        thumbs = shooter.crop_shooter(os.path.join(VIDEO_DIR, v + ".mp4"),
                                      sc, tr, OUT, key)
        # ---- 独立打分（首选帧球锚一致性）----
        frame = shooter._grab_at(os.path.join(VIDEO_DIR, v + ".mp4"), rt)
        verdict = "no_frame"
        if frame is not None:
            lo, hi = shooter._scan_hsv(sc)
            raw = person._detect_raw(frame)
            boxes = person.merge_fragments(raw)
            ball = person.ball_anchor(frame, lo, hi, *xy)
            hit, box = person.attribute(boxes, ball, *xy)
            if ball is None:
                verdict = "no_ball(低置信)"
            elif not boxes:
                verdict = "no_person(低置信)"
            else:
                holder = None
                for k in (0.15, 0.30):
                    hs = [b for b in boxes
                          if person._contains(b, ball[0], ball[1], k)]
                    if hs:
                        holder = max(hs, key=lambda b: b[3] - b[1])
                        break
                if holder is None:
                    holder = min(boxes, key=lambda b:
                                 ((ball[0] - (b[0] + b[2]) / 2) ** 2
                                  + (ball[1] - b[3]) ** 2))
                    tag = "nearest"
                else:
                    tag = "in"
                verdict = tag + ("/ok" if iou(holder, box) > 0.5 else "/WRONG")
                # 坏行标注图（原生分辨率：绿=生产选中框 橙圈=球 黄十字=出手点）
                if "WRONG" in verdict:
                    H, W = frame.shape[:2]
                    x0, y0, x1, y1 = person.crop_rect(box, W, H)
                    crop = frame[y0:y1, x0:x1].copy()
                    cv2.rectangle(crop, (int(box[0]) - x0, int(box[1]) - y0),
                                  (int(box[2]) - x0, int(box[3]) - y0),
                                  (0, 220, 0), 3)
                    cv2.circle(crop, (int(ball[0]) - x0, int(ball[1]) - y0),
                               22, (0, 140, 255), 3)
                    cv2.drawMarker(crop, (int(xy[0]) - x0, int(xy[1]) - y0),
                                   (0, 215, 255), cv2.MARKER_CROSS, 24, 2)
                    cv2.imwrite(f"{OUT}/bad/{i:02d}_{v[5:]}_"
                                f"{key.replace(':', '-')}.jpg", crop)
        rows.append({"i": i, "video": v, "key": key, "kind": s["kind"],
                     "prod_person": tr.person,
                     "prod_thumbs": len(thumbs), "verdict": verdict})
        print(f"#{i:02d} {v[5:]} {key} [{s['kind']}] prod={tr.person:<6}"
              f" thumbs={len(thumbs)} {verdict}", flush=True)
    json.dump(rows, open(os.path.join(OUT, "selftest.json"), "w",
                         encoding="utf-8"), ensure_ascii=False, indent=1)
    from collections import Counter
    c = Counter(r["verdict"] for r in rows)
    print("\n==== 生产自测汇总（36 球）====")
    print("verdict:", dict(c))
    print("生产归属档:", dict(Counter(r["prod_person"] for r in rows)))
    print("缩略图张数异常行:",
          [r["i"] for r in rows if r["prod_thumbs"] == 0])
    bad = [r for r in rows if "WRONG" in r["verdict"]]
    print("WRONG 行:", [(r["i"], r["video"][5:], r["key"]) for r in bad])


if __name__ == "__main__":
    main()
