"""可观测性：把一次运行投影成 Trace，再把多次运行聚合成 Metrics。

**Trace 是投影，不是第二套事件系统。** 运行已经把事实记在
``ExecutionContext.events`` 与 ``AgentStep`` 里；再开一条事件总线，等于把同一
个事实记两遍，两份迟早对不上。``build_trace(result)`` 只读结果、不写任何东西
——因此它可以在事后运行、可以离线重跑、不需要在运行时里插桩。

唯一的例外是模型调用耗时：那个数字无法从已有事实推导，所以运行时把它记在
``AgentStep.think_ms`` 里。工具耗时则在 ``ToolOutcome.duration_ms`` 里。**一次
运行的时间去向只有这两个来源**，Trace 只做汇总，不重复计时。

**Metrics 是聚合，不是实时指标。** 没有 exporter、没有后台线程、没有滑动窗口；
``compute_metrics(traces)`` 对一个有限集合做统计。这足够回答 Phase 0 的问题
（成功率、失败原因分布、耗时与成本），也不引入必须运维的组件。接
OpenTelemetry 时，这里仍然是事实来源，只是多一个导出器——顺序不能反过来：
**先有可解释的数据，再谈导出**。

**诊断不是猜测。** ``diagnose(trace)`` 只根据 Trace 里已有的事实给出线索：
哪个工具失败了、哪个动作被重复、预算是哪一项用尽的。它不给「模型应该怎么想」
这种没有证据的建议。
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from agent_learning_coach.core.agent.types import AgentResult, AgentStatus, AgentStep
from agent_learning_coach.core.llm.models import LLMUsage
from agent_learning_coach.core.llm.types import Message

_MAX_EVIDENCE = 3
"""诊断里最多列几条证据。"""


@dataclass(frozen=True)
class ToolSpan:
    """一次工具调用在 Trace 里的样子。"""

    name: str
    """工具名。"""

    status: str
    """执行状态：``"success"`` 或 ``"error"``。"""

    duration_ms: float
    """执行耗时（毫秒），来自 ``ToolOutcome.duration_ms``。"""

    error: str | None = None
    """错误描述；成功时为 None。"""

    @property
    def failed(self) -> bool:
        """这次调用是否失败。"""
        return self.status != "success"


@dataclass(frozen=True)
class StepTrace:
    """一步在 Trace 里的样子。"""

    index: int
    """步序号，从 0 开始。"""

    think_ms: float
    """模型调用耗时（毫秒）。"""

    input_tokens: int
    """本步输入 token。"""

    output_tokens: int
    """本步输出 token。"""

    tools: tuple[ToolSpan, ...] = ()
    """本步执行的工具调用。"""

    text: str | None = None
    """本步模型给出的文本（没有时为空）。"""

    @property
    def duration_ms(self) -> float:
        """本步耗时：模型 + 工具。

        Returns:
            毫秒。
        """
        return self.think_ms + sum(span.duration_ms for span in self.tools)


@dataclass(frozen=True)
class RunTrace:
    """一次运行的结构化轨迹。"""

    execution_id: str
    """执行标识，与 ``ExecutionContext.execution_id`` 相同——用于把 Trace 和
    日志、会话快照对上。"""

    status: AgentStatus
    """运行状态。"""

    stop_reason: str
    """停止原因。"""

    error: str | None
    """失败原因（模型调用失败时有值）。"""

    steps: tuple[StepTrace, ...]
    """逐步轨迹。"""

    started_at: float
    """开始时刻（``time.time()`` 基准），取第一个事件的时间戳。"""

    finished_at: float
    """结束时刻，取最后一个事件的时间戳。

    **是近似值**：因预算耗尽而停止的运行不产生「结束」事件，此时它等于最后
    一次可观察到的动作时刻。
    """

    usage: LLMUsage
    """整次运行的 token 用量。"""

    cost: float
    """整次运行的成本。"""

    @property
    def duration_ms(self) -> float:
        """端到端耗时（毫秒），含等待人工批准的暂停时间。"""
        return (self.finished_at - self.started_at) * 1000

    @property
    def tool_spans(self) -> tuple[ToolSpan, ...]:
        """全部工具调用，按发生顺序。"""
        return tuple(span for step in self.steps for span in step.tools)

    @property
    def tool_calls(self) -> int:
        """工具调用总数。"""
        return len(self.tool_spans)

    @property
    def failed_tool_calls(self) -> int:
        """失败的工具调用数。"""
        return sum(1 for span in self.tool_spans if span.failed)

    @property
    def succeeded(self) -> bool:
        """是否正常结束。

        与 ``AgentResult.status`` 同义：只有 ``"complete"`` 算成功，
        等待人工批准不算——它还没结束。
        """
        return self.status == "complete"


def build_trace(result: AgentResult) -> RunTrace:
    """把一次运行的结果投影成 Trace。

    纯函数：不修改 ``result``，不访问运行时，因此可以在运行结束后任意次调用。

    Args:
        result: 运行结果。

    Returns:
        结构化轨迹。
    """
    ctx = result.context
    steps = tuple(_trace_step(step) for step in result.steps)
    if ctx.events:
        started_at = ctx.events[0].timestamp
        finished_at = ctx.events[-1].timestamp
    else:  # pragma: no cover - run() 至少写入一条用户事件
        started_at = finished_at = 0.0
    return RunTrace(
        execution_id=ctx.execution_id,
        status=result.status,
        stop_reason=result.stop_reason,
        error=result.error,
        steps=steps,
        started_at=started_at,
        finished_at=finished_at,
        usage=ctx.usage,
        cost=ctx.cost,
    )


def _trace_step(step: AgentStep) -> StepTrace:
    """把一步的执行记录投影成 StepTrace。

    Args:
        step: 执行记录。

    Returns:
        逐步轨迹。
    """
    texts = [
        item.content
        for item in step.response.contents
        if isinstance(item, Message) and item.content
    ]
    return StepTrace(
        index=step.index,
        think_ms=step.think_ms,
        input_tokens=step.response.usage.input_tokens,
        output_tokens=step.response.usage.output_tokens,
        tools=tuple(
            ToolSpan(
                name=outcome.tool_name,
                status=outcome.status,
                duration_ms=outcome.duration_ms,
                error=outcome.error,
            )
            for outcome in step.outcomes
        ),
        text="\n".join(texts) or None,
    )


@dataclass(frozen=True)
class RunMetrics:
    """一组运行的聚合指标。

    **样本量小的时候百分位只是参考**：10 次以下，p95 基本等于最大值。因此
    指标用来比较（改动前后）、用来发现异常，不用来宣称「性能达标」。
    """

    runs: int
    """运行次数。"""

    successes: int
    """正常结束（``status="complete"``）的次数。"""

    stop_reasons: Mapping[str, int] = field(default_factory=dict)
    """停止原因分布。键是停止原因，值是次数。"""

    tool_calls: int = 0
    """工具调用总数。"""

    failed_tool_calls: int = 0
    """失败的工具调用数。"""

    step_counts: tuple[int, ...] = ()
    """每次运行的步数。"""

    think_ms_samples: tuple[float, ...] = ()
    """每一步的模型调用耗时（毫秒）。"""

    run_duration_ms_samples: tuple[float, ...] = ()
    """每次运行的端到端耗时（毫秒）。"""

    usage: LLMUsage = field(default_factory=LLMUsage)
    """全部运行的 token 用量。"""

    cost: float = 0.0
    """全部运行的成本。"""

    @property
    def success_rate(self) -> float:
        """成功率。没有运行时返回 0。"""
        return self.successes / self.runs if self.runs else 0.0

    @property
    def tool_failure_rate(self) -> float:
        """工具调用失败率。没有工具调用时返回 0。"""
        return self.failed_tool_calls / self.tool_calls if self.tool_calls else 0.0

    @property
    def steps_p50(self) -> float:
        """步数的中位数。"""
        return _percentile(self.step_counts, 0.5)

    @property
    def steps_p95(self) -> float:
        """步数的 p95。"""
        return _percentile(self.step_counts, 0.95)

    @property
    def think_ms_p50(self) -> float:
        """模型调用耗时的中位数（毫秒）。"""
        return _percentile(self.think_ms_samples, 0.5)

    @property
    def think_ms_p95(self) -> float:
        """模型调用耗时的 p95（毫秒）。"""
        return _percentile(self.think_ms_samples, 0.95)

    @property
    def duration_ms_p50(self) -> float:
        """端到端耗时的中位数（毫秒）。"""
        return _percentile(self.run_duration_ms_samples, 0.5)

    @property
    def duration_ms_p95(self) -> float:
        """端到端耗时的 p95（毫秒）。"""
        return _percentile(self.run_duration_ms_samples, 0.95)

    def summary(self) -> str:
        """多行摘要。

        Returns:
            形如 ``3 次运行：成功 2（67%）；停止原因 completed 2、max_steps 1``。
            没有运行时返回 ``0 次运行``。
        """
        if not self.runs:
            return "0 次运行"
        ordered = sorted(self.stop_reasons.items(), key=lambda item: (-item[1], item[0]))
        reasons = "、".join(f"{reason} {count}" for reason, count in ordered)
        return (
            f"{self.runs} 次运行：成功 {self.successes}（{self.success_rate:.0%}）；"
            f"停止原因 {reasons}；"
            f"步数 p50 {self.steps_p50:.1f} / p95 {self.steps_p95:.1f}；"
            f"模型耗时 p50 {self.think_ms_p50:.1f}ms / p95 {self.think_ms_p95:.1f}ms；"
            f"工具失败 {self.failed_tool_calls}/{self.tool_calls}；"
            f"token {self.usage.input_tokens + self.usage.output_tokens}；"
            f"成本 {self.cost:.4f}"
        )


def compute_metrics(traces: Iterable[RunTrace]) -> RunMetrics:
    """把一组 Trace 聚合成指标。

    Args:
        traces: 运行轨迹。

    Returns:
        聚合指标。
    """
    items = list(traces)
    reasons: dict[str, int] = {}
    for trace in items:
        reasons[trace.stop_reason] = reasons.get(trace.stop_reason, 0) + 1
    return RunMetrics(
        runs=len(items),
        successes=sum(1 for trace in items if trace.succeeded),
        stop_reasons=reasons,
        tool_calls=sum(trace.tool_calls for trace in items),
        failed_tool_calls=sum(trace.failed_tool_calls for trace in items),
        step_counts=tuple(len(trace.steps) for trace in items),
        think_ms_samples=tuple(step.think_ms for trace in items for step in trace.steps),
        run_duration_ms_samples=tuple(trace.duration_ms for trace in items),
        usage=LLMUsage(
            input_tokens=sum(trace.usage.input_tokens for trace in items),
            output_tokens=sum(trace.usage.output_tokens for trace in items),
        ),
        cost=sum(trace.cost for trace in items),
    )


def _percentile(values: Sequence[float], q: float) -> float:
    """最近秩百分位。

    Args:
        values: 样本。
        q: 分位数，取值 ``[0, 1]``。

    Returns:
        百分位值；没有样本时返回 0。
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    if q <= 0:
        return ordered[0]
    if q >= 1:
        return ordered[-1]
    index = math.ceil(q * len(ordered)) - 1
    return ordered[max(0, min(index, len(ordered) - 1))]


