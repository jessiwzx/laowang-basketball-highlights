# reborncd/basketball-highlights
一句话定位：PyQt6(实际代码用 PyQt5) 桌面三页式工具——传统 CV 检测进球 + **全人工**复核归人 + 按球员导出个人集锦 ｜ 1 star ｜ Python ｜ 2026-04-09 创建、04-11 最后提交（仅 1 个 squash 提交，约 7MB 主因是附带 yolo11n.pt 权重）

**重要更正：调研任务描述称该仓库"用了视觉大模型/VLM 路线"，经全库 grep（openai/api_key/mimo/gpt/vlm/base64/http 等）与逐文件通读核实——该仓库没有任何 VLM/LLM/API 调用，是纯传统 CV 路线 + 人工归人。**

## 进球判定技术路线（重点）

核心在 `app/core/detector.py`（738 行），架构与我们的项目惊人相似（像是同题独立解）：

1. **篮筐定位**（`HoopDetector`，detector.py:135）
   - 自动：画面上半部（hoop_y_ratio=0.55 以上）灰度 → 高斯模糊 → Canny(40,120) → 膨胀 → HoughCircles（半径自适应 [w/25, w/5]）。取最大圆，历史 10 次加权滑动平均出"稳定筐"，连续 3 次位置偏移 <30% 才算稳定；前 10 次命中取**中位数**锁定（`_calibrate_hoop`，detector.py:513）。
   - 手动：`manual_hoop_rect` 用户在首帧框选（GUI 里"🎯 标定篮筐"按钮，detection_page.py:250），优先于自动。锁定失败 300 处理帧后提前终止并报 `hoop_not_locked`。
2. **球检测**（`BallTracker`，detector.py:279）
   - HSV inRange 双段红橙区间（H 0-15 / 160-180，S≥100，V≥60）+ 形态学（腐蚀 3x3、膨胀 5x5×2、闭 7x7）→ 轮廓 → 面积≥30、minEnclosingCircle 半径在 [8,60]、圆度打分，**带轨迹近邻加权**（与上一球距离 <100px 加分）选最优。
   - **亮点：自适应光照补偿**（`_adaptive_light_compensation`，detector.py:287）——按全帧平均亮度把 V 通道下限动态调 ±30，应对室内外光变。这是我们 v11 没有的。
   - 可选 `use_yolo=True` 换 YOLO 球检测（`app/core/yolo_detector.py`）：yolo11n(COCO) 的 `sports ball` 类别名解析（COCO 无篮筐类，detect_hoop 恒空）。注意 YOLO 分支**不叠加而是替代** HSV，且替换后 history 直写。
3. **三区域进球判据**（`GoalDetector.process_frame`，detector.py:533）
   - 筐锁定后定义三区：HIGH（筐上方 2.5 倍半宽×150px 高）、RIM（筐矩形外扩 20px）、GOAL（筐下方 150px、横向 2.5 倍半宽）。
   - 判进球 = 球进 GOAL 区 **且**（此前 shot_window=3s 内有过 HIGH/RIM 交互，时间差须 >0.05s）**且** 距上次进球 >goal_cooldown(2s)。交互缺失时走**轨迹回退**（`_trajectory_supports_goal`，detector.py:477）：1s 窗口内 ≥3 点，筐平面上方+侧向限内有点、下方+侧向限内有点、且末点低于首点（净下落）。
   - 与我们的「穿越筐口平面 + 下落校验」几乎同构，但没有实体再现校验、没有镜头位移配准；按区域包含而非平面穿越几何，FP 口径应比我们粗。
4. **可解释性统计**：DetectionStats 记 12 项漏斗计数（高位命中/触框/进球区命中/冷却拦截/无交互拦截/时间窗拦截等）拼成一行中文摘要 `format_detection_summary`（detector.py:111）；失败原因四分类（筐没锁/从未见球/见球无进球模式/取消）。**这个"拦截原因计数"对我们调 FP 档位规则很有借鉴价值。**
5. 剪辑：`app/core/clipper.py` ffmpeg stream copy 裁剪+concat，`extract_thumbnail` 出缩略图。

### VLM 角色
**无。** 全库无任何模型 API、无 VLM、无抽帧送审。任务描述中"约 7MB、VLM 路线"的印象可能来自附带的 yolo11n.pt 与 ultralytics 依赖。

