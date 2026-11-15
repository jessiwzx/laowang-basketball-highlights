"""集成验收：检测结果 vs 人工标注 → 漏检/误报/待确认报告。

用法：
    python tools/evaluate.py work/<视频名>/events.json truth/<视频名>.json

**跨场聚合口径（2026-09-26 更正）**：部分标注集（`complete=false`，目前只有
08-21_1）**不得**计入八场「假绿/查准/查全」总账——它的 confirmed_goals 不是
完整进球表，未标注的真进球会被记成假绿。历史总账里的「假绿 178」= 7 个完整
场 106 + 08-21 过计 72；正确口径是 **106**。聚合脚本一律用
`goals = truth.get("goals")`，不要写 `goals or confirmed_goals`（那是过计
来源）。08-21 的 72 条经去球探针核查全是真网动（详见
`诊断/2026-08-21弱网/体检报告.md`），其中 25 条在片尾散场练习段。

truth.json 两种形态：
- 完整测试集 {"complete": true, "goals": [100.0, ...]}——进球标注完整，
  输出自动档（🟢 high）查准/查全（2026-08-23 用户定调：任意档浮表率
  不再作为指标，人工兜底工作另列）；
- 部分标注回归集 {"complete": false, "confirmed_goals": [100.0, ...]}——
  进球未标全，禁止计算查准/查全（未标注真进球会被误当假绿、漏检不可见），
  只做回归断言：每个确认进球必须仍浮表（任意档），丢失即退出码 1。
"""
import json
import os
import sys

TOLERANCE = 3.0  # 秒

# 已核实的「真进球-比赛间隙」行（2026-09-24 用户裁定：确为进球，但属
# 比赛间隙打球，手动标记时会删除——不加进 truth goals（用户明确不进
# 标准测试集），也不算假绿（不是检测错误，CR 冷却释放将其正确浮出）。
# 口径同 deleted.json 策展出局：评估前剔除，既不算命中也不计误报。
# 键=work 目录名（events.json 所在目录），值=行时刻秒。
INTERMISSION_GOALS = {"2026-09-12_1": [1860.35, 4839.10, 6026.59]}


def match_report(truth: list[float],
                 detected: list[tuple[float, str]],
                 tolerance: float = TOLERANCE) -> dict:
    """detected: [(t, status)]。返回 命中/漏检/误报/待确认中/低噪 统计。
    三档口径（2026-08-19 低置信档上线后）：low 命中 truth 也计入"命中"
    （球已出现在列表里，人工可找回，不算漏检），但单列"命中低置信"；
    low 未命中是噪声行，不计入"误报"（否则指标被噪声行灌满失去意义），
    单独记"低噪"。误报与验收门只衡量 high+review 的精度。
    truth 先排序再匹配——人工标注顺序错乱不应静默低估命中。"""
    truth = sorted(truth)
    used = set()
    hits = 0
    hits_review = 0
    hits_low = 0
    unmatched = 0
    noise_low = 0
    for t, status in detected:
        match = next((i for i, g in enumerate(truth)
                      if i not in used and abs(g - t) <= tolerance), None)
        if match is not None:
            used.add(match)
            hits += 1
            if status == "review":
                hits_review += 1
            elif status == "low":
                hits_low += 1
        elif status == "low":
            noise_low += 1
        else:
            unmatched += 1
    return {"命中": hits, "待确认中": hits_review, "命中低置信": hits_low,
            "漏检": len(truth) - hits, "误报": unmatched, "低噪": noise_low}


def auto_metrics(report: dict, high_total: int) -> dict:
    """自动档（🟢 high）口径：查准 = high命中/high总数；查全 = high命中/标注进球。
    假绿 = high 中未命中真值的条数（自动档误报，信任成本）。"""
    hits_high = report["命中"] - report["待确认中"] - report["命中低置信"]
    goals_total = report["命中"] + report["漏检"]
    return {"自动档命中": hits_high,
            "查准": hits_high / high_total if high_total else 1.0,
            "查全": hits_high / goals_total if goals_total else 1.0,
            "假绿": high_total - hits_high}


def regression_report(confirmed: list[float],
                      detected: list[tuple[float, str]],
                      tolerance: float = TOLERANCE) -> dict:
    """部分标注回归集：每个确认进球须在 tolerance 内浮表（任意档），
    同一检测不被两个确认认领。丢失 = 调参把已确认进球挤出表格 = 回归失败。"""
    used = set()
    tiers = {"high": 0, "review": 0, "low": 0}
    lost = []
    for g in sorted(confirmed):
        m = next((i for i, (t, _) in enumerate(detected)
                  if i not in used and abs(t - g) <= tolerance), None)
        if m is None:
            lost.append(g)
        else:
            used.add(m)
            tiers[detected[m][1]] += 1
    return {"确认进球": len(confirmed), "浮表": len(confirmed) - len(lost),
            "档位": tiers, "丢失": lost}


