"""内存参考实现——仓储协议的一个可用实现。

**它不是「假的」**：它就是存储的一个合法实现（进程内、不持久）。它的价值有三：

1. **协议可被执行**：协议本身不能证明任何事，一个真实实现能证明「这个协议是可实现
   的」，而且实现里没有一行数据库代码。
2. **写入路径复用领域层不变量**：这里存的是**负载字典**，读回时用
   ``codecs.*_from_dict`` 构造领域对象——构造期的校验全部生效。因此一份被篡改的
   负载在**读回时**就被拒绝，而不是等到展示层。
3. **乐观锁是真的**：两个 ``Stored`` 基于同一个版本读出又先后写入时，第二个会被
   ``ConflictError`` 挡住。冲突不是模拟出来的。

**它不是生产存储**：进程一停就没了。但**测试完全够用**——测试因此不需要数据库，
离线、确定、快。PostgreSQL 适配器是同一套协议的另一个实现（Day 25 只交付 schema，
适配器留给第一个真正需要它的主题）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_learning_coach.core.learning.diagnosis import Diagnosis, DiagnosticExercise
from agent_learning_coach.core.learning.domain import (
    Course,
    CourseId,
    DiagnosisId,
    GoalId,
    KnowledgePointId,
    LearnerId,
)
from agent_learning_coach.core.learning.goal import LearningGoal
from agent_learning_coach.core.learning.progress import LearningProgress
from agent_learning_coach.core.learning.versions import PlanVersion
from agent_learning_coach.core.storage import codecs
from agent_learning_coach.core.storage.protocols import Stored, ensure_writable


@dataclass
class _Row:
    """存储里的一行：版本号 + 负载。"""

    revision: int
    """当前版本号。"""

    payload: dict[str, Any]
    """负载字典（领域对象编码后的形状）。"""


class _InMemoryRepository[T]:
    """内存仓储的公共骨架。

    六个仓储的差别只有四件事：**主键怎么算**、**负载怎么编码/解码**、**索引按什么
    分组**。其余（乐观锁、版本递增、列表排序）都一样——放在一处，避免六份漂移。
    """

    def __init__(self) -> None:
        """初始化空的存储。"""
        self._rows: dict[str, _Row] = {}
        self._index: dict[str, set[str]] = {}

    # --- 子类要回答的四个问题 -----------------------------------------

    def _key(self, value: T) -> str:
        """主键。

        Args:
            value: 领域对象。

        Returns:
            主键字符串。
        """
        raise NotImplementedError

    def _encode(self, value: T) -> dict[str, Any]:
        """编码成负载。

        Args:
            value: 领域对象。

        Returns:
            负载字典。
        """
        raise NotImplementedError

    def _decode(self, payload: dict[str, Any]) -> T:
        """解码成领域对象（**构造期校验在这里生效**）。

        Args:
            payload: 负载字典。

        Returns:
            领域对象。
        """
        raise NotImplementedError

    def _group_keys(self, value: T) -> tuple[str, ...]:
        """这个对象归属哪些分组（用于列表查询的索引）。

        Args:
            value: 领域对象。

        Returns:
            分组键元组；空表示不参与任何索引。
        """
        return ()

    # --- 公共行为 -------------------------------------------------------

    def _get(self, key: str) -> Stored[T] | None:
        """按主键读取。

        Args:
            key: 主键。

        Returns:
            记录；不存在时为 ``None``。
        """
        row = self._rows.get(key)
        if row is None:
            return None
        return Stored(value=self._decode(row.payload), revision=row.revision)

    def _put(self, key: str, record: Stored[T], groups: tuple[str, ...]) -> Stored[T]:
        """写入（新建或整体替换）。

        Args:
            key: 主键。
            record: 待写入的记录。
            groups: 归属的分组。

        Returns:
            写入后的记录。

        Raises:
            ConflictError: 版本冲突。
        """
        current = self._rows.get(key)
        ensure_writable(record.revision, None if current is None else current.revision)
        revision = 1 if current is None else current.revision + 1
        payload = self._encode(record.value)
        self._rows[key] = _Row(revision=revision, payload=payload)
        for group in groups:
            self._index.setdefault(group, set()).add(key)
        return Stored(value=self._decode(payload), revision=revision)

    def _list_group(self, group: str) -> tuple[Stored[T], ...]:
        """列出某个分组里的记录。

        Args:
            group: 分组键。

        Returns:
            记录元组，按主键升序。
        """
        keys = sorted(self._index.get(group, set()))
        records = [self._get(key) for key in keys]
        return tuple(record for record in records if record is not None)


class InMemoryGoalRepository(_InMemoryRepository[LearningGoal]):
    """学习目标的内存仓储。"""

    def _key(self, value: LearningGoal) -> str:
        """主键是目标 id。"""
        return str(value.id)

    def _encode(self, value: LearningGoal) -> dict[str, Any]:
        """编码成目标负载。"""
        return codecs.goal_to_dict(value)

    def _decode(self, payload: dict[str, Any]) -> LearningGoal:
        """解码成目标。"""
        return codecs.goal_from_dict(payload)

    def _group_keys(self, value: LearningGoal) -> tuple[str, ...]:
        """按学习者分组。"""
        return (str(value.learner_id),)

    async def load(self, goal_id: GoalId) -> Stored[LearningGoal] | None:
        """按 id 读取目标。

        Args:
            goal_id: 目标标识。

        Returns:
            记录；不存在时为 ``None``。
        """
        return self._get(str(goal_id))

    async def save(self, record: Stored[LearningGoal]) -> Stored[LearningGoal]:
        """写入目标。

        Args:
            record: 待写入的记录。

        Returns:
            写入后的记录。

        Raises:
            ConflictError: 版本冲突。
        """
        return self._put(self._key(record.value), record, self._group_keys(record.value))

    async def list_for_learner(self, learner_id: LearnerId) -> tuple[Stored[LearningGoal], ...]:
        """列出某个学习者的全部目标。

        Args:
            learner_id: 学习者标识。

        Returns:
            记录元组，按目标 id 升序。
        """
        return self._list_group(str(learner_id))


class InMemoryProgressRepository(_InMemoryRepository[LearningProgress]):
    """学习进度的内存仓储。"""

    def _key(self, value: LearningProgress) -> str:
        """主键是（学习者, 课程）这一对（ADR-0018）。"""
        return f"{value.learner_id}:{value.course_id}"

    def _encode(self, value: LearningProgress) -> dict[str, Any]:
        """编码成进度负载。"""
        return codecs.progress_to_dict(value)

    def _decode(self, payload: dict[str, Any]) -> LearningProgress:
        """解码成进度。"""
        return codecs.progress_from_dict(payload)

    async def load(
        self,
        learner_id: LearnerId,
        course_id: CourseId,
    ) -> Stored[LearningProgress] | None:
        """读取某学习者在某门课上的进度。

        Args:
            learner_id: 学习者标识。
            course_id: 课程标识。

        Returns:
            记录；不存在时为 ``None``。
        """
        return self._get(f"{learner_id}:{course_id}")

    async def save(self, record: Stored[LearningProgress]) -> Stored[LearningProgress]:
        """写入进度。

        Args:
            record: 待写入的记录。

        Returns:
            写入后的记录。

        Raises:
            ConflictError: 版本冲突。
        """
        return self._put(self._key(record.value), record, ())


class InMemoryDiagnosisRepository(_InMemoryRepository[Diagnosis]):
    """诊断的内存仓储。"""

    def _key(self, value: Diagnosis) -> str:
        """主键是诊断 id。"""
        return str(value.id)

    def _encode(self, value: Diagnosis) -> dict[str, Any]:
        """编码成诊断负载。"""
        return codecs.diagnosis_to_dict(value)

    def _decode(self, payload: dict[str, Any]) -> Diagnosis:
        """解码成诊断。"""
        return codecs.diagnosis_from_dict(payload)

    def _group_keys(self, value: Diagnosis) -> tuple[str, ...]:
        """按目标分组。"""
        return (str(value.goal_id),)

    async def load(self, diagnosis_id: DiagnosisId) -> Stored[Diagnosis] | None:
        """按 id 读取诊断。

        Args:
            diagnosis_id: 诊断标识。

        Returns:
            记录；不存在时为 ``None``。
        """
        return self._get(str(diagnosis_id))

    async def save(self, record: Stored[Diagnosis]) -> Stored[Diagnosis]:
        """写入诊断。

        Args:
            record: 待写入的记录。

        Returns:
            写入后的记录。

        Raises:
            ConflictError: 版本冲突。
        """
        return self._put(self._key(record.value), record, self._group_keys(record.value))

    async def list_for_goal(self, goal_id: GoalId) -> tuple[Stored[Diagnosis], ...]:
        """列出某个目标的全部诊断。

        Args:
            goal_id: 目标标识。

        Returns:
            记录元组，按诊断 id 升序。
        """
        return self._list_group(str(goal_id))


class InMemoryPlanVersionRepository(_InMemoryRepository[PlanVersion]):
    """计划版本的内存仓储。"""

    def _key(self, value: PlanVersion) -> str:
        """主键是（计划线, 版本号）。"""
        return f"{value.plan_id}:{value.number}"

    def _encode(self, value: PlanVersion) -> dict[str, Any]:
        """编码成版本负载。"""
        return codecs.plan_version_to_dict(value)

    def _decode(self, payload: dict[str, Any]) -> PlanVersion:
        """解码成版本。"""
        return codecs.plan_version_from_dict(payload)

    def _group_keys(self, value: PlanVersion) -> tuple[str, ...]:
        """按计划线与目标分组。"""
        return (str(value.plan_id), f"goal:{value.plan.goal_id}")

    async def load(self, plan_id: str, number: int) -> Stored[PlanVersion] | None:
        """按计划线与版本号读取。

        Args:
            plan_id: 计划线标识。
            number: 版本号。

        Returns:
            记录；不存在时为 ``None``。
        """
        return self._get(f"{plan_id}:{number}")

    async def save(self, record: Stored[PlanVersion]) -> Stored[PlanVersion]:
        """写入一个新版本。

        Args:
            record: 待写入的记录。

        Returns:
            写入后的记录。

        Raises:
            ConflictError: 同版本号已存在。
        """
        return self._put(self._key(record.value), record, self._group_keys(record.value))

    async def list_for_plan(self, plan_id: str) -> tuple[Stored[PlanVersion], ...]:
        """列出某条计划线的全部版本。

        Args:
            plan_id: 计划线标识。

        Returns:
            记录元组，按版本号升序。
        """
        records = self._list_group(str(plan_id))
        return tuple(sorted(records, key=lambda record: record.value.number))

    async def list_for_goal(self, goal_id: GoalId) -> tuple[Stored[PlanVersion], ...]:
        """列出某个目标的全部计划版本。

        Args:
            goal_id: 目标标识。

        Returns:
            记录元组，按（计划线, 版本号）升序。
        """
        records = self._list_group(f"goal:{goal_id}")
        return tuple(
            sorted(records, key=lambda record: (record.value.plan_id, record.value.number))
        )


class InMemoryCourseRepository(_InMemoryRepository[Course]):
    """课程的内存仓储。"""

    def _key(self, value: Course) -> str:
        """主键是课程 id。"""
        return str(value.id)

    def _encode(self, value: Course) -> dict[str, Any]:
        """编码成课程负载。"""
        return codecs.course_to_dict(value)

    def _decode(self, payload: dict[str, Any]) -> Course:
        """解码成课程。"""
        return codecs.course_from_dict(payload)

    async def load(self, course_id: CourseId) -> Stored[Course] | None:
        """按 id 读取课程。

        Args:
            course_id: 课程标识。

        Returns:
            记录；不存在时为 ``None``。
        """
        return self._get(str(course_id))

    async def save(self, record: Stored[Course]) -> Stored[Course]:
        """写入课程。

        Args:
            record: 待写入的记录。

        Returns:
            写入后的记录。

        Raises:
            ConflictError: 版本冲突。
        """
        return self._put(self._key(record.value), record, ())


class InMemoryExerciseRepository(_InMemoryRepository[DiagnosticExercise]):
    """诊断题的内存仓储。"""

    def _key(self, value: DiagnosticExercise) -> str:
        """主键是题目标识。"""
        return value.id

    def _encode(self, value: DiagnosticExercise) -> dict[str, Any]:
        """编码成题目负载。"""
        return codecs.exercise_to_dict(value)

    def _decode(self, payload: dict[str, Any]) -> DiagnosticExercise:
        """解码成题目。"""
        return codecs.exercise_from_dict(payload)

    def _group_keys(self, value: DiagnosticExercise) -> tuple[str, ...]:
        """按知识点分组；映射缺失的题目不参与索引。"""
        if value.point_id is None:
            return ()
        return (str(value.point_id),)

    async def load(self, exercise_id: str) -> Stored[DiagnosticExercise] | None:
        """按 id 读取题目。

        Args:
            exercise_id: 题目标识。

        Returns:
            记录；不存在时为 ``None``。
        """
        return self._get(exercise_id)

    async def save(self, record: Stored[DiagnosticExercise]) -> Stored[DiagnosticExercise]:
        """写入题目。

        Args:
            record: 待写入的记录。

        Returns:
            写入后的记录。

        Raises:
            ConflictError: 同标识的题目已存在。
        """
        return self._put(self._key(record.value), record, self._group_keys(record.value))

    async def list_for_point(
        self,
        point_id: KnowledgePointId,
    ) -> tuple[Stored[DiagnosticExercise], ...]:
        """列出某个知识点的全部题目。

        Args:
            point_id: 知识点标识。

        Returns:
            记录元组，按题目标识升序。
        """
        return self._list_group(str(point_id))


__all__ = [
    "InMemoryCourseRepository",
    "InMemoryDiagnosisRepository",
    "InMemoryExerciseRepository",
    "InMemoryGoalRepository",
    "InMemoryPlanVersionRepository",
    "InMemoryProgressRepository",
]
