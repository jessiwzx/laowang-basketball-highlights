"""vrender 单测：相机路径纯函数（规格与兜底）→ 渲染 → 组装。"""
import math

import pytest

from bbh.vrender import (CFG, GoalCam, RenderConfig, _crop_frame, _blur_bg,
                         camera_centers, crop_width, visible_width)


def _cam(**kw):
    """conftest 合成视频口径：640×360@30fps。默认进球 t=10.0，片窗 5~12s，
    出手人 x=280，筐 x=340（dist=60——足够近，峰值不破 0.45×裁剪宽）。"""
    base = dict(t=10.0, start=5.0, end=12.0, shooter_x=280.0,
                release_t=8.9, hoop_x=340.0,
                frame_w=640, frame_h=360, fps=30.0)
    base.update(kw)
    return GoalCam(**base)


class TestCropWidth:
    def test_1080源_裁557(self):
        assert 556 <= crop_width(1080) <= 558   # 1080×660/1280=556.9

    def test_合成源_按比例(self):
        assert 185 <= crop_width(360) <= 187    # 360×660/1280=185.6


import cv2
import numpy as np


class TestContentScale:
    """内容缩放（2026-09-25 用户裁定档1 k=0.80：「比例不变、整体画面内容
    增多、人和球变小」）：画幅仍是 out_w×out_h，内容按 k 缩小居中、上下
    模糊留白，可见源窗按 1/k 变宽。k=1.0 必须与旧口径逐位相同。"""

    def _frame(self, h=720, w=1280, seed=0):
        # 低通噪声（真画面的空间频谱，重采样后仍可模板匹配；纯白噪声在
        # 两种缩放间的重采样差异会虚低相关）
        rng = np.random.default_rng(seed)
        raw = rng.integers(0, 256, (h, w, 3), dtype=np.uint8)
        return cv2.GaussianBlur(raw, (0, 0), 3.0)

    def test_可见窗宽_按k放大(self):
        assert visible_width(360, RenderConfig(content_scale=1.0)) == 186.0
        assert visible_width(360, RenderConfig(content_scale=0.8)) == 232.5
        assert visible_width(1080, RenderConfig(content_scale=0.5)) == 1114.0

    def test_k1_与旧口径逐位相同(self):
        # 旧口径（内容吃满画幅）的 affine 逐位复算——k=1 不许漂一个像素
        cfg = RenderConfig(content_scale=1.0)
        cw = crop_width(720, cfg)
        for cx in (640.0, 300.5):               # 居中 + 偏心（浮点中心）
            s = cfg.out_w / cw
            m = np.float32([[s, 0.0, cfg.out_w / 2.0 - s * cx],
                            [0.0, s, cfg.out_h / 2.0 - s * 720 / 2.0]])
            want = cv2.warpAffine(self._frame(), m, (cfg.out_w, cfg.out_h),
                                  flags=cv2.INTER_LINEAR,
                                  borderMode=cv2.BORDER_REPLICATE)
            got = _crop_frame(self._frame(), cx, cw, cfg)
            assert got.shape == want.shape
            assert np.array_equal(got, want)

    def test_缩小档_内容带与留白几何(self):
        cfg = RenderConfig(content_scale=0.8)        # 默认 fill_mode="black"
        fr = self._frame()
        cw = crop_width(720, cfg)
        out = _crop_frame(fr, 640.0, cw, cfg)
        assert out.shape == (cfg.out_h, cfg.out_w, 3)      # 画幅比例不变
        y0 = (cfg.out_h - round(cfg.out_h * 0.8)) // 2     # 128
        assert y0 == 128
        assert not out[:y0].any()                          # 上留白=纯黑
        assert not out[y0 + 1024:].any()                   # 下留白=纯黑
        assert out[y0:y0 + 1024].any()                      # 内容带非空

    def test_缩小档_留白可切回模糊底(self):
        # fill_mode="blur" 即 2026-09-25 旧口径，必须逐位还原
        cfg = RenderConfig(content_scale=0.8, fill_mode="blur")
        fr = self._frame()
        out = _crop_frame(fr, 640.0, crop_width(720, cfg), cfg)
        y0 = 128
        bg = _blur_bg(fr, cfg)
        assert np.array_equal(out[:y0], bg[:y0])           # 上留白=模糊底
        assert np.array_equal(out[y0 + 1024:], bg[y0 + 1024:])

    def test_内容是真的缩k_无畸变(self):
        # 模板匹配量实际尺度：k=1 帧的一块纹理在 0.8 档里最佳尺度应为 0.8
        # （唯一缩放 ⇒ 横竖同比，无畸变）
        fr = self._frame(seed=7)
        c1 = RenderConfig(content_scale=1.0)
        c8 = RenderConfig(content_scale=0.8)
        ref = _crop_frame(fr, 640.0, crop_width(720, c1), c1)
        got = _crop_frame(fr, 640.0, crop_width(720, c8), c8)
        band = got[128:128 + 1024]                          # 内容带
        patch = ref[500:700, 200:400]
        best = max(((cv2.minMaxLoc(cv2.matchTemplate(
            band, cv2.resize(patch, (w, w), interpolation=cv2.INTER_AREA),
            cv2.TM_CCOEFF_NORMED))[1], w / 200.0)
            for w in range(60, 200, 2)))
        assert abs(best[1] - 0.8) < 0.02 and best[0] > 0.9

    def test_缩小档_相机钳制按可视窗(self):
        # 窗宽 > cw：仍按 cw 钳制会露出源画面外的空白 ⇒ 边界随 k 外扩
        g = _cam(shooter_x=10.0, hoop_x=630.0)
        c1 = camera_centers(g, RenderConfig(content_scale=1.0))
        c8 = camera_centers(g, RenderConfig(content_scale=0.8))
        h1, h8 = visible_width(360, RenderConfig(content_scale=1.0)) / 2, \
            visible_width(360, RenderConfig(content_scale=0.8)) / 2
        assert min(c1) >= h1 - 1e-6 and max(c1) <= 640 - h1 + 1e-6
        assert min(c8) >= h8 - 1e-6 and max(c8) <= 640 - h8 + 1e-6
        assert min(c8) > min(c1)                            # 边界确实外扩

    def test_全源可见_相机锁死居中(self):
        # k=1/源宽×帧高（360 源 → k≈0.29）以下窗比源宽，只能居中锁死
        cs = camera_centers(_cam(shooter_x=10.0, hoop_x=630.0),
                            RenderConfig(content_scale=0.25))
        assert all(c == 320.0 for c in cs)

    def test_k不改时间线帧数(self):
        g = _cam()
        assert (len(camera_centers(g, RenderConfig(content_scale=1.0)))
                == len(camera_centers(g, RenderConfig(content_scale=0.8))))


