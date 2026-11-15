# -*- coding: utf-8 -*-
r"""阶段6：球像素面积 / 轨迹密度 特征的**数据验证**（只读，不改生产代码）。

问题
----
判定器的 conf 只吃 4 个标量：above_n / above_span / below_n / below_span /
cross_off。`positions.json` 每点还带 **像素面积 area**（第 4 列），从未使用；
`ENTITY_AREA_RATIO` 只是「≥0.1×筐面积」的单边下限门，没有上限、不看一致性。

本脚本回答三件事：
  A. 面积/密度特征在 **high 行内部**（命中 659 vs 假绿 204）能否区分 → 查准侧
  B. 在 **review/low 行内部**（降级 127 真球 vs 非进球低置信）能否区分 → 查全侧
     （升档降级不会伤查准——它们还没进 high；只有误升非进球才会）
  C. 与 conf 相比有没有增量（5 折 CV 逻辑回归 AUC）

口径
----
真值 = selection.json（第 5 套用户标注口径），容差 0.2s，1:1 贪心配对，
与 `tools/eval_sel.py` 完全一致；events 先按 deleted.json 剔出局球。
老六场无 positions.json（上游仓只有 events），故只用新六场 + 11-07 对照。

用法
----
    python 诊断/2026-11-14-优化候选/area_feat.py            # 打印报告
    python 诊断/2026-11-14-优化候选/area_feat.py --json out.json
"""
import argparse
import bisect
import json
import math
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "tools"))

import evaluate as ev                                              # noqa: E402
from derive_truth import selection_goals                           # noqa: E402
from eval_sel import sel_match                                     # noqa: E402

WORK = os.path.join(PROJECT_ROOT, "work")
VIDEOS = ["2026-10-17_1", "2026-10-23_1", "2026-10-24_1",
          "2026-10-30_1", "2026-10-31_1", "2026-11-06_1"]
VIDEO_HOLD = "2026-11-07_1"      # 不入评测集，只做样本外对照

# 与 goal_decider 保持一致的几何常量（只读镜像，勿在此调参）
X_MARGIN = 0.2
RIM_Y_RATIO = 0.35
BAND_RATIO = 0.25
ENTITY_AREA_RATIO = 0.10

WIN = 1.0          # 邻域窗半宽（与 NEIGHBOR_SEC 一致）
DENSE_WIN = 0.5    # 密度窗半宽


# ----------------------------------------------------------------- 数据装载
def load_positions(video):
    """→ [(t0, t1, hoop_rect, ts, pts)]，pts=[(t,x,y,area)]（按 t 升序）。

    扁平格式 = 单段（t0=-inf）；多段格式每段自带 calib（hoop_rect 可能不同，
    标定重扫换机位时确实会变，面积归一必须用当段筐）。
    """
    d = json.load(open(os.path.join(WORK, video, "positions.json"),
                       encoding="utf-8"))
    calib = json.load(open(os.path.join(WORK, video, "calibration.json"),
                           encoding="utf-8"))
    segs = []
    if "segments" in d:
        for s in d["segments"]:
            hr = (s.get("calib") or [calib["hoop_rect"]])[0]
            segs.append((float(s.get("t0") or 0.0),
                         float(s["t1"]) if s.get("t1") is not None else math.inf,
                         hr, s["positions"]))
    else:
        segs.append((-math.inf, math.inf, calib["hoop_rect"], d["positions"]))
    out = []
    for t0, t1, hr, rows in segs:
        pts = [(r[0], r[1], r[2], r[3]) for r in rows if len(r) >= 4]
        pts.sort(key=lambda p: p[0])
        out.append((t0, t1, hr, [p[0] for p in pts], pts))
    out.sort(key=lambda s: s[0])
    return out


