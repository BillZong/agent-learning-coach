"""Tutor 上下文构建的测试（Day 32）。

覆盖：各段的渲染、**按优先级取舍**、如实报告丢了什么、以及材料标记与工具一致。
"""

from __future__ import annotations

from datetime import date

from agent_learning_coach.core.application.tutor_context import (
    GOAL,
    MATERIALS,
    PROFILE,
    PROGRESS,
    QUESTION,
    TutorContext,
    TutorContextBuilder,
    TutorContextRequest,
)
from agent_learning_coach.core.learning.domain import (
    CourseId,
    KnowledgePointId,
    LearnerId,
)
from agent_learning_coach.core.learning.goal import (
    CapabilityRequirement,
    DailyEffort,
    GoalId,
    LearningGoal,
    TimeWindow,
)
from agent_learning_coach.core.learning.progress import (
    LearningProgress,
    PointProgress,
    PointTransition,
)
from agent_learning_coach.core.llm.types import Message
from agent_learning_coach.core.memory.types import LearnerProfile
from agent_learning_coach.core.rag.citation import Citation, CitationRegistry, CitationSet
from agent_learning_coach.core.rag.types import Chunk

GOAL_ID = "goal-1"


def _goal() -> LearningGoal:
    """构造一个学习目标。"""
    return LearningGoal(
        id=GoalId(GOAL_ID),
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-1"),
        statement="三周内掌握闭包与作用域",
        outcomes=("能解释闭包捕获了什么",),
        requirements=(
            CapabilityRequirement(
                point_id=KnowledgePointId("k1"),
                outcome="能解释闭包捕获了什么",
            ),
        ),
        time_window=TimeWindow(start=date(2026, 1, 1), end=date(2026, 1, 20)),
        daily_effort=DailyEffort(minutes_per_day=60),
    )


def _progress() -> LearningProgress:
    """构造一份学习进度。"""
    return LearningProgress(
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-1"),
        points=(
            PointProgress(
                point_id=KnowledgePointId("k1"),
                transitions=(
                    PointTransition(
                        source="not_started",
                        target="learned",
                        source_kind="learned",
                        reason="练习通过",
                        at=1000.0,
                    ),
                ),
            ),
            PointProgress(
                point_id=KnowledgePointId("k2"),
                transitions=(
                    PointTransition(
                        source="not_started",
                        target="in_progress",
                        source_kind="learned",
                        reason="正在学",
                        at=2000.0,
                    ),
                ),
            ),
        ),
    )


def _profile() -> LearnerProfile:
    """构造一份学习者画像。"""
    return LearnerProfile(
        subject="learner-1",
        level="入门",
        strengths=("动手写代码",),
        weaknesses=("作用域",),
        preferences=("先看例子",),
    )


def _citations() -> CitationSet:
    """构造一组引用。"""
    return CitationSet(
        citations=(
            Citation(
                marker="1",
                chunk_id="c1",
                document_id="d1",
                source="closures.md",
                index=0,
                text="闭包是函数加上它定义时所处的环境。",
            ),
        ),
    )


# --- 装配 ---------------------------------------------------------------


def test_question_is_always_present() -> None:
    """问题永远在——丢了它这一轮就没有回答对象。"""
    context = TutorContextBuilder().build(TutorContextRequest(question="闭包是什么？"))

    assert [section.name for section in context.sections] == [QUESTION]


def test_empty_state_produces_no_placeholder_sections() -> None:
    """缺什么就少一段——不用占位文字填满。"""
    context = TutorContextBuilder().build(TutorContextRequest(question="闭包是什么？"))

    assert context.dropped_names == ()
    assert context.total_tokens > 0


def test_all_sections_are_assembled() -> None:
    """四类状态各占一段。"""
    request = TutorContextRequest(
        question="闭包是什么？",
        profile=_profile(),
        goal=_goal(),
        progress=_progress(),
        citations=_citations(),
    )

    context = TutorContextBuilder().build(request)

    assert [section.name for section in context.sections] == [
        QUESTION,
        MATERIALS,
        GOAL,
        PROGRESS,
        PROFILE,
    ]


def test_materials_use_the_same_markers_as_the_citations() -> None:
    """材料段的标记必须与引用集合一致——否则模型引用的 [1] 不是那一段。"""
    request = TutorContextRequest(question="闭包是什么？", citations=_citations())

    context = TutorContextBuilder().build(request)

    materials = next(section for section in context.sections if section.name == MATERIALS)
    assert "[1]" in materials.content
    assert "closures.md" in materials.content


def test_goal_section_shows_the_statement_and_outcomes() -> None:
    """目标段要能指导教学——目标与可验证的结果都在。"""
    request = TutorContextRequest(question="闭包是什么？", goal=_goal())

    context = TutorContextBuilder().build(request)

    goal = next(section for section in context.sections if section.name == GOAL)
    assert "三周内掌握闭包与作用域" in goal.content
    assert "能解释闭包捕获了什么" in goal.content


