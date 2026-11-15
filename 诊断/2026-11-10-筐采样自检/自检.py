# -*- coding: utf-8 -*-
r"""筐采样自检 + 11-07_1 差场归因（只读）。

两件事
------
1) **标定质量自检**：`positions.json` 里的 `hoop` 是 YOLO 每 20s 采到的筐框
   （画面坐标）。减去 ORB 位移后应与用户标定的筐心重合；系统性偏离 = 标定
   筐位画偏了，而 cross_off 的零点是标定筐心 → 整场几何门都歪。v21 已证明
   「逐帧搬点」净亏，所以这里**只告警、不搬点**。

2) **11-07_1 为什么差**：自动档覆盖只有 61.8%（105 条真球落在 review）。
   按 review_reason / low_reason / cross_off / 网分拆开，看是标定问题、
   网信号问题还是判定门问题。

数据限制：`hoop` 采样只有 v22 之后跑的场次才有（本仓 11-06 / 11-07），其余
场次 `hoop=0`，无法做自检——这是采样覆盖率问题，不是脚本问题。
"""
import json
import os
import sys
from statistics import median

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
OUT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

from bbh.motion import interp_shift                              # noqa: E402
import evaluate as EV                                            # noqa: E402

WORK = os.path.join(ROOT, "work")
PAIR = ["2026-11-06_1", "2026-11-07_1"]      # 仅这两场有 hoop 采样


