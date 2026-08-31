"""翻译模型降级链的测试。

为什么需要这些测试：百炼的免费额度按模型独立计算，不同账号的额度状态
各不相同。降级链让项目在「首选模型额度用尽」时仍能工作，但它有两个
容易写错的地方——一是不该对网络超时之类的瞬时错误降级（换模型解决不了，
只会掩盖真正的故障），二是降级结果必须被缓存，否则每次请求都要先去撞
一次不可用的模型，白白增加延迟。这两点都用测试锁住。
"""

from __future__ import annotations

from http import HTTPStatus

import pytest

from realtalk.config import Settings
from realtalk.core import translator as translator_module
from realtalk.core.translator import (
    QuotaExhaustedError,
    TextTranslator,
    TranslationError,
)

_QUOTA_MESSAGE = (
    "Free quota exhausted. To continue accessing the model on a paid basis, "
    "please add funds or disable the \"use free tier only\" mode."
)


def _settings(model: str = "model-a") -> Settings:
    return Settings(
        dashscope_api_key="sk-0123456789abcdef0123456789abcdef", mt_model=model
    )


class _FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: str) -> None:
        self.message = _FakeMessage(content)


class _FakeOutput:
    def __init__(self, content: str) -> None:
        self.choices = [_FakeChoice(content)]


class _FakeResponse:
    def __init__(
        self,
        *,
        content: str = "",
        status: int = HTTPStatus.OK,
        message: str = "",
    ) -> None:
        self.status_code = status
        self.message = message
        self.code = ""
        self.output = _FakeOutput(content)


class _Recorder:
    """记录每次调用用了哪个模型，并按预设脚本返回结果。"""

    def __init__(self, behaviour: dict[str, object]) -> None:
        self._behaviour = behaviour
        self.calls: list[str] = []

    def __call__(self, *, model: str, **kwargs: object) -> _FakeResponse:
        self.calls.append(model)
        outcome = self._behaviour[model]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def patch_generation(monkeypatch: pytest.MonkeyPatch):
    def install(behaviour: dict[str, object]) -> _Recorder:
        recorder = _Recorder(behaviour)
        monkeypatch.setattr(
            translator_module.dashscope.Generation, "call", recorder, raising=True
        )
        return recorder

    return install


def test_uses_configured_model_when_available(patch_generation) -> None:
    recorder = patch_generation({"model-a": _FakeResponse(content="hello")})
    translator = TextTranslator(_settings(), fallback_models=("model-b",))

    assert translator.translate("你好", target_language="en") == "hello"
    assert recorder.calls == ["model-a"]
    assert translator.active_model == "model-a"


def test_falls_back_when_quota_exhausted(patch_generation) -> None:
    recorder = patch_generation(
        {
            "model-a": _FakeResponse(status=403, message=_QUOTA_MESSAGE),
            "model-b": _FakeResponse(content="hello"),
        }
    )
    translator = TextTranslator(_settings(), fallback_models=("model-b",))

    assert translator.translate("你好", target_language="en") == "hello"
    assert recorder.calls == ["model-a", "model-b"]
    assert translator.active_model == "model-b"


def test_successful_fallback_is_cached(patch_generation) -> None:
    """降级结果必须被记住，否则每次请求都要多付一次失败的往返。"""
    recorder = patch_generation(
        {
            "model-a": _FakeResponse(status=403, message=_QUOTA_MESSAGE),
            "model-b": _FakeResponse(content="hello"),
        }
    )
    translator = TextTranslator(_settings(), fallback_models=("model-b",))

    for _ in range(3):
        translator.translate("你好", target_language="en")

    # model-a 只应该被试探一次
    assert recorder.calls == ["model-a", "model-b", "model-b", "model-b"]


def test_model_switch_callback_is_invoked(patch_generation) -> None:
    patch_generation(
        {
            "model-a": _FakeResponse(status=403, message=_QUOTA_MESSAGE),
            "model-b": _FakeResponse(content="hello"),
        }
    )
    switches: list[tuple[str, str]] = []
    translator = TextTranslator(
        _settings(),
        fallback_models=("model-b",),
        on_model_switch=lambda old, new, reason: switches.append((old, new)),
    )

    translator.translate("你好", target_language="en")
    assert switches == [("model-a", "model-b")]


def test_transient_errors_do_not_trigger_fallback(patch_generation) -> None:
    """网络故障换模型也解决不了，降级只会掩盖真正的问题。"""
    recorder = patch_generation(
        {
            "model-a": _FakeResponse(status=500, message="Internal timeout"),
            "model-b": _FakeResponse(content="hello"),
        }
    )
    translator = TextTranslator(_settings(), fallback_models=("model-b",))

    with pytest.raises(TranslationError, match="Internal timeout"):
        translator.translate("你好", target_language="en")
    assert recorder.calls == ["model-a"]


def test_all_models_exhausted_raises_actionable_error(patch_generation) -> None:
    patch_generation(
        {
            "model-a": _FakeResponse(status=403, message=_QUOTA_MESSAGE),
            "model-b": _FakeResponse(status=403, message=_QUOTA_MESSAGE),
        }
    )
    translator = TextTranslator(_settings(), fallback_models=("model-b",))

    with pytest.raises(QuotaExhaustedError) as exc_info:
        translator.translate("你好", target_language="en")
    message = str(exc_info.value)
    assert "REALTALK_MT_MODEL" in message
    assert "仅使用免费额度" in message


def test_candidates_are_deduplicated() -> None:
    translator = TextTranslator(
        _settings("model-b"), fallback_models=("model-b", "model-c")
    )
    assert translator.candidates == ("model-b", "model-c")


def test_blank_text_makes_no_request(patch_generation) -> None:
    recorder = patch_generation({"model-a": _FakeResponse(content="x")})
    translator = TextTranslator(_settings(), fallback_models=())

    assert translator.translate("   \n  ", target_language="en") == ""
    assert recorder.calls == []


def test_same_language_makes_no_request(patch_generation) -> None:
    recorder = patch_generation({"model-a": _FakeResponse(content="x")})
    translator = TextTranslator(_settings(), fallback_models=())

    assert (
        translator.translate("你好", target_language="zh", source_language="zh")
        == "你好"
    )
    assert recorder.calls == []


def test_translate_quietly_swallows_failure(patch_generation) -> None:
    patch_generation({"model-a": _FakeResponse(status=403, message=_QUOTA_MESSAGE)})
    translator = TextTranslator(_settings(), fallback_models=())

    assert translator.translate_quietly("你好", target_language="en") == ""
