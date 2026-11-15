# -*- coding: utf-8 -*-
r"""批量跑检测 + 出评测报告：在「视频剪辑YOLO」里一条命令完成测试闭环。

它做的事
--------
1. 对每个场次：用**本项目自己的检出器**（默认 YOLO，见 bbh/detector_switch.py）
   跑 `bbh.pipeline.run_detection`，产物写 `work\<场>\`（events.json / positions.json）；
2. 跑完调 `scripts\eval_report.py`，按三种口径出报告（官方 / 对称 / 容差扫描）。

标定从哪来
----------
检测必须要标定（筐几何 + 参考帧）。脚本按 `--calib-from\<场>\calibration.json`
读，**默认 = `--out-work`**（即用本项目自己标定好的场次）。
新场次还没标定 → 先在界面上标一次（标定页），或用
`--calib-from "D:\claude code\视频剪辑\work"` 复用原项目当年标好、只读的标定
（这是**数据**复用，不是代码耦合；本项目不 import 对方任何模块）。

用法
----
    # 7 场标准测试集（需已标定；未标定的场次会被跳过并提示）
    .venv\Scripts\python scripts\run_eval.py

    # 复用原项目只读标定，直接跑 7 场
    .venv\Scripts\python scripts\run_eval.py --calib-from "D:\claude code\视频剪辑\work"

    # 只跑某一场，先做端到端冒烟
    .venv\Scripts\python scripts\run_eval.py --videos 2026-08-29_3

    # 只出报告（不跑检测，work\ 已有产物时）
    .venv\Scripts\python scripts\eval_report.py
"""
import argparse
import json
import os
import subprocess
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import eval_lib as E                                              # noqa: E402

ARCHIVE = r"D:\claude code\1视频获取\归档"


def _trim(src, dst, seconds, start=0.0):
    """截 [start, start+seconds) 成独立片段（调试冒烟）；PTS 从 0 起。"""
    import imageio_ffmpeg
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if not os.path.exists(dst):
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
                        "-ss", str(start), "-i", src, "-t", str(seconds),
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                        "-an", dst], check=True)
    return dst


