"""Agent 运行时的核心类型。

六个类型对应一次运行的六个方面：

- **Event**：执行过程中发生的一件事（谁说的、说了什么、什么时候）。
- **Execution Context**：一次执行的全部运行时状态——事件历史、步数、草稿本、
  用量与成本。
- **Agent Config**：运行参数——预算（步数、token、成本）与停止阈值。
- **Agent Step**：一步的执行记录——模型输出、工具调用、工具结果。
- **Agent Result**：一次运行的最终结果，携带 Execution Context。
- **Stop Reason**：为什么停。

分清它们的意义：**Context 是状态，Result 是结论**。Context 承载「发生了什么」
——它让运行可测试、可复现、可续接；Result 承载「所以呢」——调用方拿到它就
知道输出是什么、为什么停、花了多少。混在一起会让「续接一次对话」与「判断
这次成功没有」都变得别扭。
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

from agent_learning_coach.core.agent.cost import TokenPrice
from agent_learning_coach.core.llm.models import LLMResponse, LLMUsage
from agent_learning_coach.core.llm.types import ContentItem, Message, ToolCall
from agent_learning_coach.core.tools.types import ToolOutcome

StopReason = Literal[
    "completed",
    "max_steps",
    "max_tool_calls",
    "max_tokens",
    "max_cost",
    "consecutive_errors",
    "repeated_call",
    "timeout",
    "llm_error",
]
"""停止原因。

- ``completed``：模型不再请求工具，循环自然结束——这是**唯一**的成功状态。
- ``max_steps``：达到步数上限。
- ``max_tool_calls``：达到工具调用预算。
- ``max_tokens``：达到累计 token 预算。
- ``max_cost``：达到成本预算。
- ``consecutive_errors``：连续工具失败次数超限，模型陷入失败循环。
- ``repeated_call``：同一工具以相同参数被反复调用，模型陷入原地打转。
- ``timeout``：整体截止时间已到。
- ``llm_error``：模型调用失败（``LLMError``）。

多个条件同时命中时，**更具体的原因优先**：``repeated_call`` 与
``consecutive_errors`` 指向模型行为，``max_steps`` 只是预算耗尽。因此模型
原地打转时上报 ``repeated_call`` 而不是 ``max_steps``。
"""

AgentStatus = Literal["complete", "error"]
"""运行状态。

- ``complete``：正常结束（``stop_reason="completed"``）。
- ``error``：非正常结束——预算耗尽、超时、模型失败等。
"""

EXECUTION_CONTEXT_KEY = "execution_context"
"""``ToolContext.metadata`` 中承载 ``ExecutionContext`` 的键。

工具通过它读写运行时草稿本::

    ctx = tool_context.metadata.get(EXECUTION_CONTEXT_KEY)
    if ctx is not None:
        ctx.state["last_query"] = query

