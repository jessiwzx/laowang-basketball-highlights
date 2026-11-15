// 剪辑台的全部写操作。一次用户操作 = 一个请求 + **就地替换整份 state**
// （服务端回包是权威值，前端不自己算任何统计）+ 一条可撤销记录。
//
// 纯 JS、依赖注入（api / store 由页面传入）：逻辑能在 Node 里直测，不必挂
// 浏览器；组件只负责调用与渲染。
//
// 撤销栈覆盖五类：勾选 / 行标签 / 裁决 / 误标（⚠/✚ toggle）/ 删除。标签库新增、漏检补录、
// 全量恢复不入栈——后端都没有对应的反向端点（见各方法注释）。
import { dispT } from "../shared/time.js";

export const UNDO_LIMIT = 60;

// 行键（"00:12.1"）进 URL 路径：冒号在路径段里合法，但仍按规范编码，
// 免得将来出现别的字符时踩坑（Starlette 会先解码再匹配路由）。
const K = (key) => encodeURIComponent(key);

export function createActions({ api, store, getOffset = () => 0,
                                undoLimit = UNDO_LIMIT }) {
  const stack = [];
  const rowOf = (key) =>
    (store.state?.rows || []).find((r) => r.key === key) || null;
  // 撤销 label 里的时刻：屏上显示时刻处处同源——这里也吃 offset（dispT）。
  // 行缺失（row=null）回退 key；行**有** row 但**没有** `.t` 时，`dispT` 会返回
  // `fmtT(offset)`（`parseT(undefined)`=0 ⇒ `tsec`=offset）——truthy ⇒ **不回退 key**。
  // 即：与旧 `row?.t || key` **仅 offset=0 时**同形。真实行永远带 `.t`
  // （`api_session.py` 的 `"t": r[1]`），故 offset≠0 的退化今天不可达；不改代码。
  // ⚠ getOffset() 必须返回数字：ref 直传时 Number(ref)=NaN ⇒ falsy ⇒ 静默回
  // 原始串（dispT 的既有坑，见 shared/time.js 注释）。
  const tOf = (row, key) => (row ? dispT(row, getOffset()) : "") || key;

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
    star: (key, starred) => api.patch(`/api/events/${K(key)}`, { starred }),
    verdict: (key, verdict) => api.post(`/api/feedback/${K(key)}`,
                                        { verdict, offset: getOffset() }),
    flag: (key, flag) => api.post(`/api/flags/${K(key)}`, { flag }),
    del: (keys) => api.del("/api/events", { keys, offset: getOffset() }),
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
                  `${tOf(row, key)} ${selection ? "勾选" : "取消勾选"}`);
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

    // ★ 精选（2026-10-23）：单条发片的选片标记。后端不变式是**精选 ⇒ 进片**
    // —— starred=true 会自动勾上进片（用户点一次星就够），starred=false 不动
    // 勾选；而撤销勾选会连带清星（api_events.patch_event）。
    // 撤销要还两样：先把星还原，**若这次打星顺带补了勾，再把勾选还原成 false**
    // ——否则撤销完那颗球还留在集锦里，撤销只还了一半。
    setStar(key, star) {
      const row = rowOf(key);
      const wasStar = !!row?.starred;
      const wasSel = !!row?.selection;
      const undoIt = async () => {
        await replay(raw.star(key, wasStar));
        if (star && !wasSel) await replay(raw.selection(key, false));
      };
      return once(raw.star(key, star), undoIt,
                  `${tOf(row, key)} ${star ? "★ 精选" : "取消精选"}`);
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
      return once(doIt(), undoIt, `${tOf(rowOf(key), key)} ${label}`);
    },

    // 批量删除；撤销 = 按 key 恢复（R8：恢复的球回到未勾选/未归人）
    deleteKeys(keys) {
      const n = (keys || []).length;
      const label = n === 1 ? `删除 ${tOf(rowOf(keys[0]), keys[0])}`
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
