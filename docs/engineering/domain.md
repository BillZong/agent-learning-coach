# 学习领域（Learning Domain）

> 状态：生效
> 对应代码：`src/agent_learning_coach/core/learning/`
> 决策依据：[ADR-0015](../architecture/decisions/0015-learning-domain-boundaries.md)、
> [ADR-0016](../architecture/decisions/0016-knowledge-representation.md)、
> [ADR-0017](../architecture/decisions/0017-learning-goal-modeling.md)、
> [ADR-0018](../architecture/decisions/0018-learning-progress.md)、
> [ADR-0019](../architecture/decisions/0019-domain-review.md)、
> [ADR-0020](../architecture/decisions/0020-diagnostic-assessment.md)、
> [ADR-0021](../architecture/decisions/0021-structured-generation.md)、
> [ADR-0023](../architecture/decisions/0023-diagnostic-assessment.md)、
> [ADR-0024](../architecture/decisions/0024-learning-planning.md)、
> [ADR-0025](../architecture/decisions/0025-plan-versioning.md)、
> [ADR-0026](../architecture/decisions/0026-persistence-baseline.md)、
> [ADR-0027](../architecture/decisions/0027-application-services.md)、
> [ADR-0028](../architecture/decisions/0028-document-ingestion.md)、
> [ADR-0029](../architecture/decisions/0029-chunking-strategy.md)

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
| `Diagnosis` | 实体 | `id`、`learner_id`、`goal_id`、`verdicts`、`evidence`；一次诊断活动 |
| `LearnerId` / `CourseId` / `GoalId` / `KnowledgePointId` / `DiagnosisId` | 值对象 | 非空、无首尾空白；不同类型不相等 |
| `TimeWindow` / `DailyEffort` / `CapabilityRequirement` / `GoalTransition` | 值对象 | 目标的时间范围、每日投入、能力要求、状态迁移 |
| `PointProgress` / `PointTransition` | 值对象 | 某个知识点的学习进度与状态迁移 |
| `DiagnosticExercise` / `DiagnosticSubmission` / `DiagnosticEvidence` / `DiagnosticVerdict` | 值对象 | 诊断题、作答、证据、判定 |
| `ExerciseRequest` / `ExerciseSchema` | 值对象 | 出题请求、模型输出结构 |
| `ExerciseGenerator` | 生成适配器 | 调用模型、校验、产出 `DiagnosticExercise` |
| `Grader` / `ExactMatchGrader` | 判官协议与实现 | 判定一次作答对错 |
| `AssessmentResult` / `DegradationReport` | 值对象 | 评估结果与降级报告 |
| `LearningUnit` | 值对象 | 一个学习单元——一个知识点 |
| `CourseDocument` | 实体 | 课程文档——材料在课程里的记录（含知识点关联） |
| `DocumentKind` | 值对象 | 材料种类：讲义 / 习题 / 代码 / 笔记本 / 参考资料 / 其它 |
| `LearningPlan` / `DailyLoad` / `PlanOverload` | 值对象 | 学习计划、每天负载、超载报告 |
| `EffortEstimator` / `UniformEffort` | 估时协议与实现 | 估算一个知识点要学多久 |
| `PlanVersion` | 实体 | 计划的一个版本：内容快照 + 依据 + 差异 + 理由 |
| `PlanBasis` / `PlanDelta` | 值对象 | 生成依据、与上一版的差异 |

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
| `assess_diagnosis(exercises, submissions, grader)` | 评估结果：判定、证据与降级报告 |
| `generate_plan(goal, structure, progress)` | 学习计划：单元、每天负载、超载报告 |
| `start_plan(goal, structure, progress, ...)` | 开一份**全新计划**（版本 1，新计划线） |
| `revise_plan(versions, goal, structure, progress, ...)` | 在同一份计划上产生下一个版本 |
| `plan_versions(versions, plan_id=..., goal_id=...)` | 查计划版本（按计划线分组、组内按版本号） |
| `current_plan_version(versions, plan_id=... / goal_id=...)` | 当前的计划版本 |

