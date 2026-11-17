"""Gradio 工作台：剪辑台（检测 → 复核 → 出片）+ ⚙ 标定设置。

标定设置页点击采集说明：gradio 5.50 的 Sketchpad（ImageEditor 子类）会把所有笔画
提交到同一活动图层（commit_stroke 不会新增图层），无法从值里按笔画还原点击顺序；
而 gr.Image 的 .select 事件在点击时给出精确的原图像素坐标 (x, y)，且事件顺序即
点击顺序，故参考帧用 gr.Image 展示并通过 .select + gr.State 累积点击点。
"""
import glob
import html
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
from datetime import datetime

import cv2
import numpy as np

from . import _gr_compat as gr    # 打包版不装 gradio，见 bbh/_gr_compat.py
from . import appearance, calibration, detector_switch, editor, pipeline, repack, shooter, suggest, vrender
from . import manual_groups as manual   # plan 契约名 manual.load/assign/unassign
from .editor import _ffmpeg_exe
from .goal_decider import HIGH_CONF, MIN_CONF, _pos_score, _side_score
from .models import (CalibSegment, Calibration, load_events, ref_image_path,
                     segments_key, time_key)
from .motion import save_ref_image
from .net_motion import (NET_SCORE_GLOBAL, NET_SCORE_STATIC, NET_STATIC_TH,
                         net_term)

CALIB_FILE = "calibration.json"          # 文件名（存于各视频 work 目录内——
                                         # 标定随视频走，换视频不串台）
REF_IMG_FILE = "calibration_ref.png"     # 标定参考帧（用户点筐的那帧）存档
TAGS_FILE = "tags.json"                # 全局标签库（跨视频共用：每周同一批人）
ASSIGN_FILE = "tag_assignments.json"   # 每个视频的 进球时间→标签 分配表
SELECTION_FILE = "selection.json"      # 每个视频的 勾选行时间串（默认全不勾）
STARRED_FILE = "starred.json"          # 每个视频的 ★ 精选行时间串（单条发片用，
                                       # 2026-10-23；与勾选同口径存原始检测时刻）
DELETED_FILE = "deleted.json"          # 每个视频的 已删行时间串（非破坏，可恢复）
THUMB_FLAG_FILE = "thumb_flags.json"   # 每个视频的 缩略图有误球时间串（分析样本）
CROSS_FLAG_FILE = "cross_flags.json"   # 每个视频的 十字有误球时间串（分析样本，
                                       # 与上分采：没露人=裁剪/画面，十字错=定位）
DEFAULT_BEFORE_S = 5.0                 # 进球前切片秒数（默认；可全局/单条改）
DEFAULT_AFTER_S = 2.0                  # 进球后切片秒数


_QUOTE_OPENS = ('"', "'", "“")   # 成对的起始引号（ASCII 引号左右同形）
_QUOTE_CLOSES = ('"', "'", "”")  # 成对的结束引号（全角引号左右异形！）


def clean_path(p: str) -> str:
    """清洗用户粘贴的路径：去空白与成对引号（资源管理器「复制文件地址」
    会带英文引号，中文输入法可能带全角引号），否则引号被当成文件名的一部分。"""
    p = (p or "").strip()
    while len(p) >= 2 and p[0] in _QUOTE_OPENS and p[-1] in _QUOTE_CLOSES:
        p = p[1:-1].strip()
    return p


# ---------- ⚙ 标定设置 ----------

def click_hint(pts) -> str:
    """根据已记录的点击数提示下一步。"""
    n = len(pts or [])
    if n == 0:
        return "在参考帧上依次点击：**筐口左端 → 筐口右端 → 球（1-3 次）**"
    if n == 1:
        return "已点 1 点（筐口左端 ✓），接着点 **筐口右端**"
    if n == 2:
        return "已点 2 点（筐口两端 ✓），接着点 **球（1-3 次）**"
    return f"已点 {n} 点（筐口两端 + 球 {n - 2} 次）。点击「生成标定」，或继续点球补色"


def extract_frame(video_path: str):
    """取视频中段一帧给用户标注；换视频即清空点击点与上次的预览/提示。
    OpenCV 解不了的编码（部分手机 HDR/AV1 等）退回 ffmpeg 抽帧；
    仍失败则报带诊断信息的错（路径/大小/ffmpeg 输出），便于定位。"""
    video_path = clean_path(video_path)
    if not video_path:
        return None, [], click_hint([]), None, None
    if not os.path.exists(video_path):
        raise gr.Error(f"视频不存在：{video_path}")
    frame = _grab_middle_frame(video_path)
    if frame is None:
        raise gr.Error(_open_fail_msg(video_path))
    hint = click_hint([])
    dur = editor.probe_duration(video_path)
    if dur and dur > 0:
        hint += (f"\n\n视频总长 {int(dur // 60)} 分 {dur % 60:.0f} 秒。"
                 "点击的球需在黄框（检测区域）内才能验证识别；这一帧球不在"
                 "框里的话，用下方「时间点」跳到球在篮下的画面再点击。")
    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), [], hint, None, None


def extract_frame_at(video_path: str, t):
    """跳到指定秒抽帧（换参考画面用）。优先 ffmpeg——编码兼容面广且按时长精确。"""
    src = clean_path(video_path)
    if not src or not os.path.exists(src):
        raise gr.Error("请先上传视频或填好路径")
    try:
        t = max(0.0, float(t or 0))
    except (TypeError, ValueError):
        raise gr.Error("时间点需为数字（秒）")
    frame = editor.grab_frame_ffmpeg(src, t)
    if frame is None:
        cap = cv2.VideoCapture(src)
        try:
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
                ok, frame = cap.read()
                if not ok:
                    frame = None
        finally:
            cap.release()
    if frame is None:
        raise gr.Error(f"在 {t:.0f} 秒处取帧失败。" + _open_fail_msg(src))
    return cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), [], click_hint([]), None, None


def _extract_for_ui(video_path: str, t=None):
    """标定页抽帧统一入口（换视频/跳时刻都走它）：除了帧与点击提示，
    还把当前视频路径写进 video_src State（后续生成标定/微调/删段都靠它
    定位 work 目录），并把段列表/删段下拉刷成**该视频**的标定——换视频
    面板即换一套（标定随视频走）。对应 [frame_img, pts_state, click_msg,
    preview_img, calib_msg, video_src, seg_md, seg_dd]。"""
    if t is None:
        frame, pts, hint, preview, msg = extract_frame(video_path)
    else:
        frame, pts, hint, preview, msg = extract_frame_at(video_path, t)
    vp = clean_path(video_path)
    return (frame, pts, hint, preview, msg, vp,
            _seg_list_md(vp), gr.update(choices=_seg_choices(vp), value=None))


