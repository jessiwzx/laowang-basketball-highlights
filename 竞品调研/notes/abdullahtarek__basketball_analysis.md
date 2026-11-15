# abdullahtarek/basketball_analysis
一句话定位：教程级 NBA 分析全家桶——YOLO 检测 + ByteTrack 追踪 + Fashion-CLIP 零样本分队 + 持球链/传球/抢断检测 + 单应战术图 + 速度距离（无投篮判定）｜ ★185 ｜ Python（模块化包结构）｜ 最后更新 2025-05-06

## 球员检测/追踪/身份维持（重点：tracker/embedding/ID 维持细节，附代码引用）

- **YOLO + supervision.ByteTrack**（`trackers/player_tracker.py` L22 `sv.ByteTrack()`，L71 `update_with_detections`）：batch=20、conf=0.5 逐帧检测，只保留 Player 类。**无任何外观 Re-ID embedding**——身份纯靠 ByteTrack 的运动+IoU 关联。
- 球员检测模型是 Roboflow 数据集自训的（`training_notebooks/basketball_player_detection_training.ipynb`：yolov5l6u、100 epochs、640px；README 说可换 v11）。**没有 ID 切换的检测或修复机制**——ID 断了就断，下游靠"周期性重分类"兜底（见下）。
- 球不追踪（`trackers/ball_tracker.py`）：每帧取**置信度最高的 Ball 框**（L66-77），单目标假设。两个后处理很有参考价值：①`remove_wrong_detections`（L86-120）——与"上一个好框"距离超过 25px×帧间距的检测判为假阳性删除，链式推进；②`interpolate_ball_positions`（L122-139）——pandas 线性插值+回填补漏检空洞。
- **stub 缓存体系**（`utils/stubs_utils.py`）：所有中间结果（track/分队/关键点）pickle 落盘，重跑直接读——调试迭代成本低的好工程习惯（与我们的 sidecar json 哲学一致）。

## 归组/team assignment 方法（重点：非颜色特征有哪些）

- `team_assigner/team_assigner.py`：**Fashion-CLIP（patrickjohncyh/fashion-clip）零样本图文匹配**，文本 prompt 默认 `"white shirt"` vs `"dark blue shirt"`（L23-24），整框 crop 过 CLIP 取 argmax（L57-75）。本质仍是**颜色/外观语义**，只是把"取 HSV 聚类"换成"时尚域 CLIP 的语言锚点"。
- **无 embedding 聚类、无投影约束参与归组**。唯一的时序稳定手段：每球员分类一次缓存进 `player_team_dict`，且**每 50 帧强制清空重分类**（L126-127 `if frame_num %50 ==0: self.player_team_dict = {}`）——这是对 ID 漂移/换人上场的隐式容错：ID 变了或人换了，最多 50 帧后自动修正。妙处在于它不修 tracker，而是让下游属性"短期可信、定期刷新"。
- 对我们：CLIP 时尚域 prompt（"white shirt"/"dark blue shirt"）在灰白黑人群里依然会塌（语义锚点区分不开同色系），但"**属性缓存+周期性重估**"和"**每 track 一次而非每帧一次**"的省钱思路通用。

## 进球/投篮事件检测（若有）

**无。** 全管线没有 made/miss、没有出手检测、没有球轨迹-筐口几何。最接近的是传球/抢断事件（见下）。README Future Work 只提了想加 pose 判规则违例。这支仓库对痛点①零贡献。

## 出手归属逻辑（若有；没有就写「无」）

**没有投篮归属，但有完整"持球链"（possession chain）——这是离我们痛点②最近的可迁移组件**：
- `ball_aquisition/ball_aquisition_detector.py`：逐帧判"谁持球"。判据两档（L122-166）：①球框被球员框**包含 ≥0.8** → 高包含候选；②否则球中心到球员框 12 个关键点（框边投影点+九宫格点，L31-73）的**最小距离 <50px** → 距离候选取最近。**连续 11 帧同一人才确认持球**（L27-28 `min_frames=11`，L208-213），中断即清零。输出每帧一个持球人 ID（或 -1）。
- `pass_and_interception_detector.py`：持球人变化+同队=传球（L35-40）、异队=抢断（L68-73）——**事件=持球链上的转移**这个抽象对我们直接有用：出手=持球链的终点转移（人→空中）。
- 值得注意的疑似 bug：高包含候选里取的是**距离最大**者（L157 `max(...)`）而非最近，逻辑上可疑但作者未修——参考时别照抄这行。

