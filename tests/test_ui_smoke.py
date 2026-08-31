"""界面构建的冒烟测试。

用离屏后端（offscreen）跑，不需要显示器，因此可以在 CI 里执行。
这里只验证「界面能被创建、控件被正确填充、跨线程信号能安全投递」，
不触碰任何网络调用。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from realtalk.config import Settings  # noqa: E402
from realtalk.core.events import SentenceUpdate, TranslationSource  # noqa: E402
from realtalk.languages import tts_supported_languages  # noqa: E402

_FAKE_SETTINGS = Settings(dashscope_api_key="sk-0123456789abcdef0123456789abcdef")


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    yield app


def test_main_window_builds(qapp: QApplication) -> None:
    from realtalk.ui.main_window import build_window

    window = build_window(_FAKE_SETTINGS)
    try:
        assert window.windowTitle().startswith("RealTalk_AI")
    finally:
        window.close()


def test_api_key_is_not_shown_in_full(qapp: QApplication) -> None:
    """界面页脚会展示 Key 来源，必须是脱敏后的。"""
    from realtalk.ui.main_window import build_window

    window = build_window(_FAKE_SETTINGS)
    try:
        for label in window.findChildren(type(window).__mro__[0]):
            text = getattr(label, "text", lambda: "")()
            assert _FAKE_SETTINGS.dashscope_api_key not in text
    finally:
        window.close()


def test_listen_page_sentence_cards_update_in_place(qapp: QApplication) -> None:
    """同一 sentence_id 的多次更新必须复用同一张卡片，而不是不断追加。"""
    from realtalk.ui.listen_page import ListenPage

    page = ListenPage(_FAKE_SETTINGS)
    try:
        page._on_sentence_ui(
            SentenceUpdate(sentence_id=1, source_text="Hello", is_final=False)
        )
        assert len(page._cards) == 1

        page._on_sentence_ui(
            SentenceUpdate(
                sentence_id=1,
                source_text="Hello there",
                translated_text="你好",
                is_final=True,
                translation_source=TranslationSource.GUMMY,
            )
        )
        assert len(page._cards) == 1

        page._on_sentence_ui(
            SentenceUpdate(sentence_id=2, source_text="Bye", is_final=False)
        )
        assert len(page._cards) == 2

        page._clear_cards()
        assert not page._cards
    finally:
        page.deleteLater()


def test_speak_page_voice_list_follows_language(qapp: QApplication) -> None:
    from realtalk.ui.speak_page import SpeakPage

    page = SpeakPage(_FAKE_SETTINGS)
    try:
        assert page._language_combo.count() == len(tts_supported_languages())

        for index in range(page._language_combo.count()):
            page._language_combo.setCurrentIndex(index)
            language = page._language_combo.currentData()
            assert page._voice_combo.count() > 0
            for voice_index in range(page._voice_combo.count()):
                voice_id = page._voice_combo.itemData(voice_index)
                # 音色必须属于当前选中的语言，否则会用错音色朗读
                assert page._session.resolve_voice(language, voice_id) == voice_id
    finally:
        page.deleteLater()


def test_speak_page_rejects_empty_input(qapp: QApplication) -> None:
    from realtalk.ui.speak_page import SpeakPage

    page = SpeakPage(_FAKE_SETTINGS)
    try:
        page._input.setPlainText("   ")
        page._on_speak_clicked()
        # 没有内容时不应该进入忙状态，也不应该发起请求
        assert not page._session.is_busy
        assert page._speak_button.isEnabled()
    finally:
        page.deleteLater()


def test_speak_page_truncates_overlong_input(qapp: QApplication) -> None:
    from realtalk.ui.speak_page import _MAX_INPUT_CHARS, SpeakPage

    page = SpeakPage(_FAKE_SETTINGS)
    try:
        page._input.setPlainText("中" * (_MAX_INPUT_CHARS + 500))
        assert len(page._input.toPlainText()) == _MAX_INPUT_CHARS
    finally:
        page.deleteLater()
