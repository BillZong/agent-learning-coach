# Phase 1 阶段验收（Learning Domain & Planning）

> 日期：2026-11-25 ｜ 阶段：Phase 1（Week 3–4） ｜ 结论：**领域层通过，持久化与应用层编排未完成**

本文把路线图的验收标准逐条映射到**可执行的证据**。判据只有一条：**能在仓库里
跑出来的，才算证据**；文档描述只作补充。

Phase 1 覆盖 MVP（[`mvp.md`](./mvp.md)）的 M2 部分——学习领域与学习规划；M1 部分
在 [Phase 0 验收](phase-0-acceptance.md) 中验收。

## 1. 验收标准

### 1.1 路线图的阶段验收标准

| # | 标准 | 证据（可执行） | 结论 |
| ---- | ---- | ---- | ------ |
| 1 | 能够创建和管理学习领域 | `core/learning/`（学习者、课程、知识点、目标、进度、诊断、计划、版本）；`tests/test_learning_*.py`（174 个用例） | **部分**——模型齐了，「管理」只在内存里成立（缺持久化与应用层编排） |
| 2 | 能够组织知识点及其依赖关系 | `KnowledgeStructure`（`requires` / `part_of`、`order()` / `prerequisite_closure()`）；`tests/test_learning_domain.py`；ADR-0016 | 通过 |
| 3 | 能够创建学习目标 | `LearningGoal` 状态机 + 结构化约束 + 知识点绑定；`tests/test_learning_goal.py`；ADR-0017 | 通过 |
| 4 | 能够根据学习者背景开展初始诊断 | 诊断模型与评估齐备（ADR-0020/0021/0023）；`tests/test_learning_diagnosis.py`、`tests/test_learning_assessment.py`；`examples/learning_assessment.py` | **部分**——诊断机制（出题 → 作答 → 评估 → 判定 → 准入）完整；「根据背景决定诊断什么」的选题策略与应用层编排仍缺 |
| 5 | 能够识别初步知识缺口 | 诊断判定给出「会 / 不会 / 未判定」（ADR-0020）；计划据此排布 | **部分**——缺口来自诊断判定；相对目标要求的薄弱点判定属 Topic 45 |
| 6 | 能够生成结构化、可执行的学习计划 | `generate_plan`（粒度对齐知识点 + 依赖顺序 + 时间约束）；`tests/test_learning_planning.py`；`examples/learning_planning.py`；ADR-0024 | 通过 |
| 7 | 学习计划能够持久化并被后续模块使用 | 计划随会话快照持久化（ADR-0009）；计划可映射成运行时契约（`to_unit_goal_spec`） | 通过（会话级）——领域数据尚未入库（ADR-0019 留待后续） |

### 1.2 Plan.md 的阶段验收条目

| # | 条目 | 证据（可执行） | 结论 |
| ---- | ---- | ---- | ---- |
| 1 | 用户能够创建 Learning Goal | `LearningGoal` + 状态机；`tests/test_learning_goal.py` | 通过 |
| 2 | 用户能够选择 Course | `Course` 聚合 + `check_goal_against_course`；`tests/test_learning_workflow.py` | 通过 |
| 3 | 系统能够生成 Knowledge Map | `KnowledgeStructure` + 闭包查询；`examples/knowledge_structure.py` | 通过 |
| 4 | 系统能够完成初始诊断 | `ExerciseGenerator` + `assess_diagnosis` + 准入判定；`examples/learning_assessment.py` | 通过（领域机制）；编排与持久化待补 |
| 5 | 系统能够生成 Learning Plan | `generate_plan`；`examples/learning_planning.py` | 通过 |
| 6 | 用户能够查询计划和计划版本 | `start_plan` / `revise_plan` / `plan_versions` / `current_plan_version`；`tests/test_learning_plan_versioning.py`；`examples/learning_plan_versioning.py`；ADR-0025 | 通过 |

## 2. 交付物

| 交付物 | 位置 | 状态 |
| ---- | ---- | ---- |
| 学习领域模型（学习者 / 课程 / 知识点 / 目标 / 进度） | `core/learning/domain.py`、`goal.py`、`progress.py` | 完成（ADR-0015–0018） |
| 知识结构（依赖、闭包、学习顺序、主题层级） | `core/learning/domain.py` | 完成（ADR-0016） |
| 跨聚合一致性检查 | `core/learning/consistency.py` | 完成（ADR-0017） |
| 诊断模型（判定、准入、证据） | `core/learning/diagnosis.py` | 完成（ADR-0020、ADR-0023） |
| 诊断题生成（两层校验 + 重试 + 不合格不外流） | `core/learning/generation.py` | 完成（ADR-0021） |
| 学习计划生成（范围、依赖顺序、时间约束、超载） | `core/learning/planning.py` | 完成（ADR-0024） |
| 计划版本（修订 vs 全新计划、依据、差异、查询） | `core/learning/versions.py` | 完成（ADR-0025） |
| 领域模型到运行时的桥接 | `core/learning/bridge.py` | 完成（ADR-0013 §10、ADR-0015） |
| 学习领域接入说明 | `docs/engineering/domain.md` | 完成 |
| 阶段量化目标 | 本文件 | 完成 |

