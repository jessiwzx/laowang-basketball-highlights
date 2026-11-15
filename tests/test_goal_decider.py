import pytest
from bbh.models import BallPos, Calibration, GoalEvent
from bbh.goal_decider import (_cross_x, apply_area_gate, decide_goals,
                             drop_static_positions)

# 筐口矩形 (300,140,80,40) → rim_y = 140 + 0.35*40 = 154，筐心 x=340
CALIB = Calibration(hoop_rect=(300, 140, 80, 40),
                    hsv_low=(0, 0, 0), hsv_high=(179, 255, 255),
                    frame_size=(640, 360))


def pts(seq, area=400):
    """[(t, x, y)] → [BallPos]。默认面积=球体量级（≥0.1×筐矩形面积 320px²，
    下落实体校验的门槛）；模拟固定物/皮肤碎片时传 area=20 一档。"""
    return [BallPos(t=t, x=x, y=y, area=area) for t, x, y in seq]


class Test标准进球:
    def test_完整轨迹_高置信(self):
        positions = pts([
            (10.0, 340, 100), (10.1, 340, 120), (10.2, 340, 145),
            (10.3, 340, 165), (10.4, 340, 190), (10.5, 340, 215),
        ])
        events = decide_goals(positions, CALIB)
        assert len(events) == 1
        assert events[0].status == "high"
        assert events[0].t == pytest.approx(10.3, abs=0.01)

    def test_只检到交叉前后各一点_干净居中_待复核(self):
        """v18（2026-10-11 问题②）：R1 升绿删除——档位只由阈值分箱(conf)+硬门
        决定。「干净居中」不再是旁路升绿理由（被遮挡真球掉回 review 交人工，
        八场定标验证查全影响，劣化再按 spec §3.4 转几何分修正项）。"""
        positions = pts([(20.0, 340, 100), (20.2, 340, 190)])
        events = decide_goals(positions, CALIB)
        assert len(events) == 1
        assert events[0].status == "review"
        assert events[0].confidence == 0.60


class Test不触发:
    def test_打铁弹框_向上穿过(self):
        positions = pts([
            (30.0, 340, 190), (30.1, 340, 165), (30.2, 340, 145), (30.3, 340, 110),
        ])
        assert decide_goals(positions, CALIB) == []

    def test_空气球_从旁侧飞过不进筐口横向范围(self):
        positions = pts([
            (40.0, 100, 100), (40.1, 100, 145), (40.2, 100, 190), (40.3, 100, 250),
        ])
        assert decide_goals(positions, CALIB) == []

    def test_运球经过_无下穿(self):
        positions = pts([(50.0 + i * 0.1, 340, 190 + i * 5) for i in range(6)])
        assert decide_goals(positions, CALIB) == []

    def test_筐身条带内的点被忽略(self):
        # 全部点都在 |y-154|<10 条带内 → 无上下方点，无事件
        positions = pts([(60.0, 340, 150), (60.1, 340, 156), (60.2, 340, 158)])
        assert decide_goals(positions, CALIB) == []

    def test_交叉间隔超过07秒_不算同一次下穿(self):
        positions = pts([(70.0, 340, 100), (70.9, 340, 190)])
        assert decide_goals(positions, CALIB) == []


class Test判定依据与细化置信度:
    """方案B：依据字段记录 + 分档平滑打分（旧版断崖式 0.15/0.05 加分取消）。"""

    def test_完整轨迹记录全部依据字段(self):
        positions = pts([
            (10.0, 340, 100), (10.1, 340, 120), (10.25, 340, 143),
            (10.35, 340, 165), (10.5, 340, 190), (10.6, 340, 215),
        ])
        e = decide_goals(positions, CALIB)[0]
        assert e.cross_off == pytest.approx(0.0, abs=1e-6)
        assert (e.above_n, e.below_n) == (3, 3)
        assert e.above_span == pytest.approx(0.25, abs=1e-3)
        assert e.below_span == pytest.approx(0.25, abs=1e-3)
        assert e.pop_back is None
        # 0.3 + 上方满档 0.2 + 下方满档 0.2 + 筐心 0.2
        assert e.confidence == 0.9

    def test_穿越点偏移分档得分平滑(self):
        def conf_at(x):
            positions = pts([
                (10.0, x, 100), (10.1, x, 120), (10.25, x, 143),
                (10.35, x, 165), (10.5, x, 190), (10.6, x, 215),
            ])
            events = decide_goals(positions, CALIB)
            return events[0].confidence if events else None
        assert conf_at(340) == 0.9    # 筐心：位置分 0.20
        assert conf_at(360) == 0.84   # 右偏 25% 筐宽：0.14
        assert conf_at(324) == 0.87   # 左偏 20% 筐宽：0.17
        assert conf_at(388) == 0.7    # 右偏 60%：分数正常，但被规则A 降级低置信

    def test_证据点跨度不足打折(self):
        # 点数够（各 2 点）但时间跨度只有 0.07s → 短档 SIDE_SHORT 而非满档
        # （v23 起短档 = 0.17，旧值 0.14）
        positions = pts([
            (10.0, 340, 100), (10.07, 340, 143),
            (10.35, 340, 165), (10.42, 340, 210),
        ])
        e = decide_goals(positions, CALIB)[0]
        # 0.3 + 0.17 + 0.17 + 筐心 0.2
        assert e.confidence == 0.84

    def test_弹出迹象被记录但不扣分(self):
        """规则C（2026-08-19）：下穿后 ≥0.25s 球回筐口上方 → pop_back 记
        秒数并强制复核；置信度与无回弹轨迹一致（不扣分，交给人工）。"""
        rattle = pts([(20.0, 340, 100), (20.3, 340, 190), (20.6, 340, 110)])
        clean = pts([(20.0, 340, 100), (20.3, 340, 190)])
        e_rattle = decide_goals(rattle, CALIB)[0]
        e_clean = decide_goals(clean, CALIB)[0]
        assert e_rattle.pop_back == pytest.approx(0.3)
        assert e_clean.pop_back is None
        assert e_rattle.confidence == e_clean.confidence == 0.6
        assert e_rattle.status == "review"


