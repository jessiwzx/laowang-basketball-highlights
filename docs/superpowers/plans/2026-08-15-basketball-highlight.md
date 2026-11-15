# 篮球进球自动集锦工具 实现计划

> **📌 历史文档（过程存档）**：本计划任务已全部实施完毕（骨架 → 数据模型 →
> 判定状态机 → 标定 → 流式检测 → 剪辑 → 管线 → 验收工具 → Gradio 界面），
> 仅作过程记录。系统此后经历 v3~v9 判定逻辑演进与单页 UI 重设计，
> 现行说明见 `README.md`。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 固定机位篮球视频自动检测进球，人工复核后用 ffmpeg 合成集锦 MP4。

**Architecture:** 五模块单向流水：标定(calibration.json) → 检测(流式颜色分割，筐口 ROI) → 判定(纯函数状态机 → events.json) → 剪辑(ffmpeg) → Gradio 界面串起全流程。检测/判定分离，events.json 持久中间层。

**Tech Stack:** Python 3.11+ / OpenCV / numpy / Gradio 5 / ffmpeg（外部二进制）/ pytest

**设计文档:** `docs/superpowers/specs/2026-08-15-basketball-highlight-design.md`

---

## 文件结构

```
requirements.txt          依赖清单
.gitignore
bbh/
  __init__.py
  models.py               Calibration / BallPos / GoalEvent / ClipSettings + JSON 读写
  calibration.py          抽帧、点击→HSV 范围、筐口矩形、预览叠加图
  goal_decider.py         纯函数进球判定状态机 + 置信度分层
  detector.py             流式视频读取 + ROI 颜色分割 → BallPos 序列
  editor.py               片段规划（合并/越界裁剪，纯函数）+ ffmpeg 切片/拼接
  pipeline.py             检测编排：检查点续跑、events.json 读写、检出率警告
  app.py                  Gradio 三页界面（标定/分析/集锦）
tools/
  evaluate.py             集成验收：检测结果 vs 人工标注 → 漏检/误报/待确认报告
tests/
  conftest.py             合成视频 fixture（橙色球穿越筐口的 mp4）
  test_models.py
  test_calibration.py
  test_goal_decider.py
  test_detector.py
  test_editor.py
  test_pipeline.py
  test_evaluate.py
run_app.bat               双击启动界面
README.md                 使用说明
```

约定：
- 所有时间单位为秒（float），坐标为像素（帧的原始分辨率）
- HSV 使用 OpenCV 范围：H 0-179, S/V 0-255
- ⚠️ 本机（OpenCV 5.0.0.93 / Windows）实测：`cv2.VideoCapture`/`VideoWriter` 支持中文路径，但 **`cv2.imread`/`cv2.imwrite` 不支持**（静默返回 None/False）。凡图片落盘/读盘且路径可能含中文，必须用 `cv2.imdecode(np.fromfile(p, np.uint8), ...)` / `cv2.imencode(ext, img)[1].tofile(p)` 替代。界面内传递帧一律用 numpy 数组，不落盘
- 工作目录产物：`work/<视频名>/events.json`、`work/<视频名>/checkpoint.json`、`work/<视频名>/clips/`、`work/<视频名>/highlight.mp4`

---

### Task 1: 项目骨架与环境

**Files:**
- Create: `requirements.txt`, `.gitignore`, `bbh/__init__.py`, `tests/conftest.py`（空壳，后续任务填充）

- [ ] **Step 1: 创建骨架文件**

`requirements.txt`:
```
opencv-python>=4.10
numpy>=1.26
gradio>=5.0,<6
pytest>=8.0
```

`.gitignore`:
```
__pycache__/
.venv/
work/
*.mp4
!tests/data/*.mp4
calibration.json
```

`bbh/__init__.py`: 空文件。

- [ ] **Step 2: 建虚拟环境并安装依赖**

```bash
cd "/d/claude code/视频剪辑"
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
```

Expected: 全部安装成功。

- [ ] **Step 3: 确认 ffmpeg 可用（不可用则安装）**

```bash
ffmpeg -version | head -1
```

Expected: 输出版本号。若命令不存在：`winget install Gyan.FFmpeg` 后重开终端再试（或在项目根放 ffmpeg.exe）。

- [ ] **Step 4: 确认 pytest 可跑**

```bash
.venv/Scripts/python -m pytest tests/ -v
```

Expected: `no tests ran`（exit code 5），无报错。

- [ ] **Step 5: Commit**

```bash
git add requirements.txt .gitignore bbh/ tests/
git commit -m "chore: 项目骨架与依赖"
```

---

### ~~Task 2: Windows 中文路径兼容工具~~（已废弃）

> 执行时实测：OpenCV 5.0.0.93 在 Windows 上 `VideoCapture`/`VideoWriter` 原生支持非 ASCII 路径（中文路径实测通过），但 `imread`/`imwrite` 不支持（见下方约定）；且 8.3 短路径方案对"视频剪辑"（GBK 下恰好 8 字节）无效——Windows 不为其生成短名。模块已删除，视频 IO 直接使用原始路径。

---

### Task 3: 数据模型与 JSON 读写

**Files:**
- Create: `bbh/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: 写失败测试**

`tests/test_models.py`:
```python
import json
from bbh.models import Calibration, BallPos, GoalEvent, ClipSettings


def test_calibration_roundtrip(tmp_path):
    calib = Calibration(hoop_rect=(300, 140, 80, 40),
                        hsv_low=(0, 80, 120), hsv_high=(25, 255, 255),
                        frame_size=(640, 360))
    p = tmp_path / "calibration.json"
    calib.save(p)
    loaded = Calibration.load(p)
    assert loaded.hoop_rect == (300, 140, 80, 40)
    assert loaded.hsv_low == (0, 80, 120)
    assert loaded.frame_size == (640, 360)


def test_goal_event_status_layering():
    assert GoalEvent(t=10.0, confidence=0.9, status="high").status == "high"
    assert GoalEvent(t=20.0, confidence=0.55, status="review").status == "review"


def test_clip_settings_default():
    s = ClipSettings()
    assert (s.before, s.after) == (15.0, 5.0)


def test_events_json_roundtrip(tmp_path):
    from bbh.models import save_events, load_events
    p = tmp_path / "events.json"
    save_events(p, video="a.mp4", duration=3600.0,
                events=[GoalEvent(t=100.0, confidence=0.9, status="high")],
                warning=None)
    data = load_events(p)
    assert data["duration"] == 3600.0
    assert data["events"][0].t == 100.0
    assert data["warning"] is None
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/Scripts/python -m pytest tests/test_models.py -v
```

Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

`bbh/models.py`:
```python
"""数据模型与 JSON 读写。所有时间单位为秒。"""
import json
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class Calibration:
    hoop_rect: tuple  # (x, y, w, h) 筐口矩形，标定视频分辨率下的像素坐标
    hsv_low: tuple    # 球颜色下界 (H, S, V)，OpenCV 范围
    hsv_high: tuple   # 球颜色上界
    frame_size: tuple  # 标定时的视频分辨率 (w, h)

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path) -> "Calibration":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return cls(hoop_rect=tuple(d["hoop_rect"]), hsv_low=tuple(d["hsv_low"]),
                   hsv_high=tuple(d["hsv_high"]), frame_size=tuple(d["frame_size"]))


@dataclass
class BallPos:
    t: float     # 帧时间（秒）
    x: float     # 全图坐标
    y: float
    area: float  # 分割出的球像素面积