class TestCameraCenters:
    def test_无出手人_全程锁筐(self):
        cs = camera_centers(_cam(shooter_x=None))
        assert len(cs) == round(7.0 * 30) and all(c == 340.0 for c in cs)

    def test_无出手人_有跟球链_全程跟链_链尾后锁筐(self):
        # 用户 2026-09-16：出手前运球人要保持在画面横向中间。无出手锚的球
        # （从未跑过出手人识别，或 spot=bad——镜头锁筐人运球走出画面）
        # 用进球锚跟球链兜运镜：链尾=进球锚=筐，运球段球在手=跟人，飞行段
        # 跟球，链尾后（进球后）锁筐
        # 链值须落在可视窗内（合成源 640×360、k=0.8 ⇒ 钳制 [232.5, 407.5]）
        follow = [(5.0, 400.0), (7.0, 370.0), (10.0, 340.0)]   # 链尾=筐
        cs = camera_centers(_cam(shooter_x=None), CFG, follow)
        assert len(cs) == round(7.0 * 30)
        assert abs(cs[0] - 400.0) < 20.0                    # 片头跟链首
        assert 360.0 < cs[round((5.5 - 5.0) * 30)] < 400.0  # 跟着球走
        i_tail = round((10.0 - 5.0) * 30)                   # 链尾=进球时刻
        assert abs(cs[i_tail] - 340.0) < 10.0               # 链尾已在筐上
        # 平滑半窗（9//2=4 帧）后精确锁筐；窗口内允许平滑滞后
        assert all(c == 340.0 for c in cs[i_tail + 4:])

    def test_无出手人_lock_goal_进球前收镜头到筐(self):
        # 2026-11-01 用户口径「篮筐应该在视频中间才对」：无出手锚（没跑过
        # 出手人识别）的球，链在飞行末段才把球带到筐附近，穿网那一刻镜头
        # 还在平移途中——实测 2026-10-30 三球穿网时筐落在输出 x≈263
        # （正中 330）。lock_goal=True 在 t−pan_gap 前把镜头收到筐上。
        # 链：球到 9.7s 都还在 400，最后 0.3s 才突进到筐 340（10-30 实测
        # 形态——垂直上篮，球横向几乎不动，链尾才跳到筐）
        follow = [(5.0, 400.0), (9.7, 400.0), (10.0, 340.0)]
        i_pan_end = round((10.0 - CFG.pan_gap - 5.0) * 30)     # 9.65s
        off = camera_centers(_cam(shooter_x=None), CFG, follow)
        on = camera_centers(_cam(shooter_x=None), CFG, follow, lock_goal=True)
        assert len(on) == len(off) == round(7.0 * 30)          # 帧数不变
        # 关掉时：pan 截止时刻仍差筐一截（这就是用户看到偏左的那一帧）
        assert off[i_pan_end] - 340.0 > 30.0
        # 打开后：pan 截止即锁筐，进球时刻精确在筐上
        assert abs(on[i_pan_end] - 340.0) < 1.0
        assert abs(on[round((10.0 - 5.0) * 30)] - 340.0) < 1.0
        # 但不能提前抢跑：估计出手时刻（t−default_lead=8.9s）之前仍跟球
        assert all(abs(c - 400.0) < 1.0
                   for c in on[:round((8.9 - 5.0) * 30) - 2])
        # 平移是连续的（缓动），不出现整段突跳
        steps = [abs(on[i + 1] - on[i]) for i in range(len(on) - 1)]
        assert max(steps) < 40.0

    def test_无出手人_链断在半途_链首前持链首值(self):
        # 跟球链只在 [6.5, 10.0]：6.5s 之前镜头持链首值（不瞎猜更早位置）
        follow = [(6.5, 390.0), (10.0, 340.0)]
        cs = camera_centers(_cam(shooter_x=None), CFG, follow)
        # 滑平均是「9 项 ×1/9 求和」——浮点上有 1e-13 级残差（450 那份
        # 数值恰好舍回整数才显得精确），按浮点口径比
        assert abs(cs[round((5.2 - 5.0) * 30)] - 390.0) < 1e-6

    def test_hold与lock相位(self):
        # 出手后停留 release_hold（实机问题1 2026-09-25「出手动作还没结束
        # 镜头就跟着球跑了」）：avail 0.75、pan_min 0.5 ⇒ hold 缩到 0.25、
        # pan 起 9.15 = 出手 8.9 + 停留，pan 终 9.65 起贴筐。
        cs = camera_centers(_cam())
        assert cs[0] == 280.0
        assert cs[-1] == 340.0
        assert cs[round((9.1 - 5.0) * 30)] == 280.0   # 停留段仍锁出手人
        mid = cs[round((9.4 - 5.0) * 30)]             # pan 中点（u=0.5）
        assert 280.0 < mid < 340.0

    def test_出手后停留满release_hold_时间充裕(self):
        # avail 1.65 给得起整段 0.3 停留：出手后 0.3s 镜头仍贴出手人，
        # 之后才起 pan（need 按可视窗宽算：k=0.8 下 need≈0.90 ⇒ pan 起
        # 9.65−0.90=8.75，整段停留照样给足）
        cs = camera_centers(_cam(release_t=8.0))
        assert cs[round((8.3 - 5.0) * 30)] == 280.0   # 出手 +0.30s 未动
        assert cs[round((9.0 - 5.0) * 30)] > 280.0    # 之后在平移

    def test_停留挤不过pan_min_整段让位(self):
        # avail 0.35 连 pan_min 0.5 都不够 ⇒ 停留整段让位（hold=0，旧口径）：
        # 出手即起 pan，压缩到 avail 内完成、不误推迟到进球后才锁筐
        cs = camera_centers(_cam(release_t=9.3))
        assert cs[round((9.3 - 5.0) * 30)] == 280.0
        assert cs[round((9.4 - 5.0) * 30)] > 280.0
        assert all(c == 340.0 for c in cs[round((9.65 - 5.0) * 30):])

    def test_钳制不出画面(self):
        cs = camera_centers(_cam(shooter_x=10.0, hoop_x=630.0))
        half = visible_width(360) / 2        # 可视窗宽（含 content_scale）
        assert all(half - 1e-6 <= c <= 640 - half + 1e-6 for c in cs)

    def test_峰值速度受钳制(self):
        # 时间充裕（release 8.0 → avail 1.65 > need≈1.26）时 need 生效，
        # 峰值速度仍 ≤ 0.45×裁剪宽/秒
        cs = camera_centers(_cam(release_t=8.0))
        v = [abs(b - a) * 30.0 for a, b in zip(cs, cs[1:])]
        # 峰值速度按**可视窗宽**口径：输出侧恒为 peak_frac·out_w px/s（k 只
        # 改主体大小，不改运镜快慢）
        assert max(v) <= 0.45 * visible_width(360) * 1.02

    def test_出手前相机锁出手人(self):
        # 实机问题2：旧公式 need/pan_min 压过出手时刻，pan 抢在出手前起跑
        # （本例 pan 起 8.39 < 出手 8.9，出手瞬间镜头已在半途）。新契约：
        # 无跟球链时，出手时刻及之前相机静止锁在出手人身上（pan 恒不早于
        # 出手——跟球链版见 test_跟随链驱动lead段相机）。
        cs = camera_centers(_cam())
        assert all(c == 280.0 for c in cs[:round((8.9 - 5.0) * 30) + 1])

    def test_出手后才起pan_压缩时长仍按时锁筐(self):
        # avail=0.75 < need≈1.26 → pan 压缩到出手后才跑，但 pan_end 前完成
        cs = camera_centers(_cam())
        assert cs[round((9.2 - 5.0) * 30)] > 280.0            # 出手后已在移动
        assert all(c == 340.0 for c in cs[round((9.65 - 5.0) * 30):])

    def test_补篮_出手过近_跨慢放窗平移_进球瞬间恰锁筐(self):
        # 实机问题5（highlight_v2 片段2，t=118.71 补篮）：avail<0 时旧版
        # 整段锁筐，而运球人从画面右外侧一路运球接近——前 4 秒整段在裁剪
        # 窗外。新契约：pan 截止放宽到进球时刻（镜头跨进慢放窗继续平移，
        # minterpolate 拉伸后输出更缓），出手画面仍完整（pan 恒不早于出手），
        # 进球瞬间恰锁筐。
        cs = camera_centers(_cam(release_t=9.7))
        i_rel = round((9.7 - 5.0) * 30)
        assert all(c == 280.0 for c in cs[:i_rel + 1])   # 出手前锁出手人
        i_goal = round((10.0 - 5.0) * 30)
        assert all(c == 340.0 for c in cs[i_goal:])      # 进球起已到筐
        mid = cs[round((9.85 - 5.0) * 30)]               # 慢放窗内正在平移
        assert 280.0 < mid < 340.0

    def test_出手晚于进球_脏数据兜底整段锁筐(self):
        # release_t=10.4 > t=10.0：溯源脏数据（出手晚于进球不可能），放宽
        # pan 截止也凑不出时长 → 安全兜底整段锁筐，不拿脏数据运镜
        cs = camera_centers(_cam(release_t=10.4))
        assert all(c == 340.0 for c in cs)

    def test_跟随链驱动lead段相机(self):
        # 跟球运镜：pan 起点前相机沿 follow 链线性插值（人在运球接近，
        # 镜头跟着走——不再静止在出手点等）；链尾=出手点，pan 原点不变
        follow = [(5.0, 400.0), (7.0, 370.0), (8.9, 280.0)]
        cs = camera_centers(_cam(), CFG, follow)
        c55 = cs[round((5.5 - 5.0) * 30)]                # 链上 5.5s ≈ 392.5
        assert 385.0 < c55 < 400.0
        assert abs(cs[round((8.9 - 5.0) * 30)] - 280.0) < 20.0   # 出手点
        assert cs[-1] == 340.0                            # 锁筐收尾不变

    def test_链首前_相机持链首值(self):
        # 检测断链截断后，链首之前的时段镜头停在链首位置（不瞎猜更早的
        # 球位置）——人至少在画面里出现到断链点
        cs = camera_centers(_cam(), CFG, [(6.5, 450.0), (8.9, 280.0)])
        assert cs[round((5.2 - 5.0) * 30)] == 450.0

    def test_补篮跟人链_出手前跟球_进球锁筐(self):
        # 问题5 的完整形态：补篮类 avail<0 且有跟球链——运球段跟球、出手
        # 点起跨慢放窗平移、进球瞬间锁筐
        follow = [(5.0, 400.0), (7.0, 370.0), (9.7, 280.0)]
        cs = camera_centers(_cam(release_t=9.7), CFG, follow)
        assert abs(cs[0] - 400.0) < 20.0                       # 片头在链首附近
        assert 385.0 < cs[round((5.5 - 5.0) * 30)] < 400.0    # 跟着球走
        assert abs(cs[round((9.7 - 5.0) * 30)] - 280.0) < 20.0
        assert cs[round((10.0 - 5.0) * 30)] == 340.0

    def test_跟随链数值_钳制不出画面(self):
        # 链值可贴画面边（远景运球人在 x≈1861）——逐帧钳制保底
        follow = [(5.0, 600.0), (8.9, 630.0)]
        cs = camera_centers(_cam(), CFG, follow)
        half = visible_width(360) / 2
        assert all(half - 1e-6 <= c <= 640 - half + 1e-6 for c in cs)


