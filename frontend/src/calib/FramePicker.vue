<script setup>
import { computed } from "vue";
import { fmtT } from "../shared/time.js";

const props = defineProps({
  st: { type: Object, default: null },
  frameUrl: { type: String, default: "" },
  frameAt: { type: Number, default: 0 },
  frameReady: { type: Boolean, default: false },
  frameStamp: { type: Number, default: 0 },
});
const emit = defineEmits(["update:frameAt", "extract", "frame-loaded"]);

// 标签描述的是**屏上那张帧**（frameStamp 在成功抽帧时定格），不是输入框的实时值
// frameAt——改时间点但没抽帧时，用 frameAt 会让标签对着旧帧报新时刻（E15）。
const mmss = computed(() => fmtT(props.frameStamp));    // 时刻格式唯一来源（shared/time.js）

function onAt(v) { emit("update:frameAt", Math.max(0, Number(v) || 0)); }
function onImgLoad(e) {
  emit("frame-loaded", { w: e.target.naturalWidth, h: e.target.naturalHeight });
}
</script>

<template>
  <div>
    <el-alert type="info" :closable="false" show-icon style="margin-bottom: 16px"
              title="标定跟着视频走：每个拍摄机位做一次；同机位的新视频自动复用（指纹匹配）。" />

    <div class="row">
      <el-button size="large" type="primary" plain @click="emit('extract')">抽帧</el-button>
      <span class="tip">时间点（秒，跳到球在筐口附近的画面）</span>
      <el-input-number size="small" :min="0" :step="30" :model-value="frameAt"
                       @update:model-value="onAt" />
      <el-button size="small" @click="emit('extract')">跳到该时间抽帧</el-button>
    </div>

    <div v-if="frameReady" class="frame">
      <div class="label">参考帧 · 视频 {{ mmss }} 处</div>
      <img :src="frameUrl" alt="参考帧" @load="onImgLoad" />
    </div>
    <div v-if="frameReady" class="helper">✅ 帧已就绪——点「下一步」开始标点。</div>
  </div>
</template>

<style scoped>
.row { display: flex; align-items: center; gap: 10px; }
.tip { font-size: 13px; color: #a3a6ad; }
.frame { max-width: 720px; margin-top: 18px; }
.frame .label { font-size: 13px; color: #a3a6ad; margin-bottom: 6px; }
.frame img { width: 100%; display: block; border-radius: 8px; }
.helper { font-size: 13px; color: #67c23a; margin-top: 10px; }
</style>