def _grab_middle_frame(video_path: str):
    """OpenCV 优先取中段帧，打不开/读不出时用 ffmpeg 按时长取中点。"""
    cap = cv2.VideoCapture(video_path)
    try:
        if cap.isOpened():
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = cap.get(cv2.CAP_PROP_FPS)
            if total > 0 and fps > 0:
                cap.set(cv2.CAP_PROP_POS_FRAMES, total // 2)
                ok, frame = cap.read()
                if ok and frame is not None:
                    return frame
    finally:
        cap.release()
    dur = editor.probe_duration(video_path)
    if dur and dur > 0:
        return editor.grab_frame_ffmpeg(video_path, dur / 2)
    return None


def _open_fail_msg(video_path: str) -> str:
    """打开失败时的诊断信息：存在性/大小 + ffmpeg 探测输出尾部。"""
    exists = os.path.exists(video_path)
    size = os.path.getsize(video_path) if exists else 0
    try:
        r = subprocess.run([editor._ffmpeg_exe(), "-hide_banner", "-i", video_path],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        tail = (r.stderr or "").strip().splitlines()[-5:] or ["（无输出）"]
    except (subprocess.TimeoutExpired, OSError) as e:
        tail = [f"ffmpeg 探测失败: {e}"]
    hint = ""
    if any("Permission denied" in l or "拒绝访问" in l for l in tail):
        hint = ("\n提示：文件被其他程序占用（上传未收尾/微信正在传输/杀毒扫描）。"
                "长视频建议改用下方「填本机路径」，不走上传。")
    return (f"无法打开视频：{video_path}\n"
            f"存在={exists}，大小={size} 字节\nffmpeg 探测：\n" + "\n".join(tail) + hint)


def add_point(pts, evt: gr.SelectData):
    """gr.Image 点击 → 追加坐标。约定顺序：筐口左端、筐口右端、球（1-3 次）。"""
    x, y = evt.index
    pts = list(pts or []) + [(float(x), float(y))]
    return pts, click_hint(pts)


def _calib_from_pts(pts, frame_rgb):
    """点击 → (Calibration, 标注帧 BGR, HSV 下界)。主标定/新段标定共用体。
    点击约定：第 1、2 点 = 筐口左端、右端；第 3 点起 = 球（1-3 次）。"""
    pts = list(pts or [])
    if len(pts) < 3:
        raise gr.Error("请至少点击 3 次：筐口左端、筐口右端、球")
    if frame_rgb is None:
        raise gr.Error("请先上传参考视频，待自动抽帧后再点击")
    frame = np.asarray(frame_rgb)
    if frame.ndim != 3 or frame.shape[2] != 3:
        raise gr.Error("标注帧数据异常，请重新上传视频")
    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    try:
        rect = calibration.hoop_rect_from_rim_points(tuple(pts[0]), tuple(pts[1]))
        low, high = calibration.hsv_range_from_clicks(
            frame_bgr, clicks=[(round(x), round(y)) for x, y in pts[2:]])
    except ValueError as e:
        raise gr.Error(str(e))
    calib = Calibration(hoop_rect=rect, hsv_low=low, hsv_high=high,
                        frame_size=(frame.shape[1], frame.shape[0]))
    return calib, frame_bgr, low


def _wide_hsv_note(calib, for_yolo: bool = False) -> str:
    """宽色域标定知情提示（不阻断）。S 下限 <60 = 远景小球点到了运动
    模糊/混背景的低饱像素（09-04_1 段1 实测 S=37：顶棚暖反光全过颜色
    范围，出手人 89/119 球定位失效）。检测照常——收紧标定实测会丢真
    进球；出手人回扫已内置 S≥88 地板自动滤掉此类反光，故只提示。

    for_yolo=True（R-013 之后 YOLO 模式的标定页）：颜色范围这时只剩两个
    消费者——出手人溯源回扫与竖版跟球运镜，**YOLO 找球完全不读它**。
    旧文案结尾「检测不受影响」会被读成「YOLO 不受影响」，故按消费者改写。"""
    s_lo = calib.hsv_low[1]
    if s_lo >= 60:
        return ""
    if for_yolo:
        return (f"⚠ 注意：颜色范围饱和度下限偏低（S={s_lo}，远景小球常见）——"
                "顶棚/地板的低饱反光会混进颜色范围，会让出手人定位和竖版"
                "跟球跑偏；这只影响溯源与运镜，**与 YOLO 找球无关**。"
                "出手人回扫自带 S≥88 地板，可挡掉大部分此类反光，故仅提示。")
    return (f"⚠ 注意：颜色范围饱和度下限偏低（S={s_lo}，远景小球常见）——"
            "顶棚/地板的低饱反光会混入颜色范围；出手人回扫内置 S≥88 地板"
            "自动滤掉此类反光，检测不受影响。")


def build_calibration(pts, frame_rgb, video_path):
    """主标定（段 1，管片头 0:00 起，覆盖写本视频 work 目录的
    calibration.json——标定随视频走，换视频各存各的）。
    返回 (预览, 提示, S下限, V下限)——后两个用于把 HSV 微调滑块对齐到生成值。"""
    calib, frame_bgr, low = _calib_from_pts(pts, frame_rgb)
    d = _calib_dir(video_path)
    if not d:
        raise gr.Error("请先上传视频或填本机路径抽帧，再生成标定")
    # 参考帧（用户点筐的这帧）随标定存档：镜头位移校正的配准基准——
    # 标定几何只在这帧的坐标系里成立，检测期间周期性与它配准，把检出
    # 坐标折算回这套坐标系（固定机位中途被碰/漂移自动消化）。
    # 落盘位置=标定文件同目录（视频 work 目录）；存档名保持相对，
    # ref_image_path 按标定文件目录解析回同一路径。
    os.makedirs(d, exist_ok=True)
    ref_path = os.path.join(d, REF_IMG_FILE)
    calib.ref_hash = save_ref_image(frame_bgr, ref_path)
    calib.ref_image = REF_IMG_FILE
    calib.save(os.path.join(d, CALIB_FILE))
    if detector_switch.NAME == "yolo":
        # R-013：YOLO 模式下验证预览画**真检测器**的检出——旧预览画 HSV
        # 命中，与实际干活的 YOLO 无关（预览好坏不代表 YOLO 能否检出），
        # 会误导。现在预览/文案与剪辑台同帧同参，所见即所得。
        preview, n_ball = calibration.yolo_overlay(frame_bgr, calib)
        msg = ("标定已保存（随视频存 work/<视频名>/，参考帧已存档，"
               "用于镜头位移校正）。绿框=YOLO 检出的球（与剪辑台同一检测器，"
               f"本帧检出 {n_ball} 个），蓝框=筐口，红线=筐口平面。"
               "球检测由 YOLO 自动完成；颜色范围仅供出手人溯源回扫使用。"
               + _wide_hsv_note(calib, for_yolo=True))
    else:
        preview = calibration.overlay_preview(frame_bgr, calib)
        n = calibration.count_roi_hits(frame_bgr, calib)
        if n:
            msg = ("标定已保存（随视频存 work/<视频名>/，参考帧已存档，"
                   "用于镜头位移校正）。绿=球（仅黄框=检测区域内），"
                   f"蓝框=筐口，红线=筐口平面。ROI 内命中 {n} 像素。"
                   "若肤色也被标绿，用下方滑块逐步调大 S 下限。"
                   + _wide_hsv_note(calib))
        else:
            msg = (f"标定已保存，但 ROI 内 0 命中。{calibration.hsv_zero_diagnosis(frame_bgr, calib)} "
                   "注意：检测只看黄框（篮下区域）内，点击的球也需在黄框内；"
                   "可用「时间点」跳到球在篮下的画面重新点击生成。")
    return cv2.cvtColor(preview, cv2.COLOR_BGR2RGB), msg, low[1], low[2]


def build_segment_calibration(pts, frame_rgb, seg_t, video_path):
    """镜头移动后补一段标定（A2）：t0=「时间点」控件当前值（用户跳到的
    移动后稳定画面）。存本视频 work 目录的 calibration_segN.json——不动
    主标定（它管片头段）。对应 [preview_img, calib_msg, seg_md, seg_dd]。"""
    d = _calib_dir(video_path)
    if not d:
        raise gr.Error("请先上传视频或填本机路径抽帧，再补段标定")
    t = float(seg_t or 0)
    if t <= 0:
        raise gr.Error("新段起点需 >0：先用「时间点」跳到镜头稳定后的画面再点"
                       "击生成（0:00 起的片头段由主标定负责，直接「生成标定」）")
    segs = load_segments(video_path)
    if not segs:
        raise gr.Error("请先「生成标定」做主标定（管片头段），再补移动后的段")
    last_t0 = max(s.t0 for s in segs)
    if t <= last_t0:
        raise gr.Error(f"新段起点须晚于现有最后一段（{_fmt_mmss(last_t0)} 起）")
    calib, frame_bgr, _low = _calib_from_pts(pts, frame_rgb)
    os.makedirs(d, exist_ok=True)
    n = 2
    while os.path.exists(os.path.join(d, SEG_CALIB_FMT.format(n))):
        n += 1
    calib.calib_t = round(t, 2)
    calib.ref_hash = save_ref_image(frame_bgr, os.path.join(d, SEG_REF_FMT.format(n)))
    calib.ref_image = SEG_REF_FMT.format(n)
    calib.save(os.path.join(d, SEG_CALIB_FMT.format(n)))
    fname = SEG_CALIB_FMT.format(n)
    if detector_switch.NAME == "yolo":
        # R-013：与 build_calibration 同理——预览画真检测器的检出
        preview, n_ball = calibration.yolo_overlay(frame_bgr, calib)
        msg = (f"✅ 新段已存 {fname}（{_fmt_mmss(t)} 起）。绿框=YOLO 检出的球"
               f"（与剪辑台同一检测器，本帧检出 {n_ball} 个）。"
               "点「▶ 开始检测」：该时刻起用这段几何，之前的时段仍用主标定，"
               "只重扫新段（其余段缓存照用）。"
               + _wide_hsv_note(calib, for_yolo=True))
    else:
        preview = calibration.overlay_preview(frame_bgr, calib)
        hits = calibration.count_roi_hits(frame_bgr, calib)
        if hits:
            msg = (f"✅ 新段已存 {fname}（{_fmt_mmss(t)} 起），ROI 内命中 {hits} 像素。"
                   "点「▶ 开始检测」：该时刻起用这段几何，之前的时段仍用主标定，"
                   "只重扫新段（其余段缓存照用）。" + _wide_hsv_note(calib))
        else:
            msg = (f"新段已存 {fname}，但 ROI 内 0 命中。"
                   f"{calibration.hsv_zero_diagnosis(frame_bgr, calib)} "
                   "可重新点击球的位置再生成（覆盖下一个序号前先删旧段）。")
    return (cv2.cvtColor(preview, cv2.COLOR_BGR2RGB), msg,
            _seg_list_md(video_path),
            gr.update(choices=_seg_choices(video_path), value=None))


def refine_hsv(frame_rgb, s_low, v_low, video_path):
    """HSV 微调：只改下限的 S/V 分量并保存（本视频的标定档），用当前
    参考帧重算预览。肤色与篮球橙色相相近但饱和度低得多——人脸/胳膊/腿
    被标绿时，调大 S 下限即可滤掉，球（高饱和）保持绿色。"""
    calib_p = _calib_file(video_path)
    if not calib_p or not os.path.exists(calib_p):
        raise gr.Error("请先点击「生成标定」，再微调")
    if frame_rgb is None:
        raise gr.Error("参考帧缺失，请重新上传视频")
    calib = Calibration.load(calib_p)
    calib.hsv_low = (calib.hsv_low[0], int(s_low), int(v_low))
    calib.save(calib_p)
    frame_bgr = cv2.cvtColor(np.asarray(frame_rgb), cv2.COLOR_RGB2BGR)
    preview = calibration.overlay_preview(frame_bgr, calib)
    n = calibration.count_roi_hits(frame_bgr, calib)
    if n == 0:
        msg = (f"⚠️ S={int(s_low)}、V={int(v_low)} 时 ROI 内 0 命中。"
               f"{calibration.hsv_zero_diagnosis(frame_bgr, calib)} "
               "也可直接重新点「生成标定」恢复到最初能识别球的范围。")
    else:
        msg = (f"已保存：S 下限 {int(s_low)}、V 下限 {int(v_low)}，"
               f"ROI 内命中 {n} 像素。肤色不绿、球仍绿即合适。")
    return cv2.cvtColor(preview, cv2.COLOR_BGR2RGB), msg


# ---------- 检测与状态（剪辑台左栏的检测流水线） ----------

# 检测在后台线程跑，界面靠 Timer 轮询本状态刷新进度
# done_msg：完成时一次性算好的汇总文案，Timer 完成分支 O(1) 返回（M1）
# stage：转码等无帧进度的阶段性提示（stage_cb 回写），转码完置回 None
# autoloaded：完成后结果表是否已自动载入过（只载一次，防覆盖复核中的编辑）
# table_locked/last_err_md：防"剪辑台闪烁"——Timer 每 2s 一 tick，属性/值更新
# （哪怕同值）都会让组件重渲染。表格 interactive 只在解锁后的首个 tick 恢复
# 一次；错误文案只在变化时重写，空闲 tick 全部空 update。
# _fps：进度回调的帧率估计（指数滑动平均，ETA 用）；_last_cb：上次回调的
# (帧号, 时刻)，算瞬时帧率用；device_hint：这轮实际拿什么在算（CPU/CUDA +
# imgsz + 步长），2026-11-17 起写进状态行——远端排查只看截图就能判断
# 「慢」还是「卡」。
_state = {"thread": None, "running": False, "error": None,
          "done": False, "events_path": None, "done_msg": None,
          "done_frames": 0, "total_frames": 0, "stage": None,
          "autoloaded": False, "table_locked": False, "last_err_md": None,
          "cancelled": False, "last_cancel_md": None,
          "_fps": None, "_last_cb": None, "device_hint": None}

_start_lock = threading.Lock()   # 开始按钮防重入（check+set 原子化，M2）
_cancel_evt = threading.Event()  # 页面刷新/关闭 → 置位请求取消（worker 在
                                 # 检测进度节拍里查询，检测外阶段不响应）


def _work_dir_for(video_path: str) -> str:
    name = os.path.splitext(os.path.basename(video_path))[0]
    return os.path.join("work", name)


def _calib_dir(video_path) -> str | None:
    """标定随视频走：该视频的标定目录 = 其 work 目录（没给视频 → None，
    调用方各自给「未标定/请先选视频」提示）。"""
    vp = clean_path(video_path or "")
    return _work_dir_for(vp) if vp else None


def _calib_file(video_path) -> str | None:
    """该视频主标定文件完整路径（work/<视频名>/calibration.json）。"""
    d = _calib_dir(video_path)
    return os.path.join(d, CALIB_FILE) if d else None


def _load_events_or_none(video_path):
    """读 events.json；失败（未检测/损坏）返回 None，不抛。"""
    try:
        return load_events(os.path.join(
            _work_dir_for(clean_path(video_path)), "events.json"))
    except (OSError, ValueError, KeyError):
        return None


def _calib_ready(video_path) -> bool:
    """该视频的标定文件存在且可解析。路径探测的提示与开始检测的硬校验
    共用——损坏视同未标定（否则要到后台线程里才报错，提示滞后整整一轮）。"""
    return _load_calib_or_none(video_path) is not None


def _load_calib_or_none(video_path):
    """读本视频的 calibration.json；缺失/损坏/没给视频返回 None 不抛
    （run_shooter_tracing 同款容错，只换兜底方向）——供 features_valid
    做缓存指纹比对。"""
    calib_p = _calib_file(video_path)
    if not calib_p:
        return None
    try:
        return Calibration.load(calib_p)
    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        return None


# ---------- A2 多段标定：镜头移动后按时间段补标定 ----------
# 段 0 恒为 calibration.json（主标定，t0=0，形状/流程与旧版零变化）；
# 补充段各自一个文件 calibration_segN.json（t0=其 calib_t=用户跳到的
# 「时间点」，参考帧 calibration_ref_segN.png）。无索引文件——glob 发现、
# 按 t0 排序即真相（09-04_1 场景：t≈25:50 挪镜头 → 跳到移动后时刻点一次
# 筐 → 该时刻起用新几何，片头段仍用主标定）。
# 2026-09-13 起标定随视频走：全部存该视频的 work 目录，仓库根不再有
# 全局标定（换视频不串台，各视频各的筐几何/HSV）。
SEG_CALIB_FMT = "calibration_seg{}.json"
SEG_REF_FMT = "calibration_ref_seg{}.png"
SEG_GLOB = "calibration_seg*.json"


def load_segments(video_path) -> list:
    """该视频的全部标定段（CalibSegment 列表，t0 升序）。主体已迁
    calibration.load_segments_dir（vrender 复用且不能 import app），本函数
    保签名兼容旧调用点。"""
    d = _calib_dir(video_path)
    return calibration.load_segments_dir(d) if d else []


def _fmt_mmss(t: float) -> str:
    m, s = divmod(int(round(float(t))), 60)
    return f"{m}:{s:02d}"


def _seg_list_md(video_path) -> str:
    """段列表文案（标定设置页展示）。单段/未标定 → 空串（多段工作流才
    需要可见，平时零噪音）。"""
    segs = load_segments(video_path)
    if len(segs) < 2:
        return ""
    rows = ["**标定分段**——镜头移动过时按移动点分段，各段各的筐几何：",
            "- 段 1：0:00 起（主标定 calibration.json）"]
    rows += [f"- 段 {i}：{_fmt_mmss(s.t0)} 起" for i, s in enumerate(segs[1:], 2)]
    rows.append("重新「▶ 开始检测」生效：只有新段/边界变化的时段重扫，"
                "未变段缓存照用。")
    return "\n".join(rows)


def _seg_choices(video_path) -> list:
    """删段下拉的选项（段 1 主标定不可删——它由「生成标定」覆盖管理）。"""
    segs = load_segments(video_path)
    if len(segs) < 2:
        return []
    return [f"段{i}（{_fmt_mmss(s.t0)} 起）" for i, s in enumerate(segs[1:], 2)]


def del_segment(choice, video_path):
    """删除补充段（段文件+参考帧）。删中间/末段 → 后续检测该时段回退用
    前一段几何（前段扫描窗变大，positions 缓存覆盖不足自动重扫——安全）。"""
    d = _calib_dir(video_path)
    if not d:
        raise gr.Error("请先上传视频或填本机路径抽帧，再删段")
    segs = load_segments(video_path)
    if len(segs) < 2:
        raise gr.Error("只有一段主标定，没有可删的补充段")
    if not choice:
        raise gr.Error("请先在下拉框选择要删除的段")
    try:
        idx = int(str(choice).split("（")[0].replace("段", ""))
    except ValueError:
        raise gr.Error("段选择解析失败，请重选")
    if not 2 <= idx <= len(segs):
        raise gr.Error("所选段不在列表里（页面数据过期），请刷新页面重试")
    t0 = segs[idx - 1].t0
    removed = 0
    for p in glob.glob(os.path.join(d, SEG_GLOB)):
        try:
            c = Calibration.load(p)
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            continue
        if c.calib_t > 0 and abs(c.calib_t - t0) < 0.05:
            os.remove(p)
            if c.ref_image:
                rp = c.ref_image
                if not os.path.isabs(rp):
                    rp = os.path.join(d, rp)
                if os.path.exists(rp):
                    try:
                        os.remove(rp)
                    except OSError:
                        pass
            removed += 1
    if not removed:
        raise gr.Error("段文件没找到（可能已被删过），请刷新页面")
    return (_seg_list_md(video_path),
            gr.update(choices=_seg_choices(video_path), value=None),
            f"已删除 {_fmt_mmss(t0)} 起的段——重新「▶ 开始检测」后该时段回退"
            "用前一段的几何")


def migrate_root_calibrations() -> list:
    """一次性迁移（标定随视频走上线时）：仓库根的旧全局标定集
    （calibration.json + calibration_segN.json + 各自参考帧 png，2026-09-13
    前全视频共用一套——换视频会串台）按内容指纹认领——与哪个
    work/<视频>/events.json 的 calib_key 一致，整套复制进该视频目录
    （多个视频同指纹=都曾用这套标定检测过，各留一份，各自缓存继续
    有效），认领成功后删根文件；无人认领（标定后从未检测过）原地不动
    ——新代码只读 work/<视频>/，不猜归属，留给用户自行归置。
    幂等：根没有主标定文件即返回空。"""
    if not os.path.exists(CALIB_FILE):
        return []
    try:
        base = Calibration.load(CALIB_FILE)
    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        return [f"仓库根 {CALIB_FILE} 损坏，未迁移（新代码只读各视频 work 目录）"]
    segs = [CalibSegment(0.0, base, None)]
    for p in sorted(glob.glob(SEG_GLOB)):
        try:
            c = Calibration.load(p)
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            continue
        if c.calib_t > 0:
            segs.append(CalibSegment(c.calib_t, c, None))
    segs.sort(key=lambda s: s.t0)
    key = segments_key(segs)
    claims = []
    if os.path.isdir("work"):
        for d in sorted(os.listdir("work")):
            ep = os.path.join("work", d, "events.json")
            if not os.path.isfile(ep):
                continue
            try:
                if load_events(ep).get("calib_key") != key:
                    continue
            except (OSError, ValueError, KeyError):
                continue
            claims.append(os.path.join("work", d))
    if not claims:
        return ["仓库根旧标定无人认领（标定后未用其检测过），已留在原地不迁移："
                + CALIB_FILE]
    files = ([CALIB_FILE] + sorted(glob.glob(SEG_GLOB))
             + ([REF_IMG_FILE] if os.path.exists(REF_IMG_FILE) else [])
             + sorted(glob.glob(SEG_REF_FMT.format("*"))))
    for d in claims:
        os.makedirs(d, exist_ok=True)
        for f in files:
            shutil.copy2(f, os.path.join(d, os.path.basename(f)))
    for f in files:
        os.remove(f)
    return [f"旧全局标定已按指纹认领迁入 {d}/" for d in claims]


def _detect_worker(video_path: str):
    try:
        segs = load_segments(video_path)
        if not segs:
            _state["error"] = "本视频的标定缺失或不可读，请到「⚙ 标定设置」重新标定"
            return

        _state["device_hint"] = _detect_env_hint()

        def cb(frame_idx, total):
            """进度回调：帧率取指数滑动平均（0.7·旧 + 0.3·新），单次抖动
            （首帧预热、偶发卡顿）不至于把 ETA 拽成「还要 3 天」或「马上好」。
            间隔 <0.2s 的样本不计——否则开头几帧会把 dt 压到毫秒级、算出
            天文数字帧率。"""
            now = time.monotonic()
            prev = _state.get("_last_cb")
            if prev is None:
                _state["_last_cb"] = (frame_idx, now)
            elif frame_idx > prev[0] and now - prev[1] >= 0.2:
                inst = (frame_idx - prev[0]) / (now - prev[1])
                old = _state.get("_fps")
                _state["_fps"] = inst if not old else old * 0.7 + inst * 0.3
                _state["_last_cb"] = (frame_idx, now)
            _state["done_frames"] = frame_idx
            _state["total_frames"] = total

        # 多段标定（A2）：段列表整体送检——单段时 pipeline 的段组合指纹
        # 与旧版 calib_key 逐字节相同，存量缓存照常命中
        events_path, warning = pipeline.run_detection(
            video_path, segs, _work_dir_for(video_path), progress_cb=cb,
            stage_cb=lambda m: _state.update(stage=m),
            cancel_cb=_cancel_evt.is_set)
        data = load_events(events_path)
        n_high = sum(1 for e in data["events"] if e.status == "high")
        n_low = sum(1 for e in data["events"] if e.status == "low")
        n_review = len(data["events"]) - n_high - n_low
        msg = (f"✅ 完成：🟢 高置信 {n_high} 个，🟡 待确认 {n_review} 个，"
               f"⚪ 低置信 {n_low} 个（否决门槛拦下的候选，供人工查漏；"
               "默认不勾选、不进集锦）")
        if warning:
            msg += f"\n⚠️ {warning}"
        _state.update(events_path=events_path, done=True, done_msg=msg)
    except pipeline.DetectionCancelled:
        _state["cancelled"] = True       # 非错误：检查点已存，可续跑
    except Exception as e:  # noqa: BLE001 —— 任何错误都要带回界面
        _state["error"] = str(e)
    finally:
        _state["running"] = False


def start_detection(video_path: str):
    video_path = clean_path(video_path)              # 去粘贴路径的空白/引号（M6）
    if not _calib_ready(video_path):
        raise gr.Error("本视频还没标定（或标定文件损坏）——先切到"
                       "「⚙ 标定设置」完成一次（每个视频各标各的）")
    if not video_path or not os.path.exists(video_path):
        raise gr.Error(f"视频不存在: {video_path}")
    with _start_lock:                                # check+set 原子（M2）
        if _state["running"]:
            raise gr.Error("已有检测在跑")
        _state.update(running=True, done=False, error=None, events_path=None,
                      done_msg=None, done_frames=0, total_frames=0, stage=None,
                      autoloaded=False, last_err_md=None,
                      cancelled=False, last_cancel_md=None,
                      _fps=None, _last_cb=None, device_hint=None)
        _cancel_evt.clear()
    _state["thread"] = threading.Thread(target=_detect_worker,
                                        args=(video_path,), daemon=True)
    _state["thread"].start()
    md, _pct = _refresh_status()
    return md              # 进度条组件已删（2026-08-23）：百分比在文案里


def _on_page_unload():
    """页面关闭/刷新（demo.unload）：检测在跑则请求取消——否则后台线程会
    孤儿跑到完（白烧 CPU，还会和重进页面的新一轮检测打架）。单用户本地
    应用：多开页面时关掉任意一页都会触发取消。"""
    if _state["running"]:
        _cancel_evt.set()


def _fmt_eta(sec: float) -> str:
    """秒 → 人话（「3 分钟」「1 小时 20 分」「2 天 6 小时」）。"""
    if sec < 60:
        return "不到 1 分钟"
    if sec < 3600:
        return f"{int(sec // 60)} 分钟"
    if sec < 86400:
        h, m = int(sec // 3600), int(sec % 3600 // 60)
        return f"{h} 小时 {m} 分" if m else f"{h} 小时"
    d, h = int(sec // 86400), int(sec % 86400 // 3600)
    return f"{d} 天 {h} 小时" if h else f"{d} 天"


def _detect_env_hint() -> str:
    """一行说明「这轮到底拿什么在算」：CPU/CUDA + imgsz + 是否隔帧取。

    起因（2026-11-17）：远端朋友报「10 分钟进度还是 0%」，只看界面截图无法
    区分「纯 CPU 慢」与「真卡住」——两者都表现为数字不动。把设备与分辨率
    写进状态行后，一张截图就能定性。探测失败一律按 CPU 文案降级，绝不影响
    检测本身。"""
    try:
        from . import detector_yolo as DY
        cfg = DY.CFG
        dev = int(cfg.get("device", 0))
        imgsz = (cfg.get("crop_imgsz") if cfg.get("crop") == "roi"
                 else cfg.get("imgsz"))
        if dev < 0:
            dev_s = "CPU（手动指定）"
        else:
            try:
                import torch
                ok = bool(torch.cuda.is_available())
                name = (torch.cuda.get_device_name(dev)
                        if ok and dev < torch.cuda.device_count() else "")
            except Exception:            # noqa: BLE001 —— 探测失败当 CPU
                ok, name = False, ""
            dev_s = f"CUDA:{dev} {name}".strip() if ok else "CPU（CUDA 不可用）"
        out = f"{dev_s} · imgsz {imgsz}"
        step = int(cfg.get("step") or 1)
        if step > 1:
            out += f" · 每 {step} 帧取 1"
        return out
    except Exception:                    # noqa: BLE001
        return ""


def _refresh_status():
    if _state["error"]:
        return f"❌ 出错：{_state['error']}", 0
    if _state["running"]:
        done = int(_state.get("done_frames") or 0)
        total = int(_state.get("total_frames") or 0)
        pct = int(done * 100 / total) if total else 0
        if _cancel_evt.is_set():           # 已请求取消，等 worker 到下一节拍
            return f"⏹ 正在取消… ({pct}%)——正在保存检查点", pct
        if _state.get("stage"):            # 转码等阶段：无帧进度，只显提示
            return f"⏳ {_state['stage']}", 0
        # 总帧数读不到（部分容器 CAP_PROP_FRAME_COUNT 返回 0）时不能显 0/0：
        # 那和「还没开始」长得一样，用户会以为卡死。改报已处理帧数。
        head = (f"⏳ 检测中… {done:,}/{total:,} 帧 ({pct}%)" if total
                else f"⏳ 检测中… 总帧数未知，已处理 {done:,} 帧")
        bits = []
        fps = _state.get("_fps")
        if fps and fps > 0:
            bits.append(f"{fps:.1f} 帧/秒")
            if total > done:
                bits.append(f"预计还需 {_fmt_eta((total - done) / fps)}")
        hint = _state.get("device_hint")
        if hint:
            bits.append(hint)
        return head + (" · " + " · ".join(bits) if bits else ""), pct
    if _state["done"]:
        return _state.get("done_msg") or "✅ 完成", 100   # 已预计算，不重读文件
    if _state.get("cancelled"):
        return "⏹ 已取消（页面刷新/关闭）——已检测部分存入检查点，重新点「开始检测」可续跑", 0
    return "空闲。检测中途退出后，重新点开始即可从检查点续跑。", 0


def _table_unlock():
    """表格 interactive 的"恢复一次"：仅在锁定结束后的首个 tick 发
    interactive=True，之后回空 update。每 tick 都发属性更新会让 Dataframe
    反复重渲染——这是剪辑台周期性闪烁的来源之一。"""
    if _state.get("table_locked"):
        _state["table_locked"] = False
        return gr.update(interactive=True)
    return gr.update()


def refresh_and_autoload(video_path: str):
    """Timer 轮询：进度刷新 + 检测完成自动载入表格（只载一次，不覆盖复核编辑）。
    返回 6 元组，对应 [status_md, duration_md, events_table,
    rows_state, cur_row_state, cur_panel]（进度条组件 2026-08-23 删除——
    百分比在 status 文案里，条带占的左栏高度还给了表格）。检测中锁表格，
    解锁后首个 tick 经 _table_unlock 恢复 interactive=True 一次（纯值/空
    update 都不碰该属性，不恢复就永久锁死）。空闲 tick 全部空 update：
    Timer 每 2s 一 tick，同值重写 status 也会让组件重渲染（闪烁），且会
    覆盖 probe_existing 等先前写入的提示。出错分支只在文案变化时写实值，
    错误才能上屏。"""
    md, _pct = _refresh_status()
    if _state["running"]:
        _state["table_locked"] = True
        return (md, gr.update(), gr.update(interactive=False), gr.update(),
                gr.update(), "⏳ 检测中——完成后自动载入结果（刷新/关闭页面会取消检测）")
    if _state["error"]:
        # 文案有变才写（新错误/重跑后同样错误）；重复同值写入 = 周期性闪烁
        if md != _state.get("last_err_md"):
            _state["last_err_md"] = md
            return (md, gr.update(), _table_unlock(), gr.update(),
                    gr.update(), gr.update())
        return (gr.update(), gr.update(), _table_unlock(),
                gr.update(), gr.update(), gr.update())
    if _state.get("cancelled"):
        # 与错误分支同型：文案只在变化时写一次，防空闲 tick 周期性重渲染
        if md != _state.get("last_cancel_md"):
            _state["last_cancel_md"] = md
            return (md, gr.update(), _table_unlock(), gr.update(),
                    gr.update(), gr.update())
        return (gr.update(), gr.update(), _table_unlock(),
                gr.update(), gr.update(), gr.update())
    if _state["done"] and not _state.get("autoloaded"):
        _state["autoloaded"] = True
        _state["table_locked"] = False    # 成功分支的 update 自带 interactive=True
        try:
            dur_md, proj, rows, _, panel = load_table_for_ui(video_path)
            return (md, dur_md, gr.update(value=proj, interactive=True),
                    rows, None, panel)
        except (gr.Error, OSError, ValueError, KeyError) as e:
            return (md, gr.update(), _table_unlock(),
                    gr.update(), gr.update(), f"检测完成但载入失败：{e}")
    return (gr.update(), gr.update(), _table_unlock(),
            gr.update(), gr.update(), gr.update())


# ---------- 剪辑台：复核与出片 ----------

def _load_tags() -> list[str]:
    """全局标签库（tags.json）。损坏/缺失按空处理，不炸界面。"""
    try:
        with open(TAGS_FILE, encoding="utf-8") as f:
            tags = json.load(f).get("tags", [])
    except (OSError, ValueError):
        return []
    return [t for t in tags if isinstance(t, str) and t.strip()]


def _save_tags(tags: list[str]):
    with open(TAGS_FILE, "w", encoding="utf-8") as f:
        json.dump({"tags": list(tags)}, f, ensure_ascii=False, indent=1)


def _fmt_t(t: float) -> str:
    """秒 → m+:ss.s 行键——与 shooters.json/truth 键同口径，单一来源：
    委托 models.time_key（分钟宽度不定，110 分钟视频有三位）。"""
    return time_key(t)


def _load_deleted(video_path: str) -> list:
    """读已删清单（键 = 原始检测时刻，与勾选/标签同口径）；缺失/损坏当空。"""
    try:
        with open(os.path.join(_work_dir_for(video_path), DELETED_FILE),
                  encoding="utf-8") as f:
            data = json.load(f)
        return [k for k in (data if isinstance(data, list) else [])
                if isinstance(k, str)]
    except (OSError, ValueError):
        return []


def _save_deleted(video_path: str, keys) -> None:
    work = _work_dir_for(video_path)
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, DELETED_FILE), "w", encoding="utf-8") as f:
        json.dump(list(keys), f, ensure_ascii=False, indent=1)


def _filter_deleted(data, video_path: str):
    """events 数据 dict 的未删视图：已删球从 events 列表剔除，其余键
    （duration 等）原样共享。删除过滤的唯一实现——表格/判定依据/溯源/
    墙全走它，防止某处漏滤让已删球借尸还魂。data 为 None 时原样返回。"""
    deleted = set(_load_deleted(video_path))
    if not deleted or not data:
        return data
    return dict(data, events=[e for e in (data.get("events") or [])
                              if _fmt_t(e.t) not in deleted])


def _raw_time_keys(video_path: str, rows) -> list:
    """按行号取每行的**原始检测时刻**键（events.json 第 i 条 ↔ 表第 i 行，
    载入时同序生成）。标签/勾选都用它做键——显示时间受时刻校准偏移影响，
    按显示时间存会换偏移就丢。events 读不到或条数对不上（重跑检测后
    State 过期）时退回显示时间（offset=0 时两者相同）。删过的球不占
    行号——读的是未删视图（deleted.json），删行后对齐照样成立。"""
    data = _filter_deleted(_load_events_or_none(video_path), video_path)
    evs = (data.get("events") or []) if data else []
    if len(evs) == len(rows or []):
        return [_fmt_t(e.t) for e in evs]
    return [r[1] for r in (rows or [])]


def _save_assignments(video_path: str, table):
    """把表格里的标签列存到本视频 work 目录（键 = 原始检测时刻，重载可恢复）。"""
    keys = _raw_time_keys(video_path, table)
    assign = {k: r[6] for k, r in zip(keys, table or [])
              if len(r) > 6 and str(r[6] or "").strip()}
    work = _work_dir_for(video_path)
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, ASSIGN_FILE), "w", encoding="utf-8") as f:
        json.dump(assign, f, ensure_ascii=False, indent=1)


def _load_selection(video_path: str) -> set:
    """读勾选表；缺失/损坏一律当空集（默认全不勾，2026-08-22 起）。"""
    try:
        with open(os.path.join(_work_dir_for(video_path), SELECTION_FILE),
                  encoding="utf-8") as f:
            data = json.load(f)
        return {s for s in (data if isinstance(data, list)
                            else data.get("selected") or [])
                if isinstance(s, str)}
    except (OSError, ValueError, AttributeError):
        return set()


def _save_selection(video_path: str, rows) -> None:
    """勾选行的时间串落盘（键 = 原始检测时刻，与标签分配同口径：
    换偏移不丢，重跑检测后对不上的键自然作废）。"""
    video_path = clean_path(video_path)
    keys = _raw_time_keys(video_path, rows)
    sel = [k for k, r in zip(keys, rows or []) if r and r[0]]
    work = _work_dir_for(video_path)
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, SELECTION_FILE), "w", encoding="utf-8") as f:
        json.dump(sel, f, ensure_ascii=False, indent=1)


