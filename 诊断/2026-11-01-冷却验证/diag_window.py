"""单点归因：打印某场某时间窗内的原始检出点 + v19/v20 判定行。

用法: python diag_window.py 2026-09-12_1 6015 6032
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


def load_blocks(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    raw = d.get("segments") or [d]
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


def main():
    name, t0, t1 = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
    blocks = load_blocks(os.path.join(WORK, name, "positions.json"))
    for si, b in enumerate(blocks):
        pts = static_filter.apply(b["positions"], b["calib"])
        hx, hy, hw, hh = b["calib"].hoop_rect
        rim_y = hy + hh * gd.RIM_Y_RATIO
        band = hh * gd.BAND_RATIO
        x_lo, x_hi = hx - hw * gd.X_MARGIN, hx + hw * (1 + gd.X_MARGIN)
        cx = hx + hw / 2
        win = [p for p in pts if t0 <= p.t <= t1]
        if not win:
            continue
        print(f"\n=== {name} 段{si}  筐({hx},{hy},{hw},{hh}) rim_y={rim_y:.1f}"
              f" band={band:.1f} x[{x_lo:.1f},{x_hi:.1f}] cx={cx:.1f} ===")
        for p in win:
            if not (x_lo <= p.x <= x_hi):
                side = "side" if abs(p.x - cx) > 1.3 * hw else "out "
            elif abs(p.y - rim_y) < band:
                side = "band"
            else:
                side = "up  " if p.y < rim_y else "down"
            print(f"  t={p.t:9.3f} x={p.x:6.1f} y={p.y:6.1f}"
                  f" a={p.area:6.1f} {side}")
        for enable in (False, True):
            gd.NEWBALL_ENABLE = enable
            evs = gd.decide_goals(pts, b["calib"])
            sel = [e for e in evs if t0 - 6 <= e.t <= t1 + 6]
            tag = "v20" if enable else "v19"
            print(f"  --- {tag} 事件 ---")
            for e in sel:
                print(f"    t={e.t:9.2f} {e.status:6s} conf={e.confidence:.2f}"
                      f" off={e.cross_off:+.3f} ab={e.above_n} bl={e.below_n}"
                      f" low={e.low_reason or ''} rv={e.review_reason or ''}")


if __name__ == "__main__":
    main()
