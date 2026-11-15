<script setup>
// 右栏上半：切片播放器 + 分色进度条 + 播放控制（spec 3、原型 §4）。
//
// 用原生 <video>（不是 el-* 封装）：autoplay 必须配 muted——切片要等服务端
// 现切，注入时用户手势的激活窗口早过了，未静音的自动播放会被浏览器拦成
// 黑屏不动（旧界面踩过，app.py:948 同款处置）。默认 2× 也是旧界面的省时设定——
// 但**每次载源都要重设**，见下面 `applyRate` 那段注释（重构时丢过这一环）。
import { computed, ref, watch } from "vue";
import { clipUrl } from "../shared/media.js";
import { dispT, tsec } from "../shared/time.js";

const props = defineProps({
  row: { type: Object, default: null },
  videoPath: { type: String, default: "" },
  offset: { type: Number, default: 0 },
  before: { type: Number, default: 5 },      // 会话内生效的前后秒（R21）
  after: { type: Number, default: 2 },
});
const emit = defineEmits(["dur", "apply-all"]);

const videoEl = ref(null);
const playing = ref(true);
const rate = ref(2);                          // 默认 2×（旧界面同款）
const loop = ref(true);
const playheadPct = ref(0);

const src = computed(() => props.row
  ? clipUrl(props.videoPath, tsec(props.row, props.offset), props.before, props.after)
  : "");

// 进球前占比：蓝段到红线为止。红线 3px 要**骑在**交界上 ⇒ 蓝绿**两段各减
// 1.5px**（左右各让 1.5、合计 3px）；写 `- 1px` 会让总宽变成 100% + 1px，
// 红线右移 0.5px 并溢出容器（细节见模板里 `.tricolor` 那段注释）。
const goalPct = computed(() => {
  const total = (props.before || 0) + (props.after || 0);
  return total > 0 ? (props.before / total) * 100 : 100;
});

// 换行/切片重来时**两件事**都要做，都是承重的（复审 F-8：漏写后半句会让下一个
// 人以为这里只归零）：
//  ① 播放头归零——否则它会停在上一条的位置，看着像卡住；
//  ② `playing` 置回 true——新切片靠 `autoplay` 自己起播，而 `@play` 要等源就绪
//     才触发，这个赋值是**抢先**把图标打回「⏸（正在播）」；不置回的话，在上一颗
//     球按过 ⏸ 之后换行，图标会停在「▶」而实际在播。
watch(() => src.value, () => { playheadPct.value = 0; playing.value = true; });

