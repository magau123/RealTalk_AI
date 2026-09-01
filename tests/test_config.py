"""配置加载的测试，重点是 API Key 不会泄漏。"""

from __future__ import annotations

import pytest

from realtalk.config import ConfigError, Settings, load_settings

_FAKE_KEY = "sk-0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DASHSCOPE_API_KEY",
        "REALTALK_ASR_MODEL",
        "REALTALK_MT_MODEL",
        "REALTALK_TTS_MODEL",
        "REALTALK_MAX_END_SILENCE",
        "REALTALK_WEBSOCKET_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_missing_key_gives_actionable_message(tmp_path) -> None:
    with pytest.raises(ConfigError) as exc_info:
        load_settings(env_file=tmp_path / "absent.env")
    message = str(exc_info.value)
    assert "DASHSCOPE_API_KEY" in message
    assert ".env" in message


def test_placeholder_key_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
    with pytest.raises(ConfigError, match="占位值"):
        load_settings(env_file=tmp_path / "absent.env")


def test_loads_from_env_file(tmp_path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(f"DASHSCOPE_API_KEY={_FAKE_KEY}\n", encoding="utf-8")
    settings = load_settings(env_file=env_file)
    assert settings.dashscope_api_key == _FAKE_KEY


def test_process_env_wins_over_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("DASHSCOPE_API_KEY=sk-from-file-000000000000\n", encoding="utf-8")
    monkeypatch.setenv("DASHSCOPE_API_KEY", _FAKE_KEY)
    assert load_settings(env_file=env_file).dashscope_api_key == _FAKE_KEY


def test_key_is_masked_in_repr_and_property() -> None:
    settings = Settings(dashscope_api_key=_FAKE_KEY)
    assert _FAKE_KEY not in repr(settings)
    assert settings.masked_api_key.startswith("sk-012")
    assert settings.masked_api_key.endswith(_FAKE_KEY[-4:])
    assert _FAKE_KEY not in settings.masked_api_key


def test_short_key_is_fully_masked() -> None:
    assert set(Settings(dashscope_api_key="sk-123").masked_api_key) == {"*"}


def test_silence_threshold_is_range_checked(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", _FAKE_KEY)
    monkeypatch.setenv("REALTALK_MAX_END_SILENCE", "50")
    with pytest.raises(ConfigError, match="200~6000"):
        load_settings(env_file=tmp_path / "absent.env")


def test_non_numeric_silence_threshold_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", _FAKE_KEY)
    monkeypatch.setenv("REALTALK_MAX_END_SILENCE", "fast")
    with pytest.raises(ConfigError, match="整数"):
        load_settings(env_file=tmp_path / "absent.env")


def test_model_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", _FAKE_KEY)
    monkeypatch.setenv("REALTALK_MT_MODEL", "qwen-mt-plus")
    settings = load_settings(env_file=tmp_path / "absent.env")
    assert settings.mt_model == "qwen-mt-plus"
    assert settings.asr_model == "qwen3.5-livetranslate-flash-realtime"
