// 剪辑台页级 e2e：真服务 + 真夹具 + 真产物 + 真浏览器。
// 由 tests/e2e/test_edit_bay_e2e.py 启动——服务跑在临时目录里（cwd=tmp），
// 所以这里的每一次勾选/打标/裁决/删除都只写临时 work/，用户的 work/ 与
// 仓库根的 tags.json / ui_prefs.json 一个字节都不碰。
//
// 环境：BBH_BASE（服务）、BBH_VIDEO（夹具视频路径）、BBH_PLAYWRIGHT
// （playwright 的绝对 file:// URL——它装在 frontend/node_modules，而 ESM
// 从本文件位置向上解析、不认 NODE_PATH，故由 launcher 传入）
const { chromium } = await import(process.env.BBH_PLAYWRIGHT || "playwright");

const BASE = process.env.BBH_BASE;
const VIDEO = process.env.BBH_VIDEO;
if (!BASE || !VIDEO) throw new Error("需要 BBH_BASE 与 BBH_VIDEO");

let pass = 0;
const ok = (cond, msg) => {
  if (!cond) throw new Error(`❌ ${msg}`);
  pass++;
};
const eq = (a, b, msg) =>
  ok(a === b, `${msg}（实际 ${JSON.stringify(a)} ≠ 期望 ${JSON.stringify(b)}）`);
const state = async () => (await fetch(`${BASE}/api/state`)).json();

// 等服务端真落盘（请求是异步的，DOM 先变、盘后写）
async function waitFor(fn, msg, ms = 10000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    if (await fn()) return;
    await new Promise((r) => setTimeout(r, 150));
  }
  throw new Error(`❌ 等待超时：${msg}`);
}

