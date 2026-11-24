"""计划与反思：把「做什么」与「做得对不对」分成两件事。

计划（Plan）是**做什么**：把一个目标拆成有依赖关系的任务，并记录每个任务的
状态。反思（Reflection）是**做得对不对**：对已经发生的事做一次判断。

两者的分工是本模块的核心设计。教材里的实现把两者都做成工具：``create_tasks``
让模型**整份重写**任务列表，``reflection`` 只是把一段分析文字回显给模型。
短任务上没问题，但一旦需要重规划，模型每次都要重写整份清单——已完成的任务
可能被丢掉，新任务可能被重复添加，任务顺序随每次改写漂移。**计划越改越乱**，
根因不在模型，而在于「计划」是一段可以被整体覆盖的文本，而不是一个带状态
与约束的对象。

本模块把计划变成对象：

- **任务有稳定 id**（``t1``、``t2``……）：引用的是 id，不是文字。
- **状态机**：``skipped`` 是唯一终态；其余状态之间可迁移，每次迁移都被记录。
- **内容不可改写**：任务文字一旦定下就不改；要换说法就新建任务并跳过旧的。
- **不删任务**：移除一个任务 = 把它标成 ``skipped``，它仍然留在计划里。
- **同一时刻只有一个进行中的任务**：模型不能同时推进三件事。
- **依赖必须闭合且无环**：前置任务没完成就不能开始依赖它的任务。
- **结构性修改有预算**：新增 / 跳过 / 重开 / 改依赖算「重规划」，消耗预算；
  单纯推进状态不消耗。
- **反思不改计划**：反思只产出判断；改计划是另一步，且必须引用那条反思。

最后两条合起来回答了本主题的问题：**反思与重规划是两步，不是一步**。模型说
「需要重规划」之后，下一次结构性修改必须引用那条反思——于是「为什么改」永远
有出处，而预算保证改的次数有限。

职责边界：本模块不认识学习领域的具体概念。它只处理「目标描述 → 任务列表 →
状态与历史」这条链路；Phase 1 的领域模型通过 ``GoalSpec`` 映射进来。
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from agent_learning_coach.core.agent.types import EXECUTION_CONTEXT_KEY, ExecutionContext
from agent_learning_coach.core.llm.models import LLMRequest
from agent_learning_coach.core.llm.protocol import LLMClient
from agent_learning_coach.core.llm.types import Message
from agent_learning_coach.core.tools.types import (
    FunctionTool,
    ToolContext,
    ToolDefinition,
    ToolOutcome,
)

TaskStatus = Literal["pending", "in_progress", "blocked", "completed", "skipped"]
"""任务状态。

``skipped`` 是**唯一终态**：任务一旦被跳过就不能再回到进行中——想恢复就新建
一个任务。其余状态之间可以迁移，每次迁移都被记进版本历史。
"""

ReflectionVerdict = Literal["continue", "accept", "replan", "blocked"]
"""反思结论。

- ``continue``：进展正常，继续推进。
- ``accept``：当前目标已完成。
- ``replan``：需要修改计划——触发一次重规划预算，并要求下一次结构性修改
  引用这条反思。
- ``blocked``：缺少外部条件，无法继续。
"""

RevisionKind = Literal["initial", "progress", "replan"]
"""修订类型。

``progress`` 是单纯推进状态，``replan`` 是结构性修改。**只有 ``replan`` 消耗
重规划预算**——正常推进不该被当成「又改了一次计划」。
"""

PLAN_STATE_KEY = "learning_plan"
"""``ExecutionContext.state`` 中存放计划的键。

