"""检索工具、摄取与回调的测试。

对应 Topic 12「RAG 与 Tool Extension」。

**两个工具互补**——语义检索回答「哪些内容与问题相关」，结构化检索回答「这份
材料里有什么」。后者不经过嵌入，因此不受嵌入质量影响。

**引用必须落到切片上**——工具返回的文本带 ``[1]`` 这样的标记，结构化数据里
带可校验的引用对象。

**摄取幂等**——重复摄取同一份材料是覆盖而不是追加。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from agent_learning_coach.core.rag import (
    HashingEmbedder,
    InMemoryVectorStore,
    RetrievalCache,
    RetrieveKnowledgeTool,
    SearchCourseMaterialTool,
    SearchResultCompressor,
    VectorRetriever,
    ingest_directory,
)
from agent_learning_coach.core.tools import (
    ToolContext,
    ToolDispatcher,
    ToolOutcome,
    ToolRegistry,
)

DATA_DIR = Path(__file__).parent / "data"
MATERIALS_DIR = DATA_DIR / "materials"
MATERIAL_COUNT = len(list(MATERIALS_DIR.glob("*.md")))


def _data(outcome: ToolOutcome) -> dict[str, Any]:
    """取出结果数据；没有数据就是失败。

    Args:
        outcome: 工具执行结果。

    Returns:
        结构化结果数据。
    """
    assert outcome.data is not None
    return outcome.data


def _store_with_materials() -> tuple[InMemoryVectorStore, VectorRetriever]:
    """摄取测试语料并返回存储与检索器。"""
    store = InMemoryVectorStore()
    embedder = HashingEmbedder(dimension=256)
    asyncio.run(ingest_directory(MATERIALS_DIR, store, embedder))
    return store, VectorRetriever(store, embedder, top_k=3)


# ---------------------------------------------------------------------------
# 摄取
# ---------------------------------------------------------------------------


def test_ingestion_reads_and_chunks_materials() -> None:
    """摄取把目录变成切片。"""
    store = InMemoryVectorStore()
    embedder = HashingEmbedder(dimension=256)

    result = asyncio.run(ingest_directory(MATERIALS_DIR, store, embedder))

    assert result.document_count == MATERIAL_COUNT
    assert result.chunk_count >= MATERIAL_COUNT
    assert asyncio.run(store.count()) == result.chunk_count


def test_ingestion_is_idempotent() -> None:
    """重复摄取是覆盖而不是追加——切片数不变。"""
    store = InMemoryVectorStore()
    embedder = HashingEmbedder(dimension=256)

    asyncio.run(ingest_directory(MATERIALS_DIR, store, embedder))
    first = asyncio.run(store.count())
    asyncio.run(ingest_directory(MATERIALS_DIR, store, embedder))

    assert asyncio.run(store.count()) == first


def test_ingestion_reports_summary() -> None:
    """摄取结果带一句摘要。"""
    store = InMemoryVectorStore()
    embedder = HashingEmbedder(dimension=256)

    result = asyncio.run(ingest_directory(MATERIALS_DIR, store, embedder))

    assert f"{MATERIAL_COUNT} 份文档" in result.summary()


# ---------------------------------------------------------------------------
# 语义检索工具
# ---------------------------------------------------------------------------


def test_search_tool_returns_results_with_markers() -> None:
    """检索结果带引用标记与来源。"""
    _, retriever = _store_with_materials()
    tool = SearchCourseMaterialTool(retriever, top_k=2)

    outcome = asyncio.run(tool.execute(ToolContext(), {"query": "闭包 外部变量"}))

    assert outcome.succeeded
    assert "[1]" in outcome.content
    assert "closures.md" in outcome.content
    assert outcome.data is not None
    assert _data(outcome)["citations"]


def test_search_tool_markers_are_stable_across_calls() -> None:
    """同一个切片在多次检索里拿到同一个标记。"""
    _, retriever = _store_with_materials()
    tool = SearchCourseMaterialTool(retriever, top_k=2)

    first = asyncio.run(tool.execute(ToolContext(), {"query": "闭包 外部变量"}))
    second = asyncio.run(tool.execute(ToolContext(), {"query": "外部变量 闭包"}))

    first_ids = {c["chunk_id"] for c in _data(first)["citations"]}
    second_ids = {c["chunk_id"] for c in _data(second)["citations"]}
    shared = first_ids & second_ids
    assert shared

    first_markers = {c["chunk_id"]: c["marker"] for c in _data(first)["citations"]}
    second_markers = {c["chunk_id"]: c["marker"] for c in _data(second)["citations"]}
    for chunk_id in shared:
        assert first_markers[chunk_id] == second_markers[chunk_id]


def test_search_tool_returns_empty_message_when_nothing_matches() -> None:
    """没找到时明确说明，而不是假装成功返回空内容。"""
    _, retriever = _store_with_materials()
    tool = SearchCourseMaterialTool(retriever, min_score=0.5)

    outcome = asyncio.run(tool.execute(ToolContext(), {"query": "量子色动力学"}))

    assert outcome.succeeded
    assert "没有找到" in outcome.content
    assert _data(outcome)["citations"] == []


def test_search_tool_without_threshold_returns_top_k() -> None:
    """没有阈值时返回 top-k——是否相关由模型判断。"""
    _, retriever = _store_with_materials()
    tool = SearchCourseMaterialTool(retriever, top_k=2)

    outcome = asyncio.run(tool.execute(ToolContext(), {"query": "量子色动力学"}))

    assert len(_data(outcome)["citations"]) == 2


def test_search_tool_passes_filters_through() -> None:
    """过滤条件传给检索器。"""
    _, retriever = _store_with_materials()
    tool = SearchCourseMaterialTool(retriever, top_k=5)

    outcome = asyncio.run(
        tool.execute(ToolContext(), {"query": "闭包", "filters": {"filename": "pets.md"}})
    )

    assert outcome.succeeded
    assert all("pets.md" in c["source"] for c in _data(outcome)["citations"])


def test_search_tool_definition_is_valid() -> None:
    """工具契约符合 JSON Schema 约定。"""
    _, retriever = _store_with_materials()
    tool = SearchCourseMaterialTool(retriever)

    definition = tool.definition

    assert definition.name == "search_course_material"
    assert definition.parameters["required"] == ["query"]
    assert definition.scopes == ("rag:read",)


# ---------------------------------------------------------------------------
# 结构化检索工具
# ---------------------------------------------------------------------------


def test_retrieve_knowledge_fetches_by_source() -> None:
    """按来源取回内容，顺序是自然阅读顺序。"""
    store, _ = _store_with_materials()
    tool = RetrieveKnowledgeTool(store)

    outcome = asyncio.run(tool.execute(ToolContext(), {"source": "closures.md"}))

    assert outcome.succeeded
    assert "闭包" in outcome.content
    assert _data(outcome)["results"]
    indexes = [r["index"] for r in _data(outcome)["results"]]
    assert indexes == sorted(indexes)


def test_retrieve_knowledge_reports_unknown_source() -> None:
    """来源不存在时给出错误，并列出可用来源。"""
    store, _ = _store_with_materials()
    tool = RetrieveKnowledgeTool(store)

    outcome = asyncio.run(tool.execute(ToolContext(), {"source": "不存在.md"}))

    assert not outcome.succeeded
    assert "可用来源" in outcome.content
    assert _data(outcome)["available_sources"]


def test_retrieve_knowledge_has_no_score() -> None:
    """结构化取回没有相似度——不硬填一个 0。"""
    store, _ = _store_with_materials()
    tool = RetrieveKnowledgeTool(store)

    outcome = asyncio.run(tool.execute(ToolContext(), {"source": "pets.md"}))

    assert all(citation["score"] is None for citation in _data(outcome)["citations"])


# ---------------------------------------------------------------------------
# 工具接入调度器
# ---------------------------------------------------------------------------


def test_tools_work_through_the_dispatcher() -> None:
    """两个工具都能通过调度器执行——参数会被 Schema 校验。"""
    store, retriever = _store_with_materials()
    registry = ToolRegistry()
    registry.register(SearchCourseMaterialTool(retriever))
    registry.register(RetrieveKnowledgeTool(store))
    dispatcher = ToolDispatcher(registry)

    outcome = asyncio.run(
        dispatcher.dispatch("search_course_material", {"query": "闭包"}, ToolContext())
    )

    assert outcome.succeeded
    assert "[1]" in outcome.content


def test_dispatcher_rejects_bad_arguments() -> None:
    """缺参数时返回错误结果而不是抛异常。"""
    _store, retriever = _store_with_materials()
    registry = ToolRegistry()
    registry.register(SearchCourseMaterialTool(retriever))
    dispatcher = ToolDispatcher(registry)

    outcome = asyncio.run(dispatcher.dispatch("search_course_material", {}, ToolContext()))

    assert not outcome.succeeded


# ---------------------------------------------------------------------------
# 回调
# ---------------------------------------------------------------------------


def test_retrieval_cache_short_circuits_repeated_queries() -> None:
    """同一个查询第二次直接命中缓存。"""
    _store, retriever = _store_with_materials()
    registry = ToolRegistry()
    registry.register(SearchCourseMaterialTool(retriever))
    cache = RetrievalCache()
    dispatcher = ToolDispatcher(registry, before_hooks=[cache], after_hooks=[cache.store])

    first = asyncio.run(
        dispatcher.dispatch("search_course_material", {"query": "闭包"}, ToolContext())
    )
    second = asyncio.run(
        dispatcher.dispatch("search_course_material", {"query": "闭包"}, ToolContext())
    )

    assert first.succeeded and second.succeeded
    assert second.metadata.get("cache_hit") is True
    assert len(cache) == 1


def test_retrieval_cache_distinguishes_filters() -> None:
    """过滤条件不同就是不同的查询——串味会给出错答案。"""
    _store, retriever = _store_with_materials()
    registry = ToolRegistry()
    registry.register(SearchCourseMaterialTool(retriever))
    cache = RetrievalCache()
    dispatcher = ToolDispatcher(registry, before_hooks=[cache], after_hooks=[cache.store])

    asyncio.run(
        dispatcher.dispatch(
            "search_course_material",
            {"query": "闭包", "filters": {"filename": "pets.md"}},
            ToolContext(),
        )
    )
    asyncio.run(
        dispatcher.dispatch(
            "search_course_material",
            {"query": "闭包", "filters": {"filename": "closures.md"}},
            ToolContext(),
        )
    )

    assert len(cache) == 2


def test_retrieval_cache_distinguishes_top_k() -> None:
    """top_k 不同就是不同的查询——缓存少了一个维度就会给错结果。"""
    _store, retriever = _store_with_materials()
    registry = ToolRegistry()
    registry.register(SearchCourseMaterialTool(retriever))
    cache = RetrievalCache()
    dispatcher = ToolDispatcher(registry, before_hooks=[cache], after_hooks=[cache.store])

    first = asyncio.run(
        dispatcher.dispatch("search_course_material", {"query": "闭包", "top_k": 1}, ToolContext())
    )
    second = asyncio.run(
        dispatcher.dispatch("search_course_material", {"query": "闭包", "top_k": 3}, ToolContext())
    )

    assert len(_data(first)["results"]) == 1
    assert len(_data(second)["results"]) == 3
    assert len(cache) == 2


def test_compressor_shortens_long_results() -> None:
    """结果超预算时按相关度截断。"""
    _store, retriever = _store_with_materials()
    registry = ToolRegistry()
    registry.register(SearchCourseMaterialTool(retriever))
    dispatcher = ToolDispatcher(
        registry,
        after_hooks=[SearchResultCompressor(max_chars=120)],
    )

    outcome = asyncio.run(
        dispatcher.dispatch(
            "search_course_material",
            {"query": "闭包 递归 嵌入 向量", "top_k": 5},
            ToolContext(),
        )
    )

    assert outcome.succeeded
    assert outcome.metadata.get("compressed") is True
    # 被截掉的结果比原来少
    assert len(_data(outcome)["results"]) < 5
    # 结构化数据与文本要一致
    assert len(_data(outcome)["citations"]) == len(_data(outcome)["results"])


def test_compressor_leaves_short_results_alone() -> None:
    """短结果不压缩——压缩没有收益，还有截断风险。"""
    _store, retriever = _store_with_materials()
    registry = ToolRegistry()
    registry.register(SearchCourseMaterialTool(retriever))
    dispatcher = ToolDispatcher(
        registry,
        after_hooks=[SearchResultCompressor(max_chars=10_000)],
    )

    outcome = asyncio.run(
        dispatcher.dispatch("search_course_material", {"query": "闭包"}, ToolContext())
    )

    assert "compressed" not in outcome.metadata


# ---------------------------------------------------------------------------
# 评估数据集
# ---------------------------------------------------------------------------


def test_retrieval_cases_file_is_valid() -> None:
    """数据集文件格式正确。"""
    payload = json.loads((DATA_DIR / "retrieval_cases.json").read_text(encoding="utf-8"))

    assert payload["cases"]
    for case in payload["cases"]:
        assert case["query"]
        assert case["expected_sources"]


def test_dataset_evaluation_meets_the_baseline() -> None:
    """数据集跑出的指标达到基线——词面用例必须全中。

    **词面用例是流水线的基线**：查询与目标共享实词，任何嵌入模型都该命中。
    它掉了说明流水线坏了（切分错了、过滤错了、排序反了），而不是模型不行。
    """
    from agent_learning_coach.core.rag import (
        RecursiveChunker,
        RetrievalCase,
        evaluate_retrieval,
    )

    payload = json.loads((DATA_DIR / "retrieval_cases.json").read_text(encoding="utf-8"))
    cases = [
        RetrievalCase(
            query=item["query"],
            expected_sources=tuple(item["expected_sources"]),
            category=item.get("category", ""),
            note=item.get("note", ""),
        )
        for item in payload["cases"]
    ]

    store = InMemoryVectorStore()
    embedder = HashingEmbedder(dimension=256)
    asyncio.run(
        ingest_directory(
            MATERIALS_DIR,
            store,
            embedder,
            chunker=RecursiveChunker(size=200, overlap=30),
        )
    )
    retriever = VectorRetriever(store, embedder, top_k=5)

    metrics = asyncio.run(evaluate_retrieval(retriever, cases))

    assert metrics.cases == len(cases)
    assert metrics.by_category["lexical"].hit_rate_at_1 == 1.0
    assert metrics.by_category["semantic"].hit_rate_at_1 >= 0.5
    assert metrics.mrr >= 0.7


def test_evaluation_reports_misses() -> None:
    """评估给出命中率、MRR 与未命中的查询。"""
    from agent_learning_coach.core.rag import RetrievalCase, evaluate_retrieval

    _, retriever = _store_with_materials()
    cases = [
        RetrievalCase(query="闭包 外部变量", expected_sources=("closures.md",)),
        RetrievalCase(query="呼噜声 赫兹", expected_sources=("pets.md",)),
        RetrievalCase(query="量子色动力学", expected_sources=("nonexistent.md",)),
    ]

    metrics = asyncio.run(evaluate_retrieval(retriever, cases))

    assert metrics.cases == 3
    assert 0.0 <= metrics.hit_rate_at_1 <= 1.0
    assert metrics.misses == ("量子色动力学",)
    assert "3 条用例" in metrics.summary()
