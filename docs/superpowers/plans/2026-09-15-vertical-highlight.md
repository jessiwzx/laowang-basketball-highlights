# 竖屏运镜集锦（vrender）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「生成集锦」产物从 1280×720 横屏直切替换为参考片同款竖屏成品：592×1280 全屏画幅 + 关键帧运镜（锁出手人 → 缓动平移 → 锁筐）+ 进球瞬间 0.3x 慢动作 + 白闪转场 + 末球定帧淡出。

**Architecture:** 新模块 `bbh/vrender.py`：纯函数 `plan_camera` 用现有数据（shooters.json 的出手点 / calibration 的筐矩形，全是视频像素坐标，零新增检测）算逐帧裁剪中心 → `cv2.warpAffine` 浮点裁剪经 rawvideo 管道喂 ffmpeg，filter_complex 把时间线三分（常速|minterpolate 慢动作|常速）→ 各球段 + 白闪微段统一规格流拷贝 concat → loudnorm。`app.generate_highlight` 加开关路由。

**Tech Stack:** Python 3.10+ / OpenCV（已在 venv）/ numpy / ffmpeg（复用 `editor._ffmpeg_exe`，imageio 捆绑版即可）/ pytest。

**Spec:** `docs/superpowers/specs/2026-09-15-vertical-highlight-design.md`（本计划与 spec 同轮修订过：坐标来源=像素非 homography、运镜仅横向、calibration.py 迁段加载。冲突以 spec 为准。）

## Global Constraints

- 仓库根：`D:\claude code\视频剪辑`（路径带空格，shell 命令一律加引号）。测试与命令都在仓库根跑。
- Python 一律用 venv 解释器：`.venv/Scripts/python.exe`（裸 `python` 不在 PATH）。
- **无 ffprobe**：取元数据用 `ffmpeg -i` 解析 stderr（`editor.probe_duration`）或 cv2 属性。
- **零新 pip 依赖**：只用 venv 里已有的 cv2/numpy/pytest。
- ffmpeg 一律经 `bbh.editor._ffmpeg_exe()`（PATH 优先，退 imageio 捆绑二进制）；子进程复用 `editor._run`（超时/错误信息同款）。
- 导出铁律（spec ①）：592×1280@30fps、H.264 CRF19 veryfast yuv420p、AAC 128k 44100 stereo、裁剪宽 `round(源高×592/1280)`（1080→500）、整条 loudnorm −14 LUFS。
- 慢动作铁律（spec ③）：默认 `[t−0.35, t+0.25]`、0.3x、窗不足 0.2s 跳过；插值只在 592×1280 小画面上做（`minterpolate` me_mode=bidir mc_mode=aobmc vsbmc=1）。
- 运镜铁律（spec ②）：pan 结束 `= t−0.35s`、时长 `clamp(max(飞行−0.35, π/2×位移/(0.45×裁剪宽)), 0.5, 1.8)s`、正弦缓动、5 帧滑平均、窗口钳制不出画面、zoom 恒定、cy 恒 = 源高/2（仅横向）。
- 转场铁律（spec ④）：白闪 2 帧白场+静音、片内直切、末球 0.5s 定帧淡出黑；所有段编码参数完全一致（流拷贝 concat 前提）。
- `plan_clips`/前后秒规则**不动**；`bbh/editor.py` **不动**（只被 import 复用）。
- 每个任务结束时全量测试必须绿：`.venv/Scripts/python.exe -m pytest tests/ -q`（当前基线 404 过）。
- 模块内注释/文档字符串用中文，风格对齐 `bbh/editor.py`（简洁、讲为什么）。

---

### Task 1: vrender 骨架 + 相机路径纯函数（hold/pan/lock）

**Files:**
- Create: `bbh/vrender.py`
- Test: `tests/test_vrender.py`

**Interfaces:**
- Consumes: 无（纯函数，本任务不碰任何 JSON/视频）。
- Produces（后续任务依赖的精确签名）:
  - `@dataclass RenderConfig`（字段见代码）
  - `@dataclass GoalCam(t: float, start: float, end: float, shooter_x: float | None, release_t: float | None, hoop_x: float, frame_w: int, frame_h: int, fps: float)`
  - `crop_width(frame_h: int, cfg: RenderConfig = CFG) -> int`
  - `camera_centers(g: GoalCam, cfg: RenderConfig = CFG) -> list[float]`（未平滑，长度 `round((end−start)×fps)`）
  - 模块级 `CFG = RenderConfig()`

- [ ] **Step 1: Write the failing test**

`tests/test_vrender.py`：

```python
"""vrender 单测：相机路径纯函数（规格与兜底）→ 渲染 → 组装。"""
import math

import pytest

from bbh.vrender import CFG, GoalCam, RenderConfig, camera_centers, crop_width


def _cam(**kw):
    """conftest 合成视频口径：640×360@30fps。默认进球 t=10.0，片窗 5~12s，
    出手人 x=280，筐 x=340（dist=60——足够近，峰值不破 0.45×裁剪宽）。"""
    base = dict(t=10.0, start=5.0, end=12.0, shooter_x=280.0,
                release_t=8.9, hoop_x=340.0,
                frame_w=640, frame_h=360, fps=30.0)
    base.update(kw)
    return GoalCam(**base)


class TestCropWidth:
    def test_1080源_裁500(self):
        assert 499 <= crop_width(1080) <= 500   # 1080×592/1280=499.5

    def test_合成源_按比例(self):
        assert 165 <= crop_width(360) <= 167    # 360×592/1280≈166.5


class TestCameraCenters:
    def test_无出手人_全程锁筐(self):
        cs = camera_centers(_cam(shooter_x=None))
        assert len(cs) == round(7.0 * 30) and all(c == 340.0 for c in cs)

    def test_hold与lock相位(self):
        cs = camera_centers(_cam())
        # hold 段（pan 起 8.395s 之前）：贴出手人；lock 段（pan 终 9.65s
        # 之后）：贴筐。取整帧时刻 5.0s / 11.9s。
        assert cs[0] == 280.0
        assert cs[-1] == 340.0
        # pan 中点帧（≈9.0s，u≈0.48）严格介于两者之间
        mid = cs[round((9.0 - 5.0) * 30)]
        assert 280.0 < mid < 340.0

    def test_钳制不出画面(self):
        cs = camera_centers(_cam(shooter_x=10.0, hoop_x=630.0))
        half = crop_width(360) / 2
        assert all(half - 1e-6 <= c <= 640 - half + 1e-6 for c in cs)

    def test_峰值速度受钳制(self):
        cs = camera_centers(_cam())
        v = [abs(b - a) * 30.0 for a, b in zip(cs, cs[1:])]
        assert max(v) <= 0.45 * crop_width(360) * 1.02

    def test_飞行短_pan提前起跑防甩鞭(self):
        # release_t=9.7 → flight 截到 flight_min=0.6 → pan 时长仍 ≥pan_min
        cs = camera_centers(_cam(release_t=9.7))
        assert 280.0 < cs[round((9.3 - 5.0) * 30)] < 340.0   # pan 已在进行
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_vrender.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'bbh.vrender'`

