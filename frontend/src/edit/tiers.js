// 档位文案与配色的唯一来源（表格的 el-tag 与时间轴 dot 共用）。
// `dot` 是时间轴标记点的填充色：放在这里，时间轴就不再自己写死字面量——
// 否则未知档位会掉回时间轴自己的第三色，而它的 tooltip 却写着「⚪ 低」
// （同一处两个结论）。table 的 el-tag 用 `type`，时间轴用 `dot`。
// 没有 `manual`（⚠ 漏检）档：那是原型自造的行，后端的漏检补录只进
// feedback.misses、**不产生表格行**（裁定 R6）。
export const TIER = {
  high: { type: "success", text: "🟢 高", dot: "#67C23A" },
  review: { type: "warning", text: "🟡 待", dot: "#E6A23C" },
  low: { type: "info", text: "⚪ 低", dot: "#909399" },
};

// 未知档位（后端将来新增，或旧数据）一律按 ⚪ 低显示，不抛错也不显示空
export const tierOf = (row) => TIER[row?.status] || TIER.low;
export const tierText = (row) => tierOf(row).text;