def load_events(video):
    """events 原始行 + 已剔 deleted/间隙球的下标剔除集。"""
    p = os.path.join(WORK, video, "events.json")
    d = json.load(open(p, encoding="utf-8"))
    rows = d["events"]
    deleted = ev._load_deleted_seconds(p)
    inter = ev.INTERMISSION_GOALS.get(video, [])
    drop = (ev._deleted_exclusions([e["t"] for e in rows], deleted + inter)
            if (deleted or inter) else set())
    return d, rows, drop


def load_selection(video):
    p = os.path.join(WORK, video, "selection.json")
    if not os.path.exists(p):
        return []
    return selection_goals(p)[0]      # 与 eval_sel / derive_truth 同一解析


# ----------------------------------------------------------------- 特征
def _win(seg, t, half):
    """邻域窗内的点（时间裁剪，O(log n)）。"""
    _t0, _t1, _hr, ts, pts = seg
    lo = bisect.bisect_left(ts, t - half)
    hi = bisect.bisect_right(ts, t + half)
    return pts[lo:hi]


def features(seg, e):
    """单事件 → 特征 dict。seg 是该事件时刻所属段。"""
    t = e["t"]
    _t0, _t1, hr, ts, pts = seg
    hx, hy, hw, hh = hr
    rim_y = hy + hh * RIM_Y_RATIO
    band = hh * BAND_RATIO
    x_lo, x_hi = hx - hw * X_MARGIN, hx + hw * (1 + X_MARGIN)
    hoop_area = float(hw * hh)
    min_entity = hoop_area * ENTITY_AREA_RATIO

    win = _win(seg, t, WIN)
    # 与判定器同款分类：横向在筐口范围、且不在筐身条带里
    cls = [(p, "up" if p[2] < rim_y else "down") for p in win
           if x_lo <= p[1] <= x_hi and abs(p[2] - rim_y) >= band]
    up = [p for p, s in cls if s == "up" and t - WIN <= p[0] <= t]
    dn = [p for p, s in cls if s == "down" and t <= p[0] <= t + WIN]

    # 穿越点：t 完全相同的那一点（cross 点本身），退化为最近点
    i = bisect.bisect_left(ts, t)
    cand = [pts[j] for j in (i - 1, i) if 0 <= j < len(pts)]
    cross = min(cand, key=lambda p: abs(p[0] - t)) if cand else None
    a_c = cross[3] if cross else float("nan")
    dt_c = abs(cross[0] - t) if cross else float("nan")

    def med(v):
        v = sorted(v)
        if not v:
            return float("nan")
        n = len(v)
        return v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])

    a_all = [p[3] for p in up + dn]
    a_med = med(a_all)
    mu = sum(a_all) / len(a_all) if a_all else float("nan")
    sd = (math.sqrt(sum((a - mu) ** 2 for a in a_all) / len(a_all))
          if a_all else float("nan"))
    # 相邻点面积比的最大跳变（只看 dt<0.15s 的相邻对：跨遮挡断档不算跳变）
    jump = 0.0
    seq = sorted(up + dn, key=lambda p: p[0])
    for a, b in zip(seq, seq[1:]):
        if b[0] - a[0] <= 0.15 and a[3] > 0 and b[3] > 0:
            jump = max(jump, abs(math.log(b[3] / a[3])))

    dense = _win(seg, t, DENSE_WIN)
    dts = [b[0] - a[0] for a, b in zip(dense, dense[1:])]
    return {
        "t": round(t, 3),
        "a_c": a_c,                                  # 穿越点面积（px²）
        "a_rel": a_c / hoop_area,                    # 占筐矩形面积比
        "a_up": med([p[3] for p in up]),             # 上方窗面积中位
        "a_dn": med([p[3] for p in dn]),             # 下方窗面积中位
        "a_cv": (sd / mu) if mu and mu == mu and mu > 0 else float("nan"),
        "a_jump": jump,                              # 面积最大跳变（ln 比）
        "n_win": len(cls),                           # 筐口邻域点数（1s）
        "n_dense": len(dense),                        # 全画面点数（±0.5s）
        "gap_max": max(dts) if dts else float("nan"),  # 最大采样断档（s）
        "dt_med": med(dts),                          # 采样间隔中位（s）
        "big_frac": (sum(1 for a in a_all if a >= min_entity) / len(a_all)
                     if a_all else float("nan")),
        "dt_cross": round(dt_c, 4),
        "hw": hw, "hh": hh, "hoop_area": hoop_area,
    }


