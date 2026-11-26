"""向量检索。

检索器是 RAG 流水线的**读端**：把查询变成嵌入，向存储要最相关的切片。

它刻意保持很薄——只有「嵌入 → 检索」两步。**没有重排、没有查询改写、
没有多路召回**。那些是检索质量的下一步，而每加一层就多一个「哪一层先失效」
的排查对象。基础检索先跑对，再谈优化。

**过滤下推到存储层**：检索器把元数据过滤条件原样传给存储，由数据库用索引
筛。在应用层过滤等于把全部向量拉回内存再筛——那是「索引 ≠ 检索」最常见的误用。
"""

from __future__ import annotations

from collections.abc import Mapping
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
    """默认返回几条。"""

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int | None = None,
        filters: Mapping[str, object] | None = None,
        min_score: float | None = None,
    ) -> list[ScoredChunk]:
        """检索与查询相关的切片。

        **top_k 与 min_score 都由调用方决定**：它们是业务判断（「这次要几份材料」
        「低于多少分就不算相关」），检索器只提供默认值并如实执行。

        Args:
            query: 查询文本。
            top_k: 覆盖默认返回条数。
            filters: 元数据过滤，全部条件都要满足。序列按「包含任一」匹配。
            min_score: 相关度下限；低于它的结果不返回。

        Returns:
            按相关度从高到低排列的切片。查询为空时返回空列表。
        """
        if not query.strip():
            return []
        vector = (await self.embedder.embed([query]))[0]
        return await self.store.search(
            vector,
            top_k=self.top_k if top_k is None else top_k,
            filters=filters,
            min_score=min_score,
        )


__all__ = ["VectorRetriever"]
