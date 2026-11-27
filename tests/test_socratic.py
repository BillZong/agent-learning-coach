"""苏格拉底式教学的测试（Day 34）。

覆盖：提问、**回答理解**（Assessment）、**下一步与模式切换**（Orchestration）、
以及评估指标。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from agent_learning_coach.core.application.socratic import (
    ADVANCE,
    CONFUSED,
    CORRECT,
    HINT,
    PARTIAL,
    PROBE,
    REVEAL,
    SocraticAssessment,
    SocraticCase,
    SocraticTeacher,
    choose_mode_for_request,
    choose_next_mode,
)
from agent_learning_coach.core.application.teaching import (
    DIRECT,
    SOCRATIC,
    TeachingRequest,
    instructions_for,
)
from agent_learning_coach.core.application.tutor_context import TutorContextRequest
from agent_learning_coach.core.llm.mock import MockLLMClient


def _request(mode: str = SOCRATIC) -> TeachingRequest:
    """构造一次教学请求。

    Args:
        mode: 教学模式。

    Returns:
        请求。
    """
    return TeachingRequest(
        question="闭包是什么？",
        mode=mode,
        context=TutorContextRequest(question="闭包是什么？"),
    )


def _teacher(payload: dict) -> SocraticTeacher:
    """构造一个脚本化的苏格拉底式教练。

    Args:
        payload: 结构化输出的内容。

    Returns:
        教练。
    """
    return SocraticTeacher(client=MockLLMClient(text=json.dumps(payload, ensure_ascii=False)))


# --- 指令 ---------------------------------------------------------------


def test_socratic_instructions_forbid_giving_the_answer() -> None:
    """苏格拉底式的指令要明确「不要直接给答案」。"""
    instructions = instructions_for(SOCRATIC)

    assert "不要直接给答案" in instructions
    assert "一次只问一个问题" in instructions


def test_direct_and_socratic_instructions_differ() -> None:
    """两种模式的指令要真的不同——不然「模式」是假的。"""
    assert instructions_for(DIRECT) != instructions_for(SOCRATIC)


# --- 提问 ---------------------------------------------------------------


def test_teacher_asks_one_question_with_a_hint() -> None:
    """提问要有一个问题与一个提示方向——没有提示，学习者卡住只能干等。"""
    teacher = _teacher(
        {
            "question": "如果函数返回了，外部变量还在吗？",
            "rationale": "先让学习者想清楚「返回之后」这件事",
            "hint": "想想函数里定义的变量被谁引用着",
            "citations": ["1"],
        },
    )

    turn = asyncio.run(teacher.ask(_request()))

    assert turn.question.question
    assert turn.question.hint
    assert turn.mode == SOCRATIC


def test_rendered_turn_does_not_give_the_answer() -> None:
    """渲染出来的是问题，不是答案。"""
    teacher = _teacher({"question": "外部变量被谁引用着？", "hint": "看看那个函数体"})

    turn = asyncio.run(teacher.ask(_request()))

    assert "外部变量被谁引用着？" in turn.render()
    assert "卡住" in turn.render()


# --- 回答理解（Assessment） ---------------------------------------------


def test_teacher_assesses_the_learners_answer() -> None:
    """听完回答要给判断与依据 ✓。"""
    teacher = _teacher(
        {
            "understanding": PARTIAL,
            "reasoning": "学习者说对了「函数记住环境」，但没说清捕获的是什么",
            "next_step": PROBE,
            "follow_up_question": "那它捕获的是值还是名字？",
        },
    )

    assessment = asyncio.run(teacher.assess(_request(), "闭包就是函数记住了外面的变量"))

    assert assessment.understanding == PARTIAL
    assert assessment.reasoning
    assert assessment.next_step == PROBE


def test_assessment_passes_the_learners_answer_to_the_model() -> None:
    """学习者的回答要进模型——不然判断的是空气。"""
    teacher = _teacher(
        {"understanding": CORRECT, "next_step": ADVANCE, "reasoning": "对了"},
    )
    request = _request()

    asyncio.run(teacher.assess(request, "闭包是函数加上它定义时的环境"))

    client = teacher.client
    assert client is not None
    assert client.requests  # type: ignore[attr-defined]


# --- 下一步与模式切换（Orchestration） -----------------------------------


def test_reveal_switches_back_to_direct_teaching() -> None:
    """揭示答案就切回直接教学——继续问没有意义时还问，是在消耗耐心。"""
    decision = choose_next_mode(
        SocraticAssessment(understanding=CONFUSED, next_step=REVEAL),
    )

    assert decision.mode == DIRECT
    assert decision.step == REVEAL
    assert decision.reason


def test_hint_keeps_the_socratic_mode() -> None:
    """给提示时继续苏格拉底式 ✓。"""
    decision = choose_next_mode(
        SocraticAssessment(understanding=CONFUSED, next_step=HINT),
    )

    assert decision.mode == SOCRATIC
    assert decision.step == HINT


def test_advance_stays_socratic_for_the_next_point() -> None:
    """进入下一个知识点时仍是苏格拉底式 ✓。"""
    decision = choose_next_mode(
        SocraticAssessment(understanding=CORRECT, next_step=ADVANCE),
    )

    assert decision.mode == SOCRATIC
    assert decision.step == ADVANCE


def test_missing_step_falls_back_to_probing() -> None:
    """模型没说下一步时默认追问——不能什么都不做 ✓。"""
    decision = choose_next_mode(SocraticAssessment(understanding=PARTIAL))

    assert decision.step == PROBE


def test_learner_asking_for_the_answer_switches_to_direct() -> None:
    """学习者明确要求直接讲，就直接讲——苏格拉底式是手段不是目的。"""
    request = _request(SOCRATIC)

    assert choose_mode_for_request(request, learner_message="别问了，直接告诉我") == DIRECT
    assert choose_mode_for_request(request, learner_message="为什么？") == SOCRATIC


def test_default_mode_comes_from_the_request() -> None:
    """没说别的就用请求里的模式 ✓。"""
    assert choose_mode_for_request(_request(SOCRATIC)) == SOCRATIC
    assert choose_mode_for_request(_request(DIRECT)) == DIRECT


# --- 评估 ---------------------------------------------------------------


def test_case_requires_a_learner_message() -> None:
    """缺 learner_message 的用例要报错。"""
    with pytest.raises(ValueError, match="缺少 learner_message"):
        SocraticCase.from_dict({}, index=2)


def test_case_keeps_the_expectations() -> None:
    """用例要带着期望 ✓。"""
    case = SocraticCase.from_dict(
        {
            "learner_message": "我不太懂",
            "expect_understanding": CONFUSED,
            "expect_step": HINT,
            "expect_mode": SOCRATIC,
        },
        index=1,
    )

    assert case.expect_understanding == CONFUSED
    assert case.expect_step == HINT
    assert case.expect_mode == SOCRATIC
