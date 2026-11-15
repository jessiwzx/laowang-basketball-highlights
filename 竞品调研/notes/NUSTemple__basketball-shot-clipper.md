# NUSTemple/basketball-shot-clipper
一句话定位：固定机位篮球视频进球检测+剪辑+本地网页标注 UI 的完整产品（我们项目的「平行世界」——同场景同痛点，走到「规则召回优先 + 训练小分类器提准」这一步）｜ stars: 1 ｜ Python（ultralytics/OpenCV/sklearn/Flask，Poetry）｜ 最后提交 2026-09-06

素材：DJI Pocket 3 固定机位，4K 3840x2160 HEVC 59.94fps，14 个室内全场视频；开发机 Apple M3 Max。docs/PLAN.md 有 12 项需求决策表（recall 优先、每视频一次性人工标定筐、人工看一遍记真值做验证——纪律与我们 truth/ 测试集一致）。

## 进球判定技术路线（本组三仓库中最值得精读）

### 第一步：筐标定（无筐检测模型）
- 每视频一次人工拖框（calibrate_hoop.py CLI / Detect 页签浏览器拖框，ffmpeg 抽帧），存 data/configs/<video>.json 的 hoop_bbox_norm。PLAN.md 决策 11：不引入第三方筐检测模型，理由是场地/机位/光线不匹配风险+标定成本极低——与我们「用户标定筐口」相同。

### 第二步：球检测（detect_shots.py + inference.py）
- **权重：COCO 预训练 yolov8l，类别只用 32="sports ball"，零自训**。模型选择有实测依据：远机位下 yolov8m 只在 4.7% 采样帧看到球 vs yolov8l 9.1%，而轨迹规则需要两次目击（筐上+穿筐）才能触发，故默认 l；代价仅 +7% 墙钟时间（因解码占大头）。
- **空间：原生分辨率推理，绝不降采样**。代码注释（detect_shots.py L30-34）：720p 下球经 YOLO 内部 resize 后仅 ~10px 宽，yolov8n/720p 召回率实测 ~1%。PLAN.md 决策 10 原本写 720p，实现时推翻——只保留时间降采样 15fps。
- **ROI 裁剪**：compute_roi() 把推理输入裁到「筐水平 ±(0.6+1.5) 筐宽、筐上不设限、筐下 (1.0+0.5) 筐高」的区域，find_makes() 反正只用这个区域内的球，像素省 8-10 倍。imgsz=ROI 最长边对齐 32 步进（非固定 640）。
- conf=0.1 召回优先，每帧只留最高置信度球（ONNX 后端因此可跳过 NMS）。
- 性能（video_source.py 注释，RTX 4070 + 2688x1512 HEVC）：解码 ~24ms/采样帧 vs YOLO 推理 ~4ms——**检测是解码瓶颈不是推理瓶颈**，故用 ffmpeg 管道 + `-hwaccel cuda`（NVDEC 386fps vs CPU 134fps）并把裁剪放进 ffmpeg，只有 ~600KB/帧的 ROI 过管道；Mac 用 VideoToolbox。批大小 cuda=16/mps=8/cpu 更低。

### 第三步：轨迹几何规则 find_makes()（召回优先）
球心轨迹 (t,cx,cy)：水平在筐 ±0.6 筐宽内；先「筐上目击」（cy<筐顶 且在筐上方 2.5 筐高内）；后「穿筐目击」（筐顶≤cy≤筐下 1.0 筐高）；两目击间隔 ≤0.6s 且 cy 下降；1.5s 冷却；**弹回校验**：穿筐后 0.4s 内球再现筐上方 → 判 rim-bounce 弃权（注释：在全人工核对视频上抓到 3 个已知 FP 中的 2 个，8 个真进球零损失）。时间戳=帧号/源 fps（grab/retrieve 保序）。

