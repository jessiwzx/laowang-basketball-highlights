"""打包内核：入口的数据根解析。

打包版把「工作区」从仓库根搬到 exe 同级的 `data/`（exe 可能被解压到只读的
Program Files，也可能在 U 盘上）。判据：**冻结时才搬**——`run_app.bat` 走的
正是同一个入口，若无条件搬，开发者下次开界面会看到空工作区（8.5GB 成果只是
换了地方，但看起来像全丢了）。
"""
import importlib
import os
import sys
import time
import types
from pathlib import Path

import bbh
from bbh.web import __main__ as entry
from bbh.web import single_instance as si

FAKE_FFMPEG = r"C:\bundled\imageio\ffmpeg.exe"


def _freeze(monkeypatch, tmp_path, exe_name="老王篮球集锦.exe"):
    """把进程伪装成冻结态，exe 放在 tmp_path 下。"""
    exe = tmp_path / exe_name
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    return exe


def test_source_run_root_is_the_repo_root():
    """非冻结时根仍由 __file__ 推出——即仓库根（含 bbh/ 包）。"""
    assert os.path.isdir(os.path.join(entry.resolve_root(), "bbh"))
    assert not entry._is_frozen()


def test_frozen_root_is_the_exe_directory(tmp_path, monkeypatch):
    """冻结时根 = exe 所在目录（⛔ 不是 __file__：PyInstaller 下那是临时解包目录）。"""
    _freeze(monkeypatch, tmp_path)
    assert entry._is_frozen()
    assert entry.resolve_root() == str(tmp_path)
    assert entry.data_dir() == os.path.join(str(tmp_path), "data")


def test_frozen_prepare_cwd_creates_data_dir_and_enters_it(tmp_path, monkeypatch):
    """冻结时数据根是 <exe>/data/，且必须存在后才 chdir 进去。"""
    _freeze(monkeypatch, tmp_path)
    monkeypatch.chdir(tmp_path)
    got = entry.prepare_cwd()
    assert got == os.path.join(str(tmp_path), "data")
    assert os.path.isdir(got)
    assert os.getcwd() == got


def test_source_prepare_cwd_does_not_relocate_the_workspace(tmp_path, monkeypatch):
    """非冻结时 chdir 到仓库根，**不**新建 <仓库>/data —— 这是 A1 的裁定。

    无条件搬的后果：run_app.bat 下次启动看到空工作区，用户的成果「消失」。
    """
    root = entry.resolve_root()
    data_dir = os.path.join(root, "data")
    existed = os.path.isdir(data_dir)
    monkeypatch.chdir(tmp_path)
    assert entry.prepare_cwd() == root
    assert os.path.isdir(data_dir) == existed


def test_module_level_root_follows_frozen_state_without_side_effects(tmp_path, monkeypatch):
    """模块级 ROOT 是 resolve_root() 的结果（reload 后随冻结态变），

    且 import 本身**不许** chdir/makedirs —— 测试与构建脚本都会 import 它。
    """
    monkeypatch.chdir(tmp_path)
    _freeze(monkeypatch, tmp_path)
    importlib.reload(entry)
    try:
        assert entry.ROOT == str(tmp_path)
        assert os.getcwd() == str(tmp_path)
    finally:
        importlib.reload(entry)   # 还原，别把冻结态 ROOT 留给后面的测试


def _fake_heavy_apps(monkeypatch):
    """把 main() 里那几处重依赖换成假的。

    本文件验的是**启动决策**，不该为此把 gradio / ffmpeg 拖进来，也不该真跑
    一次标定迁移去动仓库根。
    """
    web_app = types.ModuleType("bbh.web.app")
    web_app.create_app = lambda: object()
    root_app = types.ModuleType("bbh.app")
    root_app.migrate_root_calibrations = lambda: []
    monkeypatch.setitem(sys.modules, "bbh.web.app", web_app)
    monkeypatch.setitem(sys.modules, "bbh.app", root_app)
    # raising=False：本文件早于 A8 就存在，那时 __main__ 还没有这个名字
    monkeypatch.setattr(entry, "_ffmpeg_exe", lambda: FAKE_FFMPEG, raising=False)


