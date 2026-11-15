# nalin1096/DS5500_Player_Tracking_and_Identification_NBA
一句话定位：NBA 转播视频球员跟踪+「识别」的全栈课程项目（Django Web 应用）——身份识别靠 NBA 官方 play-by-play 替换数据+记分牌 OCR 对时，**不含外观 re-ID** ｜ ★6 ｜ Python（Django+dramatiq+AlphaPose）｜ 最后 push 2020-12

## re-ID 方法（严格说：没有 re-ID）
- 管线（`nbaplayertracking/tracking/tasks.py`）：抽帧 → 镜头分段（去广告/特写/回放）→ 团队分类 → 球场点跟踪 → 记分牌 OCR → play-by-play 对时 → 坐标变换 → Kalman 平滑。
- **「识别」的真实实现**（`nbaplayertracking/tracking/utils/play_by_play.py`）：
  1. 抓 stats.nba.com 的 playbyplayv2 + boxscore API，解析换人事件（EVENTMSGTYPE=8），推每节每时刻在场球员名单；
  2. scoreboard OCR（`ocr.py`，easyocr）读比赛时钟/比分 → 把视频帧对到比赛时刻；
  3. 在场名单 + 团队分类 → 帧里的框属于哪队哪些人。**tracklet→姓名的映射完全靠外部元数据，不看外观**。
- **团队分类**（`team_classification.py`）：对每个 bbox 做掩膜，取 RGB 均值+标准差 6 维 → **KMeans(n_clusters=2)**，首帧拟合、后续帧 predict——本质是「两队球衣颜色可分」的强假设。

## 跟踪与 ID 碎片处理
- 检测+跟踪：AlphaPose（检测+姿态）+ PoseFlow（姿态相似度跨帧关联出 track id）（`alphapose/run_alphapose.sh`、`run_poseflow.sh`）。无 ID 切换率数字。
- 轨迹平滑：pykalman KalmanFilter 补断点（`player_smoothing/player_smoothing.py`）。
- 无 tracklet 合并/重识别逻辑——ID 碎片问题未处理。

## 对队服/号码/颜色的依赖程度（关键）
- **重度依赖**：①两队球衣颜色可分（KMeans=2）；②有 NBA 官方 play-by-play（替换事件精确到球员）；③记分牌可 OCR 对时。三者缺一身份即崩。
- 对我们（便装同色系、无号码、无官方数据、无记分牌）**三依赖全不满足**。

## 实验指标与测试集
- 无量化指标：仅 demo 视频（湖人主场 test.mp4）+ 官方 SportVU JSON（`data/0021500440.json`）做对照可视化。README 只描述系统不报告精度。

## 计算成本与部署
- AlphaPose+PoseFlow 重（GPU 时代产物），easyocr 也要 GPU 才实用；Django+dramatiq 全栈工程，CPU 单机不友好。
- 代码路径硬编码（脚本里 /Users/dgrubis/Desktop/...），是课程作业质量。

## 对我们方案的借鉴点
- **层1 归属**：
  1. 「镜头分段」预处理思路（去广告/特写/回放）对转播视频必要，我们固定机位单视频用不上；
  2. Kalman 平滑轨迹/补缺口是通用好件（pykalman 用法可直接抄）；
  3. **球场关键点跟踪+单应变换到球场坐标**（`court_tracking.py` Canny 边缘+多尺度模板匹配找球场线点；`postprocessing/affine_transformation/transformation.py` 仿射到俯视图）：固定机位下半场坐标可作为**强先验喂给层2 聚类**（同一人的 tracklet 在球场坐标系中的活动区域应一致）——这是本仓库对我们唯一有实质价值的部件。
- **层2 聚类**：KMeans(2) 均值色统计是我们已证伪路线的又一例证（他们能成是因为 NBA 两队球衣真的颜色分明）；无外观 embedding 可借鉴。

## 不适用点及原因
- 身份识别全靠官方数据源，我们野球场景不存在等价物；
- 球衣颜色二分假设在我们的灰白黑同色系语料已证伪；
- 转播多机位剪辑流 vs 我们固定机位长视频，域不同。
