# -*- coding: utf-8 -*-
r"""自包含评测库：给「视频剪辑YOLO」自己产出的 events.json 打三种口径的分。

为什么要三种口径
----------------
`truth\*.json` 的进球时刻是当年**在 HSV 检出表上逐行勾出来的**（行锚定），
自带容差 0.2s。这让「官方口径」这把尺子天然偏 HSV：

  · 官方口径（A）——原项目 `tools/evaluate.py` 的裁判，就是平时验收那个数。
      容差 0.2s 行锚定；查全 = 自动档命中 / 标注进球；查准 = 自动档命中 /
      自动档总行数；评测前剔除 deleted.json（剪辑台删球）+ 比赛间隙球。
      **任何时刻与 HSV 不同源的检测器都会额外掉点** → 它是「过不过现行门」
      的下界，不是「谁更准」。
  · 对称口径（B）——每条臂只用自己的事件时刻，±tol 双向 1:1 最近优先配对。
      看见率 / 🟢率 / 🟢+🟡率。不再要求落回 HSV 那一行，去掉「整体时刻平移」
      被直接算成错的问题（但「哪些球算进球」这份集合仍来自 HSV 输出表）。
  · 容差扫描（C）——把时刻偏差当作已被纠正，扫容差 W 看效果。
      两套规则：官方裁判（保留贪心副作用）/ 最邻近 1:1（更干净）。

三者的分母、容差、剔除规则都不同，**数字不可互相搬运**。

第四套：查准回归集（2026-11-10 起）
----------------------------------
`truth/` 里有 6 个**派生**真值（`truth_kind == "selection"`），来自剪辑台
`selection.json`（`tools/derive_truth.py`）。它们的性质与老七场不同：

  · selection 是**从当版 events 表勾出来的** → truth ⊆ events ⇒ **漏检恒 0**。
    所以这些场**不能**算查全（分母里没有「表里根本没出现的球」）。
  · 能算的是**查准**（high 里多少是用户没勾的）和**自动档覆盖率**
    （真球里多少落进 high = auto_hit / goals）。

老七场的 truth 来自当年的 HSV 检出表（异源），YOLO 漏掉的球仍在真值里，
所以查全只能看老七场。**两套数字永远分组报告、不混算**——混算会让查全
凭空 +N pct，是最容易犯的错。

补充真球（`truth/extra_goals.json`，2026-11-11 起）
--------------------------------------------------
v19→v20 的冷却身份判据新增 75 行，其中 48 条进了 high 档但与 HSV 行锚定
truth 对不上。用户裁定：**不逐条核验，直接认定这 48 条是真进球**，并入标准
答案。为了不污染原始 truth（历史 KPI 全靠它复现），单独存一个补丁表，
`load_truth()` 默认合并；`load_truth(v, with_extra=False)` 可关掉。

⚠ 这批未核验。若日后核验发现其中有假球，删掉该文件即回原口径。
用它的数字必须标注「含补充真球 48 条」，与历史数字**不可直接混比**。

第五套：用户标注口径（2026-11-14 起，见 `tools/eval_sel.py`）
------------------------------------------------------------
前四套的进球清单**都来自 truth 行锚定表**——本质是「与 HSV 一致」。但实测
322 条 high 假绿（HSV 定义）与 222 条用户 `not_goal`（肉眼定义）**只重叠
55 条（17%）**：查准一直没对准「用户满意不满意」。

第五套改用剪辑台 `selection.json`（用户逐条勾过的球单）当真值，独立成节，
场次 `eval_sel.VIDEOS_SEL`（老六 + 新六）。它把「系统没给自动档」拆成
`degraded`（落 review/low，人工补勾）和 `missing`（完全无行）两条可见去向，
比查准/查全更有行动价值。**与第 0/4 节不可比、不可相加**（同一批场、
不同标准答案）。

自包含
------
本库只依赖本项目内的 `tools/evaluate.py`（官方裁判）与 `truth/`——不 import
`bbh`、不碰任何外部工程。产物写本项目 `reports/`。
"""
import json
import os
import sys

# projects root = 本文件（tools/eval_lib.py）的上一级
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(PROJECT_ROOT, "tools") not in sys.path:
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "tools"))
import evaluate as ev                                             # noqa: E402

