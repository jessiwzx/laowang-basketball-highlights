"""检测编排：检查点续跑、events.json 读写、检出率警告。"""
import contextlib
import inspect
import json
import os
import time

import cv2

from . import static_filter
from . import hoop_track
from .detector import safe_fps
from .detector_switch import NAME as DETECTOR_NAME, detect_positions
from .editor import transcode_for_detection
from .goal_decider import COOLDOWN_SEC, apply_area_gate, decide_goals
from .models import (BallPos, CalibSegment, Calibration, calib_key, load_events,
                     save_events, segments_key)
from .motion import MotionTracker, load_ref_image
from .net_clusters import net_cluster_pass
from .net_motion import net_evidence_pass

CHECKPOINT_SEC = 300.0        # 每 5 分钟写一次检查点
CHECKPOINT_FILE = "checkpoint.json"
EVENTS_FILE = "events.json"
POSITIONS_FILE = "positions.json"   # 检出流存档：完成后保留（检查点则删除）
# 检测逻辑版本（复用缓存的身份之一）：detector/goal_decider 的判定行为
# 变化时必须 +1，否则旧 events.json 会被静默复用、逻辑升级被缓存吞掉
# （2026-08-17 事故：静止链过滤+下落校验上线后用户重跑仍得旧 102 条）。
# v2 = 静止链静态过滤 + 下落校验 + descent_px 依据字段（bfbaf91）。
# v3 = 下落实体校验（穿越后须再见球体量级检出；大穿越点降级复核、
#      小碎片穿越删除，t=127.02 类幽灵治理）+ below_big 依据字段。
# v4 = 帧时间改容器 PTS 计时（CAP_PROP_POS_MSEC），可变帧率视频的进球
#      时刻与 ffmpeg 剪辑时间轴对齐——此前 帧号/fps 比 PTS 早 2.7~3.0s，
#      标红偏早数秒、"前5后2"切片把进球挤出窗外（2026-08-18 取证
#      work/_debug_timeline/check_axis.py）。检出钟变更，旧缓存一律重算。
# v5 = 误判调优（2026-08-19 取证 work/_fp_analysis/）：穿越点改上/下点
#      内插真值（旧"下方首点 x"偏 0.2~0.4 筐宽）+ 规则A 几何门槛（球心
#      出筐口两端 |off|>0.45 拦截、压筐沿 0.30~0.45 强制复核）+ 规则C
#      弹出严格化（0.25~1.2s 内回上方强制复核）；标定余量 15%→5%。
# v6 = 检测 ROI 扩大（SIDE 1→2.5 筐宽、UP 2→5 筐高、DOWN 3→8 筐高）：
#      检出点集合变大（球在弧顶/落地的轨迹进框），标定参考帧不再要求球
#      恰好贴筐。穿越判定本身不受影响，但旧检出流不能复用，一律重跑。
# v7 = 四道否决门槛（规则A 拦截/下落校验/实体校验/置信度<0.5）不再删除
#      候选，统一降为 low 档进列表（用户决策"宁多勿漏"：所有投篮候选
#      可见，低置信默认不勾选不进集锦）。事件集合扩大，旧结果须重跑。
# v8 = 几何阈值加像素下限（kill 24px / review 16px）+ 实体阈值改纯相对
#      （删 130px² 绝对底）——1080p 行为不变，720p 小筐（35×14px）上不再
#      把像素级标定/插值误差放大成「从筐旁飞过」、不再把 50~100px² 真球
#      误判成碎片（2026-08-20 取证 work/_v8_analysis/：17 真进球 0 high）。
# v9 = 镜头位移校正（检出坐标配准回标定参考帧坐标系——取证：镜头被碰
#      +32px 后碰后真进球全被几何门槛降级，校正后 17 条真进球 15 条 high）
#      + 几何像素下限缩到误差预算（24/16→6/4px：配准修根后下限只兜底
#      点击/插值/配准残差 ~6px，筐宽 ≥13px 时纯相对阈值 0.45/0.30 生效，
#      不再随拍摄远近失灵）+ 标定指纹加参考帧内容指纹。检出坐标变了，
#      旧检出流/检查点一律作废。
# v18（2026-10-11 问题②）：网证据进置信度、档位阈值化——net_evidence_pass
# 测量对象从 high 扩到全部行，网分（静止 −0.20 / 非局部 −0.10 / 网动或未测
# 0）回写进 conf，非硬门行按 HIGH_CONF/MIN_CONF 重分箱（硬门行测网只记录
# 不改档）；R1 升绿 / P1d 降档两条 status 旁路删除，硬门改挂
# GoalEvent.review_reason 标记；B2 注入行挂 review_reason="b2" 恒 review
# 且补 net_* 实测（仅显示、网分按 0）。NET_SIGNAL_VER 不动（测量参数未变，
# 旧 sidecar 增量复用）；DETECT_VERSION 不动。详见
# docs/superpowers/specs/2026-10-11-net-confidence-design.md。
# v20（2026-11-01）：冷却身份判据——冷却是整条判定链上唯一还在静默删除
# 候选的机制（其余门槛 2026-08-19 起一律只降级不删），它用「时间」代理
# 「身份」，把 3s 内的第二颗球（篮板补篮）连同同一颗球的重复穿越一起删掉。
# 改为冷却前先判身份（goal_decider._new_ball_since）：两行之间球若重新走完
# 一遍接近轨迹（≥2 上方点、跨度 ≥0.2s、含高处点或侧移点）则放行、按 conf
# 正常定档；否则仍吞。低置信自有钟同款。三场取证 87 条增量 = 另一颗球 31
# （high 20/review 5/low 6）+ 同球重复 56（high 21/review 27/low 8），见
# 诊断/2026-11-01-冷却验证/。positions/网 sidecar 均可复用，重放不重检。
# v21（2026-11-07）：筐锚点校正——用 YOLO 的 hoop 类（class 1，模型里本来
# 就有、此前被 classes=[0] 挡掉）做绝对观测，消 ORB 配准的累积漂移。
# 动机：现行「标定 + ORB」只标一次时四场实测偏 0.35~0.68 筐宽（中位数已超
# review 门 0.30），用户实际每场要手工补 1~5 段；而筐检出率 99.86%、误检
# 0.21%、扣掉固定偏置后残差中位 0.71px（四场 1409 帧，见
# 诊断/2026-11-07-筐识别/结论_只标一次验证.md）。
# 做法：**不动 positions 的坐标语义**（仍是 ORB 校正后的量），事后叠低频
# 校正项 c(t)：x_cal = x_raw − shift_orb(t) − c(t)，c 由
# hoop_track.build_correction 从 sidecar 采样算（20s 采样 + 孤立点剔除，
# 只吃 ORB 解释不了的慢分量——摇镜是旋转、球与筐位移本就不等，绝不拿筐
# 单点位移替换全局位移）。判定几何与网区 ROI 同步用 c 修正后的 shift。
# 旧档无 hoop 字段 → c≡0 → 判定逐条等价 v20（这也是 DETECT_VERSION 不动、
# 存量六场可秒级重放的依据；要吃到收益需用 tools/remap_hoop.py 补采样）。
# v22（2026-11-07）：a) 逐帧筐锚点搬点**默认关**（hoop_track.HOOP_SHIFT_ENABLE
# ——v21 实测净亏：五场查准 +0.60 / 查全 −0.92pct、10-17_1 单场 −9.87pct；
# 采样与「疑似变焦/锚点异常」提示保留，段间桥接不吃这个开关）；
# b) 校正采样孔洞告警分两档：≥180s 原重话不变，30~180s 新增轻提示（带具体
# 时刻），解决「真被挪过镜头时 40~70s 断流全程静默」（10-16_1 41.3min 断
# 70s 旧版不报）；实测 12 场 19 段 >30s 孔洞仅 1 处，不会遍地误报。
# 判定规则与阈值零变化——升版只为让既有 events.json 不吞新提示（①级复用
# 路径会原样带回旧 warning）。positions/网 sidecar 全可复用，秒级重放。
# v23（2026-11-14）：侧分短档 0.14 → 0.17（goal_decider.SIDE_SHORT）——点数
# 够（≥2）但时间跨度 <CONT_SPAN_SEC 的证据侧，旧值等于系统性惩罚真球（真球
# 68% above_span<0.2，假绿仅 23%）。新六场（用户标注口径）自动档 813→863、
# 命中 620→659、假绿 193→204、降级 166→127 → 查准 76.26→76.36%、覆盖
# 78.88→83.84%，边际查准 78.0% 高于存量；93 条改档全为升档、零降档，逐场
# 6/6 覆盖上涨、查准最大跌 0.9pct。判定结构/阈值/冷却全未动，positions 与网
# sidecar 全可复用 → 秒级重放。L0+L1 双验见 诊断/2026-11-14-优化候选/。
# v24（2026-11-14）：穿筐物投影面积硬门（goal_decider.AREA_KILL_REL）——
# conf 只看点数/跨度/横偏，不认识「穿过去的是什么东西」，手/小臂横穿筐口
# 平面时几何分能拿满、稳稳混进 high 变成会剪错的假绿。用 positions 一直带着
# 却没被用过的检出框面积：穿越点面积 ÷ 筐矩形面积 > 0.50 → review
# （review_reason="area"，优先级排在 entity/geom/pop_back 之后，只为把影响面
# 锁在原本没有硬门的行上）。真球实测 0.12~0.16（659 条命中最大 0.488），
# 被抓的是同场真球面积的 4.1~6.8 倍。新六场：high 863→830、假绿 204→171、
# 命中 659 不变 → 查准 76.36→79.40%、覆盖 83.84% 不变；33 条里 17 条有
# 用户 feedback `not_goal`、0 条 `goal`。判定结构/阈值/冷却全未动，
# positions 与网 sidecar 全可复用 → 秒级重放。新增 GoalEvent.cross_area_rel
# 落表（否则 L0 复现闸门无法复算这一门）。详见
# 诊断/2026-11-14-优化候选/阶段6-面积特征结论.md。
LOGIC_VERSION = 24
# v17（2026-10-11）：B2 注入行置信度修复——不再硬编码 0.30，改按主判定器
# 同款公式用链证据计算（0.3 基础 + below 侧分 + 位置分，above 侧恒 0）。
# 用户报 2026-10-10_1 的 10:43.8（8 点/0.8s/off −0.35）应为 0.60 而显示
# 0.30；八场 47 行注入行同病。档位不变（注入行仍恒 review，宁多勿漏），
# 指标预期与 v16 持平；positions/网 sidecar 均可复用，重放秒级。
# v16（2026-09-25）：B2 网簇触发兜底注入（net_clusters.net_cluster_pass，
# 挂在 _merge_segment_events 之后）——全片「网动簇」对不上任何事件行/
# 已删键（±2.5s）时，簇尾锚窗内 ≥3 点严格下行残迹链成立则补一行 review
# （静默漏检兜底，八场 210 行口径、其中确证真球 4；链门 N=3 用户裁定
# 「保持3」宁多勿漏）。旧行永不改写，注入行只追加表尾；时刻=positions
# 内插穿筐点。簇扫描一次性、时长与片长成正比（实测最长 ~10 分钟/场），
# sidecar net_clusters.json 缓存
# （身份不含 LOGIC_VERSION，判定器重放不重扫）。详见模块注释。
# v15（2026-09-25）：E2 碎片链实体证据（goal_decider 两处，详见其常量
# 注释）——①小筐（hw<45）碎片链「贯穿网深的严格单调下行」（≥3 点、
# y 跨度 ≥2 筐高、步步向下）= 实体等价，不再挂「全程无球体级量检出」
# 低置信（八场分布 真 6/假 0，六条实测网信号全过门 → ⚪→🟢；大筐收
# 硬门——09-11 片头热身机位标定域外拼出 t=11.14 假绿）；②小筐（hw<45）
# 兜底链点门槛 3→2（救 09-19 1048.1 两点链漏检，大筐维持 3 防碎片噪声）。
# v14（2026-09-24）：冷却释放（Cooldown Release，CR）——网门降级行不再
# 占 3s 冷却钟，其后被吞的候选经二趟重判浮现（goal_decider 的
# cooldown_keep_ts 参数 + 本文件 _cooldown_release_pass 编排）。八场消融
# （tools/evaluate.py 口径）：救回 09-12 五已知漏检中的 2（5243.5 自动绿
# + 1957.6 review 可见）与 09-04 55:12 真进球（truth 09-24 已校正）；代价
# 面收两道闸：①筐宽 <NET_RELEASE_MIN_HW 的段不放行（08-29_3 35px 两条
# v14（2026-09-24）：冷却释放（Cooldown Release，CR）——网门降级行不再
# 占 3s 冷却钟，其后被吞的候选经二趟重判浮现（goal_decider 的
# cooldown_keep_ts 参数 + 本文件 _cooldown_release_pass 编排）。八场消融
# （tools/evaluate.py 口径）：救回 09-12 五已知漏检中的 2（5243.5 自动绿
# + 1957.6 review 可见）与 09-04 55:12 真进球（truth 09-24 已校正）；代价
# 面收两道闸：①筐宽 <NET_RELEASE_MIN_HW 的段不放行（08-29_3 35px 两条
# 自动假绿经筐区拼图对抗性二审判死——小筐网信号与视觉判读双双失真）；
# ②基线行一趟为准原样保留、二趟只附加 ±0.05s 外新行（08-22 4508 教训：
# 二趟兜底伪点集不同可令同行漂移，不可拿二趟行顶替一趟行）→ 基线零丢失
# by construction。09-12 另浮出 3 条比赛间隙真进球（用户 2026-09-24 裁定
# 手动会删、不进 truth，evaluate 以注释常量剔除不计假绿）。
# 注意与 net_motion「v14 局部性门」同名不同物（那是 NET_SIGNAL_VER=2 的
# 参数版本号，2026-09-14）。
# v13（2026-09-11）：A1 校正失效警告——判定行为零变化，仅新增 warning 文案：
# 末次有效位移采样距片尾 >CAL_GAP_WARN_SEC（或全程零采样）时显式告知
# 「校正已断、该时段判定不可信」（09-04_1 取证：镜头 t≈1525 被手持挪位后
# 门控全拒收、校正冻结在 (0,0)，后 79% 视频检测框盯空气，high 占比
# 48%→7% 且 warning=None 全程静默）。+1 只为让旧 events.json 不吞新警告
# （①级复用路径会原样带回旧 warning）。
# v12（2026-09-10）：篮网运动后置证据 pass（net_motion.net_evidence_pass，
# 挂在 decide_goals 之后）——high 事件逐个测网区事后窗帧差（shift 修 ROI +
# 相位相关补高频振动 + 筐左对照区），网静止（<5.0）→ 降 review「网未摆动」。
# 单向证据（用户提议验证）：球穿网必撑网，网静止=筐前/筐后飞过或未触网；
# 网动不升档——打铁蹭网/砸筐传振同样让网动。truth 三场消融：假绿救回
# 58/147（39%）、真绿误杀 8/381（2.1%，弱摆轻擦入网为主，降级可逆）。
# 信号存 sidecar net_signals.json（视频+筐标定+参数版本指纹），判定器
# 重放路径命中缓存不重测、保持秒级。DETECT_VERSION 不动（检出流复用）。
# v11（2026-08-31）：判定器兜底路径——筐口正下方实体级下落链注入虚拟
# 上方起点（goal_decider._fallback_up_points），救上方轨迹整体缺位的
# 真进球（08-28 19:12 斜射：上方点全出横向容差/被同帧大块截胡）。
# 兜底事件强制不升绿。三场 truth 消融：核心指标零变化、08-28 漏检
# 1→0、08-22/08-21 零回归。DETECT_VERSION 不动（检出流复用）。
# 检测器版本（positions.json 复用身份）：detector 的检出行为变化
# （ROI/HSV/位移校正/计时钟）时 +1。与 LOGIC_VERSION 分离——LOGIC_VERSION
# 涵盖判定器，仅判定阈值/规则变化时 +1，此时检出流仍可复用：跳过全片
# 检测、秒级重放判定器（调参迭代从 ~110 分钟缩到秒级）。
#
# 本项目检出层换了实现（自训 YOLO，见 bbh/detector_yolo.py）→ 版本升到 2。
# 检出流缓存指纹还额外并进检出器名（bbh/detector_switch.py 的 NAME）：YOLO 与
# HSV 的检出流不可混用，切 BBH_DETECTOR 会整档作废旧缓存、不误吃。
DETECT_VERSION = 2