- [ ] **Step 3: Write minimal implementation**

`bbh/vrender.py`：

```python
"""竖屏运镜集锦：现有数据（出手点/筐标定，均视频像素坐标）→ 关键帧相机
路径 → warpAffine 浮点裁剪 → rawvideo 管道喂 ffmpeg（minterpolate 慢动作
/ 白闪 / concat 组装）。设计见
docs/superpowers/specs/2026-09-15-vertical-highlight-design.md。"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class RenderConfig:
    # —— 导出（spec ①）——
    out_w: int = 592
    out_h: int = 1280
    crf: int = 19
    audio_kbps: int = 128
    loudnorm_i: int = -14
    # —— 慢动作（spec ③）——
    slowmo_speed: float = 0.3
    slowmo_pre: float = 0.35      # 慢动作窗起点 = t − 此值
    slowmo_post: float = 0.25     # 慢动作窗终点 = t + 此值
    slowmo_min: float = 0.2       # 截短后不足此值 → 该球跳过慢动作
    # —— 运镜（spec ②）——
    flight_min: float = 0.6       # 出手→进球间隔的估计钳制
    flight_max: float = 1.5
    pan_gap: float = 0.35         # pan 必须在慢动作开始前完成（= t − 此值）
    pan_min: float = 0.5
    pan_max: float = 1.8          # 宁快勿拖封顶
    peak_frac: float = 0.45       # 峰值速度目标 ≤ 裁剪宽×此值/秒
    smooth_win: int = 5           # 逐帧中心滑平均窗（奇数）
    default_lead: float = 1.1     # release_t 缺失时按 t − 此值当出手时刻
    # —— 转场/收尾（spec ④）——
    transition: str = "white_flash"   # "white_flash" | "hard_cut"
    flash_frames: int = 2
    end_freeze_s: float = 0.5     # 末球定帧淡出时长；0=关


CFG = RenderConfig()


@dataclass
class GoalCam:
    """单球运镜输入（坐标均为源视频像素；start/end 已按片长裁界）。"""
    t: float                    # 进球时刻
    start: float                # 片段起 = max(0, t−before)
    end: float                  # 片段止 = min(duration, t+after)
    shooter_x: float | None     # 出手人像素 x；None（bad/缺失）→ 整段锁筐
    release_t: float | None     # 出手时刻；None → t − default_lead
    hoop_x: float               # 筐像素 x（所属标定段缩放后筐口中心）
    frame_w: int
    frame_h: int
    fps: float


def crop_width(frame_h: int, cfg: RenderConfig = CFG) -> int:
    """竖屏裁剪窗宽：吃满源高、比例与输出画幅同构（1080 高 → 500）。"""
    return max(2, round(frame_h * cfg.out_w / cfg.out_h))


def _ease(u: float) -> float:
    """正弦 ease-in-out：0→1 单调，峰值斜率 π/2（平均斜率的 1.57 倍）。"""
    return (1.0 - math.cos(math.pi * u)) / 2.0


def camera_centers(g: GoalCam, cfg: RenderConfig = CFG) -> list[float]:
    """逐源帧裁剪窗中心 cx（未平滑）：hold 锁出手人 → 正弦缓动平移 → 锁筐
    至片尾。无出手人 → 全程锁筐（spec ② 兜底）。逐帧钳制不出画面。"""
    n = max(1, round((g.end - g.start) * g.fps))
    cw = crop_width(g.frame_h, cfg)
    lo, hi = cw / 2.0, g.frame_w - cw / 2.0
    if g.shooter_x is None:
        return [min(max(g.hoop_x, lo), hi)] * n
    t_rel = g.release_t if g.release_t is not None else g.t - cfg.default_lead
    flight = min(max(g.t - t_rel, cfg.flight_min), cfg.flight_max)
    dist = abs(g.hoop_x - g.shooter_x)
    need = (math.pi / 2.0) * dist / (cfg.peak_frac * cw) if dist > 0 else 0.0
    pan_end = g.t - cfg.pan_gap
    pan_dur = min(max(flight - cfg.pan_gap, need, cfg.pan_min), cfg.pan_max)
    pan_start = max(g.start, pan_end - pan_dur)
    pan_dur = max(pan_end - pan_start, 1e-6)
    centers = []
    for i in range(n):
        ts = g.start + i / g.fps
        if ts >= pan_end:
            cx = g.hoop_x
        elif ts <= pan_start:
            cx = g.shooter_x
        else:
            u = (ts - pan_start) / pan_dur
            cx = g.shooter_x + (g.hoop_x - g.shooter_x) * _ease(u)
        centers.append(min(max(cx, lo), hi))
    return centers
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_vrender.py -v`
Expected: PASS（8 个用例全绿）

- [ ] **Step 5: 全量回归 + Commit**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: 基线 404 + 新 8 全过

```bash
git add bbh/vrender.py tests/test_vrender.py
git commit -m "feat: vrender 相机路径纯函数——hold/正弦pan/锁筐三相位+钳制+无出手人兜底"
```

---

### Task 2: 平滑 + plan_camera 组合 + 慢动作窗口

**Files:**
- Modify: `bbh/vrender.py`（追加）
- Modify: `tests/test_vrender.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `camera_centers`/`GoalCam`/`RenderConfig`。
- Produces:
  - `smooth_curve(xs: list[float], win: int = 5) -> list[float]`
  - `plan_camera(g: GoalCam, cfg: RenderConfig = CFG) -> list[float]`（平滑后终值，长度同 `camera_centers`）
  - `slowmo_window(g: GoalCam, cfg: RenderConfig = CFG) -> tuple[float, float] | None`（源秒，已截进 `[start,end]`；不足 `slowmo_min` → None）

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_vrender.py`：