# 标准测试场次（8 场完整标注里 08-21_1 complete=false，不算查全/查准，未纳入）
VIDEOS = ["2026-08-22_1", "2026-08-28_1", "2026-08-29_3",
          "2026-09-04_1", "2026-09-11_3", "2026-09-12_1",
          "2026-09-19_1"]
SPLIT = {"2026-08-22_1": "train", "2026-08-28_1": "train",
         "2026-09-12_1": "train", "2026-09-19_1": "train",
         "2026-09-04_1": "val", "2026-09-11_3": "val",
         "2026-08-29_3": "test"}
SPLIT_ORDER = ["train", "val", "test"]

# 查准回归集：truth 由剪辑台 selection.json 派生（truth_kind="selection"）。
# 只算查准 / 自动档覆盖率，**不算查全**（漏检结构上恒 0）。
# 2026-11-07_1 用户裁定不入库（该场自动档覆盖仅 61.8%，最差，留作只监测）；
# 2026-11-14 用户裁定 10-10_1 / 10-16_1 也不入库：这两场的 selection 是对
# **上游 HSV 版界面**勾的（锚 HSV 事件行），重跑 YOLO v22 后事件时刻漂移
# 中位 0.14~0.19s、p90 0.5s，0.2s 容差只认回 62%/55%，会把 41% 的真球误判成
# 「系统没报」。详见 `诊断/2026-11-14-旧场重跑口径/结论.md`。
# 2025-06-28_1 是 7 球冒烟场，不纳入统计。
VIDEOS_PREC = ["2026-10-17_1", "2026-10-23_1", "2026-10-24_1",
               "2026-10-30_1", "2026-10-31_1", "2026-11-06_1"]
PREC_ONLY = set(VIDEOS_PREC)
for _v in VIDEOS_PREC:
    SPLIT.setdefault(_v, "prec")

TIER_ORDER = ["high", "review", "low"]
RANK = {"high": 0, "review": 1, "low": 2}


def truth_dir(root=None):
    return os.path.join(root or PROJECT_ROOT, "truth")


# ------------------------------------------------------------------ 读数据
def load_events(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)["events"]


EXTRA_GOALS_FILE = "extra_goals.json"


def load_extra_goals(truth_root=None):
    """→ {video: [t, ...]}，补充真球表（`truth/extra_goals.json`）。没有就空 dict。

    性质：这是一份**人工裁定、未经视频核验**的补丁（2026-11-11 用户决定把 v20
    新增的 48 条 high 行直接当真球）。单独成表而不改 `truth/*.json`，是为了让
    历史 KPI 仍可复现——删掉该文件即回原口径。读到的场次会被并入 `load_truth`。
    """
    p = os.path.join(truth_dir(truth_root), EXTRA_GOALS_FILE)
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}
    for v, ts in (doc.get("goals") or {}).items():
        if ts:
            out[v] = [float(t) for t in ts]
    return out


_EXTRA_CACHE = {}


def extra_goals_cached(truth_root=None):
    """补充真球表的进程内缓存（按 truth 根）。"""
    key = truth_root or PROJECT_ROOT
    if key not in _EXTRA_CACHE:
        _EXTRA_CACHE[key] = load_extra_goals(truth_root)
    return _EXTRA_CACHE[key]


def load_truth(video, truth_root=None, with_extra=True):
    """→ (goals_sorted, tolerance)；complete=false 的场次 → (None, None)。

    `with_extra=False` 可关掉补充真球（用于对账/复现旧口径）。
    """
    p = os.path.join(truth_dir(truth_root), video + ".json")
    if not os.path.exists(p):
        return None, None
    with open(p, encoding="utf-8") as f:
        tr = json.load(f)
    if not tr.get("complete"):
        return None, None
    goals = list(tr["goals"])
    if with_extra:
        goals.extend(extra_goals_cached(truth_root).get(video, []))
    return sorted(goals), tr.get("tolerance", ev.TOLERANCE)


def load_rows(events_path, video, deleted_from=None):
    """→ [(t, status)]，已按 deleted.json + 比赛间隙球剔除。

    deleted_from：work 根目录（`<root>/<video>/deleted.json`）。不给则用
    events.json 同目录找（arm 目录结构 `<root>/<video>/events.json` 亦然）。
    """
    events = load_events(events_path)
    del_dir = (os.path.join(deleted_from, video) if deleted_from
               else os.path.dirname(os.path.abspath(events_path)))
    deleted = ev._load_deleted_seconds(os.path.join(del_dir, "events.json"))
    inter = ev.INTERMISSION_GOALS.get(video, [])
    if deleted or inter:
        drop = ev._deleted_exclusions([e["t"] for e in events],
                                      deleted + inter)
        events = [e for i, e in enumerate(events) if i not in drop]
    return [(e["t"], e["status"]) for e in events]


