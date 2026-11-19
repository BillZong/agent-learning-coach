"""Agent Runtime：ReAct 执行循环 + 运行时状态。

一次运行的形状是::

    Think  ->  Act  ->  Observe  ->  Think -> ...
    (模型)     (工具)     (结果回填)      (模型)

三个阶段各自是一个方法，职责单一、可独立测试：

**Thought**（``_think``）：把当前对话与工具清单交给模型，模型要么给出回答，
要么请求调用工具。

**Action**（``_act``）：执行模型请求的工具调用。执行走 ``ToolDispatcher``，
因此参数校验、超时与结果归一化与本地工具完全一致。

**Observation**（``_observe``）：把工具结果回填给模型，让它看到事实后继续
推理。

## 状态：Execution Context

运行过程中的**全部状态**都记在 ``ExecutionContext`` 里——事件历史、步数、
草稿本、用量与成本。运行时不另设一份状态，因此「运行发生了什么」只有一个
答案：上下文里的事件序列。

这让运行**可测试、可复现**：给定同样的输入与同样的事件序列，就能得到同样
的结论。

## 停止策略

Topic 05 提出的问题是：**循环在什么条件下必须停止？Stop Condition 与 Max
Steps 如何配合，既避免死循环，也避免过早收敛？**

本实现的答案是分层的：

1. **自然结束是主路径**：模型不再请求工具就结束。只有模型自己知道任务是否
   完成，任何预算都不该替代这个判断。
2. **工具失败不终止循环**：失败被归一化成错误结果回填给模型，让它换参数、
   换工具或直接回答——这正是工具系统「调用级失败归一化」的意义。模型能
   自愈的错误就让它自愈。
3. **预算是兜底，不是主路径**：步数、工具调用数、token 数、成本、连续失败
   数、重复调用数六个阈值只在模型**不再前进**时兜底。它们的存在是为了让失控
   的循环一定会停下来，而不是为了提前结束一个正在取得进展的任务。

「过早收敛」的防护因此来自两条规则：预算是兜底而非目标；工具错误回填而非
终止。

## 成本控制

价格表由调用方提供（``AgentConfig.prices``），运行时只负责核算与执行预算。
设了 ``max_cost`` 却没有覆盖实际模型的单价属于配置错误，直接抛
``AgentConfigError``——预算无法被执行就等于没设。

## 边界

- 模型调用失败（``LLMError``）**不重试**，但也不丢弃已完成的结果：运行以
  ``stop_reason="llm_error"`` 结束，部分结果照常返回。重试策略由调用方决定
  ——它知道这次运行值不值得重跑。
- 工具**顺序执行**，不并发。并发执行需要考虑取消、超时传播与结果的确定性
  顺序，属于后续议题。
- 上下文裁剪（记忆管理）不在本模块：对话记录原样保留并返回，由调用方决定
  如何压缩。
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter

from agent_learning_coach.core.agent.errors import AgentConfigError
from agent_learning_coach.core.agent.types import (
    EXECUTION_CONTEXT_KEY,
    AgentConfig,
    AgentResult,
    AgentStatus,
    AgentStep,
    Event,
    ExecutionContext,
    StopReason,
)
from agent_learning_coach.core.llm.errors import LLMError
from agent_learning_coach.core.llm.models import LLMRequest, LLMResponse
from agent_learning_coach.core.llm.protocol import LLMClient
from agent_learning_coach.core.llm.types import ContentItem, Message, ToolCall
from agent_learning_coach.core.tools.dispatcher import ToolDispatcher
from agent_learning_coach.core.tools.types import ToolContext, ToolOutcome


class AgentRuntime:
    """ReAct 执行循环 + 运行时状态。

    职责边界：只管「模型 ↔ 工具」的循环控制与状态记录。模型调用委托给
    ``LLMClient``，工具执行委托给 ``ToolDispatcher``，两者都不在本类里实现
    ——因此换成别的模型或别的工具来源都不需要改运行时。
    """

    def __init__(
        self,
        client: LLMClient,
        dispatcher: ToolDispatcher,
        *,
        config: AgentConfig | None = None,
        name: str = "agent",
    ) -> None:
        """初始化运行时。

        Args:
            client: 模型客户端。
            dispatcher: 工具调度器。
            config: 运行参数。为 None 时用默认配置。
            name: 运行时的名字，用于事件作者标记。
        """
        self.client = client
        self.dispatcher = dispatcher
        self.config = config or AgentConfig()
        self.name = name

    async def run(
        self,
        prompt: str | list[ContentItem],
        *,
        context: ExecutionContext | None = None,
        tool_context: ToolContext | None = None,
    ) -> AgentResult:
        """执行一次完整的 Agent 运行。

        Args:
            prompt: 用户输入。可以是一段文本，或一组厂商无关的内容项。
            context: 执行上下文。为 None 时新建一个——传入已有上下文可以
                续接之前的对话。
            tool_context: 工具执行上下文。为 None 时构造一个只带调用方标识
                的上下文。

        Returns:
            运行结果。即使是超时或预算耗尽，也会返回已完成的部分结果——
            调用方能知道「走到了哪一步」以及「为什么停」。
        """
        config = self.config
        ctx = context if context is not None else ExecutionContext()
        tools_ctx = tool_context or ToolContext(
            caller="agent_runtime",
            metadata={EXECUTION_CONTEXT_KEY: ctx},
        )

        ctx.add_event(Event.create(ctx.execution_id, "user", _normalize_prompt(prompt)))

        steps: list[AgentStep] = []
        total_tool_calls = 0
        consecutive_errors = 0
        call_counts: Counter[str] = Counter()
        deadline = time.monotonic() + config.timeout if config.timeout is not None else None

        for index in range(config.max_steps):
            # 预算在步之间检查：达到上限就不再开始新的一步。当前步内的调用
            # 全部执行完，保证每条 tool_call 都有对应的 tool_result。
            budget_stop = self._check_budgets(ctx, total_tool_calls)
            if budget_stop is not None:
                return self._result(ctx, steps, budget_stop)

            # --- Thought：模型决定下一步要做什么 ---
            try:
                response = await self._think(ctx, deadline)
            except TimeoutError:
                return self._result(ctx, steps, "timeout")
            except LLMError as exc:
                # 模型调用失败与超时是同一类运行时失败：都要返回已完成的部分
                # 结果，而不是丢弃整个运行。调用方据此决定是否重跑。
                return self._result(ctx, steps, "llm_error", error=f"{type(exc).__name__}: {exc}")

            ctx.record_usage(response.usage, response.model or config.model, config.prices)
            ctx.add_event(Event.create(ctx.execution_id, self.name, response.contents))

            tool_calls = tuple(item for item in response.contents if isinstance(item, ToolCall))

            if not tool_calls:
                # 模型不再请求工具——循环自然结束，这是唯一的成功路径。
                steps.append(AgentStep(index=index, response=response))
                ctx.increment_step()
                return self._result(ctx, steps, "completed")

            # --- Action：执行模型请求的工具 ---
            outcomes = await self._act(tool_calls, tools_ctx)

            # --- Observation：把工具结果回填给模型 ---
            self._observe(ctx, tool_calls, outcomes)

            for outcome in outcomes:
                if outcome.succeeded:
                    consecutive_errors = 0
                else:
                    consecutive_errors += 1

            for call in tool_calls:
                total_tool_calls += 1
                call_counts[_call_key(call)] += 1

            step = AgentStep(
                index=index,
                response=response,
                tool_calls=tool_calls,
                outcomes=tuple(outcomes),
            )
            steps.append(step)
            ctx.increment_step()

            stop = self._check_stop_conditions(
                consecutive_errors=consecutive_errors,
                call_counts=call_counts,
            )
            if stop is not None:
                return self._result(ctx, steps, stop)

        return self._result(ctx, steps, "max_steps")

    async def _think(
        self,
        ctx: ExecutionContext,
        deadline: float | None,
    ) -> LLMResponse:
        """执行一次模型调用（Thought）。

        Args:
            ctx: 执行上下文。
            deadline: 整体截止时刻（``time.monotonic`` 基准）。None 表示不限。

        Returns:
            模型响应。

        Raises:
            TimeoutError: 超过整体截止时间。
        """
        config = self.config
        request = LLMRequest(
            model=config.model,
            instructions=list(config.instructions),
            contents=ctx.contents,
            tools=self.dispatcher.registry.specs(),
            tool_choice=config.tool_choice,
            temperature=config.temperature,
            max_output_tokens=config.max_output_tokens,
        )

        if deadline is None:
            return await self.client.generate(request)

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        async with asyncio.timeout(remaining):
            return await self.client.generate(request)

    async def _act(
        self,
        tool_calls: tuple[ToolCall, ...],
        tool_context: ToolContext,
    ) -> list[ToolOutcome]:
        """执行模型请求的工具（Action）。

        执行委托给 ``ToolDispatcher``，因此参数校验、超时与结果归一化与本地
        工具完全一致。调用级失败（参数不合法、工具内部报错、工具不存在）都
        被归一化成错误结果而不是抛出——它们要回填给模型，让模型自愈。

        Args:
            tool_calls: 模型请求的工具调用。
            tool_context: 工具执行上下文。

        Returns:
            与 ``tool_calls`` 一一对应的执行结果。
        """
        outcomes: list[ToolOutcome] = []
        for call in tool_calls:
            outcomes.append(
                await self.dispatcher.dispatch_safe(call.name, call.arguments, tool_context)
            )
        return outcomes

    @staticmethod
    def _observe(
        ctx: ExecutionContext,
        tool_calls: tuple[ToolCall, ...],
        outcomes: list[ToolOutcome],
    ) -> None:
        """把工具结果回填给模型（Observation）。

        回填的是 ``ToolResult``——模型能读懂的内容。这是「观察」的意义：让
        模型看到事实，而不是让它推理工具内部的数据结构。

        工具调用 id 来自**调用**而非执行结果：``ToolOutcome`` 描述的是工具
        本身（「哪个工具、执行成没成」），不知道它属于哪一次调用。

        Args:
            ctx: 执行上下文。
            tool_calls: 模型请求的工具调用。
            outcomes: 与之对应的执行结果。
        """
        results = [
            outcome.to_message_result(call.tool_call_id)
            for call, outcome in zip(tool_calls, outcomes, strict=True)
        ]
        ctx.add_event(Event.create(ctx.execution_id, "tools", results))

    def _check_budgets(
        self,
        ctx: ExecutionContext,
        total_tool_calls: int,
    ) -> StopReason | None:
        """检查预算类停止条件。

        Args:
            ctx: 执行上下文。
            total_tool_calls: 已执行的工具调用数。

        Returns:
            停止原因；未超预算时返回 None。
        """
        config = self.config
        if config.max_tool_calls is not None and total_tool_calls >= config.max_tool_calls:
            return "max_tool_calls"
        if (
            config.max_total_tokens is not None
            and ctx.usage.input_tokens + ctx.usage.output_tokens >= config.max_total_tokens
        ):
            return "max_tokens"
        if config.max_cost is not None and ctx.cost >= config.max_cost:
            return "max_cost"
        return None

    def _check_stop_conditions(
        self,
        *,
        consecutive_errors: int,
        call_counts: Counter[str],
    ) -> StopReason | None:
        """判断是否应当停止。

        只检查「模型不再前进」的两类信号：连续失败与原地打转。步数、工具
        调用预算、token 与成本预算在步之间检查。

        Args:
            consecutive_errors: 当前连续工具失败次数。
            call_counts: 各次工具调用的累计次数。

        Returns:
            停止原因；应继续时返回 None。
        """
        config = self.config

        if (
            config.max_consecutive_errors is not None
            and consecutive_errors >= config.max_consecutive_errors
        ):
            return "consecutive_errors"

        if config.max_repeated_calls is not None:
            if call_counts and max(call_counts.values()) >= config.max_repeated_calls:
                return "repeated_call"

        return None

    def _result(
        self,
        ctx: ExecutionContext,
        steps: list[AgentStep],
        stop_reason: StopReason,
        *,
        error: str | None = None,
    ) -> AgentResult:
        """打包运行结果。

        Args:
            ctx: 执行上下文。
            steps: 逐步记录。
            stop_reason: 停止原因。
            error: 失败原因。

        Returns:
            运行结果。
        """
        ctx.final_result = _last_assistant_text(ctx)
        status: AgentStatus = "complete" if stop_reason == "completed" else "error"
        return AgentResult(
            context=ctx,
            output=ctx.final_result,
            status=status,
            stop_reason=stop_reason,
            steps=tuple(steps),
            error=error,
        )


def _normalize_prompt(prompt: str | list[ContentItem]) -> list[ContentItem]:
    """把用户输入规范化为内容项列表。

    Args:
        prompt: 文本或内容项列表。

    Returns:
        内容项列表。
    """
    if isinstance(prompt, str):
        return [Message(role="user", content=prompt)]
    return list(prompt)


def _last_assistant_text(ctx: ExecutionContext) -> str | None:
    """取出最后一条 assistant 文本。

    Args:
        ctx: 执行上下文。

    Returns:
        文本；没有时返回 None。
    """
    for item in reversed(ctx.contents):
        if isinstance(item, Message) and item.role == "assistant":
            return item.content
    return None


def _call_key(call: ToolCall) -> str:
    """生成工具调用的去重键。

    参数用排序后的 JSON 序列化，保证「参数顺序不同但内容相同」被视为同一次
    调用。

    Args:
        call: 工具调用。

    Returns:
        去重键。
    """
    arguments = json.dumps(call.arguments, sort_keys=True, ensure_ascii=False, default=str)
    return f"{call.name}:{arguments}"


__all__ = ["AgentConfigError", "AgentRuntime"]
