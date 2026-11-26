"""计划版本的测试（Topic 24）。

覆盖五组关注点：

**修订 vs 全新计划**——同一 ``PlanId`` 下版本号 +1 是修订；新 ``PlanId`` 是全新
计划。

**依据**——每一版都记着它基于什么生成。

**差异**——与上一版相比新增、移除、重排了哪些知识点。

**排序**——同一计划线内按版本号，不按时间。

**过滤**——按目标、按计划线；「当前版本」答得准。

本文件是**集成测试**：目标 → 诊断 → 计划 → 修订 → 查询走完整条链。
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
    DomainError,
    GoalId,
    KnowledgePoint,
    KnowledgePointId,
    KnowledgeRelation,
    KnowledgeStructure,
    LearnerId,
    LearningGoal,
    LearningProgress,
    PlanId,
    PlanVersionId,
    TimeWindow,
    UniformEffort,
    current_plan_version,
    fingerprint_structure,
    plan_versions,
    revise_plan,
    start_plan,
)


def _structure() -> KnowledgeStructure:
    """构造一条链式结构：先学 k1，再学 k2，最后学 k3。"""
    return KnowledgeStructure(
        points=(
            KnowledgePoint(id=KnowledgePointId("k1"), title="作用域"),
            KnowledgePoint(id=KnowledgePointId("k2"), title="闭包"),
            KnowledgePoint(id=KnowledgePointId("k3"), title="装饰器"),
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


def _goal() -> LearningGoal:
    """构造一个目标：要求掌握 k1、k2、k3。"""
    return LearningGoal(
        id=GoalId("goal-1"),
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-1"),
        statement="学会装饰器",
        outcomes=("能写出装饰器",),
        requirements=(
            CapabilityRequirement(point_id=KnowledgePointId("k1"), outcome="能写出装饰器"),
            CapabilityRequirement(point_id=KnowledgePointId("k2"), outcome="能写出装饰器"),
            CapabilityRequirement(point_id=KnowledgePointId("k3"), outcome="能写出装饰器"),
        ),
        daily_effort=DailyEffort(minutes_per_day=45),
        time_window=TimeWindow(start=date(2026, 11, 25), end=date(2026, 12, 25)),
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


def _diagnosis() -> Diagnosis:
    """构造一次诊断：k1 会、k2 有缺口、k3 未判定。"""
    from agent_learning_coach.core.learning import (
        DiagnosticEvidence,
        DiagnosticExercise,
        DiagnosticSubmission,
        DiagnosticVerdict,
    )

    def _evidence(point_id: str, grading: str) -> DiagnosticEvidence:
        return DiagnosticEvidence(
            exercise=DiagnosticExercise(
                id=f"e-{point_id}",
                prompt="闭包是什么？",
                point_id=KnowledgePointId(point_id),
            ),
            submission=DiagnosticSubmission(exercise_id=f"e-{point_id}", answer="闭包记住了作用域"),
            grading=grading,  # type: ignore[arg-type]
            point_id=KnowledgePointId(point_id),
        )

    return Diagnosis(
        id=DiagnosisId("diagnosis-1"),
        learner_id=LearnerId("learner-1"),
        goal_id=GoalId("goal-1"),
        at=1000.0,
        verdicts=(
            DiagnosticVerdict(
                point_id=KnowledgePointId("k1"),
                verdict="known",
                reason="答对作用域",
                evidence=(_evidence("k1", "correct"),),
            ),
            DiagnosticVerdict(
                point_id=KnowledgePointId("k2"),
                verdict="gap",
                reason="答错捕获",
                evidence=(_evidence("k2", "incorrect"),),
            ),
        ),
    )


def _start(plan_id: str = "plan-1", **kwargs) -> object:
    """开一份计划。"""
    return start_plan(
        _goal(),
        _structure(),
        _progress(),
        plan_id=PlanId(plan_id),
        version_id=PlanVersionId(f"{plan_id}-v1"),
        reason=kwargs.pop("reason", "初次规划"),
        at=kwargs.pop("at", 1000.0),
        **kwargs,
    )


# --- 修订 vs 全新计划 ---------------------------------------------------


def test_first_version_has_no_delta() -> None:
    """首个版本没有上一版，因此没有差异。"""
    version = _start()

    assert version.number == 1
    assert version.delta is None


def test_revision_increments_version_number() -> None:
    """修订：同一计划线，版本号 +1。"""
    first = _start()
    second = revise_plan(
        [first],
        _goal(),
        _structure(),
        _progress("k1"),
        version_id=PlanVersionId("plan-1-v2"),
        reason="学习者掌握了 k1",
        at=2000.0,
    )

    assert second.plan_id == first.plan_id
    assert second.number == 2


def test_new_plan_line_starts_at_version_one() -> None:
    """全新计划：新计划线，版本号从 1 开始。"""
    first = _start(plan_id="plan-1")
    other = _start(plan_id="plan-2", reason="换了学习路径", at=3000.0)

    assert other.plan_id != first.plan_id
    assert other.number == 1
    assert other.delta is None


def test_revision_requires_same_plan_line() -> None:
    """修订需要同一条计划线的版本——混着改就是两件事。"""
    first = _start(plan_id="plan-1")
    second = _start(plan_id="plan-2", reason="换了学习路径", at=3000.0)

    with pytest.raises(DomainError, match="同一条计划线"):
        revise_plan(
            [first, second],
            _goal(),
            _structure(),
            _progress(),
            version_id=PlanVersionId("plan-x-v3"),
            reason="改",
            at=4000.0,
        )


def test_revision_requires_existing_versions() -> None:
    """没有版本就谈不上修订。"""
    with pytest.raises(DomainError, match="已有的版本"):
        revise_plan(
            [],
            _goal(),
            _structure(),
            _progress(),
            version_id=PlanVersionId("plan-1-v2"),
            reason="改",
            at=2000.0,
        )


def test_duplicate_version_numbers_are_rejected() -> None:
    """同一计划线内版本号不能重复。"""
    first = _start()
    duplicate = _start()
    duplicate = type(duplicate)(
        id=PlanVersionId("plan-1-v1b"),
        plan_id=first.plan_id,
        number=1,
        plan=duplicate.plan,
        basis=duplicate.basis,
        reason=duplicate.reason,
        at=duplicate.at,
        delta=None,
    )

    with pytest.raises(DomainError, match="版本号不能重复"):
        revise_plan(
            [first, duplicate],
            _goal(),
            _structure(),
            _progress(),
            version_id=PlanVersionId("plan-1-v2"),
            reason="改",
            at=2000.0,
        )


# --- 依据 ---------------------------------------------------------------


def test_basis_records_the_inputs() -> None:
    """依据记着这一版是基于什么生成的。"""
    version = _start(diagnosis=_diagnosis())

    assert version.basis.goal_id == GoalId("goal-1")
    assert version.basis.learner_id == LearnerId("learner-1")
    assert version.basis.course_id == CourseId("course-1")
    assert version.basis.diagnosis_id == DiagnosisId("diagnosis-1")
    assert version.basis.required_points == (
        KnowledgePointId("k1"),
        KnowledgePointId("k2"),
        KnowledgePointId("k3"),
    )


def test_basis_records_progress_snapshot() -> None:
    """依据记着生成时已经会了什么——两个版本的区别正在于此。"""
    first = _start()
    second = revise_plan(
        [first],
        _goal(),
        _structure(),
        _progress("k1"),
        version_id=PlanVersionId("plan-1-v2"),
        reason="学习者掌握了 k1",
        at=2000.0,
    )

    assert first.basis.learned_points == ()
    assert second.basis.learned_points == (KnowledgePointId("k1"),)


def test_basis_records_structure_fingerprint() -> None:
    """依据记着课程结构的指纹——结构变了旧版本是否还成立能看出来。"""
    version = _start()

    assert version.basis.structure_fingerprint == fingerprint_structure(_structure())


def test_fingerprint_changes_with_structure() -> None:
    """结构变了，指纹就变。"""
    changed = KnowledgeStructure(
        points=(
            KnowledgePoint(id=KnowledgePointId("k1"), title="作用域"),
            KnowledgePoint(id=KnowledgePointId("k2"), title="闭包"),
        ),
    )

    assert fingerprint_structure(changed) != fingerprint_structure(_structure())


def test_fingerprint_is_stable() -> None:
    """同样的结构总是同一个指纹。"""
    assert fingerprint_structure(_structure()) == fingerprint_structure(_structure())


# --- 差异 ---------------------------------------------------------------


def test_revision_records_removed_units() -> None:
    """学习者学会了 k1，它就从计划里移除——差异要记着。"""
    first = _start()
    second = revise_plan(
        [first],
        _goal(),
        _structure(),
        _progress("k1"),
        version_id=PlanVersionId("plan-1-v2"),
        reason="学习者掌握了 k1",
        at=2000.0,
    )

    assert second.delta is not None
    assert second.delta.removed == (KnowledgePointId("k1"),)
    assert second.delta.added == ()
    assert not second.delta.is_empty


def test_revision_records_rescheduled_units() -> None:
    """单元还在但排期变了，差异要记着。"""
    first = _start(estimator=UniformEffort(30))
    second = revise_plan(
        [first],
        _goal(),
        _structure(),
        _progress(),
        version_id=PlanVersionId("plan-1-v2"),
        reason="估时口径变了",
        at=2000.0,
        estimator=UniformEffort(60),
    )

    assert second.delta is not None
    assert set(second.delta.changed) == {
        KnowledgePointId("k1"),
        KnowledgePointId("k2"),
        KnowledgePointId("k3"),
    }


def test_revision_without_changes_has_empty_delta() -> None:
    """什么都没变，差异就是空的。"""
    first = _start()
    second = revise_plan(
        [first],
        _goal(),
        _structure(),
        _progress(),
        version_id=PlanVersionId("plan-1-v2"),
        reason="重新生成一次",
        at=2000.0,
    )

    assert second.delta is not None
    assert second.delta.is_empty


# --- 排序 ---------------------------------------------------------------


def test_versions_are_sorted_by_number_within_a_line() -> None:
    """同一计划线内按版本号排序——版本号是领域分配的序号。"""
    first = _start()
    second = revise_plan(
        [first],
        _goal(),
        _structure(),
        _progress("k1"),
        version_id=PlanVersionId("plan-1-v2"),
        reason="学习者掌握了 k1",
        at=2000.0,
    )

    assert [version.number for version in plan_versions([second, first])] == [1, 2]


def test_number_order_beats_time_order() -> None:
    """时间乱序时，版本号仍然说了算——时间由调用方提供，可能补录。"""
    first = _start(at=5000.0)
    second = revise_plan(
        [first],
        _goal(),
        _structure(),
        _progress("k1"),
        version_id=PlanVersionId("plan-1-v2"),
        reason="学习者掌握了 k1",
        at=1000.0,
    )

    assert [version.number for version in plan_versions([second, first])] == [1, 2]


# --- 过滤与当前版本 -----------------------------------------------------


def test_filter_by_plan_line() -> None:
    """按计划线过滤。"""
    first = _start(plan_id="plan-1")
    other = _start(plan_id="plan-2", reason="换了学习路径", at=3000.0)

    found = plan_versions([first, other], plan_id=PlanId("plan-1"))

    assert [version.id for version in found] == [PlanVersionId("plan-1-v1")]


def test_filter_by_goal() -> None:
    """按目标过滤。"""
    first = _start()
    other = _start(plan_id="plan-2", reason="换了学习路径", at=3000.0)

    found = plan_versions([first, other], goal_id=GoalId("goal-1"))

    assert len(found) == 2


def test_current_version_is_the_latest_of_the_line() -> None:
    """当前版本 = 计划线里版本号最大的那个。"""
    first = _start()
    second = revise_plan(
        [first],
        _goal(),
        _structure(),
        _progress("k1"),
        version_id=PlanVersionId("plan-1-v2"),
        reason="学习者掌握了 k1",
        at=2000.0,
    )

    current = current_plan_version([first, second], plan_id=PlanId("plan-1"))

    assert current is not None
    assert current.number == 2


def test_current_plan_line_is_the_newest_one_for_a_goal() -> None:
    """同一个目标下，最新开始的那条计划线是当前的。"""
    old_line = _start(plan_id="plan-1")
    new_line = _start(plan_id="plan-2", reason="换了学习路径", at=9000.0)

    current = current_plan_version([old_line, new_line], goal_id=GoalId("goal-1"))

    assert current is not None
    assert current.plan_id == PlanId("plan-2")


def test_current_version_is_none_when_nothing_matches() -> None:
    """没有符合条件的版本时，答案是「没有」而不是随便给一个。"""
    assert current_plan_version([], goal_id=GoalId("goal-1")) is None
    assert current_plan_version([], plan_id=PlanId("plan-1")) is None


# --- 边界 ---------------------------------------------------------------


def test_version_requires_a_reason() -> None:
    """没有理由的版本变化无法解释。"""
    with pytest.raises(DomainError, match="改动理由"):
        start_plan(
            _goal(),
            _structure(),
            _progress(),
            plan_id=PlanId("plan-1"),
            version_id=PlanVersionId("plan-1-v1"),
            reason="",
            at=1000.0,
        )


def test_version_number_must_be_positive() -> None:
    """版本号从 1 开始。"""
    version = _start()
    with pytest.raises(DomainError, match="正整数"):
        type(version)(
            id=version.id,
            plan_id=version.plan_id,
            number=0,
            plan=version.plan,
            basis=version.basis,
            reason=version.reason,
            at=version.at,
            delta=None,
        )


def test_first_version_cannot_have_a_delta() -> None:
    """首个版本没有上一版，因此不能带差异。"""
    version = _start()
    with pytest.raises(DomainError, match="首个计划版本"):
        type(version)(
            id=version.id,
            plan_id=version.plan_id,
            number=1,
            plan=version.plan,
            basis=version.basis,
            reason=version.reason,
            at=version.at,
            delta=object(),  # type: ignore[arg-type]
        )


def test_plan_version_rejects_progress_of_another_learner() -> None:
    """别人的进度排不出这个目标的计划。"""
    progress = LearningProgress(learner_id=LearnerId("learner-9"), course_id=CourseId("course-1"))

    with pytest.raises(DomainError, match="无法排计划"):
        start_plan(
            _goal(),
            _structure(),
            progress,
            plan_id=PlanId("plan-1"),
            version_id=PlanVersionId("plan-1-v1"),
            reason="初次规划",
            at=1000.0,
        )
