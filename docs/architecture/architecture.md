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

当前已建模（Phase 1，见 [domain.md](../engineering/domain.md) 与
[ADR-0015](./decisions/0015-learning-domain-boundaries.md)）：

| 概念 | 类型 | 位置 |
| ---- | ---- | ---- |
| Learner | 实体（独立聚合） | `core.learning.domain.Learner` |
| Course / KnowledgePoint | 实体（课程聚合） | `core.learning.domain.Course` |
| Learning Goal | 实体（关联聚合） | `core.learning.domain.LearningGoal` |
| 各类 id、目标约束 | 值对象 | `core.learning.domain` |

边界规则：跨聚合只按 id 引用（目标记 `learner_id` / `course_id`，学习者不记
目标）；实体不可变；不变量在构造时校验；知识顺序是派生视图，不持久化。

**依赖方向**：`core.learning` 不认识模型、工具与运行时；学习目标通过
`bridge.to_goal_spec` 单向映射成 `GoalSpec` 后交给 `core.agent`。

学习领域层不应依赖特定的 LLM 提供商或 Agent 框架。

尚未建模：学习领域注册表、诊断结果、掌握度、学习进度、持久化。

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

代码上，`Model Abstraction` 与 `Tool Calling` 分属 `core/llm` 与 `core/tools`
两个平级包；`Agent Loop` 独立成 `core/agent` 包，与它们平级——循环只负责
「模型 ↔ 工具」的控制，不寄生在模型客户端或工具系统里。可观测性与评估也在
`core/agent` 内：它们**只读**运行结果，与循环同包不会造成反向依赖。

#### Model Abstraction

模型调用通过 `LLMClient` Protocol 抽象，厂商差异由适配器隔离：

- 协议定义：`src/agent_learning_coach/core/llm/protocol.py`
- 厂商无关模型：`src/agent_learning_coach/core/llm/models.py`、`types.py`
- Provider 命名空间注册表：`src/agent_learning_coach/core/llm/registry.py`
- 客户端工厂：`src/agent_learning_coach/core/llm/providers.py`
- 提供商适配器：`src/agent_learning_coach/core/llm/litellm_client.py`（OpenAI / Anthropic / Gemini）、`openai_compatible_client.py`（国产厂商与自建网关）
- 结构化输出：`src/agent_learning_coach/core/llm/structured.py`
- 测试替身：`src/agent_learning_coach/core/llm/mock.py`

Provider 用命名空间（厂商名 + 区域，如 `minimax`（国际）、`minimax-cn`（国内）、`deepseek`、`anthropic`）表达，模型名保持厂商原生形式，不需要 `openai/` 之类的前缀。入口为 `create_client(provider, model=...)`；超时支持请求级与客户端级两级配置，超时映射为 `LLMTimeoutError`。

决策依据：[ADR-0002](decisions/0002-llm-client-protocol.md)。
接入说明：[LLM 接入说明](../engineering/llm.md)。

#### Tool Calling

工具调用由 `core/tools` 包提供，与 `core/llm` 平级：

- 核心类型：`src/agent_learning_coach/core/tools/types.py`
  - `ToolDefinition`：工具的静态契约（名称、描述、参数 JSON Schema、超时），可转换为 `ToolSpec` 发给模型
  - `ToolContext`：运行时上下文（会话、调用方、截止时间）
  - `ToolOutcome`：归一化执行结果（状态、内容、结构化数据、错误、耗时）
  - `Tool` Protocol：工具契约，实现方无需继承
- 工具注册表：`src/agent_learning_coach/core/tools/registry.py`
- 工具调度器：`src/agent_learning_coach/core/tools/dispatcher.py`
- 参数校验：`src/agent_learning_coach/core/tools/schema.py`（JSON Schema draft 2020-12）
- 结果归一化：`src/agent_learning_coach/core/tools/normalization.py`
- 异常类型：`src/agent_learning_coach/core/tools/errors.py`

职责边界：**注册表管「有什么工具」，调度器管「怎么执行」**。参数校验集中在调度器一处——工具只声明「我接受什么」（JSON Schema），由调度器保证「传进来的一定合法」，避免每个工具各自实现校验逻辑。发给模型的 schema 与实际校验的 schema 是同一份，不会出现契约错位。

