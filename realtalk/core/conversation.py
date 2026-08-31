"""对话模式：与外国人面对面交流的双向同声传译。

两个方向共用同一支麦克风，而 Gummy 的翻译目标语言在建立连接时就固定了，
一条连接只能是「外语→中文」或「中文→外语」其中之一，无法中途切换。
因此本模块采用**半双工**：同一时刻只有一条链路在跑，由使用者显式切换
当前轮到谁说话。

这样做不只是被 API 限制，也规避了两个真实问题：
- 若两条链路同时开着，我说的中文会被「外语→中文」那条也听进去，按外语
  强行拟合成乱码，而且识别与翻译分别计费，成本翻倍。
- 扬声器播放译文时，麦克风会把这段声音重新收进来形成回声循环。这里通过
  播放期间静音上行音频来切断（见 ListenSession.mute）。

两个方向的差异只有一处：轮到我说时，每句定稿的译文都会被合成成语音放给
对方听；轮到对方说时只显示文字，不发声。
"""

from __future__ import annotations

import enum
import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace

from realtalk.audio.player import PcmStreamPlayer
from realtalk.config import Settings
from realtalk.core.events import ErrorEvent, SentenceUpdate, SessionState, StateEvent
from realtalk.core.listen import ListenSession
from realtalk.core.tts import SpeechSynthesizerClient, SynthesisError
from realtalk.languages import (
    conversation_languages,
    default_voice,
    language_name,
    voice_by_id,
)

logger = logging.getLogger(__name__)

CHINESE = "zh"


class Speaker(enum.Enum):
    FOREIGN = "foreign"  # 对方，说外语，译文显示为中文
    ME = "me"            # 我，说中文，译文合成为外语语音播放