def _load_starred(video_path: str) -> set:
    """读 ★ 精选表；缺失/损坏一律当空集（= 今天的行为，无需迁移老数据）。

    **不变式 精选 ⊆ 进片**在读盘时纠一遍（`load_events_table` 里再用勾选夹）。
    历史脏数据、手改 sidecar、别的分支只写了半张表，都靠这一层兜住。
    """
    try:
        with open(os.path.join(_work_dir_for(video_path), STARRED_FILE),
                  encoding="utf-8") as f:
            data = json.load(f)
        return {s for s in (data if isinstance(data, list)
                            else data.get("starred") or [])
                if isinstance(s, str)}
    except (OSError, ValueError, AttributeError):
        return set()


def _save_starred(video_path: str, rows) -> None:
    """★ 精选行的时间串落盘（键 = 原始检测时刻，与勾选/标签同口径：
    换偏移不丢，重跑检测后对不上的键自然作废）。"""
    video_path = clean_path(video_path)
    keys = _raw_time_keys(video_path, rows)
    star = [k for k, r in zip(keys, rows or [])
            if r and len(r) > 7 and r[7]]
    work = _work_dir_for(video_path)
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, STARRED_FILE), "w", encoding="utf-8") as f:
        json.dump(star, f, ensure_ascii=False, indent=1)


def _load_assignments(video_path: str) -> dict[str, str]:
    try:
        with open(os.path.join(_work_dir_for(video_path), ASSIGN_FILE),
                  encoding="utf-8") as f:
            return {k: v for k, v in json.load(f).items()
                    if isinstance(k, str) and isinstance(v, str)}
    except (OSError, ValueError):
        return {}


def _persist_assignments(video_path: str, table):
    """表格任何手工编辑（含直接改标签格）后同步落盘；无有效视频/结果时跳过。"""
    vp = clean_path(video_path)
    if not vp or not os.path.exists(os.path.join(_work_dir_for(vp), "events.json")):
        return
    _save_assignments(vp, table)


def load_events_table(video_path: str, offset: float = 0.0):
    """读 events.json 生成表格行：勾选从本视频的 selection.json 恢复
    （没存过 = 全不勾，高置信也不默认勾选，2026-08-22 起）；前后秒默认
    5/2；标签列从分配表恢复（之前打过标的进球不用重打）。
    offset：进球时刻整体校准（秒，正=后移）——检测时刻与用户认定的
    进球瞬间有系统性偏差时用，作用在展示/切片层，不动检测结果。
    已删球（deleted.json）不生成行——删除是剪辑台视图层的非破坏剔除。"""
    work_dir = _work_dir_for(video_path)
    path = os.path.join(work_dir, "events.json")
    if not os.path.exists(path):
        raise gr.Error("尚无检测结果——先点「▶ 开始检测」")
    data = _filter_deleted(load_events(path), video_path)
    assign = _load_assignments(video_path)
    sel = _load_selection(video_path)
    star = _load_starred(video_path)

    rows = []
    for e in data["events"]:
        t = max(0.0, e.t + (offset or 0.0))
        key = _fmt_t(e.t)                 # 分配/勾选表都按原始检测时刻存
        # 第 8 列 ★ 精选（2026-10-23）：**读盘时用勾选夹一次**，把不变式
        # 「精选 ⇒ 进片」钉死在唯一的数据入口——历史脏数据、手改 sidecar、
        # 只写了半张表的分支都从这兜住。漏了它就会出现"精选了但没进片"
        # ⇒ 单条发出一个集锦里根本没有的片段。
        rows.append([1 if key in sel else 0, _fmt_t(t), e.status,
                     f"{e.confidence:.2f}", DEFAULT_BEFORE_S, DEFAULT_AFTER_S,
                     assign.get(key, ""),
                     1 if (key in star and key in sel) else 0])
    return data["duration"], rows


# 置信度三档的展示样式：表格用 emoji 色点（gr.Dataframe 不支持单元格
# 着色），HTML 面板用真彩色。绿=高置信、琥珀=待复核、灰=低置信（否决
# 门槛拦下但保留供人工查漏的候选，默认不勾选、不进集锦）。
STATUS_DOT = {"high": "🟢 高", "review": "🟡 待", "low": "⚪ 低"}
STATUS_LABEL = {"high": "高置信", "review": "待确认", "low": "低置信"}
STATUS_COLOR = {"high": "#16a34a", "review": "#d97706", "low": "#9ca3af"}


def project_rows(rows):
    """完整行 State → 4 列表格投影（☑ 选中/时间/档位/标签）。
    档位只显示色点（🟢 高 / 🟡 待 / ⚪ 低）——证据分数字与档位由不同
    逻辑决定（数字高仍可能被否决门槛拦下），放表格里徒增困惑，具体
    算式看点行后右侧判定依据面板。"""
    return [[int(bool(r[0])), r[1],
             STATUS_DOT.get(r[2], str(r[2])),
             str(r[6] or "")] for r in (rows or [])]


def sync_table_edits(rows, table):
    """4 列表格的编辑 → 写回完整行 State。只回写勾选与标签两列；
    时间/档位由数据生成、前后秒由全局应用统一写，均不接受表格侧修改。
    表格比 State 长时按 State 截断（正常不会发生，防御性）。"""
    out = [list(r) for r in (rows or [])]
    for i, t in enumerate(table or []):
        if i >= len(out):
            break
        out[i][0] = 1 if t[0] else 0
        out[i][6] = str(t[3] or "")
    return out


def _sync_manual_from_tag_edits(video_path, old_rows, new_rows):
    """表格标签编辑 → 归人双向同步（2026-10-11 起全双向；09-06 版曾只在
    球已归人时反向生效，用户以「剪辑台打标签后出手人墙未同步」报为缺陷
    推翻——归人标签库全是人名，打标即认定该人进球，没有第二个语义）。
    改成人名标签 = 归入该人（manual.assign 自动移出旧人，未归人也建组）；
    清空标签 = 移出归人（球回未归人墙）。墙是独立页面、切页即重取数，
    落盘即同步。勾选-only 编辑（标签没变）无事发生。"""
    work = _work_dir_for(clean_path(video_path))
    groups = manual.load(work)
    owned = {k for keys in groups.values() for k in keys}
    keys = _raw_time_keys(video_path, new_rows)
    for i, r in enumerate(new_rows or []):
        if i >= len(keys):
            break
        old = str((old_rows[i] or [])[6] or "") \
            if old_rows and i < len(old_rows) else ""
        new = str(r[6] or "")
        if new == old:
            continue
        if new:
            manual.assign(work, new, [keys[i]])
        elif keys[i] in owned:
            manual.unassign(work, [keys[i]])


def _parse_t(t_str) -> float:
    """表格时间列 m+:ss.s → 秒。分钟位数不定（110 分钟视频有三位分钟
    "101:16.5"——按分隔符切，不按固定位数）。"""
    m, _, s = str(t_str).partition(":")
    return int(m) * 60 + float(s or 0)


def _as_float(v, default: float) -> float:
    """表格里的数字格可能被清空成 None → 用默认值兜底。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _cut_preview(video_path: str, t: float, before: float, after: float) -> str:
    """切一段**帧精确**的预览（重编码 + 压到 720p 以内，几秒内出）。
    不能用 -c copy：流复制只能从关键帧开始拷贝，手机视频关键帧间隔可达
    数秒，切出来的窗口整体前移，改完前后秒后进球瞬间可能整个落在窗口外。
    起点恒 >0（1ms）：HEVC+AAC 手机源（09-11_3）-ss 0 不走 seek、时间戳
    原样透传，mp4 muxer 收到 AAC 起始负 DTS 直接 "Not yet implemented"
    炸掉——变体矩阵实测只有抬离 0 能救（faststart/editlist/make_zero 均无效）。"""
    video_path = clean_path(video_path)
    out = os.path.join(_work_dir_for(video_path), "preview.mp4")
    r = subprocess.run(
        [_ffmpeg_exe(), "-y", "-ss", f"{max(0.001, t - before):.3f}",
         "-i", video_path, "-t", f"{before + after:.3f}",
         "-vf", "scale=-2:'min(720,ih)'",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
         "-movflags", "+faststart", out],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180)
    if r.returncode != 0 or not os.path.exists(out):
        raise gr.Error(f"切预览失败：{(r.stderr or '')[-300:]}")
    return out


def _table_summary_md(dur: float, rows) -> str:
    """表格上方的时长/条数统计行（load_table_for_ui 与删行/恢复共用——
    行数变化后这条不刷就谎报「共 N 条」）。"""
    n_high = sum(1 for r in (rows or []) if r[2] == "high")
    n_low = sum(1 for r in (rows or []) if r[2] == "low")
    n = len(rows or [])
    return (f"视频总长 {int(dur // 60)} 分 {dur % 60:.0f} 秒 · 共 {n} 条"
            f"（🟢 高置信 {n_high} / 🟡 待确认 {n - n_high - n_low}"
            f" / ⚪ 低置信 {n_low}，低置信默认不进集锦）")


def load_table_for_ui(video_path: str, offset: float = 0.0):
    """载入检测结果 → (时长统计, 4 列投影, 完整行 State, 当前行复位, 面板)。
    对应 [duration_md, events_table, rows_state, cur_row_state, cur_panel]。"""
    dur, rows = load_events_table(clean_path(video_path), offset)
    return (_table_summary_md(dur, rows), project_rows(rows), rows, None,
            _cur_panel_html(rows, None))


def probe_existing(video_path: str):
    """路径输入变化 → 自动探测已有检测结果：有则直接载入，无则清空待检测。
    对应 [duration_md, events_table, rows_state, cur_row_state, cur_panel,
    status_md]。"""
    vp = clean_path(video_path)
    if not vp or not os.path.exists(os.path.join(_work_dir_for(vp),
                                                 "events.json")):
        if _calib_ready(vp):
            hint = "新视频：点「开始检测」开始（检测中途退出可续跑）"
        else:   # 检测的前置条件不满足时直接提醒，别等点了开始才报错
            hint = ("⚠️ 本视频还没标定——先到「⚙ 标定设置」完成一次"
                    "（每个视频各标各的），再回来检测本视频")
        return ("", [], [], None, _PANEL_INIT, hint)
    try:
        dur_md, proj, rows, _, panel = load_table_for_ui(vp)
    except (OSError, ValueError, KeyError):
        return ("", [], [], None, _PANEL_INIT,
                "检测结果文件损坏——请重新检测覆盖")
    return (dur_md, proj, rows, None, panel,
            "已载入已有检测结果——重新检测会覆盖")


def _player_html(path: str, before: float, after: float) -> str:
    """切片播放器：原生 video + 分色进度条（进球前蓝 / 进球后绿 / 进球时刻
    红线），播放圆点同步、点击进度条跳转。只用内联事件处理器——gr.HTML 经
    innerHTML 注入，<script> 不会执行，内联属性会。
    autoplay 必须配 muted：点行到视频注入隔了服务端切片的几秒，用户手势
    激活窗口已过期，未静音的自动播放会被浏览器拦截（表现为黑屏不动）。
    默认 2 倍速（2026-08-22）：onloadedmetadata 设 playbackRate=2，复核
    长列表省一半等待；原生 controls 仍可手动调回，只影响当前片段。"""
    total = before + after
    pre = 100.0 * before / total if total > 0 else 100.0
    # gradio 5 的文件路由挂在 /gradio_api 前缀下；中文/空格路径需整体编码
    url = "/gradio_api/file=" + urllib.parse.quote(
        os.path.abspath(path).replace("\\", "/"), safe="/:")
    # 每次点行都重写同一 preview.mp4 而 URL 不变 → 浏览器命中缓存不重拉，
    # 表现为"切不动"（真机复现：文件已换、dur 不变）。mtime 查询参数击穿缓存。
    url += f"?v={os.path.getmtime(path):.6f}"
    return f"""
<div style="font-size:13px">
  <video id="bbh_pv" src="{url}" controls autoplay muted playsinline
         onloadedmetadata="this.playbackRate=2"
         style="width:100%; max-height:clamp(220px,46dvh,560px); background:#000; border-radius:8px"
         ontimeupdate="document.getElementById('bbh_dot').style.left=
             (this.duration ? 100*this.currentTime/this.duration : 0)+'%'"></video>
  <div onclick="var v=document.getElementById('bbh_pv');
               var r=this.getBoundingClientRect();
               if(v.duration) v.currentTime=v.duration*(event.clientX-r.left)/r.width"
       title="点击进度条跳转到对应时间"
       style="cursor:pointer; margin-top:6px; user-select:none">
    <div style="position:relative; height:26px; border-radius:13px; overflow:hidden">
      <div style="position:absolute; left:0; top:0; bottom:0; width:{pre:.1f}%;
                  background:#2563eb; color:#fff; display:flex; align-items:center;
                  justify-content:center">进球前 {before:.0f}s</div>
      <div style="position:absolute; left:{pre:.1f}%; top:0; bottom:0; right:0;
                  background:#16a34a; color:#fff; display:flex; align-items:center;
                  justify-content:center">进球后 {after:.0f}s</div>
      <div style="position:absolute; left:calc({pre:.1f}% - 2px); top:0; bottom:0;
                  width:4px; background:#ef4444" title="进球时刻"></div>
      <div id="bbh_dot" style="position:absolute; left:{pre:.1f}%; top:3px;
                  bottom:3px; width:6px; margin-left:-3px; background:#111;
                  border-radius:3px"></div>
    </div>
  </div>
