# B2（网簇触发兜底 review 行注入器）单测：全片网动簇扫描（Temp b_scan.py
# 验证版移植）、残迹链过滤器、锚时刻内插、注入 pass（旧行永不改写）、
# sidecar 身份/多 entry 共存、管线 E2E。
# 几何与 conftest 同款：筐 (300,140,80,40)，rim_y=154，带下沿 y_lo=164，
# 横向界 [284, 396]（X_MARGIN 0.2）。
import json

import pytest

import bbh.net_clusters as nc
from bbh.models import BallPos, Calibration, GoalEvent
from bbh.pipeline import LOGIC_VERSION, run_detection

CAL = Calibration(hoop_rect=(300, 140, 80, 40), hsv_low=(0, 80, 120),
                  hsv_high=(25, 255, 255), frame_size=(640, 360))
RIM_Y = 140 + 0.35 * 40          # 154


def _pos(t, x, y, a=120.0):
    return BallPos(t=t, x=x, y=y, area=a)


def _ev(t):
    return GoalEvent(t=t, confidence=0.9, status="high")


def _boom(*_a, **_k):
    raise AssertionError("不应触发扫描（sidecar 应命中）")


# ---------- 全片簇扫描（合成视频：网摆帧 88~112 → t≈2.93~3.73） ----------

def test_扫描找到网摆簇(sample_video):
    clusters = nc.scan_net_clusters(sample_video, None, [(0.0, CAL)], {})
    assert len(clusters) == 1
    cl = clusters[0]
    # 球下落穿网区（帧 60~90 → t≈2.2 起）与网摆（帧 88~112 → t≤3.8）是
    # 同一进球事件的连续网动窗口，聚成一簇
    assert 2.1 <= cl["t0"] <= 2.4
    assert 3.7 <= cl["t1"] <= 3.85
    assert cl["peak"] >= nc.NET_STATIC_TH
    assert cl["npairs"] >= 6


def test_视频打不开时fail_open返回空():
    assert nc.scan_net_clusters("nope://missing.mp4", None, [(0.0, CAL)], {}) == []


# ---------- sidecar：身份指纹 / 多 entry 共存 ----------

def test_sidecar身份与多entry共存(tmp_path, sample_video):
    segs = [(0.0, CAL)]
    cl = [{"t0": 1.0, "t1": 2.0, "peak": 9.0, "npairs": 3}]
    nc.save_clusters(str(tmp_path), sample_video, segs, cl)
    assert nc.load_clusters(str(tmp_path), sample_video, segs) == cl
    # 换筐 → 身份不符 → None（=需要重扫）
    other = Calibration(hoop_rect=(400, 140, 80, 40), hsv_low=(0, 80, 120),
                        hsv_high=(25, 255, 255), frame_size=(640, 360))
    assert nc.load_clusters(str(tmp_path), sample_video, [(0.0, other)]) is None
    # 写入新身份后，旧身份 entry 原样保留（多段/重标定共存）
    nc.save_clusters(str(tmp_path), sample_video, [(0.0, other)], [])
    assert nc.load_clusters(str(tmp_path), sample_video, segs) == cl
    assert nc.load_clusters(str(tmp_path), sample_video, [(0.0, other)]) == []
    # 缓存命中的空簇表返回 []（已扫过无簇），与 None（未扫）区分


def test_sidecar缺失或损坏返回None(tmp_path, sample_video):
    assert nc.load_clusters(str(tmp_path), sample_video, [(0.0, CAL)]) is None
    (tmp_path / nc.NET_CLUSTERS_FILE).write_text("{ 半截", encoding="utf-8")
    assert nc.load_clusters(str(tmp_path), sample_video, [(0.0, CAL)]) is None


# ---------- 残迹链过滤器（纯函数） ----------

def test_残迹链接受严格下行链():
    pts = [_pos(10.0, 340, 170), _pos(10.3, 340, 190), _pos(10.6, 341, 215)]
    ch = nc.residue_chain(pts, CAL, 10.6)     # 窗 [9.4, 10.9]
    assert ch is not None and len(ch) == 3


def test_残迹链两点不构成():
    pts = [_pos(10.0, 340, 170), _pos(10.3, 340, 190)]
    assert nc.residue_chain(pts, CAL, 10.3) is None


def test_残迹链跨度超限不构成():
    # 锚 10.9 → 窗 [9.7, 11.2]，链 9.7→11.1 跨度 1.4 > 1.2
    pts = [_pos(9.7, 340, 170), _pos(10.4, 340, 190), _pos(11.1, 341, 215)]
    assert nc.residue_chain(pts, CAL, 10.9) is None


def test_残迹链出横向界不构成():
    # 斜扫形态：链后半漂出右界 396（B 轮 5904.6 的拦截点）
    pts = [_pos(10.0, 380, 170), _pos(10.3, 394, 190), _pos(10.6, 410, 215)]
    assert nc.residue_chain(pts, CAL, 10.6) is None