def seg_of(segs, t):
    for s in segs:
        if s[0] <= t < s[1]:
            return s
    return segs[-1]


# ----------------------------------------------------------------- 统计
def auc(pos, neg):
    """Mann-Whitney AUC：pos 越大越阳。并列取 0.5。"""
    pos = [x for x in pos if x == x]
    neg = [x for x in neg if x == x]
    if not pos or not neg:
        return float("nan")
    allv = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    # 秩
    ranks = {}
    i = 0
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1][0] == allv[i][0]:
            j += 1
        r = (i + j) / 2.0 + 1
        ranks[allv[i][0]] = r
        i = j + 1
    s = sum(ranks[v] for v in pos)
    n1, n0 = len(pos), len(neg)
    return (s - n1 * (n1 + 1) / 2.0) / (n1 * n0)


def q(v, p):
    v = sorted(x for x in v if x == x)
    if not v:
        return float("nan")
    k = (len(v) - 1) * p
    f = int(math.floor(k))
    c = min(f + 1, len(v) - 1)
    return v[f] if f == c else v[f] * (c - k) + v[c] * (k - f)


def fmt(x, nd=3):
    if x != x:
        return "  nan"
    return f"{x:.{nd}f}"


# ----------------------------------------------------------------- 主流程
def collect():
    """→ per_video: [{video, rows:[{t,status,label,feat}]}]"""
    out = []
    for v in VIDEOS + [VIDEO_HOLD]:
        segs = load_positions(v)
        _d, rows, drop = load_events(v)
        keep = [(i, e) for i, e in enumerate(rows) if i not in drop]
        rt = [(e["t"], e["status"]) for _i, e in keep]
        goals = load_selection(v)
        used_g, used_r = sel_match(goals, rt) if goals else (set(), set())
        recs = []
        for ri, (i, e) in enumerate(keep):
            st = e["status"]
            if st == "high":
                lab = "hit" if ri in used_r else "false_green"
            else:
                lab = "degraded" if ri in used_r else "neutral_low"
            f = features(seg_of(segs, e["t"]), e)
            recs.append({"t": e["t"], "status": st, "conf": e["confidence"],
                         "label": lab, "above_n": e.get("above_n"),
                         "below_n": e.get("below_n"),
                         "above_span": e.get("above_span"),
                         "below_span": e.get("below_span"),
                         "cross_off": e.get("cross_off"),
                         "review_reason": e.get("review_reason"),
                         "low_reason": e.get("low_reason"),
                         "feat": f})
        out.append({"video": v, "sel_n": len(goals),
                    "matched": len(used_g), "rows": recs,
                    "ver": _d.get("logic_ver")})
    return out


