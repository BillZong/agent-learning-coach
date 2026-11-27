"""学习进度：知识点级的学习状态与进度聚合。

Topic 18 的工程问题：**Learner Profile 与 Learning Progress 的区别是什么？
进度应与知识点绑定，还是与计划任务绑定？**

三个答案：

1. **画像是「学习者是谁」，进度是「学习者学到了哪」。** ``Learner``（学习者
   画像）承载**稳定属性**——身份、背景、偏好；它跨课程不变。``LearningProgress``
   承载**推进状态**——在某个知识点上学到了哪一步；它随每次学习活动变化。
   因此**进度不进 Learner**：否则每学一个知识点就要改一次学习者，而且同一
   学习者的多个目标会互相污染。这与 ADR-0015「Learner 不持有目标列表」是同
   一条论证。
2. **进度绑知识点，不绑计划任务。** 计划任务（``PlanTask``）是**运行时**的
   可抛弃对象：会被跳过、内容不可改写、重规划后可以整条重建；知识点是**领域**
   的稳定身份。绑到任务上，重规划一次进度就丢一次；而「这个知识点掌握了吗」
   这个问题本来就不该由「哪些步骤做完了」来回答。详见 ADR-0018。
3. **进度按（学习者 × 课程）分片，不按目标分片。** 知识点身份是**课程内**的
   （ADR-0016），知识状态（Knowledge Space Theory）也是学习者在一个知识空间
   上的状态子集。按目标分片会让同一门课的知识点状态在多个目标间重复——同一
   事实存两份，与 ADR-0015 的双写论证同一个病。目标的完成度因此是**派生
   查询**：目标要求哪些知识点，进度里这些点学会了吗。

**学习状态不复用任务状态。** ``TaskStatus``（``core.agent``）描述「一次执行
的一个步骤」：可以 ``skipped``（不做这一步）、因缺外部条件而 ``blocked``。
``LearningState`` 描述「学习者对某个概念的认知」：没有「跳过」——跳过只是不
执行那个步骤，知识点状态不受影响；``blocked`` 也不是状态而是**派生结果**——
某个点被阻塞，是因为它的**前置还没学会**（ADR-0016 的先修闭包），不是它自己
出了什么状况。

**已有知识如何表达**：诊断判定「学习者本来就会这个知识点」，就是该点的**第
一条迁移**（``not_started → learned``，来源标记为 ``assessed``）。因此不需要
额外的「已有知识」概念——看迁移序列就能区分「本来就会」与「本次学会」。
掌握程度的量化（概率、等级）不在本模块，那属于掌握度模型（Topic 41）。

尚未覆盖：复习调度（Topic 50）、诊断策略（Topic 20）、掌握度量化
（Topic 41）、持久化。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal

from agent_learning_coach.core.learning.domain import (
    CourseId,
    DomainError,
    KnowledgePointId,
    KnowledgeStructure,
    LearnerId,
    require_text,
)
from agent_learning_coach.core.learning.goal import LearningGoal

LearningState = Literal["not_started", "in_progress", "learned"]
"""知识点级的学习状态。

- ``not_started``：没学过。
- ``in_progress``：开始了但没完成。
- ``learned``：学完了（有验证依据）。

**没有 ``skipped``**：跳过是**任务**能做的事，不是知识点能有的状态——没执行
某个步骤，只是那一步没做，知识点的状态不受影响。

**没有 ``blocked``**：被阻塞是**派生结果**（前置还没学会），不是状态本身。
"""

PointSource = Literal["assessed", "learned"]
"""学习状态的来源。

- ``assessed``：诊断判定——学习者本来就会。
- ``learned``：本次学习达成。
"""

_STATE_LABELS: dict[LearningState, str] = {
    "not_started": "未开始",
    "in_progress": "学习中",
    "learned": "已学会",
}

_SOURCE_LABELS: dict[PointSource, str] = {
    "assessed": "诊断判定已有基础",
    "learned": "本次学习达成",
}

_LEARNING_TRANSITIONS: dict[LearningState, frozenset[LearningState]] = {
    "not_started": frozenset({"in_progress", "learned"}),
    "in_progress": frozenset({"learned"}),
    "learned": frozenset({"in_progress"}),
}
"""合法迁移表。

