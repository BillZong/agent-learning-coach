"""HITL（人工在环）与会话持久化的测试。

对应 Topic 09「Session 与 HITL」，覆盖三组关注点：

**暂停与恢复**——遇到需要确认的工具时，运行**整个步骤**暂停（一步的工具
调用要么全执行，要么全暂停；部分执行会让转录里出现「有调用没结果」的
断裂，厂商 API 会拒绝整个请求）。恢复时从暂停的那一步继续。

**审批**——批准的照常执行（可以用修改过的参数），没批准的回填一条「用户
拒绝」的结果。**没有决定的按拒绝处理**（fail closed）。

**持久化**——落盘的是「恢复一个运行所需的全部状态」；少存一项，恢复出来
的就不是原来那个运行。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent_learning_coach.core.agent import (
    AgentConfig,
    AgentError,
    AgentRuntime,
    ApprovalDecision,
    Event,
    InMemorySessionStore,
    JsonFileSessionStore,
    PendingApproval,
    SessionContext,
    SessionState,
)
from agent_learning_coach.core.llm.models import LLMResponse, LLMUsage
from agent_learning_coach.core.llm.types import Message, ToolCall, ToolResult
from agent_learning_coach.core.tools import (
    FunctionTool,
    ToolContext,
    ToolDefinition,
    ToolDispatcher,
    ToolRegistry,
)
from support import ScriptedClient, text_response, tool_call_response


def _registry_with_confirmable_tool(
    *,
    requires_confirmation: bool = True,
    message: str = "",
    executed: list[dict] | None = None,
) -> ToolRegistry:
    """构造一个含「需确认工具」的注册表。"""
    registry = ToolRegistry()

    async def dangerous(context: ToolContext, arguments: dict) -> str:
        """高风险工具。"""
        if executed is not None:
            executed.append(arguments)
        return f"已执行 {arguments.get('path', '')}"

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
                requires_confirmation=requires_confirmation,
                confirmation_message=message,
            ),
            func=dangerous,
        )
    )
    return registry


def _runtime(registry: ToolRegistry, *responses: object) -> AgentRuntime:
    """构造一个按脚本说话的运行时。"""
    return AgentRuntime(
        ScriptedClient(list(responses)),
        ToolDispatcher(registry),
        config=AgentConfig(model="gpt-4", max_steps=4),
    )


# ---------------------------------------------------------------------------
# 暂停
# ---------------------------------------------------------------------------


def test_run_pauses_on_confirmable_tool() -> None:
    """遇到需确认的工具时运行暂停，并报告待确认的调用。"""
    executed: list[dict] = []
    registry = _registry_with_confirmable_tool(executed=executed)
    runtime = _runtime(
        registry,
        tool_call_response("delete_notes", {"path": "a.md"}),
        text_response("好的"),
    )

    result = asyncio.run(runtime.run("删掉笔记"))

    assert result.status == "pending_approval"
    assert result.stop_reason == "pending_approval"
    assert len(result.pending_approvals) == 1
    assert result.pending_approvals[0].tool_call.name == "delete_notes"
    # 工具**没有**被执行
    assert executed == []


def test_pending_approval_carries_confirmation_message() -> None:
    """待确认的调用带着给人看的提示。"""
    registry = _registry_with_confirmable_tool(message="确认要删除 {path} 吗？")
    runtime = _runtime(registry, tool_call_response("delete_notes", {"path": "a.md"}))

    result = asyncio.run(runtime.run("删掉笔记"))

    assert result.pending_approvals[0].confirmation_message == "确认要删除 a.md 吗？"


def test_default_confirmation_message_is_generated() -> None:
    """没写模板时生成默认提示。"""
    registry = _registry_with_confirmable_tool()
    runtime = _runtime(registry, tool_call_response("delete_notes", {"path": "a.md"}))

    result = asyncio.run(runtime.run("删掉笔记"))

    assert "delete_notes" in result.pending_approvals[0].confirmation_message


def test_paused_run_refuses_to_start_again() -> None:
    """暂停的运行不能再 run()——否则请求里会带着没有结果的 tool_call。

    脚本里留足了后续响应，因此这个断言只能因为「运行时拒绝了」而成立，
    不会因为「脚本耗尽」而误判为通过。
    """
    registry = _registry_with_confirmable_tool()
    runtime = _runtime(
        registry,
        tool_call_response("delete_notes", {"path": "a.md"}),
        text_response("好的"),
        text_response("再问一句"),
    )
    context_result = asyncio.run(runtime.run("删掉笔记"))

    with pytest.raises(AgentError, match="暂停状态"):
        asyncio.run(runtime.run("再问一句", context=context_result.context))


def test_whole_step_is_paused_not_partial() -> None:
    """一步里的工具调用要么全执行，要么全暂停。

    部分执行会让转录里出现「有调用没结果」的断裂——厂商 API 会拒绝整个
    请求。因此这一步里那个不需要确认的工具也**不能**执行。
    """
    executed: list[str] = []
    registry = _registry_with_confirmable_tool()

    async def safe(context: ToolContext, arguments: dict) -> str:
        """普通工具——本该在同一步里执行，但整步暂停了。"""
        executed.append("read_notes")
        return "读了"

    registry.register(
        FunctionTool(
            definition=ToolDefinition(name="read_notes", description="读取笔记"),
            func=safe,
        )
    )

    # 一步里同时请求两个工具
    response = LLMResponse(
        model="gpt-4",
        contents=[
            ToolCall(tool_call_id="call-1", name="read_notes", arguments={}),
            ToolCall(tool_call_id="call-2", name="delete_notes", arguments={"path": "a.md"}),
        ],
        usage=LLMUsage(input_tokens=10, output_tokens=5),
    )
    runtime = _runtime(registry, response, text_response("好的"))

    result = asyncio.run(runtime.run("读并删"))

    assert result.status == "pending_approval"
    # 不需要确认的工具也没执行
    assert executed == []
    assert len(result.pending_approvals) == 1


# ---------------------------------------------------------------------------
# 恢复与审批
# ---------------------------------------------------------------------------


def test_resume_executes_approved_tool() -> None:
    """批准的调用在恢复时执行。"""
    executed: list[dict] = []
    registry = _registry_with_confirmable_tool(executed=executed)
    runtime = _runtime(
        registry,
        tool_call_response("delete_notes", {"path": "a.md"}),
        text_response("删掉了"),
    )

    paused = asyncio.run(runtime.run("删掉笔记"))
    assert executed == []

    result = asyncio.run(
        runtime.resume(
            paused.context,
            [ApprovalDecision(tool_call_id="call-1", approved=True)],
        )
    )

    assert executed == [{"path": "a.md"}]
    assert result.status == "complete"
    assert result.output == "删掉了"


def test_resume_denied_tool_feeds_back_a_refusal() -> None:
    """拒绝的调用回填一条「用户拒绝」的结果，模型据此改道。"""
    executed: list[dict] = []
    registry = _registry_with_confirmable_tool(executed=executed)
    runtime = _runtime(
        registry,
        tool_call_response("delete_notes", {"path": "a.md"}),
        text_response("那我不删了"),
    )

    paused = asyncio.run(runtime.run("删掉笔记"))
    result = asyncio.run(
        runtime.resume(
            paused.context,
            [ApprovalDecision(tool_call_id="call-1", approved=False, reason="我不确定")],
        )
    )

    assert executed == []
    assert result.status == "complete"
    assert result.output == "那我不删了"
    # 拒绝的理由以工具结果的形式回填进上下文，模型能读到
    results = [item.content for item in paused.context.contents if isinstance(item, ToolResult)]
    assert any("用户拒绝执行该工具" in text for text in results)
    assert any("我不确定" in text for text in results)


def test_missing_decision_fails_closed() -> None:
    """没有决定的调用按拒绝处理（fail closed）。

    缺少批准就执行，等于让「批准」变成默认开启——那就不是批准机制了。
    """
    executed: list[dict] = []
    registry = _registry_with_confirmable_tool(executed=executed)
    runtime = _runtime(
        registry,
        tool_call_response("delete_notes", {"path": "a.md"}),
        text_response("好的"),
    )

    paused = asyncio.run(runtime.run("删掉笔记"))
    asyncio.run(runtime.resume(paused.context, []))

    assert executed == []


def test_decision_for_another_call_fails_closed() -> None:
    """给错了 id 的批准不会误放行。"""
    executed: list[dict] = []
    registry = _registry_with_confirmable_tool(executed=executed)
    runtime = _runtime(
        registry,
        tool_call_response("delete_notes", {"path": "a.md"}),
        text_response("好的"),
    )

    paused = asyncio.run(runtime.run("删掉笔记"))
    asyncio.run(
        runtime.resume(
            paused.context,
            [ApprovalDecision(tool_call_id="call-999", approved=True)],
        )
    )

    assert executed == []


def test_approved_call_can_modify_arguments() -> None:
    """人可以改参数再批准。"""
    executed: list[dict] = []
    registry = _registry_with_confirmable_tool(executed=executed)
    runtime = _runtime(
        registry,
        tool_call_response("delete_notes", {"path": "a.md"}),
        text_response("好的"),
    )

    paused = asyncio.run(runtime.run("删掉笔记"))
    asyncio.run(
        runtime.resume(
            paused.context,
            [
                ApprovalDecision(
                    tool_call_id="call-1",
                    approved=True,
                    modified_arguments={"path": "b.md"},
                )
            ],
        )
    )

    assert executed == [{"path": "b.md"}]


def test_resume_continues_from_the_paused_step() -> None:
    """恢复后从暂停的那一步继续，而不是从头再来。"""
    registry = _registry_with_confirmable_tool()
    client = ScriptedClient(
        [tool_call_response("delete_notes", {"path": "a.md"}), text_response("删掉了")]
    )
    runtime = AgentRuntime(
        client,
        ToolDispatcher(registry),
        config=AgentConfig(model="gpt-4", max_steps=4),
    )

    paused = asyncio.run(runtime.run("删掉笔记"))
    assert paused.context.current_step == 0

    result = asyncio.run(
        runtime.resume(
            paused.context,
            [ApprovalDecision(tool_call_id="call-1", approved=True)],
        )
    )

    assert result.context.current_step == 1
    assert result.steps[0].index == 0


def test_resume_on_a_running_context_raises() -> None:
    """没有被暂停的运行不能恢复。"""
    registry = _registry_with_confirmable_tool()
    runtime = _runtime(registry, text_response("好的"))

    result = asyncio.run(runtime.run("你好"))

    with pytest.raises(AgentError):
        asyncio.run(runtime.resume(result.context, []))


def test_budget_counters_survive_pause_and_resume() -> None:
    """工具调用计数住在上下文里——否则暂停-恢复可以绕过预算。"""
    registry = _registry_with_confirmable_tool()
    runtime = AgentRuntime(
        ScriptedClient(
            [
                tool_call_response("delete_notes", {"path": "a.md"}),
                text_response("好的"),
            ]
        ),
        ToolDispatcher(registry),
        config=AgentConfig(model="gpt-4", max_steps=4, max_tool_calls=1),
    )

    paused = asyncio.run(runtime.run("删掉笔记"))
    result = asyncio.run(
        runtime.resume(
            paused.context,
            [ApprovalDecision(tool_call_id="call-1", approved=True)],
        )
    )

    # 恢复时执行的那次调用也计入预算
    assert paused.context.tool_call_count == 1
    assert result.stop_reason == "max_tool_calls"


def test_usage_survives_pause_and_resume() -> None:
    """用量与成本在暂停时已经发生，恢复后不能丢。"""
    registry = _registry_with_confirmable_tool()
    runtime = _runtime(
        registry,
        tool_call_response("delete_notes", {"path": "a.md"}),
        text_response("好的"),
    )

    paused = asyncio.run(runtime.run("删掉笔记"))
    used_before = paused.context.usage.input_tokens

    result = asyncio.run(
        runtime.resume(
            paused.context,
            [ApprovalDecision(tool_call_id="call-1", approved=True)],
        )
    )

    assert paused.context.usage.input_tokens > used_before
    assert result.context.usage.input_tokens == paused.context.usage.input_tokens


# ---------------------------------------------------------------------------
# 持久化
# ---------------------------------------------------------------------------


def test_session_state_round_trips_through_dict() -> None:
    """状态快照能完整地序列化与还原。"""
    session = SessionContext(session_id="s1", user_id="u1")
    session.context.add_event(
        Event.create(
            session.context.execution_id,
            "user",
            [Message(role="user", content="你好")],
        )
    )
    session.context.state["level"] = "初级"
    session.context.tool_call_count = 3

    state = session.to_state()
    restored = SessionContext.from_state(state)

    assert restored.session_id == "s1"
    assert restored.user_id == "u1"
    assert len(restored.context.events) == len(session.context.events)
    assert restored.context.state["level"] == "初级"
    assert restored.context.tool_call_count == 3


def test_session_state_round_trips_through_json() -> None:
    """状态快照能完整地走一遍 JSON。"""
    session = SessionContext(session_id="s1")
    session.context.pending_approvals.append(
        PendingApproval(
            tool_call=ToolCall(tool_call_id="c1", name="delete_notes", arguments={"path": "a"}),
            confirmation_message="确认删除 a？",
        )
    )

    payload = json.dumps(session.to_state().to_dict(), ensure_ascii=False)
    restored = SessionContext.from_state(SessionState.from_dict(json.loads(payload)))

    assert restored.session_id == "s1"
    assert len(restored.context.pending_approvals) == 1
    assert restored.context.pending_approvals[0].tool_call.name == "delete_notes"


def test_in_memory_store_saves_and_loads() -> None:
    """内存存储能存能取。"""
    store = InMemorySessionStore()
    session = SessionContext(session_id="s1")

    asyncio.run(session.save(store))
    loaded = asyncio.run(SessionContext.load(store, "s1"))

    assert loaded is not None
    assert loaded.session_id == "s1"


def test_store_returns_none_for_missing_session() -> None:
    """不存在的会话返回 None。"""
    store = InMemorySessionStore()

    assert asyncio.run(SessionContext.load(store, "nope")) is None


def test_json_file_store_round_trips(tmp_path: Path) -> None:
    """文件存储能存能取，且内容是合法 JSON。"""
    store = JsonFileSessionStore(tmp_path / "sessions")
    session = SessionContext(session_id="s1")
    session.context.state["level"] = "初级"

    asyncio.run(session.save(store))
    loaded = asyncio.run(SessionContext.load(store, "s1"))

    assert loaded is not None
    assert loaded.context.state["level"] == "初级"
    payload = json.loads((tmp_path / "sessions" / "s1.json").read_text(encoding="utf-8"))
    assert payload["session_id"] == "s1"


def test_paused_session_survives_a_round_trip(tmp_path: Path) -> None:
    """暂停状态能被持久化并在另一个进程里恢复。

    这是「落盘哪些状态」这个问题的直接检验：待确认的调用、事件历史、
    用量与计数都要在。
    """
    store = JsonFileSessionStore(tmp_path / "sessions")
    registry = _registry_with_confirmable_tool()
    runtime = _runtime(
        registry,
        tool_call_response("delete_notes", {"path": "a.md"}),
        text_response("删掉了"),
    )
    session = SessionContext(session_id="s1")

    paused = asyncio.run(session.run(runtime, "删掉笔记"))
    assert paused.status == "pending_approval"
    asyncio.run(session.save(store))

    # 模拟「另一个进程」：全新的 store 与 session 对象
    other_store = JsonFileSessionStore(tmp_path / "sessions")
    restored = asyncio.run(SessionContext.load(other_store, "s1"))
    assert restored is not None

    other_runtime = _runtime(
        registry,
        text_response("删掉了"),
    )
    result = asyncio.run(
        restored.resume(
            other_runtime,
            [ApprovalDecision(tool_call_id="call-1", approved=True)],
        )
    )

    assert result.status == "complete"
    assert result.output == "删掉了"


def test_paused_session_keeps_its_history(tmp_path: Path) -> None:
    """恢复后的模型能看到暂停前的历史。"""
    store = JsonFileSessionStore(tmp_path / "sessions")
    registry = _registry_with_confirmable_tool()
    runtime = _runtime(
        registry,
        tool_call_response("delete_notes", {"path": "a.md"}),
        text_response("好的"),
    )
    session = SessionContext(session_id="s1")

    asyncio.run(session.run(runtime, "删掉笔记"))
    asyncio.run(session.save(store))

    restored = asyncio.run(SessionContext.load(store, "s1"))
    assert restored is not None
    texts = [item.content for item in restored.contents if isinstance(item, Message)]
    assert "删掉笔记" in texts


def test_delete_removes_the_session(tmp_path: Path) -> None:
    """删除会话。"""
    store = JsonFileSessionStore(tmp_path / "sessions")
    session = SessionContext(session_id="s1")
    asyncio.run(session.save(store))

    asyncio.run(store.delete("s1"))

    assert asyncio.run(SessionContext.load(store, "s1")) is None


# ---------------------------------------------------------------------------
# 边界情形
# ---------------------------------------------------------------------------


def test_confirmation_message_falls_back_when_template_is_broken() -> None:
    """模板写坏时退回默认提示——确认流程不该因此崩掉。"""
    registry = _registry_with_confirmable_tool(message="确认要删除 {path[nonexistent]} 吗？")
    runtime = _runtime(registry, tool_call_response("delete_notes", {"path": "a.md"}))

    result = asyncio.run(runtime.run("删掉笔记"))

    assert "delete_notes" in result.pending_approvals[0].confirmation_message


def test_confirmation_message_tolerates_missing_argument() -> None:
    """模板里的参数这次没有提供时填占位值，不抛异常。"""
    registry = _registry_with_confirmable_tool(message="确认要删除 {path} 吗？")
    runtime = _runtime(registry, tool_call_response("delete_notes", {}))

    result = asyncio.run(runtime.run("删掉笔记"))

    assert "unknown" in result.pending_approvals[0].confirmation_message


def test_hitl_example_runs() -> None:
    """HITL 示例可运行——它是这个能力对外的可见形态。"""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "examples" / "hitl_approval.py")],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=root,
    )

    assert result.returncode == 0, result.stderr
    assert "暂停、落盘、跨进程恢复与批准执行都成立" in result.stdout
