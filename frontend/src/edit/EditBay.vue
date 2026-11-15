<script setup>
// 剪辑台主体（spec 3）：顶栏 + 左栏（时间轴/表格/页脚）+ 右栏（播放器/
// 判定依据/按钮）+ 四个弹窗。本任务先落地骨架、会话载入与空态。
//
// 数据流：全部来自服务端 state（Plan A 的 state_payload），**前端不算统计**;
// 写操作一律走 actions（撤销栈在里面），回包就地替换整份 state。
import { ref, reactive, computed, watch, nextTick, onMounted, onBeforeUnmount } from "vue";
import { ElMessage } from "element-plus";
import { api } from "../shared/api.js";
import { store } from "../shared/session.js";
import { bindHotkeys } from "../shared/hotkeys.js";
import { stripMd } from "../shared/md.js";
import { orderTags, recordTagUse } from "../shared/tagmru.js";
import { createJobMemory } from "../shared/jobmemory.js";
import { startJob, followJob } from "../shared/jobs.js";
import { createActions } from "./actions.js";
import { TIER } from "./tiers.js";
import EventTable from "./EventTable.vue";
import TimelineStrip from "./TimelineStrip.vue";
import ClipPanel from "./ClipPanel.vue";
import BasisPanel from "./BasisPanel.vue";
import HelpDialog from "./HelpDialog.vue";
import OffsetDialog from "./OffsetDialog.vue";
import BulkDeleteDialog from "./BulkDeleteDialog.vue";
import GenDialog from "./GenDialog.vue";

const state = ref(null);
const pathInput = ref("");

// api/store 用真实现；撤销栈、写操作形状都在 actions.js 里（Task 4）
// getOffset 给撤销 label 与 del/verdict 请求体用（服务端 msg 的显示时刻）。
// `offset`（`const offset = ref(0)`）在下面才声明——闭包惰性求值，动作触发时
// 早已过初始化，安全；
// ⚠ 必须 .value：ref 直传 = Number(ref)=NaN = 静默回原始串（S4）。
const act = createActions({ api, store, getOffset: () => offset.value });

const loaded = computed(() => !!state.value?.loaded);
const hint = computed(() => state.value?.hint || "");
const rows = computed(() => state.value?.rows || []);

// 档位/勾选计数是 rows[] 的派生视图（同一个权威数组）；**反馈计数与已删
// 计数不在这里算**——那两个有独立权威源（state.feedback / state.deleted）。
const cnt = computed(() => {
  const c = { high: 0, review: 0, low: 0, checked: 0, starred: 0 };
  for (const r of rows.value) {
    if (r.status in c) c[r.status] += 1;
    if (r.selection) c.checked += 1;
    if (r.starred) c.starred += 1;      // ★ 精选（只可能 ≤ checked）
  }
  return c;
});
const checkedPct = computed(() =>
  rows.value.length ? Math.round((cnt.value.checked / rows.value.length) * 100) : 0);

const tierFilter = ref("all");
const tagFilter = ref("");
const missInput = ref("");
const curKey = ref("");
const tableRef = ref(null);

const tags = computed(() => state.value?.tags || []);
// 行内标签下拉的候选序（2026-10-11 用户裁定）：最近打过的标签自动置顶
// （MRU，见 shared/tagmru.js）。热键 1-9、帮助清单、筛选下拉仍用库序
// tags——数字键是固定位置映射，跟着 MRU 动会毁手感。
const pickTags = computed(() => orderTags(tags.value));
const verdicts = computed(() => state.value?.verdicts || {});
const deletedCount = computed(() => (state.value?.deleted || []).length);
const duration = computed(() => Number(state.value?.duration) || 0);

// 勾选/档位计数补一个「未勾选」（筛选按钮要显示它）
const cntUnchecked = computed(() =>
  rows.value.filter((r) => !r.selection).length);

const visibleRows = computed(() =>
  rows.value.filter((r) => {
    if (tierFilter.value === "unchecked" && r.selection) return false;
    if (["high", "review", "low"].includes(tierFilter.value)
        && r.status !== tierFilter.value) return false;
    if (tagFilter.value && r.tag !== tagFilter.value) return false;
    return true;
  }));

// 筛选把当前行藏起来 → 连当前行一起清（R22）：el-table 的高亮本来也就没了，
// 不清的话右栏还显示着它，而键盘动作（rowByKey 只看可见行）会静默无反应。
watch(visibleRows, (list) => {
  if (curKey.value && !list.some((r) => r.key === curKey.value)) curKey.value = "";
});

const emptyText = computed(() =>
  rows.value.length ? "当前筛选无匹配的球（换个档位/标签筛选）"
                    : "尚无检测结果——先点「▶ 开始检测」");

// 服务端每次载入/每次写操作都下发这一行汇总（`api_session.py` 的
// `A._table_summary_md`：视频总长 + 共 N 条 + 三档计数）。旧界面整段渲染它，
// 新界面一度**一个地方都没渲染**⇒ 用户看不到「共几条、三档各几条」。
// **直接显示服务端那一份**，不在这里自己再拼一份同样的数字：同一数字两处实现
// 迟早漂移（本计划记过这类账），而且三档计数已经有一处权威（下面 cnt 只喂
// 顶栏的 tag）。剥 markdown 标记的理由见 md.js。
const summaryMd = computed(() => stripMd(state.value?.summaryMd || ""));

