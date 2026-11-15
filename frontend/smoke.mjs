// frontend/smoke.mjs —— 冒烟：三页能开、Vue 真挂载、tab 高亮正确、点击能跳、无 JS 报错
//
// 运行方式（playwright 不在 frontend/package.json 里，按需一次性安装）：
//   1) 起服务（cwd 必须是项目根）——⚠️ 不要用 `-m bbh.web`：
//      该入口会调 migrate_root_calibrations()，把仓库根的标定档认领走并 os.remove 掉根文件，
//      而那几个档一个都没有 git 副本 ⇒ 不可逆的数据丢失。
//      改用临时脚本 uvicorn.run(create_app(), host="127.0.0.1", port=<p>)。
//   2) 一次性装依赖：cd frontend && npm i -D playwright
//      （浏览器本机已装，可加 PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 跳过下载）
//   3) BBH_BASE=http://127.0.0.1:<p> node smoke.mjs
//   注：ESM 解析器不认 NODE_PATH，所以不能靠 npx 缓存里的 playwright 直接跑这个文件。
//
// BASE 默认 7860 是项目唯一的端口：bbh/web/__main__.py 硬编码 port=7860，
// run_app.bat（用户唯一启动入口）走的就是它；全仓除本文件外零处 7861。
// 换端口起服务时用 BBH_BASE 覆盖，别改这里的默认值——硬编码别的端口会让任何
// 照项目文档起服务的人连不上，并把「连不上」误报成「页面坏了」。
import { chromium } from "playwright";

const BASE = process.env.BBH_BASE || "http://127.0.0.1:7860";
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e)));

  // 不等 networkidle：Plan B/C 的首屏会起 0.8s 轮询，永远凑不出 500ms 静默窗口，
  // 等下去只会假红超时。改用 domcontentloaded + 显式等下面的同步屏障。
  // 三页地标：剪辑台（Plan B）、出手人墙（Plan C）、标定页（Plan D）都是 SFC 主体、
  // 页内没有 h2 ⇒ 地标一律取各自顶栏里的特征元素。地标只有 app.mount() 真跑成功
  // 才会出现——地标换掉，这条约束不换。
  for (const [file, label, heading, probe] of [
        ["edit.html", "剪辑台", null, "input"],
        ["wall.html", "出手人墙", null, "tag"],
        ["calib.html", "标定设置", null, "calibtag"]]) {
    await page.goto(`${BASE}/${file}`, { waitUntil: "domcontentloaded" });

    // 同步屏障：mountTabbar() 在 app.mount() 之前、且同在入口模块那段同步代码里执行，
    // 所以 tabbar 一出现，app.mount() 就已经跑完（成功或失败都跑完了）。
    await page.locator(".tabbar a.on").waitFor();

    if (heading) {
      // Vue 真挂载：h2 出自骨架组件模板，只有 createApp().mount("#app") 真跑成功
      // 才会出现在 #app 里；tabbar 是直接 prepend 到 body 的、与 Vue 无关。
      // 少了这条，app.mount() 被删或抛错时脚本照样全绿（假绿）。
      const head = page.locator("#app h2");
      if (!(await head.count())) {
        throw new Error(`${file}: Vue 没挂载——#app 里没有 h2（只有 tabbar），app.mount 没跑成功`);
      }
      const h = (await head.textContent()).trim();
      if (h !== heading) throw new Error(`${file}: 页面标题错，期望「${heading}」实际「${h}」`);
    } else if (probe === "input") {
      // 剪辑台：.topbar 里的路径输入框只可能由 EditBay.vue 渲染出来，
      // 同样是「app.mount 真跑成功」的判据。
      const input = page.locator("#app .topbar input");
      if (!(await input.count())) {
        throw new Error(`${file}: Vue 没挂载——#app 里没有 .topbar（app.mount 没跑成功）`);
      }
      const ph = await input.first().getAttribute("placeholder");
      if (!(ph || "").includes("填路径自动载入")) {
        throw new Error(`${file}: 剪辑台地标不对，路径输入框 placeholder=「${ph}」`);
      }
    } else if (probe === "calibtag") {
      // 标定页（Plan D）：顶栏「已标定/未标定」标签只可能由 CalibWizard.vue 渲染
      // （tabbar 是 body.prepend 的、与 Vue 无关）。无会话时它也在（未标定）——
      // GET /api/calib/state 没视频路径也 200 ⇒ 冒烟不依赖会话态。
      const tag = page.locator("#app .topbar .el-tag");
      if (!(await tag.count())) {
        throw new Error(`${file}: Vue 没挂载——#app 里没有 .topbar .el-tag`);
      }
      const t = (await tag.first().textContent()).trim();
      if (t !== "已标定" && t !== "未标定") {
        throw new Error(`${file}: 标定页地标不对，顶栏标签=「${t}」（应为 已标定/未标定）`);
      }
    } else {
      // 出手人墙（Plan C）：顶栏「未归 n」计数 tag 只可能由 WallBay.vue 渲染
      // （tabbar 是 body.prepend 的、与 Vue 无关，不能当地标——见文件头注释）。
      // 无会话时它也在（未归 0），所以冒烟不依赖会话态。
      const tags = page.locator("#app .topbar .el-tag");
      if (!(await tags.count())) {
        throw new Error(`${file}: Vue 没挂载——#app 里没有 .topbar .el-tag`);
      }
      const t1 = (await tags.nth(1).textContent()).trim();
      if (!t1.startsWith("未归")) {
        throw new Error(`${file}: 墙页地标不对，第二枚 tag=「${t1}」（应为 未归 n）`);
      }
    }

    const on = await page.locator(".tabbar a.on").textContent();
    if (!on.includes(label)) throw new Error(`${file}: tab 高亮错，实际「${on}」`);
    const n = await page.locator(".tabbar a").count();
    if (n !== 3) throw new Error(`${file}: tab 数应为 3，实际 ${n}`);
    // 只能捉「被挤下去」（例如误塞进带 padding 的容器里）：页面加载后未滚动过，
    // position:sticky 被删或改成 static 时 tabbar 依旧在文档顶部、top 同样是 0，
    // 所以这条证明不了 sticky 生效。
    const top = await page.locator(".tabbar").evaluate((el) => el.getBoundingClientRect().top);
    if (top !== 0) throw new Error(`${file}: tab 条没贴在顶部（top=${top}）`);
  }

  await page.goto(`${BASE}/edit.html`, { waitUntil: "domcontentloaded" });
  await page.locator('.tabbar a:has-text("出手人墙")').click();
  await page.waitForURL("**/wall.html");
  if (errs.length) throw new Error("页面报错：" + errs.join(" | "));
  console.log("✅ 冒烟通过：三页可开、Vue 真挂载、tab 高亮正确、可跳转、无 JS 报错");
} finally {
  // 断言失败也要关掉浏览器；异常不吞，照旧往上抛 ⇒ 非零退出。
  await browser.close();
}
