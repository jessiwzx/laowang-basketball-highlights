# griftt/ball-yolo
一句话定位：个人实验工作台（非产品），自训 YOLO 检测 basketball+rim+person，规则区域法判进球，ffmpeg 剪辑 ｜ stars: 3 ｜ Python（ultralytics/OpenCV/torch）｜ 最后提交 2026-07-02（API updated 2026-08-24）

背景：作者自用 Mac M3 Pro（MPS/CoreML），主应用在另一个 HighlightMoment 仓库（iOS？CoreML 导出），本仓库是模型侧实验台。大量 /Users/grifftwu 绝对路径、best.py~best7.0.py 版本迭代式脚本，工程质量低但演进过程完整可读。

## 进球判定技术路线

### YOLO 配置
- 版本演进：yolo11n → yolo11s → yolo11l → yolo26s（Ultralytics 新版），最终主力 yolo26s；训练脚本 best_train_yolo26s.py / best_train_yolo26s_person.py（3 类版）。
- 权重来源：**完全自训**。COCO 预训练权重做基底，自有球场视频抽帧标注微调，50 epochs、imgsz=640、batch=8、device=mps、mosaic=1.0+close_mosaic=10、hsv_s 降到 0.4（注释：0.7 会把橙球变红破坏颜色特征）。单 epoch 约 2min15s（125 iter）。
- 训练数据：自家球场录像（`/Users/grifftwu/Desktop/历史篮球/` 各日期目录）用 datacut.py / datacut_multi.py 交互式抽帧（空格暂停→A/D 逐帧→S 存图），LabelImg/Roboflow 标注。WORKFLOW.md 建议规模：最少 200-300 张、推荐 500-1000 张。类别：0=basketball, 1=rim, 2=person（person 用 auto_annotate_person.py 以预训练 yolo26s 自动补标，conf>0.25，跳过已有人标）。
- 推理配置（best7.0.py）：FRAME_SKIP=3（grab 跳帧不解码），imgsz=640 整帧缩放（宽缩到 640），conf 统一 0.1 低阈值后按类过滤（ball/rim 均 0.4）；check*.py 系列诊断工具用 imgsz=1024，注释明言「1024 或 1280 能显著提升远处篮筐的检测率」。

### 判定逻辑（三阶段区域法，纯 2D 几何+时序）
best7.0.py `_check_goal_with_dynamic_rim`：
1. 每帧同时检测球+筐，取置信度最高的 rim（**动态筐**，防机位移——与我们「偶尔被碰移位」同问题，他们用逐帧重检解决）。
2. 以 rim 框为基准划三个固定像素偏移区：高位区（筐上沿-150px 以上）、触框区（筐框±10px）、进球区（筐下方 x±30px、y+100px）。
3. 球心进高位区或触框区 → 刷新 last_interaction_ts；球心进进球区 且距上次交互 0.05s~1.2s（SHOT_WINDOW）→ 进球。触发后 3s 冷却（SHOT_COOLDOWN）防重复。
check_shoot.py / checkosnet.py 改为归一化坐标+筐宽比例（TARGET_ZONE_BELOW_HEIGHT=0.08 等）+ 前 30 帧筐位置均值校准（校准后固定），交互判据=球心到筐心距离<0.20。
对比文档《进球检测方案对比.md》自己承认局限：依赖固定区域、可能误判快速传球、无法区分空心/擦板；列了轨迹预测/状态机/端到端动作识别（SlowFast/X3D）/多特征融合/篮网晃动检测/时序注意力 6 个改进方向——与我们「单目 2D 特征穷尽后想上 CNN」的思路同构，但他们停留在文档未实施。

### 误报控制
仅靠：3s 冷却 + 交互时间窗 + 类别置信度阈值。无下落校验、无实体再现校验、无镜头配准——规则强度明显低于我们的 v11。

## 出手人/球员识别
checkosnet.py（YOLO+DINOv2+特征池，最完整的一版）：
- 射手回溯：进球前 0.5~2.5s 窗口，IoU>0.3 简易跟踪建 track；球心距某人「胸口点」（框顶部+25% 高）<0.15（归一化）则该人 score+1；score≥3 且最高者锁定为射手，取其面积最大帧为最佳帧。
- 归人：最佳帧裁**躯干**（高 15%~75%、宽收 10%，避开头/灯光/地板），DINOv2-small CLS token L2 归一化特征；PlayerDatabase 每人存 5 个角度特征（feature bank），新特征与池内任一历史特征余弦>0.90 即同人，否则新建 Player_X。注释明确记录调参经验：「DINOv2 向量非常聚集，不同人相似度也可能 0.8+；总是一个人就调高，总是分出新人就调低」。
- check_shoot.py 是同一射手逻辑无 ReID 的版本。另有 gemini_check.py（名字骗人，实为逐帧调试播放器，Gemini 辅助写成）。

## 工程亮点
- ClipWorker 线程 + queue：检测主线程把 ffmpeg `-ss` 前置 + stream copy 剪辑任务丢队列异步执行，不阻塞推理。
- 散热保护：跑 600s 休 60s（Mac 无风扇场景）。
- KeyboardInterrupt 断点续跑：打印恢复用 start 参数。
- psutil 内存监控进 tqdm 描述。
- exportml.py 导出 CoreML（Neural Engine）供主应用用。

## 声称的指标或测试
README/文档无任何 precision/recall 数字，无测试。WORKFLOW.md 只有训练日志示例。

## 对我们项目的可借鉴点
1. **「动态筐」逐帧重检替代固定标定**（best7.0.py）：我们场景「偶尔被碰移位」目前靠 ORB 配准补救；YOLO 逐帧出筐框（取最高置信度+时序平滑）是另一条更简单的路，可只在 ORB 判定漂移时触发重检。
2. **imgsz=1024 显著提升小筐检出**的实测结论（check.py 注释）：我们筐仅 35-65px，若走 YOLO 必须 1024+ 推理或 ROI 二次推理，640 整帧会把筐缩到 20-40px。
3. **自训数据规模极小即可工作**（200-1000 张自标帧，50 epochs，Mac 上 2h）：我们的场景固定机位+单一球场，同分布数据更集中，标 500-1000 帧即可训出 ball/rim 检测器，成本可控。
4. **躯干裁剪 + DINOv2 特征池归人**（checkosnet.py）：与我们「全人工归人」痛点直接相关。DINOv2 是外观特征（比 HSV 颜色聚类强得多的形态/纹理），特征池多角度+0.90 阈值+「一人多档」思路可试。但注意我们的致命约束：球场人群灰白黑同色系——DINOv2 外观特征在同色系下大概率同样挤在 0.8+，作者自己的注释就说明不同人相似度也 0.8+，需实测。
5. 持球积分回溯（score≥3 锁射手）比我们的「轨迹回溯找出手点再人工」多了自动持有人判定一步，可作为我们归人 UI 的预填建议（人只纠错不从头选）。
6. hsv_s=0.4 防橙球变红的增强细节，若我们自训时直接抄。

## 明确不适用的点及原因
1. 其进球判定规则（三区域+冷却）比我们 v11 弱得多（无下落校验/再现校验），对我们查准 78-83% 的基线无提升价值——弹筐/涮框 FP 他们根本没处理。
2. Mac MPS/CoreML 管线不适用（我们是 Windows，CPU 情况未知）。
3. FRAME_SKIP=3 固定跳帧按帧号取模，VFR 视频下时间不均匀（我们已踩过 VFR 坑，他们没处理时间戳，直接 frame_idx/fps）。
4. 无指标、无测试集，无法评估其真实效果，只能当思路参考。
