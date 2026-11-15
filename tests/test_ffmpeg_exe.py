"""打包内核：编码时用哪一份 ffmpeg。

打包版的意义是「同一份东西在哪台机器上都一样」。PATH 里那份 ffmpeg 是**变量**：
版本不同、编译选项不同，而 `-c:v libx264` 的输出随构建而变（拼接又靠
`-c:v copy` 要求各段编码参数一致，见 bbh/vrender.py）。装了旧 ffmpeg 的机器会
静默换一个二进制去编码，出错时报错来自 ffmpeg 的 stderr、极难归因到「这台机器
上装过 ffmpeg」。所以冻结时必须用包里那份 imageio-ffmpeg 构建——它也正是我们
验证过的那一份。
"""
import sys

import imageio_ffmpeg
import pytest

from bbh import editor

PATH_FFMPEG = r"C:\tools\ffmpeg.exe"
BUNDLED = r"C:\bundled\imageio\ffmpeg.exe"


@pytest.fixture()
def bundled(monkeypatch):
    monkeypatch.setattr(imageio_ffmpeg, "get_ffmpeg_exe", lambda: BUNDLED)


def test_source_run_prefers_the_ffmpeg_on_path(monkeypatch, bundled):
    monkeypatch.setattr(editor.shutil, "which", lambda name: PATH_FFMPEG)
    assert editor._ffmpeg_exe() == PATH_FFMPEG


def test_source_run_falls_back_to_the_bundled_one(monkeypatch, bundled):
    monkeypatch.setattr(editor.shutil, "which", lambda name: None)
    assert editor._ffmpeg_exe() == BUNDLED


def test_frozen_run_ignores_the_ffmpeg_on_path(monkeypatch, bundled):
    """冻结时连问都不问 PATH。"""
    asked = []

    def _which(name):
        asked.append(name)
        return PATH_FFMPEG

    monkeypatch.setattr(editor.shutil, "which", _which)
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert editor._ffmpeg_exe() == BUNDLED
    assert asked == []                  # 关键：PATH 根本没被查过
