"""数据模型与 JSON 读写。所有时间单位为秒。"""
import json
import os
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class Calibration:
    hoop_rect: tuple  # (x, y, w, h) 筐口矩形，标定视频分辨率下的像素坐标
    hsv_low: tuple    # 球颜色下界 (H, S, V)，OpenCV 范围
    hsv_high: tuple   # 球颜色上界
    frame_size: tuple  # 标定时的视频分辨率 (w, h)
    # 位移配准参考帧（用户点筐的那帧存成图片随标定保管，2026-08-20 起）：
    # 检测期间周期性与它配准，检出坐标折算回这帧的坐标系。None=旧标定
    # 未存参考帧 → 不做位移校正（附警告提示重新标定）。
    ref_image: Optional[str] = None  # 图片文件名（与标定文件同目录）
    ref_hash: Optional[str] = None   # 内容指纹 md5[:8]——进缓存指纹
                                     # (_calib_key)，换参考帧后旧结果自动失效
    # 标定时预览所在的视频时刻（秒）。0=片头标定（全片一段）；>0=在移动后
    # 的时刻标定——多段标定里该值即新段的 t0。只记不进指纹（几何相同、
    # 标定时刻不同的两套标定对检测是等价的）。
    calib_t: float = 0.0

    def save(self, path):
        tmp = str(path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path) -> "Calibration":
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return cls(hoop_rect=tuple(d["hoop_rect"]), hsv_low=tuple(d["hsv_low"]),
                   hsv_high=tuple(d["hsv_high"]), frame_size=tuple(d["frame_size"]),
                   ref_image=d.get("ref_image"), ref_hash=d.get("ref_hash"),
                   calib_t=float(d.get("calib_t", 0.0) or 0.0))


def calib_key(calib: Calibration) -> list:
    """标定指纹（可 JSON 序列化，tuple→list 才能比对）。
    含参考帧内容指纹：换参考帧（重新标定）后旧缓存自动失效。
    pipeline/appearance/shooter 共用（历史名 pipeline._calib_key 委托到此）。"""
    return [list(calib.hoop_rect), list(calib.hsv_low),
            list(calib.hsv_high), list(calib.frame_size), calib.ref_hash]


@dataclass
class CalibSegment:
    """多段标定的一段：从 t0 起的时段用这套标定几何（含参考帧）。

    动机（2026-09-11 09-04_1 取证）：视频中途镜头被手持挪位（含相机物理
    平移 → 视差，远近物体位移不同），全局平移配准原理上救不回旧参考帧
    几何；正解=在镜头稳定后的时刻人工补一段标定，各段各自配准/检测/判定，
    事件合并。单段 t0=0 即 v13 前的全部行为。"""
    t0: float                        # 段起点（秒，含）；扫描窗 [t0, 下一段 t0)
    calib: Calibration
    ref_image: Optional[str] = None  # 该段参考帧路径（扫该段时用；命中缓存不需要）

    @property
    def key(self) -> list:
        return calib_key(self.calib)


def segments_key(segs: list) -> list:
    """段列表的组合指纹（events calib_key / shooters / features 共用）。

    铁律：单段且 t0=0 时必须与 calib_key(seg.calib) 逐字节相同——所有存量
    缓存（四场 truth 的 positions/events、shooters.json、features.json）
    都持旧单段键，变了就全部身份不符触发全片重检。"""
    if len(segs) == 1 and not segs[0].t0:
        return calib_key(segs[0].calib)
    return [[round(float(s.t0), 3), calib_key(s.calib)] for s in segs]


def ref_image_path(calib_file: str, calib: Calibration) -> Optional[str]:
    """参考帧图片的路径（与标定文件同目录解析）；未存参考帧返回 None。"""
    if not calib.ref_image:
        return None
    if os.path.isabs(calib.ref_image):
        return calib.ref_image
    return os.path.join(os.path.dirname(os.path.abspath(calib_file)),
                        calib.ref_image)


@dataclass
class BallPos:
    t: float     # 帧时间（秒，容器 PTS）
    x: float     # 全图坐标
    y: float
    area: float  # 分割出的球像素面积
    fi: int = -1  # 帧号（检查点续跑定位用；-1=旧数据/未知，续跑回退 t×fps 估算）