def detect_fingerprint():
    """检出流缓存指纹 = 版本 + 检出器名（在用时读全局，便于测试改版本号）。"""
    return f"{DETECT_VERSION}-{DETECTOR_NAME}"
TRANSCODE_FILE = "det-source.mp4"   # OpenCV 解不了时的转码副本（检测专用）

CALIB_WARNING = "整段视频在筐口区域未检测到球，标定可能不适用于此视频，建议重新标定。"
DURATION_NOTE = "视频时长元数据异常，已按处理帧数估算；片段边界可能有偏差。"
FPS_NOTE = "视频帧率读取异常（{}fps），时间轴可能不准，进球检测或受影响。"
PTS_NOTE = ("视频后端读不出帧时间戳，时间轴按帧率估算；可变帧率视频的"
            "进球定位可能偏移数秒。")
TRANSCODE_NOTE = ("源视频编码不受 OpenCV 支持，已自动转码 H.264 后检测"
                  "（首次较慢，之后复用转码副本）。")
NO_REF_NOTE = ("标定未存参考帧，镜头位移校正未启用（在「⚙ 标定设置」重新生成"
               "一次标定即可启用：固定机位中途被碰/漂移的自动校正依赖它）。")
# 校正采样覆盖孔洞超过此秒数 → 该时段参考帧匹配失效，显式警告（A1）。
# 固定机位全程每 ~6.25s 一个采样，但自然断流真实存在：08-21_1（静态机位
# truth 场）实测最大间距 112.8s（暗段拒收，冻结值恰好正确无伤）——阈值
# 必须显著高于它（180s 有 60% 余量），又远小于最小真实病案 09-04_1 的
# 1531s。孔洞三处形态：片头（首样本前，用户把参考帧标在了移动后的时刻、
# 片头段全部失配）/ 中段（挪走又挪回）/ 片尾（挪走没回来，09-04_1 首例）。
CAL_GAP_WARN_SEC = 180.0
CAL_GAP_NOTE = ("镜头位移校正存在未覆盖时段（{eras}）：这些时段参考帧匹配失效"
                "（多半移动过镜头），校正被冻结在旧值，检测框可能跟不上画面、"
                "进球判定不可信。建议按移动点分段处理（镜头稳定后重新标定，"
                "或把视频切开分别检测）后重跑。")
