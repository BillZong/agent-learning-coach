# agent-learning-coach

| 属性 | 内容 |
|---|---|
| Product Name | AI Learning Coach |
| Project Name | agent-learning-coach |
| Document Type | Product Requirements Document |
| Version | V1.0 |
| Status | Draft |
| Target Users | 成年技术学习者 |
| Initial Learning Domain | Agent Engineering |
| Primary Language | English technical materials + Chinese learning interaction |


## 1. Product Overview

### 1.1 Product Background

随着 AI 技术的发展，技术人员需要持续学习新的技术领域。

但传统学习方式存在几个明显问题：

1. 学习目标不清晰：学习者通常知道自己想学什么，但难以确定需要掌握哪些知识，以及知识之间的依赖关系。
2. 学习路径缺乏个性化：固定课程无法充分考虑学习者已有知识、实际能力和学习进度。
3. 学习过程缺乏持续反馈：阅读、观看课程和完成练习，并不意味着真正掌握知识。
4. 学习成果难以量化：学习者很难准确判断自己掌握了什么、还欠缺什么。
5. 学习计划缺乏动态调整：学习过程中遇到困难或提前掌握某些知识时，原有计划往往无法及时调整。
6. 学习行为与实际能力脱节：学习者可能完成大量学习任务，却无法将知识应用到真实工程场景。

传统 AI Chatbot 可以回答问题、解释概念和生成学习资料，但通常缺少对长期学习过程的持续管理。

### 1.2 Product Vision

构建一个 AI Learning Coach，帮助成年学习者完成从学习目标确定到知识掌握的完整过程。

它不仅回答学习问题，还需要持续理解学习者的知识状态、学习进度和能力变化，并根据真实学习表现动态调整学习路径。

### 1.3 Product Positioning

**AI Learning Coach is an adaptive learning companion that manages the entire learning journey, not just answers questions.**

产品核心流程：

`Diagnose → Plan → Learn → Practice → Evaluate → Adapt`

核心设计原则：

- Learning Goal Driven：围绕明确的学习目标组织学习过程。
- Learner-Centric：根据学习者已有知识和能力制定学习计划。
- Evidence-Based：基于练习、测评和实际表现判断知识掌握程度。
- Adaptive：根据学习表现持续调整学习路径。
- Explainable：学习计划和调整结果应当能够解释原因。
- Human-in-the-Loop：学习者始终拥有对学习计划和关键决策的控制权。


## 2. Target Users

### 2.1 Primary Users

首期目标用户为成年技术学习者，包括：

- 软件工程师。
- 后端工程师。
- AI 工程师。
- 希望转型或拓展技术领域的开发者。
- 需要系统掌握新技术的专业人士。

### 2.2 User Characteristics

目标用户通常具有以下特征：

- 有一定的技术基础和自主学习能力。
- 能够理解技术文档、论文和代码。
- 有明确的学习目标，但不一定知道最佳学习路径。
- 希望将理论知识转化为工程实践能力。
- 希望获得持续反馈，而不仅仅是一次性问答。

### 2.3 Initial User Persona

**Persona: Technical Professional**

目标：

系统学习 Agent Engineering，最终能够独立设计和实现具有生产级工程意识的 Agent 系统。

当前困难：

- Agent Engineering 涉及多个相互关联的知识领域。
- 不清楚不同技术之间的依赖关系。
- 难以判断技术概念是否真正掌握。
- 阅读资料和工程实践之间缺少有效衔接。
- 学习计划需要根据实际进展不断调整。


## 3. Core User Journey

### 3.1 Learning Lifecycle

产品围绕完整学习闭环组织功能。

#### Stage 1: Diagnose

了解学习者当前的知识基础和能力。

核心任务：

- 收集学习者的学习目标。
- 识别已有知识和经验。
- 评估关键知识点的掌握情况。
- 识别知识缺口。
- 形成初始 Learner Profile。

输出：

- Learning Goal
- Initial Knowledge Assessment
- Knowledge Gaps
- Learner Profile

#### Stage 2: Plan

根据学习目标和诊断结果生成个性化学习计划。

