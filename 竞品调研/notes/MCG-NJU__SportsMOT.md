# MCG-NJU/SportsMOT
大型体育 MOT 数据集（篮球/排球/足球）+ 评测工具链，ICCV 2023 ｜ ★227 ｜ Python/Shell ｜ 最后提交 2023-07-24

## 数据集
- **规模**：240 段、150,379 帧（≈15× MOT17）、162.9 万框（≈3× MOT17）、3,401 条轨迹；全部 720p/25fps，源自 MultiSports（NCAA/Premier League/奥运会转播），人工剪成无镜头切换的片段（平均 485 帧）。
- **篮球占比**：三类之一（另有排球、足球）。切分 45 train / 45 val / 150 test（`codes/evaluation/TrackEval/data/ref/seqmaps/`）。
- **篮球子集统计**（README 表格）：平均 845.4 帧/段、10 条 track/段、**track gap len 68.7 帧**（遮挡断档很长）、track 长 767.9、密度 9.1 框/帧——即标准 NBA 转播篮球每段约 10 人、断档密集。
- **标注**：只标场上的球员（明确排除观众/裁判/教练），单类、无号码标注；遮挡时只要有可见身体部位就预测框；躯干一半出画面才跳过；全段 ID 唯一。
- **获取**：OneDrive / 百度网盘（密码 4dnw，README 直链）或 Codalab 竞赛页下载；**CC BY-NC 4.0 非商用许可**。
- 格式：MOT17 标准格式（`gt/gt.txt`、`seqinfo.ini`），1280×720。

## 跟踪管线
- 本仓库本身**不含跟踪器代码**，只有：格式转换 `codes/conversion/mot_to_coco.py` + 完整 TrackEval 评测包 `codes/evaluation/TrackEval/`（`evaluate.sh` 一键出 HOTA/IDF1/AssA/IDSw）。
- 官方 baseline 跟踪器 **MixSort** 在姊妹仓库 MCG-NJU/MixSort：ByteTrack/OC-SORT 外挂一个 MixFormer 式外观关联模块（模板-搜索区互注意力出相似度热图，与 IoU 矩阵 α 加权融合后匈牙利匹配；模板仅在检测框未被遮挡比例达标时更新）。检测器 YOLOX-X（CrowdHuman 80ep → SportsMOT 80ep）。

## 密集交错人群的 ID 维持效果（test set，论文 Tab.3/4/6/7）
- ByteTrack(train+val)：HOTA 64.1 / IDF1 71.4 / IDs 3089；OC-SORT：73.7/74.0；MixSort-OC 最佳 74.1/74.4/IDs 2781。
- **分运动（MixSort-Byte）**：篮球最难 HOTA 60.8 / IDF1 67.8 / **AssA 仅 46.8**；排球 72.5/87.0；足球 66.4/73.6。结论：篮球小场地+身体对抗→遮挡模糊最重，关联是瓶颈（DetA 普遍 78+，AssA 拖后腿）。
- **三个消融金句**：
  1. **Kalman 有害**：IoU-only（用上一帧位置直接算 IoU）HOTA 71.5 vs IoU+KF 预测 64.1——线性匀速假设在变速运动上反而掉 7 分。
  2. **纯外观在篮球上崩溃**：α=0（纯外观）篮球 HOTA 26.9 vs α=1（纯运动）65.9；外观加分篮球仅 +0.3（足球 +1.3、排球 +0.9）——运动越快外观越有用，篮球越密外观越没用。
  3. **在线 ReID 增益微小**：ByteTrack+DeepSORT 式 ReID（SportsMOT 微调）仅 64.1→64.8 HOTA（+0.7）。

## 计算成本
训练重（YOLOX-X 两阶段 160ep + MixFormer 300ep，GPU）；我们只做推理/评测的话 TrackEval 在 CPU 秒级出分。

## 对我们的借鉴点
- **评测复用（最值钱）**：val 45 段含篮球 GT，TrackEval 开箱即用——在我们 3 场人工真值之外给跟踪层一个标准考卷，任何 track_buffer/match_thresh/GMC 消融可直接量化 AssA/IDF1/IDSw。
- **跟踪器调性**：别迷信 Kalman 预测；相邻帧 IoU 直接关联（或 OC-SORT 式观测中心）在快速变速场景更稳——我们 100~300px 小目标 + 便装人群恰好命中「外观弱、运动快」象限。
- **架构背书**：论文证明「在线外观融合」在篮球上收益≈0——我们把外观 embedding 放到**离线 tracklet 聚类**（而非在线 ReID 融合）的方案与该结论一致。
- ID 碎片预期管理：NBA 级篮球 IDF1 天花板 ≈74，我们场景（10~17 人同色系便装）只会更低，tracklet 碎片化是常态，下游按「簇」而非「track ID」设计。

## 不适用点及原因
- 转播机位持续平移变焦 + 25fps，我们是固定机位 30fps——GMC/镜头运动不是主要矛盾。
- 球员有不同号码球衣（「相似但可区分」），我们便装无号码（更接近 DanceTrack 的「不可区分」端），外观类方法在该数据集的天花板不能直接搬给我们。
- 平均 485 帧短片段 vs 我们 30min~2h 单场——小时级 ID 生命周期管理（长时重识别/簇合并）在该数据集无先例。
- 数据 CC BY-NC：可评测/研究，商用发布需注意许可。
