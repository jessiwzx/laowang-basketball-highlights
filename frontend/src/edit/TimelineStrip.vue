<script setup>
// 全场进球分布标记条：点刻度跳到离点击位置最近的那颗球。
// 无框选/多选/缩放/拖动（spec 明确不做）。
import { ref, computed } from "vue";
import { dispT, fmtT, tsec } from "../shared/time.js";
import { tierOf, tierText } from "./tiers.js";

const props = defineProps({
  rows: { type: Array, required: true },
  duration: { type: Number, default: 0 },            // state.duration
  verdicts: { type: Object, default: () => ({}) },
  offset: { type: Number, default: 0 },
});
const emit = defineEmits(["pick"]);

const trackRef = ref(null);
const tlDur = computed(() => Number(props.duration) || 0);

// 位置按**显示时刻**（与时刻列、切片同口径，R9）；时长未知时全压 0 位
const pctOf = (row) => {
  if (!tlDur.value) return 0;
  return Math.min(100, Math.max(0, (tsec(row, props.offset) / tlDur.value) * 100));
};

// 填充色的唯一来源：档位色取 `TIER[..].dot`（`tiers.js`）——**未知档位因此
// 也落到 ⚪ 低那一支的颜色**，不再掉回本组件自己的第三色、与 tooltip 里
// 那句「⚪ 低」打架。已否决/已勾选是**状态覆盖**（不是档位色），归本组件。
function dotColor(row) {
  const v = props.verdicts[row.key];
  if (v === "not_goal") return "#f56c6c";     // 已否决：红
  if (row.selection) return "#67C23A";        // 已勾选进片：绿
  return tierOf(row).dot;
}
// class 只留状态钩子（颜色已全部走行内 style）：tl-yes 的光圈、tl-no 的语义标记
function dotClass(row) {
  const v = props.verdicts[row.key];
  if (v === "not_goal") return "tl-no";
  return row.selection ? "tl-yes" : "";
}
const titleOf = (row) =>
  `${dispT(row, props.offset)} · ${tierText(row)}`
  + `${row.tag ? " · " + row.tag : ""}${row.selection ? " · 已勾选" : ""}`;

function onClick(e) {
  const r = trackRef.value?.getBoundingClientRect();
  if (!r || !tlDur.value) return;
  const t = ((e.clientX - r.left) / r.width) * tlDur.value;
  let best = null;
  for (const row of props.rows) {
    const d = Math.abs(tsec(row, props.offset) - t);
    if (!best || d < best.d) best = { d, key: row.key };
  }
  if (best) emit("pick", best.key);
}
</script>

<template>
  <div class="tlstrip">
    <div ref="trackRef" class="tl" @click="onClick"
         title="全场进球分布——点刻度跳到该球（绿=已勾选 红=已否决 灰点=低置信）">
      <span v-for="row in rows" :key="row.key"
            class="tl-dot" :class="dotClass(row)"
            :style="{ left: pctOf(row) + '%', background: dotColor(row) }"
            :title="titleOf(row)"></span>
    </div>
    <span class="tl-cap">0:00</span>
    <span class="tl-cap tl-cap-r">{{ tlDur ? fmtT(tlDur) : "" }}</span>
  </div>
</template>

<style scoped>
.tlstrip { flex: none; position: relative; padding: 10px 14px 0; }
.tl { position: relative; height: 18px; margin: 0 30px; cursor: pointer;
      background: #202020; border-radius: 9px; }
/* 中轴是**横贯**那条线（原型 前端原型/剪辑台.html:88）：`top:8px` + `height:2px`
   的竖心恰好落在 9px —— 与圆点中心（`top:4px` + 10px 高 = 9px）同一水平线上，
   圆点就骑在这条轴上游走。原先写成了中间一根竖线（`left:50%;top:0;width:2px;
   height:100%`）⇒ 用户看到一根无来由的竖杠，与刻度位置毫无关系。 */
.tl::before { content: ""; position: absolute; left: 0; right: 0; top: 8px;
              height: 2px; background: #3a3a3a; }
/* 颜色全部走行内 style（唯一来源 = tiers.js 的 TIER[..].dot + 两个状态覆盖），
   这里不再留任何 background 字面量：留着就是第二条来源，未知档位又会分叉。 */
.tl-dot { position: absolute; top: 4px; width: 10px; height: 10px;
          margin-left: -5px; border-radius: 50%;
          transition: transform .12s; }
.tl-dot:hover { transform: scale(1.6); }
.tl-dot.tl-yes { box-shadow: 0 0 0 2px rgba(103, 194, 58, .35); }
.tl-cap { position: absolute; top: 12px; font-size: 10px; color: #6f7278; }
.tl-cap:first-of-type { left: 0; }
.tl-cap-r { right: 0; }
</style>