```python
from bbh.vrender import plan_camera, slowmo_window, smooth_curve


class TestSmoothCurve:
    def test_阶跃边沿变斜坡(self):
        assert smooth_curve([0, 0, 0, 10, 10, 10]) == [0, 2, 4, 6, 8, 10]

    def test_短列原样返回(self):
        assert smooth_curve([1.0, 2.0], 5) == [1.0, 2.0]


class TestPlanCamera:
    def test_长度等于片窗帧数且无折角(self):
        cs = plan_camera(_cam())
        assert len(cs) == 210
        # 相邻帧位移 ≤ pan 峰值帧步（平滑只会更缓，不会产生更大跳变）
        step = max(abs(b - a) for a, b in zip(cs, cs[1:]))
        assert step <= 0.45 * crop_width(360) / 30.0 * 1.05


class TestSlowmoWindow:
    def test_常规窗(self):
        assert slowmo_window(_cam()) == (9.65, 10.25)

    def test_被前后秒截短(self):
        assert slowmo_window(_cam(start=9.9)) == (9.9, 10.25)

    def test_不足下限跳过(self):
        assert slowmo_window(_cam(start=9.95, end=10.05)) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_vrender.py -v`
Expected: FAIL — `ImportError: cannot import name 'plan_camera'`

- [ ] **Step 3: Write minimal implementation**

追加到 `bbh/vrender.py`：

```python
import numpy as np   # （合并进文件顶部 import 区）


def smooth_curve(xs: list[float], win: int = 5) -> list[float]:
    """滑动平均（边缘复制填充）：消 hold→pan→lock 衔接折角。win 偶/过大
    自动收缩成可用奇数窗；len≤2 原样返回。"""
    if win <= 1 or len(xs) <= 2:
        return list(xs)
    arr = np.asarray(xs, np.float64)
    pad = min(win // 2, len(arr) - 1)
    if pad == 0:
        return list(xs)
    padded = np.concatenate([np.full(pad, arr[0]), arr, np.full(pad, arr[-1])])
    kernel = np.ones(2 * pad + 1) / (2 * pad + 1)
    return list(np.convolve(padded, kernel, mode="valid"))


def plan_camera(g: GoalCam, cfg: RenderConfig = CFG) -> list[float]:
    """camera_centers + 平滑 = 渲染用的最终逐帧 cx。"""
    return smooth_curve(camera_centers(g, cfg), cfg.smooth_win)


def slowmo_window(g: GoalCam, cfg: RenderConfig = CFG) -> tuple[float, float] | None:
    """慢动作源窗 (t−pre, t+post) 截进片窗；不足 slowmo_min → None
    （spec ③：窗被极小前后秒截短时收缩，太小干脆不慢）。"""
    a = max(g.start, g.t - cfg.slowmo_pre)
    b = min(g.end, g.t + cfg.slowmo_post)
    return (a, b) if b - a >= cfg.slowmo_min else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_vrender.py -v`
Expected: PASS

- [ ] **Step 5: 全量回归 + Commit**

```bash
git add bbh/vrender.py tests/test_vrender.py
git commit -m "feat: vrender 路径平滑+慢动作窗口——5帧滑平均消折角、窗口截短与下限跳过"
```

---

### Task 3: calibration.load_segments_dir 迁移（app 委托）

vrender 需要段几何，但不能 import app（gradio 重依赖 + 反向依赖）。把 `app.load_segments` 的主体原样迁到 `calibration.py`，app 保签名委托——行为零变化（`tests/test_multi_segment.py`、`tests/test_calib_per_video.py` 是既有回归网）。

**Files:**
- Modify: `bbh/calibration.py`
- Modify: `bbh/app.py:395-416`
- Test: `tests/test_vrender.py`（追加，用 tmp 目录直接测新函数）

**Interfaces:**
- Consumes: `models.Calibration/CalibSegment/ref_image_path`（已有）。
- Produces: `calibration.load_segments_dir(work_dir: str) -> list[CalibSegment]`（t0 升序；主标定缺失/损坏 → `[]`；补充段损坏跳过；`calib_t=0` 的补充段不收——与 app 现行为逐字一致）。

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_vrender.py`：

```python
import json as _json

from bbh.calibration import load_segments_dir
from bbh.models import Calibration


def _write_calib(path, calib_t=0.0):
    Calibration(hoop_rect=(300, 140, 80, 40), hsv_low=(0, 80, 120),
                hsv_high=(25, 255, 255), frame_size=(640, 360),
                calib_t=calib_t).save(str(path))


class TestLoadSegmentsDir:
    def test_只有主标定_单段t0为零(self, tmp_path):
        _write_calib(tmp_path / "calibration.json")
        segs = load_segments_dir(str(tmp_path))
        assert [(s.t0) for s in segs] == [0.0]

    def test_多段按t0升序_损坏段跳过(self, tmp_path):
        _write_calib(tmp_path / "calibration.json")
        _write_calib(tmp_path / "calibration_seg2.json", calib_t=1550.0)
        _write_calib(tmp_path / "calibration_seg1.json", calib_t=700.0)
        (tmp_path / "calibration_seg3.json").write_text("不是json")
        assert [s.t0 for s in load_segments_dir(str(tmp_path))] == [0.0, 700.0, 1550.0]

    def test_主标定缺失或损坏_空列表(self, tmp_path):
        assert load_segments_dir(str(tmp_path)) == []
        (tmp_path / "calibration.json").write_text("}")
        assert load_segments_dir(str(tmp_path)) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_vrender.py -v`
Expected: FAIL — `ImportError: cannot import name 'load_segments_dir'`

- [ ] **Step 3: Write minimal implementation**

`bbh/calibration.py` 追加（顶部补 `import glob, os`；`json` 已有则不重复）：

```python
_SEG_GLOB = "calibration_seg*.json"


def load_segments_dir(work_dir: str) -> list:
    """work 目录 → CalibSegment 列表（t0 升序）。从 app.load_segments 迁移
    （vrender 需要、不能反向 import app/gradio），行为零变化：主标定缺失/
    损坏 → []；补充段个别损坏跳过；calib_t=0 的补充段不收（主标定即段 0）。"""
    base_p = os.path.join(work_dir, "calibration.json")
    try:
        base = Calibration.load(base_p)
    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        return []
    segs = [CalibSegment(0.0, base, ref_image_path(base_p, base))]
    for p in sorted(glob.glob(os.path.join(work_dir, _SEG_GLOB))):
        try:
            c = Calibration.load(p)
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            continue
        if c.calib_t > 0:
            segs.append(CalibSegment(c.calib_t, c, ref_image_path(p, c)))
    segs.sort(key=lambda s: s.t0)
    return segs
