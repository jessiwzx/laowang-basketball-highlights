import os
import re

import cv2
from bbh import _gr_compat as gr
import numpy as np
import pytest

import bbh.app as app_module
from bbh.app import (_refresh_status, add_point, build_calibration,
                     clean_path, extract_frame, generate_highlight,
                     load_events_table, refine_hsv, start_detection)
from bbh.models import Calibration
from bbh.pipeline import run_detection


@pytest.fixture()
def app_state():
    """_state 是模块级全局：每测后复位，避免线程状态串场。_cancel_evt
    同理在测前清零——残留置位会把 running 分支顶成"正在取消"。"""
    saved = dict(app_module._state)
    app_module._cancel_evt.clear()
    yield app_module._state
    app_module._state.clear()
    app_module._state.update(saved)


def _click(x, y):
    """构造 gr.Image .select 事件的 SelectData（index 为原图像素坐标）。"""
    return gr.SelectData(None, {"index": [x, y], "value": None})


# 原有两条唯一调用 build_app() 的用例（test_build_app可构建 /
# test_Timer轮询隐藏进度遮罩）已随 A4 删除。它们断言的是 demo.title、
# demo.fns、demo.config["dependencies"] —— **真 gradio 的内部结构**；A4 把
# bbh/app.py 的 gradio 换成了 bbh/_gr_compat.py 的替身，那三样不再存在，也不该
# 被替身伪造（伪造等于让「旧界面还能跑」假装成功）。旧界面已作废（设计文档 §5
# 判据 7），这两条护的接线名单与 Timer 的 show_progress 都属于它。
# 新界面的接线由 tests/e2e/ 与 tests/test_web_*.py 覆盖。


def test_extract_frame取中段帧并清空点击(sample_video):
    frame, pts, msg, preview, calib_msg = extract_frame(sample_video)
    assert frame.shape == (360, 640, 3) and frame.dtype == np.uint8
    assert pts == [] and "筐口左端" in msg
    assert preview is None and calib_msg is None    # 换视频清掉旧预览/旧提示
    # conftest 合成视频每帧都有灰色筐线 y=154, x∈[300,380]
    assert abs(frame[154, 340].astype(int) - 180).max() <= 15


def test_extract_frame空路径不炸():
    frame, pts, msg, preview, calib_msg = extract_frame(None)
    assert frame is None and pts == []
    assert preview is None and calib_msg is None


def test_clean_path去空白与各类引号():
    assert clean_path('  "D:\\v\\a.mp4"  ') == r"D:\v\a.mp4"   # 英文引号+空白
    assert clean_path("'D:/v/a.mp4'") == "D:/v/a.mp4"          # 单引号
    assert clean_path("“D:\\v\\a.mp4”") == r"D:\v\a.mp4"       # 全角引号
    assert clean_path("") == "" and clean_path(None) == ""
    assert clean_path('"') == '"'                              # 单个引号不动
    assert clean_path("D:\\v\\a.mp4") == r"D:\v\a.mp4"         # 无引号原样


def test_extract_frame带引号路径能用(sample_video):
    """资源管理器「复制文件地址」给出的路径带英文引号，应自动剥掉。"""
    frame, pts, msg, _, _ = extract_frame(f'"{sample_video}"')
    assert frame is not None and frame.shape == (360, 640, 3)
    assert pts == []


def test_extract_frame不存在路径直接报错():
    with pytest.raises(gr.Error) as ei:
        extract_frame('"D:\\不存在\\x.mp4"')
    assert "视频不存在" in ei.value.message


def test_extract_frame损坏视频报错带诊断(tmp_path):
    bad = tmp_path / "损坏.mp4"
    bad.write_bytes(b"\x00\x00\x00\x18ftypgarbage" + b"\xff" * 512)
    with pytest.raises(gr.Error) as ei:
        extract_frame(str(bad))
    assert "无法打开视频" in ei.value.message
    assert "ffmpeg" in ei.value.message        # 带 ffmpeg 探测输出便于定位编码问题


def test_open_fail_msg权限拒绝时给占用提示(tmp_path, monkeypatch):
    """ffmpeg 报 Permission denied（大文件上传未收尾被占用）时提示改走路径。"""
    bad = tmp_path / "stodownload.mp4"
    bad.write_bytes(b"\x00" * 16)

    class _Probe:
        returncode = 1
        stderr = "[in#0 @ 0x1] Error opening input: Permission denied"

    monkeypatch.setattr(app_module.subprocess, "run",
                        lambda *a, **k: _Probe())
    msg = app_module._open_fail_msg(str(bad))
    assert "占用" in msg and "填本机路径" in msg


def test_extract_frame_ffmpeg兜底路径能出帧(sample_video, monkeypatch):
    """OpenCV 打不开（isOpened 恒 False）但文件本身有效时，退回 ffmpeg 抽中点帧。"""

    class _UnopenableCap:
        def __init__(self, *a, **k):
            pass

        def isOpened(self):
            return False

        def release(self):
            pass

    monkeypatch.setattr(app_module.cv2, "VideoCapture", _UnopenableCap)
    frame, pts, msg, _, _ = extract_frame(str(sample_video))
    assert frame is not None and frame.shape == (360, 640, 3)
    assert pts == [] and "筐口左端" in msg


def test_add_point按点击顺序追加并提示():
    pts, msg1 = add_point([], _click(300, 154))
    assert pts == [(300.0, 154.0)] and "筐口右端" in msg1
    pts, msg2 = add_point(pts, _click(380, 154))
    assert len(pts) == 2 and "球" in msg2
    pts, msg3 = add_point(pts, _click(340, 220))
    assert len(pts) == 3 and "生成标定" in msg3


def test_build_calibration生成并保存(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)          # 标定随视频走：落 CWD/work/<视频名>/
    # RGB 帧：灰筐线 + 橙球（与 conftest 的 BGR(20,100,245) 对应）
    frame = np.full((360, 640, 3), (50, 44, 40), np.uint8)
    cv2.line(frame, (300, 154), (380, 154), (180, 180, 180), 3)
    cv2.circle(frame, (340, 220), 8, (245, 100, 20), -1)
    pts = [(300.0, 154.0), (380.0, 154.0), (340.0, 220.0)]

    preview, msg, s0, v0 = build_calibration(pts, frame,
                                             str(tmp_path / "v.mp4"))

    assert preview.shape == (360, 640, 3)
    assert "标定已保存" in msg
    calib = Calibration.load(os.path.join("work", "v", "calibration.json"))
    assert calib.frame_size == (640, 360)
    x, y, w, h = calib.hoop_rect
    assert x <= 300 and x + w >= 380 and y <= 154 <= y + h  # 筐口矩形覆盖两端点
    hsv = cv2.cvtColor(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR), cv2.COLOR_BGR2HSV)
    h_, s_, v_ = hsv[220, 340]  # 球心像素应落在标定 HSV 范围内
    assert calib.hsv_low[0] <= h_ <= calib.hsv_high[0]
    assert calib.hsv_low[1] <= s_ <= calib.hsv_high[1]
    assert calib.hsv_low[2] <= v_ <= calib.hsv_high[2]
    assert preview[220, 340].tolist() == [0, 255, 0]  # 球位置被染成绿色
    assert (s0, v0) == (calib.hsv_low[1], calib.hsv_low[2])  # 滑块初值对齐生成值


def _sample_rgb_frame():
    """⚙ 标定设置测试用的 RGB 参考帧（灰筐线 + 橙球，球 BGR(20,100,245) 的 S≈234）。"""
    frame = np.full((360, 640, 3), (50, 44, 40), np.uint8)
    cv2.line(frame, (300, 154), (380, 154), (180, 180, 180), 3)
    cv2.circle(frame, (340, 220), 8, (245, 100, 20), -1)
    return frame


