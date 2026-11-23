"""Agent 运行的状态转换测试。

Day 9 checklist 里的「编写状态转换测试」——把运行当成一台**状态机**来验证，
而不是逐个场景去测。

状态机的状态由「运行处在哪个阶段」决定：

```
                 run()
                  │
                  ▼
            ┌───────────┐
            │  running  │◀─────┐
            └───────────┘      │
              │  │  │         │ 还有步数
              │  │  │         │
   completed  │  │  └─────────┘
   error      │  └──────────► paused
              │              （等待人工批准）
              │                 │
              │            resume(批准)
              └─────────────────┘
```

关键的不变量：

- **一次运行只有一个终态**：completed / error / pending_approval。
- **paused 是唯一可以恢复的状态**，且只能恢复一次——恢复后若又遇到需确认的
  工具，会再次进入 paused（这是新的一次暂停）。
- **paused 状态下不能 run()**——请求里会带着没有结果的 tool_call。
"""

from __future__ import annotations

import asyncio

import pytest

from agent_learning_coach.core.agent import (
    AgentConfig,
    AgentError,
    AgentRuntime,
    ApprovalDecision,
)
from agent_learning_coach.core.llm.models import LLMResponse, LLMUsage
from agent_learning_coach.core.llm.types import Message, ToolCall
from agent_learning_coach.core.tools import (
    FunctionTool,
    ToolDefinition,
    ToolDispatcher,
    ToolRegistry,
)


def _registry(*, requires_confirmation: bool = True) -> ToolRegistry:
    """构造一个含单个工具的注册表。"""
    registry = ToolRegistry()

    async def tool(context: object, arguments: dict) -> str:
        """工具实现。"""
        return "执行完成"

    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="do_it",
                description="做一件事",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
                requires_confirmation=requires_confirmation,
            ),
            func=tool,
        )
    )
    return registry


def _tool_call_response(call_id: str = "call-1") -> LLMResponse:
    """构造一个请求工具的模型响应。"""
    return LLMResponse(
        model="gpt-4",
        contents=[ToolCall(tool_call_id=call_id, name="do_it", arguments={"path": "a.md"})],
        usage=LLMUsage(input_tokens=10, output_tokens=5),
    )


def _text_response(text: str = "好的") -> LLMResponse:
    """构造一个纯文本响应。"""
    return LLMResponse(
        model="gpt-4",
        contents=[Message(role="assistant", content=text)],
        usage=LLMUsage(input_tokens=7, output_tokens=3),
    )


def _runtime(responses: list[LLMResponse], *, max_steps: int = 4) -> AgentRuntime:
    """构造一个按脚本说话的运行时。"""
    from agent_learning_coach.core.llm.mock import MockLLMClient

    script = list(responses)

    def handler(request: object) -> LLMResponse:
        """按脚本依次返回响应。"""
        return script.pop(0)

    return AgentRuntime(
        MockLLMClient(handler=handler),
        ToolDispatcher(_registry()),
        config=AgentConfig(model="gpt-4", max_steps=max_steps),
    )


# ---------------------------------------------------------------------------
# 状态转换表
# ---------------------------------------------------------------------------


