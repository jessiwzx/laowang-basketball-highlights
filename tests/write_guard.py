"""测试侧「用户数据防火墙」的**纯逻辑与会话状态**（不依赖 pytest，可单测）。

背景：`bbh/app.py` 的 `_work_dir_for(video_path)` 返回
`os.path.join("work", <视频名>)` —— **相对路径，按进程 cwd 解析**。于是**任何测试
走到切片/预览/渲染/`/api/gen` 而没 chdir 到临时目录，就会安静地在仓库真实的
`work/` 下建目录、写 mp4/json** —— 不是失败，是污染用户的真实数据（7394 档 /
7.09 GB / 一个 git 副本都没有，写坏不可回滚）。另外 `tags.json` / `ui_prefs.json`
等 6 个档是**裸文件名**、同样按 cwd 解析，落在**仓库根**（`tags.json` 本身就是红线档）。

把它挂到 pytest 上的是 `tests/conftest.py`；本档除纯函数外还持有**会话状态**
（当前 guard 根、命中清单、已警告集合），因为「改道链路」必须能被入库用例**真触发、
真断言**——状态留在 conftest 里测试就拿不到同一份（`import tests.conftest` 会得到
第二份模块对象，看到的永远是空清单；而 conftest 与本档走的都是
`from tests import write_guard`，`is` 为同一个模块对象）。

挂钩覆盖面（**别读成「测试进程再也写不进真数据」的全域保证**）：

* 覆盖 `_work_dir_for` 的 **40 处**路径计算。量法：
  `grep -rn "_work_dir_for" bbh/ --include=*.py | grep -v "def _work_dir_for" | grep -v "__main__.py"`
  ⇒ 40（`bbh/app.py` **35** 处裸名调用 + `bbh/web/*` **5** 处 `A._work_dir_for`；
  `bbh/app.py:336` 那行是 `def`、`bbh/web/__main__.py:3` 是注释，都不计）。
* **不覆盖**：`bbh/app.py` 的 `migrate_root_calibrations()` —— 它 `os.listdir("work")`
  + `shutil.copy2` 直接往真 `work/` 搬档、认领成功还**删仓库根的标定档**，全程不过
  `_work_dir_for`，重定向守对它完全无效；以及测试档里字面写的
  `os.path.join("work", ...)`（全仓 20+ 处）。这些今天各自靠 `chdir` 隔离。
* `work/` 整树不做逐档快照（7394 档太贵）⇒ 重定向守 + 仓库根 6 档报警器是**两条网**，
  不是全域护栏。

「为什么不把数据根显式化（作废的 `BBH_WORK_ROOT` env 方案）」与「测试自己的
monkeypatch 优先」这两条契约写在 `tests/conftest.py` 顶部；留档的交接在已提交的计划正文
`docs/superpowers/plans/2026-09-19-frontend-refactor-edit-bay.md` 的 `## 交付后已知项`
F 档（已订正为本次交付的形态：作废的 env 方案、实测 40 处、6 档名单）——那节是留档的
交接，不是取舍说明。
"""

import contextlib
import hashlib
import os
from pathlib import Path

# 本档在 tests/ 下 ⇒ 上两级就是仓库根
REPO: Path = Path(__file__).resolve().parent.parent
REAL_WORK: Path = REPO / "work"

# 仓库根这 6 个档一个都不许被测试碰：`tags.json` 本身就是红线档，
# 另外四个是用户手工标定的产物（`calibration*.json/png`）。名单与
# `tests/e2e/test_edit_bay_e2e.py:174-177` 的 e2e 侧同款守卫**完全一致**
# （别多别少——多一个就名不副实，少一个就漏一档）。
PROTECTED_ROOT_FILES: tuple[str, ...] = (
    "tags.json", "ui_prefs.json",
    "calibration.json", "calibration_ref.png",
    "calibration_seg2.json", "calibration_ref_seg2.png")


# ---------- 会话状态（conftest 挂钩与守卫自带用例共用同一份） ----------

