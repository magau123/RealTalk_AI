"""麦克风采集。

采集出 16kHz / 单声道 / 16bit 小端 PCM，正是 Gummy 需要的格式，
因此不需要任何重采样。

线程模型（这是本模块存在的主要理由）：
sounddevice 的采集回调运行在 PortAudio 的实时线程上，在那里做任何可能
阻塞的事情——尤其是 WebSocket 发送——都会造成丢帧和爆音。所以回调只做
一件事：把裸字节丢进队列。真正的消费动作由独立的转发线程完成。
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import sounddevice as sd

from realtalk.config import CHANNELS, FRAMES_PER_BUFFER, SAMPLE_RATE

logger = logging.getLogger(__name__)

WASAPI_LOOPBACK_DEVICE = -1

# 队列上限。每帧 100ms，200 帧约 20 秒；正常情况下队列几乎是空的，
# 堆积说明下游发送跟不上，此时丢弃最旧的帧比无限占用内存更合理。
_MAX_QUEUED_FRAMES = 200


@dataclass(frozen=True)
class AudioDevice:
    index: int
    name: str
    max_input_channels: int

    def __str__(self) -> str:
        return f"[{self.index}] {self.name}"


def list_input_devices() -> list[AudioDevice]:
    """列出所有可用于录音的设备。"""
    devices: list[AudioDevice] = []
    for index, info in enumerate(sd.query_devices()):
        channels = int(info.get("max_input_channels", 0))
        host_name = sd.query_hostapis(int(info["hostapi"]))["name"]
        # WDM-KS 会列出大量重复端点，其中不少通过格式检查却无法打开。
        if channels > 0 and host_name != "Windows WDM-KS":
            devices.append(
                AudioDevice(
                    index=index,
                    name=str(info.get("name", f"device {index}")),
                    max_input_channels=channels,
                )
            )
    if _wasapi_loopback_available():
        devices.append(
            AudioDevice(
                index=WASAPI_LOOPBACK_DEVICE,
                name="系统默认扬声器（WASAPI 回环）",
                max_input_channels=2,
            )
        )
    return devices


class MicrophoneRecorder:
    """把麦克风音频以固定大小的 PCM 帧推给回调函数。

    on_frame 在专用的转发线程上被调用，允许在其中执行网络 I/O。
    on_error 用于把后台线程里的异常送回调用方，因为线程里抛出的异常
    不会传播到主线程。
    """

    def __init__(
        self,
        on_frame: Callable[[bytes], None],
        *,
        on_error: Callable[[Exception], None] | None = None,
        sample_rate: int = SAMPLE_RATE,
        channels: int = CHANNELS,
        frames_per_buffer: int = FRAMES_PER_BUFFER,
        device: int | None = None,
    ) -> None:
        self._on_frame = on_frame
        self._on_error = on_error
        self._sample_rate = sample_rate
        self._input_sample_rate = sample_rate
        self._channels = channels
        self._input_channels = channels
        self._frames_per_buffer = frames_per_buffer
        self._device = device

        self._queue: queue.Queue[bytes | None] = queue.Queue(maxsize=_MAX_QUEUED_FRAMES)
        self._stream: sd.RawInputStream | None = None
        self._loopback_audio: object | None = None
        self._loopback_stream: object | None = None
        self._pump: threading.Thread | None = None
        self._running = threading.Event()
        self._dropped_frames = 0

    @property
    def is_running(self) -> bool:
        return self._running.is_set()

    @property
    def dropped_frames(self) -> int:
        """因下游处理不及时而丢弃的帧数，可用于界面上提示性能问题。"""
        return self._dropped_frames

    def start(self) -> None:
        if self._running.is_set():
            return

        self._dropped_frames = 0
        self._drain_queue()
        self._running.set()

        self._pump = threading.Thread(
            target=self._pump_loop, name="realtalk-mic-pump", daemon=True
        )
        self._pump.start()

        try:
            if self._device == WASAPI_LOOPBACK_DEVICE:
                self._start_loopback()
            else:
                self._start_microphone()
        except Exception:
            # 打开设备失败时不能留下已启动的转发线程
            self._running.clear()
            self._queue.put_nowait(None)
            raise

        logger.info(
            "声音输入已开启：采集 %dHz → 发送 %dHz，%d声道",
            self._input_sample_rate,
            self._sample_rate,
            self._channels,
        )

    def stop(self) -> None:
        if not self._running.is_set():
            return
        self._running.clear()

        if self._loopback_stream is not None:
            try:
                self._loopback_stream.stop_stream()
                self._loopback_stream.close()
            except Exception:
                logger.warning("关闭系统声音输入时出错", exc_info=True)
            self._loopback_stream = None
        if self._loopback_audio is not None:
            self._loopback_audio.terminate()
            self._loopback_audio = None

        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # 设备可能已被拔出，关闭失败不应掩盖真正的错误
                logger.warning("关闭录音设备时出错", exc_info=True)
            self._stream = None

        # 哨兵值唤醒可能正阻塞在 get() 上的转发线程
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            self._drain_queue()
            self._queue.put_nowait(None)

        if self._pump is not None:
            self._pump.join(timeout=2.0)
            self._pump = None

        if self._dropped_frames:
            logger.warning("本次录音共丢弃 %d 帧音频", self._dropped_frames)
        logger.info("麦克风已关闭")

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def _audio_callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        """PortAudio 实时线程。只做入队，不做任何阻塞操作。"""
        if status:
            logger.debug("录音状态标志：%s", status)
        if not self._running.is_set():
            return
        self._enqueue_audio(bytes(indata), self._channels)

    def _loopback_callback(
        self, in_data: bytes, frame_count: int, time_info: object, status: int
    ) -> tuple[None, int]:
        import pyaudiowpatch as pyaudio

        if self._running.is_set():
            self._enqueue_audio(in_data, self._input_channels)
        return None, pyaudio.paContinue

    def _enqueue_audio(self, data: bytes, source_channels: int) -> None:
        try:
            if source_channels > 1:
                samples = np.frombuffer(data, dtype="<i2").reshape(-1, source_channels)
                data = samples.mean(axis=1).astype("<i2").tobytes()
            if self._input_sample_rate != self._sample_rate:
                data = _resample_pcm16(
                    data, self._input_sample_rate, self._sample_rate
                )
            self._queue.put_nowait(data)
        except queue.Full:
            self._dropped_frames += 1

    def _start_microphone(self) -> None:
        self._input_sample_rate = _supported_sample_rate(
            self._device, self._sample_rate, self._channels
        )
        input_blocksize = round(
            self._frames_per_buffer * self._input_sample_rate / self._sample_rate
        )
        self._stream = sd.RawInputStream(
            samplerate=self._input_sample_rate,
            blocksize=input_blocksize,
            device=self._device,
            channels=self._channels,
            dtype="int16",
            callback=self._audio_callback,
        )
        self._stream.start()

    def _start_loopback(self) -> None:
        import pyaudiowpatch as pyaudio

        audio = pyaudio.PyAudio()
        try:
            device = audio.get_default_wasapi_loopback()
            self._input_sample_rate = round(float(device["defaultSampleRate"]))
            self._input_channels = int(device["maxInputChannels"])
            input_blocksize = round(
                self._frames_per_buffer
                * self._input_sample_rate
                / self._sample_rate
            )
            stream = audio.open(
                format=pyaudio.paInt16,
                channels=self._input_channels,
                rate=self._input_sample_rate,
                frames_per_buffer=input_blocksize,
                input=True,
                input_device_index=int(device["index"]),
                stream_callback=self._loopback_callback,
            )
        except Exception:
            audio.terminate()
            raise
        self._loopback_audio = audio
        self._loopback_stream = stream

    def _pump_loop(self) -> None:
        while True:
            try:
                frame = self._queue.get(
                    timeout=self._frames_per_buffer / self._sample_rate
                )
            except queue.Empty:
                if not self._running.is_set():
                    return
                # WASAPI 在扬声器停止播放后不再回调；补实时静音让服务端 VAD
                # 能够断句，同时避免 23 秒无数据导致连接被关闭。
                if self._device == WASAPI_LOOPBACK_DEVICE:
                    self._on_frame(bytes(self._frames_per_buffer * 2))
                continue

            if frame is None:
                return
            if not self._running.is_set():
                return

            try:
                self._on_frame(frame)
            except Exception as exc:  # 单帧发送失败不应静默中断整个录音
                logger.error("处理音频帧失败：%s", exc, exc_info=True)
                if self._on_error is not None:
                    self._on_error(exc)
                return

    def __enter__(self) -> MicrophoneRecorder:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()


def _supported_sample_rate(
    device: int | None, requested: int, channels: int
) -> int:
    """优先用服务端要求的采样率，设备不支持时退回其原生采样率。"""
    try:
        sd.check_input_settings(
            device=device, channels=channels, dtype="int16", samplerate=requested
        )
        return requested
    except sd.PortAudioError:
        native = round(float(sd.query_devices(device, "input")["default_samplerate"]))
        sd.check_input_settings(
            device=device, channels=channels, dtype="int16", samplerate=native
        )
        logger.info("输入设备不支持 %dHz，改用原生 %dHz 采集", requested, native)
        return native


def _wasapi_loopback_available() -> bool:
    try:
        import pyaudiowpatch as pyaudio

        audio = pyaudio.PyAudio()
        try:
            audio.get_default_wasapi_loopback()
            return True
        finally:
            audio.terminate()
    except (ImportError, OSError):
        return False


def _resample_pcm16(data: bytes, source_rate: int, target_rate: int) -> bytes:
    """把单声道 16 位 PCM 转为目标采样率。"""
    samples = np.frombuffer(data, dtype="<i2")
    if not len(samples) or source_rate == target_rate:
        return data

    target_count = round(len(samples) * target_rate / source_rate)
    # ponytail: 线性插值足够处理语音识别；若以后追求音乐质量，换成带
    # 抗混叠滤波的 scipy.signal.resample_poly。
    positions = np.arange(target_count) * source_rate / target_rate
    return np.interp(positions, np.arange(len(samples)), samples).astype("<i2").tobytes()
