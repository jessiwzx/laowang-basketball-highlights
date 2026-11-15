# yi-cheng-liu/basketball_registration
YOLOv5 球员检测 + ResNet18 场地关键点热图 → 单应变换，把转播视频画成鸟瞰战术板（KaliCalib 复现）｜★13｜Python（PyTorch+OpenCV）｜2022-12 前后（密歇根大学 EECS 课程项目）

> 注：clone 因带宽未完成，本文基于 raw.githubusercontent.com 直读 `bird_eye_video.py`、`model_resnet.py` 与 README。
> **关键澄清：这里的 "registration"=「场地注册」（球场单应/相机标定），不是球员身份注册**——仓库与 re-ID/身份维持无关。

## 持球/possession 逻辑（重点）
**无**。全管线不检测球（YOLO 3 类 = home/away/referee，`bird_eye_video.py` colormap 0/1/2）、无 possession、无事件。产出仅为三类视频：鸟瞰战术板 / 单应叠加原图 / 原图。

## 球员跟踪与身份维持
**无跟踪器、无 track_id、无身份**。`bird_eye_video.py` 主循环逐帧独立跑 YOLOv5（改造过的 `yolov5.detect.run` 返回检测框坐标+类别），把每帧所有检测点经单应投到战术板上画圆点；README 标题里的 "Player Tracking" 实际只是鸟瞰图上保留 `FRAME_OF_MOTION=15` 帧的历史轨迹尾迹（`players_position_of_frames` 滑动列表），帧与帧之间的点没有任何对应关系——同一个人这帧的点和下帧的点并不关联。

## 球检测方法
无球检测（见上）。3 类模型为 NBA 2K19 游戏截图自标注（Roboflow 数据集训练，仓库未附权重链接正文）。

## 场地注册细节（本仓库真正的内容，`bird_eye_video.py` + `model_resnet.py`）
1. **关键点检测**：`makeModel()` = ResNet18（BasicBlock [2,2,2,2]）+ layer3/layer4 各嵌一个 Non-Local 块（`NLBlockND` embedded gaussian）+ dilation [1,2,2] + 3 层 ConvTranspose2d 反卷积头（跳连 encoder 特征但 `.detach()`）→ **94 通道热图**（softmax over channels + clamp 1e-4），输入 960×540，热图 1/4 分辨率（坐标 ×4 还原）。94 通道 ≈ KaliCalib 场地点集（`getFieldPoints`，deepsport_utilities）+ 辅助通道。
2. **取点**：逐像素跨通道 argmax（保证一点只属一个关键点）∩ 置信 >**0.82** 的 mask，`cv2.moments` 质心。
3. **求单应**：≥4 对点才 `cv2.findHomography(RANSAC, reprojThreshold=50, maxIters=2000)`；RANSAC 内点再送 `compute_camera_model`（KaliCalib 式 P 分解得完整相机模型）。
4. **三级兜底**（与我们 fail-open 哲学同构）：热图阈值过滤 → RANSAC 剔外点 → `checkProjection` 合理性检查（画面 1/4 与 3/4 两点反投影到地面的距离必须在 100~1800 之间，否则弃用）→ 全挂时退回默认 `MEAN_H` 相机（"Default calib"）。
5. **半场判定**：检测到的最大关键点索引 >85 判右半场（`is_leftfield`），因为关键点集按左半场编码。
6. **投影画板**：场地 2800×1500 模板（`drawQuarterField` 画边线/罚球区/三分圆弧），warpPerspective 或逐点 `M@p` 投影球员落点（YOLO 框坐标直接当落点用，无脚点修正）。

## 实验指标
无量化指标。README 只有 NBA 2K19 与真实比赛两组定性结果（Original/Homography/Board 三行截图/GIF）。

## 计算成本
- 隔帧处理（主循环 `range(video_length/2)` 实际逐帧 read 但只跑前半段），输出 10 fps。
- ResNet18+反卷积 显式 CPU（`getModel` 里 device=cpu 写死）；每帧 YOLOv5 + ResNet18 两次前向。
- 工程糙点：每帧 `cv2.imwrite('./tmp.jpg')` 落盘再喂 yolov5.detect.run（文件接口），I/O 浪费；`drawTemplateFigure` 每帧重读重画地板纹理。

## 对我们层1（持球归属）的借鉴点
1. **无】直接可抄的持球逻辑（它没有）；但「固定机位只标定一次」的思路可用：我们是固定机位半场，单应可以离线标一次（甚至手点 4+ 对应点即可，不需要它的网络），把所有 tracklet 位置投到球场真实坐标——层2 聚类可加「球场坐标先验」（常在篮下的人/常在弧顶的人），层1 的距离特征也从像素变成尺度一致的米制（远处球员像素距离小的假近问题消失）。
2. 三级兜底链（阈值→RANSAC→合理性检查→默认值）是我们做任何几何估计（单应、出手点投影）时值得照抄的鲁棒模式。
3. 反面印证：**每帧独立检测、无身份维持的方案连"同一个人的点"都连不起来**——这正是我们层2（appearance 聚类归并 tracklet）必要性的外部佐证；它只画战术板展示，做不了任何"每人只标一次"的下游统计。

## 不适用点及原因
- 无球检测、无持球、无身份——与层1 核心问题零交集，只有上面的间接借鉴。
- 训练数据是 NBA 2K19 游戏渲染截图（域差极大），且转播移动机位假设（每帧重估单应）；我们固定机位用不着逐帧网络推理。
- 球员落点直接用 YOLO 框坐标（无脚点/脚踝修正），投影精度对我们可参考但不精确；街球场线条不标准（KaliCalib 的 93 点场模型对不上），真要做单应得改用手动点对应。
