"""对话会话的测试，全部离线运行。

覆盖三件在真机上不容易稳定复现、但出问题代价很高的事：
  1. 朗读期间麦克风必须被静音，否则扬声器里的外语会被重新收进来
  2. 每轮的 sentence_id 都从 0 重新开始，消息标识必须带轮次前缀
  3. 只有「我」说的话才需要朗读，且必须等定稿
"""

from __future__ import annotations

import queue

import pytest

from realtalk.config import Settings
from realtalk.core.conversation import (
    ConversationMessage,
    ConversationSession,
    Speaker,
)
from realtalk.core.events import SentenceUpdate, SessionState
from realtalk.core.listen import ListenSession
from realtalk.languages import conversation_languages

_FAKE_SETTINGS = Settings(dashscope_api_key="sk-0123456789abcdef0123456789abcdef")


class _FakeRecognizer:
    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def send_audio_frame(self, frame: bytes) -> None:
        self.frames.append(frame)


# ---- 静音（防回声） ----


def _running_listen_session() -> tuple[ListenSession, _FakeRecognizer]:
    session = ListenSession(
        _FAKE_SETTINGS, on_sentence=lambda _: None, source_language="en"
    )
    recognizer = _FakeRecognizer()
    session._recognizer = recognizer
    session._state = SessionState.RUNNING
    return session, recognizer


def test_audio_flows_when_not_muted() -> None:
    session, recognizer = _running_listen_session()
    session._send_audio(b"\x01\x02")
    assert recognizer.frames == [b"\x01\x02"]


def test_muting_replaces_microphone_audio_with_silence() -> None:
    """朗读译文时若不屏蔽上行，识别器会把扬声器的声音当成有人在说话。"""
    session, recognizer = _running_listen_session()

    session.mute()
    assert session.is_muted
    session._send_audio(b"\x01\x02\x03\x04")
    assert recognizer.frames == [b"\x00\x00\x00\x00"]

    session.unmute()
    assert not session.is_muted
    session._send_audio(b"\x05\x06")
    assert recognizer.frames[-1] == b"\x05\x06"


def test_muting_keeps_sending_frames_to_avoid_idle_timeout() -> None:
    """静音期间必须继续发帧。

    服务端 23 秒收不到数据就断开连接（SDK 里同样硬编码了这个值）。
    念一段长句子期间若真的停发，连接会被饿死，用户下一句话直接丢失。
    """
    session, recognizer = _running_listen_session()
    session.mute()

    for _ in range(5):
        session._send_audio(b"\x11" * 3200)

    assert len(recognizer.frames) == 5
    assert all(frame == bytes(3200) for frame in recognizer.frames)


def test_audio_is_dropped_when_not_running() -> None:
    session, recognizer = _running_listen_session()
    session._state = SessionState.STOPPED
    session._send_audio(b"\x01\x02")
    assert recognizer.frames == []


# ---- 会话构造与校验 ----


@pytest.fixture
def session() -> ConversationSession:
    created = ConversationSession(
        _FAKE_SETTINGS, foreign_language="en", on_message=lambda _: None
    )
    # 朗读线程会真的去调合成接口。把队列换成一个没人消费的新队列，
    # 让测试能安全地检查「什么被排进了朗读队列」。原线程仍阻塞在旧队列上。
    created._speech_queue = queue.Queue()
    yield created


def test_rejects_language_without_full_conversation_support() -> None:
    """西班牙语双向可译但没有音色，必须在构造时就拒绝而不是运行时才炸。"""
    with pytest.raises(ValueError) as exc_info:
        ConversationSession(
            _FAKE_SETTINGS, foreign_language="es", on_message=lambda _: None
        )
    assert "对话" in str(exc_info.value)


def test_rejects_voice_that_does_not_match_language() -> None:
    """用日语音色念英文译文不会报错，只会念出错误发音，必须提前拦住。"""
    with pytest.raises(ValueError, match="不能用来朗读"):
        ConversationSession(
            _FAKE_SETTINGS,
            foreign_language="en",
            voice_id="loongyuuna_v2",
            on_message=lambda _: None,
        )


def test_default_voice_matches_foreign_language() -> None:
    for code in conversation_languages():
        created = ConversationSession(
            _FAKE_SETTINGS, foreign_language=code, on_message=lambda _: None
        )
        from realtalk.languages import voice_by_id

        assert voice_by_id(created.voice).language_code == code


def test_shutdown_is_safe_without_any_turn() -> None:
    created = ConversationSession(
        _FAKE_SETTINGS, foreign_language="en", on_message=lambda _: None
    )
    created.shutdown(drain_timeout=1.0)
    assert created.active_speaker is None


# ---- 消息构造 ----


def _emit(
    session: ConversationSession,
    speaker: Speaker,
    *,
    turn: int,
    sentence_id: int,
    source: str,
    translated: str,
    is_final: bool,
) -> ConversationMessage:
    captured: list[ConversationMessage] = []
    session._on_message = captured.append
    session._handle_sentence(
        SentenceUpdate(
            sentence_id=sentence_id,
            source_text=source,
            translated_text=translated,
            is_final=is_final,
        ),
        speaker=speaker,
        turn=turn,
    )
    return captured[-1]


def test_message_id_includes_turn_to_avoid_collision(
    session: ConversationSession,
) -> None:
    """Gummy 的 sentence_id 每条新连接都从 0 开始，只用它会让后一轮覆盖前一轮。"""
    first = _emit(
        session,
        Speaker.FOREIGN,
        turn=1,
        sentence_id=0,
        source="Hello",
        translated="你好",
        is_final=True,
    )
    second = _emit(
        session,
        Speaker.ME,
        turn=2,
        sentence_id=0,
        source="你好",
        translated="Hello",
        is_final=True,
    )
    assert first.message_id != second.message_id


