# -*- coding: utf-8 -*-
"""net_motion（v12 篮网信号后置 pass + v14 局部性门）：静止/摆动分离度、
降级决策、全局噪声伪装识别、sidecar 缓存命中、fail-open、缓存指纹作废、
ROI 贴边回拉、shift 插值与 MotionTracker 一致。合成视频见 conftest
（真进球带网摆动画段）。"""
import json
import os

import cv2
import numpy as np
import pytest

from bbh.models import Calibration, GoalEvent
from bbh.motion import MotionTracker, interp_shift
from bbh.net_motion import (NET_FILE, NET_STATIC_TH, _rois,
                            measure_net_signals, net_evidence_pass)

CROSS_T = 75 / 30.0     # 合成视频球过筐时刻 ≈2.5s（网摆段自 +0.45s 起）
STILL_T = 0.5           # 全静止时刻（球未出现、无网摆）
FLICKER_T = 4.0         # 全画面闪烁段时刻（网静止、无球，整体亮暗跳变）


def test_静止时刻网信号低于阈值_摆动时刻高于阈值(sample_video, sample_calibration):
    sig = measure_net_signals(sample_video, sample_calibration, [],
                              [f"{STILL_T:.2f}", f"{CROSS_T:.2f}"])
    assert sig[f"{STILL_T:.2f}"]["post"] < NET_STATIC_TH
    assert sig[f"{CROSS_T:.2f}"]["post"] > NET_STATIC_TH
    assert sig[f"{CROSS_T:.2f}"]["local"] is True   # 网摆帧对照区安静=局部


def _make_flicker_video(path):
    """全画面亮度逐帧跳变（模拟镜头亚阈值抖动/编码噪声抬升全帧差分）、
    网静止、无球 → 网区 post≥TH 但与对照区**同对等亮**（非局部网动）。"""
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 30, (640, 360))
    for i in range(180):
        base = 40 + (12 if i % 2 == 0 else -12)
        img = np.full((360, 640, 3), (base, base + 4, base + 10), np.uint8)
        cv2.line(img, (300, 154), (380, 154), (180, 180, 180), 3)
        cv2.line(img, (330, 158), (330, 216), (235, 235, 235), 2)   # 静止网
        cv2.line(img, (352, 158), (352, 216), (235, 235, 235), 2)
        vw.write(img)
    vw.release()
    return path


def test_全局闪烁伪装网动_非局部扣026后仍high(tmp_path, sample_calibration):
    vid = _make_flicker_video(str(tmp_path / "flicker.mp4"))
    # v19：网分加重（−0.10→−0.26）后几何 0.80 会掉到 0.54（review），
    # 这条用例保护的是「非局部=证据被污染≠网没动，不该一票降档」这个语义，
    # 所以把几何分抬到 0.95 让扣完仍站在 high 线上（0.95−0.26=0.69）。
    evs = [GoalEvent(t=FLICKER_T, confidence=0.95, status="high")]
    out = net_evidence_pass(vid, str(tmp_path), sample_calibration, [], evs)
    assert out[0].net_post >= NET_STATIC_TH      # 网区帧差确实被抬亮
    assert out[0].net_local is False             # 但同帧对照区等亮=非局部
    assert out[0].confidence == 0.69             # 0.95 − 0.26（v19 网分）
    assert out[0].status == "high"               # 0.69 ≥ 0.60 → 非局部不必然降档


def test_全行测网_网分回写_非硬门行重分箱(sample_video, sample_calibration,
                                        tmp_path):
    # v19：静止 −0.30 → 几何 high 0.80 落 0.50 review（刚好压线不落 low）；
    # 几何 review 0.60 落 0.30 low（review→low 迁移是报告要盯的口径移动）；
    # 网摆局部分 0，conf/status 原样。测量对象=全部行（不再只测 high）。
    evs = [GoalEvent(t=STILL_T, confidence=0.80, status="high"),
           GoalEvent(t=CROSS_T, confidence=0.80, status="high"),
           GoalEvent(t=STILL_T, confidence=0.60, status="review")]
    out = net_evidence_pass(sample_video, str(tmp_path), sample_calibration,
                            [], evs)
    assert out[0].status == "review" and out[0].confidence == 0.50
    assert out[0].net_post < NET_STATIC_TH
    assert out[0].net_ctrl is not None         # 对照区同步记录（诊断）
    assert out[1].status == "high" and out[1].confidence == 0.80
    assert out[1].net_post > NET_STATIC_TH
    assert out[2].status == "low" and out[2].confidence == 0.30
    assert os.path.exists(os.path.join(str(tmp_path), NET_FILE))


