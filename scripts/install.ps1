# 首次安装：建 venv（Python 3.12）并装依赖（含 CUDA 12.1 轮子的 torch）。
# 用法：右键本文件 ->「使用 PowerShell 运行」，或在仓库根执行
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "== 建 venv（需要已安装 Python 3.12；用 py -3.12）=="
    py -3.12 -m venv .venv
}

Write-Host "== 升级 pip =="
.\.venv\Scripts\python -m pip install --upgrade pip

Write-Host "== 装依赖（torch 走 cu121 轮子源，约 2.5GB，首次较慢）=="
.\.venv\Scripts\python -m pip install -r requirements.txt `
    --extra-index-url https://download.pytorch.org/whl/cu121

Write-Host "== 自检：导入 + 模型可加载 =="
.\.venv\Scripts\python -c "import torch, ultralytics; print('torch', torch.__version__, 'cuda', torch.cuda.is_available()); print('ultralytics', ultralytics.__version__)"

Write-Host "`n完成。双击 run_app.bat 启动（YOLO 版）；run_app_hsv.bat 切 HSV 对照。"
