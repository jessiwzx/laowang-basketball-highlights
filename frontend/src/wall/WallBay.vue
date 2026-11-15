<script setup>
// 出手人墙主体（spec 痛点表第 3 行）：左墙大卡片（≥200px）+ 右列按人分组 +
// 底部多选托盘。数据流与剪辑台同口径：墙的全部数据来自 GET /api/wall 的
// 整份回包，前端不算统计、不拼归人文案（服务端 msg 是唯一文案源）。
// 回扫（识别出手人）与剪辑台是同一后端 job 的两处入口：进度只显服务端
// msg，完成后重取墙；刷新页面凭 jobmemory 续跟（同视频才续）。
import { ref, reactive, computed, onMounted } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { store } from "../shared/session.js";
import { api, qs } from "../shared/api.js";
import { orderTags, recordTagUse } from "../shared/tagmru.js";
import { startJob, followJob } from "../shared/jobs.js";
import { createJobMemory } from "../shared/jobmemory.js";
import WallTray from "./WallTray.vue";
import PersonGroups from "./PersonGroups.vue";
import { splitCaption, itemByKey, DROP_UN, DROP_NEW } from "./wallkit.js";

const state = ref(null);        // 会话态（videoPath 等，来自 /api/state）
const wall = ref(null);         // /api/wall 整份回包（Task 3 起真取数）
const sortBy = ref("时间");           // 排序模式（值=服务端中文标签，R1）
const openGroups = ref([]);           // 展开的人名（Task 5 接 el-collapse）
let wallSeq = 0;                      // loadWall 序号：过期回包不许覆盖新请求
const selKeys = ref([]);              // 墙上点选（未归+已归共用；key 全场唯一）
const assignName = ref("");           // 托盘的人名下拉
const guardInfo = ref(null);          // GET /api/wall/guard 的归人前读数（R4）
const dragKey = ref(null);           // 正在拖的球 key（R7：状态变量，不用 dataTransfer）
const dragOver = ref(null);          // 高亮哨兵：null / DROP_UN / DROP_NEW / 组名

const jobMem = createJobMemory();     // 回扫跨刷新续跟（与剪辑台同一个单槽约定）
const scanJob = reactive({ phase: "idle", msg: "" });   // idle | running | error

const hasVideo = computed(() => !!state.value?.videoPath);
const videoName = computed(() => {
  const p = state.value?.videoPath || "";
  return p.split(/[\\/]/).pop() || p;
});
const unassigned = computed(() => wall.value?.unassigned || []);
const assigned = computed(() => wall.value?.assigned || []);
const assignedCount = computed(() =>
  assigned.value.reduce((n, g) => n + g.items.length, 0));

const sorts = computed(() => wall.value?.sorts || []);
// 托盘人名下拉的候选序（2026-10-11 用户裁定）：最近归过的标签自动置顶
// （MRU，见 shared/tagmru.js）。并集内容仍是服务端 _tag_choices 的原样
// （R13：前端不自己 union），这里只重排展示序。
const personChoices = computed(() => orderTags(wall.value?.personChoices || []));
// caption → 显示层（⚠ 角标与 label 的拆解在 wallkit，R2）
const labelOf = (it) => splitCaption(it?.caption).label;
const badOf = (it) => splitCaption(it?.caption).bad;

// —— 数据载入（进页/换排序/回扫完成/写操作回包共用一套 apply）——
// 回包里的 sort 是服务端解析后的标签（未知值已退化「时间」），用它回写
// sortBy——前端不维护第二份解析（R1）。写端点的回包按请求里的 sort 重建，
// 就地替换不会把排序静默打回时间序（test_web_wall.py 钉过的坑）。
async function loadWall(sort) {
  const seq = ++wallSeq;
  try {
    const w = await api.get("/api/wall" + qs({ sort: sort ?? sortBy.value }));
    if (seq !== wallSeq) return;            // 用户已再切排序，过期回包丢弃
    applyWall(w);
  } catch (e) {
    ElMessage.error(e.message);
  }
}
function applyWall(w) {
  // 「新组」= 上一份墙里没有的组（不是「当前没展开的组」——用户手动收起的
  // 组仍在墙上，把它当新组会在每次刷墙时重开，违反 R15）
  const prev = new Set((wall.value?.assigned || []).map((g) => g.person));
  wall.value = w;
  sortBy.value = w.sort;
  // 组的展开态：新组自动展开、用户手动收起的保持、消失的组跟着删（R15）
  const names = w.assigned.map((g) => g.person);
  openGroups.value = openGroups.value.filter((n) => names.includes(n))
    .concat(names.filter((n) => !prev.has(n)));
}
function onSortChange(m) { loadWall(m); }

