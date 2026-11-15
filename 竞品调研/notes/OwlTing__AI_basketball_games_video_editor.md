# OwlTing/AI_basketball_games_video_editor

命令行工具：YOLOv4 逐帧检测 person/basketball/basketball_hoop，用「球 bbox 与筐 bbox 相交」规则找出手帧并剪集锦 ｜ ★126 ｜ Python (PyTorch 1.3, 2020 技术栈) ｜ 最后提交 2024-08

## 进球判定技术路线

**注意：它判定的是「出手/球到筐附近」，不区分进与不进——没有进球判定。**

- 检测：自训 YOLOv4，3 类（`pytorch_YOLOv4/data/basketball_obj.names`: person/basketball/basketball_hoop，cfg 为 `cfg/yolov4-basketball.cfg`），推理输入 1184×1184（pytorch 引擎可选 320+96n；TensorRT 引擎仅 1184）。逐帧全扫整段视频。
- 「shot」规则（`tool/utils_basketball.py` 的 `detect_shot`，全部逻辑约 70 行）：对每个检出 basketball 的帧，在 ±2 帧（`search_frame_range=2, search_mode='full'`）内找 hoop 检测；hoop 框四边外扩 0.5 倍自身宽高（`scale=0.5`）；球框交集面积 > 0 即记一个 shot。无轨迹链、无方向、无网口判据。`bb_intersection_over_union` 名不副实，只算交集面积不除并集。
- 集锦：`get_highlight_indexs` = 每个 shot 帧前后各 `cut_frame`（默认 50）帧，union 成高亮区间，重剪视频（`video_editor.py` 主流程：读帧→检测→obj_log 落盘→规则→VideoWriter）。
- 输入构造：无分类器，无 crop，无时序堆叠——纯检测框空间关系。

## 出手人/球员识别

无。person 类只画框上色，不关联出手、不追踪、不辨人。

## 工程亮点

- **特征提取与剪辑彻底解耦**：检测结果是逐帧 DataFrame（frame_index/class/box/obj_box_id）存 `obj_log_name.data`；`--read_flag 1` 可重读日志换 output_mode（full/basketball/shot/standard/clean）再剪辑，不重跑推理。两趟式架构与我们一致。
- 日志每 2000 帧分段落盘（`save_step`），长视频崩了不至全丢。
- 提供 pytorch 与 TensorRT 两套推理引擎（TRT 约 3× fps），并有 DeepStream 部署配置。

## 声称的指标或测试

README 无任何准确率/召回数字，只有 demo 视频与截图。demo 素材为室内正规比赛转播画质。

## 对我们项目的可借鉴点

1. 痛点①（进球判定）：**反面印证**——纯「球到筐区域」空间判据正是我们规则版最粗的形态；他们连轨迹都不建，说明该路线天花板远低于我们现有 v11（几何穿越+下落+实体再现）。无直接可搬的判据。
2. `obj_log` 的「逐帧检测日志 → pandas 后处理」分层值得参考：我们把 HSV 检测结果也已是类似日志形态，规则迭代不碰检测层。
3. 痛点②（出手人）：无帮助。

## 明确不适用的点及原因

- 不区分进球/打铁——对我们核心需求（进球判定）是空集。
- 1184×1184 YOLO 逐帧扫 30min~2h 视频的算力开销（README 自己要上 TensorRT），我们无此预算，且我们是「规则触发才跑重模型」的架构。
- 他们的筐在转播画面里数百像素大；我们 35~65px 小筐 + 遮挡球，YOLO 对小目标/被手遮球的召回不可指望（我们 HSV+轨迹链在这点上更适合）。
- 多机位/移动机位假设（hoop 逐帧重检），我们固定机位已标定筐口，无需逐帧检测筐。
