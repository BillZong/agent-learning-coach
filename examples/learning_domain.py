"""学习领域的边界：实体、值对象、聚合与不变量。

跑法::

    uv run python examples/learning_domain.py

脚本演示四件事：

1. **分类**——Learner / Course / LearningGoal 是三个独立聚合；知识点属于课程
   内部；四类 id 与目标约束是值对象。
2. **不变量**——课程的知识点 id 唯一、前置存在且无环；违规时给出可读理由。
3. **身份**——实体不可变：改名、达成目标都返回新实例，原对象不变。
4. **交接**——目标映射成 ``GoalSpec``，再交给确定性规划器拆成任务。领域侧
   与运行时侧通过一条单向桥连接，运行时不需要认识学习领域。

脚本不联网。
"""

from __future__ import annotations

import asyncio

from agent_learning_coach.core.agent import OutcomePlanner
from agent_learning_coach.core.learning import (
    Course,
    CourseId,
    DomainError,
    GoalConstraint,
    GoalId,
    KnowledgePoint,
    KnowledgePointId,
    KnowledgeRelation,
    KnowledgeStructure,
    Learner,
    LearnerId,
    LearningGoal,
    to_goal_spec,
)


def _point(point_id: str, title: str) -> KnowledgePoint:
    """构造一个知识点。"""
    return KnowledgePoint(id=KnowledgePointId(point_id), title=title)


def _requires(source: str, target: str) -> KnowledgeRelation:
    """构造一条前置关系。"""
    return KnowledgeRelation(
        kind="requires",
        source=KnowledgePointId(source),
        target=KnowledgePointId(target),
    )


def show_classification() -> None:
    """打印三个聚合的划分。"""
    print("=== 1. 实体与值对象 ===\n")
    rows = [
        ("Learner", "实体", "LearnerId", "独立聚合"),
        ("Course", "实体", "CourseId", "独立聚合，拥有知识结构"),
        ("KnowledgePoint", "实体", "KnowledgePointId", "知识结构内部"),
        ("LearningGoal", "实体", "GoalId", "独立聚合：Learner × Course 的关联"),
        ("KnowledgeStructure", "值对象", "无身份", "Course 聚合内部"),
        ("KnowledgeRelation", "值对象", "无身份", "结构内部：requires / part_of"),
        ("四类 id + GoalConstraint", "值对象", "无身份", "按值相等"),
    ]
    for name, kind, identity, scope in rows:
        print(f"{name} | {kind} | 身份={identity} | {scope}")
    print(
        "\n跨聚合只按 id 引用：目标记住 learner_id 与 course_id，"
        "不嵌入学习者或课程对象；\n学习者也不持有目标列表——单向引用，不双写。\n",
    )


def show_course() -> None:
    """演示课程的知识结构与顺序。"""
    print("=== 2. 课程聚合与依赖顺序 ===\n")
    course = Course(
        id=CourseId("course-js-core"),
        title="JavaScript 核心",
        structure=KnowledgeStructure(
            points=(
                _point("k3", "高阶函数"),
                _point("k2", "闭包"),
                _point("k1", "函数与作用域"),
                _point("k4", "模块化"),
            ),
            relations=(_requires("k3", "k2"), _requires("k2", "k1")),
        ),
    )
    for point in course.structure.order():
        required = "，".join(str(item.id) for item in course.structure.prerequisites_of(point.id))
        print(f"{point.id}: {point.title}（前置：{required or '无'}）")
    print("\n顺序由依赖决定；没有依赖的知识点保持声明顺序，因此结果确定。\n")


def show_rejections() -> None:
    """演示被拒绝的建模。"""
    print("=== 3. 违反不变量会被拒绝 ===\n")
    attempts = [
        (
            "知识点 id 重复",
            lambda: KnowledgeStructure(
                points=(_point("k1", "函数"), _point("k1", "作用域")),
            ),
        ),
        (
            "前置不在结构内",
            lambda: KnowledgeStructure(
                points=(_point("k1", "闭包"),),
                relations=(_requires("k1", "k0"),),
            ),
        ),
        (
            "依赖成环",
            lambda: KnowledgeStructure(
                points=(_point("k1", "A"), _point("k2", "B")),
                relations=(_requires("k1", "k2"), _requires("k2", "k1")),
            ),
        ),
        (
            "目标没有可验证的学习结果",
            lambda: LearningGoal(
                id=GoalId("g1"),
                learner_id=LearnerId("l1"),
                course_id=CourseId("c1"),
                statement="掌握闭包",
                outcomes=(),
            ),
        ),
        ("终态不可回退", lambda: _closed_goal().abandon()),
    ]
    for label, attempt in attempts:
        try:
            attempt()
        except DomainError as exc:
            print(f"[{label}] -> {exc}")
    print()


def _closed_goal() -> LearningGoal:
    """构造一个已经达成的目标。"""
    goal = LearningGoal(
        id=GoalId("g-closed"),
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-js-core"),
        statement="复习闭包",
        outcomes=("用自己的话复述闭包",),
    )
    return goal.achieve()


def show_identity() -> None:
    """演示实体身份与不可变。"""
    print("=== 4. 身份来自 id，不来自对象 ===\n")
    learner = Learner(id=LearnerId("learner-1"), name="小王", background=("有 Python 经验",))
    renamed = learner.rename("王同学")
    print(f"原对象：{learner.name}（id={learner.id}）")
    print(f"新对象：{renamed.name}（id={renamed.id}）")
    print(f"身份不变：{learner.id == renamed.id}；原对象未被修改：{learner.name == '小王'}\n")


async def show_handoff() -> None:
    """演示领域模型到运行时的单向交接。"""
    print("=== 5. 领域模型 → GoalSpec → 计划 ===\n")
    goal = LearningGoal(
        id=GoalId("goal-closures"),
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-js-core"),
        statement="掌握 JavaScript 闭包",
        outcomes=("说清闭包捕获了什么", "写出三个闭包例子"),
        constraints=(
            GoalConstraint(kind="time", text="每周 3 小时"),
            GoalConstraint(kind="prerequisite", text="函数与作用域"),
        ),
    )
    spec = to_goal_spec(goal)
    print(f"GoalSpec.goal_id = {spec.goal_id}")
    print(f"GoalSpec.constraints = {spec.constraints}")
    print("（课程绑定 course-js-core 留在领域侧，不进入 GoalSpec）\n")

    draft = await OutcomePlanner().plan(spec)
    print("确定性规划器拆出的任务：")
    for task in draft.tasks:
        print(f"  {task.content}")
    print()


def main() -> None:
    """跑一遍领域模型演示。"""
    show_classification()
    show_course()
    show_rejections()
    show_identity()
    asyncio.run(show_handoff())
    print("=== 边界提醒 ===")
    print("领域模块不认识模型、工具与运行时；映射只发生在 bridge.to_goal_spec。")


if __name__ == "__main__":
    main()
