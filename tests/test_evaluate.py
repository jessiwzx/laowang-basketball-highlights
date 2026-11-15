from tools.evaluate import (_deleted_exclusions, _load_deleted_seconds,
                           auto_metrics, match_report, regression_report)


def test_标准进球命中():
    report = match_report(truth=[100.0, 300.0],
                          detected=[(100.1, "high"), (302.9, "review"),
                                    (500.0, "high")])
    assert report["命中"] == 2
    assert report["漏检"] == 0
    assert report["误报"] == 1
    assert report["待确认中"] == 1


def test_容差3秒():
    report = match_report(truth=[100.0], detected=[(102.9, "high")])
    assert report["命中"] == 1


def test_行锚定小容差防相邻错认领():
    """表格行键真值用小容差：2.6s 外的相邻事件不得认领本行真值。"""
    report = match_report(truth=[100.0], detected=[(102.6, "high")],
                          tolerance=0.2)
    assert report["漏检"] == 1
    assert report["误报"] == 1


def test_超容差算漏检加误报():
    report = match_report(truth=[100.0], detected=[(103.1, "high")])
    assert report["漏检"] == 1
    assert report["误报"] == 1


def test_同一truth被两检测匹配时第二个算误报():
    """used 集合去重：同一标注只许一个检测认领。"""
    report = match_report(truth=[100.0],
                          detected=[(100.1, "high"), (100.2, "high")])
    assert report["命中"] == 1
    assert report["误报"] == 1


def test_乱序truth先排序再匹配():
    """人工标注顺序错乱不应静默低估命中。"""
    report = match_report(truth=[103.0, 100.0],
                          detected=[(100.5, "high"), (103.2, "high")])
    assert report["命中"] == 2
    assert report["漏检"] == 0
    assert report["误报"] == 0


def test_低置信命中算命中_未命中算低噪不算误报():
    """2026-08-19 三档口径：low 命中 truth 不算漏检（球已在列表里，人工可
    找回）；low 未命中是噪声行——不计入误报（否则指标被低置信噪声灌满，
    验收门失去衡量 high+review 精度的意义）。"""
    report = match_report(truth=[100.0, 200.0],
                          detected=[(100.5, "low"), (150.0, "low"),
                                    (201.0, "high")])
    assert report["命中"] == 2
    assert report["命中低置信"] == 1
    assert report["漏检"] == 0
    assert report["误报"] == 0
    assert report["低噪"] == 1


# ---- 自动档口径（2026-08-23 用户定调：只关注 🟢 high 档查准/查全，
# 任意档浮表率不再作为指标） ----

def test_自动档查准查全():
    report = match_report(truth=[100.0, 200.0],
                          detected=[(100.1, "high"), (200.2, "review"),
                                    (300.0, "high")])
    m = auto_metrics(report, high_total=2)
    assert m["自动档命中"] == 1
    assert m["查准"] == 0.5  # 1/2 个 high 是真进球
    assert m["查全"] == 0.5  # 2 个真进球只 high 命中 1 个
    assert m["假绿"] == 1


def test_自动档指标_零除保护():
    m = auto_metrics(match_report([], []), high_total=0)
    assert m["查准"] == 1.0
    assert m["查全"] == 1.0


# ---- 部分标注回归集（complete=false：确认进球必须仍浮表，不算查准/查全） ----

def test_回归集_确认进球全部浮表():
    r = regression_report([100.0, 200.0],
                          [(100.2, "review"), (200.1, "low"),
                           (300.0, "high")])
    assert r["确认进球"] == 2
    assert r["浮表"] == 2
    assert r["丢失"] == []
    assert r["档位"] == {"high": 0, "review": 1, "low": 1}


def test_回归集_未浮表即丢失():
    r = regression_report([100.0, 250.0], [(100.2, "high")])
    assert r["浮表"] == 1
    assert r["丢失"] == [250.0]


def test_回归集_同一检测不被两个确认认领():
    r = regression_report([100.0, 100.5], [(100.2, "high")])
    assert r["浮表"] == 1
    assert len(r["丢失"]) == 1


# ---- 删键排除（剪辑台「🗑 删除此球」的行不进真值也不计误报） ----

def test_删键秒数解析(tmp_path):
    import json
    tmp_path.joinpath("deleted.json").write_text(
        json.dumps(["1:00.0", "2:03.5", "坏键"]), encoding="utf-8")
    # 入参是 events.json 路径，函数找它同目录的 deleted.json
    assert _load_deleted_seconds(str(tmp_path / "events.json")) == [60.0, 123.5]


def test_删键漂移0点3秒仍认回最近行():
    """09-04_1 实况：多段标定重扫后行键整体漂 0.1~0.3s（deleted 记
    05:50.5、现表格行 350.55），旧 ±0.051 锚定静默丢否决 → 那 5 个 high
    行被当误报打脏查准。加宽到 ±0.5（实测该场相邻行最小间距 0.63s，
    1:1 最近匹配不可能认错行）。"""
    keep = _deleted_exclusions([350.55, 400.0], [350.5])
    assert keep == {0}


def test_删键超半秒不认领():
    keep = _deleted_exclusions([351.2], [350.5])
    assert keep == set()


def test_删键1对1不重复认领():
    keep = _deleted_exclusions([350.55], [350.5, 350.6])
    assert keep == {0}


def test_删键精确锚定不受影响():
    keep = _deleted_exclusions([100.0, 200.0], [100.0, 200.0])
    assert keep == {0, 1}
