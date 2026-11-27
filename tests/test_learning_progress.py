"""学习进度模型的测试（Topic 18）。

覆盖五组关注点：

**归属**——进度是（学习者 × 课程）的关联，不进 Learner；同一课程只有一个进度。

**状态机**——知识点级状态与任务状态是两套东西：没有 ``skipped``，``blocked``
是派生结果；状态由迁移历史派生。

**已有知识**——诊断判定是迁移序列里的第一条，与「本次学会」区分得开。

**派生查询**——目标完成度、被阻塞的知识点、现在可以开始的知识点。

**边界**——跨聚合一致性检查；进度不进运行时的 ``GoalSpec``。
"""

from __future__ import annotations

import pytest

from agent_learning_coach.core.learning import (
    CapabilityRequirement,
    Course,
    CourseId,
    DomainError,
    GoalId,
    KnowledgePoint,
    KnowledgePointId,
    KnowledgeRelation,
    KnowledgeStructure,
    Learner,
    LearnerId,
    LearningGoal,
    LearningProgress,
    LearningState,
    PointProgress,
    PointTransition,
    blocked_points,
    check_progress_against_course,
    goal_completion,
    ready_points,
    to_goal_spec,
)

AT = 1_700_000_000.0
"""固定的迁移时刻，让测试不依赖真实时钟。"""


def _point(point_id: str, title: str) -> KnowledgePoint:
    """构造一个知识点。"""
    return KnowledgePoint(id=KnowledgePointId(point_id), title=title)


def _course() -> Course:
    """构造一门有依赖链的课程：k1 → k2 → k3，另有独立点 k4。"""
    return Course(
        id=CourseId("course-js-core"),
        title="JavaScript 核心",
        structure=KnowledgeStructure(
            points=(
                _point("k1", "函数与作用域"),
                _point("k2", "闭包"),
                _point("k3", "高阶函数"),
                _point("k4", "模块化"),
            ),
            relations=(
                KnowledgeRelation(
                    kind="requires",
                    source=KnowledgePointId("k2"),
                    target=KnowledgePointId("k1"),
                ),
                KnowledgeRelation(
                    kind="requires",
                    source=KnowledgePointId("k3"),
                    target=KnowledgePointId("k2"),
                ),
            ),
        ),
    )


def _progress() -> LearningProgress:
    """构造一份空进度。"""
    return LearningProgress(learner_id=LearnerId("learner-1"), course_id=CourseId("course-js-core"))


def _goal() -> LearningGoal:
    """构造一个要求掌握 k2 与 k3 的目标。"""
    return LearningGoal(
        id=GoalId("goal-closures"),
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-js-core"),
        statement="掌握闭包与高阶函数",
        outcomes=("能写出一个闭包例子", "能用闭包实现高阶函数"),
        requirements=(
            CapabilityRequirement(point_id=KnowledgePointId("k2"), outcome="能写出一个闭包例子"),
            CapabilityRequirement(
                point_id=KnowledgePointId("k3"),
                outcome="能用闭包实现高阶函数",
            ),
        ),
    )


# --- 归属 -----------------------------------------------------------------


def test_progress_is_scoped_to_learner_and_course() -> None:
    """进度是（学习者 × 课程）的关联，不挂在学习者身上。"""
    progress = _progress()

    assert progress.learner_id == LearnerId("learner-1")
    assert progress.course_id == CourseId("course-js-core")
    assert progress.points == ()


def test_learner_does_not_hold_progress() -> None:
    """学习者画像不承载进度——否则每学一个知识点就要改一次学习者。"""
    learner = Learner(
        id=LearnerId("learner-1"),
        name="小王",
        background=("有 Python 经验",),
    )

    assert not hasattr(learner, "progress")
    assert not hasattr(learner, "points")


def test_progress_rejects_duplicate_points() -> None:
    """同一个知识点只能有一条进度。"""
    with pytest.raises(DomainError, match="同一个知识点只能有一条学习进度"):
        LearningProgress(
            learner_id=LearnerId("learner-1"),
            course_id=CourseId("course-js-core"),
            points=(
                PointProgress(point_id=KnowledgePointId("k1")),
                PointProgress(point_id=KnowledgePointId("k1")),
            ),
        )


# --- 状态机 ---------------------------------------------------------------


def test_point_starts_not_started() -> None:
    """没记录过的点就是没学过。"""
    point = PointProgress(point_id=KnowledgePointId("k1"))

    assert point.state == "not_started"
    assert not point.is_learned
    assert point.started_at is None
    assert point.learned_at is None


def test_point_learns_through_start_and_complete() -> None:
    """学习路径：未开始 → 学习中 → 已学会。"""
    point = PointProgress(point_id=KnowledgePointId("k1"))
    started = point.start(at=AT, reason="开始读作用域")
    learned = started.complete(at=AT + 60, reason="通过练习")

    assert started.state == "in_progress"
    assert learned.state == "learned"
    assert learned.is_learned
    assert learned.started_at == AT
    assert learned.learned_at == AT + 60


