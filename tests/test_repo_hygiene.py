"""仓库卫生：跑出来的东西不进历史，打包工程有指针。

两条都是被「打包前的干净检查」逼出来的：构建脚本跑 `git status --porcelain`，
非空就拒绝构建 ⇒ 任何「跑一次程序就冒出来」的未跟踪文件都会把打包卡住。
见 exe 仓库 `build/builder.py` 的 `assert_clean()`。
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _ignored(rel: str) -> bool:
    return subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", rel],
                          capture_output=True).returncode == 0


def test_runtime_artifacts_are_gitignored():
    """`ui_prefs.json`（本机「上次打开哪个视频」）与 `竖屏验收/`（验收产出）
    都是**跑出来的**，不是源码。不忽略 ⇒ 每次开程序都产生一个未跟踪文件 ⇒
    打包的干净检查永远过不去，而那道检查防的是「打进包的东西 != 记录在案的
    那一版」。用户 2026-09-26 裁定：加进忽略清单。"""
    assert _ignored("ui_prefs.json")
    assert _ignored("竖屏验收/")


def test_readme_documents_detector_switch():
    """README 必须写明检球器开关（`BBH_DETECTOR`）——本项目默认 YOLO 检球，
    半年后没人记得怎么切回 HSV 对照，翻箱底的成本比这一行高得多。"""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "BBH_DETECTOR" in text
