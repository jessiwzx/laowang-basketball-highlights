"""出手人识别 Phase 1 测试：缓存层 / 轨迹回追 / 全帧回扫 / 射手裁剪 / 编排。"""
import json
import subprocess
import sys
import os

import cv2
import numpy as np
import pytest

import bbh.app as app_module
from bbh import shooter
from bbh.models import BallPos, Calibration, time_key


def test_time_key与_key_safe():
    assert time_key(83.7) == "01:23.7"          # 与 app._fmt_t 同口径
    assert shooter._key_safe("01:23.7") == "01-23.7"   # 冒号 Windows 文件名非法


def test_save_load_roundtrip(tmp_path, sample_video, sample_calibration):
    traces = {"00:02.5": {"status": "high", "release_t": 2.3,
                          "release_xy": [340, 200], "track_n": 9,
                          "track_span": 1.2, "thumbs": ["shooters/a.jpg"]}}
    shooter.save_shooters(str(tmp_path), str(sample_video), sample_calibration,
                          traces)
    assert shooter.load_shooters(str(tmp_path))["traces"] == traces


def test_load_shooters缺失与损坏返回None(tmp_path):
    assert shooter.load_shooters(str(tmp_path)) is None
    (tmp_path / "shooters.json").write_text("{半截", encoding="utf-8")
    assert shooter.load_shooters(str(tmp_path)) is None


def test_shooters_valid身份校验(tmp_path, sample_video, sample_calibration,
                               monkeypatch):
    work = str(tmp_path)
    shooter.save_shooters(work, "v.mp4", sample_calibration, {})
    data = shooter.load_shooters(work)
    assert shooter.shooters_valid(data, "v.mp4", sample_calibration)
    assert not shooter.shooters_valid(data, "别的.mp4", sample_calibration)
    other = Calibration(hoop_rect=(1, 1, 2, 2), hsv_low=(0, 0, 0),
                        hsv_high=(1, 1, 1), frame_size=(3, 3))
    assert not shooter.shooters_valid(data, "v.mp4", other)
    monkeypatch.setattr(shooter, "SHOOTER_VERSION", 99)   # 版本不符作废
    assert not shooter.shooters_valid(data, "v.mp4", sample_calibration)


# ---------- person 框裁剪（2026-10-15 spike 36 球验证后收编） ----------
def test_crop走person框_注入假检测(sample_video, sample_calibration, tmp_path,
                                    monkeypatch):
    """检到人 → 裁剪几何换 person 框 + 45% 边距（不再出手点定宽居中），
    tr.person 记归属档（ball/in/expand/near）；_a/_b 同框。"""
    from bbh import person
    t2_4 = 2.4                                # 帧≈72，球在 (340,148)
    fake = [(300.0, 100.0, 380.0, 260.0, 0.9)]   # 含球含出手点
    monkeypatch.setattr(person, "NAME", "on")
    monkeypatch.setattr(person, "_detect_raw", lambda fr: fake)
    tr = shooter.TraceResult(status="high", release_t=t2_4,
                             release_xy=(340, 120))
    rel = shooter.crop_shooter(str(sample_video), sample_calibration, tr,
                               str(tmp_path), "00:02.4")
    assert rel == ["shooters/00-02.4_a.jpg", "shooters/00-02.4_b.jpg"]
    assert tr.person == "ball"                # 球锚命中含球框
    x0, y0, x1, y1 = person.crop_rect(fake[0], 640, 360)
    a = _read_img(tmp_path / "shooters" / "00-02.4_a.jpg")
    b = _read_img(tmp_path / "shooters" / "00-02.4_b.jpg")
    assert (a.shape[1], a.shape[0]) == (x1 - x0, y1 - y0)   # person 框几何
    assert b.shape == a.shape                 # _b 同框
    # 出手点红十字仍在（点在框内时画）
    cx, cy = 340 - x0, 120 - y0
    near = a[cy - 2:cy + 3, cx - 2:cx + 3]
    assert ((near[:, :, 0] < 80) & (near[:, :, 2] > 180)).any()


def test_trace_video旧档只重裁不重扫(sample_video, sample_calibration,
                                     tmp_path, monkeypatch):
    """person 参数版本不匹配（旧档无 person_ver）→ 只重裁缩略图、写
    person 字段；溯源 traces 不作废（scan_release_window 不得被调用——
    对照 SHOOTER_VERSION 作废=全量重扫）。"""
    from types import SimpleNamespace
    from bbh import person
    work = str(tmp_path)
    (tmp_path / "shooters").mkdir()
    (tmp_path / "shooters" / "00-02.4_a.jpg").write_bytes(b"old")
    shooter.save_shooters(work, str(sample_video), sample_calibration, {
        "00:02.4": {"status": "review", "release_t": 2.4,
                    "release_xy": [340, 120],
                    "thumbs": ["shooters/00-02.4_a.jpg"], "spot": "ok"}})

    def boom(*a, **k):
        raise AssertionError("person 升级不得触发溯源重扫")

    monkeypatch.setattr(shooter, "scan_release_window", boom)
    prog, summary = _drain(shooter.trace_video(
        str(sample_video), sample_calibration, work, [SimpleNamespace(t=2.4)]))
    tr = shooter.load_shooters(work)["traces"]["00:02.4"]
    assert tr["person_ver"] == person.PERSON_VERSION
    assert tr["person"] == "none"             # BBH_PERSON=off → 定宽裁剪兜底
    assert (tmp_path / "shooters" / "00-02.4_a.jpg").read_bytes() != b"old"
    assert any("重裁" in p for p in prog)
    assert "未重扫" in summary


def test_person重裁增量_已升级球不动(sample_video, sample_calibration,
                                     tmp_path):
    """中断重跑只补缺：已带当前 person_ver 的球不重裁（文件字节不动）。"""
    from types import SimpleNamespace
    from bbh import person
    work = str(tmp_path)
    (tmp_path / "shooters").mkdir()
    (tmp_path / "shooters" / "00-02.4_a.jpg").write_bytes(b"old")
    (tmp_path / "shooters" / "00-05.0_a.jpg").write_bytes(b"done")
    shooter.save_shooters(work, str(sample_video), sample_calibration, {
        "00:02.4": {"status": "review", "release_t": 2.4,
                    "release_xy": [340, 120],
                    "thumbs": ["shooters/00-02.4_a.jpg"], "spot": "ok"},
        "00:05.0": {"status": "review", "release_t": 5.0,
                    "release_xy": [340, 120],
                    "thumbs": ["shooters/00-05.0_a.jpg"], "spot": "ok",
                    "person": "none",
                    "person_ver": person.PERSON_VERSION}})
    _drain(shooter.trace_video(
        str(sample_video), sample_calibration, work,
        [SimpleNamespace(t=2.4), SimpleNamespace(t=5.0)]))
    assert (tmp_path / "shooters" / "00-05.0_a.jpg").read_bytes() == b"done"
    assert (tmp_path / "shooters" / "00-02.4_a.jpg").read_bytes() != b"old"


# ---------- 轨迹回追（合成检出流，纯函数） ----------
HOOP_W = 80.0                # 与 conftest 筐口宽一致，速度以筐宽为单位
HOOP_C = (1130.0, 110.0)     # 合成场景筐心：飞行段终点 (2.9,1130,110)


def _pt(t, x, y):
    return BallPos(t=t, x=x, y=y, area=100, fi=0)


def _pts_hold_then_flight():
    """持球慢移段（0.5~0.9s，≤1 筐宽/s）→ 飞行段（1.0~2.9s，~5 筐宽/s）。"""
    pts = [(_pt(0.5 + 0.1 * j, 340 + 6 * j, 300)) for j in range(5)]
    pts += [(_pt(1.0 + 0.1 * j, 370 + 40 * j, 300 - 10 * j))
            for j in range(20)]
    return pts


def test_回追到跃变点判high且出手在跃变处():
    tr = shooter.trace_release(_pts_hold_then_flight(), 3.0, HOOP_W, HOOP_C)
    assert tr.status == "high"
    assert abs(tr.release_t - 1.0) < 1e-6      # 出手=第一段快速度的起点
    assert tr.track_n == 25
    assert tr.track_span == pytest.approx(2.4, abs=0.01)


def test_全程飞行未追到持球段判review():
    pts = [_pt(1.0 + 0.1 * j, 370 + 40 * j, 300) for j in range(20)]
    tr = shooter.trace_release(pts, 3.0, HOOP_W, HOOP_C)
    assert tr.status == "review"               # 链首就在飞：出手早于链首/窗口
    assert abs(tr.release_t - 1.0) < 1e-6      # 链首当出手估计


