# 前端界面重构（Vue3 + Element Plus + FastAPI）设计

日期：2026-09-16 起，2026-09-19 定稿。上游：`前端原型/` 三页可交互原型（用户逐轮审核并裁决，2026-09-19 原型获批）。

状态：**设计已定稿，待写实施计划**。原型阶段不写实现代码的 HARD-GATE 已由用户解除。

## 背景与问题

现行界面是 `bbh/app.py`（2729 行）里的一套 Gradio Blocks，三个 Tab：剪辑台、🧍 出手人、⚙ 标定设置。检测与判定链路（`detector` / `goal_decider` / `net_motion` / `motion` / `shooter` / `editor` / `vrender` / `calibration` / `manual_groups` / `pipeline`）已稳定，问题集中**只在界面层**。用户圈定的四个痛点：

| 痛点 | 现状 | 重构改点 |
|---|---|---|
| 逐行复核流程 | 鼠标点行 → 点裁决按钮 → 点下一行，来回跑 | 键盘流复核：`↑↓` 换行、`空格` 勾选进片并自动跳下一行、`X` 误判反馈、`D` 删除、`1-9` 打标、`Ctrl+Z` 撤销、`J/K/L` 播放键、全场时间轴刻度直达 |
| 预览播放器 | 手写 HTML 塞在 `gr.HTML` 里，控制有限 | 独立播放器组件：分色进度条 + 逐帧步进 + 0.5×/1×/2× + 循环；判定依据卡常驻右侧随行联动 |
| 出手人归人墙 | 6 列 130px 小图看不清，已归/未归两墙堆叠 | 大卡片（≥200px）+ 多选托盘 + 拖到人名组直接归人 + 已归可拖回未归 + 按人折叠 |
| 标定设置流程 | 全部功能挤一个 Tab，首次使用要读说明书 | 四步向导（el-steps），每步只显示当前该做的事；补充段/删段独立管理卡 |

## 目标与非目标

**目标**：把三页 Gradio 界面 1:1 平移为 Vue3 + Element Plus 单页应用，业务逻辑一行不改，`work/` sidecar 格式一字不改，可通过改 `run_app.bat` 一行回退到旧界面。

**非目标**：不动检测/判定算法与其调参；不动 `work/` 数据格式；不做多用户/权限/远程访问；不做浅色主题；不做移动端适配（本机桌面工具）。

## 拍板记录

| 日期 | 决策点 | 结论 |
|---|---|---|
| 09-16 | 技术栈 | Vue3 + Element Plus + FastAPI（方案 A 获批） |
| 09-16 | 业务逻辑边界 | `bbh/` Python 业务逻辑与 `work/` sidecar 格式不动；FastAPI 路由层新写 |
| 09-16 | 前端产物 | 构建产物进 git；Node 仅开发期使用，用户机器只需 Python |
| 09-17 | 标定上传 | 去掉「拖入参考视频，或点此选择文件」拖拽上传区，只留填本机路径 |
| 09-17/18 | 信息密度 | 剪辑台置信度算式/网区信号/判定结论/完整判定数据全部收进档位右侧 ⓘ 悬浮卡，常显只剩「时间＋档位＋ⓘ」 |
| 09-18 | 界面减负 | 页脚快捷键提示删除（并入 ⌨ 帮助，含 1-9 标签名清单）；播放窗口 44vh→52vh；右侧按钮分两排、✅/❌ 最突出、其余三个统一规格 |
| 09-19 | 不滚动即可操作 | 右栏两排操作按钮上移到判定依据正下方，🧍 出手人 GIF 模块下移到最底 |
| 09-19 | 顶部导航 | 三页共用固定 tab 条（sticky top:0，高 40px），各页顶栏里的跳转按钮与 logo 删除；「开始检测」与「识别出手人」并排；「横屏/竖屏集锦」统一成按钮组 |
| 09-19 | 旧界面处置 | **保留可回退**：Gradio UI 代码原样留在 `bbh/app.py` 不删，`run_app.bat` 切到新入口；回退=改回 bat 一行 |
| 09-19 | 进度推送 | **轮询 job 状态**（非 SSE）：POST 启动拿 job_id，前端 0.8~1s GET `/api/jobs/{id}` |
| 09-19 | 目录布局 | 源码 `frontend/`（Vite 工程），产物 `bbh/static/`（进 git，`Path(__file__).parent` 定位，不依赖启动 cwd） |

## 架构

### 进程与入口