@dataclass
class GoalEvent:
    t: float          # 进球时刻（下穿筐口平面的时间）
    confidence: float  # 0-1
    status: str       # "high" | "review"

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(t=d["t"], confidence=d["confidence"], status=d["status"])


@dataclass
class ClipSettings:
    before: float = 15.0
    after: float = 5.0


def save_events(path, video: str, duration: float, events: list[GoalEvent],
                warning: Optional[str]):
    data = {"video": video, "duration": duration,
            "events": [e.to_dict() for e in events], "warning": warning}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_events(path) -> dict:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return {"video": d["video"], "duration": d["duration"],
            "events": [GoalEvent.from_dict(e) for e in d["events"]],
            "warning": d.get("warning")}
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/Scripts/python -m pytest tests/test_models.py -v
```

Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add bbh/models.py tests/test_models.py
git commit -m "feat: 数据模型与 events.json 读写"
```

---

### Task 4: 进球判定状态机（核心逻辑，TDD 主战场）

**Files:**
- Create: `bbh/goal_decider.py`
- Test: `tests/test_goal_decider.py`

规则（对照设计文档第 8 节）：
- 筐口平面 `rim_y = hoop_y + 0.35 * hoop_h`；筐身条带 `|y - rim_y| < 0.25 * hoop_h` 内的点视为篮筐自身，忽略
- 下穿：先有点在筐口上方（y < rim_y - 条带），随后 ≤0.7s 内有点在下方（y > rim_y + 条带），且两点 x 都落在筐口矩形横向范围内（左右各放宽 20%）
- 置信度：基础 0.3；下穿前 1.0s 内有 ≥2 个上方点 +0.2；下穿后 1.0s 内有 ≥2 个下方点 +0.2；落点 x 距筐心 < 0.3*筐宽 +0.15，否则在矩形内 +0.05
- 分层：≥0.7 high；0.5-0.7 review；<0.5 丢弃
- 冷却：距上一个已接受事件 <3.0s 的候选丢弃

- [ ] **Step 1: 写失败测试**

`tests/test_goal_decider.py`:
```python
import pytest
from bbh.models import BallPos, Calibration
from bbh.goal_decider import decide_goals

# 筐口矩形 (300,140,80,40) → rim_y = 140 + 0.35*40 = 154，筐心 x=340
CALIB = Calibration(hoop_rect=(300, 140, 80, 40),
                    hsv_low=(0, 0, 0), hsv_high=(179, 255, 255),
                    frame_size=(640, 360))


def pts(seq):
    """[(t, x, y)] → [BallPos]"""
    return [BallPos(t=t, x=x, y=y, area=100) for t, x, y in seq]


class Test标准进球:
    def test_完整轨迹_高置信(self):
        positions = pts([
            (10.0, 340, 100), (10.1, 340, 120), (10.2, 340, 145),
            (10.3, 340, 165), (10.4, 340, 190), (10.5, 340, 215),
        ])
        events = decide_goals(positions, CALIB)
        assert len(events) == 1
        assert events[0].status == "high"
        assert events[0].t == pytest.approx(10.3, abs=0.01)

    def test_只检到交叉前后各一点_进待确认(self):
        positions = pts([(20.0, 340, 100), (20.2, 340, 190)])
        events = decide_goals(positions, CALIB)
        assert len(events) == 1
        assert events[0].status == "review"


class Test不触发:
    def test_打铁弹框_向上穿过(self):
        positions = pts([
            (30.0, 340, 190), (30.1, 340, 165), (30.2, 340, 145), (30.3, 340, 110),
        ])
        assert decide_goals(positions, CALIB) == []

    def test_空气球_从旁侧飞过不进筐口横向范围(self):
        positions = pts([
            (40.0, 100, 100), (40.1, 100, 145), (40.2, 100, 190), (40.3, 100, 250),
        ])
        assert decide_goals(positions, CALIB) == []

    def test_运球经过_无下穿(self):
        positions = pts([(50.0 + i * 0.1, 340, 190 + i * 5) for i in range(6)])
        assert decide_goals(positions, CALIB) == []

    def test_筐身条带内的点被忽略(self):
        # 全部点都在 |y-154|<10 条带内 → 无上下方点，无事件
        positions = pts([(60.0, 340, 150), (60.1, 340, 156), (60.2, 340, 158)])
        assert decide_goals(positions, CALIB) == []

    def test_交叉间隔超过07秒_不算同一次下穿(self):
        positions = pts([(70.0, 340, 100), (70.9, 340, 190)])
        assert decide_goals(positions, CALIB) == []


class Test冷却合并:
    def test_三秒内连续两次下穿只记一次(self):
        positions = pts([
            (80.0, 340, 100), (80.2, 340, 190),          # 第一次下穿
            (80.5, 340, 100), (80.7, 340, 190),          # 冷却期内，丢弃
            (85.0, 340, 100), (85.2, 340, 190),          # 冷却期外，第二球
        ])
        events = decide_goals(positions, CALIB)
        assert len(events) == 2
        assert events[0].t == pytest.approx(80.2, abs=0.01)
        assert events[1].t == pytest.approx(85.2, abs=0.01)

    def test_低置信事件被丢弃且不占用冷却(self):
        # 单点下穿落点在筐口边缘外 20% 宽容带内 → 置信度 0.3+0.05 < 0.5 丢弃
        positions = pts([(90.0, 300, 100), (90.2, 295, 190)])
        assert decide_goals(positions, CALIB) == []
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/Scripts/python -m pytest tests/test_goal_decider.py -v
```

Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

`bbh/goal_decider.py`:
```python
"""进球判定状态机：球位置序列 → 进球事件（纯函数，无视频 IO）。"""
from .models import BallPos, Calibration, GoalEvent

RIM_Y_RATIO = 0.35      # 筐口平面在矩形中的纵向位置
BAND_RATIO = 0.25       # 筐身条带半高（相对筐高）
MAX_CROSS_SEC = 0.7     # 上方点→下方点的最大间隔
X_MARGIN = 0.2          # 横向宽容（相对筐宽）
NEIGHBOR_SEC = 1.0      # 前后取点窗口
COOLDOWN_SEC = 3.0
HIGH_CONF = 0.7
MIN_CONF = 0.5


def decide_goals(positions: list[BallPos], calib: Calibration) -> list[GoalEvent]:
    hx, hy, hw, hh = calib.hoop_rect
    rim_y = hy + hh * RIM_Y_RATIO
    band = hh * BAND_RATIO
    x_lo, x_hi = hx - hw * X_MARGIN, hx + hw * (1 + X_MARGIN)
    cx = hx + hw / 2

    # 只保留筐口横向范围内、且不在筐身条带里的点，标注上方/下方
    classified = []  # (BallPos, side) side: "up"/"down"
    for p in positions:
        if not (x_lo <= p.x <= x_hi):
            continue
        if abs(p.y - rim_y) < band:
            continue
        classified.append((p, "up" if p.y < rim_y else "down"))

    events: list[GoalEvent] = []
    last_t = float("-inf")
    for i, (p, side) in enumerate(classified):
        if side != "up":
            continue
        # 找 ≤MAX_CROSS_SEC 内的第一个下方点
        cross = next((q for q, s in classified[i + 1:]
                      if s == "down" and q.t - p.t <= MAX_CROSS_SEC), None)
        if cross is None:
            continue
        if cross.t - last_t < COOLDOWN_SEC:
            continue

        above_pts = [q for q, s in classified
                     if s == "up" and p.t - NEIGHBOR_SEC <= q.t <= p.t]
        below_pts = [q for q, s in classified
                     if s == "down" and cross.t <= q.t <= cross.t + NEIGHBOR_SEC]
        conf = 0.3
        conf += 0.2 if len(above_pts) >= 2 else 0.0
        conf += 0.2 if len(below_pts) >= 2 else 0.0
        if abs(cross.x - cx) < hw * 0.3:
            conf += 0.15
        elif hx <= cross.x <= hx + hw:
            conf += 0.05

        if conf < MIN_CONF:
            continue
        last_t = cross.t
        events.append(GoalEvent(
            t=cross.t, confidence=round(conf, 2),
            status="high" if conf >= HIGH_CONF else "review"))
    return events
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/Scripts/python -m pytest tests/test_goal_decider.py -v
```