# ------------------------------------------------------------------ A 官方口径
def official_one(goals, rows, tol):
    """单个场次的官方口径指标 dict。"""
    rep = ev.match_report(goals, rows, tolerance=tol)
    ht = sum(1 for _t, s in rows if s == "high")
    m = ev.auto_metrics(rep, ht)
    return {"goals": len(goals), "rows": len(rows),
            "auto_hit": m["自动档命中"], "precision": m["查准"],
            "recall": m["查全"], "false_green": m["假绿"], "high_total": ht,
            "review_hit": rep["待确认中"], "low_hit": rep["命中低置信"],
            "miss": rep["漏检"], "noise_low": rep["低噪"],
            "false_pos": rep["误报"], "tol": tol}


_OFF_KEYS = ("goals", "rows", "auto_hit", "high_total", "false_green",
             "review_hit", "low_hit", "miss", "noise_low", "false_pos")


def official_agg(per_video, vids):
    """per_video: {video: 指标dict|None} → 汇总（含 recall/precision/门）。"""
    a = {k: 0 for k in _OFF_KEYS}
    for v in vids:
        m = per_video.get(v)
        if not m:
            continue
        for k in _OFF_KEYS:
            a[k] += m[k]
    return _finish_official(a)


def _finish_official(a):
    a["recall"] = a["auto_hit"] / a["goals"] if a["goals"] else 0.0
    a["precision"] = a["auto_hit"] / a["high_total"] if a["high_total"] else 0.0
    a["miss_rate"] = a["miss"] / a["goals"] if a["goals"] else 0.0
    a["manual"] = a["review_hit"] + a["low_hit"]
    if not a["goals"]:
        a["gate"] = "—"
    else:
        a["gate"] = ("过" if (a["miss_rate"] <= 0.15
                             and a["false_pos"] <= 3 * max(a["auto_hit"], 1))
                     else "不过")
    return a


# ------------------------------------------------------------------ B 对称口径
def loose_match(goals, detected, tol):
    """双向 1:1 最近优先配对 → [(g, tier, dt)]（只含配上的；dt=事件−真值，正=晚）。"""
    gs = sorted(goals)
    pairs = sorted((abs(t - g), i, j)
                   for i, g in enumerate(gs)
                   for j, (t, _s) in enumerate(detected) if abs(t - g) <= tol)
    ug, ud, out = set(), set(), []
    for _d, i, j in pairs:
        if i in ug or j in ud:
            continue
        ug.add(i)
        ud.add(j)
        out.append((gs[i], detected[j][1], detected[j][0] - gs[i]))
    return out


def sym_one(goals, rows, tol):
    """→ 计数 dict（按档位 + paired/miss/n + dts）。"""
    paired = loose_match(goals, rows, tol)
    c = {k: 0 for k in TIER_ORDER}
    for _g, tier, _dt in paired:
        c[tier] += 1
    c["paired"] = len(paired)
    c["n"] = len(goals)
    c["miss"] = len(goals) - len(paired)
    c["dts"] = [dt for _g, _s, dt in paired]
    return c


def sym_add(a, b):
    if a is None:
        a = {k: 0 for k in TIER_ORDER + ["paired", "n", "miss"]}
        a["dts"] = []
    for k in TIER_ORDER + ["paired", "n", "miss"]:
        a[k] = a.get(k, 0) + b.get(k, 0)
    a["dts"] = a.get("dts", []) + b.get("dts", [])
    return a


def sym_agg(per_video, vids):
    out = None
    for v in vids:
        if v in per_video:
            out = sym_add(out, per_video[v])
    return out


def rate(c, key):
    return (c[key] / c["n"]) if c and c.get("n") else 0.0


