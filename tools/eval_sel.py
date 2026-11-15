# -*- coding: utf-8 -*-
r"""第五套口径：用户标注口径（selection）——「以用户亲手勾的结果为准」。

为什么要这套
------------
前四套口径的进球清单**都来自已有的 truth 行锚定表**（老七场是 HSV 检出表、
新六场是 selection 派生），两套标签的定义并不一致：

  · `truth/*.json` 说「这一行是不是进球」——本质是**与 HSV 一致**
  · `feedback.json` 说「我看了视频，这条不是进球」——是**用户满意不满意**

2026-11-11 实测：322 条 high 假绿（HSV 定义）与 222 条用户 `not_goal`（肉眼
定义）**只重叠 55 条（17%）**。也就是说查准一直在衡量「与 HSV 一致」，而不
是「用户满意」。本口径改用**用户勾选的球单**当真值，把尺子对准用户。

真值来源
--------
`<work 根>/<场>/selection.json`——剪辑台的最终球单。用户逐条勾过、删过，
就是这一场「用户认为进了的球」。18 场全部有，且 100% 能对回各自目录的
events 行（≤0.05s）。

**自洽性已验**（老六场 135 条 `not_goal` 里 0 条出现在 selection，0%）：
selection 不是「只勾了要剪的片段」，它是判定结果。

**与 deleted.json 零冲突**（12 场实测 0 条重叠），两者语义清晰分离：
selection = 我认的球；deleted = 剪辑上被我删掉的球。

四个量（比查准/查全更有行动价值）
--------------------------------
    真值球 sel_n        = len(selection)
    命中   auto_hit     = 选中且系统给了 high（会直接剪进成片）
    假绿   false_green  = high 里用户没选的（**会剪错**）
    降级   degraded     = 选中但系统只给 review/low（**人工要补勾**）
    无行   missing      = 选中但系统这一行都没报（**系统真的没给**）

    precision  = auto_hit / high_total
    auto_cover = auto_hit / sel_n
    manual     = degraded + missing          ← 人工兜底量
    恒等式：auto_hit + degraded + missing = sel_n

口径限制（必须知道）
--------------------
1. **不能算查全**。selection 只含「系统报过的行」（老六场例外：那是对上游
   HSV 行勾的，可能含 YOLO 完全没报的球，见 `missing`）。所以老六场的
   `missing` 有值，新六场的 `missing` 恒 0（truth ⊆ events）。**两组的
   `missing` 不可混算**，汇总时按组分开报。
2. **老六场锚点不同**：selection 是对**上游 HSV 版界面**勾的，与 YOLO v20 的
   行在 0.2s 容差下对齐 88.2%（0.5s 时 98.2%），偏差中位 0.047~0.085s。
   两套报的是同一批物理事件，只是时刻有微小抖动。用户 2026-11-14 裁定
   「接受这个偏差，这就是我的判断」。
3. 容差固定 0.2s，与官方口径一致。放宽会虚增命中（0.3s 时查准 +2.1pct），
   收紧会虚增 missing。

与第 0 节/第 4 节的关系
-----------------------
**结构化隔离**：本口径的场次清单 `VIDEOS_SEL` 前六场与 `eval_lib.VIDEOS`
重叠（同一批场、不同标准答案），所以绝不能把两套数字相加或互搬。
报告里独立成节，数字只在本节内自洽。
"""
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(PROJECT_ROOT, "tools") not in sys.path:
    sys.path.insert(0, os.path.join(PROJECT_ROOT, "tools"))

from derive_truth import selection_goals                          # noqa: E402
from eval_lib import RANK                                         # noqa: E402

# 老六场：selection 在上游仓（对 HSV 版界面勾的），YOLO 产物在 eval_out 臂目录。
# 09-19_1 是历来崩场（覆盖 29.6%），2026-11-14 用户裁定剔除，与历史口径一致。
VIDEOS_SEL_OLD = ["2026-08-22_1", "2026-08-28_1", "2026-08-29_3",
                  "2026-09-04_1", "2026-09-11_3", "2026-09-12_1"]
# 新六场：selection 就在本仓 work 下，truth 也由它派生 → missing 恒 0。
# 2026-11-14 裁定不扩到 10-10_1 / 10-16_1：那两场 selection 锚上游 HSV 行，
# 与 YOLO 产物 0.2s 只能对齐 62%/55%（0.5s 才 90%/88%），见 eval_lib 同处注释。
VIDEOS_SEL_NEW = ["2026-10-17_1", "2026-10-23_1", "2026-10-24_1",
                  "2026-10-30_1", "2026-10-31_1", "2026-11-06_1"]
VIDEOS_SEL = VIDEOS_SEL_OLD + VIDEOS_SEL_NEW
SEL_ONLY = set(VIDEOS_SEL)

