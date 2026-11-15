"""进球判定状态机：球位置序列 → 进球事件（纯函数，无视频 IO）。"""
import bisect

from .models import BallPos, Calibration, GoalEvent

RIM_Y_RATIO = 0.35      # 筐口平面在矩形中的纵向位置
BAND_RATIO = 0.25       # 筐身条带半高（相对筐高）
MAX_CROSS_SEC = 0.7     # 上方点→下方点的最大间隔
X_MARGIN = 0.2          # 横向宽容（相对筐宽）
NEIGHBOR_SEC = 1.0      # 前后取点窗口
COOLDOWN_SEC = 3.0
HIGH_CONF = 0.60     # 2026-10-18 v19（R-020 Step2）：0.65→0.60，与网分加重配套
                     # 使用（六场查准 60.80→66.18%、查全 −0.65pct）。原 0.65 为
                     # 2026-10-11 八场定标拐点；本次口径升级全程回归对拍见
                     # yolo/reports/KPI_官方口径_v19.md
MIN_CONF = 0.5
CONT_SPAN_SEC = 0.2     # 证据点时间跨度达到此值 → 视为连续轨迹（证据分满档）
# 侧分「点数够（≥2）但跨度不足 CONT_SPAN_SEC」这一档（v23 2026-11-14：0.14 →
# 0.17）。真球 68% 的 above_span < 0.2（球速快、筐口上方停留短），假绿只有
# 23% —— 旧值等于系统性惩罚真球。补 0.03 后新六场（用户标注口径）自动档
# 813→863、命中 620→659、假绿 193→204、降级 166→127：查准 +0.10pct、
# 覆盖 +4.96pct，边际查准 78.0% 高于存量 76.3%。L0+L1 双验、93 条改档
# 全为升档零降档，见 诊断/2026-11-14-优化候选/。
SIDE_SHORT = 0.17
# 穿越点距筐心（筐宽比例）→ 位置分：越近筐心越可信，分档平滑而非一刀切
POS_TIERS = [(0.1, 0.20), (0.2, 0.17), (0.3, 0.14), (0.4, 0.10), (0.5, 0.06)]
# 规则A（几何位置，2026-08-19 误判调优）：off=球心穿越筐口平面的横向偏移
# （筐宽比例）。标定余量 5% 时筐口两端≈±0.45——球心出两端=从筐旁飞过，
# 几何上不可能进球，位置分不该只是"不加分"而应是门槛（实测 13 条误判中
# 4 条侧沿类 off 0.54~0.66 靠 0.3+0.2+0.2 拿到 0.7 high）。0.30~0.45 为
# 球心压筐沿区（干净穿过极限 ≈0.23，含滚进 ≈0.27，实测真进球 ≤0.31）。
CROSS_KILL_OFF = 0.45    # |off| 超过 → 不生成事件
CROSS_REVIEW_OFF = 0.30  # |off| 超过 → 强制待复核（多半打铁/弹出）
# 像素下限（v9 2026-08-20，取证 work/_v8_analysis/shift_eval.py）：阈值取
# max(相对值, 下限/筐宽)。下限的职责只有一项——保证容差换算成像素后不小于
# 系统的绝对测量误差（标定点击 ±2px + 穿越点插值 ±2px + 位移配准残差 ±2px
# ≈ 6px；这些误差不随筐大小缩放，筐很小（<13px 宽）时纯相对容差会小于
# 误差本身、把真进球交给噪声误杀）。筐宽 ≥13px 的现实机位上下限永不生效，
# 规则A 按纯相对 0.45/0.30 工作、随拍摄远近自动缩放。
# v8 曾用 24/16px——那是把镜头漂移 +32px 也算进误差预算（配准落地前的
# 止痛）；配准修根后消融实测 24/16 与 6/4 对真进球判定完全相同（15 high），
# 且 24px 在筐 25px 的更远机位上会使下限=0.96 筐宽、规则A 静默失效。
CROSS_KILL_MIN_PX = 6.0
CROSS_REVIEW_MIN_PX = 4.0
POPBACK_MIN_SEC = 0.25   # 弹出窗口下限：穿后立即回上方的是噪声/他物
                         # （实测真进球 0.04s 的上方邻点会误触发，真弹出 0.42s）