计划放在草稿本里，因此它随会话快照一起持久化，也随会话恢复一起回来。
"""

UPDATE_PLAN_TOOL_NAME = "update_plan"
"""更新计划的工具名。"""

REFLECT_TOOL_NAME = "reflect"
"""记录反思的工具名。"""

_STATUS_MARKERS: dict[str, str] = {
    "pending": "[ ]",
    "in_progress": "[>]",
    "completed": "[x]",
    "blocked": "[!]",
    "skipped": "[-]",
}

_STATUS_LABELS: dict[str, str] = {
    "pending": "待办",
    "in_progress": "进行中",
    "completed": "已完成",
    "blocked": "受阻",
    "skipped": "已跳过",
}


class PlanError(Exception):
    """计划操作被拒绝。

    错误信息是**给模型看的**——它会被归一化成工具结果回填给模型，模型据此
    调整下一步。因此消息要说明「哪里不对」与「怎么改」，而不只是「失败了」。
    """


def normalize_content(text: str) -> str:
    """归一化任务文本，用于判重。

    折叠空白、忽略大小写与标点，因此「闭包的概念」与「闭包  的概念！」会被
    判为同一个任务。

    Args:
        text: 原始文本。

    Returns:
        归一化后的文本。
    """
    return re.sub(r"[\s\W_]+", "", text.casefold(), flags=re.UNICODE)


@dataclass(frozen=True)
class PlanTask:
    """计划里的一个任务。"""

    task_id: str
    """任务标识。稳定不变，引用任务靠它而不是文字。"""

    content: str
    """任务内容。**不可改写**——要换说法就新建任务并跳过旧的。"""

    status: TaskStatus = "pending"
    """任务状态。"""

    depends_on: tuple[str, ...] = ()
    """前置任务 id。前置任务未完成时不能开始本任务。"""

    created_revision: int = 0
    """创建时所在的修订号。"""

    updated_revision: int = 0
    """最近一次修改所在的修订号。"""


@dataclass(frozen=True)
class AppliedChange:
    """一次修订里落地的单个改动。"""

    task_id: str
    """被改动的任务。"""

    action: Literal["created", "status", "dependencies"]
    """改动类型。"""

    before: str
    """改动前的值。"""

    after: str
    """改动后的值。"""


@dataclass(frozen=True)
class PlanRevision:
    """计划的一次修订。"""

    revision: int
    """修订号，从 1 开始递增。"""

    kind: RevisionKind
    """修订类型。"""

    changes: tuple[AppliedChange, ...]
    """本次落地的改动。"""

    rationale: str
    """改动理由。**必填**——没有理由的修改无法审计。"""

    reflection_id: str | None = None
    """触发这次修订的反思 id（如果有）。"""

    timestamp: float = 0.0
    """修订时刻。"""


@dataclass(frozen=True)
class Reflection:
    """一次反思。"""

    reflection_id: str
    """反思标识，``r1``、``r2``……顺序分配。"""

    verdict: ReflectionVerdict
    """反思结论。"""

    assessment: str
    """判断依据。"""

    evidence: tuple[str, ...] = ()
    """支撑判断的证据。"""

    target_task_id: str | None = None
    """反思针对的任务。None 表示针对整体。"""

    created_at: float = 0.0
    """反思时刻。"""

    def to_dict(self) -> dict[str, Any]:
        """序列化成可 JSON 化的字典。

        Returns:
            字典。
        """
        return {
            "reflection_id": self.reflection_id,
            "verdict": self.verdict,
            "assessment": self.assessment,
            "evidence": list(self.evidence),
            "target_task_id": self.target_task_id,
            "created_at": self.created_at,
        }


@dataclass
class Plan:
    """一份学习计划。

    它是**可版本化的对象**，不是一段文本：每次修改都产生新的修订号，旧修订
    留在历史里。这样「计划为什么变成现在这样」永远有答案。
    """

    plan_id: str
    """计划标识。"""

    goal: str
    """计划要达成的目标。"""

    tasks: list[PlanTask] = field(default_factory=list)
    """任务列表，**只追加**，保持创建顺序。

    这是历史顺序：修订历史与「谁先存在」都以它为准。展示顺序由
    `render_plan` 另行计算，调度由 `startable_tasks()` 另行判断——三者
    不共用同一个排序结果。
    """

    revision: int = 0
    """当前修订号。"""

    revisions: list[PlanRevision] = field(default_factory=list)
    """修订历史。"""

    reflections: list[Reflection] = field(default_factory=list)
    """反思记录。"""

    replan_count: int = 0
    """已消耗的重规划次数。"""

    pending_replan_id: str | None = None
    """待处理的重规划请求——来自一条 ``verdict="replan"`` 的反思。

    存在时，下一次结构性修改必须引用它，否则被拒绝。
    """

    created_at: float = 0.0
    """创建时刻。"""

    updated_at: float = 0.0
    """最近更新时刻。"""

    def task(self, task_id: str) -> PlanTask | None:
        """按 id 查找任务。

        Args:
            task_id: 任务标识。

        Returns:
            任务；不存在时返回 None。
        """
        for item in self.tasks:
            if item.task_id == task_id:
                return item
        return None

    def status_counts(self) -> dict[str, int]:
        """统计各状态的任务数。

        Returns:
            状态到数量的映射，只包含出现过的状态。
        """
        counts: dict[str, int] = {}
        for item in self.tasks:
            counts[item.status] = counts.get(item.status, 0) + 1
        return counts

    def startable_tasks(self) -> list[PlanTask]:
        """可以现在开始的任务——**调度问的是就绪，不是顺序**。

        一个任务可以开始，当且仅当它还没开始过（``pending``）且前置全部
        ``completed``。返回列表沿用创建顺序，只是为了遍历稳定；这个顺序没有
        语义，不要拿它当执行计划。

        与展示顺序无关：展示回答「从哪读到哪」，这里回答「现在能动手做什么」。
        两者不一致是正常的——跳过、重开、重规划都会改变就绪集合。

        Returns:
            可以开始的任务；没有时返回空列表。
        """
        statuses = {item.task_id: item.status for item in self.tasks}
        return [
            item
            for item in self.tasks
            if item.status == "pending" and not _unmet_dependencies(item, statuses)
        ]

    def to_dict(self) -> dict[str, Any]:
        """序列化成可 JSON 化的字典。

        Returns:
            字典。
        """
        return {
            "plan_id": self.plan_id,
            "goal": self.goal,
            "tasks": [
                {
                    "task_id": item.task_id,
                    "content": item.content,
                    "status": item.status,
                    "depends_on": list(item.depends_on),
                    "created_revision": item.created_revision,
                    "updated_revision": item.updated_revision,
                }
                for item in self.tasks
            ],
            "revision": self.revision,
            "revisions": [
                {
                    "revision": item.revision,
                    "kind": item.kind,
                    "changes": [
                        {
                            "task_id": change.task_id,
                            "action": change.action,
                            "before": change.before,
                            "after": change.after,
                        }
                        for change in item.changes
                    ],
                    "rationale": item.rationale,
                    "reflection_id": item.reflection_id,
                    "timestamp": item.timestamp,
                }
                for item in self.revisions
            ],
            "reflections": [
                {
                    "reflection_id": item.reflection_id,
                    "verdict": item.verdict,
                    "assessment": item.assessment,
                    "evidence": list(item.evidence),
                    "target_task_id": item.target_task_id,
                    "created_at": item.created_at,
                }
                for item in self.reflections
            ],
            "replan_count": self.replan_count,
            "pending_replan_id": self.pending_replan_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Plan:
        """从字典还原。

        Args:
            data: 序列化后的计划。

        Returns:
            计划。
        """
        return cls(
            plan_id=str(data["plan_id"]),
            goal=str(data["goal"]),
            tasks=[
                PlanTask(
                    task_id=str(item["task_id"]),
                    content=str(item["content"]),
                    status=item["status"],
                    depends_on=tuple(str(dep) for dep in item.get("depends_on", ())),
                    created_revision=int(item.get("created_revision", 0)),
                    updated_revision=int(item.get("updated_revision", 0)),
                )
                for item in data.get("tasks", [])
            ],
            revision=int(data.get("revision", 0)),
            revisions=[
                PlanRevision(
                    revision=int(item["revision"]),
                    kind=item["kind"],
                    changes=tuple(
                        AppliedChange(
                            task_id=str(change["task_id"]),
                            action=change["action"],
                            before=str(change.get("before", "")),
                            after=str(change.get("after", "")),
                        )
                        for change in item.get("changes", [])
                    ),
                    rationale=str(item.get("rationale", "")),
                    reflection_id=item.get("reflection_id"),
                    timestamp=float(item.get("timestamp", 0.0)),
                )
                for item in data.get("revisions", [])
            ],
            reflections=[
                Reflection(
                    reflection_id=str(item["reflection_id"]),
                    verdict=item["verdict"],
                    assessment=str(item.get("assessment", "")),
                    evidence=tuple(str(value) for value in item.get("evidence", ())),
                    target_task_id=item.get("target_task_id"),
                    created_at=float(item.get("created_at", 0.0)),
                )
                for item in data.get("reflections", [])
            ],
            replan_count=int(data.get("replan_count", 0)),
            pending_replan_id=data.get("pending_replan_id"),
            created_at=float(data.get("created_at", 0.0)),
            updated_at=float(data.get("updated_at", 0.0)),
        )


@dataclass(frozen=True)
class TaskDraft:
    """任务草稿——尚未落到计划里的任务提案。"""

    content: str
    """任务内容。"""

    depends_on: tuple[int, ...] = ()
    """前置任务的**下标**（在草稿列表中的位置）。

    用下标而不是 id：草稿还没有 id，而拆解结果天然是「按顺序的一列任务」，
    下标是此时最自然的引用方式。落到计划时下标会被翻译成任务 id。
    """


@dataclass(frozen=True)
class PlanDraft:
    """计划草稿——拆解的结果，还没经过状态机校验。"""

    goal: str
    """目标。"""

    tasks: tuple[TaskDraft, ...] = ()
    """任务草稿列表。"""


@dataclass(frozen=True)
class GoalSpec:
    """学习目标的运行时描述。

    它是**领域无关**的：Phase 1 的 ``LearningGoal`` 领域模型通过它映射进来，
    ``core.agent`` 因此不需要认识学习领域的任何具体概念。
    """

    goal_id: str
    """目标标识。"""

    statement: str
    """目标陈述。"""

    outcomes: tuple[str, ...] = ()
    """可验证的学习结果。"""

    constraints: tuple[str, ...] = ()
    """约束条件（时间、先修要求……）。"""


@dataclass(frozen=True)
class TaskChange:
    """一次计划修改的意图。"""

    task_id: str | None = None
    """要修改的任务。None 表示新增任务。"""

    new_task_id: str | None = None
    """新增任务时指定的 id。

    默认自动分配（``t1``、``t2``……）。**指定 id 的唯一理由**是让同一批修改
    里别的任务能依赖它——自动分配的 id 模型事先不知道，指定了才写得出来。
    """

    content: str = ""
    """新任务的内容。已有任务的 content 会被拒绝。"""

    status: TaskStatus | None = None
    """目标状态。None 表示不改状态。"""

    depends_on: tuple[str, ...] | None = None
    """新的前置任务。None 表示不改依赖。"""


@dataclass(frozen=True)
class PlanPolicy:
    """计划与反思的额度约束。"""

    max_replans: int = 3
    """最多允许几次结构性修改。"""

    max_reflections: int = 6
    """最多允许几次反思。"""

    max_reflections_per_task: int = 2
    """同一个任务最多允许几次反思。"""


@runtime_checkable
class Planner(Protocol):
    """规划器协议：把目标拆成任务草稿。"""

    async def plan(self, goal: GoalSpec) -> PlanDraft:
        """把目标拆成任务草稿。

        Args:
            goal: 目标描述。

        Returns:
            计划草稿。
        """
        ...


class OutcomePlanner:
    """确定性拆解：一个学习结果一个任务，按顺序串联。

    它是 **LLM 拆解的基线**——模型拆出来的计划至少不该差于「按结果逐条推进」。
    同时它让上层在没有模型可用时也能拿到一份可执行计划。
    """

    def __init__(self, *, chain: bool = True) -> None:
        """初始化拆解器。

        Args:
            chain: 是否把任务按顺序串联（后一个依赖前一个）。
        """
        self.chain = chain

    async def plan(self, goal: GoalSpec) -> PlanDraft:
        """按学习结果逐条拆解。

        Args:
            goal: 目标描述。

        Returns:
            计划草稿。

        Raises:
            PlanError: 目标没有任何学习结果。
        """
        if not goal.outcomes:
            raise PlanError("目标没有可拆解的学习结果（outcomes 为空）")
        drafts: list[TaskDraft] = []
        for index, outcome in enumerate(goal.outcomes):
            depends = (index - 1,) if (self.chain and index > 0) else ()
            drafts.append(TaskDraft(content=outcome, depends_on=depends))
        return PlanDraft(goal=goal.statement, tasks=tuple(drafts))


class ProposedTask(BaseModel):
    """模型提出的一个任务。"""

    content: str
    """任务内容。"""

    depends_on: list[int] = Field(default_factory=list)
    """前置任务的下标（从 0 开始）。只能引用更小下标。"""


class TaskDecomposition(BaseModel):
    """模型给出的任务拆解结果。"""

    tasks: list[ProposedTask] = Field(default_factory=list)
    """任务列表。"""


_DECOMPOSITION_INSTRUCTION = """\
你是学习规划器。把学习目标拆成一份有序的任务清单。