Expected: 10 PASS

注意一个实现细节：`cross.t - last_t < COOLDOWN_SEC` 判断要在低置信丢弃**之前**跳过候选还是之后？——按上面实现，候选先查冷却（`continue`），低置信事件（<MIN_CONF）不更新 `last_t`、不占用冷却，与测试 `test_低置信事件被丢弃且不占用冷却` 一致。

- [ ] **Step 5: Commit**

```bash
git add bbh/goal_decider.py tests/test_goal_decider.py
git commit -m "feat: 进球判定状态机与置信度分层"
```

---

### Task 5: 标定模块（点击→HSV、筐口矩形、预览叠加）

**Files:**
- Create: `bbh/calibration.py`
- Test: `tests/test_calibration.py`

- [ ] **Step 1: 写失败测试**

`tests/test_calibration.py`:
```python
import cv2
import numpy as np
from bbh.calibration import (hsv_range_from_clicks, hoop_rect_from_rim_points,
                             overlay_preview)

# 与 conftest 合成视频一致的"篮球橙" BGR(20,100,245)
BALL_BGR = (20, 100, 245)


def test_hsv_range_from_clicks_covers_ball_color():
    frame = np.zeros((360, 640, 3), np.uint8)
    frame[100:110, 300:310] = BALL_BGR
    low, high = hsv_range_from_clicks(frame, clicks=[(305, 105)])
    hsv = cv2.cvtColor(np.uint8([[BALL_BGR]]), cv2.COLOR_BGR2HSV)[0][0]
    assert all(low[i] <= hsv[i] <= high[i] for i in range(3))


def test_hsv_range_clamped_to_valid():
    frame = np.full((360, 640, 3), (255, 255, 255), np.uint8)
    low, high = hsv_range_from_clicks(frame, clicks=[(10, 10)])
    assert 0 <= low[0] <= high[0] <= 179
    assert 0 <= low[1] <= high[1] <= 255
    assert 0 <= low[2] <= high[2] <= 255


def test_hoop_rect_from_two_rim_clicks():
    rect = hoop_rect_from_rim_points((300, 150), (380, 158))
    x, y, w, h = rect
    assert x < 300 and x + w > 380          # 左右留边
    assert y < 154 and y + h > 154          # 覆盖筐口平面 154 附近
    assert w > 80


def test_overlay_preview_画出筐框且球区域高亮():
    frame = np.zeros((360, 640, 3), np.uint8)
    frame[100:110, 300:310] = BALL_BGR
    from bbh.models import Calibration
    calib = Calibration(hoop_rect=(300, 140, 80, 40),
                        hsv_low=(0, 80, 120), hsv_high=(25, 255, 255),
                        frame_size=(640, 360))
    out = overlay_preview(frame, calib)
    assert out.shape[:2] == frame.shape[:2]
    assert out[105, 305].tolist() != [0, 0, 0]  # 球位置被高亮
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/Scripts/python -m pytest tests/test_calibration.py -v
```

Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

`bbh/calibration.py`:
```python
"""标定：从用户点击计算球 HSV 范围与筐口矩形，生成预览叠加图。"""
import cv2
import numpy as np

from .models import Calibration

HSV_MARGIN = (4, 40, 40)      # 点击像素向四周放宽的余量
H_MARGIN_RATIO = 0.15         # 筐口矩形在点击点左右两侧的放宽比例


def hsv_range_from_clicks(frame: np.ndarray, clicks: list[tuple[int, int]]):
    """clicks: [(x, y)]（帧坐标）。返回覆盖所有点击像素±余量的 HSV 上下界。"""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    samples = [hsv[y, x] for x, y in clicks]
    low = [max(0, int(min(s[i] for s in samples)) - HSV_MARGIN[i]) for i in range(3)]
    high = [min(u, int(max(s[i] for s in samples)) + HSV_MARGIN[i])
            for i, u in enumerate((179, 255, 255))]
    return tuple(low), tuple(high)


def hoop_rect_from_rim_points(p1: tuple[float, float], p2: tuple[float, float]):
    """筐口两端点击 → 筐口矩形 (x, y, w, h)。y 取两点均值，矩形上下延伸。"""
    x1, x2 = sorted((p1[0], p2[0]))
    rim_y = (p1[1] + p2[1]) / 2
    w_span = x2 - x1
    pad = w_span * H_MARGIN_RATIO
    x = x1 - pad
    w = w_span + 2 * pad
    y = rim_y - 0.35 * w - 0.5 * 0.4 * w   # 使 rim_y 落在矩形 35% 高度处
    h = w * 0.4
    return (round(x), round(y), round(w), round(h))


def overlay_preview(frame: np.ndarray, calib: Calibration) -> np.ndarray:
    """叠加图：命中球颜色的像素染成绿色，画出筐口矩形与筐口平面线。"""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(calib.hsv_low), np.array(calib.hsv_high))
    out = frame.copy()
    out[mask > 0] = (0, 255, 0)
    x, y, w, h = calib.hoop_rect
    cv2.rectangle(out, (x, y), (x + w, y + h), (255, 0, 0), 2)
    rim_y = int(y + h * 0.35)
    cv2.line(out, (x, rim_y), (x + w, rim_y), (0, 0, 255), 2)
    return out
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/Scripts/python -m pytest tests/test_calibration.py -v
```

Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add bbh/calibration.py tests/test_calibration.py
git commit -m "feat: 标定模块（HSV 范围、筐口矩形、预览叠加）"
```

---

### Task 6: 合成视频 fixture（供 detector/pipeline/evaluate 测试）

**Files:**
- Modify: `tests/conftest.py`

- [ ] **Step 1: 实现 fixture**

`tests/conftest.py`:
```python
import cv2
import numpy as np
import pytest

from bbh.models import Calibration

# 场景：640x360，筐口矩形 (300,140,80,40)，rim_y=154
# 篮筐画成灰色横线（避免与球同色干扰）；球 BGR(20,100,245)
# 第 60~90 帧：球从 (340,100) 直线下落到 (340,220) 穿越 rim_y
W, H, FPS, N = 640, 360, 30, 180
HOOP_RECT = (300, 140, 80, 40)
BALL_BGR = (20, 100, 245)
GOAL_CROSS_FRAME = 75  # 球心过 rim_y 的帧（近似）


def _ball_xy(frame_idx):
    if 60 <= frame_idx <= 90:
        k = (frame_idx - 60) / 30.0
        return 340, int(100 + 120 * k)
    return None


def make_video(path):
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    for i in range(N):
        img = np.full((H, W, 3), (40, 44, 50), np.uint8)
        cv2.line(img, (300, 154), (380, 154), (180, 180, 180), 3)  # 灰色筐
        pos = _ball_xy(i)
        if pos:
            cv2.circle(img, pos, 8, BALL_BGR, -1)
        vw.write(img)
    vw.release()
    return path


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory):
    return make_video(str(tmp_path_factory.mktemp("vid") / "sample.mp4"))