def test_全慢轨迹无跃变():
    """整段 1.6 筐宽/s 慢移 25 点（<HOLD_SPEED_RATIO）：链完整但全程
    持球速度、无跃变 → found=False、判 review、release 取链首估计。
    步进 13px（>STATIC_R_PX 12px 静点邻域）——实测胸口携带步进 >12px；
    6px 连续小步进属静物特征（横幅/场下球），v3 起被 _kill_statics 剔除。"""
    pts = [_pt(0.5 + 0.1 * j, 340 + 13 * j, 300) for j in range(25)]
    tr = shooter.trace_release(pts, 3.0, HOOP_W, HOOP_C)
    assert tr.found is False
    assert tr.status == "review"
    assert abs(tr.release_t - 0.5) < 1e-6      # 链首当出手估计


def test_短缺口桥接不断链():
    pts = [p for p in _pts_hold_then_flight() if not (1.5 < p.t < 1.8)]
    tr = shooter.trace_release(pts, 3.0, HOOP_W, HOOP_C)  # 缺口 ≤0.3s 桥接
    assert tr.status == "high"
    assert tr.track_n == 23                     # 25-2，缺的 2 点没进链


def test_断链超桥接上限则截断():
    pts = [p for p in _pts_hold_then_flight() if not (1.4 <= p.t < 1.9)]
    tr = shooter.trace_release(pts, 3.0, HOOP_W, HOOP_C)  # 缺口 0.5s>0.3s
    assert tr.status == "review"
    assert tr.track_n == 11                     # 只剩 1.9~2.9 的后段


def test_瞬移噪声点被跳过():
    pts = sorted(_pts_hold_then_flight() + [_pt(1.55, 3000, 3000)],
                 key=lambda p: p.t)
    tr = shooter.trace_release(pts, 3.0, HOOP_W, HOOP_C)
    assert tr.status == "high"
    assert tr.track_n == 25                     # 噪声点没进链


def test_无可用轨迹判low():
    assert shooter.trace_release([], 3.0, HOOP_W, HOOP_C).status == "low"
    assert shooter.trace_release([_pt(2.9, 340, 200)], 3.0,
                                 HOOP_W, HOOP_C).status == "low"


# ---------- v2 溯源：锚点/建链/修剪/爬升出手（2026-08-25 验收返修） ----------

def _pts_with_static_object():
    """场边静物劫持场景（01:23.7/25:08.1 根因）：左侧每个采样帧都有静止
    橙块 (100,300)，真球 2.0s 起才起飞并在窗口末尾入筐——旧锚点取
    pts[-1]=静物，链被劫持。"""
    real = _pts_hold_then_flight()              # 0.5~2.9，飞行段末端即筐心
    static = [_pt(round(0.5 + 0.1 * j, 3), 100, 300) for j in range(25)]
    return sorted(real + static, key=lambda p: p.t)


def test_锚点取筐心最近点不被场边静物劫持():
    tr = shooter.trace_release(_pts_with_static_object(), 3.0, HOOP_W, HOOP_C)
    assert tr.status == "high"
    assert abs(tr.release_t - 1.0) < 1e-6      # 出手仍在真球持球→飞行边界
    assert tr.release_xy[0] > 300               # 不落在静物 (100,300) 上
    assert tr.track_n == 25                     # 静物点全被排除在链外


def _pts_putback():
    """补篮场景（07:09.3 根因）：持球1→投篮1未进(到筐)→反弹出→持球2
    →补篮飞行→筐圈慢滚→进球。出手应取补篮起点，不是第一投。"""
    pts = [_pt(0.5 + 0.1 * j, 700 + 6 * j, 640) for j in range(4)]   # 持球1
    pts += [_pt(1.0 + 0.1 * j, 740 + 39 * j, 630 - 52 * j)           # 投篮1
            for j in range(9)]                                        # →(1092,164)近筐
    for j in range(8):                                                # 反弹出(减速)
        pts.append(_pt(1.9 + 0.15 * j, 1092 - 37 * j, 164 + 55 * j))
    pts += [_pt(3.1 + 0.1 * j, 829 - 5 * j, 590) for j in range(5)]  # 持球2
    pts += [_pt(3.6 + 0.1 * j, 804 + 41 * j, 585 - 55 * j)           # 补篮飞行
            for j in range(8)]
    pts += [_pt(4.4 + 0.12 * j, 1132 - 3 * (j % 2), 145 + 6 * j)     # 筐圈慢滚
            for j in range(5)]
    return pts


def test_补篮链取最后飞行段出手():
    tr = shooter.trace_release(_pts_putback(), 5.0, HOOP_W, HOOP_C)
    assert tr.status == "high"
    assert 3.5 <= tr.release_t <= 3.7           # 补篮飞行起点，不是 ~1.0 的第一投
    assert 780 < tr.release_xy[0] < 830         # 补篮者位置（筐左 ~4 筐宽）
    assert tr.found is True


def test_高弧线弧顶慢速点不劫持出手():
    """19:49.1 根因：弧顶平缓段速度跌破 HOLD——出手应取爬升起点
    （右底角），不是弧顶。"""
    pts = [_pt(1.0 + 0.1 * j, 1195 - 15 * j, 859 - 21 * j) for j in range(18)]
    pts += [_pt(2.8 + 0.1 * j, 1140 + 5 * j, 480 - 30 * j) for j in range(6)]
    pts += [_pt(3.4 + 0.1 * j, 1160 - 4 * j, 300 + 15 * j) for j in range(9)]
    pts += [_pt(4.3 + 0.1 * j, 1136 - 4 * j, 435 + 2.5 * j) for j in range(5)]
    tr = shooter.trace_release(pts, 4.8, HOOP_W, HOOP_C)
    assert tr.release_t < 1.4                   # 爬升起点附近，远离弧顶 (~3.4s)
    assert tr.release_xy[1] > 700               # 接近手高，不是弧顶 y~300


def test_折返垃圾点被单调修剪剔除():
    """19:49.1 根因（另一半）：链中混入比后继更靠近筐的折返点——
    距筐单调性应把它剔掉，出手仍取完整爬升起点。"""
    pts = [_pt(1.0 + 0.1 * j, 1195 - 15 * j, 859 - 30 * j) for j in range(11)]
    pts.append(_pt(2.1, 1120, 470))             # 折返垃圾：离筐比前后都近
    pts += [_pt(2.3 + 0.1 * j, 1030 - 8 * j, 540 - 40 * j) for j in range(7)]
    pts += [_pt(3.0 + 0.1 * j, 990 + 17 * j, 290 - 22 * j) for j in range(9)]
    tr = shooter.trace_release(pts, 4.2, HOOP_W, HOOP_C)
    assert tr.release_t < 1.4                   # 爬升起点（链首），不是弧顶
    assert tr.release_xy[1] > 700               # 接近手高


def test_传球接球后出手停在接球点():
    """02:30.7/02:22.1 根因：横传（抛物线）→接球人停顿→起跳出手——
    出手应停在最靠近出手点的接球/起手处，不是传球人起点。"""
    pts = [_pt(0.6 + 0.1 * j, 728 - 26 * j, 691 + 3 * j) for j in range(9)]  # 横传
    pts += [_pt(1.55 + 0.1 * j, 492 + 2 * j, 700 - 11 * j)          # 接球停顿
            for j in range(9)]
    pts += [_pt(2.45 + 0.1 * j, 520 + 40 * j, 610 - 40 * j)         # 出手飞行
            for j in range(12)]
    pts += [_pt(3.65 + 0.1 * j, 1000 + 20 * j, 170 - 26 * j)        # 续升近筐
            for j in range(5)]
    pts += [_pt(4.15 + 0.1 * j, 1088 + 10 * j, 74 + 9 * j)          # 入筐段
            for j in range(5)]
    tr = shooter.trace_release(pts, 4.6, HOOP_W, HOOP_C)
    assert tr.release_t > 1.4                   # 不是传球起点 ~0.6
    assert tr.release_xy[0] > 450               # 出手人在接球点右侧


# ---------- 全帧回扫（合成视频实测） ----------
def test_scan回扫窗口检出下落球(sample_video, sample_calibration):
    # conftest：球在第 60~90 帧（t≈2.0~3.0）从 (340,100) 直落 (340,220)
    pts, size = shooter.scan_release_window(str(sample_video),
                                            sample_calibration,
                                            t_goal=2.8, window_s=2.0)
    assert size == (640, 360)
    assert pts, "球下落段应被检出"
    assert all(p.t <= 2.8 + 1e-6 for p in pts)     # PTS 钟，不超窗口上界
    assert all(abs(p.x - 340) < 6 for p in pts)    # 直落轨迹 x 恒为 340


def test_scan无球段与窗口截断到片头(sample_video, sample_calibration):
    pts, _ = shooter.scan_release_window(str(sample_video),
                                         sample_calibration,
                                         t_goal=1.5, window_s=1.0)
    assert pts == []                               # t<2.0 无球
    pts2, _ = shooter.scan_release_window(str(sample_video),
                                          sample_calibration,
                                          t_goal=1.0, window_s=6.0)
    assert pts2 == []                              # t0=max(0,-5)=0 不崩


