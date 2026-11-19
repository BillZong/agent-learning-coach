# ADR-0008: 上下文按存储与呈现分离，预算被执行

- 状态：已接受
- 日期：2026-11-19

## 背景

Topic 06 交付的 Agent Runtime 已经能记录完整的事件历史，但**发给模型的内容
就是完整历史**。每轮工具调用都往历史里追加内容——模型输出、工具结果、工具
结果的错误信息。跑上十几轮，一个中等复杂度的任务就能把上下文窗口吃掉，
而且没有任何机制会阻止它。

这不是「以后再优化」的问题。没有上下文管理，Agent 就**不能长期运行**——
而「学习教练」恰恰是一个需要长期陪伴的角色。

Topic 08 提出的工程问题是：**上下文超限时，Sliding Window、Token Counting、
Compaction 三者的职责如何划分？**

主教材 6.2 给了完整的答案，但参考实现的落地有两个问题：

1. **滑窗会切断配对**。参考实现直接切列表（`remaining[-window_size:]`），
   会留下没有对应结果的 `tool_call`。主流厂商 API 会**拒绝整个请求**——
   不是降级，是失败。
2. **没有真正的预算**。参考实现的优化是「超阈值就裁一刀」，裁完是否装得下
   并不过问。

另外，本项目自己有一条已经确立的不变量（ADR-0005）：**每条 `tool_call` 都有
对应的 `tool_result`**。任何上下文裁剪都必须维护它。

## 决策

**存储与呈现分离；预算是被执行的，不是建议。**

### 1. 存储与呈现分离

主教材 6.2.1「Separating storage from presentation」：

- **存储**：执行上下文里的完整历史，**永远完整**。裁剪不会删掉任何发生过
  的事，事后审计与回放不受影响。
- **呈现**：这一次请求实际发给模型的内容，受预算约束。

实现上分成两个模块，依赖方向单向：

```
core/context/  →  core/llm/       （只认厂商无关类型）
core/agent/    →  core/context/    （运行时用构建器投影历史）
```

`core/context` 不认识 `core/agent`——否则上下文层会反向依赖运行时层。

这与 LangGraph 的 state 设计同源：state schema 里 `messages` 字段暴露给
模型，其余字段隔离起来，只在需要时选取。本项目的对应物是
`ExecutionContext.contents`（暴露）与 `ExecutionContext.state`（隔离）。

### 2. 三者职责

- **Token Counting**（`core/context/tokens.py`）：回答「这次请求多大」。优先用
  `tiktoken`；**拿不到分词器时回退到按字符估算**，而不是让预算功能整体
  不可用——`tiktoken` 首次使用要联网下载 BPE 文件，离线 CI 会失败。
- **Compaction**（`core/context/strategies.py`）：处理**体积**。把臃肿的工具
  结果与调用参数换成占位符。**只换内容、不增删条目**，因此配对天然不变。
- **Sliding Window**（同上）：处理**条数**。丢掉最老的内容。**丢条目会破坏
  配对**，因此必须显式修复。

顺序：**先压缩，后滑窗**。压缩保住更多条目，滑窗只在压缩不够时才出手。

Anthropic 把「清掉工具结果」称为「the safest lightest touch forms of
compaction」——本项目的 Compaction 正是这一类：保留标识符（路径、查询词），
丢掉内容，需要时让模型重新取。

### 3. 滑窗必须维护配对

裁剪后要移除没有配对的 `tool_call` 与 `tool_result`：

- **孤立的 tool_call**（有调用无结果）：丢弃调用。
- **孤立的 tool_result**（有结果无调用）：丢弃结果。

两种都必须丢——留下任何一个，请求都会被拒绝。

这是本项目相对参考实现的一处修正，源于本项目自己的不变量。

### 4. 预算是被执行的

`ContextBuilder` 的行为：

1. 没超预算 → 原样返回。
2. 超预算 → 依次应用策略，每应用一个就重新计数，装得下就停。
3. 仍超预算 → **从最新的一条往回保留**，装不下的那条**裁掉它的文本**。
4. 裁不动（工具调用的参数是结构化的，截断 JSON 会让参数失效）→ 如实标记
   `over_budget`。

