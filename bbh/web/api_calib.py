"""标定向导：四步（选视频 → 点筐口左/右端 + 球 → 验证预览 → HSV 微调）。

点选顺序是硬约定：第 1、2 点 = 筐口左端、右端，第 3 点起 = 球（1-3 次）——
由 app._calib_from_pts 解释，这里只负责把点按序攒进会话。

图片不落盘：帧/预览 PNG 放会话内存，用 /api/calib/{frame,preview}.png 出，
URL 带 ?v=<rev> 击穿缓存（每次重算 rev+1）。旧界面靠 gr.Image 传数组，
HTTP 下换成这招，对 work/ 目录零写入。

写端点（build / refine / DELETE segments/{i}）包 video_lock：标定档是
work/ 里的「读整档 → 改 → 写回」状态档（calibration.json / _segN.json），
与前端轮询的读交错会写半截、Windows 上还会撞 PermissionError。
"""
import os

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel

from .. import app as A
from . import state
from .api_session import _session_video

router = APIRouter(prefix="/api/calib")


class PointIn(BaseModel):
    x: float
    y: float


class BuildIn(BaseModel):
    segT: float | None = None      # 给了 = 补段标定（镜头移动后），不给 = 主标定


class RefineIn(BaseModel):
    sLow: int
    vLow: int


def _session():
    return state.SESSION


def _rev_url(name: str) -> str:
    s = _session()
    s.rev += 1
    return f"/api/calib/{name}.png?v={s.rev}"


def _encode(rgb) -> bytes:
    bgr = cv2.cvtColor(np.asarray(rgb), cv2.COLOR_RGB2BGR)
    ok, buf = cv2.imencode(".png", bgr)
    if not ok:
        raise HTTPException(500, "预览图编码失败")
    return buf.tobytes()


def _meta(msg: str = "") -> dict:
    s = _session()
    vp = s.video_path
    return {"points": [[float(x), float(y)] for x, y in s.points],
            "hint": A.click_hint(s.points),
            "canBuild": len(s.points) >= 3 and s.frame_rgb is not None,
            "ready": A._calib_ready(vp),
            "segments": A._seg_choices(vp),
            "segListMd": A._seg_list_md(vp),
            "hasPreview": s.preview_png is not None,
            "msg": msg}


@router.get("/state")
def calib_state():
    return _meta()


@router.get("/frame.png")
def frame_png(t: float = Query(0.0)):
    """跳时刻抽帧（换参考画面）。抽帧函数自己带诊断信息报错 → 400。

    前端 <img> 的 src 用 `/api/calib/frame.png?t={t}&v={rev}`——t 变了 URL
    就变，但同一 t 重抽（换视频后回到同一时刻）得靠 rev 击穿浏览器缓存。"""
    vp = _session_video()
    frame, _pts, _hint, _preview, _msg = A.extract_frame_at(vp, t)
    s = _session()
    s.frame_rgb = frame
    # 回包用**局部量**，不回读共享会话属性：:97 的 `s.frame_png` 会在两个并发
    # 抽帧的 A/B 之间交错（A 写在 88↔97 之间被 B 覆盖）⇒ A 的回包内容变成 B
    # 的帧。s.frame_png 照旧赋值（预览/契约层都读它），只是回包不从这里取。
    png = _encode(frame)
    s.frame_png = png
    s.points = []
    s.seg_t = float(t or 0)
    s.preview_png = None            # 换参考帧 = 旧预览作废（与旧界面同款）
    s.rev += 1
    # no-store：这张图活在会话内存里（不落盘），换视频/重算后**同一 URL 的内容就会变**——
    # 只靠 ?v=<rev> 击穿不够（rev 不随回包下发，前端记不住当前值：视频 A 抽帧 t=1.0 后
    # 切到视频 B 跳同一 t，同一 URL 命中 A 的缓存 → 界面显示上一支视频的参考帧）。
    # 禁缓存从根上消掉这类串台，且不改回包结构。
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@router.get("/preview.png")
def preview_png():
    png = _session().preview_png
    if png is None:
        raise HTTPException(404, "还没有可看的预览——先点「生成标定」")
    # no-store：内容随会话变（重算/换视频），同 URL 命中缓存会显示旧预览
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@router.post("/point")
def add_point(body: PointIn):
    """记一个点。不加校验让点越攒越多——点错就删重点（DELETE 清空）。"""
    s = _session()
    s.points.append((float(body.x), float(body.y)))
    return _meta()


