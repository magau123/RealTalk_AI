"""RealTalk_AI 启动入口。

    python main.py

命令行工具见 realtalk/cli.py：

    python -m realtalk.cli --help
"""

from __future__ import annotations

import sys

from realtalk.ui import run

if __name__ == "__main__":
    sys.exit(run())
