"""接口层自测（2026-10-21 单条视频生成）——真服务 TestClient + **真渲染**。

要验的四件事（这些只有在 HTTP 全链路上才看得见，单测是打桩的）：
1. `/api/gen/choices` 四种组合的契约：中性 stats / 画幅 stats / allLabel 换文案；
2. `output=reel` 回归：仍是一项一个 `highlight-竖.mp4`，结果能被 `_gen_result` 反解；
3. `output=single`：N 球 → N 个 `single-竖-<标签>-<时刻>.mp4`，且 `videos` 全部可播；
4. P0-4 的 pct 通道真的通：2 球时中途必须出现 50（不能只靠 job 结束时被置 100）。

隔离：元数据复制到 `work/_selftest`、`A._work_dir_for` 指过去、`selection.json`
只勾 2 个球 ⇒ 绝不触碰 `work/2026-10-17_1` 里用户真实的 highlight-*.mp4。
"""
import json
import os
import shutil
import sys
import time

ROOT = "D:/claude code/视频剪辑YOLO"
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import bbh.app as A                                          # noqa: E402
from bbh.models import load_events                            # noqa: E402
from fastapi.testclient import TestClient                     # noqa: E402

from bbh.web.app import create_app                            # noqa: E402

SRC = "work/2026-10-17_1"
MDW = "work/_selftest"
VIDEO = load_events(os.path.join(SRC, "events.json"))["video"]
TS = [16.43, 26.40]                     # 与 Step 0 / smoke 同一批球
TAGS = {16.43: "老张", 26.40: ""}        # 一球有标签、一球空 ⇒ 验「未标」兜底

fails = []


def check(cond, label, detail=""):
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        fails.append(label)


# ---------- 隔离目录 ----------
shutil.rmtree(MDW, ignore_errors=True)
os.makedirs(MDW, exist_ok=True)
for n in os.listdir(SRC):
    if n in ("events.json", "shooters.json") or n.startswith("calibration"):
        shutil.copy2(os.path.join(SRC, n), os.path.join(MDW, n))
keys = [A._fmt_t(t) for t in TS]
with open(os.path.join(MDW, "selection.json"), "w", encoding="utf-8") as f:
    json.dump(keys, f)
with open(os.path.join(MDW, A.ASSIGN_FILE), "w", encoding="utf-8") as f:
    json.dump({A._fmt_t(t): v for t, v in TAGS.items() if v}, f)

A._work_dir_for = lambda vp: MDW
c = TestClient(create_app())

r = c.post("/api/session", json={"videoPath": VIDEO})
check(r.status_code == 200, "session 设置视频", r.text[:200])


def choices(vertical="unset", output="reel"):
    p = {"output": output}
    if vertical != "unset":
        p["vertical"] = vertical
    resp = c.get("/api/gen/choices", params=p)
    return resp.status_code, resp.json()


def gen(payload, timeout=420):
    r = c.post("/api/gen", json=payload)
    if r.status_code != 200:
        return None, r.status_code, r.text[:300]
    jid = r.json()["jobId"]
    pcts, msgs, t0 = [], [], time.time()
    while time.time() - t0 < timeout:
        j = c.get(f"/api/jobs/{jid}").json()
        if j["pct"] not in pcts:
            pcts.append(j["pct"])
        if j["msg"] and (not msgs or msgs[-1] != j["msg"]):
            msgs.append(j["msg"])
        if j["state"] != "running":
            break
        time.sleep(0.4)
    else:
        return None, "timeout", f"{timeout}s 未完成"
    if j["state"] == "error":
        return None, "error", j.get("error")
    res = c.get(f"/api/jobs/{jid}/result").json()
    return res, pcts, msgs


print("\n[1] /api/gen/choices 四种组合")
st, j = choices("unset", "reel")
check(st == 200, "choices 200（不传 vertical）", st)
check(str(j.get("stats", "")).startswith("📊 "), "未选画幅 → 中性 stats（📊 开头）", j.get("stats"))
check("竖屏" not in j.get("stats", "") and "横屏" not in j.get("stats", ""),
      "中性 stats 不含画幅字样", j.get("stats"))
