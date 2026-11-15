// 三页共用条：当前页高亮 + 右侧提示。三页布局同源，用户三处看到的
// 导航一模一样（2026-09-16 用户裁决）。
const PAGES = [
  ["edit.html", "🎬 剪辑台"],
  ["wall.html", "🧍 出手人墙"],
  ["calib.html", "⚙ 标定设置"],
];

export function mountTabbar(current, hint = "") {
  const nav = document.createElement("nav");
  nav.className = "tabbar";
  nav.innerHTML = '<span class="brand">🏀 篮球集锦</span>'
    + PAGES.map(([href, label]) =>
        `<a href="/${href}" class="${href === current ? "on" : ""}">${label}</a>`).join("")
    + `<span class="hint">${hint}</span>`;
  document.body.prepend(nav);
}
