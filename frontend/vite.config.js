import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";

// 构建期自检：vue 必须解析到**带运行时编译器**的构建，否则直接让 `npm run build` 失败。
// 为什么必须挡在构建上：这行 alias 一旦没了，症状是**静默白页**（见下面 resolve.alias 的注释），
// 而这条链上没有任何会自动触发的东西——pytest 不碰前端产物，用户机器运行时不装 Node。
// 所以唯一能兜住的时机就是「发布产物的唯一路径」= build 本身：把「静默白页」变成「响亮的构建失败」。
//
// 判据是**行为**、不是字符串：用 Vite 自己的解析器拿到 vue 的真实落地文件（走的就是打包时那套
// alias/conditions，所以不存在「声明了 alias 但解析结果不是它」的缝隙），真调一次 compile() 编译模板。
// vue 的 runtime-only 构建和完整构建**导出名完全相同**（实测：两边 export 集合差集为空），
// 唯一的差别是 compile("<div/>") 的返回值——完整构建返回渲染函数，runtime-only 返回 undefined。
// 所以只能靠行为判，任何「看名字/看路径/看导出列表」的写法都会假绿。
const assertVueRuntimeCompiler = {
  name: "bbh:assert-vue-runtime-compiler",
  enforce: "pre",
  async buildStart() {
    const r = await this.resolve("vue", undefined, { skipSelf: true });
    if (!r) throw new Error("前端构建自检失败：解析不到 vue——页面会静默白屏。");
    const mod = await import(pathToFileURL(r.id).href);
    if (typeof mod.compile("<div/>") !== "function") {
      throw new Error(
        "前端构建自检失败：vue 被解析到**不含运行时编译器**的构建，"
        + "三页正文会静默白屏（无 pageerror、控制台干净、Vue 照样挂载，只是渲染出空 vnode）。\n"
        + `  实际解析到：${r.id}\n`
        + "  修法：确保 vite.config.js 里有 resolve.alias 把 vue 指到 \"vue/dist/vue.esm-bundler.js\""
        + "（三个入口 main.js 用的是 template: 字符串，必须有运行时编译器）。");
    }
  },
};

// 三入口多页构建，产物直接覆盖 bbh/static（Python 服务从那儿挂静态）。
// 构建产物入库，运行时装不装 Node 都无所谓。
export default defineConfig({
  plugins: [assertVueRuntimeCompiler, vue()],
  // ⚠️ 这一行看着多余，但**删不得**——删掉零测试变红（冒烟脚本不在 pytest 里，pytest 完全不碰前端产物），
  // 症状只在浏览器里现形，而且是静默的：
  // `vue` 的包入口（exports["."].import.default）是 dist/vue.runtime.esm-bundler.js，**只有运行时、没有模板编译器**；
  // 而三个入口 main.js 用的是 `template:` 字符串（需要运行时编译）。两者一撞，createApp().mount() 会**成功**，
  // 只是渲染出空 vnode——页面上 `<div id="app" data-v-app=""><!----></div>`，正文全白。
  // 那句「runtime compilation is not supported」警告是 dev-only，**生产构建被剥掉** ⇒ 页面白、控制台干净、
  // 无 pageerror。2026-09-19 就是靠这个白页骗过了 T10 验收（旧冒烟断言的 tab 文案/条数/top/跳转全由
  // mountTabbar 的 document.body.prepend 提供，与 Vue 无关，所以整条 Vue 链死掉也照样打印「✅ 三页可开」）。
  // Plan B/C 只要继续写 `template:` 字符串，删掉这行就会再次三页全白——留着。
  resolve: { alias: { vue: "vue/dist/vue.esm-bundler.js" } },
  build: {
    outDir: resolve(__dirname, "../bbh/static"),
    emptyOutDir: true,
    rollupOptions: {
      input: {
        edit: resolve(__dirname, "edit.html"),
        wall: resolve(__dirname, "wall.html"),
        calib: resolve(__dirname, "calib.html"),
      },
    },
  },
});
