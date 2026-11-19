"""Basic ReAct Agent：最小可运行的 ReAct 循环。

这是 Agent Loop 的**基础交付物**——不依赖 MCP、不起子进程、不需要凭证，
``uv run python examples/basic_react_agent.py`` 就能跑完一次完整的
「思考 -> 行动 -> 观察」循环。

与 ``examples/agent_loop_with_mcp.py`` 的区别只在于**工具从哪来**：

- 本示例：工具在本地注册表里；
- 另一个示例：工具来自远端 MCP Server。

循环本身完全一样——这正是「换工具来源不改循环」要证明的事。

模型侧用脚本化的 Mock 客户端，因此结果确定、可复现、不消耗额度。想接真实
模型时把 ``_build_client()`` 换成::

    from agent_learning_coach.core.llm import create_client
    return create_client("minimax-cn", model="MiniMax-M2")
"""

from __future__ import annotations

import asyncio
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


async def _search_materials(context: ToolContext, arguments: dict[str, Any]) -> str:
    """检索学习资料。"""
    query = arguments.get("query", "")
    return f"关于「{query}」的资料：闭包 = 函数 + 它引用的外部变量"


async def _explain(context: ToolContext, arguments: dict[str, Any]) -> str:
    """解释一个概念。"""
    term = arguments.get("term", "")
    return f"「{term}」的关键：先理解被引用的东西，再看函数怎么用它"


def build_registry() -> ToolRegistry:
    """构造本地工具注册表。"""
    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="search_materials",
                description="检索学习资料",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "检索关键词"},
                    },
                    "required": ["query"],
                },
            ),
            func=_search_materials,
        )
    )
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="explain_concept",
                description="解释一个概念",
                parameters={
                    "type": "object",
                    "properties": {
                        "term": {"type": "string", "description": "概念名"},
                    },
                    "required": ["term"],
                },
            ),
            func=_explain,
        )
    )
    return registry


def _build_client() -> Any:
    """构造模型客户端：脚本化的三步 ReAct。

    第 1 轮请求检索，第 2 轮请求解释，第 3 轮给出最终回答——正好覆盖
    「多轮工具调用」这条路径。
    """
    script: list[LLMResponse] = [
        LLMResponse(
            contents=[
                ToolCall(
                    tool_call_id="call-1",
                    name="search_materials",
                    arguments={"query": "闭包"},
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
                    content=(
                        "闭包是一个函数连同它所引用的外部变量的组合。"
                        "理解闭包的关键是分清「函数」与「函数用到的外部变量」。"
                    ),
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
    """跑一次完整的 ReAct 循环。"""
    registry = build_registry()
    loop = AgentRuntime(
        _build_client(),
        ToolDispatcher(registry),
        config=AgentConfig(
            instructions=("你是学习教练，回答要具体。",),
            max_steps=6,
        ),
    )

    print("=== 可用工具 ===")
    for spec in registry.specs():
        print(f"  {spec.name}: {spec.description}")

    print("\n=== 运行 ReAct 循环 ===")
    run = await loop.run("闭包是什么？")

    print("\n=== 逐步记录 ===")
    for step in run.steps:
        for call in step.tool_calls:
            print(f"  [Thought] 模型请求工具 {call.name}({call.arguments})")
        for outcome in step.outcomes:
            print(f"  [Action ] {outcome.tool_name} -> [{outcome.status}] {outcome.content}")

    print("\n=== 结果 ===")
    print(f"  停止原因: {run.stop_reason}")
    print(f"  步数: {len(run.steps)}")
    print(f"  工具调用数: {run.tool_call_count}")
    print(f"  最终回答: {run.final_text}")

    assert run.stop_reason == "completed"
    assert run.tool_call_count == 2
    assert len(run.steps) == 3
    print("\n[PASS] 多轮工具调用完成，思考/行动/观察三阶段均已执行")


if __name__ == "__main__":
    asyncio.run(main())
