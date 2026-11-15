# roboflow/supervision
一句话定位：检测结果的「标准容器+瑞士军刀」——Detections 统一格式、区域/计数/平滑/切片推理/标注全家桶，跟踪只是顺带且正在剥离 ｜ stars ≈50k ｜ **MIT License**（LICENSE.md，Copyright 2022 Roboflow）｜ 当前 dev 0.31.0.dev0（2026-09-10，活跃）

## 与我们管线相关的 API 面
- **ByteTrack 封装形态**（src/supervision/tracker/byte_tracker/core.py:31）：`sv.ByteTrack()` 构造 → `detections = tracker.update_with_detections(detections)`，输入输出都是 `sv.Detections` 对象，输出多了 `tracker_id` 字段——「检测进、带 id 检测出」的纯函数式，零 numpy 手拼。构造参数即调参面：`track_activation_threshold=0.25 / lost_track_buffer=30 / minimum_matching_threshold=0.8 / frame_rate=30 / minimum_consecutive_frames=1`。
- **重大变化：sv.ByteTrack 已废弃**（0.28.0 起，0.31.0 移除，docs/deprecated.md:10）——官方引导迁移到外部 `trackers` 包（`pip install trackers`，PyPI），方法名 `update()`。本 checkout 版本仍带完整实现+deprecation 警告；**用 `supervision<0.31` 锁版本可继续用**，新项目官方口径是装 `trackers` 包。
- `sv.Detections`（src/supervision/detection/core.py:116）：标准容器（xyxy/confidence/class_id/tracker_id/mask/data dict），带 `from_ultralytics/from_yolov8/from_transformers/from_inference` 等一堆适配器——若检测用 ultralytics YOLO，衔接是零胶水一行式。
- **reid/embedding 组件：确认没有**（全 src 树 grep 无 reid/embedding 实现，唯一命中是 labelme 格式解析的无关词）。外观这一件它不供。

## 除 tracker 外对我们有用的件
- `sv.DetectionsSmoother`（detection/tools/smoother.py:28）：按 tracker_id 滑窗平均 xyxy 的**抖动平滑**——注意是平滑不是补漏检（缺检帧只记 None 占位，不回填轨迹）。
- `sv.match_detections`（detection/utils/matching.py:131，2026-09 新公开原语）：两组 Detections 贪心 IoU 一对一匹配，返回配对索引+两侧未匹配索引——**可直接用于我们的 tracklet 端点接驳**（跨遮挡缺口把 tracklet A 尾与 tracklet B 头 IoU 配对）。
- `sv.InferenceSlicer`（detection/tools/inference_slicer.py:105）：SAHI 式切片推理，小目标（我们 100~300px 球员在 1080p 里偏小）可提召回，CPU 检测变慢为代价。
- `sv.PolygonZone/LineZone`：场内区域过滤/越线计数；annotators 全家（Trace/Box/Label/Halo/Ellipse）画轨迹调试图一行级；`CSVSink/JSONSink` 落盘；`sv.process_video` 帧循环骨架。

## CPU 可行性
- 依赖极轻（pyproject dependencies）：numpy/scipy/pillow/matplotlib/requests/tqdm/pyyaml/av/defusedxml/pydeprecate——**无 torch、无强制 opencv**（src/supervision/_cv2/ 是自带的 OpenCV 兼容 shim，按需后端加载）。ByteTrack 本体是纯 numpy+Kalman+贪心匹配，CPU 单帧 10-17 目标毫秒级（推断，repo 无 benchmark 数字）。
- 它不含任何模型推理，CPU 压力全在检测器一侧，与 tracker 选型无关。

## 接入方式建议
- 定位是**胶水层**：`Detections` 容器 + `match_detections` + smoother + annotator 值得拿；tracker 件则看时间点——现在用须锁 `supervision<0.31`，否则直接用外部 `trackers` 包或 boxmot 的 ByteTrack（后者参数面等价且无废弃风险）。
- 若走 boxmot 路线，supervision 可完全不装（boxmot 直接吃 numpy N×6，不需要 Detections 容器）；只在想要它的标注/区域/匹配工具时按需引入，二者不冲突（都 MIT/AGPL 各自干净边界——注意 supervision MIT、boxmot AGPL 混用时要分开看义务）。

## 风险与坑
- **ByteTrack 废弃中**是最大坑：0.31.0 起删掉；锁版本是短期方案，长期依赖它的 tracker 会被动迁移到 `trackers` 包（新包成熟度需另行考察）。
- `DetectionsSmoother` 需 tracker_id 才工作、不支持分割，且是平滑非插值——「补漏检」需求它不满足，需要自己写轨迹插值（或用 boxmot 输出的 det_idx=-1 传播框）。
- 库很大但核心轻；文档站大量功能（VLM/dataset/关键点）与 tracking 无关，别被 README 的 50k stars 带偏成「全家桶都用」。
- 版本迭代快、deprecation 多（pydeprecate 机制会打 warning），升级需看 changelog。
