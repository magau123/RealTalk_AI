"""对话界面：面对面双语交流的单一视图。

对话记录按时间整行排列，每条消息上方是原文（小字、弱化），下方是译文
（大字、主色）。英语监听由独立开关控制；中文回复仍采用半双工，回复期间
暂停英语输入，说完后按开关状态决定是否恢复监听。

线程处理与其他页面一致：核心层回调来自后台线程，一律通过 Qt Signal
投递到 UI 线程；会阻塞的启停操作放到后台线程执行。
"""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
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
        # 整行铺满：译文是使用者真正要读的内容，越窄折行越多越难读。
        # 说话人靠左侧色条和抬头区分，不再用左右对齐。
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        # 必须显式打开 heightForWidth，否则折行后的真实高度传不到滚动容器：
        # 容器高度停留在旧值，滚到底看到的是空白，最新内容反而在上面。
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

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


class _TranscriptArea(QScrollArea):
    """会正确计算折行高度的滚动区。

    QScrollArea 开着 widgetResizable 时，内容高度取自 sizeHint 而不是
    heightForWidth。气泡是整行铺满、靠折行撑高的，两者差得很远：实测一条
    长消息需要 226px，容器只给了 66px。结果滚动条范围与真实内容对不上，
    滚到底看到的是空白，最新的话反而落在上面，得往回滚才看得到。
    """

    def sync_content_height(self) -> None:
        content = self.widget()
        layout = content.layout() if content is not None else None
        if layout is None:
            return
        viewport = self.viewport()
        margins = layout.contentsMargins()
        inner_width = viewport.width() - margins.left() - margins.right()

        # 逐个累加子控件，不用 layout.heightForWidth()：它内部缓存在这个
        # 时机不可靠，实测每个气泡各自报 226px，它却只给出 254px。
        # ponytail: 只认得「一列控件 + 末尾弹簧」这一种结构，聊天记录以后
        # 若嵌套子布局，这里要改成递归。
        total = margins.top() + margins.bottom()
        visible = 0
        for index in range(layout.count()):
            widget = layout.itemAt(index).widget()
            if widget is None or widget.isHidden():
                continue
            if widget.sizePolicy().hasHeightForWidth():
                total += widget.heightForWidth(inner_width)
            else:
                total += widget.sizeHint().height()
            visible += 1
        if visible > 1:
            total += layout.spacing() * (visible - 1)

        # 必须锁死高度而不只是给下限：容器的 sizeHint 是「不折行」的高度，
        # 比真实内容高出一大截，QScrollArea 会取两者的较大值，多出来的部分
        # 就是滚到底后那片空白。内容不足一屏时仍撑满视口，避免底部露缝。
        content.setFixedHeight(max(total, viewport.height()))

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self.sync_content_height()


