# -*- coding: utf-8 -*-
r"""L0 离线重算：只改「档位/打分」不改「事件生成」，用 events.json 里已有的
评分自变量重算 confidence/status，再用 selection 当真值算用户口径指标。

用途：在不重跑检测的前提下，扫描「打分函数形状 / 阈值 / 网分权重」这类
改动的净收益。结构性改动（CROSS_KILL_OFF、冷却、几何门槛）不在本脚本
能力范围内，必须走 L1 判定器重放。

先跑复现校验：用当前常量重算，必须与文件里的 confidence/status 完全一致，
否则任何变体结论都不可信。
"""
import json
import os
import sys

ROOT = r"D:\claude code\视频剪辑YOLO"
ARM = r"D:\claude code\yolo\eval_out\v20_c03_s1"
UP = r"D:\claude code\视频剪辑\work"
LOCAL = os.path.join(ROOT, "work")
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)

import eval_lib as E                                              # noqa: E402
import eval_sel as ESEL                                           # noqa: E402
import evaluate as ev                                             # noqa: E402
from bbh.goal_decider import (HIGH_CONF, MIN_CONF, CONT_SPAN_SEC,   # noqa: E402
                              POS_TIERS)
from bbh.net_motion import net_score as _net_score                # noqa: E402

OLD = ["2026-08-22_1", "2026-08-28_1", "2026-08-29_3",
       "2026-09-04_1", "2026-09-11_3", "2026-09-12_1"]
NEW = ["2026-10-17_1", "2026-10-23_1", "2026-10-24_1",
       "2026-10-30_1", "2026-10-31_1", "2026-11-06_1"]
VIDEOS = OLD + NEW


def events_path(v):
    root = ARM if v in OLD else LOCAL
    return os.path.join(root, v, "events.json")


def del_dir(v):
    root = UP if v in OLD else LOCAL
    return os.path.join(root, v)


def load_kept(v):
    """读 events 并按 deleted / 间隙球剔除（与 eval_lib.load_rows 同口径）。"""
    events = E.load_events(events_path(v))
    d = del_dir(v)
    deleted = ev._load_deleted_seconds(os.path.join(d, "events.json"))
    inter = ev.INTERMISSION_GOALS.get(v, [])
    if deleted or inter:
        drop = ev._deleted_exclusions([e["t"] for e in events], deleted + inter)
        events = [e for i, e in enumerate(events) if i not in drop]
    return events


# ------------------------------------------------------------------ 打分
def side_score(n, span, cont, full=0.20, short=0.14, one=0.05):
    if n >= 2 and span >= cont:
        return full
    if n >= 2:
        return short
    if n == 1:
        return one
    return 0.0


def pos_score(off, tiers=None):
    a = abs(off)
    for lim, sc in (tiers or POS_TIERS):
        if a <= lim:
            return sc
    return 0.0


def geom_conf(e, p):
    c = 0.3
    c += side_score(e["above_n"], e["above_span"], p["cont"],
                    p["full"], p["short"], p["one"])
    c += side_score(e["below_n"], e["below_span"], p["cont"],
                    p["full"], p["short"], p["one"])
    c += pos_score(e["cross_off"])
    return round(c, 2)


def net_of(e, p):
    """参数化网分：p["net_scale"] 缩放，p["net_th"] 改静止阈值。"""
    post, local = e.get("net_post"), e.get("net_local")
    if post is None:
        return 0.0
    if post < p["net_th"]:
        base = -0.30
    elif local is False:
        base = -0.26
    else:
        base = 0.0
    return round(base * p["net_scale"], 2)