@dataclass
class GoalEvent:
    t: float          # 进球时刻（下穿筐口平面的时间）
    confidence: float  # 0-1
    status: str       # "high" | "review" | "low"（低置信=否决门槛拦下但
                      # 仍进列表供人工查漏，默认不勾选、不进集锦）
    low_reason: Optional[str] = None  # 降级低置信的原因（None=非低置信档）
    # 判定依据（可解释输出）。旧版 events.json 无这些键 → 全部 None。
    cross_off: Optional[float] = None   # 穿越点距筐心偏移，单位=筐宽（正=右）
    above_n: Optional[int] = None       # 穿越前 1s 内上方证据点数
    below_n: Optional[int] = None       # 穿越后 1s 内下方证据点数
    above_span: Optional[float] = None  # 上方证据时间跨度（秒）
    below_span: Optional[float] = None  # 下方证据时间跨度（秒）
    descent_px: Optional[float] = None  # 下方证据最大相邻下落步长（像素）；
                                        # 零/负=原地或上弹（固定物/被抱住的球）
    below_big: Optional[int] = None  # 穿越点之后球体量级检出数（下落实体）；
                                     # 0=穿筐后未再现（该事件强制待复核）
    cross_area_rel: Optional[float] = None  # 穿越点检出框面积 ÷ 筐矩形面积
                                     # （v24）。真球 0.12~0.16，>0.50 = 手/臂/
                                     # 身体横穿（review_reason="area"）。
                                     # 旧版 events.json 无此键 → None
    pop_back: Optional[float] = None    # 下穿后球又回筐口上方（疑似弹出）；
                                        # 值=距下穿的秒数。仅记录，不影响判定
    review_reason: Optional[str] = None  # 复核硬门标记（v18）：None=非硬门；
                                        # "entity"=实体未再现 / "geom"=球心压筐沿
                                        # / "pop_back"=疑似弹出 / "fallback"=兜底
                                        # 注入 / "b2"=网簇注入 / "area"=穿筐物
                                        # 投影面积过大（v24）——net pass 重分箱
                                        # 不改这些行的档位（spec §3.3/3.5）。
                                        # 旧版 events.json 无此键 → None
    net_post: Optional[float] = None    # 篮网运动信号（事后窗网区最大帧差，
                                        # net_motion 后置 pass；None=未测/测量
                                        # 失败）。<5.0 → 网未摆动，high 已降
                                        # review（筐前/筐后飞过或未触网）
    net_ctrl: Optional[float] = None    # 对照区同法测量（诊断展示：真静止时
                                        # 与 net_post 同为噪声底）
    net_local: Optional[bool] = None    # 局部性（v14）：True=存在网区亮而
                                        # 同帧对照区安静的帧对（真网动）；
                                        # False=全窗等亮（全局噪声伪装），
                                        # high 已降 review；None=未测/旧档

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(t=d["t"], confidence=d["confidence"], status=d["status"],
                   low_reason=d.get("low_reason"),
                   cross_off=d.get("cross_off"), above_n=d.get("above_n"),
                   below_n=d.get("below_n"), above_span=d.get("above_span"),
                   below_span=d.get("below_span"),
                   descent_px=d.get("descent_px"), below_big=d.get("below_big"),
                   cross_area_rel=d.get("cross_area_rel"),
                   pop_back=d.get("pop_back"),
                   review_reason=d.get("review_reason"),
                   net_post=d.get("net_post"), net_ctrl=d.get("net_ctrl"),
                   net_local=d.get("net_local"))


@dataclass
class ClipSettings:
    before: float = 15.0
    after: float = 5.0


def save_events(path, video: str, duration: float, events: list[GoalEvent],
                warning: Optional[str], calib_key: Optional[list] = None,
                logic_ver: Optional[int] = None,
                motion: Optional[dict] = None):
    """calib_key：产出本结果所用标定的指纹（pipeline._calib_key）；
    logic_ver：检测逻辑版本（pipeline.LOGIC_VERSION）。均用于复用校验。
    motion：镜头位移摘要 {samples, max_dx, max_dy}（未启用校正时 None），
    仅供诊断展示，不参与复用校验。"""
    data = {"video": video, "duration": duration,
            "events": [e.to_dict() for e in events], "warning": warning,
            "calib_key": calib_key, "logic_ver": logic_ver, "motion": motion}
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_events(path) -> dict:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    evs = [GoalEvent.from_dict(e) for e in d["events"]]
    # 2026-10-11 用户裁定：表格按时间升序——B2 注入行（v16 起追加表尾）等
    # 存储乱序在读入层统一归位；写档顺序不动（重放对账按多重集），标签/
    # 勾选/归人 sidecar 全按 t 键寻址，与行序无关。稳定排序：同 t 保序。
    evs.sort(key=lambda e: e.t)
    return {"video": d["video"], "duration": d["duration"],
            "events": evs,
            "warning": d.get("warning"),
            "calib_key": d.get("calib_key"),   # 旧文件无此键 → None
            "logic_ver": d.get("logic_ver"),
            "motion": d.get("motion")}


def time_key(t: float) -> str:
    """秒 → m+:ss.s 行键（与 app._fmt_t 同格式——标签/勾选/出手人缓存共用
    口径）。放 models 供 app 之外的模块用，避免 shooter 反向依赖 app。"""
    return f"{int(t // 60):02d}:{t % 60:04.1f}"
