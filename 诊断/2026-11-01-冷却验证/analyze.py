"""增量分层：按"距最近基线行的间隔"分桶，判断增量是同一颗球的重复
（应当继续挡）还是被误吞的后续候选（应当放行）。

用法: python analyze.py
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TRUTH_DIR = os.path.join(ROOT, "truth")

BUCKETS = [(0.0, 0.3), (0.3, 0.7), (0.7, 1.5), (1.5, 3.0), (3.0, 1e9)]


def match_high(evs, goals, tol):
    """官方口径：high 行按 tol 贪心命中 truth。"""
    ts = sorted(e["t"] for e in evs if e["status"] == "high")
    used, hit = set(), 0
    for t in ts:
        best, bi = None, None
        for i, g in enumerate(goals):
            if i in used:
                continue
            d = abs(t - g)
            if d <= tol and (best is None or d < best):
                best, bi = d, i
        if bi is not None:
            used.add(bi)
            hit += 1
    return len(ts), hit


def main():
    r = json.load(open(os.path.join(HERE, "cooldown_probe_result.json"),
                      encoding="utf-8"))
    tot = {"n": 0, "high": 0, "review": 0, "low": 0,
           "hit": 0, "b": {f"{lo}~{hi}": {"n": 0, "high": 0, "hit": 0}
                           for lo, hi in [(f"{a:g}", f"{b:g}")
                                          for a, b in BUCKETS]}}
    for name, rec in r.items():
        gt = json.load(open(os.path.join(TRUTH_DIR, name + ".json"),
                            encoding="utf-8"))
        goals = sorted(gt["goals"])
        tol = float(gt.get("tolerance") or 0.2)
        base_ev = [{"t": t, "status": "high"} for t in rec["base_t"]]
        # 基线只看 high 无法还原——从明细里反推：用 A_base 计数校验即可
        det = rec["增量明细"]
        print(f"\n=== {name}  truth {len(goals)} 球 tol={tol} ===")
        print(f"  A 自动档 high {rec['A_base']['high']}  增量 {rec['增量']} 条"
              f"（high {rec['增量_high']}）")
        for lo, hi in BUCKETS:
            key = f"{lo:g}~{hi:g}"
            sel = [d for d in det
                   if lo <= (d["gap_base"] if d["gap_base"] is not None else 9e9) < hi]
            if not sel:
                continue
            h = sum(1 for d in sel if d["status"] == "high")
            # 这批行里有几条能在 tol 内命中真值
            used, hitn = set(), 0
            for d in sel:
                best, bi = None, None
                for i, g in enumerate(goals):
                    if i in used:
                        continue
                    dd = abs(d["t"] - g)
                    if dd <= tol and (best is None or dd < best):
                        best, bi = dd, i
                if bi is not None:
                    used.add(bi)
                    hitn += 1
            print(f"    与基线间隔 {key:>9}s : {len(sel):>2} 条  "
                  f"high {h:>2}  review {sum(1 for d in sel if d['status']=='review'):>2}"
                  f"  low {sum(1 for d in sel if d['status']=='low'):>2}"
                  f"   命中真值 {hitn}")
            t = tot["b"][key]
            t["n"] += len(sel)
            t["high"] += h
            t["hit"] += hitn
        tot["n"] += rec["增量"]
        tot["high"] += rec["增量_high"]
        tot["review"] += rec["增量_review"]
        tot["low"] += rec["增量_low"]

    print("\n=== 三场合计 ===")
    print(f"  增量 {tot['n']} 条：high {tot['high']} / review {tot['review']}"
          f" / low {tot['low']}")
    for key, v in tot["b"].items():
        if v["n"]:
            print(f"    间隔 {key:>9}s : {v['n']:>3} 条  high {v['high']:>2}"
                  f"   命中真值 {v['hit']}")
    print("\n  注：与基线间隔 <0.3s 的行物理上不可能是另一颗球"
          "（一次穿越的多次配对）；≥1.5s 才是真正被吞掉的后续候选。")


if __name__ == "__main__":
    main()
