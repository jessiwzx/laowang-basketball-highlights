# 标定向导页（Plan D）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把旧 Gradio 标定页（`bbh/app.py` 的 ⚙ 标定设置 Tab）1:1 平移成 Vue3 + Element Plus 的**四步向导页**（`frontend/src/calib/CalibWizard.vue` + 子组件），纯前端接线**已就绪**的 `/api/calib/*`，**零后端改动**。

**Architecture:** 页面级状态全在 `CalibWizard.vue`（一个 ref）；四个步骤面板 + 段管理卡 + 已有标定弹窗各是一个子组件，**只收 props / 只发 emits，自己不取数**。所有取数与写操作走页面里的 `api`（`src/shared/api.js`），写操作返回的 `_meta()` 回包**整份替换**页面状态 ⇒ 屏上一切数字都是服务端权威值，前端不自己算。纯换算与判据（点击→帧像素、步骤门、段行→路径参数、滑块初值）下沉到 `calibkit.js`，由 `frontend/selftest.mjs` 在 Node 层直接断言。

**Tech Stack:** Vue 3（`template:` 字符串 / SFC）、Element Plus 2.14.6（全量引入）、Vite 5 多页构建、Node + Playwright（`tests/e2e/`）、pytest（wrapper）。

**Spec:** `docs/superpowers/specs/2026-09-16-frontend-refactor-design.md`（标定相关：`:16` 痛点表第 4 行、`:31` 拍板「去掉拖拽上传」、`:58` 目录 `src/calib/ CalibWizard.vue + 子组件`、`:120-127` §API 标定、`:141` 守门链、`:157` 落盘兼容、`:175` e2e「标定抽帧点筐口」、`:190` 实施顺序第 5 条）

**BASE:** `2c2cb8c`（本计划的地基；分支 `feature/vertical-highlight`，**不 merge / 不 push**）

---

## Global Constraints

（spec 的项目级要求 + 本仓执行纪律，逐字照抄；**每个任务的要求都隐含包含本节**）

- **分支**：只进 `feature/vertical-highlight`。**绝不** merge、**绝不** push、**绝不** rebase。
- **绝不动用户的 `work/` 状态文件**：分析只读。测试/实验一律 `cwd=临时目录`（`work/` 是相对路径，按 cwd 解析）。
- **仓库根 5 档红线**：`calibration.json` / `calibration_ref.png` / `calibration_seg2.json` / `calibration_ref_seg2.png` / `tags.json` —— **一个字节都不许动**（无 git 副本）。`ui_prefs.json` 同性质（写守卫的 6 档快照清单里含它）。
- **绝不跑 `python -m bbh.web`**：`bbh/web/__main__.py` 会把仓库根标定档搬进 `work/`。起服务一律 `uvicorn.run(create_app(), host="127.0.0.1", port=<空闲端口>)` + `cwd=临时目录`。
- **绝不**用宽泛 `taskkill //IM`；停后台进程按进程句柄 terminate。
- **用户自己的 7860 实例**：不杀、不占、不许出现在任何请求 / 探活 / 端口扫描名单里（e2e 用 `_free_port()` 拿空闲端口）。
- **产物 `bbh/static/**` 一律重建、绝不手改**：`cd frontend && npm run build`。**build 必须先于**任何 e2e / 冒烟（冒烟打的是构建产物）。
- **实现者只 `git add` 本任务点名的具体路径**，永不 `git add -A` / `git add .`。commit 信息用中文、说清「改了什么 + 为什么」。
- **实现者不许派子代理**；**绝不并行派两个实现者**。
- **改一处只跑相关用例**（全量只在 Task 9 收尾跑一次）：`cd frontend && node selftest.mjs`、`./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_web_calib.py -q`、`npm run build`、e2e spec 单跑。
- Python 一律 `./.venv/Scripts/python.exe -X utf8`（Windows / 中文路径）。
- **`bbh/` 一行不改**（含 `app.py` 的报错文案、`api_calib.py` 的任何字段）—— 本计划是**纯前端**计划（R1）。
- **落盘格式一字不改**（spec `:157`）：本计划只**读** `work/`，唯一写入由服务端既有函数发生（`build_calibration` / `refine_hsv` / `del_segment`），前端不许自己拼文件名、不许自己写档。
- **不动 `run_app.bat`**、不动 `bbh/web/**`、不动 `tests/test_*.py`（e2e wrapper 是**新增**文件，见 Task 2）。

---

## 既有契约（Plan D 的地基——实现者必读，全部已在盘上）

### 后端 `/api/calib/*`（`bbh/web/api_calib.py`，`prefix="/api/calib"`，注册于 `bbh/web/app.py:106-108`）

**公共回包 `_meta()`（8 键，`api_calib.py:60-70`）** —— `GET /state`、`POST /point`、`DELETE /point`、`POST /build`、`POST /refine` 的基底：

| 键 | 类型 | 含义 |
|---|---|---|
| `points` | `[[float, float], …]` | 已点坐标（**帧像素**，不是百分比） |
| `hint` | `str` | 引导文案（`app.py:61-70` 的 `click_hint`，含 `**` 标记） |
| `canBuild` | `bool` | `len(points) >= 3 and frame_rgb is not None` |
| `ready` | `bool` | 本视频**已有标定档**（`_calib_ready`） |
| `segments` | `[str, …]` | 补充段下拉串，如 `["段2（3:00 起）"]`；不足 2 段时 `[]` |
| `segListMd` | `str` | 段列表 markdown；不足 2 段时 `""` |
| `hasPreview` | `bool` | 会话里**有预览图**（生成/微调过；抽帧会清掉） |
| `msg` | `str` | 操作结果文案（默认 `""`） |

**错误形状**：一切失败都是 `{error: "中文文案"}`；`shared/api.js` 已把它抛成 `Error(error 原文)`，调用方一律 `catch (e) { ElMessage.error(e.message) }`（**不要**再改写文案）。守卫失败统一 400。

| 端点 | 请求 | 回包 | 关键错误（400 文案逐字） |
|---|---|---|---|
| `GET /state` | — | `_meta()` | **无错误分支**：没视频路径也 200（顶栏标签靠它，见 R8） |
| `GET /frame.png?t=<float>` | query `t`（默认 `0.0`） | **原始 PNG** + `Cache-Control: no-store` | 无视频路径 `请先填视频路径（本机文件全路径）`；抽帧失败 `在 <t:.0f> 秒处取帧失败。…` |
| `GET /preview.png` | — | 原始 PNG + `no-store` | 404 `还没有可看的预览——先点「生成标定」` |
| `POST /point {x, y}` | `x`/`y` **必填 float** | `_meta()` | 无 |
| `DELETE /point` | — | `_meta()` | 无 |
| `POST /build {segT?}` | `segT: float \| None = None` | `_meta()` + `previewUrl`，且 `segListMd` 被覆写 | `请先抽帧（选参考画面）再生成标定` ／ `请至少点击 3 次：筐口左端、筐口右端、球` ／ `请先「生成标定」做主标定（管片头段），再补移动后的段` ／ `新段起点需 >0：…` ／ `新段起点须晚于现有最后一段（<m:ss> 起）` |
| `POST /refine {sLow, vLow}` | 两个 **必填 int** | `_meta()` + `previewUrl` | `请先点击「生成标定」，再微调` |
| `GET /segments` | — | **只有 2 键**：`segments` / `segListMd` | 无视频路径 |
| `DELETE /segments/{i}` | 路径参数 `i: int` | **只有 3 键**：`segments` / `segListMd` / `msg` | `所选段不在列表里（页面数据过期），请刷新页面重试` ／ `只有一段主标定，没有可删的补充段` |
| `GET /existing` | — | 自有形状（下表） | 无视频路径 |

**`GET /existing` 的 9 键**（`api_calib.py:194-206`）：`ready: bool`、`hasReferenceFrame: bool`、`calibT: float`、`calibration: null | {hsvLow: [H,S,V], hsvHigh: [H,S,V], hoopRect: [x,y,w,h], frameSize: [w,h]}`、`wideHsvNote: str`、`segments: [str]`、`segListMd: str`、`calibPath: str`（无则 `""`）、`files: [str]`（`work/<视频名>/` 下以 `calib` 开头的文件名，`sorted`）。

**三条必须记住的服务端事实**：

1. **点是帧像素**：`add_point` 收的是 `gr.SelectData.index`（`app.py:175-176`），`_calib_from_pts` 直接 `round(x), round(y)` 切帧数组、`hoop_rect_from_rim_points(tuple(pts[0]), tuple(pts[1]))`（`app.py:193-195`）。**原型的百分比只是显示约定** ⇒ 前端必须换算（R4）。
2. **抽帧会清点、清预览**：`GET /frame.png` 成功后 `s.points = []`、`s.preview_png = None`、`s.rev += 1`（`api_calib.py:93-96`）⇒ **抽帧 = 标点从头来**（R5）。
3. **`build` 用 `segT is not None` 分流**（`api_calib.py:139`）⇒ 主标定**必须**发 `{}`；发 `{"segT": 0}` 会掉进补段分支再被 400（R6）。

**镜像 / 缓存**：`frame.png` 与 `preview.png` 都发 `Cache-Control: no-store`（`api_calib.py:101-102`、`:111-112`）——`?v=<rev>` 是 docstring 建议的加固，但 `rev` **不下发**在回包里，前端记不住 ⇒ **前端只拼 `?t=`**，禁缓存由 `no-store` 从根上保证（同 URL 换视频不串台，`api_calib.py:97-100` 逐字理由）。`previewUrl` 由 `build`/`refine` 回包给出（形如 `/api/calib/preview.png?v=3`），**用它**，别自己拼。

### 前端惯例（照抄，别自创）

- **入口形状**（业务页 17 行，`wall/main.js` ＝ `edit/main.js` 逐字同形）：9 行固定 import 头（`vue` / `element-plus` / `zhCn` locale / 两个 EP css / icons / `../shared/theme.css` / `mountTabbar`）+ `import <Bay>.vue` → `mountTabbar("<page>.html", "")` → `createApp(<Bay>)` → 两行 locale 注释 → `app.use(ElementPlus, { locale: zhCn })` → 注册 icons → `app.mount("#app")`。
- **`shared/api.js`**：`api.get(url)` / `api.post(url, body)` / `api.del(url, body)`（返回解析后的 JSON；失败抛 `Error(error 原文)`）；`qs(params)` 拼 query（跳过 `undefined/null/""`）。
- **`shared/session.js`**：`store.state` / `store.on(f)` / `store.off(f)` / `store.open(videoPath)`（`POST /api/session`）/ `store.refresh()`（`GET /api/state`）。**只有 `store` 一个导出**。
- **`shared/time.js`**：`fmtT(sec)`（`m:ss.s`）、`parseT`、`tsec(row, offset)`、`dispT(row, offset)` —— 时刻格式的唯一来源。
- **`shared/md.js`**：`stripMd(s)` —— 脱掉服务端文案里的 `**` 与反引号（渲染层去标记，**不改文案**）。`hint` 与 `msg` 渲染前一律过它。
- **`shared/tabbar.js`**：`mountTabbar(current, hint = "")`；`PAGES` 里标定项逐字 `["calib.html", "⚙ 标定设置"]`。
- **路径输入框惯例**（`edit/EditBay.vue:443-468`，逐字照抄形状）：`pathInput = ref("")`；`onState(s)` 里 `if (!pathInput.value) pathInput.value = s.videoPath || s.lastVideo || ""`（用户正在改时不顶掉）；`onPathChange()` 里 `trim()` → 与 `state.value?.videoPath` 相同则 return → `await store.open(p)` → 回填 `state.value?.videoPath || p`；`catch (e) { ElMessage.error(e.message) }`。绑在 `@change`（回车/失焦），**不用 watch 逐字触发**。
- **`selftest.mjs`**：注册式 `test(name, fn)`（顶层、行首、`^test(`），纯 JS 层；每节以 `// ---- xxx ----` 分隔；节内**顶层 `await import("./src/...")`** 解构出被测函数；末尾 `let failed = 0` 的 runner 逐条串行跑、`process.exit(failed ? 1 : 0)`。当前 **35 条**。

### 冒烟与 e2e 惯例

- **`frontend/smoke.mjs`**：三页地标表在 `:31-34`（`[file, label, heading, probe]`），判定分支 `if (heading) … else if (probe === "input") … else {tag 分支}`。**标定页今天走 `heading="标定设置"` 分支**（`#app h2`，骨架专有），edit/wall 走 probe（`input` / `tag`）。注释 `:28-30` 逐字：「地标只有 `app.mount()` 真跑成功才会出现——**地标换掉，这条约束不换**」。`:26-27` 说明**不等 `networkidle`**（首屏有 0.8s 轮询），用 `domcontentloaded` + 显式等 `.tabbar a.on`（同步屏障：`mountTabbar` 在 `app.mount()` 前、同一段同步代码）。
- **e2e spec 头**（`tests/e2e/*.spec.mjs` 逐字同构）：`const { chromium } = await import(process.env.BBH_PLAYWRIGHT || "playwright")`；`BASE = process.env.BBH_BASE`、`VIDEO = process.env.BBH_VIDEO`（缺则 throw）；`let pass = 0` + `ok(cond, msg)`（假则 `throw new Error(\`❌ ${msg}\`)`、真则 `pass++`）+ `eq(a, b, msg)`（严格 `===`，内部调 `ok` ⇒ **也 +1**）；`waitFor(fn, msg, ms = 10000)`（每 150ms 轮询，**不计 pass**）；收尾 `console.log(\`✅ 全过 ${pass} 项\`)`。**项数 = ok 调用 + eq 调用**（扣掉 `eq` 定义体内那一处 `ok`）。
- **e2e 夹具真相（本计划的关键约束）**：`tests/e2e/test_edit_bay_e2e.py::_fixture` **不造真视频**（只写 `events.json` + `calibration.json`，`video=` 那个 mp4 路径指向不存在的文件）⇒ **标定 spec 必须自带夹具**（R14），且**不能预置 `calibration.json`**（否则「生成标定」的落盘断言空转）。
- **wrapper 惯例**（`tests/e2e/test_wall_e2e.py` 是模板）：从 `test_edit_bay_e2e` 复用 `NAME, NODE, PW, ROOT, _fixture, _free_port, _untouched, _wait_up`；自带 `SPEC = Path(__file__).resolve().parent / "<name>.spec.mjs"`；`_free_port()` 拿端口 → `uvicorn.run(create_app(), host='127.0.0.1', port=<port>)` 子进程 + `cwd=root`（临时目录）→ `_wait_up(base, proc, log)` → `subprocess.run([NODE, str(SPEC)], env=dict(os.environ, BBH_BASE=…, BBH_VIDEO=…, BBH_PLAYWRIGHT=PW.as_uri(), PYTHONIOENCODING="utf-8"), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)` → `assert r.returncode == 0` + `assert "✅ 全过" in r.stdout` + **副作用真落盘**断言 + `assert _untouched(...) == b`。缺 node 或 PW ⇒ `pytest.skip`。
- **`tests/e2e/count-spec-items.py` 只认剪辑台**（`spec_from_file_location("bay_e2e", …/"test_edit_bay_e2e.py")`、`mod.SPEC` 恒为 `edit_bay.spec.mjs`、不读 `argv`）⇒ **墙 43 与标定项数都数不出来**（R15，工具泛化归 Plan E）。

---

## 原型对照（文案与视觉的冻结基准——浓缩自 `前端原型/标定向导.html`，21497 B）

原型是**纯前端假数据**（`fetch` 零命中），设计（`el-steps` 四步 + 独立段管理卡 + 点选层 + 两个滑块）是冻结基准；**服务端文案优先**（R7）。逐字段落：

**四步**（`el-steps :active="step" align-center finish-status="success"`；`step = ref(0)`；四步用 `v-if="step===N"` 切换）：

| # | `title` | `description` |
|---|---|---|
| ① | `① 选视频抽帧` | `填本机路径` |
| ② | `② 标关键点` | `筐口两端 + 球 1~3 次` |
| ③ | `③ 生成并验证` | `看 ROI 预览` |
| ④ | `④ 微调 HSV` | `滤肤色/杂色` |

**步骤①**（原型 `:99-119`）：`el-alert type="info" :closable="false" show-icon`，title 逐字 `标定跟着视频走：每个拍摄机位做一次；同机位的新视频自动复用（指纹匹配）。`；路径输入框（placeholder 逐字 `D:\videos\2026-09-18.mp4（直接读本机文件，不复制不上传）`，`#prefix` 里 `<el-icon><VideoCamera /></el-icon>`）；`抽帧` 按钮；文案 `时间点（秒，跳到球在筐口附近的画面）`；`el-input-number :min="0" :step="30"`；`跳到该时间抽帧` 按钮；帧就绪后 `div.label` ＝ `参考帧 · 视频 <m:ss> 处` + helper 逐字 `✅ 帧已就绪——点「下一步」开始标点。`

**步骤②**（原型 `:122-159`）：外层 grid `grid-template-columns:1fr 260px;gap:18px`。左列帧容器 `cursor:crosshair`，`@click` 落点、`@mousemove` 画十字、`@mouseleave` 收起；`div.label` ＝ `参考帧 · 在图上点击标点（先筐口，后球）` + `· 光标 (x.x%, y.y%)%`；**十字辅助线**两条 1px 白线（`.cross-v` 绑 `left`、`.cross-h` 绑 `top`，`pointer-events:none`）；**已落点标记** 18px 圆（`transform:translate(-50%,-50%)`，筐口两点蓝 `#409eff`、球绿 `#67C23A`，球的标记上方写编号 `no`）；图例两条（`筐口左端 / 右端`、`球（点 1~3 次）`）。右列点列表：每行读数 + 一个 `撤销`（`text type="danger"`）按钮，空时占位 `（尚未标点）`。helper 见 R7（**取服务端 `hint`**）。底部 `清空全部` 按钮。**⚠ 实现期 DOM 微调（E22）**：原型把 `div.label` 放在帧容器**内**；实现版把它移出点击容器（新增 `.frame-col` 包一层）——因为 `imgPoint` 拿的是点击容器的 `getBoundingClientRect()`，盒里含标签会让上报的帧坐标系统性偏大（实测 +13.5px/360）。**视觉不变**（label 的字号/间距归 `.frame-col`）。**别照原型把 label 挪回容器里。**

**步骤③**（原型 `:162-194`）：`⚙️ 生成标定` 按钮；未生成时文案 `由标点计算 ROI（检测区域）与色域`；生成后：预览图 + 图例四条（`球色样本命中` / `筐口` / `筐口平面（进球判定线）` / `检测区域 ROI`）+ 绿/红 alert + 文案 `🗂 参考帧已存档——日后怀疑镜头位移时，可跳到同时刻对照复检（标定随视频走，存 work/<视频名>/）` + 宽色域 alert（`type="warning"`）。

**步骤④**（原型 `:197-222`）：说明 `通常第 ③ 步通过就不需要动这里。只有当检测把肤色/黄色地板认成球时，才调大饱和度下限。`；两个滑块标签 `饱和度 S 下限（调大可滤肤色）— 当前 {{sMin}}` / `明度 V 下限（调大可滤暗部噪声）— 当前 {{vMin}}`；`el-slider :min="0" :max="255" show-input size="small"`（无 `:step`，即 1）；info alert `静止绿点（场边广告/灯）会在检测时自动滤除，标定阶段不用管`；结果 alert；两个按钮 `🔄 重算预览` / `✓ 完成标定`（后者只弹 `标定完成！已存 calibration.json —— 可去剪辑台开始检测`）。

**底部导航**（原型 `:224-228`，`.footer-nav` 在第一个 `.steps-card` 内、四个步骤共用同一对按钮）：

```html
<el-button :disabled="step===0" @click="step--">← 上一步</el-button>
<el-button type="primary"
           :disabled="step===3 || (step===0&&!frameReady) || (step===2&&!calibBuilt)"
           @click="step++">下一步 →</el-button>
```

**📐 标定段管理卡**（原型 `:231-250`，**第二个 `.steps-card`**，在向导卡之外）：标题 `📐 标定段管理（镜头中途挪动过？为挪动后的画面补一段标定）`；`➕ 新增补充段` 按钮 → 重置向导回步① + toast `回到第①步：抽挪动后画面的帧，重新走一遍标点`；段行 `🟢 主段 · 从 0:00 起 · <机位描述>` + `el-tag type="success" 当前生效`、`🔵 补充段 · 从 <时刻> 起 · <描述>` + `删除`（`danger plain`）；脚注逐字 `段起点规则：新段时刻须 > 0 且晚于现有最后一段（早于已有时段没有意义会被拒绝）；删除段不影响主段。`

**「查看已有标定」**（原型 `:83-88`）：标题行右侧 `el-button type="info" plain size="small"` 文案 `查看已有标定`（原型只弹 toast，无摘要面 —— R2 把它做成真摘要弹窗）。

**原型脚注里的冲突句**（`:252-254`，**与 spec 直接冲突，不实施**，见 R12）逐字：`实施时 app.py 的 calib_video 上传控件与六处「请先上传视频…」报错文案一并改为「请先填路径…」`

---