// 写操作的**唯一入口**。规矩（这个仓要记住的）：**新增写入口必须走
// `write()`**，否则检测期的锁不管用——`.main.locked` 只关指针事件，键盘那
// 一路全靠这道门。反面教材（已实测复现过）：`undoStep` 直接 `await
// act.undo()`，检测期照样写进服务端、还弹一句「已撤销」的成功文案。
//
// 收**动作**（thunk），不是收已经发出去的 promise：`act.*` 一被调用就立刻发
// 请求（`actions.js` 的 `raw.*`），收 promise 的话门拦不住任何东西——请求
// 早在进函数之前就已经在飞了（`once(p, …)` 收的是**已求值**的 promise）。
//
// 收尾：成功弹**服务端下发**的 msg；失败弹 e.message。
// 后端只在需要提示的端点上追加 msg（漏检/恢复/裁决/误标），PATCH 勾选与
// 打标签不带 msg ⇒ 那两处不弹 toast（裁定 R15：不自己编成功文案）。
async function write(make) {
  // 检测期锁写（R3）：这四条写端点在服务端没有 running 守卫，而检测完成会
  // 重写 events.json——跑着的时候按旧键落盘的勾选/标签，键相同的球会拿到
  // 别人的标记（静默错数据）。旧界面就是检测期间禁整张表，这里同口径。
  if (detecting.value) {
    ElMessage.warning("⏳ 检测中——表格已锁，跑完自动载入结果；"
      + "刷新/关闭页面不影响检测（重开会接上进度）");
    return null;
  }
  try {
    const s = await make();
    // 服务端 msg 是 markdown（裁决/漏检那些把 `_fp_summary` 整段带进来，
    // 里面是 `**0**` 与 `` `feedback.json` ``）；旧界面用 gr.Markdown 渲染它。
    // 显示层剥标记，**一个字都不改**文案本身（区分见 md.js 的注释）。
    if (s && s.msg) ElMessage.success(stripMd(s.msg));
    return s;
  } catch (e) {
    ElMessage.error(e.message);
    return null;
  }
}

const onMiss = () => write(() => act.recordMiss(missInput.value)).then((s) => {
  if (s) missInput.value = "";       // 只在真记上时清空输入框
});
const onPick = (key) => { curKey.value = key; };

// —— 右栏（Task 8）：切片播放器 + 判定依据卡 ——
const offset = ref(0);                    // 时刻校准偏移（Task 9 的弹窗驱动，R7）
const clipRef = ref(null);
const durAll = ref(null);                 // 「应用到全部」的会话级前后秒（R21）
const durMap = ref({});                   // 单行改动：key → {before, after}

const feedback = computed(() => state.value?.feedback || {});
const thumbFlags = computed(() => state.value?.flags?.thumb || []);
const crossFlags = computed(() => state.value?.flags?.cross || []);

const curRow = computed(() => rows.value.find((r) => r.key === curKey.value) || null);
// 行的前后秒：单行改动 > 应用到全部 > 服务端给的 row.before/after。
// ⚠ `durMap[row.key]` 是**对象真值**判定——一旦不小心存进 `{before: null, after: 2}`，
// `||` 兜不住它（对象恒 truthy），坏值会一路流进切片 URL 成 `before=null` ⇒
// `/media/clip` 被 FastAPI 的参数校验拦成 **400**、预览全坏（修复轮 1 的 F-1）。
// 所以真正承重的是下面那道 `finiteDur` 门：**非有限值根本不进这张表**。
// 不在这里补「兜底分支」——那只会让坏值进来之后假装没事。
const durOf = (row) => durMap.value[row.key] || durAll.value
  || { before: row.before, after: row.after };
// `Number.isFinite` 不是全局 `isFinite`：它**不做隐式转换** ⇒ 空串、null、NaN
// 甚至数字串 "6" 全都为 false。这里要的正是这个严格语义。
//
// 清空输入框时 el-input-number 交给 `@change` 的是 `null`（`valueOnClear` 默认
// null，见 element-plus 的 `verifyValue`：`value === ""` ⇒ 返回 null），不是 0、
// 也不是空串。裁定（修复轮 1 F-1）：**非有限值一律忽略这次提交、保留原值**，
// 并且**不退回默认 5/2**——会话级前后秒经「应用到全部」作用于**所有行**，
// 静默跳回默认会毁掉用户特意设过的值。
// 界面不用我们回填：el-input-number 的 `handleInputChange` 在 change 之后会
// `setCurrentValueToModelValue()`，把显示拉回**仍然没被改动**的 model-value
// （= 原值）⇒ 输入框自己就回到上一个有效值。
const finiteDur = (v) => Number.isFinite(v);
const setDur = ({ before, after }) => {
  if (!curKey.value || !finiteDur(before) || !finiteDur(after)) return;   // 坏提交丢掉
  durMap.value = { ...durMap.value, [curKey.value]: { before, after } };
};
const applyAll = ({ before, after }) => {
  if (!finiteDur(before) || !finiteDur(after)) return;   // 同上：坏值不扩散到全部行
  durAll.value = { before, after };
  durMap.value = {};                      // 全局值优先，旧的单行值全部作废
  ElMessage.success(`已应用到全部：前 ${before}s / 后 ${after}s`);
};

