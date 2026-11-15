# bbh/appearance.py
"""出手人识别 Phase 2：外观特征提取 + 聚类分组（2026-08-29 spec）。

依赖 Phase 1 的 shooters.json（只读——release_t/release_xy 是特征定位
锚，status 三档决定建组资格：high 建组、review 只挂不建）。两级缓存：
features.json（身份=视频+标定指纹+FEAT_VERSION，特征算法变才重提，
每球 ~1s 要回视频读帧）与 groups.json（身份=features+CLUSTER_VERSION，
调聚类参数秒级重聚）。绝不碰 DETECT/LOGIC/SHOOTER 版本。
零模型依赖：中值背景差分 + HSV 直方图（OpenCV/numpy only）。"""
import json
import os
from dataclasses import dataclass, field

import cv2
import numpy as np

from .detector import _scaled_rect
from .shooter import _detect_source, _identity, _seg_for_t, _segs_of, _t_of_key

FEAT_VERSION = 1           # 特征算法（分段/皮肤剔除/直方图口径）变化时 +1
CLUSTER_VERSION = 1        # 聚类规则/阈值变化时 +1
FEATURES_FILE = "features.json"
GROUPS_FILE = "groups.json"


@dataclass
class Appearance:
    """一球射手的外观指纹。缺失段不参与比对（missing 记名，整体置信降档）。"""
    ok: bool
    hists: dict | None = None       # {"top"/"pants"/"shoes": 各 1024 归一化拉平}
    body_ratio: float | None = None  # 人形掩码高 / 筐宽（同场同机位粗比）
    aspect: float | None = None      # 掩码高/宽
    head_hist: list | None = None    # 顶部 ~15% 发色 H 直方图（32 bin，仲裁票）
    jersey_no: str | None = None     # 号码票接口预留（spec：本期不实现 OCR）
    missing: list = field(default_factory=list)
    err: str | None = None

    def to_dict(self):
        return {"ok": self.ok, "hists": self.hists, "body_ratio": self.body_ratio,
                "aspect": self.aspect, "head_hist": self.head_hist,
                "jersey_no": self.jersey_no,
                "missing": self.missing, "err": self.err}

    @classmethod
    def from_dict(cls, d):
        return cls(ok=bool(d.get("ok")), hists=d.get("hists"),
                   body_ratio=d.get("body_ratio"), aspect=d.get("aspect"),
                   head_hist=d.get("head_hist"), jersey_no=d.get("jersey_no"),
                   missing=list(d.get("missing") or []), err=d.get("err"))


