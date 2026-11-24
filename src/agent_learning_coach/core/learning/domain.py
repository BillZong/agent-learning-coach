"""学习领域：实体、值对象与聚合边界。

Topic 15 的问题是：Learner、Course、Learning Goal 三个概念的边界在哪里？哪些是
实体，哪些是值对象？本模块的回答（论证见 ADR-0015）：

| 概念 | 分类 | 身份 | 归属 |
| ---- | ---- | ---- | ---- |
| `Learner` | 实体 | `LearnerId` | 独立聚合 |
| `Course` | 实体 | `CourseId` | 独立聚合（内含知识点） |
| `KnowledgePoint` | 实体 | `KnowledgePointId` | `Course` 聚合内部 |
| `LearningGoal` | 实体 | `GoalId` | 独立聚合：Learner 与 Course 的**关联** |
| 四类标识、`GoalConstraint` | 值对象 | 无身份，按值相等 | 不单独存在 |

三条边界规则：

1. **跨聚合只按 id 引用，不嵌入对方对象。** `LearningGoal` 记住 `LearnerId` 与
   `CourseId`，而不是持有 `Learner` / `Course`；`Learner` 也不持有目标列表。
   单向引用避免了「两个对象各自保存对方」带来的双写与不一致。
2. **身份来自 id，不来自对象。** 实体不可变（`frozen`）：改名、达成目标都返回
   新实例。历史与审计因此不会被原地修改掉，对象可以在任意地方安全传递。
3. **不变量在构造时校验，不交给调用方自觉。** 知识点 id 唯一、前置存在且无环、
   同一终态不可回退——违反时抛 `DomainError`，错误信息说明「哪里不对、怎么改」。

职责边界：本模块**不认识模型、工具与运行时**。学习目标交给 Agent 规划时，通过
``bridge.to_goal_spec`` 单向映射成 ``GoalSpec``；反方向不允许——领域模型不能
依赖 ``core.agent``。

尚未覆盖：知识点是否跨课程共享、已有知识（掌握度）、学习领域注册表、持久化。
它们在后续主题里定，本模块刻意不预留字段。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal

GoalStatus = Literal["active", "achieved", "abandoned"]
"""学习目标生命周期。

