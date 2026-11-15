"""Phase 1 验收工具：射手缩略图 ↔ truth 出手人标注 对照表。

用法：
  .venv\\Scripts\\python tools/check_shooters.py work/2026-08-22_1 truth/2026-08-22_1.json
产出：
  <work目录>/shooters_check.html——每条=两帧缩略图+当时标注的出手人，
  浏览器打开肉眼核对"图里是不是那个人"（Phase 1 验收门：缩略图有效率
  ≥80% 且无系统性偏差，spec「Phase 1 验收」节）；
  终端打印自动指标：三档统计 / 标注对照数 / 出手→进球间隔合理区占比。
退出码恒 0——验收门是人工看图，本工具只出材料。"""
import json
import os
import sys
from collections import Counter

# 脚本直跑时 sys.path[0]=tools/，补仓库根才能 import bbh（pytest 走 -m 不需要）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from bbh.shooter import STATUS_TEXT


def parse_key(k: str) -> float:
    """'01:23.7' → 83.7（truth/shooters 与 shooters.json 的行键同格式）。"""
    m, s = k.split(":")
    return int(m) * 60 + float(s)


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    work_dir, truth_path = sys.argv[1], sys.argv[2]
    with open(f"{work_dir}/shooters.json", encoding="utf-8") as f:
        traces = json.load(f)["traces"]
    with open(truth_path, encoding="utf-8") as f:
        shooters = json.load(f).get("shooters", {})

    matched = [(k, name, traces[k]) for k, name in shooters.items()
               if k in traces]
    rows, intervals = [], []
    for k, name, tr in sorted(matched, key=lambda x: parse_key(x[0])):
        rt = tr.get("release_t")
        if isinstance(rt, (int, float)):
            intervals.append(parse_key(k) - rt)
        imgs = "".join(f'<img src="{rel}" style="max-height:200px">'
                       for rel in tr.get("thumbs") or [])
        rows.append(f"<tr><td>{k}</td><td>{name}</td>"
                    f"<td>{STATUS_TEXT.get(tr.get('status'), tr.get('status') or '')}"
                    f"</td><td>{imgs}</td></tr>")
    out_html = ("<html><meta charset='utf-8'><body>"
                f"<h3>出手人对照表（{len(matched)}/{len(shooters)} 条标注"
                "有溯源）</h3>"
                "<table border=1 cellpadding=6><tr><th>时间</th>"
                "<th>标注出手人</th><th>溯源档</th>"
                "<th>射手缩略图（帧A带出手点十字）</th></tr>"
                + "".join(rows) + "</table></body></html>")
    out = f"{work_dir}/shooters_check.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(out_html)

    n = Counter(tr.get("status") for tr in traces.values())
    print(f"溯源球数 {len(traces)}：🟢{n.get('high', 0)} "
          f"🟡{n.get('review', 0)} ⚪{n.get('low', 0)}")
    print(f"标注对照 {len(matched)} 条（无溯源的标注 "
          f"{len(shooters) - len(matched)} 条）")
    if intervals:
        ok = sum(1 for i in intervals if 0.3 <= i <= 4.0)
        print(f"出手→进球间隔在 0.3~4.0s 合理区：{ok}/{len(intervals)}"
              "（离群=可能追错轨迹，对照表里重点核）")
    print(f"对照表已生成：{out}——浏览器打开，逐条核"
          "'缩略图里是不是标注的人'")


if __name__ == "__main__":
    main()
