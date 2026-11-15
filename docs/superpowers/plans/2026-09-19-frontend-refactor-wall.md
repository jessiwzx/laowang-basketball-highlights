# 出手人墙页（Plan C）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Gradio 的「🧍 出手人」墙页平移成 Vue3 + Element Plus 的 `wall.html` 独立页：左墙大卡片（≥200px）+ 多选托盘 + 拖到人名组归人 + 已归拖回未归 + 按人折叠 + 回扫 job——**前端单侧**，后端五个端点已在 Plan A/B 交付并被 `tests/test_web_wall.py` 钉死，本计划零后端改动。

**Architecture:** 每页一个独立 Vue app（无 router/Pinia）。墙页 = `WallBay.vue` 编排（顶栏/左墙/会话门）+ `PersonGroups.vue`（右列分组与放置目标）+ `WallTray.vue`（底部托盘表单）+ `wallkit.js`（纯函数层）。数据流与剪辑台同口径：墙的全部数据来自 `GET /api/wall` 的整份回包，写操作（assign/unassign）的回包就是同一份墙状态、就地替换；服务端 `msg` 是唯一文案源。

**Tech Stack:** Vue 3.4（`template:` 字符串 + 运行时编译器，vite alias 钉死）、Element Plus 2.8 全量引入、Vite 多页构建（产物入库 `bbh/static/`）、Playwright e2e（Node，`tests/e2e/`）。

**Spec:** `docs/superpowers/specs/2026-09-16-frontend-refactor-design.md`（三页平移总 spec；本计划实现其中痛点表第 3 行 + §API 出手人墙五端点的前端侧）。原型交互与视觉的冻结基准：`前端原型/出手人墙.html`（在盘、不入库，实现者可直接读）。

## Global Constraints

**spec 级（逐字从 spec 抄）：**

- `work/` 下所有 sidecar 结构、字段名、文件格式一字不改；**不新增任何 sidecar 字段**；写侧复用现有 `_save_*`（本计划不碰后端，自动满足）。
- 组件内的判定文案与字段口径**必须与原型一致**；`npm run build` 产物进 git；端到端脚本入库 `tests/e2e/`。
- Vite 多页构建三入口，tab 用普通 `<a href>`；不引 vue-router、不引 Pinia；依赖只有 `vue` + `element-plus` + `vite`（本计划零新增依赖）。

**仓库纪律（Plan A/B 沿用，违反即停）：**

- **红线**：仓库根 5 档 `calibration.json` / `calibration_ref.png` / `calibration_seg2.json` / `calibration_ref_seg2.png` / `tags.json` 绝不能动；用户 `work/` 只读。
- **绝不跑 `python -m bbh.web`**（它的 `__main__` 会把仓库根标定档搬进 `work/`）；起服务一律 `uvicorn.run(create_app(), host, port)` + 临时 cwd + 空闲端口；**绝不碰 7860**（用户自己的实例不杀、不占、不出现在任何探活名单里）；停后台进程按 task id，绝不用宽泛 `taskkill //IM`。
- `cd frontend && npm run build` **必须先于**任何 e2e/冒烟；产物 `bbh/static/**` 由构建生成，别手改；本任务改了前端就必须把重建后的 `bbh/static` 一起提交（产物入库，运行时不装 Node）。
- 实现者只 `git add` 本任务 Files 点名的具体路径，**永不** `git add -A` / `git add .`；commit 信息中文长信息；不擅自 merge/push（分支 `feature/vertical-highlight`）。
- 改一处只跑相关用例，不跑全量（全量只在 Task 8 收尾跑一次）；实现者不许派子代理；绝不并行派两个实现者。

## 既有契约（Plan C 的地基——实现者必读，全部已在盘上）

**后端五端点（零改动，只消费）：** `bbh/web/api_wall.py` + `bbh/web/api_gen.py`

| 端点 | 请求 | 回包（关键字段逐字） |
|---|---|---|
| `GET /api/wall` | `?sort=时间\|外观相似\|出手点`（中文标签；未知值服务端退化成「时间」，不报错） | `{unassigned:[{key,caption,thumb}], assigned:[{person,items:[…]}], people, manual_groups, personChoices, sorts:["时间","外观相似","出手点"], sort, minGapS, msg}`。`sorts`/`sort` 是中文标签；`personChoices` = 标签库 ∪ 本视频已归人（唯一源，前端**不自己 union**）；无会话时 400「请先填视频路径（本机文件全路径）」 |
| `POST /api/wall/assign` | `{person, keys:[], sort}` | 同上整份墙状态（**按请求里的 sort 重建**），`msg`=`✅ n 球 → 人名`＋护栏段 `\n⚠️ 护栏：a↔b 仅 X.Xs … < 2.5s，物理上不太可能同人，请核对`（警示不阻断）。空 keys/空人名/墙上没有的键 → 400 服务端文案 |
| `POST /api/wall/unassign` | `{keys:[], sort}` | 同上；`msg`=`↩️ n 球已移出归人（已打标签未动，清标签走标签下拉）` |
| `GET /api/wall/guard` | `?person=` | `{person, n, keys, violations:[{a,b,gapS}], msg}`——无违例时 `msg:""`、`violations:[]`；只读 |
| `POST /api/wall/scan` | 无体 | `{jobId}`；前置不满足同步 400（`本视频还没标定——…` / `尚无检测结果——先点「▶ 开始检测」`）；job kind=`wall_scan` |

**caption 的冻结口径**（`bbh/app.py` 的 `wall_payload` / `assigned_wall_payload`，别改它也别绕开它）：未归球 `caption = key`，已归球 `caption = 人名·key`；`spot == "bad"` 的球在 caption 末尾追加 `·⚠定位失效`（逐字）。**key 恒干净**（第三位）——点选/归人只认 key，⚠ 只是显示层。key 形如 `00:12.1`（`mm:ss.s` 原始检测时刻，跨页与 sidecar 同源）。

**job 形状**（`bbh/web/jobs.py`，轮询 `GET /api/jobs/{id}`）：`{id, kind, state: running|done|error, pct, msg, found, error, done}`。⚠ `wall_scan` 的 worker 只转发生成器文案、**从不报 pct**（`_drive` 只 `report(msg=…)`）——进度显示用 `msg`（`⏳ 识别出手人（k/n）：key → 🟢 轨迹完整` 这类），别用 pct。终态后 `GET /api/jobs/{id}/result`：wall_scan 的 result 是**平铺了 `state_payload()` 的 dict**（`msg` 在里面），**墙数据不在里面**——完成后要重新 `GET /api/wall`。

**共享前端模块（Plan A/B 已交付，直接 import，别复制）：**

- `shared/api.js`：`api.get/post/patch/del`——非 2xx 抛 `Error(data.error)`，调用方 `catch (e) { ElMessage.error(e.message) }`；`qs(params)` 拼 query（空值跳过）。
- `shared/jobs.js`：`startJob(url, body) → jobId`；`followJob(jobId, onTick)` 跟到终态取 result（error 态抛）；`runJob` 用不上（墙页要记住 jobId）。
- `shared/jobmemory.js`：`createJobMemory()` → `{remember, load, clear}`，localStorage `bbh.runningJob` 单槽。
- `shared/session.js`：`store.state / on(f) / off(f) / refresh()`。
- `shared/tabbar.js`：`mountTabbar(current, hint)`——本页 `mountTabbar("wall.html", "")`。
- `shared/theme.css`：只有 `[v-cloak]` + `.tabbar`——墙页样式全部写进组件 `<style scoped>`。

**剪辑台已交付的墙相关件（别动，知道即可）：** `EditBay.vue` 已有 `startScan`（`longJob("wall_scan", "/api/wall/scan")` + jobmemory 记录）——剪辑台的「识别出手人」与墙页的扫描按钮是**同一个后端 job 的两处入口**（原型同款）；jobmemory 单槽是既有约定（单用户工具）。`frontend/src/wall/main.js` 现在是 Plan A 骨架（本计划重写）；`frontend/wall.html` 壳、`vite.config.js` 的 `wall` 入口都已就位。

**Python 侧既有测试（全绿基线，不许动）：** `tests/test_web_wall.py`（20 例：两墙/排序回显/护栏/键求交/畸形体 400）、`tests/test_web_jobs_api.py`、`tests/e2e/test_edit_bay_e2e.py`（夹具与启动器，墙 e2e 直接复用它的 `_fixture`/`_free_port`/`_wait_up`/`_untouched`）。

## 原型对照（文案与视觉的冻结基准——浓缩自 `前端原型/出手人墙.html`）

**逐字文案（本计划照抄；服务端下发的 msg 除外——那是服务端的）：**

| 位置 | 逐字 |
|---|---|
| 顶栏计数 tag | `未归 {{n}}` / `已归 {{n}}`（warning/success 两枚 dark tag；另有一枚 plain tag 显示视频名） |
| 扫描按钮（空闲） | `🧍 识别出手人（回扫溯源）` |
| 排序 radio | 前置 `排序`，三枚 `el-radio-button`：`时间` / `外观相似` / `出手点` |
| 左墙头 | `<b>未归人 {{n}} 球</b>` ＋ `缩略图 = 出手前后动图 · 点击选中（可多选）· 可整卡拖到右侧人名 · 右侧已归的球也能拖回这里` ＋ `全选` / `清空`（text small） |
| 卡片 caption 右尾 | `拖着走 ↗`（#6f7278） |
| ⚠ 角标 | `⚠ 定位失效`（#f2c078，右上角） |
| 托盘标题 | `已选 {{n}} 球：` |
| 托盘空态 | `（点左侧卡片选择）` |
| 人名下拉 | placeholder `归人（选/输人名）`（filterable allow-create default-first-option，宽 150px） |
| 托盘按钮 | `✅ 归人`（primary）/ `↩ 移出` / `清空`（text） |
| 组头 | `.avatar` 圆标取人名首字 ＋ 人名 ＋ `{{n}} 球`（success plain small tag） |
| 右列空态 | `（还没有人——从左侧选球后用底部托盘「归人」，或直接拖进来）` |
| 缩略图 title | `拖回左侧未归墙 = 从「人名」移出` |
| 新建放置区 | `＋ 拖到这里 = 新建人名`（拖入时追加 `（松手创建）`） |
| 新建弹窗 | `ElMessageBox.prompt("新人名（入全局标签库，全部视频共用）", "拖拽新建", {confirmButtonText:"归人"})` |

**视觉常量（照抄进 scoped style）：**

- 布局：`#app` 高 `calc(100vh - 40px)` 的 flex 列；topbar `padding:10px 18px; gap:14px; background:#1a1a1a; border-bottom:1px solid #333` **＋ `flex-wrap:wrap`**（墙页特有）；`.main{flex:1;display:flex;min-height:0}`；左列 `width:56%; background:#181818; border-right:1px solid #333`；右列 `flex:1; background:#161616`；两列 `overflow-y:auto; padding:14px`。
- 卡片网格：`repeat(auto-fill, minmax(200px, 1fr)); gap:12px`（**最小宽 200px 是 spec 验收点**）；卡 `border:2px solid #3a3a3a; border-radius:10px; background:#222`，hover `#6b6b6b`；选中 `.sel{border-color:var(--el-color-primary); box-shadow:0 0 0 3px rgba(232,98,45,.25)}`；图 `width:100%; aspect-ratio:1.28; object-fit:cover; background:#000`；`.selmark` 24×24 圆（primary 底白勾）；`.warn` 右上 `rgba(0,0,0,.6)` 底、11px、#f2c078；`.cap` 12px #cfd3dc 两端对齐。
- 组：`.avatar` 34×34 圆 primary 底、15px/700；`.thumbs{flex-wrap:wrap; gap:8px; padding:4px 2px 8px}`；`.thumb{width:96px; border:2px solid #3a3a3a; border-radius:8px}`，`.sel` 边框变 primary；时间条 `.t` 底部 `rgba(0,0,0,.6)`、10.5px #ddd。
- 托盘：`background:#1f1a16; border-top:1px solid #5c3d22; padding:10px 18px; gap:12px; min-height:58px`；标题 13px nowrap；chips `gap:6px; flex:1; flex-wrap:wrap`。
- 拖放高亮：组头 `.droptarget{outline:2px dashed var(--el-color-primary); outline-offset:3px; border-radius:10px}`；左墙 `.left.droptarget{outline-offset:-6px; background:#1d1a17}`；新建区 `.drophint{border:2px dashed #4a4a4a; border-radius:10px; padding:10px; text-align:center; color:#6f7278; font-size:12.5px}`，拖入时边框/文字变 primary。
- 文字色档：#e5eaf3 / #cfd3dc / #8b8e95 / #6f7278 / #f2c078(warn)；字体栈 `system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif`。

