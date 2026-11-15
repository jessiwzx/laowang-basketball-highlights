"""剪辑台的行写操作：勾选 / 标签 / 删除 / 恢复 / 漏检 / 标签库。

一个端点 = 一个用户动作，写完返回「权威行 + 汇总」（state_payload）——
前端就地替换，不自己算统计，页脚数字与 sidecar 必然一致。

键（key）恒为原始检测时刻：勾选、标签、删除、裁决、归人五套 sidecar 都
按它存（显示时刻受时刻校准偏移影响，换偏移就丢）。

前后秒（before/after）**不落盘**（与旧界面一致：那只是会话内的切片参数，
重开页面回默认 5/2），所以这里没有对应端点——切片时前端把值随请求带上。
"""
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import app as A
from . import state
from .api_gen import _finite
from .api_session import _session_video, state_payload

router = APIRouter(prefix="/api")


class EventPatch(BaseModel):
    selection: bool | None = None
    tag: str | None = None
    # ★ 精选（2026-10-23）：单条发片用。**不变式 精选 ⇒ 进片**——
    # starred=true 时服务端自动把 selection 置 1（用户点一次星就够，
    # 不用先勾再打星）；selection=false 时服务端自动清星（否则会留下
    # "精选了但没进片"，单条发出一个集锦里没有的片段）。
    starred: bool | None = None


class KeysIn(BaseModel):
    keys: list[str] = []
    # 显示偏移（与 GenIn.offset 同名同形，api_gen.py:62）：msg 里的时刻按它平移，
    # 存储键不动。不传 = 今天的行为。
    offset: float | None = None


class MissIn(BaseModel):
    text: str = ""


class TagIn(BaseModel):
    name: str = ""


# 归一分支的窄门：恰好一个冒号 + 两边是阿拉伯数字（[0-9] 而非 \d：挡住
# 全角「０:12.1」，int()/float() 是认全角的）+ 秒可带小数。
_KEY_RE = re.compile(r"^[0-9]+:[0-9]+(?:\.[0-9]+)?$")


def _canonical_key(keys: list[str], key: str) -> str | None:
    """把 URL 里的键归一成 sidecar 里的键（键 = 原始检测时刻）。

    前端拿的是 rows[].key（已是规范串 `mm:ss.s`），原样命中就原样用；
    但用户手打/hand-made 链接的 `0:12.1` 是同一时刻的另一种写法（旧界面
    的时刻列就是这么显示的，_parse_t 也按 m+:ss.s 解析），归一后仍指向
    同一行——归一化失败保持未命中，交给 404（不是 500，也不写盘）。

    窄门**只管「不在 keys 里的输入」**：规范键永远走上面的精确命中，不会
    被动到——包括秒显示成 60.0 的进位键（t=59.96 → `_fmt_t` 给出 `00:60.0`，
    它在 keys 里 → 精确命中）。秒 < 60 之外一律不收，因为 `_fmt_t` 的
    parse+fmt 在进位边界上不是定点：`0:60` 会被读成 60 秒并归一成
    `01:00.0`（t=60.0 那条**物理上不同的球**的键），`1:70.0` → `02:10.0`
    同理——重跑检测后旧面板残留的 `00:60.0` 本该 404（与 delete 路径
    「容忍陈旧键 = 404」同一语义），绝不能被归一改成误勾/误标另一条球。
    裸秒数 `12` 也不收：`/api/events/miss` 的 `_parse_freeform_t` 把 `12`
    当 12 秒，两个相邻端点对同一写法不能语义相反。
    其余（`abc`、空白、`0:inf`、超长数字串…）同样未命中 → 404。
    """
    if key in keys:
        return key
    if not _KEY_RE.match(key or ""):
        return None
    try:
        sec = float(key.split(":", 1)[1])
        if sec >= 60:                     # 进位边界不是别名，见 docstring
            return None
        cand = A._fmt_t(A._parse_t(key))
    except (ValueError, TypeError, OverflowError):
        # OverflowError：_parse_t/_fmt_t 是冻结代码，中间那步是 `t // 60`
        # 而非 `t / 60`——写成后者时 inf 会走 OverflowError 而不是 ValueError，
        # 元组里不放它会从「畸形键 → 404」变成 500。不依赖这种侥幸。
        return None
    return cand if cand in keys else None


def _rows_and_index(vp: str, key: str):
    """当前未删视图的 7 元素行 + 该键的行号（键不存在 → 404）。"""
    _dur, rows = A.load_events_table(vp)
    keys = A._raw_time_keys(vp, rows)
    hit = _canonical_key(keys, key)
    if hit is None:
        raise HTTPException(404, f"当前列表里没有这条球：{key}"
                                 "（可能已删除或重跑过检测）")
    return rows, keys.index(hit)


