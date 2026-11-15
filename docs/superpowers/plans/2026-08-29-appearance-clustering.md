# 出手人外观聚类 + 分组命名（Phase 2）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把每球射手的外观指纹（三段 HSV 直方图+体型+头部）从视频中提取出来，聚类成"同人多球"的组，UI 上一组命名一次即整组打标签，最终按人出集锦不再逐球手标。

**Architecture:** 新模块 `bbh/appearance.py`（特征提取+聚类，两级缓存 features.json/groups.json，独立版本键），`shooters.json` 只读（release_t/release_xy 是特征定位锚）。UI 在「🧍 识别出手人」按钮链路后自动接特征提取→聚类→分组命名面板（gradio HTML+CheckboxGroup+Dropdown）。验收=两场 225 条真值重放纯度报告（tools/check_shooters.py 扩展）。

**Tech Stack:** Python 3.11 / OpenCV / numpy（**零新依赖**——无 matplotlib/scipy，分布验证用文本输出）/ gradio（UI 既有）

**Spec:** `docs/superpowers/specs/2026-08-29-appearance-clustering-design.md`（本计划从 spec 出发，执行者两个都读）

> **修订（2026-08-29 第二轮，Task 6 标定后产品重定向）**：Task 1-5 已按原案完成
> 在本分支（连边建组代码保留但**下线**）。Task 6 标定证伪颜色分离度、两轮 OCR
> 验证证伪号码自动建议（证据 `.superpowers/sdd/2026-08-29-appearance-clustering/
> progress.md` ①-⑬），spec 已按第二轮拍板誊正：**程序粗标=颜色排序提示（恒 low），
> 人工二次核对=表格勾球手动归人（manual_groups.json 一等公民，双写
> tag_assignments），护栏=MIN_GOAL_GAP_S=2.5 警示不阻断**。原「聚类→分组命名
> 面板→纯度 gate」链路作废；Task 6 起按下方重写版执行（Task 6b=原 Task 6 善后）。

## Global Constraints

- 测试命令：全套 `./.venv/Scripts/python.exe -X utf8 -m pytest -q`（Task 1-5 落地后基线 275 个，**任何任务完成后零回归**）；单文件 `./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_appearance.py -v`
- 版本键纪律：只新增 `FEAT_VERSION` / `CLUSTER_VERSION`（appearance.py 内），**绝不碰 `DETECT_VERSION` / `LOGIC_VERSION` / `SHOOTER_VERSION`**；`manual_groups.json` 不带任何版本键（手动标签是事实不是缓存）
- 中文路径铁律：`cv2.imwrite` 禁用（静默失败），一律 `cv2.imencode(...).tofile(...)`（shooter.py 2026-08-15 实测教训）
- 单球/单组异常**不 raise**：记 `err` 继续下一球——生成器中途 raise 会让 UI 按钮永久卡灰（gen_selected 教训）
- 编排层逐球写盘（原子替换 `.tmp`+`os.replace`）：中断重跑只补缺（trace_video 同款惯例）
- 帧时间一律 PTS（`CAP_PROP_POS_MSEC`，detector v4 教训：帧号/fps 钟在 VFR 下早最多 3s）
- 提交信息含根因/证据（仓库惯例）；临时标定脚本文件名 `tmp_` 前缀、用完删
- 建议护栏硬编码不可配置：恒 low、永不自动生效、已归人球不出现、新信号先过评估工装（spec§建议钩子）

---

### Task 1: appearance.py 骨架——Appearance 数据类 + features.json 读写/身份校验

**Files:**
- Create: `bbh/appearance.py`
- Test: `tests/test_appearance.py`（新建）

**Interfaces:**
- Consumes: `bbh.pipeline._calib_key(calib)`（缓存指纹，shooter.py 同款）；`bbh.models.Calibration`
- Produces（后续任务依赖，签名不得变）:
  - `FEAT_VERSION = 1`、`CLUSTER_VERSION = 1`、`FEATURES_FILE = "features.json"`、`GROUPS_FILE = "groups.json"`
  - `@dataclass Appearance`: `ok: bool`、`hists: dict | None`（`{"top": [float], "pants": [float], "shoes": [float]}` 各 1024 长归一化拉平）、`body_ratio: float | None`、`aspect: float | None`、`head_hist: list | None`、`missing: list`、`err: str | None`；方法 `to_dict() -> dict` / `@classmethod from_dict(d) -> Appearance`
  - `save_features(work_dir, video, calib, feats: dict)`、`load_features(work_dir) -> dict | None`、`features_valid(data, video, calib) -> bool`（身份=视频+calib_key+FEAT_VERSION）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_appearance.py
"""appearance.py 单测：全部合成数据（合成掩码/合成指纹/合成小视频），
不依赖真视频——真值验收走 tools/check_shooters.py 与标定轮。"""
import json

import pytest

from bbh.appearance import (Appearance, FEAT_VERSION, features_valid,
                            load_features, save_features)


def test_指纹序列化往返(tmp_path):
    a = Appearance(ok=True, hists={"top": [0.5] * 1024, "pants": [0.2] * 1024,
                                   "shoes": [0.1] * 1024},
                   body_ratio=4.2, aspect=0.28, head_hist=[0.9] * 32,
                   missing=["shoes"], err=None)
    save_features(str(tmp_path), "vid.mp4", CALIB, {"01:23.7": a})
    d = load_features(str(tmp_path))
    assert features_valid(d, "vid.mp4", CALIB)
    a2 = Appearance.from_dict(d["feats"]["01:23.7"])
    assert a2.ok and a2.body_ratio == pytest.approx(4.2) and a2.missing == ["shoes"]


def test_身份不符作废(tmp_path):
    save_features(str(tmp_path), "vid.mp4", CALIB, {})
    assert not features_valid(load_features(str(tmp_path)), "other.mp4", CALIB)


def test_缺失档容错(tmp_path):
    assert load_features(str(tmp_path)) is None          # 文件不存在
    (tmp_path / "features.json").write_text("{截断", encoding="utf-8")
    assert load_features(str(tmp_path)) is None          # 损坏不抛
```

夹具（文件顶部，`import` 之后）——与 conftest 的 `sample_calibration` 同款字段：

```python
from bbh.models import Calibration

CALIB = Calibration(hoop_rect=(300, 140, 80, 40), hsv_low=(0, 80, 120),
                    hsv_high=(25, 255, 255), frame_size=(640, 360))
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_appearance.py -v`
Expected: FAIL（`ModuleNotFoundError: bbh.appearance` / ImportError）

- [ ] **Step 3: 最小实现**

```python
# bbh/appearance.py
"""出手人识别 Phase 2：外观特征提取 + 聚类分组（2026-08-29 spec）。

依赖 Phase 1 的 shooters.json（只读——release_t/release_xy 是特征定位
锚，status 三档决定建组资格：high 建组、review 只挂不建）。两级缓存：
features.json（身份=视频+标定指纹+FEAT_VERSION，特征算法变才重提，
每球 ~1s 要回视频读帧）与 groups.json（身份=features+CLUSTER_VERSION，
调聚类参数秒级重聚）。绝不碰 DETECT/LOGIC/SHOOTER 版本。
零模型依赖：中值背景差分 + HSV 直方图（OpenCV/numpy only）。"""
import json
import os
from dataclasses import dataclass, field

from .pipeline import _calib_key

FEAT_VERSION = 1           # 特征算法（分段/皮肤剔除/直方图口径）变化时 +1
CLUSTER_VERSION = 1        # 聚类规则/阈值变化时 +1
FEATURES_FILE = "features.json"
GROUPS_FILE = "groups.json"


@dataclass
class Appearance:
    """一球射手的外观指纹。缺失段不参与比对（missing 记名，整体置信降档）。"""
    ok: bool
    hists: dict | None = None       # {"top"/"pants"/"shoes": 各 1024 归一化拉平}
    body_ratio: float | None = None  # 人形掩码高 / 筐宽（同场同机位粗比）
    aspect: float | None = None      # 掩码高/宽
    head_hist: list | None = None    # 顶部 ~15% 发色 H 直方图（32 bin，仲裁票）
    jersey_no: str | None = None     # 号码票接口预留（spec：本期不实现 OCR）
    missing: list = field(default_factory=list)
    err: str | None = None

    def to_dict(self):
        return {"ok": self.ok, "hists": self.hists, "body_ratio": self.body_ratio,
                "aspect": self.aspect, "head_hist": self.head_hist,
                "jersey_no": self.jersey_no,
                "missing": self.missing, "err": self.err}

    @classmethod
    def from_dict(cls, d):
        return cls(ok=bool(d.get("ok")), hists=d.get("hists"),
                   body_ratio=d.get("body_ratio"), aspect=d.get("aspect"),
                   head_hist=d.get("head_hist"), jersey_no=d.get("jersey_no"),
                   missing=list(d.get("missing") or []), err=d.get("err"))


