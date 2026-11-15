# -*- coding: utf-8 -*-
"""person 框归属与裁剪几何（bbh/person.py）单测。

口径出处：2026-10-15 spike（36 球，两场真实录像）——
  - 碎片合并：21% 原始框是同人碎片（远景遮挡拆成脸框+腿框）；
  - 球锚归属：出手时刻球在谁手上谁就是出手人，35/36 通过 0 认错
    （#12/#24 实测几何做成回归夹具：球就在出手点上，但出手点被
    邻居框交叠盖住，点锚会认错人）；
  - #30 实测几何：球在框外 15%~30% 处（手伸出框边）——容差档兜住。
全档注入假框/合成帧，不跑真模型（真模型已由 spike 36 球验证）。
"""
import cv2
import numpy as np
import pytest

from bbh import person

# 640x360 合成帧常量（同 conftest 场景：HSV 低高 (0,80,120)/(25,255,255)，
# 球 BGR(20,100,245) 在范围内）
FW, FH = 640, 360
BALL_BGR = (20, 100, 245)


def box(x0, y0, x1, y1, conf=0.9):
    return (float(x0), float(y0), float(x1), float(y1), float(conf))


# ---------------------------------------------------------------- 碎片合并
def test_碎片合并_同人纵邻并框():
    """spike 实测：远景遮挡把同一人拆成「脸框 + 腿框」，纵向堆叠、横向
    几乎对齐——并成整身框（conf 取大者）。"""
    face = box(300, 100, 340, 160, 0.8)
    legs = box(302, 165, 342, 260, 0.6)
    out = person.merge_fragments([face, legs])
    assert len(out) == 1
    assert out[0][:4] == (300.0, 100.0, 342.0, 260.0)
    assert out[0][4] == 0.8                      # conf 取大


def test_碎片合并_旁人不并():
    """并排两人（横向重叠不足）与前后两人（纵向间隙过大）都不并。"""
    a = box(100, 100, 160, 300)
    b = box(200, 100, 260, 300)                  # 旁人：横向不重叠
    far = box(100, 400, 160, 500)                # 纵向间隙 100 > 0.3*300=90 不并
    out = person.merge_fragments([a, b, far])
    assert len(out) == 3


def test_碎片合并_链式三段归一():
    """头+躯干+腿三段碎片（相邻段各自满足合并条件）→ 一框。"""
    out = person.merge_fragments([box(300, 100, 340, 150),    # 头
                                  box(298, 152, 342, 230),    # 躯干
                                  box(300, 232, 340, 300)])   # 腿
    assert len(out) == 1
    assert out[0][:4] == (298.0, 100.0, 342.0, 300.0)


# ---------------------------------------------------------------- 归属阶梯
def test_归属_球锚压过点锚():
    """#12/#24 回归夹具（实锤认错人根因）：出手点被两人框交叠盖住，点锚
    排序（躯干段/框高）选中邻居——球在另一人框里，含球框必须赢。"""
    taller_neighbor = box(300, 100, 360, 300)    # 更高（点锚会选它）
    holder = box(340, 120, 400, 310, 0.5)        # 球在他手上
    px, py = 345, 200                            # 出手点：两框交叠区
    hit, chosen = person.attribute([taller_neighbor, holder],
                                   ball=(370, 220), px=px, py=py)
    assert hit == "ball"
    assert chosen[:4] == holder[:4]


def test_归属_球在框外容差档():
    """#30 回归夹具：球（手举过框边）在所有框外 15%~30% 处——球锚 0.30
    容差档兜住，不许直接跳去点锚选邻居。"""
    p = box(300, 100, 360, 300)                  # 上缘 y=100，高 200
    # 球 (330,60)：距上缘 40px = 20% 高 → @0.15(30px) 不含、@0.30(60px) 含
    other = box(380, 50, 440, 250)               # 邻居（不含球不含点）
    hit, chosen = person.attribute([p, other], ball=(330, 60), px=310, py=200)
    assert hit == "ball"
    assert chosen[:4] == p[:4]


def test_归属_无球退点锚_躯干段优先():
    """HSV 没找到球（远/糊）→ 点锚：多框含点时躯干段（10%~90% 高）优先
    于头顶/脚底，同级取整身高大者。"""
    head_only = box(300, 100, 360, 180)          # 点落他头段
    torso = box(310, 120, 380, 320)              # 点落躯干段
    hit, chosen = person.attribute([head_only, torso],
                                   ball=None, px=345, py=200)
    assert hit == "in"
    assert chosen[:4] == torso[:4]


