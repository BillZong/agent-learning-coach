"""学习计划：范围、依赖顺序、时间约束与超载。

跑法::

    uv run python examples/learning_planning.py

脚本演示五件事：

1. **范围**——目标只要求一个知识点，计划却带上它的前置闭包。
2. **顺序**——依赖是硬约束：前置一定排在后继之前。
3. **理由**——每个单元都要能回答「为什么学它」。
4. **时间**——按每日投入装箱；没约定投入就不排期。
5. **超载**——时间装不下要报告，但一个单元都不能少排。

脚本不联网。
"""

from __future__ import annotations

from datetime import date

from agent_learning_coach.core.learning import (
    CapabilityRequirement,
    CourseId,
    DailyEffort,
    GoalId,
    KnowledgePoint,
    KnowledgePointId,
    KnowledgeRelation,
    KnowledgeStructure,
    LearnerId,
    LearningGoal,
    LearningProgress,
    TimeWindow,
    UniformEffort,
    generate_plan,
)


def _structure() -> KnowledgeStructure:
    """构造一条链式结构：先学作用域，再学闭包，最后学装饰器。

    ``requires`` 的方向是「source 必须先掌握 target」。

    Returns:
        知识结构。
    """
    return KnowledgeStructure(
        points=(
            KnowledgePoint(id=KnowledgePointId("k1"), title="作用域与变量绑定"),
            KnowledgePoint(id=KnowledgePointId("k2"), title="闭包"),
            KnowledgePoint(id=KnowledgePointId("k3"), title="装饰器"),
        ),
        relations=(
            KnowledgeRelation(
                kind="requires",
                source=KnowledgePointId("k2"),
                target=KnowledgePointId("k1"),
            ),
            KnowledgeRelation(
                kind="requires",
                source=KnowledgePointId("k3"),
                target=KnowledgePointId("k2"),
            ),
        ),
    )


def _goal(
    *,
    daily_effort: DailyEffort | None = None,
    time_window: TimeWindow | None = None,
) -> LearningGoal:
    """构造一个目标：只要求掌握装饰器。

    Args:
        daily_effort: 每日投入。
        time_window: 时间范围。

    Returns:
        学习目标。
    """
    return LearningGoal(
        id=GoalId("goal-1"),
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-1"),
        statement="学会用装饰器改造函数",
        outcomes=("能写出并解释一个装饰器",),
        requirements=(
            CapabilityRequirement(
                point_id=KnowledgePointId("k3"),
                outcome="能写出并解释一个装饰器",
            ),
        ),
        daily_effort=daily_effort,
        time_window=time_window,
    )


def _progress() -> LearningProgress:
    """构造一份空白进度。

    Returns:
        学习进度。
    """
    return LearningProgress(
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-1"),
    )


def show_scope() -> None:
    """演示计划范围：目标要求 ∪ 前置闭包 − 已学会。"""
    print("=== 1. 范围：目标只要求一个点，计划要带上它的前置 ===\n")
    plan = generate_plan(_goal(), _structure(), _progress())

    for unit in plan.units:
        print(f"{unit.point_id} {unit.title}——{unit.reason}")
    print("（k1、k2 不在目标的要求里，但它们是 k3 的前置：不先学它们，计划执行不了）")
    print("（目标要求的是 k3；计划带上的是「通往 k3 的路径」）\n")


def show_order() -> None:
    """演示依赖顺序。"""
    print("=== 2. 顺序：依赖是硬约束 ===\n")
    plan = generate_plan(_goal(), _structure(), _progress())

    for unit in plan.units:
        depends = "、".join(str(item) for item in unit.depends_on) or "无"
        print(f"{unit.point_id}：依赖 {depends}")
    print("（前置一定排在后继之前——知识点有先修依赖，跳过一处后面全部塌掉）")
    print("（单元的前置只含计划内的：已经学会的前置已被满足，不构成依赖）\n")


def show_schedule() -> None:
    """演示按每日投入装箱。"""
    print("=== 3. 时间：按每日投入装箱 ===\n")
    goal = _goal(daily_effort=DailyEffort(minutes_per_day=45))
    plan = generate_plan(goal, _structure(), _progress(), estimator=UniformEffort(30))

    for load in plan.days:
        print(f"第 {load.day} 天：{load.minutes} 分钟（上限 {load.capacity}）")
    print(f"总投入：{plan.total_minutes} 分钟")
    print("（没有约定每日投入时就不排期——硬排「第 0 天装下全部内容」是假精度）\n")


def show_overload() -> None:
    """演示超载：报告，但不少排。"""
    print("=== 4. 超载：装不下要报告，不能少排 ===\n")
    goal = _goal(
        daily_effort=DailyEffort(minutes_per_day=30),
        time_window=TimeWindow(start=date(2026, 11, 25), end=date(2026, 11, 26)),
    )
    plan = generate_plan(goal, _structure(), _progress(), estimator=UniformEffort(30))

    overload = plan.overload
    if overload is None:
        print("装得下")
        return
    print(f"总投入 {overload.total_minutes} 分钟，需要 {overload.required_days} 天")
    print(f"时间范围内只有 {overload.available_days} 天，超出 {overload.shortfall_days} 天")
    print(f"计划仍然包含全部 {len(plan.units)} 个单元")
    for load in plan.days:
        mark = "在期限内" if load.within_window else "已超出期限"
        print(f"  第 {load.day} 天：{load.minutes} 分钟（{mark}）")
    print("（悄悄丢掉内容会让「要补的地方」凭空消失——那是 ADR-0023 反对的宽松汇总）")
    print("（怎么处理是应用层的事：加时间、延期限，还是砍范围）\n")


def show_to_runtime() -> None:
    """演示学习单元怎么交给运行时。"""
    print("=== 5. 交给运行时：粒度对齐到知识点，契约不扩展 ===\n")
    from agent_learning_coach.core.learning import to_unit_goal_spec

    goal = _goal(daily_effort=DailyEffort(minutes_per_day=45))
    plan = generate_plan(goal, _structure(), _progress(), estimator=UniformEffort(30))
    unit = plan.units[-1]
    spec = to_unit_goal_spec(unit, goal)

    print(f"单元：{unit.point_id} {unit.title}")
    print(f"运行时看到的目标：{spec.statement}")
    print(f"结果：{'、'.join(spec.outcomes)}")
    for constraint in spec.constraints:
        print(f"约束：{constraint}")
    print("（知识点 id 不进入运行时契约——运行时因此不需要认识学习领域）")
    print("（领域侧按单元逐个映射，运行时逐个拆成任务）")


def main() -> None:
    """跑一遍学习计划演示。"""
    show_scope()
    show_order()
    show_schedule()
    show_overload()
    show_to_runtime()


if __name__ == "__main__":
    main()
