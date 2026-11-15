"""08-21 弱网假绿体检（2026-09-26）。

背景：08-21_1 是八场 truth 里**唯一部分标注**的场（`complete=false`，只有
confirmed_goals=114），也是唯一没被任何调优轮覆盖的场。历史轮次报的
「08-21 假绿 72」是把 confirmed_goals 当 goals 算出来的——按仓库纪律
（`tools/evaluate.py` 顶部）部分标注集**禁止**算查准/查全：未标注的真进球
会被记成假绿、漏检不可见。所以那 72 里真伪未知，本脚本把「未知」变「已知」。

三步：
① 生产判定链重判（Temp work 目录，仓库 `work/` 只读；本场无 deleted.json，
   无策展排除）；
② 「未确认 high 行」（±3s 内无 confirmed 真球）逐行取几何+信号特征；
③ **去球探针**：把球色像素从帧差统计里剔除后重测网区信号（post_noball）。
   - post_noball < NET_STATIC_TH ≤ post_raw ⇒ 所谓「网动」是球自己扫过 ROI
     = 球扫型假绿（**检测错误、且有可修杠杆**）；
   - post_noball 仍 ≥ TH ⇒ 网真的动了，行大概率是**未标注的真进球**
     （标注缺口，不是检测错误）。

跳过 net_cluster_pass：它只追加 review 行、不动 high 行，与假绿无关，省掉
一次全片网簇扫描（本场 111 分钟，~10 分钟级）。

用法：./.venv/Scripts/python.exe -X utf8 诊断/2026-08-21弱网/qa_0821.py
"""
import json
import os
import sys
import time

import cv2
import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from bbh import pipeline as pl                                        # noqa: E402
from bbh.goal_decider import RIM_Y_RATIO, decide_goals, drop_static_positions  # noqa: E402
from bbh.models import BallPos, Calibration                           # noqa: E402
from bbh.motion import interp_shift                                   # noqa: E402
from bbh.net_motion import (NET_DS, NET_FRAMES, NET_LOCALITY_K,       # noqa: E402
                            NET_PHASE_TOL, NET_POST_OFFS, NET_STATIC_TH,
                            _open_cap, _rois, net_evidence_pass)

NAME = "2026-08-21_1"
LAB = os.path.join(REPO, "诊断", "2026-08-21弱网")
WD = os.path.join(os.environ.get("TEMP", "/tmp"), "qa0821", NAME)   # 只放 sidecar
TOL = 3.0          # truth 无 tolerance 键 → evaluate 默认 3.0


def resolve_video(video):
    if os.path.exists(video):
        return video
    for root in (r"D:\claude code\1视频获取\归档",
                 r"D:\claude code\视频获取\归档"):
        p = os.path.join(root, os.path.basename(video))
        if os.path.exists(p):
            return p
    return video


def load():
    doc = json.load(open(rf"{REPO}\work\{NAME}\events.json", encoding="utf-8"))
    hoop, lo, hi, frame, ref = doc["calib_key"]
    calib = Calibration(hoop_rect=tuple(hoop), hsv_low=tuple(lo),
                        hsv_high=tuple(hi), frame_size=tuple(frame), ref_hash=ref)
    pos = json.load(open(rf"{REPO}\work\{NAME}\positions.json", encoding="utf-8"))
    pts = [BallPos(t=p[0], x=p[1], y=p[2], area=p[3],
                   fi=(p[4] if len(p) > 4 else -1)) for p in pos["positions"]]
    shift = [tuple(s) for s in pos["shift"]]
    video = resolve_video(doc["video"])
    return calib, pts, shift, video


def tag_1to1(truth, rows, tol=TOL):
    """贪心 1:1 匹配（口径同 evaluate.match_report）：每行是否认领了真值。"""
    truth = sorted(truth)
    used, claimed = set(), set()
    for i, t in enumerate(rows):
        m = next((j for j, g in enumerate(truth)
                  if j not in used and abs(g - t) <= tol), None)
        if m is not None:
            used.add(m)
            claimed.add(i)
    return claimed, [g for j, g in enumerate(truth) if j not in used]


def roi_mean(d, valid, y0, y1, x0, x1):
    """ROI 内**有效**（非球色）像素的均值；有效像素太少返回 None。"""
    if y1 <= y0 or x1 <= x0:
        return None
    m = valid[y0:y1, x0:x1]
    if m.sum() < 30:
        return None
    return float(d[y0:y1, x0:x1][m].mean()), float(m.mean())


