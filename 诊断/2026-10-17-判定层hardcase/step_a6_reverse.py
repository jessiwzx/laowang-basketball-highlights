# -*- coding: utf-8 -*-
"""A6 · 反向监测三场：v19 常量下的离线复算（零视频 IO、零重扫风险）。

复算口径直接复用 Step 1 的 `status_net`（存储 conf − 存储网分 + 新网分，
再按 (HIGH_CONF / 硬门) 分档），保证与 Step 1 预演同源。

交叉验证：用 v18 常量走同一复算器，与存储 status 逐条比对——一致才采信。
"""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import step1_attrib as S1                                            # noqa: E402

HC_V18, HC_V19 = 0.65, 0.60
SG_V18, SS_V18 = -0.10, -0.20
SG_V19, SS_V19 = -0.26, -0.30
FIELDS = ["2026-10-10_1", "2026-09-24_1", "2026-10-16_1"]


def main():
    rows = []
    for v in FIELDS:
        d = S1.load_field(v)
        if d is None:
            print(f"  {v}: 缺事件或真值，跳过")
            continue
        n = nb = nn = 0
        cross_n = cross_ok = 0
        for e in d["events"]:
            if not (e["kept"] and e["label"] == "goal"):
                continue
            n += 1
            s18 = e["status"]
            s18r = S1.status_net(e, HC_V18, SG_V18, SS_V18)
            cross_n += 1
            cross_ok += (s18 == s18r)
            if s18 != "high":
                nb += 1
            if S1.status_net(e, HC_V19, SG_V19, SS_V19) != "high":
                nn += 1
        rel = (nn - nb) / nb if nb else 0.0
        rows.append({"field": v, "goal_rows": n, "non_auto_v18": nb,
                     "non_auto_v19": nn, "delta": nn - nb, "rel": rel,
                     "recompute_selfcheck": f"{cross_ok}/{cross_n}",
                     "src": d["src"], "logic_ver": d["logic_ver"]})
        print(f"  {v}: 进球行 {n}  非自动档 v18={nb} → v19={nn} "
              f"({nn - nb:+d}, {100 * rel:+.1f}%)  复算自洽 {cross_ok}/{cross_n}"
              f"  logic={d['logic_ver']}")

    out = os.path.join(HERE, "step_a6_reverse.json")
    with io.open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(f"\n记录：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
