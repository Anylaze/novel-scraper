# -*- coding: utf-8 -*-
"""双击 run.cmd 时的交互入口（源码方式）。

真正的问答逻辑在 tools/launcher.py，和打包后的 exe 共用同一份。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))

from launcher import run_interactive  # noqa: E402

if __name__ == "__main__":
    sys.exit(run_interactive(ROOT, ROOT / "novel_scraper.py"))
