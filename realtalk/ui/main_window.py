"""主窗口。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QTabWidget, QVBoxLayout, QWidget

from realtalk import __version__
from realtalk.config import Settings
from realtalk.ui.listen_page import ListenPage
from realtalk.ui.speak_page import SpeakPage
from realtalk.ui.theme import TEXT_MUTED


class MainWindow(QWidget):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings

        self.setWindowTitle(f"RealTalk_AI  v{__version__}")
        self.resize(940, 720)
        self.setMinimumSize(720, 560)

        self._listen_page = ListenPage(settings)
        self._speak_page = SpeakPage(settings)

        tabs = QTabWidget()
        tabs.addTab(self._listen_page, "听译　外语 → 中文")
        tabs.addTab(self._speak_page, "说译　中文 → 外语")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(tabs, stretch=1)
        layout.addLayout(self._build_footer())

    def _build_footer(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(20, 6, 20, 10)

        info = QLabel(
            f"识别 {self._settings.asr_model}　·　"
            f"翻译 {self._settings.mt_model}　·　"
            f"合成 {self._settings.tts_model}　·　"
            f"API Key {self._settings.masked_api_key}"
        )
        info.setObjectName("HintLabel")
        info.setStyleSheet(f"color: {TEXT_MUTED};")
        row.addWidget(info)
        row.addStretch(1)
        return row

    def closeEvent(self, event: QCloseEvent) -> None:
        # 必须主动收尾：录音流和 WebSocket 都在守护线程里，
        # 直接退出可能留下没关闭的设备句柄
        self._listen_page.shutdown()
        self._speak_page.shutdown()
        super().closeEvent(event)


def build_window(settings: Settings) -> MainWindow:
    window = MainWindow(settings)
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    return window
