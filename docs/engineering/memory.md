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

### 1.8 会话与长期记忆

- **会话**（跨运行持续存在的对话、检查点、持久化、人工确认）：见
  [Session State Notes](./session.md)
- **长期记忆**（跨会话保留的学习者事实）：见本文第 2 节

## 2. 长期记忆（已实现）

跨会话保留**关于学习者的事实**：目标、水平、强项、弱点、偏好、学习节奏。

### 2.1 什么值得记

一条信息要进长期记忆，必须同时满足三条：

1. **归属**——是关于**谁**的？关于学习者的事实进；工具输出、模型推测、一次性
   任务进展不进。
2. **耐久**——下次会话还成立吗？「今天学了闭包」不成立，「我喜欢先看代码」成立。
3. **可覆盖**——它属于哪一类事实？同一类事实**只保留最新的一条**。

### 2.2 记忆模型

```python
Memory(
    id="...",
    memory_type="semantic",  # 事实 / 经历 / 规则（CoALA 三分法）
    kind="weakness",  # 领域内的类别
    subject="learner-1",  # 归属主体
    scope="闭包",  # 同类别下的细分键；留空表示该类别只有一条
    content="闭包掌握不好",
    confidence=0.9,
)
```

**唯一键是 `(主体, 类型, 类别, 作用域)`**。写入时按键 upsert——新事实**取代**
旧事实，而不是并存两条矛盾的。

```python
# 水平从初级变成中级 → 同一条记忆被更新，不是多了一条
```

### 2.3 写入

```python
from agent_learning_coach.core.memory import (
    InMemoryMemoryStore,
    LLMMemoryExtractor,
    MemoryManager,
)

manager = MemoryManager(InMemoryMemoryStore(), LLMMemoryExtractor(client))
written = await manager.capture(transcript, subject=learner_id)
```

流程：**抽取 → 准入过滤 → 同内容去重 → 按唯一键 upsert**。

三道抑制噪声的闸：

| 闸 | 位置 | 作用 |
|---|---|---|
| 抽取提示词 | `LLMMemoryExtractor` | 要求「没有值得记的就返回空列表」——空列表是被期望的答案 |
| 准入策略 | `MemoryPolicy` | 确定性检查：内容非空、置信度达标、长度不超标、类别在词表内 |
| 去重与覆盖 | `MemoryManager` | 同类别同内容不重复写；同键直接取代 |

**模型说了不算**——准入是确定性的。交给模型判断「这条算不算噪声」就等于没有
判断。

### 2.4 召回

```python
memories = await manager.recall("闭包", subject=learner_id)  # 按相关度
memories = await manager.recall(subject=learner_id, kind="weakness")  # 按类别
profile = await manager.profile(subject=learner_id)  # 画像
```

召回是**结构化筛选 + 关键词相关度**，不用向量检索：学习者的长期记忆是结构化的
（几十到几百条，每条带类别与主体），绝大多数召回是筛选而非相似度检索。这样
**不需要嵌入服务**（参考实现因此绑定了 OpenAI 的 embedding API）、完全离线
可测、结果可解释。向量检索留给 Day 11～12 的 RAG。

中文按**二元组**切分（「闭包」切成「闭」「包」）——中文没有词边界，按空格切会
得到整句话。

### 2.5 注入到提示词

记忆的召回与应用层的编排由应用层负责（运行时是领域无关的）：

```python
profile = await manager.profile(subject=learner_id)
instructions = [*base_instructions]
if text := profile.to_prompt():
    instructions.append(text)

runtime = AgentRuntime(
    client,
    dispatcher,
    config=AgentConfig(model="gpt-4", instructions=instructions),
)
```

### 2.6 写入时机

`MemoryManager` 只提供能力，**不决定何时调用**。由应用层决定：会话结束时？
每 N 轮？用户主动触发？

记忆写入**不在 Agent 的热路径上**——在热路径里写记忆会增加延迟、让 Agent
分心。抽取失败会明确抛出（`LLMError`），由调用方在后台任务里记日志。

## 3. 记忆分层

| 层级 | 作用 | 存储 | 状态 |
| ---- | ---- | ---- | ---- |
| 短期 | 当前会话上下文 | `SessionContext` + `SessionStore` | 已实现 |
| 长期 | 学习者画像与掌握度 | `MemoryStore` | 已实现（基础形态） |

### 3.1 冲突与遗忘

- **冲突**：同唯一键的新事实**取代**旧事实。语义层面的冲突（两条并不真正互斥
  的事实用了同一个作用域）没有处理——覆盖会丢掉一条。
- **遗忘**：**没有**。记忆只增不删。水平变了旧的那条被覆盖，但「曾经是初级」
  这类历史信息无处安放。时效性也没有——三个月前的偏好和今天的权重相同。

## 4. 已知边界

- **没有基于 LLM 的摘要压缩**。当前压缩是规则式的（清工具结果、换占位符），
  不如摘要能保留语义细节。需要时补，但摘要策略要一次模型往返，引入成本、
  延迟与失败语义。
- **token 计数是估算**。`tiktoken` 是 OpenAI 系分词器，对 Anthropic 系模型
  是近似；回退的字符估算更粗。预算是保守估计。
- **会话持久化没有并发控制**。两个进程同时恢复同一个会话会互相覆盖——生产用
  需要乐观锁或租约。
- **长期记忆没有遗忘与时效性**。只增不删（除同键覆盖），三个月前的偏好和今天
  的权重相同。
- **长期记忆没有冲突的语义消解**。同键覆盖是「新的赢」；两条并不互斥的事实
  用了同一个作用域时会丢掉一条。
- **长期记忆没有跨事实关联**。记忆之间没有引用关系，「这个弱点由哪个目标造成」
  这类问题答不了。
- **没有审批过期机制**。一条几天前的待批准调用恢复时仍然有效。
- **没有多租户隔离**。`user_id` 只是记录，没有据此做访问控制。
- **没有跨会话记忆**。长期记忆属 Day 10。

## 相关文档

- 决策依据：[ADR-0008](../architecture/decisions/0008-context-storage-and-presentation.md)、[ADR-0009](../architecture/decisions/0009-session-persistence-and-hitl.md)、[ADR-0010](../architecture/decisions/0010-long-term-memory-admission-and-overwrite.md)
- 学习笔记：[Topic 08 · Memory 与 Context Engineering](../learning/topics/08-memory-and-context-engineering.md)
- 技术文章：[记忆与 RAG](../articles/memory-and-rag.md)