```

`bbh/app.py:395-416` 的 `load_segments` 改为委托（docstring 说明迁移）：

```python
def load_segments(video_path) -> list:
    """该视频的全部标定段（CalibSegment 列表，t0 升序）。主体已迁
    calibration.load_segments_dir（vrender 复用且不能 import app），本函数
    保签名兼容旧调用点。"""
    d = _calib_dir(video_path)
    return calibration.load_segments_dir(d) if d else []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_vrender.py tests/test_multi_segment.py tests/test_calib_per_video.py -v`
Expected: PASS（新 3 例 + 两套既有段回归全绿——证明迁移零行为变化）

- [ ] **Step 5: 全量回归 + Commit**

```bash
git add bbh/calibration.py bbh/app.py tests/test_vrender.py
git commit -m "refactor: 标定段加载迁 calibration.load_segments_dir——app 委托保签名，vrender 不必反向依赖 gradio"
```

---

### Task 4: render_clip 单球渲染（裁剪管道 + 三分时间线 + 冻结淡出）

**Files:**
- Modify: `bbh/vrender.py`（追加）
- Modify: `tests/test_vrender.py`（追加）

**Interfaces:**
- Consumes: Task 1/2 的 `plan_camera`/`slowmo_window`/`crop_width`；`editor._ffmpeg_exe`/`editor._run`。
- Produces:
  - `_V_ENC: list[str]` / `_A_ENC: list[str]`（统一编码参数，Task 5 白闪复用）
  - `_timeline_filter(ia, ib, n, fps_in, speed, freeze_s, clip_start, clip_end) -> tuple[str, float]`（帧号半开区间 `[ia,ib)`=慢动作源窗；`ib<=ia`=无慢动作；返回 filter_complex 与输出总时长）
  - `render_clip(video_path, g: GoalCam, centers: list[float], slowmo: tuple[float,float] | None, out: str, cfg=CFG, freeze_s: float = 0.0) -> str`
  - `render_clip` 抛 `RuntimeError`（OpenCV 打不开/读帧不足/ffmpeg 失败）——Task 5 靠它跳过坏球。

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_vrender.py`（conftest 的 `sample_video` 为 640×360@30fps、6s；**本任务起测试要跑真 ffmpeg，单例数秒**。渲染用例一律用 `sample_video_snd`——cv2 VideoWriter 从不写音频轨，无音轨源会让 filtergraph 的 `[1:a]` 绑定必炸（ffmpeg 7.1 实测），mux 静音轨对齐生产形态）：

```python
import subprocess

import cv2

from bbh.editor import _ffmpeg_exe
from bbh.vrender import _timeline_filter, render_clip


@pytest.fixture(scope="session")
def sample_video_snd(sample_video, tmp_path_factory):
    """带音轨源：生产源必有现场原声，渲染链路的 [1:a] 需要它。"""
    out = str(tmp_path_factory.mktemp("snd") / "sample_snd.mp4")
    subprocess.run([_ffmpeg_exe(), "-y", "-i", str(sample_video),
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-c:v", "copy", "-c:a", "aac", "-shortest", out],
                   check=True, capture_output=True)
    return out


def _meta(path):
    cap = cv2.VideoCapture(path)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    return w, h, fps, n


class TestTimelineFilter:
    def test_三段齐全(self):
        fc, total = _timeline_filter(30, 48, 90, 30.0, 0.3, 0.0, 1.5, 4.5)
        assert "minterpolate=fps=100" in fc and "setpts=3.333333*PTS" in fc
        assert fc.count("trim=start_frame=") == 3
        assert "concat=n=3:v=1:a=0[vout]" in fc
        assert "anullsrc=r=44100:cl=stereo,atrim=end=2.000" in fc
        assert "afade=t=out" in fc and "afade=t=in" in fc
        # pre 1.0s + sm 0.6/0.3 + post 1.4s ≈ 4.4
        assert abs(total - 4.4) < 0.05

    def test_慢动作窗贴片段头_无pre段(self):
        fc, total = _timeline_filter(0, 18, 90, 30.0, 0.3, 0.0, 1.5, 4.5)
        assert fc.count("trim=start_frame=") == 2
        assert "concat=n=2:v=1:a=0[vout]" in fc

    def test_慢动作窗贴片段尾_无post段(self):
        fc, total = _timeline_filter(12, 90, 90, 30.0, 0.3, 0.0, 1.5, 4.5)
        assert fc.count("trim=start_frame=") == 2
        assert "concat=n=2" in fc and "apad" not in fc
        # 单 flat 段（pre）的 atrim 必须接在 [1:a] 预处理链尾，不能成孤儿链
        assert "[a0]atrim" not in fc

    def test_整段皆慢动作_零flat段(self):
        fc, total = _timeline_filter(0, 90, 90, 30.0, 0.3, 0.0, 1.5, 4.5)
        assert "[1:a]" not in fc              # 源音频整条不用（防孤儿输入链）
        assert fc.count("anullsrc") == 1 and "[aout]" in fc

    def test_无慢动作_单段直通(self):
        fc, total = _timeline_filter(0, 0, 90, 30.0, 0.3, 0.0, 1.5, 4.5)
        assert "minterpolate" not in fc and "concat" not in fc
        assert "[vout]" in fc and "[aout]" in fc
        assert abs(total - 3.0) < 0.05

    def test_冻结补静音(self):
        fc, total = _timeline_filter(0, 0, 90, 30.0, 0.3, 0.5, 1.5, 4.5)
        assert "apad=pad_dur=0.500" in fc
        assert abs(total - 3.5) < 0.05


class TestRenderClip:
    def test_规格与慢动作时长膨胀(self, sample_video_snd, tmp_path):
        from bbh.vrender import plan_camera, slowmo_window
        g = _cam(t=3.0, start=1.5, end=4.5, release_t=1.9)
        out = str(tmp_path / "seg.mp4")
        render_clip(sample_video_snd, g, plan_camera(g), slowmo_window(g), out)
        w, h, fps, n = _meta(out)
        assert (w, h) == (592, 1280) and 29 <= fps <= 31
        # pre 1.15 + sm 2.0 + post 1.25 = 4.4s → 132 帧 ±15（fps 滤镜容差）
        assert 117 <= n <= 147

    def test_无慢动作_直通时长(self, sample_video_snd, tmp_path):
        g = _cam(t=3.0, start=1.5, end=4.5, release_t=1.9)
        out = str(tmp_path / "seg.mp4")
        render_clip(sample_video_snd, g, plan_camera(g), None, out)
        _, _, _, n = _meta(out)
        assert 75 <= n <= 105                 # ≈3.0s×30

    def test_末球冻结淡出到黑(self, sample_video_snd, tmp_path):
        g = _cam(t=3.0, start=1.5, end=4.5, release_t=1.9)
        out = str(tmp_path / "seg.mp4")
        render_clip(sample_video_snd, g, plan_camera(g), None, out, freeze_s=0.5)
        _, _, _, n = _meta(out)
        assert n >= 105                        # ≥3.5s×30
        cap = cv2.VideoCapture(out)
        for _ in range(n - 1):
            cap.read()                         # 读到最后一帧
        ok, last = cap.read()
        cap.release()
        assert ok and float(last.mean()) < 25  # 已淡到近黑
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_vrender.py -v`
Expected: FAIL — `ImportError: cannot import name '_timeline_filter'`

