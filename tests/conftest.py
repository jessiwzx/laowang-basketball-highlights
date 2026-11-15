import os
import sys
import warnings

# 本目录加进 sys.path：测试模块之间用**裸模块名**互相 import（历史写法，
# 如 `from test_web_session import NAME`）。加了 tests/__init__.py 之后 pytest
# 不再自动把本目录塞进 sys.path，这里补回来。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 本项目默认检出器 = YOLO（见 bbh/detector_switch.py）。但单测用的合成视频画
# 的是纯色小球、**只有 HSV 检得出**；静止链过滤的断言也按原实现（orig）写。
# 所以测试进程里把两者固定回 HSV / orig，保证测试与检出器实现解耦。
# ⚠️ 必须在 `import bbh` 之前设置——detector_switch / static_filter 在导入时读 env。
os.environ["BBH_DETECTOR"] = "hsv"
os.environ["BBH_STATIC_FILTER"] = "orig"
# person 框检人（bbh/person.py）同理：合成视频里没有人，固定 off 走
# 出手点定宽裁剪；person 路径在 test_person/test_shooter 里注入假框测。
os.environ["BBH_PERSON"] = "off"

import cv2                  # noqa: E402
import numpy as np          # noqa: E402
import pytest               # noqa: E402

from bbh.models import Calibration    # noqa: E402
from tests import write_guard         # noqa: E402

# 场景：640x360，筐口矩形 (300,140,80,40)，rim_y=154
# 篮筐画成灰色横线（避免与球同色干扰）；球 BGR(20,100,245)
# 第 60~90 帧：球从 (340,100) 直线下落到 (340,220) 穿越 rim_y
W, H, FPS, N = 640, 360, 30, 180
HOOP_RECT = (300, 140, 80, 40)
BALL_BGR = (20, 100, 245)
GOAL_CROSS_FRAME = 75  # 球心过 rim_y 的帧（近似）
# 进球后网摆动画帧窗（v12 篮网信号）：真进球球穿网必撑网——合成视频同款。
# 覆盖 net_motion 事后三窗（cross+0.45/0.70/0.95s ≈ 帧 89/96/104）
NET_SWAY_FRAME0, NET_SWAY_FRAME1 = 88, 112


def _ball_xy(frame_idx):
    if 60 <= frame_idx <= 90:
        k = (frame_idx - 60) / 30.0
        return 340, int(100 + 120 * k)
    return None


def make_video(path):
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    for i in range(N):
        img = np.full((H, W, 3), (40, 44, 50), np.uint8)
        cv2.line(img, (300, 154), (380, 154), (180, 180, 180), 3)  # 灰色筐
        pos = _ball_xy(i)
        if pos:
            # r=11 → 检出面积 ~380px² ≥ 0.1×筐矩形面积(320px²)：
            # 判定器的「下落实体校验」要求下方证据含球体量级大点，
            # 球画小了会被当碎片过滤掉
            cv2.circle(img, pos, 11, BALL_BGR, -1)
        if NET_SWAY_FRAME0 <= i <= NET_SWAY_FRAME1:
            # 网区两条白色竖线左右摆动（白线零饱和度，HSV 检不出、
            # 不进检出流；帧间位移大 → 网信号远超 NET_STATIC_TH）
            sway = int(14 * np.sin((i - NET_SWAY_FRAME0) / 4.0))
            cv2.line(img, (330 + sway, 158), (330 + sway, 216),
                     (235, 235, 235), 2)
            cv2.line(img, (352 - sway, 158), (352 - sway, 216),
                     (235, 235, 235), 2)
        vw.write(img)
    vw.release()
    return path


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory):
    return make_video(str(tmp_path_factory.mktemp("vid") / "sample.mp4"))


@pytest.fixture()
def sample_calibration():
    return Calibration(hoop_rect=HOOP_RECT, hsv_low=(0, 80, 120),
                       hsv_high=(25, 255, 255), frame_size=(W, H))


