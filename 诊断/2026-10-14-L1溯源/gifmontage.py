# -*- coding: utf-8 -*-
"""从动图取 首/中/末 三帧拼网格，观察十字跟球轨迹。
用法: python gifmontage.py <场次> <起> <数量> <输出>
"""
import json
import os
import sys

from PIL import Image, ImageDraw, ImageSequence

WORK = r"D:/claude code/视频剪辑/work"
TRUTH = r"D:/claude code/视频剪辑YOLO/truth"


def main():
    vname, start, cnt, out = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
    d = os.path.join(WORK, vname)
    tr = json.load(open(os.path.join(d, "shooters.json"), encoding="utf-8"))["traces"]
    sh = {}
    tp = os.path.join(TRUTH, vname + ".json")
    if os.path.isfile(tp):
        sh = json.load(open(tp, encoding="utf-8")).get("shooters", {})

    def pk(k):
        m, s = k.split(":")
        return int(m) * 60 + float(s)

    keys = sorted(tr, key=pk)[start:start + cnt]
    cell_h, cell_w = 320, 240
    cols = 3 * 4          # 每行 4 球
    ncol_block = 3        # 每球 3 帧
    per_row = 4
    rows = (len(keys) + per_row - 1) // per_row
    canvas = Image.new("RGB", (per_row * ncol_block * cell_w,
                               rows * (cell_h + 22)), (245, 245, 248))
    dr = ImageDraw.Draw(canvas)
    for i, k in enumerate(keys):
        v = tr[k]
        r, c = divmod(i, per_row)
        thumbs = v.get("thumbs") or []
        gifrel = thumbs[0].rsplit("_", 1)[0] + ".gif" if thumbs else None
        gp = os.path.join(d, gifrel) if gifrel else None
        cap = f"{k} [{sh.get(k,'?')}] {v.get('status')}"
        dr.text((c * ncol_block * cell_w + 4, r * (cell_h + 22) + 2),
                cap, fill=(20, 20, 20))
        if gp and os.path.isfile(gp):
            frames = [f.copy().convert("RGB") for f in ImageSequence.Iterator(Image.open(gp))]
            pick = [frames[0], frames[len(frames) // 2], frames[-1]]
            for j, im in enumerate(pick):
                sc = cell_w / im.width
                im2 = im.resize((cell_w, int(im.height * sc)))
                canvas.paste(im2, (c * ncol_block * cell_w + j * cell_w,
                                   r * (cell_h + 22) + 20))
        else:
            dr.text((c * ncol_block * cell_w + 4, r * (cell_h + 22) + 20),
                    "(无动图)", fill=(180, 0, 0))
    canvas.save(out)
    print("saved", out, canvas.size, "n=", len(keys))


if __name__ == "__main__":
    main()
