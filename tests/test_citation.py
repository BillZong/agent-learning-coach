"""引用绑定与校验的测试。

对应 Topic 12「RAG 与 Tool Extension」。

**绑定**——引用必须指向具体的检索切片，且标记稳定：同一个切片永远拿到同一个
标记，模型看到 ``[1]`` 之后它指代同一段文字。

**校验**——三类问题：

- **编造**：答案引用了检索结果里没有的标记。这是最严重的一类。
- **不支撑**：引用的切片与它标注的句子没有词面交集。
- **无引用**：答案里一个标记都没有。
"""

from __future__ import annotations

import pytest

from agent_learning_coach.core.rag import (
    Chunk,
    Citation,
    CitationRegistry,
    CitationSet,
    CitationValidator,
)


def _chunk(chunk_id: str = "c1", text: str = "闭包是函数连同它引用的外部变量") -> Chunk:
    """构造一个切片。"""
    return Chunk(
        id=chunk_id,
        document_id="d1",
        text=text,
        index=0,
        source="materials/closures.md",
        metadata={"topic": "函数"},
    )


def test_registry_assigns_stable_markers() -> None:
    """同一个切片永远拿到同一个标记。"""
    registry = CitationRegistry()
    chunk = _chunk()

    first = registry.build(chunk, score=0.9)
    second = registry.build(chunk, score=0.8)

    assert first.marker == second.marker == "1"


def test_registry_assigns_distinct_markers_to_distinct_chunks() -> None:
    """不同切片拿到不同标记。"""
    registry = CitationRegistry()

    first = registry.build(_chunk("c1"), score=0.9)
    second = registry.build(_chunk("c2"), score=0.5)

    assert first.marker != second.marker


def test_citation_carries_source_and_text() -> None:
    """引用携带来源、序号与原文——读者核对时看的就是这些。"""
    registry = CitationRegistry()

    citation = registry.build(_chunk(), score=0.42)

    assert citation.source == "materials/closures.md"
    assert citation.index == 0
    assert citation.text == "闭包是函数连同它引用的外部变量"
    assert citation.score == pytest.approx(0.42)
    assert citation.metadata["topic"] == "函数"


def test_citation_round_trips_through_dict() -> None:
    """引用可以序列化再还原——会话持久化需要它。"""
    citation = CitationRegistry().build(_chunk(), score=0.42)

    restored = Citation.from_dict(citation.to_dict())

    assert restored == citation


def test_citation_set_replaces_same_marker() -> None:
    """同一标记的引用被替换而不是追加。"""
    citation = CitationRegistry().build(_chunk(), score=0.42)
    updated = Citation(
        marker=citation.marker,
        chunk_id=citation.chunk_id,
        document_id=citation.document_id,
        source=citation.source,
        index=citation.index,
        text="更新后的原文",
        score=0.5,
    )

    result = CitationSet(citations=(citation,)).with_citation(updated)

    assert len(result) == 1
    assert result.get(citation.marker) is not None
    assert result.get(citation.marker).text == "更新后的原文"


def test_reference_line_mentions_source() -> None:
    """参考文献条目含来源与序号。"""
    citation = CitationRegistry().build(_chunk(), score=0.42)

    line = citation.reference_line()

    assert "[1]" in line
    assert "materials/closures.md#0" in line


def test_reference_line_omits_absent_score() -> None:
    """没有相关度时不显示相关度——结构化取回没有相似度。"""
    citation = CitationRegistry().build(_chunk())

    assert "相关度" not in citation.reference_line()


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def _citations() -> CitationSet:
    """构造一个引用集合。"""
    registry = CitationRegistry()
    return CitationSet(
        citations=(
            registry.build(
                _chunk("c1", "闭包是函数连同它引用的外部变量"),
                score=0.9,
            ),
            registry.build(
                _chunk("c2", "递归是函数调用自己来解决问题"),
                score=0.5,
            ),
        )
    )


def test_valid_answer_passes_validation() -> None:
    """引用真实存在且与句子有交集时，校验通过。"""
    report = CitationValidator().validate(
        "闭包是函数连同它引用的外部变量[1]。递归是函数调用自己[2]。",
        _citations(),
    )

    assert report.valid
    assert not report.errors
    assert report.cited == ("1", "2")


def test_fabricated_marker_is_an_error() -> None:
    """引用了不存在的标记 = 编造，必须报错。"""
    report = CitationValidator().validate(
        "闭包是函数连同它引用的外部变量[1]，而且它能自动优化[9]。",
        _citations(),
    )

    assert not report.valid
    assert any(issue.kind == "unknown_marker" and issue.marker == "9" for issue in report.errors)


def test_answer_without_citations_is_flagged() -> None:
    """没有引用会被提示——但只是警告，不是错误。"""
    report = CitationValidator().validate("闭包是函数连同它引用的外部变量。", _citations())

    assert report.valid
    assert any(issue.kind == "missing" for issue in report.warnings)


def test_citation_pointing_at_unrelated_chunk_is_flagged() -> None:
    """引用的切片与句子毫无交集时，提示引用可能指错了地方。"""
    report = CitationValidator().validate(
        "猫喜欢在温暖的沙发上睡觉[2]。",
        _citations(),
    )

    assert report.valid
    assert any(issue.kind == "unsupported" for issue in report.warnings)


def test_report_counts_errors_and_warnings_separately() -> None:
    """错误与警告分开统计。"""
    report = CitationValidator().validate("猫喜欢睡觉[2]，还会飞[7]。", _citations())

    assert not report.valid
    assert len(report.errors) == 1
    assert any(issue.kind == "unsupported" for issue in report.warnings)


def test_empty_citation_set_rejects_every_marker() -> None:
    """没有任何检索结果时，任何标记都是编造。"""
    report = CitationValidator().validate("闭包是函数[1]。", CitationSet())

    assert not report.valid
    assert len(report.errors) == 1
