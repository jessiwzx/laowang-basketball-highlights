# 剪辑台页（共享层定型 + 剪辑台）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Gradio 旧界面的「剪辑台」页 1:1 平移成 Vue3 + Element Plus 的 `bbh/static/edit.html`（含表格、键盘流、播放器、判定依据、四个弹窗、三个长任务），并定型三页共用的共享层。

**Architecture:** 前端源码在 `frontend/`（Vite 多页构建，产物入库到 `bbh/static/`）；页面主体是 `src/edit/EditBay.vue` + 子组件，全部数据来自已交付的 FastAPI（`bbh/web/`，Plan A）——**前端不自己算统计、不自己拼证据文案**，任何写操作回包就地替换整份 `state`。逻辑（时间格式、写操作 + 撤销栈、长任务跟随）放在**纯 JS 模块**里以便用 Node 直测；组件只做展示与接线。

**Tech Stack:** Vue 3（SFC）+ Element Plus 2.8 + Vite 5；测试 = Node 断言脚本（`frontend/selftest.mjs`）+ pytest（后端增量与产物）+ Playwright（页级 e2e，入库 `tests/e2e/`）。

**Spec:** `docs/superpowers/specs/2026-09-16-frontend-refactor-design.md`（**约束权威**，计划是它的论证；冲突一律按 spec 判）

**写计划依据的两份实测清单（执行时先读，比本计划的转述更细）：**
- `.superpowers/sdd/2026-09-19-frontend-refactor-edit-bay/backend-contract.md` —— 35 个端点的精确入参/回包/错误契约（逐行读 `bbh/web/*.py` 得来）
- `.superpowers/sdd/2026-09-19-frontend-refactor-edit-bay/proto-edit-inventory.md` —— `前端原型/剪辑台.html` 的逐块 UI 事实（区域尺寸/表格列/键盘逐键/popover 文案/视觉常量/20 条 mock↔真 API 字段差异）
- `.superpowers/sdd/2026-09-19-frontend-refactor-edit-bay/plan-b-grounding.md` —— 范围切分、spec 硬约束逐字、视觉规范、键盘表、五条用户裁决

## Global Constraints

- **目标**：三页 Gradio 界面 **1:1 平移**为 Vue3 + Element Plus；**业务逻辑一行不改**；`work/` sidecar **格式一字不改**；可通过改 `run_app.bat` **一行回退**到旧界面。
- **非目标**：不动检测/判定算法与调参；不动 `work/` 数据格式；不做多用户/权限/远程；**不做浅色主题**；**不做移动端**。
- **落盘红线**：`work/` 下 sidecar 结构/字段名/格式一字不改；**不新增任何 sidecar 字段**；UI 偏好写独立文件 `ui_prefs.json`（服务端已实现）。
- **文案边界**：**不改 `bbh/app.py` 里的报错文案**——六处「请先上传视频…」（`app.py:98/187/223/255/299/435`）保持原样；新路由层自己返回「请先填路径…」（已实现）。
- **错误契约**：失败响应体一律 `{"error": "中文文案"}`，前端**不改写**文案，一律 `ElMessage.error(e.message)`（`src/shared/api.js` 已实现）。
- **写侧形状**：一次用户操作 = 一个请求；写端点返回**权威行 + 页脚汇总**（`state_payload()`，部分端点追加 `msg`）；**前端就地替换整份 state，不自己算任何统计数字**。
- **视觉**：主色篮球橙 `#E8622D`；🟢 `#67C23A` / 🟡 `#E6A23C` / ⚪ `#909399`；底色 `#141414`；**深色为默认，不做浅色切换**。三页 sticky 40px tab 条（`theme.css` 已实现）。
- **路径安全**：所有 `path` 过 `clean_path` + 白名单，越界 403（**服务端已实现**，前端只负责带对 path）。
- **开发期**：`vite dev` + `server.proxy` 把 `/api`、`/media` 代理到 7860；**`npm run build` 产物进 git**（改前端源码后必须重建）。
- **端到端**：入库 `tests/e2e/`（Node + Playwright），**不再放系统 Temp 目录**。
- **进程**：单进程 uvicorn，绑 `127.0.0.1:7860`。

### 红线（本项目长期纪律，违反代价极高）

- **绝不跑 `python -m bbh.web`**：其 `__main__` 会调 `migrate_root_calibrations()`，把仓库根的标定档搬进 `work/<视频>/`，而根那 5 个档（`calibration.json`/`calibration_ref.png`/`calibration_seg2.json`/`calibration_ref_seg2.png`/`tags.json`）**一个 git 副本都没有**。起服务一律用临时脚本 `uvicorn.run(create_app(), host, port)`。
- **不动用户 `work/` 状态文件**（分析只读）；**不杀、不占用户自己的 7860 实例**，也不把它写进任何探活/端口扫描名单。
- 停后台进程**按 task id**，绝不用宽泛的 `taskkill //IM`。
- 分支纪律：**不擅自 merge / push**。
- **原型测试纪律**：改一处只跑相关用例，**不跑全量回归**（只有本计划最后一个任务跑全量）。
- Python 侧命令固定为 `./.venv/Scripts/python.exe -X utf8 -m pytest -q`；commit 用中文长信息。

## 计划期裁定（本计划内已定，执行时不再重新讨论）

| # | 裁定 | 理由 | 判错代价 |
|---|---|---|---|
| R1 | `index.html:138` 要求改 `app.py` 的六处文案 → **按 spec 判：一律不改**，新路由层自己返回「请先填路径…」 | spec 是约束权威；保回退零影响 | 原型档案与实现有一句文案不一致，无功能影响 |
| R2 | `index.html:128` 提到 SSE → **按 spec 判：只用轮询**（0.8s） | spec 拍板记录；`jobs.js` 已是轮询 | 断线重连体验略差，功能不受影响 |
| R3 | **检测期间的写互斥由前端实现**（后端零改动）：`POST /api/detect` 成功后把 `{kind:"detect", jobId, videoPath}` 记进 localStorage；载入时若该 job 仍 `running` 且视频一致 ⇒ 恢复轮询并禁用全部写入口；job 404（服务重启）⇒ 丢弃记录 | 旧界面就是「检测期间禁整张表」（`app.py:631-632`）；spec 要求业务逻辑一行不改 | 绕过 UI 直连 API 仍可写坏数据——本机单用户工具不存在这种客户端 |
| R4 | **每行裁决状态由后端补下发**（`state_payload()` 增 `verdicts` 键） | 前端要渲染表格「裁决」列 ✔/✘ 与右栏按钮的 plain 态；回包只有计数，刷新后无从得知 | 若判错则表格出现「有反馈但不显示」的误导——已用测试钉住 |
| R5 | **`POST /api/events/restore` 支持可选 `keys`**（按 key 恢复）；不传 `keys` = 原全量语义 | spec 键盘表要求 `Ctrl+Z` 可撤「删除」；全量恢复会连带复活用户早先删掉的球（且不回滚已清理的勾选/标签） | 撤销删除多恢复几条，用户可见可再删 |
| R6 | **漏检补录不产生表格行**：只进 `feedback.misses`，界面用 `msg` toast + 页脚「反馈」摘要呈现 | 后端 `record_miss` 只写 feedback；旧界面（Gradio）的表格同样不会多出一行——原型里 `unshift` 一行是原型自造 | 补录后表格没有新行，用户可能误以为失败——故 toast 文案逐字保留「✓ 已记录漏检 …」 |
| R7 | **时刻校准是会话级偏移**（不落盘；作用在**显示时刻 + 切片预览 + 集锦切点**）：`offset` 存组件内，时刻列显示 `fmtT(tsec+offset)`，切片 `t` 跟随显示时刻，**生成时随请求体一并送给 `/api/gen`**，后端 `api_gen._apply_session_rows` 把它加进切点 | 旧界面的 `gr.Number time_offset` 是会话内临时值，重开页面回 0——但它的 `load_events_table(offset=…)` **把偏移写进了 `rows[1]`，而 `rows[1]` 就是生成时的切点** ⇒ 它**确实影响成品**，不是「纯显示」；Web 层无对应端点，故走请求体 | 交付版曾按「纯显示」实现，用户设的校准因此在成品里静默失效（终审 A-1/A-2 修的就是这条）——**订正于 Task 11 交付后**。其后 ②（用户 2026-09-19 批准）发现「关闭即复原 + 模态」使这条管线**真界面不可达**，改「关闭只是关闭、复原入口=归零」+ 顶栏 ⏱ 指示器（`4c68e06`→`273132a`→`95e6145`→`0cadbc3`） |
| R8 | **删除的撤销 = 按 key 恢复**，恢复后的球回到未勾选/未归人（**不回滚删除时的清理**），toast 如实说明 | `restore_deleted` 的既有语义（docstring 明写）；旧界面「↩ 恢复已删」同款 | 撤销删除后需重新勾选/归人，与旧界面一致 |
| R9 | **切片用「显示时刻」**（`tsec = parseT(row.t) + offset`），与旧界面 `_preview_and_reason` 取 `_parse_t(rows[i][1])`（显示列）一致 | 逐字对齐冻结代码的行为 | 校准偏移大时预览窗口与检测时刻错位——与旧界面同病，不新增 |
| R10 | **原型里「生成弹窗的假进度」「三处 `setInterval`」全部换成 job 轮询**；`found` 字段恒 `null` ⇒ 进度文案一律用 `msg`，不用「已发现 N 球」 | `Job.as_dict()` 的 `found` 实测恒 null（三个 worker 都不 `report(found=)`） | 无 |
| R11 | **`前端原型/` 保持不入库**（沿用 Plan A 决定） | 它是原型档案，不是交付物 | 无 |
| R12 | **时刻列与时间轴位置一律用服务端下发的 `t` 串**（`dispT(row, offset)`：`offset=0` 原样用，非 0 才用 `fmtT` 重算） | 表里那串就是键的可读形态且零填充 = 字典序即时间序；前端复算会与服务端差一个格式化边界（`59.96` 到底算 `00:60.0` 还是 `01:00.0`） | 校准偏移非 0 时列里是重算值，与不带偏移时同一套格式化函数，无肉眼差别 |
| R13 | **路径输入框用 `@change` + `@keyup.enter` 载入**，不 `watch` 逐字 | `watch` 会每敲一键发一次 `POST /api/session`，还把 `ui_prefs.json` 的 `lastVideo` 写成半截路径 | 与旧界面（回车/失焦才探测）一致 |
| R14 | **档位/勾选计数从 `rows[]` 聚合；反馈计数取 `state.feedback`、已删计数取 `state.deleted.length`** | 三者各有权威源：`rows[]` 是可见行、`feedback` 是盘上 feedback.json 的摘要、`deleted` 是盘上 deleted.json 的键表——已删的球不在 `rows[]` 里，用行数反推「已删」会在筛选态下算错 | 页脚某个计数与实际差几条——已用 e2e 逐条钉住 |
| R15 | **成功提示只弹服务端下发的 `msg`**；PATCH 勾选/打标签不带 `msg` ⇒ 那两处不弹 toast | 前端自己编成功文案会和后端文案两套口径并存 | 勾选/打标没有 toast（原型也没有） |
| R16 | **裁决联动勾选**：`✔ 是进球` ⇒ 勾上进片、`❌ 误判` ⇒ 取消进片（必要时先发一次 `PATCH {selection}`，再发反馈） | spec 104 行与 `mark_verdict` 实现冲突（实现只写 feedback）。取原型行为——它是用户逐轮审核并获批的那版；两步顺序固定「先勾选后反馈」，保证带 `msg` 的回包是最后落到 state 的那份 | 用户改判时会多一次请求；若判错，裁决与进片脱钩（旧界面行为） |
| R17 | **撤销栈每条带 `label`**，`Ctrl+Z` 弹「已撤销：{label}」 | 原型没有撤销反馈；不提示的话用户不知道刚才撤掉的是哪一步（栈深 60） | `label` 文案措辞可能不够精确，无功能影响 |
| R18 | **带数据的验证走 `tests/e2e/` 的临时目录服务，不用 7861**：`_work_dir_for()`（`bbh/app.py:336`）、`PREFS_FILE`（`bbh/web/api_session.py:25`）、`TAGS_FILE`（`bbh/app.py:35`）都是**裸相对路径、按 cwd 解析** | 起在仓库根的 7861 服务一开会话就写用户的 `ui_prefs.json`、一打标就写 `tags.json`（后者是红线里没有 git 副本的档）。临时目录服务把两个根档 + `work/` 一起隔离，副作用还能真验 | 若判错，e2e 会写用户真档——故跑器里加了「仓库根两个档字节不变」的收尾断言 |
| R19 | **`actions.undo()` 先撤销、成功才出栈**（失败时记录留在栈里可重试） | 先 `pop` 再请求，一次断网/500 就把这一步永久吞掉，用户再按 `Ctrl+Z` 会跳过它去撤销更早的一步 | 失败重试时可能重复施加同一次撤销——撤销是幂等的（写回原值/再点一次 toggle），重复无害 |
| R20 | **删掉原型里那 20 条 mock 专属机制**（假进度 `setInterval`、假 `found` 计数、`unshift` 出来的漏检行、`window.__toast` 等），一律换真端点 | 原型是拿 mock 演示交互的；真实现有 job 轮询与权威回包（R6/R10） | 无 |
| R21 | **「应用到全部」= 会话级前端覆盖值**（`durAll`），单行改动存 `durMap`；两者都只活在本会话 | 后端没有 before/after 端点（`PATCH /api/events/{key}` 只收 `selection`/`tag`），但它们是**真参数**——`/media/clip` 按 URL 里的 before/after 现切，不是假显示 | 重开页面回到服务端默认 5.0/2.0；用户需重设一次 |
| R22 | **`curKey` 必须始终落在 `visibleRows` 里**：筛选把它藏起来就连它一起清 | 右栏按全部 `rows[]` 找（筛选不清空），键盘按可见行找（`rowByKey`）——不分叉才不会有「右栏显示 A、按键无反应」 | 筛选切换后要重新点一行，多一次点击 |
| R23 | **生成结果不解析 `summaryMd`**：成品清单从 `videos[]` 渲染，`summaryMd` 逐字显示 | 跳过项/统计行只在 `summaryMd` 里，而解析它等于把 `_gen_result` 的「从右往左找真文件」逻辑再写一遍（第二份格式契约） | 清单与日志有重复信息，无功能影响 |
| R24 | **进度文案只显示服务端 `job.msg`**；原型那句固定「逐球渲染 + 拼装 + 响度归一」不写 | 竖屏管线还多跟球运镜/慢放/片头钩子，写死的步骤描述与实际不符 | 少一句说明性文字 |
| R25 | **`🎬 开始生成` 在竖屏下也照原型逐字**（不随模式改成 📱） | 同一颗按钮文字随模式跳变更费解；弹窗标题已经写明画幅 | 竖屏下的按钮图标与画幅不完全对应 |
| R26 | **不实现「📂 打开所在文件夹」** | 浏览器打不开本机资源管理器；为它加一个能在服务端执行本机命令的端点等于开一个安全面，而这个功能本身不值 | 用户要自己去 `work/<视频>/` 找成品（`summaryMd` 里写了路径） |
| R27 | **结果态按钮是 `↻ 重新选择`**（新文案；原型此处仍是「📂 打开所在文件夹」） | 结果视图里选择清单不可见，留着「开始生成」会让用户对着看不见的勾选重跑一遍 | 多一次点击 |
| R28 | **生成/检测的真跑不进 e2e**：`page.route` 桩住 job 端点走完 UI 全流程，`choices` 走真后端；真渲染留给 Plan E 真机验收 | 真渲染是分钟级、依赖 ffmpeg（夹具视频也要十几秒），会把 e2e 变成不稳定源；而这段要钉的是 UI 契约（按钮态/请求形状/渲染分支/记忆生命周期） | 真管线的端到端正确性直到 Plan E 才验——**Plan E 必须真跑一次两种画幅** |
| R29 | 检测完成提示直接用服务端 `done_msg`（`app.py:547` 已经给出「🟢 高置信 N 个 / 🟡 / ⚪」） | 前端复算计数多一份口径，而服务端文案比原型那句更完整 | 无 |
| R30 | **只有 `detect` 跑时锁表**；`🧍 识别出手人` 与两个集锦按钮在检测期间一并禁用（锁表以外的东西不动） | 后端 `_state["running"]` 的语义就是检测；回扫/生成都读同一份索引，检测完结果会变，跑在前面等于白跑（旧界面也是靠按钮 disable） | 若判错（例如回扫其实也该锁），用户能点一次白跑，不坏数据 |
| R31 | **长任务进度文案放顶栏下状态带 `.jobmsg`**，按钮只显示 `⏳ 检测中 {pct}%` | `msg` 逐帧带进度（「⏳ 检测中… 72/180 帧 (40%)」），塞进按钮会与按钮自身的百分比重复；旧界面也是独立一行状态 | 多占一行高度 |
| R32 | **长任务失败一律 `ElMessage.error(e.message)`**，弹窗的错误态不再重复显示正文 | 检测/回扫没有弹窗，toast 是唯一出路；同一句话弹两处反而像两个错 | 生成失败时弹窗里只有「生成失败」，原因在 toast 里 |
| R33 | **屏上文案不许抄「刷新/关闭页面会取消检测」**（旧界面 `app.py:633` 的原话）：`_cancel_evt` 只由 Gradio 的 `demo.unload`（`app.py:583`）置位，新页面没有这条钩子 ⇒ 关页面不退检测，job 继续在服务端跑到完，重开页面靠 `jobMem` 接上 | 架构变了就该改话；照抄等于对用户说假话（用户会以为关掉页面就止损了） | 无——服务端 `cancelled` 分支仍在（回退 Gradio 时还需要），只是前端不可达 |
| R34 | **右栏判定依据卡卡头的时刻也吃校准偏移**（`BasisPanel` 收 `:offset`，卡头 `dispT(row, offset)`，不再是写死的 `dispT(row, 0)`） | 旧界面 `load_events_table(offset=…)` 是「载入时整表位移」⇒ 屏幕上每一处时刻都同源；新界面若卡头写死 0，同一行的时刻会左右栏不一样（表格 `00:02.7`、右栏 `00:01.2`），用户一眼就看见 | `offset=0`（默认）时 `dispT` 原样返回服务端串，行为不变 |
| R35 | **帧步进沿用固定 1/30s**（`stepFrame(n) = seekBy(n / 30)`），不为它去改 Plan A 的 `/api/state` 下发 fps | spec §静态与媒体路由 的括号里写了「帧率由后端探测下发」，但 Plan A 的契约里 `state_payload` 没有 `fps` 字段（`bbh/web/api_session.py:142-158` 只有 `duration`），`_cut_preview` 也不重编码定帧率（无 `-r`，保留源帧率）；原型自己就是 `d/30` | 非 30fps 源上步进偶尔落在两帧之间、由浏览器就近吸附——差半帧的观感问题，不动数据；若 09-11_3 上手感不对，Plan E 真机验收时把 `fps` 加进 state（增量小改，不回头重开 Plan A） |
| R36 | **`tsec` 夹负**：`Math.max(0, parseT(row.t) + offset)`，偏移后为负的时刻一律夹到 0 | 旧界面 `app.py:801` 在同一个位置夹（`t = max(0.0, e.t + (offset or 0.0))`），不夹的话时刻校准填负数（弹窗没有 `:min`）时最早那颗球会在时刻列/时间轴/右栏卡头三处渲染出 `-1:-27.3`，切片 t 与分色条红线也会落进负区间——这是 1:1 平移的实质偏差，不是风格问题（Task 1 复审提出，控制者裁定要修） | 夹在 `tsec` 就同时管住显示与切片（旧界面也是同一处管两头）；`fmtT` 的行尾进位边界（`59.96 → "00:60.0"`）不受影响 |

---

## 文件结构（本计划创建/修改的全部文件）

**新建（前端源码）**
| 文件 | 职责 |
|---|---|
| `frontend/src/shared/time.js` | 秒 ↔ `mm:ss.s` 键格式（与后端 `models.time_key` 逐字同款）、显示时刻/切片时刻派生 |
| `frontend/src/shared/media.js` | `/media/work`、`/media/video`、`/media/clip` 的 URL 拼装（带 `&v=` 击穿缓存） |
| `frontend/src/shared/jobmemory.js` | 长任务的 localStorage 记忆（刷新后重新接上检测进度） |
| `frontend/src/edit/actions.js` | 全部写操作 + 撤销栈（纯 JS，注入 `api`/`store`，可 Node 直测） |
| `frontend/src/edit/EditBay.vue` | 页面骨架与编排：顶栏、状态装配、键盘接线、弹窗编排 |
| `frontend/src/edit/tiers.js` | 档位文案与配色的唯一来源（表格 el-tag 与时间轴 dot 的 title 共用） |
| `frontend/src/edit/EventTable.vue` | 表格渲染：7 列 + 行态（绿底/红底/时刻划掉）+ 空态文案（工具条/页脚在 EditBay——数据在那边） |
| `frontend/src/edit/TimelineStrip.vue` | 全场进球分布标记条（点刻度跳行） |
| `frontend/src/edit/ClipPanel.vue` | 视频盒 + 分色进度条 + 播放控制（帧步进/倍速/循环/前后秒） |
| `frontend/src/edit/BasisPanel.vue` | 判定依据卡（常显 + ⓘ popover）+ 两排裁决按钮 + 出手人 chip + 反馈摘要 |
| `frontend/src/edit/dialogs/GenDialog.vue` | 生成集锦弹窗（横/竖屏） |
| `frontend/src/edit/dialogs/OffsetDialog.vue` | 时刻校准弹窗 |
| `frontend/src/edit/dialogs/BulkDeleteDialog.vue` | 批量删除弹窗 |
| `frontend/src/edit/dialogs/HelpDialog.vue` | 快捷键帮助弹窗（静态文案） |
| `frontend/selftest.mjs` | Node 断言脚本：纯 JS 模块的单元测试（无第三方依赖） |
| `tests/e2e/edit_bay.spec.mjs` | 页级 e2e（Node + Playwright，真服务真产物） |
| `tests/e2e/test_edit_bay_e2e.py` | e2e 的 pytest 启动器（造夹具、起临时服务、跑 Node 脚本；Node/Playwright 缺失则 skip） |

**修改**
| 文件 | 改动 |
|---|---|
| `frontend/src/shared/hotkeys.js` | 加键名归一（`space`/`up`/`down`/`left`/`right`/`esc`/`?`/`ctrl+z`），保留「输入框聚焦不触发」 |
| `frontend/src/shared/jobs.js` | 拆出 `followJob(jobId, onTick)`（`runJob` 复用它），供刷新后接着跟随 |
| `frontend/src/shared/session.js` | 加 `off(f)`（组件卸载要摘监听；热更新/路由切换会攒死回调） |
| `frontend/src/edit/main.js` | 从 `template:` 字符串骨架改为挂载 `EditBay.vue` |
| `frontend/smoke.mjs` | 剪辑台页的地标从 `#app h2` 改为 `#app .topbar input`（该页成了 SFC 主体、页内没有 h2）；出手人墙/标定页仍是骨架 `h2` |
| `bbh/web/api_session.py` | `state_payload()` 增 `verdicts` 键（R4） |
| `bbh/web/api_events.py` | `POST /api/events/restore` 支持可选 `keys`（R5） |
| `tests/test_web_session.py` | `verdicts` 的用例 |
| `tests/test_web_events.py` | restore-by-keys 的用例 |
| `bbh/static/**` | `npm run build` 产物（入库） |

**不改**：`bbh/app.py`（除本计划不触碰）、`bbh/web/` 其余模块、`work/` 下任何档、`run_app.bat`（切到新界面是 Plan E 的事）。

---

### Task 1: 共享层——时间与媒体 URL 工具（含 Node 自测骨架）

**Files:**
- Create: `frontend/src/shared/time.js`
- Create: `frontend/src/shared/media.js`
- Create: `frontend/selftest.mjs`

**Interfaces:**
- Consumes: 无（最底层）
- Produces:
  - `fmtT(sec: number): string` —— 秒 → `mm:ss.s`，**与后端 `bbh/models.py:time_key` 逐字同款**（`f"{int(t//60):02d}:{t%60:04.1f}"`）
  - `parseT(s: string): number` —— `mm:ss(.s)` → 秒（不假定分钟位数，`101:16.5` 合法）
  - `tsec(row, offset=0): number` —— 该行切片用的秒数（R9：显示时刻）
  - `dispT(row, offset=0): string` —— 该行时刻列的显示串（`offset === 0` 时**原样用服务端的 `row.t`**）
  - `clipUrl(videoPath, t, before, after): string`
  - `videoUrl(videoPath): string`

- [ ] **Step 1: 写失败的测试**

新建 `frontend/selftest.mjs`：

```js
// frontend/selftest.mjs —— 纯 JS 模块的单元测试（无第三方依赖）
//   cd frontend && node selftest.mjs
// 只测「不经浏览器、不经服务端」的逻辑：时间格式、URL 拼装、键盘归一、
// 写操作与撤销栈、job 跟随与记忆。渲染与接线由 tests/e2e 的 Playwright 验。
import assert from "node:assert/strict";

const tests = [];
export function test(name, fn) { tests.push([name, fn]); }

// ---- time.js ----
const { fmtT, parseT, dispT, tsec } = await import("./src/shared/time.js");

test("fmtT 与后端 models.time_key 同款：分钟至少两位、秒 04.1f", () => {
  assert.equal(fmtT(12.1), "00:12.1");
  assert.equal(fmtT(0), "00:00.0");
  assert.equal(fmtT(59.96), "00:60.0");     // 后端就是这个产物（进位边界）
  assert.equal(fmtT(101 * 60 + 16.5), "101:16.5");  // 三位分钟
});

test("parseT 按分隔符切、不假定分钟位数", () => {
  assert.equal(parseT("00:12.1"), 12.1);
  assert.equal(parseT("101:16.5"), 101 * 60 + 16.5);
  assert.equal(parseT("12:34"), 754);
});

test("tsec 用显示时刻（与旧界面 _preview_and_reason 同口径）+ 校准偏移", () => {
  const row = { key: "00:12.1", t: "00:12.1" };
  assert.equal(tsec(row, 0), 12.1);
  assert.equal(tsec(row, 1.5), 13.6);
  // 进位边界：key 与 t 都是 "00:60.0" 时数值仍是 60.0
  assert.equal(tsec({ key: "00:60.0", t: "00:60.0" }, 0), 60.0);
  // R36：偏移后为负的夹到 0（旧界面 app.py:801 同址夹）；非数字 offset 回落 0
  assert.equal(tsec({ key: "00:01.2", t: "00:01.2" }, -2), 0);
  assert.equal(tsec({ key: "00:01.2", t: "00:01.2" }, "abc"), 1.2);
});

test("dispT 在 offset=0 时原样用服务端串（不复算，避免 00:60.0 → 01:00.0 的显示漂移）", () => {
  assert.equal(dispT({ key: "00:60.0", t: "00:60.0" }, 0), "00:60.0");
  assert.equal(dispT({ key: "00:60.0", t: "00:60.0" }, 1.0), "01:01.0");
  // R36：负偏移把最早那颗球压到 0 以下时显示 "00:00.0"，不是 "-1:-0.8"
  assert.equal(dispT({ key: "00:01.2", t: "00:01.2" }, -2), "00:00.0");
});

let failed = 0;
for (const [name, fn] of tests) {
  try { fn(); console.log(`  ok  ${name}`); }
  catch (e) { failed++; console.error(`FAIL  ${name}\n      ${e.message}`); }
}
console.log(failed ? `\n${failed}/${tests.length} 失败` : `\n✅ ${tests.length} 通过`);
process.exit(failed ? 1 : 0);
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && node selftest.mjs`
Expected: FAIL —— `Cannot find module …/src/shared/time.js`

- [ ] **Step 3: 实现 `time.js` 与 `media.js`**

`frontend/src/shared/time.js`：

```js
// 时刻格式的唯一来源：与后端 bbh/models.py:time_key 逐字同款
// （f"{int(t // 60):02d}:{t % 60:04.1f}"）——分钟至少两位、秒 4 宽 1 位小数。
// 键（row.key = 原始检测时刻）与显示（row.t = 受校准偏移影响的时刻）都用它格式化，
// 但**键永远来自服务端的 row.key**，前端绝不自己造键：在进位边界上
// （t=59.96 → 后端给出 "00:60.0"）parse+fmt 不是定点，
// parseT("00:60.0") → 60.0 → fmtT → "01:00.0"，那是**另一颗球**的键。
export function fmtT(sec) {
  const t = Number(sec) || 0;
  return `${String(Math.floor(t / 60)).padStart(2, "0")}:${(t % 60).toFixed(1).padStart(4, "0")}`;
}

export function parseT(s) {
  const [m, sec] = String(s ?? "").split(":");
  return (Number(m) || 0) * 60 + (Number(sec) || 0);
}

// 切片用的秒数 = 显示时刻（旧界面 _preview_and_reason 取的就是表格时间列）
// 加校准偏移（纯前端显示偏移，不落盘、不发请求），偏移后为负的夹到 0——
// 旧界面在同一个位置夹（app.py:801 的 `t = max(0.0, e.t + (offset or 0.0))`），
// 不夹的话最早那颗球会渲染出 `-1:-27.3`（R36）。
// offset 用 `(Number(offset) || 0)` 而不是 `Number(offset || 0)`：后者把非数字串
// 变成 NaN，会一路流进切片的 t= 参数（NaN 不等于 0）。
export function tsec(row, offset = 0) {
  return Math.max(0, parseT(row.t) + (Number(offset) || 0));
}

// 时刻列显示串。offset=0 时**原样用服务端下发的 row.t**：后端 time_key 在
// 进位边界会给出 "00:60.0"，而复算会把它显示成 "01:00.0"（同一秒、不同串），
// 与旧界面显示不一致——没必要为了「少一个分支」牺牲 1:1。
export function dispT(row, offset = 0) {
  return Number(offset) ? fmtT(tsec(row, offset)) : row.t;
}
```

`frontend/src/shared/media.js`：

```js
// 媒体 URL 拼装。三条硬规矩（都来自后端实现与旧界面的坑）：
//  · /media/clip 每次覆盖同一个 work/<视频>/preview.mp4 ⇒ URL **必须**带 &v=，
//    否则浏览器命中缓存、改完前后秒还是老画面；
//  · /media/video 支持 Range（拖进度条靠它），且只允许播放当前会话的视频；
//  · path 用绝对路径（后端 _media_url 就是这么生成的），白名单由服务端判。
const bust = () => String(Date.now());

export function clipUrl(videoPath, t, before, after) {
  const q = new URLSearchParams({
    path: videoPath, t: String(t), before: String(before), after: String(after),
    v: bust(),
  });
  return `/media/clip?${q}`;
}

export function videoUrl(videoPath) {
  return `/media/video?${new URLSearchParams({ path: videoPath })}`;
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && node selftest.mjs`
Expected: PASS —— `✅ 4 通过`

- [ ] **Step 5: 补 media.js 的测试并跑**

在 `selftest.mjs` 的 `// ---- time.js ----` 段之后追加：

```js
// ---- media.js ----
const { clipUrl, videoUrl } = await import("./src/shared/media.js");

test("clipUrl 带 path/t/before/after 与缓存击穿 v", () => {
  const u = clipUrl("D:/v/a b.mp4", 12.1, 5, 2);
  assert.match(u, /^\/media\/clip\?/);
  const q = new URLSearchParams(u.slice(u.indexOf("?") + 1));
  assert.equal(q.get("path"), "D:/v/a b.mp4");   // URLSearchParams 负责编码空格
  assert.equal(q.get("t"), "12.1");
  assert.equal(q.get("before"), "5");
  assert.equal(q.get("after"), "2");
  assert.ok(q.get("v"), "缺 &v= 会让浏览器命中上一次的切片缓存");
});

test("videoUrl 只带 path", () => {
  const q = new URLSearchParams(videoUrl("D:/v/a.mp4").split("?")[1]);
  assert.equal(q.get("path"), "D:/v/a.mp4");
  assert.equal([...q.keys()].length, 1);
});
```

Run: `cd frontend && node selftest.mjs`
Expected: PASS —— `✅ 6 通过`

- [ ] **Step 6: 提交**

```bash
git add frontend/src/shared/time.js frontend/src/shared/media.js frontend/selftest.mjs
git commit -m "feat(frontend): 共享层时间与媒体 URL 工具 + Node 自测骨架

fmtT 与后端 models.time_key 逐字同款（分钟至少两位、秒 04.1f）；
dispT 在 offset=0 时原样用服务端串，避免 00:60.0 → 01:00.0 的显示漂移。
clipUrl 强制带 &v= 缓存击穿（/media/clip 每次覆盖同一个 preview.mp4）。"
```

---

### Task 2: 后端两处增量——每行裁决态 `verdicts` + 按 key 恢复

本任务是本计划**唯一**触碰后端的部分，两处都是**纯增量**（不新增 sidecar 字段、不改任何既有回包字段、不改 `bbh/app.py`）。理由见裁定 R4/R5。

**Files:**
- Modify: `bbh/web/api_session.py`（`state_payload()` 增 `verdicts`）
- Modify: `bbh/web/api_events.py`（`POST /api/events/restore` 支持可选 `keys`）
- Test: `tests/test_web_session.py`、`tests/test_web_events.py`

**Interfaces:**
- Consumes: `A._load_feedback(vp)`（既有，返回 `{"verdicts": [...], "misses": [...]}`，每条 verdict 含 `t` / `t_str` / `verdict`）、`A._fmt_t(t)`、`A._save_deleted(vp, keys)`、`A._load_deleted(vp)`
- Produces:
  - `state_payload()["verdicts"]: { "<行键>": "goal" | "not_goal" }` —— 空会话为 `{}`；**键恒为行键（`row.key` 同款串）**
  - `POST /api/events/restore` 接受 `{"keys": [行键]}`；不传/传空数组 = **原全量语义**（含「没有已删除的进球」的 400）

- [ ] **Step 1: 写失败的测试（verdicts）**

在 `tests/test_web_session.py` 末尾追加：

```python
def test_verdicts_map_is_empty_in_the_base_payload():
    """空会话也要有 verdicts 键：前端无条件读它，缺键会渲染成 undefined。"""
    j = client.get("/api/state").json()
    assert j["verdicts"] == {}


def test_verdicts_map_is_per_row(env):
    # 本模块的 env 只负责 chdir + 复位会话（既有用例各自调 _make_result 造结果，
    # 那边 mkdir 不带 exist_ok，env 里造会撞 FileExistsError）——裁决要真行，
    # 这两句自己来，与下面 t_str 用例同款自给自足。
    _make_result(env)
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    client.post("/api/feedback/0:12.1", json={"verdict": "not_goal"})
    client.post("/api/feedback/1:12.1", json={"verdict": "goal"})
    j = client.get("/api/state").json()
    assert j["verdicts"] == {"00:12.1": "not_goal", "01:12.1": "goal"}
    # 汇总结数照旧（两处同源，不许分叉）
    assert j["feedback"]["goal"] == 1 and j["feedback"]["no"] == 1
    # 取消裁决 → 该行不再亮灯
    client.post("/api/feedback/0:12.1", json={"verdict": "not_goal"})
    assert client.get("/api/state").json()["verdicts"] == {"01:12.1": "goal"}


def test_verdicts_key_is_the_recorded_string_not_a_recompute(tmp_path,
                                                             monkeypatch):
    """进位边界：t=59.96 的行键是 "00:60.0"（models.time_key 的产物），
    而 60.0 那行的键是 "01:00.0"——两颗不同的球。
    映射必须用记录里的 t_str，不能 _fmt_t(t) 复算：复算会把 59.96 那行
    算成 "01:00.0"，于是 59.96 永不亮灯、60.0 被误亮。"""
    from test_web_events import _make_times
    monkeypatch.chdir(tmp_path)
    _make_times(tmp_path, [59.96, 60.0])
    state.SESSION.video_path = ""
    client.post("/api/session", json={"videoPath": f"D:/videos/{NAME}.mp4"})
    assert [r["key"] for r in client.get("/api/state").json()["rows"]] \
        == ["00:60.0", "01:00.0"]
    r = client.post("/api/feedback/00:60.0", json={"verdict": "goal"})
    assert r.status_code == 200
    assert client.get("/api/state").json()["verdicts"] == {"00:60.0": "goal"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_web_session.py -k verdicts`
Expected: FAIL —— `KeyError: 'verdicts'`

- [ ] **Step 3: 实现 `verdicts`**

`bbh/web/api_session.py`：在 `_feedback_summary` 下方新增函数，并在 `state_payload()` 的 base 字典与 `base.update(...)` 里各加一处。

```python
def _verdict_keys(vp: str) -> dict:
    """每行裁决态：行键 → "goal" / "not_goal"（表格「裁决」列与右栏按钮按行渲染用）。

    汇总结数不够用：刷新页面后前端要知道**哪几行**已裁决，否则会出现
    「页脚说有 3 条反馈、表格一个 ✔ 都没有」。
    键用记录里的 t_str（写裁决那一刻这一行的时间列串），**不要**用
    _fmt_t(v["t"]) 复算：进位边界上 parse+fmt 不是定点（t=59.96 的键是
    "00:60.0"，复算给出 "01:00.0" = 另一颗球）。旧格式（false_positives
    迁移）没有 t_str → 退回 _fmt_t(t)；再取不到就跳过（汇总结数照旧）。
    """
    out = {}
    for v in A._load_feedback(vp)["verdicts"]:
        verdict = v.get("verdict")
        if verdict not in ("goal", "not_goal"):
            continue
        k = v.get("t_str")
        if not isinstance(k, str) or not k:
            try:
                k = A._fmt_t(float(v["t"]))
            except (KeyError, TypeError, ValueError):
                continue
        out[k] = verdict
    return out
```

在 `state_payload()` 的 base 字典里，`"feedback": {...}` 一行之后加：

```python
            "verdicts": {},
```

在 `base.update(hint=hint, tags=…, …, calib={...})` 那段里加一行：

```python
                verdicts=_verdict_keys(vp),
```

