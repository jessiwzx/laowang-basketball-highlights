# tests/test_appearance.py
"""appearance.py 单测：全部合成数据（合成掩码/合成指纹/合成小视频），
不依赖真视频——真值验收走 tools/check_shooters.py 与标定轮。"""
import json

import cv2
import numpy as np
import pytest

from bbh.appearance import (Appearance, FEAT_VERSION, features_valid,
                            load_features, person_mask, save_features)

from bbh.models import Calibration

CALIB = Calibration(hoop_rect=(300, 140, 80, 40), hsv_low=(0, 80, 120),
                    hsv_high=(25, 255, 255), frame_size=(640, 360))

W2, H2, FPS2, N2 = 640, 360, 25, 200
HOOP_W2 = 80          # conftest 筐宽口径


def _person_rect(i):
    """帧 i 的人形：40x150 竖块从 x=100 移到 x=500（背景中值=纯背景）。
    高 150 ≥ PERSON_MIN_H_W×筐宽=1.6×80=128——矮了会被碎片过滤。"""
    x = 100 + int(i * 2.2)
    return x, 130, 40, 150


def make_person_video(path):
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), FPS2, (W2, H2))
    for i in range(N2):
        img = np.full((H2, W2, 3), (40, 44, 50), np.uint8)
        x, y, w, h = _person_rect(i)
        cv2.rectangle(img, (x, y), (x + w, y + 40), (40, 40, 200), -1)   # 红上衣
        cv2.rectangle(img, (x, y + 40), (x + w, y + h), (200, 120, 40), -1)  # 蓝裤
        vw.write(img)
    vw.release()
    return path


@pytest.fixture(scope="session")
def person_video(tmp_path_factory):
    return make_person_video(str(tmp_path_factory.mktemp("pv") / "person.mp4"))


def test_指纹序列化往返(tmp_path):
    a = Appearance(ok=True, hists={"top": [0.5] * 1024, "pants": [0.2] * 1024,
                                   "shoes": [0.1] * 1024},
                   body_ratio=4.2, aspect=0.28, head_hist=[0.9] * 32,
                   missing=["shoes"], err=None)
    save_features(str(tmp_path), "vid.mp4", CALIB, {"01:23.7": a})
    d = load_features(str(tmp_path))
    assert features_valid(d, "vid.mp4", CALIB)
    a2 = Appearance.from_dict(d["feats"]["01:23.7"])
    assert a2.ok and a2.body_ratio == pytest.approx(4.2) and a2.missing == ["shoes"]


def test_身份不符作废(tmp_path):
    save_features(str(tmp_path), "vid.mp4", CALIB, {})
    assert not features_valid(load_features(str(tmp_path)), "other.mp4", CALIB)


def test_缺失档容错(tmp_path):
    assert load_features(str(tmp_path)) is None          # 文件不存在
    (tmp_path / "features.json").write_text("{截断", encoding="utf-8")
    assert load_features(str(tmp_path)) is None          # 损坏不抛


