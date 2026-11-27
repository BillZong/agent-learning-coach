"""学习目标的状态机、结构化约束与跨聚合绑定。

跑法::

    uv run python examples/learning_goal.py

脚本演示五件事：

1. **创建**——目标是草稿：先有陈述与学习结果，诊断之后才绑定知识点。
2. **约束**——时间范围、每日投入、前置知识点都是结构化的值对象，可以校验。
3. **状态机**——草稿 → 进行中 ⇄ 已暂停 → 已达成 / 已放弃 / 已过期；非法迁移
   被拒绝，每次迁移留下理由与时间。
4. **跨聚合一致**——目标按 id 绑定知识点；引用不存在的知识点会被查出来。
5. **交接**——目标映射成 ``GoalSpec``，再交给确定性规划器拆成任务。

脚本不联网。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

from agent_learning_coach.core.agent import OutcomePlanner
from agent_learning_coach.core.learning import (
    CapabilityRequirement,
    Course,
    CourseId,
    DailyEffort,
    DomainError,
    GoalId,
    KnowledgePoint,
    KnowledgePointId,
    KnowledgeStructure,
    LearnerId,
    LearningGoal,
    TimeWindow,
    check_goal_against_course,
    to_goal_spec,
)

AT = 1_767_225_600.0
"""固定的迁移时刻（2026-01-01 前后），让示例输出可复现。"""


def _course() -> Course:
    """构造一门含两个知识点的课程。"""
    return Course(
        id=CourseId("course-js-core"),
        title="JavaScript 核心",
        structure=KnowledgeStructure(
            points=(
                KnowledgePoint(id=KnowledgePointId("k1"), title="函数与作用域"),
                KnowledgePoint(id=KnowledgePointId("k2"), title="闭包"),
            ),
        ),
    )


def _draft_goal() -> LearningGoal:
    """构造一个还没绑定知识点的草稿目标。"""
    return LearningGoal(
        id=GoalId("goal-closures"),
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-js-core"),
        statement="掌握 JavaScript 闭包",
        outcomes=("说清闭包捕获了什么", "能写出一个闭包例子"),
        time_window=TimeWindow(start=date(2026, 1, 1), end=date(2026, 3, 1)),
        daily_effort=DailyEffort(minutes_per_day=90),
        prerequisites=(KnowledgePointId("k1"),),
    )


def show_creation() -> None:
    """演示目标创建：草稿先有范围，再绑定知识点。"""
    print("=== 1. 目标创建：草稿 → 绑定知识点 ===\n")
    draft = _draft_goal()
    print(f"草稿状态：{draft.status}")
    print(f"已绑定知识点：{[str(item) for item in draft.knowledge_point_ids] or '无'}")
    try:
        draft.activate(at=AT, reason="诊断完成")
    except DomainError as exc:
        print(f"[没绑定知识点就激活] -> {exc}")
    print()


def show_constraints() -> None:
    """演示结构化约束。"""
    print("=== 2. 结构化约束 ===\n")
    window = TimeWindow(start=date(2026, 1, 1), end=date(2026, 3, 1))
    effort = DailyEffort(minutes_per_day=90)
    print(f"时间范围：{window.render()}")
    print(f"每日投入：{effort.render()}")
    now = datetime(2026, 3, 2, tzinfo=UTC)
    print(f"是否已过期（{now.date()}）：{window.has_passed(now)}")
    try:
        TimeWindow(start=date(2026, 3, 1), end=date(2026, 1, 1))
    except DomainError as exc:
        print(f"[结束早于开始] -> {exc}")
    try:
        DailyEffort(minutes_per_day=0)
    except DomainError as exc:
        print(f"[每日投入为零] -> {exc}")
    print()


def show_state_machine() -> LearningGoal:
    """演示状态机与迁移留痕。"""
    print("=== 3. 状态机 ===\n")
    goal = _draft_goal().bind_requirements(
        (CapabilityRequirement(point_id=KnowledgePointId("k2"), outcome="能写出一个闭包例子"),),
    )
    goal = goal.activate(at=AT, reason="诊断完成，绑定闭包知识点")
    print(f"激活后：{goal.status}")
    goal = goal.suspend(at=AT, reason="本月出差")
    print(f"暂停后：{goal.status}")
    goal = goal.resume(at=AT, reason="出差结束")
    print(f"恢复后：{goal.status}")
    print("迁移历史（每次都留理由与时间）：")
    for transition in goal.transitions:
        print(f"  {transition.source} -> {transition.target}：{transition.reason}")
    print()
    return goal


def show_rejections() -> None:
    """演示被拒绝的状态变化。"""
    print("=== 4. 非法迁移会被拒绝 ===\n")
    active = _draft_goal().bind_requirements(
        (CapabilityRequirement(point_id=KnowledgePointId("k2"), outcome="能写出一个闭包例子"),),
    )
    active = active.activate(at=AT, reason="诊断完成")
    achieved = active.achieve(at=AT, reason="通过练习")
    attempts = [
        ("终态回退", lambda: achieved.abandon(at=AT, reason="反悔")),
        ("草稿不能暂停", lambda: _draft_goal().suspend(at=AT, reason="先放放")),
        ("没有时间范围不能过期", lambda: active.expire(at=AT, reason="期限已过")),
        ("迁移没有理由", lambda: active.suspend(at=AT, reason=" ")),
        ("激活后不能再绑定知识点", lambda: active.bind_requirements(())),
    ]
    for label, attempt in attempts:
        try:
            attempt()
        except DomainError as exc:
            print(f"[{label}] -> {exc}")
    print()


def show_consistency(goal: LearningGoal) -> None:
    """演示跨聚合一致性检查。"""
    print("=== 5. 跨聚合一致性：目标引用的知识点必须存在 ===\n")
    course = _course()
    print(f"与课程一致：{check_goal_against_course(goal, course) or '无问题'}")

    broken = _draft_goal().bind_requirements(
        (CapabilityRequirement(point_id=KnowledgePointId("k9"), outcome="能写出一个闭包例子"),),
    )
    for issue in check_goal_against_course(broken, course):
        print(f"[{issue.code}] -> {issue.message}")
    print()


async def show_handoff(goal: LearningGoal) -> None:
    """演示领域模型到运行时的单向交接。"""
    print("=== 6. 领域模型 → GoalSpec → 计划 ===\n")
    spec = to_goal_spec(goal)
    print(f"GoalSpec.goal_id = {spec.goal_id}")
    print(f"GoalSpec.outcomes = {spec.outcomes}")
    print(f"GoalSpec.constraints = {spec.constraints}")
    print("（课程绑定 course-js-core 留在领域侧，不进入 GoalSpec）\n")

    draft = await OutcomePlanner().plan(spec)
    print("确定性规划器拆出的任务：")
    for task in draft.tasks:
        print(f"  {task.content}")
    print()


def main() -> None:
    """跑一遍学习目标建模演示。"""
    show_creation()
    show_constraints()
    goal = show_state_machine()
    show_rejections()
    show_consistency(goal)
    asyncio.run(show_handoff(goal))
    print("=== 边界提醒 ===")
    print("领域层保证聚合内自洽；跨聚合一致由领域服务检查；")
    print("「是否过期」「前置是否已掌握」需要外部事实，归应用层。")


if __name__ == "__main__":
    main()
