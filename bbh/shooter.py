"""出手人识别 Phase 1：出手溯源（进球前回扫全帧追出手点）+ 射手缩略图。

独立于现有检测管线——只按需回扫每个进球前几秒（每球 ~3s），产物进
shooters.json（身份键 = 视频 + 标定指纹 + SHOOTER_VERSION，与
DETECT_VERSION/LOGIC_VERSION 完全无关，溯源调参绝不触发全片重检）。
坐标约定：本模块全程用**视频原始帧坐标**（不做位移校正折算）——缩略图
从同一帧上裁剪，同帧坐标系内自洽；出手点只服务于裁剪与展示，不进判定器。
帧时间取容器 PTS（CAP_PROP_POS_MSEC——detector v4 教训：VFR 视频的
帧号/fps 钟比 PTS 早最多 3s）。"""
import glob
import json
import math
import os
from collections import Counter
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageDraw

from .detector import (BAND_RATIO, MAX_AREA_RATIO, MIN_AREA, RIM_Y_RATIO,
                       VideoOpenError, _scaled_rect, safe_fps)
from .models import BallPos, CalibSegment, Calibration, segments_key, time_key
from .pipeline import _calib_key, _normalize_segments
from . import person            # 出手人 person 框（2026-10-15 spike 收编）

SHOOTER_VERSION = 6          # 溯源算法行为变化时 +1（独立版本号，不碰检测/判定）
                             # v4：扫描口径 S 下限地板（SCAN_S_FLOOR）
SHOOTERS_FILE = "shooters.json"
THUMB_DIR = "shooters"
# ---- 阈值初值：Phase 1 验收轮在 225 条标注上标定后才算定案（spec 铁律）----
SCAN_WINDOW_S = 6.0          # 回扫窗口：进球前 6 秒（三分高弧线 ~2s+抢板余量）
SCAN_STEP = 2                # 隔 2 帧取 1 帧（15fps 足够连轨迹，每球 ~90 帧）
GAP_BRIDGE_S = 0.5           # 轨迹断点桥接上限（隔帧采样断口 0.32~0.42s 常见，
                             # 0.3 误断——2026-08-25 全量重放实测）
GAP_NEAR_S = 1.2             # 筐附近长桥接：球过筐板/入网时遮挡 >0.3s 常见
NEAR_HOOP_W = 6.0            # "筐附近"半径（筐宽倍数，自筐心）——长桥接仅限两端都在圈内
V_MAX_RATIO = 30.0           # 相邻采样点速度上限（筐宽/秒）≈13m/s——滤瞬移噪声
HOLD_SPEED_RATIO = 3.0       # 持球段速度上限（筐宽/秒）≈1.4m/s——低于它=还在手里
ANCHOR_S = 1.0               # 锚点候选窗：进球前 1s（入筐前球必接近筐心）
MONO_TOL_W = 0.5             # 单调逼近容差（筐宽倍数，吸收弧顶小幅回摆）
MONO_MAX_SKIP = 5            # 连续单调违反上限：超过=反弹/折返段，链到此截断
                             # （3 误砍近筐抛投爬升段的小幅回摆，40:10.4 实测）
FLAT_TOL_W = 0.2             # 爬升段"变平"容差（筐宽倍数，吸收检测抖动）
RISE_MIN_W = 2.0             # 有效出手的最小爬升（筐宽倍数，手→弧顶）
MIN_TRACK_N = 5              # 🟢 高置信最少轨迹点数
MIN_TRACK_SPAN_S = 0.5       # 🟢 高置信最小轨迹时长（秒）
PERSON_H_CM = 180.0          # 射手身高——像素换算标尺（筐直径 45cm）
CROP_W_CM = 120.0            # 裁剪宽度（肩宽+展开的手臂）
CROP_MARGIN = 1.8            # 透视余量：近相机射手可达筐平面尺度 1.6 倍
                             # （35:51.4 实测射手 324px vs 筐平面推算 204px）
CROP_REL_Y = 0.5             # 出手点在裁剪框竖直方向的位置（球≈髋高，人上下各半）
# ---- v3 验收第二轮返修（2026-08-25，20 问题球归因后全量重放标定）----
SCAN_MAX_BLOBS = 3           # 每帧最多收块数：断球瞬间帧内全是网带碎片，a18
                             # 碎片成当帧最大块反而入链劫持锚点（94:38.9）
SCAN_FILL_MIN = 0.35         # scan 专属形状下限（0.45 误杀被筐沿半遮挡的滚球，
                             # 103:28.3；主检测口径不变）
SCAN_FLOOR_RATIO = 0.35      # 帧内面积地板：≥当帧最大块 35% 才收——筐边小闪斑
                             # （a10~32 仅为球的 4~13%）靠它全灭
RIM_FLICK_W = 2.5            # 筐口圈半径（筐宽倍数）：圈内小碎片需时间邻伴
RIM_FLICK_AREA = 100         # 圈内小于此面积=碎片嫌疑（筐上滚球被半遮挡 a50~99）
RIM_MATE_R_PX = 20.0         # 时间邻伴空间半径（px）
RIM_MATE_HALF_S = 0.2        # 时间邻伴半窗（滚球检出隔帧稀疏，邻伴 ≥1 即留）
STATIC_R_PX = 12.0           # 静点聚类半径：12px 邻域连续在场=静物（横幅 0.91s/
                             # 场下球 0.76s 全灭；真运球底点孤立、胸口携带步进>12px）
STATIC_RUN_S = 0.5           # 连续 run 判静点时长
STATIC_RUN_STEP_S = 0.12     # run 的相邻采样步进上限（隔帧采样步长 0.13s 除外）
STATIC_HALF_S = 0.75         # 覆盖率判据半窗（run 杀不掉的周期性同位复现兜底）
STATIC_COVER = 0.7           # 覆盖率阈值（65:34.5 横幅被真球检出稀释到 56%）
STATIC_SPAN_S = 1.5          # 覆盖率判据最小时间跨度
DIR_TOL_DEG = 65.0           # 建链方向门控扇形：候选须在链尾速度反向延长线 ±65°
SMALL_JUMP_W = 2.0           # 小跳豁免（筐宽倍数）：收球下探 ~60px=1.2筐，局部
                             # 摆动方向无意义（09:42.7：夹角 115° 被门控误杀）
SLOW_JUMP_W = 3.0            # 持球尾不接远处轨迹：接球出手的 release 本就该停在
                             # 接球点，跨段接上把 release 拉向传球人（46:03.4）
RELEASE_GATE_W = 0.8         # 出手走查高度门（筐平面+0.8筐≈肩高以上才可能持球）：
                             # 近垂直落筐弧顶（108:16.0）软上篮末段减速 genuinely
                             # 三步慢，但那里不可能是持球段，不停
DEPTH_SLOPE = 2.5            # 裁剪深度自适应：release_y 每比筐平面深一屏高，标尺
                             # 放大此倍数——近机位球员像素尺寸远超筐平面推算
DEPTH_MAX_SCALE = 2.2        # 深度自适应上限（防贴镜头 runaway）
# ---- A 类修复（2026-09-06，2026-08-29_3 验收 14 问题球归因后）----
SPOT_ABOVE_TOL_W = 0.5       # 出手点物理校验容差：允许高于筐平面 0.5 筐
                             # （吸收标定/检测抖动）。越界=定位失效——链追到
                             # 窗口边界球还在飞（found=False 拿空中点当出手
                             # 估计），出手高度不可能高于筐（看台俯视几何），
                             # 11/434 球缩略图全裁到屋顶/白墙，零误伤
# ---- 贴边门控（2026-09-06 第 2 轮四类缺陷第 2 类）----
SPOT_EDGE_MARGIN = 30        # release 距任一边 < 此值（px，按 fh/720 缩放）
                             # =定位失效：链从画面边缘才开始/球刚入画，出手点
                             # 是窗口截断估计，crop 锚在它上只会拍到裤腿鞋+
                             # 围栏（08-29_3 实测 04:46.4 x=14）。命中 3 键全
                             # 用户报（边距 12.5/14/28.5），好图 38.5/42.5 不进
THUMB_FULL_W = 480           # 定位失效兜底图宽（全场下采样，px）——480 比
                             # 320 全屏放大时人物可辨（用户报「横图看不清」）
# ---- v5 链首在飞的抛物线回推（2026-09-12，09-04_1 段4 定位失效取证）----
EXTRAP_RISE_S = 0.40         # 回推的出手上升时长（秒，尺度无关）：真投篮
                             # 垂直初速 ~3.9m/s → 上升 0.40s，高弧/平投 ±0.1s
                             # → 位置差 ~100-150px，作 review 态提示图够用
EXTRAP_MIN_N = 5             # 参与拟合的下降段最少点数（抛物线三个参数）
EXTRAP_MIN_SPAN_S = 0.25     # 下降段最小时长：无曲率支撑的拟合是外推噪声
EXTRAP_MAX_RESID_W = 0.5     # y 拟合残差 RMS 上限（筐宽倍数）——段4 实测
                             # 真下降链残差 0.9~14.8px（≤0.23 筐），垃圾链拦掉
EXTRAP_TOP_R = 0.15          # 回推点 y 下限（画面顶部 15% = 顶棚区，出手人
                             # 不可能在那——段4 实测 5/46 回推到 y<130 属过冲）
EXTRAP_ABOVE_W = 5.5         # 外推点的筐上容差（筐宽倍数）：远景透视下深位
                             # 出手人合法高于筐平面（段4 实测外推点高出筐平面
                             # 22~319px≈0.3~5.0 筐宽），老 A 类 0.5 筐容差把
                             # 这类全判 bad；顶棚级 absurdity 由 EXTRAP_TOP_R
                             # 与本上限双拦。v6 起只配复扫实测命中（extrap=True
                             # 前提）享受——v5 发给纯拟合位置是 31 球拍灯根因
