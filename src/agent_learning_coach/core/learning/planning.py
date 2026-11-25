"""学习计划：把目标、进度与诊断变成一份可执行的学习安排。

Topic 17～22 建好了「学什么」（目标、知识点、诊断判定），本模块回答「**按什么
顺序、每天花多久**」。

三个决定：

1. **计划单元与知识点一一对应。** 掌握度进度（ADR-0018）、诊断判定
   （ADR-0020）、知识依赖（ADR-0016）都以知识点为单位。计划单元如果不对齐到
   知识点，完成一个单元之后回写进度就要再做一次映射——而那次映射没有权威来源
   （哪些任务算掌握了 k1？）。**粒度不对齐，进度就写不回去。**
2. **计划范围由「目标 + 进度」决定，不由诊断决定。** 范围是
   「目标要求的点 ∪ 它们的前置闭包，减去已经学会的」。诊断是**判断**而不是事实：
   ADR-0020 已定「诊断到进度的衔接是应用层的动作」，因此领域层不替应用层选边，
   诊断只用来写清「这个点为什么在计划里」。
3. **排不下要报告，不能少排。** 时间装不下时，计划仍然包含全部单元，并给出
   超载报告——悄悄丢掉内容会让「要补的地方」凭空消失，这正是 ADR-0023 反对的
   宽松汇总。

领域层不认识模型、工具与运行时。计划交给运行时执行时，经 ``bridge`` 单向映射成
``GoalSpec``（ADR-0013 §10、ADR-0015）。

**与运行时计划不是一回事**：运行时计划（``core.agent.planning``）是「当前这一步
要做什么任务」，带状态机与历史；学习计划是「接下来学哪些知识点、每天多久」，
是领域事实。两者的粒度、生命周期与消费方都不同。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from agent_learning_coach.core.learning.diagnosis import Diagnosis, DiagnosticVerdict
from agent_learning_coach.core.learning.domain import (
    DiagnosisId,
    DomainError,
    GoalId,
    KnowledgePoint,
    KnowledgePointId,
    KnowledgeStructure,
    require_text,
)
from agent_learning_coach.core.learning.goal import LearningGoal
from agent_learning_coach.core.learning.progress import LearningProgress

DEFAULT_UNIT_MINUTES = 30
"""默认的单元估时（分钟）。

