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
