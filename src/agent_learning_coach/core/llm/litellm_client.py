"""基于 LiteLLM 的 ``LLMClient`` 实现。

LiteLLM 统一了 OpenAI / Anthropic / Gemini 等提供商的调用差异，
因此本项目只需维护这一份适配器即可支持多厂商切换：切换厂商只需更换
模型标识，不需要改动业务代码。

结构化输出采用“提示词约束 + 严格 JSON 解析”的可移植路径（见
``agent_learning_coach.core.llm.structured``），不依赖厂商专属的
response schema 特性；需要更高保真度的实现可另写适配器覆盖该行为。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from agent_learning_coach.core.llm.errors import LLMError, LLMTimeoutError
from agent_learning_coach.core.llm.messages import build_messages, build_tools, parse_response
from agent_learning_coach.core.llm.models import LLMRequest, LLMResponse
from agent_learning_coach.core.llm.structured import (
    build_structured_instruction,
    extract_text,
    parse_structured_output,
)


def _translate_error(exc: Exception) -> LLMError:
    """把 LiteLLM 异常转换为厂商无关的异常。

    超时单独映射为 ``LLMTimeoutError``，因为它是可预期的、值得重试的失败。
    """
    if isinstance(exc, TimeoutError):
        return LLMTimeoutError(f"LLM 调用超时: {exc}")

    try:
        from litellm.exceptions import Timeout as LiteLLMTimeout
    except ImportError:  # pragma: no cover - 依赖缺失时的兜底
        return LLMError(f"LLM 调用失败: {exc}")

    if isinstance(exc, LiteLLMTimeout):
        return LLMTimeoutError(f"LLM 调用超时: {exc}")
    return LLMError(f"LLM 调用失败: {exc}")


class LiteLLMClient:
    """LiteLLM 适配器，满足 ``LLMClient`` Protocol。

    用法::

        client = LiteLLMClient(model="gpt-4o-mini", timeout=30.0)
        response = await client.generate(LLMRequest(instructions=["..."]))
    """

    def __init__(
        self,
        model: str,
        *,
        timeout: float | None = None,
        model_prefix: str | None = None,
        **config: Any,
    ) -> None:
        """初始化客户端。

        Args:
            model: LiteLLM 模型标识，例如 ``gpt-4o-mini``、``anthropic/claude-sonnet-4-5``。
            timeout: 默认超时秒数。请求级 ``LLMRequest.timeout`` 优先。
            model_prefix: 厂商前缀。设置后，请求级 ``LLMRequest.model``
                指定的裸模型名会自动补上该前缀，实现请求级模型切换。
            **config: 透传给 LiteLLM 的默认参数，如 ``api_base``、``api_key``。
        """
        self.model = model
        self.timeout = timeout
        self.model_prefix = model_prefix
        self.config = config

    def resolve_model(self, model: str | None) -> str:
        """确定实际发送给 LiteLLM 的模型标识。

        请求级模型优先于客户端默认模型；设置了 ``model_prefix`` 时，
        裸模型名自动补前缀，已带前缀的标识保持原样。
        """
        chosen = model or self.model
        if self.model_prefix is None or "/" in chosen:
            return chosen
        return f"{self.model_prefix}/{chosen}"

    def build_payload(self, request: LLMRequest) -> dict[str, Any]:
        """构造 LiteLLM 调用参数（公开以便测试断言，不含任何厂商类型）。"""
        model = self.resolve_model(request.model)
        payload: dict[str, Any] = {
            "model": model,
            "messages": build_messages(request),
            **self.config,
        }
        if request.tools:
            payload["tools"] = build_tools(request.tools)
        if request.tool_choice:
            payload["tool_choice"] = request.tool_choice
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens

        timeout = request.timeout if request.timeout is not None else self.timeout
        if timeout is not None:
            payload["timeout"] = timeout

        payload.update(request.extra)
        return payload

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """执行一次 LLM 调用。

        Raises:
            LLMTimeoutError: 调用超过超时时间。
            LLMError: 其他调用失败。
        """
        try:
            from litellm import acompletion
        except ImportError as exc:  # pragma: no cover - 依赖缺失时的显式错误
            raise LLMError("未安装 litellm，无法使用 LiteLLMClient") from exc

        payload = self.build_payload(request)
        model = payload["model"]

        try:
            raw = await acompletion(**payload)
        except Exception as exc:
            raise _translate_error(exc) from exc

        return parse_response(raw, model=model)

    async def generate_structured[ResponseT: BaseModel](
        self,
        request: LLMRequest,
        output_model: type[ResponseT],
    ) -> ResponseT:
        """执行一次 LLM 调用并解析为 ``output_model``。"""
        structured_request = request.model_copy(deep=True)
        structured_request.instructions = [
            build_structured_instruction(instruction, output_model)
            for instruction in request.instructions
        ]

        response = await self.generate(structured_request)
        text = extract_text(response.contents)
        return parse_structured_output(text, output_model)
