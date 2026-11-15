<script setup>
import { computed } from "vue";
import { stripMd } from "../shared/md.js";

const props = defineProps({
  st: { type: Object, default: null },
  previewSrc: { type: String, default: "" },
  sMin: { type: Number, default: 86 },
  vMin: { type: Number, default: 60 },
});
const emit = defineEmits(["update:sMin", "update:vMin", "refine", "finish"]);

const msg = computed(() => stripMd(props.st?.msg || ""));
const num = (v) => Math.max(0, Math.min(255, Number(v) || 0));
</script>

<template>
  <div>
    <div class="helper">
      这一步调整的是 <b>HSV 颜色范围</b>——它只用于「出手人溯源」的回扫与 HSV 对照版，
      <b>不影响 YOLO 球检测</b>（第③步绿框不会因此变化，这是正常的）。
      通常第 ③ 步通过就不需要动这里。只有当溯源把肤色/地板误认成球时，才调大饱和度下限。
    </div>

    <div class="row">
      <span class="lbl">饱和度 S 下限（调大可滤肤色）— 当前 {{ sMin }}</span>
      <el-slider :min="0" :max="255" show-input size="small" :model-value="sMin"
                 @update:model-value="(v) => emit('update:sMin', num(v))" />
    </div>
    <div class="row">
      <span class="lbl">明度 V 下限（调大可滤暗部噪声）— 当前 {{ vMin }}</span>
      <el-slider :min="0" :max="255" show-input size="small" :model-value="vMin"
                 @update:model-value="(v) => emit('update:vMin', num(v))" />
    </div>

    <el-alert type="info" :closable="false" class="blk"
              title="静止绿点（场边广告/灯）会在检测时自动滤除，标定阶段不用管" />

    <img v-if="st?.hasPreview" class="preview" :src="previewSrc" alt="ROI 预览" />
    <el-alert v-if="msg" type="info" :closable="false" class="blk">
      <div class="txt">{{ msg }}</div>
    </el-alert>

    <div class="row btns">
      <el-button @click="emit('refine')">🔄 重算预览</el-button>
      <el-button type="primary" @click="emit('finish')">✓ 完成标定</el-button>
    </div>
  </div>
</template>

<style scoped>
.helper { font-size: 13px; color: #a3a6ad; margin-bottom: 14px; }
.row { display: flex; align-items: center; gap: 16px; margin-bottom: 14px; }
.lbl { width: 260px; flex: none; font-size: 13px; }
.row :deep(.el-slider) { flex: 1; max-width: 520px; }
.row :deep(.el-slider__input) { width: 96px; flex: none; }
.blk { margin-top: 12px; }
.txt { white-space: pre-wrap; line-height: 1.6; }
.preview { max-width: 720px; width: 100%; display: block; border-radius: 8px; margin-top: 14px; }
.btns { margin-top: 16px; }
</style>
