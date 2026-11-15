# -*- coding: utf-8 -*-
r"""出手人 person 框：检人、归属、裁剪几何（展示层，不碰溯源/判定）。

来历
----
2026-10-15 spike（36 球两场实录验证）后收编。动机：缩略图原来按出手点
定宽裁剪，远景下框里常常没有完整的人；COCO 预训 yolov8n 检人 + **球锚
归属**后，裁的是出手人本身。

归属为什么锚在球上（spike 实测，36 球 0 认错）
------------------------------------------------
出手时刻球在谁手上，谁就是出手人。出手点只是个估计坐标（已知有
0.5~1m 低偏），且常被旁边防守者的框交叠盖住——spike #12/#24 两球实锤：
球就在出手点上（距 2~5px），但「点在谁的框里」按框高排序选中了邻居。
球是物理真值：HSV 球块（与溯源同款标定口径）落在谁的框里就归谁。

阶梯（宽进严出，宁可低置信不可认错人）：

    含球框@15% → 含球框@30% → 含点框 → 扩30%含点框 → 最近框(near)
    同级排序：躯干段(框高10%~90%) > 整身高 > 置信度

碎片合并：远景遮挡下 COCO 常把同一人拆成「脸框+腿框」（spike 实测
21% 原始框是碎片）——纵邻合并（横向重叠过半窄框 + 纵向间隙 <30% 框高
→ 并框），归属前先并。

开关与降级
----------
``BBH_PERSON = on（默认） | off``。off / 权重缺失 / 推理任何异常 →
``pick_person`` 返回 ``(None, None)``，缩略图退回出手点定宽裁剪
（shooter._crop_box）——单测合成帧里没有人，tests/conftest.py 固定 off。

版本
----
``PERSON_VERSION`` 版本归属属/裁剪**参数**：不匹配只重裁缩略图
（traces 里的 person_ver 逐球补），对照 SHOOTER_VERSION 作废=全量重扫。
"""
import os

import cv2
import numpy as np

from .detector import MIN_AREA

NAME = (os.environ.get("BBH_PERSON") or "on").strip().lower()
if NAME not in ("on", "off"):
    NAME = "on"

WEIGHTS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "weights", "yolov8n.pt")

PERSON_VERSION = 2    # 归属/裁剪参数版本（spike v3 口径收编时起步即 2：
                      # 预留 1 = 曾经的纯点锚实现，防旧实验档误判已升级）

CONF = 0.25           # COCO 预训 person 类默认阈值（spike 同款）
IMGSZ = 1280          # 远景人小，放大输入（spike 同款）
MERGE_OX = 0.55       # 碎片合并门：横向重叠 > 55% 较窄框宽
MERGE_GAP = 0.30      # 碎片合并门：纵向间隙 < 30% 较高框高
MIN_BOX_H = 20        # 合并后框高下限（px）：更矮的不是整身人（残留碎片）
BALL_TOL = 60.0       # 球锚：出手点 60px 内的最近 HSV 球块才算（spike 同款）
BALL_MARGINS = (0.15, 0.30)   # 含球判定容差档（#30：手举过框边 20% 高）
EXPAND = 0.30         # 点锚兜底：框外扩 30% 含点
TORSO = (0.10, 0.90)  # 躯干段：点/球落这里比头顶/脚底可信
CROP_PAD = 0.45       # person 框四边外扩比例（spike 同款，认人要留肢体余量）

_model = None         # 进程内单例（首球推理时加载，~6MB yolov8n）


def _detect_raw(frame):
    """单帧 COCO person 检测 → [(x0, y0, x1, y1, conf)]（classes=[0]）。
    加载/推理失败向上抛，由 pick_person 统一降级。"""
    global _model
    if _model is None:
        from ultralytics import YOLO     # 延迟导入：off 时零开销
        _model = YOLO(WEIGHTS)
    r = _model(frame, classes=[0], conf=CONF, imgsz=IMGSZ, verbose=False)[0]
    return [(float(b.xyxy[0][0]), float(b.xyxy[0][1]),
             float(b.xyxy[0][2]), float(b.xyxy[0][3]), float(b.conf[0]))
            for b in r.boxes]


# ---------------------------------------------------------------- 碎片合并
def merge_fragments(boxes):
    """纵邻合并到不动点（链式三段头+躯干+腿也能归一）。

    横向重叠 > MERGE_OX×较窄框宽 且 纵向间隙 < MERGE_GAP×较高框高 →
    并成包络框，conf 取大（合并框是整身，可信度不低于碎片）。"""
    boxes = [list(b) for b in boxes]
    merged = True
    while merged:
        merged = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                ox = min(a[2], b[2]) - max(a[0], b[0])
                if ox <= MERGE_OX * min(a[2] - a[0], b[2] - b[0]):
                    continue
                gap = max(a[1], b[1]) - min(a[3], b[3])   # 负=重叠
                if gap > MERGE_GAP * max(a[3] - a[1], b[3] - b[1]):
                    continue
                boxes[i] = [min(a[0], b[0]), min(a[1], b[1]),
                            max(a[2], b[2]), max(a[3], b[3]),
                            max(a[4], b[4])]
                boxes.pop(j)
                merged = True
                break
            if merged:
                break
    return [tuple(b) for b in boxes if b[3] - b[1] >= MIN_BOX_H]


