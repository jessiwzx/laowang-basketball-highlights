"""竖屏运镜集锦：现有数据（出手点/筐标定，均视频像素坐标）→ 关键帧相机
路径 → warpAffine 浮点裁剪 → rawvideo 管道喂 ffmpeg（minterpolate 慢动作
/ 白闪 / concat 组装）。设计见
docs/superpowers/specs/2026-09-15-vertical-highlight-design.md。"""
from __future__ import annotations

import glob
import math
import os, subprocess
from dataclasses import dataclass

import cv2
import numpy as np

from . import calibration as _calib
from .detector import _scaled_rect
from .editor import _ffmpeg_exe, _run
from .editor import concat_clips, safe_filename
from .models import load_events, time_key
from .shooter import _seg_for_t, load_shooters


@dataclass
class RenderConfig:
    # —— 导出（spec ①）——
    out_w: int = 660             # 2026-09-25 用户裁定方案B（实机问题4「画面
                                  # 稍微缩小一点，看到更多内容」）：592→660。
                                  # 比例 9:19.5 → 1:1.94，视场随比例走
                                  # 333→371px（源宽 26%→29%），零畸变
                                  # （_crop_frame 单一缩放，源高恰映射
                                  # out_h）。加宽**不会**出现上下边：缩放恒为
                                  # out_h/frame_h，变宽只多吃横向源像素；吃满
                                  # 源宽（out_w = 帧宽×out_h/帧高，1280 源 →
                                  # 2276）为止，再宽才轮到上下边
    out_h: int = 1280
    fill_mode: str = "black"     # 缩小档上下留白的填充方式（2026-10-18 用户裁定
                                  # A 案）：black=纯黑；blur=同帧模糊（2026-09-25
                                  # 旧口径，仍可切回，切回即逐位还原）。
                                  # 背景：模糊底在广角场馆里会糊出一片脏灰（尤其
                                  # 顶棚），抖音观感像渲染出错；纯黑读作电影留边，
                                  # 且给标题/字幕留出干净安全区。留白高度只由 k
                                  # 决定（ch=round(out_h·k)），与源分辨率无关。
    content_scale: float = 0.80  # 内容缩放（2026-09-25 用户裁定档1：「比例不变、
                                  # 整体画面内容增多、人和球变小」——先出
                                  # k=1.0/0.80/0.65/0.50 四档样片，用户挑 k=0.80）。
                                  # 1.0 = 内容吃满画幅（旧口径，仍可 replace 取回）；
                                  # <1 = 内容缩到 out_w×(k·out_h) 纵向居中、上下留白
                                  # 按 fill_mode 填充（默认纯黑），可见源窗等比放大到 cw/k（左右
                                  # 各自多露出）。画幅比例与内容畸变都不变（仍是
                                  # 单一均匀缩放 s = k·out_w/cw）；相机钳制改按
                                  # 「可视窗宽」算（visible_width）——窗宽 > cw 时
                                  # 若仍按 cw 钳制会露出源画面外的空白。代价：窗越宽
                                  # 相机横向余量越小（660 画幅 k=0.8 时 ±408px，
                                  # k=0.26 全源可见=运镜锁死），k 不宜再往下调
    crf: int = 19
    audio_kbps: int = 128
    loudnorm_i: int = -14
    # —— 慢动作（spec ③；锚点 2026-09-16 从进球时刻改出手时刻）——
    slowmo_speed: float = 0.6     # 2026-09-17 用户「慢放略久」→ 0.3→0.4，
                                  # 「还是有点慢，改成 0.6」（1.0s 源窗
                                  # 3.33s → 2.5s → 1.67s 输出；≥0.5 时
                                  # _atempo_chain 单级即可，无两级链）
    slowmo_pre: float = 0.35      # 慢放窗起点 = 出手时刻 − 此值（起跳收球）
    slowmo_post: float = 0.65     # 慢放窗终点 = 出手时刻 + 此值（球飞出段）
    slowmo_min: float = 0.2       # 截短后不足此值 → 该球跳过慢放
    # —— 片头钩子（用户 2026-09-17：把最后一个球的进球砍头复制到片头——
    # 抖音看重 3s/5s 完播，前 3 秒就得看到进球）——
    hook: bool = True             # 关=不做钩子（老拼法）
    hook_lead: float = 0.6        # 期望提前量 = 出手时刻 − 此值（露出投篮动作）
    hook_min: float = 1.2         # 提前量下限（出手→进球过短的球往前补够看）
    hook_max: float = 2.4         # 提前量上限（远投飞行久也不让进球拖到 3s 后）
    hook_tail: float = 0.3        # 终点 = 进球时刻 + 此值（球落网）
    # —— 运镜（spec ②）——
    release_floor: float = 0.3    # 出手后到 pan 截止不足此秒 → pan 截止放宽
                                  # 到进球时刻：镜头跨进慢放窗继续平移，进球
                                  # 瞬间恰锁筐（补篮类；出手画面仍完整——
                                  # pan 恒不早于出手）
    release_hold: float = 0.3     # 出手后先在出手人身上停留此秒再起 pan（用户
                                  # 2026-09-25：出手动作还没做完镜头就跟着球
                                  # 跑了）。可用时长不够先缩 hold（缩到 0 即
                                  # 旧口径），pan 至少保住 pan_min
    pan_gap: float = 0.35         # pan 必须在慢动作开始前完成（= t − 此值）
    pan_min: float = 0.5
    pan_max: float = 1.8          # 宁快勿拖封顶
    peak_frac: float = 0.45       # 峰值速度目标 ≤ 裁剪宽×此值/秒（时间
                                  # 充裕时生效；出手可见性优先于它）
    smooth_win: int = 5           # 逐帧中心滑平均窗（奇数）
    default_lead: float = 1.1     # release_t 缺失时按 t − 此值当出手时刻
    # —— 跟球运镜（实机问题5：补篮整段锁筐，运球人从画面外接近，前段
    # 整段在裁剪窗外）——lead 段镜头沿球的轨迹走（人运球必随球）——
    follow_step: int = 2          # 跟随扫描步长（帧）≈12fps（同出手溯源）
    follow_radius: float = 190.0  # 链最近邻门（两采样点间球最大位移，px：
                                  # 运球 ~12px/采样，弹跳竖摆兜得住）
    follow_gap: float = 0.5       # 断链桥接上限（秒，同出手溯源链口径）
    follow_s_floor: int = 88      # 扫描饱和度地板（SHOOTER v4 同款——远景
                                  # 标定易撑宽色域，顶棚暖反光劫持链）
    follow_max_w: float = 20.0    # 「球」最大边长（720p 口径，按帧高缩放）：
                                  # 实测 2026-09-25 09-19_1 球 max(w,h)≤14、
                                  # 人躯干/球衣/皮肤块 ≥19（且躯干取 max 后
                                  # 更大）——超过即判非球，仅在无球样候选
                                  # 时兜底选它（球在手/被遮时链不该断）
    follow_penalty: float = 140.0 # 非球样候选的评分罚（px，720p 口径同缩放）：
                                  # 等价「除非它比任何球样候选近这么多，否则
                                  # 不选它」——约半个裁剪窗宽，跳错到这个
                                  # 程度球就出画了
    follow_smooth_win: int = 9    # 跟随曲线平滑窗（帧，杀运球弹跳抖动）
    # —— 转场/收尾（spec ④）——
    transition: str = "white_flash"   # "white_flash" | "hard_cut"
    flash_frames: int = 2
    end_freeze_s: float = 0.5     # 末球定帧淡出时长；0=关
    # —— 筐位实测（实机问题1：标定段内镜头漂移 ±190px → 锁筐偏心）——
    probe_pre: float = 0.6        # 采样窗 [t−此值, t−probe_post]（进球前，
    probe_post: float = 0.25      # 球在筐上方未落，不遮筐口）
    probe_n: int = 5              # 采样帧数
    probe_min_hits: int = 2       # 命中少于此数 → 回退标定筐位
    probe_roi_dx: float = 260.0   # 标定筐心邻域半宽（帧像素）——实测 09-11_3
    probe_roi_dy: float = 120.0   # 段1 漂移 ≈190px，邻域须罩得住
    probe_min_w: float = 4.0      # 候选块最小宽——远景筐弧遮挡重时只剩
                                  # w5-6 碎片（09-11_3 球3），形态学开已消
                                  # 碎屑，此门只拦针尖
    probe_y_tol: float = 80.0     # 候选块中心 y 距标定筐心容忍带——三球
                                  # 实测筐块 dy∈[−72,+8]、飞行球 dy∈[−112,
                                  # −93]，80px 切在两簇之间（筐/球的分离面）


