# 网证据进置信度、档位阈值化（v18）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 网信号从「直接改档的暗门」变成置信度的加减分项（静止 −0.20 / 非局部 −0.10 / 网动或未测 0），测量覆盖全部事件行，档位 = 硬门档 if 硬门 else 阈值分箱(conf)；删除 R1 升绿 / P1d 降档两条 status 旁路。

**Architecture:** `decide_goals` 仍产**几何 conf** + 初始档位 + 硬门标记（新字段 `GoalEvent.review_reason`）；`net_evidence_pass` 全行测网 → 网分回写 conf → 仅对非硬门行按 HIGH_CONF/MIN_CONF 重分箱；`net_cluster_pass` 给 B2 注入行挂 `review_reason="b2"` 并补 net_* 实测值（仅显示、分 0）；UI 依据卡算式三处（api_session / app.py / BasisPanel.vue）加网分项。公式文本不变，只是最后一项的求值时机后移到 net pass。

**Tech Stack:** Python 3 (./.venv)、pytest、OpenCV、Vue3 + Vite（frontend → bbh/static）、Playwright e2e。

**Spec:** `docs/superpowers/specs/2026-10-11-net-confidence-design.md`（计划从 spec 出发，执行者两份都读）

## Global Constraints

- Python 一律 `./.venv/Scripts/python.exe -X utf8`（下文简写 `python`）。共享 Python 模块改动后跑**全量** pytest，且单独跑（不并发视频诊断）。
- **绝不碰用户的 `work/` 状态**：重放/实验一律 Temp 或 `诊断/` 副本（`_work_dir_for` 返回相对路径按 cwd 解析的坑）。仓库根 6 档（calibration*.json/png、tags.json、ui_prefs.json）一字节不动。
- ⛔ 绝不手动运行 `python -m bbh.web`；绝不占用/探测用户的 7860 实例。e2e 由 pytest 夹具自己起服务。
- `LOGIC_VERSION` 17→**18**；`NET_SIGNAL_VER=2` 不动（测量参数未变，sidecar 增量复用）；`DETECT_VERSION` 不动。
- **网动不给正分**（`NET_SCORE_SWING=0.0`，spec §3.1 铁结论——擦网/砸筐传振同样让网动）；fail-open=0 分且不改档。
- UI 判定依据算式「改一处要改两处」：`bbh/web/api_session.py` 与 `bbh/app.py:_reason_text` 同步改，`BasisPanel.vue` 用服务端 terms 不自拼。前端改动后 `cd frontend && npm run build` 重建 `bbh/static`。
- e2e 断言替换**保持 ok/eq 调用数量不变**（项数门钉了 spec 文件里的断言数）；EP toast 类断言不适用于本轮。
- 提交纪律：本计划每任务末尾的 commit 步骤属于**已批准的工作流**（与 v15/v16 轮同款）；**绝不 push、绝不合并**。若用户在计划审阅时裁定不提交，则所有 commit 步骤整体跳过、其余不变。
- 历史版本注释（pipeline.py 里 v2~v17 注释块）是史实记录**不改写**；只改「活注释」（函数 docstring、调用点行内注释）。

## Review Focus

规格隐含、但各任务测试未必覆盖、最可能咬人的五类输入（每条后括号 = 钉住它的任务）：

1. **旧版 events.json（≤v17）缺 `review_reason` 键** → `GoalEvent.from_dict` 必须缺省 None 不崩，否则所有存量档一加载就炸（Task 2 测试 `test_旧档缺review_reason键可加载`）。
2. **硬门行测网后档位被分箱偷改**：low_reason / review_reason / pop_back 行即使网静止扣到 0.15 也必须保持原档；反之 fallback/b2 行不得被网分推回 high（Task 3 `test_硬门行测网只记录_档位不动分值照扣` + Task 2 fallback 用例 + Task 4 b2 用例）。
3. **round 浮点边界**：0.70−0.20 在浮点下是 0.4999…，若不 round 就分箱会掉 low——「静止恰把 high 压到 review 而非 low」这个对齐现行网门行为的锚点就断了（Task 3 `test_网分round边界_070减020恰落050review`）。
4. **旧档 UI 算式对不上账**：≤v17 行的 conf 不含网分，公式和 ≠ 显示值（如 0.75 ≠ 0.3+0.2+0.2+0.05−0.20）→ terms 带 `legacy` 标注，否则用户以为算错了（Task 5 e2e + `_terms` 单元逻辑）。
5. **v14 冷却释放的 demoted 集口径**：几何 high 行被网分压到 review 必须仍进 demoted 集（pre_status 快照在 net pass 之前取，代码不动即成立）——一旦有人在 net pass 里改 conf 之外的顺序就会哑弹（Task 3 跑 test_pipeline 冷却用例 + Task 6 重放抽查）。

---

### Task 0: 前置提交——把已验收未提交的 ①/③/docs 各自落成独立提交

**Files:**
- Modify（已改未提交，本任务只 `git add` 分组提交，不改内容）: `bbh/net_clusters.py`、`bbh/pipeline.py`、`tests/test_net_clusters.py`（=①）；`frontend/src/shared/tagmru.js`、`frontend/src/edit/EditBay.vue`、`frontend/src/wall/WallBay.vue`、`bbh/static/`（=③）；`README.md`（=文档更新）；`docs/superpowers/specs/2026-10-11-net-confidence-design.md`、本计划文件（=docs）

**Interfaces:**
- Consumes: 工作区现状（① v17 已全量 737/1 绿 + 10-10_1 重放验收；③ MRU 已随全量绿）。
- Produces: 干净树——v18 各任务的提交才不会混进 ①③ 的改动。

- [ ] **Step 1: 确认用户已批准提交**（计划审阅时一并问；未批准则本任务与所有 commit 步骤整体跳过）

- [ ] **Step 2: 三个独立提交**

```bash
git add bbh/net_clusters.py bbh/pipeline.py tests/test_net_clusters.py
git commit -m "fix(net_clusters): B2 注入行置信度按链证据计算（问题①，LOGIC_VERSION 17）"
git add frontend/src/shared/tagmru.js frontend/src/edit/EditBay.vue frontend/src/wall/WallBay.vue bbh/static tests/e2e
git commit -m "feat(web): 归人标签 MRU 置顶（剪辑台+出手人墙）"
git add README.md docs/superpowers/specs/2026-10-11-net-confidence-design.md docs/superpowers/plans/2026-10-11-net-confidence.md
git commit -m "docs: v18 网分设计 spec 与实现计划；README 更新"
```

- [ ] **Step 3: 树干净核查**

Run: `git status --short`
Expected: 空（或只剩已忽略文件）。

---

### Task 1: v17 基线重放与八场在位盘点（无代码改动）

**Files:**
- Create: `%TEMP%` 一次性脚本（不入仓）、`诊断/2026-10-11-网分v18/baseline/<场>.events.json` ×9、`诊断/2026-10-11-网分v18/inventory.json`

**Interfaces:**
- Consumes: 当前工作区 v17 代码（**必须在 Task 2 改任何代码之前跑**——基线只能由 v17 行为产出）。
- Produces: 每场 v17 events 基线 + 在位清单，Task 6 的迁移表/阈值扫描/看片清单全部以此为对照。

- [ ] **Step 1: 写基线脚本并跑**

写到 `%TEMP%\v18_baseline.py`（逐字）：

