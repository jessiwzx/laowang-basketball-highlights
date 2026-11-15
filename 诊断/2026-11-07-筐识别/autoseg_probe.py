"""自动补段 · 第 1 步验证（rev2）：用「筐残差」判跳变，而不是 ORB 失配。

rev1 的结论：ORB 失配不是对的判据 —— 10-30_1 全片 107min 零失配，但用户
在 46:15 补过一段（机位只挪了 31px，ORB 跟得住）。真正恶化的是「筐的局部
位移与全图位移中值不一致」（摇镜视差）。

判据改为残差：
    c(t) = [筐心(t) − 筐心(参考帧)] − shift_orb(t)        （单位：像素）
    |c(t)| / 筐宽 > C_TH 且持续 >= 30s   →  该处需要新段

用法：
  python 诊断/2026-11-07-筐识别/autoseg_probe.py 2026-10-30_1
  python 诊断/2026-11-07-筐识别/autoseg_probe.py --all
"""
import json
import os
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, str(ROOT))

from bbh import detector_yolo, motion                      # noqa: E402
from bbh.calibration import load_segments_dir              # noqa: E402

SRC = r"D:\claude code\1视频获取\归档"
OUT = HERE

SAMPLE_EVERY_SEC = 10.0     # 采样间隔（含 YOLO 推理，比 rev1 放宽到 10s）
C_TH = 0.30                 # 残差门限（筐宽倍数，= goal_decider 的 review 门）
HOLD_SEC = 30.0             # 超门限持续时长
MIN_HOOP_CONF = 0.10        # 采样时筐检出下限（低于算未检出）
REF_CAND_OFFSETS = (0, 2, 5, 8, 12, 15, 20)


def _read_at(cap, t, fps):
    fi = int(round(t * fps))
    cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
    ok, frame = cap.read()
    return (int(round(fi / fps * 1000)) / 1000.0, frame) if ok else (None, None)


def scan(name, workdir=None):
    """全片扫：ORB 位移 + 筐检出 → 残差 c(t)。"""
    video = os.path.join(SRC, name + ".mp4")
    segs = load_segments_dir(workdir or os.path.join(ROOT, "work", name))
    if not segs:
        print(f"{name}: 无标定，跳过")
        return None
    seg1 = segs[0]
    ref_path = seg1.ref_image
    ref = motion.load_ref_image(ref_path)
    if ref is None:
        print(f"{name}: 参考帧不可读，跳过")
        return None
    rect = seg1.calib.hoop_rect
    hw = float(rect[2])
    ref_hoop = detector_yolo.hoop_center_of(ref, rect)
    if ref_hoop is None:
        print(f"{name}: 参考帧上检不出筐，跳过")
        return None
    print(f"  参考帧筐心 {ref_hoop[0]:.1f},{ref_hoop[1]:.1f}  筐宽 {hw:.0f}px")

    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    dur = cap.get(cv2.CAP_PROP_FRAME_COUNT) / fps
    tr = motion.MotionTracker(ref)
    rows = []
    t_start = time.time()
    times = np.arange(0.0, dur, SAMPLE_EVERY_SEC)
    for i, tt in enumerate(times):
        t, frame = _read_at(cap, float(tt), fps)
        if frame is None:
            continue
        ok = tr.update(frame, t)
        sx, sy = tr.shift(t)
        hp = detector_yolo.hoop_center_of(frame, rect)
        if hp and hp[4] >= MIN_HOOP_CONF:
            cx = hp[0] - ref_hoop[0] - sx
            cy = hp[1] - ref_hoop[1] - sy
            rows.append({"t": t, "orb_ok": bool(ok), "sx": round(sx, 1),
                         "sy": round(sy, 1), "cx": round(cx, 1),
                         "cy": round(cy, 1), "res": round((cx * cx + cy * cy) ** .5, 1),
                         "res_w": round((cx * cx + cy * cy) ** .5 / hw, 3),
                         "hoop_conf": round(hp[4], 2),
                         "hoop_w": round(hp[2], 1)})
        else:
            rows.append({"t": t, "orb_ok": bool(ok), "sx": round(sx, 1),
                         "sy": round(sy, 1), "cx": None, "cy": None,
                         "res": None, "res_w": None, "hoop_conf": None,
                         "hoop_w": None})
        if i and i % 120 == 0:
            bad = sum(1 for r in rows if r["res_w"] is not None and r["res_w"] > C_TH)
            print(f"  {name} {t / 60:5.1f}/{dur / 60:.0f}min  筐检出 "
                  f"{sum(1 for r in rows if r['res'] is not None)}/{len(rows)}"
                  f"  超门限 {bad}  ({time.time() - t_start:.0f}s)")
    cap.release()
    det = sum(1 for r in rows if r["res"] is not None)
    print(f"  {name} 扫完 {len(rows)} 样本（筐检出 {det}），耗时 {time.time() - t_start:.0f}s")
    return {"name": name, "video": video, "fps": fps, "dur": dur,
            "rect": list(rect), "hw": hw, "ref_hoop": list(ref_hoop),
            "ref": ref_path, "rows": rows,
            "manual_t0": [round(s.t0, 2) for s in segs[1:]],
            "manual_rects": [list(s.calib.hoop_rect) for s in segs[1:]]}


