<script setup>
import { ref, watch } from "vue";
import { api } from "../shared/api.js";
import { stripMd } from "../shared/md.js";
import { fmtT } from "../shared/time.js";

const props = defineProps({ modelValue: { type: Boolean, default: false } });
const emit = defineEmits(["update:modelValue"]);

const data = ref(null);
const err = ref("");

// 每次打开都重取：标定可能在别处刚变过（生成/补段/微调），缓存一份只会显示旧值。
watch(() => props.modelValue, async (open) => {
  if (!open) return;
  err.value = "";
  // 连同上一份快照一起清掉（只清 err 不够）：否则重开时在新回包到达之前，模板
  // 渲染的是**上一次的 data**（「屏上显示的是过期状态」）。清成 null ⇒ 走
  // 「读取中…」那支，E29 那条等待锚在数据分支首行的「状态：」上，依旧成立。
  data.value = null;
  try { data.value = await api.get("/api/calib/existing"); }
  catch (e) { data.value = null; err.value = e.message; }   // 无视频路径 ⇒ 400
});

// HSV 三数组必须带分量名：裸 join 出来读不出哪个数是 S、哪个是 V
const hsvLine = (arr) => `H ${arr[0]} · S ${arr[1]} · V ${arr[2]}`;
</script>

<template>
  <el-dialog :model-value="modelValue" title="查看已有标定" width="620px"
             @update:model-value="emit('update:modelValue', $event)">
    <div v-if="err" class="err">{{ err }}</div>
    <template v-else-if="data">
      <div class="k">状态：{{ data.ready ? "已标定" : "未标定" }}</div>
      <div class="k">
        参考帧：{{ data.hasReferenceFrame ? `已存档（标定时刻 ${fmtT(data.calibT)}）` : "未存档" }}
      </div>
      <div class="k">标定文件：{{ data.calibPath || "（无）" }}</div>

      <template v-if="data.calibration">
        <div class="k">
          筐口矩形：x={{ data.calibration.hoopRect[0] }} y={{ data.calibration.hoopRect[1] }}
          w={{ data.calibration.hoopRect[2] }} h={{ data.calibration.hoopRect[3] }}（帧
          {{ data.calibration.frameSize[0] }}×{{ data.calibration.frameSize[1] }}）
        </div>
        <div class="k">
          HSV 下限：{{ hsvLine(data.calibration.hsvLow) }}／上限：{{ hsvLine(data.calibration.hsvHigh) }}
        </div>
      </template>
      <div v-else class="k">（还没有标定：先走完向导的「生成标定」）</div>

      <el-alert v-if="data.wideHsvNote" type="warning" :closable="false" class="blk">
        <div class="txt">{{ stripMd(data.wideHsvNote) }}</div>
      </el-alert>

      <div class="k">补充段：{{ data.segments.length ? data.segments.join("、") : "（只有主段）" }}</div>
      <div v-if="data.segListMd" class="segmd">{{ stripMd(data.segListMd) }}</div>

      <div class="k">work 目录标定文件：</div>
      <div class="files">
        <el-tag v-for="f in data.files" :key="f" size="small" class="ftag">{{ f }}</el-tag>
        <span v-if="!data.files.length" class="none">（无）</span>
      </div>
    </template>
    <div v-else class="k">读取中…</div>
  </el-dialog>
</template>

<style scoped>
.k { font-size: 13.5px; line-height: 2; }
.err { color: #f56c6c; font-size: 13.5px; }
.blk { margin: 10px 0; }
.txt { white-space: pre-wrap; line-height: 1.6; }
.segmd { white-space: pre-wrap; font-size: 13px; color: #a3a6ad; line-height: 1.7; }
.files { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
.ftag { font-family: Consolas, monospace; }
.none { font-size: 13px; color: #8d9098; }
</style>