### 第四步：训练过滤器提准（train_filter.py，核心亮点）
- 动机与我们的处境一模一样：手调规则在调参视频上 70% 查准，9 视频 307 片段全量只有 34.2%——单视频调参不泛化。
- 两组互补特征：**轨迹特征**（features.py，10 维：gap_sec、vertical_drop/筐高、horiz_offset_above/through、bounced_back、time_to_bounce、n_detections、detection_rate、n_below_after）；**篮网运动特征**（net_motion.py，9 维：筐下窄 ROI 内帧差序列形状 peak/time_to_peak/decay_ratio/impulsiveness + Farneback 光流方向一致性 coherence + 事件前基线窗对照——纯像素运动不需要球检测，~2s/片）。
- 模型：sklearn GradientBoostingClassifier，19 维特征；**留一视频交叉验证**（GroupKFold 按视频分组）；阈值选择=满足 recall≥0.98 前提下查准最大化；特征缓存 CSV；训练/打分同路径无 skew（都从剪出的 clip 文件提特征）。
- 结果（README，307 片段/9 视频）：单独网运动特征≈无信号（34.3%），但与轨迹特征结合后可安全丢弃的 FP 几乎翻倍；部署模型 34.2%→41.7%（丢 60 个候选，105 真进球中 2 个有险）。基线如此低的重要原因是 yolov8m 特征提取时 19% 真进球没有可用轨迹（换 yolov8l 后 81%→100%）。
- 用法哲学：「triage aid, not auto-filter」——分数只用于 Review 界面排序（sort by confidence 先看最可能是真的），不自动删。

### 剪辑（clip_shots.py）
[t-5s, t+2s]，5s 内的多次检测合并（同一物理投篮会产生 2-4 次重检测）；x264 重编码（注释：net_motion 特征必须与训练时同编码）；--min-stars 评分导出、按球员分文件夹导出（文件名带星级后缀让 CapCut 能识别）。

## 出手人/球员识别（docs/PLAYER_IDENTIFICATION.md，写得很诚实）
- **已上线：纯人工**。Review 面板 Scorer/Assist 选人（roster.json 花名册 + 搜索框 + 名字药丸），labels.json 加 scorer 字段——与我们的全人工归人同级。
- **Phase 2b 原型（未上线）：ByteTrack + 躯干 HSV 直方图层次聚类**。结论与我们颜色聚类证伪高度共振：90s 窗口 ~10 人碎成 30 个 track ID；同人碎片相关 0.85-0.97、异人 0.3-0.6（他们的素材便装颜色分明——我们的灰白黑同色系连这点分离度都没有）；纯相关性聚类把多个深色衣的人混成一簇。三条教训：①时间重叠 must-not-link 约束（同帧并存的两个 track 必非同人）；②要上真 re-ID embedding 而非颜色直方图；③与球衣 OCR 互补融合。**均未实施**，理由：先让人工标注积累评估集。
- person_id/suggest.py 只有接口（raise NotImplementedError），设计原则：建议供人确认、绝不自动写入。

## 工程亮点
- 解码/推理分别独立选硬件加速（NVDEC/VideoToolbox vs CUDA/MPS/CPU），各有 env 逃生口。
- ONNX Runtime DirectML 后端（120MB vs 3GB CUDA torch，支持 AMD/Intel），compare-backends 工具对比两后端。
- label UI（Flask）+ 独立 worker 容器跑检测（重启 UI 不杀任务）；任务队列落盘。
- 特征缓存（labels 集或特征集变了自动失效重提）。
- validate.py：检出时间点 vs 人工真值容差匹配，输出 FP/漏检清单。
- Windows Inno Setup 安装包（自动装 Python/Poetry/ffmpeg+下模型），Docker 双容器。

