"""B2 网簇触发兜底 review 行注入器（LOGIC_VERSION 16，2026-09-25 立项）。

背景（B 轮 09-12_1/09-19_1 取证）：存在一小类真进球在既有判定管线里
**连一行都不产生**（静默漏检：涮框悬停 gap 差 50ms 断链 / 上段被撕成
小于实体阈值的碎片 + 上方点 off 出横向容差差 11px），不可勾不可裁，
truth 也不含——用户看不到就永远无法复核。唯一独立信号=篮网运动：全片
扫「网动簇」（相邻取样帧对的网区帧差 + 同对局部性门，判据同
net_motion），簇对不上任何既有事件行（±B2_MATCH_WIN）时，用簇尾锚窗内
的**下行残迹链**补一行待复核。

残迹链（B 轮验证版 b2_candidates.py 原样移植，八场干跑 210 候选）：
≥3 点严格下行（每点 y 大于前点）、全点横向界内（hx-0.2hw ~ hx+1.2hw）、
全点带下（y > rim+0.25hh）、链自身跨度 ≤1.2s、**面积不限**——上段被
撕碎的球也算实体证据（正是静默漏检形态：碎片链无实体量级点，主判定器
看不见）。链门 N=3 为用户裁定（「保持3」，宁多勿漏）。注入行
status="review"、above_n=0（UI 依据文案「上方证据 0 点」即注入行指纹）、
置信度按主判定器同款公式由链证据计算（2026-10-11 问题①修复，曾硬编码
0.30——above 侧恒 0，档位仍恒 review 不随算式升 high）、
review_reason="b2"（v18 硬门标记）、net_* 落实测值仅供展示（网分按 0）。

时刻纪律（2026-09-23）：注入行 t=positions 内插穿筐点（链首前 2s 内
最近上方平面点与链首连线内插到 rim_y；无上方点退链首时刻），4 颗确证
真球全部复算命中（727.0/731.7/1181.2/2080.3，与网簇起点互证）。

旧行永不改写：注入行只追加在表尾（注入行之间按 t 排序），0 丢行 by
construction。已删球（deleted.json 键，±B2_MATCH_WIN）不注入——删除=
策展出局（比赛间隙随意投篮），不是漏检，不能借 B2 还魂。fail-open：
视频打不开/无簇/残迹不成立 → 原样返回。

簇扫描代价与片长成正比（实测数分钟到 ~10 分钟/场）一次性，结果存
sidecar net_clusters.json（身份=
视频+分段筐几何+NET_CLUSTER_VER，**不含 LOGIC_VERSION**——簇是纯视频
测量，判定器升版重放不重扫，同 net_signals 设计；多 entry 共存供多段/
重标定各留各的）。
"""
import json
import os

import cv2
import numpy as np

from .goal_decider import (ENTITY_AREA_RATIO, RIM_Y_RATIO, _pos_score,
                           _side_score)
from .models import Calibration, GoalEvent
from .motion import interp_shift
from .net_motion import (NET_DS, NET_LOCALITY_K, NET_PHASE_TOL, NET_STATIC_TH,
                         _open_cap, _rois, _safe_mean, attach_net_display)

NET_CLUSTER_STEP = 6       # 全片取样步长：每 6 帧取 1 帧做相邻帧对（≈5fps）
NET_CLUSTER_GAP = 1.2      # 网动命中对间隔超过此值 → 断簇（B 轮验证值）
NET_CLUSTER_VER = 1        # 扫描参数版本（步长/聚簇/判据变更时 +1 作废）
NET_CLUSTERS_FILE = "net_clusters.json"
DELETED_FILE = "deleted.json"   # 已删行键清单（与 app.py 同名文件 m+:ss.s）

B2_MATCH_WIN = 2.5         # 簇与既有事件行/已删键的对账窗（秒，B 轮口径）
B2_W_BACK, B2_W_FWD = 1.2, 0.3   # 残迹链搜索窗（相对簇尾锚）
B2_SPAN_MAX = 1.2          # 链自身时间跨度上限
B2_MIN_N = 3               # 链最少点数（用户裁定「保持3」，宁多勿漏）
B2_X_MARGIN = 0.2          # 横向宽容（相对筐宽；同 X_MARGIN 值，判据域独立）
B2_BAND = 0.25             # 筐身条带半高（相对筐高；同 BAND_RATIO 值）
B2_ANCHOR_BACK = 2.0       # 锚内插窗：链首前 2s 内最近的上方平面点
B2_INJECT_DEDUP = 3.0      # 注入行彼此去重窗（同 COOLDOWN_SEC 语义）


