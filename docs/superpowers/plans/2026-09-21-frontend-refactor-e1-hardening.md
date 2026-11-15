# 前端重构 E1：测试加固 + 跨页 e2e Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把四批积累的纯自动化 parked 项一次收口：项数从「人肉数」变成「门守的量」（count 工具泛化 + 三 wrapper 钉数）、存量 spec 的八处硬化（假覆盖 / 不可达红 / 预算口径 / 非有限 offset）、显示面审计、三页联动跨页 e2e。**需要用户参与的部分（真机验收 / 双向兼容 / vrender 锚实测）全部归 E2。**

**Architecture:** G1 立秤 → G2 存量硬化（8 项，按 spec a–h 分任务）→ G3 增量覆盖（显示面审计 + 跨页 e2e）。项数门先行：后续每个改 spec 项数的任务同任务改钉数（中间态红 = 门在工作，不是事故）。跨页 e2e 复用三套既有夹具 helper（bay `_fixture` + wall `_wall_fixture` + calib `_make_video`），合流于同一 `videos/<NAME>.mp4` ⇒ 三页共享同一后端会话。

**Tech Stack:** Node + Playwright（`tests/e2e/`）、pytest（wrapper）、FastAPI TestClient、Vue3 + Element Plus（仅 T5 修分支与突变自证临时触碰）、Python `.venv`。

**Spec:** `docs/superpowers/specs/2026-09-21-frontend-refactor-e1-hardening-design.md`（E1 设计定稿，`d410228`）

**BASE:** `d410228`（分支 `feature/vertical-highlight`，**不 merge / 不 push**）

---

## Global Constraints

（spec 全局约束逐字 + 本仓执行纪律；**每个任务的要求都隐含包含本节**）

- **分支**：只进 `feature/vertical-highlight`。**绝不** merge、**绝不** push、**绝不** rebase。
- **绝不动用户的 `work/` 状态文件**：分析只读。测试/实验一律 `cwd=临时目录`（`work/` 是相对路径，按 cwd 解析）。
- **仓库根 6 档红线**：`calibration.json` / `calibration_ref.png` / `calibration_seg2.json` / `calibration_ref_seg2.png` / `tags.json` / `ui_prefs.json` —— **一个字节都不许动**（`ui_prefs.json` 是用户自己的界面状态档，工作区里是 `?? ui_prefs.json` ⇒ 不许 add / 不许删 / 不许「修」）。
- **绝不跑 `python -m bbh.web`**（`__main__` 会把仓库根标定档搬进 `work/`）。起服务一律 `uvicorn.run(create_app(), host='127.0.0.1', port=<空闲端口>)` + `cwd=临时目录`。
- **绝不裸跑 `node smoke.mjs`**：默认 BASE 是用户 7860。必须 `BBH_BASE=http://127.0.0.1:<空闲端口>` 覆盖 + 临时 cwd（E30①）。
- **用户自己的 7860 实例**：不杀、不占、不许出现在任何请求 / 探活 / 端口扫描名单里。
- **绝不**用宽泛 `taskkill //IM`；停后台进程按进程句柄 `terminate()`。
- **`cd frontend && npm run build` 必须先于任何 e2e / 冒烟**（服务端挂的是 `bbh/static/` 构建产物；spec 改动虽不碰前端源码，但突变自证会临时碰 ⇒ 跑 e2e 前一律先 build）。
- **`bbh/` 最小改动（E1 新规，解除 Plan D 的「一行不改」）**：**仅 Task 6** 可碰 `bbh/web/api_events.py` / `bbh/web/api_marks.py`（offset `_finite` 收口），**其余任务零后端改动**；`bbh/web/api_gen.py` 一行不改（`_finite` 语义已冻结，`from .api_gen import _finite` 零搬运）。
- **产物 `bbh/static/**` 只由 build 重建**、绝不手改；实现者只 `git add` 本任务点名的具体路径（含 `bbh/static`），**永不** `git add -A` / `git add .`；commit 信息中文、说清「改了什么 + 为什么」。
- **实现者不许派子代理**；**绝不并行派两个实现者**。
- **改一处只跑相关用例**（全量只在 Task 10 收尾跑一次）。Python 一律 `./.venv/Scripts/python.exe -X utf8`。
- **突变自证的纪律**：凡临时改代码/夹具做自证，**用完必须还原**，`git status --porcelain` 见净（`?? ui_prefs.json` 除外）后才许 commit；自证过程写进任务报告（红文案原文 + 还原证明）。
- **项数钉纪律（G1b）**：E1 内每个改 spec 项数的任务**同任务同步改对应 wrapper 的 `EXPECTED_ITEMS`**——改了 spec 没改钉 = 红，这正是门的目的，不许为消红去删断言。

---

## 既有契约（实现者必读，全部已在盘上核过）

### 项数门的三块地基

1. **`tests/e2e/count-spec-items.py` 现状**：硬编码只认剪辑台——`spec_from_file_location("bay_e2e", ROOT/"tests"/"e2e"/"test_edit_bay_e2e.py")`，不读 argv；夹具 = `mod._fixture(tmp)`；起服务 = uvicorn 子进程 `cwd=tmp` + `mod._wait_up`；跑 `mod.SPEC` 后**stdout 与 stderr 尾部都打**（`r.stdout[-1500:]` + `r.stderr[-500:]`——spec 的 `ok` 抛错走 stderr，只打 stdout 失败时是空白 + rc=1，R15b②）。文件名不匹配 `test_*.py` ⇒ pytest 不收集，全量数不受影响。
2. **三个 wrapper 的断言现状**（都只断 `returncode == 0` + `"✅ 全过" in r.stdout` ⇒ 项数掉了照样绿）：`test_edit_bay_e2e.py` / `test_wall_e2e.py` / `test_calib_e2e.py` 各自的测试函数体尾。今日项数真值：**bay 191 / wall 43 / calib 81**。
3. **R15b 配方（Plan D 实跑踩出来的，工具泛化必须逐条落实）**：
   - ① `python <file>` 下 `sys.path[0]` 是**脚本所在目录** ⇒ 必须两行 `sys.path.insert`：仓库根（`from bbh.models import …` 用）+ `tests/e2e`（夹具模块互 import：wall/calib wrapper → `test_edit_bay_e2e`）。现版只有仓库根那行，泛化到 wall/calib 时**第二行必须有**（wall wrapper 顶层 `from test_edit_bay_e2e import …`，不插 `tests/e2e` 就 `ModuleNotFoundError`）。
   - ② stdout 与 stderr **都打**（理由见上）。
   - ③ 按 spec 选夹具组合——bay `_fixture`；wall 在 `_fixture` 之上补 `_wall_fixture`（shooters.json / traces / shots / thumbs / spot bad）；calib 只要 `_make_video` 真视频、**不预置 `calibration.json`**。**复用各 wrapper 已有的 helper，不另写第二份。**

### spec 层的既有形状（行号是 2026-09-21 基线值，**会漂，动手前先 grep**）

- **`calib.spec.mjs`**：挂载屏障 `:36`（`#app .topbar .el-tag` 的裸 `waitFor`，**30s 是故意的**——超时报 `TimeoutError` 正是「app 没挂上」的正确诊断，E1 不动）；裸 `.first().waitFor()` 两处——`miss` toast（越界抽帧，步①）与 `done` toast（完成标定，`filter({ hasText: "标定完成" })` 已过滤）；`waitForResponse` 四处（S refine / V refine / build / del）；⑮(4) 复入守卫的过滤器 `filter({ hasText: "回到第①步" })`（撞前一条「已取消补段，回到第①步」的子串）。
- **toast 文案事实（`CalibWizard.vue`，G2c 的判别依据）**：`addSeg()` 弹 **「回到第①步：抽挪动后画面的帧，重新走一遍标点」**（`回到第①步` 后带**全角冒号**）；`cancelSeg()` 弹 **「已取消补段，回到第①步」**（`回到第①步` 结尾、**无冒号**）⇒ `hasText: "回到第①步："` 只匹配新 toast。
- **`wall.spec.mjs` F/G 段**：F 段归人 `00:01.2/00:01.6` 邻对**自己也产 warning toast**（3s 寿命、节点残留）⇒ G 段的「`.el-message--warning` 计数 ≥1」可被 F 的遗留 toast **预先满足**（已知项 1）。G 段归人后另有「未归 8」与「已归 4 球」两条断言。
- **`edit_bay.spec.mjs:587`**：`（spec:87 那条注释记的是同一件事）` 是**指向本文件的自引用行号**（`:87` 今天已是别的内容）。`rowCell(i)` 定义处的注释（点「时刻」列而非整行的原因）才是活的记录。全仓 `grep -rn "spec:[0-9]" tests/ frontend/` 只有这一处（wall/calib 0 处）。
- **spec 头部三件套（三份 spec 同构）**：`ok(cond, msg)` 假则 `throw new Error("❌ " + msg)`、真则 `pass++`；`eq(a, b, msg)` 内部调 `ok` 也 +1；`waitFor(fn, msg, ms = 10000)` 每 150ms 轮询、**不计项**。**项数 = ok 调用 + eq 调用**（扣掉 `eq` 定义体内那一处 `ok`）。⚠ 注释里出现 `ok(`/`eq(` 字面量会让静态计数多算——数项数以**实跑输出的 `✅ 全过 N 项`** 为准。

### 后端事实（Task 6 的地基）

- **两处消费点同形 `body.offset or 0.0`**：`bbh/web/api_events.py`（`delete_events`，`DELETE /api/events`）与 `bbh/web/api_marks.py:47`（`post_feedback`，裁决端点）。旧行为已实测（edit-msg 终审 M-3）：**±inf → 500**（`int(inf//60)` 抛 `ValueError`，无半写状态）；**NaN → 200 + 成功文案里藏错时刻**（`max(0.0, t+nan)=0.0` ⇒ toast 显示 `00:00.0`）——比 500 隐蔽。
- **`api_gen.py` 的 `_finite`（语义冻结，一字不改）**：`def _finite(v): return v if isinstance(v, (int, float)) and math.isfinite(v) else None`——非有限（NaN/±inf/字符串/None）一律当没传、保留原值。
- **跨 api 模块 import 是既有惯例**：`api_marks.py:16` 已有 `from .api_events import _rows_and_index` ⇒ `from .api_gen import _finite` 合规，零搬运。
- **组合语义**：`_finite(body.offset) or 0.0` —— `None→0.0`、`NaN→0.0`、`±inf→0.0`、`0.0→0.0`、有限值→原值。**pydantic 边界**：`VerdictIn.offset` / `KeysIn.offset` 是 `float | None`，字符串 `"abc"` 在 pydantic v2 门口就 **422**（不是 `_finite` 的可达输入；`"5.5"` 会被 lax 模式 coerce 成 5.5——那是合法有限值）。JSON 字面量 `NaN`/`Infinity` TestClient 能发出且 pydantic 接收（edit-msg 终审实测过 `{"verdict":"goal","offset":nan}` → 200 错文案）。
- **msg 形状（`test_web_marks.py:104` 既有用例）**：key `0:12.1` + `offset:1.5` → msg 含 `"00:13.6"`；负偏移夹 0 → msg 含 `"00:00.0"`；`feedback.json` 的 `t_str`/`t` 恒原始。⇒ NaN 收口后 msg 应含 `"00:12.1"` 且**不含** `"00:00.0"`（这条就是牙）。

### 跨页 e2e 的地标（Task 9 断言全用这些，已在盘上核过）

