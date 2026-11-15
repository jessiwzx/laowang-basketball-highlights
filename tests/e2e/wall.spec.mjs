// 出手人墙页级 e2e：真服务 + 真夹具 + 真产物 + 真浏览器。
// 由 tests/e2e/test_wall_e2e.py 启动——服务跑在临时目录里（cwd=tmp），
// 归人/移出/拖拽全部只写临时 work/，仓库根红线 5 档由 launcher 拍快照核对。
//
// 环境：BBH_BASE（服务）、BBH_VIDEO（夹具视频路径）、BBH_PLAYWRIGHT
// （playwright 的绝对 file:// URL——ESM 从本文件位置向上解析、不认 NODE_PATH）
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
const wallApi = async (sort) =>
  (await fetch(`${BASE}/api/wall${sort ? "?sort=" + encodeURIComponent(sort) : ""}`)).json();
// 等 UI 跟上回包（请求是异步的，DOM 后变）
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
  // 只收 JS 异常：假 jpg 解码失败、/media 404 走 network 不是 pageerror
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));

  await page.goto(`${BASE}/wall.html`, { waitUntil: "domcontentloaded" });
  await page.locator("#app .topbar .el-tag").first().waitFor();

  // —— A. 空会话态：服务是全新的，先引导去剪辑台（R9：墙页没有路径输入）——
  await waitFor(async () => (await page.locator("#app .stage-hint").count()) === 1,
                "无会话空态出现");
  ok((await page.locator("#app .stage-hint").innerText()).includes("先去"),
     "空态文案引导去剪辑台");
  eq((await page.locator("#app .topbar .el-tag").nth(1).innerText()).trim(),
     "未归 0", "无会话时未归计数为 0（不发 /api/wall）");

  // —— B. 开会话（用户真实路径 = 在剪辑台载入后跳过来；这里直连服务端开
  //        会话，然后整页 reload 模拟跨页导航）——
  await fetch(`${BASE}/api/session`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ videoPath: VIDEO }),
  });
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator("#app .wallgrid .card").first().waitFor({ timeout: 20000 });

  // —— C. 计数与卡片（数据全部来自服务端回包，前端不算统计）——
  const w = await wallApi();
  const cards = page.locator("#app .wallgrid .card");
  eq(await cards.count(), 12, "左墙 12 卡（夹具 12 球全上墙）");
  eq((await page.locator("#app .topbar .el-tag").nth(1).innerText()).trim(),
     "未归 12", "顶栏未归计数");
  eq((await page.locator("#app .topbar .el-tag").nth(2).innerText()).trim(),
     "已归 0", "顶栏已归计数");
  ok((await page.locator("#app .wallgrid .card .cap span").first().innerText())
     .includes("00:01.2"), "首卡=时间序第一球（label=拆 ⚠ 后的 caption）");
  eq(await page.locator("#app .wallgrid .card .warn").count(), 1,
     "spot=bad 一张 ⚠ 角标");
  ok((await page.locator("#app .wallgrid .card .warn").innerText())
     .includes("⚠ 定位失效"), "角标文案逐字");
  const src = await page.locator("#app .wallgrid .card img").first()
    .getAttribute("src");
  ok(src && src.startsWith("/media/work?path="), `缩略图走 /media/work（${src}）`);
  eq(await page.locator("#app .wallgrid .card.sel").count(), 0, "初始无选中");

  // —— D. 排序三态：值=服务端中文标签（R1）；夹具让两种序相反才有鉴别力 ——
  const radios = page.locator("#app .topbar .el-radio-button");
  eq(await radios.count(), 3, "三个排序档（来自回包 sorts）");
  eq((await radios.nth(0).innerText()).trim(), "时间", "档名·时间");
  eq((await radios.nth(1).innerText()).trim(), "外观相似", "档名·外观相似");
  eq((await radios.nth(2).innerText()).trim(), "出手点", "档名·出手点");
  await radios.nth(2).click();
  await waitFor(async () => (await page.locator("#app .wallgrid .card .cap span")
    .first().innerText()).includes("00:05.6"),
    "切「出手点」后首卡=最晚球（夹具 x 递减 ⇒ 与时间序相反）");
  await radios.nth(0).click();
  await waitFor(async () => (await page.locator("#app .wallgrid .card .cap span")
    .first().innerText()).includes("00:01.2"), "切回「时间」首卡复原");

  // —— D+. 真写盘一次（launcher 的 manual_groups.json 门：spec 每跑一次
  //         至少完成一次真归人）。Task 3 还没有托盘 UI，先走 API 路径归一球
  //         再移出还原，墙回 12 球原状；Task 4 的 E/F/G 插在本块与收尾之间，
  //         由托盘 UI 路径接管「真写」。只写盘不计数（保持 14 项口径），非 2xx 即抛。
  {
    const k0 = (await wallApi()).unassigned[0].key;
    const r1 = await fetch(`${BASE}/api/wall/assign`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ person: "三分", keys: [k0], sort: "时间" }),
    });
    if (!r1.ok) throw new Error(`❌ API 归人回包 ${r1.status}：${await r1.text()}`);
    const r2 = await fetch(`${BASE}/api/wall/unassign`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ keys: [k0], sort: "时间" }),
    });
    if (!r2.ok) throw new Error(`❌ API 移出回包 ${r2.status}：${await r2.text()}`);
  }

  // —— E. 点选与托盘 chips ——
  await cards.nth(0).click();
  await cards.nth(1).click();
  eq(await page.locator("#app .tray .el-tag").count(), 2, "点两卡 → 托盘 2 枚 chips");
  ok((await page.locator("#app .tray b").innerText()).includes("已选 2 球"),
     "托盘标题计数");
  eq(await page.locator("#app .wallgrid .card.sel").count(), 2, "两张卡带选中态");
  ok((await page.locator("#app .tray .el-tag").first().innerText()).includes("00:01.2"),
     "chip 标签=服务端 caption（R12）");
  await page.locator("#app .tray .el-tag .el-tag__close").first().click();
  eq(await page.locator("#app .tray .el-tag").count(), 1, "chip × 撤选后剩 1");
  eq(await page.locator("#app .wallgrid .card.sel").count(), 1, "卡片选中态跟着撤");
  await cards.nth(0).click();                    // 再点同一张卡 = 恢复选中
  eq(await page.locator("#app .tray .el-tag").count(), 2, "toggle 再点恢复");

  // —— F. 归人（下拉候选=回包 personChoices；guard 读数先出现）——
  ok(Array.isArray(w.personChoices) && w.personChoices.includes("三分"),
     "回包 personChoices 含标签库「三分」（夹具 tags.json）");
  await page.locator("#app .tray .el-select").click();
  // el-select 下拉 teleport 到 body（Plan B 的教训：不能限定在 #app 里找）
  await page.locator(".el-select-dropdown__item:has-text(\"三分\")").first().click();
  await waitFor(async () => (await page.locator("#app .guardline").count()) === 1,
                "guardline 出现（换人名即取读数，R4）");
  ok((await page.locator("#app .guardline").innerText()).includes("已归 0 球"),
     "guard 读数 n=0（该人还没组）");
  await page.locator("#app .tray button:has-text(\"归人\")").click();
  await waitFor(async () =>
    (await page.locator("#app .topbar .el-tag").nth(1).innerText()).trim() === "未归 10",
    "归人后未归 10");
  eq(await page.locator("#app .tray .el-tag").count(), 0, "归人后清点选");
  await waitFor(async () => (await page.locator(".el-message--success").count()) >= 1,
                "成功 toast");
  ok((await page.locator(".el-message--success").last().innerText())
     .includes("✅ 2 球 → 三分"), "toast=服务端 msg 逐字");

  // —— G. 护栏：相邻球（0.4s < 2.5s）再归同人 → warning 警示、照常归人 ——
  // （此刻未归墙首两卡 = 00:02.0 / 00:02.4，与已归的 00:01.6 也是 0.4s 邻对）
  // G2d/R6（快照+恰+1）：F 段那对邻对（00:01.2↔00:01.6）自己也产 warning，3s 内仍在屏
  //  ⇒ 旧判据「≥1」可被 F 的遗留 toast 预先满足。改为点击前快照计数、等**恰 +1**
  //  ——只有本次归人的新 warning 能推动计数越过快照（calib ⑮(4) 同款形状；
  //  收紧发生在 waitFor 的条件里，ok/eq 条数不动 ⇒ 项数不变）。
  const warn0 = await page.locator(".el-message--warning").count();
  await cards.nth(0).click();
  await cards.nth(1).click();
  await page.locator("#app .tray button:has-text(\"归人\")").click();  // 人名还是 三分
  await waitFor(async () =>
    (await page.locator(".el-message--warning").count()) === warn0 + 1,
    "护栏警示 toast（比点击前恰多一条）");
  ok((await page.locator(".el-message--warning").last().innerText()).includes("护栏"),
     "警示文案来自服务端 msg");
  await waitFor(async () =>
    (await page.locator("#app .topbar .el-tag").nth(1).innerText()).trim() === "未归 8",
    "警示不阻断：照常归人（未归 8）");
  await waitFor(async () => (await page.locator("#app .guardline").innerText())
    .includes("已归 4 球"), "归人后 guard 读数随组刷新（R4 第二路）");

  // —— H. 右列分组：组头/缩略图/折叠（R15：手动收起在重载墙后保持）——
  const ghead = page.locator("#app .right .ghead");
  eq(await ghead.count(), 1, "一个组（三分）");
  ok((await ghead.locator("b").innerText()).includes("三分"), "组头人名");
  eq((await ghead.locator(".el-tag").innerText()).trim(), "4 球", "组头计数=已归 4");
  eq(await page.locator("#app .right .thumb").count(), 4, "组内 4 缩略图");
  ok((await page.locator("#app .right .thumb .t").first().innerText())
     .includes("00:01.2"), "缩略图时间条=labelOf");
  ok((await page.locator("#app .right .thumb").first().getAttribute("title"))
     .includes("拖回左侧未归墙"), "缩略图 title 逐字");
  const wrap = page.locator("#app .right .el-collapse-item__wrap").first();
  await ghead.click();                          // 收起
  await wrap.waitFor({ state: "hidden" });
  await radios.nth(2).click();                  // 重排（applyWall 路径）
  await waitFor(async () => (await page.locator("#app .wallgrid .card .cap span")
    .first().innerText()).includes("00:05.6"), "重排生效");
  await wrap.waitFor({ state: "hidden" });      // 收起态在重排后保持（R15）
  await ghead.click();                          // 重新展开
  await wrap.waitFor({ state: "visible" });
  await radios.nth(0).click();                  // 排序复位，后续段按时间序断言
  // 首卡期望=未归墙时间序第一球：F/G 已把 4 个最早球归进三分，此刻是
  // 00:02.8（brief 原文 00:01.2 是 D 段拷贝残留，实跑超时勘误）
  await waitFor(async () => (await page.locator("#app .wallgrid .card .cap span")
    .first().innerText()).includes("00:02.8"), "切回「时间」");

  // —— I. 移出：点已归缩略图入托盘 → ↩ 移出 → 回未归墙 ——
  await page.locator("#app .right .thumb").first().click();
  eq(await page.locator("#app .tray .el-tag").count(), 1, "已归球也能点选");
  ok((await page.locator("#app .tray .el-tag").first().innerText())
     .includes("三分·"), "chip 显示 人名·key（R12）");
  await page.locator("#app .tray button:has-text(\"移出\")").click();
  await waitFor(async () =>
    (await page.locator("#app .topbar .el-tag").nth(1).innerText()).trim() === "未归 9",
    "移出后未归 9");
  await waitFor(async () => (await page.locator(".el-message--success").count()) >= 1,
                "移出 toast");
  ok((await page.locator(".el-message--success").last().innerText())
     .includes("已移出归人"), "toast=服务端 msg 逐字");

  // —— J. 拖拽三条路（合成 DragEvent，R7/R11）——
  const fire = (loc, type) => loc.evaluate((el, t) => {
    el.dispatchEvent(new DragEvent(t, { bubbles: true, cancelable: true,
                                        dataTransfer: new DataTransfer() }));
  }, type);

  // J1. 未归卡片 → 组头 = 归人
  await fire(cards.nth(0), "dragstart");
  await fire(ghead, "dragover");
  ok((await ghead.getAttribute("class")).includes("droptarget"), "组头拖入高亮");
  await fire(ghead, "drop");
  await fire(cards.nth(0), "dragend");
  await waitFor(async () =>
    (await page.locator("#app .topbar .el-tag").nth(1).innerText()).trim() === "未归 8",
    "拖卡到组头=归人（未归 8）");
  eq((await ghead.locator(".el-tag").innerText()).trim(), "4 球", "组回到 4 球");

  // J2. 已归缩略图 → 左墙 = 移出
  await fire(page.locator("#app .right .thumb").nth(0), "dragstart");
  await fire(page.locator("#app .left"), "dragover");
  ok((await page.locator("#app .left").getAttribute("class")).includes("droptarget"),
     "左墙拖入高亮");
  await fire(page.locator("#app .left"), "drop");
  await waitFor(async () =>
    (await page.locator("#app .topbar .el-tag").nth(1).innerText()).trim() === "未归 9",
    "拖回左墙=移出（未归 9）");

  // J3. 未归卡片 → 新建区 = prompt 新建人名（confirmButtonText 是我们自己传的「归人」；
  //     locale 已装 zh-cn，默认按钮也是中文——但仍用语义选择器，不按文案选）
  await fire(cards.nth(0), "dragstart");
  await fire(page.locator("#app .drophint"), "dragover");
  ok((await page.locator("#app .drophint").getAttribute("class")).includes("over"),
     "新建区拖入高亮");
  await fire(page.locator("#app .drophint"), "drop");
  await page.locator(".el-message-box__input input").waitFor();
  await page.locator(".el-message-box__input input").fill("小左");
  await page.locator(".el-message-box__btns .el-button--primary").click();
  await waitFor(async () => (await ghead.count()) === 2, "新组出现");
  ok((await ghead.nth(1).locator("b").innerText()).includes("小左"),
     "新组=小左（码点序在三分后）");
  eq((await ghead.nth(1).locator(".el-tag").innerText()).trim(), "1 球", "新组 1 球");

  // —— K. 回扫错误路径：夹具视频文件不存在 ⇒ worker 守卫「视频不存在」——
  const scanBtn = page.locator("#app .topbar button", { hasText: "识别出手人" });
  await scanBtn.click();
  // loading 态在夹具下只存活一次 POST+GET 往返（worker 第一道守卫即刻失败、
  // jobs.js followJob 首轮 GET 无初始延迟即见终态）——限时尽力观测，抓到最好、
  // 抓不到不算失败；错误 toast 与下文的 loading 归零才是确定性门（R-9）
  await page.locator("#app .topbar .el-button.is-loading")
    .waitFor({ timeout: 1500 }).catch(() => {});
  await waitFor(async () => (await page.locator(".el-message--error").count()) >= 1,
                "回扫失败 toast");
  ok((await page.locator(".el-message--error").last().innerText())
     .includes("视频不存在"), "错误文案来自服务端 worker 守卫");
  await waitFor(async () =>
    (await page.locator("#app .topbar .el-button.is-loading").count()) === 0,
    "按钮 loading 解除可再点");
  eq(await page.evaluate(() => localStorage.getItem("bbh.runningJob")), null,
     "jobmemory 清槽（错误路径也清）");

  // —— L. MRU 置顶（2026-10-11）：归过的标签自动排到托盘人名下拉首位 ——
  // 「三分」在 F/G 段归过（MRU 队首）；这里归一次「上篮」（库里原第 2 位），
  // 重开下拉它必须压过三分排第一。并集内容仍是服务端 personChoices 的原样
  // （R13），这里只断展示序。
  await cards.nth(0).click();                    // 未归墙首卡选中
  await page.locator("#app .tray .el-select").click();
  await page.locator(".el-select-dropdown__item:visible")
    .filter({ hasText: "上篮" }).first().click();
  const tL0 = await page.locator(".el-message--success").count();
  await page.locator("#app .tray button:has-text(\"归人\")").click();
  await waitFor(async () =>
    (await page.locator(".el-message--success").count()) === tL0 + 1,
    "归人成功 toast 恰 +1（G2d/R6：快照+恰+1 才有牙）");
  await page.locator("#app .tray .el-select").click();
  eq((await page.locator(".el-select-dropdown__item:visible").first().innerText()).trim(),
     "上篮", "刚归过的「上篮」置顶（压过 F 段的「三分」）");
  await page.keyboard.press("Escape");

  // —— 收尾（后续任务把新断言插在这行之前）——
  if (errs.length) throw new Error("页面报错：" + errs.join(" | "));
  console.log(`✅ 全过 ${pass} 项`);
} finally {
  await browser.close();
}
