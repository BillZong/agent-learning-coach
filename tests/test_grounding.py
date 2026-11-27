"""引用支撑门禁的测试（Day 31）。

门禁回答的是「这段答案能不能给学习者看」——**判定与处置是两件事**：
校验器只给报告，策略决定放不放行。
"""

from __future__ import annotations

from agent_learning_coach.core.application.grounding import (
    FABRICATED,
    GROUNDED,
    UNGROUNDED,
    GroundingPolicy,
    check_grounding,
    collect_citations,
    evaluate_answer,
)
from agent_learning_coach.core.rag.citation import Citation, CitationSet

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


def _citations(*markers: str) -> CitationSet:
    """构造引用集合。

    Args:
        markers: 标记。

    Returns:
        引用集合。
    """
    return CitationSet(citations=tuple(_citation(marker) for marker in markers))


# --- 判定 ---------------------------------------------------------------


def test_answer_with_real_citations_is_grounded() -> None:
    """引用都在、也撑得住——正常交付。"""
    result = check_grounding("闭包是函数加上它定义时所处的环境[1]。", _citations("1"))

    assert result.kind == GROUNDED
    assert result.deliver is True


def test_fabricated_marker_blocks_the_answer() -> None:
    """编造的引用要拦住——学习者按标记去核对会找不到东西。"""
    answer = "闭包是函数加上它定义时所处的环境[1]。它还能记住全局变量[7]。"

    result = check_grounding(answer, _citations("1"))

    assert result.kind == FABRICATED
    assert result.deliver is False
    assert result.fabricated_markers == ("7",)
    assert result.retry_instruction
    assert result.fallback_text


def test_fabrication_can_be_allowed_to_pass_with_a_warning() -> None:
    """策略可以放行编造的引用——但必须同时报告，不能静默。"""
    answer = "闭包是函数加上它定义时所处的环境[1]。它还能记住全局变量[7]。"

    result = check_grounding(
        answer,
        _citations("1"),
        policy=GroundingPolicy(block_on_fabrication=False),
    )

    assert result.kind == FABRICATED
    assert result.deliver is True
    assert result.note


def test_answer_without_citations_is_delivered_by_default() -> None:
    """没有引用默认放行——讲通用概念时，模型用自己的知识是合理的。"""
    result = check_grounding("闭包是函数加上它定义时所处的环境。", _citations("1"))

    assert result.kind == UNGROUNDED
    assert result.deliver is True


def test_policy_can_require_citations() -> None:
    """材料相关的问题可以要求必须有引用。"""
    result = check_grounding(
        "闭包是函数加上它定义时所处的环境。",
        _citations("1"),
        policy=GroundingPolicy(require_citation=True),
    )

    assert result.kind == UNGROUNDED
    assert result.deliver is False


def test_unsupported_citation_is_a_warning_not_an_error() -> None:
    """撑不住是警告——不是编造，不该拦住答案。"""
    answer = "这一章讲的是并发原语与消息队列[1]。"

    result = check_grounding(answer, _citations("1"))

    assert result.deliver is True
    assert result.warnings


def test_summary_explains_the_decision() -> None:
    """判定要能一句话说清——调用方要据此决定怎么处置。"""
    result = check_grounding("闭包是函数加上它定义时所处的环境[1]。", _citations("1"))

    assert GROUNDED in result.summary()


# --- 从草稿本取回引用 ---------------------------------------------------


def test_collect_citations_reads_the_draft_state() -> None:
    """草稿本里的引用要能取回来——工具产出与应用层之间就靠它。"""
    citation = _citation("1")

    citations = collect_citations({"citations": [citation.to_dict()]})

    assert len(citations) == 1
    assert citations.get("1") is not None


def test_collect_citations_returns_empty_when_absent() -> None:
    """草稿本里没有引用时返回空集合，不报错。"""
    citations = collect_citations({})

    assert len(citations) == 0


def test_collect_citations_ignores_malformed_entries() -> None:
    """草稿本里混进了别的东西时跳过它——草稿本是模型与应用共用的。"""
    citations = collect_citations({"citations": ["不是字典", _citation("1").to_dict()]})

    assert len(citations) == 1


# --- 打包与渲染 ---------------------------------------------------------


def test_outcome_carries_the_reference_list() -> None:
    """答案与它的依据一起交付。"""
    outcome = evaluate_answer("闭包是函数加上它定义时所处的环境[1]。", _citations("1"))

    assert outcome.deliver is True
    assert "closures.md" in outcome.reference_list
    assert "closures.md" in outcome.render()


def test_blocked_outcome_does_not_show_the_unverified_answer() -> None:
    """被拦下的答案**不给原文**——那段文字里有编造的引用。

    给学习者看兜底话术就行；严格门禁的失败模式是沉默，但把假引用递过去更糟。
    """
    answer = "闭包是函数加上它定义时所处的环境[7]。"

    outcome = evaluate_answer(answer, _citations("1"))

    assert outcome.deliver is False
    assert outcome.render() == outcome.result.fallback_text
    assert "[7]" not in outcome.render()
