// 已用标签置顶（MRU，2026-10-11 用户裁定）：剪辑台行内标签下拉与出手人墙
// 托盘人名下拉里，**最近打过/归过的标签自动排到候选首位**，方便连续打标。
//
// 边界（定稿时的取舍，别顺手扩）：
// - 只改两处**打标下拉**的候选展示序。热键 1-9、帮助弹窗清单、顶栏筛选
//   下拉仍走标签库原序——数字键是固定位置映射（「Task 7 的 1-9 索引依赖
//   库序稳定」），跟着 MRU 动会毁手感。
// - 标签库本体（tags.json / 服务端 state.tags）一个字节不动；MRU 只是
//   浏览器侧的展示层排序，两页同源共享 localStorage、跨会话保留。
// - 库里已删掉的标签自然失效（orderTags 按 all 过滤）；存不进 localStorage
//   （隐私模式等）就退化为库序，不炸页面。
//
// ⚠ MRU 列表必须是**响应式 ref**：orderTags 在 computed 里被调用，而
// localStorage 不是响应式依赖——打标后标签库没变、computed 不重算，下拉
// 会一直显示旧序（e2e 首跑就是这么红的）。换成 ref 后 recordTagUse 换引用
// 即触发依赖它的 computed 重算。

import { ref } from "vue";

const KEY = "bbh.tagMru";
const CAP = 30;               // 防无限膨胀；两处下拉都用 filterable，长尾无用

function load() {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) || "[]");
    return Array.isArray(v) ? v.filter((x) => typeof x === "string") : [];
  } catch {
    return [];
  }
}

const mru = ref(load());

export function recordTagUse(tag) {
  const t = String(tag || "").trim();
  if (!t) return;                       // 清空标签不记账
  mru.value = [t, ...mru.value.filter((x) => x !== t)].slice(0, CAP);
  try { localStorage.setItem(KEY, JSON.stringify(mru.value)); }
  catch { /* 存不进就算了：内存序仍生效，刷新后退化 */ }
}

export function orderTags(all) {
  return [...mru.value.filter((t) => all.includes(t)),
          ...all.filter((t) => !mru.value.includes(t))];
}
