# -*- coding: utf-8 -*-
r"""一键出评测报告：给「视频剪辑YOLO」的 work/ 产物打三种口径的分。

用法
----
    # 默认：读本项目 work/（app 跑完就有），7 场标准测试集，三口径一次出
    .venv\Scripts\python scripts\eval_report.py

    # A/B：把两个 work 根目录当两条臂比（例如 YOLO 版与 HSV 版各跑一遍）
    .venv\Scripts\python scripts\eval_report.py ^
        --arm yolo=work_yolo --arm hsv=work_hsv

    # 只报几场
    .venv\Scripts\python scripts\eval_report.py --videos 2026-08-29_3 2026-09-04_1

口径说明见 tools/eval_lib.py 头注释（官方 / 对称 / 容差扫描，三者不可互搬）。
产物：reports\EVAL_<tag>.md + .json
"""
import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "tools"))
import eval_lib as E                                              # noqa: E402
import eval_sel as ESEL                                           # noqa: E402

ROOT = E.PROJECT_ROOT
DEFAULT_SWEEP = [0.2, 0.3, 0.5, 0.8, 3.0]


def parse_arms(items, default_root):
    """['name=dir', ...] → [(name, abs_dir)]；空则 [(work, default_root)]。"""
    if not items:
        return [("app", default_root)]
    out = []
    for it in items:
        if "=" not in it:
            out.append((it, os.path.abspath(it)))
        else:
            name, d = it.split("=", 1)
            d = d or default_root
            out.append((name, os.path.abspath(d)))
    return out


