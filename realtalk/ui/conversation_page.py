"""对话界面：面对面双语交流的单一视图。

布局像聊天记录：对方的话靠左，我的话靠右。每条气泡上方是原文（小字、
弱化），下方是译文（大字、主色）——因为使用者真正要读的是译文，原文
只用于确认识别是否准确。

半双工由两个大按钮控制。点击其中一个会自动结束另一方的轮次，因此不会
出现两条链路同时开着的情况。

线程处理与其他页面一致：核心层回调来自后台线程，一律通过 Qt Signal
投递到 UI 线程；会阻塞的启停操作放到后台线程执行。
"""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
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
from realtalk.core.conversation import (
    ConversationMessage,
    ConversationSession,
    ConversationState,
    ConversationStateEvent,
    Speaker,
)
from realtalk.core.events import ErrorEvent
from realtalk.languages import (
    TTS_VOICES,
    conversation_languages,
    language_name,
)
from realtalk.ui.theme import SUCCESS, TEXT_MUTED, TEXT_SECONDARY, WARNING


class MessageBubble(QFrame):
    """一条消息的气泡，可随中间结果就地刷新。"""

    replayRequested = Signal(object)

    def __init__(self, message: ConversationMessage, parent: QWidget | None = None):
        super().__init__(parent)
        self._message = message
        self._is_mine = message.speaker is Speaker.ME
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        self.setMaximumWidth(560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(5)

        self._meta = QLabel()
        self._meta.setObjectName("CardMeta")

        self._original = QLabel()
        self._original.setObjectName("SourceText")
        self._original.setWordWrap(True)
        self._original.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        self._translated = QLabel()
        self._translated.setObjectName("TargetText")
        self._translated.setWordWrap(True)
        self._translated.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )

        layout.addWidget(self._meta)
        layout.addWidget(self._original)
        layout.addWidget(self._translated)

        self._replay = QPushButton("重新朗读")
        self._replay.setObjectName("ReplayButton")
        self._replay.setCursor(Qt.CursorShape.PointingHandCursor)
        self._replay.clicked.connect(lambda: self.replayRequested.emit(self._message))
        self._replay.hide()
        layout.addWidget(self._replay, alignment=Qt.AlignmentFlag.AlignRight)

        self.apply(message)

    def apply(self, message: ConversationMessage) -> None:
        self._message = message

        self._original.setText(message.original_text or "…")
        self._translated.setText(message.translated_text)
        self._translated.setVisible(bool(message.translated_text))

        self._meta.setText(self._build_meta(message))
        self.setObjectName(self._object_name(message))
        self.style().unpolish(self)
        self.style().polish(self)

        # 只有我的话才需要重放，且要等它已经念过一遍
        self._replay.setVisible(
            self._is_mine and message.has_spoken and bool(message.translated_text)
        )

    def _object_name(self, message: ConversationMessage) -> str:
        if not self._is_mine:
            return "ForeignBubble" if message.is_final else "ForeignBubbleLive"
        if message.is_speaking:
            return "MyBubbleSpeaking"
        return "MyBubble" if message.is_final else "MyBubbleLive"

    def _build_meta(self, message: ConversationMessage) -> str:
        who = "我" if self._is_mine else "对方"
        parts = [f"{who}　{language_name(message.original_language)}"]
        if message.is_speaking:
            parts.append("正在朗读 …")
        elif not message.is_final:
            parts.append("识别中 …")
        elif message.has_spoken:
            parts.append("已朗读")
        return "　·　".join(parts)


