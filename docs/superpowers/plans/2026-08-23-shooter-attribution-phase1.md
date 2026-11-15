# 出手人识别 Phase 1（出手溯源 + 射手缩略图）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 每个进球自动回扫进球前几秒的全帧画面，回追球轨迹找到出手点，裁出射手缩略图显示在剪辑台行面板——看图打标签不用回看视频。

**Architecture:** 新模块 `bbh/shooter.py`（独立于现有检测管线），产物进 `work/<视频>/shooters.json` + `shooters/` 缩略图目录，身份键 = 视频 + 标定指纹 + `SHOOTER_VERSION`（独立版本号）。UI 在左栏加「🧍 识别出手人」按钮（生成器进度），行面板加射手缩略图块。Phase 2（外观聚类/分组命名）不在本计划——Phase 1 验收通过后另立计划。

**Tech Stack:** Python + OpenCV（复用现有 HSV 检测函数）、Gradio 5.50（现有 UI 框架）、pytest。

**Spec:** `docs/superpowers/specs/2026-08-23-shooter-attribution-design.md`（本计划实现其 Phase 1 部分；术语与验收标准以 spec 为准）

## Global Constraints

- **绝不修改 `DETECT_VERSION` / `LOGIC_VERSION`**（`bbh/pipeline.py`）——出手人缓存完全独立，溯源调参只 bump `bbh/shooter.py` 的 `SHOOTER_VERSION`，不允许触发全片重检或作废测试集的 positions.json 缓存
- **帧时间唯一来源 = 容器 PTS（`cv2.CAP_PROP_POS_MSEC`）**——VFR 视频的 帧号/fps 钟偏最多 3s（detector v4 事故），任何新计时不得用 帧号/fps 主钟
- **Windows 文件名禁冒号**——行键 `01:23.7` 进文件名前必须 `_key_safe`（`:` → `-`）
- **Phase 1 不自动写标签、不做聚类**——缩略图只展示；打标签仍走现有标签下拉
- 现有 228 个测试零回归；新代码全部 TDD（先写失败测试再实现）
- 代码注释/docstring 用中文，风格与现库一致（模块 docstring 首行中文概述）；JSON 落盘一律原子替换（`.tmp` + `os.replace`）
- 运行环境：`.venv\Scripts\python`；控制台中文输出需 `PYTHONIOENCODING=utf-8`
- 单球回扫成本目标 ~3s（隔 2 帧采样 ≈15fps，窗口 6s ≈90 帧全帧 HSV）
- 阈值常量（`MIN_TRACK_N` 等）是初值，**Phase 1 验收轮在 225 条标注上标定后才算定案**（spec 铁律）——实现时不许拍脑袋改判定行为来"让测试过"

## 文件结构

| 文件 | 职责 |
|---|---|
| Create `bbh/shooter.py` | Phase 1 全部算法：缓存读写（ shooters.json）、回扫检测、轨迹回追、射手裁剪、逐球编排生成器、`SHOOTER_VERSION` |
| Modify `bbh/models.py` | 加 `time_key(t)`（行键格式函数，shooter 与 app 共用口径） |
| Modify `bbh/app.py` | 「🧍 识别出手人」按钮 + `run_shooter_tracing` 生成器 + `_shooter_block_html` 行面板块 + 3 处 `_cur_panel_html` 调用点传 `video_path` |
| Create `tools/check_shooters.py` | 验收工具：truth 出手人标注 ↔ 缩略图对照表（HTML）+ 自动指标 |
| Create `tests/test_shooter.py` | 本计划全部新测试 |
| Modify `README.md` | 使用说明、工作原理、数据目录、模块清单各补一段 |

任务间接口约定（后文各任务 **Interfaces** 详列）：`TraceResult` 数据类贯穿 T2→T5；`scan_release_window` 返回 `(points, frame_size)`；`trace_video` 是"yield 进度文案、return 汇总文案"的生成器。

---

### Task 1: shooter.py 缓存层（SHOOTER_VERSION + shooters.json 读写 + 行键工具）

**Files:**
- Modify: `bbh/models.py`（文件末尾追加 `time_key`）
- Create: `bbh/shooter.py`
- Test: `tests/test_shooter.py`

**Interfaces:**
- Consumes: `bbh.pipeline._calib_key(calib) -> list`（既有私有函数，包内跨模块引用是本库惯例——app 就引了 `goal_decider._pos_score`）；`bbh.models.Calibration`
- Produces（后续任务依赖，签名精确到此）:
  - `SHOOTER_VERSION: int`（初始 1）
  - `SHOOTERS_FILE = "shooters.json"`、`THUMB_DIR = "shooters"`
  - `@dataclass TraceResult: status: str; release_t: float|None = None; release_xy: tuple|None = None; track_n: int = 0; track_span: float = 0.0`
  - `save_shooters(work_dir: str, video: str, calib: Calibration, traces: dict) -> None`
  - `load_shooters(work_dir: str) -> dict | None`
  - `shooters_valid(data: dict | None, video: str, calib: Calibration) -> bool`
  - `_key_safe(key: str) -> str`
  - `bbh.models.time_key(t: float) -> str`（与 `app._fmt_t` 同格式 `"01:23.7"`）

- [ ] **Step 1: 写失败测试**

创建 `tests/test_shooter.py`：

```python
"""出手人识别 Phase 1 测试：缓存层 / 轨迹回追 / 全帧回扫 / 射手裁剪 / 编排。"""
import json
import subprocess
import sys
import os

import cv2
import numpy as np
import pytest

import bbh.app as app_module
from bbh import shooter
from bbh.models import BallPos, Calibration, time_key


def test_time_key与_key_safe():
    assert time_key(83.7) == "01:23.7"          # 与 app._fmt_t 同口径
    assert shooter._key_safe("01:23.7") == "01-23.7"   # 冒号 Windows 文件名非法


def test_save_load_roundtrip(tmp_path, sample_video, sample_calibration):
    traces = {"00:02.5": {"status": "high", "release_t": 2.3,
                          "release_xy": [340, 200], "track_n": 9,
                          "track_span": 1.2, "thumbs": ["shooters/a.jpg"]}}
    shooter.save_shooters(str(tmp_path), str(sample_video), sample_calibration,
                          traces)
    assert shooter.load_shooters(str(tmp_path))["traces"] == traces


def test_load_shooters缺失与损坏返回None(tmp_path):
    assert shooter.load_shooters(str(tmp_path)) is None
    (tmp_path / "shooters.json").write_text("{半截", encoding="utf-8")
    assert shooter.load_shooters(str(tmp_path)) is None


def test_shooters_valid身份校验(tmp_path, sample_video, sample_calibration,
                               monkeypatch):
    work = str(tmp_path)
    shooter.save_shooters(work, "v.mp4", sample_calibration, {})
    data = shooter.load_shooters(work)
    assert shooter.shooters_valid(data, "v.mp4", sample_calibration)
    assert not shooter.shooters_valid(data, "别的.mp4", sample_calibration)
    other = Calibration(hoop_rect=(1, 1, 2, 2), hsv_low=(0, 0, 0),
                        hsv_high=(1, 1, 1), frame_size=(3, 3))
    assert not shooter.shooters_valid(data, "v.mp4", other)
    monkeypatch.setattr(shooter, "SHOOTER_VERSION", 99)   # 版本不符作废
    assert not shooter.shooters_valid(data, "v.mp4", sample_calibration)
```