from bbh.vrender import hook_window, plan_camera, slowmo_window, smooth_curve


class TestSmoothCurve:
    def test_阶跃边沿变斜坡(self):
        assert smooth_curve([0, 0, 0, 10, 10, 10]) == [0, 2, 4, 6, 8, 10]

    def test_短列原样返回(self):
        assert smooth_curve([1.0, 2.0], 5) == [1.0, 2.0]


class TestPlanCamera:
    def test_长度等于片窗帧数且无折角(self):
        # release 8.0 → avail 充裕，pan 按 need 走（峰值速度受钳制的口径）
        cs = plan_camera(_cam(release_t=8.0))
        assert len(cs) == 210
        # 相邻帧位移 ≤ pan 峰值帧步（平滑只会更缓，不会产生更大跳变）
        step = max(abs(b - a) for a, b in zip(cs, cs[1:]))
        assert step <= 0.45 * visible_width(360) / 30.0 * 1.05

    def test_跟球链透传_长度与帧步同样成立(self):
        # plan_camera 需把 follow 链透传给 camera_centers（漏传=整段静 hold，
        # 问题5 修复静默失效）。跟球段速度由真人运球决定（链斜率 120px/s
        # =4px/帧，本就快于 pan 舒适度上界——跟真实运动不是运镜炫技），
        # 帧步上界只钉 pan 段，这里钉长度与"真跟上了"
        follow = [(5.0, 520.0), (7.0, 400.0), (8.0, 280.0)]
        cs = plan_camera(_cam(release_t=8.0), CFG, follow)
        assert len(cs) == 210
        assert 480.0 < cs[round((5.5 - 5.0) * 30)] < 500.0   # 真跟上了
        assert cs[-1] == 340.0                                # 锁筐收尾


import numpy as np


class TestBallFollowX:
    """跟球引擎：顺序读 [start, rel] 每 follow_step 帧 HSV 检球候选，从
    出手点倒序最近邻链接（门 follow_radius、断口 ≤ follow_gap 桥接）。
    合成视频口径同 conftest：640×360@30，球 BGR(20,100,245)。"""

    BALL = (20, 100, 245)   # BGR（conftest 同款）

    def _ball_follow_x(self, *a, **kw):
        # 延迟 import：引擎未实现时本类用例各自见红，不连坐整文件收集
        from bbh.vrender import ball_follow_x
        return ball_follow_x(*a, **kw)

    def _make(self, path, hide=(), decoy=True):
        """300 帧：球在帧 150-267（t 5.0→8.9）从 x=550 走到 x=300（y=280，
        模拟运球接近出手）；左侧远处放一个同色静态干扰块（最近邻门须拒绝）；
        hide=帧号集合不画球（测断链/桥接）。"""
        vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                             30, (640, 360))
        try:
            for fi in range(300):
                fr = np.full((360, 640, 3), 50, np.uint8)
                cv2.line(fr, (300, 154), (380, 154), (180, 180, 180), 3)  # 灰筐线
                if decoy:
                    cv2.rectangle(fr, (96, 96), (108, 108), self.BALL, -1)
                if 150 <= fi <= 267 and fi not in hide:
                    x = int(round(550 - 250 * (fi - 150) / 117))
                    cv2.circle(fr, (x, 280), 9, self.BALL, -1)
                vw.write(fr)
        finally:
            vw.release()
        return str(path)

    def _calib(self):
        return Calibration(hoop_rect=(300, 140, 80, 40),
                           hsv_low=(0, 80, 120), hsv_high=(25, 255, 255),
                           frame_size=(640, 360))

    def _make_hijack(self, path):
        """640×360：球（r=4，球样 9×9 ≤ 门 10）从 x=550 走到 x=300（帧
        150-267，y=280）；帧 200-209（0.33s ≤ follow_gap）藏球，全程另放
        一个 21×31 非球样块（x460-480、y290-320，与球迹留 6px 不粘连）当
        劫链者。"""
        vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                             30, (640, 360))
        try:
            for fi in range(300):
                fr = np.full((360, 640, 3), 50, np.uint8)
                cv2.rectangle(fr, (460, 290), (480, 320), self.BALL, -1)
                if 150 <= fi <= 267 and not 200 <= fi <= 209:
                    x = int(round(550 - 250 * (fi - 150) / 117))
                    cv2.circle(fr, (x, 280), 4, self.BALL, -1)
                vw.write(fr)
        finally:
            vw.release()
        return str(path)

    def _follow(self, path, **kw):
        cap = cv2.VideoCapture(path)
        try:
            return self._ball_follow_x(cap, 5.0, 8.9, (300.0, 280.0),
                                       self._calib(), 640, 360, 30.0, **kw)
        finally:
            cap.release()

    def test_全链跟球_干扰块被拒(self, tmp_path):
        # 球全程可见：链头回到片窗起点附近（x≈550），链尾=出手点锚
        # （300, 280）——左侧同色静态干扰块不劫链（距离门外）
        chain = self._follow(self._make(tmp_path / "f1.mp4"))
        ts0, x0 = chain[0]
        tsN, xN = chain[-1]
        assert abs(tsN - 8.9) < 0.05 and abs(xN - 300.0) <= 15.0
        assert ts0 < 5.1 and 520.0 < x0 < 570.0
        # 时间对齐哨兵：末采样（锚前一帧，球已到出手位 ~302）——采样流
        # 若错位（曾实测半速错位前进）末采样会停在半途 ~430
        assert abs(chain[-2][1] - 300.0) < 25.0
        xs = [x for _, x in chain]
        assert all(a >= b - 15.0 for a, b in zip(xs, xs[1:]))   # 大致单调向筐

    def test_断口超桥接上限_链止于断点(self, tmp_path):
        # 中段藏球 61 帧（2s > follow_gap 0.5s）：倒序走到断口后 miss 超
        # 0.5s → 链止于断口右缘（帧 252，t≈8.4），出手锚照常收尾
        hide = set(range(190, 251))
        chain = self._follow(self._make(tmp_path / "f2.mp4", hide=hide))
        ts0, _ = chain[0]
        assert 8.3 < ts0 < 8.5
        assert abs(chain[-1][0] - 8.9) < 0.05

    def test_断口在桥接上限内_链跨缺口续上(self, tmp_path):
        # 中段藏球 13 帧（7 个采样点 ×2/30s ≈ 0.47s ≤ 0.5s）→ 桥接续链，
        # 链头仍回到片窗起点
        hide = set(range(200, 213))
        chain = self._follow(self._make(tmp_path / "f3.mp4", hide=hide))
        assert chain[0][0] < 5.1
        assert abs(chain[-1][0] - 8.9) < 0.05

    def test_非球样块劫链_球样候选更近则选球(self, tmp_path):
        # 实机问题3（2026-09-25「镜头跟着人走了，没捕捉到球」）：断口里只有
        # 一个非球样块（21×31 的「人」，中心 x≈470）时可被选中——纯最近邻
        # 此后每步到它距离恒 0，链就永久跟人（旧规则链首停在 x≈470）。新
        # 规则给非球样块加罚 ⇒ 球一回到最近邻门内（x≈445，距块 35px < 罚
        # 140px×360/720=70）就抢回链，链首回到片窗起点附近的球位（x≈550）。
        chain = self._follow(self._make_hijack(tmp_path / "f4.mp4"))
        assert abs(chain[-1][0] - 8.9) < 0.05          # 锚照旧
        assert chain[0][0] < 5.1                       # 断口仍桥接（0.33s）
        assert chain[0][1] > 500.0                     # 回到球，不是被劫去 470

    def test_窗口太短_返回空链(self):
        # rel−start 不足一步 → 空链（调用方回退静 hold）
        assert self._ball_follow_x(None, 8.8, 8.9, (300.0, 280.0),
                                   self._calib(), 640, 360, 30.0) == []

    def test_无出手锚_返回空链(self):
        assert self._ball_follow_x(None, 5.0, 8.9, None,
                                   self._calib(), 640, 360, 30.0) == []


