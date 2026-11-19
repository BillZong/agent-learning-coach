# Topic: Execution Context 与 Runtime

> Day 6 ｜ Phase 0 · Agent Engineering Foundation ｜ 主教材：Ch4：4.3～4.5、4.7 ｜ 外部参考：OpenAI Agents SDK 文档 ｜ 状态：已完成

## 1. Engineering Question

Execution Context 需要承载哪些状态，才能让 Agent Loop 可测试、可复现？

答：**执行身份、事件历史、步数、草稿本、用量与成本、最终产物**。

前四项让运行「可复现」——给定同样的输入与事件序列就能得到同样的结论；后两项
让运行「可核算」；事件比扁平对话列表多出的「归属」与「时刻」让历史可审计、
可回放。

## 2. Primary Reading

- Book: Ch4：4.3～4.5、4.7
- Chapter / Section: Ch4 4.3～4.5、4.7
- 核心观点：

## 3. Cross References

| 来源 | 核心观点 | 与主教材的关系 |
|---|---|---|
| OpenAI Agents SDK 文档 | | |
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
  - `core/agent/` 从「Agent Loop」升级为「Agent Runtime」：新增 `ExecutionContext`、`Event`、`AgentResult`、`TokenPrice`、`AgentConfigError`
  - 命名统一：`AgentLoop` → `AgentRuntime`，`AgentRun` → `AgentResult`（不保留并行词汇）
  - 新增 `examples/agent_runtime.py`：基础 Agent Runtime 交付物
  - 新增 `tests/test_execution_context.py`（17 例）
- Execution Context
  - 承载：执行身份 `execution_id`、事件历史 `events`、步数 `current_step`、草稿本 `state`、用量 `usage`、成本 `cost`、最终产物 `final_result`
  - 对话内容不再单独存一份——由事件历史展平而来（`context.contents`），保持「每条 tool_call 都有对应 tool_result」的不变量
  - `Event` 携带归属与时刻：谁说的、说了什么、什么时候说的——这是它与扁平对话列表的区别
  - 传入已有上下文即可**续接对话**
- LLM Adapter 集成
  - 运行时只认 `LLMClient` 协议；厂商差异仍由 `core/llm` 的适配器隔离
  - 成本核算依赖响应里的 `usage` 与模型名——真实客户端都会上报，测试替身必须照做
- 结构化 Agent Output
  - 未做：`AgentResult.output` 是文本。结构化输出由 `core/llm/structured.py` 的 `generate_structured` 承担，不在运行时层重复实现
- Runtime State（草稿本）
  - `context.state` 服务于工具与应用层，**模型看不到**
  - 工具通过 `ToolContext.metadata["execution_context"]` 取到执行上下文；用字符串键而不在 `ToolContext` 上加字段，避免 `core/tools` 反向依赖 `core/agent`
- Cost Control
  - `max_total_tokens` 约束实际消耗而非步数；`max_cost` 是金额预算
  - 价格表由调用方提供（`AgentConfig.prices`）：价格会变、随厂商与地区不同，写死等于制造一个必然过期的真相来源
  - 预算无法执行就是配置错误：设了成本上限却无法确定模型、或价格表未覆盖实际模型，抛 `AgentConfigError`
- 为什么这样设计？
  - **状态收进 Context，运行时不另设一份**：「运行发生了什么」只有一个答案
  - **结果携带 Context**：「结论」与「过程」不分离，调用方不必另外保存历史
  - **草稿本不进模型上下文**：模型看到的是事实，不是内部状态
  - **价格表外置**：内置一份只会制造一个必然过期、且过期时无人收到通知的真相
- 有哪些替代方案？
  - 状态直接放在 `AgentRuntime` 实例上：多次运行会互相污染，且无法并行
  - 沿用扁平对话列表 + 额外传一个 dict：没有归属与时刻，历史不可审计
  - 内置价格表：价格会变，且内置的那份过期时无人收到通知
  - 预算无法执行时静默按 0 计费：比不设预算更危险——用户以为有限额
- 测试结果如何？
  `pytest` 259 passed；`core/agent` 全包行覆盖与分支覆盖均 100%。
  `ruff format --check` / `ruff check` / `mypy` 全部通过。
- 实践结果是否支持阅读材料中的观点？
  - 支持「Execution Context 是可测试性与可复现性的载体」：把状态收进上下文后，运行可以只靠「输入 + 事件序列」复现，与模型调用次数无关。
  - 反面教材：以为「设了成本上限就等于有成本控制」。初版在价格表缺失时静默按 0 计费，结果是**完全没有约束**——比不设更危险，因为用户以为有限额。改为直接抛配置错误。
  - 反面教材：以为「客户端返回什么就有什么」。脚本化的模型响应若不带 `usage`，用量与成本都是 0，而测试会在这上面假绿。成本核算依赖客户端上报 usage 与模型名——真实客户端都会上报，但测试替身必须照做。

## 6. Reflection

1. 今天最重要的知识是什么？
2. 哪个观点改变或修正了原来的理解？
3. 当前实现还有哪些局限？
   `state` 不持久化（进程重启即丢）；没有跨进程状态共享；事件不压缩（长会话的上下文膨胀仍未解决，属 Topic 08）；成本只按 token 估算，不含缓存命中、批处理折扣等厂商计费细节；结果对象携带可变上下文，拿到后上下文仍可能被修改。

## 7. Output

- Code / PR: `src/agent_learning_coach/core/agent/`（`runtime.py` / `types.py` / `cost.py` / `errors.py`）、`examples/agent_runtime.py`
- Tests: `tests/test_execution_context.py`、`tests/test_runtime.py`、`tests/test_runtime_mcp.py`
- ADR: `docs/architecture/decisions/0006-execution-context-and-cost-control.md`
- Article Notes: `docs/engineering/runtime.md`
- Unresolved Questions: 上下文多长之后必须压缩、压缩策略由谁决定；`state` 是否需要跨进程持久化；成本核算要不要覆盖缓存命中与批处理折扣；事件流如何支持回放与断点续跑。
