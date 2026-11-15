# -*- coding: utf-8 -*-
"""Step 0+1（R-018 持球盲区补训 Spike）：
0) 按 manifest.jsonl 从源视频重抽 train/val 训练图像（瘦身时已删，labels 现成）；
1) 对 09-12_1 / 09-19_1 的持球段帧（出手前 1.2~0.1s）跑 HSV 预标注，
   命中帧存图+存 YOLO ball 标签到 yolo仓/preannot/held/（canonical labels 不碰）。
可重入：已存在的图像跳过。
"""
import json
import math
from pathlib import Path

import cv2
import numpy as np

YOLO = Path(r"D:/claude code/yolo")
WORK = Path(r"D:/claude code/视频剪辑/work")
SRC = Path(r"D:/claude code/1视频获取/归档")
IMGS = YOLO / "dataset" / "images"
HELD_IMG = YOLO / "preannot" / "held" / "images"
HELD_LAB = YOLO / "preannot" / "held" / "labels"
HELD_FIELDS = {"2026-09-12_1", "2026-09-19_1"}
FPS = 30.0

HELD_IMG.mkdir(parents=True, exist_ok=True)
HELD_LAB.mkdir(parents=True, exist_ok=True)
(IMGS / "train").mkdir(parents=True, exist_ok=True)
(IMGS / "val").mkdir(parents=True, exist_ok=True)


def save_jpg(img, path: Path) -> bool:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if ok:
        buf.tofile(str(path))
    return ok


def hsv_pick(frame, hsv_lo, hsv_hi, hoop_w):
    """HSV 找最大橙球 blob；返回 (cx, cy, r) 或 None。面积门复用 shooter 常量口径。"""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(hsv_lo, np.uint8), np.array(hsv_hi, np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(mask)
    best = None
    for i in range(1, n):
        a = int(stats[i, cv2.CC_STAT_AREA])
        if a < 9 or a > 0.5 * hoop_w * hoop_w:      # MIN_AREA=9, 0.5×筐宽²（shooter 口径）
            continue
        if best is None or a > best[0]:
            best = (a, cent[i])
    if best is None:
        return None
    a, (cx, cy) = best
    r = max(math.sqrt(a / math.pi) * 1.6, 4.0)      # 半遮挡补偿
    return cx, cy, r


def main():
    manifest = [json.loads(l) for l in
                (YOLO / "manifest.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    by_video = {}
    for m in manifest:
        by_video.setdefault(m["video"], {})[m["frame"]] = m["file"]

    # 目标帧集合：canonical labels 现存的 train/val 文件名
    wanted = {"train": {}, "val": {}}
    for split in ("train", "val"):
        for f in (YOLO / "dataset" / "labels" / split).glob("*.txt"):
            vid = f.name.split("__f")[0]
            wanted[split].setdefault(vid, set()).add(f.stem + ".jpg")

    stats = {}
    for vid in sorted(by_video):
        frames = by_video[vid]
        splits = [s for s in ("train", "val") if vid in wanted[s]]
        held = vid in HELD_FIELDS
        if not splits and not held:
            continue
        rels, calib, held_target = [], None, {}
        if held:
            sh = json.loads((WORK / vid / "shooters.json").read_text(encoding="utf-8"))
            rels = sorted({t["release_t"] for t in sh["traces"].values()
                           if t.get("release_t")})
            calib = json.loads((WORK / vid / "calibration.json").read_text(encoding="utf-8"))
            for rel in rels:
                fi0 = int((rel - 1.2) * FPS)
                fi1 = int((rel - 0.1) * FPS)
                for fi in range(fi0, fi1 + 1, 2):
                    held_target[fi] = rel
        # 需要解码的帧号全集（含未存图的 manifest 帧——只为顺序解码流过，不保存）
        need = {}
        for s in splits:
            for fn in wanted[s][vid]:
                need[int(fn.split("__f")[1].split("__")[0])] = (s, fn)
        all_idx = sorted(set(need) | set(held_target))
        if not all_idx:
            continue
        cap = cv2.VideoCapture(str(SRC / f"{vid}.mp4"))
        fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        hr = calib["hoop_rect"] if calib else None
        hoop_w = hr[2] * (fw / calib["frame_size"][0]) if calib else 38.0
        idx_set = set(all_idx)
        maxneed = max(all_idx)
        fi = 0
        st = dict(train=0, val=0, held_scan=0, held_hit=0)
        while fi <= maxneed:
            ok, frame = cap.read()
            if not ok:
                break
            if fi in idx_set:
                if fi in need:
                    s, fn = need[fi]
                    p = IMGS / s / fn
                    if not p.exists():
                        if save_jpg(frame, p):
                            st[s] += 1
                if fi in held_target:
                    st["held_scan"] += 1
                    got = hsv_pick(frame, calib["hsv_low"], calib["hsv_high"], hoop_w)
                    if got:
                        cx, cy, r = got
                        x0, y0 = max(cx - r, 0), max(cy - r, 0)
                        x1, y1 = min(cx + r, fw), min(cy + r, fh)
                        name = f"{vid}__f{fi:06d}"
                        save_jpg(frame, HELD_IMG / f"{name}.jpg")
                        (HELD_LAB / f"{name}.txt").write_text(
                            f"0 {(x0+x1)/2/fw:.6f} {(y0+y1)/2/fh:.6f} "
                            f"{(x1-x0)/fw:.6f} {(y1-y0)/fh:.6f}\n", encoding="utf-8")
                        st["held_hit"] += 1
            fi += 1
        cap.release()
        stats[vid] = st
        print(f"[{vid}] 重抽 train={st['train']} val={st['val']} | "
              f"持球段扫 {st['held_scan']} 帧命中 {st['held_hit']}", flush=True)

    print("\n=== 汇总 ===", flush=True)
    tot_t = sum(s["train"] for s in stats.values())
    tot_v = sum(s["val"] for s in stats.values())
    tot_h = sum(s["held_hit"] for s in stats.values())
    n_lab = len(list(HELD_LAB.glob("*.txt")))
    print(f"train 图像 +{tot_t} / val 图像 +{tot_v} / held 预标注 +{tot_h}（累计 {n_lab}）",
          flush=True)


if __name__ == "__main__":
    main()