// —— 四个弹窗（Task 9）——
// 弹窗状态在 Task 7 已定型（dlg + modalOpen 闸门），这里只塞组件与开合入口。
// 画幅与输出方式（2026-10-21）**不再由顶栏按钮/父组件持有**：两个集锦入口
// 已合并成一个「生成」，两维选择都在 GenDialog 内部（且不设默认），父组件
// 只在 start 事件里拿到结果。所以这里没有 genVertical。
const genJob = reactive({ phase: "idle", msg: "", pct: 0, result: null });
const detectJob = reactive({ phase: "idle", msg: "", pct: 0, result: null });
const wallJob = reactive({ phase: "idle", msg: "", pct: 0, result: null });
const jobMem = createJobMemory();
// 键 = 后端的 kind（jobmemory 记的就是它，续接时按它找槽位）
const JOBS = { gen: genJob, detect: detectJob, wall_scan: wallJob };

// 长任务统一驱动（三个任务共用）。记住 jobId 是给「刷新页面接着看进度」用的：
// 后端 job 只在内存里，重开页面用 followJob 接上，而不是重新起一个（同类
// 任务重起会 409）。
async function longJob(kind, startUrl, body, slot) {
  slot.phase = "running";
  slot.msg = "⏳ 正在启动…"; slot.pct = 0; slot.result = null;
  try {
    const jobId = await startJob(startUrl, body);
    // 连视频一起记：续接时要确认这个 job 是在**当前这个视频**上跑的
    jobMem.remember({ jobId, kind, videoPath: state.value?.videoPath || "" });
    const res = await followJob(jobId, (j) => {
      slot.msg = j.msg || ""; slot.pct = Number(j.pct) || 0;
    });
    slot.result = res; slot.phase = "done";
    return res;
  } catch (e) {
    // 失败一律弹服务端文案（R15）：检测/回扫没有弹窗，toast 是唯一出路；
    // 生成弹窗的错误态因此不重复显示正文（同一句话弹两处反而像两个错）。
    slot.phase = "error"; slot.msg = e.message;
    ElMessage.error(e.message);
    return null;
  } finally {
    jobMem.clear();                      // 终态（含失败）就不再续接
  }
}

// 刷新后续接（三类长任务都走这里——`JOBS` 三槽落地后 detect/wall_scan 也会命中，
// 不再是「本任务只有 gen」）。
// 视频对不上就不接（R3）：job 是按视频跑的，接了别人的进度等于把锁挂错地方。
// ⚠ 续接跑完**必须把结果喂回界面**（下面 detect/wall_scan 那一支）：只写
// `slot.result` 的话，用户重开页面接上进度、跑到终态，看到的仍是检测**前**的
// 旧表——没有任何提示、也没有任何一处消费 `detectJob.result`。
async function resumeJobs() {
  const rec = jobMem.load();
  if (!rec) return;
  const slot = JOBS[rec.kind];
  const cur = state.value?.videoPath;
  if (!slot || (rec.videoPath && cur && rec.videoPath !== cur)) {
    jobMem.clear(); return;
  }
  slot.phase = "running"; slot.msg = "⏳ 接上进度…"; slot.pct = 0;
  try {
    const res = await followJob(rec.jobId, (j) => {
      slot.msg = j.msg || ""; slot.pct = Number(j.pct) || 0;
    });
    slot.result = res; slot.phase = "done";
    // 检测/回扫的结果是**平铺的 state_payload**（与 startDetect/startScan 同：
    // 后端 `api_gen.py:212` 只对这两个 kind 做 `payload.update(state_payload())`）
    // ⇒ 整份喂 onState，表格/时间轴/页脚/墙一起刷新。
    // ⚠ 不能无条件喂：gen 的 result 是 `{summaryMd, videos, firstUrl}`，不是
    // state_payload，灌进去会把整页 state 换成没有 `rows` 的对象。
    // 完成提示照各自那条路的样子（detect 有 cancelled 分支、wall_scan 没有——
    // 别顺手合并成一份「统一形态」，那是契约字段不是风格）。
    if (rec.kind === "detect" || rec.kind === "wall_scan") {
      onState(res);
      if (rec.kind === "detect" && res.cancelled) ElMessage.warning(res.msg);
      else ElMessage.success(res.msg || "✅ 完成");
    }
  } catch (e) {
    // 404 = 服务重启过（job 只在内存里），文案是服务端给的
    slot.phase = "error"; slot.msg = e.message;
    ElMessage.error(e.message);
  } finally {
    jobMem.clear();
  }
}