注意：`sample_video` / `sample_calibration` 是 `tests/conftest.py` 既有 fixture（640×360 合成视频、筐口 (300,140,80,40)），直接用。

- [ ] **Step 2: 运行确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_shooter.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'bbh.shooter'` 或 import 错误）

- [ ] **Step 3: 最小实现**

`bbh/models.py` 末尾追加：

```python
def time_key(t: float) -> str:
    """秒 → m+:ss.s 行键（与 app._fmt_t 同格式——标签/勾选/出手人缓存共用
    口径）。放 models 供 app 之外的模块用，避免 shooter 反向依赖 app。"""
    return f"{int(t // 60):02d}:{t % 60:04.1f}"
```

创建 `bbh/shooter.py`：

```python
"""出手人识别 Phase 1：出手溯源（进球前回扫全帧追出手点）+ 射手缩略图。

独立于现有检测管线——只按需回扫每个进球前几秒（每球 ~3s），产物进
shooters.json（身份键 = 视频 + 标定指纹 + SHOOTER_VERSION，与
DETECT_VERSION/LOGIC_VERSION 完全无关，溯源调参绝不触发全片重检）。
坐标约定：本模块全程用**视频原始帧坐标**（不做位移校正折算）——缩略图
从同一帧上裁剪，同帧坐标系内自洽；出手点只服务于裁剪与展示，不进判定器。
帧时间取容器 PTS（CAP_PROP_POS_MSEC——detector v4 教训：VFR 视频的
帧号/fps 钟比 PTS 早最多 3s）。"""
import json
import os
from dataclasses import dataclass

from .models import Calibration
from .pipeline import _calib_key

SHOOTER_VERSION = 1          # 溯源算法行为变化时 +1（独立版本号，不碰检测/判定）
SHOOTERS_FILE = "shooters.json"
THUMB_DIR = "shooters"
# ---- 阈值初值：Phase 1 验收轮在 225 条标注上标定后才算定案（spec 铁律）----
SCAN_WINDOW_S = 6.0          # 回扫窗口：进球前 6 秒（三分高弧线 ~2s+抢板余量）
SCAN_STEP = 2                # 隔 2 帧取 1 帧（15fps 足够连轨迹，每球 ~90 帧）
GAP_BRIDGE_S = 0.3           # 轨迹断点桥接上限（spec：允许 0.3s 短缺口桥接）
V_MAX_RATIO = 30.0           # 相邻采样点速度上限（筐宽/秒）≈13m/s——滤瞬移噪声
HOLD_SPEED_RATIO = 3.0       # 持球段速度上限（筐宽/秒）≈1.4m/s——低于它=还在手里
MIN_TRACK_N = 5              # 🟢 高置信最少轨迹点数
MIN_TRACK_SPAN_S = 0.5       # 🟢 高置信最小轨迹时长（秒）
PERSON_H_CM = 180.0          # 射手身高——像素换算标尺（筐直径 45cm）
CROP_W_CM = 120.0            # 裁剪宽度（肩宽+展开的手臂）
CROP_MARGIN = 1.5            # 透视余量：射手不在筐平面上，按筐宽换算会偏差


@dataclass
class TraceResult:
    """一个进球的溯源结果。status 三档（沿用事件档位词）：high=轨迹完整
    且找到持球→离手速度跃变；review=有轨迹但短/断/追到窗口边界（出手点
    位置可能偏，打标前多瞟预览）；low=未溯源（无可用轨迹，不出缩略图）。"""
    status: str
    release_t: float | None = None
    release_xy: tuple | None = None
    track_n: int = 0
    track_span: float = 0.0


def _key_safe(key: str) -> str:
    """行键 → 文件名安全（'01:23.7' 的冒号在 Windows 文件名非法）。"""
    return key.replace(":", "-")


