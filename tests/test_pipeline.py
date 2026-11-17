import json
import os
import time

import cv2
import numpy as np
import pytest

from bbh.models import BallPos, Calibration, GoalEvent, load_events
from bbh.pipeline import run_detection


def test_完整跑通并产出events(sample_video, sample_calibration, tmp_path):
    events_path, warning = run_detection(sample_video, sample_calibration,
                                         work_dir=str(tmp_path))
    # sample_calibration 未存参考帧（ref_image=None）→ 附"位移校正未启用"提示
    assert warning is not None and "参考帧" in warning
    data = load_events(events_path)
    assert data["duration"] > 0
    assert len(data["events"]) == 1
    assert data["events"][0].status == "high"
    assert not os.path.exists(str(tmp_path / "checkpoint.json"))  # 完成后清理
    assert os.path.exists(tmp_path / "positions.json")            # 检出流落盘保留


def test_progress_cb转发给检测器(sample_calibration, tmp_path):
    """run_detection 的 progress_cb 要贯穿到 detect_positions 的进度回调。
    合成 400 帧视频（用 400 帧是为了跨多个上报节拍：2026-11-17 起节拍是
    「≥30 帧 或 ≥2 秒」，且进循环前先报一次 (0, total)）。"""
    w, h = sample_calibration.frame_size
    path = str(tmp_path / "long.mp4")
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 30, (w, h))
    for _ in range(400):
        vw.write(np.full((h, w, 3), (40, 44, 50), np.uint8))
    vw.release()
    seen = []
    events_path, warning = run_detection(path, sample_calibration,
                                         work_dir=str(tmp_path / "work"),
                                         progress_cb=lambda fi, tot: seen.append((fi, tot)))
    assert seen, "未收到任何进度回调"
    # 进循环前先报一次：界面 1 秒内就有总帧数（旧实现要等第 300 帧，
    # 慢机器上表现为「0/0 帧 (0%)」干等十几分钟，2026-11-17 实测）
    assert seen[0][0] == 0 and seen[0][1] == 400
    assert seen[-1][0] > 0               # 之后按节拍持续上报
    assert all(tot == 400 for _fi, tot in seen)     # total 恒为视频总帧数


def test_无检出时返回警告(sample_video, tmp_path):
    from bbh.models import Calibration
    # 阈值收紧到什么都检不到
    bad = Calibration(hoop_rect=(300, 140, 80, 40), hsv_low=(0, 0, 250),
                      hsv_high=(10, 10, 255), frame_size=(640, 360))
    events_path, warning = run_detection(sample_video, bad, work_dir=str(tmp_path))
    assert warning is not None and "标定" in warning


def test_检查点写入与续跑(sample_video, sample_calibration, tmp_path, monkeypatch):
    # CHECKPOINT_SEC 缩到 0，progress 回调立即触发写检查点；随后模拟中断
    import bbh.pipeline as pl
    orig = pl.detect_positions
    calls = {"n": 0}

    def fake_detect(video, calib, step=2, progress_cb=None, resume_frame=0,
                    on_detect=None, motion=None, end_frame=None):
        positions, meta = orig(video, calib, step, None, resume_frame, on_detect,
                               motion, end_frame)
        calls["n"] += 1
        calls["resumes"].append(resume_frame)
        if calls["n"] == 1 and progress_cb:
            progress_cb(90, meta["frame_count"])   # 模拟处理到第 90 帧的进度回调
            raise KeyboardInterrupt("模拟中断")
        return positions, meta

    calls["resumes"] = []
    monkeypatch.setattr(pl, "detect_positions", fake_detect)
    monkeypatch.setattr(pl, "CHECKPOINT_SEC", 0.0)
    with pytest.raises(KeyboardInterrupt):
        pl.run_detection(sample_video, sample_calibration, work_dir=str(tmp_path))
    assert os.path.exists(tmp_path / "checkpoint.json")
    ckpt = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
    assert len(ckpt["positions"]) > 0        # on_detect 实时持久化，检查点非空
    # 续跑：恢复后正常完成（fake 仅在第 1 次调用抛中断，续跑调用正常返回）
    events_path, warning = pl.run_detection(sample_video, sample_calibration,
                                            work_dir=str(tmp_path))
    assert calls["resumes"][1] > 0           # 第二次调用真正从断点续跑
    # sample_calibration 未存参考帧 → 续跑完成的警告只剩"位移校正未启用"提示
    assert warning is not None and "参考帧" in warning
    data = load_events(events_path)
    assert len(data["events"]) == 1
    # 不重复不丢失：与无中断直跑的结果逐事件一致（重复点会造成额外候选事件）
    direct = tmp_path / "direct"
    p3, _ = pl.run_detection(sample_video, sample_calibration, work_dir=str(direct))
    d3 = load_events(p3)
    assert [(e.t, e.status) for e in data["events"]] == \
           [(e.t, e.status) for e in d3["events"]]


