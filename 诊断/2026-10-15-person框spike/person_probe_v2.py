# -*- coding: utf-8 -*-
"""Spike 探针 v2（throwaway）：修复 v1 两个根因后重测。

用户目检 v1 报告：① 定位到非出手人；② 裁剪落在人脸/人腿上。

v1 两个根因与本版修法：
  R1 帧选择 bug：`prio > best` 使同优先级时**最早帧（t-0.6）获胜**——出手
     前 0.6s 出手点可能被别人（防守者）的框盖住 → 认错人。
     v2：**就近优先**（t=0.0 > -0.3 > -0.6），出手点只在出手时刻有意义。
  R2 碎片框：远景遮挡下 COCO 把同一人拆成「脸框 + 腿框」，点落进哪个
     碎片就裁哪个 → 拼图里出现纯脸/纯腿。
     v2：归属前先**纵邻合并**（横向重叠过半 + 纵向间隙 <30% 高 → 并框），
     多框同含点时偏好「点落在躯干段（10%~90% 高）的整身框」。

同 v1 采样（seed 42、同池同序）→ 同 36 球可逐行对比归属变化。
产出：probe_stats_v2.json（含每帧全部框坐标，供碎片率统计）、
对照拼图v2_<video>.png、归属对比 v1→v2 摘要。
"""
import json
import os
import random
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ORIG_WORK = r"D:\claude code\视频剪辑\work"
VIDEO_DIR = r"D:\claude code\1视频获取\归档"
VIDEOS = ["2026-08-28_1", "2026-09-04_1"]
N_NORMAL, N_HARD = 16, 4
FRAME_OFFSETS = (0.0, -0.3, -0.6)   # 就近优先（顺序即偏好序）
EXPAND = 0.30
CROP_PAD = 0.45
CONF = 0.25
IMGSZ = 1280

random.seed(42)


def log(*a):
    print(*a, flush=True)


def load_model():
    from ultralytics import YOLO
    return YOLO("yolov8n.pt")


def persons_of(model, frame_bgr):
    r = model(frame_bgr, classes=[0], conf=CONF, imgsz=IMGSZ, verbose=False)[0]
    return [(float(b.xyxy[0][0]), float(b.xyxy[0][1]), float(b.xyxy[0][2]),
             float(b.xyxy[0][3]), float(b.conf[0])) for b in r.boxes]


# ---------------------------------------------------------------- 碎片合并
def merge_fragments(boxes):
    """纵邻合并：两框横向重叠 > 较窄者 55%，且纵向间隙 < 30% 高 → 并成一框。

    针对「同人被拆成脸框+腿框」：碎片在竖直方向堆叠、横向几乎对齐。
    conf 取两者最大（合并框是整身，可信度不低于碎片）。"""
    boxes = [list(b) for b in boxes]
    merged = True
    while merged:
        merged = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                ox = min(a[2], b[2]) - max(a[0], b[0])
                if ox <= 0:
                    continue
                wmin = min(a[2] - a[0], b[2] - b[0])
                if ox < 0.55 * wmin:
                    continue
                gap = max(a[1], b[1]) - min(a[3], b[3])   # 纵向间隙（负=重叠）
                hmax = max(a[3] - a[1], b[3] - b[1])
                if gap > 0.30 * hmax:
                    continue
                u = [min(a[0], b[0]), min(a[1], b[1]),
                     max(a[2], b[2]), max(a[3], b[3]), max(a[4], b[4])]
                boxes[i] = u
                boxes.pop(j)
                merged = True
                break
            if merged:
                break
    return [tuple(b) for b in boxes]


def torso_pos(b, py):
    """点在框内的纵向位置（0=框顶 1=框底）。"""
    return (py - b[1]) / max(b[3] - b[1], 1e-6)


def attribute(px, py, boxes):
    """v2 归属：in（多框含点时偏好躯干段+整身）> expand > nearest > miss。"""
    if not boxes:
        return "miss", None, None
    hits = [b for b in boxes if b[0] <= px <= b[2] and b[1] <= py <= b[3]]
    if hits:
        def rank(b):
            tp = torso_pos(b, py)
            torso_ok = 1 if 0.10 <= tp <= 0.90 else 0     # 躯干段优先于头顶/脚底
            return (torso_ok, b[3] - b[1], b[4])          # 整身高 > 置信度
        return "in", max(hits, key=rank), 0.0
    ex = []
    for b in boxes:
        x1, y1, x2, y2 = (b[0] - (b[2] - b[0]) * EXPAND, b[1] - (b[3] - b[1]) * EXPAND,
                          b[2] + (b[2] - b[0]) * EXPAND, b[3] + (b[3] - b[1]) * EXPAND)
        if x1 <= px <= x2 and y1 <= py <= y2:
            ex.append(b)
    if ex:
        return "expand", max(ex, key=lambda b: b[3] - b[1]), 0.0

    def dist(b):
        return ((px - (b[0] + b[2]) / 2) ** 2 + (py - b[3]) ** 2) ** 0.5
    b = min(boxes, key=dist)
    return "nearest", b, round(dist(b), 1)


