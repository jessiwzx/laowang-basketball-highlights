"""打包内核：gradio 的最小替身（bbh/_gr_compat.py）。

打包版不装 gradio：它拖进 pandas / scipy / sklearn 与一整套前端构建产物，
体积是内核自身的几倍，而新界面（FastAPI + bbh/static）一个字节都不用 gradio。
但 bbh/app.py 与 bbh/web/app.py 里还有 130 多处 `gr.X`，其中 **10 处是签名
注解——注解在 def 时求值**，所以直接删掉 import 会让 `import bbh.app` 在
import 期就炸（不是运行期）。

替身只提供真的用得到的名字：没列出的名字必须**立刻 AttributeError**，否则
以后有人真去跑旧界面会「假装成功」，那是最难查的一类问题。
"""
import os
import re
import subprocess
import sys

import pytest

from bbh import _gr_compat as gr

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(gr.__file__)))

BLOCK_GRADIO_SCRIPT = r'''
import sys


class _BlockGradio:
    """让 `import gradio` 直接失败，模拟打包版（没有装 gradio 的机器）。"""

    def find_spec(self, name, path=None, target=None):
        if name == "gradio" or name.startswith("gradio."):
            raise ImportError("gradio 被测试屏蔽——打包版没有它")
        return None


sys.meta_path.insert(0, _BlockGradio())

import bbh.app                                    # 130 多处 gr.X + 10 处签名注解
from bbh.web.app import create_app
from fastapi.testclient import TestClient

c = TestClient(create_app())
r = c.get("/")
assert r.status_code == 200, (r.status_code, r.text[:200])
assert "gradio" not in sys.modules, sorted(m for m in sys.modules if "gradio" in m)
print("OK")
'''


def test_update_returns_the_same_plain_dict_gradio_5_returns():
    """gradio 5 的 `gr.update()` 返回的就是**普通 dict**（实测 5.50.0）：

    `gr.update(visible=False, interactive=False)` →
    `{'interactive': False, '__type__': 'update', 'visible': False}`

    新界面 bbh/web/api_gen.py 正是按 dict 读它的（`upd.get("choices")`），
    所以替身必须给出同形状的 dict，而不是什么自定义对象。
    """
    assert gr.update() == {"__type__": "update"}
    assert gr.update(visible=False, interactive=False) == {
        "__type__": "update", "visible": False, "interactive": False}


def test_error_is_a_value_error_like_real_gradio():
    """真 gradio 的 `gr.Error` 继承链是 Error → AppError → **ValueError**（实测）。

    替身必须同样继承 ValueError：源码里有一批 `except ValueError` 包着可能抛
    `gr.Error` 的调用，基类不同会让它们**静默改变行为**（该被吞掉的异常逃出去，
    或者反过来）——这是最不该在拔依赖时踩的坑。
    """
    assert issubclass(gr.Error, ValueError)
    err = gr.Error("请先填路径")
    assert err.message == "请先填路径"


def test_error_can_be_caught_both_ways():
    """`except gr.Error` 与 `except ValueError` 都得接得住（源码里两种都有）。"""
    with pytest.raises(gr.Error):
        raise gr.Error("本视频还没标定")
    with pytest.raises(ValueError):
        raise gr.Error("本视频还没标定")


def test_select_data_carries_the_index_and_value_it_was_built_with():
    """`SelectData` 不只是注解用的空壳——**它是值对象**。

    构造签名与真 gradio 5 一致（实测 `(self, target, data)`），真 gradio 的
    `SelectData(None, {"index": [3, 4]})` 读出来 `.index == [3, 4]`。
    源码真读这两个字段（`bbh/app.py:175 x, y = evt.index`，`:1116`、`:1667`
    同款），测试也拿它当值对象喂给真处理器
    （`tests/test_app.py::_click` 是 `Image.select` 的像素坐标，
    `_row_evt` 是 `Dataframe.select` 的 `[行, 列]`）。
    空壳会让那十几条处理器用例集体 TypeError——正是拔 gradio 时踩到的坑。
    """
    evt = gr.SelectData(None, {"index": [12, 34], "value": None})
    assert evt.index == [12, 34]
    assert evt.value is None
    assert evt.target is None
    # 跨段/表格两处都用同一款：index 是列表还是标量都照原样带出来
    assert gr.SelectData(None, {"index": 7, "value": "x"}).index == 7


