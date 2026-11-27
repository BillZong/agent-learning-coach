"""计划与反思的测试。

对应 Topic 13「Planning 与 Reflection」，覆盖五组关注点：

**状态机**——任务状态怎么迁移，哪些迁移被拒绝。

**反混乱约束**——计划为什么不会越改越乱：内容不可改写、任务不可删除、同一
时刻只有一个进行中的任务、依赖闭合且无环、每次修改都要有理由。

**额度**——重规划预算与反思额度。结构性修改消耗预算，正常推进不消耗；反思
有总量上限与单任务上限。

**纪律**——反思不改计划；待处理的重规划请求必须被下一次结构性修改引用。

**持久化**——计划存在执行上下文的草稿本里，因此随会话快照一起保存与恢复。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from agent_learning_coach.core.agent import (
    AgentConfig,
    AgentRuntime,
    ExecutionContext,
    GoalSpec,
    LLMPlanner,
    OutcomePlanner,
    Plan,
    PlanError,
    PlanManager,
    PlanPolicy,
    PlanTask,
    TaskChange,
    TaskDraft,
    build_reflect_tool,
    build_update_plan_tool,
    render_plan,
)
from agent_learning_coach.core.agent.types import EXECUTION_CONTEXT_KEY
from agent_learning_coach.core.llm.mock import MockLLMClient
from agent_learning_coach.core.llm.types import ToolResult
from agent_learning_coach.core.tools import (
    ToolContext,
    ToolDispatcher,
    ToolRegistry,
)
from support import ScriptedClient, text_response, tool_call_response

# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------


def _task(plan: Plan, task_id: str) -> PlanTask:
    """取一个必然存在的任务。

    ``Plan.task`` 对不存在的 id 返回 ``None``。测试里那些断言本来就在检查任务
    的状态，所以在这里显式断言它存在——比让 ``None`` 在后面炸掉更清楚。
    """
    task = plan.task(task_id)
    assert task is not None
    return task


def make_manager(
    *,
    policy: PlanPolicy | None = None,
) -> tuple[ExecutionContext, PlanManager]:
    """构造执行上下文与计划管理器。"""
    context = ExecutionContext()
    return context, PlanManager(context, policy=policy)


def seed_plan(manager: PlanManager) -> Plan:
    """建立一条三步的计划。"""
    return manager.create(
        "掌握闭包",
        [
            TaskDraft(content="读一遍闭包定义"),
            TaskDraft(content="写出三个闭包例子", depends_on=(0,)),
            TaskDraft(content="用闭包实现计数器", depends_on=(1,)),
        ],
        rationale="初始拆解",
    )


# --------------------------------------------------------------------------
# 建立计划
# --------------------------------------------------------------------------


def test_create_assigns_ids_and_resolves_dependencies() -> None:
    """草稿下标会被翻译成任务 id，依赖关系随之确定。"""
    _, manager = make_manager()
    plan = seed_plan(manager)

    assert [task.task_id for task in plan.tasks] == ["t1", "t2", "t3"]
    assert plan.tasks[0].depends_on == ()
    assert plan.tasks[1].depends_on == ("t1",)
    assert plan.tasks[2].depends_on == ("t2",)
    assert plan.revision == 1
    assert plan.revisions[0].kind == "initial"


def test_create_rejects_duplicate_content() -> None:
    """内容相同的任务不算两个任务。"""
    _, manager = make_manager()
    with pytest.raises(PlanError, match="重复"):
        manager.create(
            "掌握闭包",
            [TaskDraft(content="读一遍闭包定义"), TaskDraft(content="读一遍闭包定义。")],
        )


def test_create_rejects_empty_goal_and_tasks() -> None:
    """目标与任务都不能为空。"""
    _, manager = make_manager()
    with pytest.raises(PlanError, match="目标"):
        manager.create("  ", [TaskDraft(content="做事")])
    with pytest.raises(PlanError, match="至少"):
        manager.create("掌握闭包", [])


def test_create_rejects_out_of_range_dependency() -> None:
    """任务只能依赖序号更小的任务。"""
    _, manager = make_manager()
    with pytest.raises(PlanError, match="序号更小"):
        manager.create(
            "掌握闭包",
            [TaskDraft(content="做事", depends_on=(1,)), TaskDraft(content="别的事")],
        )


def test_cannot_create_plan_twice() -> None:
    """计划不能被整体重建——那正是「越改越乱」的来源。"""
    _, manager = make_manager()
    seed_plan(manager)
    with pytest.raises(PlanError, match="已经存在"):
        manager.create("另一个目标", [TaskDraft(content="做事")])


def test_apply_requires_existing_plan() -> None:
    """没有计划就不能改。"""
    _, manager = make_manager()
    with pytest.raises(PlanError, match="还没有计划"):
        manager.apply([TaskChange(task_id="t1", status="completed")], "理由")


# --------------------------------------------------------------------------
# 状态机
# --------------------------------------------------------------------------


def test_status_change_is_recorded() -> None:
    """状态迁移会进版本历史。"""
    _, manager = make_manager()
    seed_plan(manager)
    plan = manager.apply(
        [TaskChange(task_id="t1", status="in_progress")],
        "开始第一步",
    )

    assert _task(plan, "t1").status == "in_progress"
    assert plan.revision == 2
    assert plan.revisions[-1].kind == "progress"
    change = plan.revisions[-1].changes[0]
    assert (change.task_id, change.action, change.before, change.after) == (
        "t1",
        "status",
        "pending",
        "in_progress",
    )


def test_skipped_is_terminal() -> None:
    """被跳过的任务不能再回到进行中。"""
    _, manager = make_manager()
    seed_plan(manager)
    manager.apply([TaskChange(task_id="t1", status="skipped")], "这条路线走不通")
    with pytest.raises(PlanError, match="不能再改状态"):
        manager.apply([TaskChange(task_id="t1", status="in_progress")], "又想做了")


def test_only_one_task_in_progress() -> None:
    """同一时刻只能推进一件事。"""
    _, manager = make_manager()
    seed_plan(manager)
    manager.apply([TaskChange(task_id="t1", status="in_progress")], "开始第一步")
    with pytest.raises(PlanError, match="只能有一个进行中"):
        manager.apply([TaskChange(task_id="t2", status="in_progress")], "同时开始第二步")


def test_in_progress_requires_dependencies_completed() -> None:
    """前置任务没完成就不能开始依赖它的任务。"""
    _, manager = make_manager()
    seed_plan(manager)
    with pytest.raises(PlanError, match="尚未完成"):
        manager.apply([TaskChange(task_id="t2", status="in_progress")], "跳过分组")


def test_completed_task_can_be_reopened() -> None:
    """发现之前做错了，可以重新打开——但它算结构性修改。"""
    _, manager = make_manager()
    seed_plan(manager)
    manager.apply([TaskChange(task_id="t1", status="in_progress")], "开始")
    manager.apply([TaskChange(task_id="t1", status="completed")], "做完了")
    plan = manager.apply([TaskChange(task_id="t1", status="in_progress")], "发现漏了一个例子")

    assert _task(plan, "t1").status == "in_progress"
    assert plan.revisions[-1].kind == "replan"
    assert plan.replan_count == 1


def test_dependency_cycle_is_rejected() -> None:
    """依赖成环会被抓住。"""
    _, manager = make_manager()
    seed_plan(manager)
    manager.apply([TaskChange(task_id="t1", status="completed")], "第一步完成")
    manager.apply(
        [TaskChange(task_id="t3", depends_on=("t1",))],
        "改依赖",
    )
    with pytest.raises(PlanError, match="环"):
        manager.apply([TaskChange(task_id="t1", depends_on=("t3",))], "制造环")


# --------------------------------------------------------------------------
# 反混乱约束
# --------------------------------------------------------------------------


def test_task_content_is_immutable() -> None:
    """任务文字不可改写——要换说法就新建任务并跳过旧的。"""
    _, manager = make_manager()
    seed_plan(manager)
    with pytest.raises(PlanError, match="不可改写"):
        manager.apply([TaskChange(task_id="t1", content="读两遍闭包定义")], "改措辞")


def test_task_is_never_deleted() -> None:
    """跳过不是删除：任务仍在计划里，只是不再推进。"""
    _, manager = make_manager()
    seed_plan(manager)
    plan = manager.apply([TaskChange(task_id="t3", status="skipped")], "第三个任务没必要")

    assert len(plan.tasks) == 3
    assert _task(plan, "t3").status == "skipped"
    assert "t3" in render_plan(plan)


def test_duplicate_task_is_rejected() -> None:
    """重复添加的任务会被拒绝，并指出是哪一个。"""
    _, manager = make_manager()
    seed_plan(manager)
    with pytest.raises(PlanError, match="t1"):
        manager.apply([TaskChange(content="读一遍闭包定义")], "又加了一条")


def test_rationale_is_required() -> None:
    """没有理由的修改无法审计，因此被拒绝。"""
    _, manager = make_manager()
    seed_plan(manager)
    with pytest.raises(PlanError, match="理由"):
        manager.apply([TaskChange(task_id="t1", status="in_progress")], "   ")


def test_apply_is_atomic() -> None:
    """一批修改里有一条不合法，整批都不生效。"""
    _, manager = make_manager()
    seed_plan(manager)
    before = manager.require_plan().revision
    with pytest.raises(PlanError):
        manager.apply(
            [
                TaskChange(task_id="t1", status="in_progress"),
                TaskChange(task_id="t9", status="completed"),
            ],
            "一半合法一半不合法",
        )
    plan = manager.require_plan()
    assert plan.revision == before
    assert _task(plan, "t1").status == "pending"


# --------------------------------------------------------------------------
# 额度
# --------------------------------------------------------------------------


def test_progress_does_not_consume_replan_budget() -> None:
    """正常推进不改计划的形状，因此不消耗重规划预算。"""
    _, manager = make_manager(policy=PlanPolicy(max_replans=1))
    seed_plan(manager)
    manager.apply([TaskChange(task_id="t1", status="in_progress")], "开始")
    manager.apply([TaskChange(task_id="t1", status="completed")], "做完")
    plan = manager.apply([TaskChange(task_id="t2", status="in_progress")], "推进第二步")

    assert plan.replan_count == 0
    assert plan.revisions[-1].kind == "progress"


def test_structural_change_consumes_replan_budget() -> None:
    """新增任务属于结构性修改。"""
    _, manager = make_manager(policy=PlanPolicy(max_replans=2))
    seed_plan(manager)
    plan = manager.apply([TaskChange(content="补一个练习")], "多练一次")

    assert plan.replan_count == 1
    assert plan.revisions[-1].kind == "replan"


def test_replan_budget_is_enforced() -> None:
    """预算用尽后不能再结构性修改。"""
    _, manager = make_manager(policy=PlanPolicy(max_replans=1))
    seed_plan(manager)
    manager.apply([TaskChange(content="补一个练习")], "多练一次")
    with pytest.raises(PlanError, match="预算已用尽"):
        manager.apply([TaskChange(content="再补一个练习")], "还想加")


def test_pending_replan_must_be_cited() -> None:
    """反思要求重规划之后，下一次结构性修改必须引用它。"""
    _, manager = make_manager()
    seed_plan(manager)
    reflection = manager.reflect("replan", "第二步的顺序不对，应该先做练习")
    assert manager.require_plan().pending_replan_id == reflection.reflection_id

    with pytest.raises(PlanError, match="必须引用"):
        manager.apply([TaskChange(content="补一个练习")], "加个练习")

    plan = manager.apply(
        [TaskChange(content="补一个练习")],
        "按反思调整顺序",
        reflection_id=reflection.reflection_id,
    )
    assert plan.pending_replan_id is None
    assert plan.revisions[-1].reflection_id == reflection.reflection_id


def test_later_reflection_supersedes_pending_replan() -> None:
    """最新一条反思代表当前意图：它不再要求重规划，先前的请求作废。"""
    _, manager = make_manager()
    seed_plan(manager)
    manager.reflect("replan", "顺序不对")
    manager.reflect("continue", "再想想，其实顺序没问题")
    assert manager.require_plan().pending_replan_id is None

    plan = manager.apply([TaskChange(content="补一个练习")], "加个练习")
    assert plan.replan_count == 1


def test_reflection_budget_is_enforced() -> None:
    """反思有总量上限。"""
    _, manager = make_manager(policy=PlanPolicy(max_reflections=2))
    seed_plan(manager)
    manager.reflect("continue", "还行")
    manager.reflect("continue", "还行")
    with pytest.raises(PlanError, match="反思次数已达上限"):
        manager.reflect("continue", "还是不行")


def test_reflection_per_task_budget_is_enforced() -> None:
    """同一个任务不能被反复反思。"""
    _, manager = make_manager(policy=PlanPolicy(max_reflections=6, max_reflections_per_task=1))
    seed_plan(manager)
    manager.reflect("continue", "第一步有疑问", target_task_id="t1")
    with pytest.raises(PlanError, match="反思次数已达上限"):
        manager.reflect("continue", "还是没想明白", target_task_id="t1")


def test_reflection_requires_assessment() -> None:
    """没有依据的反思不成立。"""
    _, manager = make_manager()
    seed_plan(manager)
    with pytest.raises(PlanError, match="判断依据"):
        manager.reflect("continue", "  ")


def test_reflection_rejects_unknown_task() -> None:
    """反思可以针对任务，但任务必须存在。"""
    _, manager = make_manager()
    seed_plan(manager)
    with pytest.raises(PlanError, match="不存在"):
        manager.reflect("continue", "针对某个任务", target_task_id="t9")


# --------------------------------------------------------------------------
# 新任务 id 与展示顺序
# --------------------------------------------------------------------------


def test_new_task_can_be_named() -> None:
    """模型可以给新任务指定 id——这样同一批里别的任务才能依赖它。"""
    _, manager = make_manager()
    seed_plan(manager)
    plan = manager.apply(
        [
            TaskChange(new_task_id="t0", content="动手写一个闭包例子"),
            TaskChange(task_id="t2", depends_on=("t0",)),
        ],
        "在写例子之前插入一次动手练习",
    )

    assert plan.task("t0") is not None
    assert _task(plan, "t2").depends_on == ("t0",)


def test_named_task_cannot_collide() -> None:
    """id 被占用时明确报错，而不是悄悄改掉。"""
    _, manager = make_manager()
    seed_plan(manager)
    with pytest.raises(PlanError, match="已被占用"):
        manager.apply([TaskChange(new_task_id="t1", content="新的任务")], "想用旧 id")


def test_task_id_and_new_task_id_are_exclusive() -> None:
    """一条修改不能同时指定两个 id 字段。"""
    _, manager = make_manager()
    seed_plan(manager)
    with pytest.raises(PlanError, match="不能同时指定"):
        manager.apply(
            [TaskChange(task_id="t1", new_task_id="t9", content="既改又建")],
            "含糊的修改",
        )


def test_dependency_may_target_a_task_created_in_the_same_batch() -> None:
    """同批次内引用新建任务，与它在清单里的先后无关。"""
    _, manager = make_manager()
    seed_plan(manager)
    plan = manager.apply(
        [
            TaskChange(task_id="t2", depends_on=("t9",)),
            TaskChange(new_task_id="t9", content="动手写一个闭包例子"),
        ],
        "把依赖指到同一批新建的任务上",
    )

    assert _task(plan, "t2").depends_on == ("t9",)
    assert plan.task("t9") is not None


def test_plan_renders_in_dependency_order() -> None:
    """展示顺序由依赖决定：前置任务排在依赖它的任务之前。"""
    _, manager = make_manager()
    seed_plan(manager)
    manager.apply(
        [
            TaskChange(new_task_id="t0", content="动手写一个闭包例子"),
            TaskChange(task_id="t2", depends_on=("t0",)),
        ],
        "插入前置任务",
    )
    rendered = render_plan(manager.require_plan())
    assert rendered.index("t0") < rendered.index("t2")


def test_creation_order_is_the_history_order() -> None:
    """历史看创建顺序，展示看依赖——两者不能互相顶替。"""
    _, manager = make_manager()
    seed_plan(manager)
    manager.apply(
        [
            TaskChange(new_task_id="t0", content="动手写一个闭包例子"),
            TaskChange(task_id="t2", depends_on=("t0",)),
        ],
        "插入前置任务",
    )
    plan = manager.require_plan()

    assert [task.task_id for task in plan.tasks] == ["t1", "t2", "t3", "t0"]
    assert [item["task_id"] for item in plan.to_dict()["tasks"]] == ["t1", "t2", "t3", "t0"]
    assert render_plan(plan).index("t0") < render_plan(plan).index("t2")


def test_startable_tasks_is_readiness_not_display_order() -> None:
    """调度看就绪条件：可开始的集合与展示顺序无关。"""
    _, manager = make_manager()
    seed_plan(manager)
    assert [task.task_id for task in manager.require_plan().startable_tasks()] == ["t1"]

    manager.apply([TaskChange(task_id="t1", status="in_progress")], "开始")
    assert manager.require_plan().startable_tasks() == []

    manager.apply([TaskChange(task_id="t1", status="completed")], "做完了")
    plan = manager.require_plan()
    assert [task.task_id for task in plan.startable_tasks()] == ["t2"]
    assert "可以开始：t2" in render_plan(plan)


# --------------------------------------------------------------------------
# 持久化
# --------------------------------------------------------------------------


def test_plan_lives_in_execution_context_state() -> None:
    """计划放在草稿本里，因此随会话快照一起持久化。"""
    context, manager = make_manager()
    seed_plan(manager)

    assert "learning_plan" in context.state
    assert context.state["learning_plan"]["goal"] == "掌握闭包"


def test_plan_survives_a_new_manager() -> None:
    """重新构造管理器时，计划从草稿本里回来。"""
    context, manager = make_manager()
    seed_plan(manager)
    manager.apply([TaskChange(task_id="t1", status="in_progress")], "开始")

    restored = PlanManager(context)
    plan = restored.require_plan()
    assert _task(plan, "t1").status == "in_progress"
    assert plan.revision == 2


def test_plan_round_trips_through_dict() -> None:
    """序列化往返后内容不变。"""
    _, manager = make_manager()
    seed_plan(manager)
    manager.reflect("continue", "还行")
    original = manager.require_plan()

    restored = Plan.from_dict(original.to_dict())
    assert restored.to_dict() == original.to_dict()


# --------------------------------------------------------------------------
# 规划器
# --------------------------------------------------------------------------


def test_outcome_planner_chains_outcomes() -> None:
    """确定性拆解：每个学习结果一个任务，按顺序串联。"""
    planner = OutcomePlanner()
    draft = asyncio.run(
        planner.plan(
            GoalSpec(
                goal_id="g1",
                statement="掌握闭包",
                outcomes=("读定义", "写例子", "写练习"),
            )
        )
    )

    assert [task.content for task in draft.tasks] == ["读定义", "写例子", "写练习"]
    assert draft.tasks[2].depends_on == (1,)


def test_outcome_planner_requires_outcomes() -> None:
    """没有学习结果就没法拆解。"""
    with pytest.raises(PlanError, match="学习结果"):
        asyncio.run(OutcomePlanner().plan(GoalSpec(goal_id="g1", statement="掌握闭包")))


def test_llm_planner_parses_decomposition() -> None:
    """模型给出的拆解会被翻译成计划草稿。"""
    payload = json.dumps(
        {
            "tasks": [
                {"content": "读一遍闭包定义"},
                {"content": "写出三个闭包例子", "depends_on": [0]},
            ]
        },
        ensure_ascii=False,
    )
    planner = LLMPlanner(MockLLMClient(text=payload))
    draft = asyncio.run(
        planner.plan(GoalSpec(goal_id="g1", statement="掌握闭包", outcomes=("读定义",)))
    )

    assert [task.content for task in draft.tasks] == ["读一遍闭包定义", "写出三个闭包例子"]
    assert draft.tasks[1].depends_on == (0,)


def test_llm_planner_rejects_forward_dependency() -> None:
    """模型引用了序号更大的任务——那是环的前兆，直接拒绝。"""
    payload = json.dumps(
        {"tasks": [{"content": "A", "depends_on": [1]}, {"content": "B"}]},
        ensure_ascii=False,
    )
    planner = LLMPlanner(MockLLMClient(text=payload))
    with pytest.raises(PlanError, match="序号更小"):
        asyncio.run(planner.plan(GoalSpec(goal_id="g1", statement="掌握闭包")))


def test_llm_planner_drops_duplicate_tasks() -> None:
    """模型给出重复任务时保留先出现的那条。"""
    payload = json.dumps(
        {"tasks": [{"content": "读定义"}, {"content": "读定义。"}]},
        ensure_ascii=False,
    )
    planner = LLMPlanner(MockLLMClient(text=payload))
    draft = asyncio.run(planner.plan(GoalSpec(goal_id="g1", statement="掌握闭包")))
    assert len(draft.tasks) == 1


def test_llm_planner_rejects_empty_decomposition() -> None:
    """模型没给出任务时不能建立计划。"""
    planner = LLMPlanner(MockLLMClient(text=json.dumps({"tasks": []})))
    with pytest.raises(PlanError, match="没有给出任何任务"):
        asyncio.run(planner.plan(GoalSpec(goal_id="g1", statement="掌握闭包")))


# --------------------------------------------------------------------------
# 工具路径
# --------------------------------------------------------------------------


def build_dispatcher() -> ToolDispatcher:
    """构造含计划工具的调度器。"""
    registry = ToolRegistry()
    registry.register(build_update_plan_tool())
    registry.register(build_reflect_tool())
    return ToolDispatcher(registry)


def tool_context(context: ExecutionContext) -> ToolContext:
    """构造带执行上下文的工具上下文。"""
    return ToolContext(caller="test", metadata={EXECUTION_CONTEXT_KEY: context})


def test_update_plan_tool_applies_change() -> None:
    """工具提交增量修改，模型拿到渲染后的计划。"""
    context, manager = make_manager()
    seed_plan(manager)
    manager.apply([TaskChange(task_id="t1", status="in_progress")], "开始")

    dispatcher = build_dispatcher()
    outcome = asyncio.run(
        dispatcher.dispatch(
            "update_plan",
            {
                "changes": [{"task_id": "t1", "status": "completed"}],
                "rationale": "第一步做完了",
            },
            tool_context(context),
        )
    )

    assert outcome.succeeded
    assert "t1" in outcome.content
    assert outcome.data is not None
    assert outcome.data["plan"]["tasks"][0]["status"] == "completed"


def test_update_plan_tool_reports_violation_to_the_model() -> None:
    """违反约束时返回错误结果，而不是抛异常——模型据此调整。"""
    context = ExecutionContext()
    dispatcher = build_dispatcher()
    outcome = asyncio.run(
        dispatcher.dispatch(
            "update_plan",
            {
                "changes": [{"task_id": "t1", "status": "in_progress"}],
                "rationale": "开始",
            },
            tool_context(context),
        )
    )

    assert not outcome.succeeded
    assert "还没有计划" in outcome.content


def test_reflect_tool_records_reflection() -> None:
    """反思工具把判断记进计划，并返回可读文本。"""
    context, manager = make_manager()
    seed_plan(manager)

    dispatcher = build_dispatcher()
    outcome = asyncio.run(
        dispatcher.dispatch(
            "reflect",
            {"verdict": "replan", "assessment": "顺序要调", "evidence": ["第二步依赖第一步"]},
            tool_context(context),
        )
    )

    assert outcome.succeeded
    assert "replan" in outcome.content
    plan = manager.require_plan()
    assert len(plan.reflections) == 1
    assert plan.pending_replan_id == "r1"


def test_plan_tools_need_execution_context() -> None:
    """没有执行上下文的调用拿不到计划。"""
    dispatcher = build_dispatcher()
    outcome = asyncio.run(
        dispatcher.dispatch(
            "update_plan",
            {"changes": [{"task_id": "t1", "status": "completed"}], "rationale": "做完了"},
            ToolContext(caller="test"),
        )
    )

    assert not outcome.succeeded
    assert "没有关联的执行上下文" in outcome.content


def test_runtime_routes_plan_errors_back_to_the_model() -> None:
    """工具报错不会中断运行：错误被回填给模型，模型自己改。"""
    context = ExecutionContext()
    registry = ToolRegistry()
    registry.register(build_update_plan_tool())
    dispatcher = ToolDispatcher(registry)

    client = ScriptedClient(
        [
            tool_call_response(
                "update_plan",
                {"changes": [{"task_id": "t1", "status": "completed"}], "rationale": "做完了"},
                call_id="call-1",
            ),
            text_response("没有计划，我改用直接回答"),
        ]
    )
    runtime = AgentRuntime(client, dispatcher, config=AgentConfig(max_steps=4))
    result = asyncio.run(runtime.run("帮我推进闭包学习", context=context))

    assert result.succeeded
    tool_messages = [item for item in result.context.contents if isinstance(item, ToolResult)]
    assert tool_messages
    assert tool_messages[0].name == "update_plan"
    assert tool_messages[0].status == "error"
    assert "还没有计划" in tool_messages[0].content