SEL_TOL = 0.2

# 浮点容差补充量：`abs(100.2 - 100.0)` 算出来是 0.20000000000000284，
# 严格 `<= tol` 会把正好落在边界上的球判成不命中。1ns 对业务无影响，只抵误差。
_TOL_EPS = 1e-9


def sel_roots(extra=None):
    """selection.json 的候选根：显式传入优先，其次本仓 work。"""
    out = [os.path.abspath(p) for p in (extra or [])]
    w = os.path.join(PROJECT_ROOT, "work")
    if w not in out:
        out.append(w)
    return out


def find_selection(video, roots=None):
    """→ selection.json 路径，找不到 None。"""
    for r in sel_roots(roots):
        p = os.path.join(r, video, "selection.json")
        if os.path.exists(p):
            return p
    return None


def load_selection(video, roots=None):
    """→ (goals_sorted, 路径)；找不到 → ([], None)。"""
    p = find_selection(video, roots)
    if not p:
        return [], None
    goals, _bad = selection_goals(p)
    return goals, p


def sel_match(goals, rows, tol=SEL_TOL):
    """1:1 贪心配对（距离升序，同距 high 优先）→ (used_goals, used_rows)。

    比「按 selection 顺序找最近」稳：距离升序保证全局最近先认领，不会被
    先出现的那颗球抢走邻居的行。
    """
    pairs = sorted((round(abs(g - t), 6), RANK.get(s, 3), gi, ri)
                   for gi, g in enumerate(goals)
                   for ri, (t, s) in enumerate(rows)
                   if abs(g - t) <= tol + _TOL_EPS)
    used_g, used_r = set(), set()
    for _d, _rk, gi, ri in pairs:
        if gi in used_g or ri in used_r:
            continue
        used_g.add(gi)
        used_r.add(ri)
    return used_g, used_r


def sel_one(goals, rows, tol=SEL_TOL):
    """单场用户口径指标。rows 已剔 deleted/间隙球（由调用方保证）。"""
    used_g, used_r = sel_match(goals, rows, tol)
    high_total = sum(1 for _t, s in rows if s == "high")
    auto_hit = sum(1 for ri in used_r if rows[ri][1] == "high")
    manual_hit = len(used_r) - auto_hit          # 配到 review/low = 被降级
    missing = len(goals) - len(used_g)           # 完全没配上任何行
    n = len(goals)
    return {
        "sel_n": n,
        "rows": len(rows),
        "high_total": high_total,
        "auto_hit": auto_hit,
        "false_green": high_total - auto_hit,
        "degraded": manual_hit,
        "missing": missing,
        "manual": manual_hit + missing,
        "precision": (auto_hit / high_total) if high_total else 0.0,
        "auto_cover": (auto_hit / n) if n else 0.0,
        "tol": tol,
    }


_SEL_KEYS = ("sel_n", "rows", "high_total", "auto_hit", "false_green",
             "degraded", "missing")


def sel_agg(per_video, vids):
    """汇总。precision/auto_cover 用合计值重算，不平均各场比率。"""
    a = {k: 0 for k in _SEL_KEYS}
    for v in vids:
        m = per_video.get(v)
        if not m:
            continue
        for k in _SEL_KEYS:
            a[k] += m[k]
    a["manual"] = a["degraded"] + a["missing"]
    a["precision"] = a["auto_hit"] / a["high_total"] if a["high_total"] else 0.0
    a["auto_cover"] = a["auto_hit"] / a["sel_n"] if a["sel_n"] else 0.0
    # 自检：恒等式 auto_hit + degraded + missing == sel_n
    a["identity_bad"] = (a["auto_hit"] + a["degraded"] + a["missing"]) != a["sel_n"]
    return a


def group_of(video):
    """'old' 老六场（锚点 HSV 版）/ 'new' 新六场（锚点当版 events）。"""
    return "old" if video in set(VIDEOS_SEL_OLD) else "new"