def save_shooters(work_dir: str, video: str, calib: Calibration,
                  traces: dict) -> None:
    """shooters.json 落盘（原子替换；编排层逐球调用——中断后已完成的球不丢）。"""
    os.makedirs(work_dir, exist_ok=True)
    p = os.path.join(work_dir, SHOOTERS_FILE)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"video": video, "calib": _calib_key(calib),
                   "shooter_ver": SHOOTER_VERSION, "traces": traces},
                  f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def load_shooters(work_dir: str) -> dict | None:
    """读 shooters.json；缺失/任何损坏（半截 JSON/非 UTF-8/非 dict）返回
    None 不抛（positions.json 同款容错）。"""
    try:
        with open(os.path.join(work_dir, SHOOTERS_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def shooters_valid(data: dict | None, video: str, calib: Calibration) -> bool:
    """缓存身份校验：同视频 + 同标定指纹 + 同溯源版本（复用 positions.json
    的身份模式；重标定/升版本 → 整档作废重扫）。"""
    return bool(data) and data.get("video") == video \
        and data.get("calib") == _calib_key(calib) \
        and data.get("shooter_ver") == SHOOTER_VERSION
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv\Scripts\python -m pytest tests/test_shooter.py -v`
Expected: PASS（4 个测试）

- [ ] **Step 5: 提交**

```bash
git add bbh/shooter.py bbh/models.py tests/test_shooter.py
git commit -m "feat: 出手人缓存层——shooters.json 独立身份键（视频+标定指纹+SHOOTER_VERSION），time_key 行键进 models"
```

---

### Task 2: 轨迹回追 trace_release（纯函数，合成数据 TDD）

**Files:**
- Modify: `bbh/shooter.py`（追加 `trace_release`）
- Test: `tests/test_shooter.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `TraceResult`、`GAP_BRIDGE_S`/`V_MAX_RATIO`/`HOLD_SPEED_RATIO`/`MIN_TRACK_N`/`MIN_TRACK_SPAN_S` 常量；`bbh.models.BallPos`
- Produces: `trace_release(points: list[BallPos], t_goal: float, hoop_w: float) -> TraceResult`（Task 5 编排调用）

- [ ] **Step 1: 写失败测试（追加到 tests/test_shooter.py）**

```python
# ---------- 轨迹回追（合成检出流，纯函数） ----------
HOOP_W = 80.0                # 与 conftest 筐口宽一致，速度以筐宽为单位


def _pt(t, x, y):
    return BallPos(t=t, x=x, y=y, area=100, fi=0)


def _pts_hold_then_flight():
    """持球慢移段（0.5~0.9s，≤1 筐宽/s）→ 飞行段（1.0~2.9s，~5 筐宽/s）。"""
    pts = [(_pt(0.5 + 0.1 * j, 340 + 6 * j, 300)) for j in range(5)]
    pts += [(_pt(1.0 + 0.1 * j, 370 + 40 * j, 300 - 10 * j))
            for j in range(20)]
    return pts


def test_回追到跃变点判high且出手在跃变处():
    tr = shooter.trace_release(_pts_hold_then_flight(), 3.0, HOOP_W)
    assert tr.status == "high"
    assert abs(tr.release_t - 1.0) < 1e-6      # 出手=第一段快速度的起点
    assert tr.track_n == 25
    assert tr.track_span == pytest.approx(2.4, abs=0.01)


def test_全程飞行未追到持球段判review():
    pts = [_pt(1.0 + 0.1 * j, 370 + 40 * j, 300) for j in range(20)]
    tr = shooter.trace_release(pts, 3.0, HOOP_W)
    assert tr.status == "review"               # 链首就在飞：出手早于链首/窗口
    assert abs(tr.release_t - 1.0) < 1e-6      # 链首当出手估计


def test_短缺口桥接不断链():
    pts = [p for p in _pts_hold_then_flight() if not (1.5 < p.t < 1.8)]
    tr = shooter.trace_release(pts, 3.0, HOOP_W)   # 缺口 0.3s ≤ 0.3s 桥接上限
    assert tr.status == "high"
    assert tr.track_n == 23                     # 25-2，缺的 2 点没进链


def test_断链超桥接上限则截断():
    pts = [p for p in _pts_hold_then_flight() if not (1.4 <= p.t < 1.9)]
    tr = shooter.trace_release(pts, 3.0, HOOP_W)   # 缺口 0.5s > 0.3s 上限
    assert tr.status == "review"
    assert tr.track_n == 11                     # 只剩 1.9~2.9 的后段


def test_瞬移噪声点被跳过():
    pts = sorted(_pts_hold_then_flight() + [_pt(1.55, 3000, 3000)],
                 key=lambda p: p.t)
    tr = shooter.trace_release(pts, 3.0, HOOP_W)
    assert tr.status == "high"
    assert tr.track_n == 25                     # 噪声点没进链


def test_无可用轨迹判low():
    assert shooter.trace_release([], 3.0, HOOP_W).status == "low"
    assert shooter.trace_release([_pt(2.9, 340, 200)], 3.0,
                                 HOOP_W).status == "low"
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_shooter.py -v -k 回追或轨迹`
（Windows 下 `-k` 中文可用：`.venv\Scripts\python -m pytest tests/test_shooter.py -v` 全跑也行）
Expected: FAIL（`AttributeError: ... has no attribute 'trace_release'`）

- [ ] **Step 3: 实现（追加到 bbh/shooter.py）**

```python
def trace_release(points: list, t_goal: float,
                  hoop_w: float) -> TraceResult:
    """从进球时刻沿检出点时间倒序回追轨迹，找"持球→离手"速度跃变点。

    链式条件（倒序、相邻已收点与候选点）：间隔 ≤GAP_BRIDGE_S（短缺口
    桥接——遮挡/瞬断不断链）且速度 ≤V_MAX_RATIO（筐宽/秒；超限=瞬移噪声，
    跳过它继续看更早的点）。跃变判定=时间正序找第一段速度 ≥HOLD_SPEED_RATIO
    的位置，其起点=出手点（球刚离手加速）；链首就在飞（i==0）或全程慢
    →未找到跃变，取链首当出手估计、判 review（出手可能早于窗口/链起点，
    spec：照样出结果但标出来）。轨迹点 <2 → low 未溯源。"""
    pts = sorted((p for p in (points or []) if p.t <= t_goal + 0.05),
                 key=lambda p: p.t)
    if len(pts) < 2:
        return TraceResult(status="low")
    chain = [pts[-1]]                      # 锚点=最接近进球时刻的检出点
    for p in reversed(pts[:-1]):
        gap = chain[-1].t - p.t
        if gap > GAP_BRIDGE_S:
            break                          # 断链超桥接上限，链到此为止
        dist = ((chain[-1].x - p.x) ** 2 + (chain[-1].y - p.y) ** 2) ** 0.5
        if gap > 0 and dist / hoop_w / gap > V_MAX_RATIO:
            continue                       # 瞬移噪声：跳过、链继续往早收
        chain.append(p)
    chrono = list(reversed(chain))         # 时间正序
    if len(chrono) < 2:
        return TraceResult(status="low")
    release_i, found = 0, False
    for i in range(len(chrono) - 1):
        a, b = chrono[i], chrono[i + 1]
        dt = b.t - a.t
        if dt <= 0:
            continue
        v = (((b.x - a.x) ** 2 + (b.y - a.y) ** 2) ** 0.5) / hoop_w / dt
        if v >= HOLD_SPEED_RATIO:
            release_i, found = i, i > 0    # i==0=链首就在飞，跃变早于链首
            break
    span = chrono[-1].t - chrono[0].t
    status = ("high" if found and len(chrono) >= MIN_TRACK_N
              and span >= MIN_TRACK_SPAN_S else "review")
    r = chrono[release_i]
    return TraceResult(status=status, release_t=r.t,
                       release_xy=(r.x, r.y), track_n=len(chrono),
                       track_span=round(span, 2))
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv\Scripts\python -m pytest tests/test_shooter.py -v`
Expected: PASS（10 个）

- [ ] **Step 5: 提交**

```bash
git add bbh/shooter.py tests/test_shooter.py
git commit -m "feat: 轨迹回追——倒序连链+缺口桥接+瞬移噪声剔除，持球→离手速度跃变定出手点，三档置信"
```

---

### Task 3: 全帧回扫 scan_release_window（PTS 钟 + 复用 detector 的球判据）

**Files:**
- Modify: `bbh/shooter.py`（追加 import 与 `scan_release_window`、`_detect_source`）
- Test: `tests/test_shooter.py`（追加）

**Interfaces:**
- Consumes: `bbh.detector` 的 `ball_blobs(mask, max_area)`、`_scaled_rect(calib, w, h)`、`BAND_RATIO`、`MAX_AREA_RATIO`、`RIM_Y_RATIO`、`safe_fps`、`VideoOpenError`（包内引私有函数是库内惯例）
- Produces: `scan_release_window(video_path: str, calib: Calibration, t_goal: float, work_dir: str | None = None, window_s: float = SCAN_WINDOW_S, step: int = SCAN_STEP) -> tuple[list[BallPos], tuple[int, int]]`（返回 检出点 + (frame_w, frame_h)；Task 4/5 调用）；`_detect_source(video_path, work_dir) -> str`（Task 4 复用）

- [ ] **Step 1: 写失败测试（追加）**

```python
# ---------- 全帧回扫（合成视频实测） ----------
def test_scan回扫窗口检出下落球(sample_video, sample_calibration):
    # conftest：球在第 60~90 帧（t≈2.0~3.0）从 (340,100) 直落 (340,220)
    pts, size = shooter.scan_release_window(str(sample_video),
                                            sample_calibration,
                                            t_goal=2.8, window_s=2.0)
    assert size == (640, 360)
    assert pts, "球下落段应被检出"
    assert all(p.t <= 2.8 + 1e-6 for p in pts)     # PTS 钟，不超窗口上界
    assert all(abs(p.x - 340) < 6 for p in pts)    # 直落轨迹 x 恒为 340


def test_scan无球段与窗口截断到片头(sample_video, sample_calibration):
    pts, _ = shooter.scan_release_window(str(sample_video),
                                         sample_calibration,
                                         t_goal=1.5, window_s=1.0)
    assert pts == []                               # t<2.0 无球
    pts2, _ = shooter.scan_release_window(str(sample_video),
                                          sample_calibration,
                                          t_goal=1.0, window_s=6.0)
    assert pts2 == []                              # t0=max(0,-5)=0 不崩
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_shooter.py -v`
Expected: FAIL（`AttributeError: ... scan_release_window`）

- [ ] **Step 3: 实现**

`bbh/shooter.py` 顶部 import 区改为（新增 cv2/numpy/detector 依赖）：

```python
import json
import os
from dataclasses import dataclass

import cv2
import numpy as np

from .detector import (BAND_RATIO, MAX_AREA_RATIO, RIM_Y_RATIO, VideoOpenError,
                       _scaled_rect, ball_blobs, safe_fps)
from .models import BallPos, Calibration
from .pipeline import _calib_key
```

追加实现：

```python
def _detect_source(video_path: str, work_dir: str | None) -> str:
    """检测源：OpenCV 打不开原片时退回转码副本（pipeline 检测时生成过
    det-source.mp4，只转一次）。打不开且无副本 → 原样返回，由调用方报错。"""
    cap = cv2.VideoCapture(video_path)
    ok = cap.isOpened()
    cap.release()
    if ok:
        return video_path
    if work_dir:
        alt = os.path.join(work_dir, "det-source.mp4")
        if os.path.exists(alt):
            return alt
    return video_path


def scan_release_window(video_path: str, calib: Calibration, t_goal: float,
                        work_dir: str | None = None,
                        window_s: float = SCAN_WINDOW_S,
                        step: int = SCAN_STEP):
    """回扫 [t_goal-window_s, t_goal] 的**全帧** HSV 球检测。

    复用标定 HSV 与 detector 的面积/形状/筐带过滤（语义与主检测一致），
    只是区域从筐口 ROI 扩为全画面——出手点在三分线，主检出流从未覆盖。
    相邻进球窗口重叠无冲突：每球独立扫描、独立缓存。窗口跨界截断到 0。
    帧时间取 PTS（读不出时回退 t0+帧号/fps，同 detector 的兜底策略）。
    返回 (检出点列表, (frame_w, frame_h))。"""
    src = _detect_source(video_path, work_dir)
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise VideoOpenError(f"无法打开视频: {video_path}")
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fps = safe_fps(cap.get(cv2.CAP_PROP_FPS))
    hx, hy, hw, hh = _scaled_rect(calib, frame_w, frame_h)
    rim_y, band = hy + hh * RIM_Y_RATIO, hh * BAND_RATIO
    max_area = hw * hw * MAX_AREA_RATIO
    hsv_lo = np.array(calib.hsv_low, np.uint8)
    hsv_hi = np.array(calib.hsv_high, np.uint8)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    t0 = max(0.0, t_goal - window_s)
    out: list[BallPos] = []
    use_pts = None        # None=未判定（首帧 PTS=0 无法区分正常与不支持）
    fi = 0
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, t0 * 1000.0)
        while True:
            if not cap.grab():
                break
            msec = cap.get(cv2.CAP_PROP_POS_MSEC)
            if use_pts is None and fi > 0:
                use_pts = msec > 0
            t = msec / 1000.0 if use_pts else t0 + fi / fps
            if t >= t_goal:
                break                          # 窗口上界先判后收：不收 t≥t_goal 的点
            if fi % step == 0:
                ok, frame = cap.retrieve()
                if ok:
                    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
                    mask = cv2.inRange(hsv, hsv_lo, hsv_hi)
                    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
                    blobs = ball_blobs(mask, max_area)
                    if blobs:
                        a, c, bx, by = max(blobs, key=lambda b: b[0])
                        # 筐身条带内静态橙与主检测同口径忽略
                        if abs(by - rim_y) >= band:
                            out.append(BallPos(t=t, x=bx, y=by, area=a, fi=fi))
            fi += 1
    finally:
        cap.release()
    return out, (frame_w, frame_h)
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv\Scripts\python -m pytest tests/test_shooter.py -v`
Expected: PASS（12 个）

- [ ] **Step 5: 提交**

```bash
git add bbh/shooter.py tests/test_shooter.py
git commit -m "feat: 出手溯源全帧回扫——复用 HSV/形状/筐带判据扩到全画面，PTS 钟，转码副本兜底"
```

---

### Task 4: 射手裁剪 crop_shooter（筐宽标尺换算 + 两帧 + clamp）

**Files:**
- Modify: `bbh/shooter.py`（追加 `_clamp`、`_video_size`、`_grab_at`、`crop_shooter`）
- Test: `tests/test_shooter.py`（追加）

**Interfaces:**
- Consumes: Task 3 的 `_detect_source`；Task 1 的 `TraceResult`/`_key_safe`/`THUMB_DIR`；`_scaled_rect`
- Produces: `crop_shooter(video_path: str, calib: Calibration, tr: TraceResult, work_dir: str, key: str, back_s: float = 0.3) -> list[str]`（返回相对 work_dir 的缩略图路径，如 `["shooters/00-02.4_a.jpg", "shooters/00-02.4_b.jpg"]`；未溯源/取帧失败 → `[]`；Task 5 调用）

- [ ] **Step 1: 写失败测试（追加）**

```python
# ---------- 射手裁剪（合成视频实测） ----------
def test_crop两帧落盘且出手点在框内(sample_video, sample_calibration,
                                    tmp_path):
    tr = shooter.TraceResult(status="high", release_t=2.4,
                             release_xy=(340, 120))
    rel = shooter.crop_shooter(str(sample_video), sample_calibration, tr,
                               str(tmp_path), "00:02.4")
    assert rel == ["shooters/00-02.4_a.jpg", "shooters/00-02.4_b.jpg"]
    a = cv2.imread(str(tmp_path / "shooters" / "00-02.4_a.jpg"))
    b = cv2.imread(str(tmp_path / "shooters" / "00-02.4_b.jpg"))
    assert a is not None and b is not None
    assert a.shape[0] >= 100 and a.shape[1] >= 100    # 全身量级裁剪框


def test_crop出手点贴边clamp不越界(sample_video, sample_calibration, tmp_path):
    tr = shooter.TraceResult(status="review", release_t=2.4, release_xy=(2, 2))
    rel = shooter.crop_shooter(str(sample_video), sample_calibration, tr,
                               str(tmp_path), "00:02.4")
    assert rel, "clamp 后仍应能裁出"
    a = cv2.imread(str(tmp_path / "shooters" / "00-02.4_a.jpg"))
    assert a is not None and a.shape[0] > 0


def test_crop未溯源不裁(sample_video, sample_calibration, tmp_path):
    tr = shooter.TraceResult(status="low")
    assert shooter.crop_shooter(str(sample_video), sample_calibration, tr,
                                str(tmp_path), "00:02.4") == []
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_shooter.py -v`
Expected: FAIL（`AttributeError: ... crop_shooter`）

- [ ] **Step 3: 实现（追加到 bbh/shooter.py）**

```python
def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _grab_at(video_path: str, t: float, work_dir: str | None = None):
    """按秒取一帧（裁缩略图专用）。POS_MSEC 定位在 VFR 下有 ±0.1s 级
    偏差——对"裁人"足够（不影响时刻判定，时刻由 scan 的检出点决定）。"""
    cap = cv2.VideoCapture(_detect_source(video_path, work_dir))
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
        ok, frame = cap.read()
        return frame if ok else None
    finally:
        cap.release()


def crop_shooter(video_path: str, calib: Calibration, tr: TraceResult,
                 work_dir: str, key: str, back_s: float = 0.3) -> list[str]:
    """出手点下方裁射手全身缩略图，两帧：出手瞬间(_a) + 出手前 back_s 秒
    持球姿势帧(_b，通常更好认)。帧 _a 的出手点画红十字。

    尺寸换算以筐宽为标尺（筐直径 45cm）再放大 CROP_MARGIN 余量（射手不在
    筐平面上，透视偏差）；裁剪框出画面边界 clamp（spec：不硬给图——
    clamp 后退化 <20px 视同取帧失败返回 []）。返回相对 work_dir 路径。"""
    if tr.release_t is None or tr.release_xy is None:
        return []
    rx, ry = tr.release_xy
    frame_a = _grab_at(video_path, tr.release_t, work_dir)
    frame_b = _grab_at(video_path, tr.release_t - back_s, work_dir)
    if frame_a is None:
        return []
    fh, fw = frame_a.shape[:2]
    hw = _scaled_rect(calib, fw, fh)[2]
    px_per_cm = hw / 45.0
    crop_h = PERSON_H_CM * px_per_cm * CROP_MARGIN
    crop_w = CROP_W_CM * px_per_cm * CROP_MARGIN
    x0 = _clamp(int(rx - crop_w / 2), 0, fw - 1)
    y0 = _clamp(int(ry - 0.3 * hw), 0, fh - 1)   # 手在球正下方，上界略高过球
    x1 = _clamp(x0 + int(crop_w), 1, fw)
    y1 = _clamp(y0 + int(crop_h), 1, fh)
    if x1 - x0 < 20 or y1 - y0 < 20:
        return []
    safe = _key_safe(key)
    thumb_dir = os.path.join(work_dir, THUMB_DIR)
    os.makedirs(thumb_dir, exist_ok=True)
    a = frame_a[y0:y1, x0:x1].copy()
    cx, cy = int(rx) - x0, int(ry) - y0         # 出手点十字（帧 _a 标记）
    if 0 <= cx < (x1 - x0) and 0 <= cy < (y1 - y0):
        cv2.line(a, (cx - 8, cy), (cx + 8, cy), (0, 0, 255), 2)
        cv2.line(a, (cx, cy - 8), (cx, cy + 8), (0, 0, 255), 2)
    cv2.imwrite(os.path.join(thumb_dir, f"{safe}_a.jpg"), a)
    if frame_b is not None:
        cv2.imwrite(os.path.join(thumb_dir, f"{safe}_b.jpg"),
                    frame_b[y0:y1, x0:x1])
        return [f"{THUMB_DIR}/{safe}_a.jpg", f"{THUMB_DIR}/{safe}_b.jpg"]
    return [f"{THUMB_DIR}/{safe}_a.jpg"]
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv\Scripts\python -m pytest tests/test_shooter.py -v`
Expected: PASS（15 个）

- [ ] **Step 5: 提交**

```bash
git add bbh/shooter.py tests/test_shooter.py
git commit -m "feat: 射手裁剪——筐宽标尺全身框+出手前0.3s两帧+出手点十字，边界clamp不硬给图"
```

---

### Task 5: 逐球编排 trace_video（生成器：进度 yield + 汇总 return + 增量续跑）

**Files:**
- Modify: `bbh/shooter.py`（追加 `STATUS_TEXT` 与 `trace_video`）
- Test: `tests/test_shooter.py`（追加）

**Interfaces:**
- Consumes: Task 1-4 全部（`load_shooters`/`shooters_valid`/`save_shooters`/`scan_release_window`/`trace_release`/`crop_shooter`）；`bbh.models.time_key`；事件对象只需有 `.t` 属性（`GoalEvent` 即可）
- Produces: `trace_video(video_path: str, calib: Calibration, work_dir: str, events: list) -> Generator[str, None, str]`——每完成一球 `yield` 进度文案，全部完成 `return` 汇总文案（StopIteration.value；app 居中转发并靠它恢复按钮）。**生成器中途不许 raise**（单球异常捕获记 low，防 UI 按钮卡灰——gen_selected 2026-08-22 的教训）

- [ ] **Step 1: 写失败测试（追加）**

```python
# ---------- 编排（真跑 run_detection 后全链路） ----------
def _drain(gen):
    """跑完生成器：进度列表 + return 的汇总文案。"""
    prog = []
    while True:
        try:
            prog.append(next(gen))
        except StopIteration as st:
            return prog, st.value


def test_trace_video全链路(sample_video, sample_calibration, tmp_path):
    from bbh.pipeline import run_detection
    from bbh.models import load_events
    work = str(tmp_path)
    events_path, _ = run_detection(str(sample_video), sample_calibration, work)
    events = load_events(events_path)["events"]
    assert events, "合成视频应检出至少一个进球事件"

    prog, summary = _drain(shooter.trace_video(
        str(sample_video), sample_calibration, work, events))
    assert prog and "⏳" in prog[0]
    data = shooter.load_shooters(work)
    assert time_key(events[0].t) in data["traces"]
    tr = data["traces"][time_key(events[0].t)]
    assert tr["status"] in ("high", "review", "low")
    assert summary.startswith("✅")


def test_trace_video增量续跑与版本作废(sample_video, sample_calibration,
                                       tmp_path, monkeypatch):
    from bbh.pipeline import run_detection
    from bbh.models import load_events
    work = str(tmp_path)
    events_path, _ = run_detection(str(sample_video), sample_calibration, work)
    events = load_events(events_path)["events"]
    _drain(shooter.trace_video(str(sample_video), sample_calibration,
                               work, events))
    # 全部已有缓存 → 无进度，直接汇总（中断重跑只补缺）
    prog2, summary2 = _drain(shooter.trace_video(
        str(sample_video), sample_calibration, work, events))
    assert not prog2 and "未重扫" in summary2
    # 版本升级 → 身份不符，全部重扫
    monkeypatch.setattr(shooter, "SHOOTER_VERSION", 99)
    prog3, _ = _drain(shooter.trace_video(
        str(sample_video), sample_calibration, work, events))
    assert prog3
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_shooter.py -v`
Expected: FAIL（`AttributeError: ... trace_video`）

- [ ] **Step 3: 实现（追加到 bbh/shooter.py；顶部 import 补 `from collections import Counter` 与 `from .models import ... time_key`）**

```python
STATUS_TEXT = {"high": "🟢 轨迹完整", "review": "🟡 轨迹存疑",
               "low": "⚪ 未溯源"}


def trace_video(video_path: str, calib: Calibration, work_dir: str,
                events: list):
    """逐球 溯源→裁缩略图→写盘。生成器：每完成一球 yield 进度文案、
    结束 return 汇总文案。已有缓存（身份相符）的球跳过——中断后重跑只
    补缺（逐球写盘，已完成的不白扫，spec：同检测检查点思路）。

    单球任何异常不 raise：记 status=low + err 出错原因继续下一球——
    生成器中途 raise 会让 UI 按钮永久卡灰（gen_selected 教训）。"""
    data = load_shooters(work_dir)
    traces = (dict(data["traces"])
              if shooters_valid(data, video_path, calib) else {})
    todo = [e for e in (events or []) if time_key(e.t) not in traces]
    if not todo:
        return (f"✅ 出手人已识别（{len(traces)} 球缓存齐全，未重扫）——"
                "点表格任意一行查看射手缩略图")
    for k, e in enumerate(todo, 1):
        key = time_key(e.t)
        try:
            pts, size = scan_release_window(video_path, calib, e.t, work_dir)
            hoop_w = _scaled_rect(calib, size[0], size[1])[2]
            tr = trace_release(pts, e.t, hoop_w)
            thumbs = crop_shooter(video_path, calib, tr, work_dir, key)
            err = None
        except Exception as ex:  # noqa: BLE001 —— 单球失败不拖垮整批
            tr, thumbs, err = TraceResult(status="low"), [], str(ex)
        traces[key] = {"status": tr.status, "release_t": tr.release_t,
                       "release_xy": (list(tr.release_xy)
                                      if tr.release_xy else None),
                       "track_n": tr.track_n, "track_span": tr.track_span,
                       "thumbs": thumbs, "err": err}
        save_shooters(work_dir, video_path, calib, traces)   # 逐球写盘
        yield (f"⏳ 识别出手人（{k}/{len(todo)}）：{key} → "
               f"{STATUS_TEXT.get(tr.status, tr.status)}")
    n = Counter(v.get("status", "low") for v in traces.values())
    return (f"✅ 出手人识别完成：🟢 {n.get('high', 0)} · "
            f"🟡 {n.get('review', 0)} · ⚪ {n.get('low', 0)}"
            f"（共 {len(traces)} 球，缓存 shooters.json——点表格行看射手"
            "缩略图，照常打标签）")
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv\Scripts\python -m pytest tests/test_shooter.py -v`
Expected: PASS（17 个）

- [ ] **Step 5: 提交**

```bash
git add bbh/shooter.py tests/test_shooter.py
git commit -m "feat: trace_video 编排——逐球写盘增量续跑，进度 yield/汇总 return，单球异常记 low 不卡批次"
```

---

### Task 6: app 集成（按钮 + 生成器进度 + 行面板缩略图块）

**Files:**
- Modify: `bbh/app.py`：
  - import 区加 `from . import shooter`（与 `from . import calibration, editor, pipeline` 同处）
  - 新函数 `run_shooter_tracing`、`_shooter_block_html`（放在 `gen_selected` 之后、「🧪 误判/漏检反馈」注释块之前）
  - `_cur_panel_html` 加可选参 `video_path`，3 个调用点传参
  - `build_app` 左栏按钮行加 `btn_shooter` + `shooter_md`，wiring 区加 `.click`
- Test: `tests/test_shooter.py`（追加）

**Interfaces:**
- Consumes: Task 5 的 `trace_video`（StopIteration.value 取汇总）、Task 1 的 `load_shooters`；app 既有 `clean_path`/`_calib_ready`/`_load_events_or_none`/`_work_dir_for`/`_raw_time_keys`/`Calibration.load`/`_reason_for`
- Produces: `run_shooter_tracing(video_path: str) -> Generator[tuple[str, gr.update], ...]`（对应 outputs `[shooter_md, btn_shooter]`）；`_shooter_block_html(video_path, rows, i) -> str`（纯 HTML 片段，进 `gr.HTML`，不消毒——内容全部自建/路径编码，不含用户输入）；`_cur_panel_html(rows, i, reason="", video_path=None)`（新增第 4 参，默认 None 保持旧调用兼容）

- [ ] **Step 1: 写失败测试（追加）**

```python
# ---------- app 集成 ----------
def test_run_shooter_tracing缺检测结果报错(sample_video, sample_calibration,
                                          tmp_path, monkeypatch):
    import gradio as gr
    monkeypatch.setattr(app_module, "CALIB_FILE", str(tmp_path / "c.json"))
    sample_calibration.save(str(tmp_path / "c.json"))
    monkeypatch.setattr(app_module, "_work_dir_for", lambda p: str(tmp_path / "w"))
    with pytest.raises(gr.Error):
        list(app_module.run_shooter_tracing(str(sample_video)))


def test_run_shooter_tracing全流程(sample_video, sample_calibration,
                                   tmp_path, monkeypatch):
    from bbh.pipeline import run_detection
    work = str(tmp_path / "w")
    monkeypatch.setattr(app_module, "CALIB_FILE", str(tmp_path / "c.json"))
    sample_calibration.save(str(tmp_path / "c.json"))
    monkeypatch.setattr(app_module, "_work_dir_for", lambda p: work)
    run_detection(str(sample_video), sample_calibration, work)
    outs = list(app_module.run_shooter_tracing(str(sample_video)))
    assert outs, "至少一条汇总"
    last_msg, last_btn = outs[-1]
    assert last_msg.startswith("✅")
    assert last_btn["interactive"] is True      # 结束恢复按钮
    assert shooter.load_shooters(work)["traces"]


def test_shooter_block三种状态(sample_video, sample_calibration, tmp_path,
                               monkeypatch):
    work = str(tmp_path / "w")
    monkeypatch.setattr(app_module, "_work_dir_for", lambda p: work)
    rows = [[0, "00:02.5", "high", "0.90", 5, 2, ""]]
    # ① 从未识别过 → 引导提示
    assert "识别出手人" in app_module._shooter_block_html(str(sample_video),
                                                        rows, 0)
    # ② 已识别但该球未溯源 → ⚪ 说明
    shooter.save_shooters(work, str(sample_video), sample_calibration,
                          {"99:99.9": {"status": "low", "thumbs": []}})
    assert "未溯源" in app_module._shooter_block_html(str(sample_video), rows, 0)
    # ③ 有溯源 → 缩略图 <img>（写真 jpg 满足存在性检查）
    (tmp_path / "w" / "shooters").mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(tmp_path / "w" / "shooters" / "a.jpg"),
                np.zeros((10, 10, 3), np.uint8))
    shooter.save_shooters(work, str(sample_video), sample_calibration,
                          {"00:02.5": {"status": "high", "release_t": 2.3,
                                       "release_xy": [340, 200],
                                       "thumbs": ["shooters/a.jpg"]}})
    h = app_module._shooter_block_html(str(sample_video), rows, 0)
    assert "<img" in h and "出手人" in h


def test_cur_panel带video_path附出手人块(sample_video, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_work_dir_for", lambda p: str(tmp_path / "w"))
    rows = [[0, "00:02.5", "high", "0.90", 5, 2, ""]]
    h = app_module._cur_panel_html(rows, 0, "判定依据：…",
                                   video_path=str(sample_video))
    assert "出手人" in h
    assert "出手人" not in app_module._cur_panel_html(rows, 0, "判定依据：…")
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_shooter.py -v`
Expected: FAIL（`AttributeError: module 'bbh.app' has no attribute 'run_shooter_tracing'`）

- [ ] **Step 3: 实现**

**3a. import 区**（`from . import calibration, editor, pipeline` 行改为）：

```python
from . import calibration, editor, pipeline, shooter
```

**3b. 新函数**（插在 `gen_selected` 函数定义结束之后、`# ---------- 🧪 误判/漏检反馈` 注释之前）：

```python
# ---------- 🧍 出手人识别（Phase 1：溯源+缩略图；聚类分组是 Phase 2） ----------
def run_shooter_tracing(video_path: str):
    """「🧍 识别出手人」→ 逐球回扫溯源 + 裁射手缩略图（生成器进度，同生成
    集锦模式）。已缓存的球自动跳过（增量续跑）；结果进 work/<视频>/
    shooters.json，重开页面秒载不重跑。对应 [shooter_md, btn_shooter]：
    进行中禁用按钮，结束（含空跑）恢复。"""
    video_path = clean_path(video_path)
    if not video_path or not os.path.exists(video_path):
        raise gr.Error(f"视频不存在: {video_path}")
    if not _calib_ready():
        raise gr.Error("还没标定——先去「⚙ 标定设置」完成一次")
    data = _load_events_or_none(video_path)
    if not data or not data.get("events"):
        raise gr.Error("尚无检测结果——先点「▶ 开始检测」")
    try:
        calib = Calibration.load(CALIB_FILE)
    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        raise gr.Error("标定文件损坏，请重新标定")
    gen = shooter.trace_video(video_path, calib, _work_dir_for(video_path),
                              data["events"])
    while True:    # 生成器无法预知结尾：进度逐条转发（禁用按钮），结尾取汇总
        try:
            msg = next(gen)
        except StopIteration as st:
            summary = st.value or "✅ 完成"
            break
        yield msg, gr.update(interactive=False)
    yield summary, gr.update(interactive=True)


def _shooter_block_html(video_path, rows, i) -> str:
    """行面板「出手人」块：两帧射手缩略图（帧 _a 带出手点红十字）+ 三档
    小标 + 出手时刻。未识别过 → 引导提示；该球未溯源 → ⚪ 说明。
    只展示不写标签——打标签仍走标签下拉（看图打标比回看视频快，
    Phase 1 不自动写标签）。内容全部自建（无用户输入），路径经 quote。"""
    try:
        i = int(i)
    except (TypeError, ValueError):
        return ""
    if not (0 <= i < len(rows or [])):
        return ""
    video_path = clean_path(video_path)
    if not video_path:
        return ""
    keys = _raw_time_keys(video_path, rows)
    key = keys[i] if i < len(keys) else None
    data = shooter.load_shooters(_work_dir_for(video_path))
    if not (data and data.get("traces")):
        return ('<div style="font-size:12px; color:#888; margin-top:6px">'
                '🧍 出手人：未识别——点左栏「🧍 识别出手人」批量生成射手'
                '缩略图后，看图打标不用回看视频</div>')
    tr = data["traces"].get(key or "")
    if not tr:
        return ('<div style="font-size:12px; color:#888; margin-top:6px">'
                '🧍 出手人：⚪ 该球未溯源（轨迹被挡/出手点在画面外）——'
                '需回看视频打标</div>')
    dot = shooter.STATUS_TEXT.get(tr.get("status"), "⚪")
    imgs = ""
    work = _work_dir_for(video_path)
    for rel in tr.get("thumbs") or []:
        p = os.path.join(work, rel)
        if os.path.exists(p):
            url = ("/gradio_api/file=" + urllib.parse.quote(
                os.path.abspath(p).replace("\\", "/"), safe="/:")
                + f"?v={os.path.getmtime(p):.0f}")   # mtime 击穿浏览器缓存
            imgs += (f'<img src="{url}" style="max-height:170px; '
                     'border-radius:6px; margin:4px 4px 0 0; '
                     'border:1px solid #e5e7eb">')
    rt = tr.get("release_t")
    rel_s = f"出手 {rt:.1f}s 时刻 · " if isinstance(rt, (int, float)) else ""
    return (f'<div style="margin-top:6px; font-size:12.5px">'
            f'<b>🧍 出手人 {dot}</b>（{rel_s}判定看图打标签）<br>{imgs}</div>')
```

**3c. `_cur_panel_html` 加参并追加块**——函数签名与末行改为：

```python
def _cur_panel_html(rows, i, reason: str = "", video_path: str = None) -> str:
```

（docstring 补一句："video_path 给定时附「出手人」缩略图块（_shooter_block_html）。"）末行 `return head + body` 改为：

```python
    return head + body + _shooter_block_html(video_path, rows, i)
```

**3d. 三个调用点传 `video_path`**（其余调用点不传、行为不变——重置面板等场景不展示缩略图，用户点行即出现）：

- `preview_row_click` 末尾（原 `_cur_panel_html(rows, i, reason)`）：
```python
    return (player, gr.update(visible=False), i,
            _cur_panel_html(rows, i, reason, video_path), rows[i][6] or None)
```
- `set_row_tag` 末尾（原 `_cur_panel_html(rows, i, _reason_for(video_path, rows, i))`）：
```python
       return (project_rows(rows), rows,
               _cur_panel_html(rows, i, _reason_for(video_path, rows, i),
                               video_path))
```
- `apply_all_rows` 中部（原 `_cur_panel_html(out, cur_row, _reason_for(video_path, out, cur_row))`）：
```python
        return (out, project_rows(out),
                _cur_panel_html(out, cur_row,
                                _reason_for(video_path, out, cur_row),
                                video_path))
```

**3e. `build_app` 左栏组件**（`btn_generate` 所在 `with gr.Row():` 块内加第三个按钮，其后加 `shooter_md`）：

```python
                    with gr.Row():
                        btn_start = gr.Button("▶ 开始检测", variant="primary",
                                              size="sm")
                        btn_generate = gr.Button("🎬 生成集锦", variant="primary",
                                                 size="sm")
                        btn_shooter = gr.Button("🧍 识别出手人", size="sm")
                    shooter_md = gr.Markdown("")
```

**3f. wiring**（`btn_gen_cancel.click` 之后加）：

```python
                    btn_shooter.click(run_shooter_tracing,
                                      inputs=[video_path_box],
                                      outputs=[shooter_md, btn_shooter])
```

- [ ] **Step 4: 运行确认通过（含 app 全量回归）**

Run: `.venv\Scripts\python -m pytest tests/test_shooter.py tests/test_app.py -v`
Expected: PASS（test_shooter 21 个 + test_app 既有全部——`test_build_app可构建` 会验证新按钮/组件不破坏构建）

- [ ] **Step 5: 提交**

```bash
git add bbh/app.py tests/test_shooter.py
git commit -m "feat: 剪辑台接入出手人识别——按钮+逐球进度，行面板射手缩略图块（三档标+出手点十字帧）"
```

---

### Task 7: 验收工具 check_shooters.py（对照表 + 自动指标）

**Files:**
- Create: `tools/check_shooters.py`
- Test: `tests/test_shooter.py`（追加）

**Interfaces:**
- Consumes: `work/<视频>/shooters.json`（Task 1 格式）、truth 文件的 `shooters` 字段（键 `01:23.7` 与 `time_key` 同口径，直接字典匹配——实测 truth/2026-08-22_1.json 的 shooters 键即此格式）
- Produces: `main()` 命令行入口：`python tools/check_shooters.py <work目录> <truth.json>` → 终端自动指标 + `<work目录>/shooters_check.html` 对照表；退出码恒 0（验收门是人工看图，工具只出材料）

- [ ] **Step 1: 写失败测试（追加）**

```python
# ---------- 验收工具 ----------
def test_check_shooters出对照表(tmp_path, sample_video, sample_calibration):
    work = tmp_path / "w"
    (work / "shooters").mkdir(parents=True)
    cv2.imwrite(str(work / "shooters" / "a.jpg"),
                np.zeros((10, 10, 3), np.uint8))
    shooter.save_shooters(str(work), str(sample_video), sample_calibration,
                          {"00:02.5": {"status": "high", "release_t": 2.3,
                                       "release_xy": [340, 200],
                                       "thumbs": ["shooters/a.jpg"]}})
    truth = tmp_path / "truth.json"
    truth.write_text(json.dumps({"shooters": {"00:02.5": "老杨"}}),
                     encoding="utf-8")
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "tools/check_shooters.py",
                        str(work), str(truth)], capture_output=True,
                       text=True, encoding="utf-8", cwd=repo, env=env)
    assert r.returncode == 0, r.stderr
    html_txt = (work / "shooters_check.html").read_text(encoding="utf-8")
    assert "老杨" in html_txt and "<img" in html_txt
    assert "对照" in r.stdout
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_shooter.py -v -k check`
Expected: FAIL（subprocess 报 `No such file or directory: tools/check_shooters.py`）

- [ ] **Step 3: 实现（创建 tools/check_shooters.py）**

```python
"""Phase 1 验收工具：射手缩略图 ↔ truth 出手人标注 对照表。

用法：
  .venv\\Scripts\\python tools/check_shooters.py work/2026-08-22_1 truth/2026-08-22_1.json
产出：
  <work目录>/shooters_check.html——每条=两帧缩略图+当时标注的出手人，
  浏览器打开肉眼核对"图里是不是那个人"（Phase 1 验收门：缩略图有效率
  ≥80% 且无系统性偏差，spec「Phase 1 验收」节）；
  终端打印自动指标：三档统计 / 标注对照数 / 出手→进球间隔合理区占比。
退出码恒 0——验收门是人工看图，本工具只出材料。"""
import json
import sys
from collections import Counter


def parse_key(k: str) -> float:
    """'01:23.7' → 83.7（truth/shooters 与 shooters.json 的行键同格式）。"""
    m, s = k.split(":")
    return int(m) * 60 + float(s)


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    work_dir, truth_path = sys.argv[1], sys.argv[2]
    with open(f"{work_dir}/shooters.json", encoding="utf-8") as f:
        traces = json.load(f)["traces"]
    with open(truth_path, encoding="utf-8") as f:
        shooters = json.load(f).get("shooters", {})

    matched = [(k, name, traces[k]) for k, name in shooters.items()
               if k in traces]
    rows, intervals = [], []
    for k, name, tr in sorted(matched, key=lambda x: parse_key(x[0])):
        rt = tr.get("release_t")
        if isinstance(rt, (int, float)):
            intervals.append(parse_key(k) - rt)
        imgs = "".join(f'<img src="{rel}" style="max-height:200px">'
                       for rel in tr.get("thumbs") or [])
        rows.append(f"<tr><td>{k}</td><td>{name}</td>"
                    f"<td>{tr.get('status')}</td><td>{imgs}</td></tr>")
    out_html = ("<html><meta charset='utf-8'><body>"
                f"<h3>出手人对照表（{len(matched)}/{len(shooters)} 条标注"
                "有溯源）</h3>"
                "<table border=1 cellpadding=6><tr><th>时间</th>"
                "<th>标注出手人</th><th>溯源档</th>"
                "<th>射手缩略图（帧A带出手点十字）</th></tr>"
                + "".join(rows) + "</table></body></html>")
    out = f"{work_dir}/shooters_check.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(out_html)

    n = Counter(tr.get("status") for tr in traces.values())
    print(f"溯源球数 {len(traces)}：🟢{n.get('high')} 🟡{n.get('review')} "
          f"⚪{n.get('low')}")
    print(f"标注对照 {len(matched)} 条（无溯源的标注 "
          f"{len(shooters) - len(matched)} 条）")
    if intervals:
        ok = sum(1 for i in intervals if 0.3 <= i <= 4.0)
        print(f"出手→进球间隔在 0.3~4.0s 合理区：{ok}/{len(intervals)}"
              "（离群=可能追错轨迹，对照表里重点核）")
    print(f"对照表已生成：{out}——浏览器打开，逐条核"
          "'缩略图里是不是标注的人'")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv\Scripts\python -m pytest tests/test_shooter.py -v`
Expected: PASS（22 个）

- [ ] **Step 5: 提交**

```bash
git add tools/check_shooters.py tests/test_shooter.py
git commit -m "feat: Phase 1 验收工具——truth 出手人标注×射手缩略图 HTML 对照表+三档/间隔自动指标"
```

---

### Task 8: README 更新 + 全量回归

**Files:**
- Modify: `README.md`
- Test: 全量 `tests/`

**Interfaces:**
- Consumes: 前七个任务的全部行为
- Produces: 文档与绿色测试套（228 既有 + 22 新 = 250）

- [ ] **Step 1: README 四处补文**

①「使用」节——打标签句（“…**选了标签的行自动勾选**…（清空标签不会取消勾选）”）之后、`→ 🎬 生成集锦` 之前插入一个要点：

```markdown
- **识别出手人**：复核前点左栏「🧍 识别出手人」——每球自动回扫进球前
  几秒找出手点、裁出射手缩略图（约每球 3 秒，跑一次进缓存，重开页面
  不重跑；已识别的球自动跳过）。点列表任意行 → 右侧面板显示两帧缩略
  图（帧 A 带出手点红十字；🟢 轨迹完整 / 🟡 轨迹存疑 / ⚪ 未溯源——
  🟡 的出手点位置可能偏、⚪ 没追到，这两种回看视频打标），**看图打标
  签**，不用逐条回看视频找出手人
```

②「工作原理」节末尾（「镜头位移校正」小节之后）加小节：

```markdown
### 出手人识别（Phase 1）

进球判定只看筐口周边；出手点在三分线，检出流从不覆盖。识别出手人时
对每个进球**按需回扫**进球前 6 秒的全帧画面（复用同一套 HSV/形状/筐带
判据，隔 2 帧采样 ≈15fps，每球约 3 秒），从进球穿越点倒序回追球的轨迹
（允许 0.45s 短缺口桥接、剔除瞬移噪声），找到"持球慢移→离手加速"的
速度跃变点 = 出手点，在其下方按筐宽标尺（筐直径 45cm）裁出射手全身
缩略图（出手瞬间 + 出手前 0.3s 两帧）。三档置信：🟢 轨迹完整且找到
跃变 / 🟡 轨迹短或追到窗口边界（出手点可能偏）/ ⚪ 未溯源。

缓存 `shooters.json` 身份键 = 视频 + 标定指纹 + `SHOOTER_VERSION`
（独立版本号，与 `DETECT_VERSION`/`LOGIC_VERSION` 无关）——溯源调参
不触发全片重检；逐球写盘，中断重跑只补缺。外观聚类+分组命名是
Phase 2（另行设计，Phase 1 验收通过后启动）。
```

③「数据目录」的 `work/<视频名>/` 列表里 `tag_assignments.json` 行后加：

```text
  shooters.json        出手人溯源缓存（出手点/三档/缩略图路径，独立版本键）
  shooters/            射手缩略图（每球两帧：出手瞬间 + 出手前 0.3s）
```

④「开发」节模块列表 `models.py` 行后加：

```text
  `shooter.py`（出手溯源：回扫/轨迹回追/射手裁剪，独立缓存）、
```

「设计文档」列表末尾加：

```markdown
- [出手人识别设计](docs/superpowers/specs/2026-08-23-shooter-attribution-design.md)
```

- [ ] **Step 2: 全量回归**

Run: `.venv\Scripts\python -m pytest tests/ -q`
Expected: 250 passed（228 既有 + 22 新），0 failed

- [ ] **Step 3: 提交**

```bash
git add README.md
git commit -m "docs: README 补出手人识别（Phase 1）——使用入口/工作原理/shooters 缓存/模块清单"
```

---

## Phase 1 验收（实现完成后、Phase 2 启动前——不是本计划的编码任务，是 gate）

按 spec「Phase 1 验收」节执行：

1. 两场真视频各跑一次「🧍 识别出手人」（08-22_1 约 162 球 ≈10 分钟；08-21_1 同量级）——**在真实 work 目录上跑之前确认不会触碰 positions.json**（本设计只新增 shooters.json，天然满足）
2. `.venv\Scripts\python tools/check_shooters.py work/2026-08-22_1 truth/2026-08-22_1.json`（08-21 同理），浏览器开对照表逐条肉眼核对
3. 通过标准：缩略图有效率 ≥80% 且无系统性偏差（如总慢半拍裁到防守人）→ 过 gate，启动 Phase 2 计划；不达标 → 修溯源（调阈值/`SHOOTER_VERSION+1` 重扫），不带病进 Phase 2
4. 阈值标定（spec 铁律）：用这两场的 trace 数据画分布（出手→进球间隔、轨迹点数/时长），复核 `MIN_TRACK_N`/`MIN_TRACK_SPAN_S`/`HOLD_SPEED_RATIO` 初值
