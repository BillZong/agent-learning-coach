"""按知识点检索课程材料。

**学习领域的「教 k1」翻译成检索层的过滤条件**——这是两个领域之间的接缝。

**top_k 与阈值由谁决定**：由**应用层**，也就是这里。它们是业务判断：

- 「讲一个知识点」要 3 份材料，还是「总结整章」要 10 份？
- 「低于 0.3 分就不算相关」——这是「宁可少给也不给不相关的」这条产品判断。

存储层与检索层都只**执行**：给多少条、卡多少分，是调用方的决定。它们提供默认值，
但默认值不是决策。

**过滤条件的语义由值的类型决定**：标量 → 等于；序列 → 包含任一。「教 k1 时」要的是
「元数据里的 point_ids 含 k1」——那是一个列表，用等值匹配永远匹配不上（这是
ADR-0030 修掉的坑）。
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_learning_coach.core.learning.domain import CourseId, KnowledgePointId
from agent_learning_coach.core.rag.retrieval import VectorRetriever
from agent_learning_coach.core.rag.types import ScoredChunk


@dataclass(frozen=True)
class MaterialQuery:
    """一次材料检索的请求。

    **它把学习领域的说法翻译成检索层的说法**：要教哪个知识点、限定哪门课。
    """

    query: str
    """查询文本。"""

    point_ids: tuple[KnowledgePointId, ...] = ()
    """只检索覆盖这些知识点的材料；为空表示不限定。"""

    course_id: CourseId | None = None
    """只检索这门课的材料；为空表示不限定。"""

    top_k: int = 3
    """要几条。**这是业务判断**——讲一个知识点 3 份，总结整章 10 份。"""

    min_score: float | None = None
    """相关度下限；低于它的结果不返回。**这是业务判断**——宁可少给。"""

    def filters(self) -> dict[str, object]:
        """翻译成检索层的过滤条件。

        Returns:
            过滤条件。空字典表示不过滤。
        """
        conditions: dict[str, object] = {}
        if self.point_ids:
            conditions["point_ids"] = [str(point_id) for point_id in self.point_ids]
        if self.course_id is not None:
            conditions["course_id"] = str(self.course_id)
        return conditions


@dataclass(frozen=True)
class MaterialSearch:
    """按知识点检索课程材料——**应用层用例**。

    典型用法::

        search = MaterialSearch(retriever=retriever)
        hits = await search.search(MaterialQuery(query="闭包是什么", point_ids=(k1,)))
    """

    retriever: VectorRetriever
    """检索器。"""

    async def search(self, request: MaterialQuery) -> list[ScoredChunk]:
        """检索材料。

        Args:
            request: 检索请求。

        Returns:
            按相关度从高到低排列的切片。
        """
        return await self.retriever.retrieve(
            request.query,
            top_k=request.top_k,
            filters=request.filters(),
            min_score=request.min_score,
        )


__all__ = ["MaterialQuery", "MaterialSearch"]
