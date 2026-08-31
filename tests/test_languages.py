"""语言矩阵与音色映射的测试。

这些断言的意义在于：Gummy 的翻译方向矩阵是从官方文档手抄进代码的，
一旦有人改错一个语种，方向一的降级判断就会失效——本该补译的语种被
认为可以直译，结果译文永远为空。所以矩阵本身需要被测试锁住。
"""

from __future__ import annotations

import pytest

from realtalk.languages import (
    AUTO,
    GUMMY_ASR_LANGUAGES,
    GUMMY_TRANSLATION_MATRIX,
    LANGUAGES,
    SOURCES_TRANSLATABLE_TO_ZH,
    TTS_VOICES,
    can_gummy_translate,
    default_voice,
    language_name,
    qwen_mt_name,
    tts_supported_languages,
    voice_by_id,
)


def test_matrix_only_references_known_languages() -> None:
    for source, targets in GUMMY_TRANSLATION_MATRIX.items():
        assert source in LANGUAGES, f"矩阵中出现未定义的源语种 {source}"
        for target in targets:
            assert target in LANGUAGES, f"矩阵中出现未定义的目标语种 {target}"


def test_asr_languages_are_known() -> None:
    assert set(LANGUAGES) >= GUMMY_ASR_LANGUAGES


def test_no_self_translation_in_matrix() -> None:
    for source, targets in GUMMY_TRANSLATION_MATRIX.items():
        assert source not in targets


@pytest.mark.parametrize(
    "source", ["en", "ja", "ko", "fr", "de", "es", "ru", "it", "yue"]
)
def test_languages_that_gummy_can_translate_to_chinese(source: str) -> None:
    assert can_gummy_translate(source, "zh")
    assert source in SOURCES_TRANSLATABLE_TO_ZH


@pytest.mark.parametrize("source", ["pt", "id", "ar", "th", "vi", "hi", "nl", "ms"])
def test_languages_that_need_fallback_translation(source: str) -> None:
    """这些语种官方未提供到中文的直译方向，必须走兜底翻译。"""
    assert not can_gummy_translate(source, "zh")
    assert source not in SOURCES_TRANSLATABLE_TO_ZH


def test_auto_source_is_never_treated_as_translatable() -> None:
    """源语种未知时无法查表确认方向，必须按「需要兜底」处理。"""
    assert not can_gummy_translate(AUTO, "zh")


def test_same_language_is_not_a_translation() -> None:
    assert not can_gummy_translate("zh", "zh")


def test_unknown_source_language_does_not_raise() -> None:
    assert not can_gummy_translate("xx", "zh")


def test_qwen_mt_uses_english_full_names() -> None:
    assert qwen_mt_name("ja") == "Japanese"
    assert qwen_mt_name("zh") == "Chinese"
    assert qwen_mt_name(AUTO) == "auto"
    with pytest.raises(KeyError):
        qwen_mt_name("not-a-language")


def test_language_name_falls_back_to_code() -> None:
    assert language_name("ja") == "日语"
    assert language_name(AUTO) == "自动检测"
    assert language_name("xx") == "xx"


def test_every_voice_is_tagged_with_its_own_language() -> None:
    """音色与语言的对应关系是方向二发音正确的前提。"""
    for code, voices in TTS_VOICES.items():
        assert voices, f"{code} 没有配置任何音色"
        for voice in voices:
            assert voice.language_code == code


def test_voice_ids_are_unique() -> None:
    ids = [voice.voice_id for voices in TTS_VOICES.values() for voice in voices]
    assert len(ids) == len(set(ids))


def test_default_voice_matches_language() -> None:
    for code in tts_supported_languages():
        assert default_voice(code).language_code == code


def test_default_voice_rejects_unsupported_language() -> None:
    with pytest.raises(KeyError):
        default_voice("th")


def test_voice_lookup() -> None:
    assert voice_by_id("loongyuuna_v2").language_code == "ja"
    assert voice_by_id("does-not-exist") is None