POPBACK_SEC = 1.2        # 下穿后此窗口内球又回筐口上方 → 疑似弹出
# v18 档位语义（2026-10-11 问题②，spec docs/superpowers/specs/
# 2026-10-11-net-confidence-design.md §3.3/3.4）：档位 = 硬门档 if 硬门
# else 阈值分箱(conf)。v10 时代的 R1 升绿 / P1d 降档两条 status 旁路删除
# ——其输入特征（off/above_n/below_span）全在两侧分/位置分的自变量里，
# 统一公式后保留只会再造「算式解释不了的档位」。R1 曾救的被遮挡干净真球
# 掉回 review 交人工；八场定标验证查全影响，劣化再按 spec §3.4 转几何分
# 修正项（加在网分之前）。

STATIC_R_RATIO = 0.25   # 静态聚类格子边长（相对筐宽）
STATIC_BURST_GAP = 0.5  # 同格相邻检出间隔超过此值 → 链断开
STATIC_BURST_MIN_N = 3  # 静止链 ≥3 个检出 → 整链丢弃
STATIC_JITTER_PX = 3.0  # 链内逐点步距上限（固定物检出噪声级，实测 ≤3px；
                        # 真球相邻采样步距 8px 起，只会断链不会被删）
DESCENT_RATIO = 0.16    # 下落步长阈值（相对筐高）：真球穿筐后相邻检出 y 增量
DESCENT_MIN_PX = 3.0    # 下限像素（标定框极小时兜底）
DESCENT_RUN_GAP = 0.25  # 下方点间隔超过此值 → 属另一次下穿，不跨段比下落
ENTITY_AREA_RATIO = 0.10  # 下落实体面积下限（相对筐矩形面积）：真球检出
                         # 104~524px²、筐下固定物/皮肤碎片 ≤78px²（30min3
                         # 全量取证 2026-08-17，93% 的筐邻域检出是后者）。
                         # v8（2026-08-20）删 130px² 绝对底——720p 机位真球
                         # 仅 50~100px² 达不到绝对底，「实体校验」反成专杀
                         # 真球放行人肤大块；纯相对阈值下大筐 0.1×筐面积
                         # =133px² 与旧 max(130,133) 行为不变。

# ── 穿筐物投影面积硬门（v24 2026-11-14）──────────────────────────────
# conf 只吃 above_n / above_span / below_n / below_span / cross_off 五个
# 标量，不认识「穿筐口的是什么东西」。手/小臂/身体横穿筐口平面时点数与
# 时间跨度都够、球心还可能正好落在筐口中间 → 几何分拿满，稳稳混进 high
# 变成「会剪错」的假绿。positions.json 每点带检出框面积（此前只被
# ENTITY_AREA_RATIO 当过低限门），这里补**上限门**。
#
# 阈值依据（新六场 v23 基线 863 high = 命中 659 + 假绿 204，用户标注口径）：
#   · 真球穿筐时投影面积 ≈ 筐矩形面积的 12~16%（中位 0.122、最大 0.488）
#   · 假绿里那批大块物是**同场真球面积的 4.1~6.8 倍**（中位 4.9 倍）
#   · 0.50 落在实测真空档里：命中最大 0.488 / 假绿最小 0.516
# 实测效果：降档 33 条假绿、**误降 0 条真球** → 查准 76.36%→79.40%，
# 自动档覆盖 83.84% 一分不掉（high 863→830、假绿 204→171）。
# 无视频佐证：这 33 条里 17 条有用户 feedback `not_goal`、**0 条 `goal`**；
# 且 a_c/a_up 中位仅 1.37（若「球被手挡住粘连」该比值会是 3~5 倍）→
# 穿越之前它就已经那么大，是手本身而不是球。
# 稳健性：6 场中 5 场抓到（10-17 为 0）、留一法六折阈值全落 0.51 误伤恒 0、
# 11-07 样本外抓 0 伤 0。见 诊断/2026-11-14-优化候选/阶段6-面积特征结论.md
AREA_KILL_REL = 0.50