# ---------- 用户数据防火墙：work/ 写守卫 ----------
# 纯逻辑在 tests/write_guard.py（本档只做挂钩，判定一律调那边，别在这里重写）。
#
# 为什么是测试侧重定向、而不是把数据根显式化（`BBH_WORK_ROOT` env 那条形状
# 已被控制者裁定作废），三条原因照录备查：
#   1. 那会让 _work_dir_for 变成 cwd 无关的绝对路径 ⇒ 改**已冻结的旧界面
#      （Gradio）行为**；
#   2. 仓库里大量测试的隔离手法就是 monkeypatch.chdir(tmp_path) + 相对解析
#      （tests/test_calib_per_video.py、tests/test_multi_segment.py 等），
#      env 覆盖会把这些沙箱全部架空、断言写成 <tmp>/work/... 的一律红；
#   3. e2e（tests/e2e/test_edit_bay_e2e.py）靠 cwd=临时目录起 uvicorn 子进程
#      做隔离，子进程 env=os.environ 会**继承**父进程的 BBH_WORK_ROOT ⇒
#      服务端的 work/ 与 e2e 断言的那个 root/work/ 分叉。

# 会话状态（当前 guard 根 / 命中清单 / 已警告集合）在 tests/write_guard.py 里——
# 「改道链路」必须能被入库用例真触发、真断言，而状态留在本档测试就拿不到同一份
# （`import tests.conftest` 会得到第二份模块对象，看到的永远是空清单）。
# 本档走 `from tests import write_guard`，与测试档拿到的是同一个模块对象。


@pytest.fixture(scope="session", autouse=True)
def _work_write_guard(tmp_path_factory):
    """★ 用户数据防火墙（session、autouse）。

    `bbh/app.py:_work_dir_for` 返回相对路径 `"work/<视频名>"`、按进程 cwd 解析 ⇒
    忘了 chdir 的测试会安静地把数据写进仓库真实的 `work/`（7394 档 / 7.09 GB /
    零 git 副本）。这里把 `bbh.app._work_dir_for` 这个**模块属性**包一层：拿到原
    返回值后过 `write_guard.guard_work_path`，落在真 `work/` 内的改指到当前 guard 根。

    `A._work_dir_for` 是**唯一的挂钩点**：`bbh/app.py` 内部是裸名调用（照读模块
    全局）、`bbh/web/*` 走 `A._work_dir_for` ⇒ 该函数的 **40 处**路径计算一网打尽
    （量法见 `tests/write_guard.py` 头注释；别去逐个改调用点）。

    测试自己后来的 monkeypatch（`chdir` 或 `setattr(A, "_work_dir_for", ...)`）
    **优先级更高**——这是期望行为，守卫只兜住「本该隔离而漏了」的那些。
    """
    import bbh.app as A

    real = A._work_dir_for                 # 未包装的原函数（闭包留住）
    write_guard.set_root(tmp_path_factory.mktemp("work_guard"))   # session 兜底根

    def guarded_work_dir_for(video_path):
        # 不变式：装钩子前必须先设根。宁可直接炸也不要放行 —— `guard_work_path` 对
        # `None` 不设防，会算出 `None/work/<尾巴>` 这种相对路径、在仓库根建出一个叫
        # `None/` 的目录；放行更糟（会静默写进真 `work/`）。正常走不到这儿：
        # `_work_guard_scope` 的 finally 还原的是 `prev`，首测即 session 兜底根。
        root = write_guard.get_root()
        if root is None:
            raise RuntimeError("work/ 写守卫：guard 根未初始化")
        orig = real(video_path)
        dest, hit = write_guard.guard_work_path(orig, root)
        if hit:
            nodeid = os.environ.get("PYTEST_CURRENT_TEST", "?").split(" (")[0]
            if write_guard.record_hit(nodeid, orig, dest):    # True = 首次见这条原路径
                warnings.warn(
                    f"work/ 写守卫拦截：{orig} → {dest}"
                    "（出自守卫自带用例的话属**预期触发**，收尾行会把它计入"
                    "「预期触发」而不是「非预期拦截」）",
                    UserWarning, stacklevel=2)
        return dest

    # monkeypatch fixture 是 function 级的、这里用不了 ⇒ 用 MonkeyPatch.context()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(A, "_work_dir_for", guarded_work_dir_for)
        yield


