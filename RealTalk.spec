# 构建入口：python scripts/build_macos.py（先编译系统音频组件和图标）
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

root = Path(SPECPATH)
assets = root / "build" / "macos"
analysis = Analysis(
    [str(root / "main.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[
        (str(root / ".env.example"), "."),
        (str(assets / "macos_audio_helper.zip"), "realtalk/audio"),
    ] + collect_data_files("dashscope"),
    hiddenimports=["ctypes", "ctypes.util", "sounddevice"],
    excludes=["pytest", "tkinter"],
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive, analysis.scripts, [], exclude_binaries=True,
    name="RealTalk", console=False, strip=False, upx=False,
    argv_emulation=False, codesign_identity=None,
)
collection = COLLECT(
    executable, analysis.binaries, analysis.datas,
    strip=False, upx=False, name="RealTalk",
)
application = BUNDLE(
    collection, name="RealTalk.app", icon=str(assets / "RealTalk.icns"),
    bundle_identifier="ai.realtalk.desktop",
    info_plist={
        "CFBundleDisplayName": "RealTalk",
        "CFBundleShortVersionString": "0.2.0",
        "CFBundleVersion": "20261120.3",
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription": "RealTalk 需要麦克风，把你的中文实时翻译为英文字幕。",
        "NSAudioCaptureUsageDescription": "RealTalk 需要采集电脑声音，实时翻译视频和会议内容。",
        "NSScreenCaptureUsageDescription": "RealTalk 使用系统音频采集接口接收电脑声音，不读取画面。",
    },
)
