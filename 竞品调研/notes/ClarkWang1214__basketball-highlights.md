# ClarkWang1214/basketball-highlights
一句话定位：三脚架半场 3V3 视频丢进去自动出进球集锦——VLM(小米 Mimo v2.5) 做决策 + YOLO/OpenCV 做验证 ｜ 2 stars ｜ Python ｜ 2026-05-28 创建、当天最后提交（个人 vibe coding 项目，单日完成）

## 进球判定技术路线（重点）

三阶段管线，全部在 `highlight.py`（约 460 行，唯一核心文件）：

1. **Phase A：VLM 全视频扫描找进球时间戳**（`detect_goals()`，highlight.py:121）
   - 输入：**整个视频文件 base64 编码**（`data:video/mp4;base64,...`）一次性发给 Mimo v2.5 API，客户端抽帧 fps=3（CLI 默认，README 参数表写 0.1-10 可调），上限 2048 帧（Mimo 平台限制）。
   - Prompt（highlight.py:25 `GOAL_DETECTION_PROMPT`，原文）："这是一段篮球比赛的视频。请仔细分析视频内容，找出所有投篮得分（进球）的瞬间。对于每个进球，请指出它发生在视频的第几秒（从视频开头算起）。请用以下JSON格式返回结果（不要包含其他文字）：{"goals": [{"time_sec": 5, "description": "球员上篮"}, ...], "total_goals": N}"
   - 关键工程细节：**必须禁用 thinking**（`extra_body={"thinking": {"type": "disabled"}}`，highlight.py:61），否则推理过程吃光 max_tokens 导致回复为空。
   - 输出解析：`_parse_json()`（highlight.py:93）三级容错——剥 markdown 围栏 → 正则抓最外层 `{...}` → 修复尾逗号后重试。
   - 后处理：ffprobe 拿真实时长，丢弃 `time_sec > duration` 的越界幻觉进球（highlight.py:396-405）。
2. **Phase B：VLM 逐球精细识别**（`refine_goals()`，highlight.py:141）
   - 每个进球 ffmpeg `-c copy` 截 4s 片段（进球前 1.5s ~ 后 2.5s），fps=6 重发 Mimo。
   - Prompt（highlight.py:33 `SCORER_ANALYSIS_PROMPT`）问 5 个问题：球衣颜色 / 号码 / 2分3分（按投篮位置）/ 动作类型 / 球员特征描述，要求 JSON 返回。
3. **Phase C：CV 交叉验证**（`track_players()`，highlight.py:207 + 整个 `player_tracker.py`）
   - YOLOv8n（COCO person 类，conf>0.35）→ supervision ByteTrack 跟踪 → HSV 取球衣颜色。
   - 融合逻辑（重点：**CV 只验证、不否决**）：取 Mimo Phase B 说的 `jersey_color`，在 CV 跟到的球员里找同色且 frames_tracked>5 的，取最活跃者，`cv_match` 打 ✅；找不到同色球员则回退到 CV 自己的启发式投篮者判定，打 ❓。**没有任何一路能推翻另一路**——验证结果只写进 JSON 字段，不改变进球列表本身。
   - CV 自己的投篮者启发式（player_tracker.py:199 `_identify_scorer`）：进球帧 ±0.5s 内 **y 坐标最小（位置最高）** 的球员 + 活跃度加权——"跳投/上篮时身体最高"。这是很弱的启发式，README 的 TODO 也承认要改进。

进球后的剪辑：±5s/+3s 窗口，重叠/相邻片段合并（`_merge_ranges`，highlight.py:271），ffmpeg concat stream copy。

### VLM 成本/速度（README + test_mimo_video.py 实测口径）
- 云 API 按量计费：Phase A 一场约 **64K tokens**，Phase B 约 **0.03 元/球**，短片段（同 clip 重复发送）缓存命中率 90%+。
- token 估算模型（test_mimo_video.py:20 `estimate_video_tokens`）：视觉 500-1500 tokens/帧 × min(时长×fps, 2048) + 音频 6.25 tokens/s。
- 时延：单次 API 调用按视频长度计（测试脚本输出 elapsed_s），无批量/并行处理。
- Mimo 平台硬限制：base64 ≤50MB、fps 0.1-10、≤2048 帧——**30 分钟视频 fps=3 恰好顶到 2048 帧上限**，2h 视频此路线直接不可行。

