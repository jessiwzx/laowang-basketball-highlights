<script setup>
import { ref, computed, onMounted, onUnmounted } from "vue";
import { ElMessage } from "element-plus";
import { api } from "../shared/api.js";
import { store } from "../shared/session.js";
import { hsvOf, nextDisabled, segDelPath } from "./calibkit.js";
import { stripMd } from "../shared/md.js";
import FramePicker from "./FramePicker.vue";
import PointMarker from "./PointMarker.vue";
import BuildPanel from "./BuildPanel.vue";
import HsvPanel from "./HsvPanel.vue";
import SegmentCard from "./SegmentCard.vue";
import ExistingDialog from "./ExistingDialog.vue";

// 会话态（videoPath，来自 /api/state）与标定态（/api/calib/state 的 _meta()）分两份：
// 前者与另两页同源，后者是本页的权威量（points/canBuild/hasPreview/ready/segments）。
const session = ref(null);
const st = ref(null);
const pathInput = ref("");
const step = ref(0);
const frameReady = ref(false);    // ①→② 的门：_meta() 里没有「帧是否就绪」这个键（R8）

const frameAt = ref(0);                       // 抽帧时间点（秒）
const frameRev = ref(1);                      // 自增计数：docstring 建议的 ?v= 加固
const natural = ref({ w: 0, h: 0 });          // 帧的真实像素尺寸（<img> @load 拿）
const frameObjUrl = ref("");                  // 帧图的对象 URL（换帧时 revoke）
const frameStamp = ref(0);                    // **屏上那张帧**的时刻（成功抽帧时定格）
const showExisting = ref(false);              // 「查看已有标定」摘要弹窗（只读，R9：不写 st）

// 抽帧：**必须**用 fetch 而不是 <img src> —— 抽帧失败时服务端会把 ffmpeg 诊断
// 放进 {error: 文案}（旧界面的核心诊断手段），<img> 的 error 事件拿不到响应体。
// 拿到 blob 后转对象 URL 喂 <img>：一次抽帧只发一个请求（不用 ?v=、不重复下载），
// 也不受浏览器缓存影响（frame.png 虽已发 no-store，对象 URL 从根上不涉及缓存）。
async function doExtract() {
  try {
    const r = await fetch(`/api/calib/frame.png?t=${frameAt.value}&v=${frameRev.value}`);
    if (!r.ok) {
      let msg = `${r.status} ${r.statusText}`;
      try { msg = (await r.json()).error || msg; } catch { /* 非 JSON 兜底 */ }
      throw new Error(msg);
    }
    const blob = await r.blob();
    if (frameObjUrl.value) URL.revokeObjectURL(frameObjUrl.value);
    frameObjUrl.value = URL.createObjectURL(blob);
    frameRev.value++;
    frameStamp.value = frameAt.value;   // 定格**这一张**的时刻：此后改输入框不许动标签
    natural.value = { w: 0, h: 0 };
    frameReady.value = true;
    await refreshCalib();      // 服务端已清 points / preview（R5）⇒ 回包即权威
  } catch (e) { ElMessage.error(e.message); }
}

function onFrameLoaded(n) { natural.value = n; }

async function refreshCalib() {
  try { st.value = await api.get("/api/calib/state"); }
  catch (e) { ElMessage.error(e.message); }
}

function onState(s) {
  session.value = s;
  // 只在输入框还空着时回填：用户正在改路径时别被别的请求顶掉（照抄剪辑台）
  if (!pathInput.value) pathInput.value = s.videoPath || s.lastVideo || "";
}

// @change = 回车或失焦。不用 watch 逐字触发（那会每敲一个字符发一次 POST /api/session）。
async function onPathChange() {
  const p = pathInput.value.trim();
  if (!p || p === session.value?.videoPath) return;
  try {
    await store.open(p);
    pathInput.value = session.value?.videoPath || p;   // 回填服务端清洗后的路径
    step.value = 0;                                    // 换视频 = 从头走向导
    frameReady.value = false;
    // 换视频同样要退出补段态：否则用户带着补段态切到另一个视频 ⇒ 新视频可能还没主标定，
    // 步 ③ 只剩「生成补充段」⇒ 同一个死路换个入口再来一遍（服务端 /api/calib/build
    // 的补段分支要求先有主标定 ⇒ 必 400，而 pendingSeg 只在成功时才清）。
    pendingSeg.value = false;
    await refreshCalib();
  } catch (e) { ElMessage.error(e.message); }
}

function goPrev() { if (step.value > 0) step.value--; }

async function goNext() {
  if (step.value >= 3) return;
  step.value += 1;
  if (step.value === 3) await syncHsv();    // 进门步 ④ 才取初值（省一次请求）
}

