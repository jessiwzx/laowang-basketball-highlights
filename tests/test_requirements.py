"""打包内核：运行时依赖清单（requirements.txt）。

这份清单是「装什么才算能跑」的唯一真相，也是打包的输入。三条铁律：

1. **不含 gradio**：打包版没有它（A4 用 `bbh/_gr_compat.py` 顶替），而它会
   拖进 pandas / scipy / sklearn 与整套前端构建产物，体积是内核自身的几倍。
2. **每条都钉 `==`，且钉的就是本机装着的版本**：浮动的 `>=` 意味着「用户装
   到的版本」与「我测过的版本」可以不同；打包更是把**那一刻**的版本冻进 exe，
   不钉就等于把一个不可复现的环境交给用户。钉一个没测过的号同样是假的确定
   性，所以这条同时核对 venv 实况。
3. **清单必须盖住 `bbh/` 真的 import 的每个第三方模块**：这是 2026-09-26
   实测踩到的坑——`bbh/shooter.py` 直接 import PIL，而 Pillow 只是 gradio 的
   传递依赖，**清单里没有它**。gradio 一撤，Pillow 就从 import 图上掉下去，
   打包版会在 import 期就死（不是运行期），而当时没有一条测试会红。pydantic
   与 starlette 同理（靠 fastapi 带进来，但源码是**直接** import 的）。
"""
import ast
import os
import re
import sys
from importlib.metadata import packages_distributions, version

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIREMENTS = os.path.join(REPO_ROOT, "requirements.txt")
STDLIB = set(sys.stdlib_module_names)


def _norm(name):
    """PEP 503 归一化：`opencv-python` / `opencv_python` / `Pillow` 同款比较。"""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _requirement_lines():
    """清单里的**有效行**（去空行、去整行注释、去行尾注释）。"""
    out = []
    with open(REQUIREMENTS, encoding="utf-8") as f:
        for raw in f:
            line = raw.split("#", 1)[0].strip()
            if line:
                out.append(line)
    return out


def _names():
    """清单里出现过的**全部**包名（不论有没有钉版本、是 `==` 还是 `>=`）。

    必须容得下 `gradio>=5.0,<6` 这种写法——否则「不许有 gradio」这条会
    因为解析器只认 `==` 而**空过**（实测踩到：改前它对着 gradio 行判绿）。
    """
    return {_norm(re.split(r"[^A-Za-z0-9._-]", line, 1)[0])
            for line in _requirement_lines()}


def _pinned():
    """`{归一化包名: 版本}`，只收写法正确的 `名字==版本` 行。"""
    got = {}
    for line in _requirement_lines():
        m = re.fullmatch(r"([A-Za-z0-9._-]+)\s*(?:\[[^\]]*\])?\s*==\s*(\S+)", line)
        if m:
            got[_norm(m.group(1))] = m.group(2)
    return got


def _third_party_imports(root):
    """`{顶层模块名: [用到它的文件…]}`，只收第三方（排除标准库与包内相对导入）。"""
    found = {}
    for dirpath, dirnames, filenames in os.walk(os.path.join(REPO_ROOT, root)):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    tops = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    tops = [(node.module or "").split(".")[0]]
                else:
                    continue
                for top in tops:
                    if top and top not in STDLIB and top != "bbh":
                        found.setdefault(top, []).append(
                            os.path.relpath(path, REPO_ROOT))
    return found


def test_requirements_has_no_gradio():
    """gradio 不许出现在清单里（注释里解释它为什么不在这里，不算）。

    撤掉它之后 `import bbh.app` 由 `bbh/_gr_compat.py` 顶着；真正的验证是
    `tests/test_gr_compat.py::test_bbh_app_imports_and_the_api_starts_with_gradio_blocked`
    （子进程里屏蔽 gradio 再起服务）。这条只管清单。
    """
    names = _names()
    assert "gradio" not in names, f"清单里还有 gradio：{sorted(names)}"


def test_every_requirement_is_pinned_to_the_installed_version():
    """每条都钉 `==`，且钉的号**就是 venv 里装着的号**。

    只查「有没有 `==`」会漏掉「钉了个没测过的版本」——那和浮动一样是假确定性。
    venv 是唯一跑过测试的环境，所以它就是这个号该取的值。
    """
    lines = _requirement_lines()
    not_pinned = [ln for ln in lines if "==" not in ln]
    assert not_pinned == [], f"这些没钉版本：{not_pinned}"
    ranges = [ln for ln in lines if re.search(r"[<>~]", ln)]
    assert ranges == [], f"这些是范围而不是钉死：{ranges}"

    drift = []
    for name, want in sorted(_pinned().items()):
        try:
            have = version(name)
        except Exception as exc:                    # 没装 / 名字写错
            drift.append(f"{name}：清单钉 {want}，venv 里查不到（{exc}）")
            continue
        if have != want:
            drift.append(f"{name}：清单钉 {want}，venv 装着 {have}")
    assert drift == [], "清单与 venv 不一致（改完重跑测试）：" + "；".join(drift)


def test_bbh_does_not_import_gradio():
    """源码里不许再有 `import gradio`（清单去掉它只是形式）。"""
    hits = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(REPO_ROOT, "bbh")):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            with open(path, encoding="utf-8") as f:
                for i, line in enumerate(f, 1):
                    if re.match(r"\s*(import|from)\s+gradio\b", line):
                        hits.append(f"{os.path.relpath(path, REPO_ROOT)}:{i}")
    assert hits == [], f"这些地方还在 import gradio：{hits}"


def test_requirements_covers_every_third_party_import_in_bbh():
    """`bbh/` 直接 import 的每个第三方模块，清单里都得有对应的钉死条目。

    这条是上面那个坑的长效牙：`bbh/shooter.py` import PIL，而 Pillow 只靠
    gradio 带进来。撤 gradio 那一刻 Pillow 就从 import 图上掉了，打包版会在
    import 期就死，而当时没有一条测试会红。
    """
    pinned = set(_pinned())
    by_module = packages_distributions()
    missing, unknown = [], []
    for module, files in sorted(_third_party_imports("bbh").items()):
        all_dists = by_module.get(module, [])
        if any(_norm(d) in pinned for d in all_dists):
            continue
        if all_dists:
            missing.append(f"{module}（{files[0]}）→ 应由 {all_dists} 提供")
        else:
            unknown.append(f"{module}（{files[0]}）")
    assert unknown == [], f"这些模块查不到对应的发行包（装了吗？）：{unknown}"
    assert missing == [], "这些第三方依赖没钉进 requirements.txt：" + "；".join(missing)
