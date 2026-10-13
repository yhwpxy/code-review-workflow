# -*- coding: utf-8 -*-
"""项目启动入口：python run.py <需求>

把项目根目录加入 sys.path，并强制 stdout/stderr 使用 UTF-8（避免中文在
Windows 控制台 GBK 代码页下乱码），然后委托给 workflow.main.main。
"""

import os
import sys

# 强制 stdout/stderr 使用 UTF-8，避免中文在 Windows 控制台（GBK 代码页）下乱码。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from workflow.main import main

if __name__ == "__main__":
    sys.exit(main())