def _load_deleted_seconds(events_path: str) -> list:
    """events.json 同目录的 deleted.json（剪辑台「🗑 删除此球」的非破坏
    清单，键=m+:ss.s 原始检测时刻，与勾选/标签同口径）→ 秒。缺失/损坏
    当空。已删球是策展出局（随意投篮不进集锦），不是检测错误——评估前
    剔除，既不算进球也不计假绿/误报，否则用户核对期的删除会把检测器
    的精度指标打脏。"""
    p = os.path.join(os.path.dirname(os.path.abspath(events_path)),
                     "deleted.json")
    try:
        with open(p, encoding="utf-8") as f:
            keys = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for k in (keys if isinstance(keys, list) else []):
        try:
            m, s = str(k).split(":")
            out.append(int(m) * 60 + float(s))
        except ValueError:
            continue
    return out


def _deleted_exclusions(event_ts: list[float], deleted: list[float],
                        cap: float = 0.5) -> set[int]:
    """删键 → 被否决行下标集合（评估前剔除）。1:1 最近匹配、距离上限
    0.5s：精确锚定的删键（老四场全部如此）行为不变；多段标定重扫后行
    键漂 0.1~0.3s 的（09-04_1 有 8 个）仍能认回本行——该场相邻事件最小
    间距 0.63s，认错行不可能；漂得更远的（对应行已在重扫中消失）安全
    放空。"""
    pairs = sorted((abs(t - d), i, j) for i, t in enumerate(event_ts)
                   for j, d in enumerate(deleted) if abs(t - d) <= cap)
    claimed_rows: set[int] = set()
    claimed_keys: set[int] = set()
    for _, row, key in pairs:
        if row not in claimed_rows and key not in claimed_keys:
            claimed_rows.add(row)
            claimed_keys.add(key)
    return claimed_rows


def main():
    events_path, truth_path = sys.argv[1], sys.argv[2]
    with open(events_path, encoding="utf-8") as f:
        events = json.load(f)["events"]
    with open(truth_path, encoding="utf-8") as f:
        truth = json.load(f)
    deleted = _load_deleted_seconds(events_path)
    name = os.path.basename(os.path.dirname(os.path.abspath(events_path)))
    intermission = INTERMISSION_GOALS.get(name, [])
    if intermission:
        print(f"比赛间隙真进球: {len(intermission)}"
              "（2026-09-24 用户裁定会手动删除，不进 truth 也不计假绿；"
              "±0.5s 认回行）")
    if deleted or intermission:
        drop = _deleted_exclusions([e["t"] for e in events],
                                   deleted + intermission)
        if drop:
            print(f"已删出局: {len(drop)}"
                  "（剪辑台删除的球，不进真值也不计误报；"
                  "±0.5s 认回重扫漂移键）")
        events = [e for i, e in enumerate(events) if i not in drop]
    detected = [(e["t"], e["status"]) for e in events]
    # truth 可自带容差：标注直接来自表格行键的用小容差（0.2）行锚定，
    # 避免相邻事件在默认 3s 容差下错认领；独立重建的真值用默认 3.0。
    tol = truth.get("tolerance", TOLERANCE)

    if truth.get("complete") is False:
        confirmed = truth["confirmed_goals"]
        r = regression_report(confirmed, detected, tolerance=tol)
        print(f"部分标注回归集（进球未标全，不算查准/查全）")
        print(f"确认进球: {r['确认进球']}  浮表: {r['浮表']}"
              f"（🟢{r['档位']['high']} 🟡{r['档位']['review']}"
              f" ⚪{r['档位']['low']}）")
        if r["丢失"]:
            print(f"丢失（回归失败）: {[round(t, 1) for t in r['丢失']]}")
        sys.exit(1 if r["丢失"] else 0)

    goals = truth["goals"]
    report = match_report(goals, detected, tolerance=tol)
    high_total = sum(1 for _, s in detected if s == "high")
    m = auto_metrics(report, high_total)
    total = len(goals)
    miss_rate = report["漏检"] / total if total else 0.0
    print(f"标注进球: {total}")
    print(f"自动档命中: {m['自动档命中']}  查全 {m['查全']:.1%}")
    print(f"假绿: {m['假绿']}/{high_total}  查准 {m['查准']:.1%}")
    print(f"人工兜底: 🟡{report['待确认中']} + ⚪{report['命中低置信']}"
          f"（真进球落在非自动档，需人工勾回）")
    print(f"漏检(未浮表): {report['漏检']}  ({miss_rate:.0%})")
    print(f"低噪: {report['低噪']}（低置信未命中=列表噪声行，不计验收门）")
    print("验收门: 漏检率 <=15% 且 误报 <= 3×命中数")
    sys.exit(0 if miss_rate <= 0.15 and report["误报"] <= 3 * max(report["命中"], 1)
             else 1)


if __name__ == "__main__":
    main()
