"""Agent Loop 的核心类型。

三个类型对应一次运行的三个阶段：

- **Agent Config**：运行参数——预算（步数、工具调用数）与停止阈值。
- **Agent Step**：一步的执行记录——模型输出、工具调用、工具结果。
- **Agent Run**：整次运行的结果——逐步记录、最终输出、停止原因、用量。

分清这三者的意义：Config 是「这次运行允许发生什么」，Step 是「实际发生了什么」，
Run 是「最终得到了什么」。三者混在一起会让「为什么提前停了」无法回答。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from agent_learning_coach.core.llm.models import LLMResponse, LLMUsage
from agent_learning_coach.core.llm.types import ContentItem, Message, ToolCall
from agent_learning_coach.core.tools.types import ToolOutcome

StopReason = Literal[
    "completed",
    "max_steps",
    "max_tool_calls",
    "consecutive_errors",
    "repeated_call",
    "timeout",
]
"""停止原因。

- ``completed``：模型不再请求工具，循环自然结束——这是**唯一**的成功状态。
- ``max_steps``：达到步数上限。
- ``max_tool_calls``：达到工具调用预算。
- ``consecutive_errors``：连续工具失败次数超限，模型陷入失败循环。
- ``repeated_call``：同一工具以相同参数被反复调用，模型陷入原地打转。
- ``timeout``：整体截止时间已到。

多个条件同时命中时，**更具体的原因优先**：``repeated_call`` 与
``consecutive_errors`` 指向模型行为，``max_steps`` 只是预算耗尽。因此模型
原地打转时上报 ``repeated_call`` 而不是 ``max_steps``。
"""


@dataclass(frozen=True)
class AgentConfig:
    """一次 Agent 运行的参数。

    预算类参数（``max_steps`` 等）的作用是**兜底**，不是主要停止路径。主要
    停止路径是「模型不再请求工具」——只有模型自己知道任务是否完成。
    """

    model: str | None = None
    """模型名。None 表示由客户端自身的默认模型决定。"""

    instructions: tuple[str, ...] = ()
    """系统级指令。每一轮都会带上。"""

    max_steps: int = 12
    """最多执行多少步（一步 = 一次模型调用）。

    这是防止死循环的硬上限。设得太小会让复杂任务过早收敛，设得太大则让
    失控的循环烧掉预算。默认 12 步对应「若干次工具调用 + 一次总结」。
    """

    max_tool_calls: int | None = None
    """工具调用总预算。None 表示不限制。

    预算在**步之间**检查：达到上限后不再开始新的一步；当前步内的调用全部
    执行完，保证每条 ``tool_call`` 都有对应的 ``tool_result``。
    """

    max_consecutive_errors: int = 3
    """连续工具失败次数上限。

    单次工具失败**不**终止循环——错误要回填给模型，让它换参数、换工具或
    直接回答。但连续失败说明模型已经陷在同一个错误里，继续回填只是浪费
    预算，因此到顶就停。None 表示不限制。
    """

    max_repeated_calls: int = 3
    """同一工具以相同参数被调用多少次后判定为原地打转。None 表示不限制。

    与 ``max_consecutive_errors`` 的区别：前者看「是否在重复同一个动作」，
    后者看「是否在重复失败」。一个工具每次都成功但被反复调用（例如模型
    忘了自己已经查过），前者能抓住；后者抓不住。
    """

    timeout: float | None = None
    """整体截止时间（秒）。None 表示不限。

    覆盖模型调用与工具执行在内的全部时间。超时后返回已完成的部分结果，
    而不是丢弃它们。
    """

    tool_choice: str | None = None
    """工具选择策略，透传给模型客户端。"""

    temperature: float | None = None
    """采样温度，透传给模型客户端。"""

    max_output_tokens: int | None = None
    """单次模型调用的最大输出 token 数，透传给模型客户端。"""


@dataclass(frozen=True)
class AgentStep:
    """一步的执行记录。"""

    index: int
    """步序号，从 0 开始。"""

    response: LLMResponse
    """本步的模型响应。"""

    tool_calls: tuple[ToolCall, ...] = ()
    """模型请求的工具调用。空元组表示模型没有请求任何工具。"""

    outcomes: tuple[ToolOutcome, ...] = ()
    """工具执行结果，与 ``tool_calls`` 一一对应。"""

    @property
    def executed_tool_calls(self) -> int:
        """本步实际执行的工具调用数。"""
        return len(self.outcomes)

    @property
    def failed(self) -> bool:
        """本步是否有工具调用失败。"""
        return any(not outcome.succeeded for outcome in self.outcomes)


@dataclass(frozen=True)
class AgentRun:
    """一次 Agent 运行的完整结果。"""

    steps: tuple[AgentStep, ...] = ()
    """逐步的执行记录。"""

    stop_reason: StopReason = "completed"
    """停止原因。只有 ``"completed"`` 表示正常结束。"""

    contents: tuple[ContentItem, ...] = ()
    """完整的对话记录。

    保证不变量：每条 ``ToolCall`` 都有对应的 ``ToolResult``。因此这份记录
    可以直接作为下一轮对话的输入继续。
    """

    usage: LLMUsage = field(default_factory=LLMUsage)
    """累计 token 用量。"""

    @property
    def succeeded(self) -> bool:
        """是否正常结束。"""
        return self.stop_reason == "completed"

    @property
    def tool_call_count(self) -> int:
        """本次运行累计执行的工具调用数。"""
        return sum(step.executed_tool_calls for step in self.steps)

    @property
    def final_text(self) -> str:
        """最后一条 assistant 文本。

        模型在结束前通常会给一段说明性文字。没有文本时返回空串。
        """
        for item in reversed(self.contents):
            if isinstance(item, Message) and item.role == "assistant":
                return item.content
        return ""


__all__ = [
    "AgentConfig",
    "AgentRun",
    "AgentStep",
    "StopReason",
]