```python
# -*- coding: utf-8 -*-
"""v18 前置：用当前代码（v17 行为）给九场生成基线 events（Temp 重放，
不碰用户 work/）。基线存 诊断/2026-10-11-网分v18/baseline/。
跳过规则：无存档 / 视频不在位（环境受限场）/ positions 身份不符
（需全片重扫——先停报用户，别默默跑几小时）。"""
import json, os, shutil, sys, tempfile, time

REPO = r"D:\claude code\视频剪辑"
FIELDS = ["2026-08-21_1", "2026-08-22_1", "2026-08-28_1", "2026-08-29_3",
          "2026-09-04_1", "2026-09-11_3", "2026-09-12_1", "2026-09-19_1",
          "2026-10-10_1"]   # 末位无 truth：只进迁移表不进指标
COPY = ["positions.json", "net_signals.json", "net_clusters.json", "deleted.json"]

sys.path.insert(0, REPO)
from bbh import calibration            # noqa: E402
from bbh.pipeline import run_detection  # noqa: E402

out_root = os.path.join(REPO, "诊断", "2026-10-11-网分v18", "baseline")
os.makedirs(out_root, exist_ok=True)
inv = []
for f in FIELDS:
    src = os.path.join(REPO, "work", f)
    ev_old = os.path.join(src, "events.json")
    if not os.path.exists(ev_old):
        inv.append({"field": f, "skip": "无 events.json 存档"}); print(inv[-1]); continue
    video = json.load(open(ev_old, encoding="utf-8"))["video"]
    if not os.path.exists(video):
        inv.append({"field": f, "skip": "视频不在位（环境受限场）"}); print(inv[-1]); continue
    pos = os.path.join(src, "positions.json")
    doc = json.load(open(pos, encoding="utf-8")) if os.path.exists(pos) else None
    if not doc or doc.get("video") != video:
        inv.append({"field": f, "skip": "positions 身份不符（需全片重扫）——停，报用户裁定"}); print(inv[-1]); continue
    root = tempfile.mkdtemp(prefix="v18base_")
    wd = os.path.join(root, "work", f)
    os.makedirs(wd)
    for name in COPY:
        p = os.path.join(src, name)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(wd, name))
    for name in os.listdir(src):
        if name.startswith("calibration"):
            shutil.copy2(os.path.join(src, name), os.path.join(wd, name))
    t0 = time.time()
    segs = calibration.load_segments_dir(wd)
    path, warn = run_detection(video, segs, wd)
    rows = json.load(open(path, encoding="utf-8"))["events"]
    shutil.copy2(path, os.path.join(out_root, f"{f}.events.json"))
    inv.append({"field": f, "sec": round(time.time() - t0, 1), "rows": len(rows),
                "video": video})
    print(inv[-1], flush=True)
json.dump(inv, open(os.path.join(os.path.dirname(out_root), "inventory.json"), "w",
                    encoding="utf-8"), ensure_ascii=False, indent=1)
print("baseline →", out_root)
```

Run: `cd /d/claude\ code/视频剪辑 && ./.venv/Scripts/python.exe -X utf8 %TEMP%/v18_baseline.py`（Git Bash 下用 `$TMP` 实际路径）
Expected: 每场一行；全缓存场次秒级（10-10_1 应 <60s）。**任何一行出现 `skip`（尤其「身份不符」）→ 停下来把 inventory 报给用户裁定，不要继续 Task 2 之外的任何重放。**（2026-10-11 盘点：九场视频均在 `D:\claude code\1视频获取\归档\` 在位、events 存的 video 路径与 positions 同源，预期零 skip；08-21_1 truth 为 complete=false 只进回归断言不进指标。）

---

### Task 2: goal_decider v18——删 R1/P1d、`review_reason` 硬门标记、LOGIC_VERSION 18

**Files:**
- Modify: `bbh/models.py:113-139`（GoalEvent 加字段 + from_dict）、`bbh/goal_decider.py:36-52`（删 R1/P1d 常量）、`:402-414`（档位裁决）、`:177-183`（fallback 压绿转硬门）、`bbh/pipeline.py:52`（LOGIC_VERSION）
- Test: `tests/test_goal_decider.py`

**Interfaces:**
- Consumes: 无（首个代码任务）。
- Produces: `GoalEvent.review_reason: str|None`（值域 `"entity"|"geom"|"pop_back"|"fallback"|"b2"`，旧档缺省 None）；`decide_goals` 输出**几何 conf**（0.3+两侧+位置，round 2 位）+ 初始档位（硬门优先于阈值分箱）。Task 3 的重分箱、Task 4 的 `"b2"`、Task 5 的 UI 都依赖此字段。

- [ ] **Step 1: 改既有用例 + 加新用例（先红）**

`tests/test_goal_decider.py` 六处改写 + 一处新增：

(a) L28 `test_只检到交叉前后各一点_干净居中_升绿` 整体替换：

```python
    def test_只检到交叉前后各一点_干净居中_待复核(self):
        """v18（2026-10-11 问题②）：R1 升绿删除——档位只由阈值分箱(conf)+硬门
        决定。「干净居中」不再是旁路升绿理由（被遮挡真球掉回 review 交人工，
        八场定标验证查全影响，劣化再按 spec §3.4 转几何分修正项）。"""
        positions = pts([(20.0, 340, 100), (20.2, 340, 190)])
        events = decide_goals(positions, CALIB)
        assert len(events) == 1
        assert events[0].status == "review"
        assert events[0].confidence == 0.60
```

(b) L135-139 `test_低置信候选保留且不占用冷却` 的断言行与注释改：

```python
        # 压筐沿单点证据（off=−0.44，conf 0.46）→ 低置信；1.2s 后筐心
        # 双点证据的真下穿（conf 0.60）→ v18 按分箱待复核（R1 已删），不被挡
        positions = pts([(90.0, 305, 100), (90.2, 305, 190),
                         (91.2, 340, 100), (91.4, 340, 190)])
        events = decide_goals(positions, CALIB)
        assert [e.status for e in events] == ["low", "review"]
```

(c) L340 `test_球体量级穿越后消失_干净居中升绿_v10R1` 断言段替换（positions 不动）：

```python
        events = decide_goals(positions, CALIB)
        assert len(events) == 1
        assert events[0].status == "review"   # v18：entity 硬门恒 review（v10 R1 曾越过硬门升绿——正是「算式解释不了的档位」）
        assert events[0].review_reason == "entity"
        assert events[0].below_big == 0       # 依据可查
```

(d) L371 `test_穿越后被遮挡的大实体_干净居中升绿_v10R1` 同款翻（positions 不动）：

```python
        e = decide_goals(positions, CALIB)[0]
        assert e.status == "review"
        assert e.review_reason == "entity"
```

(e) L531 `class TestV10档位规则` 改名 `TestV18档位规则`，docstring 换成：

```python
class TestV18档位规则:
    """v18（2026-10-11 问题②，spec §3.3/3.4）：R1 升绿 / P1d 降档两条 status
    旁路删除——档位 = 硬门档 if 硬门 else 阈值分箱(几何 conf)；网分是后置
    pass 的加减分项（见 test_net_motion）。本类钉删除后的边界行为：
    R1 曾救的「干净居中被遮挡真球」回到 review；P1d 曾压的「长悬停+下方
    短现」回到 high（涮框形态交给网分/人工复核兜住）。"""
```

五个用例断言改：L543-548 →

```python
    def test_R1形态_待复核但干净居中无上轨迹_不再升绿(self):
        # 上 1 点 + 下双点短现：conf 0.69 差一线 → 分箱 review（R1 已删）
        positions = pts([(30.0, 340, 100), (30.2, 340, 190), (30.25, 340, 205)])
        e = decide_goals(positions, CALIB)[0]
        assert e.confidence == 0.69           # 本身确实不到 0.7
        assert e.status == "review"           # v18：|off|≈0 也不再旁路升绿
