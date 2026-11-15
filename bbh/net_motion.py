"""篮网运动信号：进球判定的后置视频证据 pass（v12 起，v14 增局部性门）。

物理依据（2026-09 取证，truth 三场 08-29_3 / 08-28_1 / 08-22_1 全部
high 事件逐球测网区帧差 + 边界例 9 次拼图目检）：球穿网 → 网被撑开并
持续摆动 ~1s（事后窗最大帧差中位 12~14）；球从筐前/筐后飞过、或偏出
未触网 → 网静止（帧差 ≈ 压缩噪声底 2~4，同画面对照区同底）。

规则是**单向证据**：网静止 ⇒ 大概率没进（08-29_3 假绿的 74% 形态）→
high 降 review；网动**不**升级——打铁弹出蹭网/砸筐沿传振/贴网外侧扫落
同样让网强烈摆动（08-28/08-22 假绿主形态，103:57.8 等 5 例目检定性，
真进球的"纵向撑开"与擦网的"侧向甩摆"在帧差能量上不可分）。

v14 局部性门（2026-09-14 五场取证）：新两场出现「网区帧差 20~30 但
差分热力图整框无结构椒盐、对照区等亮、肉眼网完全静止」的假绿——镜头
亚阈值抖动/编码噪声让全画面帧差抬升，网区被动挨亮伪装成"网动"。
对照区若按各自最大值比（post/ctrl_max）在真进球上被别的时刻污染
（球员穿对照区），五场分布无分离度；改为**同一对帧上比**：真网动的那
一帧对里网区亮、对照区应安静。规则：post≥TH 且不存在任何一对
n ≥ k×c（NET_LOCALITY_K=3.0）→ 判「全局噪声伪装」降 review。
五场重放消融（FP=160 / TP=583，post≥5 行）：拦假绿 64/160（40%）、
真绿转复核 17/583（3%，⚪ 人工照旧勾回，非丢失）；k∈{2,2.5,4} 均被
帕累托支配。误杀例=进球恰逢镜头大幅移动（真网动帧对里对照区也被
抬亮），物理上确不可分，兜底人工。

阈值与消融（三场合计 FP=147 / TP=381）：NET_STATIC_TH=5.0 救回假绿
58/147（39%）、误杀真绿 8/381（2.1%，主要为轻擦入网网摆弱的弱信号例，
降级可逆、人工照旧勾回；4.5/5.5 两档均被帕累托支配）。测量失败
（视频打不开/seek 出界）fail-open 不降级——证据缺失不能当否证。

耗时 ~0.6s/事件（15 帧 seek+差分）：全片检测后一次性对 high 候选测量，
结果存 work 目录 sidecar（net_signals.json，身份=视频路径+筐标定+参数
版本）——判定器重放秒级路径命中缓存不重测。

v18（2026-10-11 问题②）：网证据从「直接改 status」改为「conf 加减分项」
（net_score），测量对象扩到全部事件行，非硬门行按 HIGH_CONF/MIN_CONF 对
含网分的 conf 重分箱——「只测 high」的两条降级语句自此删除。硬门行测网
只记录不改档。详见 docs/superpowers/specs/2026-10-11-net-confidence-design.md。
"""
import json
import os

import cv2
import numpy as np

from .goal_decider import HIGH_CONF, MIN_CONF, RIM_Y_RATIO
from .models import Calibration, GoalEvent
from .motion import interp_shift

NET_STATIC_TH = 5.0        # 网区事后窗最大帧差低于此 → 判「网未摆动」降级
NET_POST_OFFS = (0.45, 0.70, 0.95)  # 事后采样窗（球 +0.30s 在网内、+0.45s
                                    # 出网；三窗取最大兜住不同网的摆动相位）
NET_FRAMES = 5             # 每窗连续读帧数（4 个帧对）
NET_DS = (320, 180)        # 相位相关用降采样尺寸（与全帧差分无关）
NET_PHASE_TOL = 0.3        # 降采样域位移超过此值 → warp 对齐后再差分
NET_MARGIN_X = 0.11        # 网区横向内缩（相对筐宽，避筐沿本身）
NET_DEPTH = 1.6            # 网区纵向深度（相对筐高，含网全长）
NET_LOCALITY_K = 3.0       # 同对局部性倍率：网区亮必须比**同一对帧**的对照
                            # 区亮 k 倍才算网动；两头等亮=全局抖动/编码噪声
