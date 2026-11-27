"""直接教学模式的测试（Day 33）。

覆盖：指令的形状、结构化输出、引用门禁的接线、教学评估指标。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from agent_learning_coach.core.application.teaching import (
    DIRECT,
    DIRECT_TEACHING_INSTRUCTIONS,
    SOCRATIC,
    DirectTeacher,
    TeachingCase,
    TeachingCaseResult,
    TeachingExample,
    TeachingOutput,
    TeachingRequest,
    evaluate_teaching,
    instructions_for,
    render_output,
)
from agent_learning_coach.core.application.tutor_context import TutorContextRequest
from agent_learning_coach.core.llm.errors import LLMError
from agent_learning_coach.core.llm.mock import MockLLMClient
from agent_learning_coach.core.rag.citation import Citation, CitationSet


def _citation(marker: str = "1") -> Citation:
    """构造一条引用。

    Args:
        marker: 标记。

    Returns:
        引用。
    """
    return Citation(
        marker=marker,
        chunk_id=f"chunk-{marker}",
        document_id="d1",
        source="closures.md",
        index=0,
        text="闭包是函数加上它定义时所处的环境。",
    )


def _request(citations: CitationSet | None = None) -> TeachingRequest:
    """构造一次教学请求。

    Args:
        citations: 引用集合。

    Returns:
        请求。
    """
    return TeachingRequest(
        question="闭包是什么？",
        context=TutorContextRequest(
            question="闭包是什么？",
            citations=citations if citations is not None else CitationSet((_citation(),)),
        ),
    )


def _output(**overrides: object) -> TeachingOutput:
    """构造一份教学输出。

    Args:
        overrides: 覆盖字段。

    Returns:
        教学输出。
    """
    data: dict[str, object] = {
        "explanation": "闭包是函数加上它定义时所处的环境[1]。",
        "examples": [
            TeachingExample(
                title="一个计数的闭包",
                steps=["定义一个内部函数", "内部函数修改外部变量"],
                question="为什么这里要写 nonlocal？",
            ),
        ],
        "citations": ["1"],
        "follow_up": "闭包和作用域是什么关系？",
    }
    data.update(overrides)
    return TeachingOutput.model_validate(data)


class _ScriptedTeacher:
    """按脚本返回结构化输出的客户端。"""

    def __init__(self, output: TeachingOutput | None = None) -> None:
        """初始化。

        Args:
            output: 要返回的输出。
        """
        self.output = output or _output()
        self.requests: list[object] = []

    async def generate(self, request: object) -> object:
        """生成（未使用）。

        Args:
            request: 请求。

        Raises:
            LLMError: 不该被调用。
        """
        raise LLMError("不该被调用")

    async def generate_structured(self, request: object, output_model: type) -> object:
        """返回脚本里的输出。

        Args:
            request: 请求。
            output_model: 目标模型。

        Returns:
            结构化输出。
        """
        self.requests.append(request)
        return self.output


# --- 指令 ---------------------------------------------------------------


def test_direct_instructions_cover_the_researched_points() -> None:
    """指令的每一段都对应一条研究结论——这样改指令时知道在动什么。"""
    instructions = DIRECT_TEACHING_INSTRUCTIONS

    assert "分步" in instructions
    assert "引导问题" in instructions
    assert "表面不同" in instructions
    assert "常见错误" in instructions
    assert "引用" in instructions


def test_unknown_mode_is_rejected() -> None:
    """不认识的模式要报错——不能静默退回直接教学。"""
    with pytest.raises(ValueError, match="未知的教学模式"):
        instructions_for("lecture")


def test_socratic_mode_has_its_own_instructions() -> None:
    """苏格拉底式有自己的指令（Day 34 交付）——不是直接教学的副本。"""
    instructions = instructions_for(SOCRATIC)

    assert instructions != instructions_for(DIRECT)
    assert "不要直接给答案" in instructions


def test_direct_mode_returns_its_instructions() -> None:
    """直接教学模式返回自己的指令 ✓。"""
    assert instructions_for(DIRECT) == DIRECT_TEACHING_INSTRUCTIONS


# --- 渲染 ---------------------------------------------------------------


def test_render_includes_explanation_examples_and_references() -> None:
    """渲染要包含讲解、示例与参考文献。"""
    rendered = render_output(_output(), CitationSet((_citation(),)))

    assert "闭包是函数" in rendered
    assert "一个计数的闭包" in rendered
    assert "想一想：为什么这里要写 nonlocal？" in rendered
    assert "closures.md" in rendered


def test_render_includes_the_follow_up_question() -> None:
    """下一步引导问题要出现。"""
    rendered = render_output(_output(), CitationSet())

    assert "下一步：闭包和作用域是什么关系？" in rendered


# --- 教学流程 -----------------------------------------------------------


def test_teacher_calls_the_model_with_the_instructions() -> None:
    """指令要作为系统指令送进去 ✓。"""
    client = _ScriptedTeacher()
    teacher = DirectTeacher(client=client)  # type: ignore[arg-type]

    asyncio.run(teacher.teach(_request()))

    request = client.requests[0]
    assert DIRECT_TEACHING_INSTRUCTIONS in request.instructions  # type: ignore[attr-defined]


def test_teacher_returns_the_structured_output() -> None:
    """结构化输出原样带回来 ✓。"""
    client = _ScriptedTeacher()
    teacher = DirectTeacher(client=client)  # type: ignore[arg-type]

    turn = asyncio.run(teacher.teach(_request()))

    assert turn.output.explanation.startswith("闭包是函数")
    assert turn.output.examples[0].steps


def test_teacher_validates_citations_against_the_available_ones() -> None:
    """教学输出里声明的引用要与本次可用的引用对上。"""
    client = _ScriptedTeacher(_output(citations=["1", "9"]))
    teacher = DirectTeacher(client=client)  # type: ignore[arg-type]

    turn = asyncio.run(teacher.teach(_request()))

    assert turn.grounding.fabricated_markers == ("9",)
    assert turn.deliver is False


def test_teacher_delivers_a_grounded_answer() -> None:
    """引用都对得上就交付 ✓。"""
    client = _ScriptedTeacher()
    teacher = DirectTeacher(client=client)  # type: ignore[arg-type]

    turn = asyncio.run(teacher.teach(_request()))

    assert turn.deliver is True
    assert "闭包是函数" in turn.render()


def test_teacher_reports_the_context_it_used() -> None:
    """装配好的上下文要能看见——「为什么这次这么短」有答案。"""
    client = _ScriptedTeacher()
    teacher = DirectTeacher(client=client)  # type: ignore[arg-type]

    turn = asyncio.run(teacher.teach(_request()))

    assert turn.context is not None
    assert "question" in [section.name for section in turn.context.sections]


def test_mock_client_can_drive_the_teacher() -> None:
    """Mock 客户端能驱动整条链路——默认套件不依赖外部服务 ✓。"""
    payload = json.dumps(
        {
            "explanation": "闭包是函数加上它定义时所处的环境[1]。",
            "examples": [{"title": "例子", "steps": ["一步"], "question": "为什么？"}],
            "citations": ["1"],
            "follow_up": "接下来看什么？",
        },
        ensure_ascii=False,
    )
    teacher = DirectTeacher(client=MockLLMClient(text=payload))

    turn = asyncio.run(teacher.teach(_request()))

    assert turn.output.explanation


# --- 教学评估 -----------------------------------------------------------


def _result(
    case: TeachingCase,
    output: TeachingOutput,
    citations: CitationSet,
) -> TeachingCaseResult:
    """构造一条评估结果。

    Args:
        case: 用例。
        output: 教学输出。
        citations: 引用集合。

    Returns:
        评估结果。
    """
    from agent_learning_coach.core.application.grounding import check_grounding
    from agent_learning_coach.core.application.teaching import TeachingCaseResult, TeachingTurn

    grounding = check_grounding(output.explanation, citations)
    return TeachingCaseResult(
        case=case,
        turn=TeachingTurn(output=output, grounding=grounding, citations=citations),
    )


def test_evaluation_counts_structure_and_citations() -> None:
    """评估要量结构与引用——机制层面。"""
    case = TeachingCase(question="闭包是什么？", category="basic")
    results = [
        _result(case, _output(), CitationSet((_citation(),))),
        _result(case, _output(explanation="", examples=[], follow_up=""), CitationSet()),
    ]

    metrics = evaluate_teaching(results)

    assert metrics.cases == 2
    assert metrics.with_explanation == pytest.approx(0.5)
    assert metrics.with_examples == pytest.approx(0.5)
    assert metrics.with_follow_up == pytest.approx(0.5)


def test_evaluation_flags_examples_without_questions() -> None:
    """没有引导问题的示例只是被动阅读——要能看见。"""
    case = TeachingCase(question="闭包是什么？")
    output = _output(
        examples=[TeachingExample(title="例子", steps=["一步"], question="")],
    )

    metrics = evaluate_teaching([_result(case, output, CitationSet())])

    assert metrics.examples_with_questions == pytest.approx(0.0)


def test_evaluation_counts_fabricated_markers() -> None:
    """编造的引用要计入编造率。"""
    case = TeachingCase(question="闭包是什么？")
    output = _output(citations=["1", "9"])

    metrics = evaluate_teaching([_result(case, output, CitationSet((_citation(),)))])

    assert metrics.fabrication_rate == pytest.approx(0.5)


def test_case_requires_a_question() -> None:
    """缺 question 的用例要报错。"""
    with pytest.raises(ValueError, match="缺少 question"):
        TeachingCase.from_dict({}, index=1)


def test_empty_evaluation_gives_zero_metrics() -> None:
    """空评估集给出零指标。"""
    metrics = evaluate_teaching([])

    assert metrics.cases == 0
    assert metrics.with_explanation == 0.0
