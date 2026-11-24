"""运行时的工具回调。

**这里是通用机制，不认识任何具体的数据格式。** 工具产出的结构化数据（引用、
审计日志、缓存命中……）想在运行结束后还能读到，就用 ``StateCollector`` 把它
收进执行上下文的草稿本。

**为什么放在这里而不是 ``core/tools``**：钩子要从 ``ToolContext.metadata`` 里
取出**执行上下文**才能写草稿本，而那个键的约定属于使用方（``core/agent``）。
放进 ``core/tools`` 会让工具系统反向依赖 Agent 运行时。

**为什么按调用时取执行上下文，而不是构造时绑定**：同一个调度器会服务多次运行
（``AgentRuntime.run`` 可以带不同的上下文）。构造时绑定会把草稿本钉死在第一
次运行上——第二次运行的产出就写丢了。调用时取，状态天然按执行隔离。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_learning_coach.core.agent.types import EXECUTION_CONTEXT_KEY
from agent_learning_coach.core.tools.types import ToolContext, ToolOutcome


class StateCollector:
    """把工具结果里的结构化数据收进执行上下文的草稿本。

    典型用法::

        collector = StateCollector("citations", data_key="citations")
        dispatcher = ToolDispatcher(registry, after_hooks=[collector])

    工具在 ``outcome.data["citations"]`` 里放结构化数据，钩子把它追加到
    ``ExecutionContext.state["citations"]``。**格式由工具与应用层约定**——
    这里只管搬运。

    **列表逐条追加，其余整体追加**：检索工具一次返回多条引用，它们应该各占
    一条，而不是被塞成一个嵌套列表。
    """

    def __init__(self, state_key: str, *, data_key: str) -> None:
        """初始化收集器。

        Args:
            state_key: 草稿本里的键。
            data_key: 工具结果 ``data`` 里的键。
        """
        self.state_key = state_key
        self.data_key = data_key

    def __call__(
        self,
        context: ToolContext,
        outcome: ToolOutcome,
        arguments: dict[str, Any],
    ) -> ToolOutcome | None:
        """收集结构化数据。

        Args:
            context: 运行时上下文。
            outcome: 工具结果。
            arguments: 这次调用的参数。收集器用不上，但签名要与
                ``AfterToolHook`` 一致。

        Returns:
            始终返回 None——收集器不改变结果。
        """
        payload = self._extract(outcome)
        if payload is None:
            return None
        execution_context = context.metadata.get(EXECUTION_CONTEXT_KEY)
        if execution_context is None:
            return None
        state = execution_context.state
        existing = state.get(self.state_key)
        if not isinstance(existing, list):
            existing = []
            state[self.state_key] = existing
        if isinstance(payload, list):
            existing.extend(payload)
        else:
            existing.append(payload)
        return None

    def _extract(self, outcome: ToolOutcome) -> Any:
        """从结果里取出要收集的数据。

        Args:
            outcome: 工具结果。

        Returns:
            数据；没有时返回 None。
        """
        if not outcome.succeeded:
            return None
        data = outcome.data
        if not isinstance(data, Mapping):
            return None
        payload = data.get(self.data_key)
        if payload is None:
            return None
        return payload


__all__ = ["StateCollector"]
