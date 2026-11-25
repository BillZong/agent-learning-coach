# 学习领域（Learning Domain）

> 状态：生效
> 对应代码：`src/agent_learning_coach/core/learning/`
> 决策依据：[ADR-0015](../architecture/decisions/0015-learning-domain-boundaries.md)、
> [ADR-0016](../architecture/decisions/0016-knowledge-representation.md)、
> [ADR-0017](../architecture/decisions/0017-learning-goal-modeling.md)、
> [ADR-0018](../architecture/decisions/0018-learning-progress.md)、
> [ADR-0019](../architecture/decisions/0019-domain-review.md)、
> [ADR-0020](../architecture/decisions/0020-diagnostic-assessment.md)、
> [ADR-0021](../architecture/decisions/0021-structured-generation.md)

本文是学习领域模型的接入说明：**有哪些对象**、**怎么用**、**边界在哪**。

## 1. 核心契约

五个独立聚合，一个聚合内部的值对象结构，其余是值对象：

| 类型 | 分类 | 关键内容 |
| ---- | ---- | ---- |
| `Learner` | 实体 | `id`、`name`、`background`、`preferences`；`rename()` |
| `Course` | 实体（聚合根） | `id`、`title`、`structure` |
| `KnowledgeStructure` | 值对象 | `points`、`relations`；路径与归因查询 |
| `KnowledgePoint` | 实体（结构内） | `id`、`title`、`summary` |
| `KnowledgeRelation` | 值对象 | `kind`（`requires` / `part_of`）、`source`、`target` |
| `LearningGoal` | 实体（关联聚合） | `id`、`learner_id`、`course_id`、`statement`、`outcomes`、`requirements`、`time_window`、`daily_effort`、`prerequisites`、`transitions` |
| `LearningProgress` | 实体（关联聚合） | `learner_id`、`course_id`、`points`；知识点级学习状态 |
| `Diagnosis` | 实体 | `id`、`learner_id`、`goal_id`、`verdicts`；一次诊断活动 |
| `LearnerId` / `CourseId` / `GoalId` / `KnowledgePointId` / `DiagnosisId` | 值对象 | 非空、无首尾空白；不同类型不相等 |
| `TimeWindow` / `DailyEffort` / `CapabilityRequirement` / `GoalTransition` | 值对象 | 目标的时间范围、每日投入、能力要求、状态迁移 |
| `PointProgress` / `PointTransition` | 值对象 | 某个知识点的学习进度与状态迁移 |
| `DiagnosticVerdict` / `DiagnosticExercise` / `DiagnosticSubmission` | 值对象 | 诊断判定、诊断题、作答 |
| `ExerciseRequest` / `ExerciseSchema` | 值对象 | 出题请求、模型输出结构 |
| `ExerciseGenerator` | 生成适配器 | 调用模型、校验、产出 `DiagnosticExercise` |

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

进度的查询：

| 方法 | 回答 |
| ---- | ---- |
| `state_of(point_id)` | 某知识点的学习状态（没记录过 = 未开始） |
| `points_in_state(state)` | 处于某状态的知识点 |
| `learned_points()` | 已学会的知识点 |
| `point(point_id)` | 按 id 取进度（没记录过 = `None`） |

知识点状态的迁移（每条边只有一个操作语义）：

| 方法 | 迁移 | 含义 |
| ---- | ---- | ---- |
| `start(at=, reason=)` | 未开始 → 学习中 | 开始学 |
| `assess(at=, reason=)` | 未开始 → 已学会 | **诊断判定已有基础** |
| `complete(at=, reason=)` | 学习中 → 已学会 | 学完并验证 |
| `review(at=, reason=)` | 已学会 → 学习中 | 复习 |

跨聚合的进度查询：

| 函数 | 回答 |
| ---- | ---- |
| `goal_completion(progress, goal)` | 目标要求几个点，学会几个——**结构性完成度**，不是「目标是否达成」 |
| `ready_points(progress, structure)` | 现在可以开始学哪些点 |
| `blocked_points(progress, structure)` | 哪些点还动不了（前置没学会） |
| `check_progress_against_course(progress, course)` | 跨聚合一致性检查：课程一致、知识点存在、**前置封闭性** |

**进度与目标必须配对**：`goal_completion` 要求两者属于**同一学习者**且**同一
门课**，否则抛 `DomainError`——别人的进度算不出这个目标的完成度（ADR-0019
§2）。

诊断的查询：

