# -*- coding: utf-8 -*-
"""补段边界实验：验证"只标一次 + 自动跟筐"能否替代人工补段。

对每个段边界 T，抽 T-90/-60/-30/-10 与 T+10/+30/+60/+90 帧：
  绿框 = YOLO 检出（真实筐位）
  红框 = 旧段预测（prev_rect + prev_shift，越界后冻结末值）
          —— 这就是"不补段时判定器以为筐在哪"
  蓝框 = 新段预测（new_rect + new_shift）
"""
import bisect
import json
import os

import cv2
import numpy as np

PROJ = r"D:/claude code/视频剪辑YOLO"
WORK = os.path.join(PROJ, "work")
OUT = os.path.join(PROJ, "诊断", "2026-11-07-筐识别")
OFFS = [-90, -60, -30, -10, 10, 30, 60, 90]

PAD_X, PAD_Y, ZOOM = 130, 90, 2.2
CELL_W = int(PAD_X * 2 * ZOOM)
CELL_H = int(PAD_Y * 2 * ZOOM) + 22


def wr(p, img):
    ok, b = cv2.imencode(".png", img)
    b.tofile(p)


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


def load(name):
    base = os.path.join(WORK, name)
    if not os.path.isdir(base):
        base = os.path.join(r"D:/claude code/视频剪辑/work", name)
    fs = ["calibration.json"] + sorted(
        f for f in os.listdir(base)
        if f.startswith("calibration_seg") and f.endswith(".json"))
    cals = sorted([json.load(open(os.path.join(base, f), encoding="utf-8"))
                   for f in fs], key=lambda c: float(c.get("calib_t") or 0))
    pos = json.load(open(os.path.join(base, "positions.json"), encoding="utf-8"))
    segs = []
    for i, c in enumerate(cals):
        t0 = float(c.get("calib_t") or 0)
        t1 = float(cals[i + 1]["calib_t"]) if i + 1 < len(cals) else None
        sh = next((s.get("shift") or [] for s in pos["segments"]
                   if abs(float(s.get("t0") or 0) - t0) < 0.5), [])
        segs.append({"t0": t0, "t1": t1,
                     "rect": [float(v) for v in c["hoop_rect"]], "shift": sh})
    return segs, pos.get("video")


