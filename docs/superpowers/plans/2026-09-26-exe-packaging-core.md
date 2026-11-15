# 打包内核 · 实施计划（A：源码改动 A1–A10）

**Spec**：`D:\claude code\视频剪辑exe\docs\2026-09-26-打包内核-设计方案.md`（v3，用户已逐条拍板 20 项，见其 §10）
**范围**：只覆盖设计文档 **§3-A（原仓库的代码改动 A1–A10）**。§3-B（构建工程：spec / 打包.bat / 入口.py / 使用说明）**另立计划**。
**仓库**：本仓库（分支 `main`，无远端）。设计文档 D5 已写明「`main` 上会多出几个 commit，这是必然的」⇒ **在 main 上提交已被授权**。

## Global Constraints

1. **非冻结运行行为不得改变**（设计文档判据 7）。一切冻结专用分支必须由 `getattr(sys, "frozen", False)` 守卫 ⇒ 现有测试套件是这一条的证人。
2. **不新增第三方依赖**（D7 的互斥体用 `ctypes`）。
3. **不复制源码**（D5）：改动只落在本仓库；构建工程在 `D:\claude code\视频剪辑exe`。
4. **每个任务独立 commit**，便于单独 revert（设计文档 §6）。一个任务的提交范围就是它的回滚点。
5. **`{"error": 中文文案}` 契约逐字不变**（判据 6）。
6. **测试必须 `monkeypatch.chdir(tmp_path)`**：`tests/conftest.py` 装了 `work/` 写守卫，任何走
   `_work_dir_for` 或裸文件名（`tags.json` / `ui_prefs.json` …）写入的测试若不 chdir，会被守卫拦下并让
   会话判据变成 ⚠（**非预期拦截必须为 0**；见 `tests/write_guard.py` 与 `tests/conftest.py` 顶部契约）。
7. **测试命令**：`.venv/Scripts/python.exe -m pytest`（仓库根为 cwd，无 pytest 配置文件）。
8. 前端三页是**预构建产物**（`bbh/static`）：改动 `frontend/src` 后必须 `npm --prefix frontend run build`
   重新产出并入库（`frontend/vite.config.js` 的 `outDir` 指向 `../bbh/static`）。Node v24.14.1 / npm 11.11.0 已装。

## 与设计文档的差异（预检裁定）

**A1 原文的「chdir 目标改为 `<ROOT>/data/`」是无条件的，本计划改为「仅冻结时」。**

- 理由：`run_app.bat` 走的**正是同一个入口**（`.venv\Scripts\python -m bbh.web`）。无条件改会把开发者的工作区
  从 `<仓库>/work`（实测 8.5GB / 7394 档）与仓库根 6 个红线档，**静默搬到 `<仓库>/data/`** —— 用户下次打开
  会发现「我的成果全没了」，而它们只是换了地方。这与设计文档 D4「开发照旧用 `run_app.bat`」和判据 7 直接冲突。
- 处置：`prepare_cwd()` 里冻结 ⇒ `chdir(<exe目录>/data/)`；非冻结 ⇒ `chdir(ROOT)`（仓库根，与今天逐字一致）。
  「两个独立工作区」的诉求照样满足：打包版在 `<exe>/data/`，源码版在 `<仓库>/`。
- 代价若判断错：源码运行时的数据根与设计文档字面不符（但行为与今天一致，可随时改回）。

## 任务顺序与理由

`A1 → A2+D7 → A3 → A6 → A7 → A8 → A9 → A4 → A5 → A10`

- **A4（拔 gradio，风险最高）排在中间靠后**：先让低风险的独立改动落地，A4 的全量验证就覆盖了已集成的底座。
- **依赖**：A8 的 banner 要打印「实际端口」（A2）与「实际 ffmpeg 路径」（A7）⇒ A2、A7 必须在 A8 前。
- **A5 依赖 A4**：`requirements.txt` 去掉 gradio 的前提是源码里已无 `import gradio`。
- **A10 独立**，但需要 `frontend/src` 的勘查（见该任务）。

---

## Task 1: A1 数据根 —— 冻结时取 exe 目录，数据落 `<exe>/data/`

