"""Step 0：单球竖屏渲染耗时实测（只读源视频，写 work/ 下临时成品）。

不依赖 app/接口层，直接调 vrender.build_vertical —— 与单条模式将要走的
调用方式逐字一致（一次传 1 个 goal）。跑完删掉临时成品，只留数字。
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)) + ""))
ROOT = "D:/claude code/视频剪辑YOLO"
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from bbh import vrender                      # noqa: E402
from bbh.models import load_events           # noqa: E402

WORK = "work/2026-10-17_1"
data = load_events(os.path.join(WORK, "events.json"))
video = data["video"]
high = [e for e in data["events"] if e.status == "high"][:3]
print(f"源：{video}\n时长 {data['duration']:.0f}s，high 球 {len(high)} 个")

rows = []
for e in high:
    t = float(e.t)
    name = f"tmpstep0-{int(t)}.mp4"
    t0 = time.time()
    out = vrender.build_vertical(video, [(t, 5.0, 2.0)], WORK, name)
    dt = time.time() - t0
    size = os.path.getsize(out) / 1024 / 1024
    rows.append(dt)
    print(f"  t={t:8.2f}s  {dt:6.2f}s  {size:5.1f}MB  {os.path.basename(out)}")

avg = sum(rows) / len(rows)
print(f"\n单球均值 {avg:.2f}s（{', '.join(f'{r:.2f}' for r in rows)}）")
for n in (10, 20, 30, 40):
    print(f"  N={n:2d} → 预计 {avg * n / 60:.1f} 分钟")

for e in high:                       # 清理临时成品（不碰 highlight-*）
    p = os.path.join(WORK, f"tmpstep0-{int(float(e.t))}.mp4")
    if os.path.exists(p):
        os.remove(p)
print("临时成品已清理")