def test_归属_全都不含退最近():
    """点在所有框外（出手点漂移）→ 距框底中心最近的框，标 near（低置信）。"""
    a = box(100, 100, 160, 300)
    b = box(300, 100, 360, 300)                  # 距 (250,260) 更近
    hit, chosen = person.attribute([a, b], ball=None, px=250, py=260)
    assert hit == "near"
    assert chosen[:4] == b[:4]


def test_归属_空框返回None():
    assert person.attribute([], ball=None, px=1, py=1) == (None, None)


# ---------------------------------------------------------------- HSV 球锚
def _frame_with_blobs(*xys):
    fr = np.full((FH, FW, 3), (40, 44, 50), np.uint8)
    for x, y in xys:
        cv2.circle(fr, (x, y), 6, BALL_BGR, -1)
    return fr


def test_球锚_取出手点最近的球块():
    fr = _frame_with_blobs((200, 200), (260, 210))     # 距 (250,205): 50 vs 10
    lo = np.array((0, 80, 120), np.uint8)
    hi = np.array((25, 255, 255), np.uint8)
    got = person.ball_anchor(fr, lo, hi, 250, 205)
    assert got is not None
    assert got[0] == pytest.approx(260.0, abs=1.0)     # 连通域中心（亚像素）
    assert got[1] == pytest.approx(210.0, abs=1.0)


def test_球锚_超距返回None():
    fr = _frame_with_blobs((500, 100))                 # 距出手点 >60px
    lo = np.array((0, 80, 120), np.uint8)
    hi = np.array((25, 255, 255), np.uint8)
    assert person.ball_anchor(fr, lo, hi, 250, 205) is None


def test_球锚_无球帧返回None():
    fr = np.full((FH, FW, 3), (40, 44, 50), np.uint8)
    lo = np.array((0, 80, 120), np.uint8)
    hi = np.array((25, 255, 255), np.uint8)
    assert person.ball_anchor(fr, lo, hi, 250, 205) is None


# ---------------------------------------------------------------- 裁剪几何
def test_裁剪框_外扩与clamp():
    """person 框四边外扩 CROP_PAD（45%），出画面 clamp 到帧界。"""
    x0, y0, x1, y1 = person.crop_rect(box(300, 100, 360, 260), FW, FH)
    w, h = 60, 160
    assert x0 == 300 - round(0.45 * w) and x1 == 360 + round(0.45 * w)
    assert y0 == 100 - round(0.45 * h) and y1 == 260 + round(0.45 * h)
    # 贴角框 clamp 不越界
    e0, f0, e1, f1 = person.crop_rect(box(2, 2, 40, 90), FW, FH)
    assert e0 >= 0 and f0 >= 0 and e1 <= FW and f1 <= FH


# ---------------------------------------------------------------- 检人开关
def test_检人_开关关闭返回None(monkeypatch):
    monkeypatch.setattr(person, "NAME", "off")
    fr = _frame_with_blobs((250, 205))
    assert person.pick_person(fr, np.array((0, 80, 120), np.uint8),
                              np.array((25, 255, 255), np.uint8),
                              (250, 205)) == (None, None)


def test_检人_模型加载失败优雅降级(monkeypatch):
    """权重缺失/推理异常 → 无框返回 None（缩略图退回出手点定宽裁剪，
    不让整个识别流程 raise）。"""
    monkeypatch.setattr(person, "NAME", "on")

    def boom(frame):
        raise RuntimeError("load failed")

    monkeypatch.setattr(person, "_detect_raw", boom)
    fr = _frame_with_blobs((250, 205))
    assert person.pick_person(fr, np.array((0, 80, 120), np.uint8),
                              np.array((25, 255, 255), np.uint8),
                              (250, 205)) == (None, None)


def test_检人_注入假框端到端归属(monkeypatch):
    """pick_person 全链（检人→合并→球锚→阶梯）：帧里画真球（HSV 可检），
    框注入——持球框不在出手点上（#12 形态），最终选含球框。"""
    monkeypatch.setattr(person, "NAME", "on")
    taller = box(300, 100, 360, 300)
    holder = box(340, 120, 400, 310, 0.5)
    monkeypatch.setattr(person, "_detect_raw",
                        lambda fr: [taller, holder])
    fr = _frame_with_blobs((370, 220))                # 球在 holder 框内
    hit, chosen = person.pick_person(
        fr, np.array((0, 80, 120), np.uint8),
        np.array((25, 255, 255), np.uint8), (345, 200))
    assert hit == "ball" and chosen[:4] == holder[:4]