def test_取消时保存检查点_续跑结果一致(sample_video, sample_calibration,
                                      tmp_path, monkeypatch):
    """页面刷新/关闭 → cancel 请求 → 抛 DetectionCancelled 且已检出点落
    检查点；重新跑从断点续起，最终结果与直跑一致（取消不丢不重）。"""
    import bbh.pipeline as pl
    from bbh.pipeline import DetectionCancelled
    orig = pl.detect_positions
    resumes = []

    def fake_detect(video, calib, step=2, progress_cb=None, resume_frame=0,
                    on_detect=None, motion=None, end_frame=None):
        positions, meta = orig(video, calib, step, None, resume_frame, on_detect,
                               motion, end_frame)
        resumes.append(resume_frame)
        if len(resumes) == 1 and progress_cb:
            progress_cb(90, meta["frame_count"])   # 模拟处理到第 90 帧的进度
            raise DetectionCancelled(90)
        return positions, meta

    monkeypatch.setattr(pl, "detect_positions", fake_detect)
    with pytest.raises(DetectionCancelled):
        pl.run_detection(sample_video, sample_calibration, work_dir=str(tmp_path))
    ckpt = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
    assert ckpt["frame_idx"] == 90 and len(ckpt["positions"]) > 0
    # 续跑：第二次调用正常返回，结果与直跑完全一致
    p1, _ = pl.run_detection(sample_video, sample_calibration, work_dir=str(tmp_path))
    p2, _ = pl.run_detection(sample_video, sample_calibration,
                             work_dir=str(tmp_path / "direct"))
    assert resumes[1] > 0
    assert [(e.t, e.status) for e in load_events(p1)["events"]] == \
           [(e.t, e.status) for e in load_events(p2)["events"]]


def test_进度节拍上取消请求中止检测(sample_calibration, tmp_path):
    """真实检测循环里的取消：cancel_cb 在进度节拍（fi%300）被查询，置位
    即中止——合成 400 帧视频恰好在第 300 帧触发一次进度回调。"""
    from bbh.pipeline import DetectionCancelled
    w, h = sample_calibration.frame_size
    path = str(tmp_path / "long.mp4")
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), 30, (w, h))
    for _ in range(400):
        vw.write(np.full((h, w, 3), (40, 44, 50), np.uint8))
    vw.release()
    calls = {"n": 0}

    def cancel_cb():
        calls["n"] += 1
        return calls["n"] >= 1        # 首个进度回调即取消

    with pytest.raises(DetectionCancelled):
        run_detection(path, sample_calibration, work_dir=str(tmp_path / "work"),
                      cancel_cb=cancel_cb)
    assert os.path.exists(tmp_path / "work" / "checkpoint.json")