## 声称的指标或测试
- 307 标注片段/9 视频：基线查准 34.2%，过滤器后 41.7%@recall≥98%（2/105 真进球有险）。
- yolov8m→l 轨迹可用率 81%→100%；弹回校验抓 2/3 FP 零漏检损失（单视频）。
- 单元测试 12 个文件（features/net_motion/clip_merge/validate 等纯逻辑）。
- 注意：其查准绝对值远低于我们 v11 的 78-83%（他们无下落校验/实体再现/ORB 等多重校验，且 15fps 采样+单帧最高置信度球的链更稀疏），但其「轨迹规则+网运动」组合拳方向被验证有效。

## 对我们项目的可借鉴点（按两大痛点）
痛点① 进球判定准确率：
1. **篮网运动特征（net_motion.py）是最直接的增量**：我们 98 个 2D 无法分离的 FP 里，弹筐/涮框/打铁弧线类全部伴随「球确实穿过了筐平面附近」的轨迹几何，但**真进球的网会瞬间甩动后衰减（impulsiveness/decay_ratio），弹筐的网运动弱或不规则（flow coherence 低）**。这是与轨迹正交的纯像素证据，实现仅 OpenCV（帧差+Farneback），~2s/候选片段，无需新模型。可作为小 CNN 之外的另一条（或先行的）路径，特征喂进现有 GBM/或做 CNN 前的手工基线。
2. **训练过滤器方法论完整可抄**：留一视频 CV（防单视频过拟合——他们手调规则 70%→34% 的教训）、recall 约束下选阈值、特征 CSV 缓存、训练/打分同路径。我们有 335 事件真值，比他们 307 还多，可直接套这套评估框架（先用 GBM+手工特征做 baseline，再上 CNN 对比）。
3. **弹回校验的参数化写法**（0.4s 内再现筐上=miss）与我们「下落校验」同族，但他们的「time_to_bounce」作为连续特征进分类器的做法更优——我们 v11 是硬规则，可改成特征。
4. **ROI 裁剪+原生分辨率推理**的教训直接适用：我们筐只有 35-65px，任何 YOLO 路线必须原生分辨率+筐周边 ROI，绝不能整帧 640。COCO sports ball 预训练模型零标注可用（他们验证了 yolov8l 在远小目标上 9.1% 帧可见率的水平——对固定机位+轨迹链可能够用，值得我们先零成本试）。
5. 解码瓶颈认知：CPU 场景下 ffmpeg 管道+ROI 裁剪比 OpenCV grab 快数倍。
痛点② 出手人归人：
6. 他们的 Phase 2b 失败复盘证实我们颜色聚类证伪的结论方向正确，且给出两条我们没试过的：**时间重叠 must-not-link 约束**（并置 track 排除，纯逻辑零成本，可加进任何聚类/reid 方案）和 re-ID embedding 替代颜色。但鉴于我们同色系更极端，且他们自己也没做成，继续人工+建议预填是对的。
7. 「建议供人确认、不自动写入」的交互哲学与我们墙+人工一致，互相印证。

## 明确不适用的点及原因
1. 4K HEVC 59.94fps 素质远好于我们 720-1080p 30fps 野球视频：他们球在原生分辨率下可检测的前提是 4K 像素量，我们 720p 下球更小，COCO yolov8l 可见率会显著低于 9.1%，轨迹链更稀疏——不能直接搬「15fps+两次目击」参数。
2. 精度绝对值不可比：41.7% 查准对用户是「标注界面刷误报」体验，我们 78-83% 已远超，借鉴的是特征与评估方法而非其判定器本身。
3. 室内场（光照稳定、网清晰）vs 我们室外野球（红网/人肤干扰）——net_motion 的筐下窄 ROI 在我们场景需验证是否会被路人/背景运动污染（他们 NET_X_MARGIN 只有 0.05 筐宽，特意避伸手球员）。
4. 无 VFR 处理（帧号/fps 直算时间戳），我们 VFR 坑他们没踩。
5. 无镜头位移处理（纯固定三脚架），我们需要 ORB 保留。