@pytest.fixture()
def sample_calibration():
    return Calibration(hoop_rect=HOOP_RECT, hsv_low=(0, 80, 120),
                       hsv_high=(25, 255, 255), frame_size=(W, H))
```

- [ ] **Step 2: 人工冒烟验证（不开正式测试）**

```bash
.venv/Scripts/python -c "
import sys; sys.path.insert(0, 'tests')
from conftest import make_video
import cv2
p = make_video('work/_smoke.mp4')
cap = cv2.VideoCapture(p)
print('opened:', cap.isOpened(), 'frames:', int(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
"
```

Expected: `opened: True frames: 180`（若 VideoWriter 编码失败，改 fourcc 为 `avc1` 或 `XVID`+`.avi` 并同步调整 make_video 的扩展名参数）

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: 合成篮球视频 fixture"
```

---

### Task 7: 流式检测器

**Files:**
- Create: `bbh/detector.py`
- Test: `tests/test_detector.py`

- [ ] **Step 1: 写失败测试**

`tests/test_detector.py`:
```python
from bbh.detector import detect_positions, VideoOpenError
from bbh.goal_decider import decide_goals

import pytest


def test_打不开的视频报错():
    with pytest.raises(VideoOpenError):
        detect_positions("no_such_file.mp4", pytest.lazy_fixture if False else None)


def test_合成视频检测出球并判定进球(sample_video, sample_calibration):
    positions, meta = detect_positions(sample_video, sample_calibration, step=2)
    assert meta["fps"] == pytest.approx(30.0)
    assert len(positions) > 5                          # 有球检出
    assert all(p.area > 0 for p in positions)
    events = decide_goals(positions, sample_calibration)
    assert len(events) == 1
    assert events[0].status == "high"
    assert events[0].t == pytest.approx(GOAL_CROSS_FRAME / 30.0, abs=0.2)


def test_分辨率不同时自动缩放筐口矩形(sample_video, sample_calibration, tmp_path):
    """把标定 frame_size 改成 1280x720，筐口矩形应按比例缩放后仍正常工作。"""
    from bbh.models import Calibration
    calib2 = Calibration(hoop_rect=(600, 280, 160, 80),
                         hsv_low=sample_calibration.hsv_low,
                         hsv_high=sample_calibration.hsv_high,
                         frame_size=(1280, 720))
    positions, meta = detect_positions(sample_video, calib2, step=2)
    events = decide_goals(positions, sample_calibration)  # 判定仍用原分辨率标定
    assert len(events) == 1
```

注意 `test_打不开的视频报错` 里不要用 lazy_fixture 技巧，直接写成：

```python
def test_打不开的视频报错(sample_calibration):
    with pytest.raises(VideoOpenError):
        detect_positions("no_such_file.mp4", sample_calibration)
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/Scripts/python -m pytest tests/test_detector.py -v
```

Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

`bbh/detector.py`:
```python
"""流式检测：筐口 ROI 内颜色分割找球，输出位置序列（内存流，不落盘）。"""
import cv2
import numpy as np

from .models import BallPos, Calibration

STEP = 2                # 每 2 帧处理 1 帧
ROI_UP = 2.0            # ROI 上方延伸（相对筐高）
ROI_DOWN = 3.0          # ROI 下方延伸（含篮网）
ROI_SIDE = 1.0          # ROI 左右延伸
RIM_Y_RATIO = 0.35      # 与 goal_decider 保持一致
BAND_RATIO = 0.25
MIN_AREA = 9            # 球最小像素面积（3x3 起）
MAX_AREA_RATIO = 0.5    # 球最大面积（相对筐宽平方）


class VideoOpenError(Exception):
    pass


def _scaled_rect(calib: Calibration, frame_w: int, frame_h: int):
    """标定分辨率 ≠ 视频分辨率时按比例缩放筐口矩形。"""
    x, y, w, h = calib.hoop_rect
    cw, ch = calib.frame_size
    if (cw, ch) == (frame_w, frame_h):
        return x, y, w, h
    sx, sy = frame_w / cw, frame_h / ch
    return round(x * sx), round(y * sy), round(w * sx), round(h * sy)


def detect_positions(video_path: str, calib: Calibration, step: int = STEP,
                     progress_cb=None, resume_frame: int = 0):
    """返回 (positions, meta)。meta: fps/duration/frame_count/processed_frames。"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise VideoOpenError(f"无法打开视频: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))

    hx, hy, hw, hh = _scaled_rect(calib, frame_w, frame_h)
    rim_y = hy + hh * RIM_Y_RATIO
    band = hh * BAND_RATIO
    rx = max(0, int(hx - hw * ROI_SIDE))
    ry = max(0, int(hy - hh * ROI_UP))
    rw = min(frame_w - rx, int(hw * (1 + 2 * ROI_SIDE)))
    rh = min(frame_h - ry, int(hh * (ROI_UP + ROI_DOWN)))
    max_area = hw * hw * MAX_AREA_RATIO

    if resume_frame > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, resume_frame)

    positions: list[BallPos] = []
    fi = resume_frame
    while True:
        if not cap.grab():
            break
        if fi % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            roi = frame[ry:ry + rh, rx:rx + rw]
            hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(hsv, np.array(calib.hsv_low), np.array(calib.hsv_high))
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                                    cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                           cv2.CHAIN_APPROX_SIMPLE)
            best = None
            for c in contours:
                a = cv2.contourArea(c)
                if MIN_AREA <= a <= max_area:
                    if best is None or a > best[0]:
                        best = (a, c)
            if best is not None:
                a, c = best
                m = cv2.moments(c)
                x = rx + m["m10"] / m["m00"]
                y = ry + m["m01"] / m["m00"]
                if abs(y - rim_y) >= band:      # 筐身条带内的静态橙色忽略
                    positions.append(BallPos(t=fi / fps, x=x, y=y, area=a))
        fi += 1
        if progress_cb and fi % 300 == 0:
            progress_cb(fi, total)

    cap.release()
    meta = {"fps": fps, "duration": total / fps, "frame_count": total,
            "processed_frames": fi, "video_size": (frame_w, frame_h)}
    return positions, meta
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/Scripts/python -m pytest tests/test_detector.py -v
```

Expected: 3 PASS（若合成视频里灰色筐线误检，检查条带过滤；若球速导致面积/形态问题，放宽 MIN_AREA）

- [ ] **Step 5: Commit**

```bash
git add bbh/detector.py tests/test_detector.py
git commit -m "feat: 流式进球检测器（ROI 颜色分割）"
```

---

### Task 8: 剪辑模块（片段规划 + ffmpeg）

**Files:**
- Create: `bbh/editor.py`
- Test: `tests/test_editor.py`

- [ ] **Step 1: 写失败测试**

`tests/test_editor.py`:
```python
import pytest
from bbh.editor import plan_clips, cut_clip, concat_clips


class TestPlanClips纯函数:
    def test_常规切片(self):
        clips = plan_clips([(100.0, 15.0, 5.0)], duration=3600.0)
        assert clips == [(85.0, 105.0)]

    def test_越界裁剪到视频边界(self):
        clips = plan_clips([(3.0, 15.0, 5.0), (3598.0, 15.0, 5.0)], duration=3600.0)
        assert clips[0][0] == 0.0
        assert clips[-1][1] == 3600.0

    def test_重叠片段自动合并(self):
        clips = plan_clips([(100.0, 15.0, 5.0), (110.0, 15.0, 5.0)], duration=3600.0)
        assert clips == [(85.0, 115.0)]      # 85-105 与 95-115 合并

    def test_不相邻不合并(self):
        clips = plan_clips([(100.0, 15.0, 5.0), (200.0, 15.0, 5.0)], duration=3600.0)
        assert clips == [(85.0, 105.0), (185.0, 205.0)]


class TestFfmpeg:
    def test_cut_and_concat(self, sample_video, tmp_path):
        cut_clip(sample_video, 2.0, 1.0, str(tmp_path / "c0.mp4"))
        cut_clip(sample_video, 5.0, 1.0, str(tmp_path / "c1.mp4"))
        concat_clips([str(tmp_path / "c0.mp4"), str(tmp_path / "c1.mp4")],
                     str(tmp_path / "out.mp4"))
        import cv2
        cap = cv2.VideoCapture(str(tmp_path / "out.mp4"))
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        assert 45 <= frames <= 75             # 约 2 秒 @30fps，转码有容差
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/Scripts/python -m pytest tests/test_editor.py -v
```

Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

`bbh/editor.py`:
```python
"""剪辑：片段规划（纯函数）+ ffmpeg 切片/拼接。"""
import shutil
import subprocess


def _ffmpeg_exe() -> str:
    """PATH 里有 ffmpeg 用之，否则退回 imageio-ffmpeg 捆绑二进制（本机 winget 下载失败时的兜底）。"""
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def plan_clips(events_settings: list[tuple[float, float, float]],
               duration: float) -> list[tuple[float, float]]:
    """events_settings: [(进球时刻, 前 N 秒, 后 M 秒)] → 合并重叠并裁剪越界后的
    [(start, end)] 列表，按时间排序。"""
    intervals = sorted((max(0.0, t - before), min(duration, t + after))
                       for t, before, after in events_settings)
    merged: list[list[float]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def _run(cmd: list[str]):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败:\n{cmd}\n{r.stderr[-800:]}")


def cut_clip(src: str, start: float, dur: float, out: str):
    """精确切片：统一转码 H.264/AAC（保证拼接不花屏、手机可播）。"""
    _run(["ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", src, "-t", f"{dur:.3f}",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
          "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
          out])


def concat_clips(clips: list[str], out: str):
    """拼接已统一转码的片段（流复制，秒级完成）。"""
    import os
    list_file = out + ".txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for c in clips:
            f.write(f"file '{os.path.abspath(c)}'\n")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
          "-c", "copy", out])
    os.remove(list_file)


def build_highlight(src: str, clips: list[tuple[float, float]], work_dir: str):
    """plan_clips 的输出 → highlight.mp4。返回输出路径。"""
    import os
    clips_dir = os.path.join(work_dir, "clips")
    os.makedirs(clips_dir, exist_ok=True)
    files = []
    for i, (start, end) in enumerate(clips):
        p = os.path.join(clips_dir, f"{i:03d}_{int(start):06d}.mp4")
        cut_clip(src, start, end - start, p)
        files.append(p)
    out = os.path.join(work_dir, "highlight.mp4")
    if len(files) == 1:
        os.replace(files[0], out)
    else:
        concat_clips(files, out)
    return out
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/Scripts/python -m pytest tests/test_editor.py -v
```

Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add bbh/editor.py tests/test_editor.py
git commit -m "feat: 剪辑模块（片段规划与 ffmpeg 切拼）"
```

---

### Task 9: 检测管线（检查点续跑 + events.json + 检出率警告）

**Files:**
- Create: `bbh/pipeline.py`
- Test: `tests/test_pipeline.py`

- [ ] **Step 1: 写失败测试**

`tests/test_pipeline.py`:
```python
import json
import os

from bbh.pipeline import run_detection, CHECKPOINT_SEC
from bbh.models import load_events


def test_完整跑通并产出events(sample_video, sample_calibration, tmp_path):
    events_path, warning = run_detection(sample_video, sample_calibration,
                                         work_dir=str(tmp_path))
    assert warning is None
    data = load_events(events_path)
    assert data["duration"] > 0
    assert len(data["events"]) == 1
    assert data["events"][0].status == "high"
    assert os.path.exists(str(tmp_path / "checkpoint.json")) is False  # 完成后清理


def test_无检出时返回警告(sample_video, tmp_path):
    from bbh.models import Calibration
    # 黑白色阈值 → 什么都检不到
    bad = Calibration(hoop_rect=(300, 140, 80, 40), hsv_low=(0, 0, 250),
                      hsv_high=(10, 10, 255), frame_size=(640, 360))
    events_path, warning = run_detection(sample_video, bad, work_dir=str(tmp_path))
    assert warning is not None and "标定" in warning


def test_检查点写入与续跑(sample_video, sample_calibration, tmp_path, monkeypatch):
    # 把检查点间隔改成 1 秒，跑到中途（第 60 帧）抛异常 → 检查点存在
    import bbh.pipeline as pl
    orig = pl.detect_positions
    state = {"n": 0}

    def fake_detect(video, calib, step=2, progress_cb=None, resume_frame=0):
        positions, meta = orig(video, calib, step, progress_cb, resume_frame)
        state["n"] += 1
        if state["n"] == 1:
            raise KeyboardInterrupt("模拟中断")
        return positions, meta

    monkeypatch.setattr(pl, "detect_positions", fake_detect)
    monkeypatch.setattr(pl, "CHECKPOINT_SEC", 1.0)
    try:
        pl.run_detection(sample_video, sample_calibration, work_dir=str(tmp_path))
    except KeyboardInterrupt:
        pass
    assert os.path.exists(tmp_path / "checkpoint.json")


def test_已有events时跳过检测直接复用(sample_video, sample_calibration, tmp_path):
    p1, _ = run_detection(sample_video, sample_calibration, work_dir=str(tmp_path))
    mtime = os.path.getmtime(p1)
    p2, _ = run_detection(sample_video, sample_calibration, work_dir=str(tmp_path))
    assert p1 == p2 and os.path.getmtime(p2) == mtime   # 未重跑
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/Scripts/python -m pytest tests/test_pipeline.py -v
```

Expected: FAIL（ModuleNotFoundError）

- [ ] **Step 3: 实现**

`bbh/pipeline.py`:
```python
"""检测编排：检查点续跑、events.json 读写、检出率警告。"""
import json
import os
import time

from .detector import detect_positions
from .goal_decider import decide_goals
from .models import Calibration, GoalEvent, save_events

CHECKPOINT_SEC = 300.0        # 每 5 分钟写一次检查点
CHECKPOINT_FILE = "checkpoint.json"
EVENTS_FILE = "events.json"


def _load_checkpoint(work_dir: str) -> dict | None:
    p = os.path.join(work_dir, CHECKPOINT_FILE)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _save_checkpoint(work_dir: str, frame_idx: int, positions, video: str):
    os.makedirs(work_dir, exist_ok=True)
    p = os.path.join(work_dir, CHECKPOINT_FILE)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"frame_idx": frame_idx, "positions":
                   [[b.t, b.x, b.y, b.area] for b in positions], "video": video},
                  f)