- [ ] **Step 4: 跑测试确认通过**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_web_session.py tests/test_web_marks.py`
Expected: PASS（含既有用例；`test_feedback_summary_counts_reach_the_payload` 不受影响）

- [ ] **Step 5: 写失败的测试（按 key 恢复）**

在 `tests/test_web_events.py` 里、既有 `test_delete_and_restore` 系列之后追加：

```python
def test_restore_only_the_given_keys(env):
    """Ctrl+Z 撤销一次删除 = 只恢复那一条；全量恢复会连带复活用户早先
    删掉的球（且它们的勾选/标签不会回滚）。"""
    client.request("DELETE", "/api/events",
                   json={"keys": ["00:12.1", "01:12.1"]})
    r = client.post("/api/events/restore", json={"keys": ["00:12.1"]})
    assert r.status_code == 200
    assert [x["key"] for x in r.json()["rows"]] == ["00:12.1", "02:12.1"]
    assert _read(env, "deleted.json") == ["01:12.1"]
    assert "已恢复 1 条" in r.json()["msg"]


def test_restore_without_keys_still_restores_everything(env):
    """不传 keys = 原全量语义（「⋯ ↩ 恢复已删」按钮走这条）。"""
    client.request("DELETE", "/api/events",
                   json={"keys": ["00:12.1", "01:12.1"]})
    r = client.post("/api/events/restore", json={})
    assert len(r.json()["rows"]) == 3
    assert _read(env, "deleted.json") == []


def test_restore_a_key_that_is_not_deleted_is_400(env):
    client.request("DELETE", "/api/events", json={"keys": ["01:12.1"]})
    r = client.post("/api/events/restore", json={"keys": ["00:12.1"]})
    assert r.status_code == 400
    assert "不在已删清单里" in r.json()["error"]
    assert _read(env, "deleted.json") == ["01:12.1"]      # 未动盘
```

- [ ] **Step 6: 跑测试确认失败**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_web_events.py -k restore`
Expected: FAIL —— 新用例红（`test_restore_without_keys_still_restores_everything` 绿，其余两条红）

- [ ] **Step 7: 实现按 key 恢复**

`bbh/web/api_events.py`：`KeysIn` 之后加一个入参模型（**复用它**，形状与 `DELETE /api/events` 一致），并替换 `restore_events`：

```python
@router.post("/events/restore")
def restore_events(body: KeysIn | None = None):
    """恢复已删球。不带 keys = 原全量语义（旧界面的「↩ 恢复已删」）；
    带 keys = 只恢复这几条（剪辑台 Ctrl+Z 撤销一次删除走这条）。

    恢复的球一律回到「未勾选/未归人/无标签」（app.restore_deleted 的既有
    语义：删除时的清理不回滚）——msg 里如实说明，别让用户以为全还原了。
    """
    vp = _session_video()
    with state.video_lock(vp):
        keys = [k for k in ((body.keys if body else None) or [])
                if isinstance(k, str)]
        if not keys:
            restored = A.restore_deleted(vp)          # 内部空清单 → gr.Error → 400
        else:
            deleted = A._load_deleted(vp)
            wanted = set(keys)
            hit = [k for k in deleted if k in wanted]
            if not hit:
                raise HTTPException(
                    400, f"这些球不在已删清单里：{keys[0]}（可能已恢复）")
            A._save_deleted(vp, [k for k in deleted if k not in wanted])
            restored = hit
    payload = state_payload()
    payload["msg"] = f"↩ 已恢复 {len(restored)} 条进球（回到未勾选/未归人状态）"
    return payload
```

（`HTTPException` 已在本模块顶部 import。）

- [ ] **Step 8: 跑测试确认通过**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_web_events.py tests/test_web_session.py tests/test_web_marks.py tests/test_web_wall.py tests/test_delete_events.py`
Expected: PASS

- [ ] **Step 9: 提交**

```bash
git add bbh/web/api_session.py bbh/web/api_events.py tests/test_web_session.py tests/test_web_events.py
git commit -m "feat(web): 每行裁决态下发 + 按 key 恢复已删球（Plan B 的两处纯增量）

verdicts: state_payload 增 {行键 → goal|not_goal}，表格「裁决」列与右栏按钮
按行渲染要靠它（回包原先只有汇总结数）。键取记录里的 t_str，不复算 _fmt_t(t)
——进位边界 t=59.96 的键是 00:60.0，复算会算成 01:00.0（另一颗球）。

restore: POST /api/events/restore 接受可选 keys（不带 = 原全量语义）。
Ctrl+Z 撤销一次删除只该恢复那一条；全量恢复会连带复活用户早先删掉的球。"
```

---

### Task 3: 共享层——键盘归一 + 长任务跟随与记忆

**Files:**
- Modify: `frontend/src/shared/hotkeys.js`
- Modify: `frontend/src/shared/jobs.js`
- Create: `frontend/src/shared/jobmemory.js`
- Test: `frontend/selftest.mjs`

**Interfaces:**
- Consumes: `api`（`src/shared/api.js`）
- Produces:
  - `keyName(e): string` —— 键名归一：`" "`→`"space"`、`"ArrowDown"`→`"down"`、`"Escape"`→`"esc"`、`"Enter"`→`"enter"`、字母小写、`ctrl` 前缀 `"ctrl+z"`、**`ctrl`+`shift` 同时按下 → `"ctrl+shift+z"`**（A-9：带 shift 的 ctrl 组合键**不落回基础键**——落回 `ctrl+z` 会真的撤销一步；给一个没人注册的名字 = no-op。⚠ 只掐组合键，`?` 这种真键就是 shift+/ 的必须照旧归一到 `?`）；其余 `e.key.toLowerCase()`
  - `bindHotkeys(map, { disabled, win })` —— `win` 默认 `globalThis`（注入假窗口即可在 Node 里测）
  - `POLL_MS = 800`
  - `startJob(startUrl, body): Promise<string>`（拿 jobId）
  - `followJob(jobId, onTick, { intervalMs }): Promise<result>`（跟随到终态并取结果）
  - `runJob(startUrl, body, onTick)` —— 仍保留 = `startJob` + `followJob`（其他页直接用）
  - `createJobMemory(store)` → `{ remember(rec), load(), clear() }`；`rec = { kind, jobId, videoPath, startedAt }`

- [ ] **Step 1: 写失败的测试**

在 `frontend/selftest.mjs` 的收集段之后、`// ---- media.js ----` 之前插入一个辅助（供后续任务复用）：

```js
// 假 fetch：api.js 只用到 {ok, status, statusText, json()}。
function stubFetch(handler) {
  const calls = [];
  globalThis.fetch = async (url, opt) => {
    calls.push({ url, opt });
    const { status = 200, body } = handler(String(url), opt) || {};
    return { ok: status < 400, status, statusText: "x",
             json: async () => body };
  };
  return calls;
}
```

新用例都是 `async`，而 Task 1 留下的汇总循环是**同步**调用 `fn()`（async 函数返回的 promise 一旦拒绝就成了 unhandled rejection，还会被记成「通过」）。先把汇总循环里这一行：

```js
  try { fn(); console.log(`  ok  ${name}`); }
```

换成：

```js
  try { await fn(); console.log(`  ok  ${name}`); }
```

（`selftest.mjs` 是 ESM，顶层 `await` 合法。）

然后在汇总循环之前追加：

```js
// ---- hotkeys.js ----
const { keyName, bindHotkeys } = await import("./src/shared/hotkeys.js");

test("keyName 归一：空格/方向键/转义/问号/ctrl 前缀", () => {
  const ev = (key, ctrlKey = false) => ({ key, ctrlKey });
  assert.equal(keyName(ev(" ")), "space");
  assert.equal(keyName(ev("ArrowDown")), "down");
  assert.equal(keyName(ev("ArrowUp")), "up");
  assert.equal(keyName(ev("ArrowLeft")), "left");
  assert.equal(keyName(ev("ArrowRight")), "right");
  assert.equal(keyName(ev("Escape")), "esc");
  assert.equal(keyName(ev("?")), "?");
  assert.equal(keyName(ev("X")), "x");
  assert.equal(keyName(ev("z", true)), "ctrl+z");
  assert.equal(keyName(ev("Z", true)), "ctrl+z");
  // Ctrl+Shift+组合键**不产生任何 keyMap 里存在的键名**（A-9）：Ctrl+Shift+Z 是
  // Windows 的「重做」肌肉记忆——本应用**没有重做**，落回 `ctrl+z` 会真的撤销一步
  // 且撤不回来（`replay` 不入栈）。两条都要有牙：前一条断「不落回 ctrl+z」，
  // 少了后一条，`keyName` 改成恒返回 "ctrl+z" 也照样绿。
  assert.notEqual(keyName(ev("Z", true, true)), "ctrl+z");
  assert.equal(keyName(ev("Z", true, true)), "ctrl+shift+z");
  // ⚠ 但别掐成「有 shift 就整体失效」：`?` 在真实按键里就是 shift+/（`e.key === "?"`、
  // ctrlKey 为 false），它必须照旧归一到 `?`，否则帮助窗的开关自己就废了。
  assert.equal(keyName(ev("?", false, true)), "?");
});

test("bindHotkeys：禁用态与输入框聚焦都不触发", () => {
  const fakeWin = { document: { activeElement: null },
                    addEventListener(t, f) { this.h = f; },
                    removeEventListener() { this.h = null; } };
  const hits = [];
  const off = bindHotkeys({ space: () => hits.push("space") },
                          { disabled: () => false, win: fakeWin });
  fakeWin.h({ key: " ", preventDefault() {} });
  assert.deepEqual(hits, ["space"]);
  // 输入框聚焦：不触发（否则打标签的 1-9 会被吞）
  fakeWin.document.activeElement = { tagName: "INPUT" };
  fakeWin.h({ key: " ", preventDefault() {} });
  assert.deepEqual(hits, ["space"]);
  // 禁用（如检测进行中）：不触发
  fakeWin.document.activeElement = null;
  off();
  const off2 = bindHotkeys({ space: () => hits.push("space") },
                           { disabled: () => true, win: fakeWin });
  fakeWin.h({ key: " ", preventDefault() {} });
  assert.deepEqual(hits, ["space"]);
  off2();
});

// ---- jobs.js ----
const { runJob, followJob, startJob } = await import("./src/shared/jobs.js");

test("runJob：拿 jobId → 轮询到 done → 取 result", async () => {
  let polls = 0;
  stubFetch((url) => {
    if (url === "/api/detect") return { body: { jobId: "abc123" } };
    if (url === "/api/jobs/abc123") {
      polls++;
      return { body: { id: "abc123", state: polls < 2 ? "running" : "done",
                       pct: polls * 50, msg: "…", done: polls >= 2 } };
    }
    if (url === "/api/jobs/abc123/result") return { body: { rows: [] } };
    throw new Error("意外请求：" + url);
  });
  const ticks = [];
  const res = await runJob("/api/detect", undefined, (j) => ticks.push(j.pct));
  assert.equal(res.rows.length, 0);
  assert.deepEqual(ticks, [50, 100]);
});

test("followJob：job 报 error 时抛后端文案", async () => {
  stubFetch((url) => (url === "/api/jobs/x1"
    ? { body: { id: "x1", state: "error", error: "内部错误：X", done: true } }
    : { body: {} }));
  await assert.rejects(() => followJob("x1"), /内部错误：X/);
});

test("startJob 回 jobId（页面要拿它记住任务，刷新后才能接上）", async () => {
  stubFetch(() => ({ body: { jobId: "j7" } }));
  assert.equal(await startJob("/api/detect"), "j7");
});

// ---- jobmemory.js ----
const { createJobMemory } = await import("./src/shared/jobmemory.js");

test("jobmemory：记住/读回/清空（存储不可用时静默 no-op）", () => {
  const mem = {};
  const fake = { getItem: (k) => (k in mem ? mem[k] : null),
                 setItem: (k, v) => { mem[k] = v; },
                 removeItem: (k) => { delete mem[k]; } };
  const m = createJobMemory(fake);
  assert.equal(m.load(), null);
  m.remember({ kind: "detect", jobId: "j1", videoPath: "D:/v/a.mp4" });
  assert.equal(m.load().jobId, "j1");
  m.clear();
  assert.equal(m.load(), null);
  // 存储抛异常（隐私模式/配额）不许把页面带崩
  const bad = { getItem: () => { throw new Error("boom"); },
                setItem: () => { throw new Error("boom"); },
                removeItem: () => { throw new Error("boom"); } };
  const m2 = createJobMemory(bad);
  m2.remember({ jobId: "j2" });
  assert.equal(m2.load(), null);
  m2.clear();       // 不抛
  // 记录写坏（他程序写的）当没有
  const m3 = createJobMemory({ getItem: () => "not json",
                               setItem() {}, removeItem() {} });
  assert.equal(m3.load(), null);
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && node selftest.mjs`
Expected: FAIL —— `keyName is not a function`（time.js/media.js 的 6 条仍绿）

- [ ] **Step 3: 实现 `hotkeys.js`**

```js
// 键盘流复核：输入框聚焦时一律不触发（否则打标签的 1-9 会被吞）。
// 弹窗打开时也不触发——用 disabled 开关由页面控制。
//
// 键名归一：e.key 的原始值不适合直接当映射键（空格是 " "、方向键是
// "ArrowDown"、Escape 是 "Escape"），页面里的映射表要一眼看懂动作。
// 归一后键名：space / up / down / left / right / esc / enter / ? / 字母 /
// 带前缀的组合键（ctrl+z）。
export function keyName(e) {
  const alias = { " ": "space", ArrowUp: "up", ArrowDown: "down",
                  ArrowLeft: "left", ArrowRight: "right",
                  Escape: "esc", Enter: "enter" };
  const base = alias[e.key] || String(e.key).toLowerCase();
  return (e.ctrlKey ? "ctrl+" : "") + base;
}

// win 默认挂在 globalThis 上（浏览器里就是 window）；注入假窗口即可在
// Node 里直测「输入框聚焦 / 禁用态不触发」这两条规则。
export function bindHotkeys(map, { disabled = () => false,
                                   win = globalThis } = {}) {
  const onKey = (e) => {
    const el = win.document && win.document.activeElement;
    const typing = el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA"
      || el.isContentEditable);
    if (typing || disabled()) return;
    const fn = map[keyName(e)];
    if (fn) { e.preventDefault(); fn(e); }
  };
  win.addEventListener("keydown", onKey);
  return () => win.removeEventListener("keydown", onKey);
}
```

- [ ] **Step 4: 实现 `jobs.js` 与 `jobmemory.js`**

`frontend/src/shared/jobs.js`：

```js
import { api } from "./api.js";

// 长任务轮询：POST 启动拿 jobId → 0.8s 一次 GET /api/jobs/{id}，done 为止。
// 用轮询不用 SSE：断线刷新后还能接着看，服务端实现也最简（单用户本机工具）。
export const POLL_MS = 800;

export async function startJob(startUrl, body) {
  const { jobId } = await api.post(startUrl, body);
  return jobId;
}

// 跟随一个 job 到终态并取结果。onTick 每轮拿一次 job 对象（{pct,msg,state,done}）。
// 单独拆出来是为了「刷新页面后接着跟随」：jobId 存在浏览器里，
// 重开页面用 followJob 接上，而不是重新起一个任务（会撞 409）。
export async function followJob(jobId, onTick, { intervalMs = POLL_MS } = {}) {
  for (;;) {
    const j = await api.get(`/api/jobs/${jobId}`);
    if (onTick) onTick(j);
    if (j.done) {
      if (j.state === "error") throw new Error(j.error || "任务失败");
      return await api.get(`/api/jobs/${jobId}/result`);
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

export async function runJob(startUrl, body, onTick) {
  return followJob(await startJob(startUrl, body), onTick);
}
```

`frontend/src/shared/jobmemory.js`：

```js
// 长任务的浏览器记忆：检测要跑几分钟，刷新页面不能把进度弄丢——重开后
// 用 JobMemory 里的 jobId 接上轮询（后端 job 只在内存里，重起服务会 404，
// 那时丢弃记录、提示重跑）。
//
// store 可注入（默认 localStorage）：Node 下没有 localStorage，直接
// 触碰会 ReferenceError；测试也靠注入假 store 跑。
const KEY = "bbh.runningJob";

function safeStorage() {
  try { return globalThis.localStorage || null; } catch { return null; }
}

export function createJobMemory(store = safeStorage()) {
  const read = (k) => {
    try { return store ? store.getItem(k) : null; } catch { return null; }
  };
  const write = (k, v) => {
    try { if (store) store.setItem(k, v); } catch { /* 配额/隐私模式：不影响功能 */ }
  };
  const drop = (k) => {
    try { if (store) store.removeItem(k); } catch { /* 同上 */ }
  };
  return {
    remember(rec) { write(KEY, JSON.stringify(rec)); },
    load() {
      const raw = read(KEY);
      if (!raw) return null;
      try {
        const rec = JSON.parse(raw);
        return rec && typeof rec === "object" && rec.jobId ? rec : null;
      } catch { return null; }        // 他程序写坏 → 当作没有
    },
    clear() { drop(KEY); },
  };
}
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd frontend && node selftest.mjs`
Expected: PASS —— `✅ 15 通过`（Task 1 实交 9 条 + 本任务 6 条）

- [ ] **Step 6: 提交**

```bash
git add frontend/src/shared/hotkeys.js frontend/src/shared/jobs.js frontend/src/shared/jobmemory.js frontend/selftest.mjs
git commit -m "feat(frontend): 共享层键盘归一 + 长任务跟随与浏览器记忆

keyName 把 e.key 归一成 space/up/down/left/right/esc/enter/?/ctrl+z，
页面映射表一眼看懂动作；bindHotkeys 可注入 win，两条规则（输入框聚焦、
禁用态不触发）在 Node 里直测。

jobs.js 拆出 startJob/followJob：刷新后用存下的 jobId 接上轮询，而不是
重新起任务（同类任务重起会 409）。jobmemory 的 store 可注入，存储不可用
时静默 no-op。"
```

**注（执行后同步，2026-09-19）**：本任务的 Step 里原本**漏了构建**（构建配方是 Task 4 起才写的），但共享层源码变更按 Global Constraints 仍要走一遍「改前端源码后必须重建」。实际执行时补做了 `npm run build` + 产物完整性 pytest，结果是**零变化**：本任务新增的三个模块**没有任何入口 import**（三个 `main.js` 只引 vue/element-plus/tabbar/session），rollup 摇树后产物必然不变——js/css 的 git hash 等于 HEAD blob，3 个 html 只差行尾。故提交里**不含** `bbh/static/**`（提交空 diff 无意义），构建后已 `git checkout -- bbh/static` 还原。**后续任务（Task 5 起）的源码会被入口 import，产物会真变，那时必须带上 `bbh/static/**` 一起提交。**

---

### Task 4: 写操作与撤销栈（`src/edit/actions.js`）

**Files:**
- Create: `frontend/src/edit/actions.js`
- Test: `frontend/selftest.mjs`

**Interfaces:**
- Consumes: `api`（`{get,post,patch,del}`，注入）、`store`（`{state,set,open}`，注入）
- Produces: `createActions({api, store, undoLimit}) → actions`：
  - `actions.depth`（getter，当前栈深）
  - `actions.openVideo(videoPath): Promise<state>`（清栈 + `store.open`）
  - `actions.setSelection(key, selection): Promise<state>`
  - `actions.setTag(key, tag): Promise<state>`（`""` = 清标签，**原样发空串**，见下方 `setTag` 的 ⚠）
  - `actions.addTag(name): Promise<state>`（**不入栈**）
  - `actions.toggleFlag(key, flag): Promise<state>`（`flag ∈ "thumb"|"cross"`）
  - `actions.markVerdict(key, verdict): Promise<state>`（`verdict ∈ "goal"|"not_goal"`）
  - `actions.deleteKeys(keys): Promise<state>`
  - `actions.restoreAll(): Promise<state>`（全量恢复，**不入栈**）
  - `actions.recordMiss(text): Promise<state>`（**不入栈**）
  - `actions.undo(): Promise<{label, state}|null>`（栈空回 `null`，不抛；`label` 给 Ctrl+Z 提示用）
  - `UNDO_LIMIT = 60`
- 撤销栈覆盖五类：**勾选 / 行标签 / 裁决 / 误标（⚠/✚ toggle） / 删除**。`addTag`（标签库新增）、`recordMiss`（漏检补录）、`restoreAll`（全量恢复）**不入栈**——后端都没有对应的反向端点，硬塞进去会做出「按了 Ctrl+Z 却什么也没撤销」的假撤销。

- [ ] **Step 1: 写失败的测试**

在 `frontend/selftest.mjs` 汇总循环之前追加（`fakeApi` 等辅助放在 `// ---- actions.js ----` 段内，本任务独占使用）：

```js
// ---- edit/actions.js ----
const { createActions, UNDO_LIMIT } = await import("./src/edit/actions.js");

// 假 api：actions.js 只依赖 {get,post,patch,del} 四个方法，真实现
// （src/shared/api.js 的 fetch 与错误改写）由 e2e 覆盖。这里专心钉住
// 「发什么请求、收到回包之后栈与 state 怎么变」。
// reply 可以是对象（每轮都回它）、函数(按调用返回)、或 Error（当请求失败）。
function fakeApi(reply) {
  const calls = [];
  const mk = (m) => async (url, body) => {
    calls.push({ m, url, body });
    const r = typeof reply === "function" ? reply(m, url, body) : reply;
    if (r instanceof Error) throw r;
    return r;
  };
  return { calls,
           api: { get: mk("GET"), post: mk("POST"),
                  patch: mk("PATCH"), del: mk("DELETE") } };
}

function fakeStore(state) {
  return {
    state, opened: [],
    set(s) { this.state = s; },
    async open(path) { this.opened.push(path); this.set({ rows: [], opened: path }); },
  };
}

// 一份够用的 state 夹具（只放 actions 会读的字段：rows/verdicts）
const stateWith = (over = {}) => ({
  rows: [{ key: "00:12.1", selection: false, tag: "" },
         { key: "01:12.1", selection: true, tag: "阿林" }],
  verdicts: {}, ...over,
});

test("setSelection：PATCH 行键、就地替换整份 state、撤销发回原值", async () => {
  const reply = stateWith();                       // 后端回包 = 权威行
  const { calls, api: fake } = fakeApi(reply);
  const store = fakeStore(stateWith());
  const act = createActions({ api: fake, store });

  await act.setSelection("00:12.1", true);
  assert.deepEqual(calls[0], { m: "PATCH", url: "/api/events/00%3A12.1",
                               body: { selection: true } });
  assert.equal(store.state, reply, "必须整份替换成回包对象，不自己改行");
  assert.equal(act.depth, 1);

  const un = await act.undo();
  assert.deepEqual(calls[1], { m: "PATCH", url: "/api/events/00%3A12.1",
                               body: { selection: false } });   // 撤销回原值
  assert.equal(un.label, "00:12.1 勾选", "Ctrl+Z 提示的 label 由行数据拼");
  assert.equal(act.depth, 0);
  assert.equal(await act.undo(), null, "栈空时按 Ctrl+Z 什么都不做，不许报错");
});

test("setTag：空串清标签（发空串，不是 null）；撤销发回原标签", async () => {
  const { calls, api: fake } = fakeApi(stateWith());
  const store = fakeStore(stateWith());
  const act = createActions({ api: fake, store });

  await act.setTag("01:12.1", "");
  assert.deepEqual(calls[0].body, { tag: "" },
                   "空串才是「清标签」；null 会被后端当成「这次不改标签」");
  await act.undo();
  assert.deepEqual(calls[1].body, { tag: "阿林" });
});

test("toggleFlag：⚠/✚ 是 toggle ⇒ 撤销 = 再点一次同样的 flag", async () => {
  const { calls, api: fake } = fakeApi(stateWith());
  const act = createActions({ api: fake, store: fakeStore(stateWith()) });

  await act.toggleFlag("00:12.1", "cross");
  assert.deepEqual(calls[0], { m: "POST", url: "/api/flags/00%3A12.1",
                               body: { flag: "cross" } });
  await act.undo();
  assert.deepEqual(calls[1].body, { flag: "cross" });
});

test("markVerdict 联动勾选（R16）：✅ 勾上、❌ 取消；只在勾选态不对时多发一个 PATCH", async () => {
  // 夹具：00:12.1 未勾选、01:12.1 已勾选
  async function run(curVerdicts, key, verdict) {
    const reply = stateWith();
    const { calls, api: fake } = fakeApi(reply);
    const store = fakeStore(stateWith({ verdicts: curVerdicts }));
    const act = createActions({ api: fake, store });
    await act.markVerdict(key, verdict);
    return { calls, store, reply };
  }
  // ① ❌ + 未勾选：勾选态本来就对 ⇒ 一次请求
  let r = await run({}, "00:12.1", "not_goal");
  assert.deepEqual(r.calls, [{ m: "POST", url: "/api/feedback/00%3A12.1",
                               body: { verdict: "not_goal" } }]);
  // ② ✅ + 未勾选：先勾上、再写反馈（**反馈回包是最后一份 state**，msg 才不丢）
  r = await run({}, "00:12.1", "goal");
  assert.deepEqual(r.calls.map((c) => [c.m, c.url]),
                   [["PATCH", "/api/events/00%3A12.1"],
                    ["POST", "/api/feedback/00%3A12.1"]]);
  assert.deepEqual(r.calls[0].body, { selection: true });
  assert.equal(r.store.state, r.reply, "最后落到 state 的必须是反馈回包");
  // ③ ❌ + 已勾选：先取消勾选、再写反馈
  r = await run({}, "01:12.1", "not_goal");
  assert.deepEqual(r.calls[0].body, { selection: false });
  assert.equal(r.calls[1].url, "/api/feedback/01%3A12.1");
});

test("markVerdict 撤销：裁决发回原值（原本无裁决=再发同值 ⇒ 后端取消），勾选还原", async () => {
  // 已勾选 + ❌ ⇒ 撤销要恢复勾选
  const { calls, api: fake } = fakeApi(stateWith());
  const act = createActions({ api: fake,
                              store: fakeStore(stateWith(
                                { verdicts: { "01:12.1": "goal" } })) });
  await act.markVerdict("01:12.1", "not_goal");   // calls 0: sel=false, 1: not_goal
  await act.undo();                               // 应：改判回 goal + 勾选还原
  assert.deepEqual(calls[2].body, { verdict: "goal" });
  assert.deepEqual(calls[3].body, { selection: true });
  assert.equal(await act.undo(), null);

  // 无原裁决 + ✅ ⇒ 撤销再发一次 goal（后端取消）并把勾选还原
  const r2 = fakeApi(stateWith());
  const act2 = createActions({ api: r2.api, store: fakeStore(stateWith()) });
  await act2.markVerdict("00:12.1", "goal");
  await act2.undo();
  assert.deepEqual(r2.calls[2].body, { verdict: "goal" });
  assert.deepEqual(r2.calls[3].body, { selection: false });
});

test("deleteKeys 撤销 = 按 key 恢复（不是全量恢复）", async () => {
  const { calls, api: fake } = fakeApi(stateWith());
  const act = createActions({ api: fake, store: fakeStore(stateWith()) });

  await act.deleteKeys(["00:12.1", "01:12.1"]);
  assert.deepEqual(calls[0], { m: "DELETE", url: "/api/events",
                               body: { keys: ["00:12.1", "01:12.1"] } });
  const un = await act.undo();
  assert.deepEqual(calls[1], { m: "POST", url: "/api/events/restore",
                               body: { keys: ["00:12.1", "01:12.1"] } });
  assert.equal(un.label, "批量删除 2 球");
});

test("restoreAll 全量恢复且不入栈；漏检/标签库新增同样不入栈", async () => {
  const { calls, api: fake } = fakeApi(stateWith());
  const act = createActions({ api: fake, store: fakeStore(stateWith()) });

  await act.restoreAll();
  assert.deepEqual(calls[0], { m: "POST", url: "/api/events/restore", body: {} });
  await act.recordMiss("12:34");
  assert.deepEqual(calls[1], { m: "POST", url: "/api/events/miss",
                               body: { text: "12:34" } });
  await act.addTag("阿林");
  assert.deepEqual(calls[2], { m: "POST", url: "/api/tags",
                               body: { name: "阿林" } });
  assert.equal(act.depth, 0, "没有反向端点的动作不进撤销栈");
});

test("撤销栈上限 60：最老的被挤掉", async () => {
  const { api: fake } = fakeApi(stateWith());
  const act = createActions({ api: fake, store: fakeStore(stateWith()) });
  for (let i = 0; i < UNDO_LIMIT + 5; i++) await act.setSelection("00:12.1", i % 2 === 0);
  assert.equal(act.depth, UNDO_LIMIT);
  for (let i = 0; i < UNDO_LIMIT; i++) assert.ok(await act.undo());
  assert.equal(await act.undo(), null);
});

test("请求失败：state 不动、栈不涨、错误原文抛给调用方", async () => {
  const err = new Error("当前列表里没有这条球：99:99.9（可能已删除或重跑过检测）");
  const { api: fake } = fakeApi(err);
  const before = stateWith();
  const store = fakeStore(before);
  const act = createActions({ api: fake, store });

  await assert.rejects(() => act.setSelection("99:99.9", true),
                       /当前列表里没有这条球/);
  assert.equal(store.state, before);
  assert.equal(act.depth, 0);
  assert.equal(await act.undo(), null);
});

test("撤销失败：记录留在栈里（可重试），state 不动", async () => {
  const before = stateWith();
  const store = fakeStore(before);
  let fail = false;
  const { api: fake } = fakeApi(() => (fail ? new Error("网络断了") : before));
  const act = createActions({ api: fake, store });

  await act.setSelection("00:12.1", true);
  assert.equal(act.depth, 1);
  fail = true;
  await assert.rejects(() => act.undo(), /网络断了/);
  assert.equal(act.depth, 1, "撤销失败后记录必须还在栈里（用户再按一次 Ctrl+Z 还能重试）");
  fail = false;
  const un = await act.undo();
  assert.equal(un.label, "00:12.1 勾选");
  assert.equal(act.depth, 0);
});

test("openVideo 先清栈再开会话（换视频后不许拿旧 key 撤销）", async () => {
  const { calls, api: fake } = fakeApi(stateWith());
  const store = fakeStore(stateWith());
  const act = createActions({ api: fake, store });

  await act.setSelection("00:12.1", true);
  assert.equal(act.depth, 1);
  await act.openVideo("D:/videos/b.mp4");
  assert.deepEqual(store.opened, ["D:/videos/b.mp4"]);
  assert.equal(act.depth, 0);
  assert.equal(calls.length, 1, "openVideo 走 store.open，不自己发 session 请求");
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && node selftest.mjs`
Expected: FAIL —— `Cannot find module …/src/edit/actions.js`

- [ ] **Step 3: 实现 `actions.js`**

```js
// 剪辑台的全部写操作。一次用户操作 = 一个请求 + **就地替换整份 state**
// （服务端回包是权威值，前端不自己算任何统计）+ 一条可撤销记录。
//
// 纯 JS、依赖注入（api / store 由页面传入）：逻辑能在 Node 里直测，不必挂
// 浏览器；组件只负责调用与渲染。
//
// 撤销栈覆盖五类：勾选 / 行标签 / 裁决 / 误标（⚠/✚ toggle）/ 删除。标签库新增、漏检补录、
// 全量恢复不入栈——后端都没有对应的反向端点（见各方法注释）。
export const UNDO_LIMIT = 60;

// 行键（"00:12.1"）进 URL 路径：冒号在路径段里合法，但仍按规范编码，
// 免得将来出现别的字符时踩坑（Starlette 会先解码再匹配路由）。
const K = (key) => encodeURIComponent(key);

export function createActions({ api, store, undoLimit = UNDO_LIMIT }) {
  const stack = [];
  const rowOf = (key) =>
    (store.state?.rows || []).find((r) => r.key === key) || null;

  // 用户操作：发请求 → 换 state → **成功才入栈**（失败的操作不占栈位）。
  // label 给 Ctrl+Z 的提示用（「已撤销：{label}」）——它由**行数据**拼出，
  // 不是服务端文案；栈里只留能一笔说清的那几类（见各方法）。
  async function once(p, undo, label = "") {
    const data = await p;
    store.set(data);
    if (undo) {
      stack.push({ undo, label });
      if (stack.length > undoLimit) stack.shift();
    }
    return data;
  }

  // 撤销走这条：同样发请求 + 换 state，但不再入栈
  async function replay(p) {
    const data = await p;
    store.set(data);
    return data;
  }

  // 裸调用（不含入栈逻辑）：参数形状见 backend-contract.md 的端点表
  const raw = {
    selection: (key, selection) =>
      api.patch(`/api/events/${K(key)}`, { selection }),
    tag: (key, tag) => api.patch(`/api/events/${K(key)}`, { tag: tag || "" }),
    verdict: (key, verdict) => api.post(`/api/feedback/${K(key)}`, { verdict }),
    flag: (key, flag) => api.post(`/api/flags/${K(key)}`, { flag }),
    del: (keys) => api.del("/api/events", { keys }),
    // 不传 keys = 后端原全量语义（R5）
    restore: (keys) => api.post("/api/events/restore", keys ? { keys } : {}),
    miss: (text) => api.post("/api/events/miss", { text }),
    tagLib: (name) => api.post("/api/tags", { name }),
  };

  return {
    get depth() { return stack.length; },

    // 换视频：栈里的键全是上一个视频的（同一时刻串在新视频里通常也存在），
    // 留着按 Ctrl+Z 会对着新视频写错数据。所以一律先清栈。
    async openVideo(videoPath) {
      stack.length = 0;
      return store.open(videoPath);
    },

    // 行勾选（表格复选框）
    setSelection(key, selection) {
      const row = rowOf(key);
      const before = row?.selection;
      return once(raw.selection(key, selection),
                  before === undefined ? null
                                       : () => replay(raw.selection(key, before)),
                  `${row?.t || key} ${selection ? "勾选" : "取消勾选"}`);
    },

    // 行标签（下拉；""= 无标签 ⇒ 原样发空串）。
    // ⚠ 别发 `null`：后端 `patch_event` 用 `is not None` 判分支
    // （`api_events.py:102`），`{"tag": null}` 会被当成「这次不改标签」直接
    // 跳过 → 200 空操作，「清标签」与「撤销打标签」两条动作静默失效；后端
    // 自己的用例 `test_blank_tag_clears_it_without_unchecking` 钉的是 `{"tag": ""}`。
    // 后端 `set_row_tag` 的既有语义（`app.py:1195`）：打上**非空**标签会
    // 顺手自动勾选该行，而清空标签**不**取消勾选 ⇒ 撤销「打标签」会把
    // 那个自动勾选留在那儿，撤销不还原勾选。与旧界面同病，不修。真正不可逆
    // 的是 `set_row_tag` 里的 `_sync_manual_from_tag_edits`（改判已归人球 +
    // 写归人 sidecar），没有反向端点；勾选本身其实能补（`markVerdict` 的撤销
    // 就是额外发一次 `PATCH {selection}`），补不了的是那次改判。
    setTag(key, tag) {
      const before = rowOf(key)?.tag || "";
      return once(raw.tag(key, tag), () => replay(raw.tag(key, before)),
                  tag ? `打标签 ${tag}` : "清标签");
    },

    // 标签库新增（POST /api/tags）：后端没有删标签端点 ⇒ 不入栈
    addTag(name) { return replay(raw.tagLib(name)); },

    // ⚠ 缩略图有误 / ✚ 十字有误：后端是 toggle（`app._toggle_mark_flag`）
    // ⇒ 逆操作就是再点一次
    toggleFlag(key, flag) {
      return once(raw.flag(key, flag), () => replay(raw.flag(key, flag)),
                  flag === "thumb" ? "⚠ 缩略图有误" : "✚ 十字有误");
    },

    // 人工裁决：同按钮再点 = 取消，点另一个 = 改判（`app.mark_verdict`）。
    // 撤销时——原本没有裁决 → 再发同一个 verdict（后端取消它）；
    // 原本是另一种裁决 → 发回原值（后端改判回去）。
    // 靠 state.verdicts 才知道「原本是什么」（R4 下发这个字段的原因之一）。
    //
    // 勾选联动（裁定 R16）：✅ 翻案 = 认定进球 ⇒ 勾上；❌ 否决 ⇒ 取消勾选
    // （原型的裁决按钮就是这个行为，用户逐轮审核时批的就是它）。
    // 后端 `POST /api/feedback/{key}` 只写 feedback.json、**不动
    // selection.json**（`mark_verdict` 不碰勾选），所以勾选态不对时补一个
    // PATCH——正常路径（❌ 一颗没勾的球、✅ 一颗已勾的球）仍是一次请求。
    // 顺序固定「先勾选、后反馈」：反馈回包带 msg、且是最后落到 state 的
    // 那一份，toast 才不丢。
    markVerdict(key, verdict) {
      const before = (store.state?.verdicts || {})[key];
      const wasSel = !!rowOf(key)?.selection;
      const wantSel = verdict === "goal";
      const touchSel = wasSel !== wantSel;
      const doIt = async () => {
        if (touchSel) await raw.selection(key, wantSel);
        return raw.verdict(key, verdict);
      };
      const undoIt = async () => {
        await replay(raw.verdict(key, before || verdict));
        if (touchSel) await replay(raw.selection(key, wasSel));
      };
      // label：同按钮再点 = 取消（`before === verdict`），否则是翻案/否决
      const label = before === verdict ? "取消反馈"
        : verdict === "goal" ? "✔ 翻案反馈" : "❌ 否决反馈";
      return once(doIt(), undoIt, `${rowOf(key)?.t || key} ${label}`);
    },

    // 批量删除；撤销 = 按 key 恢复（R8：恢复的球回到未勾选/未归人）
    deleteKeys(keys) {
      const n = (keys || []).length;
      const label = n === 1 ? `删除 ${rowOf(keys[0])?.t || keys[0]}`
                            : `批量删除 ${n} 球`;
      return once(raw.del(keys), () => replay(raw.restore(keys)), label);
    },

    // ⋯「↩ 恢复已删」= 全量恢复。独立动作，且无法反向（没有「再删回去」
    // 的端点）⇒ 不入栈。
    restoreAll() { return replay(raw.restore(null)); },

    // 漏检补录：只进 feedback.misses，不产生表格行（R6）；没有删除端点 ⇒ 不入栈
    recordMiss(text) { return replay(raw.miss(text)); },

    // Ctrl+Z：弹栈执行，回 {label, state}；栈空回 null（不抛——用户多按
    // 几次不该看到报错）。
    async undo() {
      const top = stack[stack.length - 1];
      if (!top) return null;
      // 先撤销、成功了才出栈：撤销请求失败（断网/后端 500）时记录留着，
      // 用户再按一次 Ctrl+Z 还能重试——直接 pop 会把这一步永久吞掉。
      const state = await top.undo();
      stack.pop();
      return { label: top.label, state };
    },
  };
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && node selftest.mjs`
Expected: PASS —— `✅ 28 通过`（Task 3 实交 17，本任务 +11）

