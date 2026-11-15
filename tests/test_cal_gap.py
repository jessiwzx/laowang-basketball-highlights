# -*- coding: utf-8 -*-
"""孔洞告警两档（v22）——30s 轻提示 / 180s 重警告，且不叠报。

背景：180s 那档的文案是「判定不可信、建议分段重跑」级重话，套在 30s 上会
把暗场自然断流变成遍地误报（08-21_1 实测 112.8s）；反过来 180s 又太钝，
真被挪过镜头的场次常在 40~70s 就断流（10-16_1 41.3min 断 70s，旧版全程
静默）。所以拆两档：同一段只出一句，重话优先。
"""
import pytest

from bbh.pipeline import (_cal_gap_note, _cal_holes, _fmt_eras,
                          CAL_GAP_HINT_NOTE, CAL_GAP_NOTE, CAL_GAP_HINT_SEC,
                          CAL_GAP_WARN_SEC, CAL_NO_SAMPLE_NOTE)


def _dense(t0, t1, step=6.0):
    out, t = [], t0
    while t <= t1 + 1e-9:
        out.append((round(t, 3), 0.0, 0.0))
        t += step
    return out


def test_两档常量取值():
    assert CAL_GAP_HINT_SEC == 30.0
    assert CAL_GAP_WARN_SEC == 180.0


def test_正常密采样既不提示也不警告():
    assert _cal_gap_note(_dense(0, 595), 0, 600, True) is None


def test_短段不报():
    """段窗短于 30s 本就不够一个采样周期，不算断流。"""
    assert _cal_gap_note([], 0, 20, True) is None


def test_中等断流出轻提示并带时刻与秒数():
    """10-16_1 形态：1:00 后断到 2:10（70s）→ 轻提示，不得用重话。"""
    note = _cal_gap_note(_dense(0, 60) + _dense(130, 595), 0, 600, True)
    assert note and "断了 70 秒" in note
    assert "1:00~2:10" in note
    assert "判定不可信" not in note          # 轻提示 ≠ 重警告


def test_长断流仍出重话():
    """09-04_1 形态：1:00 后一路断到片尾 → 重警告（文案不变）。"""
    note = _cal_gap_note(_dense(0, 60), 0, 600, True)
    assert note and "未覆盖时段" in note and "1:00~10:00" in note


def test_重话与轻提示不叠报():
    """片头 70s 轻断流 + 中段 400s 重断流 → 只返回重话一句。"""
    note = _cal_gap_note(_dense(70, 100) + _dense(500, 595), 0, 600, True)
    assert note and "未覆盖时段" in note
    assert "断了" not in note


def test_校正未启用时不报():
    assert _cal_gap_note([], 0, 600, False) is None


def test_全程零采样长段报无采样_短段不报():
    assert _cal_gap_note([], 0, 600, True) == CAL_NO_SAMPLE_NOTE
    assert _cal_gap_note([], 0, 120, True) is None    # >30 但 <180，不升级


def test_孔洞超过三处时折叠计数():
    holes = [(float(i * 100), float(i * 100 + 50)) for i in range(5)]
    assert _fmt_eras(holes) == "0:00~0:50、1:40~2:30、3:20~4:10 等 5 段"


def test_段窗边界不算孔洞():
    """段尾=窗尾不算断流（段扫描止于下一段起点是设计行为）。"""
    assert _cal_holes(_dense(0, 595), 0, 600, CAL_GAP_HINT_SEC) == []


def test_多段场次的段头孔洞按绝对时钟判定():
    """段 k 的 shift 时间戳是**视频绝对时间**，不是段内相对 0。

    实证：10-17_1 段2 t0=1128.5，其 shift 首样本 1130.1（段头只差 1.6s）。
    所以段头孔洞必须拿 win_start=段起点 去比——若错当成段内相对时间
    （win_start=0），每段开局都会被判成上千秒的断流、逐段误报。
    """
    shift2 = _dense(1130.0, 1700.0)         # 段2：t0=1128.5 起，首样本 1130
    assert _cal_gap_note(shift2, 1128.5, 1700.0, True) is None
    # 反向对照：同一份表若被当成「段内相对时间」，就会误报 1130s 断流
    assert _cal_gap_note(shift2, 0.0, 1700.0, True) is not None


def test_折叠成等N段时秒数标明取最长():
    """{eras} 最多列 3 段，而 {n} 取所有孔洞的最大值——最长那段未必在列出的
    3 段里。文案必须点明「取最长」，否则读者会把秒数安到眼前时段上。"""
    assert "取最长" in CAL_GAP_HINT_NOTE
    holes = [(0.0, 40.0), (100.0, 140.0), (200.0, 240.0), (300.0, 390.0)]
    assert _fmt_eras(holes).endswith("等 4 段")          # 第 4 段（90s）被折叠掉
    assert max(b - a for a, b in holes) == 90.0          # 而 n 正是它的时长


@pytest.mark.parametrize("n", [1, 2])
def test_模板占位齐全(n):
    """文案占位符与 format 调用一致（少一个占位就静默炸在运行期）。"""
    if n == 1:
        CAL_GAP_HINT_NOTE.format(eras="x", n=70)
    else:
        CAL_GAP_NOTE.format(eras="x")
