"""工具调度器。

Dispatcher 是工具系统的**执行边界**，负责一次调用的完整生命周期：

```
查找工具 -> 校验参数 -> 执行（带超时）-> 归一化结果
```

每一步的失败都被收敛成 ``ToolOutcome``（``status="error"``），而不是向上抛。
原因：调用工具的主体是模型，模型能读懂错误信息并据此调整下一步（换参数、
换工具、直接回答）；而直接抛异常会中断整个 Agent Loop。

唯一的例外是**工具不存在**：这是配置错误而非调用错误，模型无法通过调整
参数修复，因此抛 ``ToolNotFoundError``。但调用方通常希望统一处理，Dispatcher
提供 ``dispatch``（抛）与 ``dispatch_safe``（归一化）两种入口。

超时用 ``asyncio.timeout`` 实现：它既能取消挂起的 await，又能保证取消信号
送达被调用的协程，而不是仅仅放弃等待。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from agent_learning_coach.core.llm.models import ToolSpec
from agent_learning_coach.core.tools.errors import (
    ToolError,
    ToolExecutionError,
    ToolPermissionError,
    ToolTimeoutError,
)
from agent_learning_coach.core.tools.normalization import (
    normalize_error,
    normalize_outcome,
)
from agent_learning_coach.core.tools.permissions import (
    AllowAllPolicy,
    PermissionPolicy,
)
from agent_learning_coach.core.tools.registry import ToolRegistry
from agent_learning_coach.core.tools.schema import validate_arguments
from agent_learning_coach.core.tools.types import ToolContext, ToolOutcome


class ToolDispatcher:
    """工具调度器。

    组合注册表与执行策略：按名字找工具、检查权限、校验参数、执行、归一化结果。

    **权限检查放在这里**：Dispatcher 是唯一的执行边界，所有工具来源（本地、
    MCP、结构化输出的 ``final_answer``）都经过它。检查点只有一个，就不存在
    「新增一条执行路径忘了检查」这种 fail-open 漏洞。
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        default_timeout: float | None = None,
        policy: PermissionPolicy | None = None,
    ) -> None:
        """初始化调度器。

        Args:
            registry: 工具注册表。
            default_timeout: 默认超时（秒）。工具自身定义了 ``timeout`` 时以工具的
                为准；两者都未设置则不限制执行时长。
            policy: 权限策略。为 None 时放行一切（``AllowAllPolicy``）。
        """
        self.registry = registry
        self.default_timeout = default_timeout
        self.policy: PermissionPolicy = policy if policy is not None else AllowAllPolicy()

    def allowed_specs(self, *, caller: str | None = None) -> list[ToolSpec]:
        """导出当前策略允许的工具清单。

        这是**可见性**过滤：不给模型看它无权调用的工具，模型就不会浪费轮次
        去尝试。真正的强制在 ``dispatch`` 里。

        Args:
            caller: 调用方标识。供按调用方区分授权的策略使用。

        Returns:
            允许调用的工具契约，按工具名排序。
        """
        specs: list[ToolSpec] = []
        for definition in self.registry.definitions():
            if self.policy.check(definition, caller=caller).allowed:
                specs.append(definition.to_spec())
        return specs

    def resolve_timeout(self, tool_name: str) -> float | None:
        """确定一次调用实际生效的超时。

        优先级：工具定义上的 ``timeout`` > Dispatcher 的 ``default_timeout``。

        Args:
            tool_name: 工具名。

        Returns:
            超时秒数；不限制时返回 None。
        """
        definition = self.registry.get(tool_name).definition
        if definition.timeout is not None:
            return definition.timeout
        return self.default_timeout

    async def dispatch(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolOutcome:
        """执行一次工具调用。

        Args:
            tool_name: 工具名。
            arguments: 工具参数，将先经过 Schema 校验。
            context: 运行时上下文。

        Returns:
            归一化后的执行结果。参数不合法、超时、执行异常都返回
            ``status="error"`` 的结果，而不是抛异常。

        Raises:
            ToolNotFoundError: 工具未注册。
        """
        tool = self.registry.get(tool_name)
        definition = tool.definition
        started = time.monotonic()

        # 权限检查在最前面：被拒绝的工具不进入校验与执行。既不浪费工作，
        # 也不把「参数应该长什么样」泄露给无权调用的一方。
        decision = self.policy.check(definition, caller=context.caller)
        if not decision.allowed:
            return normalize_error(
                tool_name,
                ToolPermissionError(decision.reason),
                duration_ms=_elapsed_ms(started),
                metadata={"permission_denied": True},
            )

        try:
            validate_arguments(definition.parameters, arguments)
        except ToolError as exc:
            return normalize_error(
                tool_name,
                exc,
                duration_ms=_elapsed_ms(started),
            )

        timeout = self.resolve_timeout(tool_name)
        try:
            value = await self._execute(tool, context, arguments, timeout)
        except ToolError as exc:
            return normalize_error(
                tool_name,
                exc,
                duration_ms=_elapsed_ms(started),
            )
        except Exception as exc:
            return normalize_error(
                tool_name,
                ToolExecutionError(f"工具 {tool_name} 执行异常：{exc}", cause=exc),
                duration_ms=_elapsed_ms(started),
            )

        return normalize_outcome(
            tool_name,
            value,
            duration_ms=_elapsed_ms(started),
        )

    async def dispatch_safe(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolOutcome:
        """执行一次工具调用，把所有失败都归一化成结果。

        与 ``dispatch`` 的区别仅在于：工具不存在也返回错误结果而非抛异常。
        适合「批量执行工具调用列表」的场景，避免第一个失败就中断整批。

        Args:
            tool_name: 工具名。
            arguments: 工具参数。
            context: 运行时上下文。

        Returns:
            归一化后的执行结果。
        """
        try:
            return await self.dispatch(tool_name, arguments, context)
        except ToolError as exc:
            return normalize_error(tool_name, exc)

    async def dispatch_all(
        self,
        calls: list[tuple[str, dict[str, Any]]],
        context: ToolContext,
    ) -> list[ToolOutcome]:
        """顺序执行一批工具调用。

        Args:
            calls: ``(工具名, 参数)`` 列表。
            context: 运行时上下文。

        Returns:
            与输入顺序一一对应的结果列表。
        """
        results: list[ToolOutcome] = []
        for tool_name, arguments in calls:
            results.append(await self.dispatch_safe(tool_name, arguments, context))
        return results

    async def _execute(
        self,
        tool: Any,
        context: ToolContext,
        arguments: dict[str, Any],
        timeout: float | None,
    ) -> Any:
        """在超时约束下执行工具。

        Args:
            tool: 工具实现。
            context: 运行时上下文。
            arguments: 已校验的参数。
            timeout: 超时秒数；None 表示不限制。

        Returns:
            工具的原始返回值。

        Raises:
            ToolTimeoutError: 执行超时。
        """
        if timeout is None:
            return await tool.execute(context, arguments)

        try:
            async with asyncio.timeout(timeout):
                return await tool.execute(context, arguments)
        except TimeoutError as exc:
            raise ToolTimeoutError(f"工具执行超时（{timeout}s）") from exc


def _elapsed_ms(started: float) -> float:
    """计算自起点以来的耗时（毫秒）。

    Args:
        started: ``time.monotonic()`` 起点。

    Returns:
        耗时毫秒数。
    """
    return (time.monotonic() - started) * 1000.0


__all__ = ["ToolDispatcher"]