- [ ] **Step 3: Write minimal implementation**

追加到 `bbh/vrender.py`（顶部 import 区补：`import os, subprocess`；`import cv2`；`from .editor import _ffmpeg_exe, _run`）：

```python
# 统一编码参数——所有段（正片/白闪）必须逐字一致，流拷贝 concat 的前提
_V_ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
          "-pix_fmt", "yuv420p", "-r", "30"]
_A_ENC = ["-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2"]


def _crop_frame(frame, cx: float, cw: int, cfg: RenderConfig):
    """warpAffine 浮点裁剪一步到位：横向取 [cx−cw/2, cx+cw/2]、纵向吃满
    源高，缩放 s=out_w/cw 映到 592×1280（画幅同构，源高恰映到 out_h）。
    subpixel 无整点抖动——不用 ffmpeg zoompan 的根本原因（Trac #4298）。"""
    s = cfg.out_w / cw
    m = np.float32([[s, 0.0, cfg.out_w / 2.0 - s * cx],
                    [0.0, s, cfg.out_h / 2.0 - s * frame.shape[0] / 2.0]])
    return cv2.warpAffine(frame, m, (cfg.out_w, cfg.out_h),
                          flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)
```

```python
def _timeline_filter(ia: int, ib: int, n: int, fps_in: float, speed: float,
                     freeze_s: float, clip_start: float,
                     clip_end: float) -> tuple[str, float]:
    """管道帧三分时间线：[0,ia) 常速 | [ia,ib) 慢动作 | [ib,n) 常速（含
    冻结帧，冻结帧由调用方追加在管道尾、天然落进末段开区间）。音频对位：
    慢动作前 0.1s 淡出 → 静音 → 后 0.1s 淡入（freeze>0 末段再补静音）。
    ib<=ia = 无慢动作（两段/单段）。返回 (filter_complex, 输出总时长秒)。"""
    fade, fps_out = 0.1, 30.0
    n_pipe = n + round(freeze_s * fps_in)
    ranges = []                      # (是否慢动作, 起帧, 止帧)——半开区间
    if ia > 0:
        ranges.append((False, 0, ia))
    if ib > ia:
        ranges.append((True, ia, ib))
    if n_pipe > ib:
        ranges.append((False, ib, n_pipe))
    vparts, vl, total = [], [], 0.0
    for k, (is_sm, a, b) in enumerate(ranges):
        dur = (b - a) / fps_in
        if is_sm:
            dur /= speed
            mi = round(fps_out / speed)   # 0.3x → 100fps 插值后 setpts 恰 30
            seg = (f"[0:v]trim=start_frame={a}:end_frame={b},"
                   f"setpts=PTS-STARTPTS,minterpolate=fps={mi}"
                   f":me_mode=bidir:mc_mode=aobmc:vsbmc=1,"
                   f"setpts={1.0 / speed:.6f}*PTS")
        else:
            seg = (f"[0:v]trim=start_frame={a}:end_frame={b},"
                   f"setpts=PTS-STARTPTS")
        label = "[vout]" if len(ranges) == 1 else f"[v{k}]"
        vparts.append(f"{seg},fps={fps_out:.0f},format=yuv420p{label}")
        vl.append(label)
        total += dur
    if len(ranges) > 1:
        vparts.append("".join(vl) + f"concat=n={len(ranges)}:v=1:a=0[vout]")

    flat_n = sum(1 for is_sm, *_ in ranges if not is_sm)
    aparts = []
    if flat_n:
        a0 = (f"[1:a]atrim=start={clip_start:.3f}:end={clip_end:.3f},"
              f"asetpts=PTS-STARTPTS,aresample=44100,"
              f"aformat=channel_layouts=stereo")
        if flat_n > 1:
            # asplit 输出必须显式贴标签，否则消费链的输入标签匹配不到
            # 任何流（ffmpeg 7.1 实测：被当流选择器解析 → 绑定失败）
            a0 += (f",asplit={flat_n}"
                   + "".join(f"[s{i}]" for i in range(flat_n)))
        aparts.append(a0)
    fi, prev_sm = 0, False
    for k, (is_sm, a, b) in enumerate(ranges):
        dur = (b - a) / fps_in
        label = "[aout]" if len(ranges) == 1 else f"[a{k}]"
        if is_sm:
            # 慢动作段静音（0.3x 环境原声=低频轰鸣，留白制造屏息感，spec ③）
            aparts.append(f"anullsrc=r=44100:cl=stereo,"
                          f"atrim=end={dur / speed:.3f}{label}")
            prev_sm = True
            continue
        chain = (f"atrim=end={dur:.3f}" if a == 0
                 else f"atrim=start={a / fps_in:.3f}")
        if prev_sm:
            chain += f",afade=t=in:d={fade}"
        if k + 1 < len(ranges) and ranges[k + 1][0]:
            chain += f",afade=t=out:st={max(0.0, dur - fade):.3f}:d={fade}"
        if k == len(ranges) - 1 and freeze_s > 0:
            chain += f",apad=pad_dur={freeze_s:.3f}"
        if flat_n > 1:
            aparts.append(f"[s{fi}]{chain}{label}")
            fi += 1
        else:
            aparts[0] += f",{chain}{label}"   # 单 flat 段：接预处理链尾，忌孤儿链
        prev_sm = False
    if len(ranges) > 1:
        aparts.append("".join(f"[a{k}]" for k in range(len(ranges)))
                      + f"concat=n={len(ranges)}:v=0:a=1[aout]")
    return ";".join(vparts + aparts), total


def render_clip(video_path: str, g: GoalCam, centers: list[float],
                slowmo: tuple[float, float] | None, out: str,
                cfg: RenderConfig = CFG, freeze_s: float = 0.0) -> str:
    """单球渲染：cv2 逐帧读 → 浮点裁剪 → rawvideo 管道喂 ffmpeg 三分时间线
    → 592×1280@30 段 MP4。返回 out；失败抛 RuntimeError（上层跳过该球）。"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV 打不开视频：{video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(g.start * fps)))
    ia = ib = 0
    if slowmo:
        ia = round((slowmo[0] - g.start) * fps)
        ib = round((slowmo[1] - g.start) * fps)
        if ib - ia < max(2, round(cfg.slowmo_min * fps)):
            ia = ib = 0                       # 截短到不足下限 → 整段常速
    n_src = len(centers)
    fz = round(freeze_s * fps)
    fc, total = _timeline_filter(ia, ib, n_src, fps, cfg.slowmo_speed,
                                 freeze_s, g.start, g.end)
    log = out + ".log"
    cmd = [_ffmpeg_exe(), "-y",
           "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{cfg.out_w}x{cfg.out_h}", "-r", f"{fps:.6f}",
           "-i", "pipe:0", "-i", video_path,
           "-filter_complex", fc,
           "-map", "[vout]", "-map", "[aout]",
           *_V_ENC, *_A_ENC, "-t", f"{total:.3f}", out]
    with open(log, "wb") as lf:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=lf)
    try:
        cw = crop_width(g.frame_h, cfg)
        last = None
        for i in range(n_src):
            ok, frame = cap.read()
            if not ok:
                break                          # 源提前断流：已喂的照常出片
            last = _crop_frame(frame, centers[i], cw, cfg)
            proc.stdin.write(last.tobytes())
        if last is None:
            raise RuntimeError(f"读帧不足：{video_path} @ {g.start:.2f}s")
        for j in range(fz):                    # 冻结帧线性淡出到黑
            a = 1.0 - (j + 1) / max(fz, 1)
            proc.stdin.write((last.astype(np.float32) * a)
                             .astype(np.uint8).tobytes())
    except OSError:
        pass  # ffmpeg 启动即死（坏 filtergraph）→ 管道断流；真实原因在 rc!=0 的 stderr
    finally:
        cap.release()
        try:
            proc.stdin.close()                 # 对端已死时 close 的 flush 也会抛
        except OSError:
            pass
        rc = proc.wait()
    if rc != 0:
        with open(log, "rb") as lf:
            tail = lf.read()[-600:].decode("utf-8", "replace")
        raise RuntimeError(f"render_clip ffmpeg 失败（{out}）：{tail}")
    os.remove(log)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_vrender.py -v`
