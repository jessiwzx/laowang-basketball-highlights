# -*- coding: utf-8 -*-
r"""L0 复现差异归因（阶段 0，只读）。

把 replay_l0.py 的「conf / status 对不上」逐条拆开：
  - 落在哪个场次 / 老组还是新组
  - Δconf 分布（stored - replay）
  - 差异能否由「存储字段三位小数」的边界翻转解释
      H1 侧分边界：n>=2 且 span 距 CONT_SPAN_SEC 在 ±0.002 内
      H2 位置分边界：|cross_off| 距 POS_TIERS 档位界在 ±0.002 内
  - 差异是否符合「老常量 vs 新常量」的整档差（0.03/0.04/0.06/0.26/0.30）
  - low_reason 分支：存储 low 但几何分够 0.5，或反之

不改任何 events / 不写回，只打印报表 + 落一份 json 明细。
"""
import json
import os
import sys
from collections import Counter, defaultdict

ROOT = r"D:\claude code\视频剪辑YOLO"
ARM = r"D:\claude code\yolo\eval_out\v20_c03_s1"
UP = r"D:\claude code\视频剪辑\work"
LOCAL = os.path.join(ROOT, "work")
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)

import eval_lib as E                                              # noqa: E402
import evaluate as ev                                             # noqa: E402
from bbh.goal_decider import (HIGH_CONF, MIN_CONF, CONT_SPAN_SEC,  # noqa: E402
                              POS_TIERS)

OLD = ["2026-08-22_1", "2026-08-28_1", "2026-08-29_3",
       "2026-09-04_1", "2026-09-11_3", "2026-09-12_1"]
NEW = ["2026-10-17_1", "2026-10-23_1", "2026-10-24_1",
       "2026-10-30_1", "2026-10-31_1", "2026-11-06_1"]
VIDEOS = OLD + NEW

FULL, SHORT, ONE = 0.20, 0.14, 0.05
NET_TH = 5.0
NET_STATIC, NET_GLOBAL = -0.30, -0.26


def events_path(v):
    return os.path.join(ARM if v in OLD else LOCAL, v, "events.json")


def del_dir(v):
    return os.path.join(UP if v in OLD else LOCAL, v)


def load_kept(v):
    events = E.load_events(events_path(v))
    d = del_dir(v)
    deleted = ev._load_deleted_seconds(os.path.join(d, "events.json"))
    inter = ev.INTERMISSION_GOALS.get(v, [])
    if deleted or inter:
        drop = ev._deleted_exclusions([e["t"] for e in events], deleted + inter)
        events = [e for i, e in enumerate(events) if i not in drop]
    return events


def side(n, span):
    if n >= 2 and span >= CONT_SPAN_SEC:
        return FULL
    if n >= 2:
        return SHORT
    if n == 1:
        return ONE
    return 0.0


def pos(off):
    a = abs(off)
    for lim, sc in POS_TIERS:
        if a <= lim:
            return sc
    return 0.0


def net_term(e):
    post, local = e.get("net_post"), e.get("net_local")
    if post is None:
        return 0.0
    if post < NET_TH:
        return NET_STATIC
    if local is False:
        return NET_GLOBAL
    return 0.0


def replay(e):
    g = 0.3 + side(e["above_n"], e["above_span"]) \
        + side(e["below_n"], e["below_span"]) + pos(e["cross_off"])
    g = round(g, 2)
    low = e.get("low_reason")
    if low and "证据点太少" in str(low) and g >= MIN_CONF:
        low = None
    conf = round(g + net_term(e), 2)
    if low is not None:
        st = "low"
    elif e.get("review_reason") is not None or e.get("pop_back") is not None:
        st = "review"
    else:
        st = "high" if conf >= HIGH_CONF else "review" if conf >= MIN_CONF else "low"
    return g, conf, st, low


def near_span_boundary(span):
    return abs(span - CONT_SPAN_SEC) <= 0.002


def near_pos_boundary(off):
    a = abs(off)
    return any(abs(a - lim) <= 0.002 for lim, _ in POS_TIERS)


def classify(e, dconf):
    """→ 标签列表（可多标签）。"""
    tags = []
    sides_amb = 0
    for n, span in (("above_n", "above_span"), ("below_n", "below_span")):
        if e[n] >= 2 and near_span_boundary(e[span]):
            sides_amb += 1
    if sides_amb and abs(abs(dconf) - FULL + SHORT) < 1e-9 * sides_amb + 1e-9:
        tags.append("H1_侧分跨度边界")
    elif sides_amb:
        tags.append("H1?跨度边界(Δ不配)")
    if near_pos_boundary(e["cross_off"]):
        tags.append("H2_位置分档位边界")
    if abs(abs(dconf) - 0.30) < 1e-9:
        tags.append("Δ=0.30_网静止档")
    if abs(abs(dconf) - 0.26) < 1e-9:
        tags.append("Δ=0.26_网全局档")
    if e.get("low_reason"):
        tags.append("存储为low")
    if e.get("review_reason") is not None:
        tags.append("硬门review:" + str(e.get("review_reason")))
    if e.get("pop_back") is not None:
        tags.append("pop_back")
    return tags


