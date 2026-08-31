"""核心业务逻辑，与界面完全解耦。

两个方向各有一个会话对象：
- ListenSession：多语言语音 -> 实时识别 -> 翻译 -> 中文文本
- SpeakSession： 中文文本 -> 翻译 -> TTS -> 播放目标语言语音

它们都通过回调向外发事件，不依赖 PySide6，因此命令行和 GUI 可以共用。
"""

from realtalk.core.events import (
    ErrorEvent,
    SentenceUpdate,
    SessionState,
    StateEvent,
    TranslationSource,
)
from realtalk.core.listen import ListenSession
from realtalk.core.speak import SpeakSession
from realtalk.core.translator import TextTranslator
from realtalk.core.tts import SpeechSynthesizerClient

__all__ = [
    "ErrorEvent",
    "ListenSession",
    "SentenceUpdate",
    "SessionState",
    "SpeakSession",
    "SpeechSynthesizerClient",
    "StateEvent",
    "TextTranslator",
    "TranslationSource",
]
