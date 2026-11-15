# 出手人自动归人专项调研：跟踪 + re-ID 聚类路线（单场每人标一次）

> **⚠ 2026-09-11 Spike 1/2 实测补记：层2 已证伪，本文路线图按实测定案为「层1 规则链 + 人工合并」（即原预案的退化路线），不再产品化。** 证据链与归因详见文末「六、Spike 实测结果」。要点：OSNet 双档（MSMT17 权重）在我们远景便装素材上**同人/异人余弦完全重叠（中位 0.701 vs 0.700）**，对拍命中率 11~14%（判据 ≥80%），聚类最优纯度 67.6%（x1_0）/58.3%（x0_25）、17 人覆盖最高 5/17（判据 ≥95%/≥90%）——风险清单 #1 应验：MSMT17 特写行人权重在 49×144px 远景便装 crop 上身份信号被淹没。crop 目检确认检测框本身有效（8 抽样 6 格干净单人框），排除「框不是人」的最后归因。

> 调研日期：2026-09-11 ｜ 方法：GitHub 搜索 player re-identification / soccer player reid / player tracking / tracklet clustering / ball possession / SportsMOT / jersey number 等 12 组查询 + 4 个并行 agent 深读 15 个仓库源码
> 目标方案：单场视频内做到「每人只标一次」——层1 持球链归属（球→tracklet）+ 层2 外观聚类合并（tracklet→人，30~80 碎片→≈17 簇）。跨视频识别（层3）已明确砍掉。
> 逐仓笔记：`notes/`（本轮 15 份 + 首轮 20 份）

---

## TL;DR

1. **架构被业界数据双重背书**：跟踪层用纯运动关联、外观 embedding 只做离线 tracklet 聚类——SportsMOT 消融显示篮球场景在线 ReID 增益仅 +0.7 HOTA、纯外观关联在篮球上崩溃（α=0 时 26.9）；而 tracklet→人的聚类层不可省（SportsMOT 榜纯运动 67.9 vs 带 reid 76~83 HOTA）。
2. **选型定案**：跟踪 = BoT-SORT(GMC=sparseOptFlow, with_reid=False)（GMC 每帧稀疏光流补偿恰好扛「偶尔被碰移位」，与 ORB 互补）；embedding = OSNet_x1_0 + sportsreid 的 SoccerNet 微调权重（唯一有「同款球衣不同人」难负样本训练证据的模型，2.2M 参数）；工程接入 = boxmot 一站式 + torchreid 只导 ONNX；持球 = 混合路线（筐周 HSV 球检出作硬锚点 + 关键点距离/containment/滑窗票规则连链，远期约束 Viterbi 升级）。
3. **最大未验证风险**：同色系 17 人在我们远景便装素材上的 OSNet 分离度——sportsreid 的同装证据产自转播特写画质，必须用我们 08-28 场 109 球/17 人真值跑 Spike 实测，不重蹈颜色线盲上的覆辙。
4. **两个硬天花板数字定案了路线边界**：扩球检测（训练过的 YOLOv8n 球召回仅 51.3%，E-BARD/NBA 域）不作主路线；无球路径推断（PathCRF F1 75.7%）依赖光学跟踪级干净轨迹，模型搬不动，但其「约束 Viterbi 拿走大部分收益」的消融给了零训练升级路径。

---

## 一、调研对象矩阵

### A. 球员 re-ID 学术/工程线（层2 聚类对口）