def test_已有events时跳过检测直接复用(sample_video, sample_calibration, tmp_path):
    p1, _ = run_detection(sample_video, sample_calibration, work_dir=str(tmp_path))
    mtime = os.path.getmtime(p1)
    p2, _ = run_detection(sample_video, sample_calibration, work_dir=str(tmp_path))
    assert p1 == p2 and os.path.getmtime(p2) == mtime   # 未重跑


def test_旧逻辑版本的结果不复用_自动重跑(sample_video, sample_calibration,
                                        tmp_path):
    """复用键必须含检测逻辑版本：判定器/过滤器改了之后，同视频同标定的
    旧 events.json 也不得被静默复用（2026-08-17 真实事故：静止链过滤 +
    下落校验上线后，用户点「开始检测」仍秒回旧 102 条——缓存身份只认
    视频+标定，逻辑升级被缓存吞掉）。"""
    from bbh.pipeline import LOGIC_VERSION
    p1, _ = run_detection(sample_video, sample_calibration, work_dir=str(tmp_path))
    assert load_events(p1)["logic_ver"] == LOGIC_VERSION
    # 篡改成旧版本号（等价于旧代码产出的文件：无 logic_ver 键）
    data = json.loads((tmp_path / "events.json").read_text(encoding="utf-8"))
    data.pop("logic_ver", None)
    (tmp_path / "events.json").write_text(json.dumps(data, ensure_ascii=False),
                                          encoding="utf-8")
    mtime = os.path.getmtime(p1)
    # NTFS 惰性 mtime 更新粒度约 15ms：暖缓存重跑仅 ~3ms，两次写盘会落进
    # 同一时间戳刻度使严格比较假阴性——拉大间隔保证跨刻度
    time.sleep(0.02)
    p2, _ = run_detection(sample_video, sample_calibration, work_dir=str(tmp_path))
    assert os.path.getmtime(p2) > mtime                   # 重跑了
    assert load_events(p2)["logic_ver"] == LOGIC_VERSION  # 新版本号落盘


def test_复用路径返回存储的警告(sample_video, tmp_path):
    from bbh.models import Calibration
    bad = Calibration(hoop_rect=(300, 140, 80, 40), hsv_low=(0, 0, 250),
                      hsv_high=(10, 10, 255), frame_size=(640, 360))
    p1, w1 = run_detection(sample_video, bad, work_dir=str(tmp_path))
    assert "标定" in w1
    mtime = os.path.getmtime(p1)
    p2, w2 = run_detection(sample_video, bad, work_dir=str(tmp_path))
    assert w2 == w1                          # 存储的警告原样带回
    assert os.path.getmtime(p2) == mtime     # 且未重跑、未重写


def test_换标定后作废旧events重跑(sample_video, sample_calibration, tmp_path):
    """复用必须校验身份：换标定后旧 events.json 不得被静默复用（I1）。"""
    from bbh.models import Calibration
    from bbh.pipeline import _calib_key
    p1, _ = run_detection(sample_video, sample_calibration, work_dir=str(tmp_path))
    assert len(load_events(p1)["events"]) == 1       # good 标定检出 1 个 high
    bad = Calibration(hoop_rect=(300, 140, 80, 40), hsv_low=(0, 0, 250),
                      hsv_high=(10, 10, 255), frame_size=(640, 360))
    p2, warning = run_detection(sample_video, bad, work_dir=str(tmp_path))
    assert p2 == p1                                  # 同一文件被覆盖重写
    data = load_events(p2)
    assert data["events"] == []                      # bad 标定什么都检不到
    assert "标定" in warning
    assert data["calib_key"] == _calib_key(bad)      # 新指纹随新结果落盘


def test_换视频后作废旧events重跑(sample_video, sample_calibration, tmp_path):
    """同 work 目录换了视频（如不同文件夹的同名文件）也必须重跑。"""
    import shutil
    work = tmp_path / "work"
    p1, _ = run_detection(str(sample_video), sample_calibration,
                          work_dir=str(work))
    other = tmp_path / "same_name.mp4"
    shutil.copy(str(sample_video), str(other))
    p2, _ = run_detection(str(other), sample_calibration, work_dir=str(work))
    assert p2 == p1
    data = load_events(p2)
    assert data["video"] == str(other)               # 重新检测并归属新视频
    assert len(data["events"]) == 1


