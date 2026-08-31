"""方向一：多语言语音 -> 实时识别 -> 翻译 -> 中文文本。

链路基于百炼 Gummy 实时语音识别与翻译模型，官方文档：
https://help.aliyun.com/zh/model-studio/real-time-python-sdk

## 为什么需要「兜底翻译」

Gummy 能在一条 WebSocket 里同时给出识别文本和译文，省掉一次网络往返，
是这条链路的最优解。但它的翻译方向是**有向矩阵**而非任意互译：能直接
翻成中文的源语种只有英、日、韩、法、德、西、俄、意、粤；葡萄牙语、
印尼语、阿拉伯语、泰语等只能翻到英文。

因此本模块按源语种分三种策略：
1. 源语种已知且矩阵支持 -> 只用 Gummy 内置翻译（快路径，无额外延迟）
2. 源语种为 auto -> 仍开启 Gummy 内置翻译，但对「定稿了却没拿到译文」
   的句子用 Qwen-MT 补译。覆盖了说话人语种事先未知的常见场景。
3. 源语种已知但矩阵不支持 -> 关闭 Gummy 翻译（否则白白计费），
   全部交给 Qwen-MT

补译只针对 is_final 的句子，中间结果不补：中间结果每秒刷新多次，
逐条送去翻译既昂贵又会让界面文本反复跳动。
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable

from dashscope.audio.asr import (
    TranscriptionResult,
    TranslationRecognizerCallback,
    TranslationRecognizerRealtime,
    TranslationResult,
)

from realtalk.audio.recorder import MicrophoneRecorder
from realtalk.config import SAMPLE_RATE, Settings, apply_to_dashscope
from realtalk.core.events import (
    ErrorEvent,
    SentenceUpdate,
    SessionState,
    StateEvent,
    TranslationSource,
)
from realtalk.core.translator import TextTranslator
from realtalk.languages import AUTO, can_gummy_translate, language_name

logger = logging.getLogger(__name__)

SentenceHandler = Callable[[SentenceUpdate], None]
StateHandler = Callable[[StateEvent], None]
ErrorHandler = Callable[[ErrorEvent], None]


class ListenSession:
    """一次「听译」会话。

    所有回调都在后台线程上触发（SDK 的接收线程或内部补译线程），
    GUI 使用时必须自行 marshal 到 UI 线程，不要直接操作控件。
    """

    def __init__(
        self,
        settings: Settings,
        *,
        on_sentence: SentenceHandler,
        on_state: StateHandler | None = None,
        on_error: ErrorHandler | None = None,
        source_language: str = AUTO,
        target_language: str = "zh",
        device: int | None = None,
    ) -> None:
        self._settings = settings
        self._on_sentence = on_sentence
        self._on_state = on_state
        self._on_error = on_error
        self._source_language = source_language
        self._target_language = target_language

        self._use_gummy_translation = (
            source_language == AUTO
            or can_gummy_translate(source_language, target_language)
        )
        self._needs_fallback = not can_gummy_translate(
            source_language, target_language
        )

        self._recognizer: TranslationRecognizerRealtime | None = None
        self._recorder = MicrophoneRecorder(
            on_frame=self._send_audio,
            on_error=lambda exc: self._emit_error(f"音频采集中断：{exc}"),
            device=device,
        )

        self._sentences: dict[int, SentenceUpdate] = {}
        self._lock = threading.Lock()
        self._state = SessionState.IDLE
        self._muted = threading.Event()

        self._translator = TextTranslator(settings) if self._needs_fallback else None
        self._fallback_queue: queue.Queue[SentenceUpdate | None] = queue.Queue()
        self._fallback_worker: threading.Thread | None = None

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def strategy_description(self) -> str:
        """人类可读的当前策略说明，用于界面上向用户交代实际链路。"""
        source_label = language_name(self._source_language)
        target_label = language_name(self._target_language)
        if self._use_gummy_translation and not self._needs_fallback:
            return f"{source_label} → {target_label}：Gummy 识别与翻译一体完成"
        if self._use_gummy_translation:
            return (
                f"{source_label} → {target_label}：Gummy 识别与翻译，"
                f"未覆盖的语种由 {self._settings.mt_model} 补译"
            )
        return (
            f"{source_label} → {target_label}：Gummy 仅转写，"
            f"翻译由 {self._settings.mt_model} 完成"
            f"（Gummy 不支持该方向直译）"
        )

    def start(self) -> None:
        if self._state in (SessionState.RUNNING, SessionState.CONNECTING):
            return

        apply_to_dashscope(self._settings)
        with self._lock:
            self._sentences.clear()
        self._set_state(SessionState.CONNECTING, self.strategy_description)

        if self._needs_fallback:
            self._start_fallback_worker()

        kwargs = {
            "model": self._settings.asr_model,
            "format": "pcm",
            "sample_rate": SAMPLE_RATE,
            "source_language": self._source_language,
            "transcription_enabled": True,
            "translation_enabled": self._use_gummy_translation,
            "max_end_silence": self._settings.max_end_silence,
            "callback": _GummyCallback(self),
        }
        if self._use_gummy_translation:
            # 官方要求该参数为列表；目标语言需与后续 get_translation() 的入参一致
            kwargs["translation_target_languages"] = [self._target_language]

        try:
            self._recognizer = TranslationRecognizerRealtime(**kwargs)
            self._recognizer.start()
        except Exception as exc:
            self._recognizer = None
            self._stop_fallback_worker()
            self._set_state(SessionState.FAILED, str(exc))
            self._emit_error(f"连接语音识别服务失败：{exc}")
            raise

        try:
            self._recorder.start()
        except Exception as exc:
            self._safe_stop_recognizer()
            self._stop_fallback_worker()
            self._set_state(SessionState.FAILED, str(exc))
            self._emit_error(
                f"打开麦克风失败：{exc}\n"
                "请检查系统是否允许本程序访问麦克风，以及是否有可用的录音设备。"
            )
            raise

        self._set_state(SessionState.RUNNING, self.strategy_description)

    def stop(self) -> None:
        if self._state not in (SessionState.RUNNING, SessionState.CONNECTING):
            return
        self._set_state(SessionState.STOPPING)

        # 顺序很重要：先断音频输入，再关识别连接。
        # 反过来会导致录音线程往已关闭的连接上发帧。
        self._recorder.stop()
        self._safe_stop_recognizer()
        self._stop_fallback_worker()
        self._set_state(SessionState.STOPPED)

    def _safe_stop_recognizer(self) -> None:
        if self._recognizer is None:
            return
        try:
            # stop() 会阻塞直到服务端返回 on_complete 或 on_error
            self._recognizer.stop()
        except Exception:
            logger.warning("关闭识别连接时出错", exc_info=True)
        finally:
            self._recognizer = None

    def mute(self) -> None:
        """暂停向服务端发送音频，但保持连接不断。

        对话场景下必需：扬声器正在播放译文语音时，麦克风会把这段声音重新
        收进来。识别器的源语种是固定的，这些「自己说出去的话」会被强行按
        该语种拟合，产生乱码句子。丢弃这段音频比事后过滤可靠得多。
        """
        self._muted.set()

    def unmute(self) -> None:
        self._muted.clear()

    @property
    def is_muted(self) -> bool:
        return self._muted.is_set()

    def _send_audio(self, frame: bytes) -> None:
        recognizer = self._recognizer
        if recognizer is None or self._state is not SessionState.RUNNING:
            return
        if self._muted.is_set():
            return
        recognizer.send_audio_frame(frame)

    # ---- Gummy 回调的处理入口，运行在 SDK 接收线程上 ----

    def _handle_event(
        self,
        transcription_result: TranscriptionResult | None,
        translation_result: TranslationResult | None,
    ) -> None:
        sentence_id = _extract_sentence_id(transcription_result, translation_result)
        if sentence_id is None:
            return

        with self._lock:
            update = self._sentences.setdefault(
                sentence_id,
                SentenceUpdate(
                    sentence_id=sentence_id,
                    source_language=(
                        None if self._source_language == AUTO else self._source_language
                    ),
                    target_language=self._target_language,
                ),
            )

            if transcription_result is not None and transcription_result.text:
                update.source_text = transcription_result.text

            if translation_result is not None:
                translation = translation_result.get_translation(self._target_language)
                if translation is not None and translation.text:
                    update.translated_text = translation.text
                    update.translation_source = TranslationSource.GUMMY

            # 识别与翻译的 is_sentence_end 只在定稿时对齐，以识别侧为准
            is_final = bool(
                transcription_result is not None
                and getattr(transcription_result, "is_sentence_end", False)
            )
            update.is_final = is_final
            snapshot = _copy_update(update)

        self._emit_sentence(snapshot)

        if is_final:
            if self._needs_fallback and not snapshot.translated_text:
                self._fallback_queue.put(snapshot)
            else:
                with self._lock:
                    self._sentences.pop(sentence_id, None)

    def _handle_sdk_error(self, message: object) -> None:
        detail = getattr(message, "message", None) or str(message)
        self._set_state(SessionState.FAILED, str(detail))
        self._emit_error(f"语音识别服务返回错误：{detail}")

    # ---- 兜底翻译 ----

    def _start_fallback_worker(self) -> None:
        if self._fallback_worker is not None:
            return
        self._fallback_worker = threading.Thread(
            target=self._fallback_loop, name="realtalk-fallback-mt", daemon=True
        )
        self._fallback_worker.start()

    def _stop_fallback_worker(self) -> None:
        if self._fallback_worker is None:
            return
        self._fallback_queue.put(None)
        self._fallback_worker.join(timeout=5.0)
        self._fallback_worker = None

    def _fallback_loop(self) -> None:
        assert self._translator is not None
        while True:
            item = self._fallback_queue.get()
            if item is None:
                return
            text = self._translator.translate_quietly(
                item.source_text,
                target_language=self._target_language,
                source_language=self._source_language,
            )
            with self._lock:
                self._sentences.pop(item.sentence_id, None)
            if not text:
                continue
            item.translated_text = text
            item.translation_source = TranslationSource.QWEN_MT
            self._emit_sentence(item)

    # ---- 回调分发，保证一个坏回调不会拖垮整个会话 ----

    def _emit_sentence(self, update: SentenceUpdate) -> None:
        try:
            self._on_sentence(update)
        except Exception:
            logger.error("句子回调抛出异常", exc_info=True)

    def _set_state(self, state: SessionState, detail: str = "") -> None:
        self._state = state
        if self._on_state is None:
            return
        try:
            self._on_state(StateEvent(state=state, detail=detail))
        except Exception:
            logger.error("状态回调抛出异常", exc_info=True)

    def _emit_error(self, message: str, *, recoverable: bool = False) -> None:
        logger.error("%s", message)
        if self._on_error is None:
            return
        try:
            self._on_error(ErrorEvent(message=message, recoverable=recoverable))
        except Exception:
            logger.error("错误回调抛出异常", exc_info=True)


class _GummyCallback(TranslationRecognizerCallback):
    """把 SDK 回调转接到 ListenSession。

    单独成类而不是让 ListenSession 直接继承 TranslationRecognizerCallback，
    是为了避免 SDK 基类的方法名与业务方法名冲突，也让线程边界更清晰。
    """

    def __init__(self, session: ListenSession) -> None:
        super().__init__()
        self._session = session

    def on_open(self) -> None:
        logger.info("Gummy 连接已建立")

    def on_close(self) -> None:
        logger.info("Gummy 连接已关闭")

    def on_complete(self) -> None:
        logger.info("Gummy 任务正常结束")

    def on_error(self, message: object) -> None:
        self._session._handle_sdk_error(message)

    def on_event(
        self,
        request_id: str,
        transcription_result: TranscriptionResult | None,
        translation_result: TranslationResult | None,
        usage: object,
    ) -> None:
        try:
            self._session._handle_event(transcription_result, translation_result)
        except Exception:
            # 绝不让异常穿回 SDK 的接收循环，否则连接会被意外关闭
            logger.error("处理识别结果时出错", exc_info=True)


def _extract_sentence_id(
    transcription_result: TranscriptionResult | None,
    translation_result: TranslationResult | None,
) -> int | None:
    """取出这批结果所属的句子编号。

    TranslationResult 本身不带 sentence_id，只有它内部各语种的 Translation
    对象带，所以识别侧缺失时需要往里翻一层。
    """
    if transcription_result is not None:
        sentence_id = getattr(transcription_result, "sentence_id", None)
        if sentence_id is not None:
            return int(sentence_id)

    if translation_result is not None:
        for code in translation_result.get_language_list() or ():
            translation = translation_result.get_translation(code)
            sentence_id = getattr(translation, "sentence_id", None)
            if sentence_id is not None:
                return int(sentence_id)
    return None


def _copy_update(update: SentenceUpdate) -> SentenceUpdate:
    """回调拿到的必须是快照，否则界面读到的对象会被后续中间结果就地改写。"""
    return SentenceUpdate(
        sentence_id=update.sentence_id,
        source_text=update.source_text,
        translated_text=update.translated_text,
        is_final=update.is_final,
        source_language=update.source_language,
        target_language=update.target_language,
        translation_source=update.translation_source,
    )
