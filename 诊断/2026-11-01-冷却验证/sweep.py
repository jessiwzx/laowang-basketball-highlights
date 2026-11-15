"""冷却窗口扫描：把 COOLDOWN_SEC 换成不同值，看每场多出多少行。

只改判定器/合并两个模块的模块级常量（不动源码文件、不写 events）。
用法: python sweep.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from bbh import goal_decider as gd, static_filter, pipeline   # noqa: E402
from bbh.models import BallPos, Calibration                   # noqa: E402

WORK = "D:/claude code/视频剪辑/work"
CASES = ["2026-09-12_1", "2026-09-04_1", "2026-10-10_1"]
WINDOWS = [3.0, 2.0, 1.5, 1.0, 0.7, 0.5, 0.0]


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
        out.append({"t0": float(s.get("t0") or 0.0), "calib": calib,
                    "positions": pos})
    return out


def run(blocks, win):
    old_gd, old_pl = gd.COOLDOWN_SEC, pipeline.COOLDOWN_SEC
    gd.COOLDOWN_SEC = pipeline.COOLDOWN_SEC = win
    try:
        per = [gd.decide_goals(static_filter.apply(b["positions"], b["calib"]),
                               b["calib"]) for b in blocks]
        return pipeline._merge_segment_events(per, [b["t0"] for b in blocks])
    finally:
        gd.COOLDOWN_SEC, pipeline.COOLDOWN_SEC = old_gd, old_pl


def main():
    table = {}
    for name in CASES:
        blocks = load_blocks(os.path.join(WORK, name, "positions.json"))
        row = {}
        for w in WINDOWS:
            evs = run(blocks, w)
            c = {"high": 0, "review": 0, "low": 0}
            for e in evs:
                c[e.status] += 1
            row[w] = {"total": len(evs), **c}
        table[name] = row
        base = row[3.0]
        print(f"\n=== {name} ===")
        print("  窗口   总数  Δ总数   high  Δhigh  review   low")
        for w in WINDOWS:
            r = row[w]
            print(f"  {w:4.1f}s {r['total']:>5}  {r['total']-base['total']:+5d}"
                  f"  {r['high']:>5} {r['high']-base['high']:+5d}"
                  f"  {r['review']:>6}  {r['low']:>4}")

    print("\n=== 三场合计 ===")
    print("  窗口   总数  Δ总数   high  Δhigh  review   low")
    for w in WINDOWS:
        tt = sum(table[n][w]["total"] for n in CASES)
        tb = sum(table[n][3.0]["total"] for n in CASES)
        hh = sum(table[n][w]["high"] for n in CASES)
        hb = sum(table[n][3.0]["high"] for n in CASES)
        rv = sum(table[n][w]["review"] for n in CASES)
        lw = sum(table[n][w]["low"] for n in CASES)
        print(f"  {w:4.1f}s {tt:>5}  {tt-tb:+5d}  {hh:>5} {hh-hb:+5d}  {rv:>6}  {lw:>4}")

    json.dump(table, open(os.path.join(HERE, "sweep_result.json"), "w",
                          encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