CFG = RenderConfig()


@dataclass
class GoalCam:
    """单球运镜输入（坐标均为源视频像素；start/end 已按片长裁界）。"""
    t: float                    # 进球时刻
    start: float                # 片段起 = max(0, t−before)
    end: float                  # 片段止 = min(duration, t+after)
    shooter_x: float | None     # 出手人像素 x；None（bad/缺失）→ 整段锁筐
    release_t: float | None     # 出手时刻；None → t − default_lead
    hoop_x: float               # 筐像素 x（所属标定段缩放后筐口中心）
    frame_w: int
    frame_h: int
    fps: float


def crop_width(frame_h: int, cfg: RenderConfig = CFG) -> int:
    """竖屏裁剪窗宽：吃满源高、比例与输出画幅同构（1080 高 → 500）。
    与 content_scale 无关（k<1 时它是「内容吃满画幅」的等效窗宽）。"""
    return max(2, round(frame_h * cfg.out_w / cfg.out_h))


def visible_width(frame_h: int, cfg: RenderConfig = CFG) -> float:
    """相机**可视**源窗宽 = cw/content_scale：内容一缩小，同样的画幅就
    装得下更宽的源画面（左右各多露出 (1/k−1)·cw/2）。相机钳制与 pan 速度
    预算都按它算——否则 k<1 时窗心会越界露出源画面外的空白。k=1 → 恒等于
    crop_width（旧口径逐位不变）。"""
    return crop_width(frame_h, cfg) / max(cfg.content_scale, 1e-3)


def _ease(u: float) -> float:
    """正弦 ease-in-out：0→1 单调，峰值斜率 π/2（平均斜率的 1.57 倍）。"""
    return (1.0 - math.cos(math.pi * u)) / 2.0


def _lead_curve(g: GoalCam, follow, n: int, cfg: RenderConfig) -> list[float]:
    """lead 段（pan 起点前）逐帧目标 x：follow 链线性插值——链首前持链首
    值（检测断链截断就停在那，不瞎猜更早的球位置），链尾后=出手点（链尾
    本就锚在出手点，此处兜住截断链）。再滑动平均杀运球弹跳抖动（±40px、
    ~2Hz；平滑的相位滞后对"镜头跟着人走"无害）。follow 空 → 恒出手点
    （旧 hold 契约，测试 test_出手前相机锁出手人 钉住）。"""
    if not follow:
        return [g.shooter_x] * n
    xs = [float(p[0]) for p in follow]
    ys = [float(p[1]) for p in follow]
    out = []
    for i in range(n):
        ts = g.start + i / g.fps
        if ts <= xs[0]:
            out.append(ys[0])
        elif ts >= xs[-1]:
            # 链尾后兜截断链：有出手人=出手点（链尾本锚出手）；无出手人
            # （进球锚链）=筐（链尾锚在筐，值连续——进球后锁筐）
            out.append(g.shooter_x if g.shooter_x is not None else g.hoop_x)
        else:
            j = 0
            while j < len(xs) - 2 and xs[j + 1] < ts:
                j += 1
            u = (ts - xs[j]) / max(xs[j + 1] - xs[j], 1e-6)
            out.append(ys[j] + (ys[j + 1] - ys[j]) * u)
    return smooth_curve(out, cfg.follow_smooth_win)


def _goal_lock_centers(g: GoalCam, lead: list[float], n: int, cw: float,
                       lo: float, hi: float,
                       cfg: RenderConfig) -> list[float]:
    """无出手锚时的「进球前收镜头」：lead 跟球 → pan → 锁筐（2026-11-01）。

    为什么需要：无 `shooters.json`（没跑过出手人识别）的场次走的是「进球
    锚跟球链」分支，链尾才把值写成筐位（`t` 时刻一个点），此前逐帧都是球
    的位置。于是穿网那一刻镜头还在从球平移到筐的路上——实测 2026-10-30
    三球，穿网时刻筐落在输出 x≈263（正中 330），要到进球后 1 秒才归位。
    用户口径（2026-11-01）：「篮筐应该在视频中间才对」。有出口锚的场次
    （如 2026-10-17）走的是上面那条 pan→lock 主分支，本来就没这问题。

    时间线不引入新常量，全部复用有锚分支那套：
    `pan_end = t − pan_gap`（慢放窗前完成，本模板不慢放但口径一致），
    起点不早于 `t − default_lead`（无 release_t 时对出手时刻的估计，保证
    出手动作期间镜头仍在球上），时长按 `need` 算完再钳到 `pan_min/pan_max`。

    **与主分支的差别**：这里没有出手点，`cx0` 取 pan 起点处的链插值——镜头
    从「球当前在哪」开始平移，而不是从固定出手点开始，接缝才不会跳。
    """
    pan_end = g.t - cfg.pan_gap
    floor_t = max(g.start, g.t - cfg.default_lead)
    if pan_end - floor_t < 1e-3:            # 片段太短，收不了镜头
        return [min(max(c, lo), hi) for c in lead]
    i0 = min(n - 1, max(0, int(round((floor_t - g.start) * g.fps))))
    dist = abs(g.hoop_x - lead[i0])
    need = (math.pi / 2.0) * dist / (cfg.peak_frac * cw) if dist > 0 else 0.0
    avail = pan_end - floor_t
    pan_dur = min(max(need, min(cfg.pan_min, avail)), avail, cfg.pan_max)
    pan_start = pan_end - pan_dur
    j0 = min(n - 1, max(0, int(round((pan_start - g.start) * g.fps))))
    cx0 = lead[j0]
    out = []
    for i in range(n):
        ts = g.start + i / g.fps
        if ts >= pan_end:
            cx = g.hoop_x
        elif ts <= pan_start:
            cx = lead[i]
        else:
            u = (ts - pan_start) / max(pan_end - pan_start, 1e-6)
            cx = cx0 + (g.hoop_x - cx0) * _ease(u)
        out.append(min(max(cx, lo), hi))
    return out


