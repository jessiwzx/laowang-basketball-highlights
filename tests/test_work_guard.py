"""`tests/write_guard.py`（用户数据防火墙的纯逻辑）单测。

**本档一个字节都不写真实的 `work/`**：重定向两个用例只算字符串、
根档快照用例拿 `tmp_path` 当假仓库根。
"""

import os

from tests import write_guard


def test_guard_redirects_paths_inside_repo_work(monkeypatch, tmp_path):
    """cwd = 仓库根 ⇒ `"work/<视频名>"` 落在真 work/ 内 ⇒ 必须重定向。

    这是守卫的核心牙齿：忘了 chdir 的测试走的就是这条路径。
    """
    monkeypatch.chdir(write_guard.REPO)          # 让前提确定：cwd = 仓库根
    guard_root = tmp_path / "guard"
    dest, hit = write_guard.guard_work_path("work/somevideo", guard_root)
    assert hit is True
    assert write_guard.is_inside(dest, guard_root)
    assert os.path.basename(dest) == "somevideo"        # 尾巴原样保留
    assert not write_guard.is_inside(dest, write_guard.REAL_WORK)


def test_guard_leaves_outside_paths_alone(monkeypatch, tmp_path):
    """cwd = tmp_path（既有隔离手法）⇒ `"work/v"` 不在真 work/ 内 ⇒ 原样放行。

    这条钉住「守卫不与既有 chdir 沙箱对抗」：断言写成 `<tmp>/work/...` 的既有
    测试照旧绿，守卫只兜「本该隔离而漏了」的那些。
    """
    monkeypatch.chdir(tmp_path)
    dest, hit = write_guard.guard_work_path("work/v", tmp_path)
    assert dest == "work/v"                      # 逐字原样
    assert hit is False


def test_root_file_state_detects_add_delete_and_modify(tmp_path):
    """`root_files_state` + `describe_root_file_changes` 三种差异都要认。"""
    def desc(changes):
        return "；".join(changes)

    # 什么都没动 ⇒ 空列表
    empty = write_guard.root_files_state(tmp_path)
    assert write_guard.describe_root_file_changes(empty, empty) == []

    # 由无到有
    (tmp_path / "tags.json").write_bytes(b"v1")
    added = write_guard.root_files_state(tmp_path)
    assert desc(write_guard.describe_root_file_changes(empty, added)) \
        == "tags.json：由无到有"

    # 内容变了
    (tmp_path / "tags.json").write_bytes(b"v2")
    modified = write_guard.root_files_state(tmp_path)
    assert desc(write_guard.describe_root_file_changes(added, modified)) \
        == "tags.json：内容变了"

    # 由有到无
    (tmp_path / "tags.json").unlink()
    deleted = write_guard.root_files_state(tmp_path)
    assert desc(write_guard.describe_root_file_changes(modified, deleted)) \
        == "tags.json：由有到无"


def test_会话守卫真挂在_work_dir_for上(monkeypatch, tmp_path):
    """端到端证明「挂钩挂对了 + 危险是真的」，且**不碰命中计数器**（N 必须保持 0）。

    本可以调一次**已安装的**守卫来看它改道（最直白的牙齿证明），但守卫每拦一次就
    往命中清单里记一笔 —— 那会把交付判据「N=0」弄脏。所以拆成两半：
      * 挂钩：`bbh.app._work_dir_for.__module__` 已不是 `bbh.app` ⇒ 模块属性真被换了
        （未包装的原函数是会话守卫闭包住的，`__wrapped__` 之类拿不干净，
        生产侧更不许为此加属性 —— 换个方向证同一件事）；
      * 危险：cwd = 仓库根时生产那串相对路径**确实**落在真 `work/` 里，
        且会被判定拦下。改道后的具体形状由上面两个纯函数用例逐字覆盖。
    """
    import bbh.app as A

    assert A._work_dir_for.__module__ != "bbh.app", \
        "会话守卫没挂上：bbh.app._work_dir_for 还是原函数"

    monkeypatch.chdir(write_guard.REPO)          # 仓库根：不 chdir 的测试就在这儿
    raw = os.path.join("work", "somevideo")
    assert write_guard.is_inside(raw, write_guard.REAL_WORK)   # 危险前提成立
    dest, hit = write_guard.guard_work_path(raw, tmp_path / "guard")
    assert hit is True
    assert not write_guard.is_inside(dest, write_guard.REAL_WORK)


