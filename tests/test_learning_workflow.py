"""学习领域的集成测试（Topic 19）。

单聚合测试见 ``test_learning_domain.py``（Topic 15）、``test_knowledge_structure.py``
（Topic 16）、``test_learning_goal.py``（Topic 17）、``test_learning_progress.py``
（Topic 18）。本文件验证的是**四个聚合协同走一遍**——这些协作没有任何单聚合测试
覆盖（ADR-0019 §5）。

覆盖五组关注点：

**完整链路**——画像 → 课程结构 → 目标 → 诊断判定已有基础 → 学习 → 完成度。

**一致性检查真的被调用**——跨聚合的校验不是在单测里各测各的，而是在这条链上
真的拦住非法数据。

**终态路径**——达成 / 放弃 / 过期，在有进度的情况下行为正确。

**失败路径**——引用不存在的知识点、违反前置封闭性，都会被拦下。

**边界**——进度与目标配对（同一学习者、同一门课）。
"""

from __future__ import annotations

from datetime import date

import pytest

from agent_learning_coach.core.learning import (
    CapabilityRequirement,
    Course,
    CourseId,
    DailyEffort,
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
    TimeWindow,
    check_goal_against_course,
    check_progress_against_course,
    goal_completion,
    ready_points,
    to_goal_spec,
)

AT = 1_700_000_000.0
"""固定的迁移时刻，让测试不依赖真实时钟。"""


def _learner() -> Learner:
    """构造一个学习者画像。"""
    return Learner(
        id=LearnerId("learner-1"),
        name="小王",
        background=("有 Python 经验",),
        preferences=("喜欢先动手再读文档",),
    )