// —— 点选与托盘（归人/移出的按钮路径；Task 6 拖拽落点直接调 assign/unassign）——
// 托盘 chips：未归球=key、已归球=人名·key——都来自服务端 caption（R12）
function chipLabel(key) {
  const it = itemByKey([unassigned.value, ...assigned.value.map((g) => g.items)], key);
  return it ? labelOf(it) : key;
}

// 点选（左墙卡片与右列缩略图共用一个 toggle；Task 5 起右列也发这个）
function toggle(key) {
  const i = selKeys.value.indexOf(key);
  if (i < 0) selKeys.value.push(key);
  else selKeys.value.splice(i, 1);
}
const selectAll = () => { selKeys.value = unassigned.value.map((x) => x.key); };
const clearSel = () => { selKeys.value = []; };

// msg 是唯一文案源：按行拆，⚠️ 行（护栏）弹 warning、其余弹 success（R4）。
// 警示不阻断——归人服务端已经做了，前端只负责把两段话都给用户看。
function toastMsg(msg) {
  for (const line of String(msg || "").split("\n")) {
    const s = line.trim();
    if (!s) continue;
    if (s.includes("⚠️")) ElMessage.warning(s);
    else ElMessage.success(s);
  }
}
async function assign(person, keys) {
  const p = String(person || "").trim();
  if (!keys.length) { ElMessage.warning("先在墙上点选要归人的球"); return; }
  if (!p) { ElMessage.warning("选或输入一个人名"); return; }
  try {
    const w = await api.post("/api/wall/assign",
                             { person: p, keys, sort: sortBy.value });
    applyWall(w);
    recordTagUse(p);           // 归过即置顶（MRU）：拖拽/托盘/新建人名三条路都走这
    selKeys.value = [];                     // 归人后清点选（旧界面同款）
    toastMsg(w.msg);
    if (assignName.value) loadGuard(assignName.value);   // 组变了，读数刷新
  } catch (e) { ElMessage.error(e.message); }
}
async function unassign(keys) {
  if (!keys.length) return;
  try {
    const w = await api.post("/api/wall/unassign", { keys, sort: sortBy.value });
    applyWall(w);
    selKeys.value = [];
    toastMsg(w.msg);
    if (assignName.value) loadGuard(assignName.value);
  } catch (e) { ElMessage.error(e.message); }
}
const trayAssign = () => assign(assignName.value, selKeys.value);
const trayUnassign = () => unassign(selKeys.value);

// 归人前护栏读数（spec API 表第 5 行）：下拉换人时看该人整组。只读、
// 失败静默置空（R17）——真护栏在服务端 assign 里，读数是辅助信息。
async function loadGuard(person) {
  try { guardInfo.value = await api.get("/api/wall/guard" + qs({ person })); }
  catch { guardInfo.value = null; }
}
function onPersonChange(p) {
  guardInfo.value = null;
  if (p) loadGuard(p);
}

const endDrag = () => { dragKey.value = null; dragOver.value = null; };
// 已归球拖回左墙 = 移出（球本就未归则 no-op，原型同款）
async function dropToUn() {
  const k = dragKey.value;
  endDrag();
  if (!k) return;
  if (unassigned.value.some((x) => x.key === k)) return;
  await unassign([k]);
}
// 落点分发：组名 → 归人；DROP_NEW → 新建人名弹窗（R13）
async function dropTo(zone) {
  const k = dragKey.value;
  endDrag();
  if (!k) return;
  if (zone === DROP_NEW) { await dropNew(k); return; }
  await assign(zone, [k]);
}
// 新建人名：prompt 文案逐字照原型；落点还是 assign——前端不写标签库（R13）
async function dropNew(key) {
  try {
    const { value } = await ElMessageBox.prompt(
      "新人名（入全局标签库，全部视频共用）", "拖拽新建",
      { confirmButtonText: "归人" });
    const name = String(value || "").trim();
    if (!name) return;
    await assign(name, [key]);
  } catch { /* 用户取消 */ }
}
// dragleave 防闪烁：进子元素也冒 dragleave，contains 判真离开（原型同款）
function leaveZone(e) {
  if (!e.currentTarget.contains(e.relatedTarget)) dragOver.value = null;
}