class Test冷却合并:
    def test_三秒内连续两次下穿只记一次(self):
        positions = pts([
            (80.0, 340, 100), (80.2, 340, 190),          # 第一次下穿
            (80.5, 340, 100), (80.7, 340, 190),          # 冷却期内，丢弃
            (85.0, 340, 100), (85.2, 340, 190),          # 冷却期外，第二球
        ])
        events = decide_goals(positions, CALIB)
        assert len(events) == 2
        assert events[0].t == pytest.approx(80.2, abs=0.01)
        assert events[1].t == pytest.approx(85.2, abs=0.01)

    def test_低置信候选保留且不占用冷却(self):
        """2026-08-19 语义变更：低置信候选不再删除（所有投篮候选都进列表
        供人工查漏），且不占用真事件冷却——碎片候选不能挡住 1s 后的真进球
        （篮板补篮场景）。"""
        # 压筐沿单点证据（off=−0.44，conf 0.46）→ 低置信；1.2s 后筐心
        # 双点证据的真下穿（conf 0.60）→ v18 按分箱待复核（R1 已删），不被挡
        positions = pts([(90.0, 305, 100), (90.2, 305, 190),
                         (91.2, 340, 100), (91.4, 340, 190)])
        events = decide_goals(positions, CALIB)
        assert [e.status for e in events] == ["low", "review"]
        assert "证据点太少" in events[0].low_reason

    def test_低置信彼此3秒去重_弹跳不刷屏(self):
        # 球在筐下反复弹跳：每次上下穿越都是低置信候选，3s 内只留首个
        # （否则一次弹跳序列能刷出十几行噪声）
        positions = []
        for k in range(6):
            positions += pts([(10.0 + k * 0.5, 340, 100),
                              (10.2 + k * 0.5, 340, 190)], area=20)
        events = decide_goals(positions, CALIB)
        assert all(e.status == "low" for e in events)
        assert len(events) <= 2          # 3s 窗口内去重，只留极少数

    def test_大量连续上方点后仍能正常判定(self):
        # 回归：全 up 输入曾是最坏 O(n²)；5000 个 up 点 + 1 次正常下穿
        positions = pts([(i * 0.02, 340, 100) for i in range(5000)])
        positions += pts([(100.0, 340, 100), (100.2, 340, 190)])
        events = decide_goals(positions, CALIB)
        assert len(events) == 1
        assert events[0].t == pytest.approx(100.2, abs=0.01)


class Test冷却身份判据:
    """v20（2026-11-01）：冷却先判「是不是同一颗球」——两行之间球若重新
    走完一遍接近轨迹（≥2 个筐上方点、跨度 ≥0.2s、含高处点或侧移点）则
    放行并按 conf 正常定档；否则仍是同球重复配对，照旧吞。取证见
    诊断/2026-11-01-冷却验证/（三场 87 条增量 = 另一颗球 31 + 同球 56）。"""

    # 筐 (300,140,80,40)：rim_y=154、band=10、筐心 x=340；
    # 「高处点」阈值 154−2×40=74；「侧移点」阈值 1.3×80=104（x<236 或 >444）
    Buzzer = pts([
        (10.0, 340, 100), (10.1, 340, 120), (10.2, 340, 145),
        (10.3, 340, 165), (10.4, 340, 190), (10.5, 340, 215),   # 第一球 cross 10.3
        (11.0, 340, 230),                 # 掉到筐下
        (11.6, 470, 300),                 # 被抢板的人捡到右侧（侧移 130 > 104）
        (12.2, 390, 60),                  # 举回高处（60 < 74）→ 接近轨迹成立
        (12.45, 380, 100),
        (12.6, 340, 165), (12.7, 340, 190),   # 第二球 cross 12.6（ΔT=2.3s）
    ])

    def test_补篮_独立接近轨迹_放行且正常定档(self):
        events = decide_goals(self.Buzzer, CALIB)
        assert [round(e.t, 2) for e in events] == [10.3, 12.6]
        assert [e.status for e in events] == ["high", "high"]

    def test_同一颗球贴筐重复_仍被吞(self):
        # 两行之间确有两个上方点、跨度 0.3s，但都在筐口正上方（无高处点、
        # 无侧移）——球没离开过筐邻域，是同一颗球被配出两个 cross 时刻
        positions = pts([
            (80.0, 340, 100), (80.2, 340, 190),      # 第一球 cross 80.2
            (80.5, 340, 120), (80.8, 340, 130),      # 贴筐上方点 ×2
            (81.0, 340, 190),                        # 同球二次配对
        ])
        events = decide_goals(positions, CALIB)
        assert [round(e.t, 2) for e in events] == [80.2]

    def test_零点三秒内二次穿越_必吞(self):
        # 物理上 0.3s 不可能穿两次：取证里 0.3s 桶 21 条全部判同球，
        # 即使球确实出现在高处（单点凑不成轨迹）
        positions = pts([
            (80.0, 340, 100), (80.2, 340, 190),
            (80.3, 340, 60),
            (80.5, 340, 190),
        ])
        events = decide_goals(positions, CALIB)
        assert [round(e.t, 2) for e in events] == [80.2]

    def test_开关关闭_逐位回到v19无条件冷却(self, monkeypatch):
        from bbh import goal_decider as gd
        monkeypatch.setattr(gd, "NEWBALL_ENABLE", False)
        events = decide_goals(self.Buzzer, CALIB)
        assert [round(e.t, 2) for e in events] == [10.3]

    def test_低置信钟同步放行(self):
        # 两颗球都是碎片候选（area=20 → low），补篮不能只留首个
        positions = [BallPos(t=t, x=x, y=y, area=20) for t, x, y in [
            (90.0, 340, 100), (90.2, 340, 190),          # 第一球 low
            (91.5, 470, 300),                            # 侧移
            (92.0, 390, 60), (92.25, 380, 100),          # 高处 + 接近轨迹
            (92.8, 340, 190),                            # 第二球 low（ΔT=2.6s）
        ]]
        events = decide_goals(positions, CALIB)
        assert [round(e.t, 2) for e in events] == [90.2, 92.8]
        assert [e.status for e in events] == ["low", "low"]


class Test冷却释放:
    """v14（2026-09-24）cooldown_keep_ts：显式占钟集——只有集合内的行推进
    3s 冷却钟（网门降级行不挡其后真进球，pipeline 二趟传入）；None=默认
    全占钟，既有行为零变化。"""

    TWO_BALLS = pts([
        (80.0, 340, 100), (80.2, 340, 190),          # 第一次下穿
        (80.5, 340, 100), (80.7, 340, 190),          # 冷却期内，默认丢弃
    ])

    def test_占钟集含首球_第二球仍被吞(self):
        # 集合含首球时刻 → 与默认语义一致（基线行为本身就是这个集合的来源）
        events = decide_goals(self.TWO_BALLS, CALIB,
                              cooldown_keep_ts={round(80.2, 3)})
        assert [round(e.t, 2) for e in events] == [80.2]

    def test_占钟集不含首球_冷却窗内第二球浮表(self):
        # keep=∅（网门把首球降级后 pipeline 传的集合）→ 首球不再挡人，
        # 0.5s 后的第二球浮表（09-12 篮板补篮形态：前一投降级、真进球紧随）
        events = decide_goals(self.TWO_BALLS, CALIB, cooldown_keep_ts=set())
        assert [round(e.t, 2) for e in events] == [80.2, 80.7]
        # 首球因第二球的上方点落在其弹出窗内（pop_back=0.3）诚实复核；
        # 第二球 conf 0.75 够 high 线但穿筐后无下方实体检出 → v18 entity
        # 硬门压 review（v10 R1 曾越过 entity 硬门把它升绿——怪序已修）
        assert [e.status for e in events] == ["review", "review"]
        assert events[1].review_reason == "entity"

    def test_非占钟行同刻去重(self):
        # 两个上方点配对同一个下方穿越点 → 同刻两次发射；非占钟模式下
        # 第二次是二趟伪影（同一颗球），丢弃（默认模式下由冷却钟去重）
        positions = pts([(80.0, 340, 100), (80.1, 340, 110), (80.2, 340, 190)])
        events = decide_goals(positions, CALIB, cooldown_keep_ts=set())
        assert [round(e.t, 2) for e in events] == [80.2]

    def test_低置信行不受占钟集影响(self):
        # 低置信走自己的钟（last_low_t），keep 集合只作用于非低置信行
        positions = pts([(90.0, 305, 100), (90.2, 305, 190),   # 压筐沿 → low
                         (91.2, 340, 100), (91.4, 340, 190)])  # 真下穿
        events = decide_goals(positions, CALIB, cooldown_keep_ts=set())
        # v18：第二球 conf 0.60 分箱 review（R1 已删，与默认占钟模式同判）
        assert [e.status for e in events] == ["low", "review"]