def test_unlisted_names_raise_immediately():
    """点名名单的牙：没列出的名字不许悄悄存在。"""
    with pytest.raises(AttributeError):
        gr.NoSuchComponent
    assert gr.Blocks is not None


def test_the_name_list_covers_every_gr_x_in_the_source():
    """替身列出的名字必须覆盖 bbh/ 源码里**真的用到的每一个** `gr.X`。

    这是清单的自我维护：以后谁在 bbh/app.py 里添一个 `gr.Foo`，这条立刻红，
    不必等人肉记得回来同步替身。
    """
    used = set()
    root = os.path.join(REPO_ROOT, "bbh")
    for dirpath, _dirnames, filenames in os.walk(root):
        if "__pycache__" in dirpath:
            continue
        for fn in filenames:
            if not fn.endswith(".py") or fn == "_gr_compat.py":
                continue
            with open(os.path.join(dirpath, fn), encoding="utf-8") as f:
                used |= set(re.findall(r"\bgr\.([A-Za-z_][A-Za-z0-9_]*)", f.read()))
    missing = sorted(n for n in used if not hasattr(gr, n))
    assert missing == [], f"替身缺这些名字：{missing}"


def test_the_app_modules_use_the_shim_and_not_gradio():
    """两个模块里的 `gr` 必须就是兼容层本身（130 多处 `gr.X` 全靠它）。"""
    import bbh.app as A
    import bbh.web.app as W

    assert A.gr is gr
    assert W.gr is gr
    assert A.gr.Error is gr.Error


def test_bbh_app_imports_and_the_api_starts_with_gradio_blocked():
    """子进程里把 gradio 屏蔽掉再 import —— 这是「拔掉 gradio」的**真验证**。

    在装了 gradio 的 venv 里跑不出来：只有屏蔽之后才证明源码真的不再需要它。
    """
    proc = subprocess.run([sys.executable, "-c", BLOCK_GRADIO_SCRIPT],
                          cwd=REPO_ROOT, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, f"stdout={proc.stdout!r}\nstderr={proc.stderr[-2000:]!r}"
    assert "OK" in proc.stdout


# ---- 换 import 之后三条 `except gr.Error` / `raise gr.Error` 的联动 ----
#
# 这两条是**刻画测试**（换 import 前后都该绿）：它们钉的是「换成替身之后，
# 抛与接仍是同一个类对象」。要是哪天有人给两个模块换了不同的 `gr`（或者把
# 某处的 except 改成别的类型），这两条就红。

def test_vertical_failure_still_surfaces_as_the_compat_error(monkeypatch, tmp_path):
    """`bbh/app.py` 竖屏路径故意把裸异常重抛成 `gr.Error`
    （`generate_highlight` 里 `except (RuntimeError, ValueError) as ex:
    raise gr.Error(str(ex)) from ex`）——注释写明理由：裸异常会半途杀死
    生成器、按钮永久卡灰。换成替身后这条重抛必须还在。"""
    import bbh.app as A
    import bbh.vrender as V

    monkeypatch.chdir(tmp_path)          # 生成路径要落 work/v/
    monkeypatch.setattr(V, "build_vertical", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("运镜炸了")))
    rows = [[1, "00:02.5", "high", "0.90", 15, 5, ""]]
    with pytest.raises(gr.Error) as ei:
        A.generate_highlight("v.mp4", rows, vertical=True)
    assert "运镜炸了" in str(ei.value)


def test_gen_selected_catches_the_compat_error_and_still_ends_cleanly(
        monkeypatch, tmp_path):
    """`gen_selected` 里 `except gr.Error` 接住同一类对象后**不 raise**，
    而是走末次 yield 给 ❌ 文案（生成器 raise 后不再有产出，按钮会永久卡灰）。"""
    import bbh.app as A

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(A, "generate_highlight", lambda *a, **k: (_ for _ in ()).throw(
        gr.Error("竖屏失败")))
    frames = list(A.gen_selected("v.mp4", [], [A.GEN_ALL_LABEL]))
    last = frames[-1]
    assert "❌ 没有生成任何视频" in last[4]
    assert "竖屏失败" in last[4]           # 原文带出来给用户看
