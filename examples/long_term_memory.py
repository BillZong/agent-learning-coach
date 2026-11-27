"""长期记忆示例。

演示三件事：

1. **什么值得记**——只有同时满足「关于学习者」「下次还成立」「能归入一个类别」
   的信息才进长期记忆。
2. **噪声的三道闸**——抽取提示词要求「没有就返回空列表」；准入策略确定性过滤；
   去重与覆盖由代码决定。
3. **覆盖而非并存**——水平从初级变成中级，是同一条记忆被更新，不是多了一条
   互相矛盾的记忆。

运行::

    uv run python examples/long_term_memory.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_learning_coach.core.llm.mock import MockLLMClient  # noqa: E402
from agent_learning_coach.core.llm.types import Message, ToolCall, ToolResult  # noqa: E402
from agent_learning_coach.core.memory import (  # noqa: E402
    InMemoryMemoryStore,
    LLMMemoryExtractor,
    MemoryManager,
)

LEARNER = "learner-1"


def _client(memories: list[dict]) -> MockLLMClient:
    """构造一个返回指定抽取结果的模型客户端。"""
    return MockLLMClient(
        text=json.dumps({"memories": memories}, ensure_ascii=False),
    )


def _transcript() -> list:
    """构造一段学习者与教练的对话。"""
    return [
        Message(role="user", content="我是初学者，想学 Agent Engineering"),
        ToolCall(tool_call_id="c1", name="read_notes", arguments={"path": "a.md"}),
        ToolResult(tool_call_id="c1", name="read_notes", content="很长的笔记内容"),
        Message(role="assistant", content="好的，我们从基础开始。"),
        Message(role="user", content="闭包我一直搞不懂，另外我喜欢先看代码再读原理"),
    ]


async def main() -> None:
    """跑一次长期记忆的写入与召回。"""
    store = InMemoryMemoryStore()

    print("=== 第一轮：抽取并写入 ===")
    manager = MemoryManager(
        store,
        LLMMemoryExtractor(
            _client(
                [
                    {
                        "memory_type": "semantic",
                        "kind": "goal",
                        "scope": "agent",
                        "content": "学 Agent Engineering",
                        "confidence": 0.95,
                    },
                    {
                        "memory_type": "semantic",
                        "kind": "level",
                        "scope": "",
                        "content": "初学者",
                        "confidence": 0.9,
                    },
                    {
                        "memory_type": "semantic",
                        "kind": "weakness",
                        "scope": "闭包",
                        "content": "闭包掌握不好",
                        "confidence": 0.85,
                    },
                    {
                        "memory_type": "semantic",
                        "kind": "preference",
                        "scope": "",
                        "content": "喜欢先看代码再读原理",
                        "confidence": 0.9,
                    },
                    # 噪声：模型自己发明的类别
                    {
                        "memory_type": "semantic",
                        "kind": "astrology",
                        "scope": "",
                        "content": "他是双子座",
                        "confidence": 0.8,
                    },
                    # 噪声：置信度不够
                    {
                        "memory_type": "semantic",
                        "kind": "weakness",
                        "scope": "装饰器",
                        "content": "可能是装饰器的问题",
                        "confidence": 0.2,
                    },
                ]
            )
        ),
    )

    written = await manager.capture(_transcript(), subject=LEARNER)
    print(f"  写入 {len(written)} 条：")
    for memory in written:
        print(f"    - [{memory.kind}] {memory.content}")

    assert len(written) == 4, "噪声应当被准入策略挡掉"

    print("\n=== 画像 ===")
    profile = await manager.profile(subject=LEARNER)
    print(profile.to_prompt())

    print("=== 第二轮：水平变了 ===")
    manager.extractor = LLMMemoryExtractor(
        _client(
            [
                {
                    "memory_type": "semantic",
                    "kind": "level",
                    "scope": "",
                    "content": "中级",
                    "confidence": 0.9,
                },
            ]
        )
    )
    await manager.capture(_transcript(), subject=LEARNER)

    levels = [m for m in await store.find(subject=LEARNER) if m.kind == "level"]
    print(f"  水平记忆条数: {len(levels)}（同键覆盖，不并存）")
    print(f"  当前水平: {levels[0].content}")
    assert len(levels) == 1
    assert levels[0].content == "中级"

    print("\n=== 召回 ===")
    related = await manager.recall("闭包", subject=LEARNER)
    for memory in related:
        print(f"  - [{memory.kind}] {memory.content}")
    assert related and "闭包" in related[0].content

    unrelated = await manager.recall("量子力学", subject=LEARNER)
    print(f"  无关查询召回: {unrelated}（不召回噪声）")
    assert unrelated == []

    print("\n=== 集成：记忆进入模型的上下文 ===")
    from agent_learning_coach.core.agent import AgentConfig, AgentRuntime
    from agent_learning_coach.core.llm.mock import MockLLMClient
    from agent_learning_coach.core.llm.models import LLMResponse
    from agent_learning_coach.core.llm.types import Message
    from agent_learning_coach.core.tools import ToolDispatcher, ToolRegistry

    context_text = await manager.build_context(subject=LEARNER)
    print("  注入的记忆上下文：")
    for line in context_text.splitlines():
        print(f"    {line}")

    seen: list = []

    def handler(request):
        """记录模型实际收到的请求。"""
        seen.append(request)
        return LLMResponse(
            model="mock-model",
            contents=[Message(role="assistant", content="好的")],
        )

    client = MockLLMClient(handler=handler, model="mock-model")
    runtime = AgentRuntime(
        client,
        ToolDispatcher(ToolRegistry()),
        config=AgentConfig(
            model="mock-model",
            instructions=["你是学习教练。", context_text],
        ),
    )
    await runtime.run("我该从哪开始？")

    injected = seen[0].instructions if seen else []
    print(f"  模型收到的指令条数: {len(injected)}")
    print(f"  其中包含记忆上下文: {any('闭包' in text for text in injected)}")
    assert any("闭包掌握不好" in text for text in injected), "记忆没有进入模型请求"

    print("\n[PASS] 噪声被挡住，新事实覆盖旧事实，召回不返回无关记忆，记忆进入模型上下文")


if __name__ == "__main__":
    asyncio.run(main())
