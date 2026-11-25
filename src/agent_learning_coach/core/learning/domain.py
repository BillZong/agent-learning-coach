"""学习领域：实体、值对象与聚合边界。

Topic 15 的问题是：Learner、Course、Learning Goal 三个概念的边界在哪里？哪些是
实体，哪些是值对象？Topic 16 继续问：知识点与知识点关系怎么建模，才能同时支撑
**路径计算**与**薄弱点归因**？Topic 17 再问：目标的状态机与约束该怎么建模。

分类（论证见 ADR-0015、ADR-0016、ADR-0017）：

| 概念 | 分类 | 身份 | 归属 |
| ---- | ---- | ---- | ---- |
| `Learner` | 实体 | `LearnerId` | 独立聚合 |
| `Course` | 实体 | `CourseId` | 独立聚合（聚合根） |
| `KnowledgeStructure` | 值对象 | 无 | `Course` 聚合内部 |
| `KnowledgePoint` | 实体 | `KnowledgePointId` | 结构内部 |
| `KnowledgeRelation` | 值对象 | 无 | 结构内部 |
| `LearningGoal` | 实体 | `GoalId` | 独立聚合：Learner 与 Course 的**关联**（见 `goal.py`） |
| 四类标识 | 值对象 | 无身份，按值相等 | 不单独存在 |

四条边界规则：

1. **跨聚合只按 id 引用，不嵌入对方对象。** `LearningGoal` 记住 `LearnerId` 与
   `CourseId`，而不是持有 `Learner` / `Course`；`Learner` 也不持有目标列表。
   单向引用避免了「两个对象各自保存对方」带来的双写与不一致。
2. **身份来自 id，不来自对象。** 实体不可变（`frozen`）：改名、达成目标都返回
   新实例。历史与审计因此不会被原地修改掉，对象可以在任意地方安全传递。
3. **不变量在构造时校验，不交给调用方自觉。** 知识点 id 唯一、关系端点存在、
   `requires` 无环、`part_of` 是树——违反时抛 `DomainError`，错误信息说明
   「哪里不对、怎么改」。目标自身的规则在 `goal.py`，跨目标的规则在
   `consistency.py`。
4. **关系是结构的事实，不是知识点的字段。** 前置不是 `KnowledgePoint` 的属性，
   而是 `KnowledgeStructure` 里的一条 `requires` 关系：同一个点可以被多条关系
   引用，把列表塞进点里会让「改一处、另一处过期」成为可能。

**路径与归因都是派生查询**，不落库：`order()` 回答「先学什么」，
`prerequisite_closure()` 回答「这一点没掌握，先补哪些」，`dependent_closure()`
回答「这一点没掌握，后续哪些受影响」，`parent_of()` / `children_of()` 提供主题
层级。它们每次重算——派生视图不应有独立状态。

职责边界：本模块**不认识模型、工具与运行时**。学习目标交给 Agent 规划时，通过
``bridge.to_goal_spec`` 单向映射成 ``GoalSpec``；反方向不允许——领域模型不能
依赖 ``core.agent``。

尚未覆盖：知识点跨课程共享（留给 Topic 22 的全局知识地图）、已有知识/掌握度
（Topic 18、20）、持久化。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

RelationKind = Literal["requires", "part_of"]
"""知识点关系的种类。

- ``requires``：**前置依赖**。``source`` 必须先掌握 ``target``。有向、必须无环，
  决定学习顺序。
- ``part_of``：**归属层级**。``source`` 属于 ``target`` 这个更大的主题。有向、
  必须无环，且每个知识点至多一个父节点（树）。

