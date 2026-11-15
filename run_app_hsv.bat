@echo off
rem HSV 对照版入口：切回原 HSV 颜色检球（与 YOLO 版同一套判定/前端）。
cd /d "%~dp0"
set BBH_DETECTOR=hsv
.venv\Scripts\python -m bbh.web
pause