def find_exceed(rows, th=C_TH, hold=HOLD_SEC):
    """连续超门限（按时间跨度算，容许中间漏检）→ 区段列表。"""
    out, cur = [], None
    for r in rows:
        bad = r["res_w"] is not None and r["res_w"] > th
        if bad:
            if cur is None:
                cur = {"t0": r["t"], "t1": r["t"], "n": 1,
                       "max": r["res_w"], "max_t": r["t"]}
            else:
                cur["t1"] = r["t"]
                cur["n"] += 1
                if r["res_w"] > cur["max"]:
                    cur["max"], cur["max_t"] = r["res_w"], r["t"]
        else:
            if cur and cur["t1"] - cur["t0"] >= hold:
                out.append(cur)
            cur = None
    if cur and cur["t1"] - cur["t0"] >= hold:
        out.append(cur)
    return out


def _seg_report(rows, t0, t1, hw):
    v = [r for r in rows if t0 <= r["t"] < t1]
    rw = [r["res_w"] for r in v if r["res_w"] is not None]
    if not rw:
        return f"n={len(v)} 无筐检出"
    rw.sort()
    return (f"n={len(v)} 筐检出{len(rw)}  残差中位 {rw[len(rw) // 2]:.2f}W  "
            f"P90 {rw[int(len(rw) * .9)]:.2f}W  最大 {rw[-1]:.2f}W")


def run(name, workdir=None):
    print(f"\n{'=' * 72}\n=== {name}")
    sc = scan(name, workdir)
    if not sc:
        return None
    rows, hw = sc["rows"], sc["hw"]
    print(f"\n  用户手标 t0：{[round(x / 60, 2) for x in sc['manual_t0']]} min")
    print(f"  手标各段筐宽：{[r[2] for r in sc['manual_rects']]}")
    # 按手标分段看残差水平
    bounds = [0.0] + list(sc["manual_t0"]) + [sc["dur"] + 1]
    print("\n  按用户手标分段，看「用段 1 标定 + ORB」的残差（单位筐宽）：")
    for i in range(len(bounds) - 1):
        print(f"    段{i + 1} [{bounds[i] / 60:6.1f}~{bounds[i + 1] / 60:6.1f}min]  "
              + _seg_report(rows, bounds[i], bounds[i + 1], hw))

    ex = find_exceed(rows)
    print(f"\n  残差 >{C_TH}W 且持续 >={HOLD_SEC:.0f}s 的区段：{len(ex)}")
    for e in ex:
        print(f"    {e['t0'] / 60:7.2f}~{e['t1'] / 60:7.2f}min  n={e['n']}"
              f"  峰值 {e['max']:.2f}W @ {e['max_t'] / 60:.2f}min")
    print("\n  自动区段 vs 手标 t0：")
    for m in sc["manual_t0"]:
        near = [e for e in ex if abs(e["t0"] - m) <= 180 or abs(e["t1"] - m) <= 180]
        flag = "✓ 命中" if near else "✗ 未命中"
        print(f"    手标 {m / 60:7.2f}min  {flag}"
              + (f"（自动 {near[0]['t0'] / 60:.2f}min 起）" if near else ""))
    sc["exceed"] = ex
    return sc


def main(argv):
    names = [a for a in argv if not a.startswith("--")]
    workdir = None
    tag = "autoseg_probe"
    for a in argv:
        if a.startswith("--workdir="):
            workdir = a.split("=", 1)[1]
        if a.startswith("--tag="):
            tag = a.split("=", 1)[1]
    if "--all" in argv or not names:
        names = ["2026-10-17_1", "2026-10-23_1", "2026-10-30_1", "2026-10-31_1"]
    results = [r for r in (run(n, workdir) for n in names) if r]
    p = os.path.join(OUT, tag + ".json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print(f"\n→ {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