- **tabbar**（`shared/tabbar.js`）：`nav.tabbar a`（三个真 `<a href>`：`edit.html` / `wall.html` / `calib.html`），当前页 `a.on`。真 `<a>` = 真整页导航。
- **edit**：路径框 `#app .topbar input`（`.first()`）；12 行 `#app .el-table__row`；选行点「时刻」列 `td.nth(2)`（`rowCell` 同款——整行中心会落在标签列下拉上）；选中行后右栏出现依据卡，其中深链 `#app a[href="/wall.html"]`（`BasisPanel.vue` 的 `el-link`，文案「在出手人墙中查看 →」，**无论有无出手人回扫都渲染**）。
- **wall**：无会话空态 `#app .stage-hint`（文案含「先去」）；卡片 `#app .wallgrid .card`；顶栏 `#app .topbar .el-tag`，`nth(1)` 是「未归 N」；托盘 `#app .tray`（`.el-select` 选人、`button:has-text("归人")`）；**el-select 下拉 teleport 到 body**——`.el-select-dropdown__item:has-text("三分")` 别限定在 `#app` 里找（Plan B 教训）。夹具 12 traces 全未归 ⇒ 「未归 12」；归 2 球 ⇒ 「未归 10」。
- **calib**：路径框 `#app .topbar input`（`onMounted store.refresh()` 后回填会话 `videoPath`）；顶栏标签 `#app .topbar .el-tag`（已标定/未标定）；四步向导地标同 `calib.spec.mjs`。
- **会话共享机制**：会话态全在服务端（`/api/session` + `/api/state`）；三页 `onMounted` 都 `store.refresh()` ⇒ edit 填路径载入后，另两页**无需重填**即同视频。
- **`state_payload()`（`api_session.py:162`）没有 manual_groups 字段** ⇒ 流④的 edit 侧联动断言**如实降级**（R11）：断「12 行不受扰」，不造假联动。
- **夹具合流点**：三套夹具共用 `NAME = "e2e_fixture"`，视频路径都是 `<root>/videos/e2e_fixture.mp4`。bay `_fixture` 写 events.json 时 video 字段就指这个路径（文件不存在也能载 12 行——bay spec 今天就这么跑）；`_make_video` 把**真视频**写到同一路径 ⇒ 三页共享同一会话且 calib 可真抽帧。

---

## 计划期裁定（R1–R13，实现与复审都以此为准）

| # | 裁定 | 依据 / 理由 |
|---|---|---|
| **R1** | **执行序 G1→G2→G3**：先立秤（T1 工具泛化 → T2 三钉）再硬化再增量 | 门先立，后面每个改项数的任务才有门可撞；spec 三组门槛结构的落实 |
| **R2** | **建门顺序**：bay/wall/calib 三钉在 T2 一次写对（191/43/81 已知）；crosspage（T9）**先跑出 N 再钉** `EXPECTED_ITEMS`（新 spec 的 N 只能源于实跑——建门那一次跑不算中间态） | 项数只能从运行取（count 工具的原理）；「先钉错再撞红」对新 spec 是浪费一轮 e2e |
| **R3** | **G2a 两处只换等待预算、不改定位器**：`miss` 是全新页面后的**第一条** toast（无残留可撞）、`done` 已按文案过滤 ⇒ 再加过滤就是顺手加固 | 「等长替换、判据语义不变」的 spec 原文；E21 的过滤纪律针对的是**有残留风险**的场合 |
| **R4** | **G2b 四处 `waitForResponse` 保持 30s + 就地注释定案**：`waitForResponse` **没有 `ms` 参数**；统一 10s 会让「服务端 30s 才回」从超时变**假绿**。`:36` 挂载屏障同样不动（TimeoutError 是「app 没挂上」的正确诊断）。E2 真机跑过后复访（承接表挂 E2） | spec G2b 逐字；已知项 16 的定案 |
| **R5** | **G2c 判别串 = `回到第①步：`（带全角冒号）**：addSeg toast 独有；cancel toast「已取消补段，回到第①步」结尾**无冒号** ⇒ 不撞。**⑬ 段的 t1 守卫不动**：其前无 cancel toast，改它 = 顺手加固 | toast 文案盘上核过（`CalibWizard.vue` 的 `addSeg`/`cancelSeg`）；已知项 18 的修法落地 |
| **R6** | **G2d 收紧写进 `waitFor` 的条件里**（快照 `warn0` → 断言后恰 `warn0 + 1`），`ok`/`eq` 条数不动 ⇒ 项数不变 | spec G2d 原文；比断 G 特有邻对稳、不与具体时刻耦合 |
| **R7** | **G2e 用 `eq(…, 1)`（恰 1）而非 spec 写的 ≥1**：topbar 恰一个 `el-input`（路径框）⇒ 恰一个 prefix svg；恰 1 蕴含 ≥1 且更强（多渲染也红）。注释如实写明边界：「渲染成**别的**图标捉不到」 | 已知项 8 的可用判据；更强的断言不增加任何维护成本 |
| **R8** | **G2f 复现口径 = 真实交互路径**（Playwright 普通 `.click()`，不 `force`、不 `evaluate` 直击 DOM 事件）；`evaluate` 直击只作**机理证据**记报告，不作判据。可复现 ⇒ 修（`PointMarker` 的 `onClick` 加 `natural` 守卫 + calib spec 同任务加牙 +1 项）；不可复现 ⇒ **零改动**、记已知项收档——**禁顺手加固** | spec G2f 双出口；「不可复现的加固没有牙，是给未来人埋假判据」 |
| **R9** | **G2g 共享形状 = `from .api_gen import _finite`**（零搬运、`api_gen.py` 一行不动；跨 api import 是 `api_marks.py:16` 既有惯例）。**pytest 牙的四种输入按实盘修正为 None / NaN / +inf / −inf**（spec 原文的「字符串」在 `float | None` 的 pydantic 门口就 422、不是 `_finite` 的可达输入——不硬造 422 断言冒充「当没传」）。**重点断 NaN 那半的 msg**（它是唯一会绿的错） | spec G2g + 计划期核清的 pydantic 边界 |
| **R10** | **G2h 只清「指向本文件」的自引用行号**（全仓口径：`grep -rn "spec:[0-9]" tests/ frontend/`）；跨文件引用与「日期+基线」实盘快照档**例外保留**；四份**计划正文**的行号清扫是 docs 活、归控制者（不派实现者） | spec G2h 口径逐字 |
| **R11** | **流④诚实降级（计划期已核清数据流）**：`state_payload()` 无 manual_groups 字段 ⇒ edit 侧断「12 行不受扰」+ wall 侧状态断言（未归计数翻）+ wrapper 文件断言（`manual_groups.json` 落临时 work 目录），**不造假联动断言** | spec G3b 流④的「须在计划期核清后钉死」——已核清 |
| **R12** | **跨页五流全部真 `<a>` 整页导航**：唯一 `page.goto` = 起航进 `edit.html`；wall 的会话由 edit 页**真实输入路径**建立（`wall.spec.mjs` B 段的直连 `fetch /api/session` + reload 是单页 spec 的合法捷径，跨页不用——导航本身就是被测物） | spec G3b「无 page.goto 作弊」 |
| **R13** | **项数链变更点全集** = T3（calib 81→82，G2e +1）、T5 **出口 A1**（82→83；A2 只修不加牙、钉不动）、T9（新门，跑出 N 后钉）。**除这三个点外，任何任务不许动 `EXPECTED_ITEMS`**；T3/T5 若因复审改了断言条数，钉数跟着实跑值走 | 「同任务同步改钉数」的可执行化；防别的任务顺手消红 |

---
### Task 1: `count-spec-items.py` 泛化（G1a）

**Files:**
- Modify: `tests/e2e/count-spec-items.py`（整份重写为参数化版本）

**Interfaces:**
- Consumes: 三个 wrapper 模块的既有导出——`test_edit_bay_e2e`（`_fixture` / `_free_port` / `_wait_up` / `NAME` / `NODE` / `PW` / `SPEC`）、`test_wall_e2e`（`_wall_fixture`）、`test_calib_e2e`（`_make_video`）。
- Produces: `--spec bay|wall|calib`（缺省 `bay`，行为与旧版一致）；stdout 打 `✅ 全过 N 项` 尾部 + stderr 尾部。Task 2 / Task 10 依赖它对三页各取一次真值。

- [ ] **Step 1: 重写 `tests/e2e/count-spec-items.py`**

```python
# 取三页 spec 的真实项数：复刻夹具隔离（临时目录 + 空闲端口 + cwd=临时目录），
# stdout/stderr 尾部都打（spec 的 ok 抛错走 stderr，只打 stdout 失败时是空白+rc=1）。
# 绝不指向 7860 / 7861。
# 用法：python count-spec-items.py [--spec bay|wall|calib]（缺省 bay = 旧版行为不变）。
# 夹具组合按 spec 选（R15b③，复用各 wrapper 的 helper，不另写第二份）：
#   bay   = _fixture（12 行 events.json + calibration.json + tags.json，无真视频）
#   wall  = _fixture + _wall_fixture（shooters.json / traces / shots / thumbs）
#   calib = _make_video 真视频，**不预置 calibration.json**（否则「生成标定」空转）
# 文件名不匹配 test_*.py ⇒ pytest 不收集，全量数不受影响。
import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))            # R15b①：夹具模块要 from bbh.models import …
sys.path.insert(0, str(ROOT / "tests" / "e2e"))  # R15b①：wall/calib wrapper 顶层
                                         # `from test_edit_bay_e2e import …` 的互 import


def _load(module_name):
    spec = importlib.util.spec_from_file_location(
        module_name, ROOT / "tests" / "e2e" / f"{module_name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build_fixture(which, root, mods):
    """按 spec 组夹具——三套 wrapper helper 的组合，别在别处写第二份。"""
    if which == "bay":
        mods["bay"]._fixture(root)
    elif which == "wall":
        wall = mods["wall"]
        wall._fixture(root)          # 墙夹具的地基 = 剪辑台 12 行
        wall._wall_fixture(root)     # shooters.json / traces / shots / thumbs
    elif which == "calib":
        calib = mods["calib"]
        calib._make_video(root / "videos" / f"{mods['calib'].NAME}.mp4")


def main():
    ap = argparse.ArgumentParser(description="取一份 e2e spec 的真实项数")
    ap.add_argument("--spec", choices=("bay", "wall", "calib"), default="bay")
    args = ap.parse_args()
    which = args.spec

    mods = {"bay": _load("count_bay_e2e"),
            "wall": _load("count_wall_e2e"),
            "calib": _load("count_calib_e2e")}
    mod = mods[which]                     # SPEC / NODE / PW / _wait_up 都取自它
    # wall/calib wrapper 顶层的 `from test_edit_bay_e2e import …` 走 sys.path 正常
    # 导入（与 _load 的副本是两个模块对象——helper 是纯函数，双份执行无副作用）。

    tmp = Path(tempfile.mkdtemp(prefix=f"count_items_{which}_"))
    _build_fixture(which, tmp, mods)
    port = mod._free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    code = ("import uvicorn\n"
            "from bbh.web.app import create_app\n"
            f"uvicorn.run(create_app(), host='127.0.0.1', port={port}, "
            "log_level='warning')\n")
    log = tmp / "server.log"
    lf = open(log, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen([sys.executable, "-c", code], cwd=tmp, env=env,
                            stdout=lf, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    lf.close()
    try:
        mod._wait_up(base, proc, log)
        senv = dict(os.environ, BBH_BASE=base,
                    BBH_VIDEO=str(tmp / "videos" / f"{mod.NAME}.mp4"),
                    BBH_PLAYWRIGHT=mod.PW.as_uri(), PYTHONIOENCODING="utf-8")
        r = subprocess.run([mod.NODE, str(mod.SPEC)], env=senv,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300)
        print(f"=== [{which}] returncode:", r.returncode)
        print("=== stdout tail ===")
        print(r.stdout[-1500:])
        print("=== stderr tail ===")
        print(r.stderr[-500:])
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
```

> ⚠ 写成 `main()` + `if __name__` 是为了让 `--help` 可用；`BBH_VIDEO` 三路都指向 `<tmp>/videos/<NAME>.mp4`（bay/wall 不需要真文件——旧版就这么跑；calib 路真文件由 `_make_video` 造好）。

- [ ] **Step 2: 三路各跑一次（build 先行）**

```bash
cd frontend && npm run build && cd ..
./.venv/Scripts/python.exe -X utf8 tests/e2e/count-spec-items.py            # 期望尾部 ✅ 全过 191 项
./.venv/Scripts/python.exe -X utf8 tests/e2e/count-spec-items.py --spec wall   # ✅ 全过 43 项
./.venv/Scripts/python.exe -X utf8 tests/e2e/count-spec-items.py --spec calib  # ✅ 全过 81 项
```

Expected: 三路 rc=0、尾部分别 191 / 43 / 81。**把三个数字抄进任务报告**（T2 的钉数来源）。
⚠ 若 wall 路报 `ModuleNotFoundError: test_edit_bay_e2e` ⇒ 是 R15b① 的第二行 `sys.path.insert` 没生效，别去改 wrapper。