```

L550 / L556 两用例**断言不变**（本来就 review），只把用例名里的「不升」注释口径顺带核对。L566-573 加一行：

```python
        assert e.review_reason == "pop_back"
```

L575-583 P1d 用例改：

```python
    def test_P1d形态_长悬停加下方短现_不再压档(self):
        # 上方 8 点悬停 0.7s + 下方只 0.03s：conf 0.84 → 分箱 high（P1d 已删，
        # 涮框/飞越形态由网分与人工复核兜住——八场定标验证假绿面）
        positions = pts([(40.0 + k * 0.1, 340, 120) for k in range(8)])
        positions += pts([(40.75, 340, 190), (40.78, 340, 200)])
        e = decide_goals(positions, CALIB)[0]
        assert e.confidence == 0.84
        assert e.above_n == 8 and e.below_span <= 0.30
        assert e.status == "high"
```

L585 `test_P1d_长悬停但下方持续_保持绿` 断言不变（仍 high）。

(f) L604-614 兜底用例加一行断言 + 换注释：

```python
        # 伪起点与链首同 x、内插落筐心 → off=0、conf 0.75 够 high 线，
        # v18 起挂 fallback 硬门压绿（原直接改 status；不挂标记会被网分
        # 重分箱推回 high，违背 v11「兜底不升绿」裁定）
        assert events[0].status == "review"
        assert events[0].review_reason == "fallback"
        assert events[0].confidence == 0.75
```

(g) 文件末尾新增：

```python
def test_旧档缺review_reason键可加载():
    # v18 前的 events.json 没有 review_reason 键——from_dict 必须缺省 None
    e = GoalEvent.from_dict({"t": 1.0, "confidence": 0.6, "status": "review"})
    assert e.review_reason is None and e.net_post is None
```

（文件顶部 import 区确认已 `from bbh.models import GoalEvent`，没有则补。）

- [ ] **Step 2: 跑红**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_goal_decider.py -q`
Expected: FAIL（`AttributeError: review_reason` / status 断言不等）。

- [ ] **Step 3: 实现**

`bbh/models.py`——L113-114 `pop_back` 字段之后、`net_post` 之前插入：

```python
    review_reason: Optional[str] = None  # 复核硬门标记（v18）：None=非硬门；
                                        # "entity"=实体未再现 / "geom"=球心压筐沿
                                        # / "pop_back"=疑似弹出 / "fallback"=兜底
                                        # 注入 / "b2"=网簇注入——net pass 重分箱
                                        # 不改这些行的档位（spec §3.3/3.5）。
                                        # 旧版 events.json 无此键 → None
```

`from_dict`（L130-139）在 `pop_back=d.get("pop_back"),` 后插一行 `review_reason=d.get("review_reason"),`。

`bbh/goal_decider.py`——三处：

① L36-52（v10 注释块 + 四个常量）整体替换为：

```python
# v18 档位语义（2026-10-11 问题②，spec docs/superpowers/specs/
# 2026-10-11-net-confidence-design.md §3.3/3.4）：档位 = 硬门档 if 硬门
# else 阈值分箱(conf)。v10 时代的 R1 升绿 / P1d 降档两条 status 旁路删除
# ——其输入特征（off/above_n/below_span）全在两侧分/位置分的自变量里，
# 统一公式后保留只会再造「算式解释不了的档位」。R1 曾救的被遮挡干净真球
# 掉回 review 交人工；八场定标验证查全影响，劣化再按 spec §3.4 转几何分
# 修正项（加在网分之前）。
```

② 档位裁决段（L402-414）替换为：

```python
        review_reason = None
        status = "high" if conf >= HIGH_CONF else "review"
        if entity_review or geom_review or pop_back is not None:
            status = "review"      # 实体未再现/球心压筐沿/疑似弹出：给人工复核
            # v18：硬门落 review_reason——net pass 重分箱时这些行档位不动。
            # （顺带修掉 v10 时代的怪序：R1 曾越过 entity 硬门把这类行升绿，
            # 硬门优先级从此明确写死：entity > geom > pop_back。）
            review_reason = ("entity" if entity_review
                             else "geom" if geom_review else "pop_back")
        if low_reason is not None:
            status = "low"
```

`GoalEvent(...)` 构造（L415-424）在 `pop_back=pop_back` 后加 `review_reason=review_reason`。

③ `decide_goals` 兜底尾部（L177-183）替换为：

```python
    out: list[GoalEvent] = []
    for e in merged:
        # 注入伪点距既有事件 >FALLBACK_EXCL_SEC，不可能改变既有事件
        # （邻域/弹出窗都 ≤1.2s）——merged 里多出来的即兜底事件，压掉升绿
        # v18：改挂 review_reason="fallback" 硬门（原直接改 status）——
        # 否则网分重分箱会把 conf≥0.7 的兜底行推回 high，违背 v11 裁定
        if round(e.t, 2) not in base_ts and e.status == "high":
            e.status = "review"
            e.review_reason = "fallback"
        out.append(e)
    return out
```

`bbh/pipeline.py`——L52 `LOGIC_VERSION = 17` 改 `LOGIC_VERSION = 18`，其上加注释块：

```python
# v18（2026-10-11 问题②）：网证据进置信度、档位阈值化——net_evidence_pass
# 测量对象从 high 扩到全部行，网分（静止 −0.20 / 非局部 −0.10 / 网动或未测
# 0）回写进 conf，非硬门行按 HIGH_CONF/MIN_CONF 重分箱（硬门行测网只记录
# 不改档）；R1 升绿 / P1d 降档两条 status 旁路删除，硬门改挂
# GoalEvent.review_reason 标记；B2 注入行挂 review_reason="b2" 恒 review
# 且补 net_* 实测（仅显示、网分按 0）。NET_SIGNAL_VER 不动（测量参数未变，
# 旧 sidecar 增量复用）；DETECT_VERSION 不动。详见
# docs/superpowers/specs/2026-10-11-net-confidence-design.md。
```

- [ ] **Step 4: 跑绿 + 无残留**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_goal_decider.py tests/test_pipeline.py -q`
Expected: PASS（test_pipeline 若有因 R1/P1d 删除而翻红的行，逐行核对是行为预期（review↔high 迁移）后改断言并注明 v18；不允许为绿而绿）。

Run: `grep -rn "R1_PROMOTE\|P1_DEMOTE" bbh/ tests/ tools/`
Expected: 零命中。

- [ ] **Step 5: Commit**

```bash
git add bbh/models.py bbh/goal_decider.py bbh/pipeline.py tests/test_goal_decider.py tests/test_pipeline.py
git commit -m "feat(decider): v18 档位阈值化——删 R1/P1d 旁路，硬门挂 review_reason 标记"
```

---

### Task 3: net_motion v18——net_score + 全行测网 + conf 回写 + 非硬门行重分箱

**Files:**
- Modify: `bbh/net_motion.py:44-57`（网分常量）、`:240-278`（net_evidence_pass 重写）、模块 docstring；`bbh/pipeline.py:335-360,672-681`（活注释口径）
- Test: `tests/test_net_motion.py`

**Interfaces:**
- Consumes: Task 2 的 `review_reason`、`bbh.goal_decider.HIGH_CONF/MIN_CONF`。
- Produces: `net_motion.net_score(post, local) -> float`（Task 4/5 import）、`NET_SCORE_STATIC=-0.20`、`NET_SCORE_GLOBAL=-0.10`、`NET_SCORE_SWING=0.0`。`net_evidence_pass` 签名不变。

- [ ] **Step 1: 改写/新增用例（先红）**

`tests/test_net_motion.py`：

(a) `test_全局闪烁伪装网动_局部性门降级`（L46-52）改名为 `test_全局闪烁伪装网动_非局部扣010后仍high`，断言改：

```python
    assert out[0].net_post >= NET_STATIC_TH      # 网区帧差确实被抬亮
    assert out[0].net_local is False             # 但同帧对照区等亮=非局部
    assert out[0].confidence == 0.70             # 0.80 − 0.10（网分）
    assert out[0].status == "high"               # v18：−0.10 后仍 ≥0.70 → 不再一票降档（非局部=证据被污染≠网没动；八场定标盯假绿面）