def camera_centers(g: GoalCam, cfg: RenderConfig = CFG,
                   follow: list | None = None,
                   lock_goal: bool = False) -> list[float]:
    """逐源帧裁剪窗中心 cx（未平滑）：lead 跟球/跟出手人 → 正弦缓动平移
    → 锁筐至片尾。pan 硬性不早于出手时刻（实机问题2：旧公式里 need/pan_min
    压过出手时刻，抢跑的 pan 让出手人出画）；出手后可用时间不足
    release_floor → pan 截止放宽到进球时刻：镜头跨进慢放窗继续平移，进球
    瞬间恰锁筐（实机问题5：补篮整段锁筐会让从画面外运球接近的人前段
    整段出画；minterpolate 拉伸后输出侧的这段 pan 更缓）；pan 起点再后
    推 release_hold——出手动作做完再跟球（实机问题1），时长不够先缩
    停留；出手晚于进球
    （溯源脏数据）→ 整段锁筐兜底。无出手人 → 有进球锚跟球链（ball_follow_x
    以筐为锚产出，尾=筐）时全程跟链——运球段球在手=跟人、飞行段跟球、
    链尾后锁筐（用户 2026-09-16：出手前运球人保持画面横向中间——未跑
    过出手人识别或 spot=bad 的球无锚，整段锁筐会让运球人走出画面；
    09-11_3 九球全有出手锚，此兜底对其不触发）；无链
    → 全程锁筐（spec ② 兜底）。follow=跟球链 [(ts, x)]（ball_follow_x
    产出，尾=出手锚）驱动 lead 段；None → 恒出手点。逐帧钳制不出画面。"""
    n = max(1, round((g.end - g.start) * g.fps))
    cw = visible_width(g.frame_h, cfg)      # 可视窗宽（含 content_scale）
    if cw >= g.frame_w:                     # 窗已不比源窄：只能居中锁死
        lo = hi = g.frame_w / 2.0
    else:
        lo, hi = cw / 2.0, g.frame_w - cw / 2.0
    if g.shooter_x is None:
        if not follow:
            return [min(max(g.hoop_x, lo), hi)] * n
        lead = _lead_curve(g, follow, n, cfg)
        if lock_goal:
            # 2026-11-01 新增（默认关，只有「25s 精选」竖版传 True）：无
            # 出手锚也要在进球前把镜头收到筐上，否则穿网时筐还在画面左
            # 侧——见 `_goal_lock_centers` 的实测数据。
            return _goal_lock_centers(g, lead, n, cw, lo, hi, cfg)
        return [min(max(c, lo), hi) for c in lead]
    t_rel = g.release_t if g.release_t is not None else g.t - cfg.default_lead
    dist = abs(g.hoop_x - g.shooter_x)
    need = (math.pi / 2.0) * dist / (cfg.peak_frac * cw) if dist > 0 else 0.0
    # 注：cw 在此处是「可视窗宽」口径 ⇒ 峰值速度 = peak_frac·窗宽/秒 ⇒
    # 输出侧恒为 peak_frac·out_w px/s，与 content_scale 无关（k 只改主体
    # 大小，不改运镜快慢）
    pan_end = g.t - cfg.pan_gap
    avail = pan_end - max(t_rel, g.start)   # 出手后到 pan 截止的可用时长
    if avail < cfg.release_floor:
        pan_end = g.t                       # 放宽到进球时刻（跨慢放窗平移）
        avail = pan_end - max(t_rel, g.start)
        if avail <= 0:   # 出手晚于进球=脏数据 → 安全兜底整段锁筐
            return [min(max(g.hoop_x, lo), hi)] * n
    # 出手后停留（实机问题1 2026-09-25：出手动作没做完镜头就跟着球跑了）：
    # pan 起点先推后 release_hold；时间不够优先缩 hold（最多让 pan 剩
    # pan_min），全不够则 hold=0 退回旧口径 ⇒ pan_dur ≤ avail−hold。
    hold = min(max(cfg.release_hold, 0.0), max(avail - cfg.pan_min, 0.0))
    # 峰值速度目标（need）只在可用时间内兑现：不够就压缩 pan（快但完整
    # 看到出手），够则按 need/pan_min 走。pan_dur ≤ avail−hold 恒成立。
    budget = avail - hold
    pan_dur = min(max(min(need, budget), min(cfg.pan_min, budget)), cfg.pan_max)
    pan_start = max(g.start, pan_end - pan_dur)
    pan_dur = max(pan_end - pan_start, 1e-6)
    lead = _lead_curve(g, follow, n, cfg)
    centers = []
    for i in range(n):
        ts = g.start + i / g.fps
        if ts >= pan_end:
            cx = g.hoop_x
        elif ts <= pan_start:
            cx = lead[i]
        else:
            u = (ts - pan_start) / pan_dur
            cx = g.shooter_x + (g.hoop_x - g.shooter_x) * _ease(u)
        centers.append(min(max(cx, lo), hi))
    return centers