| 仓库 | ★ | 方法 | 对同色系（无队服无号码）适用性 | 对我们价值 |
|---|---|---|---|---|
| shallowlearn/sportsreid | 26 | OSNet/ResNet50/ViT 在 SoccerNet ReID 训练（mAP 89.8）；**层级采样刻意排入同队同款球衣者做难负样本**+centroid loss；tracklet 帧特征 avg 池化 | 唯一有同装难负训练证据；但证据域=转播特写画质 | ★★★★★（embedding 直接用它的权重） |
| abhinine4/Soccerplayer_Reidentification | 2 | ResNet50 外观 + OpenPose 部位 Compact Bilinear 融合 | 同装纯外观有天花板（其动机）；融合网络仅 +2 mAP 不值 | ★★★（问题定性） |
| nalin1096/DS5500_…NBA | 6 | 身份来自官方 play-by-play+记分牌 OCR；KMeans(2) 分队 | 完全不适用（依赖官方数据源） | ★★（球场单应→位置先验思路） |
| Mayur-arora-05/CrossCam | 0 | CLIP+姿态+色相+位置+密度融合，匈牙利 1:1 | 低（CLIP 通用语义同装区分弱；色相/OCR 已证伪） | ★★★（must-not-link 匈牙利实现+记忆库形态） |

### B. 篮球/持球专项（层1 归属对口）

| 仓库 | ★ | 持球逻辑 | 价值 |
|---|---|---|---|
| HanaFEKI/AI_BasketBall_Analysis_v1 | 74 | 球→人逐帧归属：12 关键点最小距+containment>0.8 两级判定；5 帧滑窗多数票(≥2)+grace 3+last_valid 兜底；球检出经 25px/帧速度门+线性插值清洗 | ★★★★★（层1 规则全套可抄） |
| hyunsungkim-ds/pathcrf（KDD 2026） | 24 | **完全无球**：22 球员轨迹上逐帧选边推持球路径，Set Attention+CRF+Viterbi，事件 F1 75.7%；纯约束 Viterbi 73.1≈完整模型 | ★★★★（零训练升级路径+歧义上限定性） |
| yi-cheng-liu/basketball_registration | 13 | registration=场地单应（ResNet18 关键点+RANSAC）非球员注册；无持球无身份 | ★★★（米制坐标修距离失真+站位先验） |
| GabrieleGiudic/E-BARD | 6 | 数据集：1800 帧 NBA 检测标注 CC-BY-4.0；**训好的 YOLOv8n 球 Recall 仅 0.513、RF-DETR 小目标 F1=0；hoop mAP50=0.993** | ★★★★（球检测天花板实证） |

### C. 跟踪数据集与竞赛线（跟踪层选型对口）

| 仓库 | ★ | 关键数字 | 价值 |
|---|---|---|---|
| MCG-NJU/SportsMOT（ICCV 2023） | 227 | 240 段/15 万帧/163 万框，篮球足球排球各 1/3，720p25fps，CC BY-NC 百度网盘直下；**篮球最难：MixSort-Byte IDF1 67.8/AssA 46.8**；三消融：Kalman 有害（IoU-only 71.5 vs +KF 64.1）/纯外观篮球崩溃（26.9）/在线 ReID 仅 +0.7 | ★★★★★（val 45 段含篮球 GT+TrackEval=零成本跟踪层考卷） |
| SoccerNet/sn-tracking | 110 | ByteTrack baseline HOTA 71.5；给 GT 框纯关联榜首 AssA 也只 88（密集交错掉 10+ 分） | ★★★（评测脚手架+两阶段归因法） |
| apiantonio/SoccerNet-tracking_AV2025-26 | 5 | YOLO11x@1088→BoT-SORT，**GMC=sparseOptFlow、with_reid=False**，HOTA 0.742；match 0.9/new_track 0.4/buffer 150；HSV+LAB 脚点掩码滤场外人 | ★★★★（GMC 用法+参数组+场外人过滤） |
| rustyneuron01/Real-Time-Football-Detection | 73 | README 注水（宣称 HRNet/OSNet/FastReID 零代码实现）；真实 CPU 0.08fps 不可用 | ★★（教训：README 须 grep 验证） |

### D. 可插拔组件（工程接入对口）

