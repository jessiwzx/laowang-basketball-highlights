# -*- coding: utf-8 -*-
"""身份自查（throwaway）：出手时刻球在谁手上，谁才是出手人。

方法：对画廊 36 行的选定帧（出手时刻）及其前 0.2s 各跑一次自训检球模型
（bbh/weights/best.pt, ball 类），取离出手点最近的球心：
  - 球心距出手点 > 60px → 出手点本身存疑（上游溯源偏差），记 release_suspect
  - 球心落在哪个合并后 person 框内（含 15% 外扩容差）→ 该框=持球人
  - 持球框 vs 探针选中框：IoU>0.5 → OK；含球的是别的框 → WRONG；
    球不在任何框（手举过头顶/两人之间）→ no_holder（按距离最近框再判一次）
产出：verify_stats.json + gallery_verify/（球圈绿=一致 红=认错 橙=存疑）。
"""
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from person_probe_v2 import (ORIG_WORK, VIDEO_DIR, attribute, merge_fragments)

BALL_W = r"D:\claude code\视频剪辑YOLO\bbh\weights\best.pt"
BALL_CONF, BALL_IMGSZ = 0.05, 1280
MARGIN = 0.15          # 持球判定：球心允许出框 15% 框宽/高
RELEASE_TOL = 60.0     # 球心距出手点超过此值 → 出手点存疑（px，720p）
IOU_OK = 0.5


def iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / max(ua, 1e-6)


def contains(b, x, y, k=MARGIN):
    w, h = b[2] - b[0], b[3] - b[1]
    return (b[0] - w * k) <= x <= (b[2] + w * k) and (b[1] - h * k) <= y <= (b[3] + h * k)