def fmt(v, nd=3, pct=False):
    if v is None:
        return "—"
    if pct:
        return f"{v:.1%}"
    return f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", action="append", default=[],
                    help="name=dir（可多次）；dir 是含 <视频>/events.json 的根")
    ap.add_argument("--truth", default=os.path.join(ROOT, "truth"))
    ap.add_argument("--deleted-from", action="append", default=[],
                    help="deleted.json 所在 work 根（可多次，按序找）；默认跟"
                         "events.json 同根 + extra 根依次找")
    ap.add_argument("--work-extra", action="append", default=[],
                    help="额外产物根（按场名回退查找 events.json）；老七场在上游仓"
                         " `D:\\claude code\\视频剪辑\\work`，新场在本仓 work")
    ap.add_argument("--sel-root", action="append", default=[],
                    help="selection.json 所在 work 根（可多次，按序找）。第 5 节"
                         "用户口径用；默认取 --deleted-from 的根 + 本仓 work")
    ap.add_argument("--videos", nargs="*", default=E.VIDEOS)
    ap.add_argument("--tols", nargs="*", type=float, default=[0.3, 0.5, 0.8])
    ap.add_argument("--main-tol", type=float, default=0.5)
    ap.add_argument("--sweep", nargs="*", type=float, default=DEFAULT_SWEEP)
    ap.add_argument("--no-sweep", action="store_true")
    ap.add_argument("--drop", nargs="*", default=["2026-09-19_1"],
                    help="附加一档「剔除这些场」的汇总")
    ap.add_argument("--tag", default="app")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    E.truth_dir = lambda root=None: args.truth          # 覆盖真值目录
    arms = parse_arms(args.arm, os.path.join(ROOT, "work"))

    # ---- 读数据 ----
    extra = [os.path.abspath(p) for p in args.work_extra]
    data, goals_of = {}, {}
    for name, root in arms:
        data[name] = E.collect_arm(root, args.videos,
                                   deleted_from=args.deleted_from,
                                   extra_roots=extra)
    # 查准回归集独立收集（不混进 args.videos 的总账）
    prec_vids = [v for v in E.VIDEOS_PREC
                 if os.path.exists(os.path.join(args.truth, v + ".json"))]
    data_prec = {}
    for name, root in arms:
        data_prec[name] = E.collect_arm(root, prec_vids,
                                        deleted_from=args.deleted_from,
                                        extra_roots=extra)
    # 用户口径（第 5 节）：真值取 selection.json，与 truth/ 完全无关。
    # 场次清单独立（老六 + 新六），所以单独收集，绝不并入 args.videos。
    sel_roots = [os.path.abspath(p) for p in args.sel_root] \
        or [os.path.abspath(p) for p in args.deleted_from]
    data_sel = {}
    for name, root in arms:
        arm = E.collect_arm(root, ESEL.VIDEOS_SEL, deleted_from=args.deleted_from,
                            extra_roots=extra)
        per = {}
        for v in ESEL.VIDEOS_SEL:
            rec = arm.get(v)
            if not rec:
                continue
            goals, src = ESEL.load_selection(v, sel_roots)
            if not goals:
                continue
            m = ESEL.sel_one(goals, rec["rows"], ESEL.SEL_TOL)
            m["src"] = src
            # 严格口径（2026-11-15）：把「high 未勾且无 not_goal」当间隙球剔除。
            vd, _vp = ESEL.load_verdicts(v, sel_roots)
            m.update({k: x for k, x in ESEL.sel_one_strict(
                goals, rec["rows"], ESEL.SEL_TOL,
                verdicts=vd if _vp else None).items()
                if k in ("has_fb", "filler", "high_eff", "false_ng",
                         "precision_strict")})
            per[v] = m
        data_sel[name] = per
    for v in args.videos:
        g, _tol = E.load_truth(v)
        goals_of[v] = g
    ok_scenes = [v for v in args.videos if goals_of[v]]
    n_balls = sum(len(goals_of[v]) for v in ok_scenes)
    mt = args.main_tol
    vids6 = [v for v in ok_scenes if v not in args.drop]

    L = []

    def A(s=""):
        L.append(s)

    A(f"# 评测报告（tag={args.tag}）")
    A("")
    A(f"- 场次 {len(ok_scenes)}（truth.complete=true）；标注进球合计 {n_balls} 个。"
      + (f"  剔除 {args.drop} 后 {sum(len(goals_of[v]) for v in vids6)} 个。"
         if args.drop else ""))
    A(f"- 臂：" + "；".join(f"`{n}` = `{d}`" for n, d in arms))
    A("- 三种口径数字**不可互搬**：官方=0.2s 行锚定（偏 HSV，看「过不过门」）；"
      "对称=各自时刻 ±%gs 配对（看「看没看见 / 判没判出来」）；"
      "容差扫描=把时刻偏差扣掉后的效果。" % mt)
    A("")
    for name, _d in arms:
        cfg = E.load_run_cfg(dict(arms)[name]) if E.load_run_cfg(dict(arms)[name]) else {}
        if cfg:
            A(f"- `{name}` 实参：" + "、".join(f"{k}={v}" for k, v in cfg.items()
                                              if v is not None))
    A("")

    # 补充真球（truth/extra_goals.json）改的是**标准答案**，不写清楚会被当成
    # 「改了个东西查准就涨了」。必须放在报告最前面。
    _ex = E.extra_goals_cached()
    _ex_hit = {v: len(_ex[v]) for v in args.videos if _ex.get(v)}
    if _ex_hit:
        A(f"> ★ **标准答案含补充真球 {sum(_ex_hit.values())} 条**（"
          + "、".join(f"{v}={n}" for v, n in _ex_hit.items())
          + "）。来源 `truth/extra_goals.json`：2026-11-11 用户裁定把 v20 冷却放行"
            "新增的 48 条 high 行**直接认定为真球，未经视频核验**。"
            "带此补丁的数字与历史 KPI（无补丁）**不可直接混比**；"
            "删掉该文件即回原口径。")
        A("")

    # 删球表缺失会静默把已删球算成假绿（查准虚低），必须显式说出来
    no_del = [v for v in args.videos
              if data[arms[0][0]].get(v)
              and not data[arms[0][0]][v].get("deleted_found")]
    if no_del:
        A(f"> ⚠ **{len(no_del)} 场未找到 `deleted.json`**："
          + "、".join(no_del[:6]) + ("…" if len(no_del) > 6 else "")
          + "　已删球会被算成假绿（查准偏低）。用 "
            "`--deleted-from <标注 work 根>` 指定位置。")
        A("")

    # ================= 0 官方口径总账 =================
    A("## 0 官方口径总账（原项目 tools/evaluate.py 裁判）")
    A("")
    A("| 臂 | 覆盖场次 | 自动档命中/标注球 | 查全 | 假绿/自动档总数 | 查准 | "
      "人工兜底🟡+⚪ | 漏检 | 漏检率 | 验收门 |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for name, _d in arms:
        pv = {v: (data[name].get(v) or {}).get("official") for v in args.videos}
        a = E.official_agg(pv, ok_scenes)
        cov = sum(1 for v in ok_scenes if data[name].get(v))
        A(f"| {name} | {cov} | {a['auto_hit']}/{a['goals']} | {a['recall']:.1%} | "
          f"{a['false_green']}/{a['high_total']} | {a['precision']:.1%} | "
          f"{a['manual']} | {a['miss']} | {a['miss_rate']:.1%} | {a['gate']} |")
    A("")

    if args.drop and vids6:
        A(f"### 剔除 {'、'.join(args.drop)}（{len(vids6)} 场）后")
        A("")
        A("| 臂 | 自动档命中/标注球 | 查全 | 查准 | 漏检 | 漏检率 | 门 |")
        A("|---|---|---|---|---|---|---|")
        for name, _d in arms:
            pv = {v: (data[name].get(v) or {}).get("official") for v in args.videos}
            a = E.official_agg(pv, vids6)
            A(f"| {name} | {a['auto_hit']}/{a['goals']} | {a['recall']:.1%} | "
              f"{a['precision']:.1%} | {a['miss']} | {a['miss_rate']:.1%} | {a['gate']} |")
        A("")

    # ================= 1 官方口径逐场 =================
    A("## 1 官方口径逐场（查全 / 查准）")
    A("")
    A("| 场 | 划分 | 球 | " + " | ".join(n for n, _d in arms) + " |")
    A("|---" * (len(arms) + 3) + "|")
    for v in args.videos:
        if not goals_of[v]:
            continue
        cells = []
        for name, _d in arms:
            m = (data[name].get(v) or {}).get("official")
            cells.append("—" if not m else f"{m['recall']:.1%} / {m['precision']:.1%}")
        A(f"| {v} | {E.SPLIT.get(v, '?')} | {len(goals_of[v])} | "
          + " | ".join(cells) + " |")
    A("")

    # ================= 2 对称口径 =================
    A(f"## 2 对称口径（各自时刻 ±{mt}s 双向 1:1 配对）")
    A("")
    A("| 臂 | 覆盖场次 | 标注球 | 看见(配对) | ❌未配对 | 🟢 | 🟢率 | 🟢+🟡率 |")
    A("|---|---|---|---|---|---|---|---|")
    for name, _d in arms:
        pv = {}
        for v in ok_scenes:
            rec = data[name].get(v)
            if rec:
                pv[v] = E.sym_one(rec["goals"], rec["rows"], mt)
        c = E.sym_agg(pv, ok_scenes)
        if not c:
            A(f"| {name} | 0 | — | — | — | — | — | — |")
            continue
        A(f"| {name} | {len(pv)} | {c['n']} | {c['paired']} ({E.rate(c,'paired'):.1%}) | "
          f"{c['miss']} ({E.rate(c,'miss'):.1%}) | {c['high']} | "
          f"**{E.rate(c,'high'):.1%}** | "
          f"{E.rate(c,'high')+E.rate(c,'review'):.1%} |")
    A("")
    A("按划分（🟢率 / 🟢+🟡率）：")
    A("")
    A("| 臂 | 划分 | 场 | 标注球 | 看见率 | 🟢率 | 🟢+🟡率 |")
    A("|---|---|---|---|---|---|---|")
    for name, _d in arms:
        for sp in E.SPLIT_ORDER:
            sc = [v for v in ok_scenes if E.SPLIT.get(v) == sp]
            pv = {}
            for v in sc:
                rec = data[name].get(v)
                if rec:
                    pv[v] = E.sym_one(rec["goals"], rec["rows"], mt)
            c = E.sym_agg(pv, sc)
            if not c:
                continue
            A(f"| {name} | {sp} | {len(sc)} | {c['n']} | {E.rate(c,'paired'):.1%} | "
              f"**{E.rate(c,'high'):.1%}** | "
              f"{E.rate(c,'high')+E.rate(c,'review'):.1%} |")
    A("")
    A(f"容差敏感性（🟢率 @ {'/'.join(f'{t:g}' for t in args.tols)}s）：")
    A("")
    A("| 臂 | " + " | ".join(f"{t:g}s" for t in args.tols) + " |")
    A("|---" * (len(args.tols) + 1) + "|")
    for name, _d in arms:
        cells = []
        for tol in args.tols:
            pv = {}
            for v in ok_scenes:
                rec = data[name].get(v)
                if rec:
                    pv[v] = E.sym_one(rec["goals"], rec["rows"], tol)
            c = E.sym_agg(pv, ok_scenes)
            cells.append(f"{E.rate(c,'high'):.1%}" if c else "—")
        A(f"| {name} | " + " | ".join(cells) + " |")
    A("")

    # ================= 3 容差扫描（时刻纠正后） =================
    if not args.no_sweep:
        A("## 3 容差扫描（最邻近 1:1）：把时刻偏差扣掉后的效果")
        A("")
        A("| 容差 W | 臂 | 自动档命中 | 查全 | 查准 | 人工兜底 | 漏检 | 漏检率 | 门 |")
        A("|---|---|---|---|---|---|---|---|---|")
        for W in args.sweep:
            mark = " ←现状" if abs(W - 0.2) < 1e-9 else ""
            for name, _d in arms:
                rows_by = {v: data[name][v]["rows"] for v in ok_scenes
                           if data[name].get(v)}
                a = E.nearest_agg(rows_by, goals_of, ok_scenes, W)
                A(f"| {W:g}s{mark} | {name} | {a['auto_hit']}/{a['goals']} | "
                  f"{a['recall']:.1%} | {a['precision']:.1%} | {a['manual']} | "
                  f"{a['miss']} | {a['miss_rate']:.1%} | {a['gate']} |")
        A("")
        if len(arms) >= 2:
            base_name, _ = arms[0]
            A("### 头对头（Δ = 第 2 臂 − 第 1 臂）")
            A("")
            A(f"| 容差 W | {arms[0][0]} 查全/查准 | {arms[1][0]} 查全/查准 | "
              "Δ查全 | Δ查准 | Δ漏检 | Δ假绿 |")
            A("|---|---|---|---|---|---|---|")
            for W in args.sweep:
                r0 = E.nearest_agg({v: data[arms[0][0]][v]["rows"] for v in ok_scenes
                                    if data[arms[0][0]].get(v)}, goals_of, ok_scenes, W)
                r1 = E.nearest_agg({v: data[arms[1][0]][v]["rows"] for v in ok_scenes
                                    if data[arms[1][0]].get(v)}, goals_of, ok_scenes, W)
                A(f"| {W:g}s | {r0['recall']:.1%} / {r0['precision']:.1%} | "
                  f"{r1['recall']:.1%} / {r1['precision']:.1%} | "
                  f"{(r1['recall'] - r0['recall']) * 100:+.1f}pt | "
                  f"{(r1['precision'] - r0['precision']) * 100:+.1f}pt | "
                  f"{r1['miss'] - r0['miss']:+d} | "
                  f"{r1['false_green'] - r0['false_green']:+d} |")
            A("")

        # ---- 偏差构成（取第一臂） ----
        name0 = arms[0][0]
        rows_by = {v: data[name0][v]["rows"] for v in ok_scenes
                   if data[name0].get(v)}
        bucket, mid_tier, mid_arc, far_arc = E.offset_profile(
            rows_by, goals_of, ok_scenes)
        A(f"### 偏差构成（{name0}：每个标注球 → 最近一条事件）")
        A("")
        A("| 距离 | 球数 | 占比 |")
        A("|---|---|---|")
        for k in ("<=0.1", "<=0.2", "<=0.3", "<=0.5", ">0.5"):
            A(f"| {k}s | {bucket[k]} | {bucket[k] / max(n_balls, 1):.1%} |")
        A("")
        A(f"**0.2~0.5s（事件在、只是时刻偏了）共 {sum(mid_tier.values())} 球**，"
          "最近事件档位："
          + "、".join(f"{k} {mid_tier.get(k, 0)}" for k in ("high", "review", "low")))
        A("")
        A(f"**>0.5s 的 {bucket['>0.5']} 球 = 真漏**（与对称口径 ±0.5s 的 "
          f"{E.sym_agg({v: E.sym_one(goals_of[v], rows_by[v], 0.5) for v in rows_by}, ok_scenes)['miss']} "
          "球一致）。")
        A("")
        A("| 场 | 划分 | 球 | 0.2~0.5s（时刻偏） | >0.5s（真漏） |")
        A("|---|---|---|---|---|")
        for v in ok_scenes:
            A(f"| {v} | {E.SPLIT.get(v, '?')} | {len(goals_of[v])} | "
              f"{mid_arc.get(v, 0)} | {far_arc.get(v, 0)} |")
        A("")

    # ================= 4 查准回归集（selection 派生真值） =================
    if prec_vids:
        A("## 4 查准回归集（truth 由剪辑台 selection.json 派生）")
        A("")
        A("> 这些场的真值是从**当版 events 表**勾出来的 → truth ⊆ events，"
          "**漏检结构上恒 0**。所以只读「查准」与「自动档覆盖率」，"
          "**不能读查全**；与第 0 节查全总账不可混算（混算会让查全凭空变高）。")
        A("")
        A("| 臂 | 场次 | 真值球 | high | 命中 | 假绿 | 查准 | 自动档覆盖 | "
          "🟡捞回 | ⚪捞回 | 人工待捞 | 对齐自检 |")
        A("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for name, root in arms:
            pv = {v: E.prec_one(data_prec[name][v]["goals"],
                                data_prec[name][v]["rows"],
                                data_prec[name][v]["tol"])
                  for v in prec_vids if data_prec[name].get(v)
                  and data_prec[name][v].get("goals")}
            a = E.prec_agg(pv, prec_vids)
            A(f"| {name} | {len(pv)} | {a['goals']} | {a['high_total']} | "
              f"{a['auto_hit']} | {a['false_green']} | **{a['precision']:.1%}** | "
              f"{a['auto_cover']:.1%} | {a['review_hit']} | {a['low_hit']} | "
              f"{a['manual']} | {'⚠ 漏检非0' if a['anchor_bad'] else '✓'} |")
        A("")
        A("逐场（查准 / 自动档覆盖）：")
        A("")
        A("| 场 | 真值球 | " + " | ".join(n for n, _d in arms) + " |")
        A("|---" * (len(arms) + 2) + "|")
        for v in prec_vids:
            cells = []
            for name, _d in arms:
                rec = data_prec[name].get(v)
                if not rec or not rec.get("goals"):
                    cells.append("—")
                    continue
                m = E.prec_one(rec["goals"], rec["rows"], rec["tol"])
                cells.append(f"{m['precision']:.1%} / {m['auto_cover']:.1%}")
            g = next((data_prec[n][v]["goals"] for n, _d in arms
                      if data_prec[n].get(v) and data_prec[n][v].get("goals")), [])
            A(f"| {v} | {len(g)} | " + " | ".join(cells) + " |")
        A("")

    # ================= 5 用户标注口径（selection 真值） =================
    if any(data_sel.get(n) for n, _d in arms):
        A("## 5 用户标注口径（真值 = 剪辑台 selection.json，不是 HSV）")
        A("")
        A("> **这一节的尺子换了。** 前四节的进球清单来自 truth 行锚定表（本质是"
          "「与 HSV 一致」）；本节直接拿用户逐条勾过的球单当真值（「用户满意不满意」）。"
          "实测两套标签只在 55/322 条上重叠（17%），所以**本节数字与 0/4 节不可比、"
          "不可相加**。")
        A("")
        A("> 四个去向互斥且穷尽：命中 high（会直接剪）+ 被降级 review/low（人工补勾）"
          "+ 完全无行（系统没给这一行）= 真值球。")
        A("")
        for name, _d in arms:
            per = data_sel.get(name) or {}
            if not per:
                continue
            A(f"### 臂 `{name}`")
            A("")
            A("| 组 | 场次 | 真值球 | 自动档 | 命中 | 假绿 | 查准 | 自动档覆盖 | "
              "降级🟡⚪ | 无行 | 人工兜底 | 核对 |")
            A("|---|---|---|---|---|---|---|---|---|---|---|---|")
            for label, vids in ESEL.groups():
                a = ESEL.sel_agg(per, vids)
                got = sum(1 for v in vids if v in per)
                if not got:
                    continue
                A(f"| {label} | {got} | {a['sel_n']} | {a['high_total']} | "
                  f"{a['auto_hit']} | {a['false_green']} | "
                  f"**{a['precision']:.1%}** | {a['auto_cover']:.1%} | "
                  f"{a['degraded']} | {a['missing']} | {a['manual']} | "
                  f"{'⚠ 恒等式不平' if a['identity_bad'] else '✓'} |")
            A("")
            A("**5.1 严格口径｜剔除间隙球**（用户 2026-11-15 确认）")
            A("")
            A("> `假绿` ≠ `误判`。用户没勾的球大多是「两场比赛之间大家随便投篮」，"
              "本来就是进球但不剪进成片、也没有标注价值。按 `feedback.json` 再拆："
              "**明确误判** = 没勾且标了 `not_goal`（人眼确认判错）；"
              "**间隙球** = 没勾且没标（从分母剔除）。")
            A("")
            A("| 组 | 自动档 | 剔除间隙球 | 有效自动档 | 命中 | 明确误判 | "
              "查准（严格） | 查准（勾选） |")
            A("|---|---|---|---|---|---|---|---|")
            for label, vids in ESEL.groups():
                s = ESEL.sel_agg_strict(per, vids)
                got = sum(1 for v in vids if v in per)
                if not got:
                    continue
                bad = " ⚠" if s["strict_identity_bad"] else ""
                A(f"| {label}{bad} | {s['high_total']} | {s['filler']} | "
                  f"{s['high_eff']} | {s['auto_hit']} | {s['false_ng']} | "
                  f"**{s['precision_strict']:.2%}** | {s['precision']:.2%} |")
            A("")
            A("> ⚠ **这一列随标注密度漂移**：`not_goal` 标得越勤、剔除越少、值越低；"
              "某场完全没标时会自然接近 100%。所以它答的是「在你已确认的范围内系统"
              "有多准」，不是「系统客观上多准」。没有 `feedback.json` 的场不剔除"
              "（`has_fb=False`），在逐场表里以 `—` 标出。")
            A("")
            A("逐场：")
            A("")
            A("| 场 | 组 | 真值球 | 自动档 | 剔除 | 命中 | 明确误判 | 查准严 | "
              "假绿 | 查准勾 | 覆盖 | 降级 | 无行 |")
            A("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
            for v in ESEL.VIDEOS_SEL:
                m = per.get(v)
                if not m:
                    A(f"| {v} | — | — | — | — | — | — | — | — | — | — | — | — |")
                    continue
                g = "老" if ESEL.group_of(v) == "old" else "新"
                fs = "—" if not m.get("has_fb", True) else f"{m['filler']}"
                ps = ("—" if not m.get("has_fb", True)
                      else f"{m['precision_strict']:.1%}")
                A(f"| {v} | {g} | {m['sel_n']} | {m['high_total']} | {fs} | "
                  f"{m['auto_hit']} | {m['false_ng']} | {ps} | "
                  f"{m['false_green']} | {m['precision']:.1%} | "
                  f"{m['auto_cover']:.1%} | {m['degraded']} | {m['missing']} |")
            A("")
        A("**读这一节要先知道的三件事**：")
        A("")
        A("1. `无行` 只可能出现在老六场——那几场的 selection 是对**上游 HSV 版界面**"
          "勾的，可能含 YOLO 根本没报的球；新六场的 selection 是从当版 events 勾的，"
          "结构上不会缺行（恒 0）。所以两组 `无行` **不可混算**。")
        A("2. 老六场 selection 与 YOLO 行在 0.2s 容差下对齐 **88.2%**（0.5s 时 98.2%），"
          "偏差中位 0.047~0.085s —— 同一批物理事件、时刻小抖动。用户 2026-11-14 "
          "裁定接受该偏差。")
        A("3. `假绿` = 自动档里用户没勾的行（**会剪错**）；`降级 + 无行` = 用户勾了但"
          "系统没给自动档（**人工要补**）。前者是准确性问题，后者是覆盖率问题，"
          "调阈值只能在两者间换，不能同时改善。")
        A("")

    A("## 读表三条注意")
    A("")
    A("1. **官方口径是最偏 HSV 的一把尺子**（0.2s 行锚定）——任何时刻与 HSV 不同源的"
      "检测器都会额外掉点，所以它是**下界**，用来回答「过不过现行门」。")
    A("2. **对称口径只解开了「时刻锚」的一半**：进球清单本身仍是从 HSV 输出表勾的，"
      "HSV 整场漏掉的球可能压根不在清单里 → 这部分偏保守、利好 HSV。")
    A("3. **容差放宽 ≠ 差异不存在**：判定点差 0.3s，成片剪辑点就差 0.3s，是产品可见的"
      "差别。第 3 节回答的是「扣掉时刻因素后，检出/判定能力本身如何」。")
    A("")

    md = "\n".join(L)
    out = args.out or os.path.join(ROOT, "reports", f"EVAL_{args.tag}.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with io.open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(md)
    oj = os.path.splitext(out)[0] + ".json"
    slim = {n: {v: {"official": (data[n][v] or {}).get("official"),
                    "n_rows": len((data[n][v] or {}).get("rows") or [])}
                for v in ok_scenes} for n, _d in arms}
    with open(oj, "w", encoding="utf-8") as f:
        json.dump({"tag": args.tag, "videos": ok_scenes, "arms": dict(arms),
                   "per_arm": slim}, f, ensure_ascii=False, indent=1)
    print(md)
    print(f"\n报告：{out}\n     {oj}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