class Test静态点过滤:
    """篮网结点、筐下固定橙色物（护垫等）会反复检出于同一位置；真球快速路过
    （一至两个采样点就换格）。判据按格子内的「连续驻留段」：段内 ≥3 个检出
    → 静态物，该段整段丢弃。间歇出现的小段也要删（2026-08-16 真实视频取证：
    筐下 y≈476-484 固定结构带分六个格子漏网 ~2500 点，全部成为幽灵进球的
    "下方证据"——旧版 span+密度判据只删最大的两格）。"""

    def _net_points(self, x=340, y=184, t0=0.0, t1=60.0, dt=1 / 15):
        """模拟篮网结点整段持续检出于同一位置。"""
        n = int((t1 - t0) / dt) + 1
        return pts([(t0 + i * dt, x, y) for i in range(n)])

    def test_飞越篮筐的未进球_静态网点拼不出假进球(self):
        # 球从筐上方飞过（没进），只有上方点
        flyover = pts([(10.0, 340, 120), (10.07, 340, 130), (10.14, 340, 140)])
        positions = self._net_points() + flyover
        positions.sort(key=lambda p: p.t)
        # 双保险各自独立成立：判定器的下落校验把零下落的"下方证据"降到
        # 低置信档（不进集锦、供人工查漏）；静态过滤则直接把网点从输入里
        # 删掉——过滤后连低置信行都没有
        raw = decide_goals(positions, CALIB)
        assert [e.status for e in raw] == ["low"]
        assert decide_goals(drop_static_positions(positions, CALIB), CALIB) == []

    def test_真进球伴随静态网点_仍判出且不翻倍(self):
        # 球进网时穿过网点所在格（180~195 与网同格）：链式过滤只删静止链，
        # 球自己的穿网点（步距 15px）存活 → 事件锚定首个下方检出 10.14
        # （比旧版整格删后锚 10.28 更接近真实下穿）；上下证据跨度都 <0.2s
        # → 短档（v23 = 0.17）→ 0.3 + 0.17 + 0.17 + 筐心 0.20 = 0.84 高置信。
        goal = pts([
            (10.0, 340, 120), (10.07, 340, 135),
            (10.14, 340, 180), (10.21, 340, 195),   # 穿过网所在格，球点保留
            (10.28, 340, 210),
        ])
        positions = self._net_points() + goal
        positions.sort(key=lambda p: p.t)
        events = decide_goals(drop_static_positions(positions, CALIB), CALIB)
        assert len(events) == 1
        assert events[0].t == pytest.approx(10.14, abs=0.01)
        assert events[0].status == "high"
        assert events[0].confidence == 0.84
        assert events[0].descent_px == pytest.approx(15.0)

    def test_间歇出现的静态物_每一段都被删(self):
        # 筐下固定物被球员间歇遮挡：出现 0.9s、消失 5s、再出现 0.4s……
        # 旧版按全视频平均密度判（span 大但密度 <2/s）→ 全部漏网（真实
        # t=164.61 幽灵事件的 12 个"下方证据"点即此形态）；按段删则全灭。
        bursts = []
        t = 0.0
        for dur in (0.9, 0.4, 1.2, 0.3):
            n = int(dur / 0.083) + 1
            bursts += pts([(t + i * 0.083, 340, 190) for i in range(n)])
            t += dur + 5.0
        assert drop_static_positions(bursts, CALIB) == []

    def test_球隔久两次经过同一点_不被当静态物(self):
        # 真球在篮板下反复弹跳：每次到访只 1 点、间隔 10s → 每段 1 个检出 <3
        revisits = pts([(i * 10.0, 310, 250) for i in range(7)])   # 跨 60s，独立格
        positions = (self._net_points() + revisits
                     + pts([(5.0, 340, 120), (5.1, 340, 250)]))
        kept = drop_static_positions(positions, CALIB)
        assert any(p.t == 60.0 for p in kept)        # 复访点保留
        assert not any(p.y == 184 for p in kept)     # 网点已剔除

    def test_快速穿格的下落球不陪葬(self):
        # 真球下落：每个 20px 格子只停留 1~2 个采样点 → 各段 <3，全保留
        fall = pts([(0.0, 340, 190), (0.08, 340, 205), (0.16, 341, 230)])
        assert drop_static_positions(fall, CALIB) == fall

    def test_原地停留两秒的球也被删(self):
        # 语义变化（相对旧版 span<3s 保留）：球被抱住/停在原地本身就是非下落
        # 状态，停多久都不构成进球证据——t=164.61 幽灵事件的"下方证据"正是
        # 钉死在 (1038,482) 一动不动 0.92s 的 12 个点（真球穿网约 0.2s）。
        held = pts([(i * 0.1, 340, 190) for i in range(21)])
        assert drop_static_positions(held, CALIB) == []

    def test_空与单点输入直接原样返回(self):
        assert drop_static_positions([], CALIB) == []
        one = pts([(1.0, 340, 190)])
        assert drop_static_positions(one, CALIB) == one