**原型里明确不搬的（mock 专属）：** hint「数据为假，交互为真…」、`功能对照`/`← 原型总览` 链接、`.protobar` 死代码、`.colorhint` 颜色相近条（见 R3）、排序 radio 的内部值 `time|look|spot`（见 R1）、托盘进度自算的 `扫描中 P%（k/N）`（见 R5）、新建区的第二条「💡 也可以丢到上面这行新建」双入口（只留虚线块一个）。

## 计划期裁定（R1–R17，实现与复审都以此为准）

| # | 裁定 | 理由（含错判代价） |
|---|---|---|
| R1 | 排序 radio 的 value = **服务端中文标签**（`时间/外观相似/出手点`），从回包 `sorts` 渲染、`sort` 回写当前值 | 后端 `_WALL_MODES` 键即中文标签且写端点按它回显（`tests/test_web_wall.py::test_post_echoes_sort…` 钉死）；spec 草案的 `sort=time\|appearance\|spot` 英文值在交付后端时已定形为中文。前端自造映射表 = 两份事实迟早漂移 |
| R2 | ⚠ 角标从前端拆解 caption 得到：`bad = caption 含「·⚠定位失效」`、`label = 去掉该后缀的 caption`（`wallkit.splitCaption`） | 后端 caption 冻结、key 恒干净；不改后端就没有独立的 bad 字段。判据钉死全串 `·⚠定位失效`（不是 `includes("⚠")`），别的 ⚠ 文案将来进了 caption 也不该点亮角标 |
| R3 | **颜色相近提示（colorhint）不入本计划** | spec 痛点表/API 表都没有它；后端五端点里没有 neighbors 端点（Gradio 的 `render_manual_payload` 是内嵌 HTML，不在新契约里）；且归人线终局记录颜色提示恒 low（2026-08-31 定案）。要补得先加只读端点——本计划零后端 |
| R4 | 护栏双路：① assign 回包 msg 按 `\n` 拆行，`⚠️` 行弹 `ElMessage.warning`、其余弹 success（文案逐字来自服务端）；② 托盘人名下拉换人时 `GET /api/wall/guard`，托盘上方一行读数 `🛡 人名：已归 n 球`＋违例时附服务端 msg | spec API 表第 5 行的语义就是「归人前护栏」；原型只有事后警示（mock 自算），新页按 spec 接上事前读数。警示不阻断（服务端行为，前端只展示） |
| R5 | 扫描进度显示**服务端 job.msg**（`⏳ 识别出手人（k/n）：…`），按钮 `:loading`；不用 pct | wall_scan 的 worker 从不报 pct（`_drive` 只转发 msg），原型那行 `扫描中 P%（k/N）` 是 mock 自算。显示 0% 直到 100% 是假进度 |
| R6 | 扫描完成后墙刷新 = **重新 `GET /api/wall`**（wall_scan 的 result 只平铺 `state_payload`，墙数据不在里面）；完成 toast 用 `res.msg`（服务端汇总），不自编 | 与剪辑台 `startScan` 的 `onState(res)` 不同路——那边消费的是 state_payload。自编「✅ 回扫完成」= 第二个文案来源 |
| R7 | 拖拽 = 原生 HTML5 DnD，拖的载荷是**状态变量 `dragKey`**（不用 `dataTransfer` 传数据）；高亮哨兵 `dragOver ∈ {null, "__un__", "__new__", 组名}` | 原型同款；顺带让 e2e 能用合成 `DragEvent`（dragstart 在卡片、drop 在目标）确定性驱动整条链 |
| R8 | 检测期间墙页**不加写锁**（assign/unassign 照常可用） | 旧界面就没禁墙（墙组件不在检测 Timer 的 outputs 里）；服务端 assign/unassign 不受检测锁保护是既有行为。前端自加门 = 与旧界面行为分叉 |
| R9 | 墙页**没有路径输入框**（原型同款）：无会话时整页主区显示空态引导去剪辑台（含 `<a href="/edit.html">`），不发 `/api/wall` | 会话是服务端全局态，用户在剪辑台载入视频后跳过来。墙页自造第二个路径入口 = 两个会话入口两种行为 |
| R10 | `frontend/smoke.mjs` 的墙页地标从「#app h2」换成「#app .topbar 第二枚 tag 以 `未归` 开头」 | 墙页 SFC 化后没有 h2；保持「app.mount 真跑成功才有地标」的约束不变（Plan B 同款教训：tabbar 由 `document.body.prepend` 提供、与 Vue 无关，不能当地标） |
| R11 | e2e 的拖拽用 `page.evaluate` 派发合成 `DragEvent`（带 `new DataTransfer()`），不用 `locator.dragTo` | Playwright 鼠标拖对 HTML5 DnD 在嵌套滚动容器内不稳；R7 的状态变量设计让合成事件完全够用 |
| R12 | 托盘 chips 的标签：未归球 = 拆 ⚠ 后的 key，已归球 = 拆 ⚠ 后的 `人名·key`——都来自服务端 caption（`chipLabel` 跨两墙找项） | 原型 `labelOf` 的「人名·时刻」格式就是 caption 同源；自拼 = 第二份格式化 |
| R13 | 新建人名两条路都落到 `assign(新名, keys)`：拖到新建区 → `ElMessageBox.prompt`（文案逐字照原型）；托盘下拉 `allow-create` 直接输入。**前端不写标签库**（新名进 `personChoices` 是服务端 `_tag_choices` 的职责） | 后端已保证：归人时新名不进全局库但进下拉候选。前端若自维护名单 = 两页候选集分叉（api_wall.py 注释原话） |
| R14 | `全选` = 当前未归墙可见键全选；`清空` = `selKeys=[]`（原型同款，不加 disabled） | —— |
| R15 | 载入/写回包后 `openGroups` 只**增**新组名、保留用户手动收起的组（删掉已消失的组名）；首次载入全部展开 | spec「按人折叠」：折叠是用户主动动作不是默认态；落盘见 R16 |
| R16 | 折叠状态不落盘（会话内状态） | 落盘需新端点（ui_prefs.json 只有 lastVideo 且无写端点），超出五端点契约与「零后端」范围；spec 的「UI 偏好写独立文件」是隔离原则不是本页必做项。已知项记档 |
| R17 | guard 读数失败（网络/500）静默置空，不挡归人 | 护栏是辅助信息，真护栏在服务端 assign 里；辅助读数不该变成新的故障点 |

---

### Task 1: `wallkit.js` 纯函数层

**Files:**
- Create: `frontend/src/wall/wallkit.js`
- Modify: `frontend/selftest.mjs`（文末追加一节）

**Interfaces:**
- Produces: `splitCaption(caption) → {label, bad}`、`openAll(groups) → string[]`、`itemByKey(lists, key) → item|null`、常量 `BAD_SUFFIX = "·⚠定位失效"` / `DROP_UN = "__un__"` / `DROP_NEW = "__new__"`。组件侧（WallBay）实际 import `splitCaption/itemByKey/DROP_UN/DROP_NEW`；`BAD_SUFFIX` 由 wallkit 内部与 selftest 消费、`openAll` 仅 selftest 消费——其「首次载入全展开」职责由 applyWall 的 prev-Set 差集在首载（prev 为空集）等价达成（终审 R-12 勘误）。

- [ ] **Step 1: 先写失败的测试**——`frontend/selftest.mjs` 末尾追加：

```js
// ---- wall/wallkit.js ----
const { splitCaption, openAll, itemByKey, DROP_UN, DROP_NEW, BAD_SUFFIX } =
  await import("./src/wall/wallkit.js");

test("splitCaption：未归球 caption=key 原样、无角标", () => {
  assert.deepEqual(splitCaption("00:12.1"), { label: "00:12.1", bad: false });
});

test("splitCaption：spot=bad 的 ⚠ 后缀剥成角标，label 不带尾巴", () => {
  assert.deepEqual(splitCaption("00:12.1" + BAD_SUFFIX),
                   { label: "00:12.1", bad: true });
});

test("splitCaption：已归球 caption=人名·key（bad 同样追加在后）", () => {
  assert.deepEqual(splitCaption("老杨·00:12.1"),
                   { label: "老杨·00:12.1", bad: false });
  assert.deepEqual(splitCaption("老杨·00:12.1" + BAD_SUFFIX),
                   { label: "老杨·00:12.1", bad: true });
});

test("splitCaption：空/畸形 caption 不抛，label 落空串", () => {
  assert.deepEqual(splitCaption(undefined), { label: "", bad: false });
});

test("拖拽哨兵互不相等、且以双下划线开头（不会撞真人名）", () => {
  assert.notEqual(DROP_UN, DROP_NEW);
  assert.ok(DROP_UN.startsWith("__") && DROP_NEW.startsWith("__"));
});

test("openAll 全展开；itemByKey 跨未归/已归找项、找不到为 null", () => {
  const w = { unassigned: [{ key: "a", caption: "a" }],
              assigned: [{ person: "p", items: [{ key: "b", caption: "p·b" }] }] };
  assert.deepEqual(openAll(w.assigned), ["p"]);
  assert.deepEqual(openAll([]), []);
  assert.equal(
    itemByKey([w.unassigned, ...w.assigned.map((g) => g.items)], "b").caption,
    "p·b");
  assert.equal(itemByKey([w.unassigned], "zzz"), null);
});
```

- [ ] **Step 2: 跑它确认失败**

Run: `cd frontend && node selftest.mjs`
Expected: FAIL——`Cannot find module './src/wall/wallkit.js'`（既有 28 例照常通过）。

- [ ] **Step 3: 实现 `frontend/src/wall/wallkit.js`**