class ConversationPage(QWidget):
    _messageReceived = Signal(object)
    _stateChanged = Signal(object)
    _errorOccurred = Signal(object)

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._session: ConversationSession | None = None
        self._bubbles: dict[str, MessageBubble] = {}

        self._build_ui()

        self._messageReceived.connect(self._on_message_ui)
        self._stateChanged.connect(self._on_state_ui)
        self._errorOccurred.connect(self._on_error_ui)

    # ---- 界面搭建 ----

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 16)
        root.setSpacing(12)

        # 先建控件再装配布局：设置行里的语言下拉框在初始化时就会触发
        # _refresh_turn_buttons，那时轮次按钮必须已经存在。
        self._create_turn_buttons()

        root.addLayout(self._build_settings_row())
        root.addWidget(self._build_transcript(), stretch=1)

        self._status = QLabel("选择对方的语言，然后点击下方按钮开始对话。")
        self._status.setObjectName("StatusLabel")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        root.addLayout(self._build_turn_button_row())

    def _build_settings_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        row.addWidget(self._caption("对方语言"))
        self._language_combo = QComboBox()
        for code in conversation_languages():
            self._language_combo.addItem(language_name(code), code)
        self._language_combo.currentIndexChanged.connect(self._reload_voices)
        row.addWidget(self._language_combo)

        row.addWidget(self._caption("对方听到的音色"))
        self._voice_combo = QComboBox()
        row.addWidget(self._voice_combo, stretch=1)

        row.addWidget(self._caption("麦克风"))
        self._device_combo = QComboBox()
        self._device_combo.addItem("系统默认", None)
        for device in list_input_devices():
            self._device_combo.addItem(device.name, device.index)
        row.addWidget(self._device_combo, stretch=1)

        self._auto_toggle = QCheckBox("自动识别说话人")
        self._auto_toggle.setToolTip(
            "开启后不必手动切换：系统按识别出的文字判断是谁在说。\n"
            "整句只有汉字、不含假名的日文可能被误判为中文，遇到时切回手动。"
        )
        self._auto_toggle.toggled.connect(self._on_auto_toggled)
        row.addWidget(self._auto_toggle)

        self._clear_button = QPushButton("清空")
        self._clear_button.clicked.connect(self._clear_transcript)
        row.addWidget(self._clear_button)

        self._reload_voices()
        return row

    def _build_transcript(self) -> QScrollArea:
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        container = QWidget()
        self._transcript = QVBoxLayout(container)
        self._transcript.setContentsMargins(14, 14, 14, 14)
        self._transcript.setSpacing(10)
        self._transcript.addStretch(1)
        self._scroll.setWidget(container)

        self._placeholder = QLabel(
            "对话内容会显示在这里。\n\n"
            "对方说话时点「对方说」，你回话时点「我说」，\n"
            "同一时刻只有一方的通道开启。"
        )
        self._placeholder.setObjectName("HintLabel")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._transcript.insertWidget(0, self._placeholder)
        return self._scroll

    def _create_turn_buttons(self) -> None:
        self._foreign_button = QPushButton()
        self._foreign_button.setObjectName("TurnButton")
        self._foreign_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._foreign_button.clicked.connect(
            lambda: self._on_turn_clicked(Speaker.FOREIGN)
        )

        self._my_button = QPushButton()
        self._my_button.setObjectName("TurnButton")
        self._my_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._my_button.clicked.connect(lambda: self._on_turn_clicked(Speaker.ME))

        self._auto_button = QPushButton()
        self._auto_button.setObjectName("TurnButton")
        self._auto_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._auto_button.clicked.connect(self._on_auto_clicked)
        self._auto_button.hide()

    def _build_turn_button_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(self._foreign_button, stretch=1)
        row.addWidget(self._my_button, stretch=1)
        row.addWidget(self._auto_button, stretch=1)
        self._refresh_turn_buttons()
        return row

    @staticmethod
    def _caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("SectionTitle")
        return label

    def _reload_voices(self) -> None:
        code = self._language_combo.currentData()
        self._voice_combo.clear()
        for voice in TTS_VOICES.get(code, ()):
            self._voice_combo.addItem(voice.zh_label, voice.voice_id)
        self._refresh_turn_buttons()
        # 语言变了就必须重建会话，因为 Gummy 的翻译方向在建连时已固定
        self._teardown_session()

    # ---- 交互 ----

    def _current_language(self) -> str:
        return self._language_combo.currentData()

    def _on_auto_toggled(self, enabled: bool) -> None:
        # 两种模式的连接参数不同（自动模式源语种为 auto），必须重建会话
        self._teardown_session()
        self._refresh_turn_buttons()
        self._set_status(
            "自动模式：双方直接说话，无需切换。"
            if enabled
            else "手动模式：点击按钮切换当前说话的人。",
            TEXT_MUTED,
        )

    def _on_turn_clicked(self, speaker: Speaker) -> None:
        session = self._session
        if session is not None and session.active_speaker is speaker:
            self._run_async(session.stop)
            return
        self._begin(lambda active: active.start_turn(speaker))

    def _on_auto_clicked(self) -> None:
        session = self._session
        if session is not None and session.is_running:
            self._run_async(session.stop)
            return
        self._begin(lambda active: active.start_auto())

    def _begin(self, action) -> None:  # noqa: ANN001
        """在后台线程建立连接。start_turn / start_auto 会阻塞到握手完成，
        放在 UI 线程会让界面卡住一两秒。"""
        self._set_controls_enabled(False)

        def run() -> None:
            try:
                action(self._ensure_session())
            except Exception as exc:
                self._errorOccurred.emit(ErrorEvent(message=str(exc)))

        threading.Thread(target=run, name="realtalk-turn", daemon=True).start()

    def _ensure_session(self) -> ConversationSession:
        if self._session is not None:
            return self._session
        session = ConversationSession(
            self._settings,
            foreign_language=self._current_language(),
            on_message=self._messageReceived.emit,
            on_state=self._stateChanged.emit,
            on_error=self._errorOccurred.emit,
            input_device=self._device_combo.currentData(),
            voice_id=self._voice_combo.currentData(),
        )
        self._session = session
        return session

    def _teardown_session(self) -> None:
        session = self._session
        self._session = None
        if session is not None:
            self._run_async(session.shutdown)

    @staticmethod
    def _run_async(func) -> None:  # noqa: ANN001
        threading.Thread(target=func, daemon=True).start()

    def shutdown(self) -> None:
        session = self._session
        self._session = None
        if session is not None:
            session.shutdown()

    # ---- 槽函数，全部运行在 UI 线程 ----

    def _on_message_ui(self, message: ConversationMessage) -> None:
        self._placeholder.hide()

        bubble = self._bubbles.get(message.message_id)
        scrollbar = self._scroll.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 60

        if bubble is None:
            bubble = MessageBubble(message)
            bubble.replayRequested.connect(self._on_replay_requested)
            self._bubbles[message.message_id] = bubble

            # 用一层水平布局做左右对齐：对方靠左，我靠右
            wrapper = QWidget()
            wrapper_layout = QHBoxLayout(wrapper)
            wrapper_layout.setContentsMargins(0, 0, 0, 0)
            if message.speaker is Speaker.ME:
                wrapper_layout.addStretch(1)
                wrapper_layout.addWidget(bubble)
            else:
                wrapper_layout.addWidget(bubble)
                wrapper_layout.addStretch(1)

            self._transcript.insertWidget(self._transcript.count() - 1, wrapper)
            at_bottom = True
        else:
            bubble.apply(message)

        if at_bottom:
            scrollbar.setValue(scrollbar.maximum())

    def _on_replay_requested(self, message: ConversationMessage) -> None:
        session = self._session
        if session is not None:
            session.replay(message)

    def _on_state_ui(self, event: ConversationStateEvent) -> None:
        if event.state is ConversationState.CONNECTING:
            self._set_status(event.detail or "正在连接 …", TEXT_SECONDARY)
            return

        self._set_controls_enabled(True)
        self._refresh_turn_buttons()

        running = (
            ConversationState.FOREIGN_TURN,
            ConversationState.MY_TURN,
            ConversationState.AUTO_LISTENING,
        )
        if event.state in running:
            self._set_status(event.detail, SUCCESS)
        elif event.state is ConversationState.FAILED:
            self._set_status(f"出错了：{event.detail}", WARNING)
        else:
            self._set_status(event.detail or "已停止。", TEXT_MUTED)

    def _on_error_ui(self, event: ErrorEvent) -> None:
        self._set_controls_enabled(True)
        self._refresh_turn_buttons()
        self._set_status(event.message, WARNING)

    def _refresh_turn_buttons(self) -> None:
        auto = self._auto_toggle.isChecked()
        self._foreign_button.setVisible(not auto)
        self._my_button.setVisible(not auto)
        self._auto_button.setVisible(auto)

        language = language_name(self._current_language())
        session = self._session
        active = session.active_speaker if session is not None else None
        running = session is not None and session.is_running

        self._apply_turn_button(
            self._foreign_button,
            active=active is Speaker.FOREIGN,
            idle_text=f"对方说（{language}）",
            active_text=f"对方正在说（{language}）　点击结束",
        )
        self._apply_turn_button(
            self._my_button,
            active=active is Speaker.ME,
            idle_text="我说（中文）",
            active_text="我正在说（中文）　点击结束",
        )
        self._apply_turn_button(
            self._auto_button,
            active=auto and running,
            idle_text=f"开始对话（中文 ⇄ {language}）",
            active_text="正在对话　点击结束",
        )

    @staticmethod
    def _apply_turn_button(
        button: QPushButton, *, active: bool, idle_text: str, active_text: str
    ) -> None:
        button.setText(active_text if active else idle_text)
        button.setObjectName("TurnButtonActive" if active else "TurnButton")
        button.style().unpolish(button)
        button.style().polish(button)

    def _set_controls_enabled(self, enabled: bool) -> None:
        self._foreign_button.setEnabled(enabled)
        self._my_button.setEnabled(enabled)
        self._auto_button.setEnabled(enabled)
        self._auto_toggle.setEnabled(enabled)
        self._language_combo.setEnabled(enabled)
        self._voice_combo.setEnabled(enabled)
        self._device_combo.setEnabled(enabled)

    def _set_status(self, text: str, color: str) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color};")

    def _clear_transcript(self) -> None:
        for bubble in self._bubbles.values():
            wrapper = bubble.parentWidget()
            self._transcript.removeWidget(wrapper)
            wrapper.deleteLater()
        self._bubbles.clear()
        self._placeholder.show()
