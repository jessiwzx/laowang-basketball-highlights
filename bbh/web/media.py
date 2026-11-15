"""媒体路由：浏览器要拿的每一份文件都从这里出。

- /media/work    work/ 目录内的产物（GIF/缩略图/成品），白名单 = 项目 work/ 内
- /media/video   当前会话的源视频（<video> 直读，Starlette 自带 Range）
- /media/clip    复用 app._cut_preview 的切片预览（帧精确 + 压到 720p）

白名单不是装饰：path 参数来自前端，必须挡住 work/ 之外的任意文件读取
（.. 穿越、绝对路径、UNC）——否则浏览器就能把本机任何文件读走。
"""
import os

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse

from .. import app as A
from . import state

router = APIRouter(prefix="/media")


def _work_root() -> str:
    return os.path.realpath("work")


def _inside(child: str, parent: str) -> bool:
    child = os.path.realpath(child)
    return child == parent or child.startswith(parent + os.sep)


def _require_work_file(path: str) -> str:
    p = os.path.realpath(A.clean_path(path))
    if not _inside(p, _work_root()):
        raise HTTPException(403, "只允许访问 work/ 目录内的文件")
    if not os.path.isfile(p):
        # 只报文件名：错误文案会原样透到浏览器（app.py 的信封），带上服务端
        # 解析后的绝对路径等于把本机目录结构泄给前端。
        raise HTTPException(404, f"文件不存在：{os.path.basename(p)}")
    return p


def _require_current_video(path: str) -> str:
    vp = A.clean_path(path)
    cur = A.clean_path(state.SESSION.video_path)
    if not cur or os.path.realpath(vp) != os.path.realpath(cur):
        raise HTTPException(403, "只允许播放当前载入的视频")
    if not os.path.isfile(vp):
        raise HTTPException(404, f"视频不存在：{os.path.basename(vp)}")
    return vp


@router.get("/work")
def work_file(path: str = Query(...)):
    """work/ 内的产物文件；前端 URL 带 &v=<mtime> 击穿浏览器缓存。"""
    return FileResponse(_require_work_file(path))


@router.get("/video")
def video_file(path: str = Query(...)):
    # FileResponse 自带 Range 支持（starlette 0.52 起）——<video> 拖进度条
    # 靠它。若 test_video_supports_range_requests 没过（206 变 200），说明
    # 这个 starlette 版本要手写 Range：读 Range 头 → 解析 bytes=a-b →
    # Response(206, content=片段, headers={"Content-Range": ...,
    # "Accept-Ranges": "bytes"})。别删那个测试换实现。
    return FileResponse(_require_current_video(path), media_type="video/mp4")


@router.get("/clip")
def clip(path: str = Query(...), t: float = 0.0,
         before: float = 5.0, after: float = 2.0):
    """切片预览：走 app._cut_preview（帧精确重编码，几秒内出）。

    每次调用覆盖同一个 work/<视频名>/preview.mp4——前端 URL 必须带
    &v=<时间戳>，否则浏览器命中缓存不重拉（旧界面同款坑）。"""
    vp = _require_current_video(path)
    # _cut_preview 自己不开目录（旧界面只在表格载入后才切预览，那时
    # work/<视频名>/ 必然已存在；tests/test_app.py 里也是替它垫目录的）。
    # 新前端的进度条在检测之前就能拖——先垫出来，否则 ffmpeg 报
    # "No such file or directory" 变 400。
    os.makedirs(A._work_dir_for(vp), exist_ok=True)
    # 切片**和取内容**都在锁内：_cut_preview 用 -y 写**固定路径**
    # work/<视频名>/preview.mp4，没有 per-request 文件名。只把切片套进锁里
    # 不够——FileResponse 是**出锁之后**才迭代文件，那时另一个请求已经把同一
    # 路径 -y 覆盖了：回包内容与所请求的 t 不符（终审实测 8 条并行请求 7 条
    # 串台），或第二个 ffmpeg 打不开被占用的文件 → 500。旧界面没这个端点
    # （预览由 Gradio 事件队列串行驱动），所以这是本分支新引入的串台。
    with state.video_lock(vp):
        out = A._cut_preview(vp, float(t), float(before), float(after))
        with open(out, "rb") as f:
            data = f.read()
    return Response(content=data, media_type="video/mp4")
