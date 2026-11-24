"""工具回调与 HITL 审批组合的测试。

对应 Topic 12「RAG 与 Tool Extension」，覆盖三组关注点：

**回调语义**——执行前的钩子可以短路（工具不执行），执行后的钩子可以替换
结果。

**事务语义**——无论钩子成功、失败还是替换结果，**一条 tool_call 恰好对应一条
tool_result**。这条不变量一旦破了，厂商 API 会拒绝整个请求（请求里有调用
却没有结果），而且报错信息完全指不到真正的原因。

**与 HITL 的组合**——审批发生在回调之前。被拒绝的调用根本不进入调度器，因此
回调看不到它；回调也无法绕过审批。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_learning_coach.core.agent import (
    AgentConfig,
    AgentRuntime,
    ApprovalDecision,
)
from agent_learning_coach.core.tools import (
    FunctionTool,
    ToolContext,
    ToolDefinition,
    ToolDispatcher,
    ToolOutcome,
    ToolRegistry,
)
from support import ScriptedClient, text_response, tool_call_response


def _registry(executed: list[dict]) -> ToolRegistry:
    """构造一个含普通工具与需确认工具的注册表。"""
    registry = ToolRegistry()

    async def search(context: ToolContext, arguments: dict) -> str:
        """普通工具。"""
        executed.append(arguments)
        return "检索结果"

    async def dangerous(context: ToolContext, arguments: dict) -> str:
        """需确认的工具。"""
        executed.append(arguments)
        return "已删除"

    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="search",
                description="检索",
                parameters={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            ),
            func=search,
        )
    )
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="delete_notes",
                description="删除笔记",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                requires_confirmation=True,
            ),
            func=dangerous,
        )
    )
    return registry


def test_before_hook_can_short_circuit() -> None:
    """执行前的钩子返回结果时，工具本身不执行。"""
    executed: list[dict] = []
    calls: list[str] = []

    def before(context: ToolContext, tool_name: str, arguments: dict) -> ToolOutcome:
        """短路这次调用。"""
        calls.append(tool_name)
        return ToolOutcome(tool_name=tool_name, status="success", content="来自缓存")

    dispatcher = ToolDispatcher(_registry(executed), before_hooks=[before])

    outcome = asyncio.run(dispatcher.dispatch("search", {"query": "闭包"}, ToolContext()))

    assert outcome.succeeded
    assert outcome.content == "来自缓存"
    assert executed == []
    assert calls == ["search"]


def test_before_hook_returning_none_lets_the_tool_run() -> None:
    """执行前的钩子返回 None 时放行。"""
    executed: list[dict] = []

    def before(context: ToolContext, tool_name: str, arguments: dict) -> None:
        """放行。"""
        return None

    dispatcher = ToolDispatcher(_registry(executed), before_hooks=[before])

    outcome = asyncio.run(dispatcher.dispatch("search", {"query": "闭包"}, ToolContext()))

    assert outcome.succeeded
    assert outcome.content == "检索结果"
    assert len(executed) == 1


def test_after_hook_can_replace_the_result() -> None:
    """执行后的钩子可以替换结果。"""
    executed: list[dict] = []

    def after(context: ToolContext, outcome: ToolOutcome, arguments: dict) -> ToolOutcome:
        """把结果改写一下。"""
        return ToolOutcome(
            tool_name=outcome.tool_name,
            status=outcome.status,
            content=f"压缩后：{outcome.content}",
            data=outcome.data,
        )

    dispatcher = ToolDispatcher(_registry(executed), after_hooks=[after])

    outcome = asyncio.run(dispatcher.dispatch("search", {"query": "闭包"}, ToolContext()))

    assert outcome.content == "压缩后：检索结果"
    assert len(executed) == 1


def test_hooks_run_in_order_around_the_tool() -> None:
    """顺序是：执行前钩子 → 工具 → 执行后钩子。"""
    order: list[str] = []
    registry = ToolRegistry()

    async def tool(context: ToolContext, arguments: dict) -> str:
        """记录顺序。"""
        order.append("tool")
        return "ok"

    registry.register(
        FunctionTool(
            definition=ToolDefinition(name="t", description="d", parameters={}),
            func=tool,
        )
    )

    def before(context: ToolContext, tool_name: str, arguments: dict) -> None:
        """记录顺序。"""
        order.append("before")
        return None

    def after(context: ToolContext, outcome: ToolOutcome, arguments: dict) -> None:
        """记录顺序。"""
        order.append("after")
        return None

    dispatcher = ToolDispatcher(registry, before_hooks=[before], after_hooks=[after])

    asyncio.run(dispatcher.dispatch("t", {}, ToolContext()))

    assert order == ["before", "tool", "after"]


def test_failing_before_hook_still_produces_exactly_one_result() -> None:
    """执行前的钩子失败时，工具不执行，但仍然只有一条结果。"""
    executed: list[dict] = []

    def before(context: ToolContext, tool_name: str, arguments: dict) -> None:
        """抛异常。"""
        raise RuntimeError("钩子坏了")

    dispatcher = ToolDispatcher(_registry(executed), before_hooks=[before])

    outcome = asyncio.run(dispatcher.dispatch("search", {"query": "闭包"}, ToolContext()))

    assert not outcome.succeeded
    assert "钩子坏了" in (outcome.content or "")
    assert executed == []


def test_failing_after_hook_keeps_the_tool_result() -> None:
    """执行后的钩子失败不丢弃工具的结果——工具已经跑完了。"""
    executed: list[dict] = []

    def after(context: ToolContext, outcome: ToolOutcome, arguments: dict) -> None:
        """抛异常。"""
        raise RuntimeError("后处理坏了")

    dispatcher = ToolDispatcher(_registry(executed), after_hooks=[after])

    outcome = asyncio.run(dispatcher.dispatch("search", {"query": "闭包"}, ToolContext()))

    assert outcome.succeeded
    assert outcome.content == "检索结果"
    assert outcome.metadata["callback_errors"]


def test_permission_check_happens_before_hooks() -> None:
    """权限检查在钩子之前——钩子不能绕过权限。"""
    from agent_learning_coach.core.tools import DenyAllPolicy

    executed: list[dict] = []
    hook_calls: list[str] = []

    def before(context: ToolContext, tool_name: str, arguments: dict) -> ToolOutcome:
        """短路。"""
        hook_calls.append(tool_name)
        return ToolOutcome(tool_name=tool_name, status="success", content="绕过")

    dispatcher = ToolDispatcher(
        _registry(executed),
        policy=DenyAllPolicy("没有权限"),
        before_hooks=[before],
    )

    outcome = asyncio.run(dispatcher.dispatch("search", {"query": "闭包"}, ToolContext()))

    assert not outcome.succeeded
    assert hook_calls == []
    assert executed == []


def test_tool_may_return_a_tool_outcome_directly() -> None:
    """工具可以直接返回 ToolOutcome——文本给模型，结构给程序。"""
    registry = ToolRegistry()

    async def tool(context: ToolContext, arguments: dict) -> ToolOutcome:
        """返回归一化结果。"""
        return ToolOutcome(
            tool_name="t",
            status="success",
            content="给模型看的文本",
            data={"citations": [{"marker": "1"}]},
        )

    registry.register(
        FunctionTool(
            definition=ToolDefinition(name="t", description="d", parameters={}),
            func=tool,
        )
    )
    dispatcher = ToolDispatcher(registry)

    outcome = asyncio.run(dispatcher.dispatch("t", {}, ToolContext()))

    assert outcome.content == "给模型看的文本"
    assert outcome.data == {"citations": [{"marker": "1"}]}


# ---------------------------------------------------------------------------
# 与 HITL 审批的组合
# ---------------------------------------------------------------------------


def _runtime(registry: ToolRegistry, *responses: Any) -> AgentRuntime:
    """构造一个按脚本说话的运行时。"""
    return AgentRuntime(
        ScriptedClient(list(responses)),
        ToolDispatcher(registry),
        config=AgentConfig(model="gpt-4", max_steps=4),
    )


def test_denied_call_never_reaches_the_hooks() -> None:
    """被拒绝的调用根本不进入调度器，因此回调看不到它。"""
    executed: list[dict] = []
    hook_calls: list[str] = []

    def before(context: ToolContext, tool_name: str, arguments: dict) -> None:
        """记录调用。"""
        hook_calls.append(tool_name)
        return None

    def after(context: ToolContext, outcome: ToolOutcome, arguments: dict) -> None:
        """记录调用。"""
        hook_calls.append(f"after:{outcome.tool_name}")
        return None

    runtime = _runtime(
        _registry(executed),
        tool_call_response("delete_notes", {"path": "a.md"}),
        text_response("好的，不删了"),
    )
    runtime.dispatcher.before_hooks = (before,)
    runtime.dispatcher.after_hooks = (after,)

    result = asyncio.run(runtime.run("删掉 a.md"))

    assert result.status == "pending_approval"
    assert hook_calls == []
    assert executed == []


def test_approved_call_runs_the_hooks() -> None:
    """批准后调用照常执行，回调也照常触发。"""
    executed: list[dict] = []
    hook_calls: list[str] = []

    def before(context: ToolContext, tool_name: str, arguments: dict) -> None:
        """记录调用。"""
        hook_calls.append(f"before:{tool_name}")
        return None

    def after(context: ToolContext, outcome: ToolOutcome, arguments: dict) -> None:
        """记录调用。"""
        hook_calls.append(f"after:{outcome.tool_name}")
        return None

    runtime = _runtime(
        _registry(executed),
        tool_call_response("delete_notes", {"path": "a.md"}),
        text_response("已删除"),
    )
    runtime.dispatcher.before_hooks = (before,)
    runtime.dispatcher.after_hooks = (after,)

    pending = asyncio.run(runtime.run("删掉 a.md"))
    assert pending.status == "pending_approval"

    resumed = asyncio.run(
        runtime.resume(
            pending.context,
            [ApprovalDecision(tool_call_id="call-1", approved=True)],
        )
    )

    assert resumed.status == "complete"
    assert hook_calls == ["before:delete_notes", "after:delete_notes"]
    assert len(executed) == 1


def test_pause_resume_keeps_every_call_paired_with_a_result() -> None:
    """暂停与恢复之后，转录里每条 tool_call 仍有对应的 tool_result。"""
    from agent_learning_coach.core.llm.types import ToolCall, ToolResult

    executed: list[dict] = []
    runtime = _runtime(
        _registry(executed),
        tool_call_response("delete_notes", {"path": "a.md"}),
        text_response("已删除"),
    )

    pending = asyncio.run(runtime.run("删掉 a.md"))
    resumed = asyncio.run(
        runtime.resume(
            pending.context,
            [ApprovalDecision(tool_call_id="call-1", approved=True)],
        )
    )

    calls = [
        item
        for event in resumed.context.events
        for item in event.contents
        if isinstance(item, ToolCall)
    ]
    results = [
        item
        for event in resumed.context.events
        for item in event.contents
        if isinstance(item, ToolResult)
    ]

    assert len(calls) == 1
    assert len(results) == 1
    assert calls[0].tool_call_id == results[0].tool_call_id


def test_denied_call_still_gets_a_result() -> None:
    """被拒绝的调用也要有一条结果——否则转录会断裂。"""
    executed: list[dict] = []
    runtime = _runtime(
        _registry(executed),
        tool_call_response("delete_notes", {"path": "a.md"}),
        text_response("好的"),
    )

    pending = asyncio.run(runtime.run("删掉 a.md"))
    resumed = asyncio.run(
        runtime.resume(pending.context, [ApprovalDecision(tool_call_id="call-1", approved=False)])
    )

    tool_results = [
        item
        for event in resumed.context.events
        for item in event.contents
        if item.__class__.__name__ == "ToolResult"
    ]
    assert len(tool_results) == 1
    assert "拒绝" in tool_results[0].content


@pytest.mark.parametrize("approved", [True, False])
def test_callback_cannot_bypass_the_approval(approved: bool) -> None:
    """执行前的钩子无法放行一个未获批准的调用——它根本没进调度器。"""
    executed: list[dict] = []
    hook_calls: list[str] = []

    def before(context: ToolContext, tool_name: str, arguments: dict) -> ToolOutcome:
        """试图短路成成功。"""
        hook_calls.append(tool_name)
        return ToolOutcome(tool_name=tool_name, status="success", content="绕过审批")

    runtime = _runtime(
        _registry(executed),
        tool_call_response("delete_notes", {"path": "a.md"}),
        text_response("好的"),
    )
    runtime.dispatcher.before_hooks = (before,)

    pending = asyncio.run(runtime.run("删掉 a.md"))
    asyncio.run(
        runtime.resume(
            pending.context,
            [ApprovalDecision(tool_call_id="call-1", approved=approved)],
        )
    )

    if approved:
        assert hook_calls == ["delete_notes"]
    else:
        assert hook_calls == []
