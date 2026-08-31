"""语音合成，基于百炼 CosyVoice。

官方文档：https://help.aliyun.com/zh/model-studio/cosyvoice-python-sdk

两个要点：
1. 系统音色是单语言绑定的，text 的语言必须与 voice 匹配，否则发音会错。
   音色选择由 languages.TTS_VOICES 负责，本模块只管合成。
2. 输出格式选 PCM 而非 MP3：PCM 可以直接喂给声卡，省掉一次解码，
   首字延迟更低。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from dashscope.audio.tts_v2 import AudioFormat, ResultCallback, SpeechSynthesizer

from realtalk.audio.player import PcmStreamPlayer
from realtalk.config import TTS_SAMPLE_RATE, Settings, apply_to_dashscope

logger = logging.getLogger(__name__)

# 与 config.TTS_SAMPLE_RATE 保持一致，播放器按该采样率打开输出流
_AUDIO_FORMAT = AudioFormat.PCM_22050HZ_MONO_16BIT


class SynthesisError(RuntimeError):
    """语音合成失败。"""


class SpeechSynthesizerClient:
    """CosyVoice 合成客户端。

    每次合成都会新建一个 SpeechSynthesizer 实例，这是官方对 call() 的
    要求（「每次调用前需重新实例化」）。
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def synthesize_and_play(
        self,
        text: str,
        *,
        voice: str,
        player: PcmStreamPlayer,
        on_first_package: Callable[[float], None] | None = None,
        timeout: float = 60.0,
    ) -> None:
        """合成并边收边播，阻塞直到音频全部播放完毕。

        on_first_package 会收到首包延迟（毫秒），便于观察实时性。
        """
        stripped = text.strip()
        if not stripped:
            return

        apply_to_dashscope(self._settings)

        completed = threading.Event()
        failure: list[str] = []

        class _Callback(ResultCallback):
            def on_open(self) -> None:
                player.start()

            def on_data(self, data: bytes) -> None:
                player.feed(data)

            def on_complete(self) -> None:
                completed.set()

            def on_error(self, message: object) -> None:
                failure.append(str(message))
                completed.set()

            def on_close(self) -> None:
                completed.set()

        synthesizer = SpeechSynthesizer(
            model=self._settings.tts_model,
            voice=voice,
            format=_AUDIO_FORMAT,
            callback=_Callback(),
        )

        try:
            # 传入 callback 时 call() 以流式模式运行，音频经 on_data 返回
            synthesizer.call(stripped)
        except Exception as exc:
            player.stop()
            raise SynthesisError(f"语音合成请求失败：{exc}") from exc

        if not completed.wait(timeout=timeout):
            player.stop()
            raise SynthesisError(f"语音合成超时（{timeout:.0f} 秒未完成）")

        if failure:
            player.stop()
            raise SynthesisError(f"语音合成失败：{failure[0]}")

        if on_first_package is not None:
            delay = _first_package_delay(synthesizer)
            if delay is not None:
                on_first_package(delay)

        # 音频已全部到达，等待播放队列排空
        player.finish()

    def synthesize_to_bytes(self, text: str, *, voice: str) -> bytes:
        """一次性合成，返回完整 PCM 数据。用于导出文件或单元测试。"""
        stripped = text.strip()
        if not stripped:
            return b""

        apply_to_dashscope(self._settings)
        synthesizer = SpeechSynthesizer(
            model=self._settings.tts_model,
            voice=voice,
            format=_AUDIO_FORMAT,
        )
        try:
            audio = synthesizer.call(stripped)
        except Exception as exc:
            raise SynthesisError(f"语音合成请求失败：{exc}") from exc

        if not audio:
            raise SynthesisError("语音合成返回了空音频")
        return bytes(audio)

    @property
    def sample_rate(self) -> int:
        return TTS_SAMPLE_RATE


def _first_package_delay(synthesizer: SpeechSynthesizer) -> float | None:
    """读取首包延迟。该接口在旧版本 SDK 上可能不存在，取不到就算了。"""
    try:
        return float(synthesizer.get_first_package_delay())
    except Exception:
        return None


def pcm_to_wav(pcm: bytes, *, sample_rate: int = TTS_SAMPLE_RATE) -> bytes:
    """给裸 PCM 加上 WAV 头，便于保存成可直接播放的文件。"""
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return buffer.getvalue()
