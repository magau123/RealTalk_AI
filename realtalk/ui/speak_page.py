"""方向二的界面：输入中文，翻译成目标语言并朗读。

同样通过 Signal 把后台线程的结果投递到 UI 线程。译文一到就先显示，
不等语音合成完成，避免用户盯着空白界面等。
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from realtalk.config import Settings
from realtalk.core.events import ErrorEvent, SessionState, StateEvent
from realtalk.core.speak import SpeakResult, SpeakSession
from realtalk.languages import TTS_VOICES, language_name, tts_supported_languages
from realtalk.ui.theme import SUCCESS, TEXT_MUTED, TEXT_SECONDARY, WARNING

_MAX_INPUT_CHARS = 2000


class SpeakPage(QWidget):
    _translated = Signal(str)
    _stateChanged = Signal(object)
    _errorOccurred = Signal(object)
    _finished = Signal(object)

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings

        self._session = SpeakSession(
            settings,
            on_translated=self._translated.emit,
            on_state=self._stateChanged.emit,
            on_error=self._errorOccurred.emit,
            on_finished=self._finished.emit,
        )

        self._build_ui()

        self._translated.connect(self._on_translated_ui)
        self._stateChanged.connect(self._on_state_ui)
        self._errorOccurred.connect(self._on_error_ui)
        self._finished.connect(self._on_finished_ui)

    # ---- 界面搭建 ----

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        root.addLayout(self._build_controls())

        root.addWidget(self._section_title("中文原文"))
        self._input = QTextEdit()
        self._input.setPlaceholderText(
            "在这里输入要说的中文内容，例如：你好，请问洗手间在哪里？\n"
            "按 Ctrl+Enter 直接翻译并朗读。"
        )
        self._input.setAcceptRichText(False)
        self._input.textChanged.connect(self._on_input_changed)
        root.addWidget(self._input, stretch=2)

        root.addWidget(self._section_title("译文"))
        self._output = QTextEdit()
        self._output.setReadOnly(True)
        self._output.setPlaceholderText("翻译结果会显示在这里，并同步朗读。")
        root.addWidget(self._output, stretch=2)

        self._status = QLabel("输入中文内容，选择目标语言后点击「翻译并朗读」。")
        self._status.setObjectName("StatusLabel")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        shortcut.activated.connect(self._on_speak_clicked)

    def _build_controls(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        row.addWidget(self._section_title("目标语言"))
        self._language_combo = QComboBox()
        for code in tts_supported_languages():
            self._language_combo.addItem(language_name(code), code)
        self._language_combo.currentIndexChanged.connect(self._reload_voices)
        row.addWidget(self._language_combo)

        row.addWidget(self._section_title("音色"))
        self._voice_combo = QComboBox()
        row.addWidget(self._voice_combo, stretch=1)

        self._speak_button = QPushButton("翻译并朗读")
        self._speak_button.setObjectName("PrimaryButton")
        self._speak_button.clicked.connect(self._on_speak_clicked)
        row.addWidget(self._speak_button)

        self._stop_button = QPushButton("停止")
        self._stop_button.setObjectName("DangerButton")
        self._stop_button.setEnabled(False)
        self._stop_button.clicked.connect(self._on_stop_clicked)
        row.addWidget(self._stop_button)

        self._reload_voices()
        return row

    @staticmethod
    def _section_title(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("SectionTitle")
        return label

    def _reload_voices(self) -> None:
        code = self._language_combo.currentData()
        self._voice_combo.clear()
        for voice in TTS_VOICES.get(code, ()):
            self._voice_combo.addItem(voice.zh_label, voice.voice_id)

    # ---- 交互 ----

    def _on_input_changed(self) -> None:
        text = self._input.toPlainText()
        if len(text) > _MAX_INPUT_CHARS:
            # 截断而不是静默提交超长文本，避免一次请求产生意外费用
            cursor = self._input.textCursor()
            position = cursor.position()
            self._input.blockSignals(True)
            self._input.setPlainText(text[:_MAX_INPUT_CHARS])
            cursor.setPosition(min(position, _MAX_INPUT_CHARS))
            self._input.setTextCursor(cursor)
            self._input.blockSignals(False)
            self._set_status(
                f"单次输入上限为 {_MAX_INPUT_CHARS} 字，超出部分已截断。", WARNING
            )

    def _on_speak_clicked(self) -> None:
        text = self._input.toPlainText().strip()
        if not text:
            self._set_status("请先输入要翻译的中文内容。", WARNING)
            return
        if self._session.is_busy:
            self._set_status("上一次朗读还没结束，请稍候。", WARNING)
            return

        self._output.clear()
        self._speak_button.setEnabled(False)
        self._stop_button.setEnabled(True)

        self._session.speak_async(
            text,
            target_language=self._language_combo.currentData(),
            voice_id=self._voice_combo.currentData(),
        )

    def _on_stop_clicked(self) -> None:
        self._session.stop()
        self._reset_controls()

    def shutdown(self) -> None:
        self._session.stop()

    # ---- 槽函数，全部运行在 UI 线程 ----

    def _on_translated_ui(self, text: str) -> None:
        self._output.setPlainText(text)

    def _on_state_ui(self, event: StateEvent) -> None:
        if event.state is SessionState.RUNNING:
            self._set_status(event.detail, TEXT_SECONDARY)
        elif event.state is SessionState.FAILED:
            self._set_status(f"出错了：{event.detail}", WARNING)
            self._reset_controls()
        elif event.state is SessionState.STOPPED:
            self._set_status(event.detail or "已停止。", TEXT_MUTED)
            self._reset_controls()

    def _on_error_ui(self, event: ErrorEvent) -> None:
        self._set_status(event.message, WARNING)
        self._reset_controls()

    def _on_finished_ui(self, result: SpeakResult) -> None:
        detail = (
            f"已朗读完成（{language_name(result.target_language)}，"
            f"音色 {result.voice}）"
        )
        if result.first_package_delay_ms is not None:
            detail += f"，首包延迟 {result.first_package_delay_ms:.0f} ms"
        self._set_status(detail, SUCCESS)
        self._reset_controls()

    def _reset_controls(self) -> None:
        self._speak_button.setEnabled(True)
        self._stop_button.setEnabled(False)

    def _set_status(self, text: str, color: str) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color};")