// 落点：服务端只存坐标（帧像素）。回包是 _meta() ⇒ 整份替换即权威。
async function addPoint(p) {
  // 第 6 点起前端拦（R19）：服务端不拒（docstring 明说「不加校验让点越攒越多」），
  // 原型冻结了这条 UX（「球最多点 3 次，够了」）⇒ 拦在前端才有那声提示。
  if ((st.value?.points || []).length >= 5) {
    ElMessage.info("球最多点 3 次，够了");
    return;
  }
  try { st.value = await api.post("/api/calib/point", { x: p.x, y: p.y }); }
  catch (e) { ElMessage.error(e.message); }
}

// 逐点撤销（R17）：后端只有「清空全部」⇒ DELETE 后按原顺序重发其余点。
// keep 为空时不发 POST，改取 /state 拿权威回包（DELETE 的回包同样可用，但统一走 refreshCalib 更省心）。
async function undoPoint(i) {
  const keep = (st.value?.points || []).filter((_, k) => k !== i);
  try {
    await api.del("/api/calib/point");
    if (!keep.length) { await refreshCalib(); return; }
    for (const [x, y] of keep) st.value = await api.post("/api/calib/point", { x, y });
  } catch (e) { ElMessage.error(e.message); }
}

async function clearPoints() {
  try { st.value = await api.del("/api/calib/point"); }
  catch (e) { ElMessage.error(e.message); }
}

const pendingSeg = ref(false);        // true = 下一次生成走补段（写 calibration_segN.json）

// 预览图 src：优先用服务端给的 previewUrl（带 rev），退化到裸端点（no-store ⇒ 永远新鲜）。
// previewUrl 不在 _meta() 八键里，任何后续 GET /state 都会把它抹掉（R21）。
const previewSrc = computed(() =>
  st.value?.previewUrl || (st.value?.hasPreview ? "/api/calib/preview.png" : ""));

async function doBuild() {
  try {
    // R6：主标定**必须**发 {}——服务端用 `segT is not None` 分流，发 {segT: 0} 会掉进
    // 补段分支再被 400。补段的起点取**屏上那张帧的时刻**（frameStamp，E15），不是输入框活值。
    const body = pendingSeg.value ? { segT: frameStamp.value } : {};
    st.value = await api.post("/api/calib/build", body);
    if (pendingSeg.value) pendingSeg.value = false;    // 补段已落地 ⇒ 回主模式
  } catch (e) { ElMessage.error(e.message); }          // 失败保持 pendingSeg：改时间点可重试
}

const sMin = ref(86);            // 原型默认（R10 的兜底，无标定时用）
const vMin = ref(60);

// 步 ④ 的滑块初值：_meta() 不含 HSV ⇒ 唯一权威源是 GET /existing 的 calibration.hsvLow（R10）。
// **每次**进步 ④ 都重取：生成/补段会按球点重算色域（app.py:220-246），初值必须是刚生成的那份。
async function syncHsv() {
  try {
    const ex = await api.get("/api/calib/existing");
    const h = hsvOf(ex, { s: 86, v: 60 });
    sMin.value = h.s;
    vMin.value = h.v;
  } catch (e) { ElMessage.error(e.message); }
}

async function doRefine() {
  try { st.value = await api.post("/api/calib/refine", { sLow: sMin.value, vLow: vMin.value }); }
  catch (e) { ElMessage.error(e.message); }
}

// 原型冻结：这一步只弹提示，**不发请求**（标定在生成/重算时已落盘）。
function doFinish() {
  ElMessage.success("标定完成！已存 calibration.json —— 可去剪辑台开始检测");
}

// 新增补充段：重置向导回步①。**必须**把 frameReady 打回 false —— 补段要用
// 镜头挪动**之后**的新画面重新抽帧（服务端抽帧会一并清点，R5），
// 不然用户会拿主标定那帧的点去补段。
function addSeg() {
  pendingSeg.value = true;
  step.value = 0;
  frameReady.value = false;
  ElMessage.info("回到第①步：抽挪动后画面的帧，重新走一遍标点");
}

// 取消补段：清标志 + 回步 ① + 打回 frameReady（与 addSeg **同一套重置**）。
// **为什么要回步 ①、而不是「就地清标志、留在步 ③」**（这是本函数存在的关键理由）：
// 补段态下步 ③ 的按钮是「生成补充段」，它的 segT 锚在**屏上那张帧**（frameStamp，E25）。
// 若只清标志而留在步 ③，按钮会变回「⚙️ 生成标定」，而它拿的仍是**镜头挪动之后**那帧的
// 画面与点 ⇒ 会把**主标定**悄悄重建到挪动后的时刻上（静默错档：屏上没有任何提示，
// 落盘的 calibration.json 却已经是挪动后的画面）。回步 ① 是唯一不产生这种静默错档的出口。
function cancelSeg() {
  pendingSeg.value = false;
  step.value = 0;
  frameReady.value = false;
  ElMessage.info("已取消补段，回到第①步");
}

