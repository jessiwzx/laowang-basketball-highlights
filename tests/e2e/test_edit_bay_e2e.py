"""剪辑台页级 e2e：真夹具 + 真服务 + 真产物，由 Playwright 驱动真浏览器。

隔离机制（这是整个文件成立的前提）：`_work_dir_for()` 返回相对路径
`work/<视频名>`，`ui_prefs.json` / `tags.json` 也是裸文件名——都按 cwd 解析。
所以 uvicorn 用 `cwd=临时目录` 起，则 work/、两个根档全落在临时目录，
用户的 work/ 与仓库根那 5 个无 git 副本的档一个字节都不碰；副作用
（勾选/打标/裁决/删除）反而成了可以随便验的东西。

Node 或 playwright 缺失 → skip 并说明（不假绿，也不阻塞没有 Node 的机器）。
"""
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from bbh.models import Calibration, GoalEvent, save_events

ROOT = Path(__file__).resolve().parents[2]
SPEC = Path(__file__).resolve().parent / "edit_bay.spec.mjs"
PW = ROOT / "frontend" / "node_modules" / "playwright" / "index.mjs"
NODE = shutil.which("node")
NAME = "e2e_fixture"
DUR = 6.0
# 项数门（E1 G1b）：spec 实跑项数钉死在这里。改 spec 条数的任务必须**同任务**改这里——
# 改了 spec 没改钉 = 本文件红（这正是门的目的）。真值来源：count-spec-items.py。
EXPECTED_ITEMS = 209        # 本文件（bay）钉数；wall/calib 各自钉在 test_wall_e2e.py / test_calib_e2e.py
# 2026-11-01 205 → 209：输出方式加第三项「⚡ 25s 精选（4s 钩子）」，spec 里
# 同步补了 4 条（画幅置灰 / 自动选横屏 / 合集文案沿用 reel 串 / 可点生成）。
                            # 191→189（2026-09-25 删除不再二次确认）：删「修复轮 1 ①」确认框
                            # 那块 3 项、真删段补「不弹确认框」1 项
                            # 191→192（2026-10-11 置信度条配色跟档位走）：spec 新增一条
                            # 「12 行逐行比对 条色 == 档位色」的断言
                            # 192→201（2026-10-23 ★ 精选）：顶栏多一枚 ★ tag（+1）、
                            # 新增一段「最后一列是星 / 打星自动补勾 / 顶栏计数 /
                            # 单条清单默认勾精选 / S 键 toggle」共 8 项
                            # 201→205（2026-10-24 集锦也支持精选）：切模式勾选
                            # 重映射 + 集锦默认仍勾合集，共 4 项


