"""领域模型到 Agent 运行时的单向桥接。

依赖方向只有一条：``core.learning`` → ``core.agent``。

运行时需要一个领域无关的目标描述 ``GoalSpec``（ADR-0013 §10）。把映射放在
这里而不是 ``domain.py``，是为了让领域模型本身保持干净：它可以被单独测试、
序列化、持久化，而不需要拉起 LLM 与工具那一层。

反方向的依赖（``core.agent`` 认识 ``LearningGoal``）**不允许**——那会让运行时
绑死在学习业务上，Phase 2 之后换场景就得改运行时。
"""

from __future__ import annotations

from agent_learning_coach.core.agent.planning import GoalSpec
from agent_learning_coach.core.learning.domain import LearningGoal


def to_goal_spec(goal: LearningGoal) -> GoalSpec:
    """把学习目标映射成运行时认识的目标描述。

    课程绑定（``course_id``）不进入 ``GoalSpec``：规划器只关心「要达成什么、
    怎么验证、有什么约束」；目标属于哪门课是领域侧的事实，由应用层在组装
    prompt 与持久化时使用。

    Args:
        goal: 学习目标领域模型。

    Returns:
        领域无关的目标描述。
    """
    return GoalSpec(
        goal_id=goal.id.value,
        statement=goal.statement,
        outcomes=goal.outcomes,
        constraints=tuple(constraint.render() for constraint in goal.constraints),
    )
