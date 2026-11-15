# -*- coding: utf-8 -*-
"""v21 筐锚点验收：回归等价性 + 增益对照。

两步：
  1. --snap  把现有 events.json 存成 events_prev.json（升级前的基线快照）
  2. --run   用当前代码重放判定（positions 缓存复用，秒级），与快照逐行对比

对比口径：按 t（±0.05s）对齐，逐条比 status，并统计 high/review/low 分布。
「无筐采样时逐条完全相同」是 v21 的硬护栏——不成立说明坐标链路被动过。
"""
import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bbh.calibration import load_segments_dir          # noqa: E402
from bbh.pipeline import EVENTS_FILE, POSITIONS_FILE, run_detection  # noqa: E402

WORK = os.path.join(_ROOT, "work")
SNAP = "events_prev.json"


def _dist(evs):
    d = {"high": 0, "review": 0, "low": 0}
    for e in evs:
        d[e["status"]] = d.get(e["status"], 0) + 1
    return d


def _load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def snapshot(names):
    for n in names:
        ep = os.path.join(WORK, n, EVENTS_FILE)
        if not os.path.exists(ep):
            print(f"{n}: 无 events.json，跳过")
            continue
        doc = _load(ep)
        doc["_snap_logic"] = doc.get("logic_ver")
        with open(os.path.join(WORK, n, SNAP), "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        print(f"{n}: 快照 logic_ver={doc.get('logic_ver')} "
              f"n={len(doc.get('events') or [])}")


def run(names, logic=None, tag=None, compare_to=None):
    """logic：临时覆盖 pipeline.LOGIC_VERSION（分离 v20/v21 变量用）。
    tag：把本次 events 另存为 events_<tag>.json（多臂对照）。
    compare_to：与哪个臂对比（默认 events_prev.json）。"""
    from bbh import pipeline
    if logic is not None:
        pipeline.LOGIC_VERSION = int(logic)
    for n in names:
        work = os.path.join(WORK, n)
        pp = os.path.join(work, POSITIONS_FILE)
        if not os.path.exists(pp):
            print(f"{n}: 无 positions.json，跳过")
            continue
        doc = _load(pp)
        video = doc.get("video") or ""
        if not os.path.exists(video):
            print(f"{n}: 源视频已清理，跳过")
            continue
        segs = load_segments_dir(work)
        if not segs:
            print(f"{n}: 读不到标定，跳过")
            continue
        hoop_n = sum(len(b.get("hoop") or []) for b in
                     (doc.get("segments") or [doc]))
        path, warn = run_detection(video, segs, work)
        new = _load(path)
        evs = new.get("events") or []
        if tag:
            with open(os.path.join(work, f"events_{tag}.json"), "w",
                      encoding="utf-8") as f:
                json.dump(new, f, ensure_ascii=False)
        print(f"\n=== {n}  logic_ver={new.get('logic_ver')}  "
              f"筐采样 {hoop_n} 条 ===")
        print(f"  重放后 {len(evs)} 行  {_dist(evs)}")
        sp = os.path.join(work, compare_to or SNAP)
        if not os.path.exists(sp):
            print("  （无对照快照，跳过对比）")
            continue
        old = _load(sp)
        oe = old.get("events") or []
        om = {round(e["t"], 2): e for e in oe}
        nm = {round(e["t"], 2): e for e in evs}
        common = sorted(set(om) & set(nm))
        flip = [(t, om[t]["status"], nm[t]["status"])
                for t in common if om[t]["status"] != nm[t]["status"]]
        only_old = sorted(set(om) - set(nm))
        only_new = sorted(set(nm) - set(om))
        print(f"  对照 {len(oe)} 行 {_dist(oe)}"
              f"（{os.path.basename(sp)} / logic_ver="
              f"{old.get('_snap_logic') or old.get('logic_ver')}）")
        print(f"  共同 {len(common)}  档位变化 {len(flip)}"
              f"  仅旧 {len(only_old)}  仅新 {len(only_new)}")
        if flip:
            for t, a, b in flip[:12]:
                print(f"    t={t:8.2f}  {a} → {b}")
        if only_old[:8]:
            print("    仅旧行:", [round(t, 2) for t in only_old[:8]])
        if only_new[:8]:
            print("    仅新行:", [round(t, 2) for t in only_new[:8]])
        if warn:
            print("  warning:", warn.replace("\n", " | ")[:200])


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--snap", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--logic", type=int, default=None)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--compare", default=None)
    a = ap.parse_args(argv)
    names = a.names or sorted(
        os.path.basename(os.path.dirname(p)) for p in
        __import__("glob").glob(os.path.join(WORK, "*", POSITIONS_FILE)))
    if a.snap:
        snapshot(names)
    if a.run or not a.snap:
        run(names, logic=a.logic, tag=a.tag, compare_to=a.compare)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