// 生成入口（2026-10-21 合并）：顶栏只有一个「生成」按钮，画幅与输出方式
// 在弹窗里选（两项都必选、无默认）。原来这里是 openGen(vertical) —— 画幅
// 由"按哪个按钮"决定，现在入口不再表达画幅。
const openGen = () => { dlg.gen = true; };

// 会话级行状态跟着生成请求一起走（A-1/A-2）。**不带上这三个值就是静默丢值**：
// 服务端 `api_gen.gen()` 自己 `load_events_table(vp)` 重算行 ⇒ 前后秒恒为常量
// 默认 5.0/2.0、切点恒为未校准的检测时刻，而 `gen_selected` 真的拿 row[4]/
// row[5]/row[1] 去切（`bbh/app.py:1992`）⇒ 预览按你设的 8/3 切、成品按 5/2 切，
// 校准后预览移了、成品没移，且没有任何提示。旧界面靠 `rows_state` 带过去的就是
// 这三样（`apply_all_rows` 写回 r[4]/r[5] + `load_events_table(vp, offset)` 平移
// rows[1]）。
// 形状与 `durOf` 的三段优先级**逐条对齐**：单行 `durs[行键]` > 全局 before/after
// > 服务端原值（不传 = 服务端行里那份，所以这里给 null 而不是 5/2）。
// `durMap` 的键就是 `state.rows[].key`（= 原始时刻串），与后端要的行键同源；
// 后端的行键读取发生在偏移平移**之前**，正好对齐。
const sessionDurs = () => {
  const out = { offset: offset.value || 0, before: null, after: null, durs: null };
  if (durAll.value) { out.before = durAll.value.before; out.after = durAll.value.after; }
  const m = {};
  for (const [k, v] of Object.entries(durMap.value)) m[k] = [v.before, v.after];
  if (Object.keys(m).length) out.durs = m;
  return out;
};
// output：reel=一条集锦（旧行为）/ single=逐球各出一条（2026-10-21）。
// 服务端 `GenIn.output` 是 Literal，拼错会在起 job 之前被拦。
const startGen = ({ vertical, output, items }) =>
  longJob("gen", "/api/gen", { vertical, output, items, ...sessionDurs() },
          genJob);
const bulkDelete = (keys) => write(() => act.deleteKeys(keys));
const reloadState = () => store.refresh().catch((e) => ElMessage.error(e.message));

const anyJobRunning = computed(() =>
  Object.values(JOBS).some((j) => j.phase === "running"));
const detecting = computed(() => detectJob.phase === "running");
// 状态带只显示正在跑的那个：msg 逐帧带着进度，塞进按钮会与按钮自身的
// 百分比重复（R31）
const jobMsg = computed(() => {
  const j = Object.values(JOBS).find((s) => s.phase === "running");
  return j ? j.msg : "";
});

// detect / wall_scan 的结果是**平铺的 state_payload**（契约 §1：kind 属于
// ("detect","wall_scan") 时 payload.update(state_payload())），所以直接
// 整份喂给 onState —— 表格/时间轴/页脚/墙一起刷新。
const startDetect = async () => {
  const res = await longJob("detect", "/api/detect", undefined, detectJob);
  if (!res) return;
  onState(res);
  if (res.cancelled) ElMessage.warning(res.msg);
  else ElMessage.success(res.msg || "✅ 完成");
};
const startScan = async () => {
  const res = await longJob("wall_scan", "/api/wall/scan", undefined, wallJob);
  if (!res) return;
  onState(res);
  ElMessage.success(res.msg || "✅ 完成");
};

// —— 键盘流（spec 3 的复核闭环：左手键盘、右手不动鼠标）——
// 前置闸见 **Task 7** 定型的四条闸（本任务不改它们）。K/J/L 与 ←/→（播放器）
// 已随 Task 8 加在下面同一份 keyMap 里；`?`（帮助）在 Task 9——它的键位与这里
// 同源，落地时也是往这份 map 里加。
const dlg = reactive({ gen: false, offset: false, bulk: false, help: false });
const modalOpen = () => dlg.gen || dlg.offset || dlg.bulk || dlg.help;

const rowByKey = (key) => visibleRows.value.find((r) => r.key === key) || null;

// 键盘选中一行的唯一入口：setCurrentRow 会触发 current-change → onPick
function pick(key) {
  curKey.value = key;
  tableRef.value?.setCurrentRow(key);
  // 当前行必须跟着滚进可视区（帮助文案对复核长列表的承诺，e2e 在 420px 视口下
  // 钉住了它）：el-table 的 `setCurrentRow` 只改 store、**不滚动**——实测连按
  // 7 次 ↓ 后第 8 行整行落在 `.el-table__body-wrapper` 可视带之外。
  // 等一帧再找 `.current-row`：那个类是 Vue 重渲染后才挂上的，同一个 tick 里
  // 查到的还是**上一行**。`block: "nearest"` 只把出界的行挪进来，可见的行不动。
  nextTick(() => {
    document.querySelector("#app .el-table__row.current-row")
      ?.scrollIntoView({ block: "nearest" });
  });
}

