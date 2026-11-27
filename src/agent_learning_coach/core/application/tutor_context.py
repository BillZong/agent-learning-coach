"""Tutor 的上下文构建。

**上下文工程不是提示工程**（Anthropic《Effective context engineering for AI
agents》）：提示工程关心的是「怎么说」，上下文工程关心的是「**这一轮把哪些 token
送进模型**」——系统指令、工具、外部数据、消息历史，全都要管。

**上下文是有限资源，且边际收益递减**（context rot）：token 越多，模型从上下文里
准确回忆信息的能力越差。因此指导原则是「**用最小的高信号 token 集，最大化期望
结果**」。

本模块做的正是这件事：

1. **把教学状态分成几段**（画像 / 目标 / 进度 / 材料），每段各自可丢 ✓——
   Anthropic 的建议是「把提示分成若干段并用标记隔开」✓；
2. **按优先级取舍**：预算不够时，丢**优先级最低的整段**，而不是把每段都截断
   成半截 ✓——半截的目标陈述比没有目标更糟；
3. **如实报告丢了什么**——上下文构建的结果要能解释「为什么这次变短了」✓。

**对话历史不在这里**：那是运行时的转录（`AgentRuntime` 的事），本模块只管
**教学状态与依据**。两者分开，与「存储与呈现分离」（ADR-0008）是同一个原则 ✓。

**材料为什么由工具在运行时取**（just-in-time）：预检索会把一堆可能不相关的内容
塞进预算，而材料是「模型问了才给」——标记也由同一个登记簿分配，因此模型引用的
`[1]` 与校验器查的 `[1]` 永远是同一段文字（ADR-0012 的「标记稳定」）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from agent_learning_coach.core.context.tokens import TokenCounter
from agent_learning_coach.core.learning.goal import LearningGoal
from agent_learning_coach.core.learning.progress import LearningProgress, state_label
from agent_learning_coach.core.llm.types import ContentItem, Message
from agent_learning_coach.core.memory.types import LearnerProfile
from agent_learning_coach.core.rag.citation import CitationSet

QUESTION = "question"
"""本轮的问题。**必留**——丢了它这一轮就没有回答对象。"""

MATERIALS = "materials"
"""检索到的材料（教学依据）。"""

GOAL = "goal"
"""学习目标。"""

PROGRESS = "progress"
"""学习进度。"""

PROFILE = "profile"
"""学习者画像。"""


@dataclass(frozen=True)
class TutorContextRequest:
    """一次教学要带的上下文。

    **每一项都可以为空**：没有目标就没有目标段，没有画像就没有画像段。缺什么
    就少一段，**不用占位文字填满**。
    """

    question: str
    """学习者这一轮问的问题。"""

    profile: LearnerProfile | None = None
    """学习者画像（水平、强弱项、偏好）。"""

    goal: LearningGoal | None = None
    """学习目标（教学要朝它走）。"""

    progress: LearningProgress | None = None
    """学习进度（学习者现在在哪）。"""

    citations: CitationSet = field(default_factory=CitationSet)
    """本次要给的材料。标记必须与工具用的是**同一个登记簿**分配的 ✓。"""


@dataclass(frozen=True)
class TutorSection:
    """上下文里的一段。"""

    name: str
    """段名（``question`` / ``materials`` / ``goal`` / ``progress`` / ``profile``）。"""

    priority: int
    """优先级。**数字越小越重要**——预算不够时从大到小丢。"""

    content: str
    """渲染后的正文。"""

    tokens: int
    """这一段的 token 数。"""

    def to_item(self) -> Message:
        """转成一条消息。

        **材料与状态用 system 角色**：它们是「教练知道的背景」，不是学习者说的话。
        问题用 user 角色 ✓。

        Returns:
            消息。
        """
        role = "user" if self.name == QUESTION else "system"
        return Message(role=role, content=self.content)


@dataclass(frozen=True)
class TutorContext:
    """一次上下文构建的结果。"""

    sections: tuple[TutorSection, ...]
    """装进去的段落，按优先级排序。"""

    dropped: tuple[TutorSection, ...]
    """因为超预算被丢掉的段落。**不丢就没人知道为什么变短了** ✓。"""

    total_tokens: int
    """最终 token 数。"""

    @property
    def contents(self) -> tuple[ContentItem, ...]:
        """送给模型的内容。"""
        return tuple(section.to_item() for section in self.sections)

    @property
    def dropped_names(self) -> tuple[str, ...]:
        """被丢掉的段名。"""
        return tuple(section.name for section in self.dropped)

    def render(self) -> str:
        """渲染成可读的文本——用于日志与调试。

        Returns:
            逐段拼接的文本。
        """
        return "\n\n".join(section.content for section in self.sections)


def _render_profile(profile: LearnerProfile) -> str:
    """渲染学习者画像。

    Args:
        profile: 画像。

    Returns:
        正文。
    """
    lines = ["## 学习者画像"]
    if profile.level:
        lines.append(f"- 当前水平：{profile.level}")
    if profile.strengths:
        lines.append(f"- 强项：{'、'.join(profile.strengths)}")
    if profile.weaknesses:
        lines.append(f"- 薄弱点：{'、'.join(profile.weaknesses)}")
    if profile.preferences:
        lines.append(f"- 偏好：{'、'.join(profile.preferences)}")
    if profile.schedule:
        lines.append(f"- 可学习时段：{'、'.join(profile.schedule)}")
    return "\n".join(lines)


def _render_goal(goal: LearningGoal) -> str:
    """渲染学习目标。

    Args:
        goal: 目标。

    Returns:
        正文。
    """
    lines = ["## 学习目标", f"- 陈述：{goal.statement}"]
    if goal.outcomes:
        lines.append(f"- 可验证的结果：{'；'.join(goal.outcomes)}")
    if goal.requirements:
        points = "、".join(str(item.point_id) for item in goal.requirements)
        lines.append(f"- 能力要求涉及的知识点：{points}")
    if goal.time_window is not None:
        lines.append(f"- 时间范围：{goal.time_window.render()}")
    if goal.daily_effort is not None:
        lines.append(f"- 每日投入：{goal.daily_effort.render()}")
    return "\n".join(lines)


def _render_progress(progress: LearningProgress) -> str:
    """渲染学习进度。

    **只列已学的与正在学的**：未开始的知识点占地方又不指导教学 ✓。

    Args:
        progress: 进度。

    Returns:
        正文。
    """
    learned: list[str] = []
    learning: list[str] = []
    for point in progress.points:
        state = point.state
        if state == "learned":
            learned.append(str(point.point_id))
        elif state == "in_progress":
            learning.append(str(point.point_id))
    lines = ["## 学习进度"]
    lines.append(f"- {state_label('learned')}：{'、'.join(learned) if learned else '（暂无）'}")
    lines.append(
        f"- {state_label('in_progress')}：{'、'.join(learning) if learning else '（暂无）'}"
    )
    return "\n".join(lines)


def _render_materials(citations: CitationSet) -> str:
    """渲染材料。

    **格式与检索工具的输出一致**（`[1] 正文` + 来源行）——模型看到的标记与
    校验器查的标记因此是同一套 ✓。

    Args:
        citations: 引用集合。

    Returns:
        正文。
    """
    lines = ["## 课程材料", "（回答时请引用材料编号，如 [1]）"]
    for citation in citations.citations:
        lines.append(f"[{citation.marker}] {citation.text}")
        lines.append(f"（来源：{citation.source}#{citation.index}）")
    return "\n".join(lines)


@dataclass(frozen=True)
class TutorContextBuilder:
    """把教学状态装配成受预算约束的模型上下文。

    **取舍的顺序是「先丢整段，再谈截断」**：半截的目标陈述比没有目标更糟——
    模型会以为学习者只有半个目标 ✓。
    """

    max_tokens: int | None = None
    """单次请求的上下文预算（token）。为 None 表示不限制。"""

    counter: TokenCounter = field(default_factory=TokenCounter)
    """token 计数器。"""

    def build(self, request: TutorContextRequest) -> TutorContext:
        """构建上下文。

        Args:
            request: 上下文请求。

        Returns:
            上下文（含被丢掉的段落）。
        """
        sections = self._sections(request)
        if self.max_tokens is None:
            return TutorContext(
                sections=tuple(sections),
                dropped=(),
                total_tokens=self._total(sections),
            )

        kept = list(sections)
        dropped: list[TutorSection] = []
        # 优先级数字大的先丢；问题（优先级 0）永远不丢。
        while kept and self._total(kept) > self.max_tokens:
            victim = max(kept, key=lambda section: section.priority)
            if victim.priority == 0:
                break
            kept.remove(victim)
            dropped.append(victim)

        return TutorContext(
            sections=tuple(kept),
            dropped=tuple(dropped),
            total_tokens=self._total(kept),
        )

    def _sections(self, request: TutorContextRequest) -> list[TutorSection]:
        """按优先级生成各段。

        Args:
            request: 上下文请求。

        Returns:
            段落列表，按优先级排序。
        """
        drafts: list[tuple[str, int, str]] = [
            (QUESTION, 0, request.question),
        ]
        if request.citations.citations:
            drafts.append((MATERIALS, 1, _render_materials(request.citations)))
        if request.goal is not None:
            drafts.append((GOAL, 2, _render_goal(request.goal)))
        if request.progress is not None:
            drafts.append((PROGRESS, 3, _render_progress(request.progress)))
        if request.profile is not None:
            drafts.append((PROFILE, 4, _render_profile(request.profile)))

        return [
            TutorSection(
                name=name,
                priority=priority,
                content=content,
                tokens=self.counter.count([Message(role="system", content=content)]),
            )
            for name, priority, content in drafts
        ]

    def _total(self, sections: Sequence[TutorSection]) -> int:
        """算总 token 数。

        Args:
            sections: 段落。

        Returns:
            token 数。
        """
        return sum(section.tokens for section in sections)


__all__ = [
    "GOAL",
    "MATERIALS",
    "PROFILE",
    "PROGRESS",
    "QUESTION",
    "TutorContext",
    "TutorContextBuilder",
    "TutorContextRequest",
    "TutorSection",
]