check(j.get("vertical") is None, "vertical 回 null（不是被归一成 False）", j.get("vertical"))
check(j.get("allLabel") == A.GEN_ALL_LABEL, "reel 的 allLabel = 旧合集文案", j.get("allLabel"))
check(j.get("choices", [None])[0] == A.GEN_ALL_LABEL, "reel choices[0] = 旧合集文案")

st, jv = choices(True, "reel")
check(jv.get("stats", "").startswith("📱 竖屏运镜"), "vertical=true → 竖屏运镜 stats", jv.get("stats"))
st, jh = choices(False, "reel")
check(jh.get("stats", "").startswith("🎬 横屏直切"), "vertical=false → 横屏直切 stats", jh.get("stats"))

st, js = choices("unset", "single")
check(js.get("allLabel") == A.GEN_ALL_LABEL_SINGLE, "single 的 allLabel = 逐球文案", js.get("allLabel"))
check(js.get("choices", [None])[0] == A.GEN_STAR_LABEL,
      "single choices[0] = ★ 精选（2026-10-23 置顶）", js.get("choices", [])[:1])
check(js.get("choices", [None])[1] == A.GEN_ALL_LABEL_SINGLE,
      "single choices[1] = 逐球文案（不是旧文案）", js.get("choices", [])[:2])
check(A.GEN_ALL_LABEL not in js.get("choices", []), "single 清单里没有旧合集文案")
check(js.get("default") == [] and js.get("starred") == 0,
      "没打星 ⇒ single 默认不预选任何项（不许预选合集，否则一次全出）", js.get("default"))
check(js.get("stats", "").startswith("📊 "), "single 未选画幅 → 中性 stats（📊 开头）", js.get("stats"))

print("\n[2] reel 竖屏（回归：一项一条）")
res, pcts, msgs = gen({"vertical": True, "output": "reel", "items": [A.GEN_ALL_LABEL]})
check(isinstance(res, dict), "reel 任务成功", f"{pcts} {msgs}")
if isinstance(res, dict):
    names = [v["name"] for v in res.get("videos", [])]
    check(names == ["highlight-竖.mp4"], "reel 产出 = highlight-竖.mp4", names)
    check(res.get("output") == "reel", "结果回显 output=reel", res.get("output"))
    check(bool(res.get("firstUrl")), "firstUrl 可播", res.get("firstUrl"))
    check(os.path.exists(os.path.join(MDW, "highlight-竖.mp4")), "文件真在 work 下")

print("\n[3] single 竖屏（2 球 → 2 个文件）")
res, pcts, msgs = gen({"vertical": True, "output": "single",
                       "items": [A.GEN_ALL_LABEL_SINGLE]})
check(isinstance(res, dict), "single 任务成功", f"{pcts} {msgs}")
if isinstance(res, dict):
    names = sorted(v["name"] for v in res.get("videos", []))
    check(len(names) == 2, "2 球出 2 条", names)
    check(all(n.startswith("single-竖-") and n.endswith(".mp4") for n in names),
          "命名前缀 single-竖-*.mp4", names)
    check(any("老张" in n for n in names), "有标签的球文件名带标签", names)
    check(any("未标" in n for n in names), "空标签走「未标」兜底", names)
    check(sorted(pcts)[-2:] == [50, 100] if len(pcts) >= 2 else False,
          "pct 中途出现 50（on_progress 真通了，不是只靠结束置 100）", pcts)
    check(res.get("output") == "single", "结果回显 output=single", res.get("output"))
    check(all(v["url"] for v in res["videos"]), "每条都有可播 url", res["videos"])
    check(all(os.path.getsize(os.path.join(MDW, v["name"])) > 100_000
              for v in res["videos"]), "成品体积 >100KB（不是空文件）")

print("\n[4] single 横屏（同一批球，直切）")
res, pcts, msgs = gen({"vertical": False, "output": "single",
                       "items": [A.GEN_ALL_LABEL_SINGLE]})
check(isinstance(res, dict), "single 横屏任务成功", f"{pcts} {msgs}")
if isinstance(res, dict):
    names = sorted(v["name"] for v in res.get("videos", []))
    check(len(names) == 2 and all(n.startswith("single-横-") for n in names),
          "命名 single-横-*.mp4 ×2", names)

