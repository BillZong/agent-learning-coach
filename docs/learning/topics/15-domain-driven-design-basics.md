# Topic: Domain-Driven Design 基础

> Day 15 ｜ Phase 1 · Learning Core ｜ 主教材：—（本阶段无主教材章节） ｜ 外部参考：Eric Evans DDD、Vaughn Vernon 聚合设计、Martin Fowler ValueObject ｜ 状态：已完成

## 1. Engineering Question

Learner、Course、Learning Goal 三个概念的边界是什么？哪些是实体，哪些是值对象？

## 2. Primary Reading

- Book: —（本阶段无主教材章节）
- Chapter / Section: —
- 核心观点：
  - **实体（Entity）**：由**身份**定义，而不是由属性定义。同一个学习者改了名字、
    换了目标，仍然是同一个学习者；身份在生命周期内不变。
  - **值对象（Value Object）**：描述某个特征，**没有身份概念**；按值相等，
    应当不可变。金额、日期区间是典型例子；本项目里是各类 id 与目标约束。
  - **聚合（Aggregate）**：一组必须**同时保持一致**的对象，外部只能通过聚合根
    访问。聚合的边界由「哪些不变量必须在一个事务里成立」决定，不由包含关系
    决定。
  - **聚合之间按 id 引用**，不持有对方的对象引用——这是避免双写与级联加载的
    第一原则。

## 3. Cross References

| 来源 | 核心观点 | 与主教材的关系 |
|---|---|---|
| Eric Evans, *Domain-Driven Design*（2003） | 实体由身份定义；值对象描述特征、无身份、应不可变；聚合是一致性边界 | 本主题没有主教材章节，这条是本项目的分类依据：`Learner` / `Course` / `LearningGoal` 有身份 → 实体；四类 id 与 `GoalConstraint` 无身份 → 值对象 |
| Vaughn Vernon, *Implementing Domain-Driven Design*（2013）；*Effective Aggregate Design* | **设计小聚合**；用不变量确定边界；引用其他聚合用 id；一次事务只改一个聚合 | 决定了本项目不把 `LearningGoal` 塞进 `Learner`：目标的生命周期是它自己的不变量，不需要学习者的整份状态参与 |
| Martin Fowler, bliki: *ValueObject* | 值对象是「相等性不基于身份」的小对象；不可变使其可以安全共享 | 支撑「id 用值对象而不是裸 `str`」：`CourseId` 与 `LearnerId` 相等性不同，传错类型立刻暴露 |
| 本项目 ADR-0013 / ADR-0015 | 领域模型与运行时单向解耦：`GoalSpec` 是交接形状；顺序是派生视图，不持久化 | 领域模型不认识 `core.agent`；映射只发生在 `bridge.to_goal_spec`。`structure.order()` 与计划的「展示顺序」同一个原则：派生、可重算、不落库 |

## 4. Compare and Challenge

- **不同资料有哪些一致观点？**
  身份是实体的判定标准；值对象按值相等且不可变；聚合应当小，跨聚合按 id
  引用。三份资料在这一点上没有分歧——分歧发生在「边界画在哪」。
- **哪些观点存在分歧？**
  - **`LearningGoal` 是不是独立的聚合根？** Evans 与 Vernon 都允许两种画法：
    它可以是一个独立聚合，也可以是 `Learner` 聚合内的实体。区别不在于对错，
    而在于**你要保护哪条不变量**。如果「同一学习者不能同时有两个进行中目标」
    是硬规则，目标就必须和学习者放在同一个一致性边界里；本项目认为多目标
    并行是合法的学习场景，因此目标独立。
  - **知识点是否应该跨课程共享？** DDD 资料默认「知识」这类概念倾向于全局
    身份（像产品目录），但本项目当前只有课程内依赖闭合的需求。Vernon 的
    「小聚合」建议支持先放在课程内，等跨课程查询出现再升级——这正是分歧
    来自**应用场景**而不是理论。
- **分歧来自应用场景、设计目标还是技术演进？**
  两个分歧都来自应用场景：本项目的学习者是「有多个并行目标」的成年人，
  而教科书式的示例多是「订单—订单行」这类强包含关系。把订单行的画法套到
  学习目标上，会得到一个不可演化的大聚合。
- **哪些结论缺少充分证据？**
  - 「小聚合更好」是经验法则，本项目**没有量化**：加载多个小聚合的额外查询
    成本，与一个大聚合的一致性收益，谁更大，没有数据。
  - 「类型化 id 能减少错误」——今天的 19 个测试能证明**类型检查与相等判断
    有效**，但证明不了「减少线上 bug」：还没有真实调用方。
  - 「不变量必须在构造时校验」是设计选择；它把错误前移到建模时刻，代价是
    调用方必须处理异常。没有对照实验。
- **哪些观点不适用于当前项目？**
  - **领域事件、CQRS、事件溯源**：没有多进程、多存储与跨服务一致性需求。
  - **Repository / ORM 模式**：数据库选型仍是 TBD；现在造仓储层，选型一变
    就得重写。
  - **「聚合内实体也需要独立仓储」**：`KnowledgePoint` 只能经由 `Course` 访问，
    单独给它仓储会把聚合边界撕开。

## 5. Engineering Practice