FEATS = ["a_c", "a_rel", "a_up", "a_dn", "a_cv", "a_jump",
         "n_win", "n_dense", "gap_max", "dt_med", "big_frac"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    data = collect()
    ev_ = [d for d in data if d["video"] in VIDEOS]
    hold = [d for d in data if d["video"] == VIDEO_HOLD]

    cnt = {}
    for d in ev_:
        for r in d["rows"]:
            cnt[r["label"]] = cnt.get(r["label"], 0) + 1
    print("=" * 78)
    print("样本（新六场，v23）")
    print("=" * 78)
    print("  " + "  ".join(f"{k}={v}" for k, v in sorted(cnt.items())))
    print(f"  sel 合计 {sum(d['sel_n'] for d in ev_)} / 配上 "
          f"{sum(d['matched'] for d in ev_)}")
    print(f"  logic_ver {[d['ver'] for d in ev_]}")

    def group(lab, src=None):
        src = src or ev_
        return [r for d in src for r in d["rows"] if r["label"] == lab]

    hit = group("hit")
    fg = group("false_green")
    dg = group("degraded")
    nl = group("neutral_low")

    print("\n" + "=" * 78)
    print("A. 查准侧：high 行内部，命中 vs 假绿（面积/密度特征能否分开）")
    print("      AUC>0.5 = 特征越大越像真球；AUC<0.5 = 越大越像假绿（取反可用）")
    print("=" * 78)
    print(f"  {'特征':<10}{'AUC':>7}   {'命中 p25/p50/p75':>26}   "
          f"{'假绿 p25/p50/p75':>26}")
    res_a = {}
    for f in FEATS:
        p = [r["feat"][f] for r in hit]
        n = [r["feat"][f] for r in fg]
        a = auc(p, n)
        res_a[f] = a
        print(f"  {f:<10}{fmt(a):>7}   "
              f"{fmt(q(p,.25))}/{fmt(q(p,.5))}/{fmt(q(p,.75))}".ljust(36)
              + f"{fmt(q(n,.25))}/{fmt(q(n,.5))}/{fmt(q(n,.75))}")
    ac = auc([r["conf"] for r in hit], [r["conf"] for r in fg])
    print(f"  {'conf 对照':<10}{fmt(ac):>7}   （现有分数，基准线）")

    print("\n" + "=" * 78)
    print("B. 查全侧：review/low 行内部，降级(真球) vs 非进球低置信")
    print("    升档降级不伤查准（它们没进 high）；只有误升非进球才伤 → 看 AUC")
    print("=" * 78)
    print(f"  {'特征':<10}{'AUC':>7}   {'降级 p25/p50/p75':>26}   "
          f"{'非进球 p25/p50/p75':>26}")
    res_b = {}
    for f in FEATS:
        p = [r["feat"][f] for r in dg]
        n = [r["feat"][f] for r in nl]
        a = auc(p, n)
        res_b[f] = a
        print(f"  {f:<10}{fmt(a):>7}   "
              f"{fmt(q(p,.25))}/{fmt(q(p,.5))}/{fmt(q(p,.75))}".ljust(36)
              + f"{fmt(q(n,.25))}/{fmt(q(n,.5))}/{fmt(q(n,.75))}")
    print(f"  样本量 降级={len(dg)} 非进球={len(nl)}")

    # 穿越点时间对齐自检（特征是否真落在 cross 点上）
    dts = [r["feat"]["dt_cross"] for d in ev_ for r in d["rows"]]
    print(f"\n  穿越点时刻对齐：max|dt|={max(dts):.6f}s  "
          f"超 0.02s 的 {sum(1 for x in dts if x > 0.02)}/{len(dts)}")

    if hold:
        h = [r for d in hold for r in d["rows"] if r["label"] == "false_green"]
        hh_ = [r for d in hold for r in d["rows"] if r["label"] == "hit"]
        print("\n" + "=" * 78)
        print(f"样本外对照 {VIDEO_HOLD}（不入评测集，只看方向是否一致）")
        print("=" * 78)
        print(f"  命中={len(hh_)} 假绿={len(h)}")
        for f in ("a_rel", "a_cv", "a_jump", "big_frac", "n_dense"):
            a = auc([r["feat"][f] for r in hh_], [r["feat"][f] for r in h])
            print(f"  {f:<10}AUC={fmt(a)}   新六场同特征={fmt(res_a[f])}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"counts": cnt,
                       "auc_precision_side": res_a,
                       "auc_recall_side": res_b,
                       "data": [{"video": d["video"],
                                 "rows": d["rows"]} for d in data]},
                      fh, ensure_ascii=False)
        print(f"\n→ {args.json}")


if __name__ == "__main__":
    main()
