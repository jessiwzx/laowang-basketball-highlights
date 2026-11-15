# -*- coding: utf-8 -*-
r"""阶段6 续：面积/密度特征在**可用操作点**上的得失（只读，吃 area_feat.json）。

AUC 只说明「有没有区分力」，本脚本回答「划在哪、换回多少球」：

  查准侧（降档）  high 行里 a_rel 偏大 / a_cv 偏平 → 降 review
                  得失：失去的 high 命中 vs 去掉的假绿 → 查准净变化
  查全侧（升档）  review/low 里 a_rel 偏小 / a_cv 有波动 → 升 high
                  得失：捞回的降级真球 vs 误升的非进球 → 查准+覆盖净变化

口径固定为 v23 新六场基线：high 863 = 命中 659 + 假绿 204；降级 127；
非进球低置信 788。**任何规则都必须先过「不劣于基线」这一关**。

用法：
    python 诊断/2026-11-14-优化候选/area_op.py
"""
import argparse
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def q(v, p):
    v = sorted(x for x in v if x == x)
    if not v:
        return float("nan")
    k = (len(v) - 1) * p
    f = int(math.floor(k))
    c = min(f + 1, len(v) - 1)
    return v[f] if f == c else v[f] * (c - k) + v[c] * (k - f)


def f3(x):
    return "  nan" if x != x else f"{x:.3f}"


def load():
    d = json.load(open(os.path.join(HERE, "area_feat.json"), encoding="utf-8"))
    rows = []
    for blk in d["data"]:
        for r in blk["rows"]:
            r = dict(r)
            r["video"] = blk["video"]
            r["feat"] = dict(r["feat"])
            r["feat"]["hoop_W"] = r["feat"]["hw"]
            rows.append(r)
    return rows


def by(rows, lab, vids=None):
    return [r for r in rows if r["label"] == lab
            and (vids is None or r["video"] in vids)]


