# -*- coding: utf-8 -*-
r"""把 v23（短档 0.17）重判结果落回生产 work 目录。

为什么不在 work 目录原地跑 run_detection：复用时它会写回 positions.json，
我们只想换 events.json，检出流一个字节都不该动。所以走临时目录重算，
再把 events.json 拷回。

顺序：先备份 events.json → events.v22.json（已存在且内容不同则中止），
再覆盖，最后打印行数与档位分布对照。

    .venv\Scripts\python "诊断\2026-11-14-优化候选\rerun_events.py"
"""
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bbh.pipeline as P                            # noqa: E402
from bbh import calibration as CAL                  # noqa: E402
import replay_l1 as L1                              # noqa: E402

VIDEOS = ["2026-10-17_1", "2026-10-23_1", "2026-10-24_1",
          "2026-10-30_1", "2026-10-31_1", "2026-11-06_1", "2026-11-07_1"]


def tiers(doc):
    n = {}
    for e in doc["events"]:
        n[e["status"]] = n.get(e["status"], 0) + 1
    return n


def main():
    print(f"LOGIC_VERSION = {P.LOGIC_VERSION}")
    for v in VIDEOS:
        srcs, ev_path = L1.work_of(v)
        if not os.path.exists(ev_path):
            print(f"  skip {v}: 无 events.json")
            continue
        bak = os.path.join(os.path.dirname(ev_path), "events.v22.json")
        old = json.load(open(ev_path, encoding="utf-8"))
        if os.path.exists(bak):
            if json.load(open(bak, encoding="utf-8")) != old:
                print(f"  ABORT {v}: events.v22.json 已存在且与当前不同，"
                      f"先人工确认（{bak}）")
                return 1
        else:
            shutil.copy2(ev_path, bak)

        doc, warn = L1.run_one(v)      # 临时目录重算（当前代码 = 0.17）
        if doc is None:
            print(f"  FAIL {v}: {warn}")
            return 1
        new = doc[0]
        tmp_path = os.path.join(L1.TMP, v, "events.json")
        shutil.copy2(tmp_path, ev_path)

        a, b = tiers(old), tiers(new)
        same_rows = len(old["events"]) == len(new["events"])
        print(f"  {v:<14} 行 {len(old['events'])}→{len(new['events'])}"
              f"{'' if same_rows else '  ⚠ 行数变化'}"
              f"  high {a.get('high', 0)}→{b.get('high', 0)}"
              f"  review {a.get('review', 0)}→{b.get('review', 0)}"
              f"  low {a.get('low', 0)}→{b.get('low', 0)}"
              f"  logic_ver {new.get('logic_ver')}")
    print("\n完成。备份在 work/<场>/events.v22.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
