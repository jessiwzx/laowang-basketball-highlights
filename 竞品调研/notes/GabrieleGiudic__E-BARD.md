# GabrieleGiudic/E-BARD
BARD 篮球动作识别数据集的多任务扩展（检测/队色/号码），含 YOLOv8n 与 RF-DETR 基线及 Qwen2.5-VL 统一微调｜★6｜Python/JSON 数据管线｜2026-03-09（KDD/学术工作配套仓库）

## 数据集资产
- 源素材：NBA 2024-25 转播片段（BARD 数据集），**不是野球/固定机位**。
- **检测（对 我们最有价值）**：1,800 帧、22,210 个框级标注，4 类 = basketball / hoop / player / referee；YOLO+COCO 双格式；HF `GabrieleGiudici/E-BARD-detection`（CC-BY-4.0，需 git lfs）。
- **号码 JNR**：3,633 个 16 帧 player tracklet（ByteTrack 跑出），pose 增强裁剪后 983 个上半身 stub；HF `E-BARD-JerseyNumberRecognition`。
- **队色归属**：15,295 条主球衣颜色标注，8 个主色类；HF `E-BARD-TeamAttribution`。
- 目标分类：2,210 张裁剪目标图 4 选 1。
- `EBQwen_dataset/`：ShareGPT 格式 conversations JSON（grounding/classification/OCR 多任务），用于 Qwen2.5-VL-3B SFT（模型在 HF `EBQwen2.5-VL-3B`）。

## 检测基线实测（detection/src/yolo/train.out 尾部 + eval/yolo_vs_detr.out）
YOLOv8n @704、50 epochs、batch32、mosaic1.0+copy_paste0.5，180 测试帧：
- 总体 P 0.916 / R 0.838 / mAP50 0.895 / mAP50-95 0.647
- **basketball 类：P 0.802 / R 0.513 / mAP50 0.68 / mAP50-95 0.333**
- player P 0.93 / R 0.938 / mAP50 0.973；hoop mAP50 0.993；referee mAP50 0.961
- RF-DETR-NANO（detection/src/rfdetr/train_rfdetr.out，50 epochs）：eval 里小目标 F1@50 = **0.0**（球多为中等目标，中等目标 F1@50 0.78）
- **核心信号：即便是 NBA 高清转播 + 专门训练的检测器，球的帧级 Recall 也只有 ~51%**——「全场每帧可靠检出球」在学术基线里就不成立；所有下游（JNR tracklet、动作识别）都不依赖逐帧球检出。

## 球员跟踪与身份维持
JNR 模块用 ByteTrack 出 tracklet（16 帧粒度），身份=号码 OCR（VLM），无 ReID embedding 聚类。对我们无号码场景不适用，但「tracklet 粒度做身份任务」的组织方式与我们层2 相同。

## 球检测方法
YOLOv8n（704，50ep）/ RF-DETR-NANO（DINOv2-windowed small encoder，resolution 704）双基线；还测了 Qwen2.5-VL 3B/7B grounding（detection/eval/grounding_*.out）。

## 计算成本
- YOLOv8n @704 CPU 单帧可行但慢（eval 里 GPU 4.4ms/帧，CPU 估 80~150ms/帧）；RF-DETR-NANO 更重（GPU 训练 17GB 显存）。
- 数据集本体在 HF、需 lfs 拉取，体积未标（1,800 帧图像+标注，估计几百 MB 级）。

## 对我们层1（持球归属）的借鉴点
1. **最重要的不是模型而是那个数字**：训练检测器球 Recall≈0.51 → 「扩球检测到全域」路线有公开可引的天花板证据；持球推断必须设计成容忍球间歇丢失（与 HanaFEKI 的插值+grace 思路互证）。
2. 4 类里 hoop 有独立类且 mAP50=0.993——若走「扩检测」路线，hoop+player 用 YOLO、球仅筐周用 HSV 的混合方案是折中：其数据可作 hoop/player 微调素材。
3. 检测数据（1,800 帧 NBA 转播）与 JNR tracklet 可作为我们评估集的「风格反面」参照：转播视角、有号码、双队服色，与我们场景正交——说明通用篮球检测数据集不能直接解决我们野球场景。

## 不适用点及原因
- NBA 转播视角/球员尺度与固定机位半场差异大，直接用其权重在我们视频上预期掉点严重；license CC-BY-4.0 可用但需署名。
- 队色 8 分类、号码 OCR 任务定义对我们（便装、无号码）整体不适用。
- 无 possession/持球标注，帮不上持球链。
