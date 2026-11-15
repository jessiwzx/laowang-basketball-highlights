# -*- coding: utf-8 -*-
"""「只标一次 + 筐桥接」 vs 「人工多段标注」——判定层同口径对照

用户的问题：只标一次（后面不补标定）靠筐自动跟随，效果能不能顶得上人工
手点的多段标注？

两臂（同一份 positions.json、同一套切段、同一份网信号，唯一变量＝几何）：

  P 现状  段 k 用它自己的标定 `calib_k`（用户在第 k 个机位手点的筐口）
  S 只标一次  全片统一用段 1 的标定 `calib_1`，段 k 的球点整体平移
             −bridge_k 搬进段 1 的坐标系：

                 x_cal1 = x_raw − bridge_k   （段 1 内 bridge=0）
                 bridge_k = 筐心(段 k 参考帧) − 筐心(段 1 参考帧)

             —— 两端都是「标定参考帧」这种干净静止帧，检测偏置相减对消，
             这正是 2026-11-07 实测 6 段误差中位 0.05 / 最大 0.12 筐宽的那条。

网信号：两臂都按时间从**现网 sidecar** 取（各段用自己 calib 载入后合并），
重分箱直接复用 `net_motion.net_score` + `goal_decider.HIGH_CONF/MIN_CONF`，
与 `net_evidence_pass` 的 12 行逻辑一致。不写回 sidecar。

未复刻的两处（两臂同等缺失，对比仍公平，绝对值会低于生产）：
  ① `_cooldown_release_pass`（v14 冷却释放二趟，需要为新增行测网）
  ② `net_cluster_pass`（v16 B2 网簇注入，2~12 行/场）

用法：
    python sim_one_calib.py 2026-10-17_1 2026-10-23_1 ...
    python sim_one_calib.py --all
"""
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
UP = r"D:/claude code/视频剪辑"
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
if os.path.join(UP, "tools") not in sys.path:
    sys.path.insert(0, os.path.join(UP, "tools"))

import evaluate as EV                                          # noqa: E402
from bbh import net_motion as NM                               # noqa: E402
from bbh import static_filter                                  # noqa: E402
from bbh.calibration import load_segments_dir                  # noqa: E402
from bbh.goal_decider import (HIGH_CONF, MIN_CONF,             # noqa: E402
                              decide_goals)
from bbh.models import BallPos, Calibration                    # noqa: E402
from dataclasses import replace                                # noqa: E402

WORK = os.path.join(_ROOT, "work")