Expected: PASS（慢动作/直通/冻结三个渲染用例各数秒）

- [ ] **Step 5: 全量回归 + Commit**

```bash
git add bbh/vrender.py tests/test_vrender.py
git commit -m "feat: vrender.render_clip——warpAffine 浮点裁剪管道+三分时间线（minterpolate 慢动作/静音淡入出）+末球冻结淡出"
```

---

### Task 5: 白闪微段 + build_vertical 组装 + 响度统一

**Files:**
- Modify: `bbh/vrender.py`（追加）
- Modify: `tests/test_vrender.py`（追加）

**Interfaces:**
- Consumes: Task 1-4 全部；`models.load_events/time_key`、`shooter.load_shooters/_seg_for_t`、`detector._scaled_rect`、`calibration.load_segments_dir`（Task 3）、`editor.concat_clips/safe_filename`。
- Produces:
  - `render_flash(out: str, cfg: RenderConfig = CFG) -> str`
  - `normalize_audio(path: str, cfg: RenderConfig = CFG) -> None`
  - `build_vertical(video_path: str, goals: list[tuple[float, float, float]], work_dir: str, out_name: str = "highlight.mp4", cfg: RenderConfig = CFG) -> str`——`goals=[(t, before, after)]` 与 `app.generate_highlight` 收集的 settings 同构；返回成品路径；空 goals 抛 `ValueError`；全部球失败抛 `RuntimeError`，单球失败跳过。

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_vrender.py`：

```python
import os

from bbh.models import GoalEvent, save_events, time_key
from bbh.vrender import build_vertical


def _work(tmp_path, sample_video, spots):
    """tmp work 目录：events.json + calibration.json + shooters.json。
    spots: {t: (release_xy, release_t, spot)}"""
    wd = str(tmp_path / "work")
    os.makedirs(wd)
    save_events(os.path.join(wd, "events.json"), str(sample_video), 6.0,
                [GoalEvent(t=t, confidence=0.9, status="high")
                 for t in spots], None)
    Calibration(hoop_rect=(300, 140, 80, 40), hsv_low=(0, 80, 120),
                hsv_high=(25, 255, 255), frame_size=(640, 360)).save(
                    os.path.join(wd, "calibration.json"))
    traces = {}
    for t, (xy, rt, spot) in spots.items():
        traces[time_key(t)] = {"status": "high", "release_xy": xy,
                               "release_t": rt, "spot": spot}
    with open(os.path.join(wd, "shooters.json"), "w", encoding="utf-8") as f:
        _json.dump({"traces": traces}, f)
    return wd


class TestBuildVertical:
    def test_两球_白闪_规格(self, sample_video_snd, tmp_path):
        wd = _work(tmp_path, sample_video_snd,
                   {3.0: ([280, 200], 1.9, "ok"), 5.0: ([200, 210], 3.9, "bad")})
        out = build_vertical(str(sample_video_snd),
                             [(3.0, 1.5, 1.5), (5.0, 1.0, 0.8)], wd)
        assert os.path.exists(out)
        w, h, fps, n = _meta(out)
        assert (w, h) == (592, 1280) and 29 <= fps <= 31
        # 球1≈4.4s + 白闪0.067 + 球2≈(0.65+2.0+0.55+冻结0.5)=3.7 → ≈8.2s
        assert 210 <= n <= 280
        cap = cv2.VideoCapture(out)
        white = 0
        while True:
            ok, fr = cap.read()
            if not ok:
                break
            if float(fr.mean()) > 250:
                white += 1
        cap.release()
        assert 1 <= white <= 3                   # 白闪 2 帧（±重采样）

    def test_单球_无白闪(self, sample_video_snd, tmp_path):
        wd = _work(tmp_path, sample_video_snd, {3.0: ([280, 200], 1.9, "ok")})
        out = build_vertical(str(sample_video_snd), [(3.0, 1.5, 1.5)], wd)
        cap = cv2.VideoCapture(out)
        white = sum(1 for ok, fr in iter(cap.read, (False, None))
                    if ok and float(fr.mean()) > 250)
        cap.release()
        assert white == 0

    def test_坏球跳过_好球照出(self, sample_video_snd, tmp_path, capsys):
        wd = _work(tmp_path, sample_video_snd, {3.0: ([280, 200], 1.9, "ok")})
        out = build_vertical(str(sample_video_snd),
                             [(3.0, 1.5, 1.5), (100.0, 5.0, 2.0)], wd)
        assert os.path.exists(out)               # t=100 超片长，跳过不炸
        _, _, _, n = _meta(out)
        assert n < 200                           # 只有球 1
        assert "跳过" in capsys.readouterr().out

    def test_空goals报错(self, sample_video_snd, tmp_path):
        wd = _work(tmp_path, sample_video_snd, {})
        with pytest.raises(ValueError):
            build_vertical(str(sample_video_snd), [], wd)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_vrender.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_vertical'`

- [ ] **Step 3: Write minimal implementation**

追加到 `bbh/vrender.py`（顶部 import 区补：`import glob`；`from . import calibration as _calib`；`from .detector import _scaled_rect`；`from .editor import concat_clips, safe_filename`；`from .models import load_events, time_key`；`from .shooter import _seg_for_t, load_shooters`）：

