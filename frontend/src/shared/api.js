// 后端契约：成功 200 + JSON；失败 {error: "中文文案"}。
// 所有错误（400 用户可见 / 409 冲突 / 500 意外）都抛 Error(error 原文)，
// 调用方一律 `try { … } catch (e) { ElMessage.error(e.message) }`——
// 文案就是要给用户看的，不在这里改写。
async function request(method, url, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    opt.headers["Content-Type"] = "application/json";
    opt.body = JSON.stringify(body);
  }
  const r = await fetch(url, opt);
  let data = null;
  try { data = await r.json(); } catch { /* 空 body 或非 JSON */ }
  if (!r.ok) throw new Error((data && data.error) || `${r.status} ${r.statusText}`);
  return data;
}

export const api = {
  get: (url) => request("GET", url),
  post: (url, body) => request("POST", url, body),
  patch: (url, body) => request("PATCH", url, body),
  del: (url, body) => request("DELETE", url, body),
};

export function qs(params) {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== "") u.set(k, v);
  }
  const s = u.toString();
  return s ? "?" + s : "";
}
