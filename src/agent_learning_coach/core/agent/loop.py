"""Agent Loop：ReAct 执行循环。

一次循环的形状是::

    Think  ->  Act  ->  Observe  ->  Think -> ...
    (模型)     (工具)     (结果回填)      (模型)

**Think**：把当前对话与工具清单交给模型，模型要么给出回答，要么请求调用工具。

**Act**：执行模型请求的工具调用。执行走 ``ToolDispatcher``，因此参数校验、
超时与结果归一化与本地工具完全一致。

**Observe**：把工具结果回填给模型，让它看到事实后继续推理。

## 停止策略

Topic 05 提出的问题是：**循环在什么条件下必须停止？Stop Condition 与 Max
Steps 如何配合，既避免死循环，也避免过早收敛？**

本实现的答案是分层的：

1. **自然结束是主路径**：模型不再请求工具就结束。只有模型自己知道任务是否
   完成，任何预算都不该替代这个判断。
2. **工具失败不终止循环**：失败被归一化成错误结果回填给模型，让它换参数、
   换工具或直接回答——这正是工具系统「调用级失败归一化」的意义。模型能
   自愈的错误就让它自愈。
3. **预算是兜底，不是主路径**：步数、工具调用数、连续失败数、重复调用数
   四个阈值只在模型**不再前进**时兜底。它们的存在是为了让失控的循环一定
   会停下来，而不是为了提前结束一个正在取得进展的任务。

「过早收敛」的防护因此来自两条规则：预算是兜底而非目标；工具错误回填而非
终止。

## 边界

- 模型调用失败（``LLMError``）**向上抛出**，不重试。响应可靠性属于模型
  客户端的职责，循环只管工具调用周期。
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

from agent_learning_coach.core.agent.types import (
    AgentConfig,
    AgentRun,
    AgentStep,
    StopReason,
)
from agent_learning_coach.core.llm.models import LLMRequest, LLMResponse, LLMUsage
from agent_learning_coach.core.llm.protocol import LLMClient
from agent_learning_coach.core.llm.types import ContentItem, Message, ToolCall
from agent_learning_coach.core.tools.dispatcher import ToolDispatcher
from agent_learning_coach.core.tools.types import ToolContext


class AgentLoop:
    """ReAct 执行循环。

    职责边界：只管「模型 ↔ 工具」的循环控制。模型调用委托给 ``LLMClient``，
    工具执行委托给 ``ToolDispatcher``，两者都不在本类里实现——因此换成别的
    模型或别的工具来源都不需要改循环。
    """

    def __init__(
        self,
        client: LLMClient,
        dispatcher: ToolDispatcher,
        *,
        config: AgentConfig | None = None,
    ) -> None:
        """初始化循环。

        Args:
            client: 模型客户端。
            dispatcher: 工具调度器。
            config: 运行参数。为 None 时用默认配置。
        """
        self.client = client
        self.dispatcher = dispatcher
        self.config = config or AgentConfig()

    async def run(
        self,
        prompt: str | list[ContentItem],
        *,
        context: ToolContext | None = None,
    ) -> AgentRun:
        """执行一次完整的 Agent 运行。

        Args:
            prompt: 用户输入。可以是一段文本，或一组厂商无关的内容项。
            context: 工具执行上下文。为 None 时构造一个只带调用方标识的上下文。

        Returns:
            运行结果。即使是超时或预算耗尽，也会返回已完成的部分结果——
            调用方能知道「走到了哪一步」以及「为什么停」。
        """
        config = self.config
        tool_context = context or ToolContext(caller="agent_loop")

        contents: list[ContentItem] = list(_normalize_prompt(prompt))
        steps: list[AgentStep] = []
        usage = LLMUsage()
        total_tool_calls = 0
        consecutive_errors = 0
        call_counts: Counter[str] = Counter()
        deadline = time.monotonic() + config.timeout if config.timeout is not None else None

        for index in range(config.max_steps):
            # 预算在步之间检查：达到上限就不再开始新的一步。当前步内的调用
            # 全部执行完，保证每条 tool_call 都有对应的 tool_result。
            if config.max_tool_calls is not None and total_tool_calls >= config.max_tool_calls:
                return _finish(steps, contents, usage, "max_tool_calls")

            try:
                response = await self._think(contents, deadline)
            except TimeoutError:
                return _finish(steps, contents, usage, "timeout")

            usage = _accumulate_usage(usage, response)
            contents.extend(response.contents)

            tool_calls = tuple(item for item in response.contents if isinstance(item, ToolCall))

            if not tool_calls:
                # 模型不再请求工具——循环自然结束，这是唯一的成功路径。
                steps.append(AgentStep(index=index, response=response))
                return _finish(steps, contents, usage, "completed")

            outcomes = []
            for call in tool_calls:
                total_tool_calls += 1
                outcome = await self.dispatcher.dispatch_safe(
                    call.name, call.arguments, tool_context
                )
                outcomes.append(outcome)
                contents.append(outcome.to_message_result(call.tool_call_id))

                if outcome.succeeded:
                    consecutive_errors = 0
                else:
                    consecutive_errors += 1

                call_counts[_call_key(call)] += 1

            step = AgentStep(
                index=index,
                response=response,
                tool_calls=tool_calls,
                outcomes=tuple(outcomes),
            )
            steps.append(step)

            stop = self._check_stop_conditions(
                consecutive_errors=consecutive_errors,
                call_counts=call_counts,
            )
            if stop is not None:
                return _finish(steps, contents, usage, stop)

        return _finish(steps, contents, usage, "max_steps")

    async def _think(self, contents: list[ContentItem], deadline: float | None) -> LLMResponse:
        """执行一次模型调用（Think）。

        Args:
            contents: 当前对话记录。
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
            contents=list(contents),
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

    def _check_stop_conditions(
        self,
        *,
        consecutive_errors: int,
        call_counts: Counter[str],
    ) -> StopReason | None:
        """判断是否应当停止。

        只检查「模型不再前进」的两类信号：连续失败与原地打转。步数与工具
        调用预算在步之间检查。

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


def _accumulate_usage(total: LLMUsage, response: LLMResponse) -> LLMUsage:
    """累加 token 用量。

    Args:
        total: 当前累计值。
        response: 本次响应。

    Returns:
        新的累计值。
    """
    return LLMUsage(
        input_tokens=total.input_tokens + response.usage.input_tokens,
        output_tokens=total.output_tokens + response.usage.output_tokens,
    )


def _finish(
    steps: list[AgentStep],
    contents: list[ContentItem],
    usage: LLMUsage,
    stop_reason: StopReason,
) -> AgentRun:
    """打包运行结果。

    Args:
        steps: 逐步记录。
        contents: 对话记录。
        usage: 累计用量。
        stop_reason: 停止原因。

    Returns:
        运行结果。
    """
    return AgentRun(
        steps=tuple(steps),
        stop_reason=stop_reason,
        contents=tuple(contents),
        usage=usage,
    )


__all__ = ["AgentLoop"]