``active`` 是唯一可迁移的状态；``achieved`` 与 ``abandoned`` 都是终态——目标
一旦达成或放弃就不能回到进行中，要重新学习就新建目标。
"""

ConstraintKind = Literal["time", "prerequisite", "preference"]
"""目标约束的种类。"""

_STATUS_LABELS: dict[GoalStatus, str] = {
    "active": "进行中",
    "achieved": "已达成",
    "abandoned": "已放弃",
}

_CONSTRAINT_LABELS: dict[ConstraintKind, str] = {
    "time": "时间",
    "prerequisite": "先修",
    "preference": "偏好",
}


class DomainError(Exception):
    """领域规则被违反。

    错误信息面向调用方与用户：说明哪个字段/对象不合法，以及怎样才算合法。
    """


def _require_text(field: str, value: str) -> None:
    """校验文本字段非空且没有首尾空白。"""
    if not value or not value.strip():
        raise DomainError(f"{field}不能为空")
    if value != value.strip():
        raise DomainError(f"{field}不能包含首尾空白：{value!r}")


@dataclass(frozen=True)
class _Identifier:
    """值对象基类：一个非空的领域标识。

    子类各自是不同的类型，因此 ``LearnerId("x")`` 与 ``CourseId("x")`` **不相等**
    ——把学习者 id 当课程 id 传进来会在边界处暴露，而不是变成一个查不到的 id。
    """

    value: str

    def __post_init__(self) -> None:
        _require_text("标识", self.value)

    def __str__(self) -> str:
        """返回标识文本。"""
        return self.value


@dataclass(frozen=True)
class LearnerId(_Identifier):
    """学习者标识。"""


@dataclass(frozen=True)
class CourseId(_Identifier):
    """课程标识。"""


@dataclass(frozen=True)
class GoalId(_Identifier):
    """学习目标标识。"""


@dataclass(frozen=True)
class KnowledgePointId(_Identifier):
    """知识点标识。"""


@dataclass(frozen=True)
class GoalConstraint:
    """学习目标的约束条件——**值对象**。

    没有 id：两条内容相同的约束是同一个约束，替换其中一条不产生新身份。
    """

    kind: ConstraintKind
    """约束种类。"""

    text: str
    """约束内容，例如「每周 3 小时」。"""

    def __post_init__(self) -> None:
        """校验约束内容。"""
        _require_text("约束内容", self.text)

    @property
    def label(self) -> str:
        """约束种类的中文标签。"""
        return _CONSTRAINT_LABELS[self.kind]

    def render(self) -> str:
        """渲染成给模型与用户看的文本。"""
        return f"{self.label}：{self.text}"


@dataclass(frozen=True)
class Learner:
    """学习者——**实体**。

    身份是 ``id``。``background`` / ``preferences`` 是描述性文本，不是学习者
    的「属性值对象」：它们没有独立语义，也不参与任何不变量，因此不做建模。

    **不持有目标列表**：目标单向引用学习者（``LearningGoal.learner_id``）。
    两边都存一份，就要在每次创建/放弃目标时同步两个地方——这是不一致的来源。
    """

    id: LearnerId
    """学习者标识。"""

    name: str
    """显示名称。"""

    background: tuple[str, ...] = ()
    """学习背景，例如「有 Python 经验」。"""

    preferences: tuple[str, ...] = ()
    """学习偏好，例如「喜欢先动手再读文档」。"""

    def __post_init__(self) -> None:
        """校验名称与描述条目。"""
        _require_text("学习者名称", self.name)
        for item in (*self.background, *self.preferences):
            _require_text("学习者描述条目", item)

    def rename(self, name: str) -> Learner:
        """改名并返回新实例——实体不可变。

        Args:
            name: 新名称。

        Returns:
            改名后的学习者。

        Raises:
            DomainError: 新名称为空。
        """
        _require_text("学习者名称", name)
        return replace(self, name=name)


@dataclass(frozen=True)
class KnowledgePoint:
    """知识点——**实体**，位于 ``Course`` 聚合内部。

    它的身份在课程内被引用（前置依赖、后续的掌握度与诊断）。前置只允许指向
    同一门课的知识点；跨课程共享的问题留给 Topic 16，本模块不预设。
    """

    id: KnowledgePointId
    """知识点标识。"""

    title: str
    """知识点名称。"""

    prerequisites: tuple[KnowledgePointId, ...] = ()
    """前置知识点——必须先掌握。"""

    def __post_init__(self) -> None:
        """校验名称。"""
        _require_text("知识点名称", self.title)


@dataclass(frozen=True)
class Course:
    """课程——**实体**，也是知识点聚合的根。

    课程拥有自己的知识点与依赖关系；构造时校验完整性，因此拿出来用的
    ``Course`` 一定是自洽的：知识点 id 唯一、前置存在、依赖无环。
    """

    id: CourseId
    """课程标识。"""

    title: str
    """课程名称。"""

    knowledge_points: tuple[KnowledgePoint, ...] = ()
    """课程包含的知识点，按作者声明的顺序保存。"""

    def __post_init__(self) -> None:
        """校验课程内部一致性。

        Raises:
            DomainError: 名称不合法、知识点 id 重复、前置缺失/自指/重复，或
                依赖成环。
        """
        _require_text("课程名称", self.title)
        index: dict[KnowledgePointId, KnowledgePoint] = {}
        for point in self.knowledge_points:
            if point.id in index:
                raise DomainError(
                    f"课程「{self.title}」中知识点 id 重复：{point.id}",
                )
            index[point.id] = point
        for point in self.knowledge_points:
            seen: set[KnowledgePointId] = set()
            for required in point.prerequisites:
                if required == point.id:
                    raise DomainError(f"知识点「{point.title}」不能依赖自身")
                if required in seen:
                    raise DomainError(
                        f"知识点「{point.title}」的前置 {required} 重复声明",
                    )
                seen.add(required)
                if required not in index:
                    raise DomainError(
                        f"知识点「{point.title}」的前置 {required} 不在课程内",
                    )
        _knowledge_order(self.knowledge_points)

    def knowledge_point(self, point_id: KnowledgePointId) -> KnowledgePoint:
        """按 id 取知识点。

        Args:
            point_id: 知识点标识。

        Returns:
            知识点。

        Raises:
            DomainError: 课程中不存在该知识点。
        """
        for point in self.knowledge_points:
            if point.id == point_id:
                return point
        raise DomainError(f"课程「{self.title}」中没有知识点 {point_id}")

    def knowledge_order(self) -> tuple[KnowledgePoint, ...]:
        """按依赖关系给出学习顺序。

        没有依赖的知识点保持声明顺序，因此结果是确定的。**这只是派生视图**：
        它服务「先学什么」的展示，不承担调度与历史——与计划模块的三序分离
        （ADR-0013）同一个原则。

        Returns:
            拓扑序的知识点。
        """
        return _knowledge_order(self.knowledge_points)


@dataclass(frozen=True)
class LearningGoal:
    """学习目标——**实体**，也是 Learner 与 Course 之间的关联聚合。

    目标不是学习者的子对象，也不是课程的子对象：它引用两个 id，自己是一个
    独立的生命周期（进行中 → 已达成 / 已放弃）。
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
    """可验证的学习结果——至少一条。"""

    constraints: tuple[GoalConstraint, ...] = ()
    """约束条件。"""

    status: GoalStatus = "active"
    """生命周期状态。"""

    def __post_init__(self) -> None:
        """校验目标内容。

        Raises:
            DomainError: 陈述为空，或没有任何学习结果。
        """
        _require_text("目标陈述", self.statement)
        if not self.outcomes:
            raise DomainError(f"目标「{self.statement}」至少要有一条可验证的学习结果")
        for outcome in self.outcomes:
            _require_text("学习结果", outcome)

    @property
    def is_open(self) -> bool:
        """目标是否仍在进行中。"""
        return self.status == "active"

    def achieve(self) -> LearningGoal:
        """把目标标记为已达成并返回新实例。

        Returns:
            已达成的新实例。

        Raises:
            DomainError: 目标已不处于进行中。
        """
        return self._transition("achieved")

    def abandon(self) -> LearningGoal:
        """放弃目标并返回新实例。

        Returns:
            已放弃的新实例。

        Raises:
            DomainError: 目标已不处于进行中。
        """
        return self._transition("abandoned")

    def _transition(self, target: GoalStatus) -> LearningGoal:
        """校验状态迁移并返回新实例。"""
        if self.status != "active":
            raise DomainError(
                f"目标 {self.id} 状态为{_STATUS_LABELS[self.status]}，"
                f"不能再变更为{_STATUS_LABELS[target]}",
            )
        return replace(self, status=target)


