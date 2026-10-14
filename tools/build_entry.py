# -*- coding: utf-8 -*-
"""PyInstaller 的启动脚本：无参数双击 = 交互问答，带参数 = 走命令行。

打包命令见 run_build.cmd。源码方式不用它，直接跑 start.py / novel_scraper.py。
"""
from __future__ import annotations

import sys
from pathlib import Path

import novel_scraper

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))


def _main() -> int:
    if len(sys.argv) > 1:
        return novel_scraper.main()
    from launcher import run_interactive
    return run_interactive(Path(sys.executable).resolve().parent,
                           Path(__file__).resolve().parent / "novel_scraper.py")


if __name__ == "__main__":
    sys.exit(_main())
