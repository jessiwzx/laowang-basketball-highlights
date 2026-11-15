"""剪辑：片段规划（纯函数）+ ffmpeg 切片/拼接。"""
import glob
import os
import re
import shutil
import subprocess
import sys

import cv2
import numpy as np


def _ffmpeg_exe() -> str:
    """PATH 里有 ffmpeg 用之，否则退回 imageio-ffmpeg 捆绑二进制。

    **冻结（打包版）时反过来**：直接用包里那份，连 PATH 都不问。打包版的意义是
    「同一份东西在哪台机器上都一样」，而 PATH 里那份是变量——装了旧 ffmpeg 的
    机器会静默换一个二进制去编码，编出来的段与常规不同（而拼接靠 `-c:v copy`
    要求各段编码参数一致），报错也来自 ffmpeg 的 stderr、极难归因到「这台机器
    上装过 ffmpeg」。包里那份（imageio-ffmpeg 构建）也正是验证过 libx264 参数的
    那一份。见 tests/test_ffmpeg_exe.py::test_frozen_run_ignores_the_ffmpeg_on_path
    """
    if getattr(sys, "frozen", False):
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def plan_clips(events_settings: list[tuple[float, float, float]],
               duration: float) -> list[tuple[float, float]]:
    """events_settings: [(进球时刻, 前 N 秒, 后 M 秒)] → 裁剪越界后的
    [(start, end)] 列表，按时间排序。**不合并重叠区间**——每个进球独立成段，
    相邻片段画面有重叠属预期（实测反馈：用户不要合并）。"""
    return sorted((max(0.0, t - before), min(duration, t + after))
                  for t, before, after in events_settings)


def _run(cmd: list[str], timeout: float | None = 600):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"ffmpeg 超时（{timeout}s）:\n{cmd}") from e
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败:\n{cmd}\n{r.stderr[-800:]}")


def probe_duration(src: str) -> float | None:
    """ffmpeg -i 解析 stderr 的 Duration 行（OpenCV 打不开容器时的兜底）。"""
    try:
        r = subprocess.run([_ffmpeg_exe(), "-hide_banner", "-i", src],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
    except subprocess.TimeoutExpired:
        return None
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr or "")
    if not m:
        return None
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def grab_frame_ffmpeg(src: str, t: float):
    """ffmpeg 在 t 秒解一帧（MJPG 编码走管道，imdecode 解码，绕开中文路径）。
    成功返回 BGR 帧，失败返回 None。"""
    try:
        r = subprocess.run([_ffmpeg_exe(), "-ss", f"{t:.3f}", "-i", src,
                            "-frames:v", "1", "-f", "image2pipe", "-vcodec",
                            "mjpeg", "-"], capture_output=True, timeout=120)
    except subprocess.TimeoutExpired:
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)


def transcode_for_detection(src: str, dst: str):
    """把 OpenCV 解不了的视频转成 H.264 MP4（无音轨，检测专用）。
    先写 .part 再原子替换——转码中断不会留下半截文件被误当成品。"""
    part = dst + ".part"
    _run([_ffmpeg_exe(), "-y", "-i", src, "-an", "-c:v", "libx264",
          "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
          "-f", "mp4", part],   # .part 后缀藏掉了扩展名，必须显式给封装格式
         timeout=None)   # 长视频转码远超 600s，不设上限
    os.replace(part, dst)


def cut_clip(src: str, start: float, dur: float, out: str):
    """精确切片：统一转码 H.264/AAC（保证拼接不花屏、手机可播）。
    起点恒 >0（1ms）：首个进球 t<前窗秒数时 start=0，HEVC+AAC 手机源
    -ss 0 触发 mp4 muxer 负 DTS 死路（同 app._cut_preview 护栏）。"""
    _run([_ffmpeg_exe(), "-y", "-ss", f"{max(0.001, start):.3f}", "-i", src,
          "-t", f"{dur:.3f}",
          "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
          "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
          out])


def concat_clips(clips: list[str], out: str):
    """拼接已统一转码的片段（流复制，秒级完成）。"""
    list_file = out + ".txt"
    with open(list_file, "w", encoding="utf-8") as f:
        for c in clips:
            esc = os.path.abspath(c).replace("'", "'\\''")
            f.write(f"file '{esc}'\n")
    try:
        _run([_ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0", "-i", list_file,
              "-c", "copy", out])
    finally:
        if os.path.exists(list_file):
            os.remove(list_file)


def safe_filename(name: str) -> str:
    """用户文本（标签名等）→ 安全文件名：替换 Windows 非法字符，去首尾空白。"""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name).strip(" ._")
    return cleaned or "x"


def build_highlight(src: str, clips: list[tuple[float, float]], work_dir: str,
                    out_name: str = "highlight.mp4"):
    """plan_clips 的输出 → 集锦 MP4（out_name 可指定，如按标签命名）。返回输出路径。"""
    if not clips:
        raise ValueError("没有选中任何片段")
    clips_dir = os.path.join(work_dir, "clips")
    os.makedirs(clips_dir, exist_ok=True)
    for old in glob.glob(os.path.join(clips_dir, "*.mp4")):
        os.remove(old)
    files = []
    for i, (start, end) in enumerate(clips):
        p = os.path.join(clips_dir, f"{i:03d}_{int(start):06d}.mp4")
        cut_clip(src, start, end - start, p)
        files.append(p)
    out = os.path.join(work_dir, safe_filename(os.path.splitext(out_name)[0]) + ".mp4")
    if len(files) == 1:
        os.replace(files[0], out)
    else:
        concat_clips(files, out)
    return out
