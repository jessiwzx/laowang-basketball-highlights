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
  // Shift 不是本应用的修饰键，它是「重做 / 大写」的：Ctrl+Shift+Z 在 Windows
  // 上是「重做」的肌肉记忆，而本应用**没有重做**——若它落回 `ctrl+z` 就会
  // 真的撤销一步（`replay` 不入栈 ⇒ 这步撤销自己撤不回来）。原型的判据是
  // `if (e.ctrlKey && !e.shiftKey && …)`，这里同义：**带 shift 的 ctrl 组合
  // 不产生任何 keyMap 里存在的键名**（给个没人注册的名字 = no-op，而不是
  // 悄悄落回基础键）。
  // ⚠ 只掐「组合键」，别掐成「有 shift 就整体失效」：`?` 在真实按键里就是
  // shift+/（`e.key === "?"`、ctrlKey 为 false），它必须照旧归一到 `?`，
  // 否则帮助窗的开关自己就废了。
  if (e.ctrlKey && e.shiftKey) return `ctrl+shift+${base}`;
  return (e.ctrlKey ? "ctrl+" : "") + base;
}

// win 默认挂在 globalThis 上（浏览器里就是 window）；注入假窗口即可在
// Node 里直测「输入框聚焦 / 禁用态不触发」这两条规则。
export function bindHotkeys(map, { disabled = () => false,
                                   win = globalThis } = {}) {
  const onKey = (e) => {
    // 闸①：焦点在输入控件里时一律交给原生（含 Ctrl+Z——否则在漏检补录框里按
    // 撤销会去撤背后表格的勾选）。
    // ⚠ 判据是 `document.activeElement` 的 **tagName**，只覆盖**真实** input /
    // textarea / contentEditable；原型按 `e.target` 的祖先链判
    // （`closest("input,textarea,.el-select,.el-input-number")`）。两者对本页
    // 现有四条输入路径**当前等价**（焦点最终都落在真实 `<input>` 上，复审逐条
    // 走过），但**新增控件时要按 tagName 复核**：焦点停在某个非 input 的包裹
    // 元素上时（如 EP 的 `.el-input-number` 外壳）原型会拦住、这里不会——那时
    // 要么把新控件补进这个判据，要么改回按祖先链判。
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
