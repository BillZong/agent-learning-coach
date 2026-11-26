"""应用层用例编排：诊断 → 进度 → 计划 → 落库。

跑法::

    uv run python examples/application_workflow.py

脚本演示五件事：

1. **编排**——诊断 → 进度 → 计划 → 落库，一次跑完。
2. **矛盾**——诊断说不会、进度说已学会：保留进度并报告。
3. **取舍**——多份诊断取最新的一份。
4. **幂等**——同一份诊断跑两次，第二次不改进度。
5. **写边界**——先写进度后写计划；乐观锁冲突会重读重算。

脚本不联网、不连数据库。
"""

from __future__ import annotations

import asyncio
from datetime import date

from agent_learning_coach.core.application import LearningWorkflow, sync_diagnosis
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
    InMemoryCourseRepository,
    InMemoryDiagnosisRepository,
    InMemoryGoalRepository,
    InMemoryPlanVersionRepository,
    InMemoryProgressRepository,
    Stored,
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
        ),
    )


def _goal() -> LearningGoal:
    """构造一个目标。"""
    return LearningGoal(
        id=GoalId("goal-1"),
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-1"),
        statement="学会用装饰器改造函数",
        outcomes=("能写出装饰器",),
        requirements=(
            CapabilityRequirement(point_id=KnowledgePointId("k1"), outcome="能写出装饰器"),
            CapabilityRequirement(point_id=KnowledgePointId("k2"), outcome="能写出装饰器"),
            CapabilityRequirement(point_id=KnowledgePointId("k3"), outcome="能写出装饰器"),
        ),
        daily_effort=DailyEffort(minutes_per_day=45),
        time_window=TimeWindow(start=date(2026, 11, 25), end=date(2026, 12, 25)),
    )


def _verdict(point_id: str, verdict: str, reason: str) -> DiagnosticVerdict:
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
                    answer="闭包记住了创建时的作用域",
                ),
                grading="correct" if verdict == "known" else "incorrect",
                point_id=KnowledgePointId(point_id),
            ),
        ),
    )


def _diagnosis(
    diagnosis_id: str,
    *,
    at: float,
    verdicts: tuple[DiagnosticVerdict, ...],
) -> Diagnosis:
    """构造一次诊断。"""
    return Diagnosis(
        id=DiagnosisId(diagnosis_id),
        learner_id=LearnerId("learner-1"),
        goal_id=GoalId("goal-1"),
        at=at,
        verdicts=verdicts,
    )


def _workflow(
    *,
    diagnoses: tuple[Diagnosis, ...] = (),
    progress: LearningProgress | None = None,
) -> LearningWorkflow:
    """构造一个编排器（内存仓储）。

    Args:
        diagnoses: 预置的诊断。
        progress: 预置的进度。

    Returns:
        编排器。
    """
    goals = InMemoryGoalRepository()
    courses = InMemoryCourseRepository()
    diagnosis_repo = InMemoryDiagnosisRepository()
    progresses = InMemoryProgressRepository()

    asyncio.run(goals.save(Stored(value=_goal(), revision=0)))
    asyncio.run(courses.save(Stored(value=_course(), revision=0)))
    for diagnosis in diagnoses:
        asyncio.run(diagnosis_repo.save(Stored(value=diagnosis, revision=0)))
    if progress is not None:
        asyncio.run(progresses.save(Stored(value=progress, revision=0)))

    return LearningWorkflow(
        goals=goals,
        progresses=progresses,
        diagnoses=diagnosis_repo,
        plans=InMemoryPlanVersionRepository(),
        courses=courses,
    )


def show_orchestration() -> None:
    """演示编排：诊断 → 进度 → 计划 → 落库。"""
    print("=== 1. 编排：诊断 → 进度 → 计划 → 落库 ===\n")
    diagnosis = _diagnosis(
        "diagnosis-1",
        at=1000.0,
        verdicts=(
            _verdict("k1", "known", "答对作用域"),
            _verdict("k2", "gap", "答错捕获"),
        ),
    )
    workflow = _workflow(diagnoses=(diagnosis,))

    result = asyncio.run(
        workflow.diagnose_and_plan(GoalId("goal-1"), at=2000.0, reason="初次诊断后规划"),
    )

    print(f"诊断：{result.diagnosis_id}")
    print(f"同步进进度的点：{[str(item) for item in result.sync.applied]}")
    print(f"矛盾：{len(result.sync.conflicts)} 条")
    print(f"进度写了：{result.progress_written}")
    print(f"计划版本：{result.plan_version.value.plan_id} v{result.plan_version.value.number}")
    print(f"计划里的单元：{[str(unit.point_id) for unit in result.plan_version.value.plan.units]}")
    print("（k1 诊断判定已掌握 → 同步进进度 → 不进计划；k2 是缺口 → 进计划）")
    print("（领域层只回答「什么是真的」；顺序、写几次、冲突怎么办，是应用层的事）\n")