def test_first_launch_starts_the_server_and_records_the_port(tmp_path, monkeypatch):
    """第一次启动：起服务，并把这次用的端口写进提示文件。"""
    _fake_heavy_apps(monkeypatch)
    _freeze(monkeypatch, tmp_path)
    workspace = str(tmp_path / "data")
    monkeypatch.setattr(entry, "prepare_cwd", lambda: workspace)
    monkeypatch.setattr(entry, "try_acquire", lambda root: object())
    monkeypatch.setattr(entry, "find_free_port", lambda: 7861)
    started = []
    monkeypatch.setattr(entry.uvicorn, "run", lambda *a, **kw: started.append(kw))

    entry.main()

    assert [kw["port"] for kw in started] == [7861]
    assert si.read_runtime(workspace) == 7861


def test_second_launch_does_not_start_a_second_server(tmp_path, monkeypatch, capsys):
    """第二次启动（锁抢不到）：**一个服务都不许起**，只把用户带到已经开着的那个。

    两个实例各绑一个端口、各写同一个 work/ 是会互相覆盖的丢成果故障。
    """
    _fake_heavy_apps(monkeypatch)
    _freeze(monkeypatch, tmp_path)
    monkeypatch.setattr(entry, "prepare_cwd", lambda: str(tmp_path / "data"))
    monkeypatch.setattr(entry, "try_acquire", lambda root: None)
    monkeypatch.setattr(entry, "read_runtime", lambda root: 7871)
    monkeypatch.setattr(entry, "probe", lambda port, timeout=0.3: True)
    started = []
    monkeypatch.setattr(entry.uvicorn, "run", lambda *a, **kw: started.append(kw))
    opened = []
    # 钉**真的** `webbrowser.open`，不钉 `open_browser_later`：后者是被测对象
    # 本身，钉它等于把「浏览器到底开没开」整条路径排除在断言之外（曾经如此，
    # 于是交接分支的 daemon 竞态在这条测试里完全隐形）。
    monkeypatch.setattr(entry.webbrowser, "open", opened.append)

    entry.main()

    assert started == []
    assert "7871" in capsys.readouterr().out
    # 第二次双击应当把用户**带到已经开着的那个窗口**，而不是让他自己找
    assert opened == ["http://127.0.0.1:7871/edit.html"]


def test_second_launch_opens_the_browser_before_main_returns(tmp_path, monkeypatch):
    """交接分支必须在**返回之前**就把地址交给浏览器。

    反例（本条的由来）：交接分支用 `open_browser_later(..., delay=0.0)` ——
    那是个 **daemon** 线程，而这条分支紧接着就 return、主线程结束 ⇒ 解释器
    shutdown 会把 daemon 线程在半路掐掉，浏览器开不开全看谁抢到那几微秒。
    冻结模拟 + 真第二实例的实测：`webbrowser.open` 零耗时模型下 30 次里丢 3
    次，「开」耗时 50ms 时 30 次全丢。用户看到的是「双击、黑窗一闪、什么都没
    发生」，而判据 5 与「第二次双击」的全部承诺就是这个，且它是**间歇**的
    ——用户只会说「有时候打不开」，极难查。

    上面那条 `test_second_launch_does_not_start_a_second_server` 对这个问题
    **结构性地看不见**：它替换的是 `open_browser_later` 本身（被测对象），
    所以这里必须替换**真的** `webbrowser.open`，并让「开」有耗时。
    服务器本来就在跑（`probe` 刚连上），没有任何要等的东西。
    """
    _fake_heavy_apps(monkeypatch)
    _freeze(monkeypatch, tmp_path)
    monkeypatch.setattr(entry, "prepare_cwd", lambda: str(tmp_path / "data"))
    monkeypatch.setattr(entry, "try_acquire", lambda root: None)
    monkeypatch.setattr(entry, "read_runtime", lambda root: 7871)
    monkeypatch.setattr(entry, "probe", lambda port, timeout=0.3: True)
    monkeypatch.setattr(entry.uvicorn, "run", lambda *a, **kw: None)
    opened = []

    def slow_open(url):
        time.sleep(0.05)          # 真去开浏览器不会是零耗时
        opened.append(url)

    monkeypatch.setattr(entry.webbrowser, "open", slow_open)

    entry.main()

    # main 已经回来了 ⇒ 地址必须**已经**交出去了，不是某个后台线程还在路上
    assert opened == ["http://127.0.0.1:7871/edit.html"]


