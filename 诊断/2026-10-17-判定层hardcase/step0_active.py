# -*- coding: utf-8 -*-
"""Step 0 · 复检筛 active + 建立基线（R-020 判定层 hard-case 调优）

只读诊断：不改生产代码、不改数据集、不碰权重。

做四件事：
1. 用 **v18 事件** 按 ±0.5s 匹配 373 条人工反馈 → 逐条判 active / 已修
   fp   : 该时刻仍 high → active（假绿仍存在）；已降档/已消失 → v18 已修
   flip : 该时刻仍 low/review → active（真进球仍被降级）；已 high → v18 已修
   miss : 该时刻无任何行 → active（真漏检）
2. 复刻官方裁判（直接 import 上游 tools/evaluate.py）复算 7 场两臂基线，
   与 reports/KPI_官方口径_v18.json 逐场逐字对拍（四数验收）
3. 从 work/<场>/selection.json 派生 09-24_1 / 10-16_1 的 truth
   （写本诊断目录 truth_derived/，不污染上游仓）——并做时间轴一致性体检
4. 产出 active_cases.json + Step0复检结论.md

场次口径（grill 裁定）：
  调参集   08-22_1 / 08-28_1 / 09-04_1 / 09-11_3 / 09-12_1（active fp 按场分层 7:3）
  留出     08-29_3 整场
  反向监测 10-10_1 / 09-24_1 / 10-16_1 / 08-21_1（只测不调）
  排除     09-19_1（崩场）  跳过 08-07_1（无 truth、无点流、无 v18 事件）
"""
import io
import json
import os
import sys
from datetime import date

YOLO = r"D:/claude code/yolo"
UP = r"D:/claude code/视频剪辑"          # 上游项目仓（只读）
OUT = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, os.path.join(UP, "tools"))
import evaluate as EV                                              # noqa: E402

WIN = 0.5                 # 反馈↔v18 事件匹配窗（秒）
SPLIT_SEED = 0
SPLIT_TUNE = 0.7

OFFICIAL = ["2026-08-22_1", "2026-08-28_1", "2026-08-29_3",
            "2026-09-04_1", "2026-09-11_3", "2026-09-12_1", "2026-09-19_1"]
ARMS = [("HSV(同源)", "v18_hsv"), ("c03_s1", "v18_c03_s1")]
TUNE_FIELDS = ["2026-08-22_1", "2026-08-28_1", "2026-09-04_1",
               "2026-09-11_3", "2026-09-12_1"]
HOLDOUT_FIELD = "2026-08-29_3"
REVERSE_FIELDS = ["2026-10-10_1", "2026-09-24_1", "2026-10-16_1", "2026-08-21_1"]
EXCLUDED = {"2026-09-19_1": "崩场（用户裁定剔除回归集）"}
SKIPPED = {"2026-08-07_1": "无 truth / 无点流 / 无 v18 事件，无法定量"}