# ---------- 射手裁剪（合成视频实测） ----------
def test_裁剪框给头顶留足余量():
    """RC4（35:51.4/04:42.5 半身）：出手点在髋部、头在其上方 150~250px，
    旧上界只抬 0.3 筐宽(≈15px)切头——出手点应置于框竖直中线。"""
    fw, fh, hw = 1920, 1080, 51                 # 08-22 场实测标定
    rim_y = 471                                 # 08-22 场筐平面（hy+hh*RIM_Y_RATIO）
    x0, y0, x1, y1 = shooter._crop_box(349, 822, fw, fh, hw, rim_y)
    crop_h = shooter.PERSON_H_CM * hw / 45.0 * shooter.CROP_MARGIN
    assert y0 <= 822 - 0.45 * crop_h            # 头顶余量（头 y≈648 必须进框）
    assert y1 >= min(fh, 822 + 0.4 * crop_h)    # 腿脚也在框内
    assert x0 < 349 < x1
    # 贴底角出手也不越界（clamp 生效）
    assert shooter._crop_box(1195, 859, fw, fh, hw, rim_y)[3] <= fh


def test_裁剪框近机位深度自适应():
    """类 1 人像不全（03:38.8 等 6 球 + 帧验证 52:13.2/40:10.4）：近机位
    （release_y 深）球员像素尺寸远超筐平面推算——深度每深一屏高标尺放大
    DEPTH_SLOPE 倍；筐平面深度（release_y=rim_y）保持原尺寸。"""
    fw, fh, hw, rim_y = 1920, 1080, 51, 471
    base_h = shooter.PERSON_H_CM * hw / 45.0 * shooter.CROP_MARGIN
    # 筐平面出手：不放大
    x0, y0, x1, y1 = shooter._crop_box(500, rim_y, fw, fh, hw, rim_y)
    assert y1 - y0 <= base_h + 2
    # 近机位出手（release_y 深 280px，框完整落画内）：显著放大
    x0, y0, x1, y1 = shooter._crop_box(500, rim_y + 280, fw, fh, hw, rim_y)
    assert y1 - y0 >= base_h * 1.6
    # 上限封顶（贴镜头不 runaway）
    x0, y0, x1, y1 = shooter._crop_box(500, fh, fw, fh, hw, rim_y)
    assert y1 - y0 <= base_h * shooter.DEPTH_MAX_SCALE + 2



# 读回用 np.fromfile+imdecode：本机铁律 cv2.imread 中文路径静默返回 None
# （tmp_path 含中文测试名，2026-08-15 实测）
def _read_img(p):
    return cv2.imdecode(np.fromfile(str(p), np.uint8), cv2.IMREAD_COLOR)


def test_crop两帧落盘且出手点在框内(sample_video, sample_calibration,
                                    tmp_path):
    tr = shooter.TraceResult(status="high", release_t=2.4,
                             release_xy=(340, 120))
    rel = shooter.crop_shooter(str(sample_video), sample_calibration, tr,
                               str(tmp_path), "00:02.4")
    assert rel == ["shooters/00-02.4_a.jpg", "shooters/00-02.4_b.jpg"]
    a = _read_img(tmp_path / "shooters" / "00-02.4_a.jpg")
    b = _read_img(tmp_path / "shooters" / "00-02.4_b.jpg")
    assert a is not None and b is not None
    assert a.shape[0] >= 100 and a.shape[1] >= 100    # 全身量级裁剪框
    # 帧 _a 出手点画红十字（BGR (0,0,255)）：按 _crop_box 同式把
    # release_xy=(340,120) 换算进裁剪坐标系，中心 5x5 邻域应存在红色像素
    # （b<80 且 r>180；背景 (40,44,50) 双通道都不满足，不误判）
    from bbh.detector import RIM_Y_RATIO, _scaled_rect
    hx, hy, hw, hh = _scaled_rect(sample_calibration, 640, 360)
    x0, y0, _, _ = shooter._crop_box(340, 120, 640, 360, hw,
                                     hy + hh * RIM_Y_RATIO)
    cx, cy = 340 - x0, 120 - y0
    near = a[cy - 2:cy + 3, cx - 2:cx + 3]
    assert ((near[:, :, 0] < 80) & (near[:, :, 2] > 180)).any()


def test_crop出手点贴边clamp不越界(sample_video, sample_calibration, tmp_path):
    # y=300 筐下近左缘（(2,300) 会被贴边/A 类门控拦下换全场图，测不到
    # clamp；x=100 越过贴边阈值但裁剪框仍出画面，clamp 才是被测路径）
    tr = shooter.TraceResult(status="review", release_t=2.4, release_xy=(100, 300))
    rel = shooter.crop_shooter(str(sample_video), sample_calibration, tr,
                               str(tmp_path), "00:02.4")
    assert rel, "clamp 后仍应能裁出"
    a = _read_img(tmp_path / "shooters" / "00-02.4_a.jpg")
    assert a is not None and a.shape[0] > 0


def test_crop未溯源不裁(sample_video, sample_calibration, tmp_path):
    tr = shooter.TraceResult(status="low")
    assert shooter.crop_shooter(str(sample_video), sample_calibration, tr,
                                str(tmp_path), "00:02.4") == []


# ---------- A 类修复：出手点筐平面以上=定位失效，全场兜底图 ----------
# 2026-08-29_3 实测 11 球 release_y 高于筐平面（y<262 vs rim 280），缩略图
# 全裁到屋顶桁架/白墙——链追到窗口边界时球还在飞（found=False 拿空中点
# 当出手估计），出手高度物理上不可能高于筐（看台俯视几何），越界即失效。

def test_release_spot_bad物理校验边界(sample_calibration):
    # 640x360，筐(300,140,80,40) → rim_y=154；容差 0.5 筐=40px → y<114 判失效
    assert shooter.release_spot_bad(sample_calibration, (340, 100), 640, 360)
    assert shooter.release_spot_bad(sample_calibration, (340, 39), 640, 360)    # 帧顶
    assert not shooter.release_spot_bad(sample_calibration, (340, 114), 640, 360)  # 恰容差上
    assert not shooter.release_spot_bad(sample_calibration, (340, 154), 640, 360)
    assert not shooter.release_spot_bad(sample_calibration, None, 640, 360)     # 无出手点
    # 标定分辨率≠视频分辨率：折算后同口径（筐放大 2 倍 → 阈值 y<228）
    assert shooter.release_spot_bad(sample_calibration, (680, 200), 1280, 720)
    assert not shooter.release_spot_bad(sample_calibration, (680, 300), 1280, 720)


