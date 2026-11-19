# 记忆与上下文（Memory & Context）

> 状态：草稿 ｜ 对应 Topic 08（上下文管理）、Day 10（长期记忆）

本文覆盖两件事：**上下文管理**（已实现，Topic 08）与**记忆分层**（尚未实现）。

## 1. 上下文管理（已实现）

### 1.1 存储与呈现分离

一句话：**发生了什么**和**发给模型什么**是两件事。

- **存储**：执行上下文里的完整事件历史。永远完整——裁剪不会删掉任何发生过
  的事。
- **呈现**：这一次请求实际发送的内容。受 token 预算约束，会被裁剪。

```
ExecutionContext.events      ← 存储：完整、只增不改、可回放
        │  contents（展平）
        ▼
ContextBuilder.build()        ← 呈现：按预算投影
        │
        ▼
LLMRequest.contents          ← 模型看到的东西
```

因此「裁剪」不丢信息——模型这一轮看不到的内容还在上下文里,下一轮预算放宽时
它可以重新出现。

### 1.2 三个组件

| 组件 | 位置 | 回答什么问题 |
|---|---|---|
| `TokenCounter` | `core/context/tokens.py` | 这次请求多大？ |
| `ContextStrategy` | `core/context/strategies.py` | 怎么裁？ |
| `ContextBuilder` | `core/context/builder.py` | 裁到什么程度？ |

**TokenCounter** 优先用 `tiktoken` 估算；拿不到分词器时**回退到按字符估算**
（中日韩字符按 1 token，其余按 4 字符 1 token）。回退是**保守**的——只会高估，
不会让人以为内容比实际更小。`counter.exact` 告诉你当前走的是哪条路径。

**两种内置策略**：

- `Compaction` —— 处理**体积**。把臃肿的工具结果与调用参数换成占位符。
  只换内容、不增删条目，因此配对天然不变。
- `SlidingWindow` —— 处理**条数**。丢掉最老的内容。丢条目会破坏配对，因此
  必须显式修复。

**ContextBuilder** 按顺序应用策略，每应用一个就重新计数，装得下就停。

### 1.3 接入

```python
from agent_learning_coach.core.agent import AgentConfig, AgentRuntime
from agent_learning_coach.core.context import (
    Compaction,
    CompactionRules,
    ContextBuilder,
    SlidingWindow,
    TokenCounter,
)

builder = ContextBuilder(
    max_tokens=8000,
    strategies=(
        Compaction(CompactionRules(max_result_chars=2000)),
        SlidingWindow(keep_recent=20),
    ),
    counter=TokenCounter(model="gpt-5"),
)

runtime = AgentRuntime(
    client,
    dispatcher,
    config=AgentConfig(model="gpt-5", context_builder=builder),
)
```

不配置 `context_builder` 时**行为完全不变**——既有调用方不受影响。

### 1.4 压缩规则

```python
CompactionRules(
    max_result_chars=2000,  # 工具结果超过则截断
    max_argument_chars=1000,  # 调用参数超过则截断
    result_templates={  # 工具名 → 结果模板
        "read_file": "已读取 {path}，需要时重新读取。",
    },
    argument_placeholders={  # 工具名 → (参数名 → 占位符)
        "write_file": {"content": "[已保存到文件]"},
    },
    protected_tools=frozenset({"final_answer"}),  # 这些工具不压缩
)
```

模板里可以用这次调用的参数做占位符；缺失的参数填 `unknown`，不会抛异常。

**`protected_tools` 值得注意**：用结构化输出时应当把 `final_answer` 放进去——
它是最终产物，截断它等于丢掉答案。

### 1.5 预算被真正执行

`ContextBuilder` 的行为顺序：

1. 没超预算 → 原样返回。
2. 超预算 → 依次应用策略，每应用一个就重新计数，装得下就停。
3. 仍超预算 → **从最新的一条往回保留**，装不下的那条**裁掉它的文本**。
4. 裁不动（工具调用的参数是结构化的，截断 JSON 会让参数失效）→ 如实标记
   `over_budget`。

第 3 步是有意的：宁可让模型看到一段被截断的输入，也不要发出一个会被 API
拒绝的请求。

### 1.6 配对不变量

任何裁剪之后，「每条 `tool_call` 都有对应的 `tool_result`」这条不变量必须
成立——主流厂商 API 会**拒绝整个请求**，不是降级。

`drop_unpaired` 移除两种落单内容：孤立的调用（有调用无结果）、孤立的结果
（有结果无调用）。

### 1.7 观测

`ContextView` 记录裁剪过程：

```python
view = result.context.last_context_view
view.original_tokens  # 裁剪前
view.final_tokens  # 裁剪后
view.dropped_items  # 丢弃条目数
view.applied  # 实际应用的策略
view.truncated  # 是否有内容被截断
view.over_budget  # 是否仍然超预算
```

运行时把它记在 `ExecutionContext.last_context_view` 里。

### 1.8 会话

`SessionContext` 是跨多次运行持续存在的会话：

```python
from agent_learning_coach.core.agent import SessionContext

session = SessionContext()
await session.run(runtime, "帮我学闭包")
await session.run(runtime, "那什么是装饰器？")
```

两轮共享同一份执行上下文，因此第二轮的模型能看到第一轮的内容。草稿本
（`session.state`）也跨轮保留。

**本阶段只做基础形态**：内存里的会话状态。没有持久化、没有暂停恢复、没有
人工确认——那些属于 Day 09「Session 与 HITL」。

## 2. 记忆分层（尚未实现）

| 层级 | 作用 | 存储 | TTL |
| ---- | ---- | ---- | ---- |
| 短期 | 当前会话上下文 | 已实现：`SessionContext` | 进程内 |
| 长期 | 用户画像与掌握度 | TBD | TBD |

### 2.1 写入与召回策略

- TBD（Day 10 Long-term Memory）

### 2.2 冲突与遗忘

- TBD（Day 10 Long-term Memory）

## 3. 已知边界

- **没有基于 LLM 的摘要压缩**。当前压缩是规则式的（清工具结果、换占位符），
  不如摘要能保留语义细节。需要时补，但摘要策略要一次模型往返，引入成本、
  延迟与失败语义。
- **token 计数是估算**。`tiktoken` 是 OpenAI 系分词器，对 Anthropic 系模型
  是近似；回退的字符估算更粗。预算是保守估计。
- **会话不持久化**。进程退出即丢。
- **没有跨会话记忆**。长期记忆属 Day 10。

## 相关文档

- 决策依据：[ADR-0008](../architecture/decisions/0008-context-storage-and-presentation.md)
- 学习笔记：[Topic 08 · Memory 与 Context Engineering](../learning/topics/08-memory-and-context-engineering.md)
- 技术文章：[记忆与 RAG](../articles/memory-and-rag.md)
