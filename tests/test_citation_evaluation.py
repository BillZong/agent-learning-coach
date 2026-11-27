"""引用评估的测试（Day 31）。

覆盖：评估集加载、用例结构、校验器准不准、答案的引用质量（精确率 / 覆盖率 /
编造率）、按类别分组。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_learning_coach.core.rag.citation import Citation, CitationSet
from agent_learning_coach.core.rag.citation_evaluation import (
    CitationCase,
    evaluate_citations,
    load_citation_cases,
)

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evals" / "citations.json"

CLOSURES = "闭包是函数加上它定义时所处的环境。即使函数已经返回，变量绑定仍然保留。"


def _citation(marker: str = "1", text: str = CLOSURES) -> Citation:
    """构造一条引用。

    Args:
        marker: 标记。
        text: 切片原文。

    Returns:
        引用。
    """
    return Citation(
        marker=marker,
        chunk_id=f"chunk-{marker}",
        document_id="doc",
        source="closures.md",
        index=0,
        text=text,
    )


# --- 评估集 -------------------------------------------------------------


def test_dataset_loads() -> None:
    """评估集能加载。"""
    cases = load_citation_cases(DATASET)

    assert len(cases) >= 10
    assert all(case.answer.strip() for case in cases)


def test_dataset_covers_every_category() -> None:
    """评估集要覆盖四类情形——只测「都对」说明不了什么。"""
    cases = load_citation_cases(DATASET)

    categories = {case.category for case in cases}

    assert {"supported", "fabricated", "unsupported", "missing"} <= categories


def test_dataset_has_mixed_cases() -> None:
    """要有「一半对一半错」的用例——引用精确率要能掉下来。"""
    cases = load_citation_cases(DATASET)

    assert any(case.category == "mixed" for case in cases)


def test_loader_rejects_broken_dataset(tmp_path: Path) -> None:
    """结构不对的评估集要报清楚。"""
    path = tmp_path / "broken.json"
    path.write_text(json.dumps({"cases": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="至少要有一条用例"):
        load_citation_cases(path)


def test_loader_rejects_a_case_without_answer(tmp_path: Path) -> None:
    """缺 answer 的条目要报错。"""
    path = tmp_path / "broken.json"
    path.write_text(json.dumps({"cases": [{"citations": []}]}), encoding="utf-8")

    with pytest.raises(ValueError, match="缺少 answer"):
        load_citation_cases(path)


# --- 校验器的准不准 -----------------------------------------------------


def test_checker_matches_the_labels_on_the_dataset() -> None:
    """校验器与人工标注一致——不一致的用例要能列出来。"""
    cases = load_citation_cases(DATASET)

    metrics = evaluate_citations(cases)

    wrong = [result for result in metrics.results if not result.checker_correct]
    assert metrics.checker_accuracy == pytest.approx(1.0 - len(wrong) / len(cases))


def test_checker_finds_a_fabricated_marker() -> None:
    """编造的引用要被抓出来。"""
    case = CitationCase(
        answer="闭包是函数加上它定义时所处的环境[1]。它还能记住全局变量[7]。",
        citations=(_citation("1"),),
        expected_fabricated=("7",),
    )

    metrics = evaluate_citations([case])

    assert metrics.results[0].detected_fabricated == ("7",)
    assert metrics.results[0].checker_correct


def test_checker_finds_an_unsupported_marker() -> None:
    """撑不住的引用要被抓出来。"""
    case = CitationCase(
        answer="这一章讲的是并发原语与消息队列[1]。",
        citations=(_citation("1"),),
        expected_unsupported=("1",),
    )

    metrics = evaluate_citations([case])

    assert metrics.results[0].detected_unsupported == ("1",)
    assert metrics.results[0].checker_correct


def test_checker_flags_a_missing_citation() -> None:
    """没有引用要被标出来（是警告，不是错误）。"""
    case = CitationCase(
        answer="闭包是函数加上它定义时所处的环境。",
        citations=(_citation("1"),),
        expected_missing=True,
    )

    metrics = evaluate_citations([case])

    assert metrics.results[0].detected_missing is True
    assert metrics.results[0].checker_correct


def test_checker_reports_precision_and_recall_separately() -> None:
    """精确率与召回率分开报——多报和漏报是两种错。"""
    cases = load_citation_cases(DATASET)

    metrics = evaluate_citations(cases)

    assert 0.0 <= metrics.checker_precision <= 1.0
    assert 0.0 <= metrics.checker_recall <= 1.0


def test_empty_dataset_gives_zero_metrics() -> None:
    """空评估集给出零指标，不报错。"""
    metrics = evaluate_citations([])

    assert metrics.cases == 0
    assert metrics.checker_accuracy == 0.0


# --- 答案的引用质量 -----------------------------------------------------


def test_citation_precision_drops_when_a_citation_is_wrong() -> None:
    """引用精确率要能反映「引用指错了地方」。"""
    good = CitationCase(
        answer="闭包是函数加上它定义时所处的环境[1]。",
        citations=(_citation("1"),),
    )
    bad = CitationCase(
        answer="这一章讲的是并发原语与消息队列[1]。",
        citations=(_citation("1"),),
    )

    metrics = evaluate_citations([good, bad])

    assert metrics.citation_precision == pytest.approx(0.5)


def test_fabrication_rate_counts_made_up_markers() -> None:
    """编造率要能算出来。"""
    case = CitationCase(
        answer="闭包是函数加上它定义时所处的环境[1]。它还能记住全局变量[7]。",
        citations=(_citation("1"),),
        expected_fabricated=("7",),
    )

    metrics = evaluate_citations([case])

    assert metrics.fabrication_rate == pytest.approx(0.5)


def test_coverage_counts_sentences_with_markers() -> None:
    """覆盖率是「带引用的句子占比」。"""
    case = CitationCase(
        answer="闭包是函数加上它定义时所处的环境[1]。它还能记住全局变量。",
        citations=(_citation("1"),),
    )

    metrics = evaluate_citations([case])

    assert metrics.citation_coverage == pytest.approx(0.5)


def test_metrics_are_grouped_by_category() -> None:
    """按类别的指标要能看。"""
    cases = load_citation_cases(DATASET)

    metrics = evaluate_citations(cases)

    assert set(metrics.by_category) == {case.category for case in cases}
    assert all(item.cases > 0 for item in metrics.by_category.values())


def test_metrics_summary_mentions_every_metric() -> None:
    """摘要要把六个指标都说到——少一个就看不见它。"""
    cases = load_citation_cases(DATASET)

    summary = evaluate_citations(cases).summary()

    assert "校验器准确率" in summary
    assert "引用精确率" in summary
    assert "覆盖率" in summary
    assert "编造率" in summary


def test_case_result_describes_itself() -> None:
    """结果要能一句话说清——失败案例可查。"""
    cases = load_citation_cases(DATASET)

    metrics = evaluate_citations(cases)

    assert all(result.describe() for result in metrics.results)


def test_citation_set_is_reused_without_mutation() -> None:
    """评估不改引用集合本身——多次评估要得到同样的结果。"""
    case = CitationCase(
        answer="闭包是函数加上它定义时所处的环境[1]。",
        citations=(_citation("1"),),
    )
    citations = case.citation_set()

    evaluate_citations([case])

    assert isinstance(citations, CitationSet)
    assert len(citations) == 1
