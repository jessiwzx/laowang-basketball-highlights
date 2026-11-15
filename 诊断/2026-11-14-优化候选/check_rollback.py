# -*- coding: utf-8 -*-
r"""回退验证：把短档调回 0.14 重放，结果必须逐条等于 v22 备份。

证明「一行改动可完整回退」，同时反过来钉死「新产物的差异确实全部来自那一档」。

    .venv\Scripts\python "诊断\2026-11-14-优化候选\check_rollback.py"
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import replay_l1 as L1                              # noqa: E402

VIDEOS = ["2026-10-17_1", "2026-10-23_1", "2026-10-24_1",
          "2026-10-30_1", "2026-10-31_1", "2026-11-06_1", "2026-11-07_1"]


def rows(doc):
    return {round(e["t"], 2): (e["status"], e["confidence"])
            for e in doc["events"]}


def main():
    bad = 0
    for v in VIDEOS:
        _srcs, ev = L1.work_of(v)
        bak = os.path.join(os.path.dirname(ev), "events.v22.json")
        if not os.path.exists(bak):
            print(f"  skip {v}: 无 events.v22.json")
            continue
        doc, warn = L1.run_one(v, short=0.14)
        if doc is None:
            print(f"  FAIL {v}: {warn}")
            bad += 1
            continue
        a, b = rows(json.load(open(bak, encoding="utf-8"))), rows(doc[0])
        only_a = sorted(set(a) - set(b))
        only_b = sorted(set(b) - set(a))
        dst = [(t, a[t][0], b[t][0]) for t in set(a) & set(b) if a[t][0] != b[t][0]]
        dcf = [(t, a[t][1], b[t][1]) for t in set(a) & set(b) if a[t][1] != b[t][1]]
        ok = not (only_a or only_b or dst or dcf)
        bad += not ok
        print(f"  [{'ok' if ok else 'DIFF'}] {v:<14} {len(a)}/{len(b)} 行"
              f" · 仅 v22 {len(only_a)} · 仅回退 {len(only_b)}"
              f" · status 差 {len(dst)} · conf 差 {len(dcf)}")
        if not ok:
            print(f"        样例 {dst[:2]} {dcf[:2]} {only_a[:2]}/{only_b[:2]}")
    print(f"\n回退验证：{'全部一致 ✓' if not bad else f'{bad} 场不一致 ✗'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
