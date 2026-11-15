import { createApp } from "vue";
import ElementPlus from "element-plus";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import "element-plus/dist/index.css";
import "element-plus/theme-chalk/dark/css-vars.css";
import * as Icons from "@element-plus/icons-vue";
import "../shared/theme.css";
import { mountTabbar } from "../shared/tabbar.js";
import WallBay from "./WallBay.vue";

mountTabbar("wall.html", "");
const app = createApp(WallBay);
// locale：EP 组件默认按钮文案（不装是英文 OK/Cancel——2026-09-20 小任务）。
// e2e 点消息框一律语义选择器（primary / :not(primary)），不按文案选。
app.use(ElementPlus, { locale: zhCn });
for (const [k, c] of Object.entries(Icons)) app.component(k, c);
app.mount("#app");
