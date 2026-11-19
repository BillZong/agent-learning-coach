"""基础 Agent Runtime：Execution Context、Runtime State 与 Cost Control。

这是 Agent Runtime 层的**基础交付物**——演示运行时如何承载状态、如何控制
成本::

    uv run python examples/agent_runtime.py

与 ``examples/basic_react_agent.py`` 的区别：那个演示**循环怎么跑**，这个
演示**运行时的状态与预算**。

三件事：

1. **Execution Context**：事件历史、步数、用量、成本都记在上下文里。传入
   已有上下文可以续接对话。
2. **Runtime State**：草稿本。工具与应用层可见，**模型看不到**。
3. **Cost Control**：token 预算与成本预算。价格表由调用方提供——价格会变，
   运行时内置一份只会制造一个必然过期的真相来源。

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

from pydantic import BaseModel, Field  # noqa: E402

from agent_learning_coach.core.agent import (  # noqa: E402
    AgentConfig,
    AgentRuntime,
    ExecutionContext,
    TokenPrice,
)
from agent_learning_coach.core.llm.mock import MockLLMClient  # noqa: E402
from agent_learning_coach.core.llm.models import (  # noqa: E402
    LLMRequest,
    LLMResponse,
    LLMUsage,
)
from agent_learning_coach.core.llm.types import Message, ToolCall  # noqa: E402
from agent_learning_coach.core.tools import (  # noqa: E402
    FunctionTool,
    ToolContext,
    ToolDefinition,
    ToolDispatcher,
    ToolRegistry,
)


class StudyStep(BaseModel):
    """学习步骤。"""

    title: str = Field(description="步骤标题")
    minutes: int = Field(description="预计分钟数")


class LearningPlan(BaseModel):
    """结构化输出：学习计划。"""

    topic: str = Field(description="学习主题")
    steps: list[StudyStep] = Field(description="学习步骤")


# 价格表由调用方提供（单位：每千 token）
PRICES = {
    "mock-model": TokenPrice(input_per_1k=0.01, output_per_1k=0.02),
}


async def _search(context: ToolContext, arguments: dict[str, Any]) -> str:
    """检索学习资料，并把检索词记进运行时草稿本。"""
    query = arguments.get("query", "")
    execution = context.metadata.get("execution_context")
    if execution is not None:
        execution.state["searched"] = execution.state.get("searched", []) + [query]
    return f"关于「{query}」的资料：闭包 = 函数 + 它引用的外部变量"


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
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            ),
            func=_search,
        )
    )
    return registry


def _build_client() -> Any:
    """构造模型客户端：脚本化的两轮。

    注意：响应里要带上 ``usage``——真实客户端都会上报 token 用量，成本核算
    依赖它。少了它，用量与成本都是 0。
    """
    script: list[LLMResponse] = [
        LLMResponse(
            model="mock-model",
            contents=[
                ToolCall(
                    tool_call_id="call-1",
                    name="search_materials",
                    arguments={"query": "闭包"},
                )
            ],
            usage=LLMUsage(input_tokens=120, output_tokens=30),
        ),
        LLMResponse(
            model="mock-model",
            contents=[Message(role="assistant", content="闭包是函数连同它引用的外部变量。")],
        ),
    ]
    index = 0

    def handler(request: LLMRequest) -> LLMResponse:
        nonlocal index
        response = script[min(index, len(script) - 1)]
        index += 1
        return response

    return MockLLMClient(handler=handler, model="mock-model")


def _build_structured_client() -> Any:
    """构造提交结构化答案的模型客户端。"""
    script: list[LLMResponse] = [
        LLMResponse(
            model="mock-model",
            contents=[
                ToolCall(
                    tool_call_id="call-1",
                    name="final_answer",
                    arguments={
                        "output": {
                            "topic": "闭包",
                            "steps": [
                                {"title": "理解词法作用域", "minutes": 20},
                                {"title": "读懂闭包的概念", "minutes": 30},
                            ],
                        }
                    },
                )
            ],
            usage=LLMUsage(input_tokens=150, output_tokens=60),
        ),
    ]
    index = 0

    def handler(request: LLMRequest) -> LLMResponse:
        nonlocal index
        response = script[min(index, len(script) - 1)]
        index += 1
        return response

    return MockLLMClient(handler=handler, model="mock-model")


async def main() -> None:
    """跑一次带预算控制的 Agent 运行。"""
    runtime = AgentRuntime(
        _build_client(),
        ToolDispatcher(build_registry()),
        config=AgentConfig(
            model="mock-model",
            instructions=("你是学习教练。",),
            max_steps=6,
            max_total_tokens=1000,
            max_cost=1.0,
            prices=PRICES,
        ),
    )

    # --- 1. Execution Context：状态容器 ---
    context = ExecutionContext(execution_id="demo-run-1")
    context.state["learner_level"] = "初级"
    print(f"执行标识: {context.execution_id}")
    print(f"预置草稿本: {context.state}")

    print("\n=== 运行 ===")
    result = await runtime.run("闭包是什么？", context=context)

    print("\n=== 执行历史 ===")
    for event in result.context.events:
        print(f"  [{event.author}] {len(event.contents)} 条内容")

    print("\n=== 运行时状态 ===")
    print(f"  步数: {result.context.current_step}")
    print(f"  草稿本: {result.context.state}")
    print(f"  用量: input={result.usage.input_tokens} output={result.usage.output_tokens}")
    print(f"  成本: {result.cost:.6f}")

    print("\n=== 结果 ===")
    print(f"  状态: {result.status}")
    print(f"  停止原因: {result.stop_reason}")
    print(f"  输出: {result.output}")

    assert result.status == "complete"
    assert result.context.state["searched"] == ["闭包"]
    assert result.cost > 0
    print("\n[PASS] 执行上下文承载了历史、草稿本、用量与成本")

    # --- 4. 结构化输出：最终答案走 final_answer 工具 ---
    print("\n=== 结构化输出 ===")
    structured_runtime = AgentRuntime(
        _build_structured_client(),
        ToolDispatcher(build_registry()),
        config=AgentConfig(
            model="mock-model",
            max_steps=4,
            output_model=LearningPlan,
        ),
    )
    structured = await structured_runtime.run("安排闭包的学习计划")

    assert isinstance(structured.output, LearningPlan)
    print(f"  输出类型: {type(structured.output).__name__}")
    print(f"  主题: {structured.output.topic}")
    for step in structured.output.steps:
        print(f"    - {step.title}（{step.minutes} 分钟）")
    print("\n[PASS] 最终答案是已校验的模型实例，不是文本")


if __name__ == "__main__":
    asyncio.run(main())
