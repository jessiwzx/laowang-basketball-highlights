"""媒体路由白名单：work/ 内放行、越界拒绝、当前视频才可播、切片能出/不串台。"""
import asyncio
import os
import threading

import pytest
from fastapi.testclient import TestClient
from starlette.responses import Response as StarletteResponse

from bbh.web import media, state
from bbh.web.app import create_app

client = TestClient(create_app())


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """把 work/ 与视频都放进 tmp，并把会话视频指过去。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "work" / "vid1").mkdir(parents=True)
    (tmp_path / "work" / "vid1" / "0_12.1.gif").write_bytes(b"GIF89a-test")
    (tmp_path / "outside.gif").write_bytes(b"NOPE")
    video = tmp_path / "vid1.mp4"
    video.write_bytes(b"\x00" * 4096)
    state.SESSION.video_path = str(video)
    yield tmp_path
    state.SESSION.video_path = ""


def test_work_file_inside_work_dir_is_served(env):
    r = client.get("/media/work", params={"path": "work/vid1/0_12.1.gif"})
    assert r.status_code == 200
    assert r.content == b"GIF89a-test"


def test_work_file_outside_work_dir_is_forbidden(env):
    assert client.get("/media/work", params={"path": "outside.gif"}).status_code == 403
    assert client.get("/media/work", params={"path": "../secret.gif"}).status_code == 403
    # 兄弟目录 work2/ 与 work/ 只差一个字符：界内判据少写 os.sep 就会放行
    (env / "work2").mkdir()
    (env / "work2" / "x.gif").write_bytes(b"LEAK")
    assert client.get("/media/work", params={"path": "work2/x.gif"}).status_code == 403
    # 绝对路径（界外）也得挡：白名单不是「只管相对路径」
    assert client.get("/media/work",
                      params={"path": str(env / "outside.gif")}).status_code == 403


def test_work_symlink_inside_pointing_outside_is_forbidden(env):
    # work/ 内的软链接指向界外 → 仍须 403。判据必须是 realpath：只做词法
    # 规范化（normpath）会把链接当成界内路径放行。
    try:
        os.symlink(env / "outside.gif", env / "work" / "link.gif")
    except (OSError, NotImplementedError):
        pytest.skip("本机不允许创建符号链接（需管理员或开发者模式）")
    # ⚠「没抛异常」≠「建成了」（2026-10-23 本机实测）：os.symlink 会**返回成功
    # 但什么也不创建**（exists()/islink() 均为 False）。那种形态下 realpath 不
    # 会跟随，路由按「work 内文件不存在」返回 404 ⇒ 断言 404≠403 变成**假红**
    # （实测：同一台机同一份代码，半小时前抛 OSError 走 skip、之后连跑三次都红
    # ⇒ 该行为本身在抖，多半是 Windows 开发者模式/安全策略这类外因）。
    # 只捕异常拦不住它，必须验对象真的落地；验不了就 skip——**假红比 skip 更糟**，
    # 它会让"真回归"淹没在噪音里，而这条用例本来就提供不了保护。
    if not os.path.islink(env / "work" / "link.gif"):
        pytest.skip("symlink 调用返回成功但没有落地（本机安全策略/未开开发者模式）")
    assert client.get("/media/work",
                      params={"path": "work/link.gif"}).status_code == 403


def test_work_traversal_that_stays_inside_is_normalized(env):
    r = client.get("/media/work", params={"path": "work/vid1/../vid1/0_12.1.gif"})
    assert r.status_code == 200


def test_work_missing_file_is_404(env):
    assert client.get("/media/work",
                      params={"path": "work/vid1/nope.gif"}).status_code == 404


def test_missing_file_message_hides_server_directory(env):
    # 报错文案原样透给浏览器（app.py 的信封）：只报文件名，别泄服务端绝对路径
    r = client.get("/media/work", params={"path": "work/vid1/nope.gif"})
    assert r.status_code == 404
    assert r.json()["error"] == "文件不存在：nope.gif"
    assert os.sep not in r.json()["error"]        # JSON 里 \ 会被转义，别查 text
    ghost = env / "ghost.mp4"
    state.SESSION.video_path = str(ghost)
    r = client.get("/media/video", params={"path": str(ghost)})
    assert r.status_code == 404
    assert r.json()["error"] == "视频不存在：ghost.mp4"
    assert os.sep not in r.json()["error"]


def test_video_only_allows_current_session_video(env):
    ok = client.get("/media/video", params={"path": str(env / "vid1.mp4")})
    assert ok.status_code == 200
    other = env / "vid2.mp4"
    other.write_bytes(b"\x00" * 10)
    assert client.get("/media/video",
                      params={"path": str(other)}).status_code == 403


def test_video_supports_range_requests(env):
    r = client.get("/media/video", params={"path": str(env / "vid1.mp4")},
                   headers={"Range": "bytes=0-99"})
    assert r.status_code == 206
    assert len(r.content) == 100


def test_clip_returns_cut_mp4(sample_video, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "work").mkdir(exist_ok=True)
    state.SESSION.video_path = sample_video
    try:
        r = client.get("/media/clip", params={
            "path": sample_video, "t": 3.0, "before": 1.0, "after": 1.0})
        assert r.status_code == 200, r.text
        assert r.headers["content-type"] == "video/mp4"
    finally:
        state.SESSION.video_path = ""


def _clip_bytes(t: float) -> bytes:
    """每条切片的内容：不同 t → 不同字节**且不同长度**（长度也相同的话，
    「串台」只剩字节差；有长度差时连 Content-Length 都会自相矛盾）。"""
    return f"CLIP-T={t}".encode() + b"x" * int(t * 8)


def _late_reading_file_response(gate: threading.Event):
    """造一个 `FileResponse` 的替身：**读文件推迟到 gate 置位之后**。

    真 `FileResponse` 也是先 send(start)、**出锁之后**才打开文件迭代——替身只是
    把那一段推迟到一个确定的时刻，语义不变。

    ⚠️ 必须继承 starlette 的 `Response`：写成普通类的话 FastAPI 不认它是响应，
    会把它当 JSON 序列化，红的理由就与串台无关了（假红，踩过）。
    """

    class LateReadingResponse(StarletteResponse):
        def __init__(self, path, media_type=None, **kwargs):
            super().__init__(status_code=kwargs.pop("status_code", 200),
                             headers=kwargs.pop("headers", None),
                             media_type=media_type,
                             background=kwargs.pop("background", None))
            self.path = str(path)

        def _read(self) -> bytes:
            gate.wait(10.0)      # 等「两条写都落盘」这个事件，不是拿睡眠凑时序
            with open(self.path, "rb") as f:
                return f.read()

        async def __call__(self, scope, receive, send):
            # 等待放工作线程：不阻塞事件循环（否则另一条请求可能连线程池都派不出去，
            # gate 永不被置位 → 替身读到自己的字节 → 假绿）
            self.body = await asyncio.to_thread(self._read)
            self.headers["content-length"] = str(len(self.body))
            await super().__call__(scope, receive, send)

    return LateReadingResponse


def test_concurrent_clip_requests_do_not_cross_streams(sample_video, tmp_path,
                                                       monkeypatch):
    """两条并发 /media/clip（不同 t）必须各自回包**自己所请求的那个 t**。

    `_cut_preview` 用 -y 写**固定路径** work/<视频名>/preview.mp4（没有
    per-request 文件名），而 FileResponse 是**出锁之后**才迭代文件 ⇒ 后写的
    那条覆盖先写的，先写者的回包内容变成后写者的切片（终审实测 8 条并行请求
    7 条串台）。只把切片套进锁里不够——内容必须在锁内取走。

    这里把这个交错变成**确定的**：先写者写完就停在原地等后写者也写完，再回包。
    三种形态各是什么结果，写清楚免得高估这条用例：

    - **去锁**（删掉 `with state.video_lock(vp):`，回包保持原写法）→ 红。
      先写者被后写者覆盖是构造出来的必然，光靠交错控制就够。
    - **半修**（锁只包切片 + 回包换回 `FileResponse`）→ 红，**但要靠晚读替身**：
      真 `FileResponse` 出锁之后才迭代文件，只有交错控制时「后写者覆盖」与
      「先写者读文件」谁先谁后不确定（复审实测这种形态 5/5 绿）。替身把「读」
      推迟到 `second_written` 置位之后，交叉才必然发生。
    - **固定码**（现状 `Response(content=data…)`）→ 绿，且这行 patch 对它
      **完全惰性**：它根本不构造 `FileResponse`（「假红 = 0」靠的是这个，
      不是靠断言兼容）。

    替身的窄度要如实记账：它只逮 `bbh/web/media.py` 里 `FileResponse` 这个名字，
    换成别的流式响应类型（如 `StreamingResponse`）即失效——那时只会退回「这一半
    无覆盖」，不会假红。
    """
    import bbh.app as A
    monkeypatch.chdir(tmp_path)
    (tmp_path / "work").mkdir(exist_ok=True)
    state.SESSION.video_path = sample_video
    n_written = 0
    with_written = threading.Lock()
    second_written = threading.Event()

    def fake_cut(video_path, t, before, after):
        nonlocal n_written
        out = os.path.join(A._work_dir_for(video_path), "preview.mp4")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "wb") as f:
            f.write(_clip_bytes(t))
        with with_written:
            n_written += 1
            mine = n_written
        if mine == 1:
            second_written.wait(1.0)      # 停在原地，等后写者覆盖同一路径
        else:
            second_written.set()
        return out

    monkeypatch.setattr(A, "_cut_preview", fake_cut)
    # 第三臂：把 media.py 名义下的 FileResponse 换成晚读替身。固定实现不构造它
    # ⇒ 惰性；「半修」那条路径才会走到替身，而替身做的正是真 FileResponse 的
    # 语义（读完在锁外），只是读的时刻由一个事件钉住。
    monkeypatch.setattr(media, "FileResponse",
                        _late_reading_file_response(second_written))
    got = {}

    def go(t):
        got[t] = client.get("/media/clip", params={
            "path": sample_video, "t": t, "before": 1.0, "after": 1.0})

    ts = (1.0, 3.0)
    threads = [threading.Thread(target=go, args=(t,)) for t in ts]
    try:
        for th in threads:
            th.start()
        for th in threads:
            th.join(20)
        assert set(got) == set(ts)                 # 两条都真跑完了
        for t in ts:
            r = got[t]
            assert r.status_code == 200, r.text
            assert r.headers["content-type"] == "video/mp4"
            assert r.content == _clip_bytes(t)
    finally:
        second_written.set()                       # 别把等待留在别的用例里
        state.SESSION.video_path = ""