def test_crop出手点筐上换全场兜底图(sample_video, sample_calibration,
                                    tmp_path):
    """y=100 高于筐平面 → 不裁人像（裁了也是屋顶），换出手时刻全场
    下采样图 + 左上 ⚠ 角标，单张（无 _a/_b、无红十字）。"""
    tr = shooter.TraceResult(status="review", release_t=2.4, release_xy=(340, 100))
    rel = shooter.crop_shooter(str(sample_video), sample_calibration, tr,
                               str(tmp_path), "00:02.4")
    assert rel == ["shooters/00-02.4_full.jpg"]
    img = _read_img(tmp_path / "shooters" / "00-02.4_full.jpg")
    assert img is not None
    # 全场比例（16:9）而非人像小裁框，宽压到 THUMB_FULL_W
    assert abs(img.shape[1] / img.shape[0] - 640 / 360) < 0.01
    assert img.shape[1] <= shooter.THUMB_FULL_W
    # 左上 ⚠ 角标黄色像素（BGR：b 低 g/r 高）
    corner = img[: img.shape[0] // 3, : img.shape[1] // 3]
    assert ((corner[:, :, 0] < 100) & (corner[:, :, 1] > 180)
            & (corner[:, :, 2] > 180)).any()


def test_migrate_spot_bad旧档迁移幂等(sample_video, sample_calibration,
                                      tmp_path):
    """旧缓存（无 spot 字段）：筐上球换全场图+标 bad，筐下球只补 ok；
    幂等——再跑零迁移。SHOOTER_VERSION 不动，旧档靠迁移升级。"""
    work = str(tmp_path)
    (tmp_path / "shooters").mkdir()
    (tmp_path / "shooters" / "00-02.4_a.jpg").write_bytes(b"old")
    shooter.save_shooters(work, str(sample_video), sample_calibration, {
        "00:02.4": {"status": "review", "release_t": 2.4,
                    "release_xy": [340, 100], "track_n": 3, "track_span": 0.6,
                    "thumbs": ["shooters/00-02.4_a.jpg"], "err": None},
        "00:05.0": {"status": "high", "release_t": 5.0,
                    "release_xy": [340, 200], "track_n": 8, "track_span": 1.0,
                    "thumbs": ["shooters/00-05.0_a.jpg"], "err": None}})
    assert shooter.migrate_spot_bad(str(sample_video), sample_calibration,
                                    work) == 1
    data = shooter.load_shooters(work)["traces"]
    assert data["00:02.4"]["spot"] == "bad"
    assert data["00:02.4"]["thumbs"] == ["shooters/00-02.4_full.jpg"]
    assert (tmp_path / "shooters" / "00-02.4_full.jpg").exists()
    assert data["00:05.0"]["spot"] == "ok"                      # 筐下不动
    assert data["00:05.0"]["thumbs"] == ["shooters/00-05.0_a.jpg"]
    assert shooter.migrate_spot_bad(str(sample_video), sample_calibration,
                                    work) == 0                  # 幂等
    data2 = shooter.load_shooters(work)["traces"]
    assert data2["00:02.4"]["thumbs"] == ["shooters/00-02.4_full.jpg"]


# ---------- 贴边门控（2026-09-06 用户四类缺陷第 2 类）：release 贴画面边
# = 链从窗口边缘才开始/球刚入画，出手点是截断估计——crop 锚在它上面只会
# 拍到裤腿鞋+围栏（08-29_3 实测 04:46.4 x=14）。与 A 类同走 spot=bad 全场
# 兜底。阈值 30px（720p 标定，按 fh 缩放）：08-29_3 命中 3 键全用户报的
# （边距 12.5/14/28.5），实测好图 38.5/42.5 不进=零误伤。

def test_release_spot_bad贴边门控(sample_calibration):
    # 640x360 → margin = 30*360/720 = 15px；y 合法（200>筐平面）只看边距
    assert shooter.release_spot_bad(sample_calibration, (5, 200), 640, 360)
    assert shooter.release_spot_bad(sample_calibration, (630, 200), 640, 360)   # 右
    assert shooter.release_spot_bad(sample_calibration, (340, 358), 640, 360)   # 底
    assert not shooter.release_spot_bad(sample_calibration, (18, 200), 640, 360)  # 边距外
    assert not shooter.release_spot_bad(sample_calibration, (340, 154), 640, 360)  # 居中合法
    # 1280x720（用户真源分辨率）→ margin=30：28.5 进（90:09.8）、38.5 不进
    assert shooter.release_spot_bad(sample_calibration, (680, 700), 1280, 720)
    assert not shooter.release_spot_bad(sample_calibration, (680, 685), 1280, 720)


def test_crop贴边换全场兜底图(sample_video, sample_calibration, tmp_path):
    """x=5 贴左缘 → 人像裁框只会拍到边角杂物，换全场图（无 _a/_b、无十字），
    与 A 类同一条兜底路。"""
    tr = shooter.TraceResult(status="review", release_t=2.4, release_xy=(5, 200))
    rel = shooter.crop_shooter(str(sample_video), sample_calibration, tr,
                               str(tmp_path), "00:02.4")
    assert rel == ["shooters/00-02.4_full.jpg"]
    assert not (tmp_path / "shooters" / "00-02.4_a.jpg").exists()


def test_migrate_spot_bad贴边ok档重判(sample_video, sample_calibration,
                                       tmp_path):
    """08-29_3 全档已带 spot=ok（A 类迁移过）——贴边门控上线后旧档靠本条
    重判：spot=ok 且贴边 → 换全场图+标 bad；不贴边的 ok 不动；幂等。"""
    work = str(tmp_path)
    (tmp_path / "shooters").mkdir()
    (tmp_path / "shooters" / "00-02.4_a.jpg").write_bytes(b"old")
    shooter.save_shooters(work, str(sample_video), sample_calibration, {
        "00:02.4": {"status": "review", "release_t": 2.4,
                    "release_xy": [5, 200], "track_n": 3, "track_span": 0.6,
                    "thumbs": ["shooters/00-02.4_a.jpg"], "err": None,
                    "spot": "ok"},
        "00:05.0": {"status": "high", "release_t": 5.0,
                    "release_xy": [340, 200], "track_n": 8, "track_span": 1.0,
                    "thumbs": ["shooters/00-05.0_a.jpg"], "err": None,
                    "spot": "ok"}})
    assert shooter.migrate_spot_bad(str(sample_video), sample_calibration,
                                    work) == 1
    data = shooter.load_shooters(work)["traces"]
    assert data["00:02.4"]["spot"] == "bad"                  # 贴边重判
    assert data["00:02.4"]["thumbs"] == ["shooters/00-02.4_full.jpg"]
    assert data["00:05.0"]["thumbs"] == ["shooters/00-05.0_a.jpg"]  # 不贴边不动
    assert shooter.migrate_spot_bad(str(sample_video), sample_calibration,
                                    work) == 0               # 幂等（bad 不再进）


# ---------- 动图缩略图（2026-09-07）：前 0.7 后 0.4 / 8fps / 十字逐帧跟球 ----------
# 参数取证（两球逐帧）：00-06.1 传接球球 -0.7 前框里是白球衣传球人（更长=
# 录进错的人）、62-06.2 急停跳投 0.7s 装下完整收球→起跳链（更长=空跑帧）。
# 09-09 十字改跟球：每帧搜索区 HSV 检测（同 scan 口径）就近贴上一帧位置，
# 无球帧不画（诚实消失）；固定出手点红十字仍在 _a.jpg 上。
# 09-13 跟球双门（S≥140 且 面积≥0.10×筐宽²）：09-04_1 原帧取证——皮肤/
# 头/手块 S≤136 或为细条（≤0.035 筐宽²），飞行球 S≥143、面积 0.12~0.26
# 筐宽²；旧门 S≥88+MIN_AREA=9 放进人身上中饱和大块（实测 02-49.3 九帧
# 全锁出手人小腿），「就近」规则天然偏向慢速人体 → 双门把人块清出候选，
# 宁可十字诚实消失（v1 声明的哲学，兜底=人工核对）。

def _make_follow_video(path):
    """GIF 跟球专用合成源：球 r=15（面积≈707 ≥ 跟球面积门 0.10×筐宽²=640；
    conftest 共享夹具的 r=11 球是判定器下落实体校验的尺寸，过不了跟球门）。
    另加两块干扰（两块都过旧门，红测试锚点）：
      腿 = 中饱和大块 BGR(110,140,190)→S≈107（只被 S 门挡）
      头 = 鲜活细条 r=3 BGR(70,90,230)→S≈177（只被面积门挡）
    背景 = 绿→蓝 16px 色块墙（H60~140）：真帧场景渐变会把 GIF 自适应色板
    吃满 256 色、~50px 纯红十字被量化吞掉（v3 实证真帧编码后红像素=0），
    纯色背景测不出该坑——色块墙既复现色板压力、又全帧零严格红不冒
    十字假信号（合成时双条件实测验证过）。"""
    cellbg = np.zeros((360, 640, 3), np.uint8)
    for y0 in range(0, 360, 16):
        for x0 in range(0, 640, 16):
            hue = 60 + ((x0 // 16 * 7 + y0 // 16 * 13) % 80)
            s = 90 + ((x0 + y0) % 100)
            v = 90 + ((x0 - y0) % 120)
            px = np.uint8([[[hue, s, v]]])
            cellbg[y0:y0 + 16, x0:x0 + 16] = \
                cv2.cvtColor(px, cv2.COLOR_HSV2BGR)[0, 0]
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 30, (640, 360))
    for i in range(180):
        img = cellbg.copy()
        cv2.line(img, (300, 154), (380, 154), (180, 180, 180), 3)  # 灰色筐
        cv2.circle(img, (300, 300), 22, (110, 140, 190), -1)       # 腿块
        cv2.circle(img, (380, 150), 3, (70, 90, 230), -1)          # 头细条
        if 60 <= i <= 90:
            k = (i - 60) / 30.0
            cv2.circle(img, (340, int(100 + 120 * k)), 15, (20, 100, 245), -1)
        vw.write(img)
    vw.release()
    return path


@pytest.fixture(scope="module")
def follow_video(tmp_path_factory):
    return _make_follow_video(str(tmp_path_factory.mktemp("vid") / "follow.mp4"))


def test_save_gif十字逐帧跟球无球帧不画(follow_video, sample_calibration,
                                        tmp_path):
    from PIL import Image
    ok = shooter._save_gif(str(follow_video), sample_calibration, str(tmp_path),
                           "00:02.4",
                           {"release_t": 2.4, "release_xy": [340, 120]})
    assert ok
    p = tmp_path / "shooters" / "00-02.4.gif"
    assert p.exists()
    img = Image.open(p)
    assert img.size[1] == shooter.GIF_H
    # Pillow 合并完全相同的相邻帧（合成视频背景静止 3 帧并 1）——真视频有
    # 传感器噪声不触发；合并只减帧数不减时长，断言总时长守恒（9×125ms）
    total, n = 0, 0
    frames_rgb = []
    try:
        while True:
            total += img.info.get("duration", 0)
            frames_rgb.append(np.array(img.convert("RGB")))
            n += 1
            img.seek(img.tell() + 1)
    except EOFError:
        pass
    assert 2 <= n <= int((shooter.GIF_PRE_S + shooter.GIF_POST_S)
                         * shooter.GIF_FPS) + 1
    # GIF 时长存 10ms 整数格（125ms→120ms），每帧容差一格
    n_req = int((shooter.GIF_PRE_S + shooter.GIF_POST_S)
                * shooter.GIF_FPS) + 1
    assert round(1000 / shooter.GIF_FPS) * n_req - 10 * n_req \
        <= total <= round(1000 / shooter.GIF_FPS) * n_req

    # 合成视频球只在 t∈[2.0,3.0]（x=340 恒定，y=100+4×(30t-60)）；
    # release_xy=(340,120) 是故意偏移的估计点——十字必须跟真实球位走，
    # 不是锚死估计点。GIF 坐标 → 帧坐标换算用 _gif_crop_box 同款几何
    from bbh.detector import RIM_Y_RATIO, _scaled_rect
    hx, hy, hw, hh = _scaled_rect(sample_calibration, 640, 360)
    x0, y0, x1, y1 = shooter._gif_crop_box(340, 120, 640, 360, hw,
                                           hy + hh * RIM_Y_RATIO)
    kx = (x1 - x0) / img.size[0]
    ky = (y1 - y0) / shooter.GIF_H
    reds = []                      # [(帧坐标质心 x, y)]，无十字帧跳过
    def _cross_mask(arr):
        # 纯红十字 (255,0,0)：R−G>80 且 G<80——球本身是橙 (245,100,20)，
        # 只用 R−G 会把球块也算进质心把十字拽偏；G<80 排橙球
        r, g = arr[:, :, 0].astype(int), arr[:, :, 1].astype(int)
        return (r - g > 80) & (g < 80)
    for arr in frames_rgb:
        red = _cross_mask(arr)
        if not red.any():
            continue
        ys, xs = np.nonzero(red)
        reds.append((xs.mean() * kx + x0, ys.mean() * ky + y0))
    # 首帧 t≈1.7 无球：只剩干扰块（腿 x≈300 中饱和大块、头 x≈380 鲜活细条，
    # 两块都过旧门且头离链锚 (340,120) 更近）——旧代码就近跟头细条画十字，
    # 双门（S/面积）下必须不画
    assert not _cross_mask(frames_rgb[0]).any()
    assert len(reds) >= 6          # 7 个有球目标，个别相邻帧量化后并帧
    bx, by = reds[0]
    assert abs(bx - 340) <= 6      # 首个有球帧球位 (340, ~104)
    assert abs(by - (100 + 4 * (round(2.05 * 30) - 60))) <= 8
    ex, ey = reds[-1]
    assert abs(ex - 340) <= 6      # 末有球帧球位 (340, ~184)
    # 末目标 t=release+0.4=2.7 → 就近帧 81 → y=184（旧断言 2.775→192 靠
    # 容差 8 擦边，v3 分辨率下 8.25 露馅，改按真实目标时刻算）
    assert abs(ey - (100 + 4 * (round(2.7 * 30) - 60))) <= 8
    ys_seq = [r[1] for r in reds]
    assert all(a <= b for a, b in zip(ys_seq, ys_seq[1:]))   # 单调下落
    for bx, _by in reds:                # 干扰块不窃链：腿 x≈300、头 x≈380
        assert abs(bx - 340) <= 6       # 与球 x=340 混不进 ±6


def test_save_gif未溯源或裁剪退化放弃(sample_video, sample_calibration,
                                     tmp_path):
    work = str(tmp_path)
    assert not shooter._save_gif(str(sample_video), sample_calibration, work,
                                 "00:02.4", {"release_t": None,
                                             "release_xy": None})
    assert not (tmp_path / "shooters").exists()      # 未建目录就放弃


def test_gif_backfill幂等且跳过spot坏球未溯源(sample_video,
                                              sample_calibration, tmp_path):
    work = str(tmp_path)
    (tmp_path / "shooters").mkdir()
    (tmp_path / "shooters" / "00-02.4_a.jpg").write_bytes(b"old")
    (tmp_path / "shooters" / "00-05.0_full.jpg").write_bytes(b"old")
    shooter.save_shooters(work, str(sample_video), sample_calibration, {
        "00:02.4": {"status": "review", "release_t": 2.4,
                    "release_xy": [340, 120],
                    "thumbs": ["shooters/00-02.4_a.jpg"], "spot": "ok"},
        "00:05.0": {"status": "review", "release_t": 5.0,
                    "release_xy": [340, 100],
                    "thumbs": ["shooters/00-05.0_full.jpg"], "spot": "bad"},
        "00:06.0": {"status": "low", "release_t": None, "release_xy": None,
                    "thumbs": [], "err": None}})
    prog, made = _drain(shooter._gif_backfill(
        str(sample_video), sample_calibration, work,
        shooter.load_shooters(work)["traces"]))
    assert made == 1
    assert prog == ["🎬 生成射手动图（1/1）：00:02.4"]
    assert (tmp_path / "shooters" / "00-02.4.gif").exists()
    assert not (tmp_path / "shooters" / "00-05.0.gif").exists()  # bad 跳过
    assert not (tmp_path / "shooters" / "00-06.0.gif").exists()  # 未溯源跳过
    prog2, made2 = _drain(shooter._gif_backfill(          # 幂等：文件在→不进
        str(sample_video), sample_calibration, work,
        shooter.load_shooters(work)["traces"]))
    assert made2 == 0 and prog2 == []


def test_trace_video缓存全齐时补动图(sample_video, sample_calibration,
                                    tmp_path):
    """「未重扫」早退前也要过动图回填——旧缓存球（如 08-29_3 全档）首次点
    「识别出手人」即回填，二次点击零进度（幂等）。"""
    from types import SimpleNamespace
    work = str(tmp_path)
    (tmp_path / "shooters").mkdir()
    (tmp_path / "shooters" / "00-02.4_a.jpg").write_bytes(b"old")
    shooter.save_shooters(work, str(sample_video), sample_calibration,
                          {"00:02.4": {"status": "review", "release_t": 2.4,
                                       "release_xy": [340, 120],
                                       "thumbs": ["shooters/00-02.4_a.jpg"],
                                       "spot": "ok"}})
    prog, summary = _drain(shooter.trace_video(
        str(sample_video), sample_calibration, work, [SimpleNamespace(t=2.4)]))
    # 进度含 person 框重裁（旧档球补 person_ver，见 test_旧档只重裁不重扫）
    # + 动图回填；二次点击零进度（两道补齐都幂等）
    assert "🎬 生成射手动图（1/1）：00:02.4" in prog
    assert "未重扫" in summary and "补生成动图 1 张" in summary
    assert (tmp_path / "shooters" / "00-02.4.gif").exists()
    prog2, summary2 = _drain(shooter.trace_video(
        str(sample_video), sample_calibration, work, [SimpleNamespace(t=2.4)]))
    assert not prog2 and "未重扫" in summary2


# ---------- 编排（真跑 run_detection 后全链路） ----------
def _drain(gen):
    """跑完生成器：进度列表 + return 的汇总文案。"""
    prog = []
    while True:
        try:
            prog.append(next(gen))
        except StopIteration as st:
            return prog, st.value


def test_trace_video全链路(sample_video, sample_calibration, tmp_path):
    from bbh.pipeline import run_detection
    from bbh.models import load_events
    from bbh import person
    work = str(tmp_path)
    events_path, _ = run_detection(str(sample_video), sample_calibration, work)
    events = load_events(events_path)["events"]
    assert events, "合成视频应检出至少一个进球事件"

    prog, summary = _drain(shooter.trace_video(
        str(sample_video), sample_calibration, work, events))
    assert prog and "⏳" in prog[0]
    data = shooter.load_shooters(work)
    assert time_key(events[0].t) in data["traces"]
    tr = data["traces"][time_key(events[0].t)]
    assert tr["status"] in ("high", "review", "low")
    # 新扫描的球直接带 person 版本与归属档（下次进来不触发重裁）
    assert tr["person_ver"] == person.PERSON_VERSION
    assert "person" in tr
    assert summary.startswith("✅")
    # E2E：溯源出的缩略图相对路径在 work 目录下真实存在
    for tr in data["traces"].values():
        for t in tr.get("thumbs") or []:
            assert os.path.exists(os.path.join(work, t))


def test_trace_video增量续跑与版本作废(sample_video, sample_calibration,
                                       tmp_path, monkeypatch):
    from bbh.pipeline import run_detection
    from bbh.models import load_events
    work = str(tmp_path)
    events_path, _ = run_detection(str(sample_video), sample_calibration, work)
    events = load_events(events_path)["events"]
    _drain(shooter.trace_video(str(sample_video), sample_calibration,
                               work, events))
    # 全部已有缓存 → 无进度，直接汇总（中断重跑只补缺）
    prog2, summary2 = _drain(shooter.trace_video(
        str(sample_video), sample_calibration, work, events))
    assert not prog2 and "未重扫" in summary2
    # 版本升级 → 身份不符，全部重扫
    monkeypatch.setattr(shooter, "SHOOTER_VERSION", 99)
    prog3, _ = _drain(shooter.trace_video(
        str(sample_video), sample_calibration, work, events))
    assert prog3


def test_gif_crop_box放大且上偏():
    """GIF 专用裁剪框：包含静态 jpg 框、宽高都放大、出手点上方余量大于
    下方（v6 复扫出手点常在球发力期低位，人在上方——09-13 抽验两例实证，
    上偏让人上半身不因十字偏低被切）；纯函数零取帧。"""
    rx, ry, fw, fh, hw, rim_y = 960.0, 540.0, 1920, 1080, 64.0, 371.0
    base = shooter._crop_box(rx, ry, fw, fh, hw, rim_y)
    gif = shooter._gif_crop_box(rx, ry, fw, fh, hw, rim_y)
    assert gif[0] <= base[0] and gif[1] <= base[1]      # 包含静态框
    assert gif[2] >= base[2] and gif[3] >= base[3]
    assert (gif[2] - gif[0]) > (base[2] - base[0])      # 宽放大
    assert (gif[3] - gif[1]) > (base[3] - base[1])      # 高放大
    assert (ry - gif[1]) > (gif[3] - ry)                # 上余量 > 下余量
    # 贴边不越界（clamp 后仍在画面内）
    edge = shooter._gif_crop_box(rx, 60.0, fw, fh, hw, rim_y)
    assert edge[1] == 0 and edge[3] <= fh


def test_gif版本过期缓存相符也清旧动图(sample_video, sample_calibration,
                                        tmp_path, monkeypatch):
    """GIF 渲染参数走 gif_ver（不进身份校验）：过期 → 缓存相符也清旧图
    按新参重生成（溯源 traces 不作废——对照 SHOOTER_VERSION 作废=全量
    重扫）；落盘新 gif_ver 后幂等，二次点击不再清。"""
    from types import SimpleNamespace
    work = str(tmp_path)
    (tmp_path / "shooters").mkdir()
    gif = tmp_path / "shooters" / "00-02.4.gif"
    gif.write_bytes(b"stale")              # 模拟旧渲染参数的动图
    shooter.save_shooters(work, str(sample_video), sample_calibration,
                          {"00:02.4": {"status": "review", "release_t": 2.4,
                                       "release_xy": [340, 120],
                                       "thumbs": ["shooters/00-02.4_a.jpg"],
                                       "spot": "ok"}})
    monkeypatch.setattr(shooter, "_save_gif", lambda *a, **k: False)
    # 模拟「渲染参数升版后旧档」：缓存时写入的是当前 gif_ver，改常量即过期
    monkeypatch.setattr(shooter, "GIF_VERSION", 99)
    prog1, summary1 = _drain(shooter.trace_video(
        str(sample_video), sample_calibration, work, [SimpleNamespace(t=2.4)]))
    assert any("🗑" in s for s in prog1) and not gif.exists()
    assert shooter.load_shooters(work)["gif_ver"] == 99   # 落盘防重复清
    assert "未重扫" in summary1                          # 溯源缓存没作废
    # 二次点击：gif_ver 已新 → 不再清（幂等），也未重扫
    _, summary2 = _drain(shooter.trace_video(
        str(sample_video), sample_calibration, work, [SimpleNamespace(t=2.4)]))
    assert "未重扫" in summary2
    # gif_ver 回到当前值 → 永不再清
    monkeypatch.setattr(shooter, "GIF_VERSION",
                        shooter.load_shooters(work)["gif_ver"])
    _ = _drain(shooter.trace_video(str(sample_video), sample_calibration,
                                   work, [SimpleNamespace(t=2.4)]))


def test_trace_video版本作废清旧动图(sample_video, sample_calibration,
                                     tmp_path, monkeypatch):
    """身份失效全量重扫时须清旧 GIF——回填幂等只认文件存在性，锚旧出手点
    的动图会永久残留冒充新图（v5→v6 拍灯教训）。缓存相符的「未重扫」
    早退路径不动文件。"""
    from types import SimpleNamespace
    work = str(tmp_path)
    (tmp_path / "shooters").mkdir()
    gif = tmp_path / "shooters" / "00-02.4.gif"
    gif.write_bytes(b"stale")              # 模拟旧版本锚错出手点的动图
    shooter.save_shooters(work, str(sample_video), sample_calibration,
                          {"00:02.4": {"status": "review", "release_t": 2.4,
                                       "release_xy": [340, 120],
                                       "thumbs": ["shooters/00-02.4_a.jpg"],
                                       "spot": "ok"}},
                          gif_ver=shooter.GIF_VERSION)   # 当前参数档才免清
    # 缓存相符 → 未重扫早退：旧动图原样保留（_save_gif stub 也摸不到它）
    monkeypatch.setattr(shooter, "_save_gif", lambda *a, **k: False)
    _, summary2 = _drain(shooter.trace_video(
        str(sample_video), sample_calibration, work, [SimpleNamespace(t=2.4)]))
    assert "未重扫" in summary2 and gif.read_bytes() == b"stale"
    # 版本升级 → 身份不符全量重扫：旧动图清除（_save_gif 已 stub，不会重生）
    monkeypatch.setattr(shooter, "SHOOTER_VERSION", 99)
    _drain(shooter.trace_video(str(sample_video), sample_calibration, work,
                               [SimpleNamespace(t=2.4)]))
    assert not gif.exists()


def test_gif版本字段缺失的老档仍清旧动图(sample_video, sample_calibration,
                                         tmp_path, monkeypatch):
    """v2 之前代码写的老档没有 gif_ver 字段：trace_video 首行 migrate 对
    带 spot 的档必然重写 shooters.json——重写必须严格镜像磁盘（没有就
    保持没有），否则凭空盖章当前值后，随后的过期检查读到的已是新值、
    永不清旧图（09-13 09-04_1 实发：旧 240 图冒充 v2 未重生成）。"""
    from types import SimpleNamespace
    import json as _json
    work = str(tmp_path)
    (tmp_path / "shooters").mkdir()
    gif = tmp_path / "shooters" / "00-02.4.gif"
    gif.write_bytes(b"stale")
    shooter.save_shooters(work, str(sample_video), sample_calibration,
                          {"00:02.4": {"status": "review", "release_t": 2.4,
                                       "release_xy": [340, 120],
                                       "thumbs": ["shooters/00-02.4_a.jpg"],
                                       "spot": "ok"}})
    # 手工剥掉 gif_ver 模拟老代码写的档（save_shooters 时代之前/之外）
    p = tmp_path / "shooters.json"
    d = _json.loads(p.read_text(encoding="utf-8"))
    d.pop("gif_ver", None)                    # 镜像语义下首存可能本就无字段
    p.write_text(_json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    monkeypatch.setattr(shooter, "_save_gif", lambda *a, **k: False)
    prog, summary = _drain(shooter.trace_video(
        str(sample_video), sample_calibration, work, [SimpleNamespace(t=2.4)]))
    assert any("🗑" in s for s in prog) and not gif.exists()
    assert shooter.load_shooters(work)["gif_ver"] == shooter.GIF_VERSION
    assert "未重扫" in summary                      # 溯源缓存没作废


# ---------- app 集成 ----------
def test_run_shooter_tracing缺检测结果报错(sample_video, sample_calibration,
                                          tmp_path, monkeypatch):
    from bbh import _gr_compat as gr
    monkeypatch.setattr(app_module, "CALIB_FILE", str(tmp_path / "c.json"))
    sample_calibration.save(str(tmp_path / "c.json"))
    monkeypatch.setattr(app_module, "_work_dir_for", lambda p: str(tmp_path / "w"))
    with pytest.raises(gr.Error):
        list(app_module.run_shooter_tracing(str(sample_video)))


def test_run_shooter_tracing全流程(sample_video, sample_calibration,
                                   tmp_path, monkeypatch):
    from bbh.pipeline import run_detection
    work = str(tmp_path / "w")
    # 标定按生产布局存 work 目录内（load_segments 只认目录内的
    # calibration.json，不支持目录外绝对路径注入）。
    (tmp_path / "w").mkdir(parents=True)
    sample_calibration.save(str(tmp_path / "w" / "calibration.json"))
    monkeypatch.setattr(app_module, "_work_dir_for", lambda p: work)
    run_detection(str(sample_video), sample_calibration, work)
    outs = list(app_module.run_shooter_tracing(str(sample_video)))
    assert outs, "至少一条汇总"
    last_msg, last_btn = outs[-1]
    assert last_msg.startswith("✅")
    assert last_btn["interactive"] is True      # 结束恢复按钮
    assert shooter.load_shooters(work)["traces"]


def test_shooter_block三种状态(sample_video, sample_calibration, tmp_path,
                               monkeypatch):
    work = str(tmp_path / "w")
    monkeypatch.setattr(app_module, "_work_dir_for", lambda p: work)
    rows = [[0, "00:02.5", "high", "0.90", 5, 2, ""]]
    # ① 从未识别过 → 引导提示
    assert "识别出手人" in app_module._shooter_block_html(str(sample_video),
                                                        rows, 0)
    # ② 已识别但该球未溯源 → ⚪ 说明
    shooter.save_shooters(work, str(sample_video), sample_calibration,
                          {"99:99.9": {"status": "low", "thumbs": []}})
    assert "未溯源" in app_module._shooter_block_html(str(sample_video), rows, 0)
    # ③ 有溯源 → 缩略图 <img>（写真 jpg 满足存在性检查）
    (tmp_path / "w" / "shooters").mkdir(parents=True, exist_ok=True)
    # cv2.imwrite 对中文路径（tmp_path 含中文测试名）静默失败 → imencode+tofile
    cv2.imencode(".jpg", np.zeros((10, 10, 3), np.uint8))[1].tofile(
        str(tmp_path / "w" / "shooters" / "a.jpg"))
    shooter.save_shooters(work, str(sample_video), sample_calibration,
                          {"00:02.5": {"status": "high", "release_t": 2.3,
                                       "release_xy": [340, 200],
                                       "thumbs": ["shooters/a.jpg"]}})
    h = app_module._shooter_block_html(str(sample_video), rows, 0)
    assert "<img" in h and "出手人" in h


def test_shooter_block优先动图(sample_video, sample_calibration, tmp_path,
                               monkeypatch):
    """有 {key}.gif → 单张动图（覆盖 _a/_b 两帧语义：前 0.7s→出手后 0.4s、
    全程红十字）；无 gif → 原 _a/_b 静态对不动。"""
    work = str(tmp_path / "w")
    monkeypatch.setattr(app_module, "_work_dir_for", lambda p: work)
    rows = [[0, "00:02.5", "high", "0.90", 5, 2, ""]]
    (tmp_path / "w" / "shooters").mkdir(parents=True)
    cv2.imencode(".jpg", np.zeros((10, 10, 3), np.uint8))[1].tofile(
        str(tmp_path / "w" / "shooters" / "a.jpg"))
    shooter.save_shooters(work, str(sample_video), sample_calibration,
                          {"00:02.5": {"status": "high", "release_t": 2.3,
                                       "release_xy": [340, 200],
                                       "thumbs": ["shooters/a.jpg"]}})
    h = app_module._shooter_block_html(str(sample_video), rows, 0)
    assert h.count("<img") == 1 and "a.jpg" in h            # 无 gif → 静态
    (tmp_path / "w" / "shooters" / "00-02.5.gif").write_bytes(b"GIF89a")
    h2 = app_module._shooter_block_html(str(sample_video), rows, 0)
    assert h2.count("<img") == 1 and "00-02.5.gif" in h2    # 有 gif → 单张动图


def test_cur_panel带video_path附出手人块(sample_video, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_work_dir_for", lambda p: str(tmp_path / "w"))
    rows = [[0, "00:02.5", "high", "0.90", 5, 2, ""]]
    h = app_module._cur_panel_html(rows, 0, "判定依据：…",
                                   video_path=str(sample_video))
    assert "出手人" in h
    assert "出手人" not in app_module._cur_panel_html(rows, 0, "判定依据：…")


# ---------- 验收工具 ----------
def test_check_shooters出对照表(tmp_path, sample_video, sample_calibration):
    work = tmp_path / "w"
    (work / "shooters").mkdir(parents=True)
    cv2.imencode(".jpg", np.zeros((10, 10, 3), np.uint8))[1].tofile(str(work / "shooters" / "a.jpg"))
    shooter.save_shooters(str(work), str(sample_video), sample_calibration,
                          {"00:02.5": {"status": "high", "release_t": 2.3,
                                       "release_xy": [340, 200],
                                       "thumbs": ["shooters/a.jpg"]}})
    truth = tmp_path / "truth.json"
    truth.write_text(json.dumps({"shooters": {"00:02.5": "老杨"}}),
                     encoding="utf-8")
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "tools/check_shooters.py",
                        str(work), str(truth)], capture_output=True,
                       text=True, encoding="utf-8", cwd=repo, env=env)
    assert r.returncode == 0, r.stderr
    html_txt = (work / "shooters_check.html").read_text(encoding="utf-8")
    assert "老杨" in html_txt and "<img" in html_txt
    assert "对照" in r.stdout


# ---------- v4 扫描饱和度地板（2026-09-12，09-04_1 段1 定位失效取证） ----------
def test_scan_hsv饱和度地板():
    """S_lo<88 抬到 88（灭顶棚暖反光——实测 S 37~80 全过宽色域 mask）；
    S_lo≥88 逐字节 no-op（四场已验收场 88/90/90/140 零变化）；标定整体
    低饱和（S_hi<88）不抬——抬出空 mask 会把合法低饱场景全判 low。"""
    def mk(lo, hi):
        return Calibration(hoop_rect=(0, 0, 10, 4), hsv_low=lo, hsv_high=hi,
                           frame_size=(640, 360))
    lo, hi = shooter._scan_hsv(mk((0, 37, 40), (25, 188, 255)))
    assert lo[1] == 88 and hi[1] == 188        # 09-04_1 段1 实况：37→88
    lo, _ = shooter._scan_hsv(mk((0, 90, 60), (25, 200, 200)))
    assert lo[1] == 90                         # 已验收场地板=no-op
    lo, _ = shooter._scan_hsv(mk((0, 140, 101), (12, 232, 187)))
    assert lo[1] == 140                        # 08-21_1 同上
    lo, _ = shooter._scan_hsv(mk((0, 37, 40), (25, 70, 255)))
    assert lo[1] == 37                         # S_hi 更低：不抬（保险）


def test_scan饱和度地板灭顶棚低饱反光(tmp_path):
    """09-04_1 段1 复刻：远景球只有几像素，标定点到模糊球像素（S=77）再
    放宽 40 → S_lo=37，顶棚暖反光全过 mask、每帧在场劫持链（89/119 球
    定位失效，出手点标到 y=42~80 顶棚）。地板后顶棚斑（S≈60）全灭、
    高饱真球（S≈234）照收。标定数据不动（主检测已验收），收紧只在
    扫描口径。"""
    video = str(tmp_path / "roof.mp4")
    vw = cv2.VideoWriter(video, cv2.VideoWriter_fourcc(*"mp4v"), 30,
                         (640, 360))
    for i in range(100):
        # 背景零饱和灰（S=0 不过宽色域的 S≥37）——真实视频里正是
        # 「背景不过 mask、只有反光斑过」才让顶棚斑单独成块劫持链
        img = np.full((360, 640, 3), (45, 45, 45), np.uint8)
        cv2.line(img, (300, 154), (380, 154), (180, 180, 180), 3)
        # 顶棚反光斑：BGR(187,200,245) → H≈7 S≈60 V=245——宽色域
        # (0,37,40)~(25,188,255) 过、地板 88 后灭；远离筐带不进筐带排除
        cv2.circle(img, (500, 40), 12, (187, 200, 245), -1)
        if 60 <= i <= 90:      # 真球：BGR(90,150,245)→H≈23 S≈161——段1
            k = (i - 60) / 30.0  # 真实球就在宽域内（S 37~188），地板前后都过
            cv2.circle(img, (340, int(100 + 120 * k)), 11, (90, 150, 245), -1)
        vw.write(img)
    vw.release()
    wide = Calibration(hoop_rect=(300, 140, 80, 40), hsv_low=(0, 37, 40),
                       hsv_high=(25, 188, 255), frame_size=(640, 360))
    pts, _ = shooter.scan_release_window(video, wide, t_goal=2.8,
                                         window_s=2.0)
    assert not [p for p in pts if p.y < 80], "顶棚低饱斑不应进检出流"
    assert [p for p in pts if abs(p.x - 340) < 6 and p.y > 80], \
        "高饱真球应照常检出（地板不误杀）"


# ---------- v5 链首在飞的抛物线回推（2026-09-12，09-04_1 段4 定位失效取证） ----------
V5_FRAME = (1920, 1080)
V5_HOOP = Calibration(hoop_rect=(726, 362, 64, 26), hsv_low=(0, 111, 87),
                      hsv_high=(13, 227, 190), frame_size=V5_FRAME)
V5_W, V5_C = 64.0, (758.0, 371.1)     # 段4 实况几何：筐心=筐口中心（y=362+26*.35）


def _pts_descent_only(g=1200.0, vx=-500.0, apex_t=0.55,
                      apex=(1092.0, 92.0), n=10):
    """远景深位出手合成流：只含下降段（09-04_1 段4 实况——出手→弧顶的
    上升段球只有几像素+运动模糊，HSV 逐帧放宽参数实测 0 检出，链首=弧顶，
    在筐平面上方）。抛物线从弧顶飞向筐口，同真实 31:49.3 的 9 点链。"""
    return [_pt(round(apex_t + 0.09 * j, 3), apex[0] + vx * 0.09 * j,
                apex[1] + 0.5 * g * (0.09 * j) ** 2) for j in range(n)]


def test_链首在飞抛物线回推出手点():
    """found=False 且估计点在筐平面上方：对下降段拟合、从拟合弧顶回推
    EXTRAP_RISE_S（出手上升时长物理典型值，尺度无关）得出手时刻——段4
    46 球实测下降链都是真抛物线（拟合残差 <15px）。v6（2026-09-13 GIF
    拍灯取证）：抛物线只出时刻与 vx，位置来自 rescanner 复扫实测——透视
    下图像空间抛物线位置外推结构性不可靠（表观曲率沿轨迹 5 倍差，33 外推
    球 31 个筐上=拍灯）。status 仍 review（估计不升 high），extrap 置位。"""
    pts = _pts_descent_only()
    tr = shooter.trace_release(pts, t_goal=1.5, hoop_w=V5_W, hoop_c=V5_C,
                               frame_size=V5_FRAME,
                               rescanner=lambda *a: (1440.0, 378.0))
    assert tr.found is False and tr.status == "review"   # 链首就在飞：不升 high
    assert tr.extrap is True
    # 位置=复扫实测点（球刚离手就在出手人手边），不是拟合外推 (1292,188)
    assert tr.release_xy == (1440.0, 378.0)
    assert tr.release_t == pytest.approx(0.15, abs=1e-6)  # t_rel=弧顶0.55-0.40
    # 复扫点筐下：严口径也过；extrap 放宽（EXTRAP_ABOVE_W）留给合法深位筐上
    assert not shooter.spot_bad_for(tr, V5_HOOP, *V5_FRAME)


def test_回推门控非抛物线与贴顶():
    """护栏：链非抛物线（拟合残差超限）、无曲率、回推点贴画面顶 15%
    （顶棚区）或出边时都返回 None——保持链首估计诚实判 bad，不拿拟合
    噪声/顶棚点当出手。"""
    # a) 交替抖动 ±40px：y 残差 ~40px > 0.5 筐（32px）门
    jitter = [_pt(p.t, p.x, p.y + (40 if j % 2 else -40))
              for j, p in enumerate(_pts_descent_only())]
    assert shooter._extrap_release(jitter, V5_W, V5_FRAME, 1.5) is None
    # b) 无曲率（匀速直线下降）：拟合不出上开口抛物线
    linear = [_pt(0.55 + 0.09 * j, 1092 - 45 * j, 92 + 40 * j)
              for j in range(10)]
    assert shooter._extrap_release(linear, V5_W, V5_FRAME, 1.5) is None
    # c) 贴顶：回推点 y=60+96=156 < 0.15*1080=162 → 顶棚区不收
    high_apex = _pts_descent_only(apex=(1092.0, 60.0))
    assert shooter._extrap_release(high_apex, V5_W, V5_FRAME, 1.5) is None
    # d) 出右边框：vx=-2100 → 回推 x=1092+840=1932 > 1920-45
    far = _pts_descent_only(vx=-2100.0)
    assert shooter._extrap_release(far, V5_W, V5_FRAME, 1.5) is None


def test_spot_bad_for外推点放宽容差():
    """外推估计点的筐上容差 EXTRAP_ABOVE_W（5.5 筐）：深位出手（高出筐
    平面 ≤4 筐）不判失效；顶棚级（6 筐）仍拦；非外推点维持 0.5 筐严口径。"""
    rim = 371.1
    deep = shooter.TraceResult(status="review", extrap=True,
                               release_xy=(1292, rim - 4 * V5_W))
    assert not shooter.spot_bad_for(deep, V5_HOOP, *V5_FRAME)
    ceiling = shooter.TraceResult(status="review", extrap=True,
                                  release_xy=(1292, rim - 6.5 * V5_W))
    assert shooter.spot_bad_for(ceiling, V5_HOOP, *V5_FRAME)
    same_pt = shooter.TraceResult(status="review", extrap=False,
                                  release_xy=(1292, rim - 4 * V5_W))
    assert shooter.spot_bad_for(same_pt, V5_HOOP, *V5_FRAME)


def test_筐下链首估计不外推():
    """found=False 但估计点在筐平面以下（近筐链首在飞）：不属于远景深位
    病灶类，不外推——保持既有行为（链首当估计），避免改动面扩大。"""
    pts = _pts_descent_only(apex=(860.0, 420.0))   # 弧顶已在筐平面下
    tr = shooter.trace_release(pts, t_goal=1.5, hoop_w=V5_W, hoop_c=V5_C,
                               frame_size=V5_FRAME)
    assert tr.found is False and tr.extrap is False
    assert abs(tr.release_t - pts[0].t) < 1e-6      # 链首当出手估计


# ---------- v6 外推位置修正：时刻回推+局部复扫（2026-09-13，GIF 拍灯取证） ----------


def test_复扫失败退回链首诚实判bad():
    """rescanner 返回 None（球被手遮/假链/t_rel 时刻球已出画）：不升级——
    链首（弧顶，筐上）当估计照旧判 bad。EXTRAP_ABOVE_W 放宽只配复扫实测
    命中的球享受（可信度来源换了：不是拟合质量，是 t_rel 时刻真的看到了
    球+v 速度与链一致）；v5 把放宽发给纯拟合位置是 31 球拍灯的根因。"""
    pts = _pts_descent_only()
    tr = shooter.trace_release(pts, t_goal=1.5, hoop_w=V5_W, hoop_c=V5_C,
                               frame_size=V5_FRAME, rescanner=lambda *a: None)
    assert tr.extrap is False
    assert tr.release_xy == (pts[0].x, pts[0].y)    # 链首估计（弧顶，筐上）
    assert shooter.spot_bad_for(tr, V5_HOOP, *V5_FRAME)   # 老口径即拦


def test_无复扫器保持链首估计():
    """rescanner 缺省（None）：外推分支整体不激活——旧调用方/纯函数测试
    维持 v5 前诚实行为，外推升级只在 IO 层（trace_video 真复扫）发生。"""
    pts = _pts_descent_only()
    tr = shooter.trace_release(pts, t_goal=1.5, hoop_w=V5_W, hoop_c=V5_C,
                               frame_size=V5_FRAME)
    assert tr.extrap is False
    assert tr.release_xy == (pts[0].x, pts[0].y)


def test_复扫参数传递vx与链尾参考():
    """rescanner 收到 (t_rel, vx, 链尾x, 链尾t)：vx=链上 x 一次拟合斜率
    （真实飞行 vx 恒定、透视下 x 基本线性——v5 的 x 二次项 157.6 是拟合
    噪声），IO 层用它做预期 x 带（线性回推，105:43.6 误差实测 ~70px）+
    相邻帧速度过滤（静态假块衣服/反光 Δx≈0 一眼假）。链尾参考=离复扫时刻
    最近的链点（外推距离最短）。"""
    seen = []
    pts = _pts_descent_only()       # vx=-500, 链尾 t=0.55+0.09*9=1.36
    shooter.trace_release(pts, t_goal=1.5, hoop_w=V5_W, hoop_c=V5_C,
                          frame_size=V5_FRAME,
                          rescanner=lambda *a: seen.append(a) or None)
    assert len(seen) == 1
    t_rel, vx, x_ref, t_ref = seen[0]
    assert t_rel == pytest.approx(0.15, abs=1e-6)          # 弧顶 0.55 - 0.40
    assert vx == pytest.approx(-500.0, abs=5.0)
    # 消费语义：IO 层用 x_ref+vx*(t-t_ref) 做预期 x 带——回推到 t_rel 应得
    # 出手 x（链尾参考点=修剪后链上最后一点，可能不是 pts[-1]）
    assert x_ref + vx * (t_rel - t_ref) == pytest.approx(1292, abs=15)
    assert pts[0].t <= t_ref <= pts[-1].t
