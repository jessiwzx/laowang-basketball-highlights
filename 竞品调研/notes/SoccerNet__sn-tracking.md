# SoccerNet/sn-tracking
SoccerNet Tracking 竞赛官方 dev kit（数据下载 + ByteTrack/DeepSORT baseline + TrackEval 评测）｜ ★110 ｜ Python ｜ 最后提交 2023-07-07

## 数据集
- **规模**：12 场完整职业足球主相机视频；200 段 30 秒带跟踪标注片段 + 1 个完整半场带标注 + 全场视频。竞赛切分：train 57 段 / test 49 段 / challenge 58 段（`tools/SNMOT-test.txt`、`tools/SNMOT-challenge.txt`）。
- **标注类别**：players、goalkeepers、referees、ball 及任何进入场地的人——**评测不分类**，全算一类；主相机转播（持续平移变焦）。
- **获取**：`pip install SoccerNet` 后 `downloadDataTask(task="tracking")` 直接下，**无需申请**（`tools/download_data.py`）。
- 格式：MOT20 兼容 csv（帧号,ID,x,y,w,h,conf,-1,-1,-1）；主指标 HOTA（拆 DetA/AssA）。

## 跟踪管线
- **ByteTrack baseline**（`Benchmarks/ByteTrack/`）：官方 ByteTrack 仓库 + YOLOX-X MOT20 预训练权重，替换 `yolox_x_soccernet_no_gt.py`/`demo_track_no_gt.py` 适配数据；README 明示 test set 期望 **HOTA 71.5**。无 GMC、无 ReID。
- **DeepSORT baseline**（`Benchmarks/DeepSORT/`）：PaddleDetection 框架 + PCB-Pyramid-R101 ReID，喂 GT 检测框只做关联，test set **HOTA 69.55**。
- **FairMOT**（`Benchmarks/FairMOT/README.md`）：仅一句"参照 DeepSORT 配置"，GT 框推理不可用——占位。
- 评测：`tools/evaluate_soccernet_v3_tracking.py` 封装 TrackEval，`tools/zip_gt.py` 打包 GT，本地一键复现榜单分。

## 密集交错人群的 ID 维持效果（README 榜单）
- **2023（检测+关联）**：冠军 Kalisteo HOTA 75.61（DetA 75.38 / AssA 75.94）；MTIOT 69.54；MOT4MOT 66.27；官方 baseline 42.38。
- **2022（GT 检测纯关联）**：Kalisteo 93.64（DetA 99.56 / AssA 88.06）、CB-IUO 93.25、tactica 93.17。
- **教训读数**：给完美检测后 AssA 也只到 ~88（HOTA 93.6 是 DetA≈100 撑的）；自己检测时 AssA 掉到 ~76——**职业转播足球（两套球衣、场上 22 人）的关联天花板也就 IDF1 级 80 多**，密集交错时掉 10+ 分是常态。冠军线关键改进仓库内未披露（榜单只列分数），但 2022 前十普遍 AssA 79-88，说明纯关联仍有 ~10 分由遮挡交错贡献的损失。

## 计算成本
两个 baseline 都要求 CUDA+CUDNN（README Prerequisite），YOLOX-X/PCB-R101 均为 GPU 档；仓库未给 CPU 数字。评测本身 CPU 秒级。

## 对我们的借鉴点
- **评测脚手架照抄**：`evaluate_soccernet_v3_tracking.py` + seqmap 文件 + zip 提交的三件套，是把「我们 3 场人工真值」变成可回归评测的最短路径——只需把 SNMOT-seqmap 换成我们场次的段名。
- **两阶段评测哲学**：先固定检测（喂 GT 框）单测关联，再端到端——归因干净，我们调 track 参数时同样该锁检测单测 AssA。
- **差距拆解习惯**：DetA/AssA 分开报，一眼看出瓶颈在检测还是关联（所有上榜队都是 AssA 拖后腿）。

## 不适用点及原因
- 纯足球：球衣二色（队簇先验强）、场上人数固定 22+3，我们篮球 10~17 人便装同色系无队簇先验。
- 转播相机持续运动——他们的关联难一半来自相机运动；我们固定机位（偶碰移位）反而更接近「静态 + 突变」，GMC 在他们那是必需品、在我们这是偶发保险。
- 检测目标含裁判/球且不分类；我们只要 person。
- 30 秒短片段，无小时级长时关联考察。