def main():
    from ultralytics import YOLO
    ball_m = YOLO(BALL_W)
    stats = json.load(open("probe_stats_v2.json", encoding="utf-8"))
    traces = {}
    caps, outs = {}, []
    os.makedirs("gallery_verify", exist_ok=True)
    for i, s in enumerate(stats, 1):
        v, key = s["video"], s["key"]
        if v not in traces:
            traces[v] = json.load(open(os.path.join(ORIG_WORK, v, "shooters.json"),
                                       encoding="utf-8"))["traces"]
        tr = traces[v][key]
        rt, (px, py) = tr["release_t"], tr["release_xy"]
        # 回放探针的选择逻辑拿选中帧与选中框
        best = None
        for fi, f in enumerate(s["frames"]):
            boxes = merge_fragments([tuple(b) for b in f["boxes_raw"]])
            hit, box, dist = attribute(px, py, boxes)
            prio = {"in": 3, "expand": 2, "nearest": 1, "miss": 0}[hit]
            if best is None or fi < best[0] or (fi == best[0] and prio > best[1]):
                best = (fi, prio, f["off"], hit, box, boxes)
        _, _, off, hit, chosen, boxes = best
        # 出手时刻 + 前 0.2s 两帧检球，取离出手点最近的球心
        if v not in caps:
            caps[v] = cv2.VideoCapture(os.path.join(VIDEO_DIR, v + ".mp4"))
        ball_best = None   # (dist, bx, cy, frame)
        for dt in (0.0, -0.2):
            caps[v].set(cv2.CAP_PROP_POS_MSEC, (rt + off + dt) * 1000.0)
            ok, fr = caps[v].read()
            if not ok:
                continue
            r = ball_m(fr, classes=[0], conf=BALL_CONF, imgsz=BALL_IMGSZ,
                       verbose=False)[0]
            for b in r.boxes:
                bx, by = float(b.xyxy[0][0] + b.xyxy[0][2]) / 2, float(b.xyxy[0][1] + b.xyxy[0][3]) / 2
                d = ((bx - px) ** 2 + (by - py) ** 2) ** 0.5
                if ball_best is None or d < ball_best[0]:
                    ball_best = (d, bx, by, fr.copy())
        rec = {"i": i, "video": v, "key": key, "kind": s["kind"],
               "chosen_hit": hit}
        if ball_best is None:
            rec["verdict"] = "no_ball"
            outs.append(rec)
            continue
        d, bx, by, fr = ball_best
        rec["ball_dist_to_release"] = round(d, 1)
        if d > RELEASE_TOL:
            # 球不在出手点附近：要么出手点偏，要么此刻球已离手太远
            # 再看前 0.2s 帧的球距（上面已并入 ball_best 取最近），仍超 → 存疑
            rec["verdict"] = "release_suspect"
        else:
            holders = [b for b in boxes if contains(b, bx, by)]
            if not holders:
                # 手举过头顶/两人之间：按扩 30% 再判
                holders = [b for b in boxes if contains(b, bx, by, 0.30)]
            if not holders:
                holder = min(boxes, key=lambda b: ((bx - (b[0] + b[2]) / 2) ** 2
                                                   + (by - b[3]) ** 2)) if boxes else None
                tag = "holder_by_nearest"
            else:
                holder = max(holders, key=lambda b: b[3] - b[1])
                tag = "holder_in_box"
            if holder is None:
                rec["verdict"] = "no_holder"
            else:
                rec["holder_iou_chosen"] = round(iou(holder, chosen), 2)
                rec["verdict"] = (tag + "/ok" if iou(holder, chosen) > IOU_OK
                                  else tag + "/WRONG")
        # 标注图
        if hit != "miss":
            H, W = fr.shape[:2]
            x1, y1, x2, y2 = chosen[:4]
            w, h = x2 - x1, y2 - y1
            cx1, cy1 = max(0, x1 - w * 0.45), max(0, y1 - h * 0.45)
            cx2, cy2 = min(W, x2 + w * 0.45), min(H, y2 + h * 0.45)
            crop = fr[int(cy1):int(cy2), int(cx1):int(cx2)].copy()
            color = {"ok": (0, 220, 0), "WRONG": (0, 0, 255)}.get(
                "WRONG" if "WRONG" in rec["verdict"] else
                "release_suspect" if "suspect" in rec["verdict"] else "ok",
                (0, 160, 255))
            rbx, rby = int(bx - cx1), int(by - cy1)
            cv2.circle(crop, (max(0, min(crop.shape[1] - 1, rbx)),
                              max(0, min(crop.shape[0] - 1, rby))),
                       26, color, 3)
            cv2.drawMarker(crop, (max(0, min(crop.shape[1] - 1, int(px - cx1))),
                                  max(0, min(crop.shape[0] - 1, int(py - cy1)))),
                           (0, 215, 255), cv2.MARKER_CROSS, 26, 2)
            bar = np.zeros((46, crop.shape[1], 3), np.uint8)
            cv2.putText(bar, f"#{i:02d} {v[5:]} {key} {rec['verdict']}"
                        f" balld={rec.get('ball_dist_to_release')}",
                        (10, 31), cv2.FONT_HERSHEY_SIMPLEX, 0.75,
                        (255, 210, 80), 2, cv2.LINE_AA)
            cv2.imwrite(f"gallery_verify/{i:02d}_{v[5:]}_{key.replace(':', '-')}.jpg",
                        np.vstack([bar, crop]), [cv2.IMWRITE_JPEG_QUALITY, 92])
        outs.append(rec)
    for c in caps.values():
        c.release()
    json.dump(outs, open("verify_stats.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    from collections import Counter
    print("verdict 分布:", dict(Counter(o["verdict"] for o in outs)))
    for o in outs:
        if "WRONG" in o["verdict"] or "suspect" in o["verdict"]:
            print(f"  #{o['i']:02d} {o['video'][5:]} {o['key']} [{o['kind']}]"
                  f" {o['verdict']} balld={o.get('ball_dist_to_release')}")


if __name__ == "__main__":
    main()
