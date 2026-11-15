# -*- coding: utf-8 -*-
"""多段标定场次：YOLO 筐检出 vs 该时刻生效的标定框（含 shift 补偿）。

只读。产物只落本目录。
口径：
  原始像素 = 标定坐标 + shift(t)   （见 bbh/detector.py:144,167）
  红框 = 该段标定框 + shift(t)  → 模型此刻应当看到的位置
  绿框 = YOLO class-1 检出
  黄框 = 该段标定框（未补偿，用来显示"如果直接拿标定去比"会差多少）
"""
import bisect
import json
import os
import sys

import cv2
import numpy as np

PROJ = r"D:/claude code/视频剪辑YOLO"
WORKROOT = os.path.join(PROJ, "work")
ARCH = r"D:/claude code/1视频获取/归档"
OUT = os.path.join(PROJ, "诊断", "2026-11-07-筐识别")

PER_SEG = 6            # 每段采样点
PAD_X, PAD_Y, ZOOM = 150, 100, 1.4
COLS = 3
CELL_W, CELL_H = int(PAD_X * 2 * ZOOM), int(PAD_Y * 2 * ZOOM)


def imwrite(p, img):
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise OSError(p)
    buf.tofile(p)


def load_segments(name):
    """→ [{t0, t1, rect, shift:[[t,dx,dy],...]}]，按 t0 升序。"""
    d = os.path.join(WORKROOT, name)
    cals = []
    for fn in ["calibration.json"] + sorted(
            [f for f in os.listdir(d)
             if f.startswith("calibration_seg") and f.endswith(".json")]):
        c = json.load(open(os.path.join(d, fn), encoding="utf-8"))
        c["_file"] = fn
        cals.append(c)
    cals.sort(key=lambda c: float(c.get("calib_t") or 0.0))

    pos = json.load(open(os.path.join(d, "positions.json"), encoding="utf-8"))
    segs_pos = pos.get("segments") or []
    out = []
    for i, c in enumerate(cals):
        t0 = float(c.get("calib_t") or 0.0)
        t1 = float(cals[i + 1].get("calib_t")) if i + 1 < len(cals) else None
        # 按 t0 找对应的 positions 段（shift 表在段里，不在顶层）
        sh = []
        for s in segs_pos:
            if abs(float(s.get("t0") or 0.0) - t0) < 0.5:
                sh = s.get("shift") or []
                break
        out.append({"t0": t0, "t1": t1,
                    "rect": [float(v) for v in c["hoop_rect"]],
                    "shift": sh, "file": c["_file"]})
    return out, pos


def shift_at(sh, t):
    if not sh:
        return 0.0, 0.0
    ts = [x[0] for x in sh]
    i = bisect.bisect_left(ts, t)
    if i <= 0:
        return float(sh[0][1]), float(sh[0][2])
    if i >= len(sh):
        return float(sh[-1][1]), float(sh[-1][2])
    a, b = sh[i - 1], sh[i]
    dt = (b[0] - a[0]) or 1.0
    k = (t - a[0]) / dt
    return a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k


def pick(model, frame):
    kw = dict(imgsz=1024, conf=0.05, iou=0.7, classes=[1], verbose=False)
    try:
        res = model.predict(frame, device=0, quantize=16, **kw)[0]
    except Exception:
        res = model.predict(frame, device="cpu", **kw)[0]
    b = res.boxes
    if b is None or len(b) == 0:
        return None
    cf = b.conf.cpu().numpy()
    i = int(np.argmax(cf))
    x1, y1, x2, y2 = b.xyxy.cpu().numpy()[i]
    return float(x1), float(y1), float(x2), float(y2), float(cf[i])


