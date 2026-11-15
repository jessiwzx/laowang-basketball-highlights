<script setup>
// 批量删除（proto §7）：**弹窗本身即确认**，不再套一层 ElMessageBox——
// 删除是非破坏的（只记 deleted.json 清单，「↩ 恢复已删」随时找回），而这一屏
// 已经把每条球的时刻｜档位｜标签摆出来了，看得见就不必再问一遍。
import { ref, watch } from "vue";
import { dispT } from "../shared/time.js";
import { tierText } from "./tiers.js";

const props = defineProps({
  modelValue: Boolean,
  rows: { type: Array, default: () => [] },
  offset: { type: Number, default: 0 },
});
const emit = defineEmits(["update:modelValue", "confirm"]);

const pick = ref([]);
watch(() => props.modelValue, (v) => { if (v) pick.value = []; });  // 每次打开重开白纸

const label = (row) => `${dispT(row, props.offset)}｜${tierText(row)}`
  + (row.tag ? `｜${row.tag}` : "");
const ok = () => {
  emit("confirm", [...pick.value]);
  emit("update:modelValue", false);
};
</script>

<template>
  <el-dialog class="bulkdlg" :model-value="modelValue" width="480"
             title="🗑 批量删除"
             @update:model-value="(v) => emit('update:modelValue', v)">
    <div class="tip">
      勾选要删的进球（随意投篮等非正赛球）——只记清单不动原数据，「↩ 恢复已删」
      随时一键找回；勾选的「进片/标签/归人」随行清理。
    </div>
    <el-checkbox-group v-model="pick" class="bulklist">
      <el-checkbox v-for="r in rows" :key="r.key" :value="r.key">{{ label(r) }}</el-checkbox>
    </el-checkbox-group>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="danger" :disabled="!pick.length" @click="ok">
        🗑 确认删除所选（{{ pick.length }}）
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.tip { font-size: 13px; line-height: 1.7; color: #cfd3dc; }
.bulklist { display: flex; flex-direction: column; gap: 4px; margin-top: 12px;
            max-height: 46vh; overflow-y: auto; }
</style>