def test_残迹链带上点不参与():
    # y ≤ rim+0.25hh=164 的带内点不进窗 → 只剩 2 个带下点
    pts = [_pos(10.0, 340, 160), _pos(10.3, 340, 175), _pos(10.6, 340, 195)]
    assert nc.residue_chain(pts, CAL, 10.6) is None


def test_残迹链上行重置取最长段():
    # 中途上行断链 → 最长严格下行段只有 2 点
    pts = [_pos(10.0, 340, 170), _pos(10.3, 340, 190), _pos(10.6, 340, 185),
           _pos(10.9, 340, 210)]
    assert nc.residue_chain(pts, CAL, 10.9) is None
    # 加长下行段后按最长段成立
    pts.append(_pos(11.2, 340, 235))
    ch = nc.residue_chain(pts, CAL, 11.2)
    assert ch is not None and len(ch) == 3   # [185,210,235]


# ---------- 注入锚时刻（09-23 纪律：positions 内插穿筐点） ----------

def test_锚内插上方弧末点过rim平面():
    pts = [_pos(9.5, 340, 120), _pos(9.9, 340, 140),
           _pos(10.0, 340, 170), _pos(10.3, 340, 190), _pos(10.6, 341, 215)]
    ch = nc.residue_chain(pts, CAL, 10.6)
    t = nc.inject_anchor_t(ch, CAL, pts)
    want = 9.9 + (RIM_Y - 140) / (170 - 140) * (10.0 - 9.9)
    assert abs(t - want) < 1e-6


def test_无上方点退链首时刻():
    pts = [_pos(10.0, 340, 170), _pos(10.3, 340, 190), _pos(10.6, 341, 215)]
    ch = nc.residue_chain(pts, CAL, 10.6)
    assert nc.inject_anchor_t(ch, CAL, pts) == ch[0].t


# ---------- 注入 pass ----------

def _prepare(tmp_path, sample_video, clusters):
    nc.save_clusters(str(tmp_path), sample_video, [(0.0, CAL)], clusters)


CHAIN_PTS = [_pos(10.0, 340, 170), _pos(10.3, 340, 190), _pos(10.6, 341, 215)]


def test_注入review行追加表尾(tmp_path, monkeypatch, sample_video):
    _prepare(tmp_path, sample_video, [{"t0": 10.0, "t1": 10.6, "peak": 9.0,
                                       "npairs": 3}])
    monkeypatch.setattr(nc, "scan_net_clusters", _boom)   # sidecar 命中零扫描
    events = [_ev(50.0)]
    out = nc.net_cluster_pass(sample_video, str(tmp_path), [(0.0, CAL)], {},
                              CHAIN_PTS, events)
    assert len(out) == 2
    assert out[0] is events[0]                          # 旧行原样、次序不变
    row = out[1]
    assert row.status == "review"
    assert row.t == pytest.approx(10.0)                 # 无上方点 → 链首锚
    assert row.above_n == 0 and row.below_n == 3        # 注入行指纹
    # 置信度=主判定器同款公式（2026-10-11 问题①：曾硬编码 0.30）：
    # 0.3 基础 + below 侧分（3 点/0.6s 连续 → 0.20）+ 位置分（off=0 → 0.20）
    assert row.confidence == 0.70
    assert row.cross_off is not None                    # UI 依据文案可渲染
    assert row.review_reason == "b2"         # v18：注入行自带硬门（恒 review）


def test_注入行置信度按链证据计算(tmp_path, monkeypatch, sample_video):
    # 2026-10-11 问题①的实况形状（2026-10-10_1 的 10:43.8）：8 点 /
    # span 0.8s / off −0.35 → 0.3 + 0.20（below 连续满档）+ 0.10（0.3<off≤0.4）
    # = 0.60。旧硬编码版此处恒 0.30，正是用户报的那类错误。
    _prepare(tmp_path, sample_video, [{"t0": 10.0, "t1": 10.8, "peak": 9.0,
                                       "npairs": 8}])
    monkeypatch.setattr(nc, "scan_net_clusters", _boom)
    # head.x=312 → off=(312−340)/80=−0.35；8 点严格下行、跨度 0.8s
    pts = [_pos(10.0 + i * 0.8 / 7, 312 + i, 170 + i * 9) for i in range(8)]
    out = nc.net_cluster_pass(sample_video, str(tmp_path), [(0.0, CAL)], {},
                              pts, [_ev(50.0)])
    row = out[1]
    assert row.below_n == 8 and row.below_span == pytest.approx(0.8)
    assert row.cross_off == pytest.approx(-0.35, abs=1e-3)
    assert row.confidence == 0.60
    # 公式达 0.70（≥HIGH_CONF 0.65）也不升档：注入行恒 review（宁多勿漏裁定）
    assert row.status == "review"


def test_簇附近有既有行不注入(tmp_path, sample_video):
    _prepare(tmp_path, sample_video, [{"t0": 10.0, "t1": 10.6, "peak": 9.0,
                                       "npairs": 3}])
    events = [_ev(12.5)]        # 12.5 ∈ [10.0-2.5, 10.6+2.5]
    out = nc.net_cluster_pass(sample_video, str(tmp_path), [(0.0, CAL)], {},
                              CHAIN_PTS, events)
    assert out is events