// ↑/↓：没有当前行时两个方向都落到第一行（原型行为，不是落到最后一行）
function move(delta) {
  const keys = visibleRows.value.map((r) => r.key);
  if (!keys.length) return;
  const i = keys.indexOf(curKey.value);
  const j = i < 0 ? 0 : Math.min(keys.length - 1, Math.max(0, i + delta));
  pick(keys[j]);
}

// 勾上之后自动跳到下一颗「未勾选且未被否决」的球；找不到就从头绕一圈
function jumpNext(fromKey) {
  const list = visibleRows.value;
  const i = list.findIndex((r) => r.key === fromKey);
  const usable = (r) => !r.selection && verdicts.value[r.key] !== "not_goal";
  for (let k = i + 1; k < list.length; k++) if (usable(list[k])) return pick(list[k].key);
  for (let k = 0; k < i; k++) if (usable(list[k])) return pick(list[k].key);
}

// 空格 = 勾选「进片」＝确认这是进球（勾上后跳下一颗，长列表不用回鼠标）
function confirmRow() {
  const row = rowByKey(curKey.value);
  if (!row) return;
  const key = row.key;
  const on = !row.selection;
  write(() => act.setSelection(key, on)).then((s) => {
    if (s && on) setTimeout(() => jumpNext(key), 120);   // 等服务端回包渲染完再跳
  });
}

// S = 切换当前行的 ★ 精选（2026-10-23）：挑精彩球的键盘流——↑↓ 走行、S 打星，
// 不用回鼠标点表格最后一列。打星会**自动勾上进片**（服务端不变式），所以
// 挑片时不需要先按空格。**不跳下一行**：精选是"顺手打标"，连打相邻几颗很常见，
// 自动跳会跳过刚看的那颗。
function toggleStar() {
  const row = rowByKey(curKey.value);
  if (!row) return;
  write(() => act.setStar(row.key, !row.starred));
}

// X = ❌ 误判反馈（同一颗球再按一次 = 取消；裁决联动取消进片，见 actions.markVerdict）
function markMiss() {
  const row = rowByKey(curKey.value);
  if (!row) return;
  const key = row.key;
  write(() => act.markVerdict(key, "not_goal")).then((s) => {
    if (s) setTimeout(() => jumpNext(key), 120);
  });
}

// 1–9 = 打第 n 个标签（打标会顺带勾选该行——后端 set_row_tag 的行为，见 actions.setTag）
function tagByIndex(n) {
  const row = rowByKey(curKey.value);
  const tag = tags.value[n - 1];
  if (row && tag) write(() => act.setTag(row.key, tag));
}

// D = 删除此球（**直接删，不弹二次确认**；非破坏——撤销栈含删除，另有「⋯ 恢复
// 已删」整套找回）。2026-09-25 用户裁定去掉确认框：与「批量删除」那颗弹窗同一条
// 理由——删除只往 deleted.json 记一条清单、原数据一个字节不动，随时可 Ctrl+Z
// 撤销或整套找回，拦一次手滑的收益不值得每次删都多按一下。
// 连带 `dispT` / `ElMessageBox` 两个 import 也删了（本文件再无用处）。
async function deleteCurrent() {
  const row = rowByKey(curKey.value);
  if (!row) return;
  const s = await write(() => act.deleteKeys([row.key]));
  if (s) curKey.value = "";                    // 行没了，右栏回空态
}

// Ctrl+Z = 撤销上一步（栈空只提示，不报错）。
// ⚠ 撤销**也要走 `write()`**：`act.undo()` → `replay(p)` → `raw.*` 一被调用就
// 立刻发请求，它和空格/1–9/D 是同一类写入口。不走门的话 `.main.locked` 的
// pointer-events 对键盘无效（两道键盘闸此刻也都不成立）⇒ 检测期按 Ctrl+Z
// 会真写服务端，还弹一句「已撤销」的成功文案（blocker A，复审实测复现）。
// 不另写一份闸：另写就是又一处「两段事实各自漂移」。
// `act.undo()` 回的是 `{label, state}`——**没有 `msg`** ⇒ `write()` 不弹成功
// 文案，这里那句「已撤销：…」仍然是唯一的一条（不会双弹）。栈空那句要在
// `write()` **之前**：栈空不是「被拦」，它不该弹锁表警告。
async function undoStep() {
  if (!act.depth) { ElMessage.info("没有可撤销的操作"); return; }
  const r = await write(() => act.undo());
  if (r) ElMessage.success(`已撤销：${r.label}`);
}

// 键名照 shared/hotkeys.js 的归一：字母小写、方向键 up/down、空格 space
const keyMap = { up: () => move(-1), down: () => move(1), space: confirmRow,
                 s: toggleStar,
                 x: markMiss, d: deleteCurrent, "ctrl+z": undoStep };
for (let n = 1; n <= 9; n++) keyMap[String(n)] = () => tagByIndex(n);