两种关系语义不同，因此分开校验：``requires`` 决定「能不能开始」，
``part_of`` 决定「归到哪个主题」。把它们合成一种「关联」会让查询与校验都失去
判据。``related``（无向关联）暂时不做——没有消费者，且无向边的去重与语义都更
复杂（见 ADR-0016）。
"""


class DomainError(Exception):
    """领域规则被违反。

    错误信息面向调用方与用户：说明哪个字段/对象不合法，以及怎样才算合法。
    """


def require_text(field: str, value: str) -> None:
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
        require_text("标识", self.value)

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
class DiagnosisId(_Identifier):
    """诊断标识。

    诊断是**一次活动**，不是状态：同一个目标可以诊断多次（初诊、复诊），因此
    它有独立身份，而不是 (学习者, 目标) 这一对。
    """


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
        require_text("学习者名称", self.name)
        for item in (*self.background, *self.preferences):
            require_text("学习者描述条目", item)

    def rename(self, name: str) -> Learner:
        """改名并返回新实例——实体不可变。

        Args:
            name: 新名称。

        Returns:
            改名后的学习者。

        Raises:
            DomainError: 新名称为空。
        """
        require_text("学习者名称", name)
        return replace(self, name=name)


@dataclass(frozen=True)
class KnowledgePoint:
    """知识点——**实体**，位于 ``KnowledgeStructure`` 内部。

    只承载「这一点是什么」：标识、名称、可选摘要。**前置与归属不在这里**——
    它们是结构里的关系，见 ``KnowledgeRelation``。
    """

    id: KnowledgePointId
    """知识点标识。"""

    title: str
    """知识点名称。"""

    summary: str = ""
    """一句话说明，供 prompt 与展示使用。"""

    def __post_init__(self) -> None:
        """校验名称与摘要。"""
        require_text("知识点名称", self.title)
        if self.summary:
            require_text("知识点摘要", self.summary)


@dataclass(frozen=True)
class KnowledgeRelation:
    """知识点之间的关系——**值对象**。

    没有 id：同一 ``(kind, source, target)`` 出现两次就是同一条关系，重复声明
    是建模错误而不是新事实。
    """

    kind: RelationKind
    """关系种类。"""

    source: KnowledgePointId
    """关系起点。"""

    target: KnowledgePointId
    """关系终点。"""

    def __post_init__(self) -> None:
        """拒绝自指。"""
        if self.source == self.target:
            raise DomainError(
                f"知识点 {self.source} 的 {self.kind} 关系不能指向自身",
            )


@dataclass(frozen=True)
class KnowledgeStructure:
    """知识结构——**值对象**：知识点集合 + 它们之间的关系。

    构造时完整校验，因此拿到手的结构一定自洽；所有查询都是派生视图，每次重算。

    ``requires`` 与 ``part_of`` 各自成 DAG：前者决定学习顺序，后者是归属树。
    两种关系不互相约束——一个点可以既依赖另一个点，又属于第三个主题。
    """

    points: tuple[KnowledgePoint, ...] = ()
    """知识点，按作者声明的顺序保存（决定并列时的稳定顺序）。"""

    relations: tuple[KnowledgeRelation, ...] = ()
    """关系，按声明顺序保存。"""

    def __post_init__(self) -> None:
        """校验结构一致性。

        Raises:
            DomainError: 知识点 id 重复、关系端点缺失、关系重复、``requires``
                成环、``part_of`` 一子多父或成环。
        """
        index: dict[KnowledgePointId, KnowledgePoint] = {}
        for point in self.points:
            if point.id in index:
                raise DomainError(f"知识点 id 重复：{point.id}")
            index[point.id] = point

        seen: set[tuple[RelationKind, KnowledgePointId, KnowledgePointId]] = set()
        for relation in self.relations:
            for endpoint in (relation.source, relation.target):
                if endpoint not in index:
                    raise DomainError(
                        f"{relation.kind} 关系引用了不存在的知识点：{endpoint}",
                    )
            key = (relation.kind, relation.source, relation.target)
            if key in seen:
                raise DomainError(
                    f"重复的关系：{relation.kind} {relation.source} -> {relation.target}",
                )
            seen.add(key)

        _topological_order(self.points, self._requires_edges())
        self._validate_hierarchy()

    def point(self, point_id: KnowledgePointId) -> KnowledgePoint:
        """按 id 取知识点。

        Args:
            point_id: 知识点标识。

        Returns:
            知识点。

        Raises:
            DomainError: 结构中不存在该知识点。
        """
        for point in self.points:
            if point.id == point_id:
                return point
        raise DomainError(f"知识结构中没有知识点 {point_id}")

    def order(self) -> tuple[KnowledgePoint, ...]:
        """按 ``requires`` 给出学习顺序。

        并列知识点保持声明顺序，因此结果确定。**这只是派生视图**：它服务「先学
        什么」的展示，不承担调度与历史——与计划模块的三序分离（ADR-0013）同一个
        原则。

        Returns:
            拓扑序的知识点。
        """
        return _topological_order(self.points, self._requires_edges())

    def prerequisites_of(self, point_id: KnowledgePointId) -> tuple[KnowledgePoint, ...]:
        """直接前置知识点，按声明顺序。

        Args:
            point_id: 知识点标识。

        Returns:
            直接前置；没有则为空元组。

        Raises:
            DomainError: 结构中不存在该知识点。
        """
        self.point(point_id)
        edges = self._requires_edges()
        index = {point.id: point for point in self.points}
        return tuple(index[item] for item in edges[point_id])

    def prerequisite_closure(
        self,
        point_id: KnowledgePointId,
    ) -> tuple[KnowledgePoint, ...]:
        """全部（传递）前置，按学习顺序返回，**不含自身**。

        这是薄弱点归因的原语：某一点没掌握时，它依赖的所有点都有嫌疑；返回顺序
        就是补课顺序。

        Args:
            point_id: 知识点标识。

        Returns:
            传递前置，按 ``order()`` 的顺序。

        Raises:
            DomainError: 结构中不存在该知识点。
        """
        self.point(point_id)
        return self._closure(point_id, self._requires_edges())

    def dependent_closure(self, point_id: KnowledgePointId) -> tuple[KnowledgePoint, ...]:
        """直接或间接依赖该点的所有知识点，按学习顺序返回，**不含自身**。

        用于影响分析：某点没掌握时，后续哪些点会受影响。

        Args:
            point_id: 知识点标识。

        Returns:
            传递后继，按 ``order()`` 的顺序。

        Raises:
            DomainError: 结构中不存在该知识点。
        """
        self.point(point_id)
        reverse: dict[KnowledgePointId, list[KnowledgePointId]] = {
            point.id: [] for point in self.points
        }
        for source, targets in self._requires_edges().items():
            for target in targets:
                reverse[target].append(source)
        return self._closure(point_id, reverse)

    def parent_of(self, point_id: KnowledgePointId) -> KnowledgePoint | None:
        """归属父节点（``part_of``），没有则为 ``None``。

        Args:
            point_id: 知识点标识。

        Returns:
            父节点或 ``None``。

        Raises:
            DomainError: 结构中不存在该知识点。
        """
        self.point(point_id)
        parent_id = self._parent_map().get(point_id)
        if parent_id is None:
            return None
        return self.point(parent_id)

    def children_of(self, point_id: KnowledgePointId) -> tuple[KnowledgePoint, ...]:
        """直接子节点（``part_of``），按声明顺序。

        Args:
            point_id: 知识点标识。

        Returns:
            直接子节点；没有则为空元组。

        Raises:
            DomainError: 结构中不存在该知识点。
        """
        self.point(point_id)
        parents = self._parent_map()
        return tuple(point for point in self.points if parents.get(point.id) == point_id)

    def entry_points(self) -> tuple[KnowledgePoint, ...]:
        """没有前置的知识点——学习的起点。

        Returns:
            起点知识点，按声明顺序。
        """
        edges = self._requires_edges()
        return tuple(point for point in self.points if not edges[point.id])

    def _requires_edges(self) -> dict[KnowledgePointId, tuple[KnowledgePointId, ...]]:
        """``requires`` 邻接表：知识点 → 它的直接前置，按声明顺序。"""
        index = {point.id: position for position, point in enumerate(self.points)}
        edges: dict[KnowledgePointId, list[KnowledgePointId]] = {
            point.id: [] for point in self.points
        }
        for relation in self.relations:
            if relation.kind == "requires":
                edges[relation.source].append(relation.target)
        return {
            source: tuple(sorted(targets, key=index.__getitem__))
            for source, targets in edges.items()
        }

    def _parent_map(self) -> dict[KnowledgePointId, KnowledgePointId]:
        """``part_of`` 单父映射：知识点 → 父节点。"""
        parents: dict[KnowledgePointId, KnowledgePointId] = {}
        for relation in self.relations:
            if relation.kind == "part_of":
                parents[relation.source] = relation.target
        return parents

    def _closure(
        self,
        point_id: KnowledgePointId,
        edges: Mapping[KnowledgePointId, Sequence[KnowledgePointId]],
    ) -> tuple[KnowledgePoint, ...]:
        """沿 ``edges`` 求传递闭包，并按学习顺序返回。"""
        collected: set[KnowledgePointId] = set()
        stack = list(edges[point_id])
        while stack:
            current = stack.pop()
            if current in collected:
                continue
            collected.add(current)
            stack.extend(edges[current])
        return tuple(point for point in self.order() if point.id in collected)

    def _validate_hierarchy(self) -> None:
        """校验 ``part_of``：一子至多一父，且层级无环。"""
        parents: dict[KnowledgePointId, KnowledgePointId] = {}
        for relation in self.relations:
            if relation.kind != "part_of":
                continue
            existing = parents.get(relation.source)
            if existing is not None:
                raise DomainError(
                    f"知识点 {relation.source} 有多个 part_of 父节点："
                    f"{existing}、{relation.target}",
                )
            parents[relation.source] = relation.target

        for point in self.points:
            path: list[KnowledgePointId] = []
            seen: dict[KnowledgePointId, int] = {}
            current = point.id
            while current in parents:
                if current in seen:
                    cycle = [*path[seen[current] :], current]
                    rendered = " -> ".join(str(item) for item in cycle)
                    raise DomainError(f"part_of 层级成环：{rendered}")
                seen[current] = len(path)
                path.append(current)
                current = parents[current]


@dataclass(frozen=True)
class Course:
    """课程——**实体**，也是知识结构的聚合根。

    课程只负责「是哪门课」与「包含什么结构」；学习顺序、前置闭包、主题层级
    都由 ``structure`` 回答。
    """

    id: CourseId
    """课程标识。"""

    title: str
    """课程名称。"""

    structure: KnowledgeStructure = field(default_factory=KnowledgeStructure)
    """课程的知识结构。"""

    def __post_init__(self) -> None:
        """校验课程名称。

        Raises:
            DomainError: 名称为空或含首尾空白。
        """
        require_text("课程名称", self.title)


def _topological_order(
    points: Sequence[KnowledgePoint],
    edges: Mapping[KnowledgePointId, Sequence[KnowledgePointId]],
) -> tuple[KnowledgePoint, ...]:
    """按依赖给出稳定的拓扑序；成环时抛 ``DomainError``。"""
    remaining = list(points)
    ordered: list[KnowledgePoint] = []
    finished: set[KnowledgePointId] = set()
    while remaining:
        ready = [
            point
            for point in remaining
            if all(required in finished for required in edges[point.id])
        ]
        if not ready:
            raise DomainError(f"requires 依赖成环：{_describe_cycle(points, edges)}")
        ordered.extend(ready)
        finished.update(point.id for point in ready)
        remaining = [point for point in remaining if point.id not in finished]
    return tuple(ordered)


def _describe_cycle(
    points: Sequence[KnowledgePoint],
    edges: Mapping[KnowledgePointId, Sequence[KnowledgePointId]],
) -> str:
    """从无法排序的知识点里找出一条环，用于错误信息。"""
    known = {point.id for point in points}
    path: list[KnowledgePointId] = []
    on_path: set[KnowledgePointId] = set()

    def walk(point_id: KnowledgePointId) -> list[KnowledgePointId] | None:
        path.append(point_id)
        on_path.add(point_id)
        for required in edges[point_id]:
            if required not in known:
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

    for point in points:
        cycle = walk(point.id)
        if cycle is not None:
            return " -> ".join(str(item) for item in cycle)
        path.clear()
        on_path.clear()
    return "未知"


__all__ = [
    "Course",
    "CourseId",
    "DiagnosisId",
    "DomainError",
    "GoalId",
    "KnowledgePoint",
    "KnowledgePointId",
    "KnowledgeRelation",
    "KnowledgeStructure",
    "Learner",
    "LearnerId",
    "RelationKind",
    "require_text",
]
