"""领域模型到 Agent 运行时的单向桥接。

依赖方向只有一条：``core.learning`` → ``core.agent``。

运行时需要一个领域无关的目标描述 ``GoalSpec``（ADR-0013 §10）。把映射放在
这里而不是 ``goal.py``，是为了让领域模型本身保持干净：它可以被单独测试、
序列化、持久化，而不需要拉起 LLM 与工具那一层。

反方向的依赖（``core.agent`` 认识 ``LearningGoal``）**不允许**——那会让运行时
绑死在学习业务上，Phase 2 之后换场景就得改运行时。

映射规则（ADR-0017）：

- ``statement`` → ``statement``。
- ``outcomes`` → ``outcomes``。能力要求**不另立结果**——它只说明「这条结果由
  哪个知识点支撑」，因此不额外进入 ``outcomes``（否则规划器会拆出重复任务）。
- 时间范围、每日投入、前置知识点 → ``constraints``，渲染成文本。它们是约束，
  不是结果，因此不进 ``outcomes``。
- 课程绑定（``course_id``）**不进入** ``GoalSpec``：规划器只关心「要达成什么、
  怎么验证、有什么约束」；目标属于哪门课是领域侧的事实，由应用层在组装
  prompt 与持久化时使用。

**能力要求与知识点的对应关系不进入 ``GoalSpec``**：``GoalSpec`` 是领域无关的
运行时契约（ADR-0013 §10），把知识点塞进去会让运行时开始认识学习领域的概念。
「计划任务粒度如何与知识点对齐」是 Topic 23（Learning Planning）要回答的问题，
那里再决定运行时契约是否需要扩展。
"""

from __future__ import annotations

from agent_learning_coach.core.agent.planning import GoalSpec
from agent_learning_coach.core.learning.goal import LearningGoal


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
    constraints: list[str] = []
    if goal.time_window is not None:
        constraints.append(f"时间范围：{goal.time_window.render()}")
    if goal.daily_effort is not None:
        constraints.append(f"每日投入：{goal.daily_effort.render()}")
    if goal.prerequisites:
        rendered = "、".join(str(point_id) for point_id in goal.prerequisites)
        constraints.append(f"前置知识点：{rendered}")

    return GoalSpec(
        goal_id=goal.id.value,
        statement=goal.statement,
        outcomes=goal.outcomes,
        constraints=tuple(constraints),
    )