</div>"""


_PANEL_INIT = ('<div style="color:#888">当前行：无——点击左侧表格任意一行，'
               '这里显示该条信息并预览</div>')


def _info_card(row) -> str:
    """ℹ 悬浮卡：当前行完整数据（含表格 4 列里看不到的字段）。
    纯内联事件实现 hover——gr.HTML 经 innerHTML 注入，<script> 不执行、
    内联 onmouseover/onmouseout 执行（与播放器 ontimeupdate 同一套约束）。"""
    checked, t_str, status, conf, before, after, tag = (list(row) + [""] * 7)[:7]
    before, after = (_as_float(before, DEFAULT_BEFORE_S),
                     _as_float(after, DEFAULT_AFTER_S))  # 与头部行信息同归一
    items = [
        f"时间　{t_str}",
        f"状态　{STATUS_LABEL.get(status, str(status))}（置信度 {conf}）",
        f"切片　前 {before} 秒 + 后 {after} 秒",
        f"标签　{html.escape(str(tag)) or '—'}",
        f"选中　{'是' if checked else '否'}",
    ]
    card = "<br>".join(items)
    return (
        '<span style="position:relative; cursor:help; font-size:15px" '
        'title="悬浮查看该条完整数据" '
        "onmouseover=\"this.lastElementChild.style.display='block'\" "
        "onmouseout=\"this.lastElementChild.style.display='none'\">ℹ️"
        '<div style="display:none; position:absolute; right:0; top:130%; '
        'z-index:50; background:#fff; border:1px solid #d1d5db; '
        'border-radius:8px; padding:10px 14px; '
        'box-shadow:0 4px 16px rgba(0,0,0,.18); font-size:12px; '
        f'line-height:1.9; white-space:nowrap">{card}</div></span>')


def _cur_panel_html(rows, i, reason: str = "", video_path: str = None) -> str:
    """右栏「当前行」面板：行信息 + 判定依据 + ℹ 悬浮完整数据卡。
    取代旧 _cur_row_md 与 preview_msg 两个组件的职责。
    video_path 给定时附「出手人」缩略图块（_shooter_block_html）。"""
    if rows is None or not isinstance(i, int) or not (0 <= i < len(rows)):
        return _PANEL_INIT
    row = rows[i]
    _, t_str, status, conf, before, after, tag = (list(row) + [""] * 7)[:7]
    before, after = (_as_float(before, DEFAULT_BEFORE_S),
                     _as_float(after, DEFAULT_AFTER_S))  # 格被清空成 None 时兜底
    tag_s = html.escape(str(tag))            # 用户标签可含 < 等字符，内容位必转义
    badge = (f'<span style="color:{STATUS_COLOR.get(status, "#333")}; '
             f'font-weight:600">{STATUS_LABEL.get(status, str(status))}</span>')
    head = (f'<div style="font-size:14px"><b>当前：第 {i + 1} 条 · {t_str} · '
            f'{badge}（{conf}） · '
            f'标签 {tag_s or "—"} · 前 {before:.0f}s + 后 {after:.0f}s '
            f'{_info_card(row)}</b></div>')
    body = (f'<div style="font-size:12.5px; color:#555; margin-top:4px">'
            f'{html.escape(str(reason))}</div>') if reason else ""
    return head + body + _shooter_block_html(video_path, rows, i)


def _reason_text(e) -> str:
    """GoalEvent 的判定依据 → 一行中文（点行预览时展示，复核时有据可查）。"""
    if e.cross_off is None:      # 旧版 events.json 未记录依据字段
        return "判定依据：旧版结果未记录（重跑检测后可见）"
    low_head = f"已降为低置信——{e.low_reason}。" if e.low_reason else ""
    if abs(e.cross_off) < 0.005:
        where = "筐心正中"
    else:
        where = (f"筐心{'右' if e.cross_off > 0 else '左'}侧 "
                 f"{abs(e.cross_off) * 100:.0f}% 筐宽处")
    parts = [f"{low_head}判定依据：下穿点在{where}；"
             f"上方证据 {e.above_n} 点/{e.above_span:.1f}s、"
             f"下方 {e.below_n} 点/{e.below_span:.1f}s"]
    if e.descent_px is not None:
        parts.append(f"、下落步长 {e.descent_px:.0f}px"
                     if e.below_n and e.below_n > 1 else "（单点，下落未证）")
    if e.below_big is not None:
        if e.below_big > 0:
            parts.append(f"、球体点 {e.below_big}")
        else:
            parts.append("、穿筐后球体未再现（已降级复核）")
    # 硬门逐门说因（2026-10-14 用户报「68% 仍待」找不到原因）：entity/pop_back
    # 已有解释，geom 与 fallback 此前只字不提——用户只能盯着「conf≥0.65 该绿」
    # 与档位口径找矛盾。顺序同硬门优先级 entity > geom > pop_back。
    if e.review_reason == "geom":
        parts.append("⚠️ 球心压筐沿区（|横偏|>30% 筐宽）——多半打铁/弹出，"
                     "强制待复核（置信度再高也不放行）")
    elif e.review_reason == "fallback":
        parts.append("⚠️ 兜底注入行（由兜底下落链生成的新事件）——恒待复核，"
                     "置信度再高也不放行")
    elif e.review_reason == "area":
        rel = getattr(e, "cross_area_rel", None)
        if rel is None:
            parts += ["⚠️ 穿筐物投影面积过大（是手/臂/身体穿过而非球）——"
                      "强制待复核"]
        else:
            parts += [f"⚠️ 穿筐物投影面积达筐口的 {rel * 100:.0f}%"
                      f"（真球约 12~16%）——是手/臂/身体穿过而非球，"
                      f"强制待复核"]
    if e.pop_back is not None:
        parts.append(f"⚠️ 下穿后 {e.pop_back:.1f}s 球又回到筐口上方——疑似弹出，"
                     "请重点核对")
    if e.net_post is not None:
        if e.review_reason == "b2":
            parts.append(f"网区实测 {e.net_post:.1f}（阈 {NET_STATIC_TH}）——"
                         "注入行网分按 0 计（时刻为链内插，实测窗可能错过"
                         "摆动相位）")
        elif e.net_post < NET_STATIC_TH:
            parts.append(f"⚠️ 篮网未摆动（信号 {e.net_post:.1f}，对照 "
                         f"{e.net_ctrl:.1f}）——球大概率从筐前/筐后飞过或"
                         f"未触网（网分 −{abs(NET_SCORE_STATIC):.2f}）")
        elif e.net_local is False:
            parts.append(f"⚠️ 网区信号 {e.net_post:.1f} 与对照区 "
                         f"{e.net_ctrl:.1f} 同步等亮——是画面整体抖动/噪点"
                         f"伪装的「网动」，非局部网摆（网分 −{abs(NET_SCORE_GLOBAL):.2f}）")
    # 置信度构成：各分项由已存的依据字段重算（与检测时打分同一套函数，
    # 网分项 v18 起由 net_term 同源计算——B2 注入行按 0，spec §3.5），
    # 已有结果重载也能展示完整算式，无需重跑检测。
    up = _side_score(e.above_n, e.above_span)
    down = _side_score(e.below_n, e.below_span)
    pos = _pos_score(abs(e.cross_off))
    net = net_term(e)
    parts.append(f"置信度 {e.confidence:.2f} ＝ 基础 0.30 ＋ 上方证据 {up:.2f} "
                 f"＋ 下方证据 {down:.2f} ＋ 位置 {pos:.2f} ＋ 网 {net:.2f}"
                 f"（≥{HIGH_CONF:.2f} 高置信、"
                 f"{MIN_CONF:.2f}~{HIGH_CONF - 0.01:.2f} 待确认、"
                 "更低或被否决门槛拦截=低置信；"
                 "硬门拦截（压筐沿/球体未再现/疑似弹出/兜底/B2 注入）=恒待确认，"
                 "置信度再高也不升绿；"
                 f"网：静止 −{abs(NET_SCORE_STATIC):.2f} · "
                 f"非局部 −{abs(NET_SCORE_GLOBAL):.2f} · 网动/未测 0.00）")
    return "".join(parts)


def _reason_for(video_path, rows, i) -> str:
    """取第 i 行的判定依据文本（读 events.json，无切片成本）——
    preview_row_click 之外，打标/全局应用重建面板也要带上依据。
    文件缺失/损坏或行号越界返回空串——面板降级但不报错（已删球不占
    行号——读未删视图，与表格行对齐）。"""
    data = _filter_deleted(_load_events_or_none(video_path), video_path)
    if not data:
        return ""
    events = data.get("events") or []
    if not (0 <= i < len(events)) or not (0 <= i < len(rows or [])):
        return ""
    return _reason_text(events[i])


def preview_row_click(video_path: str, rows, evt: gr.SelectData):
    """表格行点击 → 立即预览该行并设为「当前行」（打标签的作用对象）。
    每个进球独立切片，不与相邻片段合并（实测反馈：用户不要合并）。
    前后秒只有全局一套（2026-08-19 删单条配置），预览按该行存的值切。
    返回 (播放器 HTML, 成品收起, 当前行号, 当前面板, 该行标签下拉,
    缩略图误标按钮文案, 误标计数行, 十字误标按钮文案, 十字误标计数行)，
    对应 [preview_player, highlight_player, cur_row_state, cur_panel,
    row_tag_dd, btn_thumb_flag, thumb_flag_md, btn_cross_flag,
    cross_flag_md]——下拉回显该行现标签 + choices 顺手刷成 _tag_choices 款
    （归人新名切回剪辑台点行即见）；两个误标按钮态跟随该球（点行是看
    缩略图的必经动作，标记态零额外操作即对齐）。程序化赋值不触发
    .input，无回环。"""
    i = evt.index[0] if isinstance(evt.index, (list, tuple)) else None
    if not isinstance(i, int) or i < 0 or not (0 <= i < len(rows or [])):
        raise gr.Error("请点击表格中的数据行（点击表头无效）")
    before, after = (_as_float(rows[i][4], DEFAULT_BEFORE_S),
                     _as_float(rows[i][5], DEFAULT_AFTER_S))
    player, reason = _preview_and_reason(video_path, rows, i, before, after)
    keys = _raw_time_keys(video_path, rows)
    flagged = keys[i] in _load_flag_set(video_path, THUMB_FLAG_FILE) \
        if i < len(keys) else False
    crossed = keys[i] in _load_flag_set(video_path, CROSS_FLAG_FILE) \
        if i < len(keys) else False
    return (player, gr.update(visible=False), i,
            _cur_panel_html(rows, i, reason, video_path),
            gr.update(value=rows[i][6] or None,
                      choices=_tag_choices(video_path)),
            "✅ 已标记有误（点此取消）" if flagged else "⚠ 缩略图有误",
            _thumb_flag_md(video_path),
            "✅ 十字已标记（点此取消）" if crossed else "✚ 十字有误",
            _cross_flag_md(video_path))


def _hint_html(text: str) -> str:
    """预览位占位提示的统一样式（灰字 + 上下留白），三处共用。
    输出进不消毒的 gr.HTML：调用方须传可信/已转义文本，勿直接拼用户数据。"""
    return '<div style="color:#888; padding:12px 0">' + text + "</div>"


def _back_to_preview():
    """「↩ 回到逐条预览」：收起集锦成品，恢复预览占位。
    对应 [highlight_player, preview_player, btn_back]。"""
    return (gr.update(visible=False),
            _hint_html("点表格任意一行，这里播放该片段"),
            gr.update(visible=False))


def _preview_and_reason(video_path: str, rows, i, before, after):
    """切当前行预览并取判定依据（preview_row_click 用）。
    返回 (播放器 HTML, reason)。分色条按"实际落进切片"的前后长度
    （视频首尾处窗口会被裁短）。"""
    t = _parse_t(rows[i][1])
    data = _load_events_or_none(video_path)
    duration = data["duration"] if data else float("inf")
    out = _cut_preview(video_path, t, before, after)
    if duration == float("inf"):
        split_b, split_a = before, after
    else:
        split_b = min(before, t)
        split_a = min(after, max(duration - t, 0.0))
    return _player_html(out, split_b, split_a), _reason_for(video_path, rows, i)


def apply_all_rows(video_path: str, rows, b, a, cur_row=None):
    """全局前后秒 → 写回全部行并重出投影；当前行面板同步刷新（带判定
    依据，读文件无切片成本）。前后秒只有全局一套（2026-08-19 删单条配置）。
    对应 [rows_state, events_table, cur_panel]。"""
    b = _as_float(b, DEFAULT_BEFORE_S)  # 数字框被清空/坏值时不写 None
    a = _as_float(a, DEFAULT_AFTER_S)
    out = [list(r) for r in (rows or [])]
    for r in out:
        r[4], r[5] = b, a
    if isinstance(cur_row, int) and 0 <= cur_row < len(out):
        return (out, project_rows(out),
                _cur_panel_html(out, cur_row,
                                _reason_for(video_path, out, cur_row),
                                video_path))
    return (out, project_rows(out), gr.update())


def on_table_edit(video_path: str, rows, table):
    """表格任何手工编辑（勾选/标签格）→ 写回 State 并落盘：勾选进
    selection.json、标签进分配表；已归人球的标签有变时同步改判
    manual_groups（_sync_manual_from_tag_edits）。对应 [rows_state]。"""
    synced = sync_table_edits(rows, table)
    _sync_manual_from_tag_edits(video_path, rows, synced)
    _persist_assignments(video_path, synced)
    _save_selection(video_path, synced)
    return synced


def set_row_tag(video_path: str, rows, cur_row, tag):
    """标签下拉选择 → 打给当前行；清空选择 = 去掉该行标签。落盘分配表。
    打上非空标签 = 认定这是该人的进球 → 顺手自动勾上该行，省一次点击
    （2026-08-23 需求；清空标签**不**取消勾选——去标不等于不进集锦）。
    勾选变化同样落盘 selection.json（重开页面/重启不丢）。
    返回 (表格投影, 完整行 State, 当前面板)——下拉自身保持所选值。
    （标签库维护走「新增标签」弹窗，这里只选用已入库的标签。）"""
    if cur_row is None or not (0 <= int(cur_row) < len(rows or [])):
        raise gr.Error("请先点击表格选择一行，再选标签")
    i = int(cur_row)
    old_rows = rows
    rows = [list(r) for r in rows]          # copy-on-write：不就地污染旧 State
    rows[i][6] = (tag or "").strip()
    if rows[i][6]:
        rows[i][0] = 1                      # 打标即认定进球 → 自动勾选
    video_path = clean_path(video_path)
    _sync_manual_from_tag_edits(video_path, old_rows, rows)   # 已归人球=改判
    _save_assignments(video_path, rows)
    _save_selection(video_path, rows)
    return (project_rows(rows), rows,
            _cur_panel_html(rows, i, _reason_for(video_path, rows, i),
                            video_path))


def add_tag_to_library(name: str):
    """「新增标签」弹窗确定：入全局标签库（重名不重复加），清输入框、关弹窗。
    返回 (行标签下拉 choices 刷新, 输入框清空, 弹窗收起)。"""
    tag = (name or "").strip()
    if not tag:
        raise gr.Error("标签名不能为空")
    tags = _load_tags()
    if tag not in tags:
        tags.append(tag)
        _save_tags(tags)
    return gr.update(choices=tags), "", gr.update(visible=False)


# 生成弹窗第一项：全部勾选进球的合集（其余选项 = 标签库里每个标签）
GEN_ALL_LABEL = "视频合集（全部勾选的进球）"
# 单条模式下的同一项（2026-10-21）：语义变成「逐球各出一条」。**必须与
# GEN_ALL_LABEL 一起进 _ALL_ITEMS**——gen_selected 靠"item 是不是合集项"
# 来决定筛不筛标签，漏登记就会被当成标签名去筛 ⇒ 0 行 ⇒ 勾了合集却一条都
# 不出来，而且不报错（静默失败）。**禁止任何地方再写裸 `item == GEN_ALL_LABEL`。**
GEN_ALL_LABEL_SINGLE = "全部勾选的进球（逐球各出一条）"
_ALL_ITEMS = (GEN_ALL_LABEL, GEN_ALL_LABEL_SINGLE)

# ★ 精选（2026-10-23 单条模式；2026-10-24 两模式都支持）：只取**打星**的行。
# 与合集项并列（用户可同时勾"精选 + 张三"，跨项按时刻去重）。
# ⚠ 文案是**固定串、不含球数**：这一串同时是勾选值本身（前端 pick 存字符串），
# 一旦把"N 球"拼进来，精选数一变（再打一颗星）勾选就凭空丢——合集项
# （GEN_ALL_LABEL ↔ GEN_ALL_LABEL_SINGLE）已经吃过这个亏。球数只进 stats 行。
# 两模式两串（与合集项同一套做法）：单条"每颗星各出一条"、集锦"打星的球合成
# 一条"，只写"只出打星的球"在集锦里会让人以为每颗星一条。
GEN_STAR_LABEL = "★ 精选（只出打星的球）"
GEN_STAR_LABEL_REEL = "★ 精选（打星的球合成一条）"
# **必须登记齐全**：gen_selected 靠 `_is_star_item` 认这一项，漏一个串就会被
# 当标签名去筛 ⇒ 0 行 ⇒ 勾了精选却一条都出不来，而且不报错（静默失败）。
# 合集项（_ALL_ITEMS）踩过一模一样的坑。**禁止任何地方再写裸 `item == GEN_STAR_LABEL`。**
_STAR_ITEMS = (GEN_STAR_LABEL, GEN_STAR_LABEL_REEL)


def _is_star_item(item: str) -> bool:
    """item 是不是「★ 精选」那一项（两种输出模式各有一套文案）。"""
    return str(item or "") in _STAR_ITEMS


def star_count(rows) -> int:
    """打星行数（顶栏 ★ N 与弹窗统计行共用；只数勾选且打星的）。"""
    return sum(1 for r in (rows or []) if r and len(r) > 7 and r[7])


def _is_all_item(item: str) -> bool:
    """item 是不是「合集」那一项（两种输出模式各有一套文案）。"""
    return str(item or "") in _ALL_ITEMS


def all_label(output: str = "reel") -> str:
    """当前输出模式下合集项的展示串，给接口层拼 choices / allLabel 用。

    **只给 API 层用，不要塞进 `open_gen_dialog` 的返回值**：gradio 那边
    `outputs` 固定 4 项（`build_app` 的 btn_gen_h/v.click），函数多返回一项
    会整体错位（自查 2026-10-21 P0-2）。
    """
    return GEN_ALL_LABEL_SINGLE if str(output or "") == "single" else GEN_ALL_LABEL


def star_label(output: str = "reel") -> str:
    """当前输出模式下「★ 精选」项的展示串（与 `all_label` 对称）。

    **只给 API 层用**（理由同 `all_label`：gradio 的 outputs 是固定 4 项）。
    """
    return GEN_STAR_LABEL if str(output or "") == "single" else GEN_STAR_LABEL_REEL


def _video_date_cn(video_path) -> str:
    """文件名里的 YYYY-MM-DD → 「8月29日」（去前导零，集锦统计口径）。
    无日期（文件名不规范/测试假路径）返回空串——文案退回无日期前缀。"""
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})",
                  os.path.basename(str(video_path or "")))
    return f"{int(m.group(2))}月{int(m.group(3))}日" if m else ""


def build_goal_stats(video_path, rows) -> str:
    """勾选进球的统计行：日期前缀（文件名日期）+ 总数 + 按标签计数
    （降序，并列按首次出现）。「其他」= 未打标签的勾选球，固定殿后。
    生成弹窗与结果清单共用。

    2026-10-24 用户要求**去掉每人命中率**（原口径 2026-09-06 定：分子=
    该标签勾选数、分母=该标签总数）。原因：精选星标上线后"勾选"不再等于
    "人工核对过"——只勾部分球时那个比值实际是"精选率"，名不副实还误导，
    所以连分母统计（tag_all）一起拆掉，留着就是死代码。"""
    picked = [r for r in (rows or []) if r and r[0]]
    if not picked:
        return "📊 进球统计：尚未勾选进球"
    counts: dict[str, int] = {}        # 勾选且带标签的行数
    other = 0
    for r in picked:
        tag = str(r[6] or "").strip() if len(r) > 6 else ""
        if tag:
            counts[tag] = counts.get(tag, 0) + 1
        else:
            other += 1
    parts = [f"{t} {n} 个"
             for t, n in sorted(counts.items(), key=lambda kv: -kv[1])]
    if other:
        parts.append(f"其他 {other} 个")
    date_cn = _video_date_cn(video_path)
    head = f"{date_cn}进球统计" if date_cn else "进球统计"
    return f"📊 {head}：总进球 {len(picked)}，" + "，".join(parts)


def open_gen_dialog(video_path, rows, vertical=None):
    """「生成」点击 → 弹出选择清单：视频合集 + 标签库每个标签，
    **默认勾选合集 + 本场实际有进球的标签**（2026-10-18 用户要求：不用每次
    手勾人）；弹窗顶部带画幅模式行与当前勾选的进球统计行（日期取
    文件名）。返回 (choices, 弹窗可见, 统计, 画幅旗标)——旗标由入口决定，
    确定时 gen_selected 按它选管线。

    ⚠ 2026-10-21 起 `vertical` 可为 None（新界面把画幅收进弹窗、且**不设
    默认值**）：None ⇒ 统计行不写画幅前缀，用中性的「📊 进球统计 ｜ 」
    ——否则弹窗一打开就显示"竖屏"，等于变相默认，与"强制必选"矛盾。
    gradio 旧界面两处入口显式传 True/False，行为不变。

    为什么默认只勾「本场有球」的标签：标签库是全局跨场的，全量默认勾选会
    把本场一个球都没有的人一起选中 → 生成时逐项「无勾选进球」跳过并刷
    一屏无效汇报。清单本身仍列全部标签（可能本场还没开打），只是不预选。"""
    tags = _load_tags()
    present = []
    for row in (rows or []):
        try:
            t = (row[6] or "").strip()
        except (IndexError, AttributeError):
            continue
        if t and t not in present:
            present.append(t)
    default = [GEN_ALL_LABEL] + [t for t in tags if t in present]
    if vertical is None:
        head = ""        # 中性：build_goal_stats 自己以「📊」开头，别再叠一层
    else:
        head = "📱 竖屏运镜 ｜ " if vertical else "🎬 横屏直切 ｜ "
    stats = head + build_goal_stats(video_path, rows)
    return (gr.update(choices=[GEN_ALL_LABEL] + tags,
                      value=default),
            gr.update(visible=True),
            stats,
            bool(vertical))


def _picked_rows(table, tag_filter: str = "", starred_only: bool = False):
    """按「勾选 + 标签 + ★精选」筛行 → [(t, before, after, status)]（表格原序）。

    集锦与单条**共用这一份口径**（这段原来内联在 generate_highlight 里）：
    两处各写一份迟早漂移，而漂移的表现是"同一个勾选，集锦里有这个球、单条
    里没有"，极难归因。空结果的两句报错也在这里统一。

    `starred_only=True`（2026-10-23）：只取打星行——单条生成里的「★ 精选」
    那一项。**精选仍要求勾选**（row[0]），因为不变式就是"精选 ⊆ 进片"，
    不靠读盘纠偏兜这一层：万一有脏数据进来，这里也不放行。
    """
    tag_filter = (tag_filter or "").strip()
    picked = []
    for row in (table or []):
        if not row[0]:
            continue
        if starred_only and not (len(row) > 7 and row[7]):
            continue
        if tag_filter and row[6] != tag_filter:
            continue
        picked.append((_parse_t(row[1]), row[4], row[5], row[2]))
    if not picked:
        if starred_only:
            if any(r[0] for r in (table or [])):
                raise gr.Error("精选里没有可出的球——先在表格里给想单发的球"
                               "按 S 打 ★（打星会自动勾上进片）")
            raise gr.Error("至少勾选一个进球")
        if any(r[0] for r in (table or [])):
            raise gr.Error(f"勾选的进球里没有标签为「{tag_filter}」的"
                           "（先在表格里打标签）")
        raise gr.Error("至少勾选一个进球")
    return picked


_TAG_MAX = 24      # 文件名里标签段的截断长度（Windows 260 路径限制）


def _single_name(t: float, tag: str, vertical: bool) -> str:
    """单条成品名：`single-<竖|横>-<标签|未标>-01m23s4.mp4`。

    两个坑（2026-10-21 自查，都实测过）：
    ① 时刻行键 `time_key` 是 `01:23.4`——**冒号在 Windows 文件名里非法**，
       换成 `01m23s4`；
    ② 名字里**不能出现 `.`**：收尾都是
       `safe_filename(os.path.splitext(out_name)[0]) + ".mp4"`
       （`build_vertical:847` / `build_highlight:138`），带点会被当成扩展名
       切掉（`01m23.4s` → `01m23`）⇒ 同一分钟内两个球撞名、后者静默覆盖
       前者。所以十分位拼在 `s` **后面**，不写小数点。
    """
    # 时刻换算**必须从"十分之一秒整数"倒推**，不能写 t % 1.0：
    # 12.1 % 1.0 == 0.09999999999999964，截断后十分位变成 0（实测踩过）。
    ds = int(round(t * 10))
    mm, ss, tenth = ds // 600, (ds // 10) % 60, ds % 10
    tag_part = editor.safe_filename((tag or "").strip() or "未标")[:_TAG_MAX]
    return (f"single-{'竖' if vertical else '横'}-{tag_part}-"
            f"{mm:02d}m{ss:02d}s{tenth}.mp4")


def generate_singles(video_path: str, rows, items, vertical=True,
                     on_progress=None):
    """[单条模式] 勾选项命中的**每个进球各出一条独立成品**（生成器）。

    与集锦的区别：集锦是「每项一条」，这里是「每球一条」——一次勾 30 个球
    直接出 30 个文件，逐条发平台时不用反复勾 1 球点 30 次。

    去重：跨勾选项按进球时刻去重（同时勾「合集」+「张三」时，张三的球只出
    一次）。集锦模式**不**去重——那里"合集 + 单人集锦"本来就是两个不同的
    视频，去重反而是错的。

    勾选项三类（2026-10-23 起）：**「★ 精选」= 只出打星行**（`starred_only`）、
    「合集」= 全部勾选行、其余 = 标签名。精选与其它项可同时勾，仍按时刻去重。

    渲染：逐球调用现有 `build_vertical` / `build_highlight`（各传 1 个
    goal），**不重构渲染核心**。实测（2026-10-21，1080p/7548s 源）每球
    总耗时 ~15.0s，其中重复打开视频 + 读 events/calibration/shooters 的额外
    开销 ~0.3~0.8s——用这点开销换"渲染路径零改动"是划算的。

    单球失败只跳过并汇报，绝不让整批失败，也绝不裸抛（裸抛会穿透
    gen_selected 的 `except gr.Error` 把生成器杀死、按钮永久卡灰；横屏路径
    今天就有这个洞）。

    yield 进度文案；`on_progress(k, n)` 供接口层推百分比（**不从文案里反解
    k/N**，那会变成第二份隐式格式契约）；末次 StopIteration.value =
    (outs, skipped)，outs = [(item, path)]，item 形如「张三 01:23.4」。
    """
    sel = [s for s in (items or []) if str(s).strip()]
    video_path = clean_path(video_path)
    work = _work_dir_for(video_path)
    skipped, merged, seen = [], [], set()
    for item in sel:
        if _is_star_item(item):
            # 「★ 精选」只出打星的球（2026-10-23）；精选为空时 _picked_rows
            # 抛 gr.Error 给准确文案（打星提示），不会静默回退到"全部勾选"。
            tag, starred_only = "", True
        else:
            tag, starred_only = ("" if _is_all_item(item) else item), False
        try:
            picked = _picked_rows(rows, tag, starred_only=starred_only)
        except gr.Error as e:
            skipped.append(f"· {item}：{e}（跳过）")
            continue
        for p in picked:
            key = round(float(p[0]), 2)      # 两位小数足够区分两个进球
            if key in seen:
                continue
            seen.add(key)
            merged.append(p)
    # 行标签反查：item 展示成「标签 时刻」，发布时不用开表格认人
    tag_of = {}
    for row in (rows or []):
        try:
            tag_of.setdefault(round(_parse_t(row[1]), 2), (row[6] or "").strip())
        except (IndexError, TypeError, ValueError):
            continue
    duration = None
    if not vertical:
        # 横屏要自己夹上界：build_highlight 内部不 clamp，clamp 在 plan_clips 里
        duration = load_events(os.path.join(work, "events.json"))["duration"]
    outs, n = [], len(merged)
    for k, (t, before, after, _status) in enumerate(merged, 1):
        tag = tag_of.get(round(float(t), 2), "")
        item = f"{tag or '未标'} {_fmt_t(t)}"
        yield f"⏳ 正在生成单条（{k}/{n}）：{item}…"
        if on_progress:
            on_progress(k, n)
        name = _single_name(t, tag, vertical)
        try:
            if vertical:
                p = vrender.build_vertical(video_path, [(t, before, after)],
                                           work, name)
            else:
                p = editor.build_highlight(
                    video_path,
                    [(max(0.0, t - before), min(duration, t + after))],
                    work, name)
        except Exception as ex:              # noqa: BLE001——单球绝不拖垮整批
            skipped.append(f"· {item}：{ex}（跳过）")
            continue
        outs.append((item, p))
    return outs, skipped


def _hook_reel_name(idx: int, vertical: bool = False) -> str:
    """25s 精选成片名：`hook4-横-01.mp4` / `hook4-竖-01.mp4`。

    **只用两位序号、不带时刻**：一条成片对应 3 个时刻，拿任何一个时刻命名
    都会让人以为是「那一球的短片」。重跑覆盖同名（与 `highlight-横` 同款
    行为）——用户要的就是"再出一版看看"，不想每次多一个 hook4-横-01(1).mp4。
    带画幅是因为横竖两种都能出（2026-11-01 起），同名会互相覆盖。
    """
    return f"hook4-{'竖' if vertical else '横'}-{idx:02d}.mp4"


def generate_hook_reels(video_path: str, rows, items, on_progress=None,
                        vertical: bool = False):
    """[25s 精选模式] 勾选项命中的球池 → 每 3 球一条「4s 钩子 + 7+7+7」。

    与另外两个模式的硬差别：既不是「一个球一个文件」（single），也不是
    「一个勾选项一条」（reel），而是**把球池按时刻顺序每 3 球切一块**、每块
    重排成一条 25s 的抖音向成片。勾 24 球 ⇒ 8 条，每条都能独立发。

    为什么 3 个一组：25 秒是完播率的甜点区（2026-10-31 调研口径 15~30s、
    2026 版收窄到 21~34s），球再多单球占比就被稀释；而 3 球恰好是「4 秒钩子
    里每球 1.33s」还能看清「起跳→穿网」的下限。

    每个球先扫筐口橙像素拿**真实穿网时刻**（`repack.find_hit_time`），拿不到
    （多机位段筐口对不上 / 球太小）就退回 `event_t − 0.17s` 并在结果里标注
    ——钩子剪到空镜头上必须能从清单上看出是哪一球用的兜底。

    空池 / 单条失败只跳过并汇报（同 `generate_singles`）：生成器里裸抛会穿透
    `gen_selected` 的 except、把按钮永久卡灰。进度按「球数 + 条数」推进——
    扫描每球一拍、编码每条一拍。

    **横竖两条实现（2026-11-01）**，切段结构完全一样，只是渲染路不同：

        横版 `repack.build_hook_reel`：ffmpeg 从源片直切，一次编码，快。
        竖版 `repack.build_hook_reel_vertical`：每球先走 vrender 逐帧渲染
             7s（跟球运镜），再裁钩子 + 拼正片。**全程常速不慢放**，勾 24
             球会明显比横版慢——运镜的固有成本。

    yield 进度文案；末次 StopIteration.value = (outs, skipped)。
    """
    sel = [s for s in (items or []) if str(s).strip()]
    video_path = clean_path(video_path)
    if not video_path or not os.path.exists(video_path):
        raise gr.Error(f"视频不存在：{video_path}")
    work = _work_dir_for(video_path)
    skipped, merged, seen = [], [], set()
    for item in sel:
        if _is_star_item(item):
            # 「★ 精选」只取打星行（与另外两个模式同口径：精选 ⊆ 勾选）
            tag, starred_only = "", True
        else:
            tag, starred_only = ("" if _is_all_item(item) else item), False
        try:
            picked = _picked_rows(rows, tag, starred_only=starred_only)
        except gr.Error as e:
            skipped.append(f"· {item}：{e}（跳过）")
            continue
        for p in picked:
            key = round(float(p[0]), 2)
            if key in seen:
                continue
            seen.add(key)
            merged.append(p)
    if not merged:
        raise gr.Error("至少勾选一个进球")
    # 跨勾选项合并后必须重排：按项迭代是「先张三全部、再李四全部」，
    # 不排序的球池会把一个人的球全塞进前几条，另一条里一个都没有。
    merged.sort(key=lambda p: float(p[0]))
    # 行标签反查（结果清单里要能认出是谁的球）
    tag_of = {}
    for row in (rows or []):
        try:
            tag_of.setdefault(round(_parse_t(row[1]), 2), (row[6] or "").strip())
        except (IndexError, TypeError, ValueError):
            continue

    groups = [merged[i:i + repack.BALLS_PER_REEL]
              for i in range(0, len(merged), repack.BALLS_PER_REEL)]
    total = len(merged) + len(groups)
    done, outs = 0, []
    for gi, group in enumerate(groups, 1):
        hits, fallback = [], 0
        for t, _before, _after, _status in group:
            label = f"{tag_of.get(round(float(t), 2), '') or '未标'} {_fmt_t(t)}"
            # 文案里的分母是**球数**不是总步数：百分比按「球数+条数」推进，
            # 但"正在定位第 5/8 个球"这种话会让人以为漏了两个球。
            yield f"⏳ 正在定位进球（{done + 1}/{len(merged)}）：{label}…"
            done += 1
            if on_progress:
                on_progress(done, total)
            hit = None
            seg = repack.segment_calib(work, float(t))
            if seg is not None:
                hit, _info = repack.find_hit_time(
                    video_path, seg.calib.hoop_rect, seg.calib.hsv_low,
                    seg.calib.hsv_high, float(t))
            if hit is None:
                # 兜底：events.t 比真实穿网晚 0.17s（2026-10-30 全场 24 条实测）
                hit = max(0.0, float(t) - repack.FALLBACK_HIT_LAG)
                fallback += 1
            hits.append(hit)
        item = (f"25s精选 {gi:02d}｜"
                + " ＋ ".join(_fmt_t(float(t)) for t, *_rest in group))
        if fallback:
            item += f"（{fallback} 球用默认偏移）"
        yield (f"⏳ 正在{'渲染' if vertical else '编码'}第 {gi}/{len(groups)} 条"
               f"（{len(hits)} 球）…")
        try:
            name = _hook_reel_name(gi, vertical)
            p = (repack.build_hook_reel_vertical(video_path, hits, work, name)
                 if vertical else
                 repack.build_hook_reel(video_path, hits, work, name))
        except Exception as ex:          # noqa: BLE001——单条绝不拖垮整批
            skipped.append(f"· {item}：{ex}（跳过）")
            done += 1
            if on_progress:
                on_progress(done, total)
            continue
        done += 1
        if on_progress:
            on_progress(done, total)
        outs.append((item, p))
    return outs, skipped


def gen_selected(video_path: str, rows, sel, vertical=True, output="reel",
                 on_progress=None):
    """生成弹窗确定 → 两种产出模式：

    * `output="reel"`（默认，= 旧行为）：**每个勾选项一条**——合集=全部勾选
      行；标签=勾选且带该标签的行；「★ 精选」= 勾选且打星的行（2026-10-24，
      输出 `highlight-<竖|横>-精选.mp4`）。文件名 `highlight-<竖|横>[-<标签>].mp4`。
    * `output="single"`（2026-10-21 新增）：**每个进球一条**独立成品，文件名
      `single-<竖|横>-<标签>-01m23s4.mp4`，跨勾选项按时刻去重。见
      `generate_singles`。
    * `output="hook4"`（2026-11-01 新增）：**每 3 球一条「25s 精选」**——
      4s 钩子（三球穿网各 1.33s）+ 7+7+7 正片。球池同样跨勾选项去重、按
      时刻排序后切块。文件名 `hook4-<竖|横>-01.mp4`。见
      `generate_hook_reels`。**横竖都支持**（2026-11-01 起）：横版 ffmpeg
      直切（8 Mbps），竖版走 vrender 跟球运镜成片（**不慢放**，crf 19）。

    某项无勾选进球时跳过并在结果里汇报，不挡其他项。预览位播放第一个成品。
    生成器：开工先 yield「⏳ …」并收弹窗、灰按钮；末次 yield = (成品播放,
    预览占位, 回到逐条按钮, 弹窗收起, 结果清单, 按钮恢复)。全部跳过也走
    末次 yield 给 ❌ 文案（不 raise——生成器 raise 后不再有产出，按钮永久卡灰）。

    ⚠ 结果清单的行格式 `· {item} → {basename}` 是**隐式契约**：
    `bbh/web/api_gen.py:_gen_result` 按它反解 (item, 文件名) 再判存在性。
    改格式（哪怕把 ` → ` 换成 `：`）会让界面"生成成功、清单为空"。
    """
    sel = [s for s in (sel or []) if str(s).strip()]
    if not sel:
        raise gr.Error("至少勾选一项（视频合集或任一标签）")
    outs, skipped = [], []
    mode = str(output or "")
    if mode in ("single", "hook4"):
        if not any(r[0] for r in (rows or [])):
            raise gr.Error("至少勾选一个进球")
        # single 与 hook4 都是「球级」产出（一球一文件 / 三球一条），都要逐球
        # 转发进度并把汇总从 StopIteration.value 里取；reel 是「项级」产出，
        # 走下面的老循环。两条分支的 yield 形状必须完全一致（6 项）。
        gen = (generate_singles(video_path, rows, sel, vertical=vertical,
                                on_progress=on_progress)
               if mode == "single" else
               generate_hook_reels(video_path, rows, sel,
                                   on_progress=on_progress, vertical=vertical))
        while True:      # 生成器无法预知结尾：进度逐条转发，结尾取汇总
            try:
                msg = next(gen)
            except StopIteration as st:
                outs, skipped = st.value or ([], [])
                break
            yield (gr.update(), gr.update(), gr.update(visible=False),
                   gr.update(visible=False), msg,
                   gr.update(interactive=False))
    else:
        for k, item in enumerate(sel, 1):
            yield (gr.update(), gr.update(), gr.update(visible=False),
                   gr.update(visible=False),
                   f"⏳ 正在生成（{k}/{len(sel)}）：{item}…",
                   gr.update(interactive=False))
            # 分类判据必须是 `_is_all_item` / `_is_star_item`，**不许裸比较**：
            # 这两类项的文案随输出模式变（合集两串、精选两串），漏认任何一个
            # 都会被当标签名去筛 ⇒ 0 行 ⇒ 静默不出片（合集项踩过，精选同理）。
            starred_only = _is_star_item(item)
            tag = "" if (_is_all_item(item) or starred_only) else item
            try:
                out = generate_highlight(video_path, rows, tag, vertical=vertical,
                                         starred_only=starred_only)
            except gr.Error as e:
                skipped.append(f"· {item}：{e}（跳过）")
                continue
            outs.append((item, out))
    first = outs[0][1] if outs else None
    if not outs:
        yield (gr.update(), gr.update(), gr.update(visible=False),
               gr.update(visible=False),
               "❌ 没有生成任何视频——" + "；".join(
                   s.lstrip("· ") for s in skipped),
               gr.update(interactive=True))
        return
    single = str(output or "") == "single"
    lines = [build_goal_stats(video_path, rows), "",
             f"✅ 已生成 {len(outs)} 个{'单条视频' if single else '视频'}"
             "（预览位播放第一个，全部在 work/<视频名>/ 下）："]
    lines += [f"· {item} → {os.path.basename(p)}" for item, p in outs]
    lines += skipped
    placeholder = _hint_html("✅ 集锦已生成，正在播放。点表格任意一行或"
                             "「↩ 回到逐条预览」继续复核")
    yield (first, placeholder, gr.update(visible=True),
           gr.update(visible=False), "\n".join(lines),
           gr.update(interactive=True))


# ---------- 🧍 出手人识别（Phase 1：溯源+缩略图；聚类分组是 Phase 2） ----------
def run_shooter_tracing(video_path: str):
    """「🧍 识别出手人」→ 逐球回扫溯源 + 裁射手缩略图（生成器进度，同生成
    集锦模式）。已缓存的球自动跳过（增量续跑）；结果进 work/<视频>/
    shooters.json，重开页面秒载不重跑。溯源完自动接外观特征提取
    （Phase 2 修订：特征就绪供归人面板颜色提示用；聚类已下线不调用）。
    对应 [shooter_md, btn_shooter]：进行中禁用按钮，结束（含空跑）恢复。"""
    video_path = clean_path(video_path)
    if not video_path or not os.path.exists(video_path):
        raise gr.Error(f"视频不存在: {video_path}")
    if not _calib_ready(video_path):
        raise gr.Error("本视频还没标定——先去「⚙ 标定设置」完成一次")
    data = _load_events_or_none(video_path)
    if not data or not data.get("events"):
        raise gr.Error("尚无检测结果——先点「▶ 开始检测」")
    segs = load_segments(video_path)
    if not segs:
        raise gr.Error("标定文件损坏，请重新标定")
    # 已删球不回扫（未删视图喂引擎——删掉的随意投篮不必溯源，墙也不显示）
    gen = shooter.trace_video(video_path, segs, _work_dir_for(video_path),
                              _filter_deleted(data, video_path)["events"])
    try:
        while True:    # 生成器无法预知结尾：进度逐条转发（禁用按钮），结尾取汇总
            try:
                msg = next(gen)
            except StopIteration as st:
                summary = st.value or "✅ 完成"
                break
            yield msg, gr.update(interactive=False)
    except Exception as ex:    # 兜底：溯源中途异常不许把按钮卡死（已扫的球已逐球落盘，重点可续跑）
        summary = f"⚠️ 识别中断：{ex}（已完成的球已存盘，重新点击可续跑）"
    # Phase 2（修订版）：溯源完成 → 外观特征提取（颜色提示用；聚类已下线）
    traces_cached = shooter.load_shooters(_work_dir_for(video_path))
    if traces_cached and traces_cached.get("traces"):
        fgen = appearance.build_features(video_path, segs,
                                         _work_dir_for(video_path),
                                         traces_cached["traces"])
        try:
            while True:
                try:
                    yield next(fgen), gr.update(interactive=False)
                except StopIteration as st:
                    fmsg = st.value or ""
                    break
        except Exception as ex:
            fmsg = f"⚠️ 外观提取中断：{ex}（重跑可续）"
        summary = summary + f"\n{fmsg}\n🧍 特征就绪——下方表格勾球归人"
    yield summary, gr.update(interactive=True)


def _shooter_block_html(video_path, rows, i) -> str:
    """行面板「出手人」块：两帧射手缩略图（帧 _a 带出手点红十字）+ 三档
    小标 + 出手时刻。未识别过 → 引导提示；该球未溯源 → ⚪ 说明。
    只展示不写标签——打标签仍走标签下拉（看图打标比回看视频快，
    Phase 1 不自动写标签）。内容全部自建（无用户输入），路径经 quote。"""
    try:
        i = int(i)
    except (TypeError, ValueError):
        return ""
    if not (0 <= i < len(rows or [])):
        return ""
    video_path = clean_path(video_path)
    if not video_path:
        return ""
    keys = _raw_time_keys(video_path, rows)
    key = keys[i] if i < len(keys) else None
    data = shooter.load_shooters(_work_dir_for(video_path))
    if not (data and data.get("traces")):
        return ('<div style="font-size:12px; color:#888; margin-top:6px">'
                '🧍 出手人：未识别——点左栏「🧍 识别出手人」批量生成射手'
                '缩略图后，看图打标不用回看视频</div>')
    tr = data["traces"].get(key or "")
    if not tr:
        return ('<div style="font-size:12px; color:#888; margin-top:6px">'
                '🧍 出手人：⚪ 该球未溯源（轨迹被挡/出手点在画面外）——'
                '需回看视频打标</div>')
    dot = shooter.STATUS_TEXT.get(tr.get("status"), "⚪")
    imgs = ""
    work = _work_dir_for(video_path)
    gp = _gif_or_none(work, key)
    if gp:
        # 动图（出手前 0.7s→出手后 0.4s，全程红十字）已覆盖 _a/_b 两帧语义
        imgs = (f'<img src="{_file_url(gp)}" style="max-height:170px; '
                'border-radius:6px; margin:4px 4px 0 0; '
                'border:1px solid #e5e7eb">')
    else:
        for rel in tr.get("thumbs") or []:
            p = os.path.join(work, rel)
            if os.path.exists(p):
                imgs += (f'<img src="{_file_url(p)}" style="max-height:170px; '
                         'border-radius:6px; margin:4px 4px 0 0; '
                         'border:1px solid #e5e7eb">')
    rt = tr.get("release_t")
    rel_s = f"出手 {rt:.1f}s 时刻 · " if isinstance(rt, (int, float)) else ""
    return (f'<div style="margin-top:6px; font-size:12.5px">'
            f'<b>🧍 出手人 {dot}</b>（{rel_s}判定看图打标签）<br>{imgs}</div>')


# ---------- 🧍 手动归人（Phase 2 修订：一等公民，spec 第二轮修订） ----------
# 2026-09-06 用户定案：表格勾选（r[0]）=「人工核对确实是进球」标记，
# 判断是否进球和出手人无关——归人的球来源只有墙上点选（extra_keys），
# 不并入勾选、不清勾选、不写 selection.json（曾因并集把 14 个核对勾选
# 连同老王 2 球一并抢给阿林改判）。


def _gif_or_none(work, key):
    """该球动图绝对路径（未生成为 None）——墙/面板「优先动图、缺了退静态」
    共用（生成在 shooter._gif_backfill，文件存在性即状态）。"""
    if not key:
        return None
    p = os.path.join(work, shooter.gif_rel(key))
    return p if os.path.exists(p) else None


def _file_url(p: str) -> str:
    """HTML <img> 用的 work 白名单路由 URL（mtime 击穿浏览器缓存）。"""
    return ("/gradio_api/file=" + urllib.parse.quote(
        os.path.abspath(p).replace("\\", "/"), safe=":")
        + f"?v={os.path.getmtime(p):.0f}")


def _thumb_img(work, traces, key):
    """该球第一张缩略图 HTML（优先动图；无则空串）。URL 款式同 _shooter_block_html。"""
    gp = _gif_or_none(work, key)
    if gp:
        return (f'<img src="{_file_url(gp)}" style="max-height:110px;'
                ' border-radius:4px">')
    for rel in (traces.get(key) or {}).get("thumbs") or []:
        p = os.path.join(work, rel)
        if os.path.exists(p):
            return (f'<img src="{_file_url(p)}" style="max-height:110px;'
                    ' border-radius:4px">')
    return ""


def _tag_choices(video_path):
    """两页人名下拉的唯一数据源（2026-09-06 对齐修）：标签库（tags.json，
    全局）∪ 本视频已归人人名（manual_groups——归人时输入的新名没进
    全局库，剪辑台也要能选到），sorted 码点序（两页同序，并列稳定）。"""
    groups = manual.load(_work_dir_for(video_path))
    return sorted(set(_load_tags()) | set(groups))


def render_manual_payload(video_path, rows, sel=None):
    """归人面板：人→球缩略图墙 + 选中球的颜色相近提示（恒 low）+
    人名下拉 choices（标签库 ∪ 已归人人名）。sel=当前墙上点选的球
    （提示跟随点选；rows 仅保接口兼容——勾选=核对标记已不作提示源）。"""
    video_path = clean_path(video_path)
    work = _work_dir_for(video_path)
    data = shooter.load_shooters(work)
    traces = (data or {}).get("traces") or {}
    groups = manual.load(work)
    parts = ["<div style='font-size:12.5px'><b>🧍 手动归人</b>——"
             "墙上点球 → 选/输入人名 → 归人；已归人球在下方调整墙"
             "可二次改判/移出</div>"]
    if not groups:
        parts.append("<div style='color:#888'>还没有归人记录——墙上点球归人后"
                     "这里按人显示</div>")
    for person in sorted(groups):
        imgs = "".join(_thumb_img(work, traces, k) for k in groups[person])
        # 一人一块、块内横排（gradio 全局把 img 设成 block，竖排叠到
        # 天上去——flex 行包裹住才是"一人一行"的可读形态）
        parts.append(f'<div style="margin:6px 0; padding:6px; border:1px solid '
                     f'#e5e7eb; border-radius:8px"><b>{html.escape(person)}</b> '
                     f'{len(groups[person])}球'
                     f'<div style="display:flex; flex-wrap:wrap; gap:4px; '
                     f'margin-top:4px">{imgs}</div></div>')
    sel = [k for k in (sel or []) if isinstance(k, str)]
    if sel:
        fd = appearance.load_features(work)
        # 过期 features 不当真相用（终审修复 5）：换视频/重标定/特征算法
        # 升版后，旧缓存的颜色相近提示会把人带偏。段列表读不到（空）也
        # 弃用——无标定指纹可比对本就无从判有效（A2 起身份=段组合指纹）。
        segs = load_segments(video_path)
        if fd and segs and appearance.features_valid(fd, video_path, segs):
            feats = {k: appearance.Appearance.from_dict(v)
                     for k, v in (fd.get("feats") or {}).items()}
            nbrs = suggest.color_neighbors(sel[0], feats, groups, k=5)
            if nbrs:
                tips = "、".join(f"{k}（{s:.2f}）" for k, s in nbrs)
                parts.append(f"<div style='color:#888; margin-top:6px'>"
                             f"🔎 与 {sel[0]} 颜色相近（恒 low，仅供参考）："
                             f"{tips}</div>")
    choices = _tag_choices(video_path)   # 唯一源（含归人名 union，见其 docstring）
    return "".join(parts), choices


def _manual_payload_ui(vp, rows):
    """归人面板的布线层封皮（终审修复 1）：person_dd 是 gr.Dropdown，
    render_manual_payload 的裸 choices list 会被 postprocess 成 **value**
    而非 choices（gradio 5.50 实测）——标签库选人失效，还会把 list 毒化进
    输入框使归人 AttributeError。转换只在布线层做：核心函数保持返回纯
    list（assign/unassign 的 *展开零改动，单测直调不碰 gr.update）。"""
    html, choices = render_manual_payload(vp, rows)
    return html, gr.update(choices=choices)


# ---------- 🧍 未归人缩略图墙（Phase 2 补充：排序不定身份，判断是用户） ----
_WALL_MODES = {"时间": "time", "外观相似": "appearance", "出手点": "spot"}


def wall_payload(video_path, mode_label="时间"):
    """未归人墙数据 [(缩略图绝对路径, caption, key), ...]——排序由
    suggest.wall_order 定（引擎纯函数），缩略图文件存在性在此过滤。
    caption=展示（spot=bad 定位失效球追加 ⚠，key 恒干净——点选/归人
    用第三位，防字符串拼装饰毒化 key 的布线事故复发）。外观模式需
    特征缓存身份相符（同颜色提示口径，终审修复 5：过期特征会把人
    带偏）；不可用则引擎自动退化时间序。"""
    video_path = clean_path(video_path)
    work = _work_dir_for(video_path)
    traces = (shooter.load_shooters(work) or {}).get("traces") or {}
    deleted = set(_load_deleted(video_path))
    if deleted:      # 已删球不上墙（剪辑台删除级联出手人侧）
        traces = {k: v for k, v in traces.items() if k not in deleted}
    groups = manual.load(work)
    feats = {}
    fd = appearance.load_features(work)
    segs = load_segments(video_path)
    if fd and segs and appearance.features_valid(fd, video_path, segs):
        feats = {k: appearance.Appearance.from_dict(v)
                 for k, v in (fd.get("feats") or {}).items()}
    out = []
    for key in suggest.wall_order(feats, traces, groups,
                                  _WALL_MODES.get(mode_label, "time")):
        cap = key + ("·⚠定位失效" if (traces[key].get("spot") == "bad")
                     else "")
        gp = _gif_or_none(work, key)          # 优先动图（spot=bad 球无 gif
        if gp:                                # 天然退回全场静态图）
            out.append((os.path.abspath(gp), cap, key))
            continue
        for rel in (traces[key].get("thumbs") or []):
            p = os.path.join(work, rel)
            if os.path.exists(p):
                out.append((os.path.abspath(p), cap, key))
                break
    return out


def assigned_wall_payload(video_path):
    """已归人调整墙数据 [(缩略图绝对路径, caption, key), ...]——手动归人
    结果模块的二次调整入口（多选改判/移出，2026-09-06 用户问题 3）。
    人名序+组内解析时刻序（_goal_t，防 110:xx 跨 100 分钟字典序坑）；
    caption="人名·key"（spot=bad 追加 ⚠），key 第三位恒干净同未归人墙。
    无缩略图/文件缺失的已归人球不上墙（表格标签列仍可改判它们）。"""
    video_path = clean_path(video_path)
    work = _work_dir_for(video_path)
    traces = (shooter.load_shooters(work) or {}).get("traces") or {}
    deleted = set(_load_deleted(video_path))
    if deleted:      # 已删球不上调整墙（同未归人墙的级联口径）
        traces = {k: v for k, v in traces.items() if k not in deleted}
    groups = manual.load(work)
    out = []
    for person in sorted(groups):
        for key in sorted(groups[person], key=appearance._goal_t):
            tr = traces.get(key) or {}
            cap = (f"{person}·{key}"
                   + ("·⚠定位失效" if tr.get("spot") == "bad" else ""))
            gp = _gif_or_none(work, key)
            if gp:
                out.append((os.path.abspath(gp), cap, key))
                continue
            for rel in tr.get("thumbs") or []:
                p = os.path.join(work, rel)
                if os.path.exists(p):
                    out.append((os.path.abspath(p), cap, key))
                    break
    return out


def _gallery_pairs(items, req=None):
    """Gallery value 构造 [(url_or_path, caption), ...]（两面墙共用）。

    Gallery 的 value 走服务端文件处理，两条路都踩过坑：
    - 相对 URL "/gradio_api/file=..."（HTML <img> 的合法款）会被当
      本地路径抛 InvalidPathError；
    - 本机绝对路径能过，但 gradio 逐张拷进自己缓存，434 张一次渲染
      90s+，每次归人/换排序都要重来。
    正解 = http URL：gradio 对 http 直通不碰文件，浏览器从 work
    白名单路由直取还能缓存。请求源用 gr.Request 注入（布线层透传），
    req 为空（headless/测试）退回绝对路径——能用但慢，仅兜底。"""
    base = None
    if req is not None and getattr(req, "request", None) is not None:
        base = str(req.request.base_url).rstrip("/")
    if base:
        return [(f"{base}/gradio_api/file="
                 + urllib.parse.quote(p.replace(os.sep, "/"), safe=":")
                 + f"?v={os.path.getmtime(p):.0f}", cap)
                for p, cap, k in items]
    return [(p, cap) for p, cap, k in items]


def _wall_render(video_path, mode_label="时间", sel=None, req=None):
    """墙渲染：Gallery gr.update + 当前墙序（点击索引→key 换算用）+
    过滤后的选中集（已归人/已离墙的 key 自动失效）+ 已选清单 HTML。"""
    items = wall_payload(video_path, mode_label)
    order = [k for *_, k in items]
    sel = [k for k in (sel or []) if k in order]
    upd = gr.update(
        value=_gallery_pairs(items, req),
        label=f"未归人 {len(order)} 球——缩略图=出手前后动图；点击=选中/取消"
              "（上方清单实时更新，选中后点「✅ 归人」"
              "；⚠定位失效=出手点不可信给的全场图（不是报错），点右上⛶"
              "全屏放大看人物")
    return upd, order, sel, _wall_sel_html(sel)


def _adj_wall_render(video_path, req=None):
    """已归人调整墙渲染：Gallery gr.update + 墙序（点击索引→key 换算）。
    选中集合与未归人墙共用同一个 State（wall_sel）——已选清单/归人/移出
    按钮同一套；归人=改判（assign 自动从旧人移出），移出=unassign。"""
    items = assigned_wall_payload(video_path)
    upd = gr.update(
        value=_gallery_pairs(items, req),
        label=f"已归人 {len(items)} 球——点选后可改判（归人到新人=自动移出"
              "旧人）或移出；图注=人名·时刻；⚠定位失效=全场图（点右上⛶放大）")
    return upd, [k for *_, k in items]


def _wall_sel_html(sel):
    """已选清单（选中反馈的唯一真源，Gallery 上不标记）。"""
    if not sel:
        return ("<div style='color:#888; font-size:12.5px'>"
                "墙上尚未点选——点缩略图把认出的同人多球加入已选</div>")
    return ("<div style='font-size:12.5px'><b>已选 "
            f"{len(sel)} 球</b>：{'、'.join(sel)}</div>")


def _wall_toggle(order, sel, evt: gr.SelectData):
    """点击墙缩略图=切换该球选中。evt.index→key 用当前墙序换算；
    只更新清单与 State，Gallery 不动（保滚动位置）。State 从浏览器裸
    往返无类型保证——非 list（布线事故会进 HTML 串）整体丢弃，防
    list('...') 散成单字符 key 毒化归人。"""
    i = evt.index if isinstance(evt.index, int) else (evt.index or [None])[0]
    key = order[i] if isinstance(i, int) and 0 <= i < len(order or []) else None
    sel = list(sel) if isinstance(sel, list) else []
    sel2 = sel if key is None else (
        [k for k in sel if k != key] if key in sel else sel + [key])
    return _wall_sel_html(sel2), sel2


def assign_person(video_path, rows, person, extra_keys=None):
    """墙上点选的球归人（改判=自动从别人移出）+ 双写 tag_assignments +
    MIN_GOAL_GAP 护栏警示（警示不阻断，手动永远赢）。球来源 = 墙上点选
    （extra_keys，未归人墙/已归人调整墙多选，去重）——表格勾选是
    「人工核对确认进球」标记（2026-09-06 用户定案：是否进球和出手人
    无关），不并入也不清空。
    返回 (msg, rows, 面板HTML, 人名choices)——表格/当前行面板刷新由布线层
    封皮补齐（row_tag_dd 同款）。"""
    sel = [k for k in dict.fromkeys(list(extra_keys or []))
           if isinstance(k, str) and k]
    if not sel:
        raise gr.Error("先在墙上点选要归人的球（表格勾选=核对标记，不参与归人）")
    person = (person or "").strip()
    if not person:
        raise gr.Error("选或输入一个人名")
    video_path = clean_path(video_path)
    manual.assign(_work_dir_for(video_path), person, sel)
    rows = [list(r) for r in (rows or [])]      # copy-on-write（set_row_tag 惯例）
    keys = _raw_time_keys(video_path, rows)
    for i, r in enumerate(rows):
        if i < len(keys) and keys[i] in sel:
            r[6] = person                       # 勾选 r[0] 不动（核对标记）
    _save_assignments(video_path, rows)
    # 护栏查**归人后该人的整个组**（终审修复 2），不只查本次点选——单球
    # 增量（已有 01:00.0 再单点 01:02.0）的违例跨在旧组与新球之间，
    # 只查 sel 的实现永不触发。
    gaps = appearance.gap_violations(
        manual.load(_work_dir_for(video_path)).get(person, []))
    warn = (f"\n⚠️ 护栏：{' / '.join(f'{a}↔{b} 仅 {g}s' for a, b, g in gaps)}"
            f" < {appearance.MIN_GOAL_GAP_S}s，物理上不太可能同人，请核对"
            ) if gaps else ""
    return f"✅ {len(sel)} 球 → {person}{warn}", rows, \
        *render_manual_payload(video_path, rows)


def unassign_person(video_path, rows, extra_keys=None):
    """墙上点选的球移出所属人——清 manual_groups **并清掉这些行的标签**，
    勾选（核对标记）不动。球来源同 assign_person：墙上点选。

    标签必须跟着清（2026-10-11 用户裁定「移出时同时清标签」）：归人是
    **双写**（assign_person 既进 manual_groups 又写 tag_assignments），
    移出只做一半的话墙上说未归人、剪辑台还贴着人名；更要命的是**按人
    集锦是按标签导出的**（highlight-<竖|横>-<标签>.mp4 走 tag_assignments，不看
    manual_groups）⇒ 已移出的球照样会被收进那个人的集锦，两本账各说
    各话。只清点选的键，同表其余行的标签原样保留。"""
    sel = [k for k in dict.fromkeys(list(extra_keys or []))
           if isinstance(k, str) and k]
    if not sel:
        raise gr.Error("先在墙上点选要移出的球")
    video_path = clean_path(video_path)
    manual.unassign(_work_dir_for(video_path), sel)
    rows = [list(r) for r in (rows or [])]      # copy-on-write（set_row_tag 惯例）
    keys = _raw_time_keys(video_path, rows)
    sel_set = set(sel)
    for i, r in enumerate(rows):
        if i < len(keys) and keys[i] in sel_set and len(r) > 6:
            r[6] = ""                           # 勾选 r[0] 不动（核对标记）
    _save_assignments(video_path, rows)
    return (f"↩️ {len(sel)} 球已移出归人（标签一并清掉，按人集锦不再收它们）",
            rows, *render_manual_payload(video_path, rows))


def delete_events(video_path, rows, keys, display_offset=0.0):
    """「🗑 批量删除」核心：keys=原始时刻键集合（面板 CheckboxGroup 多选），
    一次踢出多行——随意投篮常成串，逐球点删太碎。语义与单删完全同款
    （sidecar 记键非破坏+级联出手人侧+勾选/标签按剩余行重存），一次落盘
    一屏刷。键按当前视图反查（_raw_time_keys）：混入的失配键（重跑检测/
    换视频后旧面板残留）被丢弃不炸——批量宁丢失配键不中断整批。
    msg 用显示时间（用户视角），存储用原始键。返回 (msg, rows2)。
    display_offset≠0 时 msg 里的时刻按它平移显示（web 端把会话校准偏移随请求体
    带上；公式与 api_gen._apply_session_rows 的平移逐字同义、含夹负）；存储与
    匹配仍用原始键。旧界面不传（它的 rows[1] 已烘偏移）⇒ 默认 0 行为不变。"""
    all_keys = _raw_time_keys(video_path, rows)
    del_set = {k for k in (keys or []) if isinstance(k, str)} & set(all_keys)
    if not del_set:
        raise gr.Error("先在面板勾选要删除的进球")
    video_path = clean_path(video_path)
    off = float(display_offset or 0.0)
    shown = {k: (r[1] if not off else
                 _fmt_t(max(0.0, _parse_t(r[1]) + off)))    # msg 显示时间（吃偏移）
             for k, r in zip(all_keys, rows or [])}
    rows2 = [list(r) for r, k in zip(rows or [], all_keys) if k not in del_set]
    deleted = _load_deleted(video_path)
    deleted.extend(k for k in all_keys if k in del_set and k not in deleted)
    _save_deleted(video_path, deleted)
    manual.unassign(_work_dir_for(video_path),
                    sorted(del_set, key=appearance._goal_t))   # 级联出手人侧
    if os.path.exists(os.path.join(_work_dir_for(video_path), "events.json")):
        _save_assignments(video_path, rows2)   # 未删视图下键对齐成立（见测试）
        _save_selection(video_path, rows2)
    names = "、".join(shown.get(k, k) for k in sorted(del_set,
                                                      key=appearance._goal_t))
    more = "…" if len(names) > 60 else ""
    return (f"🗑 已删除 {len(del_set)} 球（{names[:60]}{more}）——出手人侧"
            "同步移出；「↩ 恢复已删」可找回", rows2)


def delete_event(video_path, rows, cur_row):
    """「🗑 删除此球」（单行快捷：点行→删）：行号换算键后走 delete_events
    批量路径——单一实现防双路径漂移。语义见 delete_events docstring。"""
    if cur_row is None or not (0 <= int(cur_row) < len(rows or [])):
        raise gr.Error("先点表格选中一行，再删除")
    key = _raw_time_keys(video_path, rows)[int(cur_row)]
    return delete_events(video_path, rows, [key])


def bulk_delete_choices(video_path, rows):
    """批量删除面板 choices [(显示名, 键)]：显示名=显示时间｜档位色点｜
    标签（用户视角的时间，时刻校准偏移后的），value=原始时刻键（删除
    对齐口径）——两层时间各司其职，与单删同一不变量。表格空（未载入
    视频）返回 []，面板开也只是空清单。"""
    out = []
    for r, k in zip(rows or [], _raw_time_keys(video_path, rows)):
        lbl = f"{r[1]}｜{STATUS_DOT.get(str(r[2]), '·')}"
        tag = str(r[6] or "").strip()
        if tag:
            lbl += f"｜{tag}"
        out.append((lbl, k))
    return out


def restore_deleted(video_path):
    """「↩ 恢复已删」核心：清空 deleted.json，返回恢复的键列表（表格行
    由布线层 reload 重建）。恢复的球回到未勾选/未归人/无标签状态——
    删除时的清理不回滚（要进集锦/归人重新核对一遍即可）。"""
    video_path = clean_path(video_path)
    deleted = _load_deleted(video_path)
    if not deleted:
        raise gr.Error("没有已删除的进球——删除只记 sidecar，随时可恢复")
    _save_deleted(video_path, [])
    return deleted


# ---------- 「⚠ 缩略图有误」/「✚ 十字有误」标记（2026-09-06）：核对时
# 遇到的坏缩略图逐个标记，标注完所有视频后统一读 json 看图归因——四类
# 缺陷同款取证流程的样本采集入口。两类分采（用户定语义）：缩略图有误=
# 图片内容上未出现真实出手人（裁剪/画面问题）→ thumb_flags.json；十字
# 有误=球的十字标记（出手点红十字）位置错（溯源定位问题）→ cross_flags.json。
# 归因路径不同故独立 sidecar，一球可同时标两类。 ----------

def _load_flag_set(video_path: str, filename: str) -> list:
    """读误标清单（键=原始检测时刻）；缺失/损坏当空。两类 sidecar 共用。"""
    try:
        with open(os.path.join(_work_dir_for(video_path), filename),
                  encoding="utf-8") as f:
            data = json.load(f)
        return [k for k in (data if isinstance(data, list) else [])
                if isinstance(k, str)]
    except (OSError, ValueError):
        return []


def _save_flag_set(video_path: str, filename: str, keys) -> None:
    work = _work_dir_for(video_path)
    os.makedirs(work, exist_ok=True)
    with open(os.path.join(work, filename), "w", encoding="utf-8") as f:
        json.dump(list(keys), f, ensure_ascii=False, indent=1)


def _flag_list_md(video_path, filename, prefix) -> str:
    """已标记计数行（常显清单，标完即可交给分析）。prefix 形如
    「⚠ 已标记 {n} 张缩略图有误」，计数由本函数拼。"""
    flags = _load_flag_set(video_path, filename)
    if not flags:
        return ""
    names = "、".join(flags)
    more = "…" if len(names) > 80 else ""
    return f"{prefix.format(n=len(flags))}：{names[:80]}{more}"


def _thumb_flag_md(video_path) -> str:
    return _flag_list_md(video_path, THUMB_FLAG_FILE,
                         "⚠ 已标记 {n} 张缩略图有误")


def _cross_flag_md(video_path) -> str:
    return _flag_list_md(video_path, CROSS_FLAG_FILE,
                         "✚ 已标记 {n} 个十字有误")


def _toggle_mark_flag(video_path, rows, cur_row, filename,
                      labels, err, md_fn):
    """两类误标 toggle 的共用体：当前行的球进/出指定 sidecar。删除已标
    球不清理标记（样本要能对回原图），删错标记再点一次即消。
    labels=(未标文案, 已标文案)；返回 (按钮文案, 计数 md)。"""
    if cur_row is None or not (0 <= int(cur_row) < len(rows or [])):
        raise gr.Error(err)
    key = _raw_time_keys(video_path, rows)[int(cur_row)]
    video_path = clean_path(video_path)
    flags = _load_flag_set(video_path, filename)
    if key in flags:
        flags.remove(key)
        btn = labels[0]
    else:
        flags.append(key)
        btn = labels[1]
    _save_flag_set(video_path, filename, flags)
    return btn, md_fn(video_path)


def toggle_thumb_flag(video_path, rows, cur_row):
    """「⚠ 缩略图有误」toggle（图里未出现真实出手人）。"""
    return _toggle_mark_flag(video_path, rows, cur_row, THUMB_FLAG_FILE,
                             ("⚠ 缩略图有误", "✅ 已标记有误（点此取消）"),
                             "先点表格选中一行，再标记缩略图", _thumb_flag_md)


def toggle_cross_flag(video_path, rows, cur_row):
    """「✚ 十字有误」toggle（球的十字标记位置错）。与缩略图有误独立：
    一球可两类同标，各自 sidecar 互不干扰。"""
    return _toggle_mark_flag(video_path, rows, cur_row, CROSS_FLAG_FILE,
                             ("✚ 十字有误", "✅ 十字已标记（点此取消）"),
                             "先点表格选中一行，再标记十字", _cross_flag_md)


# ---------- 🧪 误判/漏检反馈（临时：调优进球判定用，调完可整体删除） ----------
def _feedback_path(video_path: str) -> str:
    """反馈文件：work/<视频名>/feedback.json（与 events.json 同目录）。"""
    return os.path.join(_work_dir_for(video_path), "feedback.json")


def _load_feedback(video_path: str) -> dict:
    """读 feedback.json；缺失/损坏一律当作空反馈，不抛。
    旧格式（只有 false_positives 误判标记，2026-08-19 裁决制之前）自动
    迁移为 verdicts（误判=not_goal 裁决），历史标注不丢。"""
    try:
        with open(_feedback_path(video_path), encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("not a dict")
        if "verdicts" in data:
            verdicts = list(data.get("verdicts") or [])
        else:   # 旧格式迁移
            verdicts = [dict(m, verdict="not_goal")
                        for m in (data.get("false_positives") or [])]
        return {"verdicts": verdicts, "misses": list(data.get("misses") or [])}
    except (OSError, ValueError):
        return {"verdicts": [], "misses": []}


def _save_feedback(video_path: str, fb: dict) -> None:
    os.makedirs(_work_dir_for(video_path), exist_ok=True)
    with open(_feedback_path(video_path), "w", encoding="utf-8") as f:
        json.dump(fb, f, ensure_ascii=False, indent=1)


def _fp_summary(video_path: str) -> str:
    fb = _load_feedback(video_path)
    n_goal = sum(1 for v in fb["verdicts"] if v.get("verdict") == "goal")
    n_no = len(fb["verdicts"]) - n_goal
    return (f"已裁决 **{n_goal}** 条是进球、**{n_no}** 条未进球、"
            f"补记 **{len(fb['misses'])}** 条漏检 → `feedback.json`")


def mark_verdict(video_path: str, rows, cur_row, verdict: str,
                 display_offset=0.0):
    """「✅ 这条是进球 / ❌ 这条未进球」：记录当前行的人工裁决（2026-08-19
    起低置信候选也在列表里，被门槛降级的真进球靠「是进球」翻案）。
    同一行再点同一按钮 = 取消裁决；点另一个按钮 = 改判（覆盖）。
    记录时快照该行 events.json 里的判定依据字段（含 low_reason），防之后
    重跑检测覆盖证据。verdict: "goal" | "not_goal"。对应 [fp_status]。
    display_offset≠0 时 msg 里的时刻按它平移显示；t_str（存储键/匹配容差）与 entry 仍原始——两层时间各司其职。"""
    if cur_row is None or not (0 <= int(cur_row) < len(rows or [])):
        raise gr.Error("先点表格选中一行，再标记")
    video_path = clean_path(video_path)
    i = int(cur_row)
    t_str = str(rows[i][1])
    t = _parse_t(t_str)
    off = float(display_offset or 0.0)
    t_disp = t_str if not off else _fmt_t(max(0.0, t + off))   # msg 显示值；t_str 仍原始
    fb = _load_feedback(video_path)
    hit = next((k for k, v in enumerate(fb["verdicts"])
                if abs(v.get("t", -1) - t) < 1e-6), None)
    label = "✅ 是进球" if verdict == "goal" else "❌ 未进球"
    if hit is not None and fb["verdicts"][hit].get("verdict") == verdict:
        del fb["verdicts"][hit]              # 再点同按钮 = 取消
        _save_feedback(video_path, fb)
        return f"↩ 已取消 {t_disp} 的裁决。{_fp_summary(video_path)}"
    data = _filter_deleted(_load_events_or_none(video_path), video_path)
    events = (data.get("events") or []) if data else []
    ev = events[i].to_dict() if 0 <= i < len(events) else {}   # 未删视图对齐行号
    entry = {"t": t, "t_str": t_str, "verdict": verdict,
             "marked_at": datetime.now().isoformat(timespec="seconds"),
             "evidence": {k: ev[k] for k in
                          ("confidence", "status", "low_reason", "cross_off",
                           "above_n", "below_n", "above_span", "below_span",
                           "descent_px", "below_big", "pop_back",
                           "net_post", "net_ctrl", "net_local")
                          if k in ev}}
    action = "已改判" if hit is not None else "已标记"
    if hit is not None:
        fb["verdicts"][hit] = entry
    else:
        fb["verdicts"].append(entry)
    _save_feedback(video_path, fb)
    return f"{label}：{t_disp}（第 {i + 1} 条）{action}。{_fp_summary(video_path)}"


def _parse_freeform_t(text: str) -> float:
    """漏检时刻输入：`mm:ss(.s)` 或纯秒数（如 12:34 / 12:34.5 / 756）。"""
    s = (text or "").strip().replace("：", ":")
    try:
        if ":" in s:
            m, sec = s.split(":", 1)
            return int(m) * 60 + float(sec)
        return float(s)
    except ValueError:
        raise gr.Error(f"看不懂时刻「{text}」——请填 mm:ss（如 12:34）"
                       "或纯秒数（如 756）")


def record_miss(video_path: str, text: str):
    """「➕ 记录漏检」：补记一条真进球但没检测到的时刻。
    对应 [fp_status, miss_box]（记录后清空输入框）。"""
    t = _parse_freeform_t(text)
    video_path = clean_path(video_path)
    fb = _load_feedback(video_path)
    fb["misses"].append({"t": t, "note": (text or "").strip(),
                         "marked_at": datetime.now().isoformat(
                             timespec="seconds")})
    _save_feedback(video_path, fb)
    return (f"✓ 已记录漏检 {int(t // 60):02d}:{t % 60:04.1f}（{t:.1f}s）。"
            f"{_fp_summary(video_path)}", "")


def generate_highlight(video_path: str, table, tag_filter: str = "",
                       vertical: bool = True, starred_only: bool = False):
    """按表格勾选、标签筛选与每行前后秒生成集锦。
    tag_filter 非空时只收该标签的进球，输出 highlight-<竖|横>-<标签>.mp4。
    vertical：True=竖屏运镜管线（vrender.build_vertical）；False=旧横屏
    直切（editor.build_highlight，字节不动）——页面横/竖两个入口按钮各走
    各的，互不影响。

    starred_only（2026-10-24）：只收**打星**的行——集锦模式的「★ 精选」
    那一项（单条模式走 `generate_singles`）。输出 highlight-<竖|横>-精选.mp4。

    文件名带画幅（2026-10-18 用户要求）：横竖都写同一个 work/<视频>/ 目录，
    旧口径同标签共用 `highlight-<标签>.mp4` → 后生成的直接覆盖前者。
    """
    video_path = clean_path(video_path)
    tag_filter = (tag_filter or "").strip()
    picked = _picked_rows(table, tag_filter, starred_only=starred_only)
    if vertical:
        # 爆点前置（用户 2026-09-17）：高置信球排前面、同级保持时间序
        # （stable sort 不抖）——前几秒的强内容拉抖音 3s/5s 完播率；
        # 只动竖屏，横屏旧路径顺序原样（字节不动的契约）
        rank = {"high": 0, "review": 1, "low": 2}
        picked.sort(key=lambda p: rank.get(p[3], 3))
    settings = [p[:3] for p in picked]
    orient = "竖" if vertical else "横"
    if starred_only:
        # **不能沿用 highlight-<竖|横>.mp4**：同一批里"视频合集 + ★ 精选"两项
        # 都勾时两个产物会互相覆盖（后跑的赢），结果是清单里两条记录指向同一个
        # 文件、内容还对不上。
        suffix = "精选"
    else:
        suffix = editor.safe_filename(tag_filter) if tag_filter else ""
    out_name = f"highlight-{orient}-{suffix}.mp4" if suffix else \
        f"highlight-{orient}.mp4"
    if vertical:
        try:
            return vrender.build_vertical(video_path, settings,
                                          _work_dir_for(video_path), out_name)
        except (RuntimeError, ValueError) as ex:
            # gen_selected 只接 gr.Error——裸异常会半途杀死生成器、按钮永久卡灰
            raise gr.Error(str(ex)) from ex
    data = load_events(os.path.join(_work_dir_for(video_path), "events.json"))
    clips = editor.plan_clips(settings, duration=data["duration"])
    try:
        return editor.build_highlight(video_path, clips,
                                      _work_dir_for(video_path), out_name)
    except (RuntimeError, ValueError) as ex:
        # 附修（2026-10-21 自查 P0-3）：横屏分支此前**裸抛** RuntimeError，
        # 而 gen_selected 只 `except gr.Error` —— 一次 ffmpeg 失败就会穿透到
        # 生成器外面，把它中途杀死、按钮永久卡灰（竖屏分支早就转过了，横屏
        # 漏了）。单条模式把这个洞放大 N 倍，所以一并补上。
        raise gr.Error(str(ex)) from ex


# 剪辑台视口锁定（2026-08-23 二版，替换首版的像素偏移猜测方案——那版在
# 真实 1080p 笔记本上没命中）。机制：把 gradio 5.50 的真实 DOM 链逐层
# 变成可分配高度的 flex 容器（选择器依据打包 sourcemap 里 TabItem/Tabs/
# Row/Column/Table.svelte 的真实标记核对）。注意 gradio 5 已无 shadow DOM
# （无头浏览器实测 gradio-app.shadowRoot === null），首版的 :host 选择器
# 在文档级样式表里永远落空——这里直接标签选择器钉 gradio-app 到视口高，
# 页面物理上装不下更多内容。左栏表格与右栏 ℹ 面板是两个"弹性肺"，吸收
# 全部剩余高度；其余控件固定开销。Row 默认 stretch 会给列内所有子块
# flex-grow:1（高度一旦确定就会把每个控件均匀撑肥），先统一归零再单独
# 放行两个肺。标定页 tab 改为内部滚动，锁页面对它无感。
_COMPACT_CSS = """
gradio-app {height: 100dvh !important; max-height: 100dvh !important;
       overflow: hidden !important}