def rescore(events, p):
    """→ [(t, status, conf)]，档位逻辑与 net_motion 重分箱一致。"""
    out = []
    for e in events:
        g = geom_conf(e, p)
        low = e.get("low_reason")
        # "证据点太少" 这条依赖 geom，重算后可能不再成立
        if low and "证据点太少" in str(low) and g >= p["min_conf"]:
            low = None
        conf = round(g + net_of(e, p), 2)
        if low is not None:
            st = "low"
        elif e.get("review_reason") is not None or e.get("pop_back") is not None:
            st = "review"
        else:
            st = ("high" if conf >= p["high_conf"]
                  else "review" if conf >= p["min_conf"] else "low")
        out.append((e["t"], st, conf, low))
    return out


# ------------------------------------------------------------------ 指标
def measure(p):
    per = {}
    for v in VIDEOS:
        events = load_kept(v)
        rows = [(t, st) for t, st, _c, _l in rescore(events, p)]
        goals, _src = ESEL.load_selection(v)
        if not goals:
            continue
        per[v] = ESEL.sel_one(goals, rows, ESEL.SEL_TOL)
    return per


def agg(per, vids):
    return ESEL.sel_agg(per, vids)


def line(tag, per):
    o, n, a = agg(per, OLD), agg(per, NEW), agg(per, VIDEOS)
    return (f"{tag:<18}{o['auto_hit']:>5}{o['false_green']:>5}{o['degraded']:>5}"
            f"{o['precision']:>8.1%}{n['auto_hit']:>7}{n['false_green']:>5}"
            f"{n['degraded']:>5}{n['precision']:>8.1%}"
            f"{a['auto_hit']:>7}{a['false_green']:>5}{a['degraded']:>5}"
            f"{a['precision']:>8.1%}{a['auto_cover']:>8.1%}")


BASE = {"cont": CONT_SPAN_SEC, "full": 0.20, "short": 0.14, "one": 0.05,
        "high_conf": HIGH_CONF, "min_conf": MIN_CONF,
        "net_scale": 1.0, "net_th": 5.0}


def var(**kw):
    p = dict(BASE)
    p.update(kw)
    return p


VARIANTS = [
    ("基线", var()),
    ("span 0.20→0.10", var(cont=0.10)),
    ("span 0.20→0.05", var(cont=0.05)),
    ("span 0.20→0.00", var(cont=0.00)),
    ("短档 0.14→0.17", var(short=0.17)),
    ("网分 ×0.5", var(net_scale=0.5)),
    ("网分 ×0", var(net_scale=0.0)),
    ("span0.10 + 网分×0.5", var(cont=0.10, net_scale=0.5)),
    ("span0.10 + 短档0.17", var(cont=0.10, short=0.17)),
    ("门槛 0.60→0.65", var(high_conf=0.65)),
    ("门槛 0.60→0.55", var(high_conf=0.55)),
]


def main():
    # ---- 1 复现校验 ----
    bad_c = bad_s = tot = 0
    for v in VIDEOS:
        events = load_kept(v)
        got = rescore(events, BASE)
        for e, (t, st, conf, _low) in zip(events, got):
            tot += 1
            if abs(e["confidence"] - conf) > 0.011:
                bad_c += 1
            if e["status"] != st:
                bad_s += 1
    print(f"复现校验：{tot} 条，conf 不一致 {bad_c}，status 不一致 {bad_s}")
    if bad_c or bad_s:
        print("!! 复现失败，变体结论不可用 !!")
        return

    # ---- 2 变体扫描 ----
    hdr = (f"{'变体':<18}{'老命中':>5}{'老假绿':>5}{'老降级':>5}{'老查准':>8}"
           f"{'新命中':>7}{'新假绿':>5}{'新降级':>5}{'新查准':>8}"
           f"{'命中':>7}{'假绿':>5}{'降级':>5}{'查准':>8}{'覆盖':>8}")
    print(hdr)
    print("-" * len(hdr))
    rows_out = {}
    for tag, p in VARIANTS:
        per = measure(p)
        rows_out[tag] = agg(per, VIDEOS)
        print(line(tag, per))

    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "l0_scan.json"), "w", encoding="utf-8") as f:
        json.dump(rows_out, f, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