核心任务：

- 将学习目标分解为知识领域。
- 建立知识点之间的依赖关系。
- 生成学习阶段和任务。
- 确定每个任务的学习目标。
- 为学习任务关联参考资料和实践活动。

输出：

- Learning Roadmap
- Knowledge Map
- Learning Tasks
- Learning Schedule

#### Stage 3: Learn

为学习者提供围绕具体学习目标组织的教学和知识辅助。

核心任务：

- 解释技术概念。
- 总结和组织参考资料。
- 根据学习者水平调整解释深度。
- 提供代码示例和工程案例。
- 支持不同教学策略，例如直接讲解和苏格拉底式提问。
- 保留学习上下文。

输出：

- Learning Materials
- Concept Explanations
- Learning Notes
- Learning Session History

#### Stage 4: Practice

通过练习和实际任务检验知识理解程度。

核心任务：

- 根据学习目标生成练习。
- 提供概念理解题。
- 提供代码实践任务。
- 提供场景分析和设计任务。
- 接收学习者提交的答案或实践成果。

输出：

- Practice Tasks
- Learner Submissions
- Practice History

#### Stage 5: Evaluate

评估学习者对知识点的实际掌握情况。

核心任务：

- 评价答案正确性。
- 评价理解深度。
- 评价实际应用能力。
- 识别错误理解和知识盲区。
- 更新知识掌握状态。
- 给出针对性的反馈。

输出：

- Assessment Results
- Knowledge Mastery
- Weakness Analysis
- Learning Feedback

#### Stage 6: Adapt

根据最新评估结果调整学习计划。

核心任务：

- 识别需要重新学习的知识点。
- 调整后续学习任务。
- 处理知识依赖关系。
- 调整学习顺序和学习难度。
- 安排复习任务。
- 向学习者解释调整原因。

输出：

- Updated Learner Profile
- Updated Learning Plan
- Review Tasks
- Learning Progress


## 4. Functional Requirements

### 4.1 Learning Domain Management

系统需要能够组织和管理学习领域。

核心需求：

- 支持创建学习领域。
- 支持定义学习目标。
- 支持将学习领域拆解为知识模块。
- 支持定义知识点及其依赖关系。
- 支持关联学习资料和实践任务。

首期示例：

Learning Domain: Agent Engineering

可能包含：

- LLM Fundamentals
- Prompt Engineering
- Tool Calling
- Agent Runtime
- Agent Orchestration
- Memory
- RAG
- Planning
- Reliability
- Security
- Evaluation
- Observability

### 4.2 Learner Profile

系统需要维护学习者的长期学习状态。

核心需求：

- 保存学习目标。
- 保存已有知识和经验。
- 保存知识点掌握程度。
- 保存学习历史。
- 保存练习和测评结果。
- 记录学习者的薄弱知识点。
- 支持根据最新评估结果更新学习者状态。

Learner Profile 应当是可持续更新的状态，而不是一次性生成的静态报告。

### 4.3 Learning Diagnosis

系统需要根据学习目标和学习者现有能力进行诊断。

核心需求：

- 支持初始能力评估。
- 支持知识点级别的诊断。
- 支持通过提问和练习收集证据。
- 识别知识缺口。
- 区分未知、部分掌握和已掌握状态。
- 记录诊断依据和置信程度。

### 4.4 Personalized Learning Plan

系统需要根据学习者状态生成个性化学习计划。

核心需求：

- 将学习目标分解为阶段性目标。
- 建立知识点依赖关系。
- 生成学习任务。
- 为任务关联学习资料。
- 为任务定义完成条件。
- 支持学习者查看和调整计划。
- 支持计划版本管理。
- 支持根据评估结果调整计划。

### 4.5 AI Tutor

系统需要提供具备学习上下文的 AI Tutor。

核心需求：

- 理解当前学习目标。
- 理解当前学习任务。
- 获取相关学习资料。
- 基于可信资料提供解释。
- 支持追问和深入讲解。
- 根据学习者水平调整教学方式。
- 支持代码示例和工程案例。
- 尽可能提供来源引用。
- 将有价值的学习结果沉淀到学习记录中。