def test_point_state_is_derived_from_history() -> None:
    """状态由迁移历史派生，不单独存储——历史是唯一事实来源。"""
    learned = (
        PointProgress(point_id=KnowledgePointId("k1"))
        .start(at=AT, reason="开始")
        .complete(at=AT + 60, reason="学会")
    )

    assert learned.state == "learned"
    assert len(learned.transitions) == 2
    assert learned.transitions[0].source == "not_started"
    assert learned.transitions[0].target == "in_progress"
    assert learned.transitions[1].target == "learned"


def test_point_can_be_reviewed() -> None:
    """复习：已学会 → 学习中 → 已学会。"""
    learned = (
        PointProgress(point_id=KnowledgePointId("k1"))
        .start(at=AT, reason="开始")
        .complete(at=AT + 60, reason="学会")
    )
    reviewing = learned.review(at=AT + 86400, reason="复习")
    relearned = reviewing.complete(at=AT + 90000, reason="复习通过")

    assert reviewing.state == "in_progress"
    assert relearned.state == "learned"
    assert relearned.learned_at == AT + 90000


def test_point_cannot_be_reviewed_before_learned() -> None:
    """没学会的点没有「复习」这回事——那是「开始学习」。"""
    with pytest.raises(DomainError, match="不能变更为学习中：只有已学会的知识点才能复习"):
        PointProgress(point_id=KnowledgePointId("k1")).review(at=AT, reason="复习")


def test_point_cannot_backtrack_to_not_started() -> None:
    """刻意不允许回退：放弃一次学习只是不再推进它。"""
    started = PointProgress(point_id=KnowledgePointId("k1")).start(at=AT, reason="开始")

    with pytest.raises(DomainError, match="不能从学习中变更为未开始"):
        started._transition(
            "not_started",
            expected_source="in_progress",
            source_kind="learned",
            at=AT,
            reason="放弃",
            detail="放弃一次学习只是不再推进它",
        )


def test_point_transition_requires_reason() -> None:
    """没有理由的状态变化无法解释。"""
    with pytest.raises(DomainError, match="学习状态迁移理由不能为空"):
        PointProgress(point_id=KnowledgePointId("k1")).start(at=AT, reason=" ")


def test_broken_point_history_is_rejected() -> None:
    """迁移历史必须首尾相接。"""
    with pytest.raises(DomainError, match="学习状态历史断链"):
        PointProgress(
            point_id=KnowledgePointId("k1"),
            transitions=(
                _transition("in_progress", "not_started"),
                _transition("learned", "not_started"),
            ),
        )


def _transition(target: LearningState, source: LearningState) -> PointTransition:
    """构造一条迁移记录（测试辅助）。"""
    return PointTransition(
        source=source,
        target=target,
        source_kind="learned",
        reason="测试",
        at=AT,
    )


# --- 已有知识 -------------------------------------------------------------


def test_prior_knowledge_is_the_first_transition() -> None:
    """诊断判定已有基础 = 迁移序列的第一条；与「本次学会」区分得开。"""
    assessed = PointProgress(point_id=KnowledgePointId("k1")).assess(at=AT, reason="诊断已掌握")

    assert assessed.state == "learned"
    assert assessed.transitions[0].source_kind == "assessed"

    learned = (
        PointProgress(point_id=KnowledgePointId("k2"))
        .start(at=AT, reason="开始")
        .complete(at=AT + 60, reason="学会")
    )
    assert all(item.source_kind == "learned" for item in learned.transitions)


def test_assess_only_applies_to_not_started_points() -> None:
    """已经在学的点不能被判定为「本来就会」——它是本次学的。"""
    started = PointProgress(point_id=KnowledgePointId("k1")).start(at=AT, reason="开始")

    with pytest.raises(DomainError, match="不能变更为已学会：只有还没学过的知识点才能判定"):
        started.assess(at=AT, reason="诊断已掌握")


# --- 派生查询 -------------------------------------------------------------


def test_progress_tracks_points_and_queries_states() -> None:
    """进度按知识点组织，可查询各状态的点。"""
    progress = (
        _progress()
        .assess(KnowledgePointId("k1"), at=AT, reason="诊断已掌握")
        .start(KnowledgePointId("k2"), at=AT, reason="开始学闭包")
        .start(KnowledgePointId("k4"), at=AT, reason="自学模块化")
        .complete(KnowledgePointId("k4"), at=AT + 60, reason="自学完成")
    )

    assert progress.state_of(KnowledgePointId("k1")) == "learned"
    assert progress.state_of(KnowledgePointId("k2")) == "in_progress"
    assert progress.state_of(KnowledgePointId("k3")) == "not_started"
    assert progress.learned_points() == (KnowledgePointId("k1"), KnowledgePointId("k4"))