# ---------------------------------------------------------------- 采样（同 v1）
def sample(video):
    doc = json.load(open(os.path.join(ORIG_WORK, video, "shooters.json"),
                         encoding="utf-8"))
    tr = doc["traces"]
    def has_xy(v):
        return v.get("release_xy") and v.get("release_t") is not None
    normal = {k: v for k, v in tr.items()
              if has_xy(v) and v.get("status") == "high"
              and v.get("spot", "ok") == "ok" and not v.get("extrap")}
    hard = {k: v for k, v in tr.items()
            if has_xy(v) and (v.get("spot") == "bad" or v.get("extrap")
                              or v.get("status") == "low")}
    kn, kh = sorted(normal), sorted(hard)
    random.shuffle(kn)
    random.shuffle(kh)
    return ([(k, "normal") for k in kn[:N_NORMAL]]
            + [(k, "hard") for k in kh[:N_HARD]]), tr


def frame_at(cap, t_sec):
    cap.set(cv2.CAP_PROP_POS_MSEC, t_sec * 1000.0)
    ok, fr = cap.read()
    return fr if ok else None


def crop_person_frame(frame, box, px, py):
    H, W = frame.shape[:2]
    x1, y1, x2, y2 = box[:4]
    w, h = x2 - x1, y2 - y1
    cx1, cy1 = max(0, x1 - w * CROP_PAD), max(0, y1 - h * CROP_PAD)
    cx2, cy2 = min(W, x2 + w * CROP_PAD), min(H, y2 + h * CROP_PAD)
    crop = frame[int(cy1):int(cy2), int(cx1):int(cx2)].copy()
    return crop, (px - cx1, py - cy1)


def draw_marker(img_bgr, xy, color=(0, 215, 255)):
    H, W = img_bgr.shape[:2]
    x, y = int(xy[0]), int(xy[1])
    x, y = max(0, min(W - 1, x)), max(0, min(H - 1, y))
    cv2.drawMarker(img_bgr, (x, y), color, cv2.MARKER_CROSS, 22, 2)
    return img_bgr