- [ ] **Step 3: 全量不受影响（工具不被 pytest 收集）**

```bash
./.venv/Scripts/python.exe -X utf8 -m pytest -q --collect-only 2>&1 | tail -3
```

Expected: 收集数与 BASE 一致（636 collected = 635 passed + 1 skipped 的基线——工具文件名不匹配 `test_*.py`，多一个都算收集到了）。不必跑全量本体。

- [ ] **Step 4: commit**

```bash
git add tests/e2e/count-spec-items.py
git commit -m "test(e2e): count-spec-items.py 泛化 --spec bay|wall|calib（R15b 三条配方落实：双 sys.path/stdout+stderr 都打/按 spec 组夹具）"
```

---

### Task 2: 三个 wrapper 钉死期望项数（G1b）

**Files:**
- Modify: `tests/e2e/test_edit_bay_e2e.py`（测试函数尾 + `import re`）
- Modify: `tests/e2e/test_wall_e2e.py`（同形）
- Modify: `tests/e2e/test_calib_e2e.py`（同形）

**Interfaces:**
- Consumes: Task 1 报告的三个真值（191 / 43 / 81）。
- Produces: 每个 wrapper 模块级常量 `EXPECTED_ITEMS = <int>`——T3 / T5 / T9 会同任务改它，其余任务**不许碰**（R13）。

- [ ] **Step 1: 三份 wrapper 各加钉（同一形状，改常量值即可）**

每份 wrapper 的 `import` 区补 `import re`；模块级（`SPEC = …` 那行附近）加：

```python
# 项数门（E1 G1b）：spec 实跑项数钉死在这里。改 spec 条数的任务必须**同任务**改这里——
# 改了 spec 没改钉 = 本文件红（这正是门的目的）。真值来源：count-spec-items.py。
EXPECTED_ITEMS = 191        # test_edit_bay_e2e.py 用 191；wall 用 43；calib 用 81
```

测试函数体里，紧跟 `assert "✅ 全过" in r.stdout, …` 之后插：

```python
    m = re.search(r"✅ 全过 (\d+) 项", r.stdout)
    assert m, f"spec 没跑到收尾汇总（stdout 尾）：\n{r.stdout[-800:]}\n{r.stderr[-500:]}"
    assert int(m.group(1)) == EXPECTED_ITEMS, (
        f"spec 项数变了：实际 {m.group(1)} 项 ≠ 钉死 {EXPECTED_ITEMS} 项"
        "——若本任务确实改了 spec 条数，请同任务改 EXPECTED_ITEMS")
```

- [ ] **Step 2: 三份 wrapper 各跑一次（绿）**

```bash
./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_edit_bay_e2e.py tests/e2e/test_wall_e2e.py tests/e2e/test_calib_e2e.py -q
```

Expected: `3 passed`。

- [ ] **Step 3: 突变自证（改错一位 ⇒ 红、文案含两个 N；验完还原）**

只做**一份代表**（bay——三份代码同形，机制同一）：

```bash
# 临时把 test_edit_bay_e2e.py 的 EXPECTED_ITEMS 改成 190 →
./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_edit_bay_e2e.py -q
# 期望红，AssertionError 文案含「实际 191 项 ≠ 钉死 190 项」→ 还原成 191 →
./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_edit_bay_e2e.py -q   # 回绿
```

把红文案原文抄进任务报告。

- [ ] **Step 4: commit**

```bash
git add tests/e2e/test_edit_bay_e2e.py tests/e2e/test_wall_e2e.py tests/e2e/test_calib_e2e.py
git commit -m "test(e2e): 三 wrapper 钉死 spec 项数门 EXPECTED_ITEMS 191/43/81——项数掉了必红，红文案带两个 N"
```

---
### Task 3: calib spec 四处硬化（G2a 换牙 ×2 + G2b 注释 ×4 + G2c 判别串 + G2e 图标 +1 项）

**Files:**
- Modify: `tests/e2e/calib.spec.mjs`
- Modify: `tests/e2e/test_calib_e2e.py`（`EXPECTED_ITEMS` 81 → 82，**同任务**）
- 临时触碰（突变自证，用完还原）：`frontend/src/calib/CalibWizard.vue` + `bbh/static`（重建）

**Interfaces:**
- Consumes: T2 的 `EXPECTED_ITEMS = 81`。
- Produces: calib spec 项数 **82**；G2a/G2b/G2c 三处语义不变、判据更硬。

- [ ] **Step 1: 动手前先 grep 行号**（基线值会漂：miss `:76` / done `:310` / waitForResponse `:276`、`:299`、`:372`、`:384` / ⑮(4) 守卫 `:492`）

```bash
grep -n "miss.first().waitFor\|done.first().waitFor\|waitForResponse\|回到第①步" tests/e2e/calib.spec.mjs
```

- [ ] **Step 2: G2a 两处换牙（等长替换，不计项）**

处 1（miss toast，越界抽帧那块）：

```js
  // 改前：await miss.first().waitFor();
  // 改后（G2a：裸 .first().waitFor() 走 Playwright 默认 30s、超时报 TimeoutError
  //  而非本 spec 文案。miss 是全新页面后的第一条 toast，无残留可撞 ⇒ 定位器不动）：
  await waitFor(async () => (await miss.count()) > 0, "越界抽帧应弹提示（取帧失败）");
```

处 2（done toast，完成标定那块）：

```js
  // 改前：await done.first().waitFor();
  // 改后（done 已按文案过滤，同款换预算）：
  await waitFor(async () => (await done.count()) > 0, "「完成标定」应弹提示");
```

- [ ] **Step 3: G2b 四处 `waitForResponse` 就地注释定案（不换）**

四处（S refine / V refine / build / del）的 `page.waitForResponse(...)` 行**上方**各加同一形状注释（就地按当处上下文微调「写端点」名）：

```js
  // G2b/E23 定案：这里保持 Playwright 默认 30s——它是**文档化的第二预算**
  // （waitForResponse 没有 ms 参数；等不到响应会挂到测试超时 = 可读的红）。
  // 统一成 10s 会让「服务端 30s 才回」从超时变假绿。E2 真机跑过后复访。
```

- [ ] **Step 4: G2c ⑮(4) 守卫换判别串（项数不变）**

```js
  // 改前：const t4 = page.locator(".el-message").filter({ hasText: "回到第①步" });
  // 改后（G2c/R5：旧过滤串撞前一条「已取消补段，回到第①步」的子串（3s 内仍在屏）
  //  ⇒ 守卫可在点击前为真。带全角冒号的「回到第①步：」只属于 addSeg 的新 toast；
  //  cancel toast 结尾无冒号。⑬ 段的 t1 不动——其前无 cancel toast，改它=顺手加固）：
  const t4 = page.locator(".el-message").filter({ hasText: "回到第①步：" });
```

（只改过滤器那一行；后续 `waitFor(...)` 与断言逐字不动。）

- [ ] **Step 5: G2e VideoCamera 图标判据（+1 项）**

在 ① 真挂载块（四步条 / 顶栏标签 / 路径框 placeholder 那几条 eq 之后）插入：

```js
  // G2e/R7：顶栏路径框的 prefix 图标（main.js 全局注册 @element-plus/icons-vue，
  // 名字写错的失败形态是图标空白且 errs 为空 ⇒ 普通断言捉不到）。
  // 边界如实：这条只捉「图标整个没渲染」，捉不到「渲染成了另一个图标」。
  eq(await page.locator("#app .topbar .el-input__prefix svg").count(), 1,
     "顶栏路径框的 VideoCamera 图标应真渲染（恰 1 个）");
```

- [ ] **Step 6: 同任务改钉 81 → 82**（`test_calib_e2e.py` 的 `EXPECTED_ITEMS`）

- [ ] **Step 7: build + 跑绿**

```bash
cd frontend && npm run build
cd .. && ./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_calib_e2e.py -q
```

Expected: `1 passed`（spec 实跑 `✅ 全过 82 项`——若数出别的数，按实跑改钉并在报告说明，别凑 82）。

- [ ] **Step 8: 三颗牙各做突变自证（验完还原，`git status --porcelain` 见净）**

1. **G2a**（断言侧突变，不 build）：临时把处 1 的条件改成 `(await miss.count()) > 999` → 单跑 calib e2e ⇒ 红，红文案是本 spec 的「越界抽帧应弹提示（取帧失败）」而不是 Playwright `TimeoutError` → 还原。把两句红文案各抄一份进报告（改前 TimeoutError 形态可以从旧 spec 跑一次取，或直接引用已知项 16 的记载）。
2. **G2c**（spec 侧探针对拍，不 build）：⑮(4) 守卫的修复价值 = 「点击**前**不被 cancel toast 预先满足」。临时在守卫的 `waitFor` **前**插一行 `console.log("probe", await t4.count());`：
   - 用**新**过滤串（`回到第①步：`）跑一次 ⇒ probe 输出 `0`（cancel toast 不被匹配）；
   - 临时换回**旧**过滤串（`回到第①步`）再跑一次 ⇒ probe 输出 `≥1`（cancel toast 预先满足——这就是被修掉的假判据形态）；
   - 删探针、保留新过滤串。两次 probe 输出抄进报告。
   （不用「注释掉 addSeg 的 toast」做突变——那会先红在 ⑬ 段的 t1 守卫，证明不了 ⑮(4) 这处。）
3. **G2e**（被测物突变，需 build）：临时把 `CalibWizard.vue` 顶栏路径框的 `<template #prefix><el-icon><VideoCamera /></el-icon></template>` 整段注释 → build → 单跑 ⇒ `VideoCamera` 那条 eq 红 → 还原 + 重建 build。

还原后重跑 Step 7 确认回绿。

- [ ] **Step 9: commit**

```bash
git add tests/e2e/calib.spec.mjs tests/e2e/test_calib_e2e.py bbh/static
git commit -m "test(calib): E1 存量硬化——两处裸 waitFor 换本文件预算牙/四处 waitForResponse 注释定案/⑮(4) 守卫换带冒号判别串/VideoCamera 图标 +1 项（钉数 81→82）"
```

---

### Task 4: wall G 段 warning 断言收紧（G2d，项数不变）

**Files:**
- Modify: `tests/e2e/wall.spec.mjs`（G 段一处；F 段与「未归 8」等其余断言逐字不动）

**Interfaces:**
- Consumes: T2 的 `EXPECTED_ITEMS = 43`（本任务不动它——项数不变，跑绿即证）。
- Produces: G 段护栏 toast 判据从「≥1（可被 F 遗留满足）」收紧为「比点击前恰 +1」。

- [ ] **Step 1: 改 G 段（先 grep 定位：`护栏警示 toast`）**

```js
  // 改前：
  //   await page.locator("#app .tray button:has-text(\"归人\")").click();  // 人名还是 三分
  //   await waitFor(async () => (await page.locator(".el-message--warning").count()) >= 1,
  //                 "护栏警示 toast");
  // 改后（G2d/R6：F 段那对邻对（00:01.2↔00:01.6）自己也产 warning，3s 内仍在屏
  //  ⇒ 旧判据可被 F 的遗留 toast 预先满足。收紧发生在 waitFor 的条件里——
  //  ok/eq 条数不动，项数不变）：
  const warn0 = await page.locator(".el-message--warning").count();
  await page.locator("#app .tray button:has-text(\"归人\")").click();  // 人名还是 三分
  await waitFor(async () =>
    (await page.locator(".el-message--warning").count()) === warn0 + 1,
    "护栏警示 toast（比点击前恰多一条）");
```

（后续 `.el-message--warning` 的 `ok(... includes("护栏"))` 逐字不动。）

- [ ] **Step 2: 跑绿（build 先行照旧）**

```bash
cd frontend && npm run build
cd .. && ./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_wall_e2e.py -q
```

Expected: `1 passed`（`✅ 全过 43 项`——项数没变本身就是 R6 的验证）。

- [ ] **Step 3: 突变自证（spec 侧，不 build）**

临时把 G 段的两张选卡改成非邻对（如 `await cards.nth(0).click(); await cards.nth(5).click();`——0.4s 邻对才触发护栏）→ 单跑 ⇒ 红，红文案是本 spec 的「护栏警示 toast（比点击前恰多一条）」→ 还原 → 重跑回绿。

