"""按住说话期间静音当前系统输出，结束后恢复此前状态。"""
from __future__ import annotations

import ctypes as ct
import sys


class _Address(ct.Structure):
    _fields_ = [("selector", ct.c_uint32), ("scope", ct.c_uint32), ("element", ct.c_uint32)]


def _code(text: bytes) -> int:
    return int.from_bytes(text, "big")


class OutputMuteGuard:
    def __init__(self) -> None:
        self._saved = None
        self._audio = None

    def begin(self) -> None:
        if sys.platform != "darwin" or self._saved is not None:
            return
        audio = ct.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
        self._audio = audio
        for name in ("AudioObjectGetPropertyData", "AudioObjectSetPropertyData"):
            function = getattr(audio, name)
            function.restype = ct.c_int32
        audio.AudioObjectGetPropertyData.argtypes = [ct.c_uint32, ct.POINTER(_Address), ct.c_uint32, ct.c_void_p, ct.POINTER(ct.c_uint32), ct.c_void_p]
        audio.AudioObjectSetPropertyData.argtypes = [ct.c_uint32, ct.POINTER(_Address), ct.c_uint32, ct.c_void_p, ct.c_uint32, ct.c_void_p]
        device = ct.c_uint32()
        address = _Address(_code(b"dOut"), _code(b"glob"), 0)
        self._read(1, address, device)
        address = _Address(_code(b"mute"), _code(b"outp"), 0)
        previous = ct.c_uint32()
        self._read(device.value, address, previous)
        if not previous.value:
            self._write(device.value, address, 1)
        self._saved = (device.value, address, previous.value)

    def _read(self, device, address, value) -> None:
        size = ct.c_uint32(ct.sizeof(value))
        status = self._audio.AudioObjectGetPropertyData(device, ct.byref(address), 0, None, ct.byref(size), ct.byref(value))
        if status:
            raise RuntimeError("当前音频输出无法临时静音，请切换到 Mac 内建扬声器后重试。")

    def _write(self, device, address, value) -> None:
        data = ct.c_uint32(value)
        status = self._audio.AudioObjectSetPropertyData(device, ct.byref(address), 0, None, ct.sizeof(data), ct.byref(data))
        if status:
            raise RuntimeError("无法切换系统扬声器静音，请检查声音设置。")

    def end(self) -> None:
        if self._saved is None:
            return
        device, address, previous = self._saved
        self._write(device, address, previous)
        self._saved = None
