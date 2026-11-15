<script setup>
// 右栏下半：判定依据卡（常显「时刻 + 档位 + ⓘ」）+ 两排按钮 + 出手人 + 反馈摘要。
//
// 依据卡的每个数字都是**服务端算好的**：terms{base,up,down,pos} 与 reason 由
// /api/state 下发（api_session.py:112-127）；原型里前端自拼的 sideScore/
// posScore/confTerms/reason() 一律不实现（R20）。旧版 events.json 会给出
// ev==null / net==null，卡要少画那几行，而不是显示 0。
import { computed } from "vue";
import { dispT } from "../shared/time.js";
import { stripMd } from "../shared/md.js";
import { tierOf } from "./tiers.js";

const props = defineProps({
  row: { type: Object, required: true },
  offset: { type: Number, default: 0 },       // 时刻校准偏移（R34：卡头的时刻与表格同源）
  verdict: { type: String, default: "" },     // "" | "goal" | "not_goal"
  thumbFlag: { type: Boolean, default: false },
  crossFlag: { type: Boolean, default: false },
  feedback: { type: Object, default: () => ({}) },
  // 误标采样的**全集**（`state.flags.{thumb,cross}`，服务端下发的 key 清单）。
  // 上面两个布尔是「当前行标没标」，只够画按钮态；计数与清单得看全集。
  flags: { type: Object, default: () => ({ thumb: [], cross: [] }) },
});
// 右栏**没有**打标签控件：原型右栏两排按钮只有 ✅是进球 / ❌未进球 / 🗑删除 /
// ⚠缩略图有误 / ✚十字有误（`proto-edit-inventory.md` §6 与「右栏按钮两排」），
// 打标签的入口是**表格标签列**（allow-create）与**键盘 1–9** —— 见 Task 6。
// 所以这里既没有 tags prop 也不 emit("tag")（复审发现计划多写了三处死声明）。
const emit = defineEmits(["verdict", "flag", "delete"]);

const ev = computed(() => props.row.ev);
const net = computed(() => props.row.net);
const terms = computed(() => props.row.terms);
const num = (v) => (v == null ? "—" : Number(v).toFixed(2));

// 误标采样计数与清单（A-5）：数据**已经在 payload 里**（服务端 `_load_flag_set`
// 读 thumb_flag.json / cross_flag.json），旧界面是常显的
// 「⚠ 已标记 N 张缩略图有误：k1、k2…」（`app.py:1815-1833`）。原型把清单挂在
// `title` 上（悬停看）。清单用**服务端下发的 key 原串**——旧界面显示的也正是它。
const flagCount = computed(() => ({
  thumb: (props.flags?.thumb || []).length,
  cross: (props.flags?.cross || []).length,
}));
const flagKeys = computed(() => {
  const parts = [];
  if (flagCount.value.thumb) {
    parts.push("⚠ 缩略图有误：" + (props.flags.thumb || []).join("、"));
  }
  if (flagCount.value.cross) {
    parts.push("✚ 十字有误：" + (props.flags.cross || []).join("、"));
  }
  return parts.join("\n") || "（还没标记）";
});

// 完整数据块：字段名与 events.json 逐字一致（调优取证时要能对着原档看）
const raw = computed(() => {
  const e = ev.value;
  if (!e) return "";
  return [
    `cross_off   ${e.crossOff}`,
    `above_n     ${e.aboveN}`,
    `below_n     ${e.belowN}`,
    `above_span  ${e.aboveSpan}`,
    `below_span  ${e.belowSpan}`,
    `descent_px  ${e.descentPx}`,
    `below_big   ${e.belowBig}`,
    `pop_back    ${e.popBack}`,
    `net_post    ${net.value ? net.value.post : "None"}`,
    `net_ctrl    ${net.value ? net.value.ctrl : "None"}`,
    `net_local   ${net.value ? net.value.local : "None"}`,
  ].join("\n");
});
</script>

