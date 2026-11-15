// 媒体 URL 拼装。三条硬规矩（都来自后端实现与旧界面的坑）：
//  · /media/clip 每次覆盖同一个 work/<视频>/preview.mp4 ⇒ URL **必须**带 &v=，
//    否则浏览器命中缓存、改完前后秒还是老画面；
//  · /media/video 支持 Range（拖进度条靠它），且只允许播放当前会话的视频；
//  · path 用绝对路径（后端 _media_url 就是这么生成的），白名单由服务端判。
const bust = () => String(Date.now());

export function clipUrl(videoPath, t, before, after) {
  const q = new URLSearchParams({
    path: videoPath, t: String(t), before: String(before), after: String(after),
    v: bust(),
  });
  return `/media/clip?${q}`;
}

export function videoUrl(videoPath) {
  return `/media/video?${new URLSearchParams({ path: videoPath })}`;
}
