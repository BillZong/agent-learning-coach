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
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePath

from agent_learning_coach.core.rag.retrieval import VectorRetriever
from agent_learning_coach.core.rag.types import ScoredChunk


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

    misses: tuple[str, ...] = ()
    """完全没命中的查询。"""

    by_category: Mapping[str, RetrievalMetrics] = field(default_factory=dict)
    """按类别的指标。键是类别名，值是该类别的指标（其 ``by_category`` 为空）。"""

    def summary(self) -> str:
        """一句话摘要。

        Returns:
            形如 ``12 条用例：hit@1 0.58，hit@3 0.83，hit@5 0.92，MRR 0.71``。
        """
        return (
            f"{self.cases} 条用例："
            f"hit@1 {self.hit_rate_at_1:.2f}，"
            f"hit@3 {self.hit_rate_at_3:.2f}，"
            f"hit@5 {self.hit_rate_at_5:.2f}，"
            f"MRR {self.mrr:.2f}"
        )


def _basename(source: str) -> str:
    """取来源的文件名。

    Args:
        source: 来源路径。

    Returns:
        文件名。
    """
    return PurePath(source).name


def _is_hit(hits: Sequence[ScoredChunk], expected: Iterable[str]) -> bool:
    """判断结果里是否有期望的来源。

    Args:
        hits: 检索结果。
        expected: 期望的来源文件名。

    Returns:
        命中返回 True。
    """
    wanted = set(expected)
    return any(_basename(hit.chunk.source) in wanted for hit in hits)


def _aggregate(
    records: Sequence[tuple[bool, bool, bool, int | None]],
) -> tuple[float, float, float, float]:
    """汇总命中计数与排名。

    Args:
        records: 每条用例的 ``(hit@1, hit@3, hit@5, 首次命中的排名)``。

    Returns:
        ``(hit@1, hit@3, hit@5, mrr)``。
    """
    total = len(records)
    if total == 0:
        return 0.0, 0.0, 0.0, 0.0
    hit1 = sum(1 for record in records if record[0])
    hit3 = sum(1 for record in records if record[1])
    hit5 = sum(1 for record in records if record[2])
    reciprocal = sum(1.0 / record[3] for record in records if record[3] is not None)
    return hit1 / total, hit3 / total, hit5 / total, reciprocal / total


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
        指标。
    """
    if not cases:
        return RetrievalMetrics(
            cases=0,
            hit_rate_at_1=0.0,
            hit_rate_at_3=0.0,
            hit_rate_at_5=0.0,
            mrr=0.0,
        )

    records: list[tuple[bool, bool, bool, int | None]] = []
    grouped: dict[str, list[tuple[bool, bool, bool, int | None]]] = {}
    misses: list[str] = []

    for case in cases:
        hits = await retriever.retrieve(case.query, top_k=top_k)
        rank = next(
            (
                index
                for index, hit in enumerate(hits, start=1)
                if _basename(hit.chunk.source) in set(case.expected_sources)
            ),
            None,
        )
        record = (
            _is_hit(hits[:1], case.expected_sources),
            _is_hit(hits[:3], case.expected_sources),
            _is_hit(hits[:5], case.expected_sources),
            rank,
        )
        records.append(record)
        grouped.setdefault(case.category, []).append(record)
        if rank is None:
            misses.append(case.query)

    hit1, hit3, hit5, mrr = _aggregate(records)
    by_category = {
        category: RetrievalMetrics(
            cases=len(items),
            hit_rate_at_1=_aggregate(items)[0],
            hit_rate_at_3=_aggregate(items)[1],
            hit_rate_at_5=_aggregate(items)[2],
            mrr=_aggregate(items)[3],
        )
        for category, items in grouped.items()
    }

    return RetrievalMetrics(
        cases=len(cases),
        hit_rate_at_1=hit1,
        hit_rate_at_3=hit3,
        hit_rate_at_5=hit5,
        mrr=mrr,
        misses=tuple(misses),
        by_category=by_category,
    )


__all__ = ["RetrievalCase", "RetrievalMetrics", "evaluate_retrieval"]
