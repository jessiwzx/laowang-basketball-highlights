# -*- coding: utf-8 -*-
"""筐锚点（bbh/hoop_track.py）单测：纯函数，不碰视频/模型。

数值口径：标定筐口 [100,100,40,16] → 筐心 (120,108)、筐宽 40、筐高 16；
模型框中心比手点框中心固定偏 (6,−2)（实测 6.9~8.2px，段内恒定）。
"""
import numpy as np
import pytest

from bbh import hoop_track as ht


# --------------------------------------------------------------- 假推理结果
class _Arr:
    def __init__(self, a):
        self._a = np.asarray(a)

    def cpu(self):
        return self

    def numpy(self):
        return self._a


class _Boxes:
    def __init__(self, xyxy, conf, cls):
        self.xyxy = _Arr(xyxy)
        self.conf = _Arr(conf)
        self.cls = _Arr(cls)

    def __len__(self):
        return len(self.conf.numpy())


class _Res:
    def __init__(self, boxes):
        self.boxes = boxes


def _res(*items):
    """items: (cx, cy, w, h, conf, cls)"""
    xyxy, conf, cls = [], [], []
    for cx, cy, w, h, c, k in items:
        xyxy.append([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2])
        conf.append(c)
        cls.append(k)
    return _Res(_Boxes(xyxy, conf, cls))


RECT = [100.0, 100.0, 40.0, 16.0]
REF = (126.0, 106.0)          # 参考帧上的模型筐心 = 手点筐心 + (6,−2)


def _samples(pairs, w=44.0, h=15.0):
    """[(t, cx)] → 采样表（cy 固定 106、尺寸固定）"""
    return [[float(t), float(cx), 106.0, w, h, 0.7] for t, cx in pairs]


# ------------------------------------------------------------------ pick_hoop
def test_取置信度最高的筐框():
    res = _res((500, 300, 40, 16, 0.35, 1),      # 另一个筐（观众席）
               (126, 106, 44, 15, 0.72, 1),
               (340, 100, 20, 20, 0.90, 0))      # 球，不是筐
    assert ht.pick_hoop(res, 1, 0.08) == (126, 106, 44, 15, 0.72)


def test_没有筐框时返回None():
    res = _res((340, 100, 20, 20, 0.90, 0))
    assert ht.pick_hoop(res, 1, 0.08) is None
    assert ht.pick_hoop(_Res(_Boxes([], [], [])), 1, 0.08) is None


def test_置信度不足的筐框被拒():
    res = _res((126, 106, 44, 15, 0.05, 1))
    assert ht.pick_hoop(res, 1, 0.08) is None


# ---------------------------------------------------------------- 采样器节流
def test_采样按帧号节流():
    sp = ht.HoopSampler(every=100)
    res = _res((126, 106, 44, 15, 0.7, 1))
    assert sp.feed(res, t=0.0, fi=0) is True
    assert sp.feed(res, t=1.0, fi=50) is False       # 未到节拍
    assert sp.feed(res, t=2.0, fi=100) is True
    assert len(sp.samples) == 2
    assert sp.samples[0] == [0.0, 126.0, 106.0, 44.0, 15.0, 0.7]


def test_采样器可用外部容器承接续跑():
    store = [[0.0, 126.0, 106.0, 44.0, 15.0, 0.7]]   # 检查点预置
    sp = ht.HoopSampler(every=100, storage=store)
    sp.feed(_res((126, 106, 44, 15, 0.7, 1)), t=2.0, fi=100)
    assert len(store) == 2 and sp.samples is store


# --------------------------------------------------------------- 校正项构建
def test_无采样或无基准时不校正():
    assert ht.build_correction([], [], RECT, REF)[0] == []
    corr, st = ht.build_correction(_samples([(0, 126)]), [], RECT, None)
    assert corr == [] and st["why"] == "no_ref"


def test_固定偏置被参考帧基准抵消():
    """筐全程没动、ORB 也没动 → 残差应恒为 0（若用标定框中心当基准，
    会得到固定的 (+6,−2)，凭空平移判定几何 0.15 筐宽）。"""
    corr, st = ht.build_correction(_samples([(t, 126) for t in (0, 200, 400)]),
                                   [], RECT, REF)
    assert st["n_used"] == 3
    assert all(abs(x) < 1e-6 and abs(y) < 1e-6 for _, x, y in corr)
    assert st["max_res"] == 0.0


