# -*- coding: utf-8 -*-
"""汇总所有场次 feedback.json 里用户逐条标记的 verdict，按目标统计。

verdict == "not_goal" → 用户判定系统报错了（确定的误判）
verdict == "goal"     → 用户判定系统漏了/降级错了（确定的漏判或误降级）

每条 verdict 自带当时的证据快照（confidence/status/cross_off/above_n/...），
所以不需要重跑检测即可归因。
"""
import json
import os
import statistics as st
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UPSTREAM = r"D:\claude code\视频剪辑\work"
LOCAL = os.path.join(ROOT, "work")

FIELDS = ("confidence", "cross_off", "above_n", "below_n", "above_span",
          "below_span", "descent_px", "below_big", "net_post", "net_ctrl")


def collect():
    """→ [(场, verdict, evidence)]；同名场以本地仓为准（更新的标注）。"""
    out = []
    seen = set()
    for root in (LOCAL, UPSTREAM):
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            p = os.path.join(root, name, "feedback.json")
            if not os.path.exists(p) or name in seen:
                continue
            try:
                doc = json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            vs = doc.get("verdicts") if isinstance(doc, dict) else doc
            if not vs:
                continue
            seen.add(name)
            for v in vs:
                out.append((name, v.get("verdict"), v.get("evidence") or {},
                            v.get("marked_at", ""), v.get("t")))
    return out


def med(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return st.median(xs) if xs else None


def main():
    rows = collect()
    if not rows:
        print("没有找到任何 feedback")
        return

    by_scene = defaultdict(Counter)
    for v, verdict, ev, _, _ in rows:
        by_scene[v][verdict] += 1

    print("## 一、逐场标记量")
    print()
    print("| 场 | goal（漏/误降） | not_goal（误判） | 合计 | 最近标记 |")
    print("|---|---|---|---|---|")
    tot = Counter()
    for v in sorted(by_scene):
        c = by_scene[v]
        last = max((r[3] for r in rows if r[0] == v), default="")[:10]
        tot.update(c)
        print("| %s | %d | %d | %d | %s |" % (v, c["goal"], c["not_goal"],
                                              sum(c.values()), last))
    print("| **合计** | **%d** | **%d** | **%d** | — |"
          % (tot["goal"], tot["not_goal"], sum(tot.values())))
    print()

    NG = [r for r in rows if r[1] == "not_goal"]
    GL = [r for r in rows if r[1] == "goal"]

    print("## 二、误判（not_goal）当时在哪个档 —— 决定伤不伤成片")
    print()
    c = Counter((r[2].get("status") or "?") for r in NG)
    for k in ("high", "review", "low"):
        n = c.get(k, 0)
        print("- **%s**: %d 条 (%.0f%%)" % (k, n, 100.0 * n / max(1, len(NG))))
    print()

    print("## 三、误判（not_goal）的证据特征（高优档 vs 用户判错）")
    print()
    for grp, name in ((NG, "全部 not_goal"),):
        hi = [r for r in grp if (r[2].get("status") == "high")]
        print("### %s   n=%d（其中 high %d）" % (name, len(grp), len(hi)))
        print()
        print("| 特征 | 中位 | p10 | p90 | 缺失 |")
        print("|---|---|---|---|---|")
        for f in FIELDS:
            xs = sorted(r[2].get(f) for r in grp
                        if isinstance(r[2].get(f), (int, float)))
            if not xs:
                print("| %s | - | - | - | %d |" % (f, len(grp)))
                continue
            print("| %s | %.3f | %.3f | %.3f | %d |"
                  % (f, st.median(xs), xs[int(.1 * len(xs))],
                     xs[min(len(xs) - 1, int(.9 * len(xs)))], len(grp) - len(xs)))
        print()

    print("## 四、漏判/误降（goal）当时在哪个档")
    print()
    c = Counter((r[2].get("status") or "?") for r in GL)
    for k in ("high", "review", "low"):
        n = c.get(k, 0)
        print("- **%s**: %d 条 (%.0f%%)" % (k, n, 100.0 * n / max(1, len(GL))))
    print()
    print("### goal 的 low_reason 分布（系统当初为什么降它）")
    print()
    for k, n in Counter((r[2].get("low_reason") or "(无)") for r in GL).most_common():
        print("- %s : %d" % (k, n))
    print()
    print("### goal 的 pop_back / review_reason 线索")
    print()
    pb = sum(1 for r in GL if r[2].get("pop_back") is not None)
    print("- 有 pop_back（疑似弹出）: %d / %d" % (pb, len(GL)))
    print()

    print("## 五、网分在两组里的对比（这是判定层唯一有效证据）")
    print()
    print("| 组 | net_post 中位 | net_post p25 | net_post p75 | net_local True |n|")
    print("|---|---|---|---|---|")
    for name, grp in (("用户判错 not_goal", NG), ("用户判对 goal", GL)):
        xs = sorted(r[2].get("net_post") for r in grp
                    if isinstance(r[2].get("net_post"), (int, float)))
        loc = [r[2].get("net_local") for r in grp]
        if not xs:
            print("| %s | - | - | - | - | %d |" % (name, len(grp)))
            continue
        print("| %s | %.2f | %.2f | %.2f | %d/%d | %d |"
              % (name, st.median(xs), xs[len(xs) // 4],
                 xs[min(len(xs) - 1, 3 * len(xs) // 4)],
                 sum(1 for x in loc if x is True), len(loc), len(xs)))
    print()


if __name__ == "__main__":
    main()