class Test下落校验:
    """decide_goals 的第二道防线（2026-08-16 取证）：真球穿筐口平面后必然
    快速下坠（真实视频实测相邻检出 y 增量 8~43px），静态物的"下方点"原地
    零移动。下方证据 ≥2 点且最大相邻下落步长不达阈值 → 降级低置信（2026-08-19
    起不再删除：候选保留供人工查漏，默认不进集锦）。"""

    def test_下方证据原地不动_降级低置信(self):
        # 真实幽灵事件形态：上升弧 + 钉死在筐下一点不动的下方点簇
        positions = pts([(10.0, 340, 100), (10.1, 340, 130), (10.2, 340, 143)]
                        + [(10.3 + i * 0.08, 338, 190) for i in range(12)])
        events = decide_goals(positions, CALIB)
        assert [e.status for e in events] == ["low"]
        assert "原地不动" in events[0].low_reason

    def test_下方证据向上弹出_降级低置信(self):
        # 打铁弹出：下方点在筐下但 y 在减小（向上飞离）
        positions = pts([(10.0, 340, 100), (10.15, 340, 143),
                         (10.3, 340, 190), (10.38, 340, 187), (10.46, 340, 182)])
        events = decide_goals(positions, CALIB)
        assert [e.status for e in events] == ["low"]

    def test_单点下方证据_无法证伪_保留待确认(self):
        # 只有 1 个下方点时无从判断下落——保留（置信度天然低，进待确认），
        # 宁多勿漏交给人工复核
        positions = pts([(20.0, 340, 100), (20.2, 340, 190)])
        assert len(decide_goals(positions, CALIB)) == 1

    def test_下落步长记录在依据字段(self):
        positions = pts([
            (10.0, 340, 100), (10.1, 340, 120), (10.2, 340, 145),
            (10.3, 340, 165), (10.4, 340, 190), (10.5, 340, 215),
        ])
        e = decide_goals(positions, CALIB)[0]
        assert e.descent_px == pytest.approx(25.0)   # 165→190 为最大步长

    def test_静态过滤删除幽灵锚点_事件重锚到真实下落(self):
        # 真实 t=169.86 幽灵事件形态：真球上升弧 + 静态点抢先充当下穿点，
        # 其后才是真球的下落。不过滤时事件时刻锚在静态点上（提前 0.4s）；
        # 过滤后静态点消失，事件重锚到真实下落。
        positions = pts([
            (10.0, 340, 100), (10.08, 340, 130),          # 真球上升弧
            (10.3, 340, 190), (10.37, 340, 190), (10.44, 340, 190),  # 静态物
            (10.5, 340, 165), (10.58, 340, 200),          # 真球下落
        ])
        raw = decide_goals(positions, CALIB)
        assert raw[0].t == pytest.approx(10.3, abs=0.01)   # 锚在静态点
        fixed = decide_goals(drop_static_positions(positions, CALIB), CALIB)
        assert fixed[0].t == pytest.approx(10.5, abs=0.01)  # 重锚到真下落


class Test下落实体校验:
    """decide_goals 的第三道防线（2026-08-17 t=127.02 幽灵取证）：真球检出
    104~524px²、筐下固定物/皮肤碎片 ≤78px²（面积差一个量级）。穿越筐口后
    1s 内必须再见到球体量级检出——"穿过即消失"的是弹飞的球或人肤横穿。
    分级：穿越点本身是球体量级 → 保留但强制待复核；只是碎片 → 低置信。"""

    def test_球体量级穿越后消失_干净居中升绿_v10R1(self):
        # 真实 t=127.02 形态：a=307 大块斜穿筐口平面后，下方只剩 a=10~14
        # 静态带碎片（间隔 >0.25s 各成单点段，下落校验的单点保留规则放行）。
        positions = (pts([(10.0, 340, 100), (10.1, 340, 130)])      # 上升弧
                     + pts([(10.3, 340, 190)], area=400)            # 大块穿越
                     + pts([(10.7, 340, 192), (11.0, 340, 190)], area=20))  # 碎片
        events = decide_goals(positions, CALIB)
        assert len(events) == 1
        assert events[0].status == "review"   # v18：entity 硬门恒 review（v10 R1 曾越过硬门升绿——正是「算式解释不了的档位」）
        assert events[0].review_reason == "entity"
        assert events[0].below_big == 0       # 依据可查

    def test_碎片穿越且无任何实体_降级低置信(self):
        # 全程无球体量级检出：静态带碎片冒充下穿——降级低置信供人工查漏
        positions = (pts([(10.0, 340, 100), (10.1, 340, 130)])
                     + pts([(10.3, 340, 190), (10.7, 340, 192)], area=20))
        events = decide_goals(positions, CALIB)
        assert [e.status for e in events] == ["low"]
        assert "球体量级" in events[0].low_reason

    def test_穿越后有大点_正常高置信不降级(self):
        positions = pts([
            (10.0, 340, 100), (10.1, 340, 120), (10.2, 340, 145),
            (10.3, 340, 165), (10.4, 340, 190), (10.5, 340, 215),
        ])
        e = decide_goals(positions, CALIB)[0]
        assert e.status == "high"
        assert e.below_big == 2                 # 穿越点之后的 2 个球体量级点

    def test_穿越后被遮挡的大实体_干净居中升绿_v10R1(self):
        # 宁多勿漏：球体量级穿筐、其后被人群遮挡零检出——保留
        positions = pts([(10.0, 340, 100), (10.1, 340, 130), (10.3, 340, 190)])
        e = decide_goals(positions, CALIB)[0]
        assert e.status == "review"
        assert e.review_reason == "entity"


class Test规则A几何门槛:
    """2026-08-19 误判调优（取证 work/_fp_analysis/）：cross_off 改上/下点
    内插到筐口平面的真值（旧"下方首点 x"对斜飞球偏 0.2~0.4 筐宽）；球心
    出筐口两端（|off|>0.45，5% 余量标定下两端≈±0.45）几何不可能进球 →
    降级低置信（同日改：不再删除，候选可见供人工核查）；压筐沿区
    （0.30~0.45，实测真进球 ≤0.31）→ 保留但强制复核。"""

    def test_斜穿内插穿越点_压筐沿强制复核(self):
        # 球斜飞：上方 x=336 → 下方 x=396（检测横向边缘）。内插到筐口平面
        # y=154 处 x=366（off=+0.325）；旧逻辑记下方首点 x=396 → off=+0.70
        positions = pts([
            (10.0, 336, 100), (10.1, 336, 120), (10.25, 336, 143),
            (10.35, 396, 165), (10.5, 396, 190), (10.6, 396, 215),
        ])
        e = decide_goals(positions, CALIB)[0]
        assert e.cross_off == pytest.approx(0.325, abs=1e-3)
        assert e.confidence == 0.8       # 0.3+0.2+0.2+位置分0.1，本够 high
        assert e.status == "review"      # 压筐沿区 → 强制复核

    def test_球心出筐口两端_降级低置信(self):
        # 13 条误判中的侧沿类（off 0.54~0.66）：旧逻辑靠 0.3+0.2+0.2 拿
        # 0.7 high，几何上球是从筐旁飞过的 → 规则A 降级（不再是 high 事件）
        positions = pts([
            (10.0, 388, 100), (10.1, 388, 120), (10.25, 388, 143),
            (10.35, 388, 165), (10.5, 388, 190), (10.6, 388, 215),
        ])
        e = decide_goals(positions, CALIB)[0]
        assert e.status == "low"
        assert e.confidence == 0.7       # 分数本身够 high——几何门槛压到低档
        assert "筐口两端" in e.low_reason

    def test_内插辅助函数_退化输入回退下方点x(self):
        # 防御路径：经 classified 的点制上退化不可达（up 必在平面上方、
        # down 必在下方，frac 恒在 (0,1)），但输入异常时不崩、回退下方点 x
        down = BallPos(t=10.2, x=380, y=190, area=400)
        assert _cross_x(BallPos(t=10.0, x=340, y=200, area=400),
                        down, rim_y=154.0) == 380      # dy<=0
        assert _cross_x(BallPos(t=10.0, x=340, y=160, area=400),
                        down, rim_y=154.0) == 380      # 上点已在平面下