# ── 冷却身份判据（v20 2026-11-01）────────────────────────────────────
# 冷却原为「距上一个已接受事件 <3s 的候选丢弃」（2026-08-15 原始设计），
# 目的是挡同一颗球在筐口/网内反复穿越被配成多行。它用「时间」代理
# 「身份」：3s 窗内「一颗球穿两次」和「两颗球各穿一次」在时间上无法区分，
# 篮板补篮（前一投之后 1~2.5s 的第二颗球）因此被静默删除、界面上连 low
# 行都不出现。v20 改成先判身份再决定吞不吞：两行之间球若重新走完一遍
# 「离开筐邻域 → 从高处/侧面回来」的接近轨迹，判另一颗球、放行；否则
# 仍按冷却吞。取证（诊断/2026-11-01-冷却验证/）：三场 87 条增量里 31 条
# 是另一颗球（high 20 / review 5 / low 6）、56 条是同球重复（high 21 /
# review 27 / low 8）；0.3s 桶 21 条 0 条判另一颗球（物理上不可能穿两次），
# 1.5~3s 桶 43 条里 22 条判另一颗球（补篮球节奏）——两端都不是随机分布。
NEWBALL_ENABLE = True      # 总开关：False = 完全回到 v19 的无条件冷却
NEWBALL_MIN_UP = 2         # 两行之间的筐上方点最少个数（单点是噪声）
NEWBALL_MIN_SPAN = 0.2     # 这些上方点的最小时间跨度（单帧抖动不成轨迹）
NEWBALL_HIGH_H = 2.0       # 「高处点」：高于筐口 2 倍筐高 → 球确实出去过
NEWBALL_SIDE_W = 1.3       # 「侧移点」：离筐心超过 1.3 倍筐宽 → 去捡球了
NEWBALL_EDGE_SEC = 0.02    # 区间两端各留的缝，不把两行自身的点算进来


def _new_ball_since(positions: list[BallPos], pos_ts: list[float],
                    t_from: float, t_to: float, cx: float, hw: float,
                    hh: float, rim_y: float, band: float,
                    x_lo: float, x_hi: float) -> bool:
    """两行之间球是否重新走完一遍接近轨迹 → 是则判「另一颗球」。

    三条同时成立才算（缺一即视作同一颗球的重复配对，仍按冷却吞）：
      1. 筐上方点 ≥2 个——同一颗球重复配对时球还在筐里，凑不出轨迹；
      2. 这些上方点的时间跨度 ≥0.2s——不是同帧抖动；
      3. 其中含「高处点」（y < rim_y − 2×筐高）或区间内有「侧移点」
         （|x − 筐心| > 1.3×筐宽）——补篮必须先掉到筐下、被捡起、再从
         远处或高处举回来，物理上绕不开这两个位置之一。
    补篮真实形态：入筐 → 落筐下 → 抢板的人捡起 → 从筐上方/侧面重新
    举起 → 再进；同球重复形态：球一直在筐口附近，只有贴筐的 1~2 个
    上方点。两者的区别就是「球有没有离开过筐邻域再回来」。

    侧移点不受横向容差限制（去捡球时球必然在筐侧很远处），故扫全部
    原始点。positions 须按 t 升序，pos_ts 是其时间列（调用方建一次复用）。
    """
    if not NEWBALL_ENABLE:
        return False
    lo = bisect.bisect_right(pos_ts, t_from + NEWBALL_EDGE_SEC)
    hi = bisect.bisect_left(pos_ts, t_to - NEWBALL_EDGE_SEC)
    if hi - lo < NEWBALL_MIN_UP:
        return False
    side = False
    up: list[BallPos] = []
    for p in positions[lo:hi]:
        if not side and abs(p.x - cx) > NEWBALL_SIDE_W * hw:
            side = True
        if x_lo <= p.x <= x_hi and p.y < rim_y - band:
            up.append(p)
    if len(up) < NEWBALL_MIN_UP:
        return False
    if up[-1].t - up[0].t < NEWBALL_MIN_SPAN:
        return False
    high_h = rim_y - NEWBALL_HIGH_H * hh
    return side or any(p.y < high_h for p in up)