def scan_net_clusters(video_path: str, work_dir: str | None,
                      segs: list, shifts: dict) -> list[dict]:
    """全片顺序读帧、每 NET_CLUSTER_STEP 帧取 1 帧，相邻取样帧对测网区/
    对照区帧差（局部性门同 net_motion：n≥NET_STATIC_TH 且 n≥K×c；相位
    相关对齐 + 位移补偿），聚成网动簇 [{t0, t1, peak, npairs}…]。
    打不开视频 → []（fail-open）。segs=[(t0, Calibration)] 按簇时刻就近
    分辨标定段（多段挪机位各用各的筐几何）。"""
    cap = _open_cap(video_path, work_dir)
    if cap is None:
        return []
    seg_t0s = [t0 for t0, _ in segs]
    hits: list = []          # (t, 网区帧差) 局部网动帧对
    prev = None             # 上一取样帧灰度
    fi = -1
    try:
        while True:
            if not cap.grab():
                break
            fi += 1
            if fi % NET_CLUSTER_STEP:
                continue
            ok, fr = cap.retrieve()
            if not ok:
                break
            msec = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if msec <= 0:
                continue
            t0 = max((s for s in seg_t0s if s <= msec + 1e-9),
                     default=seg_t0s[0])
            calib = next(c for s, c in segs if s == t0)
            g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY).astype(np.float32)
            if prev is not None:
                pa = prev
                da = cv2.resize(pa, NET_DS)
                db = cv2.resize(g, NET_DS)
                (sx, sy), _ = cv2.phaseCorrelate(da, db)
                if abs(sx) > NET_PHASE_TOL or abs(sy) > NET_PHASE_TOL:
                    fw, fh = calib.frame_size
                    m = np.float32([[1, 0, sx * fw / NET_DS[0]],
                                    [0, 1, sy * fh / NET_DS[1]]])
                    pa = cv2.warpAffine(pa, m, (fw, fh))
                dx, dy = interp_shift(shifts.get(t0, []), msec)
                (nx0, ny0, nx1, ny1), (cx0, cy0, cx1, cy1) = _rois(calib,
                                                                   dx, dy)
                d = np.abs(g - pa)
                n = _safe_mean(d, ny0, ny1, nx0, nx1)
                c = _safe_mean(d, cy0, cy1, cx0, cx1)
                if n is not None and c is not None \
                        and n >= NET_STATIC_TH and n >= NET_LOCALITY_K * c:
                    hits.append((msec, n))
            prev = g
    finally:
        cap.release()
    return _cluster_hits(hits)


def _cluster_hits(hits: list) -> list[dict]:
    """聚簇。时刻/峰值保持原始浮点——曾试 round(t,3) 再比 1.2s 断簇间隙，
    在 09-19 上把 4 对刀锋间隙（±0.5ms）翻向合并侧、与验证版 b_scan 分叉
    （总命中对数 1504=1504 全等，纯聚簇差异）。"""
    clusters = []
    for t, n in hits:
        if clusters and t - clusters[-1]["t1"] <= NET_CLUSTER_GAP:
            clusters[-1]["t1"] = t
            clusters[-1]["peak"] = max(clusters[-1]["peak"], n)
            clusters[-1]["npairs"] += 1
        else:
            clusters.append({"t0": t, "t1": t, "peak": n, "npairs": 1})
    return clusters


def _identity(video_path: str, segs: list) -> dict:
    """sidecar 身份：换视频 / 任一段筐几何变 / 参数版本升 → 作废重扫。"""
    return {"video": video_path,
            "segs": [[round(t0, 3), list(c.hoop_rect), list(c.frame_size)]
                     for t0, c in segs],
            "ver": NET_CLUSTER_VER}