# ---- v6 外推局部复扫（2026-09-13，GIF 拍灯取证：位置外推不可靠、复扫实测）----
RESCAN_DT_S = 0.05           # 复扫步长（秒）：105:43.6 真球帧间 Δx≈30px
RESCAN_N = 5                 # 复扫帧数（t_rel ~ +0.20s；负时间球被手遮——
                             # 105:43.6 实测 -0.20~0.00s 全无球块，跟 GIF 十字
                             # 同哲学：看不到就不装看到）
RESCAN_X_BAND_W = 2.5        # 预期 x 带半宽（筐宽倍）：x 线性回推误差实测
                             # ~70px≈1.1 筐（105:43.6 回推 1514 vs 真 1584）
RESCAN_V_TOL = 250           # 相邻帧块对速度与链 vx 的容差（px/s）：105:43.6
                             # 真球对差 <30，静态假块（衣/地面反光 Δx≈0）差
                             # ≈|vx| 一眼假；宽松吃 vx 一次拟合误差（透视）
RESCAN_MIN_AREA = 40         # 球块面积门（px²）：主扫描口径同量级放宽
RESCAN_MAX_AREA = 4000       # 上限（px²）：105:43.6 假块 (1507,600) 面积
                             # ~1000 是地面反光条——面积分不出好坏，靠速度滤
RESCAN_Y_ABOVE_W = 3         # 复扫带上界（筐上筐宽倍）：出手人手最高也就
                             # 筐平面附近（段4 实证 173 正常球 y min=344≈筐
                             # 平面 371），筐上 3 筐以上=顶棚带不扫（v6 首版
                             # 全带宽扫出 y=87 顶棚拼对）
RESCAN_MIN_RISE_PX = 8       # 三连链 y 总降幅下限（px）：球刚离手必上升
                             # （t_rel+0.2s < 上升 0.4s），静块拼对 y 不动——
                             # x 速度骗得过（地板标记等距排列），y 单调降骗不过
# ---- 动图缩略图（2026-09-07 定参，两球逐帧取证）----
GIF_PRE_S = 0.7              # 前窗：出手前秒数。收球→急停→起跳→出手全链条
                             # 恰好装下（62:06.2 实测）；再加长是负资产——
                             # 00:06.1 传接球球 -0.7 之前框里是白球衣传球人，
                             # 62:06.2 更早帧只有半人在框的空跑画面（无号脸盲）
GIF_POST_S = 0.4             # 后窗：随挥+球飞向筐=「确系投篮且进了」的证据，
                             # 不能砍（与前窗合 1.1s/9 帧）
GIF_FPS = 8                  # 帧率（spike 实测单球 99~274KB≈静态 4 倍，
                             # 434 球全墙约 65MB；编码+取帧约 1s/球）
GIF_H = 640                  # 输出高（宽随裁剪框比例；2026-09-13 v2 放大：
                             # 240 在墙高 600 上是 2.5 倍上采样显糊，480 接近
                             # 远景源框 1:1 真增清晰度；v4 再放大 480→640——
                             # 墙 columns 10→6 格子翻大后像素密度跟上防糊）
GIF_VERSION = 4              # 动图渲染参数版本（写 shooters.json 的 gif_ver，
                             # 不进身份校验——过期只清旧图按新参重生成，溯源
                             # traces 不作废；v2=GIF_H 480+放大上偏 GIF 框，
                             # v3=跟球双门（S/面积），十字不再落到人头人手，
                             # v4=GIF_H 640 配墙 6 列放大显示）
GIF_CROP_W_GAIN = 1.4        # GIF 框比静态 jpg 框宽 40%：吸收出手点横向偏差
GIF_CROP_H_GAIN = 1.5        # 高 50%：竖直偏差更大（B 类十字低 0.5~1 米）
GIF_CROP_REL_Y = 0.6         # 出手点竖直位置 0.5→0.6 上方多留——v6 复扫
                             # 出手点常在球发力期低位、人在上方（09-13 抽验
                             # 两例十字贴地实证），上偏防人上半身被切
GIF_SEARCH_MARGIN = 0.3      # 球搜索区=裁剪框外扩比例（出手后球短暂出框，
                             # 跟到框边再消失；画仍只在框内）
FOLLOW_S_FLOOR = 140         # 跟球候选鲜活度门（只 _save_gif，不碰扫描口径）：
                             # 09-04_1 原帧取证 348 球——皮肤/头/手块中位 S
                             # 96~136，飞行球 S p5=143，140 卡在空档；跨场抽
                             # 查 08-29_3 出手点 p90S med=172 同样成立。标定
                             # S_hi 更低（暗球馆球整体不鲜活）时自守下调，
                             # min(140, S_hi−25)
FOLLOW_MIN_AREA = 0.10       # 跟球候选面积下限（×筐宽²，只 _save_gif）：真球
                             # ≈0.22（24cm 球径/45cm 筐宽），实测飞行球 0.12~
                             # 0.26；头/手高光鲜活但成细条 ≤0.035——S 门杀
                             # 中饱和大块（腿/臂），本门杀鲜活细条（05-07.6
                             # 光头块仅 10px²）。宁可十字诚实消失（兜底=人工
                             # 核对+「✚ 十字有误」标记），不落人身上
# ---- v4 扫描饱和度地板（2026-09-12，09-04_1 段1 定位失效取证）----
SCAN_S_FLOOR = 88            # 扫描口径 S 下限地板。远景机位球只有几像素，
                             # 标定点到运动模糊/混背景的球像素（实测 S=77）
                             # 再放宽 40 → S_lo=37（其他段 88~140）——顶棚暖
                             # 反光（实测 S 37~80）全过 mask，全帧回扫 67~81%
                             # 检出是顶棚静块，链被劫持到 y=42~80、出手点标
                             # 到顶棚（段1 89/119 球 spot=bad）。真球是高饱和
                             # 橙，地板只杀反光不碰球（段1 全量重放：bad
                             # 74.8%→5.0%，high 8→85）；对 S_lo≥88 的标定为
                             # no-op（四场已验收场 88/90/90/140）。只作用扫描
                             # 口径——标定数据不动：主检测用收紧 HSV 实测会
                             # 丢 2 个 high 真进球（穿越帧模糊球像素 S<100）


def _scan_hsv(calib: Calibration) -> tuple:
    """扫描口径 HSV（scan_release_window 与 _save_gif 跟球共用）：S 下限
    抬到 SCAN_S_FLOOR；S_hi 不高于地板时不抬——标定整体低饱和是合法
    场景，抬出空 mask 会把这种场全判 low。"""
    lo = list(calib.hsv_low)
    if calib.hsv_high[1] > SCAN_S_FLOOR:
        lo[1] = max(lo[1], SCAN_S_FLOOR)
    return np.array(lo, np.uint8), np.array(calib.hsv_high, np.uint8)


@dataclass
class TraceResult:
    """一个进球的溯源结果。status 三档（沿用事件档位词）：high=轨迹完整
    且找到持球→离手速度跃变；review=有轨迹但短/断/追到窗口边界（出手点
    位置可能偏，打标前多瞟预览）；low=未溯源（无可用轨迹，不出缩略图）。"""
    status: str
    found: bool = False       # 是否找到持球→离手跃变（False=链首当出手估计）
    release_t: float | None = None
    release_xy: tuple | None = None
    track_n: int = 0
    track_span: float = 0.0
    extrap: bool = False      # v5：出手点来自下降段抛物线回推（远景深位，
                              # 上升段 0 检出的兜底估计；spot 校验放宽容差）
    person: str = "none"      # person 框归属档（ball/in/expand/near/none）：
                              # crop_shooter 写回，进 shooters.json 供展示层
                              # （v1 后端记录，前端暂不显示）


def _key_safe(key: str) -> str:
    """行键 → 文件名安全（'01:23.7' 的冒号在 Windows 文件名非法）。"""
    return key.replace(":", "-")


def _segs_of(calib) -> list:
    """溯源入参归一化：单标定 → 全片一段；段列表原样（已排序/首段 t0=0
    校验在 pipeline._normalize_segments）。逐球的筐几何按事件时刻所属
    段取（A2 多段标定：挪镜头后各段各的几何，跟主检测同一分段口径）。"""
    return _normalize_segments(calib, None)


def _identity(calib) -> list:
    """缓存身份：单标定 = 旧 calib_key（存量 shooters.json 零作废）；
    多段 = segments_key（增删段/挪边界 → 段组合变了 → 整档重扫）。"""
    if isinstance(calib, Calibration):
        return _calib_key(calib)
    return segments_key(_segs_of(calib))


def _t_of_key(key: str) -> float:
    """行键 'm:ss.s' → 秒（事件时刻；按它找所属段的筐几何）。"""
    m, s = key.split(":")
    return int(m) * 60 + float(s)


def _seg_for_t(segs: list, t: float) -> CalibSegment:
    """时刻 → 所属段：最后一个 t0≤t 的段（与 pipeline 段窗 [t0,下一段)
    同口径；t 早于首段 t0=0 不可能——防御性回退首段）。"""
    for s in reversed(segs):
        if t >= s.t0:
            return s
    return segs[0]


