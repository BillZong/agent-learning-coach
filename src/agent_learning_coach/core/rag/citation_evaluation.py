"""引用准确性评估。

**回答「两个不同的问题」**：

1. **校验器准不准**——它报出的「编造」「不支撑」与人工标注一致吗？
2. **答案的引用质量如何**——引用精确率、覆盖率、编造率。

**为什么要有这一层**：Day 30 给检索做了评估（召回、MRR、找全率、假阳性率），
引用这一侧一直没有对应的量。**没有量的引用机制只能靠感觉调**——「模型好像会编
引用」这句话没法验证。

**口径必须说清楚**：

- **引用精确率是词面口径**。文献里的主流做法（ALCE 的 citation NLI）用**蕴含**
  模型判断「这段材料是否支撑这句话」；本项目没有 NLI 模型，用的是词面交集
  （``core.text.tokenize``）。**词面支撑是蕴含的必要不充分条件**——共享词不等于
  支撑（反例：「闭包捕获变量」与「变量在什么时候被绑定」共享「变量」但不互相
  支撑）。所以这里的精确率是**上界估计**，不是真值。
- **评估集是人工合成的**。它验证的是「机制对不对」，**不能**验证「真实模型会不会
  编引用」——那需要真模型（见 ADR-0031 §边界）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_learning_coach.core.rag.citation import (
    Citation,
    CitationReport,
    CitationSet,
    CitationValidator,
)


@dataclass(frozen=True)
class CitationCase:
    """一条引用评估用例。

    **标注的是「校验器应该报什么」**——这是能离线判定的那一半。另一半
    （模型会不会编引用）需要真模型，见 §边界。
    """

    answer: str
    """被评估的答案文本。"""

    citations: tuple[Citation, ...] = ()
    """本次可用的引用。"""

    expected_fabricated: tuple[str, ...] = ()
    """期望被判为「编造」的标记（引用不存在）。"""

    expected_unsupported: tuple[str, ...] = ()
    """期望被判为「不支撑」的标记（引用存在但词面撑不住）。"""

    expected_missing: bool = False
    """期望被判为「无引用」（答案里一个标记都没有）。"""

    category: str = ""
    """用例类别。"""

    note: str = ""
    """这条用例在考什么。"""

    @classmethod
    def from_dict(cls, item: Mapping[str, Any], *, index: int) -> CitationCase:
        """从 JSON 条目构造用例。

        Args:
            item: JSON 条目。
            index: 条目序号（用于错误信息）。

        Returns:
            用例。

        Raises:
            ValueError: 条目结构不对。
        """
        answer = item.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError(f"第 {index} 条引用用例缺少 answer")
        raw_citations = item.get("citations", [])
        if not isinstance(raw_citations, list):
            raise ValueError(f"第 {index} 条引用用例的 citations 必须是列表")
        citations = tuple(
            Citation.from_dict(entry) for entry in raw_citations if isinstance(entry, Mapping)
        )
        return cls(
            answer=answer,
            citations=citations,
            expected_fabricated=tuple(str(x) for x in item.get("expected_fabricated", [])),
            expected_unsupported=tuple(str(x) for x in item.get("expected_unsupported", [])),
            expected_missing=bool(item.get("expected_missing", False)),
            category=str(item.get("category", "")),
            note=str(item.get("note", "")),
        )

    def citation_set(self) -> CitationSet:
        """转成引用集合。

        Returns:
            引用集合。
        """
        return CitationSet(citations=self.citations)


@dataclass(frozen=True)
class CitationCaseResult:
    """一条用例的结果。"""

    case: CitationCase
    """被评估的用例。"""

    report: CitationReport
    """校验器的报告。"""

    @property
    def detected_fabricated(self) -> tuple[str, ...]:
        """校验器报出的编造标记。"""
        return tuple(issue.marker for issue in self.report.errors)

    @property
    def detected_unsupported(self) -> tuple[str, ...]:
        """校验器报出的不支撑标记。"""
        return tuple(issue.marker for issue in self.report.issues if issue.kind == "unsupported")

    @property
    def detected_missing(self) -> bool:
        """校验器是否报了「无引用」。"""
        return any(issue.kind == "missing" for issue in self.report.issues)

    @property
    def checker_correct(self) -> bool:
        """校验器的判定与标注是否完全一致。

        **完全一致**而不是「没有漏报」：多报和漏报同样是错——多报会把好答案
        拦下来，学习者白白看到「这条引用有问题」。
        """
        return (
            set(self.detected_fabricated) == set(self.case.expected_fabricated)
            and set(self.detected_unsupported) == set(self.case.expected_unsupported)
            and self.detected_missing == self.case.expected_missing
        )

    def describe(self) -> str:
        """一句话描述。

        Returns:
            描述文本。
        """
        verdict = "一致" if self.checker_correct else "不一致"
        return (
            f"{verdict}：{self.case.answer[:24]}"
            f"——期望编造 {list(self.case.expected_fabricated)}，"
            f"实际编造 {list(self.detected_fabricated)}"
        )


@dataclass(frozen=True)
class CitationMetrics:
    """引用质量的指标。"""

    cases: int
    """用例总数。"""

    checker_accuracy: float = 0.0
    """校验器判对的用例比例。"""

    checker_precision: float = 0.0
    """校验器报出的问题里有多少是真的。"""

    checker_recall: float = 0.0
    """真问题里有多少被校验器查出。"""

    citation_precision: float = 0.0
    """**引用精确率**：答案里的引用中，有多少撑得起来（词面口径）。

    **它是上界估计**——词面交集不等于蕴含。真正的口径要用 NLI 模型。
    """

    citation_coverage: float = 0.0
    """**引用覆盖率**：带引用的句子占全部句子的比例。"""

    fabrication_rate: float = 0.0
    """**编造率**：答案里的引用中，有多少指向不存在的标记。"""

    results: tuple[CitationCaseResult, ...] = ()
    """每条用例的结果。"""

    by_category: Mapping[str, CitationMetrics] = field(default_factory=dict)
    """按类别的指标。"""

    def summary(self) -> str:
        """一句话摘要。

        Returns:
            摘要文本。
        """
        return (
            f"{self.cases} 条用例："
            f"校验器准确率 {self.checker_accuracy:.2f}，"
            f"精确率 {self.checker_precision:.2f}，"
            f"召回率 {self.checker_recall:.2f}，"
            f"引用精确率 {self.citation_precision:.2f}，"
            f"覆盖率 {self.citation_coverage:.2f}，"
            f"编造率 {self.fabrication_rate:.2f}"
        )


def _sentences(answer: str) -> list[str]:
    """切句子。

    Args:
        answer: 答案文本。

    Returns:
        句子列表（去掉空句）。
    """
    import re

    parts = re.split(r"[。！？.!?\n]+", answer)
    return [part.strip() for part in parts if part.strip()]


def evaluate_citations(
    cases: Sequence[CitationCase],
    *,
    validator: CitationValidator | None = None,
) -> CitationMetrics:
    """评估一组引用用例。

    Args:
        cases: 用例。
        validator: 校验器。为 None 时用默认校验器。

    Returns:
        指标。
    """
    validator = validator or CitationValidator()
    results = [
        CitationCaseResult(
            case=case,
            report=validator.validate(case.answer, case.citation_set()),
        )
        for case in cases
    ]
    return _metrics_from(results)


def _metrics_from(
    results: Sequence[CitationCaseResult],
    *,
    with_categories: bool = True,
) -> CitationMetrics:
    """从每条用例的结果汇总指标。

    Args:
        results: 用例结果。
        with_categories: 是否按类别再分一层。分组时关掉它——否则会无限递归。

    Returns:
        指标。
    """
    total = len(results)
    if total == 0:
        return CitationMetrics(cases=0)

    detected_total = 0
    expected_total = 0
    detected_hits = 0
    correct_cases = 0
    used_markers = 0
    fabricated_markers = 0
    supported_markers = 0
    cited_sentences = 0
    all_sentences = 0

    for result in results:
        detected = set(result.detected_fabricated) | set(result.detected_unsupported)
        expected = set(result.case.expected_fabricated) | set(result.case.expected_unsupported)
        detected_total += len(detected)
        expected_total += len(expected)
        detected_hits += len(detected & expected)
        if result.checker_correct:
            correct_cases += 1

        cited = result.report.cited
        used_markers += len(cited)
        fabricated_markers += len(result.detected_fabricated)
        # 精确率的分母是「答案里用掉的标记」，分子是「撑得住的标记」——
        # 编造与不支撑都要从分子里扣掉。
        supported_markers += len(
            set(cited) - set(result.detected_fabricated) - set(result.detected_unsupported),
        )

        sentences = _sentences(result.case.answer)
        all_sentences += len(sentences)
        cited_sentences += sum(1 for sentence in sentences if "[" in sentence and "]" in sentence)

    grouped: dict[str, list[CitationCaseResult]] = {}
    for result in results:
        grouped.setdefault(result.case.category, []).append(result)

    return CitationMetrics(
        cases=total,
        checker_accuracy=correct_cases / total,
        checker_precision=(detected_hits / detected_total) if detected_total else 1.0,
        checker_recall=(detected_hits / expected_total) if expected_total else 1.0,
        citation_precision=(supported_markers / used_markers) if used_markers else 0.0,
        citation_coverage=(cited_sentences / all_sentences) if all_sentences else 0.0,
        fabrication_rate=(fabricated_markers / used_markers) if used_markers else 0.0,
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


def load_citation_cases(path: str | Path) -> list[CitationCase]:
    """从 JSON 加载用例。

    Args:
        path: 文件路径。

    Returns:
        用例列表。

    Raises:
        ValueError: 文件结构不对。
    """
    import json

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = data.get("cases")
    if not isinstance(raw, list) or not raw:
        raise ValueError("引用评估集至少要有一条用例")
    return [
        CitationCase.from_dict(item, index=index)
        for index, item in enumerate(raw, start=1)
        if isinstance(item, Mapping)
    ]


__all__ = [
    "CitationCase",
    "CitationCaseResult",
    "CitationMetrics",
    "evaluate_citations",
    "load_citation_cases",
]