def load_clusters(work_dir: str, video_path: str, segs: list):
    """读 sidecar 里同身份的簇表。None=无匹配 entry（需要扫）；[]=匹配但
    空表（已扫过确实无簇，别再扫）——两者语义不同。"""
    p = os.path.join(work_dir, NET_CLUSTERS_FILE)
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    if not isinstance(d, dict):
        return None
    ident = _identity(video_path, segs)
    for ent in d.get("entries", []):
        if isinstance(ent, dict) and ent.get("identity") == ident:
            cl = ent.get("clusters")
            return cl if isinstance(cl, list) else []
    return None


def save_clusters(work_dir: str, video_path: str, segs: list,
                  clusters: list) -> None:
    """写回时只更新本身份的 entry，别身份的簇表原样保留（同 net_signals
    多 entry 设计：多段/重标定各自的扫描结果共存）。"""
    p = os.path.join(work_dir, NET_CLUSTERS_FILE)
    ident = _identity(video_path, segs)
    entries = []
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("entries"), list):
            entries = [e for e in d["entries"] if isinstance(e, dict)]
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        entries = []
    entries = [e for e in entries if e.get("identity") != ident]  # 去旧留新
    entries.append({"identity": ident, "clusters": clusters})
    os.makedirs(work_dir, exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"entries": entries}, f, ensure_ascii=False)
    os.replace(tmp, p)


def residue_chain(allpts: list, calib: Calibration, t_anchor: float):
    """簇尾锚窗内的下行残迹链（B 轮验证版过滤器原样移植）。

    窗 [t_anchor-B2_W_BACK, t_anchor+B2_W_FWD] 内、横向界内、带下的点里
    找最长严格下行链；≥B2_MIN_N 点且跨度 ≤B2_SPAN_MAX → 返回链点列表，
    否则 None。allpts 须按 t 升序（调用方排序）。斜向掠过的球链后半出
    横向界（B 轮 5904.6 实测被此条拦死）——这是与「穿网下坠」的分离点。"""
    hx, hy, hw, hh = calib.hoop_rect
    rim = hy + RIM_Y_RATIO * hh
    x_lo, x_hi = hx - hw * B2_X_MARGIN, hx + hw * (1.0 + B2_X_MARGIN)
    y_lo = rim + hh * B2_BAND
    w = [p for p in allpts
         if t_anchor - B2_W_BACK <= p.t <= t_anchor + B2_W_FWD
         and x_lo <= p.x <= x_hi and p.y > y_lo]
    best, run = [], []
    for p in w:                      # allpts 有序 → w 按 t 升序
        if run and p.y > run[-1].y:
            run.append(p)
        else:
            run = [p]
        if len(run) > len(best):
            best = run
    if len(best) < B2_MIN_N or best[-1].t - best[0].t > B2_SPAN_MAX:
        return None
    return best


def inject_anchor_t(chain: list, calib: Calibration, allpts: list) -> float:
    """注入行时刻：positions 内插穿筐点（2026-09-23 纪律，不取簇时刻）。

    链首前 B2_ANCHOR_BACK 秒内、横向界内、筐口平面之上（y<rim_y）的最近
    一点与链首连线，内插到 rim_y 平面；无上方点（出手段被遮挡的形态）
    退链首时刻。退化（同 y）退链首。"""
    hx, hy, hw, hh = calib.hoop_rect
    rim = hy + RIM_Y_RATIO * hh
    head = chain[0]
    x_lo, x_hi = hx - hw * B2_X_MARGIN, hx + hw * (1.0 + B2_X_MARGIN)
    above = [p for p in allpts
             if head.t - B2_ANCHOR_BACK <= p.t <= head.t
             and x_lo <= p.x <= x_hi and p.y < rim]
    if not above:
        return head.t
    a = max(above, key=lambda p: p.t)     # 上方弧末点（离链首最近）
    if head.y == a.y:
        return head.t
    frac = (rim - a.y) / (head.y - a.y)
    frac = min(max(frac, 0.0), 1.0)
    return a.t + frac * (head.t - a.t)


def _deleted_seconds(work_dir: str) -> list:
    """已删行键清单 → 秒（evaluate.py _load_deleted_seconds 同款解析；
    删除=策展出局非漏检，簇落在删键邻域不注入）。"""
    p = os.path.join(work_dir, DELETED_FILE)
    try:
        with open(p, encoding="utf-8") as f:
            keys = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for k in (keys if isinstance(keys, list) else []):
        try:
            m, s = str(k).split(":")
            out.append(int(m) * 60 + float(s))
        except ValueError:
            continue
    return out


