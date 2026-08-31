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

import sounddevice as sd

from realtalk.config import CHANNELS, FRAMES_PER_BUFFER, SAMPLE_RATE

logger = logging.getLogger(__name__)

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
        if channels > 0:
            devices.append(
                AudioDevice(
                    index=index,
                    name=str(info.get("name", f"device {index}")),
                    max_input_channels=channels,
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
        self._channels = channels
        self._frames_per_buffer = frames_per_buffer
        self._device = device

        self._queue: queue.Queue[bytes | None] = queue.Queue(maxsize=_MAX_QUEUED_FRAMES)
        self._stream: sd.RawInputStream | None = None
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
            self._stream = sd.RawInputStream(
                samplerate=self._sample_rate,
                blocksize=self._frames_per_buffer,
                device=self._device,
                channels=self._channels,
                dtype="int16",
                callback=self._audio_callback,
            )
            self._stream.start()
        except Exception:
            # 打开设备失败时不能留下已启动的转发线程
            self._running.clear()
            self._queue.put_nowait(None)
            raise

        logger.info(
            "麦克风已开启：%dHz %d声道，每帧 %d 采样点",
            self._sample_rate,
            self._channels,
            self._frames_per_buffer,
        )

    def stop(self) -> None:
        if not self._running.is_set():
            return
        self._running.clear()

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
        try:
            self._queue.put_nowait(bytes(indata))
        except queue.Full:
            self._dropped_frames += 1

    def _pump_loop(self) -> None:
        while True:
            try:
                frame = self._queue.get(timeout=0.5)
            except queue.Empty:
                if not self._running.is_set():
                    return
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
