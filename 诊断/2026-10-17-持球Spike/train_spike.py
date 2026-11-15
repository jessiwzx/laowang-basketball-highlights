# -*- coding: utf-8 -*-
"""R-018 持球补训 Spike：基于生产 best.pt 微调（imgsz 1280），OOM 自动降 batch。"""
import time

from ultralytics import YOLO

BASE_MODEL = r"D:/claude code/视频剪辑YOLO/bbh/weights/best.pt"
DATA = r"D:/claude code/yolo/dataset_spike.yaml"
PROJECT = r"D:/claude code/yolo/runs"

def train(batch):
    model = YOLO(BASE_MODEL)
    return model.train(
        data=DATA, imgsz=1280, epochs=40, patience=10, batch=batch,
        device=0, project=PROJECT, name="held_spike_v1",
        workers=2, cache=False, exist_ok=True,
    )

if __name__ == "__main__":
    for b in (4, 2):
        try:
            print(f"=== 开始训练 batch={b} imgsz=1280 ===", flush=True)
            t0 = time.time()
            train(b)
            print(f"=== 训练完成，耗时 {(time.time()-t0)/3600:.1f}h ===", flush=True)
            break
        except RuntimeError as ex:
            print(f"batch={b} 失败: {ex}", flush=True)
            if b == 2:
                raise