function togglePlay() {
  const v = videoEl.value;
  if (!v) return;
  // ⚠ `play()` 返回 Promise，**必须**接住它的 rejection：源还没就绪时（切片
  // 404——服务端现切那几毫秒、或本机没有那个 mp4）它 reject
  // `NotSupportedError: The element has no supported sources.`，而**没人接的
  // rejection 会升级成页面级未捕获异常**——e2e 的 `page.on("pageerror")` 抓的
  // 就是它（Task 8 的「K/J/L/←/→ 没打出 JS 异常」实测就红在这一条上，
  // 见 task-8-report.md 的「K 键异常」）。用户侧同款场景：改完前后秒、切片
  // 还在重切的间隙里按 K。`autoplay` 属性那条路径不需要这个 catch——自动播放
  // 算法不把 Promise 交给脚本。
  // 接住即可、**不弹提示**：「没有可播的源」在复核流里是常态，不是错误。
  // 但也不**整条吞掉**：`console.warn` 一行，好在将来把「自动播放策略拦截 /
  // 解码失败」这类**别的** reject 原因捞回来（复审建议）。它只进控制台，
  // 不进 `pageerror` ⇒ e2e 的 `errs` 不受影响。
  if (v.paused) v.play().catch((e) => console.warn(e)); else v.pause();
}
// `seekBy` 不需要额外的 catch 网：`duration` 未就绪时读出来是 NaN，
// **下面那句** `Number.isFinite` 把它折成 0 ⇒ `currentTime` 被夹到 0
// （**不是** NaN——WebIDL 的 `double` 拒收非有限值，赋 NaN 会抛 TypeError；
// 复审实测：把那处折叠临时去掉，J/L/←/→ 各抛一条 `TypeError: … non-finite`
// ⇒ 那处折叠是**承重**的，不是装饰）。
// 实测：夹具这个「视频 404」场景下连按 J/L/←/→ 一条异常都没多出来。
function seekBy(d) {
  const v = videoEl.value;
  if (!v) return;
  const end = Number.isFinite(v.duration) ? v.duration : 0;
  v.currentTime = Math.max(0, Math.min(end, v.currentTime + d));
}
// 帧步进按固定 30fps 假设（原型同款，见 Global Constraints 的 R35）。
// ⚠ 理由**不是**「切片都是 30fps 导出的」——`bbh/app.py` 的 `_cut_preview`
// 只做 `-ss/-t/scale/crf`、**没有任何 fps 归一**（无 `-r`），切片帧率 = 源帧率；
// 而 Plan A 的 `state_payload` 也没有 `fps` 字段。所以非 30fps 源上步进会偶尔
// 落在两帧之间、由浏览器就近吸附（差半帧的观感问题，不动数据）。
const stepFrame = (n) => seekBy(n / 30);
const onTime = () => {
  const v = videoEl.value;
  if (v && v.duration) playheadPct.value = (100 * v.currentTime) / v.duration;
};
function onBar(e) {
  const v = videoEl.value;
  const r = e.currentTarget.getBoundingClientRect();
  if (v && v.duration) v.currentTime = v.duration * ((e.clientX - r.left) / r.width);
}
// 倍速**每次载源都要重设**，不能只靠模板上写一次：
//  · `playbackRate` 不是内容属性（HTMLMediaElement 上没有 attribute 反射），
//    模板绑定只在挂载/值变化时写一次，换源不会再写；
//  · 而换源时浏览器会把它重置回 `defaultPlaybackRate`（默认 1）。
// 两条合起来的症状就是**列表第一条 2×、之后点开的每一条都 1×**，而下拉框仍显示
// 2×（静默不一致，用户 2026-09-25 报「很多视频播放默认不是 2 倍速」）。旧界面正是
// 为此写成 `onloadedmetadata="this.playbackRate=2"`（app.py:967，注释标了「复核」），
// 重构时丢了这一环。这里按旧界面同款在每次载源时重设，并**同时**写
// `defaultPlaybackRate`——这样浏览器自己那次「重置」也落在同一个值上。
// 两个事件都挂：`loadstart` 尽早（源一开始选就生效），`loadedmetadata` 兜底
// （它必定晚于上面那次重置）。
function applyRate() {
  const v = videoEl.value;
  if (!v) return;
  v.defaultPlaybackRate = rate.value;
  v.playbackRate = rate.value;
}
// 唯一写入口：改速率只走这里（下拉框、将来的快捷键），免得又出现「有地方漏设」。
const setRate = (r) => {
  rate.value = r;
  applyRate();
};
const setLoop = (v) => { loop.value = v; if (videoEl.value) videoEl.value.loop = v; };

defineExpose({ togglePlay, seekBy });
</script>

