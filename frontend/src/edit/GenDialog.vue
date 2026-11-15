<script setup>
// 生成弹窗（2026-10-21 改版）：选择 → 进度 → 结果，一窗三态。
//
// 与旧版的两处硬差别：
// ① **画幅与输出方式都收进弹窗，且都不设默认**（用户 2026-10-21 裁定）。
//    原来画幅由顶栏两个按钮决定（横屏集锦 / 竖屏集锦），两个入口已合并成
//    一个「生成」——入口不再表达画幅，所以必须在这里显式选，且「开始生成」
//    三重门：画幅 + 输出方式 + 至少一项勾选。
// ② 输出方式 = 一条集锦 / 逐球各出一条。后者是「每个进球一个独立成品」，
//    一次勾 30 个球就出 30 个文件。
//
// 清单来自 /api/gen/choices。**画幅/输出方式一变就要重拉**，原因有两个：
//   · stats 的画幅前缀（📱 竖屏运镜 / 🎬 横屏直切）由服务端拼；
//   · 合集项与精选项的展示文案两模式都不同（合集：「视频合集（全部勾选的
//     进球）」↔「全部勾选的进球（逐球各出一条）」；精选：「打星的球合成
//     一条」↔「只出打星的球」）。
// 而这些文案**就是勾选值本身**（pick 里存字符串），所以切模式时必须用服务端
// 回的 allLabel / starLabel 做一次重映射，否则那一项勾选会凭空丢掉（自查
// P0-1 的前端侧对应问题；2026-10-24 集锦也支持精选后 starLabel 同样是两串）。
//
// job 不归它管：长任务由 EditBay 起与跟随，这里只是 job 的视图，所以关掉
// 弹窗再打开，进度还在。进度文案只显示服务端 job.msg（R10）。
import { computed, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { api, qs } from "../shared/api.js";

const props = defineProps({
  modelValue: Boolean,
  job: { type: Object,
         default: () => ({ phase: "idle", msg: "", pct: 0, result: null }) },
});
const emit = defineEmits(["update:modelValue", "start", "reset"]);

// 画幅：null=未选（强制必选）。**用字符串而不是 Boolean**：Vue 的 Boolean
// prop 会把 null 归一成 false，那样"没选"和"选了横屏"就分不开了。
const orient = ref(null);          // "h" | "v" | null
const output = ref(null);          // "reel" | "single" | "hook4" | null
const choices = ref([]);
const pick = ref([]);
const stats = ref("");
const allLabel = ref("");          // 服务端给的「合集项」当前文案
const starLabel = ref("");         // 服务端给的「★ 精选」文案（两模式两串）
const starN = ref(0);              // 已打星球数（0 时单条模式要提示先打星）
const lastOutput = ref(null);      // 上次拉取时的输出模式（判「模式变了」）
const loading = ref(false);

const running = computed(() => props.job.phase === "running");
const settled = computed(() => props.job.phase === "done"
                              || props.job.phase === "error");
const ready = computed(() => !!orient.value && !!output.value
                          && !!pick.value.length);

const REEL_TIP = "一条集锦：把所有勾选的进球拼成**一个**视频。"
  + "勾「★ 精选」时只把打了星的球拼成一条（清单里没有这一项 = 一颗星都还没打）。";
const SINGLE_TIP = "逐球各出一条：每个勾选的进球**各自**一个独立成品文件"
  + "（一次勾 30 球 = 30 个文件，逐条发平台用）。勾「★ 精选」时只出打了星的球。";
// 2026-11-01 新增第三种输出方式。**横竖都支持**（同日从"恒横屏"放开）：
// 横版是 ffmpeg 从源片直切（一次编码、快）；竖版走 vrender 的跟球运镜
// ——同一套 4s 钩子 + 7+7+7 结构，但全程常速不慢放（用户要求"进球的那个
// 画面可以不需要慢放"）。两条路产出时长一样，画幅文案跟着 orient 走。
const HOOK4_TIP = {
  h: "横版：前 4 秒是这 3 个球的穿网瞬间（各约 1.3s），接 7+7+7 秒完整球。"
    + "从源片直切、一次编码，8 Mbps。",
  v: "竖版：同一套结构，但每球走**跟球运镜**（镜头随球从运球移到篮筐），"
    + "**进球画面不慢放**（全程常速）。逐帧渲染，比横版慢不少。",
};

const ASPECT_TIP = {
  h: "横屏直切：按勾选时刻直接切片拼装（顺序原样、字节不动），导出最快。",
  v: "竖屏运镜：逐球跟球运镜 ＋ 出手慢放 0.6× ＋ 片头钩子 ＋ 爆点前置，适合手机发布。",
};
const SINGLE_ASPECT_TIP = {
  h: "横屏单条：按该球的前后秒直接切片，单文件导出。",
  v: "竖屏单条：该球单独走一遍跟球运镜 ＋ 慢放 ＋ 结尾定格 0.5s"
    + "（单条不挂片头钩子、不做白闪转场——那是集锦的片间效果）。",
};

const title = computed(() => {
  if (!orient.value || !output.value) return "生成视频（先选画幅与输出方式）";
  const a = orient.value === "v" ? "竖屏" : "横屏";
  const icon = orient.value === "v" ? "📱" : "🎬";
  if (output.value === "hook4") {
    return `⚡ 生成 25s 精选（${a} · 4s 钩子 · 每 3 球一条）`;
  }
  return output.value === "single"
    ? `${icon} 生成${a}单条（逐球各出一条）`
    : `${icon} 生成${a}集锦`
      + (orient.value === "v" ? "（跟球运镜）" : "（直切）");
});
const tips = computed(() => {
  if (!orient.value || !output.value) {
    return ["先选画幅与输出方式：两项都不预选，避免手快点了默认。"];
  }
  // hook4 的画幅文案不能套 ASPECT_TIP：那两句是「集锦」的口径（横屏直切按
  // 勾选时刻切片／竖屏运镜带出手慢放），而这个模式的结构完全不同（4s 钩子
  // 把三个穿网瞬间前置、竖版还不慢放）。说错会让人以为它复用了集锦管线。
  if (output.value === "hook4") {
    return [
      "25s 精选（4s 钩子）：把勾选的球按**每 3 球一条**重排成 25 秒。"
        + "勾 24 球 = 8 条。",
      HOOK4_TIP[orient.value],
    ];
  }
  const m = output.value === "single" ? SINGLE_ASPECT_TIP : ASPECT_TIP;
  return [m[orient.value], output.value === "single" ? SINGLE_TIP : REEL_TIP];
});

// 拉清单。vertical 只在已选画幅时传；没选就不传（服务端走中性统计行，
// 不带画幅前缀——带了就等于变相默认）。
async function load() {
  loading.value = true;
  try {
    const d = await api.get("/api/gen/choices" + qs({
      vertical: orient.value ? orient.value === "v" : undefined,
      output: output.value || "reel",
    }));
    const prevAll = allLabel.value;
    const prevStar = starLabel.value;
    const nextAll = d.allLabel || "";
    const nextStar = d.starLabel || "";
    const inside = (x) => (d.choices || []).includes(x);
    // 只在**输出模式真的变了**时才动「合集 ↔ 精选」的映射。只改画幅时两边的
    // 合集串相同（allLabel 只由 output 决定），若也套用这条规则，用户明明勾着
    // 「全部勾选的进球」点一下画幅就被换成「★ 精选」——勾选被悄悄改掉。
    const modeChanged = lastOutput.value !== null
      && lastOutput.value !== (d.output || "reel");
    lastOutput.value = d.output || "reel";
    choices.value = d.choices || [];
    allLabel.value = nextAll;
    starLabel.value = nextStar;
    starN.value = Number(d.starred) || 0;
    const prevPick = pick.value;
    const mapOne = (x) => {
      if (x === prevAll) {
        // 合集类项：切到单条且已有精选 ⇒ 换成「★ 精选」（单条的主路径就是
        // 只出打星的几条）；精选为空则老实留在合集，并在下方提示去打星。
        if (modeChanged && nextStar && inside(nextStar)) return nextStar;
        return nextAll;
      }
      if (x === prevStar) {
        // 精选 → 换到当前模式的精选串（2026-10-24 集锦也有这一项了）。
        // 只在**勾选确实换了模式**时才迁：只改画幅时 prevStar === nextStar，
        // 原样返回。落空（切到集锦但一颗星都没有 ⇒ 服务端不列这一项）才退回
        // 合集，否则 filter(inside) 会把它悄悄删掉、勾选凭空少一项。
        if (nextStar && inside(nextStar)) return modeChanged ? nextStar : x;
        return nextAll;
      }
      return x;
    };
    pick.value = prevPick.length
      ? [...new Set(prevPick.map(mapOne))].filter(inside)
      : (d.default || []);
    stats.value = d.stats || "";
  } catch (e) {
    ElMessage.error(e.message);     // 400「尚无检测结果——先点「▶ 开始检测」」等
  } finally {
    loading.value = false;
  }
}

// 每次打开：三项选择归零（不设默认 = 不做"上次选择"记忆），并重拉清单。
// job 不在 idle（还在跑 / 已出结果）时直接呈现 job 态，不重拉也不重置。
watch(() => props.modelValue, async (v) => {
  if (!v || props.job.phase !== "idle") return;
  orient.value = null;
  output.value = null;
  pick.value = [];
  allLabel.value = "";
  starLabel.value = "";
  starN.value = 0;
  lastOutput.value = null;     // 重开弹窗：模式比较从头算，别吃上一次的残值
  await load();
});

// 「25s 精选」不再锁画幅（2026-11-01）：横竖两条实现都齐了，锁死反而多一道
// 无谓的自动改动——用户选哪个就出哪个，跟另外两种模式的交互一致。

// 画幅/输出方式变化 → 重拉（stats 前缀 + 合集文案都变）。跑动中不打扰。
watch([orient, output], async () => {
  if (!props.modelValue || props.job.phase !== "idle" || loading.value) return;
  await load();
});

const start = () => emit("start", {
  vertical: orient.value === "v",
  output: output.value,
  items: [...pick.value],
});
</script>

<template>
  <el-dialog class="gendlg" :model-value="modelValue" width="600" :title="title"
             :close-on-click-modal="false"
             @update:model-value="(v) => emit('update:modelValue', v)">
    <!-- 跑中：服务端的 msg + pct（单条模式按条数推进度） -->
    <div v-if="running" class="gprog">
      <el-progress :percentage="job.pct" :stroke-width="14" />
      <div class="gmsg">{{ job.msg }}</div>
    </div>

    <!-- 终态：错误 / 结果清单（videos[].url 可能为 null：文件被删或名字对不上）。
         失败原因由 EditBay 统一 toast（R15），这里不重复正文。 -->
    <template v-else-if="settled">
      <el-result :icon="job.phase === 'error' ? 'error'
                        : (job.result.videos.length ? 'success' : 'warning')"
                 :title="job.phase === 'error' ? '生成失败'
                        : (job.result.videos.length ? '生成完成' : '没有生成任何视频')" />
      <div v-if="job.result" class="glist">
        <div v-for="(v, i) in job.result.videos" :key="i" class="genfile">
          <span class="gitem">{{ v.item }} → {{ v.name }}</span>
          <a v-if="v.url" :href="v.url" target="_blank" class="gprev">▶ 在线预览</a>
          <span v-else class="gmiss">（文件已不在）</span>
        </div>
        <!-- 统计行 + 跳过行只在 summaryMd 里，逐字显示；不重解析（那是
             _gen_result 的活，前端再解析一遍就成了第二份格式契约） -->
        <pre class="glog">{{ job.result.summaryMd }}</pre>
      </div>
    </template>

    <!-- 选择态：两个必选维度 + 勾选清单 -->
    <template v-else>
      <div class="gq">画幅</div>
      <el-radio-group v-model="orient" class="grow">
        <el-radio-button value="h">🎬 横屏直切</el-radio-button>
        <el-radio-button value="v">📱 竖屏运镜</el-radio-button>
      </el-radio-group>
      <div class="gq">输出方式</div>
      <!-- 三项（2026-11-01 加第三项）：放不下时换行，别溢出弹窗 -->
      <el-radio-group v-model="output" class="grow gwrap">
        <el-radio-button value="reel">📦 一条集锦</el-radio-button>
        <el-radio-button value="single">🎞 逐球各出一条</el-radio-button>
        <el-radio-button value="hook4">⚡ 25s 精选</el-radio-button>
      </el-radio-group>

      <el-alert v-for="(t, i) in tips" :key="i" :title="t" type="info"
                :closable="false" show-icon class="gtip" />

      <div class="gstats">{{ stats }}</div>
      <!-- 单条模式还没打星：这里必须明说，否则清单里没有预选项、用户以为坏了。
           不打星时仍然可以勾「全部勾选的进球」全出——所以是提示不是错误。 -->
      <el-alert v-if="output === 'single' && starN === 0" type="warning"
                :closable="false" show-icon class="gtip"
                title="★ 还没有打星的球。单条模式默认只出打星的球——回表格给想单发的球点 ★（或选中行按 S），或直接勾下面的「全部勾选的进球」全出。" />
      <div class="gq">要生成哪些（每一项下面的球都会进）？</div>
      <el-checkbox-group v-model="pick" class="gpick">
        <!-- 横排（2026-10-25）：人物标签左到右流式排列，一行 5~6 个；
             合集/精选两串长文案独占整行（见 .gwide）。**只是样式**——仍是
             同一个 el-checkbox-group + v-model="pick"，DOM 顺序没动
             （e2e 按 `.gpick .el-checkbox` 的索引取项）。 -->
        <el-checkbox v-for="c in choices" :key="c" :value="c"
                     :class="{ gwide: c === allLabel || c === starLabel }">
          {{ c === allLabel ? `📦 ${c}` : c }}
        </el-checkbox>
      </el-checkbox-group>
      <div v-if="!ready" class="ghint">
        画幅、输出方式、至少一项勾选——三项都齐才能开始。
      </div>
    </template>

    <template #footer>
      <el-button @click="emit('update:modelValue', false)">关闭</el-button>
      <el-button v-if="settled" @click="emit('reset')">↻ 重新选择</el-button>
      <el-button v-else type="primary" :loading="running"
                 :disabled="!ready || running" @click="start">
        🎬 开始生成
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.gprog { margin-top: 16px; }
.gmsg { margin-top: 8px; font-size: 13px; color: #cfd3dc; }
.gq { margin-top: 14px; font-size: 13.5px; color: #e5eaf3; }
.grow { margin-top: 6px; }
/* 输出方式三项：600px 弹窗里「📦 一条集锦 / 🎞 逐球各出一条 / ⚡ 25s 精选」
   加起来接近满宽，窄一点的屏会溢出，允许换行。 */
.gwrap { display: flex; flex-wrap: wrap; }
.gtip { margin-top: 8px; }
.gstats { margin-top: 10px; padding: 8px 10px; background: #222; border-radius: 8px;
          font-size: 12.5px; color: #cfd3dc; }
/* 勾选清单**横排**（2026-10-25 用户要求）：原来 flex-direction: column，16 个
   人物标签就是 16 行、直接把弹窗撑破。改成 wrap 流式排列，一行 5~6 个短名字，
   同样一屏能看全。
   ⚠ 两处细节都不是可选的：
   · `:deep(.el-checkbox)` 清掉 Element 自带的 margin-right:30px —— 间距统一交给
     flex 的 gap，否则行末会多出 30px 空洞、换行点算不准；
   · `white-space: nowrap` 防长名字被压成两行（横排后可用宽度比整行窄）。 */
.gpick { display: flex; flex-wrap: wrap; align-items: center;
         gap: 6px 10px; margin-top: 8px; }
.gpick :deep(.el-checkbox) { margin-right: 0; white-space: nowrap; }
.gpick :deep(.el-checkbox + .el-checkbox) { margin-left: 0; }
/* 合集 / ★ 精选这两串十几字的长文案**独占一整行**：让它们参与流式排布会把
   同一行剩下的宽度挤到只够一两个人物，看着参差；独占后人物区才是一整块。 */
.gwide { flex: 0 0 100%; }
.ghint { margin-top: 10px; font-size: 12px; color: #7f838b; }
/* 单条模式一次可能 30+ 行：列表必须自己滚，别把弹窗撑破 */
.glist { margin-top: 8px; max-height: 40vh; overflow: auto; }
.genfile { display: flex; align-items: center; gap: 10px; padding: 5px 8px;
           border-bottom: 1px dashed #3a3a3a; font-size: 13.5px; }
.gitem { flex: 1; color: #e5eaf3; }
.gprev { color: #f48553; font-size: 12.5px; }
.gmiss { color: #7f838b; font-size: 12px; }
.glog { margin-top: 10px; padding: 8px; background: #1d1d1d; border-radius: 6px;
        white-space: pre-wrap; font-family: Consolas, monospace;
        font-size: 12px; color: #9aa0aa; }
</style>
