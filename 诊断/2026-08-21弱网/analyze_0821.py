"""08-21 体检第二层：72 条未确认 high 行 vs 103 条已确认 high 行。

去球探针已证明 72 条**全是真网动**（post_noball 5.3~22.0，均 ≥ 静态门 5）
——「球扫 ROI 自己造信号」的假设死了。那这批行是什么？三条对照：

① **信号分布**：confirmed high vs unconfirmed high 的 post/ctrl/局部位比。
   若两簇重叠 → 网信号无分离器（同 B 轮结论），说明未确认行在信号上和真
   进球不可分 ⇒ 更可能是「未标注的真穿网」而非检测错误。
② **时间分布**：真进球应均匀铺满全场；若 72 条挤在片头/片尾（热身、散场
   自由投篮）或成串出现（连续试投）⇒ 形态是「练习投篮」而非比赛进球。
③ **用户裁决交叉**：truth 的 not_goal_verdicts（27 条用户当年**明确裁
   不是进球**）落在哪些行上——落在 72 里的才是**硬假绿**（有裁决背书）。
"""
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LAB = os.path.join(REPO, "诊断", "2026-08-21弱网")
sys.path.insert(0, REPO)

NAME = "2026-08-21_1"
WD = os.path.join(os.environ.get("TEMP", "/tmp"), "qa0821", NAME)
TOL = 3.0


def near(t, arr, tol=TOL):
    return any(abs(t - a) <= tol for a in arr)


def main():
    qa = json.load(open(os.path.join(LAB, "qa_0821_rows.json"), encoding="utf-8"))
    rows72 = qa["rows"]
    side = json.load(open(os.path.join(WD, "net_signals.json"), encoding="utf-8"))
    sig = side["entries"][0]["signals"]
    truth = json.load(open(rf"{REPO}\truth\{NAME}.json", encoding="utf-8"))
    confirmed = truth["confirmed_goals"]
    declined = truth["not_goal_verdicts"]

    # 已确认 high 行：72 与 114 的差集口径重算（用 sidecar 信号表逐键取）
    unconf_ts = [r["t"] for r in rows72]
    conf_high = []
    for k, v in sig.items():
        t = float(k)
        if near(t, confirmed) and not near(t, unconf_ts):
            conf_high.append((t, v))
    print(f"sidecar 覆盖 {len(sig)} 行（=全场 high 行数 175）")
    print(f"已确认 high {len(conf_high)}  未确认 high {len(rows72)}")

    def stats(pairs, label):
        if not pairs:
            return
        ps = sorted(p[1]["post"] for p in pairs)
        rs = sorted((p[1]["post"] / p[1]["ctrl"]) if p[1].get("ctrl") else 99
                    for p in pairs)
        loc = sum(1 for p in pairs if p[1].get("local"))
        med = ps[len(ps) // 2]
        print(f"  {label:14s} n={len(ps):3d}  post 中位 {med:5.2f} "
              f"[{ps[0]:5.2f}, {ps[-1]:6.2f}]  min {ps[0]:5.2f}"
              f"  局部位比中位 {rs[len(rs)//2]:5.1f}  local={loc}/{len(ps)}")

    print("\n① 信号分布")
    stats(conf_high, "已确认 high")
    stats([(r["t"], {"post": r["net_post"], "ctrl": r["net_ctrl"],
                     "local": r["net_local"]}) for r in rows72], "未确认 high")
    print("  → 两簇若完全重叠 = 网信号无分离器（无法用信号区分真进球/未确认行）")

    print("\n② 用户裁决交叉（not_goal_verdicts = 当年明确裁「不是进球」）")
    hard = [r for r in rows72 if near(r["t"], declined)]
    print(f"  落在 72 里的已裁非进球: {len(hard)}"
          + (f"  {[round(r['t'], 1) for r in hard]}" if hard else ""))
    print(f"  其余 {len(rows72) - len(hard)} 条 = 从未裁决过（真伪未知）")

    print("\n③ 时间分布（全场 6671s）")
    def bucket(ts, label):
        zones = [(0, 300, "片头 0-5min"), (300, 3000, "上半段"),
                 (3000, 6000, "下半段"), (6000, 6672, "片尾 100-111min")]
        out = []
        for a, b, nm in zones:
            n = sum(1 for t in ts if a <= t < b)
            out.append(f"{nm} {n}")
        print(f"  {label:12s} " + "  ".join(out) +
              f"   （该段时间占比 " +
              " ".join(f"{nm.split(' ')[0]}{(b-a)/6671.7:.0%}"
                       for a, b, nm in zones) + "）")
    bucket([r["t"] for r in rows72], "未确认 high")
    bucket(confirmed, "已确认进球")

    # 密集串：相邻未确认行间隔
    ts = sorted(r["t"] for r in rows72)
    gaps = [round(b - a, 1) for a, b in zip(ts, ts[1:])]
    tight = [g for g in gaps if g <= 20]
    print(f"\n④ 成串：相邻未确认行间隔 ≤20s 的有 {len(tight)} 段"
          f"（{len(ts)} 行里）")
    print("  末段（t>6000）逐行: " + " ".join(f"{t:.0f}" for t in ts if t > 6000))
    print("  片头（t<400）逐行: " + " ".join(f"{t:.1f}" for t in ts if t < 400))


if __name__ == "__main__":
    main()
