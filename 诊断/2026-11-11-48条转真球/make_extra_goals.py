# -*- coding: utf-8 -*-
"""把 v20 冷却放行新增的 48 条 high 假绿 → 转成「补充真球」，写 truth/extra_goals.json。

背景（2026-11-11 用户裁定）
---------------------------
v19 → v20 的冷却身份判据（`_new_ball_since`）新增 75 行，其中 48 条是 high 档
且与 HSV 行锚定 truth 对不上（被判假绿）。用户裁定：**不逐条看视频，直接认定
这 48 条是真进球**，作为补充真值并入标准答案。

为什么不改 truth/*.json
-----------------------
原始 truth 是 HSV 行锚定的产物，历史所有 KPI 都靠它复现。就地改 = 账本断代。
所以单独存一个补丁表，评测时合并；删掉该文件即回原口径。

产物
----
truth/extra_goals.json
  {
    "source": "v20_l1_new_rows",
    "decided_at": "2026-11-11",
    "decided_by": "user",
    "note": "...",
    "tolerance": 0.2,
    "goals": {"2026-08-22_1": [t, ...], ...},
    "per_video": {"2026-08-22_1": 9, ...}
  }

复现
----
.venv\\Scripts\\python 诊断\\2026-11-11-48条转真球\\make_extra_goals.py
"""
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)

import eval_lib as E                                                  # noqa: E402

# v20 新增行只出现在老六场（09-19_1 历来崩场，本就不在评测口径里）
V6 = ["2026-08-22_1", "2026-08-28_1", "2026-08-29_3",
      "2026-09-04_1", "2026-09-11_3", "2026-09-12_1"]

OLD_ROOTS = [r"D:\claude code\视频剪辑\work",
             r"D:\claude code\视频剪辑YOLO\work"]
ARM19 = r"D:\claude code\yolo\eval_out\v19_c03_s1"
ARM20 = r"D:\claude code\yolo\eval_out\v20_c03_s1"
OUT = os.path.join(ROOT, "truth", "extra_goals.json")


def flag_hits(goals, rows, tol=0.2):
    """复现官方裁判的贪心配对 → 每行是否命中真值。"""
    truth = sorted(goals)
    used = set()
    out = []
    for t, _s in rows:
        m = next((i for i, g in enumerate(truth)
                  if i not in used and abs(g - t) <= tol), None)
        if m is not None:
            used.add(m)
            out.append(True)
        else:
            out.append(False)
    return out


def main():
    a19 = E.collect_arm(ARM19, V6, deleted_from=None, extra_roots=OLD_ROOTS)
    a20 = E.collect_arm(ARM20, V6, deleted_from=None, extra_roots=OLD_ROOTS)

    goals_out = {}
    per_video = {}
    for v in V6:
        if not a19.get(v) or not a20.get(v):
            print("跳过 %s（产物缺失）" % v)
            continue
        r20 = a20[v]["rows"]
        f20 = flag_hits(list(a20[v]["goals"]), r20)
        have19 = {round(t, 2) for t, _s in a19[v]["rows"]}
        picked = sorted({round(t, 3) for (t, s), h in zip(r20, f20)
                         if s == "high" and not h and round(t, 2) not in have19})
        if picked:
            goals_out[v] = picked
            per_video[v] = len(picked)

    doc = {
        "source": "v20_l1_new_rows",
        "decided_at": "2026-11-11",
        "decided_by": "user",
        "note": ("v19→v20 冷却身份判据新增的 high 行中，与 HSV 行锚定 truth 对不上"
                 "的 48 条。用户裁定：未逐条看视频，直接认定为真进球，作为补充真值"
                 "并入标准答案。**这批未经人工核验**，若日后核验发现其中有假球，"
                 "删掉本文件即回原口径。"),
        "tolerance": 0.2,
        "goals": goals_out,
        "per_video": per_video,
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
        f.write("\n")

    n = sum(per_video.values())
    print("写入 %s" % OUT)
    print("补充真球合计 %d 条" % n)
    for v in V6:
        print("  %-14s %2d" % (v, per_video.get(v, 0)))
    if n != 48:
        print("!! 警告：预期 48 条，实际 %d 条，请核对" % n)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
