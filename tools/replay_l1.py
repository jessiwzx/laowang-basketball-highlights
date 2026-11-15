# -*- coding: utf-8 -*-
r"""L1 判定器重放：positions.json + calibration → 完整链路（判定器 + 网分 + 冷却二趟 + 段合并 + 网簇注入）。

什么时候必须用 L1（而不是 L0）
------------------------------
L0（`tools/offline_replay.py`）只拿 `events.json` 里已算好的分数表重算，验证不了
「分数变化之后结构规则（冷却 / 段合并 / 兜底注入）会不会跟着变」。凡是改动可能
改变「有哪些行」的，都必须走这里：
`CROSS_KILL_OFF`、冷却身份判据、几何门槛、实体与下落校验、标定、任何在判定器里
新增/删除分支的改动。

只读保证
--------
所有输入（positions / calibration / net sidecar / deleted）**复制**到 `诊断/_l1/<场>/`
临时目录，`run_detection` 的输出也写在那里。生产 `work/<场>/` 下一个字节都不动。

用法
----
    .venv\Scripts\python tools/replay_l1.py --gate
    .venv\Scripts\python tools/replay_l1.py --variant short=0.17
    .venv\Scripts\python tools/replay_l1.py --gate --videos 2026-11-06_1

`--gate` 是每次改码后的第一道闸门：用当前代码重放，必须与生产 `events.json`
逐条一致（行数 / status / conf 全同）。不一致说明要么代码漂移，要么生产产物
不是当前版本 —— 两者下任何变体结论都无意义。

新增变体
--------
`main()` 里目前只实现了 `short=<值>` 一个变体（侧分短档）。要验证别的常量时，
在 `main()` 的 `k == "short"` 旁边加分支，并**用代码当前值做锚**（见 `side_patch`
的注释）—— 硬编码旧值会让变体静默失效，回退验证变假通过。
"""
import argparse
import filecmp
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import bbh.goal_decider as gd                       # noqa: E402
import bbh.pipeline as P                            # noqa: E402
from bbh import calibration as CAL                  # noqa: E402
import eval_lib as E                                # noqa: E402
import eval_sel as ES                               # noqa: E402

WORK = os.path.join(ROOT, "work")
TMP = os.path.join(ROOT, "诊断", "_l1")             # gitignore: 诊断/**/_l1/
UP_WORK = r"D:\claude code\视频剪辑\work"
OLD_ARM = r"D:\claude code\yolo\eval_out\v20_c03_s1"   # 老六场 v20 产物（含 positions）

OLD = list(ES.VIDEOS_SEL_OLD)
NEW = list(ES.VIDEOS_SEL_NEW)

# 重放只需要这些输入；其余（clips / preview / 人员）与判定无关
NEED_FILES = ("positions.json", "calibration.json", "net_signals.json",
              "net_clusters.json", "deleted.json")   # deleted 是 B2 注入的排除清单
NEED_PREFIX = ("calibration_seg", "calibration_ref")


def work_of(video):
    """→ (按优先级排列的输入目录列表, 生产 events.json 路径)

    老六场分裂在两个根：positions/net sidecar 在 yolo 归档臂，标定与
    deleted 清单在上游 work；新场全在本仓 work。
    """
    mine = os.path.join(WORK, video)
    if os.path.exists(os.path.join(mine, "positions.json")):
        return [mine], os.path.join(mine, "events.json")
    arm = os.path.join(OLD_ARM, video)
    if os.path.exists(os.path.join(arm, "positions.json")):
        return [arm, os.path.join(UP_WORK, video)], os.path.join(arm, "events.json")
    up = os.path.join(UP_WORK, video)
    return [up], os.path.join(up, "events.json")


def prepare(video, srcs):
    """复制重放输入到临时目录（幂等；多源先到先得）。"""
    dst = os.path.join(TMP, video)
    os.makedirs(dst, exist_ok=True)
    for name in NEED_FILES:
        d = os.path.join(dst, name)
        if os.path.exists(d):
            continue
        for src in srcs:
            s = os.path.join(src, name)
            if os.path.exists(s):
                shutil.copy2(s, d)
                break
    for src in srcs:                       # 标定分段与参考帧按前缀全收
        for name in os.listdir(src):
            if name.startswith(NEED_PREFIX):
                d = os.path.join(dst, name)
                if not (os.path.exists(d) and filecmp.cmp(os.path.join(src, name), d,
                                                          shallow=True)):
                    shutil.copy2(os.path.join(src, name), d)
    # 删掉上一轮写下的 events.json —— 否则 run_detection 会按身份直接复用，
    # 变体根本不会重算（踩过：1.4 秒跑完 6 场就是这个原因）
    stale = os.path.join(dst, "events.json")
    if os.path.exists(stale):
        os.remove(stale)
    return dst