```

(b) `test_网静止的high降review_网摆的保持high并记录信号`（L55-68）整体替换：

```python
def test_全行测网_网分回写_非硬门行重分箱(sample_video, sample_calibration,
                                        tmp_path):
    # v18：静止 −0.20 → 几何 high 0.80 落 0.60 review；几何 review 0.60
    # 落 0.40 low（review→low 迁移是八场报告要盯的口径移动）；网摆局部
    # 分 0，conf/status 原样。测量对象=全部行（不再只测 high）。
    evs = [GoalEvent(t=STILL_T, confidence=0.80, status="high"),
           GoalEvent(t=CROSS_T, confidence=0.80, status="high"),
           GoalEvent(t=STILL_T, confidence=0.60, status="review")]
    out = net_evidence_pass(sample_video, str(tmp_path), sample_calibration,
                            [], evs)
    assert out[0].status == "review" and out[0].confidence == 0.60
    assert out[0].net_post < NET_STATIC_TH
    assert out[0].net_ctrl is not None         # 对照区同步记录（诊断）
    assert out[1].status == "high" and out[1].confidence == 0.80
    assert out[1].net_post > NET_STATIC_TH
    assert out[2].status == "low" and out[2].confidence == 0.40
    assert os.path.exists(os.path.join(str(tmp_path), NET_FILE))
```

(c) `test_测量失败fail_open不降级不写sidecar` 加一行：`assert out[0].confidence == 0.80`。

(d) 文件末尾新增三用例：

```python
def test_网分round边界_070减020恰落050review(sample_video, sample_calibration,
                                            tmp_path):
    # 0.70−0.20 浮点是 0.4999…：不 round 就分箱会掉 low——「静止恰把 high
    # 压到 review（而非 low）」是对齐现行网门行为的锚点，必须钉死
    evs = [GoalEvent(t=STILL_T, confidence=0.70, status="high")]
    out = net_evidence_pass(sample_video, str(tmp_path), sample_calibration,
                            [], evs)
    assert out[0].confidence == 0.50
    assert out[0].status == "review"           # 0.50 ≥ MIN_CONF


def test_硬门行测网只记录_档位不动分值照扣(sample_video, sample_calibration,
                                          tmp_path):
    # 用户裁定「所有球都走篮网判断，不然分值不准」：硬门行 conf 也要完整
    # （含网分）；但档位由硬门裁决，阈值分箱不碰（spec §3.3）
    evs = [GoalEvent(t=STILL_T, confidence=0.80, status="low",
                     low_reason="证据点太少（置信度不足 0.50）"),
           GoalEvent(t=0.7, confidence=0.80, status="review",
                     review_reason="entity"),
           GoalEvent(t=0.9, confidence=0.80, status="review", pop_back=0.42)]
    out = net_evidence_pass(sample_video, str(tmp_path), sample_calibration,
                            [], evs)
    assert [e.status for e in out] == ["low", "review", "review"]
    assert [e.confidence for e in out] == [0.60, 0.60, 0.60]
    assert all(e.net_post is not None and e.net_post < NET_STATIC_TH
               for e in out)


def test_网分常量与打分函数的取值表():
    from bbh.net_motion import (NET_SCORE_GLOBAL, NET_SCORE_STATIC,
                                NET_SCORE_SWING, net_score)
    assert (NET_SCORE_STATIC, NET_SCORE_GLOBAL, NET_SCORE_SWING) \
        == (-0.20, -0.10, 0.0)
    assert net_score(2.0, False) == -0.20      # 静止（local 值无关）
    assert net_score(8.0, False) == -0.10      # 亮但非局部
    assert net_score(8.0, True) == 0.0         # 网动且局部：不许给正分（铁结论）
    assert net_score(None, None) == 0.0        # fail-open：证据缺失不当否证
```

- [ ] **Step 2: 跑红**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_net_motion.py -q`
Expected: FAIL（`net_score` 不存在 / 断言不等）。

- [ ] **Step 3: 实现**

`bbh/net_motion.py`：

① import 行（L40）改：`from .goal_decider import HIGH_CONF, MIN_CONF, RIM_Y_RATIO`

② 常量区 `NET_SIGNAL_VER = 2` 之前插入：

```python
# v18 网分（spec §3.1）：网信号对 conf 的加减分项。取值对齐「首轮重放
# 档位迁移 ≈ 现行网门行为」：静止 −0.20 使几何 high 0.70 恰落 0.50 review。
# 网动且局部 = 0（铁结论：打铁弹出蹭网/砸筐沿传振/贴网外侧扫落同样让网
# 强烈摆动——「网动不升级」是五轮取证换来的，不得顺手对称化）；测量缺失
# = 0（fail-open）。八场定标后可再调（spec §3.6）。
NET_SCORE_STATIC = -0.20   # 网静止（post < NET_STATIC_TH）
NET_SCORE_GLOBAL = -0.10   # 网亮但非局部（全局抖动/编码噪声伪装）
NET_SCORE_SWING = 0.0      # 网动且局部
```

③ `net_evidence_pass` 前新增两个函数：

```python
def net_score(post, local) -> float:
    """(net_post, net_local) → 网分。None（未测/测量失败）= 0：证据缺失
    不当否证（fail-open，与 v12 起的降级口径同源）。"""
    if post is None:
        return 0.0
    if post < NET_STATIC_TH:
        return NET_SCORE_STATIC
    if local is False:
        return NET_SCORE_GLOBAL
    return NET_SCORE_SWING
```

④ `net_evidence_pass`（L240-278）整体替换：

```python
def net_evidence_pass(video_path: str, work_dir: str, calib: Calibration,
                      shift_samples: list, events: list[GoalEvent],
                      stage_cb=None) -> list[GoalEvent]:
    """v18：判定器输出 → 全行测网 + 网分回写 conf + 非硬门行重分箱（原地
    改，返回同一列表）。

    网证据不再直接改档（v12/v14 的两条降级语句删除）：conf += net_score
    （静止 −0.20 / 非局部 −0.10 / 网动或未测 0），round 2 位再比较；档位
    = 硬门档 if 硬门 else 阈值分箱(conf)（HIGH_CONF/MIN_CONF）。测量对象
    从 high 扩到**全部行**（用户裁定「所有球都需要走篮网的判断，不然置信
    度分值就会不准」）；硬门行（low_reason / review_reason / pop_back）
    测网只记录、conf 照扣（分值完整性），档位不动。fail-open：测不到 →
    net_* 留 None、网分 0、档位不动。

    幂等前提：输入行 conf 为几何分（decide_goals 刚产出），本 pass 在每行
    上恰好执行一次（管线 CR 二趟只对 fresh 行再跑）。全部命中 sidecar 缓存
    时零视频 IO（判定器秒级重放路径保持秒级）；NET_SIGNAL_VER 不动，旧
    sidecar 里 high 行的测量原样吃回，增量只测未覆盖行。"""
    if not events:
        return events
    cache = _load_cache(work_dir, video_path, calib)
    missing = sorted({_k(e.t) for e in events} - set(cache))
    if missing:
        if stage_cb:
            stage_cb(f"正在测量篮网运动信号（{len(missing)} 个事件行，"
                     f"约 {max(1, round(len(missing) * 0.7))} 分钟）…")
        measured = measure_net_signals(video_path, calib, shift_samples,
                                       missing, work_dir)
        if measured:
            cache.update(measured)
            _save_cache(work_dir, video_path, calib, cache)
        if stage_cb:
            stage_cb(None)
    for e in events:
        sig = cache.get(_k(e.t))
        if not sig:
            continue          # 测量失败 → fail-open：证据缺失不当否证
        e.net_post = sig["post"]
        e.net_ctrl = sig.get("ctrl")
        e.net_local = sig.get("local")
        e.confidence = round(e.confidence
                             + net_score(sig["post"], sig.get("local")), 2)
        if e.low_reason is not None:
            continue                      # low 硬门：物理否证，网信号救不回
        if e.review_reason is not None or e.pop_back is not None:
            continue                      # 复核硬门：不被网分拉高或压低
        e.status = ("high" if e.confidence >= HIGH_CONF
                    else "review" if e.confidence >= MIN_CONF else "low")
    return events
```

