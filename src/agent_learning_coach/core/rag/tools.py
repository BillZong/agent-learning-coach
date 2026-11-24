"""把检索接进工具层。

Agent 不会「知道」某个问题的答案在哪份材料的哪一段——它需要**一个能调用的
工具**。本模块把 Day 11 的检索能力包装成两个工具：

- ``SearchCourseMaterialTool``：**语义检索**。问「闭包是什么」，它按内容
  相似度找相关的切片。
- ``RetrieveKnowledgeTool``：**结构化检索**。问「闭包.md 里都写了什么」，
  它按来源取回切片。

两个工具**互补而非重复**：语义检索回答「哪些内容与问题相关」，结构化检索回答
「这份材料里有什么」。后者不经过嵌入，因此不受嵌入质量影响——当用户明确知道
要看哪份材料时，结构化检索更准也更便宜。

**两者都产出引用**：结果里带 ``[1]``、``[2]`` 这样的标记，模型在答案里复述
这些标记即可形成可追溯的引用。标记由 ``CitationRegistry`` 分配——**同一个切片
永远拿到同一个标记**，因此 ``[1]`` 在整轮对话里指代同一段文字。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_learning_coach.core.rag.citation import Citation, CitationRegistry
from agent_learning_coach.core.rag.retrieval import VectorRetriever
from agent_learning_coach.core.rag.store import VectorStore
from agent_learning_coach.core.rag.types import Chunk, ScoredChunk
from agent_learning_coach.core.tools.types import ToolContext, ToolDefinition, ToolOutcome


class _CitationTool:
    """产出引用的工具的共同部分。

    子类负责**怎么找**，这里负责**找到之后怎么呈现**：

    - 给每个切片分配稳定标记；
    - 渲染模型能读的文本（带标记）；
    - 把结构化的引用放进 ``ToolOutcome.data``，供程序消费与事后校验。
    """

    def __init__(self) -> None:
        """初始化工具。"""
        self._registry = CitationRegistry()

    def _build_outcome(
        self,
        tool_name: str,
        chunks: list[Chunk],
        scores: list[float | None],
        *,
        empty_message: str,
    ) -> ToolOutcome:
        """把切片渲染成工具结果。

        Args:
            tool_name: 工具名。
            chunks: 命中的切片。
            scores: 对应的相关度。None 表示没有相关度。
            empty_message: 没有命中时的提示。

        Returns:
            归一化结果。``data`` 里带引用与命中详情。
        """
        if not chunks:
            return ToolOutcome(
                tool_name=tool_name,
                status="success",
                content=empty_message,
                data={"citations": [], "results": []},
            )

        citations: list[Citation] = [
            self._registry.build(chunk, score=score)
            for chunk, score in zip(chunks, scores, strict=True)
        ]
        lines = [f"找到 {len(chunks)} 条相关内容：", ""]
        for citation in citations:
            lines.append(f"[{citation.marker}] {citation.text}")
            suffix = "" if citation.score is None else f"，相关度 {citation.score:.3f}"
            lines.append(f"（来源：{citation.source}#{citation.index}{suffix}）")
            lines.append("")
        lines.append("引用时请使用 [1]、[2] 这样的标记。")

        return ToolOutcome(
            tool_name=tool_name,
            status="success",
            content="\n".join(lines),
            data={
                "citations": [citation.to_dict() for citation in citations],
                "results": [
                    {
                        "chunk_id": citation.chunk_id,
                        "document_id": citation.document_id,
                        "source": citation.source,
                        "index": citation.index,
                        "score": citation.score,
                        "text": citation.text,
                    }
                    for citation in citations
                ],
            },
        )


class SearchCourseMaterialTool(_CitationTool):
    """在课程材料里做语义检索。

    给 Agent 用的「翻教材」工具：它按内容相似度找相关切片，并把结果渲染成
    带引用标记的文本。

    **它不自己决定检索什么**——查询由模型给出。这是工具与 Agent 的分工：
    模型知道用户在问什么，工具知道材料里有什么。
    """

    def __init__(
        self,
        retriever: VectorRetriever,
        *,
        top_k: int = 5,
        name: str = "search_course_material",
        min_score: float | None = None,
    ) -> None:
        """初始化工具。

        Args:
            retriever: 向量检索器。
            top_k: 默认返回几条。
            name: 工具名。
            min_score: 相似度下限。低于它的结果被丢弃。

            **没有通用默认值**：相似度的分布随嵌入模型而变，写死一个阈值要么
            过滤掉全部，要么形同虚设。由调用方按自己的嵌入模型设定。
        """
        super().__init__()
        self.retriever = retriever
        self.top_k = top_k
        self._name = name
        self.min_score = min_score

    @property
    def definition(self) -> ToolDefinition:
        """工具的静态契约。"""
        return ToolDefinition(
            name=self._name,
            description=(
                "在课程材料中做语义检索，返回与问题最相关的段落。"
                "结果带有 [1]、[2] 这样的引用标记，回答时引用它们。"
                "如果没找到相关内容，会明确说明没有找到。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要检索的问题或关键词",
                        "minLength": 1,
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回几条结果",
                        "minimum": 1,
                        "maximum": 20,
                    },
                    "filters": {
                        "type": "object",
                        "description": '元数据等值过滤，例如 {"topic": "函数"}',
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            scopes=("rag:read",),
        )

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolOutcome:
        """执行检索。

        Args:
            context: 运行时上下文。
            arguments: 已通过校验的参数。

        Returns:
            带引用标记的检索结果。
        """
        query = str(arguments.get("query", ""))
        top_k = int(arguments.get("top_k", self.top_k))
        filters = arguments.get("filters")
        hits: list[ScoredChunk] = await self.retriever.retrieve(
            query,
            top_k=top_k,
            filters=filters if isinstance(filters, Mapping) else None,
        )
        if self.min_score is not None:
            hits = [hit for hit in hits if hit.score >= self.min_score]
        return self._build_outcome(
            self._name,
            [hit.chunk for hit in hits],
            [hit.score for hit in hits],
            empty_message=f"没有找到与「{query}」相关的材料。",
        )


class RetrieveKnowledgeTool(_CitationTool):
    """按来源取回课程材料的内容。

    **结构化检索**：用户明确知道要看哪份材料时用它——「闭包.md 里都写了什么」。
    它不经过嵌入，因此不受嵌入质量影响；返回顺序是材料的自然阅读顺序。
    """

    def __init__(
        self,
        store: VectorStore,
        *,
        name: str = "retrieve_knowledge",
        limit: int = 50,
    ) -> None:
        """初始化工具。

        Args:
            store: 向量存储（也承载了按来源取回的能力）。
            name: 工具名。
            limit: 单次最多取回几条。
        """
        super().__init__()
        self.store = store
        self._name = name
        self.limit = limit

    @property
    def definition(self) -> ToolDefinition:
        """工具的静态契约。"""
        return ToolDefinition(
            name=self._name,
            description=(
                "按来源取回课程材料的内容（结构化检索）。"
                "已知材料文件名时用它，能拿到完整的段落与自然顺序。"
                "结果同样带引用标记。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "来源文件名或路径片段，例如 closures.md",
                        "minLength": 1,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多取回几条",
                        "minimum": 1,
                        "maximum": 100,
                    },
                },
                "required": ["source"],
                "additionalProperties": False,
            },
            scopes=("rag:read",),
        )

    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> ToolOutcome:
        """执行取回。

        Args:
            context: 运行时上下文。
            arguments: 已通过校验的参数。

        Returns:
            带引用标记的材料内容；来源不存在时返回错误结果并列出可用来源。
        """
        source = str(arguments.get("source", ""))
        limit = int(arguments.get("limit", self.limit))
        chunks = await self.store.fetch(source, limit=limit)
        if not chunks:
            available = await self.store.sources()
            listed = "、".join(available) if available else "（暂无材料）"
            return ToolOutcome(
                tool_name=self._name,
                status="error",
                content=(
                    f"没有找到来源包含「{source}」的材料。可用来源：{listed}。请换一个来源再试。"
                ),
                error=f"source_not_found: {source}",
                data={"citations": [], "results": [], "available_sources": available},
            )
        return self._build_outcome(
            self._name,
            chunks,
            [None] * len(chunks),
            empty_message=f"来源「{source}」里没有内容。",
        )


__all__ = ["RetrieveKnowledgeTool", "SearchCourseMaterialTool"]
