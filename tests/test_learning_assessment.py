"""诊断评估的测试（Topic 22）。

覆盖五组关注点：

**映射**——题目声明考点，评估据此归属；一个知识点的多道题聚合成一条判定。

**汇总**——全部答对才算会；有一道答错就是缺口；判不了不算已判定。

**降级**——映射缺失的作答不进判定，但**保留为证据**并被报告。

**判官**——判定是外部判断：协议可替换；无参考答案时判「判不了」。

**边界**——判定与依据必须一致；作答引用不存在的题目是错误。
"""

from __future__ import annotations

import pytest

from agent_learning_coach.core.learning import (
    AssessmentResult,
    DegradationReport,
    DiagnosticEvidence,
    DiagnosticExercise,
    DiagnosticSubmission,
    DiagnosticVerdict,
    DomainError,
    ExactMatchGrader,
    Grading,
    KnowledgePointId,
    assess_diagnosis,
)


def _exercise(
    point_id: str | None = "k1",
    exercise_id: str = "e1",
    expected_answer: str = "闭包记住了创建时的作用域",
) -> DiagnosticExercise:
    """构造一道诊断题。

    Args:
        point_id: 考哪个知识点；``None`` 表示映射缺失。
        exercise_id: 题目标识。
        expected_answer: 参考答案；空表示开放题。

    Returns:
        诊断题。
    """
    return DiagnosticExercise(
        id=exercise_id,
        prompt="闭包与普通函数在返回后访问变量时有什么不同？",
        point_id=KnowledgePointId(point_id) if point_id else None,
        expected_answer=expected_answer,
    )


def _submission(
    exercise_id: str = "e1",
    answer: str = "闭包记住了创建时的作用域",
) -> DiagnosticSubmission:
    """构造一次作答。"""
    return DiagnosticSubmission(exercise_id=exercise_id, answer=answer)


class _StubGrader:
    """按题目 id 返回预设结论的判官。"""

    def __init__(self, **by_exercise: Grading) -> None:
        """初始化判官。

        Args:
            by_exercise: 题目标识 → 判定结论。
        """
        self.by_exercise = by_exercise

    def grade(
        self,
        exercise: DiagnosticExercise,
        submission: DiagnosticSubmission,
    ) -> Grading:
        """返回预设结论。

        Args:
            exercise: 题目。
            submission: 作答。

        Returns:
            判定结论。
        """
        return self.by_exercise.get(exercise.id, "correct")


# --- 映射 -----------------------------------------------------------------


def test_verdict_is_derived_from_mapped_exercises() -> None:
    """题目声明考点，评估据此归属判定。"""
    exercises = (_exercise("k1", "e1"), _exercise("k2", "e2"))
    submissions = (_submission("e1"), _submission("e2"))

    result = assess_diagnosis(exercises, submissions, _StubGrader(e1="correct", e2="incorrect"))

    assert result.verdict_for(KnowledgePointId("k1")) == "known"
    assert result.verdict_for(KnowledgePointId("k2")) == "gap"
    assert len(result.verdicts) == 2


def test_multiple_exercises_aggregate_into_one_verdict() -> None:
    """一个知识点的多道题聚合成一条判定，依据全部保留。"""
    exercises = (_exercise("k1", "e1"), _exercise("k1", "e2"))
    submissions = (_submission("e1"), _submission("e2"))

    result = assess_diagnosis(exercises, submissions, _StubGrader(e1="correct", e2="correct"))

    assert len(result.verdicts) == 1
    verdict = result.verdicts[0]
    assert verdict.verdict == "known"
    assert len(verdict.evidence) == 2
    assert "2 道题全部答对" in verdict.reason


def test_all_correct_is_known() -> None:
    """全部答对才算会。"""
    exercises = (_exercise("k1", "e1"), _exercise("k1", "e2"))
    submissions = (_submission("e1"), _submission("e2"))

    result = assess_diagnosis(exercises, submissions, _StubGrader(e1="correct", e2="correct"))

    assert result.verdict_for(KnowledgePointId("k1")) == "known"


def test_one_wrong_is_a_gap() -> None:
    """有一道答错就是缺口——诊断的目的是找出要补的地方。"""
    exercises = (_exercise("k1", "e1"), _exercise("k1", "e2"))
    submissions = (_submission("e1"), _submission("e2"))

    result = assess_diagnosis(exercises, submissions, _StubGrader(e1="correct", e2="incorrect"))

    verdict = result.verdicts[0]
    assert verdict.verdict == "gap"
    assert "1 道答错" in verdict.reason


def test_undecided_evidence_is_not_a_verdict() -> None:
    """判不了的作答不构成判定——该知识点仍是「未判定」。"""
    exercises = (_exercise("k1", "e1"),)
    submissions = (_submission("e1"),)

    result = assess_diagnosis(exercises, submissions, _StubGrader(e1="undecided"))

    assert result.verdicts[0].verdict == "unknown"
    assert result.verdict_for(KnowledgePointId("k1")) == "unknown"


# --- 降级 -----------------------------------------------------------------


