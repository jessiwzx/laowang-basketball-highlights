"""新前端入口：python -m bbh.web（run_app.bat 调它）。

必须先 chdir 到**工作区根**：bbh/app.py 的 _work_dir_for 返回相对路径
"work/<视频名>"，从别处启动会把 sidecar 写到别的目录里去。

工作区根分两种，**只有冻结时才搬**：
- 冻结（打包版）⇒ `<exe 同级>/data/`：exe 可能被解压到只读的 Program Files 或
  在 U 盘上，工作区必须紧挨着 exe、且不能污染 exe 目录本身。
- 非冻结（开发）⇒ 仓库根，即今天的行为逐字不变。⛔ 别改成无条件搬：
  run_app.bat 走的正是本入口，搬了之后开发者下次开界面看到的是空工作区
  （`work/` 与 tags.json 等只是换了地方，但看起来像全丢了）。

回退：把 run_app.bat 那一行改回 -m bbh.app，旧 Gradio 界面照常可用。
"""
import os
import sys
import threading
import time
import webbrowser

import uvicorn

from ..editor import _ffmpeg_exe
from .single_instance import HOST, find_free_port, probe, read_runtime, try_acquire, write_runtime


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resolve_root() -> str:
    """冻结时取 exe 所在目录，否则取仓库根。

    冻结态⛔不能用 __file__：那是 PyInstaller 每次启动新建的临时解包目录
    （`_MEIxxxx`），进程一退就没了——写在那里的成绩下一分钟就不见。
    """
    if _is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def data_dir() -> str:
    """打包版的工作区根（紧挨 exe）。"""
    return os.path.join(resolve_root(), "data")


def prepare_cwd() -> str:
    """切到工作区根并返回它（冻结时顺带建出 data/）。"""
    if _is_frozen():
        cwd = data_dir()
        os.makedirs(cwd, exist_ok=True)
    else:
        cwd = resolve_root()
    os.chdir(cwd)
    return cwd


# 模块级只算路径、不 chdir/makedirs：import 它的人（测试、构建脚本）不该被改 cwd。
ROOT = resolve_root()


def _url(port: int) -> str:
    return f"http://127.0.0.1:{port}/edit.html"


#: 判定「这个目录在被同步」的依据：云盘客户端会在环境里留一个根路径
_SYNC_ENV_VARS = ("OneDrive", "OneDriveCommercial", "OneDriveConsumer")


def sync_dir_warning(path: str, env=None) -> str | None:
    """工作区落在云盘同步目录里 ⇒ 返回一句中文警告；否则 `None`。

    `work/` 动辄几个 GB，同步盘会把它们**持续上传**——占满上行带宽、占满云盘
    配额，而用户完全不知道发生了什么。Win11 默认还会把「桌面」重定向进
    OneDrive，也就是说「桌面」对很多用户**就是** OneDrive，而「解压到桌面」是
    最自然的动作。

    **只警告不拦启动**：误判时拦启动＝程序打不开，比同步这件麻烦事严重得多。
    ⛔ 文案同样不许有 emoji（见 `banner`）。
    """
    env = os.environ if env is None else env
    target = os.path.normcase(os.path.abspath(path))
    for name in _SYNC_ENV_VARS:
        root = env.get(name)
        if not root:
            continue
        root = os.path.normcase(os.path.abspath(root))
        if target == root or target.startswith(root + os.sep):
            return (
                "注意：程序的工作目录在同步盘里，剪辑成果会被持续上传，"
                "几个 GB 的文件会占满云盘和网速。建议把整个文件夹挪到 D 盘"
                "（例如 D:\\老王篮球集锦）。"
            )
    return None


def banner(port: int, data_dir: str, ffmpeg_exe: str) -> str:
    """启动时打在黑窗口里的**三行证据**。

    正常启动几乎没有日志（`log_level="warning"`），黑窗口是打包版唯一的可见面。
    出问题时要问的第一批问题就是这三样：「地址是哪个」「东西存哪去了」「用的是
    哪个编码器」——用户答不上来，就只能靠猜。

    ⛔ **不许放 emoji**：stdout 被重定向时 Python 用系统编码（中文 Windows 是
    GBK），emoji 编不出来 ⇒ `UnicodeEncodeError` ⇒ 双击闪退，而这个症状与
    「缺 VC++ 运行库」互相冒充。牙在
    tests/test_web_entry.py::test_banner_is_plain_chinese_without_emoji。
    """
    return (
        "老王篮球集锦\n"
        f"  打开地址：{_url(port)}\n"
        f"  数据目录：{data_dir}\n"
        f"  编码程序：{ffmpeg_exe}\n"
    )


def open_browser_later(url: str, delay: float = 1.0) -> threading.Thread:
    """过一小会儿自动打开浏览器，返回那个线程。

    必须**先**挂线程、再调 `uvicorn.run`：后者是阻塞的，写在它后面那行永远
    轮不到（服务不退出就一直不执行）。延迟是为了等端口真的开始监听——抢在
    前面打开会看到「无法访问」，用户的第一印象就坏了。daemon 线程：进程退出
    时不该被这个线程吊住。
    """
    def _open() -> None:
        time.sleep(delay)
        webbrowser.open(url)

    th = threading.Thread(target=_open, name="open-browser", daemon=True)
    th.start()
    return th


def _hand_off(workspace: str) -> int | None:
    """已经有一个实例在跑：把用户带到那个窗口去，**一个服务都不许再起**。

    提示文件里的端口是提示不是真相（进程被强杀会留下过期端口）⇒ 必须 `probe`
    确认真有人听着，否则不能把用户引到一个死地址上。返回可信端口，没有则 `None`。
    """
    port = read_runtime(workspace) if _is_frozen() else None
    if port and probe(port):
        print(f"程序已经开着 —— {_url(port)}")
        return port
    print("程序已经开着（在另一个窗口里）。")
    return None


def main() -> None:
    workspace = prepare_cwd()
    if not _is_frozen() and ROOT not in sys.path:
        sys.path.insert(0, ROOT)

    if try_acquire(workspace) is None:
        port = _hand_off(workspace)
        if port:
            # ⛔ 这里**同步**开，不许走 open_browser_later：那是个 daemon 线程，
            # 而这条分支紧接着就要 return、主线程结束 ⇒ 解释器 shutdown 会把
            # 它半路掐掉，浏览器开不开全看谁抢到那几微秒（实测：`open` 零耗时
            # 时 30 次丢 3 次；耗时 50ms 时 30 次全丢）。用户看到的是「双击、
            # 黑窗一闪、什么都没发生」——而判据 5 与「第二次双击」的承诺就是
            # 这一下。服务器本来就在跑（`probe` 刚连上），没有任何要等的东西。
            webbrowser.open(_url(port))
        return

    port = find_free_port()
    if _is_frozen():
        write_runtime(workspace, port)

    from .app import create_app
    from ..app import migrate_root_calibrations

    for note in migrate_root_calibrations():   # 与旧入口 __main__ 同款
        print(note)
    note = sync_dir_warning(workspace)
    if note:
        print(note)
    print(banner(port, workspace, _ffmpeg_exe()))
    open_browser_later(_url(port))
    # host 用 HOST（= single_instance.HOST = 127.0.0.1）**铁律**：本程序没有任何
    # 登录/鉴权，绑到对外地址等于把用户的视频与集锦交给同一个 Wi-Fi 下的任何人。
    # 见 tests/test_web_entry.py::test_no_source_file_opens_the_door_to_the_network。
    uvicorn.run(create_app(), host=HOST, port=port, log_level="warning")


if __name__ == "__main__":
    main()