**判定与证据必须一致**：判定 k2 不能拿 k1 的证据当依据——构造期就拒绝
（ADR-0023）。

**汇总规则**：一个知识点的所有证据**全部答对**才算「会」；有一道答错就是「不会」；
没有证据或只有「判不了」的证据，是「未判定」。

**降级与错误是两件事**：

| 情况 | 处理 |
| ---- | ---- |
| 映射缺失（题目没声明考点） | **降级**：不判定、保留证据、报告 |
| 判不了（开放题无参考答案） | **降级**：判「未判定」、保留证据、报告 |
| 判定与依据不一致 | **错误**：构造期拒绝 |
| 作答引用不存在的题目 | **错误**：拒绝 |
| 题目标识重复 | **错误**：拒绝 |

前者是「信息不全」（正常结果），后者是「信息矛盾」（数据坏了）。

**计划范围** = （目标要求的知识点 ∪ 它们的前置闭包）− 已经学会的知识点。

**计划顺序** = 知识结构的拓扑序限制到计划范围内——依赖是硬约束，前置一定排在
后继之前。

**排不下要报告，不能少排**：超载时计划仍包含全部单元，并给出 `PlanOverload`。

**目标的前置（`goal.prerequisites`）不进计划范围**，只被报告——它是「开始这个
目标前必须已经会」的前提条件，不是学习内容。

**单元与知识点一一对应**：完成一个单元就能直接回写一个知识点的进度。

**计划版本**：`PlanId` 区分「同一计划的修订」（同一计划线，版本号 +1）与「全
新计划」（新计划线，版本号从 1 开始）。**同一计划线内按版本号排序，不按时间**
——版本号是领域分配的序号，时间由调用方传入，可能补录或乱序。

**每一版都带依据、差异与理由**：依据记的是**输入**（目标、生成时已学会的知识点、
参考的诊断、课程结构指纹），差异记的是**排程变化**（新增、移除、重排），理由必
填。

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
    assess_diagnosis,
    blocked_points,
    check_goal_against_course,
    current_plan_version,
    diagnosis_plan,
    generate_plan,
    goal_completion,
    plan_versions,
    ready_points,
    revise_plan,
    start_plan,
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
            evidence=(
                DiagnosticEvidence(
                    exercise=DiagnosticExercise(
                        id="e1",
                        prompt="闭包是什么？",
                        point_id=KnowledgePointId("k1"),
                    ),
                    submission=DiagnosticSubmission(exercise_id="e1", answer="…"),
                    grading="correct",
                    point_id=KnowledgePointId("k1"),
                ),
            ),
        ),
    ),
)
diagnosis.has_full_coverage(goal)  # 准入：目标要求的每个点都有判定
diagnosis_plan(diagnosis, goal).teachable  # 要教的内容

# 评估：作答 → 判定 + 证据 + 降级报告
result = assess_diagnosis(exercises, submissions, ExactMatchGrader())
result.verdict_for(KnowledgePointId("k1"))  # 会 / 不会 / 未判定
result.degradation.unmapped_evidence  # 映射缺失的证据（保留但不判定）

# 计划：目标 + 知识结构 + 进度 → 学习计划
plan = generate_plan(goal, structure, progress)
plan.units  # 学习单元，按依赖顺序
plan.days  # 每天的负载
plan.overload  # 时间超载报告（装得下时为空）

# 版本：全新计划 → 修订 → 查询
version = start_plan(goal, structure, progress, plan_id=PlanId("plan-1"),
                     version_id=PlanVersionId("plan-1-v1"), reason="初次规划", at=now)
next_version = revise_plan([version], goal, structure, progress,
                           version_id=PlanVersionId("plan-1-v2"),
                           reason="学习者掌握了 k1", at=now + 1)