⑤ 模块 docstring 末尾补一段（v12/v14 的历史叙述不动）：

```python
v18（2026-10-11 问题②）：网证据从「直接改 status」改为「conf 加减分项」
（net_score），测量对象扩到全部事件行，非硬门行按 HIGH_CONF/MIN_CONF 对
含网分的 conf 重分箱——「只测 high」的两条降级语句自此删除。硬门行测网
只记录不改档。详见 docs/superpowers/specs/2026-10-11-net-confidence-design.md。
```

`bbh/pipeline.py` 活注释（不动 v14 历史版本块）：
- L673 `# v14：网门降级集对照快照` → `# v14：网分降档集对照快照（v18 语义：几何 high 被网分压离 high 的行）`
- `_cooldown_release_pass` docstring 首段「网门降级(high→review)的行」→「网分降档(high→非 high)的行」；尾段「新行过同款网门」→「新行过同款网证据 pass（测网+网分+重分箱）」。

- [ ] **Step 4: 跑绿（含冷却释放回归）**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_net_motion.py tests/test_pipeline.py tests/test_net_clusters.py -q`
Expected: PASS。冷却释放用例（test_pipeline.py:502 起）monkeypatch 了 net_evidence_pass 桩——桩只改 status 不动 conf，在 v18 语义下仍等价于「pass 把一行压离 high」，断言应原样绿；若红，核对桩与真实 pass 的差异后修桩（加 `e.confidence = round(e.confidence - 0.20, 2)` 之类），并在用例注释里写明 v18 口径。

- [ ] **Step 5: Commit**

```bash
git add bbh/net_motion.py bbh/pipeline.py tests/test_net_motion.py tests/test_pipeline.py
git commit -m "feat(net_motion): v18 全行测网+网分回写+非硬门行重分箱"
```

---

### Task 4: net_clusters——注入行挂 `"b2"` 硬门 + 补 net_* 实测（仅显示）

**Files:**
- Modify: `bbh/net_clusters.py:41-46`（import）、`:254-273`（_build_event）、`:276-319`（net_cluster_pass 尾部）；`bbh/net_motion.py`（新增 attach_net_display）
- Test: `tests/test_net_clusters.py`

**Interfaces:**
- Consumes: Task 3 的 `net_score`（不用于计分，仅同源常量）、Task 2 的 `review_reason`。
- Produces: `net_motion.attach_net_display(video_path, work_dir, calib, shift_samples, rows, stage_cb=None) -> None`（只填 net_* 三字段，不动 conf/status）。B2 注入行：`review_reason="b2"`、`net_*` 尽量实测（fail-open 留 None）、conf/status 恒 `review`。

- [ ] **Step 1: 用例（先红）**

`tests/test_net_clusters.py`：

(a) `test_注入review行追加表尾`（L157 附近）`assert row.cross_off is not None` 之后加：

```python
    assert row.review_reason == "b2"         # v18：注入行自带硬门（恒 review）
```

(b) 文件末尾（管线 E2E 之前）新增：

```python
def test_注入行补net实测仅显示_分值档位不动(tmp_path, monkeypatch,
                                          sample_video):
    # v18 spec §3.5：注入行 net_* 落实测值（UI 展示），但网分按 0——簇
    # 触发判据本身就是同款局部性网动，实测窗（t+0.45~0.95s）可能错过摆动
    # 相位，测出「静止」再扣 −0.20 是对同一证据的双重惩罚
    _prepare(tmp_path, sample_video, [{"t0": 10.0, "t1": 10.6, "peak": 9.0,
                                       "npairs": 3}])
    monkeypatch.setattr(nc, "scan_net_clusters", _boom)
    monkeypatch.setattr("bbh.net_motion.measure_net_signals",
                        lambda *_a, **_k: {"10.00": {"post": 2.1,
                                                     "ctrl": 2.0,
                                                     "local": False}})
    out = nc.net_cluster_pass(sample_video, str(tmp_path), [(0.0, CAL)], {},
                              CHAIN_PTS, [_ev(50.0)])
    row = out[1]
    assert row.net_post == 2.1 and row.net_ctrl == 2.0
    assert row.net_local is False
    assert row.confidence == 0.70 and row.status == "review"   # 不扣不加
    # 实测值落 sidecar：重放路径吃缓存不再重测
    doc = json.loads((tmp_path / "net_signals.json").read_text(encoding="utf-8"))
    assert any(e["identity"]["hoop"] == [300, 140, 80, 40] for e in doc["entries"])
```

（注：注入行锚=链首 t=10.0（CHAIN_PTS 无上方点），故测量键是 `"10.00"`。）

- [ ] **Step 2: 跑红**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_net_clusters.py -q`
Expected: FAIL（`review_reason` None / net_post None）。

- [ ] **Step 3: 实现**

`bbh/net_motion.py`——`net_score` 之后新增：

```python
def attach_net_display(video_path: str, work_dir: str, calib: Calibration,
                       shift_samples: list, rows: list[GoalEvent],
                       stage_cb=None) -> None:
    """给 B2 注入行补 net_* 实测值（仅显示，不改 conf/status——spec §3.5：
    注入行网分按 0，簇触发判据本身就是同款局部性网动，实测窗可能错过摆动
    相位，测出「静止」再扣分是对同一证据的双重惩罚）。缓存/测量/sidecar
    与 net_evidence_pass 同一套（身份相同，重放共享）。fail-open：测不到
    → 字段留 None。"""
    if not rows:
        return
    cache = _load_cache(work_dir, video_path, calib)
    missing = sorted({_k(e.t) for e in rows} - set(cache))
    if missing:
        if stage_cb:
            stage_cb(f"正在测量篮网运动信号（{len(missing)} 个事件行，"
                     f"约 {max(1, round(len(missing) * 0.7))} 分钟）…")
        measured = measure_net_signals(video_path, calib, shift_samples,
                                       missing, work_dir)
        if measured:
            cache.update(measured)
            _save_cache(work_dir, video_path, calib, cache)
        if stage_cb:
            stage_cb(None)
    for e in rows:
        sig = cache.get(_k(e.t))
        if not sig:
            continue
        e.net_post = sig["post"]
        e.net_ctrl = sig.get("ctrl")
        e.net_local = sig.get("local")
```

`bbh/net_clusters.py`：

① import（L45-46）`from .net_motion import (...)` 列表加 `attach_net_display`。

② `_build_event` 的 GoalEvent 构造改（conf 算式不动）：

