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
        self._bytes_played = 0

        # 生命周期锁。start / feed / finish / stop 会被三个不同的线程调用：
        # 合成回调线程（start、feed）、发起合成的业务线程（finish）、以及
        # 任意时刻想打断播放的线程（stop，例如关窗口）。没有这把锁的话，
        # finish 正在 join 时 stop 去 close 同一个流，PortAudio 会因为
        # 访问已释放的流而让整个进程崩溃（Windows 上表现为访问违例）。
        self._lock = threading.RLock()

    @property
    def is_active(self) -> bool:
        return self._active.is_set()

    def start(self) -> None:
        with self._lock:
            if self._active.is_set():
                return

            self._bytes_played = 0
            self._drain_queue()

            stream = sd.RawOutputStream(
                samplerate=self._sample_rate,
                channels=self._channels,
                device=self._device,
                dtype="int16",
            )
            stream.start()
            self._stream = stream
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
        with self._lock:
            if not self._active.is_set():
                return
            worker = self._worker
            self._queue.put(_QUEUE_SENTINEL)

        # join 必须在锁外，否则 stop() 会被挡在锁上直到播放自然结束，
        # 「立即打断」就失去意义了。
        if worker is not None:
            worker.join(timeout=timeout)
        self._teardown(worker)

    def stop(self) -> None:
        """立即中止播放并丢弃尚未播放的数据，用于打断或关闭。"""
        with self._lock:
            if not self._active.is_set():
                return
            self._active.clear()
            self._drain_queue()
            self._queue.put(_QUEUE_SENTINEL)
            worker = self._worker
            if self._stream is not None:
                try:
                    # abort 会让阻塞中的 write 立刻返回，从而唤醒播放线程
                    self._stream.abort()
                except Exception:
                    logger.debug("中止播放流时出错", exc_info=True)

        if worker is not None:
            worker.join(timeout=2.0)
        self._teardown(worker)
        logger.debug("播放已中止")

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    def _teardown(self, worker: threading.Thread | None) -> None:
        """关闭音频流。只有确认播放线程已退出才关，否则宁可泄漏也不能崩溃。"""
        with self._lock:
            self._active.clear()
            if worker is not None and worker.is_alive():
                # join 超时说明播放线程还在跑，此时 close 会造成
                # write-after-close。留着流不关，进程退出时由系统回收。
                logger.warning("播放线程未在超时内退出，跳过关闭音频流")
                return
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    logger.debug("关闭播放流时出错", exc_info=True)
                self._stream = None
            self._worker = None

    def _play_loop(self) -> None:
        while True:
            chunk = self._queue.get()
            if chunk == _QUEUE_SENTINEL:
                return
            if not self._active.is_set():
                return
            # 每次重新取流引用：stop() 可能已经把它置空了
            stream = self._stream
            if stream is None:
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
