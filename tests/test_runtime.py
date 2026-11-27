"""Tests for the Agent Loop.

覆盖三层：

- **循环形状**：模型 -> 工具 -> 结果回填 -> 模型。
- **停止条件**：自然结束、步数上限、工具预算、连续失败、原地打转、超时。
- **关键语义**：工具失败回填而非终止；对话记录保持「每条 tool_call 都有
  对应 tool_result」的不变量。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_learning_coach.core.agent import (
    AgentConfig,
    AgentResult,
    AgentRuntime,
)
from agent_learning_coach.core.llm.models import LLMRequest, LLMResponse
from agent_learning_coach.core.llm.types import ContentItem, Message, ToolCall, ToolResult
from agent_learning_coach.core.tools import (
    FunctionTool,
    ToolContext,
    ToolDefinition,
    ToolDispatcher,
    ToolRegistry,
)
from support import ScriptedClient, SlowClient, text_response, tool_call_response


def build_registry(*, failing: bool = False) -> ToolRegistry:
    """构造测试用工具注册表。

    Args:
        failing: True 时工具总是失败。
    """
    registry = ToolRegistry()

    async def search(context: ToolContext, arguments: dict[str, Any]) -> str:
        if failing:
            raise RuntimeError("检索服务不可用")
        return f"检索 {arguments.get('query')!r} 得到 3 条资料"

    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="search",
                description="检索学习资料",
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            ),
            func=search,
        )
    )
    return registry


def make_loop(
    client: Any,
    *,
    failing: bool = False,
    config: AgentConfig | None = None,
) -> AgentRuntime:
    """构造被测循环。"""
    registry = build_registry(failing=failing)
    return AgentRuntime(client, ToolDispatcher(registry), config=config)


def tool_results(run: AgentResult) -> list[ToolResult]:
    """从运行结果中取出全部工具结果。"""
    return [item for item in run.context.contents if isinstance(item, ToolResult)]


def tool_calls(run: AgentResult) -> list[ToolCall]:
    """从运行结果中取出全部工具调用。"""
    return [item for item in run.context.contents if isinstance(item, ToolCall)]


# --------------------------------------------------------------------------
# 循环形状
# --------------------------------------------------------------------------


def test_model_without_tool_call_finishes_immediately() -> None:
    """模型不请求工具时，循环自然结束。"""
    client = ScriptedClient([text_response("学习计划已生成")])

    run = asyncio.run(make_loop(client).run("帮我安排学习计划"))

    assert run.stop_reason == "completed"
    assert run.succeeded is True
    assert run.final_text == "学习计划已生成"
    assert len(run.steps) == 1
    assert run.steps[0].tool_calls == ()


def test_tool_call_is_executed_and_fed_back() -> None:
    """工具结果必须回填给模型，模型据此继续推理。"""
    client = ScriptedClient(
        [
            tool_call_response(),
            text_response("根据检索结果，闭包的关键点是……"),
        ]
    )

    run = asyncio.run(make_loop(client).run("闭包是什么"))

    assert run.stop_reason == "completed"
    assert run.tool_call_count == 1

    results = tool_results(run)
    assert len(results) == 1
    assert results[0].status == "success"
    assert "检索" in results[0].content

    # 第二轮请求里必须带上工具结果
    second_request = client.requests[1]
    assert any(isinstance(item, ToolResult) for item in second_request.contents)


def test_tool_arguments_are_passed_through() -> None:
    """模型给的参数必须原样传给工具。"""
    seen: list[dict[str, Any]] = []
    registry = ToolRegistry()

    async def echo(context: ToolContext, arguments: dict[str, Any]) -> str:
        seen.append(arguments)
        return "ok"

    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="echo",
                description="回显",
                parameters={
                    "type": "object",
                    "properties": {"q": {"type": "string"}},
                    "required": ["q"],
                },
            ),
            func=echo,
        )
    )
    loop = AgentRuntime(
        ScriptedClient([tool_call_response("echo", {"q": "闭包"}), text_response()]),
        ToolDispatcher(registry),
    )

    asyncio.run(loop.run("测试"))

    assert seen == [{"q": "闭包"}]


def test_registered_tools_are_offered_to_the_model() -> None:
    """注册表里的工具必须作为工具清单发给模型。"""
    client = ScriptedClient([text_response()])

    asyncio.run(make_loop(client).run("测试"))

    specs = client.requests[0].tools
    assert [spec.name for spec in specs] == ["search"]
    assert specs[0].parameters["required"] == ["query"]


def test_instructions_are_sent_every_turn() -> None:
    """系统指令每一轮都要带上。"""
    client = ScriptedClient([tool_call_response(), text_response()])
    loop = make_loop(client, config=AgentConfig(instructions=("你是学习教练",)))

    asyncio.run(loop.run("测试"))

    assert client.requests[0].instructions == ["你是学习教练"]
    assert client.requests[1].instructions == ["你是学习教练"]


def test_usage_is_accumulated_across_steps() -> None:
    """token 用量要跨步累计。"""
    client = ScriptedClient([tool_call_response(), text_response()])

    run = asyncio.run(make_loop(client).run("测试"))

    assert run.usage.input_tokens == 10 + 7
    assert run.usage.output_tokens == 5 + 3


# --------------------------------------------------------------------------
# 关键语义：失败回填而非终止
# --------------------------------------------------------------------------


def test_tool_failure_is_fed_back_and_loop_continues() -> None:
    """工具失败必须回填给模型让它自愈，而不是直接终止循环。"""
    client = ScriptedClient(
        [
            tool_call_response(),
            text_response("检索失败，我改用其它方式回答"),
        ]
    )

    run = asyncio.run(make_loop(client, failing=True).run("测试"))

    assert run.stop_reason == "completed"
    assert run.succeeded is True

    results = tool_results(run)
    assert len(results) == 1
    assert results[0].status == "error"
    # 错误信息要能回填给模型读懂
    assert "检索服务不可用" in results[0].content


def test_unknown_tool_is_fed_back_as_error() -> None:
    """模型幻觉出不存在的工具时，错误回填而不是中断运行。"""
    client = ScriptedClient(
        [
            tool_call_response("no_such_tool"),
            text_response("抱歉，我用错工具了"),
        ]
    )

    run = asyncio.run(make_loop(client).run("测试"))

    assert run.stop_reason == "completed"
    results = tool_results(run)
    assert results[0].status == "error"
    assert "no_such_tool" in results[0].content


def test_invalid_arguments_are_fed_back_as_error() -> None:
    """参数不合法时，错误回填给模型让它改参数。"""
    client = ScriptedClient(
        [
            tool_call_response(arguments={"wrong": 1}),
            text_response("我换个参数"),
        ]
    )

    run = asyncio.run(make_loop(client).run("测试"))

    assert run.stop_reason == "completed"
    results = tool_results(run)
    assert results[0].status == "error"


# --------------------------------------------------------------------------
# 停止条件
# --------------------------------------------------------------------------


def test_max_steps_stops_the_loop() -> None:
    """步数上限兜底：模型一直请求工具时必须停下来。"""
    client = ScriptedClient([tool_call_response()], repeat_last=True)
    loop = make_loop(
        client,
        config=AgentConfig(max_steps=3, max_repeated_calls=None),
    )

    run = asyncio.run(loop.run("测试"))

    assert run.stop_reason == "max_steps"
    assert run.succeeded is False
    assert len(run.steps) == 3


def test_specific_stop_reason_wins_over_budget() -> None:
    """多个停止条件同时命中时，更具体的原因优先。

    「模型在原地打转」比「步数用完了」更能说明问题——前者指向模型行为，
    后者只是预算耗尽。因此重复调用优先于步数上限上报。
    """
    client = ScriptedClient([tool_call_response()], repeat_last=True)
    loop = make_loop(client, config=AgentConfig(max_steps=3, max_repeated_calls=2))

    run = asyncio.run(loop.run("测试"))

    assert run.stop_reason == "repeated_call"


def test_max_tool_calls_stops_the_loop() -> None:
    """工具调用预算兜底。"""
    client = ScriptedClient([tool_call_response()], repeat_last=True)
    loop = make_loop(client, config=AgentConfig(max_tool_calls=2, max_steps=10))

    run = asyncio.run(loop.run("测试"))

    assert run.stop_reason == "max_tool_calls"
    assert run.tool_call_count == 2


def test_consecutive_errors_stop_the_loop() -> None:
    """连续工具失败超限时停止：模型陷在同一个错误里，继续回填只是浪费预算。"""
    client = ScriptedClient([tool_call_response()], repeat_last=True)
    loop = make_loop(client, failing=True, config=AgentConfig(max_consecutive_errors=2))

    run = asyncio.run(loop.run("测试"))

    assert run.stop_reason == "consecutive_errors"
    assert run.tool_call_count == 2


def test_repeated_call_stops_the_loop() -> None:
    """同一工具同一参数反复调用时停止：模型在原地打转。"""
    client = ScriptedClient([tool_call_response()], repeat_last=True)
    loop = make_loop(client, config=AgentConfig(max_repeated_calls=2, max_steps=10))

    run = asyncio.run(loop.run("测试"))

    assert run.stop_reason == "repeated_call"
    assert run.tool_call_count == 2


def test_repeated_call_with_different_arguments_is_allowed() -> None:
    """参数不同就不是原地打转，循环应当继续。"""
    client = ScriptedClient(
        [
            tool_call_response(arguments={"query": "a"}),
            tool_call_response(arguments={"query": "b"}),
            text_response(),
        ]
    )
    loop = make_loop(client, config=AgentConfig(max_repeated_calls=2, max_steps=10))

    run = asyncio.run(loop.run("测试"))

    assert run.stop_reason == "completed"


def test_timeout_stops_the_loop_and_keeps_partial_result() -> None:
    """整体超时必须停下来，且保留已完成的部分结果。"""
    client = SlowClient(delay=5.0)
    loop = make_loop(client, config=AgentConfig(timeout=0.1))

    run = asyncio.run(loop.run("测试"))

    assert run.stop_reason == "timeout"
    assert run.succeeded is False


# --------------------------------------------------------------------------
# 不变量
# --------------------------------------------------------------------------


def test_every_tool_call_has_a_matching_tool_result() -> None:
    """对话记录必须保持「每条 tool_call 都有对应 tool_result」的不变量。

    这条不变量是结果可直接作为下一轮对话输入继续的前提。
    """
    client = ScriptedClient(
        [
            tool_call_response(call_id="c1"),
            tool_call_response(call_id="c2"),
            text_response(),
        ]
    )

    run = asyncio.run(make_loop(client).run("测试"))

    calls = tool_calls(run)
    results = tool_results(run)
    assert len(calls) == 2
    assert len(results) == 2
    assert {c.tool_call_id for c in calls} == {r.tool_call_id for r in results}


def test_transcript_invariant_holds_when_budget_stops_the_loop() -> None:
    """预算耗尽时，对话记录的不变量同样成立。"""
    client = ScriptedClient([tool_call_response()], repeat_last=True)
    loop = make_loop(client, config=AgentConfig(max_tool_calls=2, max_steps=10))

    run = asyncio.run(loop.run("测试"))

    calls = tool_calls(run)
    results = tool_results(run)
    assert len(calls) == len(results)
    assert {c.tool_call_id for c in calls} == {r.tool_call_id for r in results}


def test_prompt_accepts_content_items() -> None:
    """输入可以是内容项列表，不只是文本。"""
    client = ScriptedClient([text_response()])
    contents: list[ContentItem] = [Message(role="user", content="闭包是什么")]

    run = asyncio.run(make_loop(client).run(contents))

    assert run.stop_reason == "completed"
    assert client.requests[0].contents[0] == contents[0]


def test_expired_deadline_stops_before_calling_the_model() -> None:
    """截止时间已过时，不应再发起模型调用。"""
    client = ScriptedClient([text_response()])
    loop = make_loop(client, config=AgentConfig(timeout=0.0))

    run = asyncio.run(loop.run("测试"))

    assert run.stop_reason == "timeout"
    assert client.requests == []


def test_final_text_is_empty_when_model_never_spoke() -> None:
    """模型只请求过工具、没给出文本时，final_text 为空串。"""
    client = ScriptedClient([tool_call_response()], repeat_last=True)
    loop = make_loop(client, config=AgentConfig(max_steps=2, max_repeated_calls=None))

    run = asyncio.run(loop.run("测试"))

    assert run.stop_reason == "max_steps"
    assert run.final_text == ""


def test_step_records_failure() -> None:
    """步骤记录能反映本步是否有工具失败。"""
    client = ScriptedClient([tool_call_response(), text_response()])

    run = asyncio.run(make_loop(client, failing=True).run("测试"))

    assert run.steps[0].failed is True
    assert run.steps[0].executed_tool_calls == 1


def test_multi_round_tool_calling_accumulates_history_in_order() -> None:
    """多轮工具调用：每轮结果都被回填，对话记录顺序正确，历史逐轮累积。

    这是「多轮 Tool Calling」的核心保证——模型在第 N 轮必须能看到前 N-1 轮
    的全部工具结果，否则它只能瞎猜。
    """
    client = ScriptedClient(
        [
            tool_call_response("search", {"query": "a"}, call_id="c1"),
            tool_call_response("search", {"query": "b"}, call_id="c2"),
            tool_call_response("search", {"query": "c"}, call_id="c3"),
            text_response("三轮都完成了"),
        ]
    )
    loop = make_loop(client)

    run = asyncio.run(loop.run("测试"))

    assert run.stop_reason == "completed"
    assert run.tool_call_count == 3
    assert len(run.steps) == 4  # 三轮工具 + 一轮总结

    # 对话记录顺序：用户提问 -> (工具调用 + 工具结果) * 3 -> 最终回答
    kinds = [type(item).__name__ for item in run.context.contents]
    assert kinds == [
        "Message",
        "ToolCall",
        "ToolResult",
        "ToolCall",
        "ToolResult",
        "ToolCall",
        "ToolResult",
        "Message",
    ]

    # 工具结果与调用一一对应，且顺序一致
    results = tool_results(run)
    assert [r.tool_call_id for r in results] == ["c1", "c2", "c3"]
    assert [r.content for r in results] == [
        "检索 'a' 得到 3 条资料",
        "检索 'b' 得到 3 条资料",
        "检索 'c' 得到 3 条资料",
    ]

    # 每一轮发给模型的请求都带上此前全部结果
    assert len(client.requests) == 4
    for round_index in range(1, 4):
        sent = client.requests[round_index].contents
        assert sum(1 for item in sent if isinstance(item, ToolResult)) == round_index


def test_basic_react_agent_example_runs() -> None:
    """基础 ReAct Agent 示例必须能真实跑通——它是交付物，不是摆设。"""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")

    result = subprocess.run(
        [sys.executable, str(root / "examples" / "basic_react_agent.py")],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
        cwd=str(root),
    )

    assert result.returncode == 0, result.stderr
    assert "停止原因: completed" in result.stdout
    assert "工具调用数: 2" in result.stdout
    assert "步数: 3" in result.stdout


def test_model_error_ends_the_run_but_keeps_partial_result() -> None:
    """模型调用失败时运行结束，但已完成的部分结果必须保留。

    模型失败与超时是同一类运行时失败：两者都返回部分结果，而不是把整次
    运行丢掉。调用方据此决定是否重跑。
    """
    from agent_learning_coach.core.llm.errors import LLMError

    class FailingClient:
        """第一轮正常，之后失败。"""

        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, request: LLMRequest) -> LLMResponse:
            self.calls += 1
            if self.calls == 1:
                return tool_call_response()
            raise LLMError("模型服务不可用")

        async def generate_structured(
            self, request: LLMRequest, output_model: type[Any]
        ) -> Any:  # pragma: no cover
            raise NotImplementedError

    loop = make_loop(FailingClient())

    run = asyncio.run(loop.run("测试"))

    assert run.stop_reason == "llm_error"
    assert run.succeeded is False
    assert run.error is not None
    assert "模型服务不可用" in run.error
    # 第一步已完成的部分结果保留
    assert len(run.steps) == 1
    assert run.tool_call_count == 1
    assert tool_results(run)


def test_model_error_on_first_call_ends_the_run() -> None:
    """第一次模型调用就失败时，运行以 llm_error 结束。"""
    from agent_learning_coach.core.llm.errors import LLMError

    class FailingClient:
        async def generate(self, request: LLMRequest) -> LLMResponse:
            raise LLMError("模型服务不可用")

        async def generate_structured(
            self, request: LLMRequest, output_model: type[Any]
        ) -> Any:  # pragma: no cover
            raise NotImplementedError

    loop = make_loop(FailingClient())

    run = asyncio.run(loop.run("测试"))

    assert run.stop_reason == "llm_error"
    assert run.steps == ()
    assert run.tool_call_count == 0


def test_non_llm_exception_is_not_swallowed() -> None:
    """非 LLMError 的异常必须照常抛出——那是编程错误，不该被包装成运行结果。"""

    class BrokenClient:
        async def generate(self, request: LLMRequest) -> LLMResponse:
            raise AttributeError("客户端实现有 bug")

        async def generate_structured(
            self, request: LLMRequest, output_model: type[Any]
        ) -> Any:  # pragma: no cover
            raise NotImplementedError

    loop = make_loop(BrokenClient())

    with pytest.raises(AttributeError, match="客户端实现有 bug"):
        asyncio.run(loop.run("测试"))
