"""主窗口。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from realtalk import __version__
from realtalk.config import Settings
from realtalk.ui.conversation_page import ConversationPage
from realtalk.ui.english_subtitles import EnglishSubtitlesDialog
from realtalk.ui.theme import TEXT_MUTED


class MainWindow(QWidget):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self._settings = settings
        self._normal_geometry = None
        self._english_subtitles: EnglishSubtitlesDialog | None = None

        self.setWindowTitle(f"RealTalk_AI  ·  实时对话翻译  v{__version__}")
        self.resize(960, 760)
        self.setMinimumSize(760, 600)

        self._page = ConversationPage(settings)
        self._page.opacityChanged.connect(
            lambda percent: self.setWindowOpacity(percent / 100)
        )
        self._page.subtitleModeChanged.connect(self._set_subtitle_mode)
        self._page.englishSubtitlesRequested.connect(self._open_english_subtitles)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._page, stretch=1)
        self._footer = self._build_footer()
        layout.addWidget(self._footer)

    def _open_english_subtitles(self) -> None:
        if self._english_subtitles is not None:
            self._english_subtitles.raise_()
            self._english_subtitles.activateWindow()
            return
        prepare = self._page.pause_for_english_subtitles()
        dialog = EnglishSubtitlesDialog(self._settings, self, prepare=prepare)
        self._english_subtitles = dialog
        dialog.finished.connect(self._english_subtitles_closed)
        # 原主窗口收起，只留下不抢 Teams 焦点的字幕条。
        self.hide()
        dialog.show()

    def _english_subtitles_closed(self, _result: int) -> None:
        dialog, self._english_subtitles = self._english_subtitles, None
        if dialog is not None:
            dialog.deleteLater()
        self.show()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._english_subtitles is not None:
            event.ignore()
            self._english_subtitles.reject()
            return
        self._page.shutdown()
        super().closeEvent(event)

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


def build_window(settings: Settings) -> MainWindow:
    window = MainWindow(settings)
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
    return window