print("\n[5] 全跳过不假装成功（勾一个本场无球的标签）")
res, pcts, msgs = gen({"vertical": True, "output": "single", "items": ["不存在的标签"]})
check(isinstance(res, dict), "全跳过仍返回结果（不 raise）", f"{pcts} {msgs}")
if isinstance(res, dict):
    check(res.get("videos") == [], "全跳过 → videos 为空", res.get("videos"))
    check("❌" in res.get("summaryMd", ""), "全跳过文案带 ❌", res.get("summaryMd", "")[:80])

print("\n[6] ★ 精选（2026-10-23）：打一颗星 → 单条只出这一条")
key0 = A._fmt_t(TS[0])
r = c.patch(f"/api/events/{key0}", json={"starred": True})
check(r.status_code == 200, "PATCH starred=true", r.text[:200])
_st, jj = choices("unset", "single")
check(jj.get("starred") == 1, "choices 回 starred=1", jj.get("starred"))
check(jj.get("default") == [A.GEN_STAR_LABEL],
      "单条默认勾 ★ 精选（有星时）", jj.get("default"))
row0 = next((x for x in r.json()["rows"] if x["key"] == key0), {})
check(row0.get("starred") is True and row0.get("selection") is True,
      "打星自动补勾进片（不变式 精选 ⊆ 进片）", row0)
res, pcts, msgs = gen({"vertical": True, "output": "single",
                       "items": [A.GEN_STAR_LABEL]})
check(isinstance(res, dict), "精选项生成成功", f"{pcts} {msgs}")
if isinstance(res, dict):
    names = [v["name"] for v in res.get("videos", [])]
    check(len(names) == 1, "勾「★ 精选」只出 1 条（勾了 2 个球也只出打星的）", names)
    check(names and "老张" in names[0], "命名带标签", names)
# 取消勾选要连带清星（否则「精选了但没进片」）
r = c.patch(f"/api/events/{key0}", json={"selection": False})
row0 = next((x for x in r.json()["rows"] if x["key"] == key0), {})
check(row0.get("starred") is False, "取消进片 → 星自动清掉", row0)

print("\n[7] ★ 精选 × 一条集锦（2026-10-24）：只把打星的球合成一条")
r = c.patch(f"/api/events/{key0}", json={"starred": True})
check(r.status_code == 200, "重新打星", r.text[:200])
_st, jr = choices(True, "reel")
check(jr.get("starLabel") == A.GEN_STAR_LABEL_REEL,
      "集锦 starLabel = 「打星的球合成一条」那串", jr.get("starLabel"))
check(jr.get("choices", [None])[0] == A.GEN_STAR_LABEL_REEL,
      "集锦清单头部是精选项", jr.get("choices", [])[:2])
check(jr.get("default", [None])[0] == A.GEN_ALL_LABEL,
      "集锦默认仍勾合集（不预选精选）", jr.get("default"))
check("★ 已精选 1 球" in jr.get("stats", ""), "集锦统计行带精选数", jr.get("stats"))
# 同一批里两项都勾：必须出两个**不同名**文件——同名的后者会覆盖前者
res, pcts, msgs = gen({"vertical": True, "output": "reel",
                       "items": [A.GEN_ALL_LABEL, A.GEN_STAR_LABEL_REEL]})
check(isinstance(res, dict), "集锦「合集 + 精选」同批生成成功", f"{pcts} {msgs}")
if isinstance(res, dict):
    names = sorted(v["name"] for v in res.get("videos", []))
    check(names == sorted(["highlight-竖.mp4", "highlight-竖-精选.mp4"]),
          "两个产物文件名不撞（精选不覆盖合集）", names)
    check(all(v.get("url") for v in res.get("videos", [])), "两个产物都可播", names)
    p_all = os.path.join(MDW, "highlight-竖.mp4")
    p_star = os.path.join(MDW, "highlight-竖-精选.mp4")
    if os.path.exists(p_all) and os.path.exists(p_star):
        check(os.path.getsize(p_star) < os.path.getsize(p_all),
              "精选那条更短（只含打星的 1 球，合集含 2 球）",
              f"{os.path.getsize(p_star)} vs {os.path.getsize(p_all)}")

print("\n===== 汇总 =====")
print(f"失败 {len(fails)} 项" + ("" if not fails else "：" + "；".join(fails)))
print("隔离目录：", os.path.abspath(MDW))
sys.exit(1 if fails else 0)
