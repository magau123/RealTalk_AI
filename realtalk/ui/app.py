"""桌面应用入口。"""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from realtalk.config import ConfigError, load_settings
from realtalk.ui.theme import STYLESHEET

logger = logging.getLogger(__name__)


def run(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("RealTalk_AI")
    app.setStyleSheet(STYLESHEET)

    try:
        settings = load_settings()
    except ConfigError as exc:
        # 配置问题在图形界面下必须弹窗说明，否则用户只会看到程序闪退
        QMessageBox.critical(None, "RealTalk_AI 配置错误", str(exc))
        return 2

    from realtalk.ui.main_window import build_window

    window = build_window(settings)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
