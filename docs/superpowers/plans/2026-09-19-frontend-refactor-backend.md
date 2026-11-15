# 前端重构 Plan A：FastAPI 路由层 + 前端脚手架 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `bbh/app.py` 里 2729 行 Gradio 界面背后的业务能力，暴露成一套稳定的 HTTP API，并把 Vue3 前端工程立起来——交付「服务能起、API 全绿、三页能打开」。

**Architecture:** 新增 `bbh/web/` 包（FastAPI）。路由层只做「收请求 → 调 `bbh/app.py` 里已有的纯函数 → 返回 JSON」，业务逻辑一行不复制、sidecar 格式一字不改；`app.py` 抛的 `gr.Error` 统一翻成 HTTP 400，前端拿到与旧界面逐字相同的文案。`app.py` 的 `build_app()`（Gradio UI）原样保留，`run_app.bat` 改一行切到新入口，改回即回退。

**Tech Stack:** FastAPI + uvicorn（已在 `.venv`，fastapi 0.141.1 / uvicorn 0.52.3 / httpx 0.28.1）、pytest + `fastapi.testclient`；前端 Vite + Vue3 + Element Plus（Plan A 只立脚手架，页面实现留给 Plan B/C）。

**Spec:** `docs/superpowers/specs/2026-09-16-frontend-refactor-design.md`

## Global Constraints

- **业务代码不动**：`bbh/` 下除新增 `bbh/web/`、`bbh/static/` 外**零修改**；`bbh/app.py` 的 `build_app()`、全部函数体、六处「请先上传视频…」文案（`app.py:98/187/223/255/299/435`）原样保留——回退旧界面的前提。
- **`work/` sidecar 结构/字段名/格式一字不改**，不新增字段。界面偏好写仓库根 `ui_prefs.json`（与 `tags.json` 同级），不进 `work/`。
- **cwd 必须是项目根**：`app._work_dir_for()` 返回相对路径 `work/<视频名>`；`bbh/web/__main__.py` 启动时 `os.chdir(项目根)`。
- **端口 `127.0.0.1:7860`**；`run_app.bat` 只改启动模块名（`.venv\Scripts\python -m bbh.app` → `-m bbh.web`）。
- **错误契约**：所有失败响应体 = `{"error": "中文文案"}`；用户可见错误（`gr.Error`）→ 400，未预期异常 → 500。
- **写操作串行**：所有 sidecar 写操作在 `state.video_lock(video_path)` 下执行（读整档 → 改 → 写回）。
- **键恒用原始检测时刻**：`app._raw_time_keys()`，格式由 `models.time_key` 定 = `f"{int(t//60):02d}:{t%60:04.1f}"`（**分钟零填充**：12.1s → `"00:12.1"`、132.1s → `"02:12.1"`；110 分钟视频自然出三位）。显示时刻受时刻校准偏移影响，不可作键。
- **前后秒（before/after）不落盘**（与旧界面一致，重开回默认 5/2）——只活在浏览器里，切片时随请求带上。
- Python 测试命令：`./.venv/Scripts/python.exe -X utf8 -m pytest -q`（新测试只跑自己：`-q tests/test_web_session.py`）。
- Windows 文件名禁冒号；git 提交信息用中文。

## 文件结构

| 文件 | 职责 |
|---|---|
| `bbh/web/__init__.py` | 包标记（空） |
| `bbh/web/__main__.py` | 入口：`python -m bbh.web`（chdir 项目根 + 一次性迁移 + uvicorn） |
| `bbh/web/app.py` | `create_app()`：错误映射、路由注册、静态挂载 |
| `bbh/web/state.py` | 单会话状态（当前视频 / 标定页当前帧 / 已点坐标 / 段起点）+ 每视频写锁 |
| `bbh/web/jobs.py` | 长任务注册表（单飞 + 进度上报） |
| `bbh/web/media.py` | `/media/video`、`/media/work`、`/media/clip` 三条白名单媒体路由 |
| `bbh/web/api_session.py` | 会话与全量状态、行数据装配（`build_rows`） |
| `bbh/web/api_events.py` | 行写操作（勾选/标签/删除/恢复/漏检/标签库） |
| `bbh/web/api_marks.py` | 裁决反馈与误标（feedback.json / 缩略图 / 十字） |
| `bbh/web/api_wall.py` | 出手人墙数据与归人/移出/护栏 |
| `bbh/web/api_calib.py` | 标定：抽帧、点选、生成、HSV 微调、多段管理 |
| `bbh/web/api_gen.py` | 检测 / 回扫 / 生成三个长任务 + job 查询 |
| `bbh/static/{edit,wall,calib}.html` | Plan A 占位页（Plan B 的 `vite build` 覆盖） |
| `frontend/` | Vite 工程（package.json / vite.config.js / 三入口 / src/shared） |
| `tests/test_web*.py` | 路由层测试（TestClient，不起浏览器） |

---

## Task 1: bbh/web 骨架 + 静态挂载 + 入口切换

**Files:**
- Create: `bbh/web/__init__.py`、`bbh/web/__main__.py`、`bbh/web/app.py`
- Create: `bbh/static/edit.html`、`bbh/static/wall.html`、`bbh/static/calib.html`（占位）
- Modify: `run_app.bat:3`、`requirements.txt`
- Test: `tests/test_web_skeleton.py`

**Interfaces:**
- Produces: `bbh.web.app.create_app() -> FastAPI`（后续每个任务的 `_register` 往里加路由）；静态目录常量 `bbh.web.app.STATIC_DIR`。

- [ ] **Step 1: 写失败测试**

`tests/test_web_skeleton.py`：

```python
"""路由层骨架：静态页能出、根路径重定向、错误映射装上了。

不起浏览器、不碰 work/——只用 TestClient 打这个进程里的 app 对象。"""
from fastapi.testclient import TestClient

from bbh.web.app import create_app

client = TestClient(create_app())


def test_root_redirects_to_workbench():
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/edit.html"


def test_three_pages_are_served():
    for page in ("edit.html", "wall.html", "calib.html"):
        r = client.get("/" + page)
        assert r.status_code == 200, page
        assert "篮球集锦" in r.text


def test_unknown_path_is_404():
    assert client.get("/nope.html").status_code == 404
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_web_skeleton.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'bbh.web'`

- [ ] **Step 3: 写实现**

`bbh/web/__init__.py`：

```python
"""FastAPI 路由层（2026-09-19 前端重构）。

业务逻辑全在 bbh/app.py 与其下游模块里；这里只做 HTTP 到函数调用的翻译。
"""
```

`bbh/web/app.py`：

```python
"""FastAPI 应用装配：错误映射 + 路由注册 + 静态挂载。

错误契约：bbh/app.py 里抛的 gr.Error（「请先填路径…」这类用户可见报错）
统一翻成 HTTP 400 + {"error": 文案}——前端直接把文案弹出来，与旧界面的
提示逐字相同。其余异常 500，同样带中文前缀，避免前端拿到空 body。

启动顺序陷阱：静态目录挂在 "/"，会兜住所有未匹配路径，所以 API 路由必须
先注册（_register 在 mount 之前调用）。
"""
import os

import gradio as gr
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def create_app() -> FastAPI:
    app = FastAPI(title="篮球进球自动集锦", docs_url=None, redoc_url=None)

    @app.exception_handler(gr.Error)
    async def _gr_error(_req: Request, exc: Exception):
        return JSONResponse(status_code=400, content={
            "error": getattr(exc, "message", None) or str(exc)})

    @app.exception_handler(RequestValidationError)
    async def _bad_body(_req: Request, exc: Exception):
        # FastAPI 默认 422 的 body 是 {"detail": [...]}，违反「失败恒为
        # {"error": 中文}」的契约（前端只认 error 字段）——翻成 400 同款。
        return JSONResponse(status_code=400, content={
            "error": f"请求格式不对：{exc.errors()[:1]}"})

    @app.exception_handler(Exception)
    async def _unexpected(_req: Request, exc: Exception):
        return JSONResponse(status_code=500, content={
            "error": f"{type(exc).__name__}: {exc}"})

    @app.get("/", include_in_schema=False)
    def _root():
        return RedirectResponse("/edit.html")

    _register(app)
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def _register(app: FastAPI) -> None:
    """各 API 路由的挂载点——后续任务逐个往里加（顺序即注册顺序）。"""
    return
```

`bbh/web/__main__.py`：

```python
"""新前端入口：python -m bbh.web（run_app.bat 调它）。

必须先 chdir 到项目根：bbh/app.py 的 _work_dir_for 返回相对路径
"work/<视频名>"，从别处启动会把 sidecar 写到别的目录里去。

回退：把 run_app.bat 那一行改回 -m bbh.app，旧 Gradio 界面照常可用。
"""
import os
import sys

import uvicorn

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> None:
    os.chdir(ROOT)
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from .app import create_app
    from ..app import migrate_root_calibrations

    for note in migrate_root_calibrations():   # 与旧入口 __main__ 同款
        print(note)
    print("🏀 篮球进球自动集锦 —— http://127.0.0.1:7860/")
    uvicorn.run(create_app(), host="127.0.0.1", port=7860, log_level="warning")


if __name__ == "__main__":
    main()
```

`bbh/static/edit.html`（wall/calib 同款，只改标题与名字——Plan B 会被 `vite build` 覆盖）：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>篮球集锦 · 剪辑台</title></head>
<body style="margin:0;background:#141414;color:#e5eaf3;font-family:system-ui">
  <div style="padding:40px">
    <h1>篮球集锦 · 剪辑台</h1>
    <p>前端骨架占位页——Plan B 用 vite build 覆盖本目录。</p>
    <p><a href="/wall.html" style="color:#e8622d">出手人墙</a> ·
       <a href="/calib.html" style="color:#e8622d">标定向导</a></p>
  </div>
</body>
</html>
```

`run_app.bat`（只改第 3 行）：

```bat
@echo off
cd /d "%~dp0"
.venv\Scripts\python -m bbh.web
pause
```

`requirements.txt` 追加两行（现在靠 gradio 间接带入，回退时不能断）：

```
fastapi>=0.110
uvicorn>=0.29
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_web_skeleton.py`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add bbh/web/__init__.py bbh/web/app.py bbh/web/__main__.py bbh/static/ tests/test_web_skeleton.py run_app.bat requirements.txt
git commit -m "feat: 前端重构 Plan A——FastAPI 路由层骨架 + 静态挂载 + 入口切到 bbh.web（旧 Gradio 可一行回退）"
```

---

## Task 2: job 注册表（长任务单飞 + 进度上报）

**Files:**
- Create: `bbh/web/jobs.py`
- Test: `tests/test_web_jobs.py`

**Interfaces:**
- Produces: `jobs.Job`（属性 `id/kind/state/pct/msg/found/error/result`，方法 `as_dict()`）、`jobs.JobRegistry`（`start(kind, worker) -> Job` / `get(job_id) -> Job | None` / `running_kind(kind) -> bool`）、`jobs.JobBusy`、模块级单例 `jobs.REGISTRY`。`worker(report)` 形参即 `report(msg=None, pct=None, found=None)`。

- [ ] **Step 1: 写失败测试**

`tests/test_web_jobs.py`：

```python
"""job 注册表：后台跑、进度上报、同类单飞、异常带回文案。"""
import threading
import time

import gradio as gr
import pytest

from bbh.web.jobs import JobBusy, JobRegistry


def _wait(reg, job, timeout=5.0):
    t0 = time.time()
    while reg.get(job.id).state == "running" and time.time() - t0 < timeout:
        time.sleep(0.02)


def test_worker_runs_and_reports_progress():
    reg = JobRegistry()

    def worker(report):
        report(msg="第一步", pct=10)
        report(msg="第二步", pct=60, found=3)
        return {"ok": True}

    job = reg.start("gen", worker)
    _wait(reg, job)
    j = reg.get(job.id)
    assert j.state == "done" and j.pct == 100
    assert j.result == {"ok": True}
    assert j.found == 3          # 末次上报的球数留着，翻看历史用
    assert j.as_dict()["done"] is True


def test_worker_exception_becomes_error_job():
    reg = JobRegistry()

    def worker(report):
        raise RuntimeError("炸了")

    job = reg.start("detect", worker)
    _wait(reg, job)
    assert reg.get(job.id).state == "error"
    assert "炸了" in reg.get(job.id).error


def test_gr_error_message_is_user_facing():
    reg = JobRegistry()

    def worker(report):
        raise gr.Error("本视频还没标定——先去「⚙ 标定设置」完成一次")

    job = reg.start("wall_scan", worker)
    _wait(reg, job)
    assert reg.get(job.id).error == "本视频还没标定——先去「⚙ 标定设置」完成一次"


def test_same_kind_is_single_flight_until_it_finishes():
    reg = JobRegistry()
    gate = threading.Event()

    def slow(report):
        gate.wait(2.0)
        return None

    job = reg.start("detect", slow)
    with pytest.raises(JobBusy):
        reg.start("detect", lambda r: None)
    # 别的种类不受影响，可以并行
    other = reg.start("gen", lambda r: None)
    _wait(reg, other)
    assert reg.get(other.id).state == "done"
    gate.set()
    _wait(reg, job)
    # 跑完释放，同类可以再启动
    job2 = reg.start("detect", lambda r: None)
    _wait(reg, job2)
    assert reg.get(job2.id).state == "done"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_web_jobs.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'bbh.web.jobs'`

- [ ] **Step 3: 写实现**

`bbh/web/jobs.py`：

```python
"""长任务注册表：检测 / 回扫溯源 / 生成集锦统一成 job。

前端 POST 启动拿 job_id，之后 0.8~1s GET /api/jobs/{id} 轮询（不引 SSE：
断线刷新后还能接着看，实现也最简）。job 只在内存里——本机单用户工具，
重启丢了重跑一次即可。

同类任务同时只允许一个（旧界面同样是「已有检测在跑」就拒绝），第二次
启动抛 JobBusy，路由层翻成 409。
"""
import threading
import time
import uuid
from dataclasses import dataclass, field


class JobBusy(Exception):
    """同类任务已在跑——路由层翻成 409。"""


@dataclass
class Job:
    id: str
    kind: str
    state: str = "running"           # running | done | error
    pct: int = 0
    msg: str = ""
    found: int | None = None         # 已发现球数（检测/回扫进度用）
    error: str | None = None
    result: object = None
    started: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "state": self.state,
                "pct": self.pct, "msg": self.msg, "found": self.found,
                "error": self.error, "done": self.state != "running"}


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._running: dict[str, str] = {}      # kind → job_id（单飞）
        self._lock = threading.Lock()

    def start(self, kind: str, worker) -> Job:
        """worker(report) 在后台线程里跑；返回值进 job.result，异常进
        job.error。report(msg=, pct=, found=) 只覆盖给了值的字段。"""
        with self._lock:
            if kind in self._running:
                raise JobBusy(f"{kind} 已在跑——等它结束或刷新页面看进度")
            job = Job(id=uuid.uuid4().hex[:12], kind=kind)
            self._jobs[job.id] = job
            self._running[kind] = job.id

        def report(msg=None, pct=None, found=None):
            if msg is not None:
                job.msg = msg
            if pct is not None:
                job.pct = int(pct)
            if found is not None:
                job.found = int(found)

        def run():
            try:
                job.result = worker(report)
                job.state = "done"
                job.pct = 100
            except Exception as e:  # noqa: BLE001 —— 任何异常都要带回界面
                job.state = "error"
                job.error = getattr(e, "message", None) or str(e)
            finally:
                with self._lock:
                    self._running.pop(kind, None)

        threading.Thread(target=run, daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def running_kind(self, kind: str) -> bool:
        with self._lock:
            return kind in self._running


REGISTRY = JobRegistry()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_web_jobs.py`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add bbh/web/jobs.py tests/test_web_jobs.py
git commit -m "feat: job 注册表——长任务单飞 + 进度上报 + gr.Error 文案带回"
```

---

## Task 3: 媒体路由（白名单 video / work / clip）

**Files:**
- Create: `bbh/web/state.py`（本任务先只放会话状态与写锁，标定字段后面用）
- Create: `bbh/web/media.py`
- Modify: `bbh/web/app.py`（`_register` 挂 media 路由）
- Test: `tests/test_web_media.py`

**Interfaces:**
- Consumes: `app.clean_path`、`app._cut_preview`（`bbh/app.py:884`）。
- Produces: `state.SESSION`（`video_path` / `frame_rgb` / `points` / `seg_t`）、`state.video_lock(video_path) -> RLock`；路由 `GET /media/work?path=`、`GET /media/video?path=`、`GET /media/clip?path=&t=&before=&after=`。

- [ ] **Step 1: 写失败测试**

`tests/test_web_media.py`：

```python
"""媒体路由白名单：work/ 内放行、越界拒绝、当前视频才可播、切片能出。"""
import pytest
from fastapi.testclient import TestClient

from bbh.web import state
from bbh.web.app import create_app

