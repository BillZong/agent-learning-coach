# 项目路线图（Roadmap）

> 项目名称：agent-learning-coach  
> 产品定位：AI Learning Coach  
> 当前版本：V1.0  
> 项目周期：12 周  
> 状态：Draft

## 1. 项目愿景（Project Vision）

构建一个具备生产级工程能力的 AI Learning Coach，通过 Agent Engineering 技术管理学习者的完整学习过程，帮助成年学习者实现持续、个性化和自适应的知识学习。

项目围绕两个核心方向持续演进：

1. **Agent Engineering：** 构建可靠、可观测、可评估、具备安全约束的 Agent Runtime。
2. **AI Learning Coach：** 构建能够理解学习者状态、组织知识、制定学习计划、提供教学与评估，并动态调整学习路径的 AI 学习教练。

核心学习闭环：

**Diagnose → Plan → Learn → Practice → Evaluate → Adapt**

核心工程原则：

- LLM 负责理解、生成、分析和概率性判断。
- 确定性工程逻辑负责业务状态、权限、数据一致性和关键约束。
- Agent Runtime 与具体业务领域解耦。
- 系统设计不绑定单一模型供应商或 Agent Framework。
- 学习决策应建立在真实学习证据之上。
- 优先保证系统可靠性，而不是追求架构复杂度。

## 2. 路线图概览（Roadmap Overview）

项目按照 12 周进行规划，分为六个阶段。

| 阶段 | 周期 | 阶段名称 | 核心目标 |
|---|---|---|---|
| Phase 0 | Week 1–2 | Agent Engineering Foundation | 建立 Agent Runtime 工程基础 |
| Phase 1 | Week 3–4 | Learning Domain & Planning | 建立学习领域模型和学习规划能力 |
| Phase 2 | Week 5–6 | AI Tutor & Learning Context | 实现具备学习上下文的 AI 教学 |
| Phase 3 | Week 7–8 | Practice & Assessment | 实现练习、评估和知识掌握度分析 |
| Phase 4 | Week 9–10 | Adaptive Learning | 实现基于学习状态的动态学习调整 |
| Phase 5 | Week 11–12 | Productization & Delivery | 完成产品化、工程验证和项目交付 |

阶段之间存在依赖关系，原则上应先完成当前阶段的核心验收，再进入下一阶段。

如果前置阶段存在影响后续功能的关键问题，应优先解决，不为了满足时间节点而牺牲工程质量。

---

## 3. 阶段规划（Phase Roadmap）

### Phase 0：Agent Engineering Foundation

**周期：Week 1–2**

**阶段目标：** 建立可靠的 Agent Runtime 基础，理解并掌握 Agent Engineering 的核心技术与工程实践。

本阶段以工程基础建设和系统性技术学习为主，不追求完整产品功能。

#### 核心工作

1. LLM & Tool System
   - LLM 接入与调用抽象。
   - Prompt Engineering。
   - Tool Calling。
   - Tool Schema 与参数校验。
   - MCP 基础与工具集成。

2. Agent Runtime
   - Agent 执行生命周期。
   - ReAct Loop。
   - Agent State。
   - Session 管理。
   - Execution Context。
   - Runtime Architecture。

3. Reliable Execution
   - 异常处理。
   - 超时与重试。
   - 任务状态管理。
   - 幂等性。
   - 执行恢复。
   - 持久化基础。

4. Policy & Human-in-the-Loop
   - 工具权限控制。
   - Tool Policy。
   - Human-in-the-Loop。
   - 高风险操作确认。
   - Agent 安全边界。

5. Memory & RAG
   - 短期记忆与长期记忆。
   - Context Management。
   - Embedding。
   - Vector Search。
   - RAG 基础架构。

6. Observability & Evaluation
   - Agent Tracing。
   - Logging 与 Metrics。
   - Agent Evaluation。
   - 基础评估数据集。
   - 执行过程分析。

#### 阶段交付物

- [ ] 可运行的基础 Agent Runtime。
- [ ] LLM 调用抽象。
- [ ] Tool Calling 与工具执行机制。
- [ ] Agent State 与 Session 管理。
- [ ] 基础 Memory 与 RAG 能力。
- [ ] 工具权限与安全策略。
- [ ] 基础执行追踪能力。
- [ ] 基础 Agent Evaluation。
- [ ] Agent Runtime Architecture 文档。
- [ ] 关键架构决策记录（ADR）。