def main():
    model = load_model()
    v1 = {s["key"]: s for s in json.load(open("probe_stats.json",
                                              encoding="utf-8"))}
    stats, frag_all = [], {"raw": 0, "merged": 0, "n_merge": 0}
    for video in VIDEOS:
        pick, tr = sample(video)
        cap = cv2.VideoCapture(os.path.join(VIDEO_DIR, video + ".mp4"))
        rows = []
        for key, kind in pick:
            v = tr[key]
            rt, (px, py) = v["release_t"], v["release_xy"]
            best = None      # (偏好序, hit_prio, t, hit, box, frame, n_person)
            frames_dump = []
            for fi, off in enumerate(FRAME_OFFSETS):     # 顺序即偏好序
                fr = frame_at(cap, rt + off)
                if fr is None:
                    continue
                raw = persons_of(model, fr)
                boxes = merge_fragments(raw)
                frag_all["raw"] += len(raw)
                frag_all["merged"] += len(boxes)
                frag_all["n_merge"] += len(raw) - len(boxes)
                hit, box, dist = attribute(px, py, boxes)
                frames_dump.append({
                    "off": off, "n_person_raw": len(raw), "n_person": len(boxes),
                    "hit": hit, "dist": dist,
                    "boxes_raw": [[round(x, 1) for x in b] for b in raw],
                })
                prio = {"in": 3, "expand": 2, "nearest": 1, "miss": 0}[hit]
                cand = (fi, prio, rt + off, hit, box, fr, len(boxes))
                # 就近优先：偏好序小者胜；同偏好序下 prio 高者胜
                if best is None or cand[0] < best[0] or (cand[0] == best[0]
                                                         and cand[1] > best[1]):
                    best = cand
            if best is None:
                stats.append({"video": video, "key": key, "kind": kind,
                              "error": "no frame"})
                continue
            _, _, t_use, hit, box, fr, n_person = best
            old = v1.get(key, {})
            old_img = None
            th = (v.get("thumbs") or [None])[0]
            if th:
                p = os.path.join(ORIG_WORK, video, th)
                if os.path.exists(p):
                    old_img = cv2.imread(p)
            if hit != "miss":
                crop, rel = crop_person_frame(fr, box, px, py)
                crop = draw_marker(crop, rel)
                ch, cw = crop.shape[:2]
                new_ratio = round((box[2] - box[0]) * (box[3] - box[1]) / (cw * ch), 3)
                bh, bw = box[3] - box[1], box[2] - box[0]
                hw_ratio = round(bh / max(bw, 1e-6), 2)
            else:
                crop = draw_marker(fr.copy(), (px, py))
                new_ratio, hw_ratio = 0.0, None
            rows.append((key, kind, hit, old_img, crop, new_ratio, hw_ratio,
                         n_person, v.get("spot"), v.get("status")))
            stats.append({
                "video": video, "key": key, "kind": kind,
                "status": v.get("status"), "spot": v.get("spot"),
                "release_t": round(rt, 2), "frames": frames_dump,
                "chosen_hit": hit, "n_person": n_person,
                "chosen_box_hw": hw_ratio,
                "new_person_area": new_ratio,
                "v1_hit": old.get("chosen_hit"),
            })
        cap.release()
        make_montage(video, rows)
    json.dump(stats, open("probe_stats_v2.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    summarize(stats, frag_all)


def make_montage(video, rows):
    CW = 360
    pad, head = 6, 34
    try:
        font = ImageFont.truetype("msyh.ttc", 15)
    except OSError:
        font = ImageFont.load_default()
    imgs = []
    for key, kind, hit, old, crop, new_r, hw, n_person, spot, status in rows:
        line = []
        for img, tag in ((old, f"旧 {status}/{spot or '-'}"),
                         (crop, f"新 {hit} h/w={hw}")):
            if img is None:
                img = np.zeros((180, 320, 3), np.uint8)
            h = int(img.shape[0] * CW / img.shape[1])
            im = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))\
                     .resize((CW, h))
            d = ImageDraw.Draw(im)
            d.rectangle([0, 0, CW - 1, h - 1], outline=(120, 120, 120), width=1)
            line.append((im, tag))
        H = max(im.height for im, _ in line)
        row = Image.new("RGB", (CW * 2 + pad * 3, H + head), (18, 18, 18))
        dr = ImageDraw.Draw(row)
        dr.text((pad, 8), f"{key}  [{kind}]  人占比 新 {new_r}  同帧人数 {n_person}",
                font=font, fill=(235, 235, 235))
        for i, (im, tag) in enumerate(line):
            row.paste(im, (pad + i * (CW + pad), head))
            dr.text((pad + 4 + i * (CW + pad), head + 4), tag, font=font,
                    fill=(255, 210, 80))
        imgs.append(row)
    W = imgs[0].width if imgs else 10
    total = Image.new("RGB", (W, sum(i.height for i in imgs)), (18, 18, 18))
    y = 0
    for i in imgs:
        total.paste(i, (0, y))
        y += i.height
    out = f"对照拼图v2_{video}.png"
    total.save(out)
    log("saved", out, total.size)


def summarize(stats, frag):
    n = len(stats)
    hits = {}
    for s in stats:
        hits[s["chosen_hit"]] = hits.get(s["chosen_hit"], 0) + 1
    changed = [(s["key"], s["v1_hit"], s["chosen_hit"])
               for s in stats if s.get("v1_hit") and s["v1_hit"] != s["chosen_hit"]]
    hw = [s["chosen_box_hw"] for s in stats if s.get("chosen_box_hw")]
    log(f"\n==== v2 汇总（{n} 球）====")
    log(f"归属：{hits}")
    log(f"碎片合并：raw 框 {frag['raw']} → 合并后 {frag['merged']}（并掉 {frag['n_merge']}）")
    log(f"选中框 h/w：min {min(hw) if hw else '-'} 中位 {sorted(hw)[len(hw)//2] if hw else '-'}"
        f"（整身站立人应 ~1.5-4.0）")
    log(f"归属相对 v1 变化的行：{len(changed)}")
    for k, a, b in changed:
        log(f"   {k}: {a} -> {b}")


if __name__ == "__main__":
    sys.exit(main())