def save_shooters(work_dir: str, video: str, calib, traces: dict,
                  gif_ver=None) -> None:
    """shooters.json 落盘（原子替换；编排层逐球调用——中断后已完成的球不丢）。

    gif_ver 是**磁盘 GIF 文件**的渲染参数版本，不是代码版本：不传（None）
    = 严格镜像磁盘——有则保留、没有则保持没有。migrate 在 trace_video 首行
    对带 spot 的档必然重写，若无中生有盖章当前值，紧随其后的过期检查读到
    的已是新值、永不清旧图（09-13 09-04_1 实发：旧 240 图冒充 v2 未重
    生成）。只有实际清除/重生成 GIF 的路径显式传 GIF_VERSION。"""
    if gif_ver is None:
        gif_ver = (load_shooters(work_dir) or {}).get("gif_ver")
    rec = {"video": video, "calib": _identity(calib),
           "shooter_ver": SHOOTER_VERSION, "traces": traces}
    if gif_ver is not None:
        rec["gif_ver"] = gif_ver
    os.makedirs(work_dir, exist_ok=True)
    p = os.path.join(work_dir, SHOOTERS_FILE)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def load_shooters(work_dir: str) -> dict | None:
    """读 shooters.json；缺失/任何损坏（半截 JSON/非 UTF-8/非 dict）返回
    None 不抛（positions.json 同款容错）。"""
    try:
        with open(os.path.join(work_dir, SHOOTERS_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def shooters_valid(data: dict | None, video: str, calib) -> bool:
    """缓存身份校验：同视频 + 同标定指纹 + 同溯源版本（复用 positions.json
    的身份模式；重标定/升版本 → 整档作废重扫）。"""
    return bool(data) and data.get("video") == video \
        and data.get("calib") == _identity(calib) \
        and data.get("shooter_ver") == SHOOTER_VERSION


def _dist(a, b) -> float:
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5


def _d_hoop(p, cx, cy) -> float:
    return ((p.x - cx) ** 2 + (p.y - cy) ** 2) ** 0.5


def _speed(a, b, hoop_w) -> float:
    """相邻点速度（筐宽/秒）；dt≤0（同帧多点）返回 inf 不参与慢速判定。"""
    dt = b.t - a.t
    if dt <= 0:
        return float("inf")
    return _dist(a, b) / hoop_w / dt


def _pick_anchor(pts: list, t_goal: float, cx: float, cy: float):
    """锚点=进球前 ANCHOR_S 内距筐心最近的检出点（无候选则全窗最近）。

    旧版取"时间最后一点"被场边静态橙物劫持（每个 6s 窗口都在、末尾
    每帧都在）：01:23.7/25:08.1 链被锁到静物上、红十字标到别处。真球
    入筐前必接近筐心，按距离选锚即把静物排除（它们离筐 ~18 筐宽）。"""
    cands = [p for p in pts if p.t >= t_goal - ANCHOR_S]
    pool = cands or pts
    return min(pool, key=lambda p: (p.x - cx) ** 2 + (p.y - cy) ** 2)


def _thin_rim_flicker(pts: list, hoop_c: tuple, hoop_w: float) -> list:
    """筐口圈碎片时间邻伴过滤：筐口圈（d≤RIM_FLICK_W 筐）内的小块
    （a<RIM_FLICK_AREA）需 ±RIM_MATE_HALF_S 内 RIM_MATE_R_PX 邻域还有
    ≥1 个检出。断球瞬间网带碎片 a18 单帧孤立→灭；筐上滚球被筐沿半遮挡
    a50~99 连续滚动（103:28.3 实测，rim 面积地板会误杀它；滚球检出隔帧
    稀疏，邻伴 ≥1 即留）。"""
    cx, cy = hoop_c
    out = []
    for p in pts:
        if (_d_hoop(p, cx, cy) > RIM_FLICK_W * hoop_w
                or p.area >= RIM_FLICK_AREA):
            out.append(p)
            continue
        mates = sum(1 for q in pts
                    if q is not p and abs(q.t - p.t) <= RIM_MATE_HALF_S
                    and (q.x - p.x) ** 2 + (q.y - p.y) ** 2
                    <= RIM_MATE_R_PX ** 2)
        if mates >= 1:
            out.append(p)
    return out


def _kill_statics(pts: list, hoop_c: tuple, hoop_w: float) -> list:
    """静点剔除（只作用于距筐 >NEAR_HOOP_W 的远场点——筐口停留/篮板下
    持球是真桥接材料，必须保）。判据二选一——
    a) 连续 run：STATIC_R_PX 邻域内连续在场 ≥STATIC_RUN_S（横幅 0.91s
       连续、场下球 0.76s 连续全灭；真运球底点孤立单帧、胸口携带步进
       >12px，run 均 <0.3s）；
    b) 帧覆盖率：±STATIC_HALF_S 内 ≥STATIC_COVER 采样帧在同 r px 内
       （65:34.5 横幅被同期真球检出稀释到 56%，靠 a) 灭；周期性同位
       复现靠 b) 兜底；跨度 ≥STATIC_SPAN_S 才有资格）。"""
    cx, cy = hoop_c
    far = [p for p in pts
           if _d_hoop(p, cx, cy) > NEAR_HOOP_W * hoop_w]
    times = sorted({round(p.t, 3) for p in pts})   # 有检出的采样帧全集
    dead = set()
    for p in far:
        near_t = {round(q.t, 3) for q in pts
                  if (q.x - p.x) ** 2 + (q.y - p.y) ** 2 <= STATIC_R_PX ** 2}
        if not near_t:
            continue
        # a) 连续 run：near_t 排序后最长连续步进（≤STATIC_RUN_STEP_S 视为
        # 相邻采样）时长
        run_best = run_cur = 0.0
        prev = None
        for t in sorted(near_t):
            run_cur = run_cur + (t - prev) if (prev and t - prev
                                               <= STATIC_RUN_STEP_S) else 0.0
            run_best = max(run_best, run_cur)
            prev = t
        if run_best >= STATIC_RUN_S:
            dead.add(id(p))
            continue
        # b) 覆盖率（跨度 ≥STATIC_SPAN_S 才有资格）
        if max(near_t) - min(near_t) < STATIC_SPAN_S:
            continue
        uni = {t for t in times
               if p.t - STATIC_HALF_S <= t <= p.t + STATIC_HALF_S}
        if uni and len(near_t & uni) >= STATIC_COVER * len(uni):
            dead.add(id(p))
    return [p for p in pts if id(p) not in dead]


def _dir_ok(p, chain: list, hoop_w: float, cx: float, cy: float) -> bool:
    """方向门控：候选 p 应落在链尾速度的反向延长线 ±DIR_TOL_DEG 扇形内。

    - 链只有锚点：无速度上下文，放行（V_MAX 已过滤瞬移）；
    - 小跳（≤SMALL_JUMP_W 筐）放行：收球下探 ~60px=1.2筐，局部摆动
      方向无意义（09:42.7：探底点速度向上、持球点在上方夹角 115° 误杀）；
    - 尾部在筐附近 NEAR_HOOP_W 内：反弹/补篮方向本来就反转、筐口停留→
      飞行逼近的链路常 >3 筐宽，豁免一切只留 V_MAX；
    - 尾速 <HOLD（持球，中远场）：方向未定，但不接远处轨迹（≤SLOW_JUMP_W
      筐）——接球出手的 release 本就该停在接球点，跨段接上反而把 release
      拉向传球人（46:03.4 实测）。"""
    if len(chain) < 2:
        return True
    tail, nxt = chain[-1], chain[-2]      # tail=链尾(最早)，nxt=次尾(较晚)
    if _dist(p, tail) <= SMALL_JUMP_W * hoop_w:
        return True
    if _d_hoop(tail, cx, cy) <= NEAR_HOOP_W * hoop_w:
        return True
    if _speed(tail, nxt, hoop_w) < HOLD_SPEED_RATIO:
        return _dist(p, tail) <= SLOW_JUMP_W * hoop_w
    vx, vy = nxt.x - tail.x, nxt.y - tail.y   # 链尾速度（时间正向）
    bx, by = p.x - tail.x, p.y - tail.y       # 回溯方向（往更早）
    dot = -(vx * bx + vy * by)                # 反向延长线：b≈-v 时 dot>0
    cos = dot / (((vx ** 2 + vy ** 2) * (bx ** 2 + by ** 2)) ** .5 + 1e-9)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos)))) <= DIR_TOL_DEG


def _build_chain(pts: list, anchor, hoop_w: float, cx: float, cy: float) -> list:
    """从锚点倒序建链：按帧时刻分组，每步取距链尾最近的可行前驱。

    可行=间隔 ≤ 桥接上限（两端都在筐附近放宽到 GAP_NEAR_S）、速度
    ≤V_MAX_RATIO（瞬移噪声跳过该点、链继续往早收）且过方向门控
    _dir_ok（跨段轨迹不接：传球/反弹方向反折的候选拒收）。返回时间正序链。"""
    groups: dict[float, list] = {}
    for p in pts:
        if p.t <= anchor.t + 1e-9:
            groups.setdefault(round(p.t, 3), []).append(p)
    ts_desc = sorted((t for t in groups if t < anchor.t - 1e-9), reverse=True)
    chain = [anchor]
    for t in ts_desc:
        gap = chain[-1].t - t
        near_ok = (_d_hoop(chain[-1], cx, cy) <= NEAR_HOOP_W * hoop_w
                   and any(_d_hoop(p, cx, cy) <= NEAR_HOOP_W * hoop_w
                           for p in groups[t]))
        if gap > ((GAP_NEAR_S if near_ok else GAP_BRIDGE_S) + 1e-9):
            break                      # +ε：恰 0.5s 缺口浮点上是 0.5000…04
        feas = [p for p in groups[t]
                if _speed(p, chain[-1], hoop_w) <= V_MAX_RATIO
                and _dir_ok(p, chain, hoop_w, cx, cy)]
        if feas:
            chain.append(min(feas, key=lambda p: _dist(p, chain[-1])))
    return list(reversed(chain))


def _trim_approach(chrono: list, hoop_w: float, cx: float, cy: float) -> list:
    """单调逼近修剪：从锚点侧倒序只收"距筐心不比后一点近"的点。

    投篮飞行段到筐心距离随时间单调递减（弧顶 ±0.5 筐宽内小幅回摆），
    违反单调=折返垃圾点（V_MAX 放进来的跳变）或上一攻的反弹段——
    连续违反超 MONO_MAX_SKIP 即远离筐方向，链到此截断。"""
    keep = [chrono[-1]]
    skips = 0
    for p in reversed(chrono[:-1]):
        if _d_hoop(p, cx, cy) >= _d_hoop(keep[-1], cx, cy) - MONO_TOL_W * hoop_w:
            keep.append(p)              # append 尾部=最近已收点（时间更早侧）
            skips = 0
        else:
            skips += 1
            if skips > MONO_MAX_SKIP:
                break
    keep.reverse()
    return keep