第 3 步是本项目的设计：宁可让模型看到一段被截断的输入，也不要发出一个会被
API 拒绝的请求。裁剪会记在 `ContextView.truncated` 里。

### 5. 视图可观测

`ContextView` 携带：裁剪后的内容、原始/最终 token 数、丢弃条目数、应用的
策略名、是否截断、是否仍超预算。运行时把它记在
`ExecutionContext.last_context_view` 里——因此「这次请求为什么变短了」有
答案。

### 6. 策略是协议，实现可替换

`ContextStrategy` 协议只有一个方法：`apply(contents) -> contents`。要求是
**纯函数**：只依赖入参、不修改入参、不产生副作用。同一个输入永远得到同一个
输出——否则同一份历史在不同轮次会得到不同的呈现，模型行为无法复现。

### 7. 基础会话在运行时层

`SessionContext` 持有 `ExecutionContext`，跨多次运行累积对话。它放在
`core/agent` 而不是 `core/context`，因为它持有的 `ExecutionContext` 是
`core/agent` 的类型——放错位置会破坏第 1 条的依赖方向。

## 后果

- 好处：长会话不会撑爆窗口；裁剪不丢信息（存储侧完整）；预算可执行；
  裁剪过程可观测；策略可替换；不配置构建器时行为完全不变（既有调用方不受
  影响）。
- 代价：多一层抽象（存储 vs 呈现）；token 计数是估算不是精确值；规则式压缩
  不如 LLM 摘要能保留语义细节。
- 边界：**没有实现基于 LLM 的摘要压缩**（主教材 6.2.5 / Anthropic 的
  compaction）。理由：它需要一次模型往返，引入成本、延迟与失败语义；而且
  当前 `ContextStrategy` 协议是同步的，摘要策略需要异步变体。这是明确的
  后续议题，不是遗漏。
- 边界：token 计数用 `tiktoken`（OpenAI 系分词器），对 Anthropic 系模型是
  近似；回退的字符估算更粗。预算因此是**保守估计**，不是精确控制。
- 边界：滑窗保留「最早的用户消息」——对学习教练合理（原始诉求始终有效），
  但对「最新诉求才重要」的场景不合适，可通过 `keep_leading_user=False`
  关掉。
- 边界：`SessionContext` 只是内存形态。没有持久化、没有暂停恢复、没有人工
  确认——那些属于 Day 09「Session 与 HITL」。

## 补充说明（2026-11-19）

实现中确认的事实：

1. **「预算不是建议」这句话逼出了裁剪逻辑**。初版在单条内容超预算时选择
   「保留并标记 `over_budget`」，理由是「丢掉内容比超预算更糟」。但那等于
   承认预算可以被违反——一个会被静默违反的预算是 bug 温床。改为裁文本，
   并把裁剪记进视图。
2. **兜底裁剪的第一版有顺序错误**：先 `drop_unpaired` 再检查是否装得下，
   导致落单的 `tool_call` 被删掉后内容被清空。改为「先按预算从新到旧保留，
   最后统一修复配对」——配对修复只会减少内容，不会让结果重新超预算。
3. **规则式压缩比 LLM 摘要更适合做第一版**。Anthropic 明确指出过度压缩会
   丢失「subtle but critical context」，因此先做最轻的一层（清工具结果），
   把需要语义判断的那一层留到有真实用例时再做。
4. **`tiktoken` 的离线回退不是可选项**。它首次使用要下载 BPE 文件，本地
   首次调用耗时约 5 秒；离线环境会直接失败。回退路径加上失败记忆（不重复
   下载）之后，预算功能在离线 CI 里依然可用。

## 相关文档

- 接入说明：[`docs/engineering/memory.md`](../../engineering/memory.md)
- 决策依据：[`docs/architecture/decisions/0008-context-storage-and-presentation.md`](../../architecture/decisions/0008-context-storage-and-presentation.md)
- 学习笔记：[`docs/learning/topics/08-memory-and-context-engineering.md`](../../learning/topics/08-memory-and-context-engineering.md)