def _knowledge_order(
    points: Sequence[KnowledgePoint],
) -> tuple[KnowledgePoint, ...]:
    """按依赖给出稳定的拓扑序；成环时抛 ``DomainError``。"""
    remaining = list(points)
    ordered: list[KnowledgePoint] = []
    finished: set[KnowledgePointId] = set()
    while remaining:
        ready = [
            point
            for point in remaining
            if all(required in finished for required in point.prerequisites)
        ]
        if not ready:
            raise DomainError(f"课程知识点依赖成环：{_describe_cycle(remaining)}")
        ordered.extend(ready)
        finished.update(point.id for point in ready)
        remaining = [point for point in remaining if point.id not in finished]
    return tuple(ordered)


def _describe_cycle(points: Sequence[KnowledgePoint]) -> str:
    """从无法排序的知识点里找出一条环，用于错误信息。"""
    by_id = {point.id: point for point in points}
    path: list[KnowledgePointId] = []
    on_path: set[KnowledgePointId] = set()

    def walk(point_id: KnowledgePointId) -> list[KnowledgePointId] | None:
        path.append(point_id)
        on_path.add(point_id)
        for required in by_id[point_id].prerequisites:
            if required not in by_id:
                continue
            if required in on_path:
                start = path.index(required)
                return [*path[start:], required]
            found = walk(required)
            if found is not None:
                return found
        path.pop()
        on_path.discard(point_id)
        return None

    for point_id in by_id:
        cycle = walk(point_id)
        if cycle is not None:
            return " -> ".join(str(item) for item in cycle)
        path.clear()
        on_path.clear()
    return "未知"


__all__ = [
    "ConstraintKind",
    "Course",
    "CourseId",
    "DomainError",
    "GoalConstraint",
    "GoalId",
    "GoalStatus",
    "KnowledgePoint",
    "KnowledgePointId",
    "Learner",
    "LearnerId",
    "LearningGoal",
]
