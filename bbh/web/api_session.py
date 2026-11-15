"""会话与全量状态：三页打开时都要的一份数据。

行数据在这里装配成结构化 dict（不是 app.py 给 Gradio Dataframe 的 7 元素
数组）：前端要字段名不要列序，判定依据/证据/出手人一并带上，一次拿全，
切行不再打服务端。所有数字都由服务端算（同一个 _table_summary_md /
build_goal_stats），前端不做统计——与 sidecar 必然一致。

不做视频存在性校验：与旧界面一致（填错路径照样提示「新视频」，点检测时
才报「视频不存在」）。
"""
import json
import os
import urllib.parse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import app as A
from .. import shooter
from ..goal_decider import _pos_score, _side_score
from ..net_motion import net_term
from . import state

router = APIRouter(prefix="/api")


def _terms(e) -> dict:
    """依据卡算式分项（v18 含网分）：conf = base + up + down + pos + net，
    与 goal_decider/net_motion 同名项逐字同源。旧版 events.json（≤v17）的
    conf 不含网分（和式≠conf）→ legacy=True，前端标注「旧版结果」——
    别让用户对着 0.75 ≠ 0.3+0.2+0.2+0.05−0.20 以为算错了。span 按 3 位
    落档重算，刀锋边界可能偶发 legacy=True，无害（只是多一句标注）。"""
    up = _side_score(e.above_n or 0, e.above_span or 0.0)
    down = _side_score(e.below_n or 0, e.below_span or 0.0)
    pos = _pos_score(abs(e.cross_off))
    net = net_term(e)
    return {"base": 0.30, "up": up, "down": down, "pos": pos, "net": net,
            "legacy": abs(0.30 + up + down + pos + net - e.confidence) > 0.005}

PREFS_FILE = "ui_prefs.json"          # 仓库根，与 tags.json 同级（不进 work/）


class SessionIn(BaseModel):
    videoPath: str = ""


def _media_url(p: str | None) -> str | None:
    """work/ 内文件的浏览器 URL（带 mtime 击穿缓存，与 _file_url 同口径）。"""
    if not p or not os.path.exists(p):
        return None
    q = urllib.parse.quote(os.path.abspath(p).replace("\\", "/"), safe="/:")
    return f"/media/work?path={q}&v={os.path.getmtime(p):.0f}"


def _session_video() -> str:
    vp = A.clean_path(state.SESSION.video_path)
    if not vp:
        raise HTTPException(400, "请先填视频路径（本机文件全路径）")
    return vp


def _remember_path(vp: str) -> None:
    """记住上次路径（界面偏好，独立文件——work/ 的 sidecar 一个字段不加）。"""
    try:
        prefs = {}
        if os.path.exists(PREFS_FILE):
            prefs = json.load(open(PREFS_FILE, encoding="utf-8")) or {}
        if not isinstance(prefs, dict):
            prefs = {}       # 手改/他程序写坏（42、"x"、[1]）→ 当空档重写，
            #                  否则 prefs["lastVideo"] 抛 TypeError 冒成 500
        prefs["lastVideo"] = vp
        with open(PREFS_FILE, "w", encoding="utf-8") as f:
            json.dump(prefs, f, ensure_ascii=False, indent=1)
    except (OSError, ValueError):
        pass          # 偏好写不进去不该挡住开视频


def _last_path() -> str:
    try:
        return json.load(open(PREFS_FILE, encoding="utf-8")).get("lastVideo", "")
    except (OSError, ValueError, AttributeError):
        return ""