调用级失败（参数不合法、超时、执行异常）一律归一化成 `ToolOutcome(status="error")` 回填给模型，让模型能读懂错误并调整下一步；工具未注册属配置错误，抛 `ToolNotFoundError`。超时优先级为工具定义 > 调度器默认值，用 `asyncio.timeout` 实现，保证取消信号送达被调用协程。

**工具回调**（`before_hooks` / `after_hooks`）挂在 Dispatcher 上——它是唯一的
执行边界，因此本地工具、MCP 工具、结构化输出的 `final_answer` 全部受同一套回
调约束。执行前钩子可短路，执行后钩子可替换结果。**权限检查在钩子之前**，
因此钩子不能绕过权限。

**与 HITL 的组合**：审批在运行时（暂停—恢复，可持久化），**在回调之前**。
被拒绝的调用不触发任何回调；回调无法放行一个未获批准的调用。不要再用执行前
的钩子实现第二套审批——两套并存会让拒绝路径被绕过。

决策依据：[ADR-0003](decisions/0003-tool-system.md)、
[ADR-0012](decisions/0012-tool-callbacks-citation-and-hitl.md)。
接入说明：[工具系统接入说明](../engineering/tools.md)。

#### Agent Runtime

ReAct 执行循环 + 运行时状态，位于 `src/agent_learning_coach/core/agent/`：

- 循环与状态：`src/agent_learning_coach/core/agent/runtime.py`（`AgentRuntime`）
- 核心类型：`src/agent_learning_coach/core/agent/types.py`（`ExecutionContext` / `Event` / `AgentConfig` / `AgentStep` / `AgentResult` / `StopReason`）
- 成本核算：`src/agent_learning_coach/core/agent/cost.py`（`TokenPrice`）
- 异常类型：`src/agent_learning_coach/core/agent/errors.py`

循环形状是「模型 → 工具 → 结果回填 → 模型」。模型调用委托给 `LLMClient`，工具执行委托给 `ToolDispatcher`，因此换模型或换工具来源都不需要改运行时。

**Execution Context** 承载一次执行的全部状态：执行身份、事件历史、步数、草稿本、用量、成本、最终产物。运行时不另设一份状态，因此「运行发生了什么」只有一个答案——上下文里的事件序列。这让运行可测试、可复现、可续接。

**成本控制**有两个维度：token 预算（约束实际消耗而非步数）与成本预算（金额）。价格表由调用方提供——价格会变、随厂商与地区不同，运行时内置一份只会制造一个必然过期的真相来源。预算无法执行（模型名无从确定或价格表未覆盖）属配置错误，直接抛出。

停止策略分三层：**自然结束是主路径**（模型不再请求工具即结束，只有模型自己知道任务是否完成）；**失败回填而非终止**（工具出错一律归一化成错误结果回填，让模型换参数、换工具或直接回答）；**预算是兜底**（步数、工具调用数、连续失败数、重复调用数四个阈值只在模型不再前进时生效，保证失控的循环一定会停下来）。

对话记录保持一条不变量：**每条 `tool_call` 都有对应的 `tool_result`**——因此结果可直接作为下一轮对话输入继续。

决策依据：[ADR-0005](decisions/0005-agent-loop.md)、[ADR-0006](decisions/0006-execution-context-and-cost-control.md)。
接入说明：[运行时接入说明](../engineering/runtime.md)。

#### 计划与反思

计划与反思位于 `src/agent_learning_coach/core/agent/planning.py`，与运行时同包：

- 计划类型：`Plan` / `PlanTask` / `PlanRevision` / `Reflection`
- 写入接口：`PlanManager`——建计划、改计划、记反思
- 规划器：`Planner` 协议 + `OutcomePlanner`（确定性拆解）/ `LLMPlanner`（模型拆解）
- 工具：`update_plan`（提交增量修改）/ `reflect`（记录判断）

**计划是带状态与历史的对象，不是一段可被整体覆盖的文本**。任务有稳定 id、内容不可改写、没有删除操作（移除 = 标成 `skipped`）、同一时刻只有一个进行中的任务、依赖必须闭合且无环。每次修改都要有理由，并产生一个修订；结构性修改（新增 / 跳过 / 重开 / 改依赖）消耗重规划预算，普通推进不消耗。