plan_versions([version, next_version], goal_id=goal.id)  # 按计划线分组、组内按版本号
current_plan_version([version, next_version], goal_id=goal.id)  # 当前版本
```

完整可运行示例：

- `uv run python examples/learning_domain.py`——聚合边界与不变量
- `uv run python examples/knowledge_structure.py`——关系、路径、归因
- `uv run python examples/learning_goal.py`——目标状态机、约束、绑定与交接
- `uv run python examples/learning_progress.py`——进度归属、状态机、派生查询
- `uv run python examples/learning_diagnosis.py`——三态判定、准入条件、与进度的衔接
- `uv run python examples/learning_assessment.py`——映射、汇总、降级、判官
- `uv run python examples/learning_planning.py`——范围、依赖顺序、时间约束、超载
- `uv run python examples/learning_plan_versioning.py`——修订 vs 全新计划、依据、
  查询与当前版本
- `uv run python examples/learning_persistence.py`——形状、往返、乐观锁、不变量，
  以及进度「整体重建 vs 增量更新」的成本实测
- `uv run python examples/application_workflow.py`——编排、矛盾、取舍与幂等
- `uv run python examples/document_ingestion.py`——格式、部分成功、幂等、关联与归属
- `uv run python examples/chunking_strategy.py`——按类型选策略、元数据随切片走、
  锁不变量而非数字
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
| `DiagnosticSubmission` | 答案非空 | `作答内容不能为空` |
| `DiagnosticVerdict` | 判定与证据一致 | `知识点 k2 的判定引用了指向 k1 的依据，判定与依据不一致` |
| `assess_diagnosis` | 作答引用存在的题目 | `作答引用了不存在的诊断题：e9` |
| `assess_diagnosis` | 题目标识唯一 | `诊断题标识重复：e1` |
| `LearningUnit` | 标题与理由非空 | `学习单元在计划里的理由不能为空` |
| `LearningUnit` | 估时为正整数 | `学习单元的预计时长必须是正整数分钟：0` |
| `LearningPlan` | 一个知识点一个单元 | `学习计划里知识点 k1 出现了多个单元` |
| `LearningPlan` | 依赖在计划内 | `学习单元 k3 依赖的知识点 k9 不在计划内` |
| `LearningPlan` | 后继不排在前置之前 | `学习单元 k3 排在了它的前置 k2 之前` |
| `PlanVersion` | 版本号为正整数 | `计划版本号必须是从 1 开始的正整数：0` |
| `PlanVersion` | 理由非空 | `计划版本的改动理由不能为空` |
| `PlanVersion` | 首版不带差异 | `首个计划版本没有上一版，因此不能带差异` |
| `revise_plan` | 版本属于同一条计划线 | `修订需要同一条计划线的版本，收到 2 条` |
| `revise_plan` | 版本号不重复 | `同一计划线内的版本号不能重复` |

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
| 映射由题目声明，不由评估推断 | 推断出的映射无法审计；「为什么判定这个点会」要能回答 |
| 映射缺失可表达（`point_id` 可空） | 外部题库、历史题、跨课程题确实不知道考点；靠约定藏起来会让降级无处落地 |
| 判定交给判官协议 | 精确匹配是一种实现，LLM 判官是另一种（Topic 41）；换判官不改领域模型 |
| 作答只承载事实 | 「对错」是判断，可被随手填成 True；它属于判官与证据 |
| 汇总从严（全对才算会） | 诊断的目的是找出要补的地方；宽松汇总会跳过真正没学会的知识点，而知识点有先修依赖 |
| 「判不了」是一等状态 | 算作答错会凭空制造知识缺口，算作答对会掩盖缺口 |
| 降级不抛异常 | 映射缺失是诊断的**正常结果**，不是失败；矛盾才是失败 |
| 单元与知识点一一对齐 | 进度、判定、依赖都以知识点为单位；粒度不对齐，进度就写不回去 |
| 范围带上前置闭包 | 不带闭包的计划**执行不了**——目标要求 k3，而 k3 依赖 k1、k2 |
| 目标的前置只报告 | 它是「开始这个目标前必须已经会」的前提，不是学习内容 |
| 范围由目标与进度决定 | 诊断是判断不是事实；两份事实之间的选边是应用层的事（ADR-0020） |
| 依赖顺序是硬约束 | 知识点有先修依赖，跳过一处会让后面全部塌掉 |
| 排不下要报告 | 悄悄丢掉内容会让「要补的地方」凭空消失——同 ADR-0023 的宽松汇总 |
| 没有每日投入就不排期 | 硬排「第 0 天装下全部内容」是假精度 |
| 估时可替换 | 领域层定义「需要估什么」，不规定「怎么估」（同 `Grader`） |
| 领域模型带 `to_dict` / `from_dict` | 映射放在外面，领域模型才干净——同 `bridge.py` |
| 负载不带版本号 | 领域形状已经变过两次；不认识的版本必须**明确拒绝**，不能猜 |
| 存派生状态（目标状态、知识点状态） | 它们由迁移历史派生；存两份就要同步两处，而两处会漂移 |
| 写入时校验就够 | 只在写时校验，数据库仍能绕过——**读时再校验一次**，非法数据出不来领域层 |
| 进度逐知识点存行 | 领域层是整块替换；存储粒度更细就要引入 diff 机制，那是第二份真相 |
| 每张表各写一套乐观锁 | 两个实现对同一次并发必须给出同样答案——规则只写一处 |
| 诊断与进度不一致时「信诊断」 | 领域模型没有「已学会 → 未开始」这条边——**信诊断不是可选项** |
| 矛盾时停下来报错 | 进度是事实、诊断是一次测量；带着矛盾继续规划并报告，比停下来合理 |
| 领域层自动同步诊断到进度 | 跨聚合写（ADR-0015）；ADR-0020 已把衔接定为应用层的动作 |
| 多份诊断按标识排序 | 标识的字典序不是时间——诊断是活动，活动要有时间（`at`） |
| 计划冲突也重试 | 重试要基于**新的基线**重算，得先重读当前计划——那是调用方的决策 |
| 计划每次都出新版本 | 空版本不回答「变了没有」，只在历史里堆噪音，还让人怀疑是不是出了 bug |
| 现在就加「来源版本」字段 | `revise_plan` 的基线恒等于 number-1——冗余；等回滚 / 并发合并出现时再加 |
| 课程文档放进 `Course` 聚合 | 导入一份材料就要重写整门课（ADR-0015 的「一次事务一个聚合」） |
| 课程文档与 RAG 文档合成一个类型 | 两边形状与生命周期不同（切片 vs 文档），合成会让两边都别扭 |
| 导入失败就整批回滚 | 一份坏文件毁掉整个课程库；但**前置条件错误**（课程不存在）除外 |
| 静默跳过失败的文件 | 静默的部分成功比整批失败更糟——它让人以为材料齐了 |
| 重新导入时覆盖知识点关联 | 「重导一遍更新教材」会静默清空教师之前标的关联 |
| 自动关联知识点 | 判断「这份材料讲哪个点」要模型打分（Topic 46）；本主题只提供显式关联 |
| 正文也套用「不允许首尾空白」 | 那条规则是给标识符与短标签的；文件末尾通常有换行，正文要按原样保存 |
| 所有材料用同一种切分策略 | 定长切代码会拦腰截断；递归切代码也不对——段落不是代码的语义单元 |
| 为每种语言写一个代码切分器 | 结构由正则给出，换语言改正则即可；识别不出来退回兜底 |
| 测试锁住具体参数 | 数字会随模型与语料变化；锁数字只会让改动变成「改测试」 |
| 用 `PlanId` 区分修订与全新计划 | 少了它，「重新规划」既像新版本又像新计划——无法判定的区分等于没有区分 |
| 目标不充当计划线 | 同一目标可以先后有两条计划线（换了一条学习路径） |
| 排序按版本号，不按时间 | 时间由调用方传入，可能补录、乱序；让它决定顺序，「谁是当前版本」就变成了猜 |
| 跨计划线用时间比 | 两条线之间没有共同序号，只能比「哪条更近」 |
| 依据记输入，不记输出 | 输出已在版本里；依据的价值是「当时已经会了什么」 |
| 差异只比排程，不比措辞 | 标题与理由是解释不是排程；比了它们，「这一版变了吗」永远答不上来 |
| 首版没有差异 | 没有上一版，就不存在差异 |
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

## 4.1 持久化

学习领域层**不碰存储**：领域模型保持纯函数与不可变对象，不认识数据库、文件或网络
（ADR-0015）。存储相关的三样东西在 `core/storage/`：

| 层 | 位置 | 内容 |
| -- | ---- | ---- |
| 形状 | `core/storage/codecs.py` | 领域对象 ↔ JSON 兼容的负载字典 |
| 协议 | `core/storage/protocols.py` | 仓储端口 + `Stored` 记录 + 乐观锁规则 |
| 实现 | `core/storage/memory.py` | 进程内实现（测试也用它） |

```python
from agent_learning_coach.core.storage import Stored, goal_to_dict, goal_from_dict