def video_path_of(srcs):
    for src in srcs:
        p = os.path.join(src, "positions.json")
        if os.path.exists(p):
            return json.load(open(p, encoding="utf-8"))["video"]
    return None


def side_patch(short=None):
    """→ 侧分函数补丁（short=None 时返回原函数）。只动「点数够、跨度不足」那一档。

    以**代码当前值** `gd.SIDE_SHORT` 为锚，而不是硬编码某个数字 —— 否则
    v23 之后（当前值 0.17）传 0.14 会静默失效，回退验证就成了假通过。
    """
    orig = gd._side_score
    if short is None:
        return orig

    def patched(n, span):
        v = orig(n, span)
        return short if abs(v - gd.SIDE_SHORT) < 1e-9 else v

    return patched


def _patch_all(short):
    """同时替换 goal_decider 与 net_clusters 两处引用。

    net_clusters 是 `from .goal_decider import _side_score`（导入即绑定对象），
    只改 gd._side_score 对 B2 注入行无效 —— 踩过：回退验证残 10 条 conf 差
    0.03，全是注入行。
    """
    import bbh.net_clusters as nc
    origs = (gd._side_score, nc._side_score)
    fn = side_patch(short)
    gd._side_score = fn
    nc._side_score = fn
    return origs


def _restore(origs):
    import bbh.net_clusters as nc
    gd._side_score, nc._side_score = origs


def run_one(video, short=None):
    """重放一场 → ((events 文档, eval 口径行), 警告)。short=None 即原样基线。"""
    srcs, prod_events = work_of(video)
    if not any(os.path.isdir(s) for s in srcs):
        return None, f"缺目录 {srcs}"
    vp = video_path_of(srcs)
    if not vp:
        return None, "缺 positions.json"
    if not os.path.exists(vp):
        # 老六场源视频已不在本机。sidecar 全命中时判定不需要读帧 ——
        # 只打警告继续，真要读帧会在 net 测量那步自己失败（fail-open 或报错）。
        warnings_note = f"源视频缺失（sidecar 命中则不影响）：{vp}"
    segs = None
    for src in srcs:
        if os.path.exists(os.path.join(src, "calibration.json")):
            segs = CAL.load_segments_dir(src)
            break
    if not segs:
        return None, "缺标定（无法 L1 重放）"
    dst = prepare(video, srcs)

    origs = _patch_all(short)
    try:
        ev_path, warn = P.run_detection(vp, segs, dst)
    finally:
        _restore(origs)
    doc = json.load(open(ev_path, encoding="utf-8"))
    # 立刻按 eval 口径取行（deleted + 间隙球剔除）——临时目录路径是固定的，
    # 晚一步读就会被下一轮重放覆盖（踩过：基线打印出变体的数字）
    rows = E.load_rows(ev_path, video)
    return (doc, rows), warn


def key_rows(doc):
    """events → {round(t,2): (status, conf)}，只留判定器关心的字段。"""
    return {round(e["t"], 2): (e.get("status"), e.get("confidence"))
            for e in doc["events"]}