class Test规则C弹出复核:
    """弹出窗口收紧为 0.25~1.2s（2026-08-19）：穿后立即回上方的邻点是
    噪声/邻物（GOOD t=877.32 穿后 0.04s 误触发），真弹出实测 0.42s。"""

    def test_穿后025秒内回上方_噪声不触发(self):
        positions = pts([
            (10.0, 340, 100), (10.1, 340, 120), (10.25, 340, 143),
            (10.35, 340, 165), (10.45, 340, 110),   # 穿后 0.10s 的上方噪声点
            (10.5, 340, 190), (10.6, 340, 215),
        ])
        e = decide_goals(positions, CALIB)[0]
        assert e.pop_back is None
        assert e.status == "high"        # 不因噪声降级

    def test_穿后042秒弹回上方_强制复核(self):
        # 13 条误判之 t=643.8 形态：下穿后 0.42s 球弹回筐口上方（弹筐而出）
        positions = pts([
            (10.0, 340, 100), (10.1, 340, 120), (10.25, 340, 143),
            (10.35, 340, 165), (10.5, 340, 190), (10.6, 340, 215),
            (10.77, 340, 110),                   # 穿后 0.42s 弹回上方
        ])
        e = decide_goals(positions, CALIB)[0]
        assert e.pop_back == pytest.approx(0.42)
        assert e.confidence == 0.9       # 置信度本身满档
        assert e.status == "review"      # 规则C：疑似弹出强制复核


class Test小筐像素下限:
    """v9（2026-08-20 取证 work/_v8_analysis/shift_eval.py 消融）：像素下限
    只兜底绝对测量误差（标定点击/插值/配准残差 ~6px，不随筐大小缩放），
    不再吸收镜头漂移（漂移由位移配准修根）。筐宽 ≥13px 时 6px 下限永不
    生效——规则A 按纯相对 0.45/0.30 工作、随拍摄远近自动缩放；v8 的
    24/16px 在筐 25px 的更远机位上会让下限=0.96 筐宽、规则A 静默失效。
    实体阈值 v8 起纯相对（0.1×筐面积），本组保留对应用例。"""

    # 720p 取证同款小筐：rim_y = 140+0.35*14 = 144.9，cx = 317.5
    SMALL = Calibration(hoop_rect=(300, 140, 35, 14),
                        hsv_low=(0, 0, 0), hsv_high=(179, 255, 255),
                        frame_size=(640, 360))
    # 极小筐（模拟拍得更远）：rim_y = 140+0.35*10 = 143.5，cx = 305
    TINY = Calibration(hoop_rect=(300, 140, 10, 10),
                       hsv_low=(0, 0, 0), hsv_high=(179, 255, 255),
                       frame_size=(640, 360))

    def spts(self, seq, area=60.0):
        """默认 60px²：≥ 小筐实体线 0.1×35×14=49（旧 130 绝对底达不到）。"""
        return [BallPos(t=t, x=x, y=y, area=area) for t, x, y in seq]

    def _traj(self, x):
        """竖直全轨迹（上方 3 点 + 下方 3 点），穿越 x 由参数给定。"""
        return self.spts([(10.0, x, 100), (10.1, x, 118), (10.25, x, 141),
                          (10.35, x, 148.6), (10.5, x, 160), (10.6, x, 175)])

    def _tiny_traj(self, x):
        """极小筐版：rim_y=143.5、band=2.5，上下点须出 ±2.5 条带。"""
        return [BallPos(t=t, x=x, y=y, area=60.0) for t, x, y in
                [(10.0, x, 100), (10.1, x, 118), (10.25, x, 140),
                 (10.35, x, 146.5), (10.5, x, 160), (10.6, x, 175)]]

    def test_小筐off050_超相对红线仍降级(self):
        # off=+0.50（17.5px）：6px 下限在 35px 筐上不生效（6/35=0.17<0.45），
        # 纯相对 0.45 拦截——球心出筐沿即几何不可能（漂移已由配准修正，
        # 消融实测：校正后真进球 off 全部 −0.12~+0.26）
        e = decide_goals(self._traj(317.5 + 17.5), self.SMALL)[0]
        assert e.cross_off == pytest.approx(0.5, abs=1e-3)
        assert e.status == "low"
        assert "筐口两端" in e.low_reason

    def test_小筐off040_压筐沿复核带正常(self):
        # off=+0.40（14px）：0.30~0.45 复核带随筐宽缩放，小筐与大筐行为一致
        e = decide_goals(self._traj(317.5 + 14.0), self.SMALL)[0]
        assert e.status == "review"
        assert e.low_reason is None

    def test_大筐同偏移_行为一致仍降级(self):
        # 同 off=+0.50 在大筐上同样降级——三档阈值全部随筐宽缩放
        e = decide_goals(self._traj(340 + 40.0), CALIB)[0]
        assert e.status == "low"
        assert "筐口两端" in e.low_reason

    def test_极小筐_下限兜底_容差不小于测量误差(self):
        # 筐宽 10px：纯相对 0.45 只有 4.5px，小于 ~6px 测量误差 → kill 下限
        # 抬到 0.6 筐宽；off=+0.50（5px）在误差预算内不按几何不可能杀
        e = decide_goals(self._tiny_traj(305 + 5.0), self.TINY)[0]
        assert e.cross_off == pytest.approx(0.5, abs=1e-3)
        assert e.status == "review"          # 但超复核下限 0.4 → 强制复核
        assert e.low_reason is None

    def test_极小筐_超下限仍被拦截(self):
        # 同 10px 筐，off=+0.70（7px）超 6px 下限（=0.6 筐宽）→ 几何不可能
        e = decide_goals(self._tiny_traj(305 + 7.0), self.TINY)[0]
        assert e.status == "low"
        assert "筐口两端" in e.low_reason

    def test_小筐实体阈值相对化_60px平方算球体(self):
        # 60px² ≥ 0.1×筐面积 49 → 穿筐后再现有实体；旧 130 绝对底会误判
        # 「全程无球体量级检出」把真进球打低（取证 5 条真进球即此形态）
        e = decide_goals(self._traj(317.5), self.SMALL)[0]
        assert e.status == "high"
        assert e.below_big == 2

    def test_大筐实体阈值不变_300px平方仍不够球体(self):
        # 0.1×80×40=320：300px² 在大筐上仍是碎片级（与旧 max(130,320) 一致）
        positions = [BallPos(t=t, x=x, y=y, area=300.0) for t, x, y in
                     [(10.0, 340, 100), (10.1, 340, 120), (10.25, 340, 143),
                      (10.35, 340, 165), (10.5, 340, 190), (10.6, 340, 215)]]
        e = decide_goals(positions, CALIB)[0]
        assert e.status == "low"
        assert "球体量级" in e.low_reason