def test_续跑合并已存点与重检点(sample_video, sample_calibration, tmp_path):
    """预置只含前 2 个检出点的检查点：续跑重检其后帧并与已存点合并，
    结果应与无检查点直跑完全一致（压到重检出区间的去重/合并路径）。"""
    from bbh.detector import detect_positions
    from bbh.pipeline import _calib_key
    import bbh.pipeline as pl
    full, _ = detect_positions(sample_video, sample_calibration)
    ckpt = {"frame_idx": 200,
            "positions": [[p.t, p.x, p.y, p.area] for p in full[:2]],
            "positions_len": 2, "fps": 30.0, "video": str(sample_video),
            "calib": _calib_key(sample_calibration),
            "detect_ver": pl.detect_fingerprint()}
    (tmp_path / "checkpoint.json").write_text(json.dumps(ckpt), encoding="utf-8")
    p1, _ = run_detection(sample_video, sample_calibration, work_dir=str(tmp_path))
    p2, _ = run_detection(sample_video, sample_calibration,
                          work_dir=str(tmp_path / "direct"))
    assert [(e.t, e.status) for e in load_events(p1)["events"]] == \
           [(e.t, e.status) for e in load_events(p2)["events"]]


def test_旧检测版本的检查点作废重跑(sample_video, sample_calibration, tmp_path):
    """检查点复用身份必须含检测器版本（DETECT_VERSION）：v4 换 PTS 计时后，
    旧钟（帧号/fps）检查点若被混用，同一段视频会产出两套时刻的检出点，
    时间轴错乱；v9 坐标加位移校正后同理。"""
    import bbh.pipeline as pl
    from bbh.detector import detect_positions
    from bbh.pipeline import _calib_key
    full, _ = detect_positions(sample_video, sample_calibration)
    ckpt = {"frame_idx": 200,
            "positions": [[p.t, p.x, p.y, p.area] for p in full[:2]],
            "positions_len": 2, "fps": 30.0, "video": str(sample_video),
            "calib": _calib_key(sample_calibration),
            "detect_ver": f"{pl.DETECT_VERSION - 1}-{pl.DETECTOR_NAME}"}   # 旧版本检查点
    (tmp_path / "checkpoint.json").write_text(json.dumps(ckpt), encoding="utf-8")
    p1, _ = pl.run_detection(sample_video, sample_calibration,
                             work_dir=str(tmp_path))
    p2, _ = pl.run_detection(sample_video, sample_calibration,
                             work_dir=str(tmp_path / "direct"))
    # 旧检查点被作废 → 从头全量检测 → 与直跑一致（若误续跑会混入双份头部点）
    assert [(e.t, e.status) for e in load_events(p1)["events"]] == \
           [(e.t, e.status) for e in load_events(p2)["events"]]


def test_duration兜底与fps可疑时警告(sample_video, sample_calibration, tmp_path, monkeypatch):
    import bbh.pipeline as pl
    orig = pl.detect_positions

    def weird_meta(video, calib, step=2, progress_cb=None, resume_frame=0,
                   on_detect=None, motion=None, end_frame=None):
        positions, meta = orig(video, calib, step, progress_cb, resume_frame,
                               on_detect, motion, end_frame)
        meta["duration"] = 0.0          # 模拟容器元数据异常
        meta["fps"] = 0.5               # 模拟 fps 异常
        return positions, meta

    monkeypatch.setattr(pl, "detect_positions", weird_meta)
    events_path, warning = run_detection(sample_video, sample_calibration,
                                         work_dir=str(tmp_path))
    assert warning is not None
    data = load_events(events_path)
    assert data["duration"] > 0          # 已用 processed_frames/fps 兜底