def save_features(work_dir: str, video: str, calib, feats: dict) -> None:
    """features.json 落盘（原子替换；编排层逐球调用——中断后已完成的球不丢）。"""
    os.makedirs(work_dir, exist_ok=True)
    p = os.path.join(work_dir, FEATURES_FILE)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"video": video, "calib": _identity(calib),
                   "feat_ver": FEAT_VERSION,
                   "feats": {k: a.to_dict() for k, a in feats.items()}},
                  f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def load_features(work_dir: str) -> dict | None:
    """读 features.json；缺失/损坏返回 None 不抛（load_shooters 同款容错）。"""
    try:
        with open(os.path.join(work_dir, FEATURES_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def features_valid(data: dict | None, video: str, calib) -> bool:
    """缓存身份校验：同视频 + 同标定指纹 + 同特征版本。"""
    return bool(data) and data.get("video") == video \
        and data.get("calib") == _identity(calib) \
        and data.get("feat_ver") == FEAT_VERSION


# ---- 人形分割：per-球中值背景差分（Task 2）----
BG_HALF_S = 2.5        # 中值背景半窗：出手人跑位/起跳在移动，5s 窗中值洗掉
BG_SAMPLES = 15        # 采样帧数（步进按帧率取整）
DIFF_TH = 28           # 灰度差分阈（合成测试+真值标定轮复核）
PERSON_MIN_H_W = 1.6   # 人形最小高度（筐宽倍数）——更矮=碎片/遮挡残段


def grab_window_frames(video_path, work_dir, t, half_s=BG_HALF_S,
                       n=BG_SAMPLES):
    """[t-half_s, t+half_s] 均匀取 n 帧（PTS 定位，VFR 安全；seek 失败跳过）。"""
    src = _detect_source(video_path, work_dir)
    cap = cv2.VideoCapture(src)
    out = []
    try:
        for k in range(n):
            tk = max(0.0, t - half_s + 2 * half_s * k / max(1, n - 1))
            cap.set(cv2.CAP_PROP_POS_MSEC, tk * 1000.0)
            ok, frame = cap.read()
            if ok and frame is not None:
                out.append(frame)
    finally:
        cap.release()
    return out


def person_mask(video_path, work_dir, t, xy, hoop_w):
    """出手时刻的目标人掩码（per-球中值背景差分）。返回 (mask, bbox, frame) 三元组或 None。

    目标人=包含出手点正下方半筐处的最大连通域（出手点=球在手里，必在人
    形上沿附近）；无包含则退而取中心距出手点 2 筐宽内最大域。域高
    <PERSON_MIN_H_W 筐宽=碎片不算。人静止整窗被中值洗掉 → None（宁缺勿错，
    该球特征缺失落待确认，不硬给）。"""
    frames = grab_window_frames(video_path, work_dir, t)
    if len(frames) < 5:                       # 采样不足（视频尾/读失败）
        return None
    rx, ry = xy
    target_t = t
    # 目标帧单独取（=中值序列最后一帧附近；用与采样同一 seek 路径）
    cap = cv2.VideoCapture(_detect_source(video_path, work_dir))
    try:
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, target_t) * 1000.0)
        ok, frame = cap.read()
    finally:
        cap.release()
    if not ok or frame is None:
        return None
    bg = np.median(np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
                             for f in frames]), axis=0).astype(np.uint8)
    diff = cv2.absdiff(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), bg)
    _, m = cv2.threshold(diff, DIFF_TH, 255, cv2.THRESH_BINARY)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k)
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in cnts:
        bx, by, bw, bh = cv2.boundingRect(c)
        if bh >= PERSON_MIN_H_W * hoop_w:
            boxes.append((bx, by, bw, bh, c))
    if not boxes:
        return None
    probe = (int(rx), int(ry + 0.5 * hoop_w))     # 出手点正下方半筐
    hit = [b for b in boxes
           if b[0] <= probe[0] <= b[0] + b[2] and b[1] <= probe[1] <= b[1] + b[3]]
    pool = hit or [b for b in boxes
                   if ((b[0] + b[2] / 2 - rx) ** 2
                       + (b[1] + b[3] / 2 - ry) ** 2) ** .5 <= 2 * hoop_w]
    if not pool:
        return None
    b = max(pool, key=lambda b: b[2] * b[3])
    bx, by, bw, bh, c = b
    mask_u8 = np.zeros((bh, bw), np.uint8)
    shifted = (c - np.array([[[bx, by]]])).astype(np.int32)  # 平移到 bbox 局部
    cv2.drawContours(mask_u8, [shifted], -1, 1, -1)
    return mask_u8.astype(bool), (bx, by, bw, bh), frame


# ---- 三段颜色指纹：分段直方图 + 皮肤剔除 + 头部票（Task 3）----
SEG_TOP = (0.00, 0.50)     # 上衣（权重最高）
SEG_PANTS = (0.50, 0.80)   # 裤（短裤覆盖率本身是区分特征）
SEG_SHOES = (0.88, 1.00)   # 鞋（十几像素低权重；80~88% 膝部缓冲不取）
HEAD_PART = 0.15           # 头部票：掩码顶部比例（发色+剔发肤色）
SEG_MIN_PX = 60            # 段内非皮肤像素 < 此值 = 该段缺失
HIST_BINS = 32             # H×S 二维直方图每维 bin 数（32*32=1024）
SKIN_HSV = ((3, 17), (40, 160), (60, 230))   # 皮肤 H/S/V 带（初值，标定轮校准）


def _strip_skin(hsv: np.ndarray) -> np.ndarray:
    """皮肤区布尔掩码。S/V 收窄防误剔高饱和同 H 纯色衣（球/橙衣 H 1~12
    与肤色同带——spec：分离度不够就收窄，标定轮验证）。"""
    (h0, h1), (s0, s1), (v0, v1) = SKIN_HSV
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    return ((h >= h0) & (h <= h1) & (s >= s0) & (s <= s1)
            & (v >= v0) & (v <= v1))