```python
def render_flash(out: str, cfg: RenderConfig = CFG) -> str:
    """白闪微段：flash_frames 帧白场+静音，编码参数与正片逐字一致
    （流拷贝 concat 前提）。参考片同款片间衔接（spec ④）。"""
    d = cfg.flash_frames / 30.0
    _run([_ffmpeg_exe(), "-y",
          "-f", "lavfi", "-i",
          f"color=white:s={cfg.out_w}x{cfg.out_h}:r=30:d={d:.4f}",
          "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
          "-map", "0:v", "-map", "1:a", "-shortest", "-t", f"{d:.4f}",
          *_V_ENC, *_A_ENC, out])
    return out


def normalize_audio(path: str, cfg: RenderConfig = CFG) -> None:
    """整条响度统一 −14 LUFS（抖音标准）。视频流拷贝，仅音频重编码。"""
    tmp = path + ".norm.mp4"
    _run([_ffmpeg_exe(), "-y", "-i", path, "-c:v", "copy",
          "-af", f"loudnorm=I={cfg.loudnorm_i}:TP=-1.5:LRA=11",
          *_A_ENC, tmp])
    os.replace(tmp, path)


def _goal_cam(t, before, after, duration, segs, traces, fw, fh, fps) -> GoalCam:
    """表格行 → GoalCam：出手点/出手时刻取 shooters.json（spot=bad 或缺失
    → 无 hold 整段锁筐），筐 x 取所属标定段 hoop_rect 缩放后中心。"""
    tr = traces.get(time_key(t)) or {}
    xy = tr.get("release_xy")
    shooter_x = float(xy[0]) if (xy and tr.get("spot") != "bad") else None
    hx, _hy, hw, _hh = _scaled_rect(_seg_for_t(segs, t).calib, fw, fh)
    return GoalCam(t=t, start=max(0.0, t - before), end=min(duration, t + after),
                   shooter_x=shooter_x, release_t=tr.get("release_t"),
                   hoop_x=hx + hw / 2.0, frame_w=fw, frame_h=fh, fps=fps)


def build_vertical(video_path: str, goals: list[tuple[float, float, float]],
                   work_dir: str, out_name: str = "highlight.mp4",
                   cfg: RenderConfig = CFG) -> str:
    """[(进球时刻, 前, 后)] → 竖屏运镜集锦 MP4，返回成品路径。单球失败
    跳过并打印汇总（spec：集锦不因一球全废）；全部失败才抛。"""
    if not goals:
        raise ValueError("没有选中任何片段")
    data = load_events(os.path.join(work_dir, "events.json"))
    segs = _calib.load_segments_dir(work_dir)
    if not segs:
        raise RuntimeError("缺标定（work 目录无可用 calibration.json）"
                           "——竖屏运镜需要筐几何")
    traces = (load_shooters(work_dir) or {}).get("traces", {})
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV 打不开视频：{video_path}")
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.release()

    clips_dir = os.path.join(work_dir, "vclips")
    os.makedirs(clips_dir, exist_ok=True)
    for old in glob.glob(os.path.join(clips_dir, "*.mp4")):
        os.remove(old)
    flash = (os.path.join(clips_dir, "flash.mp4")
             if cfg.transition == "white_flash" else None)
    if flash:
        render_flash(flash, cfg)

    files, errs = [], []
    for i, (t, before, after) in enumerate(goals):
        g = _goal_cam(t, before, after, data["duration"], segs, traces,
                      fw, fh, fps)
        p = os.path.join(clips_dir, f"{i:03d}_{int(t):06d}.mp4")
        try:
            render_clip(video_path, g, plan_camera(g, cfg),
                        slowmo_window(g, cfg), p, cfg,
                        freeze_s=(cfg.end_freeze_s
                                  if i == len(goals) - 1 else 0.0))
            files.append(p)
        except Exception as ex:                 # noqa: BLE001——单球不拖垮整片
            errs.append(f"{time_key(t)}: {ex}")
    if not files:
        raise RuntimeError("全部片段渲染失败：" + "；".join(errs))
    seq = []
    for j, f in enumerate(files):
        if j and flash:
            seq.append(flash)
        seq.append(f)
    out = os.path.join(work_dir, safe_filename(
        os.path.splitext(out_name)[0]) + ".mp4")
    if len(seq) == 1:
        os.replace(seq[0], out)
    else:
        concat_clips(seq, out)                  # 参数逐字一致 → 流拷贝秒拼
    normalize_audio(out, cfg)
    if errs:
        print("⚠ 跳过片段：" + "；".join(errs))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_vrender.py -v`
Expected: PASS

- [ ] **Step 5: 全量回归 + Commit**

```bash
git add bbh/vrender.py tests/test_vrender.py
git commit -m "feat: vrender.build_vertical——白闪微段+逐球渲染跳坏+流拷贝拼装+loudnorm 收尾"
```

---

### Task 6: app.generate_highlight 路由竖屏（开关可回退）

**Files:**
- Modify: `bbh/app.py`（`generate_highlight` 约 1988-2011 行；顶部 import 行 `from . import appearance, calibration, editor, pipeline, shooter, suggest` 加 `vrender`）
- Test: `tests/test_vrender.py`（追加）

**Interfaces:**
- Consumes: Task 5 `build_vertical(video_path, goals, work_dir, out_name)`；现有 `editor.build_highlight`。
- Produces: 模块级 `app.VERTICAL_HIGHLIGHT = True`。`generate_highlight` 行为零变化（签名、gr.Error 分支、返回路径字符串）。

- [ ] **Step 1: Write the failing test**

追加到 `tests/test_vrender.py`：

```python
class TestAppRouting:
    def test_开关开_走竖屏管线(self, monkeypatch, tmp_path):
        import bbh.app as app
        import bbh.vrender as vrender
        got = {}
        monkeypatch.setattr(app, "VERTICAL_HIGHLIGHT", True)
        monkeypatch.setattr(vrender, "build_vertical",
                            lambda vp, goals, wd, name: got.update(
                                goals=goals, name=name) or str(tmp_path / "v.mp4"))
        rows = [[True, "0:03.0", "high", 0.9, 1.5, 1.5, ""]]
        out = app.generate_highlight("vid.mp4", rows)
        assert got["goals"] == [(3.0, 1.5, 1.5)] and got["name"] == "highlight.mp4"
        assert out.endswith("v.mp4")

    def test_开关关_回旧横屏路径(self, monkeypatch, tmp_path):
        import bbh.app as app
        seen = {}
        monkeypatch.setattr(app, "VERTICAL_HIGHLIGHT", False)
        monkeypatch.setattr(app, "load_events",   # app 命名空间里的引用（app.py:27）
                            lambda p: {"duration": 60.0, "events": []})
        monkeypatch.setattr(app.editor, "build_highlight",
                            lambda src, clips, wd, name="highlight.mp4":
                            seen.update(clips=clips) or str(tmp_path / "h.mp4"))
        rows = [[True, "0:03.0", "high", 0.9, 1.5, 1.5, ""]]
        out = app.generate_highlight("vid.mp4", rows)
        assert seen["clips"] == [(1.5, 4.5)]
        assert out.endswith("h.mp4")
```