# ------------------------------------------------------------------ C 容差扫描
def nearest_one(goals, rows, tol):
    """最邻近 1:1（距离升序认领，同距优先高档）→ 指标 dict。"""
    pairs = sorted(
        (round(abs(g - t), 6), RANK.get(s, 3), gi, ri)
        for gi, g in enumerate(goals)
        for ri, (t, s) in enumerate(rows)
        if abs(g - t) <= tol)
    used_g, used_r = set(), set()
    for _dist, _rk, gi, ri in pairs:
        if gi in used_g or ri in used_r:
            continue
        used_g.add(gi)
        used_r.add(ri)
    auto = sum(1 for ri in used_r if rows[ri][1] == "high")
    ht = sum(1 for _t, s in rows if s == "high")
    a = {"goals": len(goals), "rows": len(rows), "auto_hit": auto,
         "high_total": ht, "false_green": ht - auto, "miss": len(goals) - len(used_g),
         "manual": len(used_r) - auto}
    return _finish_scan(a)


def _finish_scan(a):
    a["recall"] = a["auto_hit"] / a["goals"] if a["goals"] else 0.0
    a["precision"] = a["auto_hit"] / a["high_total"] if a["high_total"] else 0.0
    a["miss_rate"] = a["miss"] / a["goals"] if a["goals"] else 0.0
    a["gate"] = ("—" if not a["goals"]
                 else ("过" if a["miss_rate"] <= 0.15 else "不过"))
    return a


def nearest_agg(rows_by_video, goals_by_video, vids, tol):
    a = {"goals": 0, "rows": 0, "auto_hit": 0, "high_total": 0,
         "false_green": 0, "miss": 0, "manual": 0}
    for v in vids:
        if v not in goals_by_video or goals_by_video[v] is None:
            continue
        if v not in rows_by_video:      # 产物缺失的场次跳过（跨根评测时可能发生）
            continue
        m = nearest_one(goals_by_video[v], rows_by_video[v], tol)
        for k in a:
            a[k] += m[k]
    return _finish_scan(a)


def official_agg_tol(rows_by_video, goals_by_video, vids, tol):
    """官方裁判规则下的容差扫描（对照用，保留贪心副作用）。"""
    a = {"goals": 0, "rows": 0, "auto_hit": 0, "high_total": 0,
         "false_green": 0, "miss": 0, "manual": 0, "false_pos": 0}
    for v in vids:
        if v not in goals_by_video or goals_by_video[v] is None:
            continue
        if v not in rows_by_video:
            continue
        m = official_one(goals_by_video[v], rows_by_video[v], tol)
        for k in a:
            a[k] += m[k]
    a = _finish_scan(a)
    a["gate"] = ("—" if not a["goals"] else
                 ("过" if (a["miss_rate"] <= 0.15
                           and a["false_pos"] <= 3 * max(a["auto_hit"], 1))
                  else "不过"))
    return a


def offset_profile(rows_by_video, goals_by_video, vids):
    """每个标注球 → 最近一条事件的距离分桶 + 0.2~0.5s 档的档位构成 + 逐场。"""
    buckets = {"<=0.1": 0, "<=0.2": 0, "<=0.3": 0, "<=0.5": 0, ">0.5": 0}
    mid_tier = {"high": 0, "review": 0, "low": 0}
    mid_arc, far_arc = {}, {}
    for v in vids:
        if v not in goals_by_video or goals_by_video[v] is None:
            continue
        if v not in rows_by_video:
            continue
        rows = rows_by_video[v]
        n_mid = n_far = 0
        for g in goals_by_video[v]:
            cand = [(abs(g - t), s) for t, s in rows]
            if not cand:
                buckets[">0.5"] += 1
                n_far += 1
                continue
            dist, status = min(cand, key=lambda x: x[0])
            for k, lim in (("<=0.1", 0.1), ("<=0.2", 0.2), ("<=0.3", 0.3),
                           ("<=0.5", 0.5)):
                if dist <= lim:
                    buckets[k] += 1
                    break
            if dist > 0.5:
                buckets[">0.5"] += 1
                n_far += 1
            elif dist > 0.2:
                mid_tier[status] = mid_tier.get(status, 0) + 1
                n_mid += 1
        mid_arc[v] = n_mid
        far_arc[v] = n_far
    return buckets, mid_tier, mid_arc, far_arc