# 当前 guard 根。用 1 元素 list 当可变盒子：包装函数在调用**当时**才读，才能跟着
# 测试走（function 级 `_work_guard_scope` 每测改写，session 级挂钩先给兜底值）。
_root: list = [None]

_hits: list[tuple[str, str, str]] = []       # **非预期**命中：(nodeid, 原路径, 改道后)
_expected: list[tuple[str, str, str]] = []   # **预期**命中：守卫自带用例造成的
_warned: set[str] = set()                    # 同一条原路径只 warn 一次，别刷屏


def set_root(path) -> None:
    """设「当前 guard 根」（`_work_guard_scope` 每测调一次，测完还原）。"""
    _root[0] = path


def get_root():
    """当前 guard 根（挂钩侧与自带用例读同一个值）。"""
    return _root[0]


def record_hit(nodeid: str, orig: str, dest: str) -> bool:
    """记一笔命中，返回「这条原路径是不是第一次见」（调用方据此决定要不要 warn）。"""
    _hits.append((nodeid, orig, dest))
    if orig in _warned:
        return False
    _warned.add(orig)
    return True


def hits() -> list[tuple[str, str, str]]:
    """**非预期**命中清单 —— 交付判据：必须为空。"""
    return list(_hits)


def expected_hits() -> list[tuple[str, str, str]]:
    """**预期**命中清单（守卫自带用例）—— 交付判据：必须 ≥1。"""
    return list(_expected)


@contextlib.contextmanager
def expect_hit():
    """「这一块里守卫**应该**拦下一笔」的窗口 —— 守卫自带用例的探测器。

    块内发生的命中退出时从 `hits()` 移进 `expected_hits()`（非预期计数不受影响）；
    **一笔都没记到就抛 `AssertionError`**。

    这条断言正是「钩子还在、但改道被丢」那个空壳形状的探测器：把已装的
    `bbh.app._work_dir_for` 换成同名同签名、直接返回原路径的假货，改道不再发生 ⇒
    这里 0 笔 ⇒ 用例必红。**没有它，防火墙可以静默变成 no-op 而整套测试全绿，
    收尾还照样打「✅」**——本仓库最贵的那种假绿。

    块内**抛异常**时（复审 X-1）：已记的命中**照样转进预期**再让异常继续抛 ——
    否则那笔会假红地留在「非预期」里；异常原样上抛，**异常路径上不抛**上面那句
    「预期命中 0 笔」（那会把真异常盖掉，排查时看不到真因）。

    **用法契约**（复审 X-4 的裁定是「不修、记账」，故只用文字钉住）：
    * 本 CM **只给守卫自带用例用** —— 今天唯一调用者是
      `tests/test_work_guard.py::test_守卫改道链路真的生效`；
    * 块内**只许触发被挂钩函数一次**，不许夹带别的、可能顺带命中的调用；
    * 它把窗口内**所有**命中一律转正，不校验「是谁开的窗、转正了几笔」⇒
      「顺手把无关命中一起洗成预期」是它的**已知弱点、故意不设防**（不为它加白名单、
      不改转正机制）。将来若要收紧，形状是「窗口内断言恰好 1 笔 + 记开窗者 nodeid」。
    """
    start = len(_hits)
    try:
        yield
    except BaseException:
        _expected.extend(_hits[start:])
        del _hits[start:]
        raise
    found = _hits[start:]
    if not found:
        raise AssertionError(
            "预期命中 0 笔：work/ 写守卫的改道链路没生效 —— 钩子可能被换成了"
            "「还在但不改道」的空壳，或每测 guard 根被写坏。")
    _expected.extend(found)
    del _hits[start:]


# ---------- 纯函数 ----------

