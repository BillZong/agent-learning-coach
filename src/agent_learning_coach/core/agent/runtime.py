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
from collections.abc import Sequence

from pydantic import BaseModel

from agent_learning_coach.core.agent.errors import AgentConfigError, AgentError
from agent_learning_coach.core.agent.output import (
    FINAL_ANSWER_TOOL_NAME,
    build_final_answer_tool,
)
from agent_learning_coach.core.agent.types import (
    EXECUTION_CONTEXT_KEY,
    AgentConfig,
    AgentResult,
    AgentStatus,
    AgentStep,
    ApprovalDecision,
    Event,
    ExecutionContext,
    PendingApproval,
    StopReason,
)
from agent_learning_coach.core.llm.errors import LLMError
from agent_learning_coach.core.llm.models import LLMRequest, LLMResponse
from agent_learning_coach.core.llm.protocol import LLMClient
from agent_learning_coach.core.llm.types import ContentItem, Message, ToolCall
from agent_learning_coach.core.tools.dispatcher import ToolDispatcher
from agent_learning_coach.core.tools.registry import ToolRegistry
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
        self.config = config or AgentConfig()
        self.dispatcher = _compose_dispatcher(dispatcher, self.config.output_model)
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
        ctx = context if context is not None else ExecutionContext()
        if ctx.pending_approvals:
            # 这个运行正等着人工批准。再发起一次模型调用会让请求里带着
            # 没有结果的 tool_call，厂商 API 会拒绝整个请求。
            raise AgentError(
                "这个运行处于暂停状态，等待人工批准；请用 resume() 恢复，而不是再调用 run()"
            )
        tools_ctx = tool_context or ToolContext(
            caller="agent_runtime",
            metadata={EXECUTION_CONTEXT_KEY: ctx},
        )

        ctx.add_event(Event.create(ctx.execution_id, "user", _normalize_prompt(prompt)))
        return await self._loop(ctx, tools_ctx)

    async def resume(
        self,
        context: ExecutionContext,
        approvals: Sequence[ApprovalDecision],
        *,
        tool_context: ToolContext | None = None,
    ) -> AgentResult:
        """恢复一个因等待人工批准而暂停的运行。

        待确认的工具调用在这里落地：批准的照常执行（可以用修改过的参数），
        没批准的回填一条「用户拒绝」的结果——**没有决定的按拒绝处理**
        （fail closed）。

        Args:
            context: 被暂停的运行上下文。
            approvals: 人工决定。``tool_call_id`` 必须与待确认的调用对应。
            tool_context: 工具执行上下文。为 None 时构造一个只带调用方标识
                的上下文。

        Returns:
            运行结果。恢复后的运行从暂停的那一步继续。

        Raises:
            AgentError: 这个运行没有被暂停。
        """
        pending = tuple(context.pending_approvals)
        if not pending:
            raise AgentError("这个运行没有被暂停，无需恢复")

        tools_ctx = tool_context or ToolContext(
            caller="agent_runtime",
            metadata={EXECUTION_CONTEXT_KEY: context},
        )

        outcomes = await self._resolve_approvals(context, pending, approvals, tools_ctx)
        self._observe(context, tuple(item.tool_call for item in pending), outcomes)
        context.pending_approvals.clear()
        return await self._loop(context, tools_ctx)

    async def _loop(self, ctx: ExecutionContext, tools_ctx: ToolContext) -> AgentResult:
        """执行 ReAct 循环。

        从 ``ctx.current_step`` 开始——因此恢复一个暂停的运行时，会从暂停的
        那一步继续，而不是从头再来。

        Args:
            ctx: 执行上下文。
            tools_ctx: 工具执行上下文。

        Returns:
            运行结果。
        """
        config = self.config
        steps: list[AgentStep] = []
        deadline = time.monotonic() + config.timeout if config.timeout is not None else None

        for index in range(ctx.current_step, config.max_steps):
            # 预算在步之间检查：达到上限就不再开始新的一步。当前步内的调用
            # 全部执行完，保证每条 tool_call 都有对应的 tool_result。
            budget_stop = self._check_budgets(ctx, ctx.tool_call_count)
            if budget_stop is not None:
                return self._result(ctx, steps, budget_stop)

            # --- Thought：模型决定下一步要做什么 ---
            started = time.monotonic()
            try:
                response = await self._think(ctx, deadline, tools_ctx)
            except TimeoutError:
                return self._result(ctx, steps, "timeout")
            except LLMError as exc:
                # 模型调用失败与超时是同一类运行时失败：都要返回已完成的部分
                # 结果，而不是丢弃整个运行。调用方据此决定是否重跑。
                return self._result(ctx, steps, "llm_error", error=f"{type(exc).__name__}: {exc}")
            think_ms = (time.monotonic() - started) * 1000

            ctx.record_usage(response.usage, response.model or config.model, config.prices)
            ctx.add_event(Event.create(ctx.execution_id, self.name, response.contents))

            tool_calls = tuple(item for item in response.contents if isinstance(item, ToolCall))

            if not tool_calls:
                # 模型不再请求工具——循环自然结束，这是唯一的成功路径。
                steps.append(AgentStep(index=index, response=response, think_ms=think_ms))
                ctx.increment_step()
                return self._result(ctx, steps, "completed")

            # --- 暂停检查：需要人工确认的工具会让整个运行停下来 ---
            pending = self._pending_approvals(tool_calls)
            if pending:
                # 本步的工具调用**一个都不执行**。要么全执行，要么全暂停——
                # 部分执行会让转录里出现「有调用没结果」的断裂，厂商 API 会
                # 拒绝整个请求。
                ctx.pending_approvals.extend(pending)
                return self._result(ctx, steps, "pending_approval", pending=tuple(pending))

            # --- Action：执行模型请求的工具 ---
            outcomes = await self._act(tool_calls, tools_ctx)

            # --- Observation：把工具结果回填给模型 ---
            self._observe(ctx, tool_calls, outcomes)

            structured = self._structured_output(tool_calls, outcomes)
            if structured is not None:
                # 模型提交了最终答案——运行就此结束
                steps.append(
                    AgentStep(
                        index=index,
                        response=response,
                        tool_calls=tool_calls,
                        outcomes=tuple(outcomes),
                        think_ms=think_ms,
                    )
                )
                ctx.increment_step()
                return self._result(ctx, steps, "completed", structured=structured)

            for outcome in outcomes:
                if outcome.succeeded:
                    ctx.consecutive_errors = 0
                else:
                    ctx.consecutive_errors += 1

            for call in tool_calls:
                ctx.tool_call_count += 1
                key = _call_key(call)
                ctx.call_counts[key] = ctx.call_counts.get(key, 0) + 1

            step = AgentStep(
                index=index,
                response=response,
                tool_calls=tool_calls,
                outcomes=tuple(outcomes),
                think_ms=think_ms,
            )
            steps.append(step)
            ctx.increment_step()

            stop = self._check_stop_conditions(ctx)
            if stop is not None:
                return self._result(ctx, steps, stop)

        return self._result(ctx, steps, "max_steps")

    async def _think(
        self,
        ctx: ExecutionContext,
        deadline: float | None,
        tool_context: ToolContext,
    ) -> LLMResponse:
        """执行一次模型调用（Thought）。

        Args:
            ctx: 执行上下文。
            deadline: 整体截止时刻（``time.monotonic`` 基准）。None 表示不限。
            tool_context: 工具执行上下文。用于确定「哪些工具对本次调用可见」。

        Returns:
            模型响应。

        Raises:
            TimeoutError: 超过整体截止时间。
        """
        config = self.config
        # 只把当前策略允许的工具给模型：模型看不到无权调用的工具，
        # 不会浪费轮次去尝试。强制仍在 Dispatcher 那一侧。
        specs = self.dispatcher.allowed_specs(caller=tool_context.caller)

        # 存储与呈现分离：完整历史留在 ctx 里，发给模型的是按预算裁剪的视图。
        contents = ctx.contents
        builder = config.context_builder
        if builder is not None:
            view = builder.build(contents, tools=specs, model=config.model)
            contents = list(view.contents)
            ctx.last_context_view = view

        request = LLMRequest(
            model=config.model,
            instructions=list(config.instructions),
            contents=contents,
            tools=specs,
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

    def _structured_output(
        self,
        tool_calls: tuple[ToolCall, ...],
        outcomes: list[ToolOutcome],
    ) -> BaseModel | None:
        """取出结构化输出。

        模型通过调用 ``final_answer`` 提交最终答案。执行成功时，其结果里带着
        已校验的数据；这里把它还原成目标模型的实例。

        Args:
            tool_calls: 模型请求的工具调用。
            outcomes: 与之对应的执行结果。

        Returns:
            目标模型实例；本次没有提交最终答案时返回 None。
        """
        output_model = self.config.output_model
        if output_model is None:
            return None
        for call, outcome in zip(tool_calls, outcomes, strict=True):
            if call.name != FINAL_ANSWER_TOOL_NAME or not outcome.succeeded:
                continue
            # final_answer 总是返回已校验的模型实例，其 dump 必为 dict
            assert outcome.data is not None
            return output_model.model_validate(outcome.data)
        return None

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

    def _check_stop_conditions(self, ctx: ExecutionContext) -> StopReason | None:
        """判断是否应当停止。

        只检查「模型不再前进」的两类信号：连续失败与原地打转。步数、工具
        调用预算、token 与成本预算在步之间检查。

        计数住在上下文里而不是循环的局部变量里——否则「暂停-恢复」可以
        重置它们，从而绕过停止条件。

        Args:
            ctx: 执行上下文。

        Returns:
            停止原因；应继续时返回 None。
        """
        config = self.config
        consecutive_errors = ctx.consecutive_errors
        call_counts = ctx.call_counts

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
        structured: BaseModel | None = None,
        pending: tuple[PendingApproval, ...] = (),
    ) -> AgentResult:
        """打包运行结果。

        Args:
            ctx: 执行上下文。
            steps: 逐步记录。
            stop_reason: 停止原因。
            error: 失败原因。
            structured: 结构化输出。
            pending: 等待人工确认的工具调用。

        Returns:
            运行结果。
        """
        ctx.final_result = structured if structured is not None else _last_assistant_text(ctx)
        if stop_reason == "completed":
            status: AgentStatus = "complete"
        elif stop_reason == "pending_approval":
            status = "pending_approval"
        else:
            status = "error"
        return AgentResult(
            context=ctx,
            output=ctx.final_result,
            status=status,
            stop_reason=stop_reason,
            steps=tuple(steps),
            error=error,
            pending_approvals=pending,
        )

    def _pending_approvals(self, tool_calls: tuple[ToolCall, ...]) -> list[PendingApproval]:
        """找出需要人工确认的工具调用。

        Args:
            tool_calls: 模型请求的工具调用。

        Returns:
            待确认的调用；没有时返回空列表。
        """
        pending: list[PendingApproval] = []
        for call in tool_calls:
            tool = (
                self.dispatcher.registry.get(call.name)
                if self.dispatcher.registry.has(call.name)
                else None
            )
            if tool is None:
                continue
            definition = tool.definition
            if not definition.requires_confirmation:
                continue
            pending.append(
                PendingApproval(
                    tool_call=call,
                    confirmation_message=definition.render_confirmation(call.arguments),
                )
            )
        return pending

    async def _resolve_approvals(
        self,
        ctx: ExecutionContext,
        pending: tuple[PendingApproval, ...],
        approvals: Sequence[ApprovalDecision],
        tools_ctx: ToolContext,
    ) -> list[ToolOutcome]:
        """落实人工对待确认工具调用的决定。

        **没有决定的按拒绝处理**（fail closed）：缺少批准就执行，等于让
        「批准」变成默认开启——那就不是批准机制了。拒绝的理由会回填给模型，
        让它知道为什么做不成、改用别的方式。

        每次待确认的调用都计入工具调用预算——**包括被拒绝的**。否则「反复
        请求-反复被拒」可以不消耗任何预算地无限循环。

        Args:
            ctx: 执行上下文。计数写在这里。
            pending: 待确认的调用。
            approvals: 人工决定。
            tools_ctx: 工具执行上下文。

        Returns:
            与 ``pending`` 一一对应的执行结果。
        """
        decisions = {decision.tool_call_id: decision for decision in approvals}
        outcomes: list[ToolOutcome] = []
        for item in pending:
            call = item.tool_call
            ctx.tool_call_count += 1
            key = _call_key(call)
            ctx.call_counts[key] = ctx.call_counts.get(key, 0) + 1

            decision = decisions.get(call.tool_call_id)
            if decision is None or not decision.approved:
                reason = decision.reason if decision is not None else "没有收到该调用的批准"
                outcomes.append(
                    ToolOutcome(
                        tool_name=call.name,
                        status="error",
                        content=(
                            f"用户拒绝执行该工具（{reason}）。"
                            "不要再尝试这个操作，请改用其它方式回答。"
                        ),
                        error=f"approval_denied: {reason}",
                    )
                )
                continue
            arguments = decision.modified_arguments or call.arguments
            outcomes.append(await self.dispatcher.dispatch_safe(call.name, arguments, tools_ctx))
        return outcomes


def _compose_dispatcher(
    dispatcher: ToolDispatcher,
    output_model: type[BaseModel] | None,
) -> ToolDispatcher:
    """必要时给调度器补上 ``final_answer`` 工具。

    组合出一个**新的**注册表与调度器，而不是往调用方的注册表里塞工具——调用方
    的注册表可能被多处共享，就地修改会产生副作用。调用方的默认超时与权限策略
    都会被保留：策略丢失会让组合后的调度器**默认放行**，等于给被限制的工具
    开了一个后门。

    Args:
        dispatcher: 调用方提供的调度器。
        output_model: 结构化输出的目标模型。None 表示不需要补。

    Returns:
        实际使用的调度器。
    """
    if output_model is None:
        return dispatcher
    registry = ToolRegistry()
    for tool in dispatcher.registry.tools():
        registry.register(tool)
    registry.register(build_final_answer_tool(output_model))
    return ToolDispatcher(
        registry,
        default_timeout=dispatcher.default_timeout,
        policy=dispatcher.policy,
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