def drop_static_positions(positions: list[BallPos],
                          calib: Calibration) -> list[BallPos]:
    """滤掉停在原地的检出（篮网结点、筐下固定橙色物、被抱住的球）。

    静态点单独不产生进球，但球从筐上方飞过（未进的球）时会被当成
    "球已落筐下"，拼出高置信误报——必须滤。判据按格子（边长 = 1/4 筐宽）
    内的「静止链」：相邻检出间隔 ≤0.5s 且逐点步距 ≤3px（检出噪声级，
    x/y 各轴）延续同链，链长 ≥3 → 整链丢弃。真球相邻采样步距 8px 起
    （合成/实测皆然），只会把链断开成 1~2 点的碎片，不受影响；球恰好
    穿过固定物所在格时链被打断，固定物在球经过前后各自成链照样被删。

    旧版按"全视频跨度 ≥3s 且平均密度 ≥2/s"整格删，间歇被遮挡出现的
    固定物（每段短、段间稀疏）平均密度永远不达标 → 漏网（2026-08-16
    真实视频取证：筐下 y≈476-484 固定结构带漏网 ~2500 点，制造了 t=164.61
    等幽灵进球）。按链删后间歇小段同样被清除。
    输入须按 t 升序（detector 输出天然满足）。"""
    if len(positions) < 2:
        return list(positions)
    _, _, hw, _ = calib.hoop_rect
    r = max(4.0, hw * STATIC_R_RATIO)
    cells: dict[tuple[int, int], list[int]] = {}
    for i, p in enumerate(positions):
        cells.setdefault((int(p.x // r), int(p.y // r)), []).append(i)
    static_ids: set[int] = set()

    def close_chain(chain: list[int]) -> None:
        if len(chain) >= STATIC_BURST_MIN_N:
            static_ids.update(chain)

    for idxs in cells.values():          # 输入有序 → 每格的索引按 t 升序
        chain = [idxs[0]]
        for i in idxs[1:]:
            prev = positions[chain[-1]]
            cur = positions[i]
            if (cur.t - prev.t > STATIC_BURST_GAP
                    or abs(cur.x - prev.x) > STATIC_JITTER_PX
                    or abs(cur.y - prev.y) > STATIC_JITTER_PX):
                close_chain(chain)
                chain = []
            chain.append(i)
        close_chain(chain)
    return [p for i, p in enumerate(positions) if i not in static_ids]


def _side_score(n: int, span: float) -> float:
    """单侧证据分：点数为主、时间跨度佐证——连续弧线（跨度 ≥0.2s）满档，
    只凑够点数打折，单点微量，零点无分。"""
    if n >= 2 and span >= CONT_SPAN_SEC:
        return 0.20
    if n >= 2:
        return SIDE_SHORT
    if n == 1:
        return 0.05
    return 0.0


def _pos_score(off: float) -> float:
    """穿越点距筐心（筐宽比例）→ 位置分。旧版是 <0.3 加 0.15+0.05 的断崖，
    现分档平滑：擦筐边缘进球（偏移大）得到的可信度连续下降而非骤降。"""
    for lim, score in POS_TIERS:
        if off <= lim:
            return score
    return 0.0


def _cross_x(prev_up: BallPos, cross: BallPos, rim_y: float) -> float:
    """球心穿越筐口平面时的 x：上/下检出点连线内插。斜飞的球从上方检出到
    下方检出要位移几十像素，直接取"下方首点 x"当穿越点会偏 0.2~0.4 筐宽
    （2026-08-19 取证：判定记录 off 与内插真值差最多 0.4），内插才配得上
    规则A 的几何阈值。退化（点间隔异常）时回退下方点 x。"""
    dy = cross.y - prev_up.y
    if dy <= 0:
        return cross.x
    frac = (rim_y - prev_up.y) / dy
    if not 0.0 < frac < 1.0:
        return cross.x
    return prev_up.x + (cross.x - prev_up.x) * frac


def decide_goals(positions: list[BallPos], calib: Calibration,
                 cooldown_keep_ts: set | None = None) -> list[GoalEvent]:
    """球位置序列 → 进球事件。输入须按 t 升序（detector 输出天然满足）。

    三档输出：high / review / low。四道否决门槛（规则A 拦截、下落校验、
    实体校验、置信度不足）2026-08-19 起不再删除候选——统一降为 low 档
    进列表供人工查漏（用户决策"宁多勿漏"：所有投篮候选都要可见，被门槛
    误拦的真进球不能再静默消失），降级原因记在 low_reason；低置信默认
    不勾选、不进集锦。强制复核：实体未再现、球心压筐沿
    （CROSS_REVIEW_OFF）、疑似弹出（POPBACK_MIN_SEC~POPBACK_SEC 内回上方）。

    v11 兜底路径：主配对循环结束后，对"筐口正下方的实体级下落链"注入
    虚拟上方起点再跑一遍（_fallback_up_points）——救上方轨迹整体缺位的
    真进球（斜射入筐的上方点全出横向容差/被同帧大块截胡，below 证据完美
    但配对无从发生 → 静默漏检）。兜底事件强制不升绿（只 review/low）。

    v14 冷却释放（cooldown_keep_ts，pipeline 二趟传入）：None=默认语义，
    每条浮表行都占 3s 冷却钟；传集合则只有 round(t,3)∈集合的行占钟
    （网门降级行不挡其后真进球——篮板补篮取证 09-12/09-04），集合外
    行同刻不重复发（同刻二次发行为二趟伪影，非两颗球）。

    v20 冷却身份判据：冷却不再无条件吞候选——先判两行之间球是否重新
    走完一遍「离开筐邻域→从高处/侧面回来」的接近轨迹（_new_ball_since），
    是则判另一颗球、放行并按 conf 正常定档；否则照旧吞。低置信自有的
    3s 去重钟同款处理。开关 NEWBALL_ENABLE=False 逐位回到 v19 行为。"""
    events = _decide_pass(positions, calib, cooldown_keep_ts)
    pseudo = _fallback_up_points(positions, calib, [e.t for e in events])
    if not pseudo:
        return events
    base_ts = {round(e.t, 2) for e in events}
    merged = _decide_pass(sorted(positions + pseudo, key=lambda p: p.t),
                          calib, cooldown_keep_ts)
    out: list[GoalEvent] = []
    for e in merged:
        # 注入伪点距既有事件 >FALLBACK_EXCL_SEC，不可能改变既有事件
        # （邻域/弹出窗都 ≤1.2s）——merged 里多出来的即兜底事件，压掉升绿
        # v18：改挂 review_reason="fallback" 硬门（原直接改 status）——
        # 否则网分重分箱会把 conf≥0.7 的兜底行推回 high，违背 v11 裁定
        if round(e.t, 2) not in base_ts and e.status == "high":
            e.status = "review"
            e.review_reason = "fallback"
        out.append(e)
    return out


# 兜底路径常量（v11 2026-08-31，truth/2026-08-28 漏检 19:12 取证 +
# 三场消融定案：核心指标零变化、08-28 漏检 1→0、08-22/08-21 零回归，
# 新增行全部 low/review——每场 +5~15 行低置信噪声是已知代价）
FALLBACK_MAX_H = 1.5       # 链首距筐口平面的最大距离（筐高倍数）——更远的
                           # 下落与进球无关（人群里运球/捡球）
FALLBACK_MIN_N = 3         # 下落链最少点数（大筐默认）
FALLBACK_SMALL_HW = 45     # v15：小筐（hw<此值）链点门槛 3→2——38px 筐上球
                           # 入网后检出常被撕成两点链（09-19 1048.1 取证，
                           # 链首 a=71.5 本过实体线、n=2 被拦死成静默漏检）。
                           # 八场消融：全放宽 +44 噪声行、其中 26 条集中在大筐
                           # 场（09-04 +11/09-11 +10/09-12 +5），小筐三场仅
                           # +10 且救回该球 → 按 NET_RELEASE_MIN_HW 同款硬门
                           # 只对小筐放宽
FALLBACK_WIN_SEC = 0.5     # 链时间窗
FALLBACK_EXCL_SEC = 2.5    # 与已有事件的最小间隔（同波穿越/弹跳不重复生成）
FALLBACK_STEP_PX = 1.0     # 链内允许的向上抖动（检出噪声级）

# v15 碎片链实体等价（E2 轮，2026-09-25）：小筐上球穿网后检出被撕成
# 10~50px² 碎片、普遍 <min_entity（0.1×筐面积），09-19 有 11 条真球死在
# 「全程无球体量级检出」。八场全集分布（13 真/77 假）：碎片链「贯穿网深
# 的严格单调下行」真 6/假 0——该形态物理上只能是球穿网下坠；mono 放宽到
# 0.5 立刻 8/6 崩，严格单调是唯一分离器，勿松。
# 筐宽硬门（hw<FRAG_SMALL_HW）：碎片撕裂是小筐结构性问题（35~38px 筐
# min_entity≈50~57px²，碎片 10~50px² 普遍欠线；大筐 min_entity 130~170px²
# 而球体检出 300~500px² 稳过实体线，不需要此救济）。八场验收里大筐侧
# 唯一翻案是 09-11 t=11.14 假绿（hw=65：片头热身机位、标定筐位处站着
# 人群，抬手动作撑出假网信号 80.4+local、热身运球拼出单调下行链）——
# 按假绿不恶化裁定收门；09-04/09-12 的其余大筐翻行全是用户已删行
# （指标/界面均不可见）。08-22 6208.3（hw=51，边缘唯一真球）让位留 ⚪。
FRAG_CHAIN_MIN_N = 3
FRAG_CHAIN_SPAN_H = 2.0
FRAG_SMALL_HW = 45


def _fallback_up_points(positions: list[BallPos], calib: Calibration,
                         event_ts: list[float]) -> list[BallPos]:
    """给"筐口正下方开始的实体级下落链"造虚拟上方起点（y 置于筐口平面
    上方条带外一点，x 同链首）。真进球的 below 证据（连续下坠+实体面积）
    由主循环既有校验背书，伪点只补配对起点；生成的事件事后强制不升绿。"""
    hx, hy, hw, hh = calib.hoop_rect
    rim_y = hy + hh * RIM_Y_RATIO
    band = hh * BAND_RATIO
    x_lo, x_hi = hx - hw * X_MARGIN, hx + hw * (1 + X_MARGIN)
    min_entity = hw * hh * ENTITY_AREA_RATIO
    min_desc = max(DESCENT_MIN_PX, hh * DESCENT_RATIO)
    extra: list[BallPos] = []
    for i, q in enumerate(positions):
        if not (x_lo <= q.x <= x_hi):
            continue
        if not (rim_y + band <= q.y <= rim_y + hh * FALLBACK_MAX_H):
            continue
        if q.area < min_entity:
            continue
        if any(abs(q.t - t) < FALLBACK_EXCL_SEC for t in event_ts):
            continue
        chain = [q]
        for w in positions[i + 1:]:
            if w.t - q.t > FALLBACK_WIN_SEC:
                break
            if w.y >= chain[-1].y - FALLBACK_STEP_PX and x_lo <= w.x <= x_hi:
                chain.append(w)
        if len(chain) < (2 if hw < FALLBACK_SMALL_HW else FALLBACK_MIN_N):
            continue
        descent = max((b.y - a.y for a, b in zip(chain, chain[1:])), default=0.0)
        if descent < min_desc:
            continue
        extra.append(BallPos(t=q.t - 0.07, x=q.x,
                             y=rim_y - band - 0.1, area=q.area, fi=-1))
    return extra


def apply_area_gate(events: list[GoalEvent]) -> list[GoalEvent]:
    """v24 穿筐物投影面积硬门：只把「会直接进成片」的 high 行降到 review。

    **为什么必须放在网分重分箱之后（`net_evidence_pass` 之后）**，而不是像
    entity/geom 那样在 `_decide_pass` 里挂 review_reason：
    硬门行的语义是「网分只记录、不重分箱」，一旦在判定器里给一行挂上
    review_reason，网分那一步就再也不给它重分档了。于是「几何分够 high、
    但会被网分扣成 low」的行（例：10-23 t=2647.78，几何 0.66 − 网分 0.26
    = 0.40）会被面积门**从 low 抬回 review** —— 与「只降假绿」的目的相反，
    还会污染人工兜底计数。放在网分之后按最终档位判断，天然只降不升。

    另一个好处：影响面被锁死成「恰好 high 的行」，事件行与其 conf 全不变，
    L0 复现只需读落表的 review_reason="area"（与 entity/geom 同一套路）。

    判据与阈值见 AREA_KILL_REL 注释。旧档（无 cross_area_rel）与 B2 注入行
    （无穿越点面积）一律跳过，行为与 v23 完全一致。"""
    for e in events:
        rel = getattr(e, "cross_area_rel", None)
        if rel is not None and rel > AREA_KILL_REL and e.status == "high":
            e.status = "review"
            e.review_reason = "area"
    return events


def _decide_pass(positions: list[BallPos], calib: Calibration,
                 cooldown_keep_ts: set | None = None) -> list[GoalEvent]:
    """主配对循环（decide_goals 的判定主体，v11 起与兜底路径分层）。
    cooldown_keep_ts 语义见 decide_goals（None=全占钟的默认行为）。"""
    hx, hy, hw, hh = calib.hoop_rect
    rim_y = hy + hh * RIM_Y_RATIO
    band = hh * BAND_RATIO
    x_lo, x_hi = hx - hw * X_MARGIN, hx + hw * (1 + X_MARGIN)
    cx = hx + hw / 2
    # v20：冷却身份判据要按时间区间取点，时间列建一次复用（输入已按 t
    # 升序，bisect 定位区间 O(log n)，新增行触发的扫描量可忽略）
    pos_ts = [p.t for p in positions]

    # 只保留筐口横向范围内、且不在筐身条带里的点，标注上方/下方
    classified = []  # (BallPos, side) side: "up"/"down"
    for p in positions:
        if not (x_lo <= p.x <= x_hi):
            continue
        if abs(p.y - rim_y) < band:
            continue
        classified.append((p, "up" if p.y < rim_y else "down"))

    events: list[GoalEvent] = []
    last_t = float("-inf")
    last_low_t = float("-inf")   # 低置信单独的冷却钟（不挡真事件，见下）
    last_any_t = float("-inf")   # v20：最近一条已发行为（判据区间的左端）
    nonkeep_ts: set[float] = set()   # v14：非占钟行同刻去重（每趟重置）
    for i, (p, side) in enumerate(classified):
        if side != "up":
            continue
        # 找 ≤MAX_CROSS_SEC 内的第一个下方点（利用时间有序性提前终止）
        cross = None
        for j in range(i + 1, len(classified)):
            q, s = classified[j]
            if q.t - p.t > MAX_CROSS_SEC:
                break  # 输入按 t 升序，后续点只会更晚
            if s == "down":
                cross = q
                break
        if cross is None:
            continue
        # v20：冷却先判身份——两行之间球若重新走完一遍接近轨迹，这是第二颗
        # 球（篮板补篮），放行；否则仍是同一颗球的重复配对，按冷却吞。
        # 区间左端取最近一条已发行（不分档位），不是上一行占钟行：中间隔了
        # 一行时那行本身就是另一次穿越，判据只需看相邻两行之间。
        if cross.t - last_t < COOLDOWN_SEC \
                and not _new_ball_since(positions, pos_ts, last_any_t, cross.t,
                                        cx, hw, hh, rim_y, band, x_lo, x_hi):
            continue

        # 穿越点取离平面最近的上/下点内插值（而非下方首点 x）
        prev_up = p
        for k in range(i + 1, j):
            if classified[k][1] == "up":
                prev_up = classified[k][0]
        cross_off = (_cross_x(prev_up, cross, rim_y) - cx) / hw  # 正=右
        # 规则A：球心在筐口两端之外（几何不可能）→ 降级低置信；
        # 压筐沿区 → 保留但强制复核。阈值带像素下限（小筐防放大误差，见
        # CROSS_KILL_MIN_PX 注释）
        kill_off = max(CROSS_KILL_OFF, CROSS_KILL_MIN_PX / hw)
        review_off = max(CROSS_REVIEW_OFF, CROSS_REVIEW_MIN_PX / hw)
        low_reason = None
        if abs(cross_off) > kill_off:
            low_reason = "球心出筐口两端（从筐旁飞过，几何不可能）"
        geom_review = abs(cross_off) > review_off

        above_pts = [q for q, s in classified
                     if s == "up" and cross.t - NEIGHBOR_SEC <= q.t <= cross.t]
        below_pts = [q for q, s in classified
                     if s == "down" and cross.t <= q.t <= cross.t + NEIGHBOR_SEC]
        above_span = (above_pts[-1].t - above_pts[0].t) if above_pts else 0.0
        below_span = (below_pts[-1].t - below_pts[0].t) if below_pts else 0.0
        # 下落校验：真球穿筐后快速下坠（实测相邻检出 y 增量 8~43px）；
        # 原地不动的是固定物/被抱住的球，向上飞的是弹出——都不构成进球。
        # 下方点按时间间隙切成连续段（1s 邻域窗会扫进稍后另一次下穿的点，
        # 跨段不比下落）；只有 ≥2 点的段才可判"零下落"，全为孤立单点段
        # 说明下落不可证——保留交给置信度+人工复核（宁多勿漏）。
        descent = 0.0
        run: list[BallPos] = []
        runs: list[list[BallPos]] = []
        for q in below_pts:
            if run and q.t - run[-1].t > DESCENT_RUN_GAP:
                runs.append(run)
                run = []
            run.append(q)
        if run:
            runs.append(run)
        for seg in runs:
            descent = max(descent, max((b.y - a.y for a, b in zip(seg, seg[1:])),
                                       default=0.0))
        if any(len(seg) >= 2 for seg in runs) \
                and descent < max(DESCENT_MIN_PX, hh * DESCENT_RATIO):
            if low_reason is None:
                low_reason = "下方证据原地不动/上弹（固定物或被抱住的球）"

        # 下落实体校验：穿越筐口后必须再见到球体量级的检出，证明穿过的
        # 实体是球并继续下落。"穿过即消失"的是弹筐而出的球或人肤横穿
        # （2026-08-17 取证 t=127.02 幽灵：a=307 大块斜穿筐口平面后全画面
        # 零检出 0.67s——球会弹跳留轨迹，人肤没入人群才彻底消失）。
        # 分级处理：穿越点本身是球体量级（真有实体穿筐、其后被遮挡/漏检）
        # → 保留但强制待复核；穿越点也只是碎片（全程无任何实体）→ 低置信。
        # v8：纯相对阈值（小筐上绝对底会反杀真球，见 ENTITY_AREA_RATIO 注释）
        # v15：碎片链「贯穿网深的严格单调下行」= 实体等价（FRAG_CHAIN_*
        # 注释），不再挂量级缺失、走主流程定档
        min_entity = hw * hh * ENTITY_AREA_RATIO
        big_below = [q for q in below_pts
                     if q is not cross and q.area >= min_entity]
        entity_review = not big_below and cross.area >= min_entity
        frag_chain = (hw < FRAG_SMALL_HW
                      and len(below_pts) >= FRAG_CHAIN_MIN_N
                      and below_pts[-1].y - below_pts[0].y
                      >= FRAG_CHAIN_SPAN_H * hh
                      and all(b.y > a.y
                              for a, b in zip(below_pts, below_pts[1:])))
        if not big_below and not entity_review and not frag_chain:
            if low_reason is None:
                low_reason = "全程无球体量级检出（碎片/人肤横穿）"

        # v24 面积硬门（不在这里落档，见 apply_area_gate）：穿越点的检出框
        # 面积 ÷ 筐矩形面积。真球 12~16%，超过一半只可能是手/臂/身体。
        area_rel = cross.area / (hw * hh)

        conf = 0.3
        conf += _side_score(len(above_pts), above_span)
        conf += _side_score(len(below_pts), below_span)
        conf += _pos_score(abs(cross_off))

        conf = round(conf, 2)  # 先取整再比较，避免浮点误差
        if low_reason is None and conf < MIN_CONF:
            low_reason = "证据点太少（置信度不足 0.50）"

        # 规则C（弹出否决）：下穿后 0.25~1.2s 球又回到筐口上方 → 疑似弹筐
        # 而出（2026-08-19 取证 t=643.8：下穿后 0.42s 球弹回上方，用户标注
        # 误判）。不删事件（弹出否决历史上误伤过），强制复核交给人工。
        pop_back = next((round(q.t - cross.t, 2)
                         for q, s in classified
                         if s == "up"
                         and cross.t + POPBACK_MIN_SEC <= q.t <= cross.t + POPBACK_SEC),
                        None)

        if low_reason is not None:
            # 低置信不占真事件冷却（碎片候选不能挡住 1s 后的真进球——
            # 篮板补篮场景），但低置信彼此 3s 去重：弹跳/反复横穿只留首个。
            # v20：去重同样先判身份——补篮若两颗球都是碎片候选，不能只留
            # 首个（口径与占钟冷却保持一致，否则 low 形态的补篮仍被吞）
            if cross.t - last_low_t < COOLDOWN_SEC \
                    and not _new_ball_since(positions, pos_ts, last_any_t,
                                            cross.t, cx, hw, hh, rim_y, band,
                                            x_lo, x_hi):
                continue
            last_low_t = cross.t
        elif cooldown_keep_ts is not None:
            # v14 冷却释放：显式占钟集下只有基线非降级行推进钟，集合外
            # 行照发但不挡人（网门降级行后的真进球不再被吞）；同刻二次
            # 发行为二趟伪影，丢弃
            _t3 = round(cross.t, 3)
            if _t3 in cooldown_keep_ts:
                last_t = cross.t
            elif _t3 in nonkeep_ts:
                continue
            nonkeep_ts.add(_t3)
        else:
            last_t = cross.t
        review_reason = None
        status = "high" if conf >= HIGH_CONF else "review"
        if entity_review or geom_review or pop_back is not None:
            status = "review"      # 实体未再现/球心压筐沿/疑似弹出：给人工复核
            # v18：硬门落 review_reason——net pass 重分箱时这些行档位不动。
            # （顺带修掉 v10 时代的怪序：R1 曾越过 entity 硬门把这类行升绿，
            # 硬门优先级从此明确写死：entity > geom > pop_back。）
            review_reason = ("entity" if entity_review
                             else "geom" if geom_review else "pop_back")
        if low_reason is not None:
            status = "low"
        events.append(GoalEvent(
            t=cross.t, confidence=conf,
            status=status,
            low_reason=low_reason,
            cross_off=round(cross_off, 3),
            above_n=len(above_pts), below_n=len(below_pts),
            above_span=round(above_span, 3), below_span=round(below_span, 3),
            descent_px=round(descent, 1),
            below_big=len(big_below),
            pop_back=pop_back,
            cross_area_rel=round(area_rel, 4),
            review_reason=review_reason))
        last_any_t = cross.t
    return events
