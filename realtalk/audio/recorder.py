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
import hashlib
import plistlib
import queue
import subprocess
import sys
import threading
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import sounddevice as sd

from realtalk.config import CHANNELS, FRAMES_PER_BUFFER, SAMPLE_RATE

logger = logging.getLogger(__name__)

WASAPI_LOOPBACK_DEVICE = -1
MACOS_SYSTEM_AUDIO_DEVICE = -2

# 队列上限。每帧 50ms，最多缓存 1 秒。队列满时丢最旧帧，避免网络发送
# 短暂变慢后一直翻译过时的声音。
_MAX_QUEUED_FRAMES = 20


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
    if sys.platform == "darwin":
        devices.append(
            AudioDevice(
                index=MACOS_SYSTEM_AUDIO_DEVICE,
                name="系统声音（macOS 屏幕录制权限）",
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

        self._queue: queue.Queue[bytes | threading.Event | None] = queue.Queue(maxsize=_MAX_QUEUED_FRAMES)
        self._stream: sd.RawInputStream | None = None
        self._loopback_audio: object | None = None
        self._loopback_stream: object | None = None
        self._system_audio_process: subprocess.Popen[bytes] | None = None
        self._system_audio_reader: threading.Thread | None = None
        self._pump: threading.Thread | None = None
        self._running = threading.Event()
        self._input_enabled = threading.Event()
        self._input_enabled.set()
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
            elif self._device == MACOS_SYSTEM_AUDIO_DEVICE:
                self._start_macos_system_audio()
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

        if self._system_audio_process is not None:
            process = self._system_audio_process
            self._system_audio_process = None
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)
            if process.stdout is not None:
                process.stdout.close()
            self._system_audio_reader = None

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
        # 在采集端屏蔽未按键期间的声音；已入队的语音仍可发完并继续翻译。
        if not self._input_enabled.is_set():
            data = bytes(len(data))
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
            try:
                self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(data)
            except queue.Full:
                # 理论上只有本线程写入队列；保留防护以处理停止期间的竞态。
                pass
            self._dropped_frames += 1
            if self._dropped_frames == 1 or self._dropped_frames % 20 == 0:
                logger.warning(
                    "音频发送落后实时进度，已丢弃 %d 帧旧音频（队列深度 %d）",
                    self._dropped_frames,
                    self._queue.qsize(),
                )

    def set_input_enabled(self, enabled: bool) -> None:
        """控制新采集的语音是否进入翻译；关闭时发送静音供 VAD 断句。"""
        if enabled:
            self._input_enabled.set()
        else:
            self._input_enabled.clear()

    def flush_pending(self, timeout: float = 2.0) -> bool:
        """等待松键前已采集的帧发送完，再提交整段语音，避免截掉尾音。"""
        if not self._running.is_set():
            return True
        marker = threading.Event()
        try:
            self._queue.put(marker, timeout=timeout)
        except queue.Full:
            return False
        return marker.wait(timeout)

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

    def _start_macos_system_audio(self) -> None:
        if sys.platform != "darwin":
            raise RuntimeError("macOS 系统声音采集只能在 macOS 上使用")

        if getattr(sys, "frozen", False):
            helper_binary = self._bundled_macos_capture_binary()
            self._launch_macos_system_audio(helper_binary)
            return

        helper_source = Path(__file__).with_name("macos_audio_capture.swift")
        if not helper_source.is_file():
            raise RuntimeError(f"找不到 macOS 系统声音采集程序：{helper_source}")

        cache_dir = Path.home() / "Library" / "Application Support" / "RealTalk_AI"
        helper_app = cache_dir / "RealTalk Audio Capture.app"
        contents_dir = helper_app / "Contents"
        executable_dir = contents_dir / "MacOS"
        executable_dir.mkdir(parents=True, exist_ok=True)
        helper_binary = executable_dir / "RealTalkAudioCapture"
        info_plist = contents_dir / "Info.plist"
        if (
            not helper_binary.is_file()
            or helper_binary.stat().st_mtime < helper_source.stat().st_mtime
            or not info_plist.is_file()
        ):
            try:
                subprocess.run(
                    [
                        "xcrun",
                        "swiftc",
                        "-O",
                        str(helper_source),
                        "-framework",
                        "ScreenCaptureKit",
                        "-framework",
                        "CoreMedia",
                        "-framework",
                        "CoreAudio",
                        "-framework",
                        "Foundation",
                        "-o",
                        str(helper_binary),
                    ],
                    check=True,
                    timeout=120,
                )
            except FileNotFoundError as exc:
                raise RuntimeError(
                    "macOS 系统声音采集需要 Xcode Command Line Tools（提供 xcrun swiftc）；"
                    "请在终端运行 xcode-select --install 后重试。"
                ) from exc
            except subprocess.CalledProcessError as exc:
                raise RuntimeError(f"编译 macOS 声音采集组件失败：{exc}") from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("编译 macOS 声音采集组件超时") from exc

            info_plist.write_bytes(
                plistlib.dumps(
                    {
                        "CFBundleExecutable": "RealTalkAudioCapture",
                        "CFBundleIdentifier": "ai.realtalk.SystemAudioCapture",
                        "CFBundleName": "RealTalk Audio Capture",
                        "CFBundlePackageType": "APPL",
                        "LSMinimumSystemVersion": "13.0",
                        "NSAudioCaptureUsageDescription": (
                            "RealTalk 需要采集电脑播放的声音，才能实时翻译视频和会议内容。"
                        ),
                        "NSScreenCaptureUsageDescription": (
                            "RealTalk 使用 macOS 系统音频采集接口接收电脑播放的声音，不会读取画面。"
                        ),
                    }
                )
            )
            try:
                subprocess.run(
                    ["codesign", "--force", "--deep", "--sign", "-", str(helper_app)],
                    check=True,
                    timeout=30,
                )
            except (FileNotFoundError, subprocess.CalledProcessError) as exc:
                raise RuntimeError(f"签名 macOS 声音采集组件失败：{exc}") from exc
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("签名 macOS 声音采集组件超时") from exc

        self._launch_macos_system_audio(helper_binary)

    @staticmethod
    def _bundled_macos_capture_binary() -> Path:
        """发布应用内含预编译组件，用户不需要安装 Xcode 或 Swift。"""
        archive = Path(__file__).with_name("macos_audio_helper.zip")
        if not archive.is_file():
            raise RuntimeError("应用缺少系统声音采集组件，请重新安装 RealTalk")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()[:16]
        directory = (
            Path.home() / "Library" / "Application Support" / "RealTalk_AI"
            / "AudioCapture" / digest
        )
        binary = directory / "RealTalk Audio Capture.app" / "Contents" / "MacOS" / "RealTalkAudioCapture"
        if not binary.is_file():
            directory.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(directory)
            binary.chmod(0o755)
        return binary

    def _launch_macos_system_audio(self, helper_binary: Path) -> None:
        try:
            process = subprocess.Popen(
                [str(helper_binary)],
                stdout=subprocess.PIPE,
                # Keep diagnostics visible in the terminal that launched RealTalk.
                stderr=None,
                bufsize=0,
            )
        except OSError as exc:
            raise RuntimeError(f"启动 macOS 系统声音采集失败：{exc}") from exc

        self._system_audio_process = process
        self._input_sample_rate = self._sample_rate
        self._input_channels = self._channels
        self._system_audio_reader = threading.Thread(
            target=self._read_macos_system_audio,
            args=(process,),
            name="realtalk-macos-audio-reader",
            daemon=True,
        )
        self._system_audio_reader.start()
        logger.info(
            "macOS 系统声音采集已启动；首次使用需允许屏幕与系统音频录制权限"
        )

    def _read_macos_system_audio(self, process: subprocess.Popen[bytes]) -> None:
        if process.stdout is None:
            return
        frame_bytes = self._frames_per_buffer * 2
        pending = bytearray()
        try:
            while self._running.is_set() and process.poll() is None:
                chunk = process.stdout.read(frame_bytes - len(pending))
                if not chunk:
                    break
                pending.extend(chunk)
                if len(pending) == frame_bytes:
                    self._enqueue_audio(bytes(pending), source_channels=1)
                    pending.clear()
        except OSError as exc:
            if self._running.is_set():
                self._report_system_audio_error(exc)
            return

        if self._running.is_set():
            detail = "请检查 macOS「隐私与安全性 → 屏幕与系统音频录制」权限。"
            self._report_system_audio_error(
                RuntimeError(f"macOS 系统声音采集已停止。{detail}")
            )

    def _report_system_audio_error(self, exc: Exception) -> None:
        logger.error("macOS 系统声音采集失败：%s", exc)
        self._running.clear()
        if self._on_error is not None:
            self._on_error(exc)

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
            if isinstance(frame, threading.Event):
                frame.set()
                continue
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
