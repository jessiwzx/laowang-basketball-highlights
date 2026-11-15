# -*- coding: utf-8 -*-
r"""11-07_1 差场归因验证：网区 ROI 沿 x 平移 ±0.4 筐宽，看 net_post 峰值位置。

问题
----
`诊断/2026-11-10-筐采样自检/结论.md` §0 说 11-07 差场有两条候选原因，**未区分**：
  (a) 标定筐位偏 −0.294 筐宽（检测筐心比标定筐心靠左 9.7px）→ 网区 ROI 按标定
      筐位取 → ROI 偏到网外 → 帧差测不到网动；
  (b) 筐框本身太小（33×13 vs 11-06 的 44×18，网区面积只有 54%）→ 信号天生弱。

做法
----
网区 ROI 完全由 `net_motion._rois(calib, dx, dy)` 决定（`dx,dy` 是逐帧镜头位移
补偿，不是标定位偏）。所以：**只把网区左右平移、对照区不动**，其余测网逻辑
（三窗 + 相位对齐 + 局部性门）全部走生产代码。对 11-07 与 11-06 各取 N 个真
进球事件行，扫 9 个偏移：

  峰值出现在 ≈ −0.29 筐宽 → (a) 标定偏了，重标即可（改数据不改代码）
  峰值仍在 0            → (b) 筐太小 / 网信号天生弱（机位问题）

判定额外看两件事：平移后 11-07 的 post 中位能否回到 11-06 的水平（≈12.7），
以及 `post≥NET_STATIC_TH` 的比例能否从 55% 回到 92%。

只读：不动生产代码、不写 work 目录、不改 sidecar。产物写本目录。
用法：.venv\Scripts\python 诊断\2026-11-10-网区ROI平移\roi_shift.py
"""
import json
import os
import statistics as st
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
OUT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import bbh.net_motion as NM                                          # noqa: E402
from bbh.models import Calibration                                   # noqa: E402

ARCHIVE = r"D:\claude code\1视频获取\归档"
WORK = os.path.join(ROOT, "work")

SCENES = ["2026-11-06_1", "2026-11-07_1"]
OFFSETS = [-0.4, -0.3, -0.2, -0.1, 0.0, 0.1, 0.2, 0.3, 0.4]   # 单位=筐宽
N_EVENTS = 40          # 每场取样的事件行数（均匀抽，非取前 N）
SEED_STEP = 3          # 均匀抽步长（排序后每隔 SEED_STEP 取一条）
TOL = 0.2