def test_OpenCV打不开时转码兜底检测(sample_video, sample_calibration, tmp_path,
                                    monkeypatch):
    """源视频编码 OpenCV 解不了 → 自动转 H.264 副本检测，产出与直跑一致。"""
    import bbh.pipeline as pl
    # 只放行转码副本：模拟"原视频 OpenCV 打不开"
    monkeypatch.setattr(pl, "_opencv_can_open",
                        lambda p: os.path.basename(p) == "det-source.mp4")
    stages = []
    events_path, warning = run_detection(sample_video, sample_calibration,
                                         work_dir=str(tmp_path),
                                         stage_cb=stages.append)
    data = load_events(events_path)
    assert len(data["events"]) == 1                       # 转码副本上完成检测
    assert (tmp_path / "det-source.mp4").exists()
    assert not os.path.exists(str(tmp_path / "det-source.mp4") + ".part")
    assert warning and "转码" in warning
    assert any("转码" in s for s in stages if s)          # 转码期间界面有提示
    assert stages[-1] is None                             # 转码完提示复位
    # 与直接跑原视频的结果一致（同一标定/内容）
    p2, _ = run_detection(sample_video, sample_calibration,
                          work_dir=str(tmp_path / "direct"))
    assert [(e.t, e.status) for e in data["events"]] == \
           [(e.t, e.status) for e in load_events(p2)["events"]]


def test_判定器升级复用检出流_不重跑检测(sample_video, sample_calibration,
                                        tmp_path, monkeypatch):
    """②级快路径：仅判定器升级（LOGIC_VERSION 变）而检测器未变
    （DETECT_VERSION 同）→ 跳过全片检测、秒级重放判定器——调判定参数的
    迭代从 ~110 分钟全片重跑缩到秒级（v9 起检出流 positions.json 落盘）。"""
    import bbh.pipeline as pl
    p1, _ = pl.run_detection(sample_video, sample_calibration,
                             work_dir=str(tmp_path))
    data = json.loads((tmp_path / "positions.json").read_text(encoding="utf-8"))
    assert data["detect_ver"] == pl.detect_fingerprint()
    assert len(data["positions"]) > 0            # 检出流真的存了

    def no_detect(*a, **k):
        raise AssertionError("检出流应被复用，检测器不应被调用")
    monkeypatch.setattr(pl, "detect_positions", no_detect)
    monkeypatch.setattr(pl, "LOGIC_VERSION", pl.LOGIC_VERSION + 1)
    p2, _ = pl.run_detection(sample_video, sample_calibration,
                             work_dir=str(tmp_path))
    ev = load_events(p2)
    assert ev["logic_ver"] == pl.LOGIC_VERSION   # 新逻辑版本号（重放过判定器）
    assert len(ev["events"]) == 1                # 判定结果与检出流一致


def test_检测器升级作废检出流_全片重检(sample_video, sample_calibration,
                                      tmp_path, monkeypatch):
    """检测器行为变化（DETECT_VERSION+）→ 检出流不能复用（旧坐标/旧 ROI
    产物配新检测逻辑会错位），必须全片重检并落盘新版本检出流。"""
    import bbh.pipeline as pl
    pl.run_detection(sample_video, sample_calibration, work_dir=str(tmp_path))
    calls = {"n": 0}
    orig = pl.detect_positions

    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    monkeypatch.setattr(pl, "detect_positions", counting)
    # 检测器变了判定产物也变 → 两版本都 +1（只 patch DETECT_VERSION 会被
    # ①级 events 复用短路，走不到检出流校验）
    monkeypatch.setattr(pl, "LOGIC_VERSION", pl.LOGIC_VERSION + 1)
    new_ver = pl.DETECT_VERSION + 1
    monkeypatch.setattr(pl, "DETECT_VERSION", new_ver)
    pl.run_detection(sample_video, sample_calibration, work_dir=str(tmp_path))
    assert calls["n"] == 1                        # 重新全片检测
    data = json.loads((tmp_path / "positions.json").read_text(encoding="utf-8"))
    assert data["detect_ver"] == pl.detect_fingerprint()          # 新版本检出流落盘


