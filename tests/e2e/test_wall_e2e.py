"""出手人墙页级 e2e：真夹具 + 真服务 + 真产物 + 真浏览器（Playwright）。

隔离与红线与 test_edit_bay_e2e.py 同一套：uvicorn cwd=临时目录 ⇒ work/、
tags.json、ui_prefs.json 全落临时目录；仓库根红线 5 档 + ui_prefs.json
开跑前拍快照、结束逐字节核对。墙夹具在剪辑台 12 球之上补 shooters.json
traces + shots/*.jpg（详见 _wall_fixture 的注释）。

Node 或 playwright 缺失 → skip 并说明（与剪辑台 e2e 同一条纪律：不假绿，
也不阻塞没有 Node 的机器）。
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from test_edit_bay_e2e import (NAME, NODE, PW, ROOT, _fixture, _free_port,
                               _untouched, _wait_up)

SPEC = Path(__file__).resolve().parent / "wall.spec.mjs"
# 项数门（E1 G1b）：spec 实跑项数钉死在这里。改 spec 条数的任务必须**同任务**改这里——
# 改了 spec 没改钉 = 本文件红（这正是门的目的）。真值来源：count-spec-items.py。
EXPECTED_ITEMS = 44         # 本文件（wall）钉数；bay/calib 各自钉在 test_edit_bay_e2e.py / test_calib_e2e.py


def _wall_fixture(root: Path) -> None:
    """在剪辑台夹具（12 球 events + 标定 + tags.json）之上补回扫产物。

    12 球全上墙（traces ∩ thumbs 文件全给）；i=3 的球 spot="bad"（⚠ 角标）；
    release_xy 的 x 随 i 递减 ⇒ 「出手点」序与「时间」序正好相反（排序断言
    才有鉴别力）；相邻球间隔 0.4s < 2.5s ⇒ 任意相邻两球归同人必触发护栏。
    thumbs 用假 jpg 字节——墙只认文件存在性，浏览器解码失败不影响任何
    断言（图片 404/解码失败走 network，不是 pageerror）。
    """
    work = root / "work" / NAME
    (work / "shots").mkdir(exist_ok=True)
    traces = {}
    for i in range(12):
        t = 1.2 + i * 0.4                       # 与剪辑台夹具同一时刻序列
        (work / "shots" / f"{i}.jpg").write_bytes(b"\xff\xd8\xff\xe0jpg")
        traces[f"{int(t // 60):02d}:{t % 60:04.1f}"] = {
            "status": ("high", "review", "low")[i % 3],
            "release_t": t, "release_xy": [520.0 - 40.0 * i, 200.0],
            "track_n": 5, "track_span": 1.2,
            "thumbs": [f"shots/{i}.jpg"],
            "spot": "bad" if i == 3 else "ok",
        }
    (work / "shooters.json").write_text(
        json.dumps({"video": str(root / "videos" / f"{NAME}.mp4"),
                    "traces": traces}, ensure_ascii=False), encoding="utf-8")


@pytest.fixture(scope="module")
def wallbay(tmp_path_factory):
    if not NODE:
        pytest.skip("没有 node——出手人墙 e2e 需要 node + playwright（见计划「验证配方」）")
    if not PW.exists():
        pytest.skip(f"没有 {PW}——先 cd frontend && npm i -D --no-save playwright")
    root = tmp_path_factory.mktemp("wall")
    _fixture(root)
    _wall_fixture(root)
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    code = ("import uvicorn\n"
            "from bbh.web.app import create_app\n"
            f"uvicorn.run(create_app(), host='127.0.0.1', port={port}, "
            "log_level='warning')\n")
    # 红线快照与子进程输出引文件的取舍，与 test_edit_bay_e2e.bay 同款——
    # 那边的注释是权威，这里不复制第二份事实。
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
            f"e2e 动了仓库根的 {f}（红线 5 档一个 git 副本都没有）"
            "——隔离没兜住，停下查")


def test_wall_e2e(wallbay):
    env = dict(os.environ, BBH_BASE=wallbay["base"], BBH_VIDEO=wallbay["video"],
               BBH_PLAYWRIGHT=PW.as_uri(), PYTHONIOENCODING="utf-8")
    r = subprocess.run([NODE, str(SPEC)], env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    assert r.returncode == 0, f"墙 e2e 失败：\n{r.stdout}\n{r.stderr}"
    assert "✅ 全过" in r.stdout, f"墙 e2e 没跑到收尾汇总：\n{r.stdout}\n{r.stderr}"
    m = re.search(r"✅ 全过 (\d+) 项", r.stdout)
    assert m, f"spec 没跑到收尾汇总（stdout 尾）：\n{r.stdout[-800:]}\n{r.stderr[-500:]}"
    assert int(m.group(1)) == EXPECTED_ITEMS, (
        f"spec 项数变了：实际 {m.group(1)} 项 ≠ 钉死 {EXPECTED_ITEMS} 项"
        "——若本任务确实改了 spec 条数，请同任务改 EXPECTED_ITEMS")
    # 归人的副作用真落盘（spec 里至少完成一次托盘归人）
    assert (wallbay["root"] / "work" / NAME / "manual_groups.json").exists(), \
        "归人没落盘——e2e 没验到真写"
