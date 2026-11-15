# 继承清单：`D:\claude code\yolo` → 本项目

- 日期：2026-10-11
- 目的：判断 `D:\claude code\yolo`（YOLO 检球验证任务）里哪些内容值得继承进
  `D:\claude code\视频剪辑YOLO`（已 fork 的 YOLO 版产线），以便整理、瘦身那个文件夹。
- 结论先行：**值钱的是「数据 + 脚本 + 结论报告」三样，不值钱的是「可再生的中间产物」；
  占 6.5G 的 `raw_frames/` `eval_out/` 基本可弃，真正该保的是不到 200MB 的核心资产。**

---

## 0 体积总览（约 8 GB）

| 目录 / 文件 | 体积 | 性质 | 处置建议 |
|---|---|---|---|
| `dataset/` | 2.6 G | 6238 帧标注集（images+labels，ball 5545 / hoop 6158 框） | **保**（labels 必保；images 可再生） |
| `raw_frames/` | 2.6 G | 未标注抽帧原图（6238 张，同一批） | **弃**（抽帧脚本 + manifest 可完全重生成） |
| `eval_out/` | 1.4 G | 各臂检出/事件产物（含 `_dbg_*`/`_frames` 等调试档） | **弃**（从 positions.json 重放分钟级；只挑存档臂留） |
| `third_party/` | 1.1 G | 7 个开源项目浅克隆 + `bbh_snap`(v16/v17 代码快照) | 代码部分**弃**（可重 clone，已提炼成对照报告）；`bbh_snap` **保** |
| `reports/` | 215 M | 报告 md/json + 巨型日志 | **挑着保**：md/json 精华 ~5MB；`_queue_*.log` 220M **弃** |
| `runs/v0_auto/` | 41 M | 训练曲线/预览 + `weights/best.pt` | weights **已继承**；曲线图可弃 |
| `preannot/` | 43 M | 半自动预标注（labels + preview） | 留着备查（体积不大） |
| `scripts/` | 588 K | 40+ 分析/训练/评测脚本 | **保**（信息密度最高的一坨） |
| `weights/yolo26n.pt` | 5.3 M | base 权重 | **弃**（官方可下） |
| `tools/indep_review.html` | 16 K | 无偏真值回看工具 | **保** |
| `truth_indep/` | 4 K | 无偏真值目录（目前空，只有协议） | **保**（待你人工回看后填） |

---

## 1 本项目已经继承了什么（这些在 yolo 里可放心归档）

| 已继承 | yolo 侧来源 | 落地位置 | 校验 |
|---|---|---|---|
| 训练好的权重 | `runs/v0_auto/weights/best.pt` | `bbh/weights/best.pt` | md5 完全一致：`4a04536d46db30102ba2f7b10e64698b` |
| YOLO 主检测 | `scripts/detector_yolo.py` | `bbh/detector_yolo.py` + `detector_switch.py` | 已同签名接入、进 `DETECT_VERSION` 指纹 |
| 静止链过滤 b1m | `scripts/static_filter.py` | `bbh/static_filter.py`（默认 `BBH_STATIC_FILTER=b1m`） | 已移植 |
| 三口径评测套件 | `scripts/{sym_audit,kpi_official,timing_corrected,compare_ab}.py` 的方法 | `tools/eval_lib.py` + `scripts/eval_report.py` / `run_eval.py` | **已重写成独立版本**，不 import 外部工程 |
| 检出原理对比 / 自测记录 | E1 系列报告 | `docs/检出原理对比_YOLO_vs_HSV.md`、`docs/自测记录_2026-10-11.md` | 已写 |

> 注意：评测套件是**在本项目内重新实现**的，不是把 yolo 的脚本搬过来。
> 所以「回放 yolo 那批结论数字」仍需 yolo 侧脚本或快照（见 §5 口径提醒）。

---

## 2 P0 · 必继承（唯一 / 不可再生 / 决定后续能不能做）

### 2.1 标注数据集与训练闭环（**最不可再生**）