NEW = ["2026-10-17_1", "2026-10-23_1", "2026-10-24_1",
       "2026-10-30_1", "2026-10-31_1", "2026-11-06_1"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", nargs="*", default=NEW)
    args = ap.parse_args()
    rows = load()
    vids = args.videos
    hit = by(rows, "hit", vids)
    fg = by(rows, "false_green", vids)
    dg = by(rows, "degraded", vids)
    nl = by(rows, "neutral_low", vids)
    H = len(hit) + len(fg)
    print(f"基线：high {H}（命中 {len(hit)} / 假绿 {len(fg)}）"
          f" = 查准 {len(hit)/H:.2%}；降级 {len(dg)} / 非进球低置信 {len(nl)}")

    # ---------------- 每场分布：确认阈值不会被单场绑架 ----------------
    print("\n" + "=" * 76)
    print("0. 逐场分布（a_rel 中位 / a_cv 中位）——看是否需要按场归一")
    print("=" * 76)
    print(f"  {'场':<14}{'筐W×H':>9}{'命中 a_rel':>12}{'假绿 a_rel':>12}"
          f"{'命中 a_cv':>12}{'假绿 a_cv':>12}")
    for v in vids:
        h_, f_ = by(rows, "hit", [v]), by(rows, "false_green", [v])
        if not h_ or not f_:
            print(f"  {v:<14} 样本不足")
            continue
        hw = f_[0]["feat"]["hw"]
        hh = f_[0]["feat"]["hh"]
        print(f"  {v:<14}{f'{hw:.0f}×{hh:.0f}':>9}"
              f"{f3(q([r['feat']['a_rel'] for r in h_], .5)):>12}"
              f"{f3(q([r['feat']['a_rel'] for r in f_], .5)):>12}"
              f"{f3(q([r['feat']['a_cv'] for r in h_], .5)):>12}"
              f"{f3(q([r['feat']['a_cv'] for r in f_], .5)):>12}")

    # ---------------- 查准侧：降档规则 ----------------
    print("\n" + "=" * 76)
    print("1. 查准侧（high → review 降档）：a_rel > TH")
    print("   「穿越点投影面积超过筐矩形 TH 倍」= 大块物（手/臂/身体），非球")
    print("=" * 76)
    print(f"  {'TH':>6}{'降档假绿':>9}{'误降命中':>9}{'查准后':>9}"
          f"{'查准Δ':>8}{'覆盖后':>9}{'覆盖Δ':>9}")
    base_p = len(hit) / H
    base_c = len(hit) / len(by(rows, "hit", vids) + by(rows, "degraded", vids))
    for th in (0.20, 0.25, 0.28, 0.30, 0.35, 0.40, 0.50, 0.60, 0.80, 1.00):
        df = [r for r in fg if r["feat"]["a_rel"] > th]
        dh = [r for r in hit if r["feat"]["a_rel"] > th]
        p = (len(hit) - len(dh)) / (H - len(dh) - len(df))
        c = (len(hit) - len(dh)) / (len(hit) + len(dg))
        print(f"  {th:>6.2f}{len(df):>9}{len(dh):>9}{p:>9.2%}"
              f"{p-base_p:>+8.2%}{c:>9.2%}{c-base_c:>+9.2%}")

    print("\n" + "=" * 76)
    print("1b. 查准侧组合：a_rel > TH1 **或** (a_rel > TH2 且 a_cv < 0.22)")
    print("    加 a_cv 是因为假绿多为「面积几乎不变的大块」（静止物/手）")
    print("=" * 76)
    best = []
    for th1 in (1.00, 0.80, 0.60, 0.50, 0.45, 0.40):
        for th2 in (0.30, 0.28, 0.25, 0.22, 0.20):
            for cv in (0.18, 0.22, 0.26):
                def hitrule(r):
                    f = r["feat"]
                    return f["a_rel"] > th1 or (f["a_rel"] > th2
                                               and f["a_cv"] < cv)
                df = [r for r in fg if hitrule(r)]
                dh = [r for r in hit if hitrule(r)]
                if not dh and not df:
                    continue
                p = (len(hit) - len(dh)) / (H - len(dh) - len(df)) \
                    if H - len(dh) - len(df) else 0
                best.append((p, len(dh), len(df), th1, th2, cv))
    best.sort(reverse=True)
    print(f"  {'查准后':>8}{'误降命中':>9}{'降档假绿':>9}   TH1 / TH2 / a_cv<")
    for p, dh, df, th1, th2, cv in best[:8]:
        print(f"  {p:>8.2%}{dh:>9}{df:>9}   {th1:.2f} / {th2:.2f} / {cv:.2f}")

    # ---------------- 查全侧：升档规则 ----------------
    print("\n" + "=" * 76)
    print("2. 查全侧（review/low → high 升档）：a_rel < TH 且 a_cv > CV")
    print("   「面积像球（小）且面积有正常波动」→ 补分升档")
    print("   升降级不伤查准（它们本不在 high）；只有误升非进球才算代价")
    print("=" * 76)
    print(f"  {'TH':>6}{'CV>':>6}{'捞回落级':>9}{'误升非进球':>11}"
          f"{'查准后':>9}{'查准Δ':>8}{'覆盖Δ':>8}")
    print(f"  （基线查准 {base_p:.2%}、覆盖 {base_c:.2%}）")
    bestp = []
    for th in (0.15, 0.18, 0.20, 0.22, 0.25, 0.30, 0.35):
        for cv in (0.15, 0.18, 0.20, 0.22, 0.25, 0.30):
            up_d = [r for r in dg if r["feat"]["a_rel"] < th
                    and r["feat"]["a_cv"] > cv]
            up_n = [r for r in nl if r["feat"]["a_rel"] < th
                    and r["feat"]["a_cv"] > cv]
            p = (len(hit) + len(up_d)) / (H + len(up_d) + len(up_n))
            c = (len(hit) + len(up_d)) / (len(hit) + len(dg))
            print(f"  {th:>6.2f}{cv:>6.2f}{len(up_d):>9}{len(up_n):>11}"
                  f"{p:>9.2%}{p-base_p:>+8.2%}{c-base_c:>+8.2%}")
            if p >= base_p - 1e-9 and len(up_d):
                bestp.append((c, p, len(up_d), len(up_n), th, cv))
    bestp.sort(reverse=True)
    if bestp:
        print("\n  ★ 查准不降前提下覆盖最高的 5 组：")
        print(f"  {'覆盖':>8}{'查准':>8}{'捞回':>7}{'误升':>6}   TH / CV>")
        for c, p, ud, un, th, cv in bestp[:5]:
            print(f"  {c:>8.2%}{p:>8.2%}{ud:>7}{un:>6}   {th:.2f} / {cv:.2f}")

    # ---------------- 只用 a_c（绝对像素）的对照 ----------------
    print("\n" + "=" * 76)
    print("3. 对照：只用 a_c 绝对像素（不归一）——若效果与 a_rel 相当，")
    print("   说明筐尺寸归一不是必要的复杂度")
    print("=" * 76)
    print(f"  {'TH(px²)':>9}{'降档假绿':>9}{'误降命中':>9}{'查准后':>9}")
    for th in (100, 130, 160, 200, 260, 320):
        df = [r for r in fg if r["feat"]["a_c"] > th]
        dh = [r for r in hit if r["feat"]["a_c"] > th]
        p = (len(hit) - len(dh)) / (H - len(dh) - len(df))
        print(f"  {th:>9}{len(df):>9}{len(dh):>9}{p:>9.2%}")


if __name__ == "__main__":
    main()