# v18 网分（spec §3.1）：网信号对 conf 的加减分项。取值对齐「首轮重放
# 档位迁移 ≈ 现行网门行为」：静止 −0.20 使几何 0.85 → 0.65 恰守 high 线
# （HIGH_CONF 2026-10-11 八场定标 0.70→0.65）。
# 网动且局部 = 0（铁结论：打铁弹出蹭网/砸筐沿传振/贴网外侧扫落同样让网
# 强烈摆动——「网动不升级」是五轮取证换来的，不得顺手对称化）；测量缺失
# = 0（fail-open）。八场定标后可再调（spec §3.6）。
NET_SCORE_STATIC = -0.30   # 网静止（post < NET_STATIC_TH）；v19: -0.20→-0.30
NET_SCORE_GLOBAL = -0.26   # 网亮但非局部（全局抖动/编码噪声伪装）；v19: -0.10→-0.26
NET_SCORE_SWING = 0.0      # 网动且局部
NET_SIGNAL_VER = 2         # 参数版本（改窗口/阈值/ROI 时 +1，sidecar 作废）
NET_FILE = "net_signals.json"
TRANSCODE_FILE = "det-source.mp4"   # 同 pipeline.TRANSCODE_FILE（避免循环
                                    # import；源视频 OpenCV 解不开时的转码副本）


def _clamp(v0: float, v1: float, lim: int) -> tuple[int, int]:
    """ROI 越界平移回画面（同 detector 语义：贴边平移而非裁剪）。"""
    if v0 < 0:
        v1 -= v0
        v0 = 0
    if v1 > lim:
        v0 -= v1 - lim
        v1 = lim
    return int(v0), int(v1)


def _rois(calib: Calibration, dx: float, dy: float):
    """(dx,dy) 位移下的（网区, 对照区）像素框。对照区=筐左侧一筐宽、
    与网区同高——真进球时它必须静止（同画面的镜头噪声/编码噪声底）。"""
    hx, hy, hw, hh = calib.hoop_rect
    fw, fh = calib.frame_size
    rim_y = int(hy + RIM_Y_RATIO * hh)
    ex = max(3, int(NET_MARGIN_X * hw))
    nx0, nx1 = _clamp(hx + ex + dx, hx + hw - ex + dx, fw)
    ny0, ny1 = _clamp(rim_y + 1 + dy, rim_y + 1 + int(NET_DEPTH * hh) + dy, fh)
    cx0, cx1 = _clamp(hx - hw - 6 + dx, hx - 6 + dx, fw)
    return (nx0, ny0, nx1, ny1), (cx0, ny0, cx1, ny1)


def _safe_mean(diff: np.ndarray, y0: int, y1: int, x0: int, x1: int):
    return float(diff[y0:y1, x0:x1].mean()) if (y1 > y0 and x1 > x0) else None


def _window_scores(cap, t0: float, calib: Calibration, shift_samples: list):
    """t0 起 NET_FRAMES 连续帧 → (网区最大帧差, 对照区最大帧差, 局部性)。
    局部性=存在某一对帧 n≥NET_STATIC_TH 且 n≥NET_LOCALITY_K×c（网区亮
    而同帧对照区安静=真网动；两头等亮=全局抖动/编码噪声）。
    None=读帧失败（seek 出界/视频结束）。帧间镜头移动先用相位相关对齐
    （降采样域测移、原分辨率 warp），残差运动由对照区兜底可见。"""
    cap.set(cv2.CAP_PROP_POS_MSEC, t0 * 1000)
    frames = []
    for _ in range(NET_FRAMES):
        ok, fr = cap.read()
        if not ok:
            return None
        frames.append(fr)
    fw, fh = calib.frame_size
    nets, ctrls, local = [], [], False
    for a, b in zip(frames, frames[1:]):
        ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(np.float32)
        gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY).astype(np.float32)
        da = cv2.resize(ga, NET_DS)
        db = cv2.resize(gb, NET_DS)
        (sx, sy), _ = cv2.phaseCorrelate(da, db)
        if abs(sx) > NET_PHASE_TOL or abs(sy) > NET_PHASE_TOL:
            m = np.float32([[1, 0, sx * fw / NET_DS[0]],
                            [0, 1, sy * fh / NET_DS[1]]])
            ga = cv2.warpAffine(ga, m, (fw, fh))
        # 帧时刻取刚读完那帧的 PTS，查 shift 修 ROI（大漂移场景逐帧跟随）
        msec = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        dx, dy = interp_shift(shift_samples, msec if msec > 0 else t0)
        (nx0, ny0, nx1, ny1), (cx0, cy0, cx1, cy1) = _rois(calib, dx, dy)
        d = np.abs(gb - ga)
        n = _safe_mean(d, ny0, ny1, nx0, nx1)
        c = _safe_mean(d, cy0, cy1, cx0, cx1)
        if n is not None:
            nets.append(n)
        if c is not None:
            ctrls.append(c)
        if n is not None and c is not None and n >= NET_STATIC_TH \
                and n >= NET_LOCALITY_K * c:
            local = True
    if not nets:
        return None
    return max(nets), (max(ctrls) if ctrls else None), local


