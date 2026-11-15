# -*- coding: utf-8 -*-
r"""L0 离线重算器：只改「读数/档位/打分权重」，不改「事件生成」。

能力边界（必须记住）
--------------------
能验：打分函数形状、HIGH_CONF/MIN_CONF 阈值、网分权重与静止阈值、
      侧分跨度门限、位置分档位、基于**事件时刻**的规则（如 prev_gap 降档）。
不能验：CROSS_KILL_OFF、冷却身份判据、几何门槛、实体/下落校验、标定。
        这些会改变「有哪些行」，必须走 L1 判定器重放。报告里要如实标回放层。

为什么可信（阶段 0 归因结论，见 诊断/2026-11-14-优化候选/阶段0归因结论.md）
--------------------------------------------------------------------------
events.json 的 span / cross_off 只存 3 位小数，边界值会丢方向（真实 0.1998
被存成 0.200）。但**存储的 confidence 保留了方向**：geom = round(conf − net, 2)
是精确的，而 geom = 0.3 + 侧分×2 + 位置分，分量全取自离散集合 → 可反解真实
档位，再把混淆值往档位内侧挪 0.0005。这样下游任何阈值比较都天然正确。

另有两个必须复刻的细节，漏了就会假报不一致：
  1. review_reason=="b2" 的注入行 conf **不含网分**（net_motion.net_term 返 0）。
  2. 反解只对「真歧义维度」（存储值距档位界 ≤0.0005）枚举；全枚举会因
     0.14+0.20 与 0.20+0.14 同和造出伪解。

用法
----
    .venv\Scripts\python tools\offline_replay.py --check
        100% 复现闸门：重算后的 conf/status 必须与 events.json 逐条一致。
        不通过就拒绝出任何变体结论（可用 --force 强跑，但结论自带警告）。

    .venv\Scripts\python tools\offline_replay.py --scan
        基线 + 内置变体网格，按「新六场（主判据）/ 老六场（外溢探测器）」出表。

    .venv\Scripts\python tools\offline_replay.py --gap 3.0 --gap-prev high
        A 规则：与上一条 high 行间隔 <3s 的 high 行降一档（只有 L0 能算）。

    .venv\Scripts\python tools\offline_replay.py --scan --json out.json
"""
import argparse
import json
import os
import sys
from itertools import product

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

import eval_lib as E                                              # noqa: E402
import eval_sel as ESEL                                           # noqa: E402
import evaluate as ev                                             # noqa: E402
from bbh.goal_decider import (HIGH_CONF, MIN_CONF, CONT_SPAN_SEC,  # noqa: E402
                              POS_TIERS, SIDE_SHORT)
from bbh.net_motion import (NET_STATIC_TH, NET_SCORE_STATIC,       # noqa: E402
                            NET_SCORE_GLOBAL)

# 老六场 events 冻结在 v20 臂；deleted.json 与 selection 在上游仓 work
DEFAULT_OLD_ARM = r"D:\claude code\yolo\eval_out\v20_c03_s1"
DEFAULT_OLD_DEL = r"D:\claude code\视频剪辑\work"
LOCAL_WORK = os.path.join(ROOT, "work")

TOL = 5e-4          # 三位小数存储的半宽：档位界 ±0.0005 内判为歧义

OLD = list(ESEL.VIDEOS_SEL_OLD)
NEW = list(ESEL.VIDEOS_SEL_NEW)
ALL = OLD + NEW


# ------------------------------------------------------------------ 读入
def _paths(video, old_arm, old_del):
    if video in OLD:
        return (os.path.join(old_arm, video, "events.json"), old_del)
    return (os.path.join(LOCAL_WORK, video, "events.json"), LOCAL_WORK)


def _load_raw(video, old_arm, old_del):
    path, del_root = _paths(video, old_arm, old_del)
    events = E.load_events(path)
    deleted = ev._load_deleted_seconds(os.path.join(del_root, video, "events.json"))
    inter = ev.INTERMISSION_GOALS.get(video, [])
    if deleted or inter:
        drop = ev._deleted_exclusions([e["t"] for e in events], deleted + inter)
        events = [e for i, e in enumerate(events) if i not in drop]
    return events