# ======================================================================
# 严格口径（2026-11-15 用户确认后新增，与上面的勾选口径**并存**）
# ======================================================================
r"""「假绿」不是「误判」——严格口径把间隙球剔出去。

用户 2026-11-15 原话：

    「这 131 条我都是看过的，没标的原因是它其实是两场比赛之间大家随便投的球，
      没有标注的价值，这些球本来应该是删去的，但我没有在页面上删去，那其实
      这些没标的球其实都可以从测试集里面自动的删去，以避免影响数据判断。」
    「绿色的球我没勾，不代表它不是进球。」

所以 `high 且没勾` 混着两类：

    明确误判 false_ng = 没勾 且 ±0.2s 内有 `not_goal`  ← 人眼确认判错
    间隙球   filler   = 没勾 且 没有 `not_goal`（无标 / 标 `goal`）← 该删的

    有效自动档 high_eff       = high_total − filler
    precision_strict          = auto_hit / high_eff

2026-11-15 实测（新六场）：high 830 → 剔除 131 → 有效 699、命中 659、
明确误判 40 → **94.28%**（勾选口径同一批数据是 79.40%）。

**结果必须双报。** 只报勾选口径会把「可能本来就是进球的间隙球」当剪错，
只报严格口径则会掩盖一个事实：那 131 条确实是系统多报的（判对了但不是
用户要剪的），只是不该计为「判错」。

**已知偏置（必须随数字一起说）**：strict 值随用户标注密度漂移——标注越勤
（`not_goal` 越多），剔除越少、值越低；某一场完全没标时，它会自然接近
100%。所以 `has_fb=False` 的场必须在报告里标出来，不能静默计入。
"""


def load_verdicts(video, roots=None):
    """→ ([(t, verdict)], feedback.json 路径)。找不到 → ([], None)。

    roots 与 `load_selection` 同源：老六场的 feedback 在上游仓 `视频剪辑/work`，
    新六场的在本仓 `work`。
    """
    for r in sel_roots(roots):
        p = os.path.join(r, video, "feedback.json")
        if not os.path.exists(p):
            continue
        try:
            d = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            return [], p
        out = []
        for it in (d.get("verdicts") or []):
            if not isinstance(it, dict):
                continue
            try:
                out.append((float(it["t"]), it.get("verdict")))
            except (KeyError, TypeError, ValueError):
                continue
        return out, p
    return [], None


def _nearest_verdict(verdicts, t, vtol):
    """±vtol 内最近的 verdict 值，没有 → None。"""
    best = None
    for tt, vd in verdicts:
        d = abs(tt - t)
        if d <= vtol + _TOL_EPS and (best is None or d < best[0]):
            best = (d, vd)
    return best[1] if best else None


def sel_one_strict(goals, rows, tol=SEL_TOL, verdicts=None, vtol=SEL_TOL):
    """单场严格口径。`verdicts` 传 `load_verdicts()` 的结果。

    `verdicts=None`（该场没有 feedback.json）→ 不剔除，退回勾选口径，
    并置 `has_fb=False`，避免「没数据反而查准 100%」。
    """
    base = sel_one(goals, rows, tol)
    _used_g, used_r = sel_match(goals, rows, tol)

    if verdicts is None:
        high_eff, false_ng, filler, has_fb = base["high_total"], base["false_green"], 0, False
    else:
        has_fb, high_eff, false_ng, filler = True, 0, 0, 0
        for ri, (t, s) in enumerate(rows):
            if s != "high":
                continue
            if ri in used_r:                    # 命中：算有效自动档
                high_eff += 1
                continue
            if _nearest_verdict(verdicts, t, vtol) == "not_goal":
                false_ng += 1                   # 人眼确认判错
                high_eff += 1
            else:
                filler += 1                     # 间隙球：剔除，不进分母

    out = dict(base)
    out.update({
        "has_fb": has_fb,
        "filler": filler,                       # 剔除的间隙球行数
        "high_eff": high_eff,                   # 有效自动档 = 命中 + 明确误判
        "false_ng": false_ng,                   # 明确误判（标了 not_goal）
        "precision_strict": (base["auto_hit"] / high_eff) if high_eff else 0.0,
    })
    return out


_SEL_STRICT_KEYS = ("filler", "high_eff", "false_ng")


def sel_agg_strict(per_video, vids):
    """严格口径汇总：比率用合计值重算，不平均各场比率。"""
    a = sel_agg(per_video, vids)
    for k in _SEL_STRICT_KEYS:
        a[k] = sum((per_video[v] or {}).get(k, 0) for v in vids if per_video.get(v))
    a["precision_strict"] = a["auto_hit"] / a["high_eff"] if a["high_eff"] else 0.0
    a["no_fb"] = [v for v in vids
                  if per_video.get(v) and not per_video[v].get("has_fb", True)]
    # 自检：有效自动档 = 命中 + 明确误判；且 high_total = high_eff + filler
    a["strict_identity_bad"] = (
        a["high_eff"] != a["auto_hit"] + a["false_ng"]
        or a["high_total"] != a["high_eff"] + a["filler"])
    return a



def groups():
    """→ [(标签, 场次列表)]，用于分组报告（missing 两组不可混算）。"""
    return [("老六场（selection 对 HSV 版勾）", list(VIDEOS_SEL_OLD)),
            ("新六场（selection 对当版 events 勾）", list(VIDEOS_SEL_NEW)),
            ("12 场合计", list(VIDEOS_SEL))]
