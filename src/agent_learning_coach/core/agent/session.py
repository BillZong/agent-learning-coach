"""会话上下文与状态持久化。

**一次运行 ≠ 一次会话**。一次运行（``AgentRuntime.run``）是模型与工具之间的
一轮循环；一次会话是学习者与教练之间的**多轮对话**。前者有明确的结束，后者
只在学习者离开时才结束。

因此会话需要一个**跨运行持续存在的存储**。``SessionContext`` 就是它：一个
持有 ``ExecutionContext`` 的会话，持续累积对话，轮次累加。

**持久化边界**（详见 ADR-0009）：

- **必须落盘**：事件历史、草稿本、用量与成本、步数、工具调用计数与停止
  条件计数、待确认的审批、创建与更新时刻。少一样，恢复后的运行就不是原来
  那个运行了。
- **不必落盘**：``AgentStep``（逐步明细）。它是单次运行的观测数据，完整
  过程在事件历史里。

**为什么放在 ``core/agent`` 而不是 ``core/context``**：会话持有的是
``ExecutionContext``——那是 ``core/agent`` 的类型。若把它放进 ``core/context``，
上下文层就要反向依赖运行时层，破坏本项目一贯的依赖方向。

**本阶段只做基础形态**：内存与文件两种存储。没有数据库、没有多租户隔离、
没有并发控制——那些属于后续议题。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

from pydantic import BaseModel

from agent_learning_coach.core.agent.runtime import AgentRuntime
from agent_learning_coach.core.agent.types import (
    AgentResult,
    ApprovalDecision,
    Event,
    ExecutionContext,
    PendingApproval,
)
from agent_learning_coach.core.llm.models import LLMUsage
from agent_learning_coach.core.llm.types import ContentItem, Message, ToolCall, ToolResult

_CONTENT_TYPES: dict[str, type[BaseModel]] = {
    "message": Message,
    "tool_call": ToolCall,
    "tool_result": ToolResult,
}
"""内容类型名到模型的映射。用于反序列化对话内容。"""


def _content_to_dict(item: ContentItem) -> dict[str, Any]:
    """把一条对话内容序列化成字典。

    Args:
        item: 内容。

    Returns:
        可 JSON 序列化的字典。
    """
    return item.model_dump(mode="json")


def _content_from_dict(data: Mapping[str, Any]) -> ContentItem:
    """从字典还原一条对话内容。

    Args:
        data: 序列化后的内容。

    Returns:
        内容。

    Raises:
        ValueError: 类型未知。
    """
    kind = data.get("type")
    model = _CONTENT_TYPES.get(str(kind))
    if model is None:
        raise ValueError(f"未知的对话内容类型：{kind!r}")
    return cast(ContentItem, model.model_validate(dict(data)))


@dataclass
class SessionState:
    """可持久化的会话状态快照。

    这是「恢复一个运行所需的全部状态」。少存一项，恢复出来的就不是原来
    那个运行——详见 ADR-0009。
    """

    session_id: str
    """会话标识。"""

    user_id: str | None = None
    """用户标识。"""

    events: list[Event] = field(default_factory=list)
    """完整事件历史。"""

    state: dict[str, Any] = field(default_factory=dict)
    """运行时草稿本。"""

    usage: LLMUsage = field(default_factory=LLMUsage)
    """累计 token 用量。"""

    cost: float = 0.0
    """累计成本。"""

    current_step: int = 0
    """已完成的步数。恢复时的起点。"""

    tool_call_count: int = 0
    """累计工具调用数。"""

    consecutive_errors: int = 0
    """当前连续工具失败次数。"""

    call_counts: dict[str, int] = field(default_factory=dict)
    """各次工具调用的累计次数。"""

    pending_approvals: list[PendingApproval] = field(default_factory=list)
    """等待人工确认的工具调用。非空表示运行处于暂停状态。"""

    created_at: float = 0.0
    """创建时刻（``time.time()`` 基准）。"""

    updated_at: float = 0.0
    """最近更新时刻。"""

    def to_dict(self) -> dict[str, Any]:
        """序列化成可 JSON 化的字典。

        Returns:
            字典。
        """
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "events": [
                {
                    "id": event.id,
                    "execution_id": event.execution_id,
                    "timestamp": event.timestamp,
                    "author": event.author,
                    "contents": [_content_to_dict(item) for item in event.contents],
                }
                for event in self.events
            ],
            "state": self.state,
            "usage": self.usage.model_dump(mode="json"),
            "cost": self.cost,
            "current_step": self.current_step,
            "tool_call_count": self.tool_call_count,
            "consecutive_errors": self.consecutive_errors,
            "call_counts": dict(self.call_counts),
            "pending_approvals": [
                {
                    "tool_call": _content_to_dict(item.tool_call),
                    "confirmation_message": item.confirmation_message,
                }
                for item in self.pending_approvals
            ],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> SessionState:
        """从字典还原。

        Args:
            data: 序列化后的状态。

        Returns:
            会话状态。
        """
        return cls(
            session_id=str(data["session_id"]),
            user_id=data.get("user_id"),
            events=[
                Event(
                    id=str(event["id"]),
                    execution_id=str(event["execution_id"]),
                    timestamp=float(event["timestamp"]),
                    author=str(event["author"]),
                    contents=tuple(_content_from_dict(item) for item in event.get("contents", [])),
                )
                for event in data.get("events", [])
            ],
            state=dict(data.get("state", {})),
            usage=LLMUsage.model_validate(data.get("usage", {})),
            cost=float(data.get("cost", 0.0)),
            current_step=int(data.get("current_step", 0)),
            tool_call_count=int(data.get("tool_call_count", 0)),
            consecutive_errors=int(data.get("consecutive_errors", 0)),
            call_counts={str(k): int(v) for k, v in (data.get("call_counts") or {}).items()},
            pending_approvals=[
                PendingApproval(
                    tool_call=cast(ToolCall, _content_from_dict(item["tool_call"])),
                    confirmation_message=str(item.get("confirmation_message", "")),
                )
                for item in data.get("pending_approvals", [])
            ],
            created_at=float(data.get("created_at", 0.0)),
            updated_at=float(data.get("updated_at", 0.0)),
        )


@runtime_checkable
class SessionStore(Protocol):
    """会话状态存储。

    实现必须**原子**地写入：要么整份状态写进去，要么保持原样。写到一半的
    状态恢复出来是损坏的会话。
    """

    async def load(self, session_id: str) -> SessionState | None:
        """读取一个会话。

        Args:
            session_id: 会话标识。

        Returns:
            会话状态；不存在时返回 None。
        """
        ...

    async def save(self, state: SessionState) -> None:
        """保存一个会话。

        Args:
            state: 会话状态。
        """
        ...

    async def delete(self, session_id: str) -> None:
        """删除一个会话。

        Args:
            session_id: 会话标识。
        """
        ...


class InMemorySessionStore:
    """内存会话存储。用于开发与测试。"""

    def __init__(self) -> None:
        """初始化空存储。"""
        self._states: dict[str, SessionState] = {}

    async def load(self, session_id: str) -> SessionState | None:
        """读取一个会话。

        Args:
            session_id: 会话标识。

        Returns:
            会话状态；不存在时返回 None。
        """
        return self._states.get(session_id)

    async def save(self, state: SessionState) -> None:
        """保存一个会话。

        Args:
            state: 会话状态。
        """
        self._states[state.session_id] = state

    async def delete(self, session_id: str) -> None:
        """删除一个会话。

        Args:
            session_id: 会话标识。
        """
        self._states.pop(session_id, None)


class JsonFileSessionStore:
    """文件会话存储：一个会话一个 JSON 文件。

    写文件时先写临时文件再改名——**改名是原子的**，因此不会出现「写到一半
    的状态」被恢复出来。

    文件 I/O 放到线程里执行，避免阻塞事件循环。
    """

    def __init__(self, directory: str | Path) -> None:
        """初始化存储。

        Args:
            directory: 存放会话文件的目录。不存在时自动创建。
        """
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        """会话对应的文件路径。

        Args:
            session_id: 会话标识。

        Returns:
            路径。
        """
        return self.directory / f"{session_id}.json"

    async def load(self, session_id: str) -> SessionState | None:
        """读取一个会话。

        Args:
            session_id: 会话标识。

        Returns:
            会话状态；不存在时返回 None。
        """
        path = self._path(session_id)
        if not path.exists():
            return None
        raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
        return SessionState.from_dict(json.loads(raw))

    async def save(self, state: SessionState) -> None:
        """保存一个会话。

        Args:
            state: 会话状态。
        """
        path = self._path(state.session_id)
        payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
        temp = path.with_suffix(".json.tmp")
        await asyncio.to_thread(temp.write_text, payload, encoding="utf-8")
        await asyncio.to_thread(temp.replace, path)

    async def delete(self, session_id: str) -> None:
        """删除一个会话。

        Args:
            session_id: 会话标识。
        """
        path = self._path(session_id)
        if path.exists():
            await asyncio.to_thread(path.unlink)


@dataclass
class SessionContext:
    """跨多次运行持续存在的会话。

    典型用法::

        session = SessionContext()
        await session.run(runtime, "帮我学闭包")
        await session.run(runtime, "那什么是装饰器？")

    第二次运行时，模型能看到第一次的完整对话——因为两者共享同一个
    ``ExecutionContext``。
    """

    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    """会话标识。跨多次运行保持不变。"""

    context: ExecutionContext = field(default_factory=ExecutionContext)
    """会话的存储侧：完整对话历史，跨多次运行累积。"""

    turns: int = 0
    """已发起的轮次。

    失败的运行也计数——学习者确实问了一次，只是这次没得到回答。历史里那条
    未被回答的问题会留在 ``context`` 里，因此下一轮模型能看到「上次这里
    没接上」。
    """

    user_id: str | None = None
    """用户标识。"""

    async def run(
        self,
        runtime: AgentRuntime,
        prompt: str | list[ContentItem],
    ) -> AgentResult:
        """在同一会话里执行一轮。

        Args:
            runtime: 执行本次运行的 Agent 运行时。
            prompt: 本轮的用户输入。

        Returns:
            运行结果。它携带的 ``context`` 就是本会话的 ``context``。
        """
        result = await runtime.run(prompt, context=self.context)
        self.turns += 1
        return result

    async def resume(
        self,
        runtime: AgentRuntime,
        approvals: list[ApprovalDecision],
    ) -> AgentResult:
        """恢复一个因等待人工批准而暂停的会话。

        Args:
            runtime: 执行本次运行的 Agent 运行时。
            approvals: 人工决定。

        Returns:
            运行结果。
        """
        result = await runtime.resume(self.context, approvals)
        self.turns += 1
        return result

    @property
    def contents(self) -> list[ContentItem]:
        """会话当前的完整对话内容。"""
        return self.context.contents

    @property
    def state(self) -> dict[str, Any]:
        """会话的草稿本。跨轮次保留。"""
        return self.context.state

    def to_state(self) -> SessionState:
        """导出可持久化的状态快照。

        Returns:
            会话状态。
        """
        import time

        now = time.time()
        ctx = self.context
        return SessionState(
            session_id=self.session_id,
            user_id=self.user_id,
            events=list(ctx.events),
            state=dict(ctx.state),
            usage=ctx.usage,
            cost=ctx.cost,
            current_step=ctx.current_step,
            tool_call_count=ctx.tool_call_count,
            consecutive_errors=ctx.consecutive_errors,
            call_counts=dict(ctx.call_counts),
            pending_approvals=list(ctx.pending_approvals),
            created_at=now,
            updated_at=now,
        )

    @classmethod
    def from_state(cls, state: SessionState) -> SessionContext:
        """从状态快照还原会话。

        Args:
            state: 会话状态。

        Returns:
            会话。
        """
        ctx = ExecutionContext(
            events=list(state.events),
            current_step=state.current_step,
            state=dict(state.state),
            usage=state.usage,
            cost=state.cost,
            tool_call_count=state.tool_call_count,
            consecutive_errors=state.consecutive_errors,
            call_counts=dict(state.call_counts),
            pending_approvals=list(state.pending_approvals),
        )
        return cls(
            session_id=state.session_id,
            context=ctx,
            user_id=state.user_id,
        )

    async def save(self, store: SessionStore) -> None:
        """把会话状态存进存储。

        Args:
            store: 存储。
        """
        await store.save(self.to_state())

    @classmethod
    async def load(
        cls,
        store: SessionStore,
        session_id: str,
        *,
        user_id: str | None = None,
    ) -> SessionContext | None:
        """从存储读取会话。

        Args:
            store: 存储。
            session_id: 会话标识。
            user_id: 用户标识。读取到的会话没有用户标识时用它补上。

        Returns:
            会话；不存在时返回 None。
        """
        state = await store.load(session_id)
        if state is None:
            return None
        session = cls.from_state(state)
        if session.user_id is None:
            session.user_id = user_id
        return session


__all__ = [
    "InMemorySessionStore",
    "JsonFileSessionStore",
    "SessionContext",
    "SessionState",
    "SessionStore",
]
