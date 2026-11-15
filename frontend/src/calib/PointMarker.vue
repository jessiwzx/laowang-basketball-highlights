<script setup>
import { computed, ref } from "vue";
import { imgPoint, pctOf, pointKind, ballNo } from "./calibkit.js";
import { stripMd } from "../shared/md.js";

const props = defineProps({
  st: { type: Object, default: null },
  frameUrl: { type: String, default: "" },
  natural: { type: Object, default: () => ({ w: 0, h: 0 }) },
});
const emit = defineEmits(["click-point", "undo", "clear"]);

const framePos = ref(null);          // 光标读数（百分比，只用于画十字与显示）

const pts = computed(() => props.st?.points || []);
const hint = computed(() => stripMd(props.st?.hint || ""));
const W = computed(() => props.natural?.w || 0);
const H = computed(() => props.natural?.h || 0);

function onMove(ev) {
  const r = ev.currentTarget.getBoundingClientRect();
  if (!r.width || !r.height) return;
  framePos.value = {
    x: (ev.clientX - r.left) / r.width * 100,
    y: (ev.clientY - r.top) / r.height * 100,
  };
}
function onClick(ev) {
  emit("click-point", imgPoint(ev.clientX, ev.clientY,
                               ev.currentTarget.getBoundingClientRect(), props.natural));
}
function rowLabel(i) {
  const p = pts.value[i];
  const x = (pctOf(p[0], W.value)).toFixed(1), y = (pctOf(p[1], H.value)).toFixed(1);
  const k = pointKind(i);
  if (k === "hoop-l") return `筐口左端：(${x}%, ${y}%)`;
  if (k === "hoop-r") return `筐口右端：(${x}%, ${y}%)`;
  return `球 ${ballNo(i)}：(${x}%, ${y}%)`;
}
</script>

<template>
  <div class="grid">
    <!-- ⚠ 那行 `.label` 必须在 `.frame-wrap` **之外**（复审 Important）：`.frame-wrap` 是点击
         容器，`imgPoint` 拿的是它的 getBoundingClientRect() ⇒ 盒里多一行标签，坐标基准就含
         标签高（~22px），上报的帧 y 会系统性偏大（实测图内 0.25 处报 103.5 而非 90）。
         同理，帧上标记/十字的百分比也以 `.frame-wrap` 为定位父级 ⇒ 它必须**恰好等于 <img>
         的盒**（wrap 无 padding/border，img 是 width:100% 的块级子元素，唯一子元素）。
         谁把 `.label` 挪回 wrap 里，谁就把这个缺陷带回来。 -->
    <div class="frame-col">
      <div class="label">
        参考帧 · 在图上点击标点（先筐口，后球）
        <template v-if="framePos"> · 光标 ({{ framePos.x.toFixed(1) }}%, {{ framePos.y.toFixed(1) }}%)</template>
      </div>
      <div class="frame-wrap" @click="onClick" @mousemove="onMove" @mouseleave="framePos = null">
        <img :src="frameUrl" alt="参考帧" />
        <template v-if="framePos">
          <div class="cross-v" :style="{ left: framePos.x + '%' }"></div>
          <div class="cross-h" :style="{ top: framePos.y + '%' }"></div>
        </template>
        <div v-for="(p, i) in pts" :key="i" class="pt" :class="pointKind(i)"
             :style="{ left: pctOf(p[0], W) + '%', top: pctOf(p[1], H) + '%' }">
          <span v-if="pointKind(i) === 'ball'" class="idx">{{ ballNo(i) }}</span>
        </div>
      </div>
    </div>

    <div class="side">
      <div class="pts-list">
        <div v-for="(p, i) in pts" :key="'r' + i" class="pt-row">
          <span>{{ rowLabel(i) }}</span>
          <el-button text size="small" type="danger" @click="emit('undo', i)">撤销</el-button>
        </div>
        <div v-if="!pts.length" class="empty">（尚未标点）</div>
      </div>
      <div class="legend"><span class="dot l"></span>筐口左端 / 右端</div>
      <div class="legend"><span class="dot b"></span>球（点 1~3 次）</div>
      <el-button size="small" style="margin-top: 10px" @click="emit('clear')">清空全部</el-button>
    </div>

    <div class="helper">{{ hint }}</div>
  </div>
</template>

<style scoped>
.grid { display: grid; grid-template-columns: 1fr 260px; gap: 18px; }
.frame-col { align-self: start; }
.frame-col .label { font-size: 13px; color: #a3a6ad; margin-bottom: 6px; }
.frame-wrap { position: relative; cursor: crosshair; }
.frame-wrap img { width: 100%; display: block; border-radius: 8px; }
.cross-v { position: absolute; top: 0; bottom: 0; width: 1px; background: rgba(255, 255, 255, .55); pointer-events: none; }
.cross-h { position: absolute; left: 0; right: 0; height: 1px; background: rgba(255, 255, 255, .55); pointer-events: none; }
/* 落点圆：原先 18px + 3px 深色环 = 视觉直径 24px。帧图在预览里只有 ~700px 宽，
   而底图是 1920/2560 的原始帧 ⇒ 一个圆盖住 60+ 原始像素，正好压住筐口端点与小球
   （用户 2026-09-25 反馈「都挡住我看篮筐和篮球」）。缩到 12px + 1px 环（视觉 14px，
   遮挡面积降到 ~1/3）。尺寸用固定 px、不随图缩放：定位父级 `.frame-wrap` 有确定的
   最大宽度，固定值可预期；配色与 `pointer-events: none` 不动（点击仍由 `.frame-wrap`
   收，圆不是热区）。 */
.pt { position: absolute; width: 12px; height: 12px; border-radius: 50%; transform: translate(-50%, -50%);
      display: flex; align-items: center; justify-content: center; font-size: 10px; font-weight: 700;
      color: #fff; box-shadow: 0 0 0 1px rgba(0, 0, 0, .4); pointer-events: none; }
.pt.hoop-l, .pt.hoop-r { background: #409eff; }
.pt.ball { background: #67c23a; }
/* 序号标签跟着小圆收紧（原 -16px 是给 18px 圆的，留 1px 缝隙即可）。 */
.pt .idx { position: absolute; top: -14px; font-size: 11px; color: #9fe290; text-shadow: 0 1px 2px #000; }
.side { font-size: 13.5px; }
.pt-row { display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; }
.empty { color: #8d9098; }
.legend { display: flex; align-items: center; gap: 6px; margin-top: 8px; color: #a3a6ad; }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.dot.l { background: #409eff; }
.dot.b { background: #67c23a; }
.helper { grid-column: 1 / -1; font-size: 13px; color: #e5eaf3; margin-top: 12px; }
</style>