def test_损坏的检出流作废重跑(sample_video, sample_calibration, tmp_path,
                             monkeypatch):
    """positions.json 半截/损坏 → ②级复用作废、全量重跑，不崩。"""
    import bbh.pipeline as pl
    pl.run_detection(sample_video, sample_calibration, work_dir=str(tmp_path))
    (tmp_path / "positions.json").write_text("{ 半截", encoding="utf-8")
    monkeypatch.setattr(pl, "LOGIC_VERSION", pl.LOGIC_VERSION + 1)  # 逼出走 ①
    p2, _ = pl.run_detection(sample_video, sample_calibration, work_dir=str(tmp_path))
    assert len(load_events(p2)["events"]) == 1    # 重跑后结果正常
    data = json.loads((tmp_path / "positions.json").read_text(encoding="utf-8"))
    assert data["detect_ver"] == pl.detect_fingerprint()  # 检出流已重写为合法 JSON


def test_force跳过检出流复用全片重检(sample_video, sample_calibration,
                                     tmp_path, monkeypatch):
    """force=True：① events 与 ② 检出流两级复用都跳过。"""
    import bbh.pipeline as pl
    pl.run_detection(sample_video, sample_calibration, work_dir=str(tmp_path))
    calls = {"n": 0}
    orig = pl.detect_positions

    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    monkeypatch.setattr(pl, "detect_positions", counting)
    pl.run_detection(sample_video, sample_calibration, work_dir=str(tmp_path),
                     force=True)
    assert calls["n"] == 1                        # 不吃检出流缓存，重检了


def test_无参考帧警告与不可读参考帧降级(sample_video, sample_calibration,
                                       tmp_path):
    """ref_image=None → 附「位移校正未启用」提示；路径不可读 → 同样降级
    不校正且警告可读（不崩不静默）。可读参考帧 → 无该警告。"""
    _, w1 = run_detection(sample_video, sample_calibration,
                          work_dir=str(tmp_path), ref_image=None)
    assert "参考帧" in w1 and "未启用" in w1

    work2 = tmp_path / "w2"
    _, w2 = run_detection(sample_video, sample_calibration, work_dir=str(work2),
                          ref_image=str(tmp_path / "nope.png"))
    assert "读取失败" in w2


def _craft_positions(work, video, calib, shift, duration):
    """手工构造 positions.json 检出流（逼走 ② 级复用路径，不动检测器，
    也不需要真视频文件——复用路径的 video 只做身份比对）。"""
    import bbh.pipeline as pl
    from bbh.pipeline import _calib_key
    data = {"video": video, "calib": _calib_key(calib),
            "detect_ver": pl.detect_fingerprint(),
            "meta": {"duration": duration, "fps": 30.0, "w": 640, "h": 360},
            "shift": [list(s) for s in shift], "positions": [],
            "positions_len": 0}
    (work / "positions.json").write_text(json.dumps(data, ensure_ascii=False),
                                         encoding="utf-8")


def _write_ref_png(path):
    """可读参考帧（随机噪声图即可——只需 load_ref_image 能读）。
    必须走 save_ref_image：pytest 临时目录含中文测试名，cv2.imwrite
    在中文路径下静默失败（文件根本没写出来）。"""
    from bbh.motion import save_ref_image
    save_ref_image(np.random.randint(0, 255, (64, 64, 3), np.uint8), str(path))


def _dense(t0, t1, step=6.0):
    """真实节奏的采样表（健康跟踪 ~6s 一个样本，只给端点的假数据会
    把端点间距误当中段孔洞）。"""
    return [(t, 0, 0) for t in np.arange(t0, t1 + 0.01, step)]


