# -*- coding: utf-8 -*-
r"""检球器选择：本项目默认 **YOLO**，可用环境变量切回 HSV 做对照。

    BBH_DETECTOR = yolo（默认） | hsv
        yolo → ``bbh.detector_yolo``（自训权重，随包内置 bbh/weights/best.pt）
        hsv  → ``bbh.detector``（原 HSV 颜色检球，保留作对照与单测）

``pipeline`` 从本模块取 ``detect_positions``，并把 ``NAME`` 并入检出流指纹
（``DETECT_VERSION``），保证两种检出流的缓存互不串用——换了检出器不会误吃
旧缓存。

（单元测试在 ``tests/conftest.py`` 里把 BBH_DETECTOR 默认成 hsv：测试用的
合成视频画的是纯色球，只有 HSV 检得出。）
"""
import os

NAME = (os.environ.get("BBH_DETECTOR") or "yolo").strip().lower()
if NAME not in ("yolo", "hsv"):
    NAME = "yolo"

if NAME == "hsv":
    from .detector import detect_positions          # noqa: F401
else:
    from .detector_yolo import detect_positions     # noqa: F401

__all__ = ["NAME", "detect_positions"]