之所以用字符串键而不是在 ``ToolContext`` 上加字段：``core/tools`` 不应该知道
``core/agent`` 的存在——那会让工具系统反向依赖 Agent 运行时。约定放在使用
方（``core/agent``）更符合依赖方向。
"""


@dataclass(frozen=True)
class Event:
    """一次执行过程中发生的一件事。

    事件是**按发生顺序**追加的，构成执行的完整历史。相比「一条扁平的对话
    列表」，事件额外携带了**是谁**说的与**什么时候**说的——这两点让历史可以
    被审计、被回放、被定位问题。
    """

    id: str
    """事件标识。"""

    execution_id: str
    """所属执行的标识。"""

    timestamp: float
    """发生时刻（``time.time()`` 基准）。"""

    author: str
    """发起者。``"user"``、``"model"``、``"tools"`` 或调用方自定义的名字。"""

    contents: tuple[ContentItem, ...] = ()
    """事件携带的内容。"""

    @classmethod
    def create(
        cls,
        execution_id: str,
        author: str,
        contents: Sequence[ContentItem] = (),
        *,
        timestamp: float | None = None,
    ) -> Event:
        """创建一个事件。

        Args:
            execution_id: 所属执行的标识。
            author: 发起者。
            contents: 事件携带的内容。
            timestamp: 发生时刻。为 None 时取当前时间。

        Returns:
            事件。
        """
        return cls(
            id=str(uuid.uuid4()),
            execution_id=execution_id,
            timestamp=time.time() if timestamp is None else timestamp,
            author=author,
            contents=tuple(contents),
        )


@dataclass
class ExecutionContext:
    """一次执行的运行时状态容器。

    它承载让 Agent **可测试、可复现**所需的全部状态：

    - **执行身份**（``execution_id``）：串联事件、日志与外部系统。
    - **执行历史**（``events``）：按发生顺序排列的事件。
    - **步数**（``current_step``）：循环走到第几步。
    - **草稿本**（``state``）：Agent 自己写入的中间状态——模型看不到它，
      但工具与应用层可以读。
    - **用量与成本**（``usage`` / ``cost``）：预算执行与事后核算的依据。
    - **最终产物**（``final_result``）：运行的输出。

    上下文是**可变**的：运行过程中不断被写入。它与 ``AgentResult`` 的分工是
    「过程 vs 结论」——结果携带上下文，因此上下文不会被丢掉。
    """

    execution_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    """执行标识。默认随机生成；需要跨系统串联时可显式指定。"""

    events: list[Event] = field(default_factory=list)
    """执行历史，按发生顺序排列。"""

    current_step: int = 0
    """已完成的步数。"""

    state: dict[str, Any] = field(default_factory=dict)
    """运行时草稿本。

    Agent 运行时写入的中间状态。**模型看不到它**——它服务于工具与应用层，
    例如「本轮已经问过用户哪些问题」「学习计划草稿」。
    """

    usage: LLMUsage = field(default_factory=LLMUsage)
    """累计 token 用量。"""

    cost: float = 0.0
    """累计成本。按价格表核算。"""

    final_result: str | BaseModel | None = None
    """最终产物。文本回答，或结构化输出的已校验实例。"""

    def add_event(self, event: Event) -> None:
        """追加一个事件。

        Args:
            event: 事件。
        """
        self.events.append(event)

    def increment_step(self) -> None:
        """步数加一。"""
        self.current_step += 1

    def record_usage(
        self,
        usage: LLMUsage,
        model: str | None,
        prices: Mapping[str, TokenPrice],
    ) -> None:
        """累计一次模型调用的用量与成本。

        Args:
            usage: 本次调用的用量。
            model: 模型名。用于查价格表。
            prices: 模型名到单价的映射。

        Raises:
            AgentConfigError: 设定了成本上限但价格表没有覆盖该模型——预算
                无法被执行，属于配置错误。
        """
        self.usage = LLMUsage(
            input_tokens=self.usage.input_tokens + usage.input_tokens,
            output_tokens=self.usage.output_tokens + usage.output_tokens,
        )
        if not prices:
            return
        from agent_learning_coach.core.agent.errors import AgentConfigError

        if model is None:
            raise AgentConfigError(
                "无法确定使用的模型（响应与配置都未提供模型名），无法核算成本；"
                "请指定模型名或移除成本上限"
            )
        price = prices.get(model)
        if price is None:
            raise AgentConfigError(
                f"价格表没有覆盖模型 {model!r}，无法核算成本；"
                "请补充该模型的 TokenPrice 或移除成本上限"
            )
        self.cost += price.cost_of(usage)

    @property
    def contents(self) -> list[ContentItem]:
        """展平后的对话内容。

        按事件顺序拼接所有事件携带的内容。保证不变量：**每条 ``ToolCall``
        都有对应的 ``ToolResult``**——因此这份记录可以直接作为下一轮对话的
        输入继续。
        """
        flattened: list[ContentItem] = []
        for event in self.events:
            flattened.extend(event.contents)
        return flattened


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

    max_total_tokens: int | None = None
    """累计 token 预算（输入 + 输出）。None 表示不限制。

    与 ``max_steps`` 相比，它约束的是**实际消耗**而非步数——同样 12 步，
    简短的回答可能只花几百 token，逐字输出长文档则可能花掉几万。预算在
    **步之间**检查，与其它预算一致。
    """

    max_cost: float | None = None
    """成本预算。None 表示不限制。

    需要 ``prices`` 覆盖实际使用的模型，否则预算无法被执行——那属于配置
    错误，运行时直接抛出 ``AgentConfigError``。
    """

    prices: Mapping[str, TokenPrice] = field(default_factory=dict)
    """模型名到 token 单价的映射。

    **由调用方提供**：价格会变、随厂商与地区不同，运行时内置一份只会制造
    一个必然过期的真相来源。
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

    output_model: type[BaseModel] | None = None
    """结构化输出的目标模型。None 表示最终答案是纯文本。

    设置后运行时会补一个 ``final_answer`` 工具，其参数 schema 就是目标模型
    的 JSON Schema——模型调用它即表示给出最终答案（主教材 4.7 的做法）。
    """


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
class AgentResult:
    """一次 Agent 运行的最终结果。

    携带 ``ExecutionContext``——因此「结论」与「过程」不会分离：调用方既能
    拿到输出，也能拿到完整历史、步数与用量。
    """

    context: ExecutionContext
    """本次运行的执行上下文。"""

    output: str | BaseModel | None = None
    """最终产物。

    未设置 ``output_model`` 时是模型给出的回答文本；设置了则是目标模型的
    **已校验实例**。
    """

    status: AgentStatus = "complete"
    """运行状态。只有 ``"complete"`` 表示正常结束。"""

    stop_reason: StopReason = "completed"
    """停止原因。只有 ``"completed"`` 表示正常结束。"""

    steps: tuple[AgentStep, ...] = ()
    """逐步的执行记录。"""

    error: str | None = None
    """失败原因。非 ``None`` 时说明运行是失败结束的。

    只有 ``stop_reason="llm_error"`` 会填这个字段——其它停止原因要么是预算
    耗尽（不是错误），要么是模型主动结束。
    """

    @property
    def succeeded(self) -> bool:
        """是否正常结束。"""
        return self.stop_reason == "completed"

    @property
    def usage(self) -> LLMUsage:
        """累计 token 用量。"""
        return self.context.usage

    @property
    def cost(self) -> float:
        """累计成本。"""
        return self.context.cost

    @property
    def tool_call_count(self) -> int:
        """本次运行累计执行的工具调用数。"""
        return sum(step.executed_tool_calls for step in self.steps)

    @property
    def final_text(self) -> str:
        """最后一条 assistant 文本。

        模型在结束前通常会给一段说明性文字。没有文本时返回空串。
        """
        for item in reversed(self.context.contents):
            if isinstance(item, Message) and item.role == "assistant":
                return item.content
        return ""


__all__ = [
    "AgentConfig",
    "AgentResult",
    "AgentStatus",
    "AgentStep",
    "Event",
    "ExecutionContext",
    "StopReason",
]