<template>
  <div class="basis">
    <div class="head">
      <span class="t">{{ dispT(row, offset) }}</span>
      <el-tag size="small" effect="dark" :type="tierOf(row).type">
        {{ tierOf(row).text }}
      </el-tag>
      <el-popover placement="top-start" :width="540" trigger="hover" :show-after="120">
        <template #reference>
          <el-icon class="conf-ico"><InfoFilled /></el-icon>
        </template>
        <div class="pop">
          <div class="pop-title">判定依据（鼠标移开即收起）</div>
          <template v-if="ev && terms">
            <div>置信度 <b>{{ row.conf.toFixed(2) }}</b> ＝ 基础 {{ num(terms.base) }}
              ＋ 上方证据 {{ num(terms.up) }} ＋ 下方证据 {{ num(terms.down) }}
              ＋ 位置 {{ num(terms.pos) }} ＋ 网 {{ num(terms.net) }}</div>
            <div class="dim">档位：≥0.60 🟢高置信 · 0.50~0.59 🟡待确认 · 更低或被否决门槛拦截 = ⚪低置信（低置信不进集锦）；硬门拦截（压筐沿/球体未再现/疑似弹出/兜底/B2 注入）= 恒 🟡待确认，置信度再高也不升绿；网：静止 −0.30 · 非局部 −0.26 · 网动/未测 0.00</div>
            <div v-if="terms.legacy" class="dim">（旧版结果的置信度未含网分项——算式对不上账是正常的，重跑检测后一致）</div>
          </template>
          <template v-if="net">
            <div>网区信号 {{ net.post }}（阈 5.0） · 对照区 {{ net.ctrl }} · 同对局部比
              <span :style="{ color: net.local ? '#67C23A' : '#f56c6c' }">
                {{ net.local ? "✓ 通过" : "✗ 等亮" }}</span>（门 ≥3×）</div>
            <div class="dim">网区 ≥5.0 且同对局部比 ≥3× 才算真网摆；网动不加分（擦网/砸筐传振同样让网动）</div>
          </template>
          <div class="reason">📢 {{ row.reason }}</div>
          <template v-if="ev">
            <el-divider />
            <div>完整判定数据</div>
            <div class="dim">（轨迹/下落/网动原始字段，调优取证用）</div>
            <pre class="rawv">{{ raw }}</pre>
          </template>
          <!-- v18 全行测网后 net_* 为 None 只剩 fail-open 一种形态（测量
               失败：视频打不开/seek 出界）——证据缺失不当否证，网分按 0、
               档位不受影响。 -->
          <div v-if="!net" class="warn">net_* 为 None：网信号测量失败（fail-open）——证据缺失不当否证，网分按 0.00 计、档位不受影响。</div>
        </div>
      </el-popover>
    </div>

    <div class="rowbtns">
      <el-button type="success" size="large" style="flex: 1"
                 :plain="verdict === 'goal'"
                 title="误判反馈专用：被降级的真进球在此翻案（写 feedback.json 给判定调优）——正常确认进球用空格勾选即可"
                 @click="emit('verdict', 'goal')">
        ✅ 是进球 <span class="dim">翻案反馈</span>
      </el-button>
      <el-button type="danger" size="large" style="flex: 1"
                 :plain="verdict === 'not_goal'"
                 title="误判反馈：检测认为进了但实际没进——写 feedback.json 给判定调优，并取消进片"
                 @click="emit('verdict', 'not_goal')">
        ❌ 未进球 <span class="dim">X</span>
      </el-button>
    </div>

    <div class="subbtns">
      <el-button plain style="flex: 1" title="删除此球（D）——直接删不弹确认，非破坏：可 Ctrl+Z 撤销或「⋯ 恢复已删」找回" @click="emit('delete')">
        🗑 删除 <span class="dim">D</span>
      </el-button>
      <el-button plain style="flex: 1" :type="thumbFlag ? 'warning' : 'info'"
                 @click="emit('flag', 'thumb')">
        {{ thumbFlag ? "✅ 已标缩略图有误" : "⚠ 缩略图有误" }}
      </el-button>
      <el-button plain style="flex: 1" :type="crossFlag ? 'warning' : 'info'"
                 @click="emit('flag', 'cross')">
        {{ crossFlag ? "✅ 已标十字有误" : "✚ 十字有误" }}
      </el-button>
    </div>

    <div class="shooter">
      <template v-if="row.shooter">
        <span class="lbl">出手人 {{ row.shooter.dot }}
          {{ row.shooter.releaseT != null ? row.shooter.releaseT.toFixed(2) + "s" : "" }}</span>
        <img v-for="(u, i) in row.shooter.thumbs" :key="i" :src="u" class="shthumb" />
      </template>
      <span v-else class="lbl">尚无出手人回扫结果——点顶栏「🧍 识别出手人」生成动图</span>
      <el-link type="primary" href="/wall.html">在出手人墙中查看 →</el-link>
    </div>

    <div class="fbref">
      <!-- 反馈摘要（A-10）：文案是服务端那份，只在**显示层**剥掉 markdown 标记
           （`**0**` → `0`、`` `feedback.json` `` → `feedback.json`）。 -->
      <span>{{ stripMd(feedback.summaryMd) }}</span>
      <!-- 误标采样计数 + 清单（A-5），常显那块照原型 340-345 挂在摘要同一行 -->
      <template v-if="flagCount.thumb || flagCount.cross">
        <span> · </span>
        <span class="flagtip" :title="flagKeys">误标采样 ⚠{{ flagCount.thumb }} 张 ✚{{ flagCount.cross }} 个（悬停看清单）</span>
      </template>
    </div>
  </div>
</template>

<style scoped>
.basis { padding: 4px 14px 14px; }
.head { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #e5eaf3; }
.conf-ico { color: #7c7f86; font-size: 12px; cursor: help; }
.conf-ico:hover { color: #e8622d; }
.pop { font-size: 13px; line-height: 1.7; }
.pop-title { font-weight: 600; margin-bottom: 4px; }
.dim { color: #8b8e95; font-size: 12px; }
.reason { color: #9aa0aa; margin-top: 4px; }
.rawv { font-family: Consolas, monospace; white-space: pre; font-size: 12px;
        color: #cfd3dc; background: #1d1d1d; padding: 8px; border-radius: 6px; }
.warn { color: #e6a23c; font-size: 12px; }
.rowbtns, .subbtns { display: flex; gap: 8px; margin-top: 10px; }
.shooter { display: flex; align-items: center; flex-wrap: wrap; gap: 8px;
           margin-top: 12px; padding: 8px 10px; background: #232323;
           border-radius: 8px; font-size: 12.5px; }
.shthumb { height: 64px; border-radius: 6px; }
.fbref { margin-top: 12px; font-size: 11.5px; color: #7f838b; }
/* 清单挂在 title 上（原型同），但给个「可以悬停」的视觉线索 */
.flagtip { cursor: help; text-decoration: underline dotted; }
</style>