def main():
    rec = []
    tot = Counter()
    bad_by_v = Counter()
    for v in VIDEOS:
        events = load_kept(v)
        grp = "old" if v in OLD else "new"
        for e in events:
            tot[grp] += 1
            g, conf, st, low = replay(e)
            dc = round(e["confidence"] - conf, 4)
            if abs(dc) > 0.011 or e["status"] != st:
                bad_by_v[v] += 1
                rec.append({
                    "video": v, "group": grp, "t": e["t"],
                    "stored_conf": e["confidence"], "replay_conf": conf,
                    "dconf": dc, "stored_status": e["status"], "replay_status": st,
                    "geom_replay": g,
                    "above_n": e["above_n"], "above_span": e["above_span"],
                    "below_n": e["below_n"], "below_span": e["below_span"],
                    "cross_off": e["cross_off"],
                    "net_post": e.get("net_post"), "net_local": e.get("net_local"),
                    "low_reason": e.get("low_reason"),
                    "review_reason": e.get("review_reason"),
                    "pop_back": e.get("pop_back"),
                    "tags": classify(e, dc),
                })

    print("=" * 78)
    print(f"总行数 老组 {tot['old']} / 新组 {tot['new']} = {sum(tot.values())}")
    print(f"不一致 {len(rec)} 条：老组 "
          f"{sum(1 for r in rec if r['group'] == 'old')} / 新组 "
          f"{sum(1 for r in rec if r['group'] == 'new')}")
    print("-" * 78)
    print("逐场：", dict(bad_by_v))
    print("=" * 78)

    # ---- Δconf 分布 ----
    print("\n【1】Δconf（stored − replay）分布")
    for k, n in sorted(Counter(r["dconf"] for r in rec).items()):
        print(f"  {k:>8.2f}  ×{n}")
    print("  conf 一致但 status 不同：",
          sum(1 for r in rec if abs(r["dconf"]) <= 0.011))

    # ---- 标签统计 ----
    print("\n【2】归因标签（可多标）")
    for k, n in Counter(t for r in rec for t in r["tags"]).most_common():
        print(f"  {k:<24}{n}")
    no_tag = [r for r in rec if not r["tags"]]
    print(f"  未命中任何假设          {len(no_tag)}")

    # ---- 边界假设单独校核 ----
    print("\n【3】H1 跨度边界：n≥2 且 |span−0.2|≤0.002 的行（全库，不限于不一致）")
    h1_tot = h1_bad = 0
    for v in VIDEOS:
        for e in load_kept(v):
            amb = [s for n, s in (("above_n", "above_span"),
                                  ("below_n", "below_span"))
                   if e[n] >= 2 and near_span_boundary(e[s])]
            if amb:
                h1_tot += 1
                _g, conf, _st, _l = replay(e)
                if abs(e["confidence"] - conf) > 0.011:
                    h1_bad += 1
    print(f"  边界行 {h1_tot} 条，其中不一致 {h1_bad} 条")

    print("\n【4】H2 位置分边界：|cross_off| 距档位界 ≤0.002 的行")
    h2_tot = h2_bad = 0
    for v in VIDEOS:
        for e in load_kept(v):
            if near_pos_boundary(e["cross_off"]):
                h2_tot += 1
                _g, conf, _st, _l = replay(e)
                if abs(e["confidence"] - conf) > 0.011:
                    h2_bad += 1
    print(f"  边界行 {h2_tot} 条，其中不一致 {h2_bad} 条")

    # ---- low_reason 分支 ----
    print("\n【5】low_reason 分支核对")
    for tag, sub in (("存储 low（任何原因，全库）",
                      [(v, e) for v in VIDEOS for e in load_kept(v)
                       if e.get("low_reason")]),):
        c = Counter()
        for v, e in sub:
            low = str(e["low_reason"])
            g = 0.3 + side(e["above_n"], e["above_span"]) \
                + side(e["below_n"], e["below_span"]) + pos(e["cross_off"])
            g = round(g, 2)
            kind = "证据点太少" if "证据点太少" in low else "其他物理否证"
            c[(kind, "geom>=0.5" if g >= MIN_CONF else "geom<0.5")] += 1
        print(f"  {tag} {len(sub)} 条")
        for k, n in sorted(c.items()):
            print(f"    {k[0]:<12} {k[1]:<12} ×{n}")

    # ---- 明细 ----
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "l0_attr.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    print(f"\n明细 → {out}")

    print("\n【6】不一致明细（前 40 条）")
    hdr = (f"{'场次':<14}{'t':>9}{'stored':>7}{'replay':>7}{'Δ':>7}"
           f"{'status':>16}  {'an':>3}{'as':>6}{'bn':>3}{'bs':>6}"
           f"{'off':>7}{'post':>6}{'loc':>6}  tags")
    print(hdr)
    for r in sorted(rec, key=lambda x: (x["video"], x["t"]))[:40]:
        print(f"{r['video']:<14}{r['t']:>9.2f}{r['stored_conf']:>7.2f}"
              f"{r['replay_conf']:>7.2f}{r['dconf']:>7.2f}"
              f"{r['stored_status'] + '→' + r['replay_status']:>16}"
              f"{r['above_n']:>3}{r['above_span']:>6.2f}"
              f"{r['below_n']:>3}{r['below_span']:>6.2f}"
              f"{r['cross_off']:>7.3f}"
              f"{(r['net_post'] if r['net_post'] is not None else -1):>6.2f}"
              f"{str(r['net_local'])[:4]:>6}  {','.join(r['tags'])}")


if __name__ == "__main__":
    main()
