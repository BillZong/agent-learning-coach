# 学习领域（Learning Domain）

> 状态：生效
> 对应代码：`src/agent_learning_coach/core/learning/`
> 决策依据：[ADR-0015](../architecture/decisions/0015-learning-domain-boundaries.md)、
> [ADR-0016](../architecture/decisions/0016-knowledge-representation.md)、
> [ADR-0017](../architecture/decisions/0017-learning-goal-modeling.md)

本文是学习领域模型的接入说明：**有哪些对象**、**怎么用**、**边界在哪**。

## 1. 核心契约

三个独立聚合，一个聚合内部的值对象结构，其余是值对象：

| 类型 | 分类 | 关键内容 |
| ---- | ---- | ---- |
| `Learner` | 实体 | `id`、`name`、`background`、`preferences`；`rename()` |
| `Course` | 实体（聚合根） | `id`、`title`、`structure` |
| `KnowledgeStructure` | 值对象 | `points`、`relations`；路径与归因查询 |
| `KnowledgePoint` | 实体（结构内） | `id`、`title`、`summary` |
| `KnowledgeRelation` | 值对象 | `kind`（`requires` / `part_of`）、`source`、`target` |
| `LearningGoal` | 实体（关联聚合） | `id`、`learner_id`、`course_id`、`statement`、`outcomes`、`requirements`、`time_window`、`daily_effort`、`prerequisites`、`transitions` |
| `LearnerId` / `CourseId` / `GoalId` / `KnowledgePointId` | 值对象 | 非空、无首尾空白；不同类型不相等 |
| `TimeWindow` / `DailyEffort` / `CapabilityRequirement` / `GoalTransition` | 值对象 | 目标的时间范围、每日投入、能力要求、状态迁移 |

知识结构的查询：

| 方法 | 回答 |
| ---- | ---- |
| `order()` | 先学什么：`requires` 拓扑序，并列按声明顺序 |
| `entry_points()` | 哪些点可以开始：没有前置 |
| `prerequisites_of(id)` | 直接前置，按声明顺序 |
| `prerequisite_closure(id)` | 全部传递前置，按学习顺序——**补课顺序** |
| `dependent_closure(id)` | 全部传递后继，按学习顺序——**影响范围** |
| `parent_of(id)` / `children_of(id)` | 主题层级（`part_of`） |
| `point(id)` | 按 id 取知识点（不存在时抛错） |

目标的查询：

| 方法 | 回答 |
| ---- | ---- |
| `status` | 当前状态——由迁移历史派生 |
| `is_active` / `is_terminal` | 是否进行中 / 是否已终态 |
| `knowledge_point_ids` | 目标绑定的知识点，按声明顺序 |
| `points_for(outcome)` | 支撑某条学习结果的知识点 |
| `outcomes_for(point_id)` | 某个知识点支撑的学习结果 |
| `time_window.has_passed(now)` | 时间范围是否已过（调用方传入「现在」） |

目标的迁移：

| 方法 | 迁移 |
| ---- | ---- |
| `bind_requirements(requirements)` | 草稿 → 草稿：绑定能力要求 |
| `activate(at=, reason=)` | 草稿 → 进行中（必须已绑定能力要求） |
| `suspend(at=, reason=)` | 进行中 → 已暂停 |
| `resume(at=, reason=)` | 已暂停 → 进行中 |
| `achieve(at=, reason=)` | 进行中 → 已达成 |
| `abandon(at=, reason=)` | 草稿 / 进行中 / 已暂停 → 已放弃 |
| `expire(at=, reason=)` | 进行中 / 已暂停 → 已过期（必须有时间范围） |

两个桥：

| 函数 | 作用 |
| ---- | ---- |
| `to_goal_spec(goal)` | `LearningGoal` → `GoalSpec`，交给 `core.agent` 规划 |
| `check_goal_against_course(goal, course)` | 跨聚合一致性检查，返回 `GoalIssue` 列表 |

## 2. 快速上手

