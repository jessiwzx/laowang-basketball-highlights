# 前端重构 E1：测试加固 + 跨页 e2e（Design）

**日期**：2026-09-21　**状态**：设计定稿（用户已裁：Plan E 切两份，E1 自动化先行、E2 真机后行）
**上游**：`2026-09-16-frontend-refactor-design.md`（总 spec）+ 四份计划（edit-bay / wall / calib / edit-msg）的承接表与「交付后已知项」
**执行**：SDD（`superpowers:subagent-driven-development`）

## 背景与目标

三页 SPA（剪辑台 / 出手人墙 / 标定向导）+ 文案时间基小任务已全部交付（代码终值 `bd7c008`，全量 635/1，spec 项数 191/43/81）。四批积累的 parked 项里，**纯自动化**的那部分归本计划（E1）：把项数从「人肉数」变成「门守的量」、消灭已知的假覆盖/不可达红/预算口径含糊、收口非有限 `offset`、补上「屏上时刻同源」的判据升级与三页联动 e2e。**需要用户参与或产品决策的部分（真机验收、双向兼容、vrender 锚实测、fps 手感、预览自动播放）全部归 E2，不在本计划。**

## 范围外（承接表——写在这里让「spec 有、plan 无」永远是已知项）

| 项 | 去向 |
|---|---|
| 真机验收（09-11_3 全流程、同参比对、work/ 清点）、双向兼容、旧界面回退验收 | **E2**（问题在 E1 交付后再问用户） |
| vrender 慢放锚/跟球链非 0 偏移实测 → 语义决策 | **E2**（决策归用户） |
| fps 后端探测下发（R35）、生成完成预览位自动播放 | **E2 条件项**（手感/顺带看） |
| wall 的 `assign/unassign` 不经 `wallSeq` 守卫 | 用户已裁 **chore 批** |
| 产品侧锁不对称（`/existing` 不取 `video_lock`、`os.replace` 不重试 → 真用户概率性 500） | 用户已裁**另开后端任务** |
| `bbh/` 其余一切 | E1 只许 offset 收口一个任务碰 `bbh/web/`（见全局约束） |

## 全局约束（沿袭 + 本次新增）

沿袭全部红线：分支纪律（只进 `feature/vertical-highlight`，不 merge/push）；用户 7860 实例不杀不占不进任何名单；起服务一律 `uvicorn.run(create_app(), host, port)` + 临时 cwd + 空闲端口，**绝不** `python -m bbh.web`；仓库根 6 档（`calibration*.json/png`、`tags.json`、`ui_prefs.json`）绝不能动；`cd frontend && npm run build` 先于任何 e2e；Python 一律 `./.venv/Scripts/python.exe -X utf8`；实现者只 `git add` brief 点名的路径、永不 `add -A`；实现者不派子代理；产物 `bbh/static/**` 只由 build 重建；改一处只跑相关用例。

**新增**：Plan D 的「`bbh/` 一行不改」在 E1 **解除为最小改动**——仅 offset `_finite` 收口任务可碰 `bbh/web/api_events.py` / `bbh/web/api_marks.py`（及必要时把 `_finite` 提为两文件共享的 helper），其余任务零后端改动。

---

## G1 立秤（项数从人肉数变成门守的量）

### G1a `count-spec-items.py` 泛化

现况：`tests/e2e/count-spec-items.py` 硬编码只认剪辑台 spec（模块名与 spec 路径写死、不读 argv）⇒ 墙 43、标定 81 每次只能靠一次性内联驱动复现。

目标：加可选参数（如 `--spec bay|wall|calib`，缺省 `bay` 行为不变），三页都能数。配方（Plan D R15b，实测踩出来的）必须落实：① `python <file>` 下 `sys.path[0]` 是脚本目录 ⇒ 两行 `sys.path.insert`（仓库根 + `tests/e2e`）必须有；② stdout 与 stderr **都打**（spec 的 `ok` 抛错走 stderr，只打 stdout 失败时是空白+rc=1）；③ 按 spec 选对应夹具组合——标定要 `_make_video` 造真视频、墙要在剪辑台夹具上补 `shooters.json`/traces/shots；复用各 wrapper 已有的 helper，不另写第二份。

### G1b 三个 pytest wrapper 钉死期望项数

现况：`test_edit_bay_e2e.py:219`、`test_wall_e2e.py:101`、`test_calib_e2e.py:95` 都只断 `returncode == 0` + `"✅ 全过" in stdout` ⇒ 项数掉了照样绿。

目标：三个 wrapper 各加常量（如 `EXPECTED_ITEMS = 191/43/81`），用 `re.search(r"✅ 全过 (\d+) 项", r.stdout)` 解析实际 N，`N != EXPECTED` ⇒ assert 红，红文案带**实际 N 与期望 N**。E1 内每个改 spec 项数的任务**同任务同步改钉数**（不允许中间态：改了 spec 没改钉 = 红，正是门的目的）。

