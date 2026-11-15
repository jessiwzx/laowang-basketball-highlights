"""段块缓存复用（v21/v22 前修的三处缺陷）的回归测试。

纯 dict 构造，不碰视频、不起检测器，毫秒级。锁住四件事：
1. `_block_covers` 不许把「段窗 t1」当成「块的 t1」（原实现恒真 bug）；
2. `_blocks_from_doc` 不许用指纹去重（同机位补段两段指纹全等）；
3. `_find_block` 同指纹多段各归各位；
4. 旧扁平存档（t0=0/t1=None 单块）的补头段复用路径不许坏——09-04_1 实况。

原先这三处修复**没有任何测试**（两仓 test_multi_segment.py 逐字节相同），
2026-11-07 搬修复到非 YOLO 分支时补齐，同日回补本仓。
"""
from types import SimpleNamespace

from bbh.pipeline import (_block_covers, _blocks_from_doc, _find_block,
                          detect_fingerprint)

K = [[630, 245, 34, 14], [3, 70, 70], [13, 152, 152], [1280, 720], 'd5715347']
VP = "/tmp/v.mp4"


def blk(t0=0.0, t1=None, key=K):
    return {"key": key, "t0": t0, "t1": t1, "shift": [], "positions": [],
            "hoop": [], "hoop_ref": None}


def seg(t0=0.0, t1=None, key=K):
    """_find_block 只读 seg.key / seg.t0，用 SimpleNamespace 就够。"""
    return SimpleNamespace(key=key, t0=t0, t1=t1)


# ---- ① _block_covers：段的 t1 不等于块的 t1 -------------------------------

def test_块只扫到600而段要到1200不算覆盖():
    """原实现 end=t1 恒真：这里会误判覆盖，整段拿错数据判定。"""
    assert _block_covers(blk(0.0, 600.0), 0.0, 1200.0, 3600.0) is False


def test_块扫满段窗才算覆盖():
    assert _block_covers(blk(0.0, 1200.0), 0.0, 1200.0, 3600.0) is True


def test_块起点晚于段起点不算覆盖():
    assert _block_covers(blk(30.0, 1200.0), 0.0, 1200.0, 3600.0) is False


def test_段到片尾看块是否也到片尾():
    assert _block_covers(blk(0.0, None), 0.0, None, 3600.0) is True
    # 块只扫到 1000 却要吃到片尾 → 不覆盖
    assert _block_covers(blk(0.0, 1000.0), 0.0, None, 3600.0) is False


def test_段到片尾时块尾够长即覆盖():
    # 容差 0.05s：3599.90 差 0.10 → 不算覆盖；3599.98 才算
    assert _block_covers(blk(0.0, 3599.90), 0.0, None, 3600.0) is False
    assert _block_covers(blk(0.0, 3599.98), 0.0, None, 3600.0) is True


# ---- ②③ 块表不许去重 -----------------------------------------------------

def test_同指纹多段各存一块():
    """同机位补一段 → 两段指纹完全相同。字典去重会只剩 1 块。"""
    raw = [{"calib": K, "t0": 0.0, "t1": 600.0, "shift": [],
            "positions": [], "positions_len": 0},
           {"calib": K, "t0": 600.0, "t1": 1200.0, "shift": [],
            "positions": [], "positions_len": 0}]
    doc = {"video": VP, "detect_ver": detect_fingerprint(), "segments": raw}
    blocks = _blocks_from_doc(doc, VP)
    assert len(blocks) == 2
    assert [b["t0"] for b in blocks] == [0.0, 600.0]


def test_身份不符返回空列表():
    raw = [{"calib": K, "t0": 0.0, "t1": 600.0, "shift": [],
            "positions": [], "positions_len": 0}]
    doc = {"video": VP, "detect_ver": detect_fingerprint(), "segments": raw}
    assert _blocks_from_doc(doc, "/tmp/other.mp4") == []
    assert _blocks_from_doc(doc, VP) != []
    doc_bad = dict(doc, detect_ver="stale-version")
    assert _blocks_from_doc(doc_bad, VP) == []


def test_旧扁平格式视作单块():
    doc = {"video": VP, "detect_ver": detect_fingerprint(), "calib": K,
           "shift": [], "positions": [], "positions_len": 0}
    blocks = _blocks_from_doc(doc, VP)
    assert len(blocks) == 1 and blocks[0]["t0"] == 0.0 and blocks[0]["t1"] is None


# ---- ④ _find_block：就近优先，兜底覆盖 -----------------------------------

def test_同指纹两段按t0各归各位():
    blocks = [blk(0.0, 600.0), blk(600.0, 1200.0)]
    got = _find_block(blocks, seg(600.0), 600.0, 1200.0, 3600.0)
    assert got is blocks[1]


def test_就近块覆盖不足时不串到别的段():
    """seg2 与 seg1 指纹相同但 t0 差 >0.5s，且 seg1 块没扫到 seg2 窗口
    → 必须找不到（调用处据此重扫），绝不能拿 seg1 的数据判定。"""
    blocks = [blk(0.0, 600.0)]
    assert _find_block(blocks, seg(600.0), 600.0, 1200.0, 3600.0) is None


def test_旧扁平单块能被补头段吃到():
    """09-04_1 实况：单标定全片扫描存档 + 事后补头段，尾段缓存照吃。"""
    flat = blk(0.0, None)
    got = _find_block([flat], seg(3.0), 3.0, None, 3600.0)
    assert got is flat


def test_指纹不同不给块():
    other = [[1, 2, 3], [3, 70, 70], [13, 152, 152], [1280, 720], 'zzz']
    assert _find_block([blk(0.0, None)], seg(0.0, key=other),
                       0.0, None, 3600.0) is None


def test_空块表返回None():
    assert _find_block([], seg(0.0), 0.0, None, 3600.0) is None
