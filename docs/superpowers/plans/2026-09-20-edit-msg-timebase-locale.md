# 三处文案时间基 + Element Plus locale Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除 toast / 裁决 toast / Ctrl+Z 撤销文案三处「文案时刻」改吃显示偏移（存储键/t_str 仍原始），顺带三页 `main.js` 装 zh-cn locale 修 `ElMessageBox` 英文按钮——Plan B 计划 I 档第 2/3 条、用户 2026-09-19 已裁口径的小任务。

**Architecture:** 服务端 `bbh/app.py` 的 `delete_events`/`mark_verdict` 各加一个**可选** `display_offset=0.0`（默认 0 = 旧界面 Gradio 调用与今天的 web 行为逐字节不变），msg 里的时刻按它单独算显示值；web 端点 `DELETE /api/events` 与 `POST /api/feedback/{key}` 的请求体加 `offset` 字段（与 `GenIn.offset` 同名同形）。前端 `actions.js` 注入 `getOffset`：撤销 label 走 `dispT`，del/verdict 请求体恒带 offset。locale 三页 `app.use(ElementPlus, { locale: zhCn })`。

**Tech Stack:** FastAPI + Pydantic（服务端）、Vue 3.4 + Element Plus 2.8（`^2.8.3`，实装 2.14.x）+ Vite（前端）、pytest TestClient（服务端测试）、Node 直测（selftest）、Playwright（e2e）。

**Spec:** `docs/superpowers/specs/2026-09-16-frontend-refactor-design.md`（三页平移总 spec）＋ Plan B 计划 `docs/superpowers/plans/2026-09-19-frontend-refactor-edit-bay.md` 的「交付后已知项」I 档第 2/3 条（用户裁定原文：口径「删除/裁决端点收显示偏移、`t_str` 保持存储键不动、msg 的时刻单独算显示值；顺带 `main.js` 装 locale 修 `ElMessageBox` 英文按钮」）。

## Global Constraints

**仓库纪律（Plan A/B/C 沿用，违反即停）：**

- **红线**：仓库根 5 档 `calibration.json` / `calibration_ref.png` / `calibration_seg2.json` / `calibration_ref_seg2.png` / `tags.json` 绝不能动；用户 `work/` 只读。
- **绝不跑 `python -m bbh.web`**；起服务一律 `uvicorn.run(create_app(), host, port)` + 临时 cwd + 空闲端口；**绝不碰 7860**；停后台进程按进程句柄 terminate，绝不用宽泛 `taskkill //IM`。
- `cd frontend && npm run build` **必须先于**任何 e2e/冒烟；改了前端必须把重建后的 `bbh/static` 一起提交（产物入库）。
- 实现者只 `git add` 本任务 Files 点名的具体路径，**永不** `git add -A` / `git add .`；commit 中文长信息；不擅自 merge/push（分支 `feature/vertical-highlight`）。
- 改一处只跑相关用例，不跑全量（全量只在 Task 4 收尾跑一次）；实现者不许派子代理；绝不并行派两个实现者。
- Python 一律 `./.venv/Scripts/python.exe -X utf8`。

**本计划特有（时间基不变式）：**

- **键恒原始**：`deleted.json`/`feedback.json` 的 `t_str`/`t`、行键、URL 里的 key 全部不吃偏移——只有 **msg/label 里的时刻字符**变显示值。
- **旧界面零扰动**：`bbh/app.py` 两个函数的新参数都带默认值；Gradio 侧调用（`delete_event` wrapper、Gradio handlers）一个字节不改。
- **「屏上显示时刻处处同源」不变式**：判据是 `dispT`/`tsec` 的**调用点**（`git grep -n 'dispT(\|tsec(' frontend/src`），不是名单。
- **请求体恒带 offset（含 0）**：与 `startGen` 的 `const out = { offset: offset.value || 0, … }`（EditBay.vue:264）同款，不做「非 0 才带」的条件展开（S2）。

## 既有契约（现场已核，实现者必读）

- **`GenIn.offset` 先例**（`bbh/web/api_gen.py:62`）：`offset: float | None = None`；消费侧 `if off:` + `A._fmt_t(max(0.0, A._parse_t(r[1]) + float(off)))`（`api_gen.py:175`，含夹负）。本计划的平移公式与它逐字同义。
- **`frontend/src/shared/time.js`**：`fmtT(sec)`（`mm:ss.s`，与后端 `time_key` 逐字同款）、`parseT(s)`、`tsec(row, offset)`（`Math.max(0, parseT(row.t) + (Number(offset) || 0))`）、`dispT(row, offset)`（`Number(offset) ? fmtT(tsec(row, offset)) : row.t`——**offset=0 原样用服务端 row.t，进位边界 1:1**）。⚠ `dispT` 的 offset 形参必须是**数字**：传 ref 对象时 `Number(ref)`=NaN ⇒ falsy ⇒ 静默回 `row.t`（踩过两次的坑）。
- **`frontend/src/edit/actions.js`**：`createActions({ api, store, undoLimit })`；内部 `raw.verdict` / `raw.del` 两个成员发请求；撤销 label 在 `setSelection`、`markVerdict`、`deleteKeys` 三处拼（`rowOf(key)?.t || key` 形状）。`rowOf(key)` 找不到回 null；selftest 夹具行**没有 `.t` 字段**（靠 `|| key` 回退）。**记法**：本档行号是**开工前**（`c64b3cf`）的实盘，Task 2 改后已漂移 ⇒ 一律按**符号**指认（E-1 口径），别回头数行号。
- **EditBay.vue 挂点**（同上，按符号指认）：`createActions(...)` 挂 `act`；`const offset = ref(0)` **在 act 之后声明**（`getOffset` 是惰性闭包，故安全）；`startGen` 是「请求体恒带 offset」的先例；单删确认框已用 `dispT(row, offset.value)`；`undoStep` 弹 `已撤销：${r.label}`；`write()` 成功后 `ElMessage.success(stripMd(s.msg))`。
- **`OffsetDialog.vue:9-16`** 的「第二条腿」注释：描述的正是本任务要闭合的缺口（今天仍显示原始时刻的三处），Task 2 改写它。
- **spec 语义选择器纪律**：两处 e2e 点消息框按钮都用语义选择器（`edit_bay.spec.mjs:826` `button:not(.el-button--primary)`、`wall.spec.mjs:235` `.el-button--primary`），装 locale 不会红；两处注释提到「没装 locale 是英文」需顺手改口（Task 3）。
- **测试现场**：`tests/test_web_events.py`（env fixture + `_read` 助手；删除调用形如 `client.request("DELETE", "/api/events", json=…)`，见 :105；夹具 3 球键 `00:12.1`/`01:12.1`/`02:12.1`）、`tests/test_web_marks.py`（env 同款 + `_work(env, "feedback.json")` 助手；裁决调用 `client.post("/api/feedback/0:12.1", json=…)`）。`tests/e2e/count-spec-items.py` 只打印 spec 的 `✅ 全过 N 项`，**无内嵌期望值**——计数变化不需要改它。
- **selftest 现场会破的 4 处 deepEqual**（请求体加 `offset: 0` 后必改）：selftest.mjs :317（feedback 整条 calls 数组）、:340、:349（撤销发回的 verdict body）、:358（DELETE body）。restore/miss/tags 的 body **不加** offset、相应断言不动。
- **基线指纹**：全量 **632 passed / 1 skipped**、selftest **34**、spec 剪辑台 **190** + 墙 **43**、HEAD `d23e5f6`。