@router.delete("/point")
def clear_points():
    s = _session()
    s.points = []
    return _meta()


@router.post("/build")
def build(body: BuildIn):
    s = _session()
    vp = _session_video()
    if s.frame_rgb is None:
        raise HTTPException(400, "请先抽帧（选参考画面）再生成标定")
    # 写档 + 编码 + 读档回包在同一个临界区内：出锁再 _meta()（内含 _load_calib_or_none /
    # _seg_choices）可能读到另一个请求重写档的半截 → 那一次响应 ready:false，界面闪「未标定」。
    with state.video_lock(vp):
        if body.segT is not None:
            preview, msg, seg_md, _dd = A.build_segment_calibration(
                s.points, s.frame_rgb, body.segT, vp)
        else:
            preview, msg, _s_low, _v_low = A.build_calibration(
                s.points, s.frame_rgb, vp)
            seg_md = ""
        s.preview_png = _encode(preview)
        payload = _meta(msg)
        payload.update(previewUrl=_rev_url("preview"), segListMd=seg_md)
        return payload


@router.post("/refine")
def refine(body: RefineIn):
    s = _session()
    vp = _session_video()
    with state.video_lock(vp):        # 写档 + 编码 + 读档回包同一临界区（同 build）
        preview, msg = A.refine_hsv(s.frame_rgb, body.sLow, body.vLow, vp)
        s.preview_png = _encode(preview)
        payload = _meta(msg)
        payload["previewUrl"] = _rev_url("preview")
        return payload


@router.get("/segments")
def segments():
    vp = _session_video()
    return {"segments": A._seg_choices(vp), "segListMd": A._seg_list_md(vp)}


@router.delete("/segments/{i}")
def delete_segment(i: int):
    """删补充段。段 1 是主标定（由「生成标定」覆盖管理），不可删——
    del_segment 自己会报「只有一段主标定」→ 400。"""
    vp = _session_video()
    with state.video_lock(vp):
        choices = A._seg_choices(vp)
        if not 2 <= i <= len(choices) + 1:
            raise HTTPException(400, "所选段不在列表里（页面数据过期），请刷新页面重试")
        seg_md, _dd, msg = A.del_segment(choices[i - 2], vp)
        # 回读段列表也在临界区内（同 build）：出锁后重读可能撞上别的请求的删/写半截
        return {"segments": A._seg_choices(vp), "segListMd": seg_md, "msg": msg}


@router.get("/existing")
def existing():
    """查看已有标定：参考帧存档、宽色域提示、段列表、work 目录里的标定文件。"""
    vp = _session_video()
    calib_p = A._calib_file(vp)
    calib = A._load_calib_or_none(vp)
    d = A._calib_dir(vp) or ""
    files = []
    if d and os.path.isdir(d):      # calibration.json / calibration_ref.png / calibration_segN.json
        files = sorted(n for n in os.listdir(d) if n.startswith("calib"))
    return {"ready": calib is not None,
            "hasReferenceFrame": bool(calib and calib.ref_image),
            "calibT": getattr(calib, "calib_t", 0.0) if calib else 0.0,
            "calibration": ({"hsvLow": list(calib.hsv_low),
                             "hsvHigh": list(calib.hsv_high),
                             "hoopRect": list(calib.hoop_rect),
                             "frameSize": list(calib.frame_size)}
                            if calib else None),
            "wideHsvNote": A._wide_hsv_note(calib) if calib else "",
            "segments": A._seg_choices(vp),
            "segListMd": A._seg_list_md(vp),
            "calibPath": calib_p or "",
            "files": files}