**Files**: Mod `bbh/web/__main__.py`（30 行）；New `tests/test_web_entry.py`

**改动**

1. 在 `bbh/web/__main__.py` 抽出可测的纯函数（模块级**只允许**有 `ROOT = resolve_root()`，⛔ 不许有 `chdir` / `makedirs` —— import 不得改 cwd，测试会 import 它）：
   - `_is_frozen() -> bool`：`bool(getattr(sys, "frozen", False))`
   - `resolve_root() -> str`：冻结 ⇒ `os.path.dirname(os.path.abspath(sys.executable))`；否则维持现有三级 `dirname`（`__file__` 推导）
   - `data_dir() -> str`：`os.path.join(resolve_root(), "data")`
   - `prepare_cwd() -> str`：冻结 ⇒ `os.makedirs(data_dir(), exist_ok=True)` + `os.chdir(data_dir())` + 返回它；非冻结 ⇒ `os.chdir(resolve_root())` + 返回它。返回 cwd 是为了让 `main()` 一次算清。
2. `main()` 改为：`cwd = prepare_cwd()`；`if not _is_frozen(): sys.path.insert(0, ROOT)`；其余（`migrate_root_calibrations()` 调用、`uvicorn.run`）保持不变。
   ⇒ 冻结时**不**做 `sys.path.insert`（设计文档 A1 原文要求）。

**测试（先写、先跑红）**

- `test_非冻结时根是本仓库根`：`resolve_root()` 落在仓库根（含 `bbh/` 子目录）。
- `test_冻结时根是exe所在目录`：monkeypatch `sys.frozen=True` + `sys.executable=<tmp>/老王篮球集锦.exe` ⇒ `resolve_root() == <tmp>`。
- `test_冻结时prepare_cwd建data目录并落进去`：`prepare_cwd() == <tmp>/data`，目录存在，且 `os.getcwd()` 是它。
- `test_非冻结时prepare_cwd不建data目录`：chdir 到 tmp（模拟仓库根）后调 `prepare_cwd()` ⇒ 返回 `<tmp>`，且 `<tmp>/data` **不存在**。
- `test_import模块不改cwd`：记 cwd → `importlib.reload(bbh.web.__main__)` → cwd 不变。

**命令**: `.venv/Scripts/python.exe -m pytest tests/test_web_entry.py -q`
**Expected**: `5 passed`

**Commit**: `feat(pack): A1 数据根——冻结时取 exe 目录，数据落 <exe>/data/`

---

## Task 2: A2+D7 单实例互斥 + 端口顺延 + `data/.runtime.json`

**Files**: New `bbh/web/single_instance.py`、New `tests/test_single_instance.py`；Mod `bbh/web/__main__.py`

**改动**

`bbh/web/single_instance.py`（纯 ctypes + socket，零新依赖）：

- `mutex_name(data_dir) -> str`：`"Local\\bbh-" + hashlib.sha1(os.path.realpath(data_dir).encode()).hexdigest()[:16]`
  （**名字由数据目录派生**，⛔ 不是全局常量 —— 否则同一机器上两份不同位置的拷贝会互相误锁，与判据 4 冲突）
- `try_acquire(data_dir) -> object | None`：Windows 用 `ctypes.windll.kernel32.CreateMutexW(None, True, name)`；
  `GetLastError() == 183`（`ERROR_ALREADY_EXISTS`）⇒ 返回 `None`；成功 ⇒ 返回句柄（调用方持有到进程结束）。
  非 Windows ⇒ 返回一个哨兵对象（本计划不追求非 Windows 的单实例，但**不许炸**）。
- `find_free_port(preferred: int = 7860, tries: int = 11) -> int`：从 `preferred` 起 `socket.bind(("127.0.0.1", p))` 试。
- `write_runtime(data_dir, port)` / `read_runtime(data_dir) -> int | None`：写/读 `data/.runtime.json` 的 `{"pid":…, "port":…}`；
  读侧**容忍一切异常**（缺失 / 坏 JSON / 类型不对 ⇒ `None`）。
- `probe(port, timeout: float = 0.3) -> bool`：`127.0.0.1` 上 TCP 连一下（`.runtime.json` 是**提示不是真相**：进程被强杀会残留过期端口）。