# ------------------------------------------------------------------ 反解
def _side(n, span, th=CONT_SPAN_SEC, full=0.20, short=None, one=0.05):
    # short 默认跟 BASE 走 —— 核对 v22 及更早产物时 BASE["short"] 被 --short
    # 改成 0.14，朴素值也必须按 0.14 算，否则反解基准就错了
    short = BASE["short"] if short is None else short
    if n >= 2 and span >= th:
        return full
    if n >= 2:
        return short
    if n == 1:
        return one
    return 0.0


def _pos(off, tiers=None):
    a = abs(off)
    for lim, sc in (tiers or POS_TIERS):
        if a <= lim:
            return sc
    return 0.0


def _net_base(e):
    """与 net_motion.net_term 同口径：b2 注入行恒 0。"""
    if e.get("review_reason") == "b2":
        return 0.0
    post = e.get("net_post")
    if post is None:
        return 0.0
    if post < NET_STATIC_TH:
        return NET_SCORE_STATIC
    if e.get("net_local") is False:
        return NET_SCORE_GLOBAL
    return 0.0


def _cands(n, level):
    if n == 0:
        return [0.0]
    if n == 1:
        return [0.05]
    return [BASE["short"], 0.20]


def _amb_span(e, key_n, key_s):
    return e[key_n] >= 2 and abs(e[key_s] - CONT_SPAN_SEC) <= TOL


def _amb_pos(e):
    a = abs(e["cross_off"])
    return any(abs(a - lim) <= TOL for lim, _ in POS_TIERS)


def recover(e):
    """从存储 conf 反解几何分量档位 → (sa, sb, sp, 候选数) 或 None。

    只枚举真歧义维度；其余由存储值锁定（否则同和伪造解）。
    """
    geom = round(e["confidence"] - _net_base(e), 2)
    na = _side(e["above_n"], e["above_span"])
    nb = _side(e["below_n"], e["below_span"])
    np_ = _pos(e["cross_off"])
    ca = _cands(e["above_n"], 2) if _amb_span(e, "above_n", "above_span") else [na]
    cb = _cands(e["below_n"], 2) if _amb_span(e, "below_n", "below_span") else [nb]
    cp = [sc for _l, sc in POS_TIERS] + [0.0] if _amb_pos(e) else [np_]
    hits = [(a, b, c) for a, b, c in product(ca, cb, cp)
            if abs(0.3 + a + b + c - geom) < 1e-9]
    if not hits:
        return None
    hits.sort(key=lambda h: sum(abs(x - y) for x, y in zip(h, (na, nb, np_))))
    sa, sb, sp = hits[0]
    return sa, sb, sp, len(hits)


def resolve(e):
    """→ 去掉存储精度歧义的事件副本（档位内侧微调）；反解失败 → None。"""
    rec = recover(e)
    if rec is None:
        return None
    sa, sb, sp, _n = rec
    out = dict(e)

    def fix_span(n, span, s):
        if n < 2 or abs(span - CONT_SPAN_SEC) > TOL:
            return span
        return CONT_SPAN_SEC + TOL if s >= 0.20 else CONT_SPAN_SEC - TOL

    out["above_span"] = fix_span(e["above_n"], e["above_span"], sa)
    out["below_span"] = fix_span(e["below_n"], e["below_span"], sb)
    a = abs(e["cross_off"])
    for lim, sc in POS_TIERS:
        if abs(a - lim) <= TOL:
            a = lim - TOL if sc == sp else lim + TOL
            break
    out["cross_off"] = a if e["cross_off"] >= 0 else -a
    return out


# ------------------------------------------------------------------ 重算
BASE = {"cont": CONT_SPAN_SEC, "full": 0.20, "short": SIDE_SHORT, "one": 0.05,
        "high_conf": HIGH_CONF, "min_conf": MIN_CONF,
        "net_scale": 1.0, "net_th": NET_STATIC_TH,
        "gap": 0.0, "gap_prev": "any", "gap_src": "raw", "gap_drop": 1}


