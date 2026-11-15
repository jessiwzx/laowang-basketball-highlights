# apiantonio/SoccerNet-tracking_AV2025-26
SoccerNet Tracking 2025-26 课程竞赛管线：YOLO11x 高召回 + 自适应场地掩码 + **无 ReID 的 BoT-SORT(GMC)** ｜ ~★5 ｜ Python ｜ 最后提交 2026-01-06（萨勒诺大学课程项目，Group 16）

## 数据集
无自有数据；直接消费 SoccerNet Tracking challenge test 集（结构 `tracking/test/SNMOT-XXX/img1`）。

## 跟踪管线
- **检测**：ultralytics **YOLO11x**，imgsz=1088、conf=0.2（高召回策略，专抓小目标和运动模糊球员）、iou=0.7（`configs/main_config.yaml`）。权重来源 HF `Adit-jain/soccana`（YOLOv11 SoccerNet 检测，见 `models/README.md`）。
- **跟踪**：ultralytics 内置 BoT-SORT，`model.track()` 一把梭（`src/tracker/soccer_tracker.py`）。最终配置 `configs/botsort_best11x.yaml`：`gmc_method: sparseOptFlow`、`with_reid: False`、`match_thresh: 0.90`、`new_track_thresh: 0.40`、`track_high/low_thresh: 0.35/0.20`、`track_buffer: 150`、`fuse_score: True`。ReID 权重 `models/osnet_x1_0_soccernet.pt` 在盘但**故意关掉**（外观相似队友间 ReID 反而易换 ID，靠几何+GMC 硬扛）。
- **GMC 接法**：BoT-SORT 每帧先做稀疏光流全局运动估计，把上一帧轨迹坐标补偿到当前帧坐标系后再做 IoU 关联——相机平移/变焦时轨迹不断链。可选 `sparseOptFlow|orb|none`。
- **场地掩码后过滤**（`src/utils/field_masking.py`）：0.5 倍分辨率上，HSV 色相直方图自适应找绿色主峰（hue 30-90 窗口内）→ 与 LAB a 通道(<118，绿) 求交 → 形态学开闭 → 最大轮廓凸包 + approxPolyDP；然后**脚点（bbox 底边中点）在掩码内才保留**（`bottom_tolerance=40`）。每帧重算（`mask_frequency:1`）。
- 调参痕迹：`configs/botsort_1..8.yaml` 扫过 track_high 0.35-0.6 / track_buffer 150-300 / match_thresh 0.6-0.95 / new_track 0.4-0.8——**全部 with_reid False + GMC sparseOptFlow**。

## 密集交错人群的 ID 维持效果
- Challenge test：**HOTA@0.5 = 0.742，DetA 0.864，AssA 0.637**（`output/best_on_test/results_16_20260104_114409.json`，TP 486141 / FN 44277 / FP 27514）。又是 DetA >> AssA——关联是瓶颈。
- 无 IDSw 明细入库，但结论一致：纯运动 + GMC + 严格匹配阈在相同外观队伍上把 AssA 做到 0.64，与 sn-tracking 榜单中上游水平。

## 计算成本
YOLO11x GPU（device:0、batch 16）；场地掩码纯 OpenCV 在 0.5x 分辨率很便宜。README 未给速度数字；11x 换 n/s/m 后整套在 CPU 可跑（ultralytics 同一套 YAML）。

## 对我们的借鉴点
- **GMC vs 我们的 ORB 配准**：GMC 是「轨迹坐标系每帧随相机微调」，不用检测机位突变、无重置概念，被碰移位的瞬间轨迹大概率自动续上；ORB 是「事后把坐标重挂到固定世界系」。两者互补——**跟踪层用 BoT-SORT(sparseOptFlow) 让轨迹扛过碰撞，分析层保留 ORB 重定基线**。对我们「偶尔被碰」场景，GMC 恰好是免维护的那半。
- **无 ReID 的勇气有数据背书**：与我们「在线不融合外观、离线再聚类」的架构一致；match_thresh 0.9（严）+ new_track_thresh 0.4（克制开新轨）= 少碎片的保守出生策略，可直接抄进我们的 BoT-SORT YAML。
- **脚点过滤模板**：篮球场没有绿草地，但我们有球场多边形/底线——用「脚点在场区内」替换「脚点在绿掩码内」，可廉价滤掉场边路人（野球场景常有）。
- track_buffer=150（帧）对我们 30fps 半场 occlusion 断档（SportsMOT 篮球平均 gap 68.7 帧@25fps）是合理量级，可作为初始值。

## 不适用点及原因
- 绿色草地 HSV+LAB 掩码不能直接用（篮球场多色 + 我们是室外/室内半场）。
- 足球两队的「外观完全同队」与我们「人人不同但同色系便装」不同——他们的 ReID 无用结论部分依赖队友外观几乎相同，我们 17 人外观分布更散，在线 ReID 未必同样无益（但 SportsMOT 消融支持仍不必上）。
- GPU 11x 检测档位与我们 CPU 优先冲突（可换小模型解决）。