payload = goal_to_dict(goal)          # 带 schema_version 的 JSON 兼容负载
same = goal_from_dict(payload)         # 解码会重跑领域层的不变量
saved = await repository.save(Stored(value=goal, revision=0))   # revision 0 = 新建
```

三条规则：

1. **负载带 `schema_version`**——领域形状已经变过两次；不认识的版本**明确拒绝**，
   不半懂半猜。
2. **只存历史，不存派生状态**——目标状态与知识点学习状态都由迁移历史派生。
3. **乐观锁**——每条记录带 `revision`；写入时携带的版本必须等于库里的当前版本，
   否则抛 `ConflictError`。规则只写在 `ensure_writable` 一处，内存实现与未来的
   SQL 适配器共用。**冲突是事实**：重新读、重新合并、再写，是应用层的事。

PostgreSQL 的 schema 在 `migrations/0001_learning_domain.sql`（一行一聚合：身份与
外键用列，内层形状用 JSONB，CHECK 约束把两者绑死）。**SQL 适配器尚未实现**——驱动
选型未定。详见 [ADR-0026](../architecture/decisions/0026-persistence-baseline.md)。

## 4.2 应用层（用例编排）

**领域层回答「什么是真的」，应用层回答「现在做什么」。** Phase 1 的领域层全是纯函
数，不读、不写、不串联——顺序、冲突、落库时机都留给应用层（ADR-0027）。

```python
from agent_learning_coach.core.application import LearningWorkflow

