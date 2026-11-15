# -*- coding: utf-8 -*-
"""给存量场次补「筐锚点」采样（v21），不必重跑球检测。

背景：v21 的筐锚点校正（bbh/hoop_track.py）靠 positions.json 里的
`hoop`（每 20s 一条筐框）+ `hoop_ref`（参考帧上的筐框，绝对基准）。
新录的视频在检测时顺带就采了（零额外成本）；**存量场次没有这两样**，
判定会退化成纯 ORB（等价 v20）。本工具只跑筐那一路推理（每场约 45 秒），
把两条字段补进原 positions.json——positions/shift 一律不动，所以坐标
语义不变、DETECT_VERSION 不变，补完后重放判定即可生效（秒级）。

用法：
    python tools/remap_hoop.py 2026-10-17_1 2026-10-23_1
    python tools/remap_hoop.py --all          # work/ 下所有有 positions 的场次
    python tools/remap_hoop.py --dry 2026-10-31_1

字段含义见 bbh/hoop_track.py 模块注释。重复执行是幂等的（按段重采覆盖）。
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2                                            # noqa: E402

from bbh import hoop_track                            # noqa: E402
from bbh.calibration import load_segments_dir         # noqa: E402
from bbh.detector import safe_fps                     # noqa: E402
from bbh.motion import load_ref_image                 # noqa: E402
from bbh.pipeline import POSITIONS_FILE               # noqa: E402

WORK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "work")


def _blocks(doc: dict) -> list:
    """positions.json → 块列表（旧扁平格式视作单块，与 pipeline 同口径）。"""
    segs = doc.get("segments")
    return list(segs) if segs is not None else [doc]


def _sample_hoop(video: str, t0: float, t1: float | None, calib_rect,
                 every: int = hoop_track.SAMPLE_EVERY) -> list:
    """按段窗每 `every` 帧取一帧跑筐检测 → [[t,cx,cy,w,h,conf], ...]。

    时间优先取容器 PTS（与检测同源，VFR 下差 ~3s）；读不到回退 帧号/fps。
    """
    from bbh import detector_yolo          # 懒导入：只有本工具需要 torch
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        return []
    out = []
    try:
        fps = safe_fps(cap.get(cv2.CAP_PROP_FPS))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        f0 = max(0, int(round(t0 * fps)))
        f1 = min(total, int(round(t1 * fps))) if t1 else total
        fi = f0
        while fi < f1:
            cap.set(cv2.CAP_PROP_POS_FRAMES, fi)
            ok, frame = cap.read()
            if not ok:
                break
            if frame is None or not getattr(frame, "size", 0):
                break
            msec = cap.get(cv2.CAP_PROP_POS_MSEC)
            t = msec / 1000.0 if msec and msec > 0 else fi / fps
            got = detector_yolo.hoop_center_of(frame, calib_rect)
            if got:
                out.append([round(t, 3)] + [round(v, 2) for v in got])
            fi += max(1, int(every))
    finally:
        cap.release()
    return out


def _ref_center(seg, video_size) -> list | None:
    """参考帧上的筐框（绝对基准）。参考帧与检测画面分辨率不同 → None。"""
    img = load_ref_image(seg.ref_image) if seg.ref_image else None
    if img is None:
        return None
    vw, vh = video_size
    if vw and (img.shape[1], img.shape[0]) != (int(vw), int(vh)):
        return None
    from bbh import detector_yolo
    got = detector_yolo.hoop_center_of(img, seg.calib.hoop_rect)
    return list(got) if got else None


def remap(name: str, dry: bool = False, force: bool = False) -> dict | None:
    """单场次：补 hoop / hoop_ref 字段。返回统计；跳过时 None。"""
    work = os.path.join(WORK, name)
    pos_p = os.path.join(work, POSITIONS_FILE)
    if not os.path.exists(pos_p):
        print(f"  跳过（无 positions.json）")
        return None
    with open(pos_p, encoding="utf-8") as f:
        doc = json.load(f)
    video = doc.get("video") or ""
    if not os.path.exists(video):
        print(f"  跳过（源视频已清理：{video}）")
        return None
    segs = load_segments_dir(work)
    if not segs:
        print("  跳过（读不到标定）")
        return None

    cap = cv2.VideoCapture(video)
    video_size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                  int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    duration = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) / safe_fps(
        cap.get(cv2.CAP_PROP_FPS))
    cap.release()

    blocks = _blocks(doc)
    stat = {"n_block": len(blocks), "n_sample": 0, "n_ref": 0, "n_skip": 0}
    for blk in blocks:
        if blk.get("hoop") and not force:
            stat["n_skip"] += 1
            stat["n_sample"] += len(blk["hoop"])
            stat["n_ref"] += 1 if blk.get("hoop_ref") else 0
            continue
        t0 = float(blk.get("t0", 0.0) or 0.0)
        t1 = blk.get("t1")
        t1 = float(t1) if t1 is not None else None
        seg = min(segs, key=lambda s: abs(s.t0 - t0))
        if abs(seg.t0 - t0) > 0.5:          # 块与标定段对不上 → 宁可不采
            print(f"  段 t0={t0:.1f} 找不到对应标定（最近 {seg.t0:.1f}），跳过")
            continue
        hoop = _sample_hoop(video, t0, t1 if t1 is not None else duration,
                            seg.calib.hoop_rect)
        ref_c = _ref_center(seg, video_size)
        blk["hoop"] = hoop
        blk["hoop_ref"] = ref_c
        stat["n_sample"] += len(hoop)
        stat["n_ref"] += 1 if ref_c else 0
        hw = seg.calib.hoop_rect[2]
        corr, st = hoop_track.build_correction(hoop, blk.get("shift") or [],
                                              seg.calib.hoop_rect, ref_c)
        print(f"  段 t0={t0/60:6.1f}min 采样 {len(hoop):>3} 条"
              f"（ref {'✓' if ref_c else '✗'}）→ 校正项 {len(corr)} 条，"
              f"最大残差 {st['max_res']:.2f} 筐宽"
              + (f"，丢弃尺寸异常 {st['n_size']}" if st["n_size"] else ""))

    if not dry:
        tmp = pos_p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False)
        os.replace(tmp, pos_p)
    return stat


def main(argv=None):
    ap = argparse.ArgumentParser(description="补筐锚点采样（v21）")
    ap.add_argument("names", nargs="*", help="场次目录名（work/ 下）")
    ap.add_argument("--all", action="store_true", help="work/ 下所有场次")
    ap.add_argument("--dry", action="store_true", help="只统计不写盘")
    ap.add_argument("--force", action="store_true", help="已有 hoop 也重采")
    a = ap.parse_args(argv)

    names = a.names
    if a.all or not names:
        names = sorted(os.path.basename(os.path.dirname(p)) for p in
                       glob.glob(os.path.join(WORK, "*", POSITIONS_FILE)))
    if not names:
        print("没有可处理的场次")
        return 0
    print(f"待处理 {len(names)} 场：{', '.join(names)}")
    tot = {"n_sample": 0, "n_ref": 0}
    for n in names:
        print(f"=== {n} ===")
        st = remap(n, dry=a.dry, force=a.force)
        if st:
            for k in tot:
                tot[k] += st[k]
    print(f"\n合计：筐采样 {tot['n_sample']} 条，含绝对基准的段 {tot['n_ref']} 个"
          + ("（dry-run，未写盘）" if a.dry else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