`bbh/web/__main__.py` 的 `main()` 接线顺序（冻结与否都走）：抢锁 → 抢不到 ⇒ `read_runtime` + `probe` ⇒ `webbrowser.open` 那个端口 ⇒ `return 0`；抢到 ⇒ `find_free_port` → `write_runtime` → 起服务。

**测试（先写、先跑红）**

- `test_端口顺延跳过被占的7860`：先 `bind` 占住 7860 ⇒ `find_free_port() == 7861`；释放后可再拿 7860。
- `test_runtime回环与容错`：写 8123 → 读回 8123；写坏 JSON 文本 → `read_runtime()` 返回 `None`（不抛）；文件不存在 → `None`。
- `test_互斥体名随目录变而同目录稳定`：`mutex_name(a) == mutex_name(a)`、`mutex_name(a) != mutex_name(b)`（b 是另一 tmp 目录）。
- `test_子进程持锁时本进程抢不到`：`subprocess` 起一个短命 python 持锁（脚本 sleep 若干秒），主进程 `try_acquire` ⇒ `None`；子进程退出后 ⇒ 非 `None`（**验真实现，不用假接口**）。
- `test_probe对没人监听的端口返回False`：取一个刚被 `find_free_port` 探过的端口并保证无人监听 ⇒ `probe` False。

**命令**: `.venv/Scripts/python.exe -m pytest tests/test_single_instance.py -q`
**Expected**: `5 passed`

**Commit**: `feat(pack): A2+D7 单实例互斥 + 端口顺延 + data/.runtime.json`

---

## Task 3: A3 自动开浏览器（后台线程，不能在阻塞调用之后）

**Files**: Mod `bbh/web/__main__.py`；Mod（追加）`tests/test_web_entry.py`

**改动**：新增 `open_browser_later(url: str, delay: float = 1.0) -> threading.Thread` —— 起一个 `daemon=True` 线程，
内部 `time.sleep(delay)` 后 `webbrowser.open(url)`；返回线程对象。`main()` 在**调 `uvicorn.run()` 之前**调用它
（`uvicorn.run` 是阻塞调用，写在它之后永远不会执行）。URL = `http://127.0.0.1:{port}/edit.html`。

**测试**：`delay=0.0` + monkeypatch `webbrowser.open` ⇒ 线程 `join` 后 `open` 恰好被调用一次、参数是这个 URL；
断言线程是 daemon；断言 `open_browser_later` 返回时**不阻塞**（`time.monotonic()` 差 < 0.3s）。

**命令**: `.venv/Scripts/python.exe -m pytest tests/test_web_entry.py -q`
**Expected**: 全绿（`5 + 3 = 8 passed`）

**Commit**: `feat(pack): A3 自动开浏览器（后台线程延迟，避开阻塞的 uvicorn.run）`

---

## Task 4: A6 绑定地址铁律（现状已对，本项给它牙）

**Files**: Mod `bbh/web/__main__.py`；Mod（追加）`tests/test_web_entry.py`

**改动**：加模块常量 `HOST = "127.0.0.1"` 与注释说明它是**铁律**（程序无任何鉴权，对外等同把用户视频与成果
交给同一 Wi-Fi 下的任何人；「让手机也能看」的需求必须是一次明确决定，而不是顺手改一个字）。`uvicorn.run(..., host=HOST, ...)`。

**测试**

- `test_uvicorn绑定的必须是只对本机`：monkeypatch `uvicorn.run` 捕获 kwargs ⇒ `kwargs["host"] == "127.0.0.1"`。
- `test_全仓源码不许出现0.0.0.0`：`grep` 式遍历 `bbh/**/*.py`，断言无 `"0.0.0.0"` 字面量（防将来被改坏）。

**命令**: `.venv/Scripts/python.exe -m pytest tests/test_web_entry.py -q`
**Expected**: `10 passed`

**Commit**: `feat(pack): A6 绑定地址铁律 127.0.0.1（含防改坏测试）`

---

## Task 5: A7 冻结时强制用包内 ffmpeg