```python
from agent_learning_coach.core.agent import OutcomePlanner
from agent_learning_coach.core.learning import (
    CapabilityRequirement,
    Course,
    CourseId,
    DailyEffort,
    GoalId,
    KnowledgePoint,
    KnowledgePointId,
    KnowledgeRelation,
    KnowledgeStructure,
    LearnerId,
    LearningGoal,
    TimeWindow,
    check_goal_against_course,
    to_goal_spec,
)


def kp(point_id: str, title: str) -> KnowledgePoint:
    return KnowledgePoint(id=KnowledgePointId(point_id), title=title)


structure = KnowledgeStructure(
    points=(kp("k1", "函数与作用域"), kp("k2", "闭包"), kp("t1", "语言基础")),
    relations=(
        KnowledgeRelation(
            kind="requires",
            source=KnowledgePointId("k2"),
            target=KnowledgePointId("k1"),
        ),
        KnowledgeRelation(
            kind="part_of",
            source=KnowledgePointId("k2"),
            target=KnowledgePointId("t1"),
        ),
    ),
)
course = Course(id=CourseId("course-js-core"), title="JavaScript 核心", structure=structure)

order = structure.order()  # k1, t1, k2
closure = structure.prerequisite_closure(KnowledgePointId("k2"))  # k1

# 目标：先草稿，诊断后绑定知识点，再激活
goal = LearningGoal(
    id=GoalId("goal-closures"),
    learner_id=LearnerId("learner-1"),
    course_id=course.id,
    statement="掌握 JavaScript 闭包",
    outcomes=("说清闭包捕获了什么", "能写出一个闭包例子"),
    time_window=TimeWindow(start=date(2026, 11, 24), end=date(2027, 1, 24)),
    daily_effort=DailyEffort(minutes_per_day=90),
    prerequisites=(KnowledgePointId("k1"),),
)
goal = goal.bind_requirements(
    (CapabilityRequirement(point_id=KnowledgePointId("k2"), outcome="能写出一个闭包例子"),),
)
goal = goal.activate(at=time.time(), reason="诊断完成")

issues = check_goal_against_course(goal, course)  # ()
draft = await OutcomePlanner().plan(to_goal_spec(goal))
```

完整可运行示例：

- `uv run python examples/learning_domain.py`——聚合边界与不变量
- `uv run python examples/knowledge_structure.py`——关系、路径、归因
- `uv run python examples/learning_goal.py`——目标状态机、约束、绑定与交接

## 3. 不变量

违反时抛 `DomainError`，消息说明哪里不对。**校验只在构造时发生**，因此拿到
手的对象一定自洽。

| 对象 | 不变量 | 错误信息示例 |
| ---- | ---- | ---- |
| 所有 id | 非空、无首尾空白 | `标识不能包含首尾空白：' c1'` |
| `KnowledgeStructure` | 知识点 id 唯一 | `知识点 id 重复：k1` |
| `KnowledgeStructure` | 关系端点存在 | `requires 关系引用了不存在的知识点：k9` |
| `KnowledgeRelation` | 不自指 | `知识点 k1 的 requires 关系不能指向自身` |
| `KnowledgeStructure` | 关系不重复 | `重复的关系：requires k1 -> k2` |
| `KnowledgeStructure` | `requires` 无环 | `requires 依赖成环：k1 -> k2 -> k1` |
| `KnowledgeStructure` | `part_of` 一子一父 | `知识点 k1 有多个 part_of 父节点：t1、t2` |
| `KnowledgeStructure` | `part_of` 无环 | `part_of 层级成环：a -> b -> a` |
| `LearningGoal` | 陈述非空、至少一条结果 | `目标「掌握闭包」至少要有一条可验证的学习结果` |
| `LearningGoal` | 能力要求的结果出自 `outcomes` | `能力要求的结果「另一个结果」不在目标的学习结果中：…` |
| `LearningGoal` | 同一（知识点, 结果）只有一条能力要求 | `同一目标对知识点 k2 的结果「…」只能有一条能力要求` |
| `LearningGoal` | 前置条件不重复 | `前置条件不能重复` |
| `LearningGoal` | 迁移历史不断链 | `目标 g1 的迁移历史断链：期望来源状态为进行中，实际为草稿` |
| `LearningGoal` | 迁移历史不含非法迁移 | `目标 g1 的迁移历史包含非法迁移：已达成 -> 已放弃` |
| `LearningGoal` | 终态不可回退 | `目标 g1 状态为已达成，不能再变更为已放弃` |
| `LearningGoal` | 激活前必须已绑定能力要求 | `目标 g1 还没绑定任何能力要求，不能进入进行中状态` |
| `LearningGoal` | 激活后不能再改绑定 | `目标 g1 状态为进行中，不能再绑定能力要求` |
| `LearningGoal` | 没有时间范围不能过期 | `目标 g1 没有时间范围，不存在过期` |
| `TimeWindow` | 结束晚于开始 | `目标时间范围的结束日期 2026-01-01 必须晚于开始日期 2026-03-01` |
| `DailyEffort` | 分钟数为正 | `每日投入时间必须是正整数分钟：0` |
| `GoalTransition` | 理由非空 | `状态迁移理由不能为空` |

## 4. 边界