def _build_event(t_new: float, chain: list, calib: Calibration) -> GoalEvent:
    hx, hy, hw, hh = calib.hoop_rect
    head = chain[0]
    min_entity = ENTITY_AREA_RATIO * hw * hh
    big = sum(1 for p in chain if p.area >= min_entity)
    descent = max((b.y - a.y for a, b in zip(chain, chain[1:])), default=0.0)
    cross_off = round((head.x - (hx + hw / 2.0)) / hw, 3)
    below_span = round(chain[-1].t - chain[0].t, 3)
    # 置信度走主判定器同款公式（2026-10-11 问题①：曾硬编码 0.30，八场
    # 47 行注入行全部显示 0.30 与链证据无关——用户报 10:43.8 应为 0.60）。
    # above 侧恒 0（注入行本就无上方弧）；status 仍恒 review——即使算式
    # 达到 0.70 也不升 high（B2 是兜底候选，宁多勿漏裁定，档位不跟算式走）。
    conf = round(0.3 + _side_score(len(chain), below_span)
                 + _pos_score(abs(cross_off)), 2)
    return GoalEvent(
        t=t_new, confidence=conf, status="review",
        review_reason="b2",   # v18：注入行自带硬门——网分重分箱不适用（结构上
                              # 也不经过 net_evidence_pass），恒 review（宁多勿漏）
        cross_off=cross_off,
        above_n=0, below_n=len(chain),
        above_span=0.0, below_span=below_span,
        descent_px=round(descent, 1), below_big=big)


def net_cluster_pass(video_path: str, work_dir: str, segs: list, shifts: dict,
                     allpts: list, events: list, stage_cb=None) -> list:
    """合并后事件 → 网簇兜底注入（只追加表尾，不改写旧行）。

    每簇：±B2_MATCH_WIN 内无既有事件行且无已删键 → 簇尾锚窗残迹链成立
    时注入一行 review（above_n=0 为注入行指纹）；注入行彼此
    B2_INJECT_DEDUP 去重。sidecar 未命中先全片扫描（一次性，数分钟到
    ~10 分钟与片长成正比，stage_cb 告知界面）。fail-open：无簇/打不开/
    残迹不成立 → 原样返回。"""
    if not segs:
        return events
    cached = load_clusters(work_dir, video_path, segs)
    if cached is None:
        if stage_cb:
            stage_cb("正在全片扫描篮网运动窗口（首次需数分钟、与片长成正比，仅此一次）…")
        clusters = scan_net_clusters(video_path, work_dir, segs, shifts)
        if stage_cb:
            stage_cb(None)
        save_clusters(work_dir, video_path, segs, clusters)
    else:
        clusters = cached
    if not clusters:
        return events
    calibs = dict(segs)
    seg_t0s = [t0 for t0, _ in segs]
    pts = sorted(allpts, key=lambda p: p.t)
    skip_ts = [e.t for e in events] + _deleted_seconds(work_dir)
    injected = []
    for cl in clusters:
        a, b = cl["t0"], cl["t1"]
        if any(a - B2_MATCH_WIN <= t <= b + B2_MATCH_WIN for t in skip_ts):
            continue
        t0 = max((s for s in seg_t0s if s <= b + 1e-9), default=seg_t0s[0])
        calib = calibs[t0]
        chain = residue_chain(pts, calib, b)
        if not chain:
            continue
        t_new = round(inject_anchor_t(chain, calib, pts), 3)
        if any(abs(t_new - o.t) < B2_INJECT_DEDUP for o in injected):
            continue
        injected.append(_build_event(t_new, chain, calib))
    if not injected:
        return events
    injected = sorted(injected, key=lambda e: e.t)
    # v18：注入行补 net_* 实测值（仅 UI 展示；网分按 0、档位恒 review）。
    # 多段标定按行时刻就近归段，各用各的筐几何与 shift（同簇扫描口径）。
    by_seg: dict = {}
    for e in injected:
        t0 = max((s for s in seg_t0s if s <= e.t + 1e-9), default=seg_t0s[0])
        by_seg.setdefault(t0, []).append(e)
    for t0, grp in by_seg.items():
        attach_net_display(video_path, work_dir, calibs[t0],
                           shifts.get(t0, []), grp, stage_cb=stage_cb)
    return events + injected