| 方法 | 回答 |
| ---- | ---- |
| `verdict_for(point_id)` | 某知识点的判定（会 / 不会 / 未判定） |
| `known_points()` / `gap_points()` / `undetermined_points()` | 各类判定的知识点 |
| `has_full_coverage(goal)` | **准入条件**：目标要求的每个知识点都有判定 |
| `missing_points(goal)` | 目标要求里未被诊断覆盖的知识点 |

跨聚合的诊断查询：

| 函数 | 回答 |
| ---- | ---- |
| `diagnosis_plan(diagnosis, goal)` | 准入结论：`is_sufficient`、`teachable`（要教的内容）、`undecided`（不知道的内容）、`coverage_ratio` |

**判定是三态**（`known` / `gap` / `unknown`）：「测了不会」与「根本没测」在计划
生成上完全不同——前者是要教的内容，后者是不知道的内容。混为一谈会让计划重复教
已经会的东西。

**「会」与「不会」必须带依据**（`basis`）与理由（`reason`）——没有依据的断言
不可审计。

### 题目生成

`ExerciseGenerator.generate(request)` 出题，两层校验：

| 层 | 校验什么 | 失败 |
| -- | -------- | ---- |
| **结构层**（`ExerciseSchema`） | 字段齐全、类型正确、长度合理（题面 ≥ 8 字符） | 带反馈重试 |
| **领域层**（`DiagnosticExercise` 构造） | 题目针对目标知识点、难度符合要求 | 带反馈重试 |

**两种失败要分开**：

| 失败 | 处理 | 理由 |
| ---- | ---- | ---- |
| 题目不合格 | 带反馈重试（`max_attempts`，默认 2） | 换一次输出可能就对了 |
| 调用失败（超时、限流） | 直接上抛 `ExerciseGenerationError`，**不重试** | 重试不会让超时变好 |

**不合格的题目不外流**：`generate()` 只有两种结局——返回合格题目，或抛错。没有
「返回一道题但质量存疑」的第三种可能。

**失败信息分「摘要」与「细节」两档**（ADR-0022）：喂给模型的是**摘要**（它要
知道的是「哪个字段、为什么」），排查问题时用**细节**（含厂商原始条目、输入值）。
**原始错误始终保留**——摘要是视图，不是替代品：

| 位置 | 内容 | 消费者 |
| ---- | ---- | ------ |
| `LLMParseError.field_errors` | 原始条目 + 归一化字段 | 程序逻辑 |
| `LLMParseError.summary` / `.detail` | 摘要 / 摘要 + 原始条目 | 模型 / 人 |
| `ExerciseGenerationError.message` / `.detail` | 同上 | 调用方 / 排查 |

`DiagnosticExercise` 的三个字段：

| 字段 | 为什么必须有 |
| ---- | ------------ |
| `expected_answer` | 没有可判定的标准答案，题目只是一段文字 |
| `difficulty` | 同一知识点可出不同难度的题；难度是**题目**的属性 |
| `rationale` | 让题目可被审阅：「为什么用这道题考这个点」有答案 |

**学习状态与任务状态是两套东西**：`LearningState`（`not_started` /
`in_progress` / `learned`）描述学习者对概念的认知；运行时的 `TaskStatus` 描述
一次执行的一个步骤。知识点没有「跳过」，也没有「受阻」状态——被阻塞是派生
结果（前置没学会），详见 [ADR-0018](../architecture/decisions/0018-learning-progress.md)。

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
    Diagnosis,
    DiagnosisId,
    DiagnosticSubmission,
    DiagnosticVerdict,
    GoalId,
    KnowledgePoint,
    KnowledgePointId,
    KnowledgeRelation,
    KnowledgeStructure,
    LearnerId,
    LearningGoal,
    LearningProgress,
    TimeWindow,
    blocked_points,
    check_goal_against_course,
    diagnosis_plan,
    goal_completion,
    ready_points,
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

# 进度：按（学习者 × 课程）归属，按知识点组织
progress = LearningProgress(learner_id=LearnerId("learner-1"), course_id=course.id)
progress = progress.assess(KnowledgePointId("k1"), at=time.time(), reason="诊断已掌握")
progress = progress.start(KnowledgePointId("k2"), at=time.time(), reason="开始学闭包")

progress.state_of(KnowledgePointId("k2"))  # 'in_progress'
ready_points(progress, structure)  # 接下来能学哪些
blocked_points(progress, structure)  # 哪些还动不了
goal_completion(progress, goal)  # 目标完成度（结构性）