client = TestClient(create_app())


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """把 work/ 与视频都放进 tmp，并把会话视频指过去。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "work" / "vid1").mkdir(parents=True)
    (tmp_path / "work" / "vid1" / "0_12.1.gif").write_bytes(b"GIF89a-test")
    (tmp_path / "outside.gif").write_bytes(b"NOPE")
    video = tmp_path / "vid1.mp4"
    video.write_bytes(b"\x00" * 4096)
    state.SESSION.video_path = str(video)
    yield tmp_path
    state.SESSION.video_path = ""


def test_work_file_inside_work_dir_is_served(env):
    r = client.get("/media/work", params={"path": "work/vid1/0_12.1.gif"})
    assert r.status_code == 200
    assert r.content == b"GIF89a-test"


def test_work_file_outside_work_dir_is_forbidden(env):
    assert client.get("/media/work", params={"path": "outside.gif"}).status_code == 403
    assert client.get("/media/work", params={"path": "../secret.gif"}).status_code == 403


def test_work_traversal_that_stays_inside_is_normalized(env):
    r = client.get("/media/work", params={"path": "work/vid1/../vid1/0_12.1.gif"})
    assert r.status_code == 200


def test_work_missing_file_is_404(env):
    assert client.get("/media/work",
                      params={"path": "work/vid1/nope.gif"}).status_code == 404


def test_video_only_allows_current_session_video(env):
    ok = client.get("/media/video", params={"path": str(env / "vid1.mp4")})
    assert ok.status_code == 200
    other = env / "vid2.mp4"
    other.write_bytes(b"\x00" * 10)
    assert client.get("/media/video",
                      params={"path": str(other)}).status_code == 403


def test_video_supports_range_requests(env):
    r = client.get("/media/video", params={"path": str(env / "vid1.mp4")},
                   headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert len(r.content) == 100


def test_clip_returns_cut_mp4(sample_video, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "work").mkdir(exist_ok=True)
    state.SESSION.video_path = sample_video
    try:
        r = client.get("/media/clip", params={
            "path": sample_video, "t": 3.0, "before": 1.0, "after": 1.0})
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "video/mp4"
    finally:
        state.SESSION.video_path = ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_web_media.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'bbh.web.media'`

- [ ] **Step 3: 写实现**

`bbh/web/state.py`：

```python
"""单用户会话状态 + 每视频一把写锁。

Gradio 用 gr.State 存「当前视频 / 标定页当前参考帧 / 已点坐标」；换成
HTTP 后这些活在服务端，本机单用户只有一个实例，用模块级单例即可。

写锁的必要性：所有 sidecar 写操作都是「读整档 → 改 → 写回」，而前端在
轮询（读档）——交错会写到半截内容，Windows 上还会撞 PermissionError。
按视频路径取锁（RLock：同一请求里可重入）。
"""
import os
import threading

from ..app import clean_path


class Session:
    def __init__(self) -> None:
        self.video_path: str = ""
        self.frame_rgb = None            # 标定页当前参考帧（numpy RGB）
        self.points: list = []           # 筐口左端、右端、球（1-3 次）
        self.seg_t: float = 0.0          # 「时间点」控件当前值（补段标定的 t0）
        # 图片走内存不落盘（work/ sidecar 红线）：帧/预览各存 PNG 字节，
        # 由 /api/calib/{frame,preview}.png 出，URL 挂 ?v=<rev> 击穿缓存
        self.frame_png: bytes | None = None
        self.preview_png: bytes | None = None
        self.rev: int = 0                # 每次重算 +1（等价旧界面 preview 的 mtime）


SESSION = Session()

_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


def video_lock(video_path: str) -> threading.RLock:
    """该视频的写锁（路径大小写/分隔符归一后作键）。"""
    key = os.path.realpath(clean_path(video_path)) if video_path else ""
    with _locks_guard:
        return _locks.setdefault(key, threading.RLock())
```

`bbh/web/media.py`：

```python
"""媒体路由：浏览器要拿的每一份文件都从这里出。

- /media/work    work/ 目录内的产物（GIF/缩略图/成品），白名单 = 项目 work/ 内
- /media/video   当前会话的源视频（<video> 直读，Starlette 自带 Range）
- /media/clip    复用 app._cut_preview 的切片预览（帧精确 + 压到 720p）

白名单不是装饰：path 参数来自前端，必须挡住 work/ 之外的任意文件读取
（.. 穿越、绝对路径、UNC）——否则浏览器就能把本机任何文件读走。
"""
import os

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from .. import app as A
from . import state

router = APIRouter(prefix="/media")


def _work_root() -> str:
    return os.path.realpath("work")


def _inside(child: str, parent: str) -> bool:
    child = os.path.realpath(child)
    return child == parent or child.startswith(parent + os.sep)


def _require_work_file(path: str) -> str:
    p = os.path.realpath(A.clean_path(path))
    if not _inside(p, _work_root()):
        raise HTTPException(403, "只允许访问 work/ 目录内的文件")
    if not os.path.isfile(p):
        raise HTTPException(404, f"文件不存在：{p}")
    return p


def _require_current_video(path: str) -> str:
    vp = A.clean_path(path)
    cur = A.clean_path(state.SESSION.video_path)
    if not cur or os.path.realpath(vp) != os.path.realpath(cur):
        raise HTTPException(403, "只允许播放当前载入的视频")
    if not os.path.isfile(vp):
        raise HTTPException(404, f"视频不存在：{vp}")
    return vp


@router.get("/work")
def work_file(path: str = Query(...)):
    """work/ 内的产物文件；前端 URL 带 &v=<mtime> 击穿浏览器缓存。"""
    return FileResponse(_require_work_file(path))


@router.get("/video")
def video_file(path: str = Query(...)):
    # FileResponse 自带 Range 支持（starlette 0.52 起）——<video> 拖进度条
    # 靠它。若 test_video_supports_range_requests 没过（206 变 200），说明
    # 这个 starlette 版本要手写 Range：读 Range 头 → 解析 bytes=a-b →
    # Response(206, content=片段, headers={"Content-Range": ...,
    # "Accept-Ranges": "bytes"})。别删那个测试换实现。
    return FileResponse(_require_current_video(path), media_type="video/mp4")


@router.get("/clip")
def clip(path: str = Query(...), t: float = 0.0,
         before: float = 5.0, after: float = 2.0):
    """切片预览：走 app._cut_preview（帧精确重编码，几秒内出）。

    每次调用覆盖同一个 work/<视频名>/preview.mp4——前端 URL 必须带
    &v=<时间戳>，否则浏览器命中缓存不重拉（旧界面同款坑）。"""
    vp = _require_current_video(path)
    out = A._cut_preview(vp, float(t), float(before), float(after))
    return FileResponse(out, media_type="video/mp4")
```

`bbh/web/app.py` 的 `_register` 改为：

```python
def _register(app: FastAPI) -> None:
    """各 API 路由的挂载点——后续任务逐个往里加（顺序即注册顺序）。"""
    from . import media

    app.include_router(media.router)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_web_media.py`
Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
git add bbh/web/state.py bbh/web/media.py bbh/web/app.py tests/test_web_media.py
git commit -m "feat: 媒体路由——work/ 与当前视频白名单 + Range 播放 + 切片预览"
```

---

## Task 4: 会话 API + 行数据装配

**Files:**
- Create: `bbh/web/api_session.py`
- Modify: `bbh/web/app.py`（`_register` 挂 api_session）
- Test: `tests/test_web_session.py`

**Interfaces:**
- Consumes: `state.SESSION`、`app.probe_existing`、`app.load_events_table`、`app._raw_time_keys`、`app._filter_deleted`、`app._load_events_or_none`、`app._reason_for`、`app._gif_or_none`、`app._load_tags`、`app._load_deleted`、`app._load_flag_set`、`app._calib_ready`、`app._seg_choices`、`app._table_summary_md`、`app.build_goal_stats`、`goal_decider._side_score` / `_pos_score`、`shooter.load_shooters` / `STATUS_TEXT`。
- Produces: `GET /api/state`、`POST /api/session`；`api_session.build_rows(video_path) -> (list[dict], float)`（Plan B 前端消费的行结构，字段见下）。

**行结构（Plan B/C 全按这个字段名消费）：**

```json
{"key": "00:12.1", "t": "00:12.1", "status": "high", "conf": 0.9,
 "selection": true, "before": 5, "after": 2, "tag": "老杨",
 "thumb": "/media/work?path=…&v=17…",
 "shooter": {"status": "ok", "dot": "🟢", "releaseT": 12.1, "thumbs": ["…"]},
 "ev": {"crossOff": 0.02, "aboveN": 3, "belowN": 2, "aboveSpan": 0.6,
        "belowSpan": 0.4, "descentPx": 18.0, "belowBig": 2,
        "popBack": null, "lowReason": null},
 "net": {"post": 12.5, "ctrl": 3.1, "local": true},
 "terms": {"base": 0.3, "up": 0.2, "down": 0.2, "pos": 0.2},
 "reason": "判定依据：下穿点在筐心正中；…"}
```

- [ ] **Step 1: 写失败测试**

`tests/test_web_session.py`：

```python
"""会话：空路径拒绝、新视频提示、已有结果载入、行结构字段齐全。"""
import json

import pytest
from fastapi.testclient import TestClient

from bbh.models import Calibration, GoalEvent, save_events
from bbh.web import state
from bbh.web.app import create_app

client = TestClient(create_app())

NAME = "2026-09-11_3"


def _make_result(tmp_path, n=3):
    """造一份检测结果 + 标定（形状与真产物一致，字段名对齐 events.json）。"""
    work = tmp_path / "work" / NAME
    work.mkdir(parents=True)
    evs = [GoalEvent(
        t=12.1 + i * 60, confidence=0.90 - i * 0.10,
        status=("high", "review", "low")[i % 3],
        cross_off=0.02, above_n=3, above_span=0.6, below_n=2, below_span=0.4,
        descent_px=18.0, below_big=2, net_post=12.5, net_ctrl=3.1,
        net_local=True) for i in range(n)]
    save_events(str(work / "events.json"), video=str(tmp_path / f"{NAME}.mp4"),
                duration=480.0, events=evs, warning=None,
                calib_key=[1, 2, 3], logic_ver=1)
    Calibration(hoop_rect=(300, 140, 80, 40), hsv_low=(0, 80, 120),
                hsv_high=(25, 255, 255),
                frame_size=(1920, 1080)).save(str(work / "calibration.json"))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state.SESSION.video_path = ""
    yield tmp_path
    state.SESSION.video_path = ""


def test_blank_path_is_rejected(env):
    r = client.post("/api/session", json={"videoPath": "   "})
    assert r.status_code == 400
    assert "请先填" in r.json()["error"]


def test_new_video_without_calibration_hints_calibration(env):
    r = client.post("/api/session", json={"videoPath": "D:/videos/nope.mp4"})
    assert r.status_code == 200
    j = r.json()
    assert j["loaded"] is False and j["rows"] == []
    assert "标定" in j["hint"]


def test_loads_existing_events_and_shapes_rows(env):
    _make_result(env)
    r = client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    assert r.status_code == 200
    j = r.json()
    assert j["loaded"] is True
    assert len(j["rows"]) == 3
    assert j["duration"] == 480.0
    assert "共 3 条" in j["summaryMd"]
    row = j["rows"][0]
    assert row["key"] == "00:12.1"          # 键 = 原始检测时刻
    assert row["t"] == "00:12.1"
    assert row["status"] == "high"
    assert row["conf"] == pytest.approx(0.90)
    assert row["selection"] is False       # 没存过 selection.json = 全不勾
    assert row["before"] == 5 and row["after"] == 2
    assert row["ev"]["crossOff"] == 0.02 and row["ev"]["aboveN"] == 3
    assert row["net"] == {"post": 12.5, "ctrl": 3.1, "local": True}
    assert row["terms"]["base"] == 0.30
    assert "判定依据" in row["reason"]


def test_state_endpoint_returns_same_payload(env):
    _make_result(env)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    j = client.get("/api/state").json()
    assert j["loaded"] is True and len(j["rows"]) == 3
    assert j["calib"]["ready"] is True
    assert j["flags"] == {"thumb": [], "cross": []}
    assert j["feedback"]["goal"] == 0 and j["feedback"]["miss"] == 0


def test_state_without_session_says_fill_path(env):
    j = client.get("/api/state").json()
    assert j["loaded"] is False and "路径" in j["hint"]


def test_last_path_is_remembered_in_ui_prefs(env):
    client.post("/api/session", json={"videoPath": "D:/videos/a.mp4"})
    assert json.loads((env / "ui_prefs.json").read_text("utf-8"))["lastVideo"] \
        == "D:/videos/a.mp4"
    assert client.get("/api/state").json()["lastVideo"] == "D:/videos/a.mp4"


def test_malformed_body_is_400_with_error_key_not_422_detail(env):
    """失败恒为 {"error": 中文}——FastAPI 默认的 422 {"detail": [...]}
    被全局 handler 翻成 400 同款（前端只认 error 字段）。"""
    r = client.post("/api/session", json={"videoPath": 12345})
    assert r.status_code == 400
    assert "error" in r.json() and "detail" not in r.json()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_web_session.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'bbh.web.api_session'`

- [ ] **Step 3: 写实现**

`bbh/web/api_session.py`：

```python
"""会话与全量状态：三页打开时都要的一份数据。

行数据在这里装配成结构化 dict（不是 app.py 给 Gradio Dataframe 的 7 元素
数组）：前端要字段名不要列序，判定依据/证据/出手人一并带上，一次拿全，
切行不再打服务端。所有数字都由服务端算（同一个 _table_summary_md /
build_goal_stats），前端不做统计——与 sidecar 必然一致。

