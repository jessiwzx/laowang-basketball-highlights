# -*- coding: utf-8 -*-
"""Step 0 补充：09-24_1 / 08-21_1 的 v18 判定器点流重放（反作用监测基线）

复用各场既有检出点流（work/<场>/positions.json），只把判定器换成 v18 快照重跑
——前后对比用同一条点流，规则是唯一变量，绝对数字不与官方 KPI 混用。

写盘只在 yolo/eval_out/v18_reverse/（_guard 护栏）。
"""
import json
import os
import sys

os.environ.setdefault("YOLO_BBH_SNAP", "v18")
sys.path.insert(0, r"D:/claude code/yolo/scripts")
import replay_layer as R                                            # noqa: E402

ARCHIVE = r"D:\claude code\1视频获取\归档"
VIDEOS = ["2026-09-24_1", "2026-08-21_1"]
OUT_TAG = "v18_reverse"


def main():
    rows = []
    for v in VIDEOS:
        print(f"== {v} ==", flush=True)
        src = os.path.join(R.HSV_WORK, v, "positions.json")
        if not os.path.exists(src):
            rows.append({"video": v, "ok": False, "err": "无 positions.json"})
            continue
        with open(src, encoding="utf-8") as f:
            doc = json.load(f)
        real = os.path.join(ARCHIVE, v + ".mp4")
        ov = real if (doc.get("video") != real and os.path.exists(real)) else None
        if ov:
            print(f"  点流记的视频路径无效 → 用 {ov}", flush=True)
        r = R.replay(v, "hsv", OUT_TAG, False, None, verbose=True,
                     video_override=ov, allow_rescan=True)
        rows.append(r)
        print("  →", "OK" if r.get("ok") else "FAIL", r.get("err", ""), flush=True)
    p = os.path.join(R.OUT_ROOT, f"_replay_{OUT_TAG}.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"source": "hsv", "out_tag": OUT_TAG, "per_video": rows}, f,
                  ensure_ascii=False, indent=2)
    print("记录：" + p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