| 库 | ★ | license | 接入要点 | 价值 |
|---|---|---|---|---|
| mikel-brostrom/boxmot | 8293 | AGPL-3.0（个人本地无实际约束） | `tracker.update(dets, frame)` numpy 进出；9 tracker、reid 件可插可关（`use_embeddings=False`）、可单独 `create_reid_encoder`；自带 ORB/ECC CMC | ★★★★★（零件箱整库用） |
| KaiyangZhou/deep-person-reid（torchreid） | 4906 | MIT | `FeatureExtractor→(B,512)`；256×128+ImageNet 归一化；osnet_x0_25=0.2M/R1 91.2，osnet_x1_0=2.2M/R1 94.2；官方 ONNX 导出；权重 HF kaiyangzhou/osnet；**装库要编 Cython→只导 onnx 不装库** | ★★★★★ |
| roboflow/supervision | 49952 | MIT | **sv.ByteTrack 已废弃**（0.31.0 移除）；无 reid 件；只借 match_detections/标注器等工具 | ★★★ |

---

## 二、关键技术决策（附证据）

### 1. 跟踪器：BoT-SORT(GMC=sparseOptFlow, with_reid=False)，不用裸 ByteTrack、不用在线 ReID

- apiantonio 在 SoccerNet challenge 拿 HOTA 0.742 的配置；GMC 用稀疏光流逐帧补偿相机运动——**我们「偶尔被碰移位」场景恰好对口**：GMC 保轨迹连续跨过移位瞬间，我们已有 ORB 继续管分析层坐标重定基线，两者互补不冲突。
- 参数初始值直接抄：match_thresh 0.9、new_track_thresh 0.4、track_buffer 150（@30fps 语义）。
- SportsMOT 三个消融背书「纯运动关联」：Kalman 预测有害（IoU-only 71.5 vs +KF 64.1）——boxmot 的 BoT-SORT 需确认能关 KF；在线 ReID 在篮球上增益仅 +0.7 且外观不可分时崩溃。
- 副产品技巧：apiantonio 的 HSV+LAB 脚点掩码只保留「站在场地上的人」——我们场边有坐着的观众/水瓶，这招能滤掉大半非球员 track。

### 2. embedding：OSNet_x1_0 + sportsreid SoccerNet 权重（spike 再定尺寸档）

- sportsreid 是唯一显式做「同队同款球衣不同人」难负样本训练的（层级采样+centroid loss），这正是我们同色系场景的病；CLIP 通用语义嵌入两仓互证偏弱，只作对照基线。
- 用法：每 tracklet 抽 5~10 个高置信检测帧 → crop 256×128 → 特征 **avg 池化 + L2 归一化** = tracklet 单向量，余弦度量（sportsreid 原生支持 tracklet 级）。
- 尺寸档：x1_0（2.2M，R1 94.2）为主；若 CPU 吃紧降 x0_25（0.2M，R1 91.2）——** Spike 2 同时 A/B 两个尺寸**，看同色系分离度掉多少。跨域加强可试 ain 系（torchreid），备选。
- 接入：torchreid tools/export.py 导 ONNX，onnxruntime 推理，不装 torchreid 库。

### 3. 层2 聚类设计：层次聚类 + 两条硬约束 + 真值扫阈值

- **must-not-link 时间重叠约束**（同帧并存的两个 track 必非同人，匈牙利/查并集实现，CrossCam 示范）——零成本纯逻辑，NUSTemple 首轮复盘同款教训。
- **球场坐标活动区域先验**（DS5500 的单应思路：固定机位标定一次，把 tracklet 质心映射到米制球场坐标，同簇站位分布应一致）。
- **余弦阈值不手拍**：用 08-28 场 109 球/17 人真值做阈值扫描，选簇纯度/完整度帕累托最优；簇数由阈值驱动不预设 17；孤立短 tracklet 留人工兜底——**宁可少合不错合**（merge 错簇=把别人的球悄悄归错人，比 split 危险）。
- 旁证：部位+外观融合网络仅 +2 mAP（abhinine4），第二通道用简单特征（身高/站位）即可，不值得训网络。

### 4. 层1 持球链：混合路线（锚点+规则），不扩球检测、不上无球推断模型

