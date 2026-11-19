"""工具系统的核心类型：Tool Definition、Tool Context、Tool Result。

三个类型对应工具生命周期的三个阶段：

- **Tool Definition**：工具是什么。名称、描述、参数 JSON Schema、执行入口。
  它是**静态契约**，可以原样发给模型。
- **Tool Context**：工具在什么上下文里执行。会话 id、调用方、截止时间、
  附加元数据。它是**运行时环境**，随调用变化。
- **Tool Result**：工具执行后得到什么。状态、内容、错误、耗时。它是**归一化
  的产物**：无论工具内部返回什么类型，对外都是同一个结构。

分清这三个概念的意义：Definition 是给模型看的（决定模型会不会调它），
Context 是给工具看的（决定工具能做什么），Result 是给调用方和模型看的
（决定后续推理怎么走）。三者混在一起会导致工具无法复用、结果无法比较。
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from agent_learning_coach.core.llm.models import ToolSpec
from agent_learning_coach.core.llm.types import ToolResult

ToolHandler = Callable[["ToolContext", dict[str, Any]], Awaitable[Any]]
"""工具执行入口：接收上下文与已校验的参数，返回任意类型的执行结果。"""


class ToolDefinition(BaseModel):
    """工具的静态契约。

    这是一个可序列化的描述，既能注册到 ``ToolRegistry``，也能直接转换成
    ``ToolSpec`` 发给模型。
    """

    name: str
    """工具名。必须与模型请求中的工具名一致。"""

    description: str = ""
    """工具描述。模型的调用决策高度依赖这段文字，应说明「做什么」而非「怎么做」。"""

    parameters: dict[str, Any] = Field(default_factory=dict)
    """参数 JSON Schema。空字典表示不接受参数。

    约定使用 JSON Schema draft 2020-12 子集：``type``、``properties``、
    ``required``、``additionalProperties``、``items``、``enum``、
    ``minimum`` / ``maximum``、``minLength`` / ``maxLength``。
    """

    timeout: float | None = None
    """执行超时（秒）。为 None 时使用 Dispatcher 的默认超时。"""

    scopes: tuple[str, ...] = ()
    """工具执行所需的权限作用域，如 ``("fs:read", "net:fetch")``。

    属于工具的静态契约，但**不会发给模型**——它是运行时的授权依据，不是
    模型的输入。``ToolSpec`` 只带 name/description/parameters，作用域因此
    不会泄露给模型。

    未声明作用域的工具在任何作用域策略下都放行，因此接入不可信来源的工具
    必须显式声明。
    """

    metadata: dict[str, Any] = Field(default_factory=dict)
    """附加元数据。不参与校验，供调用方与观测使用。"""

    def to_spec(self) -> ToolSpec:
        """转换为发给模型的 ``ToolSpec``。"""
        return ToolSpec(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
        )


@dataclass(frozen=True)
class ToolContext:
    """工具执行的运行时上下文。

    冻结为不可变对象：上下文在一次调用中是只读的，工具若需要传递状态应通过
    ``metadata`` 或返回值，而不是修改上下文。
    """

    session_id: str | None = None
    """会话标识。用于把工具调用与学习会话关联。"""

    caller: str | None = None
    """调用方标识，如 ``"agent_loop"``、``"hitl"``。"""

    deadline: float | None = None
    """单调时钟截止时间（``time.monotonic()`` 基准）。None 表示无整体截止时间。

    工具可据此主动放弃长任务，避免在整体超时后才被取消。
    """

    metadata: dict[str, Any] = field(default_factory=dict)
    """附加上下文数据。"""

    def remaining_seconds(self) -> float | None:
        """返回距离截止时间的剩余秒数。

        Returns:
            剩余秒数；无截止时间时返回 None；已过期时返回负数。
        """
        if self.deadline is None:
            return None
        return self.deadline - time.monotonic()


@dataclass(frozen=True)
class ToolOutcome:
    """工具执行的归一化结果。

    无论工具返回字符串、字典、Pydantic 模型还是抛异常，Dispatcher 都把它
    归一化成这个结构，调用方因此可以用统一方式处理结果与错误。
    """

    tool_name: str
    """被执行的工具名。"""

    status: str
    """执行状态：``"success"`` 或 ``"error"``。"""

    content: str = ""
    """文本化结果。成功时是工具产出的内容，失败时是错误描述。

    始终是可安全回填给模型的字符串——这是归一化的核心目的：让模型能读懂
    工具的结果，而不必理解工具内部的数据结构。
    """

    data: dict[str, Any] | None = None
    """结构化结果。仅当工具返回 dict 时填充，供程序化消费。"""

    error: str | None = None
    """错误描述。失败时填充。"""

    duration_ms: float = 0.0
    """执行耗时（毫秒）。"""

    metadata: dict[str, Any] = field(default_factory=dict)
    """附加元数据，如重试次数、实际使用的模型等。"""

    @property
    def succeeded(self) -> bool:
        """是否执行成功。"""
        return self.status == "success"

    def to_message_result(self, tool_call_id: str) -> ToolResult:
        """转换为可回填给模型的 ``ToolResult`` 消息。

        Args:
            tool_call_id: 对应的工具调用 id。

        Returns:
            厂商无关的工具结果消息。
        """
        return ToolResult(
            tool_call_id=tool_call_id,
            name=self.tool_name,
            status="success" if self.succeeded else "error",
            content=self.content,
        )


@runtime_checkable
class Tool(Protocol):
    """工具协议。

    实现方无需继承，只要提供同名成员即可。``ToolDefinition`` 是静态契约，
    ``execute`` 是执行入口。
    """

    @property
    def definition(self) -> ToolDefinition:
        """工具的静态契约。"""
        ...

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> Any:
        """执行工具。

        Args:
            context: 运行时上下文。
            arguments: 已通过 Schema 校验的参数。

        Returns:
            任意类型的执行结果，由 Dispatcher 归一化。
        """
        ...


@dataclass
class FunctionTool:
    """把普通函数包装成 ``Tool``。

    用于快速接入已有函数：传入定义与可调用对象即可，无需实现协议类。
    """

    definition: ToolDefinition
    """工具的静态契约。"""

    func: ToolHandler
    """执行入口。"""

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> Any:
        """执行被包装的函数。"""
        return await self.func(context, arguments)


__all__ = [
    "FunctionTool",
    "Tool",
    "ToolContext",
    "ToolDefinition",
    "ToolHandler",
    "ToolOutcome",
]
