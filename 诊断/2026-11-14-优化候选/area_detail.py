# -*- coding: utf-8 -*-
r"""阶段6 续2：`a_rel > 0.5` 降档规则的稳健性核查（只读）。

要过的四关：
  1. 细扫阈值：0 误降的窗口有多宽？是不是卡在刀刃上（0.49 就崩）？
  2. 逐场分布：33 条假绿是不是集中在一场？单场绑架则不可用。
  3. 留一法：在 5 场定阈值、第 6 场验，方向是否一致。
  4. 样本外：11-07_1（不入评测集）。
  5. 物理复核：这 33 条长什么样（conf / reason / 面积 / 点数），
     是否都是「大块物横穿」这一种形态，而不是碰巧。
"""
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
NEW = ["2026-10-17_1", "2026-10-23_1", "2026-10-24_1",
       "2026-10-30_1", "2026-10-31_1", "2026-11-06_1"]
HOLD = "2026-11-07_1"


def load():
    d = json.load(open(os.path.join(HERE, "area_feat.json"), encoding="utf-8"))
    rows = []
    for blk in d["data"]:
        for r in blk["rows"]:
            r = dict(r)
            r["video"] = blk["video"]
            rows.append(r)
    return rows


def main():
    rows = load()
    new = [r for r in rows if r["video"] in NEW]
    hold = [r for r in rows if r["video"] == HOLD]

    # ---------- 1. 细扫 ----------
    print("=" * 74)
    print("1. 阈值细扫（新六场）——「0 误降」窗口有多宽")
    print("=" * 74)
    hit = [r for r in new if r["label"] == "hit"]
    fg = [r for r in new if r["label"] == "false_green"]
    H = len(hit) + len(fg)
    print(f"  {'TH':>6}{'降档假绿':>9}{'误降命中':>9}{'查准后':>9}{'Δ':>8}")
    for i in range(34, 61, 2):
        th = i / 100
        df = [r for r in fg if r["feat"]["a_rel"] > th]
        dh = [r for r in hit if r["feat"]["a_rel"] > th]
        p = (len(hit) - len(dh)) / (H - len(dh) - len(df))
        print(f"  {th:>6.2f}{len(df):>9}{len(dh):>9}{p:>9.2%}"
              f"{(p - len(hit) / H):>+8.2%}")

    print("\n  命中里 a_rel 最大的 12 条（决定阈值上不去的原因）：")
    for r in sorted(hit, key=lambda x: -x["feat"]["a_rel"])[:12]:
        f = r["feat"]
        print(f"    {r['video'][-10:]} t={r['t']:8.2f} conf={r['conf']:.2f} "
              f"a_rel={f['a_rel']:.3f} a_c={f['a_c']:.0f} cv={f['a_cv']:.2f} "
              f"hw={f['hw']:.0f}")

    # ---------- 2. 逐场 ----------
    print("\n" + "=" * 74)
    print("2. 逐场：a_rel>0.5 抓到多少假绿 / 误伤多少命中")
    print("=" * 74)
    print(f"  {'场':<14}{'high':>6}{'假绿':>6}{'抓到':>6}{'命中':>6}{'误伤':>6}")
    for v in NEW + [HOLD]:
        h_ = [r for r in rows if r["video"] == v and r["label"] == "hit"]
        f_ = [r for r in rows if r["video"] == v and r["label"] == "false_green"]
        print(f"  {v:<14}{len(h_) + len(f_):>6}{len(f_):>6}"
              f"{sum(1 for r in f_ if r['feat']['a_rel'] > .5):>6}"
              f"{len(h_):>6}"
              f"{sum(1 for r in h_ if r['feat']['a_rel'] > .5):>6}")

    # ---------- 3. 留一法 ----------
    print("\n" + "=" * 74)
    print("3. 留一法：另 5 场里取「0 误降且降档最多」的阈值 → 本场验证")
    print("=" * 74)
    print(f"  {'场':>14}{'train TH':>10}{'本场抓':>8}{'本场误伤':>9}{'净查准':>9}")
    for v in NEW:
        tr_h = [r for r in hit if r["video"] != v]
        tr_f = [r for r in fg if r["video"] != v]
        best, bth = -1, None
        for i in range(30, 101, 1):
            th = i / 100
            if any(r["feat"]["a_rel"] > th for r in tr_h):
                continue          # 该阈值在训练集会误降 → 不可用
            n = sum(1 for r in tr_f if r["feat"]["a_rel"] > th)
            if n >= best:
                best, bth = n, th
        if bth is None:
            print(f"  {v:>14}  训练集找不到 0 误降阈值")
            continue
        vh = [r for r in hit if r["video"] == v]
        vf = [r for r in fg if r["video"] == v]
        c = sum(1 for r in vf if r["feat"]["a_rel"] > bth)
        d = sum(1 for r in vh if r["feat"]["a_rel"] > bth)
        Hv = len(vh) + len(vf)
        p0 = len(vh) / Hv if Hv else 0
        p1 = (len(vh) - d) / (Hv - d - c) if Hv - d - c else 0
        print(f"  {v:>14}{bth:>10.2f}{c:>8}{d:>9}{p1 - p0:>+9.2%}")

    # ---------- 4. 样本外 ----------
    print("\n" + "=" * 74)
    print("4. 样本外 11-07_1（不入评测集）")
    print("=" * 74)
    hh = [r for r in hold if r["label"] == "hit"]
    hf = [r for r in hold if r["label"] == "false_green"]
    if hh and hf:
        Hh = len(hh) + len(hf)
        c = sum(1 for r in hf if r["feat"]["a_rel"] > .5)
        d = sum(1 for r in hh if r["feat"]["a_rel"] > .5)
        print(f"  high {Hh}（命中 {len(hh)} / 假绿 {len(hf)}）")
        print(f"  a_rel>0.5 → 抓假绿 {c}、误伤命中 {d}")
        print(f"  查准 {len(hh)/Hh:.2%} → {(len(hh)-d)/(Hh-c-d):.2%}")

    # ---------- 5. 物理复核 ----------
    print("\n" + "=" * 74)
    print("5. 被降档的 33 条假绿：逐条形态（确认是「大块物」而非碰巧）")
    print("=" * 74)
    picked = sorted([r for r in fg if r["feat"]["a_rel"] > .5],
                    key=lambda x: -x["feat"]["a_rel"])
    print(f"  {'场':<12}{'t':>9}{'conf':>6}{'a_rel':>7}{'a_c':>7}"
          f"{'cv':>6}{'up':>4}{'dn':>4}{'off':>7}  reason")
    for r in picked:
        f = r["feat"]
        rs = r["review_reason"] or r["low_reason"] or ""
        print(f"  {r['video'][-10:]:<12}{r['t']:>9.2f}{r['conf']:>6.2f}"
              f"{f['a_rel']:>7.3f}{f['a_c']:>7.0f}{f['a_cv']:>6.2f}"
              f"{r['above_n'] if r['above_n'] is not None else 0:>4}"
              f"{r['below_n'] if r['below_n'] is not None else 0:>4}"
              f"{r['cross_off']:>7.2f}  {rs[:24]}")
    print(f"\n  合计 {len(picked)} 条；a_c 中位 "
          f"{sorted(r['feat']['a_c'] for r in picked)[len(picked)//2]:.0f}px²，"
          f"筐面积中位 "
          f"{sorted(r['feat']['hoop_area'] for r in picked)[len(picked)//2]:.0f}px²")

    # 对照：没被抓到的 171 条假绿，面积分布如何（说明规则只覆盖一部分）
    rest = [r for r in fg if r["feat"]["a_rel"] <= .5]
    print(f"  未抓到的 {len(rest)} 条假绿：a_rel 中位 "
          f"{sorted(r['feat']['a_rel'] for r in rest)[len(rest)//2]:.3f}"
          f"（与命中同量级，面积不是它们错的原因）")


if __name__ == "__main__":
    main()