## 裁定（S1–S8，复审时别当缺陷报）

| # | 裁定 | 理由（含错判代价） |
|---|---|---|
| S1 | 端点字段名/形状 = `offset: float \| None = None`，透传 `body.offset or 0.0` | 与 `GenIn.offset`（api_gen.py:62）同名同形；「会话态随请求体走」一族只有一种拼法 |
| S2 | 前端 del/verdict 请求体**恒带 offset（含 0）** | 与 startGen（EditBay:264）同款；条件展开 = 两种请求形状两种测试。代价：selftest 4 处 deepEqual 随契约更新为 `offset: 0`（是改期望不是放松） |
| S3 | label 回退链 `dispT(row, getOffset()) \|\| key` | 与今天 `row?.t \|\| key` 同形：row 缺失/无 `.t`（selftest 夹具）回退 key，既有断言 `"00:12.1 勾选"` 不红 |
| S4 | `getOffset: () => offset.value` 闭包注入；act 在 :31、offset 在 :132 声明 | 闭包惰性求值，动作触发时 offset 早已过 TDZ——安全。⚠ 必须 `.value`（ref 直传 = NaN falsy = 静默回原始串，A-1/A-2 与 dispT-ref 两坑同族） |
| S5 | 错误路径与「键即身份」的文案**仍原始键**：404/400 回显键、flags/miss 的 msg、墙页 caption（Plan C 冻结口径） | 用户裁的口径就是三处 + locale；错误路径回显键是调试信息。墙 caption 要吃偏移是另一个产品决定 |
| S6 | 跨界面 t_str 缝隙（旧 Gradio 把偏移烘进 `rows[1]` ⇒ 旧界面记的 feedback `t_str` 是显示值；web 记的是原始值）**不在本任务** | 既有状态（web 交付前就如此）；归 Plan E 真机验收的旧界面兼容清单 |
| S7 | locale 三页都装（calib 骨架页也装） | 三页同一形状；Plan D 重写 calib 时沿用。代价：calib 骨架多 ~1KB 产物 |
| S8 | e2e 牙插在 `edit_bay.spec.mjs` :840（Esc 测）与 :842（归零）之间，删一行→断 toast→立刻全量恢复 | 该时点 offset=1.5s（00:01.2→00:02.7）且夹具 12 行；插入段与归零/bulk 段（只断「时刻｜档位」）无状态耦合，:873 之后本就有全量恢复 |

---

### Task 1: 服务端——删除/裁决 msg 吃显示偏移（键不动）

**Files:**
- Modify: `bbh/app.py`（`delete_events` / `mark_verdict` 两个函数，约 :1724 与 :1910）
- Modify: `bbh/web/api_events.py`（`KeysIn` + `delete_events` 路由）
- Modify: `bbh/web/api_marks.py`（`VerdictIn` + `post_feedback` 路由）
- Test: `tests/test_web_events.py`（文末追加 1 例）
- Test: `tests/test_web_marks.py`（文末追加 1 例）

**Interfaces:**
- Produces: `A.delete_events(video_path, rows, keys, display_offset=0.0) -> (msg, rows2)`、`A.mark_verdict(video_path, rows, cur_row, verdict, display_offset=0.0) -> msg`（旧签名不变，新参带默认值）；`DELETE /api/events` 与 `POST /api/feedback/{key}` 请求体可选字段 `offset`（Task 2 的前端将恒带它）。
- Consumes: 既有 `_fmt_t`/`_parse_t`（app.py 模块内）。

- [ ] **Step 1: 写失败的测试**——`tests/test_web_events.py` 文末追加：

```python
def test_delete_msg_shows_calibrated_times_but_stores_raw_keys(env):
    """删除 toast 的时刻吃显示偏移（用户 2026-09-19 裁定的「三处文案时间基」其一）：
    msg 是用户视角（app.py 的既有契约），deleted.json 仍是原始键——两层时间各司其职。"""
    r = client.request("DELETE", "/api/events",
                       json={"keys": ["00:12.1"], "offset": 1.5})
    assert r.status_code == 200
    assert "00:13.6" in r.json()["msg"]            # 12.1 + 1.5（显示值）
    assert "00:12.1" not in r.json()["msg"]        # 原始键不进文案
    assert _read(env, "deleted.json") == ["00:12.1"]   # 落盘仍原始键
```

`tests/test_web_marks.py` 文末追加：

