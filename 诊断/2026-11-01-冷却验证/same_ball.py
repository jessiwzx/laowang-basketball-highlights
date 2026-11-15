"""同一颗球判定：增量行与最近基线行之间，球是否出现"新的接近轨迹"。

判据（只读，不改判定器）：
  - 两行之间若存在 ≥2 个筐口上方点，且其中至少一个"高处点"
    （y < rim_y - 2*筐高）或"横向离开点"（|x-cx| > 1.3*筐宽）
    → 是一条独立的上方接近轨迹 → 判"另一颗球"
  - 否则 → 判"同一颗球的重复配对"（球穿一次，被配出两个 cross 时刻）

用法: python same_ball.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from bbh import goal_decider as gd                       # noqa: E402
from bbh.models import BallPos, Calibration              # noqa: E402
from bbh import static_filter                            # noqa: E402
from bbh.pipeline import _merge_segment_events           # noqa: E402

WORK = "D:/claude code/视频剪辑/work"
CASES = ["2026-09-12_1", "2026-09-04_1", "2026-10-10_1"]

BAND_RATIO = gd.BAND_RATIO
RIM_RATIO = gd.RIM_Y_RATIO
XM = gd.X_MARGIN


def load_blocks(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    raw = d.get("segments")
    if raw is None:
        raw = [d]
    out = []
    for s in raw:
        c = s["calib"]
        calib = Calibration(hoop_rect=tuple(c[0]), hsv_low=tuple(c[1]),
                            hsv_high=tuple(c[2]), frame_size=tuple(c[3]))
        n = s.get("positions_len", len(s.get("positions", [])))
        pos = [BallPos(t=r[0], x=r[1], y=r[2], area=r[3],
                       fi=r[4] if len(r) > 4 else -1)
               for r in s["positions"][:n]]
        out.append({"t0": float(s.get("t0") or 0.0),
                    "t1": (float(s["t1"]) if s.get("t1") is not None else None),
                    "calib": calib, "positions": pos})
    return d, out


def seg_of(blocks, t):
    for b in blocks:
        if b["t0"] - 0.01 <= t and (b["t1"] is None or t <= b["t1"] + 0.01):
            return b
    return blocks[-1]


def geom(calib):
    hx, hy, hw, hh = calib.hoop_rect
    rim_y = hy + hh * RIM_RATIO
    band = hh * BAND_RATIO
    return (hx, hw, hh, rim_y, band,
            hx - hw * XM, hx + hw * (1 + XM), hx + hw / 2)


def analyse(blocks, extra, base_ts):
    rows = []
    for e in extra:
        # 前一行基线事件（占掉冷却钟的那一行），不是绝对最近——增量行可能
        # 排在基线行之前（被更早的行占钟）
        prev = [t for t in base_ts if t < e.t]
        b = max(prev) if prev else min(base_ts)
        seg = seg_of(blocks, e.t)
        hx, hw, hh, rim_y, band, x_lo, x_hi, cx = geom(seg["calib"])
        mid = [p for p in seg["positions"] if b + 0.02 < p.t < e.t - 0.02]
        up = [p for p in mid if x_lo <= p.x <= x_hi and p.y < rim_y - band]
        high_pt = [p for p in up if p.y < rim_y - 2 * hh]
        side_pt = [p for p in mid if abs(p.x - cx) > 1.3 * hw]
        deep = [p for p in mid if p.y > rim_y + 3 * hh]
        span = (up[-1].t - up[0].t) if len(up) >= 2 else 0.0
        novel = (len(up) >= 2 and span >= 0.2
                 and (bool(high_pt) or bool(side_pt)))
        rows.append({"t": e.t, "status": e.status, "dT": round(e.t - b, 3),
                     "n_up": len(up), "span": round(span, 2),
                     "high_pt": len(high_pt), "side_pt": len(side_pt),
                     "deep": len(deep), "novel": novel})
    return rows


def main():
    agg = {}
    for name in CASES:
        doc, blocks = load_blocks(os.path.join(WORK, name, "positions.json"))
        pts_list = [static_filter.apply(b["positions"], b["calib"]) for b in blocks]
        base = _merge_segment_events(
            [gd.decide_goals(p, b["calib"]) for p, b in zip(pts_list, blocks)],
            [b["t0"] for b in blocks])
        no_cd = _merge_segment_events(
            [gd.decide_goals(p, b["calib"], cooldown_keep_ts=set())
             for p, b in zip(pts_list, blocks)],
            [b["t0"] for b in blocks])
        base_ts = [e.t for e in base]
        extra = [e for e in no_cd
                 if not any(abs(e.t - t) <= 0.05 for t in base_ts)]
        rows = analyse(blocks, extra, base_ts)
        print(f"\n=== {name}  增量 {len(rows)} 条 ===")
        for r in sorted(rows, key=lambda x: x["dT"]):
            flag = "另一颗球" if r["novel"] else "同球重复"
            print(f"  t={r['t']:8.2f} {r['status']:6s} ΔT={r['dT']:5.2f}s "
                  f"up={r['n_up']:>2} span={r['span']:4.1f} 高处={r['high_pt']:>2} "
                  f"侧移={r['side_pt']:>2} 深={r['deep']:>2}  → {flag}")
        for lo, hi in [(0, .3), (.3, .7), (.7, 1.5), (1.5, 3), (3, 9e9)]:
            sel = [r for r in rows if lo <= r["dT"] < hi]
            if not sel:
                continue
            novel = sum(1 for r in sel if r["novel"])
            hi_n = sum(1 for r in sel if r["status"] == "high")
            agg.setdefault(f"{lo:g}~{hi:g}", [0, 0, 0])
            agg[f"{lo:g}~{hi:g}"][0] += len(sel)
            agg[f"{lo:g}~{hi:g}"][1] += novel
            agg[f"{lo:g}~{hi:g}"][2] += hi_n
        json.dump(rows, open(os.path.join(HERE, f"same_ball_{name}.json"),
                             "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("\n=== 三场合计（按 ΔT 分桶）===")
    print("  ΔT 桶        增量  判'另一颗球'  其中high")
    for k, v in agg.items():
        print(f"  {k:>9}s : {v[0]:>4}      {v[1]:>4}        {v[2]:>4}")


if __name__ == "__main__":
    main()
