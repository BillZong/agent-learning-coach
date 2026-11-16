"""厂商无关消息模型与 OpenAI 兼容消息格式之间的转换。

LiteLLM 统一采用 OpenAI 兼容的 ``messages`` / ``tools`` 结构，
因此这里集中实现一次转换，供所有基于 LiteLLM 的适配器复用。
"""

from __future__ import annotations

import json
from typing import Any

from agent_learning_coach.core.llm.models import LLMRequest, LLMResponse, LLMUsage, ToolSpec
from agent_learning_coach.core.llm.types import Message, ToolCall, ToolResult


def build_messages(request: LLMRequest) -> list[dict[str, Any]]:
    """把 ``LLMRequest`` 转换为 OpenAI 兼容的 messages 列表。"""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": instruction} for instruction in request.instructions
    ]

    for item in request.contents:
        if isinstance(item, Message):
            messages.append({"role": item.role, "content": item.content})

        elif isinstance(item, ToolCall):
            tool_call = {
                "id": item.tool_call_id,
                "type": "function",
                "function": {
                    "name": item.name,
                    "arguments": json.dumps(item.arguments, ensure_ascii=False),
                },
            }
            if messages and messages[-1]["role"] == "assistant":
                messages[-1].setdefault("tool_calls", []).append(tool_call)
            else:
                messages.append({"role": "assistant", "content": None, "tool_calls": [tool_call]})

        elif isinstance(item, ToolResult):
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item.tool_call_id,
                    "content": item.content,
                }
            )

    return messages


def build_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
    """把 ``ToolSpec`` 列表转换为 OpenAI 兼容的 tools 定义。"""
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters or {"type": "object", "properties": {}},
            },
        }
        for tool in tools
    ]


def parse_response(raw: Any, model: str | None = None) -> LLMResponse:
    """把 LiteLLM 返回的原始响应转换为 ``LLMResponse``。"""
    choice = raw.choices[0]
    message = choice.message

    contents: list[Message | ToolCall | ToolResult] = []
    if message.content:
        contents.append(Message(role="assistant", content=message.content))

    for tool_call in message.tool_calls or []:
        arguments = tool_call.function.arguments or "{}"
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            parsed = {"_raw": arguments}
        contents.append(
            ToolCall(
                tool_call_id=tool_call.id,
                name=tool_call.function.name,
                arguments=parsed,
            )
        )

    usage = getattr(raw, "usage", None)
    return LLMResponse(
        model=getattr(raw, "model", model),
        contents=contents,
        usage=LLMUsage(
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        ),
        finish_reason=getattr(choice, "finish_reason", None),
    )
