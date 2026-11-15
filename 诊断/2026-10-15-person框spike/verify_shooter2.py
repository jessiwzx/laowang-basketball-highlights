# -*- coding: utf-8 -*-
"""身份自查 v2（throwaway）：用 HSV 检球（与溯源同款技术）做「球在谁手上」。

v1 用自训 YOLO 检球失败：远景中段真球检不到（14 帧无检出、21 帧最近
检出 150~680px = 假候选），仪器不可用。HSV 是当初回溯出手点的同款方法，
对这两场验证过。口径照抄 shooter._scan_hsv（S 下限抬到 88）+ MIN_AREA=9。

判据：
  - 出手时刻/前 0.2s 两帧里，离出手点最近的 HSV 球块距离 ≤45px
    → 出手点有效，球块即球；
  - 球块中心落在哪个合并 person 框（15%→30% 容差）→ 该框=持球人；
  - 持球框 vs 探针选中框 IoU>0.5 → OK；是别的框 → WRONG（认错人实锤）。
产出：verify_stats2.json + gallery_verify2/（橙圈=HSV 球块，绿框=选中框）。
"""
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from person_probe_v2 import ORIG_WORK, VIDEO_DIR, attribute, merge_fragments

SCAN_S_FLOOR = 88
MIN_AREA = 9
RELEASE_TOL = 45.0
MARGINS = (0.15, 0.30)
IOU_OK = 0.5


def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / max(ua, 1e-6)


def contains(b, x, y, k):
    w, h = b[2] - b[0], b[3] - b[1]
    return (b[0] - w * k) <= x <= (b[2] + w * k) and (b[1] - h * k) <= y <= (b[3] + h * k)


def hsv_blobs(frame, lo, hi):
    """inRange + 开运算 + 连通域，返回 [(cx, cy, area)]（area>=MIN_AREA）。"""
    mask = cv2.inRange(cv2.cvtColor(frame, cv2.COLOR_BGR2HSV), lo, hi)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    out = []
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if a >= MIN_AREA:
            out.append((x + w / 2, y + h / 2, int(a)))
    return out


