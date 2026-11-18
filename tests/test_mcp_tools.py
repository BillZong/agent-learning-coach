"""Tests for the MCP protocol adapter.

覆盖两个方向：

- **服务端适配**：本项目工具注册表 -> MCP Server（``tools/list`` / ``tools/call``）
- **客户端适配**：远端 MCP 工具 -> 本项目 Tool（接入 ``ToolRegistry``）

测试走真实的 MCP 协议（内存传输），不做任何协议层模拟。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import mcp.types as types
import pytest
from mcp.client.session import ClientSession
from mcp.server.lowlevel import Server
from mcp.shared.memory import create_client_server_memory_streams

from agent_learning_coach.core.tools import (
    FunctionTool,
    ToolContext,
    ToolDefinition,
    ToolDispatcher,
    ToolRegistry,
)
from agent_learning_coach.core.tools.mcp import (
    build_mcp_server,
    discover_mcp_tools,
    extract_text_content,
    from_mcp_tool,
    serve,
    to_mcp_tool,
)


async def _echo(context: ToolContext, arguments: dict[str, Any]) -> str:
    """回显参数。"""
    return f"echo:{arguments.get('text', '')}"


async def _add(context: ToolContext, arguments: dict[str, Any]) -> str:
    """两数相加。"""
    return str(arguments["a"] + arguments["b"])


async def _explode(context: ToolContext, arguments: dict[str, Any]) -> str:
    """总是失败。"""
    raise RuntimeError("remote tool failed")


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="echo",
                description="回显文本",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            ),
            func=_echo,
        )
    )
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="add",
                description="两数相加",
                parameters={
                    "type": "object",
                    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                    "required": ["a", "b"],
                },
            ),
            func=_add,
        )
    )
    registry.register(
        FunctionTool(
            definition=ToolDefinition(name="explode", description="总是失败"),
            func=_explode,
        )
    )
    return registry


@asynccontextmanager
async def _client_session(server: Server) -> AsyncIterator[ClientSession]:
    """在内存传输上建立 MCP 客户端会话。"""
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        server_task = asyncio.create_task(serve(server, server_streams[0], server_streams[1]))
        try:
            async with ClientSession(client_streams[0], client_streams[1]) as session:
                await session.initialize()
                yield session
        finally:
            server_task.cancel()
            await asyncio.gather(server_task, return_exceptions=True)


# --------------------------------------------------------------------------
# 定义映射
# --------------------------------------------------------------------------


def test_to_mcp_tool_preserves_schema() -> None:
    definition = ToolDefinition(
        name="search",
        description="检索",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
    )

    mcp_tool = to_mcp_tool(definition)

    assert mcp_tool.name == "search"
    assert mcp_tool.description == "检索"
    assert mcp_tool.input_schema == definition.parameters


def test_to_mcp_tool_empty_schema_defaults_to_object() -> None:
    mcp_tool = to_mcp_tool(ToolDefinition(name="bare"))

    assert mcp_tool.input_schema == {"type": "object", "properties": {}}


def test_from_mcp_tool_roundtrip() -> None:
    definition = ToolDefinition(
        name="search",
        description="检索",
        parameters={"type": "object", "properties": {"q": {"type": "string"}}},
    )

    restored = from_mcp_tool(to_mcp_tool(definition))

    assert restored.name == definition.name
    assert restored.description == definition.description
    assert restored.parameters == definition.parameters


# --------------------------------------------------------------------------
# 服务端适配：Tool Discovery
# --------------------------------------------------------------------------


def test_server_lists_tools() -> None:
    """验证 Tool Discovery：客户端能发现服务端暴露的全部工具。"""
    server = build_mcp_server(_registry())

    async def _run() -> list[types.Tool]:
        async with _client_session(server) as session:
            response = await session.list_tools()
            return list(response.tools)

    tools = asyncio.run(_run())

    assert {tool.name for tool in tools} == {"echo", "add", "explode"}


def test_discovery_preserves_description_and_schema() -> None:
    """发现到的工具必须携带与本地定义一致的描述与 schema。"""
    server = build_mcp_server(_registry())

    async def _run() -> list[types.Tool]:
        async with _client_session(server) as session:
            response = await session.list_tools()
            return list(response.tools)

    tools = {tool.name: tool for tool in asyncio.run(_run())}

    assert tools["echo"].description == "回显文本"
    assert tools["echo"].input_schema["required"] == ["text"]
    assert tools["add"].input_schema["properties"]["a"] == {"type": "integer"}


def test_discovery_on_empty_registry() -> None:
    server = build_mcp_server(ToolRegistry())

    async def _run() -> list[types.Tool]:
        async with _client_session(server) as session:
            response = await session.list_tools()
            return list(response.tools)

    assert asyncio.run(_run()) == []


# --------------------------------------------------------------------------
# 服务端适配：Tool Invocation
# --------------------------------------------------------------------------


def test_client_calls_tool() -> None:
    """验证 Tool Invocation：客户端能调用服务端工具并拿到结果。"""
    server = build_mcp_server(_registry())

    async def _run() -> str:
        async with _client_session(server) as session:
            result = await session.call_tool("echo", {"text": "hello"})
            return extract_text_content(result)

    assert asyncio.run(_run()) == "echo:hello"


def test_client_calls_tool_with_computation() -> None:
    server = build_mcp_server(_registry())

    async def _run() -> str:
        async with _client_session(server) as session:
            result = await session.call_tool("add", {"a": 17, "b": 25})
            return extract_text_content(result)

    assert asyncio.run(_run()) == "42"


def test_call_reports_error_for_invalid_arguments() -> None:
    """服务端复用 Dispatcher，因此参数校验错误表现为 is_error 结果。"""
    server = build_mcp_server(_registry())

    async def _run() -> types.CallToolResult:
        async with _client_session(server) as session:
            return await session.call_tool("add", {"a": 1})

    result = asyncio.run(_run())

    assert result.is_error is True
    # 跨协议只传可读消息：错误类型名留在本地 outcome.error 上
    assert "缺少必填参数" in extract_text_content(
        result
    ) or "required property" in extract_text_content(result)


def test_advertised_schema_is_the_enforced_schema() -> None:
    """「模型看到的契约」与「实际强制的契约」必须是同一份。

    这条不变式是选低层 Server 而非 ``MCPServer`` 的原因：后者的参数校验
    依据是从函数签名推导的 arg_model，与广告出去的 schema 不是同一份。

    因此这里用一个带 ``additionalProperties: False`` 与 ``minLength`` 的
    schema，同时断言两件事——客户端看到这些约束，且违反约束的调用被拒绝。
    """
    schema = {
        "type": "object",
        "properties": {"text": {"type": "string", "minLength": 3}},
        "required": ["text"],
        "additionalProperties": False,
    }

    async def _handler(context: ToolContext, arguments: dict[str, Any]) -> str:
        return str(arguments.get("text", ""))

    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            definition=ToolDefinition(name="strict", description="严格校验", parameters=schema),
            func=_handler,
        )
    )
    server = build_mcp_server(registry)

    async def _run() -> tuple[dict[str, Any], types.CallToolResult, types.CallToolResult]:
        async with _client_session(server) as session:
            tools = await session.list_tools()
            advertised = tools.tools[0].input_schema
            too_short = await session.call_tool("strict", {"text": "ab"})
            extra = await session.call_tool("strict", {"text": "abcd", "nope": 1})
            return advertised, too_short, extra

    advertised, too_short, extra = asyncio.run(_run())

    # 客户端看到完整约束
    assert advertised == schema
    assert advertised["additionalProperties"] is False
    assert advertised["properties"]["text"]["minLength"] == 3
    # 且这些约束真的被强制
    assert too_short.is_error is True
    assert extra.is_error is True


def test_call_unknown_tool_is_protocol_error() -> None:
    """工具不存在是协议级错误，不是工具执行错误。

    MCP 区分两类失败：协议错误（JSON-RPC error，客户端无法通过调整参数修复）
    与工具执行错误（is_error 结果，模型可以读懂并调整）。未知工具属于前者。
    """
    from mcp.shared.exceptions import MCPError

    server = build_mcp_server(_registry())

    async def _run() -> None:
        async with _client_session(server) as session:
            await session.call_tool("nope", {})

    with pytest.raises(BaseExceptionGroup) as exc_info:
        asyncio.run(_run())

    assert any(isinstance(exc, MCPError) for exc in _flatten(exc_info.value))


def _flatten(group: BaseExceptionGroup[BaseException]) -> list[BaseException]:
    """展开异常组，收集全部子异常。

    Args:
        group: 异常组。

    Returns:
        全部子异常（递归展开）。
    """
    collected: list[BaseException] = []
    for exc in group.exceptions:
        if isinstance(exc, BaseExceptionGroup):
            collected.extend(_flatten(exc))
        else:
            collected.append(exc)
    return collected


# --------------------------------------------------------------------------
# 真实 stdio 传输：以子进程方式运行示例 Server
# --------------------------------------------------------------------------


def test_example_server_runs_over_stdio() -> None:
    """示例 MCP Server 以子进程方式真实运行，发现与调用均可用。

    这是唯一覆盖真实传输（stdio）与真实进程边界的测试，因此不能用内存
    传输的替身。
    """
    import os
    import sys
    from pathlib import Path

    from agent_learning_coach.core.tools.mcp import stdio_session

    root = Path(__file__).resolve().parent.parent
    script = root / "examples" / "mcp_server.py"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")

    async def _run() -> tuple[list[str], str]:
        async with stdio_session(sys.executable, [str(script)], env=env) as session:
            remote = await discover_mcp_tools(session)
            names = sorted(tool.definition.name for tool in remote)
            result = await session.call_tool("search_learning_materials", {"query": "闭包"})
            return names, extract_text_content(result)

    names, text = asyncio.run(_run())

    assert names == ["generate_exercise", "query_mastery", "search_learning_materials"]
    assert "闭包" in text


def test_example_server_tool_invocation_through_dispatcher() -> None:
    """经我们的 Dispatcher 调用示例 Server 的工具，结果归一化一致。"""
    import os
    import sys
    from pathlib import Path

    from agent_learning_coach.core.tools.mcp import stdio_session

    root = Path(__file__).resolve().parent.parent
    script = root / "examples" / "mcp_server.py"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")

    async def _run() -> Any:
        async with stdio_session(sys.executable, [str(script)], env=env) as session:
            remote = await discover_mcp_tools(session)
            registry = ToolRegistry()
            for tool in remote:
                registry.register(tool)
            dispatcher = ToolDispatcher(registry)
            return await dispatcher.dispatch(
                "query_mastery", {"knowledge_point": "闭包"}, ToolContext()
            )

    outcome = asyncio.run(_run())

    assert outcome.status == "success"
    assert "闭包" in outcome.content


def test_example_server_rejects_invalid_arguments() -> None:
    """示例 Server 复用 Dispatcher，参数校验错误表现为 is_error 结果。"""
    import os
    import sys
    from pathlib import Path

    from agent_learning_coach.core.tools.mcp import stdio_session

    root = Path(__file__).resolve().parent.parent
    script = root / "examples" / "mcp_server.py"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")

    async def _run() -> types.CallToolResult:
        async with stdio_session(sys.executable, [str(script)], env=env) as session:
            return await session.call_tool("query_mastery", {})

    result = asyncio.run(_run())

    assert result.is_error is True
    assert "required property" in extract_text_content(result)


# --------------------------------------------------------------------------
# 客户端适配
# --------------------------------------------------------------------------


def test_discover_adapts_remote_tools() -> None:
    """远端工具被适配成本项目的 Tool，描述与 schema 保真。"""
    server = build_mcp_server(_registry())

    async def _run() -> list[Any]:
        async with _client_session(server) as session:
            return await discover_mcp_tools(session)

    tools = asyncio.run(_run())

    assert {tool.definition.name for tool in tools} == {"echo", "add", "explode"}
    echo = next(t for t in tools if t.definition.name == "echo")
    assert echo.definition.description == "回显文本"
    assert echo.definition.parameters["required"] == ["text"]


def test_discover_with_namespace_prefixes_names() -> None:
    """命名空间前缀用于避免多个 MCP Server 的同名工具冲突。"""
    server = build_mcp_server(_registry())

    async def _run() -> list[Any]:
        async with _client_session(server) as session:
            return await discover_mcp_tools(session, namespace="demo")

    tools = asyncio.run(_run())

    assert {tool.definition.name for tool in tools} == {"demo_echo", "demo_add", "demo_explode"}


def test_namespaced_tool_still_calls_remote_by_original_name() -> None:
    """带前缀的本地工具必须用远端原始名调用。

    本地名带命名空间前缀只是本地注册表里的标识；协议调用必须用远端的原始
    名，否则远端会报「工具不存在」。
    """
    server = build_mcp_server(_registry())

    async def _run() -> Any:
        async with _client_session(server) as session:
            remote = await discover_mcp_tools(session, namespace="demo")
            registry = ToolRegistry()
            for tool in remote:
                registry.register(tool)
            dispatcher = ToolDispatcher(registry)
            return await dispatcher.dispatch("demo_echo", {"text": "hi"}, ToolContext())

    outcome = asyncio.run(_run())

    assert outcome.status == "success"
    assert outcome.content == "echo:hi"


def test_namespaced_tool_exposes_remote_name() -> None:
    server = build_mcp_server(_registry())

    async def _run() -> list[Any]:
        async with _client_session(server) as session:
            return await discover_mcp_tools(session, namespace="demo")

    tools = asyncio.run(_run())
    echo = next(t for t in tools if t.definition.name == "demo_echo")

    assert echo.remote_name == "echo"


def test_discovered_tool_registers_into_our_registry() -> None:
    """适配进来的 MCP 工具能直接注册进本项目的注册表。"""
    server = build_mcp_server(_registry())

    async def _run() -> ToolRegistry:
        async with _client_session(server) as session:
            remote = await discover_mcp_tools(session)
            registry = ToolRegistry()
            for tool in remote:
                registry.register(tool)
            return registry

    registry = asyncio.run(_run())

    assert registry.names() == ["add", "echo", "explode"]
    assert len(registry.specs()) == 3


def test_discovered_tool_dispatched_by_our_dispatcher() -> None:
    """MCP 工具经我们的 Dispatcher 执行，拿到一致的归一化结果。"""
    server = build_mcp_server(_registry())

    async def _run() -> Any:
        async with _client_session(server) as session:
            remote = await discover_mcp_tools(session)
            registry = ToolRegistry()
            for tool in remote:
                registry.register(tool)
            dispatcher = ToolDispatcher(registry)
            return await dispatcher.dispatch("echo", {"text": "hi"}, ToolContext())

    outcome = asyncio.run(_run())

    assert outcome.status == "success"
    assert outcome.content == "echo:hi"


def test_discovered_tool_validated_locally_before_call() -> None:
    """适配进来的工具同样享受本地 Schema 校验。"""
    server = build_mcp_server(_registry())

    async def _run() -> Any:
        async with _client_session(server) as session:
            remote = await discover_mcp_tools(session)
            registry = ToolRegistry()
            for tool in remote:
                registry.register(tool)
            dispatcher = ToolDispatcher(registry)
            return await dispatcher.dispatch("echo", {"wrong": 1}, ToolContext())

    outcome = asyncio.run(_run())

    assert outcome.status == "error"
    assert "ToolValidationError" in (outcome.error or "")


def test_discovered_tool_error_propagates_as_error_outcome() -> None:
    """远端工具执行失败时，本地结果状态为 error，内容包含远端错误。"""
    server = build_mcp_server(_registry())

    async def _run() -> Any:
        async with _client_session(server) as session:
            remote = await discover_mcp_tools(session)
            registry = ToolRegistry()
            for tool in remote:
                registry.register(tool)
            dispatcher = ToolDispatcher(registry)
            return await dispatcher.dispatch("explode", {}, ToolContext())

    outcome = asyncio.run(_run())

    assert outcome.status == "error"
    assert "remote tool failed" in outcome.content


# --------------------------------------------------------------------------
# 端到端：MCP 工具与本地工具共存于同一注册表
# --------------------------------------------------------------------------


def test_mcp_and_local_tools_coexist() -> None:
    """MCP 工具与本地工具在同一注册表中共存，由同一 Dispatcher 调度。"""
    server = build_mcp_server(_registry())

    async def _run() -> tuple[list[str], Any]:
        async with _client_session(server) as session:
            remote = await discover_mcp_tools(session, namespace="remote")
            registry = ToolRegistry()
            registry.register(
                FunctionTool(
                    definition=ToolDefinition(name="local", description="本地工具"),
                    func=_echo,
                )
            )
            for tool in remote:
                registry.register(tool)
            dispatcher = ToolDispatcher(registry)
            local = await dispatcher.dispatch("local", {}, ToolContext())
            return registry.names(), local

    names, local_outcome = asyncio.run(_run())

    assert names == ["local", "remote_add", "remote_echo", "remote_explode"]
    assert local_outcome.status == "success"
