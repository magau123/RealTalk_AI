"""方向二：中文文本 -> 翻译 -> TTS -> 播放目标语言语音。

链路是两段串行的：Qwen-MT 出译文，CosyVoice 合成并播放。译文一旦拿到
就立刻通过 on_translated 回调抛出去，界面可以先把文字显示出来，不必
等语音合成完成，这样用户的主观等待感会明显变短。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

from realtalk.audio.player import PcmStreamPlayer
from realtalk.config import Settings
from realtalk.core.events import ErrorEvent, SessionState, StateEvent
from realtalk.core.translator import TextTranslator, TranslationError
from realtalk.core.tts import SpeechSynthesizerClient, SynthesisError
from realtalk.languages import default_voice, language_name, voice_by_id

logger = logging.getLogger(__name__)

SOURCE_LANGUAGE = "zh"


@dataclass
class SpeakResult:
    source_text: str
    translated_text: str
    target_language: str
    voice: str
    first_package_delay_ms: float | None = None


class SpeakSession:
    """一次「说译」会话。可复用，串行执行多次 speak。"""

    def __init__(
        self,
        settings: Settings,
        *,
        on_translated: Callable[[str], None] | None = None,
        on_state: Callable[[StateEvent], None] | None = None,
        on_error: Callable[[ErrorEvent], None] | None = None,
        on_finished: Callable[[SpeakResult], None] | None = None,
        device: int | None = None,
    ) -> None:
        self._settings = settings
        self._on_translated = on_translated
        self._on_state = on_state
        self._on_error = on_error
        self._on_finished = on_finished

        self._translator = TextTranslator(settings)
        self._synthesizer = SpeechSynthesizerClient(settings)
        self._player = PcmStreamPlayer(device=device)

        self._worker: threading.Thread | None = None
        self._busy = threading.Lock()

    @property
    def is_busy(self) -> bool:
        return self._busy.locked()

    def resolve_voice(self, target_language: str, voice_id: str | None) -> str:
        """确定使用哪个音色，并校验音色与目标语言是否匹配。

        音色与语言不匹配是这条链路最隐蔽的错误：请求会正常返回音频，
        但发音是错的。所以宁可在这里直接拒绝。
        """
        if voice_id is None:
            return default_voice(target_language).voice_id

        voice = voice_by_id(voice_id)
        if voice is None:
            raise ValueError(f"未知音色：{voice_id}")
        if voice.language_code != target_language:
            raise ValueError(
                f"音色 {voice_id} 是{language_name(voice.language_code)}音色，"
                f"不能用来朗读{language_name(target_language)}文本"
            )
        return voice.voice_id

    def translate(self, chinese_text: str, *, target_language: str) -> str:
        """只翻译不发声，供界面预览用。"""
        return self._translator.translate(
            chinese_text,
            target_language=target_language,
            source_language=SOURCE_LANGUAGE,
        )

    def speak(
        self,
        chinese_text: str,
        *,
        target_language: str,
        voice_id: str | None = None,
    ) -> SpeakResult:
        """阻塞执行完整链路，返回结果。GUI 请改用 speak_async。"""
        text = chinese_text.strip()
        if not text:
            raise ValueError("请输入要翻译的中文内容")

        voice = self.resolve_voice(target_language, voice_id)

        with self._busy:
            self._set_state(
                SessionState.RUNNING,
                f"正在翻译成{language_name(target_language)}…",
            )
            translated = self._translator.translate(
                text,
                target_language=target_language,
                source_language=SOURCE_LANGUAGE,
            )
            if not translated:
                raise TranslationError("翻译模型返回了空结果")

            if self._on_translated is not None:
                try:
                    self._on_translated(translated)
                except Exception:
                    logger.error("译文回调抛出异常", exc_info=True)

            self._set_state(SessionState.RUNNING, "正在合成并播放语音…")
            delay_holder: list[float] = []
            self._synthesizer.synthesize_and_play(
                translated,
                voice=voice,
                player=self._player,
                on_first_package=delay_holder.append,
            )

            self._set_state(SessionState.STOPPED, "播放完成")
            return SpeakResult(
                source_text=text,
                translated_text=translated,
                target_language=target_language,
                voice=voice,
                first_package_delay_ms=delay_holder[0] if delay_holder else None,
            )

    def speak_async(
        self,
        chinese_text: str,
        *,
        target_language: str,
        voice_id: str | None = None,
    ) -> None:
        """在后台线程执行完整链路，结果通过回调返回。"""
        if self.is_busy:
            self._emit_error("上一次播放还没结束，请稍候")
            return

        def run() -> None:
            try:
                result = self.speak(
                    chinese_text,
                    target_language=target_language,
                    voice_id=voice_id,
                )
            except (TranslationError, SynthesisError, ValueError) as exc:
                self._set_state(SessionState.FAILED, str(exc))
                self._emit_error(str(exc))
                return
            except Exception as exc:
                self._set_state(SessionState.FAILED, str(exc))
                self._emit_error(f"处理失败：{exc}")
                logger.error("speak_async 未预期的异常", exc_info=True)
                return

            if self._on_finished is not None:
                try:
                    self._on_finished(result)
                except Exception:
                    logger.error("完成回调抛出异常", exc_info=True)

        self._worker = threading.Thread(
            target=run, name="realtalk-speak", daemon=True
        )
        self._worker.start()

    def stop(self) -> None:
        """打断当前播放。"""
        self._player.stop()
        self._set_state(SessionState.STOPPED, "已停止")

    def _set_state(self, state: SessionState, detail: str = "") -> None:
        if self._on_state is None:
            return
        try:
            self._on_state(StateEvent(state=state, detail=detail))
        except Exception:
            logger.error("状态回调抛出异常", exc_info=True)

    def _emit_error(self, message: str) -> None:
        logger.error("%s", message)
        if self._on_error is None:
            return
        try:
            self._on_error(ErrorEvent(message=message))
        except Exception:
            logger.error("错误回调抛出异常", exc_info=True)
