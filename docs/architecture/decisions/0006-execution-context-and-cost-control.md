# ADR-0006: Execution Context、Runtime State 与成本控制

- 状态：已接受
- 日期：2026-11-19

## 背景

Topic 05 交付了 Agent Loop，但当时运行过程中只有一条扁平的对话记录：

```python
contents: list[ContentItem]
```

Topic 06 提出的工程问题是：**Execution Context 需要承载哪些状态，才能让
Agent Loop 可测试、可复现？**

具体要解决的：

1. 「发生了什么」只有对话内容——没有谁说的、什么时候说的，无法审计也无法
   回放。
2. 运行中间状态无处安放——工具查到的信息、模型算出的中间结果，只能挤在
   对话里或者干脆丢失。
3. 除了步数，没有任何消耗维度的约束。12 步的预算对「简短回答」和「逐字
   输出长文档」是同一个数字，实际成本可能差两个数量级。
4. 没有跨运行的对话续接能力——一次运行结束，对话记录只能靠调用方自己保存
   和回填。

参考项目 `ai-agent-from-scratch` 的 `scratch_agents/context.py` 提供了
`ExecutionContext`（`execution_id` / `events` / `current_step` / `state` /
`final_result`）与 `AgentResult`（`output` / `context` / `status`），以及
`Event`（`id` / `execution_id` / `timestamp` / `author` / `content`）。它的
`state` 是一个普通字典，没有类型约束；成本控制则完全缺失。

## 决策

**运行时状态统一收进 `ExecutionContext`；成本预算与价格表分离。**

### 1. 状态收进 Execution Context，运行时不另设一份

`ExecutionContext` 承载：执行身份、事件历史、步数、草稿本、用量、成本、
最终产物。

对话内容不再单独存一份——它由事件历史展平而来（`context.contents`）。这样
「运行发生了什么」只有一个答案：上下文里的事件序列。

### 2. Event 携带归属与时刻

`Event` = 「谁说的、说了什么、什么时候」。相比扁平对话列表，它多出的两点
（归属、时刻）正是审计、回放、定位问题所必需的。

事件按发生顺序追加，展平后仍满足不变量：**每条 `tool_call` 都有对应的
`tool_result`**。

### 3. Agent Result 携带 Context

`AgentResult` 替代原先的 `AgentRun`，新增 `context` 与 `output`。分工是
**「结论」与「过程」**：调用方既拿到输出，也拿到完整历史、步数、用量与
成本——不必为了看历史而单独保存一份。

同时统一命名为 `AgentRuntime` / `AgentResult` / `ExecutionContext` / `Event`，
与参考实现和架构层的「Agent Runtime」一致，不再保留 `AgentLoop` / `AgentRun`
两套并行词汇。

### 4. Runtime State 是草稿本，模型看不到

`context.state` 是普通字典，服务于工具与应用层（例如「本轮已经问过用户哪些
问题」「学习计划草稿」）。它**不进入发给模型的内容**——模型看到的是事实，
不是内部状态。

工具通过 `ToolContext.metadata["execution_context"]` 取到执行上下文。用字符串
键而不在 `ToolContext` 上加字段，是为了保持依赖方向：`core/tools` 不应该知道
`core/agent` 的存在，否则工具系统会反向依赖 Agent 运行时。

### 5. 价格表由调用方提供

`AgentConfig.prices` 是「模型名 → `TokenPrice`」的映射，由调用方填。

价格会变、随厂商与地区不同、有时还随合同而变——把它内置在包里等于制造一个
必然过期的真相来源，而且过期时**没有任何人会收到通知**。

### 6. 预算无法执行就是配置错误

设了 `max_cost` 却无法确定模型名，或价格表没有覆盖实际使用的模型，运行时抛
`AgentConfigError`。理由：预算没被执行等于没设——继续跑下去只会让真实账单
超出预期，而用户以为有限额。宁可早失败。

### 7. token 预算与成本预算互补

`max_total_tokens` 约束**实际消耗**而非步数——同样 12 步，简短回答可能只花
几百 token，逐字输出长文档可能花掉几万。`max_cost` 是金额预算。

两者都在**步之间**检查，与其它预算一致：当前步内的调用全部执行完，保证每条
`tool_call` 都有对应结果。

## 后果

- 好处：运行可测试、可复现、可续接；历史可审计可回放；工具与应用层有地方
  放中间状态；消耗有两个维度（token 与金额）的约束；价格由调用方掌握，
  不必等运行时更新。
- 代价：结果对象携带可变上下文，需要注意「结果被拿到后上下文仍可能被
  修改」；草稿本是无类型字典，写错了只有运行时才暴露；价格表要调用方自己
  维护。
- 边界：`state` 不持久化（进程重启即丢）；没有跨进程的状态共享；事件不压缩
  （长会话的上下文膨胀问题仍未解决，属 Topic 08 记忆与上下文工程）；成本
  只按 token 估算，不含缓存命中、批处理折扣等厂商计费细节。

## 补充说明（2026-11-19）

实现中确认的事实：

1. **「预算无法执行」必须早失败**：初版只在价格表缺失时静默按 0 计费，
   结果是设了成本上限却完全没有约束——比不设更危险，因为用户以为有限额。
   改为抛 `AgentConfigError`。
2. **成本核算依赖客户端上报 usage 与模型名**：脚本化的模型响应若不带
   `usage`，用量与成本都是 0。真实客户端都会上报，但测试替身必须照做——
   否则测试会在「成本恒为 0」上假绿。
3. **工具调用 id 的归属再次浮现**：`_observe` 需要把调用与结果配对，
   `ToolOutcome` 不带调用 id（它描述的是工具本身）。这与 ADR-0005 的同一
   细节一致。
4. **命名统一比新增别名更有价值**：与其同时保留 `AgentLoop` / `AgentRuntime`
   两套说法，不如一次切换到与架构层、参考实现都一致的词汇。

接入说明见 [`docs/engineering/runtime.md`](../../engineering/runtime.md)。
学习笔记见 [`docs/learning/topics/06-execution-context-and-runtime.md`](../../learning/topics/06-execution-context-and-runtime.md)。
