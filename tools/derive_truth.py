# -*- coding: utf-8 -*-
r"""从剪辑台 `selection.json` 派生 `truth/<场>.json`（查准回归集）。

背景
----
`work/<场>/selection.json` 是剪辑台的最终球单——用户逐条勾过、删过的球，
**就是这一场的真值清单**（2026-10-17 R-020 Step 0 已认定「缺 truth 的场可
直接导出」）。把最近几场标注接进固定回归集，查准样本从 6 场 771 球扩到
12 场 1500+ 球，改判定逻辑时有更大的对拍面。

**重要口径限制（写进产物字段，别忘）**
selection 是**从当版 events 表里勾出来的**，所以 truth ⊆ events ⇒ 漏检在这
些场上结构恒为 0。它们只能用于：

  · 查准（high 里有多少是用户没勾的）
  · 自动档覆盖率（真球里有多少落进 high）

**不能**用来算查全（分母缺「表里根本没有的球」）。查全仍只看老七场——
那些 truth 来自 HSV 检出表（异源），YOLO 漏的球仍在真值里。

用法
----
    # 扫 work/*/selection.json，缺 truth 的场次全部派生
    .venv\Scripts\python tools\derive_truth.py

    # 只派生指定场次
    .venv\Scripts\python tools\derive_truth.py 2026-11-06_1

    # 预览（不写文件）
    .venv\Scripts\python tools\derive_truth.py --dry-run

    # 已有派生真值且球单变了 → 允许重写（只覆盖 truth_kind=selection 的文件）
    .venv\Scripts\python tools\derive_truth.py --force

安全约束
--------
· 默认**绝不覆盖**已存在的 truth/<场>.json（老七场是原始标注）。
· `--force` 只覆盖 `truth_kind == "selection"` 的派生文件；原始标注即使
  加 --force 也拒绝改写——那是不可再生的手工资产。
· 产物写前先做对齐体检：真值点能否在 0.2s 内认回事件行。认回率 <95% 直接
  报错不写（说明 selection 与当前 events 不是同一版，派生出的真值会系统性
  错位）。
"""
import argparse
import glob
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = os.path.join(ROOT, "work")
TRUTH = os.path.join(ROOT, "truth")

TOLERANCE = 0.2          # 行锚定口径（selection 键就是事件时刻）
ANCHOR_MIN_RATE = 0.95   # 对齐体检下限


def parse_ts(s) -> float:
    """'mm:ss.s' / 'h:mm:ss.s' / 数字 → 秒。"""
    if isinstance(s, (int, float)):
        return float(s)
    v = 0.0
    for p in str(s).strip().split(":"):
        v = v * 60 + float(p)
    return v


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def selection_goals(path):
    """selection.json → (秒列表, 坏条数)。兼容字符串与 {'t': ...} 两种形态。"""
    sel = read_json(path)
    goals, bad = [], 0
    for item in sel:
        try:
            t = item.get("t") if isinstance(item, dict) else item
            goals.append(round(parse_ts(t), 3))
        except (TypeError, ValueError, AttributeError):
            bad += 1
    return sorted(set(goals)), bad


def anchor_check(goals, event_ts):
    """真值点 → 0.2s 内能认回事件行的比例（时间轴一致性体检）。"""
    hit = sum(1 for g in goals if any(abs(g - t) <= TOLERANCE for t in event_ts))
    return hit, (hit / len(goals) if goals else 0.0)


def build(video, dry_run=False, force=False):
    sel_p = os.path.join(WORK, video, "selection.json")
    ev_p = os.path.join(WORK, video, "events.json")
    out_p = os.path.join(TRUTH, video + ".json")

    if not os.path.exists(sel_p):
        return {"video": video, "ok": False, "err": "无 selection.json"}
    if not os.path.exists(ev_p):
        return {"video": video, "ok": False, "err": "无 events.json（无法取标定/时长）"}

    if os.path.exists(out_p):
        old = read_json(out_p)
        if old.get("truth_kind") != "selection":
            return {"video": video, "ok": False,
                    "err": "truth 已存在且是原始标注（非派生物），拒绝改写"}
        if not force:
            return {"video": video, "ok": False, "skip": True,
                    "err": "truth 已存在（派生），加 --force 才重写"}

    ev = read_json(ev_p)
    goals, bad = selection_goals(sel_p)
    ev_ts = [e["t"] for e in ev.get("events", [])]
    hit, rate = anchor_check(goals, ev_ts)
    if rate < ANCHOR_MIN_RATE:
        return {"video": video, "ok": False,
                "err": f"对齐体检不过：0.2s 认回率 {rate:.1%} < {ANCHOR_MIN_RATE:.0%}"
                       "（selection 与当前 events 不是同一版？）"}

    mtime = time.localtime(os.path.getmtime(sel_p))
    doc = {
        "video": video,
        "source_video": ev.get("video"),
        "calib_key": ev.get("calib_key"),
        "duration": ev.get("duration"),
        "annotated_at": time.strftime("%Y-%m-%d", mtime),
        "logic_ver_at_annotation": ev.get("logic_ver"),
        "complete": True,
        "goals": goals,
        "tolerance": TOLERANCE,
        "derived_from": "work/%s/selection.json" % video,
        "truth_kind": "selection",
        "anchor_check": {"tol": TOLERANCE, "hit": hit, "n": len(goals),
                         "rate": round(rate, 4)},
        "note": ("由剪辑台 selection.json 派生（查准回归集）。selection 即最终进球"
                 "清单，truth ⊆ events ⇒ 漏检恒 0，**只用于查准 / 自动档覆盖率，"
                 "不用于查全**。对齐体检 %d/%d 个真值点能在 %.1fs 内认回事件行。"
                 % (hit, len(goals), TOLERANCE)),
    }
    if bad:
        doc["anchor_check"]["bad_items"] = bad

    info = {"video": video, "ok": True, "goals": len(goals), "bad": bad,
            "hit": hit, "rate": rate, "out": out_p,
            "logic_ver": ev.get("logic_ver"), "wrote": False}
    if not dry_run:
        os.makedirs(TRUTH, exist_ok=True)
        tmp = out_p + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, ensure_ascii=False, indent=1)
        os.replace(tmp, out_p)
        info["wrote"] = True
    return info


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("videos", nargs="*", help="场次名；空 = 扫 work/*/selection.json")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="允许重写已有派生真值（原始标注永远拒绝）")
    args = ap.parse_args(argv)

    videos = args.videos or sorted(
        os.path.basename(os.path.dirname(p))
        for p in glob.glob(os.path.join(WORK, "*", "selection.json")))

    print("=== 派生真值（selection → truth/）%s" % ("[dry-run]" if args.dry_run else ""))
    done, skipped, failed = [], [], []
    for v in videos:
        r = build(v, args.dry_run, args.force)
        if r["ok"]:
            mark = "写入" if r["wrote"] else "预览"
            print("  ✓ %-14s %3d 球  对齐 %d/%d (%.1f%%)  logic v%s  → %s"
                  % (v, r["goals"], r["hit"], r["goals"], 100 * r["rate"],
                     r["logic_ver"], mark))
            done.append(v)
        elif r.get("skip"):
            print("  - %-14s 跳过：%s" % (v, r["err"]))
            skipped.append(v)
        else:
            print("  ✗ %-14s %s" % (v, r["err"]))
            failed.append(v)

    print("\n完成 %d / 跳过 %d / 失败 %d" % (len(done), len(skipped), len(failed)))
    print("提醒：派生真值只能算查准与自动档覆盖率，查全仍只看老七场（truth 源自 HSV 表）。")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