def build_rows(video_path: str) -> tuple[list[dict], float]:
    """一行一条球：键/时刻/档位/置信度/勾选/标签/前后秒 + 证据 + 出手人。

    key 用 _raw_time_keys（原始检测时刻）——勾选、标签、删除、裁决、归人
    五套 sidecar 全按它存；显示时刻受时刻校准偏移影响，不能当键。
    """
    vp = A.clean_path(video_path)
    dur, rows = A.load_events_table(vp)          # 无结果 → gr.Error → 400
    keys = A._raw_time_keys(vp, rows)
    data = A._filter_deleted(A._load_events_or_none(vp), vp) or {"events": []}
    evs = data.get("events") or []
    work = A._work_dir_for(vp)
    traces = (shooter.load_shooters(work) or {}).get("traces") or {}
    out = []
    for i, r in enumerate(rows):
        key = keys[i]
        e = evs[i] if i < len(evs) else None
        tr = traces.get(key) or {}
        has_ev = e is not None and e.cross_off is not None
        out.append({
            "key": key, "t": r[1], "status": r[2], "conf": float(r[3]),
            "selection": bool(r[0]), "before": r[4], "after": r[5],
            "tag": r[6] or "",
            # ★ 精选（第 8 列，2026-10-23）：缺列按未选中——老调用方/老测试
            # 手造的 7 列行不该因为多了这一维就炸。
            "starred": bool(r[7]) if len(r) > 7 else False,
            "thumb": _media_url(A._gif_or_none(work, key)),
            "shooter": ({
                "status": tr.get("status"),
                "dot": shooter.STATUS_TEXT.get(tr.get("status"), "⚪"),
                "releaseT": tr.get("release_t"),
                "thumbs": [_media_url(os.path.join(work, rel))
                           for rel in (tr.get("thumbs") or [])],
            } if tr else None),
            "ev": ({
                "crossOff": e.cross_off, "aboveN": e.above_n,
                "belowN": e.below_n, "aboveSpan": e.above_span,
                "belowSpan": e.below_span, "descentPx": e.descent_px,
                "belowBig": e.below_big, "popBack": e.pop_back,
                "lowReason": e.low_reason,
            } if has_ev else None),
            "net": ({
                "post": e.net_post, "ctrl": e.net_ctrl, "local": e.net_local,
            } if e is not None and e.net_post is not None else None),
            # 与 goal_decider 的同名项逐字同源（conf = 0.3 + up + down + pos + net）。
            # 旧版 events.json 可能只有 cross_off 没有 above_n/span → 缺项按 0
            # 计（那几档本来就等于没证据，不是"证据为 0 分"的误读）。
            "terms": (_terms(e) if has_ev else None),
            # 判定依据直接用手里的 e——与 app._reason_for 同源同义（它内部也是
            # _filter_deleted(_load_events_or_none) 后取 events[i]，与本函数的
            # evs 同一视图、同一顺序），逐行重读重解析 events.json 是纯冗余：
            # 366 事件的档实测 299 行 × ~2.5ms ≈ 0.74s，而 state_payload 每次
            # 开页/每次写操作返回都要吐一遍。改一处要改两处。
            "reason": A._reason_text(e) if e is not None else "",
        })
    return out, dur


def _feedback_summary(vp: str) -> dict:
    fb = A._load_feedback(vp)
    n_goal = sum(1 for v in fb["verdicts"] if v.get("verdict") == "goal")
    return {"goal": n_goal, "no": len(fb["verdicts"]) - n_goal,
            "miss": len(fb["misses"]), "summaryMd": A._fp_summary(vp)}


def _verdict_keys(vp: str) -> dict:
    """每行裁决态：行键 → "goal" / "not_goal"（表格「裁决」列与右栏按钮按行渲染用）。

    汇总结数不够用：刷新页面后前端要知道**哪几行**已裁决，否则会出现
    「页脚说有 3 条反馈、表格一个 ✔ 都没有」。
    键用记录里的 t_str（写裁决那一刻这一行的时间列串），**不要**用
    _fmt_t(v["t"]) 复算：进位边界上 parse+fmt 不是定点（t=59.96 的键是
    "00:60.0"，复算给出 "01:00.0" = 另一颗球）。旧格式（false_positives
    迁移）没有 t_str → 退回 _fmt_t(t)；再取不到就跳过（汇总结数照旧）。
    """
    out = {}
    for v in A._load_feedback(vp)["verdicts"]:
        verdict = v.get("verdict")
        if verdict not in ("goal", "not_goal"):
            continue
        k = v.get("t_str")
        if not isinstance(k, str) or not k:
            try:
                k = A._fmt_t(float(v["t"]))
            except (KeyError, TypeError, ValueError):
                continue
        out[k] = verdict
    return out


