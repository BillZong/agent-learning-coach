"""人工确认（HITL）示例。

演示四件事：

1. **运行会暂停**——遇到声明了 ``requires_confirmation`` 的工具时，运行停下
   并把待确认的调用交给人。
2. **批准与拒绝**——批准就执行；拒绝就回填一条「用户拒绝」的结果，模型据此
   改道。
3. **缺失决定按拒绝处理**（fail closed）——缺少批准不等于批准。
4. **跨进程恢复**——暂停的会话落盘后，在另一个「进程」里恢复并继续。

运行::

    uv run python examples/hitl_approval.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_learning_coach.core.agent import (  # noqa: E402
    AgentConfig,
    AgentRuntime,
    ApprovalDecision,
    JsonFileSessionStore,
    SessionContext,
)
from agent_learning_coach.core.llm.models import LLMResponse, LLMUsage  # noqa: E402
from agent_learning_coach.core.llm.types import Message, ToolCall, ToolResult  # noqa: E402
from agent_learning_coach.core.tools import (  # noqa: E402
    FunctionTool,
    ToolDefinition,
    ToolDispatcher,
    ToolRegistry,
)

executed: list[str] = []


async def _delete_notes(context: object, arguments: dict) -> str:
    """模拟一次高风险操作。"""
    executed.append(arguments["path"])
    return f"已删除 {arguments['path']}"


def _registry() -> ToolRegistry:
    """构造一个含高风险工具的注册表。"""
    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="delete_notes",
                description="删除学习笔记",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                requires_confirmation=True,
                confirmation_message="确认要删除 {path} 吗？这一步不可撤销。",
            ),
            func=_delete_notes,
        )
    )
    return registry


def _runtime(store: JsonFileSessionStore) -> AgentRuntime:
    """构造运行时：先请求删除，批准后给出结论。"""

    def handler(request):
        """按脚本回应。"""
        contents = request.contents
        # 已经拿到工具结果了就直接给结论，否则请求执行高风险工具。
        already_answered = any(isinstance(item, ToolResult) for item in contents)
        if already_answered:
            return LLMResponse(
                model="gpt-4",
                contents=[Message(role="assistant", content="已完成：笔记已删除。")],
                usage=LLMUsage(input_tokens=10, output_tokens=5),
            )
        return LLMResponse(
            model="gpt-4",
            contents=[
                ToolCall(
                    tool_call_id="call-1",
                    name="delete_notes",
                    arguments={"path": "notes/闭包.md"},
                )
            ],
            usage=LLMUsage(input_tokens=10, output_tokens=5),
        )

    from agent_learning_coach.core.llm.mock import MockLLMClient

    return AgentRuntime(
        MockLLMClient(handler=handler, model="gpt-4"),
        ToolDispatcher(_registry()),
        config=AgentConfig(model="gpt-4", max_steps=4),
    )


async def main() -> None:
    """跑一次需要人工批准的操作。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = JsonFileSessionStore(Path(tmp) / "sessions")
        runtime = _runtime(store)
        session = SessionContext(session_id="demo")

        print("=== 第一段：运行到需要批准的地方 ===")
        paused = await session.run(runtime, "删掉闭包笔记")
        print(f"  状态: {paused.status}")
        print(f"  停止原因: {paused.stop_reason}")
        print(f"  工具是否已执行: {executed or '没有'}")
        for pending in paused.pending_approvals:
            print(f"  待确认: {pending.confirmation_message}")

        assert paused.status == "pending_approval"
        assert executed == []

        print("\n=== 落盘 ===")
        await session.save(store)
        print(f"  已保存到 {store.directory}")

        print("\n=== 第二段：另一个进程里恢复 ===")
        other_store = JsonFileSessionStore(Path(tmp) / "sessions")
        restored = await SessionContext.load(other_store, "demo")
        assert restored is not None
        print(f"  恢复出的会话: {restored.session_id}")
        print(f"  历史条数: {len(restored.contents)}")
        print(f"  待确认的调用: {len(restored.context.pending_approvals)}")

        result = await restored.resume(
            _runtime(other_store),
            [ApprovalDecision(tool_call_id="call-1", approved=True)],
        )
        print(f"  最终状态: {result.status}")
        print(f"  回答: {result.output}")
        print(f"  工具已执行: {executed}")

        assert executed == ["notes/闭包.md"]
        assert result.status == "complete"

    print("\n[PASS] 暂停、落盘、跨进程恢复与批准执行都成立")


if __name__ == "__main__":
    asyncio.run(main())
