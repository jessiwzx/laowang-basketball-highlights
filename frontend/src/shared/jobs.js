import { api } from "./api.js";

// 长任务轮询：POST 启动拿 jobId → 0.8s 一次 GET /api/jobs/{id}，done 为止。
// 用轮询不用 SSE：断线刷新后还能接着看，服务端实现也最简（单用户本机工具）。
export const POLL_MS = 800;

export async function startJob(startUrl, body) {
  const { jobId } = await api.post(startUrl, body);
  return jobId;
}

// 跟随一个 job 到终态并取结果。onTick 每轮拿一次 job 对象（{pct,msg,state,done}）。
// 单独拆出来是为了「刷新页面后接着跟随」：jobId 存在浏览器里，
// 重开页面用 followJob 接上，而不是重新起一个任务（会撞 409）。
export async function followJob(jobId, onTick, { intervalMs = POLL_MS } = {}) {
  for (;;) {
    const j = await api.get(`/api/jobs/${jobId}`);
    if (onTick) onTick(j);
    if (j.done) {
      if (j.state === "error") throw new Error(j.error || "任务失败");
      return await api.get(`/api/jobs/${jobId}/result`);
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

export async function runJob(startUrl, body, onTick) {
  return followJob(await startJob(startUrl, body), onTick);
}
