# -*- coding: utf-8 -*-
"""验证 B：全片密集采样的 hoop 检出率 / 误检率 / 与 ORB 位移的一致性。

对每一场：
  1. 按 calib_t 排好标定段；每 SAMPLE 秒取一帧，跑 YOLO 取 class=1（筐）
  2. 与「该段 calib + 该刻 shift」的预测位置比 → 残差（px 与 筐宽归一）
  3. 与「该段 calib 原位置」比 → 不校正时的偏差（= 若只标一次会偏多少）
  4. 误检门：中心偏离预测 >1.0W、尺寸比 0.7~1.4 之外、conf 过低 → 计入可疑
输出：逐场统计 + Δ_hoop 时间序列 + 可疑帧清单（供人工看图）
"""
import json
import os
import sys

os.environ.setdefault("YOLO_VERBOSE", "0")
import cv2
import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

WORK = r"D:/claude code/视频剪辑YOLO/work"
UP = r"D:/claude code/视频剪辑/work"
SAMPLE = float(os.environ.get("HOOP_SAMPLE", 20))
T0 = float(os.environ.get("HOOP_T0", 0))      # 秒；只扫 [T0, T1]
T1 = float(os.environ.get("HOOP_T1", 0))      # 0 = 到片尾
CONF = float(os.environ.get("HOOP_CONF", 0.05))
SNAP = os.environ.get("YOLO_BBH_SNAP", "v20")


def calib_segs(name):
    base = os.path.join(WORK, name)
    if not os.path.isdir(base):
        base = os.path.join(UP, name)
    fs = ["calibration.json"] + sorted(
        f for f in os.listdir(base)
        if f.startswith("calibration_seg") and f.endswith(".json"))
    cs = []
    for f in fs:
        c = json.load(open(os.path.join(base, f), encoding="utf-8"))
        cs.append(c)
    cs.sort(key=lambda c: float(c.get("calib_t") or 0))
    return base, cs


def shift_at(doc, t):
    """(dx, dy)：该时刻 ORB 位移（多段按段取，扁平取顶层）。"""
    segs = doc.get("segments")
    sh = None
    if segs is not None:
        for s in segs:
            t0 = float(s.get("t0") or 0)
            t1 = s.get("t1")
            if t >= t0 - 1e-6 and (t1 is None or t < float(t1)):
                sh = s.get("shift") or []
                break
        if sh is None:
            sh = []
    else:
        sh = doc.get("shift") or []
    if not sh:
        return 0.0, 0.0, 0
    a = np.array(sh, float)
    i = int(np.searchsorted(a[:, 0], t))
    if i <= 0:
        return float(a[0, 1]), float(a[0, 2]), len(sh)
    if i >= len(a):
        return float(a[-1, 1]), float(a[-1, 2]), len(sh)
    p, q = a[i - 1], a[i]
    dt = q[0] - p[0]
    k = 0.0 if dt <= 0 else (t - p[0]) / dt
    return float(p[1] + (q[1] - p[1]) * k), float(p[2] + (q[2] - p[2]) * k), len(sh)


def pick_hoop(res, cal_rect):
    """从 boxes 里挑最像筐的一个：class==1 且尺寸最接近 calib。"""
    boxes = getattr(res, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return None, 0
    xyxy = boxes.xyxy.cpu().numpy()
    conf = boxes.conf.cpu().numpy()
    cls = boxes.cls.cpu().numpy().astype(int)
    cand = []
    for i in range(len(xyxy)):
        if cls[i] != 1:
            continue
        b = xyxy[i]
        w = b[2] - b[0]
        h = b[3] - b[1]
        if w <= 0 or h <= 0:
            continue
        rw = w / max(cal_rect[2], 1)
        rh = h / max(cal_rect[3], 1)
        pen = abs(np.log(rw)) + abs(np.log(rh))
        cand.append((pen, float(conf[i]), b))
    if not cand:
        return None, 0
    cand.sort(key=lambda c: c[0])
    b = cand[0][2]
    return (float((b[0] + b[2]) / 2), float((b[1] + b[3]) / 2),
            float(b[2] - b[0]), float(b[3] - b[1]), cand[0][1]), len(cand)


def run(name):
    base, cals = calib_segs(name)
    if not cals:
        print(f"{name}: 无标定"); return None
    doc = None
    for p in (os.path.join(base, "positions.json"),
              os.path.join(UP, name, "positions.json")):
        if os.path.exists(p):
            doc = json.load(open(p, encoding="utf-8"))
            break
    if doc is None:
        print(f"{name}: 无 positions"); return None
    v = doc.get("video")
    if not v or not os.path.exists(v):
        print(f"{name}: 视频缺失"); return None
    fps = (doc.get("meta") or {}).get("fps") or 30.0
    dur = (doc.get("meta") or {}).get("duration") or 0

    from bbh.detector_yolo import _model, CFG
    model = _model()
    pk = {"imgsz": CFG["imgsz"], "conf": CONF, "iou": 0.7,
          "classes": [0, 1], "verbose": False, "device": CFG["device"],
          "quantize": CFG["quantize"]}

    cap = cv2.VideoCapture(v)
    rows = []
    t_start = T0 if T0 > 0 else 0.0
    t_end = T1 if T1 > 0 else dur
    ts = np.arange(t_start, min(t_end, dur), SAMPLE)
    for k, t in enumerate(ts):
        fi = int(round(t * fps))
        cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
        ok, fr = cap.read()
        if not ok:
            continue
        ci = 0
        for i, c in enumerate(cals):
            if t >= float(c.get("calib_t") or 0):
                ci = i
        cal = cals[ci]["hoop_rect"]
        cx, cy = cal[0] + cal[2] / 2, cal[1] + cal[3] / 2
        res = model.predict(fr, **pk)[0]
        det, ncand = pick_hoop(res, cal)
        dx, dy, nsh = shift_at(doc, t)
        rec = {"t": round(float(t), 1), "seg": ci + 1, "n_cand": ncand,
               "shift_dx": round(dx, 1), "shift_dy": round(dy, 1), "n_shift": nsh}
        if det is None:
            rec["det"] = False
        else:
            hx, hy, hw, hh, cf = det
            rec.update({"det": True, "conf": round(cf, 3),
                        "w": round(hw, 1), "h": round(hh, 1),
                        # 与「标定+shift」的偏差（现有校正是否够）
                        "res_c": round(hx - (cx + dx), 1),
                        "res_cy": round(hy - (cy + dy), 1),
                        # 与「只用标定」的偏差（若只标一次 / 不补段会偏多少）
                        "raw_c": round(hx - cx, 1),
                        "raw_cy": round(hy - cy, 1)})
        rows.append(rec)
        if k % 60 == 0:
            print(f"  {name} {t/60:6.1f}/{dur/60:.1f}min  检出 "
                  f"{sum(1 for r in rows if r['det'])}/{len(rows)}", flush=True)
    cap.release()
    print(f"  {name} 完成 {len(rows)} 帧")
    return {"rows": rows, "cals": [[c.get("calib_t"), c.get("hoop_rect")] for c in cals],
            "fps": fps, "dur": dur, "video": v, "base": base}


if __name__ == "__main__":
    names = sys.argv[1:] or ["2026-10-17_1"]
    out = {}
    for n in names:
        r = run(n)
        if r:
            out[n] = r
    p = ("诊断/2026-11-07-筐识别/hoop_dense_fine.json" if T0 > 0
         else "诊断/2026-11-07-筐识别/hoop_dense_result.json")
    if os.path.exists(p):
        old = json.load(open(p, encoding="utf-8"))
        old.update(out)
        out = old
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("已写", p)
