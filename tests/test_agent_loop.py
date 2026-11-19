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
    AgentLoop,
    AgentRun,
)
from agent_learning_coach.core.llm.models import LLMRequest, LLMResponse, LLMUsage
from agent_learning_coach.core.llm.types import Message, ToolCall, ToolResult
from agent_learning_coach.core.tools import (
    FunctionTool,
    ToolContext,
    ToolDefinition,
    ToolDispatcher,
    ToolRegistry,
)


class ScriptedClient:
    """按脚本依次返回预设响应的模型客户端。

    ``repeat_last=True`` 时脚本耗尽后重复最后一个响应——用于模拟「模型一直
    请求工具」的死循环场景。
    """

    def __init__(self, responses: list[LLMResponse], *, repeat_last: bool = False) -> None:
        self.responses = list(responses)
        self.repeat_last = repeat_last
        self.requests: list[LLMRequest] = []
        self._last_response: LLMResponse | None = None

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self.responses:
            if self.repeat_last and self._last_response is not None:
                return self._last_response
            raise AssertionError("脚本已耗尽，但循环仍在调用模型")
        response = self.responses.pop(0)
        self._last_response = response
        return response

    async def generate_structured(
        self, request: LLMRequest, output_model: type[Any]
    ) -> Any:  # pragma: no cover - Agent Loop 不使用结构化输出
        raise NotImplementedError


class SlowClient:
    """每次调用都耗时的模型客户端，用于验证整体超时。"""

    def __init__(self, delay: float) -> None:
        self.delay = delay

    async def generate(self, request: LLMRequest) -> LLMResponse:
        await asyncio.sleep(self.delay)
        return text_response("太慢了")

    async def generate_structured(
        self, request: LLMRequest, output_model: type[Any]
    ) -> Any:  # pragma: no cover
        raise NotImplementedError


def tool_call_response(
    name: str = "search",
    arguments: dict[str, Any] | None = None,
    call_id: str = "call-1",
) -> LLMResponse:
    """构造一个只含工具调用的模型响应。"""
    return LLMResponse(
        contents=[
            ToolCall(
                tool_call_id=call_id,
                name=name,
                arguments=arguments if arguments is not None else {"query": "闭包"},
            )
        ],
        usage=LLMUsage(input_tokens=10, output_tokens=5),
    )


def text_response(text: str = "已完成") -> LLMResponse:
    """构造一个只含文本的模型响应。"""
    return LLMResponse(
        contents=[Message(role="assistant", content=text)],
        usage=LLMUsage(input_tokens=7, output_tokens=3),
    )


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
) -> AgentLoop:
    """构造被测循环。"""
    registry = build_registry(failing=failing)
    return AgentLoop(client, ToolDispatcher(registry), config=config)


def tool_results(run: AgentRun) -> list[ToolResult]:
    """从运行结果中取出全部工具结果。"""
    return [item for item in run.contents if isinstance(item, ToolResult)]


def tool_calls(run: AgentRun) -> list[ToolCall]:
    """从运行结果中取出全部工具调用。"""
    return [item for item in run.contents if isinstance(item, ToolCall)]


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
    loop = AgentLoop(
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
    contents = [Message(role="user", content="闭包是什么")]

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


def test_model_error_propagates_to_caller() -> None:
    """模型调用失败必须向上抛出，由调用方决定是否重试。"""
    from agent_learning_coach.core.llm.errors import LLMError

    class FailingClient:
        async def generate(self, request: LLMRequest) -> LLMResponse:
            raise LLMError("模型服务不可用")

        async def generate_structured(
            self, request: LLMRequest, output_model: type[Any]
        ) -> Any:  # pragma: no cover
            raise NotImplementedError

    loop = make_loop(FailingClient())

    with pytest.raises(LLMError, match="模型服务不可用"):
        asyncio.run(loop.run("测试"))
