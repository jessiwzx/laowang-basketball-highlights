# -*- coding: utf-8 -*-
"""汇总所有场的"可疑球"（spot_bad / low / 间隔离群 / extrap），拼对照网格。
用法: python badcases.py <输出> [标签: 全部|spot|low|ivout|extrap]
"""
import glob
import json
import os
import sys

from PIL import Image, ImageDraw

WORK = r"D:/claude code/视频剪辑/work"
TRUTH = r"D:/claude code/视频剪辑YOLO/truth"


def pk(k):
    m, s = k.split(":")
    return int(m) * 60 + float(s)


def main():
    out = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "all"
    cases = []
    for d in sorted(glob.glob(os.path.join(WORK, "*"))):
        sj = os.path.join(d, "shooters.json")
        if not os.path.isfile(sj):
            continue
        vname = os.path.basename(d)
        tr = json.load(open(sj, encoding="utf-8")).get("traces") or {}
        sh = {}
        tp = os.path.join(TRUTH, vname + ".json")
        if os.path.isfile(tp):
            sh = json.load(open(tp, encoding="utf-8")).get("shooters", {})
        for k, v in tr.items():
            rt = v.get("release_t")
            iv = (pk(k) - rt) if isinstance(rt, (int, float)) else None
            tag = None
            if v.get("spot") == "bad":
                tag = "SPOT"
            elif v.get("status") == "low":
                tag = "LOW"
            elif iv is not None and not (0.3 <= iv <= 4.0):
                tag = "IV" + f"{iv:.1f}"
            elif v.get("extrap"):
                tag = "EX"
            if tag and (mode == "all" or mode.lower() in tag.lower() or mode == tag):
                thumbs = v.get("thumbs") or []
                cases.append((vname, k, tag, sh.get(k, "?"), thumbs,
                              os.path.join(d, thumbs[0]) if thumbs else None))
    cell_w, cell_h = 300, 330
    cols = 8
    rows = (len(cases) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * cell_w, rows * cell_h), (245, 245, 248))
    dr = ImageDraw.Draw(canvas)
    for i, (vname, k, tag, name, thumbs, p) in enumerate(cases):
        r, c = divmod(i, cols)
        x0, y0 = c * cell_w, r * cell_h
        dr.text((x0 + 3, y0 + 3), f"{vname.replace('2026-','')[:5]} {k} [{name}] {tag}",
                fill=(180, 20, 20))
        if p and os.path.isfile(p):
            im = Image.open(p).convert("RGB")
            sc = (cell_w - 6) / im.width
            im2 = im.resize((cell_w - 6, int(im.height * sc)))
            if im2.height > cell_h - 22:
                sc = (cell_h - 22) / im.height
                im2 = im.resize((int(im.width * sc), cell_h - 22))
            canvas.paste(im2, (x0 + 3, y0 + 20))
    canvas.save(out)
    print("saved", out, canvas.size, "n=", len(cases))


if __name__ == "__main__":
    main()
