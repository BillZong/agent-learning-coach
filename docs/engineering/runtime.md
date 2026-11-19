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

### 停止条件

```
模型不再请求工具           -> completed        （唯一成功路径）
连续工具失败超限           -> consecutive_errors
同一工具同一参数反复调用    -> repeated_call
工具调用预算耗尽           -> max_tool_calls
步数上限                   -> max_steps
整体截止时间已到            -> timeout
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
- 模型调用失败**向上抛出**，不重试
- 无中断与恢复：一次运行是原子的

## 工具调用与并发

- TBD（并发执行需要处理取消传播、超时传播与结果顺序的确定性）

## 会话与上下文管理

- TBD

## 可观测性

- TBD

## 相关文档

- 决策依据：[ADR-0005](../architecture/decisions/0005-agent-loop.md)
- 学习笔记：[Topic 05 · ReAct 与 Agent Loop](../learning/topics/05-react-and-agent-loop.md)
- 上游：[工具系统接入说明](tools.md)、[LLM 接入说明](llm.md)