规则：
1. 每个任务是一个可执行的动作（一句话），不要只写知识点名称。
2. 任务按学习顺序排列；depends_on 用任务在列表中的下标（从 0 开始）。
3. depends_on 只能引用下标更小的任务。
4. 任务数量控制在 3～8 个；不要为同一个目标拆出内容重复的任务。
5. 目标没有给出的前置条件，不要自己编造。\
"""


class LLMPlanner:
    """用模型拆解目标。

    模型只负责**提出**任务，规则由 :class:`PlanManager` 校验：重复内容、越界的
    下标、空任务列表都会被拒绝。模型不负责保证计划合法。
    """

    def __init__(
        self,
        client: LLMClient,
        *,
        model: str | None = None,
        instructions: Sequence[str] = (),
    ) -> None:
        """初始化规划器。

        Args:
            client: 模型客户端。
            model: 模型名。为 None 时由客户端决定。
            instructions: 附加指令，排在拆解指令之前。
        """
        self.client = client
        self.model = model
        self.instructions = tuple(instructions)

    async def plan(self, goal: GoalSpec) -> PlanDraft:
        """调用模型拆解目标。

        Args:
            goal: 目标描述。

        Returns:
            计划草稿。

        Raises:
            PlanError: 模型给出的拆解无法使用。
        """
        request = LLMRequest(
            model=self.model,
            instructions=[*self.instructions, _DECOMPOSITION_INSTRUCTION],
            contents=[Message(role="user", content=_render_goal(goal))],
        )
        result = await self.client.generate_structured(request, TaskDecomposition)
        return _normalize_decomposition(result, goal)


def _render_goal(goal: GoalSpec) -> str:
    """把目标渲染成给模型的文本。

    Args:
        goal: 目标描述。

    Returns:
        文本。
    """
    lines = [f"目标：{goal.statement}"]
    if goal.outcomes:
        lines.append("学习结果：")
        lines.extend(f"- {item}" for item in goal.outcomes)
    if goal.constraints:
        lines.append("约束：")
        lines.extend(f"- {item}" for item in goal.constraints)
    return "\n".join(lines)


def _normalize_decomposition(result: TaskDecomposition, goal: GoalSpec) -> PlanDraft:
    """校验并归一化模型给出的拆解。

    Args:
        result: 模型输出。
        goal: 原始目标。

    Returns:
        计划草稿。

    Raises:
        PlanError: 拆解不可用。
    """
    if not result.tasks:
        raise PlanError("模型没有给出任何任务，无法据此建立计划")
    drafts: list[TaskDraft] = []
    seen: set[str] = set()
    for index, proposed in enumerate(result.tasks):
        content = proposed.content.strip()
        if not content:
            raise PlanError(f"模型给出的第 {index + 1} 个任务内容为空")
        key = normalize_content(content)
        if key in seen:
            # 重复任务在建立计划时是噪声：丢掉重复项，但保留先出现的那条。
            continue
        seen.add(key)
        depends: list[int] = []
        for dep in proposed.depends_on:
            if dep < 0 or dep >= index:
                raise PlanError(
                    f"模型给出的第 {index + 1} 个任务依赖了下标 {dep}，只能依赖序号更小的任务"
                )
            if dep not in depends:
                depends.append(dep)
        drafts.append(TaskDraft(content=content, depends_on=tuple(depends)))
    if not drafts:
        raise PlanError("模型给出的任务全部重复，无法据此建立计划")
    return PlanDraft(goal=goal.statement, tasks=tuple(drafts))


class PlanManager:
    """计划的写入接口：建计划、改计划、记反思。

    它**不认识学习领域**，只维护「目标 → 任务 → 状态 → 历史」这条链路。所有
    约束都在这里确定性执行——模型提不出的东西，这里替它守住。
    """

    def __init__(
        self,
        context: ExecutionContext,
        *,
        policy: PlanPolicy | None = None,
    ) -> None:
        """初始化管理器。

        Args:
            context: 执行上下文。计划存在它的草稿本里，因此随会话一起持久化。
            policy: 额度约束。为 None 时用默认约束。
        """
        self.context = context
        self.policy = policy or PlanPolicy()

    @property
    def plan(self) -> Plan | None:
        """当前计划；尚未建立时为 None。"""
        return self._load()

    def require_plan(self) -> Plan:
        """取出当前计划。

        Returns:
            计划。

        Raises:
            PlanError: 还没有建立计划。
        """
        plan = self._load()
        if plan is None:
            raise PlanError("当前运行还没有计划：先建立计划再修改它")
        return plan

    def _load(self) -> Plan | None:
        """从执行上下文的草稿本里读出计划。

        **每次都重读**：执行上下文是唯一权威。同一个上下文上可能存在多个
        管理器（应用层一个、工具每次调用一个），缓存副本会让它们各自看到
        不同的计划——那正是「计划越改越乱」的另一种形态。

        Returns:
            计划；尚未建立时返回 None。
        """
        stored = self.context.state.get(PLAN_STATE_KEY)
        return Plan.from_dict(stored) if isinstance(stored, Mapping) else None

    def create(
        self,
        goal: str,
        tasks: Sequence[TaskDraft],
        *,
        rationale: str = "",
    ) -> Plan:
        """建立计划。

        Args:
            goal: 目标。
            tasks: 任务草稿。前置依赖用草稿下标表示。
            rationale: 建立理由。写进初始修订。

        Returns:
            新建的计划。

        Raises:
            PlanError: 目标为空、任务为空、依赖越界或内容重复。
        """
        if self._load() is not None:
            raise PlanError("计划已经存在，不能重复建立；要改计划请提交增量修改")
        goal = goal.strip()
        if not goal:
            raise PlanError("计划必须有一个目标")
        if not tasks:
            raise PlanError("计划至少要有一个任务")

        now = time.time()
        plan = Plan(
            plan_id=f"plan-{uuid.uuid4().hex[:8]}",
            goal=goal,
            created_at=now,
            updated_at=now,
        )
        seen: dict[str, str] = {}
        created: list[AppliedChange] = []
        for index, draft in enumerate(tasks):
            content = draft.content.strip()
            if not content:
                raise PlanError(f"第 {index + 1} 个任务内容为空")
            key = normalize_content(content)
            if key in seen:
                raise PlanError(f"任务内容重复：{content!r} 与任务 {seen[key]} 说的是同一件事")
            seen[key] = f"t{len(plan.tasks) + 1}"
            task_id = f"t{len(plan.tasks) + 1}"
            depends: list[str] = []
            for dep in draft.depends_on:
                if dep < 0 or dep >= index:
                    raise PlanError(
                        f"第 {index + 1} 个任务依赖了下标 {dep}，只能依赖序号更小的任务"
                    )
                dep_id = f"t{dep + 1}"
                if dep_id not in depends:
                    depends.append(dep_id)
            plan.tasks.append(
                PlanTask(
                    task_id=task_id,
                    content=content,
                    status="pending",
                    depends_on=tuple(depends),
                    created_revision=1,
                    updated_revision=1,
                )
            )
            created.append(
                AppliedChange(task_id=task_id, action="created", before="", after="pending")
            )

        plan.revision = 1
        plan.revisions.append(
            PlanRevision(
                revision=1,
                kind="initial",
                changes=tuple(created),
                rationale=rationale.strip() or "初始计划",
                timestamp=now,
            )
        )
        self._persist(plan)
        return plan

    def apply(
        self,
        changes: Sequence[TaskChange],
        rationale: str,
        *,
        reflection_id: str | None = None,
    ) -> Plan:
        """提交一批计划修改。

        **整批原子**：任何一条不合法，整批都不生效——半生效的计划比不改更糟，
        因为它既不是模型想的那个样子，也没有被完整记录。

        Args:
            changes: 修改清单。
            rationale: 修改理由。必填。
            reflection_id: 触发这次修改的反思 id。

        Returns:
            修改后的计划。

        Raises:
            PlanError: 修改不合法。
        """
        plan = self.require_plan()
        rationale = rationale.strip()
        if not rationale:
            raise PlanError("修改计划必须给出理由（rationale）：没有理由的修改无法审计")
        if not changes:
            raise PlanError("没有要修改的内容：changes 不能为空")

        working = list(plan.tasks)
        applied: list[AppliedChange] = []
        revision_number = plan.revision + 1

        # 第一遍：给新增任务分配 id。分配先于校验，因此同一批里「新建 t4」
        # 与「让 t2 依赖 t4」可以一次写完，且与它们在清单里的先后无关。
        assigned = self._assign_task_ids(working, changes)
        known_ids = {task.task_id for task in working} | set(assigned.values())

        # 第二遍：真正落地。
        for index, change in enumerate(changes):
            if change.task_id is None:
                task = self._create_task(
                    working,
                    change,
                    revision_number,
                    assigned[index],
                    known_ids,
                )
                working.append(task)
                applied.append(
                    AppliedChange(
                        task_id=task.task_id,
                        action="created",
                        before="",
                        after=task.status,
                    )
                )
                continue
            applied.extend(self._update_task(working, change, revision_number, known_ids))

        self._check_consistency(working)

        structural = any(self._is_structural(change) for change in applied)
        if structural:
            self._check_replan_budget(plan)
            self._check_pending_replan(plan, reflection_id)

        now = time.time()
        plan.tasks = working
        plan.revision = revision_number
        if structural:
            plan.replan_count += 1
            if plan.pending_replan_id is not None and reflection_id == plan.pending_replan_id:
                plan.pending_replan_id = None
        plan.revisions.append(
            PlanRevision(
                revision=revision_number,
                kind="replan" if structural else "progress",
                changes=tuple(applied),
                rationale=rationale,
                reflection_id=reflection_id,
                timestamp=now,
            )
        )
        plan.updated_at = now
        self._persist(plan)
        return plan

    def reflect(
        self,
        verdict: ReflectionVerdict,
        assessment: str,
        *,
        evidence: Sequence[str] = (),
        target_task_id: str | None = None,
    ) -> Reflection:
        """记录一次反思。

        反思**不修改计划**——它只产出判断。要改计划是下一步，且必须引用这条
        反思。这就是「反思与重规划分开」的实现方式。

        Args:
            verdict: 反思结论。
            assessment: 判断依据。
            evidence: 支撑判断的证据。
            target_task_id: 反思针对的任务。

        Returns:
            反思记录。

        Raises:
            PlanError: 反思不合法或额度用尽。
        """
        plan = self.require_plan()
        assessment = assessment.strip()
        if not assessment:
            raise PlanError("反思必须给出判断依据（assessment）")
        if len(plan.reflections) >= self.policy.max_reflections:
            raise PlanError(
                f"反思次数已达上限（{self.policy.max_reflections}）："
                "继续反思不会带来新信息，请给出结论"
            )
        if target_task_id is not None:
            if plan.task(target_task_id) is None:
                raise PlanError(f"任务 {target_task_id!r} 不存在")
            used = sum(1 for item in plan.reflections if item.target_task_id == target_task_id)
            if used >= self.policy.max_reflections_per_task:
                raise PlanError(
                    f"任务 {target_task_id} 的反思次数已达上限"
                    f"（{self.policy.max_reflections_per_task}）：换个角度判断，或直接给出结论"
                )

        reflection = Reflection(
            reflection_id=f"r{len(plan.reflections) + 1}",
            verdict=verdict,
            assessment=assessment,
            evidence=tuple(item for item in evidence if item.strip()),
            target_task_id=target_task_id,
            created_at=time.time(),
        )
        plan.reflections.append(reflection)
        # 最新一条反思代表当前意图：它不是「需要重规划」，先前的请求就作废。
        plan.pending_replan_id = reflection.reflection_id if verdict == "replan" else None
        plan.updated_at = reflection.created_at
        self._persist(plan)
        return reflection

    @staticmethod
    def _assign_task_ids(
        working: Sequence[PlanTask],
        changes: Sequence[TaskChange],
    ) -> dict[int, str]:
        """给这一批里的新增任务分配 id。

        **先分配后校验**：分配在第一遍完成，因此同一批修改里「新建 t4」与
        「让 t2 依赖 t4」可以一次写完，且与两条在清单里的先后无关。自动分配的
        id 会避开已被占用的 id——包括本批其它修改显式指定的 id。

        Args:
            working: 当前任务列表。
            changes: 修改清单。

        Returns:
            修改下标到新任务 id 的映射（只含新增任务）。

        Raises:
            PlanError: id 被占用、重复指定，或一条修改同时指定了两个 id 字段。
        """
        existing = {task.task_id for task in working}
        assigned: dict[int, str] = {}
        for index, change in enumerate(changes):
            if change.task_id is not None:
                if change.new_task_id is not None:
                    raise PlanError("一条修改不能同时指定 task_id 与 new_task_id")
                continue
            if change.new_task_id is None:
                continue
            task_id = change.new_task_id.strip()
            if not task_id:
                raise PlanError("new_task_id 不能为空")
            if task_id in existing:
                raise PlanError(f"任务 id {task_id!r} 已被占用：请换一个 id")
            if task_id in assigned.values():
                raise PlanError(f"任务 id {task_id!r} 在同一批修改里被用了两次")
            assigned[index] = task_id

        reserved = existing | set(assigned.values())
        counter = len(working) + 1
        for index, change in enumerate(changes):
            if change.task_id is not None or index in assigned:
                continue
            while f"t{counter}" in reserved:
                counter += 1
            task_id = f"t{counter}"
            assigned[index] = task_id
            reserved.add(task_id)
            counter += 1
        return assigned

    def _create_task(
        self,
        working: list[PlanTask],
        change: TaskChange,
        revision_number: int,
        task_id: str,
        known_ids: set[str],
    ) -> PlanTask:
        """新建一个任务。

        Args:
            working: 当前任务列表（会被追加）。
            change: 修改意图。
            revision_number: 本次修订号。
            task_id: 分配好的任务 id。
            known_ids: 本批结束时存在的全部任务 id（含本批新建的）。

        Returns:
            新任务。

        Raises:
            PlanError: 内容为空、内容重复或依赖不存在。
        """
        content = change.content.strip()
        if not content:
            raise PlanError("新任务必须给出 content")
        key = normalize_content(content)
        for task in working:
            if task.status != "skipped" and normalize_content(task.content) == key:
                raise PlanError(
                    f"计划里已有内容相同的任务 {task.task_id}（{task.content!r}）："
                    "不要重复添加，直接更新它的状态"
                )
        status = change.status or "pending"
        if status not in ("pending", "in_progress", "blocked"):
            raise PlanError(f"新任务的状态只能是 pending / in_progress / blocked，收到 {status!r}")
        depends = tuple(change.depends_on or ())
        missing = [dep for dep in depends if dep not in known_ids]
        if missing:
            raise PlanError(f"依赖的任务 {', '.join(missing)} 不存在")
        if task_id in depends:
            raise PlanError(f"任务 {task_id} 不能依赖自己")
        return PlanTask(
            task_id=task_id,
            content=content,
            status=status,
            depends_on=depends,
            created_revision=revision_number,
            updated_revision=revision_number,
        )

    def _update_task(
        self,
        working: list[PlanTask],
        change: TaskChange,
        revision_number: int,
        known_ids: set[str],
    ) -> list[AppliedChange]:
        """修改一个已存在的任务。

        Args:
            working: 当前任务列表（会被就地更新）。
            change: 修改意图。
            revision_number: 本次修订号。
            known_ids: 本批结束时存在的全部任务 id（含本批新建的）。

        Returns:
            本次落地的改动。

        Raises:
            PlanError: 任务不存在、内容被改写、状态迁移非法或依赖不合法。
        """
        index = self._index_of(working, change.task_id)
        if index is None:
            listed = ", ".join(item.task_id for item in working) or "<无>"
            raise PlanError(f"任务 {change.task_id!r} 不存在；当前任务：{listed}")
        task = working[index]
        applied: list[AppliedChange] = []

        if change.content and change.content.strip() != task.content:
            raise PlanError(
                f"任务 {task.task_id} 的内容不可改写（当前：{task.content!r}）："
                "要换说法请新建一个任务，并把旧任务标为 skipped"
            )

        updated = task
        if change.status is not None and change.status != task.status:
            self._check_transition(task, change.status)
            updated = replace(updated, status=change.status)
            applied.append(
                AppliedChange(
                    task_id=task.task_id,
                    action="status",
                    before=task.status,
                    after=change.status,
                )
            )
        if change.depends_on is not None and tuple(change.depends_on) != task.depends_on:
            depends = tuple(change.depends_on)
            missing = [dep for dep in depends if dep not in known_ids]
            if missing:
                raise PlanError(f"依赖的任务 {', '.join(missing)} 不存在")
            if task.task_id in depends:
                raise PlanError(f"任务 {task.task_id} 不能依赖自己")
            updated = replace(updated, depends_on=depends)
            applied.append(
                AppliedChange(
                    task_id=task.task_id,
                    action="dependencies",
                    before=",".join(task.depends_on),
                    after=",".join(depends),
                )
            )
        working[index] = replace(updated, updated_revision=revision_number)
        return applied

    @staticmethod
    def _index_of(working: Sequence[PlanTask], task_id: str | None) -> int | None:
        """按 id 查找任务下标。

        Args:
            working: 任务列表。
            task_id: 任务标识。

        Returns:
            下标；不存在时返回 None。
        """
        if task_id is None:
            return None
        for index, task in enumerate(working):
            if task.task_id == task_id:
                return index
        return None

    @staticmethod
    def _check_transition(task: PlanTask, target: TaskStatus) -> None:
        """检查状态迁移是否合法。

        ``skipped`` 是唯一终态——任务被跳过之后不能回到进行中。其余状态之间
        允许迁移（含重新打开已完成的任务），因为那属于「发现之前做错了」，
        值得支持；但它算结构性修改，因此受重规划预算约束。

        Args:
            task: 当前任务。
            target: 目标状态。

        Raises:
            PlanError: 迁移非法。
        """
        if task.status == "skipped":
            raise PlanError(
                f"任务 {task.task_id} 已被跳过，不能再改状态：想恢复它的工作请新建一个任务"
            )

    @staticmethod
    def _is_structural(change: AppliedChange) -> bool:
        """判断一个改动是否属于「结构性修改」。

        结构性修改 = 新增任务、改依赖、跳过任务、重新打开已完成的任务。它们
        改变计划的**形状**，因此消耗重规划预算。单纯推进状态（待办 → 进行中
        → 完成）不改变形状，不消耗预算。

        Args:
            change: 改动。

        Returns:
            是否结构性。
        """
        if change.action in ("created", "dependencies"):
            return True
        if change.action == "status":
            return change.before == "completed" or change.after == "skipped"
        return False

    def _check_consistency(self, working: Sequence[PlanTask]) -> None:
        """检查整批修改后计划是否自洽。

        Args:
            working: 修改后的任务列表。

        Raises:
            PlanError: 存在多个进行中的任务，或进行中的任务前置未完成，或依赖成环。
        """
        active = [task for task in working if task.status == "in_progress"]
        if len(active) > 1:
            detail = "、".join(f"{task.task_id}（{task.content}）" for task in active)
            raise PlanError(
                f"同一时刻只能有一个进行中的任务，当前有 {len(active)} 个：{detail}；"
                "请先把其它任务标为 completed 或 pending"
            )

        statuses = {task.task_id: task.status for task in working}
        for task in active:
            unmet = _unmet_dependencies(task, statuses)
            if unmet:
                raise PlanError(
                    f"任务 {task.task_id} 的前置任务 {', '.join(unmet)} 尚未完成，不能开始；"
                    f"请先把 {task.task_id} 标回 pending，或先完成 {', '.join(unmet)}"
                )

        cycle = _find_cycle(working)
        if cycle is not None:
            raise PlanError(f"依赖形成了环：{' → '.join(cycle)}")

    def _check_replan_budget(self, plan: Plan) -> None:
        """检查重规划预算。

        Args:
            plan: 当前计划。

        Raises:
            PlanError: 预算用尽。
        """
        if plan.replan_count >= self.policy.max_replans:
            raise PlanError(
                f"重规划预算已用尽（{plan.replan_count}/{self.policy.max_replans}）："
                "请在现有任务内推进，或向用户说明卡在哪里"
            )

    @staticmethod
    def _check_pending_replan(plan: Plan, reflection_id: str | None) -> None:
        """检查待处理的重规划请求是否被引用。

        Args:
            plan: 当前计划。
            reflection_id: 本次修改引用的反思 id。

        Raises:
            PlanError: 存在待处理的重规划请求但本次修改没有引用它。
        """
        pending = plan.pending_replan_id
        if pending is None or reflection_id == pending:
            return
        raise PlanError(
            f"存在待处理的重规划请求（来自反思 {pending}）："
            f"本次结构性修改必须引用它（reflection_id={pending}），"
            "或先做一次反思说明不再重规划"
        )

    def _persist(self, plan: Plan) -> None:
        """把计划写回执行上下文的草稿本。

        Args:
            plan: 要保存的计划。
        """
        self.context.state[PLAN_STATE_KEY] = plan.to_dict()


def _unmet_dependencies(task: PlanTask, statuses: Mapping[str, str]) -> list[str]:
    """列出任务尚未完成的前置。

    这是**唯一的就绪谓词**：`Plan.startable_tasks`（调度）与
    `PlanManager._check_consistency`（进行中的任务前置必须已完成）共用它，
    避免两处各写一遍、慢慢走偏。

    Args:
        task: 任务。
        statuses: 任务 id 到状态的映射。

    Returns:
        尚未完成的前置任务 id；全部完成时为空列表。
    """
    return [dep for dep in task.depends_on if statuses.get(dep) != "completed"]


def _find_cycle(tasks: Sequence[PlanTask]) -> list[str] | None:
    """在任务依赖图里找环。

    Args:
        tasks: 任务列表。

    Returns:
        一条构成环的路径；无环时返回 None。
    """
    known = {task.task_id for task in tasks}
    graph = {task.task_id: [dep for dep in task.depends_on if dep in known] for task in tasks}
    color: dict[str, int] = dict.fromkeys(graph, 0)
    parent: dict[str, str] = {}

    for start in graph:
        if color[start] != 0:
            continue
        color[start] = 1
        stack: list[tuple[str, Any]] = [(start, iter(graph[start]))]
        while stack:
            node, children = stack[-1]
            advanced = False
            for child in children:
                if color.get(child) == 1:
                    path = [child]
                    current = node
                    while current != child:
                        path.append(current)
                        current = parent[current]
                    path.append(child)
                    return list(reversed(path))
                if color.get(child) == 0:
                    color[child] = 1
                    parent[child] = node
                    stack.append((child, iter(graph[child])))
                    advanced = True
                    break
            if not advanced:
                color[node] = 2
                stack.pop()
    return None


def _presentation_order(tasks: Sequence[PlanTask]) -> list[PlanTask]:
    """按依赖排出**展示**顺序。

    这里只回答一个问题：「计划该从哪读到哪」。它**不是调度，也不是历史**：

    - 调度看就绪条件（`Plan.startable_tasks`），不看「列表里的下一个」。
      跳过、重开、重规划都会改变就绪集合，静态顺序跟不上。
    - 历史看创建顺序（`Plan.tasks` 本身）。按拓扑排序记历史，新插入的前置
      任务会排到旧任务之前，修订记录就开始说谎。

    重规划插入的前置任务一定晚于依赖它的任务被创建——若按创建顺序展示，
    前置任务会排在后面，计划读起来就是乱的，所以展示需要拓扑排序。并列的
    任务按创建顺序排，保证同一份计划每次渲染结果一致。

    拓扑排序无法完成时（理论上不会：成环的依赖在写入时就被拒绝）退回创建
    顺序，保证渲染永远有结果。

    Args:
        tasks: 任务列表（创建顺序）。

    Returns:
        展示顺序的任务列表。
    """
    index = {task.task_id: position for position, task in enumerate(tasks)}
    known = set(index)
    pending = {
        task.task_id: len([dep for dep in task.depends_on if dep in known]) for task in tasks
    }
    children: dict[str, list[str]] = {task.task_id: [] for task in tasks}
    for task in tasks:
        for dep in task.depends_on:
            if dep in known:
                children[dep].append(task.task_id)

    ready = sorted(
        (task.task_id for task in tasks if pending[task.task_id] == 0),
        key=lambda task_id: index[task_id],
    )
    ordered: list[PlanTask] = []
    while ready:
        task_id = ready.pop(0)
        ordered.append(tasks[index[task_id]])
        for child in children[task_id]:
            pending[child] -= 1
            if pending[child] == 0:
                ready.append(child)
                ready.sort(key=lambda item: index[item])
    if len(ordered) != len(tasks):
        return list(tasks)
    return ordered


def render_plan(plan: Plan) -> str:
    """把计划渲染成给模型看的文本。

    任务按**依赖顺序**展示——那只是阅读顺序（见 `_presentation_order`），
    不代表执行顺序；能做什么由「可以开始」那一行回答。

    Args:
        plan: 计划。

    Returns:
        文本。
    """
    lines = [f"学习计划：{plan.goal}（修订 {plan.revision}，重规划 {plan.replan_count} 次）"]
    for task in _presentation_order(plan.tasks):
        marker = _STATUS_MARKERS.get(task.status, "[?]")
        label = _STATUS_LABELS.get(task.status, task.status)
        line = f"{marker} {task.task_id} {task.content}（{label}）"
        if task.depends_on:
            line += f" ← 依赖 {', '.join(task.depends_on)}"
        lines.append(line)
    startable = plan.startable_tasks()
    if startable:
        lines.append("可以开始：" + "、".join(task.task_id for task in startable))
    counts = plan.status_counts()
    if counts:
        summary = "，".join(
            f"{_STATUS_LABELS.get(status, status)} {count}"
            for status, count in sorted(counts.items())
        )
        lines.append(f"进度：{summary}")
    if plan.pending_replan_id is not None:
        lines.append(f"待处理的重规划请求：来自反思 {plan.pending_replan_id}")
    return "\n".join(lines)


def render_reflection(reflection: Reflection) -> str:
    """把一次反思渲染成给模型看的文本。

    Args:
        reflection: 反思记录。

    Returns:
        文本。
    """
    lines = [f"反思 {reflection.reflection_id}：{reflection.verdict}"]
    lines.append(reflection.assessment)
    if reflection.evidence:
        lines.append("依据：")
        lines.extend(f"- {item}" for item in reflection.evidence)
    if reflection.target_task_id is not None:
        lines.append(f"针对任务：{reflection.target_task_id}")
    return "\n".join(lines)


_UPDATE_PLAN_DESCRIPTION = """\
更新学习计划。计划是一份带状态的任务清单，改动以「增量」提交。