| 项 | 说明 |
|---|---|
| `dataset/labels/{train,val,test}/` | **人工标注的框**：ball 5545 + hoop 6158。这是全项目唯一的人力沉淀，丢了要重标 2 个周末 |
| `dataset/{dataset.yaml,dataset_auto.yaml}` | ultralytics 配置（类别定义 + 划分） |
| `manifest.jsonl` | 抽帧清单（video/frame/t/kind，1.2MB）——**images 靠它重建** |
| `scripts/{extract_frames.py, split_dataset.py, train.py}` | 抽帧 → 按视频划分 → 训练，闭环三件套 |
| `scripts/{gen_preannot.py, check_labels.py, manif.py}` | 半自动预标注 + 标签质检 |
| `标注资产审计.md` | 说明「哪些老标注能复用、哪些必须新标」的审计结论 |

**要点**：`dataset/images/` 和 `raw_frames/` 是 6238 张 jpg（合计 5.2G），
但都能从源视频 `D:\claude code\1视频获取\归档\*.mp4` + `manifest.jsonl` 重生成。
→ **建议只继承 labels + manifest + 脚本，images 用脚本重建**，省掉 5G。

### 2.2 结论与决策报告（体积 < 3MB，信息密度最高）

| 报告 | 价值 |
|---|---|
| `reports/目标回顾与待办.md` | **总纲**：现状、结论、ABCD 四类待办（含 A2/A3 未做项） |
| `reports/HSV环节清单与替换可行性.md` | 全部 5 个可注入点 + 可替换性矩阵（A1 已做，A2/A3/A3′ 待做） |
| `reports/决策记录_是否用YOLO替换HSV.md` | 决策链（旧「不替换」→ 新「基本不差于」，含改判理由 + 8 个自修 bug） |
| `reports/时刻偏差纠正后_效果数据.md` | **最关键的一把尺子**：扣掉时刻偏移后的同尺对比 |
| `reports/KPI_官方口径_四臂.md` | 官方 0.2s 口径四臂表 + 12.8pt 差距的构成拆解 |
| `reports/口径漂移与点流修复_发现.md` | 口径钉版方法论（v16/v17 快照、sidecar 缓存隐患） |
| `reports/优化路线_YOLO提升.md` | 提升路线 O1~O10（重训/补标/双层 conf 门），明确「后处理已到头」 |
| `reports/参考_成熟开源项目技术对照.md` | 4 个可借技术点（双层 conf 门 / ROI 并集 / 在线卡尔曼 / 点级几何清洗） |
| `环节盘点.md`、`PLAN.md`、`README.md` | 任务缘起、分期计划、验收标准（历史留档） |

---

## 3 P1 · 建议继承（直接支撑后续 A2/A3/重训/验收）

### 3.1 评测与消融脚本（复用价值高）

```
run_ab.py            # 单臂跑 A/B（同签名接入 + 独立 eval_out）
compare_ab.py        # 双臂对比
filter_ab.py         # 过滤策略对比（b1m 的 +6 就是它算的）
sym_audit.py         # 对称口径总表（±0.5s 双向 1:1）
kpi_official.py      # 官方口径 KPI（对接原项目 tools/evaluate.py）
timing_corrected.py  # 扣掉时刻因素后的效果数据
replay_layer.py      # 点流复用重放（秒级重推判定层）
truth_yolo_audit.py  # 7 场 890 球验收尺子
promote_baseline.py  # 基线提升/固化
repair_arms.py       # 点流残缺修复（check/apply/restore）
snap_bbh.py          # git archive 出 bbh 版本快照
_guard.py            # YOLO_BBH_SNAP 重定向（口径钉版）
posio.py             # positions.json 读写
eval_point_pr.py     # 球点级查全/查准（需人工球位真值）
eval_detection.py    # 检出层 L1/L2 指标
bench_speed.py       # 速度基准
```

### 3.2 诊断脚本（出事时能直接跑）

```
diag_miss_balls.py    # 丢球归因（全量）
diag_static_chain.py  # 静止链误删根因
diag_recon_why.py / ablate_recon.py   # 重建层为何负贡献
probe_net_gate.py     # 网门对 HSV 的降档率（只读探针）
sim_filter_fix.py / frame_probe.py / diag_{align,gap,tier,crossing_why,miss}.py
traj_reconstruct.py   # 轨迹重建层（判定为负贡献、默认关，带 3 个自检夹具）
static_filter.py      # 静止链过滤策略源实现（orig/b1/b1m）
```

