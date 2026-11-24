# 学习领域（Learning Domain）

> 状态：生效
> 对应代码：`src/agent_learning_coach/core/learning/`
> 决策依据：[ADR-0015](../architecture/decisions/0015-learning-domain-boundaries.md)、
> [ADR-0016](../architecture/decisions/0016-knowledge-representation.md)

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
| `LearningGoal` | 实体（关联聚合） | `id`、`learner_id`、`course_id`、`statement`、`outcomes`、`constraints`、`status` |
| `LearnerId` / `CourseId` / `GoalId` / `KnowledgePointId` | 值对象 | 非空、无首尾空白；不同类型不相等 |
| `GoalConstraint` | 值对象 | `kind`（`time` / `prerequisite` / `preference`）、`text`；`render()` |

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
    KnowledgeRelation,
    KnowledgeStructure,
    LearnerId,
    LearningGoal,
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

完整可运行示例：

- `uv run python examples/learning_domain.py`——聚合边界与不变量
- `uv run python examples/knowledge_structure.py`——关系、路径、归因

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
| `LearningGoal` | 终态不可回退 | `目标 g1 状态为已达成，不能再变更为已放弃` |

## 4. 边界

```text
core.learning.domain   实体、值对象、知识结构（不认识模型 / 工具 / 运行时）
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
| 关系外置在结构里 | 关系是结构的事实；放在点上会让改一条边复制一个点 |
| `requires` 与 `part_of` 分开 | 一个决定「能不能开始」，一个决定「归到哪个主题」 |
| 实体不可变 | 历史与审计不能被原地改掉；对象可安全传递 |
| 方向单一 | 领域模型可以脱离 LLM / 工具单独测试 |

## 5. 设计取舍

| 替代方案 | 为什么不选 |
| ---- | ---- |
| 前置列表挂在 `KnowledgePoint` 上 | 改一条关系要复制知识点；顺序与闭包都要重新拼图 |
| 一种「关联」关系 | 顺序计算分不清哪条边该阻塞；归因与层级都失去判据 |
| `part_of` 允许多父 | 「这个点属于哪」没有唯一答案，归因与展示都含糊 |
| 增加 `related` 无向边 | 没有消费者；无向去重与对称闭包让校验复杂度翻倍 |
| 持久化顺序 / 闭包 | 派生视图缓存会导致「关系改了、顺序没变」 |
| id 用裸 `str` | 学习者 id 与课程 id 可以互换，错误在运行时才暴露 |
| `Learner` 内嵌 `goals` 列表 | 放弃一个目标会变成修改学习者；加载学习者要拉起全部目标 |
| Pydantic 模型承载领域对象 | 领域对象不需要 JSON Schema；`frozen` dataclass 已提供不可变与相等语义 |
| 仓储层 / ORM | 数据库选型未定（TBD）；现在造仓储，选型一变就重写 |

## 6. 未覆盖

- **引用完整性**：`LearningGoal.learner_id` / `course_id` 是否真的存在，内存里
  无法校验，需要应用层或存储层保证。
- **持久化**：没有 `to_dict` / `from_dict`，也没有版本号；接数据库时要做，
  且写入路径必须复用同一套不变量。
- **跨课程知识地图**：知识点仍以课程为边界；全局身份留给 Topic 22。
- **掌握度 / 已有知识**：留给 Topic 18 / 20。归因查询只是原语，诊断策略不在这层。
- **`related` 无向关联**：等出现「相关知识点推荐」需求再加。
- **学习领域注册表**：等有多个领域的使用场景再做。

## 7. 验证

```bash
uv run --group dev pytest -q tests/test_learning_domain.py tests/test_knowledge_structure.py
uv run python examples/learning_domain.py     # 聚合边界与拒绝演示
uv run python examples/knowledge_structure.py # 路径、补课顺序、影响范围
uv run python examples/learning_plan.py       # 领域目标 → 计划闭环
```
