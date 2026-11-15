<script setup>
// 底部多选托盘：chips（点 × 撤选）+ 人名下拉（filterable/allow-create）+
// 归人/移出/清空。人名候选 choices 由父层从 /api/wall 回包直传——服务端
// _tag_choices（标签库 ∪ 本视频已归人）是唯一源，前端不自己 union（R13）。
const props = defineProps({
  selKeys: { type: Array, default: () => [] },
  choices: { type: Array, default: () => [] },
  chipLabel: { type: Function, required: true },
  name: { type: String, default: "" },
});
const emit = defineEmits(["toggle", "assign", "unassign", "clear",
                          "update:name", "person-change"]);
function onName(v) {
  emit("update:name", v || "");
  emit("person-change", v || "");      // 换人名 = 重取归人前护栏读数（R4）
}
</script>

<template>
  <div class="tray">
    <b class="tray-title">已选 {{ selKeys.length }} 球：</b>
    <div class="chips">
      <!-- disable-transitions：EP 的 el-tag 出场动画会让被撤选的 chip 在
           DOM 里滞留 ~0.3s（leave transition），托盘是控制面不是动画位——
           chips 增删必须即时反映 selKeys（e2e 与用户点选都依赖）。 -->
      <el-tag v-for="k in selKeys" :key="k" closable effect="dark"
              disable-transitions
              style="margin: 2px" @close="emit('toggle', k)">{{ chipLabel(k) }}</el-tag>
      <span v-if="!selKeys.length" class="tray-empty">（点左侧卡片选择）</span>
    </div>
    <el-select :model-value="name" filterable allow-create default-first-option
               placeholder="归人（选/输人名）" size="small" style="width: 150px"
               @update:model-value="onName">
      <el-option v-for="p in choices" :key="p" :label="p" :value="p" />
    </el-select>
    <el-button type="primary" size="small"
               :disabled="!selKeys.length || !name"
               @click="emit('assign')">✅ 归人</el-button>
    <el-button size="small" :disabled="!selKeys.length"
               @click="emit('unassign')">↩ 移出</el-button>
    <el-button text size="small" :disabled="!selKeys.length"
               @click="emit('clear')">清空</el-button>
  </div>
</template>

<style scoped>
.tray { flex: none; display: flex; gap: 12px; align-items: center; flex-wrap: wrap;
        min-height: 58px; padding: 10px 18px;
        background: #1f1a16; border-top: 1px solid #5c3d22; }
.tray-title { font-size: 13px; white-space: nowrap; }
.chips { display: flex; gap: 6px; flex-wrap: wrap; flex: 1; }
.tray-empty { color: #6f7278; font-size: 12.5px; }
</style>