const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  // 只收 JS 异常：媒体/图片 404 是夹具的常态（没有真 GIF、没有真切片），
  // 它们走 network，不是 pageerror，不能当失败
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));

  await page.goto(`${BASE}/edit.html`, { waitUntil: "domcontentloaded" });
  await page.locator("#app .topbar input").first().waitFor();

  // —— 空态：右栏文案必须原样来自服务端（Task 5 的承诺是「前端不自己 union
  // 文案」，而冒烟只覆盖到 .topbar、抓不到这条）。此刻还没开会话 ⇒ 服务端走的
  // 是 state_payload() 的 base 分支，hint 恒为「请先填视频路径（本机文件全路径）」。
  // 这条断言也顺手钉住「末载入时右侧渲染的就是 .stage-hint」这层结构。
  const s0 = await state();
  eq(s0.loaded, false, "刚打开时服务端还没载入会话（e2e 服务是全新的）");
  await waitFor(async () => (await page.locator("#app .stage-hint").count()) === 1,
                "右栏空态出现（store.refresh 回了包）");
  eq((await page.locator("#app .stage-hint").first().innerText()).trim(), s0.hint,
     "右栏空态文案 = 服务端下发的 hint（不是前端自编）");

  // —— 载入会话：走用户真路径（填路径 → 回车 → @change 发 POST /api/session）
  const input = page.locator("#app .topbar input").first();
  await input.fill(VIDEO);
  await input.press("Enter");
  await page.locator("#app .el-table__row").first().waitFor({ timeout: 20000 });

  const s = await state();
  const n = await page.locator("#app .el-table__row").count();
  eq(n, s.rows.length, "表格行数 = 后端 rows 条数");
  eq(n, 12, "夹具 12 颗球都上了表");

  // 时刻列用服务端下发的 t 串（R12），逐行核对
  const cell = async (i, c) => (await page.locator("#app .el-table__row").nth(i)
    .locator("td").nth(c).innerText()).trim();
  for (let i = 0; i < s.rows.length; i++) {
    ok((await cell(i, 2)).includes(s.rows[i].t.split(".")[0]),
       `第 ${i + 1} 行时刻「${await cell(i, 2)}」含后端 t=${s.rows[i].t}`);
  }

  // 置信度条的颜色必须 = 「档位」列的档位色（`tiers.js` 是配色的唯一来源）。
  // 曾按 `confColor` 里 0.8 / 0.4 这对残骸字面量着色（9ba3682 引入），档位
  // 阈值后来换成 0.65 / 0.50 也没人同步它 ⇒ 同一行两个结论（2026-10-11 用户
  // 报「77% 仍然是黄色」）。夹具 conf=0.90−0.01i、档位三色轮转，正好覆盖三档；
  // 其中第 3 颗（⚪ 低，conf 0.88）在旧实现下整条是**绿色**的，最能证伪。
  const TIER_RGB = { "🟢 高": "rgb(103, 194, 58)", "🟡 待": "rgb(230, 162, 60)",
                     "⚪ 低": "rgb(144, 147, 153)" };
  const rowLoc = page.locator("#app .el-table__row");
  const barMismatch = [];
  for (let i = 0; i < s.rows.length; i++) {
    const t = (await rowLoc.nth(i).locator("td").nth(3).locator(".el-tag")
      .innerText()).trim();
    const c = await rowLoc.nth(i).locator(".el-progress-bar__inner")
      .evaluate((el) => getComputedStyle(el).backgroundColor);
    if (TIER_RGB[t] !== c) barMismatch.push(`${i + 1}行:${t}→${c}`);
  }
  ok(barMismatch.length === 0,
     `置信度条颜色 = 档位色（不符：${barMismatch.join(" / ")}）`);

  // 时间轴标记条：每颗球一个 dot，位置按显示时刻算
  eq(await page.locator("#app .tl-dot").count(), s.rows.length, "时间轴 dot 数 = 行数");
  // A-8：中轴是**横贯**那条线（原型 `前端原型/剪辑台.html:88` 的
  // `left:0;right:0;top:8px;height:2px`），不是中间一根无来由的竖杠。
  // 断 `::before` 的**计算样式**（不是源码字面量）：竖线版的 height 会是
  // 100%/18px、left 是 50%，三个值一起把形状钉死。
  const axis = await page.locator("#app .tl").evaluate((el) => {
    const s2 = getComputedStyle(el, "::before");
    return { w: s2.width, h: s2.height, left: s2.left, top: s2.top };
  });
  eq(axis.h, "2px", "时间轴中轴高 2px（竖线版会是 100%/18px）");
  eq(axis.left, "0px", "中轴从容器左沿起（竖线版是 50%）");
  ok(parseFloat(axis.w) > 100, `中轴横贯整条刻度（宽 ${axis.w}）`);

  // 页脚：显示/未删/已删/勾选四处计数与后端一致
  const foot = (await page.locator("#app .tblfoot").innerText()).replace(/\s+/g, " ");
  ok(foot.includes(`显示 ${s.rows.length} / 未删 ${s.rows.length} 条`),
     `页脚显示计数（实际「${foot}」）`);
  ok(foot.includes("勾选 0 条进集锦"), `页脚初始勾选为 0（实际「${foot}」）`);
  // A-4：页脚渲染**服务端下发**的汇总行（`api_session.py` 的
  // `A._table_summary_md`）。原先一个地方都没渲染它 ⇒ 用户看不到「共几条、
  // 三档各几条」。断的是服务端那句原话的特征片段——数字全来自夹具（12 颗、
  // 三档各 4），不是前端自己再算一份。
  ok(foot.includes("视频总长 0 分 6 秒 · 共 12 条（🟢 高置信 4 / 🟡 待确认 4 / ⚪ 低置信 4"),
     `页脚有服务端的汇总行（实际「${foot}」）`);

  // A-4：顶栏三档计数 + 已勾选（形状照原型 `前端原型/剪辑台.html:126-130`）。
  // `type` 必须来自 `tiers.js`（本分支的单一来源）⇒ 断 el-tag 的 type 类名，
  // 光断文案的话「色值另写一份」照样绿。
  const topTags = await page.locator("#app .topbar .el-tag").evaluateAll((els) =>
    els.map((e) => `${e.innerText.trim()}|${e.className}`));
  eq(topTags.length, 5, "顶栏五枚 tag：三档计数 + 已勾选 + ★ 精选");
  ok(topTags[0].startsWith("🟢 4|") && topTags[0].includes("el-tag--success"),
     `🟢 计数与 type 来自 tiers.js（实际「${topTags[0]}」）`);
  ok(topTags[1].startsWith("🟡 4|") && topTags[1].includes("el-tag--warning"),
     `🟡 计数与 type 来自 tiers.js（实际「${topTags[1]}」）`);
  ok(topTags[2].startsWith("⚪ 4|") && topTags[2].includes("el-tag--info"),
     `⚪ 计数与 type 来自 tiers.js（实际「${topTags[2]}」）`);
  ok(topTags[3].startsWith("已勾选 0%|") && !topTags[3].includes("el-tag--success"),
     `「已勾选 n%」是 effect=plain 那枚（实际「${topTags[3]}」）`);
  // ★ 精选计数（2026-10-23）：未打星时是 info 灰、打星后转 warning 橙。
  // 它就是"这次单条能出几条"——数字必须来自 rows[].starred（服务端权威），
  // 不是前端另算一份。
  ok(topTags[4].startsWith("★ 0|") && topTags[4].includes("el-tag--info"),
     `★ 精选计数为 0 且是灰 tag（实际「${topTags[4]}」）`);

  // 勾选第一行：状态必须来自服务端回包，不是本地复选框自己变
  const row0 = page.locator("#app .el-table__row").first();
  // 先点「时刻」单元格选中它——整行中心可能落在「标签」列的下拉上
  await row0.locator("td").nth(2).click();
  ok(await page.locator("#app .el-table__row.current-row").count() === 1,
     "点行后该行成为当前行（右栏靠它出内容）");
  await row0.locator(".el-checkbox").click();
  await waitFor(async () => (await state()).rows.filter((r) => r.selection).length === 1,
                "勾选落到服务端");
  const s2 = await state();
  eq(s2.rows.filter((r) => r.selection).length, 1, "后端只有 1 行被勾选");
  ok(s2.rows[0].selection, "被勾选的是第一行");
  const foot2 = (await page.locator("#app .tblfoot").innerText()).replace(/\s+/g, " ");
  ok(foot2.includes("勾选 1 条进集锦"), `页脚勾选计数跟着变（实际「${foot2}」）`);
  // 写操作整份替换 state、行对象重建 ⇒ 当前行高亮必须还在。这条锁的是
  // 「高亮不丢」这个可见不变量，**不归因于某段代码**：实测 el-table 设了
  // `:row-key`，element-plus 的 `useCurrent.updateCurrentRowData()` 在 setData
  // 时自己就按 key 重解析了当前行，页面侧那段重指向删掉这条也照样绿。
  eq(await page.locator("#app .el-table__row.current-row").count(), 1,
     "写一次之后当前行高亮还在（整份替换 state 后高亮仍然持久）");

  // 再点一次复原：夹具回到初始态，方便后续任务追加的断言（也顺手验了取消勾选）
  await row0.locator(".el-checkbox").click();
  await waitFor(async () => (await state()).rows.every((r) => !r.selection),
                "取消勾选落到服务端");
  // 复审抓的恒真断言（B-1）：`ok(true, …)` 不验任何东西、只把项数 +1。
  // 上面那条 `waitFor` 超时确实会抛（不算假绿），但它是**等待**；这里自己断一次
  // 服务端真值，这行的文案才配得上一个项数。
  eq((await state()).rows.filter((r) => r.selection).length, 0,
     "取消勾选落回服务端（服务端勾选数 = 0）");

  // —— 标签列 allow-create：库里没有的新名字必须**先入库、再打标**（P9b）。
  // 只断行标签一定假绿：实测把 onTag 里的 act.addTag 去掉后，行标签照样打成、
  // 后端照收，只是标签库一次都不涨（POST /api/tags 一次都不发）⇒ 筛选下拉与
  // 「1–9 打标签」清单永远看不到新标签。所以这条**必须断 state.tags 的增长**。
  const TAGCELL = 4;              // 列序：0 缩略图 / 1 进片 / 2 时刻 / 3 档位 / 4 标签
  const NEWTAG = "reviewtag9";
  const tagCell = () =>
    page.locator("#app .el-table__row").first().locator("td").nth(TAGCELL);
  const sBefore = await state();
  ok(!sBefore.tags.includes(NEWTAG), `新标签 ${NEWTAG} 起点不该在库里`);
  await tagCell().locator(".el-select__wrapper").click();
  await tagCell().locator("input").fill(NEWTAG);
  // 候选必须限定在**看得见**的那个下拉里：13 行各挂一份下拉，库里也有这个
  // 名字之后别的行的下拉里同样会出现它（那些是 display:none）
  await page.locator(".el-select-dropdown__item:visible")
    .filter({ hasText: NEWTAG }).first().click();
  await waitFor(async () => (await state()).tags.includes(NEWTAG),
                "新标签进了全局标签库（= POST /api/tags 真发了）");
  // 「先入库、后打标」是**两次请求**：入库那趟的回包先把 state 换成「有标签、
  // 但该行还没打上」，所以行标签必然后到——分开等，别抢跑
  await waitFor(async () => (await state()).rows[0].tag === NEWTAG,
                "新标签落到该行（addTag 之后才是 setTag）");
  const s3 = await state();
  ok(s3.tags.includes(NEWTAG),
     `标签库长出了 ${NEWTAG}（实际 ${JSON.stringify(s3.tags)}）`);
  eq(s3.rows[0].tag, NEWTAG, "该行标签 = 刚输入的新名字");
  eq(s3.tags.slice(0, sBefore.tags.length).join(","), sBefore.tags.join(","),
     "新标签追加在库尾，原有项一个不动（Task 7 的 1–9 索引依赖它）");

  // 清回空：顺带验了 clearable 的清标签路径（库里那条留着——后端没有删标签端点）。
  // ⚠ 但本段**并没有**把夹具交回干净态：上面打标那一步，后端 `set_row_tag` 会
  // **顺带勾选**该行，而清标签**不**取消勾选（`bbh/app.py` 的既有语义，见
  // actions.js 的 setTag 注释）⇒ 交给下一段的是「第 1 行仍勾着」。
  // 下一段（本文件末尾的键盘流）自己复位，别指望这里。
  await tagCell().locator(".el-select__wrapper").hover();
  await tagCell().locator(".el-select__clear").click();
  await waitFor(async () => (await state()).rows[0].tag === "", "行标签清回空");
  // 复审抓的恒真断言（B-1）+ 原来那句还**多承诺了半句**：「标签库里这条保留」
  // 写在断言的文案里，却从来没有被断过。后端确实没有删标签端点（清标签只清行上
  // 那个引用），那就把它断出来——不然这半句是假的。
  const s5 = await state();
  ok(s5.tags.includes(NEWTAG),
     `清标签**不**动全局标签库：${NEWTAG} 还在（后端没有删标签端点，`
     + `实际 ${JSON.stringify(s5.tags)}）`);

  // —— 键盘流（Task 7）：复核闭环的左手侧 ——
  // 前置复位：本段下面两条断言（「空格勾上第一颗」「栈空了再按只提示」）都以
  // 「全不勾 + 撤销栈空」为前提，而上一段退出时两条都不成立——
  //  ① 上一段的标签列打非空标签时后端 set_row_tag 会**顺带勾选**该行，而清标签
  //     **不**取消勾选（`bbh/app.py` 的既有语义，见 actions.setTag 注释）⇒ 第一行
  //     此刻还勾着，空格按下去只会把它**取消**（本段第一次跑就红在这条上）。
  //  ② 撤销栈是**页面级**的（每个写操作入栈，见 actions.once），到这儿已经躺着
  //     上一段的四条（勾/取消勾/打标/清标签）；前端没有清栈入口，所以整页重载
  //     一次换一份新实例 = 空栈。重载只换前端：服务端会话仍在内存里、临时目录的
  //     work/ 一个字节不动（`/api/state` 照旧回已载入的那份），夹具状态不丢。
  // ⚠ 复位要**条件化**：这一击的前提是「上一段真的留下了勾选」。若无条件点下去，
  // 而上游某步被改得不再留残留，这一击反而会把第 1 行**勾上**、紧接着的
  // `waitFor(全不勾)` 就反向超时——红得像本段自己的 bug。有才点，语义不变。
  if ((await state()).rows.some((r) => r.selection)) {
    await page.locator("#app .el-table__row").first().locator(".el-checkbox").click();
    await waitFor(async () => (await state()).rows.every((r) => !r.selection),
                  "复位：摘掉上一段「打标顺带勾选」留下的那一行");
  }
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator("#app .el-table__row").first().waitFor({ timeout: 20000 });

  // 点「时刻」单元格选中行：整行中心可能正落在「标签」列的下拉上，点它会
  // 把焦点给 select 的输入框，而那正是键盘闸门①要拦的场景（键就不生效了）。
  const rowCell = (i) => page.locator("#app .el-table__row").nth(i).locator("td").nth(2);
  const curTxt = async () => {
    const r = page.locator("#app .el-table__row.current-row");
    return (await r.count()) ? (await r.first().locator("td").nth(2).innerText()).trim() : "";
  };
  const keys = async (k) => { await page.keyboard.press(k); await page.waitForTimeout(150); };
  // toast 挂在 document.body 上（ElMessage 默认 teleport），不在 #app 里；
  // 按文案过滤再看有没有——上一枚 toast 有 3s 存活期，不能用 .first() 猜。
  const toast = (t, ms = 4000) =>
    page.locator(".el-message").filter({ hasText: t }).first()
      .waitFor({ timeout: ms }).then(() => true, () => false);

  await rowCell(0).click();
  // ↓/↑ 换行
  await keys("ArrowDown");
  eq(await curTxt(), s.rows[1].t, "↓ 选中第二行");
  await keys("ArrowUp");
  eq(await curTxt(), s.rows[0].t, "↑ 回到第一行");

  // 空格 = 确认进球：勾上后自动跳到下一颗「未勾选且未被否决」的球
  await keys("Space");
  await waitFor(async () => (await state()).rows[0].selection, "空格勾上第一颗");
  // ⚠ 自动跳行发生在**回包之后**再 `setTimeout(120)`（`confirmRow`）⇒ 预算里要塞进
  // 「一次 HTTP PATCH 往返 + 120ms」，而 `keys()` 只固定等 150ms（余量 ~30ms 减 RTT，
  // 实测 7 跑 1 红）⇒ 这两条必须是 `waitFor`。上面那句等服务端真值的 `waitFor` **保留**：
  // 它证的是「服务端写进去了」，与这两条证的「120ms 后界面跟上了」是两件事。
  // ⚠ **只改这两处**：`spec` 里别的 `eq(await curTxt(), …)` 前面跟的是 `↑/↓` 之类
  // 纯前端同步移动（没有请求、没有定时器，150ms 绰绰有余）⇒ 把它们也换成 `waitFor`
  // 会把「同步移动退化成异步」这种真 bug 一起放过去。判据是「回包 + 定时器」。
  await waitFor(async () => (await curTxt()) === s.rows[1].t, "勾上后自动跳到第二颗");
  await keys("Space");
  await waitFor(async () => (await state()).rows[1].selection, "空格勾上第二颗");
  await waitFor(async () => (await curTxt()) === s.rows[2].t, "再勾一颗、再跳一颗");
  await keys("ArrowUp");
  eq(await curTxt(), s.rows[1].t, "↑ 回到第二颗");

  // X = ❌ 误判：在**已勾选**的这一行按 X，联动取消进片（R16）
  await keys("KeyX");
  await waitFor(async () => (await state()).verdicts[s.rows[1].key] === "not_goal",
                "X 记下误判反馈");
  await waitFor(async () => !(await state()).rows[1].selection,
                "❌ 联动取消了这一行的进片（R16）");

  // Ctrl+Z 撤销误判：裁决消失 **且** 那一步联动取消的勾选被还原
  await keys("Control+KeyZ");
  await waitFor(async () => !(await state()).verdicts[s.rows[1].key], "撤销误判");
  ok(await toast("已撤销"), "撤销有提示（R17）");
  await waitFor(async () => (await state()).rows[1].selection,
                "撤销把联动取消掉的勾选也还原了");
  // 再撤两次：两颗球的空格勾选
  await keys("Control+KeyZ");
  await waitFor(async () => !(await state()).rows[1].selection, "撤销第二颗的勾选");
  await keys("Control+KeyZ");
  await waitFor(async () => (await state()).rows.every((r) => !r.selection),
                "撤销第一颗的勾选，回到全不勾");
  // 栈空了再按：只提示，不报错
  await keys("Control+KeyZ");
  ok(await toast("没有可撤销的操作"), "栈空时 Ctrl+Z 只提示不报错");

  // —— A-9：`Ctrl+Shift+Z` 不许撤销 ——
  // 原型的判据是 `if (e.ctrlKey && !e.shiftKey && …)`，而 `keyName` 原先不看
  // `shiftKey` ⇒ Ctrl+Shift+Z 落回 `ctrl+z`，**真的撤销一步**。这不是「多支持了
  // 一个快捷键」：Ctrl+Shift+Z 是 Windows 的「重做」肌肉记忆，而本应用没有重做
  // （`replay` 不入栈）⇒ 那一步撤销自己撤不回来。
  // 先造一步已知的栈顶（此刻栈刚空过）：空格把第 1 颗勾上。
  await rowCell(0).click();
  await keys("Space");
  await waitFor(async () => (await state()).rows[0].selection, "造栈顶：空格勾上第 1 颗");
  await page.keyboard.press("Control+Shift+KeyZ");
  // 固定等待，不是「等绿」：坏实现下这一步撤销要走一次 HTTP 往返，600ms 足够
  // 落地；而**正因为它是固定等待**，下面必须有一条「真 Ctrl+Z 照样生效」的
  // 反向对照，否则「键全废了」也会让这条绿。
  await page.waitForTimeout(600);
  ok((await state()).rows[0].selection, "Ctrl+Shift+Z 什么都没做（坏实现会真撤销一步）");
  await keys("Control+KeyZ");
  await waitFor(async () => !(await state()).rows[0].selection,
                "真 Ctrl+Z 照旧撤销（栈是活的——上面那条不是因为键全废才绿）");
  ok(!errs.length, "键盘流没打出 JS 异常");

  // 1-9 打标签（夹具种了 tags.json：三分/上篮/罚球 → `1` 打「三分」）
  // 打标会顺带勾选该行（后端 set_row_tag 的既有行为），撤销也**不**还原那次勾选——
  // 与旧界面同病（见 actions.setTag 注释），所以本段最后手工复原。
  await rowCell(0).click();
  await keys("Digit1");
  await waitFor(async () => (await state()).rows[0].tag === "三分", "1 打上第一个标签");
  ok((await state()).rows[0].selection, "打标顺带勾选（后端 set_row_tag 的既有行为）");

  // 复原夹具：撤标签 → 手工取消勾选（后面任务的断言都从「全不勾、无标签」起）
  await keys("Control+KeyZ");
  await waitFor(async () => !(await state()).rows[0].tag, "撤销标签");
  await page.locator("#app .el-table__row").first().locator(".el-checkbox").click();
  await waitFor(async () => (await state()).rows.every((r) => !r.selection), "复原勾选");
  // 注意：复原用的那次勾选点击**也会入栈**（它走 actions.setSelection），
  // 所以本段结束时栈里还剩 1 条。后续任务不要假设栈是空的——要验撤销就自己先造一步。

  // 复核 300 颗球时「↑↓ 要跟着滚」是帮助文案里的承诺（Task 9 逐字照抄原型），
  // 所以这里必须钉住：连按 ↓ 到夹具第 8 行，当前行得在表格可视区里。
  // 若这条红，就在 pick() 的 setCurrentRow 后补
  // `rowEl.scrollIntoView({ block: "nearest" })`——el-table 的 setCurrentRow
  // 是否顺带滚动随版本而变，别假设它滚。
  // 起点是上面 `rowCell(0).click()` 选中的**第 1 行（索引 0）**（本段前面那次
  // 点复选框不会移当前行）。7 次 ↓ ⇒ 索引 7，也就是人话里的「第 8 行」——
  // 断言写 `s.rows[7]`，别写 `s.rows[8]`（那要求 8 次 ↓）。
  // ⚠ 上面那次「点复选框复原」会把焦点留在 el-checkbox 的原生 input 上
  // （实测 activeElement = INPUT.el-checkbox__original）——那正是键盘闸门①
  // （输入框里打字一律不触发）要拦的场景，键会被整段吞掉、当前行一步不动。
  // 点回「时刻」单元格一举两得：① 把焦点交回页面（td 不可聚焦 ⇒ activeElement
  // 落回 tbody）；② 把当前行**移**到该行——实测点 td 会触发 el-table 的
  // current-change（这不是「不移当前行」，别照抄旧注释）⇒ 起点被钉死在索引 0，
  // 不依赖「上一段碰巧没动过当前行」。
  await rowCell(0).click();
  // ⚠ 先把视口压矮，这条断言才不是空跑：1600×900 下表格可视区实测 662px、
  // 表体只有 492px ⇒ **根本没有滚动条**，12 行全都「在可视区里」，那条断言
  // 恒真（夹具注释里「行数够多，表格才会真的出现滚动条」的前提并不成立）。
  // 压到 420 高后可视带只剩 ~182px，第 8 行会真的落在带外——没有 pick() 里
  // 那次 scrollIntoView 时，实测该行 y=492、可视带 205~387，断言当场红。
  await page.setViewportSize({ width: 1600, height: 420 });
  await page.waitForTimeout(300);
  for (let i = 0; i < 7; i++) await keys("ArrowDown");
  eq(await curTxt(), s.rows[7].t, "连按 ↓ 到第 8 行（索引 7）");
  const box = await page.locator("#app .el-table__row.current-row").first().boundingBox();
  const wrap = await page.locator("#app .el-table__body-wrapper").first().boundingBox();
  ok(box && wrap && box.y >= wrap.y - 1 && box.y + box.height <= wrap.y + wrap.height + 1,
     `当前行滚进了表格可视区（行 y=${box?.y} 视区 ${wrap?.y}~${wrap && wrap.y + wrap.height}）`);

  // ⚠ 段尾**必须**把视口还原（后来者别删这句，它不是装饰，是给**下一段**用的）：
  // 下面的 throw 就在两行之后，而后续任务的段全都插在那个 throw 之前 ⇒ 不还原
  // 的话它们会继承上面那个 420 高的视口。复审实测 420 下 `.right` 可视带只剩
  // 327px（editbayH=380 / topbarH=53 / rightTop=93），而 Task 8 右栏内容高 435px
  // ⇒ `.subbtns` 下沿落在视口 y≈443，Task 8 的「第二排按钮不用滚动就在窗内」与
  // 「右栏 scrollTop === 0」两条会红得**与它自己的代码无关**。
  // Task 8 段首自己也会声明一次同样的还原（`392c06e`）——两处都做是有意的：
  // 每段自足，且不依赖邻居替自己还原。
  await page.setViewportSize({ width: 1600, height: 900 });

  // —— 右栏（Task 8）：切片播放器 + 判定依据卡 ——
  // ⚠ 本段**自己声明依赖的视口**，不继承上一段剩下的那份：Task 7 的段把视口
  // 压到 1600×420 好让「滚进可视区」那条断言有牙（它段尾已还原，但**别依赖
  // 邻居的还原**——每段自足才是本 spec 的纪律）。本段下面的
  // 「第二排按钮不用滚动就在窗内」量与 09-19 用户裁决（900/800/720 三种窗高
  // 下都点得到）都是按 **900** 窗高定的。实测继承到 420 的后果：右栏可视带
  // 只剩 ~327px 而内容要 ~435px（`editbayH=380, topbarH=53, rightTop=93`）⇒
  // `.subbtns` 下沿落在视口 y≈443 ⇒ 本段两条断言必红，而且红的原因与本段
  // 代码毫无关系、会把实现者引去改 `.right` 布局（错的修改方向）。
  await page.setViewportSize({ width: 1600, height: 900 });
  // 再把当前行挪回第一颗（Task 7 那段结束时停在连按 ↓ 后的第 8 行＝索引 7）
  await rowCell(0).click();
  const cur = (await state()).rows[0];
  const secs = (t) => { const [m, s] = t.split(":"); return Number(m) * 60 + Number(s); };
  const clipSrc = () => page.locator("#app .videobox video").getAttribute("src");

  const u = new URL(await clipSrc(), BASE);
  eq(u.pathname, "/media/clip", "播放器指向切片端点");
  eq(Number(u.searchParams.get("t")), secs(cur.t), "切片 t = 该行的显示时刻");
  ok(u.searchParams.has("before") && u.searchParams.has("after"), "切片带前后秒");
  ok(u.searchParams.has("v"), "切片 URL 带 v=（击穿缓存，换视频/换校准后不播旧切片）");
  eq(await page.locator("#app .tricolor").count(), 1, "分色进度条在位");
  eq(await page.locator("#app .tricolor .playhead").count(), 1, "播放头在位");
  // A-7：「进球 00:01.2」这个标注**指的就是红线位置**（= `goalPct`，此刻
  // 5/7 ≈ 71.4%）。原先写死 `left:6px` ⇒ 它指着色带最左端，与红线毫无关系。
  // 断言用**几何量**（标注左沿 + 14px 的 translateX == 红线左沿，±2px 容差
  // 涵盖红线 `calc(…% - 1.5px)` 那半个像素与取整），不是源码字面量。
  const barBox = await page.locator("#app .tricolor").boundingBox();
  const mkBox = await page.locator("#app .tricolor .goalmk").boundingBox();
  const redBox = await page.locator("#app .tricolor .seg-red").boundingBox();
  ok(Math.abs((mkBox.x + 14) - redBox.x) <= 2,
     `标注骑在红线上（标注 ${Math.round(mkBox.x + 14)} vs 红线 ${Math.round(redBox.x)}）`);
  ok(mkBox.x > barBox.x + 20,
     `标注不在色带最左端（实际距左沿 ${Math.round(mkBox.x - barBox.x)}px——写死 left:6px 那条错）`);

  // ⓘ 依据卡：常显只有「时刻 + 档位 + ⓘ」，算式/证据/reason 全在 popover 里
  const tagTxt = (await page.locator("#app .basis .head .el-tag").innerText()).trim();
  ok(["🟢 高", "🟡 待", "⚪ 低"].includes(tagTxt), `常显档位（实际「${tagTxt}」）`);
  ok(!(await page.locator("#app .basis").innerText()).includes("置信度"),
     "依据卡不常显算式（09-17/18 裁决：算式/网区/结论/完整数据只活在 ⓘ 里）");
  // ⚠ 这里**别**断 `count() === 0`（原计划写的就是它，是错的）。el-popover 的
  // `persistent` **默认就是 true**（`element-plus/es/components/popover/src/popover.mjs`
  // 的 props，其自带文档也写着「persistent 为 false 时 popover 才会被销毁」），
  // 它把该 prop 透传给 el-tooltip ⇒ `tooltip/src/content…mjs` 里
  // `shouldRender = persistent ? true : open` **恒 true** ⇒ 内容是**挂载即渲染、
  // 用 `v-show` 隐藏**。BasisPanel 一挂载（上面点了行之后）`pre.rawv` 就在 body 里，
  // 而 `count()` **不看可见性** ⇒ 那种断法必红；更坏的是下面那条 `waitFor`
  // 会因为「本来就在」而**假过**，让人误判成环境问题。
  // 要钉的裁决是「算式/证据只活在 ⓘ 里」，所以断**可见性**：
  ok(!(await page.locator("body pre.rawv").isVisible()),
     "ⓘ 收着时完整判定数据块不显示（popover 常驻 DOM、v-show 隐藏）");
  await page.locator("#app .basis .conf-ico").hover();
  // 选择器**不带 `#app`**：el-popover 的内容 teleport 到 body，加 #app 会匹配不到
  await page.locator("body pre.rawv").waitFor({ timeout: 5000 });
  const pop = (await page.locator("body .pop").innerText()).replace(/\s+/g, " ");
  ok(pop.includes("判定依据（鼠标移开即收起）"), "ⓘ 标题逐字");
  ok(pop.includes(`置信度 ${cur.conf.toFixed(2)} ＝ 基础`) && pop.includes("＋ 网")
     && pop.includes("≥0.60") && pop.includes("0.50~0.59")
     && !pop.includes("≥0.70"),
     "置信度算式自洽且含网分项（算式用服务端 terms，前端不自拼）；档位线随 HIGH_CONF=0.60 联动（前端不许残留 0.70/0.65）");
  ok(pop.includes("cross_off") && pop.includes("net_post"), "完整判定数据块逐字段");
  ok(pop.includes(cur.reason.slice(0, 8)), "reason 来自服务端（不前端自拼）");
  await page.mouse.move(0, 0);

  // A-3：v18 全行测网后，net_* 为 None 只剩一种正常形态——测量失败
  // （fail-open：视频打不开/seek 出界），证据缺失不当否证、网分按 0。
  // 夹具里唯独第 2 颗（🟡，i=1）net 为 None（见 test_edit_bay_e2e.py::_ev）
  // ——那条就是本段要断的行。
  const r1 = (await state()).rows[1];
  eq(r1.status, "review", "第 2 颗是 🟡（夹具档位三色轮转）");
  await rowCell(1).click();
  await waitFor(async () => (await page.locator("#app .basis .head .el-tag").innerText())
                .includes("🟡"), "右栏切到 🟡 那一行");
  await page.locator("#app .basis .conf-ico").hover();
  await page.locator("body .pop .warn").waitFor({ timeout: 5000 });
  const warn = (await page.locator("body .pop .warn").innerText()).replace(/\s+/g, " ");
  ok(warn.includes("fail-open") && warn.includes("不当否证"),
     `🟡 行说明为 fail-open 文案（实际「${warn}」）`);
  ok(!warn.includes("只测 high"), "不再写「只测 high」（v18 全行测网后已失效）");
  await page.mouse.move(0, 0);
  // 复位回 rows[0]：本段下面那些按钮/断言（vbtn 等）都作用在**当前行**上
  await rowCell(0).click();
  await waitFor(async () => (await page.locator("#app .basis .head .el-tag").innerText())
                .includes("🟢"), "A-3 检查完把当前行复位回第 1 颗");

  // 两排按钮 + 裁决联动（✅ 是进球 → 勾选）。
  // ⚠ 取消反馈**不**还原那次自动勾选——这是 Task 4 已交付并过审的语义，别按
  // 「取消就该还原」写断言（原计划写的就是那句，会必红、还把责任推给实现者）。
  // 真机制在 `frontend/src/edit/actions.js`：`touchSel = wasSel !== wantSel`。
  // 走「同按钮再点」这条取消路径时 `before === verdict === "goal"` ⇒
  // `wantSel = true`，而此时行**已经是勾上的**（第一次 ✅ 经 R16 勾的）⇒
  // `wasSel = true` ⇒ `touchSel === false` ⇒ **那个补勾选的 PATCH 根本不发**。
  // 后端也不管勾选：`mark_verdict` 的取消分支只 `del fb["verdicts"][hit]` +
  // `_save_feedback`，`POST /api/feedback/{key}` 不碰 selection.json ⇒ 勾选留着。
  // 与「撤销打标签不还原勾选」同病（见 actions.setTag 注释），本计划不修。
  const vbtn = page.locator("#app .basis .rowbtns .el-button").first();
  await vbtn.click();
  await waitFor(async () => (await state()).verdicts[cur.key] === "goal", "✅ 记为是进球");
  await waitFor(async () => (await state()).rows[0].selection, "✅ 联动勾选进片（R16）");
  // A-10：`feedback.summaryMd` 是**服务端下发的 markdown**（`_fp_summary`：
  // 「已裁决 **1** 条是进球、… → `feedback.json`」），旧界面用 gr.Markdown 渲染它。
  // 新界面直接塞进 `<div>` ⇒ 用户看到的是一串 `**1**` 与反引号。显示层剥标记、
  // **一个字都不改文案本身**。这条同时钉住「剥了」与「没把数字也剥掉」。
  await waitFor(async () => (await page.locator("#app .basis .fbref").innerText())
                .includes("已裁决 1 条是进球"),
                "反馈摘要跟着裁决刷新（剥掉标记后仍是服务端那份文案）");
  const fb = await page.locator("#app .basis .fbref").innerText();
  ok(fb.includes("feedback.json"),
     `反引号也剥了（feedback.json 裸着出现，实际「${fb}」）`);
  ok(!fb.includes("**") && !fb.includes("`"),
     `markdown 强调标记一个不剩（实际「${fb}」）`);
  await vbtn.click();
  await waitFor(async () => !(await state()).verdicts[cur.key], "同按钮再点取消反馈");
  ok((await state()).rows[0].selection,
     "取消反馈**不动**勾选（取消路径 touchSel=false）");
  eq(await page.locator("#app .basis .subbtns .el-button").count(), 3, "第二排三个按钮");

  // 「操作按钮排在出手人 chip 之上且不用滚动就能点到」（09-19 裁决）
  const bRow = await page.locator("#app .basis .rowbtns").boundingBox();
  const bSub = await page.locator("#app .basis .subbtns").boundingBox();
  const bChip = await page.locator("#app .basis .shooter").boundingBox();
  const vp = page.viewportSize();
  ok(bRow.y + bRow.height <= bSub.y + 1, "两排按钮：✅/❌ 在第一排（在第二排之上）");
  ok(bSub.y + bSub.height <= bChip.y + 1, "两排按钮整组排在出手人 chip 之上");
  ok(bSub.y >= 0 && bSub.y + bSub.height <= vp.height,
     `第二排按钮不用滚动就在窗内（底 ${Math.round(bSub.y + bSub.height)} / 窗高 ${vp.height}）`);
  // 「不用滚动」这半句得连「右栏真的没在滚」一起断：否则将来 `.right` 滚了
  // 一截、按钮刚好落在窗内，这条会绿得毫无意义，而 09-19 裁决的前提是整块
  // 布局在 900/800/720 三种窗高下**放得下**（不是靠滚到了位置）。
  eq(await page.locator("#app .right").evaluate((el) => el.scrollTop), 0,
     "右栏没有纵向滚动——按钮在窗内是靠布局，不是靠滚");
  // ⚠ `scrollTop === 0` **单独证明不了「没溢出」**：溢出但还没滚过时它照样是 0
  // （复审实测：720 窗高 + 真切片下 `scrollHeight 659 > clientHeight 627`，而
  // `scrollTop` 仍为 0 ⇒ 上一条照样绿）。换成量**溢出本身**，与上面那条互为独立证据。
  ok(await page.locator("#app .right")
       .evaluate((el) => el.scrollHeight <= el.clientHeight),
     "右栏内容没有溢出（scrollTop===0 单独不够：溢出未滚时它也是 0）");

  // 误标 toggle：点一次标上、再点取消（后端 _toggle_mark_flag）
  const fbtn = page.locator("#app .basis .subbtns .el-button").nth(1);
  await fbtn.click();
  await waitFor(async () => (await state()).flags.thumb.includes(cur.key), "缩略图误标落盘");
  ok((await fbtn.innerText()).includes("已标"), "按钮文案切到已标态");
  // A-5：计数 + 清单（原先 `state.flags` 只用来判当前行的按钮态，用户标完
  // 看不到自己标过哪几颗）。计数与 `title` 里的清单都取自服务端下发的 key 集。
  const ftip = page.locator("#app .basis .flagtip");
  await ftip.waitFor({ timeout: 5000 });
  ok((await ftip.innerText()).includes("误标采样 ⚠1 张 ✚0 个"),
     `误标采样计数行出现（实际「${await ftip.innerText()}」）`);
  const ftitle = await ftip.getAttribute("title");
  ok(ftitle.includes("⚠ 缩略图有误：") && ftitle.includes(cur.t),
     `悬停清单带该球的时刻（实际「${ftitle}」）`);
  await fbtn.click();
  await waitFor(async () => !(await state()).flags.thumb.includes(cur.key), "再点取消误标");
  // waitFor 而不是直接 eq：我们的轮询读的是**服务端**，页面上那次 PATCH 的回包
  // 可能还没渲染完 —— 直接断会偶发红（红的原因与 A-5 无关）
  await waitFor(async () => (await ftip.count()) === 0,
                "两个计数都为 0 时整块收起（与原型 v-if 同款）");

  // 前后秒：改「前」→ 切片 URL 跟着变（会话内生效，/media/clip 真按它切）
  // 两个输入框**都**先记下原值：本段最后要把会话级默认**原样还回去**，
  // 免得 durAll 被改成 6/2 之后留给 Task 9/10（它们假设的是夹具原值）。
  const beforeInput = page.locator("#app .pctrl .el-input-number input").first();
  const afterInput = page.locator("#app .pctrl .el-input-number input").nth(1);
  const before0 = new URL(await clipSrc(), BASE).searchParams.get("before");
  const after0 = new URL(await clipSrc(), BASE).searchParams.get("after");
  await beforeInput.fill("6");
  await beforeInput.press("Enter");
  await waitFor(async () => new URL(await clipSrc(), BASE).searchParams.get("before") === "6",
                "改「前」之后切片 URL 跟着变");
  ok(before0 !== "6", `改之前是 ${before0}`);

  // —— 修复轮 1 的 F-1：清空输入框 ⇒ 那次提交必须**被忽略**（非有限值），
  // URL 与输入框都留在上一个有效值上。为什么不能收下：清空时 el-input-number
  // 交给 `@change` 的是 `null`（不是 0、不是空串），而 `durOf` 用**对象真值**
  // 判定 ⇒ `{before: null, after: 2}` 兜不住 ⇒ URL 变 `before=null` ⇒
  // `/media/clip` 被 FastAPI 的参数校验拦成 **400**（`bbh/web/app.py:55` 把
  // RequestValidationError 映射成 400，不是默认的 422），预览全坏；再点
  // 「应用到全部」还会把坏值扩散到**所有行**。
  // 语义是「忽略这次提交、保留原值」，**不是**「退回默认 5/2」——会话级前后秒
  // 作用于所有行，静默跳默认会毁掉用户特意设过的值。所以下面断的是「仍是 6」。
  await beforeInput.fill("");
  await beforeInput.press("Enter");
  // 固定 settle 一帧多：这次 change 的后果（换 src / 回填）是 Vue 下一轮渲染才落
  // 到 DOM 上的，`getAttribute` 只等「元素在」、不等「属性变」，直接读会读到旧值。
  // 这是**固定等待**，不是「等绿」——下面每条断言自己都有明确的期望值。
  await page.waitForTimeout(300);
  const clearedSrc = await clipSrc();
  ok(!clearedSrc.includes("null"), `清空提交没把 null 写进切片 URL（实际 ${clearedSrc}）`);
  const cu = new URL(clearedSrc, BASE);
  eq(cu.pathname, "/media/clip", "清空之后仍是切片端点");
  ok(Number.isFinite(Number(cu.searchParams.get("before")))
     && Number.isFinite(Number(cu.searchParams.get("after"))),
     "before/after 仍是有限数值");
  eq(cu.searchParams.get("before"), "6",
     "被忽略的那次提交保留了原值（6，不是跳回默认 5）");
  // 端点真收得下：上面的 URL 断言看的是**我们拼出来的串**，这条看**服务端怎么答**。
  // 坏参数在进 handler 之前就被参数校验打回 400 ⇒ 404（夹具没有真 mp4）才是正常回包。
  const clrResp = await page.request.get(cu.href);
  ok(clrResp.status() !== 400,
     `切片端点没被这次清空打成 400（实际 ${clrResp.status()}）`);
  // 输入框回填放在最后：它是 el-input-number 自己的 `setCurrentValueToModelValue()`
  // 干的，也是**异步**的；上面三条断的是「坏值没进 URL」，这条断的是「界面回到
  // 上一个有效值」。顺序有意的——坏实现下 URL 会先炸，这样每条断言都被走到过。
  await waitFor(async () => (await beforeInput.inputValue()).trim() !== "",
                "被忽略的那次清空把输入框回填回来（界面回到上一个有效值）");
  // ⚠ 只断 `Number.isFinite(Number(txt))` 会被**空串假过**：`Number("")` 是 0、
  // 0 是有限的。所以必须连「非空」一起断。
  const clearedTxt = (await beforeInput.inputValue()).trim();
  ok(clearedTxt !== "" && Number.isFinite(Number(clearedTxt)),
     `输入框回填成有限数值（实际「${clearedTxt}」）`);
  // 「应用到全部」= 会话级默认（R21：后端没有 before/after 端点）
  await page.locator("#app .pctrl button", { hasText: "应用到全部" }).click();
  ok(await toast("已应用到全部：前 6s"), "应用到全部有提示");

  // —— 修复轮 1 的 F-1 另一半牙：坏值**不许扩散到别的行** ——
  // 上面那几条断的都是右栏这一个 `<video>`（`clipSrc()` 读的就是当前行），而
  // F-1 裁定的核心危害正是**扩散路径**：会话级前后秒经「应用到全部」作用于
  // **所有行**。只钉当前行在坏实现下能全绿——当前行有自己的 `q.value[key]`
  // 覆盖时，会话级坏值根本不出现在它的 URL 里。所以这里连着点三行逐行读。
  // 此刻状态：清空那次提交已被忽略、「应用到全部」刚把会话级定成 6/2 并清空
  // `q` ⇒ 所有行都取同一个会话级值。
  let prevSrc = await clipSrc();
  const seenT = new Set();
  // ⚠ 循环从 **1** 起、不读 rows[0]（本块是 Task 11 落地的，落点说明见 task-11-report.md）：
  // ① 此刻的当前行**就是 rows[0]**（本段开头 `rowCell(0).click()`，之后没有动过它）⇒
  //    i=0 那一轮点的是已经选中的行，`clipSrc()` 根本不会变，上面那条「切片 URL 换成了
  //    这一行的」永远等不到（实测：`❌ 等待超时：切到 rows[0]…`）。
  // ② rows[0] 的 URL 上面 F-1 那一段已经逐条断过了（`cu`：无 null、before=6、after 有限），
  //    而本块要证的恰恰是「坏值**不许扩散到别的行**」⇒ 读 1/2/3 这三行比读 0/1/2 更贴题。
  for (const i of [1, 2, 3]) {
    const r = (await state()).rows[i];
    const row = page.locator("#app .el-table__row").nth(i);
    // 先证明「第 i 个可见行就是 rows[i]」——筛选若没回到「全部」，这条先红
    eq((await row.locator("td:nth-child(3)").innerText()).trim(), r.t,
       `第 ${i} 个可见行就是 rows[${i}]（${r.t}）`);
    // ⚠ 点**时刻单元格**、不点整行：整行的几何中心落在「标签」列上，点它会开
    // `el-select` 的下拉，而那个 popper 会盖住下面两行 ⇒ 下一轮 `row.click()` 被它
    // 拦截（Playwright 的 hit-test 一直等到 30s 超时）。这正是本文件 `rowCell(i)`
    // 存在的原因（`rowCell(i)` 定义处那条注释记的是同一件事——行号会漂，认符号不认行号）。
    await row.locator("td").nth(2).click();
    await waitFor(async () => (await page.locator("#app .el-table__row.current-row").count()) === 1
                              && (await clipSrc()) !== prevSrc,
                  `切到 rows[${i}]，切片 URL 换成了这一行的`);
    prevSrc = await clipSrc();
    const u = new URL(prevSrc, BASE);
    ok(!u.href.includes("null"), `rows[${i}]（${r.t}）的切片 URL 不含 null（实际 ${u.href}）`);
    eq(u.searchParams.get("before"), "6",
       `rows[${i}] 拿到的是会话级有效值，不是被那次清空写坏的值`);
    ok(Number.isFinite(Number(u.searchParams.get("after"))),
       `rows[${i}] 的「后秒」仍是有限数值`);
    seenT.add(u.searchParams.get("t"));
  }
  eq(seenT.size, 3, "三行走的是三个**不同**的切片（不是同一个 URL 被读了三次）");

  // K/J/L 与 ←/→：**Task 8 的招牌交付**（播放器键盘控制），必须有断言。
  // ⚠ 夹具里**没有真的视频文件**（见本节末尾「夹具限制」），所以只能钉到
  // 「不抛异常 + 切片 URL 不被这几个键改坏」这一层——真播放/暂停/跳转行为
  // 在本夹具下验不了，别为了让断言好看去假装验了。
  const srcBefore = await clipSrc();
  for (const k of ["KeyK", "KeyJ", "KeyL", "ArrowLeft", "ArrowRight"]) await keys(k);
  ok(!errs.length, "K/J/L/←/→ 没打出 JS 异常");
  eq(await clipSrc(), srcBefore, "这五个键只动播放位置，不改切片 URL");

  // R22（Task 11 Step 1 落地）：`curKey` 必须始终落在 `visibleRows` 里 —— 筛选把
  // 当前行藏起来就**连它一起清**、右栏回空态。原来的断言是反过来的（「筛选藏起当前行
  // 时右栏也不清空」，靠 `curRow` 在**全部** rows[] 里找），而那种分叉正是 R22 要消掉的：
  // 右栏显示着 A，键盘动作却走 `rowByKey`（只看可见行）⇒ 按键静默无反应。
  // 档位筛选是 `el-radio-group`（`全部 / 未勾选 N / 🟢 / 🟡 / ⚪`，
  // 值 all/unchecked/high/review/low），所以点它按 emoji 找按钮。
  const timeCells = async () =>
    (await page.locator("#app .el-table__row td:nth-child(3)").allInnerTexts())
      .map((s) => s.trim());
  const tierEmoji = { high: "🟢", review: "🟡", low: "⚪" };
  const other = ["high", "review", "low"].find((s) => s !== cur.status);
  await page.locator("#app .el-radio-button", { hasText: tierEmoji[other] }).click();
  // 先证明当前行**确实**被藏起来了——不然下面那条断言什么都没验（假绿）
  ok(!(await timeCells()).includes(cur.t),
     `当前行（${cur.t}）已被筛到「${other}」藏起来`);
  ok((await page.locator("#app .right").innerText()).includes("点击左侧表格任意一行开始复核"),
     "筛选把当前行藏起来 ⇒ 当前行一起清、右栏回空态（R22）");
  await page.locator("#app .el-radio-button", { hasText: "全部" }).click();
  await waitFor(async () => (await timeCells()).includes(cur.t), "筛选切回「全部」");

  // ⚠ R22 落地后，这一刻 `curKey` 是**空**的（`watch(visibleRows)` 只做「藏起来就清」、
  // 没有反向），而紧接着的段尾复原要动右栏（`beforeInput`/`afterInput`/`.pctrl` 都在
  // ClipPanel 里，`curRow` 为空时那整块 `v-if` 不渲染 ⇒ `.fill()` 直接 30s 超时，
  // 而且红得与本段代码毫无关系）。**消费方自己声明前置**：把当前行放回第一颗。
  // 这与 Task 9 段首那次复位是**两份**、互不替代（每段自报前置）——别为了「只留一处」
  // 把它删掉或合并：本段的复原就在下面两行。
  await rowCell(0).click();
  await waitFor(async () => (await page.locator("#app .el-table__row.current-row").count()) === 1,
                "段尾复原前把当前行放回 rows[0]（R22 之后筛选会把它清掉）");

  // —— 段尾复原夹具（Task 6/7 同款纪律，别省）——
  // 本段留下了：第 1 行**勾着**（取消反馈不动勾选，见上）、会话级前后秒被
  // 改成 6/2、撤销栈多了 4 条（✅、✅取消、⚠标、⚠取消）。
  await beforeInput.fill(before0);
  await beforeInput.press("Enter");
  await afterInput.fill(after0);
  await afterInput.press("Enter");
  await page.locator("#app .pctrl button", { hasText: "应用到全部" }).click();
  await waitFor(async () => new URL(await clipSrc(), BASE).searchParams.get("before") === before0
                             && new URL(await clipSrc(), BASE).searchParams.get("after") === after0,
                "前后秒已还原成夹具原值");
  await page.locator("#app .el-table__row").first().locator(".el-checkbox").click();
  await waitFor(async () => (await state()).rows.every((r) => !r.selection),
                "第 1 行的勾选已复原（回到全不勾）");
  // 注意：复原那次勾选点击**也会入栈**（走 actions.setSelection）⇒ 本段结束时
  // 栈里还剩 5 条。后续任务不要假设栈是空的（要验撤销就自己先造一步）。
  ok(!errs.length, "右栏段没打出 JS 异常");

  // —— 本节末尾「夹具限制」（Task 8 必读，别绕过去）——
  // `tests/e2e/test_edit_bay_e2e.py` 的 `_fixture` 只在 `work/` 里建事件/标定档，
  // 并在**临时目录根**写一份 `tags.json`（`bbh/app.py` 的 `TAGS_FILE` 是裸文件名、
  // 按 cwd 解析 ⇒ 落在临时目录，与用户仓库根那份无关）。但它**从来不创建
  // `videos/<name>.mp4`**（只把路径字符串写进 `events.json`）。
  // 因此 `GET /media/clip` 每次都走 `bbh/web/media.py` 的
  // `_require_current_video` ⇒ **404「视频不存在」**（还没到 `_cut_preview`，
  // 所以不慢、也不碰 ffmpeg）。
  // 含义：本段所有断言只能落在 **URL / 属性 / DOM** 层——`video.src` 是
  // 「服务端给的地址长什么样」，与那个文件在不在无关，所以断言照样能过；但
  // **播放器真行为**（真播放/暂停、播放头随 `timeupdate` 走、分色比例、点条
  // 跳转、2×、循环、K/J/L 的跳转量）**在本夹具下验不了**。本任务**不扩夹具**
  // （造真 mp4 要引入 ffmpeg 依赖，属于另一件事）——这条限制留给 Task 11 收口
  // 与最终整支复审判断要不要补。

  // —— 四个弹窗（Task 9）——
  // 段末必须把所有弹窗关掉：`modalOpen` 闸门开着的话，后面任务的每个键都失效。
  const dlg = (cls) => page.locator(`.el-dialog.${cls}`);
  const dtitle = async (cls) => (await dlg(cls).locator(".el-dialog__title").innerText()).trim();
  // `⋯` 菜单是 teleport 到 body 的，不带 #app 前缀；菜单项按文案选
  const menu = async (text) => {
    await page.locator("#app .toolbar .el-dropdown button").click();
    await page.locator(".el-dropdown-menu__item").filter({ hasText: text }).first().click();
  };

  // 段首复位当前行（Task 6/7/8 同款纪律：每段自报前置）。本段后面那些
  // `rowCell(0)` 与 `clipSrc()`（`t=2.7`）**都要求当前行是 rows[0]**——右栏
  // 显示的是当前行、切片 URL 的 `t` 也是它的时刻，当前行一变这条就红。
  // Task 8 段结束时正好停在 rows[0]，本段今天是**搭了它的便车**；Task 11 的
  // R22 一落地，Task 8 段里那次「筛到别的档位再切回全部」会把 `curKey` 清空
  // 且**切回「全部」不会找回来**（watcher 只做「藏起来就清」）⇒ 不复位的话，
  // 本段的 `clipSrc()` 会读到不存在的 `#app .videobox video` 而超时，
  // 红的原因与本任务代码毫无关系。
  //
  // 这一击还顺手把焦点收回页面——本段第一件事就是按 `?`，而上一段最后一下是点
  // 第 1 行的 `.el-checkbox`，实测那会把焦点留在 `INPUT.el-checkbox__original`
  // 上（正是键盘闸门①「输入框聚焦一律不触发」要拦的场景）。td 不可聚焦 ⇒
  // 焦点落回页面。两条理由指向同一次点击，故只点一次。
  await rowCell(0).click();
  await waitFor(async () => (await page.locator("#app .el-table__row.current-row").count()) === 1,
                "段首把当前行放回 rows[0]（本段的前提）");

  // ① 帮助窗（`?`）：逐字表 + 闸③（开着时只有 `?` 能用）
  await page.keyboard.press("Shift+Slash");           // Shift+/ = `?`
  await dlg("helpdlg").waitFor();
  eq(await dtitle("helpdlg"), "⌨ 键盘流复核 — 快捷键总表", "帮助窗标题逐字");
  // ⚠ 断**行数**不断 `kbd` 数：`↑/↓` 合成一行后仍然是 9 个 `kbd`，「一行一个键帽」
  // 与「两行两个键帽」在 kbd 计数上不可分（原来那条 `kbd === 10` 抓不到这个形状）。
  eq(await dlg("helpdlg").locator("tbody tr").count(), 9, "键位表 9 行（与原型逐字）");
  ok((await dlg("helpdlg").innerText()).includes("上一球 / 下一球（自动滚动 + 播放器联动）"),
     "↑/↓ 合成一行、括注不重复（原型逐字）");
  const htxt = await dlg("helpdlg").innerText();
  ok(htxt.includes("1=三分") && htxt.includes("3=罚球"), "灰字当前标签库来自 state.tags");
  ok(htxt.includes("正常确认进球用空格即可，不产生反馈数据"), "表下注逐字");
  await page.keyboard.press("KeyX");                  // 闸③：帮助窗开着时 X 不该生效
  await page.waitForTimeout(300);
  ok(!(await state()).verdicts[cur.key], "帮助窗开着时其它键全失效（闸③）");
  await page.keyboard.press("Shift+Slash");
  await dlg("helpdlg").waitFor({ state: "hidden" });

  // ①b 顶栏那颗「⌨ 帮助」按钮（Task 9 的顶栏收尾之一）走同一个 `dlg.help`：
  // `?` 是它的键盘快捷方式，但不是**同一个**入口——只验 `?` 会漏掉按钮那一路的
  // 接线（按钮坏了、`?` 照样绿）。顺手把 footer 的「知道了」也真关一次。
  await page.locator("#app .topbar button").filter({ hasText: "⌨ 帮助" }).click();
  await dlg("helpdlg").waitFor();
  // 按钮开的帮助窗要跟 `?` 开的那颗**行为一致**——包括闸③（开着时别的键全失效）。
  // 原来这里是一句 `ok(true, …)`：恒真、只把项数 +1（复审抓的）。
  await page.keyboard.press("KeyX");
  await page.waitForTimeout(300);
  ok(!(await state()).verdicts[cur.key],
     "按钮开的帮助窗同样启用闸③（不只是 ? 那条路）");
  await dlg("helpdlg").locator(".el-dialog__footer button").click();
  await dlg("helpdlg").waitFor({ state: "hidden" });

  // ② 时刻校准：会话级偏移。规矩是**凡是屏上显示时刻的地方都吃同一个 offset**。
  //    判据是**调用点**而不是名单：`git grep -n 'dispT(\|tsec(' frontend/src`
  //    扫出来的每一处调用都得传当前 offset（别在这里数数字/列名单：一度按名单数过，
  //    每次都漏项、改了别处它就过期）。
  //    **第二条腿（2026-09-20 起闭合）**：不吃 dispT 的文案面——服务端
  //    `payload["msg"]` 里删除/裁决 toast 的时刻与 `actions.js` 的 Ctrl+Z 撤销文案。
  //    删/裁决请求体带 offset（`actions.js` 的 `getOffset`）、服务端按它单独算 msg
  //    显示值、键仍原始；撤销 label 走 `dispT`。仍用原始键的文案面**是有意的**
  //    （键即存储身份，别「顺手统一」）：404/400 回显键、flags/miss 的 msg、墙页 caption。
  //    **集锦切点**不在这个指针里——它不吃 dispT、是**服务端**平移 `row[1]`
  //    （见 `api_gen._apply_session_rows`），改校准语义时别忘了。
  //    **关闭不复原**（本批改动；理由见段尾）。
  eq((await rowCell(0).innerText()).trim(), "00:01.2", "校准前的显示时刻");
  await menu("⏱ 时刻校准");
  await dlg("offdlg").waitFor();
  const offIn = dlg("offdlg").locator(".el-input-number input");
  await offIn.fill("1.5");
  await offIn.press("Enter");
  await waitFor(async () => (await rowCell(0).innerText()).trim() === "00:02.7",
                "偏移 +1.5s：时刻列跟着变（dispT）");
  ok(Math.abs(Number(new URL(await clipSrc(), BASE).searchParams.get("t")) - 2.7) < 1e-9,
     "切片 t 也跟着偏移（tsec 同源：屏上显示时刻的地方都吃同一个 offset）");
  eq((await page.locator("#app .basis .head .t").innerText()).trim(), "00:02.7",
     "右栏卡头的时刻同源（R34：同一行左右栏不能显示两个时刻）");
  // 本批 ⑤（R34 那条的同类漏判）：切片条红线上的 `进球 …` 标签原先用**服务端原串**
  // `row.t` ⇒ 偏移一可达，它就成了这块屏上唯一不动的时刻（表格 00:02.7、卡头
  // 00:02.7、只有它 00:01.2）。这里断它与表格时刻列**逐字相同**——只断「标签变了」
  // 是不够的，那钉不住「同源」。
  eq((await page.locator("#app .tricolor .goalmk").innerText()).trim(),
     `进球 ${(await rowCell(0).innerText()).trim()}`,
     "红线标签上的时刻与表格「时刻」单元格逐字相同（本批 ⑤：都用 dispT(row, offset)）");
  // 清空输入框（R-T9-1）：`Number("")` 与 `Number(null)` 都是 0 ⇒ 不显式挡空串/null
  // 的话这里会**静默把偏移归零**（Task 8 F-1 的同类：坏值不该被当成「设成 0」）。
  // 裁定是「非有限值一律**忽略这次提交**、保留上一个有效值」，所以下面两条断言
  // 都必须**停在 00:02.7**——断言写成 00:01.2 反而会把 bug 当正确行为钉住。
  await offIn.fill("");
  await offIn.press("Enter");
  await page.waitForTimeout(300);
  ok(Math.abs(Number(new URL(await clipSrc(), BASE).searchParams.get("t")) - 2.7) < 1e-9,
     "清空偏移输入框**不改变**偏移（不接受「清空 = 归零」）");
  eq((await rowCell(0).innerText()).trim(), "00:02.7", "时刻列也停在旧值（两处显示不许不一致）");
  // 这条专逮 `v-model="draft"` 那种漏法：门加了、偏移也保住了，但
  // `update:modelValue` 把 `draft` 置成 null、输入框自己变空 ⇒ 屏幕上一处空、
  // 一处还是 2.7。上面两条断不到它（偏移确实没变），非得看输入框不可。
  //
  // ⚠ 期望值是 **1.5**（= 偏移），不是 2.7（= 那颗球的显示时刻）：框里装的是
  // 偏移量（原型 `前端原型/剪辑台.html:398` 的 `v-model="timeOffset"` 同款），
  // 2.7 是从上面三条顺下来的笔误——见 task-9-report.md §0.2。断言的立意
  //（「框里不许变空，且与偏移一致」）没变：`v-model` 那种漏法照样让它红。
  eq(await offIn.inputValue(), "1.5", "输入框自己也不许变空（显示与偏移必须一致）");
  // —— 关闭**不**复原（本批改动；旧行为已废）——
  // 旧行为：footer 那颗「取消」＝复原（`@close` 一律把 offset 打回打开时的值）。
  // 那条语义让整条管线 inert：弹窗是模态的，必须先关才能点「开始生成」⇒ 按下
  // 生成那一刻 offset **恒为 0**。用户 2026-09-19 裁定改成「关闭只是关闭、
  // 要复原按归零」（旧界面 Gradio 的偏移本来就是留在会话里的，复原才是偏离）。
  // 所以这里断的从「关完回到 00:01.2」翻成「关完仍在 00:02.7」。
  await dlg("offdlg").locator(".el-dialog__footer button").filter({ hasText: "关闭" }).click();
  await dlg("offdlg").waitFor({ state: "hidden" });
  eq((await rowCell(0).innerText()).trim(), "00:02.7",
     "关闭弹窗**不复原**（旧行为已废）：时刻仍是校准后的 00:02.7");
  // 顶栏「已校准」指示器（本批 ②）：旧界面那个偏移是**常驻可见的数字框**，而
  // 弹窗一关就看不见了 ⇒ 没有指示器就等于静默丢值。只在 offset ≠ 0 时出现。
  const offTag = page.locator("#app .topbar .el-tag.offind");
  eq((await offTag.innerText()).trim(), "⏱ 已校准 +1.5s",
     "关掉弹窗后顶栏出现「已校准」指示器（值＝偏移，不是球的时刻）");

  // —— 删除**不再有二次确认**（2026-09-25 用户裁定） ——
  // 这里原先是「修复轮 1 ①」那条牙：按 D 弹出确认框、再逐字比对框里的时刻与同屏
  // 表格时刻（`528f6bc` 起那句文案写过 `dispT(row, 0)`，偏移可达后它成了全屏唯一
  // 不动的时刻）。确认框整颗去掉了（`EditBay.deleteCurrent` 现在直接删），那条牙
  // 随之失效。它守的**同源**原则没丢——下面「三处文案时间基」段里「删除 toast 的
  // 时刻 = 校准后显示值」仍守同一把尺子（表格 00:02.7 vs 原始键 00:01.2，双向判）。
  // ⚠ 原地**不再按 D**：现在按下去就是真删，下面接着要用的 `rowCell(0)` 与「12 行」
  // 全不成立。那条「不弹确认框」的弱牙挪到了真正要删的那一段（见下段段首）。

  // —— 修复轮 1 ③：Esc 那条关闭路径同样**不复原** ——
  // ①.1 声称「右上角 × / Esc / 点遮罩 / 底部关闭」四条路一个语义，上面只走了底部
  // 那颗。× 与遮罩走的是同一个 `update:modelValue(false)` 漏斗，这里补一条代表的。
  await offTag.click();
  await dlg("offdlg").waitFor();
  await page.keyboard.press("Escape");
  await dlg("offdlg").waitFor({ state: "hidden" });
  eq((await rowCell(0).innerText()).trim(), "00:02.7",
     "Esc 关窗**也不复原**（四条关闭路径一个语义）");

  // —— 三处文案时间基（2026-09-20 小任务）：删除 toast 的时刻 = 校准后显示值 ——
  // 删一行（真删）：toast 由服务端 msg 出——判据双向：含显示值
  // 00:02.7、不含原始键 00:01.2。删完立刻全量恢复，夹具回 12 行；后续 bulk 段只断
  // 「时刻｜档位」，恢复重置不牵连（后段「③ 批量删除」末尾还有一次全量恢复）。
  // ⚠ 本段有两条**未言明的安全前提**，动它之前先读：
  //   ① 它给撤销栈留了一条 `undo = replay(raw.restore(keys))`，而键随后被「↩ 恢复
  //      已删」清出 deleted.json ⇒ **此刻栈顶若被 Ctrl+Z 触发，服务端会 400**
  //      （「这些球不在已删清单里」）。后面若要按 Ctrl+Z，必须**自己先重建栈顶**
  //      ——文件后段那条「勾选 → 取消」造栈顶就是这么做的，别删它。
  //   ② 删除会级联清勾选/标签/归人，而「恢复已删」**不回滚**这些（`restore_deleted`
  //      docstring 明写「恢复的球回到未勾选/未归人/无标签」）⇒ 本段之所以安全，
  //      是因为此刻第 1 行**无标签、未勾选**（更早的段把两者复位过）。
  const del0 = (await state()).deleted.length;
  await page.keyboard.press("KeyD");
  // 直接删、无二次确认（2026-09-25 用户裁定：删除只记 deleted.json 清单、随时可撤
  // 销/找回，不值得每次多按一下）。⚠ 这条是**弱牙**：`count()` 是同步快照，真有颗
  // 延迟弹出的框它照绿。**真牙是下面那条「删除落盘」的 waitFor**——它不再需要点任何
  // 按钮，谁把确认框加回来、谁就必须先点一下才能落盘，那条 waitFor 必然超时红。
  eq(await page.locator(".el-message-box").count(), 0, "按 D 直接删、不弹二次确认");
  await waitFor(async () => {
    const d = (await state()).deleted;
    return d.length === del0 + 1 && d.includes("00:01.2");
  }, "删除落盘（+1 条，且键是原始时刻串）");
  // 按文案等，不按「有任意成功 toast」：ElMessage 存活 3s，旧 toast 会让上面那种
  // 门提前通过、下面 .last() 读到旧文案。&& 短路保证零 toast 时不去调 innerText
  // （waitFor 的回调抛错是直接失败、不是重试）。
  await waitFor(async () => (await page.locator(".el-message--success").count()) >= 1
    && (await page.locator(".el-message--success").last().innerText())
        .includes("已删除 1 球（00:02.7"), "删除 toast 出现（按文案等）");
  ok((await page.locator(".el-message--success").last().innerText()).trim()
     .includes("已删除 1 球（00:02.7")
     && !(await page.locator(".el-message--success").last().innerText())
       .includes("00:01.2"),
     "删除 toast 的时刻=校准后显示值（服务端 msg 吃 offset，原始键不进文案）");
  await menu("↩ 恢复已删");
  await waitFor(async () => (await state()).rows.length === 12
                && (await state()).deleted.length === 0, "恢复回 12 行（后续段前提）");

  // 复开弹窗 ⇒「归零」⇒ 偏移真回到 0。
  // ⚠ 本段之后十几条断言都以「偏移 0 ⇒ 表格时刻 = 原始检测时刻」为前提，
  // 所以「归零」必须真的落到 `offset` 上，只清 draft 是不行的。
  await offTag.click();
  await dlg("offdlg").waitFor();
  await dlg("offdlg").locator(".el-dialog__footer button").filter({ hasText: "归零" }).click();
  await dlg("offdlg").locator(".el-dialog__footer button").filter({ hasText: "关闭" }).click();
  await dlg("offdlg").waitFor({ state: "hidden" });
  eq((await rowCell(0).innerText()).trim(), "00:01.2", "「归零」把偏移打回 0（时刻回原始值）");
  eq(await page.locator("#app .topbar .el-tag.offind").count(), 0,
     "归零后指示器消失（offset === 0 才渲染）");

  // ③ 批量删除：弹窗本身即确认；删完用 ⋯ 恢复，把夹具还原成 12 行
  await menu("🗑 批量删除");
  await dlg("bulkdlg").waitFor();
  const brows = dlg("bulkdlg").locator(".el-checkbox");
  eq(await brows.count(), 12, "清单 = 未删行数");
  ok((await brows.first().innerText()).includes("00:01.2｜🟢"), "行文案 = 时刻｜档位");
  const dbtn = dlg("bulkdlg").locator(".el-dialog__footer button")
    .filter({ hasText: "确认删除所选" });
  ok(await dbtn.isDisabled(), "没勾时删除按钮禁用");
  await brows.nth(0).click();
  await brows.nth(1).click();
  eq((await dbtn.innerText()).trim(), "🗑 确认删除所选（2）", "按钮文案带计数");
  await dbtn.click();
  await dlg("bulkdlg").waitFor({ state: "hidden" });
  await waitFor(async () => (await state()).deleted.length === 2, "批量删除落盘");
  eq((await state()).rows.length, 10, "表格少 2 行");
  await waitFor(async () => (await page.locator("#app .tblfoot").innerText())
                .includes("另有 2 条已删可恢复"), "页脚已删计数跟着变");
  await menu("↩ 恢复已删");
  await waitFor(async () => (await state()).rows.length === 12, "恢复回 12 行");

  // ④ 生成弹窗：真 choices（不桩）+ 桩住 job 走完 选择→进度→结果
  // 真渲染是分钟级、依赖 ffmpeg，属 Plan E 真机验收；这里钉的是**请求形状**
  // 与三个视图分支（进度文案取服务端 msg、结果清单、url 为 null 的降级）。
  //
  // —— A-1/A-2 前置：造一份「用户设过值」的会话级行状态 ——
  // 生成请求必须把前后秒与单行改动带上（旧界面靠 `rows_state` 带过去的就是它们），
  // 否则服务端自己 `load_events_table` 重算 ⇒ 成品按常量默认 5/2 切、校准后成品
  // 不移，且没有任何提示。**必须在开弹窗前做**：弹窗是模态的，弹窗开着时点不到
  // 右栏那两个输入框（overlay 会拦住 hit-test）。
  // ① 给当前行一个落点：③ 段删过第 0/1 行，`watch(visibleRows)` 把 curKey 清了。
  // ⚠ **不假设这一点能落回 `rows[0]`**：el-table 的 `current-change` 只在当前行真的
  // 变了时才发，而 ③ 之前若当前行是**没被删**的那一行（实测有一次停在 00:02.0），
  // 这一点可能什么也不改。所以下面单行改动的键按**当前行**取，不按 `rows[0]` 取。
  await rowCell(0).click();
  await waitFor(async () => (await page.locator("#app .el-table__row.current-row").count()) === 1,
                "生成段前置：表格有唯一当前行（单行改动要有落点）");
  const gBefore = page.locator("#app .pctrl .el-input-number input").first();
  const gAfter = page.locator("#app .pctrl .el-input-number input").nth(1);
  await gBefore.fill("8");
  await gBefore.press("Enter");
  await gAfter.fill("3");
  await gAfter.press("Enter");
  await page.locator("#app .pctrl button", { hasText: "应用到全部" }).click();
  ok(await toast("已应用到全部：前 8s / 后 3s"), "把会话级前后秒设成 8/3");
  // ② 单行覆盖全局：当前行改成「前 9」（后仍是 3）⇒ 请求体里 durs 只该有它一条
  await gBefore.fill("9");
  await gBefore.press("Enter");
  await waitFor(async () => new URL(await clipSrc(), BASE).searchParams.get("before") === "9",
                "单行改动生效（当前行的切片按 9 切）");
  // 键按**当前行**取，不按 `rows[0]` 取（理由见上面 ①）：两次跑里当前行一次是
  // 00:01.2、一次停在 00:02.0（那次 `rows[0]` 仍是 00:01.2，按 rows[0] 写就红）。
  // 键 = events.json 原始时刻串，而表格时刻列此刻偏移为 0（校准段落尾已复原）
  // ⇒ 用当前行的时刻文案回查 state 里的 key。
  const curTxt0 = (await page.locator("#app .el-table__row.current-row td").nth(2)
    .innerText()).trim();
  const gRows0 = (await state()).rows;
  const genKey0 = (gRows0.find((r) => r.t === curTxt0) || {}).key;

  await page.locator("#app .topbar button").filter({ hasText: "🎬 生成" }).click();
  await dlg("gendlg").waitFor();
  // 2026-10-21 入口合并：横/竖两个按钮并成一个「生成」，画幅与输出方式都
  // 收进弹窗、且**都不预选**（用户裁定「强制必选」）。所以打开时标题是中性
  // 串——这一步先钉住"没有默认值"，再选横屏，标题才成为横屏集锦。
  eq(await dtitle("gendlg"), "生成视频（先选画幅与输出方式）",
     "打开时两维都没选（无默认）");
  const genOk = () => dlg("gendlg").locator(".el-dialog__footer button")
    .filter({ hasText: "开始生成" });
  ok(await genOk().isDisabled(), "画幅/输出/勾选没齐 → 「开始生成」不可点");
  await dlg("gendlg").locator(".el-radio-button").filter({ hasText: "横屏直切" }).click();
  // 两维都必选 ⇒ 只选画幅时标题**仍然中性**（这是设计，不是 bug）：标题由
  // 画幅×输出组合决定，缺一维就没有确定的产物形态可命名。
  await page.waitForTimeout(150);
  eq(await dtitle("gendlg"), "生成视频（先选画幅与输出方式）",
     "只选了画幅 → 标题仍中性（还缺输出方式）");
  ok(await genOk().isDisabled(), "只选了画幅、输出方式还没选 → 仍不可点");
  await dlg("gendlg").locator(".el-radio-button").filter({ hasText: "一条集锦" }).click();
  await waitFor(async () => (await dtitle("gendlg")) === "🎬 生成横屏集锦（直切）",
                "横屏标题逐字（两维齐了才有确定标题）");
  await waitFor(async () => (await dlg("gendlg").locator(".el-alert__title").first()
    .innerText()).includes("按勾选时刻直接切片拼装"), "横屏说明逐字");
  const gbox = dlg("gendlg").locator(".gpick .el-checkbox");
  await gbox.first().waitFor();
  // ⚠ 是 **5** 而不是 4：夹具种的是 3 个标签（三分/上篮/罚球），但**本 spec 自己**
  // 在前面（Task 6/7 的「标签列 allow-create」段，本文件 127 行）往全局标签库里
  // 添了 `reviewtag9` 并明确「库里这条保留」（后端没有删标签端点）⇒ 这一刻
  // choices = [合集] + 4 个标签。写 4 等于假设「本 spec 没添过标签」，必红。
  eq(await gbox.count(), 5, "合集 + 标签库全部（服务端 choices）");
  // stats 随画幅变化异步重拉 ⇒ 必须等回来再断（否则满载时偶发红）
  await waitFor(async () => (await dlg("gendlg").innerText()).includes("🎬 横屏直切 ｜ 📊"),
                "横屏 stats 行（等 /api/gen/choices 回来）");
  ok(await gbox.first().locator("input").isChecked(), "默认勾合集");
  ok(!(await gbox.nth(1).locator("input").isChecked()), "默认不勾标签");

  // —— 2026-11-01：输出方式第三项「⚡ 25s 精选（4s 钩子）」，**横竖都支持** ——
  // 要钉住的三件事：
  // ① 画幅**不再锁死**（同日从"恒横屏"放开）：横=ffmpeg 从源片直切，竖=走
  //    vrender 跟球运镜且不慢放，两条实现都齐了，再锁反而多一道无谓改动；
  // ② 说明**随画幅换**：竖版必须写"跟球运镜 + 进球不慢放"，不能还留着横版
  //    的"直切、8 Mbps"——那会让人以为竖版也是直切出来的；
  // ③ 合集项文案沿用 reel 那一套（hook4 与 reel 都是"合成一条"的语义）。
  //    服务端若给 hook4 新增第三串，`_ALL_ITEMS` 少登记一个就会被当标签名筛
  //    ⇒ 0 行 ⇒ 静默不出片，所以这条断言的价值在"没有第三串"本身。
  const oH = dlg("gendlg").locator(".el-radio-button").filter({ hasText: "横屏直切" });
  const oV = dlg("gendlg").locator(".el-radio-button").filter({ hasText: "竖屏运镜" });
  await dlg("gendlg").locator(".el-radio-button").filter({ hasText: "25s 精选" }).click();
  await waitFor(async () => (await dtitle("gendlg")).includes("25s 精选"),
                "hook4 标题带模式名");
  ok(!(await oH.locator("input").isDisabled()) && !(await oV.locator("input").isDisabled()),
     "hook4 → 画幅两项都可选（横竖都支持，不再锁横屏）");
  await waitFor(async () => (await dlg("gendlg").locator(".el-alert__title").first()
    .innerText()).includes("每 3 球一条"), "hook4 说明逐字（含每 3 球一条）");
  eq((await gbox.first().innerText()).trim(),
     "📦 视频合集（全部勾选的进球）", "hook4 的合集项文案 = reel 那套（不新增串）");
  ok(!(await genOk().isDisabled()), "hook4 → 可点开始生成");
  // 切竖版：说明换串 + 标题带画幅（这条是竖版唯一能被用户看见的口径）
  await oV.click();
  await waitFor(async () => (await dlg("gendlg").locator(".el-alert__title").nth(1)
    .innerText()).includes("跟球运镜"), "hook4 竖版说明含跟球运镜");
  ok((await dlg("gendlg").locator(".el-alert__title").nth(1).innerText())
    .includes("不慢放"), "hook4 竖版说明写明进球不慢放");
  await waitFor(async () => (await dtitle("gendlg")).includes("竖屏"),
                "hook4 竖版标题带画幅");
  // 切回集锦 → 标题复位（此刻画幅是竖屏，复位成"生成竖屏集锦"）
  await dlg("gendlg").locator(".el-radio-button").filter({ hasText: "一条集锦" }).click();
  await waitFor(async () => (await dtitle("gendlg")) === "📱 生成竖屏集锦（跟球运镜）",
                "切回集锦后标题复位");
  await dlg("gendlg").locator(".el-dialog__footer button").filter({ hasText: "关闭" }).click();
  await dlg("gendlg").waitFor({ state: "hidden" });

  // —— 本批改动：让「非 0 偏移 + 点生成」这条路径**今天真能走到** ——
  // 旧行为下它不可达（弹窗模态 + 关闭即复原 ⇒ 生成那一刻 offset 恒为 0），所以
  // 原先只在 `genBody.offset` 上断「字段带上了、类型是数字」。现在关闭不复原，
  // 于是这里**先设 1.5 再开生成弹窗**，并顺手用表格时刻证明它此刻真的在生效
  //（只写进 body 不算数）。
  // ⚠ 位置只能在这一段：上面 `genKey0` 是拿**当前行显示时刻**回查
  // `state.rows[].key` 的，而那个键是**原始时刻串**（与偏移无关，服务端也是先
  // 读键再平移）—— 先设偏移再去查就查不到了。
  await menu("⏱ 时刻校准");
  await dlg("offdlg").waitFor();
  const offInGen = dlg("offdlg").locator(".el-input-number input");
  await offInGen.fill("1.5");
  await offInGen.press("Enter");
  await dlg("offdlg").locator(".el-dialog__footer button").filter({ hasText: "关闭" }).click();
  await dlg("offdlg").waitFor({ state: "hidden" });
  eq((await rowCell(0).innerText()).trim(), "00:02.7",
     "开生成弹窗前：非 0 偏移（+1.5s）此刻真的在生效（不是只写进请求体）");

  await page.locator("#app .topbar button").filter({ hasText: "🎬 生成" }).click();
  await dlg("gendlg").waitFor();
  await dlg("gendlg").locator(".el-radio-button").filter({ hasText: "竖屏运镜" }).click();
  await dlg("gendlg").locator(".el-radio-button").filter({ hasText: "一条集锦" }).click();
  await waitFor(async () => (await dtitle("gendlg")) === "📱 生成竖屏集锦（跟球运镜）",
                "竖屏标题逐字");
  eq(await dtitle("gendlg"), "📱 生成竖屏集锦（跟球运镜）",
     "竖屏标题逐字（两维齐了才有确定标题）");
  await waitFor(async () => (await dlg("gendlg").locator(".el-alert__title").first()
    .innerText()).includes("片头钩子"),
                "竖屏说明逐字（含片头钩子与只勾 1 球的例外）");
  // ⚠ 这行 stats 是**弹窗打开之后异步取**的（`GenDialog.vue` 的
  // `watch(modelValue)` → `GET /api/gen/choices` → `stats.value`）⇒ 必须等它回来
  // 再断。原先紧跟「弹窗可见」就 `ok()` 读 innerText，满载时那条 fetch 还没回来
  // 就断 ⇒ **偶发红**（2026-09-25 复现：四条 wrapper 连跑时红在第 969 行，单跑
  // bay 必过，第二次同形态）。上面那条 `.el-alert__title` 不用等——它的 `title`
  // 是 `computed(props.vertical)` 的静态串，不来自这条 fetch。
  // 处置与该文件「删除 toast」段同款：先 `waitFor` 落地，再逐字判。
  await waitFor(async () => (await dlg("gendlg").innerText()).includes("📱 竖屏运镜 ｜ 📊"),
                "竖屏 stats 行（等 /api/gen/choices 回来）");
  ok((await dlg("gendlg").innerText()).includes("📱 竖屏运镜 ｜ 📊"), "竖屏 stats 行");

  // —— park #9：键盘闸②（弹窗开着 → 所有键失效）——
  // 这条规则是本计划四次盘点出来的（原型 bug 注释：「原先会穿透到背后表格——空格
  // 偷改勾选、1-9 打标、X 写误判反馈、D 删行」），回归就是**静默改数据**，可此前
  // **一条断言都没有**。用空格当探针（它改的是勾选，最容易看）。
  //
  // ⚠ 先解决焦点：el-dialog 打开时 `focusStartEl` 默认 `"first"` ⇒
  // `focusFirstDescendant` 把焦点塞给**头部那枚关闭按钮**（`<button>` 的
  // `tabIndex` 天然是 0）。在聚焦的 button 上按空格 = **点它**、弹窗会被关掉
  // ⇒ 那验的就不是闸②了。点一下弹窗头部（不可聚焦的 `<header>`；`.el-dialog`
  // 自带 `tabindex="-1"`，焦点会落到它身上、仍是「在弹窗内部」，focus-trap 的
  // `onFocusIn` 因此不会把焦点抢回按钮）把焦点挪开，并断言挪成功了。
  await dlg("gendlg").locator(".el-dialog__header").click();
  const af = await page.evaluate(() => document.activeElement && document.activeElement.tagName);
  ok(af !== "BUTTON" && af !== "INPUT",
     `空格之前焦点不在按钮/输入框上（实际 ${af}——否则空格是「点按钮」不是「按键」）`);
  const selBefore = (await state()).rows.filter((r) => r.selection).length;
  eq(selBefore, 0, "前置：此刻全不勾（下面才看得出空格有没有偷改）");
  await page.keyboard.press("Space");
  await page.waitForTimeout(400);
  ok(await dlg("gendlg").isVisible(), "弹窗没被空格关掉（说明那一下没落在按钮上）");
  eq((await state()).rows.filter((r) => r.selection).length, 0,
     "空格没穿透到背后表格改勾选（park #9：闸②）");
  ok((await page.locator("#app .tblfoot").innerText()).includes("勾选 0 条进集锦"),
     "页脚勾选数也没变");

  let genBody = null, genPoll = 0;
  await page.route("**/api/gen", async (route) => {
    genBody = route.request().postDataJSON();
    await route.fulfill({ status: 200, contentType: "application/json",
                          body: JSON.stringify({ jobId: "e2e" }) });
  });
  await page.route("**/api/jobs/e2e", async (route) => {
    genPoll += 1;
    const running = genPoll <= 3;      // 跑够 3 轮（~2.4s）让进度视图断言得稳
    await route.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify(running
        ? { id: "e2e", kind: "gen", state: "running", pct: 40, found: null,
            msg: "⏳ 正在生成（1/1）：视频合集（全部勾选的进球）…",
            error: null, done: false }
        : { id: "e2e", kind: "gen", state: "done", pct: 100, found: null,
            msg: "✅ 完成", error: null, done: true }) });
  });
  await page.route("**/api/jobs/e2e/result", async (route) => {
    await route.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify({
        id: "e2e", kind: "gen", vertical: true,
        summaryMd: "📊 9月19日进球统计：总进球 1，其他 1 个\n\n"
          + "✅ 已生成 1 个视频（预览位播放第一个，全部在 work/<视频名>/ 下）：\n"
          + "· 视频合集（全部勾选的进球） → highlight.mp4\n"
          + "· 上篮：没有勾选的进球（跳过）",
        videos: [
          { item: "视频合集（全部勾选的进球）", name: "highlight.mp4",
            url: "/media/work?path=x&v=1" },
          { item: "上篮", name: "highlight-上篮.mp4", url: null },
        ],
        firstUrl: "/media/work?path=x&v=1" }) });
  });

  await dlg("gendlg").locator(".el-dialog__footer button")
    .filter({ hasText: "开始生成" }).click();
  await waitFor(async () => (await dlg("gendlg").innerText()).includes("正在生成（1/1）"),
                "进度文案取服务端 job.msg（R10）");
  eq(genBody.vertical, true, "POST /api/gen 的 vertical 透传");
  eq(genBody.items.length, 1, "items 是数组（不是裸字符串——后端会按字符迭代）");
  eq(genBody.items[0], "视频合集（全部勾选的进球）",
     "合集用服务端的 GEN_ALL_LABEL 原串（不是 __all__）");
  // A-1/A-2：**追加**的断言（上面三条一个字没动）——会话级行状态必须跟着请求走。
  // 不带的话服务端自己重算行 ⇒ row[4]/row[5] 恒为常量默认 5/2、row[1]（切点）
  // 恒为未校准时刻，而 `generate_highlight` 真的拿这三列去切（`bbh/app.py:1992`）
  // ⇒ 预览按你设的切、成品按 5/2 切，没有任何提示。
  eq(genBody.before, 8, "「应用到全部」的前秒进请求体");
  eq(genBody.after, 3, "「应用到全部」的后秒进请求体");
  ok(genKey0 !== undefined, `当前行时刻 ${curTxt0} 能在 state.rows 里查到键`);
  eq(JSON.stringify(genBody.durs), JSON.stringify({ [genKey0]: [9, 3] }),
     `单行改动按原始时刻串当键（与后端的行键同源）——只有当前行有覆盖`
     + `（当前行 ${curTxt0} / 键 ${genKey0}，rows[0] ${gRows0[0] && gRows0[0].key}）`);
  // ⚠ 旧注释整段作废（本批改动）：它说的是「offset 此刻恒为 0、这条路径在真机
  // 界面上不可达」——那是「关闭即复原」时代的结论。现在关闭不复原，上面已经把
  // 偏移设成 1.5（并用表格时刻证明它在生效），所以这条断的是**真值 1.5**：
  // 非 0 偏移**可达且真的随请求带走**。
  // 偏移**在服务端真的会平移切点**（`_apply_session_rows` 只动 row[1]）的数值
  // 验证仍在 `tests/test_web_jobs_api.py::test_gen_applies_session_durs_and_offset_to_rows`
  // （那里传 1.5，断言 row[1] 变成 00:13.6）——e2e 这层到「请求体里真的是 1.5」为止。
  ok(Number.isFinite(genBody.offset) && genBody.offset === 1.5,
     `非 0 偏移随请求带走（实际 ${JSON.stringify(genBody.offset)}）`);
  ok(await page.evaluate(() => localStorage.getItem("bbh.runningJob")),
     "跑中把 jobId 记进 localStorage（刷新后能接上进度）");

  await waitFor(async () => (await dlg("gendlg").innerText()).includes("生成完成"),
                "终态出结果视图", 15000);
  const gtxt = await dlg("gendlg").innerText();
  ok(gtxt.includes("highlight.mp4"), "成品清单从 videos[] 渲染");
  ok(gtxt.includes("（跳过）"), "summaryMd 逐字呈现（跳过行只有那里有）");
  ok(gtxt.includes("文件已不在"), "url 为 null 的成品降级成灰字（不假装可播）");
  eq(await dlg("gendlg").locator('a[href^="/media/work"]').count(), 1,
     "只有真能播的那条给预览链接");
  ok(!(await page.evaluate(() => localStorage.getItem("bbh.runningJob"))),
     "终态清掉记忆（不再续接）");
  await page.unroute("**/api/gen");
  await page.unroute("**/api/jobs/e2e");
  await page.unroute("**/api/jobs/e2e/result");
  await dlg("gendlg").locator(".el-dialog__footer button").filter({ hasText: "关闭" }).click();
  await dlg("gendlg").waitFor({ state: "hidden" });
  eq(await page.locator(".el-dialog:visible").count(), 0, "四个弹窗全部关上（否则后面所有键失效）");

  // —— 生成跑完立刻把偏移归零（本批改动）——
  // 后面十几条断言（长任务段 / 续接段 / 失败路径段）都以「表格时刻 = 未校准的
  // 检测时刻」为前提，别把一份被校准过的会话留给它们。走顶栏指示器（这时它一定
  // 在：offset 还是 1.5），顺带把那条路再验一次。
  // ⚠ 带偏移的这次生成**不会**弄红上面那条 `durs` 断言：`durs` 的键是
  // `state.rows[].key`（= events.json 的原始时刻串，与显示偏移无关），服务端也是
  // **先读键、再平移**（`_apply_session_rows`）。
  await page.locator("#app .topbar .el-tag.offind").click();
  await dlg("offdlg").waitFor();
  await dlg("offdlg").locator(".el-dialog__footer button").filter({ hasText: "归零" }).click();
  await dlg("offdlg").locator(".el-dialog__footer button").filter({ hasText: "关闭" }).click();
  await dlg("offdlg").waitFor({ state: "hidden" });
  eq((await rowCell(0).innerText()).trim(), "00:01.2",
     "生成段收尾：偏移归零、表格时刻回原始检测时刻");
  // 段尾复原（本段留下的会话级前后秒是 8/3 + rows[0] 单行 9）：
  // 后面几段与「夹具原值 5/2」的假设无关，但每段**自报前置、自己收尾**是本
  // spec 的纪律——别把一份被改过的会话级默认留给下一段（Task 8 段尾就是为这
  // 个原因把 durAll 还回去的）。
  await gBefore.fill(before0);
  await gBefore.press("Enter");
  await gAfter.fill(after0);
  await gAfter.press("Enter");
  await page.locator("#app .pctrl button", { hasText: "应用到全部" }).click();
  await waitFor(async () => new URL(await clipSrc(), BASE).searchParams.get("before") === before0
                             && new URL(await clipSrc(), BASE).searchParams.get("after") === after0,
                "生成段的会话级前后秒已还原成夹具原值");
  ok(!errs.length, "弹窗段没打出 JS 异常");

  // —— 三个长任务 + 检测期锁表（Task 10）——
  // 真检测是分钟级且会重写夹具的 events.json ⇒ 桩住 /api/detect 与 job 端点，
  // 只验 UI 契约（按钮态 / 状态带 / 锁表 / 记忆 / 终态整份替换）。真跑留给 Plan E。
  const snap = await state();
  const locked = () => page.locator("#app .main.locked").count();
  const btn = (t) => page.locator("#app .topbar button").filter({ hasText: t }).first();

  let detBody = "unset", detPoll = 0, detStarts = 0;
  await page.route("**/api/detect", async (route) => {
    detStarts += 1;                       // 计数：续接**不该**再起一个 job
    detBody = route.request().postData();
    await route.fulfill({ status: 200, contentType: "application/json",
                          body: JSON.stringify({ jobId: "det1" }) });
  });
  // ⚠ 桩的 running 窗口是**墙钟时间**（`jobs.js` 的 POLL_MS=800 × 轮数）。
  // 窗口内要跑完「一长串断言 + 空格 + Ctrl+Z + 一次 reload 续接」，改成 8 轮
  // ≈ 6.5s（复审实测：窗口内要干的活约 480ms/轮 ⇒ 余量从 ~5× 提到 ~13×）。
  // **窗口一关，症状极具误导性**：job 到终态 ⇒ `jobMsg` 变空 ⇒ `v-if` 把状态带
  // 卸载 ⇒ 下面「状态带显示服务端的 msg」那句 `innerText()` 会**自动等元素出现**、
  // 抛 30s 的 locator TimeoutError，读起来像「状态带根本没渲染」这种真 bug。
  // 看到那条红先查窗口，别去查 `write()`。红线：只许调这里的轮数，
  // **不许改产品代码去迁就测试**。
  await page.route("**/api/jobs/det1", async (route) => {
    detPoll += 1;
    const running = detPoll <= 8;
    await route.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify(running
        ? { id: "det1", kind: "detect", state: "running", pct: 40, found: null,
            msg: "⏳ 检测中… 72/180 帧 (40%)", error: null, done: false }
        : { id: "det1", kind: "detect", state: "done", pct: 100, found: null,
            msg: "✅ 完成：🟢 高置信 12 个", error: null, done: true }) });
  });
  await page.route("**/api/jobs/det1/result", async (route) => {
    // 结果 = **平铺的 state_payload**（契约 §1）：改一处勾选，验「整份替换」
    const rowsNext = snap.rows.map((r, i) => (i === 0 ? { ...r, selection: true } : r));
    await route.fulfill({ status: 200, contentType: "application/json",
      body: JSON.stringify({ ...snap, id: "det1", kind: "detect", rows: rowsNext,
                             msg: "✅ 完成：🟢 高置信 12 个" }) });
  });

  // 撤销栈的栈顶先造一条**「加上」类**动作（勾选 → 取消，净变化为零）：它的
  // undo 会**重新勾上**第一颗 ⇒ blocker A 真逃逸时服务端会被写出 `selection: true`，
  // 下面那条「服务端也没写」才有牙。不造的话栈顶可能是「恢复」类动作——它的 undo
  // 被服务端 400 挡掉，旁路逃逸也**看不出来**（交付版 spec 之所以绿就是纯侥幸）。
  // ⚠ 顺序必须在点「开始检测」**之前**：点完复选框焦点留在原生 input 上
  // （本文件前面那条键盘闸①「输入框聚焦」的坑），而按钮点击会把焦点挪走
  // ——颠倒了 Ctrl+Z 根本触发不了热键，两点都变成假绿。
  const row0box = page.locator("#app .el-table__row").first().locator(".el-checkbox");
  // ⚠ 这两下等的是**页脚渲染态**，不是服务端真值（Task 11 item 8，定向复审报的唯一
  // Minor）：撤销闭包里的 `before` 是 `actions.js` 从**客户端 store**（`setSelection`
  // 里的 `rowOf(key)?.selection`）取的，而 store 要等 PATCH 回包才更新 ⇒ 只等服务端
  // `rows[0].selection` 的话，第二次点击可能推入 `before = false`，于是逃逸的 undo
  // 变成重发 `selection: false`（空操作）⇒ 下面那条承重的「服务端也没写」**假绿**，
  // 红会落到它后面那条 toast 断言上。
  // 页脚是 `state.rows` 派生的 ⇒ 它变了就等于「这次改动已经落进 store」，比等服务端
  // 真值**更强**（store 更新本来就在回包之后）。判据用**变化**、不硬编码条数：
  // 本段起点是不是净未勾选，由上一段决定。
  const footTxt = async () =>
    (await page.locator("#app .tblfoot").innerText()).replace(/\s+/g, " ");
  const foot0 = await footTxt();
  await row0box.click();
  await waitFor(async () => (await footTxt()) !== foot0,
                "造栈顶：先勾上第一颗（页脚渲染态跟着变 ⇒ 已落进 store）");
  await row0box.click();
  await waitFor(async () => (await footTxt()) === foot0,
                "造栈顶：再取消回来（页脚回到点之前的值；净变化为零，服务端回到全未勾选）");

  await btn("开始检测").click();
  // 先 count() 再 innerText()：元素还没渲染出来时 innerText() 是**抛异常**的
  // （waitFor 里的异常直接冒出去，不会继续轮询）——count() 不抛，能等到它出现
  await waitFor(async () => {
    const b = page.locator("#app .topbar button").filter({ hasText: "检测中" });
    return (await b.count()) > 0 && (await b.first().innerText()).includes("40%");
  }, "检测按钮显示进度");
  eq(detBody, null, "POST /api/detect 没有 body（后端不吃参数）");
  ok((await page.locator("#app .jobmsg").innerText()).includes("72/180 帧"),
     "状态带显示服务端的 msg（R31）");
  eq(await locked(), 1, "检测中整块锁上（R3）");
  ok(await btn("🎬 生成").isDisabled(), "检测中生成入口也禁用");
  ok(await btn("🧍 识别出手人").isDisabled(), "检测中回扫入口也禁用");
  const mem = await page.evaluate(() => localStorage.getItem("bbh.runningJob"));
  ok(mem && JSON.parse(mem).kind === "detect", "记忆里记的是 detect");
  eq(JSON.parse(mem).videoPath, snap.videoPath, "连视频一起记（R3：视频不一致就不续接）");

  // 键盘路径被 write() 闸住（鼠标路径被 .locked 的 pointer-events 拦掉）
  await page.keyboard.press("Space");
  await page.waitForTimeout(300);
  ok(await toast("检测中——表格已锁"), "键盘写操作被拦并给出原因");
  ok((await state()).rows.every((r) => !r.selection),
     "被拦下的写操作**服务端也没写**（不只是界面没动）");

  // Ctrl+Z 是**第二条**键盘写入口（`undoStep` → `act.undo()` → `replay` → `raw.*`，
  // 一被调用就发请求）——它不走 `write()` 的话检测期照样真写服务端，还弹
  // 「已撤销」成功文案（blocker A，复审实测复现）。
  // ⚠ 两次被拦的提示文案**是同一句**（`write()` 里那段 warning 是固定文案），而
  // `toast(t)` 是「按文字过滤 + `.first()`」，会匹配上空格那次**还没消失**的那条
  // ⇒ 直接 `toast("检测中——表格已锁")` 判第二次是**假绿**。这里按**条数增加**判
  // 「新的一条出现了」，承重的两条放在「仍在窗口内」与「服务端仍全未勾选」上。
  const nMsg = () => page.locator(".el-message").count();
  const nMsgs = await nMsg();
  await page.keyboard.press("Control+KeyZ");
  await waitFor(async () => (await nMsg()) > nMsgs, "撤销被拦下也弹了提示（新的一条）");
  // 先判「这一刻还在跑」：窗口一旦关了，Ctrl+Z 会**真的执行**（栈顶的 undo 重新
  // 勾上第一颗）⇒ 下面两条一起红，而根因是机器慢、不是 blocker A 又漏了。
  ok(!!(await page.evaluate(() => localStorage.getItem("bbh.runningJob"))),
     "按 Ctrl+Z 的这一刻检测仍在跑（下面两条是在 running 窗口里判的）");
  // 承重的一条放前面：逃逸时先报「服务端被写了」，而不是先报一句可能被
  // 当成「只是少了个 toast」的文案问题。
  ok((await state()).rows.every((r) => !r.selection),
     "被拦下的撤销**服务端也没写**（栈顶的 undo 会重新勾上第一颗，写了这里就红）");
  ok((await page.locator(".el-message").filter({ hasText: "已撤销" }).count()) === 0,
     "也没弹「已撤销」成功文案（闸在动作之前，不是先写进去再报成功）");

  // 同理：跑完前「开始检测」这颗按钮根本不存在，直接 innerText() 会抛
  await waitFor(async () => {
    const b = page.locator("#app .topbar button").filter({ hasText: "检测中" });
    return (await b.count()) === 0
        && (await btn("开始检测").count()) > 0
        && (await btn("开始检测").first().innerText()).includes("开始检测");
  }, "终态按钮复位", 15000);
  ok(await toast("🟢 高置信 12 个"), "完成提示用服务端 msg");
  eq(await locked(), 0, "跑完自动解锁");
  ok(!(await page.evaluate(() => localStorage.getItem("bbh.runningJob"))), "终态清记忆");
  await waitFor(async () => (await page.locator("#app .tblfoot").innerText())
                .includes("勾选 1 条进集锦"),
                "检测结果整份替换 state（页脚跟着桩数据变）");

  // —— 刷新续接（R3）：重开页面接上进度、跑完，结果必须喂回界面（blocker B）——
  // ⚠ 位置是承重的：必须在下面那两条 `unroute` **之前**——它们一执行，
  // `**/api/jobs/det1` 与 `/result` 的桩就没了，之后再造窗口无桩可打。
  // 此刻页脚是桩结果的「勾选 1 条」，而**服务端真值仍是「勾选 0 条」**（桩结果
  // 从不落盘）⇒ reload 后页面回到 0 条，续接跑完若能喂结果就**必须再变回 1 条**。
  // 这条差分正是 blocker B 的签名：不修则永远停在「勾选 0 条」，且一次提示都没有。
  detPoll = 0;                          // 桩计数器复位：再给一个 running 窗口
  await btn("开始检测").click();
  // ⚠ 等的是**第一轮轮询落地**（按钮出现「40%」），不是「按钮变成检测中」：
  // `longJob` 先把 `slot.phase = "running"` 再 `await startJob(...)` ⇒ 点下去的那
  // 一刻按钮就已经是「检测中」了，而 `jobMem.remember(...)` 要等 POST /api/detect
  // 回来才写。只等按钮的话，reload 可能赶在 remember 之前发生 ⇒ 记忆是空的、
  // 根本没有东西可续接，下面那条「锁表恢复」会超时（实测踩过一次）。
  await waitFor(async () => {
    const b = page.locator("#app .topbar button").filter({ hasText: "检测中" });
    return (await b.count()) > 0 && (await b.first().innerText()).includes("40%");
  }, "续接前先让第一轮轮询落地（记忆已写进去，才有东西可续接）");
  const startsBeforeReload = detStarts;   // 重开页面之后这个数**不许再涨**
  await page.reload({ waitUntil: "domcontentloaded" });
  // 重开后靠 jobMem 接上：锁表必须恢复（复审探针这一刻量到 band=1 locked=1）
  await waitFor(async () => (await locked()) === 1, "重开页面接上进度、锁表恢复（R3）");
  eq(detStarts, startsBeforeReload,
     "重开页面是**接上**进度，不是又 POST 一个 job（同类任务重起会 409）");
  ok((await page.locator("#app .topbar button").filter({ hasText: "检测中" }).count()) > 0,
     "重开后按钮显示的是接上的进度");
  await waitFor(async () => {
    const b = page.locator("#app .topbar button").filter({ hasText: "检测中" });
    return (await b.count()) === 0 && (await btn("开始检测").count()) > 0;
  }, "续接跑完按钮复位", 20000);
  // 承重的一条放前面：不修则页脚**永远停在「勾选 0 条」**，这正是 blocker B
  // 的签名（复审探针原文）；先报它，别让一条「没弹完成提示」抢在前面、
  // 让人误以为只是少了个提示。
  await waitFor(async () => (await page.locator("#app .tblfoot").innerText())
                .includes("勾选 1 条进集锦"),
                "续接跑完把结果喂回界面（不修则停在「勾选 0 条」）");
  ok(await toast("🟢 高置信 12 个"), "续接跑完也弹完成提示（服务端 msg）");

  await page.unroute("**/api/jobs/det1");
  await page.unroute("**/api/jobs/det1/result");

  // 借「↻ 重新载入」把 state 拉回服务端真值（顺带验这条路）
  // ⚠ 关弹窗那颗按钮本批从「取消」改名成「关闭」（本批 ①：它的旧含义是复原，
  // 语义已经不存在，留着名字而行为是「保留改动」就是在骗人）。
  await menu("⏱ 时刻校准");
  await dlg("offdlg").locator(".el-dialog__footer button")
    .filter({ hasText: "重新载入" }).click();
  await dlg("offdlg").locator(".el-dialog__footer button")
    .filter({ hasText: "关闭" }).click();
  await dlg("offdlg").waitFor({ state: "hidden" });
  await waitFor(async () => (await page.locator("#app .tblfoot").innerText())
                .includes("勾选 0 条进集锦"), "重新载入拉回服务端真值");

  // 失败路径：400/409 一律把服务端文案弹出来（R15），且不锁表
  await page.unroute("**/api/detect");
  await page.route("**/api/detect", (route) => route.fulfill({
    status: 409, contentType: "application/json",
    body: JSON.stringify({ error: "已有检测在跑" }) }));
  await btn("开始检测").click();
  ok(await toast("已有检测在跑"), "409 文案逐字弹出");
  eq(await locked(), 0, "失败不锁表");
  await page.unroute("**/api/detect");

  await page.route("**/api/wall/scan", (route) => route.fulfill({
    status: 400, contentType: "application/json",
    body: JSON.stringify({ error: "尚无检测结果——先点「▶ 开始检测」" }) }));
  await btn("识别出手人").click();
  ok(await toast("尚无检测结果——先点"), "回扫守卫文案逐字弹出");
  await page.unroute("**/api/wall/scan");
  ok(!errs.length, "长任务段没打出 JS 异常");

  // —— 收尾修正 F1（R22）：筛选与当前行的权威只有一份 ——
  // 右栏在**全部** rows[] 里找当前行（筛选藏起来也不清空），而键盘动作走
  // `rowByKey`（**只看可见行**）⇒ 两者一旦分叉就会出现「右栏显示着 A，按键却
  // 毫无反应」。修法是让 `curKey` 永远落在可见集里（`watch(visibleRows)`）。
  await rowCell(0).click();
  await waitFor(async () => (await page.locator("#app .el-table__row.current-row").count()) === 1,
                "点一行 → 有当前行");
  await page.locator("#app .toolbar .el-radio-button").filter({ hasText: "🟡" }).click();
  await waitFor(async () => (await page.locator("#app .el-table__row.current-row").count()) === 0,
                "筛掉当前行 → 高亮与 curKey 一起清掉");
  ok((await page.locator("#app .right").innerText()).includes("点击左侧表格任意一行开始复核"),
     "右栏回到空态（不再显示被筛掉的那一行）");
  await page.keyboard.press("Space");
  await page.waitForTimeout(300);
  ok((await page.locator("#app .tblfoot").innerText()).includes("勾选 0 条进集锦"),
     "没有当前行时键盘动作是 no-op（不隔空勾一条看不见的球）");
  await page.locator("#app .toolbar .el-radio-button").filter({ hasText: "全部" }).click();
  await waitFor(async () => (await page.locator("#app .el-table__row").count()) === 12,
                "筛回全部 12 行");

  // —— MRU 置顶（2026-10-11）：打过的标签自动排到行内下拉候选首位 ——
  // 顺序只断**下拉候选**（pickTags），state.tags 库序不动（MRU 是展示层）；
  // 热键 1-9/帮助清单/筛选下拉仍走库序——上面「1=三分」那条就是钉子。
  // 「上篮」库里原第 2 位；此刻 MRU 队首是新标签段的 reviewtag9（也在库里），
  // 打一次「上篮」必须把它压到 reviewtag9 前面——move-to-front 真发生了。
  const tagCellOf = (i) =>
    page.locator("#app .el-table__row").nth(i).locator("td").nth(TAGCELL);
  await tagCellOf(0).locator(".el-select__wrapper").click();
  await page.locator(".el-select-dropdown__item:visible")
    .filter({ hasText: "上篮" }).first().click();
  await waitFor(async () => (await state()).rows[0].tag === "上篮",
                "第 1 行打上「上篮」");
  await tagCellOf(1).locator(".el-select__wrapper").click();
  eq((await page.locator(".el-select-dropdown__item:visible").first().innerText()).trim(),
     "上篮", "打过的「上篮」置顶（压过 reviewtag9，库里它原是第 2 位）");
  ok((await state()).tags[0] === "三分" && (await state()).tags[1] === "上篮",
     "标签库原序不动（三分/上篮…，MRU 只是下拉展示层）");
  await page.keyboard.press("Escape");
  // —— ★ 精选（2026-10-23）：单条只出打星的球 ——
  // 星列**必须在表格最后一列**：本 spec 有十几处按列序取单元格（td.nth(2)
  // 时刻 / nth(3) 档位 / TAGCELL 标签）+ CSS `.row-miss td:nth-child(3)`。
  // 插在中间这些索引全要 +1。所以这一段反过来当**位置自检**：上面那些断言
  // 全过 = 列插对了位置；这里再显式断一次"最后一列就是星"。
  const starTd = (i) =>
    page.locator("#app .el-table__row").nth(i).locator("td").last();
  ok(/[★☆]/.test(await starTd(0).innerText()), "最后一列是 ★ 精选列（列序未变）");
  const st0 = await state();
  const starIdx = st0.rows.findIndex((r) => !r.selection);
  ok(starIdx >= 0, `夹具里还有未勾选的行（用来验「打星自动补勾」，实际 ${starIdx}）`);
  const starKey = st0.rows[starIdx].key;
  await starTd(starIdx).locator(".star").click();
  await waitFor(async () => (await state()).rows.find((r) => r.key === starKey).starred,
                "点 ★ → 服务端 starred=true");
  ok((await state()).rows.find((r) => r.key === starKey).selection,
     "打星自动补勾进片（不变式：精选 ⊆ 进片，用户点一次星就够）");
  await waitFor(async () => (await page.locator("#app .topbar").innerText()).includes("★ 1"),
                "顶栏 ★ 计数 = 1（就是「这次能出几条」）");

  // 单条模式的清单：★ 精选置顶 + 默认只勾它（合集项退到第二位且不预选，
  // 否则点一下「开始生成」就是把全场勾选的球各出一条）
  await page.locator("#app .topbar button").filter({ hasText: "🎬 生成" }).click();
  await dlg("gendlg").waitFor();
  await dlg("gendlg").locator(".el-radio-button").filter({ hasText: "竖屏运镜" }).click();
  await dlg("gendlg").locator(".el-radio-button").filter({ hasText: "逐球各出一条" }).click();
  await waitFor(async () => (await dtitle("gendlg")) === "📱 生成竖屏单条（逐球各出一条）",
                "单条标题逐字");
  const gb2 = dlg("gendlg").locator(".gpick .el-checkbox");
  await waitFor(async () => (await gb2.first().innerText()).includes("只出打星的球"),
                "清单头部是单条那串 ★ 精选（等 /api/gen/choices 回来）");
  // ⚠ 判据必须带「只出打星的球」这半句，不能只等「★ 精选」：2026-10-24 起
  // 集锦清单里**也有**精选项（文案「打星的球合成一条」），只等四个字会在集锦
  // 旧清单还没重拉时就命中，接着读到的是"尚未重映射"的勾选态（假红）。
  ok(await gb2.first().locator("input").isChecked(), "单条模式默认勾 ★ 精选");
  ok(!(await gb2.nth(1).locator("input").isChecked()), "默认不勾「全部勾选的进球」");
  // 精选项文案**不含球数**：它同时是勾选值本身，拼了数字就会随打星数变化
  // 而丢勾选（合集项那套文案吃过同一个亏，见方案 §5③）
  ok(!/\d/.test(await gb2.first().innerText()), "精选项文案不含数字");
  ok((await dlg("gendlg").innerText()).includes("★ 已精选 1 球"),
     "统计行带精选数（球数只在这里出现）");
  await dlg("gendlg").locator(".el-dialog__footer button").filter({ hasText: "关闭" }).click();
  await dlg("gendlg").waitFor({ state: "hidden" });

  // S = 切换当前行星标（挑片键盘流：↑↓ 走行、S 打星，不用回鼠标点最后一列）
  // ⚠ 等待判据必须是**界面**（星变 ☆/★），不能只等 `/api/session` 回来：
  // `state()` 是直接发 GET，它可能**早于** PATCH 响应在前端 store 落地 ⇒ 那一刻
  // 按键读到的还是旧的 starred，于是发的还是"取消"（服务端值不变）⇒ 连按两次
  // S 看起来"没反应"。真人按键间隔远大于这一帧，但断言不能赌这个时序。
  await rowCell(starIdx).click();
  await page.keyboard.press("s");
  await waitFor(async () => (await starTd(starIdx).innerText()).includes("☆"),
                "按 S 取消精选（界面同步成 ☆）");
  ok(!(await state()).rows.find((r) => r.key === starKey).starred,
     "取消精选落到了服务端（S 键真的发了 PATCH）");
  await page.keyboard.press("s");
  await waitFor(async () => (await starTd(starIdx).innerText()).includes("★"),
                "再按 S 恢复精选（界面同步成 ★）");

  // —— 集锦模式也带「★ 精选」（2026-10-24）：同一批里"合集 + 精选"两项都能勾 ——
  // 切输出模式时清单要重拉，精选项文案换成"合成一条"那串；pick 存的是**字符串**
  // ⇒ 不重映射就凭空丢一项（合集项当年就是这么丢的）。
  await page.locator("#app .topbar button").filter({ hasText: "🎬 生成" }).click();
  await dlg("gendlg").waitFor();
  await dlg("gendlg").locator(".el-radio-button").filter({ hasText: "竖屏运镜" }).click();
  await dlg("gendlg").locator(".el-radio-button").filter({ hasText: "逐球各出一条" }).click();
  await waitFor(async () => (await gb2.first().innerText()).includes("只出打星的球"),
                "单条清单头部是单条那串精选");
  await dlg("gendlg").locator(".el-radio-button").filter({ hasText: "一条集锦" }).click();
  await waitFor(async () => (await gb2.first().innerText()).includes("合成一条"),
                "切到集锦 → 精选项文案换成「打星的球合成一条」");
  ok(await gb2.first().locator("input").isChecked(),
     "切模式后 ★ 精选仍勾着（勾选随模式重映射，没丢）");
  ok(!(await gb2.nth(1).locator("input").isChecked()),
     "切模式不会顺手勾上合集（那样会多出一条全场集锦）");
  await dlg("gendlg").locator(".el-dialog__footer button").filter({ hasText: "关闭" }).click();
  await dlg("gendlg").waitFor({ state: "hidden" });

  // 重开一轮，验集锦模式的**默认勾选**没被改掉：仍是合集（有星也不预选精选，
  // 否则每次出集锦都要先取消一次）。顺带验"有星才列这一项"。
  await page.locator("#app .topbar button").filter({ hasText: "🎬 生成" }).click();
  await dlg("gendlg").waitFor();
  await dlg("gendlg").locator(".el-radio-button").filter({ hasText: "竖屏运镜" }).click();
  await dlg("gendlg").locator(".el-radio-button").filter({ hasText: "一条集锦" }).click();
  await waitFor(async () => (await dtitle("gendlg")) === "📱 生成竖屏集锦（跟球运镜）",
                "集锦标题逐字");
  await waitFor(async () => (await gb2.first().innerText()).includes("合成一条"),
                "集锦清单里有精选项（本场有星）");
  ok(await gb2.nth(1).locator("input").isChecked(),
     "集锦默认仍勾「全部勾选的进球」（旧行为一个字节不变）");
  ok(!(await gb2.first().locator("input").isChecked()), "集锦不预选 ★ 精选（想用才点）");
  await dlg("gendlg").locator(".el-dialog__footer button").filter({ hasText: "关闭" }).click();
  await dlg("gendlg").waitFor({ state: "hidden" });

  ok(!errs.length, "收尾段没打出 JS 异常");

  if (errs.length) throw new Error(`页面有 JS 异常：${errs.join(" | ")}`);
  console.log(`✅ 全过 ${pass} 项（行数 ${n}，勾选/取消勾选/新标签先入库再打标都落到了服务端）`);
} finally {
  await browser.close();
}
