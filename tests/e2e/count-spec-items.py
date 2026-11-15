# 取三页 spec 的真实项数：复刻夹具隔离（临时目录 + 空闲端口 + cwd=临时目录），
# stdout/stderr 尾部都打（spec 的 ok 抛错走 stderr，只打 stdout 失败时是空白+rc=1）。
# 绝不指向 7860 / 7861。
# 用法：python count-spec-items.py [--spec bay|wall|calib]（缺省 bay = 旧版行为不变）。
# 夹具组合按 spec 选（R15b③，复用各 wrapper 的 helper，不另写第二份）：
#   bay   = _fixture（12 行 events.json + calibration.json + tags.json，无真视频）
#   wall  = _fixture + _wall_fixture（shooters.json / traces / shots / thumbs）
#   calib = _make_video 真视频，**不预置 calibration.json**（否则「生成标定」空转）
# 文件名不匹配 test_*.py ⇒ pytest 不收集，全量数不受影响。
import argparse
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))            # R15b①：夹具模块要 from bbh.models import …
sys.path.insert(0, str(ROOT / "tests" / "e2e"))  # R15b①：wall/calib wrapper 顶层
                                         # `from test_edit_bay_e2e import …` 的互 import


def _load(module_name):
    spec = importlib.util.spec_from_file_location(
        module_name, ROOT / "tests" / "e2e" / f"{module_name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _build_fixture(which, root, mods):
    """按 spec 组夹具——三套 wrapper helper 的组合，别在别处写第二份。"""
    if which == "bay":
        mods["bay"]._fixture(root)
    elif which == "wall":
        wall = mods["wall"]
        wall._fixture(root)          # 墙夹具的地基 = 剪辑台 12 行
        wall._wall_fixture(root)     # shooters.json / traces / shots / thumbs
    elif which == "calib":
        calib = mods["calib"]
        calib._make_video(root / "videos" / f"{mods['calib'].NAME}.mp4")


def main():
    ap = argparse.ArgumentParser(description="取一份 e2e spec 的真实项数")
    ap.add_argument("--spec", choices=("bay", "wall", "calib"), default="bay")
    args = ap.parse_args()
    which = args.spec

    mods = {"bay": _load("test_edit_bay_e2e"),
            "wall": _load("test_wall_e2e"),
            "calib": _load("test_calib_e2e")}
    mod = mods[which]                     # SPEC / NODE / PW / _wait_up 都取自它
    # wall/calib wrapper 顶层的 `from test_edit_bay_e2e import …` 走 sys.path 正常
    # 导入（与 _load 的副本是两个模块对象——helper 是纯函数，双份执行无副作用）。

    tmp = Path(tempfile.mkdtemp(prefix=f"count_items_{which}_"))
    _build_fixture(which, tmp, mods)
    port = mod._free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    code = ("import uvicorn\n"
            "from bbh.web.app import create_app\n"
            f"uvicorn.run(create_app(), host='127.0.0.1', port={port}, "
            "log_level='warning')\n")
    log = tmp / "server.log"
    lf = open(log, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen([sys.executable, "-c", code], cwd=tmp, env=env,
                            stdout=lf, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    lf.close()
    try:
        mod._wait_up(base, proc, log)
        senv = dict(os.environ, BBH_BASE=base,
                    BBH_VIDEO=str(tmp / "videos" / f"{mod.NAME}.mp4"),
                    BBH_PLAYWRIGHT=mod.PW.as_uri(), PYTHONIOENCODING="utf-8")
        r = subprocess.run([mod.NODE, str(mod.SPEC)], env=senv,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300)
        print(f"=== [{which}] returncode:", r.returncode)
        print("=== stdout tail ===")
        print(r.stdout[-1500:])
        print("=== stderr tail ===")
        print(r.stderr[-500:])
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
