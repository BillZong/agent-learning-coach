"""上下文预算示例。

演示三件事：

1. **存储与呈现分离**——执行上下文里的历史始终完整，发给模型的是按预算
   裁剪后的视图。
2. **预算是被执行的**——裁剪后确实装得下，而不是「裁了一刀就不管了」。
3. **裁剪过程可观测**——每次请求的 token 数、丢弃条目数、应用了哪些策略，
   都在 ``ContextView`` 里。

运行::

    uv run python examples/context_budget.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_learning_coach.core.agent import AgentConfig, AgentRuntime  # noqa: E402
from agent_learning_coach.core.context import (  # noqa: E402
    Compaction,
    CompactionRules,
    ContextBuilder,
    SlidingWindow,
    TokenCounter,
)
from agent_learning_coach.core.llm.mock import MockLLMClient  # noqa: E402
from agent_learning_coach.core.llm.models import LLMRequest, LLMResponse, LLMUsage  # noqa: E402
from agent_learning_coach.core.llm.types import Message, ToolCall, ToolResult  # noqa: E402
from agent_learning_coach.core.tools import (  # noqa: E402
    FunctionTool,
    ToolDefinition,
    ToolDispatcher,
    ToolRegistry,
)

BUDGET = 400
"""单次请求的上下文预算（token）。"""


async def _read_notes(context: object, arguments: dict) -> str:
    """模拟一次很长的工具结果——这是上下文体积的大头。"""
    return "闭包的定义与用法……" * 200


def _build_registry() -> ToolRegistry:
    """构造工具注册表。"""
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
            ),
            func=_read_notes,
        )
    )
    return registry


def _build_builder() -> ContextBuilder:
    """构造上下文构建器。

    先压缩（保住更多条目），再滑窗（丢掉更老的条目）。
    """
    return ContextBuilder(
        max_tokens=BUDGET,
        strategies=(
            Compaction(
                CompactionRules(
                    max_result_chars=200,
                    result_templates={"read_notes": "已读取 {path}，需要时重新读取。"},
                )
            ),
            SlidingWindow(keep_recent=6),
        ),
        counter=TokenCounter(model="gpt-4"),
    )


async def main() -> None:
    """跑一次会被上下文预算裁剪的运行。"""
    registry = _build_registry()
    builder = _build_builder()
    seen: list[LLMRequest] = []

    def handler(request: LLMRequest) -> LLMResponse:
        """记录请求，并让模型先调一次工具。"""
        seen.append(request)
        if len(seen) == 1:
            return LLMResponse(
                model="gpt-4",
                contents=[
                    ToolCall(
                        tool_call_id="call-1",
                        name="read_notes",
                        arguments={"path": "notes/闭包.md"},
                    )
                ],
                usage=LLMUsage(input_tokens=20, output_tokens=10),
            )
        return LLMResponse(
            model="gpt-4",
            contents=[Message(role="assistant", content="闭包是函数连同它引用的外部变量。")],
            usage=LLMUsage(input_tokens=20, output_tokens=10),
        )

    runtime = AgentRuntime(
        MockLLMClient(handler=handler, model="gpt-4"),
        ToolDispatcher(registry),
        config=AgentConfig(model="gpt-4", max_steps=4, context_builder=builder),
    )

    result = await runtime.run("帮我学闭包")

    print("=== 结果 ===")
    print(f"  状态: {result.status}")
    print(f"  回答: {result.output}")

    print("\n=== 每次请求的上下文 ===")
    for index, request in enumerate(seen):
        print(f"  第 {index + 1} 次：{len(request.contents)} 条内容")

    view = result.context.last_context_view
    if view is not None:
        print("\n=== 裁剪统计 ===")
        print(f"  原始 token: {view.original_tokens}")
        print(f"  最终 token: {view.final_tokens}（预算 {BUDGET}）")
        print(f"  节省: {view.saved_tokens}")
        print(f"  丢弃条目: {view.dropped_items}")
        print(f"  应用策略: {', '.join(view.applied)}")
        print(f"  发生截断: {view.truncated}")
        print(f"  仍然超预算: {view.over_budget}")

    print("\n=== 存储侧仍然完整 ===")
    print(f"  事件数: {len(result.context.events)}")
    print(f"  内容条数: {len(result.context.contents)}")
    for item in result.context.contents:
        if isinstance(item, ToolResult):
            print(f"  工具结果原文长度: {len(item.content)}（未被裁剪）")

    assert view is not None
    assert view.final_tokens <= BUDGET, "预算未被执行"
    assert len(result.context.contents) > len(view.contents), "存储侧应比呈现侧完整"
    print("\n[PASS] 预算是被执行的，且存储侧保持完整")


if __name__ == "__main__":
    asyncio.run(main())
