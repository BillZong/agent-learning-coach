"""学习目标的建模：状态机、结构化约束与能力要求。

Topic 15 建立了目标的**存在**（三个独立聚合之一），Topic 16 建立了知识**结构**。
Topic 17 回答两个问题：

1. **学习目标的状态机是什么？**
   目标是会迁移的实体：``draft``（草稿）→ ``active``（进行中）⇄ ``suspended``
   （已暂停），以及 ``achieved``（已达成）/ ``abandoned``（已放弃）/ ``expired``
   （已过期）三个终态。迁移表写在代码里（``_TRANSITIONS``），非法迁移在调用点
   就被拒绝；每次迁移留下一条带**理由**与**时间**的记录，因此「目标为什么变成
   现在这样」有答案（ADR-0015 的审计要求）。
2. **目标约束应由领域层保证，还是由应用层保证？**
   分成三档：

   - **领域层（聚合内自洽）**：状态迁移合法、终态不可回退、时间范围自洽
     （结束晚于开始）、每日投入为正、能力要求与前置条件不重复。这些只依赖目标
     自身的数据，因此在构造时校验。
   - **领域服务（跨聚合一致）**：目标要求与前置条件引用的知识点是否真的存在于
     目标课程的知识结构中。它需要另一个聚合的对象，但仍是纯函数、无 I/O，
     因此留在领域层：``consistency.check_goal_consistency``。
   - **应用层（依赖外部事实）**：时间窗口是否已经到期（需要「现在」）、学习者
     是否已经掌握前置（需要学习进度）、目标是否真的达成（需要学习证据）。
     这些都要外部数据或模型判断，领域层不假装自己能回答。

约束不再用自由文本（``GoalConstraint(kind, text)``）表达，而是结构化的值对象
（``TimeWindow`` / ``DailyEffort`` / ``CapabilityRequirement``）：自由文本无法
校验，也无法回答「每天能学多久」这类问题。

难度**不**建模为目标约束：难度是知识点的属性（课程侧），目标侧表达的是「掌握
哪些知识点」与「达到什么结果」；掌握层次属于掌握度模型，留给 Topic 41。

领域模型仍然不认识模型、工具与运行时；目标交给 Agent 规划时经
``bridge.to_goal_spec`` 单向映射成 ``GoalSpec``（ADR-0013、ADR-0015）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Literal

from agent_learning_coach.core.learning.domain import (
    CourseId,
    DomainError,
    GoalId,
    KnowledgePointId,
    LearnerId,
    require_text,
)

GoalStatus = Literal["draft", "active", "suspended", "achieved", "abandoned", "expired"]
"""学习目标的生命周期状态。

- ``draft``：**草稿**。目标已提出，但还没绑定要掌握的知识点——诊断（Topic 20）
  之前的目标处于这个状态。
- ``active``：**进行中**。唯一可以推进、暂停、达成的状态。
- ``suspended``：**已暂停**。因时间或优先级被搁置，可以恢复。
- ``achieved`` / ``abandoned`` / ``expired``：三个**终态**，不可回退——重新学习
  要新建目标（ADR-0015）。
"""

_STATUS_LABELS: dict[GoalStatus, str] = {
    "draft": "草稿",
    "active": "进行中",
    "suspended": "已暂停",
    "achieved": "已达成",
    "abandoned": "已放弃",
    "expired": "已过期",
}

_TERMINAL_STATUSES: frozenset[GoalStatus] = frozenset({"achieved", "abandoned", "expired"})

_TRANSITIONS: dict[GoalStatus, frozenset[GoalStatus]] = {
    "draft": frozenset({"active", "abandoned"}),
    "active": frozenset({"achieved", "abandoned", "suspended", "expired"}),
    "suspended": frozenset({"active", "abandoned", "expired"}),
    "achieved": frozenset(),
    "abandoned": frozenset(),
    "expired": frozenset(),
}
"""合法迁移表：状态 → 可以迁往的状态。

