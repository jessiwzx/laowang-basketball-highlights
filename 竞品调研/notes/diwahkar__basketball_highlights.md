# diwahkar/basketball_highlights

两个整帧时序模型对 6 秒滑窗做 basket/no_basket 二分类（vanilla 3D CNN 与 SlowFast R50 迁移学习），效果接近失败，属未完成的玩具项目 ｜ 星数极少 ｜ Python/PyTorch ｜ 最后提交 2026-01

## 进球判定技术路线

- **输入构造**（`cnn_3d/dataset.py`、`cnn_3d/highlights_detection.py`）：整段视频上滑窗——窗长 6.0s、步长 1.0s；每窗均匀采 **16 帧**，每帧整帧 resize 到 **112×112（不裁剪、不定位筐）**，堆成 C,T,H,W 张量；ImageNet 归一化。训练增强仅水平翻转；不足 16 帧用边缘帧 pad。
- **训练数据**：`dataset/train`、`dataset/val` 下按 `no_basket`/`basket` 两目录放短视频片段（一个样本=一个视频文件）。仓库不含数据集本身、也无采集/标注脚本——样本量、来源、标注方式全部未说明；训练脚本按类别计数做逆频率 `CrossEntropyLoss(weight=1/count)`，说明类别不平衡。
- **模型**（`cnn_3d/model.py`）：3 层 Conv3d+BN+ReLU+MaxPool（32/64/128 通道）+自适应池化+FC，二分类。`slowfast/model.py`：pytorchvideo `slowfast_r50` 预训练换头（Dropout+Linear）、冻结 BN 的迁移学习版。AdamW lr=1e-3、ReduceLROnPlateau(val_f1)、checkpoint resume 骨架完整。
- **事件化**（`highlights_detection.py`）：滑窗概率曲线上取 **95 分位数当自适应阈值**，连续超阈值段=事件，±3s padding、相邻合并输出剪辑区间。
- 管线编排：纯滑动窗全扫（无检测器、无 crop、无轨迹）。

## 出手人/球员识别

无。

## 工程亮点

- 训练骨架规范：seed 固定、类别加权、按 val_f1 存最优、指标逐 epoch 写 `json/*/metrics.json`、可 resume。
- `fps <= 1` 时 fallback 30fps 的防御（对脏视频文件的兜底，与我们 VFR 处理同款问题意识）。

## 声称的指标或测试

`json/cnn_3d/metrics.json`：50 epoch 后 val_acc≈0.55、val_f1 最高≈0.62、val_recall 低至 0.25——基本没学动。
`json/slowfast/metrics.json`：31 epoch val_acc≈0.65、val_f1≈0.62——预训练救回一点，仍不可用。
README 无任何声明指标。

## 对我们项目的可借鉴点

1. 痛点①：**最有价值的反面教材**——「整帧 112×112 里筐只占几十像素」正是我们场景（筐 35~65px）的极端化；连 SlowFast 预训练时序模型都学不出进球细节。与 isBre 的 Part1 放弃相互印证：**crop 到筐邻域是必要条件，不是优化项**。
2. 时序窗设计参考：他们 6s 窗/16 帧信噪比低（进球关键过程约 1~2s）。我们的穿越窗（数帧）粒度选对了；若试 3D CNN，窗应贴事件本身而非长上下文。
3. 95 分位数自适应阈值 = 「永远输出 top5% 窗口」的逃生门，输出数量与真实进球数脱钩——我们要求准确计数，明确不能学；但它提示了「按场自适应阈值」的思路（我们不同球场光照/筐况不同，绝对阈值可能要按场标定）。
4. 类别加权写法（1/count + CE）可直接用于我们的难负样本构造。
5. 痛点②：无帮助。

## 明确不适用的点及原因

- 整帧输入、滑窗全扫、分位数阈值——三件套整体不可用，指标本身即证明。
- 无检测器/无筐定位，与我们「已标定筐口」的先验完全脱节。
- 数据集不可得（仓库不含），无法复现也无数据构造经验可挖。
