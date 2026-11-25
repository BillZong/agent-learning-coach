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

Topic 23 回答了「计划任务粒度如何与知识点对齐」：**学习计划的单元粒度是知识点，
但运行时契约不需要扩展**。领域侧把当前要学的单元映射成 ``GoalSpec``
（``to_unit_goal_spec``），知识点 id 仍然不越过桥接。
"""

from __future__ import annotations

from agent_learning_coach.core.agent.planning import GoalSpec
from agent_learning_coach.core.learning.goal import LearningGoal
from agent_learning_coach.core.learning.planning import LearningUnit


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


def to_unit_goal_spec(unit: LearningUnit, goal: LearningGoal) -> GoalSpec:
    """把一个**学习单元**映射成运行时认识的目标描述。

    这是 Topic 23 对「计划任务粒度如何与知识点对齐」的回答：**对齐到知识点，
    但运行时契约不需要扩展**。

    - 学习计划（领域）的单元粒度是**知识点**——掌握度、判定、依赖都以知识点
      为单位，计划必须与之一致，否则完成单元后无法回写进度。
    - 运行时计划（``core.agent``）的粒度是**任务**，它是「怎么变」的产物，
      由领域无关的 ``GoalSpec`` 驱动。

    因此**知识点 id 不进入 ``GoalSpec``**：把知识点塞进运行时契约会让运行时开始
    认识学习领域的概念，破坏 ADR-0013 §10 的分层。应用层要做的是：取出当前要
    学的那个单元，映射成 ``GoalSpec``，交给运行时拆成任务。

    Args:
        unit: 学习单元。
        goal: 单元所属的目标。

    Returns:
        领域无关的目标描述。
    """
    constraints = [f"预计投入：{unit.estimated_minutes} 分钟"]
    if unit.depends_on:
        prerequisites = "、".join(str(point_id) for point_id in unit.depends_on)
        constraints.append(f"前置知识点：{prerequisites}")
    constraints.append(f"在计划里的理由：{unit.reason}")

    return GoalSpec(
        goal_id=goal.id.value,
        statement=f"掌握「{unit.title}」",
        outcomes=unit.outcomes,
        constraints=tuple(constraints),
    )
