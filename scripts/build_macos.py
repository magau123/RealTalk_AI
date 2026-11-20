"""在 Mac 上运行：.venv/bin/python scripts/build_macos.py。"""

from __future__ import annotations

import os
import platform
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build" / "macos"


def make_icon() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    iconset = BUILD / "RealTalk.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            pixels = size * scale
            image = QImage(pixels, pixels, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.scale(pixels / 1024, pixels / 1024)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#102a43"))
            painter.drawRoundedRect(QRectF(30, 30, 964, 964), 210, 210)
            painter.setBrush(QColor("#35d8b1"))
            painter.drawRoundedRect(QRectF(160, 208, 704, 535), 110, 110)
            tail = QPainterPath()
            tail.moveTo(260, 690)
            tail.lineTo(260, 837)
            tail.lineTo(430, 710)
            painter.drawPath(tail)
            painter.setPen(QColor("#102a43"))
            painter.setFont(QFont("Helvetica", 230, QFont.Weight.Bold))
            painter.drawText(QRectF(160, 208, 704, 535), Qt.AlignmentFlag.AlignCenter, "RT")
            painter.end()
            suffix = "@2x" if scale == 2 else ""
            image.save(str(iconset / f"icon_{size}x{size}{suffix}.png"))
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(BUILD / "RealTalk.icns")], check=True)


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("请在 macOS 上构建 Mac 应用")
    BUILD.mkdir(parents=True, exist_ok=True)
    helper = BUILD / "helper" / "RealTalk Audio Capture.app"
    contents = helper / "Contents"
    binary = contents / "MacOS" / "RealTalkAudioCapture"
    binary.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "xcrun", "swiftc", "-O", "-target", f"{platform.machine()}-apple-macos13.0",
        str(ROOT / "realtalk/audio/macos_audio_capture.swift"),
        "-framework", "ScreenCaptureKit", "-framework", "CoreMedia",
        "-framework", "CoreAudio", "-framework", "Foundation", "-o", str(binary),
    ], check=True)
    (contents / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleExecutable": "RealTalkAudioCapture",
        "CFBundleIdentifier": "ai.realtalk.SystemAudioCapture",
        "CFBundleName": "RealTalk Audio Capture",
        "CFBundlePackageType": "APPL",
        "LSMinimumSystemVersion": "13.0",
        "NSAudioCaptureUsageDescription": "实时翻译电脑播放的视频和会议声音。",
        "NSScreenCaptureUsageDescription": "使用系统音频采集接口翻译电脑声音，不读取画面。",
    }))
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(helper)], check=True)
    shutil.make_archive(str(BUILD / "macos_audio_helper"), "zip", helper.parent, helper.name)
    make_icon()
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", str(ROOT / "RealTalk.spec"),
    ], cwd=ROOT, check=True)
    print(f"构建完成：{ROOT / 'dist' / 'RealTalk.app'}")


if __name__ == "__main__":
    main()
