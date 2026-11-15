# rustyneuron01/Real-Time-Football-Detection
"Score Vision" 足球比赛状态识别分布式推理/验证服务（Bittensor 子网 44 的 miner+validator）｜ ★73 ｜ Python ｜ 最后提交 2026-03-13

## 数据集
无自有数据；推理端点收视频 URL 下载后处理。权重托管在 HF `tmoklc/scorevisionv1`（`miner/scripts/download_models.py`）：football-player-detection.pt / football-ball-detection.pt / football-pitch-detection.pt 三个 YOLO。

## 跟踪管线（实际代码 vs README 宣称严重不符）
- **README 宣称**：YOLO+HRNet+OSNet+ByteTrack+FastReID 全家桶。**grep 全仓证实：HRNet/OSNet/FastReID 只出现在 `README.md` 和 `RESUME_PROJECT.md`（求职简历文档）里，零代码实现。**
- **实际 miner 管线**（`miner/endpoints/soccer.py`）：YOLO pitch 模型出球场关键点 + YOLO player 模型（imgsz=1280）出人框 → `sv.ByteTrack()`（Roboflow supervision 库）逐帧 `update_with_detections` → 输出逐帧 {id, bbox, class_id} JSON。就这些，无 ReID、无跨 ID 合并、无 embedding 落盘。
- **TeamClassifier 死代码**（`miner/sports/common/team.py`）：SigLIP-base(`google/siglip-base-patch16-224`) 平均池化 last_hidden_state 出 embedding → UMAP 降到 3 维 → KMeans(k=2) 分两队。定义完整但**无任何调用点**（grep 证实），系从 Roboflow 足球教程拷贝。
- **validator 验证层**（`validator/evaluation/`）：抽帧用 CLIP/VLM 校验 bbox 语义 + 关键点稳定性/重投影打分，合成 GS-HOTA=√(检测×关联)，不重跑全量推理。

## 密集交错人群的 ID 维持效果
**仓库内没有任何 HOTA/MOTA/IDF1 实测数字**（README 只有指标定义）；唯一量化是延迟/吞吐基准。

## 计算成本
`min_compute.yaml` 白纸黑字：**CPU-only 0.08 fps（每帧 12.5 秒，"not_recommended"）**、Apple Silicon 3.49 fps（开发用）、RTX3060 12GB 22.36 fps（生产档）。miner 最低要求 4 核/8GB + 推荐 RTX3060。

## 对我们的借鉴点
- **SigLIP+UMAP+KMeans 聚类人裁块的配方**：正是我们「外观 embedding 聚类 tracklet」的形状——但他们 k=2 分队、我们 k≈17 分人；SigLIP 在 CPU 上偏重，参考配方结构（嵌入器+降维+聚类）即可，嵌入器选轻量的（OSNet 级）更符合我们 CPU 预算。
- **「轻量验证」哲学**：不重跑全量推理，抽样帧用更强模型/人核校验质量——与我们「诚实闪烁+人工兜底」的 UI 哲学同构，可作为抽查采样的实现参考。
- **反面教材**：README 技术栈注水（简历驱动开发）——竞品调研必须 grep 代码验证宣称，本仓就是活例。

## 不适用点及原因
- CPU 0.08 fps：整条管线 CPU 不可用，与我们 Windows+CPU 推理优先直接冲突（他们靠 GPU miner 集群）。
- Bittensor miner/validator、FastAPI 服务化、视频下载分发等基础设施与我们单机批处理完全无关。
- 无 ID 维持实测数字，跟踪层可学的东西实际只有 sv.ByteTrack 的极简接法（supervision 库确实是我们 CPU 场景下比 ultralytics 更轻的一个备选）。
