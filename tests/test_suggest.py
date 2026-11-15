# tests/test_suggest.py
"""suggest.py 单测：合成指纹（段内 Σ=1 的 {bin:权重} 构造，口径同
test_appearance 夹具——one-hot/混合不受 _hist_sim L1 修复影响）。"""
import pytest

from bbh.appearance import Appearance
from bbh.suggest import (Suggestion, color_neighbors, evaluate_neighbors,
                         suggest_groups, wall_order)


def _apph(top=None, pants=None, shoes=None, ok=True):
    """top/pants/shoes: {bin: 权重}（缺省该段 one-hot bin0）。"""
    def mix(d):
        d = d or {0: 1.0}
        h = [0.0] * 1024
        for b, w in d.items():
            h[b] = w
        return h
    return Appearance(ok=ok, hists={"top": mix(top), "pants": mix(pants),
                                    "shoes": mix(shoes)},
                      body_ratio=4.0, aspect=0.3, head_hist=None,
                      missing=[], err=None)


def test_color_neighbors_排序与剔除():
    feats = {"a": _apph(top={10: 1.0}), "b": _apph(top={10: 0.6, 11: 0.4}),
             "c": _apph(top={500: 1.0}), "d": _apph(top={10: 0.8, 12: 0.2})}
    nbrs = color_neighbors("a", feats, None, k=2)
    assert [k for k, _ in nbrs] == ["d", "b"]          # 0.8 > 0.6（top 段）
    assert nbrs[0][1] >= nbrs[1][1] > 0
    # 手动已归人的球不再出现（护栏 3：手动永远赢）
    nbrs2 = color_neighbors("a", feats, {"老陈": ["d"]}, k=5)
    assert "d" not in [k for k, _ in nbrs2]
    assert "a" not in [k for k, _ in nbrs2]            # 目标自身剔除
    assert color_neighbors("x", feats) == []           # 目标不在 feats
    bad = dict(feats); bad["e"] = _apph(ok=False)      # 缺失特征跳过
    assert "e" not in [k for k, _ in color_neighbors("a", bad)]


def test_suggest_groups_锚球与恒low():
    # key 用真实口径 m+:ss.s（锚球按 _goal_t 解析时刻排序，同 build_groups/
    # gap_violations 契约——不支持任意字符串 key）
    feats = {"01:00.0": _apph(top={10: 1.0}),
             "02:00.0": _apph(top={10: 0.6, 11: 0.4}),
             "03:00.0": _apph(top={500: 1.0})}
    assert suggest_groups(feats, None) == []           # 无人已归人 → 无建议
    out = suggest_groups(feats, {"老陈": ["01:00.0"]}, k=1)
    assert len(out) == 1 and out[0].person == "老陈"
    assert out[0].confidence == "low" and out[0].signal == "color_rank"
    assert out[0].balls == ["02:00.0"]          # 最像锚 01:00.0 的未归人球
    assert out[0].evidence["anchor"] == "01:00.0"


def test_suggest_groups_锚球按解析时刻排():
    """字典序取"最新"跨 100 分钟错乱（"110:00.0"<"32:00.0"）——锚取错建议
    全错。按 _goal_t 解析时刻排（同 gap_violations 091ae17 教训）。"""
    feats = {"32:00.0": _apph(top={10: 1.0}),
             "110:00.0": _apph(top={5: 0.9, 6: 0.1}),
             "y": _apph(top={5: 0.8, 6: 0.2}),      # 像 110 不像 32
             "z": _apph(top={10: 0.9, 11: 0.1})}    # 像 32 不像 110
    out = suggest_groups(feats, {"甲": ["32:00.0", "110:00.0"]}, k=1)
    assert out[0].evidence["anchor"] == "110:00.0"  # 真最新（6600s > 1920s）
    assert out[0].balls == ["y"]                    # 字典序锚取 32 会得 ["z"]


def test_evaluate_neighbors_真值重放():
    feats = {"a": _apph(top={10: 1.0}), "b": _apph(top={10: 0.9, 11: 0.1}),
             "c": _apph(top={500: 1.0})}
    truth = {"a": "甲", "b": "甲", "c": "乙"}
    r = evaluate_neighbors(feats, truth, k=1)
    assert r["checked"] == 3 and r["k"] == 1
    assert r["hit_rate"] == pytest.approx(2 / 3)       # a↔b 同人互中，c 落空
    assert r["precision_at_k"] == pytest.approx(2 / 3)
    assert evaluate_neighbors({}, truth)["checked"] == 0


# ---------- 未归人缩略图墙排序（Phase 2 补充：墙只排序不定身份） ----------

def test_wall_order_时间序按解析时刻并剔除已归人与无缩略图():
    traces = {"32:00.0": {"thumbs": ["a.jpg"]},
              "110:00.0": {"thumbs": ["b.jpg"]},
              "05:00.0": {"thumbs": []},            # 无缩略图不进墙
              "07:00.0": {"thumbs": ["c.jpg"]}}
    # 解析时刻升序（非字典序——"110"<"32" 跨 100 分钟坑，091ae17 教训）
    assert wall_order(None, traces, None, "time") == \
        ["07:00.0", "32:00.0", "110:00.0"]
    assert wall_order(None, traces, {"甲": ["07:00.0"]}, "time") == \
        ["32:00.0", "110:00.0"]                     # 已归人球剔除
    assert wall_order(None, {}, None, "time") == []


def test_wall_order_出手点排序无锚垫底():
    traces = {"01:00.0": {"thumbs": ["a.jpg"], "release_xy": [900, 100]},
              "02:00.0": {"thumbs": ["b.jpg"], "release_xy": [100, 300]},
              "03:00.0": {"thumbs": ["c.jpg"], "release_xy": [100, 200]},
              "04:00.0": {"thumbs": ["d.jpg"]}}     # 无锚点垫底（按时间）
    assert wall_order(None, traces, None, "spot") == \
        ["03:00.0", "02:00.0", "01:00.0", "04:00.0"]


def test_wall_order_外观贪心链无特征垫底():
    feats = {"01:00.0": _apph(top={10: 1.0}),
             "02:00.0": _apph(top={10: 0.9, 11: 0.1}),
             "03:00.0": _apph(top={500: 1.0}),
             "04:00.0": _apph(top={500: 0.9, 501: 0.1})}
    traces = {k: {"thumbs": [k + ".jpg"]} for k in feats}
    traces["05:00.0"] = {"thumbs": ["e.jpg"]}
    traces["06:00.0"] = {"thumbs": ["f.jpg"]}
    bad = dict(feats); bad["06:00.0"] = _apph(ok=False)
    out = wall_order(bad, traces, None, "appearance")
    # 链头=最早可用特征球；像的相邻（10 号族 01→02，500 号族 03→04，
    # 相似度并列时按 key 决胜负保确定序）；无/坏特征球按时间垫底
    assert out == ["01:00.0", "02:00.0", "03:00.0", "04:00.0",
                   "05:00.0", "06:00.0"]
    assert wall_order({}, traces, None, "appearance") == \
        wall_order(None, traces, None, "time")      # 无特征退化时间序