# v22：轻提示档（30s）。上一档 180s 的文案是「判定不可信」级重话，不能直接
# 套在 30s 上——自然断流真实存在（08-21_1 实测 112.8s 暗段拒收、冻结值恰好
# 正确无伤），会变成遍地误报。而 180s 又太钝：真被挪过镜头的场次往往在
# 40~70s 就断流（10-16_1 实测 41.3min 断 70s，旧阈值静默通过）。实测 12 场
# 19 段中 >30s 孔洞仅 1 处 → 本档基本只在真断流时说话。
# 与自动补段的关系（2026-11-07 验证后否决，见 docs/方案_自动补段_2026-11-07.md）：
# 12 场无一需要自动建段（段1标定+ORB 全程筐位残差 ≤0.10 筐宽），故不做自动
# 补段，改为「断流时提醒人」——本档就是那个提醒。
CAL_GAP_HINT_SEC = 30.0
# 「{n} 秒」取的是**最长**那段孔洞，而 {eras} 最多只列 3 段（更多折叠成
# 「等 N 段」）——最长的那段未必在列出的 3 段里。所以文案必须点明「取最长」，
# 否则读者会把 90 秒安到眼前那两个 40 秒时段的头上，以为文案算错了。
CAL_GAP_HINT_NOTE = ("镜头位移校正的采样在 {eras} 断了 {n} 秒（多段时取最长）："
                     "若这段画面里镜头被挪动过，建议在移动点补一段标定后重跑；"
                     "暗场/遮挡/暂停也会造成断流，多数情况无需处理。")
CAL_NO_SAMPLE_NOTE = ("镜头位移校正全程无有效采样：参考帧与画面始终匹配不上"
                      "（镜头一开始就移动过？），位移校正未真正生效；若视频"
                      "中途移动过镜头，判定不可信，请重新标定生成新参考帧。")
# v21 筐锚点：只告警不改行为的两种情形（变焦必须人工补段——判定阈值全按
# 筐宽算，尺寸跟着变会把几何搞错；锚点错乱则宁可退回纯 ORB，修错比不修更糟）
HOOP_ZOOM_NOTE = ("筐锚点检测到画面里筐的尺寸变化较大（{n} 处，疑似变焦）："
                  "位置按锚点跟了，但尺寸未跟着改（判定阈值以标定筐宽为标尺）。"
                  "若确实变过焦，建议在变焦后补一段标定。")
HOOP_ABNORMAL_NOTE = ("筐锚点与标定筐位偏差过大（最大 {res:.1f} 个筐宽），"
                      "本次未采用锚点校正、仍用参考帧配准；若该段进球明显偏少，"
                      "请检查标定是否标错筐。")
HOOP_NO_REF_NOTE = ("未取到参考帧上的筐位（参考帧不可读，或与视频分辨率不同），"
                    "筐锚点校正未启用；本段仍按参考帧配准判定。")
# 锚点残差上限（筐宽倍数）：超过即判「锚点不可信」——真实整机平移最大实测
# 7.09 个筐宽（10-17_1 的 101min 段），但那是已知的极端；放到 10 类足以
# 覆盖真实场景，又拦得住标错筐/误检链造成的几何错乱。
HOOP_MAX_RES = 10.0
# 尺寸离群样本占比超过此值才告警（手点框与模型框一贯差 1.1~1.3 倍是常态）
HOOP_ZOOM_RATIO = 0.15
SUSPECT_FPS_LOW, SUSPECT_FPS_HIGH = 1.0, 1000.0


class DetectionCancelled(Exception):
    """外部请求取消检测（页面刷新/关闭）。frame_idx=取消时刻的进度帧号，
    落检查点用。"""

    def __init__(self, frame_idx: int = 0):
        super().__init__(f"cancelled at frame {frame_idx}")
        self.frame_idx = frame_idx


def _probe_fps(video_path: str) -> float:
    """只读容器头拿 fps（写检查点时需把 t 换算回帧号）。"""
    cap = cv2.VideoCapture(video_path)
    try:
        return safe_fps(cap.get(cv2.CAP_PROP_FPS))
    finally:
        cap.release()


def _calib_key(calib: Calibration) -> list:
    """检查点归属校验用的可 JSON 序列化标定指纹（tuple→list 才能比对）。
    含参考帧内容指纹：换参考帧（重新标定）后旧缓存自动失效。
    2026-09-12 起真身在 models.calib_key（CalibSegment.key/segments_key 需要
    同一口径），这里保留历史名委托——appearance/shooter import 的是它。"""
    return calib_key(calib)