// 回扫：与剪辑台「识别出手人」是同一个后端 job 的两处入口（原型同款）。
// 进度只显示服务端 msg（R5：wall_scan 的 worker 从不报 pct）；完成后的墙刷新
// 是重新 GET /api/wall（R6：result 只平铺 state_payload，墙数据不在里面）。
async function startScan() {
  if (scanJob.phase === "running") return;
  scanJob.phase = "running";
  scanJob.msg = "⏳ 启动回扫…";
  try {
    const jobId = await startJob("/api/wall/scan");     // 400 前置在这里同步抛
    jobMem.remember({ jobId, kind: "wall_scan",
                      videoPath: state.value?.videoPath });
    const res = await followJob(jobId, (j) => {
      if (j.msg) scanJob.msg = j.msg;
    });
    jobMem.clear();
    scanJob.phase = "idle";
    scanJob.msg = "";
    await loadWall();
    ElMessage.success(res?.msg || "✅ 完成");            // 服务端汇总，不自编
  } catch (e) {
    scanJob.phase = "error";
    jobMem.clear();
    ElMessage.error(e.message);
  }
}
// 刷新页面后接着跟（记录的 videoPath 与当前不符则弃——单用户单视频工具）
async function resumeScan() {
  const rec = jobMem.load();
  if (!rec || rec.kind !== "wall_scan") return;
  if (!state.value?.videoPath || rec.videoPath !== state.value.videoPath) {
    jobMem.clear();
    return;
  }
  scanJob.phase = "running";
  scanJob.msg = "⏳ 续跟回扫…";
  try {
    const res = await followJob(rec.jobId, (j) => {
      if (j.msg) scanJob.msg = j.msg;
    });
    ElMessage.success(res?.msg || "✅ 完成");
  } catch (e) {
    ElMessage.error(e.message);
  } finally {
    jobMem.clear();
    scanJob.phase = "idle";
    scanJob.msg = "";
    await loadWall();
  }
}

function onState(s) {
  const prev = state.value?.videoPath;
  state.value = s;
  // 剪辑台载入/换视频后跳过来：墙跟着换（无会话不发，R9）。
  // store.refresh() 会通知监听者，所以首载也走这里——onMounted 不再单独 loadWall。
  if (s?.videoPath && s.videoPath !== prev) loadWall();
}

onMounted(async () => {
  store.on(onState);
  try { await store.refresh(); } catch (e) { ElMessage.error(e.message); }
  await resumeScan();     // 有未完的回扫记录（同视频）则续跟
});
</script>

