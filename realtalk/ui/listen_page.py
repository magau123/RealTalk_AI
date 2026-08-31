"""方向一的界面：听外语，实时显示原文与中文译文。

线程模型是这个页面最需要留意的地方。ListenSession 的回调来自 dashscope
的接收线程和内部补译线程，Qt 控件只能在 UI 线程上操作。这里用 Signal
做跨线程投递：从任意线程 emit 是安全的，Qt 会把调用排到 UI 线程的事件
循环里执行。所有槽函数因此都跑在 UI 线程上，可以放心碰控件。

同理，session.start() 会阻塞到 WebSocket 建连完成，绝不能在 UI 线程里调，
否则界面会卡住一两秒。所以启停都丢到后台线程。
"""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from realtalk.audio.recorder import list_input_devices
from realtalk.config import Settings
from realtalk.core.events import (
    ErrorEvent,
    SentenceUpdate,
    SessionState,
    StateEvent,
    TranslationSource,
)
from realtalk.core.listen import ListenSession
from realtalk.languages import (
    AUTO,
    GUMMY_ASR_LANGUAGES,
    SOURCES_TRANSLATABLE_TO_ZH,
    language_name,
)
from realtalk.ui.theme import SUCCESS, TEXT_MUTED, TEXT_SECONDARY, WARNING


