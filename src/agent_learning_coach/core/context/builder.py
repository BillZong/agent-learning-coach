"""上下文构建器。

**存储与呈现分离**（主教材 6.2.1）是这一层的全部要点：

- **存储**：执行上下文里的完整历史。它永远是完整的——裁剪不会删掉任何
  发生过的事，事后审计与回放因此不受影响。
- **呈现**：这一次请求实际发给模型的内容。它受预算约束，会被裁剪。

两者分开带来三个直接好处：

1. **裁剪不丢信息**。模型这一轮看不到的内容，在上下文里还在；下一轮预算
   放宽时它可以重新出现。
2. **裁剪策略可以替换**。换一套策略不影响存储，也不影响别的调用方。
3. **可以观测**。构建结果携带 token 数与丢弃条目数，因此「为什么这次请求
   变短了」有答案。

**预算不是建议**。构建器依次应用策略；若仍超预算，则从最新的一条往回保留，
装不下的那条**裁掉它的文本**；只有连文本都裁不动时（工具调用的参数是结构化
的，截断 JSON 会让参数失效）才如实标记 ``over_budget``。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from agent_learning_coach.core.context.strategies import (
    TRUNCATION_MARKER,
    ContextStrategy,
    drop_unpaired,
)
from agent_learning_coach.core.context.tokens import TokenCounter
from agent_learning_coach.core.llm.models import ToolSpec
from agent_learning_coach.core.llm.types import ContentItem, Message, ToolResult


@dataclass(frozen=True)
class ContextView:
    """一次构建的结果。

    它既是「发给模型的内容」，也是「这次裁剪做了什么」的记录。
    """

    contents: tuple[ContentItem, ...]
    """裁剪后的内容。"""

    original_tokens: int
    """裁剪前的 token 数。"""

    final_tokens: int
    """裁剪后的 token 数。"""

    dropped_items: int
    """被丢弃的条目数。"""

    applied: tuple[str, ...]
    """实际应用的策略名，按应用顺序。"""

    truncated: bool = False
    """是否有内容被**截断**。

    只有「单条内容就超过预算」时才会发生——此时该条文本被裁到预算内。
    """

    over_budget: bool = False
    """是否仍然超预算。

    只有「裁不动」时才会为 True——工具调用的参数是结构化的，截断 JSON 会让
    参数失效，因此宁可如实报告，也不要发出一个会被 API 拒绝的请求。
    """

    @property
    def saved_tokens(self) -> int:
        """节省的 token 数。"""
        return max(0, self.original_tokens - self.final_tokens)


@dataclass(frozen=True)
class ContextBuilder:
    """把完整历史投影成受预算约束的模型视图。

    典型用法::

        builder = ContextBuilder(
            max_tokens=8000,
            strategies=(Compaction(), SlidingWindow(keep_recent=20)),
        )
        view = builder.build(contents, tools=tool_specs)
    """

    max_tokens: int | None = None
    """单次请求的上下文预算（token）。为 None 表示不限制。"""

    strategies: tuple[ContextStrategy, ...] = ()
    """裁剪策略，按顺序应用。"""

    counter: TokenCounter = field(default_factory=TokenCounter)
    """token 计数器。"""

    def build(
        self,
        contents: Sequence[ContentItem],
        *,
        tools: Sequence[ToolSpec] | None = None,
        model: str | None = None,
    ) -> ContextView:
        """构建一次请求要发送的上下文视图。

        Args:
            contents: 完整历史。
            tools: 工具定义。它们的开销计入预算。
            model: 模型名。用于在计数器未指定模型时选择分词器。

        Returns:
            视图与统计。
        """
        counter = self._counter_for(model)
        original = counter.count(contents, tools=tools)
        total = len(contents)

        if self.max_tokens is None or original <= self.max_tokens:
            return ContextView(
                contents=tuple(contents),
                original_tokens=original,
                final_tokens=original,
                dropped_items=0,
                applied=(),
            )

        current = list(contents)
        applied: list[str] = []
        for strategy in self.strategies:
            current = strategy.apply(current)
            applied.append(strategy.name)
            if counter.count(current, tools=tools) <= self.max_tokens:
                break

        final = counter.count(current, tools=tools)
        truncated = False
        if final > self.max_tokens:
            current, final, truncated = self._trim_to_budget(
                current,
                tools,
                counter,
                self.max_tokens,
            )

        return ContextView(
            contents=tuple(current),
            original_tokens=original,
            final_tokens=final,
            dropped_items=max(0, total - len(current)),
            applied=tuple(applied),
            truncated=truncated,
            over_budget=final > self.max_tokens,
        )

    def _counter_for(self, model: str | None) -> TokenCounter:
        """选择本次使用的计数器。

        用户显式指定了模型的计数器时以它为准；否则按本次请求的模型选一个。

        Args:
            model: 本次请求的模型名。

        Returns:
            计数器。
        """
        if model is None or self.counter.model is not None:
            return self.counter
        return TokenCounter(model)

    def _trim_to_budget(
        self,
        contents: Sequence[ContentItem],
        tools: Sequence[ToolSpec] | None,
        counter: TokenCounter,
        max_tokens: int,
    ) -> tuple[list[ContentItem], int, bool]:
        """保留能装下的最新内容，必要时裁剪它们的文本。

        从**最新的一条往回**保留——最新的内容最相关。某条装不下时裁它的
        文本（消息与工具结果可以裁；工具调用的参数是结构化的，裁不动），
        裁不动就跳过。

        保留完成后要修复配对：丢掉一条 tool_call 就必须丢掉它的 tool_result，
        反之亦然。配对修复只会**减少**内容，因此不会让结果重新超预算。

        Args:
            contents: 当前内容。
            tools: 工具定义。
            counter: token 计数器。
            max_tokens: 预算上限（字符或 token，由计数器决定）。

        Returns:
            裁剪后的内容、token 数、是否发生了截断。
        """
        tool_tokens = counter.count_tools(tools) if tools else 0
        budget = max_tokens - tool_tokens

        kept: list[ContentItem] = []
        used = 0
        truncated = False
        for item in reversed(contents):
            cost = counter.count([item])
            if used + cost <= budget:
                kept.append(item)
                used += cost
                continue
            allowance = budget - used
            if allowance <= 0:
                continue
            fitted, tokens, did_truncate = self._fit_single_item(item, tools, counter, allowance)
            if did_truncate:
                kept.append(fitted)
                used += tokens
                truncated = True

        kept.reverse()
        result = drop_unpaired(kept)
        return result, counter.count(result, tools=tools), truncated

    def _fit_single_item(
        self,
        item: ContentItem,
        tools: Sequence[ToolSpec] | None,
        counter: TokenCounter,
        budget: int,
    ) -> tuple[ContentItem, int, bool]:
        """把单条内容裁到给定预算内。

        只裁文本型内容（消息、工具结果）。工具调用裁不动——它的参数是结构化
        的，截断 JSON 会让参数失效，模型下一次调用就会传错参数。

        Args:
            item: 待裁剪的内容。
            tools: 工具定义。
            counter: token 计数器。
            budget: 这条内容可用的 token 额度。

        Returns:
            裁剪后的内容、token 数、是否发生了截断。
        """
        text = _item_text(item)
        if text is None:
            return item, counter.count([item], tools=tools), False

        length = len(text)
        while length > 0:
            candidate = _with_text(item, text[:length] + TRUNCATION_MARKER)
            tokens = counter.count([candidate], tools=tools)
            if tokens <= budget:
                return candidate, tokens, True
            length //= 2

        candidate = _with_text(item, "")
        return candidate, counter.count([candidate], tools=tools), True


def _item_text(item: ContentItem) -> str | None:
    """取出一条内容里的可裁剪文本。

    Args:
        item: 内容。

    Returns:
        文本；该内容没有可裁剪文本时返回 None。
    """
    if isinstance(item, Message):
        return item.content
    if isinstance(item, ToolResult):
        return item.content
    return None


def _with_text(item: ContentItem, text: str) -> ContentItem:
    """替换一条内容里的文本。

    Args:
        item: 内容。
        text: 新文本。

    Returns:
        替换后的内容。
    """
    return item.model_copy(update={"content": text})


__all__ = ["ContextBuilder", "ContextView"]
