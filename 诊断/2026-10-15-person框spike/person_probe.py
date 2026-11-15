# -*- coding: utf-8 -*-
"""Spike 探针（throwaway）：person 框能否改善出手人缩略图的「认人」。

问题：远景 720p 下，COCO 预训 person 检测（yolov8n）能否
  ① 把出手点正确归属到出手人的框；
  ② 按框裁剪比现有「广角半场裁剪」更容易认人。

做法：08-28/09-04 各 20 球（正常 16 + 难例 4），出手时刻前后取 3 帧
跑 person 检测；归属规则：点在框内 > 扩 30% 框含点 > 框底中心最近。
对照拼图每球一行：旧裁剪（框上也跑检测量「人占画面比」）vs person 框裁剪。

用法（cwd = 本目录，模型权重落在本目录）：
  D:/claude\ code/视频剪辑YOLO/.venv/Scripts/python.exe -I -X utf8 person_probe.py
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
FRAME_OFFSETS = (-0.6, -0.3, 0.0)   # 出手时刻前后取 3 帧
EXPAND = 0.30                        # 归属规则的扩框比例
CROP_PAD = 0.45                      # 新裁剪：person 框四边外扩比例（认人要带手臂/姿势）
CONF = 0.25                          # person 置信度门（COCO 默认）
IMGSZ = 1280                         # 远景小人，用大输入保召回

random.seed(42)


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- 模型
def load_model():
    from ultralytics import YOLO
    return YOLO("yolov8n.pt")   # cwd 下没有则自动下载（~6MB）


def persons_of(model, frame_bgr):
    """frame → [(x1,y1,x2,y2,conf)]，person 类（COCO 0）。"""
    r = model(frame_bgr, classes=[0], conf=CONF, imgsz=IMGSZ, verbose=False)[0]
    out = []
    for b in r.boxes:
        x1, y1, x2, y2 = (float(v) for v in b.xyxy[0])
        out.append((x1, y1, x2, y2, float(b.conf[0])))
    return out


# ---------------------------------------------------------------- 归属
def expand_box(b, k):
    x1, y1, x2, y2, c = b
    w, h = x2 - x1, y2 - y1
    return (x1 - w * k, y1 - h * k, x2 + w * k, y2 + h * k)


def attribute(px, py, boxes):
    """出手点 → 框。返回 (hit_type, box, dist)：in > expand > nearest > miss。"""
    if not boxes:
        return "miss", None, None
    hits = [b for b in boxes if b[0] <= px <= b[2] and b[1] <= py <= b[3]]
    if hits:
        return "in", max(hits, key=lambda b: b[4]), 0.0
    ex = [b for b in boxes
          if (e := expand_box(b, EXPAND)) and e[0] <= px <= e[2] and e[1] <= py <= e[3]]
    if ex:
        b = min(ex, key=lambda b: (px - (b[0] + b[2]) / 2) ** 2 + (py - (b[2] and (b[1] + b[3]) / 2)) ** 2)
        return "expand", b, 0.0
    def dist(b):
        cx, cy = (b[0] + b[2]) / 2, b[3]          # 框底中心（脚的位置）
        return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5
    b = min(boxes, key=dist)
    return "nearest", b, round(dist(b), 1)


# ---------------------------------------------------------------- 抽样
def sample(video):
    doc = json.load(open(os.path.join(ORIG_WORK, video, "shooters.json"),
                         encoding="utf-8"))
    tr = doc["traces"]
    def has_xy(v): return v.get("release_xy") and v.get("release_t") is not None
    normal = {k: v for k, v in tr.items()
              if has_xy(v) and v.get("status") == "high"
              and v.get("spot", "ok") == "ok" and not v.get("extrap")}
    hard = {k: v for k, v in tr.items()
            if has_xy(v) and (v.get("spot") == "bad" or v.get("extrap")
                              or v.get("status") == "low")}
    kn = sorted(normal)
    kh = sorted(hard)
    random.shuffle(kn)
    random.shuffle(kh)
    pick = [(k, "normal") for k in kn[:N_NORMAL]] + [(k, "hard") for k in kh[:N_HARD]]
    log(f"{video}: traces={len(tr)} normal_pool={len(normal)} hard_pool={len(hard)} "
        f"-> pick {len(pick)}")
    return pick, tr


# ---------------------------------------------------------------- 帧读取
def frame_at(cap, t_sec):
    cap.set(cv2.CAP_PROP_POS_MSEC, t_sec * 1000.0)
    ok, fr = cap.read()
    return fr if ok else None


def crop_person_frame(frame, box, px, py):
    """person 框外扩裁剪（原始帧坐标），带边界钳制；返回裁剪图 + 十字相对坐标。"""
    H, W = frame.shape[:2]
    x1, y1, x2, y2 = box[:4]
    w, h = x2 - x1, y2 - y1
    cx1, cy1 = x1 - w * CROP_PAD, y1 - h * CROP_PAD
    cx2, cy2 = x2 + w * CROP_PAD, y2 + h * CROP_PAD
    cx1, cy1 = max(0, cx1), max(0, cy1)
    cx2, cy2 = min(W, cx2), min(H, cy2)
    crop = frame[int(cy1):int(cy2), int(cx1):int(cx2)].copy()
    return crop, (px - cx1, py - cy1)


def draw_marker(img_bgr, xy, color=(0, 215, 255), thick=2):
    """黄十字标出手点（像素坐标为图像坐标，出界则钳制）。"""
    H, W = img_bgr.shape[:2]
    x, y = int(xy[0]), int(xy[1])
    x, y = max(0, min(W - 1, x)), max(0, min(H - 1, y))
    cv2.drawMarker(img_bgr, (x, y), color, cv2.MARKER_CROSS, 22, thick)
    return img_bgr


# ---------------------------------------------------------------- 主流程
def main():
    model = load_model()
    log("model ready:", model.model_name if hasattr(model, "model_name") else "yolov8n")
    stats = []
    for video in VIDEOS:
        pick, tr = sample(video)
        cap = cv2.VideoCapture(os.path.join(VIDEO_DIR, video + ".mp4"))
        rows = []
        for key, kind in pick:
            v = tr[key]
            rt, (px, py) = v["release_t"], v["release_xy"]
            frames = []
            best = None   # (优先级, t, hit, box, frame)
            for off in FRAME_OFFSETS:
                fr = frame_at(cap, rt + off)
                if fr is None:
                    continue
                boxes = persons_of(model, fr)
                hit, box, dist = attribute(px, py, boxes)
                frames.append({"off": off, "n_person": len(boxes),
                               "hit": hit, "dist": dist})
                prio = {"in": 3, "expand": 2, "nearest": 1, "miss": 0}[hit]
                if best is None or prio > best[0]:
                    best = (prio, rt + off, hit, box, fr, len(boxes))
            if best is None:
                stats.append({"video": video, "key": key, "kind": kind,
                              "error": "no frame"})
                continue
            _, t_use, hit, box, fr, n_person = best
            # 旧裁剪：thumbs 第一张；上面也跑检测量「人占画面比」
            old_ratio = None
            old_img = None
            th = (v.get("thumbs") or [None])[0]
            if th:
                p = os.path.join(ORIG_WORK, video, th)
                if os.path.exists(p):
                    old_img = cv2.imread(p)
                    ob = persons_of(model, old_img)
                    if ob:
                        b = max(ob, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
                        oh, ow = old_img.shape[:2]
                        old_ratio = round((b[2] - b[0]) * (b[3] - b[1]) / (ow * oh), 3)
            # 新裁剪
            if hit in ("in", "expand", "nearest"):
                crop, rel = crop_person_frame(fr, box, px, py)
                crop = draw_marker(crop, rel)
                ch, cw = crop.shape[:2]
                new_ratio = round((box[2] - box[0]) * (box[3] - box[1]) / (cw * ch), 3)
            else:
                crop = draw_marker(fr.copy(), (px, py))
                new_ratio = 0.0
            rows.append((key, kind, hit, old_img, old_ratio, crop, new_ratio,
                         n_person, v.get("spot"), v.get("status")))
            stats.append({
                "video": video, "key": key, "kind": kind, "status": v.get("status"),
                "spot": v.get("spot"), "release_t": round(rt, 2),
                "frames": frames, "chosen_hit": hit,
                "n_person": n_person,
                "old_person_area": old_ratio, "new_person_area": new_ratio,
            })
        cap.release()
        make_montage(video, rows)
    json.dump(stats, open("probe_stats.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    summarize(stats)


def make_montage(video, rows):
    """每球一行：旧裁剪 | person 框裁剪（各 360 宽），头部标签行。"""
    CW = 360
    pad, head = 6, 34
    try:
        font = ImageFont.truetype("msyh.ttc", 15)
    except OSError:
        font = ImageFont.load_default()
    imgs = []
    for key, kind, hit, old, old_r, crop, new_r, n_person, spot, status in rows:
        line = []
        for img, tag in ((old, f"旧 {status}/{spot or '-'}"), (crop, f"新 {hit}")):
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
        dr.text((pad, 8), f"{key}  [{kind}]  人占比 旧 {old_r if old_r is not None else '?'}"
                          f" → 新 {new_r}  同帧人数 {n_person}", font=font, fill=(235, 235, 235))
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
    out = f"对照拼图_{video}.png"
    total.save(out)
    log("saved", out, total.size)


def summarize(stats):
    n = len(stats)
    hits = {}
    for s in stats:
        hits[s["chosen_hit"]] = hits.get(s["chosen_hit"], 0) + 1
    multi = sum(1 for s in stats if s.get("n_person", 0) > 1)
    olds = [s["old_person_area"] for s in stats if s.get("old_person_area")]
    news = [s["new_person_area"] for s in stats if s.get("new_person_area") is not None]
    def med(x):
        x = sorted(x)
        return x[len(x) // 2] if x else None
    log(f"\n==== 汇总（{n} 球）====")
    log(f"归属：{hits}   同帧多人行：{multi}/{n}")
    if olds and news:
        log(f"人占画面比 中位：旧 {med(olds)} → 新 {med(news)}"
            f"（约 {med(news) / max(med(olds), 1e-6):.1f} 倍）")


if __name__ == "__main__":
    sys.exit(main())