def _open_cap(video_path: str, work_dir: str | None):
    """源视频优先；OpenCV 解不开（罕见编码）时退转码副本（时间轴与检测
    共享同一假设）。打不开返回 None → 整个 pass fail-open。"""
    cap = cv2.VideoCapture(video_path)
    if cap.isOpened():
        return cap
    cap.release()
    if work_dir:
        alt = os.path.join(work_dir, TRANSCODE_FILE)
        if os.path.exists(alt):
            cap = cv2.VideoCapture(alt)
            if cap.isOpened():
                return cap
    return None


def measure_net_signals(video_path: str, calib: Calibration,
                        shift_samples: list, times: list,
                        work_dir: str | None = None) -> dict:
    """对 times（_k 款字符串键）逐一测量三窗网信号 → {键: {post, ctrl,
    local}}。local=任一窗存在局部网动帧对（同对 n≥TH 且 n≥K×c）。
    读不到视频/某时刻全部窗口读帧失败 → 该键缺省（调用方 fail-open）。"""
    cap = _open_cap(video_path, work_dir)
    if cap is None:
        return {}
    out = {}
    try:
        for k in times:                       # 字符串键（_k 款，sidecar 直存）
            t = float(k)
            nets, ctrls, local = [], [], False
            for off in NET_POST_OFFS:
                g = _window_scores(cap, t + off, calib, shift_samples)
                if g:
                    nets.append(g[0])
                    if g[1] is not None:
                        ctrls.append(g[1])
                    local = local or g[2]
            if nets:
                out[k] = {"post": round(max(nets), 2),
                          "ctrl": round(max(ctrls), 2) if ctrls else None,
                          "local": local}
    finally:
        cap.release()
    return out


def _k(t: float) -> str:
    """信号表键（字符串，JSON 往返稳定）：round(t,2) 的定点格式。"""
    return f"{round(t, 2):.2f}"


def _cache_identity(video_path: str, calib: Calibration) -> dict:
    """sidecar 身份：换视频/重标筐（hoop_rect 变）/参数版本升 → 作废重测。
    HSV 不参与——网测量只依赖筐几何，换 HSV 阈值重标不影响既有信号。"""
    return {"video": video_path, "hoop": list(calib.hoop_rect),
            "frame": list(calib.frame_size), "ver": NET_SIGNAL_VER}


def _load_cache(work_dir: str, video_path: str, calib: Calibration) -> dict:
    """读 sidecar 里与本标定同身份的信号表。2026-09-12 起文件是多 entry
    格式 {"entries":[{identity,signals}…]}（A2 多段标定：一段一筐几何一
    身份，互不清空）；旧单 entry 格式（顶层 identity/signals）兼容读。"""
    p = os.path.join(work_dir, NET_FILE)
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return {}
    if not isinstance(d, dict):
        return {}
    ident = _cache_identity(video_path, calib)
    if isinstance(d.get("entries"), list):          # 新格式
        for ent in d["entries"]:
            if isinstance(ent, dict) and ent.get("identity") == ident:
                sig = ent.get("signals")
                return sig if isinstance(sig, dict) else {}
        return {}
    if d.get("identity") == ident:                  # 旧单 entry 格式
        sig = d.get("signals")
        return sig if isinstance(sig, dict) else {}
    return {}


def _save_cache(work_dir: str, video_path: str, calib: Calibration,
                signals: dict) -> None:
    """写回时只更新本身份的 entry，别段身份的信号原样保留（段 A/B 各自的
    测网结果在同一 sidecar 共存——判定器重放秒级路径两段都吃缓存）。"""
    p = os.path.join(work_dir, NET_FILE)
    ident = _cache_identity(video_path, calib)
    entries = []
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("entries"), list):
            entries = [e for e in d["entries"] if isinstance(e, dict)]
        elif isinstance(d, dict) and isinstance(d.get("identity"), dict):
            entries = [{"identity": d["identity"],
                        "signals": d.get("signals")}]   # 旧格式迁移
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        entries = []
    entries = [e for e in entries if e.get("identity") != ident]  # 去旧留新
    entries.append({"identity": ident, "signals": signals})
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"entries": entries}, f, ensure_ascii=False)
    os.replace(tmp, p)


def net_score(post, local) -> float:
    """(net_post, net_local) → 网分。None（未测/测量失败）= 0：证据缺失
    不当否证（fail-open，与 v12 起的降级口径同源）。"""
    if post is None:
        return 0.0
    if post < NET_STATIC_TH:
        return NET_SCORE_STATIC
    if local is False:
        return NET_SCORE_GLOBAL
    return NET_SCORE_SWING


