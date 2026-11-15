"""FastAPI 应用装配：错误映射 + 路由注册 + 静态挂载。

错误契约：失败响应体恒为 {"error": "中文文案"}——不管失败来自哪一层。
bbh/app.py 里抛的 gr.Error（「请先填路径…」这类用户可见报错）翻成 HTTP 400
+ 原文案，前端直接弹出来，与旧界面的提示逐字相同；Starlette 自己抛的
HTTPException（静态目录兜出来的 404、方法不对的 405）英文 detail 换成中文；
其余异常 500，同样带中文前缀，避免前端拿到空 body。

启动顺序陷阱：静态目录挂在 "/"，会兜住所有未匹配路径，所以 API 路由必须
先注册（_register 在 mount 之前调用）。
"""
import os
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import _gr_compat as gr    # 打包版不装 gradio，见 bbh/_gr_compat.py

STATIC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")

# Starlette 默认的 detail 是英文短句（"Not Found"、"Method Not Allowed"），
# 直接透出去前端就弹英文；而业务代码自己抛的 HTTPException(404, "视频不存在")
# 里的中文文案必须原样保留。两者只能靠「是否等于标准 phrase」来区分。
_HTTP_TEXT = {
    400: "请求格式不对",
    403: "没有权限",
    404: "页面或接口不存在",
    405: "请求方法不对",
}


def _http_error_text(exc: StarletteHTTPException) -> str:
    detail = exc.detail
    try:
        is_default = detail == HTTPStatus(exc.status_code).phrase
    except ValueError:            # 非标准状态码没有 phrase 可比
        is_default = False
    if detail and not is_default:
        return detail             # 业务自己写的中文，逐字透传
    return _HTTP_TEXT.get(exc.status_code, f"HTTP {exc.status_code}")


def create_app() -> FastAPI:
    app = FastAPI(title="篮球进球自动集锦", docs_url=None, redoc_url=None)

    @app.exception_handler(gr.Error)
    async def _gr_error(_req: Request, exc: Exception):
        return JSONResponse(status_code=400, content={
            "error": getattr(exc, "message", None) or str(exc)})

    @app.exception_handler(RequestValidationError)
    async def _bad_body(_req: Request, exc: Exception):
        # FastAPI 默认 422 的 body 是 {"detail": [...]}，违反「失败恒为
        # {"error": 中文}」的契约（前端只认 error 字段）——翻成 400 同款。
        # 只带出错位置：errors() 是 Python repr（英文 msg + 类型名），整个塞进
        # 前端 toast 等于把内部实现摊给用户看。
        errs = exc.errors()
        return JSONResponse(status_code=400, content={
            "error": f"请求格式不对：{errs[0].get('loc') if errs else None}"})

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_req: Request, exc: StarletteHTTPException):
        return JSONResponse(status_code=exc.status_code, content={
            "error": _http_error_text(exc)})

    @app.exception_handler(Exception)
    async def _unexpected(_req: Request, exc: Exception):
        return JSONResponse(status_code=500, content={
            "error": f"内部错误：{type(exc).__name__}: {exc}"})

    @app.get("/", include_in_schema=False)
    def _root():
        return RedirectResponse("/edit.html")

    _register(app)
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def _register(app: FastAPI) -> None:
    """各 API 路由的挂载点——后续任务逐个往里加（顺序即注册顺序）。"""
    from . import media

    app.include_router(media.router)

    from . import api_session

    app.include_router(api_session.router)

    from . import api_events

    app.include_router(api_events.router)

    from . import api_marks

    app.include_router(api_marks.router)

    from . import api_wall

    app.include_router(api_wall.router)

    from . import api_calib

    app.include_router(api_calib.router)

    from . import api_gen

    app.include_router(api_gen.router)