def rd(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def wr(p, obj):
    with io.open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def parse_ts(s):
    """'mm:ss.s' / 'h:mm:ss.s' → 秒。"""
    parts = str(s).strip().split(":")
    v = 0.0
    for p in parts:
        v = v * 60 + float(p)
    return v


# ---------------------------------------------------------------- 1 基线复算
def field_metrics(video, events_path):
    """与 compare_ab.arm_metrics 同口径（直接复用上游裁判函数）。"""
    doc = rd(events_path)
    events = doc["events"]
    deleted = EV._load_deleted_seconds(os.path.join(UP, "work", video,
                                                    "events.json"))
    inter = EV.INTERMISSION_GOALS.get(video, [])
    drop = EV._deleted_exclusions([e["t"] for e in events], deleted + inter)
    kept = [e for i, e in enumerate(events) if i not in drop]
    detected = [(e["t"], e["status"]) for e in kept]
    truth = rd(os.path.join(UP, "truth", video + ".json"))
    tol = truth.get("tolerance", EV.TOLERANCE)
    goals = sorted(truth["goals"])
    rep = EV.match_report(goals, detected, tolerance=tol)
    high_total = sum(1 for _t, s in detected if s == "high")
    m = EV.auto_metrics(rep, high_total)
    return {"goals": len(goals), "rows": len(detected),
            "high": high_total, "review": sum(1 for _t, s in detected
                                              if s == "review"),
            "low": sum(1 for _t, s in detected if s == "low"),
            "excluded": len(drop),
            "auto_hit": m["自动档命中"], "false_green": m["假绿"],
            "precision": m["查准"], "recall": m["查全"],
            "review_hit": rep["待确认中"], "low_hit": rep["命中低置信"],
            "miss": rep["漏检"], "noise_low": rep["低噪"],
            "false_pos": rep["误报"]}


def agg(per_field, vids):
    keys = ("goals", "rows", "high", "review", "low", "excluded",
            "auto_hit", "false_green", "review_hit", "low_hit", "miss",
            "noise_low", "false_pos")
    a = {k: 0 for k in keys}
    for v in vids:
        m = per_field.get(v)
        if not m:
            continue
        for k in keys:
            a[k] += m[k]
    a["precision"] = a["auto_hit"] / a["high"] if a["high"] else 0.0
    a["recall"] = a["auto_hit"] / a["goals"] if a["goals"] else 0.0
    return a


def build_baseline():
    base = {}
    for name, tag in ARMS:
        base[name] = {}
        for v in OFFICIAL:
            p = os.path.join(YOLO, "eval_out", tag, v, "events.json")
            if os.path.exists(p):
                base[name][v] = field_metrics(v, p)
    return base


def verify_kpi(base):
    """与 v18 KPI json 逐场对拍（四数 + 逐场 auto_hit/high/false_green）。"""
    ref_path = os.path.join(YOLO, "reports", "KPI_官方口径_v18.json")
    ref = rd(ref_path)
    diffs = []
    for name, tag in ARMS:
        for v in OFFICIAL:
            mine = base[name].get(v)
            theirs = ref.get(tag, {}).get(v)
            if mine is None or theirs is None:
                diffs.append(f"{tag}/{v}: 缺失（mine={mine is not None}, "
                             f"ref={theirs is not None}）")
                continue
            for k in ("auto_hit", "high_total", "false_green", "goals", "rows"):
                a, b = mine.get("high" if k == "high_total" else k), theirs.get(k)
                if a != b:
                    diffs.append(f"{tag}/{v}.{k}: 复算 {a} ≠ 参考 {b}")
    v6 = [v for v in OFFICIAL if v != "2026-09-19_1"]
    tot = {}
    for name, _tag in ARMS:
        a6 = agg(base[name], v6)
        tot[name] = {"auto_hit": a6["auto_hit"], "high": a6["high"],
                     "recall": a6["recall"], "precision": a6["precision"],
                     "miss": a6["miss"], "goals": a6["goals"]}
    # 验收四数（c03_s1 六场）
    c = tot["c03_s1"]
    checks = {
        "自动档 1051": c["high"] == 1051,
        "命中 639": c["auto_hit"] == 639,
        "查全 82.9%": abs(c["recall"] - 0.829) < 0.0005,
        "查准 60.8%": abs(c["precision"] - 0.608) < 0.0005,
    }
    return diffs, tot, checks


# ------------------------------------------------------- 2 active 复检匹配
def v18_events(video):
    """返回 (events, source, note)；source 说明事件来自哪条臂/什么逻辑版本。"""
    for tag in ("v18_c03_s1", "v18_reverse"):
        p = os.path.join(YOLO, "eval_out", tag, video, "events.json")
        if os.path.exists(p):
            doc = rd(p)
            if doc.get("logic_ver") != 18:
                continue
            note = ("logic_ver=18（c03_s1 官方臂）" if tag == "v18_c03_s1"
                    else "logic_ver=18（同源点流重放）")
            return doc["events"], "eval_out/" + tag, note
    p = os.path.join(UP, "work", video, "events.json")
    if os.path.exists(p):
        doc = rd(p)
        lv = doc.get("logic_ver")
        if lv == 18:
            return doc["events"], "work/events.json", \
                f"logic_ver=18（生产默认检测器，非 c03_s1 臂）"
        return None, "work/events.json", f"logic_ver={lv}（非 v18，需重放）"
    return None, "-", "无 events.json"


def v18_path(video):
    """该场 v18 事件的路径（含重放产物）；没有则 None。"""
    for tag in ("v18_c03_s1", "v18_reverse"):
        p = os.path.join(YOLO, "eval_out", tag, video, "events.json")
        if os.path.exists(p) and rd(p).get("logic_ver") == 18:
            return p
    p = os.path.join(UP, "work", video, "events.json")
    if os.path.exists(p) and rd(p).get("logic_ver") == 18:
        return p
    return None


def check_case(case, events):
    """→ dict(status, active, note, matched...)"""
    t = float(case["t"])
    near = sorted([e for e in events if abs(e["t"] - t) <= WIN],
                  key=lambda e: abs(e["t"] - t))
    nearest = min(events, key=lambda e: abs(e["t"] - t)) if events else None
    nearest_d = round(abs(nearest["t"] - t), 2) if nearest else None
    kind = case["kind"]
    out = {"kind": kind, "match_n": len(near), "nearest_d": nearest_d,
           "t_v18": round(near[0]["t"], 3) if near else None}

    if kind == "fp":
        hi = [e for e in near if e["status"] == "high"]
        if hi:
            out.update(active=True, verdict="active",
                       reason="v18 仍 high（假绿仍在）", event=hi[0])
        elif near:
            out.update(active=False, verdict="fixed",
                       reason="v18 已降档（%s）" % near[0]["status"])
        else:
            out.update(active=False, verdict="fixed",
                       reason="v18 无该行（已不报）")
    elif kind == "flip":
        hi = [e for e in near if e["status"] == "high"]
        bad = [e for e in near if e["status"] in ("low", "review")]
        if hi:
            out.update(active=False, verdict="fixed",
                       reason="v18 已判 high（已修复）")
        elif bad:
            out.update(active=True, verdict="active",
                       reason="v18 仍 %s（真进球仍被降级）" % bad[0]["status"],
                       event=bad[0])
        else:
            out.update(active=True, verdict="active",
                       reason="v18 无该行（真进球完全没浮表）")
    else:  # miss
        if near:
            out.update(active=False, verdict="fixed",
                       reason="v18 已浮表（%s）" % near[0]["status"])
        else:
            out.update(active=True, verdict="active",
                       reason="v18 仍无该行（真漏检）")
    return out


def build_active(cases):
    by_field = {}
    for v in {c["field"] for c in cases}:
        ev, src, note = v18_events(v)
        by_field[v] = {"source": src, "note": note, "events": ev}
    records = []
    for c in cases:
        info = by_field[c["field"]]
        rec = {"field": c["field"], "kind": c["kind"], "t": c["t"],
               "t_str": c["t_str"], "marked_at": c["marked_at"],
               "evidence_at_mark": c["evidence"],
               "v18_source": info["source"], "v18_note": info["note"]}
        if info["events"] is None:
            rec.update(active=None, verdict="unknown",
                       reason="缺 v18 事件（%s）" % info["note"])
        else:
            rec.update(check_case(c, info["events"]))
        records.append(rec)
    return records, by_field


def stratified_split(records):
    """调参集 active fp 按场分层 7:3（固定种子，Step 1 沿用同一划分）。"""
    import random
    rng = random.Random(SPLIT_SEED)
    tune, val = [], []
    for f in TUNE_FIELDS:
        idx = [i for i, r in enumerate(records)
               if r["field"] == f and r["kind"] == "fp"
               and r.get("active") is True]
        idx.sort()
        rng.shuffle(idx)
        k = int(round(len(idx) * SPLIT_TUNE))
        tune += idx[:k]
        val += idx[k:]
    return set(tune), set(val)


# --------------------------------------------------------- 3 truth 派生
def reverse_baseline(video, events_path, truth_path):
    """反作用监测基线：真进球在 v18 下落在哪一档（只测不调，绝对数不与 KPI 混用）。

    返回 {goals, high, review, low, missing, tol}；truth 为部分标注时只做
    回归断言（confirmed_goals 必须仍浮表），不算档位指标。
    """
    doc = rd(events_path)
    events = doc["events"]
    deleted = EV._load_deleted_seconds(os.path.join(UP, "work", video,
                                                    "events.json"))
    inter = EV.INTERMISSION_GOALS.get(video, [])
    drop = EV._deleted_exclusions([e["t"] for e in events], deleted + inter)
    kept = [e for i, e in enumerate(events) if i not in drop]
    truth = rd(truth_path)
    tol = truth.get("tolerance", EV.TOLERANCE)
    if truth.get("complete") is False:
        r = EV.regression_report(truth["confirmed_goals"],
                                 [(e["t"], e["status"]) for e in kept],
                                 tolerance=tol)
        return {"partial": True, "goals": r["确认进球"], "on_table": r["浮表"],
                "tiers": r["档位"], "lost": len(r["丢失"]), "tol": tol,
                "logic_ver": doc.get("logic_ver")}
    goals = sorted(truth["goals"])
    used = set()
    tiers = {"high": 0, "review": 0, "low": 0, "missing": 0}
    for g in goals:
        i = next((k for k, e in enumerate(kept)
                  if k not in used and abs(e["t"] - g) <= tol), None)
        if i is None:
            tiers["missing"] += 1
        else:
            used.add(i)
            tiers[kept[i]["status"]] += 1
    return {"partial": False, "goals": len(goals), "tiers": tiers, "tol": tol,
            "logic_ver": doc.get("logic_ver")}


def derive_truth(video):
    sel_path = os.path.join(UP, "work", video, "selection.json")
    ev_path = os.path.join(UP, "work", video, "events.json")
    if not os.path.exists(sel_path):
        return None, "无 selection.json"
    sel = rd(sel_path)
    goals, bad = [], 0
    for item in sel:
        try:
            goals.append(round(parse_ts(item.get("t") if isinstance(item, dict)
                                        else item), 3))
        except (TypeError, ValueError):
            bad += 1
    goals = sorted(set(goals))
    doc = rd(ev_path)
    ev_ts = [e["t"] for e in doc["events"]]
    # 体检：真值点能否在 0.2s 内认回任一行（认不回 = 时间轴可能不一致，
    # 或该球确实漏检——两者要分开看，先给出比例）
    hit = sum(1 for g in goals if any(abs(g - t) <= 0.2 for t in ev_ts))
    out = {
        "video": video,
        "source_video": doc.get("video"),
        "calib_key": doc.get("calib_key"),
        "duration": doc.get("duration"),
        "annotated_at": date.today().isoformat(),
        "logic_ver_at_annotation": doc.get("logic_ver"),
        "complete": True,
        "goals": goals,
        "tolerance": 0.2,
        "derived_from": "work/%s/selection.json" % video,
        "note": ("由剪辑台 selection.json 派生（R-020 Step 0）。selection 即最终"
                 "进球清单，与 truth/<场>.json 的 goals 同义；tolerance 0.2 为"
                 "行锚定口径。体检：%d/%d 个真值点能在 0.2s 内认回 v%s 事件。"
                 % (hit, len(goals), doc.get("logic_ver"))),
    }
    p = os.path.join(OUT, "truth_derived", video + ".json")
    wr(p, out)
    return out, {"n": len(goals), "bad": bad, "anchor_hit": hit,
                 "anchor_rate": hit / len(goals) if goals else 0.0,
                 "path": p}


# ------------------------------------------------------------------- 报告
def main():
    cases = rd(os.path.join(YOLO, "reports", "hardcases_v18.json"))["cases"]
    base = build_baseline()
    diffs, tot, checks = verify_kpi(base)
    records, by_field = build_active(cases)
    tune, val = stratified_split(records)

    # 统计
    def cnt(fields=None, kind=None, active=True):
        return sum(1 for r in records
                   if (fields is None or r["field"] in fields)
                   and (kind is None or r["kind"] == kind)
                   and r.get("active") is active)
    official_active_fp = cnt(OFFICIAL, "fp")
    tune_active_fp = cnt(TUNE_FIELDS, "fp")
    holdout_active_fp = cnt([HOLDOUT_FIELD], "fp")
    # 可达上限：A = 官方 6 场 active fp（剔 09-19_1）
    six = [v for v in OFFICIAL if v != "2026-09-19_1"]
    A6 = cnt(six, "fp")
    cap = 639.0 / (1051 - A6) if A6 < 1051 else None

    result = {
        "generated_at": date.today().isoformat(),
        "window_sec": WIN,
        "split_seed": SPLIT_SEED,
        "split_ratio": SPLIT_TUNE,
        "kpi_verify": {"diffs": diffs, "checks": checks,
                       "aggregate_6": tot,
                       "pass": not diffs and all(checks.values())},
        "baseline": base,
        "counts": {
            "cases_total": len(cases),
            "official_active": {k: cnt(OFFICIAL, k) for k in
                                ("fp", "flip", "miss")},
            "official_active_fp": official_active_fp,
            "tune_active_fp": tune_active_fp,
            "holdout_active_fp": holdout_active_fp,
            "active_fp_6fields": A6,
            "cap_precision": cap,
            "unknown_no_v18": sum(1 for r in records
                                  if r.get("active") is None),
        },
        "split": {"tune": sorted(tune), "holdout": sorted(val)},
        "records": records,
    }
    wr(os.path.join(OUT, "active_cases.json"), result)

    # 派生 truth（09-24_1 / 10-16_1）
    derived = {}
    for v in ["2026-09-24_1", "2026-10-16_1"]:
        _doc, info = derive_truth(v)
        derived[v] = info

    # 反作用监测基线（只测不调）
    rbase = {}
    truth_of = {"2026-10-10_1": os.path.join(UP, "truth", "2026-10-10_1.json"),
                "2026-09-24_1": os.path.join(OUT, "truth_derived",
                                             "2026-09-24_1.json"),
                "2026-10-16_1": os.path.join(OUT, "truth_derived",
                                             "2026-10-16_1.json"),
                "2026-08-21_1": os.path.join(UP, "truth", "2026-08-21_1.json")}
    for v in REVERSE_FIELDS:
        ep, tp = v18_path(v), truth_of.get(v)
        if not ep or not tp or not os.path.exists(tp):
            rbase[v] = {"skipped": "缺 v18 事件或真值"}
            continue
        rbase[v] = reverse_baseline(v, ep, tp)
    result["reverse_baseline"] = rbase
    wr(os.path.join(OUT, "active_cases.json"), result)

    # ---- 结论 md
    L = []
    A = L.append
    A("# Step 0 复检结论 · R-020 判定层 hard-case 调优")
    A("")
    A("- 日期：%s　口径：v18（LOGIC_VERSION 18）" % date.today().isoformat())
    A("- 说明：**只读诊断**，未改生产代码 / 未碰权重 / 未改数据集")
    A("- 匹配窗：±%.1fs；v18 事件来源：官方 7 场用 `eval_out/v18_c03_s1`，"
      "10-10_1 / 10-16_1 用 `work/events.json`（logic_ver=18）" % WIN)
    A("")
    A("## 1 基线复算验收（离线复算器前置门）")
    A("")
    if not diffs and all(checks.values()):
        A("**✅ 全部对上**：逐场 auto_hit / high_total / false_green / goals / rows "
          "与 `reports/KPI_官方口径_v18.json` 完全一致；六场四数 1051 / 639 / "
          "82.9% / 60.8% 全部复现。**离线复算器前置门通过。**")
    else:
        A("**❌ 存在偏差，须先修**：")
        for d in diffs[:20]:
            A("- " + d)
        for k, v in checks.items():
            A("- %s：%s" % (k, "✅" if v else "❌"))
    A("")
    A("| 场 | 球 | 自动档 | 命中 | 假绿 | 查准 | 查全 | 🟡复核 | ⚪低置信 | 剔除行 |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for v in OFFICIAL:
        m = base["c03_s1"].get(v)
        if not m:
            A("| %s | — | — | — | — | — | — | — | — | — |" % v)
            continue
        A("| %s | %d | %d | %d | %d | %.1f%% | %.1f%% | %d | %d | %d |"
          % (v, m["goals"], m["high"], m["auto_hit"], m["false_green"],
             100 * m["precision"], 100 * m["recall"], m["review"], m["low"],
             m["excluded"]))
    c = tot["c03_s1"]
    A("| **6 场（剔 09-19）** | %d | %d | %d | %d | **%.1f%%** | **%.1f%%** | — | — | — |"
      % (c["goals"], c["high"], c["auto_hit"], c["high"] - c["auto_hit"],
         100 * c["precision"], 100 * c["recall"]))
    A("")
    A("## 2 反馈复检（v18 下是否仍判错）")
    A("")
    A("| 场 | 角色 | fp 总/active | flip 总/active | miss 总/active | v18 事件来源 |")
    A("|---|---|---|---|---|---|")
    role = {}
    for v in OFFICIAL:
        role[v] = "调参" if v in TUNE_FIELDS else (
            "留出" if v == HOLDOUT_FIELD else "排除")
    for v in REVERSE_FIELDS:
        role[v] = "反向监测"
    order = TUNE_FIELDS + [HOLDOUT_FIELD] + REVERSE_FIELDS + \
        [v for v in OFFICIAL if v == "2026-09-19_1"]
    for v in order:
        def pair(k):
            tot_n = sum(1 for r in records if r["field"] == v and r["kind"] == k)
            act = sum(1 for r in records if r["field"] == v
                      and r["kind"] == k and r.get("active") is True)
            unk = sum(1 for r in records if r["field"] == v
                      and r["kind"] == k and r.get("active") is None)
            return ("%d/%d" % (tot_n, act)) + ("（%d 待重放）" % unk if unk else "")
        A("| %s | %s | %s | %s | %s | %s |"
          % (v, role.get(v, "跳过"), pair("fp"), pair("flip"), pair("miss"),
             by_field.get(v, {}).get("note", "-")))
    A("")
    A("**关键裁决数字**：")
    A("")
    A("- 官方 6 场（剔 09-19）active fp = **%d %s**" %
      (A6, "（≥30，达到调参可信度下限）" if A6 >= 30 else "（<30，**低于止损线**）"))
    A("- 调参集 active fp = **%d**　留出场 08-29_3 active fp = **%d**" %
      (tune_active_fp, holdout_active_fp))
    if cap:
        A("- 查准可达上限 `cap = 639/(1051−%d) = %.1f%%` → 主门基线取 "
          "`min(65.8%%, cap−1pct) = %.1f%%`" % (A6, 100 * cap, 100 * min(0.658, cap - 0.01)))
    A("")
    A("## 3 划分（按场分层 7:3，种子 %d）" % SPLIT_SEED)
    A("")
    A("| 角色 | 场次 | active fp |")
    A("|---|---|---|")
    for f in TUNE_FIELDS:
        n = sum(1 for i in tune if records[i]["field"] == f)
        m = sum(1 for i in val if records[i]["field"] == f)
        A("| 调参/同场验证 | %s | %d 调参 + %d 验证 |" % (f, n, m))
    A("| 陌生场留出 | %s | %d（只验一次） |" % (HOLDOUT_FIELD, holdout_active_fp))
    A("| 排除 | 2026-09-19_1 | —（崩场） |")
    A("")
    A("## 4 truth 派生（反作用监测用）")
    A("")
    A("| 场 | 结果 |")
    A("|---|---|")
    for v, info in derived.items():
        if info is None:
            A("| %s | ❌ 跳过 |" % v)
        else:
            A("| %s | ✅ %d 球，写 `truth_derived/%s.json`；0.2s 认回率 %.1f%% |"
              % (v, info["n"], v, 100 * info["anchor_rate"]))
    A("")
    A("## 5 反作用监测基线（只测不调，绝对数不与官方 KPI 混用）")
    A("")
    A("「真进球在 v18 下落在哪一档」——这就是翻案的母数。改完规则后同一份点流"
      "再跑一次，若 review/low/未浮表 变多，就是拿查全换查准。")
    A("")
    A("| 场 | 真进球 | 🟢high | 🟡review | ⚪low | 未浮表 | 事件口径 |")
    A("|---|---|---|---|---|---|---|")
    for v in REVERSE_FIELDS:
        r = rbase.get(v) or {}
        if r.get("skipped"):
            A("| %s | — | — | — | — | — | %s |" % (v, r["skipped"]))
        elif r.get("partial"):
            A("| %s | %d（部分标注） | 浮表 %d（🟢%d 🟡%d ⚪%d） | — | — | 丢失 %d | "
              "logic_ver=%s |" % (v, r["goals"], r["on_table"],
                                  r["tiers"]["high"], r["tiers"]["review"],
                                  r["tiers"]["low"], r["lost"], r["logic_ver"]))
        else:
            t = r["tiers"]
            A("| %s | %d | %d | %d | %d | %d | logic_ver=%s |"
              % (v, r["goals"], t["high"], t["review"], t["low"], t["missing"],
                 r["logic_ver"]))
    A("")
    A("## 6 与方案假设不一致的事实（已按实况修正）")
    A("")
    A("1. **08-07_1 无 truth**（不只是无点流）：`truth/` 与 `work/` 都没有可用标注，"
      "其 9 fp / 17 flip 无法任何定量使用 → 整场跳过。")
    A("2. **08-21_1 的 truth 是部分标注**（`complete=false`，114 个 confirmed_goals）："
      "只能做回归断言（确认进球必须仍浮表），**禁止计算查准/查全**——27 条 fp 也"
      "因此不进定量调参，只作形态参考（与 grill 裁定一致）。")
    A("3. **08-21_1 无法低成本拿 v18 基线**：该场没有 `calibration.json`，标定只能由"
      "`positions.json` 兜底重建，重建出的指纹与点流块不匹配 → 段块复用必然未命中，"
      "触发**全片 HSV 补扫**（16 万帧，约 1 小时）。而其价值仅 5 条 flip + 部分标注，"
      "**本轮已中止该重放，跳过 08-21_1**。")
    A("4. **09-24_1 已完成 v18 重放**（`eval_out/v18_reverse`，275s，未触发重扫）："
      "logic_ver=18，237 事件（🟢110 🟡62 ⚪65）。")
    A("5. **10-10_1 / 10-16_1 的 work/events.json 已是 logic_ver=18**，但其检球器是"
      "生产默认（非 c03_s1 评测臂）→ 只用于反作用监测的相对对比。")
    A("")
    A("## 7 产物")
    A("")
    A("- `active_cases.json`：%d 条逐条复检结果（含 v18 匹配事件的 12 个判定特征，"
      "Step 1 直接读）" % len(records))
    A("- `truth_derived/*.json`：09-24_1 / 10-16_1 的派生真值（不写上游仓）")
    A("- 本报告")
    md = "\n".join(L)
    with io.open(os.path.join(OUT, "Step0复检结论.md"), "w", encoding="utf-8",
                 newline="\n") as f:
        f.write(md)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