def test_refine_hsv_调大S下限滤掉低饱和色(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    vid = str(tmp_path / "v.mp4")
    frame = _sample_rgb_frame()
    build_calibration([(300.0, 154.0), (380.0, 154.0), (340.0, 220.0)],
                      frame, vid)

    preview, msg = refine_hsv(frame, 250, 100, vid)   # S 下限 250 > 球的 S≈234：球不再绿
    assert "0 命中" in msg and "S 下限" in msg   # 零命中要指出卡在哪个通道
    assert preview[220, 340].tolist() != [0, 255, 0]
    calib = Calibration.load(os.path.join("work", "v", "calibration.json"))
    assert calib.hsv_low[1] == 250 and calib.hsv_low[2] == 100

    preview, msg = refine_hsv(frame, 100, 100, vid)   # 放回 100：球恢复绿
    assert preview[220, 340].tolist() == [0, 255, 0]
    assert "已保存" in msg and "命中" in msg and "0 命中" not in msg


def test_refine_hsv_缺前置条件报错(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    vid = str(tmp_path / "v.mp4")
    with pytest.raises(gr.Error):
        refine_hsv(_sample_rgb_frame(), 100, 100, vid)   # 没生成过标定
    build_calibration([(300.0, 154.0), (380.0, 154.0), (340.0, 220.0)],
                      _sample_rgb_frame(), vid)
    with pytest.raises(gr.Error):
        refine_hsv(None, 100, 100, vid)                  # 参考帧缺失


def test_build_calibration点数不足报错():
    frame = np.zeros((360, 640, 3), np.uint8)
    with pytest.raises(gr.Error):        # 点数校验在前——没给视频也是先报点数
        build_calibration([(300.0, 154.0), (380.0, 154.0)], frame, "")
    with pytest.raises(gr.Error):
        build_calibration([], frame, "")


def test_build_calibration无帧报错():
    with pytest.raises(gr.Error):
        build_calibration([(300.0, 154.0), (380.0, 154.0), (340.0, 220.0)],
                          None, "")


# ---------- 检测与状态（剪辑台检测流水线） ----------

def test_未标定时开始检测报错(sample_video, tmp_path, monkeypatch, app_state):
    monkeypatch.chdir(tmp_path)                 # 标定按生产布局找 work/<视频名>/
    monkeypatch.setattr(app_module, "CALIB_FILE", str(tmp_path / "不存在.json"))
    with pytest.raises(gr.Error) as ei:
        start_detection(str(sample_video))
    assert "标定设置" in ei.value.message       # 指引去哪完成标定
    assert not app_module._state["running"]    # 未启动线程


def test_检测完成状态(sample_video, sample_calibration, tmp_path, monkeypatch,
                    app_state):
    # 标定按生产布局存 work 目录内（标定随视频走）——load_segments 只认
    # work/<视频名>/calibration.json，不再支持目录外绝对路径注入。
    work_dir = tmp_path / "work"
    work_dir.mkdir(parents=True)
    sample_calibration.save(str(work_dir / "calibration.json"))
    monkeypatch.setattr(app_module, "_work_dir_for", lambda vp: str(work_dir))

    start_detection(str(sample_video))              # 真实起后台线程
    app_module._state["thread"].join(timeout=30)
    assert not app_module._state["thread"].is_alive()   # 线程确已退出（M5）

    assert app_module._state["done"] is True
    assert app_module._state["error"] is None
    msg, pct = _refresh_status()
    assert "高置信 1 个" in msg and pct == 100
    assert (work_dir / "events.json").exists()


def test_刷新状态空闲态文案(app_state):
    app_module._state.update(running=False, done=False, error=None)
    msg, pct = _refresh_status()
    assert "空闲" in msg and pct == 0


def test_刷新状态转码阶段提示(app_state):
    """转码期间无帧进度，界面应显示阶段文案而不是 0/0 帧。"""
    app_module._state.update(running=True, stage="正在转码 H.264…",
                             error=None, done_frames=0, total_frames=0)
    msg, pct = _refresh_status()
    assert "转码" in msg and pct == 0
    app_module._state.update(stage=None)          # 转码完回到帧进度显示
    msg, pct = _refresh_status()
    assert "检测中" in msg


def test_重入防护(sample_video, sample_calibration, tmp_path, monkeypatch,
                  app_state):
    """检测进行中再点开始应报错，且不再起新线程（M2）。"""
    monkeypatch.chdir(tmp_path)                 # 标定查找走 work/<视频名>/
    calib_file = tmp_path / "calibration.json"
    sample_calibration.save(str(calib_file))
    monkeypatch.setattr(app_module, "CALIB_FILE", str(calib_file))
    app_module._state["running"] = True
    with pytest.raises(gr.Error) as ei:
        start_detection(str(sample_video))
    assert "已有检测在跑" in ei.value.message
    assert app_module._state["thread"] is None      # 未起线程


def test_标定损坏时开始检测立即报错(sample_video, tmp_path, monkeypatch,
                                    app_state):
    """文件在但不可解析：点击当场报错，而不是起线程后一轮检测才失败。"""
    monkeypatch.chdir(tmp_path)                 # 标定查找走 work/<视频名>/
    calib_file = tmp_path / "calibration.json"
    calib_file.write_text("{oops", encoding="utf-8")
    monkeypatch.setattr(app_module, "CALIB_FILE", str(calib_file))
    with pytest.raises(gr.Error) as ei:
        start_detection(str(sample_video))
    assert "标定" in ei.value.message
    assert not app_module._state["running"]         # 未启动线程


def test_页面卸载请求取消检测(app_state):
    """检测在跑时页面刷新/关闭（demo.unload）→ 置取消标志；空闲时不置。"""
    app_module._cancel_evt.clear()
    app_module._state.update(running=True)
    app_module._on_page_unload()
    assert app_module._cancel_evt.is_set()
    app_module._state.update(running=False)         # 空闲时卸载页面无副作用
    app_module._cancel_evt.clear()
    app_module._on_page_unload()
    assert not app_module._cancel_evt.is_set()


def test_刷新状态取消中文案(app_state):
    app_module._state.update(running=True, done_frames=3, total_frames=10,
                             stage=None)
    app_module._cancel_evt.set()
    msg, pct = _refresh_status()
    assert "正在取消" in msg and pct == 30


def test_刷新状态已取消文案(app_state):
    app_module._state.update(running=False, done=False, error=None,
                             cancelled=True)
    msg, pct = _refresh_status()
    assert "已取消" in msg and "续跑" in msg and pct == 0


# ---------- 剪辑台：复核与出片 ----------

@pytest.fixture()
def events_work(sample_video, sample_calibration, tmp_path, monkeypatch):
    """跑一遍真实检测产 events.json，并把 app 的 work 目录指到该处。"""
    work = tmp_path / "work"
    run_detection(str(sample_video), sample_calibration, work_dir=str(work))
    monkeypatch.setattr(app_module, "_work_dir_for", lambda vp: str(work))
    return work


def test_载入事件表格(events_work, sample_video):
    duration, rows = load_events_table(str(sample_video))
    assert duration > 0
    assert len(rows) == 1                            # conftest 场景只有 1 个进球
    # 8 列（2026-10-23 起第 8 列是 ★ 精选）
    checked, t_str, status, conf, before, after, tag, starred = rows[0]
    assert checked == 0 and status == "high"         # 默认全不勾（含高置信）
    assert before == 5 and after == 2                # 默认前后秒数
    assert tag == ""                                 # 未打标签时空串
    assert starred == 0                              # 默认全不打星（无 starred.json）
    t = int(t_str[:2]) * 60 + float(t_str[3:])       # mm:ss.s 可往返解析
    assert 2.0 <= t <= 3.0                           # conftest 进球在 ~2.5s


class Test勾选持久化:
    """勾选状态存 work/<视频名>/selection.json（键=原始检测时刻，与标签
    同口径），表格每次勾选即落盘、重载恢复；默认全不勾（2026-08-22）。"""

    def test_表格勾选落盘_重载恢复(self, events_work, sample_video):
        from bbh.app import on_table_edit, project_rows
        _, rows = load_events_table(str(sample_video))
        proj = project_rows(rows)
        proj[0][0] = 1                                # 勾上唯一一行
        on_table_edit(str(sample_video), rows, proj)
        assert (events_work / "selection.json").exists()
        _, rows2 = load_events_table(str(sample_video))
        assert rows2[0][0] == 1                       # 重载后勾选还在

    def test_键按原始时刻_offset下勾选不丢(self, events_work, sample_video):
        """时刻校准改的是显示时间——勾选键存原始检测时刻，换偏移重载不丢。"""
        from bbh.app import on_table_edit, project_rows
        _, rows = load_events_table(str(sample_video), offset=0.4)
        proj = project_rows(rows)
        proj[0][0] = 1
        on_table_edit(str(sample_video), rows, proj)
        _, r_same = load_events_table(str(sample_video), offset=0.4)
        _, r_zero = load_events_table(str(sample_video))
        assert r_same[0][0] == 1 and r_zero[0][0] == 1

    def test_失效键安全忽略(self, events_work, sample_video):
        """重跑检测后事件时刻变了——旧勾选键对不上就静默作废，不报错。"""
        (events_work / "selection.json").write_text(
            '["99:59.9"]', encoding="utf-8")
        _, rows = load_events_table(str(sample_video))
        assert rows[0][0] == 0

    def test_标签在offset下编辑不丢(self, events_work, sample_video):
        """同口径修复：标签落盘也按原始时刻存——offset 下打标签重载不丢。"""
        from bbh.app import set_row_tag
        _, rows = load_events_table(str(sample_video), offset=0.4)
        _, rows, _ = set_row_tag(str(sample_video), rows, 0, "小王")
        _, rows2 = load_events_table(str(sample_video), offset=0.4)
        assert rows2[0][6] == "小王"


def test_未检测时载入报错(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "_work_dir_for",
                        lambda vp: str(tmp_path / "空"))
    with pytest.raises(gr.Error):
        load_events_table("whatever.mp4")


def test_载入按钮五元组不炸(events_work, sample_video):
    """「载入检测结果」全链路：曾因 _cur_row_md(rows, None) 的 0<=None 崩溃。"""
    from bbh.app import load_table_for_ui
    dur_md, proj, rows, cur, panel = load_table_for_ui(str(sample_video))
    assert "视频总长" in dur_md and len(rows) > 0 and len(proj[0]) == 4
    dur_md2, _, rows2, _, _ = load_table_for_ui(f'"{sample_video}"')
    assert len(rows2) == len(rows)


def test_生成集锦端到端(events_work, sample_video):
    # 钉旧横屏路径：竖屏需筐标定（events_work 的 work 目录无标定走不了），
    # 本族用例走 vertical=False；竖屏路由见 test_vrender.TestAppRouting
    _, rows = load_events_table(str(sample_video))
    rows[0][0] = 1                                   # 默认全不勾，手动勾上
    out = generate_highlight(str(sample_video), rows, vertical=False)
    assert os.path.basename(out) == "highlight-横.mp4"    # 文件名带画幅
    assert os.path.getsize(out) > 0
    rows[0][0] = 0                                   # 取消勾选
    with pytest.raises(gr.Error) as ei:
        generate_highlight(str(sample_video), rows, vertical=False)
    assert "至少勾选" in ei.value.message


def test_预览切片(events_work, sample_video):
    """点行切预览：固定名 preview.mp4（反复预览不堆文件）且确实落盘。
    表头点击/空表的负路径在 test_点击表头或空表报错；越界行号见下。"""
    from bbh.app import preview_row_click
    _, rows = load_events_table(str(sample_video))
    out = preview_row_click(str(sample_video), rows, _row_evt(0, 1))
    assert "preview.mp4" in out[0]                   # 播器 HTML 指向固定名切片
    assert os.path.getsize(os.path.join(events_work, "preview.mp4")) > 0
    with pytest.raises(gr.Error):
        preview_row_click(str(sample_video), rows, _row_evt(5, 1))   # 行号越界


def _row_evt(row, col=1):
    """构造 gr.Dataframe .select 事件（index = [行, 列]）。"""
    return gr.SelectData(None, {"index": [row, col], "value": None})


def test_点击表格行即预览(events_work, sample_video):
    from bbh.app import preview_row_click
    _, rows = load_events_table(str(sample_video))
    html, _, cur, panel, *_ = preview_row_click(str(sample_video), rows, _row_evt(0))
    assert "<video" in html and "preview.mp4" in html
    assert "/gradio_api/file=" in html       # gradio 5 文件路由前缀（曾裸写 /file= 而 404）
    # autoplay 必须带 muted：切片耗时数秒，回到浏览器时用户手势激活已过期，
    # 未静音自动播放被策略拦截 → 黑屏不动（真机 Edge CDP 复现并验证过）
    assert "autoplay" in html and "muted" in html
    # URL 必须带 mtime 击穿参数：点另一行重写同一 preview.mp4 后同 URL
    # 命中浏览器缓存不重拉 → 预览切不动（真机 Edge CDP 复现并验证过）
    assert re.search(r"\.mp4\?v=\d+\.\d+", html)
    assert cur == 0                                  # 同步设为「当前行」
    assert "第 1 条" in panel and "高置信" in panel and "前 5s" in panel  # 行信息回显
    # 判定依据随行展示（方案B）：位置 + 双侧证据点数/跨度 + 置信度构成算式
    assert "判定依据" in panel and "上方证据" in panel and "筐心" in panel
    # 仿真轨迹：上方 11 点/0.33s、下方 14 点/0.47s 均 0.20 档，筐心 0.20
    assert "置信度 0.90 ＝ 基础 0.30 ＋ 上方证据 0.20 ＋ 下方证据 0.20 ＋ 位置 0.20" in panel
    assert "第 1 条" in panel and "标签" in panel
    # 分色条按切片实际前后长度（视频 6s、进球 t → 前被裁到 t、后被裁到 6-t）
    t = int(rows[0][1][:2]) * 60 + float(rows[0][1][3:])
    split_b, split_a = min(5, t), min(2, 6 - t)
    assert f"{100 * split_b / (split_b + split_a):.1f}%" in html
    assert "进球前" in html and "进球后" in html
    # 改过前后秒再点同一行 → 预览与面板都反映新值
    rows[0][4], rows[0][5] = 8, 2
    _, _, _, panel, *_ = preview_row_click(str(sample_video), rows, _row_evt(0))
    assert "前 8s + 后 2s" in panel


def test_点击表头或空表报错(events_work, sample_video):
    from bbh.app import preview_row_click
    _, rows = load_events_table(str(sample_video))
    with pytest.raises(gr.Error):                    # 表头（行号非数据行）
        preview_row_click(str(sample_video), rows, _row_evt(None))
    with pytest.raises(gr.Error):                    # 表格还没载入
        preview_row_click(str(sample_video), None, _row_evt(0))


def test_预览时长帧准确(events_work, sample_video):
    """重编码预览的时长应等于所设窗口（流复制+关键帧对齐时窗口会漂移）。
    直测 _cut_preview：帧精确重编码本来就是它的事。"""
    from bbh.app import _cut_preview, _parse_t
    _, rows = load_events_table(str(sample_video))
    t = _parse_t(rows[0][1])
    out = _cut_preview(str(sample_video), t, 1, 2)   # 窗口 [t-1, t+2] = 3s
    cap = cv2.VideoCapture(out)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    assert 2.4 <= n / fps <= 3.6                     # ≈3s（编码容差）


def test_预览切片起点永不格式化成零(sample_video, tmp_path, monkeypatch):
    """HEVC+AAC 手机源（09-11_3 实测）-ss 0 时 mp4 muxer 收到负 DTS 包直接
    "Not yet implemented" 炸掉——首个进球 t<前窗秒数时起点会被 clamp 成 0。
    真复现需带音轨真机源（合成视频无音轨测不出），此处钉命令行护栏：
    -ss 恒 >0（1ms 触发 seek 路径归零时间戳），中段窗口不受影响。

    必须 chdir 到 tmp：这条是**唯一**直接调 `_cut_preview` 而没经 events_work
    夹具的用例，而 `_cut_preview` 的落点是 `_work_dir_for()` 那串相对路径。
    漏掉这行时下面的假 subprocess 会照 `work/sample/preview.mp4` 建目录写档 ——
    cwd=仓库根 ⇒ **写进用户真实的 `work/`**（实测：全量跑一次就重写一次仓库根
    `work/sample/preview.mp4`，1 字节）。装上 work/ 写守卫后这行仍是必需的：
    守卫只把写入改道到 tmp，不改「该 chdir」这件事。"""
    import types

    monkeypatch.chdir(tmp_path)                      # 隔离：别写用户真实的 work/
    seen = []

    class _R:
        returncode = 0

    def fake_run(cmd, **k):
        seen.append(cmd)
        os.makedirs(os.path.dirname(cmd[-1]), exist_ok=True)
        with open(cmd[-1], "wb") as f:
            f.write(b"x")
        return _R()

    monkeypatch.setattr(app_module, "subprocess",
                        types.SimpleNamespace(run=fake_run))
    from bbh.app import _cut_preview
    _cut_preview(str(sample_video), 4.3, 5, 2)       # t-5 <0 → clamp
    _cut_preview(str(sample_video), 1000.0, 5, 2)    # 正常中段
    sss = [c[c.index("-ss") + 1] for c in seen]
    assert sss[0] == "0.001"                         # 起点被抬离 0
    assert sss[1] == "995.000"                       # 其余窗口原样


def test_相邻片段不合并_各自预览(events_work, sample_video):
    """实测反馈：用户不要合并——窗口重叠的相邻行各预览各的窗口。"""
    from bbh.app import preview_row_click
    rows = [[1, "00:02.0", "high", "0.90", 1, 1, ""],
            [1, "00:03.0", "high", "0.90", 1, 1, ""]]
    html, *_ = preview_row_click(str(sample_video), rows, _row_evt(0))
    assert "50.0%" in html                          # 本行自己的窗口 [1,3]


def test_进球时刻校准偏移(events_work, sample_video):
    """offset 作用在展示/切片层：正数整体后移，负数钳到 0，不动 events.json。"""
    _, rows0 = load_events_table(str(sample_video))
    t0 = int(rows0[0][1][:2]) * 60 + float(rows0[0][1][3:])
    _, rows = load_events_table(str(sample_video), offset=0.4)
    t = int(rows[0][1][:2]) * 60 + float(rows[0][1][3:])
    assert t == pytest.approx(t0 + 0.4, abs=0.05)
    # 大负偏移不会出现负时刻
    _, rows = load_events_table(str(sample_video), offset=-999)
    assert rows[0][1].startswith("00:00.0") or float(rows[0][1][3:]) >= 0
    # 按钮链路把 offset 传下去
    from bbh.app import load_table_for_ui
    _, _, rows_a, _, _ = load_table_for_ui(str(sample_video))
    _, _, rows_b, _, _ = load_table_for_ui(str(sample_video), 0.4)
    assert rows_a[0][1] != rows_b[0][1]


def test_预览解析超100分钟时刻(sample_video, tmp_path, monkeypatch):
    """110 分钟视频的 100:00 后时刻是三位分钟（"101:16.5"）——旧解析
    [:2]/[3:] 取到 ":16.5" 直接 float 崩溃，点行报错、预览不切换
    （2026-08-22 真机反馈：22/240 条放不了全是 t≥6000s 的行）。"""
    from bbh.app import _parse_t, preview_row_click
    assert _parse_t("101:16.5") == pytest.approx(101 * 60 + 16.5)
    assert _parse_t("09:59.9") == pytest.approx(9 * 60 + 59.9)
    monkeypatch.chdir(tmp_path)                     # 判定依据要读 work/<视频名>/
    rows = [[1, "101:16.5", "high", "0.90", 15, 5, ""]]
    got = {}
    fake = tmp_path / "p.mp4"
    fake.write_bytes(b"x")
    monkeypatch.setattr(app_module, "_cut_preview",
                        lambda vp, t, b, a: got.update(t=t) or str(fake))
    monkeypatch.setattr(app_module, "_reason_for", lambda *a: "")
    preview_row_click(str(sample_video), rows, _row_evt(0))
    assert got["t"] == pytest.approx(101 * 60 + 16.5)


def test_生成集锦解析超100分钟时刻(monkeypatch, tmp_path):
    """generate_highlight 里的内联解析与 _parse_t 同病：超 100 分钟的行
    生成时也会崩/切错，统一走 _parse_t。"""
    from bbh.app import generate_highlight
    monkeypatch.chdir(tmp_path)                 # 生成路径要读 work/v/ 的 events
    rows = [[1, "102:49.2", "high", "0.90", 15, 5, ""]]
    got = {}
    monkeypatch.setattr(app_module, "load_events",
                        lambda p: {"duration": 7000.0, "events": []})
    monkeypatch.setattr(app_module.editor, "plan_clips",
                        lambda settings, duration: got.update(s=settings) or [])
    monkeypatch.setattr(app_module.editor, "build_highlight",
                        lambda *a, **k: "out.mp4")
    generate_highlight("v.mp4", rows, vertical=False)
    assert got["s"][0][0] == pytest.approx(102 * 60 + 49.2)


def test_extract_frame_at按时间抽帧(sample_video):
    from bbh.app import extract_frame_at
    frame, pts, msg, _, _ = extract_frame_at(str(sample_video), 2.5)
    assert frame is not None and frame.shape == (360, 640, 3)
    assert pts == []                              # 换帧清空点击
    assert abs(int(frame[160, 340, 0]) - 245) <= 25   # t=2.5s 球在 (340,160) 附近


def test_extract_frame_at无来源报错():
    from bbh.app import extract_frame_at
    with pytest.raises(gr.Error):
        extract_frame_at("", 10)
    with pytest.raises(gr.Error):
        extract_frame_at(None, "abc")             # 非数字时间


def test_extract_frame提示视频总长(sample_video):
    _, _, msg, _, _ = extract_frame(str(sample_video))
    assert "视频总长" in msg and "时间点" in msg


# ---------- 标签（2026-08-19 重构：下拉选标签 + 新增标签弹窗 + 生成弹窗多选） ----------

def test_下拉打标签更新表格并落盘(events_work, sample_video, tmp_path,
                                 monkeypatch):
    """下拉选标签 → 打给当前行并自动勾上（2026-08-23：打标即认定进球，
    省一次点击）；清空选择 = 去掉标签但**不**取消勾选。标签库不在这里动。"""
    from bbh.app import add_tag_to_library, set_row_tag
    monkeypatch.setattr(app_module, "TAGS_FILE", str(tmp_path / "tags.json"))
    add_tag_to_library("小王")
    _, rows = load_events_table(str(sample_video))
    assert rows[0][0] == 0                            # 默认全不勾
    proj, rows2, panel = set_row_tag(str(sample_video), rows, 0, "小王")
    assert rows2[0][6] == "小王" and proj[0][3] == "小王"
    assert rows2[0][0] == 1 and proj[0][0] == 1       # 打标即自动勾选
    assert "小王" in panel
    _, rows3, _ = set_row_tag(str(sample_video), rows2, 0, None)
    assert rows3[0][6] == ""                  # 清空选择 = 去掉该行标签
    assert rows3[0][0] == 1                   # 清标签不取消勾选
    _, rows4 = load_events_table(str(sample_video))  # 重开页面：勾选已落盘
    assert rows4[0][0] == 1 and rows4[0][6] == ""
    assert app_module._load_tags() == ["小王"]  # 打标签环节不动标签库
    with pytest.raises(gr.Error):             # 没先点行
        set_row_tag(str(sample_video), rows, None, "小王")


def test_新增标签入库并关弹窗(tmp_path, monkeypatch):
    from bbh.app import add_tag_to_library
    monkeypatch.setattr(app_module, "TAGS_FILE", str(tmp_path / "tags.json"))
    dd, box, dialog = add_tag_to_library("小王")
    assert app_module._load_tags() == ["小王"] and "小王" in dd["choices"]
    assert box == "" and dialog.get("visible") is False  # 清框关弹窗
    add_tag_to_library("小王")                        # 重名不重复加
    add_tag_to_library("  老李 ")                     # 去首尾空格
    assert app_module._load_tags() == ["小王", "老李"]
    with pytest.raises(gr.Error):
        add_tag_to_library("   ")                     # 空名


def test_标签按视频持久化_重载恢复(events_work, sample_video, tmp_path,
                                 monkeypatch):
    from bbh.app import set_row_tag
    monkeypatch.setattr(app_module, "TAGS_FILE", str(tmp_path / "tags.json"))
    _, rows = load_events_table(str(sample_video))
    set_row_tag(str(sample_video), rows, 0, "小王")
    # 重新载入（模拟关掉页面再打开）：标签恢复，不用重打
    _, rows2 = load_events_table(str(sample_video))
    assert rows2[0][6] == "小王"


def test_按标签生成集锦(events_work, sample_video, tmp_path, monkeypatch):
    from bbh.app import set_row_tag
    monkeypatch.setattr(app_module, "TAGS_FILE", str(tmp_path / "tags.json"))
    _, rows = load_events_table(str(sample_video))
    _, rows, _ = set_row_tag(str(sample_video), rows, 0, "小王")
    rows[0][0] = 1                                   # 默认全不勾，手动勾上
    out = generate_highlight(str(sample_video), rows, "小王", vertical=False)
    assert os.path.basename(out) == "highlight-横-小王.mp4"   # 画幅+标签命名
    assert os.path.getsize(out) > 0
    with pytest.raises(gr.Error) as ei:              # 勾选里没有该标签
        generate_highlight(str(sample_video), rows, "老李", vertical=False)
    assert "老李" in ei.value.message


def test_生成弹窗_多选各自成片_无进球标签跳过(events_work, sample_video,
                                            tmp_path, monkeypatch):
    """勾选 合集+小张+小王 → 小张有勾选进球出片、小王没有则跳过不挡别人。"""
    from bbh.app import (add_tag_to_library, set_row_tag,
                         open_gen_dialog, gen_selected, GEN_ALL_LABEL)
    monkeypatch.setattr(app_module, "TAGS_FILE", str(tmp_path / "tags.json"))
    add_tag_to_library("小张")
    add_tag_to_library("小王")
    _, rows = load_events_table(str(sample_video))
    _, rows, _ = set_row_tag(str(sample_video), rows, 0, "小张")
    rows[0][0] = 1                                   # 默认全不勾，手动勾上
    choices, dialog, stats, _vert = open_gen_dialog(str(sample_video), rows,
                                                    False)
    assert choices["choices"][0] == GEN_ALL_LABEL    # 合集永远第一项
    # 2026-10-18：默认 = 合集 + 本场有球的标签（小张有球、小王库里无球）
    assert choices["value"] == [GEN_ALL_LABEL, "小张"]
    assert dialog.get("visible") is True
    assert "总进球" in stats                          # 弹窗带勾选统计行
    steps = list(gen_selected(
        str(sample_video), rows, [GEN_ALL_LABEL, "小张", "小王"],
        vertical=False))
    assert steps[0][4].startswith("⏳") and "1/3" in steps[0][4]  # 状态反馈
    assert steps[0][3].get("visible") is False       # 一点生成立刻收弹窗
    first, html, back, dlg, report, _btn = steps[-1]
    assert os.path.basename(first) == "highlight-横.mp4"  # 预览位播合集
    assert "已生成 2 个视频" in report
    assert report.startswith("📊 进球统计")          # 结果清单头部带统计
    assert "highlight-横-小张.mp4" in report
    assert "小王" in report and "跳过" in report     # 无勾选进球的标签仅汇报
    assert "已生成" in html
    assert dlg.get("visible") is False and back.get("visible") is True
    assert os.path.getsize(os.path.join(app_module._work_dir_for(
        str(sample_video)), "highlight-横-小张.mp4")) > 0
    with pytest.raises(gr.Error):                    # 一个都没勾
        list(gen_selected(str(sample_video), rows, [], vertical=False))


def test_生成状态反馈_逐项进度与按钮防重(events_work, sample_video,
                                     tmp_path, monkeypatch):
    """生成器化 gen_selected（2026-08-22）：每项开工先弹「⏳ (k/N)」且按钮
    变灰；全部跳过时末次 yield ❌ 文案并恢复按钮（不再 raise 卡死灰按钮）。"""
    from bbh.app import (add_tag_to_library, gen_selected,
                         GEN_ALL_LABEL, set_row_tag)
    monkeypatch.setattr(app_module, "TAGS_FILE", str(tmp_path / "tags.json"))
    add_tag_to_library("小张")
    _, rows = load_events_table(str(sample_video))
    _, rows, _ = set_row_tag(str(sample_video), rows, 0, "小张")
    rows[0][0] = 1                                   # 默认全不勾，手动勾上
    steps = list(gen_selected(str(sample_video), rows,
                              [GEN_ALL_LABEL, "小张"], vertical=False))
    assert len(steps) == 3                           # 2 项进度 + 1 终态
    assert steps[0][5].get("interactive") is False   # 生成中按钮变灰
    assert "2/2" in steps[1][4]                      # 第二项开工即刷新进度
    assert steps[-1][5].get("interactive") is True   # 结束恢复可点
    assert "✅ 已生成 2 个视频" in steps[-1][4]

    rows[0][0] = 0                                   # 全不勾 → 全部跳过
    steps = list(gen_selected(str(sample_video), rows, ["小张"],
                              vertical=False))
    assert steps[-1][4].startswith("❌") and "没有生成任何视频" in steps[-1][4]
    assert steps[-1][5].get("interactive") is True   # 失败也要恢复按钮


class Test进球统计:
    """build_goal_stats：勾选行按标签计数，未标注归「其他」固定最后
    （2026-08-21）；2026-09-06 文案升级：日期前缀（文件名 YYYY-MM-DD →
    「8月29日」）+ 每人命中率；**2026-10-24 用户要求去掉命中率**（勾选不再
    等于"人工核对过"：精选星标后只勾部分球，那个比值实际是"精选率"）。下面
    带负向断言断「不含百分比」，防它哪天被顺手加回来。"""

    def test_按标签计数降序_其他最后_未勾选不计(self):
        from bbh.app import build_goal_stats
        rows = [
            [1, "00:02.5", "high", "0.90", 15, 5, "小王"],
            [1, "00:09.9", "high", "0.88", 15, 5, "小张"],
            [1, "00:20.1", "high", "0.85", 15, 5, "小王"],
            [0, "00:30.0", "high", "0.80", 15, 5, "小王"],   # 未勾选不计
            [1, "00:40.0", "high", "0.77", 15, 5, ""],        # 空标签 → 其他
            [1, "00:50.0", "high", "0.75", 15, 5, "小王"],
            [1, "01:00.0", "high", "0.70", 15, 5, None],      # None → 其他
        ]
        s = build_goal_stats("v.mp4", rows)
        assert "总进球 6" in s
        assert "小王 3 个" in s
        assert "小张 1 个" in s
        assert "其他 2 个" in s                         # 其他殿后
        assert s.index("小王") < s.index("小张") < s.index("其他")  # 降序+其他殿后
        # 2026-10-24：命中率已下线——分母（未勾行的标签总数）也不再参与
        assert "命中率" not in s and "%" not in s
        assert s == "📊 进球统计：总进球 6，小王 3 个，小张 1 个，其他 2 个"

    def test_文件名日期进前缀(self):
        from bbh.app import build_goal_stats
        rows = [[1, "00:02.5", "high", "0.9", 15, 5, "老王"]]
        assert "8月29日进球统计：总进球 1" in build_goal_stats(
            r"D:\videos\2026-08-29_3.mp4", rows)      # 去前导零
        assert "进球统计：总进球 1" in build_goal_stats("v.mp4", rows)  # 无日期退回

    def test_无未标注时省略其他(self):
        from bbh.app import build_goal_stats
        s = build_goal_stats("v.mp4",
                             [[1, "00:02.5", "high", "0.9", 15, 5, "小王"]])
        assert s == "📊 进球统计：总进球 1，小王 1 个" and "其他" not in s

    def test_全未标注_只剩总数加其他(self):
        from bbh.app import build_goal_stats
        s = build_goal_stats("v.mp4",
                             [[1, "00:02.5", "high", "0.9", 15, 5, ""],
                              [1, "00:09.9", "high", "0.8", 15, 5, None]])
        assert "总进球 2，其他 2 个" in s

    def test_空勾选与None表不炸(self):
        from bbh.app import build_goal_stats
        assert "尚未勾选" in build_goal_stats("v.mp4", [])
        assert "尚未勾选" in build_goal_stats("v.mp4", None)
        assert "尚未勾选" in build_goal_stats(
            "v.mp4", [[0, "00:02.5", "high", "0.9", 15, 5, "小王"]])  # 全未勾选

    def test_并列计数按首次出现顺序排(self):
        from bbh.app import build_goal_stats
        rows = [[1, "00:02.5", "high", "0.9", 15, 5, "后到的"],
                [1, "00:09.9", "high", "0.8", 15, 5, "先来的"]]
        s = build_goal_stats("v.mp4", rows)
        assert s.index("后到的") < s.index("先来的")    # 同数不跳变，稳定可读


def test_safe_filename去非法字符():
    from bbh.editor import safe_filename
    assert safe_filename('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"
    assert safe_filename("  . ") == "x"              # 清洗后为空 → 兜底名


class Test表格投影与写回:
    """单页工作台：表格 4 列是完整行 State 的投影，编辑要能写回。"""

    def test_投影_完整行变四列(self, events_work, sample_video):
        from bbh.app import project_rows
        _, rows = load_events_table(str(sample_video))
        proj = project_rows(rows)
        assert len(proj) == len(rows)
        assert len(proj[0]) == 4
        assert proj[0][0] == rows[0][0] and proj[0][1] == rows[0][1]
        # 档位列只显示三档色点（🟢 高 / 🟡 待 / ⚪ 低），不带证据分数字
        # ——数字与档位由不同逻辑决定，放一起徒增困惑（2026-08-19 用户反馈）
        assert proj[0][2] in ("🟢 高", "🟡 待", "⚪ 低")
        assert proj[0][3] == rows[0][6]

    def test_写回_表格改勾选与标签回写完整行(self, events_work, sample_video):
        from bbh.app import project_rows, sync_table_edits
        _, rows = load_events_table(str(sample_video))
        proj = project_rows(rows)
        proj[0][0] = 0
        proj[0][3] = "小王"
        synced = sync_table_edits(rows, proj)
        assert synced[0][0] == 0 and synced[0][6] == "小王"
        assert synced[0][1] == rows[0][1]      # 时间列不接受表格侧修改
        assert synced[0][4] == rows[0][4]      # 前秒由面板路径改，不在表格

    def test_写回_空与短表不炸(self):
        from bbh.app import sync_table_edits
        assert sync_table_edits([], []) == []
        assert sync_table_edits(None, None) == []
        rows = [[1, "00:02.5", "high", "0.90", 15, 5, ""]]
        assert sync_table_edits(rows, [[1, "00:02.5", "🟢 高", ""]])[0][0] == 1

    def test_写回_表格比State长按State截断(self):
        """防御性：表格行数不该多于 State，多出来的一律丢弃，不凭空造行。"""
        from bbh.app import sync_table_edits
        rows = [[1, "00:02.5", "high", "0.90", 15, 5, ""]]
        table = [[0, "00:02.5", "🟢 高", "小王"],
                 [1, "00:09.9", "🟢 高", "老李"]]
        synced = sync_table_edits(rows, table)
        assert len(synced) == 1                       # 按 State 长度截断
        assert synced[0][0] == 0 and synced[0][6] == "小王"  # 首行照常写回

    def test_投影_None_返回空列表(self):
        """防御性契约：rows 传 None 返回空列表而不是炸（生产路径都传列表）。"""
        from bbh.app import project_rows
        assert project_rows(None) == []


class Test当前面板:
    def test_面板_含行信息与悬浮卡(self, events_work, sample_video):
        from bbh.app import _cur_panel_html
        _, rows = load_events_table(str(sample_video))
        html = _cur_panel_html(rows, 0, "判定依据：测试文案")
        assert "第 1 条" in html and rows[0][1] in html
        assert "判定依据：测试文案" in html
        assert "ℹ" in html                       # 悬浮信息入口
        assert "onmouseover" in html             # 内联事件（gr.HTML 会执行）
        assert f"前 {rows[0][4]:.0f}s" in html and f"后 {rows[0][5]:.0f}s" in html
        assert str(rows[0][3]) in html           # 完整置信度在卡里
        assert "选中" in html and "切片" in html   # 卡片独有字段，钉住卡数据来自 7 列

    def test_面板_无当前行给提示(self):
        from bbh.app import _cur_panel_html
        assert "当前行：无" in _cur_panel_html(None, None)
        assert "当前行：无" in _cur_panel_html([[1] * 7], 99)

    def test_面板_reason为空不输出正文(self):
        from bbh.app import _cur_panel_html
        assert "font-size:12.5px" not in _cur_panel_html([[1] * 7], 0)  # reason 空不输出正文


class Test载入与探测:
    def test_载入_五元组含投影与统计(self, events_work, sample_video):
        from bbh.app import load_table_for_ui
        dur_md, proj, rows, cur, panel = load_table_for_ui(str(sample_video))
        assert "视频总长" in dur_md and "条" in dur_md
        assert len(proj) == len(rows) and len(proj[0]) == 4
        assert cur is None
        assert "当前行：无" in panel

    def test_载入_路径带引号也能载(self, events_work, sample_video):
        from bbh.app import load_table_for_ui
        out = load_table_for_ui(f'"{sample_video}"')
        assert len(out[2]) > 0

    def test_探测_已有结果直接载入(self, events_work, sample_video):
        from bbh.app import probe_existing
        dur_md, proj, rows, cur, panel, status = probe_existing(str(sample_video))
        assert "视频总长" in dur_md and len(rows) > 0
        assert "已载入已有检测结果" in status

    def test_探测_无结果给新视频提示并清空(self, tmp_path, monkeypatch):
        from bbh.app import probe_existing
        monkeypatch.chdir(tmp_path)
        dur_md, proj, rows, cur, panel, status = probe_existing(
            str(tmp_path / "不存在.mp4"))
        assert proj == [] and rows == []
        assert dur_md == ""            # Markdown 槽位必须给串，postprocess([]) 会炸
        # tmp_path 无 calibration.json → 检测前置条件不满足，提示先标定
        assert "还没标定" in status and "标定设置" in status

    def test_探测_已标定的新视频提示可检测(self, tmp_path, monkeypatch,
                                           sample_calibration):
        from bbh.app import probe_existing
        monkeypatch.chdir(tmp_path)
        # 标定随视频走：存进该视频自己的 work 目录才算「已标定」
        os.makedirs(os.path.join("work", "不存在"), exist_ok=True)
        sample_calibration.save(os.path.join("work", "不存在", "calibration.json"))
        out = probe_existing(str(tmp_path / "不存在.mp4"))
        assert "点「开始检测」" in out[5] and out[0] == ""

    def test_探测_空路径不炸(self, tmp_path, monkeypatch):
        from bbh.app import probe_existing
        monkeypatch.chdir(tmp_path)
        out = probe_existing("")
        assert "还没标定" in out[5] and out[0] == ""

    def test_探测_结果损坏给提示不炸(self, events_work, sample_video, tmp_path):
        from bbh.app import probe_existing
        p = os.path.join(app_module._work_dir_for(str(sample_video)),
                         "events.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("not json{")
        dur_md, proj, rows, cur, panel, status = probe_existing(str(sample_video))
        assert "损坏" in status and proj == [] and dur_md == ""


class Test点行预览新签名:
    def test_返回九元组_面板含依据与标签回显(self, events_work, sample_video):
        from bbh.app import preview_row_click
        _, rows = load_events_table(str(sample_video))
        rows[0][6] = "小王"
        out = preview_row_click(str(sample_video), rows, _row_evt(0))
        html, hl, cur, panel, tag, flag_btn, flag_md, cross_btn, cross_md = out
        assert "<video" in html and "/gradio_api/file=" in html
        assert isinstance(hl, dict) and hl.get("visible") is False  # 收起成品
        assert cur == 0
        assert "第 1 条" in panel and "判定依据" in panel and "置信度" in panel
        assert tag["value"] == "小王"   # 标签下拉回显该行现值（无标签行 = None）
        assert isinstance(tag.get("choices"), list)  # choices 顺手刷 union 款
        assert flag_btn.startswith("⚠") and flag_md == ""   # 未标球按钮态
        assert cross_btn.startswith("✚") and cross_md == ""  # 未标十字按钮态

    def test_回到逐条预览按钮(self):
        from bbh.app import _back_to_preview
        hl, html, back = _back_to_preview()
        assert isinstance(hl, dict) and hl.get("visible") is False
        assert isinstance(back, dict) and back.get("visible") is False
        assert "点表格任意一行" in html

    def test_预览默认2倍速(self, tmp_path):
        """2026-08-22：预览默认 2× 播（复核 240 条时省一半时间）；
        内联 onloadedmetadata 设 playbackRate——gr.HTML 里内联属性可执行。"""
        from bbh.app import _player_html
        fake = tmp_path / "p.mp4"
        fake.write_bytes(b"x")                       # _player_html 要 getmtime
        html = _player_html(str(fake), 15, 5)
        assert 'onloadedmetadata="this.playbackRate=2"' in html


class Test编辑路径:
    """2026-08-19 删「当前条前/后(秒)」单条配置：前后秒只有全局一套
    （apply_all_rows），单条重切路径 recut_current 随 UI 一起删除。"""

    def test_预览_None前后秒不炸(self, events_work, sample_video):
        from bbh.app import preview_row_click
        _, rows = load_events_table(str(sample_video))
        rows[0][4] = None
        html, *_ = preview_row_click(str(sample_video), rows, _row_evt(0))
        assert "<video" in html

    def test_全局应用_写回全部行并出投影(self, sample_video):
        from bbh.app import apply_all_rows
        rows = [[1, "00:10.0", "high", "0.90", 15, 5, ""],
                [0, "00:20.0", "review", "0.60", 15, 5, "x"]]
        rows2, proj, *_ = apply_all_rows(str(sample_video), rows, 8, 2)
        assert all(r[4] == 8 and r[5] == 2 for r in rows2)
        assert len(proj) == 2 and len(proj[0]) == 4

    def test_全局应用_同步刷新当前行面板(self, sample_video, tmp_path, monkeypatch):
        from bbh.app import apply_all_rows
        monkeypatch.chdir(tmp_path)      # cur_row 有效 ⇒ 面板要读 events.json
        rows = [[1, "00:10.0", "high", "0.90", 15, 5, ""]]
        rows2, proj, panel = apply_all_rows(str(sample_video), rows, 8, 2, 0)
        assert "前 8" in panel
        rows3, _, panel3 = apply_all_rows(str(sample_video), rows, 8, 2, None)
        assert isinstance(panel3, dict)              # 未选行不刷面板

    def test_全局应用_清空数字框按默认兜底(self, sample_video, tmp_path, monkeypatch):
        from bbh.app import apply_all_rows
        monkeypatch.chdir(tmp_path)      # 同上：cur_row 有效 ⇒ 读 work/<名>/
        rows2, *_ = apply_all_rows(
            str(sample_video), [[1, "00:10.0", "high", "0.90", 1, 1, ""]],
            None, None, 0)
        assert rows2[0][4] == 5 and rows2[0][5] == 2

    def test_表格编辑_落盘标签分配(self, events_work, sample_video):
        from bbh.app import on_table_edit, _load_assignments
        _, rows = load_events_table(str(sample_video))
        proj = [[1, rows[0][1], "高 0.90", "小王"]]
        synced = on_table_edit(str(sample_video), rows, proj)
        assert synced[0][6] == "小王"
        assert _load_assignments(str(sample_video)).get(rows[0][1]) == "小王"


class Test打标与生成切换:
    def test_打标_面板更新且行数据带标签(self, events_work, sample_video,
                                      tmp_path, monkeypatch):
        from bbh.app import set_row_tag
        monkeypatch.setattr(app_module, "TAGS_FILE",
                            str(tmp_path / "tags.json"))
        _, rows = load_events_table(str(sample_video))
        orig = rows
        _, rows2, panel = set_row_tag(str(sample_video), rows, 0, "小王")
        assert rows2[0][6] == "小王"
        assert "第 1 条" in panel and "小王" in panel and "ℹ" in panel
        assert orig[0][6] == ""                    # 原 State 不被就地污染

    def test_打标后面板仍含判定依据(self, events_work, sample_video, tmp_path,
                                 monkeypatch):
        """复核主循环「点行看依据→选标签」：打标重建面板不能把依据抹掉
        （否则想再看必须重点行、重跑数秒 ffmpeg 切片）。"""
        from bbh.app import set_row_tag
        monkeypatch.setattr(app_module, "TAGS_FILE",
                            str(tmp_path / "tags.json"))
        _, rows = load_events_table(str(sample_video))
        _, rows2, panel = set_row_tag(str(sample_video), rows, 0, "小王")
        assert "判定依据" in panel and "置信度" in panel

    def test_生成_合集单选返回成品与占位切换(self, events_work, sample_video,
                                           monkeypatch):
        from bbh.app import GEN_ALL_LABEL, gen_selected
        _, rows = load_events_table(str(sample_video))
        rows[0][0] = 1                               # 默认全不勾，手动勾上
        steps = list(gen_selected(
            str(sample_video), rows, [GEN_ALL_LABEL], vertical=False))
        first, html, back, dlg, report, _btn = steps[-1]
        assert os.path.basename(first) == "highlight-横.mp4"
        assert "已生成" in html and "已生成 1 个视频" in report
        assert isinstance(back, dict) and back.get("visible") is True


class Test自动载入:
    def _mark_done(self, app_state, video):
        app_module._state.update(running=False, done=True, error=None,
                                 autoloaded=False,
                                 events_path=os.path.join(
                                     app_module._work_dir_for(video), "events.json"))

    def test_检测中_面板提示且表格锁定(self, app_state):
        from bbh.app import refresh_and_autoload
        app_module._state.update(running=True, done=False, done_frames=5,
                                 total_frames=10, stage=None)
        out = refresh_and_autoload("whatever.mp4")
        assert len(out) == 6
        assert "检测中" in out[0] and "(50%)" in out[0]    # 百分比进文案
        assert isinstance(out[2], dict) and out[2].get("interactive") is False
        assert "完成后自动载入" in out[5]

    def test_完成_自动载入一次(self, app_state, events_work, sample_video):
        from bbh.app import refresh_and_autoload
        self._mark_done(app_state, str(sample_video))
        out = refresh_and_autoload(str(sample_video))
        assert "视频总长" in out[1] and len(out[3]) > 0    # 载入成功
        assert out[2].get("interactive") is True           # 恢复表格交互
        assert app_module._state["autoloaded"] is True

    def test_已载入后_不再重复载入(self, app_state, events_work, sample_video):
        from bbh.app import refresh_and_autoload
        self._mark_done(app_state, str(sample_video))
        refresh_and_autoload(str(sample_video))
        out2 = refresh_and_autoload(str(sample_video))
        assert isinstance(out2[2], dict) and "value" not in out2[2]  # 无操作
        # 交互恢复只在首个 tick 发一次；此后空 update（反复发属性更新会闪）
        assert "interactive" not in out2[3]

    def test_载入失败_一次性_不再重试(self, app_state, sample_video, tmp_path,
                                  monkeypatch):
        from bbh.app import refresh_and_autoload
        monkeypatch.chdir(tmp_path)                  # tick 要探 work/<视频名>/
        self._mark_done(app_state, str(sample_video))

        def _boom(vp, offset=0.0):
            raise OSError("disk error")
        monkeypatch.setattr(app_module, "load_table_for_ui", _boom)
        out = refresh_and_autoload(str(sample_video))
        assert "载入失败" in out[5]
        assert app_module._state["autoloaded"] is True      # 失败也算已尝试
        out2 = refresh_and_autoload(str(sample_video))
        assert "value" not in out2[2]                       # 不重试

    def test_空闲_全无操作(self, app_state):
        from bbh.app import refresh_and_autoload
        app_module._state.update(running=False, done=False, error=None)
        out = refresh_and_autoload("x.mp4")
        assert all(isinstance(x, dict) for x in out[1:])
        assert "value" not in out[0]            # 空闲 tick 不写 status_md
        assert not out[2].get("interactive")    # 从未锁过 → 不发属性更新

    def test_锁定后首个tick恢复表格_之后空update(self, app_state):
        """检测锁表 → 解锁后的第一个 tick 恢复 interactive=True 一次；
        再往后若每 tick 重复发属性更新，Dataframe 会周期性重渲染（闪烁）。"""
        from bbh.app import refresh_and_autoload
        app_module._state.update(running=True, done=False, done_frames=1,
                                 total_frames=2, stage=None)
        refresh_and_autoload("x.mp4")           # 检测中：锁表
        app_module._state.update(running=False)
        out1 = refresh_and_autoload("x.mp4")    # 解锁后首个 tick
        assert out1[2].get("interactive") is True
        out2 = refresh_and_autoload("x.mp4")    # 之后必须零属性更新
        assert "interactive" not in out2[2] and "value" not in out2[2]

    def test_空闲tick不覆盖探测提示(self, app_state, events_work, sample_video):
        """探测提示（已载入/文件损坏）写入后，空闲 Timer 不能 2 秒内顶掉它。"""
        from bbh.app import probe_existing, refresh_and_autoload
        probe_existing(str(sample_video))       # 真实界面里此刻写入探测提示
        app_module._state.update(running=False, done=False, error=None)
        out = refresh_and_autoload(str(sample_video))
        assert "value" not in out[0]            # status_md 槽零属性更新

    def test_出错tick仍写实值(self, app_state):
        """空闲分支改 gr.update() 后，错误文案必须仍有分支写实值上屏。"""
        from bbh.app import refresh_and_autoload
        app_module._state.update(running=False, done=False, error="boom",
                                 last_err_md=None)
        out = refresh_and_autoload("x.mp4")
        assert "出错" in out[0]

    def test_出错tick重复同文案不重写(self, app_state):
        """错误持续期间每 2s 重写同一文案 = Markdown 周期性重渲染（闪烁）；
        只在文案变化时写实值。"""
        from bbh.app import refresh_and_autoload
        app_module._state.update(running=False, done=False, error="boom",
                                 last_err_md=None)
        refresh_and_autoload("x.mp4")
        out2 = refresh_and_autoload("x.mp4")
        assert "value" not in out2[0] and "value" not in out2[1]  # 同文案 → 不重写
        app_module._state.update(error="another")   # 新错误必须重新上屏
        out3 = refresh_and_autoload("x.mp4")
        assert "another" in out3[0]

    def test_取消后tick写文案一次且解锁表格(self, app_state):
        """取消结束检测（running→False）后：首个 tick 写取消文案并恢复表格
        交互；之后同文案不重写（防闪烁，与错误分支同型）。"""
        from bbh.app import refresh_and_autoload
        app_module._state.update(running=True, done=False, done_frames=1,
                                 total_frames=2, stage=None)
        refresh_and_autoload("x.mp4")               # 检测中：锁表
        app_module._state.update(running=False, done=False, cancelled=True,
                                 last_cancel_md=None)
        out1 = refresh_and_autoload("x.mp4")
        assert "已取消" in out1[0]
        assert out1[2].get("interactive") is True   # 解锁后的首个 tick 恢复一次
        out2 = refresh_and_autoload("x.mp4")
        assert "value" not in out2[0] and "interactive" not in out2[2]


class Test人工裁决漏检反馈:
    """🧪 临时反馈功能（2026-08-19 改裁决制）：✅ 是进球 / ❌ 未进球 按钮
    裁决任意行（低置信档真进球可翻案，快照判定依据含 low_reason）；漏检
    补记，落 work/<视频名>/feedback.json。整个分区调优结束后可随 UI 删除。"""

    def test_裁决与取消_快照判定依据(self, events_work, sample_video):
        from bbh.app import _load_feedback, mark_verdict
        _, rows = load_events_table(str(sample_video))
        out = mark_verdict(str(sample_video), rows, 0, "not_goal")
        assert "未进球" in out and "已标记" in out
        fb = _load_feedback(str(sample_video))
        assert len(fb["verdicts"]) == 1
        m = fb["verdicts"][0]
        assert m["t_str"] == rows[0][1] and m["verdict"] == "not_goal"
        assert "confidence" in m["evidence"]      # 快照了判定依据防重跑丢失
        out2 = mark_verdict(str(sample_video), rows, 0, "not_goal")
        assert "已取消" in out2
        assert _load_feedback(str(sample_video))["verdicts"] == []

    def test_改判覆盖而非叠加(self, events_work, sample_video):
        """点另一个按钮 = 改判：同一行始终只有一条裁决。"""
        from bbh.app import _load_feedback, mark_verdict
        _, rows = load_events_table(str(sample_video))
        mark_verdict(str(sample_video), rows, 0, "not_goal")
        out = mark_verdict(str(sample_video), rows, 0, "goal")
        assert "改判" in out
        fb = _load_feedback(str(sample_video))
        assert [v["verdict"] for v in fb["verdicts"]] == ["goal"]

    def test_低置信行可标记翻案_快照降级原因(self, events_work, sample_video):
        """裁决制核心用例：低置信行的「是进球」裁决带 low_reason 快照——
        调优时据此定位被门槛误拦的真进球。"""
        from bbh.app import _load_feedback, load_events_table, mark_verdict
        from bbh.models import GoalEvent, load_events, save_events
        p = str(events_work / "events.json")
        data = load_events(p)
        data["events"].append(GoalEvent(t=999.0, confidence=0.46,
                                        status="low", low_reason="证据点太少"))
        save_events(p, video=data["video"], duration=data["duration"],
                    events=data["events"], warning=None,
                    calib_key=data["calib_key"], logic_ver=data["logic_ver"])
        _, rows = load_events_table(str(sample_video))
        out = mark_verdict(str(sample_video), rows, len(rows) - 1, "goal")
        assert "是进球" in out
        m = _load_feedback(str(sample_video))["verdicts"][0]
        assert m["verdict"] == "goal"
        assert m["evidence"]["low_reason"] == "证据点太少"
        assert m["evidence"]["status"] == "low"

    def test_无当前行或越界报错(self, events_work, sample_video):
        from bbh.app import mark_verdict
        _, rows = load_events_table(str(sample_video))
        with pytest.raises(gr.Error):
            mark_verdict(str(sample_video), rows, None, "goal")
        with pytest.raises(gr.Error):
            mark_verdict(str(sample_video), rows, 99, "goal")

    def test_漏检_两种时刻格式与清空输入框(self, events_work, sample_video):
        from bbh.app import _load_feedback, record_miss
        status, box = record_miss(str(sample_video), "12:34")
        assert "漏检" in status and box == ""       # 记录后清空输入框
        status, _ = record_miss(str(sample_video), "756.5")
        misses = _load_feedback(str(sample_video))["misses"]
        assert [m["t"] for m in misses] == [754.0, 756.5]
        with pytest.raises(gr.Error):
            record_miss(str(sample_video), "等一下")

    def test_反馈文件损坏当作空且可继续写(self, events_work, sample_video):
        from bbh.app import _load_feedback, mark_verdict
        (events_work / "feedback.json").write_text("{oops", encoding="utf-8")
        assert _load_feedback(str(sample_video)) == {
            "verdicts": [], "misses": []}
        _, rows = load_events_table(str(sample_video))
        assert "已标记" in mark_verdict(str(sample_video), rows, 0, "goal")

    def test_旧误判格式自动迁移为未进球裁决(self, events_work, sample_video):
        """2026-08-19 前的 false_positives 标记读入即迁移，历史标注不丢。"""
        import json as _json
        from bbh.app import _load_feedback
        (events_work / "feedback.json").write_text(
            _json.dumps({"false_positives": [{"t": 10.0, "t_str": "00:10.0"}],
                         "misses": []}, ensure_ascii=False), encoding="utf-8")
        fb = _load_feedback(str(sample_video))
        assert fb["verdicts"][0]["verdict"] == "not_goal"
        assert fb["misses"] == []


def test_build_calibration宽色域提示(tmp_path, monkeypatch):
    """S 下限<60（远景小球点到低饱模糊像素）→ 成功提示里追加知情提示；
    正常高饱球不提示（09-04_1 段1 S=37 反面教材：出手人 89/119 定位失效，
    回扫已内置 S≥88 地板自动兜底——提示只告知不阻断）。"""
    monkeypatch.chdir(tmp_path)
    vid = str(tmp_path / "v.mp4")

    def frame_with(ball_rgb):
        frame = np.full((360, 640, 3), (50, 44, 40), np.uint8)
        cv2.line(frame, (300, 154), (380, 154), (180, 180, 180), 3)
        cv2.circle(frame, (340, 220), 8, ball_rgb, -1)
        return frame

    pts = [(300.0, 154.0), (380.0, 154.0), (340.0, 220.0)]
    # 低饱橙 RGB(245,210,197)：S≈50 <60——命中成功但色域宽
    _, msg, s0, _ = build_calibration(pts, frame_with((245, 210, 197)), vid)
    assert "饱和度下限偏低" in msg and "S=10" in msg   # 50-余量40 → S_lo=10
    assert s0 < 60
    # 正常球 RGB(245,100,20)：S≈234，不提示
    _, msg, s0, _ = build_calibration(pts, frame_with((245, 100, 20)), vid)
    assert "饱和度下限偏低" not in msg and s0 >= 60


def test_reason_text_B2注入行_网分按0且不误报网静止():
    """审阅修复（Important 1 同源）：B2 注入行（该行存在的前提=网动簇触发）
    依据文本不得再挂「⚠️ 篮网未摆动（网分 −0.30）」——实测窗错过摆动相位
    是注入时刻内插的已知形态，算式网分项按 0（spec §3.5）。"""
    import bbh.app as A
    from bbh.models import GoalEvent
    e = GoalEvent(t=10.0, confidence=0.50, status="review", cross_off=0.0,
                  above_n=0, above_span=0.0, below_n=5, below_span=0.5,
                  review_reason="b2",
                  net_post=2.0, net_ctrl=2.2, net_local=True)
    s = A._reason_text(e)
    assert "＋ 网 0.00" in s                        # 算式与 conf 对账
    assert "未摆动" not in s and "网分 −0.30" not in s
    assert "注入行网分按 0" in s


def test_reason_text_geom硬门行_必须说清压筐沿强制复核():
    """2026-10-14 用户报「10:24.5 置信度 68% 却是待」：geom 硬门（|cross_off|
    >0.30 筐宽）把 conf≥0.60 的行压在待复核，但依据文本对此只字不提——
    用户盯着「68% ≥ 0.60 该绿」与档位口径文案找矛盾。硬门拦截必须像
    entity/pop_back 一样在依据里说得出原因。字段取自 2025-06-28_1 实测行。"""
    import bbh.app as A
    from bbh.models import GoalEvent
    e = GoalEvent(t=624.5, confidence=0.68, status="review", cross_off=-0.304,
                  above_n=2, above_span=0.033, below_n=5, below_span=0.133,
                  descent_px=11.2, below_big=4, review_reason="geom",
                  net_post=9.39, net_ctrl=1.87, net_local=True)
    s = A._reason_text(e)
    assert "压筐沿" in s                    # 说得出具体是哪道门
    assert "强制" in s                      # 说得出档位为何不随置信度走
    # below_big=4：entity 门没开，逐行解释不得出现（口径清单里的门名不算）
    assert "穿筐后球体未再现" not in s


def test_reason_text_档位口径文案_必须含硬门恒待规则():
    """档位口径（依据卡 ⓘ 与依据文本尾部各一份）只写了「≥0.60 高置信」，
    没写硬门拦截恒为待确认——正是「68% 仍待」被当成 bug 的直接原因。"""
    import bbh.app as A
    from bbh.models import GoalEvent
    e = GoalEvent(t=10.0, confidence=0.70, status="high", cross_off=0.1,
                  above_n=3, above_span=0.3, below_n=4, below_span=0.4,
                  descent_px=15.0, below_big=3)
    s = A._reason_text(e)
    assert "硬门" in s and "待确认" in s    # 口径补全三层规则


def test_reason_text_fallback兜底行_必须说清恒待复核():
    """兜底注入行（伪点兜底路径新发事件）v18 起挂 review_reason="fallback"
    硬门恒待，依据文本同样没解释——与 geom 同一批「待得没道理」的行。"""
    import bbh.app as A
    from bbh.models import GoalEvent
    e = GoalEvent(t=10.0, confidence=0.72, status="review", cross_off=0.12,
                  above_n=3, above_span=0.3, below_n=4, below_span=0.4,
                  descent_px=15.0, below_big=3, review_reason="fallback")
    s = A._reason_text(e)
    assert "兜底" in s and "恒" in s        # 兜底注入、恒待复核