footer {display: none !important}
main.fillable {display: flex !important; flex-direction: column !important;
       flex: 1 1 auto !important; min-height: 0 !important}
.wrap, .contain, .tabs {display: flex !important;
       flex-direction: column !important}
.wrap, .contain, .tabs {flex: 1 1 auto !important; min-height: 0 !important}
/* contain 里 gradio 自动包的 Column 是压不下去的暗层（min-height:auto），
   不归零它，tabs 永远按内容高度顶破视口 */
.contain > .column {flex: 1 1 auto !important; min-height: 0 !important}

#editbay {flex: 1 1 auto; min-height: 0; overflow: auto}
#tab-calib {flex: 1 1 auto; min-height: 0; overflow-y: auto}
/* 出手人 tab：tab 是唯一滚动者——Gallery 自带的 .grid-wrap 内部滚动窗
   会吃掉滚轮（实测 495px 窗口装 6235px 内容），wall-fit 把它撑开成
   自然高度，整页一条滚动条；归人操作条吸顶，深处选完球免滚回顶部 */
#tab-shooter {flex: 1 1 auto; min-height: 0; overflow-y: auto}
.wall-fit, .wall-fit .gallery-container {height: auto !important}
.wall-fit .grid-wrap {height: auto !important; max-height: none !important;
  overflow: visible !important}
