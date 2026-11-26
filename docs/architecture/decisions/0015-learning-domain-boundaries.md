# ADR-0015: 学习领域的三聚合边界与实体/值对象划分

- 状态：已接受
- 日期：2026-11-24

## 背景

Topic 15 的工程问题是：**Learner、Course、Learning Goal 三个概念的边界在哪里？
哪些是实体，哪些是值对象？**

这是 Phase 1 的第一天，而 Phase 1 的交付物（Learner Profile、知识结构、学习
目标管理、诊断、计划生成、计划持久化）全都建立在这三个概念之上。边界定错，
后面每个主题都要在错误的形状上加工。

本项目有三个额外约束：

1. **领域模型必须能独立测试。** 诊断、掌握度、计划调整将来的逻辑都在领域侧，
   如果领域模型拖着 LLM 与工具栈，单元测试就得联网或打桩到失去意义。
2. **运行时不能认识学习业务。** ADR-0013 已经确定：`core.agent` 只认识
   `GoalSpec`（领域无关的目标描述）。领域模型今天必须明确「谁依赖谁」。
3. **学习状态是不能被静默改写的证据。** 目标是否达成、计划为什么调整，都要
   能回答「什么时候、依据什么」。可变的领域对象会让历史被原地覆盖。

教材在本主题没有对应章节（Phase 1 无主教材），因此边界由本项目自己定。

## 决策

**三个独立聚合：`Learner`、`Course`、`LearningGoal`；`KnowledgePoint` 属于
`Course` 聚合；其余都是值对象。**

| 概念 | 分类 | 身份 | 归属 |
| ---- | ---- | ---- | ---- |
| `Learner` | 实体 | `LearnerId` | 独立聚合 |
| `Course` | 实体 | `CourseId` | 独立聚合（聚合根） |
| `KnowledgePoint` | 实体 | `KnowledgePointId` | `Course` 聚合内部 |
| `LearningGoal` | 实体 | `GoalId` | 独立聚合：Learner × Course 的关联 |
| `LearnerId` / `CourseId` / `GoalId` / `KnowledgePointId` | 值对象 | 无 | 不单独存在 |
| `GoalConstraint` | 值对象 | 无 | 不单独存在 |

### 1. 聚合边界按「一致性边界」划分，不按「包含关系」划分

一个概念是不是独立聚合，取决于**哪些不变量必须在一次修改里同时成立**：

- `Course` 的不变量是「知识点 id 唯一、前置存在且无环」——需要看到整门课的
  知识点才能判断，因此 `KnowledgePoint` 放在 `Course` 聚合内。
- `LearningGoal` 的不变量是它**自己的**生命周期（进行中 → 已达成 / 已放弃）。
  「同一学习者不能同时有两个进行中目标」**不是**本项目的不变量——多目标并行
  是合法的学习场景。因此目标不需要为了维护这条不变量而挤进 `Learner` 聚合。

把目标放进 `Learner` 的代价是具体的：放弃一个目标会变成「修改学习者」，加载
学习者会拉起全部目标，两个目标的状态修改必须串行化。这些代价在本项目里没有
对应的收益。

### 2. 跨聚合只按 id 引用，单向

- `LearningGoal` 记住 `learner_id` 与 `course_id`，**不持有** `Learner` /
  `Course` 对象。
- `Learner` **不持有**目标列表。

理由：双向引用必须双写，而双写是「同一个事实有两份副本」的同义词。目标属于
哪个学习者、使用哪门课程，只需要在目标上存一次；「这个学习者有哪些目标」是
查询，不是聚合状态。

### 3. 实体不可变，身份来自 id

实体一律 `frozen` dataclass；改名、达成、放弃都返回**新实例**。好处：

- 历史与审计不会被原地改掉；
- 对象可以安全地在任意层之间传递，不需要防御性拷贝；
- 同一批数据上的比较与测试是确定的。

「身份来自 id」不是修辞：`LearnerId("x")` 与 `CourseId("x")` 是**不同类型**，
不相等。把学习者 id 当课程 id 传进来会在类型检查或相等判断处暴露，而不是变成
一个查不到的 id。

### 4. 不变量在构造时校验

`Course` 与 `LearningGoal` 的 `__post_init__` 校验：

| 对象 | 不变量 | 违反时 |
| ---- | ---- | ---- |
| 四类 id | 非空、无首尾空白 | `DomainError` |
| `Course` | 知识点 id 唯一 | `DomainError`（指出重复的 id） |
| `Course` | 前置存在、不自指、不重复 | `DomainError`（指出知识点与前置） |
| `Course` | 依赖无环 | `DomainError`（给出环路径，如 `k1 -> k2 -> k1`） |
| `LearningGoal` | 陈述非空、至少一条学习结果、结果非空 | `DomainError` |
| `LearningGoal` | 终态不可回退（`achieved` / `abandoned` → 不可再迁移） | `DomainError` |