def _release_from_ascent(chrono: list, hoop_w: float, rim_y: float):
    """出手=最终爬升段的起点：从弧顶（链上最低 y）倒序走。

    往回走球越来越低（y 增大）即接近手；停于两种情形——
    a) 更早的点反而更高（y 小超容差）：已越过手（传球下落段/出手前
       球在手上方），b) 连续三步慢速（<HOLD）**且 y≥筐平面+RELEASE_GATE_W
       筐**（≈人体肩高以上才可能持球）：持球段开始。高度门 v3 新增——
       近垂直落筐弧顶（108:16.0，y<筐平面）与软上篮末段减速（105:57.5）
       都会出现 genuinely 三步慢，但那里不可能是持球段，不停。
       两步慢不够：慢速抛投的弧顶减速就是两步慢（29:33.8 实测），三步
       才把"接近筐的减速顶"与"接球/持球停顿"分开。补篮取补篮起点而非
       第一投，接球出手停在接球点而非传球人。返回 (出手点, found)；
       found=False=链首就在飞/全程慢，链首当估计。"""
    apex = min(range(len(chrono)), key=lambda i: chrono[i].y)

    def _hold_ahead(i: int) -> bool:
        """从 i 往前数三步慢速（不足三步到链首=链首就在持球段）。"""
        for j in (i, i - 1, i - 2):
            if j < 0:
                return True
            if _speed(chrono[j], chrono[j + 1], hoop_w) >= HOLD_SPEED_RATIO:
                return False
        return True

    r = apex
    for i in range(apex - 1, -1, -1):
        if chrono[i].y < chrono[r].y - FLAT_TOL_W * hoop_w:
            break                        # a) 更早反而更高
        if (chrono[i].y >= rim_y + RELEASE_GATE_W * hoop_w
                and _hold_ahead(i)):
            break                        # b) 连续三步慢=持球段（限肩高以上）
        r = i
    found = (r > 0
             and chrono[apex].y <= chrono[r].y - RISE_MIN_W * hoop_w)
    return chrono[r], found


def _extrap_release(chrono: list, hoop_w: float, frame_size, t_goal: float):
    """链首就在飞（found=False、估计点在筐上）时的抛物线回推：对下降段
    （弧顶→锚点侧）拟合 y 二次，取拟合弧顶回推 EXTRAP_RISE_S 得出手时刻；
    x 一次拟合得恒定水平速度 vx（真实飞行 vx 恒定、透视下 x 基本线性——
    v5 的 x 二次项 157.6 实测是拟合噪声）。远景机位出手→弧顶的上升段球
    只有几像素+运动模糊，HSV 逐帧放宽参数实测 0 检出（09-04_1 段4：
    46/219 球链只含下降段、链首=弧顶在筐平面上方被 A 类全判 bad；而下降
    链是真抛物线，拟合残差 0.9~14.8px）。

    v6（2026-09-13 GIF 拍灯取证）：只出时刻+vx——图像空间抛物线位置外推
    结构性不可靠（透视下表观垂直加速度沿轨迹剧变 ~5 倍：筐附近拟合
    a=299、出手点附近真值 ≈1400；0.5s 下降段局部拟合外推 0.77s 位置错
    (220,200)px，33 外推球 31 个筐上=GIF 拍灯）。位置由调用方 rescanner
    在 t_rel 时刻局部复扫实测（球刚离手就在出手人手边，105:43.6 原型
    (1584,412)→(1463,298) 上升轨迹、x 速度与链一致）。BallPos 的 x=线性
    回推占位（可信 ~70px）、y=拟合值占位——rescanner 未命中即整体弃用；
    贴边/贴顶门控保留，语义是回推时刻质量的代理检查（时刻若把球放到
    顶棚/框外=回推过冲）。

    门控全不过则返回 None（保持链首估计诚实判 bad）——非抛物线（残差/
    曲率）、回推点贴画面顶（顶棚区）/出边/出扫窗都不拿拟合当出手点。"""
    apex = min(range(len(chrono)), key=lambda i: chrono[i].y)
    desc = chrono[apex:]
    if (len(desc) < EXTRAP_MIN_N
            or desc[-1].t - desc[0].t < EXTRAP_MIN_SPAN_S):
        return None
    ts = np.array([p.t for p in desc])
    xs = np.array([p.x for p in desc])
    ys = np.array([p.y for p in desc])
    t0 = float(ts[0])
    fx = np.polyfit(ts - t0, xs, 1)
    fy = np.polyfit(ts - t0, ys, 2)
    resid = float(np.sqrt(np.mean((np.polyval(fy, ts - t0) - ys) ** 2)))
    if resid > EXTRAP_MAX_RESID_W * hoop_w:
        return None
    a, b, _ = fy
    if a <= 0:
        return None                # 拟合不出上开口抛物线=数据非下降弧
    t_rel = -b / (2 * a) - EXTRAP_RISE_S
    if t0 + t_rel < t_goal - SCAN_WINDOW_S - 0.2:
        return None                # 回推出 6s 扫窗（出手早于窗口）
    fw, fh = frame_size
    xe = float(np.polyval(fx, t_rel))
    ye = float(np.polyval(fy, t_rel))
    m = SPOT_EDGE_MARGIN * fh / 720.0
    if not (m <= xe <= fw - m
            and EXTRAP_TOP_R * fh <= ye <= fh - m):
        return None                # 贴边/贴顶/出框：窗口截断或过冲，不是出手人
    return BallPos(t=t0 + t_rel, x=xe, y=ye, area=0, fi=0), float(fx[0])


def trace_release(points: list, t_goal: float, hoop_w: float,
                  hoop_c: tuple, frame_size=None, rescanner=None) -> TraceResult:
    """进球前回追轨迹，取"最后一次入筐飞行"的出手点（v3，验收第二轮返修）。

    六步（对应 2026-08-25 验收第二轮 20 问题球的归因）：
    1. 筐口碎片时间邻伴过滤（断球帧 a18 网带碎片单帧孤立→灭：94:38.9）；
    2. 静点剔除（只作用远场：横幅/场下球 12px 邻域连续 ≥0.5s 或覆盖率
       ≥70% →灭：65:34.5/109:32.8；筐口停留/篮板下持球是桥接材料，保）；
    3. 锚点=进球前 ANCHOR_S 内距筐心最近的检出点（防场边静物劫持）；
    4. 倒序建链（多块、最近可行前驱、近筐长桥接、方向门控+小跳豁免：
       09:42.7 收球下探 115° 误杀、46:03.4 传球跨段误接）；
    5. 单调逼近修剪（剔折返垃圾点/上一攻反弹段）；
    6. 爬升段取出手（弧顶倒序走，三步慢限肩高以上=持球段：108:16.0）。
    found=False（链首就在飞/全程慢）→ 链首当出手估计、判 review（出手
    可能早于窗口/链起点，spec：照样出结果但标出来）。轨迹点 <2 → low。"""
    pts = sorted((p for p in (points or []) if p.t <= t_goal + 0.05),
                 key=lambda p: p.t)
    pts = _thin_rim_flicker(pts, hoop_c, hoop_w)
    pts = _kill_statics(pts, hoop_c, hoop_w)
    if len(pts) < 2:
        return TraceResult(status="low")
    cx, cy = hoop_c
    anchor = _pick_anchor(pts, t_goal, cx, cy)
    chrono = _build_chain(pts, anchor, hoop_w, cx, cy)
    if len(chrono) < 2:
        return TraceResult(status="low")
    trimmed = _trim_approach(chrono, hoop_w, cx, cy)
    if len(trimmed) >= 2:
        chrono = trimmed
    r, found = _release_from_ascent(chrono, hoop_w, cy)
    extrap = False
    if (not found and frame_size is not None
            and r.y < cy - SPOT_ABOVE_TOL_W * hoop_w):
        alt = _extrap_release(chrono, hoop_w, frame_size, t_goal)
        if alt is not None:
            est, vx = alt
            # v6：位置不信抛物线外推（31/33 拍灯），只在 rescanner 于 t_rel
            # 复扫实测命中时升级（见 _rescan_release）；None/无 rescanner
            # =不升级，链首估计照旧诚实判 bad（v5 前行为）
            hit = (rescanner(est.t, vx, chrono[-1].x, chrono[-1].t)
                   if rescanner else None)
            if hit is not None:
                r = BallPos(t=est.t, x=float(hit[0]), y=float(hit[1]),
                            area=0, fi=0)
                extrap = True
    span = chrono[-1].t - chrono[0].t
    status = ("high" if found and len(chrono) >= MIN_TRACK_N
              and span >= MIN_TRACK_SPAN_S else "review")
    return TraceResult(status=status, found=found, release_t=r.t,
                       release_xy=(r.x, r.y), track_n=len(chrono),
                       track_span=round(span, 2), extrap=extrap)


def _detect_source(video_path: str, work_dir: str | None) -> str:
    """检测源：OpenCV 打不开原片时退回转码副本（pipeline 检测时生成过
    det-source.mp4，只转一次）。打不开且无副本 → 原样返回，由调用方报错。"""
    cap = cv2.VideoCapture(video_path)
    ok = cap.isOpened()
    cap.release()
    if ok:
        return video_path
    if work_dir:
        alt = os.path.join(work_dir, "det-source.mp4")
        if os.path.exists(alt):
            return alt
    return video_path