def var(**kw):
    p = dict(BASE)
    p.update(kw)
    return p


def score(e, p):
    """→ (conf, status)。e 须已 resolve。"""
    g = 0.3 + _side(e["above_n"], e["above_span"], p["cont"],
                    p["full"], p["short"], p["one"]) \
        + _side(e["below_n"], e["below_span"], p["cont"],
                p["full"], p["short"], p["one"]) \
        + _pos(e["cross_off"])
    g = round(g, 2)
    low = e.get("low_reason")
    if low and "证据点太少" in str(low) and g >= p["min_conf"]:
        low = None
    conf = round(g + round(_net_base(e) * p["net_scale"], 2), 2)
    if low is not None:
        return conf, "low"
    if e.get("review_reason") is not None or e.get("pop_back") is not None:
        return conf, "review"
    st = "high" if conf >= p["high_conf"] else \
         "review" if conf >= p["min_conf"] else "low"
    return conf, st


_DROP = {"high": "review", "review": "low", "low": "low"}


def apply_gap(rows, p, raw_gap=None):
    """A 规则：与上一条（high 或 任意）行的间隔 < gap 秒 → 当前 high 降档。

    rows: [(t, status)]，须按 t 升序。gap=0 时原样返回。
    raw_gap: {round(t,3): 与同场原始事件序列上一行的间隔}。给定时用它算间隔
             （复刻历史模拟口径：间隔在**未剔 deleted/间隙球**的原始序列上量），
             否则按 rows 自身顺序算。
    """
    gap = p["gap"]
    if not gap:
        return rows
    out = []
    last_any = last_high = None
    n = p["gap_drop"]
    for t, st in rows:
        if raw_gap is not None:
            g = raw_gap.get(round(t, 3), 1e9)
        else:
            ref = last_high if p["gap_prev"] == "high" else last_any
            g = 1e9 if ref is None else (t - ref)
        cur = st
        if g < gap:
            for _ in range(n):
                cur = _DROP[cur]
        out.append((t, cur))
        last_any = t
        if cur == "high":
            last_high = t
    return out


_RAWGAP_CACHE = {}


def _raw_gaps(video, old_arm, old_del):
    """同场**原始**事件序列（未剔 deleted/间隙球）里，每条与上一行的间隔。"""
    key = (video, old_arm, old_del)
    if key in _RAWGAP_CACHE:
        return _RAWGAP_CACHE[key]
    path, _d = _paths(video, old_arm, old_del)
    raw_all = E.load_events(path)
    out = {}
    for i, e in enumerate(raw_all):
        out[round(e["t"], 3)] = (e["t"] - raw_all[i - 1]["t"]) if i else 1e9
    _RAWGAP_CACHE[key] = out
    return out


def replay(video, p, cache, old_arm=DEFAULT_OLD_ARM, old_del=DEFAULT_OLD_DEL):
    """→ (rows[(t, status)], 反解失败条数)"""
    key = (video, old_arm, old_del)
    if key not in cache:
        bad = 0
        rows = []
        for e in _load_raw(video, old_arm, old_del):
            ne = resolve(e)
            if ne is None:
                bad += 1
                ne = e
            rows.append((e["t"], ne, e))
        cache[key] = (rows, bad)
    rows, bad = cache[key]
    out = []
    for t, ne, _e in rows:
        _c, st = score(ne, p)
        out.append((t, st))
    rg = _raw_gaps(video, old_arm, old_del) if (p["gap"] and
                                                p["gap_src"] == "raw") else None
    return apply_gap(out, p, rg), bad


