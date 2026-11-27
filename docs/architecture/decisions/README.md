# ADR 索引

本目录存放架构决策记录（Architecture Decision Records），文件名格式：`NNNN-简短标题.md`。

模板：

```markdown
# ADR-NNNN: 标题

- 状态：提议 / 已接受 / 已废弃
- 日期：YYYY-MM-DD

## 背景

## 决策

## 后果
```

| ADR | 标题 | 状态 |
| --- | ---- | ---- |
| [0001](0001-init-architecture.md) | 初始架构 | 提议 |
| [0002](0002-llm-client-protocol.md) | LLM Client 协议 | 已接受 |
| [0003](0003-tool-system.md) | 工具系统 | 已接受 |
| [0004](0004-mcp-integration.md) | MCP 集成 | 已接受 |
| [0005](0005-agent-loop.md) | Agent Loop | 已接受 |
| [0006](0006-execution-context-and-cost-control.md) | 执行上下文与成本控制 | 已接受 |
| [0007](0007-tool-permission-and-sandbox-boundary.md) | 工具权限与沙箱边界 | 已接受 |
| [0008](0008-context-storage-and-presentation.md) | 上下文存储与展示 | 已接受 |
| [0009](0009-session-persistence-and-hitl.md) | 会话持久化与 HITL | 已接受 |
| [0010](0010-long-term-memory-admission-and-overwrite.md) | 长期记忆准入与覆盖 | 已接受 |
| [0011](0011-rag-pipeline-and-vector-store.md) | RAG 流水线与向量存储 | 已接受 |
| [0012](0012-tool-callbacks-citation-and-hitl.md) | 工具回调、Citation 与 HITL | 已接受 |
| [0013](0013-planning-and-reflection.md) | 计划与反思 | 已接受 |
| [0014](0014-observability-and-evaluation.md) | 可观测性与评估的边界 | 已接受 |
| [0015](0015-learning-domain-boundaries.md) | 学习领域三聚合边界 | 已接受 |
| [0016](0016-knowledge-representation.md) | 知识表示 | 已接受 |
| [0017](0017-learning-goal-modeling.md) | 学习目标的建模 | 已接受 |
| [0018](0018-learning-progress.md) | 学习进度 | 已接受 |
| [0019](0019-domain-review.md) | 领域模型评审（Phase 1 收口） | 已接受 |
| [0020](0020-diagnostic-assessment.md) | 诊断——判定、准入条件与与进度的衔接 | 已接受 |
| [0021](0021-structured-generation.md) | 诊断题生成——两层校验、拦截与重试 | 已接受 |
| [0022](0022-structured-errors.md) | 结构化错误——原始错误是事实依据，摘要是理解入口 | 已接受 |
| [0023](0023-diagnostic-assessment.md) | 诊断评估——映射、汇总与降级 | 已接受 |
| [0024](0024-learning-planning.md) | 学习计划——粒度、范围与时间约束 | 已接受 |
| [0025](0025-plan-versioning.md) | 计划版本——修订与全新计划、查询规则与生成依据 | 已接受 |
| [0026](0026-persistence-baseline.md) | 持久化基线——形状、乐观锁与存储粒度 | 已接受 |
| [0027](0027-application-services.md) | 应用层用例编排——顺序、冲突与写边界 | 已接受 |
| [0028](0028-document-ingestion.md) | 课程文档摄取——格式、部分成功与幂等 | 已接受 |
| [0029](0029-chunking-strategy.md) | 切分策略——按材料类型选，以及参数如何被测试锁定 | 已接受 |
| [0030](0030-retrieval-filtering-and-index.md) | 检索过滤、top-k 与阈值的归属、索引方案的选择 | 已接受 |
| [0031](0031-retrieval-evaluation.md) | 检索评估——评估集、命中率与召回、据此调整检索 | 已接受 |
| [0032](0032-reranking-and-query-rewriting.md) | 重排、查询改写与多路召回——研究过了，现在不做 | 已接受 |
| [0033](0033-citation-grounding-gate.md) | 引用支撑的处置——门禁放在应用层，评估用词面口径 | 已接受 |
