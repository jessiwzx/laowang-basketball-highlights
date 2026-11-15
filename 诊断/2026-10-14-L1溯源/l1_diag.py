# -*- coding: utf-8 -*-
"""L1 溯源诊断：聚合 work/*/shooters.json + truth/*.json，量化"不准"。
只读，不改任何项目文件。"""
import glob
import json
import os
import statistics

WORK = r"D:/claude code/视频剪辑/work"
TRUTH = r"D:/claude code/视频剪辑YOLO/truth"


def parse_key(k):
    m, s = k.split(":")
    return int(m) * 60 + float(s)


def main():
    tmap = {}
    for p in glob.glob(os.path.join(TRUTH, "*.json")):
        name = os.path.basename(p)[:-5]
        tmap[name] = json.load(open(p, encoding="utf-8"))

    rows = []
    agg_status = {"high": 0, "review": 0, "low": 0}
    agg_spot = {"ok": 0, "bad": 0, None: 0}
    agg_extrap = 0
    agg_err = 0
    all_intervals = []
    total_traced = 0
    total_annot = 0
    matched = 0

    for d in sorted(glob.glob(os.path.join(WORK, "*"))):
        sj = os.path.join(d, "shooters.json")
        if not os.path.isfile(sj):
            continue
        vname = os.path.basename(d)
        data = json.load(open(sj, encoding="utf-8"))
        tr = data.get("traces") or {}
        if not tr:
            continue
        n = len(tr)
        total_traced += n
        sc = {"high": 0, "review": 0, "low": 0}
        spb = 0
        ex = 0
        er = 0
        ivs = []
        for k, v in tr.items():
            st = v.get("status")
            sc[st] = sc.get(st, 0) + 1
            if v.get("spot") == "bad":
                spb += 1
            if v.get("extrap"):
                ex += 1
            if v.get("err"):
                er += 1
            rt = v.get("release_t")
            if isinstance(rt, (int, float)):
                iv = parse_key(k) - rt
                ivs.append(iv)
                all_intervals.append(iv)
        for kk in sc:
            agg_status[kk] += sc[kk]
        agg_spot["bad"] += spb
        agg_extrap += ex
        agg_err += er

        # 与 truth 标注对照
        truth = tmap.get(vname)
        ann = 0
        mt = 0
        if truth:
            sh = truth.get("shooters", {})
            ann = len(sh)
            total_annot += ann
            mt = sum(1 for k in sh if k in tr)
            matched += mt
        # 合理区占比
        ok = sum(1 for i in ivs if 0.3 <= i <= 4.0)
        rows.append((vname, n, sc["high"], sc["review"], sc["low"],
                     spb, ex, er, ann, mt, ok, len(ivs)))

    print("=" * 100)
    print(f"{'场次':<16}{'溯源':>5}{'🟢':>5}{'🟡':>5}{'⚪':>5}{'spot_bad':>9}{'extrap':>7}{'err':>5}{'标注':>5}{'命中':>5}{'合理区':>9}")
    print("-" * 100)
    for r in rows:
        print(f"{r[0]:<16}{r[1]:>5}{r[2]:>5}{r[3]:>5}{r[4]:>5}{r[5]:>9}{r[6]:>7}{r[7]:>5}{r[8]:>5}{r[9]:>5}{str(r[10])+'/'+str(r[11]):>9}")
    print("-" * 100)
    print(f"合计溯源 {total_traced} 球：🟢{agg_status['high']} "
          f"🟡{agg_status['review']} ⚪{agg_status['low']} "
          f"| spot_bad {agg_spot['bad']} | extrap {agg_extrap} | err {agg_err}")
    print(f"truth 标注合计 {total_annot} 条，其中有溯源 {matched} 条 "
          f"（{matched/max(total_annot,1)*100:.1f}%）")
    if all_intervals:
        ok = sum(1 for i in all_intervals if 0.3 <= i <= 4.0)
        print(f"出手→进球间隔：合理区(0.3~4.0s) {ok}/{len(all_intervals)} "
              f"({ok/len(all_intervals)*100:.1f}%)")
        print(f"  中位 {statistics.median(all_intervals):.2f}s  "
              f"均值 {statistics.mean(all_intervals):.2f}s  "
              f"最大 {max(all_intervals):.2f}s  最小 {min(all_intervals):.2f}s")
        # 离群：间隔 <0.3（十字几乎贴着进球=没追到持球段）或 >4（追到很久前）
        lo = sum(1 for i in all_intervals if i < 0.3)
        hi = sum(1 for i in all_intervals if i > 4.0)
        neg = sum(1 for i in all_intervals if i < 0)
        print(f"  <0.3s（几乎无回追=疑似锁成筐/进球点）{lo}  "
              f">4.0s（疑似追过头/追错链）{hi}  负值 {neg}")


if __name__ == "__main__":
    main()
