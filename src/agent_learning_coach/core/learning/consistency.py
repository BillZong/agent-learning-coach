"""跨聚合一致性检查——学习领域的**领域服务**。

Topic 17 的工程问题之一是：**目标约束该由领域层保证，还是由应用层保证？**
答案不是二选一，而是按「判断需要什么数据」分成三档：

| 档位 | 判断依据 | 落在哪里 |
| ---- | -------- | -------- |
| 聚合内自洽 | 只看目标自己的数据 | ``goal.py`` 构造时校验 |
| 跨聚合一致 | 需要另一个聚合的**已有数据** | 本模块（领域服务） |
| 依赖外部事实 | 需要时钟、掌握度、学习证据 | 应用层 |

本模块处理第二档。典型例子：目标按 id 引用了知识点（ADR-0015 的「跨聚合只按
id 引用」），但**引用是否指向真实存在的知识点**，只有拿到 ``Course`` 才知道。
领域服务做这件事有两个理由：

1. 它是**纯函数**：不读数据库、不调模型、不读时钟，因此可以脱离基础设施
   单独测试。
2. 规则属于**领域知识**：「目标不能引用课程里不存在的知识点」是业务规则，不是
   编排细节。放到应用层会让同一规则散落在多个用例里。

领域服务**只报告不一致，不替调用方做决定**：应用层拿到问题后是拒绝保存、还是
自动修正、还是提示用户，取决于用例。这条分界让领域层保持纯粹，也避免它反过来
依赖应用层。

不属于本模块的判断（留给应用层）：

- 时间窗口是否已经过期——需要「现在」，领域层没有时钟（``TimeWindow.has_passed``
  提供纯查询，由调用方传时间进来）。
- 前置知识点是否已被学习者掌握——需要学习进度（Topic 18）。
- 目标是否真的达成——需要学习证据（Topic 40）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agent_learning_coach.core.learning.domain import Course
from agent_learning_coach.core.learning.goal import LearningGoal

GoalIssueCode = Literal[
    "course_mismatch",
    "unknown_requirement_point",
    "unknown_prerequisite_point",
]
"""跨聚合一致性问题的种类。"""


@dataclass(frozen=True)
class GoalIssue:
    """一条跨聚合不一致——**值对象**。

    只报告事实，不抛异常：一次检查可能同时发现多个问题，逐个抛异常会让调用方
    只能看到第一个。
    """

    code: GoalIssueCode
    """问题种类。"""

    message: str
    """面向用户与调用方的说明。"""


def check_goal_against_course(goal: LearningGoal, course: Course) -> tuple[GoalIssue, ...]:
    """检查目标与它所引用课程之间的一致性。

    检查三件事：

    1. 目标引用的课程就是给定的这门课（否则后面的知识点校验没有意义）。
    2. 每条能力要求引用的知识点存在于课程的知识结构中。
    3. 每个前置知识点存在于课程的知识结构中。

    Args:
        goal: 学习目标。
        course: 目标引用的课程。

    Returns:
        发现的问题；一致时为空元组。
    """
    issues: list[GoalIssue] = []
    if goal.course_id != course.id:
        issues.append(
            GoalIssue(
                code="course_mismatch",
                message=f"目标 {goal.id} 绑定的是课程 {goal.course_id}，与课程 {course.id} 不一致",
            ),
        )
        return tuple(issues)

    known = {point.id for point in course.structure.points}
    for requirement in goal.requirements:
        if requirement.point_id not in known:
            issues.append(
                GoalIssue(
                    code="unknown_requirement_point",
                    message=(
                        f"目标 {goal.id} 的能力要求引用了课程 {course.id} 中不存在的知识点："
                        f"{requirement.point_id}"
                    ),
                ),
            )
    for point_id in goal.prerequisites:
        if point_id not in known:
            issues.append(
                GoalIssue(
                    code="unknown_prerequisite_point",
                    message=(
                        f"目标 {goal.id} 的前置条件引用了课程 {course.id} "
                        f"中不存在的知识点：{point_id}"
                    ),
                ),
            )
    return tuple(issues)


__all__ = [
    "GoalIssue",
    "GoalIssueCode",
    "check_goal_against_course",
]