- 单进程 uvicorn，新入口 `bbh/web.py`（FastAPI app），绑定 `127.0.0.1:7860`。
- `run_app.bat` 只改一行：`.venv\Scripts\python -m bbh.app` → `-m bbh.web`。
- 回退：把这一行改回去，旧 Gradio 界面立刻可用（`build_app()` 与其 gr.* 接线保持可运行）。
- `requirements.txt` 显式加 `fastapi` 与 `uvicorn`（现在靠 gradio 间接带入，回退删除 gradio 时不能断）。

### 目录布局

```
frontend/                  # Vite + Vue3 工程源码（开发期）
  edit.html  wall.html  calib.html
  vite.config.js           # base:"/static/"  build.outDir:"../bbh/static"  emptyOutDir:true
  src/shared/  api.js  jobs.js  hotkeys.js  theme.css  session.js
  src/edit/    EditBay.vue + 子组件
  src/wall/    ShooterWall.vue + 子组件
  src/calib/   CalibWizard.vue + 子组件
bbh/static/                # 构建产物（进 git）：三个 html + assets/
bbh/web/                   # FastAPI 路由层（新写）
  __init__.py  app.py（create_app）
  api_session.py  api_events.py  api_wall.py  api_calib.py  api_gen.py
  jobs.py（job 表 + 单飞锁）  media.py（媒体与静态路由）
前端原型/                   # 原型档案，原封不动保留（设计依据）
```

### 静态与媒体路由

| 路由 | 行为 |
|---|---|
| `GET /` `GET /edit.html` `GET /wall.html` `GET /calib.html` | 返回 `static/` 下对应 html（多页构建，tab 是普通 `<a href>`） |
| `GET /static/*` | 静态资源 |
| `GET /media/video?path=…` | `FileResponse`（Starlette 自带 Range），**只允许当前会话已登记的视频路径**，过 `clean_path` |
| `GET /media/work?path=…` | work/ 白名单（`_work_dir_for` 派生目录内），语义等同现有 `_file_url`，带 `?v=mtime` 击穿浏览器缓存 |
| `GET /media/clip?path=…&t=…&before=…&after=…` | 复用现有 `_cut_preview`，**保留服务端切片**：逐帧步进（1 帧 = 1/30s，帧率由后端探测下发）与默认 2× 依赖切好的片段，浏览器直读原视频做不到帧级精确 seek |

### 复用边界

- **一行不改**：`bbh/` 下除 `app.py` 的 UI 接线外全部模块；`app.py` 里的纯函数（`load_events_table`、`mark_verdict`、`assign_person`、`build_calibration`、`gen_selected`、`_cut_preview`、`_save_*`、`_load_*`、`probe_existing`、`refresh_and_autoload` 等）由路由层直接 import 调用。
- **不动 `build_app()`**：`app.py` 的 Gradio 接线保持原样可运行。
- **不改 app.py 里的报错文案**：六处「请先上传视频…」（`app.py:98/187/223/255/299/435`）保持原样，新路由层自己返回「请先填路径…」——回退到旧界面时零影响。
- **`_jump`（`app.py:2692-2693`）的 `uploaded or path_str` 收成 `path_str`** 属于旧界面内部逻辑，本次不动（旧界面保留上传能力不影响回退）。

## API 契约

写侧从现在的「整表回传 + `sync_table_edits`」改为**细粒度 REST**：一次用户操作 = 一个请求，后端读-改-写 sidecar，返回**权威行 + 页脚汇总**，前端就地替换、不自己算数字。

### 会话

| 端点 | 语义 | 现状对应 |
|---|---|---|
| `POST /api/session {videoPath}` | 清路径 + 探测已有结果 + 自动载入（行数、标定状态、归人状态） | `probe_existing` + `refresh_and_autoload` |
| `GET /api/state` | 当前视频的 rows、tags 库、selection、flags、feedback 汇总、标定段列表、是否有成品 | `load_table_for_ui` + `_table_summary_md` + `build_goal_stats` |

### 事件与复核

| 端点 | 语义 | 现状对应 |
|---|---|---|
| `GET /api/events` | 行数据（含每行的 `ev` 证据字段、`net`、`shooter`、flags） | `load_events_table` |
| `PATCH /api/events/{key}` | `{selection}` / `{tag}` / `{pre}` / `{post}`，打标自动勾选进片 | `on_table_edit` / `set_row_tag` / `apply_all_rows` |
| `POST /api/events/miss {t}` | 漏检补录（纯秒数） | `record_miss` |
| `DELETE /api/events {keys[]}` | 批量删除（单条走同端点传单元素） | `delete_events` / `delete_event` |
| `POST /api/events/restore` | 恢复已删 | `restore_deleted` |
| `POST /api/feedback/{key} {verdict}` | ✅翻案 / ❌否决，写 `feedback.json` 并取消/保持进片 | `mark_verdict` |
| `POST /api/flags/{key} {thumb\|cross}` | 缩略图有误 / 十字有误 标记 | `toggle_thumb_flag` / `toggle_cross_flag` |
| `POST /api/tags {name}` | 标签库新增 | `add_tag_to_library` |

