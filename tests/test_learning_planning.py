"""学习计划的测试（Topic 23）。

覆盖六组关注点：

**范围**--计划装哪些知识点：目标要求 ∪ 前置闭包 − 已学会。

**顺序**--依赖是硬约束：前置一定排在后继之前。

**理由**--每个单元都要能回答「为什么学它」。

**时间**--按每日投入装箱；没约定投入就不排期。

**超载**--装不下要报告，不能少排。

**边界**--进度与目标对不上、知识点不在结构里，都要拒绝。
"""

from __future__ import annotations

from datetime import date

import pytest

from agent_learning_coach.core.learning import (
    CapabilityRequirement,
    CourseId,
    DailyEffort,
    Diagnosis,
    DiagnosisId,
    DiagnosticEvidence,
    DiagnosticExercise,
    DiagnosticSubmission,
    DiagnosticVerdict,
    DomainError,
    GoalId,
    KnowledgePoint,
    KnowledgePointId,
    KnowledgeRelation,
    KnowledgeStructure,
    LearnerId,
    LearningGoal,
    LearningPlan,
    LearningProgress,
    LearningUnit,
    TimeWindow,
    UniformEffort,
    generate_plan,
)


def _day(day: int) -> date:
    """构造一个日期。"""
    return date(2026, 11, day)


def _point(point_id: str, title: str) -> KnowledgePoint:
    """构造一个知识点。"""
    return KnowledgePoint(id=KnowledgePointId(point_id), title=title)