def _calib_from_truth(video):
    """从 truth\\<场>.json 的 calib_key 重建**单段**标定（无参考帧）。

    truth 的 calib_key = [hoop_rect, hsv_low, hsv_high, frame_size, ref_hash]，
    正是 Calibration 的字段——所以 7 场标准集都能在不依赖任何外部标定文件的
    情况下跑起来。代价：没有参考帧 → 不做镜头位移校正（会附警告），
    且**多段标定的场次只能用段 0**（这类场次请用 --calib-from 给真标定）。
    """
    from bbh.models import Calibration, CalibSegment
    p = os.path.join(ROOT, "truth", video + ".json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    ck = d.get("calib_key")
    if not ck or len(ck) < 4:
        return None
    c = Calibration(hoop_rect=tuple(ck[0]), hsv_low=tuple(ck[1]),
                    hsv_high=tuple(ck[2]), frame_size=tuple(ck[3]))
    return [CalibSegment(0.0, c, None)]


def run_one(video, video_dir, work_root, calib_from, force, clip, clip_start,
            calib_truth=True, verbose=True):
    from bbh import calibration
    import bbh.pipeline as P
    from bbh import detector_switch

    work_dir = os.path.join(work_root, video)
    calib_dir = os.path.join(calib_from, video)
    calib_p = os.path.join(calib_dir, "calibration.json")
    calib_src = "work"
    if os.path.exists(calib_p):
        segs = calibration.load_segments_dir(calib_dir)
        if not segs:
            return {"video": video, "ok": False, "err": f"标定不可解析：{calib_p}"}
    elif calib_truth:
        segs = _calib_from_truth(video)
        calib_src = "truth(单段/无参考帧)"
        if not segs:
            return {"video": video, "ok": False,
                    "err": f"缺标定：{calib_p}，且 truth 里也没 calib_key"}
    else:
        return {"video": video, "ok": False,
                "err": f"缺标定：{calib_p}（先在界面标定，或用 --calib-from 指向"
                       "含本场 calibration.json 的目录）"}

    video_path = os.path.join(video_dir, video + ".mp4")
    if not os.path.exists(video_path):
        return {"video": video, "ok": False, "err": f"视频不存在：{video_path}"}
    if clip:
        video_path = _trim(video_path, os.path.join(
            work_root, "_trim", f"{video}__{int(clip_start)}__{clip}s.mp4"),
            clip, clip_start)

    os.makedirs(work_dir, exist_ok=True)
    st = {"t0": time.monotonic(), "last": 0.0}

    def progress(fi, total):
        if not verbose:
            return
        now = time.monotonic()
        if now - st["last"] < 20:
            return
        st["last"] = now
        pct = fi / total * 100 if total else 0
        print(f"    [{video}] {fi}/{total} ({pct:.0f}%) "
              f"用时 {(now - st['t0']) / 60:.1f}min", flush=True)

    def stage(msg):
        if verbose and msg:
            print(f"    [{video}] {msg}", flush=True)

    t0 = time.monotonic()
    try:
        events_path, warning = P.run_detection(
            video_path, segs, work_dir, force=force,
            progress_cb=progress, stage_cb=stage)
    except KeyboardInterrupt:
        raise
    except Exception as e:                                        # noqa: BLE001
        return {"video": video, "ok": False, "err": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc()[-1500:]}
    dt = time.monotonic() - t0

    with open(events_path, encoding="utf-8") as f:
        doc = json.load(f)
    tiers = {}
    for e in doc["events"]:
        tiers[e["status"]] = tiers.get(e["status"], 0) + 1
    if verbose:
        print(f"    [{video}] ✓ {dt / 60:.1f}min 事件 {len(doc['events'])} "
              f"档位 {tiers}（标定 {calib_src}）", flush=True)
    return {"video": video, "ok": True, "seconds": round(dt, 1),
            "events": len(doc["events"]), "tiers": tiers,
            "detector": detector_switch.NAME, "warning": warning,
            "calib_src": calib_src, "events_path": events_path,
            "calib_from": calib_from}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--videos", nargs="*", default=E.VIDEOS)
    ap.add_argument("--video-dir", default=ARCHIVE)
    ap.add_argument("--out-work", default=os.path.join(ROOT, "work"),
                    help="产物根目录（含 <场>/events.json）")
    ap.add_argument("--calib-from", default=None,
                    help="标定来源根目录（含 <场>/calibration.json）；默认 = out-work")
    ap.add_argument("--no-calib-truth", action="store_true",
                    help="禁用「缺标定时从 truth 的 calib_key 重建单段标定」的兜底")
    ap.add_argument("--force", action="store_true", help="忽略已有 events.json 重跑")
    ap.add_argument("--clip", type=int, default=None,
                    help="调试：只跑 N 秒片段（端到端冒烟，产物混进 out-work）")
    ap.add_argument("--clip-start", type=float, default=0.0)
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--report-tag", default="app")
    args = ap.parse_args()

    from bbh import detector_switch
    out_work = os.path.abspath(args.out_work)
    calib_from = os.path.abspath(args.calib_from or out_work)
    os.makedirs(out_work, exist_ok=True)

    print(f"=== 批量检测：检出器={detector_switch.NAME} 场次 {len(args.videos)} "
          f"→ {out_work} ===")
    print(f"    标定来源：{calib_from}")
    results, t0 = [], time.monotonic()
    for v in args.videos:
        print(f"  ▶ {v}", flush=True)
        try:
            r = run_one(v, args.video_dir, out_work, calib_from,
                        args.force, args.clip, args.clip_start,
                        calib_truth=not args.no_calib_truth)
        except KeyboardInterrupt:
            print("  中断（已完成的场次仍在 out-work 里）")
            break
        results.append(r)
        if not r["ok"]:
            print(f"    ✗ {r['err']}")

    log = os.path.join(out_work, "run_log.json")
    with open(log, "w", encoding="utf-8") as f:
        json.dump({"detector": detector_switch.NAME, "calib_from": calib_from,
                   "total_min": round((time.monotonic() - t0) / 60, 1),
                   "results": results}, f, ensure_ascii=False, indent=1)
    ok = [r for r in results if r["ok"]]
    print(f"\n完成 {len(ok)}/{len(results)} 场，总用时 "
          f"{(time.monotonic() - t0) / 60:.1f}min\n日志：{log}")

    if args.no_report or not ok:
        return 0

    # ---- 出报告 ----
    cmd = [sys.executable, os.path.join(ROOT, "scripts", "eval_report.py"),
           "--arm", f"{args.report_tag}={out_work}", "--tag", args.report_tag]
    print("\n=== 出评测报告 ===")
    return subprocess.call(cmd, cwd=ROOT)


if __name__ == "__main__":
    sys.exit(main())