### 出手人墙

| 端点 | 语义 | 现状对应 |
|---|---|---|
| `GET /api/wall?sort=time\|appearance\|spot` | 未归 + 已归（按人分组）两栏数据 | `wall_payload` / `assigned_wall_payload` |
| `POST /api/wall/assign {person, keys[]}` | 归人（含拖拽与托盘两条路径） | `assign_person` |
| `POST /api/wall/unassign {keys[]}` | 移出该人回未归 | `unassign_person` |
| `POST /api/wall/scan` → job | 回扫溯源（识别出手人） | `run_shooter_tracing` |
| `GET /api/wall/guard {person}` | 归人前护栏：该人已归组整组的球数与列表 | `render_manual_payload` 的护栏分支 |

### 标定

| 端点 | 语义 | 现状对应 |
|---|---|---|
| `GET /api/calib/frame?t=` | 抽帧 PNG + 像素尺寸 + 是否已有标定 | `extract_frame_at` / `_extract_for_ui` |
| `POST /api/calib/point {x, y}` | 累积点击点，返回点列表 + 提示文案 + 是否可构建 | `add_point` / `click_hint` |
| `POST /api/calib/build {segT?}` | 生成标定 + ROI 预览 + 宽色域提示 + 0 命中诊断 | `build_calibration` / `build_segment_calibration` / `_wide_hsv_note` |
| `POST /api/calib/refine {sLow, vLow}` | HSV 微调重算 + 命中像素/判定诊断 | `refine_hsv` |
| `GET /api/calib/segments` · `POST /api/calib/segments {t}` · `DELETE /api/calib/segments/{i}` | 多段标定管理（段起点规则：须 > 0 且晚于现有最后一段） | `load_segments` / `del_segment` / `_seg_list_md` |
| `GET /api/calib/existing` | 查看已有标定（参考帧存档、指纹匹配） | `_calib_ready` / `_load_calib_or_none` |

### 生成与长任务

| 端点 | 语义 | 现状对应 |
|---|---|---|
| `POST /api/gen {vertical, keys[]}` → job | 生成集锦，返回跳过汇报（全部跳过时 error 而非 success） | `open_gen_dialog` / `gen_selected` |
| `POST /api/detect` → job | 检测 | `start_detection` / `_detect_worker` |
| `POST /api/wall/scan` → job | 回扫溯源（job 详情见下节「生成与长任务」） | `run_shooter_tracing` |
| `GET /api/jobs/{id}` | `{phase, pct, found, msg, done, error}` | 取代 Gradio 的生成器 yield |
| `GET /api/jobs/{id}/result` | 完成后取结果（成品文件列表 / 行数据 / 预览 URL） | — |

**job 机制**：内存 dict + 全局锁；同类任务在跑时 `POST` 返回 409（不并发跑）；job 完成保留至被下一次同类任务覆盖；前端 0.8~1s 轮询，页面刷新后按 job_id 继续轮询（进度不因刷新丢失）。

**前置守卫链**：照抄 `app.py` 的四步守卫（检测要有标定、回扫要有 events、生成要有勾选、标定要有帧）。守门失败统一 `400 {msg}`，文案与旧界面一致（六处「请先填路径…」）。

## 前端工程结构

- **Vite 多页构建**，三入口，tab 用普通 `<a href>`；不引 vue-router、不引 Pinia（每页一个独立 Vue app，页内状态用 `reactive` 单例）。
- 依赖只有 `vue` + `element-plus` + `vite`，Element Plus 全量引入（本地工具，省掉按需导入插件）。
- `src/shared/`
  - `api.js`：fetch 封装，非 2xx 统一 `ElMessage.error(msg)` 并抛出；所有写操作返回权威行+汇总。
  - `jobs.js`：`useJob()` —— POST 启动 → 0.8s 轮询 → 进度回调 → 完成/失败回调。
  - `hotkeys.js`：全局 keydown；弹窗打开或输入框聚焦时挂起（对应原型「弹窗模态」验收点）。
  - `theme.css`：橙色主色 #E8622D + 深色底 + tabbar/表格/卡片样式（从原型搬）。
  - `session.js`：当前视频路径，localStorage 记忆。