_STOP_HINTS: dict[str, str] = {
    "max_steps": (
        "步数用尽：模型一直在请求工具，没有给出结论。"
        "检查任务是否被拆得太碎，或工具是否没有返回模型需要的信息。"
    ),
    "max_tool_calls": ("工具调用预算用尽：模型在预算内没有收敛。先看工具结果是否让它反复重试。"),
    "max_tokens": "token 预算用尽：上下文或输出过大。先看是不是把整份材料塞进了 prompt。",
    "max_cost": "成本预算用尽：先看 token 用量集中在哪些步。",
    "timeout": "整体超时：先看是模型慢还是某个工具慢（对照 think_ms 与工具耗时）。",
    "llm_error": (
        "模型调用失败。这类失败不重试——由调用方决定是否重跑；"
        "先确认是配置（模型名、凭证）还是服务端问题。"
    ),
}

_REPEAT_HINT = (
    "模型在原地打转：同一个动作被反复请求。"
    "检查工具结果是否真的推进了任务，或系统提示是否缺少「已经做过什么」的信息。"
)
_ERRORS_HINT = (
    "连续工具失败：模型没有从错误里恢复。"
    "先看失败的工具与错误信息，判断是参数问题、环境问题还是工具本身坏了。"
)
_APPROVAL_HINT = "运行暂停，等待人工批准。这不是失败——用 resume() 提交决定后继续。"