<template>
  <div class="wallwrap">
    <div class="topbar">
      <el-tag effect="plain">{{ videoName || "未载入视频" }}</el-tag>
      <el-tag type="warning" effect="dark">未归 {{ unassigned.length }}</el-tag>
      <el-tag type="success" effect="dark">已归 {{ assignedCount }}</el-tag>
      <el-button type="primary" size="small"
                 :loading="scanJob.phase === 'running'" @click="startScan">🧍 识别出手人（回扫溯源）</el-button>
      <span v-if="scanJob.phase === 'running'" class="scanmsg">{{ scanJob.msg }}</span>
      <span class="spacer"></span>
      <span class="sortlabel">排序</span>
      <el-radio-group :model-value="sortBy" size="small" @change="onSortChange">
        <el-radio-button v-for="m in sorts" :key="m" :value="m">{{ m }}</el-radio-button>
      </el-radio-group>
    </div>
    <div v-if="!hasVideo" class="stage-hint">
      先去「🎬 剪辑台」载入视频，再回来归人（<a href="/edit.html">去剪辑台</a>）
    </div>
    <div v-else class="main">
      <div class="left colwall" :class="{ droptarget: dragOver === DROP_UN }"
           @dragover.prevent="dragOver = DROP_UN" @dragleave="leaveZone"
           @drop.prevent="dropToUn">
        <div class="wallhead">
          <b>未归人 {{ unassigned.length }} 球</b>
          <span class="wh-note">缩略图 = 出手前后动图 · 点击选中（可多选）· 可整卡拖到右侧人名 · 右侧已归的球也能拖回这里</span>
          <span class="spacer"></span>
          <el-button text size="small" @click="selectAll">全选</el-button>
          <el-button text size="small" @click="clearSel">清空</el-button>
        </div>
        <div class="wallgrid">
          <div v-for="it in unassigned" :key="it.key" class="card"
               :class="{ sel: selKeys.includes(it.key) }" draggable="true"
               @click="toggle(it.key)" @dragstart="dragKey = it.key"
               @dragend="endDrag">
            <span v-if="selKeys.includes(it.key)" class="selmark">✓</span>
            <span v-if="badOf(it)" class="warn">⚠ 定位失效</span>
            <img :src="it.thumb" alt="" />
            <div class="cap"><span>{{ labelOf(it) }}</span><span class="dragtip">拖着走 ↗</span></div>
          </div>
        </div>
      </div>
      <PersonGroups v-model:open="openGroups" :groups="assigned" :sel-keys="selKeys"
                    :drag-over="dragOver" :label-of="labelOf" :bad-of="badOf"
                    @toggle="toggle" @zone="dragOver = $event" @drop-to="dropTo"
                    @drag-start="dragKey = $event" @drag-end="endDrag" />
    </div>
    <div v-if="guardInfo && assignName" class="guardline">
      🛡 {{ assignName }}：已归 {{ guardInfo.n }} 球<template
        v-if="guardInfo.violations.length"> —— {{ guardInfo.msg }}</template>
    </div>
    <WallTray v-model:name="assignName" :sel-keys="selKeys"
              :choices="personChoices" :chip-label="chipLabel"
              @toggle="toggle" @assign="trayAssign" @unassign="trayUnassign"
              @clear="clearSel" @person-change="onPersonChange" />
  </div>
</template>

<style scoped>
.wallwrap { display: flex; flex-direction: column; height: calc(100vh - 40px); }
.topbar { flex: none; display: flex; align-items: center; flex-wrap: wrap;
          gap: 14px; padding: 10px 18px; background: #1a1a1a;
          border-bottom: 1px solid #333; }
.spacer { flex: 1; }
.stage-hint { flex: 1; display: flex; align-items: center; justify-content: center;
              color: #8d9098; font-size: 13.5px; }
.stage-hint a { color: #f48553; }
.main { flex: 1; display: flex; min-height: 0; }
.colwall { overflow-y: auto; padding: 14px; }
.left { width: 56%; border-right: 1px solid #333; background: #181818; }
.right { flex: 1; background: #161616; }
.sortlabel { font-size: 12.5px; color: #a3a6ad; }
.wallhead { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.wallhead b { font-size: 14.5px; }
.wh-note { font-size: 12px; color: #8b8e95; }
.wallgrid { display: grid;
            grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }
.card { position: relative; border: 2px solid #3a3a3a; border-radius: 10px;
        overflow: hidden; background: #222; transition: border-color .1s; }
.card { cursor: pointer; }
.card:hover { border-color: #6b6b6b; }
.card.sel { border-color: var(--el-color-primary);
            box-shadow: 0 0 0 3px rgba(232, 98, 45, .25); }
.selmark { position: absolute; top: 8px; left: 8px; width: 24px; height: 24px;
           border-radius: 50%; background: var(--el-color-primary); color: #fff;
           font-size: 14px; font-weight: 700; display: flex; align-items: center;
           justify-content: center; }
.guardline { flex: none; padding: 6px 18px; font-size: 12.5px; color: #f2c078;
             background: #15202b; border-top: 1px solid #2b4257; }
.card img { display: block; width: 100%; aspect-ratio: 1.28; object-fit: cover;
            background: #000; }
.warn { position: absolute; top: 8px; right: 8px; background: rgba(0, 0, 0, .6);
        border-radius: 6px; padding: 2px 8px; font-size: 11px; color: #f2c078; }
.cap { display: flex; justify-content: space-between; padding: 5px 9px;
       font-size: 12px; color: #cfd3dc; }
.dragtip { color: #6f7278; }
.left.droptarget { outline: 2px dashed var(--el-color-primary); outline-offset: -6px;
                   border-radius: 10px; background: #1d1a17; }
.scanmsg { font-size: 12px; color: #8b8e95; max-width: 420px; overflow: hidden;
           text-overflow: ellipsis; white-space: nowrap; }
</style>