#### 阶段验收标准

- [ ] Agent 能够理解用户输入并完成多轮交互。
- [ ] Agent 能够根据任务选择并调用工具。
- [ ] Agent 能够管理执行状态和 Session。
- [ ] 工具执行异常能够得到合理处理。
- [ ] 关键执行过程能够追踪。
- [ ] 具备基本的工具安全控制。
- [ ] 能够通过评估用例验证基础 Agent 行为。

---

### Phase 1：Learning Domain & Planning

**周期：Week 3–4**

**阶段目标：** 建立 AI Learning Coach 的核心业务模型，使系统能够理解学习目标、组织知识结构并生成结构化学习计划。

#### 核心工作

1. Learning Domain
   - 学习领域模型。
   - 学习主题与课程管理。
   - 知识点管理。
   - 知识依赖关系。
   - 知识结构组织。

2. Learner Profile
   - 学习者基本信息。
   - 学习背景。
   - 已有知识与技能。
   - 学习偏好。
   - 学习目标与约束。

3. Learning Diagnosis
   - 初始能力诊断。
   - 知识基础分析。
   - 学习能力评估。
   - 知识缺口识别。
   - 诊断结果结构化。

4. Learning Planning
   - 学习目标拆解。
   - 知识点学习顺序。
   - 学习任务生成。
   - 学习计划结构化。
   - 学习计划持久化。

#### 阶段交付物

- [ ] Learning Domain 数据模型。
- [ ] Knowledge Structure 数据模型。
- [ ] Learner Profile。
- [ ] Learning Goal 管理。
- [ ] Learning Diagnosis。
- [ ] Learning Plan 生成能力。
- [ ] 学习计划持久化机制。
- [ ] Learning Domain Architecture 文档。

#### 阶段验收标准

- [ ] 能够创建和管理学习领域。
- [ ] 能够组织知识点及其依赖关系。
- [ ] 能够创建学习目标。
- [ ] 能够根据学习者背景开展初始诊断。
- [ ] 能够识别初步知识缺口。
- [ ] 能够生成结构化、可执行的学习计划。
- [ ] 学习计划能够持久化并被后续模块使用。

---

### Phase 2：AI Tutor & Learning Context

**周期：Week 5–6**

**阶段目标：** 建立具备课程知识和学习者上下文的 AI Tutor，实现连续、个性化的教学交互。

#### 核心工作

1. Course Knowledge Management
   - 课程资料管理。
   - 文档解析。
   - 文档切分。
   - Embedding。
   - 向量索引。
   - 检索与召回。

2. RAG Engineering
   - Retrieval Pipeline。
   - Chunking 策略。
   - Metadata Filtering。
   - Context Construction。
   - 检索结果引用。
   - 检索质量评估。

3. Learning Context
   - 学习目标上下文。
   - 当前学习任务上下文。
   - 知识点上下文。
   - 学习者状态上下文。
   - Session Context。
   - Memory 管理。

4. AI Tutor
   - 知识讲解。
   - 概念解释。
   - 示例生成。
   - 引导式提问。
   - 苏格拉底式教学。
   - 基于学习进度调整教学方式。

#### 阶段交付物

- [ ] 课程资料管理能力。
- [ ] RAG Pipeline。
- [ ] 检索结果来源追踪与引用。
- [ ] Learning Context 管理。
- [ ] AI Tutor。
- [ ] 教学 Session 管理。
- [ ] 基础教学质量评估用例。
- [ ] RAG 与 Context Engineering 设计文档。

#### 阶段验收标准

- [ ] 能够导入并索引课程资料。
- [ ] 能够根据问题检索相关知识。
- [ ] 教学内容能够关联知识来源。
- [ ] AI Tutor 能够结合学习目标开展教学。
- [ ] AI Tutor 能够理解当前学习任务。
- [ ] 多轮教学能够保持必要的上下文连续性。
- [ ] 能够根据学习者情况调整教学内容和方式。
- [ ] 具备基础的 RAG 质量评估能力。

---

### Phase 3：Practice & Assessment

**周期：Week 7–8**

**阶段目标：** 建立基于实际学习表现的练习与评估机制，使系统能够通过学习证据判断知识掌握情况。

#### 核心工作

