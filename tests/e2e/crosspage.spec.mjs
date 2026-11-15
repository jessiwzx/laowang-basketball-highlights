// 跨页 e2e（E1 G3b）：三页联动。全部真 <a> 整页导航（R12——导航本身就是被测物，
// 唯一 page.goto = 起航进 edit.html）。夹具（wrapper 侧组合）：剪辑台 12 行 +
// 墙 shooters/traces + 标定真视频，三页共享同一后端会话。
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
const stateApi = async () => (await fetch(`${BASE}/api/state`)).json();
const calibState = async () => (await fetch(`${BASE}/api/calib/state`)).json();

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
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));

  // —— 流①：tabbar 矩阵（三页各开一次；当前页高亮；真 <a> 导航）——
  await page.goto(`${BASE}/edit.html`, { waitUntil: "domcontentloaded" });
  await page.locator("#app .topbar input").first().waitFor({ timeout: 30000 });
  eq(await page.locator("nav.tabbar a").count(), 3, "tabbar 应有三条链");
  eq(await page.locator("nav.tabbar a.on").getAttribute("href"), "/edit.html",
     "剪辑台页 tabbar 高亮指向自己");

  await page.locator('nav.tabbar a[href="/wall.html"]').click();
  await page.locator("#app .stage-hint").waitFor({ timeout: 30000 });   // 无会话空态
  eq(await page.locator("nav.tabbar a.on").getAttribute("href"), "/wall.html", "墙页高亮");

  await page.locator('nav.tabbar a[href="/calib.html"]').click();
  await page.locator("#app .topbar .el-tag").first().waitFor({ timeout: 30000 });
  eq(await page.locator("nav.tabbar a.on").getAttribute("href"), "/calib.html", "标定页高亮");

  await page.locator('nav.tabbar a[href="/edit.html"]').click();
  await page.locator("#app .topbar input").first().waitFor({ timeout: 30000 });

  // —— 流②：会话共享（edit 真实填路径载入 → <a> 去 wall 同视频 → 去 calib 同视频）——
  const input = page.locator("#app .topbar input").first();
  await input.fill(VIDEO);
  await input.press("Enter");
  await waitFor(async () => (await page.locator("#app .el-table__row").count()) === 12,
                "剪辑台应载入 12 行");
  eq((await stateApi()).videoPath, VIDEO, "会话已登记视频路径（服务端真值）");

  await page.locator('nav.tabbar a[href="/wall.html"]').click();
  await page.locator("#app .wallgrid .card").first().waitFor({ timeout: 30000 });
  ok((await page.locator("#app .wallgrid .card").count()) === 12,
     "墙页同视频直达：12 张卡（shooters 夹具），无需重填路径");
  eq((await page.locator("#app .topbar .el-tag").nth(1).innerText()).trim(), "未归 12",
     "墙页未归计数 = 12（会话共享）");

  await page.locator('nav.tabbar a[href="/calib.html"]').click();
  await page.locator("#app .topbar .el-tag").first().waitFor({ timeout: 30000 });
  const calibInput = page.locator("#app .topbar input").first();
  await waitFor(async () => (await calibInput.inputValue()) === VIDEO,
                "标定页路径框应回填会话视频路径");
  eq((await page.locator("#app .topbar .el-tag").first().textContent()).trim(), "未标定",
     "标定页此刻应「未标定」（流⑤才生成）");

  // —— 流③：edit→wall 深链（依据卡的「在出手人墙中查看 →」，BasisPanel 的 el-link）——
  await page.locator('nav.tabbar a[href="/edit.html"]').click();
  await waitFor(async () => (await page.locator("#app .el-table__row").count()) === 12,
                "回剪辑台仍是 12 行");
  // rowCell 同款：点「时刻」列（整行中心会落在标签列下拉上——edit spec 的既有教训）
  await page.locator("#app .el-table__row").nth(0).locator("td").nth(2).click();
  const deep = page.locator('#app a[href="/wall.html"]');
  await waitFor(async () => (await deep.count()) === 1, "选中行后依据卡应出现墙深链");
  ok((await deep.innerText()).includes("在出手人墙中查看"), "深链文案");
  await deep.click();
  await page.locator("#app .wallgrid .card").first().waitFor({ timeout: 30000 });
  ok(page.url().endsWith("/wall.html"), "深链真导航到了墙页");

  // —— 流④：归人回写（wall 托盘归人 → wrapper 断 manual_groups.json；
  //      edit 侧如实断「12 行不受扰」——state_payload 无 manual_groups 字段，R11，不造假联动）——
  const cards = page.locator("#app .wallgrid .card");
  await cards.nth(0).click();
  await cards.nth(1).click();
  await page.locator("#app .tray .el-select").click();
  // el-select 下拉 teleport 到 body，别限定 #app（Plan B 教训）
  await page.locator('.el-select-dropdown__item:has-text("三分")').first().click();
  await page.locator('#app .tray button:has-text("归人")').click();
  await waitFor(async () =>
    (await page.locator("#app .topbar .el-tag").nth(1).innerText()).trim() === "未归 10",
    "归人 2 球后墙页未归 10");
  eq((await page.locator("#app .topbar .el-tag").nth(1).innerText()).trim(), "未归 10",
     "wall 侧状态面：未归 12 → 10");

  await page.locator('nav.tabbar a[href="/edit.html"]').click();
  await waitFor(async () => (await page.locator("#app .el-table__row").count()) === 12,
                "归人写盘后回剪辑台，12 行不受扰");
  eq(await page.locator("#app .el-table__row").count(), 12,
     "edit 侧如实面：行数不受扰（edit 页不读 manual_groups）");

  // —— 流⑤：calib 联动（抽帧→点筐口→生成 → 已标定 + 同 work 目录 calibration.json；
  //      回 edit 12 行不受扰）——
  await page.locator('nav.tabbar a[href="/calib.html"]').click();
  await page.locator("#app .topbar .el-tag").first().waitFor({ timeout: 30000 });
  const timeIn = page.locator("#app .panel input").first();
  await timeIn.fill("1.2"); await timeIn.press("Enter");
  await page.locator("#app .panel button:has-text('抽帧')").first().click();
  await waitFor(async () => (await page.locator("#app .panel img").count()) === 1, "抽帧出帧图");
  await waitFor(async () => await page.locator("#app .panel img").first()
                  .evaluate((el) => el.complete && el.naturalWidth > 0), "帧图真加载");
  await page.locator("#app .footer-nav button").nth(1).click();          // 步②
  const wrap = page.locator("#app .panel .frame-wrap");
  await wrap.waitFor();
  const box = await wrap.boundingBox();
  const at = (fx, fy) => wrap.click({ position: { x: box.width * fx, y: box.height * fy } });
  await at(0.6719, 0.4278);   // 筐口左端（与 calib spec 同夹具同三点——已实测可生成）
  await at(0.9375, 0.4278);   // 筐口右端
  await at(0.8047, 0.5556);   // 球
  await waitFor(async () => (await calibState()).canBuild === true, "三点落到服务端");
  await page.locator("#app .footer-nav button").nth(1).click();          // 步③
  await waitFor(async () =>
    (await page.locator("#app .panel button:has-text('生成标定')").count()) === 1,
    "步③ 出现「生成标定」");
  const rfBuild = page.waitForResponse((r) => r.url().includes("/api/calib/build"));
  await page.locator("#app .panel button:has-text('生成标定')").click();
  eq((await rfBuild).status(), 200, "生成标定 200（E26：写路径先等响应再轮询）");
  await waitFor(async () => (await calibState()).ready === true, "生成后 ready=true");
  eq((await page.locator("#app .topbar .el-tag").first().textContent()).trim(), "已标定",
     "标定页顶栏标签翻「已标定」");

  await page.locator('nav.tabbar a[href="/edit.html"]').click();
  await waitFor(async () => (await page.locator("#app .el-table__row").count()) === 12,
                "生成标定后回剪辑台");
  eq(await page.locator("#app .el-table__row").count(), 12,
     "生成标定不碰 events——12 行不受扰");

  ok(errs.length === 0, `页面报错：${errs.join(" | ")}`);
  console.log(`✅ 全过 ${pass} 项`);
} finally {
  await browser.close();
}
