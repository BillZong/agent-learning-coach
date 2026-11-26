"""仓储协议（port）与乐观锁规则。

**领域层不碰存储**：学习领域层（``core.learning``）保持纯函数与不可变对象，不认
识数据库、文件或网络。存储相关的三件事——**形状**（``codecs``）、**协议**
（本模块）、**实现**（``memory`` 与未来的 PostgreSQL 适配器）——都放在
``core.storage``。

**为什么端口也放在存储包里**：本项目的惯例是「按能力分包」，不是按层分包——
``core.llm`` 里有 ``LLMClient`` 协议与它的各厂商适配器，``core.memory`` 里有
``MemoryStore`` 协议与它的文件实现（ADR-0010）。协议与实现放在同一个能力包里，
读代码的人不必在两个目录之间跳。

**乐观锁**：领域对象不可变，「更新」是**整体替换**（ADR-0015）。两个进程同时
读-改-写同一个目标时，后写的会把先写的覆盖掉，而且**没有人会发现**——目标的时间
范围、每日投入、绑定关系可能悄悄回退。

因此每次存储都带一个 ``revision``：

- ``revision == 0`` 表示**还没有存过**（新建）；
- 保存时，携带的 ``revision`` 必须等于存储里当前的 ``revision``，否则抛
  ``ConflictError``；
- 保存成功后 ``revision`` 加一。

**冲突不是错误，是事实**：调用方拿到冲突后应当重新读取、重新合并、再次写入——
这属于应用层的用例编排（Day 26）。存储层只负责**发现**冲突，不负责解决它。

**规则写在这里，不写在各个实现里**：内存实现与 PostgreSQL 适配器必须对同一次
并发给出同样的答案。两份实现各写一遍，迟早会漂移。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agent_learning_coach.core.learning.diagnosis import Diagnosis, DiagnosticExercise
from agent_learning_coach.core.learning.documents import CourseDocument
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
from agent_learning_coach.core.storage.errors import ConflictError


@dataclass(frozen=True)
class Stored[T]:
    """一条被存储的记录——**值对象**。

    它把**领域对象**与**存储版本**绑在一起：领域对象不知道自己被存过第几版，
    存储版本也不是领域事实（它描述的是存储，不是业务）。分开存就得在每个调用点
    手动配对两者——迟早配错。

    ``revision`` 为 ``0`` 表示**尚未存储**——它是「新建」的信号。
    """

    value: T
    """领域对象。"""

    revision: int = 0
    """存储版本；``0`` 表示尚未存储。"""

    def __post_init__(self) -> None:
        """校验版本号。

        Raises:
            ValueError: 版本号为负。
        """
        if self.revision < 0:
            raise ValueError(f"存储版本号不能为负：{self.revision}")


def ensure_writable(expected: int, current: int | None) -> None:
    """乐观锁检查——**所有存储实现共用同一条规则**。

    Args:
        expected: 调用方携带的版本号（``0`` 表示新建）。
        current: 存储里当前的版本号；``None`` 表示还没有这条记录。

    Raises:
        ConflictError: 版本对不上——要么记录已被别人改动，要么它已经被删了。
    """
    if current is None:
        if expected != 0:
            raise ConflictError(f"记录不存在，无法基于版本 {expected} 写入")
        return
    if expected != current:
        raise ConflictError(f"存储中的版本是 {current}，与携带的版本 {expected} 不一致")


@runtime_checkable
class GoalRepository(Protocol):
    """学习目标的仓储。"""

    async def load(self, goal_id: GoalId) -> Stored[LearningGoal] | None:
        """按 id 读取目标。

        Args:
            goal_id: 目标标识。

        Returns:
            记录；不存在时为 ``None``。
        """
        ...

    async def save(self, record: Stored[LearningGoal]) -> Stored[LearningGoal]:
        """写入目标（新建或整体替换）。

        Args:
            record: 待写入的记录；``revision`` 为 ``0`` 表示新建。

        Returns:
            写入后的记录（``revision`` 已加一）。

        Raises:
            ConflictError: 版本冲突。
        """
        ...

    async def list_for_learner(self, learner_id: LearnerId) -> tuple[Stored[LearningGoal], ...]:
        """列出某个学习者的全部目标。

        Args:
            learner_id: 学习者标识。

        Returns:
            记录元组，按 id 升序。
        """
        ...


@runtime_checkable
class ProgressRepository(Protocol):
    """学习进度的仓储。

    身份是 ``(learner_id, course_id)`` 这一对（ADR-0018），因此读写都用这一对。
    """

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
        ...

    async def save(self, record: Stored[LearningProgress]) -> Stored[LearningProgress]:
        """写入进度（新建或整体替换）。

        Args:
            record: 待写入的记录。

        Returns:
            写入后的记录。

        Raises:
            ConflictError: 版本冲突。
        """
        ...


@runtime_checkable
class DiagnosisRepository(Protocol):
    """诊断的仓储。

    诊断是**一次活动**，有独立 id；同一个目标可以有多条诊断记录（ADR-0020）。
    """

    async def load(self, diagnosis_id: DiagnosisId) -> Stored[Diagnosis] | None:
        """按 id 读取诊断。

        Args:
            diagnosis_id: 诊断标识。

        Returns:
            记录；不存在时为 ``None``。
        """
        ...

    async def save(self, record: Stored[Diagnosis]) -> Stored[Diagnosis]:
        """写入诊断（新建或整体替换）。

        Args:
            record: 待写入的记录。

        Returns:
            写入后的记录。

        Raises:
            ConflictError: 版本冲突。
        """
        ...

    async def list_for_goal(self, goal_id: GoalId) -> tuple[Stored[Diagnosis], ...]:
        """列出某个目标的全部诊断。

        Args:
            goal_id: 目标标识。

        Returns:
            记录元组，按 id 升序。
        """
        ...


@runtime_checkable
class PlanVersionRepository(Protocol):
    """计划版本的仓储。

    版本是**只增不改**的：修订产生新版本，旧版本永远保留（ADR-0025）。因此这里
    只有 ``save``（新增）与 ``list``（读历史），没有「覆盖」。
    """

    async def load(self, plan_id: str, number: int) -> Stored[PlanVersion] | None:
        """按计划线与版本号读取。

        Args:
            plan_id: 计划线标识。
            number: 版本号。

        Returns:
            记录；不存在时为 ``None``。
        """
        ...

    async def save(self, record: Stored[PlanVersion]) -> Stored[PlanVersion]:
        """写入一个新版本。

        Args:
            record: 待写入的记录。

        Returns:
            写入后的记录。

        Raises:
            ConflictError: 版本冲突（同一版本号已存在）。
        """
        ...

    async def list_for_plan(self, plan_id: str) -> tuple[Stored[PlanVersion], ...]:
        """列出某条计划线的全部版本。

        Args:
            plan_id: 计划线标识。

        Returns:
            记录元组，按版本号升序。
        """
        ...

    async def list_for_goal(self, goal_id: GoalId) -> tuple[Stored[PlanVersion], ...]:
        """列出某个目标的全部计划版本。

        「当前计划线是哪一条」要靠它回答——版本散在多条计划线上时，得先找出
        最近开始的那条（ADR-0025）。

        Args:
            goal_id: 目标标识。

        Returns:
            记录元组，按版本号升序。
        """
        ...


@runtime_checkable
class CourseRepository(Protocol):
    """课程的仓储（含知识结构）。"""

    async def load(self, course_id: CourseId) -> Stored[Course] | None:
        """按 id 读取课程。

        Args:
            course_id: 课程标识。

        Returns:
            记录；不存在时为 ``None``。
        """
        ...

    async def save(self, record: Stored[Course]) -> Stored[Course]:
        """写入课程（新建或整体替换）。

        Args:
            record: 待写入的记录。

        Returns:
            写入后的记录。

        Raises:
            ConflictError: 版本冲突。
        """
        ...


@runtime_checkable
class ExerciseRepository(Protocol):
    """诊断题的仓储。

    题目是**只增不改**的：生成一次就固定下来（ADR-0021「不合格题目不外流」）。
    """

    async def load(self, exercise_id: str) -> Stored[DiagnosticExercise] | None:
        """按 id 读取题目。

        Args:
            exercise_id: 题目标识。

        Returns:
            记录；不存在时为 ``None``。
        """
        ...

    async def save(self, record: Stored[DiagnosticExercise]) -> Stored[DiagnosticExercise]:
        """写入题目。

        Args:
            record: 待写入的记录。

        Returns:
            写入后的记录。

        Raises:
            ConflictError: 同标识的题目已存在。
        """
        ...

    async def list_for_point(
        self,
        point_id: KnowledgePointId,
    ) -> tuple[Stored[DiagnosticExercise], ...]:
        """列出某个知识点的全部题目。

        Args:
            point_id: 知识点标识。

        Returns:
            记录元组，按 id 升序。
        """
        ...


@runtime_checkable
class CourseDocumentRepository(Protocol):
    """课程文档的仓储。

    文档身份由**来源**推导（``core.learning.documents.document_id``），因此
    重复导入同一份材料是**覆盖**而不是追加——「换了教材重新导入一遍」是安全的
    日常操作。
    """

    async def load(self, document_id: str) -> Stored[CourseDocument] | None:
        """按 id 读取文档。

        Args:
            document_id: 文档标识。

        Returns:
            记录；不存在时为 ``None``。
        """
        ...

    async def save(self, record: Stored[CourseDocument]) -> Stored[CourseDocument]:
        """写入文档（新建或整体替换）。

        Args:
            record: 待写入的记录。

        Returns:
            写入后的记录。

        Raises:
            ConflictError: 版本冲突。
        """
        ...

    async def list_for_course(self, course_id: CourseId) -> tuple[Stored[CourseDocument], ...]:
        """列出某门课的全部文档。

        Args:
            course_id: 课程标识。

        Returns:
            记录元组，按文档标识升序。
        """
        ...


__all__ = [
    "ConflictError",
    "CourseDocumentRepository",
    "CourseRepository",
    "DiagnosisRepository",
    "ExerciseRepository",
    "GoalRepository",
    "PlanVersionRepository",
    "ProgressRepository",
    "Stored",
    "ensure_writable",
]
