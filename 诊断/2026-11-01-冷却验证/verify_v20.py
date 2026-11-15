"""v20 冷却身份判据验收：三场重放，v20 vs v19（NEWBALL_ENABLE=False）。

只看判定器裸结果（static_filter → decide_goals → 跨段合并），不含网分
pass——与 2026-11-01 取证脚本同口径，便于与 31 条预期直接对拍。

用法: python verify_v20.py [场次名...]
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from bbh import goal_decider as gd                       # noqa: E402
from bbh import static_filter                            # noqa: E402
from bbh.models import BallPos, Calibration              # noqa: E402

WORK = "D:/claude code/视频剪辑/work"
CASES = ["2026-09-12_1", "2026-09-04_1", "2026-10-10_1"]


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
    return out


def run(blocks, pts_list, enable):
    """→ [(event, 段号)]。跨段冷却合并按 _merge_segment_events 同款逻辑
    复刻，但保留段号用于归因。"""
    gd.NEWBALL_ENABLE = enable
    per = [gd.decide_goals(p, b["calib"]) for p, b in zip(pts_list, blocks)]
    tagged = []
    for si, evs in enumerate(per):
        tagged.extend((e, si) for e in evs)
    tagged.sort(key=lambda p: p[0].t)
    out = []
    for e, si in tagged:
        if out and out[-1][1] != si and e.t - out[-1][0].t < gd.COOLDOWN_SEC:
            continue
        out.append((e, si))
    return out


def dist(evs):
    return {s: sum(1 for e in evs if e.status == s)
            for s in ("high", "review", "low")}


def main():
    cases = sys.argv[1:] or CASES
    tot = {"v19": [0, 0, 0], "v20": [0, 0, 0], "add": [0, 0, 0]}
    report = {}
    for name in cases:
        blocks = load_blocks(os.path.join(WORK, name, "positions.json"))
        pts_list = [static_filter.apply(b["positions"], b["calib"])
                    for b in blocks]
        v19p = run(blocks, pts_list, False)
        v20p = run(blocks, pts_list, True)
        v19 = [e for e, _ in v19p]
        v20 = [e for e, _ in v20p]
        v19_ts = [e.t for e in v19]
        extra = [e for e in v20
                 if not any(abs(e.t - t) <= 0.05 for t in v19_ts)]
        lost = [e for e in v19
                if not any(abs(e.t - e2.t) <= 0.05 for e2 in v20)]
        d19, d20, da = dist(v19), dist(v20), dist(extra)
        print(f"\n=== {name} ===")
        print(f"  v19 {len(v19):>4}  high{d19['high']:>4} review{d19['review']:>4}"
              f" low{d19['low']:>4}")
        print(f"  v20 {len(v20):>4}  high{d20['high']:>4} review{d20['review']:>4}"
              f" low{d20['low']:>4}")
        print(f"  增量 {len(extra):>3} 条（high {da['high']} / review {da['review']}"
              f" / low {da['low']}）   丢失 {len(lost)} 条")
        for e in lost:
            si = next(s for e2, s in v19p if e2 is e)
            near = min(extra, key=lambda x: abs(x.t - e.t)) if extra else None
            ds = (f"最近新增 t={near.t:.2f} Δ={near.t - e.t:+.2f}s "
                  f"{near.status}" if near else "无新增")
            print(f"  ⚠ 丢失 t={e.t:8.2f} {e.status:6s} 段{si} conf={e.confidence:.2f}"
                  f" → {ds}")
        for e in extra:
            si = next(s for e2, s in v20p if e2 is e)
            print(f"    + t={e.t:8.2f} 段{si} {e.status:6s} conf={e.confidence:.2f}"
                  f" off={e.cross_off:+.3f} ab={e.above_n} bl={e.below_n}")
        for i, k in enumerate(("high", "review", "low")):
            tot["v19"][i] += d19[k]
            tot["v20"][i] += d20[k]
            tot["add"][i] += da[k]
        report[name] = {"v19": len(v19), "v20": len(v20), "d19": d19, "d20": d20,
                        "add": len(extra), "dadd": da,
                        "lost": [round(e.t, 2) for e in lost],
                        "rows": [{"t": e.t, "status": e.status,
                                  "conf": e.confidence,
                                  "off": e.cross_off,
                                  "above_n": e.above_n, "below_n": e.below_n}
                                 for e in extra]}

    print("\n=== 三场合计 ===")
    print(f"  v19 {sum(tot['v19']):>4}   (high {tot['v19'][0]} / review"
          f" {tot['v19'][1]} / low {tot['v19'][2]})")
    print(f"  v20 {sum(tot['v20']):>4}   (high {tot['v20'][0]} / review"
          f" {tot['v20'][1]} / low {tot['v20'][2]})")
    print(f"  增量 {sum(tot['add']):>3}  (high {tot['add'][0]} / review"
          f" {tot['add'][1]} / low {tot['add'][2]})"
          f"   预期 31 (20/5/6)")

    json.dump(report, open(os.path.join(HERE, "verify_v20_result.json"), "w",
                           encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n→ verify_v20_result.json")


if __name__ == "__main__":
    main()
