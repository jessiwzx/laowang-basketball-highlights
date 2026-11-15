"""跨页 e2e（E1 G3b）：三页联动 + 真 <a> 整页导航。

夹具 = 三套既有 helper 的组合（不写第二份）：剪辑台 12 行（_fixture）+
墙 shooters/traces（_wall_fixture）+ 标定真视频（_make_video 写到 events.json
指名的同一个 videos/<NAME>.mp4）⇒ 三页共享同一后端会话、calib 可真抽帧。

项数门（G1b 同形）：EXPECTED_ITEMS 先以占位数跑一次、按实跑回填（R2）。
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from test_edit_bay_e2e import (NAME, NODE, PW, ROOT, _fixture, _free_port,
                               _untouched, _wait_up)
from test_wall_e2e import _wall_fixture
from test_calib_e2e import _make_video

SPEC = Path(__file__).resolve().parent / "crosspage.spec.mjs"
EXPECTED_ITEMS = 16       # ← 首跑后回填实跑值（R2：建门那一次不算中间态）


@pytest.fixture(scope="module")
def crossbay(tmp_path_factory):
    if not NODE:
        pytest.skip("没有 node——跨页 e2e 需要 node + playwright")
    if not PW.exists():
        pytest.skip(f"没有 {PW}——先 cd frontend && npm i -D --no-save playwright")
    root = tmp_path_factory.mktemp("crosspage")
    _fixture(root)                                                   # 剪辑台 12 行
    _wall_fixture(root)                                              # 墙 shooters/traces
    _make_video(root / "videos" / f"{NAME}.mp4")                     # 标定真视频
    # ⚠ E8 同款坑在这里复现：bay `_fixture` 会预置 work/<NAME>/calibration.json，
    #   而它会让流②的「未标定」断言恒红、流⑤的 calibration.json 落盘断言空转
    #   （夹具自己写的那份 = 断言永真）。删掉它——行的来源是 events.json，不是标定档。
    (root / "work" / NAME / "calibration.json").unlink(missing_ok=True)
    # ⚠ 删除后若首跑发现 edit 载不出 12 行（行依赖标定档的假设被证伪），**停下报告**，
    #   别悄悄把预置加回去——那等于把流⑤的牙拔了换绿。
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    code = ("import uvicorn\n"
            "from bbh.web.app import create_app\n"
            f"uvicorn.run(create_app(), host='127.0.0.1', port={port}, "
            "log_level='warning')\n")
    before = {f: _untouched(ROOT / f) for f in (
        "tags.json", "ui_prefs.json",
        "calibration.json", "calibration_ref.png",
        "calibration_seg2.json", "calibration_ref_seg2.png")}
    log = root / "server.log"
    with open(log, "w", encoding="utf-8", errors="replace") as lf:
        proc = subprocess.Popen([sys.executable, "-c", code], cwd=root, env=env,
                                stdout=lf, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
    try:
        _wait_up(base, proc, log)
        yield {"base": base, "root": root,
               "video": str(root / "videos" / f"{NAME}.mp4")}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    for f, b in before.items():
        assert _untouched(ROOT / f) == b, (
            f"e2e 动了仓库根的 {f}（红线 6 档一个 git 副本都没有）"
            "——隔离没兜住，停下查")


def test_crosspage_e2e(crossbay):
    env = dict(os.environ, BBH_BASE=crossbay["base"], BBH_VIDEO=crossbay["video"],
               BBH_PLAYWRIGHT=PW.as_uri(), PYTHONIOENCODING="utf-8")
    r = subprocess.run([NODE, str(SPEC)], env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    assert r.returncode == 0, f"跨页 e2e 失败：\n{r.stdout}\n{r.stderr}"
    assert "✅ 全过" in r.stdout, f"跨页 e2e 没跑到收尾汇总：\n{r.stdout}\n{r.stderr}"
    m = re.search(r"✅ 全过 (\d+) 项", r.stdout)
    assert m, f"spec 没跑到收尾汇总（stdout 尾）：\n{r.stdout[-800:]}\n{r.stderr[-500:]}"
    assert int(m.group(1)) == EXPECTED_ITEMS, (
        f"spec 项数变了：实际 {m.group(1)} 项 ≠ 钉死 {EXPECTED_ITEMS} 项"
        "——若本任务确实改了 spec 条数，请同任务改 EXPECTED_ITEMS")
    # 流④真落盘：wall 归人写了 manual_groups.json（临时 work 目录，不是仓库根）
    assert (crossbay["root"] / "work" / NAME / "manual_groups.json").exists(), \
        "归人没落盘——流④没验到真写"
    # 流⑤真落盘：生成标定写 calibration.json（同一临时 work 目录 = 「同目录」的夹具形状保证）
    assert (crossbay["root"] / "work" / NAME / "calibration.json").exists(), \
        "标定没落盘——流⑤没验到真写"