```python
    return GoalEvent(
        t=t_new, confidence=conf, status="review",
        review_reason="b2",   # v18：注入行自带硬门——网分重分箱不适用（结构上
                              # 也不经过 net_evidence_pass），恒 review（宁多勿漏）
        cross_off=cross_off,
        above_n=0, below_n=len(chain),
        above_span=0.0, below_span=below_span,
        descent_px=round(descent, 1), below_big=big)
```

③ `net_cluster_pass` 尾部（L317-319）替换：

```python
    if not injected:
        return events
    injected = sorted(injected, key=lambda e: e.t)
    # v18：注入行补 net_* 实测值（仅 UI 展示；网分按 0、档位恒 review）。
    # 多段标定按行时刻就近归段，各用各的筐几何与 shift（同簇扫描口径）。
    by_seg: dict = {}
    for e in injected:
        t0 = max((s for s in seg_t0s if s <= e.t + 1e-9), default=seg_t0s[0])
        by_seg.setdefault(t0, []).append(e)
    for t0, grp in by_seg.items():
        attach_net_display(video_path, work_dir, calibs[t0],
                           shifts.get(t0, []), grp, stage_cb=stage_cb)
    return events + injected
```

模块 docstring「注入行 status="review"、above_n=0 …」一句补「review_reason="b2"（v18 硬门标记）、net_* 落实测值仅供展示（网分按 0）」。