- [ ] **Step 4: commit**

```bash
git add tests/e2e/wall.spec.mjs
git commit -m "test(wall): G 段护栏 toast 判据收紧——快照后恰 +1（F 段邻对的遗留 warning 不再预先满足；项数不变）"
```

---
### Task 5: `natural={0,0}` 窗口定性（G2f，spike 内嵌、双出口）

**Files:**
- Create（**不入库**）：临时 spike 脚本（放系统临时目录，跑完即删；代码全文在 Step 1）
- Modify（**仅「可复现」出口**）：`frontend/src/calib/PointMarker.vue` + `tests/e2e/calib.spec.mjs` + `tests/e2e/test_calib_e2e.py`（钉 82→83）

**Interfaces:**
- Consumes: calib 夹具（`_make_video` 真视频 640×360；显示宽 ~720px ⇒ CSS 盒 ≠ 帧尺寸，比值 1.125 有分辨力）；`calibkit.imgPoint` 的退化语义（`natural?.w || w`——`natural={0,0}` 时按 CSS 盒换算）。
- Produces: 双出口结论（可复现 ⇒ 修 + 牙；不可复现 ⇒ 零改动 + 已知项记录进任务报告，控制者折进计划正文）。

- [ ] **Step 1: 写 spike（一次性脚本 + 驱动，放临时目录，不进仓库）**

```js
// spike-natural.mjs —— 理论窗口：<img> @load 之前 natural={0,0}，点帧容器会按 CSS 盒换算。
// 复现口径（R8）：真实交互路径——普通 .click()，不 force、不 evaluate 直击。
// 判定：服务端落点若 ≈ CSS 像素（位置 × CSS 盒）而非帧像素（位置 × 640/360）⇒ 撞上了窗口。
const { chromium } = await import(process.env.BBH_PLAYWRIGHT || "playwright");
const BASE = process.env.BBH_BASE, VIDEO = process.env.BBH_VIDEO;
let hits = 0, unreachable = 0;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const browser = await chromium.launch();
try {
  for (let round = 1; round <= 5; round++) {
    const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
    await page.goto(`${BASE}/calib.html`, { waitUntil: "domcontentloaded" });
    await page.locator("#app .topbar input").first().waitFor({ timeout: 30000 });
    const inp = page.locator("#app .topbar input").first();
    await inp.fill(VIDEO); await inp.press("Enter");
    // ⚠ 必须等会话登记完再抽帧——Enter 之后立刻点「抽帧」会 400（会话还没注册视频路径）
    for (let i = 0; i < 100; i++) {
      const s = await (await fetch(`${BASE}/api/state`)).json();
      if (s.videoPath === VIDEO) break;
      await sleep(100);
      if (i === 99) throw new Error("会话一直没登记视频路径");
    }
    await page.locator("#app .panel input").first().fill("1.2");
    await page.locator("#app .panel input").first().press("Enter");
    await page.locator("#app .panel button:has-text('抽帧')").first().click();
    // ↓ 关键：不等帧图加载完——「下一步」一可点立刻进步②并点（理论窗口就在这）
    await page.locator("#app .footer-nav button").nth(1).click();
    const wrap = page.locator("#app .panel .frame-wrap");
    await wrap.waitFor();
    const box = await wrap.boundingBox();
    const FX = 0.5, FY = 0.25;
    // ⚠ img 未加载时 wrap 可能高 0 ⇒ Playwright 的 hit-test 会拒绝点击
    //   （「点击不可达」本身就是一种定性结果：真实交互路径撞不上这个窗口）
    try {
      await wrap.click({ position: { x: box.width * FX, y: box.height * FY } });
    } catch (e) {
      unreachable++;
      console.log(`round ${round}: 点击不可达（${String(e).split("\n")[0]}；box=${JSON.stringify(box)}）`);
      await page.close();
      continue;
    }
    await sleep(400);
    const st = await (await fetch(`${BASE}/api/calib/state`)).json();
    if (!st.points.length) { console.log(`round ${round}: 没接住点击（无点）`); }
    else {
      const [x, y] = st.points[0];
      const css = { x: box.width * FX, y: box.height * FY };      // 退化换算的期望
      const nat = { x: 640 * FX, y: 360 * FY };                   // 正确换算的期望
      const dCss = Math.hypot(x - css.x, y - css.y), dNat = Math.hypot(x - nat.x, y - nat.y);
      const fellBack = dCss < dNat;      // 落点更贴近 CSS 换算 ⇒ 撞上 natural={0,0}
      if (fellBack) hits++;
      console.log(`round ${round}: 落点(${x.toFixed(1)}, ${y.toFixed(1)}) ` +
        `CSS期望(${css.x.toFixed(1)}, ${css.y.toFixed(1)}) 帧期望(${nat.x}, ${nat.y}) ` +
        `⇒ ${fellBack ? "❌ 撞上窗口（CSS 退化）" : "✓ 帧像素换算"}`);
    }
    await page.close();
  }
  console.log(`SUMMARY: ${hits}/5 撞上 natural={0,0} 窗口；${unreachable}/5 点击不可达`);
} finally { await browser.close(); }
```

驱动（照 calib wrapper 的服务形状，`cwd=临时目录`；spike 文件也放同一临时目录）：

```bash
./.venv/Scripts/python.exe -X utf8 - <<'PY'
import os, subprocess, sys, tempfile
from pathlib import Path
sys.path.insert(0, "."); sys.path.insert(0, "tests/e2e")
from test_calib_e2e import NAME, NODE, PW, ROOT, _free_port, _make_video, _wait_up
tmp = Path(tempfile.mkdtemp(prefix="spike_nat_"))
_make_video(tmp / "videos" / f"{NAME}.mp4")
port = _free_port(); base = f"http://127.0.0.1:{port}"; log = tmp / "server.log"
code = ("import uvicorn\nfrom bbh.web.app import create_app\n"
        f"uvicorn.run(create_app(), host='127.0.0.1', port={port}, log_level='warning')\n")
with open(log, "w", encoding="utf-8") as fh:
    proc = subprocess.Popen([sys.executable, "-c", code], cwd=tmp, stdout=fh,
                            stderr=subprocess.STDOUT,
                            env=dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8"))
try:
    _wait_up(base, proc, log)
    env = dict(os.environ, BBH_BASE=base, BBH_VIDEO=str(tmp / "videos" / f"{NAME}.mp4"),
               BBH_PLAYWRIGHT=PW.as_uri(), PYTHONIOENCODING="utf-8")
    r = subprocess.run([NODE, str(tmp / "spike-natural.mjs")], env=env,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600)
    print(r.stdout); print("rc =", r.returncode)
finally:
    proc.terminate(); proc.wait(timeout=10)
PY
```

- [ ] **Step 2: 跑 spike + 机理证据（evaluate 直击一轮，只记报告不作判据）**

Expected 输出 `SUMMARY: hits/5`。另做一轮 `page.evaluate` 直击 `dispatchEvent(new MouseEvent("click", …))` 的版本（同一脚本里加个 flag 即可）——它撞不撞都只写进报告作机理解释（blob 图解码在哪个任务边界完成），**不参与可复现判定**。

- [ ] **Step 3: 按出口分流（修与牙的门槛不同——R8 的精化）**

**修的门槛 = hits ≥ 1/5**（实测可达就值得守卫）；**牙的门槛 = hits ≥ 4/5 且 0 不可达**（稳定复现）。窗口若是概率性的（1–3/5），把它写成 spec 断言 = 造一条**永久 flaky 项**（新图先 @load、守卫放行、eq(0) 假红）——那比没有牙更糟。

**出口 A1（hits ≥ 4/5 ⇒ 稳定复现 ⇒ 修 + 牙，钉 82→83）**：

`PointMarker.vue` 的 `onClick` 加守卫（`ElMessage` 从 `element-plus` import）：

```js
function onClick(ev) {
  // G2f：@load 之前 natural={0,0}，imgPoint 会退化按 CSS 盒换算（帧坐标系统性错）
  // ⇒ 帧没加载完不接点（提示 + 不发请求）。修的是实测可达的失败场景（spike N/5），
  //   不是理论窗口——不可复现的加固没有牙，不做。
  if (!(props.natural?.w > 0 && props.natural?.h > 0)) {
    ElMessage.info("帧还没加载完，稍等一下再点");
    return;
  }
  emit("click-point", imgPoint(ev.clientX, ev.clientY,
                               ev.currentTarget.getBoundingClientRect(), props.natural));
}
```

`calib.spec.mjs` 的 ⑧ 坐标基准块**之前**（即步②首点之前）加牙（+1 项）：

```js
  // G2f 的牙：帧图没加载完时点帧容器 ⇒ 提示 + 服务端不落点（守卫的判据）。
  // （spike 已实测该窗口可达；这条在正常路径不会触发——帧图 waitFor 过了才走到这，
  //  所以这条用「重新抽帧后立刻点」造一次窗口：抽帧会重置 natural={0,0}。）
  await page.locator("#app .panel button:has-text('抽帧')").first().click();
  const wrapEarly = page.locator("#app .panel .frame-wrap");
  const boxEarly = await wrapEarly.boundingBox();      // 步② 的既有换算器可复用
  await wrapEarly.click({ position: { x: boxEarly.width * 0.5, y: boxEarly.height * 0.25 } });
  const earlyPts = (await calibState()).points.length;
  eq(earlyPts, 0, "帧未加载完的点击不许落服务端（G2f 守卫）");
  await waitFor(async () => await page.locator("#app .panel img").first()
                  .evaluate((el) => el.complete && el.naturalWidth > 0),
                "等帧图真正加载完再继续");
```

⚠ 插入位置与既有变量的复用（`wrap`/`box`/`at` 的定义处在 ⑧ 之前与否）以盘上代码为准——**若 ⑧ 的换算器定义在本插入点之后，就自起变量名（如上 `wrapEarly/boxEarly`），别挪既有代码**。同任务改钉 `EXPECTED_ITEMS` 82 → 83。突变自证：临时删守卫 → build → 该 eq 红（落了点）→ 还原重建。

**出口 A2（1–3/5 或有不可达轮 ⇒ 概率性 ⇒ 只修不加牙，钉数保持 82）**：

`PointMarker.vue` 的守卫照 A1 加（实测可达就值得守卫），但 **spec 牙与钉数都不动**——把概率性窗口写成 `eq(earlyPts, 0)` 是给仓库埋一条永久 flaky 项（新帧先 @load ⇒ 守卫放行 ⇒ 点落服务端 ⇒ 假红）。任务报告记：spike N/5 原始轮次输出 + 「守卫无自动化牙（窗口概率性），守卫的存在性由代码评审守」。

**出口 B（hits = 0/5 ⇒ 不可复现 ⇒ 零改动）**：

不动任何代码/规格。任务报告写明：轮次结果原文、evaluate 直击的机理证据、「人手与 Playwright 真实路径均不可达（blob 图在 click 的 actionability 检查期间已 @load）」的结论。控制者折进计划「交付后已知项」。

- [ ] **Step 4: 跑（出口 A1/A2 才有这步）**

```bash
cd frontend && npm run build
cd .. && ./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_calib_e2e.py -q
```

Expected: `1 passed`（A1 为 `✅ 全过 83 项`、A2 仍 82——以实跑为准改钉）。

- [ ] **Step 5: commit（A1 与 A2 各自的形状）**

A1：

```bash
git add frontend/src/calib/PointMarker.vue tests/e2e/calib.spec.mjs tests/e2e/test_calib_e2e.py bbh/static
git commit -m "fix(calib): 帧未加载完不接点（natural={0,0} 退化换算守卫，spike 稳定复现 N/5）+ spec 牙（钉数 82→83）"
```

A2（无 spec/钉数改动）：

```bash
git add frontend/src/calib/PointMarker.vue bbh/static
git commit -m "fix(calib): 帧未加载完不接点（natural={0,0} 退化换算守卫，spike 概率性可达 N/5——守卫无自动化牙，存在性由评审守）"
```

（出口 B 无 commit——本任务零改动，报告即交付物。）

---

### Task 6: 非有限 `offset` 收口（G2g，**唯一可碰 `bbh/web/` 的任务**）

