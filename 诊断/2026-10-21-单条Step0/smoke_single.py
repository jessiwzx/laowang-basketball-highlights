"""单条模式真链路冒烟：真渲染 2 球，验证"一球一个文件 + 命名正确"。

同样隔离：把元数据复制到 `work/_single_smoke` 并把 `_work_dir_for` 指过去，
**不往真实的 work/2026-10-17_1 里写 single-* 成品**。
"""
import os
import shutil
import sys
import time

ROOT = "D:/claude code/视频剪辑YOLO"
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import bbh.app as A                                  # noqa: E402
from bbh.models import load_events                   # noqa: E402

SRC = "work/2026-10-17_1"
MDW = os.path.abspath("work/_single_smoke")
VIDEO = load_events(os.path.join(SRC, "events.json"))["video"]

shutil.rmtree(MDW, ignore_errors=True)
os.makedirs(MDW, exist_ok=True)
for n in os.listdir(SRC):
    if n in ("events.json", "shooters.json") or n.startswith("calibration"):
        shutil.copy2(os.path.join(SRC, n), os.path.join(MDW, n))

A._work_dir_for = lambda vp: MDW

TS = [16.43, 26.40]
ROWS = [[True, A._fmt_t(t), "high", 0.8, 5.0, 2.0, "老张"] for t in TS]
ROWS[1][6] = ""                                     # 第二个球不打标签 → 未标

t0 = time.time()
gen = A.generate_singles(VIDEO, ROWS, [A.GEN_ALL_LABEL_SINGLE], vertical=True)
while True:
    try:
        print("  ", next(gen))
    except StopIteration as st:
        outs, skipped = st.value or ([], [])
        break
print(f"\n耗时 {time.time() - t0:.1f}s，产出 {len(outs)} 条，跳过 {len(skipped)} 条")
for item, p in outs:
    print(f"  {item}  →  {os.path.basename(p)}  {os.path.getsize(p) / 1024 / 1024:.1f}MB")
if skipped:
    print("跳过：", skipped)
