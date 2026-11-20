"""中文麦克风输入 → 英文文字字幕；此模式只创建听译会话。"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QCloseEvent, QTextCursor
from PySide6.QtWidgets import (
    QComboBox,
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from realtalk.audio.recorder import AudioDevice, list_input_devices
from realtalk.audio.output_guard import OutputMuteGuard
from realtalk.config import Settings
from realtalk.core.events import SentenceUpdate, SessionState
from realtalk.core.listen import create_listen_session
from realtalk.ui.macos_hotkey import MacPushToTalkHotkey, configure_floating_panel


class EnglishSubtitlesDialog(QDialog):
    """独立字幕窗口，后台串行启停会话，避免退出或快速点击造成录音重叠。"""

    _sentenceReceived = Signal(int, object)
    _failed = Signal(int, str)
    _operationFinished = Signal(int, str)

    def __init__(
        self,
        settings: Settings,
        parent: QWidget | None = None,
        *,
        prepare: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("RealTalk · 翻译模式")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.resize(820, 330)
        self.setMinimumSize(600, 230)
        self._settings = settings
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="english-subtitles")
        self._session = None
        self._generation = 0
        self._running = False
        self._busy = True
        self._closing = False
        self._finished = False
        self._compact = False
        self._normal_geometry = None
        self._latest_source = -1
        self._latest_translation = -1
        self._holding_space = False
        self._local_holding = False
        self._global_holding = False
        self._hotkey_available = False
        self._output_guard = OutputMuteGuard()
        self._utterance_active = False
        self._finishing = False
        self._segments: dict[int, SentenceUpdate] = {}
        self._live_start: int | None = None
        self._live_text = ""
        self._hotkey = MacPushToTalkHotkey(self)
        self._hotkey.changed.connect(self._set_global_holding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        controls = QHBoxLayout()
        self._device = QComboBox()
        self._microphones = {}
        for device in list_input_devices():
            if device.index >= 0 and not any(word in device.name.lower() for word in (
                "teams", "blackhole", "loopback", "soundflower", "monitor", "回环", "系统声音",
            )):
                self._device.addItem(device.name, device.index)
                self._microphones[device.index] = device.name
        for index in range(self._device.count()):
            if any(word in self._device.itemText(index).lower() for word in ("macbook", "内建", "内置")):
                self._device.setCurrentIndex(index)
                break
        self._device.setToolTip("选择你说中文时使用的麦克风")
        controls.addWidget(self._device, stretch=1)
        self._start_button = QPushButton("连接翻译")
        self._start_button.clicked.connect(self._toggle)
        controls.addWidget(self._start_button)
        self._compact_button = QPushButton("收起设置")
        self._compact_button.clicked.connect(self._toggle_compact)
        controls.addWidget(self._compact_button)
        close_button = QPushButton("返回对话")
        close_button.clicked.connect(self.reject)
        controls.addWidget(close_button)
        layout.addLayout(controls)

        self._english = QPlainTextEdit()
        self._english.setReadOnly(True)
        self._english.setPlaceholderText("按住快捷键说中文，英文实时累计显示；再次按下时清空上一段。")
        self._english.setStyleSheet("font-size: 26px; font-weight: 600;")
        layout.addWidget(self._english, stretch=1)
        self._chinese = QLabel("中文原文会显示在这里")
        self._chinese.setWordWrap(True)
        self._chinese.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self._chinese)
        self._status = QLabel("正在连接翻译，连接完成后按住空格说中文 …")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)
        self._shortcut_hint = QLabel()
        self._shortcut_hint.setWordWrap(True)
        self._shortcut_hint.setObjectName("HintLabel")
        layout.addWidget(self._shortcut_hint)
        self._sentenceReceived.connect(self._on_sentence)
        self._failed.connect(self._on_error)
        self._operationFinished.connect(self._on_finished)
        for button in self.findChildren(QPushButton):
            button.setAutoDefault(False)
        self._app = QApplication.instance()
        self._app.installEventFilter(self)
        try:
            self._hotkey.start()
            self._hotkey_available = True
            self._shortcut_hint.setText("Teams 保持静音 · 按住 Ctrl＋Option＋空格 说中文 · 松开出完译文")
        except (OSError, RuntimeError) as exc:
            self._shortcut_hint.setText(f"{exc}。展开设置后使用本窗口空格键。")
        if self._hotkey_available:
            self._toggle_compact()
            screen = QApplication.primaryScreen()
            if screen is not None:
                area = screen.availableGeometry()
                self.move(area.right() - self.width() - 20, area.top() + 60)
        self._refresh_controls()
        self._executor.submit(self._prepare, prepare)

    def _prepare(self, prepare: Callable[[], None] | None) -> None:
        try:
            if prepare is not None:
                prepare()
        except Exception as exc:
            self._failed.emit(0, f"无法关闭原来的录音会话：{exc}")
        finally:
            self._operationFinished.emit(0, "ready")

    def _toggle(self) -> None:
        if self._busy or self._closing:
            return
        self._generation += 1
        generation = self._generation
        self._busy = True
        self._refresh_controls()
        if self._running:
            self._set_holding_space(False)
            self._set_global_holding(False)
            self._status.setText("正在停止 …")
            self._executor.submit(self._stop, generation)
        else:
            self._latest_source = self._latest_translation = -1
            self._status.setText("正在连接中文 → 英文翻译 …")
            self._executor.submit(self._start, generation, self._device.currentData())

    def _start(self, generation: int, device: int | None) -> None:
        try:
            if device not in self._microphones:
                raise RuntimeError("没有可用的物理麦克风，请连接麦克风后重新打开翻译模式。")
            session = create_listen_session(
                self._settings,
                source_language="zh",
                target_language="en",
                device=device,
                on_sentence=lambda update: self._sentenceReceived.emit(generation, update),
                on_error=lambda error: self._failed.emit(generation, error.message),
                on_state=lambda event: (
                    self._failed.emit(generation, event.detail)
                    if event.state is SessionState.FAILED else None
                ),
            )
            self._session = session
            session.set_input_enabled(False)
            session.start()
            # 按键可能发生在连接期间，连接完成时以最新按键状态为准。
            session.set_input_enabled(self._holding_space and not self._closing)
        except Exception as exc:
            self._failed.emit(generation, str(exc))
            self._operationFinished.emit(generation, "failed")
            return
        self._operationFinished.emit(generation, "started")

    def _stop(self, generation: int) -> None:
        session, self._session = self._session, None
        try:
            if session is not None:
                session.stop()
        except Exception as exc:
            self._failed.emit(generation, str(exc))
        finally:
            self._operationFinished.emit(generation, "stopped")

    def _on_sentence(self, generation: int, update: SentenceUpdate) -> None:
        if generation != self._generation or self._closing:
            return
        if self._utterance_active:
            previous = self._segments.get(update.sentence_id)
            if previous is None or not previous.is_final:
                self._segments[update.sentence_id] = update
                self._render_utterance()

    def _render_utterance(self) -> None:
        """当前按键发言内实时累计，下一次按键才清空上一段。"""
        segments = [self._segments[key] for key in sorted(self._segments)]
        english = " ".join(item.translated_text.strip() for item in segments if item.translated_text.strip())
        chinese = "".join(item.source_text.strip() for item in segments)
        self._chinese.setText(chinese or "正在识别中文 …")
        if not english or english == self._live_text:
            return
        scrollbar = self._english.verticalScrollBar()
        follow = scrollbar.value() >= scrollbar.maximum() - 20
        cursor = QTextCursor(self._english.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.beginEditBlock()
        if self._live_start is None:
            if not self._english.document().isEmpty():
                cursor.insertText("\n\n")
            self._live_start = cursor.position()
        cursor.setPosition(self._live_start)
        cursor.movePosition(QTextCursor.MoveOperation.End, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(english)
        cursor.endEditBlock()
        self._live_text = english
        if follow:
            scrollbar.setValue(scrollbar.maximum())

    def _finish_utterance(self, generation: int) -> None:
        # stop() 先停录音，再发送 session.finish 并等待尾部响应；这期间仍接收结果。
        session, self._session = self._session, None
        try:
            if session is not None:
                finish_input = getattr(session, "finish_input", None)
                if finish_input is not None:
                    finish_input()
                session.stop()
        except Exception as exc:
            self._failed.emit(generation, str(exc))
        finally:
            self._operationFinished.emit(generation, "utterance")

    def _on_error(self, generation: int, message: str) -> None:
        if generation != self._generation or self._closing:
            return
        self._set_holding_space(False)
        self._set_global_holding(False)
        self._status.setText(f"翻译中断：{message}")
        self._utterance_active = self._finishing = False
        # 使当前会话剩余的回调失效，串行清理后允许再次开始。
        self._generation += 1
        self._busy = True
        self._refresh_controls()
        self._executor.submit(self._stop, self._generation)

    def _on_finished(self, generation: int, operation: str) -> None:
        if generation != self._generation:
            return
        self._busy = False
        self._running = operation == "started"
        if self._closing:
            if operation != "stopped":
                return
            self._finished = True
            self._executor.shutdown(wait=False)
            self._app.removeEventFilter(self)
            super().reject()
            return
        if operation == "ready":
            self._toggle()
            return
        elif operation == "utterance":
            self._render_utterance()
            if not self._segments:
                self._chinese.setText("本次未检测到语音")
            self._utterance_active = self._finishing = False
            self._toggle()  # 预建下一次发言连接；旧连接已经完成，结果不会串到下一段。
            return
        elif operation == "started":
            self._session.set_input_enabled(self._holding_space)
            self._show_capture_status()
        elif operation == "stopped" and not self._status.text().startswith("翻译中断"):
            self._status.setText("连接已关闭。点击「连接翻译」后，按住空格说话。")
        self._refresh_controls()

    def _refresh_controls(self) -> None:
        self._device.setEnabled(not self._running and not self._busy and not self._closing)
        self._start_button.setEnabled(not self._busy and not self._closing)
        self._start_button.setText("断开连接" if self._running else "连接翻译")

    def _show_capture_status(self) -> None:
        if self._holding_space:
            self._status.setText("● 仅接收麦克风 · 扬声器已临时静音 · 英文实时累计显示")
        elif self._hotkey_available:
            self._status.setText("就绪 · 在 Teams 中按住 Ctrl＋Option＋空格 说中文")
        else:
            self._status.setText("按住空格说中文 · 松开后继续完成英文翻译 · 仅在此窗口前台时有效")

    def _set_holding_space(self, held: bool) -> None:
        self._local_holding = held
        self._update_capture_gate()

    def _set_global_holding(self, held: bool) -> None:
        self._global_holding = held and self.isVisible() and not self._closing
        self._update_capture_gate()

    def _update_capture_gate(self) -> None:
        held = self._local_holding or self._global_holding
        if held == self._holding_space:
            return
        if held and (not self._running or self._busy or self._finishing or self._closing):
            return
        if not held and self._session is not None:
            self._session.set_input_enabled(False)
        try:
            if held:
                self._output_guard.begin()
                self._english.clear()
                self._chinese.setText("正在识别本次中文 …")
                self._segments.clear()
                self._live_start = None
                self._live_text = ""
                self._utterance_active = True
            else:
                self._output_guard.end()
        except RuntimeError as exc:
            self._local_holding = self._global_holding = False
            if self._session is not None:
                self._session.set_input_enabled(False)
            self._holding_space = False
            self._status.setText(str(exc))
            return
        self._holding_space = held
        session = self._session
        if session is not None:
            session.set_input_enabled(held and not self._closing and self._running)
        if self._running and not self._closing:
            self._show_capture_status()
        if not held and self._utterance_active and not self._finishing and not self._closing:
            self._finishing = self._busy = True
            self._running = False
            self._status.setText("正在完成整段翻译，请稍候再开始下一次发言 …")
            self._refresh_controls()
            self._executor.submit(self._finish_utterance, self._generation)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if self._closing:
            return super().eventFilter(watched, event)
        if watched is self and event.type() in (
            QEvent.Type.WindowDeactivate, QEvent.Type.Hide,
        ):
            # 本地空格失去焦点时停收；全局热键不依赖本窗口焦点。
            self._set_holding_space(False)
            if event.type() == QEvent.Type.Hide:
                self._set_global_holding(False)
        if isinstance(watched, QWidget) and (
            watched is self or self.isAncestorOf(watched)
        ) and event.type() in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            if event.key() == Qt.Key.Key_Space:
                if not event.isAutoRepeat():
                    self._set_holding_space(event.type() == QEvent.Type.KeyPress)
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _toggle_compact(self) -> None:
        self._set_holding_space(False)
        self._set_global_holding(False)
        self._compact = not self._compact
        if self._compact:
            self._normal_geometry = self.geometry()
        self._chinese.setVisible(not self._compact)
        self._device.setVisible(not self._compact)
        # 字幕条点击/刷新均不把焦点从 Teams 抢走；展开设置时恢复键盘焦点能力。
        self.setWindowFlag(
            Qt.WindowType.WindowDoesNotAcceptFocus,
            self._compact and self._hotkey_available,
        )
        self._compact_button.setText("展开设置" if self._compact else "收起设置")
        self.setMinimumSize(580 if self._compact else 600, 175 if self._compact else 230)
        self.show()
        configure_floating_panel(self)
        if self._compact:
            self.resize(760, 190)
        elif self._normal_geometry is not None:
            self.setGeometry(self._normal_geometry)

    def reject(self) -> None:
        if self._closing:
            return
        self._set_holding_space(False)
        self._set_global_holding(False)
        try:
            self._output_guard.end()
        except RuntimeError as exc:
            self._status.setText(f"请先在系统声音设置恢复扬声器：{exc}")
            return
        self._hotkey.stop()
        self._closing = True
        self._generation += 1
        self._status.setText("正在关闭麦克风与翻译连接 …")
        self._refresh_controls()
        self._executor.submit(self._stop, self._generation)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._finished:
            event.accept()
        else:
            event.ignore()
            self.reject()