- [ ] **Step 5: 提交**

```bash
git add frontend/src/edit/actions.js frontend/selftest.mjs
git commit -m "feat(frontend): 剪辑台写操作与 60 步撤销栈（纯 JS，Node 直测）

一次用户操作 = 一个请求 + 就地替换整份 state + 成功才入栈的一条撤销记录。
撤销覆盖勾选/行标签/裁决/误标/删除；标签库新增、漏检补录、全量恢复不入栈
（后端没有反向端点，硬塞会做成「按了 Ctrl+Z 什么也没撤销」的假撤销）。

两处靠后端语义定形：裁决是同按钮取消/异按钮改判（撤销据此发回原值），
⚠/✚ 标记是 toggle（撤销 = 再点一次）。openVideo 先清栈——旧视频的行键
在新视频里通常也存在，留着会对着新视频写错数据。"
```

---

## 前端任务的验证配方（Task 5 起，每个前端任务收尾都用它）

命令都在**仓库根**跑（git-bash）。五步：构建 → 临时服务 → 冒烟 → 带数据 e2e → 产物 pytest。

> ⚠ **A（构建）必须先于 C2（e2e）**，两者不能颠倒。C2 打的是 `bbh/static` 的**产物**，与 `frontend/src` 无关：`bbh/web/app.py:23` 的 `STATIC_DIR` 就是它、`:80` 用 `StaticFiles(directory=STATIC_DIR, html=True)` 挂上去，而 `frontend/vite.config.js` 的 `outDir` 也指向它。**改完 `frontend/src` 不先 `npm run build`，e2e 会红在 `waitFor('#app .videobox video')` 这种与本段断言毫无关系的地方**，把实现者往「右栏没渲染」的方向引（Task 8 实测踩过）。
> 这也意味着**任务 Step 里那句「Step N：跑 e2e，Expected: PASS」必须发生在构建之后**——若 Step 顺序把构建排在 e2e 之后，那句 PASS 按字面执行不到。想验「断言有牙」，正确的做法是**构建之后用反事实**（临时去掉实现）让它红，而不是靠「还没构建」来红。

**A. 构建**

```bash
cd frontend && npm run build && cd ..
```

产物直接覆盖 `bbh/static/`（`vite.config.js` 的 `outDir` + `emptyOutDir: true`；那里只有构建产物，清空安全）。

**B. 起临时服务**（一个终端里常驻，后续任务复用；换任务不必重启）

```bash
./.venv/Scripts/python.exe -c "import uvicorn; from bbh.web.app import create_app; uvicorn.run(create_app(), host='127.0.0.1', port=7861, log_level='warning')"
```

用**后台方式**起（记下 task id，收工按 task id 停，绝不用 `taskkill //IM`）。**永远不要**用 `python -m bbh.web`——它的 `__main__` 会把仓库根的标定档搬进 `work/<视频>/`，而根那 5 个档没有 git 副本。端口用 7861：**7860 是用户自己的实例**，不杀、不占、不探活。

⚠️ **这个 7861 服务的 cwd 是仓库根，所以它是「只读探针」**：`work/<视频>/`、`ui_prefs.json`（`bbh/web/api_session.py:25`）、`tags.json`（`bbh/app.py:35`）三者都是**裸相对路径**，按 cwd 解析 —— 在这个服务上开一次会话就会写用户的 `ui_prefs.json`，打一个标签就会写用户的 `tags.json`（那是红线里没有 git 副本的档）。所以它只用来跑**只看不写**的冒烟（配方 C：开三页、点 tab、查报错）。**任何带数据的验证（勾选/打标/裁决/删除）一律走配方 C2 的临时目录服务**，不要用 7861。

**C. 冒烟**

```bash
cd frontend && npm i -D --no-save playwright && cd ..   # 首次；Chromium 已在 %LOCALAPPDATA%\ms-playwright
BBH_BASE=http://127.0.0.1:7861 node frontend/smoke.mjs
```

`--no-save` 不写进 `package.json`（`node_modules/` 已被 git 忽略）。装不上（离线）就**如实报 DONE_WITH_CONCERNS**，不要跳过验证当成功。

**C2. 页级 e2e（带真数据、真服务、真写盘）** —— Task 6 建好 `tests/e2e/` 之后，每个 UI 任务都跑：

```bash
./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/e2e/test_edit_bay_e2e.py
```

它自己在**临时目录**里造夹具（合成视频 + `work/<名>/` 的 events/标定/缩略图）并以 `cwd=临时目录` 起一个**随机空闲端口**的 uvicorn，然后把 `tests/e2e/edit_bay.spec.mjs` 交给 Playwright 跑。上面那条「三者都是相对路径」的坑在这里反而是隔离机制：`work/`、`ui_prefs.json`、`tags.json` 全落在临时目录，**用户的 `work/` 与仓库根 5 个档一个字节都不碰**，而且测试能随便勾选/打标/删除/撤销——那些副作用正是要验的东西。Node 或 playwright 缺失时**跳过并说明**（不假绿）。

**D. pytest（只跑相关用例，不跑全量）**

```bash
./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/test_web_static_assets.py
```

**E. 提交**：源码 + `bbh/static/**` 一起提交（不重建就从 git 里看不到改动，而用户运行时不装 Node）。

> ⚠ **`git ls-files --eol` 这条纪律检查必须限定到本次改动的路径**，否则它会给出一个**假信号**。本仓 `core.autocrlf=true`（实测），于是**全仓**范围内 `git ls-files --eol | grep -v 'w/lf'` 有 **71 条**（`bbh/*.py`、`README.md`、`frontend/package.json`、`tests/test_*.py` …都在内），与任何一次前端改动都无关。正确用法是带上路径，例如
> `git ls-files --eol -- frontend/src tests/e2e bbh/static | grep -v 'w/lf'`（**应无输出**）。
> Task 8 修复轮 1 的复审按字面跑全仓、看到 71 条，**差点把它报成「换行符纪律破了」**——本条写下来是为了让 Task 9/10/11 的复审不掉进同一个坑。

---

### Task 5: 剪辑台页骨架（挂 SFC + 会话载入 + 空态）

**Files:**
- Create: `frontend/src/edit/EditBay.vue`
- Modify: `frontend/src/edit/main.js`、`frontend/src/shared/session.js`、`frontend/smoke.mjs`
- Modify: `bbh/static/**`（配方 A 的产物）

**Interfaces:**
- Consumes: `store`（`session.js`）、`api`（`api.js`）、`createActions`（Task 4）
- Produces: 挂到 `#app` 的 `<EditBay/>`；页内后续任务要用的地标与容器：`.topbar`（顶栏）、`.main > .left`（左栏）、`.main > .right`（右栏，**唯一纵向滚动容器**）；`state` / `act` / `rows` 三个 `setup` 作用域变量供后续任务就地扩展

- [ ] **Step 1: 先改 smoke 的地标（此刻必红）**

`frontend/smoke.mjs` 里那段 `for (const [file, label, heading] of [...])` 循环：剪辑台页从 Plan B 起主体是 Vue SFC、**页内不再有 `h2`**，地标换成顶栏的路径输入框。把循环开头的数组与 h2 断言替换为：

```js
  // 三页地标：剪辑台（Plan B）是 SFC 主体，页内没有 h2 ⇒ 地标取顶栏的
  // 路径输入框；出手人墙/标定页仍是 Plan A 的骨架（h2 标题），等 Plan C/D。
  // 两者都只有 app.mount() 真跑成功才会出现——地标换掉，这条约束不换。
  for (const [file, label, heading] of [["edit.html", "剪辑台", null],
                                        ["wall.html", "出手人墙", "出手人墙"],
                                        ["calib.html", "标定设置", "标定设置"]]) {
    await page.goto(`${BASE}/${file}`, { waitUntil: "domcontentloaded" });
    await page.locator(".tabbar a.on").waitFor();

    if (heading) {
      const head = page.locator("#app h2");
      if (!(await head.count())) {
        throw new Error(`${file}: Vue 没挂载——#app 里没有 h2（只有 tabbar），app.mount 没跑成功`);
      }
      const h = (await head.textContent()).trim();
      if (h !== heading) throw new Error(`${file}: 页面标题错，期望「${heading}」实际「${h}」`);
    } else {
      const input = page.locator("#app .topbar input");
      if (!(await input.count())) {
        throw new Error(`${file}: Vue 没挂载——#app 里没有 .topbar（app.mount 没跑成功）`);
      }
      const ph = await input.first().getAttribute("placeholder");
      if (!(ph || "").includes("填路径自动载入")) {
        throw new Error(`${file}: 剪辑台地标不对，路径输入框 placeholder=「${ph}」`);
      }
    }
```

（后面 tab 高亮 / tab 数 / `top === 0` 三段落原样保留。）

- [ ] **Step 2: 跑一次确认失败**

先按配方 **B** 起临时服务，再按配方 **C** 跑冒烟。
Expected: FAIL —— `edit.html: Vue 没挂载——#app 里没有 .topbar（app.mount 没跑成功）`（此刻产物还是 Plan A 的骨架页，页内只有 `h2`）。这条红证明了地标确实是新页面才有的东西。

- [ ] **Step 3: `session.js` 加 `off`**

```js
  on(f) { this.listeners.add(f); },
  off(f) { this.listeners.delete(f); },
```

（组件卸载要摘监听，不然热更新/路由切换会攒下一堆死回调。）

- [ ] **Step 4: 新建 `EditBay.vue`**

```vue
<script setup>
// 剪辑台主体（spec 3）：顶栏 + 左栏（时间轴/表格/页脚）+ 右栏（播放器/
// 判定依据/按钮）+ 四个弹窗。本任务先落地骨架、会话载入与空态。
//
// 数据流：全部来自服务端 state（Plan A 的 state_payload），**前端不算统计**;
// 写操作一律走 actions（撤销栈在里面），回包就地替换整份 state。
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { ElMessage } from "element-plus";
import { api } from "../shared/api.js";
import { store } from "../shared/session.js";
import { createActions } from "./actions.js";

const state = ref(null);
const pathInput = ref("");

// api/store 用真实现；撤销栈、写操作形状都在 actions.js 里（Task 4）
const act = createActions({ api, store });

const loaded = computed(() => !!state.value?.loaded);
const hint = computed(() => state.value?.hint || "");
const rows = computed(() => state.value?.rows || []);

// 档位/勾选计数是 rows[] 的派生视图（同一个权威数组）；**反馈计数与已删
// 计数不在这里算**——那两个有独立权威源（state.feedback / state.deleted）。
const cnt = computed(() => {
  const c = { high: 0, review: 0, low: 0, checked: 0 };
  for (const r of rows.value) {
    if (r.status in c) c[r.status] += 1;
    if (r.selection) c.checked += 1;
  }
  return c;
});
const checkedPct = computed(() =>
  rows.value.length ? Math.round((cnt.value.checked / rows.value.length) * 100) : 0);

function onState(s) {
  state.value = s;
  // 只在输入框还空着时回填：用户正在改路径时别被别的请求顶掉
  if (!pathInput.value) pathInput.value = s.videoPath || s.lastVideo || "";
}

// 路径输入框：@change = 回车或失焦（el-input 的语义）。
// 不用 watch 逐字触发——那会每敲一个字符发一次 POST /api/session，
// 并把 ui_prefs.json 的 lastVideo 写成半截路径。
async function onPathChange() {
  const p = pathInput.value.trim();
  if (!p || p === state.value?.videoPath) return;
  try { await act.openVideo(p); } catch (e) { ElMessage.error(e.message); }
}

onMounted(async () => {
  store.on(onState);
  try { await store.refresh(); } catch (e) { ElMessage.error(e.message); }
});
onBeforeUnmount(() => store.off(onState));
</script>

<template>
  <div class="editbay">
    <div class="topbar">
      <el-input v-model="pathInput" style="width: 360px"
                placeholder="D:\videos\2026-09-11_3.mp4 —— 填路径自动载入已有结果"
                @change="onPathChange">
        <template #prefix><el-icon><Folder /></el-icon></template>
      </el-input>
    </div>

    <div class="main">
      <div class="left"></div>
      <div class="right">
        <div v-if="!loaded" class="stage-hint">{{ hint }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 尺寸与配色逐条照 .superpowers/sdd/2026-09-19-frontend-refactor-edit-bay/
   proto-edit-inventory.md §1 区域骨架 / §9 视觉常量（深色为默认） */
.editbay { display: flex; flex-direction: column; height: calc(100vh - 40px); }
.topbar { flex: none; display: flex; align-items: center; gap: 10px;
          padding: 10px 18px; background: #1a1a1a; border-bottom: 1px solid #333; }
.main { flex: 1; display: flex; min-height: 0; }
.left { width: 44%; min-width: 480px; display: flex; flex-direction: column;
        min-height: 0; border-right: 1px solid #333; background: #181818; }
.right { flex: 1; min-width: 0; overflow-y: auto; background: #161616; }
.stage-hint { padding: 14px 16px; color: #8d9098; font-size: 13px; }
</style>
```

- [ ] **Step 5: `main.js` 换成挂 SFC**

```js
import { createApp } from "vue";
import ElementPlus from "element-plus";
import "element-plus/dist/index.css";
import "element-plus/theme-chalk/dark/css-vars.css";
import * as Icons from "@element-plus/icons-vue";
import "../shared/theme.css";
import { mountTabbar } from "../shared/tabbar.js";
import EditBay from "./EditBay.vue";

mountTabbar("edit.html", "");
const app = createApp(EditBay);
app.use(ElementPlus);
for (const [k, c] of Object.entries(Icons)) app.component(k, c);
app.mount("#app");
```

（`mountTabbar` 的提示语从「骨架页——页面内容在 Plan B 实现」清空：页面已经是真页面。）

- [ ] **Step 6: 构建 + 冒烟**

配方 **A** 构建，配方 **C** 冒烟。
Expected: PASS —— `✅ 冒烟通过：三页可开、Vue 真挂载、tab 高亮正确、可跳转、无 JS 报错`

- [ ] **Step 7: pytest 产物完整性**

配方 **D**。
Expected: PASS

- [ ] **Step 8: 提交**

```bash
git add frontend/src/edit/EditBay.vue frontend/src/edit/main.js \
        frontend/src/shared/session.js frontend/smoke.mjs bbh/static
git commit -m "feat(frontend): 剪辑台页骨架挂 SFC + 会话载入与空态

main.js 从 template 字符串骨架换成挂 EditBay.vue；页面顶栏落路径输入框
（@change = 回车/失焦时 POST /api/session，不逐字 watch），右栏空态直接用
服务端下发的 hint（五种文案已由 probe_existing 给全，前端不自己 union）。

smoke 的剪辑台地标从 #app h2 换成 #app .topbar input——页内没有 h2 了，
但「只有 app.mount() 真跑成功才会出现」这条约束不换。"
```

---

### Task 6: 表格 + 筛选 + 时间轴标记条 + 页脚

**Files:**
- Create: `frontend/src/edit/EventTable.vue`、`frontend/src/edit/TimelineStrip.vue`、`frontend/src/edit/tiers.js`
- Create: `tests/e2e/test_edit_bay_e2e.py`、`tests/e2e/edit_bay.spec.mjs`（页级 e2e：起一个 cwd=临时目录的临时服务，隔离用户真档）
- Modify: `frontend/src/edit/EditBay.vue`（左栏：工具条 + 时间轴 + 表格 + 页脚）
- Modify: `bbh/static/**`（配方 A 的产物）

**Interfaces:**
- Consumes: `act`（Task 4）、`tsec`/`dispT`（Task 1）
- Produces:
  - `tiers.js`：`TIER`（`{high,review,low}` → `{type, text}`）、`tierOf(row)`、`tierText(row)`
  - `<EventTable :rows :tags :verdicts :empty-text :offset @pick @check @tag>`，`defineExpose({ setCurrentRow(key) })`（Task 7 键盘流用）
  - `<TimelineStrip :rows :duration :verdicts :offset @pick>`
  - `EditBay` 内：`visibleRows`、`curKey`、`write(p)`（统一收尾：成功弹服务端 `msg`、失败弹 `e.message`）
  - **e2e 通道**：`tests/e2e/test_edit_bay_e2e.py`（夹具 + 临时服务 + 跑 spec）与 `tests/e2e/edit_bay.spec.mjs`（断言）。Task 7–11 都**往同一个 spec 追加断言段**、复用这个夹具，不再各自造临时脚本；spec 里可用的工具：`ok(cond,msg)` / `eq(a,b,msg)` / `state()` / `waitFor(fn,msg)`，`page` 变量在 `try` 块里。

- [ ] **Step 1: 新建 `tiers.js`**

```js
// 档位文案与配色的唯一来源（表格的 el-tag 与时间轴 dot 共用）。
// `dot` 是时间轴标记点的填充色：放在这里，时间轴就不再自己写死字面量——
// 否则未知档位会掉回时间轴自己的第三色，而它的 tooltip 却写着「⚪ 低」
// （同一处两个结论）。table 的 el-tag 用 `type`，时间轴用 `dot`。
// 没有 `manual`（⚠ 漏检）档：那是原型自造的行，后端的漏检补录只进
// feedback.misses、**不产生表格行**（裁定 R6）。
export const TIER = {
  high: { type: "success", text: "🟢 高", dot: "#67C23A" },
  review: { type: "warning", text: "🟡 待", dot: "#E6A23C" },
  low: { type: "info", text: "⚪ 低", dot: "#909399" },
};

// 未知档位（后端将来新增，或旧数据）一律按 ⚪ 低显示，不抛错也不显示空
export const tierOf = (row) => TIER[row?.status] || TIER.low;
export const tierText = (row) => tierOf(row).text;
```

- [ ] **Step 2: 新建 `EventTable.vue`**

```vue
<script setup>
// 表格：7 列（缩略图/进片/时刻/档位/标签/裁决/置信度）。
// 行对象是服务端下发的权威值——**勾选与标签不回写本地行**，一律发请求、
// 用回包整份替换（actions.js 负责），否则界面上会先出现一个服务端并不
// 认可的状态，刷新就打脸。
import { ref } from "vue";
import { dispT } from "../shared/time.js";
import { tierOf } from "./tiers.js";

const props = defineProps({
  rows: { type: Array, required: true },              // 已按筛选过滤
  tags: { type: Array, default: () => [] },           // state.tags（全局标签库）
  verdicts: { type: Object, default: () => ({}) },    // state.verdicts（裁决列）
  emptyText: { type: String, default: "" },
  offset: { type: Number, default: 0 },               // 校准偏移（Task 9 传入）
});
const emit = defineEmits(["pick", "check", "tag"]);

const tableRef = ref(null);

// 行态：goal / not_goal 来自 state.verdicts（键 = row.key）。
// 原型还有 `.row-deleted`——真实现里已删的球**根本不在 rows[] 里**
// （state.deleted 只存键），那条样式永远不可达，不实现。
function rowClass({ row }) {
  const v = props.verdicts[row.key];
  if (v === "goal") return "row-goal";
  if (v === "not_goal") return "row-miss";
  return "";
}

const confColor = (c) => (c >= 0.8 ? "#67C23A" : c >= 0.4 ? "#E6A23C" : "#909399");

// 键盘流（Task 7）要能按 key 选中行：转成 el-table 的 setCurrentRow，
// 它会触发 current-change → 我们再 emit('pick')，父组件照常收到
function setCurrentRow(key) {
  const row = props.rows.find((r) => r.key === key);
  if (row && tableRef.value) tableRef.value.setCurrentRow(row);
}
defineExpose({ setCurrentRow });
</script>

<template>
  <el-table ref="tableRef" :data="rows" height="100%" size="small" border
            highlight-current-row :row-key="(r) => r.key"
            :row-class-name="rowClass" :empty-text="emptyText"
            @current-change="(r) => r && emit('pick', r.key)">
    <el-table-column label="" width="52" align="center">
      <template #default="{ row }">
        <img v-if="row.thumb" :src="row.thumb" class="rowthumb"
             :class="{ dim: !row.selection }"
             :title="row.selection ? '已勾选进片' : '未勾选'">
        <span v-else class="thumb-na">—</span>
      </template>
    </el-table-column>

    <el-table-column label="进片" width="54" align="center">
      <template #default="{ row }">
        <el-checkbox :model-value="row.selection"
                     @change="(v) => emit('check', row.key, v)" />
      </template>
    </el-table-column>

    <!-- 时刻列用服务端的 t 串（dispT：offset=0 原样用，非 0 才重算）。
         零填充的 mm:ss.s 字典序 = 时间序，所以 sortable prop="t" 直接可用 -->
    <el-table-column label="时刻" width="84" sortable prop="t">
      <template #default="{ row }">{{ dispT(row, offset) }}</template>
    </el-table-column>

    <el-table-column label="档位" width="86">
      <template #default="{ row }">
        <el-tag size="small" effect="dark" :type="tierOf(row).type">{{ tierOf(row).text }}</el-tag>
      </template>
    </el-table-column>

    <el-table-column label="标签">
      <template #default="{ row }">
        <el-select :model-value="row.tag || ''" size="small" clearable filterable
                   allow-create placeholder="打标签" style="width: 100%"
                   @change="(v) => emit('tag', row.key, v || '')">
          <el-option v-for="t in tags" :key="t" :label="t" :value="t" />
        </el-select>
      </template>
    </el-table-column>

    <el-table-column label="裁决" width="58" align="center">
      <template #default="{ row }">
        <span v-if="verdicts[row.key] === 'goal'" class="mark-yes">✔</span>
        <span v-else-if="verdicts[row.key] === 'not_goal'" class="mark-no">✘</span>
        <span v-else class="mark-na">—</span>
      </template>
    </el-table-column>

    <el-table-column label="置信度" width="96">
      <template #default="{ row }">
        <el-progress :percentage="Math.round(row.conf * 100)" :stroke-width="6"
                     :color="confColor(row.conf)" />
      </template>
    </el-table-column>
  </el-table>
</template>

<style scoped>
.rowthumb { display: block; width: 38px; height: 30px; margin: 0 auto;
            border-radius: 4px; }
.rowthumb.dim { opacity: .45; }
.thumb-na { color: #4a4a4a; font-size: 11px; }
.mark-yes { color: #67C23A; font-size: 16px; }
.mark-no { color: #f56c6c; font-size: 16px; }
.mark-na { color: #4a4a4a; }
:deep(.el-table .row-goal) { --el-table-tr-bg-color: rgba(103, 194, 58, .07); }
:deep(.el-table .row-miss) { --el-table-tr-bg-color: rgba(245, 108, 108, .06); }
:deep(.el-table .row-miss td:nth-child(3) .cell) {
  text-decoration: line-through; opacity: .6;   /* 否决的球把时刻划掉 */
}
</style>
```

- [ ] **Step 3: 新建 `TimelineStrip.vue`**

```vue
<script setup>
// 全场进球分布标记条：点刻度跳到离点击位置最近的那颗球。
// 无框选/多选/缩放/拖动（spec 明确不做）。
import { ref, computed } from "vue";
import { dispT, fmtT, tsec } from "../shared/time.js";
import { tierOf, tierText } from "./tiers.js";

const props = defineProps({
  rows: { type: Array, required: true },
  duration: { type: Number, default: 0 },            // state.duration
  verdicts: { type: Object, default: () => ({}) },
  offset: { type: Number, default: 0 },
});
const emit = defineEmits(["pick"]);

const trackRef = ref(null);
const tlDur = computed(() => Number(props.duration) || 0);

// 位置按**显示时刻**（与时刻列、切片同口径，R9）；时长未知时全压 0 位
const pctOf = (row) => {
  if (!tlDur.value) return 0;
  return Math.min(100, Math.max(0, (tsec(row, props.offset) / tlDur.value) * 100));
};

// 填充色的唯一来源：档位色取 `TIER[..].dot`（`tiers.js`）——**未知档位因此
// 也落到 ⚪ 低那一支的颜色**，不再掉回本组件自己的第三色、与 tooltip 里
// 那句「⚪ 低」打架。已否决/已勾选是**状态覆盖**（不是档位色），归本组件。
function dotColor(row) {
  const v = props.verdicts[row.key];
  if (v === "not_goal") return "#f56c6c";     // 已否决：红
  if (row.selection) return "#67C23A";        // 已勾选进片：绿
  return tierOf(row).dot;
}
// class 只留状态钩子（颜色已全部走行内 style）：tl-yes 的光圈、tl-no 的语义标记
function dotClass(row) {
  const v = props.verdicts[row.key];
  if (v === "not_goal") return "tl-no";
  return row.selection ? "tl-yes" : "";
}
const titleOf = (row) =>
  `${dispT(row, props.offset)} · ${tierText(row)}`
  + `${row.tag ? " · " + row.tag : ""}${row.selection ? " · 已勾选" : ""}`;

function onClick(e) {
  const r = trackRef.value?.getBoundingClientRect();
  if (!r || !tlDur.value) return;
  const t = ((e.clientX - r.left) / r.width) * tlDur.value;
  let best = null;
  for (const row of props.rows) {
    const d = Math.abs(tsec(row, props.offset) - t);
    if (!best || d < best.d) best = { d, key: row.key };
  }
  if (best) emit("pick", best.key);
}
</script>

<template>
  <div class="tlstrip">
    <div ref="trackRef" class="tl" @click="onClick"
         title="全场进球分布——点刻度跳到该球（绿=已勾选 红=已否决 灰点=低置信）">
      <span v-for="row in rows" :key="row.key"
            class="tl-dot" :class="dotClass(row)"
            :style="{ left: pctOf(row) + '%', background: dotColor(row) }"
            :title="titleOf(row)"></span>
    </div>
    <span class="tl-cap">0:00</span>
    <span class="tl-cap tl-cap-r">{{ tlDur ? fmtT(tlDur) : "" }}</span>
  </div>
</template>
```

样式：

```css
.tlstrip { flex: none; position: relative; padding: 10px 14px 0; }
.tl { position: relative; height: 18px; margin: 0 30px; cursor: pointer;
      background: #202020; border-radius: 9px; }
.tl::before { content: ""; position: absolute; left: 50%; top: 0;
              width: 2px; height: 100%; background: #3a3a3a; }
/* 颜色全部走行内 style（唯一来源 = tiers.js 的 TIER[..].dot + 两个状态覆盖），
   这里不再留任何 background 字面量：留着就是第二条来源，未知档位又会分叉。 */
.tl-dot { position: absolute; top: 4px; width: 10px; height: 10px;
          margin-left: -5px; border-radius: 50%;
          transition: transform .12s; }
.tl-dot:hover { transform: scale(1.6); }
.tl-dot.tl-yes { box-shadow: 0 0 0 2px rgba(103, 194, 58, .35); }
.tl-cap { position: absolute; top: 12px; font-size: 10px; color: #6f7278; }
.tl-cap:first-of-type { left: 0; }
.tl-cap-r { right: 0; }
```

- [ ] **Step 4: 左栏接进 `EditBay.vue`**

`<script setup>` 里新增（放在 `cnt` 附近）：

```js
const tierFilter = ref("all");
const tagFilter = ref("");
const missInput = ref("");
const curKey = ref("");
const tableRef = ref(null);

const tags = computed(() => state.value?.tags || []);
const verdicts = computed(() => state.value?.verdicts || {});
const deletedCount = computed(() => (state.value?.deleted || []).length);
const duration = computed(() => Number(state.value?.duration) || 0);

// 勾选/档位计数补一个「未勾选」（筛选按钮要显示它）
const cntUnchecked = computed(() =>
  rows.value.filter((r) => !r.selection).length);

const visibleRows = computed(() =>
  rows.value.filter((r) => {
    if (tierFilter.value === "unchecked" && r.selection) return false;
    if (["high", "review", "low"].includes(tierFilter.value)
        && r.status !== tierFilter.value) return false;
    if (tagFilter.value && r.tag !== tagFilter.value) return false;
    return true;
  }));

const emptyText = computed(() =>
  rows.value.length ? "当前筛选无匹配的球（换个档位/标签筛选）"
                    : "尚无检测结果——先点「▶ 开始检测」");

// 写操作的统一收尾：成功弹**服务端下发**的 msg；失败弹 e.message。
// 后端只在需要提示的端点上追加 msg（漏检/恢复/裁决/误标），PATCH 勾选与
// 打标签不带 msg ⇒ 那两处不弹 toast（裁定 R15：不自己编成功文案）。
async function write(p) {
  try {
    const s = await p;
    if (s && s.msg) ElMessage.success(s.msg);
    return s;
  } catch (e) {
    ElMessage.error(e.message);
    return null;
  }
}

const onMiss = () => write(act.recordMiss(missInput.value)).then((s) => {
  if (s) missInput.value = "";       // 只在真记上时清空输入框
});
const onPick = (key) => { curKey.value = key; };

// 标签列开了 allow-create：库里没有的新名字**先入库、再打标**。后端
// `set_row_tag` 只认已入库的标签（`app.py:1201` 的 docstring：「标签库维护走
// 新增标签弹窗」）——旧界面那颗「新增标签」弹窗干的就是入库这一件事；新界面
// 照原型把入口收进标签列，入库就得在这里补上，否则 `POST /api/tags` 是够不着
// 的端点、标签库永远长不了（筛选下拉与帮助里的 1–9 清单也永远不变）。
// 顺序固定「先入库后打标」：最后落进 state 的必须是带该标签的那份回包。
async function onTag(key, tag) {
  if (tag && !tags.value.includes(tag)) await act.addTag(tag);
  return act.setTag(key, tag);
}
```

把 Task 5 的 `onState` 换成这个（唯一改动是末尾那两句回填路径之后**什么都不做**）：

```js
function onState(s) {
  state.value = s;
  // 只在输入框还空着时回填：用户正在改路径时别被别的请求顶掉
  if (!pathInput.value) pathInput.value = s.videoPath || s.lastVideo || "";
  // ⚠ 这里**不需要**重新指当前行：写操作虽然用服务端回包整份替换 state、
  // 行对象全部重建，但 `EventTable` 设了 `:row-key`，element-plus 的
  // `useCurrent.updateCurrentRowData()` 在 setData 时就会 `setCurrentRowByKey`
  // 按 key 把当前行重新解析出来（`element-plus/es/components/table/src/store/
  // current.mjs`）⇒ 高亮自动跟着走。curKey 只是页面侧「当前行是谁」的权威
  // （右栏与 Task 7 的键盘流要读它），不是拿来驱动 el-table 的。
}
```

⚠ **这一段是修复轮 1（`7fa6e3f`）改掉的**：原稿在这里有一句 `nextTick(() => tableRef.value?.setCurrentRow(curKey.value))`，注释写的机制（「当前行按**行对象引用**记的 ⇒ 每写一次高亮就丢一次」）**是错的**——element-plus 在 setData 时按 `row-key` 自行重解析（`store/current.mjs` 的 `updateCurrentRowData`）。删掉它 e2e 依然绿，**是复审实测出来的**；对应那条断言从此只锁「高亮不丢」这个可见不变量、不归因于某段代码。**照抄上面这个版本，别把 `nextTick` 加回来。**

import 三处补上：`nextTick` **不要**进 vue 那一行（上面这版不需要它）、`import EventTable from "./EventTable.vue"; import TimelineStrip from "./TimelineStrip.vue";`

⚠ **这一句只管 Task 6 这一步，别拿它当交付版的样子**：Task 7 的「↑↓ 把当前行滚进视野」要等一帧再找 `.current-row`，那里**真的**用了 `nextTick`，所以交付版 `EditBay.vue:7` 的 vue 那一行**是** `import { ref, reactive, computed, nextTick, onMounted, onBeforeUnmount } from "vue";`。两条不矛盾——不要的只有 `onState` 里那一句。

模板里 `.left` 与 `.right` 换成：

```vue
      <div class="left">
        <div class="toolbar">
          <el-radio-group v-model="tierFilter" size="small">
            <el-radio-button value="all">全部</el-radio-button>
            <el-radio-button value="unchecked">未勾选 {{ cntUnchecked }}</el-radio-button>
            <el-radio-button value="high">🟢</el-radio-button>
            <el-radio-button value="review">🟡</el-radio-button>
            <el-radio-button value="low">⚪</el-radio-button>
          </el-radio-group>
          <el-select v-model="tagFilter" size="small" clearable filterable
                     placeholder="按标签筛" style="width: 110px">
            <el-option v-for="t in tags" :key="t" :label="t" :value="t" />
          </el-select>
          <el-input v-model="missInput" size="small" style="width: 104px"
                    placeholder="漏检 m:ss" @keyup.enter="onMiss">
            <template #append><el-button @click="onMiss">➕</el-button></template>
          </el-input>
          <el-dropdown>
            <el-button size="small">⋯</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="write(act.restoreAll())">
                  ↩ 恢复已删（{{ deletedCount }}）
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>

        <TimelineStrip v-if="visibleRows.length" :rows="visibleRows"
                       :duration="duration" :verdicts="verdicts"
                       @pick="onPick" />

        <div class="tablewrap">
          <EventTable ref="tableRef" :rows="visibleRows" :tags="tags"
                      :verdicts="verdicts" :empty-text="emptyText"
                      @pick="onPick"
                      @check="(k, v) => write(act.setSelection(k, v))"
                      @tag="(k, v) => write(onTag(k, v))" />
        </div>

        <div class="tblfoot">
          <span>显示 {{ visibleRows.length }} / 未删 {{ rows.length }} 条（另有 {{ deletedCount }} 条已删可恢复）</span>
          <span>勾选 {{ cnt.checked }} 条进集锦 · 未勾选 {{ cntUnchecked }} 条待复核 · 空格复核一遍就完事</span>
        </div>
      </div>

      <div class="right">
        <div v-if="!loaded" class="stage-hint">{{ hint }}</div>
        <div v-else-if="!curKey" class="stage-hint">← 点击左侧表格任意一行开始复核</div>
      </div>
```

模板里的三处说明（**不是省略**）：
- 时间轴：原型是 `v-if="liveRows.length"`（未删行非空）；真实现里 `rows[]` 本来就只有未删行，用 `visibleRows.length` 与表格同屏。
- `⋯` 下拉只剩「↩ 恢复已删」一项：另两项（时刻校准/批量删除）是 Task 9 的弹窗，届时补上。
- 空态两义：`!loaded` 用服务端 `hint`；载入了但没选行用「← 点击左侧表格任意一行开始复核」（原型 `.stage` 的 v-else 文案逐字）。

`.left` 的样式补三条（Task 5 已有 `display:flex;flex-direction:column`）：

```css
.toolbar { flex: none; display: flex; flex-wrap: wrap; gap: 8px;
           padding: 10px 12px; }
