"""会话上下文。

**一次运行 ≠ 一次会话。** 一次运行（``AgentRuntime.run``）是模型与工具之间
的一轮循环；一次会话是学习者与教练之间的**多轮对话**。前者有明确的结束，
后者没有——它只在学习者离开时才结束。

因此会话需要一个**跨运行持续存在的存储**。本模块提供的
``SessionContext`` 就是它：一个持有 ``ExecutionContext`` 的会话，持续累积
对话，轮次累加。

**为什么放在 ``core/agent`` 而不是 ``core/context``**：会话持有的是
``ExecutionContext``——那是 ``core/agent`` 的类型。若把它放进 ``core/context``，
上下文层就要反向依赖运行时层，破坏本项目一贯的依赖方向
（``core/context`` 只依赖 ``core/llm`` 的厂商无关类型）。

**本阶段只做基础形态**：内存里的会话状态。没有持久化、没有暂停恢复、没有
人工确认——那些属于 Day 09「Session 与 HITL」。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from agent_learning_coach.core.agent.runtime import AgentRuntime
from agent_learning_coach.core.agent.types import AgentResult, ExecutionContext
from agent_learning_coach.core.llm.types import ContentItem


@dataclass
class SessionContext:
    """跨多次运行持续存在的会话。

    典型用法::

        session = SessionContext()
        await session.run(runtime, "帮我学闭包")
        await session.run(runtime, "那什么是装饰器？")

    第二次运行时，模型能看到第一次的完整对话——因为两者共享同一个
    ``ExecutionContext``。
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    """会话标识。跨多次运行保持不变。"""

    context: ExecutionContext = field(default_factory=ExecutionContext)
    """会话的存储侧：完整对话历史，跨多次运行累积。"""

    turns: int = 0
    """已发起的轮次。

    失败的运行也计数——学习者确实问了一次，只是这次没得到回答。历史里那条
    未被回答的问题会留在 ``context`` 里，因此下一轮模型能看到「上次这里
    没接上」。
    """

    async def run(
        self,
        runtime: AgentRuntime,
        prompt: str | list[ContentItem],
    ) -> AgentResult:
        """在同一会话里执行一轮。

        Args:
            runtime: 执行本次运行的 Agent 运行时。
            prompt: 本轮的用户输入。

        Returns:
            运行结果。它携带的 ``context`` 就是本会话的 ``context``。
        """
        result = await runtime.run(prompt, context=self.context)
        self.turns += 1
        return result

    @property
    def contents(self) -> list[ContentItem]:
        """会话当前的完整对话内容。"""
        return self.context.contents

    @property
    def state(self) -> dict[str, Any]:
        """会话的草稿本。跨轮次保留。"""
        return self.context.state


__all__ = ["SessionContext"]