不做视频存在性校验：与旧界面一致（填错路径照样提示「新视频」，点检测时
才报「视频不存在」）。
"""
import json
import os
import urllib.parse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import app as A
from .. import shooter
from ..goal_decider import _pos_score, _side_score
from . import state

router = APIRouter(prefix="/api")

PREFS_FILE = "ui_prefs.json"          # 仓库根，与 tags.json 同级（不进 work/）


class SessionIn(BaseModel):
    videoPath: str = ""


def _media_url(p: str | None) -> str | None:
    """work/ 内文件的浏览器 URL（带 mtime 击穿缓存，与 _file_url 同口径）。"""
    if not p or not os.path.exists(p):
        return None
    q = urllib.parse.quote(os.path.abspath(p).replace("\\", "/"), safe="/:")
    return f"/media/work?path={q}&v={os.path.getmtime(p):.0f}"


def _session_video() -> str:
    vp = A.clean_path(state.SESSION.video_path)
    if not vp:
        raise HTTPException(400, "请先填视频路径（本机文件全路径）")
    return vp


def _remember_path(vp: str) -> None:
    """记住上次路径（界面偏好，独立文件——work/ 的 sidecar 一个字段不加）。"""
    try:
        prefs = {}
        if os.path.exists(PREFS_FILE):
            prefs = json.load(open(PREFS_FILE, encoding="utf-8")) or {}
        prefs["lastVideo"] = vp
        with open(PREFS_FILE, "w", encoding="utf-8") as f:
            json.dump(prefs, f, ensure_ascii=False, indent=1)
    except (OSError, ValueError):
        pass          # 偏好写不进去不该挡住开视频


def _last_path() -> str:
    try:
        return json.load(open(PREFS_FILE, encoding="utf-8")).get("lastVideo", "")
    except (OSError, ValueError, AttributeError):
        return ""


def build_rows(video_path: str) -> tuple[list[dict], float]:
    """一行一条球：键/时刻/档位/置信度/勾选/标签/前后秒 + 证据 + 出手人。

    key 用 _raw_time_keys（原始检测时刻）——勾选、标签、删除、裁决、归人
    五套 sidecar 全按它存；显示时刻受时刻校准偏移影响，不能当键。
    """
    vp = A.clean_path(video_path)
    dur, rows = A.load_events_table(vp)          # 无结果 → gr.Error → 400
    keys = A._raw_time_keys(vp, rows)
    data = A._filter_deleted(A._load_events_or_none(vp), vp) or {"events": []}
    evs = data.get("events") or []
    work = A._work_dir_for(vp)
    traces = (shooter.load_shooters(work) or {}).get("traces") or {}
    out = []
    for i, r in enumerate(rows):
        key = keys[i]
        e = evs[i] if i < len(evs) else None
        tr = traces.get(key) or {}
        has_ev = e is not None and e.cross_off is not None
        out.append({
            "key": key, "t": r[1], "status": r[2], "conf": float(r[3]),
            "selection": bool(r[0]), "before": r[4], "after": r[5],
            "tag": r[6] or "",
            "thumb": _media_url(A._gif_or_none(work, key)),
            "shooter": ({
                "status": tr.get("status"),
                "dot": shooter.STATUS_TEXT.get(tr.get("status"), "⚪"),
                "releaseT": tr.get("release_t"),
                "thumbs": [_media_url(os.path.join(work, rel))
                           for rel in (tr.get("thumbs") or [])],
            } if tr else None),
            "ev": ({
                "crossOff": e.cross_off, "aboveN": e.above_n,
                "belowN": e.below_n, "aboveSpan": e.above_span,
                "belowSpan": e.below_span, "descentPx": e.descent_px,
                "belowBig": e.below_big, "popBack": e.pop_back,
                "lowReason": e.low_reason,
            } if has_ev else None),
            "net": ({
                "post": e.net_post, "ctrl": e.net_ctrl, "local": e.net_local,
            } if e is not None and e.net_post is not None else None),
            # 与 goal_decider 的同名项逐字同源（conf = 0.3 + up + down + pos）。
            # 旧版 events.json 可能只有 cross_off 没有 above_n/span → 缺项按 0
            # 计（那几档本来就等于没证据，不是"证据为 0 分"的误读）。
            "terms": ({
                "base": 0.30,
                "up": _side_score(e.above_n or 0, e.above_span or 0.0),
                "down": _side_score(e.below_n or 0, e.below_span or 0.0),
                "pos": _pos_score(abs(e.cross_off)),
            } if has_ev else None),
            "reason": A._reason_for(vp, rows, i),
        })
    return out, dur


def _feedback_summary(vp: str) -> dict:
    fb = A._load_feedback(vp)
    n_goal = sum(1 for v in fb["verdicts"] if v.get("verdict") == "goal")
    return {"goal": n_goal, "no": len(fb["verdicts"]) - n_goal,
            "miss": len(fb["misses"]), "summaryMd": A._fp_summary(vp)}


def state_payload() -> dict:
    """三页共用的全量状态（写操作返回后也调它：前端就地替换，不自己算）。"""
    vp = A.clean_path(state.SESSION.video_path)
    base = {"videoPath": vp, "lastVideo": _last_path(), "loaded": False,
            "hint": "请先填视频路径（本机文件全路径）", "rows": [],
            "duration": 0.0, "summaryMd": "", "goalStatsMd": "",
            "tags": A._load_tags(), "deleted": [], "flags": {"thumb": [], "cross": []},
            "feedback": {"goal": 0, "no": 0, "miss": 0, "summaryMd": ""},
            "calib": {"ready": False, "segments": []}}
    if not vp:
        return base
    # probe_existing 给的是旧界面那几句提示（已载入已有结果 / 新视频 / 还没标定）
    _dur_md, _proj, rows7, _cur, _panel, hint = A.probe_existing(vp)
    base.update(hint=hint, tags=A._load_tags(), deleted=A._load_deleted(vp),
                flags={"thumb": A._load_flag_set(vp, A.THUMB_FLAG_FILE),
                       "cross": A._load_flag_set(vp, A.CROSS_FLAG_FILE)},
                feedback=_feedback_summary(vp),
                calib={"ready": A._calib_ready(vp),
                       "segments": A._seg_choices(vp)})
    if rows7:
        rows, dur = build_rows(vp)
        base.update(loaded=True, rows=rows, duration=dur,
                    summaryMd=A._table_summary_md(dur, rows7),
                    goalStatsMd=A.build_goal_stats(vp, rows7))
    return base


@router.post("/session")
def open_session(body: SessionIn):
    vp = A.clean_path(body.videoPath)
    if not vp:
        raise HTTPException(400, "请先填视频路径（本机文件全路径）")
    state.SESSION.video_path = vp
    state.SESSION.frame_rgb = None       # 换视频 = 标定页一切归零
    state.SESSION.frame_png = None
    state.SESSION.preview_png = None
    state.SESSION.points = []
    state.SESSION.seg_t = 0.0
    _remember_path(vp)
    return state_payload()


@router.get("/state")
def get_state():
    return state_payload()
```

`bbh/web/app.py` 的 `_register` 追加：

```python
    from . import api_session

    app.include_router(api_session.router)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_web_session.py`
Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
git add bbh/web/api_session.py bbh/web/app.py tests/test_web_session.py
git commit -m "feat: 会话 API 与行数据装配——结构化行 + 证据/依据/统计全由服务端算"
```

---

## Task 5: 事件写操作 API（勾选 / 标签 / 删除 / 恢复 / 漏检 / 标签库）

**Files:**
- Create: `bbh/web/api_events.py`
- Modify: `bbh/web/app.py`（`_register` 挂 api_events）
- Test: `tests/test_web_events.py`

**Interfaces:**
- Consumes: `api_session._session_video` / `state_payload`、`state.video_lock`、`app.load_events_table`、`app._raw_time_keys`、`app._save_selection`、`app.set_row_tag`、`app.delete_events`、`app.restore_deleted`、`app.record_miss`、`app.add_tag_to_library`、`app._load_tags`。
- Produces: `PATCH /api/events/{key}`（body `{"selection": bool}` 或 `{"tag": str}`）、`POST /api/events/miss`（`{"text": "12:34"}`）、`DELETE /api/events`（`{"keys": ["00:12.1"]}`）、`POST /api/events/restore`、`POST /api/tags`（`{"name": "老杨"}`）；所有写操作返回 `api_session.state_payload()`（+ `msg`）。

- [ ] **Step 1: 写失败测试**

`tests/test_web_events.py`：

```python
"""行写操作：勾选/标签落盘、删除非破坏可恢复、漏检补记、标签库。"""
import json

import pytest
from fastapi.testclient import TestClient

from bbh.web import state
from bbh.web.app import create_app

from test_web_session import NAME, _make_result      # 复用同一份假结果

client = TestClient(create_app())


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _make_result(tmp_path, n=3)
    state.SESSION.video_path = ""
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    yield tmp_path
    state.SESSION.video_path = ""


def _read(tmp_path, name):
    return json.loads((tmp_path / "work" / NAME / name).read_text("utf-8"))


def test_selection_patch_writes_selection_json(env):
    r = client.patch("/api/events/0:12.1", json={"selection": True})
    assert r.status_code == 200
    assert _read(env, "selection.json") == ["00:12.1"]
    row = [x for x in r.json()["rows"] if x["key"] == "00:12.1"][0]
    assert row["selection"] is True
    # 再取消
    client.patch("/api/events/0:12.1", json={"selection": False})
    assert _read(env, "selection.json") == []


def test_unknown_key_is_404(env):
    assert client.patch("/api/events/9:99.9",
                        json={"selection": True}).status_code == 404


def test_tag_patch_writes_assignments_and_autochecks(env):
    r = client.patch("/api/events/0:12.1", json={"tag": "老杨"})
    assert r.status_code == 200
    assert _read(env, "tag_assignments.json") == {"00:12.1": "老杨"}
    row = [x for x in r.json()["rows"] if x["key"] == "00:12.1"][0]
    assert row["tag"] == "老杨"
    assert row["selection"] is True          # 打标签 = 认定进球，自动勾选
    assert _read(env, "selection.json") == ["00:12.1"]


def test_blank_tag_clears_it_without_unchecking(env):
    client.patch("/api/events/0:12.1", json={"tag": "老杨"})
    r = client.patch("/api/events/0:12.1", json={"tag": ""})
    row = [x for x in r.json()["rows"] if x["key"] == "00:12.1"][0]
    assert row["tag"] == ""
    assert row["selection"] is True          # 去标 ≠ 不进集锦


def test_delete_removes_row_but_keeps_it_restorable(env):
    r = client.request("DELETE", "/api/events", json={"keys": ["00:12.1"]})
    assert r.status_code == 200
    assert [x["key"] for x in r.json()["rows"]] == ["01:12.1", "02:12.1"]
    assert _read(env, "deleted.json") == ["00:12.1"]
    r2 = client.post("/api/events/restore")
    assert r2.status_code == 200
    assert len(r2.json()["rows"]) == 3
    assert _read(env, "deleted.json") == []


def test_delete_stale_key_does_not_break_the_batch(env):
    """批量宁丢失配键不中断整批（重跑检测/换视频后旧面板残留的键）——
    混进一个不存在的键，其余照删，不报错。"""
    r = client.request("DELETE", "/api/events",
                       json={"keys": ["99:99.9", "00:12.1"]})
    assert r.status_code == 200
    assert _read(env, "deleted.json") == ["00:12.1"]


def test_delete_with_no_keys_is_400(env):
    r = client.request("DELETE", "/api/events", json={"keys": []})
    assert r.status_code == 400
    assert "勾选" in r.json()["error"]


def test_events_alias_matches_state(env):
    assert client.get("/api/events").json()["rows"] == \
        client.get("/api/state").json()["rows"]


def test_miss_records_and_summarises(env):
    r = client.post("/api/events/miss", json={"text": "756"})
    assert r.status_code == 200
    fb = _read(env, "feedback.json")
    assert fb["misses"][0]["t"] == 756.0
    assert r.json()["msg"].startswith("✓ 已记录漏检")


def test_miss_with_garbage_time_is_400(env):
    r = client.post("/api/events/miss", json={"text": "abc"})
    assert r.status_code == 400
    assert "看不懂时刻" in r.json()["error"]


def test_new_tag_enters_library(env):
    r = client.post("/api/tags", json={"name": "新来的"})
    assert r.status_code == 200
    assert "新来的" in r.json()["tags"]
    assert client.post("/api/tags", json={"name": "新来的"}).json()["tags"].count(
        "新来的") == 1          # 重名不重复加


def test_blank_tag_name_is_400(env):
    assert client.post("/api/tags", json={"name": "  "}).status_code == 400
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_web_events.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'bbh.web.api_events'`

- [ ] **Step 3: 写实现**

`bbh/web/api_events.py`：

```python
"""剪辑台的行写操作：勾选 / 标签 / 删除 / 恢复 / 漏检 / 标签库。

一个端点 = 一个用户动作，写完返回「权威行 + 汇总」（state_payload）——
前端就地替换，不自己算统计，页脚数字与 sidecar 必然一致。

键（key）恒为原始检测时刻：勾选、标签、删除、裁决、归人五套 sidecar 都
按它存（显示时刻受时刻校准偏移影响，换偏移就丢）。

前后秒（before/after）**不落盘**（与旧界面一致：那只是会话内的切片参数，
重开页面回默认 5/2），所以这里没有对应端点——切片时前端把值随请求带上。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import app as A
from . import state
from .api_session import _session_video, state_payload

router = APIRouter(prefix="/api")


class EventPatch(BaseModel):
    selection: bool | None = None
    tag: str | None = None


class KeysIn(BaseModel):
    keys: list[str] = []


class MissIn(BaseModel):
    text: str = ""


class TagIn(BaseModel):
    name: str = ""


def _rows_and_index(vp: str, key: str):
    """当前未删视图的 7 元素行 + 该键的行号（键不存在 → 404）。"""
    _dur, rows = A.load_events_table(vp)
    keys = A._raw_time_keys(vp, rows)
    if key not in keys:
        raise HTTPException(404, f"当前列表里没有这条球：{key}"
                                 "（可能已删除或重跑过检测）")
    return rows, keys.index(key)


@router.patch("/events/{key}")
def patch_event(key: str, body: EventPatch):
    vp = _session_video()
    with state.video_lock(vp):
        rows, i = _rows_and_index(vp, key)
        if body.selection is not None:
            rows = [list(r) for r in rows]
            rows[i][0] = 1 if body.selection else 0
            A._save_selection(vp, rows)          # 等价旧界面 on_table_edit 的勾选列
        if body.tag is not None:
            # set_row_tag 会顺手自动勾选 + 同步已归人球的改判 + 落盘两张表，
            # 与旧界面走的是同一个函数
            _proj, rows, _panel = A.set_row_tag(vp, rows, i, body.tag)
    return state_payload()


@router.post("/events/miss")
def record_miss(body: MissIn):
    vp = _session_video()
    with state.video_lock(vp):
        msg, _box = A.record_miss(vp, body.text)
    payload = state_payload()
    payload["msg"] = msg
    return payload


@router.delete("/events")
def delete_events(body: KeysIn):
    vp = _session_video()
    with state.video_lock(vp):
        # 不过 _rows_and_index：delete_events 的契约是「批量宁丢失配键不
        # 中断整批」（重跑检测后旧面板残留的键被丢弃而不是报错），
        # 也不对已删球重复报 404。空 keys → 它自己抛「先在面板勾选」→ 400。
        _dur, rows = A.load_events_table(vp)
        msg, _rows2 = A.delete_events(vp, rows, body.keys)
    payload = state_payload()
    payload["msg"] = msg
    return payload


@router.post("/events/restore")
def restore_events():
    vp = _session_video()
    with state.video_lock(vp):
        restored = A.restore_deleted(vp)
    payload = state_payload()
    payload["msg"] = f"↩ 已恢复 {len(restored)} 条进球（回到未勾选/未归人状态）"
    return payload


@router.post("/tags")
def add_tag(body: TagIn):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "标签名不能为空")
    A.add_tag_to_library(name)
    return {"tags": A._load_tags()}


@router.get("/events")
def get_events():
    """行数据（spec 里点名的端点）。内容 = /api/state 的超集里的行部分，
    走同一个装配函数——两条路径永不分叉。前端通常只打 /api/state。"""
    return state_payload()
```

> 注意 `delete_events`：`app.delete_events(video_path, rows, keys)` 内部用
> `_raw_time_keys` 反查键，键不存在会被丢弃而不是报错（批量宁丢失配键不中断
> 整批——旧界面语义），所以路由**不**先过 `_rows_and_index`。`body.keys`
> 为空（或全失配）时该函数自己抛 gr.Error「先在面板勾选要删除的进球」→ 400。

`bbh/web/app.py` 的 `_register` 追加：

```python
    from . import api_events

    app.include_router(api_events.router)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_web_events.py`
Expected: 12 passed

- [ ] **Step 5: 提交**

```bash
git add bbh/web/api_events.py bbh/web/app.py tests/test_web_events.py
git commit -m "feat: 事件写操作 API——勾选/标签/删除/恢复/漏检/标签库（复用 app.py 原函数）"
```

---

## Task 6: 裁决与误标 API（feedback / flags）

**Files:**
- Create: `bbh/web/api_marks.py`
- Modify: `bbh/web/app.py`（`_register` 挂 api_marks）
- Test: `tests/test_web_marks.py`

**Interfaces:**
- Consumes: `api_events._rows_and_index`、`api_session._session_video` / `state_payload`、`app.mark_verdict`、`app.toggle_thumb_flag`、`app.toggle_cross_flag`。
- Produces: `POST /api/feedback/{key}`（`{"verdict": "goal"|"not_goal"}`）、`POST /api/flags/{key}`（`{"flag": "thumb"|"cross"}`）。

**两个概念的边界（旧界面已定案，别混）：**
- **进片勾选**（`selection.json`）= 人工核对「这球要进集锦」，`PATCH /api/events/{key} {selection}`。
- **裁决反馈**（`feedback.json`）= 给判定调优的标注（✅ low 球翻案 / ❌ 误判），`POST /api/feedback/{key}`。**正常确认进球不产生反馈数据**。
- **误标标记**（`thumb_flags.json` / `cross_flags.json`）= 缩略图/十字渲染有误，`POST /api/flags/{key}`，两类互相独立。

- [ ] **Step 1: 写失败测试**

`tests/test_web_marks.py`：

```python
"""裁决反馈与误标标记：各写各的 sidecar，互不干扰。"""
import json

import pytest
from fastapi.testclient import TestClient

from bbh.web import state
from bbh.web.app import create_app

from test_web_session import NAME, _make_result

client = TestClient(create_app())


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _make_result(tmp_path, n=3)
    state.SESSION.video_path = ""
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    yield tmp_path
    state.SESSION.video_path = ""


def _work(env, name):
    p = env / "work" / NAME / name
    return json.loads(p.read_text("utf-8")) if p.exists() else None


def test_not_goal_verdict_writes_feedback_with_evidence(env):
    r = client.post("/api/feedback/0:12.1", json={"verdict": "not_goal"})
    assert r.status_code == 200
    fb = _work(env, "feedback.json")
    assert fb["verdicts"][0]["verdict"] == "not_goal"
    assert fb["verdicts"][0]["t"] == pytest.approx(12.1)
    ev = fb["verdicts"][0]["evidence"]
    assert ev["cross_off"] == 0.02 and ev["above_n"] == 3   # 证据快照防重跑覆盖
    assert "已标记" in r.json()["msg"]


def test_same_verdict_twice_cancels(env):
    client.post("/api/feedback/0:12.1", json={"verdict": "not_goal"})
    r = client.post("/api/feedback/0:12.1", json={"verdict": "not_goal"})
    assert _work(env, "feedback.json")["verdicts"] == []
    assert "已取消" in r.json()["msg"]


def test_switching_verdict_overwrites(env):
    client.post("/api/feedback/0:12.1", json={"verdict": "not_goal"})
    r = client.post("/api/feedback/0:12.1", json={"verdict": "goal"})
    fid = _work(env, "feedback.json")["verdicts"]
    assert len(fid) == 1 and fid[0]["verdict"] == "goal"
    assert "已改判" in r.json()["msg"]