def main():
    stats = json.load(open("probe_stats_v2.json", encoding="utf-8"))
    traces, calibs, caps = {}, {}, {}
    os.makedirs("gallery_verify2", exist_ok=True)
    outs = []
    for i, s in enumerate(stats, 1):
        v, key = s["video"], s["key"]
        if v not in traces:
            traces[v] = json.load(open(os.path.join(ORIG_WORK, v, "shooters.json"),
                                       encoding="utf-8"))["traces"]
            # 标定统一从 positions.json 读（08-28 的 work 目录没有
            # calibration.json，但 positions.json 内嵌同一份 HSV 范围）。
            # 多段场（09-04）是 segments[].calib + t0/t1，按出手时刻选段；
            # 单段场是顶层 calib。calib = [hoop_rect, hsv_low, hsv_high, ...]
            pj = json.load(open(os.path.join(ORIG_WORK, v, "positions.json"),
                                encoding="utf-8"))
            if "segments" in pj:
                src = [(s["t0"], s["t1"], s["calib"]) for s in pj["segments"]]
            else:
                src = [(0.0, None, pj["calib"])]
            calibs[v] = []
            for t0, t1, c in src:
                lo = list(c[1])
                if c[2][1] > SCAN_S_FLOOR:
                    lo[1] = max(lo[1], SCAN_S_FLOOR)
                calibs[v].append((t0, t1, np.array(lo, np.uint8),
                                  np.array(c[2], np.uint8)))
        tr = traces[v][key]
        rt, (px, py) = tr["release_t"], tr["release_xy"]
        best = None
        for fi, f in enumerate(s["frames"]):
            boxes = merge_fragments([tuple(b) for b in f["boxes_raw"]])
            hit, box, dist = attribute(px, py, boxes)
            prio = {"in": 3, "expand": 2, "nearest": 1, "miss": 0}[hit]
            if best is None or fi < best[0] or (fi == best[0] and prio > best[1]):
                best = (fi, prio, f["off"], hit, box, boxes)
        _, _, off, hit, chosen, boxes = best
        if v not in caps:
            caps[v] = cv2.VideoCapture(os.path.join(VIDEO_DIR, v + ".mp4"))
        lo = hi = None
        for t0, t1, l, h in calibs[v]:
            if rt >= t0 and (t1 is None or rt < t1):
                lo, hi = l, h
                break
        if lo is None:                     # 出手时刻落在段间隙 → 用最后一段
            lo, hi = calibs[v][-1][2], calibs[v][-1][3]
        blob_best = None   # (dist, cx, cy, frame)
        for dt in (0.0, -0.2):
            caps[v].set(cv2.CAP_PROP_POS_MSEC, (rt + off + dt) * 1000.0)
            ok, fr = caps[v].read()
            if not ok:
                continue
            for cx, cy, a in hsv_blobs(fr, lo, hi):
                d = ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5
                if blob_best is None or d < blob_best[0]:
                    blob_best = (d, cx, cy, fr.copy(), a)
        rec = {"i": i, "video": v, "key": key, "kind": s["kind"],
               "chosen_hit": hit}
        fr = None
        if blob_best is None:
            rec["verdict"] = "hsv_no_ball"
            outs.append(rec)
            continue
        d, cx, cy, fr, area = blob_best
        rec["ball_dist"] = round(d, 1)
        rec["ball_area"] = area
        if d > RELEASE_TOL:
            rec["verdict"] = "release_suspect"
        else:
            holder, tag = None, "no_holder"
            for k in MARGINS:
                hs = [b for b in boxes if contains(b, cx, cy, k)]
                if hs:
                    holder, tag = max(hs, key=lambda b: b[3] - b[1]), f"holder@{k}"
                    break
            if holder is None and boxes:
                holder = min(boxes, key=lambda b: ((cx - (b[0] + b[2]) / 2) ** 2
                                                   + (cy - b[3]) ** 2))
                tag = "holder_nearest"
            if holder is not None:
                rec["holder_iou"] = round(iou(holder, chosen), 2)
                rec["verdict"] = tag + ("/ok" if iou(holder, chosen) > IOU_OK
                                        else "/WRONG")
            else:
                rec["verdict"] = tag
        if hit != "miss" and fr is not None:
            H, W = fr.shape[:2]
            x1, y1, x2, y2 = chosen[:4]
            w, h = x2 - x1, y2 - y1
            cx1, cy1 = max(0, x1 - w * 0.45), max(0, y1 - h * 0.45)
            cx2, cy2 = min(W, x2 + w * 0.45), min(H, y2 + h * 0.45)
            crop = fr[int(cy1):int(cy2), int(cx1):int(cx2)].copy()
            okc = "WRONG" not in rec["verdict"] and "suspect" not in rec["verdict"]
            cv2.rectangle(crop, (int(x1 - cx1), int(y1 - cy1)),
                          (int(x2 - cx1), int(y2 - cy1)),
                          (0, 220, 0) if okc else (0, 0, 255), 2)
            cv2.circle(crop, (int(cx - cx1), int(cy - cy1)), 24, (0, 140, 255), 3)
            cv2.drawMarker(crop, (int(px - cx1), int(py - cy1)), (0, 215, 255),
                           cv2.MARKER_CROSS, 26, 2)
            bar = np.zeros((46, crop.shape[1], 3), np.uint8)
            cv2.putText(bar, f"#{i:02d} {v[5:]} {key} {rec['verdict']}"
                        f" d={rec.get('ball_dist')} a={rec.get('ball_area')}",
                        (10, 31), cv2.FONT_HERSHEY_SIMPLEX, 0.72,
                        (255, 210, 80), 2, cv2.LINE_AA)
            cv2.imwrite(f"gallery_verify2/{i:02d}_{v[5:]}_{key.replace(':', '-')}.jpg",
                        np.vstack([bar, crop]), [cv2.IMWRITE_JPEG_QUALITY, 92])
        outs.append(rec)
    for c in caps.values():
        c.release()
    json.dump(outs, open("verify_stats2.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    from collections import Counter
    print("verdict 分布:", dict(Counter(o["verdict"] for o in outs)))
    print("\n非 OK 行：")
    for o in outs:
        if not o["verdict"].endswith("/ok"):
            print(f"  #{o['i']:02d} {o['video'][5:]} {o['key']} [{o['kind']}]"
                  f" {o['verdict']} d={o.get('ball_dist')} a={o.get('ball_area')}")


if __name__ == "__main__":
    main()