def test_守卫改道链路真的生效(monkeypatch, tmp_path):
    """★ **改道链路**的入库探测器：调已安装的 `bbh.app._work_dir_for` 真拦一笔。

    上面那条（`test_会话守卫真挂在_work_dir_for上`）只证「模块属性被换过」——
    任何外来函数都满足它，**包括空壳**。这条补的就是那个洞：把已装好的钩子换成
    「同名同签名、直接返回原路径」的假货后，原来四条用例**全绿**、收尾照样打
    「✅」（复审实测）。这里在 `write_guard.expect_hit()` 里真调一次钩子，一笔都
    没记到就抛错 ⇒ **空壳形状下这条必红**。

    落点断言一次钉住两条机制：
      * **改道**：返回的不再是真 `work/` 里的路径；
      * **每测 guard 根**（`_work_guard_scope`）：落到**本测试自己的** `tmp_path` 下，
        且形状与「本该 chdir」逐字一致（`<tmp>/work/<尾巴>`）。

    取证记录（上轮的一次性负对照，结论钉在这里，别只活在将删的报告里）：故意不
    chdir 调 `_cut_preview` ⇒ 守卫拦下 1 笔、写入被改道到**该测试自己的** `tmp_path`、
    仓库真档 mtime 纹丝不动 —— 「拦得住 / 改道对 / 真档没碰」三件事一次证齐。
    """
    import bbh.app as A

    monkeypatch.chdir(write_guard.REPO)      # 相对解析落在真 work/ 里（前提确定）
    before = len(write_guard.expected_hits())
    with write_guard.expect_hit():           # 块内命中记为「预期」；0 笔 ⇒ 抛错
        got = A._work_dir_for("v.mp4")       # 调**已安装的**模块属性（真钩子）
    assert len(write_guard.expected_hits()) == before + 1
    assert got == str(tmp_path / "work" / "v")
    assert write_guard.is_inside(got, tmp_path)
    assert not write_guard.is_inside(got, write_guard.REAL_WORK)


# ---------- 收尾判词（`write_guard.summary_line`）的纯函数用例 ----------
# 判词从 conftest 的收尾函数里挪出来正是为了这个：收尾代码没法单测、判据就只能靠
# 「跑一遍看它打了什么」来验；挪成纯函数后，好形状/坏形状都能在入库用例里直接喂。

def test_summary_line_有非预期拦截就不打勾():
    """非预期 ≥1 ⇒ 不含 ✅（无论预期几笔、退出码是不是 0）。"""
    for exitstatus in (0, 1):
        for expected in (0, 1):
            line = write_guard.summary_line(1, expected, exitstatus)
            assert "✅" not in line
            assert write_guard.SUMMARY_OK not in line
            assert write_guard.SUMMARY_WARN in line


def test_summary_line_红run不打勾():
    """★ 复审 X-2 的钉子：非预期 0、预期 ≥1，但**退出码非 0** ⇒ 不含 ✅。

    这正是「每测 guard 根失效」的形状 —— `expect_hit()` **先记账、后断言** ⇒ 那笔
    命中已经躺在 `_expected` 里，守卫自带用例却红了。汇总行是交付判据自己的证人：
    只数两堆数字、不看 `exitstatus`，就会替一条**已经红的 run** 打 ✅。
    """
    line = write_guard.summary_line(0, 1, 1)
    assert "✅" not in line
    assert write_guard.SUMMARY_OK not in line
    assert write_guard.SUMMARY_WARN in line


