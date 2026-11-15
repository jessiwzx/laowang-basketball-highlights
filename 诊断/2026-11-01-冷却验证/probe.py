"""探针：读 positions.json 结构（只读，不跑判定）。

用法: python probe.py <work_dir> [...]
"""
import json
import os
import sys

for wd in sys.argv[1:]:
    p = os.path.join(wd, "positions.json")
    if not os.path.exists(p):
        print(f"{os.path.basename(wd):16s} 无 positions.json")
        continue
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    segs = d.get("segments")
    if segs is None:
        blocks = [{"t0": 0.0, "t1": None,
                   "n": len(d.get("positions", [])),
                   "hw": d.get("calib", [[None] * 4])[0][2]}]
    else:
        blocks = [{"t0": s.get("t0"), "t1": s.get("t1"),
                   "n": s.get("positions_len"),
                   "hw": s.get("calib", [[None] * 4])[0][2]} for s in segs]
    dur = (d.get("meta") or {}).get("duration")
    print(f"{os.path.basename(wd):16s} dur={dur} dv={str(d.get('detect_ver'))[:18]} "
          f"blocks={len(blocks)}")
    for b in blocks:
        print(f"    t0={b['t0']} t1={b['t1']} pts={b['n']} hw={b['hw']}")
