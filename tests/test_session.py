"""会话上下文的测试。

**一次运行 ≠ 一次会话**。这里验证的是后者：学习者与教练之间的多轮对话
累积在同一份执行上下文里，模型在第二轮能看到第一轮的内容。

本阶段只做基础形态——内存里的会话状态。持久化、暂停恢复与人工确认属于
Day 09「Session 与 HITL」。
"""

from __future__ import annotations

import asyncio

from agent_learning_coach.core.agent import AgentConfig, AgentRuntime, SessionContext
from agent_learning_coach.core.llm.types import Message
from support import ScriptedClient, text_response


def _runtime(*responses: object) -> AgentRuntime:
    """构造一个只会说固定台词的运行时。"""
    from agent_learning_coach.core.tools import ToolDispatcher, ToolRegistry

    client = ScriptedClient(list(responses))
    return AgentRuntime(
        client,
        ToolDispatcher(ToolRegistry()),
        config=AgentConfig(model="gpt-4", max_steps=2),
    )


def test_session_has_stable_identity() -> None:
    """会话标识在多轮之间保持不变。"""
    session = SessionContext()

    assert session.session_id
    assert session.session_id == session.session_id


def test_session_ids_are_distinct() -> None:
    """不同会话的标识不同。"""
    assert SessionContext().session_id != SessionContext().session_id


def test_session_accumulates_turns() -> None:
    """轮次累加。"""
    session = SessionContext()
    runtime = _runtime(text_response("第一轮"), text_response("第二轮"))

    asyncio.run(session.run(runtime, "问题一"))
    asyncio.run(session.run(runtime, "问题二"))

    assert session.turns == 2


def test_second_turn_sees_first_turn() -> None:
    """第二轮的模型能看到第一轮的内容——这是会话存在的理由。"""
    from agent_learning_coach.core.llm.models import LLMRequest

    seen: list[LLMRequest] = []

    class RecordingClient(ScriptedClient):
        async def generate(self, request: LLMRequest):  # type: ignore[no-untyped-def]
            seen.append(request)
            return await super().generate(request)

    from agent_learning_coach.core.tools import ToolDispatcher, ToolRegistry

    runtime = AgentRuntime(
        RecordingClient([text_response("第一轮回答"), text_response("第二轮回答")]),
        ToolDispatcher(ToolRegistry()),
        config=AgentConfig(model="gpt-4", max_steps=2),
    )
    session = SessionContext()

    asyncio.run(session.run(runtime, "什么是闭包"))
    asyncio.run(session.run(runtime, "那装饰器呢"))

    second_request = seen[1]
    texts = [item.content for item in second_request.contents if isinstance(item, Message)]
    assert "什么是闭包" in texts
    assert "第一轮回答" in texts


def test_session_state_survives_across_turns() -> None:
    """草稿本跨轮次保留——工具在第一轮写的内容，第二轮还在。"""
    from agent_learning_coach.core.tools import ToolDispatcher, ToolRegistry

    runtime = AgentRuntime(
        ScriptedClient([text_response("好"), text_response("好")]),
        ToolDispatcher(ToolRegistry()),
        config=AgentConfig(model="gpt-4", max_steps=2),
    )
    session = SessionContext()

    asyncio.run(session.run(runtime, "第一轮"))
    session.state["learner_level"] = "初级"
    asyncio.run(session.run(runtime, "第二轮"))

    assert session.state["learner_level"] == "初级"


def test_session_contents_are_the_full_history() -> None:
    """会话持有完整对话——不因为多轮而丢失。"""
    session = SessionContext()
    runtime = _runtime(text_response("回答一"), text_response("回答二"))

    asyncio.run(session.run(runtime, "问题一"))
    asyncio.run(session.run(runtime, "问题二"))

    texts = [item.content for item in session.contents if isinstance(item, Message)]
    assert "问题一" in texts
    assert "问题二" in texts
    assert "回答一" in texts
    assert "回答二" in texts


def test_session_shares_one_execution_context() -> None:
    """两轮共享同一份执行上下文——这是「同一个会话」的物理体现。"""
    session = SessionContext()
    runtime = _runtime(text_response("好"), text_response("好"))

    first = asyncio.run(session.run(runtime, "第一轮"))
    second = asyncio.run(session.run(runtime, "第二轮"))

    assert first.context is second.context
    assert first.context is session.context


def test_failed_turn_still_counts_and_keeps_history() -> None:
    """失败的轮次也计数，未被回答的问题留在历史里。"""
    from agent_learning_coach.core.llm.errors import LLMError
    from agent_learning_coach.core.tools import ToolDispatcher, ToolRegistry

    class BrokenClient(ScriptedClient):
        async def generate(self, request):  # type: ignore[no-untyped-def]
            raise LLMError("挂了")

    runtime = AgentRuntime(
        BrokenClient([]),
        ToolDispatcher(ToolRegistry()),
        config=AgentConfig(model="gpt-4", max_steps=2),
    )
    session = SessionContext()

    result = asyncio.run(session.run(runtime, "没答上来的问题"))

    assert result.status == "error"
    assert session.turns == 1
    texts = [item.content for item in session.contents if isinstance(item, Message)]
    assert "没答上来的问题" in texts
