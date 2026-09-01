"""核心层向外发送的事件模型。

界面只需要理解这几个数据类，不需要接触任何 dashscope 类型。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class SessionState(enum.Enum):
    IDLE = "idle"
    CONNECTING = "connecting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class TranslationSource(enum.Enum):
    """译文的来源，界面上可据此提示用户当前走的是哪条链路。"""

    GUMMY = "gummy"          # Gummy 内置翻译，与识别同一条连接
    QWEN_LIVE = "qwen-live"  # Qwen3.5 LiveTranslate 实时翻译
    QWEN_MT = "qwen-mt"      # 兜底：Gummy 只转写，再由文本翻译模型补译
    NONE = "none"            # 尚无译文


@dataclass
class SentenceUpdate:
    """一句话的当前状态。

    同一个 sentence_id 会被多次下发：先是不断刷新的中间结果
    （is_final=False），最后是定稿结果（is_final=True）。界面应当按
    sentence_id 原地更新，而不是每次都追加一行。

    需要注意，官方文档明确说明识别与翻译的中间结果不保证同时到达，
    只有 is_final 为 True 时两者进度才对齐。因此中间态下 translated_text
    可能滞后于 source_text，甚至为空。
    """

    sentence_id: int
    source_text: str = ""
    translated_text: str = ""
    is_final: bool = False
    source_language: str | None = None
    target_language: str = "zh"
    translation_source: TranslationSource = TranslationSource.NONE


@dataclass
class StateEvent:
    state: SessionState
    detail: str = ""


@dataclass
class ErrorEvent:
    message: str
    recoverable: bool = False
