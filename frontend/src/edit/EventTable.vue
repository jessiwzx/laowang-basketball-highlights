<script setup>
// 表格：8 列（缩略图/进片/时刻/档位/标签/裁决/置信度/★精选）。
// ⚠ **★ 列必须留在最后**：e2e 有十几处按列序取单元格（`td.nth(2)` 时刻、
// `nth(3)` 档位、`TAGCELL=4` 标签）＋ CSS `.row-miss td:nth-child(3)`。
// 插到中间这些索引全部要 +1，漏改一处就是假绿/假红；放末尾则一处都不用动
// （2026-10-23 方案 §5 的取舍）。
// 行对象是服务端下发的权威值——**勾选/标签/精选都不回写本地行**，一律发
// 请求、用回包整份替换（actions.js 负责），否则界面上会先出现一个服务端
// 并不认可的状态，刷新就打脸。
import { ref } from "vue";
import { dispT } from "../shared/time.js";
import { tierOf } from "./tiers.js";

const props = defineProps({
  rows: { type: Array, required: true },              // 已按筛选过滤
  tags: { type: Array, default: () => [] },           // state.tags（全局标签库）
  verdicts: { type: Object, default: () => ({}) },    // state.verdicts（裁决列）
  emptyText: { type: String, default: "" },
  offset: { type: Number, default: 0 },               // 校准偏移（Task 9 传入）
});
const emit = defineEmits(["pick", "check", "tag", "star"]);

const tableRef = ref(null);

// 行态：goal / not_goal 来自 state.verdicts（键 = row.key）。
// 原型还有 `.row-deleted`——真实现里已删的球**根本不在 rows[] 里**
// （state.deleted 只存键），那条样式永远不可达，不实现。
function rowClass({ row }) {
  const v = props.verdicts[row.key];
  if (v === "goal") return "row-goal";
  if (v === "not_goal") return "row-miss";
  return "";
}

// 进度条颜色 = **档位色**（tiers.js 是配色的唯一来源）：与「档位」列的
// el-tag 恒一致。别再在这里写阈值字面量——9ba3682 起这里按 0.8/0.4 这对
// 谁也不认识的数着色，后来档位阈值换成 0.65/0.50（HIGH_CONF 还从 0.70
// 降过一次）没人同步它 ⇒ conf∈[0.65,0.8) 的高置信行会「绿标签 + 黄条」，
// 同一行两个结论（2026-10-11 用户报「77% 仍然是黄色」）。档位是后端的
// 判决（含硬门），条跟着它走才不会有第二套口径。
// v19（2026-10-18）：HIGH_CONF 0.65→0.60、网分加重（静止 −0.30 / 非局部 −0.26）；
// 上面括号里的 0.65 只是历史沿革记录，档位口径仍只认 tiers.js。
const confColor = (row) => tierOf(row).dot;

// 键盘流（Task 7）要能按 key 选中行：转成 el-table 的 setCurrentRow，
// 它会触发 current-change → 我们再 emit('pick')，父组件照常收到
function setCurrentRow(key) {
  const row = props.rows.find((r) => r.key === key);
  if (row && tableRef.value) tableRef.value.setCurrentRow(row);
}
defineExpose({ setCurrentRow });
</script>

<template>
  <el-table ref="tableRef" :data="rows" height="100%" size="small" border
            highlight-current-row :row-key="(r) => r.key"
            :row-class-name="rowClass" :empty-text="emptyText"
            @current-change="(r) => r && emit('pick', r.key)">
    <el-table-column label="" width="52" align="center">
      <template #default="{ row }">
        <img v-if="row.thumb" :src="row.thumb" class="rowthumb"
             :class="{ dim: !row.selection }"
             :title="row.selection ? '已勾选进片' : '未勾选'">
        <span v-else class="thumb-na">—</span>
      </template>
    </el-table-column>

    <el-table-column label="进片" width="54" align="center">
      <template #default="{ row }">
        <el-checkbox :model-value="row.selection"
                     @change="(v) => emit('check', row.key, v)" />
      </template>
    </el-table-column>

    <!-- 时刻列用服务端的 t 串（dispT：offset=0 原样用，非 0 才重算）。
         零填充的 mm:ss.s 字典序 = 时间序，所以 sortable prop="t" 直接可用 -->
    <el-table-column label="时刻" width="84" sortable prop="t">
      <template #default="{ row }">{{ dispT(row, offset) }}</template>
    </el-table-column>

    <el-table-column label="档位" width="86">
      <template #default="{ row }">
        <el-tag size="small" effect="dark" :type="tierOf(row).type">{{ tierOf(row).text }}</el-tag>
      </template>
    </el-table-column>

    <el-table-column label="标签">
      <template #default="{ row }">
        <el-select :model-value="row.tag || ''" size="small" clearable filterable
                   allow-create placeholder="打标签" style="width: 100%"
                   @change="(v) => emit('tag', row.key, v || '')">
          <el-option v-for="t in tags" :key="t" :label="t" :value="t" />
        </el-select>
      </template>
    </el-table-column>

    <el-table-column label="裁决" width="58" align="center">
      <template #default="{ row }">
        <span v-if="verdicts[row.key] === 'goal'" class="mark-yes">✔</span>
        <span v-else-if="verdicts[row.key] === 'not_goal'" class="mark-no">✘</span>
        <span v-else class="mark-na">—</span>
      </template>
    </el-table-column>

    <el-table-column label="置信度" width="96">
      <template #default="{ row }">
        <el-progress :percentage="Math.round(row.conf * 100)" :stroke-width="6"
                     :color="confColor(row)" />
      </template>
    </el-table-column>

    <!-- ★ 精选（2026-10-23）：单条发片的选片标记，**必须是最后一列**（见头部
         注释）。点星 = 选中 + 自动勾上进片（服务端保证），所以这一列在
         「进片」之后反而更安全：用户看到勾选同步亮起来。 -->
    <el-table-column label="★" width="44" align="center">
      <template #default="{ row }">
        <span class="star" :class="{ on: row.starred }"
              :title="row.starred ? '已精选：单条生成会出这一条（再点取消）'
                                  : '点一下精选：单条发片时只出打了星的球（会自动勾上进片）'"
              @click.stop="emit('star', row.key, !row.starred)">{{ row.starred ? "★" : "☆" }}</span>
      </template>
    </el-table-column>
  </el-table>
</template>

<style scoped>
.rowthumb { display: block; width: 38px; height: 30px; margin: 0 auto;
            border-radius: 4px; }
.rowthumb.dim { opacity: .45; }
.thumb-na { color: #4a4a4a; font-size: 11px; }
.mark-yes { color: #67C23A; font-size: 16px; }
.mark-no { color: #f56c6c; font-size: 16px; }
.mark-na { color: #4a4a4a; }
/* ★ 精选：可点的星形开关（44px 列，热区靠 padding 撑到整格） */
.star { display: inline-block; padding: 2px 6px; font-size: 16px;
        line-height: 1; color: #5c5f66; cursor: pointer; user-select: none;
        border-radius: 4px; }
.star:hover { color: #e6a23c; background: rgba(230, 162, 60, .12); }
.star.on { color: #e6a23c; }
:deep(.el-table .row-goal) { --el-table-tr-bg-color: rgba(103, 194, 58, .07); }
:deep(.el-table .row-miss) { --el-table-tr-bg-color: rgba(245, 108, 108, .06); }
:deep(.el-table .row-miss td:nth-child(3) .cell) {
  text-decoration: line-through; opacity: .6;   /* 否决的球把时刻划掉 */
}
</style>
