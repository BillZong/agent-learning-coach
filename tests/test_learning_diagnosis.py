"""诊断模型的测试（Topic 20）。

覆盖五组关注点：

**判定**——三态判定（会 / 不会 / 未判定）；「会」与「不会」必须有依据。

**归属**——诊断是一次活动，有独立标识；它不保存进度。

**准入**——诊断结果要能驱动计划生成，必须覆盖目标要求的每个知识点。

**边界**——「要教的内容」（缺口）与「不知道的内容」（未覆盖）必须分开。

**衔接**——判定经由 ``assess()`` 写成进度的迁移，来源标记为 ``assessed``。
"""

from __future__ import annotations

import pytest

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
    Grading,
    KnowledgePointId,
    LearnerId,
    LearningGoal,
    LearningProgress,
    Verdict,
    diagnosis_plan,
)

AT = 1_700_000_000.0
"""固定的迁移时刻，让测试不依赖真实时钟。"""


def _exercise(point_id: str = "k1", exercise_id: str = "e1") -> DiagnosticExercise:
    """构造一道诊断题。"""
    return DiagnosticExercise(
        id=exercise_id,
        prompt="闭包是什么？",
        point_id=KnowledgePointId(point_id) if point_id else None,
    )


def _submission(
    exercise_id: str = "e1", answer: str = "函数记住了创建时的作用域"
) -> DiagnosticSubmission:
    """构造一次作答。"""
    return DiagnosticSubmission(exercise_id=exercise_id, answer=answer)


def _evidence(
    point_id: str = "k1",
    grading: Grading = "correct",
    exercise_id: str = "e1",
) -> DiagnosticEvidence:
    """构造一条证据。"""
    exercise = _exercise(point_id, exercise_id)
    return DiagnosticEvidence(
        exercise=exercise,
        submission=_submission(exercise_id),
        grading=grading,
        point_id=exercise.point_id,
    )


def _verdict(
    point_id: str = "k1",
    verdict: Verdict = "known",
    **kwargs: object,
) -> DiagnosticVerdict:
    """构造一条判定。"""
    reason = kwargs.pop("reason", "测试判定")
    evidence = kwargs.pop("evidence", None)
    if evidence is None:
        evidence = (_evidence(point_id),) if verdict in ("known", "gap") else ()
    return DiagnosticVerdict(
        point_id=KnowledgePointId(point_id),
        verdict=verdict,
        reason=reason,
        evidence=evidence,  # type: ignore[arg-type]
    )


def _goal() -> LearningGoal:
    """构造一个要求掌握 k1 与 k2 的目标。"""
    return LearningGoal(
        id=GoalId("goal-closures"),
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-js-core"),
        statement="掌握闭包",
        outcomes=("能写出一个闭包例子",),
        requirements=(
            CapabilityRequirement(
                point_id=KnowledgePointId("k1"),
                outcome="能写出一个闭包例子",
            ),
            CapabilityRequirement(
                point_id=KnowledgePointId("k2"),
                outcome="能写出一个闭包例子",
            ),
        ),
    )


def _diagnosis(*verdicts: DiagnosticVerdict) -> Diagnosis:
    """构造一次诊断。"""
    return Diagnosis(
        id=DiagnosisId("diagnosis-1"),
        learner_id=LearnerId("learner-1"),
        goal_id=GoalId("goal-closures"),
        verdicts=verdicts,
    )


# --- 判定 -----------------------------------------------------------------


def test_verdict_requires_basis_for_known_and_gap() -> None:
    """「会」与「不会」必须有依据——没有依据的断言不可审计。"""
    with pytest.raises(DomainError, match="判为已掌握，但没有给出依据"):
        DiagnosticVerdict(
            point_id=KnowledgePointId("k1"),
            verdict="known",
            reason="凭感觉",
        )

    with pytest.raises(DomainError, match="判为有缺口，但没有给出依据"):
        DiagnosticVerdict(
            point_id=KnowledgePointId("k1"),
            verdict="gap",
            reason="凭感觉",
        )


def test_verdict_allows_unknown_without_evidence() -> None:
    """「未判定」不需要依据——它就是「没有证据」。"""
    verdict = DiagnosticVerdict(
        point_id=KnowledgePointId("k1"),
        verdict="unknown",
        reason="这次诊断没覆盖它",
    )

    assert verdict.verdict == "unknown"
    assert verdict.evidence == ()


def test_verdict_requires_reason() -> None:
    """「为什么这么判」要能回答。"""
    with pytest.raises(DomainError, match="诊断判定理由不能为空"):
        _verdict(reason=" ")


def test_verdict_evidence_must_match_the_judged_point() -> None:
    """判定与依据必须一致——判定 k2 不能拿 k1 的证据。"""
    with pytest.raises(DomainError, match="判定与依据不一致"):
        DiagnosticVerdict(
            point_id=KnowledgePointId("k2"),
            verdict="known",
            reason="答对",
            evidence=(_evidence("k1"),),
        )


def test_verdict_carries_multiple_evidence() -> None:
    """一个知识点出多道题时，每道题的作答都是一份依据。"""
    verdict = DiagnosticVerdict(
        point_id=KnowledgePointId("k1"),
        verdict="known",
        reason="两道题都答对",
        evidence=(_evidence("k1", "correct", "e1"), _evidence("k1", "correct", "e2")),
    )

    assert len(verdict.evidence) == 2
    assert verdict.label == "已掌握"