**Files**: Mod `bbh/editor.py`（`_ffmpeg_exe()`，现 12–18 行）；New `tests/test_ffmpeg_exe.py`

**改动**：`_ffmpeg_exe()` 加冻结分支 —— 冻结时**跳过** `shutil.which("ffmpeg")`，直接
`imageio_ffmpeg.get_ffmpeg_exe()`（包里那份）。理由：打包的意义是「同一份东西在哪台机器上都一样」，
而「装了旧 ffmpeg 的机器」会静默换用外部二进制、错误来自 ffmpeg 的 stderr、极难归因；库内 `libx264`
只在 imageio 构建里被验证过。非冻结维持 `which` 优先。

**测试**

- `test_非冻结时优先用PATH里的ffmpeg`：monkeypatch `shutil.which` 返回假路径 ⇒ 返回该路径。
- `test_非冻结时PATH没有才用包内`：monkeypatch `shutil.which` 返回 `None` ⇒ 返回 `imageio_ffmpeg.get_ffmpeg_exe()` 的值。
- `test_冻结时忽略PATH里的ffmpeg`：`sys.frozen=True` + `shutil.which` 返回假路径 ⇒ 返回**包内**路径（不等于假路径），且断言 `shutil.which` 未被调用。

**命令**: `.venv/Scripts/python.exe -m pytest tests/test_ffmpeg_exe.py -q`
**Expected**: `3 passed`

**Commit**: `feat(pack): A7 冻结时强制使用包内 ffmpeg（跳过 PATH 探测）`

---

## Task 6: A8 启动 banner —— 三行证据 + 去掉 emoji

**Files**: Mod `bbh/web/__main__.py`；Mod（追加）`tests/test_web_entry.py`

**改动**：把现在那行 `print("🏀 篮球进球自动集锦 —— http://127.0.0.1:7860/")` 换成纯函数
`banner(port: int, data_dir: str, ffmpeg_exe: str) -> str`，内容为**三行证据**（实际端口 / 数据目录绝对路径 /
实际使用的 ffmpeg 路径）+ 一行「关掉本窗口＝退出程序」。**去掉 🏀**：emoji 在 stdout 被重定向时会
`UnicodeEncodeError`，而症状（双击闪退）与「缺 VC++ 运行库」互相冒充，会白耗排查时间。
`main()` 打印它（必须在 `find_free_port` 与 `_ffmpeg_exe()` 之后）。

**测试**

- `test_banner能被GBK编码`：`banner(...).encode("gbk")` 不抛 —— 这就是 emoji 崩溃的机制本身。
- `test_banner不含非BMP字符`：`all(ord(c) <= 0xFFFF for c in banner(...))`，且不含 `🏀`。
- `test_banner含端口数据目录与ffmpeg路径`：三个入参都出现在返回值里。

**命令**: `.venv/Scripts/python.exe -m pytest tests/test_web_entry.py -q`
**Expected**: `13 passed`

**Commit**: `feat(pack): A8 启动 banner 打三行证据并去掉 emoji（GBK 可编码）`

---

## Task 7: A9 同步目录检测（只警告，不拦启动）

**Files**: Mod `bbh/web/__main__.py`；Mod（追加）`tests/test_web_entry.py`