def test_bad_verdict_word_is_400(env):
    r = client.post("/api/feedback/0:12.1", json={"verdict": "maybe"})
    assert r.status_code == 400
    assert "verdict" in r.json()["error"]


def test_verdict_on_unknown_key_is_404(env):
    assert client.post("/api/feedback/9:99.9",
                        json={"verdict": "goal"}).status_code == 404


def test_feedback_summary_counts_reach_the_payload(env):
    client.post("/api/feedback/0:12.1", json={"verdict": "not_goal"})
    client.post("/api/feedback/1:12.1", json={"verdict": "goal"})
    fb = client.get("/api/state").json()["feedback"]
    assert fb["goal"] == 1 and fb["no"] == 1 and fb["miss"] == 0
    assert "已裁决" in fb["summaryMd"]


def test_thumb_flag_toggles_and_is_independent(env):
    r = client.post("/api/flags/0:12.1", json={"flag": "thumb"})
    assert r.status_code == 200
    assert _work(env, "thumb_flags.json") == ["00:12.1"]
    assert _work(env, "cross_flags.json") is None       # 互不干扰
    assert _work(env, "feedback.json") is None          # 误标 ≠ 裁决
    assert r.json()["flags"]["thumb"] == ["00:12.1"]
    assert r.json()["msg"] == "⚠ 已标记 1 张缩略图有误：0:12.1"
    r2 = client.post("/api/flags/0:12.1", json={"flag": "thumb"})   # 再点=取消
    assert _work(env, "thumb_flags.json") == []
    assert r2.json()["flags"]["thumb"] == []
    assert r2.json()["msg"] == "⚠ 缩略图有误"           # 清单空了 → 回按钮名


def test_cross_flag_uses_its_own_file(env):
    client.post("/api/flags/1:12.1", json={"flag": "cross"})
    assert _work(env, "cross_flags.json") == ["01:12.1"]
    assert client.get("/api/state").json()["flags"]["cross"] == ["01:12.1"]


