"""诊断：判定学习者「已经会哪些知识点」。

Topic 20 的工程问题是：**诊断流程的输入、输出与准入条件是什么？诊断结果需要
达到何种精度，才足以驱动学习计划生成？**

三个答案：

1. **诊断是一次活动，不是一个状态。** ``Diagnosis`` 是**实体**，有独立的
   ``DiagnosisId``：同一个目标可以诊断多次（初诊、复诊），每次都是一条独立的
   记录。它与 ``LearningProgress`` 的分工是：诊断**产出判定**，进度**保存状态**。

2. **输入是「要诊断哪些知识点」，输出是「哪些会、哪些不会」。** 判定以
   ``DiagnosticVerdict``（值对象）表达，每个判定绑定一个知识点与一条证据
   （作答或说明）。**判定必须有证据**——没有证据的「会」是不可审计的断言。

3. **准入条件是领域知识，不是应用层的判断。** 诊断结果要能驱动计划生成，必须
   满足：**目标要求的每个知识点都有判定**（``has_full_coverage``）。少判一个点，
   计划就会漏掉它或凭空假设它。这是可以由领域层判定的规则，因此放在这里。

**诊断与进度通过「判定 → 迁移」衔接，不共享状态。** 诊断判定「k1 已会」，
由应用层调用 ``LearningProgress.assess()`` 写成 ``not_started → learned`` 的迁移
（ADR-0018）。诊断记录「为什么这么判」，进度记录「现在是什么状态」，两者不重复
存同一份事实。

**「会」不等于「掌握」。** 诊断给出的是**离散判定**（会 / 不会 / 未判定）。
掌握程度是连续量，属于掌握度模型（Topic 41）；薄弱点是相对概念（相对目标要求
还是绝对阈值），属于 Topic 43。本模块不做这两件事。

尚未覆盖：诊断题生成（Topic 21）、诊断结果到知识点的映射与降级（Topic 22）、
掌握度量化（Topic 41）、诊断题的难度分级。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agent_learning_coach.core.learning.domain import (
    DiagnosisId,
    DomainError,
    GoalId,
    KnowledgePointId,
    LearnerId,
    require_text,
)
from agent_learning_coach.core.learning.goal import LearningGoal

Verdict = Literal["known", "unknown", "gap"]
"""对单个知识点的诊断判定。

- ``known``：**会**。有证据支持。
- ``gap``：**不会**。有证据支持（答错、或者明确表示没学过）。
- ``unknown``：**未判定**。这次诊断没有覆盖这个点。

三态而不是二态，是因为「不会」与「没测」在计划生成上完全不同：前者是**要教
的内容**，后者是**不知道的内容**。把两者混为一谈会让计划把「没测过的点」当成
「不会的点」，从而重复教已经会的东西。
"""

_VERDICT_LABELS: dict[Verdict, str] = {
    "known": "已掌握",
    "gap": "有缺口",
    "unknown": "未判定",
}

EvidenceKind = Literal["exercise", "self_report", "external"]
"""判定依据的种类。

- ``exercise``：做题结果。
- ``self_report``：学习者自述（「我以前学过闭包」）。
- ``external``：外部给出（测评报告、面试评价等）。