class TestV18档位规则:
    """v18（2026-10-11 问题②，spec §3.3/3.4）：R1 升绿 / P1d 降档两条 status
    旁路删除——档位 = 硬门档 if 硬门 else 阈值分箱(几何 conf)；网分是后置
    pass 的加减分项（见 test_net_motion）。本类钉删除后的边界行为：
    R1 曾救的「干净居中被遮挡真球」回到 review；P1d 曾压的「长悬停+下方
    短现」回到 high（涮框形态交给网分/人工复核兜住）。"""

    def test_R1形态_待复核但干净居中无上轨迹_升绿只走分箱(self):
        # 上 1 点 + 下双点短现：conf 0.72 ≥0.60（v19 HIGH_CONF）→ 分箱 high。
        # （v23 短档 0.14→0.17 后 0.69→0.72，档位不变）
        # R1 已删：升绿只可能靠分数够线，不靠「干净居中」旁路；档位随阈值
        # 联动的记录见 test_R1_偏心025_口径变化后升绿（0.66）。
        positions = pts([(30.0, 340, 100), (30.2, 340, 190), (30.25, 340, 205)])
        e = decide_goals(positions, CALIB)[0]
        assert e.confidence == 0.72           # 阈上：分箱即 high
        assert e.status == "high"             # v18：无旁路——档位纯看分箱

    def test_R1_偏心025_口径变化后升绿(self):
        # 同形态但 off=+0.25（x=360）：介于 0.20 红线与 0.30 压筐沿线之间。
        # v19（2026-10-18）HIGH_CONF 0.65→0.60 后，这条已够线 → 升绿（v23 短档
        # 上调后 0.63→0.66）。
        # 属口径变化的有意结果（六场查准 60.80→66.18% 的一部分），不是回归：
        # 未压筐沿的偏心行现在必然过线；仍待复核的只剩 off>0.30 的压筐沿硬门档。
        positions = pts([(30.0, 360, 100), (30.2, 360, 190), (30.25, 360, 205)])
        e = decide_goals(positions, CALIB)[0]
        assert e.confidence == 0.66           # v19 阈上（v18 时 <0.65 → review）
        assert e.status == "high"

    def test_R1_上方有轨迹的待复核_不升(self):
        # 上方 4 点（悬停轨迹）——假待复核的常见形态，不升绿。
        # 下方只给 1 点（无独立下方实体）→ entity 硬门 review，阈值无关
        positions = pts([(30.0, 340, 100), (30.02, 340, 110),
                         (30.04, 340, 120), (30.06, 340, 130),
                         (30.2, 340, 190)])
        e = decide_goals(positions, CALIB)[0]
        assert e.above_n == 4
        assert e.status == "review"

    def test_R1_弹出迹象压过升绿(self):
        # 干净居中、无上轨迹，但穿后 0.42s 弹回上方（规则C）→ 弹出嫌疑
        # 保持待复核（消融数据里被升绿的真进球无一有弹出）。v18 单测
        # pop_back 硬门标记：下方先给一个球体点（不让 entity 门同触发，
        # 硬门优先级 entity > geom > pop_back——同触发时标最高优先级）
        positions = pts([(30.0, 340, 100), (30.2, 340, 190),
                         (30.5, 340, 210), (30.62, 340, 110)])
        e = decide_goals(positions, CALIB)[0]
        assert e.pop_back == pytest.approx(0.42)
        assert e.status == "review"
        assert e.review_reason == "pop_back"

    def test_P1d形态_长悬停加下方短现_不再压档(self):
        # 上方 8 点悬停 0.7s + 下方只 0.03s：conf 0.87（v23，原 0.84）→ 分箱 high（P1d 已删，
        # 涮框/飞越形态由网分与人工复核兜住——八场定标验证假绿面）
        positions = pts([(40.0 + k * 0.1, 340, 120) for k in range(8)])
        positions += pts([(40.75, 340, 190), (40.78, 340, 200)])
        e = decide_goals(positions, CALIB)[0]
        assert e.confidence == 0.87
        assert e.above_n == 8 and e.below_span <= 0.30
        assert e.status == "high"

    def test_P1d_长悬停但下方持续_保持绿(self):
        # 同样上方 8 点，但穿筐后下方持续 0.5s+（真进球的豁免形态）
        positions = pts([(50.0 + k * 0.1, 340, 120) for k in range(8)])
        positions += pts([(50.75, 340, 190), (50.9, 340, 205),
                          (51.25, 340, 230)])
        e = decide_goals(positions, CALIB)[0]
        assert e.above_n == 8 and e.below_span > 0.30
        assert e.status == "high"


