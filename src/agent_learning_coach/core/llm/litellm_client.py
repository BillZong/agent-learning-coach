"""基于 LiteLLM 的 ``LLMClient`` 实现。

LiteLLM 统一了 OpenAI / Anthropic / Gemini 等提供商的调用差异，
因此本项目只需维护这一份适配器即可支持多厂商切换。

结构化输出采用“提示词约束 + 严格 JSON 解析”的可移植路径（见
``agent_learning_coach.core.llm.structured``），不依赖厂商专属的
response schema 特性；需要更高保真度的实现可另写适配器覆盖该行为。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from agent_learning_coach.core.llm.errors import LLMError
from agent_learning_coach.core.llm.messages import build_messages, build_tools, parse_response
from agent_learning_coach.core.llm.models import LLMRequest, LLMResponse
from agent_learning_coach.core.llm.structured import (
    build_structured_instruction,
    extract_text,
    parse_structured_output,
)


class LiteLLMClient:
    """LiteLLM 适配器，满足 ``LLMClient`` Protocol。

    用法::

        client = LiteLLMClient(model="gpt-4o-mini")
        response = await client.generate(LLMRequest(instructions=["..."]))
    """

    def __init__(self, model: str, **config: Any) -> None:
        """初始化客户端。

        Args:
            model: LiteLLM 模型标识，例如 ``gpt-4o-mini``、``claude-sonnet-4-5``。
            **config: 透传给 LiteLLM 的默认参数，如 ``api_base``、``api_key``。
        """
        self.model = model
        self.config = config

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """执行一次 LLM 调用。"""
        try:
            from litellm import acompletion
        except ImportError as exc:  # pragma: no cover - 依赖缺失时的显式错误
            raise LLMError("未安装 litellm，无法使用 LiteLLMClient") from exc

        model = request.model or self.model
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
        payload.update(request.extra)

        try:
            raw = await acompletion(**payload)
        except Exception as exc:
            raise LLMError(f"LLM 调用失败: {exc}") from exc

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
