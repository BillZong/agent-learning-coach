"""Provider-neutral request/response models for LLM calls."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agent_learning_coach.core.llm.types import ContentItem


class ToolSpec(BaseModel):
    """工具的厂商无关描述。

    ``parameters`` 使用 JSON Schema 描述参数结构，各厂商适配器负责转成
    自身的 function calling 格式。
    """

    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)


class LLMRequest(BaseModel):
    """一次 LLM 调用的完整输入。"""

    model: str | None = None
    instructions: list[str] = Field(default_factory=list)
    contents: list[ContentItem] = Field(default_factory=list)
    tools: list[ToolSpec] = Field(default_factory=list)
    tool_choice: str | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    def append_instructions(self, text: str) -> None:
        """追加一条系统级指令。"""
        self.instructions.append(text)


class LLMUsage(BaseModel):
    """一次调用的 token 用量。"""

    input_tokens: int = 0
    output_tokens: int = 0


class LLMResponse(BaseModel):
    """一次 LLM 调用的结果。"""

    model: str | None = None
    contents: list[ContentItem] = Field(default_factory=list)
    usage: LLMUsage = Field(default_factory=LLMUsage)
    finish_reason: str | None = None
