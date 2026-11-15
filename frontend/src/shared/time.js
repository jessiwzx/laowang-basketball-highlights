// 时刻格式的唯一来源：与后端 bbh/models.py:time_key 逐字同款
// （f"{int(t // 60):02d}:{t % 60:04.1f}"）——分钟至少两位、秒 4 宽 1 位小数。
// 键（row.key = 原始检测时刻）与显示（row.t = 受校准偏移影响的时刻）都用它格式化，
// 但**键永远来自服务端的 row.key**，前端绝不自己造键：在进位边界上
// （t=59.96 → 后端给出 "00:60.0"）parse+fmt 不是定点，
// parseT("00:60.0") → 60.0 → fmtT → "01:00.0"，那是**另一颗球**的键。
export function fmtT(sec) {
  const t = Number(sec) || 0;
  return `${String(Math.floor(t / 60)).padStart(2, "0")}:${(t % 60).toFixed(1).padStart(4, "0")}`;
}

export function parseT(s) {
  const [m, sec] = String(s ?? "").split(":");
  return (Number(m) || 0) * 60 + (Number(sec) || 0);
}

// 切片用的秒数 = 显示时刻（旧界面 _preview_and_reason 取的就是表格时间列）
// 加校准偏移。**偏移不是纯前端的**：它随 `/api/gen` 的请求体一起送给服务端，在生成
// 那一刻平移切点（`api_gen._apply_session_rows`，只动 `row[1]`）⇒ **真的影响成品**；
// 本函数只负责「算显示值 / 切片 t」这一步。
// ⚠ 别再写成「纯前端显示偏移、不落盘、不发请求」——**那句正是 A-1/A-2 静默丢值的
// 源头**（用户设的校准在成品里静默失效，终审才揪出来；计划 R7 已订正）。
// 偏移后为负的夹到 0：旧界面在同一个位置夹（bbh/app.py:801 的
// `t = max(0.0, e.t + (offset or 0.0))`），不夹会渲染出 `-1:-27.3`（R36）。
// offset 用 `(Number(offset) || 0)` 而不是 `Number(offset || 0)`：后者把
// 非数字串变成 NaN，会一路流进切片的 t= 参数（NaN 不是 0）。
export function tsec(row, offset = 0) {
  return Math.max(0, parseT(row.t) + (Number(offset) || 0));
}

// 时刻列显示串。offset=0 时**原样用服务端下发的 row.t**：后端 time_key 在
// 进位边界会给出 "00:60.0"，而复算会把它显示成 "01:00.0"（同一秒、不同串），
// 与旧界面显示不一致——没必要为了「少一个分支」牺牲 1:1。
export function dispT(row, offset = 0) {
  return Number(offset) ? fmtT(tsec(row, offset)) : row.t;
}