## 出手人/球员识别
- Phase B VLM 直接"看"片段回答球衣颜色/号码/特征；Phase C 用 CV 颜色交叉验证 + 位置最高启发式兜底。**没有 track 级别的出手者确认**，只有颜色这一弱特征。

## 工程亮点
- 整个 VLM 接入只有 ~70 行（OpenAI 兼容 SDK + video_url 类型传 base64 + fps 参数），接入成本极低，`test_mimo_video.py` 是一份干净的 Step-0 能力验证脚本（token 估算/时长/大小检查/结构化输出解析）。
- 越界时间戳过滤（对 VLM 幻觉的最小防御）；JSON 三级容错解析。
- Streamlit Web UI（app.py，185 行）+ 手机端可用；repo 内 docs/ 完整设计文档（brainstorm 记录）。

## 声称的指标或测试
- 无正式评测。README 明说核心问题：**同一视频 Mimo 检出 6~17 球波动**（非确定性、召回不稳），VLM 精确颜色判断错误率 ~50%。
- 设计文档（docs/clarkwang-main-design-20260528-164202.md）原推荐 Approach B「CV 粗筛(帧间差分+ROI+二级时序模式) + VLM 逐段精判」，实际实现走的是更省事的 Approach A（纯 VLM 全扫），README 的 P0 TODO 第一条就是「CV 候选 + VLM 精判——解决 Mimo 非确定性的根本方案」。**即：作者自己已论证了我们已有的架构才是对的。**

## 对我们项目的可借鉴点
- 痛点①（进球判定准确率）：
  - 最有价值的反而是**反面+正面双重印证**：纯 VLM 全视频扫描路线的失败模式（召回波动 6-17 球、时间戳幻觉要靠时长过滤）正是我们两次 VLM 幻觉教训的量化版；而该作者的解法「CV 多信号融合做候选发现（高确定性召回）+ VLM 只做精判」恰好就是我们已建好的管线 + 我们开放中的「VLM 复核档 triage」方向——独立方走到了同一结论。
  - **Phase B 式「4 秒小片段 + 指令性 JSON prompt + thinking 禁用」是可以直接抄的 VLM triage 模板**：对每个候选事件截 crop/短片问"球最终进了还是弹出？{"result": "in"/"out", "evidence": "..."}"，输出用于给 low/review 档排序而非最终裁决。成本口径可参考：~4s 片段 fps=6 ≈ 24 帧 ≈ 数千 tokens/事件，一场几百候选约几十万 tokens，量级可控。
  - 越界过滤、JSON 容错解析这类小防御值得抄。
- 痛点②（出手人归人）：
  - Phase C 的「VLM 说颜色 → CV 验证场上是否真有该色球员」双向确认思路有趣，但在我们的灰白黑同色系球场已证伪（颜色聚类失败）。
  - `_identify_scorer` 的"出手时刻 y 最小=跳起者"启发式可作为我们轨迹回溯出手点的一个免费辅助信号（出手瞬间投篮者通常离地最高），但野球场上篮/抢板同样会跳，只能做弱特征。

## 明确不适用的点及原因
- 整段视频 base64 单次 API 扫描：我们 30min~2h、VFR、文件远超 50MB base64 上限，2048 帧上限也顶死；且篮筐仅 35~65px，低帧率小目标 VLM 根本看不清筐口细节，只会放大幻觉。
- Mimo 的球衣颜色/号码识别（错误率 ~50%）在我们的无号码、同色系场景完全失效，作者自己的数据就是证伪证据。
- ByteTrack track_id 做人身份：我们已知道固定机位野球遮挡/重号问题严重，该仓库 TODO 也列着"track 碎片化管理"未解决。
