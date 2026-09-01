"""对话模式：与外国人面对面交流的双向同声传译。

两个方向共用同一支麦克风，而实时模型的翻译目标语言在建立连接时就固定了，
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
from realtalk.core.listen import ListenSession, create_listen_session
from realtalk.core.qwen_listen import QwenLiveTranslateSession
from realtalk.core.translator import TextTranslator
from realtalk.core.tts import SpeechSynthesizerClient, SynthesisError
from realtalk.languages import (
    AUTO,
    conversation_languages,
    default_voice,
    language_name,
    looks_like_chinese,
    voice_by_id,
)

logger = logging.getLogger(__name__)

CHINESE = "zh"


class Speaker(enum.Enum):
    FOREIGN = "foreign"  # 对方，说外语，译文显示为中文
    ME = "me"            # 我，说中文，译文合成为外语语音播放


class TurnMode(enum.Enum):
    MANUAL = "manual"  # 两个按钮显式切换发言人
    AUTO = "auto"      # 单条连接，按识别文本的书写系统判断是谁在说


class ConversationState(enum.Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    FOREIGN_TURN = "foreign_turn"
    MY_TURN = "my_turn"
    AUTO_LISTENING = "auto_listening"
    FAILED = "failed"


@dataclass
class ConversationStateEvent:
    state: ConversationState
    detail: str = ""


@dataclass
class ConversationMessage:
    """对话中的一条消息。

    message_id 带轮次前缀。实时引擎的 sentence_id 在每条新连接上都从 0 重新
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
        listen_language: str | None = None,
        on_message: MessageHandler,
        on_state: StateHandler | None = None,
        on_error: ErrorHandler | None = None,
        input_device: int | None = None,
        foreign_input_device: int | None = None,
        my_input_device: int | None = None,
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
        source = AUTO if listen_language is None else listen_language
        if source != AUTO and source not in available:
            raise ValueError(
                f"{language_name(source)} 暂不支持听译成中文。"
            )

        self._settings = settings
        self._foreign_language = foreign_language
        self._listen_language = source
        self._on_message = on_message
        self._on_state = on_state
        self._on_error = on_error
        self._foreign_input_device = (
            foreign_input_device
            if foreign_input_device is not None
            else input_device
        )
        self._my_input_device = my_input_device
        self._voice = self._resolve_voice(voice_id)

        self._synthesizer = SpeechSynthesizerClient(settings)
        self._player = PcmStreamPlayer(device=output_device)

        self._translator = TextTranslator(settings)

        self._active: ListenSession | QwenLiveTranslateSession | None = None
        self._active_speaker: Speaker | None = None
        self._mode: TurnMode | None = None
        self._turn_index = 0
        self._state = ConversationState.IDLE
        self._lock = threading.RLock()

        # 自动模式下锁定每句话的发言人。中间结果可能短到无法判断书写系统
        # （例如只识别出「?」），逐次重判会让气泡在左右两侧来回跳。
        self._sentence_speakers: dict[int, Speaker] = {}

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
    def listen_language(self) -> str:
        return self._listen_language

    @property
    def voice(self) -> str:
        return self._voice

    @property
    def state(self) -> ConversationState:
        return self._state

    @property
    def active_speaker(self) -> Speaker | None:
        return self._active_speaker

    @property
    def mode(self) -> TurnMode | None:
        """当前正在运行的模式，未运行时为 None。"""
        return self._mode

    @property
    def is_running(self) -> bool:
        return self._active is not None

    # ---- 手动模式 ----

    def start_turn(self, speaker: Speaker) -> None:
        """开始某一方的发言。会阻塞到连接建立，UI 请在后台线程调用。

        若另一方正在发言，先结束对方的轮次。
        """
        with self._lock:
            if self._mode is TurnMode.MANUAL and self._active_speaker is speaker:
                return
            self._stop_active()

            if speaker is Speaker.FOREIGN:
                source, target = self._listen_language, CHINESE
            else:
                source, target = CHINESE, self._foreign_language

            self._set_state(
                ConversationState.CONNECTING,
                f"正在准备{self._speaker_label(speaker)}的通道 …",
            )

            turn = self._begin_turn()
            session = self._open_session(
                source_language=source,
                target_language=target,
                input_device=(
                    self._foreign_input_device
                    if speaker is Speaker.FOREIGN
                    else self._my_input_device
                ),
                on_sentence=lambda update: self._handle_sentence(
                    update, speaker=speaker, turn=turn
                ),
            )
            if session is None:
                return

            self._active = session
            self._active_speaker = speaker
            self._mode = TurnMode.MANUAL
            self._set_state(
                ConversationState.FOREIGN_TURN
                if speaker is Speaker.FOREIGN
                else ConversationState.MY_TURN,
                self._turn_hint(speaker),
            )

    # ---- 自动模式 ----

    def start_auto(self) -> None:
        """开启自动模式：一条连接，按识别文本判断说话人。

        源语种设为 auto、翻译目标固定为中文。对方说外语时实时模型直接给出
        流式中文译文，延迟与手动模式完全相同；只有我说中文时才需要额外一次
        中译外，而那一侧本来就要等语音合成，多出的这一跳被掩盖掉了。

        反过来把目标固定为外语是行不通的：那样对方的话会被翻成他自己的语言，
        等于没翻。所以只能固定中文，让「我说的话」走补翻译。
        """
        with self._lock:
            if self._mode is TurnMode.AUTO:
                return
            self._stop_active()

            self._set_state(ConversationState.CONNECTING, "正在准备对话通道 …")

            turn = self._begin_turn()
            session = self._open_session(
                source_language=AUTO,
                target_language=CHINESE,
                input_device=self._foreign_input_device,
                on_sentence=lambda update: self._handle_auto_sentence(update, turn),
            )
            if session is None:
                return

            self._active = session
            self._active_speaker = None
            self._mode = TurnMode.AUTO
            self._set_state(
                ConversationState.AUTO_LISTENING,
                f"正在自动识别。双方直接说话即可，"
                f"中文会翻成{language_name(self._foreign_language)}读给对方听。",
            )

    def stop(self) -> None:
        """结束当前会话。队列中尚未朗读的译文仍会播放完。"""
        with self._lock:
            if self._active is None:
                return
            self._stop_active()
            self._set_state(ConversationState.IDLE, "已停止。点击按钮重新开始。")

    # 手动模式下的语义化别名
    end_turn = stop

    def _begin_turn(self) -> int:
        self._turn_index += 1
        self._sentence_speakers.clear()
        return self._turn_index

    def _open_session(
        self,
        *,
        source_language: str,
        target_language: str,
        input_device: int | None,
        on_sentence: Callable[[SentenceUpdate], None],
    ) -> ListenSession | QwenLiveTranslateSession | None:
        session = create_listen_session(
            self._settings,
            on_sentence=on_sentence,
            on_state=self._handle_inner_state,
            on_error=self._forward_error,
            source_language=source_language,
            target_language=target_language,
            device=input_device,
        )
        try:
            session.start()
        except Exception as exc:
            self._set_state(ConversationState.FAILED, str(exc))
            self._emit_error(f"无法开始对话：{exc}")
            return None
        return session

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
        self._mode = None
        if session is not None:
            session.stop()

    # ---- 识别结果处理 ----

    def _handle_auto_sentence(self, update: SentenceUpdate, turn: int) -> None:
        speaker = self._resolve_speaker(update)
        if speaker is None:
            # 还看不出是谁在说（例如只识别出标点），等下一批结果
            return

        if speaker is Speaker.ME:
            # 此时模型在做中译中，结果要么是原文要么为空，两种都不能当译文
            # 用——直接拿去合成会让对方听到外语音色念中文。清空后交给补翻译。
            update = replace(update, translated_text="")

        self._handle_sentence(update, speaker=speaker, turn=turn)

    def _resolve_speaker(self, update: SentenceUpdate) -> Speaker | None:
        """判断这句是谁说的，判定结果按句锁定。

        锁定是必要的：中间结果不断增长，早期片段可能短到无法判断书写系统，
        若每批都重判，气泡会在左右两侧来回跳。
        """
        with self._lock:
            cached = self._sentence_speakers.get(update.sentence_id)
            if cached is not None:
                return cached

            is_chinese = looks_like_chinese(update.source_text)
            if is_chinese is None:
                return None

            speaker = Speaker.ME if is_chinese else Speaker.FOREIGN
            self._sentence_speakers[update.sentence_id] = speaker
            return speaker

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
                (update.source_language or self._listen_language)
                if speaker is Speaker.FOREIGN
                else CHINESE
            ),
            translated_language=(
                CHINESE if speaker is Speaker.FOREIGN else self._foreign_language
            ),
        )
        self._emit_message(message)

        # 只有我说的话需要读给对方听，且必须等这句定稿——中间结果每秒刷新
        # 多次，逐条合成会让对方听到不断被打断的半句话。
        #
        # 这里不要求译文已经就绪：自动模式下我的中文是交给补翻译的，入队时
        # 只有原文。译文缺失时由朗读线程在合成前补上。
        if speaker is Speaker.ME and update.is_final and message.original_text:
            self._speech_queue.put(message)

    def _handle_inner_state(self, event: StateEvent) -> None:
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
        message = self._ensure_translation(message)
        if message is None or not message.translated_text:
            return

        session = self._active
        # 播放期间掐掉音频上行，否则扬声器里的外语会被麦克风重新收进来，
        # 识别器会把「自己刚说出去的话」当成对方在讲，再翻译一遍显示出来。
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

    def _ensure_translation(
        self, message: ConversationMessage
    ) -> ConversationMessage | None:
        """译文缺失时补一次中译外。

        自动模式下翻译目标固定为中文，我说的中文拿不到外语译文，必须在这里
        补。手动模式通常用不到，但实时模型偶尔漏译时它同样兜底。
        """
        if message.translated_text:
            return message
        if not message.original_text:
            return None

        text = self._translator.translate_quietly(
            message.original_text,
            target_language=self._foreign_language,
            source_language=CHINESE,
        )
        if not text:
            self._emit_error("翻译失败，这句话没能读给对方听。")
            self._emit_message(replace(message, is_speaking=False))
            return None

        translated = replace(message, translated_text=text)
        self._emit_message(translated)
        return translated

    def replay(self, message: ConversationMessage) -> None:
        """重放某条译文，用于对方没听清时。"""
        if message.speaker is not Speaker.ME or not message.translated_text:
            return
        self._speech_queue.put(message)

    # ---- 辅助 ----

    def _speaker_label(self, speaker: Speaker) -> str:
        if speaker is Speaker.FOREIGN:
            return "对方"
        return "我（中文）"

    def _turn_hint(self, speaker: Speaker) -> str:
        if speaker is Speaker.FOREIGN:
            return "正在听对方说话，译文会显示为中文。"
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