.tablewrap { flex: 1; min-height: 0; overflow: hidden; padding: 0 10px 6px; }
.tblfoot { flex: none; display: flex; justify-content: space-between;
           padding: 6px 14px; color: #7c7f86; font-size: 12px; }
```

- [ ] **Step 5: 建页级 e2e 夹具与跑器（`tests/e2e/`）**

这是**本任务直到 Task 11 唯一的带数据验证通道**，一次建好、后续每个 UI 任务往 spec 里追加断言。设计要点（读码得来，不是猜的）：

- `_work_dir_for()` 返回**相对** `work/<视频名>`（`bbh/app.py:336`），`PREFS_FILE = "ui_prefs.json"`、`TAGS_FILE = "tags.json"` 也是裸文件名（`bbh/web/api_session.py:25`、`bbh/app.py:35`）——**都按 cwd 解析**。所以只要让服务进程的 `cwd` 指向临时目录，`work/`、`ui_prefs.json`、`tags.json` 就全落在临时目录里，用户的真档一个字节都不碰，而勾选/打标/删除这些写操作可以随便验。
- 端口用 `socket.bind(("127.0.0.1", 0))` 拿**系统分配的空闲端口**：不撞 7861，永不接触用户的 7860。
- playwright 装在 `frontend/node_modules`，而 spec 在 `tests/e2e/`——ESM 从文件位置向上解析，既不认 `NODE_PATH` 也找不到它。所以 launcher 把 `frontend/node_modules/playwright/index.mjs` 的**绝对 `file://` URL** 用环境变量传进去，spec 动态 import。（`package.json` 的 exports 里 `import` 指向 `index.mjs`，已确认存在。）

**新建 `tests/e2e/test_edit_bay_e2e.py`：**

```python
"""剪辑台页级 e2e：真夹具 + 真服务 + 真产物，由 Playwright 驱动真浏览器。

隔离机制（这是整个文件成立的前提）：`_work_dir_for()` 返回相对路径
`work/<视频名>`，`ui_prefs.json` / `tags.json` 也是裸文件名——都按 cwd 解析。
所以 uvicorn 用 `cwd=临时目录` 起，则 work/、两个根档全落在临时目录，
用户的 work/ 与仓库根那 5 个无 git 副本的档一个字节都不碰；副作用
（勾选/打标/裁决/删除）反而成了可以随便验的东西。

Node 或 playwright 缺失 → skip 并说明（不假绿，也不阻塞没有 Node 的机器）。
"""
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from bbh.models import Calibration, GoalEvent, save_events

ROOT = Path(__file__).resolve().parents[2]
SPEC = Path(__file__).resolve().parent / "edit_bay.spec.mjs"
PW = ROOT / "frontend" / "node_modules" / "playwright" / "index.mjs"
NODE = shutil.which("node")
NAME = "e2e_fixture"
DUR = 6.0


def _fixture(root: Path) -> None:
    """造一份与真产物同形的检测结果（形状照 tests/test_web_locks.py::_make_result）。

    **12 颗球**、档位三色轮转（各 4 颗）、置信度递减、时刻 1.2s 起每 0.4s 一颗：
    行数够多，表格才会真的出现滚动条——「↑↓ 复核时当前行滚进视野」这条
    才有得验；档位筛选三档也各有 4 行可筛。缩略图（GIF）不造：那一格显示
    「—」，不影响要验的任何一列。
    """
    work = root / "work" / NAME
    work.mkdir(parents=True)
    status = ("high", "review", "low")
    evs = [GoalEvent(t=1.2 + i * 0.4, confidence=0.90 - i * 0.01,
                     status=status[i % 3],
                     cross_off=0.02, above_n=3, above_span=0.6, below_n=2,
                     below_span=0.4, descent_px=18.0, below_big=2,
                     net_post=12.5, net_ctrl=3.1, net_local=True)
           for i in range(12)]
    save_events(str(work / "events.json"), video=str(root / "videos" / f"{NAME}.mp4"),
                duration=DUR, events=evs, warning=None, calib_key=[1, 2, 3],
                logic_ver=1)
    Calibration(hoop_rect=(300, 140, 80, 40), hsv_low=(0, 80, 120),
                hsv_high=(25, 255, 255), frame_size=(640, 360)).save(
        str(work / "calibration.json"))
    # 全局标签库（`bbh/app.py:35` 的 TAGS_FILE，按 cwd 解析 ⇒ 落在临时目录根）。
    # 不种它的话「1–9 打标签」这条键盘流没有可打的标签，等于没验。
    # ⚠ 形状必须是 `{"tags": [...]}`：`_load_tags`（`bbh/app.py:666-673`）走的是
    # `json.load(f).get("tags", [])`、`except (OSError, ValueError)` —— 写成裸列表时
    # `.get` 抛 `AttributeError`、**不在它捕获的清单里** ⇒ 每次 `/api/state` 都 500，
    # 而且 500 会被 `_wait_up` 的 `except URLError` 吞掉、伪装成「服务没起来」。
    # 真档就是这个形状（`_save_tags` 也这么写）。
    (root / "tags.json").write_text(
        json.dumps({"tags": ["三分", "上篮", "罚球"]}, ensure_ascii=False),
        encoding="utf-8")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_up(base: str, proc: subprocess.Popen, log: Path,
             timeout: float = 40.0) -> None:
    """等服务真起来；服务自己死了就立刻把它的输出端上来（别白等 40 秒）。

    `log` 是子进程 stdout/stderr 落地的文件。**每次都用新开的句柄读**
    （`Path.read_text`）：那是子进程在写的档，父进程手里没有、也不该留着
    它那份带缓冲的对象。
    """
    def _log_text() -> str:
        try:
            return log.read_text(encoding="utf-8", errors="replace")
        except OSError:                     # 还没建/被清了：当作没输出
            return ""

    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            raise AssertionError(
                f"临时服务启动即退出（code={proc.returncode}）：\n{_log_text()}")
        try:
            urllib.request.urlopen(f"{base}/api/state", timeout=1).read()
            return
        except urllib.error.HTTPError as e:
            # 服务在跑，但它自己把 /api/state 返成了 4xx/5xx（典型：夹具写坏了
            # tags.json ⇒ `_load_tags` 抛 AttributeError ⇒ 500）。HTTPError 是
            # URLError 的子类，被下面那条例外接住的话会被当成「还没起来」、
            # 一路重试到 40 秒，最后报成反的结论。这里立刻把状态码与响应体端上来。
            # 响应体是真有的（500 时实测 79 字节，与响应头的 content-length 吻合）；
            # 服务日志里还躺着 ASGI 的 traceback——两样都端上来，别只报个状态码。
            #
            # ⚠ 读响应体仍留一层兜底：这是**防御纵深**，不是常态。子进程的输出
            # 已经引到文件、不再走没人排空的管道（见 `bay` 的注释），但只要服务
            # 真卡住（哪怕卡在别的路径上），读体照样可能超时——那时如实写明
            # 「读不出来」，别让 TimeoutError 冒出去把「到底是 500 还是别的」
            # 又盖掉一层。
            # 覆盖面就是下面这两类，别把它说大了：`OSError`（含 `TimeoutError`
            # 与各种 socket 错误）+ `http.client.IncompleteRead`——后者**不是**
            # OSError 的子类（它走 `http.client.HTTPException` ⇒ `Exception`），
            # 头收全了、体被截断时抛的就是它。也**别**图省事写成裸 `except Exception`：
            # 那会把这段诊断自己的编程错误一起吃掉，又变成「盖一层」。
            try:
                body = e.read().decode("utf-8", "replace")[:400]
            except (OSError, http.client.IncompleteRead) as ex:
                body = f"（响应体读不出来：{type(ex).__name__}: {ex}）"
            raise AssertionError(
                f"临时服务在跑，但 GET /api/state 返 {e.code}：\n{body}"
                # 日志取**尾部**：traceback 的根因（`bbh/app.py:670 in _load_tags`、
                # `AttributeError: ...`）在最后几行，头部全是 uvicorn/fastapi 的
                # 框架帧。实测该 500 的 server.log 共 4872 字符，根因落在
                # 4718/4819 ⇒ 取头部 [:2000] 会把根因整段截掉、只剩噪音。
                f"\n--- 服务日志（{log.name}，尾部）---\n{_log_text()[-2000:]}") from None
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    raise AssertionError(f"临时服务 {timeout:.0f}s 没起来：{base}")


def _untouched(p: Path) -> bytes | None:
    """仓库根真档的快照（只读！）——用来证明 e2e 没碰用户的数据。"""
    return p.read_bytes() if p.exists() else None


@pytest.fixture(scope="module")
def bay(tmp_path_factory):
    if not NODE:
        pytest.skip("没有 node——剪辑台 e2e 需要 node + playwright（见计划「验证配方」C2）")
    if not PW.exists():
        pytest.skip(f"没有 {PW}——先 cd frontend && npm i -D --no-save playwright")
    root = tmp_path_factory.mktemp("bay")
    _fixture(root)
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    code = ("import uvicorn\n"
            "from bbh.web.app import create_app\n"
            f"uvicorn.run(create_app(), host='127.0.0.1', port={port}, "
            "log_level='warning')\n")
    before = {f: _untouched(ROOT / f) for f in ("tags.json", "ui_prefs.json")}
    # 子进程的输出**引到文件**，不用 `subprocess.PIPE`：PIPE 的缓冲区只有几 KB
    # 且这里没人排空（`_wait_up` 只在子进程**已退出**那条分支读它），服务一旦往
    # stderr 写点大的就会把它写满——`/api/state` 500 的 ASGI traceback 正好够，
    # 写满后子进程卡在「响应头已发、响应体还没写」中间，于是客户端只见响应头、
    # 体一个字节都到不了（实测：体其实有 79 字节，与 content-length 吻合，只是
    # 被堵在管道后面；把 stdout 引到文件、或把管道读干，体立刻就到）。
    # 文件不封顶，且失败时能原样端上来。
    log = root / "server.log"
    with open(log, "w", encoding="utf-8", errors="replace") as lf:
        proc = subprocess.Popen([sys.executable, "-c", code], cwd=root, env=env,
                                stdout=lf, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
    # 父进程这份句柄在上面那个 with 退出时就关掉了：子进程已拿到自己的句柄，
    # 而 Windows 上父进程留着它会挡住 pytest 的 tmp 目录清理。
    try:
        _wait_up(base, proc, log)
        yield {"base": base, "root": root, "video": str(root / "videos" / f"{NAME}.mp4")}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    # 红线守卫：仓库根那两个无 git 副本的档必须原样（读的是字节，不是写）
    for f, b in before.items():
        assert _untouched(ROOT / f) == b, f"e2e 动了仓库根的 {f}——隔离没兜住，停下查"


def test_edit_bay_e2e(bay):
    base = bay["base"]
    st = json.loads(urllib.request.urlopen(f"{base}/api/state", timeout=5).read())
    assert st["loaded"] is False, "夹具应是「还没开会话」的初始态"

    env = dict(os.environ, BBH_BASE=base, BBH_VIDEO=bay["video"],
               BBH_PLAYWRIGHT=PW.as_uri(), PYTHONIOENCODING="utf-8")
    r = subprocess.run([NODE, str(SPEC)], env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    assert r.returncode == 0, f"e2e 失败：\n{r.stdout}\n{r.stderr}"
    assert "✅ 全过" in r.stdout, f"e2e 没跑到收尾汇总：\n{r.stdout}\n{r.stderr}"

    # 写盘真的落在临时目录（而不是哪儿都没落）
    work = bay["root"] / "work" / NAME
    assert (work / "selection.json").exists(), "勾选的副作用没落盘——e2e 没验到真写"
```

**新建 `tests/e2e/edit_bay.spec.mjs`：**

```js
// 剪辑台页级 e2e：真服务 + 真夹具 + 真产物 + 真浏览器。
// 由 tests/e2e/test_edit_bay_e2e.py 启动——服务跑在临时目录里（cwd=tmp），
// 所以这里的每一次勾选/打标/裁决/删除都只写临时 work/，用户的 work/ 与
// 仓库根的 tags.json / ui_prefs.json 一个字节都不碰。
//
// 环境：BBH_BASE（服务）、BBH_VIDEO（夹具视频路径）、BBH_PLAYWRIGHT
// （playwright 的绝对 file:// URL——它装在 frontend/node_modules，而 ESM
// 从本文件位置向上解析、不认 NODE_PATH，故由 launcher 传入）
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
const state = async () => (await fetch(`${BASE}/api/state`)).json();

// 等服务端真落盘（请求是异步的，DOM 先变、盘后写）
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
  // 只收 JS 异常：媒体/图片 404 是夹具的常态（没有真 GIF、没有真切片），
  // 它们走 network，不是 pageerror，不能当失败
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));

  await page.goto(`${BASE}/edit.html`, { waitUntil: "domcontentloaded" });
  await page.locator("#app .topbar input").first().waitFor();

  // —— 空态：右栏文案必须原样来自服务端（Task 5 的承诺是「前端不自己 union
  // 文案」，而冒烟只覆盖到 .topbar、抓不到这条）。此刻还没开会话 ⇒ 服务端走的
  // 是 state_payload() 的 base 分支，hint 恒为「请先填视频路径（本机文件全路径）」。
  // 这条断言也顺手钉住「末载入时右侧渲染的就是 .stage-hint」这层结构。
  const s0 = await state();
  eq(s0.loaded, false, "刚打开时服务端还没载入会话（e2e 服务是全新的）");
  await waitFor(async () => (await page.locator("#app .stage-hint").count()) === 1,
                "右栏空态出现（store.refresh 回了包）");
  eq((await page.locator("#app .stage-hint").first().innerText()).trim(), s0.hint,
     "右栏空态文案 = 服务端下发的 hint（不是前端自编）");

  // —— 载入会话：走用户真路径（填路径 → 回车 → @change 发 POST /api/session）
  const input = page.locator("#app .topbar input").first();
  await input.fill(VIDEO);
  await input.press("Enter");
  await page.locator("#app .el-table__row").first().waitFor({ timeout: 20000 });

  const s = await state();
  const n = await page.locator("#app .el-table__row").count();
  eq(n, s.rows.length, "表格行数 = 后端 rows 条数");
  eq(n, 12, "夹具 12 颗球都上了表");

  // 时刻列用服务端下发的 t 串（R12），逐行核对
  const cell = async (i, c) => (await page.locator("#app .el-table__row").nth(i)
    .locator("td").nth(c).innerText()).trim();
  for (let i = 0; i < s.rows.length; i++) {
    ok((await cell(i, 2)).includes(s.rows[i].t.split(".")[0]),
       `第 ${i + 1} 行时刻「${await cell(i, 2)}」含后端 t=${s.rows[i].t}`);
  }

  // 时间轴标记条：每颗球一个 dot，位置按显示时刻算
  eq(await page.locator("#app .tl-dot").count(), s.rows.length, "时间轴 dot 数 = 行数");

  // 页脚：显示/未删/已删/勾选四处计数与后端一致
  const foot = (await page.locator("#app .tblfoot").innerText()).replace(/\s+/g, " ");
  ok(foot.includes(`显示 ${s.rows.length} / 未删 ${s.rows.length} 条`),
     `页脚显示计数（实际「${foot}」）`);
  ok(foot.includes("勾选 0 条进集锦"), `页脚初始勾选为 0（实际「${foot}」）`);

  // 勾选第一行：状态必须来自服务端回包，不是本地复选框自己变
  const row0 = page.locator("#app .el-table__row").first();
  // 先点「时刻」单元格选中它——整行中心可能落在「标签」列的下拉上
  await row0.locator("td").nth(2).click();
  ok(await page.locator("#app .el-table__row.current-row").count() === 1,
     "点行后该行成为当前行（右栏靠它出内容）");
  await row0.locator(".el-checkbox").click();
  await waitFor(async () => (await state()).rows.filter((r) => r.selection).length === 1,
                "勾选落到服务端");
  const s2 = await state();
  eq(s2.rows.filter((r) => r.selection).length, 1, "后端只有 1 行被勾选");
  ok(s2.rows[0].selection, "被勾选的是第一行");
  const foot2 = (await page.locator("#app .tblfoot").innerText()).replace(/\s+/g, " ");
  ok(foot2.includes("勾选 1 条进集锦"), `页脚勾选计数跟着变（实际「${foot2}」）`);
  // 写操作整份替换 state、行对象重建 ⇒ 当前行高亮必须还在。这条锁的是
  // 「高亮不丢」这个可见不变量，**不归因于某段代码**：实测 el-table 设了
  // `:row-key`，element-plus 的 `useCurrent.updateCurrentRowData()` 在 setData
  // 时自己就按 key 重解析了当前行，页面侧那段重指向删掉这条也照样绿。
  eq(await page.locator("#app .el-table__row.current-row").count(), 1,
     "写一次之后当前行高亮还在（整份替换 state 后高亮仍然持久）");

  // 再点一次复原：夹具回到初始态，方便后续任务追加的断言（也顺手验了取消勾选）
  await row0.locator(".el-checkbox").click();
  await waitFor(async () => (await state()).rows.every((r) => !r.selection),
                "取消勾选落到服务端");
  ok(true, "取消勾选落回服务端");

  // —— 标签列 allow-create：库里没有的新名字必须**先入库、再打标**（P9b）。
  // 只断行标签一定假绿：实测把 onTag 里的 act.addTag 去掉后，行标签照样打成、
  // 后端照收，只是标签库一次都不涨（POST /api/tags 一次都不发）⇒ 筛选下拉与
  // 「1–9 打标签」清单永远看不到新标签。所以这条**必须断 state.tags 的增长**。
  const TAGCELL = 4;              // 列序：0 缩略图 / 1 进片 / 2 时刻 / 3 档位 / 4 标签
  const NEWTAG = "reviewtag9";
  const tagCell = () =>
    page.locator("#app .el-table__row").first().locator("td").nth(TAGCELL);
  const sBefore = await state();
  ok(!sBefore.tags.includes(NEWTAG), `新标签 ${NEWTAG} 起点不该在库里`);
  await tagCell().locator(".el-select__wrapper").click();
  await tagCell().locator("input").fill(NEWTAG);
  // 候选必须限定在**看得见**的那个下拉里：13 行各挂一份下拉，库里也有这个
  // 名字之后别的行的下拉里同样会出现它（那些是 display:none）
  await page.locator(".el-select-dropdown__item:visible")
    .filter({ hasText: NEWTAG }).first().click();
  await waitFor(async () => (await state()).tags.includes(NEWTAG),
                "新标签进了全局标签库（= POST /api/tags 真发了）");
  // 「先入库、后打标」是**两次请求**：入库那趟的回包先把 state 换成「有标签、
  // 但该行还没打上」，所以行标签必然后到——分开等，别抢跑
  await waitFor(async () => (await state()).rows[0].tag === NEWTAG,
                "新标签落到该行（addTag 之后才是 setTag）");
  const s3 = await state();
  ok(s3.tags.includes(NEWTAG),
     `标签库长出了 ${NEWTAG}（实际 ${JSON.stringify(s3.tags)}）`);
  eq(s3.rows[0].tag, NEWTAG, "该行标签 = 刚输入的新名字");
  eq(s3.tags.slice(0, sBefore.tags.length).join(","), sBefore.tags.join(","),
     "新标签追加在库尾，原有项一个不动（Task 7 的 1–9 索引依赖它）");

  // 清回空：顺带验了 clearable 的清标签路径（库里那条留着——后端没有删标签端点）。
  // ⚠ 但本段**并没有**把夹具交回干净态：上面打标那一步，后端 `set_row_tag` 会
  // **顺带勾选**该行，而清标签**不**取消勾选（`bbh/app.py` 的既有语义，见
  // actions.js 的 setTag 注释）⇒ 交给下一段的是「第 1 行仍勾着」。
  // 下一段（本文件末尾的键盘流）自己复位，别指望这里。
  await tagCell().locator(".el-select__wrapper").hover();
  await tagCell().locator(".el-select__clear").click();
  await waitFor(async () => (await state()).rows[0].tag === "", "行标签清回空");
  ok(true, "清标签落回服务端（标签库里这条保留）");
```

> **本块只是 Task 6 交付的那一段**（`spec.mjs:1-147`，与仓库逐字一致）。Task 7/8/9/10 各自在文件尾部**追加自己的段**，所以文件级的 `errs` 汇总与 `finally { await browser.close(); }` 现在在**文件末尾**（Task 10 段之后），Task 6 交付时它在段尾——这就是本节原来那个「块尾带 `errs`/`finally`」的写法为什么现在不对了。

Run:

```bash
./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/e2e/test_edit_bay_e2e.py
```

Expected: `1 passed`。spec 自己会打印 `✅ 全过 N 项（行数 12，勾选/取消勾选都落到了服务端）`，但 launcher 是 `capture_output=True` 收着子进程输出的——**pytest 成功路径上看不到那行**（只有失败时才会把它拼进 assert 消息）。想亲眼看它，用 launcher 的同一套代码（stdout 继承、不用 capture_output）手跑一次 spec。首次可能需要 `cd frontend && npm i -D --no-save playwright`（配方 C 同一笔），没装则**跳过**——在报告里写明「e2e 跳过 + 原因」，别当作通过。

- [ ] **Step 6: 构建 + 冒烟 + 产物 pytest**

配方 **A**、**C**、**D**（C2 已在 Step 5 跑过）。
Expected: 全 PASS（冒烟的剪辑台地标此刻仍在 `.topbar`；`el-table` 渲染出内容不影响它）

- [ ] **Step 7: 提交**

```bash
git add frontend/src/edit/tiers.js frontend/src/edit/EventTable.vue \
        frontend/src/edit/TimelineStrip.vue frontend/src/edit/EditBay.vue \
        tests/e2e/edit_bay.spec.mjs tests/e2e/test_edit_bay_e2e.py bbh/static
git commit -m "feat(frontend): 剪辑台表格/筛选/时间轴标记条/页脚 + 页级 e2e 通道

表格 7 列按原型尺寸；行对象是服务端权威值——勾选与标签不回写本地行，
一律发请求后整份替换（否则界面会先出现服务端并不认可的状态）。
裁决列与行态（✔/✘、绿底/红底、时刻划掉）取 state.verdicts[key]（R4 下发的
原因）；原型那条 .row-deleted 不实现——已删的球根本不在 rows[] 里。
时刻列用服务端 t 串（dispT），零填充的 mm:ss.s 字典序即时间序，sortable 直接可用。
写操作整份替换 state 会重建全部行对象，而 el-table 的当前行是按对象引用记的——
onState 里按 curKey 重新指向一次，勾选后高亮不丢（e2e 钉住这条）。
原型的 manual（⚠ 漏检）档不实现：漏检补录只进 feedback.misses（R6）。
成功提示只弹服务端下发的 msg，勾选/标签不带 msg ⇒ 不弹（R15）。

测试通道：work/ 与 ui_prefs.json / tags.json 都是按 cwd 解析的相对路径，所以
tests/e2e 把服务起在临时目录（cwd=tmp、随机空闲端口）——用户真档零污染，
而勾选/打标/删除这些副作用可以真验。夹具自带 12 颗球（高/待/低档各 4）+ 标定档。
spec 是后续 UI 任务共用的追加点，不再各自写临时脚本。"
```

---

### Task 7: 键盘流（复核闭环）

**Files:**
- Modify: `frontend/src/edit/EditBay.vue`（`<script setup>` 尾部：`dlg`/闸门/键处理/接线）
- Modify: `tests/e2e/edit_bay.spec.mjs`（追加「键盘流」段）
- Modify: `bbh/static/**`（配方 A 的产物）

**Interfaces:**
- Consumes: `bindHotkeys(map, {disabled})`（Task 3）、`act`（Task 4）、`write`/`visibleRows`/`curKey`/`tableRef`/`tags`/`verdicts`（Task 6）、`dispT`（Task 1）
- Produces:
  - `dlg`（`reactive({gen:false, offset:false, bulk:false, help:false})`）与 `modalOpen()`：**四个弹窗的状态与闸门在本任务定型**，Task 8–10 只往里塞组件，不再改接线
  - `pick(key)`：键盘选中一行的唯一入口（`curKey` + `tableRef.setCurrentRow`）
  - `jumpNext(fromKey)`：空格/`X` 之后的自动跳下一颗
  - 本任务**只接** `↑ ↓ 空格 X 1-9 D Ctrl+Z`；`K/J/L`、`← →` 归 Task 8，`?` 归 Task 9（要弹帮助窗）——本任务把 `disabled: modalOpen` 先接好，那两个任务落地时不必回头动这里。**⚠ 别为 Task 8 预留任何 `vidEl` ref**：Task 8 的播放器是 `ClipPanel`，它 `defineExpose({ togglePlay(), seekBy(d) })`、**明确不暴露 video 元素**（见 Task 8 的 Interfaces），K/J/L 走的是 `clipRef.value?.togglePlay()` / `seekBy(±2)`。留一个没人用的 ref 只会变成死代码
- 四道前置闸（顺序照 `proto-edit-inventory.md` §4）：① 输入框/下拉里打字 → `hotkeys.js` 内部拦掉并交还原生行为（**含 `Ctrl+Z`**：编辑框里那是文本撤销）；② 弹窗打开 → `disabled: modalOpen` 整体挂起；③ 空表 → 除 `Ctrl+Z` 外的键各自判空 `return`；④ 有当前行才动手的键（空格/`X`/`1-9`/`D`）先 `rowByKey(curKey)` 取行，取不到就 no-op

- [ ] **Step 1: 往 e2e 里追加键盘段（先红）**

在 `tests/e2e/edit_bay.spec.mjs` 里 `if (errs.length) throw …` **之前**插入（后续任务都照这个位置追加）：

```js
  // —— 键盘流（Task 7）：复核闭环的左手侧 ——
  // 前置复位：本段下面两条断言（「空格勾上第一颗」「栈空了再按只提示」）都以
  // 「全不勾 + 撤销栈空」为前提，而上一段退出时两条都不成立——
  //  ① 上一段的标签列打非空标签时后端 set_row_tag 会**顺带勾选**该行，而清标签
  //     **不**取消勾选（`bbh/app.py` 的既有语义，见 actions.setTag 注释）⇒ 第一行
  //     此刻还勾着，空格按下去只会把它**取消**（本段第一次跑就红在这条上）。
  //  ② 撤销栈是**页面级**的（每个写操作入栈，见 actions.once），到这儿已经躺着
  //     上一段的四条（勾/取消勾/打标/清标签）；前端没有清栈入口，所以整页重载
  //     一次换一份新实例 = 空栈。重载只换前端：服务端会话仍在内存里、临时目录的
  //     work/ 一个字节不动（`/api/state` 照旧回已载入的那份），夹具状态不丢。
  // ⚠ 复位要**条件化**：这一击的前提是「上一段真的留下了勾选」。若无条件点下去，
  // 而上游某步被改得不再留残留，这一击反而会把第 1 行**勾上**、紧接着的
  // `waitFor(全不勾)` 就反向超时——红得像本段自己的 bug。有才点，语义不变。
  if ((await state()).rows.some((r) => r.selection)) {
    await page.locator("#app .el-table__row").first().locator(".el-checkbox").click();
    await waitFor(async () => (await state()).rows.every((r) => !r.selection),
                  "复位：摘掉上一段「打标顺带勾选」留下的那一行");
  }
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator("#app .el-table__row").first().waitFor({ timeout: 20000 });

  // 点「时刻」单元格选中行：整行中心可能正落在「标签」列的下拉上，点它会
  // 把焦点给 select 的输入框，而那正是键盘闸门①要拦的场景（键就不生效了）。
  const rowCell = (i) => page.locator("#app .el-table__row").nth(i).locator("td").nth(2);
  const curTxt = async () => {
    const r = page.locator("#app .el-table__row.current-row");
    return (await r.count()) ? (await r.first().locator("td").nth(2).innerText()).trim() : "";
  };
  const keys = async (k) => { await page.keyboard.press(k); await page.waitForTimeout(150); };
  // toast 挂在 document.body 上（ElMessage 默认 teleport），不在 #app 里；
  // 按文案过滤再看有没有——上一枚 toast 有 3s 存活期，不能用 .first() 猜。
  const toast = (t, ms = 4000) =>
    page.locator(".el-message").filter({ hasText: t }).first()
      .waitFor({ timeout: ms }).then(() => true, () => false);

  await rowCell(0).click();
  // ↓/↑ 换行
  await keys("ArrowDown");
  eq(await curTxt(), s.rows[1].t, "↓ 选中第二行");
  await keys("ArrowUp");
  eq(await curTxt(), s.rows[0].t, "↑ 回到第一行");

  // 空格 = 确认进球：勾上后自动跳到下一颗「未勾选且未被否决」的球
  await keys("Space");
  await waitFor(async () => (await state()).rows[0].selection, "空格勾上第一颗");
  eq(await curTxt(), s.rows[1].t, "勾上后自动跳到第二颗");
  await keys("Space");
  await waitFor(async () => (await state()).rows[1].selection, "空格勾上第二颗");
  eq(await curTxt(), s.rows[2].t, "再勾一颗、再跳一颗");
  await keys("ArrowUp");
  eq(await curTxt(), s.rows[1].t, "↑ 回到第二颗");

  // X = ❌ 误判：在**已勾选**的这一行按 X，联动取消进片（R16）
  await keys("KeyX");
  await waitFor(async () => (await state()).verdicts[s.rows[1].key] === "not_goal",
                "X 记下误判反馈");
  await waitFor(async () => !(await state()).rows[1].selection,
                "❌ 联动取消了这一行的进片（R16）");

  // Ctrl+Z 撤销误判：裁决消失 **且** 那一步联动取消的勾选被还原
  await keys("Control+KeyZ");
  await waitFor(async () => !(await state()).verdicts[s.rows[1].key], "撤销误判");
  ok(await toast("已撤销"), "撤销有提示（R17）");
  await waitFor(async () => (await state()).rows[1].selection,
                "撤销把联动取消掉的勾选也还原了");
  // 再撤两次：两颗球的空格勾选
  await keys("Control+KeyZ");
  await waitFor(async () => !(await state()).rows[1].selection, "撤销第二颗的勾选");
  await keys("Control+KeyZ");
  await waitFor(async () => (await state()).rows.every((r) => !r.selection),
                "撤销第一颗的勾选，回到全不勾");
  // 栈空了再按：只提示，不报错
  await keys("Control+KeyZ");
  ok(await toast("没有可撤销的操作"), "栈空时 Ctrl+Z 只提示不报错");
  ok(!errs.length, "键盘流没打出 JS 异常");

  // 1-9 打标签（夹具种了 tags.json：三分/上篮/罚球 → `1` 打「三分」）
  // 打标会顺带勾选该行（后端 set_row_tag 的既有行为），撤销也**不**还原那次勾选——
  // 与旧界面同病（见 actions.setTag 注释），所以本段最后手工复原。
  await rowCell(0).click();
  await keys("Digit1");
  await waitFor(async () => (await state()).rows[0].tag === "三分", "1 打上第一个标签");
  ok((await state()).rows[0].selection, "打标顺带勾选（后端 set_row_tag 的既有行为）");

  // 复原夹具：撤标签 → 手工取消勾选（后面任务的断言都从「全不勾、无标签」起）
  await keys("Control+KeyZ");
  await waitFor(async () => !(await state()).rows[0].tag, "撤销标签");
  await page.locator("#app .el-table__row").first().locator(".el-checkbox").click();
  await waitFor(async () => (await state()).rows.every((r) => !r.selection), "复原勾选");
  // 注意：复原用的那次勾选点击**也会入栈**（它走 actions.setSelection），
  // 所以本段结束时栈里还剩 1 条。后续任务不要假设栈是空的——要验撤销就自己先造一步。

  // 复核 300 颗球时「↑↓ 要跟着滚」是帮助文案里的承诺（Task 9 逐字照抄原型），
  // 所以这里必须钉住：连按 ↓ 到夹具第 8 行，当前行得在表格可视区里。
  // 若这条红，就在 pick() 的 setCurrentRow 后补
  // `rowEl.scrollIntoView({ block: "nearest" })`——el-table 的 setCurrentRow
  // 是否顺带滚动随版本而变，别假设它滚。
  // 起点是上面 `rowCell(0).click()` 选中的**第 1 行（索引 0）**（本段前面那次
  // 点复选框不会移当前行）。7 次 ↓ ⇒ 索引 7，也就是人话里的「第 8 行」——
  // 断言写 `s.rows[7]`，别写 `s.rows[8]`（那要求 8 次 ↓）。
  // ⚠ 上面那次「点复选框复原」会把焦点留在 el-checkbox 的原生 input 上
  // （实测 activeElement = INPUT.el-checkbox__original）——那正是键盘闸门①
  // （输入框里打字一律不触发）要拦的场景，键会被整段吞掉、当前行一步不动。
  // 点回「时刻」单元格一举两得：① 把焦点交回页面（td 不可聚焦 ⇒ activeElement
  // 落回 tbody）；② 把当前行**移**到该行——实测点 td 会触发 el-table 的
  // current-change（这不是「不移当前行」，别照抄旧注释）⇒ 起点被钉死在索引 0，
  // 不依赖「上一段碰巧没动过当前行」。
  await rowCell(0).click();
  // ⚠ 先把视口压矮，这条断言才不是空跑：1600×900 下表格可视区实测 662px、
  // 表体只有 492px ⇒ **根本没有滚动条**，12 行全都「在可视区里」，那条断言
  // 恒真（夹具注释里「行数够多，表格才会真的出现滚动条」的前提并不成立）。
  // 压到 420 高后可视带只剩 ~182px，第 8 行会真的落在带外——没有 pick() 里
  // 那次 scrollIntoView 时，实测该行 y=492、可视带 205~387，断言当场红。
  await page.setViewportSize({ width: 1600, height: 420 });
  await page.waitForTimeout(300);
  for (let i = 0; i < 7; i++) await keys("ArrowDown");
  eq(await curTxt(), s.rows[7].t, "连按 ↓ 到第 8 行（索引 7）");
  const box = await page.locator("#app .el-table__row.current-row").first().boundingBox();
  const wrap = await page.locator("#app .el-table__body-wrapper").first().boundingBox();
  ok(box && wrap && box.y >= wrap.y - 1 && box.y + box.height <= wrap.y + wrap.height + 1,
     `当前行滚进了表格可视区（行 y=${box?.y} 视区 ${wrap?.y}~${wrap && wrap.y + wrap.height}）`);

  // ⚠ 段尾**必须**把视口还原（后来者别删这句，它不是装饰，是给**下一段**用的）：
  // 下面的 throw 就在两行之后，而后续任务的段全都插在那个 throw 之前 ⇒ 不还原
  // 的话它们会继承上面那个 420 高的视口。复审实测 420 下 `.right` 可视带只剩
  // 327px（editbayH=380 / topbarH=53 / rightTop=93），而 Task 8 右栏内容高 435px
  // ⇒ `.subbtns` 下沿落在视口 y≈443，Task 8 的「第二排按钮不用滚动就在窗内」与
  // 「右栏 scrollTop === 0」两条会红得**与它自己的代码无关**。
  // Task 8 段首自己也会声明一次同样的还原（`392c06e`）——两处都做是有意的：
  // 每段自足，且不依赖邻居替自己还原。
  await page.setViewportSize({ width: 1600, height: 900 });
```

> **本块只是 Task 7 交付的那一段**（`spec.mjs:150-281`，与仓库逐字一致）：段首的「前置复位」与段尾的「视口还原」都是段自足的写法，后来者别当成冗余删掉。

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/e2e/test_edit_bay_e2e.py`
Expected: FAIL —— KeyError/超时出现在 `↓ 选中第二行`（此刻键盘没接，`.current-row` 停在最初点中的第一行）

- [ ] **Step 2: 在 `EditBay.vue` 里接键盘层**

`<script setup>` 的 import 行补三个：

```js
import { reactive } from "vue";              // 若已 import 就并入那一行
import { ElMessage, ElMessageBox } from "element-plus";
import { bindHotkeys } from "../shared/hotkeys.js";
```

在 `onPick` 附近（Task 6 末尾）追加：

```js
// —— 键盘流（spec 3 的复核闭环：左手键盘、右手不动鼠标）——
// 前置闸见本任务 Interfaces 的四条。`?`（帮助）在 Task 9、K/J/L 与 ←/→
// （播放器）在 Task 8——它们的键位与这里同源，落地时直接往 keyMap 里加。
const dlg = reactive({ gen: false, offset: false, bulk: false, help: false });
const modalOpen = () => dlg.gen || dlg.offset || dlg.bulk || dlg.help;

const rowByKey = (key) => visibleRows.value.find((r) => r.key === key) || null;

// 键盘选中一行的唯一入口：setCurrentRow 会触发 current-change → onPick
function pick(key) {
  curKey.value = key;
  tableRef.value?.setCurrentRow(key);
}

// ↑/↓：没有当前行时两个方向都落到第一行（原型行为，不是落到最后一行）
function move(delta) {
  const keys = visibleRows.value.map((r) => r.key);
  if (!keys.length) return;
  const i = keys.indexOf(curKey.value);
  const j = i < 0 ? 0 : Math.min(keys.length - 1, Math.max(0, i + delta));
  pick(keys[j]);
}

// 勾上之后自动跳到下一颗「未勾选且未被否决」的球；找不到就从头绕一圈
function jumpNext(fromKey) {
  const list = visibleRows.value;
  const i = list.findIndex((r) => r.key === fromKey);
  const usable = (r) => !r.selection && verdicts.value[r.key] !== "not_goal";
  for (let k = i + 1; k < list.length; k++) if (usable(list[k])) return pick(list[k].key);
  for (let k = 0; k < i; k++) if (usable(list[k])) return pick(list[k].key);
}

// 空格 = 勾选「进片」＝确认这是进球（勾上后跳下一颗，长列表不用回鼠标）
function confirmRow() {
  const row = rowByKey(curKey.value);
  if (!row) return;
  const key = row.key;
  const on = !row.selection;
  write(act.setSelection(key, on)).then((s) => {
    if (s && on) setTimeout(() => jumpNext(key), 120);   // 等服务端回包渲染完再跳
  });
}

// X = ❌ 误判反馈（同一颗球再按一次 = 取消；裁决联动取消进片，见 actions.markVerdict）
function markMiss() {
  const row = rowByKey(curKey.value);
  if (!row) return;
  const key = row.key;
  write(act.markVerdict(key, "not_goal")).then((s) => {
    if (s) setTimeout(() => jumpNext(key), 120);
  });
}

// 1–9 = 打第 n 个标签（打标会顺带勾选该行——后端 set_row_tag 的行为，见 actions.setTag）
function tagByIndex(n) {
  const row = rowByKey(curKey.value);
  const tag = tags.value[n - 1];
  if (row && tag) write(act.setTag(row.key, tag));
}

// D = 删除此球（二次确认；非破坏——可从「⋯ 恢复已删」整套找回）
async function deleteCurrent() {
  const row = rowByKey(curKey.value);
  if (!row) return;
  try {
    await ElMessageBox.confirm(
      `删除 ${dispT(row, 0)} 这一行？（非破坏——可从「⋯ 恢复已删」找回）`,
      "删除此球", { type: "warning" });
  } catch {
    return;                                   // 用户点了取消
  }
  const s = await write(act.deleteKeys([row.key]));
  if (s) curKey.value = "";                    // 行没了，右栏回空态
}

// Ctrl+Z = 撤销上一步（栈空只提示，不报错）
async function undoStep() {
  if (!act.depth) { ElMessage.info("没有可撤销的操作"); return; }
  try {
    const r = await act.undo();
    if (r) ElMessage.success(`已撤销：${r.label}`);
  } catch (e) { ElMessage.error(e.message); }
}

// 键名照 shared/hotkeys.js 的归一：字母小写、方向键 up/down、空格 space
const keyMap = { up: () => move(-1), down: () => move(1), space: confirmRow,
                 x: markMiss, d: deleteCurrent, "ctrl+z": undoStep };
for (let n = 1; n <= 9; n++) keyMap[String(n)] = () => tagByIndex(n);

let unbindHotkeys = null;
```

**不需要动 `onState`**：写操作整份替换 state 之后，当前行高亮**本来就不会丢**——element-plus 在 `setData` 时按 `:row-key` 自己重解析当前行（`store/index.mjs` 的 `setData` 无条件调 `updateCurrentRowData()` → `store/current.mjs` 在「旧当前行已不在新 data + 有 `rowKey`」时 `setCurrentRowByKey`），键盘选中行走的正是同一条 `setCurrentRow(key)`。**注意**：Task 6 交付的那段代码里**没有** `nextTick` 重指向（原计划的注释把机制说反了，`nextTick` 重指向已删，删掉后 e2e 仍绿——复审实测 + 源码级核实）。`pick()` 里再调一次 `setCurrentRow` 会触发 `current-change` → `onPick` → `curKey` 写入同一个值，无循环。

**从 Task 6 继承过来的接口事实（Task 6 修复轮 1 + 范围受限复审定稿，权威 = 仓库代码）：**

- **`pick(key)` 是「移动当前行」的唯一入口**（`curKey` + `tableRef.setCurrentRow`）。Task 6 复审 B-5 发现点时间轴刻度只写 `curKey`、**不移表格当前行**，裁定 park 到本任务——本任务把它收敛到 `pick()` 一条路径上，**别开第二条**。
- **时间轴的 dot 不再有 `.tl-dot.<status>` 类**（Task 6 修复轮把颜色收敛成行内 `style`，唯一来源是 `tiers.js` 的 `TIER[..].dot`）。类名只剩状态钩子：`tl-yes`（已勾选）、`tl-no`（未被否决的语义标记，**无 CSS 规则也无消费方**）。**要断档位就读 `state.rows[i].status` 或 dot 的 `title`，不要写 `.tl-dot.high`。**
- **标签库尾部多了一条 `reviewtag9`**（Task 6 的 P9b 反事实用临时目录建的，后端**没有删标签端点**）。它追加在**库尾**，0–2 索引仍是「三分/上篮/罚球」⇒ 本任务的 `1`–`9` 索引不受扰动。**断标签库要断「前三条 == 快照」或「包含」，别写 `length == 3`。**
- **spec 收尾那行 console 文案已变**（任务数 25→30）。**不要**对那行 console 文案写断言——Expected 只要求 pytest `1 passed`。

若 Step 3 里「当前行滚进了表格可视区」那条断言红（`el-table` 的 `setCurrentRow` 不一定顺带滚动），就在 `pick()` 里 `setCurrentRow` 之后补两行：`const el = document.querySelector("#app .el-table__row.current-row"); el?.scrollIntoView({ block: "nearest" });`——别引 el-table 的内部方法。

接线：在**已有的** `onMounted` / `onBeforeUnmount`（Task 5 那对）里各补一句——不要新开第二对 hook：

```js
onMounted(async () => {
  store.on(onState);
  unbindHotkeys = bindHotkeys(keyMap, { disabled: modalOpen });   // ← 新增
  try { await store.refresh(); } catch (e) { ElMessage.error(e.message); }
});
onBeforeUnmount(() => {
  store.off(onState);
  if (unbindHotkeys) unbindHotkeys();                               // ← 新增
});
```

另外把 `import { dispT } from "../shared/time.js";` 加进 import（`deleteCurrent` 的确认框要显示显示时刻）。

- [ ] **Step 3: 跑 e2e 确认通过**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/e2e/test_edit_bay_e2e.py`
Expected: PASS

- [ ] **Step 4: 构建 + 冒烟 + 产物 pytest**

配方 **A**、**C**、**D**。Expected: 全 PASS

- [ ] **Step 5: 提交**

```bash
git add frontend/src/edit/EditBay.vue tests/e2e/edit_bay.spec.mjs bbh/static
git commit -m "feat(frontend): 剪辑台键盘复核流（↑↓ 空格 X 1-9 D Ctrl+Z）

键盘选中一行统一走 pick()：curKey + tableRef.setCurrentRow（后者触发
current-change → onPick），键盘与鼠标不会走出两套当前行状态。
空格勾上后 120ms 自动跳下一颗「未勾选且未被否决」的球，找不到绕回表头——
复核长列表不用回鼠标。X 走 markVerdict(not_goal)（同键再按=取消，联动取消进片）；
D 二次确认后按 key 删除（非破坏，可从「⋯ 恢复已删」找回）。
Ctrl+Z 栈空只提示不报错；有则弹「已撤销：{label}」（R17）。
四道前置闸在本任务定型：dlg 四弹窗状态 + modalOpen 闸门留给 Task 8-10/9
（K/J/L 与 ←/→ 归播放器、? 归帮助窗），落地时只往 keyMap 里加键。

e2e 追加键盘段：真服务上按真键、断言服务端 selection/verdicts 真变、
撤销后回到初始态（含「打标顺带勾选」这条后端既有行为的显式断言与复原）。"
```

---

### Task 8: 右栏——切片播放器 + 判定依据卡

**Files:**
- Create: `frontend/src/edit/ClipPanel.vue`、`frontend/src/edit/BasisPanel.vue`
- Modify: `frontend/src/edit/EditBay.vue`（右栏编排 + `offset`/`clipDur` + 键 `K J L ← →`）
- Modify: `tests/e2e/edit_bay.spec.mjs`（追加「右栏」段）
- Modify: `bbh/static/**`（配方 A 的产物）

**Interfaces:**
- Consumes: `clipUrl(videoPath, t, before, after)`（Task 1 的 `media.js`）、`tsec`/`dispT`（Task 1）、`tierOf`（Task 6）、`act`（Task 4）、`write`/`verdicts`/`tags`（Task 6）
- Produces:
  - `<ClipPanel ref="clipRef" :row :video-path :offset :before :after @dur @apply-all>`；`defineExpose({ togglePlay(), seekBy(d) })`（键盘 `K/J/L/←/→` 用，**不暴露 video 元素**）
  - `<BasisPanel :row :verdict :thumb-flag :cross-flag :feedback @verdict @flag @delete>`（**无 `:tags`/`@tag`**：原型右栏没有打标签控件，见该组件里的说明）
  - `EditBay` 内：`curRow`（按 key 在**全部** `rows[]` 里找——筛选把它藏起来时右栏也不清空）、`offset`（`ref(0)`，Task 9 的校准弹窗驱动，R7）、`durOf`/`setDur`/`applyAll`（R21）
- 依据卡内容**全部读服务端**（`row.terms`/`row.net`/`row.ev`/`row.reason`、`feedback.summaryMd`）——原型里前端自拼的 `sideScore/posScore/confTerms/reason()` 一律不实现（R20）

- [ ] **Step 1: 新建 `ClipPanel.vue`**

```vue
<script setup>
// 右栏上半：切片播放器 + 分色进度条 + 播放控制（spec 3、原型 §4）。
//
// 用原生 <video>（不是 el-* 封装）：autoplay 必须配 muted——切片要等服务端
// 现切，注入时用户手势的激活窗口早过了，未静音的自动播放会被浏览器拦成
// 黑屏不动（旧界面踩过，app.py:948 同款处置）。默认 2× 也是旧界面的省时设定。
import { computed, ref, watch } from "vue";
import { clipUrl } from "../shared/media.js";
import { tsec } from "../shared/time.js";

const props = defineProps({
  row: { type: Object, default: null },
  videoPath: { type: String, default: "" },
  offset: { type: Number, default: 0 },
  before: { type: Number, default: 5 },      // 会话内生效的前后秒（R21）
  after: { type: Number, default: 2 },
});
const emit = defineEmits(["dur", "apply-all"]);

const videoEl = ref(null);
const playing = ref(true);
const rate = ref(2);                          // 默认 2×（旧界面同款）
const loop = ref(true);
const playheadPct = ref(0);

const src = computed(() => props.row
  ? clipUrl(props.videoPath, tsec(props.row, props.offset), props.before, props.after)
  : "");

// 进球前占比：蓝段到红线为止（红线 3px 骑在交界上，所以两段各减 1px）
const goalPct = computed(() => {
  const total = (props.before || 0) + (props.after || 0);
  return total > 0 ? (props.before / total) * 100 : 100;
});

// 换行/切片重来时播放头归零（否则会停在上一条的位置，看着像卡住）
watch(() => src.value, () => { playheadPct.value = 0; playing.value = true; });

function togglePlay() {
  const v = videoEl.value;
  if (!v) return;
  if (v.paused) v.play(); else v.pause();
}
function seekBy(d) {
  const v = videoEl.value;
  if (!v) return;
  const end = Number.isFinite(v.duration) ? v.duration : 0;
  v.currentTime = Math.max(0, Math.min(end, v.currentTime + d));
}
// 帧步进按固定 30fps 假设（原型同款，见 Global Constraints 的 R35）。
// ⚠ 理由**不是**「切片都是 30fps 导出的」——`bbh/app.py` 的 `_cut_preview`
// 只做 `-ss/-t/scale/crf`、**没有任何 fps 归一**（无 `-r`），切片帧率 = 源帧率；
// 而 Plan A 的 `state_payload` 也没有 `fps` 字段。所以非 30fps 源上步进会偶尔
// 落在两帧之间、由浏览器就近吸附（差半帧的观感问题，不动数据）。
const stepFrame = (n) => seekBy(n / 30);
const onTime = () => {
  const v = videoEl.value;
  if (v && v.duration) playheadPct.value = (100 * v.currentTime) / v.duration;
};
function onBar(e) {
  const v = videoEl.value;
  const r = e.currentTarget.getBoundingClientRect();
  if (v && v.duration) v.currentTime = v.duration * ((e.clientX - r.left) / r.width);
}
const setRate = (r) => {
  rate.value = r;
  if (videoEl.value) videoEl.value.playbackRate = r;
};
const setLoop = (v) => { loop.value = v; if (videoEl.value) videoEl.value.loop = v; };

defineExpose({ togglePlay, seekBy });
</script>

<template>
  <div class="clippanel">
    <div class="videobox">
      <video ref="videoEl" :src="src" muted autoplay playsinline
             :loop="loop" :playbackRate="rate"
             @timeupdate="onTime" @play="playing = true" @pause="playing = false" />
    </div>

    <!-- 三段宽度：蓝 + 红(3px) + 绿 必须正好 100%。红色那 3px 要**骑在**交界上，
         所以左右各让出 1.5px（合计 3px）——写 `- 1px` 会让总宽变成 100% + 1px，
         红线右移 1px 并溢出容器（`.seg-red { width: 3px }`，见下面样式）。 -->
    <div class="tricolor" @click="onBar"
         title="点击跳转：蓝=进球前 红=进球时刻 绿=进球后">
      <div class="seg-blue" :style="{ width: `calc(${goalPct}% - 1.5px)` }" />
      <div class="seg-red" />
      <div class="seg-green" :style="{ width: `calc(${100 - goalPct}% - 1.5px)` }" />
      <!-- ②：标签吃偏移（dispT(row, offset)，与表格时刻列同源——R34 的同类；红线画
           的就是切点、切点吃偏移）；left 绑定与偏移无关（位置由前后秒决定），不动。
           （交付码 ClipPanel.vue:122） -->
      <span class="goalmk" :style="{ left: goalPct + '%' }">进球 {{ row ? dispT(row, offset) : "" }}</span>
      <span class="playhead" :style="{ left: playheadPct + '%' }" />
    </div>

    <div class="pctrl">
      <el-button-group size="small">
        <el-button title="后退一帧" @click="stepFrame(-1)">⏮ 1帧</el-button>
        <el-button @click="togglePlay">{{ playing ? "⏸" : "▶" }}</el-button>
        <el-button title="前进一帧" @click="stepFrame(1)">1帧 ⏭</el-button>
      </el-button-group>
      <el-select :model-value="rate" size="small" style="width: 78px"
                 @change="setRate">
        <el-option :value="0.5" label="0.5×" />
        <el-option :value="1" label="1×" />
        <el-option :value="2" label="2×" />
      </el-select>
      <el-switch :model-value="loop" size="small" active-text="循环"
                 @change="setLoop" />
      <span class="lbl">片段</span>
      <el-input-number :model-value="before" :min="2" :max="12" :step="0.5"
                       size="small" style="width: 92px"
                       @change="(v) => emit('dur', { before: v, after })" />
      <span class="lbl">前 /</span>
      <el-input-number :model-value="after" :min="1" :max="6" :step="0.5"
                       size="small" style="width: 92px"
                       @change="(v) => emit('dur', { before, after: v })" />
      <span class="lbl">后</span>
      <el-button size="small" plain @click="emit('apply-all', { before, after })">
        应用到全部
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.clippanel { padding: 10px 14px 4px; }
.videobox { background: #000; border-radius: 8px; overflow: hidden; }
.videobox video { display: block; width: 100%; max-height: 52vh; background: #000; }
.tricolor { position: relative; margin-top: 8px; height: 26px;
            display: flex; align-items: stretch; cursor: pointer; user-select: none; }
.seg-blue { background: linear-gradient(90deg, #2b5f9e, #3d7ad1);
            border-radius: 13px 0 0 13px; }
.seg-red { width: 3px; background: #f56c6c; }
.seg-green { background: linear-gradient(90deg, #3f9e4f, #59b86a);
             border-radius: 0 13px 13px 0; }
.goalmk { position: absolute; top: 5px; font-size: 10px; color: #f8b4b4; }  /* left 随切点内联（:style），修复轮改 */
.playhead { position: absolute; top: 6px; width: 2px; height: 14px;
            margin-left: -1px; background: #fff; }
.pctrl { display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
         margin-top: 10px; font-size: 12.5px; color: #cfd3dc; }
.lbl { color: #a3a6ad; }
</style>
```

- [ ] **Step 2: 新建 `BasisPanel.vue`**

```vue
<script setup>
// 右栏下半：判定依据卡（常显「时刻 + 档位 + ⓘ」）+ 两排按钮 + 出手人 + 反馈摘要。
//
// 依据卡的每个数字都是**服务端算好的**：terms{base,up,down,pos} 与 reason 由
// /api/state 下发（api_session.py:112-127）；原型里前端自拼的 sideScore/
// posScore/confTerms/reason() 一律不实现（R20）。旧版 events.json 会给出
// ev==null / net==null，卡要少画那几行，而不是显示 0。
import { computed } from "vue";
import { dispT } from "../shared/time.js";
import { tierOf } from "./tiers.js";

const props = defineProps({
  row: { type: Object, required: true },
  offset: { type: Number, default: 0 },       // 时刻校准偏移（R34：卡头的时刻与表格同源）
  verdict: { type: String, default: "" },     // "" | "goal" | "not_goal"
  thumbFlag: { type: Boolean, default: false },
  crossFlag: { type: Boolean, default: false },
  feedback: { type: Object, default: () => ({}) },
});
// 右栏**没有**打标签控件：原型右栏两排按钮只有 ✅是进球 / ❌未进球 / 🗑删除 /
// ⚠缩略图有误 / ✚十字有误（`proto-edit-inventory.md` §6 与「右栏按钮两排」），
// 打标签的入口是**表格标签列**（allow-create）与**键盘 1–9** —— 见 Task 6。
// 所以这里既没有 tags prop 也不 emit("tag")（复审发现计划多写了三处死声明）。
const emit = defineEmits(["verdict", "flag", "delete"]);

const ev = computed(() => props.row.ev);
const net = computed(() => props.row.net);
const terms = computed(() => props.row.terms);
const num = (v) => (v == null ? "—" : Number(v).toFixed(2));

// 完整数据块：字段名与 events.json 逐字一致（调优取证时要能对着原档看）
const raw = computed(() => {
  const e = ev.value;
  if (!e) return "";
  return [
    `cross_off   ${e.crossOff}`,
    `above_n     ${e.aboveN}`,
    `below_n     ${e.belowN}`,
    `above_span  ${e.aboveSpan}`,
    `below_span  ${e.belowSpan}`,
    `descent_px  ${e.descentPx}`,
    `below_big   ${e.belowBig}`,
    `pop_back    ${e.popBack}`,
    `net_post    ${net.value ? net.value.post : "None"}`,
    `net_ctrl    ${net.value ? net.value.ctrl : "None"}`,
    `net_local   ${net.value ? net.value.local : "None"}`,
  ].join("\n");
});
</script>

<template>
  <div class="basis">
    <div class="head">
      <span class="t">{{ dispT(row, offset) }}</span>
      <el-tag size="small" effect="dark" :type="tierOf(row).type">
        {{ tierOf(row).text }}
      </el-tag>
      <el-popover placement="top-start" :width="540" trigger="hover" :show-after="120">
        <template #reference>
          <el-icon class="conf-ico"><InfoFilled /></el-icon>
        </template>
        <div class="pop">
          <div class="pop-title">判定依据（鼠标移开即收起）</div>
          <template v-if="ev && terms">
            <div>置信度 <b>{{ row.conf.toFixed(2) }}</b> ＝ 基础 {{ num(terms.base) }}
              ＋ 上方证据 {{ num(terms.up) }} ＋ 下方证据 {{ num(terms.down) }}
              ＋ 位置 {{ num(terms.pos) }}</div>
            <div class="dim">档位：≥0.70 🟢高置信 · 0.50~0.69 🟡待确认 · 更低或被否决门槛拦截 = ⚪低置信（低置信不进集锦）</div>
          </template>
          <template v-if="net">
            <div>网区信号 {{ net.post }}（阈 5.0） · 对照区 {{ net.ctrl }} · 同对局部比
              <span :style="{ color: net.local ? '#67C23A' : '#f56c6c' }">
                {{ net.local ? "✓ 通过" : "✗ 等亮" }}</span>（门 ≥3×）</div>
            <div class="dim">网区 ≥5.0 且同对局部比 ≥3× 才算真网摆；网动只记录不升档</div>
          </template>
          <div class="reason">📢 {{ row.reason }}</div>
          <template v-if="ev">
            <el-divider />
            <div>完整判定数据</div>
            <div class="dim">（轨迹/下落/网动原始字段，调优取证用）</div>
            <pre class="rawv">{{ raw }}</pre>
          </template>
          <div v-if="!net" class="warn">net_* 为 None：这份结果没跑过网信号那一版，重跑检测后可见</div>
        </div>
      </el-popover>
    </div>

    <div class="rowbtns">
      <el-button type="success" size="large" style="flex: 1"
                 :plain="verdict === 'goal'"
                 title="误判反馈专用：被降级的真进球在此翻案（写 feedback.json 给判定调优）——正常确认进球用空格勾选即可"
                 @click="emit('verdict', 'goal')">
        ✅ 是进球 <span class="dim">翻案反馈</span>
      </el-button>
      <el-button type="danger" size="large" style="flex: 1"
                 :plain="verdict === 'not_goal'"
                 title="误判反馈：检测认为进了但实际没进——写 feedback.json 给判定调优，并取消进片"
                 @click="emit('verdict', 'not_goal')">
        ❌ 未进球 <span class="dim">X</span>
      </el-button>
    </div>

    <div class="subbtns">
      <el-button plain style="flex: 1" title="删除此球（D）" @click="emit('delete')">
        🗑 删除 <span class="dim">D</span>
      </el-button>
      <el-button plain style="flex: 1" :type="thumbFlag ? 'warning' : 'info'"
                 @click="emit('flag', 'thumb')">
        {{ thumbFlag ? "✅ 已标缩略图有误" : "⚠ 缩略图有误" }}
      </el-button>
      <el-button plain style="flex: 1" :type="crossFlag ? 'warning' : 'info'"
                 @click="emit('flag', 'cross')">
        {{ crossFlag ? "✅ 已标十字有误" : "✚ 十字有误" }}
      </el-button>
    </div>

    <div class="shooter">
      <template v-if="row.shooter">
        <span class="lbl">出手人 {{ row.shooter.dot }}
          {{ row.shooter.releaseT != null ? row.shooter.releaseT.toFixed(2) + "s" : "" }}</span>
        <img v-for="(u, i) in row.shooter.thumbs" :key="i" :src="u" class="shthumb" />
      </template>
      <span v-else class="lbl">尚无出手人回扫结果——点顶栏「🧍 识别出手人」生成动图</span>
      <el-link type="primary" href="/wall.html">在出手人墙中查看 →</el-link>
    </div>

    <div class="fbref">
      <div>{{ feedback.summaryMd }}</div>
    </div>
  </div>
</template>

<style scoped>
.basis { padding: 4px 14px 14px; }
.head { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #e5eaf3; }
.conf-ico { color: #7c7f86; font-size: 12px; cursor: help; }
.conf-ico:hover { color: #e8622d; }
.pop { font-size: 13px; line-height: 1.7; }
.pop-title { font-weight: 600; margin-bottom: 4px; }
.dim { color: #8b8e95; font-size: 12px; }
.reason { color: #9aa0aa; margin-top: 4px; }
.rawv { font-family: Consolas, monospace; white-space: pre; font-size: 12px;
        color: #cfd3dc; background: #1d1d1d; padding: 8px; border-radius: 6px; }
.warn { color: #e6a23c; font-size: 12px; }
.rowbtns, .subbtns { display: flex; gap: 8px; margin-top: 10px; }
.shooter { display: flex; align-items: center; flex-wrap: wrap; gap: 8px;
           margin-top: 12px; padding: 8px 10px; background: #232323;
           border-radius: 8px; font-size: 12.5px; }
.shthumb { height: 64px; border-radius: 6px; }
.fbref { margin-top: 12px; font-size: 11.5px; color: #7f838b; }
</style>
```

（出手人那一行里 `尚无出手人回扫结果…` 是**新文案**：原型的数据总是齐全，没有这条兜底；`shooter.name` 归人名不在这份 payload 里，Plan C 的墙页才有。）

- [ ] **Step 3: 右栏接进 `EditBay.vue`**

`<script setup>` 新增（放在 `onPick` 附近）：

```js
const offset = ref(0);                    // 时刻校准偏移（Task 9 的弹窗驱动，R7）
const clipRef = ref(null);
const durAll = ref(null);                 // 「应用到全部」的会话级前后秒（R21）
const durMap = ref({});                   // 单行改动：key → {before, after}

const feedback = computed(() => state.value?.feedback || {});
const thumbFlags = computed(() => state.value?.flags?.thumb || []);
const crossFlags = computed(() => state.value?.flags?.cross || []);

const curRow = computed(() => rows.value.find((r) => r.key === curKey.value) || null);
// 行的前后秒：单行改动 > 应用到全部 > 服务端给的 row.before/after
const durOf = (row) => durMap.value[row.key] || durAll.value
  || { before: row.before, after: row.after };
const setDur = ({ before, after }) => {
  if (curKey.value) durMap.value = { ...durMap.value, [curKey.value]: { before, after } };
};
const applyAll = ({ before, after }) => {
  durAll.value = { before, after };
  durMap.value = {};                      // 全局值优先，旧的单行值全部作废
  ElMessage.success(`已应用到全部：前 ${before}s / 后 ${after}s`);
};
```

键位补五个（加进 Task 7 的 `keyMap`，**别新开一份**）：

```js
keyMap.k = () => clipRef.value?.togglePlay();
keyMap.j = () => clipRef.value?.seekBy(-2);
keyMap.l = () => clipRef.value?.seekBy(2);
keyMap.left = () => clipRef.value?.seekBy(-2);
keyMap.right = () => clipRef.value?.seekBy(2);
```

模板里 `.right` 换成：

```vue
      <div class="right">
        <div v-if="!loaded" class="stage-hint">{{ hint }}</div>
        <div v-else-if="!curRow" class="stage-hint">← 点击左侧表格任意一行开始复核</div>
        <template v-else>
          <ClipPanel ref="clipRef" :row="curRow" :video-path="state.videoPath"
                     :offset="offset" :before="durOf(curRow).before"
                     :after="durOf(curRow).after"
                     @dur="setDur" @apply-all="applyAll" />
          <BasisPanel :row="curRow" :offset="offset"
                      :verdict="verdicts[curRow.key] || ''"
                      :thumb-flag="thumbFlags.includes(curRow.key)"
                      :cross-flag="crossFlags.includes(curRow.key)"
                      :feedback="feedback"
                      @verdict="(k) => write(act.markVerdict(curRow.key, k))"
                      @flag="(f) => write(act.toggleFlag(curRow.key, f))"
                      @delete="deleteCurrent" />
        </template>
      </div>
```

import 补两行：

```js
import ClipPanel from "./ClipPanel.vue";
import BasisPanel from "./BasisPanel.vue";
```

**顺手补两处 `:offset`**：Task 6 给 `EventTable` / `TimelineStrip` 的绑定都没传 `offset`（吃默认值 0）——现在 `offset` 有了，两处都加上 `:offset="offset"`，让校准弹窗（Task 9）一改就三处同步。

- [ ] **Step 4: 往 e2e 里追加右栏段（先红）**

在 `if (errs.length) throw …` **之前**（Task 7 那段之后）追加：

```js
  // —— 右栏（Task 8）：切片播放器 + 判定依据卡 ——
  // ⚠ 本段**自己声明依赖的视口**，不继承上一段剩下的那份：Task 7 的段把视口
  // 压到 1600×420 好让「滚进可视区」那条断言有牙（它段尾已还原，但**别依赖
  // 邻居的还原**——每段自足才是本 spec 的纪律）。本段下面的
  // 「第二排按钮不用滚动就在窗内」量与 09-19 用户裁决（900/800/720 三种窗高
  // 下都点得到）都是按 **900** 窗高定的。实测继承到 420 的后果：右栏可视带
  // 只剩 ~327px 而内容要 ~435px（`editbayH=380, topbarH=53, rightTop=93`）⇒
  // `.subbtns` 下沿落在视口 y≈443 ⇒ 本段两条断言必红，而且红的原因与本段
  // 代码毫无关系、会把实现者引去改 `.right` 布局（错的修改方向）。
  await page.setViewportSize({ width: 1600, height: 900 });
  // 再把当前行挪回第一颗（Task 7 那段结束时停在连按 ↓ 后的第 8 行＝索引 7）
  await rowCell(0).click();
  const cur = (await state()).rows[0];
  const secs = (t) => { const [m, s] = t.split(":"); return Number(m) * 60 + Number(s); };
  const clipSrc = () => page.locator("#app .videobox video").getAttribute("src");

  const u = new URL(await clipSrc(), BASE);
  eq(u.pathname, "/media/clip", "播放器指向切片端点");
  eq(Number(u.searchParams.get("t")), secs(cur.t), "切片 t = 该行的显示时刻");
  ok(u.searchParams.has("before") && u.searchParams.has("after"), "切片带前后秒");
  ok(u.searchParams.has("v"), "切片 URL 带 v=（击穿缓存，换视频/换校准后不播旧切片）");
  eq(await page.locator("#app .tricolor").count(), 1, "分色进度条在位");
  eq(await page.locator("#app .tricolor .playhead").count(), 1, "播放头在位");

  // ⓘ 依据卡：常显只有「时刻 + 档位 + ⓘ」，算式/证据/reason 全在 popover 里
  const tagTxt = (await page.locator("#app .basis .head .el-tag").innerText()).trim();
  ok(["🟢 高", "🟡 待", "⚪ 低"].includes(tagTxt), `常显档位（实际「${tagTxt}」）`);
  ok(!(await page.locator("#app .basis").innerText()).includes("置信度"),
     "依据卡不常显算式（09-17/18 裁决：算式/网区/结论/完整数据只活在 ⓘ 里）");
  // ⚠ 这里**别**断 `count() === 0`（原计划写的就是它，是错的）。el-popover 的
  // `persistent` **默认就是 true**（`element-plus/es/components/popover/src/popover.mjs`
  // 的 props，其自带文档也写着「persistent 为 false 时 popover 才会被销毁」），
  // 它把该 prop 透传给 el-tooltip ⇒ `tooltip/src/content…mjs` 里
  // `shouldRender = persistent ? true : open` **恒 true** ⇒ 内容是**挂载即渲染、
  // 用 `v-show` 隐藏**。BasisPanel 一挂载（上面点了行之后）`pre.rawv` 就在 body 里，
  // 而 `count()` **不看可见性** ⇒ 那种断法必红；更坏的是下面那条 `waitFor`
  // 会因为「本来就在」而**假过**，让人误判成环境问题。
  // 要钉的裁决是「算式/证据只活在 ⓘ 里」，所以断**可见性**：
  ok(!(await page.locator("body pre.rawv").isVisible()),
     "ⓘ 收着时完整判定数据块不显示（popover 常驻 DOM、v-show 隐藏）");
  await page.locator("#app .basis .conf-ico").hover();
  // 选择器**不带 `#app`**：el-popover 的内容 teleport 到 body，加 #app 会匹配不到
  await page.locator("body pre.rawv").waitFor({ timeout: 5000 });
  const pop = (await page.locator("body .pop").innerText()).replace(/\s+/g, " ");
  ok(pop.includes("判定依据（鼠标移开即收起）"), "ⓘ 标题逐字");
  ok(pop.includes(`置信度 ${cur.conf.toFixed(2)} ＝ 基础`), "置信度算式自洽（算式用服务端 terms，前端不自拼）");
  ok(pop.includes("cross_off") && pop.includes("net_post"), "完整判定数据块逐字段");
  ok(pop.includes(cur.reason.slice(0, 8)), "reason 来自服务端（不前端自拼）");
  await page.mouse.move(0, 0);

  // 两排按钮 + 裁决联动（✅ 是进球 → 勾选）。
  // ⚠ 取消反馈**不**还原那次自动勾选——这是 Task 4 已交付并过审的语义，别按
  // 「取消就该还原」写断言（原计划写的就是那句，会必红、还把责任推给实现者）。
  // 真机制在 `frontend/src/edit/actions.js`：`touchSel = wasSel !== wantSel`。
  // 走「同按钮再点」这条取消路径时 `before === verdict === "goal"` ⇒
  // `wantSel = true`，而此时行**已经是勾上的**（第一次 ✅ 经 R16 勾的）⇒
  // `wasSel = true` ⇒ `touchSel === false` ⇒ **那个补勾选的 PATCH 根本不发**。
  // 后端也不管勾选：`mark_verdict` 的取消分支只 `del fb["verdicts"][hit]` +
  // `_save_feedback`，`POST /api/feedback/{key}` 不碰 selection.json ⇒ 勾选留着。
  // 与「撤销打标签不还原勾选」同病（见 actions.setTag 注释），本计划不修。
  const vbtn = page.locator("#app .basis .rowbtns .el-button").first();
  await vbtn.click();
  await waitFor(async () => (await state()).verdicts[cur.key] === "goal", "✅ 记为是进球");
  await waitFor(async () => (await state()).rows[0].selection, "✅ 联动勾选进片（R16）");
  await vbtn.click();
  await waitFor(async () => !(await state()).verdicts[cur.key], "同按钮再点取消反馈");
  ok((await state()).rows[0].selection,
     "取消反馈**不动**勾选（取消路径 touchSel=false）");
  eq(await page.locator("#app .basis .subbtns .el-button").count(), 3, "第二排三个按钮");

  // 「操作按钮排在出手人 chip 之上且不用滚动就能点到」（09-19 裁决）
  const bRow = await page.locator("#app .basis .rowbtns").boundingBox();
  const bSub = await page.locator("#app .basis .subbtns").boundingBox();
  const bChip = await page.locator("#app .basis .shooter").boundingBox();
  const vp = page.viewportSize();
  ok(bRow.y + bRow.height <= bSub.y + 1, "两排按钮：✅/❌ 在第一排（在第二排之上）");
  ok(bSub.y + bSub.height <= bChip.y + 1, "两排按钮整组排在出手人 chip 之上");
  ok(bSub.y >= 0 && bSub.y + bSub.height <= vp.height,
     `第二排按钮不用滚动就在窗内（底 ${Math.round(bSub.y + bSub.height)} / 窗高 ${vp.height}）`);
  // 「不用滚动」这半句得连「右栏真的没在滚」一起断：否则将来 `.right` 滚了
  // 一截、按钮刚好落在窗内，这条会绿得毫无意义，而 09-19 裁决的前提是整块
  // 布局在 900/800/720 三种窗高下**放得下**（不是靠滚到了位置）。
  // ⚠ 但**只断 `scrollTop === 0` 是没有牙的**（Task 8 复审实测）：溢出但还没滚过
  // 时 `scrollTop` 照样是 0——720 窗高 + 真切片下实测 `scrollHeight 659 >
  // clientHeight 627` 而 `scrollTop` 仍是 0。**要断就断真的没有溢出**：
  eq(await page.locator("#app .right").evaluate((el) => el.scrollTop), 0,
     "右栏没有纵向滚动——按钮在窗内是靠布局，不是靠滚");
  ok(await page.locator("#app .right")
       .evaluate((el) => el.scrollHeight <= el.clientHeight),
     "右栏内容没有溢出（scrollTop===0 单独不够：溢出未滚时它也是 0）");

  // 误标 toggle：点一次标上、再点取消（后端 _toggle_mark_flag）
  const fbtn = page.locator("#app .basis .subbtns .el-button").nth(1);
  await fbtn.click();
  await waitFor(async () => (await state()).flags.thumb.includes(cur.key), "缩略图误标落盘");
  ok((await fbtn.innerText()).includes("已标"), "按钮文案切到已标态");
  await fbtn.click();
  await waitFor(async () => !(await state()).flags.thumb.includes(cur.key), "再点取消误标");

  // 前后秒：改「前」→ 切片 URL 跟着变（会话内生效，/media/clip 真按它切）
  // 两个输入框**都**先记下原值：本段最后要把会话级默认**原样还回去**，
  // 免得 durAll 被改成 6/2 之后留给 Task 9/10（它们假设的是夹具原值）。
  const beforeInput = page.locator("#app .pctrl .el-input-number input").first();
  const afterInput = page.locator("#app .pctrl .el-input-number input").nth(1);
  const before0 = new URL(await clipSrc(), BASE).searchParams.get("before");
  const after0 = new URL(await clipSrc(), BASE).searchParams.get("after");
  await beforeInput.fill("6");
  await beforeInput.press("Enter");
  await waitFor(async () => new URL(await clipSrc(), BASE).searchParams.get("before") === "6",
                "改「前」之后切片 URL 跟着变");
  ok(before0 !== "6", `改之前是 ${before0}`);
  // 「应用到全部」= 会话级默认（R21：后端没有 before/after 端点）
  await page.locator("#app .pctrl button", { hasText: "应用到全部" }).click();
  ok(await toast("已应用到全部：前 6s"), "应用到全部有提示");

  // K/J/L 与 ←/→：**Task 8 的招牌交付**（播放器键盘控制），必须有断言。
  // ⚠ 夹具里**没有真的视频文件**（见本节末尾「夹具限制」），所以只能钉到
  // 「不抛异常 + 切片 URL 不被这几个键改坏」这一层——真播放/暂停/跳转行为
  // 在本夹具下验不了，别为了让断言好看去假装验了。
  const srcBefore = await clipSrc();
  for (const k of ["KeyK", "KeyJ", "KeyL", "ArrowLeft", "ArrowRight"]) await keys(k);
  ok(!errs.length, "K/J/L/←/→ 没打出 JS 异常");
  eq(await clipSrc(), srcBefore, "这五个键只动播放位置，不改切片 URL");

  // 「筛选把当前行藏起来时右栏也不清空」——`curRow` 按 key 在**全部** rows[]
  // 里找（本任务 Interfaces 的承诺），必须有断言钉住，否则它退化成「按可见行找」
  // 也没人知道。档位筛选是 `el-radio-group`（`全部 / 未勾选 N / 🟢 / 🟡 / ⚪`，
  // 值 all/unchecked/high/review/low），所以点它按 emoji 找按钮。
  // ⚠ 这条**在本任务是对的、但会被 Task 11 的 R22 反转**：R22（计划裁决表）
  // 定的是「`curKey` 必须始终落在 `visibleRows` 里，筛选把它藏起来就连它一起清」
  // ⇒ 到 Task 11 之后右栏回空态、下面那条断言随之作废，**由 Task 11 Step 1
  // 负责改写这个块**（改成断「右栏回空态」）。**本任务不要为了「让它将来还成立」
  // 去改 `curRow` 的查找范围**——那是 R22 已经裁决过的方向；也**不要删**这条，
  // 它在 Task 11 之前是有效断言。
  const timeCells = async () =>
    (await page.locator("#app .el-table__row td:nth-child(3)").allInnerTexts())
      .map((s) => s.trim());
  const tierEmoji = { high: "🟢", review: "🟡", low: "⚪" };
  const other = ["high", "review", "low"].find((s) => s !== cur.status);
  await page.locator("#app .el-radio-button", { hasText: tierEmoji[other] }).click();
  // 先证明当前行**确实**被藏起来了——不然下面那条断言什么都没验（假绿）
  ok(!(await timeCells()).includes(cur.t),
     `当前行（${cur.t}）已被筛到「${other}」藏起来`);
  eq(await page.locator("#app .videobox video").count(), 1,
     "当前行被筛选藏起来时右栏仍不清空（curRow 在全部 rows[] 里找）");
  await page.locator("#app .el-radio-button", { hasText: "全部" }).click();
  await waitFor(async () => (await timeCells()).includes(cur.t), "筛选切回「全部」");

  // —— 段尾复原夹具（Task 6/7 同款纪律，别省）——
  // 本段留下了：第 1 行**勾着**（取消反馈不动勾选，见上）、会话级前后秒被
  // 改成 6/2、撤销栈多了 4 条（✅、✅取消、⚠标、⚠取消）。
  await beforeInput.fill(before0);
  await beforeInput.press("Enter");
  await afterInput.fill(after0);
  await afterInput.press("Enter");
  await page.locator("#app .pctrl button", { hasText: "应用到全部" }).click();
  await waitFor(async () => new URL(await clipSrc(), BASE).searchParams.get("before") === before0
                             && new URL(await clipSrc(), BASE).searchParams.get("after") === after0,
                "前后秒已还原成夹具原值");
  await page.locator("#app .el-table__row").first().locator(".el-checkbox").click();
  await waitFor(async () => (await state()).rows.every((r) => !r.selection),
                "第 1 行的勾选已复原（回到全不勾）");
  // 注意：复原那次勾选点击**也会入栈**（走 actions.setSelection）⇒ 本段结束时
  // 栈里还剩 5 条。后续任务不要假设栈是空的（要验撤销就自己先造一步）。
  ok(!errs.length, "右栏段没打出 JS 异常");
```

**夹具限制（必读，别绕过去）**：`tests/e2e/test_edit_bay_e2e.py` 的 `_fixture` **只**建
`work/` 里的事件/标定档，**从来不创建 `videos/<name>.mp4`**（只把路径字符串写进
`events.json`）。因此 `GET /media/clip` 每次都走 `bbh/web/media.py` 的
`_require_current_video` ⇒ **404「视频不存在」**（还没到 `_cut_preview`，所以不慢、
也不碰 ffmpeg）。含义：本任务所有断言只能落在 **URL / 属性 / DOM** 层——`video.src`
是「服务端给的地址长什么样」，与那个文件在不在无关，所以断言照样能过；但
**播放器真行为**（真播放/暂停、播放头随 `timeupdate` 走、分色比例、点条跳转、2×、
循环、K/J/L）**在本夹具下验不了**。**本任务不扩夹具**（造真 mp4 要引入 ffmpeg 依赖，
属于另一件事）——这条限制写明在此，留给 Task 11 收口与最终整支复审判断要不要补。

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/e2e/test_edit_bay_e2e.py`
Expected: FAIL —— 右栏此刻还是空态文案，`#app .videobox video` 取不到 `src`

若「第二排按钮不用滚动就在窗内」那条红：**先核本段开头那句 `setViewportSize({1600, 900})` 还在不在**（420 窗高下这条必红、且与本任务代码无关——不要以为「上一段会还原」就把它删了）。确认视口是 900 之后**别删断言**——那是 09-19 用户裁决（右栏两排按钮上移、GIF 下移就是为了 900/800/720 三种窗高下都点得到）。往 `.right` 的布局上找原因（播放窗按 09-18 是 52vh；`.basis` 与 `.pctrl` 之间是否是 `margin-top` 堆太多），把量到的 y 值写进报告。

**⚠ 三种窗高到底是几号数（Task 8 复审实测，给后来者省一遍工）**：夹具没有真 mp4 ⇒ `<video>` 退化成 150px 高，所以 e2e 只钉了 900 一档。复审核实了真切片的高度按 `52vh` 走（`video = 0.52H`，`.right` 宽 895 时它是紧的一侧），遂**用注入 `aspect-ratio:16/9` 复现真切片**逐档量出：
- **900**：video 468、内容 753 / 可视带 807、`.subbtns` 底 **759** ⇒ 不滚，余量 54
- **800**：video 416、内容 701 / 707、`.subbtns` 底 **707** ⇒ 不滚，**余量只剩 6px**
- **720**：video 374.4、内容 **659.4** / 带 **627** ⇒ **溢出 32px**（`.subbtns` 底 665.4 仍在窗内，但 `.fbref` 底 738.4、`.basis` 底 752.4 **折到窗下**）
- **临界窗高**：`0.52H + 285 = H − 93 ⇒ H ≈ 787.5`（`H ≳ 788` 才不滚）；再叠上「当前行有出手人数据」（chip 34→80，+46px）后 **800 也放不下、900 只剩 8px**

**结论要分两半，别混**：① 用户裁决的字面要求——「**两排按钮**不用滚动就点得到」——**三档全成立**（按钮在内容之上，不受下段折行影响）；②「**右栏整块**放得下、不靠滚」只有 900/800 成立，**720 不成立**。这条属于**已知的布局缺口**，夹具验不了、本计划不修（改右栏在 720 下的高度分配是另一件事），**留给 Task 11 收口与最终整支复审判断**（要修就改 `.right` 的高度分配或 52vh 这个比例，不是改断言）。

若「K/J/L/←/→ 没打出 JS 异常」那条红：**别删断言**——在夹具这个「视频 404」的场景里对 `<video>` 写 `currentTime` **不该抛**（播放器只是没有可播的源）。红说明 `togglePlay()`/`seekBy()` 对「没加载成功」的状态没兜住（比如假设了 `video.duration` 已就绪、或在 `play()` 返回的 Promise 上少了一个 `.catch`）——修 `ClipPanel` 里的那两个函数，把量到的异常原文写进报告。这一条正是 Task 8 唯一能验到的播放器真行为，别放掉。

- [ ] **Step 5: 跑 e2e 确认通过**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/e2e/test_edit_bay_e2e.py`
Expected: PASS

- [ ] **Step 6: 构建 + 冒烟 + 产物 pytest**

配方 **A**、**C**、**D**。Expected: 全 PASS

- [ ] **Step 7: 提交**

```bash
git add frontend/src/edit/ClipPanel.vue frontend/src/edit/BasisPanel.vue \
        frontend/src/edit/EditBay.vue tests/e2e/edit_bay.spec.mjs bbh/static
git commit -m "feat(frontend): 剪辑台右栏——切片播放器 + 判定依据卡

播放器用原生 video（autoplay 必配 muted：切片要等服务端现切，注入时用户手势
的激活窗口早过了，未静音会被浏览器拦成黑屏不动——旧界面同款处置）。默认 2×、
循环、帧步进按 30fps 假设；切片 URL 带 v= 击穿缓存（换校准后不播旧切片）。
分色条：蓝=进球前 / 红=进球时刻 / 绿=进球后，播放头随 timeupdate 走。

依据卡每个数字都读服务端：terms{base,up,down,pos} 与 reason 由 /api/state 下发，
原型里前端自拼的 sideScore/posScore/confTerms/reason() 一律不实现（R20）；
ev/net 为 null（旧版 events.json）时少画那几行，不显示 0。反馈摘要直接用
feedback.summaryMd（服务端文案，不前端拼）。

前后秒没有后端端点 ⇒ 单行改动与会话级「应用到全部」都只活在本会话（R21），
但它们是**真参数**——/media/clip 按 URL 里的 before/after 现切，不是假显示。
K/J/L 与 ←/→ 接进 Task 7 的 keyMap（不新开 map）；curRow 在全部 rows[] 里找，
筛选把当前行藏起来时右栏不清空。"
```

---

### Task 9: 四个弹窗（帮助 / 时刻校准 / 批量删除 / 生成）+ 顶栏收尾 + `?` 键

**Files:**
- Create: `frontend/src/edit/HelpDialog.vue`、`OffsetDialog.vue`、`BulkDeleteDialog.vue`、`GenDialog.vue`
- Modify: `frontend/src/edit/EditBay.vue`（四弹窗接线 + 顶栏三处 + 长任务驱动 + 闸②③修正）
- Modify: `tests/e2e/edit_bay.spec.mjs`（追加「弹窗」段）
- Modify: `bbh/static/**`（配方 A 的产物）

**Interfaces:**
- Consumes: `dlg`/`modalOpen`/`write`/`keyMap`（Task 7 已定型）、`offset`（Task 8）、`act.deleteKeys(keys)`、`jobs.js` 的 `startJob`/`followJob`、`jobmemory.js` 的 `createJobMemory()`、`api.get` + `qs`、`tiers.js` 的 `tierText`、`dispT`
- Produces:
  - `<HelpDialog v-model="dlg.help" :tags="tags" />`
  - `<OffsetDialog v-model="dlg.offset" v-model:offset="offset" @reload="reloadState" />`
  - `<BulkDeleteDialog v-model="dlg.bulk" :rows="rows" :offset="offset" @confirm="bulkDelete" />`
  - `<GenDialog v-model="dlg.gen" :vertical="genVertical" :job="genJob" @start="startGen" @reset="genJob.phase = 'idle'" />`
  - `EditBay` 内：`genVertical`、`genJob`（`reactive({phase, msg, pct, result})`，phase ∈ `idle|running|done|error`）、`jobMem`、`JOBS` 表、`longJob(kind, startUrl, body, slot)`、`resumeJobs()`、`openGen(vertical)`、`startGen({vertical, items})`、`bulkDelete(keys)`、`reloadState()`、`keyMap["?"]`
- **生成 job 的归属**：job 由 `EditBay` 起与跟随（`longJob`），`GenDialog` 只是它的**视图**（收 `job` prop）——所以关掉弹窗再打开，进度还在；Task 10 的检测/回扫接同一个 `longJob`，只往 `JOBS` 表里多两行。
- ⚠️ **本任务要改 Task 7 的一行**：`bindHotkeys(keyMap, { disabled: modalOpen })` ⇒ 见 Step 3 的闸②③修正（帮助窗开着时 `?` 必须还能关自己）。

- [ ] **Step 1: 往 e2e 追加「弹窗」段（先红）**

在 `if (errs.length) throw …` **之前**（Task 8 那段之后）追加：

```js
  // —— 四个弹窗（Task 9）——
  // 段末必须把所有弹窗关掉：`modalOpen` 闸门开着的话，后面任务的每个键都失效。
  const dlg = (cls) => page.locator(`.el-dialog.${cls}`);
  const dtitle = async (cls) => (await dlg(cls).locator(".el-dialog__title").innerText()).trim();
  // `⋯` 菜单是 teleport 到 body 的，不带 #app 前缀；菜单项按文案选
  const menu = async (text) => {
    await page.locator("#app .toolbar .el-dropdown button").click();
    await page.locator(".el-dropdown-menu__item").filter({ hasText: text }).first().click();
  };

  // ① 帮助窗（`?`）：逐字表 + 闸③（开着时只有 `?` 能用）
  await page.keyboard.press("Shift+Slash");           // Shift+/ = `?`
  await dlg("helpdlg").waitFor();
  eq(await dtitle("helpdlg"), "⌨ 键盘流复核 — 快捷键总表", "帮助窗标题逐字");
  eq(await dlg("helpdlg").locator("kbd").count(), 10, "键位表 10 行（含 Ctrl+Z 与 ?）");
  const htxt = await dlg("helpdlg").innerText();
  ok(htxt.includes("1=三分") && htxt.includes("3=罚球"), "灰字当前标签库来自 state.tags");
  ok(htxt.includes("正常确认进球用空格即可，不产生反馈数据"), "表下注逐字");
  await page.keyboard.press("KeyX");                  // 闸③：帮助窗开着时 X 不该生效
  await page.waitForTimeout(300);
  ok(!(await state()).verdicts[cur.key], "帮助窗开着时其它键全失效（闸③）");
  await page.keyboard.press("Shift+Slash");
  await dlg("helpdlg").waitFor({ state: "hidden" });

  // ② 时刻校准：纯前端偏移，三处同源（表格列 / 切片 t），关闭复原
  eq((await rowCell(0).innerText()).trim(), "00:01.2", "校准前的显示时刻");
  await menu("⏱ 时刻校准");
  await dlg("offdlg").waitFor();
  const offIn = dlg("offdlg").locator(".el-input-number input");
  await offIn.fill("1.5");
  await offIn.press("Enter");
  await waitFor(async () => (await rowCell(0).innerText()).trim() === "00:02.7",
                "偏移 +1.5s：时刻列跟着变（dispT）");
  ok(Math.abs(Number(new URL(await clipSrc(), BASE).searchParams.get("t")) - 2.7) < 1e-9,
     "切片 t 也跟着偏移（tsec 同源，三处一起动）");
  eq((await page.locator("#app .basis .head .t").innerText()).trim(), "00:02.7",
     "右栏卡头的时刻同源（R34：三处其实是四处，同一行左右栏不能显示两个时刻）");
  // 清空输入框（R-T9-1）：`Number("")` 与 `Number(null)` 都是 0 ⇒ 不显式挡空串/null
  // 的话这里会**静默把偏移归零**（Task 8 F-1 的同类：坏值不该被当成「设成 0」）。
  // 裁定是「非有限值一律**忽略这次提交**、保留上一个有效值」，所以下面两条断言
  // 都必须**停在 00:02.7**——断言写成 00:01.2 反而会把 bug 当正确行为钉住。
  await offIn.fill("");
  await offIn.press("Enter");
  await page.waitForTimeout(300);
  ok(Math.abs(Number(new URL(await clipSrc(), BASE).searchParams.get("t")) - 2.7) < 1e-9,
     "清空偏移输入框**不改变**偏移（不接受「清空 = 归零」）");
  eq((await rowCell(0).innerText()).trim(), "00:02.7", "时刻列也停在旧值（两处显示不许不一致）");
  // 这条专逮 `v-model="draft"` 那种漏法：门加了、偏移也保住了，但
  // `update:modelValue` 把 `draft` 置成 null、输入框自己变空 ⇒ 屏幕上一处空、
  // 一处还是 2.7。上面两条断不到它（偏移确实没变），非得看输入框不可。
  // ⚠ 期望值是 **1.5 不是 2.7**（Task 9 实现者订正，控制者已核）：这个框里装的是
  // **偏移量本身**（`OffsetDialog.vue:15` `const draft = ref(props.offset)`、
  // `:59` `:model-value="draft"`），而上面刚填进去的就是 1.5；2.7 是
  // 「1.2 + 1.5」那个**显示时刻**，属于时刻列与切片 `t`（上面两条断的），
  // **不属于这个框**。写 2.7 是把三处同源里的两个不同量混成了一个。
  eq(await offIn.inputValue(), "1.5", "输入框自己也不许变空（显示的值就是偏移量）");
  await dlg("offdlg").locator(".el-dialog__footer button").filter({ hasText: "取消" }).click();
  await dlg("offdlg").waitFor({ state: "hidden" });
  eq((await rowCell(0).innerText()).trim(), "00:01.2", "取消 → 偏移复原（不落盘、不残留）");

  // ③ 批量删除：弹窗本身即确认；删完用 ⋯ 恢复，把夹具还原成 12 行
  await menu("🗑 批量删除");
  await dlg("bulkdlg").waitFor();
  const brows = dlg("bulkdlg").locator(".el-checkbox");
  eq(await brows.count(), 12, "清单 = 未删行数");
  ok((await brows.first().innerText()).includes("00:01.2｜🟢"), "行文案 = 时刻｜档位");
  const dbtn = dlg("bulkdlg").locator(".el-dialog__footer button")
    .filter({ hasText: "确认删除所选" });
  ok(await dbtn.isDisabled(), "没勾时删除按钮禁用");
  await brows.nth(0).click();
  await brows.nth(1).click();
  eq((await dbtn.innerText()).trim(), "🗑 确认删除所选（2）", "按钮文案带计数");
  await dbtn.click();
  await dlg("bulkdlg").waitFor({ state: "hidden" });
  await waitFor(async () => (await state()).deleted.length === 2, "批量删除落盘");
  eq((await state()).rows.length, 10, "表格少 2 行");
  await waitFor(async () => (await page.locator("#app .tblfoot").innerText())
                .includes("另有 2 条已删可恢复"), "页脚已删计数跟着变");
  await menu("↩ 恢复已删");
  await waitFor(async () => (await state()).rows.length === 12, "恢复回 12 行");

  // ④ 生成弹窗：真 choices（不桩）+ 桩住 job 走完 选择→进度→结果
  // 真渲染是分钟级、依赖 ffmpeg，属 Plan E 真机验收；这里钉的是**请求形状**
  // 与三个视图分支（进度文案取服务端 msg、结果清单、url 为 null 的降级）。
  await page.locator("#app .topbar button").filter({ hasText: "🎬 横屏集锦" }).click();
  await dlg("gendlg").waitFor();
  eq(await dtitle("gendlg"), "🎬 生成横屏集锦（直切）", "横屏标题逐字");
  ok((await dlg("gendlg").locator(".el-alert__title").innerText())
     .includes("按勾选时刻直接切片拼装"), "横屏说明逐字");
  const gbox = dlg("gendlg").locator(".gpick .el-checkbox");
  await gbox.first().waitFor();
  // ⚠ 是 **5 不是 4**（Task 9 实现者实测订正，控制者已核）：夹具只种 3 个标签
  // （`test_edit_bay_e2e.py:66` 的 `["三分","上篮","罚球"]`），但**本 spec 自己**
  // 在前面（Task 6/7 的「标签列 allow-create」段，`spec:116-138`）往全局标签库添了
  // `reviewtag9`，而**后端没有删标签端点**（`spec:148` 已注明「清标签落回服务端、
  // 标签库里这条保留」）⇒ 走到这里 `state.tags` 已经是 4 个 ⇒ choices = [合集] + 4 = 5。
  // 写 4 等于假设「本 spec 自己没添过标签」，是一条**必然假红**的期望值。
  eq(await gbox.count(), 5, "合集 + 标签库全部（服务端 choices）");
  ok((await dlg("gendlg").innerText()).includes("🎬 横屏直切 ｜ 📊"), "stats 行来自服务端");
  ok(await gbox.first().locator("input").isChecked(), "默认勾合集");
  ok(!(await gbox.nth(1).locator("input").isChecked()), "默认不勾标签");
  await dlg("gendlg").locator(".el-dialog__footer button").filter({ hasText: "关闭" }).click();
  await dlg("gendlg").waitFor({ state: "hidden" });

  await page.locator("#app .topbar button").filter({ hasText: "📱 竖屏集锦" }).click();
  await dlg("gendlg").waitFor();
  eq(await dtitle("gendlg"), "📱 生成竖屏集锦（跟球运镜）", "竖屏标题逐字");
  ok((await dlg("gendlg").locator(".el-alert__title").innerText()).includes("片头钩子"),
     "竖屏说明逐字（含片头钩子与只勾 1 球的例外）");
  ok((await dlg("gendlg").innerText()).includes("📱 竖屏运镜 ｜ 📊"), "竖屏 stats 行");

  let genBody = null, genPoll = 0;
  await page.route("**/api/gen", async (route) => {
    genBody = route.request().postDataJSON();
    await route.fulfill({ status: 200, contentType: "application/json",
                          body: JSON.stringify({ jobId: "e2e" }) });
  });
  await page.route("**/api/jobs/e2e", async (route) => {
    genPoll += 1;
    const running = genPoll <= 3;      // 跑够 3 轮（~2.4s）让进度视图断言得稳
    await route.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify(running
        ? { id: "e2e", kind: "gen", state: "running", pct: 40, found: null,
            msg: "⏳ 正在生成（1/1）：视频合集（全部勾选的进球）…",
            error: null, done: false }
        : { id: "e2e", kind: "gen", state: "done", pct: 100, found: null,
            msg: "✅ 完成", error: null, done: true }) });
  });
  await page.route("**/api/jobs/e2e/result", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify({
        id: "e2e", kind: "gen", vertical: true,
        summaryMd: "📊 9月19日进球统计：总进球 1，其他 1 个\n\n"
          + "✅ 已生成 1 个视频（预览位播放第一个，全部在 work/<视频名>/ 下）：\n"
          + "· 视频合集（全部勾选的进球） → highlight.mp4\n"
          + "· 上篮：没有勾选的进球（跳过）",
        videos: [
          { item: "视频合集（全部勾选的进球）", name: "highlight.mp4",
            url: "/media/work?path=x&v=1" },
          { item: "上篮", name: "highlight-上篮.mp4", url: null },
        ],
        firstUrl: "/media/work?path=x&v=1" }) });
  });

  await dlg("gendlg").locator(".el-dialog__footer button")
    .filter({ hasText: "开始生成" }).click();
  await waitFor(async () => (await dlg("gendlg").innerText()).includes("正在生成（1/1）"),
                "进度文案取服务端 job.msg（R10）");
  eq(genBody.vertical, true, "POST /api/gen 的 vertical 透传");
  eq(genBody.items.length, 1, "items 是数组（不是裸字符串——后端会按字符迭代）");
  eq(genBody.items[0], "视频合集（全部勾选的进球）",
     "合集用服务端的 GEN_ALL_LABEL 原串（不是 __all__）");
  ok(await page.evaluate(() => localStorage.getItem("bbh.runningJob")),
     "跑中把 jobId 记进 localStorage（刷新后能接上进度）");

  await waitFor(async () => (await dlg("gendlg").innerText()).includes("生成完成"),
                "终态出结果视图", 15000);
  const gtxt = await dlg("gendlg").innerText();
  ok(gtxt.includes("highlight.mp4"), "成品清单从 videos[] 渲染");
  ok(gtxt.includes("（跳过）"), "summaryMd 逐字呈现（跳过行只有那里有）");
  ok(gtxt.includes("文件已不在"), "url 为 null 的成品降级成灰字（不假装可播）");
  eq(await dlg("gendlg").locator('a[href^="/media/work"]').count(), 1,
     "只有真能播的那条给预览链接");
  ok(!(await page.evaluate(() => localStorage.getItem("bbh.runningJob"))),
     "终态清掉记忆（不再续接）");
  await page.unroute("**/api/gen");
  await page.unroute("**/api/jobs/e2e");
  await page.unroute("**/api/jobs/e2e/result");
  await dlg("gendlg").locator(".el-dialog__footer button").filter({ hasText: "关闭" }).click();
  await dlg("gendlg").waitFor({ state: "hidden" });
  eq(await page.locator(".el-dialog:visible").count(), 0, "四个弹窗全部关上（否则后面所有键失效）");
  ok(!errs.length, "弹窗段没打出 JS 异常");