## 出手人/球员识别
**全自动部分：无。** 复核页 `app/gui/review_page.py` 是一套成熟的人工归人 UI：左列表（缩略图+分配状态+确认勾）右预览，一排球员彩色按钮 + 新建球员（QInputDialog 输入"詹姆斯 / 23号"）+ 取消分配，**分配后自动跳到下一个未分配片段**（`_goto_next_unassigned`，review_page.py:268），已分配行染绿底，底部统计"共 N | 已分配 x | 未分配 y"，未分配>0 时禁用下一步。数据模型 `app/models/data.py`（GoalEvent/Player）与 `app/core/project.py`（project.json 持久化、clips/thumbnails/output 目录结构、按球员取片段）。

## 工程亮点
- 三页工作流（检测→复核→导出）+ project.json 断点续作，检测/导出走后台线程不卡 UI。
- 检测参数全暴露在 GUI（跳帧 sample_every_n、前后滚秒数等），手动添加/删除时间戳按钮，首帧框选标定筐。
- 失败原因四分类 + 12 项漏斗统计，"没检测到"能区分是筐没锁还是球没见过。
- 篮筐中位数锁定 + 稳定性判据，比单帧 Hough 稳。
- 缺陷：requirements 写 PyQt6 但全部代码 import PyQt5（装不对跑不起来）；moviepy 声明了未用；仅 1 个提交无历史。

## 声称的指标或测试
无任何评测数字、无真值测试。README 故障排除表只说"检测不到→调参数"。

## 对我们项目的可借鉴点
- 痛点①（进球判定准确率）：
  - **自适应 HSV 光照补偿**（V 通道下限随全帧亮度 ±30）可直接试验嫁接到我们的 inRange 前处理，成本一行级，可能缓解傍晚/逆光时段的球分割漂移。
  - **三区时间交互判据**（HIGH/RIM 交互 + GOAL 出现 + 0.05s~3s 时间窗）与我们穿越平面判据互补，其"触框 RIM 区交互"记录或可作为弹筐/涮框 FP 的一个区分特征（打铁弧线往往 RIM 交互时间模式不同）——但该仓库无真值数据支撑此区分力，只是待验证假设。
  - **拦截原因漏斗计数**：把我们 v11 各否决分支（下落校验拒/实体再现拒/冷却拒…）做成每场一行摘要，调参时肉眼可见哪个分支在吃召回/放 FP，纯工程零风险。
  - 反面印证：一个无真值测试、参数全手调的传统 CV 独立实现，作者自己也停在"检测不到→调参数"——说明我们 88-96%/78-83% + 三场真值集的位置已远超此路线的朴素形态，2D 规则穷尽后确实只剩学习法（我们已设计的 CNN crop 方案）或外部信号（VLM triage）两条路。
- 痛点②（出手人归人）：
  - **这是本仓库对我们最大的价值：它把"全人工归人"做成了最顺滑的形态**——球员按钮一排常驻（我们的墙已有）+ **分配即自动跳下一个未分配**（我们目前是否自动跳待核对，若无需点选下一行则值得抄，归人吞吐直接翻倍）+ 行级绿底着色 + "未分配>0 禁用下一步"的完成度闸门 + project.json 级断点续作。
  - 它验证了「全人工归人 + 好工具」是被独立接受的终态方案（该作者同样没做自动归人），与我们「颜色/OCR 证伪后收敛到人工+动图墙」的决策一致。

## 明确不适用的点及原因
- YOLO11n COCO sports ball 检测：COCO 的 sports ball 主要为棒球/足球/网球分布，篮球小目标（我们筐仅 35-65px，球 ~10px）在 720p 野生机位下检出率可预期极低，且该实现用 bbox 宽/2 当 radius、history 直插无过滤，比我们的 HSV 管线粗糙；仅当备选实验，不值当主力。
- HoughCircles 自动找筐：我们场景筐小、背景观众衣物干扰大，且我们已有用户标定 + ORB 位移配准，该实现无移位恢复能力（碰机会丢锁定）。
- 桌面 PyQt 形态 vs 我们的 Gradio：形态差异无借鉴必要。
