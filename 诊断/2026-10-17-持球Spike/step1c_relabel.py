# -*- coding: utf-8 -*-
"""Step 1c（R-018）：以生产链验证过的 release_xy 为锚，反向连续跟踪重标。
思路：逐帧独立 HSV 会跟到头/皮肤；但「持球」的轨迹必须能连续走回到
生产链验证过的出手点 release_xy（shooters.json）。对每个事件：
  1) 该事件持球窗各帧跑 HSV 多候选（全部 blob，非最大）；
  2) 从出手前最后一帧起，取最接近 release_xy 的 blob（门 25px）；
  3) 逐帧向前（时间倒退）走：下一个 blob 距上一位置 ≤35px（帧距 2/30s）；
  4) 走出的路径帧 = 持球标签；走不到的帧丢弃。
产物：preannot/held2/{images2,labels2}（复制，不动旧目录）；训练 yaml 指向 held2。
零删除/零移动设计：不 unlink 不 move（环境安全删除守卫会拦截批量删除）。"""
import json
import math
import shutil
from pathlib import Path

import cv2
import numpy as np

REPO = Path(r"D:/claude code/yolo")
WORK = Path(r"D:/claude code/视频剪辑/work")
HELD = REPO / "preannot" / "held"
OUT = REPO / "preannot" / "held2"      # 新一轮产物（独立目录，不碰旧 held/）
IMG2 = OUT / "images2"
LAB2 = OUT / "labels2"
# 帧池：三个来源目录都算（step15b 的 kept/rejected + 上一轮 discarded）
SRCS = [HELD / "images", HELD / "rejected", HELD / "discarded"]
FIELDS = ["2026-09-12_1", "2026-09-19_1"]
FPS = 30.0
START_GATE = 25.0     # 出手点锚定门（px）
STEP_GATE = 35.0      # 反向跟踪连续性门（px/2帧）
MIN_WALK = 2          # 路径至少 2 帧才算


def parse_key(k):
    mm, ss = k.split(":")
    return int(mm) * 60 + float(ss)


def blobs(frame, calib, hoop_w):
    """返回帧内全部合格 blob [(cx, cy, area)]（面积门同 shooter）。"""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(calib["hsv_low"], np.uint8),
                       np.array(calib["hsv_high"], np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(mask)
    out = []
    for i in range(1, n):
        a = int(stats[i, cv2.CC_STAT_AREA])
        if 9 <= a <= 0.5 * hoop_w * hoop_w:
            out.append((cent[i][0], cent[i][1], a))
    return out


def main():
    IMG2.mkdir(parents=True, exist_ok=True)
    LAB2.mkdir(parents=True, exist_ok=True)
    total_lab = 0
    for field in FIELDS:
        calib = json.loads((WORK / field / "calibration.json").read_text(encoding="utf-8"))
        sh = json.loads((WORK / field / "shooters.json").read_text(encoding="utf-8"))
        cap = cv2.VideoCapture(str(WORK / field / ".." / ".." / "1视频获取" / "归档" / f"{field}.mp4"))
        cap.release()  # 只为确认视频存在；本脚本不解码视频
        fw, fh = calib["frame_size"]
        hoop_w = calib["hoop_rect"][2]
        # 事件 -> 持球窗帧
        # 帧池：name -> 实际路径（三来源合并，后者优先级高）
        pool = {}
        for src in SRCS:
            for p in src.glob(f"{field}__f*.jpg"):
                pool[p.stem] = p
        ev = []
        for k, t in sh["traces"].items():
            rt = t.get("release_t")
            rxy = t.get("release_xy")
            if not rt or not rxy:
                continue
            fis = [fi for fi in range(int((rt - 1.2) * FPS), int((rt - 0.1) * FPS) + 1, 2)
                   if f"{field}__f{fi:06d}" in pool]
            if fis:
                ev.append((parse_key(k), rt, rxy, sorted(fis, reverse=True)))
        kept_ev = 0
        n_anchored = 0
        for e_t, rt, rxy, fis in ev:
            path_fis = []
            prev = None
            for fi in fis:
                pimg = pool[f"{field}__f{fi:06d}"]
                img = cv2.imdecode(np.fromfile(str(pimg), np.uint8), cv2.IMREAD_COLOR)
                if img is None:
                    continue
                bs = blobs(img, calib, hoop_w)
                if not bs:
                    if prev is None:
                        continue      # 锚定帧还没找到 blob，继续等
                    break             # 路径断了，停
                if prev is None:
                    # 锚定：最接近 release_xy 的 blob
                    d, b = min((math.hypot(x - rxy[0], y - rxy[1]), (x, y, a))
                               for x, y, a in bs)
                    if d > START_GATE:
                        continue
                    prev = b
                    n_anchored += 1
                else:
                    d, b = min((math.hypot(x - prev[0], y - prev[1]), (x, y, a))
                               for x, y, a in bs)
                    if d > STEP_GATE:
                        break
                    prev = b
                path_fis.append((fi, b))
            if len(path_fis) < MIN_WALK:
                continue
            kept_ev += 1
            for fi, (x, y, a) in path_fis:
                r = max((a / math.pi) ** 0.5 * 1.6, 4.0)
                x0, y0 = max(x - r, 0), max(y - r, 0)
                x1, y1 = min(x + r, fw), min(y + r, fh)
                name = f"{field}__f{fi:06d}"
                src = pool[name]
                dst = IMG2 / (name + src.suffix)
                if not dst.exists():
                    shutil.copyfile(str(src), str(dst))
                (LAB2 / f"{name}.txt").write_text(
                    f"0 {(x0+x1)/2/fw:.6f} {(y0+y1)/2/fh:.6f} "
                    f"{(x1-x0)/fw:.6f} {(y1-y0)/fh:.6f}\n", encoding="utf-8")
                total_lab += 1
        print(f"[{field}] 锚定成功 {n_anchored}/{len(ev)}，走通 {kept_ev}", flush=True)
        n_lab = sum(1 for _ in LAB2.glob(f"{field}__*.txt"))
        n_img = sum(1 for _ in IMG2.glob(f"{field}__*"))
        print(f"[{field}] held2 路径帧：图 {n_img} / 标签 {n_lab}", flush=True)
    print(f"\n=== 反向跟踪标注完成：held2 标签共 {total_lab} 帧 ===", flush=True)


if __name__ == "__main__":
    main()
