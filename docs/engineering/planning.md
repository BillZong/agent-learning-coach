# 计划与反思（Planning & Reflection）

> 状态：生效
> 对应代码：`src/agent_learning_coach/core/agent/planning.py`
> 决策依据：[ADR-0013](../architecture/decisions/0013-planning-and-reflection.md)

本文是计划与反思模块的接入说明：**怎么用**、**为什么这样设计**、**边界在哪**。

## 1. 核心契约

计划与反思拆成三组类型：

| 类型 | 含义 | 面向 |
| ---- | ---- | ---- |
| `Plan` / `PlanTask` | 计划与它的任务：id、内容、状态、依赖 | 应用层与模型 |
| `PlanRevision` / `Reflection` | 修订历史与反思记录 | 应用层（审计） |
| `PlanManager` | 唯一的写入接口：建计划、改计划、记反思 | 应用层与工具 |

工具面有两个：

| 工具 | 作用 |
| ---- | ---- |
| `update_plan` | 提交一批**增量**修改 |
| `reflect` | 记录一次判断（不改计划） |

**计划是对象，不是文本。** 这是本模块与教材实现最大的差别：教材的
`create_tasks` 每次重写整份清单，因此「已完成的任务被漏掉」「同一个任务被
重复添加」「顺序随改写漂移」都是机制性的故障。这里任务有稳定 id、内容不可
改写、每次修改都有理由与版本。

## 2. 快速上手

### 建计划

```python
from agent_learning_coach.core.agent import (
    ExecutionContext,
    GoalSpec,
    OutcomePlanner,
    PlanManager,
    TaskDraft,
)

context = ExecutionContext()
manager = PlanManager(context)

draft = await OutcomePlanner().plan(
    GoalSpec(
        goal_id="g1",
        statement="掌握闭包",
        outcomes=("说清闭包是什么", "写出三个例子", "用闭包实现计数器"),
    )
)
plan = manager.create(draft.goal, list(draft.tasks), rationale="按学习结果逐条拆解")
```

`OutcomePlanner` 按学习结果逐条拆解并串联依赖。**它是基线**：模型拆出来的
计划至少不该差于它。

### 用模型拆解

```python
from agent_learning_coach.core.agent import LLMPlanner

planner = LLMPlanner(client, model="gpt-4o-mini")
draft = await planner.plan(spec)
```

模型只负责**提出**任务；重复内容、越界的依赖下标、空任务列表都由
`PlanManager` 拒绝。**模型不负责保证计划合法。**

### 推进与重规划

```python
from agent_learning_coach.core.agent import TaskChange

# 普通推进：不消耗重规划预算
manager.apply([TaskChange(task_id="t1", status="in_progress")], "先读完定义")
manager.apply([TaskChange(task_id="t1", status="completed")], "读完定义")

# 反思只产出判断
reflection = manager.reflect("replan", "写例子之前缺一次动手练习")

# 结构性修改必须引用那条反思
manager.apply(
    [
        TaskChange(task_id="t2", status="pending"),
        TaskChange(new_task_id="t4", content="动手写一个闭包例子", depends_on=("t1",)),
        TaskChange(task_id="t2", depends_on=("t4",)),
    ],
    "按反思调整顺序",
    reflection_id=reflection.reflection_id,
)
```

### 注册成工具

```python
from agent_learning_coach.core.agent import build_reflect_tool, build_update_plan_tool
from agent_learning_coach.core.tools import ToolDispatcher, ToolRegistry

registry = ToolRegistry()
registry.register(build_update_plan_tool())
registry.register(build_reflect_tool())
dispatcher = ToolDispatcher(registry)
```

工具按调用时从 `ToolContext.metadata[EXECUTION_CONTEXT_KEY]` 取执行上下文，
因此**同一个调度器可以服务多次运行**，计划不会串。

## 3. 状态机

```
pending    ──┬──> in_progress ──┬──> completed
            │         ↑  ↓        │
            ├──> blocked ─────────┘
            └──> skipped（终态）
```

| 状态 | 含义 |
| ---- | ---- |
| `pending` | 待办 |
| `in_progress` | 进行中（同一时刻最多一个） |
| `blocked` | 受阻（缺外部条件） |
| `completed` | 已完成 |
| `skipped` | 已跳过（**唯一终态**） |

`skipped` 是终态：被跳过的任务不能再回到进行中，想恢复就新建一个任务。
`completed` 可以被**重新打开**（发现之前做错了）——那属于结构性修改，因此
消耗重规划预算。

**没有删除操作。** 移除一个任务 = 标成 `skipped`，它仍然留在计划里。

## 4. 硬约束

这些约束由 `PlanManager` 确定性执行，违反时抛 `PlanError`；工具层把它
归一化成失败结果回填给模型。

| 约束 | 拒绝时的提示 |
| ---- | ---- |
| 每次修改都要有 `rationale` | 没有理由的修改无法审计 |
| 同一时刻只能有一个 `in_progress` | 请先把其它任务标为 completed 或 pending |
| 进行中的任务，其前置必须已完成 | 请先把它标回 pending，或先完成前置 |
| 任务内容不可改写 | 要换说法请新建任务并把旧的标为 skipped |
| 不能重复添加内容相同的任务 | 直接更新已有任务的状态 |
| 依赖必须存在且不成环 | 指明是哪一个任务 |
| `task_id` 与 `new_task_id` 不能同时给 | 一条修改不能同时指定两个 id 字段 |

**整批原子**：一批修改里有一条不合法，整批都不生效。半生效的计划比不改更
糟——它既不是模型想的样子，也没有被完整记录。

## 5. 额度