**反思不改计划**：反思只产出判断；改计划是另一步，且必须引用那条反思。于是「为什么改」永远有出处，改的次数也有上限。

计划存在执行上下文的草稿本里，因此随会话快照一起持久化，也随会话恢复一起回来。

领域边界：本模块不认识学习领域的具体概念，只认识 `GoalSpec`。Phase 1 的 `LearningGoal` 领域模型映射成 `GoalSpec` 后交给规划器——**领域模型回答「目标是什么」，运行时回答「目标怎么变成任务」**。

决策依据：[ADR-0013](decisions/0013-planning-and-reflection.md)。
接入说明：[计划与反思接入说明](../engineering/planning.md)。

#### 可观测性与评估

可观测性与评估位于 `src/agent_learning_coach/core/agent/` 下两个模块，与运行时同包：

- Trace / Metrics / 诊断：`src/agent_learning_coach/core/agent/observability.py`
  - `RunTrace` / `StepTrace` / `ToolSpan`：从 `AgentResult` **投影**出运行轨迹
  - `compute_metrics`：成功率、停止原因分布、工具失败率、步数与耗时百分位、token 与成本
  - `diagnose`：停止原因 → 检查方向 + 证据（失败工具、重复动作）
- 行为评估：`src/agent_learning_coach/core/agent/evaluation.py`
  - `EvalCase` / `ScriptedTurn`：用例是数据（JSON 数据集 `evals/phase0.json`）
  - `EvalHarness` / `EvalReport`：在固定工具集上确定性执行并逐条断言
  - `ScriptedLLMClient`：确定性驱动器，测试复用同一实现

**Trace 是投影，不是第二套事件系统**：事实只有一份（执行上下文的事件、步记录、工具结果）。唯一新增的插桩是 `AgentStep.think_ms`（模型调用耗时无法从已有事实推导），工具耗时沿用 `ToolOutcome.duration_ms`。

**评估断言行为，不断言措辞**：工具调用序列、失败归一化、停止原因、输出交付都是可精确断言的行为契约；语义质量评估推迟到 Phase 2，因为判官不可回归、不能当门禁。

决策依据：[ADR-0014](decisions/0014-observability-and-evaluation.md)。
接入说明：[可靠性（Reliability）](../engineering/reliability.md)、[评估（Evaluation）](../engineering/evaluation.md)。

#### MCP 协议适配

工具系统与 MCP 协议双向适配，位于 `src/agent_learning_coach/core/tools/mcp.py`：

- 客户端侧（生产组件）：`MCPTool` / `discover_mcp_tools` 把远端 MCP Server 的工具适配成本项目的 `Tool`，注册进 `ToolRegistry`，复用现有 `ToolDispatcher` 的校验、超时与归一化。
- 服务端侧（PoC 与协议参考实现）：`build_mcp_server` 把工具注册表暴露为 MCP Server；可运行示例见 `examples/mcp_server.py`。

本项目的 `ToolDefinition`（名称 + 描述 + JSON Schema）与 MCP 的工具形状同构，因此适配是三字段映射。适配器停留在协议层（`mcp` SDK 低层 `Server` / `ClientSession`），不依赖框架封装，以保证 JSON Schema 保真——「模型看到的契约」与「实际校验的契约」必须是同一份。

适配器遵守一条硬规则：**发现与结果回填不得静默丢失信息**。远端 `tools/list` 分页必须翻完；结构化输出与非文本内容块必须保留而非变成空字符串；远端声明的输出契约（`output_schema` 等）在发现阶段不得丢弃。判据是空内容与「工具确实没有输出」无法区分——若吞成空串，模型会误判工具行为。连接建立阶段的失败（命令不存在、握手超时、远端进程退出）统一收敛为 `MCPConnectionError`，并把 SDK 任务组包起来的真实原因摊平到错误消息里。

本项目自有工具不部署为 MCP Server：MCP 的价值在于独立部署与跨客户端复用，而自有工具与 Agent Loop 同属一个进程、同一团队。第三方 MCP Server 的消费才是当前的实际需求。

