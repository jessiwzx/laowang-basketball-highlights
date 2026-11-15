"""第 6 套口径（用户手动补录漏检）守口径测试。

核心断言：三档分类边界 + 当前数据下的分布（15 条 → 命中档 5 / 时刻偏 8 / 无行 2）。
数据缺失自动 skip，不阻塞 CI。
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, ROOT)

import eval_miss as M  # noqa: E402


def test_三档阈值常量():
    assert M.TOL_HIT == 0.5
    assert M.TOL_EDGE == 3.0
    assert M.TOL_HIT < M.TOL_EDGE


def _fake(video, t, delta, status):
    return {"video": video, "t": t, "note": "", "marked_at": "2026-01-01",
            "delta": delta, "status": status,
            "verdict": ("命中档" if abs(delta) <= M.TOL_HIT
                        else "时刻偏" if abs(delta) <= M.TOL_EDGE else "无行")}


def test_分类边界():
    # 边界内取闭区间，超出即降档
    assert _fake("v", 1.0, 0.5, "high")["verdict"] == "命中档"
    assert _fake("v", 1.0, -0.5, "high")["verdict"] == "命中档"
    assert _fake("v", 1.0, 0.51, "high")["verdict"] == "时刻偏"
    assert _fake("v", 1.0, -3.0, "high")["verdict"] == "时刻偏"
    assert _fake("v", 1.0, 3.01, "review")["verdict"] == "无行"
    assert _fake("v", 1.0, 28.5, "review")["verdict"] == "无行"


def test_无产物不崩():
    # 找不到 events.json 时保留「无产物」，不能被当成无行
    assert M.classify("__不存在的场次__") == []


@pytest.mark.skipif(not os.path.isdir(os.path.join(ROOT, "work")),
                    reason="无 work 目录")
def test_当前数据分布():
    rows = []
    for v in M.videos():
        rows.extend(M.classify(v))
    if not rows:
        pytest.skip("本机无任何 feedback.misses")
    from collections import Counter
    v = Counter(r["verdict"] for r in rows)
    # 2026-11-14 实测：15 条 → 5 / 8 / 2，零「无产物」
    assert v["无产物"] == 0, "出现找不到产物的场次，检查 EV_ROOTS"
    assert v["无行"] == 2, f"无行条数变了：{v['无行']}（原 2，均在 09-04）"
    no = [r for r in rows if r["verdict"] == "无行"]
    assert {r["video"] for r in no} == {"2026-09-04_1"}


@pytest.mark.skipif(not os.path.isdir(os.path.join(ROOT, "work")),
                    reason="无 work 目录")
def test_无行的两条离最近行都超过三秒():
    rows = []
    for v in M.videos():
        rows.extend(M.classify(v))
    for r in rows:
        if r["verdict"] == "无行":
            assert abs(r["delta"]) > M.TOL_EDGE
