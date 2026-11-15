# -*- coding: utf-8 -*-
r"""把 v24（穿筐物投影面积硬门）的重判结果落回生产 work 目录。

安全流程（先验后写，不盲覆盖）
------------------------------
1. L1 在 `_l1/<场>/` 临时目录里从 positions 重算（生产目录零改动）；
2. **先比对**：行数必须相同、conf 必须逐条相同、改档必须恰好是
   `high → review` 且 review_reason 恰好为 `area`，并且这 33 个时刻要和
   `area_feat.json` 里 `a_rel>0.5` 的那批逐条对上；
3. 比对全过才备份 `events.json → events.v23.json` 并覆盖。

任一条不过就中止，生产文件不动。

    .venv\Scripts\python "诊断\2026-11-14-优化候选\rerun_v24.py"
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, HERE)

import bbh.pipeline as P                            # noqa: E402
import evaluate as ev                               # noqa: E402
import replay_l1 as L1                              # noqa: E402

VIDEOS = ["2026-10-17_1", "2026-10-23_1", "2026-10-24_1",
          "2026-10-30_1", "2026-10-31_1", "2026-11-06_1", "2026-11-07_1"]


def tiers(doc):
    n = {}
    for e in doc["events"]:
        n[e["status"]] = n.get(e["status"], 0) + 1
    return n


def expect_area_ts():
    """area_feat.json 里新六场 a_rel>0.5 的那批 (场, 时刻)。"""
    p = os.path.join(HERE, "area_feat.json")
    if not os.path.exists(p):
        return set()
    d = json.load(open(p, encoding="utf-8"))
    out = set()
    for blk in d["data"]:
        if blk["video"] not in VIDEOS:
            continue
        for r in blk["rows"]:
            if r["label"] == "false_green" and r["feat"]["a_rel"] > 0.5:
                out.add((blk["video"], round(r["t"], 2)))
    return out


def main():
    print(f"LOGIC_VERSION = {P.LOGIC_VERSION}")
    exp = expect_area_ts()
    print(f"area_feat 期望改档 {len(exp)} 条\n")

    got_all, rows_delta, conf_bad, bad_change = set(), 0, 0, []
    got_deleted = set()
    for v in VIDEOS:
        _srcs, ev_path = L1.work_of(v)
        if not os.path.exists(ev_path):
            print(f"  skip {v}: 无 events.json")
            continue
        old = json.load(open(ev_path, encoding="utf-8"))
        doc, warn = L1.run_one(v)          # 临时目录重算（当前代码 = v24）
        if doc is None:
            print(f"  FAIL {v}: {warn}")
            return 1
        new = doc[0]
        o = {round(e["t"], 2): e for e in old["events"]}
        n = {round(e["t"], 2): e for e in new["events"]}

        # 已删球（剪辑台删掉的球）不进任何指标：它们的改档也要接受，
        # 但必须单独计数核对，不能混进"评测集内的 33 条"
        dels = ev._load_deleted_seconds(ev_path) + ev.INTERMISSION_GOALS.get(v, [])
        drop = (ev._deleted_exclusions([e["t"] for e in old["events"]], dels)
                if dels else set())
        dropped = {round(old["events"][i]["t"], 2) for i in drop}

        only_o, only_n = set(o) - set(n), set(n) - set(o)
        rows_delta += len(only_o) + len(only_n)
        cbad = sum(1 for t in set(o) & set(n)
                   if abs(o[t]["confidence"] - n[t]["confidence"]) > 1e-9)
        conf_bad += cbad
        chg = [(t, o[t]["status"], n[t]["status"], n[t].get("review_reason"),
                n[t].get("cross_area_rel"))
               for t in sorted(set(o) & set(n)) if o[t]["status"] != n[t]["status"]]
        illegal = [c for c in chg
                   if not (c[1] == "high" and c[2] == "review" and c[3] == "area")]
        bad_change += [(v, c) for c in illegal]
        for t, *_ in chg:
            (got_deleted if t in dropped else got_all).add((v, t))

        a, b = tiers(old), tiers(new)
        flag = "" if (not only_o and not only_n and not cbad and not illegal
                      and len(chg) > 0) else "  ⚠ 见下"
        print(f"  {v:<14} 行 {len(old['events'])}→{len(new['events'])}"
              f"  high {a.get('high', 0)}→{b.get('high', 0)}"
              f"  review {a.get('review', 0)}→{b.get('review', 0)}"
              f"  low {a.get('low', 0)}→{b.get('low', 0)}"
              f"  改档 {len(chg)}  conf差 {cbad}{flag}")

    print()
    checks = [
        ("行数零变化", rows_delta == 0, f"多/少 {rows_delta} 行"),
        ("conf 逐条不变", conf_bad == 0, f"差 {conf_bad} 条"),
        ("改档全为 high→review/area", not bad_change,
         f"{len(bad_change)} 条越界 {bad_change[:3]}"),
        ("评测集内改档与 area_feat 一致", got_all == exp,
         f"重放 {len(got_all)} / 期望 {len(exp)}；"
         f"仅重放 {sorted(got_all - exp)[:3]}；仅期望 {sorted(exp - got_all)[:3]}"),
        ("已删球改档另行计数（不进指标）", True,
         f"{len(got_deleted)} 条 {sorted(got_deleted)[:3]}"),
    ]
    ok = all(c[1] for c in checks)
    for name, passed, note in checks:
        print(f"  {'✓' if passed else '✗'} {name:<26}{note}")
    if not ok:
        print("\n验收未过 → 生产 events.json 一个字节都没动。")
        return 1

    print("\n验收全过 → 落盘")
    for v in VIDEOS:
        _srcs, ev_path = L1.work_of(v)
        if not os.path.exists(ev_path):
            continue
        bak = os.path.join(os.path.dirname(ev_path), "events.v23.json")
        if not os.path.exists(bak):
            shutil.copy2(ev_path, bak)
        shutil.copy2(os.path.join(L1.TMP, v, "events.json"), ev_path)
        print(f"  {v} ✓")
    print("完成。备份在 work/<场>/events.v23.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