```

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/e2e/test_edit_bay_e2e.py`
Expected: FAIL —— `帮助窗标题逐字` 超时（`.el-dialog.helpdlg` 根本不存在）

- [ ] **Step 2: 四个组件**

`frontend/src/edit/HelpDialog.vue`：

```vue
<script setup>
// 快捷键总表（`proto-edit-inventory.md` §5 逐行照抄）。纯静态文案 +
// 当前标签库提示——打开就是查表，没有任何数据依赖。
const props = defineProps({
  modelValue: Boolean,
  tags: { type: Array, default: () => [] },
});
const emit = defineEmits(["update:modelValue"]);

const ROWS = [
  ["↑", "上一球（自动滚动 + 播放器联动）"],
  ["↓", "下一球（自动滚动 + 播放器联动）"],
  ["空格", "勾选「进片」——确认进球，勾上后自动跳下一个未勾选行；不写调优反馈"],
  ["X", "❌ 误判反馈「未进球」（否决 + 取消进片，证据快照存 feedback）"],
  ["1~9", "打标签（自动勾选进片——打标=认定该人的进球）"],
  ["D", "🗑 删除此球（弹确认，非破坏可恢复）"],
  ["K", "播放/暂停（专业剪辑惯例）"],
  ["J / L", "快退/快进 2 秒（专业剪辑惯例，同 ← →）"],
  ["Ctrl + Z", "撤销上一步（勾选/反馈/标签/删除都可撤）"],
  ["?", "打开本帮助"],
];
</script>

<template>
  <el-dialog class="helpdlg" :model-value="modelValue" width="520"
             title="⌨ 键盘流复核 — 快捷键总表"
             @update:model-value="(v) => emit('update:modelValue', v)">
    <table class="helptbl">
      <tbody>
        <tr v-for="[k, txt] in ROWS" :key="k">
          <td><kbd>{{ k }}</kbd></td>
          <td>
            {{ txt }}
            <span v-if="k === '1~9'" class="dim">当前标签库：{{
              tags.slice(0, 9).map((t, i) => `${i + 1}=${t}`).join("  ") || "（空）" }}</span>
          </td>
        </tr>
      </tbody>
    </table>
    <div class="note">
      ✅/❌ 按钮只给误判的球做调优反馈（翻案/否决）；正常确认进球用空格即可，不产生反馈数据。
    </div>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">知道了</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.helptbl { width: 100%; border-collapse: collapse; font-size: 13px; color: #cfd3dc; }
.helptbl td { padding: 5px 6px; border-bottom: 1px solid #2c2c2c; vertical-align: top; }
.helptbl td:first-child { width: 110px; }
kbd { padding: 0 6px; font-size: 11px; font-family: Consolas, monospace;
      background: #2a2a2a; border: 1px solid #4a4a4a; border-radius: 4px;
      border-bottom-width: 2px; color: #e5eaf3; }
.note { margin-top: 10px; font-size: 12.5px; color: #8b8e95; }
.dim { color: #7c7f86; font-size: 12px; }
</style>
```

