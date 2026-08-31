"""核心业务逻辑，与界面完全解耦。

ConversationSession 是产品的主入口：它把下面两条单向链路组织成一场
半双工的双语对话。

单向链路各自也能独立使用，命令行工具就直接用它们做诊断：
- ListenSession：语音 -> 实时识别 -> 翻译 -> 文本
- SpeakSession： 文本 -> 翻译 -> TTS -> 播放语音

所有会话都通过回调向外发事件，不依赖 PySide6，因此命令行和 GUI 共用。
"""

from realtalk.core.conversation import (
    ConversationMessage,
    ConversationSession,
    ConversationState,
    ConversationStateEvent,
    Speaker,
)
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
    "ConversationMessage",
    "ConversationSession",
    "ConversationState",
    "ConversationStateEvent",
    "ErrorEvent",
    "ListenSession",
    "SentenceUpdate",
    "SessionState",
    "SpeakSession",
    "SpeechSynthesizerClient",
    "Speaker",
    "StateEvent",
    "TextTranslator",
    "TranslationSource",
]