def run_detection(video_path: str, calib: Calibration, work_dir: str,
                  force: bool = False) -> tuple[str, str | None]:
    """跑完检测+判定。返回 (events.json 路径, warning|None)。
    events.json 已存在且未 force 时直接复用（不重跑）。"""
    os.makedirs(work_dir, exist_ok=True)
    events_path = os.path.join(work_dir, EVENTS_FILE)
    if os.path.exists(events_path) and not force:
        return events_path, None

    ckpt = _load_checkpoint(work_dir) or {}
    resume_frame = ckpt.get("frame_idx", 0)
    positions = [type("P", (), {"t": r[0], "x": r[1], "y": r[2], "area": r[3]})
                 for r in ckpt.get("positions", [])]

    last_ckpt = time.monotonic()
    holder = {"positions": positions}

    def on_progress(frame_idx, total):
        nonlocal last_ckpt
        holder["frame_idx"] = frame_idx
        now = time.monotonic()
        if now - last_ckpt >= CHECKPOINT_SEC:
            _save_checkpoint(work_dir, frame_idx, holder["positions"], video_path)
            last_ckpt = now
        if total:
            holder["total"] = total
            holder["done"] = frame_idx

    new_positions, meta = detect_positions(video_path, calib, progress_cb=on_progress,
                                           resume_frame=resume_frame)
    all_positions = positions + new_positions

    events = decide_goals(all_positions, calib)
    warning = None
    if not all_positions:
        warning = "整段视频在筐口区域未检测到球，标定可能不适用于此视频，建议重新标定。"
    save_events(events_path, video=video_path, duration=meta["duration"],
                events=events, warning=warning)

    ck = os.path.join(work_dir, CHECKPOINT_FILE)
    if os.path.exists(ck):
        os.remove(ck)          # 完成，清理检查点
    return events_path, warning