def test_progress_section_lists_learned_and_in_progress() -> None:
    """进度段列出已学会与正在学的。"""
    request = TutorContextRequest(question="闭包是什么？", progress=_progress())

    context = TutorContextBuilder().build(request)

    progress = next(section for section in context.sections if section.name == PROGRESS)
    assert "k1" in progress.content
    assert "k2" in progress.content


def test_profile_section_lists_level_and_weaknesses() -> None:
    """画像段给出水平与薄弱点。"""
    request = TutorContextRequest(question="闭包是什么？", profile=_profile())

    context = TutorContextBuilder().build(request)

    profile = next(section for section in context.sections if section.name == PROFILE)
    assert "入门" in profile.content
    assert "作用域" in profile.content


# --- 取舍 ---------------------------------------------------------------


def test_over_budget_drops_the_lowest_priority_section_first() -> None:
    """超预算时先丢优先级最低的——画像是最锦上添花的。"""
    request = TutorContextRequest(
        question="闭包是什么？",
        profile=_profile(),
        goal=_goal(),
        progress=_progress(),
    )
    full = TutorContextBuilder().build(request)
    # 预算刚好够去掉画像那一段
    budget = full.total_tokens - _tokens_of(
        TutorContextBuilder().build(
            TutorContextRequest(question="闭包是什么？", profile=_profile()),
        ),
        PROFILE,
    )

    context = TutorContextBuilder(max_tokens=budget).build(request)

    assert PROFILE in context.dropped_names
    assert QUESTION in [section.name for section in context.sections]


def test_dropped_sections_are_reported() -> None:
    """丢了什么要能被看见——否则「为什么这次变短了」没有答案。"""
    request = TutorContextRequest(
        question="闭包是什么？",
        profile=_profile(),
        goal=_goal(),
    )
    full = TutorContextBuilder().build(request)

    context = TutorContextBuilder(max_tokens=10).build(request)

    assert context.dropped_names
    assert full.total_tokens > context.total_tokens


def test_question_is_never_dropped() -> None:
    """预算再小也不丢问题——丢了就答不了。"""
    request = TutorContextRequest(question="闭包是什么？", profile=_profile())

    context = TutorContextBuilder(max_tokens=1).build(request)

    assert QUESTION in [section.name for section in context.sections]


def test_a_section_is_dropped_whole_not_truncated() -> None:
    """丢的是整段——半截的目标陈述比没有目标更糟。"""
    request = TutorContextRequest(
        question="闭包是什么？",
        profile=_profile(),
        goal=_goal(),
    )

    context = TutorContextBuilder(max_tokens=1).build(request)

    for section in context.sections:
        assert not section.content.endswith("…")
    assert GOAL in context.dropped_names


def test_no_budget_means_no_dropping() -> None:
    """不限预算时全装。"""
    request = TutorContextRequest(
        question="闭包是什么？",
        profile=_profile(),
        goal=_goal(),
    )

    context = TutorContextBuilder().build(request)

    assert context.dropped_names == ()


# --- 呈现 ---------------------------------------------------------------


def test_question_is_a_user_message_and_state_is_system() -> None:
    """材料与状态用 system 角色，问题用 user 角色。"""
    request = TutorContextRequest(question="闭包是什么？", goal=_goal())

    context = TutorContextBuilder().build(request)

    roles = {item.role for item in context.contents if isinstance(item, Message)}
    assert "user" in roles
    assert "system" in roles


def test_contents_are_returned_in_priority_order() -> None:
    """内容按优先级排列——问题在最前，画像在最后。"""
    request = TutorContextRequest(
        question="闭包是什么？",
        profile=_profile(),
        goal=_goal(),
    )

    context = TutorContextBuilder().build(request)

    assert [section.priority for section in context.sections] == sorted(
        section.priority for section in context.sections
    )


def test_render_joins_the_sections() -> None:
    """渲染出可读文本——用于日志与调试。"""
    request = TutorContextRequest(question="闭包是什么？", goal=_goal())

    context = TutorContextBuilder().build(request)

    assert "闭包是什么？" in context.render()
    assert "学习目标" in context.render()


# --- 与检索工具的标记一致性 ---------------------------------------------


def test_registry_can_be_shared_with_the_search_tool() -> None:
    """登记簿可以共享——同一段材料在上下文与工具里是同一个标记。"""
    registry = CitationRegistry()
    chunk = Chunk(
        id="c1",
        document_id="d1",
        text="闭包是函数加上它定义时所处的环境。",
        index=0,
        source="closures.md",
    )

    citation = registry.build(chunk, score=0.9)
    context = TutorContextBuilder().build(
        TutorContextRequest(
            question="闭包是什么？",
            citations=CitationSet(citations=(citation,)),
        ),
    )

    materials = next(section for section in context.sections if section.name == MATERIALS)
    assert f"[{citation.marker}]" in materials.content


def _tokens_of(context: TutorContext, section_name: str) -> int:
    """取某一段的 token 数。

    Args:
        context: 上下文。
        section_name: 段名。

    Returns:
        token 数。
    """
    for section in context.sections:
        if section.name == section_name:
            return section.tokens
    return 0
