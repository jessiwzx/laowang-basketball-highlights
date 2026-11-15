# mikel-brostrom/boxmot
一句话定位：「零件超市」型可插拔跟踪库——9 个 AABB tracker + 2 个 OBB + 自带 ReID 编码器工厂 + CMC，组件可拆用 ｜ stars ≈8.3k ｜ **AGPL-3.0**（LICENSE）｜ v25.0.0（2026-09-09，活跃维护）

## 与我们管线相关的 API 面
- **检测进/轨迹出极简**（README Minimal Usage）：`tracker = ByteTrack(); tracks = tracker.update(dets, frame)`，dets=N×6 `np.array([x1,y1,x2,y2,conf,class_id])`，frame=BGR uint8 图，返回 M×8 `(x1,y1,x2,y2,track_id,conf,class_id,det_idx)`（docs/python/index.md「Tracker factory」节；det_idx=-1 表示纯预测传播框）。
- 新版还有结构化 API：`create_tracker(TrackerSpec(name="bytetrack", options=(("track_thresh",0.55),)))`，进出 `Detections/Tracks` torch 张量，可带 `float32[N,D]` embeddings（boxmot/structures/）。
- **reid 可插拔/可关闭**（docs/python/index.md「Live embeddings」节）：reid 型 tracker（BotSort/StrongSort/DeepOcSort/HybridSort/BoostTrack/OccluBoost）构造参数 `reid_model=`（注入自建后端，只需暴露 `get_features(boxes, img)`，boxmot/reid/backends/base_backend.py:94）或 `reid_weights=`；不传 frame/不传 embeddings 就不提特征；botsort 可 `use_embeddings=False` 纯运动跑。**ByteTrack/OcSort/SFsORT 本来就无 reid**（boxmot/trackers/box/bytetrack/tracker.py 构造器里没有任何外观件）——分层用法天然支持。
- **单独用它的 reid 提特征也行**：`create_reid_encoder(ReIDEncoderSpec(backend="onnx"/"pytorch"/"openvino", artifact="osnet_x0_25_msmt17.pt"))` → `encode(frames, detections)` 返回每帧 `[N,512]` 张量（boxmot/reid/protocols.py:26），即「只买 embedding 零件不买 tracker」。
- **自带 CMC**：boxmot/motion/cmc/ 有 orb/ecc/sift/sof 四种相机运动补偿，BoT-SORT 默认 `use_cmc=True`（trackers/box/botsort/native.py:23）——与我们 ORB 配准同源思路，镜头被碰场景可白捡。
- 支持实验性 `variable_dt=True` 秒级时间步（VFR 视频友好，我们踩过 VFR 坑，普通按帧计数模式下时间戳只是元数据）。

## 内置 tracker 清单（boxmot/trackers/box/，README 基准表）
MOT17 ablation HOTA / **SportsMOT val HOTA**（后者离我们场景最近）：
- occluboost 71.10 / **83.17**（全表最高）；hybridsort 67.31 / **81.14**；strongsort 68.05 / 79.80；deepocsort 67.95 / 79.51；boosttrack 69.25 / 76.32；botsort 69.68 / 76.93；
- **纯运动**：bytetrack 67.68 / **67.93**；ocsort 66.44 / 76.34；sfsort 62.65 / 75.73；另有 sam2mot。
关键读数：**SportsMOT 上纯运动 tracker 明显掉队（bytetrack 67.9 vs 带 reid 的 76-83）**——运动员快速交错变向时外观证据对 ID 保持贡献大，支持我们「运动跟踪+外观聚类」分层但提示聚类层不可省。

## 内置 reid 模型清单（boxmot/reid/core/catalog.py）
- 直接镜像 torchreid 全家权重（**同一批 Google Drive ID**）：osnet_x1_0/x0_75/x0_5/x0_25 × {market1501,duke,msmt17}、osnet_ibn/ain_x1_0_msmt17、resnet50 系、mobilenetv2 系、hacnn、mlfn；另有 **lmbn_n 三权重托管在 GitHub Releases**（v21.0.0，国内可直连）。默认 `osnet_x0_25_msmt17.pt`（boxmot/reid/core/runtime.py:31）。
- 首次引用自动经 gdown 下载（依赖表 gdown<6）；Google Drive 直链在国内基本不可用——osnet 系需改走 HF 镜像 kaiyangzhou/osnet 手动放置，或用 lmbn_n（GH Releases 可达）。
- 输入 256×128（boxmot/reid/adapters.py:50），预处理 default=裸 resize，可选 resize_pad（保纵横比+ImageNet 均值填充，reid/core/preprocessing.py）；自带 ONNX/OpenVINO/TFLite/TensorRT/CoreML 导出器（boxmot/reid/exporters/backends/）。

## CPU 可行性（repo 无 CPU 耗时数字，以下为推断）
- 安装即支持纯 CPU torch：`uv pip install "boxmot[cpu]" --torch-backend=cpu`（docs/getting-started/installation.md）。
- 推断：bytetrack/ocsort 纯 numpy+Kalman+lap，10-17 目标每帧 <1ms；CMC(ORB, scale0.15) 每帧约 5-15ms；reid 才是瓶颈——osnet_x0_25 批 15 crop 约 50-120ms/帧（CPU），若逐帧对每人提特征 30fps 视频只能 0.2-0.5× 实时。**所以 reid 应按 tracklet 抽样帧离线提（我们本来的设计），或隔 N 帧提**——boxmot 的 lazy embedding（只在缺 embeddings 且给 frame 时提）正好兼容隔帧喂法。

## 接入方式建议
- **推荐整库 pip 装、当零件箱用**（AGPL 对个人本地工具无实际约束）：`ByteTrack()` 纯运动起步 → `BoT-SORT(use_cmc=True, use_embeddings=False)` 对照 → 离线聚类用自己的代码调 `create_reid_encoder` 提特征。我们已有球检测/事件层，boxmot 恰好只补 person-track-embedding 三件且边界干净（numpy 进 numpy 出）。
- 若嫌依赖重（torch/timm/pyarrow/pandas 全家）：抽 trackers/box/bytetrack 单目录（依赖 numpy/lapx/scipy 级）+ 独立走 onnxruntime 提 osnet 特征，代码量也可控。

## 风险与坑
- **AGPL-3.0**：个人本地自用零义务；一旦对外分发软件或提供网络服务（哪怕是内部 SaaS），链接/调用了 boxmot 的整条程序需开源。若我们管线将来商用分发，需换 MIT 件（torchreid 的 osnet 是 MIT）或抽算法重写——ByteTrack/OCSORT 算法本身无 license 传染，only 这份实现 AGPL。
- v24/v25 是大重构版（structured API/parquet/native C++），网上旧教程（boxmot 10.x 的 `from boxmot import ...` 用法）与现 API 有出入，以 docs/python/index.md 为准；Python 3.10-3.13。
- 权重自动下载走 Google Drive 会静默卡死（国内），须预下载放 `~/.cache/boxmot`（或显式传权重路径）。
- reid 预处理 default 是**裸 resize 到 256×128（拉伸变形）**，与 torchreid 官方 resize 一致但不保纵横比；crop 很窄时 resize_pad 更稳——两个库此处口径不同，A/B 时要统一。
- benchmark 全是 GPU+强检测器+MOT17/SportsMOT 分布，野球场低角度拥挤遮挡下 ID 保持会显著低于表值。