def test_unmapped_exercise_produces_no_verdict() -> None:
    """映射缺失的作答不进入判定汇总。"""
    exercises = (_exercise(None, "e1"), _exercise("k1", "e2"))
    submissions = (_submission("e1"), _submission("e2"))

    result = assess_diagnosis(exercises, submissions, _StubGrader(e1="correct", e2="correct"))

    assert len(result.verdicts) == 1
    assert result.verdicts[0].point_id == KnowledgePointId("k1")


def test_unmapped_evidence_is_kept_and_reported() -> None:
    """映射缺失的作答**保留为证据**并被报告——不静默丢弃。"""
    exercises = (_exercise(None, "e1"),)
    submissions = (_submission("e1"),)

    result = assess_diagnosis(exercises, submissions, _StubGrader(e1="correct"))

    assert len(result.evidence) == 1
    assert not result.evidence[0].is_mapped
    assert result.degradation.has_degradation
    assert len(result.degradation.unmapped_evidence) == 1


def test_degradation_report_separates_unmapped_from_undecided() -> None:
    """两类降级要分开：映射缺失 vs 判不了。"""
    exercises = (_exercise(None, "e1"), _exercise("k1", "e2"))
    submissions = (_submission("e1"), _submission("e2"))

    result = assess_diagnosis(exercises, submissions, _StubGrader(e1="correct", e2="undecided"))

    report = result.degradation
    assert len(report.unmapped_evidence) == 1
    assert len(report.undecided_evidence) == 1
    assert report.affected_points == (KnowledgePointId("k1"),)


def test_no_degradation_when_everything_mapped_and_decided() -> None:
    """一切正常时没有降级。"""
    exercises = (_exercise("k1", "e1"),)
    submissions = (_submission("e1"),)

    result = assess_diagnosis(exercises, submissions, _StubGrader(e1="correct"))

    assert not result.degradation.has_degradation
    assert result.degradation == DegradationReport()


def test_unmapped_evidence_does_not_affect_verdicts() -> None:
    """降级不改变已判定知识点的结论。"""
    exercises = (_exercise(None, "e1"), _exercise("k1", "e2"))
    submissions = (_submission("e1"), _submission("e2"))

    result = assess_diagnosis(exercises, submissions, _StubGrader(e1="correct", e2="correct"))

    assert result.verdict_for(KnowledgePointId("k1")) == "known"
    assert result.degradation.has_degradation


# --- 判官 -----------------------------------------------------------------


def test_exact_match_grader_accepts_normalized_match() -> None:
    """归一化后相同即算对——空白、大小写、标点差异不影响判定。"""
    exercise = _exercise()
    submission = _submission(answer="闭包记住了 创建时的作用域。")

    assert ExactMatchGrader().grade(exercise, submission) == "correct"


def test_exact_match_grader_rejects_wrong_answer() -> None:
    """归一化后不同即算错。"""
    exercise = _exercise()
    submission = _submission(answer="闭包就是普通的函数")

    assert ExactMatchGrader().grade(exercise, submission) == "incorrect"


def test_exact_match_grader_cannot_decide_open_question() -> None:
    """没有参考答案的开放题判不了——不算答错，避免制造假缺口。"""
    exercise = _exercise(expected_answer="")
    submission = _submission(answer="闭包保留了创建时的作用域")

    assert ExactMatchGrader().grade(exercise, submission) == "undecided"


# --- 边界 -----------------------------------------------------------------


def test_submission_referencing_unknown_exercise_is_rejected() -> None:
    """作答引用不存在的题目是错误——不是降级，是数据不一致。"""
    exercises = (_exercise("k1", "e1"),)
    submissions = (_submission("e9"),)

    with pytest.raises(DomainError, match="作答引用了不存在的诊断题"):
        assess_diagnosis(exercises, submissions, _StubGrader())


def test_duplicate_exercise_ids_are_rejected() -> None:
    """题目标识重复会让「作答 → 题目」的配对变得含糊。"""
    exercises = (_exercise("k1", "e1"), _exercise("k1", "e1"))

    with pytest.raises(DomainError, match="诊断题标识重复"):
        assess_diagnosis(exercises, (), _StubGrader())


def test_verdict_evidence_must_point_to_the_judged_point() -> None:
    """判定与依据必须一致。"""
    with pytest.raises(DomainError, match="判定与依据不一致"):
        DiagnosticVerdict(
            point_id=KnowledgePointId("k2"),
            verdict="known",
            reason="答对",
            evidence=(
                DiagnosticEvidence(
                    exercise=_exercise("k1"),
                    submission=_submission(),
                    grading="correct",
                    point_id=KnowledgePointId("k1"),
                ),
            ),
        )


def test_assessment_result_reports_verdict_for_unmeasured_point() -> None:
    """没测过的知识点返回「未判定」——没测过不等于不会。"""
    result = assess_diagnosis((), (), _StubGrader())

    assert result.verdict_for(KnowledgePointId("k9")) == "unknown"
    assert isinstance(result, AssessmentResult)


def test_exercise_without_point_id_is_allowed() -> None:
    """题目可以不声明考点——映射缺失必须能被表达。"""
    exercise = _exercise(None)

    assert exercise.point_id is None
    assert exercise.prompt != ""
