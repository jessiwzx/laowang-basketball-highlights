// 服务端下发的文案是 **markdown**（旧界面用 `gr.Markdown` 渲染它）：
// `_table_summary_md` 是纯文本，但 `_fp_summary`（app.py:1902-1907）写成
// 「已裁决 **0** 条是进球、… → `feedback.json`」，裁决/漏检那条 toast 的
// `msg` 里也整段带着它。新界面把它们放在 `<div>`/toast 里**字面显示**，
// 用户看到的就是一串 `**0**`。
//
// ⚠ 这是**渲染层**的去标记，不是改写文案：本项目的铁律是「前端不自己编/改
// 服务端文案」，这里一个字都没改（`**3**` → `3`，`` `feedback.json` `` →
// `feedback.json`），只是把 markdown 的强调标记脱掉——与旧界面 `gr.Markdown`
// 把同样的标记渲染成粗体是同一件事的两端。
// 为什么不上真 markdown 渲染器（marked/markdown-it + `v-html`）：① 那要新增
// 一个依赖并把服务端文案当 HTML 注入（XSS 面白送）；② 这些文案里除了 `**`
// 与反引号没有任何富文本意图，上一整套渲染器换不来一条信息。将来真出现
// 标题/列表，先改服务端文案的产出，别让前端替它决定怎么渲染。
export function stripMd(s) {
  return String(s ?? "").replace(/\*\*/g, "").replace(/`/g, "");
}