```text
core.learning.domain      实体、值对象、知识结构（不认识模型 / 工具 / 运行时）
        ↑
core.learning.goal        目标状态机、结构化约束、能力要求
        ↑
core.learning.consistency 跨聚合一致性检查（领域服务）
        ↑
core.learning.bridge      to_goal_spec → GoalSpec
        ↓
core.agent                 计划、反思、运行时（不认识学习领域）
```

| 规则 | 原因 |
| ---- | ---- |
| 跨聚合只按 id 引用 | 双向引用必须双写；双写等于两份事实 |
| `Learner` 不持有目标列表 | 「这个学习者有哪些目标」是查询，不是聚合状态 |
| `LearningGoal` 独立聚合 | 它有自己的生命周期；多目标并行合法 |
| 关系外置在结构里 | 关系是结构的事实；放在点上会让改一条边复制一个点 |
| `requires` 与 `part_of` 分开 | 一个决定「能不能开始」，一个决定「归到哪个主题」 |
| 实体不可变 | 历史与审计不能被原地改掉；对象可安全传递 |
| 状态由迁移历史派生 | 状态与历史不能各说一套 |
| 领域层不读时钟 | 「现在几点」是外部事实，由应用层传入 |
| 方向单一 | 领域模型可以脱离 LLM / 工具单独测试 |

### 保证责任的三档

「目标约束该由领域层还是应用层保证」的答案：**按判断需要什么数据分三档**。

| 档位 | 判断依据 | 落在哪里 | 例子 |
| ---- | -------- | -------- | ---- |
| 聚合内自洽 | 只看目标自己的数据 | `goal.py` 构造时校验 | 时间范围自洽、投入为正、结果出自 `outcomes` |
| 跨聚合一致 | 需要另一个聚合的**已有数据** | `consistency.py` 领域服务 | 引用的知识点是否存在于目标课程中 |
| 依赖外部事实 | 需要时钟、掌握度、学习证据 | **应用层** | 是否过期、前置是否已掌握、是否真的达成 |

领域服务**只报告不一致，不替调用方做决定**：

```python
issues = check_goal_against_course(goal, course)
for issue in issues:
    print(issue.code, issue.message)
# unknown_requirement_point 目标 g1 的能力要求引用了课程 c1 中不存在的知识点：k9
```

## 5. 设计取舍

| 替代方案 | 为什么不选 |
| ---- | ---- |
| 状态作为字段存储 | 会与迁移历史各说一套；派生让不一致无法表达 |
| 自由文本约束（`kind` + `text`） | 无法校验，也无法参与计算；只能给模型看 |
| 一个「时间」约束同时含范围与投入 | 两者独立变化：范围决定还剩多久，投入决定每天能学多少 |
| 目标级「难度」字段 | 难度是知识点的属性（课程侧）；复制到目标会让同一知识点在不同目标下难度不同 |
| 能力要求另立学习结果 | 同一件事两份说法，映射给规划器会变成重复任务 |
| 绑定知识点进 `GoalSpec` | `GoalSpec` 是领域无关契约；任务粒度对齐是 Topic 23 的问题 |
| 引用完整性交给应用层 | 它只需要另一个聚合对象，是纯函数，可以留在领域层；真正要上移的是「是否过期」「是否已掌握」 |
| 一致性问题抛异常 | 一次检查可能发现多个问题，逐个抛异常只让调用方看到第一个 |
| 目标与知识点合并成一个聚合 | 知识点属于课程；合并会让加载一门课拉起所有目标 |

## 6. 未覆盖

- **持久化**：没有 `to_dict` / `from_dict`，也没有版本号；迁移历史接数据库后
  需要考虑独立成表。
- **掌握度 / 已有知识**：留给 Topic 18 / 20 / 41。「前置是否已掌握」「目标是否
  真的达成」都需要它，因此这两条判断留在应用层。
- **目标与知识点的跨课程身份**：知识点仍以课程为边界，全局身份留给 Topic 22。
- **计划粒度与知识点对齐**：`GoalSpec` 不带知识点，任务如何落到知识点上留给
  Topic 23。
- **复习调度与计划调整**：`suspended` / `expired` 两个状态目前没有产品侧强需求
  驱动，等 Topic 48 / 50 检验。

## 7. 验证

```bash
uv run --group dev pytest -q tests/test_learning_domain.py tests/test_knowledge_structure.py tests/test_learning_goal.py
uv run python examples/learning_domain.py    # 聚合边界与拒绝演示
uv run python examples/knowledge_structure.py # 路径、补课顺序、影响范围
uv run python examples/learning_goal.py      # 状态机、约束、绑定与交接
uv run python examples/learning_plan.py      # 领域目标 → 计划闭环
```
