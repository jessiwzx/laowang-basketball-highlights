# -*- coding: utf-8 -*-
r"""判定层新证据：穿筐后轨迹 / 上方弧物理一致性 / 筐口检出空洞 / 尺度序列。

为什么要这个
------------
`诊断/2026-10-17-判定层hardcase/Step1归因.md` 已证明：参数族（HIGH_CONF +
网分两档，3744 组）**七门全过 0 组**。剩下卡死的是「网确实局部在动 +
conf 0.72~0.90」那 33 条——球触网了但没进，与真进球共享几何居中、网动、
高置信三件套。现有 12 个特征里没有任何一个能分开它们，所以要加**新证据
维度**，不是继续调参数。

四个物理假设
------------
H1 穿筐后的轨迹延续性——真进球穿筐后继续往下走一段（球落进网里、再落地）；
   触网没进会骤减速、横向弹开或被网兜住。判据不能用"有没有下方点"（那已经
   在 conf 里了），要用**曲线形状**：最深点、垂直速度、y 最小值（回升）、
   横向漂移。
H2 上方弧的物理一致性——真投篮穿越前是一条平滑陡降的抛物线；假绿多是横穿、
   运球、拾球经过筐口上方，轨迹是折线或长时间徘徊 → 二次拟合残差大、接近角平。
H3 筐口检出空洞——球穿过筐口必然被筐沿/网遮挡 1~3 帧。从旁边飞过的假绿不会
   在筐带里留下空洞。
H4 尺度序列——球朝筐飞近时 area 递增、穿筐后远离而递减；人肤碎块/固定物不
   跟飞行走，面积比异常。

全部特征只用 `positions.json`（[t,x,y,area,fi]）与 calib，**零视频 IO、
零检测重跑**。老六场产物在 `yolo/eval_out/v20_c03_s1`，新场在本仓 `work`。

用法
----
    .venv\Scripts\python 诊断\2026-11-10-判定层新证据\new_evidence.py extract
    .venv\Scripts\python 诊断\2026-11-10-判定层新证据\new_evidence.py analyze

只读；产物写本目录。
"""
import bisect
import json
import math
import os
import sys
from statistics import mean

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
OUT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import evaluate as EV                                              # noqa: E402
import eval_lib as E                                               # noqa: E402

OLD_ROOT = r"D:\claude code\yolo\eval_out\v20_c03_s1"      # 老六场（+09-19）
NEW_ROOT = os.path.join(ROOT, "work")                       # 新场
UP_WORK = r"D:\claude code\视频剪辑\work"                   # 老场 deleted.json

RIM_Y_RATIO = 0.35
BAND_RATIO = 0.25
X_MARGIN = 0.2

# 老六场：truth 源自 HSV 表（异源）→ 可评查全，也可评查准
OLD6 = ["2026-08-22_1", "2026-08-28_1", "2026-08-29_3",
        "2026-09-04_1", "2026-09-11_3", "2026-09-12_1"]
# 查准回归集：truth 源自 selection（⊆ events）→ 只评查准 / 覆盖
NEW6 = list(E.VIDEOS_PREC)