```js
// 出手人墙的纯函数层：caption 拆解、拖拽哨兵、默认展开组。
// 渲染与请求都不在这里——这层只放「不经浏览器、不经服务端」就能测的逻辑
// （frontend/selftest.mjs 直调）。
//
// 服务端 caption 的冻结口径（bbh/app.py 的 wall_payload / assigned_wall_payload）：
//   未归球 caption = key；已归球 caption = 人名·key；spot=bad 追加 BAD_SUFFIX。
// key 恒干净（回包第三位）——点选/归人只认 key，⚠ 只是显示层的角标。

export const BAD_SUFFIX = "·⚠定位失效";

// 拖拽高亮的哨兵取值（dragOver 的三族值：null=没在拖 / DROP_UN=左墙 /
// DROP_NEW=新建区 / 其余=人名组名）。用状态变量而不是 dataTransfer 传数据
// （原型同款），也顺便让 e2e 能用合成 DragEvent 驱动整条拖拽链。
export const DROP_UN = "__un__";
export const DROP_NEW = "__new__";

// caption → { label, bad }：label 是剥掉 ⚠ 后缀的显示串。
// bad 只认 BAD_SUFFIX 整串（不是 includes("⚠")）：别的 ⚠ 文案将来若进
// caption，也不该把角标点亮。
export function splitCaption(caption) {
  const s = String(caption || "");
  const i = s.indexOf(BAD_SUFFIX);
  return { label: i < 0 ? s : s.slice(0, i), bad: i >= 0 };
}

// 首次载入的默认展开：全部组展开（spec 痛点表「按人折叠」——折叠是用户
// 主动收起的动作，不是默认态）。
export function openAll(groups) {
  return (groups || []).map((g) => g.person);
}

// 已归组按 key 找项（托盘 chips 要给已归球显示「人名·key」）。
// lists 是「数组的数组」：[unassigned, ...assigned.map(g => g.items)]。
export function itemByKey(lists, key) {
  for (const items of lists || []) {
    const it = items.find((x) => x.key === key);
    if (it) return it;
  }
  return null;
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && node selftest.mjs`
Expected: `✅` 收尾、**34** 例全过（既有 28 + 新 6）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/wall/wallkit.js frontend/selftest.mjs
git commit -m "feat(wall): wallkit 纯函数层——caption 拆角标/拖拽哨兵/默认展开 + selftest 6 例"
```

---

### Task 2: 墙页骨架（`WallBay.vue` + `main.js` 重写 + smoke 地标切真页）

**Files:**
- Create: `frontend/src/wall/WallBay.vue`
- Modify: `frontend/src/wall/main.js`（整文件重写）
- Modify: `frontend/smoke.mjs`（墙页地标分支）
- Modify: `bbh/static/**`（构建产物，`npm run build` 生成）

**Interfaces:**
- Consumes: `store`（shared/session.js）、`mountTabbar`（shared/tabbar.js）。
- Produces: `WallBay.vue` 的响应式骨架——`state` / `wall` / `hasVideo` / `videoName` / `unassigned` / `assigned` / `assignedCount` 与 `.wallwrap/.topbar/.main/.left/.right/.stage-hint` 的 DOM 结构与样式。Task 3–7 都在这个文件上叠加。

- [ ] **Step 1: 写 `frontend/src/wall/WallBay.vue`**（本任务只到骨架 + 会话空态；左墙卡片/托盘/分组/拖拽/扫描在后续任务加）

```vue
<script setup>
// 出手人墙主体（spec 痛点表第 3 行）：左墙大卡片（≥200px）+ 右列按人分组 +
// 底部多选托盘。数据流与剪辑台同口径：墙的全部数据来自 GET /api/wall 的
// 整份回包，前端不算统计、不拼归人文案（服务端 msg 是唯一文案源）。
// 本任务先落骨架与会话空态；取数/卡片/托盘/分组/拖拽/回扫在后续任务接上。
import { ref, computed, onMounted } from "vue";
import { ElMessage } from "element-plus";
import { store } from "../shared/session.js";

const state = ref(null);        // 会话态（videoPath 等，来自 /api/state）
const wall = ref(null);         // /api/wall 整份回包（Task 3 起真取数）

const hasVideo = computed(() => !!state.value?.videoPath);
const videoName = computed(() => {
  const p = state.value?.videoPath || "";
  return p.split(/[\\/]/).pop() || p;
});
const unassigned = computed(() => wall.value?.unassigned || []);
const assigned = computed(() => wall.value?.assigned || []);
const assignedCount = computed(() =>
  assigned.value.reduce((n, g) => n + g.items.length, 0));

function onState(s) { state.value = s; }

onMounted(async () => {
  store.on(onState);
  try { await store.refresh(); } catch (e) { ElMessage.error(e.message); }
});
</script>

<template>
  <div class="wallwrap">
    <div class="topbar">
      <el-tag effect="plain">{{ videoName || "未载入视频" }}</el-tag>
      <el-tag type="warning" effect="dark">未归 {{ unassigned.length }}</el-tag>
      <el-tag type="success" effect="dark">已归 {{ assignedCount }}</el-tag>
      <span class="spacer"></span>
    </div>
    <div v-if="!hasVideo" class="stage-hint">
      先去「🎬 剪辑台」载入视频，再回来归人（<a href="/edit.html">去剪辑台</a>）
    </div>
    <div v-else class="main">
      <div class="left colwall"></div>
      <div class="right colwall"></div>
    </div>
  </div>
</template>

<style scoped>
.wallwrap { display: flex; flex-direction: column; height: calc(100vh - 40px); }
.topbar { flex: none; display: flex; align-items: center; flex-wrap: wrap;
          gap: 14px; padding: 10px 18px; background: #1a1a1a;
          border-bottom: 1px solid #333; }
.spacer { flex: 1; }
.stage-hint { flex: 1; display: flex; align-items: center; justify-content: center;
              color: #8d9098; font-size: 13.5px; }
.stage-hint a { color: #f48553; }
.main { flex: 1; display: flex; min-height: 0; }
.colwall { overflow-y: auto; padding: 14px; }
.left { width: 56%; border-right: 1px solid #333; background: #181818; }
.right { flex: 1; background: #161616; }
</style>
```

- [ ] **Step 2: 重写 `frontend/src/wall/main.js`**（形状照 `edit/main.js`；骨架注释一并消失——「页面主体在 Plan B 实现」是句过期的话）

```js
import { createApp } from "vue";
import ElementPlus from "element-plus";
import "element-plus/dist/index.css";
import "element-plus/theme-chalk/dark/css-vars.css";
import * as Icons from "@element-plus/icons-vue";
import "../shared/theme.css";
import { mountTabbar } from "../shared/tabbar.js";
import WallBay from "./WallBay.vue";

mountTabbar("wall.html", "");
const app = createApp(WallBay);
app.use(ElementPlus);
for (const [k, c] of Object.entries(Icons)) app.component(k, c);
app.mount("#app");
```

- [ ] **Step 3: 改 `frontend/smoke.mjs` 的三页地标表与分支**——把这段（现状）：

```js
  // 三页地标：剪辑台（Plan B）是 SFC 主体，页内没有 h2 ⇒ 地标取顶栏的
  // 路径输入框；出手人墙/标定页仍是 Plan A 的骨架（h2 标题），等 Plan C/D。
  // 两者都只有 app.mount() 真跑成功才会出现——地标换掉，这条约束不换。
  for (const [file, label, heading] of [["edit.html", "剪辑台", null],
                                        ["wall.html", "出手人墙", "出手人墙"],
                                        ["calib.html", "标定设置", "标定设置"]]) {
```

换成：

```js
  // 三页地标：剪辑台（Plan B）与出手人墙（Plan C）都是 SFC 主体、页内没有
  // h2 ⇒ 地标取各自顶栏里的特征元素；标定页仍是 Plan A 的骨架（h2 标题），
  // 等 Plan D。地标只有 app.mount() 真跑成功才会出现——地标换掉，这条约束不换。
  for (const [file, label, heading, probe] of [
        ["edit.html", "剪辑台", null, "input"],
        ["wall.html", "出手人墙", null, "tag"],
        ["calib.html", "标定设置", "标定设置", null]]) {
```

再把 `if (heading) { … } else { …input 分支… }` 的 `else` 分支（剪辑台路径输入框那段）改成三支：

```js
    } else if (probe === "input") {
      // 剪辑台：.topbar 里的路径输入框只可能由 EditBay.vue 渲染出来，
      // 同样是「app.mount 真跑成功」的判据。
      const input = page.locator("#app .topbar input");
      if (!(await input.count())) {
        throw new Error(`${file}: Vue 没挂载——#app 里没有 .topbar（app.mount 没跑成功）`);
      }
      const ph = await input.first().getAttribute("placeholder");
      if (!(ph || "").includes("填路径自动载入")) {
        throw new Error(`${file}: 剪辑台地标不对，路径输入框 placeholder=「${ph}」`);
      }
    } else {
      // 出手人墙（Plan C）：顶栏「未归 n」计数 tag 只可能由 WallBay.vue 渲染
      // （tabbar 是 body.prepend 的、与 Vue 无关，不能当地标——见文件头注释）。
      // 无会话时它也在（未归 0），所以冒烟不依赖会话态。
      const tags = page.locator("#app .topbar .el-tag");
      if (!(await tags.count())) {
        throw new Error(`${file}: Vue 没挂载——#app 里没有 .topbar .el-tag`);
      }
      const t1 = (await tags.nth(1).textContent()).trim();
      if (!t1.startsWith("未归")) {
        throw new Error(`${file}: 墙页地标不对，第二枚 tag=「${t1}」（应为 未归 n）`);
      }
    }
```

（`if (heading)` 那支——h2 判断——保持原样不动。）

- [ ] **Step 4: 构建**

Run: `cd frontend && npm run build`
Expected: `vite build` 成功（三入口，自检插件过）。

- [ ] **Step 5: 冒烟（临时服务，绝不碰 7860）**

Run（Git Bash，仓库根）:

```bash
./.venv/Scripts/python.exe -X utf8 -c "
import socket, subprocess, sys, time, urllib.request, tempfile, os
tmp = tempfile.mkdtemp(prefix='smoke_wall_')
port = socket.socket(); port.bind(('127.0.0.1', 0)); p = port.getsockname()[1]; port.close()
code = 'import uvicorn\nfrom bbh.web.app import create_app\nuvicorn.run(create_app(), host=\"127.0.0.1\", port=%d, log_level=\"warning\")' % p
env = dict(os.environ, PYTHONPATH=os.getcwd(), PYTHONIOENCODING='utf-8')
proc = subprocess.Popen([sys.executable, '-c', code], cwd=tmp, env=env,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(2.5)
try:
    for _ in range(20):
        try: urllib.request.urlopen(f'http://127.0.0.1:{p}/api/state', timeout=1); break
        except Exception: time.sleep(0.5)
    r = subprocess.run(['node', 'frontend/smoke.mjs'],
                       env=dict(os.environ, BBH_BASE=f'http://127.0.0.1:{p}',
                                PYTHONIOENCODING='utf-8'),
                       capture_output=True, text=True, encoding='utf-8', errors='replace')
    print(r.stdout, r.stderr)
finally:
    proc.terminate()
" 
```

Expected: `✅ 冒烟通过：三页可开、Vue 真挂载、tab 高亮正确、可跳转、无 JS 报错`。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/wall/WallBay.vue frontend/src/wall/main.js frontend/smoke.mjs bbh/static
git commit -m "feat(wall): 墙页骨架——顶栏计数/两栏布局/会话空态 + smoke 地标切真页"
```

---

### Task 3: 墙数据接线 + 左墙大卡片 + 墙 e2e 地基

**Files:**
- Modify: `frontend/src/wall/WallBay.vue`（取数/排序/卡片渲染）
- Create: `tests/e2e/test_wall_e2e.py`
- Create: `tests/e2e/wall.spec.mjs`
- Modify: `bbh/static/**`（构建产物）

**Interfaces:**
- Consumes: Task 1 的 `splitCaption`；五端点里的 `GET /api/wall`。
- Produces: `loadWall(sort)` / `applyWall(w)` / `onSortChange(m)` / `labelOf(it)` / `badOf(it)` / `sorts` / `sortBy` / `openGroups`（Task 4/5/7 复用）；e2e 侧 `test_wall_e2e.py::_wall_fixture`（Task 4–7 的断言都跑在它上面）与 `wall.spec.mjs` 的骨架（断言一律插在 `// —— 收尾` 那行之前）。

- [ ] **Step 1: `WallBay.vue` 的 script 增量**——import 区加：

```js
import { api, qs } from "../shared/api.js";
import { splitCaption } from "./wallkit.js";
```

状态区（`wall` 声明之后）加：

```js
const sortBy = ref("时间");           // 排序模式（值=服务端中文标签，R1）
const openGroups = ref([]);           // 展开的人名（Task 5 接 el-collapse）
let wallSeq = 0;                      // loadWall 序号：过期回包不许覆盖新请求
```

computed 区加：

```js
const sorts = computed(() => wall.value?.sorts || []);
// caption → 显示层（⚠ 角标与 label 的拆解在 wallkit，R2）
const labelOf = (it) => splitCaption(it?.caption).label;
const badOf = (it) => splitCaption(it?.caption).bad;
```

`onState` 之前加取数函数（终态版本，Task 4 的 assign/unassign 回包也走 `applyWall`）：

```js
// —— 数据载入（进页/换排序/回扫完成/写操作回包共用一套 apply）——
// 回包里的 sort 是服务端解析后的标签（未知值已退化「时间」），用它回写
// sortBy——前端不维护第二份解析（R1）。写端点的回包按请求里的 sort 重建，
// 就地替换不会把排序静默打回时间序（test_web_wall.py 钉过的坑）。
async function loadWall(sort) {
  const seq = ++wallSeq;
  try {
    const w = await api.get("/api/wall" + qs({ sort: sort ?? sortBy.value }));
    if (seq !== wallSeq) return;            // 用户已再切排序，过期回包丢弃
    applyWall(w);
  } catch (e) {
    ElMessage.error(e.message);
  }
}
function applyWall(w) {
  // 「新组」= 上一份墙里没有的组（不是「当前没展开的组」——用户手动收起的
  // 组仍在墙上，把它当新组会在每次刷墙时重开，违反 R15）
  const prev = new Set((wall.value?.assigned || []).map((g) => g.person));
  wall.value = w;
  sortBy.value = w.sort;
  // 组的展开态：新组自动展开、用户手动收起的保持、消失的组跟着删（R15）
  const names = w.assigned.map((g) => g.person);
  openGroups.value = openGroups.value.filter((n) => names.includes(n))
    .concat(names.filter((n) => !prev.has(n)));
}
function onSortChange(m) { loadWall(m); }
```

`onMounted` 改成：

```js
onMounted(async () => {
  store.on(onState);
  try {
    await store.refresh();                  // refresh() 不返回值（session.js）
    if (state.value?.videoPath) await loadWall();   // 无会话不发（R9）
  } catch (e) { ElMessage.error(e.message); }
});
```

（计划期勘误 R-2：本块初稿写 `const s = await store.refresh()`——`refresh()` 不返回值，取 `s` 恒 undefined、墙永不载入。实现者 Task 3 时已按正确形状落盘。）

- [ ] **Step 2: `WallBay.vue` 的 template 增量**——topbar 的 `<span class="spacer"></span>` 之后加排序 radio；左列 `<div class="left colwall"></div>` 换成墙头 + 网格：

```html
      <span class="spacer"></span>
      <span class="sortlabel">排序</span>
      <el-radio-group :model-value="sortBy" size="small" @change="onSortChange">
        <el-radio-button v-for="m in sorts" :key="m" :value="m">{{ m }}</el-radio-button>
      </el-radio-group>
```

```html
      <div class="left colwall">
        <div class="wallhead">
          <b>未归人 {{ unassigned.length }} 球</b>
          <span class="wh-note">缩略图 = 出手前后动图 · 点击选中（可多选）· 可整卡拖到右侧人名 · 右侧已归的球也能拖回这里</span>
        </div>
        <div class="wallgrid">
          <div v-for="it in unassigned" :key="it.key" class="card">
            <span v-if="badOf(it)" class="warn">⚠ 定位失效</span>
            <img :src="it.thumb" alt="" />
            <div class="cap"><span>{{ labelOf(it) }}</span><span class="dragtip">拖着走 ↗</span></div>
          </div>
        </div>
      </div>
```

（`全选/清空`、选中态 `.sel`/`.selmark`、`@click`、`draggable` 分别在 Task 4/6 加。）

- [ ] **Step 3: `WallBay.vue` 的 style 增量**（追加进 `<style scoped>`）：

```css
.sortlabel { font-size: 12.5px; color: #a3a6ad; }
.wallhead { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.wallhead b { font-size: 14.5px; }
.wh-note { font-size: 12px; color: #8b8e95; }
.wallgrid { display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }
.card { position: relative; border: 2px solid #3a3a3a; border-radius: 10px;
        overflow: hidden; background: #222; transition: border-color .1s; }
.card:hover { border-color: #6b6b6b; }
.card img { display: block; width: 100%; aspect-ratio: 1.28; object-fit: cover;
            background: #000; }
.warn { position: absolute; top: 8px; right: 8px; background: rgba(0, 0, 0, .6);
        border-radius: 6px; padding: 2px 8px; font-size: 11px; color: #f2c078; }
.cap { display: flex; justify-content: space-between; padding: 5px 9px;
       font-size: 12px; color: #cfd3dc; }
.dragtip { color: #6f7278; }
```

- [ ] **Step 4: 建 `tests/e2e/test_wall_e2e.py`**

```python
"""出手人墙页级 e2e：真夹具 + 真服务 + 真产物 + 真浏览器（Playwright）。

隔离与红线与 test_edit_bay_e2e.py 同一套：uvicorn cwd=临时目录 ⇒ work/、
tags.json、ui_prefs.json 全落临时目录；仓库根红线 5 档 + ui_prefs.json
开跑前拍快照、结束逐字节核对。墙夹具在剪辑台 12 球之上补 shooters.json
traces + shots/*.jpg（详见 _wall_fixture 的注释）。

Node 或 playwright 缺失 → skip 并说明（与剪辑台 e2e 同一条纪律：不假绿，
也不阻塞没有 Node 的机器）。
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_edit_bay_e2e import (NAME, NODE, PW, ROOT, _fixture, _free_port,
                               _untouched, _wait_up)

SPEC = Path(__file__).resolve().parent / "wall.spec.mjs"


def _wall_fixture(root: Path) -> None:
    """在剪辑台夹具（12 球 events + 标定 + tags.json）之上补回扫产物。

    12 球全上墙（traces ∩ thumbs 文件全给）；i=3 的球 spot="bad"（⚠ 角标）；
    release_xy 的 x 随 i 递减 ⇒ 「出手点」序与「时间」序正好相反（排序断言
    才有鉴别力）；相邻球间隔 0.4s < 2.5s ⇒ 任意相邻两球归同人必触发护栏。
    thumbs 用假 jpg 字节——墙只认文件存在性，浏览器解码失败不影响任何
    断言（图片 404/解码失败走 network，不是 pageerror）。
    """
    work = root / "work" / NAME
    (work / "shots").mkdir(exist_ok=True)
    traces = {}
    for i in range(12):
        t = 1.2 + i * 0.4                       # 与剪辑台夹具同一时刻序列
        (work / "shots" / f"{i}.jpg").write_bytes(b"\xff\xd8\xff\xe0jpg")
        traces[f"{int(t // 60):02d}:{t % 60:04.1f}"] = {
            "status": ("high", "review", "low")[i % 3],
            "release_t": t, "release_xy": [520.0 - 40.0 * i, 200.0],
            "track_n": 5, "track_span": 1.2,
            "thumbs": [f"shots/{i}.jpg"],
            "spot": "bad" if i == 3 else "ok",
        }
    (work / "shooters.json").write_text(
        json.dumps({"video": str(root / "videos" / f"{NAME}.mp4"),
                    "traces": traces}, ensure_ascii=False), encoding="utf-8")


@pytest.fixture(scope="module")
def wallbay(tmp_path_factory):
    if not NODE:
        pytest.skip("没有 node——出手人墙 e2e 需要 node + playwright（见计划「验证配方」）")
    if not PW.exists():
        pytest.skip(f"没有 {PW}——先 cd frontend && npm i -D --no-save playwright")
    root = tmp_path_factory.mktemp("wall")
    _fixture(root)
    _wall_fixture(root)
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    code = ("import uvicorn\n"
            "from bbh.web.app import create_app\n"
            f"uvicorn.run(create_app(), host='127.0.0.1', port={port}, "
            "log_level='warning')\n")
    # 红线快照与子进程输出引文件的取舍，与 test_edit_bay_e2e.bay 同款——
    # 那边的注释是权威，这里不复制第二份事实。
    before = {f: _untouched(ROOT / f) for f in (
        "tags.json", "ui_prefs.json",
        "calibration.json", "calibration_ref.png",
        "calibration_seg2.json", "calibration_ref_seg2.png")}
    log = root / "server.log"
    with open(log, "w", encoding="utf-8", errors="replace") as lf:
        proc = subprocess.Popen([sys.executable, "-c", code], cwd=root, env=env,
                                stdout=lf, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
    try:
        _wait_up(base, proc, log)
        yield {"base": base, "root": root,
               "video": str(root / "videos" / f"{NAME}.mp4")}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    for f, b in before.items():
        assert _untouched(ROOT / f) == b, (
            f"e2e 动了仓库根的 {f}（红线 5 档一个 git 副本都没有）"
            "——隔离没兜住，停下查")


def test_wall_e2e(wallbay):
    env = dict(os.environ, BBH_BASE=wallbay["base"], BBH_VIDEO=wallbay["video"],
               BBH_PLAYWRIGHT=PW.as_uri(), PYTHONIOENCODING="utf-8")
    r = subprocess.run([NODE, str(SPEC)], env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    assert r.returncode == 0, f"墙 e2e 失败：\n{r.stdout}\n{r.stderr}"
    assert "✅ 全过" in r.stdout, f"墙 e2e 没跑到收尾汇总：\n{r.stdout}\n{r.stderr}"
    # 归人的副作用真落盘（spec 里至少完成一次托盘归人）
    assert (wallbay["root"] / "work" / NAME / "manual_groups.json").exists(), \
        "归人没落盘——e2e 没验到真写"
```

- [ ] **Step 5: 建 `tests/e2e/wall.spec.mjs`**（本任务先到排序；后续任务的断言插在 `// —— 收尾` 之前）

```js
// 出手人墙页级 e2e：真服务 + 真夹具 + 真产物 + 真浏览器。
// 由 tests/e2e/test_wall_e2e.py 启动——服务跑在临时目录里（cwd=tmp），
// 归人/移出/拖拽全部只写临时 work/，仓库根红线 5 档由 launcher 拍快照核对。
//
// 环境：BBH_BASE（服务）、BBH_VIDEO（夹具视频路径）、BBH_PLAYWRIGHT
// （playwright 的绝对 file:// URL——ESM 从本文件位置向上解析、不认 NODE_PATH）
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
const wallApi = async (sort) =>
  (await fetch(`${BASE}/api/wall${sort ? "?sort=" + encodeURIComponent(sort) : ""}`)).json();
// 等 UI 跟上回包（请求是异步的，DOM 后变）
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
  // 只收 JS 异常：假 jpg 解码失败、/media 404 走 network 不是 pageerror
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));

  await page.goto(`${BASE}/wall.html`, { waitUntil: "domcontentloaded" });
  await page.locator("#app .topbar .el-tag").first().waitFor();

  // —— A. 空会话态：服务是全新的，先引导去剪辑台（R9：墙页没有路径输入）——
  await waitFor(async () => (await page.locator("#app .stage-hint").count()) === 1,
                "无会话空态出现");
  ok((await page.locator("#app .stage-hint").innerText()).includes("先去"),
     "空态文案引导去剪辑台");
  eq((await page.locator("#app .topbar .el-tag").nth(1).innerText()).trim(),
     "未归 0", "无会话时未归计数为 0（不发 /api/wall）");

  // —— B. 开会话（用户真实路径 = 在剪辑台载入后跳过来；这里直连服务端开
  //        会话，然后整页 reload 模拟跨页导航）——
  await fetch(`${BASE}/api/session`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ videoPath: VIDEO }),
  });
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator("#app .wallgrid .card").first().waitFor({ timeout: 20000 });

  // —— C. 计数与卡片（数据全部来自服务端回包，前端不算统计）——
  const w = await wallApi();
  const cards = page.locator("#app .wallgrid .card");
  eq(await cards.count(), 12, "左墙 12 卡（夹具 12 球全上墙）");
  eq((await page.locator("#app .topbar .el-tag").nth(1).innerText()).trim(),
     "未归 12", "顶栏未归计数");
  eq((await page.locator("#app .topbar .el-tag").nth(2).innerText()).trim(),
     "已归 0", "顶栏已归计数");
  ok((await page.locator("#app .wallgrid .card .cap span").first().innerText())
     .includes("00:01.2"), "首卡=时间序第一球（label=拆 ⚠ 后的 caption）");
  eq(await page.locator("#app .wallgrid .card .warn").count(), 1,
     "spot=bad 一张 ⚠ 角标");
  ok((await page.locator("#app .wallgrid .card .warn").innerText())
     .includes("⚠ 定位失效"), "角标文案逐字");
  const src = await page.locator("#app .wallgrid .card img").first()
    .getAttribute("src");
  ok(src && src.startsWith("/media/work?path="), `缩略图走 /media/work（${src}）`);
  eq(await page.locator("#app .wallgrid .card.sel").count(), 0, "初始无选中");

  // —— D. 排序三态：值=服务端中文标签（R1）；夹具让两种序相反才有鉴别力 ——
  const radios = page.locator("#app .topbar .el-radio-button");
  eq(await radios.count(), 3, "三个排序档（来自回包 sorts）");
  eq((await radios.nth(0).innerText()).trim(), "时间", "档名·时间");
  eq((await radios.nth(1).innerText()).trim(), "外观相似", "档名·外观相似");
  eq((await radios.nth(2).innerText()).trim(), "出手点", "档名·出手点");
  await radios.nth(2).click();
  await waitFor(async () => (await page.locator("#app .wallgrid .card .cap span")
    .first().innerText()).includes("00:05.6"),
    "切「出手点」后首卡=最晚球（夹具 x 递减 ⇒ 与时间序相反）");
  await radios.nth(0).click();
  await waitFor(async () => (await page.locator("#app .wallgrid .card .cap span")
    .first().innerText()).includes("00:01.2"), "切回「时间」首卡复原");

  // —— 收尾（后续任务把新断言插在这行之前）——
  if (errs.length) throw new Error("页面报错：" + errs.join(" | "));
  console.log(`✅ 全过 ${pass} 项`);
} finally {
  await browser.close();
}
```

（执行期偏差 R-3：launcher 断言 `manual_groups.json` 落盘，但本任务的 spec 到排序为止、没有归人路径——照抄必失败。实现者在 spec 的 D 与收尾之间加了**不计数**的 D+ 块：node 侧直连 API 真归人一球再移出还原（端到端往返真写盘），不断言、不计数、不削弱任何断言；Task 4 的 E/F/G 前置状态不受影响。）

- [ ] **Step 6: 构建并跑墙 e2e**

Run: `cd frontend && npm run build`
Run: `cd .. && ./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_wall_e2e.py -v`
Expected: `test_wall_e2e PASSED`（spec 打印 `✅ 全过 N 项`，A2+C8+D4 共 **14** 项）。

- [ ] **Step 7: Commit**

```bash
git add frontend/src/wall/WallBay.vue tests/e2e/test_wall_e2e.py tests/e2e/wall.spec.mjs bbh/static
git commit -m "feat(wall): 墙数据接线——/api/wall 载入/排序三态/大卡片渲染 + 墙 e2e 夹具"
```

---

### Task 4: 点选 + 托盘（归人/移出按钮路径）+ 护栏双路

**Files:**
- Create: `frontend/src/wall/WallTray.vue`
- Modify: `frontend/src/wall/WallBay.vue`（selKeys/assign/unassign/guard）
- Modify: `tests/e2e/wall.spec.mjs`（E/F/H 三段断言）
- Modify: `bbh/static/**`

**Interfaces:**
- Consumes: Task 3 的 `applyWall`/`unassigned`/`assigned`/`labelOf`；Task 1 的 `itemByKey`。
- Produces: `toggle(key)` / `assign(person, keys)` / `unassign(keys)` / `selKeys` / `assignName` / `guardInfo`（Task 5/6 的拖拽落点直接调 `assign`/`unassign`）；`WallTray.vue` 的 props/emits 契约（`v-model:name` + `person-change`）。

- [ ] **Step 1: 建 `frontend/src/wall/WallTray.vue`**

```vue
<script setup>
// 底部多选托盘：chips（点 × 撤选）+ 人名下拉（filterable/allow-create）+
// 归人/移出/清空。人名候选 choices 由父层从 /api/wall 回包直传——服务端
// _tag_choices（标签库 ∪ 本视频已归人）是唯一源，前端不自己 union（R13）。
const props = defineProps({
  selKeys: { type: Array, default: () => [] },
  choices: { type: Array, default: () => [] },
  chipLabel: { type: Function, required: true },
  name: { type: String, default: "" },
});
const emit = defineEmits(["toggle", "assign", "unassign", "clear",
                          "update:name", "person-change"]);
function onName(v) {
  emit("update:name", v || "");
  emit("person-change", v || "");      // 换人名 = 重取归人前护栏读数（R4）
}
</script>

<template>
  <div class="tray">
    <b class="tray-title">已选 {{ selKeys.length }} 球：</b>
    <div class="chips">
      <el-tag v-for="k in selKeys" :key="k" closable effect="dark"
              disable-transitions style="margin: 2px"
              @close="emit('toggle', k)">{{ chipLabel(k) }}</el-tag>
      <span v-if="!selKeys.length" class="tray-empty">（点左侧卡片选择）</span>
    </div>
    <el-select :model-value="name" filterable allow-create default-first-option
               placeholder="归人（选/输人名）" size="small" style="width: 150px"
               @update:model-value="onName">
      <el-option v-for="p in choices" :key="p" :label="p" :value="p" />
    </el-select>
    <el-button type="primary" size="small"
               :disabled="!selKeys.length || !name"
               @click="emit('assign')">✅ 归人</el-button>
    <el-button size="small" :disabled="!selKeys.length"
               @click="emit('unassign')">↩ 移出</el-button>
    <el-button text size="small" :disabled="!selKeys.length"
               @click="emit('clear')">清空</el-button>
  </div>
</template>

<style scoped>
.tray { flex: none; display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
        min-height: 58px; padding: 10px 18px;
        background: #1f1a16; border-top: 1px solid #5c3d22; }
.tray-title { font-size: 13px; white-space: nowrap; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; flex: 1; }
.tray-empty { color: #6f7278; font-size: 12.5px; }
</style>
```

（执行期勘误 R-6：chips 的 el-tag 加了官方 prop `disable-transitions`——EP 2.14.6 的离场动画让被撤 chip 在 DOM 滞留 ~0.3s，E 段的同步 `count()` 必撞红灯。冻结基线不覆盖进出动画维度，spec 保持逐字。）

- [ ] **Step 2: `WallBay.vue` 的 script 增量**——import 区加 `import WallTray from "./WallTray.vue";` 与 `import { itemByKey } from "./wallkit.js";`（`splitCaption` 那行合并成 `import { splitCaption, itemByKey } from "./wallkit.js";`）。状态区加：

```js
const selKeys = ref([]);              // 墙上点选（未归+已归共用；key 全场唯一）
const assignName = ref("");           // 托盘的人名下拉
const guardInfo = ref(null);          // GET /api/wall/guard 的归人前读数（R4）
```

函数区加（终态版本；Task 6 的拖拽落点直接调 `assign`/`unassign`）：

```js
// 托盘 chips：未归球=key、已归球=人名·key——都来自服务端 caption（R12）
function chipLabel(key) {
  const it = itemByKey([unassigned.value, ...assigned.value.map((g) => g.items)], key);
  return it ? labelOf(it) : key;
}

// 点选（左墙卡片与右列缩略图共用一个 toggle；Task 5 起右列也发这个）
function toggle(key) {
  const i = selKeys.value.indexOf(key);
  if (i < 0) selKeys.value.push(key);
  else selKeys.value.splice(i, 1);
}
const selectAll = () => { selKeys.value = unassigned.value.map((x) => x.key); };
const clearSel = () => { selKeys.value = []; };

// msg 是唯一文案源：按行拆，⚠️ 行（护栏）弹 warning、其余弹 success（R4）。
// 警示不阻断——归人服务端已经做了，前端只负责把两段话都给用户看。
function toastMsg(msg) {
  for (const line of String(msg || "").split("\n")) {
    const s = line.trim();
    if (!s) continue;
    if (s.includes("⚠️")) ElMessage.warning(s);
    else ElMessage.success(s);
  }
}
async function assign(person, keys) {
  const p = String(person || "").trim();
  if (!keys.length) { ElMessage.warning("先在墙上点选要归人的球"); return; }
  if (!p) { ElMessage.warning("选或输入一个人名"); return; }
  try {
    const w = await api.post("/api/wall/assign",
                             { person: p, keys, sort: sortBy.value });
    applyWall(w);
    selKeys.value = [];                     // 归人后清点选（旧界面同款）
    toastMsg(w.msg);
    if (assignName.value) loadGuard(assignName.value);   // 组变了，读数刷新
  } catch (e) { ElMessage.error(e.message); }
}
async function unassign(keys) {
  if (!keys.length) return;
  try {
    const w = await api.post("/api/wall/unassign", { keys, sort: sortBy.value });
    applyWall(w);
    selKeys.value = [];
    toastMsg(w.msg);
    if (assignName.value) loadGuard(assignName.value);
  } catch (e) { ElMessage.error(e.message); }
}
const trayAssign = () => assign(assignName.value, selKeys.value);
const trayUnassign = () => unassign(selKeys.value);

// 归人前护栏读数（spec API 表第 5 行）：下拉换人时看该人整组。只读、
// 失败静默置空（R17）——真护栏在服务端 assign 里，读数是辅助信息。
async function loadGuard(person) {
  try { guardInfo.value = await api.get("/api/wall/guard" + qs({ person })); }
  catch { guardInfo.value = null; }
}
function onPersonChange(p) {
  guardInfo.value = null;
  if (p) loadGuard(p);
}
```

- [ ] **Step 3: `WallBay.vue` 的 template 增量**——① 墙头 `.wh-note` 之后加两枚按钮：

```html
          <span class="spacer"></span>
          <el-button text size="small" @click="selectAll">全选</el-button>
          <el-button text size="small" @click="clearSel">清空</el-button>
```

② 卡片 div 加选中态与点选（`:key="it.key" class="card"` 那枚）：

```html
          <div v-for="it in unassigned" :key="it.key" class="card"
               :class="{ sel: selKeys.includes(it.key) }" @click="toggle(it.key)">
            <span v-if="selKeys.includes(it.key)" class="selmark">✓</span>
            <span v-if="badOf(it)" class="warn">⚠ 定位失效</span>
            <img :src="it.thumb" alt="" />
            <div class="cap"><span>{{ labelOf(it) }}</span><span class="dragtip">拖着走 ↗</span></div>
          </div>
```

③ `.main` 那个 div 之后（`v-else` 块的收尾处）加护栏读数行与托盘：

```html
    <div v-if="guardInfo && assignName" class="guardline">
      🛡 {{ assignName }}：已归 {{ guardInfo.n }} 球<template
        v-if="guardInfo.violations.length"> —— {{ guardInfo.msg }}</template>
    </div>
    <WallTray v-model:name="assignName" :sel-keys="selKeys"
              :choices="personChoices" :chip-label="chipLabel"
              @toggle="toggle" @assign="trayAssign" @unassign="trayUnassign"
              @clear="clearSel" @person-change="onPersonChange" />
```

（`personChoices` 的 computed：`const personChoices = computed(() => wall.value?.personChoices || []);`）

- [ ] **Step 4: style 增量**（`<style scoped>` 追加）：

```css
.card.sel { border-color: var(--el-color-primary);
            box-shadow: 0 0 0 3px rgba(232, 98, 45, .25); }
.selmark { position: absolute; top: 8px; left: 8px; width: 24px; height: 24px;
           border-radius: 50%; background: var(--el-color-primary); color: #fff;
           font-size: 14px; font-weight: 700; display: flex; align-items: center;
           justify-content: center; }
.card { cursor: pointer; }
.guardline { flex: none; padding: 6px 18px; font-size: 12.5px; color: #f2c078;
             background: #15202b; border-top: 1px solid #2b4257; }
```

（`.card { cursor: pointer; }` 单独一行追加在原 `.card` 规则之后——别改原规则。）

- [ ] **Step 5: `wall.spec.mjs` 在 `// —— 收尾` 之前加 E/F/G 三段**（H/I 归 Task 5、J 归 Task 6、K 归 Task 7）

```js
  // —— E. 点选与托盘 chips ——
  await cards.nth(0).click();
  await cards.nth(1).click();
  eq(await page.locator("#app .tray .el-tag").count(), 2, "点两卡 → 托盘 2 枚 chips");
  ok((await page.locator("#app .tray b").innerText()).includes("已选 2 球"),
     "托盘标题计数");
  eq(await page.locator("#app .wallgrid .card.sel").count(), 2, "两张卡带选中态");
  ok((await page.locator("#app .tray .el-tag").first().innerText()).includes("00:01.2"),
     "chip 标签=服务端 caption（R12）");
  await page.locator("#app .tray .el-tag .el-tag__close").first().click();
  eq(await page.locator("#app .tray .el-tag").count(), 1, "chip × 撤选后剩 1");
  eq(await page.locator("#app .wallgrid .card.sel").count(), 1, "卡片选中态跟着撤");
  await cards.nth(0).click();                    // 再点同一张卡 = 恢复选中
  eq(await page.locator("#app .tray .el-tag").count(), 2, "toggle 再点恢复");

  // —— F. 归人（下拉候选=回包 personChoices；guard 读数先出现）——
  ok(Array.isArray(w.personChoices) && w.personChoices.includes("三分"),
     "回包 personChoices 含标签库「三分」（夹具 tags.json）");
  await page.locator("#app .tray .el-select").click();
  // el-select 下拉 teleport 到 body（Plan B 的教训：不能限定在 #app 里找）
  await page.locator(".el-select-dropdown__item:has-text(\"三分\")").first().click();
  await waitFor(async () => (await page.locator("#app .guardline").count()) === 1,
                "guardline 出现（换人名即取读数，R4）");
  ok((await page.locator("#app .guardline").innerText()).includes("已归 0 球"),
     "guard 读数 n=0（该人还没组）");
  await page.locator("#app .tray button:has-text(\"归人\")").click();
  await waitFor(async () =>
    (await page.locator("#app .topbar .el-tag").nth(1).innerText()).trim() === "未归 10",
    "归人后未归 10");
  eq(await page.locator("#app .tray .el-tag").count(), 0, "归人后清点选");
  await waitFor(async () => (await page.locator(".el-message--success").count()) >= 1,
                "成功 toast");
  ok((await page.locator(".el-message--success").last().innerText())
     .includes("✅ 2 球 → 三分"), "toast=服务端 msg 逐字");

  // —— G. 护栏：相邻球（0.4s < 2.5s）再归同人 → warning 警示、照常归人 ——
  // （此刻未归墙首两卡 = 00:02.0 / 00:02.4，与已归的 00:01.6 也是 0.4s 邻对）
  await cards.nth(0).click();
  await cards.nth(1).click();
  await page.locator("#app .tray button:has-text(\"归人\")").click();  // 人名还是 三分
  await waitFor(async () => (await page.locator(".el-message--warning").count()) >= 1,
                "护栏警示 toast");
  ok((await page.locator(".el-message--warning").last().innerText()).includes("护栏"),
     "警示文案来自服务端 msg");
  await waitFor(async () =>
    (await page.locator("#app .topbar .el-tag").nth(1).innerText()).trim() === "未归 8",
    "警示不阻断：照常归人（未归 8）");
  await waitFor(async () => (await page.locator("#app .guardline").innerText())
    .includes("已归 4 球"), "归人后 guard 读数随组刷新（R4 第二路）");
```

（执行期勘误 R-7：G 段末条原是裸 `ok` 立读 guardline——`assign()` 里的 `loadGuard` 在 `applyWall` 之后**异步**发射，`waitFor(未归 8)` 通过时读数可能仍停在上一组的「已归 2 球」上，竞态。已按文件自身惯例包成 waitFor；waitFor 不计数，连带 R-5 的计数勘误。）

- [ ] **Step 6: 构建并跑墙 e2e**

Run: `cd frontend && npm run build`
Run: `cd .. && ./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_wall_e2e.py -v`
Expected: PASSED（`✅ 全过 N 项`，比 Task 3 多 **12** 项：E7+F4+G1）。（执行期勘误 R-5：初稿「多 14（E8+F4+G2）」两处皆误——E 段实为 7 条（E8 是计划期误数），R-7 的 waitFor 包裹又把 G 末条从计数 ok 变为不计数 waitFor（G 实为 1 条）。终值 **26** = 14+12，Task 8 对账按 26。）

- [ ] **Step 7: Commit**

```bash
git add frontend/src/wall/WallTray.vue frontend/src/wall/WallBay.vue tests/e2e/wall.spec.mjs bbh/static
git commit -m "feat(wall): 点选托盘——归人/移出按钮路径 + 服务端 msg 双段 toast + guard 读数"
```

---

### Task 5: 右列按人分组（`PersonGroups.vue`）+ 折叠保持

**Files:**
- Create: `frontend/src/wall/PersonGroups.vue`（**终态一次写全**——拖拽相关的 props/emits/属性本任务就位，Task 6 只在 WallBay 侧接线，本文件不再改）
- Modify: `frontend/src/wall/WallBay.vue`（挂组件）
- Modify: `tests/e2e/wall.spec.mjs`（H/I 两段断言）
- Modify: `bbh/static/**`

**Interfaces:**
- Consumes: Task 3 的 `assigned`/`openGroups`/`labelOf`/`badOf`；Task 4 的 `selKeys`/`toggle`。
- Produces: `PersonGroups.vue` 的 props/emits 契约（Task 6 直接使用）：props `groups/selKeys/dragOver/labelOf/badOf/open`，emits `toggle(key)/zone(v)/drop-to(v)/drag-start(key)/drag-end()/update:open(v)`。

- [ ] **Step 1: 建 `frontend/src/wall/PersonGroups.vue`**

```vue
<script setup>
// 右列按人分组（spec 痛点表「按人折叠」）：组头（头像/人名/计数）是拖拽
// 放置目标，组内缩略图可点选改判、可拖回左墙，底部虚线块是「新建人名」
// 放置区。组件不取数、不写数——一切经 props 进、经 emits 出，墙状态的
// 唯一持有人是 WallBay（Task 6 只在 WallBay 侧接线，本文件到此不再改）。
import { DROP_NEW } from "./wallkit.js";

defineProps({
  groups: { type: Array, default: () => [] },   // [{person, items:[{key,caption,thumb}]}]
  selKeys: { type: Array, default: () => [] },
  dragOver: { type: String, default: null },    // 哨兵：null/DROP_UN/DROP_NEW/组名
  labelOf: { type: Function, required: true },
  badOf: { type: Function, required: true },
  open: { type: Array, default: () => [] },     // 展开的人名（v-model:open）
});
const emit = defineEmits(["toggle", "zone", "drop-to", "drag-start", "drag-end",
                          "update:open"]);
// dragleave 防闪烁：进子元素也会冒 dragleave，contains 判真离开才清高亮
// （原型 leaveZone 同款）
function leaveZone(e) {
  if (!e.currentTarget.contains(e.relatedTarget)) emit("zone", null);
}
</script>

<template>
  <div class="right colwall">
    <div v-if="!groups.length" class="nope">（还没有人——从左侧选球后用底部托盘「归人」，或直接拖进来）</div>
    <el-collapse :model-value="open" @update:model-value="(v) => emit('update:open', v)">
      <el-collapse-item v-for="g in groups" :key="g.person" :name="g.person">
        <template #title>
          <div class="ghead" :class="{ droptarget: dragOver === g.person }"
               @dragover.prevent="emit('zone', g.person)"
               @dragleave="leaveZone"
               @drop.prevent="emit('drop-to', g.person)">
            <span class="avatar">{{ (g.person || "?")[0] }}</span>
            <b>{{ g.person }}</b>
            <el-tag size="small" type="success" effect="plain">{{ g.items.length }} 球</el-tag>
          </div>
        </template>
        <div class="thumbs">
          <div v-for="it in g.items" :key="it.key" class="thumb"
               :class="{ sel: selKeys.includes(it.key) }" draggable="true"
               :title="`拖回左侧未归墙 = 从「${g.person}」移出`"
               @click="emit('toggle', it.key)"
               @dragstart="emit('drag-start', it.key)" @dragend="emit('drag-end')">
            <span v-if="badOf(it)" class="tbad">⚠</span>
            <img :src="it.thumb" alt="" />
            <span class="t">{{ labelOf(it) }}</span>
          </div>
        </div>
      </el-collapse-item>
    </el-collapse>
    <div class="drophint" :class="{ over: dragOver === DROP_NEW }"
         @dragover.prevent="emit('zone', DROP_NEW)"
         @dragleave="leaveZone"
         @drop.prevent="emit('drop-to', DROP_NEW)">＋ 拖到这里 = 新建人名{{ dragOver === DROP_NEW ? "（松手创建）" : "" }}</div>
  </div>
</template>

<style scoped>
/* 根节点 .right/.colwall 的布局样式在 WallBay.vue（父组件 scoped 样式作用
   于子组件根节点），这里只放右列内部的件。 */
