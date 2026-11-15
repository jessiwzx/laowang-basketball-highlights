# -*- coding: utf-8 -*-
"""v8 取证轮（2026-08-20）：2026-08-07_1.mp4（720p 新机位，筐 35×14px）。

用户在 v7 逻辑上裁决了 26 条（17 进球全 low/review、9 未进球全 high）+
2 条漏检。对每个标注时刻 ±4s 抽窗重编码（帧精确 -ss），跑真检测器
（bbh.detector.detect_positions）重建检出流，窗口内重放判定器，产出：
  A. 保真度：窗口重放事件 vs events.json 存档（off/证据数是否对得上——
     采样相位差 ±1 帧、静态链窗口截断，允许小幅出入）
  B. 面积分布：真进球/误判窗口内检出面积——检验 ENTITY_MIN_PX=130 在
     720p 失效假设（真球 ~50-100px² 达不到、人肤大块轻松过）
  C. 穿越像素位置：真进球 cross 像素 x vs 标定筐左右沿（638/673）——
     区分"标定 cx 偏移"vs"插值系统偏差"
  D. 拼图：筐口局部放大 ×3 + 检出轨迹点 + 筐矩形/筐口平面标注——
     人工定性 9 条 high 误判与 2 条漏检
用法：.venv/Scripts/python tools/verify_v8.py
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cv2
import numpy as np
import imageio_ffmpeg

from bbh.detector_switch import detect_positions
from bbh.goal_decider import decide_goals, drop_static_positions
from bbh.models import Calibration

FF = imageio_ffmpeg.get_ffmpeg_exe()
# 输入视频与本机素材路径**不写死在代码里**（fork 自「视频剪辑」时的原始路径已失效）：
# 用环境变量传，默认取本仓库 work/ 下的同名分析目录。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIDEO = os.environ.get("VERIFY_VIDEO", "")          # 必填：待分析视频的完整路径
OUT = os.environ.get("VERIFY_OUT", os.path.join(_ROOT, "work", "_v8_analysis"))
_SRC = os.environ.get("VERIFY_SRC_WORK", os.path.join(_ROOT, "work"))
VNAME = os.environ.get("VERIFY_VIDEO_NAME", "2026-08-07_1")
FB = os.path.join(_SRC, VNAME, "feedback.json")
EV = os.path.join(_SRC, VNAME, "events.json")
CAL = Calibration(hoop_rect=(638, 265, 35, 14), hsv_low=(1, 116, 81),
                  hsv_high=(12, 221, 174), frame_size=(1280, 720))
HX, HY, HW, HH = 638, 265, 35, 14
CX, RIM_Y = HX + HW / 2, HY + HH * 0.35
BAND = HH * 0.25
WIN = 4.0          # 窗口半径（秒）
SCALE = 3          # 拼图放大倍数
OFFSETS = [-1.0, -0.5, -0.15, 0.15, 0.5, 1.0]   # 拼图帧相对标注时刻


def load_targets():
    fb = json.load(open(FB, encoding="utf-8"))
    targets = []
    for v in fb.get("verdicts", []):
        targets.append({"kind": "GOAL" if v["verdict"] == "goal" else "FP",
                        "t": v["t"], "ev": v["evidence"]})
    for m in fb.get("misses", []):
        targets.append({"kind": "MISS", "t": m["t"], "ev": None})
    targets.sort(key=lambda x: x["t"])
    return targets


def extract_window(i: int, t: float) -> tuple[str, float]:
    """帧精确抽窗重编码（不用 -c copy：流拷贝 -ss 对齐关键帧会偏窗起点）。"""
    x = max(0.0, t - WIN)
    tmp = os.path.join(OUT, f"win_{i:02d}.mp4")
    subprocess.run([FF, "-y", "-loglevel", "error", "-ss", f"{x:.3f}", "-i", VIDEO,
                    "-t", f"{2 * WIN:.3f}", "-c:v", "libx264", "-preset",
                    "ultrafast", "-crf", "20", "-an", tmp], check=True)
    return tmp, x


def read_frames(path: str, shift: float, wanted: list[float]):
    """窗口里挑最接近 wanted 各时刻的帧（PTS+shift 对回原片时间轴）。"""
    cap = cv2.VideoCapture(path)
    got: list[tuple[float, np.ndarray]] = []
    pool: list[tuple[float, np.ndarray]] = []
    while True:
        if not cap.grab():
            break
        msec = cap.get(cv2.CAP_PROP_POS_MSEC)
        ok, frame = cap.retrieve()
        if ok:
            pool.append((msec / 1000.0 + shift, frame))
    cap.release()
    for w in wanted:
        if not pool:
            break
        best = min(pool, key=lambda tf: abs(tf[0] - w))
        got.append(best)
    return got


def montage(path: str, shift: float, t: float, poss, name: str, title: str):
    frames = read_frames(path, shift, [t + o for o in OFFSETS])
    # 半径：筐宽 35 → 取 ±140px 横向、±120px 纵向，放大 SCALE 倍
    x0, x1 = int(CX - 140), int(CX + 140)
    y0, y1 = int(RIM_Y - 120), int(RIM_Y + 120)
    panels = []
    for ft, frame in frames:
        crop = frame[max(0, y0):y1, max(0, x0):x1].copy()
        ox, oy = max(0, x0), max(0, y0)
        cv2.rectangle(crop, (HX - ox, HY - oy), (HX + HW - ox, HY + HH - oy),
                      (0, 255, 0), 1)
        cv2.line(crop, (x0 - ox, int(RIM_Y) - oy), (x1 - ox, int(RIM_Y) - oy),
                 (0, 255, 255), 1)
        for p in poss:      # 轨迹点：上方蓝、下方红；窗口内全部画出
            if x0 <= p.x <= x1 and y0 <= p.y <= y1:
                c = (255, 64, 64) if p.y < RIM_Y else (64, 64, 255)
                cv2.circle(crop, (int(p.x) - ox, int(p.y) - oy), 2, c, -1)
        crop = cv2.resize(crop, None, fx=SCALE, fy=SCALE,
                          interpolation=cv2.INTER_NEAREST)
        cv2.putText(crop, f"t{ft - t:+.2f}", (6, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        panels.append(crop)
    if not panels:
        return
    h = max(p.shape[0] for p in panels)
    rows = [np.hstack(panels[:3]), np.hstack(panels[3:])] if len(panels) > 3 \
        else [np.hstack(panels)]
    img = np.vstack(rows)
    cv2.putText(img, title, (8, h * 2 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                (255, 255, 255), 2)
    # cv2.imwrite 在 Windows 上遇非 ASCII 路径静默失败——imencode+tofile 才可靠
    ok, buf = cv2.imencode(".png", img)
    assert ok, name
    buf.tofile(os.path.join(OUT, name))


def main():
    os.makedirs(OUT, exist_ok=True)
    targets = load_targets()
    report = []
    area_goal_below, area_fp_below = [], []

    print(f"目标 {len(targets)} 条（GOAL 17 / FP 9 / MISS 2 预期）\n")
    cache_path = os.path.join(OUT, "positions_cache.json")
    cache = {}
    if os.path.exists(cache_path):
        cache = json.load(open(cache_path, encoding="utf-8"))
    cache_prev = set(cache)
    goal_below_med: list[float] = []
    fp_below_med: list[float] = []
    for i, tg in enumerate(targets):
        t, kind = tg["t"], tg["kind"]
        ckey = f"{t:.1f}"
        if ckey in cache:
            poss = [BallPos(*p) for p in cache[ckey]]
        else:
            tmp, x0 = extract_window(i, t)
            poss, _ = detect_positions(tmp, CAL)
            for p in poss:
                p.t += x0      # 窗口容器 PTS 从 0 起 → 平移回原片时间轴
            cache[ckey] = [[p.t, p.x, p.y, p.area] for p in poss]
            os.remove(tmp)
        events = decide_goals(drop_static_positions(list(poss), CAL), CAL)
        ev = next((e for e in events if abs(e.t - t) < 0.6), None)

        # 面积取证：标注时刻 ±1.5s 内、筐口平面以下的检出
        below = [p for p in poss if abs(p.t - t) <= 1.5 and p.y > RIM_Y + BAND]
        (area_goal_below if kind == "GOAL" else
         area_fp_below if kind == "FP" else []).extend(p.area for p in below)
        # 位置取证：下方点的横向分布（真球穿网落成窄柱；人块漂移宽）
        below_x = sorted(p.x for p in below)
        below_med = below_x[len(below_x) // 2] if below_x else None
        (goal_below_med if kind == "GOAL" else
         fp_below_med if kind == "FP" else []).append(below_med)

        old = tg["ev"] or {}
        old_status = old.get("status", "-")
        row = {
            "kind": kind, "t": t,
            "old_status": old_status, "old_reason": old.get("low_reason"),
            "old_off": old.get("cross_off"),
            "new_status": ev.status if ev else None,
            "new_reason": ev.low_reason if ev else None,
            "new_off": ev.cross_off if ev else None,
            "new_conf": ev.confidence if ev else None,
            "below_big_new": ev.below_big if ev else None,
            "below_areas": sorted(round(p.area) for p in below),
            "below_med_x": below_med,
            "below_x_range": (below_x[0], below_x[-1]) if below_x else None,
        }
        report.append(row)

        head = (f"{kind:<4} t={t:7.1f} 存档={old_status:<6}"
                f" off={row['old_off']}")
        if ev:
            cross_px = CX + ev.cross_off * HW
            print(f"{head} -> 重放={ev.status:<6} off={ev.cross_off:+.3f}"
                  f"（cross x={cross_px:.0f}，筐沿 {HX}~{HX+HW}）"
                  f" conf={ev.confidence} below_big={ev.below_big}")
        else:
            print(f"{head} -> 重放=无事件")
        print(f"      下方检出面积: {row['below_areas'] or '无'}")

        if ckey not in cache_prev:      # 首跑生成拼图（窗口文件只在未命中时存在）
            title = (f"{kind} t={t:.1f} old={old_status}"
                     + (f" off={old.get('cross_off'):+.2f}" if old.get('cross_off') is not None else ""))
            montage(tmp, x0, t, poss,
                    f"montage_{kind}_{int(t):04d}.png", title)

    # ---- 汇总 ----
    def dist(a):
        if not a:
            return "无"
        a = sorted(a)
        return (f"n={len(a)} min={a[0]:.0f} 中位={a[len(a)//2]:.0f} "
                f"max={a[-1]:.0f} | ≥130px²: {sum(v >= 130 for v in a)}"
                f" | ≥49px²(0.1×筐): {sum(v >= 49 for v in a)}")

    print("\n== B. 筐口下方检出面积分布 ==")
    print(f"真进球窗口: {dist(area_goal_below)}")
    print(f"误判窗口  : {dist(area_fp_below)}")

    print("\n== E. 真实筐心估计（进球下方柱中位 x，标定 cx=655.5）==")
    g = sorted(v for v in goal_below_med if v is not None)
    f = sorted(v for v in fp_below_med if v is not None)
    if g:
        print(f"真进球: n={len(g)} 中位={g[len(g)//2]:.0f} 范围 {g[0]:.0f}~{g[-1]:.0f}")
    if f:
        print(f"误判  : n={len(f)} 中位={f[len(f)//2]:.0f} 范围 {f[0]:.0f}~{f[-1]:.0f}")
    print("下方 x_range 分布（窄柱 vs 宽漂）:")
    for lbl, rs in (("真进球", [r for r in report if r["kind"] == "GOAL"]),
                    ("误判", [r for r in report if r["kind"] == "FP"])):
        rngs = [r["below_x_range"][1] - r["below_x_range"][0]
                for r in rs if r["below_x_range"]]
        if rngs:
            rngs.sort()
            print(f"  {lbl}: n={len(rngs)} 中位宽={rngs[len(rngs)//2]:.0f}px"
                  f" 范围 {rngs[0]:.0f}~{rngs[-1]:.0f}px")

    print("\n== A/C. 保真度与穿越位置 ==")
    same = sum(1 for r in report
               if r["old_status"] != "-" and r["new_status"]
               and (r["new_status"] == r["old_status"]
                    or {r["new_status"], r["old_status"]} <= {"review", "high"}))
    print(f"档位一致(含 high/review 混计): {same}/{sum(1 for r in report if r['old_status'] != '-')}")
    goals_ev = [r for r in report if r["kind"] == "GOAL" and r["new_off"] is not None]
    if goals_ev:
        offs = [r["new_off"] for r in goals_ev]
        print(f"真进球重放 off: min={min(offs):+.3f} max={max(offs):+.3f}"
              f"（{sum(o > 0 for o in offs)}/{len(offs)} 为正）")

    json.dump(report, open(os.path.join(OUT, "verify_v8_report.json"), "w",
                           encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(cache, open(cache_path, "w", encoding="utf-8"))
    print(f"\n-> {OUT}/verify_v8_report.json + montage_*.png 共 {len(targets)} 张")


if __name__ == "__main__":
    main()