// 播放器键（Task 8）：K 播放/暂停、J/L 与 ←/→ 后退/前进 2s。
// 加进**上面这份** map、不另开一份：一个 keydown 监听、一套闸门（弹窗打开
// 与输入框聚焦都不触发）——新开一对 hook 会让两条闸门各自漂移。
keyMap.k = () => clipRef.value?.togglePlay();
keyMap.j = () => clipRef.value?.seekBy(-2);
keyMap.l = () => clipRef.value?.seekBy(2);
keyMap.left = () => clipRef.value?.seekBy(-2);
keyMap.right = () => clipRef.value?.seekBy(2);

// 闸②/闸③（proto §5）：别的弹窗开着 → 所有键整体失效；**帮助窗**开着时只有
// `?` 还能用（其余键各自 early-return）——所以不能把帮助也塞进 disabled，
// 那样 `?` 会连自己都关不掉。`?` 本身是开关。
keyMap["?"] = () => { dlg.help = !dlg.help; };
const gated = {};
for (const [k, fn] of Object.entries(keyMap)) {
  gated[k] = k === "?" ? fn : (e) => { if (dlg.help) return; fn(e); };
}

let unbindHotkeys = null;

// 标签列开了 allow-create：库里没有的新名字**先入库、再打标**。后端
// `set_row_tag` 只认已入库的标签（`app.py:1201` 的 docstring：「标签库维护走
// 新增标签弹窗」）——旧界面那颗「新增标签」弹窗干的就是入库这一件事；新界面
// 照原型把入口收进标签列，入库就得在这里补上，否则 `POST /api/tags` 是够不着
// 的端点、标签库永远长不了（筛选下拉与帮助里的 1–9 清单也永远不变）。
// 顺序固定「先入库后打标」：最后落进 state 的必须是带该标签的那份回包。
async function onTag(key, tag) {
  if (tag && !tags.value.includes(tag)) await act.addTag(tag);
  const st = await act.setTag(key, tag);
  if (tag) recordTagUse(tag);   // 打过即置顶（MRU）：setTag 抛错则不记账
  return st;
}

function onState(s) {
  state.value = s;
  // 只在输入框还空着时回填：用户正在改路径时别被别的请求顶掉
  if (!pathInput.value) pathInput.value = s.videoPath || s.lastVideo || "";
  // ⚠ 这里**不需要**重新指当前行：写操作虽然用服务端回包整份替换 state、
  // 行对象全部重建，但 `EventTable` 设了 `:row-key`，element-plus 的
  // `useCurrent.updateCurrentRowData()` 在 setData 时就会 `setCurrentRowByKey`
  // 按 key 把当前行重新解析出来（`element-plus/es/components/table/src/store/
  // current.mjs`）⇒ 高亮自动跟着走。curKey 只是页面侧「当前行是谁」的权威
  // （右栏与 Task 7 的键盘流要读它），不是拿来驱动 el-table 的。
}

// 路径输入框：@change = 回车或失焦（el-input 的语义）。
// 不用 watch 逐字触发——那会每敲一个字符发一次 POST /api/session，
// 并把 ui_prefs.json 的 lastVideo 写成半截路径。
async function onPathChange() {
  const p = pathInput.value.trim();
  if (!p || p === state.value?.videoPath) return;
  try {
    await act.openVideo(p);
    // 回填服务端清洗后的路径（store.set 同步过监听器，故 state 已是新回包）：
    // 输入框显示的就是真载入的那个路径，下次回车比较的是「清洗后 vs 清洗后」，
    // 不会因为原文多了引号/空白而重复发请求。
    pathInput.value = state.value?.videoPath || p;
  } catch (e) { ElMessage.error(e.message); }
}

// 「打开数据目录」：在资源管理器里打开**工作区根**（开发时是仓库根，打包版
// 是紧挨 exe 的 data\ ），`work/` 与成品都在那下面。双击进 exe 的用户根本
// 不知道文件落在哪，这一下就是让他自己去看。
// 后端该端点**不收任何参数**，打开的恒是工作区根（路径由前端说了算就等于
// 把本机任意目录交给浏览器）；失败按通用契约弹 e.message。
// 成功文案优先用服务端下发的 msg（与 write() 同一条裁定 R15：不自己编成功
// 文案），服务端没给才兜一句——这个按钮的全部反馈就是这个 toast，静默失败
// 比文案不统一糟得多。
async function openDataDir() {
  try {
    const s = await api.post("/api/session/open-data-dir");
    ElMessage.success((s && s.msg) || "已打开");
  } catch (e) { ElMessage.error(e.message); }
}

onMounted(async () => {
  store.on(onState);
  unbindHotkeys = bindHotkeys(gated, { disabled: () => modalOpen() && !dlg.help });
  try { await store.refresh(); } catch (e) { ElMessage.error(e.message); }
  resumeJobs();     // 刷新后接着跟随在跑的长任务（没有记录就是空转）
});
onBeforeUnmount(() => {
  store.off(onState);
  if (unbindHotkeys) unbindHotkeys();
});
</script>

