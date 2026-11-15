"""出手人墙：未归人墙 + 已归人分组调整墙。

归人来源有二：墙上点选（keys）与剪辑台打归人标签（2026-10-11 起双向
同步，app._sync_manual_from_tag_edits）——表格**勾选**是「人工核对确认
进球」标记，和出手人无关（2026-09-06 用户定案：是否进球与出手人两码事）。

护栏 = MIN_GOAL_GAP_S 内的两球判同一人（物理上不太可能）就警示，
但不阻断（手动永远赢）。查的口径是「归人后该人整组」：单球增量时
违例跨在旧组与新球之间，只查本次点选永远不触发。

已删球两墙都不上（剪辑台删除级联出手人侧）——由 wall_payload 内部消化。
缩略图不存在的球也不上墙（trace 有但图丢了），表格标签列仍可改判它们。

请求里的点选键先按「墙上真有的项」求交（见 _wall_keys）：失配键不进
sidecar——manual.assign 只过滤「非空字符串」，不管键属不属于本视频，
裸透传会往 manual_groups.json 写墙上不存在、用户点不到也撤不掉的
幽灵成员（与 delete_events 求交口径对齐）。

排序（sort）由前端随写请求带上、回包里原样回显：前端「归人后就地替换
整墙」若拿时间序回包去顶替出手点序的墙，排序会被静默打回时间序
（旧界面保留下拉的当前值）。
"""
from fastapi import APIRouter, Query
from pydantic import BaseModel

from .. import app as A
from .. import appearance
from .. import manual_groups as manual
from . import state
from .api_session import _media_url, _session_video

router = APIRouter(prefix="/api/wall")


class WallAssignIn(BaseModel):
    """归人请求。keys 必须是数组——裸 dict 时传字符串会按**字符**落盘
    （dict.fromkeys(list("00:12.1"))），盘先被写脏、随后 gap_violations
    里 _goal_t("0") 抛 ValueError 才是 500。模型校验把它挡在写盘之前。"""
    person: str = ""
    keys: list[str] = []
    sort: str = "时间"


class WallUnassignIn(BaseModel):
    keys: list[str] = []
    sort: str = "时间"


def _items(pairs: list, person: str = "") -> list[dict]:
    """[(缩略图绝对路径, caption, key)] → 前端项。caption 里的 ⚠定位失效
    留在 caption 里展示，key 恒干净（第三位）——点选/归人只认 key。"""
    out = []
    for p, cap, key in pairs:
        d = {"key": key, "caption": cap, "thumb": _media_url(p)}
        if person:
            d["person"] = person
        out.append(d)
    return out


def _wall_visible_keys(vp: str) -> set:
    """两墙上真实存在、点得到的球键（universal）。

    判据是「墙上可见」而不是「在 events.json 行里」：缩略图文件缺失的球
    两墙都不上（wall_payload/assigned_wall_payload 都按文件存在性过滤），
    按行键过滤会收下用户根本点不到、事后也撤不掉的键；反过来，trace 有
    图但当前 events 行里没有的球（重跑检测前的旧击球）**是可点的**，
    按行键过滤会让它归不进去、已归的也移不出来——正是本过滤要消灭的
    「界面上撤不掉」。两个墙函数的候选集相同（traces 里有图、未删），
    排序模式只换顺序不换集合，故取时间序一份即可。"""
    return {k for *_, k in A.wall_payload(vp)} \
        | {k for *_, k in A.assigned_wall_payload(vp)}


def _wall_keys(vp: str, keys) -> list:
    """请求点选键 ∩ 墙上真有的项（失配键静默丢弃，与 delete_events 的
    `& set(all_keys)` 同族）。空表原样返回——让 app.assign_person /
    app.unassign_person 自己抛 gr.Error（文案与旧界面逐字相同）。"""
    ok = _wall_visible_keys(vp)
    return [k for k in dict.fromkeys(keys or []) if k in ok]


def _wall_state(vp: str, msg: str = "", sort: str = "时间") -> dict:
    # 排序模式在这里一次算清（未知值退化成时间序，不报错——前端下拉换项时
    # 不该因为拼错一个词就整墙空白），墙只建一次：老实现按时间序建一遍再
    # 全量重建一遍，366 球实盘上白算一整墙。
    mode = sort if sort in A._WALL_MODES else "时间"
    groups = manual.load(A._work_dir_for(vp))
    pairs = A.assigned_wall_payload(vp)          # 一次算好，按 key 分到各人名下
    assigned = []
    for person in sorted(groups):
        mine = [x for x in pairs if x[2] in groups[person]]
        if mine:
            assigned.append({"person": person, "items": _items(mine, person)})
    return {"unassigned": _items(A.wall_payload(vp, mode)),
            "assigned": assigned,
            "people": sorted(groups),
            "manual_groups": {p: list(ks) for p, ks in groups.items()},
            # 归人下拉的候选 = 全局标签库 ∪ 本视频已归人人名（归人时新输入的
            # 名字不进全局库，但剪辑台/墙都得能再选到）——用 app 的唯一源，
            # 别在前端自己 union，否则两页的候选集迟早分叉。
            "personChoices": A._tag_choices(vp),
            "sorts": list(A._WALL_MODES),
            "sort": mode,
            "minGapS": appearance.MIN_GOAL_GAP_S,
            "msg": msg}


def _rows(vp: str):
    _dur, rows = A.load_events_table(vp)
    return rows


@router.get("")
def get_wall(sort: str = Query("时间")):
    return _wall_state(_session_video(), sort=sort)


@router.post("/assign")
def assign(body: WallAssignIn):
    vp = _session_video()
    with state.video_lock(vp):
        rows = _rows(vp)
        msg, _rows2, _html, _choices = A.assign_person(
            vp, rows, body.person, _wall_keys(vp, body.keys))
    return _wall_state(vp, msg, body.sort)


@router.post("/unassign")
def unassign(body: WallUnassignIn):
    vp = _session_video()
    with state.video_lock(vp):
        rows = _rows(vp)
        msg, _rows2, _html, _choices = A.unassign_person(
            vp, rows, _wall_keys(vp, body.keys))
    return _wall_state(vp, msg, body.sort)


@router.get("/guard")
def guard(person: str = Query("")):
    """归人前护栏读数：该人整组的球 + 组内 MIN_GOAL_GAP 违例对。

    只读——真正的护栏判定发生在每次 assign 里（那边查的是归人后的组）。
    keys 是 manual_groups.json 的**存储序**（本次点选在前、该人其余球保序
    在后），violations 由 gap_violations 内部按解析时刻排序给出（a 恒早
    于 b）。"""
    vp = _session_video()
    keys = list(manual.load(A._work_dir_for(vp)).get((person or "").strip(), []))
    gaps = appearance.gap_violations(keys)
    msg = (f"⚠️ 护栏：{' / '.join(f'{a}↔{b} 仅 {g}s' for a, b, g in gaps)}"
           f" < {appearance.MIN_GOAL_GAP_S}s，物理上不太可能同人，请核对"
           ) if gaps else ""
    return {"person": person, "n": len(keys), "keys": keys,
            "violations": [{"a": a, "b": b, "gapS": g} for a, b, g in gaps],
            "msg": msg}