`frontend/src/edit/OffsetDialog.vue`：

```vue
<script setup>
// 时刻校准（R7；② 2026-09-19 接通后的真语义）：**会话级偏移**——不落盘、不改
// 检测结果，但**随 /api/gen 的请求体送服务端平移切点**（显示时刻/切片预览/
// 集锦切点三处同源；判据是 dispT/tsec 的调用点，不是名单——见交付码头注释）。
//
// ⚠ 历史（本计划原文是下述语义，② 已废除，别照旧文实现）：没有「确定」按钮，
// **关闭（取消 / 右上角 / Esc）一律复原**到打开时的值——模态弹窗必须先关才能
// 点「开始生成」，关闭又抹掉 offset ⇒ 校准管线**从界面不可达**（服务端平移其实
// 早已写好）。用户 2026-09-19 裁定：**关闭只是关闭**（×/Esc/遮罩/按钮四条路
// 一个语义，都不动 offset），复原入口只有「归零」，「取消」按钮因此删掉——
// 下面模板里的 `opened`/`@close`/「取消」都是**旧文残留**，交付码里已不存在
// （以 `frontend/src/edit/OffsetDialog.vue` 为准）。
import { ref, watch } from "vue";

const props = defineProps({
  modelValue: Boolean,
  offset: { type: Number, default: 0 },
});
const emit = defineEmits(["update:modelValue", "update:offset", "reload"]);

const draft = ref(props.offset);
const opened = ref(props.offset);       // 打开时的值：关闭时复原成它 ← ② 删（关闭不复原；连同 @close 与「取消」按钮，见头注历史段）

watch(() => props.modelValue, (v) => {
  if (!v) return;
  opened.value = props.offset;
  draft.value = props.offset;
});

// 非有限值一律**忽略这次提交、保留上一个有效值**（R-T9-1，与 Task 8 的
// `finiteDur` 同一裁定）。
//
// ⚠ 判据必须**显式挡空串与 null**：`Number("")` 与 `Number(null)` 都是 **0**，
// `Number.isFinite(Number(v))` 会**假过**、把「清空」当成「设成 0」。用户清空
// 输入框的意思是「我不要这个值了」，不是「归零」。
//
// ⚠⚠ 这个输入框**不能**写成 `v-model="draft"`——光加门挡不住：清空时
// `update:modelValue` 那条路径（**不是** `@change`）照样会把 `draft` 置成
// `null`，接着 el-input-number 自己那个 `watch(() => props.modelValue)` 把
// `data.currentValue` 也置成 `null` ⇒ **输入框变空、页面上时刻却还是旧偏移**
// （两处显示不一致，比直接归零更难查）。写成 `:model-value="draft"` 则 `draft`
// 只由下面这个门来改，而 `handleInputChange` 末尾那句
// `setCurrentValueToModelValue()` 会把显示拉回 `draft`（仍是门保住的旧值）。
const setDraft = (v) => {
  if (v === "" || v === null || v === undefined) return;
  const n = Number(v);
  if (!Number.isFinite(n)) return;
  draft.value = n;
  emit("update:offset", n);
};
</script>

<template>
  <el-dialog class="offdlg" :model-value="modelValue" width="460"
             title="⏱ 进球时刻校准"
             @update:model-value="(v) => emit('update:modelValue', v)"
             @close="emit('update:offset', opened)">
    <div class="tip">
      检测比实际进球早就填正数（时刻整体后移），晚了填负数。影响显示时刻、切片预览
      与集锦切点（随 /api/gen 送服务端）——存储仍按原始检测时刻，校准不丢数据；
      关闭弹窗不会清掉校准（复原用「归零」）。
    </div>
    <div class="line">
      <el-input-number :model-value="draft" :step="0.1" :precision="1"
                       style="width: 140px" @change="setDraft" />
      <span class="unit">秒</span>
    </div>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button><!-- ② 删：「归零」替代（关闭不复原） -->
      <el-button @click="emit('reload')">↻ 重新载入</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.tip { font-size: 13px; line-height: 1.7; color: #cfd3dc; }
.line { display: flex; align-items: center; gap: 8px; margin-top: 14px; }
.unit { color: #a3a6ad; font-size: 12.5px; }
</style>
```

`frontend/src/edit/BulkDeleteDialog.vue`：

```vue
<script setup>
// 批量删除（proto §7）：**弹窗本身即确认**，不再套一层 ElMessageBox——
// 删除是非破坏的（只记 deleted.json 清单，「↩ 恢复已删」随时找回），而这一屏
// 已经把每条球的时刻｜档位｜标签摆出来了，看得见就不必再问一遍。
import { ref, watch } from "vue";
import { dispT } from "../shared/time.js";
import { tierText } from "./tiers.js";

const props = defineProps({
  modelValue: Boolean,
  rows: { type: Array, default: () => [] },
  offset: { type: Number, default: 0 },
});
const emit = defineEmits(["update:modelValue", "confirm"]);

const pick = ref([]);
watch(() => props.modelValue, (v) => { if (v) pick.value = []; });  // 每次打开重开白纸

const label = (row) => `${dispT(row, props.offset)}｜${tierText(row)}`
  + (row.tag ? `｜${row.tag}` : "");
const ok = () => {
  emit("confirm", [...pick.value]);
  emit("update:modelValue", false);
};
</script>

<template>
  <el-dialog class="bulkdlg" :model-value="modelValue" width="480"
             title="🗑 批量删除"
             @update:model-value="(v) => emit('update:modelValue', v)">
    <div class="tip">
      勾选要删的进球（随意投篮等非正赛球）——只记清单不动原数据，「↩ 恢复已删」
      随时一键找回；勾选的「进片/标签/归人」随行清理。
    </div>
    <el-checkbox-group v-model="pick" class="bulklist">
      <el-checkbox v-for="r in rows" :key="r.key" :value="r.key">{{ label(r) }}</el-checkbox>
    </el-checkbox-group>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="danger" :disabled="!pick.length" @click="ok">
        🗑 确认删除所选（{{ pick.length }}）
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.tip { font-size: 13px; line-height: 1.7; color: #cfd3dc; }
.bulklist { display: flex; flex-direction: column; gap: 4px; margin-top: 12px;
            max-height: 46vh; overflow-y: auto; }
</style>
```

（`el-checkbox` 在 element-plus 2.8 里 `value` 是取值、显示文本走默认插槽；用 `label` 当取值是 2.6 之前的旧写法。）

`frontend/src/edit/GenDialog.vue`：

```vue
<script setup>
// 生成集锦弹窗（proto §7）：选择 → 进度 → 结果，一窗三态。
//
// 选择清单来自 /api/gen/choices（服务端拼的「视频合集（全部勾选的进球）」+
// 标签库）；**job 不归它管**——长任务由 EditBay 起与跟随（Task 10 的检测/回扫
// 共用同一套），这里只是 job 的视图，所以关掉弹窗再打开，进度还在。
//
// 进度文案只显示服务端 job.msg（R10）：原型那句固定「逐球渲染 + 拼装 + 响度
// 归一」不写——竖屏管线还多跟球运镜/慢放/片头钩子，写死会与实际步骤不符。
import { computed, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { api, qs } from "../shared/api.js";

const props = defineProps({
  modelValue: Boolean,
  vertical: Boolean,
  job: { type: Object,
         default: () => ({ phase: "idle", msg: "", pct: 0, result: null }) },
});
const emit = defineEmits(["update:modelValue", "start", "reset"]);

const choices = ref([]);
const pick = ref([]);
const stats = ref("");

const VERT_TIP = "竖屏：逐球跟球运镜 ＋ 出手慢放 0.4×（锚出手时刻，进球回常速）"
  + "＋ 片头钩子（末球进球砍头前置，前 3 秒先看到球；只勾 1 球时不加——同一个球连看两遍像 bug）"
  + "＋ 爆点前置（高置信排前、同级保时间序），适合手机发布";
const HORI_TIP = "横屏：按勾选时刻直接切片拼装（顺序原样、字节不动），导出最快";

const tip = computed(() => (props.vertical ? VERT_TIP : HORI_TIP));
const title = computed(() => (props.vertical
  ? "📱 生成竖屏集锦（跟球运镜）" : "🎬 生成横屏集锦（直切）"));
const running = computed(() => props.job.phase === "running");
const settled = computed(() => props.job.phase === "done" || props.job.phase === "error");
const GEN_ALL = "视频合集（全部勾选的进球）";

// 每次打开重新拉清单：默认值是服务端的「只勾合集」，所以重开不记住上次的选择
// （弹窗是模态的，开着的时候勾选/标签不可能变，不需要中途重拉）。
watch(() => props.modelValue, async (v) => {
  if (!v || props.job.phase !== "idle") return;
  try {
    const d = await api.get("/api/gen/choices" + qs({ vertical: props.vertical }));
    choices.value = d.choices || [];
    pick.value = d.default || [];
    stats.value = d.stats || "";
  } catch (e) {
    ElMessage.error(e.message);     // 400「尚无检测结果——先点「▶ 开始检测」」等
  }
});
</script>

<template>
  <el-dialog class="gendlg" :model-value="modelValue" width="560" :title="title"
             :close-on-click-modal="false"
             @update:model-value="(v) => emit('update:modelValue', v)">
    <el-alert type="info" :closable="false" show-icon :title="tip" />

    <!-- 跑中：服务端的 msg + pct -->
    <div v-if="running" class="gprog">
      <el-progress :percentage="job.pct" :stroke-width="14" />
      <div class="gmsg">{{ job.msg }}</div>
    </div>

    <!-- 终态：错误 / 结果清单（videos[].url 可能为 null：文件被删或名字对不上）。
         失败原因由 EditBay 统一 toast（R15），这里不重复正文。 -->
    <template v-else-if="settled">
      <el-result :icon="job.phase === 'error' ? 'error'
                        : (job.result.videos.length ? 'success' : 'warning')"
                 :title="job.phase === 'error' ? '生成失败'
                        : (job.result.videos.length ? '生成完成' : '没有生成任何视频')" />
      <div v-if="job.result" class="glist">
        <div v-for="(v, i) in job.result.videos" :key="i" class="genfile">
          <span class="gitem">{{ v.item }} → {{ v.name }}</span>
          <a v-if="v.url" :href="v.url" target="_blank" class="gprev">▶ 在线预览</a>
          <span v-else class="gmiss">（文件已不在）</span>
        </div>
        <!-- 统计行 + 跳过行只在 summaryMd 里，逐字显示；不重解析（那是
             _gen_result 的活，前端再解析一遍就成了第二份格式契约） -->
        <pre class="glog">{{ job.result.summaryMd }}</pre>
      </div>
    </template>

    <!-- 选择态 -->
    <template v-else>
      <div class="gstats">{{ stats }}</div>
      <div class="gq">要生成哪些视频？</div>
      <el-checkbox-group v-model="pick" class="gpick">
        <el-checkbox v-for="c in choices" :key="c" :value="c">
          {{ c === GEN_ALL ? `📦 ${c}` : c }}
        </el-checkbox>
      </el-checkbox-group>
    </template>

    <template #footer>
      <el-button @click="emit('update:modelValue', false)">关闭</el-button>
      <el-button v-if="settled" @click="emit('reset')">↻ 重新选择</el-button>
      <el-button v-else type="primary" :loading="running"
                 :disabled="!pick.length || running"
                 @click="emit('start', { vertical, items: [...pick] })">
        🎬 开始生成
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.gprog { margin-top: 16px; }
.gmsg { margin-top: 8px; font-size: 13px; color: #cfd3dc; }
.gstats { margin-top: 14px; padding: 8px 10px; background: #222; border-radius: 8px;
          font-size: 12.5px; color: #cfd3dc; }
.gq { margin-top: 14px; font-size: 13.5px; color: #e5eaf3; }
.gpick { display: flex; flex-direction: column; gap: 4px; margin-top: 8px; }
.glist { margin-top: 8px; }
.genfile { display: flex; align-items: center; gap: 10px; padding: 5px 8px;
           border-bottom: 1px dashed #3a3a3a; font-size: 13.5px; }
.gitem { flex: 1; color: #e5eaf3; }
.gprev { color: #f48553; font-size: 12.5px; }
.gmiss { color: #7f838b; font-size: 12px; }
.glog { margin-top: 10px; padding: 8px; background: #1d1d1d; border-radius: 6px;
        white-space: pre-wrap; font-family: Consolas, monospace;
        font-size: 12px; color: #9aa0aa; }
</style>
```

（`↻ 重新选择` 是**新文案**——原型里结果态还留着「📂 打开所在文件夹」，那按钮在浏览器里没有实现基础，见 Step 3 末的说明。）

- [ ] **Step 3: EditBay 接线**

**(a) import 补三行**：

```js
import { createJobMemory } from "../shared/jobmemory.js";
import { startJob, followJob } from "../shared/jobs.js";
import HelpDialog from "./HelpDialog.vue";
import OffsetDialog from "./OffsetDialog.vue";
import BulkDeleteDialog from "./BulkDeleteDialog.vue";
import GenDialog from "./GenDialog.vue";
```

**(b) 弹窗与长任务（放在 Task 8 的 `applyAll` 之后）**：

```js
// —— 四个弹窗（Task 9）——
// 弹窗状态在 Task 7 已定型（dlg + modalOpen 闸门），这里只塞组件与开合入口。
const genVertical = ref(true);
const genJob = reactive({ phase: "idle", msg: "", pct: 0, result: null });
const jobMem = createJobMemory();
// 键 = 后端的 kind（jobmemory 记的就是它，续接时按它找槽位）
const JOBS = { gen: genJob };            // Task 10 往这里加 detect / wall_scan

// 长任务统一驱动（三个任务共用）。记住 jobId 是给「刷新页面接着看进度」用的：
// 后端 job 只在内存里，重开页面用 followJob 接上，而不是重新起一个（同类
// 任务重起会 409）。
async function longJob(kind, startUrl, body, slot) {
  slot.phase = "running";
  slot.msg = "⏳ 正在启动…"; slot.pct = 0; slot.result = null;
  try {
    const jobId = await startJob(startUrl, body);
    // 连视频一起记：续接时要确认这个 job 是在**当前这个视频**上跑的
    jobMem.remember({ jobId, kind, videoPath: state.value?.videoPath || "" });
    const res = await followJob(jobId, (j) => {
      slot.msg = j.msg || ""; slot.pct = Number(j.pct) || 0;
    });
    slot.result = res; slot.phase = "done";
    return res;
  } catch (e) {
    // 失败一律弹服务端文案（R15）：检测/回扫没有弹窗，toast 是唯一出路；
    // 生成弹窗的错误态因此不重复显示正文（同一句话弹两处反而像两个错）。
    slot.phase = "error"; slot.msg = e.message;
    ElMessage.error(e.message);
    return null;
  } finally {
    jobMem.clear();                      // 终态（含失败）就不再续接
  }
}

// 刷新后续接（Task 10 的检测也走这里；本任务只有 gen 会命中）。
// 视频对不上就不接（R3）：job 是按视频跑的，接了别人的进度等于把锁挂错地方。
async function resumeJobs() {
  const rec = jobMem.load();
  if (!rec) return;
  const slot = JOBS[rec.kind];
  const cur = state.value?.videoPath;
  if (!slot || (rec.videoPath && cur && rec.videoPath !== cur)) {
    jobMem.clear(); return;
  }
  slot.phase = "running"; slot.msg = "⏳ 接上进度…"; slot.pct = 0;
  try {
    const res = await followJob(rec.jobId, (j) => {
      slot.msg = j.msg || ""; slot.pct = Number(j.pct) || 0;
    });
    slot.result = res; slot.phase = "done";
  } catch (e) {
    // 404 = 服务重启过（job 只在内存里），文案是服务端给的
    slot.phase = "error"; slot.msg = e.message;
    ElMessage.error(e.message);
  } finally {
    jobMem.clear();
  }
}

const openGen = (vertical) => { genVertical.value = vertical; dlg.gen = true; };
const startGen = ({ vertical, items }) =>
  longJob("gen", "/api/gen", { vertical, items }, genJob);
const bulkDelete = (keys) => write(act.deleteKeys(keys));
const reloadState = () => store.refresh().catch((e) => ElMessage.error(e.message));
```

**(c) 闸②③修正** —— 把 Task 7 那一行 `unbindHotkeys = bindHotkeys(keyMap, { disabled: modalOpen });` 换成：

```js
// 闸②/闸③（proto §5）：别的弹窗开着 → 所有键整体失效；**帮助窗**开着时只有
// `?` 还能用（其余键各自 early-return）——所以不能把帮助也塞进 disabled，
// 那样 `?` 会连自己都关不掉。`?` 本身是开关。
keyMap["?"] = () => { dlg.help = !dlg.help; };
const gated = {};
for (const [k, fn] of Object.entries(keyMap)) {
  gated[k] = k === "?" ? fn : (e) => { if (dlg.help) return; fn(e); };
}
unbindHotkeys = bindHotkeys(gated, { disabled: () => modalOpen() && !dlg.help });
```

**(d) `onMounted` 末尾补一行**（在 `store.refresh()` 之后）：

```js
  resumeJobs();     // 刷新后接着跟随在跑的长任务（没有记录就是空转）
```

**(e) 顶栏** —— 把 Task 5 那段 `<el-input …>` 之后到 `</div>` 之间补上（Task 10 会在 `🎬 横屏集锦` **之前**插入「▶ 开始检测」与「🧍 识别出手人」）：

