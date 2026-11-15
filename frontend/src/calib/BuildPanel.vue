<script setup>
import { computed } from "vue";
import { stripMd } from "../shared/md.js";
import { fmtT } from "../shared/time.js";

const props = defineProps({
  st: { type: Object, default: null },
  previewSrc: { type: String, default: "" },
  pendingSeg: { type: Boolean, default: false },
  frameStamp: { type: Number, default: 0 },   // E15：补段起点的唯一来源（不是输入框活值）
});
const emit = defineEmits(["build"]);

const REF_NOTE = "🗂 参考帧已存档——日后怀疑镜头位移时，可跳到同时刻对照复检（标定随视频走，存 work/<视频名>/）";
const SEG_NOTE = "本次生成的是补充段：写 calibration_segN.json，不动主标定（主标定管 0:00 起的片头段）。";

const msg = computed(() => stripMd(props.st?.msg || ""));
const mmss = computed(() => fmtT(props.frameStamp));    // E15：与帧标签同源
const built = computed(() => !!props.st?.hasPreview);
</script>

<template>
  <div>
    <div class="row">
      <el-button v-if="!pendingSeg" type="primary" size="large" @click="emit('build')">⚙️ 生成标定</el-button>
      <el-button v-else type="warning" size="large" @click="emit('build')">
        ➕ 生成补充段（起点 {{ mmss }}）
      </el-button>
    </div>
    <div v-if="pendingSeg" class="helper warn">{{ SEG_NOTE }}</div>
    <div v-if="!built" class="helper">由标点计算筐口几何与色域；预览绿框 = YOLO 检出的球（与剪辑台同一检测器）</div>

    <template v-if="built">
      <img class="preview" :src="previewSrc" alt="标定预览" />
      <div class="legend">
        <span><i class="sw g"></i>YOLO 检出球框（与剪辑台同检测器）</span>
        <span><i class="sw b"></i>筐口</span>
        <span><i class="sw r"></i>筐口平面（进球判定线）</span>
        <span><i class="sw y"></i>检测区域 ROI</span>
      </div>
      <el-alert v-if="msg" type="info" :closable="false" class="blk">
        <div class="txt">{{ msg }}</div>
      </el-alert>
      <el-alert type="success" :closable="false" class="blk" :title="REF_NOTE" />
    </template>
  </div>
</template>

<style scoped>
.row { display: flex; align-items: center; gap: 10px; }
.helper { font-size: 13px; color: #a3a6ad; margin-top: 10px; }
.helper.warn { color: #e6a23c; }
.preview { max-width: 720px; width: 100%; display: block; border-radius: 8px; margin-top: 16px; }
.legend { display: flex; flex-wrap: wrap; gap: 16px; font-size: 13px; color: #a3a6ad; margin-top: 10px; }
.sw { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 5px; }
.sw.g { background: #67c23a; } .sw.b { background: #409eff; }
.sw.r { background: #f56c6c; } .sw.y { background: #e6a23c; }
.blk { margin-top: 12px; }
.txt { white-space: pre-wrap; line-height: 1.6; }
</style>
