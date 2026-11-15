// 标定向导的纯函数层：只放「不经浏览器、不经服务端」就能断言的换算与判据。
// 渲染与接线由 tests/e2e/calib.spec.mjs 的 Playwright 验（同 wallkit.js 的分工）。

function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

// 点击（clientX/clientY）→ **帧像素**坐标。
// 服务端收的是帧像素：bbh/app.py:175-176 的 add_point 拿的是 gr.SelectData.index，
// app.py:193-195 直接 round(x),round(y) 切帧数组 ⇒ 百分比只是显示约定，必须换算（R4）。
// rect = 元素 getBoundingClientRect()；natural = 帧的真实像素尺寸 {w, h}（<img> @load 拿）。
// 退化保护：rect 还没布局（全零）⇒ 返回 {0,0}；natural 缺失 ⇒ 退化成 CSS 像素。
export function imgPoint(clientX, clientY, rect, natural) {
  const w = rect?.width, h = rect?.height;
  if (!w || !h) return { x: 0, y: 0 };
  const nw = natural?.w || w, nh = natural?.h || h;
  return {
    x: clamp((clientX - rect.left) / w * nw, 0, nw),
    y: clamp((clientY - rect.top) / h * nh, 0, nh),
  };
}

// 帧像素 → 读数百分比（原型冻结的读法：(12.3%, 45.6%)）。
export function pctOf(px, total) { return total ? px / total * 100 : 0; }

// 点类别按**序号**定：0=筐口左端、1=筐口右端、2+=球（服务端只存坐标，无 kind 字段）。
export function pointKind(i) { return i === 0 ? "hoop-l" : i === 1 ? "hoop-r" : "ball"; }
export function ballNo(i) { return i - 1; }        // 球标记上写的编号：第 1 颗球 = 1

// 「下一步」的门（R8）：能用服务端权威量的就用服务端，① 只能用本地量
// （_meta() 里没有「帧是否就绪」这个键）。
export function nextDisabled(step, { frameReady, canBuild, hasPreview } = {}) {
  if (step >= 3) return true;        // ④ 是最后一步，没有下一步
  if (step === 0) return !frameReady;
  if (step === 1) return !canBuild;
  return !hasPreview;
}

// 段行下标 → DELETE /api/calib/segments/{i} 的 i。
// _seg_choices 从「段 2」起编号（app.py:422-427），delete_segment 反查 choices[i-2]（R11）。
export function segDelPath(rowIndex) { return rowIndex + 2; }

// 滑块初值：_meta() 不含 HSV ⇒ 唯一权威来源是 /api/calib/existing 的 calibration.hsvLow，
// 它是 [H, S, V]（app.py:301 逐字 `(calib.hsv_low[0], int(s_low), int(v_low))`）⇒ S=[1]、V=[2]。
// 无标定（calibration 为 null）或形状不对 ⇒ 回原型默认 86/60（R10）。
export function hsvOf(existing, defaults = { s: 86, v: 60 }) {
  const lo = existing?.calibration?.hsvLow;
  if (!Array.isArray(lo) || lo.length < 3) return { ...defaults };
  return { s: Number(lo[1]) || 0, v: Number(lo[2]) || 0 };
}
