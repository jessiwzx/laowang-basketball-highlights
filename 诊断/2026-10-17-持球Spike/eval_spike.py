# -*- coding: utf-8 -*-
"""R-018 持球盲区补训 Spike 验收评估（只读诊断，不改任何生产代码）。

口径（方案 §0，一次说死）：
- held-out 两场 2026-09-04_1 / 2026-09-11_3，各取事件时间序前 40 球；
- 持球段 = [rel-0.9, rel-0.2] 均匀 6 帧，命中 = 任一帧 YOLO ball（conf≥门）
  中心与 release_xy 距离 ≤ R；
- 飞行段 = [rel+0.2, rel+1.0] 均匀 6 帧，命中 = 任一帧 YOLO ball 中心与
  该帧 HSV 检出（hsv_pick，shooter 口径面积门）距离 ≤ R；
- R 敏感性 40/60/80px；conf 门 0.05/0.15/0.25 离线套用；
- 模型：base(生产 best.pt)@1024（校准历史基线）/ base@1280 / spike@1280；
过门线：持球召回 ≥50%（基线 4/40、8/40）且飞行段降幅 ≤5pct → 过；
<30% → Spike 终局失败。
"""
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]        # 视频剪辑YOLO 仓（bbh 模块 + base 权重）
YOLO_REPO = Path(r"D:/claude code/yolo")          # 训练产物所在仓
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from bbh.models import Calibration, CalibSegment  # noqa: E402
from bbh.pipeline import _normalize_segments  # noqa: E402
from bbh.shooter import _detect_source, _seg_for_t, _scaled_rect  # noqa: E402

WORK = Path(r"D:/claude code/视频剪辑/work")
OUT = Path(__file__).resolve().parent
FIELDS = ["2026-09-04_1", "2026-09-11_3", "2026-09-19_1", "2026-10-10_1"]
NBALL = 40
R_LIST = [40, 60, 80]
GATES = [0.05, 0.15, 0.25]
MODELS = {
    "base1024": (ROOT / "bbh" / "weights" / "best.pt", 1024),
    "base1280": (ROOT / "bbh" / "weights" / "best.pt", 1280),
    "spike1280": (YOLO_REPO / "runs" / "held_spike_v1" / "weights" / "best.pt", 1280),
}
FP16_TRY = True


def parse_key(key: str) -> float:
    m, s = key.split(":")
    return int(m) * 60 + float(s)


def load_segs(work_dir: Path) -> list:
    def mk(d):
        return Calibration(hoop_rect=tuple(d["hoop_rect"]),
                           hsv_low=tuple(d["hsv_low"]),
                           hsv_high=tuple(d["hsv_high"]),
                           frame_size=tuple(d["frame_size"]),
                           ref_image=d.get("ref_image"),
                           ref_hash=d.get("ref_hash"),
                           calib_t=d.get("calib_t", 0.0))
    c0 = json.loads((work_dir / "calibration.json").read_text(encoding="utf-8"))
    cb = mk(c0)
    segs = [CalibSegment(t0=cb.calib_t or 0.0, calib=cb)]
    for n in range(2, 30):
        p = work_dir / f"calibration_seg{n}.json"
        if not p.exists():
            break
        c2 = mk(json.loads(p.read_text(encoding="utf-8")))
        segs.append(CalibSegment(t0=c2.calib_t, calib=c2))
    return _normalize_segments(segs, None)