def test_sidecar缓存命中不再开视频(sample_video, sample_calibration,
                                    tmp_path, monkeypatch):
    evs = [GoalEvent(t=CROSS_T, confidence=0.80, status="high")]
    net_evidence_pass(sample_video, str(tmp_path), sample_calibration, [], evs)
    p = os.path.join(str(tmp_path), NET_FILE)
    cached = json.load(open(p, encoding="utf-8"))

    def boom(*a, **k):
        raise AssertionError("缓存命中时不应重测")
    monkeypatch.setattr("bbh.net_motion.measure_net_signals", boom)
    evs2 = [GoalEvent(t=CROSS_T, confidence=0.80, status="high")]
    net_evidence_pass(sample_video, str(tmp_path), sample_calibration, [], evs2)
    assert evs2[0].net_post is not None        # 值来自缓存
    assert json.load(open(p, encoding="utf-8")) == cached


def test_测量失败fail_open不降级不写sidecar(sample_calibration, tmp_path):
    evs = [GoalEvent(t=CROSS_T, confidence=0.80, status="high")]
    out = net_evidence_pass(r"Z:\不存在\nope.mp4", str(tmp_path),
                            sample_calibration, [], evs)
    assert out[0].status == "high"             # 证据缺失不当否证
    assert out[0].net_post is None
    assert out[0].confidence == 0.80           # fail-open：网分 0，conf 不动
    assert not os.path.exists(os.path.join(str(tmp_path), NET_FILE))


def test_重标筐使缓存作废重测(sample_video, sample_calibration, tmp_path):
    evs = [GoalEvent(t=CROSS_T, confidence=0.80, status="high")]
    net_evidence_pass(sample_video, str(tmp_path), sample_calibration, [], evs)
    other = Calibration(hoop_rect=(302, 140, 80, 40), hsv_low=(0, 80, 120),
                        hsv_high=(25, 255, 255), frame_size=(640, 360))
    evs2 = [GoalEvent(t=CROSS_T, confidence=0.80, status="high")]
    net_evidence_pass(sample_video, str(tmp_path), other, [], evs2)
    assert evs2[0].net_post is not None        # 篮筐变了 → 旧缓存不认 → 重测
    doc = json.load(
        open(os.path.join(str(tmp_path), NET_FILE), encoding="utf-8"))
    hoops = [e["identity"]["hoop"] for e in doc["entries"]]
    assert [302, 140, 80, 40] in hoops
    assert [300, 140, 80, 40] in hoops         # 旧身份信号保留（多身份共存）


def test_多筐身份共存互不清空(sample_video, sample_calibration, tmp_path,
                                monkeypatch):
    """A2 多段标定：段 A/B 各一筐几何各一身份，sidecar 各存一份互不清空
    （单身份格式下跑一段抹一段，判定器重放秒级路径退化成全量重测）。"""
    other = Calibration(hoop_rect=(200, 140, 80, 40), hsv_low=(0, 80, 120),
                        hsv_high=(25, 255, 255), frame_size=(640, 360))
    for c in (sample_calibration, other):
        net_evidence_pass(sample_video, str(tmp_path), c, [],
                          [GoalEvent(t=CROSS_T, confidence=0.80, status="high")])
    # 再跑 A：命中缓存不重测，且 B 的 entry 原样保留
    p = os.path.join(str(tmp_path), NET_FILE)
    before = json.load(open(p, encoding="utf-8"))

    def boom(*a, **k):
        raise AssertionError("缓存命中时不应重测")
    monkeypatch.setattr("bbh.net_motion.measure_net_signals", boom)
    evs = [GoalEvent(t=CROSS_T, confidence=0.80, status="high")]
    net_evidence_pass(sample_video, str(tmp_path), sample_calibration, [], evs)
    assert evs[0].net_post is not None
    assert json.load(open(p, encoding="utf-8")) == before


def test_ROI任意位移不出画面且不退化为空(sample_calibration):
    for dx, dy in ((0, 0), (-500, 300), (1000, -500)):
        (nx0, ny0, nx1, ny1), _ = _rois(sample_calibration, dx, dy)
        assert 0 <= nx0 < nx1 <= 640 and 0 <= ny0 < ny1 <= 360


def test_interp_shift与MotionTracker一致():
    samples = [(1.0, 3.0, -2.0), (5.0, 7.0, 4.0)]
    tracker = MotionTracker(np.zeros((360, 640, 3), np.uint8), samples=samples)
    for t in (-1.0, 0.0, 1.0, 2.4, 5.0, 9.9):
        assert interp_shift(samples, t) == tracker.shift(t)
    assert interp_shift([], 3.0) == (0.0, 0.0)   # 无采样表=全程零位移