- [ ] **Step 4: 跑绿**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_net_clusters.py tests/test_pipeline.py -q`
Expected: PASS（管线 E2E 用 sample_video：注入行 0 个 → attach 是 no-op；真实事件行 net 由 Task 3 全行测量覆盖）。

- [ ] **Step 5: Commit**

```bash
git add bbh/net_clusters.py bbh/net_motion.py tests/test_net_clusters.py
git commit -m "feat(net_clusters): v18 注入行挂 b2 硬门+补 net 实测（仅显示）"
```

---

### Task 5: UI 依据链——算式加网分项、warn 换 fail-open 文案、legacy 对账标注

**Files:**
- Modify: `bbh/web/api_session.py:20,108-119`（import + terms）、`bbh/app.py:1072-1086`（_reason_text）、`frontend/src/edit/BasisPanel.vue:88-110`、`tests/e2e/test_edit_bay_e2e.py:51-69`（夹具）、`tests/e2e/edit_bay.spec.mjs:406,411-426`（断言）
- Rebuild: `bbh/static/`（npm run build 产物）

**Interfaces:**
- Consumes: Task 3 的 `net_score`。
- Produces: `terms` payload 多两键：`"net": float`（网分）与 `"legacy": bool`（和式≠conf，即 ≤v17 旧档）。前端公式行多一项 `＋ 网 x.xx`。

- [ ] **Step 1: 夹具与 e2e 断言先改（红）**

`tests/e2e/test_edit_bay_e2e.py`——夹具 `_ev`（L61-69）与 docstring ⚠ 段（L51-55）替换：

```python
    ⚠ `net_*` 按 v18 全行测网给值（问题②）；唯独 i=1（🟡）留 None——
    A-3 要钉的说明块只在 net 为空（fail-open）时渲染。
    """
```

```python
    def _ev(i):
        st = status[i % 3]
        measured = i != 1                     # i=1 留 None 钉 fail-open 文案
        return GoalEvent(t=1.2 + i * 0.4, confidence=0.90 - i * 0.01, status=st,
                         cross_off=0.02, above_n=3, above_span=0.6, below_n=2,
                         below_span=0.4, descent_px=18.0, below_big=2,
                         net_post=12.5 if measured else None,
                         net_ctrl=3.1 if measured else None,
                         net_local=True if measured else None)
```

`tests/e2e/edit_bay.spec.mjs`——两处：

(a) A-1 算式断言（L406）替换为：

```js
  ok(pop.includes(`置信度 ${cur.conf.toFixed(2)} ＝ 基础`) && pop.includes("＋ 网"),
     "置信度算式自洽且含网分项（算式用服务端 terms，前端不自拼）");
```

(b) A-3 段注释（L411-415）与两断言（L424-426）替换：

```js
  // A-3：v18 全行测网后，net_* 为 None 只剩一种正常形态——测量失败
  // （fail-open：视频打不开/seek 出界），证据缺失不当否证、网分按 0。
  // 夹具里唯独第 2 颗（🟡，i=1）net 为 None（见 test_edit_bay_e2e.py::_ev）
  // ——那条就是本段要断的行。
```

```js
  ok(warn.includes("fail-open") && warn.includes("不当否证"),
     `🟡 行说明为 fail-open 文案（实际「${warn}」）`);
  ok(!warn.includes("只测 high"), "不再写「只测 high」（v18 全行测网后已失效）");
```

（ok 调用数不变 ⇒ 项数门不动。）

- [ ] **Step 2: 服务端 payload**

`bbh/web/api_session.py`——import 行（L20）后加：

```python
from ..net_motion import net_score
```

terms 装配（L114-119）替换为：

```python
            "terms": (_terms(e) if has_ev else None),
```

模块内（router 定义之前）加：

```python
def _terms(e) -> dict:
    """依据卡算式分项（v18 含网分）：conf = base + up + down + pos + net，
    与 goal_decider/net_motion 同名项逐字同源。旧版 events.json（≤v17）的
    conf 不含网分（和式≠conf）→ legacy=True，前端标注「旧版结果」——
    别让用户对着 0.75 ≠ 0.3+0.2+0.2+0.05−0.20 以为算错了。span 按 3 位
    落档重算，刀锋边界可能偶发 legacy=True，无害（只是多一句标注）。"""
    up = _side_score(e.above_n or 0, e.above_span or 0.0)
    down = _side_score(e.below_n or 0, e.below_span or 0.0)
    pos = _pos_score(abs(e.cross_off))
    net = net_score(e.net_post, e.net_local)
    return {"base": 0.30, "up": up, "down": down, "pos": pos, "net": net,
            "legacy": abs(0.30 + up + down + pos + net - e.confidence) > 0.005}
```

- [ ] **Step 3: 旧工作台同款（改一处要改两处）**

`bbh/app.py`——顶部 import 区（与其他 bbh 模块 import 并列处）加 `from .net_motion import net_score`；`_reason_text` 的降级 ⚠ 两段（L1072-1078）与算式段（L1081-1086）替换：

```python
            parts.append(f"⚠️ 篮网未摆动（信号 {e.net_post:.1f}，对照 "
                         f"{e.net_ctrl:.1f}）——球大概率从筐前/筐后飞过或"
                         "未触网（网分 −0.20）")
        elif e.net_local is False:
            parts.append(f"⚠️ 网区信号 {e.net_post:.1f} 与对照区 "
                         f"{e.net_ctrl:.1f} 同步等亮——是画面整体抖动/噪点"
                         "伪装的「网动」，非局部网摆（网分 −0.10）")
```

```python
    up = _side_score(e.above_n, e.above_span)
    down = _side_score(e.below_n, e.below_span)
    pos = _pos_score(abs(e.cross_off))
    net = net_score(e.net_post, e.net_local)
    parts.append(f"置信度 {e.confidence:.2f} ＝ 基础 0.30 ＋ 上方证据 {up:.2f} "
                 f"＋ 下方证据 {down:.2f} ＋ 位置 {pos:.2f} ＋ 网 {net:.2f}"
                 "（≥0.70 高置信、0.50~0.69 待确认、更低或被否决门槛拦截=低置信；"
                 "网：静止 −0.20 · 非局部 −0.10 · 网动/未测 0.00）")
```

（L1079-1080 的「各分项由已存的依据字段重算」注释保留，补一句「网分项 v18 起由 net_score 同源计算」。）

- [ ] **Step 4: BasisPanel.vue**

`frontend/src/edit/BasisPanel.vue`——算式/档位行（L87-92）替换：

```html
          <template v-if="ev && terms">
            <div>置信度 <b>{{ row.conf.toFixed(2) }}</b> ＝ 基础 {{ num(terms.base) }}
              ＋ 上方证据 {{ num(terms.up) }} ＋ 下方证据 {{ num(terms.down) }}
              ＋ 位置 {{ num(terms.pos) }} ＋ 网 {{ num(terms.net) }}</div>
            <div class="dim">档位：≥0.70 🟢高置信 · 0.50~0.69 🟡待确认 · 更低或被否决门槛拦截 = ⚪低置信（低置信不进集锦）；网：静止 −0.20 · 非局部 −0.10 · 网动/未测 0.00</div>
            <div v-if="terms.legacy" class="dim">（旧版结果的置信度未含网分项——算式对不上账是正常的，重跑检测后一致）</div>
          </template>
```

net 说明行（L97）替换：

```html
            <div class="dim">网区 ≥5.0 且同对局部比 ≥3× 才算真网摆；网动不加分（擦网/砸筐传振同样让网动）</div>
```

注释块 + warn（L106-110）替换：

```html
          <!-- v18 全行测网后 net_* 为 None 只剩 fail-open 一种形态（测量
               失败：视频打不开/seek 出界）——证据缺失不当否证，网分按 0、
               档位不受影响。 -->
          <div v-if="!net" class="warn">net_* 为 None：网信号测量失败（fail-open）——证据缺失不当否证，网分按 0.00 计、档位不受影响。</div>
```

- [ ] **Step 5: 重建前端 + 跑 e2e**

Run: `cd frontend && npm run build`（产物进 `bbh/static/`，哈希文件名变更属正常）
Run: `cd .. && ./.venv/Scripts/python.exe -X utf8 -m pytest tests/e2e/test_edit_bay_e2e.py -x -q`
Expected: PASS（A-1 含网分项、A-3 fail-open 文案）。若红，先看服务端 terms 是否带 net/legacy（`curl` 或读夹具日志），再查 Vue。

- [ ] **Step 6: Commit**

```bash
git add bbh/web/api_session.py bbh/app.py frontend/src/edit/BasisPanel.vue bbh/static tests/e2e/test_edit_bay_e2e.py tests/e2e/edit_bay.spec.mjs
git commit -m "feat(web): 依据卡算式加网分项+fail-open 文案+旧档 legacy 标注（v18）"
```

---

### Task 6: 全量回归 + v18 八场重放对比 + 阈值扫描 + 交付报告（PAUSE：用户裁定）

**Files:**
- Create: `%TEMP%` 重放脚本、`诊断/2026-10-11-网分v18/v18/<场>.events.json` ×N、`诊断/2026-10-11-网分v18/报告.md`

**Interfaces:**
- Consumes: Task 1 的 baseline（`诊断/2026-10-11-网分v18/baseline/`）、Task 2-5 的 v18 代码、`tools/evaluate.py`（match_report/auto_metrics/regression_report）、`truth/*.json`。
- Produces: 指标表（v17 vs v18）、档位迁移表、(HIGH_CONF, MIN_CONF) 扫描表、review→low 抽样 ≤20 行看片清单。**本任务止于报告——阈值/常量是否调整由用户裁定后另行小任务。**

- [ ] **Step 1: 全量 pytest（单独跑）**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q`
Expected: 0 failed（0.7 万秒内；基线 737 passed + 本计划新增 ~6 用例；skipped 1 沿袭）。

- [ ] **Step 2: 写重放/扫描脚本**

写到 `%TEMP%\v18_replay.py`（逐字；两处 `TODO` 不允许——已写实）：

```python
# -*- coding: utf-8 -*-
"""v18 重放验收：Temp 副本跑 v18 全管线，对照 Task 1 的 v17 基线产出
迁移表/指标/阈值扫描/review→low 清单。不碰用户 work/。"""
import json, os, shutil, sys, tempfile, time
from collections import Counter

REPO = r"D:\claude code\视频剪辑"
DIAG = os.path.join(REPO, "诊断", "2026-10-11-网分v18")
FIELDS = ["2026-08-21_1", "2026-08-22_1", "2026-08-28_1", "2026-08-29_3",
          "2026-09-04_1", "2026-09-11_3", "2026-09-12_1", "2026-09-19_1",
          "2026-10-10_1"]
COPY = ["positions.json", "net_signals.json", "net_clusters.json", "deleted.json"]

sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tools"))
from bbh import calibration          # noqa: E402
from bbh.pipeline import run_detection  # noqa: E402
import evaluate                       # noqa: E402  （match_report/auto_metrics/regression_report）

os.makedirs(os.path.join(DIAG, "v18"), exist_ok=True)
runs = {}
for f in FIELDS:
    base = os.path.join(DIAG, "baseline", f"{f}.events.json")
    src_ev = os.path.join(REPO, "work", f, "events.json")
    if not os.path.exists(base) or not os.path.exists(src_ev):
        print(f"[skip] {f}: 无基线或无存档"); continue
    video = json.load(open(src_ev, encoding="utf-8"))["video"]
    root = tempfile.mkdtemp(prefix="v18r_")
    wd = os.path.join(root, "work", f)
    os.makedirs(wd)
    for name in COPY:
        p = os.path.join(src, name)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(wd, name))
    for name in os.listdir(src):
        if name.startswith("calibration"):
            shutil.copy2(os.path.join(src, name), os.path.join(wd, name))
    t0 = time.time()
    segs = calibration.load_segments_dir(wd)
    path, warn = run_detection(video, segs, wd)
    new = json.load(open(path, encoding="utf-8"))
    shutil.copy2(path, os.path.join(DIAG, "v18", f"{f}.events.json"))
    runs[f] = (json.load(open(base, encoding="utf-8")), new)
    print(f"[done] {f}: {time.time()-t0:.0f}s rows={len(new['events'])}", flush=True)

def rows(doc):
    return {round(e["t"], 2): e for e in doc["events"]}

mig, r2l, appear, vanish = Counter(), [], [], []
for f, (old, new) in runs.items():
    ro, rn = rows(old), rows(new)
    for t, e in rn.items():
        o = ro.get(t)
        if o is None:
            appear.append((f, t, e["status"])); continue
        mig[(o["status"], e["status"])] += 1
        if o["status"] == "review" and e["status"] == "low":
            r2l.append((f, t, o["confidence"], e["confidence"],
                        e.get("net_post"), e.get("net_local")))
    for t in ro:
        if t not in rn:
            vanish.append((f, t))
print("\n== 档位迁移表 (old→new) =="); [print(f"  {k}: {v}") for k, v in sorted(mig.items())]
print("新增行:", len(appear), " 消失行:", len(vanish))
if vanish: print("  消失明细（预期=环境受限场的 B2 注入行）:", vanish[:20])

def _det(doc, field):
    """检测行 → (t, status)，剔除「比赛间隙真进球」（evaluate.INTERMISSION_GOALS
    同款口径：既不算命中也不算假绿——不滤会把 09-12_1 的 3 条灌进假绿、
    与八场历史总账 106 的口径对不上）。"""
    return [(e["t"], e["status"]) for e in doc["events"]
            if not any(abs(e["t"] - g) <= evaluate.TOLERANCE
                       for g in evaluate.INTERMISSION_GOALS.get(field, []))]

print("\n== 指标（truth 完整场；08-21_1 完整性=false 只做回归） ==")
for f, (old, new) in runs.items():
    tp = os.path.join(REPO, "truth", f"{f}.json")
    if not os.path.exists(tp): print(f"  {f}: 无 truth（只进迁移表）"); continue
    truth_doc = json.load(open(tp, encoding="utf-8"))
    det_o = _det(old, f)
    det_n = _det(new, f)
    if truth_doc.get("complete") and truth_doc.get("goals") is not None:
        g = truth_doc["goals"]
        ro_ = evaluate.match_report(g, det_o); rn_ = evaluate.match_report(g, det_n)
        ho = sum(1 for _, s in det_o if s == "high")
        hn = sum(1 for _, s in det_n if s == "high")
        print(f"  {f}: v17 命中{ro_['命中']}/假绿{evaluate.auto_metrics(ro_, ho)['假绿']}"
              f" → v18 命中{rn_['命中']}/假绿{evaluate.auto_metrics(rn_, hn)['假绿']}"
              f"  high {ho}→{hn}  review→low 见迁移表")
    else:
        conf = truth_doc.get("confirmed_goals") or []
        print(f"  {f}: 回归 v17={evaluate.regression_report(conf, det_o)}"
              f" v18={evaluate.regression_report(conf, det_n)}")

print("\n== 阈值扫描（非硬门行按 (HIGH, MIN) 重分箱；硬门/B2 行档位恒定） ==")
def status_at(e, hi, lo):
    if e.get("low_reason") is not None: return "low"
    if e.get("review_reason") is not None or e.get("pop_back") is not None:
        return "review"
    c = e["confidence"]
    return "high" if c >= hi else ("review" if c >= lo else "low")
print("  HIGH\\MIN  " + "  ".join(f"{m:.2f}" for m in (0.40, 0.45, 0.50)))
for hi in (0.60, 0.65, 0.70, 0.75):
    line = []
    for lo in (0.40, 0.45, 0.50):
        tot = Counter(hn=0, fp=0, hit=0)
        for f, (_, new) in runs.items():
            tp = os.path.join(REPO, "truth", f"{f}.json")
            if not os.path.exists(tp): continue
            td = json.load(open(tp, encoding="utf-8"))
            if not (td.get("complete") and td.get("goals") is not None): continue
            det = [(t, s) for t, s in
                   ((e["t"], status_at(e, hi, lo)) for e in new["events"])
                   if not any(abs(t - g) <= evaluate.TOLERANCE
                              for g in evaluate.INTERMISSION_GOALS.get(f, []))]
            rep = evaluate.match_report(td["goals"], det)
            h = sum(1 for _, s in det if s == "high")
            m = evaluate.auto_metrics(rep, h)
            tot["hn"] += h; tot["fp"] += m["假绿"]; tot["hit"] += m["自动档命中"]
        line.append(f"h{tot['hn']}/fp{tot['fp']}")
    print(f"  {hi:.2f}      " + "  ".join(line))
print("\n== review→low 抽样（前 20 行，看片清单；已按场聚集） ==")
for r in r2l[:20]: print("  ", r)
json.dump({"migration": {f"{k[0]}->{k[1]}": v for k, v in mig.items()},
           "review_to_low": r2l, "appear": appear, "vanish": vanish},
          open(os.path.join(DIAG, "migration.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("\n→", DIAG)
```

- [ ] **Step 3: 跑重放（长任务）**

Run: `./.venv/Scripts/python.exe -X utf8 %TEMP%/v18_replay.py`
Expected: 每场 1~20 分钟（v18 全行测网对旧 sidecar 只有 high 行缓存 → 非 high 行全部增量实测 ~0.7s/行；无 net_clusters.json 的场次另有全片簇扫描数分钟）。**不并发别的视频任务。** 抽查一场与 `python tools/evaluate.py 诊断/2026-10-11-网分v18/v18/<场>.events.json truth/<场>.json` 的 CLI 输出对数（harness 口径=evaluate 口径的自证）。

- [ ] **Step 4: 汇总报告并停**

把 Step 3 全部输出整理进 `诊断/2026-10-11-网分v18/报告.md`，必含：①指标表（假绿口径注意 08-21_1 不入总账）；②迁移表（重点 review→low 条数——假绿计数下降是口径移动不是精度提升，spec §3.7）；③阈值扫描表 + 「0.7/0.5 是否仍在帕累托前沿」的一句话结论；④review→low 看片清单。**呈报用户裁定：阈值动否 / NET_SCORE_GLOBAL 动否 / 看片终裁。任何常量调整=新小任务（改常量+受影响断言+重跑扫描），不在本计划内顺手做。**

- [ ] **Step 5: Commit（报告与产物）**

```bash
git add "诊断/2026-10-11-网分v18"
git commit -m "test(v18): 八场重放迁移表+阈值扫描+看片清单（网分定标报告）"
```

（`诊断/` 若已被 .gitignore 忽略则产物留盘不入库，报告正文以聊天呈报为准。）

---

### Task 7: 文档收口

**Files:**
- Modify: `README.md`（版本史）、`docs/superpowers/specs/2026-10-11-net-confidence-design.md:3`（状态行）

**Interfaces:**
- Consumes: Task 6 的报告结论。
- Produces: 版本史 v17/v18 两条 + spec 状态「已实现」。

- [ ] **Step 1: README 版本史加两条**（紧挨 v16 条目同款格式；v17 条目内容=问题①修复摘要，v18 条目内容=Task 2/3/4 摘要 + 八场定标结论一句话）

- [ ] **Step 2: spec 状态行改**：`**日期**：2026-10-11　**状态**：设计稿（待用户过目后才可实现）` → `**状态**：已实现（LOGIC_VERSION 18，2026-10-11）`，并在文末附一行指向 `诊断/2026-10-11-网分v18/报告.md`。

- [ ] **Step 3: 全量绿后提交**

Run: `./.venv/Scripts/python.exe -X utf8 -m pytest -q` → 0 failed。

```bash
git add README.md docs/superpowers/specs/2026-10-11-net-confidence-design.md
git commit -m "docs: 版本史 v17/v18 + spec 状态已实现"
```

---

## 自审记录（写计划时已核）

- **spec 覆盖**：§3.1 网分表→Task 3 常量+取值表用例；§3.2 全行测量→Task 3 重写（stage 文案/成本注释）；§3.3 优先级→Task 2 review_reason + Task 3 分箱段；§3.4 R1/P1d→Task 2；§3.5 B2→Task 4；§3.6 阈值→Task 6 扫描+裁定；§3.7 UI→Task 5 + Task 6 报告口径；§4 验收→LOGIC_VERSION/八场/pytest 全落实。**无缺口。**
- **两个取证新发现已入计划**：v11 兜底「强制不升绿」原是直接改 status（goal_decider.py:181-182）——不转 `review_reason="fallback"` 会被网分重分箱推回 high（Task 2③）；v10 R1 曾越过 entity 硬门升绿（L407 条件在硬门赋值之后）——v18 顺带把硬门优先级写死（Task 2②注释）。
- **类型一致性**：`review_reason` 值域五值在 Task 2 定义、Task 3（net pass 判 `is not None`）与 Task 4（`"b2"`）消费；`net_score`/`attach_net_display` 签名 Task 3/4 定义、Task 5 import 消费；`terms.net/legacy` Task 5 服务端定义、同任务前端消费。
- **占位符扫描**：Task 6 报告内容以「必含四件」列明但数字来自运行产物（研究报告的固有形态，非代码占位）；其余步骤代码全部实写。Task 1 脚本 FIELDS 第 4 场已更正为 `2026-08-29_3`。