#tab-shooter .assign-bar {position: sticky; top: 0; z-index: 30;
  background: var(--background-fill-primary, #fff)}
#editbay > .column {flex: 1 1 auto; min-height: 0; gap: 8px}
.eb-row {flex: 1 1 auto; min-height: 0; align-items: stretch !important}
.eb-left, .eb-right {min-height: 0; align-self: stretch !important;
       gap: 8px !important}
/* 底部条带固定开销不弹性：弹窗/手风琴展开变高时，由上方两个肺让位 */
.eb-band {flex: 0 0 auto; min-height: 0; gap: 8px !important}
.eb-left > *, .eb-right > * {flex-grow: 0 !important; flex-shrink: 0 !important}

/* 弹性肺 1：进球列表填满左栏剩余空间，行多了表内滚动 */
.eb-left > .fit-table {flex-grow: 1 !important; flex-shrink: 1 !important;
  min-height: 140px; display: flex; flex-direction: column}
.fit-table .table-container {flex: 1 1 auto; min-height: 0}
.fit-table .table-wrap {flex: 1 1 auto; min-height: 0;
  overflow: auto}

/* 弹性肺 2：ℹ 判定依据面板填满右栏底部，内容多时面板内滚动 */
.eb-right > .panel-fit {flex-grow: 1 !important; flex-shrink: 1 !important;
  min-height: 110px; max-height: 42dvh; overflow-y: auto}

/* 紧凑化（保留项） */
.block, .form {padding: 6px 8px !important}
.form {gap: 4px !important; border-width: 0 !important}
button {padding: 3px 12px !important; min-height: 30px !important;
        font-size: 13px !important}
.slider input[type=range] {margin: 6px 0 !important}
"""


def build_app():
    # 主题：主色取篮球橙（球的颜色）承载两个主动作（开始检测 / 生成集锦），
    # 中性色 slate。字体走系统栈——不引 Google Fonts，离线/弱网启动不卡。
    # 预览条的蓝红绿是数据编码（前/时刻/后），与按钮橙不冲突。
    theme = gr.themes.Soft(
        primary_hue="orange", neutral_hue="slate",
        font=["system-ui", "-apple-system", "Segoe UI", "Microsoft YaHei",
              "sans-serif"],
        font_mono=["ui-monospace", "Consolas", "monospace"])
    with gr.Blocks(title="篮球进球自动集锦", theme=theme,
                   css=_COMPACT_CSS) as demo:
        # ---------- 主页：剪辑台（左窄栏流水线 + 右主栏预览） ----------
        with gr.Tab("剪辑台", elem_id="editbay"):
            with gr.Row(elem_classes=["eb-row"]):
                # 左窄栏 = 流水线轨道：①② 徽章沿用主色橙；编号是真顺序
                # （先检测才有列表），不是装饰。轨道末端是终局动作按钮。
                with gr.Column(scale=2, elem_classes=["eb-left"]):
                    video_path_box = gr.Textbox(
                        label="视频路径（首次先去「⚙ 标定设置」做一次标定）",
                        placeholder=r"D:\videos\2026-08-16.mp4")
                    with gr.Row():
                        btn_start = gr.Button("▶ 开始检测", variant="primary",
                                              size="sm")
                        # 横/竖两个集锦入口（2026-09-16 实机验收问题4）：
                        # 各开各的弹窗、确定时各走各的管线，互不影响
                        btn_gen_h = gr.Button("🎬 横屏集锦", variant="primary",
                                              size="sm")
                        btn_gen_v = gr.Button("📱 竖屏集锦", variant="primary",
                                              size="sm")
                        btn_shooter = gr.Button("🧍 识别出手人", size="sm")
                    # 画幅旗标：哪个按钮开出弹窗，确定时就走哪条管线
                    # （gradio 弹窗无局部状态，旗标骑 State 随开窗写入）
                    gen_vertical = gr.State(True)
                    shooter_md = gr.Markdown("")
                    # 归人/移出唯一入口在「🧍 出手人」tab（2026-09-01 拆分）。
                    # 2026-09-06 起标签↔归人互通：已归人的球在表格改标签 =
                    # 自动改判（清空=移出回墙），面板/下拉跟随刷新——表格
                    # 标签不再只是集锦维度
                    status_md = gr.Markdown("空闲。检测中途退出后，"
                                            "重新点开始即可从检查点续跑。")
                    duration_md = gr.Markdown()
                    events_table = gr.Dataframe(
                        headers=["选中", "时间", "档位", "标签"],
                        datatype=["bool", "str", "str", "str"],
                        type="array", interactive=True,
                        elem_classes=["fit-table"],
                        label="进球列表（点行→右侧预览；可直接改「选中/标签」格）")
                    rows_state = gr.State([])   # 完整行：勾选/时间/状态/置信度/前/后/标签

                # 右主栏 = 舞台：预览占 5/7 宽是主角，复核/打标都围着它
                with gr.Column(scale=5, elem_classes=["eb-right"]):
                    preview_player = gr.HTML(_hint_html(
                        "点击表格任意一行，这里播放该片段；进度条上"
                        '<span style="color:#2563eb">蓝=进球前</span>、'
                        '<span style="color:#dc2626">红线=进球时刻</span>、'
                        '<span style="color:#16a34a">绿=进球后</span>'
                        "，点击进度条可跳转"))
                    highlight_player = gr.Video(
                        label="集锦成品", visible=False,
                        height="min(400px, 40dvh)")
                    btn_back = gr.Button("↩ 回到逐条预览", visible=False,
                                         size="sm")
                    cur_row_state = gr.State(None)   # 裁决/打标签的作用行
                    # 裁决/漏检紧跟视频窗口：看完片段顺手裁决，不用往下找。
                    # 2026-08-23 用户反馈收紧：手风琴标题与"还未标记"说明
                    # 文案整块去掉（控件自说明）、漏检/打标签改 placeholder
                    # 免标签行——把纵向空间还给视频窗口。
                    with gr.Row():
                        btn_goal_v = gr.Button("✅ 是进球", size="sm")
                        btn_not_goal = gr.Button("❌ 未进球", size="sm")
                        btn_del = gr.Button("🗑 删除此球", size="sm")
                        btn_bulk = gr.Button("🗑 批量删除…", size="sm")
                        miss_box = gr.Textbox(
                            placeholder="漏检时刻 mm:ss（列表里完全没有的"
                                        "进球才填，大致即可）",
                            show_label=False, scale=2)
                        btn_miss = gr.Button("➕ 记录", size="sm")
                        btn_restore = gr.Button("↩ 恢复已删", size="sm")
                    fp_status = gr.Markdown("")
                    # 批量删除面板（2026-09-06）：表格「选中」勾选语义=人工
                    # 核对确认进球（44754f5 定案）不能挪用，多选放删除语境
                    # 内的 CheckboxGroup——随意投篮常成串，逐球点删太碎。
                    with gr.Group(visible=False) as bulk_del_dialog:
                        gr.Markdown("勾选要删的进球（随意投篮等非正赛球）"
                                    "——只记清单不动原数据，「↩ 恢复已删」"
                                    "随时一键找回；勾选的「选中/标签/归人」"
                                    "随行清理")
                        bulk_boxes = gr.CheckboxGroup(
                            label="待删除进球（显示=时间｜档位｜标签）",
                            choices=[])
                        with gr.Row():
                            btn_bulk_ok = gr.Button("🗑 确认删除所选",
                                                    variant="stop", size="sm")
                            btn_bulk_cancel = gr.Button("取消", size="sm")
                    # 打标签在裁决正下方：点行 → 看预览 → 裁决 → 打标签，
                    # 一列走完。（表格的「标签」格是手输文本——gradio 表格
                    # 不支持单元格嵌下拉，下拉只能做独立控件）
                    with gr.Row():
                        row_tag_dd = gr.Dropdown(
                            choices=_load_tags(), value=None,
                            label="标签（清空 = 去掉）", scale=4)
                        btn_add_tag = gr.Button("➕ 新增", scale=1, size="sm")
                    with gr.Group(visible=False) as add_tag_dialog:
                        with gr.Row():
                            add_tag_box = gr.Textbox(
                                label="新人名（入全局标签库，全部视频共用；"
                                      "回车或点确定）", scale=4)
                            btn_add_tag_ok = gr.Button("✓ 确定", scale=1,
                                                       size="sm")
                            btn_add_tag_cancel = gr.Button("取消", scale=1,
                                                           size="sm")
                    cur_panel = gr.HTML(_PANEL_INIT, elem_classes=["panel-fit"])
                    # 两类误标按钮（语义见 _toggle_mark_flag 区注释）：
                    # 坏缩略图/坏十字分开采样（点行看缩略图→顺手标记），
                    # 标完所有视频后读 thumb_flags.json / cross_flags.json
                    # 统一看图归因。按钮态随当前行，计数行常显清单。
                    with gr.Row():
                        btn_thumb_flag = gr.Button("⚠ 缩略图有误", size="sm")
                        btn_cross_flag = gr.Button("✚ 十字有误", size="sm")
                    thumb_flag_md = gr.Markdown("")
                    cross_flag_md = gr.Markdown("")

            # 底部条带：时长按钮/时刻校准/生成弹窗横排一行。原本竖排在
            # 左栏要吃 ~330px 纵向空间，是表格肺被挤没的主因；生成弹窗打开
            # 或校准展开时条带变高，上方的表格/ℹ 面板两个肺自动让位。
            # 全局前后秒属低频操作（2026-08-23 用户反馈：两个 Number 常驻
            # 把进球列表挤没了）→ 收进按钮弹窗，条带只留一个「⏱ 时长」。
            with gr.Row(elem_classes=["eb-band"]):
                with gr.Column(scale=1, min_width=100):
                    btn_time_set = gr.Button("⏱ 时长", size="sm")
                with gr.Column(scale=4):
                    with gr.Accordion("更多调整（时刻校准）", open=False):
                        with gr.Row():
                            time_offset = gr.Number(
                                value=0, scale=4,
                                label="进球时刻校准（秒）：检测比实际进球早就"
                                "填正数（整体后移），晚了填负数")
                            btn_reload = gr.Button("重新载入", scale=1,
                                                   size="sm")
                with gr.Column(scale=2):
                    with gr.Group(visible=False) as gen_dialog:
                        gen_stats_md = gr.Markdown("")   # 勾选进球统计行
                        gen_choices = gr.CheckboxGroup(
                            choices=[GEN_ALL_LABEL] + _load_tags(),
                            value=[GEN_ALL_LABEL],
                            label="要生成哪些视频？（视频合集 = 全部勾选的"
                                  "进球；标签 = 勾选且带该标签的进球，每个"
                                  "标签一个）")
                        with gr.Row():
                            btn_gen_ok = gr.Button("🎬 开始生成",
                                                   variant="primary", size="sm")
                            btn_gen_cancel = gr.Button("取消", size="sm")
                    gen_result_md = gr.Markdown("")

                    status_timer = gr.Timer(2.0)
                    status_timer.tick(
                        refresh_and_autoload, inputs=[video_path_box],
                        outputs=[status_md, duration_md,
                                 events_table, rows_state, cur_row_state,
                                 cur_panel],
                        show_progress="hidden")  # 默认 full：每 tick 给全部
                        # 输出组件罩 pending 遮罩，整页每 2s 闪一次
                    video_path_box.change(
                        probe_existing, inputs=[video_path_box],
                        outputs=[duration_md, events_table, rows_state,
                                 cur_row_state, cur_panel, status_md])
                    btn_start.click(start_detection, inputs=[video_path_box],
                                    outputs=[status_md])
                    events_table.select(
                        preview_row_click,
                        inputs=[video_path_box, rows_state],
                        outputs=[preview_player, highlight_player,
                                 cur_row_state, cur_panel, row_tag_dd,
                                 btn_thumb_flag, thumb_flag_md,
                                 btn_cross_flag, cross_flag_md])
                    btn_thumb_flag.click(
                        toggle_thumb_flag,
                        inputs=[video_path_box, rows_state, cur_row_state],
                        outputs=[btn_thumb_flag, thumb_flag_md])
                    btn_cross_flag.click(
                        toggle_cross_flag,
                        inputs=[video_path_box, rows_state, cur_row_state],
                        outputs=[btn_cross_flag, cross_flag_md])
                    # events_table.input / row_tag_dd.input 的绑定随 2026-09-06
                    # 改判互通迁去「🧍 出手人」tab——outputs 要带那边的
                    # manual_md/person_dd（标签编辑=改判后面板/下拉跟随刷新）
                    def _reload_ui(vp, offset):
                        """显式重载：表格五件套 + 批量删除面板收起清空
                        （choices 打开时才重算，这里只防旧面板悬着）。"""
                        return (*load_table_for_ui(vp, offset),
                                gr.update(visible=False),
                                gr.update(value=[], choices=[]))

                    btn_reload.click(
                        _reload_ui, inputs=[video_path_box, time_offset],
                        outputs=[duration_md, events_table, rows_state,
                                 cur_row_state, cur_panel,
                                 bulk_del_dialog, bulk_boxes])
                    btn_add_tag.click(lambda: gr.update(visible=True),
                                      outputs=[add_tag_dialog])
                    # add_tag_to_library 无视频上下文只知标签库——布线层
                    # 封皮拿当前视频重算 union（新增后归人名不被冲掉）
                    def _add_tag_ui(vp, name):
                        _, box, dlg = add_tag_to_library(name)
                        return gr.update(choices=_tag_choices(vp)), box, dlg
                    btn_add_tag_ok.click(_add_tag_ui,
                                         inputs=[video_path_box, add_tag_box],
                                         outputs=[row_tag_dd, add_tag_box,
                                                  add_tag_dialog])
                    add_tag_box.submit(_add_tag_ui,
                                       inputs=[video_path_box, add_tag_box],
                                       outputs=[row_tag_dd, add_tag_box,
                                                add_tag_dialog])
                    btn_add_tag_cancel.click(
                        lambda: (gr.update(visible=False), ""),
                        outputs=[add_tag_dialog, add_tag_box])
                    def _open_gen_h(vp, rows):
                        """横屏入口：弹窗旗标=False（具名封皮，接线可测）。"""
                        return open_gen_dialog(vp, rows, False)

                    def _open_gen_v(vp, rows):
                        """竖屏入口：弹窗旗标=True。"""
                        return open_gen_dialog(vp, rows, True)

                    btn_gen_h.click(_open_gen_h,
                                    inputs=[video_path_box, rows_state],
                                    outputs=[gen_choices, gen_dialog,
                                             gen_stats_md, gen_vertical])
                    btn_gen_v.click(_open_gen_v,
                                    inputs=[video_path_box, rows_state],
                                    outputs=[gen_choices, gen_dialog,
                                             gen_stats_md, gen_vertical])
                    btn_gen_ok.click(
                        gen_selected,
                        inputs=[video_path_box, rows_state, gen_choices,
                                gen_vertical],
                        outputs=[highlight_player, preview_player, btn_back,
                                 gen_dialog, gen_result_md, btn_gen_ok])
                    btn_gen_cancel.click(lambda: gr.update(visible=False),
                                         outputs=[gen_dialog])
                    btn_shooter.click(run_shooter_tracing,
                                      inputs=[video_path_box],
                                      outputs=[shooter_md, btn_shooter])
                    # 归人/墙布线随组件迁至「🧍 出手人」tab（先定义后布线
                    # 惯例）；btn_assign/btn_unassign 的 click 也注册在那
                    # 边——gradio 事件全局生效，与所在 with 块无关
                    btn_back.click(_back_to_preview, inputs=None,
                                   outputs=[highlight_player, preview_player,
                                            btn_back])
                    def _mark_goal(vp, rws, cur):
                        return mark_verdict(vp, rws, cur, "goal")

                    def _mark_not_goal(vp, rws, cur):
                        return mark_verdict(vp, rws, cur, "not_goal")

                    btn_goal_v.click(_mark_goal,
                                     inputs=[video_path_box, rows_state,
                                             cur_row_state],
                                     outputs=[fp_status])
                    btn_not_goal.click(_mark_not_goal,
                                       inputs=[video_path_box, rows_state,
                                               cur_row_state],
                                       outputs=[fp_status])
                    btn_miss.click(record_miss,
                                   inputs=[video_path_box, miss_box],
                                   outputs=[fp_status, miss_box])

            # 全局时长弹窗（与新增标签/生成弹窗同一套 Group 开合惯例）：
            # 平时隐藏不占行高，点「⏱ 时长」才在底部条带下方展开一行。
            # 前/后秒收进这里后，常驻条带只剩一排按钮，进球列表不再被挤。
            # 组件须先于下方 wiring 定义；wiring 挂在哪个 with 块里无所谓。
            with gr.Group(visible=False, elem_classes=["eb-band"]) as time_dialog:
                with gr.Row():
                    global_before = gr.Number(value=DEFAULT_BEFORE_S, scale=1,
                                              label="全局 前(秒)")
                    global_after = gr.Number(value=DEFAULT_AFTER_S, scale=1,
                                             label="全局 后(秒)")
                    btn_apply_all = gr.Button("应用到全部", scale=1, size="sm")
                    btn_time_cancel = gr.Button("取消", scale=1, size="sm")

            btn_time_set.click(lambda: gr.update(visible=True),
                               outputs=[time_dialog])
            btn_time_cancel.click(lambda: gr.update(visible=False),
                                  outputs=[time_dialog])

            def _apply_all_and_close(vp, rows, b, a, cur):
                """应用后顺手关弹窗（2026-08-23 起低频的全局前后秒收进弹窗）。
                apply_all_rows 本体保持三返回值不动（测试直调）。"""
                return (*apply_all_rows(vp, rows, b, a, cur),
                        gr.update(visible=False))

            btn_apply_all.click(
                _apply_all_and_close,
                inputs=[video_path_box, rows_state, global_before,
                        global_after, cur_row_state],
                outputs=[rows_state, events_table, cur_panel, time_dialog])

        # ---------- 🧍 出手人：归人工作台（2026-09-01 从剪辑台拆出且收敛
        # 为唯一归人入口——未归人墙 + 已归人面板/调整墙 + 归人/移出
        # 自成闭环；球来源=两面墙点选，表格勾选=核对标记不参与） ----------
        with gr.Tab("🧍 出手人", elem_id="tab-shooter"):
            manual_md = gr.HTML("")   # 已归人面板：人→球缩略图
            # 已归人调整墙（2026-09-06 问题 3）：结果模块的二次多选
            # 改判/移出，不必回剪辑台。与未归人墙共用已选清单/按钮
            adj_gallery = gr.Gallery(
                # 6 列（2026-09-13 前 10 列每格 ~130px 看不清认人；格子翻大
                # 配 GIF_H 640，一屏少几个球多滚动，认人清楚优先）
                [], columns=6, object_fit="contain",
                allow_preview=False, elem_classes=["wall-fit"])
            adj_order_state = gr.State([])   # 调整墙序（点击索引→key）
            with gr.Row():
                wall_mode = gr.Radio(["时间", "外观相似", "出手点"],
                                     value="时间", label="排序")
                btn_wall_clear = gr.Button("清空已选", size="sm")
            wall_sel_md = gr.HTML("")   # 已选清单（选中反馈真源）
            wall_msg = gr.Markdown("")  # 归人/移出的结果反馈
            with gr.Row(elem_classes=["assign-bar"]):
                # 吸顶（_COMPACT_CSS）：墙深处的选球不用滚回顶部就能归人
                person_dd = gr.Dropdown(label="归人（选/输入人名）",
                                        allow_custom_value=True)
                btn_assign = gr.Button("✅ 归人", size="sm")
                btn_unassign = gr.Button("↩️ 移出", size="sm")
            wall_gallery = gr.Gallery(
                # 5.50 Gallery 无多选（仅单选 selected_index 且强制预览）
                # ——点击只发 select 事件，选中态走清单 HTML。wall-fit+
                # #tab-shooter overflow（_COMPACT_CSS）：墙撑成自然高度，
                # 整个 tab 一条滚动条（Gallery 自带内部滚动窗会吃滚轮）
                [], columns=6, object_fit="contain",
                allow_preview=False, elem_classes=["wall-fit"])
            wall_sel = gr.State([])           # 墙上点选的球 key
            wall_order_state = gr.State([])   # 当前墙序（点击索引→key）

            # ---- 手动归人布线（Phase 2 修订；2026-09-01 随组件迁本 tab）----
            # assign/unassign 改 rows_state → 表格刷新 outputs 逐项抄齐
            # row_tag_dd 布线（set_row_tag 同款投影+面板重建）——函数本体
            # 只回 4 路（plan 接口），封皮在布线层补投影与当前行面板。
            def _panel_after(vp, rows2, cur_row):
                if isinstance(cur_row, int) \
                        and 0 <= cur_row < len(rows2 or []):
                    return _cur_panel_html(
                        rows2, cur_row,
                        _reason_for(vp, rows2, cur_row), vp)
                return gr.update()

            def _assign_ui(vp, rows, person, cur_row, wsel, wmode,
                           req: gr.Request = None):
                msg, rows2, m_html, choices = assign_person(
                    vp, rows, person, extra_keys=wsel)
                g, order, sel, sel_md = _wall_render(vp, wmode, None, req)
                ag, aorder = _adj_wall_render(vp, req)   # 改判球搬进调整墙
                # 13 路：消息双投（剪辑台 shooter_md + 本 tab wall_msg），
                # 人名 choices 回本 tab 下拉，调整墙跟随
                return (msg, project_rows(rows2), rows2,
                        _panel_after(vp, rows2, cur_row), m_html,
                        g, order, sel, sel_md,
                        msg, gr.update(choices=choices), ag, aorder)

            def _unassign_ui(vp, rows, cur_row, wsel, wmode,
                             req: gr.Request = None):
                msg, rows2, m_html, choices = unassign_person(
                    vp, rows, extra_keys=wsel)
                g, order, sel, sel_md = _wall_render(vp, wmode, None, req)
                ag, aorder = _adj_wall_render(vp, req)   # 移出球退出调整墙
                return (msg, project_rows(rows2), rows2,
                        _panel_after(vp, rows2, cur_row), m_html,
                        g, order, sel, sel_md,
                        msg, gr.update(choices=choices), ag, aorder)

            def _manual_and_wall_ui(vp, rows, mode, req: gr.Request = None):
                """归人面板 + 两面墙一起刷新（换视频/识别完成时墙跟随，
                选中清空——key 属于上一个视频）。"""
                html, choices = render_manual_payload(vp, rows)
                g, order, sel, sel_md = _wall_render(vp, mode, None, req)
                ag, aorder = _adj_wall_render(vp, req)
                return (html, gr.update(choices=choices), g, order,
                        sel, sel_md, ag, aorder)

            # choices 经封皮转 gr.update——裸 list 会被 Dropdown
            # postprocess 成 value（终审修复 1）；墙 4 路（Gallery/
            # 墙序/选中 State/清单 HTML）同款封皮
            video_path_box.change(_manual_and_wall_ui,
                                  [video_path_box, rows_state, wall_mode],
                                  [manual_md, person_dd, wall_gallery,
                                   wall_order_state, wall_sel, wall_sel_md,
                                   adj_gallery, adj_order_state])
            btn_shooter.click(_manual_and_wall_ui,
                              [video_path_box, rows_state, wall_mode],
                              [manual_md, person_dd, wall_gallery,
                               wall_order_state, wall_sel, wall_sel_md,
                               adj_gallery, adj_order_state])
            # 表格标签编辑 = 改判（_sync_manual_from_tag_edits）→ 已归人
            # 面板/人名下拉/调整墙跟随刷新（调整墙只几十张，无 434 张
            # 大墙的重渲染顾虑）。绑定从剪辑台迁来（outputs 引用本 tab
            # 组件，先定义后布线）；未归人大墙仍不在此刷新——下次墙动作
            # （换排序/归人/换视频）自然重排。
            def _table_edit_ui(vp, rows, table, req: gr.Request = None):
                synced = on_table_edit(vp, rows, table)
                html, choices = render_manual_payload(vp, synced)
                ag, aorder = _adj_wall_render(vp, req)
                return synced, html, gr.update(choices=choices), ag, aorder

            events_table.input(
                _table_edit_ui,
                inputs=[video_path_box, rows_state, events_table],
                outputs=[rows_state, manual_md, person_dd,
                         adj_gallery, adj_order_state])

            def _row_tag_ui(vp, rows, cur_row, tag, req: gr.Request = None):
                proj, rows2, panel = set_row_tag(vp, rows, cur_row, tag)
                html, choices = render_manual_payload(vp, rows2)
                ag, aorder = _adj_wall_render(vp, req)
                return proj, rows2, panel, html, gr.update(choices=choices), \
                    ag, aorder

            row_tag_dd.input(
                _row_tag_ui,
                inputs=[video_path_box, rows_state, cur_row_state,
                        row_tag_dd],
                outputs=[events_table, rows_state, cur_panel,
                         manual_md, person_dd, adj_gallery, adj_order_state])
            # 墙：排序切换保选中重排；点缩略图=切换选中（只动清单
            # 与 State，Gallery 不重渲染——保滚动位置）；清空一键
            def _wall_mode_ui(vp, mode, sel, req: gr.Request = None):
                return _wall_render(vp, mode, sel, req)
            wall_mode.change(
                _wall_mode_ui,
                [video_path_box, wall_mode, wall_sel],
                [wall_gallery, wall_order_state, wall_sel, wall_sel_md])

            def _wall_sel_ui(vp, order, sel, evt: gr.SelectData):
                """点选封皮（两面墙共用）：切换选中 + 颜色相近提示跟随
                点选（墙点选是提示的唯一数据源——表格勾选已解耦）。
                人名 choices 不在此刷——点选不改人名集合，归人/移出后
                已统一刷新，少一路下拉重渲染。"""
                sel_md, sel2 = _wall_toggle(order, sel, evt)
                m_html, _choices = render_manual_payload(vp, None, sel2)
                return sel_md, sel2, m_html

            wall_gallery.select(_wall_sel_ui,
                                [video_path_box, wall_order_state, wall_sel],
                                [wall_sel_md, wall_sel, manual_md])
            adj_gallery.select(_wall_sel_ui,
                               [video_path_box, adj_order_state, wall_sel],
                               [wall_sel_md, wall_sel, manual_md])

            def _wall_clear_ui(vp):
                """清空点选 + 提示随选中一并清掉。"""
                m_html, _choices = render_manual_payload(vp, None, [])
                return _wall_sel_html([]), [], m_html

            btn_wall_clear.click(_wall_clear_ui, inputs=[video_path_box],
                                 outputs=[wall_sel_md, wall_sel, manual_md])
            # 归人/移出唯一入口：本 tab 按钮，球来源=墙上点选（未归人墙
            # ∪已归人调整墙；表格勾选=核对标记不参与——2026-09-06 定案）
            _ASSIGN_OUT = [shooter_md, events_table, rows_state, cur_panel,
                           manual_md, wall_gallery, wall_order_state,
                           wall_sel, wall_sel_md, wall_msg, person_dd,
                           adj_gallery, adj_order_state]
            btn_assign.click(_assign_ui,
                             [video_path_box, rows_state, person_dd,
                              cur_row_state, wall_sel, wall_mode],
                             _ASSIGN_OUT)
            btn_unassign.click(_unassign_ui,
                               [video_path_box, rows_state, cur_row_state,
                                wall_sel, wall_mode],
                               _ASSIGN_OUT)

            # ---- 剪辑台「🗑 删除此球」/「↩ 恢复已删」（2026-09-06）----
            # 按钮在剪辑台裁决行，outputs 引用本 tab 的墙/面板组件——
            # 注册随归人惯例放这边（gradio 事件全局生效）。删除=视图层
            # 过滤（deleted.json sidecar，非破坏），events.json 不动；
            # 级联：归人组移出、两面墙剔球、溯源不再回扫已删球。
            _DELETE_OUT = [duration_md, events_table, rows_state,
                           cur_row_state, cur_panel, fp_status, manual_md,
                           person_dd, wall_gallery, wall_order_state,
                           wall_sel, wall_sel_md, adj_gallery, adj_order_state]

            def _delete_ui(vp, rows, cur_row, wsel, wmode, req=None):
                msg, rows2 = delete_event(vp, rows, cur_row)
                g, order, sel, sel_md = _wall_render(vp, wmode, wsel, req)
                ag, aorder = _adj_wall_render(vp, req)   # 已删球退出两墙
                m_html, choices = render_manual_payload(vp, rows2)
                data = _load_events_or_none(vp)
                dur_md = (_table_summary_md(data["duration"], rows2)
                          if data else gr.update())       # 共 N 条要跟着变
                # 当前行随行消失：复位 None + 面板占位（行号已失效）
                return (dur_md, project_rows(rows2), rows2, None,
                        _cur_panel_html(rows2, None), msg, m_html,
                        gr.update(choices=choices), g, order, sel, sel_md,
                        ag, aorder)

            def _restore_ui(vp, offset, wmode, req=None):
                deleted = restore_deleted(vp)
                dur_md, proj, rows, _, panel = load_table_for_ui(
                    vp, offset or 0.0)
                g, order, sel, sel_md = _wall_render(vp, wmode, None, req)
                ag, aorder = _adj_wall_render(vp, req)
                m_html, choices = render_manual_payload(vp, rows)
                msg = (f"↩ 已恢复 {len(deleted)} 球（{'、'.join(deleted[:3])}"
                       f"{'…' if len(deleted) > 3 else ''}）——恢复后未勾选/"
                       "未归人，需重新核对与归人")
                return (dur_md, proj, rows, None, panel, msg, m_html,
                        gr.update(choices=choices), g, order, sel, sel_md,
                        ag, aorder)

            btn_del.click(_delete_ui,
                          [video_path_box, rows_state, cur_row_state,
                           wall_sel, wall_mode], _DELETE_OUT)
            btn_restore.click(_restore_ui,
                              [video_path_box, time_offset, wall_mode],
                              _DELETE_OUT)

            # ---- 「🗑 批量删除…」面板：打开即按当前表格行重算 choices
            # （换视频/重跑检测后重开即刷新，旧勾选不残留），确认走
            # delete_events（失配键丢弃），取消/确认都收起清空。----
            def _bulk_open(vp, rows):
                if not (rows or []):
                    raise gr.Error("先载入视频——表格里有进球才能批量删除")
                return (gr.update(visible=True),
                        gr.update(choices=bulk_delete_choices(vp, rows),
                                  value=[]))

            def _bulk_del_ui(vp, rows, keys, wsel, wmode, req=None):
                msg, rows2 = delete_events(vp, rows, keys)
                g, order, sel, sel_md = _wall_render(vp, wmode, wsel, req)
                ag, aorder = _adj_wall_render(vp, req)
                m_html, choices = render_manual_payload(vp, rows2)
                data = _load_events_or_none(vp)
                dur_md = (_table_summary_md(data["duration"], rows2)
                          if data else gr.update())
                return (dur_md, project_rows(rows2), rows2, None,
                        _cur_panel_html(rows2, None), msg, m_html,
                        gr.update(choices=choices), g, order, sel, sel_md,
                        ag, aorder,
                        gr.update(visible=False),        # 面板收起
                        gr.update(value=[], choices=[]))  # 勾选清空

            btn_bulk.click(_bulk_open,
                           [video_path_box, rows_state],
                           [bulk_del_dialog, bulk_boxes])
            btn_bulk_ok.click(_bulk_del_ui,
                              [video_path_box, rows_state, bulk_boxes,
                               wall_sel, wall_mode],
                              _DELETE_OUT + [bulk_del_dialog, bulk_boxes])
            btn_bulk_cancel.click(
                lambda: (gr.update(visible=False),
                         gr.update(value=[], choices=[])),
                outputs=[bulk_del_dialog, bulk_boxes])

        # ---------- ⚙ 标定设置（原「① 标定」页整体搬迁，内部不变） ----------
        with gr.Tab("⚙ 标定设置（首次使用做一次）", elem_id="tab-calib"):
            gr.Markdown("上传一段日常视频，或直接填本机路径（**长视频推荐，免上传**）"
                        " → 自动抽取中段一帧 → 在帧上依次点击："
                        "**筐口左端、筐口右端、球（1-3 次）**")
            calib_video = gr.Video(label="参考视频上传（短视频适用，大文件见下方路径）")
            calib_path = gr.Textbox(
                label="或填参考视频完整路径（回车或点按钮抽帧）",
                placeholder=r"D:\videos\2026-08-16.mp4")
            video_src = gr.State("")   # 当前标定的视频（标定随视频走的关键：
                                       # 生成标定/微调/补段/删段都按它定 work 目录）
            btn_from_path = gr.Button("从该路径抽帧")
            with gr.Row():
                calib_time = gr.Number(label="时间点（秒）——跳到球在筐口附近的画面",
                                       precision=0)
                btn_jump = gr.Button("跳到该时间抽帧")
            frame_img = gr.Image(
                label="参考帧（依次点击：筐口左端、筐口右端、球 1-3 次）",
                sources=(),    # 不限高度，按原视频尺寸显示（.select 坐标始终是原图像素）
                format="png")  # 默认 webp 是有损（q80）：参考帧过一遍 UI 就带伪影，改存无损 png
            click_msg = gr.Markdown(click_hint([]))
            pts_state = gr.State([])
            with gr.Row():
                btn_clear = gr.Button("清空点击")
                btn_calib = gr.Button("生成标定", variant="primary")
            gr.Markdown(
                "**镜头中途移动过？** 用「时间点」跳到移动后的稳定画面，"
                "点击筐口两端+球，再点下方按钮补一段标定（该时刻起用新几何，"
                "之前的时段仍用主标定）——比整片重标定快且两边都准。")
            btn_calib_seg = gr.Button("➕ 生成新段标定（当前时间点起）")
            seg_md = gr.Markdown("")     # 段列表随当前视频刷新（_extract_for_ui）
            with gr.Row():
                seg_dd = gr.Dropdown(label="删除补充段（标错/镜头挪回去了）",
                                     choices=[], scale=3)
                btn_del_seg = gr.Button("🗑 删除", size="sm", scale=1)
            preview_img = gr.Image(
                label="预览验证（绿=球，蓝框=筐口，红线=筐口平面，黄框=检测区域）")
            calib_msg = gr.Markdown()
            gr.Markdown(
                "预览是**单帧**，看不出时间维度：篮筐下**静止不动的绿点（篮网等）"
                "在正式检测时会被自动滤除**——同一位置持续出现数秒即判为静态物。"
                "只有跟着球移动的绿点才参与进球判定。\n\n"
                "**HSV 微调**：先把「饱和度下限」调小到**球变绿**，"
                "再逐步调大到肤色消失（肤色与球同为橙色相，但饱和度低不少）。"
                "每次重算后会显示 ROI 内命中像素数——**0 命中说明球也被滤掉了**。")
            with gr.Row():
                s_low_slider = gr.Slider(0, 255, value=80, step=1,
                                         label="饱和度 S 下限（调大滤肤色）")
                v_low_slider = gr.Slider(0, 255, value=100, step=1,
                                         label="明度 V 下限")
                btn_refine = gr.Button("重算预览")

            _extract_outs = [frame_img, pts_state, click_msg, preview_img,
                             calib_msg, video_src, seg_md, seg_dd]
            calib_video.change(_extract_for_ui, inputs=calib_video,
                               outputs=_extract_outs)
            calib_path.submit(_extract_for_ui, inputs=calib_path,
                              outputs=_extract_outs)
            btn_from_path.click(_extract_for_ui, inputs=calib_path,
                                outputs=_extract_outs)

            def _jump(uploaded, path_str, t):
                return _extract_for_ui(uploaded or path_str, t)

            btn_jump.click(_jump, inputs=[calib_video, calib_path, calib_time],
                           outputs=_extract_outs)
            frame_img.select(add_point, inputs=pts_state,
                             outputs=[pts_state, click_msg])
            btn_clear.click(lambda: ([], click_hint([]), None, None),
                            inputs=None,
                            outputs=[pts_state, click_msg, preview_img, calib_msg])
            btn_calib.click(build_calibration,
                            inputs=[pts_state, frame_img, video_src],
                            outputs=[preview_img, calib_msg,
                                     s_low_slider, v_low_slider])
            btn_calib_seg.click(build_segment_calibration,
                                inputs=[pts_state, frame_img, calib_time,
                                        video_src],
                                outputs=[preview_img, calib_msg, seg_md, seg_dd])
            btn_del_seg.click(del_segment, inputs=[seg_dd, video_src],
                              outputs=[seg_md, seg_dd, calib_msg])
            btn_refine.click(refine_hsv,
                             inputs=[frame_img, s_low_slider, v_low_slider,
                                     video_src],
                             outputs=[preview_img, calib_msg])
    # 页面关闭/刷新 → 取消在跑的检测（否则后台线程孤儿跑到完）
    demo.unload(_on_page_unload)
    return demo


if __name__ == "__main__":
    # 一次性迁移：仓库根旧全局标定按 events.json 指纹认领进各视频 work
    # 目录（只 __main__ 跑——测试 import 不触发，不碰真数据）
    for note in migrate_root_calibrations():
        print("[标定迁移]", note)
    # 放开上传上限（⚙ 标定设置页本机直传）+ 允许 /file= 服务 work 目录
    # （剪辑台自绘播放器从 work/<视频>/preview.mp4 直接取流）
    build_app().launch(max_file_size="20GB",
                       allowed_paths=[os.path.abspath("work")])