交付判据（突变自证）：把任一 wrapper 的期望值改错一位 ⇒ 该 wrapper 红，红文案含两个 N。

---

## G2 存量硬化（8 项）

### G2a calib 等待预算两处换牙（不计项）

`calib.spec.mjs:69`（T3 miss toast）与 `:296`（T6 完成 toast）现为裸 `.first().waitFor()`（Playwright 默认 30s、超时报 `TimeoutError` 而非本 spec 文案）。等长替换成 `waitFor(async () => (await X.count()) > 0, "<沿用原判据文案>")`——**不计项**（等长替换，判据语义不变）。

### G2b 四处 `waitForResponse` 定案：不换、写明（E23 同法）

`calib.spec.mjs` 的 S/V/build/del 四处 `page.waitForResponse(...)` **保持 30s**，就地注释写明：「30s = 文档化的第二预算（`waitForResponse` 没有 `ms` 参数；等不到响应会挂到测试超时=可读的红）。统一成 10s 会让『服务端 30s 才回』从超时变假绿——见已知项 16」。`:36` 挂载屏障继续不动。**E2 真机跑过后复访**（承接表挂 E2）。

### G2c ⑮(4) toast 守卫换判别前缀（项数不变）

现况：`filter({ hasText: "回到第①步" })` 撞前一条 toast「已取消补段，回到第①步」的相同子串（3s 内仍在屏）⇒ 守卫可在点击前为真。实现者先 grep 盘上**真实** toast 文案，选只属于新 toast 的更长判别串（或先等旧 toast `count() === 0`），突变自证：让新 toast 不出现 ⇒ 守卫红且红在 spec 自己的文案。

### G2d wall G 段 warning 断言收紧（项数不变或 +0）

现况：`wall.spec.mjs:150-152` 断 `.el-message--warning` 计数 ≥1 + last 含「护栏」——F 段遗留的 warning 可预先满足计数。收紧：G 段归人**前**快照 warning 计数，断言后**恰 +1**（比断 G 特有邻对稳，不与具体时刻耦合）。项数不变（收紧发生在 `waitFor` 的条件里，`ok/eq` 条数不动）。

### G2e `VideoCamera` 图标判据（+1 项）

calib spec 加一条：`#app .topbar .el-input__prefix svg` 计数 ≥1（捉「图标整个没渲染」；「渲染成别的图标」捉不到，注释里如实写明这个边界）。

### G2f `natural={0,0}` 窗口定性（spike 内嵌，双出口）

理论窗口：`@load` 前 `natural={0,0}`，点帧容器会按 CSS 盒换算。定性法：图未出现前强制点击（Playwright 先 locate 再触发，不等 `frame-loaded`）。**可稳定复现 ⇒ 修**（load 前禁点，或换算保底修正）；**不可复现 ⇒ 记已知项收档**——不许「顺手加固」（不可复现的加固没有牙，是给未来人埋假判据）。

### G2g 非有限 `offset` `_finite` 收口（后端，唯一可碰 `bbh/web/` 的任务）

现况两处消费点，同形 `body.offset or 0.0`：`api_events.py:131`（`DELETE /events`）、`api_marks.py:47`（裁决端点）。代价已实测（edit-msg 终审 M-3）：**±inf** → 500（`int(inf//60)` 抛 `ValueError`，无半写状态）；**NaN** → 200 + **成功文案里藏错时刻**（`max(0.0, t+nan)=0.0` ⇒ toast 显示 `00:00.0`）——比 500 隐蔽。

修法：语义按 `api_gen.py:125` 的 `_finite` 冻结口径——**非有限（NaN/±inf/字符串/None）一律当没传、保留原值**。共享形状取**最小侵入**：首选 `from .api_gen import _finite`（零搬运、`api_gen.py` 一行不动）；仅当仓库惯例不允许 api 模块互 import 时，才提为独立共享模块。UI 产不出这些值（`parseT` 失败 → NaN → `|| 0`），这是防线不是修用户可见 bug。

pytest 牙（`tests/test_web_events.py` / `test_web_marks.py` 各四种输入 × 两端点形状）：NaN → 200 + msg 显示**原**时刻；±inf → 当没传；字符串 → 当没传；None → 当没传。**重点断 NaN 那半的 msg**（它是唯一会绿的错）。

### G2h 行号引用清扫（spec 归实现者，计划正文归控制者）

