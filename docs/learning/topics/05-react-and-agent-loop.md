# Topic: ReAct 与 Agent Loop

> Day 5 ｜ Phase 0 · Agent Engineering Foundation ｜ 主教材：Ch4：4.1～4.2、4.6 ｜ 外部参考：Anthropic Workflow Patterns ｜ 状态：已完成

## 1. Engineering Question

ReAct 循环在什么条件下必须停止？Stop Condition 与 Max Steps 如何配合，既避免死循环，也避免过早收敛？

## 2. Primary Reading

- Book: Ch4：4.1～4.2、4.6
- Chapter / Section: Ch4 4.1～4.2、4.6
- 核心观点：

## 3. Cross References

| 来源 | 核心观点 | 与主教材的关系 |
|---|---|---|
| Anthropic Workflow Patterns | | |
| 官方工程指南 | | |
| 框架 / 协议文档 | | |
| 论文 / 研究资料 | | |

## 4. Compare and Challenge

- 不同资料有哪些一致观点？
- 哪些观点存在分歧？
- 分歧来自应用场景、设计目标还是技术演进？
- 哪些结论缺少充分证据？
- 哪些观点不适用于当前项目？

## 5. Engineering Practice

- 实现了什么？
  - 新增 `src/agent_learning_coach/core/agent/` 包（与 `core/llm`、`core/tools` 平级）：
    - `loop.py`：`AgentLoop`——ReAct 循环控制与停止条件
    - `types.py`：`AgentConfig` / `AgentStep` / `AgentRun` / `StopReason`
  - 循环形状：`Think`（模型） -> `Act`（工具） -> `Observe`（结果回填） -> `Think` ...
- Think / Act / Observe
  - `Think`：`AgentLoop._think` 构造 `LLMRequest`（对话 + `registry.specs()` 工具清单）交给 `LLMClient`
  - `Act`：`ToolDispatcher.dispatch_safe` 执行模型请求的工具调用——参数校验、超时、结果归一化全部复用工具系统，循环不重复实现
  - `Observe`：`ToolOutcome.to_message_result` 转成 `ToolResult` 回填进对话记录
- Stop Condition
  - 六种停止原因，分三层：**自然结束**（模型不再请求工具，唯一成功路径）、**模型不再前进**（`consecutive_errors` / `repeated_call`）、**预算兜底**（`max_steps` / `max_tool_calls` / `timeout`）
  - 更具体的停止原因优先：`repeated_call` 指向模型行为，`max_steps` 只是预算耗尽
- Max Steps
  - `AgentConfig.max_steps` 是步数硬上限（默认 12）；`max_tool_calls` 是工具调用总预算，**在步之间检查**——当前步内的调用全部执行完，保证每条 `tool_call` 都有对应 `tool_result`
  - 额外加一个 `max_repeated_calls`：检测「同一工具同一参数反复调用」，补上「步数用完才停」的时间浪费
- 为什么这样设计？
  - **循环独立成包**：放进 `core/llm` 会让模型客户端不再纯粹，放进 `core/tools` 则让工具系统知道模型的存在。独立后换模型或换工具来源（本地 / MCP）都不需要改循环
  - **失败回填而非终止**：工具出错一律归一化成错误结果回填给模型，让它换参数、换工具或直接回答。模型能自愈的错误就让它自愈——这是「过早收敛」的主要防护
  - **预算是兜底不是目标**：只有模型自己知道任务是否完成，任何预算都不该替代这个判断
  - **对话记录不变量**：每条 `tool_call` 都有对应 `tool_result`，因此结果可直接作为下一轮对话输入继续
- 有哪些替代方案？
  - 放进 `core/llm/`：与模型客户端同层，但 `core/llm/` 是纯「模型调用」职责，循环引入会破坏它的纯粹性
  - 放进 `core/tools/`：不推荐——工具系统不该知道模型的存在
  - 工具失败直接抛异常终止（参考项目 `ai-agent-from-scratch` 的做法）：简单，但把模型能自愈的错误变成了运行失败
  - 只用 `max_steps` 一个停止条件：够用但不够快——模型可能用满 12 步反复调同一个工具
- 测试结果如何？
  `pytest` 228 passed；`core/agent` 行覆盖与分支覆盖均 100%。
  `ruff format --check` / `ruff check` / `mypy` 全部通过。
  变异验证：分别破坏「不回填工具结果」「去掉重复调用检测」「工具失败直接终止」三处实现，对应测试分别失败 6 / 2 / 5 条——测试确实咬得住行为，而不是只走过场。
- 实践结果是否支持阅读材料中的观点？
  - 支持「Stop Condition 与 Max Steps 要配合」：单靠 Max Steps 只能保证「会停」，不能保证「及时停」；单靠 Stop Condition 又无法兜底病态模型。两者配合才既避免死循环也避免过早收敛。
  - 修正一处直觉：最初以为「工具不存在」是配置错误、该终止；实测把错误回填后，模型会自行改用其它工具并正常完成。终止反而更早地放弃了一个模型能纠正的错误。
  - 反面教材：停止原因的**优先级**必须显式定义。初版里「步数用完」与「原地打转」同时命中时，测试断言失败暴露了歧义——规则定为「更具体者优先」并补了测试。

## 6. Reflection

1. 今天最重要的知识是什么？
2. 哪个观点改变或修正了原来的理解？
3. 当前实现还有哪些局限？
   工具顺序执行不并发（并发需处理取消传播、超时传播与结果顺序的确定性）；上下文不裁剪（记录原样返回，压缩由调用方决定）；模型调用失败向上抛出不重试；一次运行是原子的，无中断与恢复的检查点。

## 7. Output

- Code / PR: `src/agent_learning_coach/core/agent/loop.py`、`src/agent_learning_coach/core/agent/types.py`
- Tests: `tests/test_agent_loop.py`
- ADR: `docs/architecture/decisions/0005-agent-loop.md`
- Article Notes: `docs/engineering/runtime.md`
- Unresolved Questions: 并发执行工具调用时，取消与超时如何传播、结果顺序如何保证确定性；上下文何时裁剪、裁剪策略由谁决定；循环中断后如何留下可续接的检查点。