## 计划期裁定（R1–R23，实现与复审都以此为准；**R15b / R20b** 是交付后补的两条，就地插在对应行之后）

| # | 裁定 | 依据 / 理由 |
|---|---|---|
| **R1** | **纯前端**：零后端改动（`bbh/**` 一行不改）。spec `:126` 写的 `POST /api/calib/segments {t}` 实盘**不存在**（`api_calib.py` 十条路由里没有它），其功能由 `POST /api/calib/build {segT}` 承担 ⇒ 前端按 `build{segT}` 实现 | 用户 2026-09-21 裁定「纯前端」；功能等价已由 `tests/test_web_calib.py` 16 例钉住，新增端点会造第二条写路径 |
| **R2** | **做「查看已有标定」摘要面**（顶栏按钮 → 弹窗，`GET /api/calib/existing`）。属**新增面**（旧 Gradio 无此按钮；spec §API 表列了该端点、原型画了按钮） | 用户 2026-09-21 裁定「做摘要」 |
| **R3** | **独立 spec**：新建 `tests/e2e/calib.spec.mjs` + `tests/e2e/test_calib_e2e.py`；`frontend/smoke.mjs` 的标定行从骨架 h2 地标换成 SFC 特征元素（R3 的具体形状见 Task 2） | 用户 2026-09-21 裁定「独立 spec」；冒烟地标**必须**同任务换（否则假绿：骨架 h2 没了，旧分支逻辑会先红，但若只删 heading 不改分支就变成「跳过判据」） |
| **R4** | **点坐标是帧像素**：前端把点击换算成帧像素再 `POST /point`；**百分比只用于读数显示**（原型冻结的读法 `(x.x%, y.y%)`）。换算纯函数 `imgPoint()` 进 `calibkit.js` | `app.py:175-176` / `:193-195`（见既有契约事实 1） |
| **R5** | **抽帧即清点**：`GET /frame.png` 成功后服务端清 `points`、清预览 ⇒ 页面把本地点列表清空、`frameReady` 置真。这是**服务端契约**，不是页面选择 | `api_calib.py:93-95`（见事实 2） |
| **R6** | **主标定请求体恒为 `{}`**；补充段发 `{segT: <本步抽帧用的时刻>}`。段起点取「本步抽帧的时间点」（与旧界面「时间点」输入同源），**不另设输入框**；后端越界文案原样显示 | `api_calib.py:139` 的 `is not None` 判定（见事实 3）；旧界面 `build_segment_calibration(pts, frame_rgb, t, …)` 的 `t` 即时间输入 |
| **R7** | **步骤②引导文案取服务端 `hint`**（渲染前过 `stripMd`）。原型的四分支 helper 只作视觉参考；冲突时以服务端为准 | spec `:123` 明写 `POST /point` 返回「提示文案」；`click_hint` 与旧界面 1:1 且被单测钉着句式（`筐口左端`/`筐口右端`/`球`） |
| **R8** | **步骤门用服务端权威量**：①→② 用本地 `frameReady`（`_meta()` **无帧标志**，只能本地记）；②→③ 用 `canBuild`；③→④ 用 `hasPreview`；顶栏「已标定」标签用 `ready` | `_meta()` 八键（既有契约）；`canBuild` / `hasPreview` / `ready` 均为服务端真值 |
| **R9** | **不是所有 calib 回包都是 `_meta()`**：`GET /segments`（2 键）、`DELETE /segments/{i}`（3 键）、`GET /existing`（自有 9 键）⇒ 这些回包**只取自己要用的键**，**绝不整份替换**页面会话态（否则 `points`/`ready` 等被抹成 `undefined`） | 既有契约的端点数键 |
| **R10** | **滑块初值来自 `GET /existing`**：`_meta()` 不含 HSV；`calibration.hsvLow` 是 `[H, S, V]` ⇒ S = `[1]`、V = `[2]`（`app.py:301` 逐字 `calib.hsv_low = (calib.hsv_low[0], int(s_low), int(v_low))`）；无标定时用原型默认 `86` / `60` | 无标定时 `existing.calibration` 为 `null` ⇒ 兜底 |
| **R11** | **删段的路径参数 `i` ＝ 该行在 `segments` 数组中的下标 + 2** | `_seg_choices` 从「段 2」起编号（`app.py:422-427`），`delete_segment` 反查 `choices[i-2]`（`api_calib.py:179`） |
| **R12** | **不改 `app.py` 报错文案**：原型脚注那句「六处报错文案一并改为『请先填路径…』」**与 spec `:81` 直接冲突 ⇒ 以 spec 为准，不实施**。新路由层自带 `请先填视频路径（本机文件全路径）`，页面**原样显示**服务端文案 | spec `:79-82` 复用边界：`app.py` 报错文案保持原样「回退到旧界面时零影响」 |
| **R13** | **订正 `calib/main.js` 的旧口径**：三处「Plan B」（`:12`/`:13`/`:23`）改为 Plan D；页面主体换成 `CalibWizard.vue`（spec `:58` 的命名）；`mountTabbar` 的 hint 改空串（业务页惯例） | 旧注释是 Plan A 骨架期写的，smoke 里早已写 Plan D |
| **R14** | **标定 e2e 自带真视频夹具**：`cv2.VideoWriter` 合成 640×360 / 30fps / 6s（暗底 + 筐口横条 + 橙球），**且不预置 `calibration.json`** | 现有 `_fixture` 不造视频 ⇒ 抽帧必 400（假红或无牙）。已实测：合成视频 → 真抽帧 → 真生成 得 `hoop_rect=(422,128,187,75)`、ROI 命中 11083、落盘 `calibration.json` + `calibration_ref.png` |
| **R15** | **计数缺口沿用**：标定 spec 的项数用「一次性内联驱动」取（同墙 43 的做法），把工具泛化（`count-spec-items.py` 加 spec 参数 + wrapper 校验项数）**归 Plan E** | 用户已把工具泛化排进 Plan E 测试加固批 |
| **R15b** | **泛化时把这三条一起搬过去**（本批实跑踩出来的，工作区已删 ⇒ 记在这里当配方）：① **两行 `sys.path` 引导**——`sys.path[0]` 在 `python - <<PY`（stdin）下是 cwd，在 `python <file>` 下是**脚本所在目录** ⇒ 后者必须 `sys.path.insert` 仓库根（`from bbh.models import …` 用）**和** `tests/e2e`（夹具模块互 import：`test_calib_e2e` → `test_edit_bay_e2e`），否则 `ModuleNotFoundError`（`tests/e2e/count-spec-items.py` 里那两行的来历）；② **stdout 与 stderr 都要打**——spec 的 `ok` 抛错走 stderr，只打 stdout 的话失败时是**空白 + rc=1**（T7 定向复审 Minor 2 实测踩到）；③ 复用 `test_calib_e2e` 的 `_free_port`/`_make_video`/`_wait_up`，`cwd=临时目录`、`BBH_BASE` 指空闲端口 | 这三条是「工具自证」的前提；不写下来，Plan E 会重踩 |
| **R16** | **路径输入框进顶栏**（与剪辑台同形），步骤①的面板只做「时间点 + 抽帧 + 帧就绪」 | 换视频不该被步骤卡住；三页顶栏一致；也让冒烟地标有天然落点（`#app .topbar .el-tag`） |
| **R17** | **逐点撤销用「清空 + 重发」实现**：后端只有 `DELETE /point`（清空全部），没有单点删除 ⇒ 撤销第 i 点 = `DELETE /point` → 按原顺序重发其余点（≤4 次小请求）；`keep` 为空时改取 `GET /state` 拿权威回包。原型的逐行「撤销」按钮照做 | `api_calib.py:117` docstring 逐字「点错就删重点（DELETE 清空）」；原型（用户已批准）有逐行撤销 |
| **R18** | **帧图用 `fetch` + 对象 URL**：抽帧成功后把响应当 blob 读出来 → `URL.createObjectURL` 喂 `<img>`；换帧时 `revokeObjectURL`，`onUnmounted` 兜底。**不用** `<img src="/api/calib/frame.png?t=…">` | 抽帧失败时服务端把 ffmpeg 诊断放进 `{error: 文案}`（`在 <t> 秒处取帧失败。…`），而 `<img>` 的 error 事件**拿不到响应体** ⇒ 诊断丢失（那是旧界面的核心诊断手段）。副带正收益：一次抽帧只发一个请求、不重复下载、不涉缓存。URL 上仍带 `?v=<rev>`（docstring 建议的加固，无害） |
| **R19** | **第 6 点在前端拦**：`points.length >= 5` 时 `ElMessage.info("球最多点 3 次，够了")` 且**不发请求** | 服务端**不拒**（`api_calib.py:117` docstring 逐字「不加校验让点越攒越多」），而原型（用户已批准）冻结了这条 UX；多点会全进 `hsv_range_from_clicks`（`app.py:195` 用满 `pts[2:]`）把色域拉宽 ⇒ 拦在前端才有那声提示 |
| **R20** | **结果提示口径**：`build`/`refine` 成功后的服务端 `msg` **原样**（过 `stripMd`）渲染成一条 `el-alert type="info"`；**不**做颜色/语义推断、**不**另造「宽色域」alert（服务端已把 `_wide_hsv_note(calib)` 拼进 msg：`app.py:241`/`:281`）；失败一律 400 ⇒ 走 `ElMessage.error(e.message)`（红 toast），面板内不重复渲染。**补段态**按钮文案改 `➕ 生成补充段（起点 00:03.6）`（`fmtT(frameStamp)`，形如 `mm:ss.s`——**不是** `m:ss`；断言的判别式见 Task 7 的 E25）＋面板顶一行说明；主标定态逐字 `⚙️ 生成标定` | 服务端文案优先（同 R7 旨）；前端**拿不到**「宽色域」这个语义（`_meta()` 无该键，唯一权威是 `/existing.wideHsvNote`，归 Task 8 的摘要弹窗）；补段与主标定写的是**不同文件**（`calibration_seg{n}.json` vs `calibration.json`）⇒ 用户必须看得出当前模式（旧界面的模式可见性由独立的「补段」按钮承担） |
| **R21** | **预览图 src ＝ `st.previewUrl \|\| "/api/calib/preview.png"`**（只在 `hasPreview` 为真时渲染这个 `<img>`） | `previewUrl` 只在 `build`/`refine` 的回包里出现、**不在 `_meta()` 八键里** ⇒ 之后任何 `GET /state`（例如撤销到 0 点后的 `refreshCalib()`）都会把它抹掉，而服务端预览还在 ⇒ 页面会显示破图。`preview.png` 发 `no-store`（`api_calib.py:110-112`）⇒ 裸端点永远新鲜；优先用 `previewUrl` 只是不丢服务端的 rev 语义 |
| **R22** | **段行不编造「机位描述」**：主段行 = `主段 · 从 0:00 起 · calibration.json`（原型占位的 `<机位描述>` 用真实文件名替）；补充段行 = `segments` 里的**服务端原文**（`段2（3:00 起）`）+ `删除`；**并且渲染 `segListMd`**（过 `stripMd`，放在 `.segmd` 块）。删段成功的反馈用 `ElMessage.success(stripMd(msg))` | 数据里**没有**「机位描述」这个字段（`_seg_choices` 只给 `段N（<m:ss> 起）`，`app.py:422-427`）⇒ 编一句就是假数据；`_seg_list_md` 的 docstring 逐字「段列表文案（**标定设置页展示**）」（`app.py:408-410`）⇒ 服务端专门为本页准备的说明不渲染就是丢信息（里面还带「重新开始检测后只有新段/边界变化的时段重扫」这条 caveat）。原型段卡没有 msg 位 ⇒ 删段反馈走 toast（`del_segment` 的 msg 是短句一次性反馈） |
| **R23** | **spec `:122` 的「抽帧 PNG + 像素尺寸 + 是否已有标定」是偏离项**（与 R1 同性质）：实盘 `GET /frame.png` 返回**裸 PNG**，没有 JSON 元数据（`api_calib.py:82-102`）⇒ 像素尺寸取自 `<img>` 的 `naturalWidth/Height`（Task 3 的 `frame-loaded` → `natural`），「是否已有标定」取自 `_meta().ready`（顶栏标签 + 步骤门）。**记偏离、不改后端** | R1 同日裁「纯前端」；这两项信息前端都拿得到（帧图尺寸浏览器自带、`ready` 在 `_meta()` 里），为它加一个 JSON 元数据端点会破 `api_calib.py` 的既有 16 例单测 |
| **R20b** | **补段态必须有「入口门 + 唯一出口」**（终审 Critical A 的修法，`1d4cf5c`）：① 无主标定（`st.ready` 假）时「➕ 新增补充段」**禁用**（`hasMain = !!st?.ready`）；② 进入补段态后段卡上有「✖ 取消补段」，`cancelSeg()` = 清 `pendingSeg` + **回步 ①** + `frameReady=false`；③ `onPathChange` 成功换路径后清 `pendingSeg` | **「回步 ①」不是随手挑的语义**：补段态步 ③ 的 `segT` 锚在**屏上那帧**（E25）⇒ 若只清标志而留在步 ③，用户接着点「⚙️ 生成标定」会用**镜头挪动之后**的帧与点去重建**主标定**——静默错档（不是报错，是写对文件名的错内容）。R20 原写「补段与主标定写的是不同文件 ⇒ 用户必须看得出当前模式」，但**只给了入口没给出口**，R20b 补的就是这条。语义由 spec 的 ⑮ 块守（取消必须发生在**步 ③**才与 `addSeg` 的重置可区分） |

---

### Task 1: `calibkit.js` 纯函数层

**Files:**
- Create: `frontend/src/calib/calibkit.js`
- Modify: `frontend/selftest.mjs`（在末尾 runner（`let failed = 0`）之前插入新节；35 → 42 条）

**Interfaces:**
- Consumes: 无（纯函数，不 import 任何模块）
- Produces（后续任务只准用这些名字）：
  - `imgPoint(clientX, clientY, rect, natural) -> {x, y}`（帧像素，已 clamp）
  - `pctOf(px, total) -> number`（读数百分比）
  - `pointKind(i) -> "hoop-l" | "hoop-r" | "ball"`
  - `ballNo(i) -> number`（第 1 颗球 = 1）
  - `nextDisabled(step, {frameReady, canBuild, hasPreview}) -> boolean`
  - `segDelPath(rowIndex) -> number`（= rowIndex + 2）
  - `hsvOf(existing, defaults = {s: 86, v: 60}) -> {s, v}`

- [ ] **Step 1: 写失败测试（先写 7 条，全部跑红）**

在 `frontend/selftest.mjs` 末尾 runner 那段（`let failed = 0;`）**之前**插入：

```js
// ---- calib/calibkit.js ----
const { imgPoint, pctOf, pointKind, ballNo, nextDisabled, segDelPath, hsvOf } =
  await import("./src/calib/calibkit.js");

test("imgPoint：点击 → 帧像素（rect 与 natural 不同尺寸时要按比例换算）", () => {
  // 640×360 的帧被显示成 320×180（CSS 尺寸是帧的一半）
  const rect = { left: 100, top: 50, width: 320, height: 180 };
  const natural = { w: 640, h: 360 };
  const p = imgPoint(100 + 80, 50 + 45, rect, natural);   // 元素内 (80,45)
  assert.equal(p.x, 160);
  assert.equal(p.y, 90);
});

test("imgPoint：natural 缺失时退化成 CSS 像素，绝不产生 NaN/Infinity", () => {
  const rect = { left: 0, top: 0, width: 320, height: 180 };
  assert.deepEqual(imgPoint(80, 45, rect, null), { x: 80, y: 45 });
  assert.deepEqual(imgPoint(80, 45, rect, { w: 0, h: 0 }), { x: 80, y: 45 });
  // rect 全零（元素还没布局）时不许出 NaN
  assert.deepEqual(imgPoint(80, 45, { left: 0, top: 0, width: 0, height: 0 }, null),
                   { x: 0, y: 0 });
});

test("imgPoint：点到框外时 clamp 进 [0, natural]", () => {
  const rect = { left: 0, top: 0, width: 320, height: 180 };
  assert.deepEqual(imgPoint(-10, -10, rect, { w: 640, h: 360 }), { x: 0, y: 0 });
  assert.deepEqual(imgPoint(999, 999, rect, { w: 640, h: 360 }), { x: 640, y: 360 });
});

test("pctOf：读数是帧像素的百分比（原型冻结的读法）", () => {
  assert.equal(pctOf(160, 640), 25);
  assert.equal(pctOf(0, 640), 0);
  assert.equal(pctOf(1, 0), 0);          // 除零退化，不出 Infinity
});

test("pointKind / ballNo：类别与球号按序号定（服务端只存坐标）", () => {
  assert.equal(pointKind(0), "hoop-l");
  assert.equal(pointKind(1), "hoop-r");
  assert.equal(pointKind(2), "ball");
  assert.equal(pointKind(4), "ball");
  assert.equal(ballNo(2), 1);            // 第 1 颗球
  assert.equal(ballNo(4), 3);            // 第 3 颗球
});

test("nextDisabled：①靠 frameReady、②靠 canBuild、③靠 hasPreview、④恒禁", () => {
  assert.equal(nextDisabled(0, { frameReady: false }), true);
  assert.equal(nextDisabled(0, { frameReady: true }), false);
  assert.equal(nextDisabled(1, { frameReady: true, canBuild: false }), true);
  assert.equal(nextDisabled(1, { frameReady: true, canBuild: true }), false);
  assert.equal(nextDisabled(2, { canBuild: true, hasPreview: false }), true);
  assert.equal(nextDisabled(2, { canBuild: true, hasPreview: true }), false);
  assert.equal(nextDisabled(3, { hasPreview: true }), true);
  // 空/缺参不许抛异常（面板还没取到数时也会渲染这行）
  assert.equal(nextDisabled(0, {}), true);
});

test("segDelPath：段行下标 → 路径参数 i+2；hsvOf：从 /existing 取 S/V，缺档回默认", () => {
  assert.equal(segDelPath(0), 2);        // 第 1 个补充段 = 段 2
  assert.equal(segDelPath(2), 4);
  assert.deepEqual(hsvOf({ calibration: { hsvLow: [5, 183, 193] } }), { s: 183, v: 193 });
  assert.deepEqual(hsvOf({ calibration: null }), { s: 86, v: 60 });
  assert.deepEqual(hsvOf(null), { s: 86, v: 60 });
  assert.deepEqual(hsvOf({ calibration: { hsvLow: [5, 183] } }), { s: 86, v: 60 });
});
```

- [ ] **Step 2: 跑测试确认全红**

Run: `cd frontend && node selftest.mjs`
Expected: 7 条新用例全 FAIL（`Cannot find module .../src/calib/calibkit.js`），旧的 35 条照旧 ok。

- [ ] **Step 3: 实现 `frontend/src/calib/calibkit.js`**

```js
// 标定向导的纯函数层：只放「不经浏览器、不经服务端」就能断言的换算与判据。
// 渲染与接线由 tests/e2e/calib.spec.mjs 的 Playwright 验（同 wallkit.js 的分工）。

function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

// 点击（clientX/clientY）→ **帧像素**坐标。
// 服务端收的是帧像素：bbh/app.py:175-176 的 add_point 拿的是 gr.SelectData.index，
// app.py:193-195 直接 round(x),round(y) 切帧数组 ⇒ 百分比只是显示约定，必须换算（R4）。
// rect = 元素 getBoundingClientRect()；natural = 帧的真实像素尺寸 {w, h}（<img> @load 拿）。
// 退化保护：rect 还没布局（全零）⇒ 返回 {0,0}；natural 缺失 ⇒ 退化成 CSS 像素。
export function imgPoint(clientX, clientY, rect, natural) {
  const w = rect?.width, h = rect?.height;
  if (!w || !h) return { x: 0, y: 0 };
  const nw = natural?.w || w, nh = natural?.h || h;
  return {
    x: clamp((clientX - rect.left) / w * nw, 0, nw),
    y: clamp((clientY - rect.top) / h * nh, 0, nh),
  };
}

// 帧像素 → 读数百分比（原型冻结的读法：(12.3%, 45.6%)）。
export function pctOf(px, total) { return total ? px / total * 100 : 0; }

// 点类别按**序号**定：0=筐口左端、1=筐口右端、2+=球（服务端只存坐标，无 kind 字段）。
export function pointKind(i) { return i === 0 ? "hoop-l" : i === 1 ? "hoop-r" : "ball"; }
export function ballNo(i) { return i - 1; }        // 球标记上写的编号：第 1 颗球 = 1

// 「下一步」的门（R8）：能用服务端权威量的就用服务端，① 只能用本地量
// （_meta() 里没有「帧是否就绪」这个键）。
export function nextDisabled(step, { frameReady, canBuild, hasPreview } = {}) {
  if (step >= 3) return true;        // ④ 是最后一步，没有下一步
  if (step === 0) return !frameReady;
  if (step === 1) return !canBuild;
  return !hasPreview;
}

// 段行下标 → DELETE /api/calib/segments/{i} 的 i。
// _seg_choices 从「段 2」起编号（app.py:422-427），delete_segment 反查 choices[i-2]（R11）。
export function segDelPath(rowIndex) { return rowIndex + 2; }

// 滑块初值：_meta() 不含 HSV ⇒ 唯一权威来源是 /api/calib/existing 的 calibration.hsvLow，
// 它是 [H, S, V]（app.py:301 逐字 `(calib.hsv_low[0], int(s_low), int(v_low))`）⇒ S=[1]、V=[2]。
// 无标定（calibration 为 null）或形状不对 ⇒ 回原型默认 86/60（R10）。
export function hsvOf(existing, defaults = { s: 86, v: 60 }) {
  const lo = existing?.calibration?.hsvLow;
  if (!Array.isArray(lo) || lo.length < 3) return { ...defaults };
  return { s: Number(lo[1]) || 0, v: Number(lo[2]) || 0 };
}
```

