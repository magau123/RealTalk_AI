"""主窗口。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from realtalk import __version__
from realtalk.config import Settings
from realtalk.ui.conversation_page import ConversationPage
from realtalk.ui.theme import TEXT_MUTED


class MainWindow(QWidget):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings
        self._normal_geometry = None

        self.setWindowTitle(f"RealTalk_AI  ·  实时对话翻译  v{__version__}")
        self.resize(960, 760)
        self.setMinimumSize(760, 600)

        self._page = ConversationPage(settings)
        self._page.opacityChanged.connect(
            lambda percent: self.setWindowOpacity(percent / 100)
        )
        self._page.subtitleModeChanged.connect(self._set_subtitle_mode)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._page, stretch=1)
        self._footer = self._build_footer()
        layout.addWidget(self._footer)

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        row = QHBoxLayout(footer)
        row.setContentsMargins(24, 0, 24, 10)

        info = QLabel(
            f"Qwen 实时翻译　·　CosyVoice 语音合成　·　"
            f"API Key {self._settings.masked_api_key}"
        )
        info.setObjectName("HintLabel")
        info.setStyleSheet(f"color: {TEXT_MUTED};")
        row.addWidget(info)
        row.addStretch(1)
        return footer

    def _set_subtitle_mode(self, enabled: bool) -> None:
        if enabled:
            self._normal_geometry = self.geometry()
            self._footer.hide()
            self.setMinimumSize(640, 140)
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
            self.show()
            self.resize(820, 160)
            self.setWindowTitle("RealTalk · 字幕")
            return

        self._footer.show()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        self.setMinimumSize(760, 600)
        self.show()
        if self._normal_geometry is not None:
            self.setGeometry(self._normal_geometry)
        self.setWindowTitle(f"RealTalk_AI  ·  实时对话翻译  v{__version__}")

    def closeEvent(self, event: QCloseEvent) -> None:
        # 必须主动收尾：录音流和 WebSocket 都在守护线程里，
        # 直接退出可能留下没关闭的设备句柄
        self._page.shutdown()
        super().closeEvent(event)


def build_window(settings: Settings) -> MainWindow:
    window = MainWindow(settings)
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    return window
