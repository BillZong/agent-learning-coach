"""Agent Loop + MCP 工具：端到端示例。

演示的核心命题：**换工具来源不改循环**。

同一个 ``AgentRuntime``，工具既可以来自本地注册表，也可以来自远端 MCP Server
——循环不知道也不需要知道工具从哪来：它只把 ``registry.specs()`` 发给模型，
再把模型请求的工具调用交给 ``ToolDispatcher``。

本示例同时注册两类工具：

- **远端 MCP 工具**：由 ``examples/mcp_server.py`` 以子进程方式提供，经
  ``discover_mcp_tools`` 发现后接入本地注册表；
- **本地工具**：直接写在本地。

两者走**同一个** ``ToolDispatcher``，因此参数校验、超时与结果归一化的行为
完全一致。

运行方式::

    uv run python examples/agent_loop_with_mcp.py

默认使用脚本化的模型客户端（确定性、无需凭证、不消耗额度）。想接真实模型
时，把 ``_build_client()`` 换成::

    from agent_learning_coach.core.llm import create_client
    return create_client("minimax-cn")  # 模型用注册表的默认值——写死会随厂商下线而过期

模型侧只需把请求的 ``ToolCall.name`` 换成远端工具的真实名称即可，其余不变。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agent_learning_coach.core.agent import AgentConfig, AgentRuntime  # noqa: E402
from agent_learning_coach.core.llm.mock import MockLLMClient  # noqa: E402
from agent_learning_coach.core.llm.models import LLMRequest, LLMResponse  # noqa: E402
from agent_learning_coach.core.llm.types import Message, ToolCall  # noqa: E402
from agent_learning_coach.core.tools import (  # noqa: E402
    FunctionTool,
    ToolContext,
    ToolDefinition,
    ToolDispatcher,
    ToolRegistry,
)
from agent_learning_coach.core.tools.mcp import (  # noqa: E402
    discover_mcp_tools,
    stdio_session,
)

SERVER_SCRIPT = ROOT / "examples" / "mcp_server.py"


async def _local_explain(context: ToolContext, arguments: dict[str, Any]) -> str:
    """本地工具：用一句话解释概念。

    它与远端 MCP 工具在注册表里平级，模型不知道也分不出来。
    """
    return f"「{arguments.get('term', '')}」是一个需要结合例子理解的概念"


def build_local_registry() -> ToolRegistry:
    """构造只含本地工具的注册表。"""
    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="explain_concept",
                description="用一句话解释某个概念",
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


def build_client() -> Any:
    """构造模型客户端。

    默认用脚本化的 Mock：先请求远端 MCP 工具，再根据工具结果给出总结。
    换成真实客户端时只需替换本函数。
    """
    script: list[LLMResponse] = [
        LLMResponse(
            contents=[
                ToolCall(
                    tool_call_id="call-1",
                    name="search_learning_materials",
                    arguments={"query": "闭包", "top_k": 2},
                )
            ]
        ),
        LLMResponse(
            contents=[
                ToolCall(
                    tool_call_id="call-2",
                    name="explain_concept",
                    arguments={"term": "闭包"},
                )
            ]
        ),
        LLMResponse(
            contents=[
                Message(
                    role="assistant",
                    content="闭包是一个函数加上它所引用的外部变量的组合。建议先理解「函数是一等公民」，再看闭包。",
                )
            ]
        ),
    ]
    index = 0

    def handler(request: LLMRequest) -> LLMResponse:
        nonlocal index
        response = script[min(index, len(script) - 1)]
        index += 1
        return response

    return MockLLMClient(handler=handler, model="scripted")


async def main() -> None:
    """跑一次「远端 MCP 工具 + 本地工具」混合的 Agent Loop。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")

    registry = build_local_registry()

    print("=== 1. 启动远端 MCP Server 并发现工具 ===")
    async with stdio_session(sys.executable, [str(SERVER_SCRIPT)], env=env) as session:
        remote_tools = await discover_mcp_tools(session)
        for tool in remote_tools:
            print(f"  [远端] {tool.definition.name}: {tool.definition.description}")

        print("=== 2. 把远端工具接入本地注册表 ===")
        for tool in remote_tools:
            registry.register(tool)
        print(f"  注册表现有工具: {registry.names()}")

        print("=== 3. 运行 Agent Loop ===")
        client = build_client()
        loop = AgentRuntime(
            client,
            ToolDispatcher(registry),
            config=AgentConfig(instructions=("你是学习教练。",), max_steps=6),
        )
        run = await loop.run("闭包是什么？")

        print("=== 4. 运行结果 ===")
        print(f"  停止原因: {run.stop_reason}")
        print(f"  工具调用数: {run.tool_call_count}")
        for step in run.steps:
            for outcome in step.outcomes:
                print(f"  - {outcome.tool_name} [{outcome.status}]: {outcome.content}")
        print(f"  最终回答: {run.final_text}")

        assert run.stop_reason == "completed"
        assert run.tool_call_count == 2
        print("\n[PASS] 同一个循环驱动了远端 MCP 工具与本地工具")


if __name__ == "__main__":
    asyncio.run(main())