def rd(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def wr(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False)


def find(video):
    """→ (events_path, positions_path) 或 (None, None)。"""
    for root in (OLD_ROOT, NEW_ROOT):
        ev = os.path.join(root, video, "events.json")
        ps = os.path.join(root, video, "positions.json")
        if os.path.exists(ev) and os.path.exists(ps):
            return ev, ps
    return None, None


# ------------------------------------------------------------ 标定（多段）
def _is_multi(ck):
    return (isinstance(ck, list) and ck and isinstance(ck[0], list)
            and len(ck[0]) >= 2 and isinstance(ck[0][0], (int, float))
            and isinstance(ck[0][1], list))


def calib_segments(ev_doc, pos_doc):
    """→ [(t0, (hx,hy,hw,hh))]，按 t0 升序。多段标定按事件时刻取段。"""
    segs = []
    ck = ev_doc.get("calib_key")
    if ck:
        if _is_multi(ck):
            for item in ck:
                try:
                    segs.append((float(item[0]), tuple(item[1][0][:4])))
                except (TypeError, ValueError, IndexError):
                    continue
        else:
            try:
                segs.append((0.0, tuple(ck[0][:4])))
            except (TypeError, ValueError, IndexError):
                pass
    if not segs:
        c = pos_doc.get("calib")
        if c:
            segs.append((0.0, tuple(c[0][:4])))
    return sorted(segs)


def seg_at(segs, t):
    cur = segs[0]
    for s in segs:
        if s[0] <= t:
            cur = s
        else:
            break
    return cur[1]


# ------------------------------------------------------------ 特征
def _win(ts, pts, t0, t1):
    return pts[bisect.bisect_left(ts, t0):bisect.bisect_right(ts, t1)]


def _poly_auc(scores, labels):
    """Mann-Whitney AUC（并列取平均秩）。labels: True=真进球。"""
    n = len(scores)
    if n == 0:
        return 0.5
    order = sorted(range(n), key=lambda i: scores[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    pos = [r for r, y in zip(ranks, labels) if y]
    neg = [r for r, y in zip(ranks, labels) if not y]
    if not pos or not neg:
        return 0.5
    return (sum(pos) - len(pos) * (len(pos) + 1) / 2.0) / (len(pos) * len(neg))


def features_at(ts, pts, t, rect):
    """单个事件（穿越点时刻 t）→ 新特征字典。缺失记 None（不参与该特征统计）。"""
    hx, hy, hw, hh = rect
    rim = hy + hh * RIM_Y_RATIO
    band = hh * BAND_RATIO
    x_lo, x_hi = hx - hw * X_MARGIN, hx + hw * (1 + X_MARGIN)
    cx = hx + hw / 2.0
    f = {}

    win = _win(ts, pts, t - 1.05, t + 1.55)
    above, below, inband = [], [], []
    for p in win:
        if p[1] < x_lo or p[1] > x_hi:
            continue
        if abs(p[2] - rim) < band:
            inband.append(p)
        elif p[2] < rim:
            above.append(p)
        else:
            below.append(p)

    post = [p for p in below if p[0] > t]
    pre_up = [p for p in above if p[0] <= t]

    # H1 穿筐后延续性
    f["post_deep_rel"] = round((max(p[2] for p in post) - rim) / hh, 4) if post else None
    p03 = [p for p in post if p[0] <= t + 0.35]
    if len(p03) >= 2 and p03[-1][0] > p03[0][0]:
        f["post_vy_rel"] = round((p03[-1][2] - p03[0][2])
                                 / (p03[-1][0] - p03[0][0]) / hh, 4)
    else:
        f["post_vy_rel"] = None
    p15 = [p for p in post if t + 0.25 <= p[0] <= t + 1.5]
    f["post_ymin_rel"] = round((min(p[2] for p in p15) - rim) / hh, 4) if p15 else None
    r10 = [p for p in post if p[0] <= t + 1.0]
    f["post_xdrift_rel"] = round(max(abs(p[1] - cx) for p in r10) / hw, 4) if r10 else None
    f["post_n"] = len(post)

    # H2 上方弧物理一致性
    up1 = [p for p in pre_up if p[0] >= t - 1.0]
    f["up_n"] = len(up1)
    if len(up1) >= 4 and up1[-1][0] - up1[0][0] >= 0.2:
        us = [p[0] - t for p in up1]
        ys = [p[2] for p in up1]
        try:
            co = _quad_fit(us, ys)
            resid = [y - _quad_eval(co, u) for u, y in zip(us, ys)]
            f["up_fit_rms_rel"] = round(math.sqrt(mean([r * r for r in resid])) / hh, 4)
        except (ZeroDivisionError, ValueError):
            f["up_fit_rms_rel"] = None
    else:
        f["up_fit_rms_rel"] = None
    if len(up1) >= 3 and up1[-1][0] > up1[0][0]:
        dx = up1[-1][1] - up1[0][1]
        dy = up1[-1][2] - up1[0][2]
        f["up_angle_deg"] = round(math.degrees(math.atan2(abs(dy), abs(dx) + 1e-6)), 2)
        f["up_vy_rel"] = round(dy / (up1[-1][0] - up1[0][0]) / hh, 4)
    else:
        f["up_angle_deg"] = None
        f["up_vy_rel"] = None

    # H3 筐口检出空洞（±0.15s 内落在筐带里的检出数；真穿筐应 0~1）
    f["gap_rim"] = sum(1 for p in inband if abs(p[0] - t) <= 0.15)

    # H4 尺度序列
    pre_a = [p[3] for p in pre_up[-5:]]
    post_a = [p[3] for p in post[:5]]
    f["area_ratio"] = (round(mean(post_a) / mean(pre_a), 4)
                       if pre_a and post_a and mean(pre_a) > 0 else None)
    near = min(win, key=lambda p: abs(p[0] - t)) if win else None
    f["area_rel"] = round(near[3] / (hw * hh), 4) if near and hw * hh > 0 else None
    return f


def _quad_fit(us, ys):
    """最小二乘二次拟合（正规方程，3x3）。返回 (a,b,c)。"""
    n = len(us)
    s = [sum(u ** k for u in us) for k in range(5)]
    t = [sum((u ** k) * y for u, y in zip(us, ys)) for k in range(3)]
    a = [[s[4], s[3], s[2], t[2]],
         [s[3], s[2], s[1], t[1]],
         [s[2], s[1], float(n), t[0]]]
    for c in range(3):                       # 高斯消元
        p = max(range(c, 3), key=lambda r: abs(a[r][c]))
        a[c], a[p] = a[p], a[c]
        if abs(a[c][c]) < 1e-12:
            raise ZeroDivisionError
        for r in range(3):
            if r == c:
                continue
            k = a[r][c] / a[c][c]
            for j in range(c, 4):
                a[r][j] -= k * a[c][j]
    return a[0][3] / a[0][0], a[1][3] / a[1][1], a[2][3] / a[2][2]


def _quad_eval(co, u):
    return co[0] * u * u + co[1] * u + co[2]


# ------------------------------------------------------------ 打标签
def label(goals, events, tol):
    """事件 → 是否命中真值（1:1 最近优先，同 official 口径）。"""
    pairs = sorted((abs(e["t"] - g), ei, gi)
                   for ei, e in enumerate(events)
                   for gi, g in enumerate(goals) if abs(e["t"] - g) <= tol)
    used_e, used_g = set(), set()
    for _d, ei, gi in pairs:
        if ei in used_e or gi in used_g:
            continue
        used_e.add(ei)
        used_g.add(gi)
    return [i in used_e for i in range(len(events))], len(goals) - len(used_g)


def load_points(ps_doc, ev_doc):
    """→ (pts, segs)。兼容两种 positions.json 格式。

    · 新（v20+ 臂）：扁平 `positions` + `calib`，多段标定在 events.calib_key
    · 旧（09-04_1 / 09-11_3）：`segments[]`，每段自带宽窄不同的 calib
    """
    if "positions" in ps_doc:
        return ps_doc["positions"], calib_segments(ev_doc, ps_doc)
    pts, segs = [], []
    for s in ps_doc.get("segments") or []:
        pts.extend(s.get("positions") or [])
        c = s.get("calib")
        try:
            if c:
                segs.append((float(s.get("t0", 0.0)), tuple(c[0][:4])))
        except (TypeError, ValueError, IndexError):
            continue
    pts.sort(key=lambda p: p[0])
    if not segs:
        segs = calib_segments(ev_doc, ps_doc)
    return pts, sorted(segs)


def deleted_flags(video, events):
    """已删球标记。deleted.json 不在评测臂目录，在标注的 work 里。"""
    evp = None
    for root in (UP_WORK, NEW_ROOT, OLD_ROOT):
        p = os.path.join(root, video, "events.json")
        if os.path.exists(p):
            evp = p
            break
    if not evp:
        return [False] * len(events)
    d = EV._load_deleted_seconds(evp)
    inter = EV.INTERMISSION_GOALS.get(video, [])
    if not d and not inter:
        return [False] * len(events)
    drop = EV._deleted_exclusions([e["t"] for e in events], d + inter)
    return [i in drop for i in range(len(events))]


# ------------------------------------------------------------ extract
def extract():
    records = []
    info = {}
    for group, vids in (("old6", OLD6), ("new6", NEW6)):
        for v in vids:
            ev_p, ps_p = find(v)
            truth_p = os.path.join(ROOT, "truth", v + ".json")
            if not (ev_p and ps_p and os.path.exists(truth_p)):
                print("  ✗ %-14s 缺 %s" % (v, "events/positions/truth"))
                continue
            ev_doc = rd(ev_p)
            ps_doc = rd(ps_p)
            truth = rd(truth_p)
            goals = sorted(truth["goals"])
            tol = truth.get("tolerance", 0.2)
            events = ev_doc["events"]
            pts, segs = load_points(ps_doc, ev_doc)
            ts = [p[0] for p in pts]
            if not segs:
                print("  ✗ %-14s 无可用标定" % v)
                continue
            is_goal, missed = label(goals, events, tol)
            is_del = deleted_flags(v, events)

            for i, e in enumerate(events):
                rect = seg_at(segs, e["t"])
                f = features_at(ts, pts, e["t"], rect)
                prev_gap = e["t"] - events[i - 1]["t"] if i > 0 else None
                next_gap = events[i + 1]["t"] - e["t"] if i + 1 < len(events) else None
                lo = bisect.bisect_left([x["t"] for x in events], e["t"] - 60)
                hi = bisect.bisect_right([x["t"] for x in events], e["t"] + 60)
                rec = {
                    "video": v, "group": group, "t": round(e["t"], 3),
                    "status": e["status"], "conf": e.get("confidence"),
                    "is_goal": is_goal[i], "deleted": is_del[i],
                    "prev_gap": round(prev_gap, 3) if prev_gap is not None else None,
                    "next_gap": round(next_gap, 3) if next_gap is not None else None,
                    "density_60": hi - lo - 1,
                    "existing": {k: e.get(k) for k in
                                 ("cross_off", "above_n", "below_n", "above_span",
                                  "below_span", "descent_px", "below_big",
                                  "pop_back", "low_reason", "review_reason",
                                  "net_post", "net_ctrl", "net_local")},
                }
                rec.update(f)
                records.append(rec)
            info[v] = {"group": group, "events": len(events), "goals": len(goals),
                       "missed": missed, "segments": len(segs),
                       "deleted": sum(is_del), "src": os.path.basename(
                           os.path.dirname(os.path.dirname(ev_p)))}
            print("  ✓ %-14s 事件 %3d  真值 %3d  已删 %3d  段 %d"
                  % (v, len(events), len(goals), sum(is_del), len(segs)))
    wr(os.path.join(OUT, "features.json"), {"cases": info, "records": records})
    print("\n记录 %d 条 → features.json" % len(records))
    return 0


# ------------------------------------------------------------ analyze
FEATS = [
    "post_deep_rel", "post_vy_rel", "post_ymin_rel", "post_xdrift_rel",
    "up_fit_rms_rel", "up_angle_deg", "up_vy_rel", "gap_rim",
    "area_ratio", "area_rel", "prev_gap", "next_gap", "density_60",
]


def _single_sweep(xs, ys, higher_danger):
    """单特征单刀：在误伤真进球 ≤3% 前提下最大降假绿。

    higher_danger=True 表示取值越大越像假绿（阈值 ≥cut 判危险）。
    """
    pts = sorted(zip(xs, ys))          # (value, is_goal)
    n_pos = sum(1 for _x, y in pts if y)
    n_neg = len(pts) - n_pos
    if not n_pos or not n_neg:
        return None
    best = {"cut": None, "drop": 0.0, "hurt": 0.0, "thr": 0.0}
    vals = sorted({x for x, _y in pts})
    for cut in vals:
        if higher_danger:
            hurt = sum(1 for x, y in pts if y and x >= cut)
            drop = sum(1 for x, y in pts if not y and x >= cut)
        else:
            hurt = sum(1 for x, y in pts if y and x <= cut)
            drop = sum(1 for x, y in pts if not y and x <= cut)
        hr = hurt / n_pos
        if hr > 0.03:
            continue
        if drop > best["drop"]:
            best = {"cut": cut, "drop": drop / n_neg, "hurt": hr,
                    "thr": 0.03}
    return best


def analyze():
    doc = rd(os.path.join(OUT, "features.json"))
    recs = doc["records"]

    def subset(group=None, only_high=True, drop_deleted=True):
        out = []
        for r in recs:
            if group and r["group"] != group:
                continue
            if only_high and r["status"] != "high":
                continue
            if drop_deleted and r["deleted"]:
                continue
            out.append(r)
        return out

    lines = []
    A = lines.append
    A("# 判定层新证据 · 可分性（只读诊断）")
    A("")
    A("> 样本：high 行（已剔 deleted.json）；标签：0.2s 内认领到 truth。")
    A("> 老六场 truth 源自 HSV 表、新六场源自 selection——**分组看，不混算**。")
    A("")

    rows = []
    for tag, group in (("老六场", "old6"), ("新六场", "new6")):
        sub = subset(group)
        n_goal = sum(1 for r in sub if r["is_goal"])
        n_fp = len(sub) - n_goal
        A("## %s（high %d：真 %d / 假 %d）" % (tag, len(sub), n_goal, n_fp))
        A("")
        A("| 特征 | 覆盖 | AUC | 假绿中位 | 真球中位 | 单刀阈值 | 降假绿 | 误伤 |")
        A("|---|---|---|---|---|---|---|---|")
        for f in FEATS:
            vals = [(r[f], r["is_goal"]) for r in sub if r.get(f) is not None]
            xs = [v for v, _y in vals]
            ys = [y for _v, y in vals]
            if len(xs) < 20:
                continue
            auc = _poly_auc(xs, ys)
            med_fp = [v for v, y in vals if not y]
            med_tp = [v for v, y in vals if y]
            med_fp.sort()
            med_tp.sort()
            higher = auc < 0.5
            best = _single_sweep(xs, ys, higher)
            rows.append({"group": group, "feat": f, "auc": round(auc, 3),
                         "drop": round(best["drop"], 3) if best else 0.0,
                         "hurt": round(best["hurt"], 3) if best else None,
                         "cut": best["cut"] if best else None})
            A("| `%s` | %d | %s | %s | %s | %s | %s | %s |" % (
                f, len(xs), f"{auc:.3f}",
                f"{med_fp[len(med_fp)//2]:.3f}" if med_fp else "—",
                f"{med_tp[len(med_tp)//2]:.3f}" if med_tp else "—",
                f"{best['cut']:.3f}" if best and best['cut'] is not None else "—",
                f"{best['drop']:.1%}" if best else "0.0%",
                f"{best['hurt']:.1%}" if best else "—"))
        A("")

    # ---- 与现有最强特征对比 ----
    A("## 与现有特征对比（老六场，同一批 high 行）")
    A("")
    A("| 特征 | AUC | 单刀降假绿 |")
    A("|---|---|---|")
    sub = subset("old6")
    for f, get in (("net_post", lambda r: r["existing"]["net_post"]),
                   ("conf", lambda r: r["conf"]),
                   ("cross_off_abs", lambda r: abs(r["existing"]["cross_off"])
                    if r["existing"]["cross_off"] is not None else None),
                   ("above_span", lambda r: r["existing"]["above_span"])):
        vals = [(get(r), r["is_goal"]) for r in sub if get(r) is not None]
        if len(vals) < 20:
            continue
        xs = [v for v, _y in vals]
        ys = [y for _v, y in vals]
        auc = _poly_auc(xs, ys)
        best = _single_sweep(xs, ys, auc < 0.5)
        A("| `%s` | %.3f | %s |" % (f, auc,
                                    f"{best['drop']:.1%}" if best else "—"))
    A("")

    wr(os.path.join(OUT, "analysis.json"), {"rows": rows})
    md = "\n".join(lines)
    with open(os.path.join(OUT, "可分性.md"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write(md)
    print(md)
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "extract"
    sys.exit(extract() if cmd == "extract" else analyze())