```vue
      <el-button-group>
        <el-button title="横屏：按勾选时刻直接切片拼装（顺序原样、字节不动），导出最快"
                   @click="openGen(false)">🎬 横屏集锦</el-button>
        <el-button title="竖屏：逐球跟球运镜 + 出手慢放 0.4× + 片头钩子，适合手机发布"
                   @click="openGen(true)">📱 竖屏集锦</el-button>
      </el-button-group>
      <span class="dim">已勾选 {{ checkedPct }}%</span>
      <span class="spacer" />
      <el-button text size="small" title="快捷键帮助（按 ? 呼出）"
                 @click="dlg.help = true">⌨ 帮助</el-button>
```

`<style scoped>` 补两条：

```css
.spacer { flex: 1; }
.dim { color: #8b8e95; font-size: 12.5px; }
```

**(e2) 路径输入框：成功后回填服务端权威路径**（Task 5 复审 F4 的落点）。Task 5 的 `onPathChange` 拿**用户原文**与 `state.videoPath` 比相等，而服务端 `clean_path`（`app.py:50-56`）会去掉首尾空白与**成对引号**（资源管理器「复制文件地址」粘出来就带引号）——两边不一致时，用户再按一次回车会对同一段路径重复 `POST /api/session`（重跑 `probe_existing`、再写一次 `ui_prefs.json`）。让输入框以服务端回包为准即可，**不要在 JS 里再写一份清洗逻辑**（那是服务端语义的复制品，迟早分叉）：

```js
async function onPathChange() {
  const p = pathInput.value.trim();
  if (!p || p === state.value?.videoPath) return;
  try {
    await act.openVideo(p);
    // 回填服务端清洗后的路径（store.set 同步过监听器，故 state 已是新回包）：
    // 输入框显示的就是真载入的那个路径，下次回车比较的是「清洗后 vs 清洗后」，
    // 不会因为原文多了引号/空白而重复发请求。
    pathInput.value = state.value?.videoPath || p;
  } catch (e) { ElMessage.error(e.message); }
}
```

**(f) 模板末尾**（`.main` 之后、`.editbay` 之内）挂四个弹窗：

```vue
    <HelpDialog v-model="dlg.help" :tags="tags" />
    <OffsetDialog v-model="dlg.offset" v-model:offset="offset" @reload="reloadState" />
    <BulkDeleteDialog v-model="dlg.bulk" :rows="rows" :offset="offset"
                      @confirm="bulkDelete" />
    <GenDialog v-model="dlg.gen" :vertical="genVertical" :job="genJob"
               @start="startGen" @reset="genJob.phase = 'idle'" />
```

**(g) 工具条 `⋯` 补两项** —— 在 `↩ 恢复已删（{{ deletedCount }}）` 那一项的**前面**插入（顺序照 proto §7：时刻校准 → 恢复已删 → 批量删除）：

```vue
                <el-dropdown-item @click="dlg.offset = true">⏱ 时刻校准</el-dropdown-item>
```

后面再补一项：

```vue
                <el-dropdown-item @click="dlg.bulk = true">🗑 批量删除…</el-dropdown-item>
```

**（不实现原型 footer 的「📂 打开所在文件夹」）**：浏览器打不开本机资源管理器，
后端也没有这种端点——为它加一个能在服务端执行本机命令的接口是纯安全面，不值当。
结果态该有的信息都在：成品清单（可点预览）+ `summaryMd`（含「全部在
`work/<视频名>/` 下」的路径说明 + 跳过原因）。这条按 **R20**（原型 mock 机制不落地）处理。

- [ ] **Step 4: 跑 e2e 确认通过**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/e2e/test_edit_bay_e2e.py`
Expected: PASS

- [ ] **Step 5: 构建 + 冒烟 + 产物 pytest**

配方 **A**、**C**、**D**。Expected: 全 PASS

- [ ] **Step 6: 提交**

```bash
git add frontend/src/edit/HelpDialog.vue frontend/src/edit/OffsetDialog.vue \
        frontend/src/edit/BulkDeleteDialog.vue frontend/src/edit/GenDialog.vue \
        frontend/src/edit/EditBay.vue tests/e2e/edit_bay.spec.mjs bbh/static
git commit -m "feat(frontend): 四个弹窗（帮助/时刻校准/批量删除/生成）+ 顶栏收尾

帮助窗逐行照原型键盘表；闸②③按原型分开实现：别的弹窗开着 → 所有键失效，
帮助窗开着时只有 ? 还能用（所以不能把帮助也塞进 disabled——那样 ? 关不掉自己）。

时刻校准是纯前端显示偏移（R7）：改数字即预览，关闭一律复原到打开时的值——
写着「取消」就得真取消，不能留一个改了一半的偏移在页面上。表格列/时间轴/
切片 t 三处读同一个 offset，一起变。

批量删除弹窗本身即确认（删除非破坏、可恢复，而这一屏已经把每条球的时刻｜
档位｜标签摆出来了）。

生成弹窗三态一窗（选择/进度/结果），但 **job 不归它管**：长任务由 EditBay 起
与跟随，弹窗只是 job 的视图 ⇒ 关掉再打开进度还在，Task 10 的检测/回扫直接
复用同一个 longJob。进度文案只显示服务端 job.msg（R10：原型那句固定的
「逐球渲染+拼装+响度归一」对竖屏管线是错的）。结果清单从 videos[] 渲染
（url 为 null 降级成灰字），summaryMd 逐字显示——统计行与跳过行只在它里面，
前端重解析就成了第二份格式契约。合集项用服务端的 GEN_ALL_LABEL 原串，
items 传数组（后端按字符迭代裸字符串会产出垃圾项）。

不实现原型的「📂 打开所在文件夹」：浏览器打不开本机资源管理器，为它加一个
能在服务端执行本机命令的端点不值当（R20）。"
```

---

### Task 10: 三个长任务的按钮与状态带 + 检测期锁表（R3/R10/R15/R31）

**Files:**
- Modify: `frontend/src/edit/EditBay.vue`
- Modify: `tests/e2e/edit_bay.spec.mjs`（追加「长任务」段）
- Modify: `bbh/static/**`（配方 A 的产物）

**Interfaces:**
- Consumes: Task 9 的 `longJob`/`JOBS`/`jobMem`/`resumeJobs`/`genJob`、Task 6 的 `write(p)`/`onState(s)`
- Produces:
  - `detectJob` / `wallJob`（与 `genJob` 同形状）、`JOBS` 三槽（键 = 后端 kind：`gen`/`detect`/`wall_scan`）
  - `anyJobRunning`、`detecting`、`jobMsg` 三个 computed；`startDetect()` / `startScan()`
  - 顶栏「▶ 开始检测」「🧍 识别出手人」；顶栏下一条状态带 `.jobmsg`
  - `write()` 头部的检测期闸 + `.main.locked`（指针事件整块关掉）
- **进度文案的落点（R31）**：`job.msg` 逐帧带进度（「⏳ 检测中… 72/180 帧 (40%)」），塞进按钮会和按钮自身的百分比重复 ⇒ 文案放**状态带**（旧界面也是独立一行状态），按钮只显示 `⏳ 检测中 {pct}%` + `:loading`。原型那句「检测中 `{pct}%` · `{found} 条`」里的 `found` 恒 `null`（R10）⇒ 不用它。
- **刷新不取消检测**：`_cancel_evt` 只由 Gradio 的 `demo.unload`（`app.py:583 _on_page_unload`）置位，新页面没有这条钩子 ⇒ 关页面**不会**取消，`job` 继续在服务端跑，重开页面靠 `jobMem` 接上。所以旧文案「刷新/关闭页面会取消检测」**不能抄**——照抄就是错话。

- [ ] **Step 1: 往 e2e 追加「长任务」段（先红）**

在 `if (errs.length) throw …` 之前（Task 9 那段之后）追加：

```js
  // —— 三个长任务 + 检测期锁表（Task 10）——
  // 真检测是分钟级且会重写夹具的 events.json ⇒ 桩住 /api/detect 与 job 端点，
  // 只验 UI 契约（按钮态 / 状态带 / 锁表 / 记忆 / 终态整份替换）。真跑留给 Plan E。
  const snap = await state();
  const locked = () => page.locator("#app .main.locked").count();
  const btn = (t) => page.locator("#app .topbar button").filter({ hasText: t }).first();

  let detBody = "unset", detPoll = 0;
  await page.route("**/api/detect", async (route) => {
    detBody = route.request().postData();
    await route.fulfill({ status: 200, contentType: "application/json",
                          body: JSON.stringify({ jobId: "det1" }) });
  });
  await page.route("**/api/jobs/det1", async (route) => {
    detPoll += 1;
    const running = detPoll <= 3;
    await route.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify(running
        ? { id: "det1", kind: "detect", state: "running", pct: 40, found: null,
            msg: "⏳ 检测中… 72/180 帧 (40%)", error: null, done: false }
        : { id: "det1", kind: "detect", state: "done", pct: 100, found: null,
            msg: "✅ 完成：🟢 高置信 12 个", error: null, done: true }) });
  });
  await page.route("**/api/jobs/det1/result", async (route) => {
    // 结果 = **平铺的 state_payload**（契约 §1）：改一处勾选，验「整份替换」
    const rowsNext = snap.rows.map((r, i) => (i === 0 ? { ...r, selection: true } : r));
    await route.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify({ ...snap, id: "det1", kind: "detect", rows: rowsNext,
                             msg: "✅ 完成：🟢 高置信 12 个" }) });
  });

  await btn("开始检测").click();
  // 先 count() 再 innerText()：元素还没渲染出来时 innerText() 是**抛异常**的
  // （waitFor 里的异常直接冒出去，不会继续轮询）——count() 不抛，能等到它出现
  await waitFor(async () => {
    const b = page.locator("#app .topbar button").filter({ hasText: "检测中" });
    return (await b.count()) > 0 && (await b.first().innerText()).includes("40%");
  }, "检测按钮显示进度");
  eq(detBody, null, "POST /api/detect 没有 body（后端不吃参数）");
  ok((await page.locator("#app .jobmsg").innerText()).includes("72/180 帧"),
     "状态带显示服务端的 msg（R31）");
  eq(await locked(), 1, "检测中整块锁上（R3）");
  ok(await btn("🎬 横屏集锦").isDisabled(), "检测中生成入口也禁用");
  ok(await btn("🧍 识别出手人").isDisabled(), "检测中回扫入口也禁用");
  const mem = await page.evaluate(() => localStorage.getItem("bbh.runningJob"));
  ok(mem && JSON.parse(mem).kind === "detect", "记忆里记的是 detect");
  eq(JSON.parse(mem).videoPath, snap.videoPath, "连视频一起记（R3：视频不一致就不续接）");

  // 键盘路径被 write() 闸住（鼠标路径被 .locked 的 pointer-events 拦掉）
  await page.keyboard.press("Space");
  await page.waitForTimeout(300);
  ok(await toast("检测中——表格已锁"), "键盘写操作被拦并给出原因");
  ok((await page.locator("#app .tblfoot").innerText()).includes("勾选 0 条进集锦"),
     "被拦下的写操作不留本地假状态");

  // 同理：跑完前「开始检测」这颗按钮根本不存在，直接 innerText() 会抛
  await waitFor(async () => {
    const b = page.locator("#app .topbar button").filter({ hasText: "检测中" });
    return (await b.count()) === 0
        && (await btn("开始检测").count()) > 0
        && (await btn("开始检测").first().innerText()).includes("开始检测");
  }, "终态按钮复位", 15000);
  ok(await toast("🟢 高置信 12 个"), "完成提示用服务端 msg");
  eq(await locked(), 0, "跑完自动解锁");
  ok(!(await page.evaluate(() => localStorage.getItem("bbh.runningJob"))), "终态清记忆");
  await waitFor(async () => (await page.locator("#app .tblfoot").innerText())
                .includes("勾选 1 条进集锦"),
                "检测结果整份替换 state（页脚跟着桩数据变）");
  await page.unroute("**/api/jobs/det1");
  await page.unroute("**/api/jobs/det1/result");

  // 借「↻ 重新载入」把 state 拉回服务端真值（顺带验这条路）
  await menu("⏱ 时刻校准");
  await dlg("offdlg").locator(".el-dialog__footer button")
    .filter({ hasText: "重新载入" }).click();
  await dlg("offdlg").locator(".el-dialog__footer button")
    .filter({ hasText: "取消" }).click();
  await dlg("offdlg").waitFor({ state: "hidden" });
  await waitFor(async () => (await page.locator("#app .tblfoot").innerText())
                .includes("勾选 0 条进集锦"), "重新载入拉回服务端真值");

  // 失败路径：400/409 一律把服务端文案弹出来（R15），且不锁表
  await page.unroute("**/api/detect");
  await page.route("**/api/detect", (route) => route.fulfill({
    status: 409, contentType: "application/json",
    body: JSON.stringify({ error: "已有检测在跑" }) }));
  await btn("开始检测").click();
  ok(await toast("已有检测在跑"), "409 文案逐字弹出");
  eq(await locked(), 0, "失败不锁表");
  await page.unroute("**/api/detect");

  await page.route("**/api/wall/scan", (route) => route.fulfill({
    status: 400, contentType: "application/json",
    body: JSON.stringify({ error: "尚无检测结果——先点「▶ 开始检测」" }) }));
  await btn("识别出手人").click();
  ok(await toast("尚无检测结果——先点"), "回扫守卫文案逐字弹出");
  await page.unroute("**/api/wall/scan");
  ok(!errs.length, "长任务段没打出 JS 异常");
```

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/e2e/test_edit_bay_e2e.py`
Expected: FAIL —— 「检测按钮显示进度」超时（顶栏还没有这颗按钮）

- [ ] **Step 2: `write()` 改成收**「动作」**而不是收**「已经发出去的请求」**，再加检测期闸**

> ⚠ **本节原稿是错的，而且错得很隐蔽，照抄会卡死在 Step 4。** 原稿说「`write(p)` 是所有写操作的唯一入口，闸设在这里就不会漏」——**它漏定了**。
> `write` 收的 `p` 是**已经创建好的 promise**：写操作的真实发起点是 `actions.js` 的 `raw.*`（`raw.selection = (key, selection) => api.patch(…)`，`actions.js:42-52`），而 `act.setSelection(key, on)` 里那句 `once(raw.selection(key, selection), …)`（`actions.js` 的 `once`，`:23`）是**先把 `raw.selection(...)` 求值**才传给 `once` 的 ⇒ **进 `write` 之前 HTTP 请求就已经在飞了**。
> 于是原稿的门只会：请求照发、服务端照写、`once`/`replay` 里的 `store.set(data)` 照换 state、撤销栈照入栈——**同时给用户弹一句「检测中——表格已锁」**。既没锁住（R3 想要的正是别让按旧键的写落盘），又是**一句假话**。
> 而且本任务 Step 1 里那条 `"被拦下的写操作不留本地假状态"`（页脚仍是「勾选 0 条进集锦」）**在错实现下必红**——服务端真被写了，回包换掉 state，页脚会变成「勾选 1 条」。 ⇒ **Step 4 永远到不了 PASS**，实现者会以为是断言的问题。

**改法：`write` 收一个 thunk，门在**调用它之前**。** 这样被拦下时动作**根本没被创建**，请求不发、state 不换、栈不入。

把 Task 6 的 `write` 换成：

```js
// 写操作的统一入口：收**动作**（thunk）而不是收已经发出去的 promise。
// ⚠ 必须收 thunk，别写回 `write(p)`：`act.*` 一被调用就立刻发请求
// （`actions.js` 的 `raw.*`），收 promise 的话门拦不住任何东西——请求已经在飞了。
async function write(make) {
  // 检测期锁写（R3）：这四条写端点在服务端没有 running 守卫，而检测完成会
  // 重写 events.json——跑着的时候按旧键落盘的勾选/标签，键相同的球会拿到
  // 别人的标记（静默错数据）。旧界面就是检测期间禁整张表，这里同口径。
  if (detecting.value) {
    ElMessage.warning("⏳ 检测中——表格已锁，跑完自动载入结果；"
      + "刷新/关闭页面不影响检测（重开会接上进度）");
    return null;
  }
  try {
    const s = await make();
    if (s && s.msg) ElMessage.success(s.msg);
    return s;
  } catch (e) {
    ElMessage.error(e.message);
    return null;
  }
}
```

**每一个调用点都要包成 thunk**（`write(act.x(…))` ⇒ `write(() => act.x(…))`）。逐个点名，漏一个就等于那个入口没锁（**并且**那个入口会在检测期弹假警告）：

| 位置 | 现在 | 改成 |
|---|---|---|
| `onMiss` | `write(act.recordMiss(missInput.value))` | `write(() => act.recordMiss(missInput.value))` |
| `confirmRow`（空格） | `write(act.setSelection(key, on))` | `write(() => act.setSelection(key, on))` |
| `markMiss`（X） | `write(act.markVerdict(key, "not_goal"))` | `write(() => act.markVerdict(key, "not_goal"))` |
| `tagByIndex`（1–9） | `write(act.setTag(row.key, tag))` | `write(() => act.setTag(row.key, tag))` |
| `deleteCurrent`（D） | `write(act.deleteKeys([row.key]))` | `write(() => act.deleteKeys([row.key]))` |
| 模板 ⋯ 恢复已删 | `@click="write(act.restoreAll())"` | `@click="write(() => act.restoreAll())"` |
| `EventTable @check` | `write(act.setSelection(k, v))` | `write(() => act.setSelection(k, v))` |
| `EventTable @tag` | `write(onTag(k, v))` | `write(() => onTag(k, v))` |
| `BasisPanel @verdict` | `write(act.markVerdict(curRow.key, k))` | `write(() => act.markVerdict(curRow.key, k))` |
| `BasisPanel @flag` | `write(act.toggleFlag(curRow.key, f))` | `write(() => act.toggleFlag(curRow.key, f))` |
| Task 9 的 `bulkDelete` | `write(act.deleteKeys(keys))` | `write(() => act.deleteKeys(keys))` |

`onTag`（Task 9 已在用）**本身不用改**：它内部是 `await act.addTag(tag)` + `return act.setTag(key, tag)`，收 thunk 之后它整条被门挡在外面——顺带把 `POST /api/tags` 也锁住了（收 promise 的老写法连它也锁不住）。

（`detecting` 声明在下面的 Step 3，比 `write` 晚——没关系：`write` 只在事件里被调用，那时 setup 早就跑完，不会踩 TDZ。别为此把它挪上去。）

改完顺手把 Step 1 那条断言换成**直接问服务端**（比读页脚强：页脚是 state 的函数，而这要的是「服务端一个字节都没写」）：

```js
  ok((await state()).rows.every((r) => !r.selection),
     "被拦下的写操作**服务端也没写**（不只是界面没动）");
```

- [ ] **Step 3: 长任务槽位、按钮、状态带、锁表**

**(a) `JOBS` 换成三槽** —— 把 Task 9 的 `const JOBS = { gen: genJob };` 换成：

```js
const JOBS = { gen: genJob, detect: detectJob, wall_scan: wallJob };
```

**(b) 在 Task 9 的 `jobMem` 之前补两个槽位**：

```js
const detectJob = reactive({ phase: "idle", msg: "", pct: 0, result: null });
const wallJob = reactive({ phase: "idle", msg: "", pct: 0, result: null });
```

**(c) 在 `reloadState` 之后补 computed 与两个启动器**：

```js
const anyJobRunning = computed(() =>
  Object.values(JOBS).some((j) => j.phase === "running"));
const detecting = computed(() => detectJob.phase === "running");
// 状态带只显示正在跑的那个：msg 逐帧带着进度，塞进按钮会与按钮自身的
// 百分比重复（R31）
const jobMsg = computed(() => {
  const j = Object.values(JOBS).find((s) => s.phase === "running");
  return j ? j.msg : "";
});

// detect / wall_scan 的结果是**平铺的 state_payload**（契约 §1：kind 属于
// ("detect","wall_scan") 时 payload.update(state_payload())），所以直接
// 整份喂给 onState —— 表格/时间轴/页脚/墙一起刷新。
const startDetect = async () => {
  const res = await longJob("detect", "/api/detect", undefined, detectJob);
  if (!res) return;
  onState(res);
  if (res.cancelled) ElMessage.warning(res.msg);
  else ElMessage.success(res.msg || "✅ 完成");
};
const startScan = async () => {
  const res = await longJob("wall_scan", "/api/wall/scan", undefined, wallJob);
  if (!res) return;
  onState(res);
  ElMessage.success(res.msg || "✅ 完成");
};
```

**(d) 顶栏** —— 在 Task 9 的 `<el-button-group>`（🎬/📱）**之前**插两颗：

```vue
      <el-button :type="detecting ? 'warning' : 'primary'"
                 :loading="detecting" :disabled="anyJobRunning"
                 :title="detecting
                   ? '检测进行中——跑完自动载入结果（刷新/关闭页面不影响它，重开自动接上进度）'
                   : '开始检测（几分钟；中途退出可续跑）'"
                 @click="startDetect">
        {{ detecting ? `⏳ 检测中 ${detectJob.pct}%` : "▶ 开始检测" }}
      </el-button>
      <el-button plain type="primary" :loading="wallJob.phase === 'running'"
                 :disabled="anyJobRunning"
                 title="逐球回扫出手人 + 生成缩略图动图（已缓存的球自动跳过、已删球不回扫）"
                 @click="startScan">
        🧍 识别出手人
      </el-button>
```

并把 Task 9 的两颗集锦按钮也加上 `:disabled="anyJobRunning"`，路径输入框加 `:disabled="detecting"`（检测中换视频会让锁挂错对象）——**一条规则：有长任务在跑，三颗按钮全灰**。

**(e) 状态带** —— `.topbar` 之后、`.main` 之前插一行：

```vue
    <div v-if="jobMsg" class="jobmsg">{{ jobMsg }}</div>
```

**(f) 锁表** —— Task 5 的 `<div class="main">` 换成：

```vue
    <div class="main" :class="{ locked: detecting }">
```

`<style scoped>` 补三条：

```css
.jobmsg { flex: none; padding: 6px 18px; background: #221a12;
          border-bottom: 1px solid #3a2a1a; color: #f0b37e; font-size: 12.5px; }
/* 检测期锁写（R3）：指针事件整块关掉 —— 让「锁」看得见，而不是点了没反应 */
.main.locked .left, .main.locked .right { pointer-events: none; opacity: .55; }
```

- [ ] **Step 4: 跑 e2e 确认通过**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/e2e/test_edit_bay_e2e.py`
Expected: PASS

- [ ] **Step 5: 构建 + 冒烟 + 产物 pytest**

配方 **A**、**C**、**D**。Expected: 全 PASS

- [ ] **Step 6: 提交**

```bash
git add frontend/src/edit/EditBay.vue tests/e2e/edit_bay.spec.mjs bbh/static
git commit -m "feat(frontend): 检测/回扫按钮 + 状态带 + 检测期锁表

三个长任务共用 Task 9 的 longJob：起 job → 记 jobId（带 videoPath）→ 轮询 →
取结果。detect/wall_scan 的结果是**平铺的 state_payload**，拿到就整份喂 onState，
表格/时间轴/页脚一起刷新。

进度文案放顶栏下的状态带（R31）：msg 逐帧带进度（「⏳ 检测中… 72/180 帧 (40%)」），
塞进按钮会和按钮自身的百分比重复。原型的 `{found} 条` 不用——found 恒 null（R10）。
文案只抄服务端的 msg：旧界面那句「刷新/关闭页面会取消检测」在新架构里是**错话**
（_cancel_evt 只由 Gradio 的 unload 钩子置位，新页面没有它），照抄会骗用户。

检测期锁表（R3）：后端四条写端点没有 running 守卫，而检测完成会重写 events.json
——跑着时按旧键落盘的勾选/标签，键相同的球会拿到别人的标记（静默错数据）。
闸设在 write() + .main.locked 指针事件整块关掉（鼠标路径看得见地锁上），口径与
旧界面「检测期间禁整张表」一致。

