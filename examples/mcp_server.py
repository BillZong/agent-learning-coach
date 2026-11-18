"""MCP Server 示例：把学习教练的工具暴露为 MCP Server。

运行方式（stdio 传输，MCP 客户端以子进程方式启动它）::

    uv run python examples/mcp_server.py

客户端连接示例::

    from agent_learning_coach.core.tools.mcp import stdio_session, discover_mcp_tools

    async with stdio_session("uv", ["run", "python", "examples/mcp_server.py"]) as session:
        tools = await discover_mcp_tools(session)

本示例刻意使用**低层** ``mcp`` SDK 而非 FastMCP：适配器应当直接说协议，
而不是说某个框架。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agent_learning_coach.core.tools import (  # noqa: E402
    FunctionTool,
    ToolContext,
    ToolDefinition,
    ToolRegistry,
)
from agent_learning_coach.core.tools.mcp import build_mcp_server, run_stdio_server  # noqa: E402


async def _search_learning_materials(context: ToolContext, arguments: dict[str, Any]) -> str:
    """检索学习资料。

    这是一个玩具实现：真实项目里它应当查询 RAG 索引或检索服务。
    """
    query = arguments.get("query", "")
    top_k = arguments.get("top_k", 3)
    return f"检索 {query!r} 得到 {top_k} 条学习资料（示例实现，未接入真实索引）"


async def _generate_exercise(context: ToolContext, arguments: dict[str, Any]) -> str:
    """生成一道练习题。

    这是一个玩具实现：真实项目里它应当调用 LLM 生成符合难度与知识点要求的题目。
    """
    topic = arguments.get("topic", "")
    difficulty = arguments.get("difficulty", "basic")
    return f"围绕 {topic!r} 生成一道 {difficulty} 难度的练习题（示例实现）"


async def _query_mastery(context: ToolContext, arguments: dict[str, Any]) -> str:
    """查询知识点的掌握度。

    这是一个玩具实现：真实项目里它应当查询掌握度模型。
    """
    point = arguments.get("knowledge_point", "")
    return f"知识点 {point!r} 的掌握度为 0.62（示例实现）"


def build_registry() -> ToolRegistry:
    """构造示例工具注册表。

    Returns:
        含三个示例工具的注册表。
    """
    registry = ToolRegistry()

    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="search_learning_materials",
                description="按关键词检索学习资料，返回最相关的若干条",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "检索关键词"},
                        "top_k": {"type": "integer", "description": "返回条数", "minimum": 1},
                    },
                    "required": ["query"],
                },
                timeout=10.0,
            ),
            func=_search_learning_materials,
        )
    )

    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="generate_exercise",
                description="围绕指定知识点生成一道练习题",
                parameters={
                    "type": "object",
                    "properties": {
                        "topic": {"type": "string", "description": "知识点"},
                        "difficulty": {
                            "type": "string",
                            "description": "难度",
                            "enum": ["basic", "intermediate", "advanced"],
                        },
                    },
                    "required": ["topic"],
                },
                timeout=30.0,
            ),
            func=_generate_exercise,
        )
    )

    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="query_mastery",
                description="查询某个知识点的掌握度",
                parameters={
                    "type": "object",
                    "properties": {
                        "knowledge_point": {"type": "string", "description": "知识点名称"},
                    },
                    "required": ["knowledge_point"],
                },
                timeout=5.0,
            ),
            func=_query_mastery,
        )
    )

    return registry


def main() -> None:
    """启动 MCP Server（stdio 传输）。"""
    registry = build_registry()
    server = build_mcp_server(registry, name="agent-learning-coach-tools")
    asyncio.run(run_stdio_server(server))


if __name__ == "__main__":
    main()