def test_移动人被检出为目标(person_video, tmp_path):
    i = 100                                  # t = 4.0s
    t = i / FPS2
    x, y, w, h = _person_rect(i)
    res = person_mask(person_video, str(tmp_path), t, (x + w / 2, y + 30),
                      HOOP_W2)
    assert res is not None
    mask, (bx, by, bw, bh), frame = res
    assert frame.shape == (360, 640, 3)             # 目标帧随掩码返回（Task 3 用）
    assert abs(bx - x) <= 6 and abs(by - y) <= 6     # bbox 贴住人
    assert bh >= 80 and bw >= 34
    assert mask[bh // 2, bw // 2]                   # 中心在人身上


def test_静止人被中值洗掉返回None(tmp_path_factory, tmp_path):
    """人整窗不动 → 烧进中值背景 → 找不到前景 → None（宁缺勿错）。"""
    p = str(tmp_path_factory.mktemp("pv2") / "static.mp4")
    vw = cv2.VideoWriter(p, cv2.VideoWriter_fourcc(*"mp4v"), FPS2, (W2, H2))
    for _ in range(N2):
        img = np.full((H2, W2, 3), (40, 44, 50), np.uint8)
        cv2.rectangle(img, (300, 130), (340, 280), (40, 40, 200), -1)
        vw.write(img)
    vw.release()
    assert person_mask(p, str(tmp_path), 4.0, (320, 160), HOOP_W2) is None


def test_出手点旁小碎片不抢目标(person_video, tmp_path):
    """出手点不落在任何域上 → 取 2 筐宽内最近的域（小碎片 <1.6 筐高被
    PERSON_MIN_H_W 滤掉）。"""
    i = 120
    t = i / FPS2
    x, y, w, h = _person_rect(i)
    res = person_mask(person_video, str(tmp_path), t, (x + w + 100, y + 60),
                      HOOP_W2)
    assert res is not None and abs(res[1][0] - x) <= 6


from bbh.appearance import (_strip_skin, describe_person, SEG_MIN_PX)


def _person_frame_mask():
    """200x90 合成人：0-50% 红上衣、50-90% 蓝裤、88-100% 白鞋，含肤色调块。"""
    frame = np.full((90, 200, 3), (40, 44, 50), np.uint8)
    frame[0:45, :] = (40, 40, 200)        # 红上衣
    frame[45:72, :] = (200, 120, 40)      # 蓝裤
    frame[79:90, :] = (240, 240, 240)     # 白鞋
    frame[30:40, 90:110] = (110, 140, 175) # 肤色块（低饱和手臂）
    mask = np.ones((90, 200), bool)
    return frame, mask


def test_皮肤剔除保衣服剔肤色():
    hsv = cv2.cvtColor(_person_frame_mask()[0], cv2.COLOR_BGR2HSV)
    skin = _strip_skin(hsv)
    assert skin[35, 100]                       # 肤色块命中
    assert not skin[20, 20]                    # 高饱和红衣不误剔


def test_三段指纹与缺失段():
    frame, mask = _person_frame_mask()
    app = describe_person(frame, mask, (0, 0, 200, 90), 50.0)
    assert app.ok and not app.missing
    top = np.array(app.hists["top"]).reshape(32, 32)
    # 上衣主导：top 段直方图质量集中在 H 低 bin（红 H≈2/180*32≈0）
    assert top[0:3, :].sum() > 0.5
    pants = np.array(app.hists["pants"]).reshape(32, 32)
    assert pants.sum() == pytest.approx(1.0)    # 每段归一化
    assert app.head_hist is not None and len(app.head_hist) == 32
    assert app.body_ratio == pytest.approx(90 / 50.0)   # 掩码高/筐宽
    assert app.aspect == pytest.approx(90 / 200.0)


def test_段像素不足记缺失():
    frame, mask = _person_frame_mask()
    mask2 = mask.copy()
    mask2[79:90, :] = False                    # 鞋段全灭
    app = describe_person(frame, mask2, (0, 0, 200, 90), 50.0)
    assert "shoes" in app.missing and app.hists["shoes"] is None


def test_掩码bbox局部坐标_非原点bbox不炸():
    """person_mask 返回 bbox 局部掩码（bh×bw）——describe_person 须在局部
    坐标切段（Task 4 直接接线，回归防护）。"""
    frame, mask = _person_frame_mask()          # 90x200 合成人
    big = np.full((240, 320, 3), (40, 44, 50), np.uint8)
    big[100:190, 60:260] = frame                # 人平移到 (60,100)
    app = describe_person(big, mask, (60, 100, 200, 90), 50.0)
    assert app.ok and not app.missing
    assert app.body_ratio == pytest.approx(90 / 50.0)


from bbh.appearance import build_features, features_valid


def test_编排逐球写盘与续跑(person_video, tmp_path):
    traces = {"04:00.0": {"status": "high", "release_t": 4.0,
                          "release_xy": [340, 160], "thumbs": []},
              "04:04.0": {"status": "review", "release_t": 4.0,
                          "release_xy": [340, 160], "thumbs": []}}
    gen = build_features(person_video, CALIB, str(tmp_path), traces)
    msgs = list(gen)                     # 消费生成器（return 值丢弃，StopIteration 已被 list 吞）
    assert len(msgs) == 2
    d = load_features(str(tmp_path))
    assert features_valid(d, str(person_video), CALIB)
    assert set(d["feats"]) == {"04:00.0", "04:04.0"}
    assert d["feats"]["04:00.0"]["ok"]           # 合成人提到指纹
    # 续跑：缓存齐全 → 0 条新进度
    assert list(build_features(person_video, CALIB, str(tmp_path), traces)) == []


from bbh.appearance import (EDGE_SIM, _hist_sim, build_groups, feat_similar,
                            gap_violations, groups_valid, head_splits,
                            load_groups, save_groups)


def _hot(n=1024, at=0):
    """one-hot 归一化直方图（bin at=1.0）——合成指纹的距离可控。"""
    v = [0.0] * n
    v[at % n] = 1.0
    return v


def _mix(n=1024, a=0, b=130, wa=0.5):
    """两 bin 混合直方图——链式复检测试用（A-B、B-C 相似，A-C 不相似）。"""
    v = [0.0] * n
    v[a % n], v[b % n] = wa, 1.0 - wa
    return v


def _app(hist_top, body=4.0, head=None, ok=True):
    return Appearance(ok=ok,
                      hists={"top": hist_top, "pants": _hot(1024, 500),
                             "shoes": _hot(1024, 900)},
                      body_ratio=body, aspect=0.28, head_hist=head,
                      missing=[])


def test_相似度同高异低():
    a = _app(_hot())
    assert feat_similar(a, _app(_hot())) == pytest.approx(1.0, abs=0.02)
    # top 不同 bin（0 交集）、pants/shoes 同 bin：加权和=0.30+0.15=0.45，
    # 远低于连边阈值 0.70
    assert feat_similar(a, _app(_hot(1024, 300))) < 0.55 < EDGE_SIM


def test_头部票只拆不合():
    h1 = [0.9] + [0.0] * 31
    h2 = [0.0] * 31 + [0.9]
    # 巴氏距离：不相交≈1（明显不同→拆）、相同≈0（不拆）
    assert head_splits(_app(_hot(), head=h1), _app(_hot(), head=h2))   # 异发色→拆
    assert not head_splits(_app(_hot(), head=h1), _app(_hot(), head=h1))
    assert not head_splits(_app(_hot(), head=None), _app(_hot(), head=h1))  # 缺票不拆


def test_聚类连边成组_时空拆分_review只挂不建():
    traces, feats = {}, {}
    spec = [("01:00.0", 0), ("01:40.0", 0), ("02:20.0", 0),    # A 三球同指纹
            ("03:00.0", 400), ("03:30.0", 400),                # B 两球
            ("03:00.5", 400)]        # 与 B 撞衫且与 03:00.0 同刻——时空必拆
    for key, bin_ in spec:
        traces[key] = {"status": "high"}
        feats[key] = _app(_hot(1024, bin_))
    traces["01:10.0"] = {"status": "review"}   # review：A 的指纹，只挂不建
    feats["01:10.0"] = _app(_hot())
    out = build_groups(traces, feats)
    gs = [g for g in out if len(g["members"]) > 1]
    assert len(gs) == 2                          # A 组 3+1 球、B 组 2 球
    g_a = next(g for g in gs if "01:00.0" in g["members"])
    assert set(g_a["members"]) == {"01:00.0", "01:40.0", "02:20.0", "01:10.0"}
    assert g_a["status"] == "suspect"            # 含 review 挂入→标黄提示
    g_b = next(g for g in gs if "03:00.0" in g["members"])
    assert set(g_b["members"]) == {"03:00.0", "03:30.0"}
    solo = [g for g in out if g["members"] == ["03:00.5"]]
    assert solo and solo[0]["status"] == "ok"    # 时空拆出的 high 单列（非 pending）


def test_链式复检拆最不像():
    """A-B 相似(0.725)、B-C 相似(0.725)、A-C 不相似(0.45) → 传递并入后
    复检把 A/C 拆出，最终无 3 球同组。"""
    traces, feats = {}, {}
    for key, h in [("01:00.0", _hot()), ("01:30.0", _mix()),
                   ("02:00.0", _hot(1024, 130))]:
        traces[key] = {"status": "high"}
        feats[key] = _app(h)
    out = build_groups(traces, feats)
    assert not [g for g in out if len(g["members"]) == 3]
    # 复检后：{B,C} 一组（两两 ≥CHAIN_SIM），A 单列
    assert any(set(g["members"]) == {"01:30.0", "02:00.0"} for g in out)


def test_时空拆桥后两人组仍复检():
    """桥 B 被时空硬约束拆走后，幸存 {A,C} 相似度 0.45 < CHAIN_SIM——
    链式复检不得在 len==2 停（宁拆勿错并）。"""
    traces, feats = {}, {}
    for key, h in [("03:00.0", _hot()), ("03:00.5", _mix()),
                   ("05:00.0", _hot(1024, 130))]:
        traces[key] = {"status": "high"}
        feats[key] = _app(h)
    out = build_groups(traces, feats)
    for g in out:
        assert len(g["members"]) == 1    # 0.45 的两人组必须被拆开


def test_groups_json_往返(tmp_path):
    traces = {"01:00.0": {"status": "high"}}
    feats = {"01:00.0": _app(_hot())}
    out = build_groups(traces, feats)
    save_groups(str(tmp_path), "v.mp4", CALIB, out)
    d = load_groups(str(tmp_path))
    assert groups_valid(d, "v.mp4", CALIB)
    assert d["groups"][0]["members"] == ["01:00.0"]


def test_hist_sim_契约_交集前L1归一化():
    # _seg_hist 实际产出是 L2 归一化：均匀分布 Σbin=√32>1，裸交集破 [0,1]
    h = np.full(32, 1.0 / np.sqrt(32), dtype=np.float32)
    assert _hist_sim(h, h) == pytest.approx(1.0)          # 自身=1（修复前=√32）
    g = np.zeros(32, dtype=np.float32); g[0] = 2.0        # 非归一化 one-hot
    z = np.zeros(32, dtype=np.float32); z[1] = 3.0        # 不同 bin
    assert _hist_sim(g, z) == pytest.approx(0.0)
    assert _hist_sim(g, g) == pytest.approx(1.0)          # 与幅度无关
    assert _hist_sim(g, h) <= 1.0 + 1e-6                  # 恒 ≤1


def test_gap_violations_护栏():
    keys = ["01:00.0", "01:02.0", "05:00.0"]
    assert gap_violations(keys) == [("01:00.0", "01:02.0", 2.0)]  # 2.0s<2.5
    assert gap_violations(["01:00.0", "01:03.3"]) == []  # 3.3s=两场同人最短
    assert gap_violations(["01:00.0", "01:01.0", "01:01.5"]) == [
        ("01:00.0", "01:01.0", 1.0), ("01:01.0", "01:01.5", 0.5)]
    # 跨 100 分钟的 key 字典序≠时间序——必须按时刻排，不能按字符串排
    assert gap_violations(["110:40.7", "32:41.0"]) == []      # 相差 78 分钟
    assert gap_violations(["99:59.0", "100:01.0"]) == [("99:59.0", "100:01.0", 2.0)]
    assert gap_violations([]) == []