教学方式：

- Direct Teaching：直接解释知识。
- Socratic Teaching：通过提问引导学习者思考。
- Example-Based Teaching：通过案例和代码解释概念。
- Review-Based Teaching：针对薄弱知识点进行复习。

### 4.6 Practice & Assessment

系统需要通过练习和评估收集学习证据。

核心需求：

- 支持不同类型的练习。
- 支持提交答案。
- 支持代码和文本类作业。
- 支持基于 Rubric 的评价。
- 提供错误分析。
- 提供改进建议。
- 更新知识点掌握状态。
- 保存评估历史。

评估维度可以包括：

- Correctness
- Conceptual Understanding
- Reasoning
- Practical Application
- Problem Solving

评估结果不应仅依赖单次 LLM 判断，应当结合任务类型、评价标准和历史证据。

### 4.7 Adaptive Learning

系统需要根据学习者的实际表现动态调整学习计划。

核心需求：

- 根据评估结果识别薄弱知识点。
- 根据知识依赖关系调整学习顺序。
- 为薄弱知识点安排补充学习。
- 为已掌握知识点减少重复学习。
- 支持复习任务安排。
- 保留学习计划调整历史。
- 解释调整原因。

### 4.8 Learning Progress

系统需要让学习者了解自己的学习进展。

核心需求：

- 展示整体学习进度。
- 展示阶段性目标完成情况。
- 展示知识点掌握情况。
- 展示薄弱知识点。
- 展示练习和测评记录。
- 展示学习计划变化。
- 展示下一步建议。


## 5. Non-Functional Requirements

### 5.1 Reliability

- 关键学习状态不能因单次 Agent 执行失败而丢失。
- 关键操作需要具备明确的状态管理。
- 对外部模型和工具调用设置超时及错误处理。
- 对可能重复执行的操作考虑幂等性。
- 重要状态变更需要可追踪。

### 5.2 Explainability

- 学习计划应说明生成依据。
- 诊断结果应保留证据。
- 测评结果应说明判断理由。
- 学习计划调整应说明触发原因。

### 5.3 Observability

- 记录 Agent 执行过程中的关键事件。
- 记录模型调用、工具调用和任务执行情况。
- 记录 Token、延迟和成本。
- 支持错误排查和执行链路追踪。
- 支持 Agent 评估结果分析。

### 5.4 Security

- 对模型输入和外部内容进行必要的安全处理。
- 限制 Agent 的工具权限。
- 防止 Prompt Injection 和越权工具调用。
- 对外部资源访问设置边界。
- 保护学习者数据和学习记录。

### 5.5 Maintainability

- 采用清晰的领域边界。
- 核心业务逻辑与 Agent 执行逻辑解耦。
- 模型、工具和工作流具备可替换性。
- 关键业务状态具备明确的数据模型。
- 重要架构决策需要记录。


## 6. Product Scope

### 6.1 Current Scope

当前产品围绕单个学习者的完整学习闭环展开。

重点关注：

- 学习目标管理。
- 学习者知识诊断。
- 个性化学习计划。
- AI Tutor。
- 练习与评估。
- 动态学习计划调整。
- 学习状态管理。
- Agent Runtime 的可靠性、可观测性和评估。

### 6.2 Explicitly Out of Scope

当前阶段不考虑：

- K12 教育场景。
- 教师和学校管理。
- 完整 LMS 系统。
- 课程交易市场。
- 社交学习和社区。
- 多租户 SaaS。
- 复杂商业化和支付系统。
- 原生移动 App。
- 复杂推荐算法。
- 多 Agent 协作架构。
- 企业培训管理系统。

这些能力不属于当前产品目标，后续是否引入应由实际需求决定。


## 7. Product Success Metrics

产品成功不能仅通过功能数量或 Agent 调用次数衡量。

### 7.1 Learning Effectiveness

- 学习目标完成率。
- 知识点掌握程度变化。
- 练习正确率变化。
- 薄弱知识点改善情况。
- 延迟复习后的知识保持情况。

### 7.2 Learning Experience

