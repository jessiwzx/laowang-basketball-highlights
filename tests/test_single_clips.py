"""单条视频生成（2026-10-21）：命名规则、跨项去重、单球失败跳过、横屏夹界。

全部用替身渲染函数——真渲染每球 ~15s（实测），单测跑不起。
真链路的一致性由 test_web_jobs_api.py 的 summary↔结果解析用例和 md5 对拍保证。
"""
import json
import os

import pytest

import bbh.app as A

NAME = "2026-01-01_1"


def _rows(*specs):
    """specs: (t, tag, checked) → 表格行（列序与 load_events_table 一致）。

    列：0 勾选、1 时刻串、2 档位、3 置信、4 前秒、5 后秒、6 标签。
    """
    out = []
    for t, tag, checked in specs:
        out.append([checked, f"{int(t // 60):02d}:{t % 60:04.1f}", "high", 0.8,
                    5.0, 2.0, tag])
    return out


def _drain(gen):
    """跑干生成器，取 StopIteration.value（= (outs, skipped)）。"""
    msgs = []
    try:
        while True:
            msgs.append(next(gen))
    except StopIteration as st:
        return msgs, st.value


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """chdir 到 tmp（work/ 是相对路径），建好 work/<名>/ 与 events.json。"""
    monkeypatch.chdir(tmp_path)
    w = tmp_path / "work" / NAME
    w.mkdir(parents=True)
    (w / "events.json").write_text(json.dumps({
        "video": f"D:/videos/{NAME}.mp4", "duration": 100.0, "events": [],
    }), encoding="utf-8")
    yield tmp_path


@pytest.fixture()
def fake_render(monkeypatch):
    """替身渲染：按 out_name 落一个文件，记录每次调用的参数。"""
    calls = []

    def _fake_v(video_path, goals, work_dir, out_name, cfg=None):
        calls.append({"kind": "v", "goals": goals, "name": out_name})
        p = os.path.join(work_dir, out_name)
        with open(p, "wb") as f:
            f.write(b"x")
        return p

    def _fake_h(video_path, clips, work_dir, out_name):
        calls.append({"kind": "h", "clips": clips, "name": out_name})
        p = os.path.join(work_dir, out_name)
        with open(p, "wb") as f:
            f.write(b"x")
        return p

    monkeypatch.setattr(A.vrender, "build_vertical", _fake_v)
    monkeypatch.setattr(A.editor, "build_highlight", _fake_h)
    return calls


# ---------- 命名（P1-1：名字里不能有点，否则被 splitext 切掉） ----------

def test_单条命名_竖屏_时刻无冒号无小数点():
    n = A._single_name(83.4, "张三", True)
    assert n == "single-竖-张三-01m23s4.mp4"
    assert ":" not in n
    # 渲染层收尾是 safe_filename(os.path.splitext(out_name)[0]) + ".mp4"
    # —— 名字里带点会被当成扩展名切掉，同分钟两个球就互撞
    assert os.path.splitext(n)[0] == "single-竖-张三-01m23s4"


def test_单条命名_横屏_未打标签():
    assert A._single_name(5.0, "", False) == "single-横-未标-00m05s0.mp4"


def test_单条命名_超长标签被截断():
    n = A._single_name(0.0, "阿" * 80, True)
    assert len(n) <= len("single-竖-") + A._TAG_MAX + len("-00m00s0.mp4")


def test_单条命名_同一秒内的两个球不撞名():
    assert A._single_name(83.4, "张三", True) != A._single_name(83.8, "张三", True)


# ---------- 生成口径 ----------

def test_单条_每球一个文件(env, fake_render):
    rows = _rows((10.0, "张三", True), (20.0, "李四", True))
    msgs, (outs, skipped) = _drain(A.generate_singles(
        f"D:/videos/{NAME}.mp4", rows, [A.GEN_ALL_LABEL_SINGLE], vertical=True))
    assert [o[1].rsplit(os.sep, 1)[-1] for o in outs] == [
        "single-竖-张三-00m10s0.mp4", "single-竖-李四-00m20s0.mp4"]
    assert skipped == []
    assert len(msgs) == 2 and "1/2" in msgs[0] and "2/2" in msgs[1]
    # 每个 goal 只传一个球（单条=独立成品，不与别人拼）
    assert all(len(c["goals"]) == 1 for c in fake_render)


