# 学习领域（Learning Domain）

> 状态：生效
> 对应代码：`src/agent_learning_coach/core/learning/`
> 决策依据：[ADR-0015](../architecture/decisions/0015-learning-domain-boundaries.md)

本文是学习领域模型的接入说明：**有哪些对象**、**怎么用**、**边界在哪**。

## 1. 核心契约

三个独立聚合，一个聚合内部实体，其余是值对象：

| 类型 | 分类 | 关键内容 |
| ---- | ---- | ---- |
| `Learner` | 实体 | `id`、`name`、`background`、`preferences`；`rename()` |
| `Course` | 实体（聚合根） | `id`、`title`、`knowledge_points`；校验唯一性/前置/无环 |
| `KnowledgePoint` | 实体（课程内） | `id`、`title`、`prerequisites` |
| `LearningGoal` | 实体（关联聚合） | `id`、`learner_id`、`course_id`、`statement`、`outcomes`、`constraints`、`status` |
| `LearnerId` / `CourseId` / `GoalId` / `KnowledgePointId` | 值对象 | 非空、无首尾空白；不同类型不相等 |
| `GoalConstraint` | 值对象 | `kind`（`time` / `prerequisite` / `preference`）、`text`；`render()` |

两个派生视图：

| 方法 | 作用 |
| ---- | ---- |
| `Course.knowledge_order()` | 拓扑序：先学什么（派生，不持久化） |
| `Course.knowledge_point(id)` | 按 id 取知识点（不存在时抛错） |

一个桥：

| 函数 | 作用 |
| ---- | ---- |
| `to_goal_spec(goal)` | `LearningGoal` → `GoalSpec`，交给 `core.agent` 规划 |

## 2. 快速上手

```python
from agent_learning_coach.core.agent import OutcomePlanner
from agent_learning_coach.core.learning import (
    Course,
    CourseId,
    GoalConstraint,
    GoalId,
    KnowledgePoint,
    KnowledgePointId,
    LearnerId,
    LearningGoal,
    to_goal_spec,
)

course = Course(
    id=CourseId("course-js-core"),
    title="JavaScript 核心",
    knowledge_points=(
        KnowledgePoint(id=KnowledgePointId("k1"), title="函数与作用域"),
        KnowledgePoint(
            id=KnowledgePointId("k2"),
            title="闭包",
            prerequisites=(KnowledgePointId("k1"),),
        ),
    ),
)
order = course.knowledge_order()  # k1, k2

goal = LearningGoal(
    id=GoalId("goal-closures"),
    learner_id=LearnerId("learner-1"),
    course_id=course.id,
    statement="掌握 JavaScript 闭包",
    outcomes=("说清闭包捕获了什么", "写出三个闭包例子"),
    constraints=(GoalConstraint(kind="time", text="每周 3 小时"),),
)
draft = await OutcomePlanner().plan(to_goal_spec(goal))
```

完整可运行示例：`uv run python examples/learning_domain.py`。

## 3. 不变量

违反时抛 `DomainError`，消息说明哪里不对。**校验只在构造时发生**，因此拿到
手的对象一定自洽。

| 对象 | 不变量 | 错误信息示例 |
| ---- | ---- | ---- |
| 所有 id | 非空、无首尾空白 | `标识不能包含首尾空白：' c1'` |
| `Course` | 知识点 id 唯一 | `课程「A」中知识点 id 重复：k1` |
| `Course` | 前置存在 | `知识点「闭包」的前置 k0 不在课程内` |
| `Course` | 不自指、不重复声明前置 | `知识点「函数」不能依赖自身` |
| `Course` | 依赖无环 | `课程知识点依赖成环：k1 -> k2 -> k1` |
| `LearningGoal` | 陈述非空、至少一条结果 | `目标「掌握闭包」至少要有一条可验证的学习结果` |
| `LearningGoal` | 终态不可回退 | `目标 g1 状态为已达成，不能再变更为已放弃` |

## 4. 边界

```text
core.learning.domain   实体、值对象、不变量（不认识模型 / 工具 / 运行时）
        │
        ▼
core.learning.bridge   to_goal_spec → GoalSpec
        │
        ▼
core.agent             计划、反思、运行时（不认识学习领域）
```

| 规则 | 原因 |
| ---- | ---- |
| 跨聚合只按 id 引用 | 双向引用必须双写；双写等于两份事实 |
| `Learner` 不持有目标列表 | 「这个学习者有哪些目标」是查询，不是聚合状态 |
| `LearningGoal` 独立聚合 | 它有自己的生命周期；多目标并行合法 |
| 实体不可变 | 历史与审计不能被原地改掉；对象可安全传递 |
| 方向单一 | 领域模型可以脱离 LLM / 工具单独测试 |

## 5. 设计取舍

| 替代方案 | 为什么不选 |
| ---- | ---- |
| id 用裸 `str` | 学习者 id 与课程 id 可以互换，错误在运行时才暴露；类型化 id 让 mypy 直接拦住 |
| `Learner` 内嵌 `goals` 列表 | 放弃一个目标会变成修改学习者；加载学习者要拉起全部目标；需要双写 |
| 可变实体 + setter | 历史被原地覆盖，「为什么变成现在这样」答不上来 |
| Pydantic 模型承载领域对象 | 领域对象不需要 JSON Schema；`frozen` dataclass 已提供不可变与相等语义，且零运行时依赖 |
| 仓储层 / ORM | 数据库选型未定（TBD）；现在造仓储，选型一变就重写 |
| 把 `knowledge_order()` 结果缓存进对象 | 顺序是派生视图；缓存会让「依赖改了、顺序没变」成为可能 |

## 6. 未覆盖

- **引用完整性**：`LearningGoal.learner_id` / `course_id` 是否真的存在，内存里
  无法校验，需要应用层或存储层保证。
- **持久化**：没有 `to_dict` / `from_dict`，也没有版本号；接数据库时要做，
  且写入路径必须复用同一套不变量。
- **知识点跨课程共享**：留给 Topic 16。
- **掌握度 / 已有知识**：留给 Topic 18 / 20。
- **学习领域注册表**：等有多个领域的使用场景再做。

## 7. 验证

```bash
uv run --group dev pytest -q tests/test_learning_domain.py   # 19 passed
uv run python examples/learning_domain.py                    # 边界与拒绝演示
uv run python examples/learning_plan.py                      # 领域目标 → 计划闭环
```
