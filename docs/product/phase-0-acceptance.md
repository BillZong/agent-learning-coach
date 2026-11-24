# Phase 0 阶段验收（Agent Engineering Foundation）

> 日期：2026-11-24 ｜ 阶段：Phase 0（Week 1–2） ｜ 结论：**通过**

本文把路线图的验收标准逐条映射到**可执行的证据**。判据只有一条：
**能在仓库里跑出来的，才算证据**；文档描述只作补充。

Phase 0 覆盖 MVP（[`mvp.md`](./mvp.md)）的 M1 部分——运行时、可控执行与可观测；
M2 部分（学习领域与计划）在 Phase 1 验收。

## 1. 验收标准

| # | 标准 | 证据（可执行） | 结论 |
| ---- | ---- | ---- | ---- |
| 1 | Agent 能够理解用户输入并完成多轮交互 | `tests/test_session.py`、`tests/test_execution_context.py`；`examples/agent_runtime.py` | 通过 |
| 2 | 能够根据任务选择并调用工具 | `tests/test_tool_system.py`、`tests/test_mcp_tools.py`；`examples/basic_react_agent.py`、`examples/agent_loop_with_mcp.py`；评估用例「一次工具调用后回答」 | 通过 |
| 3 | 能够管理执行状态和 Session | `tests/test_execution_context.py`、`tests/test_session.py`（检查点、恢复、JSON 文件存储）；ADR-0009 | 通过 |
| 4 | 工具执行异常能够得到合理处理 | `tests/test_tool_system.py`、`tests/test_runtime.py`；评估用例「工具失败被归一化后自愈」「未知工具不炸掉运行」「连续工具失败被识别」 | 通过 |
| 5 | 关键执行过程能够追踪 | `tests/test_observability.py`；`uv run python examples/reliability_report.py`；ADR-0014 | 通过 |
| 6 | 具备基本的工具安全控制 | `tests/test_runtime_security.py`、`tests/test_hitl.py`；`examples/hitl_approval.py`；ADR-0007 | 通过 |
| 7 | 能够通过评估用例验证基础 Agent 行为 | `tests/test_evaluation.py`；`evals/phase0.json` **7/7 通过** | 通过 |

## 2. 交付物

| 交付物 | 位置 | 状态 |
| ---- | ---- | ---- |
| 可运行的基础 Agent Runtime | `core/agent/runtime.py`、`examples/agent_runtime.py` | 完成 |
| LLM 调用抽象 | `core/llm/`（Protocol + Provider 注册表 + 适配器）、ADR-0002 | 完成 |
| Tool Calling 与工具执行机制 | `core/tools/`、ADR-0003 | 完成 |
| Agent State 与 Session 管理 | `core/agent/types.py`、`core/agent/session.py`、ADR-0009 | 完成 |
| 基础 Memory 与 RAG 能力 | `core/memory/`、`core/rag/`、ADR-0010、ADR-0011 | 完成 |
| 工具权限与安全策略 | `core/tools/permissions.py`、ADR-0007 | 完成 |
| 基础执行追踪能力 | `core/agent/observability.py`、ADR-0014 | 完成 |
| 基础 Agent Evaluation | `core/agent/evaluation.py`、`evals/phase0.json`、ADR-0014 | 完成 |
| Agent Runtime Architecture 文档 | `docs/architecture/architecture.md` | 完成 |
| 关键架构决策记录（ADR） | `docs/architecture/decisions/`（0001–0014） | 完成 |

## 3. 工程质量指标

| 指标 | 数值 | 说明 |
| ---- | ---- | ---- |
| 测试 | **594 passed** | `uv run pytest`；冒烟测试之外全部是行为测试 |
| 静态检查 | ruff + mypy 全绿 | `make check` |
| 学习主题 | 14 篇 | `docs/learning/topics/` |
| ADR | 14 篇 | `docs/architecture/decisions/` |
| 可运行示例 | 11 个 | `examples/`，全部无凭证可跑 |
| 评估用例 | 7 条 | `evals/phase0.json`，覆盖成功与四类失败路径 |

## 4. 未覆盖的边界（不假装完成）

这是验收里最容易被含糊过去的部分，因此单列：

| 边界 | 现状 | 影响 |
| ---- | ---- | ---- |
| 语义质量评估 | 未覆盖 | 回答是否讲清楚、是否有教学价值，现有机制答不上来；推迟到 Phase 2 |
| 真实模型行为回归 | 未覆盖 | 评估集用脚本化模型验机制；真实模型需要抽样与容差，目前没有基线 |
| 分布式追踪 | 未覆盖 | MCP 子进程内部不可见；没有跨进程/跨服务关联 |
| 告警阈值 | 未覆盖 | 指标已具备，阈值需要先积累基线 |
| 上下文预算裁剪（计划） | 未覆盖 | 计划渲染后由应用层放进 prompt，没有裁剪 |
| 运行断点续跑 | 未覆盖 | 一次运行不是原子的；失败后只能从会话检查点重来 |

这些边界已写入 [可靠性（Reliability）](../engineering/reliability.md#6-已知限制) 与
[ADR-0014](../architecture/decisions/0014-observability-and-evaluation.md)。

## 5. 进入 Phase 1 的输入

1. **领域模型接上运行时契约**：Phase 1 的 `LearningGoal` 映射成 `GoalSpec`，
   规划器不认识学习领域的具体概念。
2. **语义质量评估需要锚点**：Phase 2 之前要先有真实教学场景与人工标注，否则
   判官没有可校准的标准。
3. **积累指标基线**：成功率、步数分布、模型耗时需要在真实任务上积累，才能定
   告警阈值。

## 相关文档

- 路线图：[Roadmap](../product/roadmap.md)
- 架构：[系统架构](../architecture/architecture.md)
- 可靠性：[可靠性（Reliability）](../engineering/reliability.md)
- 评估：[评估（Evaluation）](../engineering/evaluation.md)
- 学习笔记：[Topic 14 · 可靠性与阶段验收](../learning/topics/14-reliability-and-phase-acceptance.md)