## 工程亮点与计算成本

- **全 CPU 可跑**（无 GPU 要求声明；batch 推理 + Dockerfile；依赖仅 ultralytics/supervision/transformers）。速度靠 stub 缓存而非实时性；无 FPS 数字。
- 模块化干净：trackers/team_assigner/ball_aquisition/pass_detector/tactical_view/speed 各自独立类 + drawers 纯渲染，main.py 只做编排（`main.py` L38-161）——**读代码像读管线图**，教学仓库的架构范本。
- 战术图：18 个场地关键点（yolov8x-pose 自训 500 epochs）+ `cv2.findHomography` + 关键点比例校验（`tactical_view_converter/tactical_view_converter.py` `validate_keypoints`——用关键点间距比例检验检测合法性再算 H），投影脚点得米制坐标算速度距离。
- 数据集全部来自 Roboflow 公开集（ball: `basketball-players-fy4c2-vfsuv` yolov5l6u 250ep；court: `reloc2-den7l` yolov8x-pose 500ep）。

## 声称的指标

README **无任何量化指标**，只有 YouTube demo 视频。训练 notebook 里也不展示最终 mAP 结论。185 星来自教程传播而非效果验证。

## 对我们项目的可借鉴点（结合痛点②重点说）

1. **持球链是"自动归人"的正确中间表示**：我们要的"出手人"在数学上就是**出手时刻之前最后一个持球人**。这套 `containment≥0.8 → 12 关键点最小距离<50px → 连续 11 帧确认` 的三段判据（`ball_aquisition_detector.py` L27-29）不依赖颜色/号码/队服，**纯几何**，可直接嫁接到我们的语料：用 YOLO 人框 + 我们已有的橙球 HSV 检测（替换他的球检测器）即可复现。出手点回溯我们已有，把"出手前最后持球人"接到出手点上，就完成了 出手→人 的自动链路。
2. **"连续 N 帧才确认"的去抖结构**与我们 v11 状态机同哲学，持球这种高频抖动信号尤其需要；N=11 帧在 30fps≈0.37s，可作起点。
3. **球的链式距离门限+线性插值**两个后处理（`ball_tracker.py`）几乎是我们轨迹链的朴素版，可对照补漏检：他们敢对漏检直接插值，因为下游（持球）只吃 50px 粒度。
4. **周期性重分类容错 ID 漂移**：与其追求 tracker 永不断 ID，不如让下游属性定期刷新——对我们"自动归人+人工抽检"的架构，等价物是"每次出手事件独立归人、跨事件用出现位置/轨迹连续性弱关联"。
5. ByteTrack 本身（无外观、纯运动关联）在我们的固定机位场景反而可能是优点：机位不动则运动预测可靠，且无需 GPU 训练 Re-ID。

## 明确不适用的点及原因

1. **分队=CLIP 颜色 prompt**：与我们颜色已证伪同因，灰白黑同色系无语言锚点可分；且我们问题是 10+ 个体身份而非 2 队。
2. **单球假设+全图最高置信球框**：野球多人周围可能有多个橙色类物（球、黄色衣服、反光），无空间先验的 global argmax 会串球；我们的检测已带搜索区约束，比它强。
3. **18 关键点全场单应→米制速度**：半场+35-65px 小筐+机位偶被碰移，关键点检测与 H 稳定性都不足；我们也不需要速度距离产品功能。
4. **25px/帧球速上限**是 1080p NBA 转播标定的常数，我们 VFR+低分辨率下球的帧间位移会超，直接照搬会误删真检测（我们的链判据已有自适应版本）。
5. 无投篮/进球检测，痛点①无贡献；NBA 演示视频里球员大、清晰、遮挡少，ByteTrack 裸跑即可——我们 720p 远机位小人+密集卡位，ID 切换频率会远高于其演示设定，须实测。
