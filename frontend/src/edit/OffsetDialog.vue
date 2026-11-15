<script setup>
// 时刻校准（R7）：**会话级偏移**——不落盘、不改检测结果。规矩是：凡是**屏上
// 显示时刻**的地方都吃同一个 `offset`。判据是**调用点**，不是这段注释里的名单：
//     git grep -n 'dispT(\|tsec(' frontend/src
// 上面这条命令扫出来的每一处 `dispT` / `tsec` 调用都必须传当前 offset；新增时刻
// 显示时照它自查。**别在这里列名单、也别数数字**——一度按名单数过，每次都漏项、
// 改了别处它就过期；判据只有「调用点」这一条路。
//
// 规则还有**第二条腿：不吃 `dispT` 的文案面**——服务端 `payload["msg"]` 里的时刻
// （删除 toast 与裁决 toast）与前端 Ctrl+Z 的撤销 label。**这条腿的 msg/label 面
// 2026-09-20 起闭合**（del/verdict 请求体带 offset（`actions.js` 的 `getOffset` 注入），
// 服务端 `delete_events`/`mark_verdict` 按它单独算 msg 显示值、`t_str`/键仍原始；
// 撤销 label 走 `dispT`）。**有意保留原始键的文案面**（键即存储身份，别「顺手统一」）：
// 404/400 错误路径回显的键、flags/miss 的 msg、墙页 caption（Plan C 冻结口径）。
// ⚠ **调用点判据（上面那条 grep）有盲区**：它只扫 `dispT(`/`tsec(`，**看不见不叫 `.t`
// 的时刻**——`BasisPanel.vue` 的 `row.shooter.releaseT`（渲染成 `X.XXs` 的出手时刻）
// 就是这样一处：它来自 `release_t` 直传、**原始视频轴、不吃 offset**，与同卡走
// `dispT` 的卡头时刻同屏不同源（旧界面 `app.py` 的「出手 {rt:.1f}s 时刻」同款，
// 墙页 caption 同族）。**行为不改**（要吃偏移是产品决定，已登记进计划承接表、
// 归 Plan E 真机验收通看时定）；在此登记是为了别把「第二条腿闭合」读成
// 「屏上时刻已处处同源」。
//
// **集锦切点**不在这个指针里（它不在屏上、也不吃 `dispT`）：`startGen` 把 offset
// 一起交给 /api/gen，由**服务端**在生成那一刻把 `row[1]` 平移，见
// `api_gen._apply_session_rows`。改校准语义时别忘了它。
// ⚠ 旧界面同样是这样：`_reload_ui` 用 `load_events_table(vp, offset)` 把偏移写进
// `rows[1]`，而 `rows[1]` 就是切点 ⇒ 校准**确实**影响成品。本分支一度只在预览
// 里用它，是静默丢值（A-1/A-2）。
//
// ⚠ 历史（**别把这段语义又改回去**）：本弹窗一度是「关闭即复原」——没有
// 「确定」按钮，模态弹窗又必须先关闭才能点「开始生成」，而关闭一律把 offset
// 打回打开时的值 ⇒ 按下生成那一刻 `offset` **恒为 0**，上面这条把 offset 带进
// 请求体的管线因此是 **inert** 的（终审 A-1/A-2 的同类遗留：接得好，却没人
// 能走到）。而**旧界面（Gradio）不是这样**：`_reload_ui` 用
// `load_events_table(vp, offset)` 把偏移烘进 `rows[1]`，偏移就一直留在会话里，
// 直到用户自己改回来 ⇒ 「关闭即复原」是**偏离旧界面**的。用户 2026-09-19
// 裁定改成不复原，这条路今天走通了。
//
// 现在：改数字即生效，**关闭只是关闭**（右上角 × / Esc / 点遮罩 / 底部
// 「关闭」四条路一个语义，都不动 offset）；要复原按「归零」（draft 与 offset
// 一起打回 0）。底部的「取消」因此**删掉**了——它的旧含义就是复原，留着它而
// 行为变成「保留改动」就是在骗人。
import { ref, watch } from "vue";