错误信息面向调用方与用户，说明「哪里不对」；否则调用方只能靠猜或抓字符串。

### 5. 知识顺序是派生视图，不持久化

`Course.knowledge_order()` 给出拓扑序，只服务「先学什么」的展示。它不承担
调度，也不承担历史——与 ADR-0013 的三序分离同一个原则：把展示顺序当调度，
跳过与重开会让它偏离就绪集合；把展示顺序当历史，新插入的前置会排到旧任务
之前，修订记录开始说谎。

### 6. 依赖方向：`learning → agent`，单向，且只有一层

```
core.learning.domain   （不认识模型/工具/运行时）
        ↑
core.learning.bridge   →  GoalSpec  →  core.agent
```

- 领域模型文件不 import `core.agent`；
- 映射只在 `bridge.to_goal_spec` 里发生；
- `core.agent` 永远不认识 `LearningGoal`、知识点、掌握度。

这条边界让领域模型可以脱离 LLM 与工具单独测试（今天的 19 个用例全部是纯
内存、无网络）。

### 7. 明确不做

- **不做 Repository / ORM。** Phase 1 目前没有数据库选型（架构文档里 TBD）；
  现在造仓储层，等选型一变就得重写。领域对象是纯内存值，持久化由应用层在
  选型确定后接入。
- **不做领域事件、CQRS、事件溯源。** 当前没有多进程、多存储或跨服务一致性
  需求。
- **不做知识点跨课程共享。** 知识点身份目前在课程内有意义；是否全局唯一留给
  Topic 16（Knowledge Representation）决定。
- **不做掌握度 / 已有知识。** 那是 Topic 18（Learning Progress）与 Topic 20
  （Diagnostic Assessment）的建模对象。
- **不做学习领域注册表。** 「学习领域管理」需要先有多个领域的使用场景；现在
  建注册表只是空壳。

## 后果

- 好处：三个聚合可以分别测试与演化；领域模型不依赖运行时；不变量在入口处
  被强制，后续主题拿到的一定是自洽对象；错误信息可以直接回给模型或用户。
- 代价：实体不可变意味着「更新」都是替换，将来接数据库时需要自己处理版本号
  与并发写（当前没有）；`LearningGoal` 的引用完整性（learner / course 是否
  存在）在内存里无法校验——需要应用层或存储层保证。
- 风险：**不变量只在内存校验。** 将来接数据库时，写入路径必须复用同一套校验，
  否则会出现「内存里合法、库里非法」的两套规则。这是本 ADR 遗留的主要工程债。
- 边界：`LearningGoal` 没有 `knowledge_point_ids`——目标绑定知识点是 Topic 17
  （Learning Goal Modeling）的建模内容，今天不预设。

## 补充说明（2026-11-24）

实现中确认的事实：

1. **`GoalConstraint` 必须是值对象**：它在 `GoalSpec.constraints` 里被渲染成
   文本（`时间：每周 3 小时`），没有 id 需要。给它加 id 只会让「同一条约束」
   出现多个身份，比较变得不可靠。
2. **`Learner.background` 不做建模**：它没有独立语义、不参与任何不变量，
   用 `tuple[str, ...]` 比造一个 `Background` 值对象更诚实。等诊断需要结构化
   背景时（Topic 20），再把它升级成值对象。
3. **不可变 + `replace` 足够**：不需要 Lombok 式的 with-er 生成，`dataclasses.replace`
   就是「返回改了字段的新实例」，且类型检查能发现漏改。
4. **环路径必须打印出来**：只说「依赖成环」，调用方还得自己找环；给出
   `k1 -> k2 -> k1` 才可以直接定位。实现是 DFS，且错误信息用 id 而不是标题
   （标题可能重复，id 唯一）。
5. **`to_goal_spec` 不放 `domain.py`**：最初自然想写成 `LearningGoal.to_spec()`，
   那会让领域模块 import `core.agent`，测试领域模型时连带拉起 LLM 与工具依赖。
   移到 `bridge.py` 后，依赖方向仍是一条边，但领域文件保持纯净。

接入说明见 [`docs/engineering/domain.md`](../../engineering/domain.md)。
学习笔记见 [`docs/learning/topics/15-domain-driven-design-basics.md`](../../learning/topics/15-domain-driven-design-basics.md)。

## 补充说明（2026-11-24，Topic 16）

Topic 16 重构了知识结构的形状，本 ADR 的聚合边界不变，具体接口有三处演进：

1. **关系外置**：`KnowledgePoint.prerequisites` 移除，前置与归属成为
   `KnowledgeStructure` 里的 `KnowledgeRelation`（ADR-0016）。理由：关系是结构的
   事实，不是点的属性；改一条边不应复制知识点。