1. Practice Generation
   - 基于知识点生成练习。
   - 基于学习目标生成任务。
   - 练习难度控制。
   - 练习任务管理。

2. Practice Execution
   - 练习提交。
   - 答案与过程记录。
   - 提交结果持久化。
   - 学习任务状态管理。

3. Assessment
   - 评估标准设计。
   - Rubric 管理。
   - 规则校验。
   - LLM 辅助评估。
   - 评估结果结构化。
   - 评估反馈生成。

4. Learner Knowledge Assessment
   - 知识点掌握度估计。
   - 学习薄弱点识别。
   - 错误类型分析。
   - 学习证据记录。
   - 评估结果追踪。

#### 阶段交付物

- [ ] Practice 数据模型。
- [ ] 练习生成能力。
- [ ] 练习提交与记录能力。
- [ ] Assessment Rubric。
- [ ] 自动评估能力。
- [ ] 结构化评估结果。
- [ ] Mastery Estimation 基础实现。
- [ ] Learning Evidence 数据模型。
- [ ] Assessment Evaluation Dataset。

#### 阶段验收标准

- [ ] 能够根据知识点生成练习。
- [ ] 学习者能够提交练习结果。
- [ ] 系统能够根据明确的评估标准进行评价。
- [ ] 能够结合规则和 LLM 完成评估。
- [ ] 能够生成结构化反馈。
- [ ] 能够识别知识薄弱点。
- [ ] 能够基于实际练习结果更新初步掌握度。
- [ ] 评估过程和结果具备可追踪性。

---

### Phase 4：Adaptive Learning

**周期：Week 9–10**

**阶段目标：** 建立基于学习者知识状态和实际学习证据的动态调整能力，实现学习计划的持续优化。

#### 核心工作

1. Learner Modeling
   - 学习者知识状态。
   - 知识点掌握度。
   - 学习历史。
   - 学习表现。
   - 学习信心与不确定性。

2. Knowledge State Analysis
   - 知识依赖分析。
   - 知识缺口分析。
   - 薄弱点识别。
   - 知识掌握情况变化。
   - 学习进度分析。

3. Adaptive Planning
   - 学习计划调整。
   - 学习任务重新排序。
   - 学习路径调整。
   - 学习任务难度调整。
   - 补充学习内容。
   - 复习任务生成。

4. Plan Management
   - 学习计划版本管理。
   - 计划调整记录。
   - 调整原因说明。
   - 学习者确认机制。
   - 计划执行状态管理。

5. Learning Reflection
   - 学习过程总结。
   - 学习效果复盘。
   - 阶段性能力分析。
   - 下一阶段学习建议。

#### 阶段交付物

- [ ] Learner Knowledge State。
- [ ] 知识掌握度更新机制。
- [ ] Knowledge Gap Analysis。
- [ ] Adaptive Planning Engine。
- [ ] Learning Path Adjustment。
- [ ] Learning Plan Versioning。
- [ ] 学习计划调整解释机制。
- [ ] Learning Reflection。

#### 阶段验收标准

- [ ] 能够持续更新学习者知识状态。
- [ ] 能够根据学习证据识别知识薄弱点。
- [ ] 能够分析知识依赖关系对学习路径的影响。
- [ ] 能够根据学习表现调整后续学习计划。
- [ ] 能够记录计划调整历史。
- [ ] 能够向学习者解释调整原因。
- [ ] 学习者能够理解并控制计划调整。
- [ ] 能够形成完整的学习复盘。

---

### Phase 5：Productization & Delivery

**周期：Week 11–12**

**阶段目标：** 完成产品化交付，将前面构建的能力整合为一个可运行、可演示、可评估的完整工程项目。

#### 核心工作

1. Application API
   - 核心业务 API。
   - Agent API。
   - 异步任务 API。
   - API 参数校验。
   - API 异常处理。

2. User Interface
   - 基础 Web UI。
   - 学习目标展示。
   - 学习计划展示。
   - AI Tutor 交互。
   - 练习与评估展示。
   - 学习进度展示。

3. Observability
   - Agent Execution Tracing。
   - 业务事件追踪。
   - 关键 Metrics。
   - 日志管理。
   - 错误分析。

4. Reliability & Security
   - 核心链路测试。
   - 异常恢复测试。
   - 工具安全测试。
   - 权限控制验证。
   - 数据一致性验证。
   - Agent 可靠性评估。

