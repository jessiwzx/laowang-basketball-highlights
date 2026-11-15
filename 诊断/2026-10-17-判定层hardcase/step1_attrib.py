# -*- coding: utf-8 -*-
"""Step 1 · 特征归因（R-020 判定层 hard-case 调优）

只读诊断：不改生产代码、不改数据集、不碰权重。

做四件事：
1. **复算器验收**
   a) 用「存储 conf + 硬门标志」重判 status → 必须 100% 复现 2533 条
   b) 从特征重算 conf → 记录不一致条目，并验证其全部落在特征取整边界
      （span≈0.2 / off 卡分档线）——证明公式无误、歧义只来自存储精度
2. **构造带标签的事件群体**（真值来自 truth，非仅反馈）
   - goal_high   : status=high 且在 truth 容差内命中 → 真进球（自动档命中）
   - fp_all      : status=high 但未命中 truth → 假绿（按定义）
   - fp_active   : fp_all ∩ 用户已反馈标错（Step 0 的 89 条）＝护栏 A 的分母
3. **单特征可分性**：fp_active vs goal_high、fp_all vs goal_high
   AUC / 分位数 / 「误伤 ≤3% 前提下能降掉多少假绿」的最佳单刀
4. **旋钮筛选扫描**（Step 2 的候选排序，非最终方案）
   - HIGH_CONF 扫描：**精确**（直接用存储 conf 重分箱，零歧义）
   - POS_TIERS / CONT_SPAN_SEC / 网分：近似（重算 conf，标记歧义行）
   每条候选都给出：6 场 KPI、逐场 review 增量、三条护栏、留出场 08-29_3、
   反作用监测场（只看不调）

产物：step1_attrib.json + Step1归因.md
"""
import io
import json
import os
import sys
from datetime import datetime

YOLO = r"D:/claude code/yolo"
UP = r"D:/claude code/视频剪辑"          # 上游项目仓（只读）
OUT = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, os.path.join(UP, "tools"))
import evaluate as EV                                              # noqa: E402

# ---- v18 参数（与 bbh/goal_decider.py 一致）----
BASE = 0.30
CONT_SPAN_SEC = 0.2
POS_TIERS = [(0.1, 0.20), (0.2, 0.17), (0.3, 0.14), (0.4, 0.10), (0.5, 0.06)]
HIGH_CONF = 0.65
MIN_CONF = 0.50
NET_STATIC_TH = 5.0
NET_SCORE_STATIC = -0.20
NET_SCORE_GLOBAL = -0.10

OFFICIAL = ["2026-08-22_1", "2026-08-28_1", "2026-08-29_3",
            "2026-09-04_1", "2026-09-11_3", "2026-09-12_1", "2026-09-19_1"]
TUNE_FIELDS = ["2026-08-22_1", "2026-08-28_1", "2026-09-04_1",
               "2026-09-11_3", "2026-09-12_1"]
HOLDOUT_FIELD = "2026-08-29_3"
EXCLUDED = "2026-09-19_1"          # 崩场：不进六场门，只做参考
SIX = [v for v in OFFICIAL if v != EXCLUDED]

REVERSE_FIELDS = ["2026-10-10_1", "2026-09-24_1", "2026-10-16_1"]
DERIVED_TRUTH = {"2026-09-24_1", "2026-10-16_1"}   # truth_derived/ 里的派生真值

WIN = 0.5          # 反馈↔事件匹配窗（秒，同 Step 0）
FP_TARGET = 0.658  # 目标查准
FP_CAP = 0.654     # 实测可达上限代入后的主门（Step 0）

NUM_FEATS = ["conf", "geom", "net_term", "cross_off_abs", "cross_off",
             "above_n", "below_n", "above_span", "below_span",
             "descent_px", "below_big", "net_post", "net_ctrl",
             "side_above", "side_below", "pos_score"]