workflow = LearningWorkflow(
    goals=goals, progresses=progresses, diagnoses=diagnoses, plans=plans, courses=courses,
)
result = await workflow.diagnose_and_plan(goal_id, at=now, reason="初次诊断后规划")
result.sync.applied      # 同步进进度的点
result.sync.conflicts    # 诊断与进度的矛盾
result.plan_version      # 落库后的计划版本
result.plan_written      # 内容没变时为 False——不产生空版本
```

三条规则：

1. **先写进度，后写计划**——计划是从进度算出来的，反过来写会写出一份「依据是假的」
   计划；这个顺序还让重试是幂等的。
2. **矛盾按领域模型允许的迁移处置**：诊断说不会、进度说已学会 → **保留进度并报告**
   ——领域模型没有「已学会 → 未开始」这条边（ADR-0018），所以「信诊断」根本不是
   一个可选项。
3. **写边界是「两次独立的聚合写」**，不是跨聚合事务；中间失败会留下半更新状态，
   重跑是安全的。
4. **幂等**：进度内容没变就不写；计划内容没变就**不产生新版本**——空版本是噪音，
   它不回答「计划变了没有」（`delta` 已经回答了）。

多份诊断取**最新**的一份（按诊断的 `at`）；给了 `diagnosis_id` 就用它。

## 4.3 课程文档摄取

**课程文档**是材料在课程里的记录：内容、来源、种类、属于哪门课、覆盖哪些知识点。
它是**独立实体**（不是 ``Course`` 聚合的一部分）——导入一份材料不该重写整门课。

```python
from agent_learning_coach.core.application import DocumentRequest, ingest_course_documents