5. Evaluation
   - Agent Evaluation Dataset。
   - Learning Evaluation Dataset。
   - 端到端测试。
   - 评估结果分析。
   - 关键质量指标。

6. Documentation & Delivery
   - 项目架构文档。
   - API 文档。
   - 部署文档。
   - 开发文档。
   - ADR 整理。
   - Demo 场景。
   - 技术总结。

#### 阶段交付物

- [ ] 完整的后端 API。
- [ ] 基础 Web UI。
- [ ] 完整学习流程演示。
- [ ] Agent Observability。
- [ ] 核心链路测试。
- [ ] Agent Evaluation。
- [ ] Learning Evaluation。
- [ ] 部署与运行文档。
- [ ] 完整架构文档。
- [ ] 项目 Demo。
- [ ] 项目技术总结。

#### 阶段验收标准

- [ ] 系统能够正常部署和运行。
- [ ] 学习者能够通过系统完成完整学习闭环。
- [ ] 各业务模块之间能够正确协作。
- [ ] 核心业务状态能够持久化。
- [ ] Agent 执行过程能够追踪。
- [ ] 核心功能具备自动化测试。
- [ ] 系统具备必要的安全和异常处理机制。
- [ ] 具备可复现的评估用例。
- [ ] 能够通过 Demo 清晰展示产品价值和工程能力。

---

## 4. 关键里程碑（Milestones）

| 里程碑 | 时间 | 预期成果 | 验收重点 |
|---|---|---|---|
| M1：Runtime Foundation | Week 2 | 基础 Agent Runtime | 工具执行、状态管理、可靠性、可观测性 |
| M2：Learning Core | Week 4 | 学习领域与学习规划 | 知识结构、诊断、学习计划 |
| M3：AI Tutor | Week 6 | 具备学习上下文的 AI Tutor | RAG、教学质量、上下文连续性 |
| M4：Assessment | Week 8 | 练习与评估系统 | 学习证据、评估质量、掌握度 |
| M5：Adaptive Learning | Week 10 | 自适应学习能力 | 知识状态、计划调整、可解释性 |
| M6：Project Delivery | Week 12 | 完整项目交付 | 端到端闭环、可靠性、评估和 Demo |

## 5. 跨阶段工程建设（Cross-Phase Engineering）

以下工程能力不是某一个阶段的独立功能，而是需要贯穿整个项目。

| 工程领域 | 建设重点 | 主要阶段 |
|---|---|---|
| Agent Runtime | 执行生命周期、状态管理、工具调用 | Phase 0–5 |
| Reliability | 异常处理、重试、幂等、恢复 | Phase 0–5 |
| Security | 权限控制、工具安全、输入输出约束 | Phase 0–5 |
| Memory & Context | Session、短期记忆、长期记忆、上下文管理 | Phase 0–5 |
| RAG | 知识检索、引用、检索评估 | Phase 0–5 |
| Observability | Tracing、Logging、Metrics | Phase 0–5 |
| Evaluation | Agent Evaluation、Learning Evaluation | Phase 0–5 |
| Data Management | 业务数据模型、状态持久化、数据一致性 | Phase 1–5 |
| Testing | 单元测试、集成测试、端到端测试 | Phase 0–5 |
| Documentation | Architecture、ADR、技术文档 | Phase 0–5 |

说明：

- Phase 0 建立基础机制。
- 后续阶段根据具体业务需求持续完善。
- Phase 5 对整体工程质量进行集中验证和交付。

## 6. 技术知识积累路线（Knowledge Development Roadmap）

项目开发与 Agent Engineering 系统性学习同步进行。

### 6.1 主教材

以《Build an AI Agent (From Scratch)》作为主要学习材料：

- Ch1–Ch8：重点阅读，结合代码实践理解核心概念。
- Ch9：概念性阅读，掌握相关设计思路。
- Ch10：系统性阅读，重点关注 Evaluation，并结合项目实践。

### 6.2 多来源交叉学习

避免只依赖单一本教材或单一技术体系，结合官方资料、工程实践和学术研究进行交叉验证。

