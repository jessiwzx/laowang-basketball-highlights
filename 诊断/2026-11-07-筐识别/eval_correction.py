# -*- coding: utf-8 -*-
"""离线评估：筐锚点校正能把「只标一次」的偏差压到多少。

数据源：hoop_dense_result.json（四场 1409 帧筐观测，每 20s 一条，已含
        `res_c` = 筐实测中心 − (该段标定中心 + ORB 位移)，即**不校正时的偏差**）。

评估方式：隔点留出——偶数位当锚点（模拟管线里 20s 采样得到的 c 节点），
奇数位当测试点（模拟落在两锚点之间的球时刻），c 用两侧锚点线性插值。
这样测的是**插值误差**，不是拟合残差。

口径：
  不校正      = |res_c|（只标一次 + ORB 的偏差）
  校正后      = |res_c − c|（锚点修正后的残差）
单位：筐宽（code 里 res_c 已是 px，这里除 hw）。
"""
import json
import os
import sys
from statistics import median

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bbh.hoop_track import _medfilt                       # noqa: E402

SRC = os.path.join(_ROOT, "诊断", "2026-11-07-筐识别", "hoop_dense_result.json")


def _interp(nodes, t):
    """nodes: [(t, v)] 升序 —— 分段线性，端外保持端值（与管线 interp_shift 同款）"""
    if not nodes:
        return 0.0
    if t <= nodes[0][0]:
        return nodes[0][1]
    for (t0, v0), (t1, v1) in zip(nodes, nodes[1:]):
        if t < t1:
            f = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
            return v0 + (v1 - v0) * f
    return nodes[-1][1]


def main():
    d = json.load(open(SRC, encoding="utf-8"))
    tot_raw, tot_fix = [], []
    print(f"{'场次':14s}{'段':>4}{'点':>6}{'不校正中位':>11}{'最大':>8}"
          f"{'校正后中位':>12}{'P90':>8}{'最大':>8}")
    for name, blk in d.items():
        rows = [r for r in blk["rows"] if r.get("det")]
        cals = {i + 1: c[1] for i, c in enumerate(blk["cals"])}
        for si in sorted(set(r["seg"] for r in rows)):
            rs = sorted([r for r in rows if r["seg"] == si], key=lambda r: r["t"])
            hw = cals[si][2]
            if len(rs) < 6:
                continue
            raw = [abs(r["res_c"]) / hw for r in rs]
            # 隔点留出：偶数位当锚点，奇数位当测试点
            nodes = [(rs[i]["t"], _medfilt([r["res_c"] for r in rs], 3)[i] / hw)
                     for i in range(0, len(rs), 2)]
            err = [abs(rs[i]["res_c"] / hw - _interp(nodes, rs[i]["t"]))
                   for i in range(1, len(rs), 2)]
            tot_raw += raw
            tot_fix += err
            print(f"{name:14s}{si:>4}{len(rs):>6}{median(raw):>11.2f}{max(raw):>8.2f}"
                  f"{median(err):>12.2f}{sorted(err)[int(len(err) * 0.9)]:>8.2f}"
                  f"{max(err):>8.2f}")
    if not tot_raw:
        print("无数据")
        return
    over = [v for v in tot_raw if v > 0.30]
    over_fix = [v for v in tot_fix if v > 0.30]
    print(f"\n合计 {len(tot_raw)} 点"
          f"：不校正 中位 {median(tot_raw):.2f}W / P90 "
          f"{sorted(tot_raw)[int(len(tot_raw) * 0.9)]:.2f}W / 超 0.30W 门 "
          f"{len(over)} 点（{len(over) / len(tot_raw) * 100:.1f}%）")
    print(f"      校正后 中位 {median(tot_fix):.2f}W / P90 "
          f"{sorted(tot_fix)[int(len(tot_fix) * 0.9)]:.2f}W / 超 0.30W 门 "
          f"{len(over_fix)} 点（{len(over_fix) / len(tot_fix) * 100:.1f}%）")


if __name__ == "__main__":
    main()