def run(name, model):
    segs, video = load(name)
    if not video or not os.path.exists(video):
        print(f"=== {name} 源视频不存在，跳过")
        return []
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"\n=== {name}  {total/fps/60:.1f}min  段={len(segs)} ===")
    cells, recs = [], []
    for bi in range(1, len(segs)):
        T = segs[bi]["t0"]
        prev, cur = segs[bi - 1], segs[bi]
        hw, hh = prev["rect"][2], prev["rect"][3]
        print(f"  边界 {T/60:6.1f}min  旧段 T0={prev['t0']/60:.1f}  新段 T0={cur['t0']/60:.1f}"
              f"  筐位变化 dx={cur['rect'][0]-prev['rect'][0]:+.0f}"
              f" dy={cur['rect'][1]-prev['rect'][1]:+.0f}px")
        for dt in OFFS:
            t = T + dt
            if not (prev["t0"] <= t < (cur["t1"] or 1e9)):
                continue
            fi = max(0, min(int(round(t * fps)), total - 1))
            cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
            frame = cap.retrieve()[1] if cap.grab() else None
            if frame is None:
                continue
            res = model.predict(frame, device=0, quantize=16, imgsz=1024, conf=0.05,
                                iou=0.7, classes=[1], verbose=False)[0]
            b = res.boxes
            det = None
            if b is not None and len(b) > 0:
                cf = b.conf.cpu().numpy()
                i = int(np.argmax(cf))
                x1, y1, x2, y2 = b.xyxy.cpu().numpy()[i]
                det = ((x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1, float(cf[i]))
            # 旧段预测（超出该段末端后冻结末值）
            sdx, sdy = shift_at(prev["shift"], min(t, T - 0.1))
            px, py = prev["rect"][0] + sdx, prev["rect"][1] + sdy
            sdx2, sdy2 = shift_at(cur["shift"], t) if t >= cur["t0"] else (0.0, 0.0)
            nx, ny = cur["rect"][0] + sdx2, cur["rect"][1] + sdy2
            rec = {"file": name, "boundary": T, "dt": dt, "t": t,
                   "det": det, "pred_old": [px, py, hw, hh],
                   "pred_new": [nx, ny, cur["rect"][2], cur["rect"][3]]}
            if det is not None:
                rec["err_old"] = [det[0] - (px + hw / 2), det[1] - (py + hh / 2)]
                rec["err_new"] = [det[0] - (nx + cur["rect"][2] / 2),
                                  det[1] - (ny + cur["rect"][3] / 2)]
            recs.append(rec)
            if det:
                print(f"     dt={dt:+4d}s  conf={det[4]:.2f}  "
                      f"旧段误差 {rec['err_old'][0]:+7.1f},{rec['err_old'][1]:+6.1f}px"
                      f"（{abs(rec['err_old'][0])/hw:.2f}W）  "
                      f"新段误差 {rec['err_new'][0]:+6.1f},{rec['err_new'][1]:+6.1f}px")
            else:
                print(f"     dt={dt:+4d}s  NO DET")

            # 画：以检出（或旧段预测）为中心裁剪
            ax, ay = (det[0], det[1]) if det else (px + hw / 2, py + hh / 2)
            x0 = max(0, min(int(ax - PAD_X), frame.shape[1] - PAD_X * 2))
            y0 = max(0, min(int(ay - PAD_Y), frame.shape[0] - PAD_Y * 2))
            crop = frame[y0:y0 + PAD_Y * 2, x0:x0 + PAD_X * 2]
            big = cv2.resize(crop, (CELL_W, int(PAD_Y * 2 * ZOOM)),
                             interpolation=cv2.INTER_CUBIC)

            def m(x, y):
                return (int(round((x - x0) * ZOOM)), int(round((y - y0) * ZOOM)))

            cv2.rectangle(big, m(px, py), m(px + hw, py + hh), (50, 50, 235), 1)
            cv2.rectangle(big, m(nx, ny), m(nx + cur["rect"][2], ny + cur["rect"][3]),
                          (235, 120, 50), 1)
            if det:
                cv2.rectangle(big, m(det[0] - det[2] / 2, det[1] - det[3] / 2),
                              m(det[0] + det[2] / 2, det[1] + det[3] / 2),
                              (80, 235, 90), 2)
            canvas = np.full((CELL_H, CELL_W, 3), 18, np.uint8)
            canvas[22:22 + big.shape[0], :] = big
            cv2.putText(canvas, f"B{bi} {dt:+d}s  段{bi}->{bi+1}", (5, 14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.42, (235, 235, 235), 1)
            if det:
                cv2.putText(canvas,
                            f"old {rec['err_old'][0]:+.0f}px  new {rec['err_new'][0]:+.0f}px",
                            (5, CELL_H - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                            (150, 200, 255), 1)
            else:
                cv2.putText(canvas, "NO DET", (5, CELL_H - 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.4, (80, 80, 235), 1)
            cells.append(canvas)
    cap.release()
    if cells:
        ncol = 4
        rows = (len(cells) + ncol - 1) // ncol
        sheet = np.full((CELL_H * rows, CELL_W * ncol, 3), 18, np.uint8)
        for i, c in enumerate(cells):
            r, cc = divmod(i, ncol)
            sheet[r * CELL_H:(r + 1) * CELL_H, cc * CELL_W:(cc + 1) * CELL_W] = c
        p = os.path.join(OUT, f"boundary_{name}.png")
        wr(p, sheet)
        print(f"  → {p}")
    return recs


if __name__ == "__main__":
    from ultralytics import YOLO
    model = YOLO(os.path.join(PROJ, "bbh", "weights", "best.pt"))
    names = ["2026-10-17_1", "2026-10-23_1", "2026-10-31_1",
             "2026-10-30_1", "2026-10-10_1"]
    allr = []
    for n in names:
        allr += run(n, model)
    with open(os.path.join(OUT, "boundary_result.json"), "w", encoding="utf-8") as f:
        json.dump(allr, f, ensure_ascii=False, indent=1, default=float)