- **不做主路线的两个决定**：①扩球检测——E-BARD 实证训好的 YOLOv8n 球召回仅 51.3%（我们无标注数据只会更差），且我们瓶颈从来不在筐周检出；②PathCRF 式无球模型——前提是光学跟踪级干净轨迹+逐帧持球真值，我们没有，搬不动；其边准确率 ~70% 也说明纯轨迹推断本质歧义。
- **务实混合**：person/hoop 用 YOLO（E-BARD 实证这两类很稳：hoop mAP50=0.993）；球只在筐周用我们现成 HSV 检出（出手后段天然在筐周 ROI 内=硬证据锚点）；锚点之间/出手前段用 **HanaFEKI 规则**连链：球块→人框 12 关键点最小距 + containment>0.8 两级判定、5 帧滑窗多数票(≥2)+grace 3+last_valid 兜底、球检出 25px/帧速度门+线性插值清洗（速度门可直接加在我们 HSV 检出后处理上）。
- 远期升级：PathCRF 消融证明「手写 emission 分数 + 约束 Viterbi 全局解码」零训练即拿其 73.1/75.7 的大部收益——若规则连链在真值上不够，这是现成的升级路径（17 人 289 边，CPU 秒级）。

### 5. 窗口化处理（成本关键设计）

不需要全片连续跟踪：**只在我们需要出手人的事件窗内跑 person 检测+跟踪**（每球出手前 ~8s 到 +2s），窗口内 tracklet 本来就是碎片（层2 聚类负责跨窗合并），还天然规避长时 ID 漂移——与 SportsMOT「短窗 IoU 关联更稳」的结论一致。CPU 预估（组件组推断值）：全片逐帧 30min 视频检测 40~90 分钟；窗口化+隔帧可再砍一个量级；跟踪毫秒级；~2000 crop 提特征仅 1~2 分钟。**瓶颈=person 检测，spike 时实测定窗口/步长。**

### 6. 评估资产

- 我们的 3 场人工归人真值（08-28：109 球/17 人最完整）= Spike 1/2 的判卷标准。
- SportsMOT val 45 段含篮球 GT + TrackEval = 跟踪层（GMC 参数、buffer、碎片率）的零成本标准考卷，先在它上调好跟踪再上我们视频。

---

## 三、业界证据对我们的验证与修正

| 原设想 | 调研后结论 |
|---|---|
| ByteTrack 纯运动跟踪 | ✅ 纯运动方向对，但**修正为 BoT-SORT+GMC**（碰移位场景+竞赛实证）；Kalman 需可关（SportsMOT 有害消融） |
| supervision 封装 ByteTrack 即用 | ❌ 已废弃（0.31.0 移除）——改走 boxmot |
| OSNet/CLIP 二选一未定 | ✅ 定 OSNet（同装难负训练证据）；CLIP 降为对照 |
| 持球链可能需要扩球检测到全场 | ❌ 不需要且不该（51% 召回天花板）；筐周 HSV 锚点+规则连链够用 |
| 全片跟踪再聚类 | ✅ 修正为**事件窗口化**，成本降一个量级且更稳 |
| 层2 聚类阈值手拍 | ❌ 用 17 人真值扫描选点；must-not-link+位置先验两条硬约束加上 |
| 在线 ReID 跟踪器（DeepSORT 式） | ❌ 篮球场景增益≈0 且同色系必崩（SportsMOT+apiantonio 双证）——离线聚类是唯一形态 |

## 四、风险清单（诚实预期）

1. **同色系 17 人 OSNet 分离度（最大风险，Spike 2 判生死）**：sportsreid 同装证据是转播特写画质（球员大、清晰）；我们是远景便装、crop 后 100~300px 高、还常被遮挡。若分离度不足→层2 失败退化为「层1+人工合并」（400 球→30~80 片段，仍值）。
2. **持球链锚点覆盖率**：出手前段球离开筐周 ROI 后，HSV 全帧找橙的误检（观众橙衣/地板反光）可能污染归属——速度门+containment 两级过滤是防线，Spike 1 实测命中率。
3. **CPU 检测耗时**：窗口/步长没调好可能一小时级；有 SportsMOT 考卷先调参，不盲跑。
4. **merge 错簇的静默错误**：层2 的失败形态里最危险的一种（别人球归错人还不显眼）——簇永远只作预填建议+每簇人工点一次名（本就是产品形态，天然防线）。