def save_features(work_dir: str, video: str, calib, feats: dict) -> None:
    """features.json 落盘（原子替换；编排层逐球调用——中断后已完成的球不丢）。"""
    os.makedirs(work_dir, exist_ok=True)
    p = os.path.join(work_dir, FEATURES_FILE)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"video": video, "calib": _calib_key(calib),
                   "feat_ver": FEAT_VERSION,
                   "feats": {k: a.to_dict() for k, a in feats.items()}},
                  f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def load_features(work_dir: str) -> dict | None:
    """读 features.json；缺失/损坏返回 None 不抛（load_shooters 同款容错）。"""
    try:
        with open(os.path.join(work_dir, FEATURES_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def features_valid(data: dict | None, video: str, calib) -> bool:
    """缓存身份校验：同视频 + 同标定指纹 + 同特征版本。"""
    return bool(data) and data.get("video") == video \
        and data.get("calib") == _calib_key(calib) \
        and data.get("feat_ver") == FEAT_VERSION
```

（`bbh/__init__.py` 不用动——shooter 就不是包导出的。）

- [ ] **Step 4: 跑测试确认通过**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_appearance.py -v`
Expected: 3 PASS

- [ ] **Step 5: 提交**

```bash
git add bbh/appearance.py tests/test_appearance.py
git commit -m "feat: appearance.py 骨架——Appearance 指纹类与 features.json 缓存（身份键=视频+标定+FEAT_VERSION，shooters 同款容错/原子写）"
```

---

### Task 2: 人形分割——per-球中值背景 + 目标人选取

**Files:**
- Modify: `bbh/appearance.py`（追加）
- Test: `tests/test_appearance.py`（追加；新合成视频夹具）

**Interfaces:**
- Consumes: `bbh.shooter._detect_source(video_path, work_dir)`（转码副本兜底）
- Produces:
  - `grab_window_frames(video_path, work_dir, t: float, half_s=2.5, n=15) -> list[np.ndarray]`（均匀采样，seek 失败的帧跳过）
  - `person_mask(video_path, work_dir, t: float, xy: tuple, hoop_w: float) -> tuple[np.ndarray, tuple, np.ndarray] | None`——`(掩码布尔阵, (x, y, w, h), 目标帧BGR)`；None=取不到（宁缺勿错）。内部：中值背景→灰度 absdiff→阈值→形态学→连通域→目标人=包含 `(rx, ry+0.5*hoop_w)` 的最大域，无包含则中心距出手点 < 2 筐宽内最大域
- 常量：`BG_HALF_S = 2.5`、`BG_SAMPLES = 15`、`DIFF_TH = 28`（灰度差分阈）、`PERSON_MIN_H_W = 1.6`（掩码高 < 1.6 筐宽=碎片不算人形）

- [ ] **Step 1: 写失败测试**

夹具——合成"移动人形"视频（文件顶部追加）：

```python
import cv2
import numpy as np

W2, H2, FPS2, N2 = 640, 360, 25, 200
HOOP_W2 = 80          # conftest 筐宽口径


def _person_rect(i):
    """帧 i 的人形：40x150 竖块从 x=100 移到 x=500（背景中值=纯背景）。
    高 150 ≥ PERSON_MIN_H_W×筐宽=1.6×80=128——矮了会被碎片过滤。"""
    x = 100 + int(i * 2.2)
    return x, 130, 40, 150


def make_person_video(path):
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), FPS2, (W2, H2))
    for i in range(N2):
        img = np.full((H2, W2, 3), (40, 44, 50), np.uint8)
        x, y, w, h = _person_rect(i)
        cv2.rectangle(img, (x, y), (x + w, y + 40), (40, 40, 200), -1)   # 红上衣
        cv2.rectangle(img, (x, y + 40), (x + w, y + h), (200, 120, 40), -1)  # 蓝裤
        vw.write(img)
    vw.release()
    return path


@pytest.fixture(scope="session")
def person_video(tmp_path_factory):
    return make_person_video(str(tmp_path_factory.mktemp("pv") / "person.mp4"))


def test_移动人被检出为目标(person_video, tmp_path):
    i = 100                                  # t = 4.0s
    t = i / FPS2
    x, y, w, h = _person_rect(i)
    res = person_mask(person_video, str(tmp_path), t, (x + w / 2, y + 30),
                      HOOP_W2)
    assert res is not None
    mask, (bx, by, bw, bh), frame = res
    assert frame.shape == (360, 640, 3)             # 目标帧随掩码返回（Task 3 用）
    assert abs(bx - x) <= 6 and abs(by - y) <= 6     # bbox 贴住人
    assert bh >= 80 and bw >= 34
    assert mask[bh // 2, bw // 2]                   # 中心在人身上


def test_静止人被中值洗掉返回None(tmp_path_factory):
    """人整窗不动 → 烧进中值背景 → 找不到前景 → None（宁缺勿错）。"""
    p = str(tmp_path_factory.mktemp("pv2") / "static.mp4")
    vw = cv2.VideoWriter(p, cv2.VideoWriter_fourcc(*"mp4v"), FPS2, (W2, H2))
    for _ in range(N2):
        img = np.full((H2, W2, 3), (40, 44, 50), np.uint8)
        cv2.rectangle(img, (300, 130), (340, 280), (40, 40, 200), -1)
        vw.write(img)
    vw.release()
    assert person_mask(p, str(tmp_path), 4.0, (320, 160), HOOP_W2) is None


def test_出手点旁小碎片不抢目标(person_video, tmp_path):
    """出手点不落在任何域上 → 取 2 筐宽内最近的域（小碎片 <1.6 筐高被
    PERSON_MIN_H_W 滤掉）。"""
    i = 120
    t = i / FPS2
    x, y, w, h = _person_rect(i)
    res = person_mask(person_video, str(tmp_path), t, (x + w + 100, y + 60),
                      HOOP_W2)
    assert res is not None and abs(res[1][0] - x) <= 6
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_appearance.py -v`
Expected: 新增 3 个 FAIL（NameError: person_mask）

- [ ] **Step 3: 实现**

```python
# bbh/appearance.py 追加
import cv2
import numpy as np

from .shooter import _detect_source

BG_HALF_S = 2.5        # 中值背景半窗：出手人跑位/起跳在移动，5s 窗中值洗掉
BG_SAMPLES = 15        # 采样帧数（步进按帧率取整）
DIFF_TH = 28           # 灰度差分阈（合成测试+真值标定轮复核）
PERSON_MIN_H_W = 1.6   # 人形最小高度（筐宽倍数）——更矮=碎片/遮挡残段


def grab_window_frames(video_path, work_dir, t, half_s=BG_HALF_S,
                       n=BG_SAMPLES):
    """[t-half_s, t+half_s] 均匀取 n 帧（PTS 定位，VFR 安全；seek 失败跳过）。"""
    src = _detect_source(video_path, work_dir)
    cap = cv2.VideoCapture(src)
    out = []
    try:
        for k in range(n):
            tk = max(0.0, t - half_s + 2 * half_s * k / max(1, n - 1))
            cap.set(cv2.CAP_PROP_POS_MSEC, tk * 1000.0)
            ok, frame = cap.read()
            if ok and frame is not None:
                out.append(frame)
    finally:
        cap.release()
    return out


def person_mask(video_path, work_dir, t, xy, hoop_w):
    """出手时刻的目标人掩码（per-球中值背景差分）。返回 (mask, bbox) 或 None。

    目标人=包含出手点正下方半筐处的最大连通域（出手点=球在手里，必在人
    形上沿附近）；无包含则退而取中心距出手点 2 筐宽内最大域。域高
    <PERSON_MIN_H_W 筐宽=碎片不算。人静止整窗被中值洗掉 → None（宁缺勿错，
    该球特征缺失落待确认，不硬给）。"""
    frames = grab_window_frames(video_path, work_dir, t)
    if len(frames) < 5:                       # 采样不足（视频尾/读失败）
        return None
    rx, ry = xy
    target_t = t
    # 目标帧单独取（=中值序列最后一帧附近；用与采样同一 seek 路径）
    cap = cv2.VideoCapture(_detect_source(video_path, work_dir))
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, target_t) * 1000.0)
        ok, frame = cap.read()
    finally:
        cap.release()
    if not ok or frame is None:
        return None
    bg = np.median(np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
                             for f in frames]), axis=0).astype(np.uint8)
    diff = cv2.absdiff(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), bg)
    _, m = cv2.threshold(diff, DIFF_TH, 255, cv2.THRESH_BINARY)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in cnts:
        bx, by, bw, bh = cv2.boundingRect(c)
        if bh >= PERSON_MIN_H_W * hoop_w:
            boxes.append((bx, by, bw, bh, c))
    if not boxes:
        return None
    probe = (int(rx), int(ry + 0.5 * hoop_w))     # 出手点正下方半筐
    hit = [b for b in boxes
           if b[0] <= probe[0] <= b[0] + b[2] and b[1] <= probe[1] <= b[1] + b[3]]
    pool = hit or [b for b in boxes
                   if ((b[0] + b[2] / 2 - rx) ** 2
                       + (b[1] + b[3] / 2 - ry) ** 2) ** .5 <= 2 * hoop_w]
    if not pool:
        return None
    b = max(pool, key=lambda b: b[2] * b[3])
    bx, by, bw, bh, c = b
    mask_u8 = np.zeros((bh, bw), np.uint8)
    shifted = (c - np.array([[[bx, by]]])).astype(np.int32)  # 平移到 bbox 局部
    cv2.drawContours(mask_u8, [shifted], -1, 1, -1)
    return mask_u8.astype(bool), (bx, by, bw, bh), frame
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_appearance.py -v`
Expected: 全 PASS（旧 3 个 + 新 3 个）

- [ ] **Step 5: 提交**

```bash
git add bbh/appearance.py tests/test_appearance.py
git commit -m "feat: per-球中值背景人形分割——±2.5s 采样 15 帧中值洗掉移动人，出手点正下方选目标连通域，静止人烧进背景返回 None（宁缺勿错）"
```

---

### Task 3: 三段指纹——分段直方图 + 皮肤剔除 + 头部票 + 缺失段

**Files:**
- Modify: `bbh/appearance.py`（追加）
- Test: `tests/test_appearance.py`（追加）

**Interfaces:**
- Consumes: Task 2 的 `person_mask`
- Produces:
  - `describe_person(frame_bgr: np.ndarray, mask: np.ndarray, bbox: tuple, hoop_w: float) -> Appearance`（frame 与 mask 同框——调用方 Task 4 从 person_mask 的目标帧来；为可测性把帧作为参数而非内部重取）
  - `_strip_skin(hsv: np.ndarray) -> np.ndarray`（皮肤区布尔；H 3~17 且 S 40~160 且 V 60~230——初值，标定轮校准）
  - 常量：`SEG_TOP = (0.00, 0.50)`、`SEG_PANTS = (0.50, 0.80)`、`SEG_SHOES = (0.88, 1.00)`（80~88% 膝部缓冲带不取）、`HEAD_PART = 0.15`、`SEG_MIN_PX = 60`（段内有效像素下限，低于=缺失）、`HIST_BINS = 32`
- 说明：`person_mask` 在 Task 2 已返回 `(mask, bbox, frame)` 三元组——本任务直接用其目标帧

- [ ] **Step 1: 写失败测试**

```python
from bbh.appearance import (_strip_skin, describe_person, SEG_MIN_PX)


def _person_frame_mask():
    """200x90 合成人：0-50% 红上衣、50-90% 蓝裤、88-100% 白鞋，含肤色调块。"""
    frame = np.full((90, 200, 3), (40, 44, 50), np.uint8)
    frame[0:45, :] = (40, 40, 200)        # 红上衣
    frame[45:72, :] = (200, 120, 40)      # 蓝裤
    frame[79:90, :] = (240, 240, 240)     # 白鞋
    frame[30:40, 90:110] = (110, 140, 175) # 肤色块（H≈14/S≈95/V≈175 落皮肤带）
    mask = np.ones((90, 200), bool)
    return frame, mask


def test_皮肤剔除保衣服剔肤色():
    hsv = cv2.cvtColor(_person_frame_mask()[0], cv2.COLOR_BGR2HSV)
    skin = _strip_skin(hsv)
    assert skin[35, 100]                       # 肤色块命中
    assert not skin[20, 20]                    # 高饱和红衣不误剔


def test_三段指纹与缺失段():
    frame, mask = _person_frame_mask()
    app = describe_person(frame, mask, (0, 0, 200, 90), 50.0)
    assert app.ok and not app.missing
    top = np.array(app.hists["top"]).reshape(32, 32)
    # 上衣主导：top 段直方图质量集中在 H 低 bin（红 H≈2/180*32≈0）
    assert top[0:3, :].sum() > 0.5
    pants = np.array(app.hists["pants"]).reshape(32, 32)
    assert pants.sum() == pytest.approx(1.0)    # 每段归一化
    assert app.head_hist is not None and len(app.head_hist) == 32
    assert app.body_ratio == pytest.approx(90 / 50.0)   # 掩码高/筐宽
    assert app.aspect == pytest.approx(90 / 200.0)


def test_段像素不足记缺失():
    frame, mask = _person_frame_mask()
    mask2 = mask.copy()
    mask2[79:90, :] = False                    # 鞋段全灭
    app = describe_person(frame, mask2, (0, 0, 200, 90), 50.0)
    assert "shoes" in app.missing and app.hists["shoes"] is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_appearance.py -v`
Expected: 新 3 个 FAIL（ImportError: describe_person）

- [ ] **Step 3: 实现**

```python
# bbh/appearance.py 追加
SEG_TOP = (0.00, 0.50)     # 上衣（权重最高）
SEG_PANTS = (0.50, 0.80)   # 裤（短裤覆盖率本身是区分特征）
SEG_SHOES = (0.88, 1.00)   # 鞋（十几像素低权重；80~88% 膝部缓冲不取）
HEAD_PART = 0.15           # 头部票：掩码顶部比例（发色+剔发肤色）
SEG_MIN_PX = 60            # 段内非皮肤像素 < 此值 = 该段缺失
HIST_BINS = 32             # H×S 二维直方图每维 bin 数（32*32=1024）
SKIN_HSV = ((3, 17), (40, 160), (60, 230))   # 皮肤 H/S/V 带（初值，标定轮校准）


def _strip_skin(hsv: np.ndarray) -> np.ndarray:
    """皮肤区布尔掩码。S/V 收窄防误剔高饱和同 H 纯色衣（球/橙衣 H 1~12
    与肤色同带——spec：分离度不够就收窄，标定轮验证）。"""
    (h0, h1), (s0, s1), (v0, v1) = SKIN_HSV
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    return ((h >= h0) & (h <= h1) & (s >= s0) & (s <= s1)
            & (v >= v0) & (v <= v1))


def _seg_hist(hsv: np.ndarray, seg_mask: np.ndarray) -> list | None:
    """段内 H-S 二维直方图（归一化拉平 1024）；像素不足返回 None。"""
    if int(seg_mask.sum()) < SEG_MIN_PX:
        return None
    hist = cv2.calcHist([hsv], [0, 1], seg_mask.astype(np.uint8),
                        [HIST_BINS, HIST_BINS], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return hist.flatten().tolist()


def describe_person(frame_bgr, mask, bbox, hoop_w) -> Appearance:
    """人形掩码 → 三段指纹+体型+头部票。段被挡/出画面 → missing 记名、
    该票不参与后续比对（缺一票少一票，不拿空段瞎算）。

    mask 是 person_mask 返回的 **bbox 局部掩码**（bh×bw）——内部先把
    HSV 裁到同尺寸补丁，全部按局部坐标切段（Task 4 直接接线）。"""
    bx, by, bw, bh = bbox
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    hsv = hsv[by:by + bh, bx:bx + bw]              # 裁到 bbox 局部（与 mask 同形）
    keep = ~_strip_skin(hsv) & mask                # 剔皮肤后的有效像素
    hists, missing = {}, []
    for name, (r0, r1) in (("top", SEG_TOP), ("pants", SEG_PANTS),
                           ("shoes", SEG_SHOES)):
        y0, y1 = int(bh * r0), int(bh * r1)
        seg = np.zeros_like(keep)
        seg[y0:y1, :] = keep[y0:y1, :]
        h = _seg_hist(hsv, seg)
        hists[name] = h
        if h is None:
            missing.append(name)
    # 头部票：顶部 15% 发色 H 直方图（含光头=头皮肤色，与发色同票处理）
    head = np.zeros_like(mask)
    head[:int(bh * HEAD_PART), :] = mask[:int(bh * HEAD_PART), :]
    if int(head.sum()) >= 20:      # 头部票是 H-only 32bin（与 Task 1 契约/
        hh = cv2.calcHist([hsv], [0], head.astype(np.uint8), [HIST_BINS],
                          [0, 180])   # Task 5 测试/spec 一致——非 _seg_hist 的
        cv2.normalize(hh, hh)         # H-S 二维 1024）
        head_hist = hh.flatten().tolist()
    else:
        head_hist = None
    # 体型：掩码高/筐宽 + 高宽比（防守人贴身粘连致掩码过宽时 aspect 失真，
    # 由聚类端 body_compatible 容差吸收，这里照记）
    return Appearance(ok=sum(1 for v in hists.values() if v) >= 2,
                      hists=hists, body_ratio=round(bh / hoop_w, 3),
                      aspect=round(bh / max(1, bw), 3), head_hist=head_hist,
                      missing=missing)
```

同步改 `person_mask` 返回值（Task 2 代码）：

```python
    # Task 2 的 return 处：
    return mask, (bx, by, bw, bh), frame
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_appearance.py -v`
Expected: 全 PASS（9 个）

- [ ] **Step 5: 提交**

```bash
git add bbh/appearance.py tests/test_appearance.py
git commit -m "feat: 三段颜色指纹（上衣/裤/鞋 H-S 直方图，皮肤剔除+S/V 收窄防误剔同 H 纯色衣）+ 头部仲裁票 + 缺失段机制；person_mask 帧随掩码一起返回"
```

---

### Task 4: 特征提取编排 build_features——生成器逐球写盘

**Files:**
- Modify: `bbh/appearance.py`（追加）
- Test: `tests/test_appearance.py`（追加）

**Interfaces:**
- Consumes: Task 1-3 全部；`bbh.shooter.load_shooters`；`bbh.detector._scaled_rect(calib, fw, fh)` 取筐宽
- Produces:
  - `build_features(video_path, calib, work_dir, traces: dict) -> generator`——参数 traces=shooters.json 的 traces（调用方读好传入，UI/脚本共用）；逐球：`person_mask`（取不到 → ok=False 的 Appearance，不 raise）→ `describe_person` → 写盘 → `yield` 进度文案；`return` 汇总文案；缓存身份相符的球跳过（续跑）
- 进度文案格式对齐 trace_video：`⏳ 提取外观（3/195）：01:06.9 → ✅ / ⚪ 未取到`

- [ ] **Step 1: 写失败测试**

```python
from bbh.appearance import build_features, features_valid


def test_编排逐球写盘与续跑(person_video, tmp_path):
    traces = {"04:00.0": {"status": "high", "release_t": 4.0,
                          "release_xy": [340, 160], "thumbs": []},
              "04:04.0": {"status": "review", "release_t": 4.0,
                          "release_xy": [340, 160], "thumbs": []}}
    gen = build_features(person_video, CALIB, str(tmp_path), traces)
    msgs = list(gen)                     # 消费生成器（return 值丢弃，StopIteration 已被 list 吞）
    assert len(msgs) == 2
    d = load_features(str(tmp_path))
    assert features_valid(d, str(person_video), CALIB)
    assert set(d["feats"]) == {"04:00.0", "04:04.0"}
    assert d["feats"]["04:00.0"]["ok"]           # 合成人提到指纹
    # 续跑：缓存齐全 → 0 条新进度
    assert list(build_features(person_video, CALIB, str(tmp_path), traces)) == []
```

注意：`person_video` 的绝对路径两处调用一致（缓存身份=视频路径字面量）。

- [ ] **Step 2: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_appearance.py -v`
Expected: FAIL（ImportError: build_features）

- [ ] **Step 3: 实现**

```python
# bbh/appearance.py 追加
from .detector import _scaled_rect


def build_features(video_path, calib, work_dir, traces: dict):
    """逐球提外观指纹（生成器：yield 进度、return 汇总——trace_video 惯例）。

    只处理 traces 里 release_t/release_xy 齐全的球（low/未溯源无锚点，
    直接跳过不产条目）。单球异常不 raise：记 ok=False + err 继续——
    生成器中途 raise 会让 UI 按钮永久卡灰。缓存身份相符的球跳过（续跑）。
    """
    data = load_features(work_dir)
    feats = {k: Appearance.from_dict(v)
             for k, v in (data.get("feats") or {}).items()} \
        if features_valid(data, video_path, calib) else {}
    todo = [(k, v) for k, v in (traces or {}).items()
            if k not in feats and v.get("release_t") is not None
            and v.get("release_xy")]
    if not todo:
        return (f"✅ 外观指纹已齐全（{len(feats)} 球缓存，未重提）")
    ok_n = 0
    cap = cv2.VideoCapture(_detect_source(video_path, work_dir))   # 帧尺寸只取一次
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    _, _, hoop_w, _ = _scaled_rect(calib, fw, fh)
    for k, (key, tr) in enumerate(todo, 1):
        try:
            res = person_mask(video_path, work_dir, tr["release_t"],
                              tuple(tr["release_xy"]), hoop_w)
            app = (describe_person(res[2], res[0], res[1], hoop_w)
                   if res else Appearance(ok=False, err="无人形掩码"))
        except Exception as ex:  # noqa: BLE001 —— 单球失败不拖垮整批
            app = Appearance(ok=False, err=str(ex))
        feats[key] = app
        ok_n += bool(app.ok)
        save_features(work_dir, video_path, calib, feats)    # 逐球写盘
        yield (f"⏳ 提取外观（{k}/{len(todo)}）：{key} → "
               + ("✅" if app.ok else "⚪ " + (app.err or "未取到")))
    return (f"✅ 外观指纹完成：{ok_n}/{len(todo)} 球有效（缓存 "
            f"{FEATURES_FILE}）")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_appearance.py -v`
Expected: 全 PASS（10 个）

- [ ] **Step 5: 提交**

```bash
git add bbh/appearance.py tests/test_appearance.py
git commit -m "feat: build_features 编排——逐球 溯源锚→人形分割→指纹→写盘，生成器进度/续跑/单球异常不 raise（trace_video 惯例）"
```

---

### Task 5: 相似度与聚类——连通分量 + 宁拆勿错并 + review 只挂不建

**Files:**
- Modify: `bbh/appearance.py`（追加）
- Test: `tests/test_appearance.py`（追加）

**Interfaces:**
- Consumes: Task 1/3 的 `Appearance`
- Produces:
  - `feat_similar(a: Appearance, b: Appearance) -> float`——可用段加权和（top 0.55 / pants 0.30 / shoes 0.15，缺失段按可用段重归一），无可比段返回 0
  - `body_compatible(a, b) -> bool`——`body_ratio` 差 ≤ BODY_TOL（0.35）且 `aspect` 差 ≤ 0.18；任一缺失 → True（缺票不拦）
  - `head_splits(a, b) -> bool`——头部票**只拆不合**：双方 head_hist 齐全且巴氏距离 > HEAD_BHAT（0.55）→ True（拆）——距离大=发色肤色明显不同
  - `build_groups(traces: dict, feats: dict) -> list`——**纯内存聚类**，返回 groups.json 的 `groups` 数据：`[{"id": "G1", "members": [key…], "status": "ok"|"suspect"|"pending"}]`；规则：①high+ok 指纹球连边（`feat_similar ≥ EDGE_SIM` 且 `body_compatible` 且非 `head_splits`）→ 连通分量=组；②时空硬约束**组内后验拆分**（进球时刻差 ≤ MIN_GOAL_GAP_S 的成员对必拆——不做边过滤，边过滤挡不住传递并组；拆冲突最多者、平手取更晚，重复至无冲突）；③链式复检（组内两两相似度矩阵，每成员最小相似度 < CHAIN_SIM 的最低者拆出重复直至无违反，拆出者单列）；④review 球挂组（与组内**任一**成员 `feat_similar ≥ ATTACH_SIM` 且 `body_compatible` 且时空不冲突 → 挂入，挂不上 → pending 单球组）；**suspect**=组内含 review 挂入成员（UI 提示逐张看）
  - `save_groups(work_dir, video, calib, groups_data)` / `load_groups(work_dir)` / `groups_valid(data, video, calib)`——groups.json 身份=视频+calib_key+两版本；**命名/并组等 UI 操作改内容不改身份**（手动演化不作废缓存）
  - 常量：`EDGE_SIM = 0.70`、`CHAIN_SIM = 0.55`、`ATTACH_SIM = 0.75`、`MIN_GOAL_GAP_S = 2.5`、`BODY_TOL = 0.35`、`ASPECT_TOL = 0.18`、`HEAD_BHAT = 0.55`（全部初值，Task 6 标定轮在真值上定案）

- [ ] **Step 1: 写失败测试**

```python
from bbh.appearance import (EDGE_SIM, build_groups, feat_similar, groups_valid,
                            head_splits, load_groups, save_groups)


def _hot(n=1024, at=0):
    """one-hot 归一化直方图（bin at=1.0）——合成指纹的距离可控。"""
    v = [0.0] * n
    v[at % n] = 1.0
    return v


def _mix(n=1024, a=0, b=130, wa=0.5):
    """两 bin 混合直方图——链式复检测试用（A-B、B-C 相似，A-C 不相似）。"""
    v = [0.0] * n
    v[a % n], v[b % n] = wa, 1.0 - wa
    return v


def _app(hist_top, body=4.0, head=None, ok=True):
    return Appearance(ok=ok,
                      hists={"top": hist_top, "pants": _hot(1024, 500),
                             "shoes": _hot(1024, 900)},
                      body_ratio=body, aspect=0.28, head_hist=head,
                      missing=[])


def test_相似度同高异低():
    a = _app(_hot())
    assert feat_similar(a, _app(_hot())) == pytest.approx(1.0, abs=0.02)
    # top 不同 bin（0 交集）、pants/shoes 同 bin：加权和=0.30+0.15=0.45，
    # 远低于连边阈值 0.70
    assert feat_similar(a, _app(_hot(1024, 300))) < 0.55 < EDGE_SIM


def test_头部票只拆不合():
    h1 = [0.9] + [0.0] * 31
    h2 = [0.0] * 31 + [0.9]
    # 巴氏距离：不相交≈1（明显不同→拆）、相同≈0（不拆）
    assert head_splits(_app(_hot(), head=h1), _app(_hot(), head=h2))   # 异发色→拆
    assert not head_splits(_app(_hot(), head=h1), _app(_hot(), head=h1))
    assert not head_splits(_app(_hot(), head=None), _app(_hot(), head=h1))  # 缺票不拆


def test_聚类连边成组_时空拆分_review只挂不建():
    traces, feats = {}, {}
    spec = [("01:00.0", 0), ("01:40.0", 0), ("02:20.0", 0),    # A 三球同指纹
            ("03:00.0", 400), ("03:30.0", 400),                # B 两球
            ("03:00.5", 400)]        # 与 B 撞衫且与 03:00.0 同刻——时空必拆
    for key, bin_ in spec:
        traces[key] = {"status": "high"}
        feats[key] = _app(_hot(1024, bin_))
    traces["01:10.0"] = {"status": "review"}   # review：A 的指纹，只挂不建
    feats["01:10.0"] = _app(_hot())
    out = build_groups(traces, feats)
    gs = [g for g in out if len(g["members"]) > 1]
    assert len(gs) == 2                          # A 组 3+1 球、B 组 2 球
    g_a = next(g for g in gs if "01:00.0" in g["members"])
    assert set(g_a["members"]) == {"01:00.0", "01:40.0", "02:20.0", "01:10.0"}
    assert g_a["status"] == "suspect"            # 含 review 挂入→标黄提示
    g_b = next(g for g in gs if "03:00.0" in g["members"])
    assert set(g_b["members"]) == {"03:00.0", "03:30.0"}
    solo = [g for g in out if g["members"] == ["03:00.5"]]
    assert solo and solo[0]["status"] == "ok"    # 时空拆出的 high 单列（非 pending）


def test_链式复检拆最不像():
    """A-B 相似(0.725)、B-C 相似(0.725)、A-C 不相似(0.45) → 传递并入后
    复检把 A/C 拆出，最终无 3 球同组。"""
    traces, feats = {}, {}
    for key, h in [("01:00.0", _hot()), ("01:30.0", _mix()),
                   ("02:00.0", _hot(1024, 130))]:
        traces[key] = {"status": "high"}
        feats[key] = _app(h)
    out = build_groups(traces, feats)
    assert not [g for g in out if len(g["members"]) == 3]
    # 复检后：{B,C} 一组（两两 ≥CHAIN_SIM），A 单列
    assert any(set(g["members"]) == {"01:30.0", "02:00.0"} for g in out)


def test_时空拆桥后两人组仍复检():
    """桥 B 被时空硬约束拆走后，幸存 {A,C} 相似度 0.45 < CHAIN_SIM——
    链式复检不得在 len==2 停（宁拆勿错并）。"""
    traces, feats = {}, {}
    for key, h in [("03:00.0", _hot()), ("03:00.5", _mix()),
                   ("05:00.0", _hot(1024, 130))]:
        traces[key] = {"status": "high"}
        feats[key] = _app(h)
    out = build_groups(traces, feats)
    for g in out:
        assert len(g["members"]) == 1    # 0.45 的两人组必须被拆开


def test_groups_json_往返(tmp_path):
    traces = {"01:00.0": {"status": "high"}}
    feats = {"01:00.0": _app(_hot())}
    out = build_groups(traces, feats)
    save_groups(str(tmp_path), "v.mp4", CALIB, out)
    d = load_groups(str(tmp_path))
    assert groups_valid(d, "v.mp4", CALIB)
    assert d["groups"][0]["members"] == ["01:00.0"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_appearance.py -v`
Expected: 新 5 个 FAIL（ImportError）

- [ ] **Step 3: 实现**

```python
# bbh/appearance.py 追加
EDGE_SIM = 0.70        # 连边阈值：两球颜色相似度 ≥ 此值且体型兼容才连
CHAIN_SIM = 0.55       # 组内复检阈值：成员最小两两相似度 < 此值被拆出
ATTACH_SIM = 0.75      # review 挂组阈值（比连边严——错并是最怕的错误方向）
MIN_GOAL_GAP_S = 2.5   # 时空硬约束：进球时刻差 ≤ 此值的两个射手必是不同人
BODY_TOL = 0.35        # 体型容差（人高/筐宽比率差；深度尺度差被它吸收）
ASPECT_TOL = 0.18      # 高宽比容差（贴身防守粘连失真由它吸收）
HEAD_BHAT = 0.55       # 头部票拆分门槛：巴氏距离 > 此值=发色肤色明显不同
SEG_W = {"top": 0.55, "pants": 0.30, "shoes": 0.15}


def _hist_sim(h1, h2) -> float:
    """归一化直方图交集相似度（[0,1]）。"""
    a, b = np.array(h1, np.float32), np.array(h2, np.float32)
    return float(cv2.compareHist(a, b, cv2.HISTCMP_INTERSECT))


def feat_similar(a: Appearance, b: Appearance) -> float:
    """可用段加权和（缺失段按可用段重归一）——缺一票少一票，不拿空段瞎算。"""
    pairs = [(SEG_W[n], _hist_sim(a.hists[n], b.hists[n]))
             for n in SEG_W
             if a.hists.get(n) and b.hists.get(n)]
    if not pairs:
        return 0.0
    w_sum = sum(w for w, _ in pairs)
    return sum(w * s for w, s in pairs) / w_sum


def body_compatible(a: Appearance, b: Appearance) -> bool:
    """体型 AND 条件（从严合并的第二票）。任一缺失 → True（缺票不拦）。"""
    if a.body_ratio and b.body_ratio \
            and abs(a.body_ratio - b.body_ratio) > BODY_TOL:
        return False
    if a.aspect and b.aspect and abs(a.aspect - b.aspect) > ASPECT_TOL:
        return False
    return True


def head_splits(a: Appearance, b: Appearance) -> bool:
    """头部仲裁票（只拆不合）：双方齐全且巴氏距离大（发色肤色明显不同）
    → 拆。距离小=相似不拆；任一缺票不拆（缺票不拦）。"""
    if not a.head_hist or not b.head_hist:
        return False
    d = cv2.compareHist(np.array(a.head_hist, np.float32),
                        np.array(b.head_hist, np.float32),
                        cv2.HISTCMP_BHATTACHARYYA)
    return bool(d > HEAD_BHAT)


def _goal_t(key: str) -> float:
    m, s = key.split(":")
    return int(m) * 60 + float(s)


def build_groups(traces: dict, feats: dict) -> list:
    """聚类（纯内存，秒级）：high+有效指纹连边建组 → 链式复检 → review
    只挂不建。返回组列表（id G1..、members 行键、status ok/suspect/pending）。
    宁拆勿错并：拆开=同人命名两次；错并=一串球全标错人。"""
    builders = [k for k, v in (traces or {}).items()
                if v.get("status") == "high"
                and (feats.get(k) or Appearance(ok=False)).ok]
    # ---- 连通分量（并查集）----
    parent = {k: k for k in builders}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    for i, k1 in enumerate(builders):
        for k2 in builders[i + 1:]:
            a, b = feats[k1], feats[k2]
            if (feat_similar(a, b) >= EDGE_SIM and body_compatible(a, b)
                    and not head_splits(a, b)):
                union(k1, k2)
    comps: dict[str, list] = {}
    for k in builders:
        comps.setdefault(find(k), []).append(k)
    # ---- 时空硬约束（只拆不合，组内后验）：进球时刻几乎相同的两个射手
    # 必是不同人。不做边过滤——边过滤挡不住传递并组（A 与 C 同刻、经 B
    # 仍进同组）。拆冲突数最多的成员（平手取进球更晚者），重复至无冲突。----
    def _split_conflicts(members: list) -> tuple[list, list]:
        kept, removed = list(members), []
        while True:
            conflict = {m: 0 for m in kept}
            for i, m1 in enumerate(kept):
                for m2 in kept[i + 1:]:
                    if abs(_goal_t(m1) - _goal_t(m2)) <= MIN_GOAL_GAP_S:
                        conflict[m1] += 1
                        conflict[m2] += 1
            bad = [m for m, c in conflict.items() if c]
            if not bad:
                return kept, removed
            worst = max(bad, key=lambda m: (conflict[m], _goal_t(m)))
            kept.remove(worst)
            removed.append(worst)

    groups = []
    for members in comps.values():
        kept, removed = _split_conflicts(members)
        groups.extend({"members": [m]} for m in removed)
        members = kept
        # ---- 链式复检：组内两两最小相似度 < CHAIN_SIM 的最低者拆出 ----
        while len(members) > 1:   # 两人组也查——时空拆桥/复检收缩幸存的对
                                   # 可能无直达边（<CHAIN_SIM），直达边对
                                   # ≥EDGE_SIM>CHAIN_SIM 不受影响
            sims = {m: min(feat_similar(feats[m], feats[o])
                           for o in members if o != m) for m in members}
            worst = min(sims, key=sims.get)
            if sims[worst] >= CHAIN_SIM:
                break
            members = [m for m in members if m != worst]
            groups.append({"members": [worst]})   # 拆出单列，下轮与 review 一起挂
        groups.append({"members": members})
    groups.sort(key=lambda g: -len(g["members"]))
    # ---- review 只挂不建 ----
    pending = []
    for k, v in (traces or {}).items():
        if v.get("status") != "review":
            continue
        f = feats.get(k)
        best = None
        if f and f.ok:
            for g in groups:
                if any(abs(_goal_t(k) - _goal_t(m)) <= MIN_GOAL_GAP_S
                       for m in g["members"]):
                    continue
                if any(feat_similar(f, feats[m]) >= ATTACH_SIM
                       and body_compatible(f, feats[m])
                       for m in g["members"] if (feats.get(m) or Appearance(ok=False)).ok):
                    best = g
                    break
        if best:
            best["members"].append(k)
        else:
            pending.append(k)
    # ---- 编号与状态 ----
    out = []
    for i, g in enumerate(groups, 1):
        has_review = any((traces.get(m) or {}).get("status") == "review"
                         for m in g["members"])
        out.append({"id": f"G{i}", "members": sorted(g["members"]),
                    "status": "suspect" if has_review else "ok"})
    out += [{"id": f"P{j}", "members": [k], "status": "pending"}
            for j, k in enumerate(pending, 1)]
    return out


def save_groups(work_dir, video, calib, groups: list) -> None:
    os.makedirs(work_dir, exist_ok=True)
    p = os.path.join(work_dir, GROUPS_FILE)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"video": video, "calib": _calib_key(calib),
                   "feat_ver": FEAT_VERSION, "cluster_ver": CLUSTER_VERSION,
                   "groups": groups}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def load_groups(work_dir) -> dict | None:
    try:
        with open(os.path.join(work_dir, GROUPS_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def groups_valid(data: dict | None, video: str, calib) -> bool:
    """身份=视频+标定指纹+两版本。命名/并组等 UI 手动演化**不改身份**
    （groups.json 是活数据不是推导缓存，重开页面不重聚）。"""
    return bool(data) and data.get("video") == video \
        and data.get("calib") == _calib_key(calib) \
        and data.get("feat_ver") == FEAT_VERSION \
        and data.get("cluster_ver") == CLUSTER_VERSION
```

（`build_groups` 里被链式复检拆出的单成员组没有 id/status——它们进了 `groups` 列表后统一在编号循环处理，覆盖到。）

- [ ] **Step 4: 跑测试确认通过**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_appearance.py -v`
Expected: 全 PASS（15 个）

- [ ] **Step 5: 提交**

```bash
git add bbh/appearance.py tests/test_appearance.py
git commit -m "feat: 聚类——连通分量+体型AND+头部仲裁只拆不合+时空硬约束+链式复检+review 只挂不建；groups.json 手动演化不作废身份"
```

---

### Task 6b: 标定善后——_hist_sim 契约修复 + 归人护栏助手 + 聚类常量下线

**不是全新功能**：原 Task 6（真值标定）已执行完毕且得出"颜色无分离度、连边建组下线"的结论（证据：`.superpowers/sdd/2026-08-29-appearance-clustering/progress.md` ①-⑬，标定中同时发现 `_hist_sim` 破 [0,1] 契约的 bug——跨人 p95=2.31 即其产物）。本任务落地三件善后。

**Files:**
- Modify: `bbh/appearance.py`（`_hist_sim` 约 278 行；常量块约 268-275 行；`_goal_t` 约 316 行下方追加 `gap_violations`）
- Test: `tests/test_appearance.py`（文件末追加；顶部 import 区补缺）

**Interfaces:**
- Consumes: `_seg_hist` 产出 **L2 归一化**直方图（Σbin 可 >1，均匀分布 Σ=√n）；`_goal_t(key)`（"mm:ss.s"→秒）；常量 `MIN_GOAL_GAP_S = 2.5`
- Produces（Task 7/8 依赖，签名不得变）:
  - `_hist_sim(h1, h2) -> float`——语义不变，但交集前先 L1 归一化，严格落 [0,1]
  - `gap_violations(keys: list[str], gap_s: float = MIN_GOAL_GAP_S) -> list[tuple[str, str, float]]`——按时刻排序后**相邻对**间隔 < gap_s 的违例列表 `(key1, key2, 间隔秒)`（排序后只查相邻对足够：非相邻对 <gap 必然夹着更小的相邻间隔）
  - 常量块注释更新（值不动——Task 5 聚类测试仍依赖）

- [ ] **Step 1: 写失败测试**

`tests/test_appearance.py` 末尾追加（顶部 import 区补 `import numpy as np` 与 `from bbh.appearance import gap_violations, _hist_sim`——若已有 `from bbh.appearance import (...)` 行则并入，numpy 已有则跳过）：

```python
def test_hist_sim_契约_交集前L1归一化():
    # _seg_hist 实际产出是 L2 归一化：均匀分布 Σbin=√32>1，裸交集破 [0,1]
    h = np.full(32, 1.0 / np.sqrt(32), dtype=np.float32)
    assert _hist_sim(h, h) == pytest.approx(1.0)          # 自身=1（修复前=√32）
    g = np.zeros(32, dtype=np.float32); g[0] = 2.0        # 非归一化 one-hot
    z = np.zeros(32, dtype=np.float32); z[1] = 3.0        # 不同 bin
    assert _hist_sim(g, z) == pytest.approx(0.0)
    assert _hist_sim(g, g) == pytest.approx(1.0)          # 与幅度无关
    assert _hist_sim(g, h) <= 1.0 + 1e-6                  # 恒 ≤1


def test_gap_violations_护栏():
    keys = ["01:00.0", "01:02.0", "05:00.0"]
    assert gap_violations(keys) == [("01:00.0", "01:02.0", 2.0)]  # 2.0s<2.5
    assert gap_violations(["01:00.0", "01:03.3"]) == []  # 3.3s=两场同人最短
    assert gap_violations(["01:00.0", "01:01.0", "01:01.5"]) == [
        ("01:00.0", "01:01.0", 1.0), ("01:01.0", "01:01.5", 0.5)]
    assert gap_violations([]) == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_appearance.py -v`
Expected: 新 2 个 FAIL（`_hist_sim(h,h)`=√32≈5.657≠1；`gap_violations` ImportError）

- [ ] **Step 3: 实现**

`bbh/appearance.py`——`_hist_sim` 整函数替换（约 278 行；numpy/cv2 顶部已 import）：

```python
def _hist_sim(h1, h2):
    """分段直方图相似度 [0,1]。_seg_hist 产出是 L2 归一化（Σbin 可 >1，
    均匀分布 Σ=√n），裸交集会破 [0,1] 契约——交集前先 L1 归一化。
    （2026-08-29 Task 6b 修复；Task 6 标定口径未归一、绝对值偏大，
    分布重叠的相对结论不变。）"""
    a = np.asarray(h1, dtype=np.float32)
    b = np.asarray(h2, dtype=np.float32)
    a = a / (float(a.sum()) or 1.0)
    b = b / (float(b.sum()) or 1.0)
    return float(cv2.compareHist(a, b, cv2.HISTCMP_INTERSECT))
```

`_goal_t` 下方追加：

```python
def gap_violations(keys, gap_s: float = MIN_GOAL_GAP_S):
    """归人护栏（spec 第二轮修订：聚类拆分约束转手动归人护栏，警示不阻断）。
    球 key 列表内最短间隔违例——排序后只查相邻对：非相邻对 <gap 必然
    夹着更小的相邻间隔。返回 [(key1, key2, 间隔秒)]。"""
    ts = sorted((k, _goal_t(k)) for k in keys or [])
    return [(k1, k2, round(t2 - t1, 2))
            for (k1, t1), (k2, t2) in zip(ts, ts[1:]) if t2 - t1 < gap_s]
```

常量块（约 268-275 行）整块注释替换——**值一个不改**（Task 5 测试断言依赖），只换注释：

```python
# ---- 聚类阈值（2026-08-29 Task 6 两场真值标定结论：同人/不同人相似度
# 分布重叠、无分离度——连边建组下线，UI/生产路径不再调用 build_groups。
# 常量与代码保留仅供调试；证据：spec 第二轮修订拍板表 + progress.md ①-⑬）----
EDGE_SIM = 0.70        # （下线）原连边阈值
CHAIN_SIM = 0.55       # （下线）原链式复检阈值
ATTACH_SIM = 0.75      # （下线）原 review 挂组阈值
MIN_GOAL_GAP_S = 2.5   # 保留：唯一真值存活约束（两场同人最短间隔 3.3s），
                       # 转手动归人护栏（gap_violations）
BODY_TOL = 0.35        # （下线）原体型 AND 票容差
ASPECT_TOL = 0.18      # （下线）原高宽比容差
HEAD_BHAT = 0.55       # （下线）原头部仲裁票阈值
```

（SEG_W 等其余常量原样保留；以上仅注释变更，若有常量名/值与实际文件不符，以实际文件为准只改注释。）

- [ ] **Step 4: 跑测试确认通过**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_appearance.py -v`
Expected: 全 PASS（Task 5 既有聚类测试不受影响——其夹具 Σ=1，L1 归一化前后值不变）

再跑全套 `./.venv/Scripts/python.exe -X utf8 -m pytest -q` → 零回归。

- [ ] **Step 5: 提交**

```bash
git add bbh/appearance.py tests/test_appearance.py
git commit -m "fix: _hist_sim 交集前 L1 归一化修复 [0,1] 契约（_seg_hist 产出 L2 归一化 Σ可>1，裸交集即 Task 6 跨人 p95=2.31 之源）；gap_violations 归人护栏助手；聚类阈值常量标注下线（两场标定证伪）"
```

---

### Task 7: 建议钩子——bbh/suggest.py 颜色排序提示（恒 low）+ 真值重放评估工装

**Files:**
- Create: `bbh/suggest.py`、`tools/eval_suggestions.py`
- Test: `tests/test_suggest.py`（新建）

**Interfaces:**
- Consumes: `bbh.appearance.Appearance`（字段 `ok/hists/body_ratio/aspect/head_hist/missing/err`，`from_dict`）；`bbh.appearance.feat_similar(a1, a2) -> float`（Task 6b 修复后 [0,1]，可用段加权和+缺失重归一）；`bbh.appearance.load_features(work_dir) -> dict | None`（原始 dict：`{"video","calib","feat_ver","feats":{key: dict}}`，还原用 `Appearance.from_dict`）；`bbh.models.Calibration`（`load(path)` 与 `hoop_rect/hsv_low/hsv_high/frame_size/ref_hash` 关键字构造——先读 models.py 核对）
- Produces（Task 8 依赖，签名不得变）:
  - `@dataclass Suggestion`：`balls: list[str]`、`person: str | None = None`、`signal: str = "color_rank"`、`confidence: str = "low"`（**硬编码恒 low，spec 护栏 2**）、`evidence: dict = field(default_factory=dict)`
  - `color_neighbors(target: str, feats: dict[str, Appearance], manual_groups: dict | None = None, k: int = 5) -> list[tuple[str, float]]`——按 feat_similar 降序前 k；已归人球（manual_groups 值并集）与目标自身剔除；`!ok`/缺失球跳过；目标不在 feats 或 !ok → []
  - `suggest_groups(feats, manual_groups=None, k=3) -> list[Suggestion]`——每个已归人取其**最新**有效球（key 字典序降序第一个 `ok`）为锚，未归人球中前 k 个；无已归人 → []
  - `evaluate_neighbors(feats, truth_shooters: dict, k=5) -> dict`——`{"precision_at_k": float, "hit_rate": float, "checked": int, "k": int}`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_suggest.py
"""suggest.py 单测：合成指纹（段内 Σ=1 的 {bin:权重} 构造，口径同
test_appearance 夹具——one-hot/混合不受 _hist_sim L1 修复影响）。"""
import pytest

from bbh.appearance import Appearance
from bbh.suggest import (Suggestion, color_neighbors, evaluate_neighbors,
                         suggest_groups)


def _apph(top=None, pants=None, shoes=None, ok=True):
    """top/pants/shoes: {bin: 权重}（缺省该段 one-hot bin0）。"""
    def mix(d):
        d = d or {0: 1.0}
        h = [0.0] * 1024
        for b, w in d.items():
            h[b] = w
        return h
    return Appearance(ok=ok, hists={"top": mix(top), "pants": mix(pants),
                                    "shoes": mix(shoes)},
                      body_ratio=4.0, aspect=0.3, head_hist=None,
                      missing=[], err=None)


def test_color_neighbors_排序与剔除():
    feats = {"a": _apph(top={10: 1.0}), "b": _apph(top={10: 0.6, 11: 0.4}),
             "c": _apph(top={500: 1.0}), "d": _apph(top={10: 0.8, 12: 0.2})}
    nbrs = color_neighbors("a", feats, None, k=2)
    assert [k for k, _ in nbrs] == ["d", "b"]          # 0.8 > 0.6（top 段）
    assert nbrs[0][1] >= nbrs[1][1] > 0
    # 手动已归人的球不再出现（护栏 3：手动永远赢）
    nbrs2 = color_neighbors("a", feats, {"老陈": ["d"]}, k=5)
    assert "d" not in [k for k, _ in nbrs2]
    assert "a" not in [k for k, _ in nbrs2]            # 目标自身剔除
    assert color_neighbors("x", feats) == []           # 目标不在 feats
    bad = dict(feats); bad["e"] = _apph(ok=False)      # 缺失特征跳过
    assert "e" not in [k for k, _ in color_neighbors("a", bad)]


def test_suggest_groups_锚球与恒low():
    feats = {"a": _apph(top={10: 1.0}), "b": _apph(top={10: 0.6, 11: 0.4}),
             "c": _apph(top={500: 1.0})}
    assert suggest_groups(feats, None) == []           # 无人已归人 → 无建议
    out = suggest_groups(feats, {"老陈": ["a"]}, k=1)
    assert len(out) == 1 and out[0].person == "老陈"
    assert out[0].confidence == "low" and out[0].signal == "color_rank"
    assert out[0].balls == ["b"]                       # 最像锚 a 的未归人球
    assert out[0].evidence["anchor"] == "a"


def test_evaluate_neighbors_真值重放():
    feats = {"a": _apph(top={10: 1.0}), "b": _apph(top={10: 0.9, 11: 0.1}),
             "c": _apph(top={500: 1.0})}
    truth = {"a": "甲", "b": "甲", "c": "乙"}
    r = evaluate_neighbors(feats, truth, k=1)
    assert r["checked"] == 3 and r["k"] == 1
    assert r["hit_rate"] == pytest.approx(2 / 3)       # a↔b 同人互中，c 落空
    assert r["precision_at_k"] == pytest.approx(2 / 3)
    assert evaluate_neighbors({}, truth)["checked"] == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_suggest.py -v`
Expected: 全 FAIL（ModuleNotFoundError: bbh.suggest）

- [ ] **Step 3: 实现**

```python
# bbh/suggest.py
"""建议钩子（spec 第二轮修订§建议钩子）：颜色排序提示，恒 low、
永不自动生效；已手动归人的球不出现（手动永远赢）。"""
from dataclasses import dataclass, field

from bbh.appearance import Appearance, feat_similar


@dataclass
class Suggestion:
    balls: list[str]                 # 建议 same-person 的球 key
    person: str | None = None        # 该人已在 manual_groups 则带名字
    signal: str = "color_rank"
    confidence: str = "low"          # 硬编码恒 low（spec 护栏 2，不可配置）
    evidence: dict = field(default_factory=dict)


def _assigned(manual_groups: dict | None) -> set[str]:
    """manual_groups {person: [key,...]} → 已归人球集合。"""
    return {k for keys in (manual_groups or {}).values() for k in keys}


def color_neighbors(target, feats, manual_groups=None, k=5):
    """选中球的颜色相近提示：其余球按 feat_similar 降序前 k。
    两场精度≈随机（AUC 0.53-0.56）——排序提示定位，永不单独定身份。"""
    base = feats.get(target)
    if base is None or not base.ok:
        return []
    done = _assigned(manual_groups) | {target}
    sims = [(key, feat_similar(base, f)) for key, f in feats.items()
            if key not in done and f.ok]
    sims.sort(key=lambda t: -t[1])
    return sims[:k]


def suggest_groups(feats, manual_groups=None, k=3):
    """每个已归人 → 未归人球中颜色最像其锚球（最新有效球）的前 k 个。"""
    out = []
    for person, keys in (manual_groups or {}).items():
        anchor = None
        for key in sorted(keys, reverse=True):        # key 定宽 mm:ss.s，降序=最新
            f = feats.get(key)
            if f is not None and f.ok:
                anchor = (key, f)
                break
        if anchor is None:
            continue
        done = _assigned(manual_groups)
        sims = [(key2, feat_similar(anchor[1], f)) for key2, f in feats.items()
                if key2 not in done and f.ok]
        sims.sort(key=lambda t: -t[1])
        if sims:
            out.append(Suggestion(balls=[s[0] for s in sims[:k]], person=person,
                                  evidence={"anchor": anchor[0],
                                            "sims": [[s[0], round(s[1], 3)]
                                                     for s in sims[:k]]}))
    return out


def evaluate_neighbors(feats, truth_shooters, k=5):
    """真值重放基线：每球取 color_neighbors（视作无手动归人），
    precision@k=top-k 里同真值人占比；hit_rate=top-k 含至少一个同人。
    预期≈随机——作基线报告，不设 gate（spec§评估工装）。"""
    truth = truth_shooters or {}
    prec, hit, n = [], 0, 0
    for key, name in truth.items():
        f = feats.get(key)
        if f is None or not f.ok:
            continue
        nbrs = color_neighbors(key, feats, None, k)
        if not nbrs:
            continue
        n += 1
        same = sum(1 for key2, _ in nbrs if truth.get(key2) == name)
        prec.append(same / len(nbrs))
        hit += (same > 0)
    return {"precision_at_k": (sum(prec) / len(prec)) if prec else 0.0,
            "hit_rate": (hit / n) if n else 0.0, "checked": n, "k": k}
```

（若 `Appearance` 未用到可从 import 去掉——类型注解未标它时保留 feat_similar 即可。）

`tools/eval_suggestions.py`（薄 CLI，逻辑全在 suggest.py；惯例照 `tools/check_shooters.py`：`sys.path` 补根、退出码恒 0）：

```python
# tools/eval_suggestions.py
"""颜色建议真值重放基线报告（spec§评估工装）。预期精度≈随机——只作基线不设 gate。
用法:
  ./.venv/Scripts/python.exe -X utf8 tools/eval_suggestions.py work/2026-08-22_1 truth/2026-08-22_1.json
  ./.venv/Scripts/python.exe -X utf8 tools/eval_suggestions.py work/2026-08-21_1 truth/2026-08-21_1.json --snap work/2026-08-21_1/positions.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bbh.appearance import Appearance, build_features, load_features  # noqa: E402
from bbh.models import Calibration  # noqa: E402
from bbh.suggest import evaluate_neighbors  # noqa: E402


def _calib_from_snap(path):
    snap = json.load(open(path, encoding="utf-8"))["calib"]
    return Calibration(hoop_rect=tuple(snap[0]), hsv_low=tuple(snap[1]),
                       hsv_high=tuple(snap[2]), frame_size=tuple(snap[3]),
                       ref_hash=snap[4])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work_dir")
    ap.add_argument("truth_path")
    ap.add_argument("--snap", default=None,
                    help="08-21 存档标定 positions.json（该场不可用全局 calibration.json）")
    ap.add_argument("-k", type=int, default=5)
    args = ap.parse_args()
    sh = json.load(open(f"{args.work_dir}/shooters.json", encoding="utf-8"))
    calib = _calib_from_snap(args.snap) if args.snap \
        else Calibration.load("calibration.json")
    fd = load_features(args.work_dir)
    feats = {k: Appearance.from_dict(v)
             for k, v in (fd or {}).get("feats", {}).items()}
    if not feats:
        print("features.json 缺失——重提（每球 ~1s）…")
        for _ in build_features(sh["video"], calib, args.work_dir, sh["traces"]):
            pass
        feats = {k: Appearance.from_dict(v) for k, v in
                 load_features(args.work_dir)["feats"].items()}
    truth = json.load(open(args.truth_path, encoding="utf-8"))["shooters"]
    r = evaluate_neighbors(feats, truth, k=args.k)
    print(f"{args.work_dir} 颜色建议基线（恒 low，不设 gate）")
    print(f"  precision@{r['k']} = {r['precision_at_k']:.3f}")
    print(f"  hit_rate       = {r['hit_rate']:.3f}")
    print(f"  checked        = {r['checked']} 球")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_suggest.py -v` → 全 PASS
再 `./.venv/Scripts/python.exe -X utf8 -m pytest -q` → 零回归。

- [ ] **Step 5: 提交**

```bash
git add bbh/suggest.py tools/eval_suggestions.py tests/test_suggest.py
git commit -m "feat: 建议钩子 suggest.py——颜色排序提示恒 low+手动永远赢(已归人球剔除)；eval_suggestions 真值重放基线工装(precision@k/hit_rate 不设 gate)"
```

---

### Task 8: 手动归人一等公民——bbh/manual_groups.py + 归人面板布线

**Files:**
- Create: `bbh/manual_groups.py`、`tests/test_manual_groups.py`
- Modify: `bbh/app.py`（`run_shooter_tracing` 约 948 行起；布局约 1233 行 `shooter_md` 处；布线约 1379 行；import 区补 `from bbh import appearance, manual, suggest`——appearance 若已导入只补缺）

**Interfaces:**
- Consumes: Task 6b `gap_violations`/`MIN_GOAL_GAP_S`；Task 7 `color_neighbors`；`app._load_assignments/_save_assignments/_raw_time_keys/_work_dir_for/clean_path`（读函数体确认行为后再用）；`app.shooter.load_shooters(work)`；标签库读取（`app.py` 搜 `TAGS_FILE`——现有标签下拉的 choices 来源）；`run_shooter_tracing` 是产出 `(shooter_md, btn_shooter)` 两路更新的生成器（先读现函数确认 yield 形状）
- Produces:
  - `manual.load(work_dir) -> dict[str, list[str]]`（损坏/缺失 → {}，UI 不崩）
  - `manual.save(work_dir, groups)`（`.tmp`+`os.replace` 原子写，`sort_keys=True`）
  - `manual.assign(work_dir, person, keys) -> dict`——改判语义：keys 并入 person 并**从其他人移出**（球同时只能在一人）；空人名/空 keys 原样返回不落盘；去重保序；移空的组删除
  - `manual.unassign(work_dir, keys) -> dict`——keys 从任何人的组删除（只动 manual_groups，不碰已打标签）
  - `app._selected_keys(video_path, rows) -> list[str]`——表格「选中」列（r[0]）勾选的球 key
  - `app.render_manual_payload(video_path, rows) -> tuple[str, list]`——(面板 HTML, person_dd choices=标签库∪已归人人名)
  - `app.assign_person(video_path, rows, person) -> tuple`——4 路 `(msg, rows, *render_manual_payload)`：manual.assign + rows 标签列 r[6] 更新 + `_save_assignments` 双写 + 护栏警示（不 raise 不阻断）
  - `app.unassign_person(video_path, rows) -> tuple`——同型 4 路
  - `run_shooter_tracing` 尾部接 `appearance.build_features` 生成器转发进度；**不调用 build_groups**（聚类下线）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_manual_groups.py
"""manual_groups.py 单测 + app.assign_person 行为级（tmp work 目录+monkeypatch）。"""
import json

from bbh import manual


def test_归人与改判语义(tmp_path):
    manual.assign(str(tmp_path), "老陈", ["01:00.0", "01:40.0"])
    manual.assign(str(tmp_path), "小左", ["02:00.0"])
    g = manual.assign(str(tmp_path), "小左", ["01:40.0", "02:00.0"])  # 改判
    assert g == {"老陈": ["01:00.0"], "小左": ["01:40.0", "02:00.0"]}
    g2 = manual.assign(str(tmp_path), "小左", ["01:40.0"])   # 去重幂等
    assert g2["小左"] == ["01:40.0", "02:00.0"]
    assert manual.assign(str(tmp_path), "", ["03:00.0"]) == g2  # 空人名不落盘
    assert manual.assign(str(tmp_path), "老陈", []) == g2        # 空keys不落盘


def test_移出与容错(tmp_path):
    manual.assign(str(tmp_path), "老陈", ["01:00.0", "01:40.0"])
    g = manual.unassign(str(tmp_path), ["01:00.0"])
    assert g == {"老陈": ["01:40.0"]}
    assert manual.unassign(str(tmp_path), ["09:99.9"]) == g   # 不在组=无操作
    assert manual.unassign(str(tmp_path), ["01:40.0"]) == {}  # 空组清理
    (tmp_path / "manual_groups.json").write_text("{坏json", encoding="utf-8")
    assert manual.load(str(tmp_path)) == {}                   # 损坏容错
    assert manual.unassign(str(tmp_path), []) == {}


def test_身份与特征版本解耦(tmp_path):
    """manual_groups.json 只挂人名+球 key，不含任何版本字段——
    特征重算/FEAT_VERSION 变化不作废（spec：手动标签是事实不是缓存）。"""
    manual.assign(str(tmp_path), "老陈", ["01:00.0"])
    raw = (tmp_path / "manual_groups.json").read_text(encoding="utf-8")
    assert json.loads(raw) == {"老陈": ["01:00.0"]}
    assert "feat_ver" not in raw and "calib" not in raw


def test_assign_person_双写与护栏(tmp_path, monkeypatch):
    from bbh import app
    monkeypatch.setattr(app, "_work_dir_for", lambda vp: str(tmp_path))
    monkeypatch.setattr(app, "_raw_time_keys",
                        lambda vp, rows: [r[1] for r in rows])
    (tmp_path / "shooters.json").write_text(json.dumps(
        {"video": "v.mp4", "ver": 3, "traces": {
            "01:00.0": {"thumbs": [], "status": "high"},
            "01:02.0": {"thumbs": [], "status": "high"},
            "05:00.0": {"thumbs": [], "status": "high"}}}), encoding="utf-8")
    rows = [[1, "01:00.0", "high", "0.9", 5, 2, ""],
            [1, "01:02.0", "high", "0.9", 6, 2, ""],
            [0, "05:00.0", "high", "0.9", 7, 2, ""]]
    msg, rows2, html, choices = app.assign_person("v.mp4", rows, "老陈")
    assert "护栏" in msg and "01:00.0" in msg        # 2.0s < 2.5s → 警示
    assert manual.load(str(tmp_path)) == {"老陈": ["01:00.0", "01:02.0"]}
    raw = (tmp_path / app.ASSIGN_FILE).read_text(encoding="utf-8")
    assert "老陈" in raw and "01:00.0" in raw and "01:02.0" in raw  # 双写
    assert rows2[0][6] == "老陈" and rows2[1][6] == "老陈"
    assert rows2[2][6] == ""                          # 未勾选不动
    assert "老陈" in choices and "手动归人" in html
```

（`app.ASSIGN_FILE` 若不是纯文件名常量而是含路径，按实际改取法；`_save_assignments` 落盘形状以函数体为准——最低断言=文件出现且含人名与两个球 key。）

- [ ] **Step 2: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_manual_groups.py -v`
Expected: 全 FAIL（ModuleNotFoundError: bbh.manual / AttributeError: assign_person——import app 耗时但无副作用）

- [ ] **Step 3: 实现**

```python
# bbh/manual_groups.py
"""手动归人（spec 第二轮修订一等公民）：manual_groups.json 读写+归人/改判/撤销。
身份只挂视频名+球 key——与 features/groups 版本完全解耦，特征重算不作废
（手动标签是事实不是缓存）。"""
import json
import os

GROUPS_FILE = "manual_groups.json"


def load(work_dir) -> dict:
    """{person: [球key,...]}；损坏/缺失 → {}（UI 不崩）。"""
    try:
        with open(os.path.join(work_dir, GROUPS_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(p): [str(k) for k in ks]
            for p, ks in data.items() if isinstance(ks, list)}


def save(work_dir, groups: dict) -> None:
    p = os.path.join(work_dir, GROUPS_FILE)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(groups, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, p)


def assign(work_dir, person: str, keys: list) -> dict:
    """归人/改判：keys 并入 person 并从其他人移出（球同时只能在一人）。
    空人名/空 keys → 原样返回不落盘。返回更新后 groups。"""
    if not person or not keys:
        return load(work_dir)
    groups = load(work_dir)
    keys = list(dict.fromkeys(keys))                 # 去重保序
    ks = set(keys)
    for p in list(groups):
        if p != person:
            groups[p] = [k for k in groups[p] if k not in ks]
            if not groups[p]:
                del groups[p]
    groups.setdefault(person, [])
    for k in keys:
        if k not in groups[person]:
            groups[person].append(k)
    save(work_dir, groups)
    return groups


def unassign(work_dir, keys: list) -> dict:
    """移出：keys 从任何人的组里删除（只动 manual_groups，不碰已打标签）。"""
    if not keys:
        return load(work_dir)
    groups = load(work_dir)
    ks = set(keys)
    for p in list(groups):
        groups[p] = [k for k in groups[p] if k not in ks]
        if not groups[p]:
            del groups[p]
    save(work_dir, groups)
    return groups
```

`bbh/app.py`——`_shooter_block_html` 之后追加（`urllib.parse`/`os` 已有 import 则不重复）：

```python
# ---------- 🧍 手动归人（Phase 2 修订：一等公民，spec 第二轮修订） ----------
def _selected_keys(video_path, rows):
    """表格「选中」列勾选的球 key（行结构 r[0]=选中，见 events_table）。"""
    keys = _raw_time_keys(video_path, rows)
    return [keys[i] for i, r in enumerate(rows or [])
            if i < len(keys) and r[0]]


def _thumb_img(work, traces, key):
    """该球第一张缩略图 HTML（无则空串）。URL 款式同 _shooter_block_html。"""
    for rel in (traces.get(key) or {}).get("thumbs") or []:
        p = os.path.join(work, rel)
        if os.path.exists(p):
            url = ("/gradio_api/file=" + urllib.parse.quote(
                os.path.abspath(p).replace("\\", "/"), safe=":")
                + f"?v={os.path.getmtime(p):.0f}")
            return (f'<img src="{url}" style="max-height:110px;'
                    ' border-radius:4px; margin:2px">')
    return ""


def _tag_choices(video_path):
    """标签库人名 choices（现有 tags.json 读取逻辑抽此函数；缺失/损坏 → []）。"""
    # 实现时把 app.py 现有标签下拉的数据源搬进来/复用其函数，保持容错
    ...


def render_manual_payload(video_path, rows):
    """归人面板：人→球缩略图墙 + 选中球的颜色相近提示（恒 low）+
    人名下拉 choices（标签库 ∪ 已归人人名）。"""
    video_path = clean_path(video_path)
    work = _work_dir_for(video_path)
    data = shooter.load_shooters(work)
    traces = (data or {}).get("traces") or {}
    groups = manual.load(work)
    html = ["<div style='font-size:12.5px'><b>🧍 手动归人</b>——"
             "表格勾球 → 选/输入人名 → 归人；同人球自动成组</div>"]
    if not groups:
        html.append("<div style='color:#888'>还没有归人记录——勾球归人后"
                    "这里按人显示</div>")
    for person in sorted(groups):
        imgs = "".join(_thumb_img(work, traces, k) for k in groups[person])
        html.append(f'<div style="margin:6px 0; padding:6px; border:1px solid '
                    f'#e5e7eb; border-radius:8px"><b>{person}</b> '
                    f'{len(groups[person])}球<br>{imgs}</div>')
    sel = _selected_keys(video_path, rows)
    if sel:
        fd = appearance.load_features(work)
        if fd:
            feats = {k: appearance.Appearance.from_dict(v)
                     for k, v in (fd.get("feats") or {}).items()}
            nbrs = suggest.color_neighbors(sel[0], feats, groups, k=5)
            if nbrs:
                tips = "、".join(f"{k}（{s:.2f}）" for k, s in nbrs)
                html.append(f"<div style='color:#888; margin-top:6px'>"
                            f"🔎 与 {sel[0]} 颜色相近（恒 low，仅供参考）："
                            f"{tips}</div>")
    choices = sorted(set(groups) | set(_tag_choices(video_path)))
    return "".join(html), choices


def assign_person(video_path, rows, person):
    """勾选球归人（改判=自动从别人移出）+ 双写 tag_assignments +
    MIN_GOAL_GAP 护栏警示（警示不阻断，手动永远赢）。"""
    sel = _selected_keys(video_path, rows)
    if not sel:
        raise gr.Error("先在表格勾选要归人的球")
    person = (person or "").strip()
    if not person:
        raise gr.Error("选或输入一个人名")
    video_path = clean_path(video_path)
    manual.assign(_work_dir_for(video_path), person, sel)
    keys = _raw_time_keys(video_path, rows)
    for i, r in enumerate(rows or []):
        if i < len(keys) and keys[i] in sel:
            r[6] = person
    _save_assignments(video_path, rows)
    gaps = appearance.gap_violations(sel)
    warn = (f"\n⚠️ 护栏：{' / '.join(f'{a}↔{b} 仅 {g}s' for a, b, g in gaps)}"
            f" < {appearance.MIN_GOAL_GAP_S}s，物理上不太可能同人，请核对"
            ) if gaps else ""
    return f"✅ {len(sel)} 球 → {person}{warn}", rows, \
        *render_manual_payload(video_path, rows)


def unassign_person(video_path, rows):
    """勾选球移出所属人——只动 manual_groups，已打标签不动。"""
    sel = _selected_keys(video_path, rows)
    if not sel:
        raise gr.Error("先勾选要移出的球")
    manual.unassign(_work_dir_for(clean_path(video_path)), sel)
    return (f"↩️ {len(sel)} 球已移出归人（已打标签未动，清标签走标签下拉）",
            rows, *render_manual_payload(video_path, rows))
```

`_tag_choices` 实现指引（不许留 `...`）：读 app.py 中现有标签下拉（约 1277 行 `row_tag_dd` 附近）的 choices 构造——它读 `TAGS_FILE`（人名库 json）。把那段读取搬进 `_tag_choices` 并保持原有容错（缺失/损坏 → []）。若已存在等价函数，直接改名复用。

`run_shooter_tracing` 尾部（溯源 summary 组装完、return 前——**先读原函数**确认 yield 元组形状与 calib 变量名，下例按 `(msg, btn_update)` 两路）：

```python
    # Phase 2（修订版）：溯源完成 → 外观特征提取（颜色提示用；聚类已下线）
    traces_cached = shooter.load_shooters(_work_dir_for(video_path))
    if traces_cached and traces_cached.get("traces"):
        fgen = appearance.build_features(video_path, calib,
                                         _work_dir_for(video_path),
                                         traces_cached["traces"])
        try:
            while True:
                try:
                    yield next(fgen), gr.update(interactive=False)
                except StopIteration as st:
                    fmsg = st.value or ""
                    break
        except Exception as ex:
            fmsg = f"⚠️ 外观提取中断：{ex}（重跑可续）"
        summary = summary + f"\n{fmsg}\n🧍 特征就绪——下方表格勾球归人"
```

布局（约 1233 行 `shooter_md = gr.Markdown("")` 之后追加）：

```python
                    manual_md = gr.HTML("")
                    with gr.Row():
                        person_dd = gr.Dropdown(label="归人（选/输入人名）",
                                                allow_custom_value=True)
                        btn_assign = gr.Button("✅ 归人", size="sm")
                        btn_unassign = gr.Button("↩️ 移出", size="sm")
```

布线（约 1379 行 `btn_shooter.click` 处追加；**凡改 `rows_state` 的回调，outputs 必须照 app.py 现有标签下拉 `row_tag_dd` 的 click/ change 布线逐项抄齐表格刷新 outputs**——漏了表格不刷新）：

```python
                    video_path.change(render_manual_payload,
                                      [video_path, rows_state],
                                      [manual_md, person_dd])
                    btn_shooter.click(render_manual_payload,
                                      [video_path, rows_state],
                                      [manual_md, person_dd])
                    btn_assign.click(assign_person,
                                     [video_path, rows_state, person_dd],
                                     [shooter_md, rows_state, manual_md,
                                      person_dd])   # + 表格刷新 outputs，抄 row_tag_dd
                    btn_unassign.click(unassign_person,
                                       [video_path, rows_state],
                                       [shooter_md, rows_state, manual_md,
                                        person_dd])  # + 同上
```

- [ ] **Step 4: 跑测试+全套回归**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q`
Expected: 全 PASS（零回归）

手工冒烟（报告里写给用户）：`./.venv/Scripts/python.exe -X utf8 -m bbh.app` → 剪辑台 → 点「🧍 识别出手人」→（缓存齐全会跳过溯源）特征提取进度 → 表格勾 2 球 → 归人选人名 → 面板出现人组+缩略图、标签列变、（若两球间隔 <2.5s）警示出现 → 「移出」恢复。

- [ ] **Step 5: 提交**

```bash
git add bbh/manual_groups.py bbh/app.py tests/test_manual_groups.py
git commit -m "feat: 手动归人一等公民——manual_groups.json(版本解耦/改判语义/容错)+归人面板(勾选→归人/移出/颜色提示恒low/MIN_GOAL_GAP护栏警示)+双写tag_assignments；识别链路尾接特征提取(聚类下线)"
```

---

### Task 9: 收尾——README、两场新口径验收、证据归档、清理

**Files:**
- Modify: `README.md`（出手人识别节约 99 行起补 Phase 2 修订段；数据目录表约 139 行 shooters.json 行后加两行）
- Create: `tmp_accept_phase2.py`（临时，不进库，跑完删）

**Interfaces:**
- Consumes: 全部前序任务；truth `shooters` 字段（键→人名）
- Produces: 验收材料（数字进提交信息）+ 证据归档 + 干净工作区

- [ ] **Step 1: 新口径验收脚本**

```python
# tmp_accept_phase2.py（临时，不进库）
"""新口径验收：护栏两场真值 0 违例 + 构造违例生效（spec§验收）。"""
import json
import sys

sys.path.insert(0, ".")
from bbh.appearance import gap_violations

for truth_path in ("truth/2026-08-22_1.json", "truth/2026-08-21_1.json"):
    shooters = json.load(open(truth_path, encoding="utf-8"))["shooters"]
    by_name = {}
    for k, name in shooters.items():
        by_name.setdefault(name, []).append(k)
    bad = [(n, gap_violations(keys)) for n, keys in by_name.items()
           if gap_violations(keys)]
    print(f"{truth_path}: 同人护栏违例 {len(bad)} 组（期望 0——两场同人最短间隔 3.3s）")
    for n, g in bad:
        print("  ", n, g)

assert gap_violations(["01:00.0", "01:02.0"]) == [("01:00.0", "01:02.0", 2.0)]
print("构造违例生效 ✓（<2.5s 会被警示，不阻断）")
```

Run: `./.venv/Scripts/python.exe -X utf8 tmp_accept_phase2.py` → 两场 0 违例 + 构造违例 ✓。

- [ ] **Step 2: 两场建议基线**

```bash
./.venv/Scripts/python.exe -X utf8 tools/eval_suggestions.py work/2026-08-22_1 truth/2026-08-22_1.json
./.venv/Scripts/python.exe -X utf8 tools/eval_suggestions.py work/2026-08-21_1 truth/2026-08-21_1.json --snap work/2026-08-21_1/positions.json
```

（若 features.json 不在则自动重提，08-22 约 6 分钟、08-21 约 5 分钟。）数字记进提交信息——**基线，不设 gate**。

- [ ] **Step 3: README 更新 + 全套回归 + 证据归档清理**

README 出手人识别节（约 99 行）后补：

```markdown
### 出手人归人（Phase 2，2026-08-29 修订：手动归人为一等公民）

- 「🧍 识别出手人」跑完溯源后自动接外观特征提取（features.json，逐球续跑）
- 表格勾选多球 → 「归人」下拉选/输入人名 → 写 manual_groups.json 并双写
  tag_assignments（打标即自动勾选照常生效）；已归人球可「移出」重归（改判）
- 颜色相近提示恒 low（两场精度≈随机，只作排序参考，永不单独定身份）；
  号码识别=放大裁剪自己看（OCR 自动建议经两场验证证伪不进产品，证据见 spec 修订）
- 护栏：勾选内两球间隔 <2.5s 警示"物理上不太可能同人"（不阻断，手动永远赢）
```

数据目录表 shooters.json 行后加：

```markdown
| `work/<视频>/features.json` | 每球外观指纹缓存（视频+标定+FEAT_VERSION 身份键） |
| `work/<视频>/manual_groups.json` | 手动归人（人名→球 key；与版本解耦，重跑不覆盖） |
```

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q` → 全 PASS。

证据归档与清理（OCR 扫描证据 CSV 先归档再删 tmp）：

```bash
mkdir -p .superpowers/sdd/2026-08-29-appearance-clustering/evidence
cp tmp_scan_0821/verdicts_redo1.csv .superpowers/sdd/2026-08-29-appearance-clustering/evidence/ 2>/dev/null
cp tmp_scan_0822/verdicts_s1.csv tmp_scan_0822/verdicts_s2.csv tmp_scan_0822/verdicts_s3.csv tmp_scan_0822/verdicts_s4.csv tmp_scan_0822/manifest.csv .superpowers/sdd/2026-08-29-appearance-clustering/evidence/ 2>/dev/null
cp tmp_ocr_spike.csv .superpowers/sdd/2026-08-29-appearance-clustering/evidence/ 2>/dev/null
rm -rf tmp_scan_0821 tmp_scan_0822
rm -f tmp_0821_trace.py tmp_aggregate_0822.py tmp_ocr_nat.py tmp_ocr_spike.csv tmp_calib_appearance.py tmp_accept_phase2.py
git status   # 应无 tmp_ 残留（.superpowers 本地不进库）
```

- [ ] **Step 4: 报告**

验收数字（护栏 0 违例/构造违例、两场 precision@k 与 hit_rate 基线）、README 变更、清理清单、UI 冒烟建议（开 app 勾球归人一轮）。建议恒 low/不含已归人/改判语义已有单测背书，逐条对号。

- [ ] **Step 5: 提交**

```bash
git add README.md
git commit -m "docs: README 补 Phase 2 修订——手动归人面板/颜色提示恒low/护栏；数据目录加 features+manual_groups"
```

---

## Self-Review 记录（2026-08-29 第二轮重写）

- **Spec 覆盖**：spec 第二轮修订各条 → 手动归人一等公民+版本解耦+改判（Task 8 manual_groups 与面板）、建议钩子恒 low+护栏 4 条（Task 7 dataclass/剔除已归人 + Task 8 面板提示；护栏 1"永不自动生效"=无任何自动写入路径，护栏 4=Task 7 evaluate_neighbors 工装）、OCR 不进 v1（零代码；被动展示=面板放大裁剪既有 UX）、MIN_GOAL_GAP 护栏（Task 6b gap_violations + Task 8 警示 + Task 9 两场 0 违例/构造违例）、评估工装（Task 7 eval_suggestions + Task 9 两场基线）、常量下线注释（Task 6b）、_hist_sim 修复（Task 6b）、新口径验收四条（Task 9 Step 1/2 + Task 7/8 单测）。✓
- **占位符扫描**：无 TBD/TODO。Task 8 两处"抄 row_tag_dd 布线/搬现有标签读取"是定向指令（给出具体行号锚点与要抄的具体对象），`_tag_choices` 给了实现指引而非留空。✓
- **类型一致性**：`gap_violations(keys)->list[tuple[str,str,float]]`（6b 定义、8 调用解包 a,b,g）；`color_neighbors(target, feats, manual_groups, k)`（7 定义、8 调用同参序）；`render_manual_payload -> (str, list)` 且 assign/unassign `*payload` 展开 → 4 路输出与布线 `[shooter_md, rows_state, manual_md, person_dd]` 一一对应；`manual.load/save/assign/unassign` 全文一致；`Suggestion` 字段与 spec §建议钩子逐字对齐。✓
- **修订说明**：Task 1-5 按原案已完成（连边建组代码保留但下线）；原 Task 6 标定已执行（结论=证伪），本版 Task 6b 只做善后；原 Task 7 纯度报告/Task 8 分组命名面板/Task 9 纯度 gate 随 gate 作废一并重写。
