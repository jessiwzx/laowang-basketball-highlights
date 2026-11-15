"""抖音向二次剪辑：「25s 精选（4s 钩子）」成片。

结构（2026-11-01 用户定稿，`work/2026-10-30_1/repack-C-hook4s.mp4` 是同款）：

    0.0 ─ 4.0   钩子：3 个球各自「穿网前 1.33s → 穿网」，每球 1.33s
    4.0 ─ 25.0  正片：3 个球各 7s（真实穿网前 5s + 穿网后 2s）

球数不足 3 时钩子按 `4/N` 均分、正片仍是每球 7s（1 球 = 11.0s，2 球 = 18.0s）。
**为什么钩子要给到 4 秒**：2 秒判定点落在第二个球飞向篮筐的途中、3 秒内已经进
了两个球，而 4 秒钩子的三条镜头（1.33s 一条）恰好是「起跳最高点 → 穿网」，
再短就只剩一颗球自己飞进筐、看不出是人投的。

三条硬约束，改本文件前先读：
① **横版不 import vrender / pipeline**：横版分支只读源视频 +
   `calibration*.json` + `events.json`，走自己的 ffmpeg 命令。出问题直接删
   本文件 + `app.py` 的一个分支即可完全回退，现有渲染链一个字节都不受影响。
   （竖版分支做不到零依赖——跟球运镜只实现在 `vrender` 里，重写一遍不现实。
   那一路是**延迟 import**，横版路径仍然不加载 vrender；要回退就删
   `build_hook_reel_vertical` 与 `vertical` 分派，现有集锦/单条零影响。）
② **不复用已渲染的 `single-横-*.mp4`**：那些是二次编码产物，再拼一次就是三
   次编码；从源片直接切只编码一次，画质反而最好。而且进球位置由筐口扫描实
   测定位，不吃导出时 before/after 设置的漂移。
③ **进球时刻不信任 `events.json` 的 t**：那是网信号峰值时刻，实测比真实穿网
   晚约 0.17s（2026-10-30 全场 24 条一致）。钩子要卡在穿网那一刻，0.17s 就是
   一整帧多的偏差，所以逐球扫筐口橙像素取「球首次进入筐口」的时刻当锚点。
"""
from __future__ import annotations

import glob
import os
import re
import subprocess

import cv2
import numpy as np

from . import calibration as _calib
from .editor import _ffmpeg_exe

# —— 模板常量（2026-11-01 用户裁定「写死，先跑通」）——
HOOK_SEC = 4.0        # 片头钩子总时长（每球均分）
BODY_BEFORE = 5.0     # 正片：真实穿网前
BODY_AFTER = 2.0      # 正片：真实穿网后
BALLS_PER_REEL = 3    # 一条成片几个球
BITRATE_K = 8000      # 目标码率（抖音建议 8~12 Mbps）

# 钩子单球时长下限：再短就看不出是「人投进的」，会变成一颗球自己飞进筐
HOOK_MIN = 0.9

# 穿网锚点的实测兜底偏移：events.t 比真实穿网晚这么多（10-30 全场 24 条实测
# 4.83s vs before=5.0）。扫描失败（多机位段筐口对不上 / 画面里橙像素本来就多）
# 时用 event_t - 该值，并在结果里如实标注。
FALLBACK_HIT_LAG = 0.17

# 段间音频交叉淡入淡出（秒）：段是硬切的，不加会有「咔」的爆音
AFADE = 0.015

# 竖屏片段恒 30fps（vrender._v_enc 的 `-r 30`）。裁剪一律**按帧号**算：按秒裁
# 会因「该帧 PTS 略小于起点」被排除、每段少 1 帧，三球拼下来 24.90s ≠ 承诺的
# 25.0s（2026-11-01 实测出来的，不是理论担心）。
_VCLIP_FPS = 30


# ---------- 探测 ----------
_RX_DUR = re.compile(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)")
_RX_VID = re.compile(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})")
_RX_FPS = re.compile(r"(\d+(?:\.\d+)?)\s*fps")


