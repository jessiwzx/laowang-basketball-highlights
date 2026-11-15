<script setup>
// 快捷键总表（`proto-edit-inventory.md` §5 逐行照抄）。纯静态文案 +
// 当前标签库提示——打开就是查表，没有任何数据依赖。
const props = defineProps({
  modelValue: Boolean,
  tags: { type: Array, default: () => [] },
});
const emit = defineEmits(["update:modelValue"]);

const ROWS = [
  ["↑/↓", "上一球 / 下一球（自动滚动 + 播放器联动）"],   // 与原型同一行
  ["空格", "勾选「进片」——确认进球，勾上后自动跳下一个未勾选行；不写调优反馈"],
  ["X", "❌ 误判反馈「未进球」（否决 + 取消进片，证据快照存 feedback）"],
  ["1~9", "打标签（自动勾选进片——打标=认定该人的进球）"],
  ["D", "🗑 删除此球（直接删不弹确认，非破坏可恢复）"],
  ["K", "播放/暂停（专业剪辑惯例）"],
  ["J / L", "快退/快进 2 秒（专业剪辑惯例，同 ← →）"],
  ["Ctrl + Z", "撤销上一步（勾选/反馈/标签/删除都可撤）"],
  ["?", "打开本帮助"],
];
</script>

<template>
  <el-dialog class="helpdlg" :model-value="modelValue" width="520"
             title="⌨ 键盘流复核 — 快捷键总表"
             @update:model-value="(v) => emit('update:modelValue', v)">
    <table class="helptbl">
      <tbody>
        <tr v-for="[k, txt] in ROWS" :key="k">
          <td><kbd>{{ k }}</kbd></td>
          <td>
            {{ txt }}
            <span v-if="k === '1~9'" class="dim">当前标签库：{{
              tags.slice(0, 9).map((t, i) => `${i + 1}=${t}`).join("  ") || "（空）" }}</span>
          </td>
        </tr>
      </tbody>
    </table>
    <div class="note">
      ✅/❌ 按钮只给误判的球做调优反馈（翻案/否决）；正常确认进球用空格即可，不产生反馈数据。
    </div>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">知道了</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.helptbl { width: 100%; border-collapse: collapse; font-size: 13px; color: #cfd3dc; }
.helptbl td { padding: 5px 6px; border-bottom: 1px solid #2c2c2c; vertical-align: top; }
.helptbl td:first-child { width: 110px; }
kbd { padding: 0 6px; font-size: 11px; font-family: Consolas, monospace;
      background: #2a2a2a; border: 1px solid #4a4a4a; border-radius: 4px;
      border-bottom-width: 2px; color: #e5eaf3; }
.note { margin-top: 10px; font-size: 12.5px; color: #8b8e95; }
.dim { color: #7c7f86; font-size: 12px; }
</style>
