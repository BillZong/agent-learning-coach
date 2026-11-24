"""学习领域（Learning Domain）。

Phase 1 的业务核心：学习者、课程与知识点、学习目标。

- **实体**（身份来自 id）：``Learner``、``Course``、``KnowledgePoint``、
  ``LearningGoal``。
- **值对象**（按值相等）：四类 id、``GoalConstraint``、``KnowledgeStructure``、
  ``KnowledgeRelation``。
- **聚合边界**：``Course`` 拥有知识结构（知识点 + 关系）；``LearningGoal`` 用
  id 引用学习者与课程，是三者之间的关联，而不是任何一方的子对象。
- **知识表示**：``KnowledgeStructure`` 提供派生查询——学习顺序 ``order()``、
  前置闭包 ``prerequisite_closure()``、影响范围 ``dependent_closure()``、
  主题层级 ``parent_of()`` / ``children_of()``。
- **不变量**：知识点 id 唯一、关系端点存在且不重复、``requires`` 无环、
  ``part_of`` 是树、目标至少一条学习结果、终态不可回退。违反时抛
  ``DomainError``。

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
    KnowledgeRelation,
    KnowledgeStructure,
    Learner,
    LearnerId,
    LearningGoal,
    RelationKind,
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
    "KnowledgeRelation",
    "KnowledgeStructure",
    "Learner",
    "LearnerId",
    "LearningGoal",
    "RelationKind",
    "to_goal_spec",
]