def _ref_note_unreadable(path: str) -> str:
    return f"标定参考帧读取失败（{path}），本次未做镜头位移校正。"


def _ref_note(ref_image: str | None) -> str | None:
    """参考帧状态 → 警告文案；None=校正正常启用，无需警告。"""
    if ref_image is None:
        return NO_REF_NOTE
    if load_ref_image(ref_image) is None:
        return _ref_note_unreadable(ref_image)
    return None


def _motion_summary(shift: list) -> dict | None:
    """events.json 的位移摘要（诊断展示用）：采样数与两轴最大位移像素。"""
    if not shift:
        return None
    return {"samples": len(shift),
            "max_dx": round(max(abs(s[1]) for s in shift), 1),
            "max_dy": round(max(abs(s[2]) for s in shift), 1)}


def _fmt_clock(t: float) -> str:
    """秒 → m:ss（警告文案里的时刻展示，与剪辑台表格口径一致）。"""
    s = int(round(t))
    return f"{s // 60}:{s % 60:02d}"


def _cal_gap_note(shift: list, win_start: float, win_end: float,
                  motion_active: bool) -> str | None:
    """校正采样覆盖孔洞检查（A1 失效警告）——按段窗算（A2 多段标定）。

    v9 门控拒收=不采样，interp_shift 对孔洞两端就近延伸——参考帧失配的
    时段校正会悄悄冻结（09-04_1 取证：镜头 t≈1525 被挪位后片尾 79% 视频
    检测框盯空气、high 占比 48%→7%，且 warning=None 全程静默；用户事后
    把参考帧重标到移动后，病况镜像翻到片头段）。这里把「采样断流」变成
    用户可见警告。校正未启用（motion_active=False）不报——NO_REF_NOTE
    已覆盖该情形，不叠报。
    段窗语义（A2）：窗 [win_start, win_end]，段尾=窗尾不算孔洞——段扫描
    止于下一段起点是设计行为，不是校正断流。单段调用 win_start=0、
    win_end=时长，即 v13 全片口径。
    v22 两档：≥CAL_GAP_WARN_SEC（180s）出重话，否则 ≥CAL_GAP_HINT_SEC
    （30s）出轻提示。**不叠报**——同一段只会拿到一句，重话优先。"""
    if not motion_active or win_end - win_start <= CAL_GAP_HINT_SEC:
        return None
    if not shift:
        # 全程零采样只在长段才算病（短段本就不够一个采样周期，正常现象）
        return (CAL_NO_SAMPLE_NOTE
                if win_end - win_start > CAL_GAP_WARN_SEC else None)
    holes = _cal_holes(shift, win_start, win_end, CAL_GAP_WARN_SEC)
    if holes:
        return CAL_GAP_NOTE.format(eras=_fmt_eras(holes))
    holes = _cal_holes(shift, win_start, win_end, CAL_GAP_HINT_SEC)
    if holes:
        return CAL_GAP_HINT_NOTE.format(
            eras=_fmt_eras(holes), n=int(max(b - a for a, b in holes)))
    return None


def _cal_holes(shift: list, win_start: float, win_end: float,
               sec: float) -> list:
    """段窗内（段头 + 中段 + 段尾）超过 sec 秒的采样孔洞 [(a, b), ...]。"""
    holes = []
    prev = float(win_start)
    for s in shift:
        if s[0] - prev > sec:
            holes.append((prev, s[0]))
        prev = s[0]
    if win_end - prev > sec:
        holes.append((prev, win_end))
    return holes


def _fmt_eras(holes: list) -> str:
    """孔洞列表 → 「1:23~2:45、5:00~5:40 等 5 段」。"""
    eras = "、".join(f"{_fmt_clock(a)}~{_fmt_clock(b)}" for a, b in holes[:3])
    if len(holes) > 3:
        eras += f" 等 {len(holes)} 段"
    return eras


def _load_checkpoint(work_dir: str) -> dict | None:
    """读检查点；任何形式的外部损坏（半截 JSON/非 UTF-8/非 dict/不可读）
    一律作废返回 None 全量重跑，不崩溃。"""
    p = os.path.join(work_dir, CHECKPOINT_FILE)
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _save_checkpoint(work_dir: str, frame_idx: int, positions: list[BallPos],
                     video: str, fps: float, calib: Calibration,
                     shift: list | None = None, t0: float = 0.0,
                     hoop: list | None = None):
    p = os.path.join(work_dir, CHECKPOINT_FILE)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"frame_idx": frame_idx,
                   "positions": [[b.t, b.x, b.y, b.area, b.fi] for b in positions],
                   "positions_len": len(positions),
                   "fps": fps,
                   "video": video,
                   "calib": _calib_key(calib),
                   "detect_ver": detect_fingerprint(),
                   "t0": float(t0),   # 多段标定：检查点所属段的起点（归属校验用）
                   "shift": [list(s) for s in (shift or [])],
                   # 筐采样（v21）：中断续跑时保住已采到的锚点，否则续跑段
                   # 只能从断点后重新采，前半段没有锚点可用
                   "hoop": [list(s) for s in (hoop or [])]}, f, ensure_ascii=False)
    os.replace(tmp, p)     # 原子替换，中断不会留下半截检查点


def _restore_positions(ckpt: dict) -> list[BallPos]:
    """恢复已持久化的检出点；按写入时记录的长度截断，丢弃截断点之后的脏数据。
    行格式 [t,x,y,area,(fi)]：第 5 项帧号 v4 起写入，旧检查点无则 fi=-1。"""
    raw = ckpt.get("positions", [])
    n = ckpt.get("positions_len", len(raw))
    return [BallPos(t=r[0], x=r[1], y=r[2], area=r[3],
                    fi=r[4] if len(r) > 4 else -1) for r in raw[:n]]


def _ktuple(key: list) -> tuple:
    """标定指纹 list → 可哈希 tuple（集合去重用；嵌套一层 list 递归转）。"""
    return tuple(tuple(x) if isinstance(x, list) else x for x in key)


def _parse_block(d: dict) -> dict:
    """positions.json 的一个段块（旧扁平顶层或新 segments 元素）→ 内部块
    {key, t0, t1, shift, positions, hoop}。t1=None=覆盖到片尾（旧扁平全片扫描、
    多段末段都是 None——块记录的是「实际扫过的窗」，复用时只看覆盖够不够，
    不看当年是谁的段）。
    hoop：v21 筐锚点采样（[t,cx,cy,w,h,conf] 画面像素）；旧档无此键 → []
    → 校正项为空 → 判定逐条等价 v20。
    hoop_ref：v21 参考帧上的模型筐框（绝对基准），旧档 None。
    """
    return {"key": d["calib"], "t0": float(d.get("t0", 0.0) or 0.0),
            "t1": (float(d["t1"]) if d.get("t1") is not None else None),
            "shift": [tuple(s) for s in d.get("shift", [])],
            "hoop": [list(s) for s in (d.get("hoop") or [])],
            "hoop_ref": (list(d["hoop_ref"]) if d.get("hoop_ref") else None),
            "positions": _restore_positions({"positions": d.get("positions", []),
                                             "positions_len": d.get("positions_len")})}


def _blocks_from_doc(doc: dict | None, video_path: str) -> list:
    """positions.json → 块列表。身份不符（别的视频/检测器版本）或格式损坏
    → []（整档作废）。多段格式按段块收；旧扁平格式视作 t0=0、覆盖全片的
    单块——这就是 09-04_1 实况的复用路径：单标定全片旧存档，事后补头段时
    尾段 95 分钟缓存原样吃回。

    2026-11-07 由「{指纹: 块}」改回列表：两段**标定指纹完全相同**很常见
    （同一机位补一段、参考帧内容一致，如 10-17_1 的 seg1/seg2），字典去重
    会让后段覆盖前段，前段查不到块就重扫、而重扫又只写回自己的块、把其他
    段的缓存**整批冲掉**（10-17_1 实测：4 段存档缩成 1 段）。改由
    `_find_block` 按「指纹 + t0」匹配，同指纹多段各归各位。"""
    if not doc or doc.get("video") != video_path \
            or doc.get("detect_ver") != detect_fingerprint():
        return []
    try:
        raw = doc.get("segments")
        return ([_parse_block(s) for s in raw] if raw is not None
                else [_parse_block(doc)])    # 旧扁平：顶层即单块
    except (KeyError, TypeError, ValueError):
        return []


