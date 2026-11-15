# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

篮球视频自动剪辑工具（fork 自「视频剪辑」仓）。核心变化：**主检球器已从 HSV 换成自训 YOLOv8n**（`bbh/weights/best.pt`，3 类：0=ball, 1=hoop, 2=person；判定只消费 ball/hoop，检人另走 COCO `yolov8n`）。链路：源视频 → 筐口标定 → 筐口 ROI 检球 → 进球判定（篮网运动证据 + 网簇兜底）→ 出手人溯源 → 出片。

- 后端：FastAPI/Starlette（`bbh/web/`），判定与检测核心在 `bbh/`
- 前端：Vue 3 + Element Plus（`frontend/`，四页：calib 标定 / edit 剪辑台 / wall 归人墙 / shared），构建产物落 `bbh/static/`
- 代码不依赖、不引用上游仓任何一行：`bbh/` 内无绝对路径、无跨仓库 import

## 硬性规矩（每次会话必须遵守）

1. **需求池**：`docs/需求池.md` 是"能做但暂不做 / 优先级低 / 条件触发"需求的**唯一入口**（一条一编号 R-xxx）。新需求谈完先进池；已在池的不再重议，用户要做时直接报编号。进池 ≠ 要做。
2. **改代码前必须先出方案、经用户确认后才开发**；只读的诊断/排查/评估不受限，但结论要落地实现时，同样先出方案等确认。
3. **出手人溯源（shooter A2/A3）正由另一 Claude Code 会话推进**：不要重复动 `bbh/shooter.py`、不要起 O1/O2 Spike，除非用户明确要求接管。（现状注记：person 框裁剪已收编入库 `a814f67`；判定层 v19 亦由外部线完成上线 `ac43dea`——各线进度以 `docs/需求池.md` 台账为准。）

## 常用命令

```
run_app.bat                                 # 用户入口：YOLO 检球版（默认）
run_app_hsv.bat                             # HSV 对照版（同一套判定/前端）
powershell -ExecutionPolicy Bypass -File scripts\install.ps1   # 首次安装（venv + 依赖）

.venv\Scripts\python -m pytest tests/ -q    # 单测（conftest 强制 BBH_DETECTOR=hsv，合成视频只有 HSV 检得出）
.venv\Scripts\python scripts\run_tests.py   # 沙箱环境跑测试用这个（自动分批 + 唯一 basetemp，--one 单文件 / --clean 收尾）
.venv\Scripts\python scripts\eval_report.py # 三口径评测报告 → reports/EVAL_<tag>.md
.venv\Scripts\python scripts\run_eval.py    # 跑检测 + 出报告
```

## 架构要点

- **检球器切换**：`bbh/detector_switch.py` 读环境变量 `BBH_DETECTOR`（`yolo` 默认 / `hsv` 对照），`NAME` 并入检出流指纹 `DETECT_VERSION` —— 两种检出流缓存互不串用。
- **主管线只检筐口 ROI**（约占画面 3.6%~6.4%）：`positions.json` 里的球点 `[t,x,y,area,fi]` 是标定参考帧坐标（加回 `shift` 才是画面像素），**不含三分线出手段**——那段是 `shooter.py` 的全帧 HSV 回扫域（独立于主管线）。
- **进球判定**：`goal_decider.py`（篮网运动证据）+ `net_motion.py` / `net_clusters.py`（网簇兜底）；评测口径钉 **LOGIC_VERSION v24**（2026-11-14：面积门 `AREA_KILL_REL=0.50`；v19 起 `HIGH_CONF` 0.60 + 网分 −0.30/−0.26；v23 起 `SIDE_SHORT` 0.17），历史 ≤v23 数字不可混用。
- **Web 层**：`bbh/web/`（app + api_* 路由 + media.py ffmpeg 现切预览 + jobs + single_instance）。入口 `python -m bbh.web` 必须 chdir 到仓库根（工作区 `work/` 按"相对仓库根"落盘）；打包冻结版改用 exe 同级 `data/`。

## 关键事实（踩过坑的）

- **判定层当前 v24 已上线（2026-11-14）**：面积门 `AREA_KILL_REL=0.50`（`goal_decider.py`）；沿革 v19（2026-10-18）定标 `HIGH_CONF=0.60`、网分 −0.30/−0.26；v20 冷却身份判据、v21/v22 筐锚点（逐帧搬点默认关）、v23 `SIDE_SHORT` 0.17。**v25 候选（A 规则 `prev_gap<3s` 降档）实测为交换（误判 −32 / 命中 −12，查准 +2.92pct、覆盖 −1.52pct），2026-11-14 裁定不做、11-15 复述确认，不落地**。进度与数字唯一台账 = `docs/需求池.md`，KPI 详报在 YOLO 训练仓 `yolo/reports/`（不在本仓）。
- **真实运行数据不在本仓**：在上游仓 `D:\claude code\视频剪辑\work\*`（10 场 shooters.json/events/标定）；源视频在 `D:\claude code\1视频获取\归档\`。本仓 `work/` 仅个别样例。
- **受限环境跑 pytest 可能被删除守卫掐断**（SystemExit:1，删除计数超阈值）——不是用例挂了，改用 `scripts/run_tests.py`（详见该脚本顶部注释）。
- `requirements.txt` 全部钉 `==`，且有测试守着（`tests/test_requirements.py`）：不许有 gradio、`bbh/` import 的第三方包漏登记就红；torch 是 cu121 轮子，安装要带 `--extra-index-url`。
- 依赖隔离：统一用本仓 `.venv`（torch 2.5.1+cu121 / ultralytics 8.4.163 / Python 3.12），不要装进全局环境。

## 落档约定

- 结论长文 → `docs/`；可复现脚本与证据图 → `诊断/<日期>-<主题>/`；评测报告 → `reports/`；低优需求 → `docs/需求池.md`。不新建第二份池子。