spec 文件里**指向本文件**的行号引用改按符号指认（已核实一处：`edit_bay.spec.mjs:587` 的 `（spec:87 那条注释…）`——`:87` 今天已是别的内容）。口径：跨文件引用不随本文件漂移、**例外保留**；标注「日期+基线」的实盘快照档除外。四份**计划正文**里的行号清扫是 docs 活，归控制者（不派实现者）。

---

## G3 增量覆盖（2 任务）

### G3a 显示面审计（「屏上时刻同源」判据升级）

方法论教训（edit-msg 终审核心洞察）：同源不变式的判据是**调用点**（`dispT(`/`tsec(`），而调用点判据**看不见不叫 `.t` 的时刻**——`releaseT` 既不在调用点里也不匹配 `.t` 正则，于是整支（含终审者）在同一条判据上漏掉同族面。**换判据：按显示面枚举**——凡渲染「秒/时刻」的模板节点都是面，逐面核它的时间源。

做法：grep `dispT\|tsec\|toFixed\|fmtT\|mm:ss` 圈范围 + **人工过三页全部模板**（grep 圈不住 `releaseT.toFixed(2)+"s"` 这类）。产出**审计表**：每面一行（页面/组件/DOM 面/数据源/同源与否/定性）。出口三分：① 明显同源破坏且**无语义争议** ⇒ 当场修；② 有语义争议（`releaseT` 吃不吃偏移、墙 caption/卡片 label 是否吃偏移）⇒ **决策清单交用户裁，不替用户定**；③ 已同源 ⇒ 表里记 ✓ 收档。审计表进计划正文存档。

### G3b 跨页 e2e（新 `tests/e2e/crosspage.spec.mjs` + `test_crosspage_e2e.py`）

夹具：组合三套既有 helper——剪辑台 12 行 + 墙 `shooters.json`/traces/shots + 标定 `_make_video` 真视频（流⑤要真抽帧）。**复用 wrapper 的 helper，不写第二份**（R15b 同旨）。

五条流（全部真 `<a>` 整页导航，无 `page.goto` 作弊——导航本身就是被测物）：

1. **tabbar 矩阵**：三页各开一次；tabbar 存在、当前页高亮、另两链真导航到对页（`shared/tabbar.js` 的 `<a href>`）。
2. **会话共享**：edit 填路径载入 12 行 → `<a>` 去 wall → 同视频卡片直达；去 calib → 同视频（三页共享同一后端会话）。
3. **edit→wall 深链**：依据卡的「在出手人墙中查看 →」（`BasisPanel.vue:151`，`href="/wall.html"`）。
4. **归人回写**：wall 托盘归人一球 → wrapper 断 `manual_groups.json` 落盘；回 edit 页断状态面反映。**注意**：edit 页哪个 DOM 面反映归人须在计划期核清数据流后钉死；若 edit 页不读 `manual_groups.json`，就**如实**降为「wrapper 文件断言 + wall 侧状态断言」，不造假联动断言。
5. **calib 联动**：calib 页抽帧→点筐口→生成标定 → 已标定标签 + wrapper 断**同一 work 目录**里 `calibration.json` 落盘；回 edit 页 12 行不受扰。

wrapper 从第一天就钉项数（G1b 同形）。spec 条款「顶栏 tab 三页共用条」到此归宿。

---

## 测试与验收

- 全量：`635 passed + 新增 pytest 数 / 1 skipped`（G2g 新增用例计入；收尾跑一次全量）。
- selftest 42 不降；三页冒烟绿（覆盖 `BBH_BASE` + 临时 cwd，E30 口径）。
- 三处项数门突变自证真红（改错期望值 → wrapper 红，文案含两个 N）。
- G2c/G2d/G2e 的每颗新牙做突变自证（红在 spec 自己的文案）。
- 净树：只剩 `?? ui_prefs.json`；仓库根 6 档 mtime 不变。
- 交付态表格回填计划正文（含项数链新值：191→?、43→?、81→?——以实现者独立计数为准）。

## 风险与回退

| 风险 | 缓解 |
|---|---|
| 项数门钉死后，后续任务忘改钉数 ⇒ 中间态红 | 这是**门的目的**不是风险；SDD 每任务的验收步骤里显式含「改钉数」 |
| 跨页夹具组合三套 helper 出缝隙（如墙夹具没造真视频而流⑤需要） | G3b 任务第一步就是组合核验；核不拢就地补，不回头改别的任务 |
| 显示面审计「当场修」越界改了语义 | 三分出口写死：语义争议一律进决策清单；修的判据=「无语义争议的同源破坏」 |
| `_finite` 提共享改了 `api_gen` 既有行为 | 语义逐字冻结（docstring 原文就是口径）；`test_web_gen` 既有用例全绿为回归门 |
| 行号清扫误伤「实盘快照」类合法行号 | 口径写死：只清**指向本文件**的自引用；快照档（标注日期+基线）除外 |