report = await ingest_course_documents(
    [DocumentRequest(source="notes.md", point_ids=(KnowledgePointId("k1"),))],
    course,
    repository=documents,
)
report.imported    # 新导入的
report.updated     # 内容变了被覆盖的
report.unchanged   # 内容没变没重写的
report.failed      # 没进来的与原因
report.issues      # 一致性问题（关联了不存在的知识点）
```

四条规则：

1. **部分成功，失败必须进报告**——一份坏文件不该毁掉整个课程库；但**静默的部分
   成功比整批失败更糟**，所以失败清单在报告里。
2. **幂等**——文档 id 由来源推导，重复导入是覆盖；内容没变就不写。
3. **知识点关联不会被顺手抹掉**——重新导入只更新材料本身；关联的语义是「不改 /
   清空 / 设置」三种。
4. **材料属于一门课**——同一份材料导入到另一门课会被拒绝并报告。

支持的格式：纯文本、Markdown、代码、PDF（PyMuPDF）、Jupyter 笔记本。Word 不做
（依赖在 notebook 组，且还没验证有需求）。详见
[ADR-0028](../architecture/decisions/0028-document-ingestion.md)。

## 4.4 切分策略

**每类材料有它自己的语义单元**，一刀切的策略总有一半材料是错的。

| 材料类型 | 切分器 | 语义单元 |
|---|---|---|
| 讲义 | `RecursiveChunker` | 段落 / 句子 |
| 习题 | `ExerciseChunker` | 一道题（含答案说明） |
| 代码 | `CodeChunker` | 一个顶层定义 |
| 笔记本 | `RecursiveChunker` | 单元 |
| 参考资料 | `FixedSizeChunker` | 固定长度 |

```python
from agent_learning_coach.core.rag import default_chunking_policy