# ------------------------------------------------------------------ 闸门
def _short_for(v, old_arm, old_del, override=None):
    """→ 该场产物产出时用的短档值（按 events.json 自带 logic_ver 推断）。

    v23（2026-11-14）起 0.17，更早 0.14。逐场推断而不是全局一个值 ——
    老六场产物冻结在 v20/v22，永远只能按 0.14 复现（无视频无法重跑）。
    """
    if override is not None:
        return override
    path, _d = _paths(v, old_arm, old_del)
    try:
        ver = json.load(open(path, encoding="utf-8")).get("logic_ver") or 0
    except (OSError, ValueError):
        ver = 0
    return SIDE_SHORT if ver >= 23 else 0.14


def check(videos=ALL, old_arm=DEFAULT_OLD_ARM, old_del=DEFAULT_OLD_DEL,
          short_override=None):
    """100% 复现闸门：重算 conf/status 必须逐条等于 events.json。"""
    cache = {}
    tot = bad_c = bad_s = unrec = multi = 0
    per = {}
    for v in videos:
        BASE["short"] = _short_for(v, old_arm, old_del, short_override)
        rows, _b = replay(v, var(), cache, old_arm, old_del)
        raw = [e for _t, _ne, e in cache[(v, old_arm, old_del)][0]]
        c_bad = s_bad = 0
        for (t, st), e in zip(rows, raw):
            tot += 1
            conf, _ = score(resolve(e) or e, var())
            if abs(conf - e["confidence"]) > 1e-9:
                bad_c += 1
                c_bad += 1
            if st != e["status"]:
                bad_s += 1
                s_bad += 1
        for _t, _ne, e in cache[(v, old_arm, old_del)][0]:
            rec = recover(e)
            if rec is None:
                unrec += 1
            elif rec[3] > 1:
                multi += 1
        per[v] = {"rows": len(raw), "conf_bad": c_bad, "status_bad": s_bad}
    return {"rows": tot, "conf_bad": bad_c, "status_bad": bad_s,
            "unrecoverable": unrec, "multi": multi, "per_video": per,
            "ok": bad_c == 0 and bad_s == 0 and unrec == 0}


# ------------------------------------------------------------------ 指标
def measure(p, videos=ALL, sel_roots=None,
            old_arm=DEFAULT_OLD_ARM, old_del=DEFAULT_OLD_DEL):
    """→ ({video: sel_one 指标}，反解失败总数)"""
    cache = {}
    per = {}
    bad = 0
    for v in videos:
        rows, b = replay(v, p, cache, old_arm, old_del)
        bad += b
        goals, _src = ESEL.load_selection(v, sel_roots)
        if not goals:
            continue
        per[v] = ESEL.sel_one(goals, rows, ESEL.SEL_TOL)
    return per, bad


def agg(per, vids):
    return ESEL.sel_agg(per, vids)


def fmt_row(tag, per):
    """一行四组：新六（主判据）/ 老六（外溢）/ 合计。"""
    def cell(vids):
        a = agg(per, vids)
        return (a["high_total"], a["auto_hit"], a["false_green"],
                a["precision"], a["auto_cover"], a["degraded"], a["missing"])
    n = cell(NEW)
    o = cell(OLD)
    t = cell(ALL)
    return (f"{tag:<22}"
            f"{t[1]:>5}{t[2]:>5}{t[3]:>8.1%}{t[4]:>8.1%}"
            f"{n[1]:>7}{n[2]:>5}{n[3]:>8.1%}{n[4]:>8.1%}"
            f"{o[1]:>7}{o[2]:>5}{o[3]:>8.1%}{o[4]:>8.1%}")


HDR = (f"{'变体':<22}{'命中':>5}{'假绿':>5}{'查准':>8}{'覆盖':>8}"
       f"{'新命中':>7}{'新假绿':>5}{'新查准':>8}{'新覆盖':>8}"
       f"{'老命中':>7}{'老假绿':>5}{'老查准':>8}{'老覆盖':>8}")

