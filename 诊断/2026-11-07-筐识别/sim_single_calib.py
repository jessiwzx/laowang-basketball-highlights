# -*- coding: utf-8 -*-
"""留出验证：把多段标定场次当「只标一次」跑，看锚点能不能顶替人工补段。

做法（不改任何项目文件，只读 positions.json + 标定）：
  方案 X（现状） 每段各用各的标定 + ORB → 判定
  方案 Y（只标一次 + 锚点）全片统一用**第一段**的标定，球点按筐的绝对观测搬过去：
                            x_cal = (x_cal_orig + shift_段) − Δ_hoop(t)
                            Δ_hoop(t) = 筐心(t) − 筐心(段1参考帧)
  对照 X 与 Y 的事件行：t 对齐后比 status。越接近说明「不用补段」越成立。

前提：positions.json 里有 hoop / hoop_ref（先跑 tools/remap_hoop.py）。
"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bbh import hoop_track, static_filter            # noqa: E402
from bbh.calibration import load_segments_dir        # noqa: E402
from bbh.goal_decider import decide_goals            # noqa: E402
from bbh.models import BallPos                       # noqa: E402
from bbh.motion import interp_shift                  # noqa: E402

WORK = os.path.join(_ROOT, "work")


def _blocks(doc):
    segs = doc.get("segments")
    return list(segs) if segs is not None else [doc]


def _dist(evs):
    d = {"high": 0, "review": 0, "low": 0}
    for e in evs:
        d[e.status] = d.get(e.status, 0) + 1
    return d


def run(name):
    work = os.path.join(WORK, name)
    pp = os.path.join(work, "positions.json")
    if not os.path.exists(pp):
        print(f"{name}: 无 positions"); return
    doc = json.load(open(pp, encoding="utf-8"))
    blocks = _blocks(doc)
    segs = load_segments_dir(work)
    if len(segs) < 2:
        print(f"{name}: 单段场次，无需验证"); return
    need = [b for b in blocks if not (b.get("hoop") and b.get("hoop_ref"))]
    if need:
        print(f"{name}: {len(need)} 个段缺 hoop/hoop_ref，先跑 tools/remap_hoop.py")
        return

    ref1 = blocks[0]["hoop_ref"]
    base_shift = 0.0        # 段 1 的参考帧就是标定帧，ref_center 已含其偏置

    # ---- 方案 X：每段自己的标定（现状） ----
    x_events = []
    for i, b in enumerate(blocks):
        seg = min(segs, key=lambda s: abs(s.t0 - float(b.get("t0") or 0)))
        pts = [BallPos(t=r[0], x=r[1], y=r[2], area=r[3],
                       fi=r[4] if len(r) > 4 else -1)
               for r in (b.get("positions") or [])]
        p2 = static_filter.apply(pts, seg.calib)
        x_events += decide_goals(p2, seg.calib)

    # ---- 方案 Y：全片用段 1 的标定 + 筐绝对锚点搬点 ----
    calib1 = segs[0].calib
    y_events = []
    for i, b in enumerate(blocks):
        sh = [tuple(s) for s in (b.get("shift") or [])]
        hoop = b.get("hoop") or []
        corr, st = hoop_track.build_correction(hoop, sh, calib1.hoop_rect, ref1)
        if not corr:
            print(f"  段{i+1} 校正项为空（{st}），跳过")
            continue
        pts = []
        for r in (b.get("positions") or []):
            t = r[0]
            sx, sy = interp_shift(sh, t)
            cx, cy = interp_shift(corr, t)
            pts.append(BallPos(t=t, x=r[1] + sx - cx, y=r[2] + sy - cy,
                               area=r[3], fi=r[4] if len(r) > 4 else -1))
        p2 = static_filter.apply(pts, calib1)
        y_events += decide_goals(p2, calib1)

    # ---- 对照 ----
    xm = {round(e.t, 2): e for e in x_events}
    ym = {round(e.t, 2): e for e in y_events}
    ts = sorted(set(xm) | set(ym))
    same_t = sorted(set(xm) & set(ym))
    same = [(t, xm[t].status, ym[t].status) for t in same_t
            if xm[t].status == ym[t].status]
    diff = [(t, xm[t].status, ym[t].status) for t in same_t
            if xm[t].status != ym[t].status]
    only_x = sorted(set(xm) - set(ym))
    only_y = sorted(set(ym) - set(xm))
    print(f"\n=== {name}  {len(blocks)} 段 → 统一用段 1 标定 ===")
    print(f"  X 现状 {len(x_events)} 行 {_dist(x_events)}")
    print(f"  Y 锚点 {len(y_events)} 行 {_dist(y_events)}")
    print(f"  同刻 {len(same_t)}：status 相同 {len(same)}"
          f"（{len(same) / max(1, len(same_t)) * 100:.1f}%）  不同 {len(diff)}")
    print(f"  仅 X {len(only_x)}  仅 Y {len(only_y)}")
    if diff[:10]:
        for t, a, b in diff[:10]:
            print(f"    t={t:8.2f}  X={a} → Y={b}")
    # 高置信层面的一致性（KPI 只看 high）
    xh = {round(e.t, 2) for e in x_events if e.status == "high"}
    yh = {round(e.t, 2) for e in y_events if e.status == "high"}
    print(f"  自动档 high：X {len(xh)}  Y {len(yh)}  交集 {len(xh & yh)}"
          f"  仅X {len(xh - yh)}  仅Y {len(yh - xh)}")


def main(argv):
    names = argv or sorted(
        os.path.basename(os.path.dirname(p)) for p in
        __import__("glob").glob(os.path.join(WORK, "*", "positions.json")))
    for n in names:
        run(n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