def test_hand_off_does_not_trust_a_stale_port(tmp_path, monkeypatch, capsys):
    """进程被强杀会留下过期端口——没人听着就不许把用户引到一个死地址上。"""
    _freeze(monkeypatch, tmp_path)
    monkeypatch.setattr(entry, "read_runtime", lambda root: 7871)
    monkeypatch.setattr(entry, "probe", lambda port, timeout=0.3: False)

    assert entry._hand_off(str(tmp_path)) is None      # 没有可信端口就别给地址

    out = capsys.readouterr().out
    assert "7871" not in out
    assert "已经开着" in out


def test_hand_off_returns_the_live_port(tmp_path, monkeypatch, capsys):
    """端口真有人听着 ⇒ 把它交回去，让调用方能把浏览器带过去。"""
    _freeze(monkeypatch, tmp_path)
    monkeypatch.setattr(entry, "read_runtime", lambda root: 7871)
    monkeypatch.setattr(entry, "probe", lambda port, timeout=0.3: True)

    assert entry._hand_off(str(tmp_path)) == 7871
    assert "7871" in capsys.readouterr().out


def test_open_browser_later_opens_the_url_once_in_a_daemon_thread(monkeypatch):
    opened = []
    monkeypatch.setattr(entry.webbrowser, "open", lambda url: opened.append(url))

    th = entry.open_browser_later("http://127.0.0.1:7861/edit.html", delay=0.0)
    th.join(timeout=5)
    assert th.daemon is True                            # 不许把进程吊住
    assert opened == ["http://127.0.0.1:7861/edit.html"]


def test_open_browser_later_returns_before_the_delay_elapses(monkeypatch):
    """必须**立刻**返回：uvicorn.run 是阻塞的，写在它之后那行永远轮不到。"""
    def _slow(url):
        time.sleep(30)

    monkeypatch.setattr(entry.webbrowser, "open", _slow)
    t0 = time.monotonic()
    entry.open_browser_later("http://127.0.0.1:7861/edit.html", delay=5.0)
    assert time.monotonic() - t0 < 0.5


def test_main_opens_the_browser_before_blocking_on_the_server(tmp_path, monkeypatch):
    """顺序就是这条测试的全部意义：先挂上开浏览器的线程，再进阻塞的服务循环。"""
    _fake_heavy_apps(monkeypatch)
    _freeze(monkeypatch, tmp_path)
    monkeypatch.setattr(entry, "prepare_cwd", lambda: str(tmp_path / "data"))
    monkeypatch.setattr(entry, "try_acquire", lambda root: object())
    monkeypatch.setattr(entry, "find_free_port", lambda: 7861)
    order = []
    monkeypatch.setattr(entry, "open_browser_later",
                        lambda url, delay=1.0: order.append(("browser", url)))
    monkeypatch.setattr(entry.uvicorn, "run",
                        lambda *a, **kw: order.append(("serve", kw["port"])))

    entry.main()

    assert order[0] == ("browser", "http://127.0.0.1:7861/edit.html")
    assert order[1] == ("serve", 7861)


def _start_with_fake_server(tmp_path, monkeypatch):
    _fake_heavy_apps(monkeypatch)
    _freeze(monkeypatch, tmp_path)
    monkeypatch.setattr(entry, "prepare_cwd", lambda: str(tmp_path / "data"))
    monkeypatch.setattr(entry, "try_acquire", lambda root: object())
    monkeypatch.setattr(entry, "find_free_port", lambda: 7861)
    monkeypatch.setattr(entry, "open_browser_later", lambda url, delay=1.0: None)
    started = []
    monkeypatch.setattr(entry.uvicorn, "run", lambda *a, **kw: started.append(kw))
    return started


def test_uvicorn_binds_loopback_only(tmp_path, monkeypatch):
    """只绑本机——这是铁律，不是偏好。

    程序**没有任何登录/鉴权**：绑到对外地址等于把用户的视频、标定和集锦成果
    交给同一个 Wi-Fi 下的任何人（路由器另一端就是邻居）。「手机也能看」这种
    需求将来要满足，也得是一次**明确决定**，而不是顺手改一个字。
    """
    started = _start_with_fake_server(tmp_path, monkeypatch)

    entry.main()

    assert entry.HOST == "127.0.0.1"
    assert [kw["host"] for kw in started] == ["127.0.0.1"]


