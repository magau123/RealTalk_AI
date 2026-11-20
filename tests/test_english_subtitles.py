"""英文字幕模式的生命周期与流式显示；不访问麦克风或真实 API。"""

from __future__ import annotations

import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from realtalk.config import Settings  # noqa: E402
from realtalk.core.events import SentenceUpdate  # noqa: E402
from realtalk.ui import english_subtitles as subtitles  # noqa: E402


class IdleSession:
    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.input_enabled = True

    def start(self):
        pass

    def stop(self):
        pass

    def set_input_enabled(self, enabled):
        self.input_enabled = enabled


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def wait_for(qapp, condition):
    deadline = time.monotonic() + 3
    while not condition() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.005)
    assert condition()


@pytest.fixture
def dialog(qapp, monkeypatch):
    monkeypatch.setattr(subtitles, "OutputMuteGuard", FakeOutputGuard)
    monkeypatch.setattr(subtitles, "list_input_devices", lambda: [subtitles.AudioDevice(8, "MacBook Air麦克风", 1)])
    monkeypatch.setattr(subtitles, "create_listen_session", IdleSession)
    window = subtitles.EnglishSubtitlesDialog(Settings(dashscope_api_key="fake"))
    wait_for(qapp, lambda: not window._busy)
    yield window
    window.reject()
    wait_for(qapp, lambda: window._finished)
    window.deleteLater()


def test_real_session_requests_only_text_and_chinese_as_source(monkeypatch):
    from realtalk.core import qwen_listen
    from realtalk.core.qwen_listen import QwenLiveTranslateSession

    configuration = {}

    class Conversation:
        def __init__(self, **kwargs):
            pass

        def connect(self):
            pass

        def update_session(self, **kwargs):
            configuration.update(kwargs)

        def end_session(self, **kwargs):
            pass

        def close(self):
            pass

    monkeypatch.setattr(qwen_listen, "OmniRealtimeConversation", Conversation)
    session = QwenLiveTranslateSession(
        Settings(dashscope_api_key="fake"),
        source_language="zh",
        target_language="en",
        on_sentence=lambda update: None,
    )
    monkeypatch.setattr(session._recorder, "start", lambda: None)
    monkeypatch.setattr(session._recorder, "stop", lambda: None)
    session.start()
    try:
        assert [mode.value for mode in configuration["output_modalities"]] == ["text"]
        assert configuration["translation_params"].language == "en"
        assert configuration["input_audio_transcription"]["language"] == "zh"
    finally:
        session.stop()


class FakeOutputGuard:
    def __init__(self): self.muted = False
    def begin(self): self.muted = True
    def end(self): self.muted = False


def complete_turn(dialog, qapp, sentences):
    generation = dialog._generation
    dialog._set_holding_space(True)
    assert dialog._session.input_enabled
    assert dialog._output_guard.muted
    callback = dialog._session.kwargs["on_sentence"]
    for update in sentences: callback(update)
    qapp.processEvents()
    dialog._set_holding_space(False)
    assert not dialog._output_guard.muted
    wait_for(qapp, lambda: dialog._running and dialog._generation > generation)
    return callback


def test_whole_hold_streams_into_one_record_without_losing_earlier_sentences(dialog, qapp):
    dialog._set_holding_space(True)
    callback = dialog._session.kwargs["on_sentence"]
    callback(SentenceUpdate(0, "你好", "Hel", False))
    qapp.processEvents()
    assert dialog._holding_space
    assert dialog._english.toPlainText() == "Hel"
    callback(SentenceUpdate(0, "你好。", "Hello.", True))
    callback(SentenceUpdate(1, "谢谢。", "Thank you.", True))
    qapp.processEvents()
    assert dialog._english.toPlainText() == "Hello. Thank you."
    generation = dialog._generation
    dialog._set_holding_space(False)
    wait_for(qapp, lambda: dialog._running and dialog._generation > generation)
    assert dialog._english.toPlainText().strip() == "Hello. Thank you."
    assert dialog._english.toPlainText().count("Hello.") == 1
    callback(SentenceUpdate(2, "迟到", "Late result", True))
    qapp.processEvents()
    assert "Late result" not in dialog._english.toPlainText()


def test_new_hold_clears_previous_record_immediately(dialog, qapp):
    complete_turn(dialog, qapp, [SentenceUpdate(0, "你好", "Hello.", True)])
    assert dialog._english.toPlainText() == "Hello."
    dialog._set_holding_space(True)
    assert dialog._english.toPlainText() == ""
    assert "你好" not in dialog._chinese.text()
    dialog._session.kwargs["on_sentence"](SentenceUpdate(0, "明天见", "See you tomorrow.", True))
    qapp.processEvents()
    assert dialog._english.toPlainText() == "See you tomorrow."
    dialog._set_holding_space(False)