class TestSlowmoWindow:
    def test_常规窗_锚在出手时刻(self):
        # 慢放锚=出手时刻（用户 2026-09-16：进球慢放改出手慢放）：
        # 出手前 0.35s（起跳收球）→ 后 0.65s（球飞出），进球本身回常速
        assert slowmo_window(_cam()) == (8.55, 9.55)

    def test_出手时刻缺失_按默认提前量估计(self):
        assert slowmo_window(_cam(release_t=None)) == (8.55, 9.55)

    def test_被片窗截短(self):
        assert slowmo_window(_cam(start=8.7)) == (8.7, 9.55)
        assert slowmo_window(_cam(end=9.2)) == (8.55, 9.2)

    def test_不足下限跳过(self):
        assert slowmo_window(_cam(start=9.5)) is None

    def test_默认慢放速度_用户提速到0_6(self):
        # 用户 2026-09-17「还是有点慢，改成 0.6」：0.3 → 0.4 → 0.6
        # （1.0s 源窗 3.33s → 1.67s 输出，进球后更快回常速）
        assert CFG.slowmo_speed == 0.6


class TestHookWindow:
    """片头钩子源窗（用户 2026-09-17）：把最后一个球的进球砍头复制到片头
    ——前 3 秒看到进球（抖音看重 3s/5s 完播）。提前量夹在 [hook_min,
    hook_max]：出手→进球过短的球往前补到够看，远投飞行久也不让进球拖到
    3 秒后；窗不越出用户选的片段窗（before/after）与片长。"""

    def test_常规_提前量出手前0_6(self):
        # 出手 8.5（飞行 1.5s）→ 提前量 1.5+0.6=2.1；再被用户片段窗
        # （before 2.0，起点不早于 8.0）兜住
        assert hook_window(10.0, 2.0, 2.0, 8.5, 30.0) == (8.0, 10.3)

    def test_出手到进球过短_提前量补到下限(self):
        # 补篮：出手 9.85（飞行 0.15s）→ 0.15+0.6=0.75 低于下限 1.2 → 1.2
        # （保证钩子够看：投篮动作 + 进球）
        assert hook_window(10.0, 3.0, 2.0, 9.85, 30.0) == (8.8, 10.3)

    def test_远投飞行久_进球仍落在前2_4秒(self):
        # 飞行 5.0s（回推/远投）→ 5.6 超上限 2.4 → 2.4：进球必须在前
        # 2.4 秒内出现，钩子不许拖成第二个正片
        assert hook_window(10.0, 8.0, 2.0, 5.0, 30.0) == (7.6, 10.3)

    def test_不越出用户片段窗与片长(self):
        # before 0.8 比提前量紧 → 用户片段窗说了算
        assert hook_window(10.0, 0.8, 0.1, 8.5, 10.6) == (9.2, 10.1)
        assert hook_window(10.0, 0.8, 2.0, 8.5, 10.6) == (9.2, 10.3)

    def test_出手时刻缺失_按默认提前量估计(self):
        # 无 release_t → 按 t−default_lead(1.1) 当出手 → 提前量 1.7
        assert hook_window(10.0, 3.0, 2.0, None, 30.0) == (8.3, 10.3)

    def test_窗过短_不出钩子(self):
        # 用户窗只给了 0.05s（before 0.05/after 0）→ 不值当，不出钩子
        assert hook_window(10.0, 0.05, 0.0, 9.9, 30.0) is None


import json as _json

from bbh.calibration import load_segments_dir
from bbh.models import Calibration


def _write_calib(path, calib_t=0.0):
    Calibration(hoop_rect=(300, 140, 80, 40), hsv_low=(0, 80, 120),
                hsv_high=(25, 255, 255), frame_size=(640, 360),
                calib_t=calib_t).save(str(path))


class TestLoadSegmentsDir:
    def test_只有主标定_单段t0为零(self, tmp_path):
        _write_calib(tmp_path / "calibration.json")
        segs = load_segments_dir(str(tmp_path))
        assert [(s.t0) for s in segs] == [0.0]

    def test_多段按t0升序_损坏段跳过(self, tmp_path):
        _write_calib(tmp_path / "calibration.json")
        _write_calib(tmp_path / "calibration_seg2.json", calib_t=1550.0)
        _write_calib(tmp_path / "calibration_seg1.json", calib_t=700.0)
        (tmp_path / "calibration_seg3.json").write_text("不是json")
        assert [s.t0 for s in load_segments_dir(str(tmp_path))] == [0.0, 700.0, 1550.0]

    def test_主标定缺失或损坏_空列表(self, tmp_path):
        assert load_segments_dir(str(tmp_path)) == []
        (tmp_path / "calibration.json").write_text("}")
        assert load_segments_dir(str(tmp_path)) == []


import subprocess

import cv2

from bbh.editor import _ffmpeg_exe
from bbh.vrender import render_clip


