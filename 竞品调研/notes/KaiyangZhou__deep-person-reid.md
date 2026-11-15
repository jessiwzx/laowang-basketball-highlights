# KaiyangZhou/deep-person-reid（torchreid）
一句话定位：学术系 person re-ID 模型库（OSNet 作者官方实现），训练/评测为主业、特征提取为副业，不是跟踪库 ｜ stars ≈4.9k ｜ **MIT License**（LICENSE，Copyright 2018 Kaiyang Zhou）｜ 最后提交 2026-01（README 维护中）

## 与我们管线相关的 API 面
- 无 tracker、无检测，只提供「crop 图 → 512 维外观向量」这一件事，正好补我们三件套的第三件。
- 核心入口 `FeatureExtractor`（`torchreid/utils/feature_extractor.py`）：`extractor = FeatureExtractor(model_name='osnet_x1_0', model_path='xxx.pth.tar', image_size=(256,128), device='cpu')`，直接吃 numpy(H,W,C) 数组列表（即我们的检测框 crop），返回 (B,512) 张量（feature_extractor.py:109-152 `__call__`，B 个 crop 一次前向）。
- 内置预处理链（feature_extractor.py:90-96）：`Resize((256,128)) → ToTensor → Normalize(mean=[0.485,0.456,0.406], std=[0.229,0.224,0.225])`——**ImageNet 均值方差**，导 onnx 后必须原样复刻这两步，否则特征全废。导出时文档示例打印 output 2048 维是抄来的注释（resnet 系），osnet 实际 512（osnet.py:297 `feature_dim=512`）。
- eval 模式下 forward 直接返回特征向量不进分类头（osnet.py:422-434），所以导 onnx 得到的就是 embedding 模型。

## 内置 reid 模型清单（docs/MODEL_ZOO.md）
| 型号 | 参数量 | GFLOPs | Market1501 R1(mAP) | 跨域代表成绩 |
|---|---|---|---|---|
| osnet_x1_0 | 2.2M | 0.98 | 94.2 (82.6) | MS→M 66.6 |
| osnet_x0_75 | 1.3M | 0.57 | 93.7 (81.2) | — |
| osnet_x0_5 | 0.6M | 0.27 | 92.5 (79.8) | MS→M 64.3 |
| osnet_x0_25 | 0.2M | 0.08 | 91.2 (75.0) | MS→M 59.9 |
| osnet_ibn_x1_0 | 2.2M | 0.98 | — | 跨域专用 66.5 |
| osnet_ain_x1_0 | 2.2M | 0.98 | — | 跨域最优：MS+D+C→M 73.3 (45.8) |

- 权重获取：**Google Drive 链接**散在 MODEL_ZOO.md 表格里（国内直连不可达）；代码内 `init_pretrained_weights`（osnet.py:440 起）用 gdown 自动下载 ImageNet 底模到 `TORCH_HOME/checkpoints`。**好消息：全系列权重也官方镜像在 HuggingFace `kaiyangzhou/osnet`**（README.rst:33），国内可走 hf-mirror.com。reid 微调权重需手动传 `model_path`，不自动下载。
- 距离度量：MODEL_ZOO 各表标明 osnet 用 `euclidean`、ain 系用 `cosine`——聚类的距离选型跟着权重走。

## CPU 可行性（无 repo 内 benchmark，以下为推断）
- 仓库不含任何 CPU 耗时数字。按 GFLOPs 推断：osnet_x0_25（0.08 GFLOPs/张）在 onnxruntime CPU 单张约 3~8ms，osnet_x1_0（0.98 GFLOPs/张）约 15~35ms/张；批处理（一帧 10-17 人一批）摊薄更好。我们的用法是「tracklet 抽样帧提特征」（每 tracklet 抽 5-15 帧，不是每帧每人），一场 30min 假设 200 个 tracklet × 10 帧 = 2000 张，osnet_x1_0 约 30-70s、x0_25 约 6-15s，完全可接受。**此为推断，接入后需实测**。

## 接入方式建议
- **不要整库安装**。`pip install torchreid` 要编译 Cython 扩展（setup.py ext_modules rank_cylib，Windows 需 MSVC），且 `import torchreid` 连带引入 metrics/engine/数据集全家桶；requirements 里还有 tb-nightly、gdown、opencv 等无关重依赖（requirements.txt）。
- 最小侵入路径两条：
  1. **抽单文件**：`torchreid/models/osnet.py`（598 行）只依赖 torch，零其他 import，拷出来 + 手写 20 行预处理（resize+归一化）即可用 pth 权重提特征；
  2. **官方 ONNX 导出**：`tools/export.py` 现成支持 onnx(opset12+dynamic batch+onnx-simplifier)/OpenVINO/TFLite（export.py:47-98），导一次 onnx 后我们管线只需 numpy+onnxruntime，torch 都不用留。推荐 2，OpenVINO 在 Intel CPU 上还能再提速。

## 风险与坑
- **权重下载是最大坑**：Google Drive 直链国内不可达、gdown 经常被限流；务必用 HF 镜像 `kaiyangzhou/osnet`，下载后校验文件名与 MODEL_ZOO 对应（pth.tar 含 state_dict+meta 字典，load 时有 key 兼容处理）。
- **小 crop 上采样**：训练分布是 Market1501 类 128~256px 高的清晰行人图；我们 100~300px 高球员 crop 到 256x128 时宽度常需 1.5~3x 上采样，<100px 高的框特征质量会明显退化。缓解：只对「面积居中段」的帧抽样提特征、tracklet 内多帧取均值/中值向量（顺带抗遮挡模糊）。
- **域差距**：野球场便装灰白黑同色系与任何 reid 训练集都不同分布，R1 94% 不代表我们聚类能分开 17 个黑衣人；osnet_ain/ibn 系（instance normalization，为跨域设计）值得与 osnet_x1_0 同场 A/B。聚类阈值需用我们 3 场人工归人真值标定，不能照抄论文距离。
- license MIT 干净，无 AGPL 传染，商用/内嵌无义务；`pretrained=True` 会触发 gdown 联网下载底模——离线环境务必显式传 model_path 关掉。