// 删补充段：路径参数 = 行下标 + 2（R11，segDelPath 在 calibkit 里，已 selftest）。
// 回包**只有 3 键**（segments / segListMd / msg，R9）⇒ 只覆盖这 3 个键，绝不整份替换。
async function delSeg(i) {
  try {
    const r = await api.del(`/api/calib/segments/${segDelPath(i)}`);
    st.value = { ...st.value, ...r };
    if (r.msg) ElMessage.success(stripMd(r.msg));
  } catch (e) { ElMessage.error(e.message); }
}

onMounted(async () => {
  store.on(onState);
  try { await store.refresh(); } catch (e) { ElMessage.error(e.message); }
  await refreshCalib();     // GET /api/calib/state 没视频路径也 200 ⇒ 顶栏标签恒可渲染
});
onUnmounted(() => {
  store.off(onState);
  if (frameObjUrl.value) URL.revokeObjectURL(frameObjUrl.value);
});
</script>

<template>
  <div class="calibbay">
    <div class="topbar">
      <el-input v-model="pathInput" style="width: 360px"
                placeholder="D:\videos\2026-09-18.mp4（直接读本机文件，不复制不上传）"
                @change="onPathChange">
        <!-- 图标按**冻结基准**（计划「原型对照」:114 逐字）：`<VideoCamera />`。
             不写本地 import：本页的图标一律由 calib/main.js:16 的
             `for (const [k, c] of Object.entries(Icons)) app.component(k, c)` 全局注册
             （`Folder` 当年也是这么用的）⇒ `VideoCamera` 同一条通路，无未使用 import 可删。 -->
        <template #prefix><el-icon><VideoCamera /></el-icon></template>
      </el-input>
      <el-tag :type="st?.ready ? 'success' : 'info'">{{ st?.ready ? "已标定" : "未标定" }}</el-tag>
      <el-button type="info" plain size="small" @click="showExisting = true">查看已有标定</el-button>
    </div>

    <div class="steps-card">
      <el-steps :active="step" align-center finish-status="success">
        <el-step title="① 选视频抽帧" description="填本机路径" />
        <el-step title="② 标关键点" description="筐口两端 + 球 1~3 次" />
        <el-step title="③ 生成并验证" description="YOLO 检出预览" />
        <el-step title="④ 微调 HSV" description="滤肤色/杂色" />
      </el-steps>

      <div v-if="step === 0" class="panel">
        <FramePicker :st="st" :frame-url="frameObjUrl" :frame-at="frameAt"
                     :frame-ready="frameReady" :frame-stamp="frameStamp"
                     @update:frame-at="frameAt = $event" @extract="doExtract"
                     @frame-loaded="onFrameLoaded" />
      </div>
      <div v-if="step === 1" class="panel">
        <PointMarker :st="st" :frame-url="frameObjUrl" :natural="natural"
                     @click-point="addPoint" @undo="undoPoint" @clear="clearPoints" />
      </div>
      <div v-if="step === 2" class="panel">
        <BuildPanel :st="st" :preview-src="previewSrc" :pending-seg="pendingSeg"
                    :frame-stamp="frameStamp" @build="doBuild" />
      </div>
      <div v-if="step === 3" class="panel">
        <HsvPanel :st="st" :preview-src="previewSrc" :s-min="sMin" :v-min="vMin"
                  @update:s-min="sMin = $event" @update:v-min="vMin = $event"
                  @refine="doRefine" @finish="doFinish" />
      </div>

      <div class="footer-nav">
        <el-button :disabled="step === 0" @click="goPrev">← 上一步</el-button>
        <el-button type="primary" @click="goNext"
                   :disabled="nextDisabled(step, { frameReady, canBuild: st?.canBuild, hasPreview: st?.hasPreview })">
          下一步 →
        </el-button>
      </div>
    </div>

    <div class="steps-card" style="margin-top: 16px">
      <SegmentCard :st="st" :pending-seg="pendingSeg" @add-seg="addSeg"
                   @del-seg="delSeg" @cancel-seg="cancelSeg" />
    </div>

    <!-- E28：弹窗挂模板根（**不**进 `.topbar`——那里是 display:flex，且照 EditBay 四处
         弹窗的惯例：一律在模板尾部、不在内层块里）。不加 append-to-body。 -->
    <ExistingDialog v-model="showExisting" />
  </div>
</template>

<style scoped>
.calibbay { max-width: 1060px; margin: 0 auto; padding: 18px 24px 80px; }
.topbar { display: flex; align-items: center; gap: 10px; margin-bottom: 16px; }
.steps-card { border: 1px solid #3a3a3a; border-radius: 10px; padding: 18px 20px; }
.panel { margin-top: 22px; min-height: 220px; }
.footer-nav { display: flex; justify-content: space-between; margin-top: 18px; }
</style>