@pytest.fixture(scope="session")
def sample_video_snd(sample_video, tmp_path_factory):
    """带音轨源：生产源必有现场原声，渲染链路的 [1:a] 需要它。"""
    out = str(tmp_path_factory.mktemp("snd") / "sample_snd.mp4")
    subprocess.run([_ffmpeg_exe(), "-y", "-i", str(sample_video),
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-c:v", "copy", "-c:a", "aac", "-shortest", out],
                   check=True, capture_output=True)
    return out


def _meta(path):
    cap = cv2.VideoCapture(path)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()
    return w, h, fps, n


@pytest.fixture(scope="session")
def sample_video_tone(sample_video, tmp_path_factory):
    """带正弦音轨源：慢放段音频验收用——anullsrc 静音源测不出「有没有声」。"""
    out = str(tmp_path_factory.mktemp("tone") / "sample_tone.mp4")
    subprocess.run([_ffmpeg_exe(), "-y", "-i", str(sample_video),
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=60",
                    "-c:v", "copy", "-c:a", "aac", "-shortest", out],
                   check=True, capture_output=True)
    return out


class TestSlowmoAudio:
    def test_慢放段保音高原声_非静音(self, sample_video_tone, tmp_path):
        # 用户 2026-09-16 问题②：慢放段静音效果差 → atempo 拉慢原声。
        # 源恒定正弦：慢放输出窗（0.4x 后 0.05~2.55s）若仍是 anullsrc，
        # 中段 RMS≈0——真实渲染+解码音轨钉死。保音高本身由 atempo 保证
        g = _cam(t=3.0, start=1.5, end=4.5, release_t=1.9)
        out = str(tmp_path / "tone.mp4")
        render_clip(sample_video_tone, g, plan_camera(g), slowmo_window(g), out)
        raw = subprocess.run(
            [_ffmpeg_exe(), "-i", out, "-map", "0:a", "-f", "s16le",
             "-ac", "1", "-ar", "8000", "pipe:1"],
            capture_output=True, check=True).stdout
        x = np.frombuffer(raw, np.int16).astype(np.float32)
        # 音画齐长：视频 3.67s（0.6x 提速后：0.05+1.67+1.95）。曾实测生产
        # 管道（rawvideo 喂帧+真实源 [1:a]）中 -t total 会把 atempo 延迟
        # 输出的音频提前截断（音频只剩 flat 段和 2.0s）——长度必须钉到
        # 视频全长附近（两头都钉：旧实现偏短，concat PTS 空洞案偏长）
        assert 27000 < len(x) < 32000               # 3.667s×8000 ± 5%
        # 采样窗 [1.5,2.8]s 横跨慢放尾段与之后常速段——旧 anullsrc 实现
        # 此段必静音（真见红），atempo 实现两段都是原声
        seg = x[12000:22400]                        # [1.5,2.8]s
        assert float(np.abs(seg).mean()) > 100.0    # 有声（静音则≈0）


class TestTimelineParts:
    """视频链与音频链分家（2026-09-16）：单进程混跑已证死路——-t 按
    muxer 时钟早停会丢 atempo 缓冲里的慢放音频、concat 给 atempo 段留
    PTS 空洞把 duration 元数据撑到 14.8s（实长 10.3s）。改三步：管道出
    无声视频 → 源文件独立跑音频链出 PCM wav（按样本密铺，无 PTS 语义）
    → copy+编码 mux（-shortest 对齐）。"""

    def _parts(self, *a):
        from bbh.vrender import _timeline_parts      # 延迟 import 防连坐
        return _timeline_parts(*a)

    def test_慢放提速_默认0_6(self):
        # 用户 2026-09-17：慢放略久 → 提速到 0.6x（窗 1.0s 源 → 1.67s 输出）
        vc, ac, total = self._parts(30, 48, 90, 30.0, 0.6, 0.0, 1.5, 4.5)
        assert "minterpolate=fps=50" in vc and "setpts=1.666667*PTS" in vc
        assert "atempo=0.6" in ac            # ≥0.5 单级即可（无两级链）
        assert "atempo=0.5,atempo=" not in ac
        # pre 1.0 + sm 0.6/0.6 + post 1.4 = 3.4
        assert abs(total - 3.4) < 0.05

    def test_三段齐全(self):
        vc, ac, total = self._parts(30, 48, 90, 30.0, 0.3, 0.0, 1.5, 4.5)
        assert "minterpolate=fps=100" in vc and "setpts=3.333333*PTS" in vc
        assert vc.count("trim=start_frame=") == 3
        assert "concat=n=3:v=1:a=0[vout]" in vc
        # 慢放段音频=同窗原声 atempo 保音高拉慢（用户 2026-09-16 弃静音）
        assert "anullsrc" not in ac
        assert "atempo=0.5" in ac and "atempo=0.6" in ac   # 0.5×0.6=0.3
        assert "asplit=3" in ac                            # 三段音频共用 [0:a]
        assert "afade=t=out" in ac and "afade=t=in" in ac
        # pre 1.0s + sm 0.6/0.3 + post 1.4s ≈ 4.4
        assert abs(total - 4.4) < 0.05

    def test_慢动作窗贴片段头_无pre段(self):
        vc, ac, _ = self._parts(0, 18, 90, 30.0, 0.3, 0.0, 1.5, 4.5)
        assert vc.count("trim=start_frame=") == 2
        assert "concat=n=2:v=1:a=0[vout]" in vc
        assert "asplit=2" in ac and "anullsrc" not in ac

    def test_慢动作窗贴片段尾_无post段(self):
        vc, ac, _ = self._parts(12, 90, 90, 30.0, 0.3, 0.0, 1.5, 4.5)
        assert vc.count("trim=start_frame=") == 2
        assert "concat=n=2" in vc and "apad" not in ac
        # 单 flat 段（pre）的 atrim 必须接在 [0:a] 预处理链尾，不能成孤儿链
        assert "[a0]atrim" not in ac
        assert "atempo=0.5" in ac

    def test_整段皆慢动作_零flat段(self):
        _, ac, _ = self._parts(0, 90, 90, 30.0, 0.3, 0.0, 1.5, 4.5)
        assert "[0:a]" in ac          # 整条原声拉慢（旧静音案废除）
        assert "anullsrc" not in ac and "atempo" in ac and "[aout]" in ac

    def test_无慢动作_单段直通(self):
        vc, ac, total = self._parts(0, 0, 90, 30.0, 0.3, 0.0, 1.5, 4.5)
        assert "minterpolate" not in vc and "concat" not in vc
        assert "[vout]" in vc and "[aout]" in ac
        assert "atempo" not in ac
        assert abs(total - 3.0) < 0.05

    def test_冻结补静音(self):
        vc, ac, total = self._parts(0, 0, 90, 30.0, 0.3, 0.5, 1.5, 4.5)
        assert "apad=pad_dur=0.500" in ac
        assert abs(total - 3.5) < 0.05


class TestRenderClip:
    def test_规格与慢动作时长膨胀(self, sample_video_snd, tmp_path):
        from bbh.vrender import plan_camera, slowmo_window
        g = _cam(t=3.0, start=1.5, end=4.5, release_t=1.9)
        out = str(tmp_path / "seg.mp4")
        render_clip(sample_video_snd, g, plan_camera(g), slowmo_window(g), out)
        w, h, fps, n = _meta(out)
        assert (w, h) == (660, 1280) and 29 <= fps <= 31
        # 慢放窗锚出手 (1.55,2.55)：pre 0.05 + sm 1.0/0.6 + post 1.95
        # = 3.67s → 110 帧 ±15（fps 滤镜容差；2026-09-17 提速 0.3→0.4→0.6）
        assert 95 <= n <= 125

    def test_无慢动作_直通时长(self, sample_video_snd, tmp_path):
        g = _cam(t=3.0, start=1.5, end=4.5, release_t=1.9)
        out = str(tmp_path / "seg.mp4")
        render_clip(sample_video_snd, g, plan_camera(g), None, out)
        _, _, _, n = _meta(out)
        assert 75 <= n <= 105                 # ≈3.0s×30

    def test_末球冻结淡出到黑(self, sample_video_snd, tmp_path):
        g = _cam(t=3.0, start=1.5, end=4.5, release_t=1.9)
        out = str(tmp_path / "seg.mp4")
        render_clip(sample_video_snd, g, plan_camera(g), None, out, freeze_s=0.5)
        _, _, _, n = _meta(out)
        assert n >= 105                        # ≥3.5s×30
        cap = cv2.VideoCapture(out)
        for _ in range(n - 1):
            cap.read()                         # 读到最后一帧
        ok, last = cap.read()
        cap.release()
        assert ok and float(last.mean()) < 25  # 已淡到近黑


import os

import numpy as np

from bbh.models import GoalEvent, save_events, time_key
from bbh.vrender import build_vertical, measure_hoop_x


def _work(tmp_path, sample_video, spots):
    """tmp work 目录：events.json + calibration.json + shooters.json。
    spots: {t: (release_xy, release_t, spot)}"""
    wd = str(tmp_path / "work")
    os.makedirs(wd)
    save_events(os.path.join(wd, "events.json"), str(sample_video), 6.0,
                [GoalEvent(t=t, confidence=0.9, status="high")
                 for t in spots], None)
    Calibration(hoop_rect=(300, 140, 80, 40), hsv_low=(0, 80, 120),
                hsv_high=(25, 255, 255), frame_size=(640, 360)).save(
                    os.path.join(wd, "calibration.json"))
    traces = {}
    for t, (xy, rt, spot) in spots.items():
        traces[time_key(t)] = {"status": "high", "release_xy": xy,
                               "release_t": rt, "spot": spot}
    with open(os.path.join(wd, "shooters.json"), "w", encoding="utf-8") as f:
        _json.dump({"traces": traces}, f)
    return wd


def _render_log(monkeypatch):
    """包 render_clip 记录渲染调用（文件名/片窗/慢放/冻结），真渲染照跑。"""
    import bbh.vrender as vr
    calls = []
    real = vr.render_clip

    def fake(vp, g, centers, slowmo, out, cfg=None, freeze_s=0.0):
        calls.append((os.path.basename(out), round(g.start, 2), round(g.end, 2),
                      slowmo, freeze_s))
        return real(vp, g, centers, slowmo, out, cfg, freeze_s)
    monkeypatch.setattr(vr, "render_clip", fake)
    return calls


def _by_name(calls, prefix):
    return [c for c in calls if c[0].startswith(prefix)]


def _seq_log(monkeypatch):
    """包 concat_clips 记录最终拼接序（文件名），真拼接照跑。"""
    import bbh.vrender as vr
    seqs = []
    real = vr.concat_clips

    def fake(seq, out):
        seqs.append([os.path.basename(p) for p in seq])
        return real(seq, out)
    monkeypatch.setattr(vr, "concat_clips", fake)
    return seqs


class TestMeasureHoopX:
    """逐球筐位实测（实机问题1：标定段内镜头漂移 ±190px → 锁筐偏心）。
    合成帧用 09-11_3 真实标定参数（1920×1080，筐口 [651,368,65,26]，
    HSV (0,116,62)-(12,215,148)）。筐块形态按真源三球 probe 窗 dump
    （2026-09-16）：远景筐弧 HSV 块是**近方块**（ar 1.0-1.3、w19-26，
    遮挡重时只剩 w5-6 碎片）；飞行球在筐上方（dy −93~−112）。旧判据
    aspect≥1.6 宽薄门把真筐全杀（球1/2 实测 None 回退偏 ~180px 的标定
    值），运动模糊球条（ar 1.65+）反而过门（球3 实测错 +195px）——筐/球
    的区分信号是 y 位置，不是宽薄比。"""
    REF = (651, 368, 65, 26)          # 标定筐口（=筐口中心 683.5,381）
    HSV_LO, HSV_HI = (0, 116, 62), (12, 215, 148)
    RIM_BGR = (60, 88, 140)           # 暗橙（真实标定界 V≤148/S≤215，
                                      # 亮橙不进界；前置用例自检）

    def _frame(self, rim_x=None, rim_dy=0, ball_xy=None):
        """筐弧=24×20 近方块（中心 y=标定筐心+rim_dy）；球=正圆 r=18。"""
        f = np.full((1080, 1920, 3), 50, np.uint8)   # 灰底（低值低饱和不命中）
        if rim_x is not None:
            y = 381 + rim_dy
            cv2.rectangle(f, (rim_x - 12, y - 10), (rim_x + 12, y + 10),
                          self.RIM_BGR, -1)
        if ball_xy is not None:
            cv2.circle(f, ball_xy, 18, self.RIM_BGR, -1)
        return f

    def test_前置_橙色确实落在HSV界内(self):
        h, s, v = cv2.cvtColor(np.uint8([[self.RIM_BGR[::-1]]]),
                               cv2.COLOR_RGB2HSV)[0, 0]
        lo, hi = self.HSV_LO, self.HSV_HI
        assert lo[0] <= h <= hi[0] and lo[1] <= s <= hi[1] and lo[2] <= v <= hi[2]

    def test_实测漂移后的筐位(self):
        # 段内漂移：标定 683.5，实测帧里筐弧（近方块）在 850——真源球1 复现
        fs = [self._frame(rim_x=850) for _ in range(3)]
        x = measure_hoop_x(fs, self.REF, self.HSV_LO, self.HSV_HI)
        assert x is not None and abs(x - 850.0) <= 2.0

    def test_筐弧竖向漂移_门内照收(self):
        # 真源段1 实测筐弧比标定筐心高 ~60px（竖向漂移）——y 容忍带内照收
        fs = [self._frame(rim_x=850, rim_dy=-60) for _ in range(3)]
        x = measure_hoop_x(fs, self.REF, self.HSV_LO, self.HSV_HI)
        assert x is not None and abs(x - 850.0) <= 2.0

    def test_筐弧竖向漂移超门_None回退(self):
        # 高过容忍带（>80px）不算筐——宁回退标定不测错对象
        fs = [self._frame(rim_x=850, rim_dy=-100) for _ in range(3)]
        assert measure_hoop_x(fs, self.REF, self.HSV_LO, self.HSV_HI) is None

    def test_飞行球在筐上方_不抢筐(self):
        # 球比筐弧更靠近标定筐心（x 距离 56 vs 166）——筐/球靠 y 门分
        # （球在筐上方 dy−91），不靠宽薄比（正圆 ar 1.0，运动模糊时球条
        # ar 可 ≥1.6，宽薄门两头都靠不住）
        fs = [self._frame(rim_x=850, ball_xy=(740, 290)) for _ in range(3)]
        x = measure_hoop_x(fs, self.REF, self.HSV_LO, self.HSV_HI)
        assert x is not None and abs(x - 850.0) <= 2.0

    def test_运动模糊球条_不算筐(self):
        # 真源球3：筐只剩碎片、高速球被拉成 28×16 宽条（ar 1.75 过旧
        # aspect 门，实测 1059.8 错 +195px）——筐上方的球条必须 y 门拒掉
        f = np.full((1080, 1920, 3), 50, np.uint8)
        cv2.rectangle(f, (726, 273), (754, 289), self.RIM_BGR, -1)  # 球条
        assert measure_hoop_x([f] * 3, self.REF, self.HSV_LO,
                              self.HSV_HI) is None

    def test_筐弧碎片_小过旧下限也参选(self):
        # 真源球3：筐弧被遮只剩 w6-7 碎片（旧 rw*0.35=22.75 下限全杀）；
        # 邻域内还有更大的静态干扰块——距标定筐心近的碎片该赢
        f = np.full((1080, 1920, 3), 50, np.uint8)
        cv2.rectangle(f, (866, 319), (873, 325), self.RIM_BGR, -1)  # w7 筐弧碎片
        cv2.rectangle(f, (890, 305), (908, 315), self.RIM_BGR, -1)  # w18 干扰块
        x = measure_hoop_x([f] * 3, self.REF, self.HSV_LO, self.HSV_HI)
        assert x is not None and abs(x - 869.5) <= 2.0

    def test_无筐_返回None供回退(self):
        fs = [self._frame() for _ in range(3)]
        assert measure_hoop_x(fs, self.REF, self.HSV_LO, self.HSV_HI) is None

    def test_筐移出邻域_不算(self):
        # ROI 纪律：筐在 1300（邻域 683±260 之外）→ 不认，宁可回退标定
        fs = [self._frame(rim_x=1300) for _ in range(3)]
        assert measure_hoop_x(fs, self.REF, self.HSV_LO, self.HSV_HI) is None

    def test_多帧抖动取中位(self):
        fs = [self._frame(rim_x=x) for x in (846, 852, 848)]
        x = measure_hoop_x(fs, self.REF, self.HSV_LO, self.HSV_HI)
        assert x is not None and abs(x - 848.0) <= 2.0

    def test_命中不足下限_None(self):
        # 5 帧只有 1 帧有筐 → 低于 min_hits=2 → None
        fs = [self._frame(rim_x=850)] + [self._frame() for _ in range(4)]
        assert measure_hoop_x(fs, self.REF, self.HSV_LO, self.HSV_HI,
                                   min_hits=2) is None


class TestBuildVertical:
    def test_两球_白闪_规格(self, sample_video_snd, tmp_path):
        wd = _work(tmp_path, sample_video_snd,
                   {3.0: ([280, 200], 1.9, "ok"), 5.0: ([200, 210], 3.9, "bad")})
        out = build_vertical(str(sample_video_snd),
                             [(3.0, 1.5, 1.5), (5.0, 1.0, 0.8)], wd)
        assert os.path.exists(out)
        w, h, fps, n = _meta(out)
        assert (w, h) == (660, 1280) and 29 <= fps <= 31
        # 片头钩子 1.3s（末球 5.0：用户窗 before 1.0 收紧提前量）+ 球1≈3.67s
        # （pre 0.05+sm 1.0/0.6+post 1.95）+ 球2≈2.67s（sm 0.55/0.6+post
        # 1.25+冻结0.5）+ 白闪 2×0.067 → ≈7.8s（0.6x 提速，旧 0.4x≈9.1s）
        assert 215 <= n <= 260
        cap = cv2.VideoCapture(out)
        white = 0
        while True:
            ok, fr = cap.read()
            if not ok:
                break
            if float(fr.mean()) > 250:
                white += 1
        cap.release()
        assert 2 <= white <= 6                   # 两道接缝各 2 帧（±重采样）

    def test_单球_无白闪(self, sample_video_snd, tmp_path):
        wd = _work(tmp_path, sample_video_snd, {3.0: ([280, 200], 1.9, "ok")})
        out = build_vertical(str(sample_video_snd), [(3.0, 1.5, 1.5)], wd)
        cap = cv2.VideoCapture(out)
        white = sum(1 for ok, fr in iter(cap.read, (False, None))
                    if ok and float(fr.mean()) > 250)
        cap.release()
        assert white == 0

    def test_坏球跳过_好球照出(self, sample_video_snd, tmp_path, capsys):
        wd = _work(tmp_path, sample_video_snd, {3.0: ([280, 200], 1.9, "ok")})
        out = build_vertical(str(sample_video_snd),
                             [(3.0, 1.5, 1.5), (100.0, 5.0, 2.0)], wd)
        assert os.path.exists(out)               # t=100 超片长，跳过不炸
        _, _, _, n = _meta(out)
        assert n < 200                           # 只有球 1
        assert "跳过" in capsys.readouterr().out

    def test_清旧段遇占用_跳过该文件照常渲染(self, sample_video_snd, tmp_path,
                                            monkeypatch, capsys):
        # 实机：vclips 里旧段被外部进程锁（播放器/资源管理器预览，
        # WinError 32）不该炸整次渲染——旧段不进本次 files，留着无害
        wd = _work(tmp_path, sample_video_snd, {3.0: ([280, 200], 1.9, "ok")})
        clips = os.path.join(wd, "vclips")
        os.makedirs(clips, exist_ok=True)
        locked = os.path.join(clips, "043_002641.mp4")
        open(locked, "w").close()
        real = os.remove
        monkeypatch.setattr(
            "bbh.vrender.os.remove",
            lambda p: (_ for _ in ()).throw(PermissionError(32, "占用"))
            if p == locked else real(p))
        out = build_vertical(str(sample_video_snd), [(3.0, 1.5, 1.5)], wd)
        assert os.path.exists(out)
        assert os.path.exists(locked)            # 被锁的留着
        assert "占用" in capsys.readouterr().out

    def test_空goals报错(self, sample_video_snd, tmp_path):
        wd = _work(tmp_path, sample_video_snd, {})
        with pytest.raises(ValueError):
            build_vertical(str(sample_video_snd), [], wd)

    def test_开头钩子_末球进球前置_砍头常速(self, sample_video_snd, tmp_path,
                                        monkeypatch):
        """用户 2026-09-17：把最后一个球的进球砍头复制到片头——前 3 秒就
        看到进球（抖音看重 3s/5s 完播）。钩子=按时间序最后一个球的一小段，
        常速不慢放不冻结（钩子要脆），拼在整条最前、白闪隔开正片。"""
        wd = _work(tmp_path, sample_video_snd,
                   {3.0: ([280, 200], 1.9, "ok"), 5.0: ([200, 210], 3.9, "ok")})
        calls = _render_log(monkeypatch)
        seqs = _seq_log(monkeypatch)
        build_vertical(str(sample_video_snd),
                       [(3.0, 1.5, 1.5), (5.0, 1.0, 0.8)], wd)
        hook = _by_name(calls, "hook_")
        assert len(hook) == 1
        assert hook[0][1:3] == (4.0, 5.3)   # 末球 t=5.0、用户窗 before 1.0
        assert hook[0][3] is None and hook[0][4] == 0.0   # 无慢放、无冻结
        # 拼接序：钩子 → 白闪 → 正片（本球慢放段照旧）
        assert seqs[0][:3] == [hook[0][0], "flash.mp4", "000_000003.mp4"]

    def test_开头钩子_关闭_不出钩子(self, sample_video_snd, tmp_path,
                                  monkeypatch):
        wd = _work(tmp_path, sample_video_snd,
                   {3.0: ([280, 200], 1.9, "ok"), 5.0: ([200, 210], 3.9, "ok")})
        calls = _render_log(monkeypatch)
        build_vertical(str(sample_video_snd),
                       [(3.0, 1.5, 1.5), (5.0, 1.0, 0.8)], wd,
                       cfg=RenderConfig(hook=False))
        assert _by_name(calls, "hook_") == []
        assert [c[0] for c in calls] == ["000_000003.mp4", "001_000005.mp4"]

    def test_开头钩子_单球不出钩子(self, sample_video_snd, tmp_path,
                                monkeypatch):
        # 单球片做钩子=同一个球连看两遍（像 bug）；单球无白闪契约不变
        wd = _work(tmp_path, sample_video_snd, {3.0: ([280, 200], 1.9, "ok")})
        calls = _render_log(monkeypatch)
        build_vertical(str(sample_video_snd), [(3.0, 1.5, 1.5)], wd)
        assert _by_name(calls, "hook_") == []

    def test_开头钩子_按时间序末球_不随传入顺序(self, sample_video_snd,
                                          tmp_path, monkeypatch):
        # 爆点前置（app 侧按档位重排）会打乱传入顺序：钩子仍取**时间序**
        # 最后一个球的进球（用户口径），不是列表末元素
        wd = _work(tmp_path, sample_video_snd,
                   {3.0: ([280, 200], 1.9, "ok"), 5.0: ([200, 210], 3.9, "ok")})
        calls = _render_log(monkeypatch)
        build_vertical(str(sample_video_snd),
                       [(5.0, 1.0, 0.8), (3.0, 1.5, 1.5)], wd)
        hook = _by_name(calls, "hook_")
        assert len(hook) == 1 and hook[0][1:3] == (4.0, 5.3)   # 仍是 t=5.0

    def test_开头钩子_只认渲染成功的球_坏球不算(self, sample_video_snd,
                                          tmp_path, monkeypatch):
        # 时间序末球渲染失败（t 超片长）→ 只剩一球成功 → 不出钩子：钩子源
        # 必须真出现在正片里，否则钩子与正片对不上
        wd = _work(tmp_path, sample_video_snd, {3.0: ([280, 200], 1.9, "ok")})
        calls = _render_log(monkeypatch)
        build_vertical(str(sample_video_snd),
                       [(3.0, 1.5, 1.5), (100.0, 5.0, 2.0)], wd)
        assert _by_name(calls, "hook_") == []

    def test_逐球实测筐位接线(self, sample_video_snd, tmp_path, monkeypatch):
        # 实机问题1接线：渲染前逐球实测筐位，实测值覆盖标定值（spy 钩在
        # plan_camera 上验 GoalCam.hoop_x）；实测 None → 回退标定筐心
        import bbh.vrender as vr
        wd = _work(tmp_path, sample_video_snd, {3.0: ([280, 200], 1.9, "ok")})
        seen = []
        real = vr.plan_camera
        monkeypatch.setattr(vr, "_read_hoop_x",
                            lambda cap, t, calib, fw, fh, fps, cfg=None: 821.0)
        monkeypatch.setattr(vr, "plan_camera",
                            lambda g, cfg=None, follow=None, lock_goal=False:
                                seen.append(g.hoop_x) or real(g, cfg, follow))
        build_vertical(str(sample_video_snd), [(3.0, 1.5, 1.5)], wd)
        assert seen == [821.0]
        monkeypatch.setattr(vr, "_read_hoop_x",
                            lambda cap, t, calib, fw, fh, fps, cfg=None: None)
        build_vertical(str(sample_video_snd), [(3.0, 1.5, 1.5)], wd)
        assert seen == [821.0, 340.0]              # 300+80/2 标定筐心

    def test_跟球链接线_有出手人的球链进相机(self, sample_video_snd, tmp_path,
                                        monkeypatch):
        # 实机问题5接线：有出手人（release_xy 可用）→ ball_follow_x 扫出的
        # 链透传进 plan_camera（漏传=整段静 hold，跟球静默失效）；
        # spot=bad 无出手人 → 不扫、follow=None
        import bbh.vrender as vr
        wd = _work(tmp_path, sample_video_snd,
                   {3.0: ([280, 200], 1.9, "ok")})
        follows, calls = [], {"n": 0}
        chain = [(1.5, 500.0), (1.9, 280.0)]
        monkeypatch.setattr(vr, "ball_follow_x",
                            lambda *a, **k: calls.update(n=calls["n"] + 1)
                            or chain)
        monkeypatch.setattr(vr, "_read_hoop_x",
                            lambda cap, t, calib, fw, fh, fps, cfg=None: None)
        real = vr.plan_camera
        monkeypatch.setattr(vr, "plan_camera",
                            lambda g, cfg=None, follow=None, lock_goal=False:
                                follows.append(follow) or real(g, cfg, follow))
        build_vertical(str(sample_video_snd), [(3.0, 1.5, 1.5)], wd)
        assert follows == [chain] and calls["n"] == 1
        # spot=bad → 无出手人：改用进球锚链（锚参数见专项用例），链同样
        # 进相机——不再是 follow=None 锁筐
        follows.clear()
        wd2 = _work(tmp_path / "bad", sample_video_snd,
                    {3.0: ([280, 200], 1.9, "bad")})
        build_vertical(str(sample_video_snd), [(3.0, 1.5, 1.5)], wd2)
        assert follows == [chain] and calls["n"] == 2

    def test_进球锚链接线_无出手人用筐位锚扫到进球时刻(self, sample_video_snd,
                                                    tmp_path, monkeypatch):
        # 用户 2026-09-16：出手前运球人保持画面横向中间。无出手锚的球
        # （从未跑过出手人识别，或 spot=bad）此前整段锁筐、运球人走出
        # 画面。接线契约：改用进球锚——锚=（进球时刻 t，筐口
        # 中心），实测筐位可用则用实测值，否则标定筐心（与 GoalCam.hoop_x
        # 同源——链尾锁的筐=渲染锁的筐，不跳变）
        import bbh.vrender as vr
        calls = []
        monkeypatch.setattr(vr, "ball_follow_x",
                            lambda cap, start, rel, xy, *a, **k:
                            calls.append((start, rel, tuple(xy))) or [])
        monkeypatch.setattr(vr, "_read_hoop_x",
                            lambda *a, **k: 821.0)
        wd = _work(tmp_path / "m", sample_video_snd,
                   {3.0: ([280, 200], 1.9, "bad")})
        build_vertical(str(sample_video_snd), [(3.0, 1.5, 1.5)], wd)
        # 实测筐位 821 覆盖标定；锚 y=筐口中心 140+40/2=160（缩放 1:1）
        assert calls == [(1.5, 3.0, (821.0, 160.0))]
        # 实测 None → 回退标定筐心 340
        calls.clear()
        monkeypatch.setattr(vr, "_read_hoop_x", lambda *a, **k: None)
        wd2 = _work(tmp_path / "c", sample_video_snd,
                    {3.0: ([280, 200], 1.9, "bad")})
        build_vertical(str(sample_video_snd), [(3.0, 1.5, 1.5)], wd2)
        assert calls == [(1.5, 3.0, (340.0, 160.0))]

    def test_畸形出手点跳过该球_好球照出(self, sample_video_snd, tmp_path,
                                    capsys):
        # release_xy 非数值 → _goal_cam 抛异常必须只跳过该球（spec：集锦
        # 不因一球全废），不能炸整条
        wd = _work(tmp_path, sample_video_snd,
                   {3.0: ([280, 200], 1.9, "ok"),
                    5.0: (["坏", "数据"], 3.9, "ok")})
        out = build_vertical(str(sample_video_snd),
                             [(3.0, 1.5, 1.5), (5.0, 1.0, 0.8)], wd)
        assert os.path.exists(out)
        assert "跳过" in capsys.readouterr().out


class TestAppRouting:
    def test_默认竖屏_走竖屏管线(self, monkeypatch, tmp_path):
        import bbh.app as app
        import bbh.vrender as vrender
        monkeypatch.chdir(tmp_path)              # 路由要探 work/<视频名>/
        got = {}
        monkeypatch.setattr(vrender, "build_vertical",
                            lambda vp, goals, wd, name: got.update(
                                goals=goals, name=name) or str(tmp_path / "v.mp4"))
        rows = [[True, "0:03.0", "high", 0.9, 1.5, 1.5, ""]]
        out = app.generate_highlight("vid.mp4", rows)   # vertical 默认 True
        assert got["goals"] == [(3.0, 1.5, 1.5)] and got["name"] == "highlight-竖.mp4"
        assert out.endswith("v.mp4")

    def test_竖屏_档位high前置_同级保时间序(self, monkeypatch, tmp_path):
        """用户 2026-09-17（A 爆点前置）：高置信球排前面，同级保持时间序
        （stable sort 不抖）；横屏旧路径顺序原样不动。"""
        import bbh.app as app
        import bbh.vrender as vrender
        monkeypatch.chdir(tmp_path)              # 路由要探 work/<视频名>/
        got = {}
        monkeypatch.setattr(vrender, "build_vertical",
                            lambda vp, goals, wd, name: got.update(goals=goals)
                            or str(tmp_path / "v.mp4"))
        rows = [[True, "0:03.0", "low", 0.9, 1.5, 1.5, ""],
                [True, "0:09.0", "high", 0.9, 1.5, 1.5, ""],
                [True, "0:15.0", "high", 0.9, 1.5, 1.5, ""]]
        app.generate_highlight("vid.mp4", rows)
        assert got["goals"] == [(9.0, 1.5, 1.5), (15.0, 1.5, 1.5),
                                (3.0, 1.5, 1.5)]
        seen = {}
        monkeypatch.setattr(app, "load_events",       # 横屏段要读 events.json
                            lambda p: {"duration": 60.0, "events": []})
        monkeypatch.setattr(app.editor, "build_highlight",
                            lambda src, clips, wd, name="highlight.mp4":
                            seen.update(clips=clips) or str(tmp_path / "h.mp4"))
        app.generate_highlight("vid.mp4", rows, vertical=False)
        assert seen["clips"] == [(1.5, 4.5), (7.5, 10.5), (13.5, 16.5)]

    def test_横屏参数_回旧横屏路径(self, monkeypatch, tmp_path):
        import bbh.app as app
        monkeypatch.chdir(tmp_path)              # 路由要探 work/<视频名>/
        seen = {}
        monkeypatch.setattr(app, "load_events",   # app 命名空间里的引用（app.py:27）
                            lambda p: {"duration": 60.0, "events": []})
        monkeypatch.setattr(app.editor, "build_highlight",
                            lambda src, clips, wd, name="highlight.mp4":
                            seen.update(clips=clips) or str(tmp_path / "h.mp4"))
        rows = [[True, "0:03.0", "high", 0.9, 1.5, 1.5, ""]]
        out = app.generate_highlight("vid.mp4", rows, vertical=False)
        assert seen["clips"] == [(1.5, 4.5)]
        assert out.endswith("h.mp4")

    def test_open_gen_dialog_回画幅旗标(self, monkeypatch, tmp_path):
        import bbh.app as app
        monkeypatch.setattr(app, "TAGS_FILE", str(tmp_path / "tags.json"))
        rows = [[True, "0:03.0", "high", 0.9, 1.5, 1.5, ""]]
        *_, stats_v, vert_v = app.open_gen_dialog("vid.mp4", rows, True)
        *_, stats_h, vert_h = app.open_gen_dialog("vid.mp4", rows, False)
        assert vert_v is True and vert_h is False
        assert "竖屏" in stats_v and "横屏" in stats_h   # 弹窗标明模式

    def test_gen_selected_画幅旗标透传(self, monkeypatch, tmp_path):
        import bbh.app as app
        modes = []

        def fake_gen(vp, rows, tag="", vertical=True, starred_only=False):
            # 形参必须跟真 `generate_highlight` 对齐：gen_selected 会显式传
            # starred_only（2026-10-24 集锦也支持精选），少一个就 TypeError，
            # 而 gen_selected 只 `except gr.Error` ⇒ 生成器被杀死、断言全挂
            assert starred_only is False          # 合集项不该是精选筛选
            modes.append(vertical)
            return str(tmp_path / f"h-{len(modes)}.mp4")
        monkeypatch.setattr(app, "generate_highlight", fake_gen)
        rows = [[True, "0:03.0", "high", 0.9, 1.5, 1.5, ""]]
        steps = list(app.gen_selected("vid.mp4", rows, [app.GEN_ALL_LABEL],
                                      vertical=False))
        assert modes == [False] and "已生成" in steps[-1][4]
