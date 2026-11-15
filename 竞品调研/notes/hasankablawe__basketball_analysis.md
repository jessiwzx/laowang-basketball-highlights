# hasankablawe/basketball_analysis
一句话定位：NBA 转播画面的模块化分析管线——RT-DETR 检测 + SAM 2 mask 传播做球员追踪 + 单应投影 2D 半场图 + HSV 直方图 K-Means 分队 + 状态机投篮计分 ｜ ★5 ｜ Python ｜ 最后更新 2026-05-19（活跃）

## 球员检测/追踪/身份维持（重点：tracker/embedding/ID 维持细节，附代码引用）

- **不用任何经典 tracker**（ByteTrack/DeepSORT 都没有），身份完全靠 **SAM 2 video predictor 的时序记忆**：frame 0 用 RT-DETR 框 prompt 一次（`main.py` L255-261 `add_new_points_or_box`），之后 `propagate_in_video` 逐帧传播 mask，obj_id 即球员 ID（`main.py` L268-287）。mask→xyxy 后写 `detections_history`。
- **ID 恢复机制**：每 60 帧用 CPU 上的备用 RT-DETR 重检测一次，与现有 track 的 IoU<0.3 的框视为"新出现的球员"，注入新 box prompt 并**重启传播**（`main.py` L317-346）。这是它对"ID 丢失/新球员入场"的全部处理——没有外观 Re-ID embedding，纯靠 SAM2 记忆 + 周期性检测回注。
- `detection.py` 里留有一个 `detect_and_track`（ultralytics `model.track` = **BoT-SORT**）但主流程未用，是被 SAM2 方案替代的旧路径。
- 轨迹清理是**后处理数学**而非 tracker 能力：Savitzky-Golay 平滑 + 跳变抑制 + 起跳帧冻结（`main.py` L535-566，`analytics.py` `smooth_trail`）——脚部 y 突然上移 >25px 判为起跳，冻结该帧投影位置，防止跳起时脚点乱飘。这个细节对投影稳定性很关键。

## 归组/team assignment 方法（重点：非颜色特征有哪些）

- **现行方案（纯颜色）**：`analytics.py` `HueTeamClassifier`——bbox 中心 40% 裁剪（约球衣区），HSV 前景像素（max>30 去近黑）做 **18-bin H + 8-bin S = 26 维直方图**，K-Means(k=2) 聚类，**按簇平均饱和度锚定**（低饱和=白队），再对每个 track 的多帧 crop 标签**多数投票**。裁判剔除：中位框面积低于全体 10 分位的 track 不参与（`main.py` L370-384）。
- **重要演进史（注释里写明，对我们极有参考价值）**：作者先后试过三层信号融合——①**SigLIP 语义 embedding + KMeans**（96x96 球员裁剪、每 15 帧采样存 40 张，代码仍留在 `main.py` L289-303）②HSV 直方图 ③frame 0 球场左右半场位置锚定，三信号多数投票（`main.py` L353-359 注释）。最终**全部废弃，只留纯颜色**，理由写在注释里："SigLIP abstracts too much; 5v5 heuristics fail when subs/refs/occlusions happen. The purest signal is ACTUAL COLOR."（`main.py` L364-367）。
- **对我们**：这是一条别人替我们走过的路——语义 embedding（SigLIP）在**有队服的两队设定下**都输给了直方图颜色；我们的语料里颜色本身已证伪，等于这条仓库整个归组体系的地基（区分两支队伍）在我们场景不存在。我们要归的不是"2 个队"而是"10+ 个个体"，问题形态不同：需要的是 per-player 外观指纹而非二分。
- 非颜色信号仅剩两个可借鉴的骨架：**多帧多数投票**（对每 track 的时序标签做投票，天然抗单帧噪声）和**投影几何约束**（court 坐标）——后者他们用于画图和三分判定，没用于身份。

## 进球/投篮事件检测（若有）

- **ShotStateMachine**（`main.py` L62-161）：完全由 RT-DETR 的**类别检测驱动**，不看球轨迹。模型类别含 jump_shot(5)、layup_dunk(6)、ball_in_basket(1)。状态机：IDLE→(jump 或 layup 连续≥3 帧)→SHOT IN PROGRESS（START，deadline=fps*1.7）；进行中 ball_in_basket≥3 帧→MADE；超 deadline→MISSED；进行中又见新 jump/layup→MISSED+新 START。MADE 后 0.5s 冷却。IDLE 直接见 ball_in_basket 也算 MADE（补篮）。
- 打铁=纯"超时兜底"：deadline 内没看到球进筐就算 MISSED。没有球轨迹链，没有筐口几何。
- 辅助路径 `analytics.py` `detect_shot`（球投影点进入筐 8ft 邻域再离开=goal）是旧版逻辑，主流程未用。