# 收尾判词的文案常量：**判据逐字钉在这里**（测试也断言这两个常量本身，
# 不用「看起来不一样」这种断言 —— 见 tests/test_work_guard.py）。
SUMMARY_HEAD = "⚖ work/ 写守卫：非预期拦截 {} 次（预期触发 {} 次，来自守卫自带用例）"
SUMMARY_OK = " ✅"
SUMMARY_WARN = " ⚠ 判据要求未满足：非预期 0 且预期 ≥1 且退出码为 0"
SUMMARY_INCONCLUSIVE = " ℹ 判据不可判：本会话未跑到守卫自带用例（-k/--co/-x 选子集？）"
# 上面两条用的是**不同标记**（⚠ vs ℹ）：不是同一句话换词，而是两码事 ——
# 「判据没被满足」与「压根没跑到」。别把后者并进前者（任务书 ②：不许冒充 ⚠）。
#
# ⚠ 那一支**只说「判据没被满足」、不声称原因**（复审 Minor-1 真机复现）：
# `pytest tests/ -q -k zzz_nothing_matches` ⇒ 全 deselected、`exitstatus = 5`
# (NO_TESTS_COLLECTED)、非预期 0、预期 0 ⇒ 落到这支，而此刻**既没有红 run、也没有
# 任何拦截** —— 上一版括号里写的「（红 run 或出现非预期拦截）」两句话都假。
# 三支的入参只有三个计数，**计数不足以还原原因** ⇒ 话少不等于放宽，谎报才是。
#
# ⚠ 但「不说原因」不等于「少印判据」：印出来的必须是**完整判据**（三个合取项一个都
# 不能少，与计划正文 `## 交付后已知项` F 档那句逐字同源）。少了「且退出码为 0」就会
# 把读者引向一个**没坏**的项 —— 复审真机反例 `summary_line(0, 1, 1)`（自带用例绿、
# 但任一无关测试红）：前两个合取项恰好都成立，真相是退出码那项没满足。
# 用例 `test_summary_line_警告支的判据是完整的` 把这三个字样钉死。


def summary_line(unexpected: int, expected: int, exitstatus: int) -> str:
    """**纯函数**：会话收尾那行判词（入参是计数，不是清单）。

    这是交付判据自己的证人 ⇒ 判词只许**收紧**、不许在证据不足时给好话。三分支：

    * 非预期 == 0 且 预期 >= 1 且 `exitstatus == 0` ⇒ `SUMMARY_OK`（✅）。
      看 `exitstatus` 是必须的：`expect_hit()` 先记账后断言 ⇒ 每测 guard 根一旦失效，
      那笔命中已经进了 `_expected`，自带用例却红了 —— 只数两堆数字就会替一条**红的
      run** 打 ✅（复审 X-2 的实测形状）。
    * 非预期 == 0 且 预期 == 0 且 `exitstatus == 0` ⇒ `SUMMARY_INCONCLUSIVE`
      （本会话压根没跑到自带用例：`-k` 子集 / `--co` / 收集不到），**不冒充**
      `SUMMARY_WARN`（那支的语义是「判据没被满足」）、更不打 ✅。
    * 其余（有非预期拦截 / 预期 ≥1 但 run 红了 / 预期 0 且 `exitstatus != 0`）
      ⇒ `SUMMARY_WARN`。这一支**只说「判据没被满足」，不声称原因** —— 三个计数
      还原不出原因（真机一例：`-k zzz_nothing_matches` ⇒ 全 deselected、
      `exitstatus = 5`、非预期 0、预期 0，此刻既没有红 run 也没有任何拦截）。
      但它印出的是**完整判据**（三个合取项，与计划正文 `## 交付后已知项` F 档那句
      同源）：不说「为什么没满足」，只说「**哪条要求**没满足」—— 少印一项就会把
      读者引向一个没坏的项（`(0, 1, 1)`：自带用例绿、任一无关测试红）。

    **为什么不把「预期 == 0 且 `exitstatus != 0`」并进不可判支**（复审 Minor-1 给的
    另一条路，**控制者裁定不采**，理由照录）：三个计数**无法区分**「压根没跑到
    （子集 / 一条都没收集到）」与「自带用例**跑了却没记到就红了**」—— 后者正是
    「守卫坏了」的形状（空壳探针复现出来就是 `2 failed` + 预期 0）。把后者报成中性的
    `SUMMARY_INCONCLUSIVE` 会**低估真故障**；宁可话少（不给原因）也不谎报、也不放宽。
    真要分这两者，得让入参带上「有没有执行到自带用例」这类证据，而不是继续挤计数。
    """
    head = SUMMARY_HEAD.format(unexpected, expected)
    if unexpected == 0 and expected >= 1 and exitstatus == 0:
        return head + SUMMARY_OK
    if unexpected == 0 and expected == 0 and exitstatus == 0:
        return head + SUMMARY_INCONCLUSIVE
    return head + SUMMARY_WARN


