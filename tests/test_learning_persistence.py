"""持久化基线的测试（Day 25）。

覆盖五组关注点：

**往返**——每个聚合编码再解码后完全相等。

**解码拒绝**——坏版本、坏结构、坏类型都明确拒绝，不半懂半猜。

**不变量**——负载里的事实不合法时，**读回时**就被领域层拒绝。

**乐观锁**——基于旧版本写入会冲突；冲突是事实，不是异常状态。

**协议**——内存实现真的满足六个仓储协议。
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

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
    Learner,
    LearnerId,
    LearningGoal,
    LearningProgress,
    PlanId,
    PlanVersion,
    PlanVersionId,
    TimeWindow,
    revise_plan,
    start_plan,
)
from agent_learning_coach.core.storage import (
    SCHEMA_VERSION,
    ConflictError,
    CourseRepository,
    DecodeError,
    DiagnosisRepository,
    ExerciseRepository,
    GoalRepository,
    InMemoryCourseRepository,
    InMemoryDiagnosisRepository,
    InMemoryExerciseRepository,
    InMemoryGoalRepository,
    InMemoryPlanVersionRepository,
    InMemoryProgressRepository,
    PlanVersionRepository,
    ProgressRepository,
    Stored,
    course_from_dict,
    course_to_dict,
    diagnosis_from_dict,
    diagnosis_to_dict,
    ensure_writable,
    exercise_from_dict,
    exercise_to_dict,
    goal_from_dict,
    goal_to_dict,
    learner_from_dict,
    learner_to_dict,
    plan_version_from_dict,
    plan_version_to_dict,
    progress_from_dict,
    progress_to_dict,
)


def _course() -> Course:
    """构造一门课。"""
    return Course(
        id=CourseId("course-1"),
        title="Python 基础",
        structure=KnowledgeStructure(
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
        ),
    )


def _goal() -> LearningGoal:
    """构造一个目标。"""
    return LearningGoal(
        id=GoalId("goal-1"),
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-1"),
        statement="学会闭包",
        outcomes=("能解释闭包",),
        requirements=(
            CapabilityRequirement(point_id=KnowledgePointId("k2"), outcome="能解释闭包"),
        ),
        daily_effort=DailyEffort(minutes_per_day=45),
        time_window=TimeWindow(start=date(2026, 11, 25), end=date(2026, 12, 25)),
    )


def _progress() -> LearningProgress:
    """构造一份进度。"""
    progress = LearningProgress(
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-1"),
    )
    progress = progress.start(KnowledgePointId("k1"), at=1000.0, reason="开始学")
    return progress.complete(KnowledgePointId("k1"), at=2000.0, reason="学会了")


def _plan_version() -> PlanVersion:
    """构造一个计划版本。"""
    return start_plan(
        _goal(),
        _course().structure,
        LearningProgress(learner_id=LearnerId("learner-1"), course_id=CourseId("course-1")),
        plan_id=PlanId("plan-1"),
        version_id=PlanVersionId("plan-1-v1"),
        reason="初次规划",
        at=1000.0,
    )


def _diagnosis() -> Diagnosis:
    """构造一次诊断。"""
    return Diagnosis(
        id=DiagnosisId("diagnosis-1"),
        learner_id=LearnerId("learner-1"),
        goal_id=GoalId("goal-1"),
        at=1000.0,
        verdicts=(
            DiagnosticVerdict(
                point_id=KnowledgePointId("k2"),
                verdict="gap",
                reason="答错捕获",
                evidence=(
                    DiagnosticEvidence(
                        exercise=DiagnosticExercise(
                            id="e1",
                            prompt="闭包是什么？",
                            point_id=KnowledgePointId("k2"),
                        ),
                        submission=DiagnosticSubmission(exercise_id="e1", answer="普通函数"),
                        grading="incorrect",
                        point_id=KnowledgePointId("k2"),
                    ),
                ),
            ),
        ),
    )


# --- 往返 ---------------------------------------------------------------


def test_learner_round_trip() -> None:
    """学习者编码再解码后完全相等。"""
    learner = Learner(
        id=LearnerId("learner-1"),
        name="张三",
        background=("有 Python 经验",),
        preferences=("喜欢先动手",),
    )

    assert learner_from_dict(learner_to_dict(learner)) == learner


def test_course_round_trip() -> None:
    """课程（含知识结构）编码再解码后完全相等。"""
    course = _course()

    assert course_from_dict(course_to_dict(course)) == course


def test_goal_round_trip() -> None:
    """目标编码再解码后完全相等。"""
    goal = _goal()

    assert goal_from_dict(goal_to_dict(goal)) == goal


def test_progress_round_trip() -> None:
    """进度编码再解码后完全相等。"""
    progress = _progress()

    assert progress_from_dict(progress_to_dict(progress)) == progress


def test_diagnosis_round_trip() -> None:
    """诊断编码再解码后完全相等。"""
    diagnosis = _diagnosis()

    assert diagnosis_from_dict(diagnosis_to_dict(diagnosis)) == diagnosis


def test_exercise_round_trip() -> None:
    """题目编码再解码后完全相等。"""
    exercise = DiagnosticExercise(
        id="e1",
        prompt="闭包是什么？",
        point_id=KnowledgePointId("k2"),
        expected_answer="记住了创建时的作用域",
    )

    assert exercise_from_dict(exercise_to_dict(exercise)) == exercise


def test_derived_state_is_not_stored() -> None:
    """派生状态不落库——目标状态由迁移历史派生，负载里没有它。"""
    goal = _goal().activate(at=1000.0, reason="开始")

    payload = goal_to_dict(goal)

    assert "status" not in payload
    assert payload["transitions"][0]["target"] == "active"


def test_plan_version_round_trip() -> None:
    """计划版本编码再解码后完全相等（含计划内容、依据与差异）。"""
    version = _plan_version()

    assert plan_version_from_dict(plan_version_to_dict(version)) == version


# --- 解码拒绝 -----------------------------------------------------------


def test_unknown_schema_version_is_rejected() -> None:
    """不认识的版本明确拒绝，不半懂半猜。"""
    payload = goal_to_dict(_goal())
    payload["schema_version"] = SCHEMA_VERSION + 1

    with pytest.raises(DecodeError, match="schema_version"):
        goal_from_dict(payload)


def test_missing_version_is_rejected() -> None:
    """没有版本号的负载进不来。"""
    payload = goal_to_dict(_goal())
    del payload["schema_version"]

    with pytest.raises(DecodeError, match="schema_version"):
        goal_from_dict(payload)


def test_missing_field_is_rejected() -> None:
    """缺字段明确拒绝。"""
    payload = goal_to_dict(_goal())
    del payload["statement"]

    with pytest.raises(DecodeError, match="缺少字段"):
        goal_from_dict(payload)


def test_wrong_type_is_rejected() -> None:
    """类型不对明确拒绝。"""
    payload = goal_to_dict(_goal())
    payload["statement"] = 42

    with pytest.raises(DecodeError, match="必须是字符串"):
        goal_from_dict(payload)


def test_wrong_kind_is_rejected() -> None:
    """负载种类不对明确拒绝——别把目标当诊断读。"""
    payload = goal_to_dict(_goal())
    payload["kind"] = "diagnosis"

    with pytest.raises(DecodeError, match="负载种类"):
        goal_from_dict(payload)


# --- 不变量 -------------------------------------------------------------


def test_broken_history_is_rejected_on_decode() -> None:
    """负载里的迁移历史断了，领域层在**读回时**就拒绝。"""
    payload = goal_to_dict(_goal().activate(at=1000.0, reason="开始"))
    payload["transitions"].append(
        {"source": "draft", "target": "active", "reason": "伪造", "at": 2000.0},
    )

    with pytest.raises(DomainError, match="断链"):
        goal_from_dict(payload)


def test_broken_progress_history_is_rejected_on_decode() -> None:
    """进度里的迁移历史非法，同样在读回时被拒。"""
    payload = progress_to_dict(_progress())
    payload["points"][0]["transitions"].append(
        {
            "source": "not_started",
            "target": "in_progress",
            "source_kind": "learning",
            "reason": "伪造",
            "at": 3000.0,
        },
    )

    with pytest.raises(DomainError, match="断链"):
        progress_from_dict(payload)


def test_verdict_without_evidence_is_rejected_on_decode() -> None:
    """判定与依据不自洽，读回时被拒。"""
    payload = diagnosis_to_dict(_diagnosis())
    payload["verdicts"][0]["evidence"] = []

    with pytest.raises(DomainError, match="没有给出依据"):
        diagnosis_from_dict(payload)


# --- 乐观锁 -------------------------------------------------------------


def test_ensure_writable_accepts_new_record() -> None:
    """版本 0 表示新建，允许。"""
    ensure_writable(0, None)


def test_ensure_writable_accepts_matching_version() -> None:
    """版本对得上，允许。"""
    ensure_writable(3, 3)


def test_ensure_writable_rejects_stale_version() -> None:
    """版本对不上，拒绝。"""
    with pytest.raises(ConflictError, match="不一致"):
        ensure_writable(2, 3)


def test_ensure_writable_rejects_update_of_missing_record() -> None:
    """记录已经没了，不能基于旧版本写回去。"""
    with pytest.raises(ConflictError, match="记录不存在"):
        ensure_writable(2, None)


def test_save_then_load_round_trip() -> None:
    """存进去再取出来，是同一个对象。"""
    repository = InMemoryGoalRepository()
    goal = _goal()

    saved = asyncio.run(repository.save(Stored(value=goal, revision=0)))
    loaded = asyncio.run(repository.load(GoalId("goal-1")))

    assert saved.revision == 1
    assert loaded is not None
    assert loaded.value == goal


def test_stale_write_is_rejected() -> None:
    """基于旧版本写入会冲突——两个并发写不会静默互相覆盖。"""
    repository = InMemoryGoalRepository()
    goal = _goal()
    first = asyncio.run(repository.save(Stored(value=goal, revision=0)))

    updated = goal.activate(at=1000.0, reason="开始")
    asyncio.run(repository.save(Stored(value=updated, revision=first.revision)))

    with pytest.raises(ConflictError, match="不一致"):
        asyncio.run(repository.save(Stored(value=updated, revision=first.revision)))


def test_saving_over_existing_record_without_revision_is_rejected() -> None:
    """版本 0 只能用于新建，不能覆盖已有记录。"""
    repository = InMemoryGoalRepository()
    asyncio.run(repository.save(Stored(value=_goal(), revision=0)))

    with pytest.raises(ConflictError, match="不一致"):
        asyncio.run(repository.save(Stored(value=_goal(), revision=0)))


# --- 协议与列表 ---------------------------------------------------------


def test_in_memory_repositories_satisfy_the_protocols() -> None:
    """内存实现真的满足六个仓储协议。"""
    assert isinstance(InMemoryGoalRepository(), GoalRepository)
    assert isinstance(InMemoryProgressRepository(), ProgressRepository)
    assert isinstance(InMemoryDiagnosisRepository(), DiagnosisRepository)
    assert isinstance(InMemoryPlanVersionRepository(), PlanVersionRepository)
    assert isinstance(InMemoryCourseRepository(), CourseRepository)
    assert isinstance(InMemoryExerciseRepository(), ExerciseRepository)


def test_list_for_learner_filters_by_learner() -> None:
    """按学习者过滤目标。"""
    repository = InMemoryGoalRepository()
    mine = _goal()
    theirs = LearningGoal(
        id=GoalId("goal-2"),
        learner_id=LearnerId("learner-2"),
        course_id=CourseId("course-1"),
        statement="别的学习者的目标",
        outcomes=("能写代码",),
    )
    asyncio.run(repository.save(Stored(value=mine, revision=0)))
    asyncio.run(repository.save(Stored(value=theirs, revision=0)))

    found = asyncio.run(repository.list_for_learner(LearnerId("learner-1")))

    assert [record.value.id for record in found] == [GoalId("goal-1")]


def test_list_for_goal_filters_diagnoses() -> None:
    """按目标过滤诊断。"""
    repository = InMemoryDiagnosisRepository()
    asyncio.run(repository.save(Stored(value=_diagnosis(), revision=0)))

    found = asyncio.run(repository.list_for_goal(GoalId("goal-1")))

    assert [record.value.id for record in found] == [DiagnosisId("diagnosis-1")]


def test_list_for_point_filters_exercises() -> None:
    """按知识点过滤题目。"""
    repository = InMemoryExerciseRepository()
    mapped = DiagnosticExercise(id="e1", prompt="闭包？", point_id=KnowledgePointId("k2"))
    unmapped = DiagnosticExercise(id="e2", prompt="跑题？")
    asyncio.run(repository.save(Stored(value=mapped, revision=0)))
    asyncio.run(repository.save(Stored(value=unmapped, revision=0)))

    found = asyncio.run(repository.list_for_point(KnowledgePointId("k2")))

    assert [record.value.id for record in found] == ["e1"]


def test_list_for_plan_sorts_by_version_number() -> None:
    """计划线的版本按版本号升序。"""
    repository = InMemoryPlanVersionRepository()
    first = _plan_version()
    second = revise_plan(
        [first],
        _goal(),
        _course().structure,
        _progress(),
        version_id=PlanVersionId("plan-1-v2"),
        reason="学习者掌握了 k1",
        at=2000.0,
    )
    asyncio.run(repository.save(Stored(value=first, revision=0)))
    asyncio.run(repository.save(Stored(value=second, revision=0)))

    found = asyncio.run(repository.list_for_plan("plan-1"))

    assert [record.value.number for record in found] == [1, 2]


def test_loading_a_missing_record_returns_none() -> None:
    """没有就是没有，不返回一个空对象。"""
    repository = InMemoryGoalRepository()

    assert asyncio.run(repository.load(GoalId("goal-1"))) is None


def test_progress_identity_is_learner_and_course() -> None:
    """进度的身份是（学习者, 课程）这一对。"""
    repository = InMemoryProgressRepository()
    asyncio.run(repository.save(Stored(value=_progress(), revision=0)))

    found = asyncio.run(
        repository.load(LearnerId("learner-1"), CourseId("course-1")),
    )

    assert found is not None
    assert found.value == _progress()