VARIANTS = [
    ("基线", var()),
    ("span 0.20→0.10", var(cont=0.10)),
    ("span 0.20→0.05", var(cont=0.05)),
    ("span 0.20→0.00", var(cont=0.00)),
    ("短档回退 0.14", var(short=0.14)),
    ("网分 ×0.5", var(net_scale=0.5)),
    ("网分 ×0", var(net_scale=0.0)),
    ("span0.10 + 网分×0.5", var(cont=0.10, net_scale=0.5)),
    ("span0.10 + 短档0.14", var(cont=0.10, short=0.14)),
    ("门槛 0.60→0.65", var(high_conf=0.65)),
    ("门槛 0.60→0.55", var(high_conf=0.55)),
    ("A: gap<3s(prev-any)", var(gap=3.0, gap_prev="any")),
    ("A: gap<3s(prev-high)", var(gap=3.0, gap_prev="high")),
    ("A: gap<5s(prev-high)", var(gap=5.0, gap_prev="high")),
]


def measure_official(p, videos=ALL, with_extra=False,
                     old_arm=DEFAULT_OLD_ARM, old_del=DEFAULT_OLD_DEL):
    """官方口径（HSV truth）：唯一能算查全的尺子。真值缺场自动跳过。"""
    cache = {}
    per = {}
    for v in videos:
        rows, _b = replay(v, p, cache, old_arm, old_del)
        goals, _src = E.load_truth(v, with_extra=with_extra)
        if not goals:
            continue
        per[v] = E.official_one(goals, rows, 0.2)
    return per