def test_校正采样断流距片尾过远时警告(sample_calibration, tmp_path):
    """镜头中途被挪 → 门控全拒收 → 采样断流 → 校正悄悄冻结在旧值
    （09-04_1 根因：t=1518.8 后 79% 视频校正失效、high 占比 48%→7% 且
    warning=None 全静默）。缺口>180s 必须显式警告并给出断流时段。"""
    _write_ref_png(tmp_path / "ref.png")
    _craft_positions(tmp_path, "v.mp4", sample_calibration,
                     shift=_dense(0, 60), duration=600.0)
    _, warning = run_detection("v.mp4", sample_calibration,
                               work_dir=str(tmp_path),
                               ref_image=str(tmp_path / "ref.png"))
    assert warning and "未覆盖时段" in warning
    assert "1:00~10:00" in warning          # 断流时段（末次采样→片尾）


def test_校正片头无采样时警告(sample_calibration, tmp_path):
    """用户把参考帧标在移动后的时刻 → 片头段全部失配（09-04_1 用户手动
    重标定后的镜像病况：首样本 t=1531 前 25 分钟零覆盖）→ 片头孔洞要报。"""
    _write_ref_png(tmp_path / "ref.png")
    _craft_positions(tmp_path, "v.mp4", sample_calibration,
                     shift=_dense(200, 595), duration=600.0)
    _, warning = run_detection("v.mp4", sample_calibration,
                               work_dir=str(tmp_path),
                               ref_image=str(tmp_path / "ref.png"))
    assert warning and "0:00~3:20" in warning


def test_校正中段断流时警告(sample_calibration, tmp_path):
    """挪走又挪回（参考帧重新匹配上）→ 中段孔洞，同样要报。"""
    _write_ref_png(tmp_path / "ref.png")
    _craft_positions(tmp_path, "v.mp4", sample_calibration,
                     shift=_dense(0, 96) + _dense(500, 595), duration=600.0)
    _, warning = run_detection("v.mp4", sample_calibration,
                               work_dir=str(tmp_path),
                               ref_image=str(tmp_path / "ref.png"))
    assert warning and "1:36~8:20" in warning


def test_校正采样覆盖到片尾时不警告(sample_calibration, tmp_path):
    """固定机位正常全程有采样（末样本距片尾仅几秒）→ 不得误报。"""
    _write_ref_png(tmp_path / "ref.png")
    _craft_positions(tmp_path, "v.mp4", sample_calibration,
                     shift=_dense(0, 595), duration=600.0)
    _, warning = run_detection("v.mp4", sample_calibration,
                               work_dir=str(tmp_path),
                               ref_image=str(tmp_path / "ref.png"))
    assert not warning or "未覆盖时段" not in warning
    assert "无有效采样" not in (warning or "")


def test_校正全程零采样时警告(sample_calibration, tmp_path):
    """参考帧从头就匹配不上（好匹配<MATCH_MIN）→ 全程零采样单独文案。"""
    _write_ref_png(tmp_path / "ref.png")
    _craft_positions(tmp_path, "v.mp4", sample_calibration,
                     shift=[], duration=600.0)
    _, warning = run_detection("v.mp4", sample_calibration,
                               work_dir=str(tmp_path),
                               ref_image=str(tmp_path / "ref.png"))
    assert warning and "全程无有效采样" in warning


def test_参考帧未启用时不叠报失效警告(sample_calibration, tmp_path):
    """ref_image=None 时校正本来就未启用（NO_REF_NOTE 已告知），
    断流检查不该在同一份警告里重复报「失效」。"""
    _craft_positions(tmp_path, "v.mp4", sample_calibration,
                     shift=[(0.0, 0, 0), (60.0, 0, 0)], duration=600.0)
    _, warning = run_detection("v.mp4", sample_calibration,
                               work_dir=str(tmp_path), ref_image=None)
    assert "无有效采样" not in (warning or "")
    assert "参考帧" in warning