def scan_release_window(video_path: str, calib: Calibration, t_goal: float,
                        work_dir: str | None = None,
                        window_s: float = SCAN_WINDOW_S,
                        step: int = SCAN_STEP):
    """回扫 [t_goal-window_s, t_goal] 的**全帧** HSV 球检测。

    复用标定 HSV 与 detector 的面积/形状/筐带过滤（语义与主检测一致），
    只是区域从筐口 ROI 扩为全画面——出手点在三分线，主检出流从未覆盖。
    相邻进球窗口重叠无冲突：每球独立扫描、独立缓存。窗口跨界截断到 0。
    帧时间取 PTS（读不出时回退 t0+帧号/fps，同 detector 的兜底策略）。
    返回 (检出点列表, (frame_w, frame_h))。"""
    src = _detect_source(video_path, work_dir)
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise VideoOpenError(f"无法打开视频: {video_path}")
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fps = safe_fps(cap.get(cv2.CAP_PROP_FPS))
    hx, hy, hw, hh = _scaled_rect(calib, frame_w, frame_h)
    rim_y, band = hy + hh * RIM_Y_RATIO, hh * BAND_RATIO
    max_area = hw * hw * MAX_AREA_RATIO
    hsv_lo, hsv_hi = _scan_hsv(calib)   # S 下限地板：灭顶棚类低饱反光（v4）
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    t0 = max(0.0, t_goal - window_s)
    out: list[BallPos] = []
    use_pts = None        # None=未判定（首帧 PTS=0 无法区分正常与不支持）
    fi = 0
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, t0 * 1000.0)
        while True:
            if not cap.grab():
                break
            msec = cap.get(cv2.CAP_PROP_POS_MSEC)
            if use_pts is None and fi > 0:
                use_pts = msec > 0
            t = msec / 1000.0 if use_pts else t0 + fi / fps
            if t >= t_goal:
                break                          # 窗口上界先判后收：不收 t≥t_goal 的点
            if fi % step == 0:
                ok, frame = cap.retrieve()
                if ok:
                    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
                    mask = cv2.inRange(hsv, hsv_lo, hsv_hi)
                    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
                    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                               cv2.CHAIN_APPROX_SIMPLE)
                    # v3 多块扫描：断球瞬间帧内全是网带碎片，a18 碎片成当帧
                    # 最大块（v2 只收最大块=喂劫持材料，94:38.9）。多收前
                    # SCAN_MAX_BLOBS 块 + 帧内面积地板（≥当帧最大块 35%）：
                    # 真球在碎片帧也能进，筐边小闪斑（a10~32）靠地板全灭，
                    # 静物靠后处理 _kill_statics 兜底。筐身条带内静态橙与
                    # 主检测同口径忽略
                    blobs = []
                    for c in cnts:
                        a = cv2.contourArea(c)
                        if a < MIN_AREA or a > max_area:
                            continue
                        (bcx, bcy), r = cv2.minEnclosingCircle(c)
                        if r <= 0 or a / (math.pi * r * r) < SCAN_FILL_MIN:
                            continue
                        if abs(bcy - rim_y) < band:
                            continue
                        blobs.append((a, bcx, bcy))
                    blobs.sort(reverse=True)
                    floor = SCAN_FLOOR_RATIO * blobs[0][0] if blobs else 0
                    picked = [b for b in blobs
                              if b[0] >= max(floor, 2 * MIN_AREA)]
                    for a, bcx, bcy in picked[:SCAN_MAX_BLOBS]:
                        out.append(BallPos(t=t, x=bcx, y=bcy, area=a, fi=fi))
            fi += 1
    finally:
        cap.release()
    return out, (frame_w, frame_h)


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _grab_at(video_path: str, t: float, work_dir: str | None = None):
    """按秒取一帧（裁缩略图专用）。POS_MSEC 定位在 VFR 下有 ±0.1s 级
    偏差——对"裁人"足够（不影响时刻判定，时刻由 scan 的检出点决定）。"""
    cap = cv2.VideoCapture(_detect_source(video_path, work_dir))
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
        ok, frame = cap.read()
        return frame if ok else None
    finally:
        cap.release()


def _crop_box(rx: float, ry: float, fw: int, fh: int, hw: float,
              rim_y: float) -> tuple:
    """射手裁剪框（纯函数，便于测试）。

    尺寸换算以筐宽为标尺（筐直径 45cm）再放大 CROP_MARGIN 余量（射手不在
    筐平面上，近相机尺度可达 1.6 倍）。出手点是持球位置（髋/胸高），
    头顶在其上方 150~250px——上界只抬 0.3 筐宽(≈15px)会把头整个切掉
    （35:51.4/04:42.5 验收教训），故出手点置于框竖直中线 CROP_REL_Y。

    v3 深度自适应：固定筐平面标尺 ×1.8 余量装不下近机位球员（03:38.8
    等 6 球人像不全；帧验证 52:13.2/40:10.4 等 release_y 748~900 时下半
    身超框）——release_y 每比筐平面 rim_y 深一屏高，标尺再放大
    DEPTH_SLOPE 倍（上限 DEPTH_MAX_SCALE），裁后仍由缩略图端归一。"""
    px_per_cm = hw / 45.0
    scale = min(DEPTH_MAX_SCALE,
                1.0 + DEPTH_SLOPE * max(0.0, ry - rim_y) / fh)
    crop_h = PERSON_H_CM * px_per_cm * CROP_MARGIN * scale
    crop_w = CROP_W_CM * px_per_cm * CROP_MARGIN * scale
    x0 = _clamp(int(rx - crop_w / 2), 0, fw - 1)
    y0 = _clamp(int(ry - CROP_REL_Y * crop_h), 0, fh - 1)
    x1 = _clamp(x0 + int(crop_w), 1, fw)
    y1 = _clamp(y0 + int(crop_h), 1, fh)
    return x0, y0, x1, y1


def _gif_crop_box(rx: float, ry: float, fw: int, fh: int, hw: float,
                  rim_y: float) -> tuple:
    """GIF 专用裁剪框：静态 _crop_box 同源标尺再放大（宽 ×GIF_CROP_W_GAIN、
    高 ×GIF_CROP_H_GAIN、出手点竖直位置 GIF_CROP_REL_Y 上偏）。静态 jpg 是
    110px 格子里的快速浏览、口径不动；GIF 是放大细看的认人主画面——出手点
    有横向/竖直偏差（B 类十字低、vx 回推误差），框大+上偏让偏差球也装得下
    出手人。放大后必然包含静态框（各边外扩），跟球十字/搜索区随框同比走。"""
    x0, y0, x1, y1 = _crop_box(rx, ry, fw, fh, hw, rim_y)
    gw = round((x1 - x0) * GIF_CROP_W_GAIN)
    gh = round((y1 - y0) * GIF_CROP_H_GAIN)
    x0 = _clamp(int(rx - gw / 2), 0, fw - 1)
    y0 = _clamp(int(ry - GIF_CROP_REL_Y * gh), 0, fh - 1)
    x1 = _clamp(x0 + gw, 1, fw)
    y1 = _clamp(y0 + gh, 1, fh)
    return x0, y0, x1, y1


def release_spot_bad(calib: Calibration, release_xy, fw: int, fh: int,
                     above_w: float = SPOT_ABOVE_TOL_W) -> bool:
    """出手点物理校验（A 类+贴边）：筐平面以上 或 贴画面边 → 定位失效。

    A 类：看台俯视几何下出手高度不可能高于筐；越界的一定是链追到窗口
    边界球还在飞（found=False 空中估计）或假斑轨迹（2026-08-29_3 实测
    11 球全裁到屋顶/白墙）。容差 SPOT_ABOVE_TOL_W 筐吸收标定/检测抖动，
    该片 423 筐下球零误伤。above_w 参数供外推估计点放宽（v5，见
    spot_bad_for——远景透视下深位出手合法高于筐平面，09-04_1 段4）。
    贴边：release 距任一边 < SPOT_EDGE_MARGIN（fh/720 缩放）=窗口截断
    估计（链从边缘才开始/球刚入画），crop 锚在它上只会拍到边角杂物。
    08-29_3 边距分档实测：用户报问题球 12.5/14/28.5 全进，实测好图
    38.5/42.5 不进——纯 y 门控无区分度（y=613 真球员膝下 vs y=501 围栏
    红锥），边距是比 y 更硬的截断证据。"""
    if not release_xy:
        return False
    rx, ry = release_xy
    margin = SPOT_EDGE_MARGIN * fh / 720.0
    if min(rx, ry, fw - rx, fh - ry) < margin:
        return True
    _, hy, hw, hh = _scaled_rect(calib, fw, fh)
    return ry < hy + hh * RIM_Y_RATIO - above_w * hw


def spot_bad_for(tr: TraceResult, calib: Calibration, fw: int, fh: int) -> bool:
    """trace 落 shooters.json spot 字段的判定：外推估计点（v5 extrap）用
    放宽筐上容差 EXTRAP_ABOVE_W——它的可信度来自抛物线拟合质量与贴顶/
    出框门控，不是筐上几何；老 0.5 筐容差会把远景深位合法出手全数误判
    （09-04_1 段4：46/219 球）。非外推点维持原严口径不动。"""
    return release_spot_bad(calib, tr.release_xy, fw, fh,
                            above_w=(EXTRAP_ABOVE_W if tr.extrap
                                     else SPOT_ABOVE_TOL_W))


