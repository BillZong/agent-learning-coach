"""Provider-neutral conversation content types.

这些类型描述“对话内容”本身，不包含任何厂商 SDK 的对象。
厂商适配器负责在内部完成与厂商消息格式的互转。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]


class Message(BaseModel):
    """一条纯文本消息。"""

    type: Literal["message"] = "message"
    role: Role
    content: str


class ToolCall(BaseModel):
    """模型请求执行某个工具的调用请求。"""

    type: Literal["tool_call"] = "tool_call"
    tool_call_id: str
    name: str
    arguments: dict = Field(default_factory=dict)


class ToolResult(BaseModel):
    """工具执行结果，回填给模型继续推理。"""

    type: Literal["tool_result"] = "tool_result"
    tool_call_id: str
    name: str
    status: Literal["success", "error"] = "success"
    content: str = ""


ContentItem = Message | ToolCall | ToolResult
