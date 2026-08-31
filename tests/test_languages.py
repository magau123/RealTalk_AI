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
    conversation_languages,
    default_voice,
    language_name,
    looks_like_chinese,
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


@pytest.mark.parametrize(
    "text",
    [
        "你好，请问洗手间在哪里？",
        "我想订一张去北京的票",
        "好的",
        "我要去 Starbucks 买咖啡",  # 中英混说，主体是中文
    ],
)
def test_chinese_is_detected(text: str) -> None:
    assert looks_like_chinese(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Where is the restroom?",
        "Could you tell me how to get to the station",
        "すみません、駅はどこですか",   # 含假名，日文
        "コーヒーをください",           # 片假名
        "화장실이 어디예요",             # 谚文
        "Où est la gare",               # 带变音符的拉丁字母
    ],
)
def test_foreign_is_detected(text: str) -> None:
    assert looks_like_chinese(text) is False


def test_kana_is_checked_before_han() -> None:
    """日文和中文共用汉字区块，必须先查假名。

    先查汉字的话，「これは本です」会因为含「本」被误判成中文，
    于是系统会把对方说的日语当成我的话，翻成日语再念给对方听。
    """
    assert looks_like_chinese("これは本です") is False
    assert looks_like_chinese("東京駅に行きます") is False


@pytest.mark.parametrize("text", ["", "   ", "？？", "123", "..."])
def test_undecidable_text_returns_none(text: str) -> None:
    """无法判断时必须返回 None，让调用方沿用上一次判定而不是瞎猜。"""
    assert looks_like_chinese(text) is None


def test_conversation_languages_satisfy_all_three_conditions() -> None:
    """对话模式的语种必须双向可直译且有音色，任缺一条都不能出现在列表里。"""
    for code in conversation_languages():
        assert can_gummy_translate(code, "zh"), f"{code} 无法翻译成中文"
        assert can_gummy_translate("zh", code), f"中文无法翻译成 {code}"
        assert code in TTS_VOICES, f"{code} 没有可用音色"


def test_chinese_is_not_a_conversation_language() -> None:
    """中文是「我」这一侧固定的语言，不能作为对方的语言。"""
    assert "zh" not in conversation_languages()


def test_english_and_japanese_are_conversation_ready() -> None:
    languages = conversation_languages()
    assert "en" in languages
    assert "ja" in languages


def test_spanish_is_excluded_for_lack_of_voice() -> None:
    """西班牙语双向翻译都支持，但 cosyvoice-v2 没有西语系统音色。

    这条断言是为了把「翻译能力」和「朗读能力」两件事的区别固定下来：
    只看翻译矩阵会误以为西语可用，实际上译文没法读出来。
    """
    assert can_gummy_translate("es", "zh")
    assert can_gummy_translate("zh", "es")
    assert "es" not in TTS_VOICES
    assert "es" not in conversation_languages()