def test_网分压线_080减030恰落050review(sample_video, sample_calibration,
                                       tmp_path):
    # 0.80−0.30 = 0.50（精确）：静止恰把 high 压到 review 而非 low，
    # 是对齐网门档位的压线锚点，必须钉死。
    # 注：v18 的原锚点 0.70−0.20 = 0.4999…（浮点陷阱、靠 round 救回）在
    # v19 常量下已复现不出——几何 0.01 网格上 −0.30 / −0.26 都落在精确值；
    # 「先 round 到 2 位再分箱」仍由 net_motion.py 的 round(..., 2) 保证。
    evs = [GoalEvent(t=STILL_T, confidence=0.80, status="high")]
    out = net_evidence_pass(sample_video, str(tmp_path), sample_calibration,
                            [], evs)
    assert out[0].confidence == 0.50
    assert out[0].status == "review"           # 0.50 ≥ MIN_CONF


def test_硬门行测网只记录_档位不动分值照扣(sample_video, sample_calibration,
                                          tmp_path):
    # 用户裁定「所有球都走篮网判断，不然分值不准」：硬门行 conf 也要完整
    # （含网分）；但档位由硬门裁决，阈值分箱不碰（spec §3.3）
    evs = [GoalEvent(t=STILL_T, confidence=0.80, status="low",
                     low_reason="证据点太少（置信度不足 0.50）"),
           GoalEvent(t=0.7, confidence=0.80, status="review",
                     review_reason="entity"),
           GoalEvent(t=0.9, confidence=0.80, status="review", pop_back=0.42)]
    out = net_evidence_pass(sample_video, str(tmp_path), sample_calibration,
                            [], evs)
    assert [e.status for e in out] == ["low", "review", "review"]
    assert [e.confidence for e in out] == [0.50, 0.50, 0.50]
    assert all(e.net_post is not None and e.net_post < NET_STATIC_TH
               for e in out)


def test_网分常量与打分函数的取值表():
    from bbh.net_motion import (NET_SCORE_GLOBAL, NET_SCORE_STATIC,
                                NET_SCORE_SWING, net_score)
    assert (NET_SCORE_STATIC, NET_SCORE_GLOBAL, NET_SCORE_SWING) \
        == (-0.30, -0.26, 0.0)
    assert net_score(2.0, False) == -0.30      # 静止（local 值无关）
    assert net_score(8.0, False) == -0.26      # 亮但非局部
    assert net_score(8.0, True) == 0.0         # 网动且局部：不许给正分（铁结论）
    assert net_score(None, None) == 0.0        # fail-open：证据缺失不当否证


def test_net_term_B2注入行恒0_其余行同net_score():
    """审阅修复（Important 1）：B2 注入行 conf 不含网分（spec §3.5——注入
    时刻是链内插值，实测窗可能错过摆动相位），依据卡算式的网分项也必须按
    0，否则九场 240 条 b2 行中 132 条实测为静止的行在 UI 上算式对不上账
    并误挂 legacy「重跑后一致」标注（重跑结果相同——分按 0 是设计）。"""
    from bbh.net_motion import net_term
    b2 = GoalEvent(t=1.0, confidence=0.50, status="review",
                   review_reason="b2", net_post=2.0, net_local=True)
    assert net_term(b2) == 0.0                     # 实测静止也不进算式
    normal = GoalEvent(t=1.0, confidence=0.30, status="review",
                       cross_off=0.0, net_post=2.0, net_local=True)
    assert net_term(normal) == -0.30               # 同实测、非注入行照扣（v19 静止档）
    assert net_term(GoalEvent(t=1.0, confidence=0.8,
                              status="high")) == 0.0   # 未测 fail-open


def test_测网估时单位是秒(sample_video, sample_calibration, tmp_path):
    """审阅修复：估时曾按「行数×0.7 分钟」报，实际 ~0.7 秒/行（本模块
    docstring 自证）——差 60 倍，v18 全行测网后 300 行新场会显示「约 210
    分钟」吓退用户（实际 ~4 分钟）。"""
    msgs = []
    net_evidence_pass(sample_video, str(tmp_path), sample_calibration, [],
                      [GoalEvent(t=CROSS_T, confidence=0.80, status="high")],
                      stage_cb=msgs.append)
    est = [m for m in msgs if m and "篮网运动信号" in m]
    assert est and "秒" in est[0] and "分钟" not in est[0]
