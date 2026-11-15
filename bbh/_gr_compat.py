"""gradio 的最小替身：打包版不装 gradio，但源码还要能 import。

**为什么需要它**：新界面（FastAPI + `bbh/static`）一个字节都不用 gradio，而
gradio 会拖进 pandas / scipy / sklearn 与一整套前端构建产物，体积是内核自身的
几倍。但 `bbh/app.py` 与 `bbh/web/app.py` 里还有 130 多处 `gr.X`，其中 **10 处
是签名注解**——Python 在 `def` 时就会求值注解（除非 `from __future__ import
annotations`），所以直接把 `import gradio` 删掉，`import bbh.app` 会在 **import
期**炸掉，而不是等到运行期。

**设计约束（都不是风格问题）**：

- **点名名单，不用模块级 `__getattr__` 兜底**：没列出的名字必须立刻
  `AttributeError`。兜底会让「有人真去跑旧界面」变成**假装成功**——最难查的
  一类问题。名单的完整性由
  `tests/test_gr_compat.py::test_the_name_list_covers_every_gr_x_in_the_source`
  自动守着：源码里新添一个 `gr.Foo` 就立刻红。
- **`Error` 必须继承 `ValueError`**：真 gradio 的继承链是
  `Error → AppError → ValueError`（实测 5.50.0）。源码里有一批 `except
  ValueError` 包着可能抛 `gr.Error` 的调用，基类不同会让它们**静默**改变行为。
- **`update()` 必须返回普通 dict**：真 gradio 5 的 `gr.update()` 返回的就是
  dict（`{'__type__': 'update', ...}`），`bbh/web/api_gen.py` 正是按 dict 读的。
- 旧界面组件只是**占位**：`build_app()` 不再被调用，`bbh/app.py` 的
  `python -m bbh.app` 那条回退路已作废（设计文档 §5 判据 7 的注）。真有人去调
  `build_app()` 会因缺少 `launch` / `__enter__` 而**报在明处**，不会假装成功。
  原 `tests/test_app.py` 里两条唯一会调 `build_app()` 的用例
  （`test_build_app可构建` / `test_Timer轮询隐藏进度遮罩`）因此**已删除**：它们
  断言的是 `demo.title` / `demo.fns` / `demo.config["dependencies"]` ——
  **真 gradio 的内部结构**，替身没有也不该有。那两条护的是已作废的旧界面接线
  （处理器名单、Timer 的 `show_progress="hidden"`），删掉不减弱打包版的任何
  保障。旧界面代码本身留在 `bbh/app.py` 里（A4 的范围是换 import，不是删旧
  界面）：回退仍只是一次两行 import 的改动。

回退：`bbh/app.py` 与 `bbh/web/app.py` 两行 import 改回 `import gradio as gr`，
再把 `requirements.txt` 的 gradio 加回去即可。
"""


class Error(ValueError):
    """`gr.Error` 的替身：用户可见的中文报错。

    `bbh/web/app.py` 用 `getattr(exc, "message", None) or str(exc)` 取文案 ⇒
    `message` 必须是**原文**（不加引号）。继承 `ValueError` 与真 gradio 一致，
    见模块头。
    """

    def __init__(self, message: str = "", **kwargs):
        super().__init__(message)
        self.message = message


class SelectData:
    """旧界面的点选事件对象。

    不只是注解：源码真读它的字段（`bbh/app.py:175 x, y = evt.index`，
    `:1116`、`:1667` 同款），**测试也拿它当值对象**喂给真处理器
    （`tests/test_app.py::_click` 的 `SelectData(None, {"index": [x, y]})`）。
    构造签名与真 gradio 5 一致（实测 `(self, target, data)`），所以 `index`
    必须是 `data["index"]` 本身，不能是空壳。

    只实现源码真读到的两个字段（`index` / `value`）；真 gradio 还有
    `row_value` / `col_value` / `selected`，本项目没读，不实现。
    """

    def __init__(self, target=None, data=None):
        self.target = target
        self._data = data if isinstance(data, dict) else {}

    @property
    def index(self):
        return self._data.get("index")

    @property
    def value(self):
        return self._data.get("value")


class Request:
    """旧界面的请求对象（`req: gr.Request` 注解用）。

    注解之外源码只读过 `req.request.base_url`（`bbh/app.py:1617`），而那条
    路径属于作废的 `-m bbh.app` 回退，打包版永不执行。
    """


def update(**kwargs) -> dict:
    """`gr.update()` 的替身：返回与真 gradio 5 同形状的普通 dict。"""
    return {"__type__": "update", **kwargs}


class _LegacyComponent:
    """旧界面组件的占位：能构造、能被当类用，仅此而已。"""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


#: 源码 `bbh/**/*.py` 里出现过的全部 `gr.<组件名>`（由上面那条测试守着完整性）
_COMPONENT_NAMES = (
    "Accordion", "Blocks", "Button", "CheckboxGroup", "Column", "Dataframe",
    "Dropdown", "Gallery", "Group", "HTML", "Image", "Markdown", "Number",
    "Radio", "Row", "Slider", "State", "Tab", "Textbox", "Timer", "Video",
)

for _name in _COMPONENT_NAMES:
    globals()[_name] = type(_name, (_LegacyComponent,), {
        "__doc__": f"旧界面组件 {_name} 的占位（新界面不走 gradio）",
    })
del _name


class _Themes:
    """`gr.themes` 的替身：源码里只用过 `themes.Soft`。"""

    Soft = _LegacyComponent


themes = _Themes()