# ------------------------------------------------------------------ 验收
def acceptance(videos=ALL, sel_roots=None,
               old_arm=DEFAULT_OLD_ARM, old_del=DEFAULT_OLD_DEL,
               short_override=None):
    """三条验收（缺一不算过）。→ [(名称, 通过, 说明)]

    1 复现 100%：重算 conf/status 与 events.json 逐条一致。
    2 用户口径基线：新六场必须落在 786/830/659/171（真值/自动档/命中/假绿）。
    3 官方口径 A 规则：无补丁老六场 63.33% → 66.24%，假绿 370 → 318。
      （这条唯一能证明工具算的是真东西；口径与 2026-11-11 那次模拟一致：
        间隔量在**未剔 deleted** 的原始事件序列上。）
    """
    out = []
    ck = check(videos, old_arm, old_del, short_override)
    out.append(("1 复现 100%", ck["ok"],
                f"{ck['rows']} 条 · conf 差 {ck['conf_bad']} · "
                f"status 差 {ck['status_bad']} · 反解失败 {ck['unrecoverable']}"))

    # check() 逐场改过 BASE["short"]，这里先复位成当前版本口径
    BASE["short"] = SIDE_SHORT if short_override is None else short_override
    per, _bad = measure(var(), NEW, sel_roots, old_arm, old_del)
    a = agg(per, NEW)
    got = (a["high_total"], a["auto_hit"], a["false_green"], a["missing"])
    ok2 = got == (830, 659, 171, 0)
    out.append(("2 用户口径新六基线（v24 面积硬门）", ok2,
                f"自动档/命中/假绿/无行 = {got[0]}/{got[1]}/{got[2]}/{got[3]} "
                f"（应 830/659/171/0）查准 {a['precision']:.2%} "
                f"覆盖 {a['auto_cover']:.2%}"))

    # 老六场产物冻结在短档 0.14 的年代（无视频无法重跑），历史对照必须按
    # 0.14 复现，否则量的是「新代码算旧产物」，不是当初那次模拟
    keep = BASE["short"]
    BASE["short"] = 0.14
    try:
        b = E.official_agg(measure_official(var(), OLD, False, old_arm, old_del), OLD)
        r = E.official_agg(measure_official(var(gap=3.0), OLD, False,
                                            old_arm, old_del), OLD)
    finally:
        BASE["short"] = keep
    ok3 = (abs(b["precision"] - 0.6333) < 5e-4
           and abs(r["precision"] - 0.6624) < 5e-4
           and b["false_green"] == 370 and r["false_green"] == 318)
    out.append(("3 A 规则对照（官方·无补丁老六）", ok3,
                f"{b['precision']:.2%} → {r['precision']:.2%}"
                f"（应 63.33% → 66.24%），假绿 {b['false_green']} → "
                f"{r['false_green']}（应 370 → 318），命中 "
                f"{b['auto_hit']} → {r['auto_hit']}"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只跑复现闸门")
    ap.add_argument("--accept", action="store_true",
                    help="跑三条验收（复现 / 用户口径基线 / A 规则对照）")
    ap.add_argument("--scan", action="store_true", help="基线 + 变体网格")
    ap.add_argument("--gap", type=float, default=0.0,
                    help="A 规则间隔秒数（0=关）")
    ap.add_argument("--gap-prev", choices=["any", "high"], default="high")
    ap.add_argument("--videos", nargs="*", default=None)
    ap.add_argument("--old-arm", default=DEFAULT_OLD_ARM)
    ap.add_argument("--old-del", default=DEFAULT_OLD_DEL)
    ap.add_argument("--sel-root", action="append", default=[],
                    help="selection.json 根目录（老六场在上游 work）")
    ap.add_argument("--json", default=None)
    ap.add_argument("--force", action="store_true",
                    help="复现不过也强跑（结论会标警告）")
    ap.add_argument("--short", type=float, default=None,
                    help="侧分短档覆盖（默认取代码值；核对 v22 及更早产物、"
                         "或做回退验证时须显式给 0.14）")
    args = ap.parse_args()

    if args.short is not None:
        BASE["short"] = args.short

    vids = args.videos or ALL
    sel_roots = [os.path.abspath(p) for p in args.sel_root] or [DEFAULT_OLD_DEL]

    print("=" * 96)
    ck = check(vids, args.old_arm, args.old_del, args.short)
    print(f"复现闸门：{ck['rows']} 条 · conf 不一致 {ck['conf_bad']} · "
          f"status 不一致 {ck['status_bad']} · 反解失败 {ck['unrecoverable']} · "
          f"多解 {ck['multi']} → {'通过 ✓' if ck['ok'] else '未通过 ✗'}")
    off = {v: d for v, d in ck["per_video"].items()
           if d["conf_bad"] or d["status_bad"]}
    for v, d in off.items():
        print(f"    {v}: {d['rows']} 条中 conf 差 {d['conf_bad']} / "
              f"status 差 {d['status_bad']}")
    if not ck["ok"] and not args.force:
        print("闸门未过 → 拒绝出结论。修好或加 --force（结论须标警告）。")
        return 1

    if args.accept:
        print("-" * 96)
        print("验收三条")
        ok_all = True
        for name, ok, note in acceptance(vids, sel_roots,
                                         args.old_arm, args.old_del):
            print(f"  {'✓' if ok else '✗'} {name:<28}{note}")
            ok_all &= ok
        print(f"→ {'全部通过' if ok_all else '有未通过项，结论不可用'}")
        return 0 if ok_all else 1

    if args.check or not (args.scan or args.gap):
        return 0 if ck["ok"] else 1

    print("-" * 96)
    p0 = var(gap=args.gap, gap_prev=args.gap_prev)
    per0, bad0 = measure(p0, vids, sel_roots, args.old_arm, args.old_del)
    print(fmt_row("基线" if not args.gap else f"A规则 gap<{args.gap}s"
                  f"({args.gap_prev})", per0))
    if bad0:
        print(f"!! {bad0} 条反解失败，已回退存储值参与打分（比例极低可忽略，"
              f"但需在报告中标注）")

    out = {"check": ck}
    if args.scan:
        print("-" * 96)
        print(HDR)
        print("-" * len(HDR))
        for tag, p in VARIANTS:
            per, _bad = measure(p, vids, sel_roots, args.old_arm, args.old_del)
            print(fmt_row(tag, per))
            out[tag] = {k: ESEL.sel_agg(per, v) for k, v in
                        (("all", ALL), ("new", NEW), ("old", OLD))}
        print("-" * len(HDR))
        print("注：新六场=主判据（判是否采纳）；老六场=外溢探测器"
              "（结构改动应完全不变，变了就是范围失控）。")

    if args.json:
        out["base"] = {k: v for k, v in per0.items()}
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print(f"\n明细 → {args.json}")
    return 0 if ck["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