# 诊断：判定学习者已经会哪些知识点
diagnosis = Diagnosis(
    id=DiagnosisId("diagnosis-1"),
    learner_id=LearnerId("learner-1"),
    goal_id=goal.id,
    verdicts=(
        DiagnosticVerdict(
            point_id=KnowledgePointId("k1"),
            verdict="known",
            reason="答对作用域与捕获的定义",
            basis=DiagnosticSubmission(exercise_id="e1", answer="…", correct=True),
            basis_kind="exercise",
        ),
    ),
)
diagnosis.has_full_coverage(goal)  # 准入：目标要求的每个点都有判定
diagnosis_plan(diagnosis, goal).teachable  # 要教的内容
```

完整可运行示例：

- `uv run python examples/learning_domain.py`——聚合边界与不变量
- `uv run python examples/knowledge_structure.py`——关系、路径、归因
- `uv run python examples/learning_goal.py`——目标状态机、约束、绑定与交接
- `uv run python examples/learning_progress.py`——进度归属、状态机、派生查询
- `uv run python examples/learning_diagnosis.py`——三态判定、准入条件、与进度的衔接
- `uv run python examples/exercise_generation.py`——两层拦截、带反馈重试、不外流
- `uv run python examples/learning_review.py`——四个聚合协同走一遍
- `uv run python examples/learning_plan.py`——领域目标 → 计划闭环

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
| `LearningProgress` | 同一知识点只有一条进度 | `同一个知识点只能有一条学习进度` |
| `PointProgress` | 迁移历史不断链 | `知识点 k1 的学习状态历史断链：期望来源状态为学习中，实际为未开始` |
| `PointProgress` | 迁移历史不含非法迁移 | `知识点 k1 的学习状态包含非法迁移：学习中 -> 未开始` |
| `PointTransition` | 理由非空 | `学习状态迁移理由不能为空` |
| 知识点状态 | 每条边只有一个操作语义 | `知识点 k1 的学习状态为未开始，不能变更为学习中：只有已学会的知识点才能复习` |
| `Diagnosis` | 同一知识点只有一条判定 | `同一个知识点只能有一条诊断判定` |
| `DiagnosticVerdict` | 「会」与「不会」必须有依据 | `知识点 k1 判为已掌握，但没有给出依据` |
| `DiagnosticVerdict` | 给了依据就要说明种类 | `知识点 k1 的判定给了依据，但没有说明依据种类` |
| `DiagnosticVerdict` | 理由非空 | `诊断判定理由不能为空` |
| `DiagnosticExercise` | 题面非空 | `诊断题面不能为空` |
| `DiagnosticExercise` | 难度合法 | `诊断题难度必须是 basic / intermediate / advanced 之一：'trivial'` |

## 4. 边界

```text
core.learning.domain      实体、值对象、知识结构（不认识模型 / 工具 / 运行时）
        ↑
core.learning.goal        目标状态机、结构化约束、能力要求
        ↑
core.learning.progress    学习进度：知识点级状态与派生查询
        ↑
core.learning.diagnosis   诊断：三态判定与准入条件
        ↑
