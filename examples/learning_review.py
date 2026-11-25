"""领域模型评审：四个聚合协同走一遍完整学习流程。

跑法::

    uv run python examples/learning_review.py

脚本演示六件事：

1. **画像**——学习者是谁：稳定属性，不含任何随学习变化的状态。
2. **课程结构**——知识点及其依赖：顺序与闭包都是派生查询。
3. **目标**——想达成什么：绑定知识点、结构化约束、状态由迁移历史派生。
4. **进度**——学到了哪：诊断判定已有基础、学习推进、目标完成度。
5. **一致性**——跨聚合检查在整条链上真的拦得住非法数据。
6. **边界**——进度与目标必须配对（同一学习者、同一门课）。

脚本不联网。
"""

from __future__ import annotations

from datetime import date

from agent_learning_coach.core.learning import (
    CapabilityRequirement,
    Course,
    CourseId,
    DailyEffort,
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
    TimeWindow,
    check_goal_against_course,
    check_progress_against_course,
    goal_completion,
    ready_points,
    to_goal_spec,
)

AT = 1_767_225_600.0
"""固定的迁移时刻，让示例输出可复现。"""


def _learner() -> Learner:
    """构造一个学习者画像。"""
    return Learner(
        id=LearnerId("learner-1"),
        name="小王",
        background=("有 Python 经验",),
        preferences=("喜欢先动手再读文档",),
    )


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


def show_profile(learner: Learner) -> None:
    """演示学习者画像。"""
    print("=== 1. 画像：学习者是谁 ===\n")
    print(f"姓名：{learner.name}")
    print(f"背景：{learner.background}")
    print(f"偏好：{learner.preferences}")
    print(f"身上没有进度字段：{not hasattr(learner, 'progress')}")
    print("（画像是稳定属性；进度是「学到了哪」，两者变化频率差几个数量级）\n")


def show_structure(course: Course) -> None:
    """演示课程知识结构。"""
    print("=== 2. 课程结构：知识点及其依赖 ===\n")
    for point in course.structure.order():
        required = "、".join(str(item.id) for item in course.structure.prerequisites_of(point.id))
        print(f"{point.id}: {point.title}（前置：{required or '无'}）")
    closure = course.structure.prerequisite_closure(KnowledgePointId("k3"))
    print(f"k3 的补课顺序：{[item.id for item in closure]}")
    print("（顺序与闭包都是派生查询，每次重算，不落库）\n")


def show_goal(learner: Learner, course: Course) -> LearningGoal:
    """演示目标建模。"""
    print("=== 3. 目标：想达成什么 ===\n")
    goal = LearningGoal(
        id=GoalId("goal-closures"),
        learner_id=learner.id,
        course_id=course.id,
        statement="掌握闭包与高阶函数",
        outcomes=("能写出一个闭包例子", "能用闭包实现高阶函数"),
        time_window=TimeWindow(start=date(2026, 1, 1), end=date(2026, 3, 1)),
        daily_effort=DailyEffort(minutes_per_day=90),
        prerequisites=(KnowledgePointId("k1"),),
    )
    print(f"草稿状态：{goal.status}")
    goal = goal.bind_requirements(
        (
            CapabilityRequirement(
                point_id=KnowledgePointId("k2"),
                outcome="能写出一个闭包例子",
            ),
            CapabilityRequirement(
                point_id=KnowledgePointId("k3"),
                outcome="能用闭包实现高阶函数",
            ),
        ),
    )
    goal = goal.activate(at=AT, reason="诊断完成，绑定知识点")
    bound = [str(item) for item in goal.knowledge_point_ids]
    print(f"绑定并激活后：{goal.status}；绑定的知识点：{bound}")
    print(f"一致性检查：{check_goal_against_course(goal, course) or '无问题'}\n")
    return goal


def show_progress(
    learner: Learner,
    course: Course,
    goal: LearningGoal,
) -> LearningProgress:
    """演示进度推进与完成度。"""
    print("=== 4. 进度：学到了哪 ===\n")
    progress = LearningProgress(learner_id=learner.id, course_id=course.id)
    print(f"进度归属：学习者 {progress.learner_id} × 课程 {progress.course_id}")

    progress = progress.assess(KnowledgePointId("k1"), at=AT, reason="诊断：已掌握作用域")
    print("诊断判定 k1 已有基础（迁移序列的第一条，来源 assessed）")
    print(f"现在可以学：{[str(i) for i in ready_points(progress, course.structure)]}")

    progress = progress.start(KnowledgePointId("k2"), at=AT, reason="开始学闭包")
    progress = progress.complete(KnowledgePointId("k2"), at=AT + 3600, reason="通过闭包练习")
    completion = goal_completion(progress, goal)
    print(f"学完 k2 后完成度：{completion.learned}/{completion.required}（{completion.ratio:.0%}）")
    print(f"尚未学会：{[str(i) for i in completion.outstanding]}")
    print(f"一致性检查：{check_progress_against_course(progress, course) or '无问题'}\n")
    return progress


def show_consistency(learner: Learner, course: Course) -> None:
    """演示一致性检查拦得住非法数据。"""
    print("=== 5. 一致性：跨聚合检查拦得住非法数据 ===\n")
    broken = (
        LearningProgress(learner_id=learner.id, course_id=course.id)
        .start(KnowledgePointId("k3"), at=AT, reason="跳过前置直接学")
        .complete(KnowledgePointId("k3"), at=AT + 3600, reason="学会了高阶函数")
    )
    for issue in check_progress_against_course(broken, course):
        print(f"[{issue.code}] -> {issue.message}")
    print("（知识状态对先修封闭：学会了 k3 就应当已经学会 k2）\n")


def show_boundaries(learner: Learner, course: Course, goal: LearningGoal) -> None:
    """演示进度与目标的配对边界。"""
    print("=== 6. 边界：进度与目标必须配对 ===\n")
    other = LearningProgress(learner_id=LearnerId("learner-2"), course_id=course.id)
    try:
        goal_completion(other, goal)
    except DomainError as exc:
        print(f"[别人的进度] -> {exc}")

    spec = to_goal_spec(goal)
    print(f"\n交给运行时的 GoalSpec：outcomes={spec.outcomes}")
    print(f"约束：{spec.constraints}")
    print("（课程绑定与进度都留在领域侧，不进 GoalSpec）\n")


def main() -> None:
    """跑一遍领域模型评审演示。"""
    learner = _learner()
    course = _course()
    show_profile(learner)
    show_structure(course)
    goal = show_goal(learner, course)
    show_progress(learner, course, goal)
    show_consistency(learner, course)
    show_boundaries(learner, course, goal)

    print("=== 评审结论 ===")
    print("四个聚合的边界与 Phase 1 的用例匹配，不变量都有 ADR 出处；")
    print("剩余的验收缺口全在策略层（诊断、缺口判定）与持久化，不在模型形状。")


if __name__ == "__main__":
    main()