写成数据而不是散落在方法里的 ``if``，是为了让「哪些迁移合法」有一个可以
直接读、也可以直接测的地方。
"""


@dataclass(frozen=True)
class TimeWindow:
    """目标时间范围——**值对象**。

    只描述「什么时候之前要完成」。**是否已经过期不在这里判断**：领域层没有
    时钟，过期判定属于应用层（它才知道「现在」）。
    """

    start: date
    """开始日期。"""

    end: date
    """结束日期。"""

    def __post_init__(self) -> None:
        """校验时间范围自洽。

        Raises:
            DomainError: 结束日期不晚于开始日期。
        """
        if self.end <= self.start:
            raise DomainError(
                f"目标时间范围的结束日期 {self.end} 必须晚于开始日期 {self.start}",
            )

    def has_passed(self, now: datetime) -> bool:
        """给定时刻是否已经越过这个时间范围。

        这是**纯查询**：调用方给出「现在」，领域层不自己读时钟。

        Args:
            now: 判定时刻。

        Returns:
            越过了结束日期则为 ``True``。
        """
        return now.date() > self.end

    def render(self) -> str:
        """渲染成给模型与用户看的文本。"""
        return f"{self.start.isoformat()} ~ {self.end.isoformat()}"


@dataclass(frozen=True)
class DailyEffort:
    """每日投入时间——**值对象**。

    「每周 3 小时」是模糊的，「每天 90 分钟」才能和日程、容量比较。
    """

    minutes_per_day: int
    """每天投入的分钟数。"""

    def __post_init__(self) -> None:
        """校验投入为正整数。

        Raises:
            DomainError: 分钟数不是正整数。
        """
        if self.minutes_per_day <= 0:
            raise DomainError(f"每日投入时间必须是正整数分钟：{self.minutes_per_day}")

    def render(self) -> str:
        """渲染成给模型与用户看的文本。"""
        return f"{self.minutes_per_day} 分钟/天"


@dataclass(frozen=True)
class CapabilityRequirement:
    """目标能力要求——**值对象**。

    「哪个知识点」+「支撑目标的哪一条学习结果」。知识点是课程聚合内部的实体，
    这里**只按 id 引用**：跨聚合不嵌入对方对象（ADR-0015）。

    ``outcome`` 必须是目标 ``outcomes`` 中已有的一条——能力要求**不另立结果**，
    它回答的是「这条结果由哪个知识点支撑」。否则同一件事会有两份说法（一条在
    outcomes，一条在 requirements），映射给规划器时会变成重复任务。
    """

    point_id: KnowledgePointId
    """支撑该结果的知识点。"""

    outcome: str
    """目标的一条学习结果（必须出自 ``LearningGoal.outcomes``）。"""

    def __post_init__(self) -> None:
        """校验结果非空。

        Raises:
            DomainError: 结果为空或含首尾空白。
        """
        require_text("能力要求的结果", self.outcome)

    def render(self) -> str:
        """渲染成给模型与用户看的文本。"""
        return f"{self.point_id}：{self.outcome}"


@dataclass(frozen=True)
class GoalTransition:
    """一次状态迁移——**值对象**。

    迁移历史是目标「为什么变成现在这样」的证据：来源状态、目标状态、理由与
    发生时间。**时间由调用方传入**——领域层不读时钟（ADR-0015 的分层要求）。
    """

    source: GoalStatus
    """迁移前的状态。"""

    target: GoalStatus
    """迁移后的状态。"""

    reason: str
    """迁移理由，必填——没有理由的状态变化无法解释。"""

    at: float
    """发生时刻（epoch 秒）。"""

    def __post_init__(self) -> None:
        """校验理由非空。

        Raises:
            DomainError: 理由为空或含首尾空白。
        """
        require_text("状态迁移理由", self.reason)


@dataclass(frozen=True)
class LearningGoal:
    """学习目标——**实体**，也是 Learner 与 Course 之间的关联聚合。

    目标不是学习者的子对象，也不是课程的子对象：它引用两个 id，自己是一个
    独立的生命周期。

    状态不作为字段存储，而是从迁移历史**派生**（``status``）——历史是唯一事实
    来源，避免「状态说一套、历史说另一套」。
    """

    id: GoalId
    """目标标识。"""

    learner_id: LearnerId
    """目标所属的学习者。"""

    course_id: CourseId
    """目标对应的课程。"""

    statement: str
    """目标陈述。"""

    outcomes: tuple[str, ...]
    """总体可验证的学习结果——至少一条。"""

    requirements: tuple[CapabilityRequirement, ...] = ()
    """目标能力要求：掌握哪些知识点、达到什么结果。

    空表示「还没绑定知识点」——诊断之前的目标就是这种状态，因此草稿目标允许
    没有能力要求。
    """

    time_window: TimeWindow | None = None
    """目标时间范围；``None`` 表示没有期限。"""

    daily_effort: DailyEffort | None = None
    """每日投入时间；``None`` 表示未约定。"""

    prerequisites: tuple[KnowledgePointId, ...] = ()
    """前置条件：开始这个目标前必须已经掌握的知识点。"""

    transitions: tuple[GoalTransition, ...] = ()
    """迁移历史，按发生顺序。空历史意味着目标还在草稿状态。"""

    def __post_init__(self) -> None:
        """校验目标内容与迁移历史。

        Raises:
            DomainError: 陈述为空、没有学习结果、能力要求或前置条件重复、
                迁移历史断链或含非法迁移。
        """
        require_text("目标陈述", self.statement)
        if not self.outcomes:
            raise DomainError(f"目标「{self.statement}」至少要有一条可验证的学习结果")
        for outcome in self.outcomes:
            require_text("学习结果", outcome)

        seen: set[tuple[KnowledgePointId, str]] = set()
        for requirement in self.requirements:
            require_text("能力要求的结果", requirement.outcome)
            if requirement.outcome not in self.outcomes:
                raise DomainError(
                    f"能力要求的结果「{requirement.outcome}」不在目标的学习结果中："
                    f"{list(self.outcomes)}",
                )
            key = (requirement.point_id, requirement.outcome)
            if key in seen:
                raise DomainError(
                    f"同一目标对知识点 {requirement.point_id} 的结果"
                    f"「{requirement.outcome}」只能有一条能力要求",
                )
            seen.add(key)

        if len(set(self.prerequisites)) != len(self.prerequisites):
            raise DomainError("前置条件不能重复")

        previous: GoalStatus = "draft"
        for transition in self.transitions:
            if transition.source != previous:
                raise DomainError(
                    f"目标 {self.id} 的迁移历史断链：期望来源状态为"
                    f"{_STATUS_LABELS[previous]}，实际为{_STATUS_LABELS[transition.source]}",
                )
            if transition.target not in _TRANSITIONS[transition.source]:
                raise DomainError(
                    f"目标 {self.id} 的迁移历史包含非法迁移："
                    f"{_STATUS_LABELS[transition.source]} -> {_STATUS_LABELS[transition.target]}",
                )
            previous = transition.target

    @property
    def status(self) -> GoalStatus:
        """当前状态——由迁移历史派生。"""
        if not self.transitions:
            return "draft"
        return self.transitions[-1].target

    @property
    def is_active(self) -> bool:
        """目标是否处于进行中。"""
        return self.status == "active"

    @property
    def is_terminal(self) -> bool:
        """目标是否已进入终态。"""
        return self.status in _TERMINAL_STATUSES

    @property
    def knowledge_point_ids(self) -> tuple[KnowledgePointId, ...]:
        """目标绑定的知识点——由能力要求派生，按声明顺序，不去重。"""
        return tuple(requirement.point_id for requirement in self.requirements)

    def points_for(self, outcome: str) -> tuple[KnowledgePointId, ...]:
        """支撑某条学习结果的知识点，按声明顺序。

        Args:
            outcome: 一条学习结果。

        Returns:
            支撑该结果的知识点；没有则为空元组。
        """
        return tuple(
            requirement.point_id
            for requirement in self.requirements
            if requirement.outcome == outcome
        )

    def outcomes_for(self, point_id: KnowledgePointId) -> tuple[str, ...]:
        """某个知识点支撑的学习结果，按声明顺序。

        Args:
            point_id: 知识点标识。

        Returns:
            该知识点支撑的学习结果；没有则为空元组。
        """
        return tuple(
            requirement.outcome
            for requirement in self.requirements
            if requirement.point_id == point_id
        )

    def bind_requirements(
        self,
        requirements: tuple[CapabilityRequirement, ...],
    ) -> LearningGoal:
        """绑定能力要求（草稿 → 草稿，返回新实例）。

        诊断（Topic 20）之后才知道目标要掌握哪些知识点，因此这一步发生在激活
        **之前**。已激活的目标不允许改绑定——那是一次范围变更，属于计划调整
        （Topic 48），不是目标建模。

        Args:
            requirements: 要绑定的能力要求。

        Returns:
            绑定后的新实例。

        Raises:
            DomainError: 目标已不是草稿状态。
        """
        if self.status != "draft":
            raise DomainError(
                f"目标 {self.id} 状态为{_label(self.status)}，不能再绑定能力要求",
            )
        return replace(self, requirements=requirements)

    def activate(self, *, at: float, reason: str) -> LearningGoal:
        """激活目标：草稿 → 进行中。

        Args:
            at: 发生时刻（epoch 秒），由调用方提供。
            reason: 激活理由。

        Returns:
            激活后的新实例。

        Raises:
            DomainError: 当前状态不允许激活，或目标还没绑定任何能力要求。
        """
        if not self.requirements:
            raise DomainError(
                f"目标 {self.id} 还没绑定任何能力要求，不能进入进行中状态",
            )
        return self._transition("active", at=at, reason=reason)

    def suspend(self, *, at: float, reason: str) -> LearningGoal:
        """暂停目标：进行中 → 已暂停。

        Args:
            at: 发生时刻（epoch 秒）。
            reason: 暂停理由。

        Returns:
            暂停后的新实例。

        Raises:
            DomainError: 当前状态不允许暂停。
        """
        return self._transition("suspended", at=at, reason=reason)

    def resume(self, *, at: float, reason: str) -> LearningGoal:
        """恢复目标：已暂停 → 进行中。

        Args:
            at: 发生时刻（epoch 秒）。
            reason: 恢复理由。

        Returns:
            恢复后的新实例。

        Raises:
            DomainError: 当前状态不允许恢复。
        """
        return self._transition("active", at=at, reason=reason)

    def achieve(self, *, at: float, reason: str) -> LearningGoal:
        """达成目标：进行中 → 已达成。

        Args:
            at: 发生时刻（epoch 秒）。
            reason: 达成依据的说明（证据型评估留给 Topic 40）。

        Returns:
            已达成的新实例。

        Raises:
            DomainError: 当前状态不允许达成。
        """
        return self._transition("achieved", at=at, reason=reason)

    def abandon(self, *, at: float, reason: str) -> LearningGoal:
        """放弃目标：草稿 / 进行中 / 已暂停 → 已放弃。

        Args:
            at: 发生时刻（epoch 秒）。
            reason: 放弃理由。

        Returns:
            已放弃的新实例。

        Raises:
            DomainError: 当前状态不允许放弃。
        """
        return self._transition("abandoned", at=at, reason=reason)

    def expire(self, *, at: float, reason: str) -> LearningGoal:
        """让目标过期：进行中 / 已暂停 → 已过期。

        **过期判定属于应用层**：它比较当前时间与时间范围，然后调用这个迁移。
        领域层只负责「这次迁移合法」。

        Args:
            at: 发生时刻（epoch 秒）。
            reason: 过期说明。

        Returns:
            已过期的新实例。

        Raises:
            DomainError: 当前状态不允许过期，或目标没有时间范围。
        """
        if self.time_window is None:
            raise DomainError(f"目标 {self.id} 没有时间范围，不存在过期")
        return self._transition("expired", at=at, reason=reason)

    def _transition(self, target: GoalStatus, *, at: float, reason: str) -> LearningGoal:
        """校验迁移合法性并返回新实例。

        Raises:
            DomainError: 当前状态不允许迁往目标状态。
        """
        source = self.status
        if target not in _TRANSITIONS[source]:
            raise DomainError(
                f"目标 {self.id} 状态为{_STATUS_LABELS[source]}，"
                f"不能再变更为{_STATUS_LABELS[target]}",
            )
        transition = GoalTransition(source=source, target=target, reason=reason, at=at)
        return replace(self, transitions=(*self.transitions, transition))


def _label(status: GoalStatus) -> str:
    """状态的中文标签。"""
    return _STATUS_LABELS[status]


__all__ = [
    "CapabilityRequirement",
    "DailyEffort",
    "GoalStatus",
    "GoalTransition",
    "LearningGoal",
    "TimeWindow",
]
