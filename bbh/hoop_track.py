# -*- coding: utf-8 -*-
"""筐锚点定位：用 YOLO 的 hoop 类（class 1）做**绝对观测**，消 ORB 配准的漂移。

问题
----
镜头位移靠 ORB 全画面匹配取位移中位数（`bbh/motion.py`）——它不给绝对值，
只给「相对参考帧动了多少」，误差随时间累积；视频中途挪机位而没补标定时，
整段偏 0.35~0.68 个筐宽（判定门 0.30 review / 0.45 kill）。四场 1409 帧
实测（诊断/2026-11-07-筐识别/结论_只标一次验证.md）：筐检出 99.86%、
误检 0.21%，扣掉段内固定偏置后残差中位 0.71px。

做法
----
**不改 positions 的坐标语义**（那是 ORB 已校正的量），事后叠一个低频校正项：

    x_cal = x_raw − shift_orb(t) − c(t)
    c(t)  = 时间中值( Δ_hoop(t) − shift_orb(t) )
    Δ_hoop(t) = 筐心(t) − 筐心(标定参考帧)

绝对基准取**标定参考帧**上的筐心（一帧，检测时算一次存下来，重放不再跑
推理）。这样「模型框中心 vs 用户手点框中心」的固定偏置（实测 6.9~8.2px）
在相减时自动抵消。**不能**改用「残差中位数自估零点」：漂移常是单向累积的
（ORB 跟丢就不回来），中位数会把真实漂移吸收掉一半，反而把前半段**正确**
的几何拉走——修错比不修更糟。没有参考帧基准时直接放弃校正。

c(t) 时间尺度是分钟级（20s 采样 + 3 窗中值），判定几何保持准静态——逐帧
抖动只有 ~0.7px，远小于门限；反过来若逐帧跟随，`cross_off` 会跟着抖，
静止链/网分/冷却判据全要重新对拍。3 窗中值既能抹掉单点误检，又完整保留
整机平移造成的真实台阶。

为什么不能拿 Δ_hoop 直接替换 ORB
--------------------------------
摇镜（俯仰/偏转）是旋转，画面近处与远处位移不同（视差）。实测 12 个镜头
突变点里 4 个是摇镜，各区域位移差达 152~231px（5.8 个筐宽）。此时筐的位移
与球的位移本来就不相等，用筐单点的位移去纠正全画面球点位，会在球所在区域
引入新误差。所以 hoop 只用于消「ORB 解释不了的慢分量」，全局位移仍由 ORB
提供。突变只持续 0.8~1.0s（画面全糊、球检不到），20s 采样自然跳过，其价值
是**突变结束后立刻重新锚定**——正好等价于手工补一段标定的作用。
"""
import math
import os
from statistics import median

from .motion import interp_shift

HOOP_ENABLE = True          # 采样总开关；关掉逐条回到 v20（无 hoop 字段的旧档亦然）
# 逐帧搬点（v22 起默认关）：把 c(t) 真的加到球点与 shift 上。**已实测净亏**
# ——五场真值（剪辑台 selection.json）查准 +0.60pct / 查全 −0.92pct，10-17_1
# 单场查全 −9.87pct（15 个真进球被降 review），见
# 诊断/2026-11-07-筐识别/结论_v21验收.md。原因两条：①比赛画面里筐框自带
# 5~9px 持续偏置（同刻干净帧 688.8/47.5、0.5s 后 695/35），中值滤波只能去
# 单点尖峰、去不掉常数项，量级 0.15~0.25 筐宽已逼近 review 门 0.30；②段内
# ORB 本来就准（10-17_1 段2 101min 真进球 cross_off 四分段 +0.008/+0.059/
# +0.091/+0.070 无趋势），加进去的是常数而非漂移。采样与统计仍保留——段间
# 桥接、变焦提示都要用，且桥接是「两个干净静止帧相减」，偏置自动对消。
HOOP_SHIFT_ENABLE = False

SAMPLE_EVERY = 600          # 每 600 绝对帧采一次筐（30fps ≈ 20s）。实测 10s/20s
                            # 采样插值误差 P90 均 ≤0.08 筐宽，20s 足够且省一半推理。
CONF_MIN = 0.08             # 筐框置信度下限（实测最低 0.19——镜头刚移动、有
                            # 运动模糊的帧，留一倍余量）
SIZE_JUMP = 0.20            # 段内尺寸一致性：偏离段内中位数超过 20% → 判变焦/
                            # 误检（区分「手点框与模型框一贯差 1.24 倍」与突变）
SIZE_ABS_LO, SIZE_ABS_HI = 0.4, 2.5   # 绝对尺寸合理性（相对标定筐宽/筐高）
MED_WIN = 3                 # 时间中值滤波窗（样本数）。去单点误检，保真实台阶
                            # （3 点中位对 [0,0,0,20,20,20] 只在台阶处换值）
MIN_SAMPLES = 3             # 少于此数无法构成时间序列，整段放弃校正