## 3. 工程质量指标

| 指标 | 数值 | 说明 |
| ---- | ---- | ---- |
| 测试 | **800 passed** | `uv run pytest`；其中学习领域 **174** 个 |
| 静态检查 | ruff + mypy 全绿 | `make check`；mypy 覆盖 68 个源文件 |
| 学习主题 | 10 篇 | `docs/learning/topics/`（Day 15–24） |
| ADR | 25 篇 | `docs/architecture/decisions/`（Phase 1 新增 0015–0025） |
| 可运行示例 | 21 个 | `examples/`，全部无凭证可跑 |
| 学习领域代码规模 | 4464 行 | `core/learning/` 全部模块 |

## 4. 未覆盖的边界（不假装完成）

| 边界 | 现状 | 影响 |
| ---- | ---- | ---- |
| **持久化** | 没有仓储层、没有 `to_dict` / `from_dict`；学习目标、进度、诊断、计划版本都只在内存里 | 「管理学习领域」只在进程内成立；重启即丢（ADR-0019 已决定仓储层后置；台账 P1-A 建议新增 Day 27） |
| **应用层编排** | 「诊断流程」「计划生成流程」都只有领域侧纯函数，没有编排它们的应用服务 | 阶段验收 #1、#4 的缺口都在这里（台账 P1-B 建议新增 Day 28） |
| **HTTP API** | API 框架仍是 TBD，架构里的 API 层尚未实现 | 用户无法通过接口触达 Phase 1 的能力 |
| **薄弱点判定** | 诊断给出的是逐知识点的判定；「相对目标要求」的薄弱点排序未建模 | 阶段验收 #5 未完全达标（Topic 45） |
| **LLM 判官** | 只有精确匹配判官；开放题只能判「判不了」 | 诊断覆盖率受限于客观题（Topic 41） |
| **掌握度量化** | 进度只有三档状态，没有连续掌握度 | 计划无法按「掌握得多好」调整（Topic 43） |
| **估时的实证依据** | 默认 30 分钟/知识点，没有数据支撑 | 排期看起来精确而实际不准；用户可能拿它做承诺（Topic 46） |
| **诊断与进度同步** | 诊断到进度的衔接是应用层的动作，领域层不选边 | 两份事实不一致时，计划范围可能与诊断结论不同（台账 P1-B） |
| **知识结构的维护流程** | `requires` 边由谁维护、怎么校验质量，没有定义 | 依赖质量直接决定计划范围与薄弱点判定（台账 P1-C，建议并入 Day 47） |

## 5. Week 4 回顾（Day 22–24）与阶段补账

Phase 1 结束后，遗留问题台账（[`open-issues.md`](./open-issues.md)）判定：**「持久
化」与「应用层编排」两条缺口在后续阶段（Day 27–66）没有任何主题承接**。因此新增
**Phase 1.5 · Foundation Remediation**（Day 25「Persistence Baseline」、Day 26
「Application Services」），原 Day 25–64 顺延为 Day 27–66。详见台账 §2.1 与 §5。

| 主题 | 交付 | 一句话结论 |
| ---- | ---- | ------ |
| Day 22 · Knowledge Mapping | 诊断评估（ADR-0023） | **信息不全与信息矛盾必须分开**：映射缺失走降级（保留、报告），矛盾走错误 |
| Day 23 · Learning Planning | 学习计划生成（ADR-0024） | **粒度不是审美问题，是回写问题**：单元不对齐知识点，进度就写不回去 |
| Day 24 · Plan Versioning | 计划版本化（ADR-0025） | **无法判定的区分等于没有区分**：修订与全新计划必须能被数据分开 |

三天三条结论指向同一件事：**先把不同的东西分开**——降级与错误、目标与前置、修订
与全新计划。每一处分开的代价都是多一个类型、多一个标识，但收益是「为什么变成
现在这样」这类问题终于有确定答案。

## 6. 进入 Phase 2 的输入

1. **领域模型需要被应用层用起来**：Phase 2 的 AI Tutor 会第一次真正编排这些能
   力（诊断 → 教学 → 练习 → 评估）。在编排之前，仓储层与 API 框架必须选型——
   否则 Phase 2 只能在内存里跑。
2. **教学能力需要模型**：Phase 1 的计划只有结构（单元、顺序、时长），没有内容。
   「这个单元具体怎么教」要由生成层给出（同 ADR-0021 的题目生成）。
3. **语义质量评估需要锚点**：Phase 2 之前要先有真实教学场景与人工标注，否则判官
   没有可校准的标准（承自 Phase 0 的结论）。
4. **诊断覆盖率需要 LLM 判官**：只有精确匹配时，开放题只能判「判不了」，诊断的
   实际覆盖会很低（Topic 41）。

## 相关文档

- [遗留问题台账](open-issues.md)——两个阶段的未决问题、覆盖情况与建议插入点
- [产品范围](product-scope.md)、[路线图](roadmap.md)、[MVP 定义](mvp.md)
- [Phase 0 验收](phase-0-acceptance.md)
- [学习领域接入说明](../engineering/domain.md)
- [系统架构](../architecture/architecture.md)