const props = defineProps({
  modelValue: Boolean,
  offset: { type: Number, default: 0 },
});
const emit = defineEmits(["update:modelValue", "update:offset", "reload"]);

const draft = ref(props.offset);

// 每次打开都把 draft 拉回当前 offset（外部可能改过它，例如「归零」之后）。
// 注意这里**只同步 draft、不留副本**：关闭不再复原，所以不需要「打开时的值」。
watch(() => props.modelValue, (v) => {
  if (!v) return;
  draft.value = props.offset;
});

// 非有限值一律**忽略这次提交、保留上一个有效值**（R-T9-1，与 Task 8 的
// `finiteDur` 同一裁定）。
//
// ⚠ 判据必须**显式挡空串与 null**：`Number("")` 与 `Number(null)` 都是 **0**，
// `Number.isFinite(Number(v))` 会**假过**、把「清空」当成「设成 0」。用户清空
// 输入框的意思是「我不要这个值了」，不是「归零」。
//
// ⚠⚠ 这个输入框**不能**写成 `v-model="draft"`——光加门挡不住：清空时
// `update:modelValue` 那条路径（**不是** `@change`）照样会把 `draft` 置成
// `null`，接着 el-input-number 自己那个 `watch(() => props.modelValue)` 把
// `data.currentValue` 也置成 `null` ⇒ **输入框变空、页面上时刻却还是旧偏移**
// （两处显示不一致，比直接归零更难查）。写成 `:model-value="draft"` 则 `draft`
// 只由下面这个门来改，而 `handleInputChange` 末尾那句
// `setCurrentValueToModelValue()` 会把显示拉回 `draft`（仍是门保住的旧值）。
// （2.14.6 实测同款：`verifyValue("")` 走 `valueOnClear === null` 分支 return null
// ⇒ 清空那一击发的是 `update:modelValue(null)` + `change(null, 旧值)`。）
const setDraft = (v) => {
  if (v === "" || v === null || v === undefined) return;
  const n = Number(v);
  if (!Number.isFinite(n)) return;
  draft.value = n;
  emit("update:offset", n);
};

// 「归零」是**唯一的复原入口**（关闭不再复原，见头注释）：draft 与 offset 一起
// 打回 0。两处都要设——只 emit 的话输入框会停在旧数字上，与页面上的时刻不一致。
const zero = () => {
  draft.value = 0;
  emit("update:offset", 0);
};
</script>

<template>
  <el-dialog class="offdlg" :model-value="modelValue" width="460"
             title="⏱ 进球时刻校准"
             @update:model-value="(v) => emit('update:modelValue', v)">
    <div class="tip">
      检测比实际进球早就填正数（时刻整体后移），晚了填负数。校准作用于
      <b>显示时刻、切片预览与集锦切点</b>：表格/时间轴/预览一起动，成品也按
      校准后的时刻切。<b>关闭弹窗不会清掉校准</b>——要复原请点「归零」。
      检测结果本身不改（events.json 里的原始时刻、勾选与标签的键都不动），
      也不丢任何数据。
    </div>
    <div class="line">
      <el-input-number :model-value="draft" :step="0.1" :precision="1"
                       style="width: 140px" @change="setDraft" />
      <span class="unit">秒</span>
    </div>
    <template #footer>
      <el-button @click="zero">归零</el-button>
      <el-button @click="emit('reload')">↻ 重新载入</el-button>
      <el-button @click="emit('update:modelValue', false)">关闭</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.tip { font-size: 13px; line-height: 1.7; color: #cfd3dc; }
.line { display: flex; align-items: center; gap: 8px; margin-top: 14px; }
.unit { color: #a3a6ad; font-size: 12.5px; }
</style>