def probe_video(src: str) -> dict:
    """一次 ffmpeg -i 解析出宽高/fps/时长/有无音轨。

    不用 cv2 探：Opencv 对 fps 字段偶尔回 0（VFR 源），而下面所有切段长度都
    依赖它；ffmpeg 的 stderr 是唯一同时给全四项的地方。"""
    try:
        r = subprocess.run([_ffmpeg_exe(), "-hide_banner", "-i", src],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"探测视频超时：{src}") from e
    err = r.stderr or ""
    m_dur = _RX_DUR.search(err)
    m_vid = _RX_VID.search(err)
    if not m_dur or not m_vid:
        raise RuntimeError(f"读不出视频参数（不是视频文件？）：{src}\n{err[-400:]}")
    h, mi, s = m_dur.groups()
    m_fps = _RX_FPS.search(err)
    fps = float(m_fps.group(1)) if m_fps else 30.0
    if not 1.0 <= fps <= 240.0:       # 解析到怪值（如 tbr 行）就退回 30
        fps = 30.0
    return {"w": int(m_vid.group(1)), "h": int(m_vid.group(2)),
            "fps": fps,
            "duration": int(h) * 3600 + int(mi) * 60 + float(s),
            "audio": "Audio:" in err}


# ---------- 穿网锚点 ----------
def find_hit_time(src: str, hoop_rect, hsv_low, hsv_high, t_hint: float,
                  back: float = 1.5, ahead: float = 0.8) -> tuple[float | None, dict]:
    """在 `t_hint` 附近按筐口橙像素定位「球穿过筐口」的时刻。

    判据与检测用的是同一套 HSV 阈值（`calibration.json` 的 hsv_low/high），
    只统计筐口矩形内的橙像素数。

    **取峰值时刻，不取首次越阈**——实测一条完整曲线（2026-10-30 16:36.9，
    `work/2026-10-30_1`）是这样：

        t-0.57   41     球还在筐上方远处
        t-0.53  116  ← 球到达筐口上沿（首次越阈）
        t-0.17  164  ← 球正好在筐口正中（峰值）
        t+0.03   77     球已穿下去
        t+0.10   33     回基线

    首次越阈比真穿网早 0.36s，钩子结尾会停在「球悬在筐上方」；而峰值时刻正
    是观众眼里的「球进筐」那一帧，也与已验收的 `repack-C-hook4s.mp4` 对齐
    （它的钩子结束在片段内 4.83s，即 event_t − 0.17）。干扰更难误触：远处
    路过的东西在 44×17 的筐口里凑不出 164 个橙像素。

    返回 `(t_hit | None, info)`；`info` 进结果清单，用于「这个球是扫出来的
    还是用的兜底偏移」（不写清楚的话，钩子剪到空镜头上无从归因）。
    """
    x, y, w, h = (int(round(v)) for v in hoop_rect)
    lo = np.array(hsv_low, dtype=np.uint8)
    hi = np.array(hsv_high, dtype=np.uint8)
    start = max(0.0, float(t_hint) - back)
    end = float(t_hint) + ahead
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        return None, {"why": "OpenCV 打不开视频"}
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(start * fps)))
        series = []
        idx = int(round(start * fps))
        while True:
            ok, fr = cap.read()
            if not ok:
                break
            t = idx / fps
            if t > end:
                break
            roi = fr[max(0, y):y + h, max(0, x):x + w]
            if roi.size:
                mask = cv2.inRange(cv2.cvtColor(roi, cv2.COLOR_BGR2HSV), lo, hi)
                series.append((t, int(cv2.countNonZero(mask))))
            idx += 1
    finally:
        cap.release()
    if len(series) < 4:
        return None, {"why": "扫描窗口内取不到帧"}
    vals = np.array([v for _t, v in series], dtype=float)
    base = float(np.median(vals))
    thr = max(base + 12.0, base * 1.6)
    peak = float(vals.max())
    info = {"base": round(base, 1), "peak": int(peak), "thr": round(thr, 1)}
    if peak <= thr:
        # 橙像素全程没跳起来：这一段的筐口对不上（多机位换过镜头）或球太小
        info["why"] = "筐口橙像素无跳变"
        return None, info
    # 只在「越阈帧」里挑峰值：整窗最大值可能来自窗口边缘的无关画面
    # （球员走过、记分牌反光），而球在筐口正中那几帧一定是窗口内最强的。
    hit_i = max(range(len(series)),
                key=lambda i: (series[i][1] if series[i][1] > thr else -1))
    t_hit, v_hit = series[hit_i]
    first = next((t for t, v in series if v > thr), None)
    info.update(by="scan", at_count=int(v_hit),
                lead=round(t_hit - first, 2) if first is not None else None)
    return float(t_hit), info