def net_term(e) -> float:
    """依据卡算式的网分项：B2 注入行恒 0（spec §3.5——conf 未含网分：注入
    时刻是链内插值，实测窗可能错过摆动相位，测出「静止」不能反推扣分，
    实测 net_* 照旧展示）；其余行 = net_score。UI/旧工作台共用本函数，
    保证「公式和 = 显示 conf」在注入行上也成立。"""
    if getattr(e, "review_reason", None) == "b2":
        return 0.0
    return net_score(e.net_post, e.net_local)


def attach_net_display(video_path: str, work_dir: str, calib: Calibration,
                       shift_samples: list, rows: list[GoalEvent],
                       stage_cb=None) -> None:
    """给 B2 注入行补 net_* 实测值（仅显示，不改 conf/status——spec §3.5：
    注入行网分按 0，簇触发判据本身就是同款局部性网动，实测窗可能错过摆动
    相位，测出「静止」再扣分是对同一证据的双重惩罚）。缓存/测量/sidecar
    与 net_evidence_pass 同一套（身份相同，重放共享）。fail-open：测不到
    → 字段留 None。"""
    if not rows:
        return
    cache = _load_cache(work_dir, video_path, calib)
    missing = sorted({_k(e.t) for e in rows} - set(cache))
    if missing:
        if stage_cb:
            stage_cb(f"正在测量篮网运动信号（{len(missing)} 个事件行，"
                     f"约 {max(1, round(len(missing) * 0.7))} 秒）…")
        measured = measure_net_signals(video_path, calib, shift_samples,
                                       missing, work_dir)
        if measured:
            cache.update(measured)
            _save_cache(work_dir, video_path, calib, cache)
        if stage_cb:
            stage_cb(None)
    for e in rows:
        sig = cache.get(_k(e.t))
        if not sig:
            continue
        e.net_post = sig["post"]
        e.net_ctrl = sig.get("ctrl")
        e.net_local = sig.get("local")


def net_evidence_pass(video_path: str, work_dir: str, calib: Calibration,
                      shift_samples: list, events: list[GoalEvent],
                      stage_cb=None) -> list[GoalEvent]:
    """v18：判定器输出 → 全行测网 + 网分回写 conf + 非硬门行重分箱（原地
    改，返回同一列表）。

    网证据不再直接改档（v12/v14 的两条降级语句删除）：conf += net_score
    （静止 −0.20 / 非局部 −0.10 / 网动或未测 0），round 2 位再比较；档位
    = 硬门档 if 硬门 else 阈值分箱(conf)（HIGH_CONF/MIN_CONF）。测量对象
    从 high 扩到**全部行**（用户裁定「所有球都需要走篮网的判断，不然置信
    度分值就会不准」）；硬门行（low_reason / review_reason / pop_back）
    测网只记录、conf 照扣（分值完整性），档位不动。fail-open：测不到 →
    net_* 留 None、网分 0、档位不动。

    幂等前提：输入行 conf 为几何分（decide_goals 刚产出），本 pass 在每行
    上恰好执行一次（管线 CR 二趟只对 fresh 行再跑）。全部命中 sidecar 缓存
    时零视频 IO（判定器秒级重放路径保持秒级）；NET_SIGNAL_VER 不动，旧
    sidecar 里 high 行的测量原样吃回，增量只测未覆盖行。"""
    if not events:
        return events
    cache = _load_cache(work_dir, video_path, calib)
    missing = sorted({_k(e.t) for e in events} - set(cache))
    if missing:
        if stage_cb:
            stage_cb(f"正在测量篮网运动信号（{len(missing)} 个事件行，"
                     f"约 {max(1, round(len(missing) * 0.7))} 秒）…")
        measured = measure_net_signals(video_path, calib, shift_samples,
                                       missing, work_dir)
        if measured:
            cache.update(measured)
            _save_cache(work_dir, video_path, calib, cache)
        if stage_cb:
            stage_cb(None)
    for e in events:
        sig = cache.get(_k(e.t))
        if not sig:
            continue          # 测量失败 → fail-open：证据缺失不当否证
        e.net_post = sig["post"]
        e.net_ctrl = sig.get("ctrl")
        e.net_local = sig.get("local")
        e.confidence = round(e.confidence
                             + net_score(sig["post"], sig.get("local")), 2)
        if e.low_reason is not None:
            continue                      # low 硬门：物理否证，网信号救不回
        if e.review_reason is not None or e.pop_back is not None:
            continue                      # 复核硬门：不被网分拉高或压低
        e.status = ("high" if e.confidence >= HIGH_CONF
                    else "review" if e.confidence >= MIN_CONF else "low")
    return events
