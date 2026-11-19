"""运行时安全测试。

对应 Topic 07「Runtime 测试与安全边界」，覆盖三组关注点：

**权限**：无权的工具不能被执行，也不能出现在给模型的工具清单里；策略在
结构化输出组合出 ``final_answer`` 工具之后仍然有效。

**非法参数**：参数不满足 schema 时，工具**不执行**，错误回填给模型让它
自愈；schema 本身不合法时，异常必须被收敛——不能逃出去打断整个运行。

**超时与失败**：工具超时、工具报错、工具不存在、模型报错、预算耗尽，
每一条路径都要有确定的停止原因，并且保持「每条 tool_call 都有对应的
tool_result」这条不变量。

这些测试是本项目**唯一**不接触真实 LLM 的地方——所有模型调用都由脚本化
客户端替身完成，因此可以在 CI 里稳定运行。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from mcp import types
from mcp.server.lowlevel import Server
from pydantic import BaseModel

from agent_learning_coach.core.agent import AgentConfig, AgentRuntime
from agent_learning_coach.core.llm.errors import LLMError
from agent_learning_coach.core.llm.models import LLMRequest, LLMResponse
from agent_learning_coach.core.llm.types import ToolCall
from agent_learning_coach.core.tools import (
    DenyAllPolicy,
    FunctionTool,
    ScopePolicy,
    ToolContext,
    ToolDefinition,
    ToolDispatcher,
    ToolRegistry,
    discover_mcp_tools,
)
from support import ScriptedClient, mcp_client_session, text_response, tool_call_response


def _registry_with_scoped_tool(scopes: tuple[str, ...] = ()) -> ToolRegistry:
    """构造一个只含单个带作用域工具的注册表。"""
    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="read_notes",
                description="读取学习笔记",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                scopes=scopes,
            ),
            func=_read_notes,
        )
    )
    return registry


async def _read_notes(context: ToolContext, arguments: dict[str, Any]) -> str:
    """读取笔记的假工具。"""
    return f"读到 {arguments['path']}"


def _runtime(
    registry: ToolRegistry,
    responses: list[LLMResponse],
    *,
    policy: Any = None,
    config: AgentConfig | None = None,
) -> tuple[AgentRuntime, ScriptedClient]:
    """构造一个使用脚本化客户端的运行时。"""
    client = ScriptedClient(responses)
    dispatcher = ToolDispatcher(registry, policy=policy)
    runtime = AgentRuntime(
        client,
        dispatcher,
        config=config or AgentConfig(model="mock-model", max_steps=4),
    )
    return runtime, client


# ---------------------------------------------------------------------------
# 权限检查
# ---------------------------------------------------------------------------


def test_denied_tool_is_not_executed() -> None:
    """无权的工具不能被执行——这是强制的核心。"""
    executed: list[str] = []

    async def spy(context: ToolContext, arguments: dict[str, Any]) -> str:
        executed.append(arguments["path"])
        return "不该发生"

    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="read_notes",
                description="读取笔记",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                scopes=("fs:read",),
            ),
            func=spy,
        )
    )

    runtime, _ = _runtime(
        registry,
        [tool_call_response("read_notes", {"path": "/etc/passwd"}), text_response("改用别的办法")],
        policy=ScopePolicy(granted=frozenset()),
    )

    result = asyncio.run(runtime.run("读取笔记"))

    assert executed == []
    assert result.status == "complete"
    assert "权限作用域" in result.steps[0].outcomes[0].content


def test_denied_tool_is_hidden_from_the_model() -> None:
    """无权的工具不出现在给模型的工具清单里——模型看不见就不会去试。"""
    registry = _registry_with_scoped_tool(scopes=("fs:read",))
    runtime, client = _runtime(
        registry,
        [text_response("好的")],
        policy=ScopePolicy(granted=frozenset()),
    )

    asyncio.run(runtime.run("你好"))

    assert client.requests[0].tools == []


def test_granted_tool_is_visible_to_the_model() -> None:
    """有权的工具正常出现在工具清单里。"""
    registry = _registry_with_scoped_tool(scopes=("fs:read",))
    runtime, client = _runtime(
        registry,
        [text_response("好的")],
        policy=ScopePolicy(granted=frozenset({"fs:read"})),
    )

    asyncio.run(runtime.run("你好"))

    assert [tool.name for tool in client.requests[0].tools] == ["read_notes"]


def test_permission_denial_is_fed_back_to_the_model() -> None:
    """权限拒绝被归一化成错误结果回填，模型可以据此改用别的工具。"""
    registry = _registry_with_scoped_tool(scopes=("fs:read",))
    runtime, _ = _runtime(
        registry,
        [
            tool_call_response("read_notes", {"path": "a.md"}),
            text_response("我没有权限，改用聊天回答"),
        ],
        policy=ScopePolicy(granted=frozenset()),
    )

    result = asyncio.run(runtime.run("读取笔记"))

    assert result.status == "complete"
    outcome = result.steps[0].outcomes[0]
    assert not outcome.succeeded
    assert "fs:read" in outcome.content


def test_scope_escalation_is_denied() -> None:
    """只授予读权限时，写权限的工具仍然被拒——作用域是子集关系，不是包含即放行。"""
    registry = ToolRegistry()
    for name, scopes in (("read_notes", ("fs:read",)), ("write_notes", ("fs:write",))):
        registry.register(
            FunctionTool(
                definition=ToolDefinition(name=name, description=name, scopes=scopes),
                func=_read_notes,
            )
        )

    runtime, client = _runtime(
        registry,
        [tool_call_response("write_notes", {}), text_response("好的")],
        policy=ScopePolicy(granted=frozenset({"fs:read"})),
    )

    result = asyncio.run(runtime.run("写入"))

    assert [tool.name for tool in client.requests[0].tools] == ["read_notes"]
    assert not result.steps[0].outcomes[0].succeeded


def test_deny_all_policy_blocks_every_tool() -> None:
    """全拒策略下没有任何工具可见也没有工具被执行。"""
    registry = _registry_with_scoped_tool()
    runtime, client = _runtime(
        registry,
        [tool_call_response("read_notes", {"path": "a.md"}), text_response("好的")],
        policy=DenyAllPolicy(),
    )

    result = asyncio.run(runtime.run("读取笔记"))

    assert client.requests[0].tools == []
    assert not result.steps[0].outcomes[0].succeeded


def test_allow_all_policy_keeps_default_behaviour() -> None:
    """默认策略放行一切——不配置权限时行为与引入权限机制之前一致。"""
    registry = _registry_with_scoped_tool(scopes=("fs:read",))
    runtime, client = _runtime(registry, [text_response("好的")])

    asyncio.run(runtime.run("你好"))

    assert [tool.name for tool in client.requests[0].tools] == ["read_notes"]


def test_permission_check_happens_before_argument_validation() -> None:
    """被拒绝的工具连参数都不校验——不向无权的一方泄露契约细节。"""
    registry = _registry_with_scoped_tool(scopes=("fs:read",))
    runtime, _ = _runtime(
        registry,
        [tool_call_response("read_notes", {"wrong": "参数"}), text_response("好的")],
        policy=ScopePolicy(granted=frozenset()),
    )

    result = asyncio.run(runtime.run("读取笔记"))

    content = result.steps[0].outcomes[0].content
    assert "权限作用域" in content
    assert "校验失败" not in content


def test_permission_policy_survives_final_answer_composition() -> None:
    """组合出 final_answer 工具之后，权限策略依然生效。

    这是真实的安全漏洞形态：组合调度器时如果漏掉策略，被限制的工具就等于
    被放开了。
    """

    class Plan(BaseModel):
        topic: str

    registry = _registry_with_scoped_tool(scopes=("fs:read",))
    runtime, _ = _runtime(
        registry,
        [tool_call_response("read_notes", {"path": "a.md"}), text_response("好的")],
        policy=ScopePolicy(granted=frozenset()),
        config=AgentConfig(model="mock-model", max_steps=4, output_model=Plan),
    )

    result = asyncio.run(runtime.run("读取笔记"))

    assert not result.steps[0].outcomes[0].succeeded
    assert "权限作用域" in result.steps[0].outcomes[0].content


def test_scopes_are_not_sent_to_the_model() -> None:
    """作用域是运行时的授权依据，不发给模型。"""
    registry = _registry_with_scoped_tool(scopes=("fs:read", "net:fetch"))
    runtime, client = _runtime(
        registry,
        [text_response("好的")],
        policy=ScopePolicy(granted=frozenset({"fs:read", "net:fetch"})),
    )

    asyncio.run(runtime.run("你好"))

    spec = client.requests[0].tools[0]
    assert not hasattr(spec, "scopes")
    assert "fs:read" not in repr(spec)


def test_unscoped_tool_is_allowed_under_scope_policy() -> None:
    """未声明作用域的工具恒放行——文档化的代价，行为必须与文档一致。"""
    registry = _registry_with_scoped_tool()
    runtime, client = _runtime(
        registry,
        [text_response("好的")],
        policy=ScopePolicy(granted=frozenset()),
    )

    asyncio.run(runtime.run("你好"))

    assert [tool.name for tool in client.requests[0].tools] == ["read_notes"]


# ---------------------------------------------------------------------------
# 非法参数
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "arguments",
    [
        {},
        {"path": 123},
        {"path": ["a.md"]},
    ],
)
def test_invalid_arguments_are_rejected(arguments: dict[str, Any]) -> None:
    """非法参数被拒绝，且错误可读。"""
    registry = _registry_with_scoped_tool()
    dispatcher = ToolDispatcher(registry)

    outcome = asyncio.run(
        dispatcher.dispatch_safe("read_notes", arguments, ToolContext(caller="t"))
    )

    assert not outcome.succeeded
    assert "校验失败" in outcome.content


def test_extra_properties_follow_the_schema_not_the_validator() -> None:
    """多余参数是否被拒绝，取决于 schema 而不是校验器。

    JSON Schema 的默认语义是允许多余属性。校验器若擅自收紧，就变成了
    「告诉模型 A、实际按 B 校验」——正是本模块要避免的错位。因此严格性
    必须由 schema 作者用 ``additionalProperties: false`` 显式声明。
    """
    permissive = ToolRegistry()
    permissive.register(
        FunctionTool(
            definition=ToolDefinition(
                name="read_notes",
                description="读取笔记",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            ),
            func=_read_notes,
        )
    )
    strict = ToolRegistry()
    strict.register(
        FunctionTool(
            definition=ToolDefinition(
                name="read_notes",
                description="读取笔记",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
            func=_read_notes,
        )
    )
    arguments = {"path": "a.md", "extra": 1}

    loose = asyncio.run(
        ToolDispatcher(permissive).dispatch_safe("read_notes", arguments, ToolContext(caller="t"))
    )
    tight = asyncio.run(
        ToolDispatcher(strict).dispatch_safe("read_notes", arguments, ToolContext(caller="t"))
    )

    assert loose.succeeded
    assert not tight.succeeded


def test_invalid_arguments_do_not_execute_the_tool() -> None:
    """参数不合法时工具不能被执行——校验必须发生在执行之前。"""
    executed: list[dict[str, Any]] = []

    async def spy(context: ToolContext, arguments: dict[str, Any]) -> str:
        executed.append(arguments)
        return "不该发生"

    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="read_notes",
                description="读取笔记",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            ),
            func=spy,
        )
    )
    dispatcher = ToolDispatcher(registry)

    outcome = asyncio.run(dispatcher.dispatch_safe("read_notes", {}, ToolContext(caller="t")))

    assert not outcome.succeeded
    assert executed == []


def test_type_coercion_is_not_performed() -> None:
    """字符串不会被悄悄转成数字——宽松的强制转换会让「模型传错了」变成静默通过。"""
    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="count",
                description="计数",
                parameters={
                    "type": "object",
                    "properties": {"n": {"type": "integer"}},
                    "required": ["n"],
                },
            ),
            func=_read_notes,
        )
    )
    dispatcher = ToolDispatcher(registry)

    outcome = asyncio.run(dispatcher.dispatch_safe("count", {"n": "5"}, ToolContext(caller="t")))

    assert not outcome.succeeded


def test_nested_argument_violation_is_rejected() -> None:
    """嵌套结构的参数也要被校验——只看顶层会漏掉真正的注入点。"""
    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="search",
                description="搜索",
                parameters={
                    "type": "object",
                    "properties": {
                        "filters": {
                            "type": "object",
                            "properties": {"limit": {"type": "integer"}},
                            "required": ["limit"],
                        }
                    },
                    "required": ["filters"],
                },
            ),
            func=_read_notes,
        )
    )
    dispatcher = ToolDispatcher(registry)

    outcome = asyncio.run(
        dispatcher.dispatch_safe("search", {"filters": {"limit": "lots"}}, ToolContext(caller="t"))
    )

    assert not outcome.succeeded
    assert "filters" in outcome.content


def test_array_item_violation_is_rejected() -> None:
    """数组元素的类型也要校验。"""
    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="search",
                description="搜索",
                parameters={
                    "type": "object",
                    "properties": {"terms": {"type": "array", "items": {"type": "string"}}},
                    "required": ["terms"],
                },
            ),
            func=_read_notes,
        )
    )
    dispatcher = ToolDispatcher(registry)

    outcome = asyncio.run(
        dispatcher.dispatch_safe("search", {"terms": ["ok", 3]}, ToolContext(caller="t"))
    )

    assert not outcome.succeeded


def test_malformed_schema_is_normalized_not_raised() -> None:
    """schema 本身不合法时，异常必须被收敛成错误结果。

    schema 可能来自远端 MCP Server——一个畸形 schema 不应该打断整个 Agent
    运行。这是真实缺陷的回归测试：``UnknownType`` 曾从 ``validate_arguments``
    逃出去，直冲运行时。
    """
    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="broken",
                description="契约损坏的工具",
                parameters={"type": "object", "properties": {"x": {"type": "bogus"}}},
            ),
            func=_read_notes,
        )
    )
    dispatcher = ToolDispatcher(registry)

    outcome = asyncio.run(dispatcher.dispatch_safe("broken", {"x": 1}, ToolContext(caller="t")))

    assert not outcome.succeeded
    assert "schema" in outcome.content


def test_malformed_schema_does_not_break_the_agent_loop() -> None:
    """契约损坏的工具不能打断整个运行——错误回填给模型，它换别的工具。"""
    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="broken",
                description="契约损坏的工具",
                parameters={"type": "object", "properties": {"x": {"type": "bogus"}}},
            ),
            func=_read_notes,
        )
    )

    runtime, _ = _runtime(
        registry,
        [tool_call_response("broken", {"x": 1}), text_response("我换一个工具")],
    )

    result = asyncio.run(runtime.run("试试这个工具"))

    assert result.status == "complete"
    assert not result.steps[0].outcomes[0].succeeded
    assert result.output == "我换一个工具"


# ---------------------------------------------------------------------------
# 超时与失败场景
# ---------------------------------------------------------------------------


def test_tool_timeout_is_normalized_and_fed_back() -> None:
    """工具超时归一化成错误结果，循环继续——超时是可预期的失败。"""

    async def slow(context: ToolContext, arguments: dict[str, Any]) -> str:
        await asyncio.sleep(10)
        return "太慢了"

    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            definition=ToolDefinition(name="slow", description="慢工具", timeout=0.01),
            func=slow,
        )
    )

    runtime, _ = _runtime(registry, [tool_call_response("slow", {}), text_response("换个方式")])

    result = asyncio.run(runtime.run("调用慢工具"))

    outcome = result.steps[0].outcomes[0]
    assert not outcome.succeeded
    assert "超时" in outcome.content
    assert result.output == "换个方式"


def test_tool_raising_exception_is_normalized() -> None:
    """工具内部报错归一化成错误结果，而不是打断运行。"""

    async def boom(context: ToolContext, arguments: dict[str, Any]) -> str:
        raise RuntimeError("工具炸了")

    registry = ToolRegistry()
    registry.register(
        FunctionTool(definition=ToolDefinition(name="boom", description="会炸"), func=boom)
    )

    runtime, _ = _runtime(registry, [tool_call_response("boom", {}), text_response("好的")])

    result = asyncio.run(runtime.run("调用会炸的工具"))

    assert not result.steps[0].outcomes[0].succeeded
    assert result.status == "complete"


def test_unknown_tool_is_normalized() -> None:
    """未注册的工具归一化成错误结果。"""
    runtime, _ = _runtime(
        _registry_with_scoped_tool(),
        [tool_call_response("nonexistent", {}), text_response("好的")],
    )

    result = asyncio.run(runtime.run("调用不存在的工具"))

    assert not result.steps[0].outcomes[0].succeeded
    assert result.status == "complete"


def test_every_failure_path_keeps_the_transcript_invariant() -> None:
    """所有失败路径都保持「每条 tool_call 都有对应的 tool_result」。

    这条不变量是模型能继续推理的前提：缺一条 tool_result，多数厂商的 API
    会直接拒绝整个请求。
    """

    async def boom(context: ToolContext, arguments: dict[str, Any]) -> str:
        raise RuntimeError("工具炸了")

    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="read_notes",
                description="读取笔记",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            ),
            func=boom,
        )
    )

    runtime, _ = _runtime(
        registry,
        [
            tool_call_response("read_notes", {"path": "a.md"}),
            tool_call_response("read_notes", {}),
            tool_call_response("nonexistent", {}),
            text_response("好的"),
        ],
    )

    result = asyncio.run(runtime.run("连续触发各种失败"))

    call_ids: set[str] = set()
    result_ids: set[str] = set()
    for event in result.context.events:
        for item in event.contents:
            if isinstance(item, ToolCall):
                call_ids.add(item.tool_call_id)
            elif getattr(item, "tool_call_id", None):
                result_ids.add(item.tool_call_id)
    assert call_ids
    assert call_ids == result_ids


def test_model_error_keeps_partial_result() -> None:
    """模型报错时保留已完成的部分——调用方据此决定是否重跑。"""

    class BrokenClient(ScriptedClient):
        async def generate(self, request: LLMRequest) -> LLMResponse:
            raise LLMError("模型挂了")

    registry = _registry_with_scoped_tool()
    dispatcher = ToolDispatcher(registry)
    runtime = AgentRuntime(
        BrokenClient([]),
        dispatcher,
        config=AgentConfig(model="mock-model", max_steps=4),
    )

    result = asyncio.run(runtime.run("你好"))

    assert result.status == "error"
    assert result.stop_reason == "llm_error"
    assert result.error is not None
    assert "模型挂了" in result.error


def test_cancellation_propagates_and_is_not_swallowed() -> None:
    """取消信号必须向上传播，不能被归一化成错误结果。

    ``asyncio.CancelledError`` 在 Python 3.8 起继承 ``BaseException`` 而非
    ``Exception``，因此不会被 ``except Exception`` 捕获——但这依赖一个容易
    被误改的实现细节，必须有测试钉住。
    """

    async def hang(context: ToolContext, arguments: dict[str, Any]) -> str:
        await asyncio.sleep(30)
        return "永远到不了"

    registry = ToolRegistry()
    registry.register(
        FunctionTool(definition=ToolDefinition(name="hang", description="挂住"), func=hang)
    )
    dispatcher = ToolDispatcher(registry)

    async def main() -> None:
        task = asyncio.create_task(dispatcher.dispatch_safe("hang", {}, ToolContext(caller="t")))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())


def test_consecutive_failures_stop_the_loop() -> None:
    """连续失败达到上限时停止——不让模型在同一个错误上无限打转。"""

    async def boom(context: ToolContext, arguments: dict[str, Any]) -> str:
        raise RuntimeError("工具炸了")

    registry = ToolRegistry()
    registry.register(
        FunctionTool(definition=ToolDefinition(name="boom", description="会炸"), func=boom)
    )

    runtime, _ = _runtime(
        registry,
        [
            tool_call_response("boom", {}),
            tool_call_response("boom", {}),
            tool_call_response("boom", {}),
            text_response("好的"),
        ],
        config=AgentConfig(model="mock-model", max_steps=6, max_consecutive_errors=2),
    )

    result = asyncio.run(runtime.run("一直失败"))

    assert result.stop_reason == "consecutive_errors"
    assert result.status == "error"


def test_budget_exhaustion_stops_the_loop() -> None:
    """预算耗尽时停止，并保留已完成的部分。"""
    registry = _registry_with_scoped_tool()
    runtime, _ = _runtime(
        registry,
        [tool_call_response("read_notes", {"path": "a.md"}), text_response("好的")],
        config=AgentConfig(model="mock-model", max_steps=4, max_tool_calls=1),
    )

    result = asyncio.run(runtime.run("读取笔记"))

    assert result.stop_reason == "max_tool_calls"
    assert result.status == "error"
    assert len(result.steps) == 1


def test_permission_denial_counts_toward_failure_budget() -> None:
    """权限拒绝计入连续失败——反复尝试被拒的工具应当被预算拦住。"""
    registry = _registry_with_scoped_tool(scopes=("fs:read",))
    runtime, _ = _runtime(
        registry,
        [
            tool_call_response("read_notes", {"path": "a.md"}),
            tool_call_response("read_notes", {"path": "b.md"}),
            text_response("好的"),
        ],
        policy=ScopePolicy(granted=frozenset()),
        config=AgentConfig(model="mock-model", max_steps=6, max_consecutive_errors=2),
    )

    result = asyncio.run(runtime.run("读取笔记"))

    assert result.stop_reason == "consecutive_errors"


# ---------------------------------------------------------------------------
# 远端 MCP 工具的作用域
# ---------------------------------------------------------------------------


def _mcp_server_with_tool(name: str = "search") -> Any:
    """构造一个只列出一个工具的 MCP Server。"""

    async def on_list_tools(context: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[types.Tool(name=name, description="远端工具", inputSchema={"type": "object"})]
        )

    async def on_call_tool(context: Any, params: Any) -> types.CallToolResult:
        return types.CallToolResult(content=[types.TextContent(type="text", text="远端结果")])

    return Server("test", on_list_tools=on_list_tools, on_call_tool=on_call_tool)


def test_mcp_tools_get_a_server_scoped_permission() -> None:
    """远端工具按 server 授予作用域——一个作用域就能关掉整个不可信 Server。"""
    server = _mcp_server_with_tool()

    async def _run() -> tuple[str, ...]:
        async with mcp_client_session(server) as session:
            tools = await discover_mcp_tools(session, namespace="web")
            return tools[0].definition.scopes

    assert asyncio.run(_run()) == ("mcp:web",)


def test_mcp_scope_can_be_overridden() -> None:
    """作用域可以显式指定，不依赖命名空间推导。"""
    server = _mcp_server_with_tool()

    async def _run() -> tuple[str, ...]:
        async with mcp_client_session(server) as session:
            tools = await discover_mcp_tools(session, scope="mcp:trusted")
            return tools[0].definition.scopes

    assert asyncio.run(_run()) == ("mcp:trusted",)


def test_untrusted_mcp_server_can_be_denied_entirely() -> None:
    """未授予 server 作用域时，该 Server 的全部工具都被拒绝。"""
    server = _mcp_server_with_tool()

    async def _run() -> bool:
        async with mcp_client_session(server) as session:
            tools = await discover_mcp_tools(session, namespace="web")
            registry = ToolRegistry()
            for tool in tools:
                registry.register(tool)
            dispatcher = ToolDispatcher(
                registry, policy=ScopePolicy(granted=frozenset({"mcp:other"}))
            )
            outcome = await dispatcher.dispatch_safe("web_search", {}, ToolContext(caller="test"))
            return outcome.succeeded

    assert asyncio.run(_run()) is False