**Files:**
- Modify: `bbh/web/api_events.py`（`delete_events` 一处 + import）
- Modify: `bbh/web/api_marks.py`（`post_feedback` 一处 + import）
- Test: `tests/test_web_marks.py` / `tests/test_web_events.py`（各追加一个用例）

**Interfaces:**
- Consumes: `api_gen._finite`（语义冻结，`from .api_gen import _finite` 零搬运——R9）。
- Produces: 两端点的 offset 语义 = 「非有限一律当没传、保留原值」；pytest 牙四种输入（None / NaN / +inf / −inf）× 两端点。

- [ ] **Step 1: 写失败测试（先红）**

`tests/test_web_marks.py` 追加（`client` / `env` fixture 既有）：

```python
def test_verdict_nonfinite_offset_is_treated_as_absent(env):
    """E1 G2g：非有限 offset 一律当没传（api_gen._finite 冻结口径）。
    重点断 NaN 那半的 msg——旧行为是 200 + 成功文案里藏 00:00.0（比 500 隐蔽）。
    字符串形状在 float|None 的 pydantic 门口就 422，不是 _finite 的可达输入（R9）。"""
    for bad in (float("nan"), float("inf"), float("-inf"), None):
        r = client.post("/api/feedback/0:12.1",
                        json={"verdict": "goal", "offset": bad})
        assert r.status_code == 200, f"offset={bad!r} 不该 5xx"
        assert "00:12.1" in r.json()["msg"], f"offset={bad!r} 的 msg 应显示原时刻"
        assert "00:00.0" not in r.json()["msg"], f"offset={bad!r} 的 msg 不许藏 00:00.0"
```

`tests/test_web_events.py` 追加：

```python
def test_delete_nonfinite_offset_is_treated_as_absent(env):
    """E1 G2g 同款：DELETE /events 的 offset 收口（旧行为 ±inf → 500，无半写状态）。"""
    r = client.request("DELETE", "/api/events",
                       json={"keys": ["00:12.1"], "offset": float("inf")})
    assert r.status_code == 200, "±inf 不该 500（当没传）"
    assert _read(env, "deleted.json") == ["00:12.1"], "键按原始时刻落 deleted.json"
    assert "00:00.0" not in r.json()["msg"]
    r2 = client.request("DELETE", "/api/events",
                        json={"keys": ["01:12.1"], "offset": float("nan")})
    assert r2.status_code == 200
    assert _read(env, "deleted.json") == ["00:12.1", "01:12.1"], "NaN 同样当没传"
```

- [ ] **Step 2: 跑红**

```bash
./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_web_marks.py tests/test_web_events.py -q
```

Expected: 两条新用例 FAIL（marks 的 NaN 断言红在 msg 藏 `00:00.0`；events 的 inf 红在 500）。既有用例照旧绿。

- [ ] **Step 3: 实现（两文件同形，各两行）**

`bbh/web/api_marks.py`：import 区（`from .api_events import _rows_and_index` 那行附近）加 `from .api_gen import _finite`；`post_feedback` 里的 `body.offset or 0.0` 改成 `_finite(body.offset) or 0.0`。
`bbh/web/api_events.py`：import 区加 `from .api_gen import _finite`；`delete_events` 里的 `body.offset or 0.0` 改成 `_finite(body.offset) or 0.0`。

（`api_gen.py` 一行不改；`_finite(None)=None → or 0.0 → 0.0`、`_finite(0.0)=0.0 → 0.0`——有限值含 0 全部保持原语义。）

- [ ] **Step 4: 跑绿 + 回归门**

```bash
./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_web_marks.py tests/test_web_events.py tests/test_web_gen.py -q
```

Expected: 全绿（`test_web_gen` 是 `_finite` 语义冻结的回归门——本任务没碰它，它红就是改错了地方）。

- [ ] **Step 5: 突变自证（验完还原）**

临时把 `api_marks.py` 的 `_finite(body.offset) or 0.0` 改回 `body.offset or 0.0` → 只跑 marks 新用例 ⇒ NaN 那轮红（msg 含 `00:00.0`）→ 还原 → 回绿。红文案抄进报告。

- [ ] **Step 6: commit**

```bash
git add bbh/web/api_events.py bbh/web/api_marks.py tests/test_web_marks.py tests/test_web_events.py
git commit -m "fix(web): 裁决/删除两端点 offset 收口 _finite（NaN/±inf/None 一律当没传——NaN 旧行为是成功文案里藏 00:00.0，±inf 是 500）+ pytest 牙四种输入"
```

---
### Task 7: spec 自引用行号清扫（G2h）

**Files:**
- Modify: `tests/e2e/edit_bay.spec.mjs`（一处注释；**全仓扫描只此一处**，wall/calib 已核为 0）

**Interfaces:**
- Consumes: R10 口径（只清「指向本文件」的自引用；跨文件引用与实盘快照档例外保留）。
- Produces: spec 文件里不再有会漂的行号自引用。**计划正文里的行号清扫是控制者的 docs 活，不在本任务**（若复审在本任务提交里发现 docs 改动 = 越界）。

- [ ] **Step 1: 全仓扫描（口径固定）**

```bash
grep -rn "spec:[0-9]" tests/ frontend/ --include="*.mjs" --include="*.js" --include="*.vue" --include="*.py"
```

Expected: 只有 `tests/e2e/edit_bay.spec.mjs:587` 一处（`（spec:87 那条注释记的是同一件事）`）。若扫出**别处**的新增自引用（E1 期间其他任务引入），一并按同法清；若是跨文件引用（指向别的文件的行号）——**保留不动**并记进报告。

- [ ] **Step 2: 改那一处**

```js
  // 改前：// 拦截（Playwright 的 hit-test 一直等到 30s 超时）。这正是本文件 `rowCell(i)`
  //        // 存在的原因（`spec:87` 那条注释记的是同一件事）。
  // 改后：// 拦截（Playwright 的 hit-test 一直等到 30s 超时）。这正是本文件 `rowCell(i)`
  //        // 存在的原因（`rowCell(i)` 定义处那条注释记的是同一件事——行号会漂，认符号不认行号）。
```

（`rowCell(i)` 定义处的注释讲的就是「点时刻列而非整行」的同一件事，盘上已核。）

- [ ] **Step 3: 跑相关（改的是注释，跑步数即证没碰断言）**

```bash
./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_edit_bay_e2e.py -q
```

Expected: `1 passed`（`✅ 全过 191 项`——项数不变，钉不动）。

- [ ] **Step 4: commit**

```bash
git add tests/e2e/edit_bay.spec.mjs
git commit -m "test(e2e): edit_bay spec 清掉会漂的自引用行号（spec:87 → 认 rowCell 符号不认行号；全仓口径扫描仅此一处）"
```

---

### Task 8: 显示面审计（G3a，「屏上时刻同源」判据升级）

**Files:**
- Create（**不入库**，交控制者折进计划正文）：审计表报告（放 SDD 工作区）
- Modify（**仅出口①**）：无语义争议的同源破坏所在的 `frontend/src/**` + 对应 spec/selftest 牙

**Interfaces:**
- Consumes: 三页全部 `.vue` 模板 + `shared/time.js`（`fmtT` / `tsec` / `dispT` 家族）。
- Produces: 审计表（每面一行）；出口三分——①无语义争议的同源破坏当场修 + 牙；②语义争议 → 决策清单交用户（**不替用户定**）；③已同源 → 表里 ✓。修了显示面且 spec 断言条数变化 ⇒ 同任务改钉（R13 之外的例外须报告控制者裁定后进钉）。

- [ ] **Step 1: grep 圈范围（圈不住的靠人工——这正是本任务的方法论）**

```bash
grep -rn "dispT\|tsec\|toFixed\|fmtT\|mm:ss\|mm: ss\|：秒\|秒）\|{\{" frontend/src/edit frontend/src/wall frontend/src/calib frontend/src/shared --include="*.vue" --include="*.js" | grep -iv "^.*import\|comment" > /tmp/display-faces.txt
wc -l /tmp/display-faces.txt
```

（产物只是工作清单；**grep 圈不住** `releaseT.toFixed(2) + "s"` 之外的同类——`toFixed` 能圈住这条，但圈不住「不叫这些名字的时刻渲染」。）

- [ ] **Step 2: 人工过三页全部模板，逐面填表**

每面一行，列：**页面 / 组件 / DOM 面（选择器或文案）/ 数据源表达式 / 同源与否 / 定性**。已知的第一个候选（上一批 park 的原案，**大概率进出口②**）：

| 页面 | 组件 | DOM 面 | 数据源 | 同源 | 定性 |
|---|---|---|---|---|---|
| edit | BasisPanel | 出手人行 `releaseT.toFixed(2)+"s"` | `row.shooter.releaseT`（原始秒） | ✗（表格/卡头吃 offset，它不吃） | **出口②候选**：吃不吃偏移是语义决策（ shooters 回扫时刻的基准是什么），交用户裁 |

其余面逐个补齐：edit 表格时刻列 / 卡头时刻 / 切片播放器时刻 / 页脚汇总；wall 卡 caption / 未归 tag / guardline 读数 / 组头；calib 帧标签 `mmss` / 补段按钮起点 / 弹窗 calibT——**每个渲染「秒/时刻」的模板节点都要有一行**（包括 ✓ 的，✓ 也是结论）。

- [ ] **Step 3: 按出口分流**

- **出口①（无语义争议的同源破坏）**：判据 = 「同类面在别处都已同源、唯它落单」且修复不改变任何语义决策（例：某面用了原始 `row.t` 而同页同类面全用 `dispT`）。修 + 对应 spec/selftest 加牙 + 同任务处理项数。
- **出口②（语义争议）**：写进决策清单（问题 / 两个方向的后果 / 我的倾向），**不改代码**。控制者收拢后交用户。
- **出口③（已同源）**：表里 ✓，零动作。

- [ ] **Step 4: 跑（出口①才有改动）**

```bash
cd frontend && node selftest.mjs && npm run build
cd .. && ./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/ -q
```

Expected: 全绿；项数若变，钉数同任务改并在报告注明（控制者复核进 R13 的变更点清单）。

- [ ] **Step 5: commit（出口①）**

```bash
# 按 brief 点名的具体路径 add；出口②/③零代码改动 ⇒ 无 commit，报告即交付物
```

---
### Task 9: 跨页 e2e（G3b，`crosspage.spec.mjs` + `test_crosspage_e2e.py`）

**Files:**
- Create: `tests/e2e/crosspage.spec.mjs`
- Create: `tests/e2e/test_crosspage_e2e.py`

**Interfaces:**
- Consumes: `test_edit_bay_e2e`（`NAME/NODE/PW/ROOT/_fixture/_free_port/_untouched/_wait_up`）+ `test_wall_e2e`（`_wall_fixture`）+ `test_calib_e2e`（`_make_video`）——三套 helper 组合，**不写第二份**。
- Produces: 第五个 e2e wrapper（项数门从第一天就钉——先跑出 N 再回填 `EXPECTED_ITEMS`，R2）；spec 条款「顶栏 tab 三页共用条」到此归宿。

- [ ] **Step 1: 写 `tests/e2e/crosspage.spec.mjs`**

