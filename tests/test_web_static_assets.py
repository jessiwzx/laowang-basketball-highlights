"""入库产物 bbh/static/ 完整性：三页 html 与它们引用的 assets/ 都得真在盘上。

`bbh/static/` 是 `cd frontend && npm run build` 的**入库产物**，pytest 平时
完全碰不到它：忘了 build、或写阶段失败把目录清空，三页与 /assets/* 会全
404 而 pytest 全绿。「浏览器里正文渲染」不在本文件验——那需要浏览器，
留在 frontend/smoke.mjs。
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "bbh" / "static"


def test_pages_reference_existing_non_empty_assets():
    for page in ("edit.html", "wall.html", "calib.html"):
        html = (STATIC / page).read_text("utf-8")
        assert 'id="app"' in html, f"{page} 没有挂载点 id=\"app\""
        assert '<script type="module"' in html, f"{page} 没有 module 入口脚本"
        refs = re.findall(r"assets/[^\"'\s>]+", html)
        assert refs, f"{page} 没有引用任何 assets/ 产物（忘了 npm run build？）"
        for rel in refs:
            p = STATIC / rel
            assert p.is_file(), f"{page} 引用的产物不存在：{rel}"
            assert p.stat().st_size > 0, f"{page} 引用的产物是空文件：{rel}"
