"""文本翻译，基于百炼 Qwen-MT。

官方文档：https://help.aliyun.com/zh/model-studio/qwen-mt-api

三处容易踩错的地方：

1. translation_options 里的语种要用**英文全称**（"Japanese"），不是 "ja"
   这样的语言代码。Gummy 用代码、Qwen-MT 用全称，两套体系并存，转换
   统一走 languages.qwen_mt_name()。

2. messages 有且仅有一条 role 为 user 的消息，不支持 System Message，
   也不支持多轮对话。

3. 百炼的免费额度是**按模型独立计算**的，不能跨模型共用。也就是说
   qwen-mt-flash 额度用尽时，qwen-mt-plus 往往仍然可用。不同用户账号
   的额度状态各不相同，把模型写死会让别人克隆项目后直接失败，所以这里
   实现了一条降级链：首选模型因额度或权限不可用时，自动换到下一个候选
   并记住结果，后续请求直接用可用的那个，不再重复试探。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from http import HTTPStatus

import dashscope

from realtalk.config import Settings
from realtalk.languages import AUTO, qwen_mt_name

logger = logging.getLogger(__name__)

# 候选顺序按「效果 / 速度」权衡：plus 效果最好，turbo 速度较快且仍在服务。
# 官方已说明 turbo 不再更新，因此它排在 plus 之后，仅作为兜底。
DEFAULT_FALLBACK_MODELS: tuple[str, ...] = ("qwen-mt-plus", "qwen-mt-turbo")

# 这些错误说明「换个模型可能就好了」，属于可降级错误。
# 网络超时之类的瞬时故障不在此列——换模型也解决不了，应当直接上报。
_SWITCHABLE_MARKERS = (
    "free quota exhausted",
    "arrearage",
    "model not exist",
    "access denied",
    "not activated",
    "no permission",
)


class TranslationError(RuntimeError):
    """翻译请求失败。"""


class QuotaExhaustedError(TranslationError):
    """所有候选模型都因额度或权限不可用。"""


def _is_switchable(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in _SWITCHABLE_MARKERS)


class TextTranslator:
    """文本翻译客户端，可在多个线程中共享。"""

    def __init__(
        self,
        settings: Settings,
        *,
        fallback_models: Sequence[str] = DEFAULT_FALLBACK_MODELS,
        on_model_switch: Callable[[str, str, str], None] | None = None,
    ) -> None:
        self._settings = settings
        self._on_model_switch = on_model_switch

        candidates: list[str] = []
        for model in (settings.mt_model, *fallback_models):
            if model and model not in candidates:
                candidates.append(model)
        self._candidates = tuple(candidates)

        self._lock = threading.Lock()
        self._active = 0

    @property
    def active_model(self) -> str:
        """当前实际使用的模型。可能与配置值不同（发生过降级）。"""
        with self._lock:
            return self._candidates[self._active]

    @property
    def candidates(self) -> tuple[str, ...]:
        return self._candidates

    def translate(
        self,
        text: str,
        *,
        target_language: str,
        source_language: str = AUTO,
    ) -> str:
        """把 text 翻译成 target_language，返回译文。

        text 为空或纯空白时直接返回空串，避免无意义的计费请求。
        """
        stripped = text.strip()
        if not stripped:
            return ""
        if source_language != AUTO and source_language == target_language:
            return stripped

        options = {
            "source_lang": qwen_mt_name(source_language),
            "target_lang": qwen_mt_name(target_language),
        }

        with self._lock:
            start = self._active

        failures: list[str] = []
        for index in range(start, len(self._candidates)):
            model = self._candidates[index]
            try:
                result = self._call(model, stripped, options)
            except TranslationError as exc:
                message = str(exc)
                failures.append(f"{model}: {message}")
                if not _is_switchable(message) or index == len(self._candidates) - 1:
                    break
                logger.warning("模型 %s 不可用，尝试下一个候选：%s", model, message)
                continue

            if index != start:
                self._promote(
                    index, previous=self._candidates[start], reason=failures[-1]
                )
            return result

        detail = "；".join(failures)
        if failures and _is_switchable(failures[-1]):
            raise QuotaExhaustedError(
                f"所有候选翻译模型都不可用（{detail}）。\n"
                "百炼免费额度按模型独立计算，可在控制台充值，或关闭「仅使用免费额度」模式；\n"
                "也可以通过环境变量 REALTALK_MT_MODEL 指定一个仍有额度的模型。"
            )
        raise TranslationError(detail or "翻译失败，且没有可用的候选模型")

    def _promote(self, index: int, *, previous: str, reason: str) -> None:
        """记住可用的模型，避免后续每次请求都先去撞不可用的那个。"""
        with self._lock:
            if index <= self._active:
                return
            self._active = index
            new_model = self._candidates[index]

        logger.warning("翻译模型已从 %s 切换到 %s", previous, new_model)
        if self._on_model_switch is not None:
            try:
                self._on_model_switch(previous, new_model, reason)
            except Exception:
                logger.error("模型切换回调抛出异常", exc_info=True)

    def _call(self, model: str, text: str, options: dict[str, str]) -> str:
        try:
            response = dashscope.Generation.call(
                api_key=self._settings.dashscope_api_key,
                model=model,
                messages=[{"role": "user", "content": text}],
                translation_options=options,
                result_format="message",
            )
        except Exception as exc:
            raise TranslationError(f"调用翻译模型失败：{exc}") from exc

        status = getattr(response, "status_code", None)
        if status != HTTPStatus.OK:
            detail = (
                getattr(response, "message", "")
                or getattr(response, "code", "")
                or "未知错误"
            )
            raise TranslationError(f"{status}：{detail}")

        try:
            return response.output.choices[0].message.content.strip()
        except (AttributeError, IndexError, KeyError, TypeError) as exc:
            raise TranslationError(f"无法解析翻译结果：{response}") from exc

    def translate_quietly(
        self,
        text: str,
        *,
        target_language: str,
        source_language: str = AUTO,
    ) -> str:
        """翻译失败时返回空串而不抛异常。

        用于兜底场景：补译只是锦上添花，失败不应该中断正在进行的实时识别。
        """
        try:
            return self.translate(
                text, target_language=target_language, source_language=source_language
            )
        except TranslationError as exc:
            logger.warning("兜底翻译失败：%s", exc)
            return ""