def test_簇附近有已删键不注入(tmp_path, sample_video):
    # 删除=策展出局，不是漏检（09-19 有 5 个漂移删键簇，不能借 B2 还魂）
    _prepare(tmp_path, sample_video, [{"t0": 10.0, "t1": 10.6, "peak": 9.0,
                                       "npairs": 3}])
    (tmp_path / nc.DELETED_FILE).write_text(json.dumps(["0:11.5"]),
                                            encoding="utf-8")
    events = [_ev(50.0)]
    out = nc.net_cluster_pass(sample_video, str(tmp_path), [(0.0, CAL)], {},
                              CHAIN_PTS, events)
    assert out is events


def test_注入行彼此3秒去重(tmp_path, sample_video):
    _prepare(tmp_path, sample_video, [
        {"t0": 10.0, "t1": 10.6, "peak": 9.0, "npairs": 3},
        {"t0": 12.2, "t1": 12.8, "peak": 8.0, "npairs": 2}])
    pts = CHAIN_PTS + [_pos(12.2, 340, 170), _pos(12.5, 340, 190),
                       _pos(12.8, 341, 215)]
    out = nc.net_cluster_pass(sample_video, str(tmp_path), [(0.0, CAL)], {},
                              pts, [_ev(50.0)])
    assert len(out) == 2                     # 第二簇锚 12.2 距 10.0 <3s 让位


def test_残迹不成立不注入(tmp_path, sample_video):
    _prepare(tmp_path, sample_video, [{"t0": 10.0, "t1": 10.6, "peak": 9.0,
                                       "npairs": 3}])
    events = [_ev(50.0)]
    out = nc.net_cluster_pass(sample_video, str(tmp_path), [(0.0, CAL)], {},
                              [_pos(10.0, 340, 170)], events)
    assert out is events


def test_无簇fail_open原样返回(tmp_path):
    # 视频打不开 → 扫描得空簇表落 sidecar → 原样返回（fail-open）
    events = [_ev(1.0)]
    out = nc.net_cluster_pass("nope://missing.mp4", str(tmp_path),
                              [(0.0, CAL)], {}, CHAIN_PTS, events)
    assert out is events


def test_注入行补net实测仅显示_分值档位不动(tmp_path, monkeypatch,
                                          sample_video):
    # v18 spec §3.5：注入行 net_* 落实测值（UI 展示），但网分按 0——簇
    # 触发判据本身就是同款局部性网动，实测窗（t+0.45~0.95s）可能错过摆动
    # 相位，测出「静止」再扣 −0.20 是对同一证据的双重惩罚
    _prepare(tmp_path, sample_video, [{"t0": 10.0, "t1": 10.6, "peak": 9.0,
                                       "npairs": 3}])
    monkeypatch.setattr(nc, "scan_net_clusters", _boom)
    monkeypatch.setattr("bbh.net_motion.measure_net_signals",
                        lambda *_a, **_k: {"10.00": {"post": 2.1,
                                                     "ctrl": 2.0,
                                                     "local": False}})
    out = nc.net_cluster_pass(sample_video, str(tmp_path), [(0.0, CAL)], {},
                              CHAIN_PTS, [_ev(50.0)])
    row = out[1]
    assert row.net_post == 2.1 and row.net_ctrl == 2.0
    assert row.net_local is False
    assert row.confidence == 0.70 and row.status == "review"   # 不扣不加
    # 实测值落 sidecar：重放路径吃缓存不再重测
    doc = json.loads((tmp_path / "net_signals.json").read_text(encoding="utf-8"))
    assert any(e["identity"]["hoop"] == [300, 140, 80, 40] for e in doc["entries"])


# ---------- 管线 E2E：挂点在 merge 之后、旧行零变化 ----------

def test_管线全跑产出网簇sidecar且不注入既有行邻域(sample_video,
                                                sample_calibration, tmp_path):
    events_path, _ = run_detection(sample_video, sample_calibration,
                                   work_dir=str(tmp_path))
    data = json.loads((tmp_path / "events.json").read_text(encoding="utf-8"))
    # 动态比（同 test_pipeline 约定）：这里验的是「落盘版本号 == 代码常量」
    # （缓存身份自洽）；常量**取值**由 README 版本表与升版纪律守，硬编码
    # 只会让每次升版多一条无信息量的红。
    assert data["logic_ver"] == LOGIC_VERSION
    sidecar = json.loads((tmp_path / nc.NET_CLUSTERS_FILE)
                         .read_text(encoding="utf-8"))
    assert sidecar["entries"]                     # 扫描结果已落 sidecar
    # 合成视频唯一网摆簇 [≈3.0, ≈3.8] 距事件行 t≈2.5 在 ±2.5s 对账窗内
    # → 不注入：事件数与既有行为一致（above_n≥1=非注入行指纹）
    events = data["events"]
    assert len(events) == 1
    assert events[0]["above_n"] >= 1
