<script setup>
// 右列按人分组（spec 痛点表「按人折叠」）：组头（头像/人名/计数）是拖拽
// 放置目标，组内缩略图可点选改判、可拖回左墙，底部虚线块是「新建人名」
// 放置区。组件不取数、不写数——一切经 props 进、经 emits 出，墙状态的
// 唯一持有人是 WallBay（Task 6 只在 WallBay 侧接线，本文件到此不再改）。
import { DROP_NEW } from "./wallkit.js";

defineProps({
  groups: { type: Array, default: () => [] },   // [{person, items:[{key,caption,thumb}]}]
  selKeys: { type: Array, default: () => [] },
  dragOver: { type: String, default: null },    // 哨兵：null/DROP_UN/DROP_NEW/组名
  labelOf: { type: Function, required: true },
  badOf: { type: Function, required: true },
  open: { type: Array, default: () => [] },     // 展开的人名（v-model:open）
});
const emit = defineEmits(["toggle", "zone", "drop-to", "drag-start", "drag-end",
                          "update:open"]);
// dragleave 防闪烁：进子元素也会冒 dragleave，contains 判真离开才清高亮
// （原型 leaveZone 同款）
function leaveZone(e) {
  if (!e.currentTarget.contains(e.relatedTarget)) emit("zone", null);
}
</script>

<template>
  <div class="right colwall">
    <div v-if="!groups.length" class="nope">（还没有人——从左侧选球后用底部托盘「归人」，或直接拖进来）</div>
    <el-collapse :model-value="open" @update:model-value="(v) => emit('update:open', v)">
      <el-collapse-item v-for="g in groups" :key="g.person" :name="g.person">
        <template #title>
          <div class="ghead" :class="{ droptarget: dragOver === g.person }"
               @dragover.prevent="emit('zone', g.person)"
               @dragleave="leaveZone"
               @drop.prevent="emit('drop-to', g.person)">
            <span class="avatar">{{ (g.person || "?")[0] }}</span>
            <b>{{ g.person }}</b>
            <el-tag size="small" type="success" effect="plain">{{ g.items.length }} 球</el-tag>
          </div>
        </template>
        <div class="thumbs">
          <div v-for="it in g.items" :key="it.key" class="thumb"
               :class="{ sel: selKeys.includes(it.key) }" draggable="true"
               :title="`拖回左侧未归墙 = 从「${g.person}」移出`"
               @click="emit('toggle', it.key)"
               @dragstart="emit('drag-start', it.key)" @dragend="emit('drag-end')">
            <span v-if="badOf(it)" class="tbad">⚠</span>
            <img :src="it.thumb" alt="" />
            <span class="t">{{ labelOf(it) }}</span>
          </div>
        </div>
      </el-collapse-item>
    </el-collapse>
    <div class="drophint" :class="{ over: dragOver === DROP_NEW }"
         @dragover.prevent="emit('zone', DROP_NEW)"
         @dragleave="leaveZone"
         @drop.prevent="emit('drop-to', DROP_NEW)">＋ 拖到这里 = 新建人名{{ dragOver === DROP_NEW ? "（松手创建）" : "" }}</div>
  </div>
</template>

<style scoped>
/* 根节点 .right/.colwall 的布局样式在 WallBay.vue（父组件 scoped 样式作用
   于子组件根节点），这里只放右列内部的件。 */
.nope { color: #6f7278; font-size: 13px; padding: 6px 2px; }
.ghead { display: flex; align-items: center; gap: 10px; }
.ghead.droptarget { outline: 2px dashed var(--el-color-primary); outline-offset: 3px;
                    border-radius: 10px; }
.avatar { width: 34px; height: 34px; border-radius: 50%;
          background: var(--el-color-primary); color: #fff; font-weight: 700;
          font-size: 15px; display: flex; align-items: center; justify-content: center; }
.thumbs { display: flex; flex-wrap: wrap; gap: 8px; padding: 4px 2px 8px; }
.thumb { position: relative; width: 96px; border: 2px solid #3a3a3a;
         border-radius: 8px; overflow: hidden; cursor: pointer; background: #222; }
.thumb.sel { border-color: var(--el-color-primary); }
.thumb img { display: block; width: 100%; aspect-ratio: 1.28; object-fit: cover;
             background: #000; }
.t { position: absolute; bottom: 0; left: 0; right: 0; background: rgba(0, 0, 0, .6);
     font-size: 10.5px; color: #ddd; padding: 1px 6px; }
.tbad { position: absolute; top: 4px; right: 4px; font-size: 11px; color: #f2c078;
        background: rgba(0, 0, 0, .6); border-radius: 6px; padding: 1px 5px; }
.drophint { margin-top: 12px; border: 2px dashed #4a4a4a; border-radius: 10px;
            padding: 10px; text-align: center; color: #6f7278; font-size: 12.5px; }
.drophint.over { border-color: var(--el-color-primary); color: var(--el-color-primary); }
</style>