def test_transition_table() -> None:
    """按表驱动逐条验证状态转换。

    每一行是「起始状态 + 触发动作 → 期望的结束状态」。用参数化而不是散落的
    断言，是为了让转换关系一眼可见——新增状态时只要加一行。
    """
    # (用例名, 起始状态, 动作, 期望状态, 期望停止原因)
    cases = [
        ("新运行 → 完成", "fresh", "run_text", "complete", "completed"),
        ("新运行 → 等待批准", "fresh", "run_tool", "pending_approval", "pending_approval"),
        ("等待批准 → 批准后完成", "paused", "approve", "complete", "completed"),
        ("等待批准 → 拒绝后完成", "paused", "deny", "complete", "completed"),
        ("等待批准 → 无决定后完成", "paused", "no_decision", "complete", "completed"),
        ("等待批准 → 再次 run 被拒", "paused", "run_again", "refused", "pending_approval"),
        ("完成 → 无需恢复", "completed", "resume", "refused", "completed"),
    ]

    for name, start_state, action, expected_state, expected_reason in cases:
        if start_state == "fresh":
            if action == "run_text":
                result = asyncio.run(_runtime([_text_response()]).run("你好"))
            else:
                result = asyncio.run(_runtime([_tool_call_response()]).run("做一件事"))
            assert result.status == expected_state, name
            assert result.stop_reason == expected_reason, name
            continue

        if start_state == "paused":
            runtime = _runtime([_tool_call_response(), _text_response("完成")])
            paused = asyncio.run(runtime.run("做一件事"))
            assert paused.status == "pending_approval", name

            if action == "approve":
                result = asyncio.run(
                    runtime.resume(
                        paused.context,
                        [ApprovalDecision(tool_call_id="call-1", approved=True)],
                    )
                )
            elif action == "deny":
                result = asyncio.run(
                    runtime.resume(
                        paused.context,
                        [
                            ApprovalDecision(
                                tool_call_id="call-1",
                                approved=False,
                                reason="不",
                            )
                        ],
                    )
                )
            elif action == "no_decision":
                result = asyncio.run(runtime.resume(paused.context, []))
            elif action == "run_again":
                with pytest.raises(AgentError):
                    asyncio.run(runtime.run("再问一句", context=paused.context))
                continue
            else:  # pragma: no cover - 用例表不会走到
                raise AssertionError(name)

            assert result.status == expected_state, name
            assert result.stop_reason == expected_reason, name
            continue

        if start_state == "completed" and action == "resume":
            runtime = _runtime([_text_response()])
            result = asyncio.run(runtime.run("你好"))
            with pytest.raises(AgentError):
                asyncio.run(runtime.resume(result.context, []))
            continue


def test_fresh_run_to_paused() -> None:
    """新运行遇到需确认的工具 → paused。"""
    runtime = _runtime([_tool_call_response()])

    result = asyncio.run(runtime.run("做一件事"))

    assert result.status == "pending_approval"
    assert result.stop_reason == "pending_approval"


def test_paused_is_the_only_resumable_state() -> None:
    """只有 paused 能恢复——其它状态恢复会报错。"""
    runtime = _runtime([_text_response()])
    completed = asyncio.run(runtime.run("你好"))

    with pytest.raises(AgentError):
        asyncio.run(runtime.resume(completed.context, []))


def test_resume_clears_the_pending_state() -> None:
    """恢复后待审批被清空——运行不再是暂停状态。"""
    runtime = _runtime([_tool_call_response(), _text_response("完成")])
    paused = asyncio.run(runtime.run("做一件事"))
    assert paused.context.pending_approvals

    asyncio.run(
        runtime.resume(
            paused.context,
            [ApprovalDecision(tool_call_id="call-1", approved=True)],
        )
    )

    assert paused.context.pending_approvals == []


def test_second_pause_is_a_new_pause() -> None:
    """恢复后又遇到需确认的工具 → 再次 paused（是新的一次暂停，不是恢复失败）。"""
    runtime = _runtime(
        [
            _tool_call_response("call-1"),
            _tool_call_response("call-2"),
            _text_response("完成"),
        ]
    )
    paused = asyncio.run(runtime.run("做一件事"))

    resumed = asyncio.run(
        runtime.resume(
            paused.context,
            [ApprovalDecision(tool_call_id="call-1", approved=True)],
        )
    )

    assert resumed.status == "pending_approval"
    assert resumed.stop_reason == "pending_approval"
    assert len(resumed.pending_approvals) == 1
    assert resumed.pending_approvals[0].tool_call.tool_call_id == "call-2"


def test_terminal_states_are_mutually_exclusive() -> None:
    """completed / error / pending_approval 三者互斥。"""
    completed = _runtime([_text_response()])
    result = asyncio.run(completed.run("你好"))
    assert (result.status == "complete") != (result.status == "error")
    assert (result.status == "complete") != (result.status == "pending_approval")

    failing = _runtime([_tool_call_response()])
    paused = asyncio.run(failing.run("做一件事"))
    assert paused.status != "complete"
    assert paused.status != "error"


def test_status_and_stop_reason_agree() -> None:
    """状态与停止原因始终一致——两者不会各说各话。"""
    runtime = _runtime([_tool_call_response()])
    paused = asyncio.run(runtime.run("做一件事"))

    assert (paused.status, paused.stop_reason) == ("pending_approval", "pending_approval")

    runtime = _runtime([_text_response()])
    done = asyncio.run(runtime.run("你好"))

    assert (done.status, done.stop_reason) == ("complete", "completed")