def test_no_source_file_opens_the_door_to_the_network():
    """源码里不许出现这三样——它们都会让同一 Wi-Fi 下的其他人看到用户的视频：

    `0.0.0.0`（绑所有网卡）、`server_name`（同上，Gradio 的写法）、
    `share=True`（Gradio 直接给一个公网链接，比前者更远）。

    这是防改坏的牙：今天一条都没有，谁加上谁红。
    """
    offenders = []
    for path in Path(bbh.__file__).parent.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for bad in ("0.0.0.0", "server_name", "share=True"):
            if bad in text:
                offenders.append(f"{path.name}: {bad}")
    assert offenders == []


def test_banner_is_plain_chinese_without_emoji():
    """banner 必须是 GBK 能编码的纯中文。

    黑窗口是打包版唯一的可见面，而 stdout 被重定向时 Python 用系统编码（中文
    Windows = GBK）。emoji 编不出来 ⇒ `UnicodeEncodeError` ⇒ **双击闪退**，
    而这个症状与「缺 VC++ 运行库」互相冒充，会白耗排查时间。
    """
    text = entry.banner(7861, r"D:\老王篮球集锦\data", FAKE_FFMPEG)
    text.encode("gbk")                      # 编不出来就在这里红
    assert "🏀" not in text


def test_banner_shows_all_three_pieces_of_evidence():
    """启动 banner 要打**三行证据**：实际端口 / 数据目录绝对路径 / 实际 ffmpeg 路径。

    正常启动几乎没有日志（log_level="warning"），用户看不到任何东西。这三样是
    出问题时要问的第一批问题：「地址是哪个」「东西存哪去了」「用的是哪个编码器」。
    """
    text = entry.banner(7861, r"D:\老王篮球集锦\data", FAKE_FFMPEG)
    assert "7861" in text
    assert r"D:\老王篮球集锦\data" in text
    assert FAKE_FFMPEG in text


def test_main_prints_the_banner_it_actually_runs_with(tmp_path, monkeypatch, capsys):
    """打出来的必须是**这次真实用的**端口与 ffmpeg，不是写死的示例值。"""
    started = _start_with_fake_server(tmp_path, monkeypatch)

    entry.main()

    out = capsys.readouterr().out
    assert "7861" in out
    assert str(tmp_path / "data") in out
    assert FAKE_FFMPEG in out
    assert "🏀" not in out
    assert [kw["port"] for kw in started] == [7861]


def test_warns_when_the_workspace_sits_in_a_synced_folder():
    """`work/` 动辄几个 GB，落在同步盘里会被**持续上传**。"""
    note = entry.sync_dir_warning(
        r"C:\Users\x\OneDrive\老王篮球集锦\data",
        env={"OneDrive": r"C:\Users\x\OneDrive"})
    assert note and "同步" in note
    note.encode("gbk")          # 与 banner 同一条铁律：不许有编不出的字符


def test_win11_desktop_redirection_is_caught_too():
    """Win11 默认把「桌面」重定向进 OneDrive ⇒「桌面」对很多用户**就是** OneDrive。

    解压到桌面是最自然的动作，所以这条路径必须命中。
    """
    note = entry.sync_dir_warning(
        r"C:\Users\x\OneDrive\桌面\老王篮球集锦\data",
        env={"OneDrive": r"C:\Users\x\OneDrive"})
    assert note and "同步" in note


def test_says_nothing_when_the_workspace_is_outside_sync_folders():
    assert entry.sync_dir_warning(
        r"D:\老王篮球集锦\data",
        env={"OneDrive": r"C:\Users\x\OneDrive"}) is None


def test_sync_detection_tolerates_case_and_other_drives():
    """同一个目录的大小写写法要命中（normcase），换到别的盘符不许抛异常。"""
    assert entry.sync_dir_warning(
        r"c:\users\x\onedrive\老王\data",
        env={"OneDrive": r"C:\Users\X\OneDrive"}) is not None
    assert entry.sync_dir_warning(r"D:\老王\data", env={"OneDrive": ""}) is None
    assert entry.sync_dir_warning(r"D:\老王\data", env={}) is None


def test_main_warns_about_a_synced_workspace_but_still_starts(tmp_path, monkeypatch,
                                                              capsys):
    """**只警告不拦启动**：误判时拦启动＝程序打不开，比同步这件麻烦事严重得多。"""
    started = _start_with_fake_server(tmp_path, monkeypatch)
    monkeypatch.setenv("OneDrive", str(tmp_path))
    monkeypatch.setenv("OneDriveCommercial", "")

    entry.main()

    out = capsys.readouterr().out
    assert "同步" in out
    assert [kw["port"] for kw in started] == [7861]     # 警告归警告，服务照起