**改动**：纯函数 `sync_dir_warning(path: str, env: dict | None = None) -> str | None`：把 `path` 与
`env` 里 `OneDrive` / `OneDriveCommercial` / `OneDriveConsumer` 的值做前缀比较（`os.path.normcase` + `abspath`，
跨盘符不许抛），命中 ⇒ 返回一句中文警告（说明 `work/` 会被持续上传、建议换到 `D:\`）；不命中 ⇒ `None`。
`main()` 检测到只 `print` 警告，**继续启动**（误判时拦启动＝程序打不开，比同步麻烦得多）。

**测试**

- `test_命中OneDrive前缀时给警告`：`env={"OneDrive": r"C:\Users\x\OneDrive"}`，path 取其子目录 ⇒ 返回非空且含「同步」。
- `test_不命中返回None`：path 在 `D:\老王篮球集锦\data`、env 指向 C 盘 OneDrive ⇒ `None`。
- `test_大小写与跨盘符不炸`：盘符不同 ⇒ `None` 且不抛。
- `test_检测到警告也不拦启动`：monkeypatch 使警告命中 + `uvicorn.run` 桩 ⇒ `main()` 仍走到 `uvicorn.run`。

**命令**: `.venv/Scripts/python.exe -m pytest tests/test_web_entry.py -q`
**Expected**: `17 passed`

**Commit**: `feat(pack): A9 同步目录检测——只警告不拦启动`

---

## Task 8: A4 兼容层顶替 gradio（拔掉 gradio）

**Files**: New `bbh/_gr_compat.py`、New `tests/test_gr_compat.py`；Mod `bbh/app.py:20`、`bbh/web/app.py:15`、`bbh/web/app.py:50`；Mod 8 处测试 import

**改动**

1. 新增 `bbh/_gr_compat.py`：一个**点名名单**的替身（⛔ 不用模块级 `__getattr__` 万能兜底 ——
   未列出的名字必须**立刻 `AttributeError`**，否则以后有人真去跑旧界面会「假装成功」，那是最难查的问题）：
   - `class Error(Exception)`：`__init__(self, message="", **kw)` 存 `self.message`（handler 用 `getattr(exc, "message", None) or str(exc)`）
   - `def update(**kw) -> dict`：`return {"__type__": "update", **kw}`（**实测 gradio 5 的 `gr.update` 返回的就是 dict**）
   - `class SelectData: pass` / `class Request: pass`（10 处签名注解在 import 时求值 ⇒ 这两个名字必须存在）
   - `build_app()` 用到的组件名（`Button` / `Row` / `Markdown` / `State` / `Column` / `HTML` / `Textbox` / `Number` /
     `Group` / `Tab` / `Dropdown` / `Gallery` / `Video` / `Image` / `Slider` / `CheckboxGroup` / `Dataframe` /
     `Radio` / `Timer` / `Accordion` / `Blocks` / `themes`）—— 每个都是空类（旧界面永不执行；只用其名字）
   - 模块级 `gr = sys.modules[__name__]` 或用 `types.SimpleNamespace` 导出同名对象，使调用方仍写 `gr.Error`
2. `bbh/app.py:20` 与 `bbh/web/app.py:15`：`import gradio as gr` → `from ._gr_compat import gr`（app.py 在 `bbh/` 包内）
3. `bbh/web/app.py:50`：handler 装饰器从 `gr.Error` 换成兼容层的 `Error`（`@app.exception_handler(gr.Error)` 内容不变）
4. **三处必须联动**（实测的隐性契约）：`bbh/app.py:657`（`except (gr.Error, …)`）、`bbh/app.py:1311`（`except gr.Error`，
   生成器）、`bbh/app.py:2023`（故意的重抛 `raise gr.Error(str(ex)) from ex`，注释写明「裸异常会半途杀死生成器、
   按钮永久卡灰」）。兼容层的 `Error` 是同一个类对象 ⇒ 这三处**无需改字面**，但要**验证**它们仍生效。
5. 8 处测试 import 改到兼容层：`tests/test_app.py:5`、`test_calib_per_video.py:11`、`test_delete_events.py:8`、
   `test_manual_groups.py:426`、`test_multi_segment.py:233`、`test_shooter.py:769`、`test_web_calib.py:117`、`test_web_jobs.py:5`。
   **40 处 `pytest.raises(gr.Error)` 断言一行不动。**

**测试（先写、先跑红）**

- `test_update返回dict且形状与gradio5一致`：`update(visible=False, interactive=False) == {"__type__": "update", "visible": False, "interactive": False}`。
- `test_Error带message属性`：`Error("请先填路径").message == "请先填路径"` 且 `str(...) == "请先填路径"`。
- `test_未列出的名字立刻报错`：`getattr(gr, "NoSuchComponent")` ⇒ `AttributeError`（点名名单的牙）。
- `test_不装gradio也能import bbh_app`（**子进程**）：`python -c "import sys, bbh.app; assert 'gradio' not in sys.modules"` ⇒ 退出码 0。
- `test_gen_selected的异常契约仍成立`：直接调 `bbh.app.gen_selected` 的异常路径，断言抛的是 `bbh._gr_compat.Error`
  （证明第 4 条的 2023 处联动未断）。

**命令（本任务必须全量）**: `.venv/Scripts/python.exe -m pytest -q`
**Expected**: 全绿，且尾部那行 `⚖ work/ 写守卫：非预期拦截 0 次（预期触发 ≥1 次…）✅`

**Commit**（两个，便于分别 revert）：
1. `feat(pack): A4 新增 gradio 兼容层并切换到它（bbh/app.py 与 bbh/web/app.py）`
2. `test(pack): A4 把 8 处测试 import 切到兼容层（40 处断言不动）`

---

## Task 9: A5 `requirements.txt` 去掉 gradio + 钉死版本

**Files**: Mod `requirements.txt`；New `tests/test_requirements.py`

**改动**：去掉 `gradio>=5.0,<6`；把剩下的 `>=` 改成 `==` 的实际在用版本（取自 `pip freeze`：
`opencv-python==5.0.0.93`、`numpy==2.5.2`、`imageio-ffmpeg==0.6.0`、`pytest==9.1.1`、`fastapi==0.141.1`、
`uvicorn` 与 `starlette` 用 venv 实测版本）。**注意**：`bbh/` 不 import pandas / scipy / sklearn，
它们只是 gradio 的传递依赖 ⇒ 本项落地后它们自动掉出 import 图（设计文档 §3-B 因此**不需要 `excludes`**）。

**测试**：`test_requirements不含gradio`；`test_requirements每条都钉了版本`（每条非注释行含 `==`）；
`test_bbh不许出现import gradio`（遍历 `bbh/**/*.py` 断言无 `import gradio`）。

**命令**: `.venv/Scripts/python.exe -m pytest tests/test_requirements.py -q`
**Expected**: `3 passed`

**Commit**: `build(pack): A5 requirements 去掉 gradio 并钉死版本`

---

## Task 10: A10 「打开数据目录」按钮（API + 前端）

**⚠ 本任务开工前需先勘查 `frontend/src` 的结构**（哪个页面/组件放按钮、`shared/api.js` 的调用形态、
构建产物命名），勘查结论写进本任务的 ledger 行。接口与验收如下：

**Files**: Mod `bbh/web/api_session.py`（新端点）、Mod `frontend/src/**`（按钮）、Regenerated `bbh/static/**`

**改动**

- 后端：`POST /api/session/open-data-dir` ⇒ 打开**当前工作区根**（非冻结＝仓库根，冻结＝`<exe>/data`）。
  动作隔离成可注入的函数（`_reveal(path)`，Windows `os.startfile`），便于测试不真开资源管理器。
  路径**只允许**工作区根，⛔ 不接受请求参数里的任意路径（沿用 `media.py` 的收束思路）。
- 前端：剪辑页顶部一个按钮，文案「打开数据目录」，成功后 toast 提示「已打开」。
- 必须 `npm --prefix frontend run build` 重新产出 `bbh/static`，产物**入库**（本仓约定）。

**测试**

- `test_open_data_dir调用reveal且参数是工作区根`：monkeypatch `_reveal` 捕获入参 ⇒ 断言等于工作区根。
- `test_open_data_dir不接受路径参数`：带 `?path=C:\Windows` 请求 ⇒ 入参仍是工作区根（防越权）。
- `test_open_data_dir失败返回error契约`：`_reveal` 抛异常 ⇒ 400 且响应体单键 `{"error": 中文}`。
- （前端）构建通过 + `bbh/static` 有内容变化即算验过（本仓 e2e 在 `tests/e2e/`，属可选）。

**命令**: `.venv/Scripts/python.exe -m pytest tests/ -q -k "open_data_dir or web_session"`
**Expected**: 全绿

**Commit**: `feat(pack): A10 打开数据目录按钮（API + 前端 + 重建 bbh/static）`

---

## 收尾

全部任务完成后：`git -C <SRC> tag exe-v0.1.0`（设计文档 D6-5，实测由构建脚本负责打，本次手工先立一个）——
**仅在用户确认第一版可交付时打**。随后另立 §3-B 构建工程计划。