def hsv_cands(frame, hsv_lo, hsv_hi, hoop_w):
    """HSV 全部合格橙 blob（面积 9 ~ 0.5x筐宽²），按面积降序。
    飞行段锚用全集（最大 blob 在部分场次会锁错目标），命中 = YOLO 检出
    与任一候选距离 ≤ R——两模型同锚，公平对比。"""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(hsv_lo, np.uint8), np.array(hsv_hi, np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(mask)
    out = []
    for i in range(1, n):
        a = int(stats[i, cv2.CC_STAT_AREA])
        if a < 9 or a > 0.5 * hoop_w * hoop_w:
            continue
        out.append((float(cent[i][0]), float(cent[i][1]), a))
    out.sort(key=lambda x: -x[2])
    return out


def yolo_model(tag):
    from ultralytics import YOLO
    w, imgsz = MODELS[tag]
    m = YOLO(str(w))
    kw = dict(imgsz=imgsz, conf=0.05, iou=0.7, classes=[0],
              device=0, verbose=False)
    global INFER_KW
    INFER_KW = kw
    if FP16_TRY:
        try:
            m.predict(np.zeros((64, 64, 3), np.uint8), quantize=16, **kw)
            INFER_KW["quantize"] = 16
        except Exception:
            pass
    return m


INFER_KW = {}


def infer(model, frame):
    r = model.predict(frame, **INFER_KW)[0]
    return [((float(b.xyxy[0][0]) + float(b.xyxy[0][2])) / 2.0,
             (float(b.xyxy[0][1]) + float(b.xyxy[0][3])) / 2.0,
             float(b.conf[0])) for b in r.boxes]


def sample_ts(rel):
    """[a,b] 均匀 6 个时刻。"""
    a, b = rel - 0.9, rel - 0.2
    return [a + i * (b - a) / 5.0 for i in range(6)]


def run_field_v2(field, tag, model):
    """主实现：每帧证据对各 R 独立判定。"""
    t0 = time.time()
    work_dir = WORK / field
    sh = json.loads((work_dir / "shooters.json").read_text(encoding="utf-8"))
    segs = load_segs(work_dir)
    src = _detect_source(sh["video"], str(work_dir))
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise RuntimeError(f"{field}: 打不开视频 {src}")
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    events = []
    for k, t in sh["traces"].items():
        if not t.get("release_t") or not t.get("release_xy"):
            continue
        events.append((parse_key(k), k, t))
    events.sort()
    n_all = len(events)
    events = events[:NBALL]
    print(f"[{field}/{tag}] 评估 {len(events)}/{n_all} 球", flush=True)

    # hits[seg][gate][R] = 命中球数；fly_anchor_none[gate] = 飞行段 HSV 无锚帧数
    hits = {"held": {g: {R: 0 for R in R_LIST} for g in GATES},
            "fly": {g: {R: 0 for R in R_LIST} for g in GATES}}
    fly_anchor_fail = 0
    for n, (e_t, key, tr) in enumerate(events, 1):
        sc = _seg_for_t(segs, e_t).calib
        hx, hy, hw, hh = _scaled_rect(sc, fw, fh)
        rel_t = float(tr["release_t"])
        rel_xy = tr["release_xy"]
        samples = ([("held", tt) for tt in sample_ts(rel_t)]
                   + [("fly", tt) for tt in
                      [rel_t + 0.2 + i * 0.8 / 5.0 for i in range(6)]])
        hit = {"held": {g: {R: False for R in R_LIST} for g in GATES},
               "fly": {g: {R: False for R in R_LIST} for g in GATES}}
        for seg, tt in samples:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(tt, 0.0) * 1000.0)
            if not cap.grab():
                continue
            ok, frame = cap.retrieve()
            if not ok:
                continue
            if seg == "fly":
                cands = hsv_cands(frame, sc.hsv_low, sc.hsv_high, hw)
                if not cands:
                    fly_anchor_fail += 1
                    continue
                for (cx, cy, cf) in infer(model, frame):
                    for g in GATES:
                        if cf < g:
                            continue
                        for (ax, ay, _) in cands:
                            d = math.hypot(cx - ax, cy - ay)
                            for R in R_LIST:
                                if d <= R:
                                    hit[seg][g][R] = True
            else:
                anchor = (float(rel_xy[0]), float(rel_xy[1]))
                for (cx, cy, cf) in infer(model, frame):
                    d = math.hypot(cx - anchor[0], cy - anchor[1])
                    for g in GATES:
                        if cf < g:
                            continue
                        for R in R_LIST:
                            if d <= R:
                                hit[seg][g][R] = True
        for seg in ("held", "fly"):
            for g in GATES:
                for R in R_LIST:
                    if hit[seg][g][R]:
                        hits[seg][g][R] += 1
        if n % 10 == 0:
            print(f"[{field}/{tag}] {n}/{len(events)} "
                  f"({time.time()-t0:.0f}s)", flush=True)
    cap.release()
    nb = len(events)
    summary = {"field": field, "tag": tag, "n": nb,
               "fly_anchor_fail": fly_anchor_fail}
    for seg in ("held", "fly"):
        for g in GATES:
            summary[f"{seg}_g{g}"] = {
                R: round(100 * hits[seg][g][R] / nb, 1) for R in R_LIST}
            summary[f"{seg}_g{g}_hits"] = {R: hits[seg][g][R] for R in R_LIST}
    print(f"[{field}/{tag}] 完成 {time.time()-t0:.0f}s → "
          f"held g0.05 R60 = {summary['held_g0.05'][60]}%", flush=True)
    return summary


def main():
    args = sys.argv[1:]
    fields = [a.split("=", 1)[1] for a in args if a.startswith("--fields=")]
    only = [a for a in args if not a.startswith("--")] or list(MODELS)
    if fields:
        global FIELDS
        FIELDS = fields
    allres = {}
    for tag in only:
        model = yolo_model(tag)
        for field in FIELDS:
            allres[f"{field}|{tag}"] = run_field_v2(field, tag, model)
    (OUT / "eval_spike.json").write_text(
        json.dumps(allres, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n=== 汇总（主口径 R=60px, g=0.05）===", flush=True)
    for field in FIELDS:
        row = [f"{tag}: 持球 {allres[f'{field}|{tag}']['held_g0.05'][60]}% / "
               f"飞行 {allres[f'{field}|{tag}']['fly_g0.05'][60]}%"
               for tag in only]
        print(f"{field}  " + " | ".join(row), flush=True)


if __name__ == "__main__":
    main()