def gate_one(video):
    """基线重放 vs 生产 events.json 逐条比对。"""
    _srcs, prod_events = work_of(video)
    if not os.path.exists(prod_events):
        return video, "skip", "无生产 events.json"
    doc, warn = run_one(video)
    if doc is None:
        return video, "fail", warn
    doc = doc[0]
    prod = json.load(open(prod_events, encoding="utf-8"))
    a, b = key_rows(prod), key_rows(doc)
    only_a = sorted(set(a) - set(b))
    only_b = sorted(set(b) - set(a))
    diff_st = [(t, a[t][0], b[t][0]) for t in set(a) & set(b) if a[t][0] != b[t][0]]
    diff_cf = [(t, a[t][1], b[t][1]) for t in set(a) & set(b) if a[t][1] != b[t][1]]
    ok = not (only_a or only_b or diff_st or diff_cf)
    msg = (f"双方 {len(a)}/{len(b)} 行 · 仅生产 {len(only_a)} · 仅重放 {len(only_b)}"
           f" · status 差 {len(diff_st)} · conf 差 {len(diff_cf)}")
    if not ok:
        msg += f"\n      样例 status={diff_st[:3]} conf={diff_cf[:3]} 独有={only_a[:3]}/{only_b[:3]}"
    return video, ("ok" if ok else "DIFF"), msg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", action="store_true", help="基线重放闸门（须 100%% 一致）")
    ap.add_argument("--variant", default=None, help="变体，如 short=0.17")
    ap.add_argument("--videos", nargs="*", default=None)
    args = ap.parse_args()

    os.makedirs(TMP, exist_ok=True)
    vids = args.videos or (OLD + NEW)
    if args.gate:
        bad = 0
        for v in vids:
            name, st, msg = gate_one(v)
            print(f"[{st:>4}] {name:<14} {msg}")
            bad += st not in ("ok", "skip")
        print(f"\n闸门：{len(vids) - bad}/{len(vids)} 通过")
        return 1 if bad else 0

    if not args.variant:
        ap.print_help()
        return 2
    k, val = args.variant.split("=")
    assert k == "short", f"暂只支持 short=<值>，收到 {args.variant}"
    short = float(val)
    print(f"变体 short={short}\n")

    per_b, per_v, rows_v = {}, {}, {}
    for v in vids:
        b, w1 = run_one(v)
        a, w2 = run_one(v, short=short)
        if b is None or a is None:
            print(f"  skip {v}: {w1 or w2}")
            continue
        # 与 eval_lib 同口径剔除（deleted + 比赛间隙球）后再算指标，
        # 否则已删球会被算成假绿（首版就多出 79 行）
        rows_b, rows_a = b[1], a[1]
        goals, _src = ES.load_selection(v, [UP_WORK, WORK])
        if goals:
            per_b[v] = ES.sel_one(goals, rows_b, ES.SEL_TOL)
            per_v[v] = ES.sel_one(goals, rows_a, ES.SEL_TOL)
        kb, ka = key_rows(b[0]), key_rows(a[0])
        up = [(t, ka[t][0], kb[t][0]) for t in ka if t in kb and ka[t][0] != kb[t][0]]
        rows_v[v] = up
        print(f"  {v:<14} 行 {len(kb)}→{len(ka)}  改档 {len(up)}", end="")
        if v in per_b and v in per_v:
            x, y = per_b[v], per_v[v]
            print(f"  查准 {x['precision']:.1%}→{y['precision']:.1%}"
                  f"  覆盖 {x['auto_cover']:.1%}→{y['auto_cover']:.1%}"
                  f"  命中 {x['auto_hit']}→{y['auto_hit']}"
                  f"  假绿 {x['false_green']}→{y['false_green']}")
        else:
            print("  （无 selection，仅监看改动数）")

    print()
    for tag, g in (("老六", OLD), ("新六", NEW), ("12 场", OLD + NEW)):
        sb = ES.sel_agg(per_b, [v for v in g if v in per_b])
        sv = ES.sel_agg(per_v, [v for v in g if v in per_v])
        print(f"{tag:<5} 基线 自动档{sb['high_total']:>5} 命中{sb['auto_hit']:>5} "
              f"假绿{sb['false_green']:>4} 降级{sb['degraded']:>4} 查准{sb['precision']:>7.2%} "
              f"覆盖{sb['auto_cover']:>7.2%}")
        print(f"{'':<5} 变体 自动档{sv['high_total']:>5} 命中{sv['auto_hit']:>5} "
              f"假绿{sv['false_green']:>4} 降级{sv['degraded']:>4} 查准{sv['precision']:>7.2%} "
              f"覆盖{sv['auto_cover']:>7.2%}")
        print(f"{'':<5} Δ    自动档{sv['high_total']-sb['high_total']:+d} "
              f"命中{sv['auto_hit']-sb['auto_hit']:+d} "
              f"假绿{sv['false_green']-sb['false_green']:+d} "
              f"降级{sv['degraded']-sb['degraded']:+d} "
              f"查准{100*(sv['precision']-sb['precision']):+.2f}pct "
              f"覆盖{100*(sv['auto_cover']-sb['auto_cover']):+.2f}pct\n")

    out = os.path.join(TMP, f"l1_{args.variant.replace('=', '')}.json")
    json.dump({"variant": args.variant, "per_video": rows_v}, open(out, "w",
              encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"明细 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