def test_live_partial_revisions_only_change_current_hold(dialog, qapp):
    complete_turn(dialog, qapp, [SentenceUpdate(0, "你好", "Hello.", True)])
    dialog._set_holding_space(True)
    callback = dialog._session.kwargs["on_sentence"]
    callback(SentenceUpdate(0, "我们", "We", False))
    qapp.processEvents()
    assert dialog._english.toPlainText() == "We"
    callback(SentenceUpdate(0, "我们明天见", "See you tomorrow.", True))
    callback(SentenceUpdate(0, "迟到的临时结果", "Wrong revision", False))
    qapp.processEvents()
    assert dialog._english.toPlainText() == "See you tomorrow."
    dialog._set_holding_space(False)


def test_final_tail_received_during_stop_is_included(dialog, qapp):
    session = dialog._session
    callback = session.kwargs["on_sentence"]
    session.stop = lambda: callback(SentenceUpdate(1, "再见", "Goodbye.", True))
    complete_turn(dialog, qapp, [SentenceUpdate(0, "你好", "Hello.", True)])
    assert dialog._english.toPlainText().strip() == "Hello. Goodbye."


def test_virtual_system_and_teams_inputs_are_excluded(qapp, monkeypatch):
    monkeypatch.setattr(subtitles, "OutputMuteGuard", FakeOutputGuard)
    monkeypatch.setattr(subtitles, "create_listen_session", IdleSession)
    monkeypatch.setattr(subtitles, "list_input_devices", lambda: [
        subtitles.AudioDevice(-2, "系统声音", 2),
        subtitles.AudioDevice(3, "Microsoft Teams Audio", 1),
        subtitles.AudioDevice(4, "BlackHole 2ch", 2),
        subtitles.AudioDevice(8, "MacBook Air麦克风", 1),
    ])
    window = subtitles.EnglishSubtitlesDialog(Settings(dashscope_api_key="fake"))
    wait_for(qapp, lambda: window._running)
    assert window._microphones == {8: "MacBook Air麦克风"}
    assert window._session.kwargs["device"] == 8
    window.reject()
    wait_for(qapp, lambda: window._finished)


def test_speaker_mute_failure_prevents_microphone_input(dialog):
    def fail(): raise RuntimeError("output unavailable")
    dialog._output_guard.begin = fail
    dialog._set_holding_space(True)
    assert not dialog._session.input_enabled
    assert not dialog._utterance_active
    assert "output unavailable" in dialog._status.text()


def test_local_focus_loss_finishes_and_restores_output(dialog, qapp):
    from PySide6.QtCore import QEvent
    generation = dialog._generation
    dialog._set_holding_space(True)
    qapp.sendEvent(dialog, QEvent(QEvent.Type.WindowDeactivate))
    assert not dialog._output_guard.muted
    wait_for(qapp, lambda: dialog._running and dialog._generation > generation)


def test_capture_gate_preserves_queued_speech_and_silences_new_input():
    from realtalk.audio.recorder import MicrophoneRecorder
    recorder = MicrophoneRecorder(on_frame=lambda data: None)
    speech = b"\x01\x02" * 800
    recorder.set_input_enabled(True)
    recorder._enqueue_audio(speech, 1)
    recorder.set_input_enabled(False)
    recorder._enqueue_audio(speech, 1)
    assert recorder._queue.get_nowait() == speech
    assert recorder._queue.get_nowait() == bytes(len(speech))


def test_flush_before_finish_preserves_tail_audio(dialog, qapp):
    order = []
    callback = dialog._session.kwargs['on_sentence']
    dialog._session.finish_input = lambda: order.append('flushed')
    def stop():
        assert order == ['flushed']
        callback(SentenceUpdate(0, '完整的尾句', 'The complete last sentence.', True))
    dialog._session.stop = stop
    complete_turn(dialog, qapp, [])
    assert 'The complete last sentence.' in dialog._english.toPlainText()


@pytest.mark.parametrize('previous', [0, 1])
def test_output_guard_restores_original_mute_state(monkeypatch, previous):
    from realtalk.audio import output_guard
    class Function:
        def __call__(self, *args): return 0
    class Audio:
        AudioObjectGetPropertyData = Function()
        AudioObjectSetPropertyData = Function()
    monkeypatch.setattr(output_guard.sys, 'platform', 'darwin')
    monkeypatch.setattr(output_guard.ct, 'CDLL', lambda name: Audio())
    guard = output_guard.OutputMuteGuard()
    reads = iter([42, previous])
    monkeypatch.setattr(guard, '_read', lambda device, address, value: setattr(value, 'value', next(reads)))
    writes = []
    monkeypatch.setattr(guard, '_write', lambda device, address, value: writes.append((device, value)))
    guard.begin()
    guard.begin()
    guard.end()
    assert writes == ([(42, 1), (42, 0)] if previous == 0 else [(42, 1)])
    assert guard._saved is None