def _warn_badge(img):
    """定位失效兜底图左上角画 ⚠（黄三角+黑叹号）——图脱离墙单独流转
    （已归人面板/导出）时警示仍在。纯几何绘制（叹号 ASCII，无字体依赖）；
    边长≈图宽 15%（480 宽→72px，首版 70% 图宽大三角被认成场地元素）。"""
    w = img.shape[1]
    side = max(24, round(w * 0.15))
    x0, y0 = round(w * 0.05), round(w * 0.05)
    tri = np.array([[x0, y0], [x0 + side, y0],
                    [x0 + side // 2, y0 + side]], np.int32)
    cv2.fillPoly(img, [tri], (0, 215, 255))
    cv2.putText(img, "!", (x0 + side // 2 - round(side * 0.08),
                           y0 + round(side * 0.8)),
                cv2.FONT_HERSHEY_SIMPLEX, side / 64.0, (0, 0, 0),
                max(1, round(side / 24)), cv2.LINE_AA)


def _rescan_release(video_path: str, calib: Calibration, work_dir: str,
                    t_rel: float, vx: float, x_ref: float, t_ref: float):
    """外推球的局部复扫（v6，trace_release 的 rescanner 真实现）：抛物线
    只保证时刻 t_rel 可靠（位置不可靠——透视表观曲率剧变），t_rel 后
    RESCAN_N 帧逐帧宽松 HSV 检块，在 x 线性回推预期带内找相邻帧速度 ≈vx
    且 y 单调上升的三连球块链——球刚离手就在出手人手边且必在上升（
    t_rel+0.2s < 上升期 0.4s；105:43.6 实测 +0.00s (1584,412)→(1463,298)，
    x 速度 -600 与链 vx -590 一致）；负时间球被手遮检测不到，不扫（同
    GIF 十字哲学：看不到就不装看到）。三连+y 单调是硬门：两帧拼对能被
    地板标记/顶棚桁架的等距排列骗过 x 速度（首版回放 33 全命中里混着
    y=810 地板、y=87 顶棚拼对，105:43.6 真球 (1584,412) 反而落选），三
    点等速+递减同时成立的静块排列概率暴跌。筐上 RESCAN_Y_ABOVE_W 筐以
    上（顶棚带）不扫。返回三连里两段速度合计最贴 vx 的链首 (x, y)；无
    可信链 → None（外推不升级，退链首诚实判 bad）。"""
    lo = np.array([max(0, calib.hsv_low[0] - 3),
                   max(0, calib.hsv_low[1] - 30),
                   max(0, calib.hsv_low[2] - 20)])
    hi = np.array([min(179, calib.hsv_high[0] + 5), 255, 255])
    kern = np.ones((3, 3), np.uint8)
    frames = []
    for i in range(RESCAN_N):
        t = t_rel + i * RESCAN_DT_S
        frame = _grab_at(video_path, t, work_dir)
        if frame is None:
            continue
        fh, fw = frame.shape[:2]
        _, hy, hw, hh = _scaled_rect(calib, fw, fh)
        m = SPOT_EDGE_MARGIN * fh / 720.0
        x_c = x_ref + vx * (t - t_ref)      # 链尾线性回推的预期 x（±2.5 筐）
        mask = cv2.morphologyEx(
            cv2.inRange(cv2.cvtColor(frame, cv2.COLOR_BGR2HSV), lo, hi),
            cv2.MORPH_OPEN, kern)
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
        blobs = []
        for cnt in cnts:
            area = cv2.contourArea(cnt)
            if not (RESCAN_MIN_AREA <= area <= RESCAN_MAX_AREA):
                continue
            M = cv2.moments(cnt)
            if not M["m00"]:
                continue
            bx, by = M["m10"] / M["m00"], M["m01"] / M["m00"]  # 全帧坐标
            if (abs(bx - x_c) > RESCAN_X_BAND_W * hw
                    or not (hy + hh * RIM_Y_RATIO - RESCAN_Y_ABOVE_W * hw
                            <= by <= fh - m)):
                continue
            blobs.append((bx, by))
        if blobs:
            frames.append((t, blobs))
    best = None
    for i in range(len(frames) - 2):        # 三连链：i → i+1 → i+2
        (ta, ba), (tb, bb), (tc, bc) = frames[i], frames[i + 1], frames[i + 2]
        for xa, ya in ba:
            for xb, yb in bb:
                v1 = (xb - xa) / (tb - ta)
                if abs(v1 - vx) >= RESCAN_V_TOL:
                    continue
                for xc, yc in bc:
                    if (abs((xc - xb) / (tc - tb) - vx) >= RESCAN_V_TOL
                            or not (ya >= yb >= yc
                                    and ya - yc >= RESCAN_MIN_RISE_PX)):
                        continue
                    score = abs(v1 - vx) + abs((xc - xb) / (tc - tb) - vx)
                    if best is None or score < best[0]:
                        best = (score, xa, ya)
    return None if best is None else (best[1], best[2])


def _save_full_frame(frame, thumb_dir: str, safe: str) -> list[str]:
    """定位失效兜底缩略图：出手时刻全场下采样（THUMB_FULL_W 宽）+ ⚠。
    不裁框不画十字——出手点不可信，裁了只会裁到屋顶/白墙（A 类教训）。
    返回相对 work_dir 路径（编码失败返回 []）。"""
    fh, fw = frame.shape[:2]
    w = min(THUMB_FULL_W, fw)
    img = cv2.resize(frame, (w, max(1, round(fh * w / fw))))
    _warn_badge(img)
    ok, buf = cv2.imencode(".jpg", img)
    if not ok:
        return []
    buf.tofile(os.path.join(thumb_dir, f"{safe}_full.jpg"))
    return [f"{THUMB_DIR}/{safe}_full.jpg"]


def crop_shooter(video_path: str, calib: Calibration, tr: TraceResult,
                 work_dir: str, key: str, back_s: float = 0.3) -> list[str]:
    """裁出手人缩略图，两帧：出手瞬间(_a) + 出手前 back_s 秒持球姿势帧
    (_b，通常更好认)。帧 _a 的出手点画红十字。

    裁剪几何优先 **person 框**（person.pick_person 球锚归属，2026-10-15
    spike 36 球 0 认错）：出手帧没检到人再退持球姿势帧；都没人/开关关/
    推理失败 → 出手点定宽居中（_crop_box 老路径）。归属档写 tr.person。

    出手点在筐平面以上（release_spot_bad）→ 单张全场兜底图（A 类）。
    裁剪框出画面边界 clamp（spec：不硬给图——clamp 后退化 <20px 视同
    取帧失败返回 []）。返回相对 work_dir 路径。"""
    tr.person = "none"
    if tr.release_t is None or tr.release_xy is None:
        return []
    rx, ry = tr.release_xy
    frame_a = _grab_at(video_path, tr.release_t, work_dir)
    frame_b = _grab_at(video_path, tr.release_t - back_s, work_dir)
    if frame_a is None:
        return []
    fh, fw = frame_a.shape[:2]
    if release_spot_bad(calib, tr.release_xy, fw, fh):
        os.makedirs(os.path.join(work_dir, THUMB_DIR), exist_ok=True)
        return _save_full_frame(
            frame_a, os.path.join(work_dir, THUMB_DIR), _key_safe(key))
    p_hit, pbox = None, None
    hsv_lo, hsv_hi = _scan_hsv(calib)
    for fr in (frame_a, frame_b):        # 就近帧优先：出手时刻没检到人再退
        if fr is None:
            continue
        p_hit, pbox = person.pick_person(fr, hsv_lo, hsv_hi, tr.release_xy)
        if pbox is not None:
            break
    if pbox is not None:
        x0, y0, x1, y1 = person.crop_rect(pbox, fw, fh)
        tr.person = p_hit
    else:
        hx, hy, hw, hh = _scaled_rect(calib, fw, fh)
        x0, y0, x1, y1 = _crop_box(rx, ry, fw, fh, hw,
                                   hy + hh * RIM_Y_RATIO)
    if x1 - x0 < 20 or y1 - y0 < 20:
        return []
    safe = _key_safe(key)
    thumb_dir = os.path.join(work_dir, THUMB_DIR)
    os.makedirs(thumb_dir, exist_ok=True)
    a = frame_a[y0:y1, x0:x1].copy()
    cx, cy = int(rx) - x0, int(ry) - y0         # 出手点十字（帧 _a 标记）
    if 0 <= cx < (x1 - x0) and 0 <= cy < (y1 - y0):
        cv2.line(a, (cx - 8, cy), (cx + 8, cy), (0, 0, 255), 2)
        cv2.line(a, (cx, cy - 8), (cx, cy + 8), (0, 0, 255), 2)
    # ⚠ 本机铁律（2026-08-15 实测）：cv2.imwrite 遇中文路径静默失败
    # （返回 False 不落盘），work_dir 常含中文视频名 → imencode+tofile 落盘
    ok_a, buf_a = cv2.imencode(".jpg", a)
    if not ok_a:
        return []
    buf_a.tofile(os.path.join(thumb_dir, f"{safe}_a.jpg"))
    if frame_b is not None:
        ok_b, buf_b = cv2.imencode(".jpg", frame_b[y0:y1, x0:x1])
        if ok_b:
            buf_b.tofile(os.path.join(thumb_dir, f"{safe}_b.jpg"))
            return [f"{THUMB_DIR}/{safe}_a.jpg", f"{THUMB_DIR}/{safe}_b.jpg"]
    return [f"{THUMB_DIR}/{safe}_a.jpg"]


def gif_rel(key: str) -> str:
    """动图相对路径 shooters/{key}.gif——显示层「优先动图、缺了退静态」的
    唯一状态源。刻意不进 shooters.json：thumbs 列表不动（外观特征吃
    _a.jpg），文件存在性即真相，回填幂等口径同 spot=bad（失败不标字段
    下次重试）。"""
    return f"{THUMB_DIR}/{_key_safe(key)}.gif"


def _save_gif(video_path: str, calib: Calibration, work_dir: str,
              key: str, tr: dict) -> bool:
    """出手点居中动图 → shooters/{key}.gif（单 cap 顺序读取
    [release-GIF_PRE_S, release+0.4] 就近贴帧、_crop_box 同款框、resize
    高 GIF_H、**十字逐帧跟球走**——每帧在搜索区跑 HSV 球检测，候选过
    跟球双门（中位 S≥FOLLOW_S_FLOOR、面积≥FOLLOW_MIN_AREA×筐宽²，见
    _ball_near；比扫描口径严——扫描吃穿越帧模糊球，跟球宁缺勿错），
    十字画在离上一帧位置最近的合格球块上；该帧无球块则不画（十字消失=
    这帧没看到球，诚实闪烁；固定的出手点红十字仍在 _a.jpg 静态图上）。
    跟错/丢球的兜底是人工核对本身，与 C 类假斑同哲学，不做更重的跟踪。

    裁剪框锚定出手点全程不动（人相对框动——spike 两球目视验证认人足够）；
    取帧失败的帧跳过不整球作废，<2 帧才放弃。
    返回是否落盘（False=下次点击重试，不写任何状态）。"""
    rt, xy = tr.get("release_t"), tr.get("release_xy")
    if rt is None or not xy:
        return False
    rx, ry = xy
    cap = cv2.VideoCapture(_detect_source(video_path, work_dir))
    try:
        if not cap.isOpened():
            return False
        fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        if not fw or not fh:
            return False
        hx, hy, hw, hh = _scaled_rect(calib, fw, fh)
        x0, y0, x1, y1 = _gif_crop_box(rx, ry, fw, fh, hw,
                                       hy + hh * RIM_Y_RATIO)
        if x1 - x0 < 20 or y1 - y0 < 20:
            return False

        # 球搜索区 = 裁剪框外扩 30%：出手后球飞向筐会短暂出框，外扩让
        # 十字跟到框边再消失（画仍只在框内）；无筐带过滤——搜索区在
        # 人身上不在筐上，球跨筐平面高度时十字不该凭空消失
        mx = round((x1 - x0) * GIF_SEARCH_MARGIN)
        my = round((y1 - y0) * GIF_SEARCH_MARGIN)
        sx0, sy0 = max(0, x0 - mx), max(0, y0 - my)
        sx1, sy1 = min(fw, x1 + mx), min(fh, y1 + my)
        hsv_lo, hsv_hi = _scan_hsv(calib)   # 同 scan 口径（含 v4 S 地板）
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        max_area = hw * hw * MAX_AREA_RATIO
        # 跟球双门（v3）：标定 S_hi 更低时鲜活度门自守下调，防空门杀光球
        s_floor = min(FOLLOW_S_FLOOR, int(hsv_hi[1]) - 25)
        follow_min_area = FOLLOW_MIN_AREA * hw * hw

        def _ball_near(sub, prev):
            """搜索区内找离 prev 最近的合格球块（全帧坐标），无则 None。
            距离上限=半框对角线：球被手遮住时宁可让十字消失，也不跳到
            远处无关橙块（观众衣/地板反光）上。候选双门（v3）：块中位
            S≥FOLLOW_S_FLOOR（杀皮肤/衣料中饱和大块）且面积≥0.10×筐宽²
            （杀头/手高光细条）——「就近」规则天然偏向慢速人体，09-04_1
            实测旧门下 02-49.3 九帧全锁出手人小腿，双门后人块出局、
            无合格块即诚实消失。"""
            hsv = cv2.cvtColor(sub, cv2.COLOR_BGR2HSV)
            mask = cv2.morphologyEx(cv2.inRange(hsv, hsv_lo, hsv_hi),
                                    cv2.MORPH_OPEN, kernel)
            cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
            cands = []
            for c in cnts:
                a = cv2.contourArea(c)
                if a < follow_min_area or a > max_area:
                    continue
                (bcx, bcy), r = cv2.minEnclosingCircle(c)
                if r <= 0 or a / (math.pi * r * r) < SCAN_FILL_MIN:
                    continue
                blob = np.zeros(mask.shape, np.uint8)
                cv2.drawContours(blob, [c], -1, 255, -1)
                if float(np.median(hsv[:, :, 1][blob > 0])) < s_floor:
                    continue
                cands.append((bcx + sx0, bcy + sy0))   # 局部→全帧坐标
            if not cands:
                return None
            px, py = prev
            bcx, bcy = min(cands,
                           key=lambda p: (p[0] - px) ** 2 + (p[1] - py) ** 2)
            if math.hypot(bcx - px, bcy - py) > 0.5 * math.hypot(x1 - x0,
                                                                 y1 - y0):
                return None
            return bcx, bcy

        # 取帧（第一趟只选不画）：窗口起点一次定位+顺序读（真源实测
        # 0.47s vs 逐帧 seek 2.56s——每目标就近贴帧，|dt|≤半步 62ms 视觉
        # 无感）；视频头 clamp 到 0。存搜索区原始帧，绘制趟才有全分辨率
        # 可检（检测吃原图面积，不能在缩放后做）
        targets = [rt - GIF_PRE_S + i / GIF_FPS
                   for i in range(int((GIF_PRE_S + GIF_POST_S) * GIF_FPS) + 1)]
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, targets[0]) * 1000.0)
        best: dict[int, tuple] = {}       # 目标序号 → (|dt|, 搜索区帧)
        while True:
            ok, fr = cap.read()
            if not ok:
                break
            t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if t > targets[-1] + 0.5 / GIF_FPS:
                break                     # 过窗尾（半步余量）
            for i, tg in enumerate(targets):
                if i in best and best[i][0] <= abs(t - tg):
                    continue
                sub = fr[sy0:sy1, sx0:sx1]
                if sub.size:
                    best[i] = (abs(t - tg), sub)
        if len(best) < 2:
            return False
        # 绘制趟（第二趟）：时间序逐帧检测+画十字，prev 链保连续。编码坑
        # （v3 实证）：真帧场景渐变把 GIF 自适应调色板吃满 256 色，~50px
        # 纯红十字分不到色槽被整块量化吞掉（真帧实测编码后红像素=0、合成
        # 纯色背景则存活——夹具须色块墙背景才测得出）——先共享量化到
        # 255 色、纯红钉进第 256 槽，十字按调色板索引写入，红必存活
        prev = (float(rx), float(ry))
        frames = []
        pal_img = None
        for i in sorted(best):
            sub = best[i][1]
            pos = _ball_near(sub, prev)
            if pos:
                prev = pos
            crop = sub[y0 - sy0:y1 - sy0, x0 - sx0:x1 - sx0]
            tw = max(1, round(crop.shape[1] * GIF_H / crop.shape[0]))
            img = Image.fromarray(cv2.cvtColor(
                cv2.resize(crop, (tw, GIF_H)), cv2.COLOR_BGR2RGB))
            if pal_img is None:    # 首帧定共享色板（255 色，纯红占第 256 槽）
                pal_img = img.quantize(colors=255)
            p = img.quantize(palette=pal_img)
            if pos:
                cx = round((pos[0] - x0) * tw / (x1 - x0))
                cy = round((pos[1] - y0) * GIF_H / (y1 - y0))
                if 0 <= cx < tw and 0 <= cy < GIF_H:   # 出框不画
                    arr = np.asarray(p).copy()
                    arr[max(0, cy - 6):cy + 7, max(0, cx - 1):cx + 2] = 255
                    arr[max(0, cy - 1):cy + 2, max(0, cx - 6):cx + 7] = 255
                    p = Image.fromarray(arr)
            p.putpalette(list(pal_img.getpalette())[:255 * 3] + [255, 0, 0])
            frames.append(p)
        if len(frames) < 2:
            return False
        thumb_dir = os.path.join(work_dir, THUMB_DIR)
        os.makedirs(thumb_dir, exist_ok=True)
        # 中文路径铁律同 imencode+tofile：PIL 走文件对象而非路径字符串。
        # 不 optimize：它按首帧用色裁色板，首帧无十字时钉死的红槽被裁掉
        with open(os.path.join(thumb_dir, f"{_key_safe(key)}.gif"), "wb") as f:
            frames[0].save(f, format="GIF", save_all=True,
                           append_images=frames[1:],
                           duration=round(1000 / GIF_FPS), loop=0)
        return True
    finally:
        cap.release()


def _gif_backfill(video_path: str, calib, work_dir: str, traces: dict):
    """动图回填/生成（生成器）：{key}.gif 缺失的已溯源球逐个补，每球 yield
    进度、return 成功数。幂等只认文件存在性、不写 shooters.json——旧缓存
    与新扫的球统一走这一条路（crop_shooter 的 jpg 产物/thumbs 列表不动）。
    spot=bad 球跳过：全场兜底图无裁剪框可依、出手点不可信，动起来只会把
    错误十字放大成动画。calib 可为多段列表——每球按事件时刻所属段取几何。"""
    segs = _segs_of(calib)
    todo = [k for k, v in traces.items()
            if isinstance(v, dict) and v.get("release_t") is not None
            and v.get("release_xy") and v.get("spot") != "bad"
            and not os.path.exists(os.path.join(work_dir, gif_rel(k)))]
    for i, k in enumerate(todo, 1):
        try:
            ok = _save_gif(video_path, _seg_for_t(segs, _t_of_key(k)).calib,
                           work_dir, k, traces[k])
        except Exception:            # 单球失败不拖垮整批（主循环同款纪律）
            ok = False
        if ok:
            yield f"🎬 生成射手动图（{i}/{len(todo)}）：{k}"
    return sum(1 for k in todo
               if os.path.exists(os.path.join(work_dir, gif_rel(k))))


STATUS_TEXT = {"high": "🟢 轨迹完整", "review": "🟡 轨迹存疑",
               "low": "⚪ 未溯源"}


def migrate_spot_bad(video_path: str, calib, work_dir: str) -> int:
    """旧缓存迁移（A 类+贴边，SHOOTER_VERSION 不动）：spot 缺失=首代旧档
    逐键补判；spot=ok=A 类时代已迁移档，贴边门控上线后要重判（08-29_3
    全档即此态）。几何判定零取帧成本（分辨率一次读全档共用），判 bad 才
    取帧换全场图。幂等——重判落 bad 后不再进；换图落盘失败不标字段
    （下次重试），避免 bad 标记配着旧屋顶图。返回换图球数。
    calib 可为多段列表：逐键按事件时刻所属段取几何（A2）。"""
    data = load_shooters(work_dir)
    if not shooters_valid(data, video_path, calib):
        return 0
    traces = data.get("traces") or {}
    segs = _segs_of(calib)
    cap = cv2.VideoCapture(video_path)
    fw, fh = int(cap.get(3)), int(cap.get(4))
    cap.release()
    if not fw or not fh:
        return 0
    fixed = 0
    for key, v in traces.items():
        if not isinstance(v, dict) or v.get("spot") not in (None, "ok"):
            continue
        t, xy = v.get("release_t"), v.get("release_xy")
        if not xy or not release_spot_bad(
                _seg_for_t(segs, _t_of_key(key)).calib, tuple(xy), fw, fh):
            v.setdefault("spot", "ok")     # 无 spot 旧档补 ok；ok 档不动
            continue
        frame = _grab_at(video_path, t, work_dir) if t is not None else None
        if frame is None:
            continue
        os.makedirs(os.path.join(work_dir, THUMB_DIR), exist_ok=True)
        rel = _save_full_frame(
            frame, os.path.join(work_dir, THUMB_DIR), _key_safe(key))
        if not rel:
            continue
        v["spot"], v["thumbs"] = "bad", rel
        fixed += 1
    if fixed or any(isinstance(v, dict) and "spot" in v
                    for v in traces.values()):
        save_shooters(work_dir, video_path, calib, traces)
    return fixed


def _purge_gifs(work_dir: str) -> int:
    """身份失效全量重扫时清除全部旧动图——回填幂等只认文件存在性，锚旧
    出手点的动图会永久残留冒充新图（v5→v6 拍灯教训）。返回删除张数。"""
    thumb_dir = os.path.join(work_dir, THUMB_DIR)
    gifs = glob.glob(os.path.join(thumb_dir, "*.gif")) \
        if os.path.isdir(thumb_dir) else []
    for p in gifs:
        try:
            os.remove(p)
        except OSError:
            pass                       # 只读属性等：删不掉的由回填存在性兜底
    return len(gifs)


def _regen_person_thumbs(video_path: str, calib, work_dir: str,
                         traces: dict):
    """person 归属/裁剪参数（person.PERSON_VERSION）过期的球只重裁缩略图。

    不作废溯源（对照 SHOOTER_VERSION 作废=全量重扫，代价大）：从 traces
    重建 TraceResult 走新版 crop_shooter，写回 thumbs/person/person_ver。
    逐球落盘（中断重跑只补剩下的——person_ver 已当前的球不进 todo）；
    重裁取帧失败（返回 []）保留旧图不盖章，下次重试。GIF 不在此期
    （锚出手点未变，无需重生成）。"""
    segs = _segs_of(calib)
    todo = [k for k, v in traces.items()
            if v.get("release_t") and v.get("release_xy")
            and v.get("person_ver") != person.PERSON_VERSION]
    for i, key in enumerate(todo, 1):
        v = traces[key]
        sc = _seg_for_t(segs, _t_of_key(key)).calib
        tr = TraceResult(status=v.get("status", "low"),
                         release_t=v["release_t"],
                         release_xy=tuple(v["release_xy"]))
        try:
            thumbs = crop_shooter(video_path, sc, tr, work_dir, key)
        except Exception:              # noqa: BLE001 —— 单球失败不拖垮整批
            continue
        if not thumbs and v.get("thumbs"):
            continue                   # 取帧失败：留旧图，不盖章，下次重试
        v["thumbs"], v["person"] = thumbs, tr.person
        v["person_ver"] = person.PERSON_VERSION
        save_shooters(work_dir, video_path, calib, traces)
        yield f"🖼 出手人缩略图重裁升级 person 框（{i}/{len(todo)}）：{key}"


def trace_video(video_path: str, calib, work_dir: str, events: list):
    """逐球 溯源→裁缩略图→写盘。生成器：每完成一球 yield 进度文案、
    结束 return 汇总文案。已有缓存（身份相符）的球跳过——中断后重跑只
    补缺（逐球写盘，已完成的不白扫，spec：同检测检查点思路）。
    calib 可为多段列表（A2）：每球按事件时刻所属段取筐几何/HSV——挪镜头
    后的段用自己段的标定溯源，跟主检测同一分段口径。

    单球任何异常不 raise：记 status=low + err 出错原因继续下一球——
    生成器中途 raise 会让 UI 按钮永久卡灰（gen_selected 教训）。"""
    fixed = migrate_spot_bad(video_path, calib, work_dir)   # 旧档先补 A 类标记
    if fixed:
        yield (f"⚠ {fixed} 球出手点定位失效（筐平面以上）——已换出手时刻"
               "全场图，墙上按 ⚠ 标记人工核对该批")
    data = load_shooters(work_dir)
    # data.get("traces") or {}：防身份相符但缺 traces 键的畸形档（KeyError）
    if shooters_valid(data, video_path, calib):
        traces = dict(data.get("traces") or {})
        # GIF 渲染参数版本（gif_ver）不进身份校验：过期只清旧图按新参
        # 重生成——溯源 traces 不作废（对照 SHOOTER_VERSION 作废=全量重扫）
        if data.get("gif_ver") != GIF_VERSION:
            purged = _purge_gifs(work_dir)
            save_shooters(work_dir, video_path, calib, traces,
                          gif_ver=GIF_VERSION)   # 落盘防重复清
            if purged:
                yield (f"🗑 清除旧版射手动图 {purged} 张"
                       "（动图渲染已升级，重生成中）")
        # person 归属/裁剪参数版本同哲学：过期只重裁缩略图（旧档球补
        # person 框裁剪与归属档），必须在「未重扫」早退**之前**跑
        yield from _regen_person_thumbs(video_path, calib, work_dir, traces)
    else:
        traces = {}
        # 身份失效=全量重扫：旧动图锚的是旧出手点，不删则回填跳过、拍灯图
        # 永久残留（v5→v6 教训）；删了由回填按新位置重生成。无条件先登记
        # 新 gif_ver——purge=0 的新场也要（否则逐球镜像档一直无 gif_ver，
        # 下次进来把刚生成的图再白清一遍），中断在第一球前也不让旧值残档
        purged = _purge_gifs(work_dir)
        save_shooters(work_dir, video_path, calib, {}, gif_ver=GIF_VERSION)
        if purged:
            yield f"🗑 清除旧版射手动图 {purged} 张（出手点已重定位，重扫后重生成）"
    todo = [e for e in (events or []) if time_key(e.t) not in traces]
    if not todo:
        made = yield from _gif_backfill(video_path, calib, work_dir, traces)
        extra = f"，补生成动图 {made} 张" if made else ""
        return (f"✅ 出手人已识别（{len(traces)} 球缓存齐全，未重扫{extra}）——"
                "点表格任意一行查看射手缩略图")
    segs = _segs_of(calib)
    for k, e in enumerate(todo, 1):
        key = time_key(e.t)
        sc = _seg_for_t(segs, e.t).calib   # 该球时刻所属段的标定几何
        try:
            pts, size = scan_release_window(video_path, sc, e.t, work_dir)
            hx, hy, hoop_w, hh = _scaled_rect(sc, size[0], size[1])
            # 筐心=筐口中心（rim_y 同主检测口径）；锚点/单调修剪都以它为原点
            hoop_c = (hx + hoop_w / 2, hy + hh * RIM_Y_RATIO)
            tr = trace_release(
                pts, e.t, hoop_w, hoop_c, frame_size=size,
                rescanner=lambda t_rel, vx, x_ref, t_ref: _rescan_release(
                    video_path, sc, work_dir, t_rel, vx, x_ref, t_ref))
            thumbs = crop_shooter(video_path, sc, tr, work_dir, key)
            err = None
        except Exception as ex:  # noqa: BLE001 —— 单球失败不拖垮整批
            tr, thumbs, err = TraceResult(status="low"), [], str(ex)
        traces[key] = {"status": tr.status, "release_t": tr.release_t,
                       "release_xy": (list(tr.release_xy)
                                      if tr.release_xy else None),
                       "track_n": tr.track_n, "track_span": tr.track_span,
                       "thumbs": thumbs, "err": err, "extrap": bool(tr.extrap),
                       "spot": ("bad" if spot_bad_for(tr, sc, *size)
                                else "ok"),
                       "person": tr.person,
                       "person_ver": person.PERSON_VERSION}
        save_shooters(work_dir, video_path, calib, traces)   # 逐球写盘
        yield (f"⏳ 识别出手人（{k}/{len(todo)}）：{key} → "
               f"{STATUS_TEXT.get(tr.status, tr.status)}")
    # 残键修剪：重检测可能让事件时刻跨 0.1s 键界——旧键不再是任何事件的
    # 行键，剔除后再计数（否则"共 N 球"虚高）；修剪发生在最后一次逐球
    # 写盘之后 → 补一次落盘
    valid = {time_key(e.t) for e in events}
    n_before = len(traces)
    traces = {k: v for k, v in traces.items() if k in valid}
    if len(traces) != n_before:
        save_shooters(work_dir, video_path, calib, traces)
    made = yield from _gif_backfill(video_path, calib, work_dir, traces)
    gif_note = f"，🎬 动图 {made} 张" if made else ""
    n = Counter(v.get("status", "low") for v in traces.values())
    n_err = sum(1 for tr in traces.values() if tr.get("err"))
    err_note = f"（其中 {n_err} 球扫描异常）" if n_err else ""
    return (f"✅ 出手人识别完成：🟢 {n.get('high', 0)} · "
            f"🟡 {n.get('review', 0)} · ⚪ {n.get('low', 0)}{err_note}{gif_note}"
            f"（共 {len(traces)} 球，缓存 shooters.json——点表格行看射手"
            "缩略图，照常打标签）")
