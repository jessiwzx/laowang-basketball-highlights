# stephanj/basketballVideoAnalysis

教程/调研枢纽仓库（非成品系统）：球场线检测、Mask R-CNN 人体分割、球员球衣颜色分类、单应映射 2D 迷你图四个独立教程模块 + 极详尽的 wiki 调研笔记 ｜ ★327 ｜ Python notebook ｜ Apache-2.0 ｜ 活跃期 2019-12~2020（未 clone，基于 README/wiki/子目录 README 网页调研）

## 进球判定技术路线

**无实现。** 仓库五个目录（color-detection / court-detection / homography-mapping / mask-rcnn / mini-map-tutorial）均与进球无关。wiki「Goal (Score)」一节只是愿景：单一模型不够，需多信号 ensemble/stacking——候选信号列了音频峰值（进球欢呼）、球跟踪进入环口、记分牌 OCR 分数递增；并引用 Rembert Daems 的开源 shot detection 项目（检测出手+单目估球速/出手角，用「球轨迹被物体打断」丢弃遮挡帧）。也就是说高星经典仓库在「进球判定」上是空白，只留下了「多弱信号融合」的方向论。

（wiki 另引 Simone Francia 的动作识别工作：CNN 分类篮球动作，其论文结论认为 RNN+LSTM+人体姿态会更好。）

## 出手人/球员识别

**颜色分队伍路线**（color-detection 目录）：Mask R-CNN（COCO 预训练，`mask_rcnn_video.py`，单帧 1.2s）分割前景人体 → 对每个分割人像统计 RGB 主色占比 → 输出 CSV（team,red,green,blue,percentage）。README 自认 ToDo 未竟：「Classify players based on the colors percentage so we can group them in either team A or B」。

**wiki 引用的球员识别论文结论（Learning to Track and Identify Players from Broadcast Sports Videos）**：人脸识别因分辨率不可行；**球衣号码 OCR「poor results」**（形变球衣+阈值化找不到数字+正面视角罕见）——与我们球衣 OCR 证伪结论一致；该论文改用 CRF 融合脸/号码/肤色/发色等全身弱特征做球员实体识别，达到 precision 97% / recall 74%（论文数字，非本仓库实现）。另有 AlphaPose 姿态估计当动作分类积木的设想。

## 工程亮点

- wiki 是一份高质量文献综述：球场检测（HSV+形态学+Canny+Hough → 结论是业余球场线太乱检测不动 → 转自编码器分割，需 10 万帧标注+旋转±15° 增强）、球员追踪三方案对比（结论：遮挡仍是灾难）、PTZ 相机两点标定、Jetson Nano 低成本部署。
- 「自家 7 年数千小时儿子比赛录像（YouTube）= 训练数据」的自采数据观。

## 声称的指标或测试

仓库本身无指标。wiki 转述的论文数字：颜色直方图+逻辑回归分队伍 precision 97%/recall 74%（广播视频）；CRF 球员识别同上数字。Mask R-CNN 每帧 1.2s（CPU）。

## 对我们项目的可借鉴点

1. 痛点②（出手人）：**颜色分人路线的独立证伪旁证**——该仓库在「两队球衣颜色可分」的正规比赛假设下都只做到 ToDo 未完成；我们的球场人群灰白黑同色系更不可行。wiki 引论文的 OCR 证伪原话可直接当我们结论的文献引用。
2. 痛点②替代思路：该体系的正解是「弱特征 CRF 融合」（肤色/发色/位置连续性），对应到我们场景=「出手点空间先验 + 轨迹连续性 + 人工兜底」，我们的 GIF 墙+人工归人在没有号码/双色球衣的野球场可能已是合理终态，不必再追自动方案。
3. 痛点①：wiki「进球=多弱信号 ensemble」方向论对我们有启发——我们已有视觉几何判据，可考虑的低成本第二信号：音频（野球场进球常有叫好声/网声，若录像带音轨）；这与我们「多个弱校验叠加」的 v11 哲学同构。
4. 单应映射（homography-mapping）：固定机位下把出手点/球员位置投到 2D 半场平面，可做「谁站在出手点附近」的空间先验辅助归人——若未来想降低人工归人成本，这是比颜色/OCR 更有物理意义的特征（出手点与人位置强耦合）。
5. 痛点①反面印证：最高星的经典仓库也没做出进球判定——这个细分问题开源界没有现成答案，我们自研路线无捷径可抄。

## 明确不适用的点及原因

- 全部模块假设广播级多机位、球场线完整、两队球衣可分、筐/人大而清晰——我们固定机位野球、无清晰球场线、灰白黑同色系、35~65px 小筐，四个假设全不成立。
- 它是教程集合不是管线，无端到端可用代码。
- 记分牌 OCR 信号：我们自制视频无记分牌。