def window_scores_noball(cap, t0, calib, shift_samples):
    """_window_scores 的去球变体：同一批帧、同一 ROI、同一相位对齐，
    只在求均值时剔除「球色膨胀 5px」的像素（球扫过 ROI 时像素会自己造出
    帧差，把它剔掉才是「网在不在动」的净信号）。返回
    (post_raw, post_noball, ctrl_noball, local_noball, 有效占比min)。"""
    cap.set(cv2.CAP_PROP_POS_MSEC, t0 * 1000)
    frames = []
    for _ in range(NET_FRAMES):
        ok, fr = cap.read()
        if not ok:
            return None
        frames.append(fr)
    fw, fh = calib.frame_size
    lo = np.array(calib.hsv_low, dtype=np.uint8)
    hi = np.array(calib.hsv_high, dtype=np.uint8)
    ker = np.ones((11, 11), np.uint8)          # 5px 膨胀
    raw, nob, ctl, vfrac = [], [], [], []
    local = False
    for a, b in zip(frames, frames[1:]):
        ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(np.float32)
        gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY).astype(np.float32)
        da, db = cv2.resize(ga, NET_DS), cv2.resize(gb, NET_DS)
        (sx, sy), _ = cv2.phaseCorrelate(da, db)
        if abs(sx) > NET_PHASE_TOL or abs(sy) > NET_PHASE_TOL:
            m = np.float32([[1, 0, sx * fw / NET_DS[0]],
                            [0, 1, sy * fh / NET_DS[1]]])
            a = cv2.warpAffine(a, m, (fw, fh))     # 连色一起对齐，掩膜才不偏
            ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(np.float32)
        msec = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        dx, dy = interp_shift(shift_samples, msec if msec > 0 else t0)
        (nx0, ny0, nx1, ny1), (cx0, cy0, cx1, cy1) = _rois(calib, dx, dy)
        ma = cv2.dilate(cv2.inRange(cv2.cvtColor(a, cv2.COLOR_BGR2HSV), lo, hi), ker)
        mb = cv2.dilate(cv2.inRange(cv2.cvtColor(b, cv2.COLOR_BGR2HSV), lo, hi), ker)
        d = np.abs(gb - ga)
        valid = (ma == 0) & (mb == 0)
        nr = _rois_raw(d, ny0, ny1, nx0, nx1)
        n = roi_mean(d, valid, ny0, ny1, nx0, nx1)
        c = roi_mean(d, valid, cy0, cy1, cx0, cx1)
        if nr is not None:
            raw.append(nr)
        if n is not None:
            nob.append(n[0])
            vfrac.append(n[1])
            if c is not None and n[0] >= NET_STATIC_TH \
                    and n[0] >= NET_LOCALITY_K * c[0]:
                local = True
        if c is not None:
            ctl.append(c[0])
    if not nob:
        return None
    return (max(raw) if raw else None, max(nob),
            max(ctl) if ctl else None, local,
            min(vfrac) if vfrac else None)


def _rois_raw(d, y0, y1, x0, x1):
    return float(d[y0:y1, x0:x1].mean()) if (y1 > y0 and x1 > x0) else None


def ball_features(pts, t, calib):
    """行时刻附近球轨迹特征（positions 采样 ~9.5/s）。"""
    hx, hy, hw, hh = calib.hoop_rect
    hcx, rim_y = hx + hw / 2.0, hy + RIM_Y_RATIO * hh
    win = [p for p in pts if t - 1.2 <= p.t <= t + 1.6]
    if not win:
        return {}
    near = min(win, key=lambda p: abs(p.t - t))
    pre = [p for p in win if p.t <= t]
    below = [p for p in win if p.t > t and p.y > rim_y]
    return {
        "ball_x": round(near.x, 1), "ball_y": round(near.y, 1),
        "ball_dt": round(near.t - t, 2), "ball_area": round(near.area, 1),
        "ball_max_area": round(max(p.area for p in win), 1),
        "ball_dx_hoop": round((near.x - hcx) / hw, 2),
        "ball_min_dx_hoop": round(min(abs(p.x - hcx) for p in win) / hw, 2),
        "ball_above_rim_pre": sum(1 for p in pre if p.y <= rim_y),
        "ball_below_rim_post": len(below),
    }


