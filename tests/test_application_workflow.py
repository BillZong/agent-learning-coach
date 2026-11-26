"""应用层用例编排的测试（Day 26）。

这是**用例级集成测试**：走完整的链路——目标 → 诊断 → 进度 → 计划 → 落库，
中间经过真实的仓储实现（内存版）与真实的领域规则。

覆盖六组关注点：

**编排**--诊断 → 进度 → 计划 → 落库，一次跑完。

**同步**--诊断说会、进度说没学 → 同步为已学会；其余不动。

**矛盾**--诊断说不会、进度说已学会 → 保留进度并报告。

**取舍**--多份诊断取最新的一份。

**写边界**--先写进度后写计划；进度没变就不写；乐观锁冲突会重读重算。

**幂等**--同一份诊断跑两次，第二次不改状态。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date

import pytest

from agent_learning_coach.core.application import (
    LearningWorkflow,
    NotFoundError,
    sync_diagnosis,
)
from agent_learning_coach.core.learning import (
    CapabilityRequirement,
    Course,
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
    LearningProgress,
    TimeWindow,
)
from agent_learning_coach.core.storage import (
    ConflictError,
    InMemoryCourseRepository,
    InMemoryDiagnosisRepository,
    InMemoryGoalRepository,
    InMemoryPlanVersionRepository,
    InMemoryProgressRepository,
    Stored,
)


def _structure() -> KnowledgeStructure:
    """构造一条链式结构：先学 k1，再学 k2。"""
    return KnowledgeStructure(
        points=(
            KnowledgePoint(id=KnowledgePointId("k1"), title="作用域"),
            KnowledgePoint(id=KnowledgePointId("k2"), title="闭包"),
        ),
        relations=(
            KnowledgeRelation(
                kind="requires",
                source=KnowledgePointId("k2"),
                target=KnowledgePointId("k1"),
            ),
        ),
    )


def _course() -> Course:
    """构造一门课。"""
    return Course(id=CourseId("course-1"), title="Python 基础", structure=_structure())


def _goal() -> LearningGoal:
    """构造一个目标：要求掌握 k1、k2。"""
    return LearningGoal(
        id=GoalId("goal-1"),
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-1"),
        statement="学会闭包",
        outcomes=("能解释闭包",),
        requirements=(
            CapabilityRequirement(point_id=KnowledgePointId("k1"), outcome="能解释闭包"),
            CapabilityRequirement(point_id=KnowledgePointId("k2"), outcome="能解释闭包"),
        ),
        daily_effort=DailyEffort(minutes_per_day=45),
        time_window=TimeWindow(start=date(2026, 11, 25), end=date(2026, 12, 25)),
    )


def _verdict(point_id: str, verdict: str, reason: str = "测试判定") -> DiagnosticVerdict:
    """构造一条判定。"""
    return DiagnosticVerdict(
        point_id=KnowledgePointId(point_id),
        verdict=verdict,  # type: ignore[arg-type]
        reason=reason,
        evidence=(
            DiagnosticEvidence(
                exercise=DiagnosticExercise(
                    id=f"e-{point_id}",
                    prompt="闭包是什么？",
                    point_id=KnowledgePointId(point_id),
                ),
                submission=DiagnosticSubmission(
                    exercise_id=f"e-{point_id}",
                    answer="闭包记住了作用域",
                ),
                grading="correct" if verdict == "known" else "incorrect",
                point_id=KnowledgePointId(point_id),
            ),
        ),
    )


def _diagnosis(
    diagnosis_id: str = "diagnosis-1",
    *,
    at: float = 1000.0,
    verdicts: tuple[DiagnosticVerdict, ...] | None = None,
) -> Diagnosis:
    """构造一次诊断。"""
    return Diagnosis(
        id=DiagnosisId(diagnosis_id),
        learner_id=LearnerId("learner-1"),
        goal_id=GoalId("goal-1"),
        at=at,
        verdicts=(
            verdicts if verdicts is not None else (_verdict("k1", "known"), _verdict("k2", "gap"))
        ),
    )


@dataclass
class _Harness:
    """测试用的编排器与它的仓储——全部是公开对象。"""

    workflow: LearningWorkflow
    goals: InMemoryGoalRepository
    progresses: InMemoryProgressRepository
    diagnoses: InMemoryDiagnosisRepository
    plans: InMemoryPlanVersionRepository
    courses: InMemoryCourseRepository


def _harness(
    *,
    with_goal: bool = True,
    with_course: bool = True,
    diagnoses: tuple[Diagnosis, ...] = (),
    progress: LearningProgress | None = None,
) -> _Harness:
    """构造一个编排器（默认带上目标与课程）。

    Args:
        with_goal: 是否预置目标。
        with_course: 是否预置课程。
        diagnoses: 预置的诊断。
        progress: 预置的进度。

    Returns:
        测试装置。
    """
    goals = InMemoryGoalRepository()
    progresses = InMemoryProgressRepository()
    diagnosis_repo = InMemoryDiagnosisRepository()
    plans = InMemoryPlanVersionRepository()
    courses = InMemoryCourseRepository()

    if with_goal:
        asyncio.run(goals.save(Stored(value=_goal(), revision=0)))
    if with_course:
        asyncio.run(courses.save(Stored(value=_course(), revision=0)))
    for diagnosis in diagnoses:
        asyncio.run(diagnosis_repo.save(Stored(value=diagnosis, revision=0)))
    if progress is not None:
        asyncio.run(progresses.save(Stored(value=progress, revision=0)))

    workflow = LearningWorkflow(
        goals=goals,
        progresses=progresses,
        diagnoses=diagnosis_repo,
        plans=plans,
        courses=courses,
    )
    return _Harness(
        workflow=workflow,
        goals=goals,
        progresses=progresses,
        diagnoses=diagnosis_repo,
        plans=plans,
        courses=courses,
    )


class _RecordingProgressRepository(InMemoryProgressRepository):
    """记录写入顺序的进度仓储。"""

    def __init__(self, events: list[str]) -> None:
        """初始化。

        Args:
            events: 写入顺序记录。
        """
        super().__init__()
        self._events = events

    async def save(self, record: Stored[LearningProgress]) -> Stored[LearningProgress]:
        """记录并写入。"""
        self._events.append("progress")
        return await super().save(record)


class _RecordingPlanRepository(InMemoryPlanVersionRepository):
    """记录写入顺序的计划版本仓储。"""

    def __init__(self, events: list[str]) -> None:
        """初始化。

        Args:
            events: 写入顺序记录。
        """
        super().__init__()
        self._events = events

    async def save(self, record: Stored[object]) -> Stored[object]:
        """记录并写入。"""
        self._events.append("plan")
        return await super().save(record)


class _ConflictingProgressRepository(InMemoryProgressRepository):
    """第一次保存时制造一次乐观锁冲突。"""

    def __init__(self) -> None:
        """初始化。"""
        super().__init__()
        self._pending = True

    async def save(self, record: Stored[LearningProgress]) -> Stored[LearningProgress]:
        """先写进去（模拟别人先写了），再报冲突。

        Args:
            record: 待写入的记录。

        Returns:
            写入后的记录。

        Raises:
            ConflictError: 第一次保存时抛出。
        """
        if self._pending:
            self._pending = False
            await super().save(record)
            raise ConflictError("存储中的版本是 1，与携带的版本 0 不一致")
        return await super().save(record)


# --- 编排 ---------------------------------------------------------------


def test_diagnose_and_plan_runs_the_whole_chain() -> None:
    """诊断 → 进度 → 计划 → 落库，一次跑完。"""
    harness = _harness(diagnoses=(_diagnosis(),))

    result = asyncio.run(
        harness.workflow.diagnose_and_plan(GoalId("goal-1"), at=2000.0, reason="初次诊断后规划"),
    )

    assert result.diagnosis_id == DiagnosisId("diagnosis-1")
    assert result.progress_written is True
    assert result.sync.applied == (KnowledgePointId("k1"),)
    assert result.plan_version.value.number == 1
    assert [unit.point_id for unit in result.plan_version.value.plan.units] == [
        KnowledgePointId("k2"),
    ]


def test_plan_excludes_points_the_diagnosis_confirmed() -> None:
    """诊断判定已掌握的知识点不进计划。"""
    harness = _harness(diagnoses=(_diagnosis(),))

    result = asyncio.run(
        harness.workflow.diagnose_and_plan(GoalId("goal-1"), at=2000.0, reason="初次诊断后规划"),
    )

    planned = {unit.point_id for unit in result.plan_version.value.plan.units}
    assert KnowledgePointId("k1") not in planned


def test_missing_goal_is_reported() -> None:
    """目标不存在就说清楚，不要返回一个空计划。"""
    harness = _harness(with_goal=False)

    with pytest.raises(NotFoundError, match="目标"):
        asyncio.run(
            harness.workflow.diagnose_and_plan(GoalId("goal-1"), at=1000.0, reason="规划"),
        )


def test_missing_course_is_reported() -> None:
    """课程不存在也要说清楚——没有知识结构就排不出计划。"""
    harness = _harness(with_course=False)

    with pytest.raises(NotFoundError, match="课程"):
        asyncio.run(
            harness.workflow.diagnose_and_plan(GoalId("goal-1"), at=1000.0, reason="规划"),
        )


def test_unknown_diagnosis_id_is_reported() -> None:
    """指定的诊断不存在要说清楚。"""
    harness = _harness()

    with pytest.raises(NotFoundError, match="诊断"):
        asyncio.run(
            harness.workflow.diagnose_and_plan(
                GoalId("goal-1"),
                at=1000.0,
                reason="规划",
                diagnosis_id=DiagnosisId("diagnosis-9"),
            ),
        )


# --- 同步与矛盾 ---------------------------------------------------------


def test_sync_applies_known_verdict() -> None:
    """诊断说会、进度说没学 → 同步为已学会（来源 assessed）。"""
    progress = LearningProgress(learner_id=LearnerId("learner-1"), course_id=CourseId("course-1"))

    synced, report = sync_diagnosis(_diagnosis(), progress, at=1000.0)

    assert report.applied == (KnowledgePointId("k1"),)
    assert synced.state_of(KnowledgePointId("k1")) == "learned"
    point = synced.point(KnowledgePointId("k1"))
    assert point is not None
    assert point.transitions[0].source_kind == "assessed"


def test_sync_leaves_gap_verdict_alone() -> None:
    """诊断说不会 → 进度不动（还没学）。"""
    progress = LearningProgress(learner_id=LearnerId("learner-1"), course_id=CourseId("course-1"))

    synced, report = sync_diagnosis(_diagnosis(), progress, at=1000.0)

    assert synced.state_of(KnowledgePointId("k2")) == "not_started"
    assert report.conflicts == ()


def test_conflict_is_reported_and_progress_is_kept() -> None:
    """诊断说不会、进度说已学会 → 保留进度并报告矛盾。"""
    progress = LearningProgress(learner_id=LearnerId("learner-1"), course_id=CourseId("course-1"))
    progress = progress.start(KnowledgePointId("k2"), at=500.0, reason="开始学")
    progress = progress.complete(KnowledgePointId("k2"), at=600.0, reason="学会了")

    synced, report = sync_diagnosis(_diagnosis(), progress, at=1000.0)

    assert synced.state_of(KnowledgePointId("k2")) == "learned"
    assert len(report.conflicts) == 1
    conflict = report.conflicts[0]
    assert conflict.point_id == KnowledgePointId("k2")
    assert conflict.state == "learned"
    assert "不允许把已学会退回未学会" in conflict.resolution


def test_conflict_does_not_block_planning() -> None:
    """矛盾要被报告，但**不阻断**流程——进度才是事实。"""
    progress = LearningProgress(learner_id=LearnerId("learner-1"), course_id=CourseId("course-1"))
    progress = progress.start(KnowledgePointId("k2"), at=400.0, reason="开始学")
    progress = progress.complete(KnowledgePointId("k2"), at=500.0, reason="学会了")
    harness = _harness(diagnoses=(_diagnosis(),), progress=progress)

    result = asyncio.run(
        harness.workflow.diagnose_and_plan(GoalId("goal-1"), at=2000.0, reason="规划"),
    )

    assert len(result.sync.conflicts) == 1
    assert result.plan_version.value.number == 1


# --- 取舍与幂等 ---------------------------------------------------------


def test_latest_diagnosis_is_used() -> None:
    """多份诊断 → 取最新的一份（按诊断发生的时间）。"""
    harness = _harness(
        diagnoses=(
            _diagnosis("diagnosis-old", at=1000.0),
            _diagnosis("diagnosis-new", at=5000.0, verdicts=(_verdict("k2", "known"),)),
        ),
    )

    result = asyncio.run(
        harness.workflow.diagnose_and_plan(GoalId("goal-1"), at=6000.0, reason="规划"),
    )

    assert result.diagnosis_id == DiagnosisId("diagnosis-new")
    assert result.sync.applied == (KnowledgePointId("k2"),)


def test_explicit_diagnosis_id_wins() -> None:
    """给了诊断 id 就用它，不取最新的。"""
    harness = _harness(
        diagnoses=(
            _diagnosis("diagnosis-old", at=1000.0),
            _diagnosis("diagnosis-new", at=5000.0),
        ),
    )

    result = asyncio.run(
        harness.workflow.diagnose_and_plan(
            GoalId("goal-1"),
            at=6000.0,
            reason="规划",
            diagnosis_id=DiagnosisId("diagnosis-old"),
        ),
    )

    assert result.diagnosis_id == DiagnosisId("diagnosis-old")


def test_running_twice_is_idempotent() -> None:
    """同一份诊断跑两次：第二次不改进度，但会出一个新的计划版本。"""
    harness = _harness(diagnoses=(_diagnosis(),))

    first = asyncio.run(
        harness.workflow.diagnose_and_plan(GoalId("goal-1"), at=2000.0, reason="初次规划"),
    )
    second = asyncio.run(
        harness.workflow.diagnose_and_plan(GoalId("goal-1"), at=3000.0, reason="再规划一次"),
    )

    assert first.progress_written is True
    assert second.progress_written is False
    assert second.sync.applied == ()
    assert second.plan_version.value.number == 2
    assert second.plan_version.value.plan_id == first.plan_version.value.plan_id


# --- 写边界 -------------------------------------------------------------


def test_plan_is_revised_not_restarted() -> None:
    """目标已有计划线 → 修订它（同一计划线，版本号 +1）。"""
    harness = _harness(diagnoses=(_diagnosis(),))

    first = asyncio.run(
        harness.workflow.diagnose_and_plan(GoalId("goal-1"), at=2000.0, reason="初次规划"),
    )
    second = asyncio.run(
        harness.workflow.diagnose_and_plan(GoalId("goal-1"), at=3000.0, reason="再规划一次"),
    )

    assert second.plan_version.value.plan_id == first.plan_version.value.plan_id
    assert second.plan_version.value.number == 2
    assert second.plan_version.value.delta is not None


def test_record_learning_updates_progress_and_replans() -> None:
    """学完一个知识点 → 进度更新 → 重新规划（那个点从计划里消失）。"""
    harness = _harness(diagnoses=(_diagnosis(),))
    asyncio.run(
        harness.workflow.diagnose_and_plan(GoalId("goal-1"), at=2000.0, reason="初次规划"),
    )

    result = asyncio.run(
        harness.workflow.record_learning(
            GoalId("goal-1"),
            KnowledgePointId("k2"),
            at=3000.0,
            reason="学完了闭包",
        ),
    )

    assert result.progress.value.state_of(KnowledgePointId("k2")) == "learned"
    assert result.plan_version.value.number == 2
    assert result.plan_version.value.plan.units == ()


def test_record_learning_rejects_illegal_transition() -> None:
    """重复完成同一个知识点是非法迁移——领域层拒绝。"""
    progress = LearningProgress(learner_id=LearnerId("learner-1"), course_id=CourseId("course-1"))
    progress = progress.start(KnowledgePointId("k2"), at=400.0, reason="开始学")
    progress = progress.complete(KnowledgePointId("k2"), at=500.0, reason="学会了")
    harness = _harness(progress=progress)

    with pytest.raises(DomainError):
        asyncio.run(
            harness.workflow.record_learning(
                GoalId("goal-1"),
                KnowledgePointId("k2"),
                at=3000.0,
                reason="再学一次",
            ),
        )


def test_current_unit_returns_the_next_one() -> None:
    """当前该学哪个单元——Tutor 从这里开始。"""
    harness = _harness(diagnoses=(_diagnosis(),))
    asyncio.run(
        harness.workflow.diagnose_and_plan(GoalId("goal-1"), at=2000.0, reason="初次规划"),
    )

    unit = asyncio.run(harness.workflow.current_unit(GoalId("goal-1")))

    assert unit is not None
    assert unit.point_id == KnowledgePointId("k2")


def test_current_unit_is_none_without_a_plan() -> None:
    """还没有计划 → 没有当前单元。"""
    harness = _harness()

    assert asyncio.run(harness.workflow.current_unit(GoalId("goal-1"))) is None


# --- 写边界与冲突 -------------------------------------------------------


def test_progress_is_written_before_the_plan() -> None:
    """先写进度，后写计划——否则会写出一份「依据是假的」计划。"""
    events: list[str] = []
    goals = InMemoryGoalRepository()
    courses = InMemoryCourseRepository()
    diagnoses = InMemoryDiagnosisRepository()
    asyncio.run(goals.save(Stored(value=_goal(), revision=0)))
    asyncio.run(courses.save(Stored(value=_course(), revision=0)))
    asyncio.run(diagnoses.save(Stored(value=_diagnosis(), revision=0)))
    workflow = LearningWorkflow(
        goals=goals,
        progresses=_RecordingProgressRepository(events),
        diagnoses=diagnoses,
        plans=_RecordingPlanRepository(events),
        courses=courses,
    )

    asyncio.run(
        workflow.diagnose_and_plan(GoalId("goal-1"), at=2000.0, reason="初次规划"),
    )

    assert events == ["progress", "plan"]


def test_conflict_triggers_reread_and_retry() -> None:
    """乐观锁冲突 → 重读、重算、再写，而不是盲目重试。"""
    goals = InMemoryGoalRepository()
    courses = InMemoryCourseRepository()
    diagnoses = InMemoryDiagnosisRepository()
    asyncio.run(goals.save(Stored(value=_goal(), revision=0)))
    asyncio.run(courses.save(Stored(value=_course(), revision=0)))
    asyncio.run(diagnoses.save(Stored(value=_diagnosis(), revision=0)))
    workflow = LearningWorkflow(
        goals=goals,
        progresses=_ConflictingProgressRepository(),
        diagnoses=diagnoses,
        plans=InMemoryPlanVersionRepository(),
        courses=courses,
    )

    result = asyncio.run(
        workflow.diagnose_and_plan(GoalId("goal-1"), at=2000.0, reason="初次规划"),
    )

    assert result.progress_written is True
    assert result.progress.value.state_of(KnowledgePointId("k1")) == "learned"
    assert result.plan_version.value.number == 1
