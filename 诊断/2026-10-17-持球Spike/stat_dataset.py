# -*- coding: utf-8 -*-
"""数据集分布统计（只读）：类别数、每场帧数、ball 目标像素尺寸分布。"""
import collections
from pathlib import Path

root = Path(r"D:/claude code/yolo/dataset/labels")
for split in ("train", "val", "test"):
    d = root / split
    files = list(d.glob("*.txt"))
    cls_cnt = collections.Counter()
    areas = []
    vids = collections.Counter()
    empty = 0
    for f in files:
        lines = [l for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not lines:
            empty += 1
        for l in lines:
            p = l.split()
            c = int(p[0])
            cls_cnt[c] += 1
            a = float(p[3]) * float(p[4]) * 1024 * 1024
            if c == 0:
                areas.append(a)
        vids[f.name.split("__f")[0]] += 1
    print(f"{split}: frames={len(files)} empty={empty} cls={dict(cls_cnt)}")
    print(f"  videos={dict(vids)}")
    if areas:
        areas.sort()
        n = len(areas)
        q = lambda x: areas[min(n - 1, int(n * x))]
        print(f"  ball 框 {n} 个 | 面积 p10={q(.1):.0f} p50={q(.5):.0f} "
              f"p90={q(.9):.0f} px² | 直径中位≈{(q(.5) * 4 / 3.1416) ** 0.5:.0f}px "
              f"| ≤16px² 占 {100 * sum(1 for a in areas if a <= 16) / n:.1f}%")
