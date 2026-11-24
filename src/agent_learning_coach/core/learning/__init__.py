"""学习领域（Learning Domain）。

Phase 1 的业务核心：学习者、课程与知识点、学习目标。

- **实体**（身份来自 id）：``Learner``、``Course``、``KnowledgePoint``、
  ``LearningGoal``。
- **值对象**（按值相等）：``LearnerId`` / ``CourseId`` / ``GoalId`` /
  ``KnowledgePointId``、``GoalConstraint``。
- **聚合边界**：``Course`` 拥有知识点；``LearningGoal`` 用 id 引用学习者与
  课程，是三者之间的关联，而不是任何一方的子对象。
- **不变量**：知识点 id 唯一、前置存在且无环、目标至少一条学习结果、终态
  不可回退。违反时抛 ``DomainError``。

它与 Agent 运行时的关系是单向的：``to_goal_spec`` 把 ``LearningGoal`` 映射成
``GoalSpec``，运行时因此不需要认识学习领域（ADR-0013 §10、ADR-0015）。

导入约定：调用方统一从 ``agent_learning_coach.core.learning`` 导入公开符号。
"""

from agent_learning_coach.core.learning.bridge import to_goal_spec
from agent_learning_coach.core.learning.domain import (
    ConstraintKind,
    Course,
    CourseId,
    DomainError,
    GoalConstraint,
    GoalId,
    GoalStatus,
    KnowledgePoint,
    KnowledgePointId,
    Learner,
    LearnerId,
    LearningGoal,
)

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
    "to_goal_spec",
]