def rd(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def q(vals, p):
    if not vals:
        return float("nan")
    s = sorted(vals)
    return s[min(len(s) - 1, max(0, int(p * (len(s) - 1))))]


def hoop_bias(video):
    ps = rd(os.path.join(WORK, video, "positions.json"))
    hoop = ps.get("hoop") or []
    shift = ps.get("shift") or []
    calib = ps.get("calib")
    if not hoop or not calib:
        return None
    hx, hy, hw, hh = calib[0][:4]
    cx, cy = hx + hw / 2.0, hy + hh / 2.0
    dxs, dys, sizes = [], [], []
    for s in hoop:
        try:
            t, x, y, w, h = (float(s[i]) for i in range(5))
        except (TypeError, ValueError, IndexError):
            continue
        sx, sy = interp_shift(shift, t)
        dxs.append((x - sx - cx) / hw)
        dys.append((y - sy - cy) / hh)
        if hw > 0:
            sizes.append(w / hw)
    if not dxs:
        return None
    return {"n": len(dxs), "rect": [hx, hy, hw, hh],
            "dx_med": median(dxs), "dx_iqr": q(dxs, .75) - q(dxs, .25),
            "dx_abs_p90": q([abs(v) for v in dxs], .9),
            "dy_med": median(dys), "dy_iqr": q(dys, .75) - q(dys, .25),
            "dy_abs_p90": q([abs(v) for v in dys], .9),
            "w_ratio": median(sizes) if sizes else float("nan")}


def tier_profile(video):
    doc = rd(os.path.join(WORK, video, "events.json"))
    ev = doc["events"]
    deleted = EV._load_deleted_seconds(os.path.join(WORK, video, "events.json"))
    drop = EV._deleted_exclusions([e["t"] for e in ev], deleted)
    ev = [e for i, e in enumerate(ev) if i not in drop]
    out = {"logic": doc.get("logic_ver"), "n": len(ev),
           "high": sum(1 for e in ev if e["status"] == "high"),
           "review": sum(1 for e in ev if e["status"] == "review"),
           "low": sum(1 for e in ev if e["status"] == "low"),
           "rr": {}, "lr": {}, "off_review": 0, "net_neg": 0}
    for e in ev:
        if e["status"] == "review":
            k = e.get("review_reason") or "conf<0.6"
            out["rr"][k] = out["rr"].get(k, 0) + 1
            if (e.get("cross_off") is not None
                    and abs(e["cross_off"]) > 0.30):
                out["off_review"] += 1
        if e["status"] == "low":
            k = (e.get("low_reason") or "?")[:14]
            out["lr"][k] = out["lr"].get(k, 0) + 1
        if (e.get("net_post") is not None and e["net_post"] < 0
                and e["status"] != "high"):
            out["net_neg"] += 1
    offs = [abs(e["cross_off"]) for e in ev if e.get("cross_off") is not None]
    out["off_p50"] = q(offs, .5)
    out["off_p90"] = q(offs, .9)
    return out


def truth_hit_split(video):
    """真球落在哪一档（只对 selection 派生真值，可算）。"""
    p = os.path.join(ROOT, "truth", video + ".json")
    if not os.path.exists(p):
        return None
    tr = rd(p)
    if tr.get("truth_kind") != "selection":
        return None
    goals = sorted(tr["goals"])
    ev = rd(os.path.join(WORK, video, "events.json"))["events"]
    deleted = EV._load_deleted_seconds(os.path.join(WORK, video, "events.json"))
    drop = EV._deleted_exclusions([e["t"] for e in ev], deleted)
    ev = [e for i, e in enumerate(ev) if i not in drop]
    tol = tr.get("tolerance", 0.2)
    used = set()
    tiers = {"high": 0, "review": 0, "low": 0, "missing": 0}
    offs = []
    for g in goals:
        i = next((k for k, e in enumerate(ev)
                  if k not in used and abs(e["t"] - g) <= tol), None)
        if i is None:
            tiers["missing"] += 1
            continue
        used.add(i)
        tiers[ev[i]["status"]] += 1
        if ev[i].get("cross_off") is not None:
            offs.append(abs(ev[i]["cross_off"]))
    return {"goals": len(goals), "tiers": tiers,
            "off_p50": q(offs, .5), "off_p90": q(offs, .9)}


def main():
    L = []
    A = L.append
    A("# 筐采样自检 + 11-07_1 差场归因（只读）")
    A("")
    A("## 1 标定质量自检（筐框中位数 vs 标定筐心）")
    A("")
    A("| 场 | 采样 | 标定筐 | Δx 中位 | Δx IQR | Δx\\|p90\\| | "
      "Δy 中位 | Δy IQR | Δy\\|p90\\| | 框宽/标定宽 | 判定 |")
    A("|---|---|---|---|---|---|---|---|---|---|---|")
    for v in PAIR:
        b = hoop_bias(v)
        if not b:
            A(f"| {v} | 0 | — | — | — | — | — | — | — | — | 无采样 |")
            continue
        worst = max(abs(b["dx_med"]), abs(b["dy_med"]))
        verdict = "✅ 正常" if worst <= 0.25 else "⚠ 建议重标"
        A("| %s | %d | %s | %+.3f | %.3f | %.3f | %+.3f | %.3f | %.3f | %.2f | %s |"
          % (v, b["n"], b["rect"], b["dx_med"], b["dx_iqr"], b["dx_abs_p90"],
             b["dy_med"], b["dy_iqr"], b["dy_abs_p90"], b["w_ratio"], verdict))
    A("")
    A("> 判据 0.25 筐宽：判定门的 review 线是 0.30、kill 线 0.45，标定若偏一半"
      "就把余量吃光了。**只告警不搬点**——v21 已实测逐帧搬点净亏。")
    A("")

    A("## 2 档位与复核原因（11-06 vs 11-07）")
    A("")
    A("| 场 | logic | 行 | high | review | low | 复核：geom | 复核：entity | "
      "复核：pop_back | 复核：fallback | 复核：conf<0.6 | low：几何 | low：置信不足 |")
    A("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    profs = {}
    for v in PAIR:
        p = tier_profile(v)
        profs[v] = p
        rr = p["rr"]
        lr = p["lr"]
        A("| %s | v%s | %d | %d | %d | %d | %d | %d | %d | %d | %d | %d | %d |"
          % (v, p["logic"], p["n"], p["high"], p["review"], p["low"],
             rr.get("geom", 0), rr.get("entity", 0), rr.get("pop_back", 0),
             rr.get("fallback", 0), rr.get("conf<0.6", 0),
             sum(c for k, c in lr.items() if "筐口两端" in k or "几何" in k),
             sum(c for k, c in lr.items() if "置信" in k)))
    A("")
    A("| 场 | \\|cross_off\\| p50 | p90 | 复核行中 \\|off\\|>0.30 的比例 |")
    A("|---|---|---|---|")
    for v in PAIR:
        p = profs[v]
        r = (p["off_review"] / p["review"] * 100) if p["review"] else 0
        A("| %s | %.3f | %.3f | %.1f%%（%d/%d）|"
          % (v, p["off_p50"], p["off_p90"], r, p["off_review"], p["review"]))
    A("")

    A("## 3 真球落在哪一档（selection 派生真值，只读覆盖）")
    A("")
    A("| 场 | 真值 | high | review | low | missing | \\|off\\| p50 | p90 |")
    A("|---|---|---|---|---|---|---|---|")
    for v in PAIR:
        t = truth_hit_split(v)
        if not t:
            continue
        A("| %s | %d | %d | %d | %d | %d | %.3f | %.3f |"
          % (v, t["goals"], t["tiers"]["high"], t["tiers"]["review"],
             t["tiers"]["low"], t["tiers"]["missing"], t["off_p50"], t["off_p90"]))
    A("")

    md = "\n".join(L)
    with open(os.path.join(OUT, "结论.md"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write(md)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
