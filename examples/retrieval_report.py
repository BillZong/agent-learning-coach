"""检索评估：命中率与召回的差别，以及不同切分策略的对比。

跑法::

    uv run python examples/retrieval_report.py

脚本演示四件事：

1. **评估集怎么构建**——来源粒度、人工合成、分类（词面 / 语义 / 多来源）。
2. **命中率与召回的差别**——命中率高但召回低说明什么。
3. **失败案例可查**——每条用例的期望与实际。
4. **不同切分策略的对比**——回答「按类型选策略真的更好吗」。

脚本不联网、不连数据库。**注意**：用的是占位的哈希嵌入器——它的分数分布与真嵌入
不同，所以对比**只能看趋势，不能看绝对值**。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from agent_learning_coach.core.rag import (
    Chunk,
    Chunker,
    Document,
    EmbeddingClient,
    FixedSizeChunker,
    HashingEmbedder,
    RecursiveChunker,
    VectorRetriever,
    VectorStore,
)
from agent_learning_coach.core.rag.chunking import default_chunking_policy
from agent_learning_coach.core.rag.evaluation import (
    RetrievalCase,
    compare_strategies,
    evaluate_retrieval,
    load_retrieval_cases,
)

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "evals" / "corpus"


KINDS = {
    "closures.md": "notes",
    "scopes.md": "notes",
    "decorators.md": "notes",
    "exercises.md": "exercise",
    "reference.md": "reference",
    "demo.py": "code",
}
"""每份材料的种类。

**显式声明**：扩展名推断不出「习题」——``exercises.md`` 是 .md，推断出来是讲义。
真实的摄取就是这样（``DocumentRequest.kind`` 可以覆盖推断结果）。
"""


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


def _store(
    documents: list[Document],
    chunker: Chunker,
    embedder: EmbeddingClient,
) -> tuple[VectorStore, VectorRetriever]:
    """按切分器建一个可检索的存储。

    Args:
        documents: 语料。
        chunker: 切分器。
        embedder: 嵌入客户端。

    Returns:
        (存储, 检索器)。
    """
    from agent_learning_coach.core.rag import InMemoryVectorStore

    chunks: list[Chunk] = []
    for document in documents:
        chunks.extend(chunker.split(document))
    vectors = asyncio.run(embedder.embed([chunk.text for chunk in chunks]))
    store = InMemoryVectorStore(dimension=embedder.dimension)
    asyncio.run(store.add(chunks, vectors))
    return store, VectorRetriever(store, embedder, top_k=5)


def show_dataset(cases: list[RetrievalCase]) -> None:
    """演示评估集的样子与构建原则。

    Args:
        cases: 评估用例。
    """
    print("=== 1. 评估集怎么构建 ===\n")
    print(f"共 {len(cases)} 条用例，来源在 evals/corpus/：")
    for case in cases:
        print(f"  [{case.category:8s}] {case.query} → {list(case.expected_sources)}")
    print()
    print("（**相关性的粒度是来源**，不是切片——切片 id 随切分参数变，改一次策略数据集就废）")
    print("（**分类的意义在对比**：词面用例是基线，语义用例只有真嵌入才命得中）")
    print("（这份评估集是**人工合成**的——本项目还没有真实学习者提问，")
    print("  它能验证「机制对不对」，不能验证「真实用户会不会这样问」）\n")


def show_hit_vs_recall(cases: list[RetrievalCase], documents: list[Document]) -> None:
    """演示命中率与召回的差别。

    Args:
        cases: 评估用例。
        documents: 语料。
    """
    print("=== 2. 命中率与召回的差别 ===\n")
    embedder = HashingEmbedder(dimension=256)
    _, retriever = _store(documents, default_chunking_policy(), embedder)
    metrics = asyncio.run(evaluate_retrieval(retriever, cases))

    print(metrics.summary())
    print()
    for result in metrics.results:
        if result.case.expected_sources and len(result.case.expected_sources) > 1:
            print(f"  {result.describe()}")
            print(f"    召回 {result.recall:.2f}（应命中 {len(result.case.expected_sources)} 份）")
    print()
    print("（**命中率高但召回低** = 找对了材料却只找回了它的一部分 → 切分粒度或近似误差）")
    print("（**两者都低** = 查询与材料的表达对不上 → 嵌入质量或过滤太严）\n")


def show_failures(cases: list[RetrievalCase], documents: list[Document]) -> None:
    """演示失败案例可查。

    Args:
        cases: 评估用例。
        documents: 语料。
    """
    print("=== 3. 失败案例可查 ===\n")
    embedder = HashingEmbedder(dimension=256)
    _, retriever = _store(documents, default_chunking_policy(), embedder)
    metrics = asyncio.run(evaluate_retrieval(retriever, cases))

    failed = [result for result in metrics.results if not result.is_hit]
    print(f"未命中 {len(failed)} 条：")
    for result in failed:
        print(f"  {result.describe()}")
        print(f"    类别：{result.case.category}｜{result.case.note}")
    print()

    violated = [result for result in metrics.results if result.violated_sources]
    print(f"被带回来的负例 {len(violated)} 条（假阳性率 {metrics.false_positive_rate:.2f}）：")
    for result in violated:
        print(f"  {result.case.query}——不该来的是 {list(result.case.forbidden_sources)}")
        print(f"    实际带回来：{list(result.violated_sources)}")
    print()
    print("（**只给汇总数字的评估没法指导改进**——得看见哪条没中、为什么）")
    print("（词面用例未命中 = 索引或过滤坏了）")
    print("（语义用例未命中 = 嵌入不够语义——占位哈希嵌入必然如此）")
    print("（**假阳性率量的是合成查询的偏差**：问题照着文档写，与它共享词的材料也会被带回来）\n")


def show_strategies(cases: list[RetrievalCase], documents: list[Document]) -> None:
    """演示不同切分策略的对比。

    Args:
        cases: 评估用例。
        documents: 语料。
    """
    print("=== 4. 不同切分策略的对比 ===\n")
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
    print(comparison.summary())
    print()
    print("（**占位哈希嵌入有偏差**：块越大越容易与查询共享词 → 这个对比偏向大块）")
    print("（所以**只能看趋势，不能看绝对值**；要可信的结论需要真嵌入模型）")
    print("（而且语料只有六份、查询十一条——这个规模能看出方向，说明不了上限）\n")


def main() -> None:
    """跑一遍检索评估演示。"""
    cases = load_retrieval_cases(ROOT / "evals" / "retrieval.json")
    documents = _corpus()

    show_dataset(cases)
    show_hit_vs_recall(cases, documents)
    show_failures(cases, documents)
    show_strategies(cases, documents)


if __name__ == "__main__":
    main()