区分依据的种类不是为了评判证据强弱（那是应用层的策略），而是为了**让「会」这个
断言可追溯**：自述与做题给出的结论，置信度显然不同。
"""


@dataclass(frozen=True)
class DiagnosticExercise:
    """一道诊断题——**值对象**。

    题目的**生成**属于 Topic 21（Structured Generation）；本模块只定义它作为
    诊断输入的形状。
    """

    point_id: KnowledgePointId
    """这道题考哪个知识点。"""

    prompt: str
    """题面。"""

    id: str
    """题目标识。"""

    def __post_init__(self) -> None:
        """校验题面非空。

        Raises:
            DomainError: 题面为空或含首尾空白；题目标识为空。
        """
        require_text("诊断题面", self.prompt)
        require_text("诊断题标识", self.id)


@dataclass(frozen=True)
class DiagnosticSubmission:
    """一次作答——**值对象**。

    作答是**事实**：学习者答了什么、答对没有。它不解释「会哪个知识点」——
    那是判定的职责。
    """

    exercise_id: str
    """对应的题目标识。"""

    answer: str
    """学习者的回答。"""

    correct: bool
    """是否答对。"""

    def __post_init__(self) -> None:
        """校验作答非空。

        Raises:
            DomainError: 题目标识为空。
        """
        require_text("作答的题目标识", self.exercise_id)


@dataclass(frozen=True)
class DiagnosticVerdict:
    """对单个知识点的诊断判定——**值对象**。

    「会 / 不会」这个**断言**必须能追溯到**依据**。因此判定带着证据，而不是一个
    孤零零的布尔值。
    """

    point_id: KnowledgePointId
    """被判定的知识点。"""

    verdict: Verdict
    """判定结果。"""

    reason: str
    """判定理由，必填——「为什么这么判」要能回答。"""

    basis: DiagnosticExercise | DiagnosticSubmission | None = None
    """判定依据：一道题、一次作答，或 ``None``（无依据的判断，例如结构性推断）。"""

    basis_kind: EvidenceKind | None = None
    """依据的种类。"""

    def __post_init__(self) -> None:
        """校验理由非空，且判定与依据自洽。

        Raises:
            DomainError: 理由为空；判定为「会」或「不会」却没有依据。
        """
        require_text("诊断判定理由", self.reason)
        if self.verdict in ("known", "gap") and self.basis is None:
            raise DomainError(
                f"知识点 {self.point_id} 判为{_VERDICT_LABELS[self.verdict]}，但没有给出依据",
            )
        if self.basis is not None and self.basis_kind is None:
            raise DomainError(f"知识点 {self.point_id} 的判定给了依据，但没有说明依据种类")

    @property
    def label(self) -> str:
        """判定的中文标签。"""
        return _VERDICT_LABELS[self.verdict]


@dataclass(frozen=True)
class Diagnosis:
    """一次诊断——**实体**。

    身份是 ``DiagnosisId``：同一个目标可以诊断多次，每次是独立的一条记录。
    它**不保存学习进度**——进度由 ``LearningProgress`` 持有，诊断只产出判定。
    两份状态就是两份事实，会不一致。

    诊断的**输入**是「要诊断哪些知识点」，由应用层用目标的知识点集合确定；本
    实体只持有「判定了什么」。
    """

    id: DiagnosisId
    """诊断标识。"""

    learner_id: LearnerId
    """被诊断的学习者。"""

    goal_id: GoalId
    """诊断针对的目标。"""

    verdicts: tuple[DiagnosticVerdict, ...] = ()
    """各知识点的判定，按声明顺序。"""

    def __post_init__(self) -> None:
        """校验判定不重复。

        Raises:
            DomainError: 同一个知识点出现多条判定。
        """
        point_ids = [verdict.point_id for verdict in self.verdicts]
        if len(set(point_ids)) != len(point_ids):
            raise DomainError("同一个知识点只能有一条诊断判定")

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

    def points_with(self, verdict: Verdict) -> tuple[KnowledgePointId, ...]:
        """判定为某结果的知识点，按声明顺序。

        Args:
            verdict: 判定结果。

        Returns:
            知识点标识元组。
        """
        return tuple(item.point_id for item in self.verdicts if item.verdict == verdict)

    def known_points(self) -> tuple[KnowledgePointId, ...]:
        """判定为「会」的知识点。"""
        return self.points_with("known")

    def gap_points(self) -> tuple[KnowledgePointId, ...]:
        """判定为「不会」的知识点。"""
        return self.points_with("gap")

    def undetermined_points(self) -> tuple[KnowledgePointId, ...]:
        """未判定的知识点——**目标要求里没被诊断覆盖的那些**。"""
        return self.points_with("unknown")

    def has_full_coverage(self, goal: LearningGoal) -> bool:
        """诊断结果是否足以驱动计划生成。

        准入条件：**目标要求的每个知识点都有判定**（会或不会）。少判一个点，
        计划就会漏掉它或凭空假设它——这是可以由领域层判定的规则，因此放在这里。

        「会」还是「不会」不影响准入：两者都会进入计划，区别只是「要不要教」。

        Args:
            goal: 诊断针对的目标。

        Returns:
            覆盖完整则为 ``True``。
        """
        return all(self.verdict_for(point_id) != "unknown" for point_id in goal.knowledge_point_ids)

    def missing_points(self, goal: LearningGoal) -> tuple[KnowledgePointId, ...]:
        """目标要求里**未被诊断覆盖**的知识点。

        这是「不知道要教什么」的部分，与 ``gap_points()``（「知道不会」）不同。

        Args:
            goal: 诊断针对的目标。

        Returns:
            未覆盖的知识点，按目标要求的顺序。
        """
        return tuple(
            point_id
            for point_id in goal.knowledge_point_ids
            if self.verdict_for(point_id) == "unknown"
        )


@dataclass(frozen=True)
class DiagnosisPlan:
    """诊断流程的**准入结论**——**值对象**。

    它回答「这次诊断够不够驱动计划生成」，并把**要教的内容**与**不知道的内容**
    分开列出。计划生成据此决定：缺口要教，未覆盖的要么补诊断、要么保守地当作
    要教。
    """

    goal_id: GoalId
    """诊断针对的目标。"""

    covered: tuple[KnowledgePointId, ...]
    """已判定的知识点，按目标要求的顺序。"""

    teachable: tuple[KnowledgePointId, ...]
    """判定为「不会」、需要教的知识点。"""

    undecided: tuple[KnowledgePointId, ...]
    """未判定的知识点。"""

    @property
    def is_sufficient(self) -> bool:
        """诊断结果是否足以驱动计划生成。"""
        return not self.undecided

    @property
    def coverage_ratio(self) -> float:
        """覆盖率；没有要求时为 ``0.0``。"""
        required = len(self.covered) + len(self.undecided)
        if required == 0:
            return 0.0
        return len(self.covered) / required


def diagnosis_plan(diagnosis: Diagnosis, goal: LearningGoal) -> DiagnosisPlan:
    """评估诊断结果能否驱动计划生成。

    这是**跨聚合查询**：需要同时看到诊断与目标，因此是模块级函数而不是方法
    （与 ADR-0018 的 ``goal_completion`` 同一形状）。

    Args:
        diagnosis: 诊断结果。
        goal: 诊断针对的目标。

    Returns:
        准入结论。

    Raises:
        DomainError: 诊断与目标不属于同一个学习者或目标。
    """
    if diagnosis.learner_id != goal.learner_id:
        raise DomainError(
            f"诊断属于学习者 {diagnosis.learner_id}，目标属于学习者 {goal.learner_id}，无法比较",
        )
    if diagnosis.goal_id != goal.id:
        raise DomainError(
            f"诊断针对目标 {diagnosis.goal_id}，无法与目标 {goal.id} 比较",
        )
    required = goal.knowledge_point_ids
    covered = tuple(
        point_id for point_id in required if diagnosis.verdict_for(point_id) != "unknown"
    )
    teachable = tuple(point_id for point_id in required if diagnosis.verdict_for(point_id) == "gap")
    undecided = tuple(
        point_id for point_id in required if diagnosis.verdict_for(point_id) == "unknown"
    )
    return DiagnosisPlan(
        goal_id=goal.id,
        covered=covered,
        teachable=teachable,
        undecided=undecided,
    )


__all__ = [
    "Diagnosis",
    "DiagnosisId",
    "DiagnosisPlan",
    "DiagnosticExercise",
    "DiagnosticSubmission",
    "DiagnosticVerdict",
    "EvidenceKind",
    "Verdict",
    "diagnosis_plan",
]