| 资料来源 | 重点学习内容 |
|---|---|
| Anthropic | Building Effective Agents、Workflow Patterns、Evaluator-Optimizer |
| OpenAI | Agent 构建实践、Tool Calling、Agent Evaluation、Agents SDK |
| LangGraph | Runtime、Persistence、Memory、Human-in-the-Loop |
| MCP | Specification、Architecture、Tools、Resources、Authorization |
| OpenTelemetry | Traces、Metrics、Logs、Python Instrumentation |
| OWASP | Top 10 for Agentic Applications |
| NIST | Generative AI Risk Management Profile |
| 学术论文 | ReAct、RAG、Agent Memory、Agent Evaluation、Knowledge Tracing |

### 6.3 知识输出

围绕项目实践，至少形成以下技术主题的知识沉淀：

1. Agent Architecture & Runtime。
2. LLM & Tool Calling。
3. MCP & Tool Integration。
4. Agent State & Session Management。
5. Agent Memory & Context Engineering。
6. RAG Engineering。
7. Agent Reliability & Fault Tolerance。
8. Agent Security & Policy。
9. Agent Observability。
10. Agent Evaluation。
11. Learner Modeling。
12. Adaptive Learning。

计划形成四篇技术文章初稿：

- Agent Runtime Engineering：如何构建可靠的 Agent Runtime。
- Reliable Agent System：Agent 系统的可靠性设计。
- Memory and RAG Engineering：Agent Memory 与 RAG 工程实践。
- Agent Evaluation and Observability：Agent 评估与可观测性。

## 7. 项目完成标准（Definition of Done）

12 周项目周期结束时，至少满足以下要求：

### 产品层面

- [ ] 完成 AI Learning Coach 的核心学习闭环。
- [ ] 能够支持一个真实学习者完成 Agent Engineering 学习过程。
- [ ] 能够根据学习证据评估知识掌握情况。
- [ ] 能够根据学习状态调整学习计划。
- [ ] 能够展示学习进度与学习历史。

### 工程层面

- [ ] Agent Runtime 具备基本的可靠执行能力。
- [ ] 关键状态具备持久化能力。
- [ ] 工具执行具备安全控制。
- [ ] 关键执行过程具备可观测性。
- [ ] 核心 Agent 能力具备评估机制。
- [ ] 业务逻辑与 Agent Runtime 保持合理解耦。
- [ ] 具备必要的自动化测试。

### 知识积累层面

- [ ] 完成主教材重点章节学习。
- [ ] 完成关键官方资料与研究论文的交叉学习。
- [ ] 形成至少 10 篇主题知识笔记。
- [ ] 形成架构文档、ADR、Threat Model 和 Evaluation Design。
- [ ] 完成四篇技术文章初稿。
- [ ] 能够清晰解释关键架构设计、技术取舍和工程实践。

### 项目展示层面

- [ ] 代码仓库结构清晰。
- [ ] 项目能够独立部署和运行。
- [ ] README 能够清晰介绍项目。
- [ ] 提供架构图和核心设计文档。
- [ ] 提供端到端 Demo。
- [ ] 能够展示 Agent Engineering 的核心工程实践。
- [ ] 能够清晰说明项目的技术挑战与解决方案。

## 8. 路线图管理原则（Roadmap Governance）

1. 本路线图描述项目阶段目标和预期交付成果，不作为每日任务清单。
2. 具体学习与开发任务由 `PLAN.md` 管理。
3. 阶段目标和可量化结果由 `OKR.md` 管理。
4. 产品需求和长期产品方向由 `prd.md` 管理。
5. 当前阶段的实际实现边界由 `product-scope.md` 管理。
6. 技术架构和重要设计取舍由 Architecture 与 ADR 管理。
7. 每个阶段结束时进行阶段评审，确认交付物和验收标准。
8. 如果阶段目标、依赖关系或项目周期发生变化，应同步更新相关文档。
9. 不为追求技术覆盖面而引入与核心学习闭环无关的复杂功能。
10. 优先交付可运行、可验证、可解释的工程能力。

---

## 9. 相关文档（Related Documents）

| 文档 | 说明 |
|---|---|
| `prd.md` | 产品需求与长期产品方向 |
| `product-scope.md` | 当前产品范围与边界 |
| `roadmap.md` | 项目阶段规划与里程碑 |
| `OKR.md` | 项目目标与关键结果 |
| `PLAN.md` | 学习与开发执行计划 |
| `architecture/` | 系统架构设计 |
| `ADR/` | 架构决策记录 |
| `docs/knowledge/` | 技术知识积累 |
| `docs/evaluation/` | Agent 与 Learning Evaluation 设计 |