write() 改成收 thunk（`write(() => act.x(…))`）——这一改是承重的，不是风格：
`act.*` 一被调用就立刻发请求（actions.js 的 raw.*），收「已经发出去的 promise」
的话门拦不住任何东西（请求早已在飞），只会一边写进去一边弹「已锁」，是句假话。
全部 11 个调用点一并包成 thunk（含 Task 9 的 bulkDelete），漏一个那个入口就没锁。
顺带把 POST /api/tags 也锁住了：onTag 内部的动作现在整条在门后面。"
```

---

### Task 11: 收尾修正 + 全量 e2e + 构建 + 冒烟 + **唯一一次全量 pytest**

**Files:**
- Modify: `frontend/src/edit/EditBay.vue`（收尾修正 F1）
- Modify: `frontend/src/edit/HelpDialog.vue`（下面第 7 条的 1:1 偏差：`↑/↓` 合成一行）
- Modify: `tests/e2e/edit_bay.spec.mjs`（追加 F1 的断言 + 第 5/6/7 条的订正）
- Modify: `tests/e2e/test_edit_bay_e2e.py`（下面 F3′ 那条一行兜底）
- Modify: `bbh/static/**`（配方 A 的产物）

**Interfaces:**
- Consumes: 前十个任务的全部产物
- Produces: 全绿的 e2e spec + 入库的 `bbh/static` 产物 + 全量 Python 测试结论

**本任务不碰用户数据**：真机验收（09-11_3）是 **Plan E** 的事，这里的 e2e 一律跑在临时目录夹具上（配方 C2 已保证）。

⚠ **「顺手清一笔账」那两段的执行者不是本任务的实现者**（控制者派发时会明确划出去）：同步计划文本是**控制者的 docs 提交**（本计划的纪律：实现者不碰计划文件、不碰 ledger）。**实现者只做代码那部分**——就是下面 items（**不数条数**，数了就会漂）与 Step 1~5 里点名的**代码与测试**改动（含 `tests/e2e/test_edit_bay_e2e.py` 的 F3′ 那一行与夹具 docstring 那句订正）。**实现者不要编辑本计划文件的任何一行。**

**顺手清一笔账（控制者做，不派给实现者）——已清**：Task 6 节顶部那条 ⚠ 连同它列的三处落后代码块（`onState` 的 `nextTick` 重指向、`TimelineStrip` 的 `.tl-dot.<status>` 类与 CSS、`tests/e2e/` 两份文件的断言）已按仓库里的**权威代码**同步进计划正文、那条 ⚠ 已删；Task 7 节那块 spec 代码也补齐了交付版的「前置复位（摘残留勾选 + 整页重载换空栈）」「滚动循环前补的 `rowCell(0).click()` + 视口压到 1600×420」「段尾还原 1600×900」三处（`s.rows[7]` 那条订正更早已在 `5b8bf54` 同步过）。**两份 `tests/e2e/` 文件的代码块现在与仓库逐字一致**，逐块 `diff` 验过：`test_edit_bay_e2e.py` 全 195 行、`spec.mjs:1-147`、`spec.mjs:150-281`。只让计划不再撒谎，不改任何实现。

**还有一笔 park 下来的小账（Task 6 复审 F3′，我裁定沿用「不再开修复轮」时 park）**：`tests/e2e/test_edit_bay_e2e.py:129` 那条「临时服务 {timeout}s 没起来」的兜底**完全不端服务日志**，而同一个 `_wait_up` 的另外两条失败路径（子进程已退出、`/api/state` 返 4xx/5xx）都端了。**为什么当时 park**：它只在「进程活着但 40 秒内一次都不应答 `/api/state`」时触发，本仓从未观察到这个失败模式（实际观察到的是 500 那条，已在前三轮修透）；而第 3 轮之后我按纪律不再开新修复轮。**修法（一行）**：把 `_log_text()[-2000:]` 拼进那条 `AssertionError` 的信息里，与另外两条路径拉齐。**这条不是「可选美化」**：同一个函数的失败诊断，三条路径给出三种信息量，下一个撞上它的人只会看到「没起来」——正是本任务前三轮反复打的同一个形状（诊断看着到位、实际不递证据）。收口时一并做掉。

**同一趟顺手订正一句夹具 docstring**：`tests/e2e/test_edit_bay_e2e.py:39-40` 那句「行数够多，表格才会真的出现滚动条——「↑↓ 复核时当前行滚进视野」这条才有得验」**有一半是假的**，Task 7 段的实测已经推翻它：1600×900 下表格可视区 662px、表体 492px ⇒ 12 行全在带内、**根本没有滚动条**，是段里把视口压到 1600×420 之后那条断言才有牙（见 `spec.mjs:260-262` 的 ⚠ 与 `:264` 的 `setViewportSize`，两块都在本计划 Task 7 段里，与仓库逐字一致）。**修法**：把那半句改成「行数够多**且段里把视口压矮**，那条断言才有牙」，别让下一个人照着 docstring 推理、得出「12 行足够」的错结论。

**再一笔 park 下来的账（Task 8 修复轮 1 的复审遗留，我裁定收到这里而不是再开一轮）**：

1. **`applyAll` 那道 `finiteDur` 门没有自己的牙** —— 已交付的 e2e 里**没有一条**只钉「坏值不扩散到全部行」的断言。修复轮的反事实是**两个门一起摘**（那是当时要求的验证形状），所以 `applyAll` 那一半是**读代码判定**的，没有实测。而 F-1 裁定的**核心危害就是这个扩散路径**——「会话级前后秒经『应用到全部』作用于所有行，静默跳默认会毁掉用户特意设过的值」。

   **修法（下面这段照抄进 `tests/e2e/edit_bay.spec.mjs` 的 Task 8 段**，紧跟在 `ok(await toast("已应用到全部：前 6s"), "应用到全部有提示");` 那一行**之后、`// K/J/L 与 ←/→` 那一段之前**——那正是「清空 → 回车 → 再点应用到全部」刚跑完的位置，**不要**再补一次清空或再点一次「应用到全部」）：

   ```js
   // —— 修复轮 1 的 F-1 另一半牙：坏值**不许扩散到别的行** ——
   // 上面那几条断的都是右栏这一个 `<video>`（`clipSrc()` 读的就是当前行），而
   // F-1 裁定的核心危害正是**扩散路径**：会话级前后秒经「应用到全部」作用于
   // **所有行**。只钉当前行在坏实现下能全绿——当前行有自己的 `q.value[key]`
   // 覆盖时，会话级坏值根本不出现在它的 URL 里。所以这里连着点三行逐行读。
   // 此刻状态：清空那次提交已被忽略、「应用到全部」刚把会话级定成 6/2 并清空
   // `q` ⇒ 所有行都取同一个会话级值。
   let prevSrc = await clipSrc();
   const seenT = new Set();
   for (const i of [0, 1, 2]) {
     const r = (await state()).rows[i];
     const row = page.locator("#app .el-table__row").nth(i);
     // 先证明「第 i 个可见行就是 rows[i]」——筛选若没回到「全部」，这条先红
     eq((await row.locator("td:nth-child(3)").innerText()).trim(), r.t,
        `第 ${i} 个可见行就是 rows[${i}]（${r.t}）`);
     await row.click();
     await waitFor(async () => (await page.locator("#app .el-table__row.current-row").count()) === 1
                               && (await clipSrc()) !== prevSrc,
                   `切到 rows[${i}]，切片 URL 换成了这一行的`);
     prevSrc = await clipSrc();
     const u = new URL(prevSrc, BASE);
     ok(!u.href.includes("null"), `rows[${i}]（${r.t}）的切片 URL 不含 null（实际 ${u.href}）`);
     eq(u.searchParams.get("before"), "6",
        `rows[${i}] 拿到的是会话级有效值，不是被那次清空写坏的值`);
     ok(Number.isFinite(Number(u.searchParams.get("after"))),
        `rows[${i}] 的「后秒」仍是有限数值`);
     seenT.add(u.searchParams.get("t"));
   }
   eq(seenT.size, 3, "三行走的是三个**不同**的切片（不是同一个 URL 被读了三次）");
   ```

   （`prevSrc` 用 `let` 且**不要**撞上后面 `const srcBefore = await clipSrc();`——那个在 K/J/L 段里，是另一个名字。三行 `t` 互不相同所以 `seenT.size === 3` 成立；夹具 12 行都是不同时刻。）

   放在这里做的理由：Task 11 本来就要改 spec 的这一带（R22 改写 + 三处同步），加这条与它们同批提交、同批复审，比现在单开一轮便宜。
2. **720/800 两档窗高仍未进 e2e**（Task 8 节已写明「已知布局缺口、夹具验不了、留给 Task 11 收口判断」）。这里的判断是：**要不要给夹具造真 mp4**——造了才能让 `<video>` 不再退化成 150px，720 的溢出才验得到；代价是给 e2e 引入 ffmpeg 依赖。**裁定：不造。理由是**——(a) 本计划的价值在 **UI 契约**（哪个控件在什么条件下出现/禁用、写操作有没有落盘），不在播放器像素；`<video>` 退化成 150px 只影响"播放器自己的像素"，不影响任何一条契约断言。(b) 720 档溢出是**已知且已量化**的缺口（临界窗高 ≈787.5，Task 8 节有实测数），给未知数付 ffmpeg 依赖的代价不划算；而 787.5 这个数**只有当窗口内容变高时才需要复算**，现在的量法（Task 8 反事实 B，注入 `aspect-ratio:16/9` 后 `scrollHeight 659 > clientHeight 627`）已经把它钉住了。(c) e2e 一旦依赖 ffmpeg，本仓的"改一处只跑相关用例"纪律会被拖慢，而收益只有这一档窗高。**代价（若这条裁定错了）**：720/800 档的真实溢出会一直只靠 Task 8 那次的注入实验背书，真机上有人用 720 档窗口时可能撞到——那时再回来造 mp4。**这条已写进计划，不再是留白。**
3. **F-1 的「清空后失焦」路径没单独实测**（新 e2e 只跑了回车）。源码上回车与失焦两条都汇到同一个 `handleInputChange`，所以方向判定没风险，但依据是**推演**。**如实记在这里、不补测**：依据是 element-plus 2.8.x 的 `handleInputChange` 是 `@change` 的唯一落点，回车与失焦都经它；方向（"忽略这次提交、保留原值"）由 `verifyValue` 的返回值决定，与触发方式无关。真机若出现"回车被忽略、点走却生效"的分叉，从这里查。
4. 两处措辞 nit（F-5 保留的「并**溢出容器**」主语略糊；F-6 仍未提 `ui_prefs.json`）：**不修**，与 F-9/F-10 同例——注释断言的**机制与方向**错了才修，措辞糊不修。

5. **Task 9 实现者报上来的既有 flake（Task 7 段，不是 Task 9 的代码）：`spec:195` 与 `spec:198`** —— 7 次跑红 1 次。诊断（控制者已按源码核过机制，见下）：`keys()`（`spec:178`）是 `press` 之后固定等 **150ms**，而这两条断的是 `confirmRow` 的**自动跳行**——它发生在**回包之后**再 `setTimeout(120)` ⇒ 预算 150ms 里要塞进「一次 HTTP PATCH 往返 + 120ms 定时器」，余量只有 ~30ms 减 RTT。7 次红 1 次正是这个量级的余量该有的样子。**修法（两行，本任务做）**：把这两条的

```js
  eq(await curTxt(), s.rows[1].t, "勾上后自动跳到第二颗");
  ...
  eq(await curTxt(), s.rows[2].t, "再勾一颗、再跳一颗");
```

换成同期望值的 `waitFor`（`waitFor(async () => (await curTxt()) === s.rows[1].t, "勾上后自动跳到第二颗")`，文案照旧）。前面那句 `waitFor(… rows[0].selection)` **保留**——它证的是「服务端写进去了」，与这条证的是「120ms 后界面跟上了」，两件事。

⚠ **只改这两处，别顺手改其它 `eq(await curTxt(), …)`**：`spec:188/190/200/266` 那几处前面跟的是 `↑/↓` 之类的**纯前端同步移动**（没有请求、没有定时器，150ms 绰绰有余）⇒ 把它们也换成 `waitFor` 会把「同步移动退化成异步」这种真 bug 一起放过去。**这条 flake 的判据是「回包 + 定时器」，不是「后面跟了 `keys()`」。**

6. **Task 9 复审报的「永真断言」——把假牙换成真牙**（Control 裁定：本任务做）。`spec:549` 那句 `ok(true, "顶栏「⌨ 帮助」按钮也开同一颗弹窗（不只是 ? 那条路）")` **恒真**，只把项数 +1。它周围的 `:547` 点击 + `:548 waitFor` **确实有牙**（按钮坏了会超时），所以**不是**空覆盖，但漏掉的那一半正好是复审点名的「闸②无直接断言」：`?` 那条路验过闸③（开着时 X 失效），**按钮**那条路没验。改法是把它换成同一件事的**真断言**：

```js
  // 按钮开的帮助窗要跟 `?` 开的那颗**行为一致**——包括闸③（开着时别的键全失效）。
  // 原来这里是一句 `ok(true, …)`：恒真、只把项数 +1（复审抓的）。
  await page.keyboard.press("KeyX");
  await page.waitForTimeout(300);
  ok(!(await state()).verdicts[cur.key],
     "按钮开的帮助窗同样启用闸③（不只是 ? 那条路）");
```

（`cur` 在本段作用域里已定义、`:538` 那条闸③断言就是这么用的；`KeyX` 在这个位置是安全的——帮助窗开着，键本来就该无效。）

7. **帮助表 `↑ / ↓` 那一行的 1:1 偏差**（复审只能算「观察」，我核了原型，**是缺陷**）：原型 `前端原型/剪辑台.html:431` 是**一行两个键帽**——

```html
<tr><td><kbd>↑</kbd> / <kbd>↓</kbd></td><td>上一球 / 下一球（自动滚动 + 播放器联动）</td></tr>
```

而交付版 (`HelpDialog.vue:11-12`) 拆成了**两行**、每行各带一遍完整括注 ⇒ 用户读到的字符多出一整句重复，行数 10 → 原型是 9。本计划的 Global Constraints 第一条就是「三页 Gradio 界面 **1:1 平移**」。改法（`HelpDialog.vue` 的 `ROWS`，把两行合成一行）：

```js
  ["↑/↓", "上一球 / 下一球（自动滚动 + 播放器联动）"],   // 与原型同一行（删掉原来 ↑、↓ 两行）
```

同时把 `spec:534` 那句改掉并**补一条有牙的**（`kbd` 计数抓不到这个形状——一行一个键帽和两行两个键帽都是「10 个键帽」）：

```js
  eq(await dlg("helpdlg").locator("tbody tr").count(), 9, "键位表 9 行（与原型逐字）");
  ok((await dlg("helpdlg").innerText()).includes("上一球 / 下一球（自动滚动 + 播放器联动）"),
     "↑/↓ 合成一行、括注不重复（原型逐字）");
```

**裁定边界（别扩大）**：`1~9` / `J / L` / `Ctrl + Z` 那三行交付版各用**一个**键帽（原型是 `1`~`9`、`J` / `L`、`Ctrl`+`Z` 多个键帽）——**这三处不改**。理由：用户读到的**字**一样，差的只是键帽的 DOM 粒度与空格排版；为一个装饰性粒度去重排另外三行的标签，是把风险加到刚过审的组件上。**判据是「用户读到的字符是否一致」**（括注重复 = 不一致 ⇒ 改），不是「键帽个数是否一致」（⇒ 不改）。真机验收若对这三行提出异议，那是一条 4 行的后续。

8. **Task 10 修复轮 1 新加的那条撤销锁断言：把「造栈顶」的软前提换成硬前提**（定向复审报的唯一一条 Minor，控制者裁定**归本任务做**、不下发第二轮修复——它不是 blocker，但与本任务的 item 2、item 6 是**同一族**：软前提会让有牙的断言变成假绿）。

**背景**：`spec` 里那两次复选框点击是用来给撤销栈造一条「加上」类栈顶的（栈顶的 undo 会**重新勾上**，于是旁路真逃逸时服务端会被写出 `selection: true`，承重断言才会红）。它现在等的是**服务端** `rows[0].selection`；而撤销闭包里的 `before` 是 `actions.js` 从**客户端 store**（`rowOf(key).selection`）取的，后者要等 PATCH 回包落进 store 才更新。**若 store 落后**：第二次点击会推入 `before = false`，逃逸的 undo 变成重发 `selection: false`（空操作）⇒「被拦下的撤销**服务端也没写**」这条承重断言**假绿**，红会落到它后面那条 toast 断言上。

**为什么不是 blocker（别把它当事故读）**：现实暴露面是「服务端写完 → 浏览器把回包刷进 store」之间不到 10ms；而且修复轮的红跑实测证明 `before === true` **确实成立**（退回旧写法后红在承重那条、不是 toast 那条）。

**改法**：两次点击各补一次**渲染态**的等待，**替代**（不是叠加）现在那次等服务端 `selection` 的 waitFor——点第一次后等页脚那条「勾选 N 条进集锦」**变**成选中态、点第二次后等它**变回**点之前的值。⚠ **判据用「变化」而不是硬编码数字**（本段起点是不是净未勾选，由上一段决定）；页脚的实际文案以仓库现况为准，别照抄本计划正文里的措辞。页脚是 `state.rows` 派生的 ⇒ 它变了就等于「这次改动已经落进 store」，比等服务端真值**更强**（store 更新本来就在回包之后）。

- [ ] **Step 1: 收尾修正 F1 —— 筛选与当前行的一致性（R22）**

Task 8 让右栏在**全部** `rows[]` 里找当前行（筛选藏起来也不清空），但 Task 7 的键盘动作走 `rowByKey`（**只看可见行**）⇒ 两者一旦分叉就会出现「右栏显示着 A，按键却毫无反应」。修法是让 `curKey` 永远落在可见集里：在 Task 6 的 `visibleRows` 之后补一个 watcher：

```js
// 筛选把当前行藏起来 → 连当前行一起清（R22）：el-table 的高亮本来也就没了，
// 不清的话右栏还显示着它，而键盘动作（rowByKey 只看可见行）会静默无反应。
watch(visibleRows, (list) => {
  if (curKey.value && !list.some((r) => r.key === curKey.value)) curKey.value = "";
});
```

（`watch` 并进 vue 那一行的 import。）

e2e 追加段（放在 Task 10 那段之后）：

```js
  // —— 收尾修正 F1（R22）：筛选与当前行的权威只有一份 ——
  await rowCell(0).click();
  await waitFor(async () => (await page.locator("#app .el-table__row.current-row").count()) === 1,
                "点一行 → 有当前行");
  await page.locator("#app .toolbar .el-radio-button").filter({ hasText: "🟡" }).click();
  await waitFor(async () => (await page.locator("#app .el-table__row.current-row").count()) === 0,
                "筛掉当前行 → 高亮与 curKey 一起清掉");
  ok((await page.locator("#app .right").innerText()).includes("点击左侧表格任意一行开始复核"),
     "右栏回到空态（不再显示被筛掉的那一行）");
  await page.keyboard.press("Space");
  await page.waitForTimeout(300);
  ok((await page.locator("#app .tblfoot").innerText()).includes("勾选 0 条进集锦"),
     "没有当前行时键盘动作是 no-op（不隔空勾一条看不见的球）");
  await page.locator("#app .toolbar .el-radio-button").filter({ hasText: "全部" }).click();
  await waitFor(async () => (await page.locator("#app .el-table__row").count()) === 12,
                "筛回全部 12 行");
  ok(!errs.length, "收尾段没打出 JS 异常");
```

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/e2e/test_edit_bay_e2e.py`
Expected: FAIL —— **红在「右栏回空态」那条**。（控制者订正，Task 11 复审抓出来的：原文写「第二段断言超时（`current-row` 还在，筛选没清当前行）」，**两处都不对**——`current-row` 无论有没有 watcher 都是 0，因为 el-table 的 `updateCurrentRowData` 自己会把内部 currentRow 清掉、行也从 `data` 里没了；照这条 Expected 去验的人会以为段落写错了、去改不该改的地方。）

⚠ **同时必须改写 Task 8 段里那个「筛选把当前行藏起来时右栏也不清空」的块**（在 Task 8 那段里，紧跟 `const timeCells = …` / `const tierEmoji = …` / `const other = …` 之后，含 `ok(!(await timeCells()).includes(cur.t), …)` 与 `eq(await page.locator("#app .videobox video").count(), 1, …)`）：**本步的 watcher 一落地，那条断言就必红**——它钉的行为（`curRow` 在**全部** `rows[]` 里找 ⇒ 右栏不清空）正是 R22 要反转掉的。改法是把它换成 R22 的行为断言（右栏回空态、与上面那段新的「筛掉当前行 → 高亮与 curKey 一起清掉」同义），**不是**去回滚 `curRow` 的查找范围。Task 8 那段里的注释已写明「本任务是对的、Task 11 Step 1 负责改写」，别把它当成 Task 8 的 bug。

⚠ **改写那个块时注意它清空当前行、而这件事会波及邻居**（派发前审计抓到）：本步的 watcher 只做「筛选把当前行藏起来就清」，**没有反向**——上面那个块「筛到别的档位再切回『全部』」之后 `curKey` 是**空**的，切回「全部」不会把它找回来。Task 8 段原来能过，靠的正是「筛选不动 `curRow`」这个**已被 R22 反转掉**的行为。

**这段耦合已经由 Task 9 段自己承担掉了**（在 Task 9 交付时，控制者按「每段自报前置」的既有形状要它补了段首复位：`rowCell(0).click()` + 等 `current-row` 出现），所以**本步不要在这个块里再补一次段尾还原**——重复的机制会把「Task 9 段依赖 `rows[0]`」这件事藏起来，下一个读 spec 的人看不到。**本步要做的只有**：把这个块的断言从「右栏不清空」改成「右栏回空态」，别动其它。

⚠ **已裁定（Task 11 交付后回填，控制者）：上面那句「本步不要在这个块里再补一次段尾还原」只对了一半——交付版确实补了一次。** 位置在 `tests/e2e/edit_bay.spec.mjs` 的 **Task 8 段尾**（把当前行放回 `rows[0]`，1 击 + 1 个 `waitFor` + 注释）。**为什么那句禁令错了**：它只把这段耦合记在 **Task 9 段**头上，**漏了同一个块下面两行的段尾复原是 Task 8 段自己的消费者**。复原要用的 `beforeInput`/`.afterInput`/`.pctrl` 都在 `ClipPanel` 里，而 `ClipPanel` 挂在 `v-else`（`curRow` 非空）那一支（`EditBay.vue:537-539` + `ClipPanel.vue:134-144`）⇒ `curKey` 一空，`.fill()` 找不到元素、Playwright 等 30s 抛错，**红得与本段代码无关**。补充的写法符合那句禁令**的本意**（禁令真正要禁的是「去回滚 `curRow` 的查找范围」），所以**裁定为准，禁令的后半句作废**。**教训**：写「某段耦合已经由某某承担掉了」时，要把**同一节里往下数**的消费者一起数完——只数到别的节，就会禁掉一件非做不可的事。

补上 watcher 后再跑，Expected: PASS

- [ ] **Step 2: 全量 e2e（spec 的每一段一起跑）**

```bash
cd frontend && npm run build
```

```bash
./.venv/Scripts/python.exe -X utf8 -m pytest -q tests/e2e/test_edit_bay_e2e.py
```

Expected: PASS，输出里有 `✅ 全过`；并且**确认没有 skip**——`1 skipped` 意味着 Node/Playwright 没装上，那不是通过而是没跑（先按配方 C2 装：`cd frontend && npm i -D --no-save playwright`）。

- [ ] **Step 3: 冒烟（三页可开无 JS 报错）**

配方 **C**（临时服务 + `BBH_BASE=… node frontend/smoke.mjs`）。
Expected: PASS —— `✅ 冒烟通过：三页可开、Vue 真挂载、tab 高亮正确、可跳转、无 JS 报错`

- [ ] **Step 4: **唯一一次**全量 Python 测试**

⚠ **跑之前先给 `work/` 拍一张照，跑完再拍一张，把两张的差抄进报告**（这是**真风险**，不是仪式）：`_work_dir_for()`（`app.py:336`）返回的是**相对路径** `work/<视频名>`、按 cwd 解析（就是 R18 那条机制）——任何**没有 `chdir` 到临时目录**却走到了切片/出片路径的用例或探针，都会把产物写进**仓库根的 `work/`**。已经在仓库里抓到过一次实物：`work/sample/preview.mp4`（**1 字节**，mtime 2026-09-19 10:10）；`work/sample/` 里没有任何 sidecar ⇒ 它是**测试残渣**，不是用户的会话目录（用户的真档在 `work/2026-09-11_3` 等目录里，至今未被动过）。

```bash
SDD=.superpowers/sdd/2026-09-19-frontend-refactor-edit-bay   # 本计划的工作区（gitignored）
find work -type f -printf '%T@ %s %p\n' | sort > "$SDD/work-before.txt"
./.venv/Scripts/python.exe -X utf8 -m pytest -q
find work -type f -printf '%T@ %s %p\n' | sort > "$SDD/work-after.txt"
diff "$SDD/work-before.txt" "$SDD/work-after.txt"
```

**判据**（`work/` 顶层现在有 25 项，分三类，别把三类混着看）：
- **允许**：`work/_*/**`（既知 scratch，如 `_debug_preview`、`_edge_tmp2`）、`work/sample/**`（既知残渣，本来就可能被同一条机制再写一次），以及本次全量跑**新造**的夹具目录（例如 `work/e2e_fixture/**`——出现了要记一笔，说明有用例没 `chdir`，但它不是用户数据）。
- **停手并如实报告**：`work/2026-*`、`work/stodownload*`（**用户的会话目录**）、以及 `work/` 根下那几个散档（`t.png`、`测试视频.mp4`、`_smoke.mp4`）被改写 —— **不要做任何清理**，那是用户的数据，清不清理是用户的事。
- `0 failed` 的期望值不变；把上面的实测数字一起抄进提交信息——**数出来的，不是猜的**。基线是 Plan A 的 `610 passed / 1 skipped`，Plan B **只增不减**（新增：`tests/test_web_session.py` 的 `verdicts`、`tests/test_web_events.py` 的 restore-by-keys、`tests/e2e/test_edit_bay_e2e.py` 1 条）。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/edit/EditBay.vue frontend/src/edit/HelpDialog.vue tests/e2e/edit_bay.spec.mjs tests/e2e/test_edit_bay_e2e.py bbh/static
git commit -m "fix(frontend): 筛选藏起当前行时连当前行一起清（R22）+ 补四处牙、修一处 1:1 偏差

右栏在全部 rows[] 里找当前行（筛选藏起来也不清空），而键盘动作按可见行找
（rowByKey）——两者分叉就会出现「右栏显示着 A，按键毫无反应」。改成 curKey
永远落在可见集里：筛选把它藏起来就一起清，右栏回空态提示。

顺手清的账：
- Task 8 的 applyAll 门只被「两个门一起摘」验过 ⇒ 逐行读切片 URL 的断言补上
  （坏值不许扩散到别的行）。
- Task 7 段两处自动跳行断言是固定等 150ms、而自动跳行发生在回包后 120ms ⇒
  换成 waitFor（只这两处：别的 curTxt 断言跟的是纯前端同步移动）。
- Task 9 段那句 ok(true, …) 恒真 ⇒ 换成「按钮开的帮助窗同样启用闸③」。
- 帮助表 ↑/↓ 两行合成一行（原型是同一行，原来括注重复了两遍）⇒ 与原型逐字。
- test_edit_bay_e2e.py 的临时服务超时兜底不端日志（另外两条失败路径都端），
  与它们拉齐。
- 同一份夹具的 docstring 那句「行数够多，表格才会真的出现滚动条」被 Task 7 段的
  实测推翻（900 高下 12 行全在可视带内）⇒ 订正成「行数够多**且段里把视口压矮**
  才有牙」。
- Task 10 修复轮 1 的撤销锁断言里，「造栈顶」的两次点击等的是服务端真值、而 undo
  闭包的 before 取自客户端 store ⇒ 改等页脚渲染态（同一族的软前提换硬前提）。

顺带跑完全量：e2e spec 全过（含冒烟）+ 全量 pytest <实际数字>（Plan A 基线
610 passed / 1 skipped）。"
```

⚠ **`git add` 里那五行一个都不能少**：`frontend/src/edit/HelpDialog.vue` 是第 7 条（1:1 偏差）改的；`tests/e2e/test_edit_bay_e2e.py` 是本任务第一次改它（F3′ 那一行），漏了它提交里就没有那笔账；`bbh/static` 是配方 A 的产物，漏了 `bbh/web/app.py` 的 `StaticFiles` 就会把**旧前端**端给冒烟和 e2e（`STATIC_DIR` 指向它）。

---

## 本计划不做的（Plan C–E 范围表）

spec 里属于剪辑台、但本计划**刻意不做**的条目，一条条指到承接的 Plan——写在这里是为了让「spec 有、plan 无」永远是**已知项**而不是漏项：

| spec 的位置 | 内容 | 承接 |
|---|---|---|
| 痛点表第 3 行 + §API 出手人墙（6 个端点，含 `GET /api/wall` 的 `sort=time\|appearance\|spot` 三态与 `GET /api/wall/guard` 归人护栏） | 出手人墙页：大卡片（≥200px）、多选托盘、拖到人名组归人、已归拖回未归、按人折叠 | **Plan C** |
| 痛点表第 4 行 + §API 标定（6 组端点） | 标定向导页：四步 `el-steps`、补充段/删段独立管理卡 | **Plan D** |
| §浏览器端到端「用例名与原型自测对齐」里的 `顶栏 tab 三页共用条` | 三页共用的 sticky tab 条（`shared/tabbar.js` 已在 Plan A 交付）——三页齐全后这条断言才有意义 | **Plan E**（跨页 e2e） |
| §真机验收 | 09-11_3 全流程；横屏/竖屏成品与旧界面同参比对（条数/顺序/时长/文件名）；确认 `work/` 没多出意外文件；新旧界面**双向兼容**逐项核对；`run_app.bat` 切到 `bbh.web` | **Plan E**（开工前先备份 09-11_3 的 `work/`） |
| §API 生成「完成后预览位自动播放第一个成品」 | Task 9 的结果视图只给**预览链接**（`<a href="/media/work…">`）；「自动播放」要真渲染出成品才验得动 | **Plan E**（真机验收时顺带看） |
| §风险与回退「旧界面回退路径失效」 | 回退验收 = 改一行 bat 后旧界面能打开并完成一次检测 | **Plan E** |
| §静态与媒体路由「帧率由后端探测下发」 | 见 R35：本计划按固定 1/30 步进（原型同款），`fps` 不进 state | **Plan E**（若手感不对再补） |

剪辑台自己的 spec 条目（痛点表第 1/2 行、09-17/18/19 的六条裁决、§API 会话/事件与复核/生成与长任务里剪辑台用到的端点）逐条落在 Task 1–11，对应关系见各 Task 的 Interfaces 块。

---

## 收尾自查（writing-plans 的 Self-Review，已由控制者跑完，结果记在这里）

1. **裁定表**：R21–R35 全部落在上面「计划期裁定」表里，不在本节重复列（两份清单必然漂移）。
2. **`_cancel_evt` 的死路**（R33）：新页面没有 Gradio 的 `demo.unload` 钩子 ⇒「刷新/关闭页面会取消检测」在新架构里不成立，`res.cancelled` 分支在前端不可达（后端仍保留，回退 Gradio 时仍需要）——文案已按 R33 改掉。
3. **placeholder 扫描**：全文搜 `TBD`/`TODO`/`待补`/`同 Task N`/`略` ⇒ 只剩两处合法命中（`jobs.js` 里 `catch { /* 同上 */ }` 注释、本节自身），无真占位。
4. **命名一致性**：`dispT`/`tsec`/`tierOf`/`tierText`/`write`/`act.*`（13 个方法名在 Task 4 定义处与各 Task 调用处逐一核过）/`durOf`/`setDur`/`applyAll`/`longJob`/`JOBS`/`rowByKey`/`curKey` 前后同名同义；`before`/`after` 不与 `pre`/`post` 混用（原型变量的 `preInput` 已改名 `beforeInput`）。
   修掉的三处不一致：① `BasisPanel` 卡头写死 `dispT(row, 0)` ⇒ 改吃 `:offset`（R34，并补 e2e）；② Task 10 的两条 e2e 断言用「元素不存在就抛」的 `innerText()` 等渲染 ⇒ 改先 `count()` 再读；③ popover 的两个选择器带 `#app`（`el-popover` 内容 teleport 到 body，会匹配不到）⇒ 改 `body …`。
5. **spec 覆盖核对**：剪辑台条目逐条指到 Task 1–11（痛点表第 1/2 行 → Task 6–8；09-17/18/19 六条裁决 → Task 6/8/9/10；会话/事件/生成端点 → Task 2–11）；落不到本计划的七条已进上面的「Plan C–E 范围表」。spec §测试 点名的用例（`置信度算式自洽`、`依据卡不常显算式`、`操作按钮排在出手人 chip 之上且不用滚动就能点到`）已作为断言名写进 Task 8 的 e2e；`顶栏 tab 三页共用条` 归 Plan E（跨页）。
6. **执行方式**：按 writing-plans 的收尾给用户两个选项（SDD 推荐 / inline），见对话正文。

---

## 交付后已知项（**不修，记账**——控制者在计划收尾时回填）

11 个任务已交付，终审 + **两轮修复** + 两次定向复审闭环，无未决承重项。下面这些是**明确裁定不修**的项。
写在这里是为了让「知道有问题但决定不修」永远是**已知项**而不是漏项——判据一律按**它会不会让假绿漏过去 /
会不会让用户可见的功能出错**两级判，**不按「写得好不好看」判**，并在每一档内保持同一把尺子。

> ⚠ 本计划的一次性工作区 `.superpowers/sdd/2026-09-19-frontend-refactor-edit-bay/`（账本、任务书、复审报告、
> 红跑证据）按 SDD 流程**在收尾时删除**。原本 committed 代码里有两处指向它的注释（`tests/test_web_jobs_api.py`
> 的 docstring、`frontend/src/edit/EditBay.vue` 的注释）——**② 的修复轮已把它们清干净**（现存指向一次性工作区
> 的注释只剩先例那处：`tests/test_web_locks.py:9`，它引的工作区早已不在，只是文字）。要留证据的话，
> **判词与裁定都已回填进本文件**；spec 项数的复核脚本已抢救至 `tests/e2e/count-spec-items.py`。

### A. 终审 park 的 11 条（终审逐条判「可以留」）

1. `POST /api/wall/scan` 的 `undefined` body 没有**直接**断言（现有桩只断 400 的形状）。
2. 某条 `eq(await locked(), 0, "失败不锁表")` 的**牙偏软**（锁表本会有别的断言先红）。
3. `D` 键确认框的 teleport 在 `.main.locked` 之外仍可弹——**写入被闸住**，属 UX 小瑕疵，不是越权。
4. `res.cancelled` 分支的 UI **不可达**（`detect` 的取消走的是别处；R33 已记）。
5. `wall_scan` 那半边续接**只由共享 `if` 覆盖**（e2e 走的是 `detect` 那条腿）。
6. `resumeJobs` 的 **gen（生成）那条腿没有测试**。
7. `720` 宽时右栏溢出的临界高度 ≈ **787.5px**（「不造真 mp4」的裁定挂着）+ 时间轴圆点跟随偏移一处。
8. 生成弹窗「**关掉再开**」的进度态未测。
9. ~~键盘闸② 没有直接断言~~ ⇒ **已修**（修复轮组② 的 park #9，`spec.mjs:854-876` 五条断言）。
10. `spec.mjs:508` 的 R22 断言在 Task 8 段是**裸 `ok()`**，而同一件事在新 F1 段用 `waitFor`；靠紧邻的
    `timeCells()`（一次 CDP 往返）兜住，实测绿。
11. 偏离①把当前行留在 `rows[3]`，而 Task 8 段 R22 块的前置断言断的是 `cur.t`（= `rows[0].t`）——
    **当前恰好两者同档（都 `high`）才成立**；夹具档位若轮转，会变成一条**假红**（不是假绿）。

### B. 终审裁定不修（组③「明确不做」）

`A-6`（单位/`null` 写法）、`A-11`（强调丢失）、`A-7` 的**分色条外形**（26px 色带 / `.seg-red` / `calc(…% - 1.5px)`
一字未动，只修了 `goalmk` 的落点——② 之后它还吃了偏移：`dispT(row, offset)`，与表格时刻列同源）、**`B-2`**（`markVerdict` 的两步撤销：第 1 步成功、第 2 步失败后重按
`Ctrl+Z` 会重发第 1 步 ⇒ 把裁决又标回去；触发窗口窄，改法要重设计栈记录形状——**若后续动 `actions.js` 的撤销栈，
必须先处理它**）。
两处 **1:1 的缝**：帮助表交付 `↑/↓`（原型是两枚键帽，**用户读到的「字」一样**）、`K` 行「播放/暂停」vs 原型
「播放 / 暂停」——按计划判据（**字一致、空格/键帽粒度不算**）不修。

### C. 修复轮复审 park

- **新-2｜顶栏四枚 tag 与生成按钮组的先后/右对齐与原型不同**（原型 `.stats{margin-left:auto}` 包住「tag → 按钮组 → 帮助」；
  实现是「按钮组 → tag → `.spacer` → 帮助」）。**但这是旧的**（`bb941ff` 就是「按钮组→计数→spacer→帮助」，
  `f364c93` 只是把 `<span class="dim">已勾选` 换成了 tag），终审读过那份 diff 没判它重要 ⇒ 纯观感，与 B 档同尺。
- **新-4｜`_finite` 放行 `bool`**（`isinstance(True, int)` ⇒ `before: true` 会被当成 1 秒）。**理论边界**：
  正常前端发不出，且动它会碰到被牙齿单测钉住的有限值语义（「坏值退下一层、绝不掉回常量 5/2」）⇒ **不修**。

### D. 二轮复审 park 的 2 条 Minor

- ~~模板折行留下的**半角空格**进渲染文本（渲染成「…关闭弹窗即复原 （没有…」）~~ ⇒ **已于 ② 消除**
  （「关闭弹窗即复原」那句文案随弹窗改写一并不在了）。
- ~~`OffsetDialog.vue` 头注释 `:3-5` 的断言要到 `:10-14` 才被 `⚠⚠` 限定~~ ⇒ **已于 ② 消除**
  （头注释随 ② 整体重写）。

### E. 范围外观察（复审记录在案，均**非阻塞**）

1. **会话级前后秒跨视频不重置**（`durAll`/`durMap` 在换视频时不清零）——既有行为；修复轮让它**对成品也生效**，
   ⇒ 反而**减轻**了「预览 8/3、成品 5/2」的不一致。要不要在换视频时清零属**产品决策**。
2. ~~校准开启时 `ClipPanel` 的「进球 mm:ss」标注用 `row.t`（原始串）~~ ⇒ **已修于 ②**
   （`ClipPanel.vue:122` 改 `dispT(row, offset)`，红线标签与表格时刻列同源）；「该路径今天不可达」
   也随之作废——② 接通后校准可达（顶栏 ⏱ 指示器）。
3. 校准弹窗里点「↻ 重新载入」会让预览与输入框短暂不一致（`@reload` 走 `/api/state`，不带 offset）——既有。
4. **一次未能解释的观察**：e2e 里 `rowCell(0).click()` 后「当前行」两次跑分别落在 `00:01.2` / `00:02.0`。
   按代码两种情形都不该出现；两种可能（过期高亮对象 / ③ 段删除+恢复后 DOM 行序与 `state.rows` 不同）
   都落在「高亮 DOM 与 `curKey` 短暂不一致」这一侧，而 `durs` 的键取的是 `curKey` ⇒ **不影响成品正确性**。
   记在这里是为了让后人知道这处观察**没有被解释掉**——将来再看到「高亮与右栏不是同一行」从这里查起。

### F. 交接给下一个计划的两件事（第 1 件已交付，本节留档）

1. **`work/` 写守卫 —— ✅ 已交付并关闭（2026-09-19，分支 `feature/vertical-highlight`：`c39858a` 守卫本体 →
   `93c3ac1` 修复轮 1 → `97ec6dd` 修复轮 2 → `2be72db` 修复轮 3 → `0db9db8` 修复轮 4；`a198899`/`824b1f6` 是
   控制者对本节的两笔订正 —— 三轮定向复审 + 一次收尾复审，`631 passed, 1 skipped`）**，不再是待办。
   交付形态是**测试侧重定向**（`tests/write_guard.py`
   的纯逻辑与会话状态 + `tests/conftest.py` 三个 autouse fixture），**一行生产代码都没动**：
   - 挂钩 `bbh.app._work_dir_for` 这个**模块属性**（`_work_dir_for` 的 **40 处**路径计算全走它，量法命令见
     `tests/write_guard.py` 头注释），被判进真 `work/` 的路径改指到当前 guard 根下的同一尾巴
     （`<guard_root>/work/<尾巴>` —— 与测试自己 `chdir` 的落点逐字一致，否则一半经守卫写、一半按相对路径读会分叉）；
   - 每测 guard 根 = 该测试自己的 `tmp_path`；仓库根 6 档（`tags.json` / `ui_prefs.json` /
     `calibration*.{json,png}` —— 与 e2e 侧 `tests/e2e/test_edit_bay_e2e.py:174-177` 那份名单**完全一致**）
     在 conftest **导入期**拍 sha256 基线、teardown 比对，变了就抛（报警器，**不设白名单**）；
   - 收尾行 `pytest_terminal_summary` 分开报**非预期**与**预期**两个数，判据是「非预期 0 且预期 ≥1
     且退出码为 0」—— 末一条是必须的：`expect_hit()` 是**先记账、后断言**，只看两个计数的话，
     每测 guard 根一旦失效（自带用例红了）那笔命中已在「预期」里 ⇒ 会替一条**已经红的 run** 打 ✅。
     另有第三支「**判据不可判**」：非预期 0 + 预期 0 + 退出码 0（`-k` 子集 / `--co` / 收集不到）
     ⇒ 不给 ✅、也不报 ⚠。判词是 `tests/write_guard.py` 的**纯函数** `summary_line`，三分支被 **6 条**
     纯函数用例钉住（含一条「全绿必须含 ✅」的正对照，与一条「印出来的 ⚠ 必须写全**三个**合取项」）。
     ⚠ 那一支**只声明判据未满足、不声称原因**（`SUMMARY_WARN` 印的就是上面那句判据本身）—— 因为计数
     分不清「压根没跑到」（子集/收集不到，本该是 ℹ 那格）与「自带用例**跑了却没记到就红了**」
     （= 守卫坏掉的形状），后者报 ℹ 会**低估**真故障 ⇒ 宁可话少也不谎报。单数字写法（`拦截 0 次 ✅`）
     在钩子被换成「还在但不改道」的空壳时照样打 ✅，是主动误导；
   - **已知边界**：收尾行的判据只数两个计数 + 退出码 ⇒「守卫自带用例被改名/删掉」与「`-k` 只选了子集」
     在它看来**不可区分**（都是非预期 0 + 预期 0，退出码 0 时打中性 ℹ）。要更严得改看 session 侧的
     nodeid 是否被 collected/deselected —— 属「扩判据」，本批不做；
   - ⚠ **一度想过的形状（数据根显式化：`_ROOT` + `os.environ.get("BBH_WORK_ROOT") or os.path.join(_ROOT, "work")`，
     conftest autouse 把 `BBH_WORK_ROOT` 指到 tmp）已作废**，别再照旧版文档去改生产代码。三条理由
     （`tests/conftest.py:68-77` 有照录）：① 那会让 `_work_dir_for` 变成 cwd 无关的**绝对路径** ⇒ 改**已冻结的
     旧界面（Gradio）行为**；② 仓库里大量 `monkeypatch.chdir(tmp_path)` 的沙箱手法全靠相对解析
     （量法：`git grep -c 'monkeypatch.chdir(tmp_path)' -- tests` ⇒ **36 行**文本匹配，含注释里的提及，
     其中 12 处是守卫本批为漏网测试补的），env 覆盖会把它们
     **全部架空**（断言写成 `<tmp>/work/...` 的一律红）；③ e2e 靠 `cwd=临时目录` 起 uvicorn 子进程做隔离，子进程
     `env=os.environ` 会**继承**父进程的 `BBH_WORK_ROOT` ⇒ 服务端的 `work/` 与 e2e 断言的 `root/work/` 分叉。
   - **已知边界**（别读成「测试进程再也写不进真数据」的全域保证）：`bbh/app.py` 的 `migrate_root_calibrations()`
     不过 `_work_dir_for`（它 `os.listdir("work")` + `copy2` 直写真 `work/`、认领成功还删仓库根标定档）；
     测试档里另有大量字面 `os.path.join("work", …)` **也不经它**（量法：`git grep -c 'os.path.join("work"' -- tests`
     ⇒ **27 行**文本匹配）—— 这些今天各自靠 `chdir` 隔离。`work/`
     整树不做逐档快照（7394 档太贵）⇒ 重定向守 + 根档报警器是**两条网**，不是全域护栏。
2. **`B-2`**（见 B 档末）——动 `actions.js` 撤销栈之前必须先处理它。

### G. 本轮交付的指纹（便于回溯）

- `f364c93` 修复轮一（组① 4 条 + 组② 9 条）：`17 files changed, 614 insertions(+), 32 deletions(-)`
- `ade290b` 计划文本订正（R7 那句「纯前端显示偏移」是 A-1/A-2 的源头 + `keyName` 两处定义）
- `ada8785` 修复轮二（校准弹窗 tip 改回「今天为真」的说法 + 两处注释订正）：`7 files changed, 20 insertions(+), 11 deletions(-)`
- 验证口径：全量 `620 passed / 1 skipped`、e2e `1 passed`、spec 项数 **180**（`129 →(T10 +6)→ 135 →(T11 +15)→ 150 →(修复轮 +30)→ 180`）、
  `frontend/selftest.mjs` **28 通过**；红线 5 档 md5 每次提交前后逐位一致。
- **未 merge、未 push**——分支去向由用户决定。
- ①（work/ 写守卫）与 ②（校准偏移接通）的指纹见 F 档与下文 H 档。

### H. ② 校准偏移接通（2026-09-19，用户批准）

**服务端一行没动**——`_apply_session_rows` 的平移是终审 A-1/A-2 就交付的；② 只是接通界面
（「关闭即复原 + 模态」曾让这条管线**真界面不可达**，用户裁定废掉该语义）。链路五跳：
`EditBay` 的 `offset` ref → `sessionDurs()` 并进 `/api/gen` 请求体（`EditBay.vue:264/272`）→
`api_gen._apply_session_rows` **先把所有行的 `durs` 键读完、最后才整体平移 `row[1]`**
（`max(0.0, …)` 夹负与 `app.py:801`/前端 `tsec` 三处同义）→ 平移后的切点进 `gen_selected`。
**绝不**把 offset 传给 `load_events_table`（行键会与 `_verdict_keys` 分叉）。

- **界面语义**：关闭只是关闭（× / Esc / 遮罩 / 按钮四条路一个语义，都不动 offset）；
  复原入口只有「归零」，「取消」按钮删掉；`offset ≠ 0` 时顶栏显示 `⏱ 已校准 +1.5s`。
- **「时刻同源」不变式**（改前端必须守）：凡是**屏上显示时刻**的地方都吃同一个 offset。
  判据是 `dispT`/`tsec` 的**调用点**（`git grep -n 'dispT(\|tsec(' frontend/src`），不是名单——
  名单必然过期（R34 后「三处」→「四处」，② 里「四处」→「六处」，最后改成指针）。
  服务端切点**不吃** `dispT`（它平移 `row[1]`），别算进前端名单。
  第二条腿（不吃 `dispT` 的文案面）今天仍不同源、已 park（见 I 档 2）。
- **⚠ ref 陷阱**：`dispT`/`tsec` 的 offset 形参必须传**数字**。传 ref 时 `Number(ref)`=NaN
  ⇒ falsy 分支 ⇒ **静默退回原始串**，症状与「没改」逐字相同。`<script setup>` 的 JS 里写
  `offset.value`，模板自动解包，子组件用 `props.offset`。这条是修复轮 1 的实锤，被
  「删除确认框文字 == 表格时刻单元格**逐字比对**」的 e2e 牙第一次跑就红挡住——软判据
  （只断「字样变了」）对 ref 泄漏照样绿。
- **指纹**：`4c68e06`（9 档 +136/−41）→ `273132a`（6 档 +58/−15）→ `95e6145`（5 档 +29/−17）
  → `0cadbc3`（7 档 +36/−9），合计 `10 files changed, 226 insertions(+), 49 deletions(-)`。
  验证口径：全量 **631 passed / 1 skipped**（守卫非预期 0）、e2e `1 passed`、
  spec **190**（180 →(② 主轮 +6)→ 186 →(修复轮 1 +4)→ 190）、selftest 28；
  红线 5 档 md5 每次提交前后逐位一致、`work/` 全树指纹不变（7394 档）。
- **复审链**：任务复审（承重 1 / 实质 4 → 修复轮 1）→ 定向复审（轻微 2 → 修复轮 2）→
  收尾复审（承重 1 / 实质 4 / 轻微 3，裁定全 park → 修复轮 3）→ fix3 定向复审
  （**承重 0 / 实质 0 / 轻微 1 park**）。过程教训三笔（详账本 ⑪-⑯）：ref 陷阱（上述）；
  收尾复审 Info 8「负偏移夹负无断言」是**错报**（`f364c93` 起就有，任务书照抄未核）——
  **控制者任务书里每个「今天没有 X」与每个具体值（类型/判别力）都必须先核再写**；
  修复轮 3 中途一次 API 402 截断，①②③ 由上一段会话完成、④ 由续段完成（诚实史在实施者报告）。

### I. ② 收尾复审后的已知项（2026-09-19 回填并经用户裁定：1 → Plan E 实测后定、2 → Plan C 后小任务，其余 park）

1. **（承重，park，1:1 旧界面）** `bbh/vrender.py` 的慢放锚/跟球链在**非 0 偏移**下静默失效：
   `_goal_cam`（`:594`）/`_prepare_ball`（`:619`）/ 钩子（`:659`）都用**偏移后的**切点 `t` 查
   `traces.get(time_key(t))`，而 `traces` 的键来自 `shooters.json` 的**原始** `e.t` ⇒ 查不到（退化）
   或极端偏移下命中**别人的**锚。**旧界面同病**（`app.py:2315` 把 `load_table_for_ui(vp, offset)`
   出来的、已烘进偏移的 rows 交给 `gen_selected`）⇒ 按 R9 的尺子判 **1:1，非本批缺陷**。
   要不要按「锚数据也在偏移时间基上」重新定义语义是**产品/领域决策**，不在本计划内做。
   ——**用户 2026-09-19 裁定：park 到 Plan E**（真机验收用非 0 校准实际生成一次、实测影响面后再定语义）。
2. **（实质，park，需口径）** 三处文案仍在**原始时间基**：删除 toast、裁决 toast（文案在
   `bbh/app.py` 的 `delete_events`/`mark_verdict`）、前端 `actions.js` 的 Ctrl+Z 撤销文案
   （`rowOf(key)?.t`）——而 `app.py:1723-1747` 的既有契约明写「**msg 用显示时间（用户视角）**」
   （旧界面靠把偏移烘进 `rows[1]` 成立），`bbh/web/api_events.py` 却用 `load_events_table(vp)`
   （offset=0）⇒ 与表格不同源。修它要把 offset 传进删除/裁决端点，且 `app.py:1919/1932` 的
   `t_str` **兼作存储字段与匹配容差**（不能就地替换）⇒ 新任务 + 口径决策。
   ——**用户 2026-09-19 裁定：Plan C 之后开小任务**（口径：删除/裁决端点收显示偏移、`t_str`
   保持存储键不动、msg 的时刻单独算显示值；顺手带上 `main.js` 装 locale 修 `ElMessageBox`
   英文按钮——即第 3 条）。
3. **`ElMessageBox` 按钮是英文**（`main.js` 裸 `app.use(ElementPlus)` 没装 locale）——既有状态
   （Plan A 期起），② 裁定不修；**Plan E 真机验收候选**。连带：e2e 点确认框不能按文案选，
   用语义选择器 `.el-message-box__btns button:not(.el-button--primary)`。
4. **偏移跨视频切换持续**（② 复审轻微 C）——与 E 档第 1 条（前后秒跨视频不重置）同族同尺，
   会话级语义 + 顶栏指示器可见 ⇒ park。
5. **spec.mjs 的「第二条腿」注释括号理由不完整**（fix3 复审轻微 1）：`spec.mjs:740` 那句
   「（服务端不知道 offset）」只解释两条服务端 toast，没提 `actions.js` 那处的病因
   （没走 `dispT`）；权威表述在 `OffsetDialog.vue` 头注释，是对的 ⇒ 观感级，按 D 档同尺留。
6. **已关闭防再报**：收尾复审 Info 7 曾报「`durs` 键在非 0 偏移下的 e2e 覆盖缺口」——经核
   **两侧都有覆盖**（`spec.mjs:1024-1035` + `test_web_jobs_api.py:473-483`），关闭。
