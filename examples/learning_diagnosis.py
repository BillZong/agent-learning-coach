"""诊断：判定学习者「已经会哪些知识点」，以及准入条件。

跑法::

    uv run python examples/learning_diagnosis.py

脚本演示五件事：

1. **输入**——诊断针对哪个目标，要判定哪些知识点。
2. **判定**——三态判定：会 / 不会 / 未判定；「会」与「不会」必须有依据。
3. **准入**——诊断结果要能驱动计划生成，必须覆盖目标要求的每个知识点。
4. **分清**——「要教的内容」（缺口）与「不知道的内容」（未覆盖）不是一回事。
5. **衔接**——判定经由 ``assess()`` 写成进度的迁移，来源标记为 ``assessed``。

脚本不联网。
"""

from __future__ import annotations

from agent_learning_coach.core.learning import (
    CapabilityRequirement,
    CourseId,
    Diagnosis,
    DiagnosisId,
    DiagnosticEvidence,
    DiagnosticExercise,
    DiagnosticSubmission,
    DiagnosticVerdict,
    DomainError,
    GoalId,
    KnowledgePointId,
    LearnerId,
    LearningGoal,
    LearningProgress,
    diagnosis_plan,
)

AT = 1_767_225_600.0
"""固定的迁移时刻，让示例输出可复现。"""


def _goal() -> LearningGoal:
    """构造一个要求掌握 k1 与 k2 的目标。"""
    return LearningGoal(
        id=GoalId("goal-closures"),
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-js-core"),
        statement="掌握闭包",
        outcomes=("能说清闭包是什么", "能写出一个闭包例子"),
        requirements=(
            CapabilityRequirement(
                point_id=KnowledgePointId("k1"),
                outcome="能说清闭包是什么",
            ),
            CapabilityRequirement(
                point_id=KnowledgePointId("k2"),
                outcome="能写出一个闭包例子",
            ),
        ),
    )


def _exercise(point_id: str, exercise_id: str) -> DiagnosticExercise:
    """构造一道诊断题。"""
    return DiagnosticExercise(
        id=exercise_id,
        prompt="闭包是什么？",
        point_id=KnowledgePointId(point_id),
    )


def _submission(exercise_id: str, answer: str) -> DiagnosticSubmission:
    """构造一次作答。"""
    return DiagnosticSubmission(exercise_id=exercise_id, answer=answer)


def _evidence(point_id: str, exercise_id: str, grading: str) -> DiagnosticEvidence:
    """构造一条证据。"""
    exercise = _exercise(point_id, exercise_id)
    return DiagnosticEvidence(
        exercise=exercise,
        submission=_submission(exercise_id, "函数记住了创建时的作用域"),
        grading=grading,  # type: ignore[arg-type]
        point_id=exercise.point_id,
    )


def show_judgement() -> None:
    """演示判定：必须有依据。"""
    print("=== 1. 判定：会 / 不会 / 未判定 ===\n")
    verdict = DiagnosticVerdict(
        point_id=KnowledgePointId("k1"),
        verdict="known",
        reason="答对作用域与捕获的定义",
        evidence=(_evidence("k1", "e1", "correct"),),
    )
    print(f"知识点 k1：{verdict.label}（{verdict.reason}）")
    print(f"依据条数：{len(verdict.evidence)}")

    try:
        DiagnosticVerdict(
            point_id=KnowledgePointId("k2"),
            verdict="known",
            reason="凭感觉",
        )
    except DomainError as exc:
        print(f"[没有依据的判定] -> {exc}")

    try:
        DiagnosticVerdict(
            point_id=KnowledgePointId("k2"),
            verdict="known",
            reason="拿错了依据",
            evidence=(_evidence("k1", "e1", "correct"),),
        )
    except DomainError as exc:
        print(f"[判定与依据不一致] -> {exc}")
    print()


def show_admission() -> None:
    """演示准入条件：覆盖完整才够驱动计划生成。"""
    print("=== 2. 准入：覆盖完整才够驱动计划生成 ===\n")
    goal = _goal()
    partial = Diagnosis(
        id=DiagnosisId("diagnosis-partial"),
        learner_id=LearnerId("learner-1"),
        goal_id=GoalId("goal-closures"),
        verdicts=(
            DiagnosticVerdict(
                point_id=KnowledgePointId("k1"),
                verdict="known",
                reason="答对",
                evidence=(_evidence("k1", "e1", "correct"),),
            ),
        ),
    )
    plan = diagnosis_plan(partial, goal)
    print(f"只判定了 k1：覆盖 {len(plan.covered)}/{len(goal.knowledge_point_ids)}")
    print(f"够驱动计划生成吗：{plan.is_sufficient}")
    print(f"未覆盖：{[str(item) for item in plan.undecided]}")
    print()


def show_separation() -> None:
    """演示「要教的内容」与「不知道的内容」的区别。"""
    print("=== 3. 分清：缺口 ≠ 未覆盖 ===\n")
    goal = _goal()
    diagnosis = Diagnosis(
        id=DiagnosisId("diagnosis-full"),
        learner_id=LearnerId("learner-1"),
        goal_id=GoalId("goal-closures"),
        verdicts=(
            DiagnosticVerdict(
                point_id=KnowledgePointId("k1"),
                verdict="known",
                reason="答对",
                evidence=(_evidence("k1", "e1", "correct"),),
            ),
            DiagnosticVerdict(
                point_id=KnowledgePointId("k2"),
                verdict="gap",
                reason="答错，只会背定义",
                evidence=(_evidence("k2", "e2", "incorrect"),),
            ),
        ),
    )
    plan = diagnosis_plan(diagnosis, goal)
    print(f"覆盖完整：{plan.is_sufficient}（覆盖率 {plan.coverage_ratio:.0%}）")
    print(f"要教的内容（判定为不会）：{[str(item) for item in plan.teachable]}")
    print(f"不知道的内容（未判定）：{[str(item) for item in plan.undecided]}")
    print("（前者进计划，后者要么补诊断、要么保守地当作要教）\n")


def show_bridge() -> None:
    """演示判定如何写进进度。"""
    print("=== 4. 衔接：判定 → 进度迁移 ===\n")
    progress = LearningProgress(
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-js-core"),
    )
    updated = progress.assess(
        KnowledgePointId("k1"),
        at=AT,
        reason="诊断 diagnosis-full：答对",
    )
    transition = updated.points[0].transitions[0]
    print(f"知识点 k1 的进度状态：{updated.state_of(KnowledgePointId('k1'))}")
    print(f"迁移来源：{transition.source_kind}（assessed = 诊断判定已有基础）")
    print(f"理由：{transition.reason}")
    print("（诊断记录「为什么这么判」，进度记录「现在是什么状态」，两份事实不重复）\n")


def main() -> None:
    """跑一遍诊断演示。"""
    show_judgement()
    show_admission()
    show_separation()
    show_bridge()

    print("=== 边界提醒 ===")
    print("诊断判定的是「会 / 不会」，不是掌握程度；")
    print("薄弱点是相对概念（相对目标要求还是绝对阈值），属于 Topic 43。")


if __name__ == "__main__":
    main()
