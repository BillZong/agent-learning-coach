"""RAG 侧的回调。

主教材 5.5.4 用「工具回调」压缩检索结果：结果太长时重新切分、重新检索，只留
最相关的几段。

本项目的检索工具返回的是**已经排好序的离散切片**，因此不需要重新切分、重新
检索——按相关度从高到低装进预算即可。本模块实现两个真实用得上的回调：

- ``RetrievalCache``：**执行前**的短路钩子。同一个查询重复检索时直接返回缓存
  的结果，不碰向量库。
- ``SearchResultCompressor``：**执行后**的替换钩子。结果超过预算时按相关度
  截断。

**缓存为什么放在执行前**：它要回答的是「这次调用还需要真的执行吗」。放在执行
后意味着工具已经查完了，缓存省不下任何东西——只是把结果换掉。

**压缩为什么放在执行后**：压缩的对象是**已经产生的结果**，它需要看到结果有多
长、里面有什么，才能决定压不压、压成什么样。

**两个回调都不破坏事务语义**：执行前的钩子短路时，仍然产出恰好一条结果（它
就是那条结果）；执行后的钩子替换结果时，仍然只有一条。**一条 tool_call 恰好
对应一条 tool_result**——这是运行时最硬的不变量。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from agent_learning_coach.core.tools.types import ToolContext, ToolOutcome


def _canonical(value: Any) -> str:
    """把参数值规范成稳定的字符串。

    **键的稳定性比键的可读性重要**：两个语义相同的参数必须得到同一个键
    （否则是缓存未命中，不是错答案）；两个语义不同的参数必须得到不同的键
    （否则是错答案）。

    Args:
        value: 任意参数值。

    Returns:
        规范化的字符串。映射按键排序，序列保序。
    """
    if isinstance(value, Mapping):
        items = ",".join(f"{key!r}:{_canonical(item)}" for key, item in sorted(value.items()))
        return "{" + items + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical(item) for item in value) + "]"
    return repr(value)


@dataclass
class RetrievalCache:
    """检索结果缓存（执行前钩子）。

    **只缓存成功的检索**。失败的检索不缓存——否则一次偶发失败会被后续所有
    同查询复用，把瞬时故障变成持续故障。

    缓存键是「工具名 + 全部参数」。**结果由哪些参数决定，键就得包含哪些**——
    少一个维度就会串味：同一个查询但 top_k 不同、或过滤条件不同，串起来给的是
    **错答案**，比缓存未命中严重得多。

    （这条不是假设。原来的键只含「查询 + 过滤」，漏了 top_k：先查 top_k=1
    再查 top_k=3，第二次直接拿回了第一条的结果——返回条数是错的。）
    """

    max_entries: int = 32
    """最多缓存多少条。超出后按插入顺序淘汰最旧的。"""

    _entries: dict[str, ToolOutcome] = field(default_factory=dict)

    def key(self, tool_name: str, arguments: Mapping[str, Any]) -> str:
        """构造缓存键。

        Args:
            tool_name: 工具名。
            arguments: 工具参数。

        Returns:
            缓存键。
        """
        return f"{tool_name}|{_canonical(arguments)}"

    def __call__(
        self,
        context: ToolContext,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> ToolOutcome | None:
        """命中则返回缓存结果，否则放行。

        Args:
            context: 运行时上下文。
            tool_name: 工具名。
            arguments: 已通过校验的参数。

        Returns:
            缓存的结果；未命中返回 None。
        """
        cached = self._entries.get(self.key(tool_name, arguments))
        if cached is None:
            return None
        return ToolOutcome(
            tool_name=cached.tool_name,
            status=cached.status,
            content=cached.content,
            data=cached.data,
            duration_ms=0.0,
            metadata={**cached.metadata, "cache_hit": True},
        )

    def store(
        self,
        context: ToolContext,
        outcome: ToolOutcome,
        arguments: Mapping[str, Any],
    ) -> None:
        """把一次成功的结果存进缓存。

        签名与 ``AfterToolHook`` 一致，因此可以直接交给调度器当钩子用。

        Args:
            context: 运行时上下文。
            outcome: 工具结果。
            arguments: 这次调用的参数。用于构造缓存键。
        """
        if not outcome.succeeded:
            return
        if len(self._entries) >= self.max_entries:
            oldest = next(iter(self._entries))
            del self._entries[oldest]
        self._entries[self.key(outcome.tool_name, arguments)] = outcome

    def __len__(self) -> int:
        """缓存条数。"""
        return len(self._entries)

    def clear(self) -> None:
        """清空缓存。"""
        self._entries.clear()


@dataclass
class SearchResultCompressor:
    """检索结果压缩（执行后钩子）。

    **按相关度截断**：结果里已经有排好序的切片，从最相关的开始装，装满预算
    为止。比主教材的「重新切分 + 重新检索」更简单——我们的检索工具返回的就是
    离散切片，重新切分没有必要。

    **只在结果确实超预算时才动手**：短结果压缩没有收益，还要冒「截断掉关键
    内容」的风险。

    压缩后 ``data`` 里的命中列表同步截断——否则结构化数据与文本对不上，
    引用校验会拿不到被引用的那几段。
    """

    max_chars: int = 1200
    """超过这个长度就压缩。"""

    def __call__(
        self,
        context: ToolContext,
        outcome: ToolOutcome,
        arguments: Mapping[str, Any],
    ) -> ToolOutcome | None:
        """必要时压缩结果。

        Args:
            context: 运行时上下文。
            outcome: 工具结果。
            arguments: 这次调用的参数。

        Returns:
            压缩后的结果；不需要压缩时返回 None。
        """
        if not outcome.succeeded or len(outcome.content) <= self.max_chars:
            return None

        results = (outcome.data or {}).get("results")
        if not isinstance(results, list) or not results:
            return None

        kept: list[dict[str, Any]] = []
        used = 0
        for item in results:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", ""))
            if used + len(text) > self.max_chars and kept:
                break
            kept.append(item)
            used += len(text)
        if not kept or len(kept) == len(results):
            return None

        data = outcome.data or {}
        kept_ids = {item.get("chunk_id") for item in kept}
        citations = [
            citation
            for citation in data.get("citations", [])
            if isinstance(citation, dict) and citation.get("chunk_id") in kept_ids
        ]
        lines = [f"找到 {len(kept)} 条相关内容：", ""]
        for citation in citations:
            lines.append(f"[{citation.get('marker')}] {citation.get('text')}")
            suffix = ""
            score = citation.get("score")
            if score is not None:
                suffix = f"，相关度 {float(score):.3f}"
            lines.append(f"（来源：{citation.get('source')}#{citation.get('index')}{suffix}）")
            lines.append("")
        lines.append("引用时请使用 [1]、[2] 这样的标记。")

        return ToolOutcome(
            tool_name=outcome.tool_name,
            status=outcome.status,
            content="\n".join(lines),
            data={**data, "citations": citations, "results": kept},
            duration_ms=outcome.duration_ms,
            metadata={**outcome.metadata, "compressed": True},
        )


__all__ = ["RetrievalCache", "SearchResultCompressor"]