**没有实证依据**——它只是一个占位值，让「每天能学多少」这件事有个起点。真实
估时应来自学习数据（Topic 44）；在那之前，调用方可以注入自己的
``EffortEstimator``。
"""


class EffortEstimator(Protocol):
    """学习投入估算器——估算一个知识点要学多久。

    领域层定义「需要估什么」，不规定「怎么估」——与 ADR-0023 的 ``Grader``、
    ADR-0002 的 ``LLMClient`` 是同一个做法。默认实现是统一时长；换成模型估算
    或按历史数据估算都不需要改领域模型。
    """

    def estimate(self, point: KnowledgePoint) -> int:
        """估算学习某个知识点需要的分钟数。

        Args:
            point: 知识点。

        Returns:
            预计分钟数，必须是正整数。
        """
        ...


class UniformEffort:
    """统一时长的估算器——每个知识点估一样的分钟数。

    Args:
        minutes: 每个知识点的估时。

    Raises:
        DomainError: 分钟数不是正整数。
    """

    def __init__(self, minutes: int = DEFAULT_UNIT_MINUTES) -> None:
        """初始化估算器。

        Args:
            minutes: 每个知识点的估时。

        Raises:
            DomainError: 分钟数不是正整数。
        """
        if minutes <= 0:
            raise DomainError(f"单元估时必须是正整数分钟：{minutes}")
        self.minutes = minutes

    def estimate(self, point: KnowledgePoint) -> int:
        """返回统一估时。

        Args:
            point: 知识点（未使用）。

        Returns:
            估时分钟数。
        """
        return self.minutes


@dataclass(frozen=True)
class LearningUnit:
    """学习单元——**值对象**。

    **一个单元对应一个知识点**。它没有独立 id：知识点就是它的身份，因此计划
    里不会出现两个单元指向同一个点，也不会出现「这个单元到底算不算掌握了 k1」
    的疑问。

    它回答四件事：学什么（``point_id`` / ``title``）、为什么学（``reason``）、
    支撑目标的哪条结果（``outcomes``）、什么时候学（``day``）。
    """

    point_id: KnowledgePointId
    """这个单元讲哪个知识点。"""

    title: str
    """知识点名称。"""

    reason: str
    """它为什么在计划里，必填——「为什么先学这个」要能回答。

    三种来源：诊断判定为缺口、目标要求掌握、它是别的单元的前置。
    """

    outcomes: tuple[str, ...] = ()
    """它支撑目标的学习结果；不是目标要求的点则为空。"""

    estimated_minutes: int = DEFAULT_UNIT_MINUTES
    """预计投入分钟数。"""

    depends_on: tuple[KnowledgePointId, ...] = ()
    """它依赖的单元——**只含计划内的前置**。

    已经学会的前置不在这里：那些前置已经被满足，不构成计划内的依赖。
    """

    day: int | None = None
    """排在第几天（从 0 开始）；``None`` 表示**没有时间约束可依据，未排期**。

    没有约定每日投入时无从排期——硬排一个「第 0 天装下全部内容」的计划是假
    精度。
    """

    def __post_init__(self) -> None:
        """校验单元内容。

        Raises:
            DomainError: 标题或理由为空；估时不是正整数；排期天数为负。
        """
        require_text("学习单元标题", self.title)
        require_text("学习单元在计划里的理由", self.reason)
        for outcome in self.outcomes:
            require_text("学习单元支撑的学习结果", outcome)
        if self.estimated_minutes <= 0:
            raise DomainError(f"学习单元的预计时长必须是正整数分钟：{self.estimated_minutes}")
        if self.day is not None and self.day < 0:
            raise DomainError(f"学习单元的排期天数不能为负：{self.day}")


@dataclass(frozen=True)
class DailyLoad:
    """某一天的负载——**值对象**。

    回答「这一天要学多久、有没有超出每天能投入的时间」。
    """

    day: int
    """第几天（从 0 开始）。"""

    minutes: int
    """这一天计划的分钟数。"""

    capacity: int | None = None
    """这一天能投入的上限；``None`` 表示没有约定每日投入。"""

    within_window: bool = True
    """这一天是否落在目标的时间范围内。

    为 ``False`` 意味着计划已经排过了期限——超载报告会把这些天标出来。
    """

    @property
    def is_overloaded(self) -> bool:
        """这一天是否超出每日投入上限。"""
        return self.capacity is not None and self.minutes > self.capacity


@dataclass(frozen=True)
class PlanOverload:
    """时间超载——**值对象**。

    计划的总量装不进目标的时间范围。它**不是错误**：目标本身是自洽的，装不下是
    一个需要人来决定的事实（加时间、延期限，还是砍范围）。
    """

    total_minutes: int
    """计划的总分钟数。"""

    required_days: int
    """按每日投入，装下全部内容需要的天数。"""

    available_days: int
    """目标时间范围内可用的天数。"""

    daily_capacity: int
    """每天的投入上限。"""

    @property
    def shortfall_days(self) -> int:
        """超出期限的天数。"""
        return self.required_days - self.available_days


@dataclass(frozen=True)
class LearningPlan:
    """学习计划——**值对象**。

    它是**派生**的：给定目标、知识结构与进度就能算出来，因此不需要独立身份，
    也不自己维护状态。计划的**历史与版本**属于 Topic 24。

    它**不替调用方做决定**：时间装不下（``overload``）、目标的前置还没学会
    （``unmet_prerequisites``）都只被**报告**，怎么处理是应用层的事
    （ADR-0017 第三档）。
    """

    goal_id: GoalId
    """计划服务于哪个目标。"""

    units: tuple[LearningUnit, ...] = ()
    """学习单元，按学习顺序排列。"""

    days: tuple[DailyLoad, ...] = ()
    """每天的负载；没有约定每日投入时为空。"""

    overload: PlanOverload | None = None
    """时间超载报告；装得下时为空。"""

    unmet_prerequisites: tuple[KnowledgePointId, ...] = ()
    """目标的前置条件中还没学会的知识点。

    目标的前置是「**开始这个目标前必须已经会**」，不是「要学的内容」——因此
    它们不进计划范围，只被报告。补它们属于另一个目标。
    """

    diagnosis_id: DiagnosisId | None = None
    """生成计划时参考的诊断；没有诊断时为空。

    它是计划**生成依据**的一部分（Topic 24 要把它存下来）。
    """

    def __post_init__(self) -> None:
        """校验计划自洽。

        Raises:
            DomainError: 同一个知识点出现多个单元；单元依赖不在计划内；依赖排期
                在被依赖的单元之后。
        """
        seen: set[KnowledgePointId] = set()
        for unit in self.units:
            if unit.point_id in seen:
                raise DomainError(f"学习计划里知识点 {unit.point_id} 出现了多个单元")
            seen.add(unit.point_id)

        by_id = {unit.point_id: unit for unit in self.units}
        for unit in self.units:
            for prerequisite in unit.depends_on:
                other = by_id.get(prerequisite)
                if other is None:
                    raise DomainError(
                        f"学习单元 {unit.point_id} 依赖的知识点 {prerequisite} 不在计划内",
                    )
                if unit.day is not None and other.day is not None and other.day > unit.day:
                    raise DomainError(
                        f"学习单元 {unit.point_id} 排在了它的前置 {prerequisite} 之前",
                    )

    @property
    def total_minutes(self) -> int:
        """计划的总投入分钟数。"""
        return sum(unit.estimated_minutes for unit in self.units)

    def unit_for(self, point_id: KnowledgePointId) -> LearningUnit | None:
        """取某个知识点的单元。

        Args:
            point_id: 知识点标识。

        Returns:
            对应的学习单元；不在计划里则为 ``None``。
        """
        for unit in self.units:
            if unit.point_id == point_id:
                return unit
        return None

    def units_for_outcome(self, outcome: str) -> tuple[LearningUnit, ...]:
        """取支撑某条学习结果的单元。

        Args:
            outcome: 一条学习结果。

        Returns:
            支撑该结果的单元，按计划顺序。
        """
        return tuple(unit for unit in self.units if outcome in unit.outcomes)


def generate_plan(
    goal: LearningGoal,
    structure: KnowledgeStructure,
    progress: LearningProgress,
    *,
    diagnosis: Diagnosis | None = None,
    estimator: EffortEstimator | None = None,
) -> LearningPlan:
    """生成学习计划。

    计划范围 = **目标要求的知识点 ∪ 它们的前置闭包 − 已经学会的知识点**。

    为什么带上前置闭包：目标要求 k3，而 k3 依赖 k2、k2 依赖 k1——不学会 k1、
    k2 就学不了 k3。不带闭包的计划是**执行不了**的。带上前置不会扩大目标
    （掌握 k3 本来就意味着会 k1、k2），但会让每个单元的 ``reason`` 说清
    「它为什么在计划里」。

    为什么减去已学会的：已学会的点不需要再排一次时间。

    顺序 = 知识结构的拓扑序（ADR-0016 的 ``order()``）限制到计划范围内——
    **依赖是硬约束**，拓扑序是唯一能保证「前置在前」的确定性顺序，并列时保持
    声明顺序。

    Args:
        goal: 学习目标。
        structure: 知识结构，提供依赖关系与学习顺序。
        progress: 学习进度，提供「已经会哪些」。
        diagnosis: 诊断，只用于写清每个单元「为什么在计划里」。
        estimator: 估时器；不给则用统一时长。

    Returns:
        学习计划。

    Raises:
        DomainError: 进度与目标不属于同一学习者或同一门课；目标要求的知识点不
            在知识结构中。
    """
    if progress.learner_id != goal.learner_id:
        raise DomainError(
            f"进度属于学习者 {progress.learner_id}，目标属于学习者 {goal.learner_id}，无法排计划",
        )
    if progress.course_id != goal.course_id:
        raise DomainError(
            f"进度属于课程 {progress.course_id}，目标属于课程 {goal.course_id}，无法排计划",
        )

    effort = estimator if estimator is not None else UniformEffort()

    goal_points = tuple(dict.fromkeys(goal.knowledge_point_ids))
    for point_id in goal_points:
        structure.point(point_id)

    scope: set[KnowledgePointId] = set()
    for point_id in goal_points:
        scope.add(point_id)
        scope.update(prerequisite.id for prerequisite in structure.prerequisite_closure(point_id))

    learned = set(progress.learned_points())
    planned_points = [
        point for point in structure.order() if point.id in scope and point.id not in learned
    ]
    planned_ids = {point.id for point in planned_points}

    dependents = _direct_dependents(structure, planned_ids)
    verdicts = _verdicts_by_point(diagnosis)

    units: list[LearningUnit] = []
    for point in planned_points:
        depends_on = tuple(
            prerequisite.id
            for prerequisite in structure.prerequisites_of(point.id)
            if prerequisite.id in planned_ids
        )
        units.append(
            LearningUnit(
                point_id=point.id,
                title=point.title,
                reason=_reason_for(point.id, goal, structure, dependents, verdicts),
                outcomes=goal.outcomes_for(point.id),
                estimated_minutes=effort.estimate(point),
                depends_on=depends_on,
            ),
        )

    days, scheduled = _schedule(units, goal)
    overload = _overload(days, goal)
    unmet = tuple(point_id for point_id in goal.prerequisites if point_id not in learned)

    return LearningPlan(
        goal_id=goal.id,
        units=scheduled,
        days=days,
        overload=overload,
        unmet_prerequisites=unmet,
        diagnosis_id=diagnosis.id if diagnosis is not None else None,
    )


def _schedule(
    units: Sequence[LearningUnit],
    goal: LearningGoal,
) -> tuple[tuple[DailyLoad, ...], tuple[LearningUnit, ...]]:
    """把单元按每日投入排进天里。

    贪心装箱：当前天放得下就放，放不下就开新天。因此**每一天都不超过每日
    投入**——除非单个单元本身就超过它（那天的 ``is_overloaded`` 为真）。

    没有约定每日投入时**不排期**：硬排一个「第 0 天装下全部内容」是假精度。

    Args:
        units: 学习单元（按学习顺序）。
        goal: 学习目标，提供每日投入与时间范围。

    Returns:
        每天的负载与排期后的单元。
    """
    if goal.daily_effort is None:
        return (), tuple(units)

    capacity = goal.daily_effort.minutes_per_day
    available_days = _available_days(goal)

    scheduled: list[LearningUnit] = []
    loads: list[DailyLoad] = []
    day = 0
    minutes = 0
    for unit in units:
        if minutes > 0 and minutes + unit.estimated_minutes > capacity:
            loads.append(_load(day, minutes, capacity, available_days))
            day += 1
            minutes = 0
        minutes += unit.estimated_minutes
        scheduled.append(replace(unit, day=day))
    if minutes > 0 or not units:
        loads.append(_load(day, minutes, capacity, available_days))
    return tuple(loads), tuple(scheduled)


def _load(
    day: int,
    minutes: int,
    capacity: int,
    available_days: int | None,
) -> DailyLoad:
    """构造某一天的负载。

    Args:
        day: 第几天。
        minutes: 计划的分钟数。
        capacity: 每日投入上限。
        available_days: 时间范围内的可用天数；没有时间范围时为空。

    Returns:
        当天的负载。
    """
    within = True if available_days is None else day < available_days
    return DailyLoad(
        day=day,
        minutes=minutes,
        capacity=capacity,
        within_window=within,
    )


def _available_days(goal: LearningGoal) -> int | None:
    """目标时间范围内的可用天数。

    结束日期当天是可以用的（``TimeWindow.has_passed`` 在 ``now.date() > end``
    时才过期），因此天数是「相隔天数 + 1」。

    Args:
        goal: 学习目标。

    Returns:
        可用天数；没有时间范围时为空。
    """
    if goal.time_window is None:
        return None
    return (goal.time_window.end - goal.time_window.start).days + 1


def _overload(
    days: Sequence[DailyLoad],
    goal: LearningGoal,
) -> PlanOverload | None:
    """判断计划是否超出时间范围。

    Args:
        days: 每天的负载。
        goal: 学习目标。

    Returns:
        超载报告；装得下时为空。
    """
    available = _available_days(goal)
    if available is None or not days or goal.daily_effort is None:
        return None
    required = len(days)
    if required <= available:
        return None
    return PlanOverload(
        total_minutes=sum(load.minutes for load in days),
        required_days=required,
        available_days=available,
        daily_capacity=goal.daily_effort.minutes_per_day,
    )


def _direct_dependents(
    structure: KnowledgeStructure,
    planned_ids: set[KnowledgePointId],
) -> dict[KnowledgePointId, tuple[KnowledgePointId, ...]]:
    """计划内每个知识点被哪些知识点直接依赖。

    Args:
        structure: 知识结构。
        planned_ids: 计划内的知识点。

    Returns:
        知识点 → 直接依赖它的计划内知识点。
    """
    dependents: dict[KnowledgePointId, list[KnowledgePointId]] = {
        point_id: [] for point_id in planned_ids
    }
    for point in structure.order():
        if point.id not in planned_ids:
            continue
        for prerequisite in structure.prerequisites_of(point.id):
            if prerequisite.id in planned_ids:
                dependents[prerequisite.id].append(point.id)
    return {point_id: tuple(items) for point_id, items in dependents.items()}


def _reason_for(
    point_id: KnowledgePointId,
    goal: LearningGoal,
    structure: KnowledgeStructure,
    dependents: dict[KnowledgePointId, tuple[KnowledgePointId, ...]],
    verdicts: dict[KnowledgePointId, DiagnosticVerdict],
) -> str:
    """说明一个知识点为什么在计划里。

    优先说诊断结论（那是「实际不会」），其次说目标要求，最后说它是别人的前置
    ——三者的信息量依次递减，但都比「因为它在范围里」有用。

    Args:
        point_id: 知识点标识。
        goal: 学习目标。
        structure: 知识结构。
        dependents: 直接依赖关系。
        verdicts: 诊断判定，按知识点索引。

    Returns:
        理由文本。
    """
    verdict = verdicts.get(point_id)
    if verdict is not None and verdict.verdict == "gap":
        return f"诊断判定为缺口：{verdict.reason}"

    if point_id in goal.knowledge_point_ids:
        outcomes = goal.outcomes_for(point_id)
        if outcomes:
            return f"目标要求掌握它以支撑：{'、'.join(outcomes)}"
        return "目标要求掌握它"

    required_by = dependents.get(point_id, ())
    if required_by:
        names = "、".join(str(item) for item in required_by)
        return f"它是 {names} 的前置"
    return "它在目标的前置路径上"


def _verdicts_by_point(
    diagnosis: Diagnosis | None,
) -> dict[KnowledgePointId, DiagnosticVerdict]:
    """把诊断判定按知识点索引。

    Args:
        diagnosis: 诊断；没有诊断时为空。

    Returns:
        知识点 → 判定。
    """
    if diagnosis is None:
        return {}
    return {verdict.point_id: verdict for verdict in diagnosis.verdicts}


__all__ = [
    "DEFAULT_UNIT_MINUTES",
    "DailyLoad",
    "EffortEstimator",
    "LearningPlan",
    "LearningUnit",
    "PlanOverload",
    "UniformEffort",
    "generate_plan",
]