```python
def test_verdict_msg_uses_display_offset_and_keeps_t_str_raw(env):
    """裁决 toast 的时刻吃显示偏移（三处文案时间基其二）；feedback.json 的
    t_str/t 仍原始（存储键 + 匹配容差，不能就地替换——用户裁定）。"""
    r = client.post("/api/feedback/0:12.1",
                    json={"verdict": "goal", "offset": 1.5})
    assert r.status_code == 200
    assert "00:13.6" in r.json()["msg"]
    fb = _work(env, "feedback.json")
    assert fb["verdicts"][0]["t_str"] == "00:12.1"
    assert fb["verdicts"][0]["t"] == pytest.approx(12.1)
    # 改判句同样吃偏移；负偏移夹 0（与 api_gen._apply_session_rows 的平移同义）
    r2 = client.post("/api/feedback/0:12.1",
                     json={"verdict": "not_goal", "offset": -20})
    assert "00:00.0" in r2.json()["msg"]
    fb2 = _work(env, "feedback.json")
    assert fb2["verdicts"][0]["t_str"] == "00:12.1"
    assert r2.json()["verdicts"]["00:12.1"] == "not_goal"   # 行键命中不受偏移影响
```

- [ ] **Step 2: 跑它确认失败**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_web_events.py::test_delete_msg_shows_calibrated_times_but_stores_raw_keys tests/test_web_marks.py::test_verdict_msg_uses_display_offset_and_keeps_t_str_raw -v`
Expected: 2 FAIL——msg 里是原始键 `00:12.1`，断言 `"00:13.6" in msg` 不成立（Pydantic 会忽略未知字段 `offset`，请求本身 200）。

- [ ] **Step 3: 实现**——三处编辑：

① `bbh/app.py` `delete_events`（:1724）签名与 shown 计算（docstring 末尾追加一句）：

```python
def delete_events(video_path, rows, keys, display_offset=0.0):
```

docstring 末尾（`返回 (msg, rows2)。`之后）追加：

```python
    display_offset≠0 时 msg 里的时刻按它平移显示（web 端把会话校准偏移随请求体
    带上；公式与 api_gen._apply_session_rows 的平移逐字同义、含夹负）；存储与
    匹配仍用原始键。旧界面不传（它的 rows[1] 已烘偏移）⇒ 默认 0 行为不变。
```

`shown = {k: r[1] for k, r in zip(all_keys, rows or [])}   # msg 显示时间` 那行（:1736）换成：

```python
    off = float(display_offset or 0.0)
    shown = {k: (r[1] if not off else
                 _fmt_t(max(0.0, _parse_t(r[1]) + off)))    # msg 显示时间（吃偏移）
             for k, r in zip(all_keys, rows or [])}
```

② `bbh/app.py` `mark_verdict`（:1910）签名 + 显示值（**t_str/t 与 entry 全部不动**）：

```python
def mark_verdict(video_path, rows, cur_row, verdict, display_offset=0.0):
```

docstring 末尾追加一句：`display_offset≠0 时 msg 里的时刻按它平移显示；t_str（存储键/匹配容差）与 entry 仍原始——两层时间各司其职。`

`t = _parse_t(t_str)` 之后加两行：

```python
    off = float(display_offset or 0.0)
    t_disp = t_str if not off else _fmt_t(max(0.0, t + off))   # msg 显示值；t_str 仍原始
```

两个 return 的 msg 里 `{t_str}` 换成 `{t_disp}`（:1929 取消句、:1947 标记句；`entry` 里的 `"t_str": t_str` **不动**）。

③ `bbh/web/api_events.py`：

```python
class KeysIn(BaseModel):
    keys: list[str] = []
    # 显示偏移（与 GenIn.offset 同名同形，api_gen.py:62）：msg 里的时刻按它平移，
    # 存储键不动。不传 = 今天的行为。
    offset: float | None = None
```

路由 `delete_events` 里 `msg, _rows2 = A.delete_events(vp, rows, body.keys)` 改成：

```python
        msg, _rows2 = A.delete_events(vp, rows, body.keys,
                                      body.offset or 0.0)
```

④ `bbh/web/api_marks.py`：

```python
class VerdictIn(BaseModel):
    verdict: str = ""
    # 显示偏移（与 GenIn.offset 同名同形）：msg 里的时刻按它平移，t_str 仍原始。
    offset: float | None = None
```

`post_feedback` 里 `msg = A.mark_verdict(vp, rows, i, body.verdict)` 改成：

```python
        msg = A.mark_verdict(vp, rows, i, body.verdict,
                             body.offset or 0.0)
```