<template>
  <div class="clippanel">
    <div class="videobox">
      <!-- ⚠ 别写回 `:playbackRate="rate"`：那个绑定只在挂载/值变化时写一次，换源后
           浏览器把 playbackRate 重置回 1、它不会再写 ⇒ 静默 1× 而界面还显示 2×。
           载源重设见 `applyRate`。 -->
      <video ref="videoEl" :src="src" muted autoplay playsinline
             :loop="loop"
             @loadstart="applyRate" @loadedmetadata="applyRate"
             @timeupdate="onTime" @play="playing = true" @pause="playing = false" />
    </div>

    <!-- 三段宽度：蓝 + 红(3px) + 绿 必须正好 100%。红色那 3px 要**骑在**交界上，
         所以左右各让出 1.5px（合计 3px）——写 `- 1px` 会让总宽变成 100% + 1px，
         红线右移 0.5px 并溢出容器（`.seg-red { width: 3px }`，见下面样式）。 -->
    <div class="tricolor" @click="onBar"
         title="点击跳转：蓝=进球前 红=进球时刻 绿=进球后">
      <div class="seg-blue" :style="{ width: `calc(${goalPct}% - 1.5px)` }" />
      <div class="seg-red" />
      <div class="seg-green" :style="{ width: `calc(${100 - goalPct}% - 1.5px)` }" />
      <!-- 红线上的时刻标签与**表格时刻列同源**（都是 `dispT(row, offset)`）：
           红线画的就是切点，而切点吃校准偏移 ⇒ 标签也得吃。原先这里用服务端
           原串 `row.t`，偏移一旦可达就会与正下方 BasisPanel 卡头（R34 已修为
           `dispT`）分叉：表格写 00:02.7、卡头写 00:02.7，只有这一处写 00:01.2。
           （旧界面 `load_events_table(vp, offset)` 是「载入时整表位移」，屏幕上
           每一处时刻都同源——R34 修卡头用的就是这条理由。）
           ⚠ `left` 那个绑定不动：红线位置由前后秒决定，与偏移无关。 -->
      <span class="goalmk" :style="{ left: goalPct + '%' }">进球 {{ row ? dispT(row, offset) : "" }}</span>
      <span class="playhead" :style="{ left: playheadPct + '%' }" />
    </div>

    <div class="pctrl">
      <el-button-group size="small">
        <el-button title="后退一帧" @click="stepFrame(-1)">⏮ 1帧</el-button>
        <el-button @click="togglePlay">{{ playing ? "⏸" : "▶" }}</el-button>
        <el-button title="前进一帧" @click="stepFrame(1)">1帧 ⏭</el-button>
      </el-button-group>
      <el-select :model-value="rate" size="small" style="width: 78px"
                 @change="setRate">
        <el-option :value="0.5" label="0.5×" />
        <el-option :value="1" label="1×" />
        <el-option :value="2" label="2×" />
      </el-select>
      <el-switch :model-value="loop" size="small" active-text="循环"
                 @change="setLoop" />
      <span class="lbl">片段</span>
      <el-input-number :model-value="before" :min="2" :max="12" :step="0.5"
                       size="small" style="width: 92px"
                       @change="(v) => emit('dur', { before: v, after })" />
      <span class="lbl">前 /</span>
      <el-input-number :model-value="after" :min="1" :max="6" :step="0.5"
                       size="small" style="width: 92px"
                       @change="(v) => emit('dur', { before, after: v })" />
      <span class="lbl">后</span>
      <el-button size="small" plain @click="emit('apply-all', { before, after })">
        应用到全部
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.clippanel { padding: 10px 14px 4px; }
.videobox { background: #000; border-radius: 8px; overflow: hidden; }
.videobox video { display: block; width: 100%; max-height: 52vh; background: #000; }
.tricolor { position: relative; margin-top: 8px; height: 26px;
            display: flex; align-items: stretch; cursor: pointer; user-select: none; }
.seg-blue { background: linear-gradient(90deg, #2b5f9e, #3d7ad1);
            border-radius: 13px 0 0 13px; }
.seg-red { width: 3px; background: #f56c6c; }
.seg-green { background: linear-gradient(90deg, #3f9e4f, #59b86a);
             border-radius: 0 13px 13px 0; }
/* 「进球 00:12.1」这个标注**指的就是红线位置**（= `goalPct`），所以 left 由
   goalPct 驱动，不再写死 6px（写死时它指着色带最左端，与红线无关 = 标注指错
   地方）。`translateX(-14px)` + `nowrap` 照原型（前端原型/剪辑台.html:55）：
   标签自己约 28px 宽，左移半个身位让它骑在红线上，而不是从红线往右铺。
   26px 色带 / 红线 `calc(…% - 1.5px)` 的外形是有意设计，不动。 */
.goalmk { position: absolute; top: 5px; font-size: 10px; color: #f8b4b4;
          transform: translateX(-14px); white-space: nowrap; }
.playhead { position: absolute; top: 6px; width: 2px; height: 14px;
            margin-left: -1px; background: #fff; }
.pctrl { display: flex; flex-wrap: wrap; align-items: center; gap: 6px;
         margin-top: 10px; font-size: 12.5px; color: #cfd3dc; }
.lbl { color: #a3a6ad; }
</style>
