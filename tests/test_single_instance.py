"""打包内核：单实例互斥、端口顺延、端口提示文件。

双击第二遍那个 exe 时，第二次启动**必须不抢第二个端口、也不新开一个服务**，
而是把已经开着的那个窗口带出来——否则用户会有两个窗口各写各的 work/，
互相覆盖对方的剪辑成果。

锁的名字由**工作区路径**派生（不是全局常量）：同一台机器上两份放在不同位置
的拷贝各锁各的，谁也不该拦住谁。
"""
import os
import socket
import subprocess
import sys
import textwrap
import time

import pytest

from bbh.web import single_instance as si

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(si.__file__))))


def test_find_free_port_skips_a_taken_port():
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        base = taken.getsockname()[1]
        got = si.find_free_port(base)
        assert got != base
        assert base < got <= base + 10
        with socket.socket() as can_bind:
            can_bind.bind(("127.0.0.1", got))      # 探回来的端口真的能用
    assert si.find_free_port(base) == base          # 让出来之后能再拿回去


def test_find_free_port_gives_up_loudly_when_all_taken():
    with socket.socket() as probe_sock:
        probe_sock.bind(("127.0.0.1", 0))
        base = probe_sock.getsockname()[1]
    socks = []
    try:
        for p in range(base, base + 3):
            s = socket.socket()
            s.bind(("127.0.0.1", p))
            s.listen(1)
            socks.append(s)
        with pytest.raises(RuntimeError):
            si.find_free_port(base, tries=3)
    finally:
        for s in socks:
            s.close()


def test_probe_reflects_whether_someone_listens():
    """提示文件是提示、不是真相：端口有没有人真听着，得连一下才算。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        assert si.probe(port) is True
    assert si.probe(port) is False                  # 关掉之后


def test_runtime_roundtrip(tmp_path):
    si.write_runtime(str(tmp_path), 8123)
    assert si.read_runtime(str(tmp_path)) == 8123
    assert (tmp_path / si.RUNTIME_NAME).is_file()


def test_read_runtime_tolerates_missing_and_broken(tmp_path):
    assert si.read_runtime(str(tmp_path)) is None               # 文件不存在
    for text in ("{不是 json", '{"port": "nope"}', "[1, 2]", '{"port": 0}', ""):
        (tmp_path / si.RUNTIME_NAME).write_text(text, encoding="utf-8")
        assert si.read_runtime(str(tmp_path)) is None, text


def test_write_runtime_never_raises(tmp_path):
    """提示文件是尽力而为：写不进去（只读盘、权限）不该拦启动。"""
    blocker = tmp_path / "blocked"
    blocker.write_text("x", encoding="utf-8")       # 拿文件占住路径
    si.write_runtime(str(blocker), 8123)            # 不许抛


def test_mutex_name_is_stable_per_dir_and_differs_across_dirs(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    name = si.mutex_name(str(a))
    assert name.startswith("Local\\")
    assert name == si.mutex_name(str(a))
    assert name != si.mutex_name(str(b))
    # 同一个目录的不同写法必须落到同一把锁上（realpath 归一）
    assert name == si.mutex_name(os.path.join(str(a), "..", "a"))


def test_second_process_cannot_take_the_lock(tmp_path):
    """真起一个子进程持锁（不拿假接口糊弄），验的是真实现。"""
    data = str(tmp_path / "data")
    script = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {REPO_ROOT!r})
        from bbh.web.single_instance import try_acquire
        print("OK" if try_acquire({data!r}) else "BUSY", flush=True)
        time.sleep(60)
    """)
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    try:
        line = proc.stdout.readline().strip()
        assert line == "OK", f"子进程没能持锁：{line!r} / {proc.stderr.read()}"
        assert si.try_acquire(data) is None                 # 第二个进程抢不到
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    deadline = time.monotonic() + 5                         # 内核回收句柄要一点时间
    while time.monotonic() < deadline:
        if si.try_acquire(data) is not None:
            return
        time.sleep(0.05)
    pytest.fail("子进程退出后锁仍未释放")


def test_non_windows_returns_a_sentinel_without_raising(monkeypatch, tmp_path):
    """非 Windows 不追求单实例，但**不许炸**（模块导入与调用都得活着）。"""
    monkeypatch.setattr(si, "_IS_WINDOWS", False)
    assert si.try_acquire(str(tmp_path)) is not None
