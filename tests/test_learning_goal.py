"""学习目标模型的测试（Topic 17）。

覆盖四组关注点：

**状态机**——合法迁移表、终态不可回退、状态由迁移历史派生、迁移必须留理由。

**约束**——时间范围自洽、每日投入为正、能力要求与前置条件不重复；草稿目标
允许还没有能力要求。

**绑定**——目标按 id 绑定知识点；跨聚合一致性检查能发现指向不存在知识点的引用。

**边界**——领域模型映射成运行时的 ``GoalSpec``；课程绑定不进入 ``GoalSpec``。

聚合与知识结构的建模规则见 ``test_learning_domain.py``（Topic 15/16）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from agent_learning_coach.core.learning import (
    CapabilityRequirement,
    Course,
    CourseId,
    DailyEffort,
    DomainError,
    GoalId,
    GoalStatus,
    GoalTransition,
    KnowledgePoint,
    KnowledgePointId,
    KnowledgeStructure,
    LearnerId,
    LearningGoal,
    TimeWindow,
    check_goal_against_course,
    to_goal_spec,
)

AT = 1_700_000_000.0
"""固定的迁移时刻，让测试不依赖真实时钟。"""


def _point(point_id: str, title: str) -> KnowledgePoint:
    """构造一个知识点。"""
    return KnowledgePoint(id=KnowledgePointId(point_id), title=title)


def _course() -> Course:
    """构造一门含两个知识点的课程。"""
    return Course(
        id=CourseId("course-js-core"),
        title="JavaScript 核心",
        structure=KnowledgeStructure(points=(_point("k1", "函数与作用域"), _point("k2", "闭包"))),
    )


def _requirement(
    point_id: str = "k2",
    outcome: str = "能写出一个闭包例子",
) -> CapabilityRequirement:
    """构造一条能力要求。"""
    return CapabilityRequirement(point_id=KnowledgePointId(point_id), outcome=outcome)


def _goal(**overrides: object) -> LearningGoal:
    """构造一个合法目标，允许按需覆盖字段。"""
    fields: dict[str, object] = {
        "id": GoalId("goal-closures"),
        "learner_id": LearnerId("learner-1"),
        "course_id": CourseId("course-js-core"),
        "statement": "掌握 JavaScript 闭包",
        "outcomes": ("说清闭包捕获了什么", "能写出一个闭包例子"),
        "requirements": (_requirement(),),
    }
    fields.update(overrides)
    return LearningGoal(**fields)  # type: ignore[arg-type]


def _active_goal(**overrides: object) -> LearningGoal:
    """构造一个已进入进行中状态的目标。"""
    goal = _goal(**overrides)
    return goal.activate(at=AT, reason="诊断完成，绑定知识点")


# --- 状态机 ---------------------------------------------------------------


def test_goal_starts_as_draft() -> None:
    """新目标处于草稿状态，直到绑定知识点并激活。"""
    goal = _goal(requirements=())

    assert goal.status == "draft"
    assert not goal.is_active
    assert not goal.is_terminal


def test_goal_activates_and_derives_status_from_history() -> None:
    """状态由迁移历史派生，不单独存储——历史是唯一事实来源。"""
    active = _active_goal()

    assert active.status == "active"
    assert active.is_active
    assert not active.is_terminal
    assert len(active.transitions) == 1
    assert active.transitions[0].source == "draft"
    assert active.transitions[0].target == "active"


def test_goal_without_requirements_cannot_activate() -> None:
    """没绑定知识点就激活，等于给规划器一个没有范围的目标。"""
    goal = _goal(requirements=())

    with pytest.raises(DomainError, match="还没绑定任何能力要求"):
        goal.activate(at=AT, reason="开始")


def test_draft_goal_binds_requirements() -> None:
    """诊断之后绑定能力要求，返回新实例且原对象不变。"""
    draft = _goal(requirements=())
    bound = draft.bind_requirements((_requirement(),))

    assert bound.knowledge_point_ids == (KnowledgePointId("k2"),)
    assert draft.knowledge_point_ids == ()
    assert bound.status == "draft"


def test_activated_goal_cannot_rebind_requirements() -> None:
    """激活后改绑定是一次范围变更，属于计划调整（Topic 48），不是目标建模。"""
    with pytest.raises(DomainError, match="状态为进行中，不能再绑定能力要求"):
        _active_goal().bind_requirements((_requirement("k1"),))


def test_goal_suspends_and_resumes() -> None:
    """进行中 ⇄ 已暂停：搁置是可逆的，与放弃不同。"""
    active = _active_goal()

    suspended = active.suspend(at=AT, reason="本月出差")
    assert suspended.status == "suspended"
    assert suspended.is_terminal is False

    resumed = suspended.resume(at=AT, reason="出差结束")
    assert resumed.status == "active"
    assert len(resumed.transitions) == 3


def test_goal_reaches_terminal_states() -> None:
    """达成、放弃、过期都是终态。"""
    assert _active_goal().achieve(at=AT, reason="通过练习").status == "achieved"
    assert _active_goal().abandon(at=AT, reason="换方向").status == "abandoned"
    expired = _active_goal(
        time_window=TimeWindow(start=date(2026, 1, 1), end=date(2026, 2, 1)),
    ).expire(at=AT, reason="期限已过")
    assert expired.status == "expired"


def test_terminal_goal_cannot_transition() -> None:
    """终态不可回退——重新学习要新建目标。"""
    achieved = _active_goal().achieve(at=AT, reason="通过练习")

    with pytest.raises(DomainError, match="状态为已达成，不能再变更为已放弃"):
        achieved.abandon(at=AT, reason="反悔")
    with pytest.raises(DomainError, match="状态为已达成，不能再变更为已暂停"):
        achieved.suspend(at=AT, reason="再看看")


def test_draft_goal_can_be_abandoned_but_not_suspended() -> None:
    """草稿目标可以直接放弃（诊断结论不成立），但不能暂停。"""
    draft = _goal(requirements=())

    assert draft.abandon(at=AT, reason="诊断结论不成立").status == "abandoned"
    with pytest.raises(DomainError, match="状态为草稿，不能再变更为已暂停"):
        draft.suspend(at=AT, reason="先放放")


def test_goal_without_time_window_cannot_expire() -> None:
    """没有期限的目标不存在「过期」。"""
    with pytest.raises(DomainError, match="没有时间范围，不存在过期"):
        _active_goal().expire(at=AT, reason="期限已过")


def test_transition_requires_reason() -> None:
    """没有理由的状态变化无法解释，因此拒绝。"""
    with pytest.raises(DomainError, match="状态迁移理由不能为空"):
        _active_goal().suspend(at=AT, reason=" ")


def test_broken_transition_history_is_rejected() -> None:
    """迁移历史必须首尾相接——断链意味着历史在说谎。"""
    with pytest.raises(DomainError, match="迁移历史断链"):
        LearningGoal(
            id=GoalId("g1"),
            learner_id=LearnerId("l1"),
            course_id=CourseId("c1"),
            statement="掌握闭包",
            outcomes=("说清闭包是什么",),
            transitions=(
                _transition("active", "draft"),
                _transition("achieved", "draft"),
            ),
        )


def test_illegal_transition_in_history_is_rejected() -> None:
    """历史里出现非法迁移，说明构造方绕过了状态机。"""
    with pytest.raises(DomainError, match="包含非法迁移"):
        LearningGoal(
            id=GoalId("g1"),
            learner_id=LearnerId("l1"),
            course_id=CourseId("c1"),
            statement="掌握闭包",
            outcomes=("说清闭包是什么",),
            transitions=(_transition("achieved", "draft"),),
        )


def _transition(target: GoalStatus, source: GoalStatus) -> GoalTransition:
    """构造一条迁移记录（测试辅助）。"""
    return GoalTransition(source=source, target=target, reason="测试", at=AT)


# --- 约束 -----------------------------------------------------------------


def test_time_window_requires_end_after_start() -> None:
    """时间范围必须自洽。"""
    with pytest.raises(DomainError, match="必须晚于开始日期"):
        TimeWindow(start=date(2026, 2, 1), end=date(2026, 1, 1))


def test_time_window_has_passed_is_a_pure_query() -> None:
    """过期判定由调用方传入「现在」，领域层不读时钟。"""
    window = TimeWindow(start=date(2026, 1, 1), end=date(2026, 2, 1))

    assert window.has_passed(datetime(2026, 2, 2, 9, 0, tzinfo=UTC)) is True
    assert window.has_passed(datetime(2026, 2, 1, 23, 0, tzinfo=UTC)) is False


def test_daily_effort_requires_positive_minutes() -> None:
    """每日投入必须是正整数分钟。"""
    with pytest.raises(DomainError, match="必须是正整数分钟"):
        DailyEffort(minutes_per_day=0)


def test_goal_rejects_duplicate_requirements() -> None:
    """同一目标对同一个知识点的同一条结果只能有一条能力要求。"""
    with pytest.raises(DomainError, match="只能有一条能力要求"):
        _goal(requirements=(_requirement("k2"), _requirement("k2")))


def test_goal_rejects_requirement_outside_outcomes() -> None:
    """能力要求不另立结果——它必须指向目标已有的学习结果。"""
    with pytest.raises(DomainError, match="不在目标的学习结果中"):
        _goal(requirements=(_requirement("k2", "另一个结果"),))


def test_goal_rejects_duplicate_prerequisites() -> None:
    """前置条件不能重复。"""
    with pytest.raises(DomainError, match="前置条件不能重复"):
        _goal(prerequisites=(KnowledgePointId("k1"), KnowledgePointId("k1")))


def test_requirement_requires_non_empty_outcome() -> None:
    """能力要求必须说明「达到什么结果」。"""
    with pytest.raises(DomainError, match="能力要求的结果不能为空"):
        _requirement("k2", " ")


def test_goal_requires_verifiable_outcomes() -> None:
    """没有学习结果的目标是愿望，不是可验证的目标。"""
    with pytest.raises(DomainError, match="至少要有一条可验证的学习结果"):
        _goal(outcomes=())


# --- 绑定与跨聚合一致 -----------------------------------------------------


def test_goal_binds_knowledge_points_by_id() -> None:
    """目标绑定的知识点由能力要求派生，按声明顺序。"""
    goal = _goal(
        outcomes=("说清闭包捕获了什么", "能写出一个闭包例子", "说清作用域"),
        requirements=(_requirement("k2"), _requirement("k1", "说清作用域")),
    )

    assert goal.knowledge_point_ids == (KnowledgePointId("k2"), KnowledgePointId("k1"))


def test_goal_queries_binding_both_ways() -> None:
    """绑定关系可双向查询：结果→知识点，知识点→结果。"""
    goal = _goal(
        outcomes=("能写出一个闭包例子", "说清作用域"),
        requirements=(_requirement("k2"), _requirement("k1", "说清作用域")),
    )

    assert goal.points_for("能写出一个闭包例子") == (KnowledgePointId("k2"),)
    assert goal.outcomes_for(KnowledgePointId("k1")) == ("说清作用域",)
    assert goal.points_for("没绑定的结果") == ()


def test_check_goal_against_course_accepts_consistent_goal() -> None:
    """引用都存在时没有一致性问题。"""
    assert check_goal_against_course(_goal(), _course()) == ()


def test_check_goal_against_course_reports_unknown_requirement_point() -> None:
    """目标引用了课程里不存在的知识点——跨聚合引用完整性问题。"""
    goal = _goal(requirements=(_requirement("k9"),))

    issues = check_goal_against_course(goal, _course())

    assert len(issues) == 1
    assert issues[0].code == "unknown_requirement_point"
    assert "k9" in issues[0].message


def test_check_goal_against_course_reports_unknown_prerequisite_point() -> None:
    """前置条件同样要指向真实存在的知识点。"""
    goal = _goal(prerequisites=(KnowledgePointId("k0"),))

    issues = check_goal_against_course(goal, _course())

    assert len(issues) == 1
    assert issues[0].code == "unknown_prerequisite_point"


def test_check_goal_against_course_reports_course_mismatch() -> None:
    """目标绑定的课程不是给定课程时，后续校验没有意义，直接报告。"""
    goal = _goal(course_id=CourseId("course-other"))

    issues = check_goal_against_course(goal, _course())

    assert len(issues) == 1
    assert issues[0].code == "course_mismatch"


# --- 边界：领域模型 → 运行时 ----------------------------------------------


def test_to_goal_spec_maps_results_and_constraints() -> None:
    """学习结果进 outcomes；时间、投入、前置进 constraints；知识点绑定不进契约。"""
    goal = _goal(
        outcomes=("说清闭包捕获了什么", "能写出一个闭包例子"),
        requirements=(_requirement("k2"),),
        time_window=TimeWindow(start=date(2026, 1, 1), end=date(2026, 3, 1)),
        daily_effort=DailyEffort(minutes_per_day=90),
        prerequisites=(KnowledgePointId("k1"),),
    )

    spec = to_goal_spec(goal)

    assert spec.goal_id == "goal-closures"
    assert spec.statement == "掌握 JavaScript 闭包"
    assert spec.outcomes == ("说清闭包捕获了什么", "能写出一个闭包例子")
    assert spec.constraints == (
        "时间范围：2026-01-01 ~ 2026-03-01",
        "每日投入：90 分钟/天",
        "前置知识点：k1",
    )
    assert not hasattr(spec, "course_id")
