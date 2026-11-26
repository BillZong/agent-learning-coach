"""检索过滤与「谁决定 top-k / 阈值」的测试（Day 29）。

覆盖四组关注点：

**过滤语义**--标量等值、序列包含任一。

**阈值**--低于下限的结果不返回。

**归属**--top-k 与阈值由应用层决定，存储层只执行。

**接缝**--学习领域的「教 k1」翻译成检索层的过滤条件。

这些测试全部**离线**：用内存存储与哈希嵌入器，不需要数据库。
"""

from __future__ import annotations

import pytest

from agent_learning_coach.core.application import MaterialQuery, MaterialSearch
from agent_learning_coach.core.learning import CourseId, KnowledgePointId
from agent_learning_coach.core.rag import (
    Chunk,
    HashingEmbedder,
    InMemoryVectorStore,
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


@pytest.fixture
def store() -> InMemoryVectorStore:
    """构造一个装了四份材料的内存存储。

    Returns:
        向量存储。
    """
    import asyncio

    storage = InMemoryVectorStore(dimension=64)
    chunks = [
        _chunk("c1", "讲闭包", {"course_id": "c1", "point_ids": ["k1", "k2"]}),
        _chunk("c2", "讲作用域", {"course_id": "c1", "point_ids": ["k1"]}),
        _chunk("c3", "讲装饰器", {"course_id": "c2", "point_ids": ["k3"]}),
        _chunk("c4", "讲参考资料", {"course_id": "c1", "point_ids": ["k9"]}),
    ]
    embedder = HashingEmbedder(dimension=64)
    vectors = asyncio.run(embedder.embed([chunk.text for chunk in chunks]))
    asyncio.run(storage.add(chunks, vectors))
    return storage


# --- 过滤语义 -----------------------------------------------------------


def test_scalar_filter_matches_equality(store: InMemoryVectorStore) -> None:
    """标量过滤是等值匹配。"""
    import asyncio

    hits = asyncio.run(
        store.search([0.1] * 64, top_k=10, filters={"course_id": "c1"}),
    )

    assert {hit.chunk.id for hit in hits} == {"c1", "c2", "c4"}


def test_sequence_filter_matches_any_element(store: InMemoryVectorStore) -> None:
    """序列过滤是「包含任一」——这正是「教 k1 时」要的语义。"""
    import asyncio

    hits = asyncio.run(
        store.search([0.1] * 64, top_k=10, filters={"point_ids": ["k2", "k3"]}),
    )

    assert {hit.chunk.id for hit in hits} == {"c1", "c3"}


def test_sequence_filter_does_not_match_scalar_metadata(store: InMemoryVectorStore) -> None:
    """标量元数据不会被序列过滤误伤。"""
    import asyncio

    hits = asyncio.run(
        store.search([0.1] * 64, top_k=10, filters={"point_ids": ["k1"]}),
    )

    assert {hit.chunk.id for hit in hits} == {"c1", "c2"}


def test_filters_are_combined(store: InMemoryVectorStore) -> None:
    """多个条件全部满足才算命中。"""
    import asyncio

    hits = asyncio.run(
        store.search(
            [0.1] * 64,
            top_k=10,
            filters={"course_id": "c1", "point_ids": ["k2"]},
        ),
    )

    assert {hit.chunk.id for hit in hits} == {"c1"}


# --- 阈值 ---------------------------------------------------------------


def test_min_score_drops_weak_hits() -> None:
    """阈值：低于它的结果不返回。"""
    import asyncio

    embedder = HashingEmbedder(dimension=64)
    storage = InMemoryVectorStore(dimension=64)
    chunks = [_chunk("hit", "闭包是什么", {}), _chunk("miss", "完全无关的内容", {})]
    vectors = asyncio.run(embedder.embed([chunk.text for chunk in chunks]))
    asyncio.run(storage.add(chunks, vectors))

    query = asyncio.run(embedder.embed(["闭包是什么"]))[0]
    all_hits = asyncio.run(storage.search(query, top_k=10))
    filtered = asyncio.run(storage.search(query, top_k=10, min_score=0.99))

    assert len(all_hits) == 2
    assert [hit.chunk.id for hit in filtered] == ["hit"]


def test_min_score_none_keeps_everything(store: InMemoryVectorStore) -> None:
    """不设阈值就不筛。"""
    import asyncio

    hits = asyncio.run(store.search([0.1] * 64, top_k=10, min_score=None))

    assert len(hits) == 4


# --- 归属：top-k 与阈值由应用层决定 --------------------------------------


def test_top_k_is_decided_by_the_caller(store: InMemoryVectorStore) -> None:
    """top-k 是业务判断——调用方说几条就是几条。"""
    import asyncio

    two = asyncio.run(store.search([0.1] * 64, top_k=2))
    four = asyncio.run(store.search([0.1] * 64, top_k=4))

    assert len(two) == 2
    assert len(four) == 4


def test_retriever_passes_top_k_and_threshold_through(store: InMemoryVectorStore) -> None:
    """检索器如实透传调用方的决定。"""
    import asyncio

    retriever = VectorRetriever(store, HashingEmbedder(dimension=64), top_k=5)

    hits = asyncio.run(retriever.retrieve("闭包是什么", top_k=1))

    assert len(hits) == 1


def test_retriever_returns_nothing_for_empty_query(store: InMemoryVectorStore) -> None:
    """空查询不检索。"""
    import asyncio

    retriever = VectorRetriever(store, HashingEmbedder(dimension=64))

    assert asyncio.run(retriever.retrieve("   ")) == []


# --- 接缝：学习领域 → 检索层 ---------------------------------------------


def test_material_query_translates_point_ids_into_filters() -> None:
    """「教 k1」翻译成「元数据里的 point_ids 含 k1」。"""
    request = MaterialQuery(
        query="闭包是什么",
        point_ids=(KnowledgePointId("k1"),),
        course_id=CourseId("course-1"),
    )

    filters = request.filters()

    assert filters["point_ids"] == ["k1"]
    assert filters["course_id"] == "course-1"


def test_material_query_without_constraints_has_no_filters() -> None:
    """没有约束就没有过滤条件。"""
    assert MaterialQuery(query="闭包").filters() == {}


def test_material_search_applies_the_translation(store: InMemoryVectorStore) -> None:
    """检索用例按翻译出来的条件过滤。"""
    import asyncio

    search = MaterialSearch(retriever=VectorRetriever(store, HashingEmbedder(dimension=64)))

    hits = asyncio.run(
        search.search(
            MaterialQuery(
                query="闭包",
                point_ids=(KnowledgePointId("k3"),),
                top_k=10,
            ),
        ),
    )

    assert [hit.chunk.id for hit in hits] == ["c3"]


def test_material_search_decides_top_k(store: InMemoryVectorStore) -> None:
    """要几条由应用层决定——这里给 2，就只回 2 条。"""
    import asyncio

    search = MaterialSearch(retriever=VectorRetriever(store, HashingEmbedder(dimension=64)))

    hits = asyncio.run(search.search(MaterialQuery(query="闭包", top_k=2)))

    assert len(hits) == 2
