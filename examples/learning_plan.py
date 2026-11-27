"""学习目标 → 学习计划 → 反思 → 重规划的完整闭环。

跑法::

    uv run python examples/learning_plan.py

脚本演示五件事：

1. **转换**——学习目标（领域模型）映射成运行时认识的目标描述，再拆成任务。
2. **推进**——模型用增量修改推进任务，而不是整份重写计划。
3. **纪律**——反思只产出判断；改计划是另一步，且必须引用那条反思。
4. **拒绝**——违反约束的修改被拒绝，并把原因回填给模型。
5. **留痕**——计划有版本与历史，「为什么变成现在这样」有答案。

**为什么不用教材的「整份重写」**：模型每次重写整份清单，已完成的任务可能被
丢掉，新任务可能被重复添加，顺序随改写漂移——**计划越改越乱**。根因不在模型，
而在于计划是一段可以被整体覆盖的文本。这里把计划做成带状态的对象。

**学习目标来自 ``core.learning`` 的正式领域模型**：领域模型负责「目标是什么」，
``core.agent`` 负责「目标怎么变成任务」，两者通过 ``GoalSpec`` 交接。今天演示的
是这条转换链路，而不是领域模型的全部规则。

脚本用脚本化的模型客户端，因此**不联网**也能跑。
"""

from __future__ import annotations

import asyncio
from datetime import date

from pydantic import BaseModel

from agent_learning_coach.core.agent import (
    AgentConfig,
    AgentRuntime,
    ExecutionContext,
    OutcomePlanner,
    PlanManager,
    PlanPolicy,
    TaskChange,
    TaskDraft,
    build_reflect_tool,
    build_update_plan_tool,
    render_plan,
)
from agent_learning_coach.core.learning import (
    CapabilityRequirement,
    CourseId,
    DailyEffort,
    GoalId,
    KnowledgePointId,
    LearnerId,
    LearningGoal,
    TimeWindow,
    to_goal_spec,
)
from agent_learning_coach.core.llm.models import LLMRequest, LLMResponse, LLMUsage
from agent_learning_coach.core.llm.types import Message, ToolCall, ToolResult
from agent_learning_coach.core.tools import ToolDispatcher, ToolRegistry


class ScriptedClient:
    """按脚本依次返回预设响应的模型客户端。"""

    def __init__(self, responses: list[LLMResponse]) -> None:
        """初始化脚本化客户端。

        Args:
            responses: 依次返回的响应。
        """
        self.responses = list(responses)

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """返回脚本中的下一个响应。"""
        return self.responses.pop(0)

    async def generate_structured[ResponseT: BaseModel](
        self,
        request: LLMRequest,
        output_model: type[ResponseT],
    ) -> ResponseT:
        """本示例不使用结构化输出。"""
        raise NotImplementedError


def _tool_call(name: str, arguments: dict[str, object], call_id: str) -> LLMResponse:
    """构造一个只含工具调用的响应。

    Args:
        name: 工具名。
        arguments: 工具参数。
        call_id: 调用 id。

    Returns:
        响应。
    """
    return LLMResponse(
        model="mock-model",
        contents=[ToolCall(tool_call_id=call_id, name=name, arguments=arguments)],
        usage=LLMUsage(input_tokens=20, output_tokens=8),
    )


def _text(text: str) -> LLMResponse:
    """构造一个只含文本的响应。

    Args:
        text: 文本。

    Returns:
        响应。
    """
    return LLMResponse(
        model="mock-model",
        contents=[Message(role="assistant", content=text)],
        usage=LLMUsage(input_tokens=15, output_tokens=30),
    )


def show_rejections() -> None:
    """演示被拒绝的修改——拒绝的理由会回填给模型。

    用**独立的计划**做演示，免得这些被拒的尝试消耗主计划的额度。
    """
    context = ExecutionContext()
    manager = PlanManager(context, policy=PlanPolicy(max_replans=2))
    manager.create(
        "掌握闭包",
        [
            TaskDraft(content="读一遍闭包定义"),
            TaskDraft(content="写出三个闭包例子", depends_on=(0,)),
            TaskDraft(content="用闭包实现计数器", depends_on=(1,)),
        ],
        rationale="演示用计划",
    )

    print("=== 违反约束的修改会被拒绝 ===\n")
    attempts = [
        (
            "没有理由",
            lambda: manager.apply(
                [TaskChange(task_id="t2", status="in_progress")],
                "",
            ),
        ),
        (
            "同时推进两个任务",
            lambda: manager.apply(
                [
                    TaskChange(task_id="t2", status="in_progress"),
                    TaskChange(task_id="t3", status="in_progress"),
                ],
                "并行推进",
            ),
        ),
        (
            "重复添加任务",
            lambda: manager.apply(
                [TaskChange(content="读一遍闭包定义")],
                "再加一条一样的",
            ),
        ),
        (
            "改写已有任务的内容",
            lambda: manager.apply(
                [TaskChange(task_id="t1", content="读两遍闭包定义")],
                "换个说法",
            ),
        ),
        (
            "前置任务没完成就想开始",
            lambda: manager.apply(
                [TaskChange(task_id="t3", status="in_progress")],
                "直接做最后一个",
            ),
        ),
    ]
    for label, attempt in attempts:
        try:
            attempt()
        except Exception as exc:
            print(f"[{label}] -> {exc}\n")

    # 未处理的重规划请求：先记一条反思，再试着改计划。
    manager.reflect("replan", "顺序要调")
    try:
        manager.apply([TaskChange(content="补一个练习")], "加个练习")
    except Exception as exc:
        print(f"[未引用待处理的重规划请求] -> {exc}\n")

    # 引用之后就被接受——这就是「反思与重规划是两步」的样子。
    plan = manager.apply(
        [TaskChange(content="补一个练习")],
        "按反思 r1 调整",
        reflection_id="r1",
    )
    print(f"[引用反思后] -> 接受，重规划次数 {plan.replan_count}\n")


