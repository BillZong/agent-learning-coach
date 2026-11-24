"""学习领域聚合的测试（Topic 15）。

覆盖四组关注点：

**分类**——哪些是实体、哪些是值对象：id 有类型且互不相等；约束按值比较。

**身份与不可变**——实体改属性返回新实例，原实例不变；改名不改变身份。

**聚合不变量**——课程名合法、目标至少一条学习结果、终态不可回退。

**边界**——领域模型映射成运行时的 ``GoalSpec``；课程绑定不进入 ``GoalSpec``。

知识点与关系的建模规则见 ``test_knowledge_structure.py``（Topic 16）。
"""

from __future__ import annotations

import pytest

from agent_learning_coach.core.learning import (
    Course,
    CourseId,
    DomainError,
    GoalConstraint,
    GoalId,
    Learner,
    LearnerId,
    LearningGoal,
    to_goal_spec,
)


def _goal(**overrides: object) -> LearningGoal:
    """构造一个合法目标，允许按需覆盖字段。"""
    fields: dict[str, object] = {
        "id": GoalId("goal-closures"),
        "learner_id": LearnerId("learner-1"),
        "course_id": CourseId("course-js"),
        "statement": "掌握闭包",
        "outcomes": ("说清闭包是什么",),
    }
    fields.update(overrides)
    return LearningGoal(**fields)  # type: ignore[arg-type]


# --- 分类与值对象 ---------------------------------------------------------


def test_identifiers_are_typed_and_not_interchangeable() -> None:
    """不同聚合的 id 即使文本相同也不相等——传错边界会立刻暴露。"""
    assert LearnerId("x") != CourseId("x")
    assert LearnerId("x") == LearnerId("x")
    assert str(LearnerId("x")) == "x"


@pytest.mark.parametrize("blank", ["", "   "])
def test_identifier_rejects_blank(blank: str) -> None:
    """id 不能为空或纯空白。"""
    with pytest.raises(DomainError, match="标识不能为空"):
        LearnerId(blank)


def test_identifier_rejects_surrounding_whitespace() -> None:
    """id 不做静默 strip：首尾空白是调用方的错误。"""
    with pytest.raises(DomainError, match="首尾空白"):
        CourseId(" course-js")


def test_constraint_is_a_value_object() -> None:
    """约束没有身份：内容相同就是同一个约束。"""
    assert GoalConstraint(kind="time", text="每周 3 小时") == GoalConstraint(
        kind="time",
        text="每周 3 小时",
    )
    assert GoalConstraint(kind="time", text="每周 3 小时").render() == "时间：每周 3 小时"


def test_constraint_rejects_blank_text() -> None:
    """约束内容不能为空。"""
    with pytest.raises(DomainError, match="约束内容不能为空"):
        GoalConstraint(kind="preference", text=" ")


# --- 实体身份与不可变 -----------------------------------------------------


def test_learner_rename_returns_new_instance() -> None:
    """改名不改变身份，也不修改原对象。"""
    learner = Learner(id=LearnerId("l1"), name="小王", background=("有 Python 经验",))
    renamed = learner.rename("王同学")

    assert renamed.id == learner.id
    assert renamed.name == "王同学"
    assert learner.name == "小王"
    assert renamed.background == learner.background


def test_learner_rejects_blank_fields() -> None:
    """名称与描述条目都不能是空白。"""
    with pytest.raises(DomainError, match="学习者名称不能为空"):
        Learner(id=LearnerId("l1"), name=" ")
    with pytest.raises(DomainError, match="学习者描述条目不能为空"):
        Learner(id=LearnerId("l1"), name="小王", preferences=("",))


# --- 课程聚合 -------------------------------------------------------------


def test_course_rejects_blank_title() -> None:
    """课程名称不能为空。"""
    with pytest.raises(DomainError, match="课程名称不能包含首尾空白"):
        Course(id=CourseId("c1"), title=" JS ")


def test_course_defaults_to_empty_structure() -> None:
    """课程可以暂时没有知识结构（Phase 1 先建骨架）。"""
    course = Course(id=CourseId("c1"), title="JavaScript 基础")

    assert course.structure.order() == ()
    assert course.structure.entry_points() == ()


# --- 学习目标的生命周期 ---------------------------------------------------


def test_goal_requires_verifiable_outcomes() -> None:
    """没有学习结果的目标是愿望，不是可验证的目标。"""
    with pytest.raises(DomainError, match="至少要有一条可验证的学习结果"):
        _goal(outcomes=())


def test_goal_requires_statement() -> None:
    """目标陈述不能为空。"""
    with pytest.raises(DomainError, match="目标陈述不能为空"):
        _goal(statement="")


def test_goal_transitions_to_terminal_state() -> None:
    """进行中 → 已达成 / 已放弃；两种终态都不可回退。"""
    goal = _goal()
    achieved = goal.achieve()
    abandoned = goal.abandon()

    assert goal.is_open
    assert achieved.status == "achieved"
    assert not achieved.is_open
    assert abandoned.status == "abandoned"
    with pytest.raises(DomainError, match="状态为已达成，不能再变更为已放弃"):
        achieved.abandon()
    with pytest.raises(DomainError, match="状态为已放弃，不能再变更为已达成"):
        abandoned.achieve()


# --- 边界：领域模型 → 运行时 ----------------------------------------------


def test_to_goal_spec_maps_target_fields_only() -> None:
    """映射保留陈述、结果与约束渲染；课程绑定留在领域侧。"""
    goal = _goal(
        outcomes=("说清闭包是什么", "写出三个例子"),
        constraints=(
            GoalConstraint(kind="time", text="每周 3 小时"),
            GoalConstraint(kind="prerequisite", text="函数与作用域"),
        ),
    )

    spec = to_goal_spec(goal)

    assert spec.goal_id == "goal-closures"
    assert spec.statement == "掌握闭包"
    assert spec.outcomes == ("说清闭包是什么", "写出三个例子")
    assert spec.constraints == ("时间：每周 3 小时", "先修：函数与作用域")
    assert not hasattr(spec, "course_id")
