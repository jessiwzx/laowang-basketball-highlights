"""标定页级 e2e（Plan D）：自带真视频夹具 + 真服务 + 真产物。

与剪辑台/墙共用的 `_fixture` **不造真视频**（只写 events.json），而标定页要真抽帧
（GET /api/calib/frame.png?t=）⇒ 本文件自带夹具：cv2.VideoWriter 合成
640×360/30fps/6s 的 mp4（暗底 + 灰筐口横条 + 橙球），**且不预置 calibration.json**
——「生成标定」的落盘断言才有牙（夹具自己写的那份会让断言空转）。

形状经实测（2026-09-21）：以 (430,154)/(600,154)/(515,200) 三点生成标定得
hoop_rect=(422,128,187,75)、hsv_low=(5,183,193)、ROI 命中 11083 像素。

隔离机制同另两份 wrapper：uvicorn 用 cwd=临时目录起 ⇒ work/ 落临时目录，
仓库根 6 档开跑前快照、结束逐个比对。Node 或 playwright 缺失 → skip 并说明。
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from test_edit_bay_e2e import (NAME, NODE, PW, ROOT, _free_port, _untouched,
                               _wait_up)

SPEC = Path(__file__).resolve().parent / "calib.spec.mjs"
W, H, FPS, SECS = 640, 360, 30, 6.0
# 项数门（E1 G1b）：spec 实跑项数钉死在这里。改 spec 条数的任务必须**同任务**改这里——
# 改了 spec 没改钉 = 本文件红（这正是门的目的）。真值来源：count-spec-items.py。
EXPECTED_ITEMS = 82         # 本文件（calib）钉数；bay/wall 各自钉在 test_edit_bay_e2e.py / test_wall_e2e.py


def _make_video(path: Path) -> None:
    """合成一支能真抽帧的视频：暗底 + 灰筐口横条 + 橙球。"""
    import cv2
    import numpy as np

    path.parent.mkdir(parents=True, exist_ok=True)
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (W, H))
    if not vw.isOpened():
        pytest.skip("cv2.VideoWriter 打不开 mp4v —— 本机造不出夹具视频")
    try:
        for _ in range(int(FPS * SECS)):
            f = np.full((H, W, 3), (40, 40, 60), np.uint8)                  # 暗底(BGR)
            cv2.rectangle(f, (430, 150), (600, 158), (200, 200, 200), -1)   # 筐口横条
            cv2.circle(f, (515, 200), 60, (30, 90, 235), -1)                # 橙球
            vw.write(f)
    finally:
        vw.release()


@pytest.fixture(scope="module")
def calibbay(tmp_path_factory):
    if not NODE:
        pytest.skip("没有 node——标定 e2e 需要 node + playwright（见计划「验证配方」）")
    if not PW.exists():
        pytest.skip(f"没有 {PW}——先 cd frontend && npm i -D --no-save playwright")
    root = tmp_path_factory.mktemp("calib")
    video = root / "videos" / f"{NAME}.mp4"
    _make_video(video)
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
        yield {"base": base, "root": root, "video": str(video)}
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


def test_calib_e2e(calibbay):
    env = dict(os.environ, BBH_BASE=calibbay["base"], BBH_VIDEO=calibbay["video"],
               BBH_PLAYWRIGHT=PW.as_uri(), PYTHONIOENCODING="utf-8")
    r = subprocess.run([NODE, str(SPEC)], env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    assert r.returncode == 0, f"标定 e2e 失败：\n{r.stdout}\n{r.stderr}"
    assert "✅ 全过" in r.stdout, f"标定 e2e 没跑到收尾汇总：\n{r.stdout}\n{r.stderr}"
    m = re.search(r"✅ 全过 (\d+) 项", r.stdout)
    assert m, f"spec 没跑到收尾汇总（stdout 尾）：\n{r.stdout[-800:]}\n{r.stderr[-500:]}"
    assert int(m.group(1)) == EXPECTED_ITEMS, (
        f"spec 项数变了：实际 {m.group(1)} 项 ≠ 钉死 {EXPECTED_ITEMS} 项"
        "——若本任务确实改了 spec 条数，请同任务改 EXPECTED_ITEMS")
    # E8 已兑现：Task 5 落地。Task 2 建 wrapper 时故意不写这条（T2–T4 的 spec 走不到
    #   「生成标定」，写了必红、与当时的 `1 passed` 门自相矛盾）；现在 spec 真会点「生成
    #   标定」，这条断言从这一刻起才有牙——夹具不预置 `calibration.json`（见本文件开头
    #   docstring），所以只有 spec 真走了「生成标定」它才会绿。
    # 副作用的真落盘：spec 里真抽了帧、真点了筐口、真生成了标定
    assert (calibbay["root"] / "work" / NAME / "calibration.json").exists(), \
        "标定没落盘——e2e 没验到真写"
    # 主标定两件套真落盘（标定随视频走：落 <临时 root>/work/<视频名>/，**不是仓库根**）
    assert (calibbay["root"] / "work" / NAME / "calibration_ref.png").exists(), \
        "参考帧存档没落盘——标定没走真流程"