```js
// 跨页 e2e（E1 G3b）：三页联动。全部真 <a> 整页导航（R12——导航本身就是被测物，
// 唯一 page.goto = 起航进 edit.html）。夹具（wrapper 侧组合）：剪辑台 12 行 +
// 墙 shooters/traces + 标定真视频，三页共享同一后端会话。
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
const stateApi = async () => (await fetch(`${BASE}/api/state`)).json();
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

  // —— 流①：tabbar 矩阵（三页各开一次；当前页高亮；真 <a> 导航）——
  await page.goto(`${BASE}/edit.html`, { waitUntil: "domcontentloaded" });
  await page.locator("#app .topbar input").first().waitFor({ timeout: 30000 });
  eq(await page.locator("nav.tabbar a").count(), 3, "tabbar 应有三条链");
  eq(await page.locator("nav.tabbar a.on").getAttribute("href"), "edit.html",
     "剪辑台页 tabbar 高亮指向自己");

  await page.locator('nav.tabbar a[href="wall.html"]').click();
  await page.locator("#app .stage-hint").waitFor({ timeout: 30000 });   // 无会话空态
  eq(await page.locator("nav.tabbar a.on").getAttribute("href"), "wall.html", "墙页高亮");

  await page.locator('nav.tabbar a[href="calib.html"]').click();
  await page.locator("#app .topbar .el-tag").first().waitFor({ timeout: 30000 });
  eq(await page.locator("nav.tabbar a.on").getAttribute("href"), "calib.html", "标定页高亮");

  await page.locator('nav.tabbar a[href="edit.html"]').click();
  await page.locator("#app .topbar input").first().waitFor({ timeout: 30000 });

  // —— 流②：会话共享（edit 真实填路径载入 → <a> 去 wall 同视频 → 去 calib 同视频）——
  const input = page.locator("#app .topbar input").first();
  await input.fill(VIDEO);
  await input.press("Enter");
  await waitFor(async () => (await page.locator("#app .el-table__row").count()) === 12,
                "剪辑台应载入 12 行");
  eq((await stateApi()).videoPath, VIDEO, "会话已登记视频路径（服务端真值）");

  await page.locator('nav.tabbar a[href="wall.html"]').click();
  await page.locator("#app .wallgrid .card").first().waitFor({ timeout: 30000 });
  ok((await page.locator("#app .wallgrid .card").count()) === 12,
     "墙页同视频直达：12 张卡（shooters 夹具），无需重填路径");
  eq((await page.locator("#app .topbar .el-tag").nth(1).innerText()).trim(), "未归 12",
     "墙页未归计数 = 12（会话共享）");

  await page.locator('nav.tabbar a[href="calib.html"]').click();
  await page.locator("#app .topbar .el-tag").first().waitFor({ timeout: 30000 });
  const calibInput = page.locator("#app .topbar input").first();
  await waitFor(async () => (await calibInput.inputValue()) === VIDEO,
                "标定页路径框应回填会话视频路径");
  eq((await page.locator("#app .topbar .el-tag").first().textContent()).trim(), "未标定",
     "标定页此刻应「未标定」（流⑤才生成）");

  // —— 流③：edit→wall 深链（依据卡的「在出手人墙中查看 →」，BasisPanel 的 el-link）——
  await page.locator('nav.tabbar a[href="edit.html"]').click();
  await waitFor(async () => (await page.locator("#app .el-table__row").count()) === 12,
                "回剪辑台仍是 12 行");
  // rowCell 同款：点「时刻」列（整行中心会落在标签列下拉上——edit spec 的既有教训）
  await page.locator("#app .el-table__row").nth(0).locator("td").nth(2).click();
  const deep = page.locator('#app a[href="/wall.html"]');
  await waitFor(async () => (await deep.count()) === 1, "选中行后依据卡应出现墙深链");
  ok((await deep.innerText()).includes("在出手人墙中查看"), "深链文案");
  await deep.click();
  await page.locator("#app .wallgrid .card").first().waitFor({ timeout: 30000 });
  ok(page.url().endsWith("/wall.html"), "深链真导航到了墙页");

  // —— 流④：归人回写（wall 托盘归人 → wrapper 断 manual_groups.json；
  //      edit 侧如实断「12 行不受扰」——state_payload 无 manual_groups 字段，R11，不造假联动）——
  const cards = page.locator("#app .wallgrid .card");
  await cards.nth(0).click();
  await cards.nth(1).click();
  await page.locator("#app .tray .el-select").click();
  // el-select 下拉 teleport 到 body，别限定 #app（Plan B 教训）
  await page.locator('.el-select-dropdown__item:has-text("三分")').first().click();
  await page.locator('#app .tray button:has-text("归人")').click();
  await waitFor(async () =>
    (await page.locator("#app .topbar .el-tag").nth(1).innerText()).trim() === "未归 10",
    "归人 2 球后墙页未归 10");
  eq((await page.locator("#app .topbar .el-tag").nth(1).innerText()).trim(), "未归 10",
     "wall 侧状态面：未归 12 → 10");

  await page.locator('nav.tabbar a[href="edit.html"]').click();
  await waitFor(async () => (await page.locator("#app .el-table__row").count()) === 12,
                "归人写盘后回剪辑台，12 行不受扰");
  eq(await page.locator("#app .el-table__row").count(), 12,
     "edit 侧如实面：行数不受扰（edit 页不读 manual_groups）");

  // —— 流⑤：calib 联动（抽帧→点筐口→生成 → 已标定 + 同 work 目录 calibration.json；
  //      回 edit 12 行不受扰）——
  await page.locator('nav.tabbar a[href="calib.html"]').click();
  await page.locator("#app .topbar .el-tag").first().waitFor({ timeout: 30000 });
  const timeIn = page.locator("#app .panel input").first();
  await timeIn.fill("1.2"); await timeIn.press("Enter");
  await page.locator("#app .panel button:has-text('抽帧')").first().click();
  await waitFor(async () => (await page.locator("#app .panel img").count()) === 1, "抽帧出帧图");
  await waitFor(async () => await page.locator("#app .panel img").first()
                  .evaluate((el) => el.complete && el.naturalWidth > 0), "帧图真加载");
  await page.locator("#app .footer-nav button").nth(1).click();          // 步②
  const wrap = page.locator("#app .panel .frame-wrap");
  await wrap.waitFor();
  const box = await wrap.boundingBox();
  const at = (fx, fy) => wrap.click({ position: { x: box.width * fx, y: box.height * fy } });
  await at(0.6719, 0.4278);   // 筐口左端（与 calib spec 同夹具同三点——已实测可生成）
  await at(0.9375, 0.4278);   // 筐口右端
  await at(0.8047, 0.5556);   // 球
  await waitFor(async () => (await calibState()).canBuild === true, "三点落到服务端");
  await page.locator("#app .footer-nav button").nth(1).click();          // 步③
  await waitFor(async () =>
    (await page.locator("#app .panel button:has-text('生成标定')").count()) === 1,
    "步③ 出现「生成标定」");
  const rfBuild = page.waitForResponse((r) => r.url().includes("/api/calib/build"));
  await page.locator("#app .panel button:has-text('生成标定')").click();
  eq((await rfBuild).status(), 200, "生成标定 200（E26：写路径先等响应再轮询）");
  await waitFor(async () => (await calibState()).ready === true, "生成后 ready=true");
  eq((await page.locator("#app .topbar .el-tag").first().textContent()).trim(), "已标定",
     "标定页顶栏标签翻「已标定」");

  await page.locator('nav.tabbar a[href="edit.html"]').click();
  await waitFor(async () => (await page.locator("#app .el-table__row").count()) === 12,
                "生成标定后回剪辑台");
  eq(await page.locator("#app .el-table__row").count(), 12,
     "生成标定不碰 events——12 行不受扰");

  ok(errs.length === 0, `页面报错：${errs.join(" | ")}`);
  console.log(`✅ 全过 ${pass} 项`);
} finally {
  await browser.close();
}
```

（静态估算约 **17** 项——**以实跑为准**，别凑数：`waitFor` 不计项。）

- [ ] **Step 2: 写 `tests/e2e/test_crosspage_e2e.py`**

```python
"""跨页 e2e（E1 G3b）：三页联动 + 真 <a> 整页导航。

夹具 = 三套既有 helper 的组合（不写第二份）：剪辑台 12 行（_fixture）+
墙 shooters/traces（_wall_fixture）+ 标定真视频（_make_video 写到 events.json
指名的同一个 videos/<NAME>.mp4）⇒ 三页共享同一后端会话、calib 可真抽帧。

项数门（G1b 同形）：EXPECTED_ITEMS 先以占位数跑一次、按实跑回填（R2）。
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from test_edit_bay_e2e import (NAME, NODE, PW, ROOT, _fixture, _free_port,
                               _untouched, _wait_up)
from test_wall_e2e import _wall_fixture
from test_calib_e2e import _make_video

SPEC = Path(__file__).resolve().parent / "crosspage.spec.mjs"
EXPECTED_ITEMS = 0        # ← 首跑后回填实跑值（R2：建门那一次不算中间态）


@pytest.fixture(scope="module")
def crossbay(tmp_path_factory):
    if not NODE:
        pytest.skip("没有 node——跨页 e2e 需要 node + playwright")
    if not PW.exists():
        pytest.skip(f"没有 {PW}——先 cd frontend && npm i -D --no-save playwright")
    root = tmp_path_factory.mktemp("crosspage")
    _fixture(root)                                                   # 剪辑台 12 行
    _wall_fixture(root)                                              # 墙 shooters/traces
    _make_video(root / "videos" / f"{NAME}.mp4")                     # 标定真视频
    # ⚠ E8 同款坑在这里复现：bay `_fixture` 会预置 work/<NAME>/calibration.json，
    #   而它会让流②的「未标定」断言恒红、流⑤的 calibration.json 落盘断言空转
    #   （夹具自己写的那份 = 断言永真）。删掉它——行的来源是 events.json，不是标定档。
    (root / "work" / NAME / "calibration.json").unlink(missing_ok=True)
    # ⚠ 删除后若首跑发现 edit 载不出 12 行（行依赖标定档的假设被证伪），**停下报告**，
    #   别悄悄把预置加回去——那等于把流⑤的牙拔了换绿。
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    code = ("import uvicorn\n"
            "from bbh.web.app import create_app\n"
            f"uvicorn.run(create_app(), host='127.0.0.1', port={port}, "
            "log_level='warning')\n")
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
            f"e2e 动了仓库根的 {f}（红线 6 档一个 git 副本都没有）"
            "——隔离没兜住，停下查")


def test_crosspage_e2e(crossbay):
    env = dict(os.environ, BBH_BASE=crossbay["base"], BBH_VIDEO=crossbay["video"],
               BBH_PLAYWRIGHT=PW.as_uri(), PYTHONIOENCODING="utf-8")
    r = subprocess.run([NODE, str(SPEC)], env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    assert r.returncode == 0, f"跨页 e2e 失败：\n{r.stdout}\n{r.stderr}"
    assert "✅ 全过" in r.stdout, f"跨页 e2e 没跑到收尾汇总：\n{r.stdout}\n{r.stderr}"
    m = re.search(r"✅ 全过 (\d+) 项", r.stdout)
    assert m, f"spec 没跑到收尾汇总（stdout 尾）：\n{r.stdout[-800:]}\n{r.stderr[-500:]}"
    assert int(m.group(1)) == EXPECTED_ITEMS, (
        f"spec 项数变了：实际 {m.group(1)} 项 ≠ 钉死 {EXPECTED_ITEMS} 项"
        "——若本任务确实改了 spec 条数，请同任务改 EXPECTED_ITEMS")
    # 流④真落盘：wall 归人写了 manual_groups.json（临时 work 目录，不是仓库根）
    assert (crossbay["root"] / "work" / NAME / "manual_groups.json").exists(), \
        "归人没落盘——流④没验到真写"
    # 流⑤真落盘：生成标定写 calibration.json（同一临时 work 目录 = 「同目录」的夹具形状保证）
    assert (crossbay["root"] / "work" / NAME / "calibration.json").exists(), \
        "标定没落盘——流⑤没验到真写"
```

- [ ] **Step 3: 首跑取 N → 回填钉数 → 跑绿**

```bash
cd frontend && npm run build
cd .. && ./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_crosspage_e2e.py -q
```

Expected: 首跑因 `EXPECTED_ITEMS = 0` 红（红文案含「实际 N 项 ≠ 钉死 0 项」——**这正是门在工作**）→ 把 N 抄进 `EXPECTED_ITEMS` → 重跑 `1 passed`。N 与静态估算不一致时**以实跑为准**并数一遍 spec 里的 ok/eq（扣 `eq` 定义体内那一处）自查差异来源，写进报告。

- [ ] **Step 4: 项数门突变自证（同 T2 形，改错一位 ⇒ 红）**

临时把 `EXPECTED_ITEMS` 改成 N+1 → 单跑红（文案含两个 N）→ 还原回绿。

- [ ] **Step 5: commit**

```bash
git add tests/e2e/crosspage.spec.mjs tests/e2e/test_crosspage_e2e.py
git commit -m "test(e2e): 跨页 e2e 五流——tabbar 矩阵/会话共享/依据卡深链/归人回写(如实面)/calib 联动，全程真 <a> 整页导航；wrapper 钉项数门"
```