- 学习计划可理解程度。
- 学习任务完成情况。
- AI Tutor 回答的相关性和可信度。
- 学习反馈的有效性。
- 学习者对动态调整的接受程度。

### 7.3 Agent Engineering Quality

- Agent Task Success Rate。
- Tool Calling Success Rate。
- Workflow Completion Rate。
- Evaluation Pass Rate。
- 平均响应延迟。
- 单次学习任务成本。
- 异常恢复能力。
- 关键执行链路可追踪率。

### 7.4 Initial Validation

首期通过真实学习过程验证产品：

使用 Agent Engineering 作为学习主题，由真实学习者持续使用产品完成学习闭环。

重点观察：

1. 系统能否正确理解学习目标。
2. 能否识别学习者已有知识和知识缺口。
3. 能否生成合理的学习计划。
4. 能否围绕学习目标提供有效教学。
5. 能否通过练习判断真实掌握程度。
6. 能否根据学习证据调整后续计划。


## 8. Key Product Principles

### 8.1 Learning Evidence Over Learning Activity

不能简单地将阅读时长、任务完成数量或学习天数视为知识掌握程度。

应优先使用练习结果、实际应用表现和复习表现作为学习证据。

### 8.2 Adaptive Planning Over Static Curriculum

学习计划不是固定课程目录。

它需要随着学习者的知识状态和实际表现持续变化。

### 8.3 Structured Learning Over Unbounded Chat

AI Tutor 的对话应服务于当前学习目标和学习任务。

不能让整个产品退化为一个没有学习状态管理的通用 Chatbot。

### 8.4 Evidence-Based AI Decisions

重要的学习诊断、知识掌握判断和计划调整，应当有可追溯的证据支持。

### 8.5 Deterministic Engineering Around Probabilistic Models

LLM 负责理解、生成和判断等概率性任务。

核心业务状态、权限控制、任务状态流转、数据持久化和关键约束应尽可能由确定性工程逻辑保证。


## 9. Product Evolution

产品后续可以逐步演进：

### Phase 1: Learning Loop Foundation

建立完整的单学习者学习闭环。

### Phase 2: Learning Intelligence

增强知识掌握建模、学习效果评估和自适应学习能力。

### Phase 3: Learning Experience

改善学习交互、学习进度展示和长期学习体验。

### Phase 4: Extensibility

在实际需求明确后，考虑支持更多学习领域、学习者类型和外部学习资源。

以上是产品演进方向，不代表当前阶段必须实现的功能。


## 10. Product Acceptance Criteria

产品需要通过以下端到端场景验证：

一个成年技术学习者希望系统学习 Agent Engineering。

1. 能够创建学习目标。
2. 系统能够了解学习者的已有知识基础。
3. 系统能够识别主要知识缺口。
4. 系统能够生成有知识依赖关系的学习计划。
5. 学习者能够按照计划进行学习。
6. AI Tutor 能够围绕当前学习目标提供教学支持。
7. 学习者能够完成练习并提交结果。
8. 系统能够根据评价标准分析学习者表现。
9. 系统能够更新知识掌握状态。
10. 系统能够根据新的学习证据调整后续计划。
11. 学习者能够查看完整的学习进度和计划变化。
12. 整个学习过程具备必要的执行记录、错误处理和可观测性。

最终验收标准：

**系统能够持续管理一个真实学习者的完整学习过程，并根据学习证据做出合理、可解释的学习计划调整。**


## 11. Open Questions

以下问题留待后续产品和 MVP 设计阶段进一步确定：

- 学习者知识状态采用什么样的数据模型？
- 知识掌握程度如何计算和更新？
- 学习计划采用怎样的任务粒度？
- 学习资料如何组织、索引和引用？
- 如何区分 LLM 生成内容与经过验证的知识资料？
- 哪些决策由 Agent 完成，哪些由确定性业务逻辑完成？
- 如何设计学习计划的版本管理和回滚？
- 如何评估长期学习效果？
- 如何建立可靠的 Agent Evaluation Dataset？

这些问题需要通过领域建模、技术研究和实际实验逐步解决，而不是在 PRD 阶段预设唯一答案。
