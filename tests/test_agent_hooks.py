"""``StateCollector`` 的测试。

**这个钩子以前没有测试**——它只在示例里被用到。钩子是「工具结果 → 执行上下文
草稿本」的唯一搬运工，格式由调用方约定，但**搬运这件事本身**必须可靠：丢一条
引用，应用层就以为模型没引用过。
"""

from __future__ import annotations

from agent_learning_coach.core.agent.hooks import StateCollector
from agent_learning_coach.core.agent.types import EXECUTION_CONTEXT_KEY, ExecutionContext
from agent_learning_coach.core.tools.types import ToolContext, ToolOutcome


def _context(execution: ExecutionContext | None = None) -> ToolContext:
    """构造带（或不带）执行上下文的工具上下文。

    Args:
        execution: 执行上下文。为 None 时不带——模拟「不在运行里」的调用。

    Returns:
        工具上下文。
    """
    metadata = {} if execution is None else {EXECUTION_CONTEXT_KEY: execution}
    return ToolContext(metadata=metadata)


def _outcome(status: str = "success", data: dict[str, object] | None = None) -> ToolOutcome:
    """构造一个工具结果。

    Args:
        status: 结果状态。
        data: 结果数据。

    Returns:
        工具结果。
    """
    return ToolOutcome(tool_name="search", status=status, content="", data=data)


def test_collector_appends_a_list_item_by_item() -> None:
    """列表逐条追加——多条引用不该被塞成一个嵌套列表。"""
    execution = ExecutionContext()
    collector = StateCollector("citations", data_key="citations")

    collector(_context(execution), _outcome(data={"citations": [1, 2]}), {})

    assert execution.state["citations"] == [1, 2]


def test_collector_appends_a_scalar_as_one_item() -> None:
    """标量整体追加。"""
    execution = ExecutionContext()
    collector = StateCollector("citations", data_key="citations")

    collector(_context(execution), _outcome(data={"citations": {"marker": "1"}}), {})

    assert execution.state["citations"] == [{"marker": "1"}]


def test_collector_accumulates_across_calls() -> None:
    """多次调用累积到同一个键。"""
    execution = ExecutionContext()
    collector = StateCollector("citations", data_key="citations")

    collector(_context(execution), _outcome(data={"citations": [1]}), {})
    collector(_context(execution), _outcome(data={"citations": [2]}), {})

    assert execution.state["citations"] == [1, 2]


def test_collector_ignores_a_failed_result() -> None:
    """失败的结果没有可信数据，不该被收集。"""
    execution = ExecutionContext()
    collector = StateCollector("citations", data_key="citations")

    collector(_context(execution), _outcome(status="error", data={"citations": [1]}), {})

    assert "citations" not in execution.state


def test_collector_ignores_a_result_without_the_key() -> None:
    """结果里没有约定的键就不收集。"""
    execution = ExecutionContext()
    collector = StateCollector("citations", data_key="citations")

    collector(_context(execution), _outcome(data={"other": [1]}), {})

    assert "citations" not in execution.state


def test_collector_does_nothing_without_an_execution_context() -> None:
    """不在运行里调用时不收集——草稿本只属于某一次执行。"""
    collector = StateCollector("citations", data_key="citations")

    assert collector(_context(), _outcome(data={"citations": [1]}), {}) is None


def test_collector_replaces_a_non_list_state() -> None:
    """已有的值不是列表时，重建为列表——收集器只追加，不合并异构数据。"""
    execution = ExecutionContext()
    execution.state["citations"] = "旧值"
    collector = StateCollector("citations", data_key="citations")

    collector(_context(execution), _outcome(data={"citations": [1]}), {})

    assert execution.state["citations"] == [1]
