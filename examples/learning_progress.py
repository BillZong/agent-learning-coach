"""学习进度：知识点级的状态、归属与派生查询。

跑法::

    uv run python examples/learning_progress.py

脚本演示五件事：

1. **归属**——进度是（学习者 × 课程）的关联，不挂在学习者身上。
2. **状态机**——知识点级的状态与任务状态是两套东西：没有「跳过」，「被阻塞」
   是派生结果而不是状态。
3. **已有知识**——诊断判定「本来就会」是迁移序列里的第一条，与「本次学会」
   区分得开。
4. **派生查询**——目标完成度、被阻塞的知识点、现在可以开始学的知识点。
5. **边界**——进度与知识结构的前置封闭性检查。

脚本不联网。
"""

from __future__ import annotations

from datetime import date

from agent_learning_coach.core.learning import (
    CapabilityRequirement,
    Course,
    CourseId,
    DomainError,
    GoalId,
    KnowledgePoint,
    KnowledgePointId,
    KnowledgeRelation,
    KnowledgeStructure,
    Learner,
    LearnerId,
    LearningGoal,
    LearningProgress,
    blocked_points,
    check_progress_against_course,
    goal_completion,
    ready_points,
)

AT = 1_767_225_600.0
"""固定的迁移时刻，让示例输出可复现。"""


def _course() -> Course:
    """构造一门有依赖链的课程：k1 → k2 → k3，另有独立点 k4。"""
    return Course(
        id=CourseId("course-js-core"),
        title="JavaScript 核心",
        structure=KnowledgeStructure(
            points=(
                KnowledgePoint(id=KnowledgePointId("k1"), title="函数与作用域"),
                KnowledgePoint(id=KnowledgePointId("k2"), title="闭包"),
                KnowledgePoint(id=KnowledgePointId("k3"), title="高阶函数"),
                KnowledgePoint(id=KnowledgePointId("k4"), title="模块化"),
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
        ),
    )


def _goal() -> LearningGoal:
    """构造一个要求掌握 k2 与 k3 的目标。"""
    return LearningGoal(
        id=GoalId("goal-closures"),
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-js-core"),
        statement="掌握闭包与高阶函数",
        outcomes=("能写出一个闭包例子", "能用闭包实现高阶函数"),
        requirements=(
            CapabilityRequirement(
                point_id=KnowledgePointId("k2"),
                outcome="能写出一个闭包例子",
            ),
            CapabilityRequirement(
                point_id=KnowledgePointId("k3"),
                outcome="能用闭包实现高阶函数",
            ),
        ),
        time_window=None,
    )


def show_ownership() -> LearningProgress:
    """演示进度的归属：学习者 × 课程。"""
    print("=== 1. 归属：进度是（学习者 × 课程）的关联 ===\n")
    learner = Learner(id=LearnerId("learner-1"), name="小王", background=("有 Python 经验",))
    progress = LearningProgress(
        learner_id=learner.id,
        course_id=CourseId("course-js-core"),
    )
    print(f"学习者画像：{learner.name}，背景 {learner.background}")
    print(f"学习者身上没有进度字段：{not hasattr(learner, 'progress')}")
    print(f"进度归属：学习者 {progress.learner_id} × 课程 {progress.course_id}")
    print("（画像是「学习者是谁」，进度是「学到了哪」——后者随每次学习活动变化）\n")
    return progress


def show_state_machine(progress: LearningProgress) -> LearningProgress:
    """演示知识点级状态机与任务状态的区别。"""
    print("=== 2. 状态机：知识点状态 ≠ 任务状态 ===\n")
    progress = progress.assess(KnowledgePointId("k1"), at=AT, reason="诊断：已掌握作用域").start(
        KnowledgePointId("k2"), at=AT, reason="开始学闭包"
    )
    print("知识点 k1：已学会（诊断判定）")
    print("知识点 k2：学习中")
    print("知识点 k3：未开始（还没碰过）")
    print("\n与任务状态的差别：")
    print("  任务可以 skipped（不做这一步）——知识点没有「跳过」；")
    print("  任务可以 blocked（缺外部条件）——知识点没有「受阻」状态，")
    print("  「被阻塞」是前置没学会算出来的结果。\n")

    attempts = [
        ("没学过就复习", lambda: progress.review(KnowledgePointId("k3"), at=AT, reason="复习")),
        (
            "正在学的点判定为已有基础",
            lambda: progress.assess(KnowledgePointId("k2"), at=AT, reason="诊断已掌握"),
        ),
    ]
    for label, attempt in attempts:
        try:
            attempt()
        except DomainError as exc:
            print(f"[{label}] -> {exc}")
    print()
    return progress


def show_prior_knowledge(progress: LearningProgress) -> None:
    """演示已有知识的表达方式。"""
    print("=== 3. 已有知识：迁移序列里的第一条 ===\n")
    for point in progress.points:
        first = point.transitions[0]
        print(f"知识点 {point.point_id}：首条迁移 {first.source} → {first.target}")
        print(f"  来源：{first.source_kind}；理由：{first.reason}")
    print("\n「本来就会」与「本次学会」因此区分得开，不需要额外的概念。\n")


def show_queries(progress: LearningProgress, course: Course) -> LearningProgress:
    """演示派生查询。"""
    print("=== 4. 派生查询 ===\n")
    structure = course.structure

    completion = goal_completion(progress, _goal())
    print(f"目标完成度：{completion.learned}/{completion.required}（{completion.ratio:.0%}）")
    print(f"尚未学会：{[str(item) for item in completion.outstanding]}")

    ready = ready_points(progress, structure)
    print(f"现在可以学：{[str(item) for item in ready]}")

    blocked = blocked_points(progress, structure)
    print(f"被前置阻塞：{[str(item) for item in blocked]}")
    print("（阻塞是算出来的：k3 需要先会 k2，而 k2 还在学习中）\n")

    progress = progress.complete(KnowledgePointId("k2"), at=AT + 3600, reason="通过闭包练习")
    print(f"学完 k2 后可以学：{[str(item) for item in ready_points(progress, structure)]}")
    print(f"目标完成度：{goal_completion(progress, _goal()).ratio:.0%}\n")
    return progress


def show_consistency(progress: LearningProgress, course: Course) -> None:
    """演示跨聚合一致性检查。"""
    print("=== 5. 前置封闭性检查 ===\n")
    print(f"正常进度：{check_progress_against_course(progress, course) or '无问题'}")

    broken = (
        LearningProgress(
            learner_id=LearnerId("learner-1"),
            course_id=CourseId("course-js-core"),
        )
        .start(KnowledgePointId("k3"), at=AT, reason="跳过前置直接学")
        .complete(KnowledgePointId("k3"), at=AT + 3600, reason="学会了高阶函数")
    )
    for issue in check_progress_against_course(broken, course):
        print(f"[{issue.code}] -> {issue.message}")
    print("\n知识状态对先修封闭：学会了 k3 就应当已经学会 k2。")
    print(f"（今天是 {date.today().isoformat()}，示例中的时刻是固定的）\n")


def main() -> None:
    """跑一遍学习进度演示。"""
    course = _course()
    progress = show_ownership()
    progress = show_state_machine(progress)
    show_prior_knowledge(progress)
    progress = show_queries(progress, course)
    show_consistency(progress, course)

    print("=== 边界提醒 ===")
    print("进度是领域层的事实，不进运行时的 GoalSpec；")
    print("它与知识结构的前置关系通过派生查询回答，不另存一份。")


if __name__ == "__main__":
    main()
