# 可靠性（Reliability）

> 状态：草稿 ｜ 对应 Topic 14 ｜ 决策依据：ADR-0005、0006、0009、0014

可靠性在这个项目里不是「加个 try/except」，而是四件分开的事：**失败被归一化、
预算一定生效、状态可以恢复、失败能被看见**。本文按这四件事组织。

## 1. 失败模式分类

运行时的失败先按「谁的责任」分层，再决定处置方式。分类的好处是：同一条
停止原因永远对应同一种处置，不需要在故障现场临场判断。

| 停止原因 | 根因层 | 表现 | 处置 |
| ---- | ---- | ---- | ---- |
| `completed` | — | 模型不再请求工具 | 唯一成功路径 |
| `max_steps` | 模型行为 | 一直在请求工具，没有结论 | 看任务是否拆得太碎、工具是否没给出需要的信息 |
| `max_tool_calls` | 模型行为 | 预算内没有收敛 | 看是否反复重试同一工具 |
| `max_tokens` / `max_cost` | 上下文 / 输出 | 消耗超预算 | 看是否把整份材料塞进 prompt |
| `consecutive_errors` | 工具 / 环境 | 连续失败，模型没恢复 | 看失败工具与错误信息（参数？环境？工具本身？） |
| `repeated_call` | 模型行为 | 同一动作反复请求 | 看工具结果是否真的推进了任务 |
| `timeout` | 模型 / 工具 | 整体截止时间到 | 对照 `think_ms` 与工具耗时定位慢在哪 |
| `llm_error` | 供应商 | 模型调用失败 | 不重试，由调用方决定是否重跑 |
| `pending_approval` | 人工 | 高风险工具等待批准 | 不是失败；`resume()` 后继续 |

调用级错误（工具不存在、参数不合法、工具抛异常、超时）**不进这张表**：它们被
`ToolDispatcher` 归一化成 `ToolOutcome(status="error")` 回填给模型，让模型
自愈。只有「模型自己也修不好」的情况才上升到停止原因。

`diagnose(trace)` 直接读这张表：停止原因 → 检查方向，证据从 Trace 里取。

## 2. 重试与超时

**模型调用默认不重试。** 理由：重试是调用方的决定，因为它知道这次运行值不值
得重跑——一个已经消耗了三步与两万 token 的运行，重跑的成本可能比失败更高。
运行时只保证**部分结果不丢失**：失败时照常返回已完成的事件、步数与用量。

**超时有两层**：

- 运行级（`AgentConfig.timeout`）：覆盖模型调用与工具执行在内的全部时间，到点
  抛 `TimeoutError`，运行以 `stop_reason="timeout"` 结束。
- 工具级（`ToolDefinition.timeout` / `ToolDispatcher.default_timeout`）：单次
  工具执行的上限，用 `asyncio.timeout` 实现（会真正取消挂起的协程，而不是
  放弃等待）。

**重试工具还是重试运行**：工具失败先由模型决定（换参数、换工具、直接回答）；
模型也修不好时运行才停。应用层可以基于 `stop_reason` 决定是否整轮重跑。

## 3. 降级与熔断

预算与停止条件承担熔断的角色，但它们的定位是**兜底**，不是主路径：

- 六个阈值：步数、工具调用数、token、成本、连续失败、重复调用。
- 前四个在**步之间**检查，当前步的调用全部执行完——保证每条 `tool_call` 都有
  对应的 `tool_result`，否则转录断裂，多数厂商 API 会拒绝后续请求。
- 后两个在步之后检查，指向**模型行为**：连续失败＝陷入同一个错误，重复调用＝
  原地打转。因此它们比 `max_steps` 更具体，优先上报。

计数住在 `ExecutionContext` 里，不是循环的局部变量——否则暂停-恢复可以重置
它们，从而绕过停止条件。

**人工确认是 fail-closed**：需要确认的工具会让整个运行暂停，本步的工具调用
**一个都不执行**；没有决定的调用按拒绝处理。不存在「超时自动放行」的路径。

## 4. 幂等与状态恢复

**框架不保证工具幂等。** 工具作者要自己保证外部副作用可重放（或让副作用在
重放时可识别），框架只保证：

- 一次运行内，每条 `ToolCall` 恰好对应一条 `ToolResult`；
- 暂停-恢复不会重复执行已完成的调用（待确认的调用在 `resume()` 时执行）；
- 会话检查点（`SessionContext.checkpoint()`）包含恢复所需的全部状态：事件、
  草稿本、用量与成本、步数、循环计数、待确认审批。

恢复 = 从检查点重建上下文，然后继续；不是「重跑」。少存一样，恢复出来的就
不是原来那个运行（ADR-0009）。

**一次运行不是原子的**：中途失败会留下部分结果。断点续跑需要可回放的状态机，
目前没有——这是已知边界。

## 5. 可观测性

代码入口：`agent_learning_coach.core.agent` 的 `build_trace` / `compute_metrics` /
`diagnose`。完整示例：`examples/reliability_report.py`。

**Trace 是投影**：`build_trace(result)` 只读运行结果。事实只有一份（事件、
步记录、工具结果），所以不存在「两份日志对不上」的问题。模型耗时来自
`AgentStep.think_ms`，工具耗时来自 `ToolOutcome.duration_ms`，运行耗时来自
第一个与最后一个事件的时间戳（因预算停止的运行是近似值）。

**Metrics 是聚合**：

```python
metrics = compute_metrics(build_trace(result) for result in results)
metrics.summary()
# 3 次运行：成功 2（67%）；停止原因 completed 2、repeated_call 1；…
```

**诊断只给证据**：

```
停止原因：repeated_call
模型在原地打转：同一个动作被反复请求。…
证据：
- echo 被调用 3 次
```

指标用来**比较**（改动前后），不用来**宣称**（系统很可靠）。十次以下的运行，
p95 基本等于最大值。

不引入 OpenTelemetry 的理由与接入顺序见 [ADR-0014](../architecture/decisions/0014-observability-and-evaluation.md)。

## 6. 已知限制

| 限制 | 说明 |
| ---- | ---- |
| 无分布式追踪 | 跨服务、跨进程（MCP 子进程内部）调用不可见 |
| 无自动重试 | 重试策略完全由调用方决定，框架只保证部分结果不丢 |
| 无断点续跑 | 运行失败后只能从会话检查点重来，不能从失败的那一步继续 |
| 无告警阈值 | 指标需要先积累基线，阈值不在框架内 |
| 工具内部行为不可见 | 重试次数、缓存命中等只能由工具写进 `ToolOutcome.metadata` |

## 相关文档

- 运行时：[运行时接入说明](runtime.md)
- 会话与恢复：[会话与持久化](session.md)、[ADR-0009](../architecture/decisions/0009-session-persistence-and-hitl.md)
- 安全：[安全（Security）](security.md)、[威胁模型](threat-model.md)
- 评估：[评估（Evaluation）](evaluation.md)、[ADR-0014](../architecture/decisions/0014-observability-and-evaluation.md)
- 学习笔记：[Topic 14 · 可靠性与阶段验收](../learning/topics/14-reliability-and-phase-acceptance.md)
