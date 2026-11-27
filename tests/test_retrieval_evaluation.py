"""检索评估的测试（Day 30）。

覆盖四组关注点：

**评估集**--能加载；结构不对时报清楚。

**指标**--命中率、MRR、**召回@来源**三者分开。

**详情**--每条用例的期望与实际可查，失败案例能定位。

**对比**--同一份评估集跑过多种切分策略。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent_learning_coach.core.rag import (
    Chunk,
    Chunker,
    Document,
    EmbeddingClient,
    FixedSizeChunker,
    HashingEmbedder,
    RecursiveChunker,
    VectorRetriever,
)
from agent_learning_coach.core.rag.chunking import default_chunking_policy
from agent_learning_coach.core.rag.evaluation import (
    RetrievalCase,
    compare_strategies,
    evaluate_retrieval,
    load_retrieval_cases,
)

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evals" / "retrieval.json"
CORPUS = ROOT / "evals" / "corpus"

KINDS = {
    "closures.md": "notes",
    "scopes.md": "notes",
    "decorators.md": "notes",
    "exercises.md": "exercise",
    "reference.md": "reference",
    "demo.py": "code",
}
"""每份材料的种类——扩展名推断不出「习题」。"""


def _corpus() -> list[Document]:
    """读语料。

    Returns:
        文档列表。
    """
    documents: list[Document] = []
    for path in sorted(CORPUS.iterdir()):
        if path.suffix not in (".md", ".py"):
            continue
        documents.append(
            Document(
                id=path.stem,
                text=path.read_text(encoding="utf-8"),
                source=str(path),
                metadata={"kind": KINDS.get(path.name, "notes"), "filename": path.name},
            ),
        )
    return documents


def _index(
    documents: list[Document],
    chunker: Chunker,
    embedder: EmbeddingClient,
) -> VectorRetriever:
    """按切分器建一个可检索的存储。

    Args:
        documents: 语料。
        chunker: 切分器。
        embedder: 嵌入客户端。

    Returns:
        检索器。
    """
    from agent_learning_coach.core.rag import InMemoryVectorStore, VectorRetriever

    chunks: list[Chunk] = []
    for document in documents:
        chunks.extend(chunker.split(document))
    vectors = asyncio.run(embedder.embed([chunk.text for chunk in chunks]))
    store = InMemoryVectorStore(dimension=embedder.dimension)
    asyncio.run(store.add(chunks, vectors))
    return VectorRetriever(store, embedder, top_k=5)


# --- 评估集 -------------------------------------------------------------


def test_dataset_loads() -> None:
    """评估集能加载。"""
    cases = load_retrieval_cases(DATASET)

    assert len(cases) >= 10
    assert all(case.query.strip() for case in cases)
    assert all(case.expected_sources for case in cases)


def test_dataset_has_both_lexical_and_semantic_cases() -> None:
    """评估集要有词面与语义两类用例——它们的差距是嵌入能力的量尺。"""
    cases = load_retrieval_cases(DATASET)

    categories = {case.category for case in cases}

    assert "lexical" in categories
    assert "semantic" in categories


def test_dataset_has_multi_source_cases() -> None:
    """要有期望多个来源的用例——否则测不出召回。"""
    cases = load_retrieval_cases(DATASET)

    assert any(len(case.expected_sources) > 1 for case in cases)


def test_loader_rejects_broken_dataset(tmp_path: Path) -> None:
    """结构不对的评估集要报清楚。"""
    path = tmp_path / "broken.json"
    path.write_text(json.dumps({"cases": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="至少一条用例"):
        load_retrieval_cases(path)


def test_case_requires_query(tmp_path: Path) -> None:
    """缺 query 的条目要报清楚是哪一条。"""
    path = tmp_path / "broken.json"
    path.write_text(
        json.dumps({"cases": [{"expected_sources": ["a.md"]}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="缺少 query"):
        load_retrieval_cases(path)


# --- 指标 ---------------------------------------------------------------


def test_metrics_report_hit_rate_and_mrr() -> None:
    """命中率与 MRR 有值。"""
    cases = load_retrieval_cases(DATASET)
    embedder = HashingEmbedder(dimension=256)
    retriever = _index(_corpus(), default_chunking_policy(), embedder)

    metrics = asyncio.run(evaluate_retrieval(retriever, cases))

    assert metrics.cases == len(cases)
    assert 0.0 <= metrics.hit_rate_at_1 <= 1.0
    assert 0.0 <= metrics.mrr <= 1.0
    assert "召回" in metrics.summary()


def test_recall_is_reported_separately_from_hit_rate() -> None:
    """召回与命中率是两个指标：找全了没有 ≠ 找没找到。"""
    cases = [
        RetrievalCase(query="闭包", expected_sources=("closures.md", "scopes.md")),
    ]
    embedder = HashingEmbedder(dimension=256)
    retriever = _index(_corpus(), default_chunking_policy(), embedder)

    metrics = asyncio.run(evaluate_retrieval(retriever, cases))

    assert metrics.hit_rate_at_5 in (0.0, 1.0)
    assert 0.0 <= metrics.recall <= 1.0
    if metrics.hit_rate_at_5 == 1.0 and metrics.recall < 1.0:
        assert metrics.results[0].is_hit


def test_case_result_records_what_was_found() -> None:
    """每条用例的结果记着期望与实际——失败案例可查。"""
    cases = load_retrieval_cases(DATASET)
    embedder = HashingEmbedder(dimension=256)
    retriever = _index(_corpus(), default_chunking_policy(), embedder)

    metrics = asyncio.run(evaluate_retrieval(retriever, cases))

    assert len(metrics.results) == len(cases)
    for result in metrics.results:
        assert result.describe()
        if not result.is_hit:
            assert result.missing_sources == result.case.expected_sources


def test_metrics_are_grouped_by_category() -> None:
    """按类别的指标要能看——词面与语义的差距是嵌入能力的量尺。"""
    cases = load_retrieval_cases(DATASET)
    embedder = HashingEmbedder(dimension=256)
    retriever = _index(_corpus(), default_chunking_policy(), embedder)

    metrics = asyncio.run(evaluate_retrieval(retriever, cases))

    assert set(metrics.by_category) == {case.category for case in cases}
    assert all(item.cases > 0 for item in metrics.by_category.values())


def test_empty_dataset_gives_zero_metrics() -> None:
    """空评估集给出全零指标，不报错。"""
    embedder = HashingEmbedder(dimension=256)
    retriever = _index(_corpus(), default_chunking_policy(), embedder)

    metrics = asyncio.run(evaluate_retrieval(retriever, []))

    assert metrics.cases == 0
    assert metrics.hit_rate_at_1 == 0.0


# --- 策略对比 -----------------------------------------------------------


def test_strategies_are_compared_on_the_same_dataset() -> None:
    """同一份评估集跑过多种策略。"""
    cases = load_retrieval_cases(DATASET)
    documents = _corpus()
    embedder = HashingEmbedder(dimension=256)

    comparison = asyncio.run(
        compare_strategies(
            {
                "按类型选": (default_chunking_policy(), embedder),
                "全部递归": (RecursiveChunker(size=800, overlap=80), embedder),
                "全部定长": (FixedSizeChunker(size=800, overlap=80), embedder),
            },
            documents,
            cases,
        ),
    )

    assert len(comparison.rows) == 3
    assert all(row.metrics.cases == len(cases) for row in comparison.rows)
    assert "按类型选" in comparison.summary()


def test_fine_grained_chunking_produces_more_chunks() -> None:
    """按类型选会切得更细（代码按定义、习题按题号）——这是机制的事实。"""
    cases = load_retrieval_cases(DATASET)
    documents = _corpus()
    embedder = HashingEmbedder(dimension=256)

    comparison = asyncio.run(
        compare_strategies(
            {
                "按类型选": (default_chunking_policy(), embedder),
                "全部递归": (RecursiveChunker(size=800, overlap=80), embedder),
            },
            documents,
            cases,
        ),
    )

    rows = {row.name: row for row in comparison.rows}
    assert rows["按类型选"].chunk_count > rows["全部递归"].chunk_count


# --- 负例与假阳性 --------------------------------------------------------


def test_dataset_has_negative_labels() -> None:
    """评估集要有负例——只标「该命中什么」量不出假阳性。"""
    cases = load_retrieval_cases(DATASET)

    assert any(case.forbidden_sources for case in cases)


def test_forbidden_and_expected_cannot_overlap(tmp_path: Path) -> None:
    """同一个来源既是期望又是负例——数据集自相矛盾，要报错。"""
    path = tmp_path / "broken.json"
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "query": "闭包",
                        "expected_sources": ["a.md"],
                        "forbidden_sources": ["a.md"],
                    },
                ],
            },
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="期望来源与负例重叠"):
        load_retrieval_cases(path)


def test_false_positive_rate_counts_returned_negatives() -> None:
    """被带回来的负例要计入假阳性率。"""
    cases = load_retrieval_cases(DATASET)
    embedder = HashingEmbedder(dimension=256)
    retriever = _index(_corpus(), default_chunking_policy(), embedder)

    metrics = asyncio.run(evaluate_retrieval(retriever, cases))

    violations = sum(len(result.violated_sources) for result in metrics.results)
    negatives = sum(len(case.forbidden_sources) for case in cases)
    assert negatives > 0
    assert metrics.false_positive_rate == violations / negatives


def test_violated_negative_is_visible_per_case() -> None:
    """哪条负例被带回来了要能查。"""
    cases = load_retrieval_cases(DATASET)
    embedder = HashingEmbedder(dimension=256)
    retriever = _index(_corpus(), default_chunking_policy(), embedder)

    metrics = asyncio.run(evaluate_retrieval(retriever, cases))

    for result in metrics.results:
        assert set(result.violated_sources) <= set(result.case.forbidden_sources)


def test_complete_rate_uses_the_strict_criterion() -> None:
    """找全率是严格口径：期望来源要全部在前 5 条里。"""
    cases = [
        RetrievalCase(query="闭包", expected_sources=("closures.md", "scopes.md")),
    ]
    embedder = HashingEmbedder(dimension=256)
    retriever = _index(_corpus(), default_chunking_policy(), embedder)

    metrics = asyncio.run(evaluate_retrieval(retriever, cases))

    assert metrics.complete_rate_at_5 in (0.0, 1.0)
    if metrics.complete_rate_at_5 == 1.0:
        assert metrics.results[0].is_complete
        assert not metrics.results[0].missing_sources