def rd(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def wr(p, obj):
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def load_goals(v):
    """真值：优先 truth/（老场口径），否则 selection.json（新场派生口径，
    形如 ["02:13.8", ...] 的时间码列表 → 复用 derive_truth.selection_goals）。"""
    p = os.path.join(ROOT, "truth", v + ".json")
    if os.path.exists(p):
        return sorted(rd(p)["goals"]), "truth"
    from derive_truth import selection_goals
    goals, bad = selection_goals(os.path.join(WORK, v, "selection.json"))
    return goals, "selection(坏 %d)" % bad


def true_rows(events, goals):
    """真球对应的 **已被判定器输出的** 事件行（0.2s 内最近优先 1:1）。"""
    pairs = sorted((abs(e["t"] - g), ei, gi)
                   for ei, e in enumerate(events)
                   for gi, g in enumerate(goals) if abs(e["t"] - g) <= TOL)
    used_e, used_g, keep = set(), set(), []
    for _d, ei, gi in pairs:
        if ei in used_e or gi in used_g:
            continue
        used_e.add(ei)
        used_g.add(gi)
        keep.append(events[ei])
    return keep


def patch_roi(extra_px):
    """只平移网区 x，对照区保持原位（对照区一动就换了一套噪声参考系）。"""
    orig = NM.__dict__.get("_rois_orig") or NM._rois
    NM._rois_orig = orig

    def _rois(calib, dx, dy):
        (nx0, ny0, nx1, ny1), (cx0, cy0, cx1, cy1) = orig(calib, dx, dy)
        fw = calib.frame_size[0]
        ax0, ax1 = NM._clamp(nx0 + extra_px, nx1 + extra_px, fw)
        return (ax0, ny0, ax1, ny1), (cx0, cy0, cx1, cy1)

    NM._rois = _rois


def restore_roi():
    if NM.__dict__.get("_rois_orig"):
        NM._rois = NM._rois_orig


def summarize(vals):
    ok = [x for x in vals if x is not None]
    if not ok:
        return None
    return {
        "n": len(ok),
        "post_p50": round(st.median(ok), 2),
        "post_p25": round(sorted(ok)[len(ok) // 4], 2),
        "post_p75": round(sorted(ok)[3 * len(ok) // 4], 2),
        "ge_th": round(100.0 * sum(1 for x in ok if x >= NM.NET_STATIC_TH) / len(ok), 1),
    }


def main():
    report = {"th": NM.NET_STATIC_TH, "offsets": OFFSETS, "scenes": {}}
    for v in SCENES:
        calib = Calibration.load(os.path.join(WORK, v, "calibration.json"))
        pos = rd(os.path.join(WORK, v, "positions.json"))
        ev = rd(os.path.join(WORK, v, "events.json"))["events"]
        goals, gsrc = load_goals(v)
        rows = true_rows(ev, goals)
        rows.sort(key=lambda e: e["t"])
        pick = rows[::SEED_STEP][:N_EVENTS]
        times = ["%.2f" % round(e["t"], 2) for e in pick]
        W = calib.hoop_rect[2]

        video = os.path.join(ARCHIVE, v + ".mp4")
        if not os.path.exists(video):
            video = pos.get("video") or video
        shift = pos.get("shift") or []

        per_off = {}
        for off in OFFSETS:
            px = off * W
            patch_roi(px)
            try:
                sig = NM.measure_net_signals(video, calib, shift, times)
            finally:
                restore_roi()
            posts = [sig.get(k, {}).get("post") for k in times]
            locals_ = [sig.get(k, {}).get("local") for k in times]
            s = summarize(posts) or {}
            if s:
                s["local_true"] = sum(1 for x in locals_ if x is True)
                s["local_rate"] = round(
                    100.0 * s["local_true"] / max(1, len(posts)), 1)
            per_off["%+.1f" % off] = s
            print("  %-14s off=%+.1fW (%+.1fpx)  n=%s  p50=%-6s ≥TH=%-5s local=%s"
                  % (v, off, px, s.get("n"), s.get("post_p50"),
                     s.get("ge_th"), s.get("local_rate")))

        report["scenes"][v] = {
            "hoop": list(calib.hoop_rect), "W": W,
            "goals_src": gsrc, "goals": len(goals),
            "rows_picked": len(times), "offsets": per_off,
        }

    wr(os.path.join(OUT, "roi_shift.json"), report)

    # ---- 出表 ----
    L = []
    A = L.append
    A("# 网区 ROI 沿 x 平移扫描（%s）" % " / ".join(SCENES))
    A("")
    A("每格 = **网区帧差中位 / post≥%.1f 的比例**；local = 局部性门通过率。"
      % NM.NET_STATIC_TH)
    A("11-06_1 是正常对照场（真球 post 中位 12.66、local 67%%）。")
    A("")
    A("| 场 | " + " | ".join("%+.1fW" % o for o in OFFSETS) + " |")
    A("|---|" + "---|" * len(OFFSETS))
    for v in SCENES:
        d = report["scenes"][v]["offsets"]
        cells = []
        for o in OFFSETS:
            s = d.get("%+.1f" % o) or {}
            cells.append("%s / %s%%" % (s.get("post_p50", "-"), s.get("ge_th", "-")))
        A("| %s | %s |" % (v[5:], " | ".join(cells)))
    A("")
    A("| 场 | 偏移 | n | post p25 | post 中位 | post p75 | ≥TH 比例 | local 通过 |")
    A("|---|---|---|---|---|---|---|---|")
    for v in SCENES:
        d = report["scenes"][v]["offsets"]
        for o in OFFSETS:
            s = d.get("%+.1f" % o) or {}
            A("| %s | %+.1fW | %s | %s | %s | %s | %s%% | %s%% |"
              % (v[5:], o, s.get("n", "-"), s.get("post_p25", "-"),
                 s.get("post_p50", "-"), s.get("post_p75", "-"),
                 s.get("ge_th", "-"), s.get("local_rate", "-")))
    A("")
    for v in SCENES:
        d = report["scenes"][v]
        best = max((o for o in OFFSETS if d["offsets"].get("%+.1f" % o)),
                   key=lambda o: d["offsets"]["%+.1f" % o].get("post_p50") or 0)
        A("- **%s**：筐 %s（W=%d），取样 %d 条真球行，峰值偏移 **%+.1fW**"
          "（post 中位 %s）"
          % (v, d["hoop"], d["W"], d["rows_picked"], best,
             d["offsets"]["%+.1f" % best].get("post_p50")))
    A("")
    md = "\n".join(L)
    with open(os.path.join(OUT, "roi_shift.md"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write(md)
    print()
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
