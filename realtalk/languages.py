"""语种定义、Gummy 翻译方向矩阵、CosyVoice 音色映射。

本模块所有数据均来自阿里云官方文档，改动前请先核对来源：
- Gummy 支持语种与翻译方向：
  https://help.aliyun.com/zh/model-studio/real-time-python-sdk
- Qwen-MT 语种命名（用英文全称而非语言代码）：
  https://help.aliyun.com/zh/model-studio/qwen-mt-api
- CosyVoice 音色列表：
  https://help.aliyun.com/zh/model-studio/cosyvoice-voice-list

这里最需要注意的一点：Gummy 的翻译是**有向的**，不是任意语种互译。
例如葡萄牙语只能翻到英文，翻不到中文。所以「翻译成中文」这件事不能
假定 Gummy 一定能做，必须通过 can_gummy_translate() 判断后再决定
是走 Gummy 内置翻译，还是退化为「Gummy 只转写 + Qwen-MT 补翻译」。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

AUTO = "auto"


@dataclass(frozen=True)
class Language:
    code: str
    zh_name: str
    # Qwen-MT 的 translation_options 用英文全称（如 "Japanese"），不认 "ja" 这种代码
    qwen_mt_name: str


_LANGUAGE_LIST: Final[tuple[Language, ...]] = (
    Language("zh", "中文", "Chinese"),
    Language("en", "英语", "English"),
    Language("ja", "日语", "Japanese"),
    Language("ko", "韩语", "Korean"),
    Language("yue", "粤语", "Cantonese"),
    Language("de", "德语", "German"),
    Language("fr", "法语", "French"),
    Language("ru", "俄语", "Russian"),
    Language("es", "西班牙语", "Spanish"),
    Language("it", "意大利语", "Italian"),
    Language("pt", "葡萄牙语", "Portuguese"),
    Language("id", "印尼语", "Indonesian"),
    Language("ar", "阿拉伯语", "Arabic"),
    Language("th", "泰语", "Thai"),
    Language("hi", "印地语", "Hindi"),
    Language("da", "丹麦语", "Danish"),
    Language("ur", "乌尔都语", "Urdu"),
    Language("tr", "土耳其语", "Turkish"),
    Language("nl", "荷兰语", "Dutch"),
    Language("ms", "马来语", "Malay"),
    Language("vi", "越南语", "Vietnamese"),
)

LANGUAGES: Final[Mapping[str, Language]] = MappingProxyType(
    {lang.code: lang for lang in _LANGUAGE_LIST}
)

# Gummy 能够**识别**的语种（source_language 参数取值）。
# 注意这比它能**翻译**的语种少：识别 14 种，翻译侧多支持 hi/da/ur/tr/nl/ms/vi。
GUMMY_ASR_LANGUAGES: Final[frozenset[str]] = frozenset(
    {"zh", "en", "ja", "ko", "yue", "de", "fr", "ru", "es", "it", "pt", "id", "ar", "th"}
)

# Gummy 的翻译方向矩阵，逐条抄自官方文档的 translation_target_languages 参数说明。
GUMMY_TRANSLATION_MATRIX: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        "zh": frozenset({"en", "ja", "ko", "fr", "de", "es", "ru", "it"}),
        "en": frozenset(
            {
                "zh", "ja", "ko", "pt", "fr", "de", "ru", "vi", "es", "nl",
                "da", "ar", "it", "hi", "yue", "tr", "ms", "ur", "id",
            }
        ),
        "ja": frozenset({"th", "en", "zh", "vi", "fr", "it", "de", "es"}),
        "ko": frozenset({"th", "en", "zh", "vi", "fr", "es", "ru", "de"}),
        "fr": frozenset({"th", "en", "ja", "zh", "vi", "de", "it", "es", "ru", "pt"}),
        "de": frozenset({"th", "en", "ja", "zh", "fr", "vi", "ru", "es", "it", "pt"}),
        "es": frozenset({"th", "en", "ja", "zh", "fr", "vi", "it", "de", "ru", "pt"}),
        "ru": frozenset(
            {"th", "en", "ja", "zh", "fr", "vi", "de", "es", "it", "yue", "pt"}
        ),
        "it": frozenset({"th", "en", "ja", "zh", "fr", "vi", "es", "ru", "de"}),
        "yue": frozenset({"zh", "en"}),
        "th": frozenset({"ja", "vi", "fr"}),
        "vi": frozenset({"ja", "fr"}),
        # 以下语种官方只列出了到英文一个方向
        "pt": frozenset({"en"}),
        "id": frozenset({"en"}),
        "ar": frozenset({"en"}),
        "hi": frozenset({"en"}),
        "da": frozenset({"en"}),
        "ur": frozenset({"en"}),
        "tr": frozenset({"en"}),
        "nl": frozenset({"en"}),
        "ms": frozenset({"en"}),
    }
)

# 能被 Gummy 直接翻译成中文的源语种。方向一的「快路径」就是这一批。
SOURCES_TRANSLATABLE_TO_ZH: Final[frozenset[str]] = frozenset(
    code for code, targets in GUMMY_TRANSLATION_MATRIX.items() if "zh" in targets
)


@dataclass(frozen=True)
class Voice:
    voice_id: str
    zh_label: str
    language_code: str


# CosyVoice 系统音色。关键约束（官方原文）：系统音色是**单语言绑定**的，
# text 必须是该音色支持的语言，否则会出现发音错误。所以方向二必须按目标
# 语言切换 voice，不能固定用一个中文音色去念日语。
#
# 只收录了在官方音色列表页确认存在的 cosyvoice-v2 音色。法语/德语/西语/
# 俄语/意语没有对应的系统音色（需要走 v3 系列的声音复刻），因此方向二的
# 可选目标语言比 Qwen-MT 的翻译能力要窄，见 tts_supported_languages()。
TTS_VOICES: Final[Mapping[str, tuple[Voice, ...]]] = MappingProxyType(
    {
        "zh": (
            Voice("longxiaochun_v2", "龙小淳 · 知性女声（中英）", "zh"),
            Voice("loongbella_v2", "Bella · 干练女声（中英）", "zh"),
        ),
        "en": (
            Voice("loongeva_v2", "Eva · 知性女声（英式）", "en"),
            Voice("loongbrian_v2", "Brian · 沉稳男声（英式）", "en"),
            Voice("loongluna_v2", "Luna · 女声（英式）", "en"),
            Voice("loongluca_v2", "Luca · 男声（英式）", "en"),
            Voice("loongemily_v2", "Emily · 女声（英式）", "en"),
            Voice("loongeric_v2", "Eric · 男声（英式）", "en"),
        ),
        "ja": (
            Voice("loongyuuna_v2", "Yuuna · 元气女声", "ja"),
            Voice("loongyuuma_v2", "Yuuma · 干练男声", "ja"),
            Voice("loongtomoka_v2", "Tomoka · 女声", "ja"),
            Voice("loongtomoya_v2", "Tomoya · 男声", "ja"),
        ),
        "ko": (
            Voice("loongjihun_v2", "Jihun · 阳光男声", "ko"),
            Voice("loongkyong_v2", "Kyong · 女声", "ko"),
        ),
    }
)


def language_name(code: str) -> str:
    """返回语种的中文名，未知代码原样回显而不是抛异常。"""
    if code == AUTO:
        return "自动检测"
    lang = LANGUAGES.get(code)
    return lang.zh_name if lang else code


def qwen_mt_name(code: str) -> str:
    """把语言代码转成 Qwen-MT 需要的英文全称。"""
    if code == AUTO:
        return "auto"
    lang = LANGUAGES.get(code)
    if lang is None:
        raise KeyError(f"未知语言代码：{code!r}")
    return lang.qwen_mt_name


def can_gummy_translate(source: str, target: str) -> bool:
    """Gummy 是否支持 source -> target 的内置翻译。

    source 为 auto 时返回 False：此时源语种未知，无法查表确认方向可行，
    调用方应当按「可能不支持」处理并准备兜底翻译。
    """
    if source in (AUTO, target):
        return False
    return target in GUMMY_TRANSLATION_MATRIX.get(source, frozenset())


def tts_supported_languages() -> tuple[str, ...]:
    """有可用 TTS 音色的语种。"""
    return tuple(TTS_VOICES.keys())


def conversation_languages() -> tuple[str, ...]:
    """可用于对话模式的外语。

    对话是双向的，一个语种要能用必须同时满足三个条件：
      1. Gummy 支持「该语种 → 中文」直译（听懂对方）
      2. Gummy 支持「中文 → 该语种」直译（把我的话翻给对方）
      3. 有可用的 TTS 音色（把译文读出来给对方听）

    这里用交集算出来而不是硬编码，是为了将来往 TTS_VOICES 或翻译矩阵里
    加语种时，不会漏掉其中某一个条件而在运行时才暴露问题。

    西班牙语是个典型例子：它满足前两条，但 cosyvoice-v2 没有西语系统音色，
    因此不会出现在结果里。
    """
    return tuple(
        code
        for code in TTS_VOICES
        if code != "zh"
        and can_gummy_translate(code, "zh")
        and can_gummy_translate("zh", code)
    )


def default_voice(language_code: str) -> Voice:
    voices = TTS_VOICES.get(language_code)
    if not voices:
        raise KeyError(
            f"语种 {language_name(language_code)}({language_code}) 没有可用的 "
            f"CosyVoice 系统音色，当前支持：{', '.join(tts_supported_languages())}"
        )
    return voices[0]


def voice_by_id(voice_id: str) -> Voice | None:
    for voices in TTS_VOICES.values():
        for voice in voices:
            if voice.voice_id == voice_id:
                return voice
    return None
