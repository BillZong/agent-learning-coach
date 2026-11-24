"""学习领域聚合的测试（Topic 15）。

覆盖三组关注点：

**分类**——哪些是实体、哪些是值对象：id 有类型且互不相等。

**身份与不可变**——实体改属性返回新实例，原实例不变；改名不改变身份。

**聚合不变量**——课程名合法。

知识点与关系的建模规则见 ``test_knowledge_structure.py``（Topic 16）；
目标的状态机、约束与绑定见 ``test_learning_goal.py``（Topic 17）。
"""

from __future__ import annotations

import pytest

from agent_learning_coach.core.learning import (
    Course,
    CourseId,
    DomainError,
    Learner,
    LearnerId,
)

# --- 分类与值对象 ---------------------------------------------------------


def test_identifiers_are_typed_and_not_interchangeable() -> None:
    """不同聚合的 id 即使文本相同也不相等——传错边界会立刻暴露。"""
    assert LearnerId("x") != CourseId("x")
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
        Learner(id=LearnerId("l1"), name=" ", preferences=())
    with pytest.raises(DomainError, match="学习者描述条目不能为空"):
        Learner(id=LearnerId("l1"), name="小王", preferences=("",))


# --- 课程聚合 -------------------------------------------------------------


def test_course_rejects_blank_title() -> None:
    """课程名称不能为空。"""
    with pytest.raises(DomainError, match="课程名称不能包含首尾空白"):
        Course(id=CourseId("c1"), title=" JS ")


def test_course_defaults_to_empty_structure() -> None:
    """课程可以暂时没有知识结构（Phase 1 先建骨架）。"""
    course = Course(id=CourseId("c1"), title="JavaScript 核心")

    assert course.structure.points == ()
    assert course.structure.entry_points() == ()