def _seg_hist(hsv: np.ndarray, seg_mask: np.ndarray) -> list | None:
    """段内 H-S 二维直方图（归一化拉平 1024）；像素不足返回 None。"""
    if int(seg_mask.sum()) < SEG_MIN_PX:
        return None
    hist = cv2.calcHist([hsv], [0, 1], seg_mask.astype(np.uint8),
                        [HIST_BINS, HIST_BINS], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return hist.flatten().tolist()


def describe_person(frame_bgr, mask, bbox, hoop_w) -> Appearance:
    """人形掩码 → 三段指纹+体型+头部票。段被挡/出画面 → missing 记名、
    该票不参与后续比对（缺一票少一票，不拿空段瞎算）。

    mask 是 person_mask 返回的 **bbox 局部掩码**（bh×bw）——内部先把
    HSV 裁到同尺寸补丁，全部按局部坐标切段（Task 4 直接接线）。"""
    bx, by, bw, bh = bbox
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    hsv = hsv[by:by + bh, bx:bx + bw]              # 裁到 bbox 局部（与 mask 同形）
    keep = ~_strip_skin(hsv) & mask                # 剔皮肤后的有效像素
    hists, missing = {}, []
    for name, (r0, r1) in (("top", SEG_TOP), ("pants", SEG_PANTS),
                           ("shoes", SEG_SHOES)):
        y0, y1 = int(bh * r0), int(bh * r1)
        seg = np.zeros_like(keep)
        seg[y0:y1, :] = keep[y0:y1, :]
        h = _seg_hist(hsv, seg)
        hists[name] = h
        if h is None:
            missing.append(name)
    # 头部票：顶部 15% 发色 H 直方图（含光头=头皮肤色，与发色同票处理）
    head = np.zeros_like(mask)
    head[:int(bh * HEAD_PART), :] = mask[:int(bh * HEAD_PART), :]
    if int(head.sum()) >= 20:      # 头部票是 H-only 32bin（与 Task 1 契约/
        hh = cv2.calcHist([hsv], [0], head.astype(np.uint8), [HIST_BINS],
                          [0, 180])   # Task 5 测试/spec 一致——非 _seg_hist 的
        cv2.normalize(hh, hh)         # H-S 二维 1024）
        head_hist = hh.flatten().tolist()
    else:
        head_hist = None
    # 体型：掩码高/筐宽 + 高宽比（防守人贴身粘连致掩码过宽时 aspect 失真，
    # 由聚类端 body_compatible 容差吸收，这里照记）
    return Appearance(ok=sum(1 for v in hists.values() if v) >= 2,
                      hists=hists, body_ratio=round(bh / hoop_w, 3),
                      aspect=round(bh / max(1, bw), 3), head_hist=head_hist,
                      missing=missing)


# ---- 编排：逐球 溯源锚 → 人形分割 → 指纹 → 写盘（Task 4）----
def build_features(video_path, calib, work_dir, traces: dict):
    """逐球提外观指纹（生成器：yield 进度、return 汇总——trace_video 惯例）。

    只处理 traces 里 release_t/release_xy 齐全的球（low/未溯源无锚点，
    直接跳过不产条目）。单球异常不 raise：记 ok=False + err 继续——
    生成器中途 raise 会让 UI 按钮永久卡灰。缓存身份相符的球跳过（续跑）。
    """
    data = load_features(work_dir)
    feats = {k: Appearance.from_dict(v)
             for k, v in (data.get("feats") or {}).items()} \
        if features_valid(data, video_path, calib) else {}
    todo = [(k, v) for k, v in (traces or {}).items()
            if k not in feats and v.get("release_t") is not None
            and v.get("release_xy")]
    if not todo:
        return (f"✅ 外观指纹已齐全（{len(feats)} 球缓存，未重提）")
    ok_n = 0
    cap = cv2.VideoCapture(_detect_source(video_path, work_dir))   # 帧尺寸只取一次
    fw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    fh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    segs = _segs_of(calib)   # A2 多段：筐宽尺度逐球按所属段几何取
    for k, (key, tr) in enumerate(todo, 1):
        _, _, hoop_w, _ = _scaled_rect(
            _seg_for_t(segs, _t_of_key(key)).calib, fw, fh)
        try:
            res = person_mask(video_path, work_dir, tr["release_t"],
                              tuple(tr["release_xy"]), hoop_w)
            app = (describe_person(res[2], res[0], res[1], hoop_w)
                   if res else Appearance(ok=False, err="无人形掩码"))
        except Exception as ex:  # noqa: BLE001 —— 单球失败不拖垮整批
            app = Appearance(ok=False, err=str(ex))
        feats[key] = app
        ok_n += bool(app.ok)
        save_features(work_dir, video_path, calib, feats)    # 逐球写盘
        yield (f"⏳ 提取外观（{k}/{len(todo)}）：{key} → "
               + ("✅" if app.ok else "⚪ " + (app.err or "未取到")))
    return (f"✅ 外观指纹完成：{ok_n}/{len(todo)} 球有效（缓存 "
            f"{FEATURES_FILE}）")


# ---- 相似度与聚类：连通分量 + 宁拆勿错并 + review 只挂不建（Task 5）----
# ---- 聚类阈值（2026-08-29 Task 6 两场真值标定结论：同人/不同人相似度
# 分布重叠、无分离度——连边建组下线，UI/生产路径不再调用 build_groups。
# 常量与代码保留仅供调试；证据：spec 第二轮修订拍板表 + progress.md ①-⑬）----
EDGE_SIM = 0.70        # （下线）原连边阈值
CHAIN_SIM = 0.55       # （下线）原链式复检阈值
ATTACH_SIM = 0.75      # （下线）原 review 挂组阈值
MIN_GOAL_GAP_S = 2.5   # 保留：唯一真值存活约束（两场同人最短间隔 3.3s），
                       # 转手动归人护栏（gap_violations）
BODY_TOL = 0.35        # （下线）原体型 AND 票容差
ASPECT_TOL = 0.18      # （下线）原高宽比容差
HEAD_BHAT = 0.55       # （下线）原头部仲裁票阈值
SEG_W = {"top": 0.55, "pants": 0.30, "shoes": 0.15}


def _hist_sim(h1, h2) -> float:
    """分段直方图相似度 [0,1]。_seg_hist 产出是 L2 归一化（Σbin 可 >1，
    均匀分布 Σ=√n），裸交集会破 [0,1] 契约——交集前先 L1 归一化。
    （2026-08-29 Task 6b 修复；Task 6 标定口径未归一、绝对值偏大，
    分布重叠的相对结论不变。）"""
    a = np.asarray(h1, dtype=np.float32)
    b = np.asarray(h2, dtype=np.float32)
    a = a / (float(a.sum()) or 1.0)
    b = b / (float(b.sum()) or 1.0)
    return float(cv2.compareHist(a, b, cv2.HISTCMP_INTERSECT))


def feat_similar(a: Appearance, b: Appearance) -> float:
    """可用段加权和（缺失段按可用段重归一）——缺一票少一票，不拿空段瞎算。"""
    pairs = [(SEG_W[n], _hist_sim(a.hists[n], b.hists[n]))
             for n in SEG_W
             if a.hists.get(n) and b.hists.get(n)]
    if not pairs:
        return 0.0
    w_sum = sum(w for w, _ in pairs)
    return sum(w * s for w, s in pairs) / w_sum


def body_compatible(a: Appearance, b: Appearance) -> bool:
    """体型 AND 条件（从严合并的第二票）。任一缺失 → True（缺票不拦）。"""
    if a.body_ratio and b.body_ratio \
            and abs(a.body_ratio - b.body_ratio) > BODY_TOL:
        return False
    if a.aspect and b.aspect and abs(a.aspect - b.aspect) > ASPECT_TOL:
        return False
    return True


def head_splits(a: Appearance, b: Appearance) -> bool:
    """头部仲裁票（只拆不合）：双方齐全且巴氏距离大（发色肤色明显不同）
    → 拆。距离小=相似不拆；任一缺票不拆（缺票不拦）。"""
    if not a.head_hist or not b.head_hist:
        return False
    d = cv2.compareHist(np.array(a.head_hist, np.float32),
                        np.array(b.head_hist, np.float32),
                        cv2.HISTCMP_BHATTACHARYYA)
    return bool(d > HEAD_BHAT)


def _goal_t(key: str) -> float:
    m, s = key.split(":")
    return int(m) * 60 + float(s)


def gap_violations(keys, gap_s: float = MIN_GOAL_GAP_S):
    """归人护栏（spec 第二轮修订：聚类拆分约束转手动归人护栏，警示不阻断）。
    球 key 列表内最短间隔违例——排序后只查相邻对：非相邻对 <gap 必然
    夹着更小的相邻间隔。返回 [(key1, key2, 间隔秒)]。"""
    # 按解析后的时刻排序（不能按 key 字符串排：跨 100 分钟后字典序错乱，
    # 08-22 场 110:xx 会排在 32:xx 前，负间隔被误判违例）
    ts = sorted(((k, _goal_t(k)) for k in keys or []), key=lambda p: p[1])
    return [(k1, k2, round(t2 - t1, 2))
            for (k1, t1), (k2, t2) in zip(ts, ts[1:]) if t2 - t1 < gap_s]


def build_groups(traces: dict, feats: dict) -> list:
    """聚类（纯内存，秒级）：high+有效指纹连边建组 → 链式复检 → review
    只挂不建。返回组列表（id G1..、members 行键、status ok/suspect/pending）。
    宁拆勿错并：拆开=同人命名两次；错并=一串球全标错人。"""
    builders = [k for k, v in (traces or {}).items()
                if v.get("status") == "high"
                and (feats.get(k) or Appearance(ok=False)).ok]
    # ---- 连通分量（并查集）----
    parent = {k: k for k in builders}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        parent[find(x)] = find(y)

    for i, k1 in enumerate(builders):
        for k2 in builders[i + 1:]:
            a, b = feats[k1], feats[k2]
            if (feat_similar(a, b) >= EDGE_SIM and body_compatible(a, b)
                    and not head_splits(a, b)):
                union(k1, k2)
    comps: dict[str, list] = {}
    for k in builders:
        comps.setdefault(find(k), []).append(k)
    # ---- 时空硬约束（只拆不合，组内后验）：进球时刻几乎相同的两个射手
    # 必是不同人。不做边过滤——边过滤挡不住传递并组（A 与 C 同刻、经 B
    # 仍进同组）。拆冲突数最多的成员（平手取进球更晚者），重复至无冲突。----
    def _split_conflicts(members: list) -> tuple[list, list]:
        kept, removed = list(members), []
        while True:
            conflict = {m: 0 for m in kept}
            for i, m1 in enumerate(kept):
                for m2 in kept[i + 1:]:
                    if abs(_goal_t(m1) - _goal_t(m2)) <= MIN_GOAL_GAP_S:
                        conflict[m1] += 1
                        conflict[m2] += 1
            bad = [m for m, c in conflict.items() if c]
            if not bad:
                return kept, removed
            worst = max(bad, key=lambda m: (conflict[m], _goal_t(m)))
            kept.remove(worst)
            removed.append(worst)

    groups = []
    for members in comps.values():
        kept, removed = _split_conflicts(members)
        groups.extend({"members": [m]} for m in removed)
        members = kept
        # ---- 链式复检：组内两两最小相似度 < CHAIN_SIM 的最低者拆出 ----
        while len(members) > 1:   # 两人组也查——时空拆桥/复检收缩幸存的对
                                   # 可能无直达边（<CHAIN_SIM），直达边对
                                   # ≥EDGE_SIM>CHAIN_SIM 不受影响
            sims = {m: min(feat_similar(feats[m], feats[o])
                           for o in members if o != m) for m in members}
            worst = min(sims, key=sims.get)
            if sims[worst] >= CHAIN_SIM:
                break
            members = [m for m in members if m != worst]
            groups.append({"members": [worst]})   # 拆出单列，下轮与 review 一起挂
        groups.append({"members": members})
    groups.sort(key=lambda g: -len(g["members"]))
    # ---- review 只挂不建 ----
    pending = []
    for k, v in (traces or {}).items():
        if v.get("status") != "review":
            continue
        f = feats.get(k)
        best = None
        if f and f.ok:
            for g in groups:
                if any(abs(_goal_t(k) - _goal_t(m)) <= MIN_GOAL_GAP_S
                       for m in g["members"]):
                    continue
                if any(feat_similar(f, feats[m]) >= ATTACH_SIM
                       and body_compatible(f, feats[m])
                       for m in g["members"] if (feats.get(m) or Appearance(ok=False)).ok):
                    best = g
                    break
        if best:
            best["members"].append(k)
        else:
            pending.append(k)
    # ---- 编号与状态 ----
    out = []
    for i, g in enumerate(groups, 1):
        has_review = any((traces.get(m) or {}).get("status") == "review"
                         for m in g["members"])
        out.append({"id": f"G{i}", "members": sorted(g["members"]),
                    "status": "suspect" if has_review else "ok"})
    out += [{"id": f"P{j}", "members": [k], "status": "pending"}
            for j, k in enumerate(pending, 1)]
    return out


def save_groups(work_dir, video, calib, groups: list) -> None:
    os.makedirs(work_dir, exist_ok=True)
    p = os.path.join(work_dir, GROUPS_FILE)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"video": video, "calib": _identity(calib),
                   "feat_ver": FEAT_VERSION, "cluster_ver": CLUSTER_VERSION,
                   "groups": groups}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def load_groups(work_dir) -> dict | None:
    try:
        with open(os.path.join(work_dir, GROUPS_FILE), encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def groups_valid(data: dict | None, video: str, calib) -> bool:
    """身份=视频+标定指纹+两版本。命名/并组等 UI 手动演化**不改身份**
    （groups.json 是活数据不是推导缓存，重开页面不重聚）。"""
    return bool(data) and data.get("video") == video \
        and data.get("calib") == _identity(calib) \
        and data.get("feat_ver") == FEAT_VERSION \
        and data.get("cluster_ver") == CLUSTER_VERSION