<template>
  <div class="editbay">
    <div class="topbar">
      <el-input v-model="pathInput" style="width: 360px"
                :disabled="detecting"
                placeholder="D:\videos\2026-09-11_3.mp4 —— 填路径自动载入已有结果"
                @change="onPathChange">
        <template #prefix><el-icon><Folder /></el-icon></template>
      </el-input>
      <el-button :type="detecting ? 'warning' : 'primary'"
                 :loading="detecting" :disabled="anyJobRunning"
                 :title="detecting
                   ? '检测进行中——跑完自动载入结果（刷新/关闭页面不影响它，重开自动接上进度）'
                   : '开始检测（几分钟；中途退出可续跑）'"
                 @click="startDetect">
        {{ detecting ? `⏳ 检测中 ${detectJob.pct}%` : "▶ 开始检测" }}
      </el-button>
      <el-button plain type="primary" :loading="wallJob.phase === 'running'"
                 :disabled="anyJobRunning"
                 title="逐球回扫出手人 + 生成缩略图动图（已缓存的球自动跳过、已删球不回扫）"
                 @click="startScan">
        🧍 识别出手人
      </el-button>
      <!-- 生成入口（2026-10-21 合并）：横/竖两个按钮并成一个。画幅与输出
           方式都在弹窗里选，且**都不预选** —— 代价是老路径多两步，换来的是
           "手快点错画幅"这种事故不会静默发生。 -->
      <el-button type="primary" :disabled="anyJobRunning"
                 title="生成视频：弹窗里选「画幅（横/竖）」×「输出（一条集锦 / 逐球各出一条）」"
                 @click="openGen()">🎬 生成</el-button>
      <!-- 三档计数 + 已勾选（照原型 前端原型/剪辑台.html:126-130 的形状）：
           `type` 一律取自 tiers.js —— 表格 el-tag 与时间轴 dot 已经是它的
           消费者，顶栏再写一份色值就是同一档位三处各自漂移 -->
      <el-tag size="small" :type="TIER.high.type" effect="dark">🟢 {{ cnt.high }}</el-tag>
      <el-tag size="small" :type="TIER.review.type" effect="dark">🟡 {{ cnt.review }}</el-tag>
      <el-tag size="small" :type="TIER.low.type" effect="dark">⚪ {{ cnt.low }}</el-tag>
      <el-tag size="small" effect="plain">已勾选 {{ checkedPct }}%</el-tag>
      <!-- ★ 精选计数（2026-10-23）：单条生成只出打了星的球，所以这个数字
           就是"这次能出几条"。打星在表格最后一列、或选中行按 S。 -->
      <el-tag size="small" effect="plain" :type="cnt.starred ? 'warning' : 'info'"
              title="★ 精选：单条生成只出打了星的球（表格最后一列点星，或选中行按 S；打星会自动勾上进片）">
        ★ {{ cnt.starred }}
      </el-tag>
      <!-- 已校准指示器（本批 ②）：旧界面那个偏移是一个**常驻可见的数字框**，
           而校准弹窗是模态的、关了就没了 ⇒ 只把值留在 `offset` 里的话，用户
           再也看不见自己设过什么，那就是静默丢值。⏱ 只在 offset ≠ 0 时出现
           （`toFixed(1)` 与弹窗输入框的 precision=1 一致，负数照实带符号），
           点它把弹窗叫回来改/归零。
           ⚠ 别顺手重排顶栏：四枚 tag 与生成按钮组的先后/右对齐是既有已 park
           项（计划「交付后已知项」C 档）。 -->
      <el-tag v-if="offset !== 0" class="offind" size="small" effect="plain"
              title="点击调整或归零" @click="dlg.offset = true"
              >⏱ 已校准 {{ offset > 0 ? "+" : "" }}{{ offset.toFixed(1) }}s</el-tag>
      <span class="spacer" />
      <el-button text size="small"
                 title="在资源管理器里打开工作区目录——work/ 与生成的成品都在那里"
                 @click="openDataDir">📂 打开数据目录</el-button>
      <el-button text size="small" title="快捷键帮助（按 ? 呼出）"
                 @click="dlg.help = true">⌨ 帮助</el-button>
    </div>

    <div v-if="jobMsg" class="jobmsg">{{ jobMsg }}</div>

    <div class="main" :class="{ locked: detecting }">
      <div class="left">
        <div class="toolbar">
          <el-radio-group v-model="tierFilter" size="small">
            <el-radio-button value="all">全部</el-radio-button>
            <el-radio-button value="unchecked">未勾选 {{ cntUnchecked }}</el-radio-button>
            <el-radio-button value="high">🟢</el-radio-button>
            <el-radio-button value="review">🟡</el-radio-button>
            <el-radio-button value="low">⚪</el-radio-button>
          </el-radio-group>
          <el-select v-model="tagFilter" size="small" clearable filterable
                     placeholder="按标签筛" style="width: 110px">
            <el-option v-for="t in tags" :key="t" :label="t" :value="t" />
          </el-select>
          <el-input v-model="missInput" size="small" style="width: 104px"
                    placeholder="漏检 m:ss" @keyup.enter="onMiss">
            <template #append><el-button @click="onMiss">➕</el-button></template>
          </el-input>
          <el-dropdown>
            <el-button size="small">⋯</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item @click="dlg.offset = true">⏱ 时刻校准</el-dropdown-item>
                <el-dropdown-item @click="write(() => act.restoreAll())">
                  ↩ 恢复已删（{{ deletedCount }}）
                </el-dropdown-item>
                <el-dropdown-item @click="dlg.bulk = true">🗑 批量删除…</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>

        <TimelineStrip v-if="visibleRows.length" :rows="visibleRows"
                       :duration="duration" :verdicts="verdicts"
                       :offset="offset"
                       @pick="pick" />

        <div class="tablewrap">
          <EventTable ref="tableRef" :rows="visibleRows" :tags="pickTags"
                      :verdicts="verdicts" :empty-text="emptyText"
                      :offset="offset"
                      @pick="onPick"
                      @check="(k, v) => write(() => act.setSelection(k, v))"
                      @star="(k, v) => write(() => act.setStar(k, v))"
                      @tag="(k, v) => write(() => onTag(k, v))" />
        </div>

        <div class="tblfoot">
          <span>显示 {{ visibleRows.length }} / 未删 {{ rows.length }} 条（另有 {{ deletedCount }} 条已删可恢复）</span>
          <span>勾选 {{ cnt.checked }} 条进集锦 · 未勾选 {{ cntUnchecked }} 条待复核 · 空格复核一遍就完事</span>
          <!-- 服务端下发的汇总行（A-4）：整行独占一条，不挤上面两句 -->
          <span v-if="summaryMd" class="sum">{{ summaryMd }}</span>
        </div>
      </div>

      <div class="right">
        <div v-if="!loaded" class="stage-hint">{{ hint }}</div>
        <div v-else-if="!curRow" class="stage-hint">← 点击左侧表格任意一行开始复核</div>
        <template v-else>
          <ClipPanel ref="clipRef" :row="curRow" :video-path="state.videoPath"
                     :offset="offset" :before="durOf(curRow).before"
                     :after="durOf(curRow).after"
                     @dur="setDur" @apply-all="applyAll" />
          <BasisPanel :row="curRow" :offset="offset"
                      :verdict="verdicts[curRow.key] || ''"
                      :thumb-flag="thumbFlags.includes(curRow.key)"
                      :cross-flag="crossFlags.includes(curRow.key)"
                      :feedback="feedback"
                      :flags="state.flags"
                      @verdict="(k) => write(() => act.markVerdict(curRow.key, k))"
                      @flag="(f) => write(() => act.toggleFlag(curRow.key, f))"
                      @delete="deleteCurrent" />
        </template>
      </div>
    </div>

    <HelpDialog v-model="dlg.help" :tags="tags" />
    <OffsetDialog v-model="dlg.offset" v-model:offset="offset" @reload="reloadState" />
    <BulkDeleteDialog v-model="dlg.bulk" :rows="rows" :offset="offset"
                      @confirm="bulkDelete" />
    <GenDialog v-model="dlg.gen" :job="genJob"
               @start="startGen" @reset="genJob.phase = 'idle'" />
  </div>