def main():
    t_start = time.time()
    os.makedirs(WD, exist_ok=True)
    os.makedirs(LAB, exist_ok=True)
    calib, pts, shift, video = load()
    truth = json.load(open(rf"{REPO}\truth\{NAME}.json", encoding="utf-8"))
    confirmed = truth["confirmed_goals"]
    print(f"场次 {NAME}  视频 {video}")
    print(f"truth：complete={truth.get('complete')}  confirmed={len(confirmed)}")

    # ① 生产判定链（与 b2_acceptance 同链；跳过只写 review 的 net_cluster_pass）
    ptd = drop_static_positions(pts, calib)
    evs = decide_goals(ptd, calib)
    pre = [e.status for e in evs]
    evs = net_evidence_pass(video, WD, calib, shift, evs)
    seg = {"positions": pts, "shift": shift, "calib": calib, "t0": 0.0,
           "t1": None, "active": True}
    evs = pl._cooldown_release_pass(video, WD, seg, ptd, evs, pre)
    rows = pl._merge_segment_events([evs], [0.0])
    statuses = {}
    for e in rows:
        statuses[e.status] = statuses.get(e.status, 0) + 1
    print(f"判定链 {time.time() - t_start:.0f}s  行数 {len(rows)}  {statuses}")

    ts = [e.t for e in rows]
    claimed, missing = tag_1to1(confirmed, ts)
    tiers = {"high": 0, "review": 0, "low": 0}
    for i, e in enumerate(rows):
        if claimed and i in claimed:
            tiers[e.status] = tiers.get(e.status, 0) + 1
    print(f"确认进球 {len(confirmed)}：浮表 {len(confirmed) - len(missing)}"
          f"（🟢{tiers['high']} 🟡{tiers['review']} ⚪{tiers['low']}）"
          f"  丢失 {len(missing)}")
    if missing:
        print(f"  ❌ 丢失（回归失败）: {[round(t, 1) for t in missing]}")

    unconfirmed_high = [e for i, e in enumerate(rows)
                        if e.status == "high" and i not in claimed]
    print(f"未确认 high 行（假绿候选）: {len(unconfirmed_high)}"
          f"  / 全场 high {statuses.get('high', 0)}")

    # ③ 去球探针
    cap = _open_cap(video, WD)
    if cap is None:
        print("视频打不开，去球探针跳过")
        return
    out = []
    try:
        for n, e in enumerate(unconfirmed_high, 1):
            post_nb = None
            for off in NET_POST_OFFS:
                g = window_scores_noball(cap, e.t + off, calib, shift)
                if g and g[1] is not None:
                    if post_nb is None or g[1] > post_nb[1]:
                        post_nb = g
            f = ball_features(pts, e.t, calib)
            row = {"t": round(e.t, 2), "conf": round(e.confidence, 3),
                   "cross_off": e.cross_off, "above_n": e.above_n,
                   "below_n": e.below_n, "descent_px": e.descent_px,
                   "below_big": e.below_big, "pop_back": e.pop_back,
                   "net_post": e.net_post, "net_ctrl": e.net_ctrl,
                   "net_local": e.net_local}
            if post_nb:
                row.update({"post_raw": round(post_nb[0], 2) if post_nb[0] else None,
                            "post_noball": round(post_nb[1], 2),
                            "ctrl_noball": round(post_nb[2], 2) if post_nb[2] else None,
                            "local_noball": post_nb[3],
                            "valid_frac": round(post_nb[4], 3) if post_nb[4] else None})
            row.update(f)
            if row.get("post_noball") is None:
                row["cls"] = "?测量失败"
            elif row["post_noball"] < NET_STATIC_TH:
                row["cls"] = "A球扫型"          # 去球后网区安静 ⇒ 球自己造的信号
            else:
                row["cls"] = "B真网动"          # 球剔掉网还在动 ⇒ 像真穿网
            out.append(row)
            if n % 10 == 0:
                print(f"  去球探针 {n}/{len(unconfirmed_high)}"
                      f"  {time.time() - t_start:.0f}s")
                sys.stdout.flush()
    finally:
        cap.release()

    cls_count = {}
    for r in out:
        cls_count[r["cls"]] = cls_count.get(r["cls"], 0) + 1
    print(f"\n去球探针分类: {cls_count}")

    with open(os.path.join(LAB, "qa_0821_rows.json"), "w", encoding="utf-8") as f:
        json.dump({"summary": {"rows": len(rows), "statuses": statuses,
                               "confirmed": len(confirmed),
                               "confirmed_missing": missing,
                               "confirmed_tiers": tiers,
                               "unconfirmed_high": len(unconfirmed_high),
                               "cls": cls_count,
                               "net_static_th": NET_STATIC_TH},
                   "rows": out}, f, ensure_ascii=False, indent=1)
    hdr = ["t", "cls", "conf", "net_post", "net_ctrl", "net_local",
           "post_raw", "post_noball", "ctrl_noball", "local_noball",
           "valid_frac", "cross_off", "above_n", "below_n", "descent_px",
           "below_big", "pop_back", "ball_area", "ball_dx_hoop",
           "ball_min_dx_hoop", "ball_below_rim_post"]
    with open(os.path.join(LAB, "qa_0821_rows.csv"), "w", encoding="utf-8") as f:
        f.write(",".join(hdr) + "\n")
        for r in out:
            f.write(",".join(str(r.get(k, "")) for k in hdr) + "\n")

    # 正排打印（B 类优先，像真进球的先看）
    def key(r):
        return (0 if r["cls"].startswith("B") else 1, -r.get("post_noball") or 0)
    print(f"\n{'t':>9} {'cls':<7} {'post':>6} {'no球':>6} {'ctrl':>6} "
          f"{'cross':>6} {'ab':>3} {'bl':>3} {'desc':>6} {'area':>6} {'dx筐':>6}")
    for r in sorted(out, key=key):
        print(f"{r['t']:>9.2f} {r['cls']:<7} {str(r.get('net_post')):>6} "
              f"{str(r.get('post_noball')):>6} {str(r.get('ctrl_noball')):>6} "
              f"{str(r.get('cross_off')):>6} {str(r.get('above_n')):>3} "
              f"{str(r.get('below_n')):>3} {str(r.get('descent_px')):>6} "
              f"{str(r.get('ball_area')):>6} {str(r.get('ball_dx_hoop')):>6}")
    print(f"\n总耗时 {time.time() - t_start:.0f}s  产物在 {LAB}")


if __name__ == "__main__":
    main()
