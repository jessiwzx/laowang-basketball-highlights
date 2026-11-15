# -*- coding: utf-8 -*-
r"""静止链过滤策略（默认 ``b1m``）：补齐「被静止链误删的真球」。

背景
----
`bbh.goal_decider.drop_static_positions` 用「同格静止链」滤掉停在原地的检出
（筐下固定橙色物、篮网结点、被抱住的球）：格边长 = 1/4 筐宽；同格相邻检出
间隔 ≤0.5s 且逐点步距 ≤3px 续链；链长 ≥3 → **整链丢弃**。

问题（2026-10-10 逐帧取证）：**真球入筐前会"粘"在筐沿 0.4~0.9s**（相邻帧位移
≤3px），正好踩中静止链判据 → 被当固定物整链删掉 → 穿越对的「上方点」消失
→ 进球被丢。不是"上游假框连累"，是球自己被删。

三条策略（都只重写过滤，不碰判定器/几何/检出）
--------------------------------------------
- ``orig`` : 原样委托 `goal_decider.drop_static_positions`（基线）
- ``b1``   : orig + 把「**整条点都在筐口平面以上**（y < rim_y）」的链整条撤回
- ``b1m``  : ``b1`` 的**最小版**——同条件，但每条链只补回「够判定用」的 2 点
             （链尾 + 它之前 ≥0.2s 的最近一点），避免把 ``above_n`` 抬过判定器
             P1d 长悬停降档线（**默认策略**）

实测（7 场 890 球，对称口径）：``b1m`` 相对 ``orig`` 🟢 702→**708（+6）**，
配对不降、幽灵 high 不增；``b1`` 整条补回会被判定器 P1d 反噬（08-22 两颗真
进球 high→review）；``traj``（单链运动外推）已实测否证，不启用。

用法
----
    from .static_filter import apply
    kept = apply(positions, calib)          # 用默认策略（env BBH_STATIC_FILTER）
    kept = apply(positions, calib, "orig")  # 指定策略
"""
import os

from .goal_decider import (CONT_SPAN_SEC, STATIC_BURST_GAP, STATIC_BURST_MIN_N,
                           STATIC_JITTER_PX, STATIC_R_RATIO)
from .goal_decider import drop_static_positions as _ORIG

RIM_Y_RATIO = 0.35          # 与 bbh.detector / goal_decider 同源
POLICIES = ("orig", "b1", "b1m")

POLICY = (os.environ.get("BBH_STATIC_FILTER") or "b1m").strip().lower()
if POLICY not in POLICIES:
    POLICY = "b1m"


# ------------------------------------------------------------------ 链划分
def build_chains(positions, calib):
    """复刻 `drop_static_positions` 的格子 + 静止链逻辑。

    返回 ``(static_ids, chains, rim_y)``：``static_ids`` 是原策略会删的点索引、
    ``chains`` 是每条静止链（点索引，按 t 升序）、``rim_y`` 是筐口平面。
    """
    _, hy, hw, hh = calib.hoop_rect
    r = max(4.0, hw * STATIC_R_RATIO)
    cells: dict[tuple[int, int], list[int]] = {}
    for i, p in enumerate(positions):
        cells.setdefault((int(p.x // r), int(p.y // r)), []).append(i)

    static_ids: set[int] = set()
    chains: list[list[int]] = []

    def close(chain):
        if len(chain) >= STATIC_BURST_MIN_N:
            chains.append(list(chain))
            static_ids.update(chain)

    for idxs in cells.values():          # 输入已按 t 升序 → 格内索引亦升序
        chain = [idxs[0]]
        for i in idxs[1:]:
            prev, cur = positions[chain[-1]], positions[i]
            if (cur.t - prev.t > STATIC_BURST_GAP
                    or abs(cur.x - prev.x) > STATIC_JITTER_PX
                    or abs(cur.y - prev.y) > STATIC_JITTER_PX):
                close(chain)
                chain = []
            chain.append(i)
        close(chain)

    rim_y = hy + hh * RIM_Y_RATIO
    return static_ids, chains, rim_y


# ------------------------------------------------------------------ 救回判据
def _rescue_b1(positions, chains, rim_y):
    """整条点在筐口平面以上 → 整条撤回（球在筐沿悬停的形态）。"""
    out = set()
    for ch in chains:
        if max(positions[i].y for i in ch) < rim_y:
            out.update(ch)
    return out


def _rescue_b1m(positions, chains, rim_y):
    """**最小撤回**：整条在筐口平面以上的链，只补回「够判定用」的 2 个点。

    ``b1`` 把悬停链整条补回后 ``above_n`` 从 4/6 跳到 10，越过判定器 P1d 长
    悬停降档线（``P1_DEMOTE_ABOVE_N=8`` 且下方跨度短）→ 08-22 两颗真进球被
    降成 review。P1d 是在「悬停点已被删干净」的点流上标定的，整条补回会破坏
    它的隐含前提。这里只补链尾 + 它之前 ≥``CONT_SPAN_SEC``(0.2s) 的最近一点：
    判定器「同侧 ≥2 点且跨度 ≥0.2s」拿满证据分所需的最小集合。
    """
    out = set()
    for ch in chains:
        if max(positions[i].y for i in ch) >= rim_y:
            continue
        last = ch[-1]
        out.add(last)
        t_last = positions[last].t
        for i in reversed(ch[:-1]):
            if t_last - positions[i].t >= CONT_SPAN_SEC:
                out.add(i)
                break
    return out


# ------------------------------------------------------------------ 入口
def apply(positions, calib, policy=None):
    """按策略过滤点流（签名与 `drop_static_positions` 一致）。"""
    policy = policy or POLICY
    if policy == "orig":
        return _ORIG(positions, calib)
    if policy not in POLICIES:
        raise ValueError(f"未知策略：{policy}")
    if len(positions) < 2:
        return list(positions)
    _static_ids, chains, rim_y = build_chains(positions, calib)
    rescue = (_rescue_b1(positions, chains, rim_y) if policy == "b1"
              else _rescue_b1m(positions, chains, rim_y))
    return [p for i, p in enumerate(positions)
            if i not in _static_ids or i in rescue]