def rd(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def parse_ts(s):
    v = 0.0
    for p in str(s).strip().split(":"):
        v = v * 60 + float(p)
    return v


def truth_of(name):
    sel = rd(os.path.join(WORK, name, "selection.json"))
    out = []
    for item in sel:
        try:
            out.append(round(parse_ts(item.get("t")
                                      if isinstance(item, dict) else item), 3))
        except (TypeError, ValueError):
            pass
    return sorted(set(out))


def _points(rows, dx=0.0, dy=0.0):
    return [BallPos(t=r[0], x=r[1] + dx, y=r[2] + dy, area=r[3],
                    fi=r[4] if len(r) > 4 else -1) for r in rows]


def _apply_net(evs, cache):
    """与 net_evidence_pass 的后半段同逻辑（网分回写 conf + 非硬门重分箱）。"""
    for e in evs:
        sig = cache.get(NM._k(e.t))
        if not sig:
            continue
        e.net_post = sig["post"]
        e.net_ctrl = sig.get("ctrl")
        e.net_local = sig.get("local")
        e.confidence = round(e.confidence
                             + NM.net_score(sig["post"], sig.get("local")), 2)
        if e.low_reason is not None:
            continue
        if e.review_reason is not None or e.pop_back is not None:
            continue
        e.status = ("high" if e.confidence >= HIGH_CONF
                    else "review" if e.confidence >= MIN_CONF else "low")
    return evs


def _kpi(evs, goals, deleted):
    ts = [e.t for e in evs]
    drop = EV._deleted_exclusions(ts, deleted)
    kept = [e for i, e in enumerate(evs) if i not in drop]
    det = [(e.t, e.status) for e in kept]
    rep = EV.match_report(goals, det)
    high = sum(1 for _t, s in det if s == "high")
    m = EV.auto_metrics(rep, high)
    return {"rows": len(det), "high": high,
            "review": sum(1 for _t, s in det if s == "review"),
            "low": sum(1 for _t, s in det if s == "low"),
            "hit": m["自动档命中"], "fg": m["假绿"],
            "P": m["查准"], "R": m["查全"], "miss": rep["漏检"],
            "review_hit": rep["待确认中"], "low_hit": rep["命中低置信"]}


def run(name):
    work = os.path.join(WORK, name)
    pp = os.path.join(work, "positions.json")
    if not os.path.exists(pp):
        print(f"{name}: 无 positions"); return None
    doc = rd(pp)
    video = doc.get("video") or ""
    blocks = doc.get("segments") or [doc]
    segs = sorted(load_segments_dir(work), key=lambda s: s.t0)
    if len(segs) < 2:
        print(f"{name}: 单段场次，跳过"); return None
    if any(not b.get("hoop_ref") for b in blocks):
        print(f"{name}: 有段缺 hoop_ref，先跑 tools/remap_hoop.py"); return None

    # 网信号：各段用自己 calib 的身份载入，按时间合并（段间不重叠）
    cache = {}
    for s in segs:
        cache.update(NM._load_cache(work, video, s.calib))

    ref1 = blocks[0]["hoop_ref"]
    calib1 = segs[0].calib
    goals = truth_of(name)
    deleted = EV._load_deleted_seconds(os.path.join(work, "events.json")
                                       if os.path.exists(os.path.join(
                                           work, "events.json"))
                                       else pp)

    armP, armS, armS0, armN = [], [], [], []
    print(f"\n=== {name}  {len(segs)} 段  真值 {len(goals)} 球")
    print("  段  窗口(min)  桥接(px)  桥接残差  |  P行  S行  S0行")
    h1 = calib1.hoop_rect
    for i, b in enumerate(blocks):
        seg = min(segs, key=lambda s: abs(s.t0 - float(b.get("t0") or 0)))
        rows = b.get("positions") or []
        rk = b.get("hoop_ref")
        bx = rk[0] - ref1[0]          # 筐心位移（段 k 参考帧 → 段 1 参考帧）
        by = rk[1] - ref1[1]
        # P：段自己的标定 + 原坐标（＝现状）
        pP = _apply_net(decide_goals(static_filter.apply(
            _points(rows), seg.calib), seg.calib), cache)
        # S：全片用段 1 的标定。球点搬进段 1 坐标系（x_cal1 = x_cal_k − 筐心位移），
        #    筐框中心仍取段 1 手点的中心，**尺寸按参考帧检测尺寸等比缩放**
        #    （真实实现里这就是"在新机位重新找到筐"的产物）。
        w_s = h1[2] * rk[2] / ref1[2]
        h_s = h1[3] * rk[3] / ref1[3]
        calib_s = replace(calib1, hoop_rect=(
            round(h1[0] + (h1[2] - w_s) / 2, 1),
            round(h1[1] + (h1[3] - h_s) / 2, 1),
            round(w_s, 1), round(h_s, 1)))
        pS = _apply_net(decide_goals(static_filter.apply(
            _points(rows, -bx, -by), calib_s), calib_s), cache)
        # S0：尺寸也照搬段 1（不缩放）——对照，看尺寸缩放值多少
        pS0 = _apply_net(decide_goals(static_filter.apply(
            _points(rows, -bx, -by), calib1), calib1), cache)
        # N：只标一次但**不靠筐**（不搬点）——说明筐锚点在干实事
        pN = _apply_net(decide_goals(static_filter.apply(
            _points(rows), calib1), calib1), cache)
        armP += pP
        armS += pS
        armS0 += pS0
        armN += pN
        # 桥接残差：预测出来的手点中心 vs 用户实际手点中心
        hk = seg.calib.hoop_rect
        ex = (h1[0] + h1[2] / 2 + bx) - (hk[0] + hk[2] / 2)
        ey = (h1[1] + h1[3] / 2 + by) - (hk[1] + hk[3] / 2)
        print(f"   {i + 1}   {float(b.get('t0') or 0) / 60:7.1f}"
              f"–{(float(b['t1']) / 60 if b.get('t1') else float('nan')):7.1f}"
              f"   dx={-bx:+6.1f} dy={-by:+6.1f}"
              f"   {((ex * ex + ey * ey) ** .5) / hk[2]:4.2f}W"
              f"  | {len(pP):>3}  {len(pS):>3}  {len(pS0):>3}")

    armP.sort(key=lambda e: e.t)
    armS.sort(key=lambda e: e.t)
    armS0.sort(key=lambda e: e.t)
    armN.sort(key=lambda e: e.t)
    kP = _kpi(armP, goals, deleted)
    kS = _kpi(armS, goals, deleted)
    kS0 = _kpi(armS0, goals, deleted)
    kN = _kpi(armN, goals, deleted)
    for tag, k in (("P 人工多段", kP), ("S 只标一次(尺寸缩放)", kS),
                   ("S0 只标一次(尺寸照搬)", kS0),
                   ("N  只标一次(不靠筐)", kN)):
        print(f"  {tag:<20} 行 {k['rows']:>4}  high {k['high']:>4}"
              f" / review {k['review']:>4} / low {k['low']:>4}"
              f" | 命中 {k['hit']:>4} 假绿 {k['fg']:>3} 漏检 {k['miss']:>3}"
              f" | 查准 {k['P'] * 100:5.2f}%  查全 {k['R'] * 100:5.2f}%")
    for tag, k in (("S", kS), ("S0", kS0), ("N", kN)):
        print(f"  Δ{tag}(只标一次 − 人工多段)：查准 {(k['P'] - kP['P']) * 100:+.2f}pct"
              f"  查全 {(k['R'] - kP['R']) * 100:+.2f}pct"
              f"  命中 {k['hit'] - kP['hit']:+d}  假绿 {k['fg'] - kP['fg']:+d}")

    # 逐球档位变化
    def by_t(evs):
        d = {}
        for e in evs:
            k = round(e.t, 2)
            if k not in d or e.confidence > d[k].confidence:
                d[k] = e
        return d
    mp, ms = by_t(armP), by_t(armS)
    common = set(mp) & set(ms)
    flip = sum(1 for t in common if mp[t].status != ms[t].status)
    print(f"  同刻 {len(common)}：档位变 {flip}  仅P {len(set(mp) - set(ms))}"
          f"  仅S {len(set(ms) - set(mp))}")
    return {"name": name, "P": kP, "S": kS, "S0": kS0, "N": kN,
            "goals": len(goals)}


def main(argv):
    names = [a for a in argv if not a.startswith("--")]
    if "--all" in argv or not names:
        names = ["2026-10-17_1", "2026-10-23_1", "2026-10-30_1", "2026-10-31_1"]
    out = [r for r in (run(n) for n in names) if r]
    if len(out) > 1:
        print("\n=== 汇总 ===")
        goals = sum(r["goals"] for r in out)
        for arm in ("P", "S", "S0", "N"):
            hit = sum(r[arm]["hit"] for r in out)
            high = sum(r[arm]["high"] for r in out)
            fg = sum(r[arm]["fg"] for r in out)
            print(f"  {arm:<3} high {high:>4} 命中 {hit:>4} 假绿 {fg:>4}"
                  f"  查准 {hit / max(1, high) * 100:5.2f}%"
                  f"  查全 {hit / max(1, goals) * 100:5.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