# ---------- 切段规划 ----------
def _clamp_window(hit: float, before: float, after: float,
                  duration: float) -> tuple[float, float]:
    """[hit-before, hit+after] 夹进 [0, duration]，**段长不变**（两侧补）。

    段长必须恒定：钩子和正片的总时长就是这个模板的全部承诺（25.0s），某一球
    因为贴着片头/片尾被截短，成片时长就会漂，用户拿去比对就无意义了。"""
    want = before + after
    start = hit - before
    end = hit + after
    if start < 0:
        end -= start
        start = 0.0
    if end > duration:
        start -= (end - duration)
        end = duration
    start = max(0.0, min(start, max(0.0, duration - want)))
    end = min(duration, start + want)
    return start, end


def plan_segments(hits: list[float], duration: float,
                  hook_sec: float = HOOK_SEC,
                  before: float = BODY_BEFORE,
                  after: float = BODY_AFTER) -> list[tuple[float, float]]:
    """球池 → 切段计划：钩子段（按时间序）在前，正片段在后。

    钩子段**收在穿网时刻**而不是从它开始——这是钩子能卡住 2s 判定点/让第 3 个
    球正好在钩子末尾穿网的全部原因。钩子内部顺序沿用球池顺序（= 时刻序），
    正片同理，两段顺序一致：观众在钩子里看到的第一个球，正片里也是第一个。"""
    n = len(hits)
    if not n:
        return []
    # n≤3（上层每 3 球一条）时恒等于 hook_sec/n＝1.33s；n>3 只可能来自手工调
    # 参，按均分走、允许低于 HOOK_MIN（总时长承诺优先于单镜头可读性）。
    hook_len = max(HOOK_MIN, hook_sec / n)
    segs = []
    for hit in hits:
        segs.append(_clamp_window(hit, hook_len, 0.0, duration))
    for hit in hits:
        segs.append(_clamp_window(hit, before, after, duration))
    return segs


# ---------- 渲染 ----------
def build_hook_reel(src: str, hits: list[float], work_dir: str, out_name: str,
                    *, hook_sec: float = HOOK_SEC, before: float = BODY_BEFORE,
                    after: float = BODY_AFTER, bitrate_k: int = BITRATE_K) -> str:
    """球池（真实穿网时刻列表）→ 一条 25s 横版成片，返回成品路径。

    **一次编码**：所有段各自 `-ss`/`-t` 从同一个源文件独立解码，经
    `filter_complex` 的 concat 合成后只走一遍 libx264。用「先切小段再流复制
    拼接」是两次编码，8 Mbps 下画面会明显软一档。
    """
    if not hits:
        raise ValueError("没有选中任何进球")
    info = probe_video(src)
    duration = info["duration"]
    segs = plan_segments(hits, duration, hook_sec, before, after)
    if not segs:
        raise ValueError("没有切出任何片段")
    fps = info["fps"]
    os.makedirs(work_dir, exist_ok=True)
    out = os.path.join(work_dir, out_name)
    if not out.endswith(".mp4"):
        out += ".mp4"

    inputs: list[str] = []
    parts: list[str] = []
    for i, (start, end) in enumerate(segs):
        # `-ss` 放在 `-i` 前面 = 输入侧 seek（按关键帧索引跳，秒级）；放后面
        # 会把源从头解到该点——100 分钟的场次要等十几分钟，且 8 段累积。
        inputs += ["-ss", f"{max(0.001, start):.3f}",
                   "-t", f"{max(0.02, end - start):.3f}", "-i", src]
        d = max(0.02, end - start)
        parts.append(
            f"[{i}:v]trim=duration={d:.3f},setpts=PTS-STARTPTS,"
            f"fps={fps:g},setsar=1,format=yuv420p[v{i}]")
        if info["audio"]:
            # 每段都做 15ms 淡入淡出：段与段是硬切，波形不连续会「咔」一声，
            # 一条冲量里有 6 个接缝，攒起来非常明显。
            fade_out = max(0.0, d - AFADE)
            parts.append(
                f"[{i}:a]atrim=duration={d:.3f},asetpts=PTS-STARTPTS,"
                f"afade=t=in:st=0:d={AFADE},"
                f"afade=t=out:st={fade_out:.3f}:d={AFADE}[a{i}]")
    n = len(segs)
    parts.append("".join(f"[v{i}]" for i in range(n))
                 + f"concat=n={n}:v=1:a=0[vout]")
    if info["audio"]:
        parts.append("".join(f"[a{i}]" for i in range(n))
                     + f"concat=n={n}:v=0:a=1[aout]")

    cmd = [_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error"] + inputs + [
        "-filter_complex", ";".join(parts), "-map", "[vout]"]
    if info["audio"]:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-ar", "44100"]
    cmd += [
        "-c:v", "libx264",
        "-b:v", f"{int(bitrate_k)}k",
        "-minrate", f"{int(bitrate_k * 0.75)}k",
        "-maxrate", f"{int(bitrate_k * 1.25)}k",
        "-bufsize", f"{int(bitrate_k * 2)}k",
        "-preset", "slow", "-pix_fmt", "yuv420p", "-r", f"{fps:g}",
        "-movflags", "+faststart", out,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=None)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("ffmpeg 超时") from e
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败：{(r.stderr or '')[-600:]}")
    if not os.path.exists(out) or os.path.getsize(out) < 1024:
        raise RuntimeError("ffmpeg 没有产出文件")
    return out


