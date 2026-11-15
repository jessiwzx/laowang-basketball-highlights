# abhinine4/Soccerplayer_Reidentification
一句话定位：SoccerNet-v3 ReID 挑战赛参赛方案——「外观流 + OpenPose 部位流」双流 Compact Bilinear Pooling 融合（arXiv:2310.14469），主打同队同球衣难样本 ｜ ★2 ｜ Python/PyTorch（Torchreid 改造）｜ 最后 push 2024-01

## re-ID 方法
- 也是 Torchreid 骨架（`benchmarks/baseline/main.py`），在其上新增三个文件：`torchreid/models/our_part_model.py`（CPM 姿态子网）、`torchreid/models/our_bipoollayer.py`（Compact Bilinear Pooling）、`torchreid/models/our_resnet.py`。
- **双流结构**：
  - A. 外观流：ResNet-50（ImageNet/Zoo 分类预训练），整人框输入；
  - B. 部位流：**OpenPose 的 CPM 子网**（COCO 姿态预训练权重 pose_iter_440000），不用部位标注，取中间卷积层输出做「部位空间特征」（`our_part_model.py` 的 `pose1` conv → 128 维特征图）；
  - C. 融合：Compact Bilinear Pooling（外积的低维近似，`our_bipoollayer.py`）让外观特征与部位位置特征交互；
  - 另有 layer-wise similarity：取 ResNet 较浅层特征（低层形状/边缘）与深层特征共同参与相似度。
- **核心动机（README 原话）**：同队球员外观极相似时，**re-ID 图像时间间隔≈0，姿态/位置线索可以当判别特征**——即外观不够用时用体型/姿态补。
- **crop 构造**：整人框（同 Torchreid 256×128），无手工部位切割；部位信息由 CPM 特征图隐式承载。
- **聚类/匹配**：无聚类，query→gallery 检索，euclidean/cosine（`default_config.py` L102-103，默认 euclidean 不归一化），标准 RandomIdentitySampler（**没有** sportsreid 那种层级同队采样）。

## 跟踪与 ID 碎片处理
无跟踪器。数据集是「action frame vs replay frame」跨机位检索协议，不涉及 tracklet。

## 对队服/号码/颜色的依赖程度（关键）
- 无号码 OCR；不依赖具体颜色。
- 明确面向「同队球衣几乎一样、外观特征不足」的场景，但解法是**加姿态/体型通道**而非纯外观，且效果增益有限（见下）。对我们的启示是「同色系靠外观单通道不够」这一判断被他们独立验证过。

## 实验指标与测试集
- SoccerNet-v3 ReID，仅用 10% 训练集时：本方案 mAP 63.7 / rank-1 52.8，**OSNet 61.6/51.2，ResNet50-fc512 46.7/32.8**；
- 仅用 2% 训练集时：**OSNet 反超**（55.5 vs 55.0）。
- 结论：双流部位方案比 OSNet 只赢 ~2 mAP，且训练不充分时被反超——增益与复杂度不成正比。

## 计算成本与部署
- ResNet-50(24.6M) + CPM 子网 + 双线性池化，显著重于 OSNet(2.2M)；CPU 推理不友好。
- 代码是 Colab 硬编码路径（`our_part_model.py` 里 caffe 权重绝对路径 /content/drive/...），工程质量差，不可直接复用。

## 对我们方案的借鉴点
- **层1 归属**：无（无跟踪/持球）。
- **层2 聚类**：
  1. 最重要的不是他的模型，而是他的**问题定性**：「外观特征不足以区分同装不同人时，引入第二通道（姿态/体型）」——我们 17 人灰白黑同色系下，纯外观 embedding 大概率也到天花板，**体型/身高先验（固定机位像素高度稳定）是免费的第二通道**，比训练双流网络便宜得多。
  2. 反面教训：部位流方案实现重、增益小（+2 mAP）、数据少时还退化——**不要走「训练融合网络」路线**，用简单特征拼接/双条件（外观相似 AND 体型相近）即可。
  3. layer-wise 思路提示：若用 CNN 提特征，浅层（形状轮廓）可能比深层（语义）在同装场景更判别——可作为 embedding 选型时的消融维度。

## 不适用点及原因
- 跨机位（action frame→replay frame）检索协议与我们单机位 tracklet 合并不同域；
- CPM 姿态权重下载/加载工程负担大，且其部位特征是「空间位置敏感」的——固定机位下我们反而可以直接利用像素位置/高度，不需要 CNN 部位特征；
- 精度上不比 OSNet 明显强，无「必用」理由。
