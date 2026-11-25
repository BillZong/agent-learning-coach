"""计划版本：修订与全新计划怎么区分，计划怎么查。

跑法::

    uv run python examples/learning_plan_versioning.py

脚本演示五件事：

1. **全新计划**——开一条计划线，版本号从 1 开始。
2. **修订**——同一计划线，版本号 +1，并记着与上一版的差异。
3. **全新计划（换路径）**——新的计划线，与旧计划线并存。
4. **依据**——每一版都记着它基于什么生成。
5. **查询**——排序按版本号，过滤按目标与计划线，「当前版本」答得准。

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
    PlanId,
    PlanVersionId,
    TimeWindow,
    UniformEffort,
    current_plan_version,
    plan_versions,
    revise_plan,
    start_plan,
)


def _structure() -> KnowledgeStructure:
    """构造一条链式结构：先学 k1，再学 k2，最后学 k3。"""
    return KnowledgeStructure(
        points=(
            KnowledgePoint(id=KnowledgePointId("k1"), title="作用域"),
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


def _goal() -> LearningGoal:
    """构造一个目标：要求掌握 k1、k2、k3。"""
    return LearningGoal(
        id=GoalId("goal-1"),
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-1"),
        statement="学会装饰器",
        outcomes=("能写出装饰器",),
        requirements=(
            CapabilityRequirement(point_id=KnowledgePointId("k1"), outcome="能写出装饰器"),
            CapabilityRequirement(point_id=KnowledgePointId("k2"), outcome="能写出装饰器"),
            CapabilityRequirement(point_id=KnowledgePointId("k3"), outcome="能写出装饰器"),
        ),
        daily_effort=DailyEffort(minutes_per_day=45),
        time_window=TimeWindow(start=date(2026, 11, 25), end=date(2026, 12, 25)),
    )


def _progress(*learned: str) -> LearningProgress:
    """构造一份进度。"""
    progress = LearningProgress(
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-1"),
    )
    for point_id in learned:
        progress = progress.start(KnowledgePointId(point_id), at=0.0, reason="开始学")
        progress = progress.complete(KnowledgePointId(point_id), at=1.0, reason="学会了")
    return progress


def show_start() -> object:
    """演示开一份全新计划。

    Returns:
        第一个版本。
    """
    print("=== 1. 全新计划：版本号从 1 开始 ===\n")
    version = start_plan(
        _goal(),
        _structure(),
        _progress(),
        plan_id=PlanId("plan-1"),
        version_id=PlanVersionId("plan-1-v1"),
        reason="初次规划",
        at=1000.0,
        estimator=UniformEffort(30),
    )

    print(f"计划线 {version.plan_id}，版本 {version.number}（{version.id}）")
    print(f"理由：{version.reason}")
    print(f"单元：{[str(unit.point_id) for unit in version.plan.units]}")
    print("（首个版本没有上一版，因此没有差异）\n")
    return version


def show_revision(first: object) -> object:
    """演示修订：同一计划线，版本号 +1。

    Args:
        first: 第一个版本。

    Returns:
        第二个版本。
    """
    print("=== 2. 修订：同一份计划的下一个版本 ===\n")
    second = revise_plan(
        [first],
        _goal(),
        _structure(),
        _progress("k1"),
        version_id=PlanVersionId("plan-1-v2"),
        reason="学习者掌握了作用域",
        at=2000.0,
        estimator=UniformEffort(30),
    )

    print(f"计划线 {second.plan_id}（同一条），版本 {second.number}")
    print(f"理由：{second.reason}")
    assert second.delta is not None
    print(f"差异：移除 {second.delta.removed}，新增 {second.delta.added}")
    print(f"剩下的单元：{[str(unit.point_id) for unit in second.plan.units]}")
    print("（学会了 k1，它就从计划里移除——差异让「计划为什么变了」有答案）\n")
    return second


def show_new_line(first: object) -> object:
    """演示全新计划：新计划线，版本号重新从 1 开始。

    Args:
        first: 第一个版本。

    Returns:
        新计划线的第一个版本。
    """
    print("=== 3. 全新计划：换一条学习路径 ===\n")
    other = start_plan(
        _goal(),
        _structure(),
        _progress(),
        plan_id=PlanId("plan-2"),
        version_id=PlanVersionId("plan-2-v1"),
        reason="换了学习路径：从实践倒着学",
        at=9000.0,
        estimator=UniformEffort(30),
    )

    print(f"计划线 {other.plan_id}（新的），版本 {other.number}")
    print(f"理由：{other.reason}")
    print(f"旧计划线 {first.plan_id} 仍然保留（版本 {first.number}）")
    print("（少了 PlanId 这层区分，「重新规划」既像新版本又像新计划）")
    print("（无法判定的区分等于没有区分）\n")
    return other


def show_basis(version: object) -> None:
    """演示生成依据。

    Args:
        version: 版本。
    """
    print("=== 4. 依据：这一版是基于什么生成的 ===\n")
    basis = version.basis
    print(f"目标：{basis.goal_id}")
    print(f"要求掌握：{[str(item) for item in basis.required_points]}")
    print(f"生成时已学会：{[str(item) for item in basis.learned_points]}")
    print(f"参考诊断：{basis.diagnosis_id}")
    print(f"课程结构指纹：{basis.structure_fingerprint}")
    print("（结构变了，旧版本是否还成立就能看出来——否则只能靠猜）\n")


def show_queries(first: object, second: object, other: object) -> None:
    """演示查询：排序、过滤与当前版本。

    Args:
        first: 第一个版本。
        second: 第二个版本。
        other: 新计划线的第一个版本。
    """
    print("=== 5. 查询：排序、过滤与当前版本 ===\n")
    versions = [second, other, first]

    print("全部版本（按计划线分组，组内按版本号）：")
    for version in plan_versions(versions):
        print(f"  {version.plan_id} v{version.number}：{version.reason}")

    print("\n只看 plan-1：")
    for version in plan_versions(versions, plan_id=PlanId("plan-1")):
        print(f"  v{version.number}")

    print("\n只看目标 goal-1：")
    for version in plan_versions(versions, goal_id=GoalId("goal-1")):
        print(f"  {version.plan_id} v{version.number}")

    current = current_plan_version(versions, goal_id=GoalId("goal-1"))
    assert current is not None
    print(f"\n当前版本：{current.plan_id} v{current.number}（{current.reason}）")
    print("（同一计划线内按版本号排——版本号是领域分配的序号，时间可能补录或乱序）")
    print("（跨计划线按首个版本的时间排——跨线没有共同序号）")


def main() -> None:
    """跑一遍计划版本演示。"""
    first = show_start()
    second = show_revision(first)
    other = show_new_line(first)
    show_basis(second)
    show_queries(first, second, other)


if __name__ == "__main__":
    main()