def run(name, model):
    segs, pos = load_segments(name)
    video = pos.get("video") or os.path.join(ARCH, name + ".mp4")
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    dur = total / fps
    print(f"\n=== {name}  {dur/60:.1f}min  fps={fps:.2f}  段={len(segs)} ===")

    cells, stats = [], []
    for si, sg in enumerate(segs, 1):
        t0 = sg["t0"]
        t1 = sg["t1"] if sg["t1"] is not None else dur
        hx, hy, hw, hh = sg["rect"]
        cx, cy = hx + hw / 2.0, hy + hh / 2.0
        span = max(1.0, t1 - t0)
        ts = [t0 + span * (k + 0.5) / PER_SEG for k in range(PER_SEG)]
        for t in ts:
            fi = int(round(t * fps))
            if fi >= total:
                fi = total - 1
            cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
            ok = cap.grab()
            frame = cap.retrieve()[1] if ok else None
            if frame is None:
                continue
            d = pick(model, frame)
            sdx, sdy = shift_at(sg["shift"], t)
            # 补偿后应当看到的位置
            ex, ey = cx + sdx, cy + sdy
            rec = {"seg": si, "t": t, "shift_dx": sdx, "shift_dy": sdy,
                   "conf": None, "dx": None, "dy": None,
                   "dx_raw": None, "dy_raw": None, "w": None, "h": None}
            if d is not None:
                x1, y1, x2, y2, cf = d
                mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                rec.update(conf=cf, dx=mx - ex, dy=my - ey,
                           dx_raw=mx - cx, dy_raw=my - cy,
                           w=x2 - x1, h=y2 - y1)
            stats.append(rec)

            # 裁剪窗锚在补偿后标定筐心
            x0 = int(round(ex - PAD_X)); y0 = int(round(ey - PAD_Y))
            x0 = max(0, min(x0, frame.shape[1] - PAD_X * 2))
            y0 = max(0, min(y0, frame.shape[0] - PAD_Y * 2))
            crop = frame[y0:y0 + PAD_Y * 2, x0:x0 + PAD_X * 2]
            big = cv2.resize(crop, (CELL_W, CELL_H), interpolation=cv2.INTER_CUBIC)

            def m(x, y):
                return (int(round((x - x0) * ZOOM)), int(round((y - y0) * ZOOM)))

            # 黄：未补偿标定（会随镜头移动漂离画面）
            cv2.rectangle(big, m(hx, hy), m(hx + hw, hy + hh), (60, 200, 235), 1)
            # 红：补偿后应有位置
            cv2.rectangle(big, m(ex - hw / 2, ey - hh / 2),
                          m(ex + hw / 2, ey + hh / 2), (60, 60, 230), 1)
            if d is not None:
                x1, y1, x2, y2, cf = d
                cv2.rectangle(big, m(x1, y1), m(x2, y2), (80, 220, 90), 1)
                tag = f"SEG{si} t={t/60:.1f}m conf={cf:.2f}"
                sub = (f"补偿后 dx={rec['dx']:+.1f} dy={rec['dy']:+.1f}px"
                       f" ({abs(rec['dx'])/hw:.2f}W)  未补偿{rec['dx_raw']:+.0f}")
            else:
                tag = f"SEG{si} t={t/60:.1f}m  NO HOOP"
                sub = f"shift=({sdx:+.0f},{sdy:+.0f})"
            cv2.rectangle(big, (0, 0), (CELL_W, 34), (28, 28, 28), -1)
            cv2.putText(big, tag, (6, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                        (235, 235, 235), 1)
            cv2.putText(big, sub, (6, 29), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                        (150, 200, 255), 1)
            cells.append(big)

    cap.release()
    rows = (len(cells) + COLS - 1) // COLS
    sheet = np.full((CELL_H * rows, CELL_W * COLS, 3), 22, np.uint8)
    for i, cell in enumerate(cells):
        r, c = divmod(i, COLS)
        sheet[r * CELL_H:(r + 1) * CELL_H, c * CELL_W:(c + 1) * CELL_W] = cell
    path = os.path.join(OUT, f"hoop_multi_{name}.png")
    imwrite(path, sheet)

    hit = [r for r in stats if r["conf"] is not None]
    print(f"  检出 {len(hit)}/{len(stats)}")
    for si, sg in enumerate(segs, 1):
        hx, hy, hw, hh = sg["rect"]
        ss = [r for r in hit if r["seg"] == si]
        raw = [abs(r["dx_raw"]) for r in ss]
        cmp_ = [abs(r["dx"]) for r in ss]
        cmy = [abs(r["dy"]) for r in ss]
        if not ss:
            print(f"  SEG{si} t0={sg['t0']/60:6.1f}m 筐{hw:.0f}x{hh:.0f}  无检出")
            continue
        print(f"  SEG{si} t0={sg['t0']/60:6.1f}m 筐{hw:.0f}x{hh:.0f} "
              f"检出{len(ss)}/{PER_SEG}  "
              f"补偿后|dx| 中位{np.median(cmp_):.1f} 最大{max(cmp_):.1f}px "
              f"({np.median(cmp_)/hw:.2f}W/{max(cmp_)/hw:.2f}W)  "
              f"|dy| 中位{np.median(cmy):.1f}px  "
              f"未补偿|dx| 中位{np.median(raw):.1f} 最大{max(raw):.1f}px  "
              f"conf 中位{np.median([r['conf'] for r in ss]):.2f}")
    print(f"  → {path}")
    return stats


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    from ultralytics import YOLO
    model = YOLO(os.path.join(PROJ, "bbh", "weights", "best.pt"))
    allst = {}
    for nm in (sys.argv[1:] or ["2026-10-17_1", "2026-10-23_1"]):
        allst[nm] = run(nm, model)
    with open(os.path.join(OUT, "hoop_multi_result.json"), "w",
              encoding="utf-8") as f:
        json.dump(allst, f, ensure_ascii=False, indent=1)