用法：
- 新增任务：changes 里给 content，不给 task_id。
- 推进任务：给 task_id 与 status。
- 放弃任务：给 task_id 与 status="skipped"——任务不会消失，只会被跳过。
- 调整依赖：给 task_id 与 depends_on（任务 id 列表）。
- 需要在同一批里让别的任务依赖新任务时，给新任务一个 new_task_id。

约束（由程序强制，违反会被拒绝并说明原因）：
- 每次修改都要给 rationale。
- 同一时刻只能有一个 status="in_progress" 的任务。
- 有前置依赖的任务，前置任务必须已完成才能开始。
- 任务内容不可改写；要换说法就新建任务并跳过旧的。
- 新增 / 跳过 / 重开 / 改依赖属于结构性修改，受重规划预算限制。
- 若此前有一条 verdict="replan" 的反思尚未处理，本次结构性修改必须带上它的
  reflection_id。

计划里的任务按依赖排成阅读顺序，**那只是阅读顺序**；「可以开始」一行才是
当前能动手的任务，不要按列表顺序依次执行。
"""

_REFLECT_DESCRIPTION = """\
记录一次反思：对已经发生的事做判断，说明当前状况与下一步方向。

verdict 取值：
- continue：进展正常，继续推进。
- accept：当前目标已完成。
- replan：需要修改计划——下一次结构性修改必须引用本次返回的 reflection_id。
- blocked：缺少外部条件，无法继续，应当向用户求助。

