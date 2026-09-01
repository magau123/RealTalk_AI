"""配置加载。

优先级：进程环境变量 > 项目根目录下的 .env > 本文件中的默认值。

设计原则：API Key 只从环境读取，任何时候都不写入仓库内的文件，
也不出现在日志和异常信息里（见 masked_api_key）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Qwen3.5 LiveTranslate 是官方当前推荐的实时同传模型，内置 Qwen3 ASR。
DEFAULT_ASR_MODEL = "qwen3.5-livetranslate-flash-realtime"
DEFAULT_MT_MODEL = "qwen-mt-flash"
DEFAULT_TTS_MODEL = "cosyvoice-v2"

# 麦克风采集参数。Gummy 要求 16000Hz 及以上，16k 单声道是识别类模型的通用最优输入。
SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16bit PCM

# 每个音频帧的采样点数。100ms 一帧，对应 3200 字节，
# 与官方示例的 stream.read(3200) 一致，也落在「每包 1KB~16KB」的建议区间内。
FRAMES_PER_BUFFER = 1600

# CosyVoice 合成输出的采样率，用于本地播放时初始化输出流。
TTS_SAMPLE_RATE = 22050


class ConfigError(RuntimeError):
    """配置缺失或非法。"""


@dataclass(frozen=True)
class Settings:
    """一次运行所需的全部配置。"""

    dashscope_api_key: str
    asr_model: str = DEFAULT_ASR_MODEL
    mt_model: str = DEFAULT_MT_MODEL
    tts_model: str = DEFAULT_TTS_MODEL
    max_end_silence: int = 800
    websocket_url: str | None = None
    _loaded_from: str = field(default="", repr=False, compare=False)

    @property
    def masked_api_key(self) -> str:
        """用于日志和界面展示的脱敏 Key。"""
        key = self.dashscope_api_key
        if len(key) <= 11:
            return "*" * len(key)
        return f"{key[:6]}{'*' * 8}{key[-4:]}"

    def __repr__(self) -> str:  # 避免 Key 出现在异常堆栈里
        return (
            f"Settings(dashscope_api_key={self.masked_api_key!r}, "
            f"asr_model={self.asr_model!r}, mt_model={self.mt_model!r}, "
            f"tts_model={self.tts_model!r}, max_end_silence={self.max_end_silence})"
        )


def _read_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"环境变量 {name} 需要是整数，当前值为 {raw!r}") from exc
    if not minimum <= value <= maximum:
        raise ConfigError(
            f"环境变量 {name} 需要在 {minimum}~{maximum} 之间，当前值为 {value}"
        )
    return value


def load_settings(*, env_file: str | os.PathLike[str] | None = None) -> Settings:
    """读取配置。缺少 API Key 时抛出带操作指引的 ConfigError。"""
    dotenv_path = Path(env_file) if env_file is not None else PROJECT_ROOT / ".env"
    # override=False：已经存在的进程环境变量优先，方便 CI 和临时覆盖
    loaded = load_dotenv(dotenv_path, override=False)

    api_key = (os.environ.get("DASHSCOPE_API_KEY") or "").strip()
    if not api_key:
        raise ConfigError(
            "未找到 DASHSCOPE_API_KEY。\n"
            f"请把 {PROJECT_ROOT / '.env.example'} 复制为 "
            f"{PROJECT_ROOT / '.env'} 并填入你的百炼 API Key，\n"
            "或直接设置环境变量：\n"
            '  PowerShell:  $env:DASHSCOPE_API_KEY = "sk-..."\n'
            '  bash/zsh:    export DASHSCOPE_API_KEY="sk-..."\n'
            "API Key 获取地址：https://bailian.console.aliyun.com/ 右上角「API-KEY」"
        )
    if api_key.startswith(("sk-xxxx", "your-", "<")):
        raise ConfigError(
            "DASHSCOPE_API_KEY 看起来还是 .env.example 里的占位值，请填入真实的 API Key。"
        )

    return Settings(
        dashscope_api_key=api_key,
        asr_model=os.environ.get("REALTALK_ASR_MODEL") or DEFAULT_ASR_MODEL,
        mt_model=os.environ.get("REALTALK_MT_MODEL") or DEFAULT_MT_MODEL,
        tts_model=os.environ.get("REALTALK_TTS_MODEL") or DEFAULT_TTS_MODEL,
        max_end_silence=_read_int("REALTALK_MAX_END_SILENCE", 800, 200, 6000),
        websocket_url=(os.environ.get("REALTALK_WEBSOCKET_URL") or "").strip() or None,
        _loaded_from=str(dotenv_path) if loaded else "环境变量",
    )


def apply_to_dashscope(settings: Settings) -> None:
    """把配置写入 dashscope 全局状态。

    dashscope SDK 的鉴权与网关地址都是模块级全局变量，构造识别器/合成器时
    才会读取，所以必须在创建任何客户端之前调用一次。
    """
    import dashscope

    dashscope.api_key = settings.dashscope_api_key
    if settings.websocket_url:
        dashscope.base_websocket_api_url = settings.websocket_url