def test_整机平移的真实台阶被保留():
    """前半段筐在 126、后半段被平移到 146，而 ORB 没跟上（shift 恒 0）
    → 校正项必须在台阶处跳到 +20（0.5 筐宽），否则那段几何仍然错。"""
    smp = _samples([(0, 126), (200, 126), (400, 126),
                    (600, 146), (800, 146), (1000, 146)])
    corr, st = ht.build_correction(smp, [(0.0, 0.0, 0.0)], RECT, REF)
    xs = [x for _, x, _ in corr]
    assert xs[:3] == [0.0, 0.0, 0.0]
    assert xs[3:] == [20.0, 20.0, 20.0]
    assert st["max_res"] == pytest.approx(0.5, abs=1e-6)


def test_单点误检被中值滤波抹掉():
    """镜头没动，某个采样点冒了 +50px 的误检框 → 不能让它把几何带偏。"""
    smp = _samples([(t, 126) for t in (0, 200, 400, 600, 800)])
    smp[2][1] = 176                                  # 第 3 点是误检
    corr, _ = ht.build_correction(smp, [], RECT, REF)
    assert [x for _, x, _ in corr] == [0.0] * 5


def test_ORB已经跟上的位移不重复校正():
    """筐被平移 +20，ORB 也测到了 +20 → 校正项应为 0（不然会双减）。

    ORB 采样间隔 ~5s，与筐采样（20s）在时间上必然有 ≤20s 的过渡差；这里
    让两者在同一采样点对齐，断言的是**稳态**（过渡区间允许短暂非零）。"""
    smp = _samples([(0, 126), (200, 126), (600, 146), (800, 146), (1000, 146)])
    shift = [(0.0, 0.0, 0.0), (500.0, 0.0, 0.0), (600.0, 20.0, 0.0),
             (1000.0, 20.0, 0.0)]
    corr, st = ht.build_correction(smp, shift, RECT, REF)
    assert all(abs(x) < 1e-6 for _, x, _ in corr)
    assert st["max_res"] < 1e-6


def test_变焦样本被段内尺寸一致性剔除():
    smp = _samples([(t, 126) for t in (0, 200, 400, 600)])
    smp[3][3] = 88.0                                 # 宽度翻倍 = 变焦/框错
    corr, st = ht.build_correction(smp, [], RECT, REF)
    assert st["n_size"] == 1
    assert st["n_used"] == 3


def test_绝对尺寸越界的样本先被丢():
    smp = _samples([(t, 126) for t in (0, 200, 400, 600)])
    smp[1][3] = 200.0                                # 200/40 = 5 倍，绝对门拦下
    corr, st = ht.build_correction(smp, [], RECT, REF)
    assert st["n_size"] == 1 and st["n_used"] == 3


def test_样本太少不复算():
    corr, st = ht.build_correction(_samples([(0, 126), (200, 126)]), [], RECT, REF)
    assert corr == [] and st["why"] == "too_few"


# ------------------------------------------------------------------- 坐标重映射
def test_remap空校正原样返回同一对象():
    pos = [object()]
    sh = [("s",)]
    assert ht.remap_positions(pos, []) is pos
    assert ht.remap_shift(sh, []) is sh


def test_remap按校正项平移检出点与位移表():
    from bbh.models import BallPos
    pos = [BallPos(t=0.0, x=200.0, y=150.0, area=30.0, fi=7),
           BallPos(t=100.0, x=200.0, y=150.0, area=30.0, fi=8)]
    corr = [(0.0, 5.0, -3.0), (100.0, 5.0, -3.0)]
    out = ht.remap_positions(pos, corr)
    assert [round(p.x, 3) for p in out] == [195.0, 195.0]
    assert [round(p.y, 3) for p in out] == [153.0, 153.0]
    assert out[0].area == 30.0 and out[0].fi == 7      # 其余字段原样
    # 网区 ROI 用 shift 搬家，必须同步加上校正项，否则测错区域
    assert ht.remap_shift([(0.0, 10.0, 4.0)], corr) == [(0.0, 15.0, 1.0)]


def test_开关关闭时整条链不生效(monkeypatch):
    from bbh import pipeline
    from bbh.models import BallPos, Calibration
    monkeypatch.setenv("BBH_HOOP", "0")
    calib = Calibration(hoop_rect=tuple(RECT), frame_size=(640, 360),
                        hsv_low=(0, 0, 0), hsv_high=(180, 255, 255))
    pos = [BallPos(t=0.0, x=200.0, y=150.0, area=30.0, fi=1)]
    hoop = [[0.0, 146.0, 106.0, 44.0, 15.0, 0.7],
            [200.0, 146.0, 106.0, 44.0, 15.0, 0.7],
            [400.0, 146.0, 106.0, 44.0, 15.0, 0.7]]
    got_p, got_s, notes = pipeline._hoop_apply(pos, [(0.0, 0.0, 0.0)], hoop,
                                               calib, REF)
    assert got_p is pos and notes == []


