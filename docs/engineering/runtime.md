# 运行时（Runtime）

> 状态：部分实现（Agent Loop 已交付）

Agent Runtime 层为应用层提供可复用的 Agent 执行能力，位于
`src/agent_learning_coach/core/agent/`。

## Agent Loop

ReAct 执行循环：「模型 → 工具 → 结果回填 → 模型」。

```python
from agent_learning_coach.core.agent import AgentConfig, AgentLoop
from agent_learning_coach.core.tools import ToolDispatcher, ToolRegistry

registry = ToolRegistry()
dispatcher = ToolDispatcher(registry)
loop = AgentLoop(client, dispatcher, config=AgentConfig(max_steps=12))

run = await loop.run("帮我安排本周的学习计划")

print(run.stop_reason)  # completed / max_steps / ...
print(run.final_text)  # 模型最后一段说明
```

### 职责边界

循环只管「模型 ↔ 工具」的节奏：

| 关注点 | 归属 |
| ------ | ---- |
| 模型调用、厂商差异、结构化输出 | `core/llm`（`LLMClient`） |
| 工具契约、参数校验、超时、结果归一化 | `core/tools`（`ToolDispatcher`） |
| 循环控制、停止条件、对话记录 | `core/agent`（`AgentLoop`） |

因此替换模型不需要改循环，换工具来源（本地工具 / MCP 远端工具）也不需要改循环。

### 三个阶段

ReAct 的三个阶段各自是一个方法，职责单一、可独立测试：

| 阶段 | 方法 | 做什么 |
| ---- | ---- | ------ |
| **Thought** | `_think` | 把当前对话与工具清单交给模型，模型要么给出回答，要么请求调用工具 |
| **Action** | `_act` | 执行模型请求的工具调用，委托 `ToolDispatcher` |
| **Observation** | `_observe` | 把工具结果转成 `ToolResult` 回填给模型 |

一处细节：工具调用 id 来自**调用**而非执行结果——`ToolOutcome` 描述的是工具
本身（哪个工具、执行成没成），不知道它属于哪一次调用，因此 `_observe` 同时
接收 `tool_calls` 与 `outcomes` 并按位置配对。

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
| `examples/agent_loop_with_mcp.py` | 同一个循环驱动远端 MCP 工具与本地工具（真实 MCP 子进程 + 真实协议） |

两个示例并列即可看出「换工具来源不改循环」：循环代码完全一样，只有工具注册表
的填充方式不同。

## 相关文档

- 决策依据：[ADR-0005](../architecture/decisions/0005-agent-loop.md)
- 学习笔记：[Topic 05 · ReAct 与 Agent Loop](../learning/topics/05-react-and-agent-loop.md)
- 上游：[工具系统接入说明](tools.md)、[LLM 接入说明](llm.md)
