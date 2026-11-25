"""计划版本：同一计划的修订与全新计划怎么区分，计划怎么查。

Topic 23 的 ``LearningPlan`` 是**派生的值对象**——给定目标、知识结构与进度就
能算出来。它因此没有身份，也没有历史：算两次得到两个相等的对象，但「这份计划
从第一次到现在改过什么」无处可查。

本模块补上版本层，回答两个问题：

1. **「同一计划的修订」与「全新计划」怎么表达？**
   - **修订**：同一 ``PlanId`` 下，版本号 +1。它是同一份计划的演进。
   - **全新计划**：新的 ``PlanId``，版本号从 1 开始。换了学习路径就是新的一条，
     而不是把旧计划接着改。

   少了 ``PlanId`` 这层区分，「重新规划一次」既可以写成新版本、也可以写成新计划，
   两种解释都能自圆其说——**无法判定的区分等于没有区分**。它与 ``DiagnosisId``
   是同一条线索：活动有独立身份。

2. **查询怎么排序、怎么过滤？**
   - **同一计划线内按版本号排序**，不按时间排。版本号是领域分配的序号；时间
     由调用方传入，可能补录、乱序，让它决定顺序会把「谁是当前版本」变成猜。
   - **跨计划线**（同一个目标下有几条线）按**首个版本的时间**排，最新的那条线
     是当前线——跨线没有共同序号，只能用时间。
   - 过滤维度：目标、计划线。

每个版本都带三样东西，让「为什么变成现在这样」有答案：

- **依据**（``PlanBasis``）：这一版是基于什么生成的——目标、当时的进度、参考的
  诊断、课程结构的指纹。
- **差异**（``PlanDelta``）：与上一版相比，新增、移除、重排了哪些知识点。
- **理由**（``reason``）：为什么改。

领域层仍然不认识存储与框架：版本集合由调用方持有，查询是纯函数。仓储层按
ADR-0019 留待后续。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

from agent_learning_coach.core.learning.diagnosis import Diagnosis
from agent_learning_coach.core.learning.domain import (
    CourseId,
    DiagnosisId,
    DomainError,
    GoalId,
    KnowledgePointId,
    KnowledgeStructure,
    LearnerId,
    PlanId,
    PlanVersionId,
    require_text,
)
from agent_learning_coach.core.learning.goal import LearningGoal
from agent_learning_coach.core.learning.planning import (
    EffortEstimator,
    LearningPlan,
    LearningUnit,
    generate_plan,
)
from agent_learning_coach.core.learning.progress import LearningProgress


@dataclass(frozen=True)
class PlanBasis:
    """计划生成依据——**值对象**。

    回答「这一版是基于什么生成的」。它记录的是**输入**，不是输出：输出（计划
    内容）在 ``LearningPlan`` 里。

    记录进度快照（已学会哪些点）尤其重要：同一个目标在不同时刻生成的计划是
    不同的版本，它们的区别正是「那时已经会了什么」。
    """

    goal_id: GoalId
    """目标标识。"""

    learner_id: LearnerId
    """学习者标识。"""

    course_id: CourseId
    """课程标识。"""

    required_points: tuple[KnowledgePointId, ...] = ()
    """目标要求掌握的知识点，按声明顺序。"""

    learned_points: tuple[KnowledgePointId, ...] = ()
    """生成这一版时**已经学会**的知识点，按声明顺序。"""

    diagnosis_id: DiagnosisId | None = None
    """参考的诊断；没有参考诊断时为空。"""

    structure_fingerprint: str = ""
    """课程知识结构的指纹。

    结构变了（加点、改依赖）之后，旧版本是否还成立就能看出来——否则只能靠猜。
    """


@dataclass(frozen=True)
class PlanDelta:
    """两个版本之间的差异——**值对象**。

    只比三件事：**有哪些知识点**、**排在第几天**、**估时多少**。计划的内容文字
    （标题、理由）不在差异里——它们是解释，不是排程。
    """

    added: tuple[KnowledgePointId, ...] = ()
    """新出现的知识点。"""

    removed: tuple[KnowledgePointId, ...] = ()
    """不再出现的知识点——通常是学习者已经学会并从计划里移出的。"""

    changed: tuple[KnowledgePointId, ...] = ()
    """还在，但排期或估时变了。"""

    @property
    def is_empty(self) -> bool:
        """两次生成的结果是否完全一样。"""
        return not (self.added or self.removed or self.changed)


@dataclass(frozen=True)
class PlanVersion:
    """计划版本——**实体**。

    它是一次**修订的记录**：内容快照 + 生成依据 + 与上一版的差异 + 改动理由。

    版本号（``number``）在计划线内从 1 开始递增，是排序依据；标识只用于引用。
    """

    id: PlanVersionId
    """版本标识。"""

    plan_id: PlanId
    """所属计划线。"""

    number: int
    """版本号，从 1 开始。"""

    plan: LearningPlan
    """这一版的计划内容快照。"""

    basis: PlanBasis
    """生成这一版的依据。"""

    reason: str
    """为什么产生这一版，必填——没有理由的版本变化无法解释。"""

    at: float
    """生成时刻（epoch 秒），由调用方提供——领域层不读时钟。"""

    delta: PlanDelta | None = None
    """与上一版的差异；**首个版本没有差异**（``None``）。"""

    def __post_init__(self) -> None:
        """校验版本内容。

        Raises:
            DomainError: 版本号不是正整数；理由为空；首个版本却带差异。
        """
        if self.number < 1:
            raise DomainError(f"计划版本号必须是从 1 开始的正整数：{self.number}")
        require_text("计划版本的改动理由", self.reason)
        if self.number == 1 and self.delta is not None:
            raise DomainError("首个计划版本没有上一版，因此不能带差异")


def fingerprint_structure(structure: KnowledgeStructure) -> str:
    """课程知识结构的指纹。

    指纹由**知识点与关系**的规范渲染算出：同样的结构总是同一个指纹，结构变了
    指纹就变。它不是内容哈希的通用工具，而是「这一版计划基于哪一份结构」的
    凭据。

    Args:
        structure: 知识结构。

    Returns:
        12 位十六进制指纹。
    """
    parts = [f"{point.id}|{point.title}" for point in structure.points]
    parts.extend(
        f"{relation.kind}:{relation.source}>{relation.target}" for relation in structure.relations
    )
    payload = "\n".join(parts).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


def start_plan(
    goal: LearningGoal,
    structure: KnowledgeStructure,
    progress: LearningProgress,
    *,
    plan_id: PlanId,
    version_id: PlanVersionId,
    reason: str,
    at: float,
    diagnosis: Diagnosis | None = None,
    estimator: EffortEstimator | None = None,
) -> PlanVersion:
    """开一份**全新计划**——版本 1。

    「全新计划」与「修订」的差别只在这里：**新计划线**（新 ``plan_id``），版本号
    从 1 开始。同一目标重新规划一次，如果换了学习路径，就该开新计划线，而不是
    把旧计划接着改——否则「这份计划从哪来」永远说不清。

    Args:
        goal: 学习目标。
        structure: 知识结构。
        progress: 学习进度。
        plan_id: 计划线标识。
        version_id: 版本标识。
        reason: 开这份计划的理由。
        at: 生成时刻（epoch 秒），由调用方提供。
        diagnosis: 参考的诊断。
        estimator: 估时器。

    Returns:
        计划的第一个版本。

    Raises:
        DomainError: 理由为空；目标与进度不匹配（由 ``generate_plan`` 抛出）。
    """
    plan = generate_plan(goal, structure, progress, diagnosis=diagnosis, estimator=estimator)
    return PlanVersion(
        id=version_id,
        plan_id=plan_id,
        number=1,
        plan=plan,
        basis=_basis(goal, structure, progress, diagnosis),
        reason=reason,
        at=at,
        delta=None,
    )


def revise_plan(
    versions: Sequence[PlanVersion],
    goal: LearningGoal,
    structure: KnowledgeStructure,
    progress: LearningProgress,
    *,
    version_id: PlanVersionId,
    reason: str,
    at: float,
    diagnosis: Diagnosis | None = None,
    estimator: EffortEstimator | None = None,
) -> PlanVersion:
    """在**同一份计划**上产生下一个版本（修订）。

    版本号 = 该计划线现有最大版本号 + 1；差异 = 与上一版相比的变化。

    Args:
        versions: 该计划线的**全部**版本（含历史）。
        goal: 学习目标。
        structure: 知识结构。
        progress: 学习进度。
        version_id: 新版本的标识。
        reason: 改这一版的理由。
        at: 生成时刻（epoch 秒），由调用方提供。
        diagnosis: 参考的诊断。
        estimator: 估时器。

    Returns:
        新版本。

    Raises:
        DomainError: 版本集合为空；版本分属不同计划线；版本号重复；理由为空。
    """
    if not versions:
        raise DomainError("修订计划需要该计划线已有的版本")
    plan_ids = {version.plan_id for version in versions}
    if len(plan_ids) > 1:
        raise DomainError(f"修订需要同一条计划线的版本，收到 {len(plan_ids)} 条")

    previous = max(versions, key=lambda version: version.number)
    if len({version.number for version in versions}) != len(versions):
        raise DomainError("同一计划线内的版本号不能重复")

    plan = generate_plan(goal, structure, progress, diagnosis=diagnosis, estimator=estimator)
    return PlanVersion(
        id=version_id,
        plan_id=previous.plan_id,
        number=previous.number + 1,
        plan=plan,
        basis=_basis(goal, structure, progress, diagnosis),
        reason=reason,
        at=at,
        delta=_delta(previous.plan, plan),
    )


def plan_versions(
    versions: Sequence[PlanVersion],
    *,
    plan_id: PlanId | None = None,
    goal_id: GoalId | None = None,
) -> tuple[PlanVersion, ...]:
    """查计划版本。

    **排序规则**：同一计划线内按**版本号**升序，不按时间。版本号是领域分配的
    序号；时间由调用方传入，可能补录或乱序——让它决定顺序，「谁是当前版本」就
    变成了猜。跨计划线按**首个版本的时间**升序（跨线没有共同序号）。

    **过滤维度**：目标、计划线。两个都不给则返回全部。

    Args:
        versions: 版本集合。
        plan_id: 只看这条计划线。
        goal_id: 只看这个目标。

    Returns:
        版本元组，按上述规则排序。
    """
    selected = [
        version
        for version in versions
        if (plan_id is None or version.plan_id == plan_id)
        and (goal_id is None or version.plan.goal_id == goal_id)
    ]
    lines: dict[PlanId, list[PlanVersion]] = {}
    for version in selected:
        lines.setdefault(version.plan_id, []).append(version)
    ordered = sorted(lines.items(), key=lambda item: min(version.at for version in item[1]))
    result: list[PlanVersion] = []
    for _, line in ordered:
        result.extend(sorted(line, key=lambda version: version.number))
    return tuple(result)


def current_plan_version(
    versions: Sequence[PlanVersion],
    *,
    plan_id: PlanId | None = None,
    goal_id: GoalId | None = None,
) -> PlanVersion | None:
    """当前的计划版本。

    - 指定 ``plan_id``：该计划线里版本号最大的那个。
    - 只指定 ``goal_id``：该目标**当前计划线**（首个版本时间最新的那条）里版本号
      最大的那个。跨计划线没有共同序号，只能用时间判定哪条线是当前的。
    - 都没有：全部版本里 ``at`` 最新的那个。

    Args:
        versions: 版本集合。
        plan_id: 指定计划线。
        goal_id: 指定目标。

    Returns:
        当前版本；没有符合条件的版本时为 ``None``。
    """
    if plan_id is not None:
        candidates = [version for version in versions if version.plan_id == plan_id]
        if not candidates:
            return None
        return max(candidates, key=lambda version: version.number)

    if goal_id is not None:
        scoped = [version for version in versions if version.plan.goal_id == goal_id]
        if not scoped:
            return None
        line = _current_line(scoped)
        return max(line, key=lambda version: version.number)

    if not versions:
        return None
    return max(versions, key=lambda version: (version.at, version.number))


def _current_line(versions: Sequence[PlanVersion]) -> list[PlanVersion]:
    """挑出当前的计划线。

    规则：按**首个版本的时间**最新的那条线是当前的。跨线没有共同序号，只有时间
    能判定「哪条线更近」。

    Args:
        versions: 候选版本（应属于同一目标）。

    Returns:
        当前计划线的版本。
    """
    first_at: dict[PlanId, float] = {}
    for version in versions:
        current = first_at.get(version.plan_id)
        if current is None or version.at < current:
            first_at[version.plan_id] = version.at
    newest_line = max(first_at, key=lambda key: first_at[key])
    return [version for version in versions if version.plan_id == newest_line]


def _basis(
    goal: LearningGoal,
    structure: KnowledgeStructure,
    progress: LearningProgress,
    diagnosis: Diagnosis | None,
) -> PlanBasis:
    """构造生成依据。

    Args:
        goal: 学习目标。
        structure: 知识结构。
        progress: 学习进度。
        diagnosis: 参考的诊断。

    Returns:
        生成依据。
    """
    return PlanBasis(
        goal_id=goal.id,
        learner_id=goal.learner_id,
        course_id=goal.course_id,
        required_points=goal.knowledge_point_ids,
        learned_points=progress.learned_points(),
        diagnosis_id=diagnosis.id if diagnosis is not None else None,
        structure_fingerprint=fingerprint_structure(structure),
    )


def _delta(previous: LearningPlan, current: LearningPlan) -> PlanDelta:
    """比较两个计划，算出差异。

    Args:
        previous: 上一版计划。
        current: 当前版计划。

    Returns:
        差异。
    """
    old_units = {unit.point_id: unit for unit in previous.units}
    new_units = {unit.point_id: unit for unit in current.units}

    added = tuple(point_id for point_id in new_units if point_id not in old_units)
    removed = tuple(point_id for point_id in old_units if point_id not in new_units)
    changed = tuple(
        point_id
        for point_id, unit in new_units.items()
        if point_id in old_units and _placement(old_units[point_id]) != _placement(unit)
    )
    return PlanDelta(added=added, removed=removed, changed=changed)


def _placement(unit: LearningUnit) -> tuple[int | None, int]:
    """单元的排期位置与估时。

    Args:
        unit: 学习单元。

    Returns:
        (排期日, 估时) 元组。
    """
    return (unit.day, unit.estimated_minutes)


__all__ = [
    "PlanBasis",
    "PlanDelta",
    "PlanVersion",
    "current_plan_version",
    "fingerprint_structure",
    "plan_versions",
    "revise_plan",
    "start_plan",
]
