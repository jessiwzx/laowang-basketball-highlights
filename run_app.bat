@echo off
rem YOLO 检球版入口：默认走自训 YOLO 权重（bbh/weights/best.pt）。
cd /d "%~dp0"
set BBH_DETECTOR=yolo
.venv\Scripts\python -m bbh.web
pause