def is_inside(path, parent) -> bool:
    """`path` 是否落在 `parent` 里（含 `parent` 自身）。

    跨盘符时 `os.path.commonpath` 抛 `ValueError` ⇒ 当作「不在里面」
    （Windows 上 tmp 可能在别的盘）。`normcase` 是给 Windows 的大小写不敏感
    兜底：`commonpath` 内部已经 lower 过，但返回的是原大小写。
    """
    try:
        p = os.path.abspath(str(path))
        q = os.path.abspath(str(parent))
        return os.path.normcase(os.path.commonpath([p, q])) == os.path.normcase(q)
    except ValueError:
        return False


def guard_work_path(orig, guard_root, real_work=REAL_WORK) -> tuple[str, bool]:
    """**纯函数**：`orig` 落在真 `work/` 内 ⇒ 重定向到 `guard_root` 下的同一尾巴。

    返回 `(新路径, 是否拦截)`；没拦就原样返回 `orig`。

    判定用 `abspath` —— `orig` 是相对路径（生产常态）时按**当前 cwd** 解析：
    cwd=仓库根 ⇒ `"work/foo"` 落在真 work 内 ⇒ **拦**；
    cwd=某测试的 tmp_path ⇒ `"work/foo"` 落在 tmp 下、不在真 work 内 ⇒ **放行**。
    后者正是仓库既有隔离手法（`monkeypatch.chdir(tmp_path)`）赖以成立的前提，
    守卫必须原样放行、不去对抗它。

    落点**保留 `work` 这一层**（`<guard_root>/work/<尾巴>`）：这样「守卫替你隔离」
    与「你本该 chdir」两条路算出来的路径**逐字一致**（chdir 沙箱也是
    `<tmp>/work/<尾巴>`）。否则同一测试里一半经守卫写、一半按相对路径读会分叉，
    症状是「刚写的数据不见了」。
    """
    s = str(orig)
    if not is_inside(s, real_work):
        return s, False
    tail = os.path.relpath(os.path.abspath(s), os.path.abspath(str(real_work)))
    return os.path.join(str(guard_root), "work", tail), True


def root_files_state(root) -> dict[str, str | None]:
    """仓库根 6 个红线档的 sha256 快照（档不存在记 `None`）。"""
    state: dict[str, str | None] = {}
    for name in PROTECTED_ROOT_FILES:
        try:
            state[name] = hashlib.sha256((Path(root) / name).read_bytes()).hexdigest()
        except OSError:
            state[name] = None          # 不存在 / 读不动都当「无」
    return state


def describe_root_file_changes(before, after) -> list[str]:
    """人话描述两次快照之间的差异：`由无到有` / `由有到无` / `内容变了`。

    只按 `PROTECTED_ROOT_FILES` 的顺序遍历 ⇒ 输出顺序确定、且不会把别的东西
    报进来。两次一模一样 ⇒ 空列表。
    """
    changes: list[str] = []
    for name in PROTECTED_ROOT_FILES:
        b, a = before.get(name), after.get(name)
        if b == a:
            continue
        if b is None:
            changes.append(f"{name}：由无到有")
        elif a is None:
            changes.append(f"{name}：由有到无")
        else:
            changes.append(f"{name}：内容变了")
    return changes