```

注意：`on_progress` 写检查点用的是 `holder["positions"]`（续跑前 + 新检出），`_save_checkpoint` 里的列表要和 `resume` 时的解析格式一致（`[t, x, y, area]`）。

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/Scripts/python -m pytest tests/test_pipeline.py -v
```

Expected: 4 PASS

- [ ] **Step 5: 全量回归**

```bash
.venv/Scripts/python -m pytest tests/ -v
```

Expected: 全部 PASS

- [ ] **Step 6: Commit**

```bash
git add bbh/pipeline.py tests/test_pipeline.py
git commit -m "feat: 检测管线（检查点续跑与 events.json）"
```

---

### Task 10: 集成验收工具

**Files:**
- Create: `tools/evaluate.py`
- Test: `tests/test_evaluate.py`

- [ ] **Step 1: 写失败测试**

`tests/test_evaluate.py`:
```python
from tools.evaluate import match_report


def test_标准进球命中():
    report = match_report(truth=[100.0, 300.0],
                          detected=[(100.1, "high"), (310.0, "review"),
                                    (500.0, "high")])
    assert report["命中"] == 2
    assert report["漏检"] == 0
    assert report["误报"] == 1
    assert report["待确认中"] == 1


def test_容差3秒():
    report = match_report(truth=[100.0], detected=[(102.9, "high")])
    assert report["命中"] == 1


def test_超容差算漏检加误报():
    report = match_report(truth=[100.0], detected=[(103.1, "high")])
    assert report["漏检"] == 1
    assert report["误报"] == 1
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/Scripts/python -m pytest tests/test_evaluate.py -v
```

Expected: FAIL（ModuleNotFoundError）。若报包路径问题，在项目根建 `pyproject.toml` 加入 `[tool.pytest.ini_options] pythonpath = ["."]`。

- [ ] **Step 3: 实现**

`tools/evaluate.py`:
```python
"""集成验收：检测结果 vs 人工标注 → 漏检/误报/待确认报告。

用法：
    python tools/evaluate.py work/<视频名>/events.json truth.json

truth.json 格式：{"goals": [100.0, 300.5, ...]}（人工标注的进球秒数）
"""
import json
import sys

TOLERANCE = 3.0  # 秒


def match_report(truth: list[float],
                 detected: list[tuple[float, str]]) -> dict:
    """detected: [(t, status)]。返回 命中/漏检/误报/待确认中 统计。"""
    used = set()
    hits = 0
    hits_review = 0
    for t, status in detected:
        match = next((i for i, g in enumerate(truth)
                      if i not in used and abs(g - t) <= TOLERANCE), None)
        if match is not None:
            used.add(match)
            hits += 1
            if status == "review":
                hits_review += 1
    return {"命中": hits, "待确认中": hits_review,
            "漏检": len(truth) - hits, "误报": len(detected) - hits}


def main():
    events_path, truth_path = sys.argv[1], sys.argv[2]
    with open(events_path, encoding="utf-8") as f:
        events = json.load(f)["events"]
    with open(truth_path, encoding="utf-8") as f:
        truth = json.load(f)["goals"]
    report = match_report(truth, [(e["t"], e["status"]) for e in events])
    total = len(truth)
    miss_rate = report["漏检"] / total if total else 0.0
    print(f"标注进球: {total}")
    print(f"命中: {report['命中']}（其中待确认组 {report['待确认中']}）")
    print(f"漏检: {report['漏检']}  ({miss_rate:.0%})")
    print(f"误报: {report['误报']}")
    print("验收门: 漏检率 <=15% 且 误报 <= 3×命中数")
    sys.exit(0 if miss_rate <= 0.15 and report["误报"] <= 3 * max(report["命中"], 1)
             else 1)


if __name__ == "__main__":
    main()
```

若加了 `pyproject.toml`，内容：
```toml
[tool.pytest.ini_options]
pythonpath = ["."]
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/Scripts/python -m pytest tests/test_evaluate.py -v
```

Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add tools/evaluate.py tests/test_evaluate.py pyproject.toml
git commit -m "feat: 集成验收工具（漏检/误报报告）"
```

---

### Task 11: Gradio 界面 —— 标定页

**Files:**
- Create: `bbh/app.py`（本任务只做标定页 + 页面骨架）

界面测试以人工验证为主（Gradio 组件 API 随版本变化，逻辑已由前面任务的测试覆盖）。

- [ ] **Step 1: 实现**

`bbh/app.py`:
```python
"""Gradio 三页界面：标定 / 分析 / 集锦。"""
import cv2
import gradio as gr
import numpy as np

from . import calibration, editor, pipeline
from .models import Calibration, ClipSettings, load_events

CALIB_FILE = "calibration.json"


# ---------- 标定页 ----------