---
### Task 10: 收尾验证批（零代码改动，只跑与报数）

**Files:** 无（**只读验证**；跑出红就是新任务，不在本任务里改）

**Interfaces:**
- Consumes: 前面九个任务交付的全部内容。
- Produces: 真值报告（下面各数）交回控制者，用于回填计划正文与台账。

- [ ] **Step 1: 纯 JS 层 + 产物重建幂等**

```bash
cd frontend && node selftest.mjs            # 期望 ✅ 42 通过（E1 无 selftest 增项）
npm run build
cd .. && git status --porcelain bbh/static  # 期望空：入库产物 == 源码产物
```

- [ ] **Step 2: 三处项数门（count 工具三路）**

```bash
./.venv/Scripts/python.exe -X utf8 tests/e2e/count-spec-items.py            # bay 191
./.venv/Scripts/python.exe -X utf8 tests/e2e/count-spec-items.py --spec wall   # wall 43
./.venv/Scripts/python.exe -X utf8 tests/e2e/count-spec-items.py --spec calib  # calib 82（T5 出口 A1 则 83；A2 仍 82）
```

（crosspage 的项数由它自己的 wrapper 门守，不进本工具——三路是 bay/wall/calib。）

- [ ] **Step 3: 五个 e2e wrapper 全绿**

```bash
./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/ -q
```

Expected: `5 passed`。

- [ ] **Step 4: 三页冒烟（E30 口径——必须覆盖 `BBH_BASE` + 临时 cwd）**

```bash
# 另起临时服务（cwd=临时目录、空闲端口），然后：
cd frontend && BBH_BASE=http://127.0.0.1:<空闲端口> node smoke.mjs
```

Expected: `✅ 冒烟通过：三页可开、Vue 真挂载、tab 高亮正确、可跳转、无 JS 报错`。

- [ ] **Step 5: 全量（整批只跑这一次）**

```bash
./.venv/Scripts/python.exe -X utf8 -m pytest -q
```

Expected: **635 + 新增 pytest 数 / 1 skipped**。新增来源：T6 两条（marks + events）+ T9 一条 wrapper = **638/1**（若 T5 走出口 A 无 pytest 变化；**以实跑为准报数**）。

- [ ] **Step 6: 净树 + 红线复核**

```bash
git status --porcelain                          # 期望只剩 ?? ui_prefs.json
git log --oneline d410228..HEAD                 # 复核：实现提交全部点名路径、无 docs 夹带
```

`work/**` 按 E30③ 的前后清点比对另证（跑前 `find work -type f -printf '%p %s %T@\n' | sort` 存一份，跑完再取一份，逐字相同）；仓库根 6 档 mtime 不变。

- [ ] **Step 7: 报数（不 commit）**

| 项 | 真值 |
|---|---|
| `node selftest.mjs` | |
| 三处项数（bay / wall / calib） | / / |
| crosspage 项数 N | |
| 五个 e2e wrapper | `5 passed` |
| 冒烟 | 三页绿 |
| 全量 pytest | |
| 工作树 | 只剩 `?? ui_prefs.json` |
| T5 出口结论（A 修 / B 已知项）+ T8 审计表 / 决策清单 | |

---

## 本计划不做的（承接表）

| 不做的事 | 去处 / 理由 |
|---|---|
| 真机验收（09-11_3 全流程、同参比对、work/ 清点）、双向兼容、旧界面回退 | **E2**（问题在 E1 交付后再问用户） |
| vrender 慢放锚/跟球链非 0 偏移实测 → 语义决策 | **E2**（决策归用户） |
| fps 后端探测下发（R35）、生成完成预览位自动播放 | **E2 条件项** |
| wall 的 `assign/unassign` 不经 `wallSeq` 守卫 | 用户已裁 **chore 批** |
| 产品侧锁不对称（`/existing` 不取 `video_lock`、`os.replace` 不重试） | 用户已裁**另开后端任务**（Plan D 已知项 19） |
| `segListMd` 服务端口径不一致（主标定重建回空串） | **后端**待办（Plan D 已知项 9） |
| 四份**计划正文**里的行号清扫 | **控制者 docs 活**（R10，不派实现者） |
| G2b 的 30s 统一化 / `:36` 挂载屏障调整 | 定案**不换**（R4）；E2 真机跑过后复访 |
| 显示面审计的语义争议项（如 `releaseT` 吃不吃偏移） | **决策清单交用户裁**（T8 出口②），不替用户定 |

---

## 验证配方（速查）

```bash
# 1) 纯 JS 层 + 产物（必须先于任何 e2e/冒烟）
cd frontend && node selftest.mjs && npm run build
# 2) 项数门三路
cd .. && ./.venv/Scripts/python.exe -X utf8 tests/e2e/count-spec-items.py [--spec wall|calib]
# 3) 单个 e2e wrapper（例）
./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_calib_e2e.py -q
# 4) 三页冒烟（⚠ 必须 BBH_BASE 覆盖到空闲端口 + 临时 cwd——裸跑默认指向用户 7860，E30①）
cd frontend && BBH_BASE=http://127.0.0.1:<空闲端口> node smoke.mjs
# 5) 全量（收尾只跑一次）
./.venv/Scripts/python.exe -X utf8 -m pytest -q
```

**红线复述**（每条都有前科，别试）：
- 起服务一律 `uvicorn.run(create_app(), host, port)` + **临时 cwd** + 空闲端口；**绝不** `python -m bbh.web`；**绝不**裸跑 `node smoke.mjs`。
- 仓库根 6 档（`calibration*.json/png` ×4 / `tags.json` / `ui_prefs.json`）**绝不能动**。
- 用户自己的 7860 实例：不杀、不占、不进任何探活/端口扫描名单。
- 停后台进程按句柄 `terminate()`，**绝不** `taskkill //IM`。
- 实现者只 `git add` 本任务 brief 点名的路径，**永不** `git add -A` / `git add .`；docs 归控制者。
- 分支纪律：只 commit 到 `feature/vertical-highlight`，**不擅自 merge / push**。
- 实现者**不许**派子代理；**绝不**并行派两个实现者。
- 突变自证用完必须还原，`git status --porcelain` 见净（`?? ui_prefs.json` 除外）后才许 commit。

---

## 收尾自查（控制者成文时已核，随计划存档）

计划成文时我逐条**核过**的事实（不是推断），行号会漂、用前先 grep：

1. **项数真值**：bay 191 / wall 43 / calib 81 来自 Plan D 收尾实测与台账（E31 链终值 81）；T3 的 82 = 81 + G2e 一条（G2a/G2c 不计项——等长替换与过滤器替换都不增删 ok/eq）。
2. **toast 判别串**（R5）：`CalibWizard.vue` 的 `addSeg` 弹「回到第①步：抽挪动后画面的帧，重新走一遍标点」（带全角冒号）、`cancelSeg` 弹「已取消补段，回到第①步」（无冒号）——`回到第①步：` 只匹配前者。`calib.spec.mjs` 里 `回到第①步` 出现在 `:327`（⑬ 的 t1）与 `:492`（⑮(4) 的 t4）两处过滤器 + `:331` 一处 `ok` 文案——**只改 `:492`**。
3. **wall F 段确实产 warning**：F 的 `00:01.2/00:01.6` 是 0.4s 邻对（已知项 1 的记载 + G 段注释「与已归的 00:01.6 也是 0.4s 邻对」）⇒ 快照 +1 的判据有真险可守。
4. **`state_payload()`（`api_session.py:162`）逐键读过**：`videoPath/lastVideo/loaded/hint/rows/duration/summaryMd/goalStatsMd/tags/deleted/flags/feedback/verdicts/calib` —— **没有 manual_groups**，流④的诚实降级（R11）是核清后的结论，不是猜测。
5. **`api_marks.py:16` 已有 `from .api_events import _rows_and_index`** ⇒ R9 的跨 api import 合规；`_finite` 在 `api_gen.py`，docstring 即冻结口径。
6. **pydantic 边界**（R9）：`VerdictIn.offset: float | None`——字符串 `"abc"` 422、`"5.5"` coerce 5.5、JSON 字面量 `NaN/Infinity` 可达（edit-msg 终审 M-3 实测）⇒ 四输入 = None/NaN/+inf/−inf。
7. **跨页地标全部在既有 spec 里用过**：`stage-hint`（wall A 段）、`wallgrid .card`（wall B 段）、topbar `el-tag` nth(1)=未归（wall A/C 段）、`el-table__row` 12 行（edit）、`td.nth(2)` 选行（edit `rowCell`）、`a[href="/wall.html"]`（`BasisPanel.vue` 的 `el-link`，无出手人回扫也渲染——模板核过）；el-select 下拉 teleport body（wall F 段注释）。
8. **夹具合流**：三套夹具同 `NAME = "e2e_fixture"`、同视频路径 `<root>/videos/e2e_fixture.mp4`；bay `_fixture` 的 events.json `video` 字段就指它（文件不存在也载 12 行——bay spec 今天就这么跑），`_make_video` 写真文件后 calib 可真抽帧、三页共享会话。
9. **三点坐标**：流⑤ 的 `(0.6719, 0.4278)/(0.9375, 0.4278)/(0.8047, 0.5556)` 与 calib spec 同夹具同三点（实测可得 `hoop_rect=(422,128,187,75)`、ROI 11083 命中）⇒ 生成必成。
10. **`spec:[0-9]` 全仓只有一处**（`edit_bay.spec.mjs:587`，tests/ + frontend/ 口径）——R10 的「只此一处」是扫出来的，不是假设。
11. **全量数预测 638/1** = 635/1（BASE）+ T6 两条 + T9 一条；T5 出口 A1/A2 都不加 pytest（A1 的 +1 是 spec 项数，不是 pytest 数）。
12. **count 工具的 wall 路**：`test_wall_e2e` 顶层 `from test_edit_bay_e2e import …` ⇒ **第二行 `sys.path.insert`（tests/e2e）是硬前提**（R15b①）——现版工具没有它（bay 路不需要），泛化不加必炸。

---

## 交付后已知项

（2026-09-22 收尾，控制者回填。执行台账：`.superpowers/sdd/2026-09-21-frontend-refactor-e1-hardening/progress.md`。）

### 终值表（T10 实测）

| 项 | 真值 |
|---|---|
| `node selftest.mjs` | ✅ 42 通过（无增项） |
| 三处项数门（bay / wall / calib） | 191 / 43 / 82（count 工具三路实跑） |
| crosspage 项数门 | **16**（R2 先跑后钉；ok×4 + eq×12 手数逐条吻合） |
| e2e wrapper | **4 passed**（bay/wall/calib/crosspage 各 1；计划 Step 3 写「5 passed」系笔误——把 T9「五流」误记成「五个 wrapper」，638=635+2+1 与恰一个新 wrapper 自洽） |
| 三页冒烟 | ✅（临时服务 57904 + BBH_BASE 覆盖 + 临时 cwd，按句柄 terminate） |
| 全量 pytest | **638 passed / 1 skipped**（== 预测逐字吻合，321s） |
| 工作树 | 只剩 `?? ui_prefs.json`；根 6 档两查不变；`d410228..HEAD` 实现提交全点名路径、无 docs 夹带 |

### T5 出口 B 定性记录（G2f，零改动）

spike 五轮 hits 0/5、0 不可达；evaluate 直击轮 0/1 且 dispatch 前 `img.complete=true、naturalWidth=640`。复审独立闭合时序链：`nextDisabled(step0)=!frameReady` 翻 enabled 与步① img 挂载同一 flush、两步共用同一 `frameObjUrl` ⇒ 步② 可达时 `@load` 必已完成——**真实路径 0/5 是结构性结论（最早可达时刻 > @load 完成时刻），非统计**。按 R8 不修不加牙。

已知项残留（E2 观察）：
1. **步② 无自愈的结构性残留**：`natural` 只由步① FramePicker `@load` 填、步② PointMarker img 无 `@load`、`frameReady` 在 `r.blob()` 返回即翻（不等解码）⇒ 真实大帧解码拖过「下一步」则整步② 按 CSS 盒错档。最小自愈修法（步② img 补 `@load` 回填 natural）已留档 `task-5-report.md`，**E2 真机（09-11_3 真实大帧）观察点②首点落点后再裁**——工作区删除后以本条为准。
2. lastVideo 预填后直点「抽帧」400（服务端重启会话空、`@change` 门不发 POST）——UX 边角 park，E1 后端零改动红线内不修。
3. spike 复跑注记（临时目录 ui_prefs.json 需先删否则会话登记伪失败）随 spike 归档。

