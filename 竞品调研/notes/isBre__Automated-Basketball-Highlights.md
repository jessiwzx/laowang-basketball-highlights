# isBre/Automated-Basketball-Highlights-with-Deep-Learning

硕士课程项目（Bologna AI 专业）：YOLO 检筐 → 筐心 128×128 crop → ResNet50 逐帧二分类 point/no-point → 1D 峰检测把置信度曲线转成进球事件 ｜ ★20 ｜ Python + 单个大 notebook ｜ 最后提交 2024-10（2025 有整理）

## 进球判定技术路线（对我们最重要的仓库）

**输入构造的三阶段演化（notebook `Davide Brescia - ML4CV...ipynb` markdown cell 36/43/50/76/111）：**

- Part 1：整帧 1280×720 直接二分类 → 明确失败，原话 "the images are too large and the models do not know where to look"，放弃评测。
- Part 2/3：**筐周边 crop**。`src/inference.py` 的 `pipeline()`：每帧先跑 YOLO 检筐，取 bbox 中心，`crop_frame = converted_frame[y-64:y+64, x-64:x+64]` 得固定 128×128，过 ResNet50（fc→Linear(.,1)+Sigmoid）输出该帧 point 概率。crop 尺寸固定、不随筐大小缩放、不带上采样。
- 事件化：`pipeline.py` 用 `scipy.signal.find_peaks(frame_confidence, height=0.5, distance=3*fps)`——峰=一次进球；`distance`=3 秒防同一球多计；crop 异常（bbox 太小触发 RuntimeError）catch 后置信度置 0。

**训练数据：**

- Small-Frames：430 张整帧（215 point / 215 no-point 平衡），从比赛片段人工手选；point=球在筐内或明显撑变形网，no-point=球近但不在筐内。
- Cropped（主训练集）：6863 张 128×128（**644 point / 6219 no-point，约 1:10 不平衡**）——YOLO 自动裁剪 Small-Frames + 另外 ~7000 张「precisely classified」截图。70/15/15 切分。
- BasketLocalization：430 张人工标筐 bbox，训 YOLOv5s（`train_detector.py`：现版 yolo11n，epochs=50, imgsz=640, batch=8）。

**模型与指标：**

- 单帧 crop 测试集：Baseline F1 0.64；ResNet50 F1 0.718；**ResNet50 微调 F1 0.834 / acc 0.954**（主用模型）。
- 20 分钟真实节：TP12 FP8 FN2 → **P=0.60 R=0.86 F1=0.71**。
- 整场 2h（换球馆+推拉镜头）：451 检出中 305 个是热身投篮，剔除后 **P=0.44 R=0.97 F1=0.60**（TP64 FP82 FN2）。
- FP 归因（作者人工看片）：透视上球「靠近」筐（后场长传/掷界外球时球长时间停在筐附近）+ missed shot 撑网变形——**与我们 FP 谱系（弹筐/涮框/网变形/同色物）几乎同源**。
- FN 归因：三分球速快、穿网帧数少、网变形幅度小。
- 推理速度：2h 1280×720 GPU 上 >1 小时（作者自列为最大缺点）。

## 出手人/球员识别

无。

## 工程亮点

- find_peaks 把逐帧置信度转事件，`distance` 参数即「最短事件间隔」先验，天然去重，一行代替轨迹链。
- 已知系统性误报源（热身时段）直接时间剔除而非模型硬扛。
- 三模型（baseline/ResNet/ResNet-FT）同管线对比，试验记录完整可复现。

## 对我们项目的可借鉴点

1. 痛点①：**整帧不行→筐 crop 行**的量化证据链，直接支撑我们「穿越窗帧 crop + 小 CNN」方案；连放弃理由（模型不知看哪）都与我们预设一致。
2. **逐帧分类 + 1D 峰检测**是比 3D CNN 轻得多的时序化方案：我们的 CNN 对穿越窗逐帧出概率后，可用 find_peaks(height, distance) 替代/叠加现有多帧判据，参数可解释。
3. **期望管理**：转播画质+筐大+平衡测试集 F1 0.834，落到野球长视频 P 掉到 0.44——CNN 端到端并未解决「球近筐/网变形」类 FP（与我们 98 FP 同类）。启示：crop-CNN 应定位为**对我们现有 98 个 FP 的仲裁器**（二道门），而非替换规则版；我们规则版 P 0.78~0.83 已高于他们端到端的 0.44~0.60。
4. 训练数据构造可抄：正类=球在筐内或明显变形网的帧，负类刻意收「球近筐但不在筐内」的难负样本（我们可从 98 个 FP 帧直接构造难负类，1:10 不平衡他们没做加权也能训，我们可试 BCE pos_weight）。
5. 痛点②：无帮助。

## 明确不适用的点及原因

- 转播画质、多机位、筐在画面中占比大（128 固定窗够用）；我们筐 35~65px，直接照抄 128 窗会塞进大量背景——需按筐宽定窗（如筐宽 3~4 倍）并上采样。
- 25fps CFR；我们 VFR（distance=3s×fps 的 fps 来源要小心）。
- 逐帧全扫 GPU 一小时起步；我们只在规则触发的穿越窗跑 CNN，预算逻辑不同。
- 他们 YOLO 逐帧检筐是为移动机位设计的；我们固定机位+用户标定，筐位置已知，crop 可直接由标定+镜头配准得到。