def test_goal_completion_counts_learned_requirements() -> None:
    """目标完成度：目标要求几个点，学会几个。"""
    progress = (
        _progress()
        .start(KnowledgePointId("k2"), at=AT, reason="开始学闭包")
        .complete(KnowledgePointId("k2"), at=AT + 60, reason="学会闭包")
    )

    completion = goal_completion(progress, _goal())

    assert completion.required == 2
    assert completion.learned == 1
    assert completion.outstanding == (KnowledgePointId("k3"),)
    assert completion.ratio == 0.5


def test_goal_completion_rejects_other_course() -> None:
    """进度与目标不属于同一门课时，比较它们没有意义。"""
    progress = LearningProgress(
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-other"),
    )

    with pytest.raises(DomainError, match="无法比较"):
        goal_completion(progress, _goal())


def test_blocked_points_are_derived_from_prerequisites() -> None:
    """被阻塞是派生结果：前置没学会就还不能学。

    k2 被 k1 阻塞；k3 被 k2 阻塞（k2 还没学会）。k1 与 k4 没有前置，不阻塞。
    """
    structure = _course().structure
    progress = _progress().start(KnowledgePointId("k2"), at=AT, reason="直接学闭包")

    assert blocked_points(progress, structure) == (
        KnowledgePointId("k2"),
        KnowledgePointId("k3"),
    )


def test_learned_point_is_not_blocked() -> None:
    """已学会的点不再受前置约束。"""
    structure = _course().structure
    progress = (
        _progress()
        .assess(KnowledgePointId("k1"), at=AT, reason="诊断已掌握")
        .start(KnowledgePointId("k2"), at=AT, reason="开始学闭包")
        .complete(KnowledgePointId("k2"), at=AT + 60, reason="学会闭包")
    )

    assert blocked_points(progress, structure) == ()


def test_ready_points_are_ready_to_learn() -> None:
    """可以开始的点：没学过，且直接前置都学完了。

    覆盖**全部知识点**，不只进度里已记录的那些——k4 没有前置，从一开始就能学。
    顺序按知识结构的学习顺序（稳定拓扑序）：k4 在 k2 之前。
    """
    structure = _course().structure
    progress = _progress().assess(KnowledgePointId("k1"), at=AT, reason="诊断已掌握")

    assert ready_points(progress, structure) == (KnowledgePointId("k4"), KnowledgePointId("k2"))


# --- 边界：跨聚合一致 -----------------------------------------------------


def test_progress_against_course_accepts_consistent_progress() -> None:
    """一致时没有跨聚合问题。"""
    progress = _progress().assess(KnowledgePointId("k1"), at=AT, reason="诊断已掌握")

    assert check_progress_against_course(progress, _course()) == ()


def test_progress_against_course_reports_unknown_point() -> None:
    """进度引用了课程里不存在的知识点。"""
    progress = _progress().assess(KnowledgePointId("k9"), at=AT, reason="诊断已掌握")

    issues = check_progress_against_course(progress, _course())

    assert len(issues) == 1
    assert issues[0].code == "unknown_point"
    assert "k9" in issues[0].message


def test_progress_against_course_reports_course_mismatch() -> None:
    """进度所属课程不是给定课程时，后续校验没有意义。"""
    progress = LearningProgress(
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-other"),
    )

    issues = check_progress_against_course(progress, _course())

    assert len(issues) == 1
    assert issues[0].code == "course_mismatch"


def test_progress_against_course_checks_prerequisite_closure() -> None:
    """知识状态对先修封闭：学会了 k2 就应当已经学会 k1。"""
    progress = (
        _progress()
        .start(KnowledgePointId("k2"), at=AT, reason="跳过前置直接学")
        .complete(KnowledgePointId("k2"), at=AT + 60, reason="学会闭包")
    )

    issues = check_progress_against_course(progress, _course())

    assert len(issues) == 1
    assert issues[0].code == "prerequisite_not_learned"
    assert "k1" in issues[0].message


# --- 边界：领域模型 → 运行时 ----------------------------------------------


def test_progress_does_not_enter_goal_spec() -> None:
    """进度不进 GoalSpec——它是领域侧的事实，不是规划输入。"""
    progress = (
        _progress()
        .start(KnowledgePointId("k2"), at=AT, reason="开始学闭包")
        .complete(KnowledgePointId("k2"), at=AT + 60, reason="学会闭包")
    )

    spec = to_goal_spec(_goal())

    assert not hasattr(spec, "progress")
    assert not hasattr(spec, "learning_state")
    assert progress.state_of(KnowledgePointId("k2")) == "learned"
