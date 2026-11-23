# Session State Notes

> 状态：草稿 ｜ 对应 Topic 09「Session 与 HITL」

本文是**会话状态**的参考文档：会话模型、检查点、持久化、暂停与恢复、人工确认。

长期记忆（跨会话的学习者事实）见 [记忆（Memory）](./memory.md)；上下文预算见
同一份文档的第 1 节。

## 1. 会话模型

**一次运行 ≠ 一次会话。**

- **一次运行**（`AgentRuntime.run`）是模型与工具之间的一轮循环。它有明确的
  结束。
- **一次会话**是学习者与教练之间的多轮对话。它只在学习者离开时才结束。

```python
from agent_learning_coach.core.agent import SessionContext

session = SessionContext(session_id="s1", user_id="learner-1")
await session.run(runtime, "帮我学闭包")
await session.run(runtime, "那什么是装饰器？")
```

两轮共享同一份 `ExecutionContext`，因此第二轮的模型能看到第一轮的内容；
草稿本（`session.state`）也跨轮保留。

**会话状态只有一份权威**——`SessionContext` 持有**一个** `ExecutionContext`，
事件只有一份。参考实现（书本 Listing 6.11）让会话与执行上下文各存一份事件，
实测结果是**多轮会话里模型完全看不到历史**。详见 [ADR-0009](../architecture/decisions/0009-session-persistence-and-hitl.md)。

## 2. 检查点（Checkpoint）

**检查点 = 一个可以从这一刻起被恢复的运行状态。**

它包含恢复所需的全部东西——事件历史、草稿本、用量与成本、步数、三项循环
计数、待确认的审批、创建与更新时刻。**少一样，恢复出来的就不是原来那个
运行**：

| 缺了什么 | 后果 |
|---|---|
| 事件历史 | 模型看不到之前的对话 |
| 草稿本 | 工具的中间状态丢失 |
| 用量与成本 | 账目对不上；成本预算失效 |
| 步数 | 恢复后从头再来，浪费预算 |
| 工具调用计数 | **暂停-恢复可以绕过工具调用数上限** |
| 连续失败 / 调用计数 | **暂停-恢复可以绕过停止条件** |
| 待确认的审批 | 暂停的运行恢复不了 |

```python
checkpoint = session.checkpoint()  # 打一个检查点
restored = SessionContext.restore(checkpoint)  # 从检查点还原
```

`AgentStep`（逐步明细）**不落盘**——完整过程在事件历史里；恢复后的
`AgentResult.steps` 覆盖的是**本次运行段落**。

## 3. 持久化

```python
from agent_learning_coach.core.agent import InMemorySessionStore, JsonFileSessionStore

store = JsonFileSessionStore("./sessions")  # 一个会话一个 JSON 文件
await session.save(store)

restored = await SessionContext.load(store, "s1")
```

`SessionStore` 是协议，实现可替换（数据库、Redis……）。文件存储写文件时先写
临时文件再改名——**改名是原子的**，因此不会恢复出「写到一半」的会话；文件 I/O
放到线程里，不阻塞事件循环。

## 4. 暂停与恢复

### 声明需要确认

在工具契约上声明：

```python
ToolDefinition(
    name="delete_notes",
    description="删除学习笔记",
    parameters={...},
    requires_confirmation=True,
    confirmation_message="确认要删除 {path} 吗？",
)
```

`confirmation_message` 是模板，可用这次调用的参数做占位符。

### 运行会暂停

```python
result = await runtime.run("删掉笔记")

if result.status == "pending_approval":
    for pending in result.pending_approvals:
        print(pending.confirmation_message)
```

**整步暂停**：一步里模型请求的工具要么全执行，要么全暂停。部分执行会让转录
里出现「有调用没结果」的断裂，厂商 API 会拒绝整个请求。

暂停的运行**不能再 `run()`**——否则请求里会带着没有结果的 `tool_call`。

### 恢复

```python
result = await runtime.resume(
    result.context,
    [ApprovalDecision(tool_call_id="call-1", approved=True)],
)
```

- **批准**的照常执行；可以用 `modified_arguments` 改参数后再批准。
- **拒绝**的回填一条「用户拒绝执行该工具（理由）。不要再尝试这个操作」。
- **没有决定的按拒绝处理**（fail closed）——缺少批准就执行，等于让「批准」
  变成默认开启。

批准是**运行时的约束**，模型无法绕过：模型只能请求，不能批准。

### 跨进程恢复

```python
await session.save(store)
# ……进程重启……
restored = await SessionContext.load(store, "s1")
result = await restored.resume(runtime, decisions)
```

恢复出来的会话带着完整的历史、待确认的调用、用量与计数——因此恢复后的运行
**不是**从头再来。

## 5. 状态转换

运行是一台状态机：

```
                 run()
                  │
                  ▼
            ┌───────────┐
            │  running  │◀─────┐
            └───────────┘      │
              │  │  │         │ 还有步数
              │  │  │         │
   completed  │  │  └─────────┘
   error      │  └──────────► pending_approval
              │              （等待人工批准）
              │                 │
              │            resume(批准)
              └─────────────────┘
```

| 起始状态 | 动作 | 结束状态 | 停止原因 |
|---|---|---|---|
| 新运行 | 模型不再请求工具 | complete | `completed` |
| 新运行 | 请求了需确认的工具 | pending_approval | `pending_approval` |
| pending_approval | 批准后 resume | running → 终态 | 视后续而定 |
| pending_approval | 拒绝后 resume | running → 终态 | 视后续而定 |
| pending_approval | 无决定时 resume | running → 终态 | 视后续而定 |
| pending_approval | 再次 run | **被拒绝**（抛 `AgentError`） | — |
| 终态 | resume | **被拒绝**（抛 `AgentError`） | — |

转换表由 `tests/test_state_transitions.py` 表驱动验证。

## 6. 已知边界

- **没有并发控制**。两个进程同时恢复同一个会话会互相覆盖——生产用需要乐观锁
  或租约。
- **没有审批过期机制**。几天前的待批准调用恢复时仍然有效。
- **没有多租户隔离**。`user_id` 只是记录，没有据此做访问控制。
- **`AgentStep` 不落盘**，恢复后的 `steps` 只覆盖本次运行段落。
- **恢复时整体超时重新计时**。人不在场的时间不计入运行超时——否则人下班了
  运行就超时了；但这也意味着超时不是「端到端」的。
- **落盘格式与运行时类型耦合**。`ExecutionContext` 加字段要同步
  `SessionState.to_dict` / `from_dict`，两处漏一处就是静默丢数据。

## 相关文档

- 决策依据：[ADR-0009](../architecture/decisions/0009-session-persistence-and-hitl.md)
- 相关：[记忆（Memory）](./memory.md)、[威胁模型](./threat-model.md)
- 示例：`examples/hitl_approval.py`
