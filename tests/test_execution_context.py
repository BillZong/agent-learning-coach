"""Execution Context、Runtime State 与 Cost Control 的测试。

覆盖 Topic 06 的核心问题：**Execution Context 需要承载哪些状态，才能让
Agent 可测试、可复现？**

三个关注点：

- **Execution Context**：事件历史、步数、草稿本、用量与成本。
- **Runtime State**：草稿本的读写边界——工具与应用层可见，模型不可见。
- **Cost Control**：token 预算与成本预算的执行，以及预算无法执行时的报错。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_learning_coach.core.agent import (
    AgentConfig,
    AgentConfigError,
    AgentRuntime,
    Event,
    ExecutionContext,
    TokenPrice,
)
from agent_learning_coach.core.llm.models import LLMUsage
from agent_learning_coach.core.llm.types import Message
from agent_learning_coach.core.tools import (
    FunctionTool,
    ToolContext,
    ToolDefinition,
    ToolDispatcher,
    ToolRegistry,
)
from support import ScriptedClient, text_response, tool_call_response


def build_registry(*, writing_state: bool = False) -> ToolRegistry:
    """构造测试用注册表。

    Args:
        writing_state: True 时工具会往运行时草稿本写东西。
    """
    registry = ToolRegistry()

    async def search(context: ToolContext, arguments: dict[str, Any]) -> str:
        if writing_state:
            execution = context.metadata.get("execution_context")
            if execution is not None:
                execution.state["last_query"] = arguments.get("query")
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


def make_runtime(
    client: Any,
    *,
    config: AgentConfig | None = None,
    writing_state: bool = False,
) -> AgentRuntime:
    """构造被测运行时。"""
    registry = build_registry(writing_state=writing_state)
    return AgentRuntime(client, ToolDispatcher(registry), config=config)


# --------------------------------------------------------------------------
# Execution Context
# --------------------------------------------------------------------------


def test_context_records_events_in_order() -> None:
    """执行历史按发生顺序记录，且能还原出完整的对话。"""
    client = ScriptedClient(
        [
            tool_call_response(call_id="c1"),
            text_response("完成"),
        ]
    )
    runtime = make_runtime(client)

    run = asyncio.run(runtime.run("测试"))

    # 每次模型响应各成一个事件：请求工具一轮、给最终答案一轮
    authors = [event.author for event in run.context.events]
    assert authors == ["user", "agent", "tools", "agent"]

    # 展平后仍是对话顺序
    kinds = [type(item).__name__ for item in run.context.contents]
    assert kinds == ["Message", "ToolCall", "ToolResult", "Message"]


def test_context_step_counter_tracks_steps() -> None:
    """步数随执行推进。"""
    client = ScriptedClient(
        [
            tool_call_response(call_id="c1"),
            tool_call_response(call_id="c2"),
            text_response("完成"),
        ]
    )
    runtime = make_runtime(client)

    run = asyncio.run(runtime.run("测试"))

    assert run.context.current_step == 3
    assert len(run.steps) == 3


def test_context_id_is_generated_and_can_be_preset() -> None:
    """执行标识默认生成，也可显式指定以便跨系统串联。"""
    assert ExecutionContext().execution_id != ExecutionContext().execution_id
    assert ExecutionContext(execution_id="fixed").execution_id == "fixed"


def test_event_carries_author_and_timestamp() -> None:
    """事件携带发起者与时刻——这是「执行历史」与「对话列表」的区别。"""
    event = Event.create("exec-1", "model", (Message(role="assistant", content="hi"),))

    assert event.execution_id == "exec-1"
    assert event.author == "model"
    assert event.timestamp > 0
    assert event.contents[0].content == "hi"


def test_passing_existing_context_continues_the_conversation() -> None:
    """传入已有上下文可以续接之前的对话。"""
    context = ExecutionContext(execution_id="session-42")
    context.add_event(
        Event.create("session-42", "user", (Message(role="user", content="上一轮的问题"),))
    )
    client = ScriptedClient([text_response("这一轮的回答")])
    runtime = make_runtime(client)

    run = asyncio.run(runtime.run("这一轮的问题", context=context))

    assert run.context is context
    assert run.context.execution_id == "session-42"
    # 历史事件保留，新的一轮（用户提问 + 模型回答）追加在后
    assert len(run.context.events) == 3
    assert run.context.contents[0].content == "上一轮的问题"
    assert run.context.contents[-1].content == "这一轮的回答"


# --------------------------------------------------------------------------
# Runtime State
# --------------------------------------------------------------------------


def test_runtime_state_is_writable_by_tools() -> None:
    """工具能读写运行时草稿本。"""
    client = ScriptedClient(
        [
            tool_call_response(arguments={"query": "闭包"}),
            text_response("完成"),
        ]
    )
    runtime = make_runtime(client, writing_state=True)

    run = asyncio.run(runtime.run("测试"))

    assert run.context.state["last_query"] == "闭包"


def test_runtime_state_is_not_visible_to_the_model() -> None:
    """草稿本服务于工具与应用层，模型看不到它。"""
    client = ScriptedClient(
        [
            tool_call_response(arguments={"query": "闭包"}),
            text_response("完成"),
        ]
    )
    runtime = make_runtime(client, writing_state=True)

    asyncio.run(runtime.run("测试"))

    # 发给模型的请求里只有对话内容，没有草稿本
    for request in client.requests:
        assert not any(
            isinstance(item, Message) and "last_query" in item.content for item in request.contents
        )


def test_runtime_state_survives_across_runs_with_same_context() -> None:
    """同一个上下文里，草稿本跨运行保留。"""
    context = ExecutionContext()
    context.state["preset"] = "值"

    client = ScriptedClient([text_response("完成")])
    runtime = make_runtime(client)

    run = asyncio.run(runtime.run("测试", context=context))

    assert run.context.state["preset"] == "值"


# --------------------------------------------------------------------------
# Cost Control
# --------------------------------------------------------------------------


def test_token_budget_stops_the_loop() -> None:
    """累计 token 预算耗尽时停止。"""
    client = ScriptedClient([tool_call_response()], repeat_last=True)
    runtime = make_runtime(
        client,
        config=AgentConfig(max_total_tokens=25, max_steps=10, max_repeated_calls=None),
    )

    run = asyncio.run(runtime.run("测试"))

    assert run.stop_reason == "max_tokens"
    assert run.succeeded is False


def test_cost_is_accumulated_from_the_price_table() -> None:
    """成本按价格表核算并累计。"""
    client = ScriptedClient([text_response()])
    runtime = make_runtime(
        client,
        config=AgentConfig(prices={"mock-model": TokenPrice(input_per_1k=1.0, output_per_1k=2.0)}),
    )

    run = asyncio.run(runtime.run("测试"))

    # mock 响应: input=7, output=3 -> 7/1000*1.0 + 3/1000*2.0
    assert run.cost == pytest.approx(7 / 1000 * 1.0 + 3 / 1000 * 2.0)
    assert run.context.cost == run.cost


def test_cost_budget_stops_the_loop() -> None:
    """成本预算耗尽时停止。"""
    client = ScriptedClient([tool_call_response()], repeat_last=True)
    runtime = make_runtime(
        client,
        config=AgentConfig(
            prices={"mock-model": TokenPrice(input_per_1k=1.0, output_per_1k=2.0)},
            max_cost=0.02,
            max_steps=10,
            max_repeated_calls=None,
        ),
    )

    run = asyncio.run(runtime.run("测试"))

    assert run.stop_reason == "max_cost"
    assert run.cost >= 0.02


def test_cost_budget_without_price_for_model_is_a_config_error() -> None:
    """设了成本上限却没覆盖实际模型，是配置错误——预算无法被执行。"""
    client = ScriptedClient([text_response()])
    runtime = make_runtime(
        client,
        config=AgentConfig(
            prices={"other-model": TokenPrice(input_per_1k=1.0)},
            max_cost=1.0,
        ),
    )

    with pytest.raises(AgentConfigError, match="价格表"):
        asyncio.run(runtime.run("测试"))


def test_cost_budget_without_known_model_is_a_config_error() -> None:
    """模型名都无从确定时，同样无法核算成本。"""
    client = ScriptedClient([text_response(model="")])
    runtime = make_runtime(
        client,
        config=AgentConfig(
            prices={"mock-model": TokenPrice(input_per_1k=1.0)},
            max_cost=1.0,
        ),
    )

    with pytest.raises(AgentConfigError, match="无法确定使用的模型"):
        asyncio.run(runtime.run("测试"))


def test_token_price_computes_cost() -> None:
    """单价换算：按每千 token 计。"""
    price = TokenPrice(input_per_1k=3.0, output_per_1k=15.0)

    cost = price.cost_of(LLMUsage(input_tokens=1000, output_tokens=2000))

    assert cost == pytest.approx(3.0 + 30.0)


def test_agent_runtime_example_runs() -> None:
    """基础 Agent Runtime 示例必须能真实跑通——它是交付物，不是摆设。"""
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")

    result = subprocess.run(
        [sys.executable, str(root / "examples" / "agent_runtime.py")],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=str(root),
    )

    assert result.returncode == 0, result.stderr
    assert "停止原因: completed" in result.stdout
    assert "成本: " in result.stdout
    assert "[PASS]" in result.stdout


# --------------------------------------------------------------------------
# Agent Result
# --------------------------------------------------------------------------


def test_result_carries_output_status_and_context() -> None:
    """结果同时携带产物、状态与上下文。"""
    client = ScriptedClient([text_response("最终答案")])
    runtime = make_runtime(client)

    run = asyncio.run(runtime.run("测试"))

    assert run.output == "最终答案"
    assert run.status == "complete"
    assert run.stop_reason == "completed"
    assert run.succeeded is True
    assert run.context.final_result == "最终答案"
    assert run.error is None


def test_result_status_is_error_for_non_completed_runs() -> None:
    """非正常结束时状态为 error。"""
    client = ScriptedClient([tool_call_response()], repeat_last=True)
    runtime = make_runtime(client, config=AgentConfig(max_steps=2, max_repeated_calls=None))

    run = asyncio.run(runtime.run("测试"))

    assert run.status == "error"
    assert run.stop_reason == "max_steps"


def test_result_usage_reflects_context() -> None:
    """结果上的用量与上下文一致。"""
    client = ScriptedClient([text_response()])
    runtime = make_runtime(client)

    run = asyncio.run(runtime.run("测试"))

    assert run.usage.input_tokens == 7
    assert run.usage.output_tokens == 3
    assert run.usage is run.context.usage
