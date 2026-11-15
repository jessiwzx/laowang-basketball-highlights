"""冷却口径对比（只读：吃已有 positions.json，不跑检测、不写 events）。

三口径：
  A base       = 现状 v19（decide_goals 默认，所有非 low 行占 3s 钟）
  B high_only  = 只有 high 行占钟（review 与 low 同级，low 之间仍 3s 去重）
  C nocooldown = 不占钟（等价于"不因冷却删任何候选"；low 之间仍 3s 去重）

用法: python cooldown_probe.py <work_dir> [...]
"""
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from bbh import goal_decider as gd, static_filter          # noqa: E402
from bbh.models import BallPos, Calibration                 # noqa: E402
from bbh.pipeline import _merge_segment_events              # noqa: E402

TRUTH_DIR = os.path.join(ROOT, "truth")


def load_blocks(path: str):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    raw = d.get("segments")
    if raw is None:
        raw = [d]
    blocks = []
    for s in raw:
        c = s["calib"]
        calib = Calibration(hoop_rect=tuple(c[0]), hsv_low=tuple(c[1]),
                            hsv_high=tuple(c[2]), frame_size=tuple(c[3]))
        n = s.get("positions_len", len(s.get("positions", [])))
        pos = [BallPos(t=r[0], x=r[1], y=r[2], area=r[3],
                       fi=r[4] if len(r) > 4 else -1)
               for r in s["positions"][:n]]
        blocks.append({"t0": float(s.get("t0") or 0.0),
                       "calib": calib, "positions": pos})
    return d, blocks


def run(blocks, keep):
    per = []
    for b in blocks:
        pts = static_filter.apply(b["positions"], b["calib"])
        per.append(gd.decide_goals(pts, b["calib"], cooldown_keep_ts=keep))
    return _merge_segment_events(per, [b["t0"] for b in blocks])


def run_high_only(blocks, rounds=4):
    ev = run(blocks, set())
    for _ in range(rounds):
        keep = {round(e.t, 3) for e in ev if e.status == "high"}
        ev2 = run(blocks, keep)
        if [(round(e.t, 3), e.status) for e in ev2] == \
           [(round(e.t, 3), e.status) for e in ev]:
            break
        ev = ev2
    return ev


def load_truth(name: str):
    p = os.path.join(TRUTH_DIR, name + ".json")
    if not os.path.exists(p):
        return None, None
    with open(p, encoding="utf-8") as f:
        t = json.load(f)
    goals = sorted(t.get("goals") or t.get("confirmed_goals") or [])
    tol = float(t.get("tolerance") or 1.5)
    return goals, tol


def match(ts, goals, tol):
    """时间戳 → 命中的 truth 目标索引（贪心，每个目标只用一次）。"""
    hit, used = {}, set()
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
            hit[t] = bi
    return hit


def counts(evs):
    c = {"high": 0, "review": 0, "low": 0}
    for e in evs:
        c[e.status] = c.get(e.status, 0) + 1
    return c


