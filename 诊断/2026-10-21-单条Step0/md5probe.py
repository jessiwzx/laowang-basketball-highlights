"""集锦成品 md5 对拍（2026-10-21 单条改造的回归门）。

同一输入、同一参数，**改动前后必须逐字节一致**——不一致就说明无意动了
渲染路径，按方案约定"回滚重来"，不接受"编码器抖动"的解释。

隔离措施：把 2026-10-17_1 的元数据（events/calibration*/shooters）复制到
work/_md5probe，并把 `_work_dir_for` 指过去 ⇒ 成品写在探针目录里，**绝不
覆盖用户真实的 highlight-*.mp4**。
"""
import hashlib
import os
import shutil
import sys

ROOT = "D:/claude code/视频剪辑YOLO"
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import bbh.app as A                                  # noqa: E402
from bbh.models import load_events                   # noqa: E402

SRC = "work/2026-10-17_1"
MDW = os.path.abspath("work/_md5probe")
VIDEO = load_events(os.path.join(SRC, "events.json"))["video"]

os.makedirs(MDW, exist_ok=True)
for n in os.listdir(SRC):
    if n in ("events.json", "shooters.json") or n.startswith("calibration"):
        shutil.copy2(os.path.join(SRC, n), os.path.join(MDW, n))

A._work_dir_for = lambda vp: MDW                    # 隔离：成品不进真实目录

TS = [16.43, 26.40, 29.70]                          # 与 Step 0 同一批球
ROWS = [[True, A._fmt_t(t), "high", 0.8, 5.0, 2.0, "老张"] for t in TS]

for vertical in (True, False):
    out = A.generate_highlight(VIDEO, ROWS, "", vertical=vertical)
    h = hashlib.md5(open(out, "rb").read()).hexdigest()
    tag = "竖屏集锦" if vertical else "横屏集锦"
    print(f"{tag}\t{h}\t{os.path.getsize(out)}\t{os.path.basename(out)}")