class SentenceCard(QFrame):
    """一句话的展示卡片：上方原文，下方中文译文。

    中间结果会反复刷新同一张卡片，所以卡片本身要能就地更新。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SentenceCardLive")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(6)

        self._meta = QLabel("识别中 …")
        self._meta.setObjectName("CardMeta")

        self._source = QLabel()
        self._source.setObjectName("SourceText")
        self._source.setWordWrap(True)
        self._source.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self._target = QLabel()
        self._target.setObjectName("TargetText")
        self._target.setWordWrap(True)
        self._target.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._target.hide()

        layout.addWidget(self._meta)
        layout.addWidget(self._source)
        layout.addWidget(self._target)

    def apply(self, update: SentenceUpdate) -> None:
        self._source.setText(update.source_text or "…")

        if update.translated_text:
            self._target.setText(update.translated_text)
            self._target.show()
        else:
            self._target.hide()

        self._meta.setText(self._build_meta(update))

        # 定稿后去掉高亮边框，让用户一眼看出哪句还在变
        self.setObjectName("SentenceCard" if update.is_final else "SentenceCardLive")
        self.style().unpolish(self)
        self.style().polish(self)

    @staticmethod
    def _build_meta(update: SentenceUpdate) -> str:
        parts: list[str] = [f"#{update.sentence_id}"]
        if update.source_language:
            parts.append(language_name(update.source_language))
        if update.translation_source is TranslationSource.GUMMY:
            parts.append("Gummy 内置翻译")
        elif update.translation_source is TranslationSource.QWEN_MT:
            parts.append("文本模型补译")
        parts.append("已定稿" if update.is_final else "识别中 …")
        return "　·　".join(parts)


class ListenPage(QWidget):
    _sentenceReceived = Signal(object)
    _stateChanged = Signal(object)
    _errorOccurred = Signal(object)

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._session: ListenSession | None = None
        self._cards: dict[int, SentenceCard] = {}
        self._transition_lock = threading.Lock()

        self._build_ui()

        self._sentenceReceived.connect(self._on_sentence_ui)
        self._stateChanged.connect(self._on_state_ui)
        self._errorOccurred.connect(self._on_error_ui)

    # ---- 界面搭建 ----

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        root.addLayout(self._build_controls())

        self._status = QLabel("未开始。选择源语种后点击「开始聆听」。")
        self._status.setObjectName("StatusLabel")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        container = QWidget()
        self._cards_layout = QVBoxLayout(container)
        self._cards_layout.setContentsMargins(12, 12, 12, 12)
        self._cards_layout.setSpacing(10)
        self._cards_layout.addStretch(1)
        self._scroll.setWidget(container)
        root.addWidget(self._scroll, stretch=1)

        self._placeholder = QLabel(
            "开始聆听后，识别到的原文与中文译文会实时出现在这里。"
        )
        self._placeholder.setObjectName("HintLabel")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._cards_layout.insertWidget(0, self._placeholder)

    def _build_controls(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        row.addWidget(self._label("源语种"))
        self._source_combo = QComboBox()
        self._source_combo.addItem("自动检测", AUTO)
        for code in sorted(GUMMY_ASR_LANGUAGES):
            suffix = "" if code in SOURCES_TRANSLATABLE_TO_ZH else "（需补译）"
            self._source_combo.addItem(f"{language_name(code)}{suffix}", code)
        self._source_combo.currentIndexChanged.connect(self._refresh_strategy_hint)
        row.addWidget(self._source_combo)

        row.addWidget(self._label("麦克风"))
        self._device_combo = QComboBox()
        self._device_combo.addItem("系统默认", None)
        for device in list_input_devices():
            self._device_combo.addItem(device.name, device.index)
        row.addWidget(self._device_combo, stretch=1)

        self._toggle_button = QPushButton("开始聆听")
        self._toggle_button.setObjectName("PrimaryButton")
        self._toggle_button.clicked.connect(self._on_toggle_clicked)
        row.addWidget(self._toggle_button)

        self._clear_button = QPushButton("清空")
        self._clear_button.clicked.connect(self._clear_cards)
        row.addWidget(self._clear_button)

        return row

    @staticmethod
    def _label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("SectionTitle")
        return label

    # ---- 交互 ----

    def _refresh_strategy_hint(self) -> None:
        if self._session is not None:
            return
        code = self._source_combo.currentData()
        if code == AUTO:
            self._set_status(
                "自动检测源语种。Gummy 无法直译成中文的语种会自动改由文本"
                "翻译模型补译。",
                TEXT_SECONDARY,
            )
        elif code in SOURCES_TRANSLATABLE_TO_ZH:
            self._set_status(
                f"{language_name(code)} → 中文：识别与翻译由 Gummy 一次完成，延迟最低。",
                TEXT_SECONDARY,
            )
        else:
            self._set_status(
                f"Gummy 不支持{language_name(code)}直译中文，将改为「只转写 + "
                f"{self._settings.mt_model} 补译」，译文会比原文稍慢出现。",
                WARNING,
            )

    def _on_toggle_clicked(self) -> None:
        if self._session is None:
            self._start_session()
        else:
            self._stop_session()

    def _start_session(self) -> None:
        self._toggle_button.setEnabled(False)
        self._toggle_button.setText("正在连接 …")
        self._set_status("正在连接语音识别服务 …", TEXT_SECONDARY)

        session = ListenSession(
            self._settings,
            on_sentence=self._sentenceReceived.emit,
            on_state=self._stateChanged.emit,
            on_error=self._errorOccurred.emit,
            source_language=self._source_combo.currentData(),
            target_language="zh",
            device=self._device_combo.currentData(),
        )

        def run() -> None:
            try:
                session.start()
            except Exception as exc:
                self._errorOccurred.emit(ErrorEvent(message=str(exc)))
                self._stateChanged.emit(
                    StateEvent(state=SessionState.FAILED, detail=str(exc))
                )
                return
            self._session = session

        threading.Thread(target=run, name="realtalk-ui-start", daemon=True).start()

    def _stop_session(self) -> None:
        session = self._session
        if session is None:
            return
        self._session = None
        self._toggle_button.setEnabled(False)
        self._toggle_button.setText("正在停止 …")

        def run() -> None:
            session.stop()

        threading.Thread(target=run, name="realtalk-ui-stop", daemon=True).start()

    def shutdown(self) -> None:
        """窗口关闭时调用，确保后台连接与录音被释放。"""
        session = self._session
        self._session = None
        if session is not None:
            session.stop()

    # ---- 槽函数，全部运行在 UI 线程 ----

    def _on_sentence_ui(self, update: SentenceUpdate) -> None:
        self._placeholder.hide()

        card = self._cards.get(update.sentence_id)
        if card is None:
            card = SentenceCard()
            self._cards[update.sentence_id] = card
            # 插到 stretch 之前，保证新卡片出现在底部
            self._cards_layout.insertWidget(self._cards_layout.count() - 1, card)
            at_bottom = True
        else:
            scrollbar = self._scroll.verticalScrollBar()
            at_bottom = scrollbar.value() >= scrollbar.maximum() - 40

        card.apply(update)

        if at_bottom:
            # 控件尺寸要等布局刷新后才确定，直接滚会滚不到底
            self._scroll.verticalScrollBar().setValue(
                self._scroll.verticalScrollBar().maximum()
            )

    def _on_state_ui(self, event: StateEvent) -> None:
        if event.state is SessionState.RUNNING:
            self._toggle_button.setEnabled(True)
            self._toggle_button.setText("停止聆听")
            self._toggle_button.setObjectName("DangerButton")
            self._restyle(self._toggle_button)
            self._source_combo.setEnabled(False)
            self._device_combo.setEnabled(False)
            self._set_status(f"正在聆听 …　{event.detail}", SUCCESS)
        elif event.state in (SessionState.STOPPED, SessionState.IDLE):
            self._reset_controls()
            self._set_status(event.detail or "已停止。", TEXT_MUTED)
        elif event.state is SessionState.FAILED:
            self._reset_controls()
            self._set_status(f"出错了：{event.detail}", WARNING)
        elif event.state is SessionState.CONNECTING:
            self._set_status(f"正在连接 …　{event.detail}", TEXT_SECONDARY)

    def _on_error_ui(self, event: ErrorEvent) -> None:
        self._set_status(event.message, WARNING)
        if not event.recoverable:
            self._reset_controls()

    def _reset_controls(self) -> None:
        self._session = None
        self._toggle_button.setEnabled(True)
        self._toggle_button.setText("开始聆听")
        self._toggle_button.setObjectName("PrimaryButton")
        self._restyle(self._toggle_button)
        self._source_combo.setEnabled(True)
        self._device_combo.setEnabled(True)

    def _restyle(self, widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _set_status(self, text: str, color: str) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color};")

    def _clear_cards(self) -> None:
        for card in self._cards.values():
            self._cards_layout.removeWidget(card)
            card.deleteLater()
        self._cards.clear()
        self._placeholder.show()