（`generate_highlight` 读 events 用的是 app.py:27 导入的 `load_events`，patch 该命名空间引用即可；`_work_dir_for("vid.mp4")` 只是字符串拼接，不落盘。）

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_vrender.py::TestAppRouting -v`
Expected: FAIL — `AttributeError: app has no attribute 'VERTICAL_HIGHLIGHT'`（开关开用例先炸）

- [ ] **Step 3: Write minimal implementation**

`bbh/app.py`：顶部加

```python
VERTICAL_HIGHLIGHT = True   # 竖屏运镜集锦（2026-09-15 spec）；False=旧横屏直切回退位
```

`generate_highlight` 在 `settings` 收集完、空校验后、`data = load_events(...)` 之前插：

```python
    if VERTICAL_HIGHLIGHT:
        return vrender.build_vertical(video_path, settings,
                                      _work_dir_for(video_path), out_name)
```

（`out_name` 的计算上移到该分支之前，两分支共用；横屏分支原样不动。）

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_vrender.py tests/test_app.py -v`
Expected: PASS

- [ ] **Step 5: 全量回归 + Commit**

```bash
git add bbh/app.py tests/test_vrender.py
git commit -m "feat: 生成集锦路由竖屏运镜管线——VERTICAL_HIGHLIGHT 开关，False 一键回旧横屏"
```

---

### Task 7: 真源冒烟 + README

**Files:**
- Modify: `README.md`（集锦章节补竖屏运镜说明）
- 无新测试（真源验收是人工门，产物不进 git）

**Interfaces:**
- Consumes: Task 5 `build_vertical`；真源 `work/2026-09-11_3/`（events.json/calibration.json/shooters.json 齐备；源视频 `D:\claude code\1视频获取\归档\2026-09-11_3.mp4`，1920×1080@24fps HEVC，2h04m）。
- Produces: `work/2026-09-11_3/smoke.mp4`（冒烟产物，供用户手机实机看）。

- [ ] **Step 1: 渲 3 球冒烟**

Run（仓库根）:

```bash
".venv/Scripts/python.exe" -c "
from bbh.models import load_events
from bbh import vrender
d = load_events(r'work/2026-09-11_3/events.json')
goals = [(e.t, 5.0, 2.0) for e in d['events'] if e.status == 'high'][:3]
print(vrender.build_vertical(d['video'], goals, r'work/2026-09-11_3', 'smoke.mp4'))
"
```

Expected: 打印输出路径，无 `⚠ 跳过片段`（有则逐条看原因）。预计 1-2 分钟。

- [ ] **Step 2: 规格与时长校验**

```bash
".venv/Scripts/python.exe" -c "
import cv2
cap = cv2.VideoCapture(r'work/2026-09-11_3/smoke.mp4')
print(cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT),
      cap.get(cv2.CAP_PROP_FPS), cap.get(cv2.CAP_PROP_FRAME_COUNT))
cap.release()
"
```

Expected: `592.0 1280.0 30.0`，帧数 ≈ (7.0+1.4)×30×3 ≈ 750±80（末球另有 0.5s 冻结）。

- [ ] **Step 3: 用户实机验收**

把 `work/2026-09-11_3/smoke.mp4` 发手机播放，对照参考片检查四件事：全屏满屏无黑边 / 跟人→移筐的缓动 / 进球瞬间慢动作顺滑无果冻 / 白闪衔接。**不通过则按 spec 的退路调参重渲（慢动作伪影→该段 0.5x 或 me_mode=bilat；运镜太快→peak_frac/pan_max），不通过不进下一步。**

- [ ] **Step 4: README 补章节**

`README.md` 找到集锦/剪辑相关章节（`grep -n 集锦 README.md`），追加：

```markdown
### 竖屏运镜集锦（2026-09-15）

「生成集锦」产出抖音向竖屏成品：592×1280 全屏画幅（裁剪窗吃满源高、仅横向
运镜）、跟出手人→缓动平移→锁筐的关键帧运镜（出手点/筐几何来自 shooters.json
与标定，零新增检测）、进球瞬间 0.3x minterpolate 慢动作（静音留白）、片间
白闪、末球定帧淡出、整条 −14 LUFS。参数集中在 `bbh/vrender.py` 的
`RenderConfig`；`bbh/app.py` 的 `VERTICAL_HIGHLIGHT = False` 一键回退旧横屏
直切。设计：`docs/superpowers/specs/2026-09-15-vertical-highlight-design.md`。
```

- [ ] **Step 5: 全量回归 + Commit**

```bash
".venv/Scripts/python.exe" -m pytest tests/ -q
git add README.md
git commit -m "docs: README 补竖屏运镜集锦章节（冒烟验收通过）"
```

---

## Self-Review 记录

- **Spec 覆盖**：①导出规格（Task 1 crop_width/Task 4 _V_ENC/Task 5 loudnorm）②时间线+运镜（Task 1/2）③慢动作（Task 2 窗口/Task 4 插值链）④转场组装（Task 5）⑤接入（Task 3/6）⑥兜底（Task 1 无出手人、Task 2 窗下限、Task 5 跳坏球）⑦测试（各任务内联+Task 7 冒烟）——全覆盖。
- **类型一致性**：`GoalCam` 字段、`build_vertical(goals)` 元组序、`_V_ENC/_A_ENC`、`load_segments_dir` 名字跨任务一致；`render_clip` 的 `centers` 长度=源帧数、慢动作窗转帧号半开区间在 Task 4 内闭环。
- **已知让步**（executor 不用修）：`fps=30` 滤镜对 24fps 源是复制补帧（spec ① 既定）；管道若在片中断流（读帧不足）按已喂帧出片。
- **自审修订记录**：初稿 `_timeline_filter` 音频段在 flat_n≤1 时会生成无输入的孤儿滤镜链（ffmpeg 拒绝）、flat_n=0 时 [1:a] 悬空——已改预处理链尾拼接/跳过源音频，并补「贴片段头/贴片段尾/整段慢动作」三个边界用例钉死；Task 6 测试 patch 目标核对为 `app.load_events`（app.py:27 命名空间导入）。
