"""存储层：领域模型的持久化基线。

**领域层不碰存储**：学习领域层（``core.learning``）保持纯函数与不可变对象，不
识别数据库、文件或网络。这里放三样东西：

- **形状**（``codecs``）：领域对象 ↔ JSON 兼容的负载字典，带 ``schema_version``。
  解码会重跑领域层的不变量——从存储读回一份坏负载，会在读回时就被拒绝。
- **协议**（``protocols``）：仓储的端口，以及**乐观锁规则**（``ensure_writable``）。
  领域对象不可变，「更新」是整体替换；没有版本号，两个并发写会静默互相覆盖。
- **参考实现**（``memory``）：进程内的合法实现。测试因此不需要数据库——离线、
  确定、快。

PostgreSQL 的 schema 在 ``migrations/0001_learning_domain.sql``；适配器尚未实现
（驱动选型未定）。**DDL 是契约**：适配器必须与内存实现对同一次并发给出同样的
答案。

导入约定：调用方统一从 ``agent_learning_coach.core.storage`` 导入公开符号。
"""

from agent_learning_coach.core.storage.codecs import (
    SCHEMA_VERSION,
    course_document_from_dict,
    course_document_to_dict,
    course_from_dict,
    course_to_dict,
    diagnosis_from_dict,
    diagnosis_to_dict,
    exercise_from_dict,
    exercise_to_dict,
    goal_from_dict,
    goal_to_dict,
    learner_from_dict,
    learner_to_dict,
    plan_version_from_dict,
    plan_version_to_dict,
    progress_from_dict,
    progress_to_dict,
)
from agent_learning_coach.core.storage.errors import (
    ConflictError,
    DecodeError,
    StorageError,
)
from agent_learning_coach.core.storage.memory import (
    InMemoryCourseDocumentRepository,
    InMemoryCourseRepository,
    InMemoryDiagnosisRepository,
    InMemoryExerciseRepository,
    InMemoryGoalRepository,
    InMemoryPlanVersionRepository,
    InMemoryProgressRepository,
)
from agent_learning_coach.core.storage.protocols import (
    CourseDocumentRepository,
    CourseRepository,
    DiagnosisRepository,
    ExerciseRepository,
    GoalRepository,
    PlanVersionRepository,
    ProgressRepository,
    Stored,
    ensure_writable,
)

__all__ = [
    "SCHEMA_VERSION",
    "ConflictError",
    "CourseDocumentRepository",
    "CourseRepository",
    "DecodeError",
    "DiagnosisRepository",
    "ExerciseRepository",
    "GoalRepository",
    "InMemoryCourseDocumentRepository",
    "InMemoryCourseRepository",
    "InMemoryDiagnosisRepository",
    "InMemoryExerciseRepository",
    "InMemoryGoalRepository",
    "InMemoryPlanVersionRepository",
    "InMemoryProgressRepository",
    "PlanVersionRepository",
    "ProgressRepository",
    "StorageError",
    "Stored",
    "course_document_from_dict",
    "course_document_to_dict",
    "course_from_dict",
    "course_to_dict",
    "diagnosis_from_dict",
    "diagnosis_to_dict",
    "ensure_writable",
    "exercise_from_dict",
    "exercise_to_dict",
    "goal_from_dict",
    "goal_to_dict",
    "learner_from_dict",
    "learner_to_dict",
    "plan_version_from_dict",
    "plan_version_to_dict",
    "progress_from_dict",
    "progress_to_dict",
]