def build_hook_reel_vertical(src: str, hits: list[float], work_dir: str,
                             out_name: str, *,
                             hook_sec: float = HOOK_SEC,
                             before: float = BODY_BEFORE,
                             after: float = BODY_AFTER,
                             cfg=None) -> str:
    """球池（真实穿网时刻）→ 一条 25s **竖屏**成片（跟球运镜、全程常速）。

    与横版 `build_hook_reel` 的差别是**成本结构完全不同**：

        横版：ffmpeg 从源片直切 → concat → 只编码一次
        竖版：每球先经 vrender 逐帧渲染 7s（跟球运镜）→ 再从这些片段里裁
              钩子 + 拼正片 → 整体编码一次

    竖版做不到一次编码：跟球运镜要逐帧重算裁剪窗，只能走 vrender 的渲染链
    （那是唯一实现）。代价是每球要解 7s 源帧做 cv2 裁剪，勾 24 球（8 条）
    会比横版慢一个量级——这是运镜的固有成本，不是这里的实现问题。

    **钩子和正片共用同一次渲染**（钩子 = 该片段内「穿网前 hook_len 秒」）。
    分开渲染两遍的话两次运镜起点不同，钩子切进正片时构图会跳一下。

    慢放：`render_vertical_clip(..., slowmo=False)` 全程常速——用户 2026-11-01
    要求「进球的那个画面可以不需要慢放」。
    """
    if not hits:
        raise ValueError("没有选中任何进球")
    from . import vrender                # 延迟 import：横版路径不背这条依赖链

    cfg = cfg or vrender.CFG
    os.makedirs(work_dir, exist_ok=True)
    out = os.path.join(work_dir, out_name)
    if not out.endswith(".mp4"):
        out += ".mp4"

    tmp_dir = os.path.join(work_dir, "hook4v")
    os.makedirs(tmp_dir, exist_ok=True)
    for old in glob.glob(os.path.join(tmp_dir, "*.mp4")):
        try:
            os.remove(old)
        except OSError:          # 播放器预览把旧段锁住（Win32）：跳过，无害
            pass

    n = len(hits)
    hook_len = max(HOOK_MIN, hook_sec / n)
    clips = []                   # (路径, 段起, 段止, 钩子起帧, 钩子止帧, 正片帧数)
    for i, hit in enumerate(hits):
        p = os.path.join(tmp_dir, f"{i:03d}.mp4")
        p, start, end = vrender.render_vertical_clip(
            src, work_dir, float(hit), before, after, p, cfg, slowmo=False)
        # 穿网在片段内第几秒：**按实际起点算**。贴着片头的球起点被钳制过，
        # 用理论值 t-before 会差出整秒，钩子直接剪到隔壁球。
        #
        # 下面一律换成**帧号**再交给 filter：按时间秒裁（trim=start=3.667）
        # 会因为「该帧 PTS 略小于起点」被排除，每段少 1 帧，三球下来总时长
        # 24.90s 而不是承诺的 25.0s（实测过，不是理论担心）。
        off_f = (float(hit) - start) * _VCLIP_FPS
        hook_f = min(hook_len * _VCLIP_FPS, off_f)
        clips.append((p,
                      max(0, round(off_f - hook_f)),   # 钩子起帧
                      round(off_f),                    # 钩子止帧（收在穿网）
                      round((end - start) * _VCLIP_FPS)))  # 正片帧数

    has_audio = probe_video(src)["audio"]
    # 同一个片段要用两次（钩子窗口 + 整段），各挂一个输入：mp4 才几 MB，
    # 重复 -i 的解码开销远小于 asplit 把视频/音频两条链都拆开写。
    inputs: list[str] = []
    for p, *_ in clips:
        inputs += ["-i", p]
    for p, *_ in clips:
        inputs += ["-i", p]

    parts, lv, la = [], [], []
    ci = 0
    for i, (_p, h_a, h_b, _bf) in enumerate(clips):
        parts.append(f"[{i}:v]trim=start_frame={h_a}:end_frame={h_b},"
                     f"setpts=PTS-STARTPTS,fps={_VCLIP_FPS:g},setsar=1,"
                     f"format=yuv420p[v{ci}]")
        lv.append(f"[v{ci}]")
        if has_audio:
            a0, a1 = h_a / _VCLIP_FPS, h_b / _VCLIP_FPS
            parts.append(
                f"[{i}:a]atrim=start={a0:.3f}:end={a1:.3f},"
                f"asetpts=PTS-STARTPTS,afade=t=in:st=0:d={AFADE},"
                f"afade=t=out:st={max(0.0, (a1 - a0) - AFADE):.3f}:d={AFADE}[a{ci}]")
            la.append(f"[a{ci}]")
        ci += 1
    for i, (_p, _ha, _hb, bf) in enumerate(clips):
        parts.append(f"[{n + i}:v]trim=start_frame=0:end_frame={bf},"
                     f"setpts=PTS-STARTPTS,fps={_VCLIP_FPS:g},setsar=1,"
                     f"format=yuv420p[v{ci}]")
        lv.append(f"[v{ci}]")
        if has_audio:
            d = bf / _VCLIP_FPS
            parts.append(
                f"[{n + i}:a]atrim=start=0:end={d:.3f},asetpts=PTS-STARTPTS,"
                f"afade=t=in:st=0:d={AFADE},"
                f"afade=t=out:st={max(0.0, d - AFADE):.3f}:d={AFADE}[a{ci}]")
            la.append(f"[a{ci}]")
        ci += 1
    parts.append("".join(lv) + f"concat=n={len(lv)}:v=1:a=0[vout]")
    if has_audio:
        parts.append("".join(la) + f"concat=n={len(la)}:v=0:a=1[aout]")

    cmd = [_ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error"] + inputs + [
        "-filter_complex", ";".join(parts), "-map", "[vout]"]
    if has_audio:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-ar", "44100"]
    cmd += [
        "-c:v", "libx264", "-preset", "slow", "-crf", str(cfg.crf),
        "-pix_fmt", "yuv420p", "-r", f"{_VCLIP_FPS:g}",
        "-movflags", "+faststart", out,
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=None)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("ffmpeg 超时") from e
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败：{(r.stderr or '')[-600:]}")
    if not os.path.exists(out) or os.path.getsize(out) < 1024:
        raise RuntimeError("ffmpeg 没有产出文件")
    return out


def segment_calib(work_dir: str, t: float):
    """取 t 所在的标定段（最后一个 t0≤t 的段），无标定返回 None。

    与 `shooter._seg_for_t` / pipeline 的段窗 `[t0, 下一段)` 同口径。本模块
    自己写而不 import，是为了不把 shooter 那条重依赖链拖进来（见模块头 ①）。"""
    segs = _calib.load_segments_dir(work_dir)
    for s in reversed(segs):
        if t >= s.t0:
            return s
    return segs[0] if segs else None