def smooth_curve(xs: list[float], win: int = 5) -> list[float]:
    """滑动平均（边缘复制填充）：消 hold→pan→lock 衔接折角。win 偶/过大
    自动收缩成可用奇数窗；len≤2 原样返回。"""
    if win <= 1 or len(xs) <= 2:
        return list(xs)
    arr = np.asarray(xs, np.float64)
    pad = min(win // 2, len(arr) - 1)
    if pad == 0:
        return list(xs)
    padded = np.concatenate([np.full(pad, arr[0]), arr, np.full(pad, arr[-1])])
    kernel = np.ones(2 * pad + 1) / (2 * pad + 1)
    return list(np.convolve(padded, kernel, mode="valid"))


def plan_camera(g: GoalCam, cfg: RenderConfig = CFG,
                follow: list | None = None,
                lock_goal: bool = False) -> list[float]:
    """camera_centers + 平滑 = 渲染用的最终逐帧 cx（follow 链透传；
    lock_goal 透传，见 `camera_centers`）。"""
    return smooth_curve(camera_centers(g, cfg, follow, lock_goal), cfg.smooth_win)


def _ball_blobs(frame, calib, cfg: RenderConfig) -> list[tuple[float, float, float, float]]:
    """全帧 HSV 球候选（跟球运镜扫描用）：S 地板压掉顶棚暖反光（SHOOTER
    v4 同款），形态学开消碎屑，最小宽只拦针尖（远景球仅 4-6px）。
    宽高一并带回供链做球性判决（follow_max_w 门）。返回 [(cx, cy, w, h)]。"""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    lo = np.array([calib.hsv_low[0], max(cfg.follow_s_floor, calib.hsv_low[1]),
                   calib.hsv_low[2]])
    mask = cv2.inRange(hsv, lo, np.array(calib.hsv_high))
    mask = cv2.morphologyEx(
        mask, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in contours:
        bx, by, bw, bh = cv2.boundingRect(c)
        if bw < 3 or bh < 1 or bw > frame.shape[1] * 0.5:
            continue
        out.append((bx + bw / 2.0, by + bh / 2.0, float(bw), float(bh)))
    return out


def ball_follow_x(cap, start: float, rel: float, release_xy,
                  calib, fw: int, fh: int, fps: float,
                  cfg: RenderConfig = CFG) -> list[tuple[float, float]]:
    """从出手点向过去回追球的横向位置（跟球运镜 lead 段的目标链）。
    HEVC 随机 seek 一次 ~160ms（逐帧随机寻址不可行），seek 到片段头一次
    后顺序读 [start, rel]，每 follow_step 帧 grab/retrieve 全帧 HSV 检球
    （步长跳帧只 grab 不 retrieve，省一半解码）；再从出手点（溯源最可信
    的锚）倒序链接（2D 门 follow_radius，断口 ≤ follow_gap 桥接——同出手
    溯源链口径）。返回 (ts, x) 升序链，链尾恒为出手锚；断口超限即止——
    更早时段镜头停在链首值，不瞎猜。rel−start 不足一步或无锚 → 空链
    （调用方回退静 hold）。
    关联打分（实机问题3 2026-09-25：镜头跟着人走没捕捉到球）：候选窗内
    取 min(最近邻距离 + 非球样罚)——非球样块（max(w,h) > follow_max_w）
    只在没有球样候选时才选（球在手/被人挡住导致链断更糟——运球段本就是
    「跟球=跟人」；09-19_1 实测该块就是被跟在身后的人）。等速外推的预测
    项实测是净负（09-19_1 八球消融：预测把 4240 的窗内 24/30 压到 15/30、
    其余无一改善，罚则是 5401 22/37→26/37、4204 +1 零回归）——不采用。"""
    if cap is None or release_xy is None or rel is None:
        return []
    i0, i1 = int(round(start * fps)), int(round(rel * fps))
    if i1 - i0 < cfg.follow_step:
        return []
    cap.set(cv2.CAP_PROP_POS_FRAMES, i0)
    samples = []                       # [(ts, [(cx, cy, w, h), ...]), ...]
    for fi in range(i0, i1 + 1):
        # 每帧都 grab（推进+解码）；采样点 retrieve（只转换最近 grab 的帧，
        # 不推进）——写成「跳帧只 grab、采样只 retrieve」会让采样流以半速
        # 错位前进（实测 mp4v：循环第 266 帧取到真第 207 帧的球）
        if not cap.grab():
            break
        if (fi - i0) % cfg.follow_step:
            continue
        ok, fr = cap.retrieve()
        if not ok:
            break
        samples.append((start + (fi - i0) / fps, _ball_blobs(fr, calib, cfg)))
    dt = cfg.follow_step / fps
    scale = fh / 720.0                 # 球样门/罚是 720p 口径
    max_w = cfg.follow_max_w * scale
    pen = cfg.follow_penalty * scale
    chain = [(float(rel), float(release_xy[0]))]      # 出手锚收尾
    cur = (float(release_xy[0]), float(release_xy[1]))
    miss = 0.0
    for ts, blobs in reversed(samples):               # ts 从 rel 侧向 start
        best = None
        for cx, cy, bw, bh in blobs:
            d = math.hypot(cx - cur[0], cy - cur[1])
            if d > cfg.follow_radius:
                continue
            s = d + (pen if max(bw, bh) > max_w else 0.0)
            if best is None or s < best[0]:
                best = (s, cx, cy)
        if best is None:
            miss += dt
            if miss > cfg.follow_gap:                 # 断口超限：诚实截断
                break
            continue
        miss = 0.0
        cur = (best[1], best[2])
        chain.append((ts, best[1]))
    chain.reverse()
    return chain


def measure_hoop_x(frames: list, ref_rect, hsv_low, hsv_high,
                   roi_dx: float = 260.0, roi_dy: float = 120.0,
                   min_hits: int = 2, min_w: float = 4.0,
                   y_tol: float = 80.0) -> float | None:
    """进球前采样帧实测筐口中心 x。标定按段存、段内假设镜头不动，但实测
    段内镜头会缓慢漂移（09-11_3 段1 达 +190px）——锁标定值导致筐在成片
    横向偏心。做法：标定筐心邻域内 HSV 命中的连通块，先过 y 门（筐/球的
    分离面：09-11_3 三球实测筐块 dy∈[−72,+8]、飞行球 dy∈[−112,−93]，
    筐弧总在筐心高度附近、球在筐上方）滤掉飞行球，再取距标定筐心最近者
    的中心 x，多帧取中位。远景筐弧块是近方块（ar 1.0-1.3、w19-26，遮挡
    重时只剩 w5-6 碎片）——不设 aspect 门、最小宽只拦针尖（形态学开已
    消碎屑）。命中不足 min_hits → None（调用方回退标定筐位）。"""
    rx, ry, rw, _rh = ref_rect
    cx0, cy0 = rx + rw / 2.0, ry + _rh / 2.0
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    hits = []
    for f in frames:
        h_img, w_img = f.shape[:2]
        x0, x1 = max(0, round(cx0 - roi_dx)), min(w_img, round(cx0 + roi_dx))
        y0, y1 = max(0, round(cy0 - roi_dy)), min(h_img, round(cy0 + roi_dy))
        if x1 <= x0 or y1 <= y0:
            continue
        roi = f[y0:y1, x0:x1]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, np.array(hsv_low), np.array(hsv_high))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        best = None
        for c in contours:
            bx, by, bw, bh = cv2.boundingRect(c)
            if bh <= 0 or bw < min_w or bw > rw * 3.0:
                continue
            if abs(by + bh / 2.0 - (cy0 - y0)) > y_tol:   # 筐上方=飞行球
                continue
            d = abs(bx + bw / 2.0 - (cx0 - x0))
            if best is None or d < best[0]:
                best = (d, bx + bw / 2.0 + x0)
        if best is not None:
            hits.append(best[1])
    return float(np.median(hits)) if len(hits) >= min_hits else None


def slowmo_window(g: GoalCam, cfg: RenderConfig = CFG) -> tuple[float, float] | None:
    """慢放源窗锚在**出手时刻**（用户 2026-09-16：进球慢放改出手慢放——
    起跳收球+出手+球飞出段慢放，进球本身回常速看「唰」一下）。出手缺失按
    t − default_lead 估计（同 camera_centers 口径）。截进片窗；不足
    slowmo_min → None（spec ③：窗被极小前后秒截短时收缩，太小干脆不慢）。"""
    t_rel = g.release_t if g.release_t is not None else g.t - cfg.default_lead
    a = max(g.start, t_rel - cfg.slowmo_pre)
    b = min(g.end, t_rel + cfg.slowmo_post)
    return (a, b) if b - a >= cfg.slowmo_min else None


def hook_window(t: float, before: float, after: float,
                release_t: float | None, duration: float,
                cfg: RenderConfig = CFG) -> tuple[float, float] | None:
    """片头钩子的源窗 (start, end)：末球进球砍头前置，前 3 秒看到进球。
    提前量（= 进球时刻 − 起点）以「出手前 hook_lead」为目标，夹在
    [hook_min, hook_max]：出手→进球过短的球（补篮 0.15s）往前补到
    hook_min 保证够看，远投飞行久也不让进球拖到 3 秒后（钩子不许拖成
    第二个正片）。窗不越出用户选的片段窗（before/after）与片长；出手
    缺失按 t − default_lead 估计（同 camera_centers 口径）。窗短于
    slowmo_min（用户窗太窄）→ None（不值当，不出钩子）。"""
    t_rel = release_t if release_t is not None else t - cfg.default_lead
    lead = min(max(t - t_rel + cfg.hook_lead, cfg.hook_min), cfg.hook_max)
    start = max(0.0, t - before, t - lead)
    end = min(duration, t + min(after, cfg.hook_tail))
    return (start, end) if end - start >= cfg.slowmo_min else None


def _v_enc(cfg: RenderConfig) -> list:
    # 统一编码参数——所有段（正片/白闪）必须逐字一致，流拷贝 concat 的前提
    return ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(cfg.crf),
            "-pix_fmt", "yuv420p", "-r", "30"]