决策依据：[ADR-0004](decisions/0004-mcp-integration.md)。
接入说明：[MCP 架构说明](../engineering/mcp.md)。

#### RAG

检索增强生成由 `core/rag` 包提供，与 `core/llm`、`core/tools`、`core/agent` 平级：

- 核心类型：`src/agent_learning_coach/core/rag/types.py`
  - `Document`：摄取单位（一份材料），带来源与元数据
  - `Chunk`：检索单位（材料的一段），**必须能回溯到来源**
  - `ScoredChunk`：切片 + 相关度
- 文档加载：`src/agent_learning_coach/core/rag/loader.py`（`TextFileLoader` / `DirectoryLoader`）
- 切片：`src/agent_learning_coach/core/rag/chunking.py`（`FixedSizeChunker` 定长 / `RecursiveChunker` 按结构递归）
- 嵌入：`src/agent_learning_coach/core/rag/embedding.py`（`HashingEmbedder` 离线 / `OpenAICompatibleEmbedder` 生产）
- 向量存储：`src/agent_learning_coach/core/rag/store.py`（`InMemoryVectorStore` / `PgVectorStore`）
- 检索：`src/agent_learning_coach/core/rag/retrieval.py`（`VectorRetriever`）
- 摄取：`src/agent_learning_coach/core/rag/ingestion.py`（`ingest_directory`，幂等）
- 检索工具：`src/agent_learning_coach/core/rag/tools.py`
  - `SearchCourseMaterialTool`：语义检索——「哪些内容与问题相关」
  - `RetrieveKnowledgeTool`：结构化检索——「这份材料里有什么」
- 引用：`src/agent_learning_coach/core/rag/citation.py`（`Citation` /
  `CitationSet` / `CitationValidator`）——引用绑定到**具体切片**，标记稳定，
  且**可校验**（编造的引用被判为错误）
- 检索评估：`src/agent_learning_coach/core/rag/evaluation.py`（命中率@k、MRR，
  按类别分组）
- 工具回调：`src/agent_learning_coach/core/rag/callbacks.py`（检索缓存、结果
  压缩）

流水线分四段，每段一个 Protocol，各配两个实现——**一个离线可测，一个能上生产**：

```
DocumentLoader → Chunker → EmbeddingClient → VectorStore
   读成文档        切成检索单元    变成向量        存下来
                                     ↓
                              VectorRetriever
```

三条硬约束：

1. **文档与切片 id 必须可复现**——由来源哈希推导，重新摄取不产生重复记录。
2. **过滤必须下推到存储层**——在应用层过滤等于把全部向量拉回内存再筛，向量库退化成一个 JSON 文件。
3. **对外统一暴露相似度**——pgvector 的 `<=>` 返回的是距离（方向相反），实现负责换算；两种存储的相对顺序一致，这是可替换的前提。

依赖方向：`core/rag` 只依赖 `core/llm` 的错误类型与 `core/text` 的分词，不认识 `core/agent`。

决策依据：[ADR-0011](decisions/0011-rag-pipeline-and-vector-store.md)、
[ADR-0012](decisions/0012-tool-callbacks-citation-and-hitl.md)。
接入说明：[RAG 接入说明](../engineering/rag.md)。

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
| 向量数据库 | PostgreSQL + pgvector | 已确定 |
| 自动化测试 | pytest | 已确定 |
| 代码检查与格式化 | Ruff | 已确定 |
| 静态类型检查 | mypy | 已确定 |
| API 框架 | TBD | 待确定 |
| 关系型数据库 | PostgreSQL | 计划采用 |
| 可观测性 | 内置 Trace / Metrics，OpenTelemetry 待接入 | 已确定（ADR-0014） |

注：技术选型会随着工程实践持续验证和调整。依赖库已经引入，不代表对应技术方案已经成为最终架构决策。

`chromadb` 仍在依赖列表里，但它是早期从参考项目带过来的声明，**本项目的向量存储不基于它**——路线是 PostgreSQL + pgvector（ADR-0011）。`pymupdf`、`e2b-code-interpreter`、`tavily-python` 同样是给后续 Topic 预留的。

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
- 学习领域接入说明：`../engineering/domain.md`
- 计划与反思接入说明：`../engineering/planning.md`