**每条边只有一个操作语义**，这样迁移历史才读得出「发生了什么」：

| 迁移 | 操作 | 含义 |
| ---- | ---- | ---- |
| ``not_started → in_progress`` | ``start`` | 开始学 |
| ``not_started → learned`` | ``assess`` | **诊断判定已有基础**（跳过学习） |
| ``in_progress → learned`` | ``complete`` | 学完并验证 |
| ``learned → in_progress`` | ``review`` | 复习（Topic 50 会用它排复习） |

刻意**不允许** ``in_progress → not_started``：放弃一次学习只是不再推进它，
状态停在「学习中」即可；回退会让「学过没有」变得不可判定。
"""


@dataclass(frozen=True)
class PointTransition:
    """一个知识点的一次状态迁移——**值对象**。

    没有 ``point_id``：它由所在的 ``PointProgress`` 给出。重复存一份就是
    「同一个事实有两个地方」，改一处就可能不一致（ADR-0016 的同一条理由）。
    """

    source: LearningState
    """迁移前的状态。"""

    target: LearningState
    """迁移后的状态。"""

    source_kind: PointSource
    """迁移的来源：诊断判定还是本次学习。"""

    reason: str
    """迁移理由，必填——「为什么变成现在这样」要能回答。"""

    at: float
    """发生时刻（epoch 秒），由调用方传入——领域层不读时钟。"""

    def __post_init__(self) -> None:
        """校验理由非空。

        Raises:
            DomainError: 理由为空或含首尾空白。
        """
        require_text("学习状态迁移理由", self.reason)


@dataclass(frozen=True)
class PointProgress:
    """某个知识点的学习进度——**值对象**。

    状态**由迁移历史派生**：没有迁移就是 ``not_started``。这样「状态说一套、
    历史说另一套」在类型上无法表达（与 ADR-0017 的目标状态机同一个原则）。
    """

    point_id: KnowledgePointId
    """知识点标识。"""

    transitions: tuple[PointTransition, ...] = ()
    """状态迁移历史，按发生顺序。"""

    def __post_init__(self) -> None:
        """校验迁移历史。

        Raises:
            DomainError: 迁移链断裂，或包含非法迁移。
        """
        previous: LearningState = "not_started"
        for transition in self.transitions:
            if transition.source != previous:
                raise DomainError(
                    f"知识点 {self.point_id} 的学习状态历史断链：期望来源状态为"
                    f"{_STATE_LABELS[previous]}，实际为{_STATE_LABELS[transition.source]}",
                )
            if transition.target not in _LEARNING_TRANSITIONS[transition.source]:
                raise DomainError(
                    f"知识点 {self.point_id} 的学习状态包含非法迁移："
                    f"{_STATE_LABELS[transition.source]} -> {_STATE_LABELS[transition.target]}",
                )
            previous = transition.target

    @property
    def state(self) -> LearningState:
        """当前学习状态——由迁移历史派生。"""
        if not self.transitions:
            return "not_started"
        return self.transitions[-1].target

    @property
    def started_at(self) -> float | None:
        """第一次开始学习的时间；从未开始过则为 ``None``。"""
        for transition in self.transitions:
            if transition.target == "in_progress":
                return transition.at
        return None

    @property
    def learned_at(self) -> float | None:
        """最近一次学会的时间；还没学会则为 ``None``。"""
        for transition in reversed(self.transitions):
            if transition.target == "learned":
                return transition.at
        return None

    @property
    def is_learned(self) -> bool:
        """是否已学会。"""
        return self.state == "learned"

    def start(self, *, at: float, reason: str) -> PointProgress:
        """开始学习：未开始 → 学习中。

        Args:
            at: 发生时刻（epoch 秒）。
            reason: 开始理由。

        Returns:
            新实例。

        Raises:
            DomainError: 当前状态不允许开始学习。
        """
        return self._transition(
            "in_progress",
            expected_source="not_started",
            source_kind="learned",
            at=at,
            reason=reason,
            detail="只有还没学过的知识点才能开始学习；已学会的请用复习",
        )

    def complete(self, *, at: float, reason: str) -> PointProgress:
        """学完：学习中 → 已学会。

        Args:
            at: 发生时刻（epoch 秒）。
            reason: 完成理由。

        Returns:
            新实例。

        Raises:
            DomainError: 当前状态不允许完成。
        """
        return self._transition(
            "learned",
            expected_source="in_progress",
            source_kind="learned",
            at=at,
            reason=reason,
            detail="只有正在学习的知识点才能标记为已学会",
        )

    def assess(self, *, at: float, reason: str) -> PointProgress:
        """诊断判定已有基础：未开始 → 已学会。

        这是「已有知识」的唯一表达方式：迁移序列里的**第一条**就是判定，
        与「本次学会」区分得开。

        Args:
            at: 发生时刻（epoch 秒）。
            reason: 判定理由。

        Returns:
            新实例。

        Raises:
            DomainError: 当前状态不允许判定（只能从未开始判定）。
        """
        return self._transition(
            "learned",
            expected_source="not_started",
            source_kind="assessed",
            at=at,
            reason=reason,
            detail="只有还没学过的知识点才能判定为已有基础；正在学的请用学完",
        )

    def review(self, *, at: float, reason: str) -> PointProgress:
        """复习：已学会 → 学习中。

        Args:
            at: 发生时刻（epoch 秒）。
            reason: 复习理由。

        Returns:
            新实例。

        Raises:
            DomainError: 当前状态不允许复习。
        """
        return self._transition(
            "in_progress",
            expected_source="learned",
            source_kind="learned",
            at=at,
            reason=reason,
            detail="只有已学会的知识点才能复习；没学过的请用开始学习",
        )

    def _transition(
        self,
        target: LearningState,
        *,
        expected_source: LearningState,
        source_kind: PointSource,
        at: float,
        reason: str,
        detail: str,
    ) -> PointProgress:
        """校验迁移合法性并返回新实例。

        ``expected_source`` 让**每条边只有一个操作语义**：``review`` 与
        ``start`` 都指向「学习中」，但它们的前提不同——没学过的是开始学，
        学过的是复习。混用会让迁移历史读不出「发生了什么」。

        迁移边本身是否合法由 ``__post_init__`` 校验（那里管的是历史）；这里只
        管「这个操作此刻能不能做」。

        Raises:
            DomainError: 当前状态不允许这个操作。
        """
        source = self.state
        if target not in _LEARNING_TRANSITIONS[source]:
            raise DomainError(
                f"知识点 {self.point_id} 的学习状态不能从"
                f"{_STATE_LABELS[source]}变更为{_STATE_LABELS[target]}",
            )
        if source != expected_source:
            raise DomainError(
                f"知识点 {self.point_id} 的学习状态为{_STATE_LABELS[source]}，"
                f"不能变更为{_STATE_LABELS[target]}：{detail}",
            )
        transition = PointTransition(
            source=source,
            target=target,
            source_kind=source_kind,
            reason=reason,
            at=at,
        )
        return replace(self, transitions=(*self.transitions, transition))


@dataclass(frozen=True)
class LearningProgress:
    """学习进度——**实体**，是 Learner 与 Course 之间的关联聚合。

    身份是 ``(learner_id, course_id)`` 这一对：同一个学习者在同一门课上只有
    一份进度（重新学同一门课是另一个学习者或另一个课程实例，Phase 1 不涉及）。
    因此**不需要额外的进度标识**。

    进度按**知识点**组织，不按计划任务：任务会跳过、会被重建，知识点不会
    （ADR-0018）。
    """

    learner_id: LearnerId
    """进度所属的学习者。"""

    course_id: CourseId
    """进度对应的课程。"""

    points: tuple[PointProgress, ...] = ()
    """各知识点的学习进度，按声明顺序。"""

    def __post_init__(self) -> None:
        """校验进度一致性。

        Raises:
            DomainError: 同一个知识点出现多条进度。
        """
        point_ids = [point.point_id for point in self.points]
        if len(set(point_ids)) != len(point_ids):
            raise DomainError("同一个知识点只能有一条学习进度")

    def point(self, point_id: KnowledgePointId) -> PointProgress | None:
        """按 id 取知识点的进度。

        Args:
            point_id: 知识点标识。

        Returns:
            该点的进度；没记录过则为 ``None``。
        """
        for point in self.points:
            if point.point_id == point_id:
                return point
        return None

    def state_of(self, point_id: KnowledgePointId) -> LearningState:
        """某知识点当前的学习状态。

        从没记录过的点返回 ``not_started``——没记录就是没学过。

        Args:
            point_id: 知识点标识。

        Returns:
            学习状态。
        """
        point = self.point(point_id)
        return "not_started" if point is None else point.state

    def points_in_state(self, state: LearningState) -> tuple[KnowledgePointId, ...]:
        """处于某状态的知识点，按声明顺序。

        Args:
            state: 学习状态。

        Returns:
            知识点标识元组。
        """
        return tuple(point.point_id for point in self.points if point.state == state)

    def learned_points(self) -> tuple[KnowledgePointId, ...]:
        """已学会的知识点，按声明顺序。"""
        return self.points_in_state("learned")

    def start(self, point_id: KnowledgePointId, *, at: float, reason: str) -> LearningProgress:
        """开始学习某个知识点。

        Args:
            point_id: 知识点标识。
            at: 发生时刻（epoch 秒）。
            reason: 开始理由。

        Returns:
            新实例。
        """
        return self._update(point_id, lambda point: point.start(at=at, reason=reason))

    def complete(self, point_id: KnowledgePointId, *, at: float, reason: str) -> LearningProgress:
        """标记某个知识点已学会。

        Args:
            point_id: 知识点标识。
            at: 发生时刻（epoch 秒）。
            reason: 完成理由。

        Returns:
            新实例。
        """
        return self._update(point_id, lambda point: point.complete(at=at, reason=reason))

    def assess(self, point_id: KnowledgePointId, *, at: float, reason: str) -> LearningProgress:
        """记录诊断判定：学习者本来就会某个知识点。

        Args:
            point_id: 知识点标识。
            at: 发生时刻（epoch 秒）。
            reason: 判定理由。

        Returns:
            新实例。
        """
        return self._update(point_id, lambda point: point.assess(at=at, reason=reason))

    def review(self, point_id: KnowledgePointId, *, at: float, reason: str) -> LearningProgress:
        """开始复习某个已学会的知识点。

        Args:
            point_id: 知识点标识。
            at: 发生时刻（epoch 秒）。
            reason: 复习理由。

        Returns:
            新实例。
        """
        return self._update(point_id, lambda point: point.review(at=at, reason=reason))

    def _update(
        self,
        point_id: KnowledgePointId,
        change: Callable[[PointProgress], PointProgress],
    ) -> LearningProgress:
        """更新某个知识点的进度并返回新实例。

        没记录过的点会先建一条 ``not_started`` 的空进度。

        Raises:
            DomainError: 进度里出现重复的知识点。
        """
        updated: list[PointProgress] = []
        found = False
        for point in self.points:
            if point.point_id == point_id:
                updated.append(change(point))
                found = True
            else:
                updated.append(point)
        if not found:
            fresh = PointProgress(point_id=point_id)
            updated.append(change(fresh))
        return replace(self, points=tuple(updated))


@dataclass(frozen=True)
class GoalCompletion:
    """目标完成度——**值对象**。

    只是**结构性完成度**：目标要求的知识点有几个学会了。它**不是**「目标是否
    达成」的结论——达成还需要其它证据，属于应用层判断（ADR-0017 第三档）。
    """

    required: int
    """目标要求的知识点总数。"""

    learned: int
    """其中已学会的个数。"""

    outstanding: tuple[KnowledgePointId, ...]
    """尚未学会的知识点，按目标要求的顺序。"""

    @property
    def ratio(self) -> float:
        """完成比例；没有要求时为 ``0.0``。"""
        if self.required == 0:
            return 0.0
        return self.learned / self.required


def goal_completion(progress: LearningProgress, goal: LearningGoal) -> GoalCompletion:
    """目标的结构性完成度。

    这是**跨聚合查询**：需要同时看到进度与目标，因此是模块级函数而不是方法
    （与 ADR-0017 的 ``check_goal_against_course`` 同一形状）。

    进度与目标必须属于**同一个学习者**且**同一门课**才算数：别人的进度算不出
    这个目标的完成度。

    Args:
        progress: 学习进度。
        goal: 学习目标。

    Returns:
        完成度。

    Raises:
        DomainError: 进度与目标不属于同一学习者，或不属于同一门课。
    """
    if progress.learner_id != goal.learner_id:
        raise DomainError(
            f"进度属于学习者 {progress.learner_id}，目标属于学习者 {goal.learner_id}，无法比较",
        )
    if progress.course_id != goal.course_id:
        raise DomainError(
            f"进度属于课程 {progress.course_id}，目标属于课程 {goal.course_id}，无法比较",
        )
    required = goal.knowledge_point_ids
    outstanding = tuple(
        point_id for point_id in required if progress.state_of(point_id) != "learned"
    )
    return GoalCompletion(
        required=len(required),
        learned=len(required) - len(outstanding),
        outstanding=outstanding,
    )


def state_label(state: LearningState) -> str:
    """学习状态的中文标签。

    **词汇归领域层所有**：渲染层（上下文构建、评估报告）不该自己再造一套状态
    说法——两处说法漂移比多写一个函数糟得多。

    Args:
        state: 学习状态。

    Returns:
        中文标签。
    """
    return _STATE_LABELS[state]


def blocked_points(
    progress: LearningProgress,
    structure: KnowledgeStructure,
) -> tuple[KnowledgePointId, ...]:
    """被前置阻塞的知识点——**派生查询**。

    某个点被阻塞，当且仅当它**自己还没学会**，且它的**直接前置**里还有没学会
    的。这不是状态，而是算出来的结果（ADR-0018）。遍历知识结构的**全部
    知识点**：还没开始学但前置没学会的点，同样是被阻塞的。

    与 ``ready_points`` 的关系：``ready_points`` 回答「现在能开始学哪些」，
    本函数回答「哪些还动不了」。一个正在学、但前置没学会的点，会出现在这里
    ——它是最需要被看见的那种阻塞。

    Args:
        progress: 学习进度。
        structure: 知识结构，提供前置关系与学习顺序。

    Returns:
        被阻塞的知识点，按学习顺序排列。
    """
    blocked: set[KnowledgePointId] = set()
    for point in structure.order():
        if progress.state_of(point.id) == "learned":
            continue
        for prerequisite in structure.prerequisites_of(point.id):
            if progress.state_of(prerequisite.id) != "learned":
                blocked.add(point.id)
                break
    return tuple(point.id for point in structure.order() if point.id in blocked)


def ready_points(
    progress: LearningProgress,
    structure: KnowledgeStructure,
) -> tuple[KnowledgePointId, ...]:
    """现在可以开始学的知识点——**派生查询**。

    尚未学过、且直接前置都已学会。遍历的是**知识结构里的全部知识点**，不是
    进度里已记录的那些——没记录过的点同样是「没学过」，而且它们往往正是
    「接下来该学哪个」的答案。

    与 ``blocked_points`` 相对：这里回答「能开始哪些」，那里回答「哪些还动
    不了」。

    Args:
        progress: 学习进度。
        structure: 知识结构，提供前置关系与学习顺序。

    Returns:
        可以开始的知识点，按学习顺序排列。
    """
    ready: set[KnowledgePointId] = set()
    for point in structure.order():
        if progress.state_of(point.id) != "not_started":
            continue
        if all(
            progress.state_of(prerequisite.id) == "learned"
            for prerequisite in structure.prerequisites_of(point.id)
        ):
            ready.add(point.id)
    return tuple(point.id for point in structure.order() if point.id in ready)


__all__ = [
    "GoalCompletion",
    "LearningProgress",
    "LearningState",
    "PointProgress",
    "PointSource",
    "PointTransition",
    "blocked_points",
    "goal_completion",
    "ready_points",
    "state_label",
]