反思不修改计划。同一任务不要反复反思——额度用尽后请直接给出结论。
"""

_UPDATE_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "changes": {
            "type": "array",
            "description": "本次要做的修改清单。",
            "items": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "要修改的任务 id；新增任务时省略。",
                    },
                    "new_task_id": {
                        "type": "string",
                        "description": "新增任务时指定的 id。默认自动分配；"
                        "需要在同一批里让别的任务依赖它时才指定。",
                    },
                    "content": {
                        "type": "string",
                        "description": "新任务的内容。已有任务不接受这个字段。",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "blocked", "completed", "skipped"],
                        "description": "任务的新状态。",
                    },
                    "depends_on": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "前置任务 id 列表。",
                    },
                },
                "additionalProperties": False,
            },
        },
        "rationale": {
            "type": "string",
            "description": "修改理由。必填。",
        },
        "reflection_id": {
            "type": "string",
            "description": "触发本次修改的反思 id。",
        },
    },
    "required": ["changes", "rationale"],
    "additionalProperties": False,
}

_REFLECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string",
            "enum": ["continue", "accept", "replan", "blocked"],
            "description": "反思结论。",
        },
        "assessment": {
            "type": "string",
            "description": "判断依据：当前状况与下一步方向。必填。",
        },
        "evidence": {
            "type": "array",
            "items": {"type": "string"},
            "description": "支撑判断的证据。",
        },
        "target_task_id": {
            "type": "string",
            "description": "反思针对的任务 id。",
        },
    },
    "required": ["verdict", "assessment"],
    "additionalProperties": False,
}


def _resolve_manager(context: ToolContext, policy: PlanPolicy | None) -> PlanManager:
    """从工具上下文取出执行上下文并构造管理器。

    **按调用时取**：同一个调度器服务多次运行，构造时绑定会把计划钉死在第一
    次运行上。

    Args:
        context: 工具上下文。
        policy: 额度约束。

    Returns:
        计划管理器。

    Raises:
        PlanError: 这次调用没有关联的执行上下文。
    """
    execution_context = context.metadata.get(EXECUTION_CONTEXT_KEY)
    if execution_context is None:
        raise PlanError("这次工具调用没有关联的执行上下文，无法访问学习计划")
    return PlanManager(execution_context, policy=policy)


def build_update_plan_tool(policy: PlanPolicy | None = None) -> FunctionTool:
    """构造「更新计划」工具。

    Args:
        policy: 额度约束。为 None 时用默认约束。

    Returns:
        工具实现。
    """

    async def handler(context: ToolContext, arguments: dict[str, Any]) -> ToolOutcome:
        """提交一批计划修改。

        Args:
            context: 工具上下文。
            arguments: 已通过 Schema 校验的参数。

        Returns:
            归一化前的结果：给模型看的是渲染后的计划，给程序看的是结构化计划。

        Raises:
            PlanError: 修改不合法。
        """
        manager = _resolve_manager(context, policy)
        changes = [
            TaskChange(
                task_id=item.get("task_id") or None,
                new_task_id=item.get("new_task_id") or None,
                content=str(item.get("content") or ""),
                status=item.get("status"),
                depends_on=(
                    tuple(str(dep) for dep in item["depends_on"])
                    if item.get("depends_on") is not None
                    else None
                ),
            )
            for item in arguments.get("changes", [])
        ]
        plan = manager.apply(
            changes,
            str(arguments.get("rationale") or ""),
            reflection_id=arguments.get("reflection_id"),
        )
        return ToolOutcome(
            tool_name=UPDATE_PLAN_TOOL_NAME,
            status="success",
            content=render_plan(plan),
            data={"plan": plan.to_dict()},
        )

    return FunctionTool(
        definition=ToolDefinition(
            name=UPDATE_PLAN_TOOL_NAME,
            description=_UPDATE_PLAN_DESCRIPTION,
            parameters=_UPDATE_PLAN_SCHEMA,
        ),
        func=handler,
    )


def build_reflect_tool(policy: PlanPolicy | None = None) -> FunctionTool:
    """构造「记录反思」工具。

    Args:
        policy: 额度约束。为 None 时用默认约束。

    Returns:
        工具实现。
    """

    async def handler(context: ToolContext, arguments: dict[str, Any]) -> ToolOutcome:
        """记录一次反思。

        Args:
            context: 工具上下文。
            arguments: 已通过 Schema 校验的参数。

        Returns:
            归一化前的结果。

        Raises:
            PlanError: 反思不合法或额度用尽。
        """
        manager = _resolve_manager(context, policy)
        reflection = manager.reflect(
            arguments["verdict"],
            str(arguments.get("assessment") or ""),
            evidence=[str(item) for item in arguments.get("evidence", [])],
            target_task_id=arguments.get("target_task_id"),
        )
        plan = manager.plan
        content = render_reflection(reflection)
        if plan is not None:
            content = f"{content}\n\n{render_plan(plan)}"
        return ToolOutcome(
            tool_name=REFLECT_TOOL_NAME,
            status="success",
            content=content,
            data={"reflection": reflection.to_dict()},
        )

    return FunctionTool(
        definition=ToolDefinition(
            name=REFLECT_TOOL_NAME,
            description=_REFLECT_DESCRIPTION,
            parameters=_REFLECT_SCHEMA,
        ),
        func=handler,
    )


__all__ = [
    "PLAN_STATE_KEY",
    "REFLECT_TOOL_NAME",
    "UPDATE_PLAN_TOOL_NAME",
    "GoalSpec",
    "LLMPlanner",
    "OutcomePlanner",
    "Plan",
    "PlanDraft",
    "PlanError",
    "PlanManager",
    "PlanPolicy",
    "PlanRevision",
    "PlanTask",
    "Planner",
    "ProposedTask",
    "Reflection",
    "ReflectionVerdict",
    "RevisionKind",
    "TaskChange",
    "TaskDecomposition",
    "TaskDraft",
    "TaskStatus",
    "build_reflect_tool",
    "build_update_plan_tool",
    "normalize_content",
    "render_plan",
    "render_reflection",
]