- **实现了什么？**
  - `core/learning/domain.py`：
    - 值对象：`LearnerId` / `CourseId` / `GoalId` / `KnowledgePointId`（共享
      `_Identifier` 基类，类型不同即不等）、`GoalConstraint`（`render()` 产出
      `时间：每周 3 小时`）。
    - 实体：`Learner`（`rename()` 返回新实例）、`KnowledgePoint`、`Course`
      （构造时校验唯一性/前置/无环）、`LearningGoal`（`achieve()` /
      `abandon()`，终态不可回退）。
    - 派生视图：`Course.structure.order()` 拓扑序、`structure.point(id)`。
    - `DomainError`：错误信息面向调用方，成环时给出环路径。
  - `core/learning/bridge.py`：`to_goal_spec()` 单向映射到 `GoalSpec`。
  - `core/learning/__init__.py`：统一导出。
- **为什么这样设计？**
  - **三聚合独立**：边界由不变量决定。目标的终态规则不需要学习者状态参与，
    所以不需要同一个事务边界。
  - **单向引用**：目标记住 `learner_id` / `course_id`，学习者不记目标列表——
    「有哪些目标」是查询，不是聚合状态。
  - **不可变**：改名、达成、放弃都返回新实例，历史不会被原地覆盖。
  - **构造时校验**：拿到 `Course` 就是自洽的；缺陷在建模时刻暴露，而不是在
    生成计划时才炸。
- **有哪些替代方案？**
  | 替代方案 | 结果 |
  | ---- | ---- |
  | id 用裸 `str` | 学习者 id 与课程 id 可互换，错误留到运行时 |
  | `Learner` 内嵌 `goals` | 放弃目标变成修改学习者；加载学习者拉起全部目标；双写 |
  | 可变实体 + setter | 历史被覆盖，「为什么变成这样」答不上来 |
  | `LearningGoal.to_spec()` | 领域模块被迫 import `core.agent`，领域测试拖着 LLM 依赖 |
  | 把拓扑序缓存到对象 | 依赖改了顺序不刷新——派生视图不该有独立状态 |
- **测试结果如何？**
  - `tests/test_learning_domain.py`：**19 passed**，覆盖四组：分类（类型化 id、
    值对象相等）、身份与不可变、聚合不变量（重复 id / 自指 / 缺失前置 /
    重复前置 / 成环路径 / 查不到知识点）、生命周期与边界（终态不可回退、
    `GoalSpec` 只携带目标字段）。
  - `examples/learning_domain.py` 跑通：课程顺序 `k1, k4, k2, k3`（无依赖保持
    声明顺序，因此确定）；五类建模错误全部被拒绝；改名后身份不变；目标映射
    后由 `OutcomePlanner` 拆出两条任务。
  - `examples/learning_plan.py` 改为使用正式领域模型，删掉临时 dataclass，
    闭环输出不变（计划 3 条任务、一次重规划）。
  - `ruff format --check` / `ruff check` / `mypy` 全绿。
- **实践结果是否支持阅读材料中的观点？**
  支持，但只支持「机制层」：不变量与类型化 id 确实在构造时拦住了错误；
  不支持「收益量化」——没有证据说明这套建模比裸 dict 更省时间或更少出错。
  这与 Phase 0 的结论一致：**机制可验证，效果需要实验**。

## 6. Reflection

1. **今天最重要的知识是什么？**
   聚合边界由**不变量**决定，不由「谁包含谁」决定。一旦把「哪些规则必须在
   一次修改里同时成立」写清楚，`LearningGoal` 应不应该塞进 `Learner` 就不再
   是风格争论，而是一个可以直接回答的问题。
2. **哪个观点改变或修正了原来的理解？**
   原以为「值对象只是不可变的小类」。实际上它的价值在**相等性语义**：四类 id
   变成不同值对象之后，「把学习者 id 当课程 id 传」从运行时故障变成类型错误；
   而 `GoalConstraint` 不设 id，是因为同一条约束出现两次不该有两个身份。
3. **当前实现还有哪些局限？**
   引用完整性（目标引用的学习者/课程是否存在）在内存里无法校验；没有持久化
   与版本号，接数据库时写入路径必须复用同一套不变量；知识点跨课程共享、
   掌握度、诊断都还没建模。顺序是派生的，因此调用方必须自己决定何时重算。

## 7. Output

- Code / PR: `core/learning/`（`domain.py`、`bridge.py`、`__init__.py`）；
  `examples/learning_domain.py`；`examples/learning_plan.py` 改用正式领域模型
- Tests: `tests/test_learning_domain.py`（19 passed）；全量 613 passed
- ADR: [ADR-0015](../../architecture/decisions/0015-learning-domain-boundaries.md)
- Article Notes: [domain.md](../../engineering/domain.md)；
  [architecture.md](../../architecture/architecture.md) 学习领域层一节
- Unresolved Questions:
  - 知识点是否跨课程共享（Topic 16 已决定：仍在课程内，见 ADR-0016）
  - 学习目标是否需要绑定知识点集合（Topic 17）
  - 已有知识/掌握度的模型（Topic 18、Topic 20）
  - 持久化与引用完整性校验放在哪一层（Phase 1 后段）
  - 聚合粒度与小聚合查询成本的量化比较（尚无实验）