- [ ] **Step 4: 跑测试确认全绿**

Run: `cd frontend && node selftest.mjs`
Expected: `✅ 42 通过`（35 + 7）。

- [ ] **Step 5: commit**

```bash
git add frontend/src/calib/calibkit.js frontend/selftest.mjs
git commit -m "feat(calib): 标定向导纯函数层 calibkit.js（点击换算/步骤门/段路径/滑块初值）+ selftest 7 例"
```

---

### Task 2: 页面骨架（`CalibWizard.vue` + `main.js` 重写 + smoke 地标切真页 + 标定 e2e 地基）

**Files:**
- Create: `frontend/src/calib/CalibWizard.vue`
- Modify: `frontend/src/calib/main.js`（整份重写，17 行标准形状；顺带清掉三处「Plan B」旧口径 —— R13）
- Modify: `frontend/smoke.mjs`（`:28-30` 注释 + `:31-34` 地标表 + 新 probe 分支）
- Create: `tests/e2e/calib.spec.mjs`
- Create: `tests/e2e/test_calib_e2e.py`

**Interfaces:**
- Consumes：`calibkit.js` 的 `nextDisabled`（Task 1）；`shared/api.js` 的 `api.get`；`shared/session.js` 的 `store`。
- Produces（后续任务在此骨架上填空，名字不许改）：
  - 页面级 ref：`session`（`/api/state` 回包）、`st`（`/api/calib/state` 的 `_meta()`，**权威**）、`pathInput`、`step`、`frameReady`、`frameAt`（输入框活值）、`frameStamp`（E15：屏上那张帧的时刻）、`frameRev`、`natural`、`pendingSeg`、`sMin`、`vMin`
    ⚠ **没有**独立的 `points` ref：点列表的唯一权威就是 `st.value.points`（Task 4 只读它），页面不另存一份副本（两份状态必然分叉）
  - 页面级函数：`refreshCalib()`、`onState(s)`、`onPathChange()`、`goPrev()`、`goNext()`
  - DOM 约定（e2e 与冒烟都靠它）：`.topbar`（`input` + `el-tag`）、`.steps-card`、`.panel`、`.footer-nav`（两个 `el-button`）；段管理卡是**第二个** `.steps-card`

- [ ] **Step 1: 写 `frontend/src/calib/CalibWizard.vue`（骨架）**

四个步骤面板先放 `el-empty` 占位（**不放假 UI**），后续任务逐个换成真面板：

```vue
<script setup>
import { ref, onMounted, onUnmounted } from "vue";
import { ElMessage } from "element-plus";
import { api } from "../shared/api.js";
import { store } from "../shared/session.js";
import { nextDisabled } from "./calibkit.js";

// 会话态（videoPath，来自 /api/state）与标定态（/api/calib/state 的 _meta()）分两份：
// 前者与另两页同源，后者是本页的权威量（points/canBuild/hasPreview/ready/segments）。
const session = ref(null);
const st = ref(null);
const pathInput = ref("");
const step = ref(0);
const frameReady = ref(false);    // ①→② 的门：_meta() 里没有「帧是否就绪」这个键（R8）

async function refreshCalib() {
  try { st.value = await api.get("/api/calib/state"); }
  catch (e) { ElMessage.error(e.message); }
}

function onState(s) {
  session.value = s;
  // 只在输入框还空着时回填：用户正在改路径时别被别的请求顶掉（照抄剪辑台）
  if (!pathInput.value) pathInput.value = s.videoPath || s.lastVideo || "";
}

// @change = 回车或失焦。不用 watch 逐字触发（那会每敲一个字符发一次 POST /api/session）。
async function onPathChange() {
  const p = pathInput.value.trim();
  if (!p || p === session.value?.videoPath) return;
  try {
    await store.open(p);
    pathInput.value = session.value?.videoPath || p;   // 回填服务端清洗后的路径
    step.value = 0;                                    // 换视频 = 从头走向导
    frameReady.value = false;
    await refreshCalib();
  } catch (e) { ElMessage.error(e.message); }
}

function goPrev() { if (step.value > 0) step.value--; }
function goNext() { if (step.value < 3) step.value++; }

onMounted(async () => {
  store.on(onState);
  try { await store.refresh(); } catch (e) { ElMessage.error(e.message); }
  await refreshCalib();     // GET /api/calib/state 没视频路径也 200 ⇒ 顶栏标签恒可渲染
});
onUnmounted(() => store.off(onState));
</script>

<template>
  <div class="calibbay">
    <div class="topbar">
      <el-input v-model="pathInput" style="width: 360px"
                placeholder="D:\videos\2026-09-18.mp4（直接读本机文件，不复制不上传）"
                @change="onPathChange">
        <template #prefix><el-icon><VideoCamera /></el-icon></template>
      </el-input>
      <el-tag :type="st?.ready ? 'success' : 'info'">{{ st?.ready ? "已标定" : "未标定" }}</el-tag>
    </div>

    <div class="steps-card">
      <el-steps :active="step" align-center finish-status="success">
        <el-step title="① 选视频抽帧" description="填本机路径" />
        <el-step title="② 标关键点" description="筐口两端 + 球 1~3 次" />
        <el-step title="③ 生成并验证" description="看 ROI 预览" />
        <el-step title="④ 微调 HSV" description="滤肤色/杂色" />
      </el-steps>

      <div v-if="step === 0" class="panel"><el-empty description="步骤 ①：抽帧（Task 3 实现）" /></div>
      <div v-if="step === 1" class="panel"><el-empty description="步骤 ②：点关键点（Task 4 实现）" /></div>
      <div v-if="step === 2" class="panel"><el-empty description="步骤 ③：生成并验证（Task 5 实现）" /></div>
      <div v-if="step === 3" class="panel"><el-empty description="步骤 ④：HSV 微调（Task 6 实现）" /></div>

      <div class="footer-nav">
        <el-button :disabled="step === 0" @click="goPrev">← 上一步</el-button>
        <el-button type="primary" @click="goNext"
                   :disabled="nextDisabled(step, { frameReady, canBuild: st?.canBuild, hasPreview: st?.hasPreview })">
          下一步 →
        </el-button>
      </div>
    </div>

    <div class="steps-card" style="margin-top: 16px">
      <el-empty description="📐 标定段管理（Task 7 实现）" />
    </div>
  </div>
</template>

<style scoped>
.calibbay { max-width: 1060px; margin: 0 auto; padding: 18px 24px 80px; }
.topbar { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
.steps-card { border: 1px solid #3a3a3a; border-radius: 10px; padding: 18px 20px; }
.panel { margin-top: 22px; min-height: 220px; }
.footer-nav { display: flex; justify-content: space-between; margin-top: 18px; }
</style>
```

> ⚠ 底栏的 handler 用 `goPrev()` / `goNext()` 而不是 `step--` / `step++`：`<script setup>` 里对 ref 写自增表达式的编译行为不值得赌一次静默失效；`disabled` 判据（原型冻结的那条表达式）一字不改地搬进 `nextDisabled()`（Task 1），这才是「每步只做当前事」的判据源。

- [ ] **Step 2: 重写 `frontend/src/calib/main.js` 为 17 行标准形状**

```js
import { createApp } from "vue";
import ElementPlus from "element-plus";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import "element-plus/dist/index.css";
import "element-plus/theme-chalk/dark/css-vars.css";
import * as Icons from "@element-plus/icons-vue";
import "../shared/theme.css";
import { mountTabbar } from "../shared/tabbar.js";
import CalibWizard from "./CalibWizard.vue";

mountTabbar("calib.html", "");
const app = createApp(CalibWizard);
// locale：EP 组件默认按钮文案（不装是英文 OK/Cancel——2026-09-20 小任务）。
// e2e 点消息框一律语义选择器（primary / :not(primary)），不按文案选。
app.use(ElementPlus, { locale: zhCn });
for (const [k, c] of Object.entries(Icons)) app.component(k, c);
app.mount("#app");
```

（这把 `:12`/`:13`/`:23` 三处「Plan B」旧口径一并清掉 —— R13；`mountTabbar` 的 hint 从「骨架页——…」改成空串，与 `wall/main.js:11`、`edit/main.js:11` 同形。）

- [ ] **Step 3: 改 `frontend/smoke.mjs` 的标定地标**

把 `:28-30` 的注释换成：

```js
  // 三页地标：剪辑台（Plan B）、出手人墙（Plan C）、标定页（Plan D）都是 SFC 主体、
  // 页内没有 h2 ⇒ 地标一律取各自顶栏里的特征元素。地标只有 app.mount() 真跑成功
  // 才会出现——地标换掉，这条约束不换。
```

把 `:31-34` 的地标表里 calib 那一行改成（`heading` 置 null、换成新 probe）：

```js
        ["calib.html", "标定设置", null, "calibtag"]]) {
```

在 `probe === "input"` 那个分支**之后**、最后的 `else`（墙 tag）分支**之前**插入：

```js
    } else if (probe === "calibtag") {
      // 标定页（Plan D）：顶栏「已标定/未标定」标签只可能由 CalibWizard.vue 渲染
      // （tabbar 是 body.prepend 的、与 Vue 无关）。无会话时它也在（未标定）——
      // GET /api/calib/state 没视频路径也 200 ⇒ 冒烟不依赖会话态。
      const tag = page.locator("#app .topbar .el-tag");
      if (!(await tag.count())) {
        throw new Error(`${file}: Vue 没挂载——#app 里没有 .topbar .el-tag`);
      }
      const t = (await tag.first().textContent()).trim();
      if (t !== "已标定" && t !== "未标定") {
        throw new Error(`${file}: 标定页地标不对，顶栏标签=「${t}」（应为 已标定/未标定）`);
      }
```

- [ ] **Step 4: 写 `tests/e2e/calib.spec.mjs` 地基**

```js
// 标定页级 e2e（Plan D）：真夹具（合成视频可抽帧）+ 真服务 + 真产物。
// 项数口径与另两份 spec 一致：ok() 各 +1、eq() 各 +1（eq 内部调 ok）。
const { chromium } = await import(process.env.BBH_PLAYWRIGHT || "playwright");

const BASE = process.env.BBH_BASE;
const VIDEO = process.env.BBH_VIDEO;
if (!BASE || !VIDEO) throw new Error("需要 BBH_BASE 与 BBH_VIDEO");

let pass = 0;
const ok = (cond, msg) => {
  if (!cond) throw new Error(`❌ ${msg}`);
  pass++;
};
const eq = (a, b, msg) =>
  ok(a === b, `${msg}（实际 ${JSON.stringify(a)} ≠ 期望 ${JSON.stringify(b)}）`);
const calibState = async () => (await fetch(`${BASE}/api/calib/state`)).json();