`PlanPolicy` 控制三件事：

| 参数 | 默认 | 作用 |
| ---- | ---- | ---- |
| `max_replans` | 3 | 结构性修改的次数上限 |
| `max_reflections` | 6 | 反思的总量上限 |
| `max_reflections_per_task` | 2 | 同一个任务的反思次数上限 |

**普通推进不消耗重规划预算**，只有结构性修改（新增 / 跳过 / 重开 / 改依赖）
才消耗。分账的理由：正常推进本来就不该被算成「又改了一次计划」。

## 6. 反思纪律

```
reflect(verdict="replan")             ->  产生待处理请求 r1
update_plan(..., reflection_id="r1")  ->  结构性修改被接受
```

- 反思**不改计划**——它只产出判断。
- 待处理的重规划请求存在时，下一次结构性修改**必须引用它**，否则被拒绝。
- **最新一条反思代表当前意图**：请求重规划之后又记了一条 `continue`，先前的
  请求就作废（否则会死锁）。

## 7. 持久化

计划存在 `ExecutionContext.state["learning_plan"]`，因此随会话快照一起保存，
也随会话恢复一起回来。

```python
from agent_learning_coach.core.agent import PLAN_STATE_KEY

context.state[PLAN_STATE_KEY]  # 序列化后的计划
```

**管理器不缓存计划**：每次都从执行上下文重读。同一个上下文上会存在多个
管理器（应用层一个、工具每次调用一个），缓存副本会让它们各自看到不同的
计划。

## 8. 学习目标到学习任务的转换

```
LearningGoal（领域模型，Phase 1）
    -> GoalSpec（运行时契约，领域无关）
    -> PlanDraft（任务草稿，依赖用下标表示）
    -> Plan（状态机校验后的计划）
```

职责分工：**领域模型回答「目标是什么」，运行时回答「目标怎么变成任务」。**
`core.agent` 因此不需要认识学习领域的任何具体概念。

```python
@dataclass(frozen=True)
class GoalSpec:
    goal_id: str
    statement: str
    outcomes: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
```

## 9. 顺序：三个问题，三种答案

同一份计划会被问到三个不同的问题。它们**不能共用一个排序结果**：

| 问题 | 谁回答 | 顺序 | 用错会怎样 |
| ---- | ---- | ---- | ---- |
| 「这个计划怎么变成现在这样？」 | `Plan.tasks` | 创建顺序（只追加） | 拓扑排序会让历史说谎：新插入的前置任务被排到旧任务之前，看起来像它先存在 |
| 「现在能做什么？」 | `Plan.startable_tasks()` | **集合**，无顺序 | 拓扑排序会把调度变成「照单执行」：跳过、重开、重规划都会改变就绪集合，静态顺序跟不上 |
| 「我该从哪读到哪？」 | `render_plan` | 依赖拓扑（并列按创建顺序） | 创建顺序会把前置任务排到后面，计划读起来是乱的 |

边界落在代码里：

- `Plan.tasks` 只追加、不重排；`to_dict()` 存的就是创建顺序。
- 就绪判断只有一个谓词：`_unmet_dependencies(task, statuses)`。
  `startable_tasks()`（调度）与「进行中的任务前置必须已完成」（一致性检查）
  共用它，不会各写一遍然后慢慢走偏。
- `_presentation_order(tasks)` 只被 `render_plan` 调用。它返回一个线性扩展，
  **不承诺是最优执行顺序，也不承诺稳定到可以当历史**。

```
学习计划：掌握闭包（修订 4，重规划 1 次）
[x] t1 能说清闭包是什么（已完成）
[>] t4 动手写一个闭包例子（进行中） ← 依赖 t1
[ ] t2 写出三个例子（待办） ← 依赖 t4
[ ] t3 用闭包实现计数器（待办） ← 依赖 t2
[ ] t5 复盘三个例子（待办）
可以开始：t5
进度：已完成 1，进行中 1，待办 3
```

任务列表是阅读顺序；「可以开始」一行是调度视图。两者不一致是正常的：
`t5` 与 `t4` 没有依赖关系，它排在计划末尾，但现在就能动手。

**不做的事：这不是 Bazel。** 计划是「带依赖约束的任务清单」，不是构建图——
没有 target / action 分层，没有增量重算与缓存，没有并行执行器。真需要那些
时，应该先有真实的调度需求，而不是现在就把骨架铺好。

`Plan.tasks` 保留**创建顺序**——那是历史查阅的依据；渲染顺序是另一回事。

## 10. 可运行示例

```bash
uv run python examples/learning_plan.py
```

演示完整的「目标 → 任务 → 推进 → 反思 → 重规划」闭环，以及五种被拒绝的
修改和它们的原因。

## 11. 当前未覆盖范围

| 能力 | 状态 | 备注 |
| ---- | ---- | ---- |
| 计划进上下文窗口 | 未实现 | 目前靠应用层把渲染后的计划放进 prompt，没有做预算裁剪 |
| 反思质量评估 | 未实现 | `replan` 的判断对不对，程序不验证，只记录 |
| 任务级重试策略 | 未实现 | 失败的任务由模型自己决定是否重开 |
| 计划并发修改 | 未实现 | 计划按执行上下文隔离，不支持跨进程共享 |
| 学习领域模型 | 未实现 | Phase 1 建立；今天只定了 `GoalSpec` 的交接形状 |

## 相关文档

- 决策依据：[ADR-0013](../architecture/decisions/0013-planning-and-reflection.md)
- 运行时：[runtime.md](runtime.md)
- 学习笔记：[Topic 13](../learning/topics/13-planning-and-reflection.md)
