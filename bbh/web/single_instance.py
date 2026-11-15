"""打包内核：单实例互斥、端口顺延、端口提示文件。

用户的动作是**双击 exe**，而双击是没有反馈的：他连开两下就会有两个窗口，
两个服务各自绑一个端口、各自 chdir 到同一个工作区，`work/<视频名>/` 互相
覆盖——这是丢成果级别的故障，不是「有点乱」。所以：

- 用一把**以工作区路径为名字**的系统级互斥锁（Windows 命名 Mutex）保证只有
  一个实例在跑。名字由路径派生而不是写死一个常量：同一台机器上两份放在不同
  位置的拷贝各锁各的，谁也不该拦住谁。
- 端口从 7860 起顺延（7860 被别的软件占了就 7861、7862…），而不是失败退出。
- `<工作区>/.runtime.json` 记下「这次用的是哪个端口」，让第二次启动能把用户
  带到已经开着的那个窗口。它是**提示不是真相**：进程被强杀会留下过期端口，
  用之前必须 `probe()` 连一下确认真有人听着。

零新依赖（`ctypes` + `socket` 都是标准库）。
"""
import ctypes
import hashlib
import json
import os
import socket
import sys

#: 提示文件名（工作区根下）
RUNTIME_NAME = ".runtime.json"

#: 只绑本机，见 bbh/web/__main__.py 的 HOST 铁律
HOST = "127.0.0.1"

#: 错开成两份不同位置的拷贝时锁名字要不同，故前缀只做命名空间
_MUTEX_PREFIX = "Local\\bbh-"

_IS_WINDOWS = sys.platform == "win32"

_ERROR_ALREADY_EXISTS = 183

_kernel32 = None


def _k32():
    """懒加载 kernel32。

    用 `use_last_error=True` 而不是裸 `ctypes.windll`：后者的 `GetLastError`
    会被 ctypes 自己的中间调用冲掉，读到的可能不是 CreateMutexW 留下的那个。
    """
    global _kernel32
    if _kernel32 is None:
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        _kernel32.CreateMutexW.restype = ctypes.c_void_p
        _kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    return _kernel32


def mutex_name(workspace: str) -> str:
    """由工作区路径派生锁名（同一目录的不同写法归一成同一把锁）。"""
    digest = hashlib.sha1(os.path.realpath(workspace).encode("utf-8")).hexdigest()[:16]
    return _MUTEX_PREFIX + digest


def try_acquire(workspace: str):
    """抢单实例锁。

    抢到 ⇒ 返回句柄（**必须持有到进程结束**，一松手锁就没了）；
    已被别的进程持有 ⇒ 返回 `None`。
    非 Windows ⇒ 返回一个哨兵对象：本程序只发 Windows 包，但模块不许炸。
    """
    if not _IS_WINDOWS:
        return object()
    k32 = _k32()
    handle = k32.CreateMutexW(None, True, mutex_name(workspace))
    if not handle:
        return None
    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        k32.CloseHandle(handle)
        return None
    return handle


def find_free_port(preferred: int = 7860, tries: int = 11) -> int:
    """从 `preferred` 起找一个真能绑上的端口。

    ⛔ 不设 SO_REUSEADDR：那样「探到空」是假的，真起服务时照样绑不上。
    """
    for port in range(preferred, preferred + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, port))
                return port
            except OSError:
                continue
    raise RuntimeError(
        f"{HOST} 上 {preferred}–{preferred + tries - 1} 全被别的程序占着"
    )


def write_runtime(workspace: str, port: int) -> None:
    """记下这次用的端口。尽力而为：写不进去也不该拦启动。"""
    try:
        os.makedirs(workspace, exist_ok=True)
        with open(os.path.join(workspace, RUNTIME_NAME), "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "port": int(port)}, f)
    except OSError:
        pass


def read_runtime(workspace: str) -> int | None:
    """读上次的端口。缺失 / 坏 JSON / 类型不对 ⇒ `None`（提示文件坏掉不该拦启动）。"""
    try:
        with open(os.path.join(workspace, RUNTIME_NAME), encoding="utf-8") as f:
            data = json.load(f)
        port = int(data["port"])
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        return None
    return port if 1 <= port <= 65535 else None


def probe(port: int, timeout: float = 0.3) -> bool:
    """`127.0.0.1:port` 上真有人听着吗。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((HOST, int(port))) == 0
