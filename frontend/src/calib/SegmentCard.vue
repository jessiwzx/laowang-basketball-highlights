<script setup>
import { computed } from "vue";
import { stripMd } from "../shared/md.js";

const props = defineProps({
  st: { type: Object, default: null },
  pendingSeg: { type: Boolean, default: false },
});
const emit = defineEmits(["add-seg", "del-seg", "cancel-seg"]);

const segs = computed(() => props.st?.segments || []);
const segMd = computed(() => stripMd(props.st?.segListMd || ""));
// 「主标定是否存在」= `st.ready`（顶栏那枚 已标定/未标定 tag 用的**同一个键**）。
// 服务端 /api/calib/build 的补段分支（`segT is not None`）要求**先有主标定**，
// 没有主标定时进补段态 ⇒ 必然 400 ⇒ 而 pendingSeg 只在成功时才清 ⇒ 只能 F5 的死路。
const hasMain = computed(() => !!props.st?.ready);
</script>

<template>
  <div>
    <div class="head">
      <div class="title">📐 标定段管理（镜头中途挪动过？为挪动后的画面补一段标定）</div>
      <div class="segbtns">
        <el-button size="small" :disabled="!hasMain"
                   title="先按上面的向导生成主标定，才能补段——服务端要求先有主标定"
                   @click="emit('add-seg')">➕ 新增补充段</el-button>
        <!-- 「取消补段」是 pendingSeg 的**唯一出口**（此前只有「生成成功」才清标志）。
             它**放在段卡里**（段卡在任何步骤都渲染，不像步 ③ 的面板）⇒ 它的在/不在
             在任意步骤上都等价于 pendingSeg 的真值，e2e 的判据不受步骤变化干扰。 -->
        <el-button v-if="pendingSeg" size="small" type="warning" plain
                   @click="emit('cancel-seg')">✖ 取消补段</el-button>
      </div>
    </div>

    <div class="seg-row">
      <span class="dot main"></span>
      <span class="name">主段 · 从 0:00 起 · calibration.json</span>
      <el-tag type="success" size="small">当前生效</el-tag>
    </div>

    <div v-for="(s, i) in segs" :key="i" class="seg-row">
      <span class="dot sub"></span>
      <span class="name">{{ s }}</span>
      <el-button size="small" type="danger" plain @click="emit('del-seg', i)">删除</el-button>
    </div>

    <div v-if="segMd" class="segmd">{{ segMd }}</div>

    <div class="foot">
      段起点规则：新段时刻须 > 0 且晚于现有最后一段（早于已有时段没有意义会被拒绝）；删除段不影响主段。
    </div>
  </div>
</template>

<style scoped>
.head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
.title { font-size: 15px; font-weight: 600; }
.segbtns { display: flex; align-items: center; gap: 8px; flex: none; }
.seg-row { display: flex; align-items: center; gap: 10px; padding: 7px 0; font-size: 14px; }
.seg-row .name { flex: 1; }
.dot { width: 10px; height: 10px; border-radius: 50%; flex: none; }
.dot.main { background: #67c23a; }
.dot.sub { background: #409eff; }
.segmd { white-space: pre-wrap; font-size: 13px; color: #a3a6ad; margin: 10px 0 0; line-height: 1.7; }
.foot { font-size: 12.5px; color: #8d9098; margin-top: 14px; line-height: 1.7; }
</style>
