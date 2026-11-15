// 长任务的浏览器记忆：检测要跑几分钟，刷新页面不能把进度弄丢——重开后
// 用 JobMemory 里的 jobId 接上轮询（后端 job 只在内存里，重起服务会 404，
// 那时丢弃记录、提示重跑）。
//
// store 可注入（默认 localStorage）：Node 下没有 localStorage，直接
// 触碰会 ReferenceError；测试也靠注入假 store 跑。
const KEY = "bbh.runningJob";

function safeStorage() {
  try { return globalThis.localStorage || null; } catch { return null; }
}

export function createJobMemory(store = safeStorage()) {
  const read = (k) => {
    try { return store ? store.getItem(k) : null; } catch { return null; }
  };
  const write = (k, v) => {
    try { if (store) store.setItem(k, v); } catch { /* 配额/隐私模式：不影响功能 */ }
  };
  const drop = (k) => {
    try { if (store) store.removeItem(k); } catch { /* 同上 */ }
  };
  return {
    remember(rec) { write(KEY, JSON.stringify(rec)); },
    load() {
      const raw = read(KEY);
      if (!raw) return null;
      try {
        const rec = JSON.parse(raw);
        return rec && typeof rec === "object" && rec.jobId ? rec : null;
      } catch { return null; }        // 他程序写坏 → 当作没有
    },
    clear() { drop(KEY); },
  };
}
