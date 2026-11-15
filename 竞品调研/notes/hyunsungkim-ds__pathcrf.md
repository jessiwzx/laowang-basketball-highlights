# hyunsungkim-ds/pathcrf
KDD 2026：只用球员轨迹（无球）推断「持球路径」做足球 on-ball 事件检测，Set Attention backbone + 动态掩码 CRF + Viterbi｜★24｜Python（PyTorch）｜2026-02（KAIST+Fitogether，arXiv 2602.12080）

> 注：clone 因带宽未完成，本文基于 raw.githubusercontent.com 直读 `datatools/preprocess.py`、`datatools/config.py`、`datatools/utils.py`、README 与 arXiv HTML 全文。仓库自带 5 个训练好的 checkpoint（saved/100 Non-CRF ~ saved/140 Dynamic Masked CRF）与教程 `tutorial.ipynb`。

## 持球/possession 逻辑（重点）
**问题形式化**（论文 §2.1）：22 球员 + 4 个「出界」节点 = 26 节点全连接有向图；每个时间步的持球状态 = 恰好一条边：自环 (u,u)=u 在控球，有向边 (u,v)=球在 u→v 飞行途中。676 个候选边里逐帧选一，选出的边序列即 possession path；相邻步边变化即事件（control/kick/out-of-play）。
**骨干**（§2.2）：ROLAND 式两层 socio-temporal 编码器——social 模块 = PPE（队内 ISAB）+FPE（全场 ISAB）拼接（Ball Radar），temporal 模块 = PE+堆叠 SAB（TranSPORTmer）；粗层中间监督（sender/receiver logits 辅助损失）；边嵌入 = MLP([h_u; h_v])。
**动态掩码 CRF**（§2.3）：emission = MLP_emit(z_e,t)；transition 不用静态矩阵，= MLP_trans([z_e',t-1; z_e,t]) 动态算；非法转移 mask 成 −10^4。允许转移集：id（状态保持）/kick（自环→出边）/rec（飞行边→接收者自环或一脚出边）/out（→出界自环吸收态）。训练 = CRF 负对数似然 + 两个辅助 CE（λ1/λ2 加权），forward 算 partition；推理 = Viterbi（复杂度 O(T·|A|)，比稠密 CRF 还小）。
**与我们持球链的同构**：我们的「出手前最后持球 tracklet=出手人」就是「进球时刻前最后一个自环节点」——PathCRF 给了这套语义的完整形式化+全局最优解码框架。

## 球员跟踪与身份维持
不做视觉跟踪——输入直接是德甲 Sportec Open DFL **光学跟踪数据**（25Hz 厘米级坐标，`data/sportec/tracking`，kloppy 读取）。身份天然完美（无 ID 切换/漏检）。这是它一切指标的前提。

## 球检测方法
**没有球检测——这正是它的卖点**。球轨迹只用于训练时构造 ground truth：`datatools/preprocess.py` 用 RDP 折线简化球轨迹（config：RDP_MIN_ANGLE=15°）取方向突变点为候选触球点，与 ELASTIC 同步后的人工事件做 Needleman-Wunsch 对齐（`align_event_rdp`：gap −10、repeat −20、score=100×(1−dist/POSS_MAX_DIST=5m)），未匹配拐点在 5m 内归最近球员（前后 5 帧内同队候选优先），出界/进球按场几何归 4 个 out 节点；`merge_tracking_poss` 逐 episode ffill/bfill——**前后不一致的帧留 NaN 不硬填**。推理时模型只见球员 x,y,vx,vy,speed,accel（`utils.calculate_running_features`：>12m/s 人体极限剔离群+插值+Savitzky-Golay 平滑）。

## 实验指标（测试集=1 场 91 episode/1,831 事件）
- Dynamic Masked CRF：边准确率 69.64%（676 选 1）、sender 80.44%/receiver 82.28%、违规率 0%、事件 P 73.18/R 78.37/**F1 75.69**。
- **关键消融**：Non-CRF（逐帧独立 argmax）F1 65.34 且 2.8% 非法转移；同一模型只加**纯约束 Viterbi 解码（VCD）= F1 73.14、0 违规**——约束贡献了 CRF 收益的大部分，学习型转移分数只再 +2.5。
- Ball TP 基线（先回归球轨迹再规则判持球）F1 仅 48.39——「两段式（先补球再归人）」不如直接推断边序列。
- 松弛召回：1s/4m 容差 ~80%，2s/10m 86.1%（多为近失而非全错）→ 论文定位为人工标注的预标注系统。
- 队控球率 62.13% 真 vs 62.64% 预测（帧级 92.40%，误差互相抵消）。
- 训练：5 场 54,683 窗（10s 窗、5FPS 降采样、步长 5 帧）。

## 计算成本
backbone 训练需 GPU；但解码本身（Viterbi over 允许转移集）极轻。对我们：17 人=289 边状态空间 + 规则 emission，CPU 纯 Python/NumPy 即可全片实时级。

## 对我们层1（持球归属）的借鉴点
1. **最重要的数字**：干净光学跟踪输入下边准确率也只有 ~70%——纯轨迹推断持球是本质歧义的；但我们任务更简单（只需进球前最后持球人，不需要每个事件），且有筐周 HSV 球证据可作硬锚点，两者互补。
2. **零训练可抄的 VCD 结论**：独立逐帧打分 + 转移约束 Viterbi ≈ 全 CRF 效果。我们可以：emission 用手写证据（筐周球检出+关键点距离/containment），转移约束写死「持球人只能保持自环或经飞行边切换，球不瞬移」，DP 全局解码——即 HanaFEKI 式规则的序列化升级版，CPU 可行。
3. 允许转移集（id/kick/rec/out）直接翻译成我们的 持有/出手/传球/抢断/出框。
4. ground truth 半自动构造法（RDP 拐点+NW 对齐+就近归人、不一致不填）可参考来扩标我们 3 场人工标注。
5. 另一印证：两段式 Ball TP（先补球轨迹再归人）显著劣于直接边序列推断——支持我们「不执着补全球轨迹，直接推持球状态」。

## 不适用点及原因
- **输入质量前提不成立**：光学跟踪 vs 我们的 YOLO+ByteTrack（断裂/漏检/ID 切换/像素坐标）；其 69.64% 是干净输入的数字，直接搬模型到我们 tracklet 上只会更差。
- 需要逐帧 possession 真值训练（5 场 54k 窗）；我们只有 3 场出手级标注，量级差 3 个数量级，学习型 emission/transition 学不出来。
- 足球场景假设：两队 22 人、边界出球常见；我们半场野球无队别、球基本不出框，out 节点可换成「筐/篮板吸收态」。
- 学习型 backbone（ISAB/SAB 堆叠）在 CPU + 30min~2h 视频上不划算——但其思想可用规则 emission 替代（见借鉴点 2）。