.nope { color: #6f7278; font-size: 13px; padding: 6px 2px; }
.ghead { display: flex; align-items: center; gap: 10px; }
.ghead.droptarget { outline: 2px dashed var(--el-color-primary); outline-offset: 3px;
                    border-radius: 10px; }
.avatar { width: 34px; height: 34px; border-radius: 50%;
          background: var(--el-color-primary); color: #fff; font-weight: 700;
          font-size: 15px; display: flex; align-items: center; justify-content: center; }
.thumbs { display: flex; flex-wrap: wrap; gap: 8px; padding: 4px 2px 8px; }
.thumb { position: relative; width: 96px; border: 2px solid #3a3a3a;
         border-radius: 8px; overflow: hidden; cursor: pointer; background: #222; }
.thumb.sel { border-color: var(--el-color-primary); }
.thumb img { display: block; width: 100%; aspect-ratio: 1.28; object-fit: cover;
             background: #000; }
.t { position: absolute; bottom: 0; left: 0; right: 0; background: rgba(0, 0, 0, .6);
     font-size: 10.5px; color: #ddd; padding: 1px 6px; }
.tbad { position: absolute; top: 4px; right: 4px; font-size: 11px; color: #f2c078;
        background: rgba(0, 0, 0, .6); border-radius: 6px; padding: 1px 5px; }
.drophint { margin-top: 12px; border: 2px dashed #4a4a4a; border-radius: 10px;
            padding: 10px; text-align: center; color: #6f7278; font-size: 12.5px; }
.drophint.over { border-color: var(--el-color-primary); color: var(--el-color-primary); }
</style>
```

- [ ] **Step 2: `WallBay.vue` 挂组件**——import 区加 `import PersonGroups from "./PersonGroups.vue";`；template 里把空的右列 `<div class="right colwall"></div>` 换成：

```html
      <PersonGroups v-model:open="openGroups" :groups="assigned" :sel-keys="selKeys"
                    :label-of="labelOf" :bad-of="badOf" @toggle="toggle" />
```

（Task 6 在这行上补 `:drag-over` 与四个拖拽 emits；本任务的 PersonGroups 已把 handler 挂好但没人监听——emit 无监听者是 no-op，无害。）

- [ ] **Step 3: 构建并跑墙 e2e（本任务先只到挂载不炸）**

Run: `cd frontend && npm run build`
Run: `cd .. && ./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_wall_e2e.py -v`
Expected: PASSED（新断言还没加，但分组区渲染不能引发 pageerror——收尾 errs 检查兜底）。

- [ ] **Step 4: `wall.spec.mjs` 在 `// —— 收尾` 之前加 H/I 两段**

```js
  // —— H. 右列分组：组头/缩略图/折叠（R15：手动收起在重载墙后保持）——
  const ghead = page.locator("#app .right .ghead");
  eq(await ghead.count(), 1, "一个组（三分）");
  ok((await ghead.locator("b").innerText()).includes("三分"), "组头人名");
  eq((await ghead.locator(".el-tag").innerText()).trim(), "4 球", "组头计数=已归 4");
  eq(await page.locator("#app .right .thumb").count(), 4, "组内 4 缩略图");
  ok((await page.locator("#app .right .thumb .t").first().innerText())
     .includes("00:01.2"), "缩略图时间条=labelOf");
  ok((await page.locator("#app .right .thumb").first().getAttribute("title"))
     .includes("拖回左侧未归墙"), "缩略图 title 逐字");
  const wrap = page.locator("#app .right .el-collapse-item__wrap").first();
  await ghead.click();                          // 收起
  await wrap.waitFor({ state: "hidden" });
  await radios.nth(2).click();                  // 重排（applyWall 路径）
  await waitFor(async () => (await page.locator("#app .wallgrid .card .cap span")
    .first().innerText()).includes("00:05.6"), "重排生效");
  await wrap.waitFor({ state: "hidden" });      // 收起态在重排后保持（R15）
  await ghead.click();                          // 重新展开
  await wrap.waitFor({ state: "visible" });
  await radios.nth(0).click();                  // 排序复位，后续段按时间序断言
  await waitFor(async () => (await page.locator("#app .wallgrid .card .cap span")
    .first().innerText()).includes("00:02.8"), "切回「时间」");

  // —— I. 移出：点已归缩略图入托盘 → ↩ 移出 → 回未归墙 ——
  await page.locator("#app .right .thumb").first().click();
  eq(await page.locator("#app .tray .el-tag").count(), 1, "已归球也能点选");
  ok((await page.locator("#app .tray .el-tag").first().innerText())
     .includes("三分·"), "chip 显示 人名·key（R12）");
  await page.locator("#app .tray button:has-text(\"移出\")").click();
  await waitFor(async () =>
    (await page.locator("#app .topbar .el-tag").nth(1).innerText()).trim() === "未归 9",
    "移出后未归 9");
  await waitFor(async () => (await page.locator(".el-message--success").count()) >= 1,
                "移出 toast");
  ok((await page.locator(".el-message--success").last().innerText())
     .includes("已移出归人"), "toast=服务端 msg 逐字");
```

（执行期勘误 R-8：H 段末条「切回时间」的期望首卡初稿写 00:01.2——那是 D 段（尚无归人时）的拷贝残留；H 时点 F/G 已把 4 个最早球（00:01.2–00:02.4）归进三分，未归墙时间序首卡实为 **00:02.8**。实现者实跑超时取证后按本段自身意图（排序复位、后续段按时间序断言）改定此一字面值，判据未放松。）

- [ ] **Step 5: 构建并跑墙 e2e**

Run: `cd frontend && npm run build`
Run: `cd .. && ./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_wall_e2e.py -v`
Expected: PASSED（比 Task 4 多 **9** 项：H6+I3）。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/wall/PersonGroups.vue frontend/src/wall/WallBay.vue tests/e2e/wall.spec.mjs bbh/static
git commit -m "feat(wall): 右列按人分组——折叠组/缩略图点选改判/空态与新建放置区"
```

---

### Task 6: 拖拽（卡片→组头归人 / 已归→左墙移出 / →新建区弹窗）

**Files:**
- Modify: `frontend/src/wall/WallBay.vue`（PersonGroups 不动——Task 5 已写全）
- Modify: `tests/e2e/wall.spec.mjs`（J 段断言）
- Modify: `bbh/static/**`

**Interfaces:**
- Consumes: Task 4 的 `assign(person,keys)`/`unassign(keys)`；Task 5 的 PersonGroups emits；Task 1 的 `DROP_UN/DROP_NEW`。
- Produces: `dragKey`/`dragOver`/`endDrag()`/`dropToUn()`/`dropTo(zone)`/`dropNew(key)`/`leaveZone(e)`。

- [ ] **Step 1: `WallBay.vue` 的 script 增量**——import 行改成（两处合并）：

```js
import { ElMessage, ElMessageBox } from "element-plus";
```

```js
import { splitCaption, itemByKey, DROP_UN, DROP_NEW } from "./wallkit.js";
```

状态区（`guardInfo` 之后）加：

```js
const dragKey = ref(null);           // 正在拖的球 key（R7：状态变量，不用 dataTransfer）
const dragOver = ref(null);          // 高亮哨兵：null / DROP_UN / DROP_NEW / 组名
```

函数区（`onPersonChange` 之后）加：

```js
const endDrag = () => { dragKey.value = null; dragOver.value = null; };
// 已归球拖回左墙 = 移出（球本就未归则 no-op，原型同款）
async function dropToUn() {
  const k = dragKey.value;
  endDrag();
  if (!k) return;
  if (unassigned.value.some((x) => x.key === k)) return;
  await unassign([k]);
}
// 落点分发：组名 → 归人；DROP_NEW → 新建人名弹窗（R13）
async function dropTo(zone) {
  const k = dragKey.value;
  endDrag();
  if (!k) return;
  if (zone === DROP_NEW) { await dropNew(k); return; }
  await assign(zone, [k]);
}
// 新建人名：prompt 文案逐字照原型；落点还是 assign——前端不写标签库（R13）
async function dropNew(key) {
  try {
    const { value } = await ElMessageBox.prompt(
      "新人名（入全局标签库，全部视频共用）", "拖拽新建",
      { confirmButtonText: "归人" });
    const name = String(value || "").trim();
    if (!name) return;
    await assign(name, [key]);
  } catch { /* 用户取消 */ }
}
// dragleave 防闪烁：进子元素也冒 dragleave，contains 判真离开（原型同款）
function leaveZone(e) {
  if (!e.currentTarget.contains(e.relatedTarget)) dragOver.value = null;
}
```

- [ ] **Step 2: `WallBay.vue` 的 template 增量**——① 左列开标签（Task 3 的 `<div class="left colwall">`）换成：

```html
      <div class="left colwall" :class="{ droptarget: dragOver === DROP_UN }"
           @dragover.prevent="dragOver = DROP_UN" @dragleave="leaveZone"
           @drop.prevent="dropToUn">
```

② 卡片（Task 4 的那枚）加 draggable 与拖起：

```html
          <div v-for="it in unassigned" :key="it.key" class="card"
               :class="{ sel: selKeys.includes(it.key) }" draggable="true"
               @click="toggle(it.key)" @dragstart="dragKey = it.key"
               @dragend="endDrag">
```

③ PersonGroups 那行（Task 5 的）换成：

```html
      <PersonGroups v-model:open="openGroups" :groups="assigned" :sel-keys="selKeys"
                    :drag-over="dragOver" :label-of="labelOf" :bad-of="badOf"
                    @toggle="toggle" @zone="dragOver = $event" @drop-to="dropTo"
                    @drag-start="dragKey = $event" @drag-end="endDrag" />
```

- [ ] **Step 3: style 增量**（`<style scoped>` 追加）：

```css
.left.droptarget { outline: 2px dashed var(--el-color-primary); outline-offset: -6px;
                   border-radius: 10px; background: #1d1a17; }
```

- [ ] **Step 4: `wall.spec.mjs` 在 `// —— 收尾` 之前加 J 段**（合成 DragEvent——R11；R7 的状态变量设计让这条链确定性可驱动）

```js
  // —— J. 拖拽三条路（合成 DragEvent，R7/R11）——
  const fire = (loc, type) => loc.evaluate((el, t) => {
    el.dispatchEvent(new DragEvent(t, { bubbles: true, cancelable: true,
                                        dataTransfer: new DataTransfer() }));
  }, type);

  // J1. 未归卡片 → 组头 = 归人
  await fire(cards.nth(0), "dragstart");
  await fire(ghead, "dragover");
  ok((await ghead.getAttribute("class")).includes("droptarget"), "组头拖入高亮");
  await fire(ghead, "drop");
  await fire(cards.nth(0), "dragend");
  await waitFor(async () =>
    (await page.locator("#app .topbar .el-tag").nth(1).innerText()).trim() === "未归 8",
    "拖卡到组头=归人（未归 8）");
  eq((await ghead.locator(".el-tag").innerText()).trim(), "4 球", "组回到 4 球");

  // J2. 已归缩略图 → 左墙 = 移出
  await fire(page.locator("#app .right .thumb").nth(0), "dragstart");
  await fire(page.locator("#app .left"), "dragover");
  ok((await page.locator("#app .left").getAttribute("class")).includes("droptarget"),
     "左墙拖入高亮");
  await fire(page.locator("#app .left"), "drop");
  await waitFor(async () =>
    (await page.locator("#app .topbar .el-tag").nth(1).innerText()).trim() === "未归 9",
    "拖回左墙=移出（未归 9）");

  // J3. 未归卡片 → 新建区 = prompt 新建人名（confirmButtonText 是我们自己传的「归人」，
  //     不依赖 locale；按钮仍用语义选择器——ElMessageBox 没装 locale 时默认文案是英文）
  await fire(cards.nth(0), "dragstart");
  await fire(page.locator("#app .drophint"), "dragover");
  ok((await page.locator("#app .drophint").getAttribute("class")).includes("over"),
     "新建区拖入高亮");
  await fire(page.locator("#app .drophint"), "drop");
  await page.locator(".el-message-box__input input").waitFor();
  await page.locator(".el-message-box__input input").fill("小左");
  await page.locator(".el-message-box__btns .el-button--primary").click();
  await waitFor(async () => (await ghead.count()) === 2, "新组出现");
  ok((await ghead.nth(1).locator("b").innerText()).includes("小左"),
     "新组=小左（码点序在三分后）");
  eq((await ghead.nth(1).locator(".el-tag").innerText()).trim(), "1 球", "新组 1 球");
```

- [ ] **Step 5: 构建并跑墙 e2e**

Run: `cd frontend && npm run build`
Run: `cd .. && ./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_wall_e2e.py -v`
Expected: PASSED（比 Task 5 多 **6** 项：J1 两项 + J2 一项 + J3 三项；结束时 manual_groups.json 含 三分 与 小左 两组——launcher 的落盘断言随之变真）。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/wall/WallBay.vue tests/e2e/wall.spec.mjs bbh/static
git commit -m "feat(wall): 拖拽三路——卡片入组/已归拖回/新建人名弹窗 + 合成事件 e2e"
```

---

### Task 7: 回扫 job（墙页入口 + 刷新续跟 + 服务端 msg 进度）

**Files:**
- Modify: `frontend/src/wall/WallBay.vue`
- Modify: `tests/e2e/wall.spec.mjs`（K 段断言）
- Modify: `bbh/static/**`

**Interfaces:**
- Consumes: `startJob(url, body) → jobId` / `followJob(jobId, onTick) → result`（`shared/jobs.js`，error 态抛 `Error(j.error)`）；`createJobMemory()`（`shared/jobmemory.js`）；剪辑台 EditBay 的同款约定（jobmemory 单槽、kind=`wall_scan`、videoPath 匹配才续跟）。
- Produces: `scanJob` / `startScan()` / `resumeScan()`；onState 换视频自动重载墙。

- [ ] **Step 1: `WallBay.vue` 的 script 增量**——import 区改两行、加两行：

```js
import { ref, reactive, computed, onMounted } from "vue";   // 原行加 reactive
```

```js
import { startJob, followJob } from "../shared/jobs.js";
import { createJobMemory } from "../shared/jobmemory.js";
```

状态区加：

```js
const jobMem = createJobMemory();     // 回扫跨刷新续跟（与剪辑台同一个单槽约定）
const scanJob = reactive({ phase: "idle", msg: "" });   // idle | running | error
```

函数区加：

```js
// 回扫：与剪辑台「识别出手人」是同一个后端 job 的两处入口（原型同款）。
// 进度只显示服务端 msg（R5：wall_scan 的 worker 从不报 pct）；完成后的墙刷新
// 是重新 GET /api/wall（R6：result 只平铺 state_payload，墙数据不在里面）。
async function startScan() {
  if (scanJob.phase === "running") return;
  scanJob.phase = "running";
  scanJob.msg = "⏳ 启动回扫…";
  try {
    const jobId = await startJob("/api/wall/scan");     // 400 前置在这里同步抛
    jobMem.remember({ jobId, kind: "wall_scan",
                      videoPath: state.value?.videoPath });
    const res = await followJob(jobId, (j) => {
      if (j.msg) scanJob.msg = j.msg;
    });
    jobMem.clear();
    scanJob.phase = "idle";
    scanJob.msg = "";
    await loadWall();
    ElMessage.success(res?.msg || "✅ 完成");            // 服务端汇总，不自编
  } catch (e) {
    scanJob.phase = "error";
    jobMem.clear();
    ElMessage.error(e.message);
  }
}
// 刷新页面后接着跟（记录的 videoPath 与当前不符则弃——单用户单视频工具）
async function resumeScan() {
  const rec = jobMem.load();
  if (!rec || rec.kind !== "wall_scan") return;
  if (!state.value?.videoPath || rec.videoPath !== state.value.videoPath) {
    jobMem.clear();
    return;
  }
  scanJob.phase = "running";
  scanJob.msg = "⏳ 续跟回扫…";
  try {
    const res = await followJob(rec.jobId, (j) => {
      if (j.msg) scanJob.msg = j.msg;
    });
    ElMessage.success(res?.msg || "✅ 完成");
  } catch (e) {
    ElMessage.error(e.message);
  } finally {
    jobMem.clear();
    scanJob.phase = "idle";
    scanJob.msg = "";
    await loadWall();
  }
}
```

- [ ] **Step 2: 换视频自动重载 + 挂载续跟**——`onState` 与 `onMounted`（Task 3 的版本）整体换成：

```js
function onState(s) {
  const prev = state.value?.videoPath;
  state.value = s;
  // 剪辑台载入/换视频后跳过来：墙跟着换（无会话不发，R9）。
  // store.refresh() 会通知监听者，所以首载也走这里——onMounted 不再单独 loadWall。
  if (s?.videoPath && s.videoPath !== prev) loadWall();
}

onMounted(async () => {
  store.on(onState);
  try { await store.refresh(); } catch (e) { ElMessage.error(e.message); }
  await resumeScan();     // 有未完的回扫记录（同视频）则续跟
});
```

- [ ] **Step 3: template 增量**——topbar 第三枚 tag 与 spacer 之间插扫描按钮与进度行（Task 3 加排序后的现状是 `<el-tag type="success" …>已归 …</el-tag>` 之后直接跟 `<span class="spacer"></span>`）：

```html
      <el-button type="primary" size="small"
                 :loading="scanJob.phase === 'running'" @click="startScan">🧍 识别出手人（回扫溯源）</el-button>
      <span v-if="scanJob.phase === 'running'" class="scanmsg">{{ scanJob.msg }}</span>
```

- [ ] **Step 4: style 增量**（`<style scoped>` 追加）：

```css
.scanmsg { font-size: 12px; color: #8b8e95; max-width: 420px; overflow: hidden;
           text-overflow: ellipsis; white-space: nowrap; }
```

- [ ] **Step 5: `wall.spec.mjs` 在 `// —— 收尾` 之前加 K 段**（夹具不造视频文件 ⇒ job 在 worker 的第一道守卫上失败——这正好验「错误路径」：toast 文案来自服务端、按钮复原、jobmemory 清槽）

```js
  // —— K. 回扫错误路径：夹具视频文件不存在 ⇒ worker 守卫「视频不存在」——
  const scanBtn = page.locator("#app .topbar button", { hasText: "识别出手人" });
  await scanBtn.click();
  // loading 态在夹具下只存活一次 POST+GET 往返（worker 第一道守卫即刻失败、
  // jobs.js followJob 首轮 GET 无初始延迟即见终态）——限时尽力观测，抓到最好、
  // 抓不到不算失败；错误 toast 与下文的 loading 归零才是确定性门（R-9）
  await page.locator("#app .topbar .el-button.is-loading")
    .waitFor({ timeout: 1500 }).catch(() => {});
  await waitFor(async () => (await page.locator(".el-message--error").count()) >= 1,
                "回扫失败 toast");
  ok((await page.locator(".el-message--error").last().innerText())
     .includes("视频不存在"), "错误文案来自服务端 worker 守卫");
  await waitFor(async () =>
    (await page.locator("#app .topbar .el-button.is-loading").count()) === 0,
    "按钮 loading 解除可再点");
  eq(await page.evaluate(() => localStorage.getItem("bbh.runningJob")), null,
     "jobmemory 清槽（错误路径也清）");
```

（执行期勘误 R-9：K1 的 `.is-loading` 观测初稿是默认 30s 超时的硬门——夹具无视频文件使 worker 第一道守卫即刻失败、`followJob` 首轮 GET 又无初始延迟，loading 只存活一次 POST+GET 往返（~10-30ms），Playwright 抓不到时假红且挂 30s。改为 1.5s 限时**尽力观测**：抓到即证 :loading 绑定，抓不到不算失败——错误 toast 与 loading 归零仍是确定性门。代价：:loading 绑定彻底失效的视觉回归不再被 e2e 拦（phase===running 门仍防双跑），由 Plan E 真机验收兜底。）

- [ ] **Step 6: 构建并跑墙 e2e**

Run: `cd frontend && npm run build`
Run: `cd .. && ./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_wall_e2e.py -v`
Expected: PASSED（比 Task 6 多 **2** 项：错误文案 + jobmemory 清槽）。

- [ ] **Step 7: Commit**

```bash
git add frontend/src/wall/WallBay.vue tests/e2e/wall.spec.mjs bbh/static
git commit -m "feat(wall): 回扫 job 墙页入口——服务端 msg 进度/完成重取墙/刷新续跟/错误复原"
```

---

### Task 8: 收尾全量（全仓回归一次，此处才跑全量）

**Files:** 无新文件（若全量跑出需要修补的问题，修补按所属任务的 Files 清单提交）。

- [ ] **Step 1: 构建**

Run: `cd frontend && npm run build`
Expected: 三入口构建成功、自检插件过。

- [ ] **Step 2: 前端纯 JS 层**

Run: `cd frontend && node selftest.mjs`
Expected: `✅` 收尾、**34** 例全过（28 基线 + Task 1 的 6）。

- [ ] **Step 3: 全量 pytest（本计划唯一一次全量）**

Run: `cd .. && ./.venv/Scripts/python.exe -X utf8 -m pytest -q`
Expected: **632 passed / 1 skipped**（Plan B 基线 631+1 之上加墙 e2e 1 例；skip 是既有那例，与墙无关）。

- [ ] **Step 4: spec 项数复核（Plan B 的既有纪律）**

Run: `./.venv/Scripts/python.exe -X utf8 tests/e2e/count-spec-items.py`
Expected: 剪辑台 spec **190** 不变（本计划不碰它）。另记录墙 e2e 输出的 `✅ 全过 N 项` 的 N 进交付台账。

- [ ] **Step 5: 冒烟（临时服务配方同 Task 2 Step 5）**

Expected: `✅ 冒烟通过：三页可开、Vue 真挂载、tab 高亮正确、可跳转、无 JS 报错`。

- [ ] **Step 6: 工作树核对**

Run: `git status --short`
Expected: 干净（每个任务的产物已随任务提交；这里再出现未跟踪/未暂存 = 有任务漏提交，补上并中文说明）。

---

## 本计划不做的（承接表——复审时别当缺陷报）

| 项 | 去向 / 裁定 |
|---|---|
| 颜色相近提示（colorhint） | **R3 已裁不入**：五端点没有 neighbors 数据源，要补先加只读端点（另立计划）；归人线终局记录该提示恒 low |
| 折叠状态落盘 | **R16**：会话内状态；落盘需写端点，超「零后端」范围。已知项记档 |
| 跨页 e2e（剪辑台↔墙真跳转流） | Plan E 跨页 e2e（spec 的三页联动验收归那里） |
| 真机验收 / `run_app.bat` 切 `bbh.web` / 旧 Gradio 双向兼容 | Plan E |
| 媒体细节（GIF 自动播放、fps、卡片动图体积） | Plan E 真机验收通看 |
| 检测期间墙页写锁 | **R8 已裁不加**（旧界面就没禁墙，前端自加门=行为分叉） |
| `ElMessageBox` 英文默认按钮（locale） | Plan C 之后的小任务（与三处文案时间基同批，用户 2026-09-19 已裁；本计划自己的 prompt 都显式传了 confirmButtonText，e2e 用语义选择器不受影响） |
| 后端任何改动 | 本计划零后端：五端点已交付并被 `tests/test_web_wall.py` 钉死 |
| 原型 mock 专属件（hint「数据为假」/功能对照链接/.protobar/排序内部值 time\|look\|spot/自算进度百分比） | 见「原型对照·明确不搬的」，不承接 |

## 验证配方（速查）

- 逐任务：`cd frontend && npm run build` → `./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_wall_e2e.py -v`（改了纯 JS 层另跑 `node selftest.mjs`）。
- 全量（仅 Task 8）：`./.venv/Scripts/python.exe -X utf8 -m pytest -q` → 632 passed / 1 skipped。
- 冒烟：Task 2 Step 5 的临时服务配方（绝不起 `python -m bbh.web`、绝不碰 7860）。
- spec 项数：`./.venv/Scripts/python.exe -X utf8 tests/e2e/count-spec-items.py` → 190 不变。

## 收尾自查（控制者已跑，随计划存档）

1. **spec 覆盖**：spec 痛点表第 3 行六要素 → 左墙大卡片 ≥200px（T3 样式 `minmax(200px,1fr)`）、多选托盘（T4）、拖到人名组（T6）、已归拖回（T6）、按人折叠（T5）、回扫 job（T7）；API 表五端点 → GET wall（T3）/ assign、unassign、guard（T4）/ scan（T7）；R1–R17 逐条有落点（R3/R8/R16 是「不做」的裁定，进承接表）。**无缺口。**
2. **占位符扫描**：全文无 TBD/TODO/「类似 Task N」/「适当处理」；每个代码步骤带完整代码。**通过。**
3. **类型/名字一致性**（对照已读源码核过，非凭记忆）：`wallkit` 导出名六件与 T3/T4/T6 用法一致；`WallTray`/`PersonGroups` 的 props/emits 与 WallBay 模板逐字对上；`startJob/followJob/createJobMemory` 签名与 `shared/jobs.js`/`shared/jobmemory.js` 在盘实现一致（`api.post(url, undefined)` 不带 body，`startJob("/api/wall/scan")` 合法）；`store.refresh()` 会通知监听者（`session.js` 的 `set()`），T7 的 onState 载墙路径成立；wall_scan worker 的第一道守卫是 `视频不存在`（`run_shooter_tracing`），夹具不造视频文件 ⇒ K 段错误路径成立。**通过。**

## 交付后已知项（2026-09-20 整支终审 parked，全部 Minor——终审结论 Clean，均不构成合并前必修）

| # | 项 | 裁定/去向 |
|---|---|---|
| 1 | `wall.spec.mjs` G 段 warning toast 断言可被 F 段遗留 toast 预先满足（护栏查归人后**整组**，F 的 00:01.2↔00:01.6 邻对本就产 warning） | 段级归因松弛非假绿——G 的真判据是「未归 8」+ guardline「已归 4 球」两道状态门，R4 双路已在 F 段真验。收紧法：归人前快照 warning 计数要求 +1，或断言 G 特有对 00:02.0↔00:02.4。**随 Plan E 测试加固批** |
| 2 | assign/unassign 回包不经 wallSeq 守卫：与在途 loadWall 竞态、旧 sort 回包后落时 `sortBy` 可被静默回退 | 单用户本机毫秒窗、实测难触发。修法：POST 前 `const seq = ++wallSeq`、回包 `seq !== wallSeq` 丢弃（与 loadWall 同纪律）。**随 chore 批** |
| 3 | onState 换视频分支不清 `selKeys`/`guardInfo`（跨视频 key `mm:ss.s` 高度可撞 ⇒ 陈旧 chip 选中错球） | 当前不可达：无轮询、纯 `<a>` 整页导航，`videoPath !== prev` 实际只在首载（prev=undefined）触发。修法：该分支顺手 `selKeys=[]; guardInfo=null`。**未来加任何中逽数据刷新时必修** |
| 4 | wallkit `openAll` 无组件消费者（selftest 钉住；首载全展开由 applyWall prev-Set 差集等价达成） | **留作纯函数层 API 不删**；Task 1 Interfaces「全部 import 这些名字」措辞已勘误（R-12） |