def _find_block(blocks: list, seg, t0: float, t1: float | None,
                win_end_hint: float) -> dict | None:
    """在块列表里找本段对应的块。

    两条路径，顺序不能反：
    1. **指纹相同且 t0 相差 ≤0.5s** —— 同机位补段（两段指纹完全一样，如
       10-17_1 的 seg1/seg2）靠 t0 各归各位，绝不串段；
    2. 找不到就近块时退回「指纹相同 + 窗口罩住本段」——旧扁平存档是单标定
       全片扫描（t0=0、t1=None），补头段后尾段 t0=3.0 也要能吃到它
       （09-04_1 的实况复用路径，行为与旧版一致）。
    """
    cand = [b for b in blocks if b["key"] == seg.key]
    if not cand:
        return None
    near = [b for b in cand if abs(b["t0"] - float(seg.t0)) <= 0.5]
    if near:
        return min(near, key=lambda b: abs(b["t0"] - float(seg.t0)))
    for b in cand:
        if _block_covers(b, t0, t1, win_end_hint):
            return b
    return None


def _trim_to_window(block: dict, t0: float, t1: float | None) -> tuple:
    """块按段窗 [t0, t1) 裁剪 → (positions, shift, hoop)（时间域，检出点自带的
    PTS 时钟）。段边界在帧号域划定，可变帧率下 帧号/fps 与 PTS 实测差
    ~3s（v4 取证）——相邻两段的扫描窗按 DRIFT_PAD 外扩互相搭接，这里
    按时间严格切分，既不丢也不重。"""
    def keep(t):
        return t >= t0 - 1e-6 and (t1 is None or t < t1 - 1e-6)
    positions = [b for b in block["positions"] if keep(b.t)]
    shift = [s for s in block["shift"] if keep(s[0])]
    hoop = [s for s in (block.get("hoop") or []) if keep(s[0])]
    return positions, shift, hoop


def _block_covers(block: dict, t0: float, t1: float | None,
                  win_end_hint: float) -> bool:
    """块的实际扫过窗是否罩住段窗 [t0, t1)（t1=None=段到片尾，用文档 meta
    的时长兜底判定——块本身 t1=None 即已扫到片尾）。

    2026-11-07 修正：`t1` 非 None 的段，原实现把 `end` 赋成 `t1` 自身再拿它
    比 `t1`，恒为真——于是「同指纹的另一段」的块会被当成覆盖本段，整段拿
    错数据判定（10-17_1 的 seg2 实际拿到 seg1 的 0~18.8min）。现在必须
    真的读到块的 t1 且它罩到段尾。"""
    if block["t0"] > t0 + 0.05:
        return False
    if t1 is None:
        return block["t1"] is None or block["t1"] >= win_end_hint - 0.05
    return block["t1"] is not None and block["t1"] >= t1 - 0.05


# v14 CR：冷却释放的最小筐宽（px）。小于此的远机位段不放行——网信号
# 局部性与视觉判读在小筐上双双失真（08-29_3 35px：CR 预演 2 条自动 high
# 假绿，筐区拼图对抗性二审一例判打筐沿弹出、一例无法确认）。阈值与
# net_motion 小筐分档（E1 议案）同源。
NET_RELEASE_MIN_HW = 45


def _cooldown_release_pass(video_path: str, work_dir: str, r: dict, pts: list,
                            evs: list, pre_status: list,
                            stage_cb=None) -> list:
    """v14 冷却释放二趟：网分降档(high→非 high)的行不占冷却钟 → 其后 3s
    窗内被吞的候选重判浮现（篮板补篮取证：前一投被网门降级、真进球
    2~3s 后被钟吞，09-12 五已知漏检中的两例、09-04 55:12 均此形态）。

    加法式——基线行一趟为准原样保留，二趟只附加新行，零丢失 by
    construction（08-22 4508 教训：二趟兜底伪点集不同可令同行漂移，绝不
    拿二趟行顶替一趟行）。放行条件（缺一即原样返回，默认路径零行为
    变化）：本段确有降级行，且筐宽 ≥NET_RELEASE_MIN_HW。新行过同款网证据
    pass（测网+网分+重分箱；sidecar 缓存命中时零视频 IO）定终档后追加。

    evs：一趟判定+网门后的事件（net_evidence_pass 原地改 status、原序
    原长返回）；pre_status：网门前的状态快照——两者 zip 出降级集。"""
    demoted = {round(e.t, 3) for s, e in zip(pre_status, evs)
               if s == "high" and e.status != "high"}
    if not demoted or r["calib"].hoop_rect[2] < NET_RELEASE_MIN_HW:
        return evs
    keep_ts = {round(e.t, 3) for e in evs} - demoted
    redecided = decide_goals(pts, r["calib"], cooldown_keep_ts=keep_ts)
    fresh = [e for e in redecided
             if not any(abs(e.t - b.t) <= 0.05 for b in evs)]
    if not fresh:
        return evs
    fresh = net_evidence_pass(video_path, work_dir, r["calib"], r["shift"],
                              fresh, stage_cb=stage_cb)
    return evs + fresh


def _merge_segment_events(per_seg_events: list, t0s: list) -> list:
    """各段已判定事件 → 全片列表：按 t 排序 + 跨段冷却去重。

    段边界两侧常见同一颗球在两套几何里各判一次（扫描窗按 DRIFT_PAD 搭接
    过），或旧几何垃圾贴边漏检成 near-duplicate——相邻事件来自不同段且
    间隔 <COOLDOWN_SEC 时弃后保前，复刻判定器单段内的冷却语义
    （goal_decider 段内同款去重）。单段调用（列表长 1）应零变化。"""
    tagged = []
    for si, evs in enumerate(per_seg_events):
        tagged.extend((e, si) for e in evs)
    tagged.sort(key=lambda p: p[0].t)
    out: list = []
    for e, si in tagged:
        if out and out[-1][1] != si and e.t - out[-1][0].t < COOLDOWN_SEC:
            continue
        out.append((e, si))
    return [e for e, _ in out]


def _save_positions(work_dir: str, video: str, segs: list, blocks: list,
                    meta: dict):
    """检出流存档（与检查点不同：完成后长期保留）。blocks 与 segs 等长
    对位——每段一个块 {t0, t1(实际扫过窗尾，None=到片尾), calib, shift,
    positions}。身份键 = 视频+检测器版本（指纹在各段块内）——判定器升级
    （LOGIC_VERSION+）不 invalidate，下次只重放判定器（秒级）；检测器升级
    （DETECT_VERSION+）则整档作废重检。
    铁律：单段且 t0=0 仍写旧扁平格式（顶层 calib/shift/positions）——
    存量工具/测试直接读顶层键，格式漂移=全量重扫。"""
    p = os.path.join(work_dir, POSITIONS_FILE)
    tmp = p + ".tmp"

    def block_json(b: dict) -> dict:
        return {"t0": b["t0"], "t1": b["t1"], "calib": b["key"],
                "detect_ver": detect_fingerprint(),
                "shift": [list(s) for s in b["shift"]],
                # v21 筐锚点采样：**与上面 shift 同源**（都是在线检测时记的），
                # 校正项不存——它由 build_correction(hoop, shift, calib,
                # hoop_ref) 可复算，存两份会允许二者不同步
                "hoop": [list(s) for s in (b.get("hoop") or [])],
                "hoop_ref": b.get("hoop_ref"),
                "positions": [[p_.t, p_.x, p_.y, p_.area, p_.fi]
                              for p_ in b["positions"]],
                "positions_len": len(b["positions"])}

    with open(tmp, "w", encoding="utf-8") as f:
        if len(segs) == 1 and not segs[0].t0 and len(blocks) == 1:
            b = blocks[0]
            json.dump({"video": video, "calib": b["key"],
                       "detect_ver": detect_fingerprint(), "meta": meta,
                       "shift": [list(s) for s in b["shift"]],
                       "hoop": [list(s) for s in (b.get("hoop") or [])],
                       "hoop_ref": b.get("hoop_ref"),
                       "positions": [[p_.t, p_.x, p_.y, p_.area, p_.fi]
                                     for p_ in b["positions"]],
                       "positions_len": len(b["positions"])},
                      f, ensure_ascii=False)
        else:
            json.dump({"video": video, "detect_ver": detect_fingerprint(),
                       "meta": meta, "segments": [block_json(b) for b in blocks]},
                      f, ensure_ascii=False)
    os.replace(tmp, p)


