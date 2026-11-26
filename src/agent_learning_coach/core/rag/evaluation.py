"""检索评估。

**没有指标的检索质量是感觉，不是事实。** 本模块提供两个最基础的指标：

- **命中率@ k**（hit rate @ k）：前 k 条结果里命中了相关来源的查询比例。
- **MRR**（mean reciprocal rank）：第一个命中结果排名的倒数，再对全部查询求
  平均。未命中记 0。

**为什么先做这两个**：它们只依赖「排得对不对」，不需要标注「哪一段真正回答了
问题」。逐字的正确性标注（是否真的回答了问题）成本高得多——那是生成质量的
评估，不是检索的。

**为什么用「来源」而不是「切片」作为相关性的粒度**：切片 id 依赖切分参数与
文件路径，改一次切分策略整个数据集就失效了。来源是稳定的——它回答的是
「这份查询应该命中哪份材料」，而这正是切分策略要影响的东西。

**指标不解释因果**：命中率高不代表切分策略好，也可能是语料太简单。因此指标
用来**比较**（改动前后），不用来**宣称**（我们检索得很好）。

**命中率和召回不是一回事**（Day 30）：

- **命中率@k**：前 k 条里**有没有**相关的来源。
- **召回@来源**：应命中的来源**找全了没有**（命中的来源数 ÷ 应命中的来源数）。

两者差得远说明什么：**命中率高但召回低** = 找对了材料，却只找回了它的一部分——
问题在**切分粒度**（一份材料被切成二十片，只召回一片）或向量检索的近似误差；
**两者都低** = 查询与材料的表达对不上（嵌入质量）或过滤太严；**两者都高** = 这条
查询没问题——但不能推广，语料可能太简单。

**每条用例的详情**（``RetrievalCaseResult``）让失败案例可查：哪条查询没命中、命中了
什么、期望是什么。**只给汇总数字的评估没法指导改进**。

**策略对比**（``compare_strategies``）把同一份评估集跑过多种切分策略，回答「按类型
选策略真的更好吗」。**注意占位嵌入器的偏差**：哈希嵌入按词重叠打分，块越大越容易
与查询共享词——所以这个对比**只能看趋势，不能看绝对值**。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any

from agent_learning_coach.core.rag.chunking import Chunker
from agent_learning_coach.core.rag.embedding import EmbeddingClient
from agent_learning_coach.core.rag.retrieval import VectorRetriever
from agent_learning_coach.core.rag.store import InMemoryVectorStore
from agent_learning_coach.core.rag.types import Chunk, Document, ScoredChunk


@dataclass(frozen=True)
class RetrievalCase:
    """一条检索测试用例。

    ``expected_sources`` 用**来源文件名**表达期望——切片 id 会随切分参数变化，
    来源不会。
    """

    query: str
    """查询文本。"""

    expected_sources: tuple[str, ...]
    """期望命中的来源文件名（不含目录）。任一命中即算命中。"""

    category: str = ""
    """用例类别，如 ``"lexical"`` / ``"semantic"``。

    **分类的意义在于对比**：词面用例（查询与目标共享实词）是基线，任何嵌入都
    该命中；语义用例（换词提问）只有真正理解语义的嵌入才命得中。两者差距
    就是「嵌入模型的能力边界」——用占位级的哈希嵌入时，差距会大得刺眼。
    """

    note: str = ""
    """这条用例在考什么。"""

    @classmethod
    def from_dict(cls, item: Mapping[str, Any], *, index: int) -> RetrievalCase:
        """从 JSON 条目构造用例。

        Args:
            item: JSON 条目。
            index: 条目序号（用于错误信息）。

        Returns:
            检索用例。

        Raises:
            ValueError: 条目结构不对。
        """
        query = item.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"第 {index} 条检索用例缺少 query")
        expected = item.get("expected_sources")
        if not isinstance(expected, list) or not all(isinstance(item, str) for item in expected):
            raise ValueError(f"第 {index} 条检索用例的 expected_sources 必须是字符串列表")
        return cls(
            query=query,
            expected_sources=tuple(expected),
            category=str(item.get("category", "")),
            note=str(item.get("note", "")),
        )


@dataclass(frozen=True)
class RetrievalCaseResult:
    """一条用例的评估结果——**值对象**。

    **失败案例要能查**：这条查询期望命中哪些来源、实际命中了哪些、排第几。
    """

    case: RetrievalCase
    """被评估的用例。"""

    rank: int | None
    """第一个命中结果的排名；没命中为 ``None``。"""

    matched_sources: tuple[str, ...]
    """实际命中的来源文件名（按排名顺序，去重）。"""

    missing_sources: tuple[str, ...]
    """应命中却没找到的来源文件名。"""

    @property
    def is_hit(self) -> bool:
        """有没有命中。"""
        return self.rank is not None

    @property
    def recall(self) -> float:
        """召回@来源：命中的来源数 ÷ 应命中的来源数。"""
        expected = len(self.case.expected_sources)
        if expected == 0:
            return 0.0
        return len(set(self.matched_sources) & set(self.case.expected_sources)) / expected

    def describe(self) -> str:
        """一句话描述这条结果。

        Returns:
            描述文本。
        """
        if self.is_hit:
            return (
                f"命中（第 {self.rank} 位）：{self.case.query}"
                f"——期望 {list(self.case.expected_sources)}"
            )
        return (
            f"未命中：{self.case.query}"
            f"——期望 {list(self.case.expected_sources)}，实际命中 {list(self.matched_sources)}"
        )


@dataclass(frozen=True)
class RetrievalMetrics:
    """检索质量指标。"""

    cases: int
    """用例总数。"""

    hit_rate_at_1: float
    """top-1 命中率。"""

    hit_rate_at_3: float
    """top-3 命中率。"""

    hit_rate_at_5: float
    """top-5 命中率。"""

    mrr: float
    """平均倒数排名。"""

    recall: float = 0.0
    """平均召回@来源：命中的来源数 ÷ 应命中的来源数。

    **它与命中率的差说明了什么**：命中率高但召回低 = 找对了材料却只找回了它的一
    部分（切分粒度太碎，或向量检索的近似误差）；两者都低 = 查询与材料的表达对不上
    （嵌入质量）或过滤太严。
    """

    misses: tuple[str, ...] = ()
    """完全没命中的查询。"""

    results: tuple[RetrievalCaseResult, ...] = ()
    """每条用例的结果——失败案例可查。"""

    by_category: Mapping[str, RetrievalMetrics] = field(default_factory=dict)
    """按类别的指标。键是类别名，值是该类别的指标（其 ``by_category`` 为空）。"""

    def summary(self) -> str:
        """一句话摘要。

        Returns:
            形如 ``12 条用例：hit@1 0.58，hit@3 0.83，hit@5 0.92，MRR 0.71，召回 0.64``。
        """
        return (
            f"{self.cases} 条用例："
            f"hit@1 {self.hit_rate_at_1:.2f}，"
            f"hit@3 {self.hit_rate_at_3:.2f}，"
            f"hit@5 {self.hit_rate_at_5:.2f}，"
            f"MRR {self.mrr:.2f}，"
            f"召回 {self.recall:.2f}"
        )


def _basename(source: str) -> str:
    """取来源的文件名。

    Args:
        source: 来源路径。

    Returns:
        文件名。
    """
    return PurePath(source).name


def _case_result(case: RetrievalCase, hits: Sequence[ScoredChunk]) -> RetrievalCaseResult:
    """算一条用例的结果。

    Args:
        case: 用例。
        hits: 检索结果。

    Returns:
        用例结果。
    """
    wanted = set(case.expected_sources)
    matched: list[str] = []
    rank: int | None = None
    for index, hit in enumerate(hits, start=1):
        source = _basename(hit.chunk.source)
        if source in wanted:
            if rank is None:
                rank = index
            if source not in matched:
                matched.append(source)
    return RetrievalCaseResult(
        case=case,
        rank=rank,
        matched_sources=tuple(matched),
        missing_sources=tuple(sorted(wanted - set(matched))),
    )


def _metrics_from(
    results: Sequence[RetrievalCaseResult],
    *,
    with_categories: bool = True,
) -> RetrievalMetrics:
    """从每条用例的结果汇总指标。

    Args:
        results: 用例结果。
        with_categories: 是否按类别再分一层。分组时关掉它——否则会无限递归。

    Returns:
        指标。
    """
    total = len(results)
    if total == 0:
        return RetrievalMetrics(
            cases=0,
            hit_rate_at_1=0.0,
            hit_rate_at_3=0.0,
            hit_rate_at_5=0.0,
            mrr=0.0,
        )

    def hit_within(limit: int) -> int:
        return sum(1 for result in results if result.rank is not None and result.rank <= limit)

    reciprocal = sum(1.0 / result.rank for result in results if result.rank is not None)
    grouped: dict[str, list[RetrievalCaseResult]] = {}
    for result in results:
        grouped.setdefault(result.case.category, []).append(result)

    return RetrievalMetrics(
        cases=total,
        hit_rate_at_1=hit_within(1) / total,
        hit_rate_at_3=hit_within(3) / total,
        hit_rate_at_5=hit_within(5) / total,
        mrr=reciprocal / total,
        recall=sum(result.recall for result in results) / total,
        misses=tuple(result.case.query for result in results if result.rank is None),
        results=tuple(results),
        by_category=(
            {
                category: _metrics_from(items, with_categories=False)
                for category, items in grouped.items()
            }
            if with_categories
            else {}
        ),
    )


async def evaluate_retrieval(
    retriever: VectorRetriever,
    cases: Sequence[RetrievalCase],
    *,
    top_k: int = 5,
) -> RetrievalMetrics:
    """评估检索质量。

    Args:
        retriever: 向量检索器。
        cases: 测试用例。
        top_k: 评估时取多少条。

    Returns:
        指标（含每条用例的结果，失败案例可查）。
    """
    results = [
        _case_result(case, await retriever.retrieve(case.query, top_k=top_k)) for case in cases
    ]
    return _metrics_from(results)


@dataclass(frozen=True)
class StrategyRow:
    """一种切分策略在评估集上的表现——**值对象**。"""

    name: str
    """策略名。"""

    metrics: RetrievalMetrics
    """该策略下的指标。"""

    chunk_count: int
    """该策略切出的切片数。"""


@dataclass(frozen=True)
class StrategyComparison:
    """多种切分策略的对比——**值对象**。"""

    rows: tuple[StrategyRow, ...] = ()
    """每种策略一行。"""

    def summary(self) -> str:
        """一张对比表。

        Returns:
            表格文本。
        """
        lines = [
            "| 策略 | 切片数 | hit@1 | hit@3 | hit@5 | MRR | 召回 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for row in self.rows:
            metrics = row.metrics
            lines.append(
                f"| {row.name} | {row.chunk_count} | "
                f"{metrics.hit_rate_at_1:.2f} | {metrics.hit_rate_at_3:.2f} | "
                f"{metrics.hit_rate_at_5:.2f} | {metrics.mrr:.2f} | {metrics.recall:.2f} |",
            )
        return "\n".join(lines)


async def compare_strategies(
    strategies: Mapping[str, tuple[Chunker, EmbeddingClient]],
    documents: Sequence[Document],
    cases: Sequence[RetrievalCase],
    *,
    top_k: int = 5,
) -> StrategyComparison:
    """同一份评估集跑过多种切分策略。

    **回答「按类型选策略真的更好吗」**——用同一份语料、同一份评估集、同一套嵌入，
    只换切分器。

    **注意占位嵌入器的偏差**：哈希嵌入按词重叠打分，**块越大越容易与查询共享词**
    ——所以这个对比**只能看趋势，不能看绝对值**。要得出可信的结论需要真正的嵌入
    模型（ADR-0030）。

    Args:
        strategies: 策略名 → （切分器, 嵌入客户端）。
        documents: 语料文档。
        cases: 评估用例。
        top_k: 评估时取多少条。

    Returns:
        对比结果。
    """
    rows: list[StrategyRow] = []
    for name, (chunker, embedder) in strategies.items():
        chunks: list[Chunk] = []
        for document in documents:
            chunks.extend(chunker.split(document))
        vectors = await embedder.embed([chunk.text for chunk in chunks])
        store = InMemoryVectorStore(dimension=embedder.dimension)
        await store.add(chunks, vectors)
        retriever = VectorRetriever(store, embedder, top_k=top_k)
        metrics = await evaluate_retrieval(retriever, cases, top_k=top_k)
        rows.append(StrategyRow(name=name, metrics=metrics, chunk_count=len(chunks)))
    return StrategyComparison(rows=tuple(rows))


def load_retrieval_cases(path: str | Path) -> list[RetrievalCase]:
    """从 JSON 文件加载检索评估集。

    文件格式::

        {"cases": [{"query": "...", "expected_sources": ["..."], "category": "..."}]}

    Args:
        path: 文件路径。

    Returns:
        用例列表。

    Raises:
        ValueError: 文件结构不对。
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = data.get("cases") if isinstance(data, Mapping) else None
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{path}：数据集必须是 {{'cases': [...]}} 且至少一条用例")
    return [
        RetrievalCase.from_dict(item, index=index)
        for index, item in enumerate(raw)
        if isinstance(item, Mapping)
    ]


__all__ = [
    "RetrievalCase",
    "RetrievalCaseResult",
    "RetrievalMetrics",
    "StrategyComparison",
    "StrategyRow",
    "compare_strategies",
    "evaluate_retrieval",
    "load_retrieval_cases",
]
