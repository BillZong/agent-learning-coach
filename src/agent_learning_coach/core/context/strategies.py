"""上下文裁剪策略。

两种策略解决两个不同的问题：

- **Sliding Window**（滑动窗口）处理**条数**：对话轮次太多，按时间丢掉最老
  的部分。它丢的是**内容**，因此必须小心维护「每条 tool_call 都有对应的
  tool_result」这条不变量——主流厂商 API 会直接拒绝配对断裂的请求。
- **Compaction**（压缩）处理**体积**：单个工具结果或参数可能有几万字符，
  把它换成占位符。它换的是**内容**而不是条目，因此天然保持配对。

两者可以叠加：先压缩（保住更多条目），仍超预算再滑窗（丢掉更老的条目）。

**策略只改「呈现」，不改「存储」**（主教材 6.2.1 Separating storage from
presentation）。历史永远完整保留在执行上下文里，裁剪只发生在「这一次请求
发给模型什么」这一层。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agent_learning_coach.core.llm.types import ContentItem, Message, ToolCall, ToolResult

TRUNCATION_MARKER = "…[已截断]"
"""截断标记。放在内容末尾，让模型知道这里不是完整内容。"""


@runtime_checkable
class ContextStrategy(Protocol):
    """上下文裁剪策略。

    实现必须是**纯函数**：只依赖入参，不修改入参，不产生副作用。同一个输入
    永远得到同一个输出——否则同一份历史在不同轮次会得到不同的呈现，模型
    的行为将无法复现。
    """

    @property
    def name(self) -> str:
        """策略名。用于观测：记录一次构建应用了哪些策略。

        **只读**：实现多半是 frozen dataclass，协议不该要求一个可写属性。
        """
        ...

    def apply(self, contents: Sequence[ContentItem]) -> list[ContentItem]:
        """对对话内容做裁剪。

        Args:
            contents: 完整的对话内容。

        Returns:
            裁剪后的内容。实现**不应**原地修改入参。
        """
        ...


def drop_unpaired(contents: Sequence[ContentItem]) -> list[ContentItem]:
    """移除没有配对的 tool_call 与 tool_result。

    配对断裂有两种，处置不同：

    - **孤立的 tool_call**（有调用无结果）：丢弃调用。调用方在等一个结果，
      而结果不在这次请求里。
    - **孤立的 tool_result**（有结果无调用）：丢弃结果。厂商 API 不接受
      没有对应调用的结果。

    两种都必须丢弃——留下任何一个，请求都会被拒绝。

    Args:
        contents: 内容列表。

    Returns:
        配对完整的内容列表。
    """
    result_ids = {item.tool_call_id for item in contents if isinstance(item, ToolResult)}
    call_ids = {item.tool_call_id for item in contents if isinstance(item, ToolCall)}
    kept: list[ContentItem] = []
    for item in contents:
        if isinstance(item, ToolCall) and item.tool_call_id not in result_ids:
            continue
        if isinstance(item, ToolResult) and item.tool_call_id not in call_ids:
            continue
        kept.append(item)
    return kept


@dataclass(frozen=True)
class SlidingWindow:
    """滑动窗口：只保留最近若干条内容。

    默认还会保留**最早的那条用户消息**——它通常是这次会话的原始诉求
    （「帮我学闭包」）。丢掉它，模型就不知道自己该解决什么问题；留着它，
    即使中间的对话被裁掉，任务边界仍然清楚。

    这个取舍对学习教练尤其重要：原始目标始终有效，而中间轮次的内容可以
    由压缩策略保留要点。
    """

    keep_recent: int = 20
    """保留的最近条目数。"""

    keep_leading_user: bool = True
    """是否保留最早的用户消息。"""

    name: str = "sliding_window"
    """策略名。"""

    def apply(self, contents: Sequence[ContentItem]) -> list[ContentItem]:
        """按窗口裁剪。

        Args:
            contents: 完整内容。

        Returns:
            裁剪后的内容，保证 tool_call 与 tool_result 仍然配对。
        """
        if self.keep_recent <= 0:
            return []
        if len(contents) <= self.keep_recent:
            return list(contents)

        head_end = 0
        if self.keep_leading_user:
            for index, item in enumerate(contents):
                if isinstance(item, Message) and item.role == "user":
                    head_end = index + 1
                    break

        tail = contents[head_end:]
        if len(tail) > self.keep_recent:
            tail = tail[-self.keep_recent :]
        kept = list(contents[:head_end]) + list(tail)
        return drop_unpaired(kept)


@dataclass(frozen=True)
class CompactionRules:
    """压缩规则。

    规则分两类：

    - **按工具定制的模板**：``result_templates`` 指定某个工具的结果换成什么
      文本（例如「文件内容已读取，需要时重新读取」）；``argument_placeholders``
      指定某个工具的哪些参数换成占位符（例如把 ``create_file`` 的 ``content``
      换成「已保存到文件」）。
    - **通用阈值**：超过 ``max_result_chars`` 的结果与超过
      ``max_argument_chars`` 的参数一律截断。这条兜底很重要——规则表不可能
      列全所有工具，而一个超大的返回值足以撑爆整个窗口。
    """

    max_result_chars: int = 2000
    """工具结果的最大字符数。超过则截断。"""

    max_argument_chars: int = 1000
    """工具参数的最大字符数。超过则截断。"""

    result_templates: Mapping[str, str] = field(default_factory=dict)
    """工具名 → 结果模板。模板里可以用该次调用的参数做占位符。"""

    argument_placeholders: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    """工具名 → (参数名 → 占位符文本)。"""

    protected_tools: frozenset[str] = field(default_factory=frozenset)
    """受保护的工具名。

    这些工具的调用参数与结果**不参与压缩**。用于结构化输出时应当把
    ``final_answer`` 放进来——它是最终产物，截断它等于丢掉答案。
    """


@dataclass(frozen=True)
class Compaction:
    """压缩策略：把臃肿的调用参数与工具结果换成占位符。"""

    rules: CompactionRules = field(default_factory=CompactionRules)
    """压缩规则。"""

    name: str = "compaction"
    """策略名。"""

    def apply(self, contents: Sequence[ContentItem]) -> list[ContentItem]:
        """压缩调用参数与工具结果。

        Args:
            contents: 完整内容。

        Returns:
            压缩后的内容。条目本身不增不减，因此配对关系不变。
        """
        arguments_by_call: dict[str, dict[str, Any]] = {}
        for item in contents:
            if isinstance(item, ToolCall):
                arguments_by_call[item.tool_call_id] = item.arguments

        compacted: list[ContentItem] = []
        for item in contents:
            if isinstance(item, ToolCall):
                compacted.append(self._compact_call(item))
            elif isinstance(item, ToolResult):
                compacted.append(
                    self._compact_result(item, arguments_by_call.get(item.tool_call_id, {}))
                )
            else:
                compacted.append(item)
        return compacted

    def _compact_call(self, call: ToolCall) -> ToolCall:
        """压缩一次工具调用的参数。

        Args:
            call: 工具调用。

        Returns:
            压缩后的调用。
        """
        if call.name in self.rules.protected_tools:
            return call
        placeholders = self.rules.argument_placeholders.get(call.name, {})
        arguments: dict[str, Any] = {}
        for key, value in call.arguments.items():
            if key in placeholders:
                arguments[key] = placeholders[key]
            else:
                arguments[key] = _truncate_value(value, self.rules.max_argument_chars)
        if arguments == call.arguments:
            return call
        return call.model_copy(update={"arguments": arguments})

    def _compact_result(self, result: ToolResult, arguments: Mapping[str, Any]) -> ToolResult:
        """压缩一条工具结果。

        Args:
            result: 工具结果。
            arguments: 该次调用的原始参数。模板里的占位符用它填充。

        Returns:
            压缩后的结果。
        """
        if result.name in self.rules.protected_tools:
            return result
        template = self.rules.result_templates.get(result.name)
        if template is not None:
            content = _safe_format(template, arguments)
        else:
            content = _truncate_text(result.content, self.rules.max_result_chars)
        if content == result.content:
            return result
        return result.model_copy(update={"content": content})


class _MissingAsUnknown(dict[str, Any]):
    """缺失键返回 ``"unknown"`` 的字典。

    模板里写了某个参数名，但这次调用没有这个参数——此时不应抛异常中断
    压缩流程，填一个占位值继续即可。
    """

    def __missing__(self, key: str) -> str:
        """返回缺失键的占位值。

        Args:
            key: 缺失的键。

        Returns:
            占位文本。
        """
        return "unknown"


def _safe_format(template: str, values: Mapping[str, Any]) -> str:
    """按参数填充模板，失败时退回通用文本。

    Args:
        template: 模板文本。
        values: 可用参数。

    Returns:
        填充后的文本。
    """
    try:
        return template.format_map(_MissingAsUnknown(values))
    except Exception:
        # 模板本身写坏了（例如带 {a.b} 这种嵌套占位符）——用通用文本兜住，
        # 不能让压缩失败中断整个运行。
        return "[结果已压缩]"


def _truncate_text(text: str, limit: int) -> str:
    """截断过长文本。

    Args:
        text: 文本。
        limit: 最大字符数。小于等于 0 表示不限制。

    Returns:
        截断后的文本。
    """
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + TRUNCATION_MARKER


def _truncate_value(value: Any, limit: int) -> Any:
    """截断过长的参数值。

    字符串直接截断；字典与列表先序列化成 JSON 再截断——它们的体积同样可能
    失控，但不是文本，不能直接切片。

    Args:
        value: 参数值。
        limit: 最大字符数。小于等于 0 表示不限制。

    Returns:
        截断后的值。类型可能改变（dict/list 变成字符串）。
    """
    if limit <= 0:
        return value
    if isinstance(value, str):
        return _truncate_text(value, limit)
    if isinstance(value, (dict, list)):
        serialized = json.dumps(value, ensure_ascii=False)
        if len(serialized) <= limit:
            return value
        return _truncate_text(serialized, limit)
    return value


__all__ = [
    "TRUNCATION_MARKER",
    "Compaction",
    "CompactionRules",
    "ContextStrategy",
    "SlidingWindow",
    "drop_unpaired",
]