def _load_positions(work_dir: str) -> dict | None:
    """读检出流存档；任何损坏（半截 JSON/非 UTF-8/非 dict）作废返回 None。"""
    p = os.path.join(work_dir, POSITIONS_FILE)
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _resume_frame(ckpt: dict, positions: list[BallPos]) -> int:
    """计算续跑起点。

    检查点里的 positions 是"最后一次写检查点时"已持久化的点（on_detect
    实时入库，但落盘只在周期性 checkpoint 时发生）；其后到中断之间的检出
    未持久化。因此不能用 frame_idx 恢复（会丢这段检出），要从最后一个
    已存点的下一帧重跑（无点则从头跑）：重跑区间重新产出这些点，与已存
    点既不丢也不重。优先用检出点自带的帧号 fi（v4 起 t 为 PTS，VFR 下
    t×fps 反算帧号会偏差数十帧）；旧检查点无 fi 才回退 t×fps 估算。"""
    if not positions:
        return 0
    if positions[-1].fi >= 0:
        return positions[-1].fi + 1
    fps = safe_fps(ckpt.get("fps", 0))
    return int(round(positions[-1].t * fps)) + 1


def _normalize_meta(meta: dict) -> list[str]:
    """duration/fps 异常兜底（meta 原地净化），返回附加警告列表。"""
    notes = []
    raw_fps = meta.get("fps", 0)
    fps = safe_fps(raw_fps)
    if fps != raw_fps:
        meta["fps"] = fps                          # 持久化净化后的值
        notes.append(FPS_NOTE.format(raw_fps))     # 原值异常要告知
    if meta.get("duration", 0) <= 0:
        meta["duration"] = meta["processed_frames"] / fps
        notes.append(DURATION_NOTE)
    if fps <= SUSPECT_FPS_LOW or fps >= SUSPECT_FPS_HIGH:
        notes.append(FPS_NOTE.format(fps))
    if meta.get("pts_clock") is False:
        notes.append(PTS_NOTE)
    return notes


def _opencv_can_open(video_path: str) -> bool:
    """OpenCV 能否解这个视频（决定是否走 ffmpeg 转码兜底）。"""
    cap = cv2.VideoCapture(video_path)
    try:
        return cap.isOpened()
    finally:
        cap.release()


# 多段标定的段边界搭接（秒）。段窗在帧号域划定（seek 按帧号），可变帧率
# 视频 帧号/fps 与 PTS 实测差 ~3s（v4 取证 stodownload 系列前半段
# +2.7~3.0s）——相邻段各向对方侧多扫这么多，之后按检出点自带的 PTS 时钟
# 在时间域严格裁剪，边界既不丢也不重（时间裁剪才是唯一真相源）。5.0 对
# 3.1s 观测最大值留余量；代价仅边界各多扫几秒。
DRIFT_PAD = 5.0


def _hoop_out_kwargs(hoop_out: list) -> dict:
    """`hoop_out` 是 v21 给检出层新加的可选形参（筐锚点采样容器）。检出器可
    被环境变量/测试替换成任意实现（含第三方桩），不支持的不能传——否则整段
    检测直接 TypeError。不支持时退回「无筐采样」，判定等价 v20。"""
    try:
        if "hoop_out" in inspect.signature(detect_positions).parameters:
            return {"hoop_out": hoop_out}
    except (TypeError, ValueError):
        pass
    return {}


def _hoop_ref_center(ref_path: str | None, video_size,
                     calib_rect=None) -> list | None:
    """标定参考帧上的模型筐框中心（绝对基准）。不可得 → None（放弃校正）。

    三种放弃情形都是「宁可退回纯 ORB」：HSV 检出器没有 hoop 类；参考帧读不
    出来；参考帧与检测画面分辨率不同（标定框会在 `_scaled_rect` 里被缩放，
    参考帧上不能再缩一次，否则双重缩放）。
    """
    if ref_path is None or not hoop_track.enabled() or DETECTOR_NAME != "yolo":
        return None
    img = load_ref_image(ref_path)
    if img is None:
        return None
    vw, vh = (video_size or (0, 0))[:2]
    if vw and (img.shape[1], img.shape[0]) != (int(vw), int(vh)):
        return None
    from . import detector_yolo
    try:
        got = detector_yolo.hoop_center_of(img, calib_rect)
    except (OSError, RuntimeError):     # 权重缺失/推理失败 → 不校正，不崩
        return None
    return list(got) if got else None


def _hoop_apply(positions: list[BallPos], shift: list, hoop: list, calib,
                ref_center: list | None):
    """筐锚点校正：→ (positions, shift, notes)。

    corr 为空（旧档无采样/无绝对基准/样本不足/开关关闭）→ **原样返回同一
    对象**，判定路径逐字节等价 v20。启用时 positions 平移到「筐位已校正」
    的坐标系，shift 同步加上校正项——网证据 pass 用它把网区 ROI 从标定
    坐标系搬到画面，两者必须同一套坐标，否则筐位偏得多时等于测错区域。

    v22 起**搬点默认关**（hoop_track.shift_enabled()）：关掉时这里直接早退，
    c(t) 不会算、三条锚点类提示也不会出——positions 一律原样返回，判定与
    v20 逐条等价。净亏实测见 hoop_track.HOOP_SHIFT_ENABLE 注释；桥接
    （段间补标定）不吃这个开关。
    """
    notes: list[str] = []
    if not hoop or not hoop_track.enabled():
        return positions, shift, notes
    if not hoop_track.shift_enabled():
        # v22 默认：搬点关（HOOP_SHIFT_ENABLE=False）。校正项不参与判定时，
        # 「疑似变焦 / 锚点异常 / 无参考帧」三条提示同时失去意义——它们的
        # 文案写的是「位置按锚点跟了」「本次未采用锚点校正」，在搬点关闭时
        # 是假话或纯噪音（用户从未启用校正，却被告知校正出了问题），
        # HOOP_ABNORMAL_NOTE 还会反指「请检查标定是否标错筐」——把用户引去
        # 改一份本来正确的标定。所以这里连 stats 都不求：省一次参考帧推理，
        # 也杜绝在健康数据上弹误导文案。要看这些诊断 → BBH_HOOP_SHIFT=1。
        return positions, shift, notes
    corr, st = hoop_track.build_correction(hoop, shift, calib.hoop_rect,
                                           ref_center)
    if not corr:
        if st["why"] == "no_ref":
            notes.append(HOOP_NO_REF_NOTE)
        elif st["why"] in ("too_few", "size_out") and st["n_size"]:
            notes.append(HOOP_ZOOM_NOTE.format(n=st["n_size"]))
        return positions, shift, notes
    if st["max_res"] > HOOP_MAX_RES:
        notes.append(HOOP_ABNORMAL_NOTE.format(res=st["max_res"]))
        return positions, shift, notes
    tie = st["n_size"] / st["n_in"] if st["n_in"] else 0.0
    if tie > HOOP_ZOOM_RATIO:
        notes.append(HOOP_ZOOM_NOTE.format(n=st["n_size"]))
    if not hoop_track.shift_enabled():
        return positions, shift, notes      # 只出提示，不搬点（v22 默认）
    return (hoop_track.remap_positions(positions, corr),
            hoop_track.remap_shift(shift, corr), notes)