def _fixture(root: Path) -> None:
    """造一份与真产物同形的检测结果（形状照 tests/test_web_locks.py::_make_result）。

    **12 颗球**、档位三色轮转（各 4 颗）、置信度递减、时刻 1.2s 起每 0.4s 一颗：
    行数够多**且段里把视口压矮**——「↑↓ 复核时当前行滚进视野」这条才有牙
    （1600×900 下表格可视区 662px、表体只有 492px ⇒ 12 行全在带内、**根本没有
    滚动条**，光靠 12 行验不动那条；`spec` 是把视口压到 1600×420 之后才验的）。
    档位筛选三档也各有 4 行可筛。缩略图（GIF）不造：那一格显示
    「—」，不影响要验的任何一列。

    ⚠ `net_*` 按 v18 全行测网给值（问题②）；唯独 i=1（🟡）留 None——
    A-3 要钉的说明块只在 net 为空（fail-open）时渲染。
    """
    work = root / "work" / NAME
    work.mkdir(parents=True)
    status = ("high", "review", "low")

    def _ev(i):
        st = status[i % 3]
        measured = i != 1                     # i=1 留 None 钉 fail-open 文案
        return GoalEvent(t=1.2 + i * 0.4, confidence=0.90 - i * 0.01, status=st,
                         cross_off=0.02, above_n=3, above_span=0.6, below_n=2,
                         below_span=0.4, descent_px=18.0, below_big=2,
                         net_post=12.5 if measured else None,
                         net_ctrl=3.1 if measured else None,
                         net_local=True if measured else None)

    evs = [_ev(i) for i in range(12)]
    save_events(str(work / "events.json"), video=str(root / "videos" / f"{NAME}.mp4"),
                duration=DUR, events=evs, warning=None, calib_key=[1, 2, 3],
                logic_ver=1)
    Calibration(hoop_rect=(300, 140, 80, 40), hsv_low=(0, 80, 120),
                hsv_high=(25, 255, 255), frame_size=(640, 360)).save(
        str(work / "calibration.json"))
    # 全局标签库（`bbh/app.py:35` 的 TAGS_FILE，按 cwd 解析 ⇒ 落在临时目录根）。
    # 不种它的话「1–9 打标签」这条键盘流没有可打的标签，等于没验。
    # ⚠ 形状必须是 `{"tags": [...]}`：`_load_tags`（`bbh/app.py:666-673`）走的是
    # `json.load(f).get("tags", [])`、`except (OSError, ValueError)` —— 写成裸列表时
    # `.get` 抛 `AttributeError`、**不在它捕获的清单里** ⇒ 每次 `/api/state` 都 500，
    # 而且 500 会被 `_wait_up` 的 `except URLError` 吞掉、伪装成「服务没起来」。
    # 真档就是这个形状（`_save_tags` 也这么写）。
    (root / "tags.json").write_text(
        json.dumps({"tags": ["三分", "上篮", "罚球"]}, ensure_ascii=False),
        encoding="utf-8")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_up(base: str, proc: subprocess.Popen, log: Path,
             timeout: float = 40.0) -> None:
    """等服务真起来；服务自己死了就立刻把它的输出端上来（别白等 40 秒）。

    `log` 是子进程 stdout/stderr 落地的文件。**每次都用新开的句柄读**
    （`Path.read_text`）：那是子进程在写的档，父进程手里没有、也不该留着
    它那份带缓冲的对象。
    """
    def _log_text() -> str:
        try:
            return log.read_text(encoding="utf-8", errors="replace")
        except OSError:                     # 还没建/被清了：当作没输出
            return ""

    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            raise AssertionError(
                f"临时服务启动即退出（code={proc.returncode}）：\n{_log_text()}")
        try:
            urllib.request.urlopen(f"{base}/api/state", timeout=1).read()
            return
        except urllib.error.HTTPError as e:
            # 服务在跑，但它自己把 /api/state 返成了 4xx/5xx（典型：夹具写坏了
            # tags.json ⇒ `_load_tags` 抛 AttributeError ⇒ 500）。HTTPError 是
            # URLError 的子类，被下面那条例外接住的话会被当成「还没起来」、
            # 一路重试到 40 秒，最后报成反的结论。这里立刻把状态码与响应体端上来。
            # 响应体是真有的（500 时实测 79 字节，与响应头的 content-length 吻合）；
            # 服务日志里还躺着 ASGI 的 traceback——两样都端上来，别只报个状态码。
            #
            # ⚠ 读响应体仍留一层兜底：这是**防御纵深**，不是常态。子进程的输出
            # 已经引到文件、不再走没人排空的管道（见 `bay` 的注释），但只要服务
            # 真卡住（哪怕卡在别的路径上），读体照样可能超时——那时如实写明
            # 「读不出来」，别让 TimeoutError 冒出去把「到底是 500 还是别的」
            # 又盖掉一层。
            # 覆盖面就是下面这两类，别把它说大了：`OSError`（含 `TimeoutError`
            # 与各种 socket 错误）+ `http.client.IncompleteRead`——后者**不是**
            # OSError 的子类（它走 `http.client.HTTPException` ⇒ `Exception`），
            # 头收全了、体被截断时抛的就是它。也**别**图省事写成裸 `except Exception`：
            # 那会把这段诊断自己的编程错误一起吃掉，又变成「盖一层」。
            try:
                body = e.read().decode("utf-8", "replace")[:400]
            except (OSError, http.client.IncompleteRead) as ex:
                body = f"（响应体读不出来：{type(ex).__name__}: {ex}）"
            raise AssertionError(
                f"临时服务在跑，但 GET /api/state 返 {e.code}：\n{body}"
                # 日志取**尾部**：traceback 的根因（`bbh/app.py:670 in _load_tags`、
                # `AttributeError: ...`）在最后几行，头部全是 uvicorn/fastapi 的
                # 框架帧。实测该 500 的 server.log 共 4872 字符，根因落在
                # 4718/4819 ⇒ 取头部 [:2000] 会把根因整段截掉、只剩噪音。
                f"\n--- 服务日志（{log.name}，尾部）---\n{_log_text()[-2000:]}") from None
        except (urllib.error.URLError, OSError):
            time.sleep(0.25)
    raise AssertionError(
        f"临时服务 {timeout:.0f}s 没起来：{base}"
        # 与上面两条失败路径拉齐：同一个函数的三种失败，别只这条不递证据
        # （这条只在「进程活着但一次都不应答 /api/state」时触发，本仓尚未观察到）。
        f"\n--- 服务日志（{log.name}，尾部）---\n{_log_text()[-2000:]}")


