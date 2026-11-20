"""用 macOS 注册特定全局热键，不安装键盘监听器、不读取其他按键内容。"""

from __future__ import annotations

import ctypes as ct
import logging
import sys

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication

logger = logging.getLogger(__name__)


def _fourcc(value: bytes) -> int:
    return int.from_bytes(value, "big")


class _EventType(ct.Structure):
    _fields_ = [("event_class", ct.c_uint32), ("kind", ct.c_uint32)]


class _HotKeyID(ct.Structure):
    _fields_ = [("signature", ct.c_uint32), ("id", ct.c_uint32)]


_Callback = ct.CFUNCTYPE(ct.c_int32, ct.c_void_p, ct.c_void_p, ct.c_void_p)


class MacPushToTalkHotkey(QObject):
    changed = Signal(bool)
    shortcut = "Ctrl＋Option＋空格"
    _modifiers = (1 << 12) | (1 << 11)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._carbon = None
        self._handler = ct.c_void_p()
        self._hotkey = ct.c_void_p()
        self._callback = _Callback(self._on_event)
        self._held = False
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(100)
        self._watchdog.timeout.connect(self._check_modifiers)

    def start(self) -> None:
        if sys.platform != "darwin" or QGuiApplication.platformName() == "offscreen":
            raise RuntimeError("当前环境不支持 macOS 全局快捷键，可在展开窗口中使用空格")
        if self._hotkey.value:
            return
        carbon = ct.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        self._carbon = carbon
        carbon.GetApplicationEventTarget.argtypes = []
        carbon.GetApplicationEventTarget.restype = ct.c_void_p
        carbon.InstallEventHandler.argtypes = [
            ct.c_void_p, _Callback, ct.c_uint32, ct.POINTER(_EventType),
            ct.c_void_p, ct.POINTER(ct.c_void_p),
        ]
        carbon.InstallEventHandler.restype = ct.c_int32
        carbon.RegisterEventHotKey.argtypes = [
            ct.c_uint32, ct.c_uint32, _HotKeyID, ct.c_void_p,
            ct.c_uint32, ct.POINTER(ct.c_void_p),
        ]
        carbon.RegisterEventHotKey.restype = ct.c_int32
        carbon.UnregisterEventHotKey.argtypes = [ct.c_void_p]
        carbon.UnregisterEventHotKey.restype = ct.c_int32
        carbon.RemoveEventHandler.argtypes = [ct.c_void_p]
        carbon.RemoveEventHandler.restype = ct.c_int32
        carbon.GetEventKind.argtypes = [ct.c_void_p]
        carbon.GetEventKind.restype = ct.c_uint32
        carbon.GetEventParameter.argtypes = [
            ct.c_void_p, ct.c_uint32, ct.c_uint32, ct.c_void_p,
            ct.c_uint32, ct.c_void_p, ct.c_void_p,
        ]
        carbon.GetEventParameter.restype = ct.c_int32
        carbon.GetCurrentKeyModifiers.argtypes = []
        carbon.GetCurrentKeyModifiers.restype = ct.c_uint32
        events = (_EventType * 2)(
            _EventType(_fourcc(b"keyb"), 5), _EventType(_fourcc(b"keyb"), 6)
        )
        target = carbon.GetApplicationEventTarget()
        status = carbon.InstallEventHandler(
            target, self._callback, 2, events, None, ct.byref(self._handler)
        )
        if status:
            raise RuntimeError(f"无法安装全局快捷键处理器（{status}）")
        status = carbon.RegisterEventHotKey(
            0x31, self._modifiers, _HotKeyID(_fourcc(b"RTLK"), 1),
            target, 1, ct.byref(self._hotkey),
        )
        if status:
            self.stop()
            raise RuntimeError(f"快捷键 {self.shortcut} 注册失败或已被占用（{status}）")
        logger.info("全局按住说话快捷键已注册：%s", self.shortcut)

    def _on_event(self, _next, event, _data) -> int:
        try:
            key_id = _HotKeyID()
            status = self._carbon.GetEventParameter(
                event, _fourcc(b"----"), _fourcc(b"hkid"), None,
                ct.sizeof(key_id), None, ct.byref(key_id),
            )
            if status or key_id.signature != _fourcc(b"RTLK") or key_id.id != 1:
                return -9874  # eventNotHandledErr
            self._set_held(self._carbon.GetEventKind(event) == 5)
            return 0
        except Exception:
            logger.exception("处理全局快捷键失败")
            self._set_held(False)
            return -9874

    def _set_held(self, held: bool) -> None:
        if held == self._held:
            return
        self._held = held
        if held:
            self._watchdog.start()
        else:
            self._watchdog.stop()
        self.changed.emit(held)

    def _check_modifiers(self) -> None:
        # 松开修饰键、系统休眠等情况下补发停止，避免热键松开事件丢失后常开。
        if self._carbon.GetCurrentKeyModifiers() & self._modifiers != self._modifiers:
            self._set_held(False)

    def stop(self) -> None:
        self._set_held(False)
        if self._carbon is not None:
            if self._hotkey.value:
                self._carbon.UnregisterEventHotKey(self._hotkey)
                self._hotkey = ct.c_void_p()
            if self._handler.value:
                self._carbon.RemoveEventHandler(self._handler)
                self._handler = ct.c_void_p()


def configure_floating_panel(widget) -> None:
    """让 Qt 工具窗口在 macOS 其他桌面及全屏会议中显示。"""
    if sys.platform != "darwin" or QGuiApplication.platformName() == "offscreen":
        return
    objc = ct.CDLL("/usr/lib/libobjc.A.dylib")
    objc.sel_registerName.argtypes = [ct.c_char_p]
    objc.sel_registerName.restype = ct.c_void_p
    address = ct.cast(objc.objc_msgSend, ct.c_void_p).value
    get_window = ct.CFUNCTYPE(ct.c_void_p, ct.c_void_p, ct.c_void_p)(address)
    get_bits = ct.CFUNCTYPE(ct.c_ulong, ct.c_void_p, ct.c_void_p)(address)
    set_bits = ct.CFUNCTYPE(None, ct.c_void_p, ct.c_void_p, ct.c_ulong)(address)
    window = get_window(int(widget.winId()), objc.sel_registerName(b"window"))
    if window:
        behavior = get_bits(window, objc.sel_registerName(b"collectionBehavior"))
        # canJoinAllSpaces | fullScreenAuxiliary; moveToActiveSpace 不能与前者并用。
        set_bits(window, objc.sel_registerName(b"setCollectionBehavior:"),
                 (behavior & ~((1 << 1) | (1 << 7))) | 1 | (1 << 8))