def state_payload() -> dict:
    """三页共用的全量状态（写操作返回后也调它：前端就地替换，不自己算）。"""
    vp = A.clean_path(state.SESSION.video_path)
    base = {"videoPath": vp, "lastVideo": _last_path(), "loaded": False,
            "hint": "请先填视频路径（本机文件全路径）", "rows": [],
            "duration": 0.0, "summaryMd": "", "goalStatsMd": "",
            "tags": A._load_tags(), "deleted": [], "flags": {"thumb": [], "cross": []},
            "feedback": {"goal": 0, "no": 0, "miss": 0, "summaryMd": ""},
            "verdicts": {},
            "calib": {"ready": False, "segments": []}}
    if not vp:
        return base
    # probe_existing 给的是旧界面那几句提示（已载入已有结果 / 新视频 / 还没标定）
    _dur_md, _proj, rows7, _cur, _panel, hint = A.probe_existing(vp)
    base.update(hint=hint, tags=A._load_tags(), deleted=A._load_deleted(vp),
                flags={"thumb": A._load_flag_set(vp, A.THUMB_FLAG_FILE),
                       "cross": A._load_flag_set(vp, A.CROSS_FLAG_FILE)},
                feedback=_feedback_summary(vp),
                verdicts=_verdict_keys(vp),
                calib={"ready": A._calib_ready(vp),
                       "segments": A._seg_choices(vp)})
    if rows7:
        rows, dur = build_rows(vp)
        base.update(loaded=True, rows=rows, duration=dur,
                    summaryMd=A._table_summary_md(dur, rows7),
                    goalStatsMd=A.build_goal_stats(vp, rows7))
    return base


@router.post("/session")
def open_session(body: SessionIn):
    vp = A.clean_path(body.videoPath)
    if not vp:
        raise HTTPException(400, "请先填视频路径（本机文件全路径）")
    state.SESSION.video_path = vp
    state.SESSION.frame_rgb = None       # 换视频 = 标定页一切归零
    state.SESSION.frame_png = None
    state.SESSION.preview_png = None
    state.SESSION.points = []
    state.SESSION.seg_t = 0.0
    # ui_prefs.json 在仓库根、跨视频共用（与 tags.json 同级同形状的
    # 「读整档 → 改 → 写回」），不属于任何一个视频——所以锁键用空串
    # （state.video_lock 对空路径退化成同一个模块级 RLock）。写的是盘上档，
    # 只圈这一句：state.SESSION 的赋值是会话状态，不是盘上档。
    with state.video_lock(""):
        _remember_path(vp)
    return state_payload()


@router.get("/state")
def get_state():
    return state_payload()


def _reveal(path: str) -> None:
    """真的去「打开」这个目录（Windows 资源管理器）。

    ⛔ 单独成一个函数是为了**可注入**：测试替换它，不真弹资源管理器。
    `os.startfile` 只有 Windows 有；本项目的打包版只在 Windows 跑，别的
    平台上直接报错比静默什么都不做要好。
    """
    startfile = getattr(os, "startfile", None)
    if startfile is None:
        raise RuntimeError("打开目录只支持 Windows")
    startfile(path)


def _workspace_root() -> str:
    """工作区根：入口 `prepare_cwd()` 切过去、`work/` 与 `tags.json` 所在的
    那个目录（非冻结＝仓库根，冻结＝`<exe>/data`）。

    读的是**进程自己的 cwd**，不是请求参数——与 `media._work_root()` 同口径：
    工作区由入口保证，「打开哪个目录」不该由浏览器说了算。
    """
    return os.path.realpath(os.getcwd())


@router.post("/session/open-data-dir")
def open_data_dir():
    """「打开数据目录」按钮：在资源管理器里打开工作区根。

    ⛔ 端点**不收任何参数**（查询串与 body 都忽略）：能打开的目录只有工作区
    根一个——与 `media.py` 同一条收束思路，路径来自前端就等于把本机任意目录
    交给浏览器。`work/` 动辄几个 GB，用户想自己看看成品在哪，这是他该有的
    能力；给按钮一个自由路径参数就不是了。
    """
    root = _workspace_root()
    try:
        _reveal(root)
    except Exception as ex:      # 兜底：打不开就报文案，绝不让它变成 500
        raise HTTPException(400, f"打开数据目录失败：{ex}") from ex
    return {"msg": "已打开", "path": root}