## 五、Spike 验证方案（两层各自判生死，先评估后产品化）

**Spike 0（准备，1 小时）**：下 SportsMOT val 篮球子集 + boxmot/torchreid onnx 权重；在 SportsMOT 上调 BoT-SORT(GMC) 参数（match 0.9/buffer 150 起点），记录 HOTA/IDF1/碎片率作基线。

**Spike 1（层1 生死，半天~1 天）**：08-28 场 109 个已归人球，每球窗口（出手前 8s~后 2s）跑 YOLOv8n person + BoT-SORT(GMC) + 筐周 HSV 球锚点 + HanaFEKI 规则链 → 输出每球「最后持球 tracklet」。
判据：**该 tracklet 的 crop 是否与用户归的人一致**（用已归人球做对拍）。≥80% 命中=层1 成立；50~80%=修规则（速度门/窗口/grace）；<50%=层1 证伪，退回墙流程。
产出顺带：~2000 tracklet crop + 特征（Spike 2 的输入）。

**Spike 2（层2 生死，1 天）**：Spike 1 的 tracklet 特征（osnet_x1_0 与 x0_25 双档）→ 层次聚类+must-not-link+位置先验 → 与 17 人真值对比。
判据：**簇纯度**（每簇内同人占比）与**每人覆盖**（17 人各有主簇）。纯度≥95% 且覆盖≥90%=层2 成立（每人标一次达成）；纯度 80~95%=聚类可用但簇内需人工确认（仍降本）；<80%=层2 证伪，退化为层1+人工合并。
关键动作：**余弦阈值扫描曲线**（0.5~0.9 步长 0.02）与「同帧 must-not-link 前后对比」一并出图。

**Spike 3（取消）**：跨视频识别不做（用户已砍层3）。

---

## 六、Spike 实测结果（2026-09-11 补记，两层定案）

**执行环境**（throwaway venv，代码不进仓库）：ultralytics 8.4.147（YOLOv8n person）/ boxmot 11.0.1 BoT-SORT(GMC=sof, with_reid=False) / OSNet x1_0 + x0_25 官方 MSMT17 权重（boxmot backbone 构建 num_classes=4101 + 掉 classifier 头出 512 维）。窗口=出手前 8s~后 2s，隔帧 stride 2，conf 0.3。全量 96 段 / 16265 检测帧 / 1071s 视频约 18 分钟跑完。

### 层1：规则链跑通，但「109/109」只说明无兜底缺失，不说明归属正确

- 109 球全部归属到 tracklet（**0 个走前扩兜底**）——持球链规则（筐周 HSV 锚点+containment/中心距+2s 窗多数票+5s 前扩）在本场素材上没有缺口。
- 碎片化图谱：107 tracklet / 17 人（john 12、阿骆 12、阿林 10、立健 9、阿煌 9、小左 9、阿伟 6、毛哥 6、昭哥 6、老陈 6、老谭 5、周敏 4、omg 4、老王 4、三分射手 3、孙权 1、星光 1）——与调研预估 30~80 碎片同量级偏多。
- **诚实边界：层1 归属正确率无法单独验证**（没有 tracklet 级真值）。调研 Spike 1 判据「crop 与用户归的人一致」需要靠层2 特征对拍实现——层2 证伪后，层1 正确性成为悬置项。帧级诊断（同 track 5 连帧 cos 0.936）说明 tracklet 本身是稳定单人轨迹，链路机制无硬伤。

### 层2：OSNet 无身份信号，四条独立证据实锤（判死）