# ------------------------------------------------------------------ D 查准回归集
def prec_one(goals, rows, tol):
    """单场「查准 + 自动档覆盖率」（truth 来自 selection 派生，漏检恒 0）。

    与 official_one 的区别：不把 miss 当指标（结构上不可能出现），而是把
    「真球没进 high」拆成两条可见的去向——落 review、落 low——这两条才是
    人工要捞的量。`miss` 仍返回，但只作**自检**用：>0 说明 selection 与
    events 不是同一版（派生错位），数字不可信。
    """
    rep = ev.match_report(goals, rows, tolerance=tol)
    ht = sum(1 for _t, s in rows if s == "high")
    m = ev.auto_metrics(rep, ht)
    goals_n = len(goals)
    return {"goals": goals_n, "rows": len(rows), "high_total": ht,
            "review": sum(1 for _t, s in rows if s == "review"),
            "low": sum(1 for _t, s in rows if s == "low"),
            "auto_hit": m["自动档命中"], "false_green": m["假绿"],
            "precision": m["查准"],
            "auto_cover": (m["自动档命中"] / goals_n) if goals_n else 0.0,
            "review_hit": rep["待确认中"], "low_hit": rep["命中低置信"],
            "miss": rep["漏检"], "noise_low": rep["低噪"],
            "manual": goals_n - m["自动档命中"] if goals_n else 0, "tol": tol}


_PREC_KEYS = ("goals", "rows", "high_total", "review", "low", "auto_hit",
              "false_green", "review_hit", "low_hit", "miss", "noise_low")


def prec_agg(per_video, vids):
    """查准回归集汇总。precision = 命中/high 总数；auto_cover = 命中/真值总数。"""
    a = {k: 0 for k in _PREC_KEYS}
    for v in vids:
        m = per_video.get(v)
        if not m:
            continue
        for k in _PREC_KEYS:
            a[k] += m[k]
    a["precision"] = a["auto_hit"] / a["high_total"] if a["high_total"] else 0.0
    a["auto_cover"] = a["auto_hit"] / a["goals"] if a["goals"] else 0.0
    a["manual"] = a["goals"] - a["auto_hit"]
    a["anchor_bad"] = a["miss"] > 0        # 自检：漏检应恒 0
    return a


# ------------------------------------------------------------------ 装配
def _pick_deleted_root(video, deleted_from, fallback_roots):
    """按场挑 deleted.json 所在的根：显式指定优先，否则按 fallback 找存在的。

    跨根评测时 events.json 与 deleted.json 往往不在同一目录（评测臂目录只有
    events，删球表留在标注的 work 里）。挑错根 = 删球没剔 → 假绿虚高。
    """
    cands = []
    if isinstance(deleted_from, str):
        cands.append(deleted_from)
    elif deleted_from:
        cands.extend(deleted_from)
    cands.extend(fallback_roots or [])
    for r in cands:
        if os.path.exists(os.path.join(r, video, "deleted.json")):
            return r
    return cands[0] if cands else None


def collect_arm(arm_root, vids, deleted_from=None, extra_roots=None):
    """扫 work 根目录 → {video: {rows, goals, official, ...}}。缺场记 None。

    为什么允许多个根：老七场的产物在**上游仓** `D:\\claude code\\视频剪辑\\work`，
    2026-11 起的场次在**本仓** `work`——一次评测要跨两个目录。`arm_root`
    先找，找不到再按 `extra_roots` 顺序找；`deleted.json` 默认跟 events.json
    同根（否则跨根时会去错目录找删球表，把假绿算多），显式传
    `deleted_from` 才覆盖。
    """
    roots = [arm_root] + [r for r in (extra_roots or []) if r != arm_root]
    out = {}
    for v in vids:
        found = None
        for r in roots:
            cand = os.path.join(r, v, "events.json")
            if os.path.exists(cand):
                found = (r, cand)
                break
        if not found:
            out[v] = None
            continue
        root_v, p = found
        goals, tol = load_truth(v)
        del_root = _pick_deleted_root(v, deleted_from, roots)
        have_del = bool(del_root
                        and os.path.exists(os.path.join(del_root, v,
                                                        "deleted.json")))
        rows = load_rows(p, v, deleted_from=del_root)
        n_extra = len(extra_goals_cached().get(v, []))
        out[v] = {"events_path": p, "rows": rows, "goals": goals, "tol": tol,
                  "official": official_one(goals, rows, tol) if goals else None,
                  "work_root": root_v, "deleted_root": del_root,
                  "deleted_found": have_del, "extra_n": n_extra}
    return out


def load_run_cfg(arm_root):
    """尽力从 run_log.json 读臂的实参（没有就算了）。"""
    p = os.path.join(arm_root, "run_log.json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return {}
    c = dict(d.get("cfg") or {})
    if c.get("weights"):
        c["weights"] = os.path.basename(str(c["weights"]))
    return c