class Test冷却释放:
    """v14（2026-09-24）_cooldown_release_pass：网门降级行不占冷却钟的二趟
    编排。占钟语义本身在 test_goal_decider 的 Test冷却释放。"""

    # 与判定器用例同款几何：一次下穿 80.2（被网门降级）+ 冷却窗内被吞的
    # 第二次下穿 80.7（篮板补篮形态）
    PTS = [BallPos(t=80.0, x=340, y=100, area=400),
           BallPos(t=80.2, x=340, y=190, area=400),
           BallPos(t=80.5, x=340, y=100, area=400),
           BallPos(t=80.7, x=340, y=190, area=400)]

    @staticmethod
    def _r(calib):
        return {"calib": calib, "shift": []}

    def test_降级行后的新行浮表_基线行原样保留(self, sample_calibration,
                                                monkeypatch):
        import bbh.pipeline as pl
        calls = []
        monkeypatch.setattr(
            pl, "net_evidence_pass",
            lambda vp, wd, calib, shift, events, stage_cb=None:
                (calls.append([e.t for e in events]), events)[1])
        evs = [GoalEvent(t=80.2, confidence=0.9, status="review")]
        out = pl._cooldown_release_pass(
            "v.mp4", "wd", self._r(sample_calibration), self.PTS, evs,
            pre_status=["high"])
        # 基线行是同一个对象（一趟为准，绝不重写）；新行附加在后
        assert out[0] is evs[0]
        assert [round(e.t, 2) for e in out] == [80.2, 80.7]
        # v18：释放行 conf 0.75 够 high 线但穿筐后无下方实体检出 → entity
        # 硬门 review（v10 R1 曾越过 entity 硬门升绿，怪序已修）
        assert out[1].status == "review"
        assert out[1].review_reason == "entity"
        assert calls == [[80.7]]                # 只有新行过网门

    def test_新行过网门定终档(self, sample_calibration, monkeypatch):
        import bbh.pipeline as pl

        def demote(vp, wd, calib, shift, events, stage_cb=None):
            for e in events:
                e.status = "review"             # 网静止 → 降级
            return events
        monkeypatch.setattr(pl, "net_evidence_pass", demote)
        evs = [GoalEvent(t=80.2, confidence=0.9, status="review")]
        out = pl._cooldown_release_pass(
            "v.mp4", "wd", self._r(sample_calibration), self.PTS, evs,
            pre_status=["high"])
        assert [e.status for e in out] == ["review", "review"]

    def test_小筐段不放行(self, monkeypatch):
        import bbh.pipeline as pl
        monkeypatch.setattr(
            pl, "net_evidence_pass",
            lambda *a, **k: pytest.fail("小筐段不该走到测网"))
        small = Calibration(hoop_rect=(300, 140, 40, 20),
                            hsv_low=(0, 80, 120), hsv_high=(25, 255, 255),
                            frame_size=(640, 360))
        assert small.hoop_rect[2] < pl.NET_RELEASE_MIN_HW
        evs = [GoalEvent(t=80.2, confidence=0.9, status="review")]
        out = pl._cooldown_release_pass(
            "v.mp4", "wd", self._r(small), self.PTS, evs, pre_status=["high"])
        assert out is evs

    def test_无降级行_原样返回(self, sample_calibration, monkeypatch):
        import bbh.pipeline as pl
        monkeypatch.setattr(
            pl, "decide_goals",
            lambda *a, **k: pytest.fail("无降级行不该跑二趟"))
        evs = [GoalEvent(t=80.2, confidence=0.9, status="review")]
        out = pl._cooldown_release_pass(
            "v.mp4", "wd", self._r(sample_calibration), self.PTS, evs,
            pre_status=["review"])              # 网门前就是 review：没降级
        assert out is evs
