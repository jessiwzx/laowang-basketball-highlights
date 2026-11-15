"""★ 精选标记（2026-10-23）：sidecar 往返、不变式「精选 ⊆ 进片」、筛选与生成。

**不变式是本文件的重点**——它有三个维护点（打星补勾 / 取消勾选清星 / 读盘
纠偏），漏任何一个的症状都是"精选了但没进片" ⇒ 单条发出一个集锦里根本没有
的片段，而界面上看不出来（星还亮着）。
"""
import json
import os

import pytest

import bbh.app as A
from bbh.models import GoalEvent, save_events

NAME = "2026-01-01_1"


def _mk(tmp_path, ts=(12.1, 72.1, 132.1)):
    """建 work/<名>/ + events.json（形状与真产物一致）。"""
    work = tmp_path / "work" / NAME
    work.mkdir(parents=True)
    evs = [GoalEvent(t=t, confidence=0.9, status="high") for t in ts]
    save_events(str(work / "events.json"), video=f"D:/videos/{NAME}.mp4",
                duration=480.0, events=evs, warning=None,
                calib_key=[1, 2, 3], logic_ver=1)
    return work


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    work = _mk(tmp_path)
    yield tmp_path, work


def _sidecar(work, name):
    p = work / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _write(work, name, data):
    (work / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


# ---------- sidecar 往返 ----------

def test_读缺失的精选表当空集(env):
    _root, work = env
    assert A._load_starred(f"D:/videos/{NAME}.mp4") == set()
    assert not (work / A.STARRED_FILE).exists()   # 只读不建文件


def test_精选表往返(env):
    _root, work = env
    keys = [A._fmt_t(12.1), A._fmt_t(132.1)]
    _write(work, A.STARRED_FILE, keys)
    assert A._load_starred(f"D:/videos/{NAME}.mp4") == set(keys)


def test_精选表损坏当空集(env):
    _root, work = env
    (work / A.STARRED_FILE).write_text("{不是 json", encoding="utf-8")
    assert A._load_starred(f"D:/videos/{NAME}.mp4") == set()


def test_保存只落打星行(env):
    _root, work = env
    _dur, rows = A.load_events_table(f"D:/videos/{NAME}.mp4")
    rows[0][0] = rows[1][0] = 1
    rows[1][7] = 1                       # 只给第 2 行打星
    A._save_starred(f"D:/videos/{NAME}.mp4", rows)
    assert _sidecar(work, A.STARRED_FILE) == [rows[1][1]]


# ---------- 不变式：精选 ⊆ 进片 ----------

def test_读盘纠偏_没勾选的行不能是精选(env):
    """历史脏数据 / 手改 sidecar：星在、勾选不在 ⇒ 必须落成未精选。

    这条兜不住的话，单条会出一条「集锦里没有的球」。"""
    _root, work = env
    _write(work, A.STARRED_FILE, [A._fmt_t(12.1)])       # 只有星，没有 selection
    _dur, rows = A.load_events_table(f"D:/videos/{NAME}.mp4")
    assert rows[0][0] == 0 and rows[0][7] == 0

    _write(work, A.SELECTION_FILE, [A._fmt_t(12.1)])     # 补上勾选才认星
    _dur, rows = A.load_events_table(f"D:/videos/{NAME}.mp4")
    assert rows[0][0] == 1 and rows[0][7] == 1


def test_行结构是八列(env):
    """第 8 列是 ★。所有读 row[6] 的老代码都带 len 保护，新列只能追加。"""
    _root, _work = env
    _dur, rows = A.load_events_table(f"D:/videos/{NAME}.mp4")
    assert all(len(r) == 8 for r in rows)


def test_精选项与标签项可区分():
    assert A._is_star_item(A.GEN_STAR_LABEL)
    assert not A._is_star_item("张三")
    assert not A._is_star_item(A.GEN_ALL_LABEL)
    assert not A._is_all_item(A.GEN_STAR_LABEL)      # 两类项不许互相串
    # 文案不含球数：它能当勾选值，数字一变勾选就会丢
    assert not any(ch.isdigit() for ch in A.GEN_STAR_LABEL)


def test_star_count_只数打星行():
    rows = [[1, "00:01.0", "high", .8, 5, 2, "", 1],
            [1, "00:02.0", "high", .8, 5, 2, "", 0],
            [0, "00:03.0", "high", .8, 5, 2, "", 0]]
    assert A.star_count(rows) == 1


# ---------- 筛选与生成 ----------

def _rows(*specs):
    """specs: (t, tag, checked, starred) → 8 列表格行。"""
    out = []
    for t, tag, checked, starred in specs:
        out.append([checked, f"{int(t // 60):02d}:{t % 60:04.1f}", "high", 0.8,
                    5.0, 2.0, tag, starred])
    return out


def test_筛选只回打星行(env):
    rows = _rows((10.0, "张三", 1, 1), (20.0, "李四", 1, 0), (30.0, "", 1, 1))
    got = A._picked_rows(rows, "", starred_only=True)
    assert [g[0] for g in got] == [10.0, 30.0]


def test_精选为空且已勾选时的报错文案(env):
    rows = _rows((10.0, "张三", 1, 0))
    with pytest.raises(Exception) as ei:
        A._picked_rows(rows, "", starred_only=True)
    assert "精选" in str(ei.value) and "★" in str(ei.value)


def test_精选筛选不放行未勾选的行():
    """脏数据在内存里也能挡住——不靠读盘纠偏兜（那是另一个入口）。"""
    rows = _rows((10.0, "", 0, 1))
    with pytest.raises(Exception):
        A._picked_rows(rows, "", starred_only=True)


def test_精选项生成只出打星的球(env, monkeypatch):
    calls = []

    def _fake_v(video_path, goals, work_dir, out_name, cfg=None):
        calls.append((goals, out_name))
        p = os.path.join(work_dir, out_name)
        open(p, "wb").write(b"x")
        return p

    monkeypatch.setattr(A.vrender, "build_vertical", _fake_v)
    rows = _rows((10.0, "张三", 1, 1), (20.0, "李四", 1, 0), (30.0, "", 1, 1))
    gen = A.generate_singles(f"D:/videos/{NAME}.mp4", rows,
                             [A.GEN_STAR_LABEL], vertical=True)
    msgs = []
    try:
        while True:
            msgs.append(next(gen))
    except StopIteration as st:
        outs, skipped = st.value
    assert [os.path.basename(p) for _i, p in outs] == [
        "single-竖-张三-00m10s0.mp4", "single-竖-未标-00m30s0.mp4"]
    assert skipped == []
    assert len(msgs) == 2 and "1/2" in msgs[0]


def test_精选与标签同时勾_按时刻去重(env, monkeypatch):
    calls = []
    monkeypatch.setattr(A.vrender, "build_vertical",
                        lambda vp, g, wd, n, cfg=None: (
                            calls.append(n), open(os.path.join(wd, n), "wb")
                            .write(b"x"), os.path.join(wd, n))[2])
    rows = _rows((10.0, "张三", 1, 1), (20.0, "张三", 1, 0))
    gen = A.generate_singles(f"D:/videos/{NAME}.mp4", rows,
                             [A.GEN_STAR_LABEL, "张三"], vertical=True)
    try:
        while True:
            next(gen)
    except StopIteration as st:
        outs, _skipped = st.value
    # 精选出 10.0；标签「张三」出 10.0 + 20.0；10.0 不重复渲染
    assert len(outs) == 2 and len(calls) == 2


# ---------- 集锦模式也支持精选（2026-10-24） ----------

def test_集锦精选项两模式文案都认():
    """两个模式的精选串**都必须**被 `_is_star_item` 认出来。

    漏登记的症状是静默失败：`gen_selected` 会把这一项当标签名去 `_picked_rows`
    筛 ⇒ 0 行 ⇒ 勾了精选却一条都不出、也不报错（合集项当年踩的就是这个坑）。
    """
    assert A._is_star_item(A.GEN_STAR_LABEL_REEL)
    assert A._is_star_item(A.GEN_STAR_LABEL)
    assert A.star_label("reel") == A.GEN_STAR_LABEL_REEL
    assert A.star_label("single") == A.GEN_STAR_LABEL
    assert A.star_label("reel") != A.star_label("single")   # 文案要能区分出几条
    # 两类项不许互相串，且文案仍不含球数（它同时是勾选值）
    assert not A._is_all_item(A.GEN_STAR_LABEL_REEL)
    assert not A._is_star_item(A.GEN_ALL_LABEL)
    assert not any(ch.isdigit() for ch in A.GEN_STAR_LABEL_REEL)


def test_集锦精选只出打星的球且文件名不撞合集(env, monkeypatch):
    """集锦模式的精选项：**合成一条**、只含打星行，输出 `-精选.mp4`。
    文件名必须与合集那条不同——同一批里两项都勾时，同名的后者会覆盖前者。"""
    calls = []

    def _fake_v(video_path, goals, work_dir, out_name, cfg=None):
        calls.append(([g[0] for g in goals], out_name))
        p = os.path.join(work_dir, out_name)
        open(p, "wb").write(b"x")
        return p

    monkeypatch.setattr(A.vrender, "build_vertical", _fake_v)
    rows = _rows((10.0, "张三", 1, 1), (20.0, "李四", 1, 0), (30.0, "", 1, 1))
    vp = f"D:/videos/{NAME}.mp4"
    out_star = A.generate_highlight(vp, rows, "", vertical=True, starred_only=True)
    out_all = A.generate_highlight(vp, rows, "", vertical=True)
    # 竖屏"爆点前置"会重排（同为 high，稳定排序 ⇒ 实际保持时间序）
    assert calls[0][1] == "highlight-竖-精选.mp4"
    assert sorted(calls[0][0]) == [10.0, 30.0]
    assert calls[1][1] == "highlight-竖.mp4"
    assert sorted(calls[1][0]) == [10.0, 20.0, 30.0]
    assert os.path.basename(out_star) != os.path.basename(out_all)


def test_集锦精选_横屏分支同样命名隔离(env, monkeypatch):
    """横屏走 editor.build_highlight（旧直切路径），命名规则必须一致：
    不然"横屏精选"会把 `highlight-横.mp4` 覆盖掉。"""
    calls = []
    monkeypatch.setattr(A.editor, "build_highlight",
                        lambda vp, clips, wd, name: (
                            calls.append((len(clips), name)),
                            open(os.path.join(wd, name), "wb").write(b"x"),
                            os.path.join(wd, name))[2])
    rows = _rows((10.0, "", 1, 1), (20.0, "", 1, 0))
    A.generate_highlight(f"D:/videos/{NAME}.mp4", rows, "",
                         vertical=False, starred_only=True)
    assert calls == [(1, "highlight-横-精选.mp4")]


# ---------- 集锦模式也支持精选（2026-10-24） ----------

def test_集锦精选项两模式文案都认():
    """两个模式的精选串**都必须**被 `_is_star_item` 认出来。

    漏登记的症状是静默失败：`gen_selected` 会把这一项当标签名去 `_picked_rows`
    筛 ⇒ 0 行 ⇒ 勾了精选却一条都不出、也不报错（合集项当年踩的就是这个坑）。
    """
    assert A._is_star_item(A.GEN_STAR_LABEL_REEL)
    assert A._is_star_item(A.GEN_STAR_LABEL)
    assert A.star_label("reel") == A.GEN_STAR_LABEL_REEL
    assert A.star_label("single") == A.GEN_STAR_LABEL
    assert A.star_label("reel") != A.star_label("single")   # 文案要能区分出几条
    # 两类项不许互相串，且文案仍不含球数（它同时是勾选值）
    assert not A._is_all_item(A.GEN_STAR_LABEL_REEL)
    assert not A._is_star_item(A.GEN_ALL_LABEL)
    assert not any(ch.isdigit() for ch in A.GEN_STAR_LABEL_REEL)


def test_集锦精选只出打星的球且文件名不撞合集(env, monkeypatch):
    """集锦模式的精选项：**合成一条**、只含打星行，输出 `-精选.mp4`。
    文件名必须与合集那条不同——同一批里两项都勾时，同名的后者会覆盖前者。"""
    calls = []

    def _fake_v(video_path, goals, work_dir, out_name, cfg=None):
        calls.append(([g[0] for g in goals], out_name))
        p = os.path.join(work_dir, out_name)
        open(p, "wb").write(b"x")
        return p

    monkeypatch.setattr(A.vrender, "build_vertical", _fake_v)
    rows = _rows((10.0, "张三", 1, 1), (20.0, "李四", 1, 0), (30.0, "", 1, 1))
    vp = f"D:/videos/{NAME}.mp4"
    out_star = A.generate_highlight(vp, rows, "", vertical=True, starred_only=True)
    out_all = A.generate_highlight(vp, rows, "", vertical=True)
    # 竖屏"爆点前置"会重排（同为 high，稳定排序 ⇒ 实际保持时间序）
    assert calls[0][1] == "highlight-竖-精选.mp4"
    assert sorted(calls[0][0]) == [10.0, 30.0]
    assert calls[1][1] == "highlight-竖.mp4"
    assert sorted(calls[1][0]) == [10.0, 20.0, 30.0]
    assert os.path.basename(out_star) != os.path.basename(out_all)


def test_集锦精选_横屏分支同样命名隔离(env, monkeypatch):
    """横屏走 editor.build_highlight（旧直切路径），命名规则必须一致：
    不然"横屏精选"会把 `highlight-横.mp4` 覆盖掉。"""
    calls = []
    monkeypatch.setattr(A.editor, "build_highlight",
                        lambda vp, clips, wd, name: (
                            calls.append((len(clips), name)),
                            open(os.path.join(wd, name), "wb").write(b"x"),
                            os.path.join(wd, name))[2])
    rows = _rows((10.0, "", 1, 1), (20.0, "", 1, 0))
    A.generate_highlight(f"D:/videos/{NAME}.mp4", rows, "",
                         vertical=False, starred_only=True)
    assert calls == [(1, "highlight-横-精选.mp4")]
