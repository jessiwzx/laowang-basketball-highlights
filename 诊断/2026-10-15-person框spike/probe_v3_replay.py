# -*- coding: utf-8 -*-
"""v3 重放（throwaway）：归属锚点 = 出手时刻的 HSV 球块，回测 36 球。

v2 的 2 个 WRONG 根因：同一点被多人框交叠，点归属的排序偏好（躯干段→
整身高）选错。v3：先在偏好帧上找离出手点 ≤60px 的 HSV 球块，归属改为
「含球的框」优先，无球块才退回「含点的框」。球在谁手上就是谁。
回放不重跑 person 模型（用 probe_stats_v2.json 里的框），只补 HSV 球块。
产出：v3 与 v2 的选中框差异 + 用球-人一致性标准对 v3 打分。
"""
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from person_probe_v2 import ORIG_WORK, VIDEO_DIR, attribute, merge_fragments
from verify_shooter2 import SCAN_S_FLOOR, MIN_AREA, hsv_blobs, iou, contains

BALL_ANCHOR_TOL = 60.0


def main():
    stats = json.load(open("probe_stats_v2.json", encoding="utf-8"))
    traces, calibs, caps = {}, {}, {}
    changed, results = [], []
    for i, s in enumerate(stats, 1):
        v, key = s["video"], s["key"]
        if v not in traces:
            traces[v] = json.load(open(os.path.join(ORIG_WORK, v, "shooters.json"),
                                       encoding="utf-8"))["traces"]
            pj = json.load(open(os.path.join(ORIG_WORK, v, "positions.json"),
                                encoding="utf-8"))
            if "segments" in pj:
                src = [(g["t0"], g["t1"], g["calib"]) for g in pj["segments"]]
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
        # 回放 v2 选择（帧偏好序 + prio）拿帧 off 与 v2 选中框
        best = None
        for fi, f in enumerate(s["frames"]):
            boxes = merge_fragments([tuple(b) for b in f["boxes_raw"]])
            hit, box, dist = attribute(px, py, boxes)
            prio = {"in": 3, "expand": 2, "nearest": 1, "miss": 0}[hit]
            if best is None or fi < best[0] or (fi == best[0] and prio > best[1]):
                best = (fi, prio, f["off"], hit, box, boxes)
        _, _, off, hit2, chosen2, boxes = best
        # v3：偏好帧上找球块（含前 0.2s 帧）
        if v not in caps:
            caps[v] = cv2.VideoCapture(os.path.join(VIDEO_DIR, v + ".mp4"))
        lo = hi = None
        for t0, t1, l, h in calibs[v]:
            if rt >= t0 and (t1 is None or rt < t1):
                lo, hi = l, h
                break
        ball = None    # (bx, by)
        for dt in (0.0, -0.2):
            caps[v].set(cv2.CAP_PROP_POS_MSEC, (rt + off + dt) * 1000.0)
            ok, fr = caps[v].read()
            if not ok:
                continue
            for cx, cy, a in hsv_blobs(fr, lo, hi):
                d = ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5
                if d <= BALL_ANCHOR_TOL and (ball is None or
                                             d < ball[0]):
                    ball = (d, cx, cy)
        # v3 归属：含球框 > 含点框（其余排序同 v2）
        anchor = (ball[1], ball[2]) if ball else None
        hit3, chosen3 = attribute_v3(anchor, px, py, boxes)
        same = chosen2 == chosen3 or (chosen2 and chosen3
                                      and iou(chosen2, chosen3) > 0.9)
        # 对 v3 打分：持球框 vs v3 选中框（无框含球时按最近框降级判）
        if ball:
            holder = None
            for k in (0.15, 0.30):
                hs = [b for b in boxes if contains(b, ball[1], ball[2], k)]
                if hs:
                    holder = max(hs, key=lambda b: b[3] - b[1])
                    break
            if holder is None:
                holder = min(boxes, key=lambda b: ((ball[1] - (b[0] + b[2]) / 2) ** 2
                                                   + (ball[2] - b[3]) ** 2))
                verdict = "holder_nearest/" + ("ok" if iou(holder, chosen3) > 0.5
                                               else "WRONG")
            else:
                verdict = "ok" if iou(holder, chosen3) > 0.5 else "WRONG"
        else:
            verdict = "no_ball(低置信)"
        results.append({"i": i, "key": key, "video": v, "kind": s["kind"],
                        "ball_found": ball is not None, "v3_hit": hit3,
                        "changed": not same, "verdict": verdict})
        if not same:
            changed.append((i, key, v[5:]))
    for c in caps.values():
        c.release()
    from collections import Counter
    print("v3 持球一致性:", dict(Counter(r["verdict"] for r in results)))
    print("v3 相对 v2 换框的行:", changed)
    okc = [r for r in results if r["verdict"] == "ok"]
    print(f"球块锚定率: {sum(1 for r in results if r['ball_found'])}/{len(results)}")
    json.dump(results, open("v3_replay.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


def _rank(b, ax, ay):
    tp = (ay - b[1]) / max(b[3] - b[1], 1e-6)
    return (1 if 0.10 <= tp <= 0.90 else 0, b[3] - b[1], b[4])


def attribute_v2(px, py, boxes):
    """回放 v2 选择用（与 person_probe_v2.attribute 同判定）。"""
    if not boxes:
        return "miss", None
    hits = [b for b in boxes if b[0] <= px <= b[2] and b[1] <= py <= b[3]]
    if hits:
        return "in", max(hits, key=lambda b: _rank(b, px, py))
    return "not_in", None


def attribute_v3(ball, px, py, boxes):
    """球锚阶梯：含球@0.15 → 含球@0.30 → 点锚（v2 全链：in→expand→nearest）。"""
    if not boxes:
        return "miss", None
    if ball:
        bx, by = ball
        for k in (0.15, 0.30):
            hits = [b for b in boxes if contains(b, bx, by, k)]
            if hits:
                return "in_ball", max(hits, key=lambda b: _rank(b, bx, by))
    hits = [b for b in boxes if b[0] <= px <= b[2] and b[1] <= py <= b[3]]
    if hits:
        return "in", max(hits, key=lambda b: _rank(b, px, py))
    ex = [b for b in boxes if contains(b, px, py, 0.30)]
    if ex:
        return "expand", max(ex, key=lambda b: _rank(b, px, py))

    def dist(b):
        return ((px - (b[0] + b[2]) / 2) ** 2 + (py - b[3]) ** 2) ** 0.5
    return "nearest", min(boxes, key=dist)


if __name__ == "__main__":
    main()