def _course() -> Course:
    """构造一门有依赖链的课程：k1 → k2 → k3，另有独立点 k4。"""
    return Course(
        id=CourseId("course-js-core"),
        title="JavaScript 核心",
        structure=KnowledgeStructure(
            points=(
                KnowledgePoint(id=KnowledgePointId("k1"), title="函数与作用域"),
                KnowledgePoint(id=KnowledgePointId("k2"), title="闭包"),
                KnowledgePoint(id=KnowledgePointId("k3"), title="高阶函数"),
                KnowledgePoint(id=KnowledgePointId("k4"), title="模块化"),
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


def _goal(learner: Learner, course: Course) -> LearningGoal:
    """构造一个绑定 k2 与 k3 的目标。"""
    goal = LearningGoal(
        id=GoalId("goal-closures"),
        learner_id=learner.id,
        course_id=course.id,
        statement="掌握闭包与高阶函数",
        outcomes=("能写出一个闭包例子", "能用闭包实现高阶函数"),
        time_window=TimeWindow(start=date(2026, 1, 1), end=date(2026, 3, 1)),
        daily_effort=DailyEffort(minutes_per_day=90),
        prerequisites=(KnowledgePointId("k1"),),
    )
    goal = goal.bind_requirements(
        (
            CapabilityRequirement(
                point_id=KnowledgePointId("k2"),
                outcome="能写出一个闭包例子",
            ),
            CapabilityRequirement(
                point_id=KnowledgePointId("k3"),
                outcome="能用闭包实现高阶函数",
            ),
        ),
    )
    return goal.activate(at=AT, reason="诊断完成，绑定知识点")


def _progress(learner: Learner, course: Course) -> LearningProgress:
    """构造一份空进度。"""
    return LearningProgress(learner_id=learner.id, course_id=course.id)


# --- 完整链路 -------------------------------------------------------------


def test_full_learning_workflow() -> None:
    """诊断 → 目标 → 学习 → 完成度：四个聚合协同走通。

    这是 Phase 1 建模阶段的代表性场景：画像给出学习者，课程给出知识结构，
    目标绑定要掌握的知识点，诊断判定已有基础，学习推进进度，最后算出目标完成度。
    """
    learner = _learner()
    course = _course()
    goal = _goal(learner, course)

    # 目标引用的知识点都在课程里——跨聚合一致
    assert check_goal_against_course(goal, course) == ()

    # 诊断：判定学习者本来就会 k1
    progress = _progress(learner, course).assess(
        KnowledgePointId("k1"),
        at=AT,
        reason="诊断：已掌握作用域",
    )

    # 前置封闭性成立
    assert check_progress_against_course(progress, course) == ()

    # 现在可以学 k2（前置 k1 已学会）
    assert ready_points(progress, course.structure) == (
        KnowledgePointId("k4"),
        KnowledgePointId("k2"),
    )

    # 学习 k2
    progress = progress.start(KnowledgePointId("k2"), at=AT, reason="开始学闭包")
    progress = progress.complete(KnowledgePointId("k2"), at=AT + 3600, reason="通过闭包练习")

    # 完成度：2 个要求点，会了 1 个
    completion = goal_completion(progress, goal)
    assert completion.required == 2
    assert completion.learned == 1
    assert completion.outstanding == (KnowledgePointId("k3"),)
    assert completion.ratio == 0.5

    # 前置封闭性仍然成立：k2 的前置 k1 已学会
    assert check_progress_against_course(progress, course) == ()

    # 学完 k3，目标结构性完成
    progress = progress.start(KnowledgePointId("k3"), at=AT + 7200, reason="开始学高阶函数")
    progress = progress.complete(KnowledgePointId("k3"), at=AT + 10800, reason="通过高阶函数练习")
    assert goal_completion(progress, goal).ratio == 1.0


def test_workflow_hands_off_to_runtime() -> None:
    """领域 → 运行时的交接在完整链路里仍然成立。"""
    learner = _learner()
    course = _course()
    goal = _goal(learner, course)

    spec = to_goal_spec(goal)

    assert spec.goal_id == "goal-closures"
    assert spec.outcomes == ("能写出一个闭包例子", "能用闭包实现高阶函数")
    assert spec.constraints == (
        "时间范围：2026-01-01 ~ 2026-03-01",
        "每日投入：90 分钟/天",
        "前置知识点：k1",
    )


# --- 一致性检查真的被调用 -------------------------------------------------


def test_workflow_rejects_unknown_knowledge_point() -> None:
    """目标引用课程里不存在的知识点时，一致性检查拦得住。

    绑定发生在激活之前——激活后不能改绑定（那是范围变更，属 Topic 48）。
    """
    learner = _learner()
    course = _course()
    goal = LearningGoal(
        id=GoalId("goal-unknown"),
        learner_id=learner.id,
        course_id=course.id,
        statement="掌握某个不存在的知识点",
        outcomes=("能写出一个闭包例子",),
    ).bind_requirements(
        (
            CapabilityRequirement(
                point_id=KnowledgePointId("k9"),
                outcome="能写出一个闭包例子",
            ),
        ),
    )

    issues = check_goal_against_course(goal, course)

    assert len(issues) == 1
    assert issues[0].code == "unknown_requirement_point"


def test_workflow_rejects_prerequisite_closure_violation() -> None:
    """跳过前置直接学会一个点，前置封闭性检查拦得住。"""
    learner = _learner()
    course = _course()
    progress = _progress(learner, course)
    progress = progress.start(KnowledgePointId("k3"), at=AT, reason="跳过前置直接学")
    progress = progress.complete(KnowledgePointId("k3"), at=AT + 3600, reason="学会了高阶函数")

    issues = check_progress_against_course(progress, course)

    assert len(issues) == 1
    assert issues[0].code == "prerequisite_not_learned"


# --- 终态路径 -------------------------------------------------------------


def test_goal_terminal_states_with_progress() -> None:
    """有进度的情况下，三个终态路径行为正确。"""
    learner = _learner()
    course = _course()
    goal = _goal(learner, course)
    progress = (
        _progress(learner, course)
        .assess(KnowledgePointId("k1"), at=AT, reason="诊断已掌握")
        .start(KnowledgePointId("k2"), at=AT, reason="开始学闭包")
        .complete(KnowledgePointId("k2"), at=AT + 3600, reason="学会闭包")
    )

    achieved = goal.achieve(at=AT + 7200, reason="通过练习")
    abandoned = goal.abandon(at=AT + 7200, reason="换方向")
    expired = goal.expire(at=AT + 7200, reason="期限已过")

    assert achieved.status == "achieved"
    assert abandoned.status == "abandoned"
    assert expired.status == "expired"
    # 进度不随目标状态变化——学习过的事实不因为目标结束而消失
    assert progress.state_of(KnowledgePointId("k2")) == "learned"
    assert goal_completion(progress, goal).ratio == 0.5


def test_achieved_goal_refuses_further_progress() -> None:
    """目标达成后，进度不再为它推进——但事实保留。"""
    learner = _learner()
    course = _course()
    goal = _goal(learner, course).achieve(at=AT, reason="已完成")
    progress = _progress(learner, course)

    with pytest.raises(DomainError, match="状态为已达成"):
        goal.suspend(at=AT, reason="再看看")

    assert progress.state_of(KnowledgePointId("k2")) == "not_started"


# --- 边界：进度与目标的配对 -----------------------------------------------


def test_completion_rejects_progress_of_another_learner() -> None:
    """A 的进度算不出 B 的目标的完成度（ADR-0019 §2 修的缺口）。"""
    learner = _learner()
    course = _course()
    goal = _goal(learner, course)
    other_progress = LearningProgress(
        learner_id=LearnerId("learner-2"),
        course_id=course.id,
    )

    with pytest.raises(DomainError, match="无法比较"):
        goal_completion(other_progress, goal)


def test_completion_rejects_progress_of_another_course() -> None:
    """不同课程之间的进度与目标也不可比。"""
    learner = _learner()
    course = _course()
    goal = _goal(learner, course)
    other_progress = LearningProgress(
        learner_id=learner.id,
        course_id=CourseId("course-other"),
    )

    with pytest.raises(DomainError, match="无法比较"):
        goal_completion(other_progress, goal)


def test_same_course_different_learners_have_separate_progress() -> None:
    """同一门课下不同学习者的进度互不干扰——这是按（学习者 × 课程）分片的意义。"""
    learner = _learner()
    course = _course()
    goal = _goal(learner, course)

    mine = (
        _progress(learner, course)
        .start(KnowledgePointId("k2"), at=AT, reason="开始学闭包")
        .complete(KnowledgePointId("k2"), at=AT + 3600, reason="学会闭包")
    )
    theirs = LearningProgress(
        learner_id=LearnerId("learner-2"),
        course_id=course.id,
    )

    assert mine.state_of(KnowledgePointId("k2")) == "learned"
    assert theirs.state_of(KnowledgePointId("k2")) == "not_started"
    assert goal_completion(mine, goal).learned == 1