class ConversationPage(QWidget):
    _messageReceived = Signal(object)
    _stateChanged = Signal(object)
    _errorOccurred = Signal(object)
    opacityChanged = Signal(int)

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._session: ConversationSession | None = None
        self._bubbles: dict[str, MessageBubble] = {}
        self._listening_requested = False
        self._settle_pending = False
        self._scroll_pending = False

        self._build_ui()

        self._messageReceived.connect(self._on_message_ui)
        self._stateChanged.connect(self._on_state_ui)
        self._errorOccurred.connect(self._on_error_ui)

    # ---- 界面搭建 ----

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 14)
        root.setSpacing(14)

        self._create_action_buttons()

        root.addLayout(self._build_header())
        root.addWidget(self._build_settings_panel())
        root.addLayout(self._build_action_row())
        root.addWidget(self._build_transcript(), stretch=1)

        self._status = QLabel("已就绪。点击「开始检测英语」后接收并实时翻译。")
        self._status.setObjectName("StatusLabel")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)

        title_column = QVBoxLayout()
        title_column.setSpacing(2)
        title = QLabel("RealTalk")
        title.setObjectName("AppTitle")
        subtitle = QLabel("实时双向语音翻译")
        subtitle.setObjectName("AppSubtitle")
        title_column.addWidget(title)
        title_column.addWidget(subtitle)
        row.addLayout(title_column)
        row.addStretch(1)

        self._opacity_label = QLabel("界面透明度 100%")
        self._opacity_label.setObjectName("OpacityLabel")
        row.addWidget(self._opacity_label)

        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setObjectName("OpacitySlider")
        self._opacity_slider.setRange(50, 100)
        self._opacity_slider.setValue(100)
        self._opacity_slider.setFixedWidth(130)
        self._opacity_slider.setToolTip("拖动调整整个窗口的透明度，最低 50%")
        self._opacity_slider.valueChanged.connect(self._on_opacity_changed)
        row.addWidget(self._opacity_slider)
        return row

    def _build_settings_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("SettingsPanel")
        grid = QGridLayout(panel)
        grid.setContentsMargins(16, 12, 16, 14)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)

        grid.addWidget(self._caption("对方语言"), 0, 0)
        self._language_combo = QComboBox()
        for code in conversation_languages():
            self._language_combo.addItem(language_name(code), code)
        self._language_combo.currentIndexChanged.connect(self._reload_voices)
        grid.addWidget(self._language_combo, 1, 0)

        grid.addWidget(self._caption("回复音色"), 0, 1)
        self._voice_combo = QComboBox()
        self._voice_combo.currentIndexChanged.connect(self._restart_default_listener)
        grid.addWidget(self._voice_combo, 1, 1)

        devices = list_input_devices()

        grid.addWidget(self._caption("英语声音来源"), 0, 2)
        self._foreign_device_combo = QComboBox()
        self._foreign_device_combo.setToolTip(
            "选择电脑回环声音，或在没有回环设备时使用默认麦克风。"
        )
        loopback_index = None
        for device in devices:
            is_loopback = "回环" in device.name
            if is_loopback:
                self._foreign_device_combo.addItem(
                    f"🔊 {device.name}", device.index
                )
                loopback_index = self._foreign_device_combo.count() - 1
        if loopback_index is None:
            self._foreign_device_combo.addItem("🎙 系统默认麦克风", None)
        else:
            self._foreign_device_combo.setCurrentIndex(loopback_index)
        self._foreign_device_combo.currentIndexChanged.connect(
            self._restart_default_listener
        )
        grid.addWidget(self._foreign_device_combo, 1, 2)

        grid.addWidget(self._caption("我的麦克风"), 0, 3)
        self._mic_combo = QComboBox()
        self._mic_combo.addItem("🎙 系统默认麦克风", None)
        for device in devices:
            if "回环" not in device.name:
                self._mic_combo.addItem(f"🎙 {device.name}", device.index)
        self._mic_combo.currentIndexChanged.connect(self._restart_default_listener)
        grid.addWidget(self._mic_combo, 1, 3)
        for column in range(4):
            grid.setColumnStretch(column, 1)

        self._reload_voices()
        return panel

    def _build_action_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(self._listen_button, stretch=1)
        row.addWidget(self._speak_button, stretch=1)
        row.addWidget(self._clear_button)
        return row

    def _build_transcript(self) -> QScrollArea:
        self._scroll = _TranscriptArea()
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
            "点击「开始检测英语」，接收声音并实时翻译为中文。\n"
            "需要回复时，点击「我要说中文」。"
        )
        self._placeholder.setObjectName("HintLabel")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._transcript.insertWidget(0, self._placeholder)
        return self._scroll

    def _create_action_buttons(self) -> None:
        self._listen_button = QPushButton("开始检测英语")
        self._listen_button.setObjectName("ListenButton")
        self._listen_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._listen_button.clicked.connect(self._on_listen_clicked)

        self._speak_button = QPushButton("我要说中文")
        self._speak_button.setObjectName("TurnButton")
        self._speak_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._speak_button.clicked.connect(self._on_speak_clicked)

        self._clear_button = QPushButton("清空记录")
        self._clear_button.setObjectName("QuietButton")
        self._clear_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._clear_button.clicked.connect(self._clear_transcript)

    @staticmethod
    def _caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("SectionTitle")
        return label

    def _reload_voices(self) -> None:
        code = self._language_combo.currentData()
        self._voice_combo.blockSignals(True)
        self._voice_combo.clear()
        for voice in TTS_VOICES.get(code, ()):
            self._voice_combo.addItem(voice.zh_label, voice.voice_id)
        self._voice_combo.blockSignals(False)
        self._restart_default_listener()

    # ---- 交互 ----

    def _current_language(self) -> str:
        return self._language_combo.currentData()

    def _start_default_listener(self) -> None:
        self._begin(lambda active: active.start_turn(Speaker.FOREIGN))

    def _on_listen_clicked(self) -> None:
        if self._listening_requested:
            self._listening_requested = False
            self._refresh_action_buttons()
            self._stop_current_session()
            return

        self._listening_requested = True
        self._refresh_action_buttons()
        self._start_default_listener()

    def _on_speak_clicked(self) -> None:
        session = self._session
        if session is not None and session.active_speaker is Speaker.ME:
            if self._listening_requested:
                self._start_default_listener()
            else:
                self._stop_current_session()
            return
        self._begin(lambda active: active.start_turn(Speaker.ME))

    def _stop_current_session(self) -> None:
        session = self._session
        if session is None or not session.is_running:
            self._set_status("英语检测已关闭。", TEXT_MUTED)
            self._set_controls_enabled(True)
            return
        self._set_controls_enabled(False)
        self._set_status("正在关闭英语检测 …", TEXT_SECONDARY)

        def run() -> None:
            try:
                session.stop()
            except Exception as exc:
                self._errorOccurred.emit(ErrorEvent(message=str(exc)))

        threading.Thread(target=run, name="realtalk-stop", daemon=True).start()

    def _on_opacity_changed(self, value: int) -> None:
        self._opacity_label.setText(f"界面透明度 {value}%")
        self.opacityChanged.emit(value)

    def _begin(self, action) -> None:  # noqa: ANN001
        """在后台线程切换通道，避免 WebSocket 握手卡住界面。"""
        active = self._ensure_session()
        self._set_controls_enabled(False)

        def run() -> None:
            try:
                action(active)
            except Exception as exc:
                self._errorOccurred.emit(ErrorEvent(message=str(exc)))

        threading.Thread(target=run, name="realtalk-turn", daemon=True).start()

    def _ensure_session(self) -> ConversationSession:
        if self._session is not None:
            return self._session
        self._session = self._new_session()
        return self._session

    def _new_session(self) -> ConversationSession:
        return ConversationSession(
            self._settings,
            foreign_language=self._current_language(),
            on_message=self._messageReceived.emit,
            on_state=self._stateChanged.emit,
            on_error=self._errorOccurred.emit,
            foreign_input_device=self._foreign_device_combo.currentData(),
            my_input_device=self._mic_combo.currentData(),
            voice_id=self._voice_combo.currentData(),
        )

    def _restart_default_listener(self, *_: object) -> None:
        old = self._session
        self._session = None
        if not self.isVisible() or not self._listening_requested:
            if old is not None:
                self._run_async(old.shutdown)
            return

        active = self._new_session()
        self._session = active
        self._set_controls_enabled(False)

        def run() -> None:
            if old is not None:
                old.shutdown()
            active.start_turn(Speaker.FOREIGN)

        threading.Thread(target=run, name="realtalk-restart", daemon=True).start()

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
            self._transcript.insertWidget(self._transcript.count() - 1, bubble)
            # 刚插入的气泡要等布局激活才会显示，在那之前它算「隐藏」，
            # 量高度时会被跳过，于是滚动范围少算了整整一屏。
            bubble.show()
            at_bottom = True
        else:
            bubble.apply(message)

        if at_bottom:
            self._scroll_pending = True
        # 刚插入的气泡还没显示，字体度量无效，此刻量出来的高度偏小。
        # 必须等这一轮布局跑完再量，否则会把错的高度锁死。流式刷新一秒
        # 好几次，这里合并成一次。
        if not self._settle_pending:
            self._settle_pending = True
            QTimer.singleShot(0, self._settle_transcript)

    def _settle_transcript(self) -> None:
        self._settle_pending = False
        self._scroll.sync_content_height()
        if self._scroll_pending:
            self._scroll_pending = False
            # 高度刚改，滚动条范围要下一轮才更新，现在读 maximum() 还是旧值
            QTimer.singleShot(0, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> None:
        scrollbar = self._scroll.verticalScrollBar()
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
        if event.state is ConversationState.FAILED:
            self._listening_requested = False
        self._refresh_action_buttons()

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
        self._refresh_action_buttons()
        self._set_status(event.message, WARNING)

    def _refresh_action_buttons(self) -> None:
        session = self._session
        active = session.active_speaker if session is not None else None
        self._apply_turn_button(
            self._listen_button,
            active=self._listening_requested,
            idle_text="开始检测英语",
            active_text="关闭英语检测",
            idle_name="ListenButton",
            active_name="ListenButtonActive",
        )
        self._apply_turn_button(
            self._speak_button,
            active=active is Speaker.ME,
            idle_text="我要说中文",
            active_text=(
                f"说完了，继续听{language_name(self._current_language())}"
                if self._listening_requested
                else "说完了，停止录音"
            ),
            idle_name="TurnButton",
            active_name="TurnButtonActive",
        )

    @staticmethod
    def _apply_turn_button(
        button: QPushButton,
        *,
        active: bool,
        idle_text: str,
        active_text: str,
        idle_name: str,
        active_name: str,
    ) -> None:
        button.setText(active_text if active else idle_text)
        button.setObjectName(active_name if active else idle_name)
        button.style().unpolish(button)
        button.style().polish(button)

    def _set_controls_enabled(self, enabled: bool) -> None:
        self._listen_button.setEnabled(enabled)
        self._speak_button.setEnabled(enabled)
        self._language_combo.setEnabled(enabled)
        self._voice_combo.setEnabled(enabled)
        self._foreign_device_combo.setEnabled(enabled)
        self._mic_combo.setEnabled(enabled)

    def _set_status(self, text: str, color: str) -> None:
        self._status.setText(text)
        self._status.setStyleSheet(f"color: {color};")

    def _clear_transcript(self) -> None:
        for bubble in self._bubbles.values():
            self._transcript.removeWidget(bubble)
            bubble.deleteLater()
        self._bubbles.clear()
        self._placeholder.show()
        self._scroll.sync_content_height()