core.learning.generation  出题适配器：调用模型、两层校验、带反馈重试
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
| `Learner` 不持有进度 | 画像是「学习者是谁」，进度是「学到了哪」；变化频率差几个数量级 |
| `LearningGoal` 独立聚合 | 它有自己的生命周期；多目标并行合法 |
| `LearningProgress` 独立聚合 | 进度按（学习者 × 课程）分片；同一事实不按目标重复存 |
| 进度绑知识点不绑任务 | 任务可跳过、可重建，知识点身份稳定；掌握与否不由步骤完成度回答 |
| 诊断是独立聚合 | 诊断是一次**活动**（可多次），进度是**当前状态**；两者不共享状态 |
| 判定与进度靠应用层衔接 | 跨聚合写会破坏「一次事务只改一个聚合」；`assess()` 已预留接口 |
| 领域层给「覆盖完整」而非「精度够」 | 覆盖是**集合问题**（可判定），精度是经验问题（需实证） |
| 无效题目在生成层拦截 | 坏题会污染判定；生成层是唯一能重试修好的地方 |
| 结构层与领域层不合并 | 结构层可被其他生成场景复用；领域层只属于学习领域 |
| 题目不合格重试，调用失败不重试 | 重试不会让超时变好，只会把一次限流放大成 N 次 |
| 重试把失败原因回喂给模型 | 模型要能看见自己错在哪，重试才有意义 |
| 不合格题目不外流 | 交出去之后下游就没有理由再怀疑它，判定会把它当依据 |
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
| 进度挂进 `Learner` | 画像与进度变化频率差几个数量级；且多目标会互相污染 |
| 进度按目标分片 | 同一门课的知识点状态会在多个目标间重复——同一事实存两份 |
| 进度绑计划任务 | 任务可跳过、可重建；任务完成不等于掌握概念 |
| 学习状态复用 `TaskStatus` | 任务状态描述「一个步骤」，学习状态描述「对概念的认知」；知识点没有「跳过」 |
| 知识点设「受阻」状态 | 被阻塞是结构事实（前置没学会），存成状态就要同步更新所有下游点 |
| 单列「已有知识」概念 | 它就是迁移序列的第一条；单列会让同一事实存两份 |
| 进度进 `GoalSpec` | `GoalSpec` 是领域无关契约；进度是领域侧的观测事实，不是规划输入 |
| 判定只用二态 | 「不会」要同时表示「测了不会」与「没测过」，计划会把没测过的点重复教 |
| 判定不带依据 | 没有依据的「会」不可审计；事后查不出为何跳过这个知识点 |
| 诊断自动同步进度 | 跨聚合写会破坏「一次事务只改一个聚合」；衔接是应用层的动作 |
| 准入条件交给应用层判断 | 「覆盖完整」是**集合问题**，可由领域层判定；「精度够不够」才需实证 |
| 诊断持有进度 | 同一事实两份副本；本项目已因此吃过三次亏 |

## 6. 未覆盖

- **持久化**：没有 `to_dict` / `from_dict`，也没有版本号；迁移历史接数据库后
  需要考虑独立成表。进度按知识点逐条更新，知识点多时重建成本会上升。
- **掌握程度的量化**：进度只有三档状态（未开始 / 学习中 / 已学会），没有「部分
  掌握」。区分它们属于掌握度模型（Topic 41）。「前置是否已掌握」「目标是否
  真的达成」这两条判断因此仍留在应用层。
- **复习调度**：进度记了 `learned_at`，但「什么时候该复习」需要间隔重复规则，
  留给 Topic 50。
- **题目持久化**：生成器产出题目，但**没有仓储层**——ADR-0019 已决定仓储层留待
  后续（不可变实体与迁移历史的存储形态尚未决策）。
- **题目质量的强判断**：生成层只做**弱检查**（题面是否出现知识点实词），挡得住
  完全跑题，挡不住「相关但角度偏了」。后者需要模型打分或人工审阅（Topic 44）。
- **诊断结果到知识点的映射**：题目与知识点的对应、映射缺失时如何降级，留给
  Topic 22。
- **掌握程度与薄弱点**：诊断给的是离散判定（「会 / 不会」）；掌握度量化留给
  Topic 41，薄弱点判定（相对目标要求还是绝对阈值）留给 Topic 43。
- **部分覆盖的折中**：领域层的准入条件是「覆盖完整」。目标要求很多知识点时，
  「部分覆盖 + 保守假设」是应用层策略，领域层目前没有给这个口子。
- **目标与知识点的跨课程身份**：知识点仍以课程为边界，全局身份留给 Topic 22。
- **计划粒度与知识点对齐**：`GoalSpec` 不带知识点，任务如何落到知识点上留给
  Topic 23。
- **复习调度与计划调整**：`suspended` / `expired` 两个状态目前没有产品侧强需求
  驱动，等 Topic 48 / 50 检验。

## 7. 验证

```bash
uv run --group dev pytest -q tests/test_learning_domain.py tests/test_knowledge_structure.py \
  tests/test_learning_goal.py tests/test_learning_progress.py tests/test_learning_workflow.py
uv run python examples/learning_domain.py    # 聚合边界与拒绝演示
uv run python examples/knowledge_structure.py # 路径、补课顺序、影响范围
uv run python examples/learning_goal.py      # 状态机、约束、绑定与交接
uv run python examples/learning_progress.py  # 进度归属、状态机、派生查询
uv run python examples/learning_review.py    # 四个聚合协同走一遍
uv run python examples/learning_plan.py      # 领域目标 → 计划闭环
```

`tests/test_learning_workflow.py` 是**集成测试**：它让四个聚合一起动，验证协作
（一致性检查真的被调用、完成度算对、终态行为正确、进度与目标必须配对），不重复
单聚合测试已覆盖的不变量（ADR-0019 §5）。