</template>

<style scoped>
/* 尺寸与配色逐条照原型档 前端原型/剪辑台.html（深色为默认） */
.editbay { display: flex; flex-direction: column; height: calc(100vh - 40px); }
.topbar { flex: none; display: flex; align-items: center; gap: 10px;
          padding: 10px 18px; background: #1a1a1a; border-bottom: 1px solid #333; }
.spacer { flex: 1; }
.main { flex: 1; display: flex; min-height: 0; }
.left { width: 44%; min-width: 480px; display: flex; flex-direction: column;
        min-height: 0; border-right: 1px solid #333; background: #181818; }
.right { flex: 1; min-width: 0; overflow-y: auto; background: #161616; }
.stage-hint { padding: 14px 16px; color: #8d9098; font-size: 13px; }
.toolbar { flex: none; display: flex; flex-wrap: wrap; gap: 8px;
           padding: 10px 12px; }
.tablewrap { flex: 1; min-height: 0; overflow: hidden; padding: 0 10px 6px; }
.tblfoot { flex: none; display: flex; justify-content: space-between;
           flex-wrap: wrap; gap: 2px 12px;
           padding: 6px 14px; color: #7c7f86; font-size: 12px; }
/* 汇总行独占一行（flex-basis 100%）：它比上面两句长，混在同一行会把
   「显示 N / 未删 N」挤成两截 —— 也顺手防窄视口下的横向溢出 */
.tblfoot .sum { flex: 1 1 100%; color: #6f7278; }
.jobmsg { flex: none; padding: 6px 18px; background: #221a12;
          border-bottom: 1px solid #3a2a1a; color: #f0b37e; font-size: 12.5px; }
/* 检测期锁写（R3）：指针事件整块关掉 —— 让「锁」看得见，而不是点了没反应 */
.main.locked .left, .main.locked .right { pointer-events: none; opacity: .55; }
</style>
