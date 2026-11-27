"""Tests for the MCP protocol adapter.

覆盖两个方向：

- **服务端适配**：本项目工具注册表 -> MCP Server（``tools/list`` / ``tools/call``）
- **客户端适配**：远端 MCP 工具 -> 本项目 Tool（接入 ``ToolRegistry``）

测试走真实的 MCP 协议（内存传输），不做任何协议层模拟。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from mcp import types
from mcp.client.session import ClientSession
from mcp.server.lowlevel import Server
from mcp.shared.exceptions import MCPError
from mcp.shared.memory import create_client_server_memory_streams

from agent_learning_coach.core.tools import (
    FunctionTool,
    MCPConnectionError,
    ToolContext,
    ToolDefinition,
    ToolDispatcher,
    ToolRegistry,
)
from agent_learning_coach.core.tools.mcp import (
    MCPTool,
    build_mcp_server,
    discover_mcp_tools,
    extract_text_content,
    from_mcp_tool,
    serve,
    stdio_session,
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


# --------------------------------------------------------------------------
# 结果提取：结构化输出与非文本内容块不得被静默丢弃
# --------------------------------------------------------------------------


def test_structured_output_is_not_dropped() -> None:
    """远端按 outputSchema 返回结构化输出时，内容不得变成空字符串。

    空字符串与「工具确实没有输出」无法区分——模型会误判工具行为。
    """

    async def on_list_tools(context: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name="structured",
                    description="结构化输出",
                    inputSchema={"type": "object"},
                    outputSchema={
                        "type": "object",
                        "properties": {"answer": {"type": "number"}},
                        "required": ["answer"],
                    },
                )
            ]
        )

    async def on_call_tool(
        context: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        return types.CallToolResult(content=[], structuredContent={"answer": 42})

    server = Server("structured", on_list_tools=on_list_tools, on_call_tool=on_call_tool)

    async def _run() -> str:
        async with _client_session(server) as session:
            tools = await discover_mcp_tools(session)
            return await tools[0].execute(ToolContext(caller="test"), {})  # type: ignore[arg-type]

    text = asyncio.run(_run())

    assert text != ""
    assert "42" in text


def test_non_text_content_block_is_not_dropped() -> None:
    """远端返回图片等非文本内容块时，内容不得变成空字符串。"""

    async def on_list_tools(context: Any, params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[types.Tool(name="image", description="图片", inputSchema={"type": "object"})]
        )

    async def on_call_tool(
        context: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        return types.CallToolResult(
            content=[types.ImageContent(type="image", data="AAAA", mimeType="image/png")]
        )

    server = Server("image", on_list_tools=on_list_tools, on_call_tool=on_call_tool)

    async def _run() -> str:
        async with _client_session(server) as session:
            tools = await discover_mcp_tools(session)
            return await tools[0].execute(ToolContext(caller="test"), {})  # type: ignore[arg-type]

    text = asyncio.run(_run())

    assert text != ""
    assert "image" in text


def test_text_and_structured_output_are_both_kept() -> None:
    """文本与结构化输出同时存在时，两者都要保留。"""
    result = types.CallToolResult(
        content=[types.TextContent(type="text", text="结论:42")],
        structuredContent={"answer": 42},
    )

    text = extract_text_content(result)

    assert "结论:42" in text
    # 结构化部分必须以 JSON 形态出现，而不只是恰好包含数字 42
    assert '"answer": 42' in text


def test_extraction_never_silently_loses_content() -> None:
    """提取结果的每一部分都能在原始内容里找到出处。"""
    result = types.CallToolResult(
        content=[
            types.TextContent(type="text", text="A"),
            types.AudioContent(type="audio", data="BBB", mimeType="audio/wav"),
            types.ResourceLink(type="resource_link", uri="file:///notes.md", name="notes"),
        ],
        structuredContent={"k": "v"},
    )

    text = extract_text_content(result)

    assert "A" in text
    assert "audio" in text
    assert "notes" in text
    assert '"k"' in text


def test_discovery_preserves_remote_output_contract() -> None:
    """发现阶段不得丢弃远端的结构化输出契约与元数据。"""
    mcp_tool = types.Tool(
        name="search",
        title="检索资料",
        description="检索",
        inputSchema={"type": "object"},
        outputSchema={"type": "object", "properties": {"hits": {"type": "array"}}},
        annotations=types.ToolAnnotations(readOnlyHint=True),
    )

    definition = from_mcp_tool(mcp_tool)

    assert definition.metadata.get("output_schema") == mcp_tool.output_schema
    assert definition.metadata.get("title") == "检索资料"
    assert definition.metadata.get("annotations") is not None


# --------------------------------------------------------------------------
# 发现机制：分页不得被静默截断
# --------------------------------------------------------------------------


def _paged_server(pages: list[list[types.Tool]]) -> Server:
    """构造一个按页返回工具的 MCP Server。"""

    async def on_list_tools(context: Any, params: Any) -> types.ListToolsResult:
        cursor = getattr(params, "cursor", None) if params is not None else None
        index = 0 if cursor is None else int(cursor)
        page = pages[index]
        next_cursor = str(index + 1) if index + 1 < len(pages) else None
        return types.ListToolsResult(tools=page, nextCursor=next_cursor)

    return Server("paged", on_list_tools=on_list_tools)


def test_discovery_follows_pagination_cursor() -> None:
    """远端分页时，发现机制必须翻完所有页，不能只取第一页。"""
    first = [
        types.Tool(name=f"a{i}", description="d", inputSchema={"type": "object"}) for i in range(3)
    ]
    second = [
        types.Tool(name=f"b{i}", description="d", inputSchema={"type": "object"}) for i in range(3)
    ]
    server = _paged_server([first, second])

    async def _run() -> list[Any]:
        async with _client_session(server) as session:
            return await discover_mcp_tools(session)

    tools = asyncio.run(_run())

    assert len(tools) == 6
    assert {t.definition.name for t in tools} == {f"a{i}" for i in range(3)} | {
        f"b{i}" for i in range(3)
    }


def test_discovery_stops_on_repeating_cursor() -> None:
    """远端若反复返回同一个游标，发现机制不得无限循环。"""

    async def on_list_tools(context: Any, params: Any) -> types.ListToolsResult:
        # 病态服务端：永远返回同一个游标
        return types.ListToolsResult(
            tools=[types.Tool(name="t", description="d", inputSchema={"type": "object"})],
            nextCursor="same",
        )

    server = Server("looping", on_list_tools=on_list_tools)

    async def _run() -> list[Any]:
        async with _client_session(server) as session:
            return await discover_mcp_tools(session, max_pages=3)

    tools = asyncio.run(_run())

    # 到达页数上限即停止，而不是挂死
    assert len(tools) == 3


# --------------------------------------------------------------------------
# 连接建立：握手超时与可读错误
# --------------------------------------------------------------------------


def test_stdio_session_raises_readable_error_when_server_exits() -> None:
    """远端进程立即退出时，错误必须可读，不能只抛 ExceptionGroup。"""

    async def _run() -> None:
        async with stdio_session("true") as session:  # type: ignore[arg-type]
            await session.initialize()

    with pytest.raises(MCPConnectionError) as exc_info:
        asyncio.run(_run())

    message = str(exc_info.value)
    # 必须包含真正的失败原因，而不是 "unhandled errors in a TaskGroup"
    assert "TaskGroup" not in message
    assert "true" in message


def test_stdio_session_raises_readable_error_when_command_missing() -> None:
    """启动命令不存在时，错误必须指出是哪个命令找不到。"""

    async def _run() -> None:
        async with stdio_session("no-such-mcp-binary-xyz") as session:  # type: ignore[arg-type]
            await session.initialize()

    with pytest.raises(MCPConnectionError) as exc_info:
        asyncio.run(_run())

    message = str(exc_info.value)
    assert "no-such-mcp-binary-xyz" in message
    assert "TaskGroup" not in message


def test_stdio_session_handshake_times_out() -> None:
    """远端无响应时，握手必须超时退出，不能无限期挂起。"""

    async def _run() -> None:
        async with stdio_session("sleep", ["30"], timeout=1.0) as session:  # type: ignore[arg-type]
            await session.initialize()

    started = time.monotonic()
    with pytest.raises(MCPConnectionError) as exc_info:
        asyncio.run(_run())
    elapsed = time.monotonic() - started

    assert "超时" in str(exc_info.value)
    # 必须真的超时返回，而不是等满 30 秒
    assert elapsed < 10


def test_caller_exception_after_handshake_is_not_rewritten() -> None:
    """握手完成后，调用方代码的异常不得被改写成连接错误。"""
    import os
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    script = root / "examples" / "mcp_server.py"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")

    async def _run() -> None:
        async with stdio_session(sys.executable, [str(script)], env=env):
            raise ValueError("调用方自己的 bug")

    with pytest.raises(ValueError, match="调用方自己的 bug"):
        asyncio.run(_run())


def test_keyboard_interrupt_is_not_rewritten() -> None:
    """中断信号必须原样透传，不能被改写成连接错误。"""
    import os
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    script = root / "examples" / "mcp_server.py"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")

    async def _run() -> None:
        async with stdio_session(sys.executable, [str(script)], env=env):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(_run())


def test_closed_session_gives_clear_error() -> None:
    """会话关闭后调用远端工具，错误必须指出是会话问题。

    远端工具的生命周期绑定在会话上。SDK 原始报错是 "Connection closed"，
    容易被误读成「远端服务坏了」——适配器必须把它翻译成明确的会话问题。
    """
    server = build_mcp_server(_registry())

    async def _run() -> str:
        async with _client_session(server) as session:
            tools = await discover_mcp_tools(session)
        tool = tools[0]
        with pytest.raises(RuntimeError) as exc_info:
            await tool.execute(ToolContext(caller="test"), {"text": "x"})
        return str(exc_info.value)

    message = asyncio.run(_run())

    assert "会话已关闭" in message
    assert "Connection closed" not in message


def test_other_mcp_errors_are_not_misreported_as_closed_session() -> None:
    """非「连接关闭」的 MCP 错误必须原样抛出，不能被误报成会话问题。"""
    server = build_mcp_server(_registry())

    async def _run() -> str:
        async with _client_session(server) as session:
            ghost = MCPTool(
                types.Tool(name="ghost", description="远端不存在", inputSchema={"type": "object"}),
                session,
            )
            with pytest.raises(MCPError) as exc_info:
                await ghost.execute(ToolContext(caller="test"), {})
            return str(exc_info.value)

    message = asyncio.run(_run())

    assert "会话已关闭" not in message
    assert "ghost" in message


def test_call_unknown_tool_is_protocol_error() -> None:
    """工具不存在是协议级错误，不是工具执行错误。

    MCP 区分两类失败：协议错误（JSON-RPC error，客户端无法通过调整参数修复）
    与工具执行错误（is_error 结果，模型可以读懂并调整）。未知工具属于前者。
    """
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