def extract_frame(video_path: str):
    """取视频中段一帧给用户标注。"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise gr.Error("无法打开视频")
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.set(cv2.CAP_PROP_POS_FRAMES, total // 2)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise gr.Error("读帧失败")
    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)


def points_from_sketch(value):
    """从 Sketchpad 提取每次点击的坐标（每层笔画的质心）。"""
    if value is None:
        return []
    layers = value["layers"] if isinstance(value, dict) else [value[1]]
    pts = []
    for layer in layers:
        arr = np.array(layer)
        alpha = arr[:, :, 3] if arr.ndim == 3 else arr
        ys, xs = np.nonzero(alpha > 0)
        if len(xs):
            pts.append((float(xs.mean()), float(ys.mean())))
    return pts


def build_calibration(sketch_value):
    """点击约定：第 1、2 点 = 筐口左端、右端；第 3 点起 = 球（1-3 次）。"""
    pts = points_from_sketch(sketch_value)
    if len(pts) < 3:
        raise gr.Error("请至少点击 3 次：筐口左端、筐口右端、球")
    rect = calibration.hoop_rect_from_rim_points(pts[0], pts[1])
    frame = np.array(sketch_value["background"]) if isinstance(sketch_value, dict) \
        else np.array(sketch_value[0])
    low, high = calibration.hsv_range_from_clicks(
        cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
        clicks=[(round(x), round(y)) for x, y in pts[2:]])
    calib = Calibration(hoop_rect=rect, hsv_low=low, hsv_high=high,
                        frame_size=(frame.shape[1], frame.shape[0]))
    calib.save(CALIB_FILE)
    preview = calibration.overlay_preview(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR),
                                          calib)
    n_events_hint = "标定已保存。绿色=识别为球的区域，蓝框=筐口，红线=筐口平面。"
    return cv2.cvtColor(preview, cv2.COLOR_BGR2RGB), n_events_hint


def build_app():
    with gr.Blocks(title="篮球进球自动集锦") as demo:
        with gr.Tab("① 标定（首次使用做一次）"):
            gr.Markdown("上传任意一段日常视频 → 在画面上依次点击：**筐口左端、筐口右端、球（1-3 次）**")
            calib_video = gr.Video(label="参考视频（上传后自动抽帧）")
            sketch = gr.Sketchpad(label="点击标注", layers=False, bounding_box=None)
            btn_calib = gr.Button("生成标定")
            preview_img = gr.Image(label="预览验证（绿=球高亮，蓝框=筐口）")
            calib_msg = gr.Markdown()
            calib_video.change(extract_frame, inputs=calib_video, outputs=sketch)
            btn_calib.click(build_calibration, inputs=sketch,
                            outputs=[preview_img, calib_msg])
        with gr.Tab("② 分析"):
            pass  # Task 12 填充
        with gr.Tab("③ 集锦"):
            pass  # Task 13 填充
    return demo


if __name__ == "__main__":
    build_app().launch()
```

- [ ] **Step 2: 人工验证**

```bash
.venv/Scripts/python -m bbh.app
```

浏览器打开 http://127.0.0.1:7860：
1. 上传一段真实篮球视频 → 出现中段帧
2. 按顺序点击筐口左端、右端、球 → 点"生成标定"
3. 预览图中球区域变绿、篮框正确 → `calibration.json` 出现在项目根目录
4. 用样本视频反复调整点击位置直到预览满意

Expected: 预览叠加图符合预期，无报错。

- [ ] **Step 3: Commit**

```bash
git add bbh/app.py
git commit -m "feat: Gradio 标定页"
```

---

### Task 12: Gradio 界面 —— 分析页（后台检测 + 进度 + 续跑）

**Files:**
- Modify: `bbh/app.py`

- [ ] **Step 1: 实现（在 Task 11 的 `② 分析` Tab 内填充）**

在文件顶部补充：
```python
import os
import threading

_state = {"thread": None, "running": False, "video": None, "work_dir": None,
          "done": False, "error": None, "events_path": None, "warning": None,
          "done_frames": 0, "total_frames": 0}


def _work_dir_for(video_path: str) -> str:
    name = os.path.splitext(os.path.basename(video_path))[0]
    return os.path.join("work", name)


def _detect_worker(video_path: str):
    try:
        calib = Calibration.load(CALIB_FILE)
        work_dir = _work_dir_for(video_path)
        holder = _state

        def cb(frame_idx, total):
            holder["done_frames"] = frame_idx
            holder["total_frames"] = total

        # pipeline 的进度通过 monkey 层转发：直接调用 detect+pipeline 组合
        import bbh.pipeline as pl
        orig = pl.detect_positions
        def wrapped(video, calib_, step=2, progress_cb=None, resume_frame=0):
            def cb2(fi, tot):
                cb(fi, tot)
                if progress_cb:
                    progress_cb(fi, tot)
            return orig(video, calib_, step, cb2, resume_frame)
        pl.detect_positions = wrapped
        try:
            events_path, warning = pl.run_detection(video_path, calib, work_dir)
        finally:
            pl.detect_positions = orig
        _state.update(events_path=events_path, warning=warning, done=True)
    except Exception as e:  # noqa: BLE001 —— 任何错误都要带回界面
        _state.error = str(e)
    finally:
        _state.running = False


def start_detection(video_path: str):
    if not os.path.exists(CALIB_FILE):
        raise gr.Error("请先完成 ① 标定")
    if not os.path.exists(video_path):
        raise gr.Error(f"视频不存在: {video_path}")
    if _state["running"]:
        raise gr.Error("已有检测在跑")
    _state.update(running=True, done=False, error=None, video=video_path,
                  events_path=None, warning=None, done_frames=0,
                  total_frames=0, work_dir=_work_dir_for(video_path))
    _state["thread"] = threading.Thread(target=_detect_worker,
                                        args=(video_path,), daemon=True)
    _state["thread"].start()
    return _refresh_status()


def _refresh_status():
    if _state["error"]:
        return f"❌ 出错：{_state['error']}", 0, ""
    if _state["running"]:
        pct = int(_state["done_frames"] * 100 / _state["total_frames"]) \
            if _state["total_frames"] else 0
        return f"⏳ 检测中… {_state['done_frames']}/{_state['total_frames']} 帧 ({pct}%)", \
            pct, ""
    if _state["done"]:
        from .models import load_events
        data = load_events(_state["events_path"])
        n_high = sum(1 for e in data["events"] if e.status == "high")
        n_review = len(data["events"]) - n_high
        msg = f"✅ 完成：高置信 {n_high} 个，待确认 {n_review} 个"
        if data["warning"]:
            msg += f"\n⚠️ {data['warning']}"
        return msg, 100, msg
    return "空闲", 0, ""
```

`② 分析` Tab 内容：
```python
        with gr.Tab("② 分析"):
            gr.Markdown("输入本周视频的**完整本机路径**（无需上传），点击开始。中途退出可点开始续跑。")
            video_path_box = gr.Textbox(label="视频路径",
                                        placeholder=r"D:\videos\2026-08-15.mp4")
            btn_start = gr.Button("开始检测", variant="primary")
            status_md = gr.Markdown("空闲")
            progress_bar = gr.Progress()
            status_timer = gr.Timer(2.0)
            status_timer.tick(_refresh_status, outputs=[status_md, progress_bar, status_md])
            btn_start.click(start_detection, inputs=video_path_box,
                            outputs=[status_md, progress_bar, status_md])
```

注意：`gr.Progress` 输出绑定的写法在不同 Gradio 5 小版本有差异；若 `outputs` 不接受 Progress，改为用 `gr.Slider(0, 100, value=0, label="进度%")` 展示，`_refresh_status` 返回的 pct 直接喂 slider。

- [ ] **Step 2: 人工验证**

```bash
.venv/Scripts/python -m bbh.app
```

1. 先完成标定（Task 11 验证过）
2. 分析页贴入样本视频路径 → 开始 → 进度推进 → 显示"高置信 X 待确认 Y"
3. 中途 Ctrl+C 关掉进程 → 重启 → 再点开始 → 从检查点续跑（观察日志/帧计数不从 0 开始）
4. `work/<视频名>/events.json` 生成且内容合理

Expected: 以上全部符合。

- [ ] **Step 3: Commit**

```bash
git add bbh/app.py
git commit -m "feat: Gradio 分析页（后台检测、进度、续跑）"
```

---

### Task 13: Gradio 界面 —— 集锦页（复核 + 时间编辑 + 生成）

**Files:**
- Modify: `bbh/app.py`
- Create: `run_app.bat`, `README.md`

- [ ] **Step 1: 实现（在 `③ 集锦` Tab 内填充）**

补充函数：
```python
def load_events_table(video_path: str):
    work_dir = _work_dir_for(video_path)
    path = os.path.join(work_dir, "events.json")
    if not os.path.exists(path):
        raise gr.Error("请先在 ② 分析 完成检测")
    data = load_events(path)
    rows = [[1 if e.status == "high" else 0,
             f"{int(e.t // 60):02d}:{e.t % 60:04.1f}", e.status,
             f"{e.confidence:.2f}", 15, 5] for e in data["events"]]
    return data["duration"], rows


def preview_clip(video_path: str, table, row_idx: int):
    """按当前行的时间窗口切预览（流复制，秒出，关键帧对齐即可）。"""
    if row_idx is None or row_idx >= len(table):
        raise gr.Error("请先在表格中选中一行")
    _, t_str, _, _, before, after = table[row_idx]
    t = int(t_str[:2]) * 60 + float(t_str[3:])
    out = os.path.join(_work_dir_for(video_path), f"preview_{row_idx}.mp4")
    import subprocess
    subprocess.run(["ffmpeg", "-y", "-ss", f"{max(0.0, t - before):.3f}",
                    "-i", video_path, "-t", f"{before + after:.3f}",
                    "-c", "copy", out], capture_output=True)
    return out


def generate_highlight(video_path: str, table, default_before: float,
                       default_after: float):
    settings = []
    for checked, t_str, _, _, before, after in table:
        if not checked:
            continue
        t = int(t_str[:2]) * 60 + float(t_str[3:])
        settings.append((t, before, after))
    if not settings:
        raise gr.Error("至少勾选一个进球")
    data = load_events(os.path.join(_work_dir_for(video_path), "events.json"))
    clips = editor.plan_clips(settings, duration=data["duration"])
    out = editor.build_highlight(video_path, clips, _work_dir_for(video_path))
    return out
```

`③ 集锦` Tab 内容：
```python
        with gr.Tab("③ 集锦"):
            hl_video_box = gr.Textbox(label="视频路径（与 ② 相同）")
            btn_load = gr.Button("载入检测结果")
            duration_md = gr.Markdown()
            events_table = gr.Dataframe(
                headers=["选中", "时间", "状态", "置信度", "前秒", "后秒"],
                datatype=["bool", "str", "str", "str", "number", "number"],
                label="进球列表（可直接编辑"选中/前秒/后秒"列）",
                interactive=True)
            with gr.Row():
                global_before = gr.Number(15, label="全局默认：进球前(秒)")
                global_after = gr.Number(5, label="进球后(秒)")
                btn_apply_all = gr.Button("应用到全部")
            selected_row = gr.Number(label="预览行号（0 起）", precision=0)
            btn_preview = gr.Button("▶ 预览该行")
            preview_player = gr.Video(label="预览")
            btn_generate = gr.Button("🎬 生成集锦 MP4", variant="primary")
            highlight_player = gr.Video(label="集锦成品")

            def _load(v):
                dur, rows = load_events_table(v)
                return f"视频总长 {int(dur // 60)} 分 {dur % 60:.0f} 秒", rows

            def _apply_all(table, b, a):
                for row in table:
                    row[4], row[5] = b, a
                return table

            btn_load.click(_load, inputs=hl_video_box,
                           outputs=[duration_md, events_table])
            btn_apply_all.click(_apply_all,
                                inputs=[events_table, global_before, global_after],
                                outputs=events_table)
            btn_preview.click(preview_clip,
                              inputs=[hl_video_box, events_table, selected_row],
                              outputs=preview_player)
            btn_generate.click(generate_highlight,
                               inputs=[hl_video_box, events_table,
                                       global_before, global_after],
                               outputs=highlight_player)
```

`run_app.bat`:
```bat
@echo off
cd /d "%~dp0"
.venv\Scripts\python -m bbh.app
pause
```

`README.md`:
```markdown
# 篮球进球自动集锦工具

固定机位（三脚架、半场单篮筐）篮球视频自动检测进球并合成集锦。

## 使用

1. 双击 `run_app.bat`（首次先完成下方"安装"）
2. **① 标定**：上传任意一段日常视频，点击筐口左端、右端、球 → 生成标定（固定机位只需一次）
3. **② 分析**：贴入本周视频的完整路径 → 开始检测（可中断续跑）
4. **③ 集锦**：载入结果 → 勾选/剔除、调整每个片段前后秒数 → 预览 → 生成 MP4
   - 产物在 `work/<视频名>/highlight.mp4`，单片段在 `work/<视频名>/clips/`

## 安装（首次）

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```
需要 ffmpeg 在 PATH 中（`winget install Gyan.FFmpeg`）。

## 验收评估

人工标注真实进球时间（`truth.json` 格式 `{"goals": [秒, ...]}`）后：
```
.venv\Scripts\python tools/evaluate.py work/<视频名>/events.json truth.json
```
```

- [ ] **Step 2: 人工验证（端到端）**

```bash
.venv/Scripts/python -m bbh.app
```

1. 用样本视频走完 标定 → 分析 → 集锦 全流程
2. 表格中取消勾选一个事件 → 生成 → 集锦不含该球
3. 改某行"前秒"为 30 → 预览确认起点提前
4. 全局应用 → 生成 → `highlight.mp4` 在手机可播（H.264/AAC）

Expected: 全部符合。

- [ ] **Step 3: 全量回归 + Commit**

```bash
.venv/Scripts/python -m pytest tests/ -v
git add bbh/app.py run_app.bat README.md
git commit -m "feat: Gradio 集锦页与启动脚本，完成端到端流程"
```

---

### Task 14: 真实样本集成验收（验收门）

**Files:**
- Create: `truth_<片段名>.json`（人工标注，不入库也行）

- [ ] **Step 1: 用户标注真实进球**

请用户从样本视频挑 2-3 个各 10 分钟的片段，人工看一遍，把真实进球时间点（分:秒）记下来，转成 `{"goals": [秒, ...]}` 存为 `truth.json`（每个片段一个文件）。

- [ ] **Step 2: 跑全管线 + 评估**

对每个片段：
```bash
.venv/Scripts/python -m bbh.app        # 界面里跑分析（或直接调 pipeline）
.venv/Scripts/python tools/evaluate.py work/<片段名>/events.json truth_<片段名>.json
```

Expected 报告输出：命中/待确认中/漏检(率)/误报。

- [ ] **Step 3: 判定验收门**

- 漏检率 ≤15% 且 误报 ≤ 3×命中数 → **通过**，项目完成，提交：
  ```bash
  git add -A && git commit -m "chore: 真实样本验收通过"
  ```
- 不达标 → 按设计文档第 10 节启动 YOLO 升级路径（另立计划，模块 2 替换，其余复用）

---

## Self-Review 记录

- **Spec 覆盖**：需求表（宁多勿漏→置信度分层 Task 4；两级时间编辑→Task 13 Dataframe 前后秒列+全局应用；图形界面→Task 11-13；断点续跑→Task 9/12；标定失效警告→Task 9；片段合并/越界→Task 8；待确认默认不选→Task 13 `1 if status=="high" else 0`；验收门→Task 14）。错误处理表中"视频损坏报错"在 Task 7 `VideoOpenError`、"ffmpeg 失败显示日志"在 Task 8 `_run`、"无音轨兼容"由 ffmpeg 默认行为覆盖（aac 编码无输入轨时自动静音，已在 Task 13 人工验证含样本视频）
- **占位符**：Task 11/12 的 Tab 留空属于有意的分任务填充（后续任务有完整代码），非 TBD
- **类型一致性**：`Calibration(hoop_rect/hsv_low/hsv_high/frame_size)`、`BallPos(t/x/y/area)`、`decide_goals(positions, calib)`、`detect_positions(video, calib, step, progress_cb, resume_frame) → (positions, meta)`、`plan_clips(list[(t, before, after)], duration) → [(start, end)]` 各任务间已核对一致
