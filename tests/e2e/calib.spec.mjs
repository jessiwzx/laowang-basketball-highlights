// 标定页级 e2e（Plan D）：真夹具（合成视频可抽帧）+ 真服务 + 真产物。
// 项数口径与另两份 spec 一致：ok() 各 +1、eq() 各 +1（eq 内部调 ok）。
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
const calibState = async () => (await fetch(`${BASE}/api/calib/state`)).json();
const sessionState = async () => (await fetch(`${BASE}/api/state`)).json();
const existingState = async () => (await fetch(`${BASE}/api/calib/existing`)).json();

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

  await page.goto(`${BASE}/calib.html`, { waitUntil: "domcontentloaded" });
  await page.locator("#app .topbar .el-tag").first().waitFor();

  // ① 真挂载（不是骨架）：四步条、顶栏标签、路径输入框都在页内
  eq(await page.locator("#app .el-steps .el-step").count(), 4, "四步向导应恰有 4 步");
  eq((await page.locator("#app .topbar .el-tag").first().textContent()).trim(),
     "未标定", "还没标定时顶栏标签");
  // ⓪ 补段入口的门（终审 Critical A）：还没主标定时**不许**进补段态——服务端
  //    /api/calib/build 的补段分支（`segT is not None`）要求先有主标定 ⇒ 进去必 400，
  //    而 pendingSeg 只在**生成成功**时才清 ⇒ 只能刷新页面（F5）的死路。
  //    门 = `st.ready`（顶栏那枚 tag 用的**同一个键**）⇒ 此刻它刚断完「未标定」。
  ok(await page.locator("#app .steps-card").nth(1)
       .locator("button:has-text('新增补充段')").isDisabled(),
     "还没标定时「➕ 新增补充段」应禁用（否则走进补段死路：服务端要求先有主标定）");
  eq(await page.locator("#app .topbar input").count(), 1, "顶栏应恰有一个路径输入框");
  eq(await page.locator("#app .topbar input").first().getAttribute("placeholder"),
     "D:\\videos\\2026-09-18.mp4（直接读本机文件，不复制不上传）", "路径输入框 placeholder");
  // G2e/R7：顶栏路径框的 prefix 图标（main.js 全局注册 @element-plus/icons-vue，
  // 名字写错的失败形态是图标空白且 errs 为空 ⇒ 普通断言捉不到）。
  // 边界如实：这条只捉「图标整个没渲染」，捉不到「渲染成了另一个图标」。
  eq(await page.locator("#app .topbar .el-input__prefix svg").count(), 1,
     "顶栏路径框的 VideoCamera 图标应真渲染（恰 1 个）");

  // ② 底栏导航的门（步 ① 只认「抽过帧」这条本地量）
  eq((await page.locator("#app .footer-nav button").first().textContent()).trim(),
     "← 上一步", "底栏第一个按钮是上一步");
  ok(await page.locator("#app .footer-nav button").first().isDisabled(), "步 ① 的「上一步」应禁用");
  ok(await page.locator("#app .footer-nav button").nth(1).isDisabled(),
     "还没抽帧时「下一步」应禁用（门 = frameReady）");

  // ③ 服务端基线：还没开会话时 /api/calib/state 也必须 200（顶栏标签靠它）
  const st0 = await calibState();
  eq(st0.ready, false, "夹具应是「还没标定」的初始态");
  eq(st0.points.length, 0, "初始没有点");
  eq(st0.hasPreview, false, "初始没有预览");

  // ③ 步 ①：填路径 → 抽帧失败路径（时间点越界，真视频给真诊断）→ 真抽帧
  await page.locator("#app .topbar input").fill(VIDEO);
  await page.locator("#app .topbar input").press("Enter");
  await waitFor(async () => !!(await sessionState()).videoPath, "会话没登记视频路径");

  const timeIn = page.locator("#app .panel input").first();    // ⚠ 别叫 at：Task 4 在本文件里
  await timeIn.fill("999999");                                 //    另有一个 const at（帧容器点击器）
  await timeIn.press("Enter");
  await page.locator("#app .panel button:has-text('抽帧')").first().click();
  const miss = page.locator(".el-message");
  // G2a：裸 .first().waitFor() 走 Playwright 默认 30s、超时报 TimeoutError
  //  而非本 spec 文案。miss 是全新页面后的第一条 toast，无残留可撞 ⇒ 定位器不动。
  await waitFor(async () => (await miss.count()) > 0, "越界抽帧应弹提示（取帧失败）");
  ok((await miss.first().textContent()).includes("取帧失败"),
     "越界抽帧应弹服务端的真实诊断（含「取帧失败」）");
  ok(await page.locator("#app .footer-nav button").nth(1).isDisabled(),
     "抽帧失败后「下一步」仍应禁用");

  await timeIn.fill("1.2");
  await timeIn.press("Enter");
  await page.locator("#app .panel button:has-text('抽帧')").first().click();
  await waitFor(async () => (await page.locator("#app .panel img").count()) === 1,
                "抽帧后应出现参考帧图");
  ok(await page.locator("#app .panel img").first().evaluate((el) => el.complete && el.naturalWidth > 0),
     "参考帧图真加载出来了（naturalWidth > 0）");
  eq(await page.locator("#app .panel img").first().evaluate((el) => el.naturalWidth), 640,
     "帧宽应等于夹具视频宽（640）");
  // 标签描述的是**屏上这张帧**、不是输入框的实时值（E15）：改时间点但**不抽帧** ⇒ 标签不动。
  // 有牙：fill+Enter 真会提交 frameAt——上面那次越界抽帧靠的就是同一套「fill + Enter +
  // 点抽帧」，服务端回的是「取帧失败」（若没提交，t 会是 0 而抽帧成功，那条断言先红）。
  await timeIn.fill("3.4");
  await timeIn.press("Enter");
  const staleIn = await timeIn.inputValue();
  const staleLabel = (await page.locator("#app .panel .frame .label").textContent()).trim();
  ok(staleIn === "3.4" && staleLabel.includes("00:01.2")
     && (await page.locator("#app .panel").textContent()).includes("帧已就绪"),
     `帧就绪提示应出现，且标签仍描述屏上那张帧（时间点改成 3.4 未抽帧 ⇒ 标签应仍为 00:01.2，`
     + `实际输入框 ${JSON.stringify(staleIn)} / 标签 ${JSON.stringify(staleLabel)}）`);
  // 还原输入框：让后面的 Task 块看到与修前一致的状态（时间点仍是抽帧用的 1.2）
  await timeIn.fill("1.2");
  await timeIn.press("Enter");
  ok(!(await page.locator("#app .footer-nav button").nth(1).isDisabled()),
     "抽过帧后「下一步」应变可点（门 = frameReady）");

  // ④ 步 ②：点筐口左右 + 球 —— 断言**帧像素**（这一条是 R4 的牙：
  //    若前端误把百分比发上去，points 会是 0.67 这种数，下面立刻红）
  await page.locator("#app .footer-nav button").nth(1).click();      // 进步 ②
  await waitFor(async () => (await page.locator("#app .panel .frame-wrap").count()) === 1,
                "步 ② 应出现点选层");

  const wrap = page.locator("#app .panel .frame-wrap");
  const box = await wrap.boundingBox();
  const at = (fx, fy) => wrap.click({ position: { x: box.width * fx, y: box.height * fy } });
  await at(0.6719, 0.4278);        // 筐口左端 (430,154)
  await at(0.9375, 0.4278);        // 筐口右端 (600,154)
  await at(0.8047, 0.5556);        // 球心 (515,200)

  await waitFor(async () => (await calibState()).points.length === 3, "三点应落到服务端");
  const p3 = await calibState();
  ok(Math.abs(p3.points[0][0] - 430) <= 4 && Math.abs(p3.points[0][1] - 154) <= 4,
     `第一点应是帧像素(430,154)±4，实际 ${JSON.stringify(p3.points[0])}`);
  ok(Math.abs(p3.points[2][0] - 515) <= 4 && Math.abs(p3.points[2][1] - 200) <= 4,
     `第三点应是球心(515,200)±4，实际 ${JSON.stringify(p3.points[2])}`);
  ok(p3.canBuild === true, "三点后服务端应报 canBuild");
  // E19（实施者实测加的等待，非简报原文）：上面 `calibState()` 是**本进程另发**的一次
  //   HTTP，它比浏览器那条 POST 的回包先落地 ⇒ 轮询一返回就查按钮会撞上「服务端已有三点、
  //   前端还没 flush」的窄窗（实测红过一次）。这条 waitFor 只等**同一个渲染**落地
  //   （st 整份替换 ⇒ 按钮与下面的 .pt 计数同时更新），断言本身仍逐字是简报那条。
  await waitFor(async () => !(await page.locator("#app .footer-nav button").nth(1).isDisabled()),
                "canBuild 落地后「下一步」应变成可点");
  ok(!(await page.locator("#app .footer-nav button").nth(1).isDisabled()),
     "canBuild 后「下一步」应变可点");
  eq(await page.locator("#app .panel .pt").count(), 3, "帧上应有 3 个点标记");
  eq(await page.locator("#app .panel .pt.ball").count(), 1, "其中 1 个是球（绿）");

  // ④b natural 通路（M7：T3 的 frame-loaded → onFrameLoaded → natural 只写不读，此前无断言）：
  //     行文案的百分比 = pctOf(服务端帧像素, natural.w/h) ⇒ 它与上面那条 430±4 一起把 natural
  //     夹死：服务端值 430、屏上读数 67.x% ⇒ w ≈ 430/0.67 ≈ 640 = 真帧宽（natural 只由
  //     <img>@load 填）。若没填上（w=h=0），渲染点打出 (0.0%, 0.0%) 红，且 ±4 那两条同时红
  //     （imgPoint 退化成 CSS 像素，640 宽的帧在 ~690px 的框里会算出 ~465）。
  //     正则卡「恰好一位小数」= E5 的牙：pctOf 返回未取整浮点，渲染点不 toFixed(1) 就会
  //     打出 67.18750000000001%。锚定整串 ⇒ 顺带钉住 E3（i<2 的行不许出现球号）。
  const rowTxt = (i) => page.locator("#app .panel .pt-row > span").nth(i).textContent();
  const row0 = (await rowTxt(0)).trim(), row2 = (await rowTxt(2)).trim();
  ok(/^筐口左端：\(67\.\d%, 42\.\d%\)$/.test(row0) && /^球 1：\(80\.\d%, 55\.\d%\)$/.test(row2),
     `行文案应是「帧像素 ÷ 真帧尺寸」的百分比、且筐口行无球号：实际 ${JSON.stringify([row0, row2])}`);

  // ⑥ 第 6 点被前端拦（R19）：两点再点两次，服务端点数仍停在 5
  await at(0.55, 0.30);
  await at(0.60, 0.35);
  await waitFor(async () => (await calibState()).points.length === 5, "第 4/5 点应落到服务端");
  await at(0.65, 0.40);
  // 复审 Minor（E21）：原 `.last()` 在 toast 自行卸载后（~3.3s）会让 `textContent()` 阻塞到
  //   Playwright 默认 30s ⇒ 失败态是 30s 的 TimeoutError，不是本 spec 的文案。改成**按文案
  //   过滤**（拦截提示是 `ElMessage.info`）+ 本文件自己的 `waitFor(…, 3000)`：判据不变
  //   （有 toast 在说拦截文案），失败时 3s 内以 spec 自己的 ❌ 文案报出。
  const capped = page.locator(".el-message").filter({ hasText: "球最多点 3 次" });
  await waitFor(async () => (await capped.count()) > 0,
                "第 6 点应被拦并提示（toast 里出现「球最多点 3 次」）", 3000);
  ok((await capped.first().textContent()).includes("球最多点 3 次"), "第 6 点应被拦并提示");
  eq((await calibState()).points.length, 5, "第 6 点不许上服务端");

  // ⑦ 逐点撤销（R17：DELETE + 重发）与清空
  await page.locator("#app .panel .pt-row button").first().click();
  await waitFor(async () => (await calibState()).points.length === 4, "撤销后应剩 4 点");
  const after = await calibState();
  ok(Math.abs(after.points[0][0] - 600) <= 4, "撤销第 1 点后，队首应变成原第 2 点(筐口右端)");
  await page.locator("#app .panel button:has-text('清空全部')").click();
  await waitFor(async () => (await calibState()).points.length === 0, "清空后应 0 点");
  // E19 同款：上面的轮询是**本进程**发的 GET，可能比浏览器那条 DELETE 的回包先落地 ⇒
  // 直接查按钮会撞上「服务端已清空、前端还没 flush」的窄窗。等的是同一个渲染落地。
  await waitFor(async () => await page.locator("#app .footer-nav button").nth(1).isDisabled(),
                "点数不足 3 后「下一步」应重新禁用（等前端 flush）");
  ok(await page.locator("#app .footer-nav button").nth(1).isDisabled(),
     "点数不足 3 后「下一步」应重新禁用");

  // ⑧ 坐标基准必须落在 **<img> 自己的盒**上（复审 Important）：`imgPoint` 拿的是点击容器的
  //    `getBoundingClientRect()`，容器若含上方 `.label` 行（~22px），上报的帧 y 就系统性偏大
  //    （比例性偏差：屏上分率是按 wrap 高算的，而用户意图是「图上那一点」）。
  //    这条断言与上面的 `at()` **基准不同**（用 img 的 boundingBox 定位），所以缺陷不再自洽：
  //    不变量 = 图内分率 (fx,fy) ⇒ 上报帧像素 ≈ (fx×natural.w, fy×natural.h)（夹具 ⇒ (320,90)）。
  const img = page.locator("#app .panel .frame-wrap img");
  const ib = await img.boundingBox();
  const nat = await img.evaluate((el) => ({ w: el.naturalWidth, h: el.naturalHeight }));
  await img.click({ position: { x: ib.width * 0.50, y: ib.height * 0.25 } });
  await waitFor(async () => (await calibState()).points.length === 1, "基准那一点应落到服务端");
  const one = (await calibState()).points[0];
  const want = { x: 0.50 * nat.w, y: 0.25 * nat.h };
  // 容差 = max(2 帧px, 1.5 CSS px 折算成帧 px)：量化误差在 CSS 像素上，别用帧像素预算去吃它
  // （原写的 ±2 是帧px，只有一半口径对；本视口 1 CSS px ≈ 0.865 帧px ⇒ 1.5 CSS px ≈ 1.30 帧px
  // ⇒ 仍是 2/2，不放松；若将来帧被显示得更小（img CSS 高 ≲130px）也不至于让正确实现假红）
  const tolY = Math.max(2, 1.5 * nat.h / ib.height);
  const tolX = Math.max(2, 1.5 * nat.w / ib.width);
  ok(Math.abs(one[0] - want.x) <= tolX && Math.abs(one[1] - want.y) <= tolY,
     `图内(0.50,0.25) 应上报帧像素(${want.x}, ${want.y})±(${tolX}, ${tolY})，实际 ${JSON.stringify(one)}`);

  // ⑨ 单点撤销（复审 Minor）：`keep` 为空的分支走 `refreshCalib()` ⇒ 0 点 + 帧上无标记 + 空态文案
  await page.locator("#app .panel .pt-row button").first().click();
  await waitFor(async () => (await calibState()).points.length === 0, "撤销唯一一点后应 0 点");
  await waitFor(async () => (await page.locator("#app .panel .pt").count()) === 0,
                "撤销唯一一点后帧上应无标记（等前端 flush）");
  eq(await page.locator("#app .panel .pt").count(), 0, "撤销唯一一点后帧上应无标记");
  // 复审 Minor（E21 同形状，今天被上面那条 `.pt === 0` 挡着不可达）：裸定位器的
  //   `textContent()` 在元素缺席时会阻塞到 Playwright 默认 30s 抛 TimeoutError，不是 spec
  //   自己的文案 ⇒ 按**文案过滤** + 本文件的 `waitFor(…, 3000)`；`eq` 比的是**整串**，
  //   所以它不是「过滤器的回声」（文案被改坏照样红，且 3s 内报出）。
  const emptyMsg = page.locator("#app .panel .empty").filter({ hasText: "尚未标点" });
  await waitFor(async () => (await emptyMsg.count()) > 0,
                "撤销唯一一点后应回到「（尚未标点）」空态", 3000);
  eq((await emptyMsg.first().textContent()).trim(), "（尚未标点）",
     "撤销唯一一点后的空态文案");

  // ⑩ 步 ③：生成标定（主标定请求体恒 {}，R6）。简报原文把这一段编号成 ⑧，与本文件既有
  //    的 ⑧（坐标基准）/⑨（单点撤销）撞号 ⇒ 顺延为 ⑩，正文逐字照抄。
  //    步 ② 末尾把点清空了 ⇒ 这一段先重新点满 3 点（3 点前服务端 canBuild=false，
  //    「下一步」也是禁用的）。
  await at(0.6719, 0.4278); await at(0.9375, 0.4278); await at(0.8047, 0.5556);
  await waitFor(async () => (await calibState()).canBuild === true, "重新点满 3 点");
  await page.locator("#app .footer-nav button").nth(1).click();       // 进步 ③
  // E21 同款（实施者实测加的等待，非简报原文）：下面那条 `.click()` 判的正是「步 ③ 有
  //   「生成标定」按钮」，可裸 `.click()` 在按钮缺席时会阻塞到 Playwright 默认 30s 抛
  //   TimeoutError、报错文案不是本 spec 的话（Task 4 刚吃过两次）。改成先用本文件的
  //   waitFor 等按钮出现 ⇒ 失败态 10s 内以本文件自己的 ❌ 文案报出。断言本身逐字不变。
  await waitFor(async () => (await page.locator("#app .panel button:has-text('生成标定')").count()) === 1,
                "步 ③ 面板应有「生成标定」按钮");
  await page.locator("#app .panel button:has-text('生成标定')").click();
  await waitFor(async () => (await page.locator("#app .panel img.preview").count()) === 1,
                "生成后应出现 ROI 预览图");
  // 同款牙（实施者加的等待；**防御性**——复审 6/6 未能复现出这个窄窗）：预览图是会话内存
  //   里现编的 PNG，`<img>` 元素先出现、字节后到，而 `evaluate()` 不等人（它只等元素
  //   attach）⇒ 直接 evaluate 可能撞上「元素在、图还没解码完」。ok 那条判据逐字不动，
  //   只是先等它真的加载完。
  await waitFor(async () => await page.locator("#app .panel img.preview")
                  .evaluate((el) => el.complete && el.naturalWidth > 0),
                "ROI 预览图应真加载出来（naturalWidth > 0）");
  ok(await page.locator("#app .panel img.preview").evaluate((el) => el.complete && el.naturalWidth > 0),
     "ROI 预览图真加载出来了");
  const b = await calibState();
  ok(b.hasPreview === true, "生成后服务端应报 hasPreview");
  ok(b.ready === true, "生成后服务端应报 ready（已落 calibration.json）");
  ok((await page.locator("#app .panel").textContent()).includes("标定已保存"),
     "应显示服务端 msg 原文（含「标定已保存」）");
  ok((await page.locator("#app .panel").textContent()).includes("检测区域 ROI"),
     "图例应含「检测区域 ROI」");
  ok((await page.locator("#app .panel").textContent()).includes("参考帧已存档"),
     "应显示参考帧存档文案");
  eq(await page.locator("#app .topbar .el-tag").textContent(), "已标定",
     "顶栏标签应翻成「已标定」（ready 门）");
  ok(!(await page.locator("#app .footer-nav button").nth(1).isDisabled()),
     "hasPreview 后「下一步」应可点");

  // ⑪ 步 ④：滑块初值来自 /existing（R10）；改 S 后重算并回读 /existing（写路径的牙）
  //    （编号勘误 2026-09-21：本块简报原文写 ⑨，但交付版里 ⑨/⑩ 已分别被「单点撤销」「步 ③」
  //     占用 ⇒ 从本块起重新编号并**一路顺延到计划末尾**：T6 步④=⑪、T6 完成标定=⑫、
  //     T7 段管理=⑬、T8 查看已有标定=⑭。编号只在注释里，新块一律取「当前最大 +1」。）
  await page.locator("#app .footer-nav button").nth(1).click();       // 进步 ④
  await waitFor(async () => (await page.locator("#app .panel .el-slider input").count()) === 2,
                "步 ④ 应有两个滑块（S/V 下限）");
  const ex = await existingState();
  const srvS = ex.calibration.hsvLow[1], srvV = ex.calibration.hsvLow[2];
  ok(srvS !== 86, `夹具球的 S 下限不该恰好等于兜底值 86（实际 ${srvS}）——这条是下一行的分辨力保险`);
  const sIn = page.locator("#app .panel .el-slider input").first();
  const vIn = page.locator("#app .panel .el-slider input").nth(1);
  await waitFor(async () => (await sIn.inputValue()) === String(srvS),
                `S 滑块初值应 = /existing 的 hsvLow[1] = ${srvS}`);
  eq(Number(await vIn.inputValue()), srvV, "V 滑块初值应 = /existing 的 hsvLow[2]");

  // 写路径的等待必须与写**在时间上不重叠**：`existing()` 不吃 video_lock（api_calib.py:184），
  //   而 Calibration.save 走 `os.replace`（models.py:29）——Windows 上目标文件被读句柄打开时
  //   `os.replace` 抛 WinError 5 ⇒ 500 ⇒ 档不变。旧写法（点完就 150ms 轮询 /existing）
  //   实测出现过该假红（插桩定案，见计划 E26）。先等响应再轮询；判据一字未动。
  await sIn.fill("250"); await sIn.press("Enter");
  // G2b/E23 定案：这里保持 Playwright 默认 30s——它是**文档化的第二预算**
  // （waitForResponse 没有 ms 参数；等不到 refine（S=250 重算）响应会挂到测试超时 = 可读的红）。
  // 统一成 10s 会让「服务端 30s 才回」从超时变假绿。E2 真机跑过后复访。
  const rfS = page.waitForResponse((r) => r.url().includes("/api/calib/refine"));
  await page.locator("#app .panel button:has-text('重算预览')").click();
  eq((await rfS).status(), 200, "重算（S=250）应 200——5xx 多半是 os.replace 撞上读句柄（WinError 5）");
  await waitFor(async () => (await existingState()).calibration.hsvLow[1] === 250,
                "重算后服务端 S 下限应变 250（证明请求真带上了滑块值）");
  // 判据必须锚在**服务端 msg 的渲染块**上（`.el-alert .txt`）：面板里 HsvPanel 自己的
  //   「饱和度 S 下限（调大可滤肤色）— 当前 <sMin>」标签就含 250 ⇒ 断整个 panel 的
  //   textContent 是**假覆盖**（E27 同族漏网，终审指出：msg 渲染块整个坏掉也照样绿）。
  //   同面板另两条静态 el-alert 只有 `title`、**没有 `.txt` 子元素** ⇒ `.el-alert .txt`
  //   唯一指向服务端 msg（refine 的 msg 形状：`已保存：S 下限 250、V 下限 60，ROI 内命中 N 像素。`）。
  //   用 allTextContents().join() 而**不是** .textContent()：元素不存在时后者抛 Playwright
  //   超时错误，前者给空串 ⇒ 红的是本 spec 自己的文案（牙口纪律：红必须报本 spec 的话）。
  const msgTxt = (await page.locator("#app .panel .el-alert .txt").allTextContents()).join(" ");
  ok(msgTxt.includes("250"), `面板应显示服务端 msg（含新 S 值 250），实际「${msgTxt}」`);
  eq(await page.locator("#app .panel img.preview").count(), 1, "重算后预览图仍在");

  // ⑪b V 滑块的**写路径**（复审 Minor 2）：此前只覆盖了 S，`update:vMin → vLow → hsvLow[2]`
  //    整条没有断言 ⇒ `@update:v-min` 写成错的 kebab 时全套仍绿。形状与上面 S 那段对称。
  // 写路径的等待必须与写**在时间上不重叠**：`existing()` 不吃 video_lock（api_calib.py:184），
  //   而 Calibration.save 走 `os.replace`（models.py:29）——Windows 上目标文件被读句柄打开时
  //   `os.replace` 抛 WinError 5 ⇒ 500 ⇒ 档不变。旧写法（点完就 150ms 轮询 /existing）
  //   实测出现过该假红（插桩定案，见计划 E26）。先等响应再轮询；判据一字未动。
  await vIn.fill("120"); await vIn.press("Enter");
  // G2b/E23 定案：这里保持 Playwright 默认 30s——它是**文档化的第二预算**
  // （waitForResponse 没有 ms 参数；等不到 refine（V=120 重算）响应会挂到测试超时 = 可读的红）。
  // 统一成 10s 会让「服务端 30s 才回」从超时变假绿。E2 真机跑过后复访。
  const rfV = page.waitForResponse((r) => r.url().includes("/api/calib/refine"));
  await page.locator("#app .panel button:has-text('重算预览')").click();
  eq((await rfV).status(), 200, "重算（V=120）应 200——5xx 多半是 os.replace 撞上读句柄（WinError 5）");
  await waitFor(async () => (await existingState()).calibration.hsvLow[2] === 120,
                "重算后服务端 V 下限应变 120");
  eq((await existingState()).calibration.hsvLow[2], 120,
     "重算后服务端 V 下限应变 120（证明 V 滑块的值真带上去了）");

  // ⑫ 完成标定：只弹提示、不发请求、不换步
  await page.locator("#app .panel button:has-text('完成标定')").click();
  const done = page.locator(".el-message").filter({ hasText: "标定完成" });   // E21：按文案过滤，别用裸 .first()
  // G2a：done 已按文案过滤，同款换预算（裸 .first().waitFor() 走 Playwright 默认 30s、
  //      超时报 TimeoutError 而非本 spec 文案）。
  await waitFor(async () => (await done.count()) > 0, "「完成标定」应弹提示");
  ok((await done.first().textContent()).includes("标定完成"), "「完成标定」应弹提示");
  eq(await page.locator("#app .panel .el-slider").count(), 2, "「完成标定」不该离开步 ④");

  // ⑬ 段管理：补段全链（新增 → 回步① → 抽帧 → 点 3 点 → 生成补充段）→ 删除
  const card = page.locator("#app .steps-card").nth(1);
  ok((await card.textContent()).includes("标定段管理"), "第二张卡应是段管理卡");
  ok((await card.textContent()).includes("段起点规则"), "脚注应逐字在卡里");
  eq(await card.locator(".seg-row").count(), 1, "此时只应有主段一行");
  ok(!(await existingState()).files.includes("calibration_seg2.json"), "此刻不该有补段文件");
  // ⓪ 那条的**正控**（终审 Critical A）：主标定此刻已生成（上面 ⑩ 刚落盘）⇒ 同一个按钮
  //    必须**不再禁用**。两条合起来才钉住 `:disabled="!hasMain"`：只有「未标定时禁用」
  //    一条时，实现写成「永远禁用」也会绿。
  ok(!(await card.locator("button:has-text('新增补充段')").isDisabled()),
     "主标定已生成后「➕ 新增补充段」应可点（⓪ 那条的正控）");

  await card.locator("button:has-text('新增补充段')").click();
  const t1 = page.locator(".el-message").filter({ hasText: "回到第①步" });   // E21：按文案过滤，别用裸 .first()
  // E26：等本文件自己的 waitFor（有界 10s + 红在本 spec 的 ❌ 文案），别用裸 `.first().waitFor()`
  //      （走 Playwright 默认 30s，失败态是 `TimeoutError`）。等长替换，不计项。
  await waitFor(async () => (await t1.count()) > 0, "「新增补充段」应弹提示");
  ok((await t1.first().textContent()).includes("回到第①步"), "「新增补充段」应提示回到第①步");
  // 「取消补段」是 pendingSeg 的**唯一出口**（终审 Critical A）：它渲染在**段卡**里
  //   （段卡在任何步骤都渲染，不像步 ③ 的面板）⇒ 它的在/不在在任意步骤上都等价于
  //   pendingSeg 的真值。这里先钉「addSeg 之后它确实在」。
  eq(await card.locator("button:has-text('取消补段')").count(), 1,
     "补段态应出现「✖ 取消补段」（pendingSeg 的真值见证，段卡在任何步骤都渲染）");
  // ⚠ **勘误（实施者独立计数，2026-09-21）**：简报此处的期望值写 1，实测为 **2**——
  //   步 ① 的 FramePicker 有**两个**按钮含「抽帧」（`抽帧` / `跳到该时间抽帧`），
  //   Playwright 的 `:has-text()` 是子串匹配 ⇒ 两个都命中（简报自己下面那行点击就带
  //   `.first()`，正是同一个事实）。选择器一字未动，只把期望值改成真值 2；判别力不变：
  //   其它任何一步的 `.panel` 里都没有含「抽帧」的按钮（命中数 0）。
  eq(await page.locator("#app .panel button:has-text('抽帧')").count(), 2, "应回到步 ①");
  ok(await page.locator("#app .footer-nav button").nth(1).isDisabled(),
     "补段模式未抽帧时「下一步」应禁用（frameReady 被打回 false）");

  const atSeg = page.locator("#app .panel input").first();
  await atSeg.fill("3.6"); await atSeg.press("Enter");
  await page.locator("#app .panel button:has-text('抽帧')").first().click();
  await waitFor(async () => (await page.locator("#app .panel img").count()) === 1, "补段抽帧应出帧图");
  // ⚠ **E25（控制者勘误，2026-09-21）**：这里必须**只改输入框、不重新抽帧**。
  //   补段起点要锚在**屏上那张帧**（`frameStamp`），不是输入框活值（`frameAt`）——这是 E15
  //   那条「屏上那张帧」语义在**第二个调用点**（`doBuild`）上的判别式。若实现写成活值，
  //   Task 5 的 spec 与本 spec **都不会红**（Task 5 那一刻 frameAt === frameStamp === 1.2；
  //   本 spec 修前也是抽完帧就一路走到生成，二者恒等）⇒ 全绿却把错时刻写进
  //   `calibration_segN.json`（屏幕上的「起点」标签与落盘的段起点静默不一致）。
  //   `fill("20")` 后屏上仍是 3.6 那张帧：T3 已用同一手法（改输入框不抽帧）证明过 `fill`+`Enter`
  //   **不**触发抽帧 ⇒ 这里等于把 T3 的判别式搬到 `doBuild` 这个调用点上。
  await atSeg.fill("20"); await atSeg.press("Enter");
  await page.locator("#app .footer-nav button").nth(1).click();      // 步 ②
  await waitFor(async () => (await page.locator("#app .panel .frame-wrap").count()) === 1,
                "步 ② 应出现点选层");
  const w2 = page.locator("#app .panel .frame-wrap");
  const bb = await w2.boundingBox();
  const at2 = (fx, fy) => w2.click({ position: { x: bb.width * fx, y: bb.height * fy } });
  await at2(0.6719, 0.4278); await at2(0.9375, 0.4278); await at2(0.8047, 0.5556);
  await waitFor(async () => (await calibState()).canBuild === true, "补段的三点应落到服务端");
  await page.locator("#app .footer-nav button").nth(1).click();      // 步 ③
  ok((await page.locator("#app .panel button").first().textContent())
       .includes("生成补充段（起点 00:03.6）"),
     "补段态按钮文案的起点须是**屏上那张帧**的时刻（E25：输入框已改成 20，起点仍须 00:03.6）");
  // E26：写端点之后**不要**立刻轮询服务端——先等这次写请求的**响应**回来，再开始轮询。
  // G2b/E23 定案：这里保持 Playwright 默认 30s——它是**文档化的第二预算**
  // （waitForResponse 没有 ms 参数；等不到 build（生成补充段）响应会挂到测试超时 = 可读的红）。
  // 统一成 10s 会让「服务端 30s 才回」从超时变假绿。E2 真机跑过后复访。
  const rfBuild = page.waitForResponse((r) => r.url().includes("/api/calib/build"));
  await page.locator("#app .panel button:has-text('生成补充段')").click();
  eq((await rfBuild).status(), 200, "生成补充段应 200——5xx 多半是落盘撞上读句柄（WinError 5）");
  await waitFor(async () => (await calibState()).segments.length === 1, "补段后应出现 1 个补充段");
  const seg = await calibState();
  ok(seg.segments[0].includes("段2（0:04 起）"),
     `补充段串须锚在被抽那张帧（3.6 ⇒「段2（0:04 起）」，服务端 _fmt_mmss），实际 ${seg.segments[0]}`);
  ok((await existingState()).files.includes("calibration_seg2.json"), "补段文件应真落盘（/existing.files 可见）");
  ok((await card.textContent()).includes("段2（"), "段卡应出现补充段行");
  eq(await card.locator(".seg-row").count(), 2, "段卡应两行（主段 + 1 补充段）");

  // E26：同上。删除也是写端点（`del_segment` 走 `os.remove`，Windows 上撞读句柄同样抛 WinError 5）。
  // G2b/E23 定案：这里保持 Playwright 默认 30s——它是**文档化的第二预算**
  // （waitForResponse 没有 ms 参数；等不到 DELETE（删段）响应会挂到测试超时 = 可读的红）。
  // 统一成 10s 会让「服务端 30s 才回」从超时变假绿。E2 真机跑过后复访。
  const rfDel = page.waitForResponse((r) => r.request().method() === "DELETE");
  await card.locator(".seg-row button:has-text('删除')").click();
  eq((await rfDel).status(), 200, "删段应 200——5xx 多半是 os.remove 撞上读句柄（WinError 5）");
  await waitFor(async () => (await calibState()).segments.length === 0, "删段后应无补充段");
  const t2 = page.locator(".el-message").filter({ hasText: "已删除" });   // E21：按文案过滤（原为裸 .last()）
  await waitFor(async () => (await t2.count()) > 0, "删段应弹提示");
  ok((await t2.first().textContent()).includes("已删除"), "删段应弹服务端 msg（含「已删除」）");
  ok(!(await existingState()).files.includes("calibration_seg2.json"), "删段应把文件真删掉");
  eq(await card.locator(".seg-row").count(), 1, "删后应只剩主段一行");

  // ⑬b R9「只覆盖 3 键、绝不整份替换」的判别式（复审 Minor 2）：若写成 `st.value = r`，
  //     `hasPreview`/`points`/`canBuild` 会一起消失 ⇒ 步③ 的预览图随之不见，而此前四条门全绿。
  eq(await page.locator("#app .panel img.preview").count(), 1,
     "删段不该弄丢预览图（写成整份替换时 hasPreview 丢失 ⇒ 预览图消失）");

  // ⚠ 删段后再回步 ③ 的 `pendingSeg` 已经是 false（Task 5 里生成成功就清了）⇒ 按钮仍是
  //   `⚙️ 生成标定`。这一段**不要**再点生成。

  // ⑭ 查看已有标定（R2 的新增面）：9 键里挑有分辨力的几处断
  await page.locator("#app .topbar button:has-text('查看已有标定')").click();
  await waitFor(async () => (await page.locator(".el-dialog").count()) === 1, "应弹出摘要弹窗");
  const dlg = page.locator(".el-dialog");
  // E29（实施者实测加的等待，非简报原文）：弹窗是**打开之后才发** /existing（`watch(modelValue)`
  //   ⇒ 先渲染「读取中…」，回包到了才切成数据分支）⇒ 简报那句只等 `.el-dialog` 出现，紧接着
  //   读 textContent 会撞上「壳在、数据还没到」的窄窗（实测红：`❌ 弹窗应显示「已标定」`，
  //   此刻弹窗里是「读取中…」）。这里等的是**同一个渲染**落地（`v-else-if="data"` 分支出现 ⇒
  //   下面五条判据读的都是同一份回包）。不新增判据、不改判据，纯等待。
  await waitFor(async () => (await dlg.textContent()).includes("状态："),
                "弹窗应加载出 /existing 的数据（而不是停在「读取中…」）");
  ok((await dlg.textContent()).includes("已标定"), "弹窗应显示「已标定」");
  ok((await dlg.textContent()).includes("已存档"), "弹窗应显示参考帧已存档");
  // E27：判据从 `includes("250")` **收紧成** `includes("S 250")`——裸 250 太松：筐口矩形 x/y/w/h、
  //   frameSize、calibT 或 wideHsvNote 里出现 250 同样会绿。`hsvLine` 的输出形状是
  //   `H <h> · S <s> · V <v>`（本任务 Step 1 那段），上限行是 `S 255`/`V 255`，不撞。
  //   ⚠ 250 **不是字面常量**，是 Task 6 块刚写进落盘的值（T6 先 S→250 再 V→120；Task 7 的补段/删段
  //     只动 calibration_seg2.json、不碰主档）⇒ 这条依赖 spec 的**线性顺序**，将来重排段落必须跟着改。
  ok((await dlg.textContent()).includes("S 250"), "弹窗 HSV 行应含 S=250（步 ④ 刚写进去的值）");
  // 复审 F1（控制者指定收紧）：原判据是 `dlg.textContent().includes("calibration.json")`，
  //   那是**假覆盖**——复审做了突变实验：把 `.files` 整段从 DOM 移除后它**仍然绿**（rc=0）。原因是
  //   双通道：同一份 textContent 里「标定文件：<calibPath>」天然含该子串（`_calib_file()` 返回
  //   `<dir>/calibration.json`）⇒ `.files` 的 `v-for` 完全不渲染也照样满足。改成断**计数**：
  //   此刻 work 目录恰有 calibration.json + calibration_ref.png 两个 `calib*`（复审实测 = 2，
  //   移掉 `.files` 块后 = 0）。
  eq(await dlg.locator(".files .el-tag").count(), 2,
     "弹窗应真渲染 work 目录文件清单（2 个 el-tag）——只断 textContent 含 calibration.json 是假覆盖：calibPath 那行天然含该子串");
  ok((await dlg.textContent()).includes("（只有主段）"), "删段后弹窗应报「只有主段」");
  await page.locator(".el-dialog__headerbtn").click();
  await waitFor(async () => !(await page.locator(".el-dialog").first().isVisible()), "关闭后弹窗应隐藏");

  // ⑮ 取消补段 + 换视频自动退出补段态（终审 Critical A 的**出口**）：pendingSeg 此前只有
  //    「生成成功」一个出口 ⇒ 误入补段态后改路径、重抽帧、来回翻页全部无效，只能 F5。
  //    ⚠ **定向复审（第二轮）勘误 1 —— 取消必须发生在步 ③，不能在步 ①**：本块原先在步 ①
  //      取消，而 `addSeg` 刚刚做过**同样两个重置**（step=0 / frameReady=false）⇒ `cancelSeg`
  //      里那两行是**无操作**，各自都测不到。复审实测：单独删掉 `step.value = 0;` ⇒ 全绿 80/rc=0；
  //      单独删掉 `frameReady.value = false;` ⇒ 同样全绿——而后者被删会重新打开 `cancelSeg`
  //      注释里写明的**静默错档**（拿挪动后那帧的点把主标定重建到挪动后的时刻上）。
  //      ⇒ 现在**真走完 ①②③**、在补段按钮已取代主按钮的**危险态**上取消，那两行才与
  //      `addSeg` 的重置**可区分**（步 ① 的重置发生在两步之前，不可能是它的功劳）。
  //    ⚠ **勘误 2 —— 不可达红的断言已删**：进入段原先那条「`'抽帧'` 计数 = 2，应回到步 ①」
  //      与它前面**同选择器同期望值**的 `waitFor` 重复 ⇒ waitFor 永远先超时，那条计数
  //      **不可能成为失败点**（没有判别力；信息由该 waitFor 与 T7 的同款计数覆盖）。
  //    ⚠ **判据纪律（本轮定下）**：每个计数项**前面不许有守同一条件的 waitFor**——否则它永远
  //      不是失败点。需要同步时一律用**另一个信号**做前置守卫：本条用 toast / 顶栏 tag / 帧图。
  //    ⚠ 替身视频用**复制夹具那份**：服务端 POST /api/session 不校验文件是否存在，
  //      但拿真视频走这条路才是「换视频」的真实语义（与将来加校验互相独立）。
  const { copyFileSync } = await import("node:fs");
  const otherVideo = VIDEO.replace(/\.mp4$/, "-b.mp4");
  copyFileSync(VIDEO, otherVideo);
  const segBtn = card.locator("button:has-text('取消补段')");

  // (1) 进入补段态（前置守卫守「回到步 ①」，不计项；下面的计数项是**另一个**条件 ⇒ 仍可成为失败点）
  await card.locator("button:has-text('新增补充段')").click();
  await waitFor(async () => (await page.locator("#app .panel button:has-text('抽帧')").count()) === 2,
                "「新增补充段」应把向导打回步 ①", 3000);
  eq(await segBtn.count(), 1, "补段态应可见「✖ 取消补段」（段卡在任何步骤都渲染）");

  // (2) 真走完 ①②③：抽挪动后的帧 → 点筐口两端 + 球 → 进步 ③（那里按钮才换成「生成补充段」）
  const atSeg2 = page.locator("#app .panel input").first();
  await atSeg2.fill("3.6"); await atSeg2.press("Enter");
  await page.locator("#app .panel button:has-text('抽帧')").first().click();
  await waitFor(async () => (await page.locator("#app .panel img").count()) === 1, "补段抽帧应出帧图");
  await page.locator("#app .footer-nav button").nth(1).click();      // 步 ②
  await waitFor(async () => (await page.locator("#app .panel .frame-wrap").count()) === 1,
                "步 ② 应出现点选层");
  const w3 = page.locator("#app .panel .frame-wrap");
  const bb3 = await w3.boundingBox();
  const at3 = (fx, fy) => w3.click({ position: { x: bb3.width * fx, y: bb3.height * fy } });
  await at3(0.6719, 0.4278); await at3(0.9375, 0.4278); await at3(0.8047, 0.5556);
  await waitFor(async () => (await calibState()).canBuild === true, "补段的三点应落到服务端");
  await page.locator("#app .footer-nav button").nth(1).click();      // 步 ③
  // 前置守卫（不计项）：按钮读作「生成补充段」= 我们**确实站在**那个危险态上（主按钮已被取代）
  await waitFor(async () => (await page.locator("#app .panel button:has-text('生成补充段')").count()) === 1,
                "步 ③ 应处于补段态（按钮读作「生成补充段」而非「生成标定」）");

  // (3) **在步 ③ 取消** —— 下面两条只有在这里才有牙（步 ① 取消时 addSeg 早已做过同样的重置）
  await segBtn.click();
  // E21 纪律：按**文案过滤** + 本文件自己的 waitFor，不要裸 .first()/.last() 位置赌博。
  const t3 = page.locator(".el-message").filter({ hasText: "已取消补段" });
  await waitFor(async () => (await t3.count()) > 0, "「取消补段」应弹提示", 3000);
  eq(await page.locator("#app .panel button:has-text('抽帧')").count(), 2,
     "「取消补段」应回到步 ①（此刻只有 cancelSeg 能造成回到步①——addSeg 的重置在两步之前）");
  ok(await page.locator("#app .footer-nav button").nth(1).isDisabled(),
     "取消补段后「下一步」应禁用（frameReady 被打回 false——否则会拿挪动后那帧的点重建主标定）");
  eq(await segBtn.count(), 0, "「取消补段」应把按钮收掉（pendingSeg 归 false）");
  ok((await t3.first().textContent()).includes("已取消补段"), "「取消补段」应弹「已取消补段」提示");

  // (4) 复入（守卫用 addSeg 自己的 toast：与下面的 segBtn 计数是**两个**条件）
  // G2c/R5：带全角冒号的「回到第①步：」排掉「已取消补段，回到第①步」那重撞车
  //  （cancel toast 结尾无冒号）。⑬ 段的 t1 不动——其前无 cancel toast，改它=顺手加固。
  // G2c 修复轮（2026-09-22，快照+恰+1）：EP 的 .el-message 节点**不随 3s 寿命离场**
  //  ——点击前 DOM 里还留着 ⑬/⑮(1) 两条**同文案** addSeg toast（探针实测换冒号串后
  //  仍匹配 2 条）⇒ 任何 hasText 串都无法只属新 toast，「count>0」会被旧 toast 预先
  //  满足。改为点击前快照计数、等**恰 +1**——只有本次点击的新 toast 能推动计数越过
  //  快照（addSeg 同步弹 toast，click() 返回时新节点已在 DOM）。wall G2d 同款形状；
  //  收紧发生在 waitFor 条件里，ok/eq 条数不动 ⇒ 项数不变（82）。
  const t4 = page.locator(".el-message").filter({ hasText: "回到第①步：" });
  const t4n0 = await t4.count();
  await card.locator("button:has-text('新增补充段')").click();
  await waitFor(async () => (await t4.count()) === t4n0 + 1,
                "再点「新增补充段」应弹提示", 3000);
  eq(await segBtn.count(), 1, "再点「新增补充段」应再次可见「取消补段」");

  // (5) 换视频 ⇒ 必须把它清掉（onPathChange）：否则带着补段态切到另一个视频，同一个死路换个入口
  //     再来一遍。守卫用**顶栏 tag**（另一个信号：新视频的 st 已落地）——若守 segBtn 自己，下面
  //     那条 eq 就不可达红（复审上轮抓的正是这个形状）。改路径放在**最末**：服务端会重置该路径的页面态。
  await page.locator("#app .topbar input").fill(otherVideo);
  await page.locator("#app .topbar input").press("Enter");
  await waitFor(async () => (await page.locator("#app .topbar .el-tag").first().textContent()).trim() === "未标定",
                "换视频后顶栏应翻成「未标定」（新视频还没标定 ⇒ 新会话已落地）", 5000);
  eq(await segBtn.count(), 0, "换视频路径后应自动退出补段态（onPathChange 清 pendingSeg）");

  ok(errs.length === 0, `页面报错：${errs.join(" | ")}`);
  console.log(`✅ 全过 ${pass} 项`);
} finally {
  await browser.close();
}
