# 系统架构

> 版本：0.1.0
> 状态：草稿（Draft）
> 最后更新：2026-11-13

## 1. 系统概述

Agent Learning Coach 是一个面向成年学习者的 AI 学习教练，旨在通过个性化和自适应的学习体验，帮助学习者实现学习目标。

系统围绕完整的学习闭环组织核心业务流程：

`Diagnose → Plan → Learn → Practice → Evaluate → Adapt`

即：

**诊断 → 规划 → 学习 → 练习 → 评估 → 调整**

系统将学习业务领域与 Agent 执行能力分离，以提高系统的可维护性、可测试性和技术扩展能力。

## 2. 架构设计原则

1. **领域优先（Domain First）**：学习领域的业务逻辑不依赖特定的 LLM 提供商或 Agent 框架。
2. **关注点分离（Separation of Concerns）**：业务逻辑、Agent 执行、API 和基础设施具有明确的职责边界。
3. **确定性核心（Deterministic Core）**：关键业务规则和状态流转由确定性的应用逻辑控制。
4. **技术解耦（Provider Independence）**：LLM 提供商和基础设施组件应具备可替换性。
5. **基于证据的学习（Evidence-Based Learning）**：学习进度和知识掌握度的判断应基于可观察的学习证据。
6. **可靠性优先（Reliability by Design）**：从设计初期就考虑 Agent 执行失败处理、可观测性和效果评估。

## 3. 系统组件

系统在逻辑上划分为五个层次。

### 3.1 API 层（API Layer）

负责系统对外提供服务和处理外部请求。

主要职责：

- 提供 HTTP API 接口。
- 处理请求和响应。
- 进行请求参数校验。
- 处理 API 层面的异常。

### 3.2 应用层（Application Layer）

负责应用用例的组织、业务流程编排和模块协作。

主要职责：

- 编排应用业务流程。
- 协调 Learning Domain 与 Agent Runtime。
- 管理应用层事务和业务状态流转。
- 将用户请求转换为具体的业务操作。

### 3.3 学习领域层（Learning Domain）

负责学习产品的核心业务能力和领域规则。

主要组成：

- Learner Profile：学习者档案。
- Learning Goal：学习目标。
- Knowledge Map：知识地图。
- Learning Plan：学习计划。
- Learning Session：学习会话。
- Practice & Assessment：练习与评估。
- Learning Progress：学习进度。
- Mastery Model：知识掌握度模型。

学习领域层不应依赖特定的 LLM 提供商或 Agent 框架。

### 3.4 Agent 运行时层（Agent Runtime）

负责 Agent 的执行、运行控制和通用能力。

主要组成：

- Model Abstraction：模型抽象。
- Agent Loop：Agent 执行循环。
- Tool Calling：工具调用。
- Context Management：上下文管理。
- Memory：记忆管理。
- RAG：检索增强生成。
- Guardrails：安全约束。
- Observability：可观测性。
- Evaluation：效果评估。

Agent Runtime 为应用层提供可复用的 Agent 执行能力。

### 3.5 基础设施层（Infrastructure Layer）

负责外部依赖和底层技术基础设施的接入。

主要组成：

- 关系型数据库。
- 向量数据库。
- LLM 服务提供商。
- 外部工具和服务。
- 文件存储。

## 4. 核心业务流程

系统的核心业务流程是自适应学习闭环。

1. **诊断（Diagnose）**：了解学习者当前的知识水平，识别知识薄弱点。
2. **规划（Plan）**：根据学习目标和诊断结果生成个性化学习计划。
3. **学习（Learn）**：提供具有上下文的教学内容和学习材料。
4. **练习（Practice）**：生成练习任务，收集学习者的答题结果。
5. **评估（Evaluate）**：根据学习证据评估学习效果和知识掌握情况。
6. **调整（Adapt）**：更新学习者状态，并根据评估结果调整学习计划。

整个流程由应用层负责协调。

LLM 主要承担理解、推理、生成和辅助评估等概率性任务；关键业务状态变更和业务规则由确定性的领域逻辑进行管理。

## 5. 技术选型

| 技术领域 | 当前选型 | 状态 |
|---|---|---|
| 开发语言 | Python 3.13+ | 已确定 |
| 包管理 | uv | 已确定 |
| 数据校验 | Pydantic | 已确定 |
| LLM 接入 | LiteLLM | 计划采用 |
| Agent 协议 | MCP | 计划采用 |
| 向量数据库 | ChromaDB | 计划采用 |
| 自动化测试 | pytest | 已确定 |
| 代码检查与格式化 | Ruff | 已确定 |
| 静态类型检查 | mypy | 已确定 |
| API 框架 | TBD | 待确定 |
| 关系型数据库 | TBD | 待确定 |
| 可观测性 | TBD | 待确定 |

注：技术选型会随着工程实践持续验证和调整。依赖库已经引入，不代表对应技术方案已经成为最终架构决策。

## 6. 架构演进

本文档描述系统当前阶段的初始逻辑架构。

随着项目开发逐步推进，将进一步补充模块边界、接口定义、数据模型、数据库设计、Agent Runtime 内部架构以及部署架构等内容。

重要的架构决策应通过架构决策记录（Architecture Decision Records，ADR）进行管理。

---

## 相关文档

- 产品需求文档：`../product/prd.md`
- 产品范围：`../product/product-scope.md`
- 产品路线图：`../product/roadmap.md`
- 架构决策记录：`./decisions/README.md`