def test_unknown_flag_name_is_400(env):
    r = client.post("/api/flags/0:12.1", json={"flag": "wat"})
    assert r.status_code == 400
    assert "flag" in r.json()["error"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_web_marks.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'bbh.web.api_marks'`

- [ ] **Step 3: 写实现**

`bbh/web/api_marks.py`：

```python
"""裁决反馈（调优标注）与误标标记（渲染质控）——两组独立的 sidecar。

裁决 = ✅「这条是进球」（低置信被门槛拦下的真球靠它翻案）/ ❌「未进球」，
存 feedback.json，带该行判定证据快照（防之后重跑检测覆盖证据）。
误标 = 缩略图有误 / 十字有误，存 thumb_flags.json / cross_flags.json，
两类可同标一球、互不影响。

三者都不改 events.json：反馈是给判定调优的、误标是给渲染质控的、
进片勾选是给生成的——各改各的档，复盘时可分别归因。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import app as A
from . import state
from .api_events import _rows_and_index
from .api_session import _session_video, state_payload

router = APIRouter(prefix="/api")

_VERDICTS = ("goal", "not_goal")
_FLAGS = {
    "thumb": (A.THUMB_FLAG_FILE, A.toggle_thumb_flag),
    "cross": (A.CROSS_FLAG_FILE, A.toggle_cross_flag),
}


class VerdictIn(BaseModel):
    verdict: str = ""


class FlagIn(BaseModel):
    flag: str = ""


@router.post("/feedback/{key}")
def post_feedback(key: str, body: VerdictIn):
    if body.verdict not in _VERDICTS:
        raise HTTPException(400, f"verdict 只能是 {' 或 '.join(_VERDICTS)}，"
                                 f"收到「{body.verdict}」")
    vp = _session_video()
    with state.video_lock(vp):
        rows, i = _rows_and_index(vp, key)
        msg = A.mark_verdict(vp, rows, i, body.verdict)
    payload = state_payload()
    payload["msg"] = msg
    return payload


@router.post("/flags/{key}")
def post_flag(key: str, body: FlagIn):
    entry = _FLAGS.get(body.flag)
    if entry is None:
        raise HTTPException(400, f"flag 只能是 {' 或 '.join(_FLAGS)}，"
                                 f"收到「{body.flag}」")
    _filename, toggle = entry
    vp = _session_video()
    with state.video_lock(vp):
        rows, i = _rows_and_index(vp, key)
        btn, md = toggle(vp, rows, i)
    payload = state_payload()
    # 标上时给「⚠ 已标记 N 张缩略图有误：键…」清单；取消后清单空 → 退回按钮名
    payload["msg"] = md or btn
    return payload
```

`bbh/web/app.py` 的 `_register` 追加：

```python
    from . import api_marks

    app.include_router(api_marks.router)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_web_marks.py`
Expected: 9 passed

- [ ] **Step 5: 提交**

```bash
git add bbh/web/api_marks.py bbh/web/app.py tests/test_web_marks.py
git commit -m "feat: 裁决反馈与误标标记 API——三种 sidecar 各自独立，证据快照随裁决存"
```

---

## Task 7: 出手人墙 API（两墙数据 + 归人/移出 + 护栏）

**Files:**
- Create: `bbh/web/api_wall.py`
- Modify: `bbh/web/app.py`（`_register` 挂 api_wall）
- Test: `tests/test_web_wall.py`

**Interfaces:**
- Consumes: `app.wall_payload` / `app.assigned_wall_payload` / `app.assign_person` / `app.unassign_person` / `app._WALL_MODES` / `app.load_events_table` / `app._tag_choices`、`manual_groups.load`、`appearance.gap_violations` / `appearance.MIN_GOAL_GAP_S`、`api_session._media_url`。
- Produces: `GET /api/wall?sort=`、`POST /api/wall/assign`、`POST /api/wall/unassign`、`GET /api/wall/guard?person=`。墙数据项结构 `{"key", "caption", "thumb", "person"}`（`person` 仅已归人墙有）；顶层含 `unassigned` / `assigned` / `people` / `manual_groups` / `personChoices` / `sorts` / `minGapS` / `msg`。

**说明：** 归人来源恒为「墙上点选」（`keys`），表格勾选是「人工核对确认进球」标记、与归人无关（2026-09-06 用户定案）。护栏不只是提示——`assign_person` 查的是**归人后该人整组**（单球增量违例会跨在旧组与新球之间，只查本次点选永远不触发）。

- [ ] **Step 1: 写失败测试**

`tests/test_web_wall.py`：

```python
"""出手人墙：两墙数据、归人/移出、护栏、排序模式。"""
import json

import pytest
from fastapi.testclient import TestClient

from bbh.web import state
from bbh.web.app import create_app

from test_web_session import NAME, _make_result

client = TestClient(create_app())


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _make_result(tmp_path, n=3)
    work = tmp_path / "work" / NAME
    # 回扫产物：3 球各一张缩略图 + traces（墙只显示缩略图存在的球）
    (work / "shots").mkdir(exist_ok=True)
    traces = {}
    for i, key in enumerate(("00:12.1", "01:12.1", "02:12.1")):
        p = work / "shots" / f"{i}.jpg"
        p.write_bytes(b"\xff\xd8\xff\xe0jpg")
        traces[key] = {"status": "ok", "release_t": 12.1 + i * 60,
                       "thumbs": [f"shots/{i}.jpg"]}
    (work / "shooters.json").write_text(
        json.dumps({"traces": traces}, ensure_ascii=False), encoding="utf-8")
    state.SESSION.video_path = ""
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    yield tmp_path
    state.SESSION.video_path = ""


def test_wall_lists_all_scanned_balls_unassigned(env):
    j = client.get("/api/wall").json()
    assert [x["key"] for x in j["unassigned"]] == ["00:12.1", "01:12.1", "02:12.1"]
    assert j["assigned"] == []
    assert j["people"] == []
    assert j["unassigned"][0]["thumb"].startswith("/media/work?path=")
    assert j["sorts"] == ["时间", "外观相似", "出手点"]
    assert j["minGapS"] > 0               # 护栏阈值随数据下发，前端照实显示


def test_unknown_sort_falls_back_to_time(env):
    assert client.get("/api/wall", params={"sort": "乱写"}).status_code == 200


def test_assign_moves_ball_to_person_and_writes_groups(env):
    r = client.post("/api/wall/assign",
                    json={"person": "老杨", "keys": ["00:12.1", "01:12.1"]})
    assert r.status_code == 200
    j = r.json()
    assert j["people"] == ["老杨"]
    grp = [g for g in j["assigned"] if g["person"] == "老杨"][0]
    assert [x["key"] for x in grp["items"]] == ["00:12.1", "01:12.1"]
    assert [x["key"] for x in j["unassigned"]] == ["02:12.1"]
    assert sorted(j["manual_groups"]["老杨"]) == ["00:12.1", "01:12.1"]
    # 归人时新输入的名字**不进全局标签库**，但必须进归人下拉候选
    # （否则再想把这球归给同一个人就选不到了）
    assert "老杨" in j["personChoices"]
    assert j["msg"].startswith("✅ 2 球 → 老杨")


def test_guard_silent_for_far_apart_goals(env):
    """夹具相邻两球差 60s ≫ MIN_GOAL_GAP_S(2.5s)——同人完全可能，不警示。"""
    client.post("/api/wall/assign", json={"person": "老杨", "keys": ["00:12.1"]})
    r = client.post("/api/wall/assign",
                    json={"person": "老杨", "keys": ["01:12.1"]})
    assert "护栏" not in r.json()["msg"]


def test_guard_triggers_on_close_pair(env):
    """00:12.1 与 00:12.5 相隔 0.4s < 2.5s——同一人物理上不太可能，出护栏警示。
    （护栏警示不阻断：两球照样都归到老杨名下。）"""
    work = env / "work" / NAME
    traces = json.loads((work / "shooters.json").read_text("utf-8"))
    p = work / "shots" / "close.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0jpg")
    traces["traces"]["00:12.5"] = {"status": "ok", "release_t": 12.5,
                                  "thumbs": ["shots/close.jpg"]}
    (work / "shooters.json").write_text(json.dumps(traces, ensure_ascii=False),
                                        encoding="utf-8")
    client.post("/api/wall/assign", json={"person": "老杨", "keys": ["00:12.1"]})
    r = client.post("/api/wall/assign", json={"person": "老杨", "keys": ["00:12.5"]})
    assert "护栏" in r.json()["msg"] and "0.4s" in r.json()["msg"]
    assert r.json()["msg"].startswith("✅ 2 球 → 老杨")   # 警示在后，归人照做


def test_assign_without_keys_is_400(env):
    r = client.post("/api/wall/assign", json={"person": "老杨", "keys": []})
    assert r.status_code == 400
    assert "墙上点选" in r.json()["error"]


def test_assign_with_blank_person_is_400(env):
    r = client.post("/api/wall/assign", json={"person": "  ", "keys": ["00:12.1"]})
    assert r.status_code == 400
    assert "人名" in r.json()["error"]


def test_unassign_returns_ball_to_pool(env):
    client.post("/api/wall/assign", json={"person": "老杨", "keys": ["00:12.1"]})
    r = client.post("/api/wall/unassign", json={"keys": ["00:12.1"]})
    assert r.status_code == 200
    j = r.json()
    assert j["assigned"] == [] and len(j["unassigned"]) == 3
    assert j["manual_groups"] == {}


def test_unassign_keeps_tag_and_selection(env):
    """移出归人只动 manual_groups，标签与核对勾选都不动。"""
    client.patch("/api/events/0:12.1", json={"tag": "老杨"})     # 打标签=自动勾选
    client.post("/api/wall/assign", json={"person": "老杨", "keys": ["00:12.1"]})
    client.post("/api/wall/unassign", json={"keys": ["00:12.1"]})
    j = client.get("/api/state").json()
    row = [x for x in j["rows"] if x["key"] == "00:12.1"][0]
    assert row["tag"] == "老杨" and row["selection"] is True


def test_guard_endpoint_reports_group_and_violations(env):
    client.post("/api/wall/assign", json={"person": "老杨", "keys": ["00:12.1"]})
    j = client.get("/api/wall/guard", params={"person": "老杨"}).json()
    assert j["person"] == "老杨" and j["n"] == 1 and j["keys"] == ["00:12.1"]
    assert j["violations"] == []
    assert j["msg"] == ""              # 无违例 = 无话可说，前端不弹


def test_guard_on_unknown_person_is_empty(env):
    j = client.get("/api/wall/guard", params={"person": "查无此人"}).json()
    assert j["n"] == 0 and j["keys"] == [] and j["violations"] == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_web_wall.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'bbh.web.api_wall'`

- [ ] **Step 3: 写实现**

`bbh/web/api_wall.py`：

```python
"""出手人墙：未归人墙 + 已归人分组调整墙。

归人来源恒为墙上点选（keys）——表格勾选是「人工核对确认进球」标记，
和出手人无关（2026-09-06 用户定案：是否进球与出手人两码事）。

护栏 = MIN_GOAL_GAP_S 内的两球判同一人（物理上不太可能）就警示，
但不阻断（手动永远赢）。查的口径是「归人后该人整组」：单球增量时
违例跨在旧组与新球之间，只查本次点选永远不触发。

已删球两墙都不上（剪辑台删除级联出手人侧）——由 wall_payload 内部消化。
缩略图不存在的球也不上墙（trace 有但图丢了），表格标签列仍可改判它们。
"""
from fastapi import APIRouter, Query

from .. import app as A
from .. import appearance
from .. import manual_groups as manual
from . import state
from .api_session import _media_url, _session_video

router = APIRouter(prefix="/api/wall")


def _items(pairs: list, person: str = "") -> list[dict]:
    """[(缩略图绝对路径, caption, key)] → 前端项。caption 里的 ⚠定位失效
    留在 caption 里展示，key 恒干净（第三位）——点选/归人只认 key。"""
    out = []
    for p, cap, key in pairs:
        d = {"key": key, "caption": cap, "thumb": _media_url(p)}
        if person:
            d["person"] = person
        out.append(d)
    return out


def _wall_state(vp: str, msg: str = "") -> dict:
    groups = manual.load(A._work_dir_for(vp))
    pairs = A.assigned_wall_payload(vp)          # 一次算好，按 key 分到各人名下
    assigned = []
    for person in sorted(groups):
        mine = [x for x in pairs if x[2] in groups[person]]
        if mine:
            assigned.append({"person": person, "items": _items(mine, person)})
    return {"unassigned": _items(A.wall_payload(vp)),
            "assigned": assigned,
            "people": sorted(groups),
            "manual_groups": {p: list(ks) for p, ks in groups.items()},
            # 归人下拉的候选 = 全局标签库 ∪ 本视频已归人人名（归人时新输入的
            # 名字不进全局库，但剪辑台/墙都得能再选到）——用 app 的唯一源，
            # 别在前端自己 union，否则两页的候选集迟早分叉。
            "personChoices": A._tag_choices(vp),
            "sorts": list(A._WALL_MODES),
            "minGapS": appearance.MIN_GOAL_GAP_S,
            "msg": msg}


def _rows(vp: str):
    _dur, rows = A.load_events_table(vp)
    return rows


@router.get("")
def get_wall(sort: str = Query("时间")):
    vp = _session_video()
    # 排序模式由引擎的 wall_order 决定；传未知值退化成时间序（不报错——
    # 前端下拉换项时不该因为拼错一个词就整墙空白）
    payload = _wall_state(vp)
    if sort != "时间":
        payload["unassigned"] = _items(A.wall_payload(vp, sort))
    payload["sort"] = sort if sort in A._WALL_MODES else "时间"
    return payload


@router.post("/assign")
def assign(body: dict):
    vp = _session_video()
    with state.video_lock(vp):
        msg, _rows2, _html, _choices = A.assign_person(
            vp, _rows(vp), body.get("person", ""), body.get("keys") or [])
    return _wall_state(vp, msg)


@router.post("/unassign")
def unassign(body: dict):
    vp = _session_video()
    with state.video_lock(vp):
        msg, _rows2, _html, _choices = A.unassign_person(
            vp, _rows(vp), body.get("keys") or [])
    return _wall_state(vp, msg)


@router.get("/guard")
def guard(person: str = Query("")):
    """归人前护栏读数：该人整组的球 + 组内 MIN_GOAL_GAP 违例对。

    只读——真正的护栏判定发生在每次 assign 里（那边查的是归人后的组）。"""
    vp = _session_video()
    keys = list(manual.load(A._work_dir_for(vp)).get((person or "").strip(), []))
    gaps = appearance.gap_violations(keys)
    msg = (f"⚠️ 护栏：{' / '.join(f'{a}↔{b} 仅 {g}s' for a, b, g in gaps)}"
           f" < {appearance.MIN_GOAL_GAP_S}s，物理上不太可能同人，请核对"
           ) if gaps else ""
    return {"person": person, "n": len(keys), "keys": keys,
            "violations": [{"a": a, "b": b, "gapS": g} for a, b, g in gaps],
            "msg": msg}
```

`bbh/web/app.py` 的 `_register` 追加：

```python
    from . import api_wall

    app.include_router(api_wall.router)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_web_wall.py`
Expected: 11 passed

- [ ] **Step 5: 提交**

```bash
git add bbh/web/api_wall.py bbh/web/app.py tests/test_web_wall.py
git commit -m "feat: 出手人墙 API——两墙数据/归人/移出/护栏（护栏查归人后整组）"
```

---

## Task 8: 标定向导 API（抽帧 / 点选 / 生成 / 微调 / 多段）

**Files:**
- Create: `bbh/web/api_calib.py`
- Modify: `bbh/web/app.py`（`_register` 挂 api_calib）
- Test: `tests/test_web_calib.py`

**Interfaces:**
- Consumes: `state.SESSION`（`frame_rgb` / `frame_png` / `preview_png` / `rev` / `points` / `seg_t`，见 Task 3）、`app.extract_frame_at` / `app.click_hint` / `app.build_calibration` / `app.build_segment_calibration` / `app.refine_hsv` / `app.load_segments` / `app.del_segment` / `app._seg_list_md` / `app._seg_choices` / `app._calib_ready` / `app._load_calib_or_none` / `app._calib_file` / `app._fmt_mmss`。
- Produces: `GET /api/calib/state`、`GET /api/calib/frame.png?t=`、`GET /api/calib/preview.png`、`POST /api/calib/point`、`DELETE /api/calib/point`、`POST /api/calib/build`、`POST /api/calib/refine`、`GET /api/calib/segments`、`DELETE /api/calib/segments/{i}`、`GET /api/calib/existing`。

**图片怎么给浏览器（关键设计）：** 旧界面把帧和预览当 `gr.Image` 直接传 numpy 数组。HTTP 下**不落盘**（`work/` 的 sidecar 红线，不让标定页往 work/ 里塞 PNG），改成：帧/预览各编码成 PNG 字节放会话内存，用 `/api/calib/frame.png`、`/api/calib/preview.png` 出，URL 挂 `?v=<rev>` 击穿缓存（`rev` 每次重算递增，同旧界面 `preview.mp4` 的 mtime 套路）。

- [ ] **Step 1: 写失败测试**

`tests/test_web_calib.py`：

```python
"""标定向导：抽帧出 PNG、点选累积、生成标定落盘、微调、多段管理。"""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from bbh.models import Calibration
from bbh.web import state
from bbh.web.app import create_app

client = TestClient(create_app())

NAME = "videofile"


@pytest.fixture()
def env(sample_video, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "work").mkdir(exist_ok=True)
    state.SESSION.video_path = sample_video
    client.post("/api/session", json={"videoPath": sample_video})
    yield tmp_path
    state.SESSION.video_path = ""
    state.SESSION.frame_rgb = None
    state.SESSION.points = []


def test_state_reports_nothing_calibrated_yet(env):
    j = client.get("/api/calib/state").json()
    assert j["ready"] is False and j["segments"] == []
    assert j["points"] == [] and j["canBuild"] is False
    assert "筐口左端" in j["hint"]          # click_hint 的原文，别改写


def test_frame_endpoint_returns_png(env):
    r = client.get("/api/calib/frame.png", params={"t": 1.0})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert state.SESSION.frame_rgb is not None
    assert state.SESSION.frame_rgb.ndim == 3


def test_frame_failure_surfaces_as_400(env, monkeypatch):
    """抽帧失败（编码解不了/超时长）→ 抽帧函数抛 gr.Error → 400 + 原文。
    这里换成确定性桩，不去赌 ffmpeg 对超时长 t 的行为。"""
    import gradio as gr
    import bbh.app as A
    monkeypatch.setattr(A, "extract_frame_at",
                        lambda vp, t: (_ for _ in ()).throw(
                            gr.Error("在 999999 秒处取帧失败。诊断：…")))
    r = client.get("/api/calib/frame.png", params={"t": 999999})
    assert r.status_code == 400
    assert "取帧失败" in r.json()["error"]


def test_points_accumulate_and_hint_advances(env):
    client.get("/api/calib/frame.png", params={"t": 1.0})
    a = client.post("/api/calib/point", json={"x": 10, "y": 20}).json()
    assert a["points"] == [[10.0, 20.0]] and a["canBuild"] is False
    assert "筐口右端" in a["hint"]
    b = client.post("/api/calib/point", json={"x": 30, "y": 20}).json()
    assert "球" in b["hint"] and b["canBuild"] is False
    c = client.post("/api/calib/point", json={"x": 20, "y": 20}).json()
    assert len(c["points"]) == 3 and c["canBuild"] is True


def test_clear_points(env):
    client.post("/api/calib/point", json={"x": 1, "y": 2})
    j = client.request("DELETE", "/api/calib/point").json()
    assert j["points"] == [] and "筐口左端" in j["hint"]


def test_build_writes_calibration_and_returns_preview(env):
    client.get("/api/calib/frame.png", params={"t": 1.0})
    for x, y in ((10, 20), (30, 20), (20, 20)):      # 筐口左/右 + 球
        client.post("/api/calib/point", json={"x": x, "y": y})
    r = client.post("/api/calib/build", json={})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["msg"] and "标定" in j["msg"]
    assert j["previewUrl"].startswith("/api/calib/preview.png?v=")
    assert j["ready"] is True
    work = list((env / "work").iterdir())[0]
    assert (work / "calibration.json").is_file()
    assert (work / "calibration_ref.png").is_file()  # 参考帧随标定存档（REF_IMG_FILE）
    prev = client.get("/api/calib/preview.png")
    assert prev.status_code == 200 and prev.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_preview_404_before_build(env):
    assert client.get("/api/calib/preview.png").status_code == 404


def test_build_with_too_few_points_is_400(env):
    client.get("/api/calib/frame.png", params={"t": 1.0})
    client.post("/api/calib/point", json={"x": 10, "y": 20})
    r = client.post("/api/calib/build", json={})
    assert r.status_code == 400
    assert "3" in r.json()["error"]


def test_build_without_frame_is_400(env):
    r = client.post("/api/calib/build", json={})
    assert r.status_code == 400
    assert "参考视频" in r.json()["error"] or "抽帧" in r.json()["error"]


def test_segment_requires_t_after_main(env):
    client.get("/api/calib/frame.png", params={"t": 1.0})
    for x, y in ((10, 20), (30, 20), (20, 20)):
        client.post("/api/calib/point", json={"x": x, "y": y})
    r = client.post("/api/calib/build", json={"segT": 300.0})   # 还没主标定
    assert r.status_code == 400
    assert "主标定" in r.json()["error"]


def test_segment_build_and_delete(env):
    client.get("/api/calib/frame.png", params={"t": 1.0})
    for x, y in ((10, 20), (30, 20), (20, 20)):
        client.post("/api/calib/point", json={"x": x, "y": y})
    client.post("/api/calib/build", json={})                    # 主标定
    seg = client.post("/api/calib/build", json={"segT": 300.0}).json()
    assert "新段已存" in seg["msg"]
    assert len(seg["segments"]) == 1 and "段2" in seg["segListMd"]
    d = client.request("DELETE", "/api/calib/segments/2").json()
    assert d["segments"] == [] and "已删除" in d["msg"]


def test_delete_main_segment_is_400(env):
    client.get("/api/calib/frame.png", params={"t": 1.0})
    for x, y in ((10, 20), (30, 20), (20, 20)):
        client.post("/api/calib/point", json={"x": x, "y": y})
    client.post("/api/calib/build", json={})
    r = client.request("DELETE", "/api/calib/segments/1")
    assert r.status_code == 400


def test_refine_changes_s_low_and_reports_hits(env):
    client.get("/api/calib/frame.png", params={"t": 1.0})
    r = client.post("/api/calib/refine", json={"sLow": 90, "vLow": 100})
    assert r.status_code == 200
    assert "S 下限 90" in r.json()["msg"]
    assert r.json()["previewUrl"].startswith("/api/calib/preview.png?v=")


def test_refine_before_build_is_400(env):
    client.get("/api/calib/frame.png", params={"t": 1.0})
    r = client.post("/api/calib/refine", json={"sLow": 90, "vLow": 100})
    assert r.status_code == 400
    assert "生成标定" in r.json()["error"]


def test_existing_reports_saved_calibration(env):
    client.get("/api/calib/frame.png", params={"t": 1.0})
    for x, y in ((10, 20), (30, 20), (20, 20)):
        client.post("/api/calib/point", json={"x": x, "y": y})
    client.post("/api/calib/build", json={})
    j = client.get("/api/calib/existing").json()
    assert j["ready"] is True
    assert j["calibration"]["hsvLow"][1] >= 0
    assert j["files"] and "calibration.json" in j["files"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_web_calib.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'bbh.web.api_calib'`

- [ ] **Step 3: 写实现**

`bbh/web/api_calib.py`：

```python
"""标定向导：四步（选视频 → 点筐口左/右端 + 球 → 验证预览 → HSV 微调）。

点选顺序是硬约定：第 1、2 点 = 筐口左端、右端，第 3 点起 = 球（1-3 次）——
由 app._calib_from_pts 解释，这里只负责把点按序攒进会话。

图片不落盘：帧/预览 PNG 放会话内存，用 /api/calib/{frame,preview}.png 出，
URL 带 ?v=<rev> 击穿缓存（每次重算 rev+1）。旧界面靠 gr.Image 传数组，
HTTP 下换成这招，对 work/ 目录零写入。
"""
import os

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from .. import app as A
from . import state
from .api_session import _session_video

router = APIRouter(prefix="/api/calib")


class PointIn(BaseModel):
    x: float
    y: float


class BuildIn(BaseModel):
    segT: float | None = None      # 给了 = 补段标定（镜头移动后），不给 = 主标定


class RefineIn(BaseModel):
    sLow: int
    vLow: int


def _session():
    return state.SESSION


def _rev_url(name: str) -> str:
    s = _session()
    s.rev += 1
    return f"/api/calib/{name}.png?v={s.rev}"


def _encode(rgb) -> bytes:
    bgr = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".png", bgr)
    if not ok:
        raise HTTPException(500, "预览图编码失败")
    return buf.tobytes()


def _meta(msg: str = "") -> dict:
    s = _session()
    vp = s.video_path
    return {"points": [[float(x), float(y)] for x, y in s.points],
            "hint": A.click_hint(s.points),
            "canBuild": len(s.points) >= 3 and s.frame_rgb is not None,
            "ready": A._calib_ready(vp),
            "segments": A._seg_choices(vp),
            "segListMd": A._seg_list_md(vp),
            "hasPreview": s.preview_png is not None,
            "msg": msg}


@router.get("/state")
def calib_state():
    return _meta()


@router.get("/frame.png")
def frame_png(t: float = Query(0.0)):
    """跳时刻抽帧（换参考画面）。抽帧函数自己带诊断信息报错 → 400。

    前端 <img> 的 src 用 `/api/calib/frame.png?t={t}&v={rev}`——t 变了 URL
    就变，但同一 t 重抽（换视频后回到同一时刻）得靠 rev 击穿浏览器缓存。"""
    vp = _session_video()
    frame, _pts, _hint, _preview, _msg = A.extract_frame_at(vp, t)
    s = _session()
    s.frame_rgb = frame
    s.frame_png = _encode(frame)
    s.points = []
    s.seg_t = float(t or 0)
    s.preview_png = None            # 换参考帧 = 旧预览作废（与旧界面同款）
    s.rev += 1
    return Response(content=s.frame_png, media_type="image/png")


@router.get("/preview.png")
def preview_png():
    png = _session().preview_png
    if png is None:
        raise HTTPException(404, "还没有可看的预览——先点「生成标定」")
    return Response(content=png, media_type="image/png")


@router.post("/point")
def add_point(body: PointIn):
    """记一个点。不加校验让点越攒越多——点错就删重点（DELETE 清空）。"""
    s = _session()
    s.points.append((float(body.x), float(body.y)))
    return _meta()


@router.delete("/point")
def clear_points():
    s = _session()
    s.points = []
    return _meta()


@router.post("/build")
def build(body: BuildIn):
    s = _session()
    vp = _session_video()
    if s.frame_rgb is None:
        raise HTTPException(400, "请先抽帧（选参考画面）再生成标定")
    if body.segT is not None:
        preview, msg, seg_md, _dd = A.build_segment_calibration(
            s.points, s.frame_rgb, body.segT, vp)
    else:
        preview, msg, _s_low, _v_low = A.build_calibration(
            s.points, s.frame_rgb, vp)
        seg_md = ""
    s.preview_png = _encode(preview)
    payload = _meta(msg)
    payload.update(previewUrl=_rev_url("preview"), segListMd=seg_md)
    return payload


@router.post("/refine")
def refine(body: RefineIn):
    s = _session()
    vp = _session_video()
    preview, msg = A.refine_hsv(s.frame_rgb, body.sLow, body.vLow, vp)
    s.preview_png = _encode(preview)
    payload = _meta(msg)
    payload["previewUrl"] = _rev_url("preview")
    return payload


@router.get("/segments")
def segments():
    vp = _session_video()
    return {"segments": A._seg_choices(vp), "segListMd": A._seg_list_md(vp)}


@router.delete("/segments/{i}")
def delete_segment(i: int):
    """删补充段。段 1 是主标定（由「生成标定」覆盖管理），不可删——
    del_segment 自己会报「只有一段主标定」→ 400。"""
    vp = _session_video()
    choices = A._seg_choices(vp)
    if not 2 <= i <= len(choices) + 1:
        raise HTTPException(400, "所选段不在列表里（页面数据过期），请刷新页面重试")
    seg_md, _dd, msg = A.del_segment(choices[i - 2], vp)
    return {"segments": A._seg_choices(vp), "segListMd": seg_md, "msg": msg}


@router.get("/existing")
def existing():
    """查看已有标定：参考帧存档、宽色域提示、段列表、work 目录里的标定文件。"""
    vp = _session_video()
    calib_p = A._calib_file(vp)
    calib = A._load_calib_or_none(vp)
    d = A._calib_dir(vp) or ""
    files = []
    if d and os.path.isdir(d):      # calibration.json / calibration_ref.png / calibration_segN.json
        files = sorted(n for n in os.listdir(d) if n.startswith("calib"))
    return {"ready": calib is not None,
            "hasReferenceFrame": bool(calib and calib.ref_image),
            "calibT": getattr(calib, "calib_t", 0.0) if calib else 0.0,
            "calibration": ({"hsvLow": list(calib.hsv_low),
                             "hsvHigh": list(calib.hsv_high),
                             "hoopRect": list(calib.hoop_rect),
                             "frameSize": list(calib.frame_size)}
                            if calib else None),
            "wideHsvNote": A._wide_hsv_note(calib) if calib else "",
            "segments": A._seg_choices(vp),
            "segListMd": A._seg_list_md(vp),
            "calibPath": calib_p or "",
            "files": files}
```

`bbh/web/app.py` 的 `_register` 追加：

```python
    from . import api_calib

    app.include_router(api_calib.router)
```

> `_seg_choices` 的索引口径：返回的是**段 2 起**的标签列表（段 1 主标定不在
> 里面）。所以「段 N」（N≥2）对应 `choices[N-2]`，`N == len(choices)+1` 是上限。

- [ ] **Step 4: 跑测试确认通过**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_web_calib.py`
Expected: 15 passed

- [ ] **Step 5: 提交**

```bash
git add bbh/web/api_calib.py bbh/web/app.py tests/test_web_calib.py
git commit -m "feat: 标定向导 API——抽帧/点选/主标定/补段/微调/删段（预览走内存 PNG 不落盘）"
```

---

## Task 9: 长任务 API（检测 / 回扫 / 生成 + job 查询）

**Files:**
- Create: `bbh/web/api_gen.py`
- Modify: `bbh/web/app.py`（`_register` 挂 api_gen）
- Test: `tests/test_web_jobs_api.py`

**Interfaces:**
- Consumes: `jobs.REGISTRY` / `jobs.JobBusy`、`app.start_detection` / `app._refresh_status` / `app._state` / `app.run_shooter_tracing` / `app.gen_selected` / `app.open_gen_dialog` / `app.load_events_table` / `app.GEN_ALL_LABEL`、`api_session._session_video` / `_media_url`。
- Produces: `POST /api/detect`、`POST /api/wall/scan`、`POST /api/gen`、`GET /api/jobs/{id}`、`GET /api/gen/choices`。

**两种任务的接线差异（读 app.py 时最容易踩的地方）：**
- **检测**不是生成器：它在**后台线程**里跑，进度在模块级 `app._state`（`running` / `done_frames` / `total_frames` / `error` / `cancelled` / `done_msg`），由 `app._refresh_status()` 读成 `(文案, pct)`。所以 job 的 worker 只干两件事：调一次 `start_detection`（同步守卫：未标定/视频不存在/已有检测在跑 → `gr.Error` → 400），然后轮询 `_state` 直到 `running` 变 False。
- **回扫/生成**是生成器：每个 `yield` 就是一次进度。取哪一位是文案不一样——见下面 `_MSG_IDX`：

| 生成器 | 进度文案位置 | 返回值位置 |
|---|---|---|
| `run_shooter_tracing` | `out[0]` | 末次 `out[0]` = 汇总文案 |
| `gen_selected` | `out[4]` | 末次 `out[0]` = 首个成品路径、`out[4]` = 结果清单 |

- [ ] **Step 1: 写失败测试**

`tests/test_web_jobs_api.py`：

```python
"""长任务：守卫同步 400、job 进度轮询、同类单飞 409、生成结果。"""
import time

import pytest
from fastapi.testclient import TestClient

from bbh.web import state
from bbh.web.app import create_app

from test_web_session import NAME, _make_result

client = TestClient(create_app())


def _wait(job_id, timeout=20.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["done"]:
            return j
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} 超时未完成")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state.SESSION.video_path = ""
    yield tmp_path
    state.SESSION.video_path = ""
    import bbh.app as A
    A._state["running"] = False


def test_detect_without_calibration_is_400_before_any_job_starts(env):
    r = client.post("/api/session", json={"videoPath": "D:/videos/nocal.mp4"})
    assert r.status_code == 200
    r = client.post("/api/detect")
    assert r.status_code == 400
    assert "标定" in r.json()["error"]


def test_detect_without_session_is_400(env):
    assert client.post("/api/detect").status_code == 400


def test_gen_without_selection_is_400(env):
    _make_result(env, n=2)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    r = client.post("/api/gen", json={"vertical": False, "items": []})
    assert r.status_code == 400
    assert "至少勾选一项" in r.json()["error"]


def test_gen_choices_lists_collection_and_tags(env):
    _make_result(env, n=2)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    j = client.get("/api/gen/choices").json()
    assert j["choices"][0].startswith("视频合集")
    assert j["default"] == [j["choices"][0]]
    assert "📱" in j["stats"]          # vertical=true 时是竖屏运镜行


def test_job_endpoint_404_for_unknown_id(env):
    assert client.get("/api/jobs/nope").status_code == 404


def test_scan_requires_detection_results(env, tmp_path, monkeypatch):
    """有标定但没检测结果 → 回扫守卫报「尚无检测结果」→ 400。

    这条同时验证「守卫在起 job 之前同步跑」：拿到 400 而不是 jobId。
    """
    from bbh.models import Calibration
    work = tmp_path / "work" / "raw"
    work.mkdir(parents=True)
    Calibration(hoop_rect=(1, 2, 3, 4), hsv_low=(0, 80, 120),
                hsv_high=(25, 255, 255), frame_size=(1920, 1080)
                ).save(str(work / "calibration.json"))
    client.post("/api/session", json={"videoPath": "D:/videos/raw.mp4"})
    r = client.post("/api/wall/scan")
    assert r.status_code == 400
    assert "尚无检测结果" in r.json()["error"]


def test_scan_returns_job_id_then_finishes(env, monkeypatch):
    """用假生成器替掉真回扫：验证 job 接线（进度转发 + 末次结果），
    不跑真视频（真回扫要几分钟）。"""
    import bbh.app as A
    _make_result(env, n=2)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})

    def fake_trace(video_path):
        yield "⏳ 正在回扫 1/2…", None
        yield "⏳ 正在回扫 2/2…", None
        yield "✅ 回扫完成：2 球", None

    monkeypatch.setattr(A, "run_shooter_tracing", fake_trace)
    r = client.post("/api/wall/scan")
    assert r.status_code == 200, r.text
    j = _wait(r.json()["jobId"])
    assert j["state"] == "done" and j["pct"] == 100
    assert j["found"] is None                    # 回扫不上报球数（只有检测报）
    res = client.get(f"/api/jobs/{j['id']}/result").json()
    assert res["msg"] == "✅ 回扫完成：2 球"      # /jobs/{id} 只报进度，结果在 /result
    assert res["kind"] == "wall_scan"
    assert "rows" in res                         # 回扫完行数据一起刷（出手人列变了）


def test_single_flight_conflict_is_409(env, monkeypatch):
    import bbh.app as A
    import threading
    _make_result(env, n=1)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    gate = threading.Event()

    def slow_trace(video_path):
        gate.wait(5)
        yield "✅ 完成", None

    monkeypatch.setattr(A, "run_shooter_tracing", slow_trace)
    first = client.post("/api/wall/scan")
    assert first.status_code == 200
    second = client.post("/api/wall/scan")
    assert second.status_code == 409
    assert "已在跑" in second.json()["error"]
    gate.set()


def test_gen_job_reports_finished_videos(env, monkeypatch):
    """用假生成器替掉真生成：验 job 接线（进度转发 + 末次结果解析）。
    真生成要跑完整视频渲染，几分钟起步。"""
    import bbh.app as A
    _make_result(env, n=2)
    (env / "work" / NAME / "highlight.mp4").write_bytes(b"\x00" * 64)

    def fake_gen(video_path, rows, sel, vertical=True):
        yield (None, None, None, None, "⏳ 正在生成（1/1）：视频合集…", None)
        yield ("work/" + NAME + "/highlight.mp4", None, None, None,
               "✅ 已生成 1 个视频（预览位播放第一个，全部在 work/<视频名>/ 下）：\n"
               "· 视频合集（全部勾选的进球） → highlight.mp4", None)

    monkeypatch.setattr(A, "gen_selected", fake_gen)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    r = client.post("/api/gen", json={"vertical": False,
                                      "items": ["视频合集（全部勾选的进球）"]})
    assert r.status_code == 200, r.text
    j = _wait(r.json()["jobId"])
    assert j["state"] == "done"
    res = client.get(f"/api/jobs/{j['id']}/result").json()
    assert res["vertical"] is False
    assert res["videos"][0]["item"] == "视频合集（全部勾选的进球）"
    assert res["videos"][0]["name"] == "highlight.mp4"
    assert res["videos"][0]["url"].startswith("/media/work?path=")
    assert "&v=" in res["videos"][0]["url"]        # 带 mtime 击穿缓存
    assert res["firstUrl"] == res["videos"][0]["url"]


def test_gen_result_keeps_skipped_items_visible(env, monkeypatch):
    """全部跳过时 [0] 是 gr.update 而非路径、[4] 是 ❌ 文案——不假装成功，
    也不让前端去点一个不存在的成品。"""
    import bbh.app as A
    _make_result(env, n=2)

    def fake_gen(video_path, rows, sel, vertical=True):
        yield (None, None, None, None,
               "❌ 没有生成任何视频——视频合集：没有勾选的进球（跳过）", None)

    monkeypatch.setattr(A, "gen_selected", fake_gen)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    r = client.post("/api/gen", json={"vertical": True, "items": ["视频合集"]})
    assert r.status_code == 200, r.text
    res = client.get(f"/api/jobs/{_wait(r.json()['jobId'])['id']}/result").json()
    assert res["videos"] == [] and res["firstUrl"] is None
    assert "没有生成任何视频" in res["summaryMd"]

```

- [ ] **Step 2: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_web_jobs_api.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'bbh.web.api_gen'`

- [ ] **Step 3: 写实现**

`bbh/web/api_gen.py`：

```python
"""长任务：检测 / 回扫溯源 / 生成集锦。

三类任务都走 jobs.REGISTRY：同类单飞（第二次 409）、进度在 job 里、结果
在 /api/jobs/{id}/result。前端 0.8~1s 轮询一次。

守卫**同步跑**：前置条件不满足（未标定 / 无检测结果 / 没勾选）时，
POST 直接 400 把文案给出去，而不是先返回一个 jobId 再让它变红——
前端一步到位弹提示，少一次往返也少一种状态。

检测的进度不在生成器里而在模块级 app._state（旧界面靠 Timer 轮询它），
所以 job 的 worker 是个轮询循环；回扫/生成是生成器，直接转发 yield。
"""
import os
import time

from fastapi import APIRouter, HTTPException, Query

from .. import app as A
from . import state
from .api_session import _media_url, _session_video, state_payload
from .jobs import REGISTRY, JobBusy

router = APIRouter(prefix="/api")

_MSG_IDX = {"wall_scan": 0, "gen": 4}      # 各生成器里「进度文案」在第几位


def _start(kind: str, worker) -> dict:
    try:
        job = REGISTRY.start(kind, worker)
    except JobBusy as e:
        raise HTTPException(409, str(e))
    return {"jobId": job.id}


def _drive(gen, report, kind: str):
    """把生成器的 yield 转发成 job 进度；返回末次 yield（结果在这）。"""
    idx = _MSG_IDX[kind]
    last = None
    for out in gen:
        last = out
        text = out[idx] if isinstance(out, (tuple, list)) else out
        if isinstance(text, str):
            report(msg=text)
    return last


def _poll_detect(report) -> dict:
    """检测已在后台线程里跑（route 里同步起好了），这里只报进度等它完。"""
    while A._state.get("running"):
        msg, pct = A._refresh_status()
        report(msg=msg, pct=pct)
        time.sleep(0.5)
    _msg, pct = A._refresh_status()
    report(pct=pct)
    if A._state.get("error"):
        raise RuntimeError(A._state["error"])
    if A._state.get("cancelled"):
        return {"cancelled": True,
                "msg": "⏹ 已取消——检查点已保存，再点「开始检测」可续跑"}
    return {"msg": A._state.get("done_msg") or "✅ 完成"}


@router.post("/detect")
def detect():
    vp = _session_video()
    if REGISTRY.running_kind("detect"):
        raise HTTPException(409, "已有检测在跑")
    # start_detection 起线程后立刻返回；前置守卫（未标定 / 视频不存在 /
    # 已有检测）在它里面同步抛 gr.Error → 400
    A.start_detection(vp)
    return _start("detect", lambda report: _poll_detect(report))


@router.post("/wall/scan")
def wall_scan():
    vp = _session_video()
    if not A._calib_ready(vp):
        raise HTTPException(400, "本视频还没标定——先去「⚙ 标定设置」完成一次")
    data = A._load_events_or_none(vp)
    if not data or not data.get("events"):
        raise HTTPException(400, "尚无检测结果——先点「▶ 开始检测」")
    return _start("wall_scan", lambda report: {
        "msg": str((_drive(A.run_shooter_tracing(vp), report, "wall_scan")
                    or ["✅ 完成"])[0])})


@router.post("/gen")
def gen(body: dict):
    vp = _session_video()
    items = [str(s) for s in (body.get("items") or []) if str(s).strip()]
    if not items:
        raise HTTPException(400, "至少勾选一项（视频合集或任一标签）")
    vertical = bool(body.get("vertical", True))
    _dur, rows = A.load_events_table(vp)
    work = A._work_dir_for(vp)
    return _start("gen", lambda report: _gen_result(
        _drive(A.gen_selected(vp, rows, items, vertical=vertical),
               report, "gen"), work, vertical))


def _path_in(work: str, p: str) -> str:
    """成品路径归一：管线返回的是 os.path.join(work_dir, name)，而 work_dir
    本身就是相对的 `work/<视频名>`——所以 [0] 里已经是带目录的路径；但结果
    清单里写的是裸文件名（basename）。按有没有目录前缀决定要不要拼。"""
    return p if os.path.dirname(p) else os.path.join(work, p)


def _gen_result(last, work: str, vertical: bool) -> dict:
    """末次 yield = (成品播放, 预览占位, …, 结果清单, …)。

    全跳过时 [0] 是 gr.update（不是路径），所以成品清单从 [4] 的结果清单
    里按 `· 项名 → 文件名` 反解；跳过项留在 summaryMd 里让用户看见
    （不假装成功）。gen_selected 只把已生成的写进那几行。
    """
    summary = ""
    first = None
    if isinstance(last, (tuple, list)):
        first = last[0] if isinstance(last[0], str) else None
        summary = last[4] if len(last) > 4 and isinstance(last[4], str) else ""
    videos = []
    for line in summary.splitlines():
        if line.startswith("· ") and " → " in line:
            item, name = line[2:].split(" → ", 1)
            videos.append({"item": item.strip(), "name": name.strip(),
                           "url": _media_url(_path_in(work, name.strip()))})
    first_url = _media_url(_path_in(work, first)) if first else None
    return {"summaryMd": summary, "vertical": vertical, "videos": videos,
            "firstUrl": first_url or (videos[0]["url"] if videos else None)}


@router.get("/gen/choices")
def gen_choices(vertical: bool = Query(True)):
    """生成弹窗的清单：视频合集 + 标签库每个标签，默认只勾合集。

    open_gen_dialog 返回的 choices 是 gr.update(...)，在 gradio 5 里就是个
    dict（{"choices": [...], "value": [...]}）——按 dict 取，别按属性取。"""
    vp = _session_video()
    _dur, rows = A.load_events_table(vp)
    upd, _vis, stats, _v = A.open_gen_dialog(vp, rows, bool(vertical))
    upd = upd if isinstance(upd, dict) else {}
    return {"choices": list(upd.get("choices") or []),
            "default": list(upd.get("value") or []),
            "stats": stats, "vertical": bool(vertical)}


@router.get("/jobs/{job_id}")
def job_state(job_id: str):
    job = REGISTRY.get(job_id)
    if job is None:
        raise HTTPException(404, f"没有这个任务：{job_id}（服务可能重启过）")
    return job.as_dict()


@router.get("/jobs/{job_id}/result")
def job_result(job_id: str):
    job = REGISTRY.get(job_id)
    if job is None:
        raise HTTPException(404, f"没有这个任务：{job_id}（服务可能重启过）")
    if job.state == "running":
        raise HTTPException(409, "任务还在跑——先等到 done 再来取结果")
    if job.state == "error":
        raise HTTPException(400, job.error or "任务失败")
    payload = dict(job.result or {})
    payload["id"] = job.id
    payload["kind"] = job.kind
    if job.kind in ("detect", "wall_scan"):
        payload.update(state_payload())     # 检测/回扫完 → 行数据与墙一起刷新
    return payload
```

`bbh/web/app.py` 的 `_register` 追加：

```python
    from . import api_gen

    app.include_router(api_gen.router)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_web_jobs_api.py`
Expected: 10 passed

- [ ] **Step 5: 提交**

```bash
git add bbh/web/api_gen.py bbh/web/app.py tests/test_web_jobs_api.py
git commit -m "feat: 长任务 API——检测/回扫/生成走 job 单飞，守卫同步 400，进度轮询"
```

---

## Task 10: 前端 Vite 脚手架（三入口 + 共享模块 + tab 条）

**Files:**
- Create: `frontend/package.json`、`frontend/vite.config.js`
- Create: `frontend/edit.html`、`frontend/wall.html`、`frontend/calib.html`
- Create: `frontend/src/shared/{theme.css,api.js,jobs.js,hotkeys.js,session.js,tabbar.js}`、`frontend/src/edit/main.js`、`frontend/src/wall/main.js`、`frontend/src/calib/main.js`
- Create: `frontend/.gitignore`
- Modify: `bbh/static/`（构建产物，**提交进仓库**——运行时不装 Node）
- Test: `frontend/../.claude/…` 无；验证靠 `npm run build` + Playwright 冒烟脚本

**Interfaces:**
- Consumes: Task 1 起的全部 `/api/*` 端点。
- Produces: 三个页面骨架 + 共享模块（`api.request` / `jobs.poll` / `hotkeys.bind` / `session.load` / `tabbar.mount`），Plan B/C 在这套骨架上写页面。

**为什么产物入库：** 用户机器上不必装 Node 就能跑（`run_app.bat` 只管起 Python）。`npm run build` 直接输出到 `../bbh/static`，构建一次提交一次。

**为什么不用 vue-router / Pinia：** 三个页面是三个独立入口（互跳靠 `location.href`），没有共享的客户端状态——一个会话态全在服务端 `/api/state`，前端拿到的就是权威值。引路由和状态库只会多两层心智负担。

- [ ] **Step 1: 写工程文件**

`frontend/package.json`：

```json
{
  "name": "bbh-frontend",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build"
  },
  "dependencies": {
    "vue": "^3.4.38",
    "element-plus": "^2.8.3",
    "@element-plus/icons-vue": "^2.3.1"
  },
  "devDependencies": {
    "vite": "^5.4.10",
    "@vitejs/plugin-vue": "^5.1.4"
  }
}
```

`frontend/vite.config.js`：

```javascript
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { resolve } from "node:path";

// 三入口多页构建，产物直接覆盖 bbh/static（Python 服务从那儿挂静态）。
// 构建产物入库，运行时装不装 Node 都无所谓。
export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: resolve(__dirname, "../bbh/static"),
    emptyOutDir: true,
    rollupOptions: {
      input: {
        edit: resolve(__dirname, "edit.html"),
        wall: resolve(__dirname, "wall.html"),
        calib: resolve(__dirname, "calib.html"),
      },
    },
  },
});
```

`frontend/.gitignore`：

```
node_modules/
```

`frontend/src/shared/api.js`：

```javascript
// 后端契约：成功 200 + JSON；失败 {error: "中文文案"}。
// 所有错误（400 用户可见 / 409 冲突 / 500 意外）都抛 Error(error 原文)，
// 调用方一律 `try { … } catch (e) { ElMessage.error(e.message) }`——
// 文案就是要给用户看的，不在这里改写。
async function request(method, url, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(url, opt);
  let data = null;
  try { data = await r.json(); } catch { /* 空 body 或非 JSON */ }
  if (!r.ok) throw new Error((data && data.error) || `${r.status} ${r.statusText}`);
  return data;
}

export const api = {
  get: (url) => request("GET", url),
  post: (url, body) => request("POST", url, body),
  patch: (url, body) => request("PATCH", url, body),
  del: (url, body) => request("DELETE", url, body),
};

export function qs(params) {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== "") u.set(k, v);
  }
  const s = u.toString();
  return s ? "?" + s : "";
}
```

`frontend/src/shared/jobs.js`：

```javascript
import { api } from "./api.js";

// 长任务轮询：POST 启动拿 jobId → 0.8s 一次 GET /api/jobs/{id}，done 为止。
// 用轮询不用 SSE：断线刷新后还能接着看，服务端实现也最简（单用户本机工具）。
export async function runJob(startUrl, body, onTick) {
  const { jobId } = await api.post(startUrl, body);
  for (;;) {
    const j = await api.get(`/api/jobs/${jobId}`);
    if (onTick) onTick(j);
    if (j.done) {
      if (j.state === "error") throw new Error(j.error || "任务失败");
      return await api.get(`/api/jobs/${jobId}/result`);
    }
    await new Promise((r) => setTimeout(r, 800));
  }
}
```

`frontend/src/shared/session.js`：

```javascript
import { api } from "./api.js";

// 会话态全在服务端：前端拿到的行数据/统计/证据都是权威值，不自己算。
// 任何写操作返回的也是同一份 state（就地替换即可）。
export const store = {
  state: null,
  listeners: new Set(),
  set(s) {
    this.state = s;
    this.listeners.forEach((f) => f(s));
  },
  on(f) { this.listeners.add(f); },
  async open(videoPath) { this.set(await api.post("/api/session", { videoPath })); },
  async refresh() { this.set(await api.get("/api/state")); },
};
```

`frontend/src/shared/hotkeys.js`：

```javascript
// 键盘流复核：输入框聚焦时一律不触发（否则打标签的 1-9 会被吞）。
// 弹窗打开时也不触发——用 disabled 开关由页面控制。
export function bindHotkeys(map, { disabled = () => false } = {}) {
  const onKey = (e) => {
    const el = document.activeElement;
    const typing = el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA"
      || el.isContentEditable);
    if (typing || disabled()) return;
    const key = (e.ctrlKey ? "ctrl+" : "") + e.key.toLowerCase();
    const fn = map[key];
    if (fn) { e.preventDefault(); fn(e); }
  };
  window.addEventListener("keydown", onKey);
  return () => window.removeEventListener("keydown", onKey);
}
```

`frontend/src/shared/theme.css`：

```css
/* 深色为默认（视频工具惯例，看片不刺眼）；主色篮球橙。 */
html { color-scheme: dark; }
body { margin: 0; background: #141414; color: #e5eaf3;
  font-family: system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; }
[v-cloak] { display: none; }
:root {
  --el-color-primary: #e8622d;
  --el-color-primary-light-3: #a84b26;
  --el-color-primary-light-5: #7e3b21;
  --el-color-primary-light-7: #542b1b;
  --el-color-primary-light-8: #3f2519;
  --el-color-primary-light-9: #2a1e16;
  --el-color-primary-dark-2: #f48553;
}
.tabbar { position: sticky; top: 0; z-index: 2000; display: flex;
  align-items: center; gap: 6px; padding: 6px 14px; height: 40px;
  background: #1b1b1b; border-bottom: 1px solid #333; box-sizing: border-box; }
.tabbar .brand { font-weight: 700; margin-right: 10px; }
.tabbar a { color: #cfd3dc; text-decoration: none; padding: 6px 12px;
  border-radius: 6px; font-size: 13.5px; }
.tabbar a:hover { background: #262626; }
.tabbar a.on { background: var(--el-color-primary); color: #fff; }
.tabbar .hint { margin-left: auto; color: #8d9098; font-size: 12.5px; }
```

`frontend/src/shared/tabbar.js`：

```javascript
// 三页共用条：当前页高亮 + 右侧提示。三页布局同源，用户三处看到的
// 导航一模一样（2026-09-16 用户裁决）。
const PAGES = [
  ["edit.html", "🎬 剪辑台"],
  ["wall.html", "🧍 出手人墙"],
  ["calib.html", "⚙ 标定设置"],
];

export function mountTabbar(current, hint = "") {
  const nav = document.createElement("nav");
  nav.className = "tabbar";
  nav.innerHTML = '<span class="brand">🏀 篮球集锦</span>'
    + PAGES.map(([href, label]) =>
        `<a href="/${href}" class="${href === current ? "on" : ""}">${label}</a>`).join("")
    + `<span class="hint">${hint}</span>`;
  document.body.prepend(nav);
}
```

`frontend/edit.html`（wall/calib 同款，改标题、入口 js 与 tab 参数）：

```html
<!DOCTYPE html>
<html lang="zh-CN" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>篮球集锦 · 剪辑台</title>
</head>
<body>
  <div id="app" v-cloak></div>
  <script type="module" src="/src/edit/main.js"></script>
</body>
</html>
```

> `class="dark"` 是必须的：Element Plus 的暗色变量挂在 `html.dark` 下，
> 漏了它整套组件会是浅色（原型期踩过）。

`frontend/src/edit/main.js`（wall/calib 把 `edit` 换成各自名字；Plan B/C 在这里长出血肉）：

```javascript
import { createApp } from "vue";
import ElementPlus from "element-plus";
import "element-plus/dist/index.css";
import "element-plus/theme-chalk/dark/css-vars.css";
import * as Icons from "@element-plus/icons-vue";
import "../shared/theme.css";
import { mountTabbar } from "../shared/tabbar.js";
import { store } from "../shared/session.js";

// 骨架：证明三页能开、tab 能跳、/api/state 能通。
// 页面主体（表格/播放器/判定依据/按钮）在 Plan B 实现。
const App = {
  data: () => ({ st: store.state || {} }),
  mounted() { store.on((s) => { this.st = s; }); },
  template: `<div style="padding:16px">
    <h2 style="margin:0 0 8px">剪辑台</h2>
    <p style="color:#8d9098;font-size:13.5px">{{ st.hint }}</p>
  </div>`,
};

mountTabbar("edit.html", "骨架页——页面内容在 Plan B 实现");
const app = createApp(App);
app.use(ElementPlus);
for (const [k, c] of Object.entries(Icons)) app.component(k, c);
app.mount("#app");
```

- [ ] **Step 2: 装依赖并构建**

```bash
cd frontend
npm install --registry=https://registry.npmmirror.com
npm run build
```

Expected: `../bbh/static/` 出现 `edit.html` / `wall.html` / `calib.html` + `assets/*.js|css`，且**占位页被覆盖**（`grep -c "vite" bbh/static/edit.html` 或看 HTML 里是否含 `type="module"`）。

- [ ] **Step 3: 提交产物**

```bash
cd ..
git add frontend/ bbh/static/
git commit -m "feat: 前端 Vite 脚手架——三入口多页构建 + 共享模块（api/jobs/hotkeys/theme/tabbar），产物入库可直接起服务"
```

- [ ] **Step 4: 起服务并冒烟**

**后台**起服务（它不会自己退出，前台跑会挂住）：

```bash
./.venv/Scripts/python.exe -m bbh.web
```

（用后台执行，别占住终端。）然后：

```bash
curl -s http://127.0.0.1:7860/api/state
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:7860/edit.html
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:7860/wall.html
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:7860/calib.html
```

Expected: `/api/state` 返回 `{"videoPath": "", "loaded": false, "hint": "请先填视频路径…"}`；三个页面 200。

> ⚠️ 端口 7860 上如果还挂着用户自己的旧 Gradio 实例，别杀它（用户在用）——
> 停掉自己的、或换端口起来验，验完记得停自己的后台任务（按 task id 停，
> **别用宽泛的 `taskkill //IM`**）。

- [ ] **Step 5: Playwright 验 tab 可跳转**

复用原型期那套 Playwright（`npm i playwright` 已在系统 Temp 里装过一次，这里新建脚本即可）：

```javascript
// frontend/smoke.mjs —— 冒烟：三页能开、tab 高亮正确、点击能跳
import { chromium } from "playwright";

const BASE = "http://127.0.0.1:7860";
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
const errs = [];
page.on("pageerror", (e) => errs.push(String(e)));

for (const [file, label] of [["edit.html", "剪辑台"], ["wall.html", "出手人墙"],
                             ["calib.html", "标定设置"]]) {
  await page.goto(`${BASE}/${file}`, { waitUntil: "networkidle" });
  const on = await page.locator(".tabbar a.on").textContent();
  if (!on.includes(label)) throw new Error(`${file}: tab 高亮错，实际「${on}」`);
  const n = await page.locator(".tabbar a").count();
  if (n !== 3) throw new Error(`${file}: tab 数应为 3，实际 ${n}`);
  const top = await page.locator(".tabbar").evaluate((el) => el.getBoundingClientRect().top);
  if (top !== 0) throw new Error(`${file}: tab 条没贴在顶部（top=${top}）`);
}
await page.goto(`${BASE}/edit.html`);
await page.locator('.tabbar a:has-text("出手人墙")').click();
await page.waitForURL("**/wall.html");
if (errs.length) throw new Error("页面报错：" + errs.join(" | "));
console.log("✅ 冒烟通过：三页可开、tab 高亮正确、可跳转、无 JS 报错");
await browser.close();
```

Run: `cd frontend && node smoke.mjs`
Expected: `✅ 冒烟通过：三页可开、tab 高亮正确、可跳转、无 JS 报错`

> Playwright 不在 `frontend/node_modules` 里（前端工程只装构建依赖，不引
> 300MB 浏览器）。用原型期那份 npx 缓存：`NODE_PATH="$(npm root -g 2>/dev/null)
> :/c/Users/<用户>/AppData/Local/npm-cache/_npx/<hash>/node_modules" node smoke.mjs`
> ——`node smoke.mjs` 报 `Cannot find package 'playwright'` 时按这个补 NODE_PATH；
> 也可以 `npm i -D playwright && npx playwright install chromium` 装进工程。
> `smoke.mjs` 里用 `headless`（默认），别开有头浏览器。

- [ ] **Step 6: 收尾全量测试 + 提交**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q`
Expected: 原有全部测试 + 本次新增 78 项全绿（**这是 Plan A 唯一一次跑全量**——前面每个任务只跑自己的文件；跨页收尾才需要证明没打断存量）。

```bash
git add frontend/smoke.mjs
git commit -m "test: 前端骨架 Playwright 冒烟——三页可开/tab 高亮/可跳转/无 JS 报错"
```

---

## spec 覆盖对照与两处修正

**端点 → 任务对照**（spec §API 契约的每一行都有落点）：

| spec 端点 | 落在 |
|---|---|
| `POST /api/session` · `GET /api/state` · `GET /api/events` | Task 4 / Task 5（别名） |
| `PATCH /api/events/{key}` · `/miss` · `DELETE` · `/restore` · `POST /api/tags` | Task 5 |
| `POST /api/feedback/{key}` · `/api/flags/{key}` | Task 6 |
| `GET /api/wall` · `/assign` · `/unassign` · `/guard` | Task 7 |
| `GET /api/calib/frame` · `/point` · `/build` · `/refine` · `/segments` · `/existing` | Task 8 |
| `POST /api/gen` · `/api/detect` · `/api/wall/scan` · `GET /api/jobs/{id}` · `/result` | Task 9 |
| job 机制 · 单飞 409 · 轮询 | Task 2（实现）+ Task 9（接线） |
| 静态页 · `/` 重定向 · 入口切换 · 兼容红线 | Task 1 |
| 前端工程结构（Vite 多页 + 共享模块） | Task 10 |

**修正 1 —— `PATCH /api/events/{key}` 不处理 `pre` / `post`。** spec 表里写了
`{pre}` / `{post}`，但落盘红线那节又定「前后秒不落盘，重开页面回默认 5/2」
（与旧界面一致：那只是会话内的切片参数）。两者冲突，取后者：`EventPatch`
只有 `selection` / `tag`。前端把 before/after 留在自己的会话里，切片时随
`GET /media/clip?before=&after=` 带上。Pydantic 默认忽略多余字段，所以
前端多传 `pre` 也不会 422。

**修正 2 —— `POST /api/gen` 的字段叫 `items` 不叫 `keys`。** spec 写
`{vertical, keys[]}`，但 `gen_selected` 的选中单位是**汇总项**（
`"视频合集（全部勾选的进球）"` 或标签名），不是球键——球键是表格勾选列决定的，
`gen_selected` 自己从 rows 里读。写成 `keys[]` 会让人以为要传球键。故改名为
`items[]`，与 `GET /api/gen/choices` 返回的 `choices` 同名。

**另有两处对 spec 的补白**（spec 没写、实现必须定）：`/api/calib/{frame,preview}.png`
两个媒体端点（旧界面靠 `gr.Image` 传数组，HTTP 下得有个出图的地方；选内存
PNG 不落盘，守住 work/ 红线）；以及 422 → 400 的全局映射（FastAPI 默认的
`{"detail": [...]}` 违反「失败恒为 `{"error": 中文}`」契约）。


## 实施修正记录（执行期裁定）

以下 18 条是执行本计划时发现「计划/brief 原文与冻结代码或自身矛盾」、由执行控制者裁定后
修正的口径。**实现已按修正后的口径落地并各有测试钉住**；计划正文未改，以本节为准。

**1. Task 1 — 错误层补 `StarletteHTTPException` handler。**
计划只列了 `HTTPException` / `ValidationError` 的翻译。FastAPI **默认**就给
`StarletteHTTPException` 注册了 handler（所以路由未命中的 404 **确实经过**它），但默认 handler
翻出来的是 `{"detail": …}` 的**英文**体，不满足「失败恒为 `{"error": 中文文案"}`」契约。
已加自定义 handler 覆盖默认：404 统一为「页面或接口不存在」。
（早期草稿把这句写成「**不经过** FastAPI 的异常 handler」——**机制说错了**，结论不变但
理由要留准确，否则后来人可能误判「删掉自己的 handler 也没关系」。）

**2. Task 3 — 媒体路由的错误文案不回显绝对路径。**
brief Step 3 逐字把 realpath 后的绝对路径写进消息。改为 `os.path.basename(...)`：
用户仍知道是哪个文件不存在（诊断价值保留），但不回显服务端目录。

**3. Task 4 — 行结构的证据字段**可空**。**
计划的行结构契约块把 `shooter` / `ev` / `terms` / `net` 当必有对象举例。实测真实产物
（`truth/2026-08-29_3`）**434/434 行这四项全为 null**。前端必须按可空消费——
照抄示例写 `row.net.post` 会在真实视频上炸。

**4. Task 5 / 6 / 7 — 三处「brief 的测试期望与 brief 自己的参考实现互斥」。**
均裁定**保留实现者改法**（改测试字面量，不改转发层）：

| 处 | brief 期望 | 实情 | 依据 |
|---|---|---|---|
| T5 | 键归一化 | 窄门（秒 < 60 且有冒号）才算可归一 | 宽门会误伤规范进位键 |
| T6 | `msg` 里 `0:12.1` | `00:12.1`（`models.time_key` 零填充） | 同一条测试上文又要求盘上是 `["00:12.1"]`，两者不可能同真 |
| T7 | `✅ 2 球 → 老杨` | `✅ 1 球 → 老杨`（本次点选数，非「该人整组」） | 第二次是**单球增量** |

共同理由：要凑 brief 的字面量，api 层就得自己重排清单键 / 重算计数，直接违反
「只转发、不自己拼」这条本计划的核心约束。

> ⚠️ 与 T7 同批要记住的**护栏语义**：护栏是**警示不阻断**——200 + `msg` 里带护栏行，
> api 层**不得**解析 `msg` 自己抛 400（那既改了旧界面语义，也越过了「护栏由
> `assign_person` 内部实现」的边界）。

**5. Task 8 — 两处。**
① 段列表文案的字面量是 **`段 2`（带空格）**：`_seg_list_md` 输出带空格、`_seg_choices` 不带，
而 `segListMd` 直通前者，brief 的 `"段2" in segListMd` 必红。
② **「微调」用例必须先「点 3 点 + 生成标定」**：冻结的 `refine_hsv` 首查标定档，
无档直接 400「请先点击「生成标定」，再微调」。

**6. Task 9 — `env` fixture 改用快照法。**
brief 只复位 `_state["running"]`，残留的 `error` / `cancelled` 会污染同进程后续用例
（实测 `pytest tests/test_web_jobs_api.py tests/test_app.py::test_刷新状态取消中文案` 打红）。
改为进 `saved = dict(A._state)`、出 `clear() + update(saved)`（照 `tests/test_app.py` 的样板）。

**7. Task 9 — `Job.found` 恒为 `None`。**
`bbh/app.py` 的 `_state` 里没有任何球数字段，三类任务的 worker 都不 `report(found=…)`。
brief 那句断言注释「回扫不上报球数（只有检测报）」是错的，已改为实情。
**前端要球数读 `/api/state` 的 `rows`，别读 `Job.found`。**

**8. Task 10 — 起服务的命令必须换掉（红线：会让根档被搬走，且无 git 副本）。**
计划 Step 4 写 `./.venv/Scripts/python.exe -m bbh.web`。该入口（`bbh/web/__main__.py`）
在 `os.chdir(ROOT)` 之后调 `migrate_root_calibrations()`——它把仓库根的标定档认领进
`work/<视频>/` 后 **`os.remove` 掉根文件**，而仓库根躺着 4 个真实标定档
（`calibration.json` / `calibration_ref.png` / `calibration_seg2.json` / `calibration_ref_seg2.png`）。
**关键事实（实测）**：这 4 个档 + `tags.json` **一个都没有被 git 跟踪**
（`git ls-files` 对这 5 个路径输出为空；`.gitignore:6/7/8` 明确忽略 `calibration.json` /
`calibration_ref.png` / `tags.json`，另两个 seg2 档未被忽略但也从未提交）——
所以根位置那一份**没有 git 副本可回滚**。但要说准**后果**：认领是**先 `shutil.copy2` 进
`work/<视频>/`（`bbh/app.py:517-520`）、再 `os.remove` 根档（`:521-522`）**——即**搬移而非销毁**
（文件能在认领视频目录里找回，界面也会返回提示），且**只有**某视频 `events.json` 的 `calib_key`
等于**根这套标定的指纹**时才会发生（2026-09-19 只读实测：`work/` 下 13 个有 `events.json` 的视频
**0 命中**，根套是两段指纹 `t=0` + `t=149.0`；今天跑不会认领）。
**改为**：临时脚本（放 `%TEMP%`，不进仓库）`uvicorn.run(create_app(), host="127.0.0.1", port=7861)`，
**不调** `migrate_root_calibrations`；7860 留给用户自己的旧实例。见 `task-10-dispatch-notes.md` 的 R1/R2。

> **定性说明（别把这条读成「Plan A 新添了危险」）**：`bbh/app.py:2721` 的
> `if __name__ == "__main__"` 段**本来就调** `migrate_root_calibrations()`，所以 Task 1 把
> `run_app.bat`（与 `python -m` 入口）从 `bbh.app` 切到 `bbh.web` **没有引入新的数据风险**——
> 那条迁移一直在用户的默认启动路径上，`bbh/web/__main__.py:21-23` 只是「与旧入口同款」。
> 本条的适用范围是**执行期的冒烟/验证**：不要让 subagent 通过这个入口起服务，仅此而已。
>
> **附一条与代码无关的、可选的动作项**：这 5 个档至今**没有任何 git 副本**，而 `run_app.bat`
> **每次启动**都会尝试按指纹认领它们。**认领 = 搬移，不是销毁**（先复制进 `work/<视频>/`、
> 再删根档），且**只有**某视频的 `calib_key` 等于根套指纹时才发生（2026-09-19 实测 **0 命中**）。
> ⇒ 把它们**复制一份到仓库外**是**零成本的可选对冲**（保住「根位置」那一份），**不是必须**——
> 除非你把根位置当权威副本。

**9. Task 10 — 冒烟脚本的端口默认值。**
计划 Step 5 的 `smoke.mjs` 把 BASE 写成 7860（与项目端口一致），执行时为避开用户挂在 7860 的
旧实例改成了 7861。**仓库里那份已改回默认 7860**、执行期用环境变量覆盖
（`const BASE = process.env.BBH_BASE || "http://127.0.0.1:7860";`）——7860 是项目**唯一**端口
（`bbh/web/__main__.py` 硬编码 + `run_app.bat` 走它），硬编码 7861 会让按文档起服务的人连不上，
且报错指向「页面坏了」这个错误结论。同时脚本头部写清运行配方（见第 12 条）。

**10. Task 10 — brief §Interfaces 的五个名字与 brief 自己的代码块不一致（Plan B/C 必踩）。**
brief §Interfaces 写的是 `jobs.poll` / `session.load` / `api.request` / `tabbar.mount`，
而 brief 自己给出的参考实现里是 `runJob` / `store` / 私有的 `request` / `mountTabbar`——
**五个名字全不一致**。实现按代码块落地（正确），但 Plan B/C 若照 §Interfaces 写会立刻踩空。
**后续计划以代码块/实际落地为准。**

**11. Task 10 — spec 三处草图已过期，以实现为准。**
① spec §目录布局写的 `base: "/static/"`：若真落地，在 T1 的 `app.mount("/")` 下会变成
`/static/assets/…` → **三页全 404**；brief 省略 `base` 才是自洽解。
② spec §前端要求 `vite dev` + `server.proxy` 代理 `/api`、`/media` 到 7860：brief 未含，
`npm run dev` 现状不可用——**不影响用户路径**（交付走 `vite build` 产物 + FastAPI 静态挂载）。
**但 Plan B 要频繁改前端**，没有 proxy 的 dev 模式会很难受 ⇒ 把「是否补 `server.proxy`」
列为 **Plan B 的必答项**，而不是当 spec 过期就翻页。
③ spec 要求 e2e 入库 `tests/e2e/`：实际落在 `frontend/`（它靠 npx 缓存 + CJS 副本跑，
放进 `tests/` 反而会让人以为 pytest 能跑）。
④ spec:133 说 `POST /api/gen` 收 `keys[]` ⇒ 实现是 `GenIn(vertical, items: list[str])`（**实现正确**：
冻结的 `gen_selected` 收的就是项名清单，`open_gen_dialog` 的 choices 也是项名；计划正文末尾已另记一条同名修正）。
**同一行还有半句不是过期、而是未落地**：「全部跳过时 error 而非 success」——实现恒
`state:"done"` + `videos: []`。**裁定：这半句作废，以实现为准。** 理由：报「错误」会丢掉
`summaryMd` 里逐条的**跳过原因**，而那正是用户唯一可行动的信息（`api_gen.py:100-111, 158`）。
**Plan B 契约**：`state=="done" && videos.length==0` ⇒ 呈现「未生成任何成品（原因见下）」，
**不要弹绿色成功**。

**12. Task 10 — `smoke.mjs` 的运行前提要写进文件头。**
`playwright` 不在 `frontend/package.json` 里，而 **ESM 解析器忽略 `NODE_PATH`** ⇒ 仓库里这份
`.mjs` 靠 npx 缓存跑不起来。**不把 playwright 写进 devDependencies**（会让 lockfile 与
package.json 不一致、`npm ci` 报错，且补跑 `npm install` 会触发浏览器下载），改为在文件头
写清三步配方：起服务（**不要**用 `-m bbh.web`，见第 8 条）→ 一次性 `npm i -D playwright` →
`BBH_BASE=… node smoke.mjs`。

**13. 通用 — 「没写盘」的证据不能用 `git status`，也不能用「今天」。**
执行期的红线复核一度写成「`git status --porcelain -- work/` 为空」——`work/` 在 `.gitignore`
里，git **永远不会**报它，那是**空证据**。改用 `find` 后还要再收一格：`-newermt <今天>`
**在跑过全量 pytest 之后必然不空**（`tests/test_app.py:421` 会写
`work/sample/preview.mp4`，实测命中）。凡断言「没写盘」一律用**本轮操作时间窗**
或**跑全量前后差集**，或前后逐字节快照比对。

**14. Task 10 — Vue runtime-only 构建 + `template:` 字符串 = 静默白页（Plan B/C 头号坑）。**
`vue` 默认解析到 `vue.runtime.esm-bundler.js`（**无模板编译器**），而 `frontend/src/*/main.js`
用的是 `template:` **字符串**（需运行时编译）→ 生产构建剥掉 warn → **三页正文全白、无任何报错**。
实测：三页 `#app` 的 innerHTML 都是 `<!---->`（空 vnode）、`#app h2` 计数 0，而 `v-cloak`
已被移除（Vue **挂载成功了**，空的是渲染结果）。
**修法**：`frontend/vite.config.js` 加 `resolve: { alias: { vue: "vue/dist/vue.esm-bundler.js" } }`
（产物 +98 kB 即编译器）。**这行不能删**——删掉零测试变红（冒烟脚本不在 pytest 里）。
已再加一道**构建期自检**：`vue` 若被解析到 runtime-only 构建，`npm run build` **直接非零退出**
并点名「三页正文会静默白屏」（判据是**行为**：真调一次 `compile("<div/>")` 看能否编出渲染函数——
「有没有 `compile` 导出」是**假绿**，两个构建的 export 名集合差集为空）。
⇒ 现在删掉 alias 的后果是**响亮的构建失败**，而不是静默白页。
**更重要的教训**：当时的冒烟脚本断言的三样东西（tab 文案/条数/点击跳转）**全部由
`mountTabbar` 的 `body.prepend` 提供，与 Vue 无关**，所以整条 Vue 链死掉也照样全绿、
T10 的验收结论是**假绿**。⇒ **Plan B/C 的页面验收必须在浏览器里断言「正文渲染出来了」
（`#app` 内出现预期文案），不能只断言 HTTP 200、`<title>` 或导航条。**

**15. Task 10 — 构建失败会把 `bbh/static` 清空（不是 fail-safe）。**
`emptyOutDir: true` 的清目录动作（Vite 的 `prepareOutDir()` / `emptyDir`）在 `await rollup()`
**成功返回之后**才发生，与插件的 `enforce` 无关（对照实验：`buildStart` 抛错 → 产物存活；
`generateBundle` 抛错 → **outDir 已被清空**）。所以**任何写阶段失败**
（`generateBundle` / `renderChunk` / `augmentChunkHash`，或 Windows 上写文件遇 `EBUSY`/`EPERM`）
都会留下一个**空的 `bbh/static`**：服务对三个 html 与 `/assets/*` 全 404，而构建退出码非零一闪而过。
⇒ **改完前端必须看 `npm run build` 的退出码，失败后必须重新构建成功再起服务。**
（两种形态：**写阶段**失败留下**空目录**；**`buildStart` 阶段**失败时 outDir **从未被创建**
——失败臂实测 `out/` 不存在。后者反而安全，前者才是要命的那个。）

**16. 通用 — `git checkout --` 不是逐字回滚（本仓 `core.autocrlf=true`）。**
实测：`git checkout -- frontend/vite.config.js` 把 LF(3922B) 变成 CRLF(3981B)、sha256 变化，
**而 `git status` 仍报「干净」**；反方向也成立（字节已还原后 `git status` 报 ` M` 而 `git diff` 为空）。
⇒ 凡「已逐字回滚」的结论**必须用 sha256 + size 判**，不能用 `git checkout --` 或 `git status` 代替；
变异实验前先留一份**字节副本**（或用 `git update-index --refresh` 只刷 stat 缓存）。

**17. Task 10 — 检测运行期间「编辑表格」的互斥被移除了（Plan B 必答项，Plan A 无页面故不阻塞代码验收）。**
旧界面在检测期间**刻意禁用整张表**（`bbh/app.py:631-632`：`_state["running"]` ⇒
`gr.update(interactive=False)` + `table_locked` 兜死）；新的 `bbh/web/api_events.py` 四个写端点
**没有任何 `running` 守卫**。
**失败场景**：用户点「▶ 开始检测」（几分钟）→ 切到剪辑台（旧结果集仍在）→ 勾球 / 打标签 / 归人 /
打裁决 ⇒ 这些写按**当时的** `events.json` 键落盘；检测完成时 worker 写入新档，**键相同的那几颗球
拿到别人的勾选/标签**（静默错数据），键变了的成为孤儿键（静默丢失）。
这条同时**放宽了两个隐患**：① T4 的「双读档竞态」（`api_session.build_rows` 里
`load_events_table` 与 `_load_events_or_none` 是两次独立读档，`keys[i]`/`evs[i]` 按位置对齐 ⇒
两次读之间被 worker 写入时，第 i 行的证据会挂到**另一颗球**上）；② 冻结代码 `_raw_time_keys`
在 events 与行数不匹配时**静默退回显示时刻键**（带校准偏移语义）⇒ 一旦写进
`selection.json`/`tag_assignments.json`，该视频**整个按键体系被污染**。
**Plan B 契约**：写端点要么加 `if A._state.get("running"): raise HTTPException(409, "检测进行中——请等它跑完")`，
要么由前端禁表（与旧界面同口径）。**注意别照抄全局版**：检测是**按视频**跑的，对**别的**视频的写
不该被冻住——守卫要按 `video_path` 判，不是全局 `_state["running"]`。

**18. Task 10 — 补一条「入库产物完整性」测试兜底。**
理由见第 15 条：`bbh/static` 是**入库产物**，而 `pytest` 原本完全碰不到它——忘了 build、
或**写阶段失败把目录清空**，都会让三页与 `/assets/*` 全 404 而 pytest 全绿。
已新增 `tests/test_web_static_assets.py`（纯 stdlib、不依赖 Node）：解析三个 html 里的
`assets/...` 引用，断言每个文件存在且 `size > 0`，并断言每个 html 含 `id="app"` 与
`<script type="module"`。
**「浏览器里正文真渲染」仍需浏览器**，留在 `frontend/smoke.mjs`（它不在 pytest 里，
**要人手动跑**，这是本条兜不住的残余）。

> 另记一条**未触发的预判**：曾预判 `"type": "module"` 会让 `vite.config.js` 的 `__dirname` 报错，
> 实测 Vite 5.4.21 正常注入 shim，**无需改**。

---

## 附：终审发现并修复的缺陷（对 Plan B/C 的接口约束）

全分支终审（最强模型 + 全分支包）判「有条件通过」，以下四条已修复；**它们给 Plan B/C 留下了
必须遵守的接口约束**：

1. **`/media/clip` 回包串台**（本分支新引入）：冻结的 `_cut_preview` 用 `-y` 写**固定路径**
   `work/<视频>/preview.mp4`，端点原本无锁 ⇒ 连点两行的「▶ 预览」会互相覆盖、**回包内容与所请求的
   时刻不符**（实测 8 条并行请求 7 条串台）。已改为**锁内切片 + 锁内读内容**。
   ⇒ **约束：凡调用写固定路径的冻结函数的端点，都必须在 `state.video_lock(vp)` 内完成
   「写 + 取内容」；只在切片外面套锁不够——`FileResponse` 是出锁之后才迭代文件。**
   该约束在 `tests/test_web_media.py::test_concurrent_clip_requests_do_not_cross_streams` 里用
   **「晚读替身」**（`_late_reading_file_response`：把「读文件」推迟到两条写都落盘之后）钉住，
   所以「只锁切片 + 回包换回 `FileResponse`」这种半修也是**确定性红**；替身**必须继承 Starlette 的
   `Response`**（写成普通类会被 FastAPI 当 JSON 序列化 → 假红），且它只覆盖
   `bbh/web/media.py` 里 `FileResponse` 这个名字——**换成别的流式响应类型（如
   `StreamingResponse`）该替身即失效**（那时只会退回「无覆盖」，不会假红）。
2. **`/api/calib/frame.png` 跨请求串帧**：先给 `s.frame_png` 赋值、又**读回同一共享属性**回包
   ⇒ 并发抽帧会张冠李戴。已改为局部变量。⇒ **约束：共享会话属性只用于「存」，回包一律用局部值。**
3. **`_MSG_IDX` / `_gen_result` ↔ `bbh/app.py:1326` 的静默耦合**：原先只写在一行内联注释里，
   而测试**全用假生成器** ⇒ 改 summary 格式会静默出 0 条成品而测试仍绿。已补一条**真
   `gen_selected`** 的契约用例（只替身 `generate_highlight`）并把耦合点名写进注释。
   ⇒ **约束：谁改 `bbh/app.py:1326` 的 summary 格式，谁就得同步 `_MSG_IDX` 与解析器。**
4. **`POST /api/tags` 是唯一不回 `state_payload()` 的写端点** —— 已统一。
   ⇒ **约束：写端点一律回「权威行 + 页脚汇总」。**