def _a_enc(cfg: RenderConfig) -> list:
    return ["-c:a", "aac", "-b:a", f"{cfg.audio_kbps}k", "-ar", "44100", "-ac", "2"]


def _bar_canvas(frame, cfg: RenderConfig = CFG):
    """缩小档留白底：fill_mode="black" → 纯黑（2026-10-18 用户裁定 A 案）；
    "blur" → 同帧模糊底（2026-09-25 旧口径，逐位可还原）。
    纯黑读作电影留边；模糊底在广角场馆会糊出一片脏灰，观感像渲染出错。"""
    if cfg.fill_mode == "blur":
        return _blur_bg(frame, cfg)
    return np.zeros((cfg.out_h, cfg.out_w, 3), dtype=frame.dtype)


def _blur_bg(frame, cfg: RenderConfig = CFG):
    """留白底（blur 档）：整帧缩到 1/8 宽再放大回画幅（便宜的块状模糊），比纯黑或
    镜像边缘贴画——黑边在手机上像「片子拍错了」，模糊底与画面同色系。"""
    sw = max(2, cfg.out_w // 8)
    sh = max(2, int(round(sw * cfg.out_h / cfg.out_w)))
    sm = cv2.resize(frame, (sw, sh), interpolation=cv2.INTER_AREA)
    return cv2.resize(sm, (cfg.out_w, cfg.out_h), interpolation=cv2.INTER_LINEAR)


def _crop_frame(frame, cx: float, cw: int, cfg: RenderConfig):
    """warpAffine 浮点裁剪一步到位：横向取可视窗 [cx−w/2, cx+w/2]（w =
    cw/content_scale）、纵向吃满源高，缩放 s = out_w/w = k·out_w/cw 映到
    out_w×ch（ch = s·源高，画幅同构、零畸变）。k=1 → ch=out_h，内容吃满
    画幅（旧口径逐位不变）；k<1 → 内容缩到 out_w×(k·out_h) 纵向居中，
    上下留白按 cfg.fill_mode 填充（black 纯黑 / blur 同帧模糊）；k>1 →
    内容放大后纵向居中裁（不进留白分支）。
    subpixel 无整点抖动——不用 ffmpeg zoompan 的根本原因（Trac #4298）。"""
    k = max(cfg.content_scale, 1e-3)
    s = cfg.out_w / (cw / k)
    fh = frame.shape[0]
    # 内容高按 k·out_h 定（不是 round(s·源高)！cw 是取整值 ⇒ s·720 会溢出
    # 到 out_h+1，k=1 就多裁一行、整幅下沉半像素——旧口径必须逐位不变）
    ch = max(2, int(round(cfg.out_h * k)))
    m = np.float32([[s, 0.0, cfg.out_w / 2.0 - s * cx],
                    [0.0, s, ch / 2.0 - s * fh / 2.0]])
    content = cv2.warpAffine(frame, m, (cfg.out_w, ch),
                             flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_REPLICATE)
    if ch == cfg.out_h:
        return content
    if ch > cfg.out_h:                      # 放大档：纵向居中裁
        y0 = (ch - cfg.out_h) // 2
        return content[y0:y0 + cfg.out_h]
    y0 = (cfg.out_h - ch) // 2              # 缩小档：上下留白（按 fill_mode）
    canvas = _bar_canvas(frame, cfg)
    canvas[y0:y0 + ch] = content
    return canvas


def _atempo_chain(speed: float) -> str:
    """音频保音高拉慢到 1/speed 倍时长：atempo 滤镜系数下限 0.5，不够
    就两级链（0.3 → 0.5×0.6）。不能 asetrate 直接减速——音调崩+环境底噪
    变低频轰隆（正是旧慢放静音案的根因），atempo 保音高无此问题。"""
    parts, f = [], speed
    while f < 0.5 - 1e-9:
        parts.append(0.5)
        f /= 0.5
    parts.append(f)
    return ",".join(f"atempo={p:.6g}" for p in parts)


def _timeline_parts(ia: int, ib: int, n: int, fps_in: float, speed: float,
                    freeze_s: float, clip_start: float,
                    clip_end: float) -> tuple[str, str, float]:
    """管道帧三分时间线：[0,ia) 常速 | [ia,ib) 慢动作 | [ib,n) 常速（含
    冻结帧，冻结帧由调用方追加在管道尾、天然落进末段开区间）。音频对位：
    每段用源原声对齐各自的窗——慢放段取同窗原声经 atempo 保音高拉慢
    （2026-09-16 弃静音案：慢放没声音效果差），段界前后 0.1s 淡出淡入
    防「嗒」声（freeze>0 末段再补静音）。
    ib<=ia = 无慢动作（两段/单段）。
    返回 (视频链, 音频链, 输出总时长秒)——**两链分家、各自独立进程跑**：
    视频链消费 [0:v]（rawvideo 管道），音频链消费 [0:a]（源文件）。曾
    单进程混跑：-t 按 muxer 时钟早停丢 atempo 缓冲（音频只剩 flat 段和）、
    去掉 -t 又因 concat 给 atempo 段留 PTS 空洞把音频流 duration 元数据
    撑大 4.5s（实长对、时间轴烂）——PCM wav 中转是唯一干净路。"""
    fade, fps_out = 0.1, 30.0
    n_pipe = n + round(freeze_s * fps_in)
    ranges = []                      # (是否慢动作, 起帧, 止帧)——半开区间
    if ia > 0:
        ranges.append((False, 0, ia))
    if ib > ia:
        ranges.append((True, ia, ib))
    if n_pipe > ib:
        ranges.append((False, ib, n_pipe))
    vparts, vl, total = [], [], 0.0
    for k, (is_sm, a, b) in enumerate(ranges):
        dur = (b - a) / fps_in
        if is_sm:
            dur /= speed
            mi = round(fps_out / speed)   # 0.3x → 100fps 插值后 setpts 恰 30
            seg = (f"[0:v]trim=start_frame={a}:end_frame={b},"
                   f"setpts=PTS-STARTPTS,minterpolate=fps={mi}"
                   f":me_mode=bidir:mc_mode=aobmc:vsbmc=1,"
                   f"setpts={1.0 / speed:.6f}*PTS")
        else:
            seg = (f"[0:v]trim=start_frame={a}:end_frame={b},"
                   f"setpts=PTS-STARTPTS")
        label = "[vout]" if len(ranges) == 1 else f"[v{k}]"
        vparts.append(f"{seg},fps={fps_out:.0f},format=yuv420p{label}")
        vl.append(label)
        total += dur
    if len(ranges) > 1:
        vparts.append("".join(vl) + f"concat=n={len(ranges)}:v=1:a=0[vout]")

    nr = len(ranges)
    aparts = []
    if nr:
        a0 = (f"[0:a]atrim=start={clip_start:.3f}:end={clip_end:.3f},"
              f"asetpts=PTS-STARTPTS,aresample=44100,"
              f"aformat=channel_layouts=stereo")
        if nr > 1:
            # asplit 输出必须显式贴标签，否则消费链的输入标签匹配不到
            # 任何流（ffmpeg 7.1 实测：被当流选择器解析 → 绑定失败）
            aparts.append(a0 + f",asplit={nr}"
                          + "".join(f"[s{i}]" for i in range(nr)))
        prev_sm = False
        for k, (is_sm, a, b) in enumerate(ranges):
            dur = (b - a) / fps_in
            label = "[aout]" if nr == 1 else f"[a{k}]"
            head = a0 + "," if nr == 1 else f"[s{k}]"   # 单段：接链尾忌孤儿
            if is_sm:
                chain = (f"atrim=start={a / fps_in:.3f}:end={b / fps_in:.3f},"
                         f"asetpts=PTS-STARTPTS,{_atempo_chain(speed)}")
            else:
                chain = (f"atrim=end={dur:.3f}" if a == 0
                         else f"atrim=start={a / fps_in:.3f}")
                if prev_sm:
                    chain += f",afade=t=in:d={fade}"
                if k + 1 < len(ranges) and ranges[k + 1][0]:
                    chain += f",afade=t=out:st={max(0.0, dur - fade):.3f}:d={fade}"
                if k == len(ranges) - 1 and freeze_s > 0:
                    chain += f",apad=pad_dur={freeze_s:.3f}"
            aparts.append(head + chain + label)
            prev_sm = is_sm
        if nr > 1:
            aparts.append("".join(f"[a{k}]" for k in range(nr))
                          + f"concat=n={nr}:v=0:a=1[aout]")
    return ";".join(vparts), ";".join(aparts), total


def render_clip(video_path: str, g: GoalCam, centers: list[float],
                slowmo: tuple[float, float] | None, out: str,
                cfg: RenderConfig = CFG, freeze_s: float = 0.0) -> str:
    """单球渲染（三步，2026-09-16 拆分）：① cv2 逐帧读 → 浮点裁剪 →
    rawvideo 管道喂 ffmpeg 出**无声**视频；② 源文件独立跑音频链出 PCM
    wav（慢放段 atempo 保音高拉慢；wav 按样本密铺，无 PTS 语义——单进程
    混跑时 concat 会给 atempo 段留 PTS 空洞、-t 会早停丢 atempo 缓冲，
    均已实测）；③ 视频流拷贝 + 音频编码 mux（-shortest 音画对齐）。
    返回 out；失败抛 RuntimeError（上层跳过该球）。"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV 打不开视频：{video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(g.start * fps)))
    ia = ib = 0
    if slowmo:
        ia = round((slowmo[0] - g.start) * fps)
        ib = round((slowmo[1] - g.start) * fps)
        if ib - ia < max(2, round(cfg.slowmo_min * fps)):
            ia = ib = 0                       # 截短到不足下限 → 整段常速
    n_src = len(centers)
    fz = round(freeze_s * fps)
    vc, ac, _total = _timeline_parts(ia, ib, n_src, fps, cfg.slowmo_speed,
                                     freeze_s, g.start, g.end)
    base = os.path.splitext(out)[0]
    vonly, awav, log = base + ".v.mp4", base + ".a.wav", out + ".log"
    try:
        cmd_v = [_ffmpeg_exe(), "-y",
                 "-f", "rawvideo", "-pix_fmt", "bgr24",
                 "-s", f"{cfg.out_w}x{cfg.out_h}", "-r", f"{fps:.6f}",
                 "-i", "pipe:0",
                 "-filter_complex", vc, "-map", "[vout]", "-an",
                 *_v_enc(cfg), vonly]
        with open(log, "wb") as lf:
            proc = subprocess.Popen(cmd_v, stdin=subprocess.PIPE, stderr=lf)
        try:
            cw = crop_width(g.frame_h, cfg)
            last = None
            for i in range(n_src):
                ok, frame = cap.read()
                if not ok:
                    break                      # 源提前断流：已喂的照常出片
                last = _crop_frame(frame, centers[i], cw, cfg)
                proc.stdin.write(last.tobytes())
            if last is None:
                raise RuntimeError(f"读帧不足：{video_path} @ {g.start:.2f}s")
            for j in range(fz):                # 冻结帧线性淡出到黑
                a = 1.0 - (j + 1) / max(fz, 1)
                proc.stdin.write((last.astype(np.float32) * a)
                                 .astype(np.uint8).tobytes())
        except OSError:
            pass  # ffmpeg 启动即死（坏 filtergraph）→ 管道断流；原因在 rc!=0 的 stderr
        finally:
            cap.release()
            try:
                proc.stdin.close()             # 对端已死时 close 的 flush 也会抛
            except OSError:
                pass
            rc = proc.wait()
        if rc != 0:
            with open(log, "rb") as lf:
                tail = lf.read()[-600:].decode("utf-8", "replace")
            raise RuntimeError(f"render_clip ffmpeg 失败（{out}）：{tail}")
        _run([_ffmpeg_exe(), "-y", "-i", video_path,
              "-filter_complex", ac, "-map", "[aout]",
              "-c:a", "pcm_s16le", awav])
        _run([_ffmpeg_exe(), "-y", "-i", vonly, "-i", awav,
              "-map", "0:v", "-map", "1:a", "-c:v", "copy",
              *_a_enc(cfg), "-shortest", out])
    finally:
        for tmp in (vonly, awav, log):
            if os.path.exists(tmp):
                os.remove(tmp)
    return out


def render_flash(out: str, cfg: RenderConfig = CFG) -> str:
    """白闪微段：flash_frames 帧白场+静音，编码参数与正片逐字一致
    （流拷贝 concat 前提）。参考片同款片间衔接（spec ④）。"""
    d = cfg.flash_frames / 30.0
    _run([_ffmpeg_exe(), "-y",
          "-f", "lavfi", "-i",
          f"color=white:s={cfg.out_w}x{cfg.out_h}:r=30:d={d:.4f}",
          "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
          "-map", "0:v", "-map", "1:a", "-shortest", "-t", f"{d:.4f}",
          *_v_enc(cfg), *_a_enc(cfg), out])
    return out


def normalize_audio(path: str, cfg: RenderConfig = CFG) -> None:
    """整条响度统一 −14 LUFS（抖音标准）。视频流拷贝，仅音频重编码。"""
    tmp = path + ".norm.mp4"
    _run([_ffmpeg_exe(), "-y", "-i", path, "-c:v", "copy",
          "-af", f"loudnorm=I={cfg.loudnorm_i}:TP=-1.5:LRA=11",
          *_a_enc(cfg), tmp])
    os.replace(tmp, path)


def _read_hoop_x(cap, t, calib, fw, fh, fps, cfg: RenderConfig = CFG):
    """进球前采样帧（probe 窗）→ measure_hoop_x。读帧失败/无命中 → None
    （调用方回退标定筐位）。"""
    rx, ry, rw, rh = _scaled_rect(calib, fw, fh)
    frames = []
    span = cfg.probe_pre - cfg.probe_post
    for k in range(cfg.probe_n):
        ts = t - cfg.probe_pre + span * k / max(cfg.probe_n - 1, 1)
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(round(ts * fps)))
        ok, fr = cap.read()
        if ok:
            frames.append(fr)
    if not frames:
        return None
    return measure_hoop_x(frames, (rx, ry, rw, rh), calib.hsv_low,
                          calib.hsv_high, cfg.probe_roi_dx, cfg.probe_roi_dy,
                          cfg.probe_min_hits, cfg.probe_min_w,
                          cfg.probe_y_tol)


def _goal_cam(t, before, after, duration, segs, traces, fw, fh, fps,
              hoop_x: float | None = None) -> GoalCam:
    """表格行 → GoalCam：出手点/出手时刻取 shooters.json（spot=bad 或缺失
    → 无 hold 整段锁筐），筐 x 取所属标定段 hoop_rect 缩放后中心；
    hoop_x 非空 = 渲染时实测筐位，覆盖标定值（实机问题1：段内漂移）。"""
    tr = traces.get(time_key(t)) or {}
    xy = tr.get("release_xy")
    shooter_x = float(xy[0]) if (xy and tr.get("spot") != "bad") else None
    hx, _hy, hw, _hh = _scaled_rect(_seg_for_t(segs, t).calib, fw, fh)
    return GoalCam(t=t, start=max(0.0, t - before), end=min(duration, t + after),
                   shooter_x=shooter_x, release_t=tr.get("release_t"),
                   hoop_x=hoop_x if hoop_x is not None else hx + hw / 2.0,
                   frame_w=fw, frame_h=fh, fps=fps)


def _prepare_ball(probe, t: float, before: float, after: float, data, segs,
                  traces, fw: int, fh: int, fps: float,
                  cfg: RenderConfig = CFG, lock_goal: bool = False):
    """单球渲染前准备 → (GoalCam, 逐帧相机中心)。三件接线：
    ① 跟球链（实机问题5）：有出手锚（release_xy+release_t，spot 非 bad）
    才扫——链尾锚在出手点，lead 段镜头跟着球走。先扫链后筐位探针：链从
    片段头顺序读，探针窗在 t 附近，probe 上的 seek 总路程最短（扫炸/空链
    → None 回退静 hold）。
    ② 进球锚跟球链（用户 2026-09-16：出手前运球人保持在画面横向中间）：
    无出手锚（从未跑过出手人识别，或 spot=bad）的球此前整段锁筐，运球人
    运着球走出画面。改用进球锚：球进筐前一刻就在筐口，锚天然已知；从筐
    倒序回追到片段头——运球段球在手=跟人、飞行段跟球，链尾=筐 → 进球
    恒锁筐自然保持。锚 x 与 GoalCam.hoop_x 同源（实测优先、回退标定），
    链尾锁的筐=渲染锁的筐不跳变。
    ③ 逐球实测筐位（实机问题1）：测不到/读帧炸 → 回退标定值。"""
    tr = traces.get(time_key(t)) or {}
    xy = tr.get("release_xy")
    rt = tr.get("release_t")
    follow = None
    seg_calib = _seg_for_t(segs, t).calib
    if xy and rt is not None and tr.get("spot") != "bad":
        try:
            follow = ball_follow_x(probe, max(0.0, t - before), float(rt), xy,
                                   seg_calib, fw, fh, fps, cfg)
        except Exception:                   # noqa: BLE001——只回退，不跳球
            follow = None
    try:
        mx = _read_hoop_x(probe, t, seg_calib, fw, fh, fps, cfg)
    except Exception:                       # noqa: BLE001——只回退，不跳球
        mx = None
    if follow is None:
        rx, ry, rw, rh = _scaled_rect(seg_calib, fw, fh)
        ax = mx if mx is not None else rx + rw / 2.0
        try:
            follow = ball_follow_x(
                probe, max(0.0, t - before), t, (ax, ry + rh / 2.0),
                seg_calib, fw, fh, fps, cfg) or None
        except Exception:                   # noqa: BLE001——只回退，不跳球
            follow = None
    # _goal_cam 在调用方的 try 内：畸形 release_xy 只废这球，不炸整条
    g = _goal_cam(t, before, after, data["duration"], segs, traces, fw, fh, fps,
                  hoop_x=mx)
    return g, plan_camera(g, cfg, follow, lock_goal)


def _render_hook(probe, video_path: str, rendered, clips_dir: str, data, segs,
                 traces, fw: int, fh: int, fps: float,
                 cfg: RenderConfig = CFG) -> str | None:
    """片头钩子（用户 2026-09-17）：把最后一个球的进球砍头复制到片头。
    钩子源取**已成功渲染**的时间序末球（钩子与正片必须对得上——坏球跳过
    后剩下的最后一个）；常速不慢放、不冻结（钩子要脆）。失败只跳过钩子，
    不拖累正片。返回钩子路径或 None。"""
    if len(rendered) < 2:
        return None          # 单球片做钩子=同一个球连看两遍，像 bug
    t, before, after = max(rendered, key=lambda x: x[0])
    tr = traces.get(time_key(t)) or {}
    w = hook_window(t, before, after, tr.get("release_t"), data["duration"], cfg)
    if w is None:
        return None
    out = os.path.join(clips_dir, f"hook_{int(t):06d}.mp4")
    try:
        g, centers = _prepare_ball(probe, t, t - w[0], w[1] - t, data, segs,
                                   traces, fw, fh, fps, cfg)
        render_clip(video_path, g, centers, None, out, cfg)
        return out
    except Exception as ex:                 # noqa: BLE001——钩子失败不废正片
        print(f"⚠ 片头钩子渲染失败（跳过，正片不受影响）：{ex}")
        return None


def render_vertical_clip(video_path: str, work_dir: str, t: float,
                         before: float, after: float, out: str,
                         cfg: RenderConfig = CFG,
                         slowmo: bool = False,
                         lock_goal: bool = True) -> tuple[str, float, float]:
    """单球竖屏片段（跟球运镜）——供「25s 精选」重编排复用（2026-11-01）。

    与 `build_vertical` 是同一套 `_prepare_ball` + `render_clip`，差别只有两点：

    ① 只渲染**一个球**，怎么拼装由调用方决定（现有集锦是逐个渲染后 concat）；
    ② `slowmo=False` 时**全程常速**——现有竖屏集锦在出手时刻放慢到
       `cfg.slowmo_speed`（0.6×），而「25s 精选」用户明确要求
       「进球的那个画面可以不需要慢放」。这里把慢放窗直接传 None，
       `_timeline_parts` 走 `ib<=ia` 的单段分支，等于原速播放。

    运镜锚点调用方不用管，`_prepare_ball` 自己按数据齐不齐分派：有
    `shooters.json` 的 release_xy 就是「跟出手人 → 跟球 → 锁筐」；没跑过
    出手人溯源（如 2026-10-30 场）自动退到进球锚跟球链——从筐口倒追片段
    头，运球段跟人、飞行段跟球、链尾锁筐。

    `lock_goal=True`（默认，2026-11-01）：无出手锚时**额外**在进球前
    `pan_gap` 秒把镜头收到筐上。没有它，穿网那一刻镜头还在平移途中，筐
    停在画面左侧约 26% 处（2026-10-30 实测输出 x≈263，正中 330）。有出手
    锚的场次走的是主分支，本来就有 pan→lock，此开关对它无影响。

    返回 `(out, start, end)`。**钩子必须用返回的 start 定位，不能用
    `t - before`**：贴着片头/片尾的球会被 `_goal_cam` 钳制起点，用理论值
    算「穿网在片段内第几秒」会差出整秒，钩子就剪到隔壁球或空镜头上。
    """
    data = load_events(os.path.join(work_dir, "events.json"))
    segs = _calib.load_segments_dir(work_dir)
    if not segs:
        raise RuntimeError("缺标定（work 目录无可用 calibration.json）"
                           "——竖屏运镜需要筐几何")
    traces = (load_shooters(work_dir) or {}).get("traces", {})
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV 打不开视频：{video_path}")
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.release()
    d = os.path.dirname(out)
    if d:
        os.makedirs(d, exist_ok=True)
    # probe 单独开一个：_prepare_ball 要在片段头顺序读帧扫跟球链，和
    # render_clip 内部那路的 seek 互不干扰（同 build_vertical 的用法）。
    probe = cv2.VideoCapture(video_path)
    try:
        g, centers = _prepare_ball(probe, float(t), before, after, data, segs,
                                   traces, fw, fh, fps, cfg, lock_goal)
    finally:
        probe.release()
    render_clip(video_path, g, centers,
                slowmo_window(g, cfg) if slowmo else None, out, cfg)
    return out, g.start, g.end


def build_vertical(video_path: str, goals: list[tuple[float, float, float]],
                   work_dir: str, out_name: str = "highlight.mp4",
                   cfg: RenderConfig = CFG) -> str:
    """[(进球时刻, 前, 后)] → 竖屏运镜集锦 MP4，返回成品路径。单球失败
    跳过并打印汇总（spec：集锦不因一球全废）；全部失败才抛。
    goals 顺序 = 正片顺序（爆点前置的档位排序在 app 侧做，此处不重排）；
    片头钩子恒取**时间序**末球（用户口径），与传入顺序无关。"""
    if not goals:
        raise ValueError("没有选中任何片段")
    data = load_events(os.path.join(work_dir, "events.json"))
    segs = _calib.load_segments_dir(work_dir)
    if not segs:
        raise RuntimeError("缺标定（work 目录无可用 calibration.json）"
                           "——竖屏运镜需要筐几何")
    traces = (load_shooters(work_dir) or {}).get("traces", {})
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV 打不开视频：{video_path}")
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.release()

    clips_dir = os.path.join(work_dir, "vclips")
    os.makedirs(clips_dir, exist_ok=True)
    for old in glob.glob(os.path.join(clips_dir, "*.mp4")):
        try:
            os.remove(old)
        except OSError as ex:    # 旧段被播放器/资源管理器预览锁住（Win32）：
            print(f"⚠ 旧段 {os.path.basename(old)} 删不掉（{ex}），跳过——"
                  "不进本次拼接，无害")
    # 单球时白闪**不可能被用上**：下面组装 seq 的条件是
    # `flash and (hook or j)`，而单球恒 hook=None（_render_hook 要求
    # len(rendered)>=2）、j 恒为 0 ⇒ 永不插入；且 len(seq)==1 走 os.replace，
    # 产物字节与"渲染了但没用"完全一致。单条模式一球一进程，这行省掉 N 次
    # 无用渲染（2026-10-21）。
    flash = (os.path.join(clips_dir, "flash.mp4")
             if cfg.transition == "white_flash" and len(goals) > 1 else None)
    if flash:
        render_flash(flash, cfg)

    files, errs, rendered = [], [], []
    probe = cv2.VideoCapture(video_path)
    try:
        for i, (t, before, after) in enumerate(goals):
            p = os.path.join(clips_dir, f"{i:03d}_{int(t):06d}.mp4")
            try:
                g, centers = _prepare_ball(probe, t, before, after, data, segs,
                                           traces, fw, fh, fps, cfg)
                render_clip(video_path, g, centers, slowmo_window(g, cfg), p, cfg,
                            freeze_s=(cfg.end_freeze_s
                                      if i == len(goals) - 1 else 0.0))
                files.append(p)
                rendered.append((t, before, after))
            except Exception as ex:             # noqa: BLE001——单球不拖垮整片
                errs.append(f"{time_key(t)}: {ex}")
        hook = (_render_hook(probe, video_path, rendered, clips_dir, data, segs,
                             traces, fw, fh, fps, cfg) if cfg.hook else None)
    finally:
        probe.release()
    if not files:
        raise RuntimeError("全部片段渲染失败：" + "；".join(errs))
    seq = [hook] if hook else []          # 钩子在整条最前
    for j, f in enumerate(files):
        if flash and (hook or j):         # 有钩子=每球前都隔白闪（含首球）
            seq.append(flash)
        seq.append(f)
    out = os.path.join(work_dir, safe_filename(
        os.path.splitext(out_name)[0]) + ".mp4")
    if len(seq) == 1:
        os.replace(seq[0], out)
    else:
        concat_clips(seq, out)                  # 参数逐字一致 → 流拷贝秒拼
    normalize_audio(out, cfg)
    if errs:
        print("⚠ 跳过片段：" + "；".join(errs))
    return out