- 三页组件划分见上文目录；组件内的判定文案与字段口径**必须与原型一致**（置信度算式 0.30＋上方＋下方＋位置、满分 0.90、档位 0.70/0.50、网区阈 5.0、同对局部比门 3×）。

## 落盘与兼容（验收红线）

- `work/` 下所有 sidecar **结构、字段名、文件格式一字不改**：`events.json`、`tags.json`、`deleted.json`、`selection.json`、`assignments.json`、`feedback.json`、`*.gif`、缩略图、成品目录、`calibration.json`。
- 写侧仍是「读整档 → 改 → 写回」，直接复用现有 `_save_*` 函数；只加**每视频一把写锁**（`threading.Lock`），防轮询与写操作交错。
- **不新增任何 sidecar 字段**。UI 偏好（上次视频路径、折叠状态）写独立文件 `ui_prefs.json`，与判定数据隔离。
- **双向兼容验收**：同一 `work/` 目录，新旧界面轮流打开，行数/勾选/标签/裁决/归人/反馈/删除恢复逐项一致；反向用旧界面确认新界面写的档能正常读。
- 路径安全：所有 `path` 参数过 `clean_path` + 白名单（当前会话视频、或 `_work_dir_for` 派生目录内）。越界一律 403。

## 测试与验收

**Python 侧**
- 现有 19 个 `tests/test_*.py` 保持全绿（业务逻辑没动，这是回归基线）。
- 新增 `tests/test_web.py`（`TestClient`，不起浏览器）覆盖：会话载入与自动探测、事件 PATCH 落盘后读回、六处守门文案、路径白名单拒绝（403）、job 生命周期（启动→轮询→完成→重复启动 409）、标定点累积与构建、生成选择过滤与跳过汇报、归人护栏。

**前端**
- `npm run build` 产物进 git（三个 html + assets）。
- 开发期 `vite dev` + `server.proxy` 把 `/api`、`/media` 代理到 7860。
- 端到端脚本**入库** `tests/e2e/`（Node + Playwright），不再像原型期那样放在系统 Temp 目录里。

**浏览器端到端**（照搬现有 `selftest.js` 的 Playwright 思路，但打真端口）
- 三页主流程：填路径 → 载入 12 行 → 键盘流复核（空格/1-9/X/D/Ctrl+Z）→ 归人墙拖拽（含拖回未归）→ 标定抽帧点筐口 → 生成弹窗汇报。
- 用例名与原型自测对齐（`置信度算式自洽`、`依据卡不常显算式`、`顶栏 tab 三页共用条`、`操作按钮排在出手人 chip 之上且不用滚动就能点到` 等），多一层「真后端」。

**真机验收**
- 09-11_3 跑全流程；横屏/竖屏成品与旧界面同参生成的结果比对（条数/顺序/时长/文件名）。
- 确认 `work/` 没有多出意外文件。

## 实施顺序

粗粒度（细粒度步骤进 plan）：

1. 路由层骨架：`bbh/web.py` + `static` 挂载 + media/white-list + `run_app.bat` 切换（此时前端可以是占位页）。
2. 会话 + 事件 CRUD + 写锁 + `tests/test_web.py` 骨架。
3. 剪辑台页（表格/时间轴/播放器/依据卡/按钮/键盘层）——最大一块，先跑通复核闭环。
4. 出手人墙页（两栏/拖拽/托盘/回扫 job）。
5. 标定向导页（四步/点选/构建/微调/多段）。
6. 生成与长任务（job 轮询、横竖屏、跳过汇报、预览）。
7. 端到端 Playwright + 真机验收 + 双向兼容核对。

## 风险与回退

| 风险 | 缓解 |
|---|---|
| 前端重写引入写侧 bug，污染 `work/` | 复用 `_save_*` 不变；写锁；验收做双向兼容核对；真机验收前先备份 09-11_3 的 work 目录 |
| 浏览器直读原视频的兼容性问题 | 本设计保留服务端切片（`/media/clip`），不依赖直读 |
| 大文件/CJK 路径问题 | 路径统一走 `clean_path` + URL 整体编码；`_file_url` 的 `?v=mtime` 语义保留 |
| 旧界面回退路径失效 | `build_app()` 与 `app.py` 文案一律不动；回退验收 = 改一行 bat 后能打开并完成一次检测 |
| 轮询放大写冲突 | 轮询只读；写操作独立端点 + 每视频写锁 |

## 待办与后续（不在本 spec 范围）

- 原型档案 `前端原型/`（含 34MB 演示素材）是否入库，待用户决定。
- 竖屏线未提交改动、`work/` 调试文件清理、09-04_1 GIF 重生成等遗留项与本重构无关，另行处理。