def diagnose(trace: RunTrace) -> str | None:
    """根据 Trace 给出失败诊断。

    只陈述 Trace 里存在的事实：停在哪、哪个工具失败、哪个动作被重复。成功
    与「等待人工批准」返回 None——它们不是失败。

    Args:
        trace: 运行轨迹。

    Returns:
        诊断文本；不需要诊断时返回 None。
    """
    if trace.stop_reason == "completed":
        return None
    if trace.stop_reason == "pending_approval":
        return _APPROVAL_HINT

    lines = [f"停止原因：{trace.stop_reason}"]
    if trace.stop_reason == "repeated_call":
        lines.append(_REPEAT_HINT)
    elif trace.stop_reason == "consecutive_errors":
        lines.append(_ERRORS_HINT)
    else:
        lines.append(_STOP_HINTS.get(trace.stop_reason, _STOP_HINTS.get("max_steps", "")))
    if trace.error:
        lines.append(f"错误：{trace.error}")

    evidence = _evidence(trace)
    if evidence:
        lines.append("证据：")
        lines.extend(f"- {item}" for item in evidence)
    return "\n".join(lines)


def _evidence(trace: RunTrace) -> list[str]:
    """收集诊断证据。

    Args:
        trace: 运行轨迹。

    Returns:
        证据条目，最多 ``_MAX_EVIDENCE`` 条。
    """
    items: list[str] = [
        f"{span.name} 失败：{span.error or span.status}" for span in trace.tool_spans if span.failed
    ][-_MAX_EVIDENCE:]
    counts: dict[str, int] = {}
    for span in trace.tool_spans:
        counts[span.name] = counts.get(span.name, 0) + 1
    if counts and trace.stop_reason == "repeated_call":
        name, count = max(counts.items(), key=lambda item: item[1])
        items.append(f"{name} 被调用 {count} 次")
    return items[:_MAX_EVIDENCE]


__all__ = [
    "RunMetrics",
    "RunTrace",
    "StepTrace",
    "ToolSpan",
    "build_trace",
    "compute_metrics",
    "diagnose",
]
