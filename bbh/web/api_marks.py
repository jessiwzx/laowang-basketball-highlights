"""裁决反馈（调优标注）与误标标记（渲染质控）——两组独立的 sidecar。

裁决 = ✅「这条是进球」（低置信被门槛拦下的真球靠它翻案）/ ❌「未进球」，
存 feedback.json，带该行判定证据快照（防之后重跑检测覆盖证据）。
误标 = 缩略图有误 / 十字有误，存 thumb_flags.json / cross_flags.json，
两类可同标一球、互不影响。

三者都不改 events.json：反馈是给判定调优的、误标是给渲染质控的、
进片勾选是给生成的——各改各的档，复盘时可分别归因。
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import app as A
from . import state
from .api_events import _rows_and_index
from .api_gen import _finite
from .api_session import _session_video, state_payload

router = APIRouter(prefix="/api")

_VERDICTS = ("goal", "not_goal")
_FLAGS = {
    "thumb": (A.THUMB_FLAG_FILE, A.toggle_thumb_flag),
    "cross": (A.CROSS_FLAG_FILE, A.toggle_cross_flag),
}


class VerdictIn(BaseModel):
    verdict: str = ""
    # 显示偏移（与 GenIn.offset 同名同形）：msg 里的时刻按它平移，t_str 仍原始。
    offset: float | None = None


class FlagIn(BaseModel):
    flag: str = ""


@router.post("/feedback/{key}")
def post_feedback(key: str, body: VerdictIn):
    if body.verdict not in _VERDICTS:
        raise HTTPException(400, f"verdict 只能是 {' 或 '.join(_VERDICTS)}，"
                                 f"收到「{body.verdict}」")
    vp = _session_video()
    with state.video_lock(vp):
        rows, i = _rows_and_index(vp, key)
        msg = A.mark_verdict(vp, rows, i, body.verdict,
                             _finite(body.offset) or 0.0)
    payload = state_payload()
    payload["msg"] = msg
    return payload


@router.post("/flags/{key}")
def post_flag(key: str, body: FlagIn):
    entry = _FLAGS.get(body.flag)
    if entry is None:
        raise HTTPException(400, f"flag 只能是 {' 或 '.join(_FLAGS)}，"
                                 f"收到「{body.flag}」")
    _filename, toggle = entry
    vp = _session_video()
    with state.video_lock(vp):
        rows, i = _rows_and_index(vp, key)
        btn, md = toggle(vp, rows, i)
    payload = state_payload()
    # 标上时给「⚠ 已标记 N 张缩略图有误：键…」清单；取消后清单空 → 退回按钮名
    payload["msg"] = md or btn
    return payload
