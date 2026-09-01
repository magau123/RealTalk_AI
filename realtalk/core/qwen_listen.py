"""Qwen3.5 LiveTranslate 实时语音识别与翻译。"""

from __future__ import annotations

import base64
import logging
import threading
from dataclasses import replace

from dashscope.audio.qwen_omni import (
    MultiModality,
    OmniRealtimeCallback,
    OmniRealtimeConversation,
)
from dashscope.audio.qwen_omni.omni_realtime import TranslationParams

from realtalk.audio.recorder import MicrophoneRecorder
from realtalk.config import Settings, apply_to_dashscope
from realtalk.core.events import (
    ErrorEvent,
    SentenceUpdate,
    SessionState,
    StateEvent,
    TranslationSource,
)
from realtalk.languages import language_name

logger = logging.getLogger(__name__)


class QwenLiveTranslateSession:
    """把 Qwen 实时事件整理成现有界面使用的 SentenceUpdate。"""

    def __init__(
        self,
        settings: Settings,
        *,
        on_sentence,
        on_state=None,
        on_error=None,
        source_language: str = "auto",
        target_language: str = "zh",
        device: int | None = None,
    ) -> None:
        self._settings = settings
        self._on_sentence = on_sentence
        self._on_state = on_state
        self._on_error = on_error
        self._source_language = source_language
        self._target_language = target_language
        self._conversation: OmniRealtimeConversation | None = None
        self._state = SessionState.IDLE
        self._muted = threading.Event()
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        # 服务端把一句话拆成两条流：输入条目给原文，响应给译文。两者各自
        # 有 ID 且顺序一一对应，因此按出现顺序配对成同一句。不能改用
        # speech_started 计数：一个语音轮次里可能产生多个响应，那样后一个
        # 响应会覆盖掉前一句的气泡，界面上就是「只识别出一个词就不动了」。
        self._sentences: dict[int, SentenceUpdate] = {}
        self._response_slots: dict[str, int] = {}
        self._item_slots: dict[str, int] = {}
        self._next_response_slot = 0
        self._next_item_slot = 0
        self._source_done: set[int] = set()
        self._translation_done: set[int] = set()
        self._recorder = MicrophoneRecorder(
            on_frame=self._send_audio,
            on_error=lambda exc: self._emit_error(f"音频采集中断：{exc}"),
            device=device,
        )

    @property
    def state(self) -> SessionState:
        return self._state

    @property
    def strategy_description(self) -> str:
        return (
            f"Qwen3.5 LiveTranslate：{language_name(self._source_language)} → "
            f"{language_name(self._target_language)}"
        )

    def start(self) -> None:
        if self._state in (SessionState.RUNNING, SessionState.CONNECTING):
            return
        apply_to_dashscope(self._settings)
        self._stopping.clear()
        self._set_state(SessionState.CONNECTING, self.strategy_description)

        conversation = OmniRealtimeConversation(
            model=self._settings.asr_model,
            callback=_QwenCallback(self),
            url=self._settings.websocket_url,
            api_key=self._settings.dashscope_api_key,
        )
        try:
            conversation.connect()
            conversation.update_session(
                output_modalities=[MultiModality.TEXT],
                voice="Tina",
                input_audio_transcription_model="qwen3-asr-flash-realtime",
                translation_params=TranslationParams(
                    language=self._target_language
                ),
                turn_detection_silence_duration_ms=(
                    self._settings.max_end_silence
                ),
                turn_detection_threshold=0.2,
            )
            self._conversation = conversation
            self._recorder.start()
        except Exception as exc:
            conversation.close()
            self._set_state(SessionState.FAILED, str(exc))
            self._emit_error(f"连接 Qwen 实时翻译失败：{exc}")
            raise

        self._set_state(SessionState.RUNNING, self.strategy_description)

    def stop(self) -> None:
        if self._state not in (SessionState.RUNNING, SessionState.CONNECTING):
            return
        self._stopping.set()
        self._set_state(SessionState.STOPPING)
        self._recorder.stop()
        conversation = self._conversation
        self._conversation = None
        if conversation is not None:
            try:
                conversation.end_session(timeout=5)
            except Exception:
                logger.warning("结束 Qwen 翻译会话时出错", exc_info=True)
            finally:
                conversation.close()
        self._set_state(SessionState.STOPPED)

    def mute(self) -> None:
        self._muted.set()

    def unmute(self) -> None:
        self._muted.clear()

    @property
    def is_muted(self) -> bool:
        return self._muted.is_set()

    def _send_audio(self, frame: bytes) -> None:
        conversation = self._conversation
        if conversation is None or self._state is not SessionState.RUNNING:
            return
        if self._muted.is_set():
            frame = bytes(len(frame))
        conversation.append_audio(base64.b64encode(frame).decode("ascii"))

    def _handle_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "conversation.item.input_audio_transcription.text":
            self._set_source(
                event.get("item_id"),
                f"{event.get('text', '')}{event.get('stash', '')}",
                event.get("language"),
            )
        elif kind == "conversation.item.input_audio_transcription.completed":
            self._set_source(
                event.get("item_id"),
                event.get("transcript", ""),
                event.get("language"),
                done=True,
            )
        elif kind == "response.text.text":
            self._set_translation(
                event.get("response_id"),
                f"{event.get('text', '')}{event.get('stash', '')}",
            )
        elif kind == "response.text.done":
            self._set_translation(
                event.get("response_id"), event.get("text", ""), done=True
            )
        elif kind == "error":
            error = event.get("error") or {}
            self._handle_error(error.get("message") or str(error))

    def _set_source(
        self, item_id: str | None, text: str, language: str | None, *, done: bool = False
    ) -> None:
        with self._lock:
            slot = self._slot(self._item_slots, item_id, "item")
            update = self._sentence(slot)
            update.source_text = text
            update.source_language = language
            if done:
                self._source_done.add(slot)
            snapshot = self._snapshot(slot, update)
        self._emit_sentence(snapshot)

    def _set_translation(
        self, response_id: str | None, text: str, *, done: bool = False
    ) -> None:
        with self._lock:
            slot = self._slot(self._response_slots, response_id, "response")
            update = self._sentence(slot)
            update.translated_text = text
            if done:
                self._translation_done.add(slot)
            snapshot = self._snapshot(slot, update)
        self._emit_sentence(snapshot)

    def _slot(self, known: dict[str, int], key: str | None, kind: str) -> int:
        """把服务端 ID 映射成句子序号，两条流按各自的出现顺序对齐。"""
        key = key or f"{kind}-anonymous"
        slot = known.get(key)
        if slot is None:
            if kind == "item":
                slot = self._next_item_slot
                self._next_item_slot += 1
            else:
                slot = self._next_response_slot
                self._next_response_slot += 1
            known[key] = slot
        return slot

    def _sentence(self, slot: int) -> SentenceUpdate:
        update = self._sentences.get(slot)
        if update is None:
            update = SentenceUpdate(
                sentence_id=slot,
                target_language=self._target_language,
                translation_source=TranslationSource.QWEN_LIVE,
            )
            self._sentences[slot] = update
        return update

    def _snapshot(self, slot: int, update: SentenceUpdate) -> SentenceUpdate:
        return replace(
            update,
            is_final=slot in self._source_done and slot in self._translation_done,
        )

    def _handle_error(self, detail: str) -> None:
        self._set_state(SessionState.FAILED, detail)
        self._emit_error(f"Qwen 实时翻译返回错误：{detail}")

    def _emit_sentence(self, update: SentenceUpdate) -> None:
        # 尾部静音也会触发一次响应，服务端给的是空白字符而非空串
        if not update.source_text.strip() and not update.translated_text.strip():
            return
        try:
            self._on_sentence(update)
        except Exception:
            logger.error("句子回调抛出异常", exc_info=True)

    def _set_state(self, state: SessionState, detail: str = "") -> None:
        self._state = state
        if self._on_state is not None:
            self._on_state(StateEvent(state=state, detail=detail))

    def _emit_error(self, message: str) -> None:
        logger.error("%s", message)
        if self._on_error is not None:
            self._on_error(ErrorEvent(message=message))


class _QwenCallback(OmniRealtimeCallback):
    def __init__(self, session: QwenLiveTranslateSession) -> None:
        self._session = session

    def on_open(self) -> None:
        logger.info("Qwen3.5 LiveTranslate 连接已建立")

    def on_close(self, code: int | None, message: str | None) -> None:
        logger.info("Qwen3.5 LiveTranslate 连接已关闭：%s %s", code, message)
        if not self._session._stopping.is_set():
            self._session._handle_error(
                f"连接意外关闭（{code or '无状态码'}：{message or '无说明'}）"
            )

    def on_event(self, event: dict) -> None:
        try:
            self._session._handle_event(event)
        except Exception:
            logger.error("处理 Qwen 实时翻译结果时出错", exc_info=True)