### 3.3 无偏真值工具链（**A1 待办唯一依赖**）

- `tools/indep_review.html`：单文件、无依赖的回看工具（只看视频、不开表格 → 去掉尺子偏置）
- `scripts/indep_truth_audit.py`：独立真值打分
- `truth_indep/README.md` + `reports/无偏真值_回看协议.md`：协议与操作说明
- 用途：给 `2026-08-29_3`（test 场）造一把不锚定 HSV 的尺子。**需你人工回看 1~2 小时**

### 3.4 自动筐（E2 前置，自研、无开源参照）

```
auto_hoop.py / probe_hoop_auto.py / compare_hoop_ab.py / diag_hoop_roi.py / merge_hoop_scan.py
reports/auto_hoop_segments*.json、DIAG_自动筐重放_口径审查.md
```

### 3.5 口径快照

- `third_party/bbh_snap/v16_pkg`（← b609574）、`v17_pkg`（← 4233671）：只读代码快照，
  配合 `_guard.py` 可让任何结论「钉在某个版本上」。体积小，留档成本极低。

---

## 4 P2 · 留档即可（不合并进本项目）

| 项 | 理由 |
|---|---|
| `third_party/` 的 7 个开源项目源码（1.1G） | 价值已提炼进 `参考_成熟开源项目技术对照.md`；需要时重新 clone |
| `reports/` 里的过程性诊断 md（E1_*、AB_*、B0_*、DIAG_* 等 100+ 份） | 结论已汇总到 §2.2 的 9 份；其余作为留档 |
| `preannot/`（43M） | 半自动预标注中间产物，留 `README.md` 即可 |
| `eval_out/` 里挑 2~3 个存档臂（如 `hsv_v16_net`、`yolo_c03_s1`） | 作为「历史基线逐条可复现」的实物凭证 |

---

## 5 ★ 口径提醒（继承时必须写清，否则数字会误导）

1. yolo 那批结论数字是 **LOGIC_VERSION v16 / v17**（v17 快照 commit `4233671`）口径；
   本项目已 **fork 到 v18**（`3b11861`）。**两边的验收数字不可直接搬运。**
2. 若要在本项目复用那张「四臂表」，需在 v18 下用 `replay_layer.py` 重放（分钟级，
   点流已存在即可，不用 GPU）。
3. 权威口径 = 本项目 `tools/eval_lib.py` 的三口径（官方 / 对称 / 容差扫描），
   与原项目 `tools/evaluate.py` 对齐——yolo 侧的表是同一套方法的早期版本。
4. 权重 md5 已核对一致，**模型本身不需要重新继承**（只继承数据与脚本以支持重训）。

---

## 6 建议的整理动作（待你确认后执行）

**方案 A（推荐）：在 yolo 内改造为「训练/实验子仓」，只留核心**
- 保留：`dataset/labels/`、`dataset/*.yaml`、`manifest.jsonl`、`scripts/`、`tools/`、
  `truth_indep/`、`reports/` 里的 md/json（删日志）、`third_party/bbh_snap/`
- 清理：`raw_frames/`、`eval_out/`（挑 2~3 臂存档）、`reports/_queue_*.log`（220M）、
  `third_party/` 开源源码、`runs/` 曲线图、`yolo26n.pt`、各 `__pycache__`
- 预计从 ~8G 降到 ~0.3G