### T8 显示面审计（G3a）——审计表与决策清单（全表存档）

出口分布：**① 0 处 ｜ ② 3 项（交用户裁）｜ ③ 47 面 ✓**。方法：按显示面枚举（凡渲染「秒/时刻」的节点逐面核时间源）+ 服务端文本面后端取证。50 面计数 = edit 29 + wall 8 + calib 12 + shared 1。

**edit 页**（唯一有 offset 概念的页——「同源」= 与 `dispT(row, offset)` 家族同轴）：

| # | 组件 | DOM 面 | 数据源 | 同源 | 定性 |
|---|---|---|---|---|---|
| 1 | EventTable | 时刻列 | `dispT(row, offset)` | ✓ | ③；`sortable prop="t"` 按原始串排——均匀平移保序、夹负只影响并列 00:00.0 |
| 2 | BasisPanel | 卡头时刻 | `dispT(row, offset)` | ✓ | ③（R34 修） |
| 3 | BasisPanel | 出手人行「出手人 ● X.XXs」 | `row.shooter.releaseT.toFixed(2)+"s"`（原始视频轴） | ✗ | **②决策 #1** |
| 4 | BasisPanel | 误标采样 tooltip | `flags.thumb/cross.join("、")`（原始键） | ✗ | ③ 已冻结（键即存储身份） |
| 5 | BasisPanel | 反馈摘要行 | `_fp_summary` 纯计数 | ✓ | ③ |
| 6 | BasisPanel | 置信度弹层数字 | 服务端分数/信号值 | ✓ | ③（非时刻面） |
| 7 | ClipPanel | 红线标签「进球 mm:ss.s」 | `dispT(row, offset)` | ✓ | ③ |
| 8 | ClipPanel | 切片源 `/media/clip?t=` | `tsec(row, offset)` | ✓ | ③（切点=显示时刻契约） |
| 9 | ClipPanel | 前后秒输入 | `durOf` | ✓ | ③（时长非时刻） |
| 10 | TimelineStrip | 圆点 tooltip | `dispT(row, props.offset)` | ✓ | ③ |
| 11 | TimelineStrip | 圆点定位 | `tsec(row, offset)/duration` | ✓ | ③（R9） |
| 12 | TimelineStrip | 右端刻度 | `fmtT(tlDur)` | ✓ | ③（时长面） |
| 13 | TimelineStrip | 左端刻度「0:00」 | 静态字面量 | ✓ | ③（轴零点，原型同款） |
| 14 | EditBay | 删除确认 | `dispT(row, offset.value)` | ✓ | ③ |
| 15 | EditBay | 撤销 toast | `dispT(row, getOffset())` | ✓ | ③（selftest 钉） |
| 16 | EditBay | del/verdict 服务端 toast | 服务端按请求体 offset 平移的 `t_disp` | ✓ | ③（第二腿已闭合） |
| 17 | EditBay | miss toast「已记录漏检 mm:ss.s（t 秒）」 | 服务端回显输入（原始轴） | ✗ | ③ 已冻结 + **②-邻接决策 #3**（输入基准未决） |
| 18 | EditBay | 已校准指示器 | `offset.toFixed(1)` | ✓ | ③（显示的就是 offset 本身） |
| 19 | EditBay | 状态带 jobmsg（回扫跑动时） | 服务端 job.msg 含原始键（`shooter.py:1094/1226`） | ✗ | **②决策 #2（本次新发现）** |
| 20-29 | EditBay/GenDialog/BulkDelete/Help/Offset/EventTable | 检测按钮 pct、顶栏计数、页脚汇总、漏检 placeholder、批量清单 `dispT`、gstats/glog/进度、静态文案、缩略图 title、裁决列 | 计数/静态/`dispT` 族/无时刻内容 | ✓ | ③ |

**wall 页**（整页无 offset 概念——口径 = Plan C 冻结的原始键族）：#30-32 caption 族（未归卡/角标/chips）与 #33 guardline 读数均原始键 ✗*（wall 自身冻结决策就是「原始键即显示」故归 ③；#33「同族冻结」系引申——若 #1/#2 用户裁「吃 offset」，#33 随族重开）；#34 回扫进度 scanmsg **②决策 #2**（与 #19 合并）；#35-37 计数/组头/toast ✓ ③。

**calib 页**（整页原始视频轴——口径 = fmtT 唯一格式源 + 服务端串逐字直渲）：#38-49 全 ✓ ③（帧标签/补段按钮 `fmtT(frameStamp)` 同源、主段静态、段行服务端串、已存档 `fmtT(calibT)`、抽帧失败 toast 自洽回显等）。

**shared**：#50 `time.js` fmtT/parseT/tsec/dispT 族 = 唯一格式与换算源（selftest 13-49 钉死族行为）；其余 shared/*.js 零时刻渲染。

复审 Minor 补记：表漏 `BasisPanel.vue:99`（`row.reason`）与 `:104`（`raw` pre）两节点——内容为筐心偏移 %/证据点数/above_span 等 **X.Xs 时长**与像素取证，时长语义非视频轴、三出口均不受影响（纯表不完整，结论无误）。

**出口②决策清单（「时刻轴口径」包，三条打包交用户——单点平移会造成跨页新不同源）：**

1. **#1 BasisPanel `releaseT.toFixed(2)+"s"` 吃不吃 offset**（上一批 park 原案）。A（吃）：同卡两时刻同轴、提前量读数不变；代价=前端一行+牙，但出手域整族（墙 caption、GIF 锚、慢放锚）全原始轴，跨页反而新增不同源。B（不吃）：与旧界面 1:1（`app.py:1428`）、与整族冻结口径一致；代价=校准≠0 时同屏两轴并存（现状）。**实现者倾向 B**——真要平移应整族一起决策（墙页 caption 同上 offset），归 E2 真机验收通看时定。
2. **#2 回扫进度文本里的原始键**（edit jobmsg / wall scanmsg，`shooter.py:1094/1226` 直渲——调用点判据看不见的一族，本任务方法论直接产出）。A（平移显示）：跑扫对照无歧义；代价=**后端改动**（job API 传 offset + worker 换算 + 牙），届时**须单开任务**、不在任何前端任务顺手做；且回扫主场 wall 页无 offset 概念，同一文本两页两口径更乱。B（保持）：进度文本=「正在处理哪个工作项」的身份标识，瞬态非决策面。**实现者倾向 B**。
3. **#3 漏检补录的输入轴**（面已冻结、输入基准未决）。A（输入按显示轴折算 raw 后存）：调优数据与检测轴恒对齐；代价=后端 MissIn 加 offset 折算、提示语改口、旧界面无此折算（行为变更）。B（保持现状）：1:1 旧界面；代价=用户若照校准后显示抄时刻，misses 系统性偏移一个 offset。**实现者倾向 B**——三条里唯一「错了会污染调优数据」的，值得用户知情拍板一次。

> **裁决（2026-09-25，用户）：三条均取 B（保持现状），零代码改动。** 即 #1 BasisPanel
> 出手人时刻继续走原始视频轴（与出手域整族——墙 caption / GIF 锚 / 慢放锚——同轴），
> #2 回扫进度文本继续直渲原始键，#3 漏检补录继续按原始检测轴存（不折算 offset）。
> #33 wall guardline 随族，不重开。三条随之关闭；若日后改用非 0 校准并希望整族平移，
> 须**整族一起**重开（单点平移会造成跨页新不同源，见本清单抬头）。

### R10 行号清扫（控制者 docs 活）结果

`spec:[0-9]` 全计划文档扫描：命中全部为**跨文件引用**（计划→spec 文档 / 计划→spec.mjs，R10 明文例外）或**日期化历史记录**（edit-msg 裁定原文、E1 计划里 T7 任务定义本身）——**0 行动项**。同文档自引用定向扫描（本计划:N/本文件:N 形）零命中。口径重申（edit-msg 终审）：spec 与计划正文一律按符号指认，行号只许出现在明确标注日期+基线的实盘快照档里。

### 复审 park 项（不返工，随档）

1. T3 G2b 四处注释「waitForResponse 没有 ms 参数」字面不准（API 有 options.timeout；准确=「此处未传 timeout、走默认 30s」）——spec 原文即如此写（plan-mandated）；下次动 calib.spec.mjs 顺手带上准确措辞。
2. T3 ⑮(4) 注释「click() 返回时新节点已在 DOM」偏强（EP 渲染可能落 click resolve 后微任务）——行为零影响；`=== 快照+1` 形状隐含「toast 节点滞留远超等待窗」前提，EP 升级若真使节点离场会假红（wall G 同款前提）。
3. T6 Info-1：events 新用例只盖 inf/NaN（None/-inf 未盖）——同一表达式 marks 循环已钉死四输入，接线覆盖不重复。
4. T9 Info：commit message 比模板多个「 16」后缀（如实记钉数）；单跑 wrapper 时写守卫「ℹ 判据不可判」为 -q 子集既有表现。
5. 终审 M1：`test_crosspage_e2e.py` 的 `EXPECTED_ITEMS = 16` 行注释仍是「首跑后回填」脚手架措辞，与另三份 wrapper 标准化的门注释形状（「本文件钉数；…各自钉在…」）不一致——行为零影响，下次动该文件顺手换三份同款注释。

**终审网眼记档（E2 与后续批的预期管理素材，非缺陷）**：(a) 计数门是漂移绊网不是内容哈希——保条数的断言弱化过全部门，R13 白名单靠评审执行；(b) waitFor 条件不计项——缓解=绝大多数 waitFor 后紧跟重读同一状态的计项 ok/eq；(c) 项数是 spec 自报（`pass++`）——威胁模型是自家+评审；(d) `bbh/static` 与 `frontend/src` 新鲜度无持续门（T10 的一次性 porcelain 检查之外只剩「build 先于任何 e2e」纪律）——若要把 static 一致性变成可跑检查是新任务（chore 候选）。

### E2 承接核对

- 真机验收（09-11_3 全流程、同参比对、work/ 清点）、双向兼容、旧界面回退——问用户。
- vrender 慢放锚/跟球链非 0 偏移实测——决策归用户。
- **T5 残留①**：真机真实大帧下观察步②首点落点（natural 解码滞后）——撞上则按留档最小自愈修法裁。
- **work/ 清点判据**：用户 7860 并发会话下「逐字相同」不可判（T10 实证：2026-09-19_1 有用户并发写入、晚于 pytest 进程结束数分钟；本批零写入证据链完整）——E2 复用前先约窗口。
- **「时刻轴口径」包**（决策清单 #1/#2/#3，#33 随族）——交用户裁。

**用户裁决与收口（2026-09-25）：**

- **「时刻轴口径」三条均取 B（保持现状）**，零代码改动——见上「出口②决策清单」裁决块。**关闭**。
- **真机验收**：用户已自行跑过（近期持续在用真视频跑全流程），结论=无阻塞；后续遇到问题由用户反馈。**本项不再作为待办挂账**。
- **以下经用户裁定「必要性不高，暂不处理」，后续遇到实际问题再启**（均挂账，非遗漏）：
  T5 残留①（步②首点落点）/ vrender 慢放锚·跟球链非 0 偏移实测 / work/ 清点判据复用 /
  09-12 seg2/seg3 筐位劫持（t∈[3698,5247)）。
- E2 条件项（fps 后端探测下发 R35、生成完成预览位自动播放）一并暂缓。
- **e2e 偶发（2026-09-25 观察，与本批改动无关）**：剪辑台 e2e 在连续三次全量跑中
  失败 1 次（`test_edit_bay_e2e` → spec 子进程非 0 退出），**单独跑稳定通过**；
  同日的两次全量中含一次 673 绿。疑为全量负载下的浏览器时序抖动，未复现、未定位，
  未做任何改动。判据：若后续全量中反复出现同形态，再按真缺陷查（不要按「偶发」放过
  第二次）。