## 出手归属逻辑（若有）

**有，两段式**（`main.py` L475-509）：
1. 检测到 jump_shot/layup 框时，取该框**底边中点**，与当前所有 SAM2 track 的脚点算欧氏距离，**最近且 <100px（图像空间）** 的 track = 出手人，其队伍得分（L486-490）。
2. 兜底：只有 ball_in_basket 没有动作框时，用进筐框中心**就近匹配球员**当得分手（L492-509）——注释自嘲这是 "Fix"，本质是"筐边最近的人"，野球里抢到篮板补篮的人往往离筐最近，这个兜底在缺动作检测时会错。
- 注意：归属只在**事件帧**做一次瞬时最近邻，没有"持球期连续跟随"（ball possession 链）的概念。

## 工程亮点与计算成本

- 三趟式批处理架构：抽帧落盘(jpg q95)→SAM2 全片传播（mask 存 npz）→重载检测模型做单应+事件→渲染。中途 `torch.cuda.empty_cache()` + 删模型换 VRAM、SAM2 `offload_video_to_cpu` + 手动剪 `cached_features`（只留 30 帧）防 OOM（`main.py` L196-313）。
- **必须 CUDA GPU**（SAM2 bf16 autocast），README 明说 "modern GPU with CUDA"；帧全部落盘再读，长视频 RAM 换磁盘。无任何速度数字。
- 模型不放仓库：`best.engine`/`RTdetrL_best.pt`/`sam2.1_hiera_small.pt`/`pose_yolo26m.pt` 全在 `constants.py` 引用但需自备。
- 每帧跑 33 关键点 court pose + RANSAC 单应，支持镜头切换后重算（好设计），`ok_mask.sum()>=6` 才接受。

## 声称的指标

README 与代码中**无任何量化指标**（无 mAP、无准确率、无 FPS）。只有定性声称 "state-of-the-art"、"True-Color Team Classification"。聚类打印信息只有簇饱和度与人数分布。

## 对我们项目的可借鉴点（结合痛点②重点说）

1. **出手归属的直接模板**：`动作框底边中点 ↔ track 脚点最近邻(<100px)` 这 6 行代码（`main.py` L486-490）就是我们痛点②的最小可行自动归人：我们已经有人工标注的出手点坐标（缩略图墙的锚点），缺的只是"出手点→轨迹 ID"的匹配。可以不引入 SAM2，用任意 tracker（如我们已有 HSV 球检测同源思路换 YOLO 人检测+ByteTrack）生成脚点轨迹后套用同一最近邻规则。
2. **"动作即检测类别"**：RT-DETR 直接把 jump_shot/layup/ball_in_basket 当目标类训练——说明投篮动作可以塞进检测器而不必单独做时序动作分类。我们若训练自己的检测器（半场野球域），把"出手动作/球进筐"设为类，可同时服务痛点①②。
3. **多帧多数投票做 track 级属性**：任何 per-track 的噪声属性（我们的"哪次出手属于谁"也类同）都可用"事件帧投票"压噪声。
4. **起跳帧冻结 + 跳变抑制的后处理**：脚点 y 上移阈值判跳、冻结投影点——如果我们做脚点轨迹用于归属，这个技巧直接可用（出手瞬间人人起跳，正是脚点最容易乱的时刻）。
5. SigLIP 尝试失败的教训值得记档：在他们有两队队服的设定里 SigLIP embedding 都没能稳定区分，野球便服下 embedding 分"个体"更悬——支持我们"外观 embedding 不能当主信号"的预判。

## 明确不适用的点及原因

1. **归组=颜色二分队**：我们颜色已证伪（人群灰白黑同色系），且我们需要的是 10+ 个体身份而非 2 队二分，整块 `HueTeamClassifier` 不可用。
2. **SAM2 全片传播的身份维持**：野球场 30min~2h、10+ 人频繁遮挡换防，SAM2 记忆在 NBA 的短片段演示里可行，长视频 ID 漂移无评估；且必须 NVIDIA GPU，成本高。其"每 60 帧检测回注"只能发现新人，不能把漂移的旧 ID 拉回来。
3. **33 关键点全场单应**：NBA 标准全场+固定转播机位才有充足关键点；我们是半场、篮筐 35-65px、机位偶尔被碰移位，关键点检测不可靠（虽然"机位移位后每帧重算 H"的思路本身是对的）。
4. **进球判定靠 ball_in_basket 检测类 + 超时即打铁**：打铁无正样本逻辑，比我们 v11 的轨迹+筐口几何判据弱得多（我们查准 78-83% 的方案信息量更大）；仅" MADE 后冷却 0.5s、防连环计数"可取。
5. **出手兜底=筐边最近的人**：野球补篮/抢板场景必错，不能照搬。
