"""Agent Loop 与 MCP 工具的集成测试。

验证的核心命题：**换工具来源不改循环**。

同一个 ``AgentRuntime``，工具既可以来自本地注册表，也可以来自远端 MCP Server；
两者都经由同一个 ``ToolDispatcher`` 执行，参数校验、超时与结果归一化的行为
完全一致。

测试走真实 MCP 协议（内存传输），不做协议层模拟。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from agent_learning_coach.core.agent import AgentRuntime
from agent_learning_coach.core.llm.types import ToolResult
from agent_learning_coach.core.tools import (
    FunctionTool,
    ToolContext,
    ToolDefinition,
    ToolDispatcher,
    ToolRegistry,
)
from agent_learning_coach.core.tools.mcp import build_mcp_server, discover_mcp_tools
from support import (
    ScriptedClient,
    mcp_client_session,
    text_response,
    tool_call_response,
)

ROOT = Path(__file__).resolve().parent.parent


async def _remote_search(context: ToolContext, arguments: dict[str, Any]) -> str:
    """远端工具：检索资料。"""
    return f"远端检索 {arguments.get('query')!r} 得到 2 条资料"


async def _remote_explode(context: ToolContext, arguments: dict[str, Any]) -> str:
    """远端工具：总是失败。"""
    raise RuntimeError("远端服务不可用")


async def _local_explain(context: ToolContext, arguments: dict[str, Any]) -> str:
    """本地工具：解释概念。"""
    return f"本地解释 {arguments.get('term')!r}"


def build_remote_server() -> Any:
    """构造提供远端工具的 MCP Server。"""
    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="remote_search",
                description="远端检索资料",
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            ),
            func=_remote_search,
        )
    )
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="remote_explode",
                description="远端总是失败",
                parameters={"type": "object"},
            ),
            func=_remote_explode,
        )
    )
    return build_mcp_server(registry)


def build_local_registry() -> ToolRegistry:
    """构造只含本地工具的注册表。"""
    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="local_explain",
                description="本地解释概念",
                parameters={
                    "type": "object",
                    "properties": {"term": {"type": "string"}},
                    "required": ["term"],
                },
            ),
            func=_local_explain,
        )
    )
    return registry


@asynccontextmanager
async def registry_with_remote_tools(registry: ToolRegistry) -> AsyncIterator[ToolRegistry]:
    """发现远端工具并接入注册表。

    远端工具的生命周期**绑定在会话上**，因此会话必须在整个使用期间保持
    打开——这正是这个上下文管理器存在的理由。
    """
    async with mcp_client_session(build_remote_server()) as session:
        tools = await discover_mcp_tools(session)
        for tool in tools:
            registry.register(tool)
        yield registry


def test_remote_tool_is_called_through_the_same_loop() -> None:
    """远端 MCP 工具接入后，循环能像调用本地工具一样调用它。"""
    registry = build_local_registry()
    client = ScriptedClient(
        [
            tool_call_response("remote_search", {"query": "闭包"}),
            text_response("根据远端检索结果……"),
        ]
    )

    async def _run() -> Any:
        async with registry_with_remote_tools(registry):
            loop = AgentRuntime(client, ToolDispatcher(registry))
            return await loop.run("闭包是什么")

    run = asyncio.run(_run())

    assert run.stop_reason == "completed"
    assert run.tool_call_count == 1

    results = [item for item in run.context.contents if isinstance(item, ToolResult)]
    assert len(results) == 1
    assert results[0].status == "success"
    assert "远端检索" in results[0].content


def test_remote_and_local_tools_coexist_in_one_registry() -> None:
    """远端与本地工具平级共存，由同一个调度器执行。"""
    registry = build_local_registry()
    client = ScriptedClient(
        [
            tool_call_response("remote_search", {"query": "闭包"}, call_id="c1"),
            tool_call_response("local_explain", {"term": "闭包"}, call_id="c2"),
            text_response("都调用完了"),
        ]
    )

    async def _run() -> Any:
        async with registry_with_remote_tools(registry):
            loop = AgentRuntime(client, ToolDispatcher(registry))
            return await loop.run("闭包是什么")

    run = asyncio.run(_run())

    assert run.stop_reason == "completed"
    assert run.tool_call_count == 2
    names = {result.name for result in run.context.contents if isinstance(result, ToolResult)}
    assert names == {"remote_search", "local_explain"}


def test_model_sees_both_remote_and_local_tools() -> None:
    """模型拿到的工具清单必须同时包含远端与本地工具。"""
    registry = build_local_registry()
    client = ScriptedClient([text_response()])

    async def _run() -> Any:
        async with registry_with_remote_tools(registry):
            loop = AgentRuntime(client, ToolDispatcher(registry))
            await loop.run("测试")
        return client

    client = asyncio.run(_run())

    assert {spec.name for spec in client.requests[0].tools} == {
        "local_explain",
        "remote_search",
        "remote_explode",
    }


def test_remote_failure_is_fed_back_and_loop_continues() -> None:
    """远端工具失败时，错误回填给模型，循环继续。"""
    registry = build_local_registry()
    client = ScriptedClient(
        [
            tool_call_response("remote_explode", {}),
            text_response("远端挂了，我换个方式回答"),
        ]
    )

    async def _run() -> Any:
        async with registry_with_remote_tools(registry):
            loop = AgentRuntime(client, ToolDispatcher(registry))
            return await loop.run("测试")

    run = asyncio.run(_run())

    assert run.stop_reason == "completed"
    results = [item for item in run.context.contents if isinstance(item, ToolResult)]
    assert results[0].status == "error"
    assert "远端服务不可用" in results[0].content


def test_remote_tool_arguments_are_validated_before_the_call() -> None:
    """远端工具的参数也要先经本地校验，非法参数不打到远端。"""
    registry = build_local_registry()
    client = ScriptedClient(
        [
            tool_call_response("remote_search", {"wrong": 1}),
            text_response("我改一下参数"),
        ]
    )

    async def _run() -> Any:
        async with registry_with_remote_tools(registry):
            loop = AgentRuntime(client, ToolDispatcher(registry))
            return await loop.run("测试")

    run = asyncio.run(_run())

    assert run.stop_reason == "completed"
    results = [item for item in run.context.contents if isinstance(item, ToolResult)]
    assert results[0].status == "error"
    assert "远端检索" not in results[0].content


def test_example_script_runs_end_to_end() -> None:
    """示例脚本必须能真实跑通——它是交付物，不是摆设。

    这里走真实 stdio 子进程：启动示例 MCP Server、发现工具、跑完循环。
    """
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [sys.executable, str(ROOT / "examples" / "agent_loop_with_mcp.py")],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        cwd=str(ROOT),
    )

    assert result.returncode == 0, result.stderr
    assert "停止原因: completed" in result.stdout
    assert "search_learning_materials [success]" in result.stdout
    assert "explain_concept [success]" in result.stdout


def test_example_uses_same_loop_for_both_tool_sources() -> None:
    """示例必须证明「同一个循环驱动两类工具」，而不是跑两套逻辑。"""
    source = (ROOT / "examples" / "agent_loop_with_mcp.py").read_text(encoding="utf-8")

    # 只构造一次 AgentRuntime
    assert source.count("AgentRuntime(") == 1
    # 两类工具进同一个注册表
    assert "registry.register(tool)" in source
    assert source.count("registry.register(") == 2
