# bbh/suggest.py
"""建议钩子（spec 第二轮修订§建议钩子）：颜色排序提示，恒 low、
永不自动生效；已手动归人的球不出现（手动永远赢）。"""
from dataclasses import dataclass, field

from bbh.appearance import _goal_t, feat_similar


@dataclass
class Suggestion:
    balls: list[str]                 # 建议 same-person 的球 key
    person: str | None = None        # 该人已在 manual_groups 则带名字
    signal: str = "color_rank"
    confidence: str = "low"          # 硬编码恒 low（spec 护栏 2，不可配置）
    evidence: dict = field(default_factory=dict)


def _assigned(manual_groups: dict | None) -> set[str]:
    """manual_groups {person: [key,...]} → 已归人球集合。"""
    return {k for keys in (manual_groups or {}).values() for k in keys}


def color_neighbors(target, feats, manual_groups=None, k=5):
    """选中球的颜色相近提示：其余球按 feat_similar 降序前 k。
    两场精度≈随机（AUC 0.53-0.56）——排序提示定位，永不单独定身份。"""
    base = feats.get(target)
    if base is None or not base.ok:
        return []
    done = _assigned(manual_groups) | {target}
    sims = [(key, feat_similar(base, f)) for key, f in feats.items()
            if key not in done and f.ok]
    sims.sort(key=lambda t: -t[1])
    return sims[:k]


def wall_order(feats, traces, manual_groups=None, mode="time"):
    """未归人缩略图墙的排序（墙只排序不定身份——判断仍是用户眼睛，
    恒 low 哲学；把"像的放一起"省去翻表，扫一眼好框选）。

    universe = traces 里有缩略图且未归人的球（缩略图文件是否存在由
    UI 载荷层过滤，引擎保持纯函数可测）。
    mode: "time"=按解析时刻升序（_goal_t，防跨 100 分钟字典序错乱）；
    "spot"=按出手点坐标（同人常有惯常出手区域；x 后 y，无锚点球按
    时间垫底）；"appearance"=外观指纹贪心链——从最早的可用特征球出发，
    每步取与上一球最像的未入链球（相似度并列按 key 决胜负保确定序），
    无/坏特征球按时间垫底（特征不可用时整体退化时间序）。"""
    done = _assigned(manual_groups)
    cand = [k for k in (traces or {})
            if k not in done and (traces[k].get("thumbs"))]
    if mode == "spot":
        with_xy = [k for k in cand if traces[k].get("release_xy")]
        no_xy = [k for k in cand if not traces[k].get("release_xy")]
        with_xy.sort(key=lambda k: (traces[k]["release_xy"][0],
                                    traces[k]["release_xy"][1]))
        return with_xy + sorted(no_xy, key=_goal_t)
    if mode == "appearance":
        pool = {k: f for k, f in (feats or {}).items()
                if f.ok and k in set(cand)}
        chain = []
        if pool:
            cur_key = min(pool, key=_goal_t)
            chain.append(cur_key)
            rest = set(pool) - {cur_key}
            while rest:
                nxt = max(sorted(rest),
                          key=lambda k: feat_similar(pool[cur_key], pool[k]))
                chain.append(nxt)
                rest.remove(nxt)
                cur_key = nxt
        tail = sorted((k for k in cand if k not in set(pool)), key=_goal_t)
        return chain + tail
    return sorted(cand, key=_goal_t)


def suggest_groups(feats, manual_groups=None, k=3):
    """每个已归人 → 未归人球中颜色最像其锚球（最新有效球）的前 k 个。"""
    out = []
    for person, keys in (manual_groups or {}).items():
        anchor = None
        for key in sorted(keys, key=_goal_t, reverse=True):   # 按解析时刻排（字符串序跨 100 分钟错乱，同 gap_violations 091ae17 教训）
            f = feats.get(key)
            if f is not None and f.ok:
                anchor = (key, f)
                break
        if anchor is None:
            continue
        done = _assigned(manual_groups)
        sims = [(key2, feat_similar(anchor[1], f)) for key2, f in feats.items()
                if key2 not in done and f.ok]
        sims.sort(key=lambda t: -t[1])
        if sims:
            out.append(Suggestion(balls=[s[0] for s in sims[:k]], person=person,
                                  evidence={"anchor": anchor[0],
                                            "sims": [[s[0], round(s[1], 3)]
                                                     for s in sims[:k]]}))
    return out


def evaluate_neighbors(feats, truth_shooters, k=5):
    """真值重放基线：每球取 color_neighbors（视作无手动归人），
    precision@k=top-k 里同真值人占比；hit_rate=top-k 含至少一个同人。
    预期≈随机——作基线报告，不设 gate（spec§评估工装）。"""
    truth = truth_shooters or {}
    prec, hit, n = [], 0, 0
    for key, name in truth.items():
        f = feats.get(key)
        if f is None or not f.ok:
            continue
        nbrs = color_neighbors(key, feats, None, k)
        if not nbrs:
            continue
        n += 1
        same = sum(1 for key2, _ in nbrs if truth.get(key2) == name)
        prec.append(same / len(nbrs))
        hit += (same > 0)
    return {"precision_at_k": (sum(prec) / len(prec)) if prec else 0.0,
            "hit_rate": (hit / n) if n else 0.0, "checked": n, "k": k}