@pytest.fixture(autouse=True)
def _work_guard_scope(_work_write_guard, tmp_path):
    """把「当前 guard 根」设成**该测试自己的 tmp_path**，测完还原。

    这样「忘了 chdir 的测试」的数据只会落到它自己的 tmp 里 —— 与它本该 chdir 的
    落点**逐字一致**（`guard_work_path` 保留 `work` 这一层，两边都是
    `<tmp>/work/<尾巴>`）。显式依赖 session 的 `_work_write_guard` 只为钉住顺序：
    挂钩必须先装上，这里的根才有意义。
    """
    prev = write_guard.get_root()
    write_guard.set_root(tmp_path)
    try:
        yield
    finally:
        write_guard.set_root(prev)


# 仓库根 6 个红线档的基线**在 `tests/conftest.py` 的导入期就拍**（早于任何测试档
# import）⇒ 收集阶段（测试档 import 期）的改动也在覆盖内。放在 session fixture 的
# setup 里拍会漏掉这一段 —— 那时所有测试模块早已 import 完，改后的状态会被当成基线。
#
# ⚠ 结构性盲区（复审 X-6，裁定「记账不修」）：「进程启动 → 本 conftest 被 import」
# 之间的写入仍看不到（`-p` 插件 / `sitecustomize` / `.pth` 的 import 期）。要覆盖它
# 得把求值挪进 `-p` 插件，成本不值当；本仓库没有根 conftest / pytest.ini /
# pyproject.toml / tests/__init__.py ⇒ 那个窗口里今天没有任何本仓库的代码。
_ROOT_FILES_BEFORE = write_guard.root_files_state(write_guard.REPO)


@pytest.fixture(scope="session", autouse=True)
def _root_file_guard():
    """★ 仓库根红线档报警器（session、autouse）。

    `tags.json` / `ui_prefs.json` / `calibration*.{json,png}` 是**裸文件名**、按
    cwd 解析 ⇒ 落在仓库根。这 6 个档一个都不许被测试碰：与上面那份**导入期**基线
    比 sha256，teardown 里不一致就抛 `AssertionError`（不是 warning —— 这是红线
    风险，必须红）。判据只有这一条，不设白名单。

    `work/` 整树的逐档快照**不做**（7394 档，每测试一次太贵）；重定向守 + 本报警器
    是**两条网**，不是「测试进程再也写不进真数据」的全域保证（覆盖面见
    `tests/write_guard.py` 头注释）。⚠ 这条真响了：**停下上报，不许降判据**。
    """
    yield
    after = write_guard.root_files_state(write_guard.REPO)
    changes = write_guard.describe_root_file_changes(_ROOT_FILES_BEFORE, after)
    if changes:
        raise AssertionError(
            "仓库根红线档被改动（用户数据防火墙报警）：\n  "
            + "\n  ".join(changes)
            + "\n⇒ **不一定怪测试**：也可能是别的进程写的 —— 例如用户正开着的旧界面"
              "实例（它的 cwd 就是仓库根，界面上勾一下就会写 `tags.json` / "
              "`ui_prefs.json`）。先核对 mtime 与 `git diff` 再归因。")


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """会话收尾：取三样东西 → 交给 `write_guard.summary_line` 出整行判词。

    判词是**纯函数**、单测钉在 `tests/test_work_guard.py`（三分支：✅ / ⚠ 判据要求 /
    ℹ 判据不可判），本函数只负责 `write_sep` 与列出非预期命中的 nodeid —— 判据不在这
    里就地算，否则它又会是一段没有用例可钉的收尾代码。
    """
    unexpected = write_guard.hits()
    terminalreporter.write_sep(
        "=", write_guard.summary_line(
            len(unexpected), len(write_guard.expected_hits()), exitstatus))
    for nodeid in sorted({n for n, _, _ in unexpected}):
        terminalreporter.write_line(f"  - [非预期] {nodeid}")
