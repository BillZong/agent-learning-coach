"""材料检索：过滤语义、top-k 与阈值的归属、索引方案的选择。

跑法::

    uv run python examples/material_retrieval.py

脚本演示四件事：

1. **过滤语义由值的类型决定**——标量等值、序列包含任一（「教 k1 时」靠它）。
2. **top-k 与阈值由应用层决定**——存储层与检索层只执行。
3. **索引方案怎么选**——语料小的时候精确扫描就够。
4. **过滤下推到存储层**——不在应用层筛。

脚本不联网、不连数据库。
"""

from __future__ import annotations

import asyncio

from agent_learning_coach.core.application import MaterialQuery, MaterialSearch
from agent_learning_coach.core.learning import CourseId, KnowledgePointId
from agent_learning_coach.core.rag import (
    Chunk,
    HashingEmbedder,
    InMemoryVectorStore,
    PgVectorStore,
    VectorRetriever,
)


def _chunk(chunk_id: str, text: str, metadata: dict[str, object]) -> Chunk:
    """构造一个切片。

    Args:
        chunk_id: 切片标识。
        text: 正文。
        metadata: 元数据。

    Returns:
        切片。
    """
    return Chunk(
        id=chunk_id,
        document_id=f"doc-{chunk_id}",
        text=text,
        index=0,
        source=f"/tmp/{chunk_id}.md",
        metadata=metadata,
    )


def _store() -> InMemoryVectorStore:
    """构造一个装了四份材料的内存存储。

    Returns:
        向量存储。
    """
    embedder = HashingEmbedder(dimension=64)
    storage = InMemoryVectorStore(dimension=64)
    chunks = [
        _chunk("c1", "闭包记住了创建时的作用域", {"course_id": "c1", "point_ids": ["k1", "k2"]}),
        _chunk("c2", "作用域与变量绑定", {"course_id": "c1", "point_ids": ["k1"]}),
        _chunk("c3", "装饰器把函数包一层", {"course_id": "c2", "point_ids": ["k3"]}),
        _chunk("c4", "Python 语言参考", {"course_id": "c1", "point_ids": ["k9"]}),
    ]
    vectors = asyncio.run(embedder.embed([chunk.text for chunk in chunks]))
    asyncio.run(storage.add(chunks, vectors))
    return storage


def show_filter_semantics() -> None:
    """演示过滤语义。"""
    print("=== 1. 过滤语义由值的类型决定 ===\n")
    store = _store()
    query = asyncio.run(HashingEmbedder(dimension=64).embed(["闭包"]))[0]

    scalar = asyncio.run(store.search(query, top_k=10, filters={"course_id": "c1"}))
    print(f"标量过滤 course_id=c1：{[hit.chunk.id for hit in scalar]}")
    print("  （标量 → 等值匹配）")

    sequence = asyncio.run(store.search(query, top_k=10, filters={"point_ids": ["k2", "k3"]}))
    print(f"序列过滤 point_ids 含 k2 或 k3：{[hit.chunk.id for hit in sequence]}")
    print("  （序列 → 包含任一——「教 k2 或 k3 时」要的正是这个语义）")
    print()
    print("（原来的过滤只会等值匹配，于是「point_ids 含 k1」永远匹配不上——")
    print("  元数据里存的是数组，数据库取出来是数组的文本）\n")


def show_ownership() -> None:
    """演示 top-k 与阈值的归属。"""
    print("=== 2. top-k 与阈值由应用层决定 ===\n")
    search = MaterialSearch(retriever=VectorRetriever(_store(), HashingEmbedder(dimension=64)))

    request = MaterialQuery(
        query="闭包是什么",
        point_ids=(KnowledgePointId("k1"),),
        course_id=CourseId("c1"),
        top_k=2,
        min_score=0.0,
    )
    print(f"翻译出的过滤条件：{request.filters()}")
    hits = asyncio.run(search.search(request))
    print(f"取回：{[hit.chunk.id for hit in hits]}（top_k=2）")
    print()
    print("（top-k 是「这次要几份材料」、阈值是「低于多少分就不算相关」）")
    print("  两者都是**业务判断**，所以由应用层决定；存储层与检索层只执行）")
    print("  ——它们提供默认值，但默认值不是决策）\n")


def show_index_choice() -> None:
    """演示索引方案的选择。"""
    print("=== 3. 索引方案怎么选 ===\n")
    for index in ("none", "hnsw", "ivfflat"):
        storage = PgVectorStore("host=/tmp dbname=x", dimension=3, table="t", index=index)
        schema = storage.schema()
        has_index = "USING hnsw" in schema or "USING ivfflat" in schema
        print(f"  {index:8s} -> {'建索引' if has_index else '不建索引（精确扫描）'}")

    print()
    print("| 方案 | 什么时候用 |")
    print("| --- | --- |")
    print("| none | 语料小（几千片以内）：精确扫描召回 100%，ANN 的近似误差不值得 |")
    print("| hnsw | 默认：写入友好、召回高、内存换速度 |")
    print("| ivfflat | 写入极少、只读查询、内存紧张；代价是要定期 REINDEX |")
    print()
    print("（本项目的语料是一个学习者的课程库——几千片以内，精确扫描就够）")
    print("（但索引方案是**部署决策**，所以它是配置项，不是写死的常量）\n")


def show_pushdown() -> None:
    """演示过滤下推。"""
    print("=== 4. 过滤下推到存储层 ===\n")
    store = _store()
    query = asyncio.run(HashingEmbedder(dimension=64).embed(["闭包"]))[0]

    hits = asyncio.run(store.search(query, top_k=10, filters={"point_ids": ["k3"]}))
    print(f"带过滤的检索：{[hit.chunk.id for hit in hits]}")
    print()
    print("（过滤在**存储层**执行：数据库用索引筛完再返回。")
    print("  在应用层过滤等于把全部向量拉回内存再筛——那是「索引 ≠ 检索」最常见的误用）")


def main() -> None:
    """跑一遍材料检索演示。"""
    show_filter_semantics()
    show_ownership()
    show_index_choice()
    show_pushdown()


if __name__ == "__main__":
    main()