- [ ] **Step 4: 跑测试确认通过 + 两个文件的相关用例**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_web_events.py tests/test_web_marks.py -q`
Expected: 全绿（既有用例一个不红——不传 offset 的行为逐字节不变；新 2 例过）。

- [ ] **Step 5: Commit**

```bash
git add bbh/app.py bbh/web/api_events.py bbh/web/api_marks.py tests/test_web_events.py tests/test_web_marks.py
git commit -m "feat(web): 删除/裁决 msg 的时刻吃显示偏移——端点收 offset、t_str/deleted 仍存原始键（三处文案时间基 1/2；旧界面不传参行为不变）"
```

---

### Task 2: 前端——撤销 label 走 dispT + del/verdict 请求体带 offset + e2e 牙

**Files:**
- Modify: `frontend/src/edit/actions.js`
- Modify: `frontend/src/edit/EditBay.vue`（:31 一处）
- Modify: `frontend/src/edit/OffsetDialog.vue`（:9-16 注释块改写）
- Modify: `frontend/selftest.mjs`（4 处 deepEqual 更新 + 新增 1 例）
- Modify: `tests/e2e/edit_bay.spec.mjs`（:840/:842 之间插入一段）
- Modify: `bbh/static/**`（构建产物）

**Interfaces:**
- Consumes: Task 1 的端点 `offset` 字段；`shared/time.js` 的 `dispT`。
- Produces: `createActions({ api, store, getOffset = () => 0, undoLimit })`——`getOffset()` 必须返回**数字**（S4）。

- [ ] **Step 1: 先写失败的 selftest 例**——`frontend/selftest.mjs` 在「deleteKeys 撤销」测试（:353-364）之后插入：

```js
test("getOffset 注入：撤销 label 显示校准后时刻，del/verdict 请求体恒带 offset", async () => {
  const st = stateWith({ rows: [{ key: "00:12.1", t: "00:12.1",
                                  selection: false, tag: "" }] });
  const { calls, api: fake } = fakeApi(st);
  const act = createActions({ api: fake, store: fakeStore(st),
                              getOffset: () => 2 });

  await act.setSelection("00:12.1", true);
  const un = await act.undo();
  assert.equal(un.label, "00:14.1 勾选",
               "label 用校准后显示时刻（dispT + getOffset），不是原始 row.t");
  await act.deleteKeys(["00:12.1"]);
  assert.deepEqual(calls.find((c) => c.m === "DELETE").body,
                   { keys: ["00:12.1"], offset: 2 },
                   "删除请求体把当前校准偏移带给服务端（msg 显示值用）");
  await act.markVerdict("00:12.1", "goal");
  const fb = calls.findIndex((c) => c.url.startsWith("/api/feedback/"));
  assert.deepEqual(calls[fb].body, { verdict: "goal", offset: 2 });
});
```

- [ ] **Step 2: 跑它确认失败**

Run: `cd frontend && node selftest.mjs`
Expected: 新例 FAIL（label 是 `00:12.1 勾选`、body 无 offset）；既有 34 例照常通过。

- [ ] **Step 3: 实现 `actions.js`**——四处编辑：

① import 区加：

```js
import { dispT } from "../shared/time.js";
```

② 签名 + 回退助手（`rowOf` 定义之后）：

```js
export function createActions({ api, store, getOffset = () => 0,
                                undoLimit = UNDO_LIMIT }) {
  const stack = [];
  const rowOf = (key) =>
    (store.state?.rows || []).find((r) => r.key === key) || null;
  // 撤销 label 里的时刻：屏上显示时刻处处同源——这里也吃 offset（dispT）。
  // 行缺失 / 行没有 .t（测试夹具）回退 key，与旧 `row?.t || key` 同形（S3）。
  // ⚠ getOffset() 必须返回数字：ref 直传时 Number(ref)=NaN ⇒ falsy ⇒ 静默回
  // 原始串（dispT 的既有坑，见 shared/time.js 注释）。
  const tOf = (row, key) => (row ? dispT(row, getOffset()) : "") || key;
```

③ `raw` 两行——

```js
    verdict: (key, verdict) => api.post(`/api/feedback/${K(key)}`,
                                        { verdict, offset: getOffset() }),
```

```js
    del: (keys) => api.del("/api/events", { keys, offset: getOffset() }),
```

④ 三个 label——

`setSelection` 末段 `` `${row?.t || key} ${selection ? "勾选" : "取消勾选"}` `` 换成 `` `${tOf(row, key)} ${selection ? "勾选" : "取消勾选"}` ``；

`markVerdict` 末段 `` `${rowOf(key)?.t || key} ${label}` `` 换成 `` `${tOf(rowOf(key), key)} ${label}` ``；

`deleteKeys` 的 `` `删除 ${rowOf(keys[0])?.t || keys[0]}` `` 换成 `` `删除 ${tOf(rowOf(keys[0]), keys[0])}` ``。

- [ ] **Step 4: `EditBay.vue` :31 注入**——

```js
// getOffset 给撤销 label 与 del/verdict 请求体用（服务端 msg 的显示时刻）。
// offset 在下面（:132）才声明——闭包惰性求值，动作触发时早已过初始化，安全；
// ⚠ 必须 .value：ref 直传 = Number(ref)=NaN = 静默回原始串（S4）。
const act = createActions({ api, store, getOffset: () => offset.value });
```

- [ ] **Step 5: `OffsetDialog.vue:9-16` 注释块改写**（第二条腿闭合）——原 8 行（「规则还有**第二条腿…别以为这条规则已经处处成立。」）整体换成：

```js
// 规则还有**第二条腿：不吃 `dispT` 的文案面**——服务端 `payload["msg"]` 里的时刻
// （删除 toast 与裁决 toast）与前端 Ctrl+Z 的撤销 label。**这条腿 2026-09-20 起
// 闭合**：del/verdict 请求体带 offset（`actions.js` 的 `getOffset` 注入），服务端
// `delete_events`/`mark_verdict` 按它单独算 msg 显示值、`t_str`/键仍原始；撤销
// label 走 `dispT`。**有意保留原始键的文案面**（键即存储身份，别「顺手统一」）：
// 404/400 错误路径回显的键、flags/miss 的 msg、墙页 caption（Plan C 冻结口径）。
```

- [ ] **Step 6: selftest 4 处既有 deepEqual 随契约更新**（S2）——

:317 `body: { verdict: "not_goal" } }` → `body: { verdict: "not_goal", offset: 0 } }`；

:340 `assert.deepEqual(calls[2].body, { verdict: "goal" });` → `assert.deepEqual(calls[2].body, { verdict: "goal", offset: 0 });`

:349 `assert.deepEqual(r2.calls[2].body, { verdict: "goal" });` → 同上加 `, offset: 0`；

:358 `body: { keys: ["00:12.1", "01:12.1"] } }` → `body: { keys: ["00:12.1", "01:12.1"], offset: 0 } }`。

（restore/miss/tags 的 body 断言 :361/:371/:373/:376 **不动**——那些请求不带 offset。）

- [ ] **Step 7: e2e 牙**——`tests/e2e/edit_bay.spec.mjs` 在 :840（Esc 断言）之后、:842（「复开弹窗 ⇒「归零」」注释）之前插入（S8）：

```js
  // —— 三处文案时间基（2026-09-20 小任务）：删除 toast 的时刻 = 校准后显示值 ——
  // 删一行（真删，非上面的取消路径）：toast 由服务端 msg 出——判据双向：含显示值
  // 00:02.7、不含原始键 00:01.2。删完立刻全量恢复，夹具回 12 行；后续 bulk 段只断
  // 「时刻｜档位」，恢复重置不牵连（:873 之后本就有一次全量恢复）。
  const del0 = (await state()).deleted.length;
  await page.keyboard.press("KeyD");
  await mbox.waitFor();
  await mbox.locator(".el-message-box__btns .el-button--primary").click();
  await mbox.waitFor({ state: "hidden" });
  await waitFor(async () => (await state()).deleted.length === del0 + 1,
                "删除落盘（键=原始）");
  await waitFor(async () => (await page.locator(".el-message--success").count()) >= 1
    && (await page.locator(".el-message--success").last().innerText())
        .includes("已删除 1 球（00:02.7"), "删除 toast 出现（按文案等）");
  ok((await page.locator(".el-message--success").last().innerText()).trim()
     .includes("已删除 1 球（00:02.7")
     && !(await page.locator(".el-message--success").last().innerText())
       .includes("00:01.2"),
     "删除 toast 的时刻=校准后显示值（服务端 msg 吃 offset，原始键不进文案）");
  await menu("↩ 恢复已删");
  await waitFor(async () => (await state()).rows.length === 12
                && (await state()).deleted.length === 0, "恢复回 12 行（后续段前提）");
```

（`ok` 1 条 ⇒ spec 计数 190 → **191**；两个 `waitFor` 是状态门不计数——文件既有惯例。）

- [ ] **Step 8: 构建 + selftest + 剪辑台 e2e**

Run: `cd frontend && npm run build && node selftest.mjs`
Expected: 构建成功；selftest `✅` 收尾、**35** 例全过（34 + 新 1）。

Run: `cd .. && ./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_edit_bay_e2e.py -v`
Expected: PASSED，spec 输出 `✅ 全过 191 项`。

- [ ] **Step 9: Commit**

```bash
git add frontend/src/edit/actions.js frontend/src/edit/EditBay.vue frontend/src/edit/OffsetDialog.vue frontend/selftest.mjs tests/e2e/edit_bay.spec.mjs bbh/static
git commit -m "feat(edit): 撤销 label 走 dispT、del/verdict 请求体恒带 offset（三处文案时间基 3/3）+ 剪辑台 e2e 删除 toast 显示值牙；OffsetDialog 第二条腿注释闭合"
```

---

### Task 3: locale 三页（zh-cn）

**Files:**
- Modify: `frontend/src/edit/main.js`、`frontend/src/wall/main.js`、`frontend/src/calib/main.js`（同款两行）
- Modify: `tests/e2e/edit_bay.spec.mjs`（「取消掉它」那段注释改口，仅注释；按**文案**指认不按行号——见 E-6）
- Modify: `tests/e2e/wall.spec.mjs`（「J3. 未归卡片 → 新建区」那段注释改口，仅注释；同上）
- Modify: `bbh/static/**`（构建产物）

**Interfaces:**
- Produces: 三页 EP 组件默认文案中文化（`ElMessageBox` 确认框按钮 `确定/取消`、prompt 取消键等）。e2e 全部用语义选择器点按钮，不受影响。

- [ ] **Step 1: 三个 main.js 同款两行**——import 区（`import ElementPlus …` 之后）加：

```js
import zhCn from "element-plus/es/locale/lang/zh-cn";
```

`app.use(ElementPlus);` 换成：

```js
// locale：EP 组件默认按钮文案（不装是英文 OK/Cancel——2026-09-20 小任务）。
// e2e 点消息框一律语义选择器（primary / :not(primary)），不按文案选。
app.use(ElementPlus, { locale: zhCn });
```

calib 的 `main.js` 同款（Plan D 重写时沿用，S7）。

- [ ] **Step 2: 两处 e2e 注释改口（仅注释，断言与计数零改动）**——

`tests/e2e/edit_bay.spec.mjs` 里**「取消掉它（**不按 Esc**——Esc 那条路让给下面 ③）」起首的那段注释**（改前含「今天这两颗按钮显示的是英文默认值「OK / Cancel」…这是**既有状态、不在本批范围**，所以这里**不按文案选**…」；按文案指认，行号见 E-6）前半改口为：

```js
  // 取消掉它（**不按 Esc**——Esc 那条路让给下面 ③），并确认**一行都没删**。
  // EP 消息框的按钮文案来自 locale：本仓 main.js 已装 zh-cn（2026-09-20 小任务）
  // ⇒ 这两颗显示中文（我们自己传的 message/title 本来就是中文）。仍然**不按
  // 文案选**，按**语义**选：EP 里 `confirmButtonType` 默认 `"primary"`、
```

（其后 :819 起的语义选择器理由原样保留。）

`tests/e2e/wall.spec.mjs:226-227` 换成：

```js
  // J3. 未归卡片 → 新建区 = prompt 新建人名（confirmButtonText 是我们自己传的「归人」；
  //     locale 已装 zh-cn，默认按钮也是中文——但仍用语义选择器，不按文案选）
```

- [ ] **Step 3: 构建 + 冒烟 + 两页 e2e（都点过消息框）**

Run: `cd frontend && npm run build`
Expected: `vite build` 成功（三入口 + 自检插件过）。

Run（Git Bash，仓库根；**起服务一律这条配方**——临时 cwd + 空闲端口 + `uvicorn.run(create_app())`，绝不 `python -m bbh.web`、绝不碰 7860）：

```bash
./.venv/Scripts/python.exe -X utf8 -c "
import socket, subprocess, sys, time, urllib.request, tempfile, os
tmp = tempfile.mkdtemp(prefix='smoke_locale_')
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

Run: `cd .. && ./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_edit_bay_e2e.py tests/e2e/test_wall_e2e.py -v`
Expected: 2 passed（剪辑台 `✅ 全过 191 项`、墙 `✅ 全过 43 项`——语义选择器实证 locale 无感）。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/edit/main.js frontend/src/wall/main.js frontend/src/calib/main.js tests/e2e/edit_bay.spec.mjs tests/e2e/wall.spec.mjs bbh/static
git commit -m "feat(frontend): 三页装 Element Plus zh-cn locale——ElMessageBox 按钮中文化；两处 e2e 注释改口（选择器本就语义化，断言零改动）"
```

---

### Task 4: 收尾全量（本计划唯一一次全量）

**Files:** 无新文件（修补按所属任务的 Files 清单提交）。

- [ ] **Step 1: 构建 + selftest**

Run: `cd frontend && npm run build && node selftest.mjs`
Expected: 构建成功；`✅` 收尾、**35** 例。

- [ ] **Step 2: 全量 pytest**

Run: `cd .. && ./.venv/Scripts/python.exe -X utf8 -m pytest -q`
Expected: **634 passed / 1 skipped**（基线 632+1 之上加 Task 1 的 2 例；skip 是既有那例）。

- [ ] **Step 3: spec 项数复核**

Run: `./.venv/Scripts/python.exe -X utf8 tests/e2e/count-spec-items.py`
Expected: 剪辑台 `✅ 全过 191 项`（190 + Task 2 的 1 条计数断言）；墙 43 不变（本计划不碰它——Task 3 已顺手复跑过）。

- [ ] **Step 4: 冒烟**（配方同 Task 3）
Expected: `✅ 冒烟通过：三页可开、Vue 真挂载、tab 高亮正确、可跳转、无 JS 报错`。

- [ ] **Step 5: 工作树核对**

Run: `git status --short`
Expected: 干净（有漏提交就补上并中文说明）。

---

## 本计划不做的（承接表——复审时别当缺陷报）

| 项 | 去向 / 裁定 |
|---|---|
| 错误路径文案回显键（404「当前列表里没有这条球：{key}」/400「不在已删清单里」） | **S5 有意保留原始键**：错误路径回显的是用户发来的键，是调试信息不是显示面 |
| flags（⚠/✚）与 miss 的 msg 里的键 | **S5**：键即存储身份；量小且清单本就按键对账。要改另立口径 |
| 墙页 caption/卡片 label 的时间基 | Plan C 冻结口径（caption=服务端原串、key 恒干净）；要吃偏移是产品决定，Plan E 真机验收通看时定 |
| 跨界面 t_str 缝隙（旧 Gradio 烘偏移的 feedback t_str ↔ web 原始键） | **S6**：既有状态，Plan E 旧界面兼容清单 |
| vrender 锚查表偏移失效 | Plan E（用户 09-19 已裁 park，与本任务无关） |
| `record_miss` 输入的时刻基 | 用户手输的自由文本，原样存 `note`——无「显示层」可言 |
| **`BasisPanel.vue` 卡内 `row.shooter.releaseT`**（终审 I-1 登记） | **第三类：不叫 `.t` 的时刻 ⇒ 调用点判据（`dispT(`/`tsec(`）看不见它**。它与卡头 `dispT(row, offset)` 同屏不同源：`releaseT` 是 `release_t` 直传（`api_session.py` 的 shooter 块）、**原始视频轴**零平移。与上行「墙页 caption/卡片 label」同族（旧界面 `app.py` 的「出手 {rt:.1f}s 时刻」同款）⇒ **同归 Plan E 通看时定**，本计划不改显示（用户 09-19 只裁了「三处文案」）。已把「闭合」限定为 msg/撤销 label 面、并在 `OffsetDialog.vue` 头注释登记此例外 |

## 执行期勘误（Task 2 复审回填，2026-09-20）

| # | 计划原文 | 实盘勘误 | 处置 |
|---|---|---|---|
| E-1 | Task 2 Step 4 注释写「`offset` 在下面（`:132`）才声明」 | 实盘是 `:135`——注释自身那 3 行把它顶下去了；**行号引用还会再漂移**，按行号核对「声明在后是否安全」反而会读错位置、误怀疑 TDZ | 修复轮**去掉行号、改按符号指认**（「`const offset = ref(0)` 在下面才声明」）；本计划 Step 4 的代码块随之作废，以实盘注释为准 |
| E-2 | Task 2 Step 7 的「删除 toast 出现」门写成 `count() >= 1` | 只数「有任意成功 toast」会读到 3s 内尚未消失的旧 toast ⇒ 慢机上 **flaky 红**（非假绿——同文件 `:1206-1209` 已为这类形状写过告诫） | 修复轮改为**按文案等**：先断 `count() >= 1` 再断 `.last()` 含 `已删除 1 球（00:02.7`（`&&` 短路保证零 toast 时不调 `innerText`——`waitFor` 的回调抛错是直接失败、不是重试） |
| E-3 | 计划只点名两处 e2e 注释改口（`edit_bay.spec.mjs:815-818`、`wall.spec.mjs:226-227`） | 同文件 `:738-741` 还有**第三处**「第二条腿（今天还没同源，已 park）…别照着它写新断言」的副本，恰与本任务落的牙直接矛盾——下一位作者会读错 | 修复轮一并改口（同一文件、同类编辑）；Task 3 的 Step 2 范围不变（仍只管那两处） |
| E-4 | （无——本轮新知）「只改注释 ⇒ 产物不必重建」的直觉 | **错**：SFC 的 scope id 按**文件内容**算，改一行注释就让它变（`data-v-443b6c3a → data-v-504e70af`），chunk 哈希随之翻、`edit.html` 也要跟着改。反过来，**「产物 diff 非零」不能反证源码有行为改动**——修复轮逐字节核过：JS/CSS 长度分毫不变、全局只有那一个 scope id 变 | 记此以防将来把「产物变」误读成「改坏了」；纪律不变：改了前端一律 `npm run build` + 产物一起提交 |
| E-5 | Task 3 Step 3 的冒烟只写「配方同 Plan C Task 2 Step 5」 | **跨计划引用 = 任务书里没有可执行命令**：`task-brief` 只抽取本计划的单个任务，实现者拿到的是「配方同别处」这句话，跑不了（同类还有 Task 4 Step 4 的「配方同 Task 3」） | 已把**完整冒烟配方**（临时 cwd + 空闲端口 + `uvicorn.run(create_app())` 的整段 python -c）就地内联进 Task 3 Step 3。**教训**：任务书必须自包含——凡「同 Task N / 见某计划」都是占位符 |
| E-6 | Task 3 的 Files/Step 2 按行号指认 e2e 注释段（`edit_bay.spec.mjs:815-818`、`wall.spec.mjs:226-227`） | 实盘被改段在 **816-820**（Task 2 插入段 + 修复轮注释把行号顶下去了）。实现者按 **Step 2 代码块**落，方向正确、内容逐字全等——**偏差只在行号引用**，但下一位读者按 `:815-818` 去核对会读到别的注释 | 两处 Files 行与 Step 2 引言**改按文案指认**（「取消掉它（不按 Esc…）起首那段」/「J3. 未归卡片 → 新建区 那段」），行号从此不进正文（同 E-1 的处置口径）。代码零改动 |

（修复轮不动任何断言口径：spec 计数仍 **191**、selftest 仍 **35**——E-2 改的是 `waitFor` 门不计数，E-1/E-3 只动注释。）

## 交付后已知项（2026-09-20 Task 1 / Task 3 复审 parked）

| # | 项 | 裁定/去向 |
|---|---|---|
| 1 | 非有限 `offset` → **两种代价，别混为一谈**（终审 M-3 更正；原先只写了 ±inf 那半）：**±inf**：两个新端点各 **500**（`float(off)` 放行 `inf` ⇒ `max(0.0, t+inf)=inf` ⇒ `time_key` 的 `int(inf//60)` 抛 `ValueError`），发生在 `_save_*` 之前、**无半写状态**；**NaN**：`float(nan)` **truthy** ⇒ 走平移支路，而 `max(0.0, t+nan)` 在 Python 里返回 `0.0` ⇒ 端点 **200 + 写盘正确 + toast 却显示 `00:00.0`**（终审实测：`{"verdict":"goal","offset":nan}` → `msg='✅ 是进球：00:00.0（第 1 条）…'`）。**先例不是这样**：`GenIn.offset` 经 `_finite`（api_gen.py:125），其 docstring 本来就点名 NaN 要「当没传、保留原值」 | **控制者裁定 park**（结论不变）：① UI 产不出——offset 源自校准弹窗解析的时刻串（`parseT` 失败 → NaN → `\|\| 0`），非法输入只会得到有限数；② 代价是 500 或**错文案**，都非脏数据（存储正确、`t_str` 仍原始）；③ 计划正文逐字要求的消费形就是 `body.offset or 0.0`（S1），当场改成 `_finite` 会偏离计划冻结的公式，且要连带再走一轮复审。**归 Plan E 测试加固批**（与「非有限输入」同族硬化一起做：端点侧统一 `_finite` 化——**两个端点一起收口**）。**注意**：NaN 那半比 500 更难察觉（成功文案里藏着错时刻） |
| 2 | **spec 内的行号引用会自己腐烂**（本轮亲身两例）：`edit_bay.spec.mjs` 里 `（:873 之后本就有一次全量恢复）`——本计划往该文件插了 10 行注释后它**指回了本段自己那条恢复**（追补 `440366a` 改按符号指认）；另 `:587` 的 `（\`spec:87\` 那条注释记的是同一件事）` 里的 `:87` 今天是一条 `eq(axis.h, "2px", …)` **断言**（复审核实：**本轮之前就已失效**，非本次引入） | **控制者裁定**：`:873` 那处**当场修**（在本轮爆炸半径内、且指认已变成自指/说不通）；`:587` **park 归 Plan E**——与 E-1/E-6 同批，做一次**全仓行号引用清扫**（scoped/跨文件引用除外：跨文件引用的目标文件不同、不随本文件漂移）。**口径**：spec 与计划正文里一律按**符号**指认，行号只许出现在「实盘快照」这类**明确标注日期+基线**的档里 |
| 3 | **墙的 43 项计数没有仓库自带工具可复现**：`tests/e2e/count-spec-items.py` 硬编码只指向剪辑台 spec（`git grep` 核过：模块名与 spec 路径写死在脚本内、不读 `argv`）⇒ 墙侧「43」每次只能靠一次性内联驱动复现。另有同族既有门形：`tests/e2e/test_edit_bay_e2e.py:219`、`test_wall_e2e.py:101` 的 wrapper **只断言 `✅ 全过`、不校验项数** ⇒ 绿 pytest 本身证不了计数没掉 | **控制者裁定 park 归 Plan E 测试加固批**：给 `count-spec-items.py` 加可选 spec 参数（同时能打 191/43）、并让两个 pytest wrapper 把期望项数钉进门内（项数掉 = 红）。**本计划不动**——本轮 191/43 已由复审用 `count-spec-items.py` + 等价内联驱动**独立实测**（非转述实现者数字），且 `ok/eq/waitFor` 三类计数 106/70/100、21/23/26 均 BASE=HEAD 无位移 |

## 整支终审（opus，`0e7e4ea..8a5fbd2`，2026-09-20）

**总判 ✅ 可交付**：四条冻结契约逐条在代码里兑现；三处文案时间基同源（「平移 + 夹 0」三处一致，offset=0 时三处都走原串直出 ⇒ 进位边界不分叉）；两处新牙经**换回旧代码实测真红**（pytest `2 failed, 25 passed` + 失败信息恰是用户可见的旧缺陷；selftest 新+旧 `4/35 失败`、旧+旧 `✅ 34 通过` ⇒ 红是本次契约变更引起）；产物为真重建（session chunk `1273083 → 1276987 B`、`确定` 0→4 / `取消` 0→3、CSS 长度分毫不变）。0 Critical。

| # | 终审发现 | 裁定 |
|---|---|---|
| I-1 | `BasisPanel.vue` 卡内 `row.shooter.releaseT`（非 `.t` 时刻，两条判据都扫不到）与卡头 `dispT` 同屏不同源 | **行为 park**（登记进承接表，与墙页 caption 同族，归 Plan E 通看）；**呈现修**：`OffsetDialog.vue` 头注释把「闭合」限定到 msg/撤销 label 面并登记此例外 |
| M-1 | e2e 插入段未言明前提：① 它给撤销栈留了一条 `undo=restore(keys)`（键随后被全量恢复 ⇒ 此刻 Ctrl+Z 吃 400）② 删/恢复不回滚勾选与标签，本段安全靠「第 1 行两者皆空」 | **修**（注释补前提与栈顶残留，点明 `:1154` 一带为何要重建栈顶） |
| M-2 | `waitFor(..., "删除落盘（键=原始）")` 只数条数、不校验键是原始串——**门名承诺了判据没做的事** | **修（判据加强）**：门内加 `deleted.includes("00:01.2")`（`state.deleted = A._load_deleted(vp)` 是原始键，已核）⇒ 真牙。**口径更正（复审指出我先前说重了）**：这条门的**真实增量是钉「键的同一性」**，不是「救一个会被静默吞掉的失效」——服务端对**全**失配键批会抛 `Gr.Error`（`del_set` 为空）、原门同样会红；只有**部分失配**（`_raw_time_keys` 把失配键丢掉、剩下一部分照删）才是原门拦不住的那类。故**别据此删 `tests/test_web_events.py` 的落盘断言当重复覆盖**——两者牙口不同（pytest 钉落盘内容，e2e 钉屏上这一串链路） |
| M-3 | 已知项 1 的代价描述只对 ±inf 准确；NaN 是 200 + 错文案 | **修（docs）**：已知项 1 已拆成两种代价 |
| M-4 | 正文仍留已漂移的行号（`actions.js`/`EditBay.vue` 契约段、收尾自查的帮手行号）——与 E-6 自称的「行号从此不进正文」矛盾 | **修（docs）**：三处改按符号指认 |
| M-5 | `actions.js` 的 `tOf` 注释写「与旧 `row?.t \|\| key` 同形」，但 `offset≠0` 且行缺 `.t` 时 `dispT` 返回 `fmtT(offset)`（truthy）⇒ 不再回退 key | **修**（注释限定为「offset=0 时同形」并点明退化；不为不可达路径加代码） |

**收尾（微修轮 `4b0b2eb` + 追补 `440366a`，复审两门 ✅ 0 Critical / 0 Important / 3 Minor）**：I-1 的呈现面（「闭合」限定为 msg/label 面 + 注释里登记 `releaseT` 例外）、M-1（插入段补两条安全前提）、M-2（门内加断原始键 `00:01.2`，论证口径已按复审更正）、M-5（`tOf` 注释限定「同形仅 offset=0」）全部落地；**判据零放松**（spec 仍 191、`ok/eq/waitFor` 106/70/100 零位移、selftest 仍 35）。**最终 HEAD 重跑全量：634 passed / 1 skipped**（微修轮后 HEAD 已动，故重跑而非沿用 Task 4 那次）。

**SDD 工作区已删**（`.superpowers/sdd/2026-09-20-edit-msg-timebase-locale/`，随本计划收口）：实现者/复审报告与审查包**不再存在**——本计划的「整支终审」表、「执行期勘误 E-1..E-6」、「交付后已知项 1/2/3」三节即其**唯一留存**。要找依据从这里找，别去找工作区。

**方法论教训（终审的核心洞察，记 Plan E）**：「屏上显示时刻处处同源」这条不变式的判据是**调用点**（`dispT(`/`tsec(`），而**调用点判据看不见不叫 `.t` 的时刻**——`releaseT` 这类字段既不在 `dispT` 调用点里、也不匹配 `.t` 正则，于是整支（含终审者本人）都可能在同一条判据上漏掉同族显示面。**Plan E 换判据**：按**显示面**枚举（凡是渲染「秒/时刻」的模板节点），而不是按符号名 grep。

## 验证配方（速查）

- Task 1：`./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_web_events.py tests/test_web_marks.py -q`
- Task 2：`cd frontend && npm run build && node selftest.mjs`（35）→ `pytest tests/e2e/test_edit_bay_e2e.py -v`（191）
- Task 3：build + 冒烟 + `pytest tests/e2e/test_edit_bay_e2e.py tests/e2e/test_wall_e2e.py -v`（191 / 43）
- Task 4（全量）：`pytest -q` → 634/1；selftest 35；count-spec-items → 191；冒烟；净树。

## 收尾自查（控制者已跑，随计划存档）

1. **口径覆盖**：用户裁定四件——删除/裁决端点收显示偏移（Task 1）、`t_str` 存储键不动（Task 1 测试钉死）、msg 时刻单独算显示值（Task 1）、`main.js` 装 locale（Task 3）——全有落点；Ctrl+Z 撤销文案（裁定原文明列的第三处）在 Task 2。**无缺口。**
2. **占位符扫描**：全文无 TBD/TODO/「类似 Task N」；每个代码步骤带完整代码。**通过。**
3. **类型/名字一致性**（对照已读源码核过，非凭记忆）：`GenIn.offset: float | None = None`（api_gen.py:62）与 S1 逐字一致；`dispT/tsec/fmtT` 签名与 shared/time.js 一致；selftest 将破的 deepEqual 恰好 4 处（:317/:340/:349/:358，逐行核过）；e2e 插入点的 `mbox`/`menu`/`state`/`waitFor`/`ok` 均为该文件既有帮手（:810/:855/:868 一带实测）；`element-plus ^2.8.3`（package.json）支持 `es/locale/lang/zh-cn` 导入路径；count-spec-items.py 无内嵌期望值。**通过。**
4. **e2e 插入段的状态安全性**（写入前实测）：插入时点（「Esc 关窗**也不复原**」那条断言之后）offset 仍 1.5s、`deleted.length` 恒 0（更早的「一行都没删」与「恢复清单=12」两条把前置钉死）；恢复后行序不受扰动——`load_events_table` 按 events.json 检测序出行，删除/恢复只动 `deleted.json` 的视图过滤（`_filter_deleted`），故 `rowCell(0)` 在归零后仍 `00:01.2` 成立。toast 判据取 `.last()`：断言时点与删除 toast 之间无新 toast（恢复点击在断言之后）。**通过。**（**行号一律免写**——本档早期版本按 `:810/:855/:868` 指认帮手，现均已漂移；帮手按**符号**认：`mbox`/`menu`/`state`/`waitFor`/`ok`。）