# ---------------------------------------------------------------- 归属
def _contains(b, x, y, k):
    """点是否在框内（含 k 比例外扩容差；k=0 即严格含）。"""
    w, h = b[2] - b[0], b[3] - b[1]
    return (b[0] - w * k) <= x <= (b[2] + w * k) \
        and (b[1] - h * k) <= y <= (b[3] + h * k)


def _rank(b, x, y):
    """同级候选排序键：锚点在躯干段 > 整身高 > 置信度。"""
    tp = (y - b[1]) / max(b[3] - b[1], 1e-6)
    return (1 if TORSO[0] <= tp <= TORSO[1] else 0, b[3] - b[1], b[4])


def attribute(boxes, ball, px, py):
    """归属阶梯（模块 docstring 的口径）。

    ball=(bx,by) 或 None；px,py=出手点。返回 (hit, box)：
    hit ∈ ball/in/expand/near（near=低置信，都不含锚点）；
    boxes 空 → (None, None)。"""
    if not boxes:
        return None, None
    if ball:
        for k in BALL_MARGINS:
            hits = [b for b in boxes if _contains(b, ball[0], ball[1], k)]
            if hits:
                return "ball", max(hits, key=lambda b: _rank(b, *ball))
    hits = [b for b in boxes
            if b[0] <= px <= b[2] and b[1] <= py <= b[3]]
    if hits:
        return "in", max(hits, key=lambda b: _rank(b, px, py))
    ex = [b for b in boxes if _contains(b, px, py, EXPAND)]
    if ex:
        return "expand", max(ex, key=lambda b: _rank(b, px, py))
    near = min(boxes, key=lambda b: ((px - (b[0] + b[2]) / 2) ** 2
                                     + (py - b[3]) ** 2) ** 0.5)
    return "near", near


# ---------------------------------------------------------------- 球锚
def ball_anchor(frame, hsv_lo, hsv_hi, px, py):
    """出手点 BALL_TOL 内最近的 HSV 球块中心 (bx, by)；无则 None。

    口径同溯源找出手点的技术（inRange + 开运算 + 连通域）：这里要的是
    「手上那颗球」的精确位置，小到 9px 的远景球也算（MIN_AREA）；
    开运算核 3x3 比扫描口径 5x5 细——spike 36 球按此口径 0 认错。"""
    mask = cv2.inRange(cv2.cvtColor(frame, cv2.COLOR_BGR2HSV), hsv_lo, hsv_hi)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask)
    best = None       # (dist, cx, cy)
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if a < MIN_AREA:
            continue
        cx, cy = x + w / 2, y + h / 2
        d = ((cx - px) ** 2 + (cy - py) ** 2) ** 0.5
        if d <= BALL_TOL and (best is None or d < best[0]):
            best = (d, cx, cy)
    return (best[1], best[2]) if best else None


# ---------------------------------------------------------------- 单帧流水
def pick_person(frame, hsv_lo, hsv_hi, release_xy):
    """一帧定归属：检人 → 碎片合并 → 球锚 → 阶梯。

    返回 (hit, box)；检人不可用/没检到人 → (None, None)（调用方试下一帧
    或退定宽裁剪）。hsv_lo/hi 传溯源口径（shooter._scan_hsv，含 S 地板）。"""
    if NAME != "on" or frame is None or release_xy is None:
        return None, None
    try:
        raw = _detect_raw(frame)
    except Exception:                     # noqa: BLE001 —— 降级不 raise
        return None, None
    boxes = merge_fragments(raw)
    if not boxes:
        return None, None
    px, py = release_xy
    hsv_lo = np.asarray(hsv_lo, np.uint8)
    hsv_hi = np.asarray(hsv_hi, np.uint8)
    ball = ball_anchor(frame, hsv_lo, hsv_hi, px, py)
    return attribute(boxes, ball, px, py)


# ---------------------------------------------------------------- 裁剪几何
def crop_rect(box, fw, fh):
    """person 框 + CROP_PAD 四边外扩，clamp 到帧界（纯函数，便于测试）。"""
    w, h = box[2] - box[0], box[3] - box[1]
    x0 = max(0, int(round(box[0] - w * CROP_PAD)))
    y0 = max(0, int(round(box[1] - h * CROP_PAD)))
    x1 = min(fw, int(round(box[2] + w * CROP_PAD)))
    y1 = min(fh, int(round(box[3] + h * CROP_PAD)))
    return x0, y0, x1, y1