class Test兜底路径:
    """v11（2026-08-31）：上方轨迹整体缺位的进球兜底。truth/2026-08-28
    19:12 取证——斜射入筐 4 个上方点 3 个出横向容差、1 个与红网同帧
    面积并列被每帧 max 截胡 → 判定器眼里无上方点 → 配对无从发生 →
    静默漏检。该球 below 证据完美（10 点连续下坠、实体级、居中）。
    兜底：筐口正下方的实体级下落链注入虚拟上方起点走主配对循环；
    三场 truth 消融核心指标零变化、08-28 漏检 1→0、新增行全 low/review。
    CALIB 筐高 40：链首窗 y∈[164,214]、min_entity=320、descent≥6.4px。"""

    def test_纯下方下落链_生成兜底事件且不升绿(self):
        # 19:12 形态：无任何上方点，筐口正下实体级链首+连续下坠
        positions = pts([(100.0, 340, 170), (100.1, 340, 180),
                         (100.2, 340, 195), (100.3, 340, 215),
                         (100.4, 340, 240)])
        events = decide_goals(positions, CALIB)
        assert len(events) == 1
        # 伪起点与链首同 x、内插落筐心 → off=0、conf 0.75 够 high 线，
        # v18 起挂 fallback 硬门压绿（原直接改 status；不挂标记会被网分
        # 重分箱推回 high，违背 v11「兜底不升绿」裁定）
        assert events[0].status == "review"
        assert events[0].review_reason == "fallback"
        assert events[0].confidence == 0.75

    def test_兜底事件偏筐沿_主循环几何降级照常生效(self):
        # 链首偏筐口一端：主循环内插 off 大 → low（几何不可能类），兜底
        # 不豁免主循环的降级规则
        positions = pts([(110.0, 380, 170), (110.1, 380, 185),
                         (110.2, 380, 200), (110.3, 380, 220)])
        events = decide_goals(positions, CALIB)
        assert len(events) == 1
        assert events[0].status == "low"
        assert events[0].low_reason is not None

    def test_已有事件两秒半内不注入(self):
        # 标准真球事件后 1.2s 的下落链：属同波/弹跳，不重复生成
        positions = pts([(10.0, 340, 100), (10.1, 340, 120), (10.2, 340, 145),
                         (10.3, 340, 165), (10.4, 340, 190), (10.5, 340, 215)])
        positions += pts([(11.5, 340, 170), (11.6, 340, 185), (11.7, 340, 205)])
        events = decide_goals(positions, CALIB)
        assert len(events) == 1

    def test_平行链_无下落_不注入(self):
        # 筐下水平漂移的三点（被抱住的球横走）：descent 不达标
        positions = pts([(120.0, 340, 170), (120.1, 350, 170), (120.2, 360, 170)])
        assert decide_goals(positions, CALIB) == []

    def test_链首为碎片_不注入(self):
        # 链首面积 20 < min_entity 320：人肤/碎片，不构成下落实体
        positions = pts([(130.0, 340, 170), (130.1, 340, 185),
                         (130.2, 340, 200)], area=20)
        assert decide_goals(positions, CALIB) == []

    def test_链首低于筐口一倍半筐高_不注入(self):
        # y=216 > rim_y+1.5*40=214：离筐口太远，下落与进球无关（人群里运球）
        positions = pts([(140.0, 340, 220), (140.1, 340, 235),
                         (140.2, 340, 250), (140.3, 340, 265)])
        assert decide_goals(positions, CALIB) == []

    def test_正常轨迹零影响_远处兜底共存(self):
        # 标准 high 真球 + 时间上远离（>2.5s）的一条下落链：1 high + 1 兜底，
        # 既有事件的档位/字段不变
        std = pts([(10.0, 340, 100), (10.1, 340, 120), (10.2, 340, 145),
                   (10.3, 340, 165), (10.4, 340, 190), (10.5, 340, 215)])
        chain = pts([(20.0, 340, 170), (20.1, 340, 185), (20.2, 340, 205)])
        events = decide_goals(std + chain, CALIB)
        assert len(events) == 2
        assert events[0].status == "high"      # 原事件不被兜底改动
        assert events[1].status == "review"    # 远处链走兜底


class Test碎片链实体等价:
    """v15（2026-09-25 E2 轮）：小筐（38px）上球穿网后检出被撕成 10~50px²
    碎片、普遍 <min_entity（0.1×筐面积），09-19 有 11 条真球死在
    「全程无球体量级检出」。八场全集分布（13 真/77 假）：碎片链「贯穿网深
    的严格单调下行」（≥3 点、y 跨度 ≥2 筐高、步步向下）真 6/假 0——
    该形态物理上只能是球穿网下坠；mono 放宽到 0.5 立刻 8/6 崩，严格
    单调是唯一分离器。过判据的行不再挂量级缺失 low_reason，按主流程
    （conf/几何/网门）正常定档。筐宽硬门 hw<45（FRAG_SMALL_HW）：碎片
    撕裂是小筐结构性问题，大筐上不需要救济、反而开面（09-11 t=11.14
    片头热身机位假绿）。"""

    # 小筐夹具：hw=40<45、cx=340/hh=40 与 CALIB 对齐——上方/下方坐标
    # 全沿用，min_entity=160（碎片 a=20 仍欠线）
    SMALL = Calibration(hoop_rect=(320, 140, 40, 40),
                        hsv_low=(0, 0, 0), hsv_high=(179, 255, 255),
                        frame_size=(640, 360))

    def test_贯穿网深的单调碎片链_不再降级低置信(self):
        # 下方全是 a=20 碎片（<min_entity 160），但 3 点严格下行、
        # y 跨度 165→245=80=2×筐高——实体等价，走正常档位
        positions = (pts([(10.0, 340, 100), (10.1, 340, 120), (10.2, 340, 135)])
                     + pts([(10.3, 340, 165), (10.5, 340, 205),
                            (10.7, 340, 245)], area=20))
        e = decide_goals(positions, self.SMALL)[0]
        assert e.low_reason is None
        assert e.status == "high"
        assert e.below_big == 0               # 依据可查：实体缺失仍属实

    def test_大筐同形态_硬门外仍降级(self):
        # 同一条完美单调链在 hw=80 大筐上：碎片撕裂非大筐结构问题，
        # 硬门（FRAG_SMALL_HW）外不适用实体等价（09-11 t=11.14 假绿收门）
        positions = (pts([(10.0, 340, 100), (10.1, 340, 120), (10.2, 340, 135)])
                     + pts([(10.3, 340, 165), (10.5, 340, 205),
                            (10.7, 340, 245)], area=20))
        e = decide_goals(positions, CALIB)[0]
        assert e.status == "low"
        assert "球体量级" in e.low_reason

    def test_碎片链中途上弹_不满足严格单调_仍降级(self):
        # 下行中出现一步上弹（245→200）：弹跳/涮框碎片不是穿网形态
        positions = (pts([(10.0, 340, 100), (10.1, 340, 120), (10.2, 340, 135)])
                     + pts([(10.3, 340, 165), (10.5, 340, 245),
                            (10.7, 340, 200)], area=20))
        e = decide_goals(positions, self.SMALL)[0]
        assert e.status == "low"
        assert "球体量级" in e.low_reason

    def test_碎片链纵向跨度不足_仍降级(self):
        # 3 点单调但 y 跨度 40 < 2×筐高 80：没贯穿网深，证据不足
        positions = (pts([(10.0, 340, 100), (10.1, 340, 120), (10.2, 340, 135)])
                     + pts([(10.3, 340, 165), (10.5, 340, 185),
                            (10.7, 340, 205)], area=20))
        e = decide_goals(positions, self.SMALL)[0]
        assert e.status == "low"
        assert "球体量级" in e.low_reason

    def test_两点碎片链_不适用实体等价(self):
        # 只有 2 个下方点（含穿越点）：链太短，八场消融里 2 点门只值
        # +2 真/+2 假，维持低置信
        positions = (pts([(10.0, 340, 100), (10.1, 340, 120)])
                     + pts([(10.3, 340, 165), (10.7, 340, 245)], area=20))
        e = decide_goals(positions, self.SMALL)[0]
        assert e.status == "low"
        assert "球体量级" in e.low_reason

    def test_几何拦截优先级不变(self):
        # 碎片链形态完美但球心出筐口两端（off=0.625>0.45）：规则 A 先挂
        # low_reason，实体等价不豁免几何不可能
        positions = (pts([(10.0, 365, 100), (10.1, 365, 120)])
                     + pts([(10.3, 365, 165), (10.5, 365, 205),
                            (10.7, 365, 245)], area=20))
        e = decide_goals(positions, self.SMALL)[0]
        assert e.status == "low"
        assert "筐口两端" in e.low_reason


