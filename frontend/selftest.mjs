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
});

test("tsec 偏移后为负则夹 0（与旧界面 app.py:801 同址）", () => {
  assert.equal(tsec({ key: "00:01.2", t: "00:01.2" }, -2), 0);
});

test("tsec 非数字 offset 回落 0，不产生 NaN（NaN 会流进切片的 t= 参数）", () => {
  assert.equal(tsec({ key: "00:01.2", t: "00:01.2" }, "abc"), 1.2);
});

test("负偏移夹 0 后显示串是 00:00.0，不是 -1:-0.8", () => {
  assert.equal(dispT({ key: "00:01.2", t: "00:01.2" }, -2), "00:00.0");
});

test("dispT 在 offset=0 时原样用服务端串（不复算，避免 00:60.0 → 01:00.0 的显示漂移）", () => {
  assert.equal(dispT({ key: "00:60.0", t: "00:60.0" }, 0), "00:60.0");
  assert.equal(dispT({ key: "00:60.0", t: "00:60.0" }, 1.0), "01:01.0");
});

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

// ---- hotkeys.js ----
const { keyName, bindHotkeys } = await import("./src/shared/hotkeys.js");

test("keyName 归一：空格/方向键/转义/问号/ctrl 前缀", () => {
  const ev = (key, ctrlKey = false, shiftKey = false) => ({ key, ctrlKey, shiftKey });
  assert.equal(keyName(ev(" ")), "space");
  assert.equal(keyName(ev("ArrowDown")), "down");
  assert.equal(keyName(ev("ArrowUp")), "up");
  assert.equal(keyName(ev("ArrowLeft")), "left");
  assert.equal(keyName(ev("ArrowRight")), "right");
  assert.equal(keyName(ev("Escape")), "esc");
  assert.equal(keyName(ev("?")), "?");
  assert.equal(keyName(ev("X")), "x");
  assert.equal(keyName(ev("z", true)), "ctrl+z");
  // Ctrl+Shift 组合**不产生任何 keyMap 里存在的键名**（A-9）：原型的判据是
  // `if (e.ctrlKey && !e.shiftKey && …)`，而 Ctrl+Shift+Z 是 Windows 的「重做」
  // 肌肉记忆——本应用**没有重做**，落回 `ctrl+z` 会真的撤销一步且撤不回来。
  // 这两条必须有牙：「不落回 ctrl+z」+「给出的是带 shift 的那个键名」，
  // 少了后半句，`keyName` 改成恒返回 "ctrl+z" 也照样绿。
  assert.notEqual(keyName(ev("Z", true, true)), "ctrl+z");
  assert.equal(keyName(ev("Z", true, true)), "ctrl+shift+z");
  // ⚠ 但别掐成「有 shift 就整体失效」：`?` 在真实按键里就是 shift+/（e.key === "?"、
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

test("bindHotkeys 的 off() 真的解绑（假窗口留全部监听器，不是只留最后一个）", () => {
  const seen = [];
  const fakeWin = {
    document: { activeElement: null },
    addEventListener(t, f) { seen.push(f); },
    removeEventListener(t, f) {
      const i = seen.indexOf(f);
      if (i >= 0) seen.splice(i, 1);
    },
  };
  const hits = [];
  const off = bindHotkeys({ space: () => hits.push("space") }, { win: fakeWin });
  const fire = () => { for (const f of [...seen]) f({ key: " ", preventDefault() {} }); };
  fire();
  assert.deepEqual(hits, ["space"]);
  off();
  assert.equal(seen.length, 0);     // 监听器从窗口上摘掉了（off() 空实现会留 1 个）
  fire();                          // 解绑后再按：不该再触发
  assert.deepEqual(hits, ["space"]);
});

// ---- jobs.js ----
const { runJob, followJob, startJob, POLL_MS } = await import("./src/shared/jobs.js");

test("POLL_MS 是 spec 拍板的 800ms（Task 5–10 都用它，随手改动会让全部用例照过）", () => {
  assert.equal(POLL_MS, 800);
});

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
  m.remember({ kind: "detect", jobId: "j1", videoPath: "D:/v/a.mp4",
               startedAt: 1758250000000 });
  const rec = m.load();      // 四个字段都要原样往返（页面靠 kind/videoPath 认领任务）
  assert.equal(rec.jobId, "j1");
  assert.equal(rec.kind, "detect");
  assert.equal(rec.videoPath, "D:/v/a.mp4");
  assert.equal(rec.startedAt, 1758250000000);
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
  // JSON 合法但缺 jobId（半截记录）：接上了也没法轮询，同样当没有
  const m4 = createJobMemory({ getItem: () => JSON.stringify({ kind: "detect" }),
                               setItem() {}, removeItem() {} });
  assert.equal(m4.load(), null);
});

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
                               body: { verdict: "not_goal", offset: 0 } }]);
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
  assert.deepEqual(calls[2].body, { verdict: "goal", offset: 0 });
  assert.deepEqual(calls[3].body, { selection: true });
  assert.equal(await act.undo(), null);

  // 无原裁决 + ✅ ⇒ 撤销再发一次 goal（后端取消）并把勾选还原
  const r2 = fakeApi(stateWith());
  const act2 = createActions({ api: r2.api, store: fakeStore(stateWith()) });
  await act2.markVerdict("00:12.1", "goal");
  await act2.undo();
  assert.deepEqual(r2.calls[2].body, { verdict: "goal", offset: 0 });
  assert.deepEqual(r2.calls[3].body, { selection: false });
});

test("deleteKeys 撤销 = 按 key 恢复（不是全量恢复）", async () => {
  const { calls, api: fake } = fakeApi(stateWith());
  const act = createActions({ api: fake, store: fakeStore(stateWith()) });

  await act.deleteKeys(["00:12.1", "01:12.1"]);
  assert.deepEqual(calls[0], { m: "DELETE", url: "/api/events",
                               body: { keys: ["00:12.1", "01:12.1"],
                                       offset: 0 } });
  const un = await act.undo();
  assert.deepEqual(calls[1], { m: "POST", url: "/api/events/restore",
                               body: { keys: ["00:12.1", "01:12.1"] } });
  assert.equal(un.label, "批量删除 2 球");
});

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

let failed = 0;
for (const [name, fn] of tests) {
  try { await fn(); console.log(`  ok  ${name}`); }
  catch (e) { failed++; console.error(`FAIL  ${name}\n      ${e.message}`); }
}
console.log(failed ? `\n${failed}/${tests.length} 失败` : `\n✅ ${tests.length} 通过`);
process.exit(failed ? 1 : 0);
