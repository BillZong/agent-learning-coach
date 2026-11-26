"""向量检索。

检索器是 RAG 流水线的**读端**：把查询变成嵌入，向存储要最相关的切片。

它刻意保持很薄——只有「嵌入 → 检索」两步。**没有重排、没有查询改写、
没有多路召回**。那些是检索质量的下一步，而每加一层就多一个「哪一层先失效」
的排查对象。基础检索先跑对，再谈优化。

**过滤下推到存储层**：检索器把元数据过滤条件原样传给存储，由数据库用索引
筛。在应用层过滤等于把全部向量拉回内存再筛——那是「索引 ≠ 检索」最常见的误用。

**top_k 是「几份材料」，不是「几片」。** 检索结果按**来源**去重（默认每份来源只留
最相关的一片），理由是：相邻切片的**内容高度重叠**（切分器本来就带重叠），把它们
一起放进上下文是浪费；而一份材料切成二十片时，那些碎片会**互相竞争 top-k 名额**，
把别的材料挤出去——检索评估（Day 30）量到的正是这个。

因此检索器会**多取一些候选**再按来源去重。``per_source`` 由调用方决定：讲一个知识
点要几份材料给 1，引用一份长材料的几段给 2～3。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agent_learning_coach.core.rag.embedding import EmbeddingClient
from agent_learning_coach.core.rag.store import VectorStore
from agent_learning_coach.core.rag.types import ScoredChunk


@dataclass(frozen=True)
class VectorRetriever:
    """向量检索器。

    典型用法::

        retriever = VectorRetriever(store, embedder, top_k=5)
        hits = await retriever.retrieve("闭包是什么")
    """

    store: VectorStore
    """向量存储。"""

    embedder: EmbeddingClient
    """嵌入客户端。"""

    top_k: int = 5
    """默认返回几**份材料**。"""

    per_source: int = 1
    """每份来源最多取几片。"""

    oversample: int = 3
    """去重前多取几倍候选。

    去重会**减少**结果条数，所以候选要多取一些，否则「每份来源一片」会把结果砍到
    远少于 top_k。
    """

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
        filters: Mapping[str, object] | None = None,
        min_score: float | None = None,
        per_source: int | None = None,
    ) -> list[ScoredChunk]:
        """检索与查询相关的切片。

        **top_k 与 min_score 都由调用方决定**：它们是业务判断（「这次要几份材料」
        「低于多少分就不算相关」），检索器只提供默认值并如实执行。

        Args:
            query: 查询文本。
            top_k: 覆盖默认返回条数（**份材料**）。
            filters: 元数据过滤，全部条件都要满足。序列按「包含任一」匹配。
            min_score: 相关度下限；低于它的结果不返回。
            per_source: 覆盖每份来源最多取几片。

        Returns:
            按相关度从高到低排列的切片。查询为空时返回空列表。
        """
        if not query.strip():
            return []
        limit = self.top_k if top_k is None else top_k
        source_limit = self.per_source if per_source is None else per_source
        if source_limit <= 0:
            vector = (await self.embedder.embed([query]))[0]
            return await self.store.search(
                vector,
                top_k=limit,
                filters=filters,
                min_score=min_score,
            )

        vector = (await self.embedder.embed([query]))[0]
        candidates = await self.store.search(
            vector,
            top_k=limit * self.oversample,
            filters=filters,
            min_score=min_score,
        )
        return _dedupe_by_source(candidates, limit, source_limit)


def _dedupe_by_source(
    hits: Sequence[ScoredChunk],
    limit: int,
    per_source: int,
) -> list[ScoredChunk]:
    """按来源去重。

    Args:
        hits: 候选（按相关度从高到低）。
        limit: 最多返回几条。
        per_source: 每份来源最多取几片。

    Returns:
        去重后的候选。
    """
    counts: dict[str, int] = {}
    picked: list[ScoredChunk] = []
    for hit in hits:
        source = hit.chunk.source
        count = counts.get(source, 0)
        if count >= per_source:
            continue
        counts[source] = count + 1
        picked.append(hit)
        if len(picked) >= limit:
            break
    return picked


__all__ = ["VectorRetriever"]