def _untouched(p: Path) -> bytes | None:
    """仓库根真档的快照（只读！）——用来证明 e2e 没碰用户的数据。"""
    return p.read_bytes() if p.exists() else None


@pytest.fixture(scope="module")
def bay(tmp_path_factory):
    if not NODE:
        pytest.skip("没有 node——剪辑台 e2e 需要 node + playwright（见计划「验证配方」C2）")
    if not PW.exists():
        pytest.skip(f"没有 {PW}——先 cd frontend && npm i -D --no-save playwright")
    root = tmp_path_factory.mktemp("bay")
    _fixture(root)
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    code = ("import uvicorn\n"
            "from bbh.web.app import create_app\n"
            f"uvicorn.run(create_app(), host='127.0.0.1', port={port}, "
            "log_level='warning')\n")
    # 仓库根**红线 5 档**（一个 git 副本都没有：误删就是真丢）开跑前全拍一遍。
    # `tags.json`/`ui_prefs.json` 是「夹具形状写错就会 500」的那两个；另外四个是
    # 用户手工标定的产物（`calibration*.json/png`），本计划零改动、e2e 更不该碰。
    # 这条守卫的卖点正是「证明 e2e 没碰用户数据」——只拍两个就名不副实。
    before = {f: _untouched(ROOT / f) for f in (
        "tags.json", "ui_prefs.json",
        "calibration.json", "calibration_ref.png",
        "calibration_seg2.json", "calibration_ref_seg2.png")}
    # 子进程的输出**引到文件**，不用 `subprocess.PIPE`：PIPE 的缓冲区只有几 KB
    # 且这里没人排空（`_wait_up` 只在子进程**已退出**那条分支读它），服务一旦往
    # stderr 写点大的就会把它写满——`/api/state` 500 的 ASGI traceback 正好够，
    # 写满后子进程卡在「响应头已发、响应体还没写」中间，于是客户端只见响应头、
    # 体一个字节都到不了（实测：体其实有 79 字节，与 content-length 吻合，只是
    # 被堵在管道后面；把 stdout 引到文件、或把管道读干，体立刻就到）。
    # 文件不封顶，且失败时能原样端上来。
    log = root / "server.log"
    with open(log, "w", encoding="utf-8", errors="replace") as lf:
        proc = subprocess.Popen([sys.executable, "-c", code], cwd=root, env=env,
                                stdout=lf, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
    # 父进程这份句柄在上面那个 with 退出时就关掉了：子进程已拿到自己的句柄，
    # 而 Windows 上父进程留着它会挡住 pytest 的 tmp 目录清理。
    try:
        _wait_up(base, proc, log)
        yield {"base": base, "root": root, "video": str(root / "videos" / f"{NAME}.mp4")}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    # 红线守卫：仓库根那 5 个无 git 副本的档（+ ui_prefs.json）必须原样——
    # 读的是字节，不是写
    for f, b in before.items():
        assert _untouched(ROOT / f) == b, (
            f"e2e 动了仓库根的 {f}（红线 5 档 calibration*/tags.json 一个 git 副本"
            "都没有）——隔离没兜住，停下查")


def test_edit_bay_e2e(bay):
    base = bay["base"]
    st = json.loads(urllib.request.urlopen(f"{base}/api/state", timeout=5).read())
    assert st["loaded"] is False, "夹具应是「还没开会话」的初始态"

    env = dict(os.environ, BBH_BASE=base, BBH_VIDEO=bay["video"],
               BBH_PLAYWRIGHT=PW.as_uri(), PYTHONIOENCODING="utf-8")
    r = subprocess.run([NODE, str(SPEC)], env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    assert r.returncode == 0, f"e2e 失败：\n{r.stdout}\n{r.stderr}"
    assert "✅ 全过" in r.stdout, f"e2e 没跑到收尾汇总：\n{r.stdout}\n{r.stderr}"
    m = re.search(r"✅ 全过 (\d+) 项", r.stdout)
    assert m, f"spec 没跑到收尾汇总（stdout 尾）：\n{r.stdout[-800:]}\n{r.stderr[-500:]}"
    assert int(m.group(1)) == EXPECTED_ITEMS, (
        f"spec 项数变了：实际 {m.group(1)} 项 ≠ 钉死 {EXPECTED_ITEMS} 项"
        "——若本任务确实改了 spec 条数，请同任务改 EXPECTED_ITEMS")

    # 写盘真的落在临时目录（而不是哪儿都没落）
    work = bay["root"] / "work" / NAME
    assert (work / "selection.json").exists(), "勾选的副作用没落盘——e2e 没验到真写"
