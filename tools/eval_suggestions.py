# tools/eval_suggestions.py
"""颜色建议真值重放基线报告（spec§评估工装）。预期精度≈随机——只作基线不设 gate。
用法:
  ./.venv/Scripts/python.exe -X utf8 tools/eval_suggestions.py work/2026-08-22_1 truth/2026-08-22_1.json
  ./.venv/Scripts/python.exe -X utf8 tools/eval_suggestions.py work/2026-08-21_1 truth/2026-08-21_1.json --snap work/2026-08-21_1/positions.json
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bbh.appearance import Appearance, build_features, load_features  # noqa: E402
from bbh.models import Calibration  # noqa: E402
from bbh.suggest import evaluate_neighbors  # noqa: E402


def _calib_from_snap(path):
    snap = json.load(open(path, encoding="utf-8"))["calib"]
    return Calibration(hoop_rect=tuple(snap[0]), hsv_low=tuple(snap[1]),
                       hsv_high=tuple(snap[2]), frame_size=tuple(snap[3]),
                       ref_hash=snap[4])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("work_dir")
    ap.add_argument("truth_path")
    ap.add_argument("--snap", default=None,
                    help="08-21 存档标定 positions.json（该场不可用全局 calibration.json）")
    ap.add_argument("-k", type=int, default=5)
    args = ap.parse_args()
    sh = json.load(open(f"{args.work_dir}/shooters.json", encoding="utf-8"))
    calib = _calib_from_snap(args.snap) if args.snap \
        else Calibration.load("calibration.json")
    fd = load_features(args.work_dir)
    feats = {k: Appearance.from_dict(v)
             for k, v in (fd or {}).get("feats", {}).items()}
    if not feats:
        print("features.json 缺失——重提（每球 ~1s）…")
        for _ in build_features(sh["video"], calib, args.work_dir, sh["traces"]):
            pass
        feats = {k: Appearance.from_dict(v) for k, v in
                 load_features(args.work_dir)["feats"].items()}
    truth = json.load(open(args.truth_path, encoding="utf-8"))["shooters"]
    r = evaluate_neighbors(feats, truth, k=args.k)
    print(f"{args.work_dir} 颜色建议基线（恒 low，不设 gate）")
    print(f"  precision@{r['k']} = {r['precision_at_k']:.3f}")
    print(f"  hit_rate       = {r['hit_rate']:.3f}")
    print(f"  checked        = {r['checked']} 球")


if __name__ == "__main__":
    main()
