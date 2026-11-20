"""独立应用的外部配置和预编译音频组件；不启动录音或联网。"""

from __future__ import annotations

import zipfile
from pathlib import Path

from realtalk import config
from realtalk.audio import recorder


def test_packaged_settings_use_user_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(config.sys, "frozen", True, raising=False)
    monkeypatch.setattr(config.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    path = tmp_path / "Library/Application Support/RealTalk_AI/.env"
    path.parent.mkdir(parents=True)
    path.write_text("DASHSCOPE_API_KEY=sk-test-packaged-configuration\n")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    assert config.default_env_path() == path
    assert config.load_settings().dashscope_api_key == "sk-test-packaged-configuration"


def test_packaged_audio_does_not_compile_swift(monkeypatch, tmp_path):
    monkeypatch.setattr(recorder.sys, "frozen", True, raising=False)
    monkeypatch.setattr(recorder.sys, "platform", "darwin")
    binary = tmp_path / "RealTalkAudioCapture"
    monkeypatch.setattr(recorder.MicrophoneRecorder, "_bundled_macos_capture_binary", staticmethod(lambda: binary))
    audio = recorder.MicrophoneRecorder(on_frame=lambda frame: None)
    launches = []
    monkeypatch.setattr(audio, "_launch_macos_system_audio", launches.append)
    audio._start_macos_system_audio()
    assert launches == [binary]


def test_bundled_audio_extracts_once_with_executable_permission(monkeypatch, tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    monkeypatch.setattr(recorder, "__file__", str(assets / "recorder.py"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "user")
    with zipfile.ZipFile(assets / "macos_audio_helper.zip", "w") as archive:
        archive.writestr("RealTalk Audio Capture.app/Contents/MacOS/RealTalkAudioCapture", b"helper")
    binary = recorder.MicrophoneRecorder._bundled_macos_capture_binary()
    assert binary.read_bytes() == b"helper"
    assert binary.stat().st_mode & 0o111 == 0o111
    timestamp = binary.stat().st_mtime_ns
    assert recorder.MicrophoneRecorder._bundled_macos_capture_binary() == binary
    assert binary.stat().st_mtime_ns == timestamp