def test_summary_line_没跑到自带用例是独立的不可判支():
    """复审 X-3：非预期 0 + 预期 0 + 退出码 0 ⇒ 「判据不可判」，不冒充 ⚠、更不打 ✅。

    `-k` 子集 / `--co` / `-x` 早停时 `_expected` 为空，**两堆数字上**与「钩子被换成
    『还在但不改道』的空壳」长得一模一样（都是 `0 / 0` + 退出码 0 时也区分不开），
    故必须报成独立一支。判据是**两个文案常量本身**（见下面两行断言）。
    """
    line = write_guard.summary_line(0, 0, 0)
    assert write_guard.SUMMARY_INCONCLUSIVE in line
    assert "✅" not in line
    assert write_guard.SUMMARY_OK not in line
    # 与「⚠ 那句」的关系：逐字为两个不同的常量，不是「看起来不一样」
    assert write_guard.SUMMARY_WARN not in line
    assert write_guard.SUMMARY_WARN != write_guard.SUMMARY_INCONCLUSIVE


def test_summary_line_只有全绿才打勾():
    """正对照：非预期 0 + 预期 ≥1 + 退出码 0 ⇒ 必须含 ✅。

    没有这条，判词可以被写成「永远不含 ✅」而上面三条照样全绿 —— 那种「收紧」是
    把证人变成哑巴，不是把判据变严。
    """
    line = write_guard.summary_line(0, 1, 0)
    assert "✅" in line
    assert write_guard.SUMMARY_OK in line
    assert write_guard.SUMMARY_WARN not in line
    assert write_guard.SUMMARY_INCONCLUSIVE not in line


def test_summary_line_警告支不声称原因():
    """★ 复审 Minor-1 的钉子：`exitstatus = 5` 落 ⚠，且那句 ⚠ **不许声称原因**。

    来历（真机复现）：`pytest tests/ -q -k zzz_nothing_matches` ⇒ 一条没选中、全
    deselected、`exitstatus = 5` (NO_TESTS_COLLECTED)、非预期 0、预期 0 ⇒ 落这一支，
    而此刻**既没有红 run、也没有任何拦截** —— 上一版括号里那句「（红 run 或出现非预期
    拦截）」两件事都假。三个计数还原不出原因 ⇒ ⚠ 只说「判据没被满足」，原因不许猜。
    （同一形状还有 `--co` 收集到 0 条、`-x` 早停。）
    """
    line = write_guard.summary_line(0, 0, 5)
    assert write_guard.SUMMARY_WARN in line
    assert "✅" not in line
    assert write_guard.SUMMARY_OK not in line
    assert write_guard.SUMMARY_INCONCLUSIVE not in line   # 这一格是 ⚠，不是中性的 ℹ
    assert "红 run" not in write_guard.SUMMARY_WARN


def test_summary_line_警告支的判据是完整的():
    """★ 复审收尾反例的钉子：⚠ 印出来的必须是**完整判据**（三个合取项一个都不能少）。

    反例（真机形状）：`summary_line(0, 1, 1)` —— 自带用例绿、但**任一无关测试红**。
    上一版文案只印「非预期 0 且预期 ≥1」，而这两项此刻**恰好都成立** ⇒ 读者会去查
    一个**没坏**的项、甚至怀疑判据本身；真相是第三个合取项（退出码）没满足，而它
    没被印出来。

    判据与计划正文 `## 交付后已知项` F 档那句同源（「非预期 0 且 预期 ≥ 1 且退出码
    为 0」），故把三个字样**逐字**钉在这里 —— 文案将来再被砍掉一项，这条会红。
    """
    line = write_guard.summary_line(0, 1, 1)
    assert write_guard.SUMMARY_WARN in line
    assert "✅" not in line
    # 三个合取项的字样逐字都在（这就是本条的牙齿）
    assert "非预期 0" in write_guard.SUMMARY_WARN
    assert "预期 ≥1" in write_guard.SUMMARY_WARN
    assert "退出码为 0" in write_guard.SUMMARY_WARN
