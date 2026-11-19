# 运行时（Runtime）

> 状态：部分实现（Agent Runtime 基础能力已交付）

Agent Runtime 层为应用层提供可复用的 Agent 执行能力，位于
`src/agent_learning_coach/core/agent/`。

## 1. 组成

| 模块 | 内容 |
| ---- | ---- |
| `runtime.py` | `AgentRuntime`——ReAct 执行循环与状态记录 |
| `types.py` | `Event` / `ExecutionContext` / `AgentConfig` / `AgentStep` / `AgentResult` / `StopReason` |
| `cost.py` | `TokenPrice`——token 单价与成本核算 |
| `errors.py` | `AgentError` / `AgentConfigError` |

## 2. Agent Runtime

ReAct 执行循环 + 运行时状态：「模型 → 工具 → 结果回填 → 模型」。

```python
from agent_learning_coach.core.agent import AgentConfig, AgentRuntime
from agent_learning_coach.core.tools import ToolDispatcher, ToolRegistry

registry = ToolRegistry()
dispatcher = ToolDispatcher(registry)
runtime = AgentRuntime(client, dispatcher, config=AgentConfig(max_steps=12))

result = await runtime.run("帮我安排本周的学习计划")

print(result.status)  # complete / error
print(result.stop_reason)  # completed / max_steps / ...
print(result.output)  # 最终产物
print(result.usage)  # 累计 token
print(result.cost)  # 累计成本
```

### 职责边界

运行时只管「模型 ↔ 工具」的节奏与状态：

| 关注点 | 归属 |
| ------ | ---- |
| 模型调用、厂商差异、结构化输出 | `core/llm`（`LLMClient`） |
| 工具契约、参数校验、超时、结果归一化 | `core/tools`（`ToolDispatcher`） |
| 循环控制、停止条件、状态记录 | `core/agent`（`AgentRuntime`） |

因此替换模型不需要改运行时，换工具来源（本地工具 / MCP 远端工具）也不需要改
运行时。

### 三个阶段

ReAct 的三个阶段各自是一个方法，职责单一、可独立测试：

| 阶段 | 方法 | 做什么 |
| ---- | ---- | ------ |
| **Thought** | `_think` | 把当前对话与工具清单交给模型，模型要么给出回答，要么请求调用工具 |
| **Action** | `_act` | 执行模型请求的工具调用，委托 `ToolDispatcher` |
| **Observation** | `_observe` | 把工具结果转成 `ToolResult` 回填给模型 |

一处细节：工具调用 id 来自**调用**而非执行结果——`ToolOutcome` 描述的是工具
本身（哪个工具、执行成没成），不知道它属于哪一次调用，因此 `_observe` 需要
同时接收 `tool_calls` 与 `outcomes` 并按位置配对。

### Execution Context

一次执行的**全部运行时状态**都记在 `ExecutionContext` 里：

| 状态 | 用途 |
| ---- | ---- |
| `execution_id` | 执行身份，串联事件、日志与外部系统 |
| `events` | 执行历史，按发生顺序排列 |
| `current_step` | 已完成的步数 |
| `state` | 运行时草稿本 |
| `usage` / `cost` | 预算执行与事后核算 |
| `final_result` | 最终产物 |

这让运行**可测试、可复现**：给定同样的输入与同样的事件序列，就能得到同样的
结论。

**Event** 与「扁平的对话列表」的区别在于额外携带了**是谁**说的与**什么时候**
说的——这两点让历史可审计、可回放、可定位问题。`context.contents` 提供展平
后的对话视图，并保持不变量：**每条 `tool_call` 都有对应的 `tool_result`**。

传入已有上下文即可续接对话：

```python
context = ExecutionContext(execution_id="session-42")
result = await runtime.run("这一轮的问题", context=context)
# 上一轮的事件仍在 context.events 里
```

### Runtime State

`context.state` 是运行时草稿本：**工具与应用层可见，模型看不到**。

工具通过 `ToolContext.metadata["execution_context"]` 取到执行上下文：

```python
execution = context.metadata.get("execution_context")
if execution is not None:
    execution.state["searched"] = execution.state.get("searched", []) + [query]
```

之所以用字符串键而不在 `ToolContext` 上加字段：`core/tools` 不应该知道
`core/agent` 的存在——那会让工具系统反向依赖 Agent 运行时。约定放在使用方更
符合依赖方向。

### Cost Control

价格表**由调用方提供**，不内置在包里——价格会变、随厂商与地区不同、有时还随
合同而变；写死等于制造一个必然过期的真相来源。

```python
from agent_learning_coach.core.agent import AgentConfig, TokenPrice

config = AgentConfig(
    max_total_tokens=50_000,
    max_cost=2.0,
    prices={"minimax-cn/MiniMax-M2": TokenPrice(input_per_1k=0.01, output_per_1k=0.02)},
)
```

两个预算的分工：

- **`max_total_tokens`** 约束**实际消耗**而非步数——同样 12 步，简短回答可能
  只花几百 token，逐字输出长文档可能花掉几万。
- **`max_cost`** 是金额预算，需要价格表。

两者都在**步之间**检查，与其它预算一致。

**预算无法执行就是配置错误**：设了 `max_cost` 却无法确定模型名，或价格表没有
覆盖实际使用的模型，运行时抛 `AgentConfigError`——预算没被执行等于没设。