2. **`Course` 持有结构**：`Course.knowledge_points` 与 `Course.knowledge_order()`
   被 `Course.structure` 与 `structure.order()` 取代；查询（顺序、闭包、层级）
   都由结构回答。
3. **跨课程共享**：本 ADR 留给 Topic 16 的问题已决定——知识点仍以课程为边界，
   全局知识地图留给 Topic 22。

知识点仍然是 `Course` 聚合内部的实体，`LearningGoal` 仍然只按 id 引用课程。

## 补充说明（2026-11-24，Topic 17）

Topic 17 深化了目标这个聚合，本 ADR 的聚合边界不变，具体接口有四处演进：

1. **目标模型独立成 `core/learning/goal.py`**：`LearningGoal` 与它的值对象
   （`TimeWindow` / `DailyEffort` / `CapabilityRequirement` / `GoalTransition`）
   从 `domain.py` 拆出，`domain.py` 保留学习者、课程与知识结构。
2. **状态机取代单一 `status` 字段**：六状态（草稿 / 进行中 / 已暂停 / 已达成 /
   已放弃 / 已过期），状态**由迁移历史派生**；每次迁移留理由与时间
   （ADR-0017）。
3. **约束从自由文本升级为结构化值对象**：`GoalConstraint(kind, text)` 移除。
   自由文本无法校验、无法计算，只能给模型看。
4. **目标按 id 绑定知识点**：本 ADR 留下的「`LearningGoal` 没有
   `knowledge_point_ids`」在本主题决定——绑定用 `CapabilityRequirement`，
   仍然**只按 id 引用**，不嵌入 `Course`。

同时，本 ADR 把「引用完整性归应用层或存储层」这句话细化为三档（ADR-0017）：

| 判断依据 | 落在哪里 |
| -------- | -------- |
| 只看目标自己的数据 | 领域层构造时校验 |
| 需要另一个聚合的已有数据 | 领域服务（`consistency.check_goal_against_course`） |
| 需要时钟、掌握度、学习证据 | 应用层 |

原话「在内存里无法校验」混淆了「需要外部事实」与「需要别的聚合」：后者是纯
函数，可以留在领域层并脱离基础设施测试。

## 补充说明（2026-11-24，Topic 18）

Topic 18 增加了第四个聚合，本 ADR 的三聚合边界不变，但「不做什么」清单有两项
被本主题兑现：

1. **「不做掌握度 / 已有知识」已部分兑现**。本 ADR 把它留给 Topic 18 与
   Topic 20：Topic 18 交付了**知识点级学习状态**（`LearningState`）与**已有
   知识的表达方式**（迁移序列的第一条，带 `source_kind="assessed"`）；掌握程度
   的**量化**（概率、等级）仍留给 Topic 43，诊断**策略**留给 Topic 20
   （ADR-0018）。
2. **`Learner` 依然不持有任何跨聚合的状态**。本主题明确：进度是
   （学习者 × 课程）的独立聚合，**不进 Learner**。理由与「Learner 不持有目标
   列表」同源——画像与进度的**变化频率**差几个数量级，且同一学习者的多个目标
   会互相污染。

另外，本主题把「进度绑知识点还是绑计划任务」定了下来：**绑知识点**。计划任务
是运行时的可抛弃对象（可跳过、可重建），知识点是领域的稳定身份；且「掌握了吗」
不由「哪些步骤做完了」回答。详见 ADR-0018。

## 补充说明（2026-11-25，Topic 19）

Phase 1 的领域评审（ADR-0019）回头读了本 ADR，发现**一条 ADR 的理由会随前提
变化而失效**：

本 ADR §7 写着「**不做 Repository / ORM**。Phase 1 目前没有数据库选型（架构
文档里 TBD）；现在造仓储层，等选型一变就得重写」。这个**理由已经不成立**：

- 架构文档的选型表已写明「关系型数据库：PostgreSQL（计划采用）」；
- `pyproject.toml` 已引入 `psycopg[binary]` 与 `pgvector`；
- `core/rag/store.py` 已有 pgvector 的生产实现与建表语句（ADR-0011）。

评审的处理是**把理由与结论分开**：选型已定（记入 ADR-0019），但**实现形态未定**
（不可变实体与迁移历史怎么存），因此仓储层仍然不做。理由失效不等于结论失效——
这是评审本 ADR 时最值得记下的一条。

评审同时确认了本 ADR 的其他决策在 Phase 1 结束时**仍然成立**：三聚合边界（现已
四聚合，见 Topic 18）、实体不可变、跨聚合只按 id 引用、不变量在构造时校验。
十二条不变量的 ADR 出处清单见 ADR-0019 §3。