class ConversationState(enum.Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    FOREIGN_TURN = "foreign_turn"
    MY_TURN = "my_turn"
    FAILED = "failed"


@dataclass
class ConversationStateEvent:
    state: ConversationState
    detail: str = ""


@dataclass
class ConversationMessage:
    """对话中的一条消息。

    message_id 带轮次前缀。Gummy 的 sentence_id 在每条新连接上都从 0 重新
    开始，只用它做标识的话，第二轮的第一句会覆盖第一轮的第一句。
    """

    message_id: str
    speaker: Speaker
    original_text: str = ""
    translated_text: str = ""
    is_final: bool = False
    original_language: str = ""
    translated_language: str = ""
    is_speaking: bool = False   # 译文正在朗读
    has_spoken: bool = False    # 译文已朗读完毕


MessageHandler = Callable[[ConversationMessage], None]
StateHandler = Callable[[ConversationStateEvent], None]
ErrorHandler = Callable[[ErrorEvent], None]


class ConversationSession:
    """一场对话。可反复切换发言方，直到 shutdown。

    所有回调都在后台线程触发，GUI 使用时必须自行 marshal 到 UI 线程。
    """

    def __init__(
        self,
        settings: Settings,
        *,
        foreign_language: str = "en",
        on_message: MessageHandler,
        on_state: StateHandler | None = None,
        on_error: ErrorHandler | None = None,
        input_device: int | None = None,
        output_device: int | None = None,
        voice_id: str | None = None,
    ) -> None:
        available = conversation_languages()
        if foreign_language not in available:
            raise ValueError(
                f"{language_name(foreign_language)} 暂不支持对话模式。\n"
                f"对话要求该语种同时支持「→中文」「中文→」双向直译且有可用音色，"
                f"当前可用：{', '.join(language_name(c) for c in available)}"
            )

        self._settings = settings
        self._foreign_language = foreign_language
        self._on_message = on_message
        self._on_state = on_state
        self._on_error = on_error
        self._input_device = input_device
        self._voice = self._resolve_voice(voice_id)

        self._synthesizer = SpeechSynthesizerClient(settings)
        self._player = PcmStreamPlayer(device=output_device)

        self._active: ListenSession | None = None
        self._active_speaker: Speaker | None = None
        self._turn_index = 0
        self._state = ConversationState.IDLE
        self._lock = threading.RLock()

        self._speech_queue: queue.Queue[ConversationMessage | None] = queue.Queue()
        self._speech_worker = threading.Thread(
            target=self._speech_loop, name="realtalk-conversation-tts", daemon=True
        )
        self._speech_worker.start()

    def _resolve_voice(self, voice_id: str | None) -> str:
        if voice_id is None:
            return default_voice(self._foreign_language).voice_id
        voice = voice_by_id(voice_id)
        if voice is None:
            raise ValueError(f"未知音色：{voice_id}")
        if voice.language_code != self._foreign_language:
            raise ValueError(
                f"音色 {voice_id} 是{language_name(voice.language_code)}音色，"
                f"不能用来朗读{language_name(self._foreign_language)}译文"
            )
        return voice.voice_id

    @property
    def foreign_language(self) -> str:
        return self._foreign_language

    @property
    def voice(self) -> str:
        return self._voice

    @property
    def state(self) -> ConversationState:
        return self._state

    @property
    def active_speaker(self) -> Speaker | None:
        return self._active_speaker

    # ---- 轮次控制 ----

    def start_turn(self, speaker: Speaker) -> None:
        """开始某一方的发言。会阻塞到连接建立，UI 请在后台线程调用。

        若另一方正在发言，先结束对方的轮次。
        """
        with self._lock:
            if self._active_speaker is speaker:
                return
            self._stop_active()

            self._turn_index += 1
            turn = self._turn_index

            if speaker is Speaker.FOREIGN:
                source, target = self._foreign_language, CHINESE
            else:
                source, target = CHINESE, self._foreign_language

            self._set_state(
                ConversationState.CONNECTING,
                f"正在准备{self._speaker_label(speaker)}的通道 …",
            )

            session = ListenSession(
                self._settings,
                on_sentence=lambda update: self._handle_sentence(
                    update, speaker=speaker, turn=turn
                ),
                on_state=lambda event: self._handle_inner_state(event, speaker),
                on_error=self._forward_error,
                source_language=source,
                target_language=target,
                device=self._input_device,
            )

            try:
                session.start()
            except Exception as exc:
                self._set_state(ConversationState.FAILED, str(exc))
                self._emit_error(f"无法开始{self._speaker_label(speaker)}：{exc}")
                return

            self._active = session
            self._active_speaker = speaker
            self._set_state(
                ConversationState.FOREIGN_TURN
                if speaker is Speaker.FOREIGN
                else ConversationState.MY_TURN,
                self._turn_hint(speaker),
            )

    def end_turn(self) -> None:
        """结束当前发言。队列中尚未朗读的译文仍会播放完。"""
        with self._lock:
            if self._active_speaker is None:
                return
            self._stop_active()
            self._set_state(ConversationState.IDLE, "已停止。点击按钮开始下一轮。")

    def shutdown(self, *, drain_timeout: float = 8.0) -> None:
        """结束会话。

        先让朗读线程把当前这句念完再关音频设备：反过来做的话，播放器会在
        合成线程还在写数据时被拆掉。播放器本身现在是线程安全的，不会因此
        崩溃，但用户会听到话说到一半被切断。
        """
        with self._lock:
            self._stop_active()

        self._speech_queue.put(None)
        self._speech_worker.join(timeout=drain_timeout)
        if self._speech_worker.is_alive():
            logger.warning("朗读线程未在 %.0f 秒内结束，强制关闭播放", drain_timeout)

        self._player.stop()
        self._set_state(ConversationState.IDLE)

    def _stop_active(self) -> None:
        session = self._active
        self._active = None
        self._active_speaker = None
        if session is not None:
            session.stop()

    # ---- 识别结果处理 ----

    def _handle_sentence(
        self, update: SentenceUpdate, *, speaker: Speaker, turn: int
    ) -> None:
        message = ConversationMessage(
            message_id=f"t{turn}-s{update.sentence_id}",
            speaker=speaker,
            original_text=update.source_text,
            translated_text=update.translated_text,
            is_final=update.is_final,
            original_language=(
                self._foreign_language if speaker is Speaker.FOREIGN else CHINESE
            ),
            translated_language=(
                CHINESE if speaker is Speaker.FOREIGN else self._foreign_language
            ),
        )
        self._emit_message(message)

        # 只有我说的话需要读给对方听，且必须等这句定稿——中间结果每秒刷新
        # 多次，逐条合成会让对方听到不断被打断的半句话。
        if speaker is Speaker.ME and update.is_final and update.translated_text:
            self._speech_queue.put(message)

    def _handle_inner_state(self, event: StateEvent, speaker: Speaker) -> None:
        if event.state is SessionState.FAILED:
            self._set_state(ConversationState.FAILED, event.detail)

    # ---- 译文朗读 ----

    def _speech_loop(self) -> None:
        while True:
            message = self._speech_queue.get()
            if message is None:
                return
            try:
                self._speak(message)
            except Exception:
                # 这个线程一旦因异常退出，后续所有译文都不会再被朗读，
                # 而且失败是静默的。宁可记下来继续处理下一条。
                logger.error("朗读线程遇到未预期的异常", exc_info=True)
                self._emit_message(replace(message, is_speaking=False))
                self._emit_error("朗读失败，请查看日志。下一句仍会正常朗读。")

    def _speak(self, message: ConversationMessage) -> None:
        session = self._active
        # 播放期间掐掉音频上行，否则扬声器里的外语会被麦克风重新收进来，
        # 被固定为中文的识别器强行拟合成乱码。
        if session is not None:
            session.mute()

        self._emit_message(replace(message, is_speaking=True))
        try:
            self._synthesizer.synthesize_and_play(
                message.translated_text, voice=self._voice, player=self._player
            )
        except SynthesisError as exc:
            self._emit_error(f"朗读失败：{exc}")
            self._emit_message(replace(message, is_speaking=False))
            return
        finally:
            if session is not None:
                session.unmute()

        self._emit_message(replace(message, is_speaking=False, has_spoken=True))

    def replay(self, message: ConversationMessage) -> None:
        """重放某条译文，用于对方没听清时。"""
        if message.speaker is not Speaker.ME or not message.translated_text:
            return
        self._speech_queue.put(message)

    # ---- 辅助 ----

    def _speaker_label(self, speaker: Speaker) -> str:
        if speaker is Speaker.FOREIGN:
            return f"对方（{language_name(self._foreign_language)}）"
        return "我（中文）"

    def _turn_hint(self, speaker: Speaker) -> str:
        if speaker is Speaker.FOREIGN:
            return (
                f"正在听对方说{language_name(self._foreign_language)}，"
                f"译文会显示为中文。"
            )
        return (
            f"请说中文，译文会用{language_name(self._foreign_language)}"
            f"朗读给对方听。"
        )

    def _emit_message(self, message: ConversationMessage) -> None:
        try:
            self._on_message(message)
        except Exception:
            logger.error("消息回调抛出异常", exc_info=True)

    def _set_state(self, state: ConversationState, detail: str = "") -> None:
        self._state = state
        if self._on_state is None:
            return
        try:
            self._on_state(ConversationStateEvent(state=state, detail=detail))
        except Exception:
            logger.error("状态回调抛出异常", exc_info=True)

    def _forward_error(self, event: ErrorEvent) -> None:
        self._emit_error(event.message)

    def _emit_error(self, message: str) -> None:
        logger.error("%s", message)
        if self._on_error is None:
            return
        try:
            self._on_error(ErrorEvent(message=message))
        except Exception:
            logger.error("错误回调抛出异常", exc_info=True)