def _normalize_segments(calib, ref_image: str | None) -> list:
    """归一化检测输入：单标定 → 全片一段（t0=0，行为与旧版逐字节一致）；
    段列表 → 校验后按 t0 升序。首段必须从 0 起（之前的时段没有任何几何
    可用，宁可不跑也不静默错判——错误数据比没数据更糟）。"""
    if isinstance(calib, Calibration):
        return [CalibSegment(0.0, calib, ref_image)]
    segs = [CalibSegment(float(s.t0), s.calib, s.ref_image) for s in calib]
    if not segs:
        raise ValueError("标定段列表为空")
    segs.sort(key=lambda s: s.t0)
    if segs[0].t0 > 0:
        raise ValueError("第一段标定必须覆盖视频开头（t=0）")
    for a, b in zip(segs, segs[1:]):
        if round(b.t0 - a.t0, 3) <= 0:
            raise ValueError("标定段起点必须严格递增（两段 t0 相同）")
    return segs


def run_detection(video_path: str, calib, work_dir: str,
                  force: bool = False, progress_cb=None, stage_cb=None,
                  cancel_cb=None, ref_image: str | None = None) -> tuple[str, str | None]:
    """跑完检测+判定。返回 (events.json 路径, warning|None)。
    calib：单标定（Calibration，全片一段）或多段标定（list[CalibSegment]，
    2026-09-12 A2：视频中途挪镜头后按时间段各用各的几何，各段各自配准/
    检测/判定后合并，段边界按冷却去重）。
    三级快慢路径：① events.json 复用——存在、未 force、身份相符（同视频
    路径 + 同段组合指纹 + 同逻辑版本）时直接返回；② positions.json 检出流
    复用——段块按标定指纹匹配、按段窗裁剪（09-04_1 实况：单标定全片旧
    存档，事后补头段时尾段缓存原样吃回，只重扫头段），全命中时跳过视频
    IO 秒级重放判定器；③ 缺失的段各自扫描（检查点续跑，段完成即增量
    落盘，中断不丢已完成段）。
    ref_image：标定参考帧图片路径（单标定入参/段列表时各段自带；第一段
    缺省时回退本参数）。None/读取失败 → 该段不做镜头位移校正，附警告
    （不崩不静默）。
    progress_cb(frame_idx, total)：检测进度回调（全局帧号），与内部检查点
    落盘并存；复用路径（①②）直接返回，不会触发 progress_cb。
    stage_cb(msg|None)：阶段性步骤提示（转码/测网），期间无帧进度可报，
    靠它告知界面在干什么而不是卡死。
    cancel_cb()→bool：每个进度节拍（≥30 帧或 ≥2 秒，见 detector.REPORT_*）
    查询，True 则取消——已检出点落检查点后抛 DetectionCancelled（界面在
    页面刷新/关闭时用它中止孤儿检测，重新点开始可从断点续跑）。转码阶段
    （阻塞 ffmpeg）不响应取消。
    force：跳过 ①② 复用，全片重检（身份相符的检查点仍续跑）。"""
    os.makedirs(work_dir, exist_ok=True)
    events_path = os.path.join(work_dir, EVENTS_FILE)
    segs = _normalize_segments(calib, ref_image)
    skey = segments_key(segs)

    if os.path.exists(events_path) and not force:
        data = load_events(events_path)
        if (data["video"] == video_path
                and data.get("calib_key") == skey
                and data.get("logic_ver") == LOGIC_VERSION):
            return events_path, data.get("warning")
        # 身份不符 → 视同无 events，继续（覆盖旧文件）

    notes: list[str] = []
    meta: dict = {}

    # ---- ② 段块缓存匹配（缺失的段走 ③ 扫描）----
    doc = None if force else _load_positions(work_dir)
    blocks = _blocks_from_doc(doc, video_path)
    doc_meta = (doc or {}).get("meta") or {}
    win_end_hint = float(doc_meta.get("duration", 0) or 0)
    if win_end_hint <= 0:
        blocks = []     # meta 缺时长=段覆盖没法判定，整档视同无（同旧口径）

    resolved: list[dict] = []   # 每段 {positions, shift, calib, t0, t1, active}
    persist_blocks: list[dict] = []   # 落盘块（复用段=原文档块原窗，扫描段=新块）
    det_src = None              # 懒初始化：全缓存重放不碰视频/转码
    fps_hint = None

    for si, seg in enumerate(segs):
        t1 = segs[si + 1].t0 if si + 1 < len(segs) else None
        ref = (seg.ref_image if seg.ref_image is not None
               else (ref_image if si == 0 else None))
        n = _ref_note(ref)                # 参考帧状态两路径同一套文案
        if n:
            if n not in notes:            # 多段同病只报一次（文案去重）
                notes.append(n)
        blk = _find_block(blocks, seg, seg.t0, t1, win_end_hint)
        if blk is not None and _block_covers(blk, seg.t0, t1, win_end_hint):
            # ② 该段命中检出流缓存：裁剪到段窗即用
            seg_positions, seg_shift, seg_hoop = _trim_to_window(blk, seg.t0, t1)
            # v21：筐锚点校正（旧档无 hoop 字段 → 原样返回，等价 v20）
            seg_positions, seg_shift, hnotes = _hoop_apply(
                seg_positions, seg_shift, seg_hoop, seg.calib,
                blk.get("hoop_ref"))
            for hn in hnotes:
                if hn not in notes:
                    notes.append(hn)
            resolved.append({"positions": seg_positions, "shift": seg_shift,
                             "calib": seg.calib, "t0": seg.t0, "t1": t1,
                             "active": n is None})
            persist_blocks.append(blk)    # 原窗原样写回（保留最大覆盖信息）
            continue

        # ---- ③ 该段扫描（检查点续跑）----
        if det_src is None:               # 首个需扫描的段：判转码+探 fps 一次
            det_src = video_path
            if not _opencv_can_open(video_path):
                det_dst = os.path.join(work_dir, TRANSCODE_FILE)
                if not os.path.exists(det_dst):
                    if stage_cb:
                        stage_cb("视频编码不受 OpenCV 支持，正在转码 H.264"
                                 "（约数分钟到几十分钟，只此一次）…")
                    transcode_for_detection(video_path, det_dst)
                    if stage_cb:
                        stage_cb(None)
                meta["transcoded"] = True
                det_src = det_dst
            fps_hint = _probe_fps(det_src)

        ckpt = _load_checkpoint(work_dir) or {}
        if (ckpt.get("video") != video_path or ckpt.get("calib") != seg.key
                or ckpt.get("detect_ver") != detect_fingerprint()
                or abs(float(ckpt.get("t0", 0.0) or 0.0) - seg.t0) > 0.05):
            ckpt = {}      # 检查点属于别的视频/标定/段，作废重扫该段
        seg_positions = _restore_positions(ckpt)
        seg_shift = [tuple(s) for s in ckpt.get("shift", [])]
        # v21 筐锚点采样：检查点预置 + 边检边 append（同一个 list，检查点
        # 存下去的就是全量，续跑不丢断点前的锚点）
        hoop_out: list = [list(h) for h in (ckpt.get("hoop") or [])]
        # fps 必须取自实际检测的文件：转码副本与原视频帧率可能不同，拿错
        # 会导致续跑起点换算错位（重复/跳帧）
        seg_fps = ckpt.get("fps") or fps_hint

        motion = None
        if n is None:
            motion = MotionTracker(load_ref_image(ref), samples=seg_shift)

        start_f = max(0, int(round((seg.t0 - DRIFT_PAD) * seg_fps)))
        end_f = int(round((t1 + DRIFT_PAD) * seg_fps)) if t1 is not None else None
        resume_frame = max(_resume_frame(ckpt, seg_positions), start_f)

        last_ckpt = time.monotonic()
        last_frame = resume_frame   # 取消时检查点用的最新帧号（回调间隔内兜底）

        def on_progress(frame_idx, total):
            nonlocal last_ckpt, last_frame
            last_frame = frame_idx
            if cancel_cb and cancel_cb():
                raise DetectionCancelled(frame_idx)
            if progress_cb:
                progress_cb(frame_idx, total)      # 界面进度（全局帧号）
            now = time.monotonic()
            if now - last_ckpt >= CHECKPOINT_SEC:
                _save_checkpoint(work_dir, frame_idx, seg_positions,
                                 video_path, seg_fps, seg.calib,
                                 motion.samples if motion else seg_shift,
                                 t0=seg.t0, hoop=hoop_out)
                last_ckpt = now

        try:
            _, det_meta = detect_positions(det_src, seg.calib,
                                           progress_cb=on_progress,
                                           resume_frame=resume_frame,
                                           on_detect=seg_positions.append,
                                           motion=motion, end_frame=end_f,
                                           **_hoop_out_kwargs(hoop_out))
        except DetectionCancelled:
            # 取消不丢进度：已检出点立即落检查点，重新点「开始」续跑该段
            _save_checkpoint(work_dir, last_frame, seg_positions,
                             video_path, seg_fps, seg.calib,
                             motion.samples if motion else seg_shift,
                             t0=seg.t0, hoop=hoop_out)
            raise
        det_meta["transcoded"] = bool(meta.get("transcoded"))
        meta = det_meta
        # 筐采样已单独进块，meta 里只留计数（否则单段扁平格式会重复存一份）
        meta.pop("hoop_samples", None)
        # 原始检出（ORB 坐标）+ 锚点采样 —— 落盘存这两样（校正项可复算，
        # 存校正后的坐标会让「同档缓存」在下一次读时被二次校正）
        raw_positions, raw_shift, seg_hoop = _trim_to_window(
            {"positions": seg_positions,
             "shift": list(motion.samples) if motion else [],
             "hoop": hoop_out},
            seg.t0, t1)
        # v21：筐锚点校正（positions + shift 同步平移；无采样则原样返回）。
        # 绝对基准只在有采样时才跑一次参考帧推理（无采样=旧档/关开关，零成本）。
        # 搬点关闭时**仍然算并落盘**：hoop_ref 是筐桥接（段间补标定）与事后
        # 诊断要用的绝对基准，丢了下一次就得重扫参考帧；它不影响判定。
        ref_center = (_hoop_ref_center(ref, meta.get("video_size"),
                                       seg.calib.hoop_rect)
                      if seg_hoop else None)
        eff_positions, eff_shift, hnotes = _hoop_apply(
            raw_positions, raw_shift, seg_hoop, seg.calib, ref_center)
        for hn in hnotes:
            if hn not in notes:
                notes.append(hn)
        resolved.append({"positions": eff_positions, "shift": eff_shift,
                         "calib": seg.calib, "t0": seg.t0, "t1": t1,
                         "active": n is None})
        # 段完成即增量落盘：之后调判定参数（LOGIC_VERSION+）秒级重判；
        # 多段跑中途取消，已完成的段不丢（下次直接吃缓存）
        persist_blocks.append({"key": seg.key, "t0": seg.t0, "t1": t1,
                               "shift": raw_shift,
                               "hoop": seg_hoop,
                               "hoop_ref": ref_center,
                               "positions": raw_positions})
        # 增量落盘：本段块 + **尚未被本次替换的旧块**。只写 persist_blocks 会
        # 把还没轮到的段整批冲掉——重扫某一段时中断/取消，其余段的缓存就没了
        # （2026-11-07 实测：10-17_1 重扫 seg1 期间中断，4 段存档缩成 1 段）。
        done = {(_ktuple(b["key"]), round(b["t0"], 1)) for b in persist_blocks}
        out_blocks = list(persist_blocks)
        if len(segs) > 1:      # 单段场次保持「只写本段」的旧语义（扁平格式）
            out_blocks += [b for b in blocks
                           if (_ktuple(b["key"]), round(b["t0"], 1)) not in done]
        out_blocks.sort(key=lambda b: b["t0"])
        _save_positions(work_dir, video_path, segs, out_blocks, meta)

    all_positions = [p for r in resolved for p in r["positions"]]
    all_shift = sorted((s for r in resolved for s in r["shift"]),
                       key=lambda s: s[0])
    if not meta:
        meta = doc_meta      # 全缓存重放：容器 meta 用文档里的（时长>0 已保证）

    notes = _normalize_meta(meta) + notes
    for r in resolved:       # A1 覆盖孔洞按段窗（段尾=窗尾不算孔洞）
        gap_note = _cal_gap_note(r["shift"], r["t0"],
                                 r["t1"] if r["t1"] is not None
                                 else meta.get("duration", 0),
                                 r["active"])
        if gap_note and gap_note not in notes:
            notes.append(gap_note)

    per_seg_events = []
    for r in resolved:
        # 静止链过滤：默认 b1m（补齐被静止链误删的悬停真球，见 bbh/static_filter.py）
        pts = static_filter.apply(r["positions"], r["calib"])
        evs = decide_goals(pts, r["calib"])
        pre_status = [e.status for e in evs]   # v14：网分降档集对照快照（v18 语义：几何 high 被网分压离 high 的行）
        # v12：篮网运动后置证据 pass（按段几何/位移）——sidecar 命中时零
        # 视频 IO，未命中才逐事件测（stage_cb 告知界面"在测网"而非卡死）
        evs = net_evidence_pass(video_path, work_dir, r["calib"], r["shift"],
                                evs, stage_cb=stage_cb)
        # v14：冷却释放二趟（网门降级行不占钟 → 释放其后被吞的候选，
        # 小筐段不放行；基线行原样保留、新行同款网门后附加）
        evs = _cooldown_release_pass(video_path, work_dir, r, pts, evs,
                                     pre_status, stage_cb=stage_cb)
        per_seg_events.append(evs)
    events = _merge_segment_events(per_seg_events, [s.t0 for s in segs])
    # v16（B2）：网簇兜底注入——须在跨段合并之后（对账用全片最终行集）、
    # save_events 之前；用原始检出流（非 drop_static——碎片正是证据）
    events = net_cluster_pass(video_path, work_dir,
                              [(r["t0"], r["calib"]) for r in resolved],
                              {r["t0"]: r["shift"] for r in resolved},
                              all_positions, events, stage_cb=stage_cb)
    # v24：穿筐物投影面积硬门——必须放在网分重分箱**之后**（在判定器里挂
    # review_reason 会让网分不再重分箱，把「几何够 high 但会被网分扣成 low」
    # 的行反而抬回 review），详见 goal_decider.apply_area_gate 注释。
    events = apply_area_gate(events)

    if not all_positions:
        notes.insert(0, CALIB_WARNING)
    if meta.get("transcoded"):
        notes.append(TRANSCODE_NOTE)
    warning = "\n".join(notes) if notes else None
    save_events(events_path, video=video_path, duration=meta["duration"],
                events=events, warning=warning, calib_key=skey,
                logic_ver=LOGIC_VERSION, motion=_motion_summary(all_shift))

    with contextlib.suppress(OSError):
        os.remove(os.path.join(work_dir, CHECKPOINT_FILE))   # 完成，清理检查点
    return events_path, warning