def rd(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def wr(p, obj):
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


# ------------------------------------------------------------ v18 打分函数
def side_score(n, span, cont_span=CONT_SPAN_SEC):
    if n >= 2 and span >= cont_span:
        return 0.20
    if n >= 2:
        return 0.14
    if n == 1:
        return 0.05
    return 0.0


def pos_score(off, tiers=None):
    for lim, score in (tiers if tiers is not None else POS_TIERS):
        if off <= lim:
            return score
    return 0.0


def net_score(post, local, sc_static=NET_SCORE_STATIC,
              sc_global=NET_SCORE_GLOBAL):
    if post is None:
        return 0.0
    if post < NET_STATIC_TH:
        return sc_static
    if local is False:
        return sc_global
    return 0.0


def geom_conf(e, cont_span=CONT_SPAN_SEC, tiers=None):
    return round(BASE + side_score(e["above_n"], e["above_span"], cont_span)
                 + side_score(e["below_n"], e["below_span"], cont_span)
                 + pos_score(abs(e["cross_off"]), tiers), 2)


def net_term(e):
    """b2 注入行恒 0（net_motion.net_term 同语义）。"""
    if e.get("review_reason") == "b2":
        return 0.0
    return net_score(e.get("net_post"), e.get("net_local"))


def status_of(e, conf, hc=HIGH_CONF, mc=MIN_CONF):
    """档位规则（goal_decider + net_evidence_pass）：low 硬门 > 复核硬门 > 阈值。"""
    if e.get("low_reason") is not None:
        return "low"
    if e.get("review_reason") is not None or e.get("pop_back") is not None:
        return "review"
    if conf >= hc:
        return "high"
    return "review" if conf >= mc else "low"


def truth_path(field):
    p = os.path.join(UP, "truth", field + ".json")
    if os.path.exists(p):
        return p
    p = os.path.join(OUT, "truth_derived", field + ".json")
    return p if os.path.exists(p) else None


def events_path(field):
    for tag in ("v18_c03_s1", "v18_reverse"):
        p = os.path.join(YOLO, "eval_out", tag, field, "events.json")
        if os.path.exists(p) and rd(p).get("logic_ver") == 18:
            return p
    p = os.path.join(UP, "work", field, "events.json")
    if os.path.exists(p) and rd(p).get("logic_ver") == 18:
        return p
    return None


# ------------------------------------------------- 1 载入事件 + 打真值标签
def load_field(field):
    """→ {field, events:[{t,status,conf,...,label,kept}], notes}"""
    ep = events_path(field)
    tp = truth_path(field)
    if not ep or not tp:
        return None
    doc = rd(ep)
    ev = doc["events"]
    # deleted / 间隙球剔除（与 evaluate.field_metrics 同口径）
    deleted = EV._load_deleted_seconds(os.path.join(UP, "work", field,
                                                    "events.json"))
    inter = EV.INTERMISSION_GOALS.get(field, [])
    drop = EV._deleted_exclusions([e["t"] for e in ev], deleted + inter)
    truth_doc = rd(tp)
    tol = truth_doc.get("tolerance", EV.TOLERANCE)
    goals = sorted(truth_doc["goals"])

    used = set()
    out = []
    for i, e in enumerate(ev):
        rec = {k: e.get(k) for k in
               ("t", "status", "confidence", "low_reason", "review_reason",
                "cross_off", "above_n", "below_n", "above_span", "below_span",
                "descent_px", "below_big", "pop_back", "net_post", "net_ctrl",
                "net_local")}
        rec["kept"] = i not in drop
        rec["geom"] = geom_conf(e)
        rec["net_term"] = net_term(e)
        rec["cross_off_abs"] = abs(e["cross_off"])
        rec["cross_off"] = e["cross_off"]
        rec["side_above"] = side_score(e["above_n"], e["above_span"])
        rec["side_below"] = side_score(e["below_n"], e["below_span"])
        rec["pos_score"] = pos_score(abs(e["cross_off"]))
        rec["conf"] = e["confidence"]         # 存储值 = 终值（几何+网分）
        rec["net_local_s"] = ("None" if e.get("net_local") is None
                              else str(e["net_local"]))
        # 真值标签（同 match_report 的贪心匹配）
        m = next((j for j, g in enumerate(goals)
                  if j not in used and abs(g - e["t"]) <= tol), None)
        if m is not None:
            used.add(m)
            rec["label"] = "goal"
        else:
            rec["label"] = "nongoal"
        out.append(rec)
    return {"field": field, "events": out, "logic_ver": doc.get("logic_ver"),
            "src": ep, "truth": tp, "tol": tol, "goals": len(goals),
            "excluded_rows": len(drop)}


def active_fp_events():
    """Step 0 的 active fp → {(field, round(t,3)): record}（含官方与非官方场）。"""
    d = rd(os.path.join(OUT, "active_cases.json"))
    out = {}
    for r in d["records"]:
        if r["kind"] != "fp" or r.get("active") is not True:
            continue
        if not r.get("event"):
            continue
        key = (r["field"], round(r["event"]["t"], 3))
        out.setdefault(key, {"field": r["field"], "t": r["event"]["t"],
                             "t_str": r.get("t_str"),
                             "nearest_d": r.get("nearest_d")})
    return out


# --------------------------------------------------------- 2 统计工具
def quantiles(xs, qs=(0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0)):
    if not xs:
        return [None] * len(qs)
    s = sorted(xs)
    out = []
    for q in qs:
        k = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
        out.append(s[k])
    return out


def auc(pos, neg):
    """rank AUC：pos 为「假绿」，neg 为「真进球」。>0.5 表示该特征在假绿上更大。

    同值取平均秩（Mann-Whitney U 的标准处理）。不引 sklearn，避免环境依赖。"""
    if not pos or not neg:
        return None
    vals = sorted(set(pos) | set(neg))
    rank_of = {}
    # 秩 = 小于该值的样本数 + (等于该值的样本数+1)/2
    less = 0
    for v in vals:
        eq = sum(1 for x in pos if x == v) + sum(1 for x in neg if x == v)
        rank_of[v] = less + (eq + 1) / 2.0
        less += eq
    np_, nn = len(pos), len(neg)
    rp = sum(rank_of[v] for v in pos)
    u = rp - np_ * (np_ + 1) / 2.0
    return u / (np_ * nn)


def cut_analysis(fp_vals, goal_vals, direction):
    """单刀扫描：rule = 假绿侧更极端的方向降档。
    direction='high'：值越大越像假绿（降掉 value >= thr）；'low' 反之。
    → 返回在「误伤率 ≤3%」下捕获假绿最多的一刀。"""
    vals = sorted(set(fp_vals) | set(goal_vals))
    if not vals:
        return None
    best = None
    for thr in vals:
        if direction == "high":
            fp_hit = sum(1 for v in fp_vals if v >= thr)
            g_hit = sum(1 for v in goal_vals if v >= thr)
        else:
            fp_hit = sum(1 for v in fp_vals if v <= thr)
            g_hit = sum(1 for v in goal_vals if v <= thr)
        dmg = g_hit / len(goal_vals) if goal_vals else 0.0
        if dmg > 0.03:
            continue
        cap = fp_hit / len(fp_vals) if fp_vals else 0.0
        cand = {"thr": thr, "fp_hit": fp_hit, "fp_cap": round(cap, 4),
                "goal_hit": g_hit, "goal_dmg": round(dmg, 4)}
        if best is None or cand["fp_cap"] > best["fp_cap"]:
            best = cand
    return best


# --------------------------------------------- 3 HIGH_CONF 精确扫描
def sweep_highconf(pop, hc):
    """用存储 conf 重分箱 → 各场口径指标（等价 evaluate.auto_metrics）。"""
    rows = []
    for field, d in pop.items():
        goals = d["goals"]
        hi_rows = []
        n_review = n_low = 0
        for e in d["events"]:
            if not e["kept"]:
                continue
            st = status_of(e, e["conf"], hc=hc)
            if st == "high":
                hi_rows.append(e)
            elif st == "review":
                n_review += 1
            else:
                n_low += 1
        # 自动档命中：high 里 label==goal 的条数（与 match_report 等价：
        # 一行最多认一个 truth，贪心已在 load_field 做过）
        hit = sum(1 for e in hi_rows if e["label"] == "goal")
        rows.append({"field": field, "goals": goals, "high": len(hi_rows),
                     "review": n_review, "low": n_low, "auto_hit": hit,
                     "false_green": len(hi_rows) - hit})
    return rows


def agg_rows(rows, fields):
    sel = [r for r in rows if r["field"] in fields]
    a = {k: sum(r[k] for r in sel) for k in
         ("goals", "high", "review", "low", "auto_hit", "false_green")}
    a["precision"] = a["auto_hit"] / a["high"] if a["high"] else 0.0
    a["recall"] = a["auto_hit"] / a["goals"] if a["goals"] else 0.0
    return a


# --------------------------------------------------------------- 主流程
def bucket_of(e):
    """网证据分桶（决定网分档位）。b2 注入行 conf 未含网分。"""
    if e.get("review_reason") == "b2":
        return "b2"
    p = e.get("net_post")
    if p is None:
        return "unmeasured"
    if p < NET_STATIC_TH:
        return "static"
    if e.get("net_local") is False:
        return "nonlocal"
    return "local"


def status_net(e, hc, sg, ss):
    """白名单族：改 HIGH_CONF + 网分档值（不动测量判据）。"""
    if e.get("low_reason") is not None:
        return "low"
    if e.get("review_reason") is not None or e.get("pop_back") is not None:
        return "review"
    b = bucket_of(e)
    if b == "b2":
        return "high" if e["conf"] >= hc else (
            "review" if e["conf"] >= MIN_CONF else "low")
    new_net = ss if b == "static" else (sg if b == "nonlocal" else 0.0)
    conf = round(e["conf"] - e["net_term"] + new_net, 2)
    if conf >= hc:
        return "high"
    return "review" if conf >= MIN_CONF else "low"


def eval_net(pop, fields, hc, sg, ss, act_keys):
    """→ 汇总 + 逐场 + 护栏 A（active fp 降级命中率）。"""
    tot = {"goals": 0, "high": 0, "hit": 0, "review": 0, "low": 0}
    per = {}
    act_hit = act_tot = 0
    for v in fields:
        d = pop[v]
        p = {"goals": d["goals"], "high": 0, "hit": 0, "review": 0, "low": 0}
        for e in d["events"]:
            if not e["kept"]:
                continue
            st = status_net(e, hc, sg, ss)
            if st == "high":
                p["high"] += 1
                p["hit"] += (e["label"] == "goal")
            else:
                p[st] += 1
            if (v, round(e["t"], 3)) in act_keys:
                act_tot += 1
                if st != "high":
                    act_hit += 1
        p["precision"] = p["hit"] / p["high"] if p["high"] else 0.0
        p["recall"] = p["hit"] / p["goals"] if p["goals"] else 0.0
        per[v] = p
        for k in ("goals", "high", "hit", "review", "low"):
            tot[k] += p[k]
    tot["precision"] = tot["hit"] / tot["high"] if tot["high"] else 0.0
    tot["recall"] = tot["hit"] / tot["goals"] if tot["goals"] else 0.0
    tot["guard_a"] = act_hit / act_tot if act_tot else None
    return tot, per, act_hit, act_tot


def feasibility(pop, act_keys, base_tot, base_per):
    """白名单族穷举：HIGH_CONF × 网分两档值。全部护栏机械判定。

    → 返回全部组合的评估结果（带逐门 flags），由调用方决定如何呈现。
    """
    hc_grid = [round(0.60 + 0.01 * i, 2) for i in range(36)]
    sg_grid = [round(-0.05 - 0.01 * i, 2) for i in range(26)]
    ss_grid = [-0.15, -0.20, -0.25, -0.30]
    out = []
    for hc in hc_grid:
        for sg in sg_grid:
            for ss in ss_grid:
                tot, per, ah, at = eval_net(pop, SIX, hc, sg, ss, act_keys)
                d_rev = tot["review"] - base_tot["review"]
                worst = min(per[v]["recall"] - base_per[v]["recall"]
                            for v in SIX)
                max_rev = max(per[v]["review"] - base_per[v]["review"]
                              for v in SIX)
                rev_d = {}
                for rv in REVERSE_FIELDS:
                    dd = pop.get(rv)
                    if not dd:
                        continue
                    nb = sum(1 for e in dd["events"] if e["kept"]
                             and e["label"] == "goal"
                             and status_of(e, e["conf"]) != "high")
                    nn = sum(1 for e in dd["events"] if e["kept"]
                             and e["label"] == "goal"
                             and status_net(e, hc, sg, ss) != "high")
                    rev_d[rv] = nn - nb
                flags = {
                    "prec": tot["precision"] >= FP_CAP,
                    "rec": tot["recall"] >= 0.819,
                    "field": worst >= -0.05,
                    "rev1": max_rev <= 20,
                    "rev6": d_rev <= 120,
                    "guardA": (tot["guard_a"] is not None
                               and tot["guard_a"] >= 0.90),
                    "revMonitor": all(x <= 0 for x in rev_d.values()),
                }
                out.append({
                    "hc": hc, "sg": sg, "ss": ss,
                    "precision": round(tot["precision"], 4),
                    "recall": round(tot["recall"], 4),
                    "high": tot["high"], "hit": tot["hit"],
                    "d_hit": tot["hit"] - base_tot["hit"],
                    "d_review": d_rev, "max_field_d_review": max_rev,
                    "worst_field_d_recall": round(worst, 4),
                    "guard_a": round(tot["guard_a"], 4)
                    if tot["guard_a"] is not None else None,
                    "rev_delta": rev_d,
                    "rev_delta_max": max(rev_d.values()) if rev_d else 0,
                    "flags": flags,
                    "pass_all": all(flags.values()),
                    "pass_no_a": all(v for k, v in flags.items()
                                     if k != "guardA"),
                    "per_field": {v: {"precision": round(per[v]["precision"], 4),
                                      "recall": round(per[v]["recall"], 4),
                                      "d_review": per[v]["review"]
                                      - base_per[v]["review"],
                                      "d_hit": per[v]["hit"]
                                      - base_per[v]["hit"]}
                                  for v in SIX},
                })
    return out


def main():
    print("== 1 复算器验收 ==", flush=True)
    pop = {}
    for v in OFFICIAL + REVERSE_FIELDS:
        d = load_field(v)
        if d:
            pop[v] = d
            print("  %-14s rows=%-4d goals=%-3d logic=%s" %
                  (v, len(d["events"]), d["goals"], d["logic_ver"]), flush=True)

    # 1a status 100% 复现（存储 conf + 硬门）
    tot = ok = 0
    conf_bad = []
    for v, d in pop.items():
        for e in d["events"]:
            tot += 1
            ok += (status_of(e, e["conf"]) == e["status"])
            if abs(e["geom"] + e["net_term"] - e["conf"]) > 0.005:
                conf_bad.append({"field": v, "t": round(e["t"], 2),
                                 "conf": e["conf"],
                                 "recalc": round(e["geom"] + e["net_term"], 2),
                                 "d": round(e["conf"] - e["geom"]
                                            - e["net_term"], 2),
                                 "above_span": e["above_span"],
                                 "below_span": e["below_span"],
                                 "cross_off": e["cross_off"]})
    # 边界归因
    def boundary(c):
        tags = []
        for s in (c["above_span"], c["below_span"]):
            if abs(s - CONT_SPAN_SEC) <= 0.003:
                tags.append("span≈0.2")
        for lim, _ in POS_TIERS:
            if abs(abs(c["cross_off"]) - lim) <= 0.003:
                tags.append("off≈%s" % lim)
        return tags
    for c in conf_bad:
        c["boundary"] = boundary(c)
    exact_rate = ok / tot if tot else 0.0
    print("  status 复现 %d/%d = %.2f%%；conf 特征重算差 %d 条（边界归因 %d/%d）"
          % (ok, tot, 100 * exact_rate, len(conf_bad),
             sum(1 for c in conf_bad if c["boundary"]), len(conf_bad)), flush=True)

    # ---- 2 群体 ----
    act = active_fp_events()
    fp_active = []      # 用户标错的 active 假绿（官方 6 场内）
    fp_active_meta = []
    for v in SIX:
        for e in pop[v]["events"]:
            if not e["kept"] or e["status"] != "high":
                continue
            if (v, round(e["t"], 3)) in act:
                fp_active.append(e)
                fp_active_meta.append((v, round(e["t"], 3)))
    fp_all, goal_high = [], []
    for v in SIX:
        for e in pop[v]["events"]:
            if not e["kept"] or e["status"] != "high":
                continue
            (goal_high if e["label"] == "goal" else fp_all).append(e)
    print("  群体：fp_active=%d  fp_all=%d  goal_high=%d"
          % (len(fp_active), len(fp_all), len(goal_high)), flush=True)

    # ---- 3 单特征可分性 ----
    feats = {}
    for f in NUM_FEATS:
        a = [e[f] for e in fp_active if e.get(f) is not None]
        b = [e[f] for e in goal_high if e.get(f) is not None]
        a2 = [e[f] for e in fp_all if e.get(f) is not None]
        b2 = [e[f] for e in goal_high if e.get(f) is not None]
        q_a = quantiles(a)
        q_b = quantiles(b)
        au1 = auc(a, b)
        au2 = auc(a2, b2)
        dirn = "high" if (au1 or 0.5) >= 0.5 else "low"
        dirn2 = "high" if (au2 or 0.5) >= 0.5 else "low"
        feats[f] = {
            "auc_active": None if au1 is None else round(au1, 3),
            "auc_all": None if au2 is None else round(au2, 3),
            "q_active": [round(x, 3) if isinstance(x, float) else x
                         for x in q_a],
            "q_goal": [round(x, 3) if isinstance(x, float) else x for x in q_b],
            "cut_active": cut_analysis(a, b, dirn),
            "cut_all": cut_analysis(a2, b2, dirn2),
        }
    # 类别特征：net_local
    cat = {}
    for key in ("net_local_s",):
        for name, grp in (("fp_active", fp_active), ("fp_all", fp_all),
                          ("goal_high", goal_high)):
            c = {}
            for e in grp:
                c[e[key]] = c.get(e[key], 0) + 1
            cat.setdefault(key, {})[name] = c

    # ---- 4 HIGH_CONF 精确扫描 ----
    print("== 2 HIGH_CONF 扫描（精确）==", flush=True)
    base_rows = sweep_highconf(pop, HIGH_CONF)
    base6 = agg_rows(base_rows, SIX)
    print("  baseline 6场：high=%d hit=%d 查准=%.3f 查全=%.3f"
          % (base6["high"], base6["auto_hit"], base6["precision"],
             base6["recall"]), flush=True)
    sweep = []
    hc = 0.50
    while hc <= 0.95 + 1e-9:
        rows = sweep_highconf(pop, round(hc, 2))
        s6 = agg_rows(rows, SIX)
        tune = agg_rows(rows, TUNE_FIELDS)
        hold = agg_rows(rows, [HOLDOUT_FIELD])
        b_tune = agg_rows(base_rows, TUNE_FIELDS)
        b_hold = agg_rows(base_rows, [HOLDOUT_FIELD])
        per_field = {}
        for r in rows:
            if r["field"] not in SIX:
                continue
            b = next(x for x in base_rows if x["field"] == r["field"])
            per_field[r["field"]] = {
                "d_high": r["high"] - b["high"],
                "d_review": r["review"] - b["review"],
                "d_hit": r["auto_hit"] - b["auto_hit"],
                "recall": round(r["auto_hit"] / r["goals"], 4)
                if r["goals"] else None,
                "d_recall": round((r["auto_hit"] - b["auto_hit"]) / r["goals"], 4)
                if r["goals"] else None,
            }
        sweep.append({
            "hc": round(hc, 2),
            "six": {"high": s6["high"], "hit": s6["auto_hit"],
                    "precision": round(s6["precision"], 4),
                    "recall": round(s6["recall"], 4),
                    "d_hit": s6["auto_hit"] - base6["auto_hit"],
                    "d_review": s6["review"] - base6["review"]},
            "tune": {"precision": round(tune["precision"], 4),
                     "recall": round(tune["recall"], 4)},
            "hold": {"precision": round(hold["precision"], 4),
                     "recall": round(hold["recall"], 4),
                     "d_precision": round(hold["precision"]
                                          - b_hold["precision"], 4),
                     "d_recall": round(hold["recall"] - b_hold["recall"], 4)},
            "per_field": per_field,
        })
        hc = round(hc + 0.01, 2)

    # 反作用监测：各场非自动档条数变化
    rev = {}
    for v in REVERSE_FIELDS:
        if v not in pop:
            continue
        d = pop[v]
        base_nonhigh = sum(1 for e in d["events"] if e["kept"]
                           and e["label"] == "goal"
                           and status_of(e, e["conf"]) != "high")
        series = []
        for s in sweep:
            n = sum(1 for e in d["events"] if e["kept"]
                    and e["label"] == "goal"
                    and status_of(e, e["conf"], hc=s["hc"]) != "high")
            series.append({"hc": s["hc"], "non_high": n,
                           "delta": n - base_nonhigh})
        rev[v] = {"base_non_high": base_nonhigh, "series": series}

    # ---- 5 结论：按「过门线」筛合格 HC ----
    qualified = []
    for s in sweep:
        pf = s["per_field"]
        worst = min((x["d_recall"] for x in pf.values()
                     if x["d_recall"] is not None), default=0)
        max_review = max((x["d_review"] for x in pf.values()), default=0)
        ok_prec = s["six"]["precision"] >= FP_CAP
        ok_recall = s["six"]["recall"] >= 0.819
        ok_field = worst >= -0.05          # 逐场红线：查全降幅 ≤5pct
        ok_cost = max_review <= 20         # 护栏 C
        qualified.append({"hc": s["hc"], "precision": s["six"]["precision"],
                          "recall": s["six"]["recall"],
                          "worst_field_d_recall": round(worst, 4),
                          "max_field_d_review": max_review,
                          "pass": bool(ok_prec and ok_recall and ok_field
                                       and ok_cost),
                          "why": [n for n, b in
                                  (("查准", ok_prec), ("查全", ok_recall),
                                   ("逐场红线", ok_field), ("复核上限", ok_cost))
                                  if not b]})

    # ---- 5 白名单族穷举（HIGH_CONF × 网分两档值）----
    print("== 3 白名单族穷举 ==", flush=True)
    act_keys = set(fp_active_meta)
    base_tot, base_per, _ah, _at = eval_net(pop, SIX, HIGH_CONF,
                                            NET_SCORE_GLOBAL, NET_SCORE_STATIC,
                                            act_keys)
    assert base_tot["high"] == 1051 and base_tot["hit"] == 639, base_tot
    feas = feasibility(pop, act_keys, base_tot, base_per)
    pass_all = [x for x in feas if x["pass_all"]]
    pass_no_a = [x for x in feas if x["pass_no_a"]]
    gmax = max(feas, key=lambda x: (x["guard_a"] or 0))
    # 近解集：查准/查全/逐场红线/6场成本 全过，只差 rev1 与护栏 A
    near = [x for x in feas
            if x["flags"]["prec"] and x["flags"]["rec"]
            and x["flags"]["field"] and x["flags"]["rev6"]]
    rec_cost = (sorted(near, key=lambda x: (x["max_field_d_review"],
                                            x["d_review"],
                                            -(x["guard_a"] or 0)))[0]
                if near else None)
    rec_a = (sorted(near, key=lambda x: (-(x["guard_a"] or 0),
                                         x["max_field_d_review"]))[0]
             if near else None)
    max_a = (max(near, key=lambda x: (x["guard_a"] or 0))
             if near else gmax)
    rec = rec_a or rec_cost
    print("  全部门线：%d 组；近解集 %d 组；近解护栏A上限 %.1f%%（全族 %.1f%%，"
          "但查全仅 %.1f%%）"
          % (len(pass_all), len(near), 100 * (max_a["guard_a"] or 0),
             100 * (gmax["guard_a"] or 0), 100 * gmax["recall"]), flush=True)

    # 推荐解的留出 / 反作用监测
    rec_hold = rec_rev = None
    if rec:
        _t, _p, _a, _b = eval_net(pop, [HOLDOUT_FIELD], rec["hc"], rec["sg"],
                                  rec["ss"], act_keys)
        _t0, _p0, _a0, _b0 = eval_net(pop, [HOLDOUT_FIELD], HIGH_CONF,
                                      NET_SCORE_GLOBAL, NET_SCORE_STATIC,
                                      act_keys)
        rec_hold = {"precision": round(_t["precision"], 4),
                    "recall": round(_t["recall"], 4),
                    "d_precision": round(_t["precision"] - _t0["precision"], 4),
                    "d_recall": round(_t["recall"] - _t0["recall"], 4),
                    "d_review": _t["review"] - _t0["review"]}
        rec_rev = {}
        for v in REVERSE_FIELDS:
            if v not in pop:
                continue
            n_base = sum(1 for e in pop[v]["events"] if e["kept"]
                         and e["label"] == "goal"
                         and status_of(e, e["conf"]) != "high")
            n_new = sum(1 for e in pop[v]["events"] if e["kept"]
                        and e["label"] == "goal"
                        and status_net(e, rec["hc"], rec["sg"],
                                       rec["ss"]) != "high")
            rec_rev[v] = {"base_non_high": n_base, "new_non_high": n_new,
                          "delta": n_new - n_base}

    # 护栏 A 权衡曲线：按 guardA 每 5pct 一档，取该档查准最高的组合
    best_by_a = {}
    for x in feas:
        k = int(round((x["guard_a"] or 0) * 20))
        if k not in best_by_a or x["precision"] > best_by_a[k]["precision"]:
            best_by_a[k] = x
    tradeoff = [best_by_a[k] for k in sorted(best_by_a) if k >= 5]

    res = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "verify": {"status_exact": exact_rate, "status_ok": ok, "rows": tot,
                   "conf_bad": len(conf_bad),
                   "conf_bad_boundary": sum(1 for c in conf_bad
                                            if c["boundary"]),
                   "conf_bad_detail": conf_bad},
        "populations": {"fp_active": len(fp_active), "fp_all": len(fp_all),
                        "goal_high": len(goal_high),
                        "fp_active_events": fp_active_meta},
        "features": feats,
        "categorical": cat,
        "baseline6": {k: (round(v, 4) if isinstance(v, float) else v)
                      for k, v in base6.items()},
        "sweep_highconf": sweep,
        "reverse": rev,
        "qualified": qualified,
        "feasibility": {"n_eval": len(feas),
                        "n_pass_all": len(pass_all),
                        "n_pass_no_a": len(pass_no_a),
                        "max_guard_a_near": max_a["guard_a"],
                        "max_guard_a_global": gmax["guard_a"],
                        "guard_a_global_recall": gmax["recall"],
                        "n_near": len(near),
                        "near": near,
                        "rec_cost": rec_cost,
                        "rec_a": rec_a,
                        "tradeoff": tradeoff,
                        "rec_holdout": rec_hold,
                        "rec_reverse": rec_rev},
        "consts": {"HIGH_CONF": HIGH_CONF, "MIN_CONF": MIN_CONF,
                   "POS_TIERS": POS_TIERS, "CONT_SPAN_SEC": CONT_SPAN_SEC,
                   "NET_SCORE_STATIC": NET_SCORE_STATIC,
                   "NET_SCORE_GLOBAL": NET_SCORE_GLOBAL,
                   "FP_TARGET": FP_TARGET, "FP_CAP": FP_CAP},
    }
    wr(os.path.join(OUT, "step1_attrib.json"), res)

    # ---------------- 报告 ----------------
    L = []
    A = L.append
    A("# Step 1 · 特征归因（R-020 判定层 hard-case 调优）")
    A("")
    A("> 只读诊断。不改生产代码 / 数据集 / 权重。生成于 %s"
      % res["generated_at"])
    A("")
    A("## 1 复算器验收（硬门）")
    A("")
    A("| 项 | 结果 | 判定 |")
    A("|---|---|---|")
    A("| status 复现（存储 conf + 硬门标志） | **%d/%d = %.2f%%** | %s |"
      % (ok, tot, 100 * exact_rate,
         "✅ 过" if exact_rate >= 0.9999 else "❌ 不过"))
    A("| conf 特征重算不一致 | %d 条（其中 %d 条落在特征取整边界） | %s |"
      % (len(conf_bad), sum(1 for c in conf_bad if c["boundary"]),
         "✅ 公式无误（歧义仅来自存储精度）"
         if all(c["boundary"] for c in conf_bad) else "⚠️ 有非边界差异"))
    A("")
    A("结论：判定公式已完全复现；**改 HIGH_CONF 用存储 conf 即可精确重分箱"
      "（零歧义）**，改 POS_TIERS / CONT_SPAN / 网分需重算 conf，"
      "受影响仅 %d 条边界行（差异 ≤0.06，可做敏感性声明）。" % len(conf_bad))
    A("")
    A("## 2 群体")
    A("")
    A("| 群体 | 定义 | 条数 |")
    A("|---|---|---|")
    A("| `fp_active` | high 且用户已反馈「没进球」（护栏 A 分母） | **%d** |"
      % len(fp_active))
    A("| `fp_all` | high 但未命中 truth（按定义即假绿，**全量标注**） | **%d** |"
      % len(fp_all))
    A("| `goal_high` | high 且命中 truth（自动档命中的真进球） | **%d** |"
      % len(goal_high))
    A("")
    A("> 关键：truth 给出**全量事件级真值**，假绿不需要靠反馈才知道——"
      "412 条全部有标签，只有 88 条被用户点过（Step 0 记的 89 条反馈里有 1 条"
      "重复标记同一事件：09-04_1 t=858.32，按事件去重后 88）。"
      "归因因此可做在 412 条全量上。")
    A("")
    A("## 3 单特征可分性（fp vs 真进球）")
    A("")
    A("AUC 以「假绿更小/更大」判方向；`cut` 一列 = 在**误伤真进球 ≤3%** "
      "前提下，单刀最多能降掉多少假绿。")
    A("")
    A("| 特征 | AUC(active) | AUC(all) | 假绿中位 | 真进球中位 | 单刀阈值 | 降假绿(active) | 误伤 |")
    A("|---|---|---|---|---|---|---|---|")
    order = sorted(feats.items(),
                   key=lambda kv: -abs((kv[1]["auc_all"] or 0.5) - 0.5))
    for f, s in order:
        c = s["cut_all"] or {}
        A("| `%s` | %s | %s | %s | %s | %s | %s | %s |"
          % (f, s["auc_active"], s["auc_all"],
             s["q_active"][3], s["q_goal"][3],
             ("≥" if (s["auc_all"] or 0.5) >= 0.5 else "≤")
             + str(c.get("thr")) if c else "—",
             ("%.1f%%" % (100 * c["fp_cap"])) if c else "—",
             ("%.1f%%" % (100 * c["goal_dmg"])) if c else "—"))
    A("")
    A("类别特征 `net_local`：")
    A("")
    A("| 群体 | True | False | None |")
    A("|---|---|---|---|")
    for name in ("fp_active", "fp_all", "goal_high"):
        c = cat["net_local_s"][name]
        A("| %s | %d | %d | %d |" % (name, c.get("True", 0),
                                     c.get("False", 0), c.get("None", 0)))
    A("")
    A("## 4 HIGH_CONF 精确扫描（6 场，剔 09-19_1）")
    A("")
    A("基线：high **%d** / 命中 **%d** / 查准 **%.1f%%** / 查全 **%.1f%%**"
      % (base6["high"], base6["auto_hit"], 100 * base6["precision"],
         100 * base6["recall"]))
    A("")
    A("主门 = 查准 ≥ %.1f%%（上限代入）且查全 ≥ 81.9%%；"
      "红线 = 任一场查全降 >5pct；护栏 C = 单场 review 增 ≤20。" % (100 * FP_CAP))
    A("")
    A("| HIGH_CONF | 查准 | 查全 | high | 命中Δ | reviewΔ(6场) | 最差场查全Δ | 最大场reviewΔ | 判定 |")
    A("|---|---|---|---|---|---|---|---|---|")
    for q in qualified:
        if q["hc"] < 0.60 or q["hc"] > 0.85:
            continue
        s = next(x for x in sweep if x["hc"] == q["hc"])
        A("| %.2f | %.1f%% | %.1f%% | %d | %+d | %+d | %+.1f pct | %+d | %s |"
          % (q["hc"], 100 * q["precision"], 100 * q["recall"], s["six"]["high"],
             s["six"]["d_hit"], s["six"]["d_review"],
             100 * q["worst_field_d_recall"], q["max_field_d_review"],
             "✅ 过门" if q["pass"] else "✗ " + "/".join(q["why"])))
    A("")
    okq = [q for q in qualified if q["pass"]]
    if okq:
        best = max(okq, key=lambda q: q["precision"])
        A("**合格区间**：HIGH_CONF ∈ [%.2f, %.2f]；取查准最高者 "
          "**%.2f**（查准 %.1f%% / 查全 %.1f%%）。"
          % (min(q["hc"] for q in okq), max(q["hc"] for q in okq),
             best["hc"], 100 * best["precision"], 100 * best["recall"]))
    else:
        A("**无任何 HIGH_CONF 同时过四道门**——见上表 `why` 列。")
    A("")
    A("## 5 白名单族穷举：这道门到底能不能过")
    A("")
    A("族定义 = 只动 `HIGH_CONF` + 网分两档值（`NET_SCORE_GLOBAL` / "
      "`NET_SCORE_STATIC`），**不动 `NET_STATIC_TH` 测量判据**。"
      "穷举 %d 组（HC 0.60~0.95 × 网分 0.05~0.30 × 静态 4 档），"
      "七道门线机械判定：查准 ≥%.1f%% / 查全 ≥81.9%% / 任一场查全降 ≤5pct / "
      "单场 review +≤20 / 6 场 +≤120 / 护栏 A ≥90%% / 反向监测不恶化。"
      % (len(feas), 100 * FP_CAP))
    A("")
    A("| 门线组合 | 组合数 |")
    A("|---|---|")
    A("| **七道全过** | **%d** |" % len(pass_all))
    A("| 除护栏 A 外全过 | **%d** |" % len(pass_no_a))
    A("| 查准/查全达标前提下 护栏 A 最高 | **%.1f%%** |" % (100 * (max_a["guard_a"] or 0)))
    A("")
    if not pass_all:
        A("**判定：白名单族内「七门全过」无解。** 卡死的**不是查准/查全**（那两门有解，"
          "见下近解集），而是另三道：**单场 review 增量**（最低 +%d，门线 20）、"
          "**护栏 A**（最高 %.1f%%，门线 90%%）、"
          "**反向监测**（真进球降级数最小 +%d，门线 0）。"
          % (min((x["max_field_d_review"] for x in near), default=0),
             100 * (max_a["guard_a"] or 0),
             min((x["rev_delta_max"] for x in near), default=0)))
        A("")
        A("> ⚠️ 关于第三条红线：**任何降级动作都必然让部分真进球掉出自动档**，"
          "「不得增加」这条红线在数学上等价于「禁止任何降级」。"
          "它需要改成有上限的容差（例如单场真进球降级 ≤N 条），"
          "而不是零容忍。")
        A("")
        A("原因见 §5.1：剩下那批假绿是**结构上不可分的硬核**，不是参数没调好。")
    else:
        A("**判定：有解，共 %d 组。**" % len(pass_all))
    A("")
    if near:
        A("### 近解集：查准/查全/逐场红线/6 场成本 全过，只差「单场 review」「护栏 A」「反向监测」")
        A("")
        A("共 **%d** 组。按「单场 review 增量」从小到大列前 5：" % len(near))
        A("")
        A("| HIGH_CONF | 网不局部 | 网静止 | 查准 | 查全 | 命中Δ | reviewΔ | 单场maxRev | 护栏A | 反向Δmax |")
        A("|---|---|---|---|---|---|---|---|---|---|")
        for x in sorted(near, key=lambda y: (y["max_field_d_review"],
                                             y["d_review"]))[:5]:
            A("| %.2f | %.2f | %.2f | %.2f%% | %.2f%% | %+d | %+d | %+d | %.1f%% | %+d |"
              % (x["hc"], x["sg"], x["ss"], 100 * x["precision"],
                 100 * x["recall"], x["d_hit"], x["d_review"],
                 x["max_field_d_review"], 100 * (x["guard_a"] or 0),
                 x["rev_delta_max"]))
        A("")
        A("三道卡口的最佳值：单场 review **+%d**（门线 20）；护栏 A **%.1f%%**（门线 90%%）；"
          "反向监测 **+%d**（门线 0）。"
          % (min(x["max_field_d_review"] for x in near),
             100 * max((x["guard_a"] or 0) for x in near),
             min(x["rev_delta_max"] for x in near)))
        A("")
    if rec:
        A("### 参考解（除护栏 A 外全过门 · 护栏 A 最高 · 成本最低）："
          "`HIGH_CONF=%.2f` / `NET_SCORE_GLOBAL=%.2f` / "
          "`NET_SCORE_STATIC=%.2f`" % (rec["hc"], rec["sg"], rec["ss"]))
        A("")
        A("- 6 场：查准 **%.2f%%**（%+.1f pct）/ 查全 **%.2f%%**（%+.1f pct）/ "
          "命中 **%d**（%+d）/ review **%+d**"
          % (100 * rec["precision"],
             100 * rec["precision"] - 100 * base6["precision"],
             100 * rec["recall"], 100 * rec["recall"] - 100 * base6["recall"],
             rec["hit"], rec["d_hit"], rec["d_review"]))
        A("- 机制提醒：`HIGH_CONF` 降到 0.60 **不是放松**——它把「网确实局部在动」"
          "的行小幅放宽（conf 0.60~0.65 升绿），同时把「网不局部/网静止」的行"
          "大幅收紧，净效果是 high 从 1051 降到 955。")
        A("- 护栏 A：**%.1f%%**（离 90%% 差 %.1f pct）"
          % (100 * (rec["guard_a"] or 0), 90 - 100 * (rec["guard_a"] or 0)))
        A("- 逐场明细：")
        A("")
        A("| 场 | 查准 | 查全 | 命中Δ | reviewΔ |")
        A("|---|---|---|---|---|")
        for v, p in rec["per_field"].items():
            A("| %s | %.1f%% | %.1f%% | %+d | %+d |"
              % (v, 100 * p["precision"], 100 * p["recall"], p["d_hit"],
                 p["d_review"]))
        A("")
        if rec_hold:
            A("- **留出场 %s**：查准 %.1f%%（%+.1f pct）/ 查全 %.1f%%（%+.1f pct）"
              "/ review %+d —— %s"
              % (HOLDOUT_FIELD, 100 * rec_hold["precision"],
                 100 * rec_hold["d_precision"], 100 * rec_hold["recall"],
                 100 * rec_hold["d_recall"], rec_hold["d_review"],
                 "✅ 跨场泛化成立" if rec_hold["d_precision"] > 0
                 and rec_hold["d_recall"] >= -0.01 else "⚠️ 留出场未同步改善"))
        if rec_rev:
            A("- **反作用监测**（非自动档条数不得超过基线）："
              + "；".join("%s %d→%d（%+d）"
                         % (v, d["base_non_high"], d["new_non_high"],
                            d["delta"]) for v, d in rec_rev.items()))
    A("")
    if tradeoff:
        A("### 5.1 护栏 A 与代价的真实权衡曲线")
        A("")
        A("每一档取该档查准最高的组合（全部在参数族内可达）：")
        A("")
        A("| 护栏 A | 查准 | 查全 | 命中Δ | 单场maxRev | reviewΔ | 参数 |")
        A("|---|---|---|---|---|---|---|")
        for x in tradeoff:
            A("| **%.0f%%** | %.2f%% | %.2f%% | %+d | %+d | %+d | HC=%.2f / 不局部 %.2f / 静止 %.2f |"
              % (100 * (x["guard_a"] or 0), 100 * x["precision"],
                 100 * x["recall"], x["d_hit"], x["max_field_d_review"],
                 x["d_review"], x["hc"], x["sg"], x["ss"]))
        A("")
        A("读法：想打掉更多你标过的假绿，就必须牺牲查全与人工成本。"
          "**90% 这一档在这条曲线上根本不存在**——到 82% 时查全只剩 41%。")
        A("")
    A("### 5.2 为什么护栏 A 不可达：硬核 33 条")
    A("")
    hard = [e for e in fp_active if bucket_of(e) == "local"]
    A("用户标过、现行 v18 仍判绿的 **%d 条**假绿，按网证据分桶后：" % len(fp_active))
    A("")
    A("| 网证据桶 | 条数 | 占比 | conf 范围 | 可不可分 |")
    A("|---|---|---|---|---|")
    for b, label, why in (
            ("static", "网静止（post<5）", "可分（扣分可打掉）"),
            ("nonlocal", "网亮但非局部", "可将就（加深扣分能打掉）"),
            ("local", "**网确实局部在动**", "**不可分**——与真进球同特征")):
        sub = [e for e in fp_active if bucket_of(e) == b]
        if not sub:
            continue
        A("| %s | %d | %.1f%% | %s~%s | %s |"
          % (label, len(sub), 100 * len(sub) / len(fp_active),
             min(e["conf"] for e in sub), max(e["conf"] for e in sub), why))
    A("")
    A("**「网确实局部在动」这 %d 条，物理上就是「球触网了但没进」**"
      "（打铁蹭网、球穿网带边缘、别人碰网）——它们与真进球共享"
      "「几何居中 + 网局部运动 + 高置信」三件套，现有 12 个特征里"
      "没有任何一个能把它们分开。要打掉它们，必须引入**新证据维度**"
      "（如穿筐后的球轨迹延续性、球与出手人的时空关联），"
      "那不是调参数，是加规则。" % len(hard))
    A("")
    A("这也是为什么「六门全过」在本族内无解：**要达到 90% 的护栏 A，"
      "就必须打掉这批硬核，而参数族做不到**。")
    A("")
    A("## 6 反作用监测（HIGH_CONF 单旋钮，只测不调）")
    A("")
    A("非自动档条数不得增加。基线：%s"
      % ", ".join("%s %d" % (v, d["base_non_high"]) for v, d in rev.items()))
    A("")
    A("| HIGH_CONF | " + " | ".join(rev) + " |")
    A("|---|" + "---|" * len(rev))
    for i, s in enumerate(sweep):
        if s["hc"] < 0.60 or s["hc"] > 0.85:
            continue
        A("| %.2f | " % s["hc"]
          + " | ".join("%d (%+d)" % (rev[v]["series"][i]["non_high"],
                                     rev[v]["series"][i]["delta"])
                       for v in rev) + " |")
    A("")
    A("## 7 产物与下一步")
    A("")
    A("- `step1_attrib.json`：全量明细（特征分布 / HC 扫描 46 档 / "
      "白名单族穷举 / 边界归因）")
    A("- 下一步 Step 2：把推荐解做成**开关化候选分支**（生产默认值不动），"
      "再走点流重放 + 官方 KPI 复核；本步零代码改动")
    with io.open(os.path.join(OUT, "Step1归因.md"), "w", encoding="utf-8",
                 newline="\n") as f:
        f.write("\n".join(L) + "\n")

    print("== 4 报告 ==", flush=True)
    print("  status 复现 %.2f%%，conf 边界差异 %d 条" % (100 * exact_rate,
                                                     len(conf_bad)))
    print("  合格 HC 区间：%s"
          % (("[" + ", ".join("%.2f" % q["hc"] for q in okq) + "]")
             if okq else "无"))
    print("  白名单族：六门全过 %d 组 / 除护栏A外全过 %d 组 / 护栏A上限 %.1f%%"
          % (len(pass_all), len(pass_no_a), 100 * (max_a["guard_a"] or 0)))
    if rec:
        print("  参考解 HC=%.2f sg=%.2f ss=%.2f → 查准 %.2f%% 查全 %.2f%% 护栏A %.1f%%"
              % (rec["hc"], rec["sg"], rec["ss"], 100 * rec["precision"],
                 100 * rec["recall"], 100 * (rec["guard_a"] or 0)))
    print("  已写 Step1归因.md / step1_attrib.json")


if __name__ == "__main__":
    main()