def main(dirs):
    report = {}
    for wd in dirs:
        name = os.path.basename(wd.rstrip("/\\"))
        p = os.path.join(wd, "positions.json")
        if not os.path.exists(p):
            print(f"{name}: 无 positions.json，跳过")
            continue
        doc, blocks = load_blocks(p)
        pts_n = sum(len(b["positions"]) for b in blocks)
        hw = [b["calib"].hoop_rect[2] for b in blocks]

        base = run(blocks, None)
        no_cd = run(blocks, set())
        high_only = run_high_only(blocks)

        goals, tol = load_truth(name)
        base_ts = [e.t for e in base]
        no_ts = [e.t for e in no_cd]
        extra = [e for e in no_cd
                 if not any(abs(e.t - b.t) <= 0.05 for b in base)]

        # 增量行归属：命中 truth 的目标是否已被基线占（重复 vs 救回）
        dup, rescued, noise = 0, 0, 0
        gaps = []
        if goals:
            base_hit = match(base_ts, goals, tol or 1.5)
            base_targets = set(base_hit.values())
            no_hit = match(no_ts, goals, tol or 1.5)
            for e in extra:
                bi = no_hit.get(e.t)
                if bi is None:
                    noise += 1
                elif bi in base_targets:
                    dup += 1
                else:
                    rescued += 1
                    base_targets.add(bi)
        for e in extra:
            if base_ts:
                gaps.append(min(abs(e.t - t) for t in base_ts))

        # 归因：距最近 truth 目标的距离（宽窗，判定时刻与真值本来就有 0.1~0.5s 偏差）
        detail = []
        for e in extra:
            dt = min((abs(e.t - g) for g in goals), default=None) if goals else None
            db = min((abs(e.t - t) for t in base_ts), default=None) if base_ts else None
            detail.append({"t": round(e.t, 3), "status": e.status,
                           "conf": e.confidence,
                           "low_reason": e.low_reason,
                           "review_reason": e.review_reason,
                           "cross_off": e.cross_off,
                           "above_n": e.above_n, "below_n": e.below_n,
                           "descent": e.descent_px,
                           "dist_truth": round(dt, 3) if dt is not None else None,
                           "gap_base": round(db, 3) if db is not None else None})
        band = {"<0.3": 0, "0.3~0.8": 0, "0.8~1.5": 0, "1.5~3": 0, ">3": 0}
        for d in detail:
            v = d["dist_truth"]
            if v is None:
                continue
            if v < 0.3:
                band["<0.3"] += 1
            elif v < 0.8:
                band["0.3~0.8"] += 1
            elif v < 1.5:
                band["0.8~1.5"] += 1
            elif v < 3.0:
                band["1.5~3"] += 1
            else:
                band[">3"] += 1

        rec = {
            "场次": name, "时长s": round((doc.get("meta") or {}).get("duration") or 0),
            "段数": len(blocks), "筐宽": hw, "点数": pts_n,
            "A_base": counts(base), "A_total": len(base),
            "B_high_only": counts(high_only), "B_total": len(high_only),
            "C_no_cooldown": counts(no_cd), "C_total": len(no_cd),
            "增量": len(extra),
            "增量_high": sum(1 for e in extra if e.status == "high"),
            "增量_review": sum(1 for e in extra if e.status == "review"),
            "增量_low": sum(1 for e in extra if e.status == "low"),
            "与基线最近间隔_中位s": round(statistics.median(gaps), 2) if gaps else None,
            "与基线最近间隔_min_s": round(min(gaps), 2) if gaps else None,
            "truth球数": len(goals) if goals else None, "tolerance": tol,
            "增量命中真进球_重复": dup, "增量命中真进球_救回": rescued,
            "增量未命中_噪声": noise,
            "增量明细": detail,
            "增量距truth分布": band,
            "base_t": [round(e.t, 3) for e in base],
        }
        report[name] = rec
        print(f"\n=== {name}  时长{rec['时长s']}s  段{len(blocks)}  筐宽{hw}  点数{pts_n} ===")
        print(f"  A 现状      {rec['A_total']:>5}  high{rec['A_base']['high']:>4}"
              f" review{rec['A_base']['review']:>5} low{rec['A_base']['low']:>5}")
        print(f"  B 仅high占钟 {rec['B_total']:>5}  high{rec['B_high_only']['high']:>4}"
              f" review{rec['B_high_only']['review']:>5} low{rec['B_high_only']['low']:>5}")
        print(f"  C 不冷却    {rec['C_total']:>5}  high{rec['C_no_cooldown']['high']:>4}"
              f" review{rec['C_no_cooldown']['review']:>5} low{rec['C_no_cooldown']['low']:>5}")
        print(f"  增量 {rec['增量']} 条（high {rec['增量_high']} / review {rec['增量_review']}"
              f" / low {rec['增量_low']}）  距基线中位 {rec['与基线最近间隔_中位s']}s"
              f" min {rec['与基线最近间隔_min_s']}s")
        if goals:
            print(f"  truth {len(goals)} 球(tol {tol})：增量命中 重复{dup} / 救回{rescued}"
                  f" / 未命中{noise}")
            print(f"  增量距最近真实进球：{band}")

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "cooldown_probe_result.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print(f"\n明细已写 {out}")


if __name__ == "__main__":
    main(sys.argv[1:])
