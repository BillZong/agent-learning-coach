"""诊断评估——把作答映射到知识点，并处理映射缺失的降级。

Topic 22 的工程问题是：**诊断结果如何映射到知识点？当题目与知识点的映射缺失
时，系统应该如何降级？**

三个答案：

1. **映射是「题目 → 知识点」，由题目自己声明。** ``DiagnosticExercise`` 带
   ``point_id``：一道题考哪个知识点，是出题时就定下的事实（Topic 21）。评估
   不去猜、不去推断——推断出的映射无法审计。

2. **映射缺失要能被表达，不能被绕过。** 题目**可能**没有知识点：外部题库导入
   的题、历史题、跨课程题。原来的模型里 ``point_id`` 是必填的，于是「没有
   映射」这件事**无法表达**——只能靠约定「所有题都必须绑定」把它藏起来。现在
   ``point_id`` 可空，映射缺失成为一等状态。

3. **降级是「不产出判定，但保留证据」。** 映射缺失的题目，其作答**不进入判定
   汇总**，但**必须留在证据里**并被报告出来。理由：静默丢弃会让「诊断覆盖了
   什么」不可回答；而凭空猜一个知识点会把判定建立在错误的地基上。

因此本模块提供：

- ``DiagnosticEvidence``：**证据**——一次作答 + 它的判定结果 + 归属的知识点。
  它是「判定」的依据，也是「诊断覆盖了什么」的凭据（Plan.md「保存诊断证据」）。
- ``Grader`` 协议：**怎么判对错**（答对 / 答错 / 无法判定）。判定是**外部
  判断**——精确匹配是一种，LLM 判官是另一种（Topic 39）。领域层只定义
  「需要判官给出什么」，不规定怎么判。
- ``assess_diagnosis``：把作答汇总成每个知识点的判定，并给出**降级报告**。

**判定与依据必须一致**：判定 k2 就不能拿 k1 的题当依据。这条不变量在
``DiagnosticVerdict`` 构造时校验——「判定说会，依据却指向别的点」是自相矛盾的
事实。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from agent_learning_coach.core.learning.diagnosis import (
    DiagnosticEvidence,
    DiagnosticExercise,
    DiagnosticSubmission,
    DiagnosticVerdict,
    Grading,
    Verdict,
)
from agent_learning_coach.core.learning.domain import DomainError, KnowledgePointId
from agent_learning_coach.core.text import normalize_for_match


class Grader(Protocol):
    """判官——判定一次作答对错的协议。

    判定是**外部判断**：精确匹配是一种实现，LLM 判官是另一种（Topic 39）。
    领域层定义「需要判官给出什么」，不规定怎么判——这样换判官不需要改领域模型。
    """

    def grade(
        self,
        exercise: DiagnosticExercise,
        submission: DiagnosticSubmission,
    ) -> Grading:
        """判定一次作答。

        Args:
            exercise: 题目。
            submission: 作答。

        Returns:
            判定结论。
        """
        ...


class ExactMatchGrader:
    """精确匹配判官——归一化后作答与参考答案完全相同才算对。

    **只适用于有唯一答案的题目**（选择题、填空题）。开放题没有唯一答案，精确
    匹配会把「意思对但表述不同」的作答判成错——那是**假缺口**，比不判更坏。

    因此它对没有参考答案的题目返回 ``undecided``，而不是 ``incorrect``。

    归一化规则复用 ``core.text.normalize_for_match``（折叠空白、忽略大小写与
    标点），与计划判重用同一条规则——两处各写一份必然漂移。
    """

    def grade(
        self,
        exercise: DiagnosticExercise,
        submission: DiagnosticSubmission,
    ) -> Grading:
        """判定一次作答。

        Args:
            exercise: 题目。
            submission: 作答。

        Returns:
            判定结论。
        """
        if not exercise.expected_answer:
            return "undecided"
        answer = normalize_for_match(submission.answer)
        expected = normalize_for_match(exercise.expected_answer)
        if not answer or not expected:
            return "undecided"
        return "correct" if answer == expected else "incorrect"


@dataclass(frozen=True)
class DegradationReport:
    """降级报告——**值对象**。

    回答「这次诊断有哪些地方降级了」。降级不抛异常：它是一次诊断的**正常结果**
    （题目可能来自外部题库），不是失败。调用方据此决定补题、忽略还是人工处理。
    """

    unmapped_evidence: tuple[DiagnosticEvidence, ...] = ()
    """映射缺失的证据——题目没有声明考点。"""

    undecided_evidence: tuple[DiagnosticEvidence, ...] = ()
    """判不了的证据——开放题之类没有唯一答案的作答。"""

    @property
    def has_degradation(self) -> bool:
        """是否发生了任何降级。"""
        return bool(self.unmapped_evidence or self.undecided_evidence)

    @property
    def affected_points(self) -> tuple[KnowledgePointId, ...]:
        """降级影响到的知识点。

        映射缺失的证据不指向任何知识点，因此**不在这里**——它们影响的是
        「这个知识点有没有被测到」，而不是某个具体知识点的结论。
        """
        points = {
            evidence.point_id
            for evidence in self.undecided_evidence
            if evidence.point_id is not None
        }
        return tuple(sorted(points, key=str))


@dataclass(frozen=True)
class AssessmentResult:
    """一次诊断评估的结果——**值对象**。

    判定与证据**并存**：判定是结论，证据是结论的依据与审计凭据。只留判定，
    「为什么这么判」就没了；只留证据，调用方还得自己汇总。
    """

    verdicts: tuple[DiagnosticVerdict, ...] = ()
    """各知识点的判定，按知识点声明顺序。"""

    evidence: tuple[DiagnosticEvidence, ...] = ()
    """全部证据，包括映射缺失与判不了的。"""

    degradation: DegradationReport = field(default_factory=DegradationReport)
    """降级报告。"""

    def verdict_for(self, point_id: KnowledgePointId) -> Verdict:
        """某知识点的判定结果。

        没有判定时返回 ``unknown``——没测过不等于不会。

        Args:
            point_id: 知识点标识。

        Returns:
            判定结果。
        """
        for verdict in self.verdicts:
            if verdict.point_id == point_id:
                return verdict.verdict
        return "unknown"


def assess_diagnosis(
    exercises: Sequence[DiagnosticExercise],
    submissions: Sequence[DiagnosticSubmission],
    grader: Grader,
) -> AssessmentResult:
    """把作答评估成每个知识点的判定。

    流程：配对题目与作答 → 判官判定 → 归属知识点 → 汇总成判定。

    汇总规则（**全部答对才算会**）：一个知识点的所有证据都答对，才判「会」；
    只要有一道答错就判「不会」；没有证据或只有「判不了」的证据，判「未判定」。

    为什么不是「有一道答对就算会」：诊断的目的是**找出要补的地方**，而不是
    给出尽量好的评价。宽松的汇总会把「侥幸答对一道」当成掌握，计划因此跳过
    真正没学会的知识点——而知识点之间有先修依赖（ADR-0016），跳过一处会让后面
    全部塌掉。严格汇总的代价是「多教一点」，那只是浪费一点时间。

    Args:
        exercises: 诊断题集合。
        submissions: 学习者的作答集合。
        grader: 判官。

    Returns:
        评估结果：判定、证据与降级报告。

    Raises:
        DomainError: 作答引用了不存在的题目。
    """
    by_id: dict[str, DiagnosticExercise] = {}
    for exercise in exercises:
        if exercise.id in by_id:
            raise DomainError(f"诊断题标识重复：{exercise.id}")
        by_id[exercise.id] = exercise

    evidence: list[DiagnosticEvidence] = []
    for submission in submissions:
        matched = by_id.get(submission.exercise_id)
        if matched is None:
            raise DomainError(f"作答引用了不存在的诊断题：{submission.exercise_id}")
        evidence.append(
            DiagnosticEvidence(
                exercise=matched,
                submission=submission,
                grading=grader.grade(matched, submission),
                point_id=matched.point_id,
            ),
        )

    verdicts = _aggregate(evidence)
    return AssessmentResult(
        verdicts=verdicts,
        evidence=tuple(evidence),
        degradation=_degradation(evidence),
    )


def _aggregate(evidence: Sequence[DiagnosticEvidence]) -> tuple[DiagnosticVerdict, ...]:
    """把证据汇总成每个知识点的判定。

    Args:
        evidence: 全部证据。

    Returns:
        判定元组，按知识点首次出现顺序。
    """
    grouped: dict[KnowledgePointId, list[DiagnosticEvidence]] = {}
    order: list[KnowledgePointId] = []
    for item in evidence:
        if item.point_id is None:
            continue
        if item.point_id not in grouped:
            grouped[item.point_id] = []
            order.append(item.point_id)
        grouped[item.point_id].append(item)

    verdicts: list[DiagnosticVerdict] = []
    for point_id in order:
        items = grouped[point_id]
        decided = [item for item in items if item.grading != "undecided"]
        if not decided:
            verdict: Verdict = "unknown"
            reason = "该知识点的作答都无法判定"
        elif all(item.grading == "correct" for item in decided):
            verdict = "known"
            reason = f"{len(decided)} 道题全部答对"
        else:
            verdict = "gap"
            wrong = sum(1 for item in decided if item.grading == "incorrect")
            reason = f"{len(decided)} 道题中有 {wrong} 道答错"
        verdicts.append(
            DiagnosticVerdict(
                point_id=point_id,
                verdict=verdict,
                evidence=tuple(items),
                reason=reason,
            ),
        )
    return tuple(verdicts)


def _degradation(evidence: Sequence[DiagnosticEvidence]) -> DegradationReport:
    """汇总降级情况。

    Args:
        evidence: 全部证据。

    Returns:
        降级报告。
    """
    unmapped = tuple(item for item in evidence if not item.is_mapped)
    undecided = tuple(item for item in evidence if item.grading == "undecided")
    return DegradationReport(
        unmapped_evidence=unmapped,
        undecided_evidence=undecided,
    )


__all__ = [
    "AssessmentResult",
    "DegradationReport",
    "DiagnosticEvidence",
    "Grader",
    "Grading",
    "assess_diagnosis",
]
