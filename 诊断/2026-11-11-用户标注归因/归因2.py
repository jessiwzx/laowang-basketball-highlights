# -*- coding: utf-8 -*-
"""用户标注归因：区分「误判」与「漏判」，并找出可动的规则。

数据来源：各场 feedback.json 的 verdicts（用户逐条亲眼看视频的判定）。
  not_goal → 用户判定系统报错（确定误判）
  goal     → 用户判定系统降级错了 / 界面看不见（确定漏判）

关键：goal 的 status=low 表示用户在界面上根本看不到，属真漏判。
"""
import json
import os
import statistics as st
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UPSTREAM = r"D:\claude code\视频剪辑\work"
LOCAL = os.path.join(ROOT, "work")


def collect():
    out, seen = [], set()
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
            out += [(name, v) for v in vs]
    return out


def pct(n, d):
    return "%.0f%%" % (100.0 * n / d) if d else "-"


def main():
    rows = collect()
    NG = [r for r in rows if r[1].get("verdict") == "not_goal"]
    GL = [r for r in rows if r[1].get("verdict") == "goal"]
    print("总标注 %d（误判 %d / 漏判·误降 %d）" % (len(rows), len(NG), len(GL)))
    print()

    print("## 1 上方证据缺失是不是误降主因")
    print()
    print("| 组 | n | above_n=0 | above_n≤1 | above_span<0.2 | conf 中位 | conf<0.60 |")
    print("|---|---|---|---|---|---|---|")
    for name, grp in (("用户判对 goal", GL), ("用户判错 not_goal", NG)):
        ev = [r[1].get("evidence") or {} for r in grp]
        z = sum(1 for e in ev if (e.get("above_n") or 0) == 0)
        one = sum(1 for e in ev if (e.get("above_n") or 0) <= 1)
        sp = sum(1 for e in ev if (e.get("above_span") or 0) < 0.2)
        cf = [e.get("confidence") for e in ev
              if isinstance(e.get("confidence"), (int, float))]
        lt = sum(1 for x in cf if x < 0.60)
        print("| %s | %d | %d (%s) | %d (%s) | %d (%s) | %.2f | %d (%s) |"
              % (name, len(grp), z, pct(z, len(grp)), one, pct(one, len(grp)),
                 sp, pct(sp, len(grp)), st.median(cf) if cf else 0,
                 lt, pct(lt, len(cf))))
    print()

    print("## 2 界面上看不见的真球（goal 且 status=low）")
    print()
    low = [r for r in GL if (r[1].get("evidence") or {}).get("status") == "low"]
    print("共 %d 条。按系统给出的降级原因：" % len(low))
    print()
    for k, n in Counter(((r[1].get("evidence") or {}).get("low_reason") or "(无)")
                        for r in low).most_common():
        print("- %s : **%d**" % (k, n))
    print()
    if low:
        print("| 场 | t | conf | cross_off | ab_n | be_n | descent | 降级原因 |")
        print("|---|---|---|---|---|---|---|---|")
        for v, x in sorted(low, key=lambda r: (r[0], r[1].get("t") or 0))[:60]:
            e = x.get("evidence") or {}
            print("| %s | %.1f | %.2f | %+.2f | %d | %d | %.1f | %s |"
                  % (v[5:], x.get("t") or 0, e.get("confidence") or 0,
                     e.get("cross_off") or 0, e.get("above_n") or 0,
                     e.get("below_n") or 0, e.get("descent_px") or 0,
                     (e.get("low_reason") or "")[:20]))
        print()

    print("## 3 被「球心出筐口两端」杀掉、但用户说是真球的")
    print()
    cand = [r for r in GL if "筐口两端" in ((r[1].get("evidence") or {})
                                            .get("low_reason") or "")]
    print("共 %d 条，|cross_off| 分布：" % len(cand))
    offs = sorted(abs((r[1].get("evidence") or {}).get("cross_off") or 0)
                  for r in cand)
    if offs:
        print("- 中位 %.2f，范围 %.2f ~ %.2f" % (st.median(offs), offs[0], offs[-1]))
        print("- 阈值 CROSS_KILL_OFF = 0.45，其中 %.0f%% 在此附近"
              % (100.0 * sum(1 for o in offs if o < 0.55) / len(offs)))
    print()

    print("## 4 用户判错但系统给满分的（最伤成片的一批）")
    print()
    hi = [(v, x) for v, x in NG
          if ((x.get("evidence") or {}).get("confidence") or 0) >= 0.84]
    print("conf ≥ 0.84 的误判：**%d 条**" % len(hi))
    print()
    if hi:
        print("| 场 | t | conf | cross_off | ab_n | be_n | descent | net_post | net_local |")
        print("|---|---|---|---|---|---|---|---|---|")
        for v, x in sorted(hi, key=lambda r: -((r[1].get("evidence") or {})
                                               .get("confidence") or 0))[:15]:
            e = x.get("evidence") or {}
            print("| %s | %.1f | %.2f | %+.2f | %d | %d | %.1f | %s | %s |"
                  % (v[5:], x.get("t") or 0, e.get("confidence") or 0,
                     e.get("cross_off") or 0, e.get("above_n") or 0,
                     e.get("below_n") or 0, e.get("descent_px") or 0,
                     e.get("net_post"), e.get("net_local")))
        print()


if __name__ == "__main__":
    main()
