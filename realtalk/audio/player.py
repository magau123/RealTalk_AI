"""PCM 流式播放。

CosyVoice 的流式合成通过 on_data(bytes) 一小块一小块地吐出音频，
本模块负责边收边放，从而让「翻译完立刻听到声音」而不必等整段合成结束。

线程模型与采集端相反但同理：SDK 的 on_data 回调不能被 write() 长时间
阻塞（否则 WebSocket 收包被拖慢），所以 feed() 只入队，实际写声卡由
播放线程完成。
"""

from __future__ import annotations

import logging
import queue
import threading

import sounddevice as sd

from realtalk.config import CHANNELS, TTS_SAMPLE_RATE

logger = logging.getLogger(__name__)

_QUEUE_SENTINEL = b""


class PcmStreamPlayer:
    """把陆续到达的 PCM 数据块播放出去。

    典型用法：
        player = PcmStreamPlayer()
        player.start()
        player.feed(chunk)      # 在 SDK 回调里反复调用
        player.finish()         # 数据发完，等待剩余音频播放完毕
    """

    def __init__(
        self,
        *,
        sample_rate: int = TTS_SAMPLE_RATE,
        channels: int = CHANNELS,
        device: int | None = None,
    ) -> None:
        self._sample_rate = sample_rate
        self._channels = channels
        self._device = device

        self._queue: queue.Queue[bytes] = queue.Queue()
        self._stream: sd.RawOutputStream | None = None
        self._worker: threading.Thread | None = None
        self._active = threading.Event()
        self._finished = threading.Event()
        self._bytes_played = 0

    @property
    def is_active(self) -> bool:
        return self._active.is_set()

    def start(self) -> None:
        if self._active.is_set():
            return

        self._bytes_played = 0
        self._finished.clear()
        while not self._queue.empty():
            self._queue.get_nowait()

        self._stream = sd.RawOutputStream(
            samplerate=self._sample_rate,
            channels=self._channels,
            device=self._device,
            dtype="int16",
        )
        self._stream.start()
        self._active.set()

        self._worker = threading.Thread(
            target=self._play_loop, name="realtalk-player", daemon=True
        )
        self._worker.start()
        logger.debug("播放器已启动：%dHz %d声道", self._sample_rate, self._channels)

    def feed(self, data: bytes) -> None:
        """追加一段 PCM 数据。未启动时自动启动，方便直接在回调里调用。"""
        if not data:
            return
        if not self._active.is_set():
            self.start()
        self._queue.put(data)

    def finish(self, timeout: float = 30.0) -> None:
        """告知数据已全部送入，阻塞等待队列中剩余音频播完后关闭。"""
        if not self._active.is_set():
            return
        self._queue.put(_QUEUE_SENTINEL)
        if self._worker is not None:
            self._worker.join(timeout=timeout)
        self._teardown()

    def stop(self) -> None:
        """立即中止播放并丢弃尚未播放的数据，用于「打断」。"""
        if not self._active.is_set():
            return
        self._active.clear()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._queue.put(_QUEUE_SENTINEL)

        if self._stream is not None:
            try:
                self._stream.abort()
            except Exception:
                logger.debug("中止播放流时出错", exc_info=True)

        if self._worker is not None:
            self._worker.join(timeout=2.0)
        self._teardown()
        logger.debug("播放已中止")

    def _teardown(self) -> None:
        self._active.clear()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                logger.debug("关闭播放流时出错", exc_info=True)
            self._stream = None
        self._worker = None
        self._finished.set()

    def _play_loop(self) -> None:
        stream = self._stream
        if stream is None:
            return
        while True:
            chunk = self._queue.get()
            if chunk == _QUEUE_SENTINEL:
                return
            if not self._active.is_set():
                return
            try:
                stream.write(chunk)
                self._bytes_played += len(chunk)
            except Exception:
                logger.warning("写入播放流失败", exc_info=True)
                return

    def __enter__(self) -> PcmStreamPlayer:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()
