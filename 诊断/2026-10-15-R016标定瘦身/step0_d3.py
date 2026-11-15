# -*- coding: utf-8 -*-
"""Step 0 补充：D3 变体（贴 7 场观测并集的收敛默认）+ 降档样本的可视化裁决。

D3 = low=[0, 88, 60], high=[19, 232, 205]   （观测并集 H≤17/S≤227/V≤199 各留余量）
可视化：对指定降档球，把 A 与 D1 的 (release_t, release_xy) 各裁一帧小图并排，
人工判谁框住了真实出手人。
"""
import json, os, sys
from dataclasses import replace

ROOT = r"D:/claude code/视频剪辑YOLO"
sys.path.insert(0, ROOT)
import cv2
import numpy as np
from PIL import Image

from bbh.models import Calibration
from bbh.shooter import _grab_at
from step0_ab import GAMES, run_one

D3 = ((0, 88, 60), (19, 232, 205))
OUT = os.path.join(ROOT, "诊断", "2026-10-15-R016标定瘦身")

# 复检样本：每场抽 2 个降档 + 1 个升档（看 D1 修好的长什么样）
CHECK = {
    "09-04_1": ["09:24.8", "26:45.9", "25:38.6"],
    "10-10_1": ["03:21.6", "03:42.2", "23:48.4"],
    "06-28_1": ["07:38.2", "24:11.2", "14:49.6"],
}


def crop_at(video, t, xy, half=110):
    fr = _grab_at(video, t)
    if fr is None or not xy:
        return None
    x, y = int(xy[0]), int(xy[1])
    H, W = fr.shape[:2]
    x0, x1 = max(0, x - half), min(W, x + half)
    y0, y1 = max(0, y - half), min(H, y + half)
    c = fr[y0:y1, x0:x1].copy()
    cv2.rectangle(c, (x - x0 - 2, y - y0 - 2), (x - x0 + 2, y - y0 + 2), (0, 0, 255), 1)
    cv2.putText(c, f"t={t:.2f}", (4, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)
    return cv2.cvtColor(c, cv2.COLOR_BGR2RGB)


def main():
    report = {}
    for name, work in GAMES:
        calib_d = json.load(open(os.path.join(work, "calibration.json"), encoding="utf-8"))
        calib = Calibration(**calib_d)
        video = json.load(open(os.path.join(work, "shooters.json"), encoding="utf-8"))["video"]
        ev = json.load(open(os.path.join(work, "events.json"), encoding="utf-8"))
        evs = ev.get("events") or ev.get("rows") or ev
        ev_t = {}
        from bbh.shooter import time_key
        for e in evs:
            ev_t[time_key(e["t"])] = e["t"]
        rows = []
        cells = []
        for k in CHECK.get(name, []):
            if k not in ev_t:
                continue
            t_goal = ev_t[k]
            A = run_one(video, calib, t_goal)
            D3r = run_one(video, replace(calib, hsv_low=list(D3[0]), hsv_high=list(D3[1])), t_goal)
            rows.append({"key": k, "A": A, "D3": D3r})
            print(f"  {name} {k}: A={A['status']}/{A['spot']}(tn={A['track_n']}) "
                  f"D3={D3r['status']}/{D3r['spot']}(tn={D3r['track_n']})", flush=True)
            for tag, r in (("A", A), ("D3", D3r)):
                if r["release_t"] is not None and r["release_xy"]:
                    img = crop_at(video, r["release_t"], r["release_xy"])
                    if img is not None:
                        im = np.asarray(img)
                        cv2.putText(cv2.cvtColor(im, cv2.COLOR_RGB2BGR),
                                    f"{tag} {k}", (4, 210),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 0), 2)
                        cells.append(im)
        if cells:
            h = max(c.shape[0] for c in cells)
            cells = [cv2.copyMakeBorder(cv2.cvtColor(c, cv2.COLOR_RGB2BGR),
                                        0, h - c.shape[0], 0, 4,
                                        cv2.BORDER_CONSTANT, value=(30, 30, 30))
                     for c in cells]
            canvas = np.hstack(cells)
            p = os.path.join(OUT, f"step0_裁决_{name}.png")
            Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)).save(p)
            print("  saved", p, flush=True)
        low = sum(1 for r in rows if r["D3"]["status"] == "low")
        bad = sum(1 for r in rows if r["D3"]["spot"] == "bad")
        report[name] = {"n": len(rows), "D3_low": low, "D3_spot_bad": bad, "rows": rows}
    json.dump(report, open(os.path.join(OUT, "step0_D3.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("saved step0_D3.json")


if __name__ == "__main__":
    main()