def test_单条_未勾选的行不出片(env, fake_render):
    rows = _rows((10.0, "张三", True), (20.0, "张三", False))
    _msgs, (outs, _sk) = _drain(A.generate_singles(
        f"D:/videos/{NAME}.mp4", rows, [A.GEN_ALL_LABEL_SINGLE]))
    assert len(outs) == 1


def test_单条_跨勾选项按时刻去重(env, fake_render):
    """同时勾「合集」+「张三」不得把张三的球出两遍（P0-1 的姊妹问题）。"""
    rows = _rows((10.0, "张三", True), (20.0, "张三", True), (30.0, "李四", True))
    _msgs, (outs, _sk) = _drain(A.generate_singles(
        f"D:/videos/{NAME}.mp4", rows,
        [A.GEN_ALL_LABEL_SINGLE, "张三"]))
    assert len(outs) == 3                       # 不是 5
    names = [c["name"] for c in fake_render]
    assert len(names) == len(set(names)) == 3


def test_单条_两套合集文案都被认成合集(env, fake_render):
    """P0-1：漏认合集项会被当标签名去筛 ⇒ 0 行 ⇒ 静默不出片。"""
    rows = _rows((10.0, "张三", True), (20.0, "", True))
    for item in (A.GEN_ALL_LABEL, A.GEN_ALL_LABEL_SINGLE):
        fake_render.clear()
        _msgs, (outs, _sk) = _drain(A.generate_singles(
            f"D:/videos/{NAME}.mp4", rows, [item]))
        assert len(outs) == 2, item


def test_单条_无该标签的球则跳过并汇报(env, fake_render):
    rows = _rows((10.0, "张三", True))
    _msgs, (outs, skipped) = _drain(A.generate_singles(
        f"D:/videos/{NAME}.mp4", rows, ["李四"]))
    assert outs == []
    assert len(skipped) == 1 and "李四" in skipped[0] and "跳过" in skipped[0]


def test_单条_单球渲染失败只跳过该球(env, fake_render, monkeypatch):
    """P0-3：单球异常必须被吞掉并汇报，不能拖垮整批、更不能裸抛把按钮卡灰。"""
    rows = _rows((10.0, "张三", True), (20.0, "李四", True))
    real = A.vrender.build_vertical
    state = {"n": 0}

    def _boom(*a, **kw):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("ffmpeg 失败")
        return real(*a, **kw)

    monkeypatch.setattr(A.vrender, "build_vertical", _boom)
    _msgs, (outs, skipped) = _drain(A.generate_singles(
        f"D:/videos/{NAME}.mp4", rows, [A.GEN_ALL_LABEL_SINGLE]))
    assert len(outs) == 1 and len(skipped) == 1
    assert "ffmpeg 失败" in skipped[0]


def test_单条_横屏自己夹上界(env, fake_render):
    """build_highlight 内部不 clamp（clamp 在 plan_clips 里），单条要自己夹，
    否则越界片段交给 ffmpeg 会切出短于预期的尾巴。"""
    rows = _rows((99.0, "张三", True))          # duration=100，99+2=101 → 夹到 100
    _msgs, (_outs, _sk) = _drain(A.generate_singles(
        f"D:/videos/{NAME}.mp4", rows, [A.GEN_ALL_LABEL_SINGLE], vertical=False))
    assert fake_render[0]["clips"] == [(94.0, 100.0)]


def test_单条_进度回调按条数上报(env, fake_render):
    rows = _rows((10.0, "", True), (20.0, "", True), (30.0, "", True))
    seen = []
    _drain(A.generate_singles(f"D:/videos/{NAME}.mp4", rows,
                              [A.GEN_ALL_LABEL_SINGLE],
                              on_progress=lambda k, n: seen.append((k, n))))
    assert seen == [(1, 3), (2, 3), (3, 3)]
