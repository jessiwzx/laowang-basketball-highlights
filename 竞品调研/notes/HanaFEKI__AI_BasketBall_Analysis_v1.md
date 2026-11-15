# HanaFEKI/AI_BasketBall_Analysis_v1
一站式篮球比赛分析管线（检测/跟踪/持球/传球/拦截/控球率/战术视图）｜★74｜Python｜2025-07-25（ENSTA Paris 课程项目个人重写版）

## 持球/possession 逻辑（重点）
核心在 `ball_acquisition/ball_acquisition.py`（BallAquisitionDetector），逐帧球→人归属，参数硬编码：
- **距离度量**：不是框中心距，而是球心到球员 bbox 周围 **12 个关键点**的最小欧氏距离（`get_key_basketball_player_assignment_points`：左/右/上/下边与球心对齐的 4 点 + 框 9 个固定特征点）。比中心距更合理——球在脚边/身侧也算近。
- **两级候选**：先算「球 bbox 被球员 bbox 包含的比例」（`calculate_ball_containment_ratio`，交面积/球面积）；containment > **0.8** 的进 high_containment 组直接选中（组内取 max 距离，见下坑），否则退化为全距离最近邻，且 min_dist < **50 px** 才有效，无候选返回 -1。
- **时序去抖**：滑窗 deque(maxlen=**5**) 收集候选，`Counter.most_common` 多数票且 count ≥ 5//2=2 才采纳；不满足时沿用 `last_valid_player`；球丢失帧有 **grace_period=3** 帧宽限沿用上一持球人，之后保持 -1 直到新证据。
- **已知坑**：high_containment 组内用 `max(dist)` 而非 min（注释称 prefer containment，多人同时包含球时会选最远的那个）——`find_best_candidate_for_possession` L96。
- 下游 `pass_interception_detector/pass_interception_detector.py`：持球人变化 + 前后持球人 team 相同=传球、不同=抢断，纯规则无模型。

## 球员跟踪与身份维持
- `trackers/player_tracker.py`：YOLO（Roboflow basketball-players v17 自训）+ `supervision.ByteTrack`，conf=0.5，batch 20；结果 pickle stub 缓存（`utils/stubs_utils.py`）。
- **无 tracklet→人合并**、无 ReID embedding：track_id 即身份，ByteTrack 断了就换 id。团队归属只是逐帧贴标签。
- `team_assigner/team_assigner.py`：Fashion-CLIP zero-shot，文本 prompt 两个（"white shirt" vs "dark blue shirt"），按 track_id 缓存、每 50 帧清缓存重判。本质是二分类颜色，不是号码 OCR（README 把 OCR 列为 future work）。

## 球检测方法
- `training/ball_detection.py`：Roboflow `basketball-players` v17 数据集 + **YOLOv5l6u** @640、250 epochs（球员与球同模型同数据集，player 版 100 epochs）。
- `trackers/ball_tracker.py`：每帧只留置信度最高的"Ball"类单检测（并不真用 ByteTrack 跟球）；`remove_wrong_detections` 用**速度上限 25 px/帧**（线性按帧距放大）剔除飞出物理可能的检测；`interpolate_ball_positions` 用 pandas interpolate+bfill 线性补全缺失帧。三段式清洗（速度门限→插值→再进 possession）是其检出不稳的隐含自认。

## 实验指标
README 无任何量化指标，仅定性 demo（3 个 mp4 + gif）。属课程级作品，无可复现评估。

## 计算成本
- YOLOv5l6u（large + 640）跑全程逐帧，CPU 上会很慢（l6u 是大模型）；Fashion-CLIP 逐新 tracklet 出现帧跑一次。全程帧进内存（`read_video` 全量读入）。
- 我们 30min~2h 视频直接套用不现实，但其"检测→stub 缓存→轻量后处理"的解耦结构值得学。

## 对我们层1（持球归属）的借鉴点
1. **关键点最小距离 + containment 两级判定**直接可抄：我们出手窗内球只在筐周 ROI 可见时，containment（球心进球员框）与 12 点最小距的思路对「球出手前最后几帧贴手」的判定比中心距稳。
2. **5 帧滑窗多数票 + 3 帧 grace + last_valid_player 兜底**：一套零成本去抖，与我们持球链「出手前最后持球 tracklet」需要的连续性语义完全对口——grace 兜底正好覆盖球被手遮住的帧。
3. **25 px/帧速度门限剔除野检测 + 线性插值补洞**：可加在我们 HSV 球检出后处理上，把筐周 ROI 的间歇检出串成段。
4. 传球/抢断的「持球人变化 + 队伍同异」纯规则判定，可平移为「持球链切换=出手或传球」的事件切分逻辑。

## 不适用点及原因
- 球检测靠自训 YOLOv5l6u 全场检出，正是我们缺失的环节，且 l6u 在 CPU 不可行；Roboflow 数据集是转播视角，与我们固定机位半场差异大。
- Fashion-CLIP 二分类颜色：我们 10~17 人灰白黑同色系，颜色线已证伪。
- 无 tracklet→人合并（层2 我们自己做）；无量化指标可参考调参。