async function waitFor(fn, msg, ms = 10000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (await fn()) return;
    await new Promise((r) => setTimeout(r, 150));
  }
  throw new Error(`❌ 等待超时：${msg}`);
}

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));

  await page.goto(`${BASE}/calib.html`, { waitUntil: "domcontentloaded" });
  await page.locator("#app .topbar .el-tag").first().waitFor();

  // ① 真挂载（不是骨架）：四步条、顶栏标签、路径输入框都在页内
  eq(await page.locator("#app .el-steps .el-step").count(), 4, "四步向导应恰有 4 步");
  eq((await page.locator("#app .topbar .el-tag").first().textContent()).trim(),
     "未标定", "还没标定时顶栏标签");
  eq(await page.locator("#app .topbar input").count(), 1, "顶栏应恰有一个路径输入框");
  eq(await page.locator("#app .topbar input").first().getAttribute("placeholder"),
     "D:\\videos\\2026-09-18.mp4（直接读本机文件，不复制不上传）", "路径输入框 placeholder");

  // ② 底栏导航的门（步 ① 只认「抽过帧」这条本地量）
  eq((await page.locator("#app .footer-nav button").first().textContent()).trim(),
     "← 上一步", "底栏第一个按钮是上一步");
  ok(await page.locator("#app .footer-nav button").first().isDisabled(), "步 ① 的「上一步」应禁用");
  ok(await page.locator("#app .footer-nav button").nth(1).isDisabled(),
     "还没抽帧时「下一步」应禁用（门 = frameReady）");

  // ③ 服务端基线：还没开会话时 /api/calib/state 也必须 200（顶栏标签靠它）
  const st0 = await calibState();
  eq(st0.ready, false, "夹具应是「还没标定」的初始态");
  eq(st0.points.length, 0, "初始没有点");
  eq(st0.hasPreview, false, "初始没有预览");

  ok(errs.length === 0, `页面报错：${errs.join(" | ")}`);
  console.log(`✅ 全过 ${pass} 项`);
} finally {
  await browser.close();
}
```

（本任务落地 **11 项**。后续任务在此文件内追加断言，收尾的项数用 Step 7 的驱动取真值。）

- [ ] **Step 5: 写 `tests/e2e/test_calib_e2e.py`**

**先打开 `tests/e2e/test_wall_e2e.py` 通读一遍**——本文件是它的同构副本（同样的 `_free_port` / `_wait_up` / `_untouched` / uvicorn 子进程 / 环境变量注入框架）。**照抄它的 fixture 骨架**，只改四处：① 夹具自带真视频（`_make_video`）、② **不预置 `calibration.json`**、③ `SPEC` 指向 `calib.spec.mjs`、④ 副作用的落盘断言。下面给出可用的完整实现；**若 `_untouched` 的签名/用法与 `test_wall_e2e.py` 现文不同，以现文为准**（它是本文件的地基）：

```python
"""标定页级 e2e（Plan D）：自带真视频夹具 + 真服务 + 真产物。

与剪辑台/墙共用的 `_fixture` **不造真视频**（只写 events.json），而标定页要真抽帧
（GET /api/calib/frame.png?t=）⇒ 本文件自带夹具：cv2.VideoWriter 合成
640×360/30fps/6s 的 mp4（暗底 + 灰筐口横条 + 橙球），**且不预置 calibration.json**
——「生成标定」的落盘断言才有牙（夹具自己写的那份会让断言空转）。

形状经实测（2026-09-21）：以 (430,154)/(600,154)/(515,200) 三点生成标定得
hoop_rect=(422,128,187,75)、hsv_low=(5,183,193)、ROI 命中 11083 像素。

隔离机制同另两份 wrapper：uvicorn 用 cwd=临时目录起 ⇒ work/ 落临时目录，
仓库根 6 档开跑前快照、结束逐个比对。Node 或 playwright 缺失 → skip 并说明。
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_edit_bay_e2e import (NAME, NODE, PW, ROOT, _free_port, _untouched,
                               _wait_up)

SPEC = Path(__file__).resolve().parent / "calib.spec.mjs"
W, H, FPS, SECS = 640, 360, 30, 6.0


def _make_video(path: Path) -> None:
    """合成一支能真抽帧的视频：暗底 + 灰筐口横条 + 橙球。"""
    import cv2
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    if not vw.isOpened():
        pytest.skip("cv2.VideoWriter 打不开 mp4v —— 本机造不出夹具视频")
    try:
        for _ in range(int(FPS * SECS)):
            f = np.full((H, W, 3), (40, 40, 60), np.uint8)                  # 暗底(BGR)
            cv2.rectangle(f, (430, 150), (600, 158), (200, 200, 200), -1)   # 筐口横条
            cv2.circle(f, (515, 200), 60, (30, 90, 235), -1)                # 橙球
            vw.write(f)
    finally:
        vw.release()


@pytest.fixture
def calibbay(tmp_path):
    # ⚠ 以下 20 行照抄 test_wall_e2e.py 的 fixture（快照 → 造夹具 → 起服务 → yield →
    #   finally 收尾），只把「造夹具」这一步换成本文件的 _make_video、并去掉
    #   calibration.json 的预置。
    if NODE is None or not PW.exists():
        pytest.skip("没有 node 或 playwright —— 跳过标定 e2e")
    root = tmp_path
    before = _untouched(ROOT)
    video = root / "videos" / f"{NAME}.mp4"
    _make_video(video)
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    log = root / "server.log"
    code = ("import uvicorn\n"
            "from bbh.web.app import create_app\n"
            f"uvicorn.run(create_app(), host='127.0.0.1', port={port}, "
            "log_level='warning')\n")
    with open(log, "w", encoding="utf-8") as fh:
        proc = subprocess.Popen(
            [sys.executable, "-c", code], cwd=root, stdout=fh,
            stderr=subprocess.STDOUT,
            env=dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8"))
    try:
        _wait_up(base, proc, log)
        yield {"base": base, "video": str(video), "root": root}
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        assert _untouched(ROOT) == before, "仓库根的状态档被动了（e2e 没隔离干净）"


def test_calib_e2e(calibbay):
    env = dict(os.environ, BBH_BASE=calibbay["base"], BBH_VIDEO=calibbay["video"],
               BBH_PLAYWRIGHT=PW.as_uri(), PYTHONIOENCODING="utf-8")
    r = subprocess.run([NODE, str(SPEC)], env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    assert r.returncode == 0, f"标定 e2e 失败：\n{r.stdout}\n{r.stderr}"
    assert "✅ 全过" in r.stdout, f"标定 e2e 没跑到收尾汇总：\n{r.stdout}\n{r.stderr}"
    # ⚠ 落盘断言**本任务不写**（E8）：T2–T4 的 spec 走不到「生成标定」（那在 Task 5），
    #   在这里写就跟本任务自己的 `1 passed` 门自相矛盾；由 Task 5 Step 4 在
    #   `assert "✅ 全过"` 之后补上那条 `calibration.json` 存在断言。
```

> ⚠ **本副本与 `test_wall_e2e.py` 的这份 fixture 代码必须在同一轮里自洽**：如果 `_untouched` / `_wait_up` 的签名与上面写的不一致（我按墙 wrapper 的用法写的），以 `test_wall_e2e.py` 的现文逐字为准改本文件，**不要改墙 wrapper**。

- [ ] **Step 6: build 产物，再跑标定 e2e**

```bash
cd frontend && npm run build
cd .. && ./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_calib_e2e.py -q
```

Expected: build 成功；pytest `1 passed`（未跑全量）。

⚠ **build 必须先于 e2e**（服务端从 `bbh/static/` 挂静态产物；不建就是拿旧骨架跑新 spec，症状是 spec 一进页面就超时）。

- [ ] **Step 7: 取标定 spec 的真实项数（一次性内联驱动，**不提交**）**

`count-spec-items.py` 硬编码只跑剪辑台（R15），所以标定侧照墙 43 的老办法，用一段一次性驱动取 `✅ 全过 N 项`：

```bash
./.venv/Scripts/python.exe -X utf8 - <<'PY'
import os, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, "tests/e2e")
from test_calib_e2e import NAME, NODE, PW, ROOT, SPEC, _free_port, _make_video, _wait_up
tmp = Path(tempfile.mkdtemp(prefix="calibcount_"))
video = tmp / "videos" / f"{NAME}.mp4"
_make_video(video)
port = _free_port(); base = f"http://127.0.0.1:{port}"; log = tmp / "server.log"
code = ("import uvicorn\nfrom bbh.web.app import create_app\n"
        f"uvicorn.run(create_app(), host='127.0.0.1', port={port}, log_level='warning')\n")
with open(log, "w", encoding="utf-8") as fh:
    proc = subprocess.Popen([sys.executable, "-c", code], cwd=tmp, stdout=fh,
                            stderr=subprocess.STDOUT,
                            env=dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8"))
try:
    _wait_up(base, proc, log)
    env = dict(os.environ, BBH_BASE=base, BBH_VIDEO=str(video),
               BBH_PLAYWRIGHT=PW.as_uri(), PYTHONIOENCODING="utf-8")
    r = subprocess.run([NODE, str(SPEC)], env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    print(r.stdout[-800:]); print("rc =", r.returncode)
finally:
    proc.terminate(); proc.wait(timeout=10)
PY
```

Expected: `✅ 全过 11 项`、`rc = 0`。**把实际数字写进任务报告**（它是后面每个任务项数链的起点）。

- [ ] **Step 8: 冒烟（三页，含新地标）**

```bash
# 另起一个临时服务（cwd=临时目录，空闲端口），然后：
cd frontend && BBH_BASE=http://127.0.0.1:<port> node smoke.mjs
```

Expected: `✅ 冒烟通过：三页可开、Vue 真挂载、tab 高亮正确、可跳转、无 JS 报错`。
⚠ **若标定页那条先红，说明地标分支写错了**——先看 `#app .topbar .el-tag` 是否真被渲染，别急着改断言。

- [ ] **Step 9: commit**

```bash
git add frontend/src/calib/CalibWizard.vue frontend/src/calib/main.js frontend/smoke.mjs \
        tests/e2e/calib.spec.mjs tests/e2e/test_calib_e2e.py bbh/static
git commit -m "feat(calib): 标定向导页骨架（四步壳/顶栏路径+已未标定标签/底栏门）+ 冒烟地标切真页 + 标定 e2e 地基（自带合成视频夹具）"
```

---

### Task 3: 步骤① 抽帧（`FramePicker.vue`）

**Files:**
- Create: `frontend/src/calib/FramePicker.vue`
- Modify: `frontend/src/calib/CalibWizard.vue`（`step===0` 面板 + 抽帧逻辑）
- Modify: `tests/e2e/calib.spec.mjs`（追加步 ① 断言）

**Interfaces:**
- Consumes：`api`、`shared/time.js` 的 `fmtT`、`shared/md.js` 的 `stripMd`、`calibkit`（Task 1）、`refreshCalib()`（Task 2）。
- Produces（Task 4 要接的对象）：
  - `FramePicker.vue` props：`st` / `frameUrl` / `frameAt`(`Number`) / `frameReady`(`Boolean`) / **`frameStamp`(`Number`，E15)**；emits：`update:frameAt` / `extract` / `frame-loaded`(`{w, h}`)
  - `CalibWizard.vue` 新增 ref：`frameAt`、**`frameStamp`(E15)**、`frameRev`、`natural`、`frameObjUrl`；新增函数：`doExtract()`、`onFrameLoaded({w, h})`
  - ⚠ **E15（执行期裁定，2026-09-21）**：`frameAt` 是**输入框的活值**，`frameStamp` 是**屏上那张帧的时刻**（`doExtract()` 成功时才定格）。标签、补段起点一律取 `frameStamp` —— 否则「改了时间点没重抽帧」和「抽帧失败」两种可达状态下，界面会宣称一个与画面不符的时刻（原型 `:115` 的静态 `2:15` 字面量说明标签语义就是描述紧邻其下的那张帧）。文案逐字不变，只换绑进文案的值。

- [ ] **Step 1: 写 `frontend/src/calib/FramePicker.vue`**

```vue
<script setup>
import { computed } from "vue";
import { fmtT } from "../shared/time.js";

const props = defineProps({
  st: { type: Object, default: null },
  frameUrl: { type: String, default: "" },
  frameAt: { type: Number, default: 0 },
  frameReady: { type: Boolean, default: false },
  frameStamp: { type: Number, default: 0 },   // E15：屏上那张帧的时刻（不是输入框活值）
});
const emit = defineEmits(["update:frameAt", "extract", "frame-loaded"]);

const mmss = computed(() => fmtT(props.frameStamp));    // E15：描述屏上那张帧；格式唯一来源（shared/time.js）

function onAt(v) { emit("update:frameAt", Math.max(0, Number(v) || 0)); }
function onImgLoad(e) {
  emit("frame-loaded", { w: e.target.naturalWidth, h: e.target.naturalHeight });
}
</script>

<template>
  <div>
    <el-alert type="info" :closable="false" show-icon style="margin-bottom: 16px"
              title="标定跟着视频走：每个拍摄机位做一次；同机位的新视频自动复用（指纹匹配）。" />

    <div class="row">
      <el-button size="large" type="primary" plain @click="emit('extract')">抽帧</el-button>
      <span class="tip">时间点（秒，跳到球在筐口附近的画面）</span>
      <el-input-number size="small" :min="0" :step="30" :model-value="frameAt"
                       @update:model-value="onAt" />
      <el-button size="small" @click="emit('extract')">跳到该时间抽帧</el-button>
    </div>

    <div v-if="frameReady" class="frame">
      <div class="label">参考帧 · 视频 {{ mmss }} 处</div>
      <img :src="frameUrl" alt="参考帧" @load="onImgLoad" />
    </div>
    <div v-if="frameReady" class="helper">✅ 帧已就绪——点「下一步」开始标点。</div>
  </div>
</template>

<style scoped>
.row { display: flex; align-items: center; gap: 10px; }
.tip { font-size: 13px; color: #a3a6ad; }
.frame { max-width: 720px; margin-top: 18px; }
.frame .label { font-size: 13px; color: #a3a6ad; margin-bottom: 6px; }
.frame img { width: 100%; display: block; border-radius: 8px; }
.helper { font-size: 13px; color: #67c23a; margin-top: 10px; }
</style>
```

- [ ] **Step 2: 在 `CalibWizard.vue` 里接线（步骤①）**

`<script setup>` 里加 ref 与函数：

```js
import { computed, ref, onMounted, onUnmounted } from "vue";   // computed 是新增的
import FramePicker from "./FramePicker.vue";

const frameAt = ref(0);                       // 抽帧时间点（秒）—— 输入框的活值
const frameStamp = ref(0);                    // E15：屏上那张帧的时刻（成功抽帧时定格）
const frameRev = ref(1);                      // 自增计数：docstring 建议的 ?v= 加固
const natural = ref({ w: 0, h: 0 });          // 帧的真实像素尺寸（<img> @load 拿）
const frameObjUrl = ref("");                  // 帧图的对象 URL（换帧时 revoke）

// 抽帧：**必须**用 fetch 而不是 <img src> —— 抽帧失败时服务端会把 ffmpeg 诊断
// 放进 {error: 文案}（旧界面的核心诊断手段），<img> 的 error 事件拿不到响应体。
// 拿到 blob 后转对象 URL 喂 <img>：一次抽帧只发一个请求（不用 ?v=、不重复下载），
// 也不受浏览器缓存影响（frame.png 虽已发 no-store，对象 URL 从根上不涉及缓存）。
async function doExtract() {
  try {
    const r = await fetch(`/api/calib/frame.png?t=${frameAt.value}&v=${frameRev.value}`);
    if (!r.ok) {
      let msg = `${r.status} ${r.statusText}`;
      try { msg = (await r.json()).error || msg; } catch { /* 非 JSON 兜底 */ }
      throw new Error(msg);
    }
    const blob = await r.blob();
    if (frameObjUrl.value) URL.revokeObjectURL(frameObjUrl.value);
    frameObjUrl.value = URL.createObjectURL(blob);
    frameRev.value++;
    frameStamp.value = frameAt.value;         // E15：定格「屏上这张帧」的时刻（标签 + 补段起点同源）
    natural.value = { w: 0, h: 0 };
    frameReady.value = true;
    await refreshCalib();      // 服务端已清 points / preview（R5）⇒ 回包即权威
  } catch (e) { ElMessage.error(e.message); }
}

function onFrameLoaded(n) { natural.value = n; }
```

`onUnmounted` 里补一行（避免对象 URL 泄漏）：

```js
onUnmounted(() => {
  store.off(onState);
  if (frameObjUrl.value) URL.revokeObjectURL(frameObjUrl.value);
});
```

模板里把 `step===0` 的面板换成：

```html
      <div v-if="step === 0" class="panel">
        <FramePicker :st="st" :frame-url="frameObjUrl" :frame-at="frameAt"
                     :frame-stamp="frameStamp"
                     :frame-ready="frameReady"
                     @update:frame-at="frameAt = $event" @extract="doExtract"
                     @frame-loaded="onFrameLoaded" />
      </div>
```

- [ ] **Step 3: 追加 e2e 断言（步骤①）**

先在 spec 头部加一个 helper（放在 `calibState` 那一行下面）：

```js
const sessionState = async () => (await fetch(`${BASE}/api/state`)).json();
```

再在 `ok(errs.length === 0, ...)` **之前**插入：

```js
  // ③ 步 ①：填路径 → 抽帧失败路径（时间点越界，真视频给真诊断）→ 真抽帧
  await page.locator("#app .topbar input").fill(VIDEO);
  await page.locator("#app .topbar input").press("Enter");
  await waitFor(async () => !!(await sessionState()).videoPath, "会话没登记视频路径");

  const timeIn = page.locator("#app .panel input").first();    // ⚠ 别叫 at：Task 4 在本文件里
  await timeIn.fill("999999");                                 //    另有一个 const at（帧容器点击器）
  await timeIn.press("Enter");
  await page.locator("#app .panel button:has-text('抽帧')").first().click();
  const miss = page.locator(".el-message");
  await miss.first().waitFor();
  ok((await miss.first().textContent()).includes("取帧失败"),
     "越界抽帧应弹服务端的真实诊断（含「取帧失败」）");
  ok(await page.locator("#app .footer-nav button").nth(1).isDisabled(),
     "抽帧失败后「下一步」仍应禁用");

  await timeIn.fill("1.2");
  await timeIn.press("Enter");
  await page.locator("#app .panel button:has-text('抽帧')").first().click();
  await waitFor(async () => (await page.locator("#app .panel img").count()) === 1,
                "抽帧后应出现参考帧图");
  ok(await page.locator("#app .panel img").first().evaluate((el) => el.complete && el.naturalWidth > 0),
     "参考帧图真加载出来了（naturalWidth > 0）");
  eq(await page.locator("#app .panel img").first().evaluate((el) => el.naturalWidth), 640,
     "帧宽应等于夹具视频宽（640）");
  ok((await page.locator("#app .panel").textContent()).includes("帧已就绪"),
     "帧就绪提示应出现");
  ok(!(await page.locator("#app .footer-nav button").nth(1).isDisabled()),
     "抽过帧后「下一步」应变可点（门 = frameReady）");
```

> ⚠ `.el-message` 是 EP 的全局提示（3 秒自动消失），必须在点击后**立刻** `waitFor`，不要插 sleep。
>
> ⚠ **E21（控制者裁定，2026-09-21）**：EP 的提示**节点会残留**——步 ① 那条「取帧失败」实测在后续步骤的断言时刻仍在 DOM 里（Task 4 实施者用 `allTextContents()` 取证）。⇒ **凡断言某条提示，定位器必须按文案过滤**：`page.locator(".el-message").filter({ hasText: "<期望文案>" })`；**不要**用裸 `.el-message` 配 `.first()`（会抓到上一步残留的那条 ⇒ 断言与提示错配），也别用 `.last()`（位置赌注）。**判据**：本步骤若压根没弹那条提示，过滤后的 `waitFor` 必然超时 ⇒ 红；位置选择器在同样情形下可能照样绿。**已实现处不动**：T3 那处当刻只有一条提示、T4 那处已按 E20 改 `.last()` 并过复审；Task 6/7 的正文已按本条改为过滤式。

- [ ] **Step 4: 跑（build → selftest → 标定 e2e）**

```bash
cd frontend && node selftest.mjs && npm run build
cd .. && ./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_calib_e2e.py -q
```

Expected: selftest `✅ 42 通过`；pytest `1 passed`。

- [ ] **Step 5: commit**

```bash
git add frontend/src/calib/FramePicker.vue frontend/src/calib/CalibWizard.vue \
        tests/e2e/calib.spec.mjs bbh/static
git commit -m "feat(calib): 步骤① 抽帧接通 /api/calib/frame.png（fetch+对象URL 保住服务端诊断文案）+ e2e 真视频抽帧与越界报错"
```

---

### Task 4: 步骤② 点关键点（`PointMarker.vue` + 逐点撤销）

**Files:**
- Create: `frontend/src/calib/PointMarker.vue`
- Modify: `frontend/src/calib/CalibWizard.vue`（`step===1` 面板 + 点/撤销/清空）
- Modify: `tests/e2e/calib.spec.mjs`（追加步 ② 断言）

**Interfaces:**
- Consumes：`calibkit` 的 `imgPoint`/`pctOf`/`pointKind`/`ballNo`；`api`；`shared/md.js` 的 `stripMd`；页面 ref `st` / `natural`。
- Produces（Task 5 依赖 `st.points` 已就位）：
  - `PointMarker.vue` props：`st` / `frameUrl` / `natural`；emits：`click-point`(`{x, y}` 帧像素) / `undo`(`i`) / `clear`
  - `CalibWizard.vue` 新增函数：`addPoint(p)` / `undoPoint(i)` / `clearPoints()`

- [ ] **Step 1: 写 `frontend/src/calib/PointMarker.vue`**

```vue
<script setup>
import { computed, ref } from "vue";
import { imgPoint, pctOf, pointKind, ballNo } from "./calibkit.js";
import { stripMd } from "../shared/md.js";

const props = defineProps({
  st: { type: Object, default: null },
  frameUrl: { type: String, default: "" },
  natural: { type: Object, default: () => ({ w: 0, h: 0 }) },
});
const emit = defineEmits(["click-point", "undo", "clear"]);

const framePos = ref(null);          // 光标读数（百分比，只用于画十字与显示）

const pts = computed(() => props.st?.points || []);
const hint = computed(() => stripMd(props.st?.hint || ""));
const W = computed(() => props.natural?.w || 0);
const H = computed(() => props.natural?.h || 0);

function onMove(ev) {
  const r = ev.currentTarget.getBoundingClientRect();
  if (!r.width || !r.height) return;
  framePos.value = {
    x: (ev.clientX - r.left) / r.width * 100,
    y: (ev.clientY - r.top) / r.height * 100,
  };
}
function onClick(ev) {
  emit("click-point", imgPoint(ev.clientX, ev.clientY,
                               ev.currentTarget.getBoundingClientRect(), props.natural));
}
function rowLabel(i) {
  const p = pts.value[i];
  const x = (pctOf(p[0], W.value)).toFixed(1), y = (pctOf(p[1], H.value)).toFixed(1);
  const k = pointKind(i);
  if (k === "hoop-l") return `筐口左端：(${x}%, ${y}%)`;
  if (k === "hoop-r") return `筐口右端：(${x}%, ${y}%)`;
  return `球 ${ballNo(i)}：(${x}%, ${y}%)`;
}
</script>

<template>
  <div class="grid">
    <!-- ⚠ E22（修复轮 1 起，**原计划不是这样写的**）：`.label` 必须在 `.frame-wrap` **之外**。
         `.frame-wrap` 是点击容器，`imgPoint` 拿的是它的 `getBoundingClientRect()` ⇒ 盒里多一行
         标签（~22px）坐标基准就含标签高，上报的帧 y 系统性偏大（实测图内 0.25 处报 103.5 而非
         90，顶部最多 +18px/360）。帧上标记与十字也以 `.frame-wrap` 为定位父级 ⇒
         **它必须恰好等于 `<img>` 的盒**（无 padding/border、img 是唯一子元素）。
         谁把 `.label` 挪回 wrap 里，谁就把这个缺陷带回来。 -->
    <div class="frame-col">
      <div class="label">
        参考帧 · 在图上点击标点（先筐口，后球）
        <template v-if="framePos"> · 光标 ({{ framePos.x.toFixed(1) }}%, {{ framePos.y.toFixed(1) }}%)</template>
      </div>
      <div class="frame-wrap" @click="onClick" @mousemove="onMove" @mouseleave="framePos = null">
        <img :src="frameUrl" alt="参考帧" />
        <template v-if="framePos">
          <div class="cross-v" :style="{ left: framePos.x + '%' }"></div>
          <div class="cross-h" :style="{ top: framePos.y + '%' }"></div>
        </template>
        <div v-for="(p, i) in pts" :key="i" class="pt" :class="pointKind(i)"
             :style="{ left: pctOf(p[0], W) + '%', top: pctOf(p[1], H) + '%' }">
          <span v-if="pointKind(i) === 'ball'" class="idx">{{ ballNo(i) }}</span>
        </div>
      </div>
    </div>

    <div class="side">
      <div class="pts-list">
        <div v-for="(p, i) in pts" :key="'r' + i" class="pt-row">
          <span>{{ rowLabel(i) }}</span>
          <el-button text size="small" type="danger" @click="emit('undo', i)">撤销</el-button>
        </div>
        <div v-if="!pts.length" class="empty">（尚未标点）</div>
      </div>
      <div class="legend"><span class="dot l"></span>筐口左端 / 右端</div>
      <div class="legend"><span class="dot b"></span>球（点 1~3 次）</div>
      <el-button size="small" style="margin-top: 10px" @click="emit('clear')">清空全部</el-button>
    </div>

    <div class="helper">{{ hint }}</div>
  </div>
</template>

<style scoped>
.grid { display: grid; grid-template-columns: 1fr 260px; gap: 18px; }
.frame-col { align-self: start; }                     /* E22：标签的定位/间距归它，不归点击容器 */
.frame-col .label { font-size: 13px; color: #a3a6ad; margin-bottom: 6px; }
.frame-wrap { position: relative; cursor: crosshair; } /* ⚠ 不许有 padding/border/多余子元素：
                                                         它必须与 <img> 的盒逐像素相同（E22） */
.frame-wrap img { width: 100%; display: block; border-radius: 8px; }
.cross-v { position: absolute; top: 0; bottom: 0; width: 1px; background: rgba(255, 255, 255, .55); pointer-events: none; }
.cross-h { position: absolute; left: 0; right: 0; height: 1px; background: rgba(255, 255, 255, .55); pointer-events: none; }
.pt { position: absolute; width: 18px; height: 18px; border-radius: 50%; transform: translate(-50%, -50%);
      display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 700;
      color: #fff; box-shadow: 0 0 0 3px rgba(0, 0, 0, .4); pointer-events: none; }
.pt.hoop-l, .pt.hoop-r { background: #409eff; }
.pt.ball { background: #67c23a; }
.pt .idx { position: absolute; top: -16px; font-size: 11px; color: #9fe290; text-shadow: 0 1px 2px #000; }
.side { font-size: 13.5px; }
.pt-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; }
.empty { color: #8d9098; }
.legend { display: flex; align-items: center; gap: 6px; margin-top: 8px; color: #a3a6ad; }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.dot.l { background: #409eff; }
.dot.b { background: #67c23a; }
.helper { grid-column: 1 / -1; font-size: 13px; color: #e5eaf3; margin-top: 12px; }
</style>
```

- [ ] **Step 2: 在 `CalibWizard.vue` 里接线（步骤②）**

```js
import PointMarker from "./PointMarker.vue";

// 落点：服务端只存坐标（帧像素）。回包是 _meta() ⇒ 整份替换即权威。
async function addPoint(p) {
  // 第 6 点起前端拦（R19）：服务端不拒（docstring 明说「不加校验让点越攒越多」），
  // 原型冻结了这条 UX（「球最多点 3 次，够了」）⇒ 拦在前端才有那声提示。
  if ((st.value?.points || []).length >= 5) {
    ElMessage.info("球最多点 3 次，够了");
    return;
  }
  try { st.value = await api.post("/api/calib/point", { x: p.x, y: p.y }); }
  catch (e) { ElMessage.error(e.message); }
}

// 逐点撤销（R17）：后端只有「清空全部」⇒ DELETE 后按原顺序重发其余点。
// keep 为空时不发 POST，改取 /state 拿权威回包（DELETE 的回包同样可用，但统一走 refres​hCalib 更省心）。
async function undoPoint(i) {
  const keep = (st.value?.points || []).filter((_, k) => k !== i);
  try {
    await api.del("/api/calib/point");
    if (!keep.length) { await refreshCalib(); return; }
    for (const [x, y] of keep) st.value = await api.post("/api/calib/point", { x, y });
  } catch (e) { ElMessage.error(e.message); }
}

async function clearPoints() {
  try { st.value = await api.del("/api/calib/point"); }
  catch (e) { ElMessage.error(e.message); }
}
```

模板里把 `step===1` 的面板换成：

```html
      <div v-if="step === 1" class="panel">
        <PointMarker :st="st" :frame-url="frameObjUrl" :natural="natural"
                     @click-point="addPoint" @undo="undoPoint" @clear="clearPoints" />
      </div>
```

- [ ] **Step 3: 追加 e2e 断言（步骤②）**

在步 ① 断言之后、`ok(errs.length === 0, ...)` 之前插入：

```js
  // ④ 步 ②：点筐口左右 + 球 —— 断言**帧像素**（这一条是 R4 的牙：
  //    若前端误把百分比发上去，points 会是 0.67 这种数，下面立刻红）
  await page.locator("#app .footer-nav button").nth(1).click();      // 进步 ②
  await waitFor(async () => (await page.locator("#app .panel .frame-wrap").count()) === 1,
                "步 ② 应出现点选层");

  const wrap = page.locator("#app .panel .frame-wrap");
  const box = await wrap.boundingBox();
  const at = (fx, fy) => wrap.click({ position: { x: box.width * fx, y: box.height * fy } });
  await at(0.6719, 0.4278);        // 筐口左端 (430,154)
  await at(0.9375, 0.4278);        // 筐口右端 (600,154)
  await at(0.8047, 0.5556);        // 球心 (515,200)

  await waitFor(async () => (await calibState()).points.length === 3, "三点应落到服务端");
  const p3 = await calibState();
  ok(Math.abs(p3.points[0][0] - 430) <= 4 && Math.abs(p3.points[0][1] - 154) <= 4,
     `第一点应是帧像素(430,154)±4，实际 ${JSON.stringify(p3.points[0])}`);
  ok(Math.abs(p3.points[2][0] - 515) <= 4 && Math.abs(p3.points[2][1] - 200) <= 4,
     `第三点应是球心(515,200)±4，实际 ${JSON.stringify(p3.points[2])}`);
  ok(p3.canBuild === true, "三点后服务端应报 canBuild");
  // E19（**必红**，不是防抖——实施期实测两次轮询的首次判定 8/8 全假）：上面 `calibState()` 是
  //   **本进程另发**的一次 HTTP，系统性地比浏览器那条 POST 的回包先落地 ⇒ 轮询一返回就查按钮会撞上
  //   「服务端已有三点、前端还没 flush」。这条 waitFor 只等**同一个渲染**落地，断言本身逐字不变。
  await waitFor(async () => !(await page.locator("#app .footer-nav button").nth(1).isDisabled()),
                "canBuild 落地后「下一步」应变成可点");
  ok(!(await page.locator("#app .footer-nav button").nth(1).isDisabled()),
     "canBuild 后「下一步」应变可点");
  eq(await page.locator("#app .panel .pt").count(), 3, "帧上应有 3 个点标记");
  eq(await page.locator("#app .panel .pt.ball").count(), 1, "其中 1 个是球（绿）");

  // ④b natural 通路（**实施期增补**，非本计划原文）：行文案的百分比 = pctOf(服务端帧像素, natural.w/h)
  //     ⇒ 它与 430±4 一起把 natural 夹死（服务端 430、屏上 67.x% ⇒ w≈640 = 真帧宽）；正则卡「恰好
  //     一位小数」= E5 的牙，锚定整串 ⇒ 顺带钉住 E3（i<2 的行不许出现球号）。
  const rowTxt = (i) => page.locator("#app .panel .pt-row > span").nth(i).textContent();
  const row0 = (await rowTxt(0)).trim(), row2 = (await rowTxt(2)).trim();
  ok(/^筐口左端：\(67\.\d%, 42\.\d%\)$/.test(row0) && /^球 1：\(80\.\d%, 55\.\d%\)$/.test(row2),
     `行文案应是「帧像素 ÷ 真帧尺寸」的百分比、且筐口行无球号：实际 ${JSON.stringify([row0, row2])}`);

  // ⑥ 第 6 点被前端拦（R19）：两点再点两次，服务端点数仍停在 5
  await at(0.55, 0.30);
  await at(0.60, 0.35);
  await waitFor(async () => (await calibState()).points.length === 5, "第 4/5 点应落到服务端");
  await at(0.65, 0.40);
  const capped = page.locator(".el-message");
  // E20（**不许 .first()**，实施期实测改的）：步 ① 那条「取帧失败」的旧 toast 到这刻仍挂在 DOM 上
  //   （两次点击实测相隔 615ms，而 ElMessage 寿命 3s）⇒ `.first()` 命中的是旧的那条、断言恒红。
  //   判据「最新一条 toast 说的是拦截文案」不变；`.last()` 落在空集时抛 TimeoutError 而非返回 ""，
  //   假绿两条路都堵死。
  await waitFor(async () => (await capped.last().textContent()).includes("球最多点 3 次"),
                "第 6 点应被拦并提示（最新一条 toast）");
  ok((await capped.last().textContent()).includes("球最多点 3 次"), "第 6 点应被拦并提示");
  eq((await calibState()).points.length, 5, "第 6 点不许上服务端");

  // ⑦ 逐点撤销（R17：DELETE + 重发）与清空
  await page.locator("#app .panel .pt-row button").first().click();
  await waitFor(async () => (await calibState()).points.length === 4, "撤销后应剩 4 点");
  const after = await calibState();
  ok(Math.abs(after.points[0][0] - 600) <= 4, "撤销第 1 点后，队首应变成原第 2 点(筐口右端)");
  await page.locator("#app .panel button:has-text('清空全部')").click();
  await waitFor(async () => (await calibState()).points.length === 0, "清空后应 0 点");
  await waitFor(async () => await page.locator("#app .footer-nav button").nth(1).isDisabled(),
                "点数不足 3 后「下一步」应重新禁用（等前端 flush）");   // E19 同款
  ok(await page.locator("#app .footer-nav button").nth(1).isDisabled(),
     "点数不足 3 后「下一步」应重新禁用");

  // ⑧ 坐标基准（**E22 修复轮新增，本任务最重要的回归网**）：点击容器必须与 <img> 的盒逐像素相同，
  //     否则上报的帧 y 系统性偏大（修前实测图内 0.25 处报 103.5、真值 90）。这条**用 img 自己的
  //     boundingBox 定位**、与上面 `at()` 的 wrap 基准不同 ⇒ 缺陷不再自洽（修前必红，实测吻合）。
  const img = page.locator("#app .panel .frame-wrap img");
  const ib = await img.boundingBox();
  const nat = await img.evaluate((el) => ({ w: el.naturalWidth, h: el.naturalHeight }));
  await img.click({ position: { x: ib.width * 0.50, y: ib.height * 0.25 } });
  await waitFor(async () => (await calibState()).points.length === 1, "基准那一点应落到服务端");
  const one = (await calibState()).points[0];
  const want = { x: 0.50 * nat.w, y: 0.25 * nat.h };
  // 容差 = max(2 帧px, 1.5 CSS px 折算成帧 px)：量化误差在 CSS 像素上，别用帧像素预算去吃它。
  // 本视口 1 CSS px ≈ 0.865 帧px ⇒ 1.5 CSS px ≈ 1.30 帧px ⇒ 仍是 2/2（不放松）。
  const tolY = Math.max(2, 1.5 * nat.h / ib.height);
  const tolX = Math.max(2, 1.5 * nat.w / ib.width);
  ok(Math.abs(one[0] - want.x) <= tolX && Math.abs(one[1] - want.y) <= tolY,
     `图内(0.50,0.25) 应上报帧像素(${want.x}, ${want.y})±(${tolX}, ${tolY})，实际 ${JSON.stringify(one)}`);

  // ⑨ 单点撤销（**E22 修复轮新增**）：`keep` 为空的分支走 `refreshCalib()` ⇒ 0 点 + 帧上无标记 + 空态文案
  await page.locator("#app .panel .pt-row button").first().click();
  await waitFor(async () => (await calibState()).points.length === 0, "撤销唯一一点后应 0 点");
  await waitFor(async () => (await page.locator("#app .panel .pt").count()) === 0,
                "撤销唯一一点后帧上应无标记（等前端 flush）");
  eq(await page.locator("#app .panel .pt").count(), 0, "撤销唯一一点后帧上应无标记");
  // E21 同形状：裸定位器的 textContent() 在元素缺席时阻塞到 Playwright 默认 30s 抛 TimeoutError，
  //   不是 spec 自己的文案 ⇒ 按**文案过滤** + 本文件的 `waitFor(…, 3000)`；`eq` 比的是**整串**
  //   （`filter` 是子串匹配）⇒ 它不是「过滤器的回声」，文案被改坏照样红。
  const emptyMsg = page.locator("#app .panel .empty").filter({ hasText: "尚未标点" });
  await waitFor(async () => (await emptyMsg.count()) > 0,
                "撤销唯一一点后应回到「（尚未标点）」空态", 3000);
  eq((await emptyMsg.first().textContent()).trim(), "（尚未标点）", "撤销唯一一点后的空态文案");
```

> 📌 **本任务实施期实测**：E7 驱动项数 **17 → 28**（任务书 10 项 + 实施期增补的行文案断言 1 项）→
> **31**（E22 修复轮 +3：⑧ 1 项、⑨ 2 项）。上表的 `Expected: 1 passed` 不变；**项数只有跑 E7 驱动才看得见**。

- [ ] **Step 4: 跑（build → selftest → 标定 e2e）**

```bash
cd frontend && node selftest.mjs && npm run build
cd .. && ./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_calib_e2e.py -q
```

Expected: `✅ 42 通过`；`1 passed`。

- [ ] **Step 5: commit**

```bash
git add frontend/src/calib/PointMarker.vue frontend/src/calib/CalibWizard.vue \
        tests/e2e/calib.spec.mjs bbh/static
git commit -m "feat(calib): 步骤② 点关键点（点击→帧像素换算/十字与标记/逐点撤销/第6点前置拦截）+ e2e 像素级断言"
```

---

### Task 5: 步骤③ 生成并验证（`BuildPanel.vue` + `pendingSeg` 分流）

**Files:**
- Create: `frontend/src/calib/BuildPanel.vue`
- Modify: `frontend/src/calib/CalibWizard.vue`（`step===2` 面板 + `previewSrc` + `doBuild()`）
- Modify: `tests/e2e/calib.spec.mjs`（追加步 ③ 断言）

**Interfaces:**
- Consumes：`api.post`；`shared/md.js` 的 `stripMd`；`shared/time.js` 的 `fmtT`；`calibkit`（Task 1）；页面 ref `st` / **`frameStamp`**（E15，Task 3 已落地）/ `pendingSeg`。
- Produces（Task 6/7 依赖）：
  - `BuildPanel.vue` props：`st` / `previewSrc`(`String`) / `pendingSeg`(`Boolean`) / `frameAt`(`Number`)；emits：`build`（无参）
  - `CalibWizard.vue` 新增：`pendingSeg = ref(false)`、`previewSrc`（computed）、`doBuild()`
  - 面板里的预览图带 `class="preview"`（Task 6 的 HsvPanel 同款 class，e2e 靠 `img.preview` 选它）

- [ ] **Step 1: 写 `frontend/src/calib/BuildPanel.vue`**

> ⚠ 长文案里含 `<视频名>` 这种尖括号，**直接写在模板里会被当成标签**。所以两条长文案进 `<script setup>` 常量再插值（`{{ REF_NOTE }}`）。

```vue
<script setup>
import { computed } from "vue";
import { stripMd } from "../shared/md.js";
import { fmtT } from "../shared/time.js";

const props = defineProps({
  st: { type: Object, default: null },
  previewSrc: { type: String, default: "" },
  pendingSeg: { type: Boolean, default: false },
  frameStamp: { type: Number, default: 0 },   // E15：补段起点的唯一来源（不是输入框活值）
});
const emit = defineEmits(["build"]);

const REF_NOTE = "🗂 参考帧已存档——日后怀疑镜头位移时，可跳到同时刻对照复检（标定随视频走，存 work/<视频名>/）";
const SEG_NOTE = "本次生成的是补充段：写 calibration_segN.json，不动主标定（主标定管 0:00 起的片头段）。";

const msg = computed(() => stripMd(props.st?.msg || ""));
const mmss = computed(() => fmtT(props.frameStamp));    // E15：与帧标签同源
const built = computed(() => !!props.st?.hasPreview);
</script>

<template>
  <div>
    <div class="row">
      <el-button v-if="!pendingSeg" type="primary" size="large" @click="emit('build')">⚙️ 生成标定</el-button>
      <el-button v-else type="warning" size="large" @click="emit('build')">
        ➕ 生成补充段（起点 {{ mmss }}）
      </el-button>
    </div>
    <div v-if="pendingSeg" class="helper warn">{{ SEG_NOTE }}</div>
    <div v-if="!built" class="helper">由标点计算 ROI（检测区域）与色域</div>

    <template v-if="built">
      <img class="preview" :src="previewSrc" alt="ROI 预览" />
      <div class="legend">
        <span><i class="sw g"></i>球色样本命中</span>
        <span><i class="sw b"></i>筐口</span>
        <span><i class="sw r"></i>筐口平面（进球判定线）</span>
        <span><i class="sw y"></i>检测区域 ROI</span>
      </div>
      <el-alert v-if="msg" type="info" :closable="false" class="blk">
        <div class="txt">{{ msg }}</div>
      </el-alert>
      <el-alert type="success" :closable="false" class="blk" :title="REF_NOTE" />
    </template>
  </div>
</template>

<style scoped>
.row { display: flex; align-items: center; gap: 10px; }
.helper { font-size: 13px; color: #a3a6ad; margin-top: 10px; }
.helper.warn { color: #e6a23c; }
.preview { max-width: 720px; width: 100%; display: block; border-radius: 8px; margin-top: 16px; }
.legend { display: flex; flex-wrap: wrap; gap: 16px; font-size: 13px; color: #a3a6ad; margin-top: 10px; }
.sw { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; }
.sw.g { background: #67c23a; } .sw.b { background: #409eff; }
.sw.r { background: #f56c6c; } .sw.y { background: #e6a23c; }
.blk { margin-top: 12px; }
.txt { white-space: pre-wrap; line-height: 1.6; }
</style>
```

- [ ] **Step 2: 在 `CalibWizard.vue` 里接线（步骤③）**

```js
import { computed } from "vue";   // E22 修复轮删掉了 Task 3 遗留的未使用 import（复审 Minor ⑥）⇒ 这里必须补回，否则 previewSrc 编译不过
import BuildPanel from "./BuildPanel.vue";

const pendingSeg = ref(false);        // true = 下一次生成走补段（写 calibration_segN.json）

// 预览图 src：优先用服务端给的 previewUrl（带 rev），退化到裸端点（no-store ⇒ 永远新鲜）。
// previewUrl 不在 _meta() 八键里，任何后续 GET /state 都会把它抹掉（R21）。
const previewSrc = computed(() =>
  st.value?.previewUrl || (st.value?.hasPreview ? "/api/calib/preview.png" : ""));

async function doBuild() {
  try {
    // R6：主标定**必须**发 {}——服务端用 `segT is not None` 分流，发 {segT: 0} 会掉进
    // 补段分支再被 400。补段的起点取**屏上那张帧的时刻**（frameStamp，E15），不是输入框活值。
    const body = pendingSeg.value ? { segT: frameStamp.value } : {};
    st.value = await api.post("/api/calib/build", body);
    if (pendingSeg.value) pendingSeg.value = false;    // 补段已落地 ⇒ 回主模式
  } catch (e) { ElMessage.error(e.message); }          // 失败保持 pendingSeg：改时间点可重试
}
```

模板里把 `step===2` 的面板换成：

```html
      <div v-if="step === 2" class="panel">
        <BuildPanel :st="st" :preview-src="previewSrc" :pending-seg="pendingSeg"
                    :frame-stamp="frameStamp" @build="doBuild" />
      </div>
```

- [ ] **Step 3: 追加 e2e 断言（步骤③）**

> 步 ② 那段最后把点「清空全部」清成 0 了 ⇒ 这一段要先重新点满 3 点。

```js
  // ⑩ 步 ③：生成标定（主标定请求体恒 {}，R6）。简报原文把这一段编号成 ⑧，与本文件既有
  //    的 ⑧（坐标基准）/⑨（单点撤销）撞号 ⇒ 顺延为 ⑩，正文逐字照抄。
  //    步 ② 末尾把点清空了 ⇒ 这一段先重新点满 3 点（3 点前服务端 canBuild=false，
  //    「下一步」也是禁用的）。
  await at(0.6719, 0.4278); await at(0.9375, 0.4278); await at(0.8047, 0.5556);
  await waitFor(async () => (await calibState()).canBuild === true, "重新点满 3 点");
  await page.locator("#app .footer-nav button").nth(1).click();       // 进步 ③
  // E21 同款（实施者实测加的等待，非简报原文）：下面那条 `.click()` 判的正是「步 ③ 有
  //   「生成标定」按钮」，可裸 `.click()` 在按钮缺席时会阻塞到 Playwright 默认 30s 抛
  //   TimeoutError、报错文案不是本 spec 的话（Task 4 刚吃过两次）。改成先用本文件的
  //   waitFor 等按钮出现 ⇒ 失败态 10s 内以本文件自己的 ❌ 文案报出。断言本身逐字不变。
  await waitFor(async () => (await page.locator("#app .panel button:has-text('生成标定')").count()) === 1,
                "步 ③ 面板应有「生成标定」按钮");
  await page.locator("#app .panel button:has-text('生成标定')").click();
  await waitFor(async () => (await page.locator("#app .panel img.preview").count()) === 1,
                "生成后应出现 ROI 预览图");
  // 同款牙（实施者加的等待；**防御性**——复审 6/6 未能复现出这个窄窗）：预览图是会话内存
  //   里现编的 PNG，`<img>` 元素先出现、字节后到，而 `evaluate()` 不等人（它只等元素
  //   attach）⇒ 直接 evaluate 可能撞上「元素在、图还没解码完」。ok 那条判据逐字不动，
  //   只是先等它真的加载完。
  await waitFor(async () => await page.locator("#app .panel img.preview")
                  .evaluate((el) => el.complete && el.naturalWidth > 0),
                "ROI 预览图应真加载出来（naturalWidth > 0）");
  ok(await page.locator("#app .panel img.preview").evaluate((el) => el.complete && el.naturalWidth > 0),
     "ROI 预览图真加载出来了");
  const b = await calibState();
  ok(b.hasPreview === true, "生成后服务端应报 hasPreview");
  ok(b.ready === true, "生成后服务端应报 ready（已落 calibration.json）");
  ok((await page.locator("#app .panel").textContent()).includes("标定已保存"),
     "应显示服务端 msg 原文（含「标定已保存」）");
  ok((await page.locator("#app .panel").textContent()).includes("检测区域 ROI"),
     "图例应含「检测区域 ROI」");
  ok((await page.locator("#app .panel").textContent()).includes("参考帧已存档"),
     "应显示参考帧存档文案");
  eq(await page.locator("#app .topbar .el-tag").textContent(), "已标定",
     "顶栏标签应翻成「已标定」（ready 门）");
  ok(!(await page.locator("#app .footer-nav button").nth(1).isDisabled()),
     "hasPreview 后「下一步」应可点");
```

- [ ] **Step 4: 跑（build → selftest → 标定 e2e）**

```bash
cd frontend && node selftest.mjs && npm run build
cd .. && ./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_calib_e2e.py -q
```

Expected: `✅ 42 通过`；`1 passed`。

> **落盘断言在本步补上（E8）**：Task 2 建 wrapper 时**故意不写**那条断言（T2–T4 的 spec 走不到「生成标定」，写了必红）。现在 spec 真会写 `calibration.json` 了，在 `tests/e2e/test_calib_e2e.py` 的 `assert "✅ 全过" in r.stdout, …` 之后追加：
>
> ```python
>     # 副作用的真落盘：spec 里真抽了帧、真点了筐口、真生成了标定
>     assert (calibbay["root"] / "work" / NAME / "calibration.json").exists(), \
>         "标定没落盘——e2e 没验到真写"
> ```
>
> 追加后重跑本步命令，仍应是 `1 passed`——这条断言**从这一刻起才有牙**（夹具不预置 `calibration.json`，所以只有 spec 真走了「生成标定」它才会绿）。

> 📌 **本任务实施期实测**（交付版 `b0e7474`，复审 spec ✅ / Approved 0C/0I/3M）：
> - **项数链 17 → 28 → 31 → 39**（本任务块恰 **8** 条 `ok/eq`；`waitFor` 不计项）。⚠ 数项数时别用朴素正则：`calib.spec.mjs:15` 的 `eq` **定义体**也匹配，会多算 1（31/39 → 剔掉才是真值）。
> - 12 次仓库外变异实测牙齿：落盘断言**非恒真式**（从 spec 里切掉生成那步 ⇒ 仍 `✅ 全过 31 项` 但 `calibration.json` 不存在）；ready 门 / `st` 替换 / **R6 的 body 恒 `{}`**（改 `{segT:0}` ⇒ 红）都有牙。
> - 两条 `waitFor` 是**时序/诊断修复、不是把断言改软**：同一失败场景下简报原版 33.5s 报 Playwright `TimeoutError`，实现版 13.5s 报本 spec 自己的 ❌。
> - `pendingSeg` 补段分支在本 spec 里**结构性不可达**（全仓无一处把 `pendingSeg` 置 `true`，唯一置真处在 Task 7 的 `addSeg()`）⇒ 补段链路的覆盖归 **Task 7 Step 4 第 ⑪ 段**，见那里的 **E25** 勘误。

- [ ] **Step 5: commit**

```bash
git add frontend/src/calib/BuildPanel.vue frontend/src/calib/CalibWizard.vue \
        tests/e2e/calib.spec.mjs tests/e2e/test_calib_e2e.py bbh/static
git commit -m "feat(calib): 步骤③ 生成并验证（主标定恒 {} / 补段 segT 分流 + 预览与图例 + msg 原样）+ e2e 落盘与 ready 门"
```

---

### Task 6: 步骤④ 微调 HSV（`HsvPanel.vue` + 滑块初值对齐）

**Files:**
- Create: `frontend/src/calib/HsvPanel.vue`
- Modify: `frontend/src/calib/CalibWizard.vue`（`step===3` 面板 + `syncHsv()` + `doRefine()` + `doFinish()`；`goNext` 改 async）
- Modify: `tests/e2e/calib.spec.mjs`（追加步 ④ 断言）

**Interfaces:**
- Consumes：`api.get`/`api.post`；`shared/md.js` 的 `stripMd`；`calibkit` 的 `hsvOf`（Task 1）；页面 ref `st` / `previewSrc`（Task 5）。
- Produces：
  - `HsvPanel.vue` props：`st` / `previewSrc` / `sMin`(`Number`) / `vMin`(`Number`)；emits：`update:sMin` / `update:vMin` / `refine` / `finish`
  - `CalibWizard.vue` 新增：`syncHsv()`、`doRefine()`、`doFinish()`；`goNext()` 变 async（进门步 ④ 时取一次初值）

- [ ] **Step 1: 写 `frontend/src/calib/HsvPanel.vue`**

```vue
<script setup>
import { computed } from "vue";
import { stripMd } from "../shared/md.js";

const props = defineProps({
  st: { type: Object, default: null },
  previewSrc: { type: String, default: "" },
  sMin: { type: Number, default: 86 },
  vMin: { type: Number, default: 60 },
});
const emit = defineEmits(["update:sMin", "update:vMin", "refine", "finish"]);

const msg = computed(() => stripMd(props.st?.msg || ""));
const num = (v) => Math.max(0, Math.min(255, Number(v) || 0));
</script>

<template>
  <div>
    <div class="helper">
      通常第 ③ 步通过就不需要动这里。只有当检测把肤色/黄色地板认成球时，才调大饱和度下限。
    </div>

    <div class="row">
      <span class="lbl">饱和度 S 下限（调大可滤肤色）— 当前 {{ sMin }}</span>
      <el-slider :min="0" :max="255" show-input size="small" :model-value="sMin"
                 @update:model-value="(v) => emit('update:sMin', num(v))" />
    </div>
    <div class="row">
      <span class="lbl">明度 V 下限（调大可滤暗部噪声）— 当前 {{ vMin }}</span>
      <el-slider :min="0" :max="255" show-input size="small" :model-value="vMin"
                 @update:model-value="(v) => emit('update:vMin', num(v))" />
    </div>

    <el-alert type="info" :closable="false" class="blk"
              title="静止绿点（场边广告/灯）会在检测时自动滤除，标定阶段不用管" />

    <img v-if="st?.hasPreview" class="preview" :src="previewSrc" alt="ROI 预览" />
    <el-alert v-if="msg" type="info" :closable="false" class="blk">
      <div class="txt">{{ msg }}</div>
    </el-alert>

    <div class="row btns">
      <el-button @click="emit('refine')">🔄 重算预览</el-button>
      <el-button type="primary" @click="emit('finish')">✓ 完成标定</el-button>
    </div>
  </div>
</template>

<style scoped>
.helper { font-size: 13px; color: #a3a6ad; margin-bottom: 14px; }
.row { display: flex; align-items: center; gap: 16px; margin-bottom: 14px; }
.lbl { width: 260px; flex: none; font-size: 13px; }
.row :deep(.el-slider) { flex: 1; max-width: 520px; }
.row :deep(.el-slider__input) { width: 96px; flex: none; }
.blk { margin-top: 12px; }
.txt { white-space: pre-wrap; line-height: 1.6; }
.preview { max-width: 720px; width: 100%; display: block; border-radius: 8px; margin-top: 14px; }
.btns { margin-top: 16px; }
</style>
```

- [ ] **Step 2: 在 `CalibWizard.vue` 里接线（步骤④）**

```js
import HsvPanel from "./HsvPanel.vue";
import { hsvOf } from "./calibkit.js";     // 与 nextDisabled 同一行 import 里合并也行

const sMin = ref(86);            // 原型默认（R10 的兜底，无标定时用）
const vMin = ref(60);

// 步 ④ 的滑块初值：_meta() 不含 HSV ⇒ 唯一权威源是 GET /existing 的 calibration.hsvLow（R10）。
// **每次**进步 ④ 都重取：生成/补段会按球点重算色域（app.py:220-246），初值必须是刚生成的那份。
async function syncHsv() {
  try {
    const ex = await api.get("/api/calib/existing");
    const h = hsvOf(ex, { s: 86, v: 60 });
    sMin.value = h.s;
    vMin.value = h.v;
  } catch (e) { ElMessage.error(e.message); }
}

async function doRefine() {
  try { st.value = await api.post("/api/calib/refine", { sLow: sMin.value, vLow: vMin.value }); }
  catch (e) { ElMessage.error(e.message); }
}

// 原型冻结：这一步只弹提示，**不发请求**（标定在生成/重算时已落盘）。
function doFinish() {
  ElMessage.success("标定完成！已存 calibration.json —— 可去剪辑台开始检测");
}
```

`goNext` 改 async 并接上初值同步：

```js
async function goNext() {
  if (step.value >= 3) return;
  step.value += 1;
  if (step.value === 3) await syncHsv();    // 进门步 ④ 才取初值（省一次请求）
}
```

模板里把 `step===3` 的面板换成：

```html
      <div v-if="step === 3" class="panel">
        <HsvPanel :st="st" :preview-src="previewSrc" :s-min="sMin" :v-min="vMin"
                  @update:s-min="sMin = $event" @update:v-min="vMin = $event"
                  @refine="doRefine" @finish="doFinish" />
      </div>
```

- [ ] **Step 3: 追加 e2e 断言（步骤④）**

先在 spec 头部再补一个 helper（Task 7/8 复用；放在 `sessionState` 那行下面）：

```js
const existingState = async () => (await fetch(`${BASE}/api/calib/existing`)).json();
```

再在 `ok(errs.length === 0, ...)` 之前插入：

```js
  // ⑪ 步 ④：滑块初值来自 /existing（R10）；改 S 后重算并回读 /existing（写路径的牙）
  //    （编号勘误 2026-09-21：本块简报原文写 ⑨，但交付版里 ⑨/⑩ 已分别被「单点撤销」「步 ③」
  //     占用 ⇒ 从本块起重新编号并**一路顺延到计划末尾**：T6 步④=⑪、T6 完成标定=⑫、
  //     T7 段管理=⑬、T8 查看已有标定=⑭。编号只在注释里，新块一律取「当前最大 +1」。）
  await page.locator("#app .footer-nav button").nth(1).click();       // 进步 ④
  await waitFor(async () => (await page.locator("#app .panel .el-slider input").count()) === 2,
                "步 ④ 应有两个滑块（S/V 下限）");
  const ex = await existingState();
  const srvS = ex.calibration.hsvLow[1], srvV = ex.calibration.hsvLow[2];
  ok(srvS !== 86, `夹具球的 S 下限不该恰好等于兜底值 86（实际 ${srvS}）——这条是下一行的分辨力保险`);
  const sIn = page.locator("#app .panel .el-slider input").first();
  const vIn = page.locator("#app .panel .el-slider input").nth(1);
  await waitFor(async () => (await sIn.inputValue()) === String(srvS),
                `S 滑块初值应 = /existing 的 hsvLow[1] = ${srvS}`);
  eq(Number(await vIn.inputValue()), srvV, "V 滑块初值应 = /existing 的 hsvLow[2]");

  await sIn.fill("250"); await sIn.press("Enter");
  await page.locator("#app .panel button:has-text('重算预览')").click();
  await waitFor(async () => (await existingState()).calibration.hsvLow[1] === 250,
                "重算后服务端 S 下限应变 250（证明请求真带上了滑块值）");
  // 📌 E31 回填（交付后，`1d4cf5c`）：原写 `ok((await page.locator("#app .panel").textContent()).includes("250"), …)`
  //   ——两处「假覆盖」：① 读 `.panel` 整块 ⇒ 滑块输入框的 value、点数标签里出现 250 同样会绿；
  //   ② `textContent()` 在元素不存在时抛 Playwright 超时错误 ⇒ 红的是 Playwright 的话，不是本 spec 的。
  //   现盘上（已交付）逐字如下：
  const msgTxt = (await page.locator("#app .panel .el-alert .txt").allTextContents()).join(" ");
  ok(msgTxt.includes("250"), `面板应显示服务端 msg（含新 S 值 250），实际「${msgTxt}」`);
  eq(await page.locator("#app .panel img.preview").count(), 1, "重算后预览图仍在");

  // ⑫ 完成标定：只弹提示、不发请求、不换步
  await page.locator("#app .panel button:has-text('完成标定')").click();
  const done = page.locator(".el-message").filter({ hasText: "标定完成" });   // E21：按文案过滤，别用裸 .first()
  await done.first().waitFor();
  ok((await done.first().textContent()).includes("标定完成"), "「完成标定」应弹提示");
  eq(await page.locator("#app .panel .el-slider").count(), 2, "「完成标定」不该离开步 ④");
```

> ⚠ S 调到 250 会让夹具的球（S≈182+ 起）落在色域外 ⇒ 服务端走「0 命中」分支，msg 里是 `S=250`、**不是** `S 下限 250`。所以上面那条断的是「含 250」而不是任一句式；真正的牙是**回读 `/existing` 等于 250**。

- [ ] **Step 4: 跑（build → selftest → 标定 e2e）**

```bash
cd frontend && node selftest.mjs && npm run build
cd .. && ./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_calib_e2e.py -q
```

Expected: `✅ 42 通过`；`1 passed`。**注意**：这一步会把夹具 work 目录里的 `calibration.json` 的 S 下限改成 250（夹具目录是临时的，不影响仓库）。

- [ ] **Step 5: commit**

```bash
git add frontend/src/calib/HsvPanel.vue frontend/src/calib/CalibWizard.vue \
        tests/e2e/calib.spec.mjs bbh/static
git commit -m "feat(calib): 步骤④ HSV 微调（滑块初值取自 /existing 的 hsvLow[1]/[2]、重算落盘、完成只提示）+ e2e 回读校验"
```

---

### Task 7: 独立段管理卡（`SegmentCard.vue` + 补段全流程）

**Files:**
- Create: `frontend/src/calib/SegmentCard.vue`
- Modify: `frontend/src/calib/CalibWizard.vue`（第二个 `.steps-card` 换真卡 + `addSeg()` / `delSeg()`）
- Modify: `tests/e2e/calib.spec.mjs`（追加段管理断言）
- Modify: `tests/e2e/test_calib_e2e.py`（wrapper 补「主标定两件套真落盘」的文件级断言）

**Interfaces:**
- Consumes：`calibkit` 的 `segDelPath`（Task 1，已 selftest）；`api.del`；`shared/md.js` 的 `stripMd`；页面 ref `st` / `step` / `frameReady` / `pendingSeg`（Task 5）。
- Produces：
  - `SegmentCard.vue` props：`st`、**`pendingSeg`**（`Boolean`，默认 `false`）；emits：`add-seg`（无参）/ `del-seg`（**行下标 i**，不是路径参数）/ **`cancel-seg`**（无参）
  - `CalibWizard.vue` 新增：`addSeg()` / `delSeg(i)` / **`cancelSeg()`**（R20b）
  > 📌 **E31 回填（交付后）**：`pendingSeg` 与 `cancel-seg` 是终审修复轮（`1d4cf5c`）为 R20b 加的。
  > 卡内 `hasMain = !!props.st?.ready` ⇒「➕ 新增补充段」带 `:disabled="!hasMain"`（**入口门**）；
  > 「✖ 取消补段」按钮**放在段卡里而不是步骤③**——这样它的存在本身就是 `pendingSeg` 的目击者（任何步骤都看得见）。
  > 卡内两项都不改本任务的 `add-seg`/`del-seg` 契约。
  - DOM 约定（e2e 靠它）：段行 `class="seg-row"`；`segListMd` 块 `class="segmd"`

- [ ] **Step 1: 写 `frontend/src/calib/SegmentCard.vue`**

```vue
<script setup>
import { computed } from "vue";
import { stripMd } from "../shared/md.js";

const props = defineProps({ st: { type: Object, default: null } });
const emit = defineEmits(["add-seg", "del-seg"]);

const segs = computed(() => props.st?.segments || []);
const segMd = computed(() => stripMd(props.st?.segListMd || ""));
</script>

<template>
  <div>
    <div class="head">
      <div class="title">📐 标定段管理（镜头中途挪动过？为挪动后的画面补一段标定）</div>
      <el-button size="small" @click="emit('add-seg')">➕ 新增补充段</el-button>
    </div>

    <div class="seg-row">
      <span class="dot main"></span>
      <span class="name">主段 · 从 0:00 起 · calibration.json</span>
      <el-tag type="success" size="small">当前生效</el-tag>
    </div>

    <div v-for="(s, i) in segs" :key="i" class="seg-row">
      <span class="dot sub"></span>
      <span class="name">{{ s }}</span>
      <el-button size="small" type="danger" plain @click="emit('del-seg', i)">删除</el-button>
    </div>

    <div v-if="segMd" class="segmd">{{ segMd }}</div>

    <div class="foot">
      段起点规则：新段时刻须 > 0 且晚于现有最后一段（早于已有时段没有意义会被拒绝）；删除段不影响主段。
    </div>
  </div>
</template>

<style scoped>
.head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
.title { font-size: 15px; font-weight: 600; }
.seg-row { display: flex; align-items: center; gap: 10px; padding: 7px 0; font-size: 14px; }
.seg-row .name { flex: 1; }
.dot { width: 10px; height: 10px; border-radius: 50%; flex: none; }
.dot.main { background: #67c23a; }
.dot.sub { background: #409eff; }
.segmd { white-space: pre-wrap; font-size: 13px; color: #a3a6ad; margin: 10px 0 0; line-height: 1.7; }
.foot { font-size: 12.5px; color: #8d9098; margin-top: 14px; line-height: 1.7; }
</style>
```

- [ ] **Step 2: 在 `CalibWizard.vue` 里接线（段管理）**

```js
import SegmentCard from "./SegmentCard.vue";
import { nextDisabled, segDelPath } from "./calibkit.js";    // 合并进 Task 2 那行 import

// 新增补充段：重置向导回步①。**必须**把 frameReady 打回 false —— 补段要用
// 镜头挪动**之后**的新画面重新抽帧（服务端抽帧会一并清点，R5），
// 不然用户会拿主标定那帧的点去补段。
function addSeg() {
  pendingSeg.value = true;
  step.value = 0;
  frameReady.value = false;
  ElMessage.info("回到第①步：抽挪动后画面的帧，重新走一遍标点");
}

// 删补充段：路径参数 = 行下标 + 2（R11，segDelPath 在 calibkit 里，已 selftest）。
// 回包**只有 3 键**（segments / segListMd / msg，R9）⇒ 只覆盖这 3 个键，绝不整份替换。
async function delSeg(i) {
  try {
    const r = await api.del(`/api/calib/segments/${segDelPath(i)}`);
    st.value = { ...st.value, ...r };
    if (r.msg) ElMessage.success(stripMd(r.msg));
  } catch (e) { ElMessage.error(e.message); }
}
```
（`stripMd` 从 `../shared/md.js` import，与 `calibkit` 那行并列。）

模板里把第二个 `.steps-card` 的占位换掉：

```html
    <div class="steps-card" style="margin-top: 16px">
      <SegmentCard :st="st" @add-seg="addSeg" @del-seg="delSeg" />
    </div>
```

- [ ] **Step 3: wrapper 补文件级断言（`tests/e2e/test_calib_e2e.py`）**

在 `test_calib_e2e` 已有断言之后（`returncode` / `✅ 全过` 两条断言之后）补：

```python
    # 主标定两件套真落盘（标定随视频走：落 <临时 root>/work/<视频名>/，**不是仓库根**）
    assert (calibbay["root"] / "work" / NAME / "calibration_ref.png").exists(), \
        "参考帧存档没落盘——标定没走真流程"
```

> ① 路径形状照抄 `test_wall_e2e.py:103`（`wallbay["root"] / "work" / NAME / "manual_groups.json"`）——`calibbay` 的 `root` 是那个**临时**目录（uvicorn `cwd=root`），`NAME` 是视频名/目录名。`calibration.json` 那条断言 Task 5 里已经有了（E8），这里只补参考帧。
> ② **不要**断言 `calibration_seg2.json` **存在**：spec 末尾会把它删掉。补段的「真落盘」与「真删除」由 spec 里 `/existing.files` 的**双向**断言覆盖（Step 4），文件级事实不重复第二份。

- [ ] **Step 4: 追加 e2e 断言（段管理 + 补段全链 + 删除）**

```js
  // ⑬ 段管理：补段全链（新增 → 回步① → 抽帧 → 点 3 点 → 生成补充段）→ 删除
  const card = page.locator("#app .steps-card").nth(1);
  ok((await card.textContent()).includes("标定段管理"), "第二张卡应是段管理卡");
  ok((await card.textContent()).includes("段起点规则"), "脚注应逐字在卡里");
  eq(await card.locator(".seg-row").count(), 1, "此时只应有主段一行");
  ok(!(await existingState()).files.includes("calibration_seg2.json"), "此刻不该有补段文件");

  await card.locator("button:has-text('新增补充段')").click();
  const t1 = page.locator(".el-message").filter({ hasText: "回到第①步" });   // E21：按文案过滤，别用裸 .first()
  // E26：等本文件自己的 waitFor（有界 10s + 红在本 spec 的 ❌ 文案），别用裸 `.first().waitFor()`
  //      （走 Playwright 默认 30s，失败态是 `TimeoutError`）。等长替换，不计项。
  await waitFor(async () => (await t1.count()) > 0, "「新增补充段」应弹提示");
  ok((await t1.first().textContent()).includes("回到第①步"), "「新增补充段」应提示回到第①步");
  eq(await page.locator("#app .panel button:has-text('抽帧')").count(), 1, "应回到步 ①");
  ok(await page.locator("#app .footer-nav button").nth(1).isDisabled(),
     "补段模式未抽帧时「下一步」应禁用（frameReady 被打回 false）");

  const atSeg = page.locator("#app .panel input").first();
  await atSeg.fill("3.6"); await atSeg.press("Enter");
  await page.locator("#app .panel button:has-text('抽帧')").first().click();
  await waitFor(async () => (await page.locator("#app .panel img").count()) === 1, "补段抽帧应出帧图");
  // ⚠ **E25（控制者勘误，2026-09-21）**：这里必须**只改输入框、不重新抽帧**。
  //   补段起点要锚在**屏上那张帧**（`frameStamp`），不是输入框活值（`frameAt`）——这是 E15
  //   那条「屏上那张帧」语义在**第二个调用点**（`doBuild`）上的判别式。若实现写成活值，
  //   Task 5 的 spec 与本 spec **都不会红**（Task 5 那一刻 frameAt === frameStamp === 1.2；
  //   本 spec 修前也是抽完帧就一路走到生成，二者恒等）⇒ 全绿却把错时刻写进
  //   `calibration_segN.json`（屏幕上的「起点」标签与落盘的段起点静默不一致）。
  //   `fill("20")` 后屏上仍是 3.6 那张帧：T3 已用同一手法（改输入框不抽帧）证明过 `fill`+`Enter`
  //   **不**触发抽帧 ⇒ 这里等于把 T3 的判别式搬到 `doBuild` 这个调用点上。
  await atSeg.fill("20"); await atSeg.press("Enter");
  await page.locator("#app .footer-nav button").nth(1).click();      // 步 ②
  await waitFor(async () => (await page.locator("#app .panel .frame-wrap").count()) === 1,
                "步 ② 应出现点选层");
  const w2 = page.locator("#app .panel .frame-wrap");
  const bb = await w2.boundingBox();
  const at2 = (fx, fy) => w2.click({ position: { x: bb.width * fx, y: bb.height * fy } });
  await at2(0.6719, 0.4278); await at2(0.9375, 0.4278); await at2(0.8047, 0.5556);
  await waitFor(async () => (await calibState()).canBuild === true, "补段的三点应落到服务端");
  await page.locator("#app .footer-nav button").nth(1).click();      // 步 ③
  ok((await page.locator("#app .panel button").first().textContent())
       .includes("生成补充段（起点 00:03.6）"),
     "补段态按钮文案的起点须是**屏上那张帧**的时刻（E25：输入框已改成 20，起点仍须 00:03.6）");
  // E26：写端点之后**不要**立刻轮询服务端——先等这次写请求的**响应**回来，再开始轮询。
  const rfBuild = page.waitForResponse((r) => r.url().includes("/api/calib/build"));
  await page.locator("#app .panel button:has-text('生成补充段')").click();
  eq((await rfBuild).status(), 200, "生成补充段应 200——5xx 多半是落盘撞上读句柄（WinError 5）");
  await waitFor(async () => (await calibState()).segments.length === 1, "补段后应出现 1 个补充段");
  const seg = await calibState();
  ok(seg.segments[0].includes("段2（0:04 起）"),
     `补充段串须锚在被抽那张帧（3.6 ⇒「段2（0:04 起）」，服务端 _fmt_mmss），实际 ${seg.segments[0]}`);
  ok((await existingState()).files.includes("calibration_seg2.json"), "补段文件应真落盘（/existing.files 可见）");
  ok((await card.textContent()).includes("段2（"), "段卡应出现补充段行");
  eq(await card.locator(".seg-row").count(), 2, "段卡应两行（主段 + 1 补充段）");

  // E26：同上。删除也是写端点（`del_segment` 走 `os.remove`，Windows 上撞读句柄同样抛 WinError 5）。
  const rfDel = page.waitForResponse((r) => r.request().method() === "DELETE");
  await card.locator(".seg-row button:has-text('删除')").click();
  eq((await rfDel).status(), 200, "删段应 200——5xx 多半是 os.remove 撞上读句柄（WinError 5）");
  await waitFor(async () => (await calibState()).segments.length === 0, "删段后应无补充段");
  const t2 = page.locator(".el-message").filter({ hasText: "已删除" });   // E21：按文案过滤（原为裸 .last()）
  await waitFor(async () => (await t2.count()) > 0, "删段应弹提示");
  ok((await t2.first().textContent()).includes("已删除"), "删段应弹服务端 msg（含「已删除」）");
  ok(!(await existingState()).files.includes("calibration_seg2.json"), "删段应把文件真删掉");
  eq(await card.locator(".seg-row").count(), 1, "删后应只剩主段一行");

  // ⑬b R9「只覆盖 3 键、绝不整份替换」的判别式（复审 Minor 2 裁定修，已交付 `4ba837f`）：
  //     `delSeg` 写成 `st.value = r` 会让 `hasPreview`/`points`/`canBuild` 一起消失 ⇒ 步③ 的预览图
  //     随之不见，而在此之前**四条门全绿**。删段那一刻向导停在步 ③（`addSeg` 打回 0 后本块点了两次
  //     「下一步」）⇒ 只有 `BuildPanel` 在册（`step === 2` 面板），而它渲染 `img.preview` 的唯一条件是
  //     `built = !!st.hasPreview`（`HsvPanel` 那份 `img.preview` 在 `step === 3`，两者互斥）
  //     ⇒ `count ∈ {0,1}` 且 `1 ⟺ hasPreview 活下来了`。
  //     ⚠ 这只是 `hasPreview` **存活的代理量**，探不到 `points`/`canBuild`（文案别替整条 R9 背书）。
  eq(await page.locator("#app .panel img.preview").count(), 1,
     "删段不该弄丢预览图（写成整份替换时 hasPreview 丢失 ⇒ 预览图消失）");
```

> ⚠ 删段后再回步 ③ 的 `pendingSeg` 已经是 false（Task 5 里生成成功就清了）⇒ 按钮仍是 `⚙️ 生成标定`。这一段**不要**再点生成。

> 📌 **E26（控制者勘误，2026-09-21）—— 本块已就地改 4 处（①②③ 见下）+ 1 条控制者指定的清扫项（⑤）：**
>
> **① 内因**：**写端点之后紧跟着轮询服务端 = 与写撞 Windows 文件锁。** `GET /api/calib/state` → `_meta()`
> （`bbh/web/api_calib.py:60-70`）里的 `ready`（`_calib_ready`）与 `segments`（`_seg_choices` → `load_segments`）
> **每次调用都读盘**；而写侧 `Calibration.save` 走 `os.replace`（`bbh/models.py:29`）。**Windows 上目标文件被
> 读句柄打开时 `os.replace` 失败**（`MoveFileEx(REPLACE_EXISTING)` 要求目标可删除，Python `open()` 只给
> `FILE_SHARE_READ|WRITE`）⇒ `PermissionError: [WinError 5]` ⇒ 500 ⇒ 档不变 ⇒ 轮询落空。
> **Task 6 实施期实测：该写法 ~22% 假红**（插桩定案：`REFINE REQ {"sLow":250,…}` → `RES 500` / WinError 5，
> traceback = `api_calib.py:157 → app.py:302 → models.py:29`；同一次跑的**第一次** refine 是 200 ⇒ 请求体无罪）。
> T6 已把「写路径的等待与写**在时间上不重叠**」修进 spec（`calib.spec.mjs:269-272`、`:285-290`，S/V 两段同形）。
> **② 本任务同形状的两处**（生成补充段 `:1772`、删段 `:1781`）——点击**前**挂 `page.waitForResponse(...)`，
> 等响应回来**再**轮询；各加一条 `eq(status, 200)` 把「请求失败」与「值没传上去」**分开报**。
> **判据（原先那几条 `ok`/`eq` 的值）一字未动**，只把「何时开始读」挪到响应之后。
> **③ `t1`/`t2` 的裸 `.first().waitFor()`** 换成本文件自己的 `waitFor`（等长替换，**不计项**）。
> **④ 项数**：本块原有 **15** 条 `ok/eq`，加新 2 条 status ⇒ **17**；E7 累计 `48 → **65**`。
> **⑥ 事后补丁（已交付，见下）**：本块后来又加了 ⑬b（R9 判别式，复审 Minor 2）⇒ 本块 **18** 条、
> E7 累计 **66**（`d8cea94` → 修复轮 `4ba837f`）。**Task 8 的 ⑭ 块再加 5 条 ⇒ 累计 71。**
> 📌 **E31 回填——项数链终值 81**：17 → 28 → 31(T4) → 39(T5) → 45(T6) → 46 → 48(T6 修复) → 66(T7) → 71(T8)
> → **80**（终审修复轮 `1d4cf5c`：删 1 条 + 新增 2 条 + ⑭ 的 `.files` 双通道补 1 条后的净额由实现者独立数出）
> → **81**（⑮ 定向轮 `bd7c008`：删掉 1 条**不可达红** `:447` + 新增 2 条有牙）。
> ⚠ **计数坑**：注释里只要出现 `ok(` 或 `eq(` 的**字面量**，静态计数就会多算（本批实测算出 82 ≠ 81）⇒
> 数项数时两处要剔（`:15` 的 `eq` 定义体 + `:2` 表头注释里的字样），**并且写注释时别引用这两个字面量**。
> ⚠ 这是**我数出来的数**（脚本数的，不是手数的）：**你若数出别的数，按你的报，别凑我的数**——项数链
> 17 → 28 → 31(T4) → 39(T5) → 45(T6) → 46 → 48(T6 修复轮) 里我已经错过两次，实现者的独立计数两次都对。
> **数项数时两处都要剔**：`calib.spec.mjs:15` 的 `eq` **定义体** 1 处 + `:2` 表头注释里的 `ok()`/`eq()` 字样 2 处。
>
> **⑤ 顺手（控制者指定，计入本任务，只为消一条复审 Minor）**：`calib.spec.mjs` 里 T6 那两处写路径的机制注释
> 把**历史实测数字**（「58 跑 11 失败」「~22%」）写死进了源码——那个探针 harness 不在仓库里、**读者无法复现**，
> 于是这句话会随口径变化变成**过期的断言**。**把数字去掉**（保留机制句、保留「曾实测出现过该假红」这层意思，
> 例如写成「实测出现过该假红（插桩定案，见计划 E26）」）⇒ **不引入任何新断言、判据一字不动、项数不变**。
> 在 commit message 里写明这一条是控制者指定的清扫项，免得复审把它当夹带。

- [ ] **Step 5: 跑（build → selftest → 标定 e2e）**

```bash
cd frontend && node selftest.mjs && npm run build
cd .. && ./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_calib_e2e.py -q
```

Expected: `✅ 42 通过`；`1 passed`。

- [ ] **Step 6: commit**

```bash
git add frontend/src/calib/SegmentCard.vue frontend/src/calib/CalibWizard.vue \
        tests/e2e/calib.spec.mjs tests/e2e/test_calib_e2e.py bbh/static
git commit -m "feat(calib): 段管理卡（补段全流程重置向导/删段 i+2/R9 三键合并不是整份替换）+ e2e 补段与删段文件级双向断言"
```

---

### Task 8: 「查看已有标定」摘要弹窗（`ExistingDialog.vue`）

**Files:**
- Create: `frontend/src/calib/ExistingDialog.vue`
- Modify: `frontend/src/calib/CalibWizard.vue`（顶栏按钮 + `v-model`）
- Modify: `tests/e2e/calib.spec.mjs`（追加弹窗断言）

**Interfaces:**
- Consumes：`api.get("/api/calib/existing")`（9 键，**自有形状** ⇒ 只读不写 `st`，R9）；`shared/md.js` 的 `stripMd`；`shared/time.js` 的 `fmtT`。
- Produces：`ExistingDialog.vue` props：`modelValue`(`Boolean`)；emits：`update:modelValue`；`CalibWizard.vue` 新增 `showExisting = ref(false)`。

- [ ] **Step 1: 写 `frontend/src/calib/ExistingDialog.vue`**

```vue
<script setup>
import { ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { api } from "../shared/api.js";
import { stripMd } from "../shared/md.js";
import { fmtT } from "../shared/time.js";

const props = defineProps({ modelValue: { type: Boolean, default: false } });
const emit = defineEmits(["update:modelValue"]);

const data = ref(null);
const err = ref("");

// 每次打开都重取：标定可能在别处刚变过（生成/补段/微调），缓存一份只会显示旧值。
watch(() => props.modelValue, async (open) => {
  if (!open) return;
  err.value = "";
  try { data.value = await api.get("/api/calib/existing"); }
  catch (e) { data.value = null; err.value = e.message; }   // 无视频路径 ⇒ 400
});

// HSV 三数组必须带分量名：裸 join 出来读不出哪个数是 S、哪个是 V
const hsvLine = (arr) => `H ${arr[0]} · S ${arr[1]} · V ${arr[2]}`;
</script>

<template>
  <el-dialog :model-value="modelValue" title="查看已有标定" width="620px"
             @update:model-value="emit('update:modelValue', $event)">
    <div v-if="err" class="err">{{ err }}</div>
    <template v-else-if="data">
      <div class="k">状态：{{ data.ready ? "已标定" : "未标定" }}</div>
      <div class="k">
        参考帧：{{ data.hasReferenceFrame ? `已存档（标定时刻 ${fmtT(data.calibT)}）` : "未存档" }}
      </div>
      <div class="k">标定文件：{{ data.calibPath || "（无）" }}</div>

      <template v-if="data.calibration">
        <div class="k">
          筐口矩形：x={{ data.calibration.hoopRect[0] }} y={{ data.calibration.hoopRect[1] }}
          w={{ data.calibration.hoopRect[2] }} h={{ data.calibration.hoopRect[3] }}（帧
          {{ data.calibration.frameSize[0] }}×{{ data.calibration.frameSize[1] }}）
        </div>
        <div class="k">
          HSV 下限：{{ hsvLine(data.calibration.hsvLow) }}／上限：{{ hsvLine(data.calibration.hsvHigh) }}
        </div>
      </template>
      <div v-else class="k">（还没有标定：先走完向导的「生成标定」）</div>

      <el-alert v-if="data.wideHsvNote" type="warning" :closable="false" class="blk">
        <div class="txt">{{ stripMd(data.wideHsvNote) }}</div>
      </el-alert>

      <div class="k">补充段：{{ data.segments.length ? data.segments.join("、") : "（只有主段）" }}</div>
      <div v-if="data.segListMd" class="segmd">{{ stripMd(data.segListMd) }}</div>

      <div class="k">work 目录标定文件：</div>
      <div class="files">
        <el-tag v-for="f in data.files" :key="f" size="small" class="ftag">{{ f }}</el-tag>
        <span v-if="!data.files.length" class="none">（无）</span>
      </div>
    </template>
    <div v-else class="k">读取中…</div>
  </el-dialog>
</template>

<style scoped>
.k { font-size: 13.5px; line-height: 2; }
.err { color: #f56c6c; font-size: 13.5px; }
.blk { margin: 10px 0; }
.txt { white-space: pre-wrap; line-height: 1.6; }
.segmd { white-space: pre-wrap; font-size: 13px; color: #a3a6ad; line-height: 1.7; }
.files { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
.ftag { font-family: Consolas, monospace; }
.none { font-size: 13px; color: #8d9098; }
</style>
```

> ⚠ HSV 那行故意用 `hsvLow`／`hsvHigh` 两个数组各取 `[0]` 位的写法（`H S V` 三数一组），**不要**写成 `hsvLow.join()`：那样读不出哪个数是 S、哪个是 V。e2e 断的是「含 `S <值>`」这个形状。

- [ ] **Step 2: 在 `CalibWizard.vue` 里接线（摘要弹窗）**

```js
import ExistingDialog from "./ExistingDialog.vue";
const showExisting = ref(false);
```

顶栏里（`el-tag` 之后）加**按钮**：

```html
      <el-button type="info" plain size="small" @click="showExisting = true">查看已有标定</el-button>
```

**弹窗挂到模板根**（`.calibbay` 内、第二个 `.steps-card` **之后**、闭合 `</div>` 之前）：

```html
      <ExistingDialog v-model="showExisting" />
```

> 📌 **E28（控制者勘误，2026-09-21）**：弹窗**不**跟按钮一起塞进 `.topbar`。`.topbar` 是
> `display:flex`（`CalibWizard.vue:241`）⇒ `el-dialog` 会成为 flex item；不写 `append-to-body` 时
> EP 把 `.el-overlay`/`.el-dialog` 就地渲染（位置靠 `position:fixed` 兜住，多数情况看不出来），
> 但这是**父容器的布局约束在管一个模态**，且本仓库既有四处弹窗（`edit/GenDialog.vue` /
> `HelpDialog.vue` / `OffsetDialog.vue` / `BulkDeleteDialog.vue`）**一律挂在模板尾部、不在内层块里**
> （`EditBay.vue:612-616`）——照惯例走。判据面**不受影响**：e2e 选的是
> `#app .topbar button:has-text('查看已有标定')`（按钮仍在顶栏）与**全局** `.el-dialog`。
> 另：**不要**为了省事加 `append-to-body`——那会改变既有四处的同类写法口径，本任务无此需要。

- [ ] **Step 3: 追加 e2e 断言（摘要弹窗）**

```js
  // ⑭ 查看已有标定（R2 的新增面）：9 键里挑有分辨力的几处断
  await page.locator("#app .topbar button:has-text('查看已有标定')").click();
  await waitFor(async () => (await page.locator(".el-dialog").count()) === 1, "应弹出摘要弹窗");
  const dlg = page.locator(".el-dialog");
  ok((await dlg.textContent()).includes("已标定"), "弹窗应显示「已标定」");
  ok((await dlg.textContent()).includes("已存档"), "弹窗应显示参考帧已存档");
  // E27：判据从 `includes("250")` **收紧成 `includes("S 250")`**——裸 250 太松：筐口矩形 x/y/w/h、
  //   frameSize、calibT 或 wideHsvNote 里出现 250 同样会绿。`hsvLine` 的输出形状是
  //   `H <h> · S <s> · V <v>`（本任务 Step 1 那段），上限行是 `S 255`/`V 255`，不撞。
  //   ⚠ 250 **不是字面常量**，是 Task 6 块刚写进落盘的值（T6 先 S→250 再 V→120；Task 7 的补段/删段
  //     只动 calibration_seg2.json、不碰主档）⇒ 这条依赖 spec 的**线性顺序**，将来重排段落必须跟着改。
  ok((await dlg.textContent()).includes("S 250"), "弹窗 HSV 行应含 S=250（步 ④ 刚写进去的值）");
  ok((await dlg.textContent()).includes("calibration.json"), "弹窗应列出 work 目录里的标定文件名");
  ok((await dlg.textContent()).includes("（只有主段）"), "删段后弹窗应报「只有主段」");
  await page.locator(".el-dialog__headerbtn").click();
  await waitFor(async () => !(await page.locator(".el-dialog").first().isVisible()), "关闭后弹窗应隐藏");
```

> ⚠ 关闭判定用 `isVisible()`，**不要**断 `.el-dialog` 的 `count()===0`：EP 关闭后 DOM 里可能仍留着节点。

> 📌 **E27（控制者勘误，2026-09-21）**：本块判据已在上文就地收紧（`includes("250")` → `includes("S 250")`，
> 理由与「250 依赖线性顺序」的提醒见块内注释）；项数 **5 条 ⇒ E7 累计 71**（我的数，**以你自己独立数出的为准**）。
> **另有一条控制者指定的顺手改动（计入本任务）**：`Task 7` 交付的 ⑬b 那条断言的**文案**在定向复审里被判
> 「替整条 R9 背书、过度声明」（它只是 `hasPreview` 存活的代理量）⇒ **把那条 `eq` 的第三个参数文案改短**成
> 计划里现在这版：`"删段不该弄丢预览图（写成整份替换时 hasPreview 丢失 ⇒ 预览图消失）"`。
> **只改文案、不改判据、不改项数**；在 commit message 里写明这一条是控制者指定的清扫项，免得复审当夹带。
>
> 📌 **E31 回填（交付后，`1d4cf5c`）**：本块实际交付时比上面多两条，都已落盘：
> ① `eq(await dlg.locator(".files .el-tag").count(), 2, "…只断 textContent 含 calibration.json 是假覆盖：calibPath 那行天然含该子串")`
> ——把「文件清单真渲染」从文本包含改成**结构计数**（`.files` 下必须真有 2 个 `el-tag`）；
> ② 弹窗**壳**出现 ≠ **载荷**到达：原写法等 `.el-dialog` 出现就读 `textContent`，读到的是「读取中…」⇒
> 现盘上加了一条等**数据分支首行**的 `waitFor`（E29）。**教训**：`waitFor(壳)` 之后的断言读的是**旧内容**。

- [ ] **Step 4: 跑（build → selftest → 标定 e2e）**

```bash
cd frontend && node selftest.mjs && npm run build
cd .. && ./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_calib_e2e.py -q
```

Expected: `✅ 42 通过`；`1 passed`。

- [ ] **Step 5: commit**

```bash
git add frontend/src/calib/ExistingDialog.vue frontend/src/calib/CalibWizard.vue \
        tests/e2e/calib.spec.mjs bbh/static
git commit -m "feat(calib): 查看已有标定摘要弹窗（/existing 九键只读渲染，含宽色域提示与文件清单）+ e2e"
```

---

### Task 9: 收尾验证批（零代码改动，只跑与报数）

**Files:** 无（**只读验证**；跑出红就是新任务，不在本任务里改）

**Interfaces:**
- Consumes：前面八个任务交付的全部内容。
- Produces：一份**真值报告**（下面四行数字）交回控制者，用于回填计划正文与台账。

- [ ] **Step 1: 纯 JS 层 + 产物重建幂等**

```bash
cd frontend && node selftest.mjs            # 期望 ✅ 42 通过
npm run build
cd .. && git status --porcelain bbh/static  # 期望空：入库产物 == 源码产物
```

- [ ] **Step 2: 标定 e2e（自带合成视频夹具）**

```bash
./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_calib_e2e.py -q
```

Expected: `1 passed`。

- [ ] **Step 3: 取标定 spec 的**真实**项数（R15 的一次性内联驱动，不入库）**

**重跑 Task 2 Step 7 的那段驱动**（同一份代码，**别另写第二份**——两份必然分叉）。Expected: 尾部出现 `✅ 全过 <N> 项` + `rc = 0`，**把 N 抄进报告**（这是标定 spec 的台账项数）。

- [ ] **Step 4: 三页冒烟**

```bash
# ⚠ **不要**裸跑 `node smoke.mjs`——见 E30①。必须覆盖 BBH_BASE 到**空闲端口**、cwd=**临时目录**。
cd frontend && BBH_BASE=http://127.0.0.1:<空闲端口> node smoke.mjs
```

Expected: 三页全绿（标定行的地标是 `#app .topbar .el-tag`，判定 `已标定/未标定`）。

> 📌 **E30（控制者勘误，2026-09-21，T9 实测后）—— 本步骤三处订正：**
>
> **① 裸跑 `node smoke.mjs` 是错的（也是红线风险）。** `smoke.mjs:19` 的 `BASE` 默认 **`http://127.0.0.1:7860`**
> ——那是**用户自己实例的端口**，而本计划的红线是「用户 7860 **不许出现在任何请求/探活/端口扫描名单里**」。
> T9 实测：裸跑 ⇒ `page.goto: net::ERR_CONNECTION_REFUSED at http://127.0.0.1:7860/edit.html`（该实例此刻无监听）。
> **正确形态是覆盖 `BBH_BASE`**：`smoke.mjs:15` 逐字授权了这条路（「换端口起服务时用 `BBH_BASE` 覆盖，
> **别改这里的默认值**」）⇒ **改调用方，不改 `smoke.mjs` 的默认值**。T9 用空闲端口 59694 + 临时 cwd 补跑：
> `✅ 冒烟通过：三页可开、Vue 真挂载、tab 高亮正确、可跳转、无 JS 报错`，`rc = 0`，且临时 cwd 下**压根没生成 `work/`**。
> ⇒ **裸跑的那次红是环境态（端口没人听），不是页面缺陷**；台账两笔都记。
> **⇒ 纪律（后续计划通用）**：我写的命令里**绝不能**出现「默认目标指向用户端口」的裸命令——默认值是别人的环境态，
> 不是可执行指令。
>
> **② 页面名写错**：实际是 **`calib.html`**（`smoke.mjs:34` + 构建产物 `bbh/static/{edit,wall,calib}.html` 实测），
> 计划原写 `calibration.html`。**无功能后果**（冒烟按**地标选择器**判定，不按文件名），仅口径。
>
> **③ 「`work/**` 一律不得出现」是空判据**（T9 自查发现，很准）：`work/` 被 `.gitignore:3` **整目录忽略**、
> 库内 **0 个受跟踪文件** ⇒ 它被污染成什么样，`git status` **永远不会**显示。有牙的形态是**前后清点比对**：
> 跑之前 `find work -type f -printf '%p %s %T@\n' | sort` 存一份，跑完再取一份，**两份必须逐字相同**。

- [ ] **Step 5: 全量（整批只跑这一次）**

```bash
./.venv/Scripts/python.exe -X utf8 -m pytest -q
```

Expected: **635 passed / 1 skipped**（交付前基线 634/1，本批新增标定 wrapper 1 例 ⇒ 635/1）。

- [ ] **Step 6: 净树**

```bash
git status --porcelain
```

Expected: **只剩 `?? ui_prefs.json`**（**用户自己的**界面状态档 ⇒ 不许 add / 不许删 / 不许"修"）；
控制者的勘误提交（`1b23611` / `9271287` / `3775ad9`）已全部落库 ⇒ 计划文档此刻应当是**干净**的
（原写「只有计划文档未提交」是错的：那份 docs 变动是**控制者**的活，不是执行者的）。
`bbh/static/**` 与仓库根 5 档标定档一律不得出现。
⚠ **`work/**` 无法由本命令判定**（`.gitignore:3` 整目录忽略、库内 0 受跟踪文件）⇒ 按 E30③ 的**前后清点比对**另证，
不要在这条 `git status` 上声称「`work/` 没被碰过」——那是**空判据**。T9 实测：`find work -newermt "2026-09-21 00:00"` = **0 个文件**。

- [ ] **Step 7: 报数（不 commit）**

本任务零代码改动 ⇒ **不需要 commit**。把下面五项交回控制者：

| 项 | 真值 |
|---|---|
| `node selftest.mjs` | |
| 标定 e2e | `1 passed` |
| 标定 spec 项数 N（Step 3） | |
| 冒烟 | 三页绿 |
| 全量 pytest | |

---

## 本计划不做的（承接表）

| 不做的事 | 去处 / 理由 |
|---|---|
| `POST /api/calib/segments {t}` | spec `:126` 列了但实盘不存在（R1）⇒ 不实现、**零后端改动**；功能已由 `POST /build {segT}` 承担 |
| 摘要弹窗里的**编辑**能力（改 HSV、删文件、删段） | 只做只读摘要（R2）；写入口径仍只有向导 + 段卡的「删除」 |
| 步骤 ③ 的独立「宽色域」alert | R20：服务端已把 `_wide_hsv_note` 拼进 `msg`，另造一条要靠前端嗅探服务端文案 |
| `app.py` 的报错文案统一 | R12：与 spec `:81` 冲突，以 spec 为准 |
| `count-spec-items.py` 泛化（加 spec 参数 + wrapper 断项数） | R15 ⇒ **Plan E** 测试加固批 |
| 真机验收 / 跨页 e2e / `run_app.bat` 切 `bbh.web` / vrender 锚实测 | **Plan E** |
| `BasisPanel.vue` 的 `releaseT` 时刻同源缺口 | 上一批 park 给 **Plan E**（按显示面枚举，见 edit-msg 计划） |
| 非有限 `offset`（NaN/±inf）收口 | 上一批 park 给 **Plan E** |

---

## 验证配方（速查）

```bash
# 1) 纯 JS 层
cd frontend && node selftest.mjs            # 期望 ✅ 42 通过
# 2) 产物重建（**必须先于**任何 e2e/冒烟）
cd frontend && npm run build
# 3) 标定 e2e（自带合成视频夹具 + 临时 cwd + 空闲端口）
cd .. && ./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_calib_e2e.py -q
# 4) 三页冒烟（⚠ **必须**覆盖 BBH_BASE 到空闲端口 + cwd=临时目录——裸跑默认指向用户 7860，见 E30①）
cd frontend && BBH_BASE=http://127.0.0.1:<空闲端口> node smoke.mjs
# 5) 剪辑台 spec 项数（仓库工具；硬编码只认剪辑台）
cd .. && ./.venv/Scripts/python.exe -X utf8 tests/e2e/count-spec-items.py
# 6) 全量（收尾只跑一次）
./.venv/Scripts/python.exe -X utf8 -m pytest -q
```

**红线复述**（每条都有前科，别试）：
- 起服务一律 `uvicorn.run(create_app(), host, port)` + **临时 cwd** + 空闲端口；**绝不** `python -m bbh.web`（`__main__` 会把仓库根标定档搬进 `work/`）。
- 仓库根 5 档 `calibration.json` / `calibration_ref.png` / `calibration_seg2.json` / `calibration_ref_seg2.png` / `tags.json` **绝不能动**（`ui_prefs.json` 同性质）。
- 用户自己的 7860 实例：不杀、不占、不进任何探活/端口扫描名单。
- 停后台进程按句柄 `terminate()`，**绝不** `taskkill //IM`。
- 实现者只 `git add` 本任务 brief 点名的路径，**永不** `git add -A` / `git add .`。
- 分支纪律：只 commit 到 `feature/vertical-highlight`，**不擅自 merge / push**。
- 实现者**不许**派子代理；**绝不**并行派两个实现者。

---

## 收尾自查（控制者已跑，随计划存档）

计划成文时我逐条**核过**的事实（不是推断），每条都改过任务书里的措辞：

1. `bbh/app.py:94-119` 的 `extract_frame_at` 返回 **5 元组**（gradio 多输出形状），`api_calib.py:85` 也是 5 元解包。我一开始按 ndarray 写（`AttributeError: 'tuple' object has no attribute 'shape'`）⇒ 已订正进「既有契约」。
2. **R14 的夹具配方实测跑通**：`cv2.VideoWriter(..., mp4v, 30, (640,360))` 合成暗底 + 灰筐口横条 + 橙球 → 真抽帧 → 真生成 ⇒ `hoop_rect=(422,128,187,75)`、`count_roi_hits=11083`、`calibration.json` + `calibration_ref.png` **真落盘**，且 `cwd` 是临时目录（仓库真 `work/` 零接触）。
3. HSV 索引（R10）：`app.py:301` 逐字 `calib.hsv_low = (calib.hsv_low[0], int(s_low), int(v_low))` ⇒ S=`[1]`、V=`[2]`；`calibration.py:15` `HSV_MARGIN = (4, 40, 40)` ⇒ 夹具橙球的 S 下限 ≈182（**不是**兜底 86）⇒ Task 6 的 `srvS !== 86` 是有效保险。
4. `_wide_hsv_note` **已拼进** build / 补段 / refine 的 msg（`app.py:241`、`:281`、`:313` 附近）⇒ R20 不另造宽色域 alert 有据。
5. `_seg_choices`（`app.py:422-427`）只给 `段N（<m:ss> 起）`、`_seg_list_md`（`:408-419`）给「标定分段…重新检测生效」长文 ⇒ **数据里没有「机位描述」** ⇒ R22 不编造、改渲染服务端原文。
6. `del_segment` 的 msg 逐字 `已删除 <m:ss> 起的段——重新「▶ 开始检测」后该时段回退用前一段的几何`（`app.py:470-471`）⇒ Task 7 断「含『已删除』」有效；删除按 `abs(c.calib_t - t0) < 0.05` 匹配 ⇒ 夹具 t=3.6 能真删掉文件。
7. `tests/e2e/count-spec-items.py` **不做静态计数**：它跑 spec、打印 `r.stdout[-1500:]`（含 `✅ 全过 N 项`）⇒ 项数只能从运行取，R15 的驱动照它的形状写。
8. 现有 e2e 夹具 `test_edit_bay_e2e.py::_fixture` **不造真视频**（`video=` 指向不存在的 mp4）⇒ R14 必须自带夹具，且**不能**预置 `calibration.json`（否则 Task 5 的落盘断言空转）。
9. `GET /existing` 的 9 键与 `_meta()` 的 8 键**不重叠**（含 `files` 这个只在 `/existing` 里的键）⇒ R9 的「不整份替换」有实际后果，Task 3 的 `?v=`、Task 5 的 `previewUrl` 都属于同一个坑（R21）。
10. **越界抽帧实测**：`extract_frame_at(vp, 999999)` **真抛 `gr.Error`**，文案 `在 999999 秒处取帧失败。无法打开视频：…\nffmpeg 探测：…` ⇒ Task 3 那条「越界抽帧应弹服务端诊断」的牙**有货**；同一次实测里 `t=1.2` 返回 5 元组、帧 shape `(360, 640, 3)` ⇒ Task 3 断 `naturalWidth === 640` 有效。
11. **补段全链实测**（临时 cwd，仓库 `work/` 逐字节零接触）：主标定 ⇒ `hsv_low=(5,183,193)`（⇒ Task 6 的 `srvS !== 86` 成立）、`load_segments` 主段 `t0=[0.0]`（⇒ 补段 3.6s 能过 `>0` 与「晚于最后一段」两道门）；补段 ⇒ msg `✅ 新段已存 calibration_seg2.json（0:04 起），ROI 内命中 11081 像素。…`、`_seg_choices` 变 `['段2（0:04 起）']`、work 目录 4 个文件（`calibration.json` / `calibration_ref.png` / `calibration_ref_seg2.png` / `calibration_seg2.json`）⇒ Task 7 的 `files` 双向牙与 `.seg-row` 计数有效。
    注意 `_fmt_mmss(3.6)` = **`0:04`**（`app.py:403-405` 用 `int(round(t))` 取整到秒）⇒ 段串是 `段2（0:04 起）`，**不是** `0:03.6`；Task 7 的 e2e 因此只断 `includes("段2")`，不断具体时刻。
12. `_calib_dir` 返回**相对**路径 `work\<视频名>`（`app.py:341-345` → `_work_dir_for`）⇒ 脚本不 `chdir` 就写进仓库真 `work/`（同 [[work-dir-cwd-trap]] 那个坑，守卫在 `tests/` 层、对一次性脚本不生效）。Task 2 Step 7 / Task 9 的驱动都把 `cwd=tmp` 写死了。

---

## 交付后已知项

（**已由终审回填完毕**，2026-09-21，E31——1–6 是计划期预见项，7–18 是本批交付后从终审报告、
两轮修复轮、T8/T9 的诚实留白里收拢的。**实现者不必处理其中任何一条**。）

---

### 交付态（2026-09-21，本计划收尾时的实测真值）

| 项 | 真值 |
|---|---|
| 分支 / HEAD | `feature/vertical-highlight`，代码终值 `bd7c008` + 本勘误提交（docs-only） |
| **未 merge、未 push** | ✓（用户长期纪律） |
| 全量 pytest | **635 passed / 1 skipped**（4:42；work 写守卫 `非预期拦截 0 次`） |
| `node selftest.mjs` | ✅ 42 通过 |
| 标定 spec 项数 | **81**（`✅ 全过 81 项`；连跑三次绿，无 flake） |
| 标定 e2e wrapper | `1 passed` |
| 三页冒烟 | 绿（**必须**覆盖 `BBH_BASE` 到空闲端口 + 临时 cwd，见 E30①） |
| 工作树 | 只剩 `?? ui_prefs.json`（用户自己的档，红线，不 add／不删／不改） |
| 仓库根 6 档 mtime | 未变 |
| 任务数 | Task 1–9 全部交付；终审 1 Critical / 7 Minor 已裁，两轮修复轮 + 各一轮定向复审结案 |

1. 顶栏「已标定」标签**不跨页实时**：`ready` 只随本页请求刷新，另开一个标签页标定完，本页不会自己变（无轮询）。
2. `frameReady` 是**页内**状态：刷新页面后即使服务端 `frame_rgb` 还在，步骤①也得重新抽帧（`_meta()` 没有帧标志，R8）。这是设计选择。
3. 逐点撤销要发最多 4 个 POST（R17）；点数上限 5（R19）⇒ 肉眼无感。若将来放开点数上限，这条应改成后端单点删除。
4. 段卡的**主段行描述固定为 `calibration.json`**（R22 用真实文件名替原型占位）——标定文件若改名，这行要跟着改。
5. ~~**补段起点 = 生成时输入框的当前值**（R6 用 `frameAt`）~~ → **已由 E15 修掉（2026-09-21）**：`segT` 与帧标签改取 `frameStamp`（成功抽帧时定格），「改了时间点没重抽帧」不再让段界与画面脱钩。原始隐患的形态留档：旧写法下若用户抽帧后又改输入框，`segT` 用的是改后的值（服务端有 `> 0` 且晚于最后一段的守卫，最坏一次 400，不会写错档）。
6. 步骤 ③ 的 msg 与段卡的删段 toast 都源自 `_meta().msg`：同一时刻在屏上最多出现一条 msg（本页没有并发写操作），但如果将来加了并发入口，这个「单 msg 槽」要重新设计。（此条已在第 17 条里加强。）

7. **补段态的入口门与唯一出口（R20b，终审 Critical 的修法）**：无主标定时「➕ 新增补充段」**禁用**；进入补段态后
   段卡上有「✖ 取消补段」（清 `pendingSeg` + **回步 ①** + `frameReady=false`）；换视频路径（`onPathChange`）
   也清 `pendingSeg`。**「回步 ①」不是随手选的**：补段态步 ③ 的 `segT` 锚在屏上那帧（E25），
   只清标志而留在步 ③ 会让「⚙️ 生成标定」拿**镜头挪动之后**的帧与点去重建**主标定** ⇒ 静默错档。
   这条回步①的语义由 spec 的 ⑮ 块守着（取消在**步 ③**发生才与 `addSeg` 的重置可区分）；
   若将来把 `cancelSeg` 重构成别的退出语义，先看 ⑮ 的注释与那两颗牙。

8. **`VideoCamera` 图标无自动化判据**（本批唯一「改了但无判据覆盖」的点）：`main.js` 全局注册
   `@element-plus/icons-vue` 的全部图标（组件零本地 import），**名字写错时的失败形态是图标空白 + `errs` 为空**
   （Vue 的 failed-to-resolve 是 warning 不是 error）⇒ e2e 不会红。**可用的判据**（留给 Plan E 测试加固批）：
   断 `#app .topbar .el-input__prefix svg` 计数 ≥ 1 能捉「图标整个没渲染」，但捉不到「渲染成了另一个图标」。

9. **`segListMd` / `.segmd` 的渲染块零断言 + 服务端两处口径不一致**（终审 M1 + M3）：主标定重建时服务端回
   `segListMd:""`（`bbh/web/api_calib.py:145` + `bbh/app.py:216-247`）而段行照旧在 ⇒ 段卡里那段说明**凭空消失**，
   与 `/existing.segListMd` 非空**自相矛盾**。**前端不修**（红线「`bbh/` 一行不改」；前端补一块只是给服务端口径
   不一致贴创可贴），测试也不断（要先定「服务端应该给什么」）。⇒ 属**后端**待办。

10. **`undoPoint` 无 in-flight 门**（终审 M4）：同一 tick 内双派发「撤销」会交错成 `DELETE→POST A→DELETE→POST B`，
    服务端点序列被写成 `[B,B,C,C]`。终审实测 0/40/80/150/250 ms 的「等回包再点」**全部正确** ⇒ 窗口**窄于人手双击**，
    **有意不上调**。若将来加「一键清空」之类会连续派发的入口，这条要重新评估。

11. **`natural={0,0}` 窗口（终审 M5，未定性）**：`calibkit.js:14`（`natural?.w || w`）+ `FramePicker.vue:20` —— `@load`
    之前 `natural` 是 `{0,0}`，此时点帧容器会**静默按 CSS 盒换算**。终审**未能确定性复现**；
    反证：本 spec 正是「图一出现就点」，81 项全绿 ⇒ 实际窗口比理论窄。⇒ 留给 Plan E 实测。

12. **冒烟不覆盖页面文案回归**（终审 M6，**已驳回为缺陷**）：`smoke.mjs` 的 `heading` 分支对三页**永不可达**
    （三页都传 `null`，`smoke.mjs:28-30` 注释逐字写明了这是设计：三页页内没有 h2 ⇒ 地标一律取顶栏特征元素）。
    死分支属实，但删它要动**三份计划共用**的冒烟骨架，收益极低。`?v=<rev>`（`CalibWizard.vue:36`）不参与缓存键
    属**应用侧无可动缺陷**（抽帧走 blob 对象 URL，缓存本就不在链上）。

13. **两处与原型对照的偏离（一修一留）**：① `#prefix` 图标已按冻结基准改回 `<VideoCamera />`（终审 M7a，
    原实现是 `<Folder />`——**我的计划自相矛盾**：Task 3 代码块写 `Folder`、原型对照 `:114` 写 `VideoCamera`；
    **计划正文已由 E31 订正**，不再是矛盾项）；② 标注点百分比文案保留实现的 `(12.3%, 45.6%)`，
    原型对照里写的是 `(12.3%, 45.6%)%`（**原型笔误**）⇒ **有意保留实现**，不照抄笔误。

14. **项数链终值 81**（详见 Task 6 块的 E31 回填注）：
    17 → 28 → 31(T4) → 39(T5) → 45(T6) → 46 → 48(T6 修复) → 66(T7) → 71(T8) → 80（终审修复轮）→ **81**（⑮ 定向轮）。
    ⚠ **计数坑（实现者实测踩到）**：注释里只要出现 `ok(` 或 `eq(` 字面量，静态计数就会**多算** ⇒
    数项数时两处要剔（`:15` 的 `eq` 定义体 + `:2` 表头注释字样），**并且**写注释时不要引用这两个字面量。

15. **自洽检查的判据升级（终审异议 3，流程教训）**：本计划 Task 2 的自洽检查把 `probe === "calibtag"`
    判 ✓ 时只核了「表与分支名字对得上」，没核「这条断言还有没有判别力」。
    ⇒ **自洽检查的判据从「名字一致」升级为「破坏后会不会红」**（本批有四处假覆盖都是这个漏洞的变体：
    T7 的 R9 判别式、T8 的 `.files` 双通道、E27 的裸 `250`、`:274` 的面板标签）。

16. **等待预算三个口径**（T6/T7 Minor 的合并）：本文件自己的 `waitFor` 默认 **10 s**（`calib.spec.mjs:20`）；
    `:36` 的挂载屏障与四个 `waitForResponse` 走 **Playwright 默认 30 s**（后者**没有** `ms` 参数）。
    **`:36` 明确不换**（超时报 `TimeoutError` 正是「app 根本没挂上」的正确诊断，换 10 s 会造冷启动假红）。
    **Plan E 清扫清单（行号是 2026-09-21 值，**会漂，用前先 grep**）**：
    ① `:69`（T3 的 miss toast）、`:296`（T6 的完成 toast）——**真待换**，等长替换成
    `waitFor(async () => (await X.count()) > 0, "…")`，**不计项**；
    ② `:269`/`:285`/`:348`/`:360` 四处 `waitForResponse`（S、V、build、del）——**待决策**：
    `waitForResponse` 没有 `ms` 参数，要么接受「30 s 是对得上的第二预算」并写进注释，要么包一层 `Promise.race`；
    **换之前必须先在真机跑一次，确认 30 s 不是假绿的来源**。
    ❗ **为什么这不是纯机械清扫**：把 30 s 统一成 10 s 会让「服务端 30 s 才回」从**超时**变成**假绿**
    （等不到响应时 `rfX` 会挂到测试超时为止，而不是给出可读的红）⇒ 任何统一动作**必须先答「超时该报哪句话」**。

17. **`pendingSeg` 的 msg 单槽**（第 6 条的加强）：步骤③的 msg 与段卡的删段 toast 都源自 `_meta().msg`；
    同一时刻屏上最多一条 msg（本页无并发写）。若将来加并发入口（例如「一键重建全部段」），这个单槽要重新设计。

18. **⑮(4) 的守卫是子串匹配（终审修复轮 2 的定向复审新发现，**有意 park**）**：该块等新 toast 用的是
    `page.locator(".el-message").filter({ hasText: "回到第①步" })`，而**前一条** toast（取消补段）
    的文案 `已取消补段，回到第①步` **也含这个子串**，且 3 s 内仍在屏上 ⇒ 守卫可能在点击**之前**就为真。
    实测：把第三次 `addSeg` 改成不置 `pendingSeg` 时，红点**从守卫位移到 `:494`** ⇒ **无假绿**（后面的断言各报各的话），
    只有「红点归属」变差 + 反向 flake 风险。**修法（一行，留给 Plan E）**：把守卫的过滤串换成**只属于新 toast** 的
    更长前缀（新 toast 形如「回到第①步：抽挪动后画面的帧，重新走一遍标点」，取 `回到第①步：` 即可），
    或先等 `t3.count() === 0`。**为什么 park**：判据判别力未丢、无假绿，而再开一轮修复的收益低于停轮成本。

19. **⚠ 产品侧锁不对称（本批最该被单独记账的一条，**不属本计划**）**：读侧 `GET /api/calib/existing`
    （`bbh/web/api_calib.py:184`）**不取** `video_lock`；写侧 `Calibration.save` 的 `os.replace`
    （`bbh/models.py:29`）在 Windows 上撞读句柄会抛 `PermissionError: [WinError 5]` 且**不重试**
    ⇒ 「写端点之后紧跟着读端点」在 Windows 上是**概率性 500**（Task 6 实施期实测 ~22% 假红，插桩定案）。
    spec 侧已用 `waitForResponse` 把「等待与写不重叠」修掉（E26），**产品侧未修**。
    ⇒ **真实用户点「生成标定 → 立刻打开摘要弹窗」也可能碰到 500**。修它需要**读侧加锁**或**写侧重试**，
    属后端改动 ⇒ **另开任务**（不在 Plan D/E 范围）。**这条是给后端排期的，不是给前端的。**