**方案 B：把核心文档/脚本复制进本项目，yolo 整体归档**
- 在本项目新建 `training/`（放数据集 labels + 训练脚本）与 `docs/research/`（放结论报告）
- yolo 目录整体移到 `D:\claude code\_archive\yolo\` 封存

**方案 C：只做文档继承，yolo 原样不动**
- 把 §2.2 + §3 的文档脚本清单作为索引写进本项目 README，需要时按路径去取

> 任何删除动作执行前，我会先列出完整文件清单并等你逐项确认（不做静默删除）。

---

## 7 执行记录（2026-10-11 · 已按方案 A 执行）

**执行方式：同盘隔离移动，不删除**——所有移出内容都在
`D:\claude code\_yolo_archive_2026-10-11\`，可原样搬回。
（详见 `D:\claude code\yolo\整理说明_2026-10-11.md`）

| 结果 | 数值 |
|---|---|
| yolo 原体积 | **≈ 12.9 G**（此前 §0 报的 8 G 漏算了 `.venv` 4.9 G） |
| 整理后（不含 `.venv`） | **≈ 143 M** |
| 整理后（含 `.venv`） | ≈ 5.0 G |
| 移出到归档区 | 7.7 G |

**移出明细**：`raw_frames/` 2.6G、`dataset/images/` 2.6G、`eval_out/` 46 个次级臂 1.3G、
`third_party/` 开源源码 1.1G、`reports/_queue_*.log` 等 209M、`runs/` 曲线图 29M、
base 权重 11.8M。

**保留**：`dataset/labels`(11M) + `manifest.jsonl` + `dataset*.yaml` + `scripts/`(54 个)
+ `reports/`(100 份 md/json) + `eval_out/{v17_hsv, yolo_c03_s1}` 两个基线臂
+ `third_party/bbh_snap/` + `runs/v0_auto/weights` + `preannot/` + `tools/indep_review.html`
+ `truth_indep/` + 4 份顶层文档。

**追加执行（同日，用户确认）**：

| 项 | 结果 |
|---|---|
| 归档区 `_yolo_archive_2026-10-11\` | **已删除**（7.7 G 真正腾出） |
| yolo `.venv/` | **已删除**（4.9 G） |
| **口径升级 v16/v17 → v18** | 新增快照 `third_party/bbh_snap/v18_pkg`（← 原项目 `视频剪辑` `0f09825`，`LOGIC_VERSION=18`），已实测 `YOLO_BBH_SNAP=v18` 可加载；约定「评测一律带 `YOLO_BBH_SNAP=v18`」 |

**最终体积**：yolo = **159 M**（原 12.9 G，净减约 12.7 G）。

**⚠️ 连带影响（重要）**：`eval_out/v17_c03_s1`、`v17_c03_s2`、`v17_c10` 等次级臂
随归档一起删除。它们是 **v17 口径**、且点流可重放性本身残缺（上游段块指纹缺陷）；
数字已留档在本项目 `reports/EVAL_probe.md`。本项目评测套件用 `--arm` 传路径、
**无硬编码依赖**，但若要**重新生成**那张 v17 表，需真实重跑流水线
（≈14 分钟/场，网层开），或先按 `yolo/reports/口径结论_v18.md` §4 修点流。

**下游待办**：`dataset/images` 的重建依赖源视频
`D:\claude code\1视频获取\归档\`（现存 17 个）；重建命令见 yolo 侧整理说明 §3。

**追加执行（2026-10-12 · v18 数字已重跑）**：用户确认后，yolo 侧已把两条主臂
在 v18 口径下**真实重跑**完毕（HSV 同源臂 ≈70 min；YOLO c03_s1 臂 445 min，
`step=1` 逐帧），7/7 场全部成功、`logic_ver=18`。结果：
官方口径（0.2s 行锚定）HSV 87.3%/74.4% 过、c03_s1 76.6%/58.7% **不过**
（漏检 16.5% 压线，剔除 09-19 后 12.8% 过）；对称口径（0.5s）c03_s1 🟢率 **88.9%
首次反超** HSV 87.4%。详见 `yolo/reports/V18_双口径重跑结论.md`、
`KPI_官方口径_v18.md`、`SYM_audit_v18.md`。
基础设施：`replay_layer.py` 加 `--allow-rescan`（仅 hsv 源）、`sym_audit.py`
加 `--variant v18`、新增 `kpi_official_v18.py`（v17 原表保留）。
**遗留**：09-19_1 是 YOLO 唯一崩场（官方查全 36.1%），待专项诊断；
无偏真值回看（待办 A1）仍未做。下游如需引用 v18 数字，以
`yolo/reports/KPI_官方口径_v18.md` / `SYM_audit_v18.md` 为准。