class Test兜底两点链小筐:
    """v15（2026-09-25 E2 轮）：小筐（hw<45）链点门槛 3→2。09-19 1048.1
    取证——38px 筐上球入网后只剩两点链（链首 a=71.5 本过实体线，n=2 被
    FALLBACK_MIN_N 拦死 → 静默漏检）。八场消融：全放宽 +44 噪声行，其中
    26 条集中在大筐场（09-04 +11/09-11 +10/09-12 +5——大筐碎片多、两点
    链是噪声），小筐三场仅 +10 且救回该球 → 按 NET_RELEASE_MIN_HW 同款
    硬门限只对小筐放宽。"""

    SMALL = Calibration(hoop_rect=(300, 140, 35, 14),
                        hsv_low=(0, 0, 0), hsv_high=(179, 255, 255),
                        frame_size=(640, 360))

    def test_小筐两点下落链_生成兜底事件(self):
        # 链首 y=160 ∈[152.6,170.1]、a=400≥49、第二点 0.3s 内下行 40px
        positions = pts([(200.0, 317, 160), (200.3, 317, 200)])
        events = decide_goals(positions, self.SMALL)
        assert len(events) == 1
        assert events[0].status != "high"       # 兜底口径：只 review/low

    def test_大筐两点链_维持三点门_不注入(self):
        # 同形态在 hw=80 大筐上：碎片生态不同，两点链不构成兜底证据
        positions = pts([(210.0, 340, 170), (210.3, 340, 210)])
        assert decide_goals(positions, CALIB) == []


def test_旧档缺review_reason键可加载():
    # v18 前的 events.json 没有 review_reason 键——from_dict 必须缺省 None
    e = GoalEvent.from_dict({"t": 1.0, "confidence": 0.6, "status": "review"})
    assert e.review_reason is None and e.net_post is None


class Test面积硬门:
    """v24（2026-11-14）：穿筐物投影面积 ÷ 筐矩形面积 > AREA_KILL_REL → review。

    动机：conf 只吃点数/跨度/横偏，不认识「穿过去的是什么东西」——手/小臂
    横穿筐口时几何分能拿满、稳稳混进 high 变成会剪错的假绿。真球实测
    0.12~0.16（659 条命中最大 0.488），被抓的大块物是同场真球的 4~7 倍。
    新六场 33 条降档、误降 0 条 → 查准 76.36→79.40%、覆盖不变。

    门本身是**后置纯降级**（`apply_area_gate`，管线里排在网分重分箱之后），
    判定器只负责把 cross_area_rel 记进事件。
    """

    # 本 CALIB 筐矩形 80×40 = 3200px²，故阈值线 = 1600px²
    def _traj(self, cross_area):
        """完整上下穿轨迹；穿筐点（t=10.3, y=165）单独指定面积。"""
        return [
            BallPos(t=10.0, x=340, y=100, area=400),
            BallPos(t=10.1, x=340, y=120, area=400),
            BallPos(t=10.2, x=340, y=145, area=400),
            BallPos(t=10.3, x=340, y=165, area=cross_area),
            BallPos(t=10.4, x=340, y=190, area=400),
            BallPos(t=10.5, x=340, y=215, area=400),
        ]

    def test_正常球径_不触发(self):
        ev = apply_area_gate(decide_goals(self._traj(400), CALIB))
        assert len(ev) == 1
        assert ev[0].status == "high"
        assert ev[0].review_reason is None
        assert ev[0].cross_area_rel == pytest.approx(0.125, abs=1e-6)

    def test_阈值线下_不触发(self):
        # 1568/3200 = 0.49
        ev = apply_area_gate(decide_goals(self._traj(1568), CALIB))
        assert ev[0].status == "high" and ev[0].review_reason is None

    def test_阈值线上_降级复核(self):
        # 1632/3200 = 0.51
        ev = apply_area_gate(decide_goals(self._traj(1632), CALIB))
        assert len(ev) == 1
        assert ev[0].status == "review"
        assert ev[0].review_reason == "area"
        assert ev[0].cross_area_rel == pytest.approx(0.51, abs=1e-6)
        # 只降档、不减分：conf 与同形态小球完全一致（0.30+0.17+0.17+0.20）
        assert ev[0].confidence == 0.84

    def test_判定器自身不落档_只记字段(self):
        """门在管线后置，decide_goals 的输出仍是纯几何档（L0/L1 分层的前提）。"""
        ev = decide_goals(self._traj(1632), CALIB)
        assert ev[0].status == "high"          # 未被判定器降档
        assert ev[0].review_reason is None
        assert ev[0].cross_area_rel == pytest.approx(0.51, abs=1e-6)

    def test_面积门不动存量硬门标记(self):
        """已有硬门（entity）的行不被改成 "area"——存量标记零漂移。"""
        traj = self._traj(1632)
        for p in traj[3:]:            # 穿筐点之后全变成碎片量级 → 实体未再现
            p.area = 20
        traj[3].area = 1632           # 穿越点本身仍是实体量级
        ev = apply_area_gate(decide_goals(traj, CALIB))
        assert ev[0].review_reason == "entity"

    def test_网分压成low的行不被抬回review(self):
        """★ 关键回归：门必须在网分重分箱之后。

        10-23 t=2647.78 实证——几何 0.66（够 high）被网分扣 0.26 成 0.40
        （low）。若在判定器里挂 review_reason，网分那一步就不再重分箱，
        这行反而从 low 升成 review。后置门按最终档位判断，天然只降不升。
        """
        ev = decide_goals(self._traj(1632), CALIB)
        ev[0].confidence = 0.40       # 模拟网分扣分后的结果
        ev[0].status = "low"
        apply_area_gate(ev)
        assert ev[0].status == "low"
        assert ev[0].review_reason is None

    def test_B2注入行无面积字段_跳过(self):
        e = GoalEvent(t=5.0, confidence=0.6, status="review",
                      review_reason="b2")
        apply_area_gate([e])
        assert e.review_reason == "b2"


def test_旧档缺cross_area_rel键可加载():
    # v24 前的 events.json 没有该键 → None（不参与任何判定复算）
    e = GoalEvent.from_dict({"t": 1.0, "confidence": 0.6, "status": "review"})
    assert e.cross_area_rel is None