### 异常处理

每一类可预期的失败都有明确的处理方式，没有一类是「崩掉」：

| 失败 | 处理 | 循环继续？ |
| ---- | ---- | ---------- |
| 工具参数不合法 | 归一化成 `ToolOutcome(status="error")` 回填 | 继续 |
| 工具内部报错 | 同上 | 继续 |
| 工具不存在（模型幻觉） | 同上 | 继续 |
| 远端 MCP 会话已关闭 | 同上，错误消息明确指出是会话问题 | 继续 |
| 连续工具失败超限 | `stop_reason="consecutive_errors"` | 停止 |
| 同一工具同一参数反复调用 | `stop_reason="repeated_call"` | 停止 |
| 整体超时 | `stop_reason="timeout"`，保留部分结果 | 停止 |
| 模型调用失败（`LLMError`） | `stop_reason="llm_error"`，保留部分结果，`AgentRun.error` 记原因 | 停止 |
| 外部取消（`CancelledError`） | 照常传播 | 停止 |
| 非 `LLMError` 的异常 | 照常抛出——那是编程错误，不该被包装成运行结果 | 停止 |

两条原则：

1. **模型能自愈的错误就让它自愈**——工具级失败一律回填，这是「过早收敛」的
   主要防护。
2. **运行时失败不丢弃已完成的成果**——超时与模型失败都返回部分结果（步数、
   累计用量、完整对话记录），调用方据此决定是否重跑。只有编程错误才抛异常。

重试策略不在循环内：它知道这次运行值不值得重跑。

### 与 MCP 工具集成

远端 MCP 工具接入本地注册表后，与本地工具**完全平级**：

```python
async with stdio_session(cmd) as session:
    for tool in await discover_mcp_tools(session):
        registry.register(tool)

loop = AgentLoop(client, ToolDispatcher(registry))
run = await loop.run("闭包是什么")
```

模型看到的工具清单里两类工具混在一起，走同一个调度器，因此参数校验、超时与
结果归一化的行为一致——模型不需要知道工具来自哪里。

可运行示例见 `examples/agent_loop_with_mcp.py`（真实 MCP 子进程 + 真实协议）。

**约束**：远端工具的生命周期绑定在会话上，会话关闭后已接入的远端工具立即失效。
注册表的长生命周期必须与连接的长生命周期对齐。

### 停止条件

```
模型不再请求工具           -> completed        （唯一成功路径）
连续工具失败超限           -> consecutive_errors
同一工具同一参数反复调用    -> repeated_call
工具调用预算耗尽           -> max_tool_calls
token 预算耗尽             -> max_tokens
成本预算耗尽               -> max_cost
步数上限                   -> max_steps
整体截止时间已到            -> timeout
模型调用失败               -> llm_error
```

分层的理由：**只有模型自己知道任务是否完成**。预算是兜底——保证失控的循环一定
会停下来，而不是为了提前结束一个正在取得进展的任务。

多个条件同时命中时，**更具体的原因优先**：`repeated_call` 指向模型行为，
`max_steps` 只是预算耗尽。

### 失败回填而非终止

工具执行失败（参数不合法、工具内部报错、工具不存在）一律归一化成
`ToolOutcome(status="error")` 回填给模型，让它换参数、换工具或直接回答。

模型能自愈的错误就让它自愈——这是「过早收敛」的主要防护。只有当失败**连续
发生**到一定次数，才判定模型陷在同一个错误里并停止。

### 对话记录的不变量

`AgentRun.contents` 保证：**每条 `tool_call` 都有对应的 `tool_result`**。

因此这份记录可以直接作为下一轮对话的输入继续。工具调用预算因此在**步之间**
检查：达到上限后不再开始新的一步，当前步内的调用全部执行完——否则会留下没有
结果的悬空 `tool_call`，多数厂商 API 会直接报错。

### 已知限制

- 工具**顺序执行**，不并发
- 上下文**不裁剪**，对话记录原样返回
- 模型调用失败**不重试**，只保留部分结果由调用方决定
- 无中断与恢复：一次运行是原子的

## 工具调用与并发

- TBD（并发执行需要处理取消传播、超时传播与结果顺序的确定性）

## 会话与上下文管理

- TBD

## 可观测性

- TBD

## 可运行示例

| 示例 | 说明 |
| ---- | ---- |
| `examples/basic_react_agent.py` | 基础 ReAct Agent：纯本地工具、无子进程、无凭证，脚本化模型走完三轮工具调用 |
| `examples/agent_runtime.py` | 基础 Agent Runtime：演示 Execution Context、Runtime State 与 Cost Control |
| `examples/agent_loop_with_mcp.py` | 同一个循环驱动远端 MCP 工具与本地工具（真实 MCP 子进程 + 真实协议） |

两个示例并列即可看出「换工具来源不改循环」：循环代码完全一样，只有工具注册表
的填充方式不同。

## 相关文档

- 决策依据：[ADR-0005](../architecture/decisions/0005-agent-loop.md)、[ADR-0006](../architecture/decisions/0006-execution-context-and-cost-control.md)
- 学习笔记：[Topic 05 · ReAct 与 Agent Loop](../learning/topics/05-react-and-agent-loop.md)
- 上游：[工具系统接入说明](tools.md)、[LLM 接入说明](llm.md)