def test_exercise_requires_non_empty_prompt() -> None:
    """题面不能为空。"""
    with pytest.raises(DomainError, match="诊断题面不能为空"):
        DiagnosticExercise(id="e1", prompt=" ")


# --- 归属 -----------------------------------------------------------------


def test_diagnosis_is_an_activity_with_its_own_id() -> None:
    """诊断是一次活动：同一目标可诊断多次，每次是独立记录。"""
    first = _diagnosis(_verdict("k1"))
    second = Diagnosis(
        id=DiagnosisId("diagnosis-2"),
        learner_id=LearnerId("learner-1"),
        goal_id=GoalId("goal-closures"),
        verdicts=(_verdict("k1"),),
    )

    assert first.id != second.id
    assert first.goal_id == second.goal_id


def test_diagnosis_does_not_hold_progress() -> None:
    """诊断不保存进度——进度由 LearningProgress 持有，两份状态会不一致。"""
    diagnosis = _diagnosis(_verdict("k1"))

    assert not hasattr(diagnosis, "progress")
    assert not hasattr(diagnosis, "points")


def test_diagnosis_rejects_duplicate_verdicts() -> None:
    """同一个知识点只能有一条判定。"""
    with pytest.raises(DomainError, match="同一个知识点只能有一条诊断判定"):
        _diagnosis(_verdict("k1"), _verdict("k1", verdict="gap"))


# --- 准入 -----------------------------------------------------------------


def test_full_coverage_is_the_admission_condition() -> None:
    """目标要求的每个知识点都有判定，才够驱动计划生成。"""
    goal = _goal()

    partial = _diagnosis(_verdict("k1"))
    assert not partial.has_full_coverage(goal)
    assert partial.missing_points(goal) == (KnowledgePointId("k2"),)

    complete = _diagnosis(_verdict("k1"), _verdict("k2", verdict="gap"))
    assert complete.has_full_coverage(goal)
    assert complete.missing_points(goal) == ()


def test_unknown_verdict_does_not_count_as_coverage() -> None:
    """「未判定」不是覆盖——没测过不等于测过。"""
    goal = _goal()
    diagnosis = _diagnosis(_verdict("k1"), _verdict("k2", verdict="unknown"))

    assert not diagnosis.has_full_coverage(goal)
    assert diagnosis.missing_points(goal) == (KnowledgePointId("k2"),)


def test_diagnosis_plan_separates_teachable_from_undecided() -> None:
    """「要教的内容」与「不知道的内容」必须分开。"""
    goal = _goal()
    diagnosis = _diagnosis(_verdict("k1"), _verdict("k2", verdict="gap"))

    plan = diagnosis_plan(diagnosis, goal)

    assert plan.is_sufficient
    assert plan.covered == (KnowledgePointId("k1"), KnowledgePointId("k2"))
    assert plan.teachable == (KnowledgePointId("k2"),)
    assert plan.undecided == ()
    assert plan.coverage_ratio == 1.0


def test_diagnosis_plan_reports_insufficient_coverage() -> None:
    """覆盖不全时，计划不足以驱动生成。"""
    goal = _goal()
    diagnosis = _diagnosis(_verdict("k1"))

    plan = diagnosis_plan(diagnosis, goal)

    assert not plan.is_sufficient
    assert plan.undecided == (KnowledgePointId("k2"),)
    assert plan.coverage_ratio == 0.5


# --- 边界：配对 ------------------------------------------------------------


def test_diagnosis_plan_rejects_other_learner() -> None:
    """A 的诊断算不出 B 的目标的准入结论。"""
    goal = _goal()
    diagnosis = Diagnosis(
        id=DiagnosisId("diagnosis-other"),
        learner_id=LearnerId("learner-2"),
        goal_id=GoalId("goal-closures"),
    )

    with pytest.raises(DomainError, match="无法比较"):
        diagnosis_plan(diagnosis, goal)


def test_diagnosis_plan_rejects_other_goal() -> None:
    """诊断与目标必须配对。"""
    goal = _goal()
    diagnosis = Diagnosis(
        id=DiagnosisId("diagnosis-other"),
        learner_id=LearnerId("learner-1"),
        goal_id=GoalId("goal-other"),
    )

    with pytest.raises(DomainError, match="无法与目标"):
        diagnosis_plan(diagnosis, goal)


# --- 衔接：判定 → 进度 ----------------------------------------------------


def test_verdict_becomes_assessed_progress_transition() -> None:
    """判定「会」经由 assess() 写成进度的迁移，来源标记为 assessed。"""
    progress = LearningProgress(
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-js-core"),
    )
    updated = progress.assess(
        KnowledgePointId("k1"),
        at=AT,
        reason="诊断 diagnosis-1：答对",
    )

    assert updated.state_of(KnowledgePointId("k1")) == "learned"
    assert updated.points[0].transitions[0].source_kind == "assessed"


def test_gap_verdict_leaves_progress_untouched() -> None:
    """判定「不会」不写进度——它还是「未开始」，等真正学完再迁移。"""
    progress = LearningProgress(
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-js-core"),
    )

    assert progress.state_of(KnowledgePointId("k2")) == "not_started"