# --------------------------------------------------------- 采样/搬点开关分离
def _hoop_block():
    """3 个采样点、筐心整体右移 20px（真实台阶，不会被中值滤掉）。"""
    return [[0.0, 126.0, 106.0, 44.0, 15.0, 0.7],
            [200.0, 146.0, 106.0, 44.0, 15.0, 0.7],
            [400.0, 146.0, 106.0, 44.0, 15.0, 0.7]]


def _zoom_hoop():
    """5 个采样点，其中 1 帧尺寸明显变大（62x21 vs 44x15）→ 触发变焦判定。"""
    return [[0.0, 126.0, 106.0, 44.0, 15.0, 0.7],
            [100.0, 126.0, 106.0, 44.0, 15.0, 0.7],
            [200.0, 126.0, 106.0, 44.0, 15.0, 0.7],
            [300.0, 130.0, 106.0, 62.0, 21.0, 0.7],
            [400.0, 126.0, 106.0, 44.0, 15.0, 0.7]]


def _calib():
    from bbh.models import Calibration
    return Calibration(hoop_rect=tuple(RECT), frame_size=(640, 360),
                       hsv_low=(0, 0, 0), hsv_high=(180, 255, 255))


def test_搬点默认关闭时只算不搬(monkeypatch):
    """v22 默认：搬点关 ⇒ positions/shift 原样返回（同一对象）、也不出提示。

    净亏实测见 hoop_track.HOOP_SHIFT_ENABLE 注释；桥接不吃这个开关。
    """
    from bbh import pipeline
    from bbh.models import BallPos
    monkeypatch.delenv("BBH_HOOP", raising=False)
    monkeypatch.delenv("BBH_HOOP_SHIFT", raising=False)
    pos = [BallPos(t=0.0, x=100.0, y=100.0, area=30.0, fi=1)]
    sh = [(0.0, 0.0, 0.0)]
    got_p, got_s, notes = pipeline._hoop_apply(pos, sh, _hoop_block(),
                                               _calib(), REF)
    assert got_p is pos and got_s is sh      # 零拷贝，逐条等价 v20
    assert notes == []


def test_搬点开关显式打开时生效(monkeypatch):
    from bbh import pipeline
    from bbh.models import BallPos
    monkeypatch.delenv("BBH_HOOP", raising=False)
    monkeypatch.setenv("BBH_HOOP_SHIFT", "1")
    # 台阶在 t=200 之后（3 点中值对 [0,20,20] 只在台阶处换值），故取 t=300
    pos = [BallPos(t=300.0, x=100.0, y=100.0, area=30.0, fi=1)]
    got_p, got_s, _ = pipeline._hoop_apply(pos, [(300.0, 0.0, 0.0)],
                                           _hoop_block(), _calib(), REF)
    assert got_p is not pos
    assert got_p[0].x == 80.0                # 筐右移 20px → 球点左移 20px
    assert got_s[0][1] == 20.0               # 网区 ROI 同步跟上


def test_搬点关闭时锚点类提示一律不出(monkeypatch):
    """搬点关闭 ⇒ 变焦/异常/无参考帧三条提示一律不出。

    v22 初版曾想「提示与搬点解耦」（不搬点但仍提醒变焦），自查后推翻：
    三条文案写的是「位置按锚点跟了」「本次未采用锚点校正」，搬点关闭时是
    假话或纯噪音；HOOP_ABNORMAL_NOTE 还会反指「请检查标定是否标错筐」，把
    用户引去改一份本来正确的标定。既然校正项不参与判定，就一条都不说。
    """
    from bbh import pipeline
    from bbh.models import BallPos
    monkeypatch.delenv("BBH_HOOP", raising=False)
    monkeypatch.delenv("BBH_HOOP_SHIFT", raising=False)
    pos = [BallPos(t=0.0, x=100.0, y=100.0, area=30.0, fi=1)]
    got_p, _, notes = pipeline._hoop_apply(pos, [(0.0, 0.0, 0.0)], _zoom_hoop(),
                                           _calib(), REF)
    assert got_p is pos                      # 不搬点
    assert notes == []                       # 也不出会误导的文案


def test_搬点打开时变焦提示照出(monkeypatch):
    """显式开搬点（实验用）时诊断提示恢复——那时「位置按锚点跟了」才是真的。"""
    from bbh import pipeline
    from bbh.models import BallPos
    monkeypatch.delenv("BBH_HOOP", raising=False)
    monkeypatch.setenv("BBH_HOOP_SHIFT", "1")
    pos = [BallPos(t=0.0, x=100.0, y=100.0, area=30.0, fi=1)]
    got_p, _, notes = pipeline._hoop_apply(pos, [(0.0, 0.0, 0.0)], _zoom_hoop(),
                                           _calib(), REF)
    assert got_p is not pos                          # 搬点
    assert notes and "变焦" in notes[0]               # 且提示