def _structure() -> KnowledgeStructure:
    """构造一条链式结构：先学 k1，再学 k2，最后学 k3；另有独立的 k4。

    ``requires`` 的方向是「source 必须先掌握 target」，因此关系写成 k3 → k2、
    k2 → k1。
    """
    return KnowledgeStructure(
        points=(
            _point("k1", "作用域"),
            _point("k2", "闭包"),
            _point("k3", "装饰器"),
            _point("k4", "模块"),
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
    )


def _goal(
    *,
    point_ids: tuple[str, ...] = ("k3",),
    outcomes: tuple[str, ...] = ("能写出装饰器",),
    daily_effort: DailyEffort | None = None,
    time_window: TimeWindow | None = None,
    prerequisites: tuple[str, ...] = (),
) -> LearningGoal:
    """构造一个目标。"""
    return LearningGoal(
        id=GoalId("goal-1"),
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-1"),
        statement="学会装饰器",
        outcomes=outcomes,
        requirements=tuple(
            CapabilityRequirement(point_id=KnowledgePointId(point_id), outcome=outcome)
            for point_id, outcome in zip(point_ids, outcomes, strict=False)
        ),
        daily_effort=daily_effort,
        time_window=time_window,
        prerequisites=tuple(KnowledgePointId(item) for item in prerequisites),
    )


def _progress(*learned: str) -> LearningProgress:
    """构造一份进度。"""
    progress = LearningProgress(
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-1"),
    )
    for point_id in learned:
        progress = progress.start(KnowledgePointId(point_id), at=0.0, reason="开始学")
        progress = progress.complete(KnowledgePointId(point_id), at=1.0, reason="学会了")
    return progress


# --- 范围 ---------------------------------------------------------------


def test_scope_includes_prerequisite_closure() -> None:
    """目标只要求 k3，计划也要带上它的前置 k1、k2——否则计划执行不了。"""
    plan = generate_plan(_goal(), _structure(), _progress())

    assert [unit.point_id for unit in plan.units] == [
        KnowledgePointId("k1"),
        KnowledgePointId("k2"),
        KnowledgePointId("k3"),
    ]


def test_scope_excludes_learned_points() -> None:
    """已经学会的点不再排进计划。"""
    plan = generate_plan(_goal(), _structure(), _progress("k1"))

    assert [unit.point_id for unit in plan.units] == [
        KnowledgePointId("k2"),
        KnowledgePointId("k3"),
    ]


def test_scope_excludes_unrelated_points() -> None:
    """与目标无关的知识点不进计划。"""
    plan = generate_plan(_goal(), _structure(), _progress())

    assert KnowledgePointId("k4") not in {unit.point_id for unit in plan.units}


def test_empty_plan_when_everything_learned() -> None:
    """都学会了就没有要学的东西——空计划是合法计划。"""
    plan = generate_plan(_goal(), _structure(), _progress("k1", "k2", "k3"))

    assert plan.units == ()
    assert plan.total_minutes == 0


# --- 顺序 ---------------------------------------------------------------


def test_prerequisite_comes_before_dependent() -> None:
    """依赖是硬约束：前置一定排在后继之前。"""
    plan = generate_plan(_goal(), _structure(), _progress())

    order = [unit.point_id for unit in plan.units]
    assert order.index(KnowledgePointId("k1")) < order.index(KnowledgePointId("k2"))
    assert order.index(KnowledgePointId("k2")) < order.index(KnowledgePointId("k3"))


def test_depends_on_lists_only_planned_prerequisites() -> None:
    """单元的前置只含计划内的——已经学会的前置已被满足，不构成依赖。"""
    plan = generate_plan(_goal(), _structure(), _progress("k1"))

    k2 = plan.unit_for(KnowledgePointId("k2"))
    k3 = plan.unit_for(KnowledgePointId("k3"))
    assert k2 is not None
    assert k3 is not None
    assert k2.depends_on == ()
    assert k3.depends_on == (KnowledgePointId("k2"),)


def test_plan_rejects_dependency_outside_plan() -> None:
    """依赖指向计划外的知识点是自相矛盾——构造期就拒绝。"""
    unit = LearningUnit(
        point_id=KnowledgePointId("k3"),
        title="装饰器",
        reason="目标要求掌握它",
        depends_on=(KnowledgePointId("k9"),),
    )

    with pytest.raises(DomainError, match="不在计划内"):
        LearningPlan(goal_id=GoalId("goal-1"), units=(unit,))


def test_plan_rejects_dependent_before_prerequisite() -> None:
    """后继排在前置之前是错的排期——构造期就拒绝。"""
    first = LearningUnit(
        point_id=KnowledgePointId("k2"),
        title="闭包",
        reason="目标要求掌握它",
        day=1,
    )
    second = LearningUnit(
        point_id=KnowledgePointId("k3"),
        title="装饰器",
        reason="目标要求掌握它",
        depends_on=(KnowledgePointId("k2"),),
        day=0,
    )

    with pytest.raises(DomainError, match="排在了它的前置"):
        LearningPlan(goal_id=GoalId("goal-1"), units=(first, second))


# --- 理由 ---------------------------------------------------------------


def test_reason_for_goal_requirement() -> None:
    """目标要求的点，理由说清它支撑哪条结果。"""
    plan = generate_plan(_goal(), _structure(), _progress())

    unit = plan.unit_for(KnowledgePointId("k3"))
    assert unit is not None
    assert "能写出装饰器" in unit.reason
    assert unit.outcomes == ("能写出装饰器",)


def test_reason_for_prerequisite_only_point() -> None:
    """只作为前置进来的点，理由说清它是誰的前置。"""
    plan = generate_plan(_goal(), _structure(), _progress())

    unit = plan.unit_for(KnowledgePointId("k1"))
    assert unit is not None
    assert "前置" in unit.reason
    assert unit.outcomes == ()


def test_reason_prefers_diagnosis_gap() -> None:
    """诊断判定为缺口时，理由说清诊断结论。"""
    diagnosis = Diagnosis(
        id=DiagnosisId("diagnosis-1"),
        learner_id=LearnerId("learner-1"),
        goal_id=GoalId("goal-1"),
        verdicts=(
            DiagnosticVerdict(
                point_id=KnowledgePointId("k3"),
                verdict="gap",
                reason="答错作用域与捕获的定义",
                evidence=(
                    DiagnosticEvidence(
                        exercise=DiagnosticExercise(
                            id="e1",
                            prompt="闭包是什么？",
                            point_id=KnowledgePointId("k3"),
                        ),
                        submission=DiagnosticSubmission(exercise_id="e1", answer="普通函数"),
                        grading="incorrect",
                        point_id=KnowledgePointId("k3"),
                    ),
                ),
            ),
        ),
    )

    plan = generate_plan(_goal(), _structure(), _progress(), diagnosis=diagnosis)

    unit = plan.unit_for(KnowledgePointId("k3"))
    assert unit is not None
    assert "诊断判定为缺口" in unit.reason
    assert "答错作用域与捕获的定义" in unit.reason
    assert plan.diagnosis_id is not None


# --- 时间 ---------------------------------------------------------------


def test_units_are_packed_by_daily_capacity() -> None:
    """按每日投入装箱：每装不下就开新天。"""
    goal = _goal(daily_effort=DailyEffort(minutes_per_day=45))
    plan = generate_plan(goal, _structure(), _progress(), estimator=UniformEffort(30))

    assert [unit.day for unit in plan.units] == [0, 1, 2]
    assert [(load.day, load.minutes) for load in plan.days] == [(0, 30), (1, 30), (2, 30)]


def test_no_daily_effort_means_no_schedule() -> None:
    """没约定每日投入就无从排期——不硬排「第 0 天装下全部内容」。"""
    plan = generate_plan(_goal(), _structure(), _progress())

    assert plan.days == ()
    assert all(unit.day is None for unit in plan.units)


def test_day_capacity_is_reported() -> None:
    """每天的负载带上投入上限。"""
    goal = _goal(daily_effort=DailyEffort(minutes_per_day=45))
    plan = generate_plan(goal, _structure(), _progress(), estimator=UniformEffort(30))

    assert all(load.capacity == 45 for load in plan.days)
    assert all(not load.is_overloaded for load in plan.days)


def test_single_unit_longer_than_capacity_is_flagged() -> None:
    """单个单元就超过一天的投入——那天超载。"""
    goal = _goal(daily_effort=DailyEffort(minutes_per_day=20))
    plan = generate_plan(goal, _structure(), _progress(), estimator=UniformEffort(30))

    assert plan.days[0].is_overloaded


# --- 超载 ---------------------------------------------------------------


def test_overload_is_reported_when_window_too_short() -> None:
    """时间窗口装不下要报告——但单元一个都不能少。"""
    goal = _goal(
        daily_effort=DailyEffort(minutes_per_day=30),
        time_window=TimeWindow(start=_day(1), end=_day(2)),
    )
    plan = generate_plan(goal, _structure(), _progress(), estimator=UniformEffort(30))

    assert plan.overload is not None
    assert plan.overload.required_days == 3
    assert plan.overload.available_days == 2
    assert plan.overload.shortfall_days == 1
    assert len(plan.units) == 3


def test_overload_marks_days_beyond_window() -> None:
    """超出期限的天要标出来。"""
    goal = _goal(
        daily_effort=DailyEffort(minutes_per_day=30),
        time_window=TimeWindow(start=_day(1), end=_day(2)),
    )
    plan = generate_plan(goal, _structure(), _progress(), estimator=UniformEffort(30))

    assert [load.within_window for load in plan.days] == [True, True, False]


def test_no_overload_when_plan_fits() -> None:
    """装得下就没有超载报告。"""
    goal = _goal(
        daily_effort=DailyEffort(minutes_per_day=60),
        time_window=TimeWindow(start=_day(1), end=_day(5)),
    )
    plan = generate_plan(goal, _structure(), _progress(), estimator=UniformEffort(30))

    assert plan.overload is None


def test_no_overload_without_time_window() -> None:
    """没有期限就没有「超期」这回事。"""
    goal = _goal(daily_effort=DailyEffort(minutes_per_day=30))
    plan = generate_plan(goal, _structure(), _progress(), estimator=UniformEffort(30))

    assert plan.overload is None


# --- 边界 ---------------------------------------------------------------


def test_unmet_goal_prerequisites_are_reported() -> None:
    """目标的前置还没学会——只报告，不进计划范围。"""
    goal = _goal(prerequisites=("k1",))
    plan = generate_plan(goal, _structure(), _progress())

    assert plan.unmet_prerequisites == (KnowledgePointId("k1"),)


def test_learned_goal_prerequisites_are_not_reported() -> None:
    """前置已经学会就没有未满足项。"""
    goal = _goal(prerequisites=("k1",))
    plan = generate_plan(goal, _structure(), _progress("k1"))

    assert plan.unmet_prerequisites == ()


def test_progress_of_another_learner_is_rejected() -> None:
    """别人的进度算不出这个目标的计划。"""
    progress = LearningProgress(learner_id=LearnerId("learner-9"), course_id=CourseId("course-1"))

    with pytest.raises(DomainError, match="无法排计划"):
        generate_plan(_goal(), _structure(), progress)


def test_progress_of_another_course_is_rejected() -> None:
    """别的课程的进度也算不出来。"""
    progress = LearningProgress(learner_id=LearnerId("learner-1"), course_id=CourseId("course-9"))

    with pytest.raises(DomainError, match="无法排计划"):
        generate_plan(_goal(), _structure(), progress)


def test_goal_point_outside_structure_is_rejected() -> None:
    """目标要求的知识点不在知识结构里——数据不一致，拒绝。"""
    goal = _goal(point_ids=("k9",), outcomes=("能写出装饰器",))

    with pytest.raises(DomainError, match="没有知识点"):
        generate_plan(goal, _structure(), _progress())


def test_custom_estimator_is_used() -> None:
    """估时器可替换——领域层只定义「需要估什么」。"""

    class _ByTitle:
        def estimate(self, point: KnowledgePoint) -> int:
            return 10 if point.title == "闭包" else 20

    plan = generate_plan(_goal(), _structure(), _progress(), estimator=_ByTitle())

    assert plan.total_minutes == 10 + 20 + 20


def test_uniform_effort_rejects_non_positive_minutes() -> None:
    """估时必须为正。"""
    with pytest.raises(DomainError, match="正整数分钟"):
        UniformEffort(0)


def test_unit_rejects_non_positive_estimate() -> None:
    """估时不为正会让计划失去意义——构造期就拒绝。"""
    with pytest.raises(DomainError, match="正整数分钟"):
        LearningUnit(
            point_id=KnowledgePointId("k1"),
            title="作用域",
            reason="目标要求掌握它",
            estimated_minutes=0,
        )
