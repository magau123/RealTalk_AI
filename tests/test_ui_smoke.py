"""界面构建的冒烟测试。

用离屏后端（offscreen）跑，不需要显示器，因此可以在 CI 里执行。
这里只验证「界面能被创建、控件被正确填充、消息能就地刷新」，不触碰
任何网络调用——对话会话是在点击按钮时才惰性创建的，构造页面本身不联网。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from realtalk.config import Settings  # noqa: E402
from realtalk.core.conversation import ConversationMessage, Speaker  # noqa: E402
from realtalk.languages import TTS_VOICES, conversation_languages  # noqa: E402

_FAKE_SETTINGS = Settings(dashscope_api_key="sk-0123456789abcdef0123456789abcdef")


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def _message(
    message_id: str,
    speaker: Speaker,
    original: str = "",
    translated: str = "",
    *,
    is_final: bool = False,
    is_speaking: bool = False,
    has_spoken: bool = False,
) -> ConversationMessage:
    return ConversationMessage(
        message_id=message_id,
        speaker=speaker,
        original_text=original,
        translated_text=translated,
        is_final=is_final,
        original_language="en" if speaker is Speaker.FOREIGN else "zh",
        translated_language="zh" if speaker is Speaker.FOREIGN else "en",
        is_speaking=is_speaking,
        has_spoken=has_spoken,
    )


def test_main_window_builds(qapp: QApplication) -> None:
    from realtalk.ui.main_window import build_window

    window = build_window(_FAKE_SETTINGS)
    try:
        assert "RealTalk_AI" in window.windowTitle()
    finally:
        window.close()


def test_api_key_is_never_rendered_in_full(qapp: QApplication) -> None:
    from realtalk.ui.main_window import build_window

    window = build_window(_FAKE_SETTINGS)
    try:
        for label in window.findChildren(QLabel):
            assert _FAKE_SETTINGS.dashscope_api_key not in label.text()
    finally:
        window.close()


def test_language_choices_are_conversation_capable(qapp: QApplication) -> None:
    """下拉框里只能出现双向可译且有音色的语种，否则用户选了才发现不能用。"""
    from realtalk.ui.conversation_page import ConversationPage

    page = ConversationPage(_FAKE_SETTINGS)
    try:
        codes = [
            page._language_combo.itemData(i)
            for i in range(page._language_combo.count())
        ]
        assert codes == list(conversation_languages())
        assert "zh" not in codes
        # 西班牙语没有 cosyvoice-v2 系统音色，不应出现
        assert "es" not in codes
    finally:
        page.deleteLater()


def test_voice_choices_follow_selected_language(qapp: QApplication) -> None:
    from realtalk.ui.conversation_page import ConversationPage

    page = ConversationPage(_FAKE_SETTINGS)
    try:
        for index in range(page._language_combo.count()):
            page._language_combo.setCurrentIndex(index)
            language = page._language_combo.currentData()
            expected = [v.voice_id for v in TTS_VOICES[language]]
            actual = [
                page._voice_combo.itemData(i)
                for i in range(page._voice_combo.count())
            ]
            assert actual == expected
    finally:
        page.deleteLater()


def test_bubbles_update_in_place(qapp: QApplication) -> None:
    """同一 message_id 的多次更新必须复用同一个气泡，而不是不断追加。"""
    from realtalk.ui.conversation_page import ConversationPage

    page = ConversationPage(_FAKE_SETTINGS)
    try:
        page._on_message_ui(_message("t1-s0", Speaker.FOREIGN, "Hello"))
        assert len(page._bubbles) == 1

        page._on_message_ui(
            _message("t1-s0", Speaker.FOREIGN, "Hello there", "你好", is_final=True)
        )
        assert len(page._bubbles) == 1

        page._on_message_ui(_message("t2-s0", Speaker.ME, "你好", "Hello"))
        assert len(page._bubbles) == 2

        page._clear_transcript()
        assert not page._bubbles
    finally:
        page.deleteLater()


def test_same_sentence_id_across_turns_does_not_collide(qapp: QApplication) -> None:
    """Gummy 的 sentence_id 每轮都从 0 重新开始，轮次前缀必须能区分开。"""
    from realtalk.ui.conversation_page import ConversationPage

    page = ConversationPage(_FAKE_SETTINGS)
    try:
        page._on_message_ui(_message("t1-s0", Speaker.FOREIGN, "Hello", "你好"))
        page._on_message_ui(_message("t2-s0", Speaker.ME, "你好", "Hello"))
        page._on_message_ui(_message("t3-s0", Speaker.FOREIGN, "Bye", "再见"))
        assert len(page._bubbles) == 3
    finally:
        page.deleteLater()


def test_replay_button_only_appears_for_my_spoken_messages(qapp: QApplication) -> None:
    """对方的话是听来的、没有音频可放；我的话也要念过之后才谈得上重放。"""
    from realtalk.ui.conversation_page import ConversationPage

    page = ConversationPage(_FAKE_SETTINGS)
    try:
        page._on_message_ui(
            _message("t1-s0", Speaker.FOREIGN, "Hello", "你好", is_final=True)
        )
        page._on_message_ui(
            _message("t2-s0", Speaker.ME, "你好", "Hello", is_final=True)
        )
        page._on_message_ui(
            _message(
                "t2-s1", Speaker.ME, "再见", "Bye", is_final=True, has_spoken=True
            )
        )

        # 用 isHidden 而不是 isVisible：离屏测试里窗口从未 show 过，
        # isVisible 要求整条祖先链都可见，永远是 False。
        assert page._bubbles["t1-s0"]._replay.isHidden()
        assert page._bubbles["t2-s0"]._replay.isHidden()
        assert not page._bubbles["t2-s1"]._replay.isHidden()
    finally:
        page.deleteLater()


def test_turn_buttons_reflect_language(qapp: QApplication) -> None:
    from realtalk.ui.conversation_page import ConversationPage

    page = ConversationPage(_FAKE_SETTINGS)
    try:
        page._language_combo.setCurrentIndex(0)
        assert "对方说" in page._foreign_button.text()
        assert "我说" in page._my_button.text()
        assert "中文" in page._my_button.text()
    finally:
        page.deleteLater()