async def main() -> None:
    """跑一遍完整闭环。"""
    # --- 1. 转换：学习目标 → 目标描述 → 任务草稿 → 计划 ---
    goal = LearningGoal(
        id=GoalId("goal-closures"),
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-js-core"),
        statement="掌握 JavaScript 闭包并能独立使用",
        outcomes=(
            "能说清闭包是什么、捕获了什么",
            "能写出三个真实的闭包例子",
            "能用闭包实现一个带私有状态的计数器",
        ),
        requirements=(
            CapabilityRequirement(
                point_id=KnowledgePointId("k2"),
                outcome="能用闭包实现一个带私有状态的计数器",
            ),
        ),
        time_window=TimeWindow(start=date(2026, 11, 24), end=date(2027, 1, 24)),
        daily_effort=DailyEffort(minutes_per_day=90),
        prerequisites=(KnowledgePointId("k1"),),
    )
    spec = to_goal_spec(goal)
    draft = await OutcomePlanner().plan(spec)

    context = ExecutionContext()
    manager = PlanManager(context, policy=PlanPolicy(max_replans=2))
    plan = manager.create(goal.statement, list(draft.tasks), rationale="按学习结果逐条拆解")

    print("=== 1. 学习目标 → 学习计划 ===\n")
    print(render_plan(plan))

    # --- 2. 推进：模型用增量修改推进任务 ---
    registry = ToolRegistry()
    registry.register(build_update_plan_tool(PlanPolicy(max_replans=2)))
    registry.register(build_reflect_tool(PlanPolicy(max_replans=2)))
    dispatcher = ToolDispatcher(registry)

    client = ScriptedClient(
        [
            _tool_call(
                "update_plan",
                {
                    "changes": [{"task_id": "t1", "status": "in_progress"}],
                    "rationale": "先读完定义",
                },
                "call-1",
            ),
            _tool_call(
                "update_plan",
                {
                    "changes": [
                        {"task_id": "t1", "status": "completed"},
                        {"task_id": "t2", "status": "in_progress"},
                    ],
                    "rationale": "读完定义，开始写例子",
                },
                "call-2",
            ),
            _tool_call(
                "reflect",
                {
                    "verdict": "replan",
                    "assessment": "写例子之前缺一次动手练习，直接写例子容易照抄定义",
                    "evidence": ["第二步只是模仿定义里的写法"],
                    "target_task_id": "t2",
                },
                "call-3",
            ),
            _tool_call(
                "update_plan",
                {
                    "changes": [
                        {"task_id": "t2", "status": "pending"},
                        {
                            "new_task_id": "t4",
                            "content": "动手写一个闭包例子再对比定义",
                            "depends_on": ["t1"],
                        },
                        {"task_id": "t2", "depends_on": ["t4"]},
                        {"task_id": "t4", "status": "in_progress"},
                    ],
                    "rationale": "按反思 r1 调整：在写例子之前插入一次动手练习",
                    "reflection_id": "r1",
                },
                "call-4",
            ),
            _text("计划已按反思调整：先动手写一次，再照着写三个例子。"),
        ]
    )
    runtime = AgentRuntime(client, dispatcher, config=AgentConfig(max_steps=6))
    prompt = f"学习目标：{goal.statement}\n\n当前计划：\n{render_plan(plan)}"
    result = await runtime.run(prompt, context=context)

    print("\n=== 2. 模型推进计划 ===\n")
    for item in result.context.contents:
        if isinstance(item, ToolCall):
            print(f"模型调用 {item.name}({item.arguments})")
        elif isinstance(item, ToolResult):
            head = item.content.strip().splitlines()
            print(f"  -> [{item.status}] {head[0] if head else ''}")

    # --- 3. 纪律：被拒绝的修改 ---
    show_rejections()

    # --- 4. 留痕：版本与历史 ---
    final = manager.require_plan()
    print("=== 3. 计划历史 ===\n")
    for revision in final.revisions:
        summary = "，".join(
            f"{change.task_id} {change.action} {change.before}->{change.after}"
            for change in revision.changes
        )
        cite = f"（依据反思 {revision.reflection_id}）" if revision.reflection_id else ""
        print(f"修订 {revision.revision}［{revision.kind}］{cite}：{summary}")
        print(f"        理由：{revision.rationale}")
    print("\n反思：")
    for reflection in final.reflections:
        print(f"  {reflection.reflection_id} [{reflection.verdict}] {reflection.assessment}")
    print(f"\n重规划次数：{final.replan_count}/{manager.policy.max_replans}")
    print("\n最终计划：")
    print(render_plan(final))


if __name__ == "__main__":
    asyncio.run(main())
