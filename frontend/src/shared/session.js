import { api } from "./api.js";

// 会话态全在服务端：前端拿到的行数据/统计/证据都是权威值，不自己算。
// 任何写操作返回的也是同一份 state（就地替换即可）。
export const store = {
  state: null,
  listeners: new Set(),
  set(s) {
    this.state = s;
    this.listeners.forEach((f) => f(s));
  },
  on(f) { this.listeners.add(f); },
  off(f) { this.listeners.delete(f); },
  async open(videoPath) { this.set(await api.post("/api/session", { videoPath })); },
  async refresh() { this.set(await api.get("/api/state")); },
};
