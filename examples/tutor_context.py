"""Tutor 的上下文：这一轮把哪些东西送进模型，以及预算不够时丢什么。

四段演示：

1. **装配**——画像、目标、进度、材料各占一段，问题是最后一段；
2. **优先级**——预算不够时，先丢优先级最低的（画像）；
3. **如实报告**——丢了什么要看得见；
4. **标记一致**——上下文里的 `[1]` 与检索工具给的是同一段文字。

**为什么值得单独做**：上下文是有限资源，且边际收益递减（context rot）——token 越多，
模型从上下文里准确回忆信息的能力越差。**用最小的高信号 token 集换最大期望结果**，
是这一层的全部工作。
"""

from __future__ import annotations

from datetime import date

from agent_learning_coach.core.application import (
    TutorContextBuilder,
    TutorContextRequest,
)
from agent_learning_coach.core.learning.domain import (
    CourseId,
    KnowledgePointId,
    LearnerId,
)
from agent_learning_coach.core.learning.goal import (
    CapabilityRequirement,
    DailyEffort,
    GoalId,
    LearningGoal,
    TimeWindow,
)
from agent_learning_coach.core.learning.progress import (
    LearningProgress,
    PointProgress,
    PointTransition,
)
from agent_learning_coach.core.memory.types import LearnerProfile
from agent_learning_coach.core.rag import CitationRegistry, CitationSet
from agent_learning_coach.core.rag.types import Chunk


def _goal() -> LearningGoal:
    """构造一个学习目标。

    Returns:
        目标。
    """
    return LearningGoal(
        id=GoalId("goal-1"),
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-1"),
        statement="三周内掌握闭包与作用域",
        outcomes=("能解释闭包捕获了什么",),
        requirements=(
            CapabilityRequirement(
                point_id=KnowledgePointId("k1"),
                outcome="能解释闭包捕获了什么",
            ),
        ),
        time_window=TimeWindow(start=date(2026, 1, 1), end=date(2026, 1, 20)),
        daily_effort=DailyEffort(minutes_per_day=60),
    )


def _progress() -> LearningProgress:
    """构造一份学习进度。

    Returns:
        进度。
    """
    return LearningProgress(
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-1"),
        points=(
            PointProgress(
                point_id=KnowledgePointId("k1"),
                transitions=(
                    PointTransition(
                        source="not_started",
                        target="learned",
                        source_kind="learned",
                        reason="练习通过",
                        at=1000.0,
                    ),
                ),
            ),
            PointProgress(
                point_id=KnowledgePointId("k2"),
                transitions=(
                    PointTransition(
                        source="not_started",
                        target="in_progress",
                        source_kind="learned",
                        reason="正在学",
                        at=2000.0,
                    ),
                ),
            ),
        ),
    )


def _profile() -> LearnerProfile:
    """构造一份学习者画像。

    Returns:
        画像。
    """
    return LearnerProfile(
        subject="learner-1",
        level="入门",
        strengths=("动手写代码",),
        weaknesses=("作用域",),
        preferences=("先看例子",),
    )


def _citations() -> CitationSet:
    """用**共享的登记簿**给一段材料分配标记。

    登记簿共享是这段演示的重点：上下文里的 `[1]` 与检索工具给的是同一段文字，
    模型引用的标记才能被校验器查到。

    Returns:
        引用集合。
    """
    registry = CitationRegistry()
    chunk = Chunk(
        id="c1",
        document_id="d1",
        text="闭包是函数加上它定义时所处的环境。即使函数已经返回，变量绑定仍然保留。",
        index=0,
        source="closures.md",
    )
    return CitationSet(citations=(registry.build(chunk, score=0.82),))


def show_assembly(request: TutorContextRequest) -> None:
    """演示装配。

    Args:
        request: 上下文请求。
    """
    print("=== 1. 装配：这一轮送进模型的东西 ===\n")
    context = TutorContextBuilder().build(request)

    for section in context.sections:
        print(f"[{section.priority}] {section.name}（{section.tokens} token）")
    print(f"合计 {context.total_tokens} token")
    print()
    print(context.render())
    print()
    print("（问题用 user 角色，材料与状态用 system 角色——它们是「教练知道的背景」）")
    print("（**缺什么就少一段**，不用占位文字填满）\n")


def show_priority(request: TutorContextRequest) -> None:
    """演示按优先级取舍。

    Args:
        request: 上下文请求。
    """
    print("=== 2. 优先级：预算不够时丢什么 ===\n")
    full = TutorContextBuilder().build(request)
    print("优先级（数字越小越重要）：")
    for section in full.sections:
        print(f"  {section.priority}  {section.name}")
    print()
    print("取舍顺序：**先丢整段，再谈截断**——半截的目标陈述比没有目标更糟。")
    print("（问题永远不丢：丢了就答不了）")
    print()
    budget = int(full.total_tokens * 0.8)
    trimmed = TutorContextBuilder(max_tokens=budget).build(request)
    print(f"预算 {budget} token 时：")
    print(f"  装进去：{[s.name for s in trimmed.sections]}")
    print(f"  丢掉：{list(trimmed.dropped_names)}")
    print()
    print("（**丢了什么要看得见**——否则「为什么这次变短了」没有答案）\n")


def show_markers(request: TutorContextRequest) -> None:
    """演示标记的一致性。

    Args:
        request: 上下文请求。
    """
    print("=== 3. 标记一致：上下文里的 [1] 就是材料那一段 ===\n")
    context = TutorContextBuilder().build(request)
    materials = next(s for s in context.sections if s.name == "materials")
    print(materials.content)
    print()
    print("（**登记簿共享**是关键：上下文与检索工具用同一份登记簿，")
    print("  模型引用的 [1] 与校验器查的 [1] 才是同一段文字）")
    print("（Day 31 的支撑门禁靠的就是这个）\n")


def main() -> None:
    """跑一遍 Tutor 上下文演示。"""
    request = TutorContextRequest(
        question="闭包为什么能记住外部变量？",
        profile=_profile(),
        goal=_goal(),
        progress=_progress(),
        citations=_citations(),
    )

    show_assembly(request)
    show_priority(request)
    show_markers(request)


if __name__ == "__main__":
    main()
