"""可观测性测试：Trace 投影、Metrics 聚合与失败诊断。

对应 Topic 14。三组关注点：

**Trace 是投影**——它只读运行结果，不另记一份事件；模型耗时来自
``AgentStep.think_ms``，工具耗时来自 ``ToolOutcome.duration_ms``。

**Metrics 是聚合**——成功率、停止原因分布、工具失败率、步数与耗时百分位。

**诊断只陈述事实**——哪个工具失败、哪个动作被重复；成功与「等待批准」不诊断。
"""

from __future__ import annotations

import asyncio

from agent_learning_coach.core.agent import (
    AgentConfig,
    AgentRuntime,
    build_eval_tools,
    build_trace,
    compute_metrics,
    diagnose,
)
from agent_learning_coach.core.llm.errors import LLMError
from agent_learning_coach.core.llm.models import LLMRequest
from agent_learning_coach.core.tools import (
    FunctionTool,
    ToolContext,
    ToolDefinition,
    ToolDispatcher,
    ToolRegistry,
)
from support import ScriptedClient, SlowClient, text_response, tool_call_response


def make_runtime(client: object, *, max_steps: int = 4) -> AgentRuntime:
    """构造一个使用评估工具集的小运行时。"""
    return AgentRuntime(
        client,  # type: ignore[arg-type]
        ToolDispatcher(build_eval_tools()),
        config=AgentConfig(model="mock-model", max_steps=max_steps),
    )


async def danger(context: ToolContext, arguments: dict[str, object]) -> str:
    """需要人工确认的工具。"""
    return "已执行"


class FailingClient:
    """每次调用都失败的模型客户端。"""

    async def generate(self, request: LLMRequest) -> object:
        """抛出模型调用错误。"""
        raise LLMError("服务端 500")

    async def generate_structured(self, request: LLMRequest, output_model: object) -> object:
        """未使用。"""
        raise NotImplementedError


def test_trace_projects_steps_and_tool_spans() -> None:
    """Trace 从已有的步与工具结果投影，不新增事实。"""
    client = ScriptedClient(
        [tool_call_response(name="boom", arguments={}), text_response("工具坏了")]
    )
    result = asyncio.run(make_runtime(client).run("试试 boom"))

    trace = build_trace(result)

    assert trace.execution_id == result.context.execution_id
    assert trace.stop_reason == "completed"
    assert trace.succeeded
    assert len(trace.steps) == 2
    assert trace.steps[0].tools[0].name == "boom"
    assert trace.steps[0].tools[0].failed
    assert "故意失败" in (trace.steps[0].tools[0].error or "")
    assert trace.steps[1].text == "工具坏了"
    assert trace.tool_calls == 1
    assert trace.failed_tool_calls == 1


def test_trace_records_model_latency() -> None:
    """模型耗时是运行时真正测出来的，不是推算的。"""
    result = asyncio.run(make_runtime(SlowClient(0.01)).run("慢一点"))

    assert result.steps[0].think_ms >= 5
    assert build_trace(result).steps[0].think_ms == result.steps[0].think_ms


def test_trace_of_llm_error_keeps_partial_result() -> None:
    """模型调用失败时，Trace 仍可用：停止原因与错误都在。"""
    result = asyncio.run(make_runtime(FailingClient()).run("会失败"))

    trace = build_trace(result)

    assert trace.status == "error"
    assert trace.stop_reason == "llm_error"
    assert trace.error is not None and "LLMError" in trace.error
    assert trace.steps == ()
    hint = diagnose(trace)
    assert hint is not None and "模型调用失败" in hint


def test_metrics_aggregate_runs() -> None:
    """Metrics 聚合成功率、停止原因、工具失败率与步数分布。"""
    completed = asyncio.run(make_runtime(ScriptedClient([text_response()])).run("一"))
    recovered = asyncio.run(
        make_runtime(
            ScriptedClient([tool_call_response(name="boom", arguments={}), text_response()])
        ).run("二")
    )
    spinning = asyncio.run(
        make_runtime(
            ScriptedClient(
                [tool_call_response(name="echo", arguments={"text": "x"})],
                repeat_last=True,
            ),
            max_steps=6,
        ).run("三")
    )

    metrics = compute_metrics(build_trace(item) for item in (completed, recovered, spinning))

    assert metrics.runs == 3
    assert metrics.successes == 2
    assert metrics.stop_reasons == {"completed": 2, "repeated_call": 1}
    assert metrics.tool_calls == 4
    assert metrics.failed_tool_calls == 1
    assert metrics.tool_failure_rate == 0.25
    assert metrics.step_counts == (1, 2, 3)
    assert metrics.steps_p50 == 2.0
    assert metrics.steps_p95 == 3.0
    assert "repeated_call 1" in metrics.summary()


def test_metrics_of_no_runs() -> None:
    """空样本不产生除零，也不编造成功率。"""
    metrics = compute_metrics([])

    assert metrics.runs == 0
    assert metrics.success_rate == 0.0
    assert metrics.summary() == "0 次运行"


def test_diagnose_points_at_repeated_action() -> None:
    """原地打转的诊断指出被重复的动作与次数。"""
    client = ScriptedClient(
        [tool_call_response(name="echo", arguments={"text": "x"})], repeat_last=True
    )
    trace = build_trace(asyncio.run(make_runtime(client, max_steps=6).run("打转")))

    hint = diagnose(trace)

    assert hint is not None
    assert "repeated_call" in hint
    assert "echo 被调用 3 次" in hint


def test_diagnose_lists_failed_tools() -> None:
    """连续失败的诊断带上失败的工具名。"""
    client = ScriptedClient([tool_call_response(name="boom", arguments={})], repeat_last=True)
    trace = build_trace(asyncio.run(make_runtime(client, max_steps=6).run("连续失败")))

    hint = diagnose(trace)

    assert hint is not None
    assert trace.stop_reason == "consecutive_errors"
    assert "boom 失败" in hint


def test_successful_run_needs_no_diagnosis() -> None:
    """成功的运行不产生诊断——没有事实可说。"""
    result = asyncio.run(make_runtime(ScriptedClient([text_response()])).run("一"))

    assert diagnose(build_trace(result)) is None


def test_pending_approval_is_not_a_failure() -> None:
    """等待人工批准不是失败：状态是暂停，诊断只解释怎么继续。"""
    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="danger",
                description="危险动作",
                parameters={"type": "object", "properties": {}},
                requires_confirmation=True,
            ),
            func=danger,
        )
    )
    runtime = AgentRuntime(
        ScriptedClient([tool_call_response(name="danger", arguments={})]),
        ToolDispatcher(registry),
        config=AgentConfig(model="mock-model"),
    )
    trace = build_trace(asyncio.run(runtime.run("做危险的事")))

    assert trace.status == "pending_approval"
    assert not trace.succeeded
    hint = diagnose(trace)
    assert hint is not None and "resume" in hint
    assert compute_metrics([trace]).success_rate == 0.0
