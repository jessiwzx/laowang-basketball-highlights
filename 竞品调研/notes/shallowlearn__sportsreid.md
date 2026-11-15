# shallowlearn/sportsreid
一句话定位：学术圈最知名的「广播团队运动球员 re-ID」训练/评测框架（Sports Re-ID, arXiv:2206.02373），SoccerNet 2022 ReID 挑战赛测试榜第 2 ｜ ★26 ｜ Python/PyTorch ｜ 最后 push 2022-11（依赖 Python 3.7 时代）

## re-ID 方法
- **框架**：在 Torchreid（deep-person-reid）上改造，叠加 SoccerNet 官方 sn-reid 数据管线。训练入口 `benchmarks/baseline/main.py`，配置 `benchmarks/baseline/default_config.py`。
- **embedding 模型**（README 表格，均在 SoccerNet ReID 训练集上训练）：
  - ResNet50-fc512：24.6M 参数，256×128，mAP 81.8 / rank-1 76.1
  - **OSNet_x1_0：2.2M 参数，256×128，mAP 83.4 / rank-1 78.0**（参数最少、精度第二高，定义在 `torchreid/models/osnet.py`）
  - DeiT-Tiny/Small、ViT-B/L（224×224），最高 ViT-L 89.8/86.7（5 种子权重平均=model soup，`tools/create_model_soups.py`）
- **crop 构造**：整人框直接 Resize 到 256×128（OSNet 系）或 224×224（ViT 系）+ ImageNet 归一化（`torchreid/data/transforms.py` L271-321），**没有身体部位分块**。训练时 ColorJitter 只动亮度/对比度不动饱和度/色调（L300——刻意保留球衣颜色信息）。
- **两个核心创新点**（对应论文）：
  1. **层级采样** `RandomIdentitySampler_Hierarchical`（`torchreid/data/sampler.py`）：每个 batch 优先采样**同队/同场/同联赛**的其他球员身份作为 batch 内负样本——即刻意制造「同款球衣不同人」的难负样本。lookupdict 按 pid→team/year/league 多级组织（`tools/prepare_lookups_for_hierarchical_sampling.py`）。
  2. **中心损失 centroid loss**（`torchreid/losses/hard_mine_triplet_loss.py` L91 起）：triplet 之外，把每个样本到「同类中心」的距离做 hinge、再加「类中心间距」项（cc_loss 取负→最大化类中心距离）——显式压紧类内、推开类间，**对聚类场景友好**（比纯 triplet 的特征空间更适合阈值聚类）。
- **聚类/匹配**：仓库本身**没有聚类代码**，只有 query→gallery 检索（`torchreid/metrics/rank.py`，标准 CMC/mAP），距离用 euclidean 或 cosine（`torchreid/metrics/distance.py`），默认 euclidean + 不归一化（default_config.py L110-111）。
- **tracklet 级特征**：video 引擎支持对整条 tracklet 的帧特征做 avg/max 池化成单向量（`torchreid/engine/video/softmax.py` L105，`cfg.video.pooling_method='avg'`）——tracklet→单 embedding 的最简可行做法。

## 跟踪与 ID 碎片处理
不含跟踪器，纯图像 re-ID 训练/检索框架（上游 SoccerNet 数据本身是检测框+身份标注，ID 切换率等由数据集论文负责，本仓库不管）。对「tracklet 合并」零贡献——只提供「两条 tracklet 的特征像不像」的度量。

## 对队服/号码/颜色的依赖程度（关键）
- **不依赖号码**（无 OCR）。
- **不依赖特定队服颜色**，但**强依赖「球衣款式有差异」**：层级采样说明模型主攻「同队球衣相同、靠体貌/发型/体态区分相邻队球员」——这是它的卖点（同款球衣难负样本）。SoccerNet 转播足球有清晰高分辨率球员特写（广播机位多、常 zoom），我们的固定机位远景 720p 便装人群可分辨纹理远少于转播特写。
- 归纳：它的「同色系不同人」能力有实验支撑（同队负样本训练+榜单指标），但**证据域是职业转播画质**，非我们的域。

## 实验指标与测试集
- SoccerNet-v3 ReID 数据集（`torchreid/data/datasets/image/soccernetv3.py`，广播足球 500+ 场、含 query/gallery 检索协议），评测走 SoccerNet 官方 `evaluate`（`tools/evaluate_soccernetv3_reid.py`）。
- README 表格给出 6 个模型的 mAP/rank-1（OSNet 83.4/78.0）。无「同队 vs 跨队」分层数字（论文里有层级消融，仓库未附）。

## 计算成本与部署
- OSNet 仅 2.2M 参数、输入 256×128，是 re-ID 界著名的 CPU 友好模型；提供 ImageNet 预训练 + SoccerNet 微调两档权重（Google Drive）。ViT-L 303.6M 不考虑。
- requirements 较老（Python 3.7、SoccerNet 包），Windows CPU 推理只需抄 `torchreid/models/osnet.py` + 权重，不必装整个包。

## 对我们方案的借鉴点
- **层1 归属**：无直接帮助（无跟踪/持球逻辑）。
- **层2 聚类**：
  1. **OSNet_x1_0 是首选 embedding 候选**：2.2M 参数 CPU 可跑、且是**专门在「同款球衣不同人」难负样本上训练过**的域适配模型（比通用 ImageNet 模型强、比 CLIP 更轻）。
  2. tracklet→单向量用「帧特征平均池化」直接抄（avg pooling 即可起步）。
  3. centroid loss 的设计哲学（类内紧/类间远）可当作我们选 embedding 时的评估视角：不只看检索 mAP，看**簇可分性**。
  4. 余弦 vs 欧氏：它默认欧氏不归一化，但归一化后余弦等价；对我们更实用的是「L2 归一化 + 余弦」便于统一阈值语义。

## 不适用点及原因
- 训练数据是转播足球特写（高分辨率、多机位），与我们固定单机位远景便装域差距大，直接用其权重零微调效果未知——**必须拿我们的 3 场人工归人真值先验证再信**。
- 无聚类/合并代码：30~80 tracklet → 17 簇的层次聚类、时间重叠 must-not-link 约束都要自己写。
- 代码栈老（Python3.7、旧版依赖），只能当「模型定义+权重」来源，不能当工程依赖。
