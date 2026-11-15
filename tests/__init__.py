# -*- coding: utf-8 -*-
"""测试包。

> 本文件是**本项目新增**的：原「视频剪辑」的 tests/ 目录没有 __init__.py（靠
> 命名空间包 + 仓库根在 sys.path 上解析）。本项目装了 ultralytics，它在
> site-packages 里也带了一个顶层 `tests` **常规包**——命名空间包会输给常规包，
> 导致 `from tests import write_guard` 解析到 ultralytics 的 tests。加一个空
> __init__.py 让本目录成为常规包，在 sys.path 更靠前的仓库根即被命中。
"""