def enabled() -> bool:
    """采样总开关（环境变量 BBH_HOOP=0 临时关闭，便于对拍）。"""
    v = os.environ.get("BBH_HOOP")
    if v is None:
        return HOOP_ENABLE
    return str(v).strip().lower() not in ("0", "false", "off", "no")


def shift_enabled() -> bool:
    """逐帧搬点开关（环境变量 BBH_HOOP_SHIFT=1 临时打开做实验）。

    与 enabled() 分离：采样默认开（段间桥接/变焦提示都要用），搬点默认关
    （净亏，见 HOOP_SHIFT_ENABLE 注释）。关掉搬点时 build_correction 仍会
    跑一遍——只用来出「疑似变焦/锚点异常」提示，不改 positions。
    """
    v = os.environ.get("BBH_HOOP_SHIFT")
    if v is None:
        return HOOP_SHIFT_ENABLE
    return str(v).strip().lower() not in ("0", "false", "off", "no")


# --------------------------------------------------------------- 采样（在线）
def pick_hoop(res, hoop_class: int = 1, conf_min: float = CONF_MIN,
              ref_size=None):
    """单帧推理结果 → (cx, cy, w, h, conf) 画面坐标，或 None（无合格筐框）。

    ref_size=(w,h)：有标定筐尺寸时**按尺寸最接近**选，而不是按置信度——同一
    画面里常有第二个"像筐"的东西（观众席、备用架），置信度更高但几何不对；
    20s 采样实测按 conf 选会漏掉 19% 的点（选到错框后尺寸对不上被尺寸门丢）。
    无 ref_size 时退回最高置信度（旧行为）。"""
    boxes = getattr(res, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return None
    try:
        cls = boxes.cls.cpu().numpy().astype(int)
        conf = boxes.conf.cpu().numpy()
        xyxy = boxes.xyxy.cpu().numpy()
    except (AttributeError, ValueError):
        return None
    cands = []
    for i in range(len(cls)):
        if cls[i] != int(hoop_class) or float(conf[i]) < conf_min:
            continue
        x1, y1, x2, y2 = (float(v) for v in xyxy[i][:4])
        w, h = x2 - x1, y2 - y1
        if w <= 0 or h <= 0:
            continue
        pen = 0.0
        if ref_size:
            rw, rh = w / float(ref_size[0]), h / float(ref_size[1])
            if not (SIZE_ABS_LO <= rw <= SIZE_ABS_HI) \
                    or not (SIZE_ABS_LO <= rh <= SIZE_ABS_HI):
                continue
            pen = abs(math.log(rw)) + abs(math.log(rh))
        cands.append((pen, -float(conf[i]), i))
    if not cands:
        return None
    k = min(cands)[2]
    x1, y1, x2, y2 = (float(v) for v in xyxy[k][:4])
    return (x1 + x2) / 2.0, (y1 + y2) / 2.0, x2 - x1, y2 - y1, float(conf[k])


class HoopSampler:
    """检测循环内的筐采样器：按帧号节流，从同一次推理结果里顺带取筐框。

    零额外推理成本——YOLO 本来就在整帧上跑（`crop="full"`），只多解析一类框。
    采样点存**画面像素坐标**（不减 shift）：坐标换算留给事后
    `build_correction`，因为非因果的中值滤波需要全部样本。

    storage：外部列表（断点续跑由检查点预置、随采样实时增长，这样检查点
    存下去的就是全量锚点，续跑不会丢前半段）。None=自建。
    """

    def __init__(self, hoop_class: int = 1, every: int | None = None,
                 conf_min: float = CONF_MIN, storage: list | None = None,
                 ref_size=None):
        self.hoop_class = int(hoop_class)
        self.every = int(SAMPLE_EVERY if every is None else every)
        self.conf_min = float(conf_min)
        self.ref_size = ref_size                 # (w,h) 标定筐尺寸（选框用）
        self.samples: list = storage if storage is not None else []

    def due(self, fi: int) -> bool:
        """该帧是否到采样节拍。断点续跑会跳过部分节拍——采样本就稀疏
        （20s 一个），缺几个点只让插值区间变长，不影响正确性。"""
        return self.every > 0 and fi % self.every == 0

    def feed(self, res, t: float, fi: int, off_x: float = 0.0,
             off_y: float = 0.0) -> bool:
        """到节拍且有合格筐框 → 记一条并返回 True。"""
        if not self.due(fi):
            return False
        box = pick_hoop(res, self.hoop_class, self.conf_min, self.ref_size)
        if box is None:
            return False
        cx, cy, w, h, cf = box
        self.samples.append([round(float(t), 3), round(off_x + cx, 2),
                             round(off_y + cy, 2), round(w, 2), round(h, 2),
                             round(cf, 3)])
        return True


# ------------------------------------------------------- 校正项（事后，可重放）
def _medfilt(values: list[float], win: int) -> list[float]:
    """一维时间中值滤波（边缘复制填充）。保留真实台阶、抹掉单点尖峰：
    [0,0,0,20,20,20] 只在台阶处换值；[0,50,0] 被压回 0。"""
    n = len(values)
    if win < 3 or n < 3:
        return list(values)
    half = win // 2
    padded = [values[0]] * half + list(values) + [values[-1]] * half
    return [median(padded[i:i + win]) for i in range(n)]


def build_correction(samples, shift_samples, calib_rect, ref_center,
                     med_win: int = MED_WIN):
    """采样表 + ORB 位移表 + 参考帧筐心 → (corr, stats)。

    ref_center：(cx, cy) 或 [cx, cy, ...]——**标定参考帧**上模型筐框的中心
    （画面像素，与检测画面同一分辨率）。这是绝对基准：Δ_hoop(t) =
    center(t) − ref_center 把「模型框中心 vs 用户手点框中心」的固定偏置
    （实测 6.9~8.2px）自动抵消掉，剩下的才是真实位移。

    为什么不用「残差中位数」自估零点：漂移常常是单向累积的（ORB 一旦跟丢
    就不回来）。若用中位数当零点，真实漂移会被当成偏置吸收掉一半——前半
    段的**正确**几何反而被拉走，比不修更糟。没有绝对基准时宁可不校正。

    corr: [(t, dx, dy)]——t 时刻筐位相对「ORB 已解释位移」的额外偏移；线性
    插值后即 c(t)。空列表 = 无需/无法校正（调用方必须原样返回所有数据，
    这是「旧档逐字节等价 v20」的保证）。
    stats: 诊断计数（丢弃原因、最大残差），进 warning 与报告。
    """
    stats = {"n_in": 0, "n_used": 0, "n_size": 0, "max_res": 0.0, "why": ""}
    if not samples or not calib_rect:
        stats["why"] = "no_samples"
        return [], stats
    stats["n_in"] = len(samples)
    if not ref_center:
        stats["why"] = "no_ref"
        return [], stats
    try:
        hx, hy, hw, hh = (float(v) for v in calib_rect[:4])
        rcx, rcy = float(ref_center[0]), float(ref_center[1])
    except (TypeError, ValueError, IndexError):
        stats["why"] = "bad_rect"
        return [], stats
    if hw <= 0 or hh <= 0:
        stats["why"] = "bad_rect"
        return [], stats

    rows = []      # [t, Δ_hoop_x − shift_orb_x, 同 y, w, h]
    for s in samples:
        try:
            t, x, y, w, h = (float(s[0]), float(s[1]), float(s[2]),
                             float(s[3]), float(s[4]))
        except (TypeError, ValueError, IndexError):
            continue
        if not (SIZE_ABS_LO <= w / hw <= SIZE_ABS_HI) \
                or not (SIZE_ABS_LO <= h / hh <= SIZE_ABS_HI):
            stats["n_size"] += 1
            continue
        sx, sy = interp_shift(shift_samples, t)
        rows.append([t, x - rcx - sx, y - rcy - sy, w, h])
    if len(rows) < MIN_SAMPLES:
        stats["why"] = "too_few"
        return [], stats

    # 段内尺寸一致性：手点框与模型框一贯差 1.1~1.3 倍是常态（不该整段丢弃），
    # 突变（变焦/框到别的东西）才是异常。
    mw = median([r[3] for r in rows])
    if mw > 0:
        keep = [r for r in rows if abs(r[3] - mw) / mw <= SIZE_JUMP]
        stats["n_size"] += len(rows) - len(keep)
        rows = keep
    if len(rows) < MIN_SAMPLES:
        stats["why"] = "size_out"
        return [], stats

    xs = _medfilt([r[1] for r in rows], med_win)
    ys = _medfilt([r[2] for r in rows], med_win)
    corr = [(round(rows[i][0], 3), round(xs[i], 2), round(ys[i], 2))
            for i in range(len(rows))]
    stats["n_used"] = len(corr)
    stats["max_res"] = round(max(max(abs(x) / hw, abs(y) / hh)
                                 for _, x, y in corr), 3)
    return corr, stats


def remap_positions(positions, corr):
    """检出点 − 校正项 → 新坐标。corr 空则**原样返回同一个对象**（零拷贝、
    零行为变化——旧档没有 hoop 字段时走这条）。"""
    if not corr or not positions:
        return positions
    from dataclasses import replace
    out = []
    for p in positions:
        cx, cy = interp_shift(corr, p.t)
        out.append(replace(p, x=p.x - cx, y=p.y - cy))
    return out


def remap_shift(shift, corr):
    """ORB 位移 + 校正项 = 「画面相对标定位置」的总位移（网区 ROI 用）。

    判定几何与网区必须用同一套坐标：positions 减了 c(t)，网区若不跟着减，
    网区就会停在旧位置——筐位偏得多时测网等于测错区域。"""
    if not corr or not shift:
        return shift
    out = []
    for s in shift:
        t = float(s[0])
        cx, cy = interp_shift(corr, t)
        out.append((t, s[1] + cx, s[2] + cy))
    return out