1. **对拍命中率 13.8%（x0_25）/ 11.0%（x1_0）**——判据 ≥80%。留一最近邻：每球 tracklet 特征在其他 106 球里找最近邻，邻的真人=本球真人的比例。
2. **同人/异人余弦完全重叠**：同人球对中位 0.701 vs 异人 0.700（x1_0；x0_25 同形态）——特征空间里「同一人」和「不同人」没有可分性。两轮实现修正后依旧：①帧级 L2 先于 avg（防高范数帧主导池化）②crop 外扩 0.12→0.02（去背景）→ 0.701 vs 0.700 纹丝不动。
3. **帧级有区分度、avg 后坍缩**：同 track 连续 5 帧间 cos 中位 0.936，同帧并存不同人 0.467——单帧特征对「当下框里是谁」有信息；跨时间平均后全部坍缩到 0.70 附近，说明被平均掉的不是身份而是位置/背景/光照等共模。
4. **位置假说否定 + crop 目检框有效**：按出手点距离分桶（0-100/100-200/200-400/400+px），所有桶同人-异人 gap ≤0.03、同人 cos-距离相关 -0.110——特征不是「编码了位置」而是纯无身份信号；8 人 tracklet 中间帧 crop 目检（放大拼图）：6/8 为干净完整单人框（人占比>70%、框贴合），仅 2 格坏样本（两人对抗重叠、坐姿模糊）——**排除「检测框没框住人」的最后归因**。

### 聚类佐证（spike2 判据数字）

层次聚类（平均 linkage cos 距离）+ must-not-link（同帧并存禁合并）全阈值扫描 0.50~0.90：

- x1_0 最优：阈值 0.90 → 66 簇 / 108 tracklet（几乎不合并），加权纯度 67.6%，17 人覆盖 5/17、主簇覆盖 1/17
- x0_25 最优：阈值 0.90 → 55 簇，纯度 58.3%，覆盖 2/17
- 判据纯度 ≥95% 且覆盖 ≥90% → **差一个量级，层2 证伪**。形态说明：能保纯度的唯一方式是「不合并」（66 簇≈每簇 1.6 个 tracklet），同人在不同球的 tracklet 根本聚不到一起——正是余弦完全重叠的直接后果。

### 定案

- **归因=调研风险清单 #1 应验**：sportsreid 同装证据产自转播特写画质；MSMT17 权重是特写行人域，我们的 crop 中位 49×144px、远景便装，身份信号（脸/发型/体态/衣着细节）在该分辨率+该权重域下不存在。原计划「若 OSNet 弱再试 sportsreid SoccerNet 权重」不再执行——SoccerNet 权重同为转播画质域（且 GDrive 不可达），域差同类，无翻盘理由。
- **路线退化为「层1+人工合并」**（调研预案）：层1 规则链已验证跑通且零兜底缺口，若将来做产品化，形态=持球链把 109 球归到 107 个 tracklet 片段、人工给片段点人名（约百次点击 vs 逐球全标）。是否值得做由用户定——09-06 已定案的手动归人+墙流程继续是主线，本结论只是把「将来自动归人」的可行边界划清。
- **证伪确认新增一条业界旁证**：abhinine4 的动机（同装纯外观有天花板）在我们素材上以更极端形式成立——不仅同装，还是远景小目标。
- 可复用资产（都在 `%TEMP%\reid_spike`，throwaway）：层1 持球链脚本（spike1_track/assign 的规则若将来产品化可移植）、96 段 segs 数据、双档特征 feats1.json、crops_diag 拼图。

---

## 附：本轮笔记清单（notes/）

sportsreid、abhinine4、nalin1096、Mayur-arora-05（re-ID 线）；HanaFEKI、pathcrf、yi-cheng-liu、E-BARD（持球线）；MCG-NJU__SportsMOT、SoccerNet__sn-tracking、apiantonio、rustyneuron01（跟踪线）；mikel-brostrom__boxmot、KaiyangZhou__deep-person-reid、roboflow__supervision（组件线）。克隆缓存 `%TEMP%\bball_research` 可删。