@router.patch("/events/{key}")
def patch_event(key: str, body: EventPatch):
    vp = _session_video()
    with state.video_lock(vp):
        rows, i = _rows_and_index(vp, key)
        rows = [list(r) for r in rows]
        sel_dirty = star_dirty = False
        if body.starred is not None:
            if body.starred:
                rows[i][0] = 1               # 不变式：精选 ⇒ 进片，自动补勾
                sel_dirty = True
            rows[i][7] = 1 if body.starred else 0
            star_dirty = True
        if body.selection is not None:
            rows[i][0] = 1 if body.selection else 0
            sel_dirty = True
            # 撤销进片必须同时清星：留着星会出现「精选了但没进片」⇒ 单条
            # 发出一个集锦里根本没有的片段。读盘纠偏只兜历史数据，兜不住
            # 本进程内紧接着的生成请求（它直接用这份 rows）。
            if not body.selection and rows[i][7]:
                rows[i][7] = 0
                star_dirty = True
        if sel_dirty:
            A._save_selection(vp, rows)   # 等价旧界面 on_table_edit 的勾选列
        if star_dirty:
            A._save_starred(vp, rows)
        if body.tag is not None:
            # set_row_tag 会顺手自动勾选 + 同步已归人球的改判 + 落盘两张表，
            # 与旧界面走的是同一个函数
            _proj, rows, _panel = A.set_row_tag(vp, rows, i, body.tag)
    return state_payload()


@router.post("/events/miss")
def record_miss(body: MissIn):
    vp = _session_video()
    with state.video_lock(vp):
        msg, _box = A.record_miss(vp, body.text)
    payload = state_payload()
    payload["msg"] = msg
    return payload


@router.delete("/events")
def delete_events(body: KeysIn):
    vp = _session_video()
    with state.video_lock(vp):
        # 不过 _rows_and_index：delete_events 的契约是「批量宁丢失配键不
        # 中断整批」（重跑检测后旧面板残留的键被丢弃而不是报错），
        # 也不对已删球重复报 404。空 keys → 它自己抛「先在面板勾选」→ 400。
        _dur, rows = A.load_events_table(vp)
        msg, _rows2 = A.delete_events(vp, rows, body.keys,
                                      _finite(body.offset) or 0.0)
    payload = state_payload()
    payload["msg"] = msg
    return payload


@router.post("/events/restore")
def restore_events(body: KeysIn | None = None):
    """恢复已删球。不带 keys = 原全量语义（旧界面的「↩ 恢复已删」）；
    带 keys = 只恢复这几条（剪辑台 Ctrl+Z 撤销一次删除走这条）。

    恢复的球一律回到「未勾选/未归人/无标签」（app.restore_deleted 的既有
    语义：删除时的清理不回滚）——msg 里如实说明，别让用户以为全还原了。
    """
    vp = _session_video()
    with state.video_lock(vp):
        keys = [k for k in ((body.keys if body else None) or [])
                if isinstance(k, str)]
        if not keys:
            restored = A.restore_deleted(vp)          # 内部空清单 → gr.Error → 400
        else:
            deleted = A._load_deleted(vp)
            wanted = set(keys)
            hit = [k for k in deleted if k in wanted]
            if not hit:
                raise HTTPException(
                    400, f"这些球不在已删清单里：{keys[0]}（可能已恢复）")
            A._save_deleted(vp, [k for k in deleted if k not in wanted])
            restored = hit
    payload = state_payload()
    payload["msg"] = f"↩ 已恢复 {len(restored)} 条进球（回到未勾选/未归人状态）"
    return payload


@router.post("/tags")
def add_tag(body: TagIn):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(400, "标签名不能为空")
    # tags.json 在仓库根、跨视频共用，不属于任何一个视频——所以锁键用空串
    # （state.video_lock 对空路径退化成同一个模块级 RLock）。它同样是
    # 「读整档 → 改 → 写回」，而前端轮询 _load_tags 会并发打开同一个文件：
    # 交错写坏后 _load_tags 把损坏当空 → 整个标签库静默清空，只剩刚加的这条。
    # 读回也必须在锁内（锁外读可能读到别的写者刚截断的文件）。
    with state.video_lock(""):
        A.add_tag_to_library(name)
        # 与其他 12 个写端点同契约：写完回「权威行 + 页脚汇总」（state_payload），
        # 前端就地替换整份状态。之前这里只回 {"tags": ...}，是本模块唯一一个
        # 形状不合写侧契约的端点（模块 docstring 也是这么写的）。
        return state_payload()


@router.get("/events")
def get_events():
    """行数据（spec 里点名的端点）。内容 = /api/state 的超集里的行部分，
    走同一个装配函数——两条路径永不分叉。前端通常只打 /api/state。"""
    return state_payload()