def test_language_direction_is_set_per_speaker(session: ConversationSession) -> None:
    foreign = _emit(
        session,
        Speaker.FOREIGN,
        turn=1,
        sentence_id=0,
        source="Hi",
        translated="嗨",
        is_final=True,
    )
    assert foreign.original_language == "en"
    assert foreign.translated_language == "zh"

    mine = _emit(
        session,
        Speaker.ME,
        turn=2,
        sentence_id=0,
        source="嗨",
        translated="Hi",
        is_final=True,
    )
    assert mine.original_language == "zh"
    assert mine.translated_language == "en"


# ---- 朗读排队规则 ----


def test_my_final_sentence_is_queued_for_speech(session: ConversationSession) -> None:
    _emit(
        session,
        Speaker.ME,
        turn=1,
        sentence_id=0,
        source="你好",
        translated="Hello",
        is_final=True,
    )
    assert session._speech_queue.qsize() == 1


def test_foreign_speech_is_never_read_aloud(session: ConversationSession) -> None:
    """对方的话只需要显示中文，读出来毫无意义还会干扰对话。"""
    _emit(
        session,
        Speaker.FOREIGN,
        turn=1,
        sentence_id=0,
        source="Hello",
        translated="你好",
        is_final=True,
    )
    assert session._speech_queue.empty()


def test_intermediate_results_are_not_read_aloud(
    session: ConversationSession,
) -> None:
    """中间结果每秒刷新多次，逐条合成会让对方听到不断被打断的半句话。"""
    _emit(
        session,
        Speaker.ME,
        turn=1,
        sentence_id=0,
        source="你好，请",
        translated="Hello, please",
        is_final=False,
    )
    assert session._speech_queue.empty()


def test_final_without_translation_is_still_queued(
    session: ConversationSession,
) -> None:
    """自动模式下我的中文入队时还没有译文，朗读线程会在合成前补翻译。"""
    _emit(
        session,
        Speaker.ME,
        turn=1,
        sentence_id=0,
        source="你好",
        translated="",
        is_final=True,
    )
    assert session._speech_queue.qsize() == 1


def test_empty_sentence_is_not_queued(session: ConversationSession) -> None:
    """既没有原文也没有译文时无话可说，入队只会浪费一次翻译请求。"""
    _emit(
        session,
        Speaker.ME,
        turn=1,
        sentence_id=0,
        source="",
        translated="",
        is_final=True,
    )
    assert session._speech_queue.empty()


# ---- 自动模式 ----


def _auto_emit(
    session: ConversationSession, text: str, *, sentence_id: int = 0, translated: str = ""
) -> ConversationMessage | None:
    captured: list[ConversationMessage] = []
    session._on_message = captured.append
    session._handle_auto_sentence(
        SentenceUpdate(
            sentence_id=sentence_id,
            source_text=text,
            translated_text=translated,
            is_final=True,
        ),
        1,
    )
    return captured[-1] if captured else None


def test_auto_routes_chinese_to_me(session: ConversationSession) -> None:
    message = _auto_emit(session, "请问洗手间在哪里")
    assert message is not None
    assert message.speaker is Speaker.ME


def test_auto_routes_foreign_to_them(session: ConversationSession) -> None:
    message = _auto_emit(session, "Where is the restroom", translated="洗手间在哪里")
    assert message is not None
    assert message.speaker is Speaker.FOREIGN
    assert message.translated_text == "洗手间在哪里"


def test_auto_discards_gummy_translation_for_my_speech(
    session: ConversationSession,
) -> None:
    """自动模式的翻译目标固定为中文，我说中文时 Gummy 在做中译中。

    那个结果若被当成译文，会让外语音色去念中文，对方完全听不懂。
    """
    message = _auto_emit(session, "你好", translated="你好")
    assert message is not None
    assert message.translated_text == ""


def test_auto_defers_undecidable_sentences(session: ConversationSession) -> None:
    """只识别出标点时看不出是谁在说，应当等后续结果而不是猜。"""
    assert _auto_emit(session, "？？") is None
    assert session._speech_queue.empty()


def test_auto_locks_speaker_for_the_whole_sentence(
    session: ConversationSession,
) -> None:
    """判定一旦做出就锁定，否则中间结果会让气泡在左右之间来回跳。"""
    first = _auto_emit(session, "Hello", sentence_id=7)
    assert first is not None and first.speaker is Speaker.FOREIGN

    # 后续片段即便看着像中文，也必须沿用同一句已锁定的判定
    later = _auto_emit(session, "Hello 你好", sentence_id=7)
    assert later is not None and later.speaker is Speaker.FOREIGN


def test_speaker_lock_is_reset_between_runs(session: ConversationSession) -> None:
    """sentence_id 每条新连接都从 0 开始，上一场的判定不能延续到下一场。"""
    _auto_emit(session, "Hello", sentence_id=0)
    assert session._sentence_speakers[0] is Speaker.FOREIGN

    session._begin_turn()
    assert session._sentence_speakers == {}


def test_replay_only_accepts_my_translated_messages(
    session: ConversationSession,
) -> None:
    mine = ConversationMessage(
        message_id="t1-s0", speaker=Speaker.ME, translated_text="Hello"
    )
    foreign = ConversationMessage(
        message_id="t1-s1", speaker=Speaker.FOREIGN, translated_text="你好"
    )
    empty = ConversationMessage(message_id="t1-s2", speaker=Speaker.ME)

    session.replay(foreign)
    session.replay(empty)
    assert session._speech_queue.empty()

    session.replay(mine)
    assert session._speech_queue.qsize() == 1