chunks = default_chunking_policy().split(to_rag_document(course_document))
```

**课程元数据跟着切片走**：切片带上 `kind` / `course_id` / `point_ids`，检索时才能
按课程与知识点过滤。**材料只解析一次**，课程侧与 RAG 侧共用。

**测试锁不变量，不锁数字**（ADR-0029）：代码不切断顶层定义、习题不切断一道题、
切片不超上限、切片 id 可复现、切分是幂等——数字会变，不变量不会。

## 5. 设计取舍

| 替代方案 | 为什么不选 |
| ---- | ---- |
| 状态作为字段存储 | 会与迁移历史各说一套；派生让不一致无法表达 |
| 自由文本约束（`kind` + `text`） | 无法校验，也无法参与计算；只能给模型看 |
| 一个「时间」约束同时含范围与投入 | 两者独立变化：范围决定还剩多久，投入决定每天能学多少 |
| 目标级「难度」字段 | 难度是知识点的属性（课程侧）；复制到目标会让同一知识点在不同目标下难度不同 |
| 能力要求另立学习结果 | 同一件事两份说法，映射给规划器会变成重复任务 |
| 绑定知识点进 `GoalSpec` | `GoalSpec` 是领域无关契约；单元与知识点的对齐由领域侧按单元映射完成（ADR-0024） |
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
| 计划单元覆盖多个知识点 | 回写进度时无法确定「这个单元算掌握了哪个点」——粒度不对齐，进度就写不回去 |
| 计划只含目标要求的点 | 目标要求 k3 而 k3 依赖 k1、k2 时，不带闭包的计划执行不了 |
| 目标的前置也放进计划 | 它是「开始这个目标前必须已经会」的前提，不是学习内容；补前置属于另一个目标 |
| 计划范围由诊断决定 | 诊断是判断不是事实；两份事实之间选边是应用层的事（ADR-0020） |
| 排不下就少排几个 | 丢掉单元会让「要补的地方」凭空消失——同 ADR-0023 反对的宽松汇总 |
| 没有每日投入也硬排一天 | 假精度：看起来有日程，其实没有信息量 |
| 估时写死在领域层 | 领域层定义「需要估什么」，不规定「怎么估」——同 `Grader`、`LLMClient` |

## 6. 未覆盖

- **负载的版本迁移**：负载带 `schema_version`，解码拒绝不认识的版本——这是刻意
  的（明确失败优于半懂半猜），但**迁移工具还没做**。领域形状改过两次，两次都靠
  「还没有真实数据」侥幸过去；有了真实数据这条路要补（ADR-0026）。
- **掌握程度的量化**：进度只有三档状态（未开始 / 学习中 / 已学会），没有「部分
  掌握」。区分它们属于掌握度模型（Topic 43）。「前置是否已掌握」「目标是否
  真的达成」这两条判断因此仍留在应用层。
- **复习调度**：进度记了 `learned_at`，但「什么时候该复习」需要间隔重复规则，
  留给 Topic 52。
- **题目与仓储的连接**：题目有 codec、有表、有仓储端口，但**没有 SQL 适配器**
  （驱动选型未定）；且题目上的 `point_id` 建不了外键——知识点身份是课程内的
  （ADR-0016），而题目不记属于哪门课（ADR-0026）。
- **题目质量的强判断**：生成层只做**弱检查**（题面是否出现知识点实词），挡得住
  完全跑题，挡不住「相关但角度偏了」。后者需要模型打分或人工审阅（Topic 46）。
- **能力估计（连续掌握度）**：诊断给的是离散判定；掌握度量化留给 Topic 43。
- **LLM 判官**：Phase 1 的 `ExactMatchGrader` 只适用于有唯一答案的题目；开放题只能
  判「判不了」。LLM 判官留给 Topic 41。
- **证据的多种类型**：自述与外部证据尚无采集流程，`EvidenceKind` 目前只有 `exercise`
  真正在用；证据类型与置信权重留给 Topic 42。
- **诊断阈值**：一个知识点出几道题才算判准，尚无实证依据（Topic 46；台账
  `open-issues.md` §2.2）。
- **掌握程度与薄弱点**：诊断给的是离散判定（「会 / 不会」）；掌握度量化留给
  Topic 43，薄弱点判定（相对目标要求还是绝对阈值）留给 Topic 45。
- **部分覆盖的折中**：领域层的准入条件是「覆盖完整」。目标要求很多知识点时，
  「部分覆盖 + 保守假设」是应用层策略，领域层目前没有给这个口子。
- **目标与知识点的跨课程身份**：知识点仍以课程为边界，全局身份待定（ADR-0016 §7；
  台账 `open-issues.md` §4）。
- **计划的内容**：计划单元只有标题、理由、估时——「这个单元具体怎么学」要由
  应用层或模型补上（Phase 2 的 AI Tutor）。领域层给结构，内容由生成层给。
- **估时的实证依据**：默认 30 分钟/知识点没有数据支撑，会让排期看起来精确而
  实际不准。真实估时应来自学习数据（Topic 46；台账 `open-issues.md` §2.2）。
- **从 URL 导入**：只支持文件路径；抓网页没做。
- **文档的历史版本**：重复导入是覆盖，材料的历史不保留（计划版本有历史，材料没有）。
- **证据没有时间戳**：「学习者什么时候答的」答不上来；「哪份诊断更新」只能靠诊断
  自己的 `at`。
- **多诊断并发时的合并**：冲突重试是「以最新进度为基准重新同步」，不是「合并两个
  进度」；两次同步基于不同诊断时，先到的那次会被覆盖。
- **SQL 适配器与迁移工具**：持久化基线（ADR-0026）已给出形状、协议、内存实现与
  PostgreSQL schema，但**适配器没写**（驱动选型未定）；`migrations/` 谁按顺序执行、
  旧负载怎么迁移，都还没有工具。
- **HTTP API**：API 框架尚未选型，因此本阶段只给领域侧的纯函数。
- **复习安排**：计划只排「还没学会的知识点」，到期该复习的点不在计划里——复习
  调度留给 Topic 52。
- **复习调度与计划调整**：`suspended` / `expired` 两个状态目前没有产品侧强需求
  驱动，等 Topic 50 / 50 检验。

## 7. 验证

```bash
uv run --group dev pytest -q tests/test_learning_domain.py tests/test_knowledge_structure.py \
  tests/test_learning_goal.py tests/test_learning_progress.py tests/test_learning_diagnosis.py \
  tests/test_learning_assessment.py tests/test_learning_planning.py \
  tests/test_learning_plan_versioning.py tests/test_learning_persistence.py \
  tests/test_application_workflow.py tests/test_document_ingestion.py \
  tests/test_learning_workflow.py
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