def show_conflict() -> None:
    """演示矛盾：诊断说不会、进度说已学会。"""
    print("=== 2. 矛盾：诊断说不会、进度说已学会 ===\n")
    progress = LearningProgress(
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-1"),
    )
    progress = progress.start(KnowledgePointId("k2"), at=400.0, reason="开始学")
    progress = progress.complete(KnowledgePointId("k2"), at=500.0, reason="学会了")

    diagnosis = _diagnosis(
        "diagnosis-1",
        at=1000.0,
        verdicts=(_verdict("k2", "gap", "答错捕获"),),
    )
    synced, report = sync_diagnosis(diagnosis, progress, at=1000.0)

    print(f"同步后 k2 的状态：{synced.state_of(KnowledgePointId('k2'))}")
    for conflict in report.conflicts:
        print(f"矛盾：{conflict.point_id} 诊断说{conflict.verdict}，进度说{conflict.state}")
        print(f"处置：{conflict.resolution}")
    print("（不是「信诊断」——领域模型不允许把已学会退回未学会（ADR-0018））")
    print("（因此应用层能做的只有：保留进度 + 报告矛盾。要不要复习是另一个用例的事）\n")


def show_pick_latest() -> None:
    """演示多份诊断的取舍。"""
    print("=== 3. 取舍：多份诊断取最新的一份 ===\n")
    workflow = _workflow(
        diagnoses=(
            _diagnosis("diagnosis-old", at=1000.0, verdicts=(_verdict("k1", "gap", "旧诊断"),)),
            _diagnosis(
                "diagnosis-new",
                at=5000.0,
                verdicts=(_verdict("k1", "known", "新诊断：答对了"),),
            ),
        ),
    )

    result = asyncio.run(
        workflow.diagnose_and_plan(GoalId("goal-1"), at=6000.0, reason="规划"),
    )

    print(f"用到的诊断：{result.diagnosis_id}（最新的那份）")
    print(f"同步进进度的点：{[str(item) for item in result.sync.applied]}")
    print("（诊断是**一次活动**，活动有时间——所以要给它加 at 字段，否则无从排序）")
    print("（给了 diagnosis_id 就用它，不取最新的——调用方说了算）\n")


def show_idempotency() -> None:
    """演示幂等：同一份诊断跑两次，什么都不写。"""
    print("=== 4. 幂等：同一份诊断跑两次 ===\n")
    diagnosis = _diagnosis(
        "diagnosis-1",
        at=1000.0,
        verdicts=(_verdict("k1", "known", "答对作用域"),),
    )
    workflow = _workflow(diagnoses=(diagnosis,))

    first = asyncio.run(
        workflow.diagnose_and_plan(GoalId("goal-1"), at=2000.0, reason="初次规划"),
    )
    second = asyncio.run(
        workflow.diagnose_and_plan(GoalId("goal-1"), at=3000.0, reason="再规划一次"),
    )

    print(f"第一次：写了进度 {first.progress_written}，出了计划 v{first.plan_version.value.number}")
    print(
        f"第二次：写了进度 {second.progress_written}，出了计划 v{second.plan_version.value.number}"
    )
    print("（进度没变 → 不写；计划没变 → **不产生新版本**）")
    print("（空版本是噪音：它不回答「计划变了没有」，只在历史里堆出一串内容相同的记录）")
    print("（换了诊断即使结果一样也会出版本——依据变了，那是有信息量的）")
    print("（同一目标已有计划线就**修订**它，不重开一条——否则「这份计划从哪来」说不清）\n")


def main() -> None:
    """跑一遍应用层编排演示。"""
    show_orchestration()
    show_conflict()
    show_pick_latest()
    show_idempotency()


if __name__ == "__main__":
    main()
