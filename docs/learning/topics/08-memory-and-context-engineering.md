# Topic: Memory 与 Context Engineering

> Day 8 ｜ Phase 0 · Agent Engineering Foundation ｜ 主教材：Ch6：6.1～6.2 ｜ 外部参考：LangGraph Persistence ｜ 状态：已完成

## 1. Engineering Question

上下文超限时，Sliding Window、Token Counting、Compaction 三者的职责如何划分？

## 2. Primary Reading

- Book: Ch6：6.1～6.2
- Chapter / Section: Ch6 6.1「The anatomy of agent memory」、6.2「Managing context during execution」（6.2.1～6.2.6）
- 核心观点：
  - **6.2.1 Separating storage from presentation**（本 Topic 的地基）：优化策略改的是**发给模型的请求**，不是**执行上下文里的历史**。历史永远完整，裁剪只发生在「这一次发什么」这一层。
  - **6.2.2 Sliding window**：找到最早的用户消息并保留到它，再保留最近 N 条内容——原始诉求不能丢。
  - **6.2.3 Token counting**：用 `tiktoken` 估算，每条消息加 4 token 的固定开销，工具定义的 JSON 也要计入；模型没有专属分词器时回退到默认分词器。
  - **6.2.4 Compaction**：按工具名定制规则，把臃肿的调用参数与工具结果换成占位符（模板里可用调用参数做占位符）。**只换内容、不增删条目**，因此配对关系不变。
  - **6.2.5 Summarization**：用 LLM 把旧消息总结成一段摘要，保留最近 N 条；要记住上一次摘要的位置以避免重复总结重叠区间。
  - **6.2.6 Hierarchical context management**：`ContextOptimizer` 分层——先测量，超阈值就压缩，再测，还超就摘要。**策略是分级的，不是单一招**。

> **读法说明**：6.2.1～6.2.4、6.2.6 的细节可在参考实现的 listings 与代码里逐条核对。**6.2.5 在参考笔记本里只有标题、没有正文**——正文在书中，本地不可得；下面这条是从标题与相邻 listings 推断的**读法**，不是书中原文的转述，读时应以书为准。

## 3. Cross References

| 来源 | 核心观点 | 与主教材的关系 |
|---|---|---|
| Anthropic《Effective context engineering for AI agents》（2025-09） | 上下文是**有限资源**且边际收益递减（引 Chroma 的 *context rot*：token 越多，模型准确回忆的能力越下降）；目标是找到「**smallest possible set of high-signal tokens**」；**Compaction** 是「把接近上限的对话总结后重新开一个窗口」；**「One of the safest lightest touch forms of compaction is tool result clearing」**——清掉深处的工具调用与结果是最轻的一层；**just-in-time** 策略：只维护文件路径、查询词这类**轻量标识符**，让模型在运行时用工具去取 | 直接印证并**限定**了 6.2.4 的做法：规则式压缩正是「最轻的一层」，而 6.2.5 的 LLM 摘要对应 Anthropic 定义的主形态。just-in-time 的「保留标识符、丢掉内容」是本项目压缩模板的设计来源 |
| LangChain《Context Engineering》（2025-07） | 把上下文工程分成四类手段——**write / select / compress / isolate**；**Summarization 与 Trimming 是两回事**——「summarization typically uses an LLM to distill… trimming can often filter or prune… using hard-coded heuristics like removing older messages」；**Agent 的 state object 本身就是一种上下文隔离**——`messages` 字段暴露给模型，其余字段隔离待选；引用 Drew Breunig 的四种上下文失效模式：**Context Poisoning / Distraction / Confusion / Clash** | 印证了本项目「压缩（规则）优先于摘要（LLM）」的顺序选择，也印证了「草稿本不进模型上下文」的设计——那正是 LangGraph state 隔离的翻版。四种失效模式是威胁模型 T1/T5/T6 的另一种表述 |
| LangGraph Persistence（checkpointer / thread） | 用 checkpointing 把 agent state 在**一个 thread 内**的所有步骤间持久化；长期记忆跨 session 存取；每个节点里可以按需 fetch state，得到「每一步呈现哪些内容」的细粒度控制 | 补上了主教材 6.3（属 Day 09）之后的部分：LangGraph 把「存储」与「每步选取」都做成了框架能力。本项目的对应物是 `ExecutionContext`（存储）+ `ContextBuilder`（每步选取） |
| Chroma《context rot》研究（2025） | 上下文变长时，模型在其中的检索精度与推理表现下降，且**模型之间表现不同**——长上下文的退化不是悬崖而是梯度 | 解释了「为什么预算要被执行」：不是窗口满了才要裁，而是**装得下就已经在掉精度** |
| 官方工程指南（OpenAI / Anthropic 工具设计） | 工具描述即契约；工具数量膨胀会让模型选错工具；上下文里工具定义本身也占预算 | 说明「可见性过滤」既是安全措施也是上下文措施——无权的工具不给模型看，顺带减少了上下文噪声 |

## 4. Compare and Challenge

- **不同资料有哪些一致观点？**
  - **存储与呈现必须分开**（6.2.1、LangGraph 的 state 隔离、Anthropic 的「curation phase happens each time we decide what to pass to the model」）。
  - **上下文是有限预算，不是越多越好**（6.2.3 的 token 计数、context rot、Anthropic 的 attention budget）。
  - **压缩要分级**（6.2.6 的分层优化器、LangChain 的 write/select/compress/isolate 四分法、Anthropic 的「lightest touch」表述）。

- **哪些观点存在分歧？**
  - **压缩该用规则还是用 LLM**：主教材 6.2.4/6.2.5 与 Anthropic 的主形态都是 **LLM 摘要**；本项目本次只做了**规则式**压缩。
  - **裁剪时保什么**：参考实现的滑窗保留「最早的用户消息 + 最近 N 条」；本项目保留同样的一条，但**额外要求裁剪后配对完整**。
  - **token 计数要不要精确**：参考实现直接用 `tiktoken`，失败就抛异常；本项目加了**无网络的字符估算回退**。

- **分歧来自应用场景、设计目标还是技术演进？**
  - **规则 vs 摘要来自工程成熟度**：LLM 摘要需要一次模型往返——成本、延迟、失败语义都进来了，而且**摘要本身可能丢关键信息**（Anthropic 明确警告「overly aggressive compaction can result in the loss of subtle but critical context」）。本项目当前没有任何真实长会话的语料，调摘要提示词没有依据。先做最轻的一层，等有语料了再上第二层。
  - **配对要求来自本项目自身的约束**：ADR-0005 已经确立「每条 `tool_call` 都有对应的 `tool_result`」。参考实现没有这条约束，因此它的滑窗可以放心地直接切列表——**在我们这里不行**。同一段代码在两个项目里的正确实现不同，差异来自约束不同，不是谁写错了。
  - **离线回退来自部署环境**：本项目要能在离线 CI 里跑；参考实现是教学项目，不考虑这件事。

- **哪些结论缺少充分证据？**
  - **「裁剪不会降低回答质量」**：没有对照实验。本项目的裁剪是确定性的、免费的，但「省下的 token」是否值得「丢掉的细节」——没有数据。
  - **「保留最早的用户消息是对的」**：对学习教练合理（原始目标始终有效），但对「最新诉求才重要」的场景是错的。没有公开的通用结论。
  - **规则式压缩能省多少**：本项目的用例里工具结果占了大头，但真实的比例取决于工具形态。没有跨项目的统计。

- **哪些观点不适用于当前项目？**
  - **Summarization（6.2.5）**：不在本次范围（见 §5「有哪些替代方案」）。
  - **Sub-agent 架构**：Anthropic 与 LangChain 都把「上下文隔离」列为长时程任务的三大手段之一，但本项目是单智能体，引入子 Agent 是架构级决定。
  - **LangGraph 的 checkpointer**：它是持久化方案，属 Day 09「Session 与 HITL」；本次的 `SessionContext` 只是内存形态。

- **本项目的立场小结**：采纳「存储与呈现分离」「预算是被执行的」「压缩分级，先轻后重」「裁剪必须维护配对」四条；与主教材在**摘要的引入时机**上保留分歧并写明理由；与参考实现在**裁剪后配对**与**离线回退**上做本项目特有的修正——两者都源于本项目自己的约束，不是对参考实现的贬低。

## 5. Engineering Practice

### 实现了什么？

**1. Token Counting**（`core/context/tokens.py`）

- `TokenCounter` 统计一次请求的全部输入：对话内容 + 工具定义。
- 优先用 `tiktoken`（模型专属分词器 → 默认分词器）。
- **无网络回退**：`tiktoken` 首次使用要下载 BPE 文件（本地实测约 5 秒），离线 CI 会直接失败。回退到按字符估算——中日韩字符按 1 token，其余按 4 字符 1 token。估算**保守**（高估），不会让人以为内容比实际更小。
- **失败记忆**：加载失败后不再重试，避免每次调用都等一次下载超时。

**2. Compaction**（`core/context/strategies.py`）

- 按工具定制：结果模板（可用调用参数做占位符）、参数占位符。
- 通用兜底：超过阈值的工具结果与调用参数一律截断——规则表不可能列全所有工具。
- `protected_tools`：受保护的工具不参与压缩。用结构化输出时应当把 `final_answer` 放进去，它是最终产物。
- **只换内容、不增删条目**，因此配对天然不变。

**3. Sliding Window**（同上）

- 保留最早的用户消息（原始诉求）+ 最近 N 条。
- **裁剪后修复配对**：孤立的 `tool_call`（有调用无结果）与孤立的 `tool_result`（有结果无调用）都被移除。参考实现直接切列表会留下断裂的配对，厂商 API 会拒绝整个请求。

**4. Context Builder**（`core/context/builder.py`）

- 依次应用策略，每应用一个就重新计数，装得下就停。
- 仍超预算时**从最新的一条往回保留**，装不下的那条裁掉它的文本。
- 输出 `ContextView`：裁剪后的内容、原始/最终 token 数、丢弃条目数、应用的策略、是否截断、是否仍超预算。

**5. 运行时接入**

- `AgentConfig.context_builder`：设置后，运行时发给模型的是**按预算裁剪的视图**；执行上下文里的历史始终完整。
- `ExecutionContext.last_context_view`：记录最近一次呈现的统计，「这次请求为什么变短了」有答案。
- 不配置构建器时**行为完全不变**——既有调用方不受影响。

**6. 基础 Session Context**（`core/agent/session.py`）

- `SessionContext` 持有 `ExecutionContext`，跨多次运行累积对话。
- 两轮共享同一份执行上下文，因此第二轮的模型能看到第一轮的内容；草稿本也跨轮保留。
- 失败的轮次也计数，未被回答的问题留在历史里——下一轮模型能看到「上次这里没接上」。

### 为什么这样设计？

**为什么存储与呈现要分开？** 因为「丢弃」与「裁剪」的代价完全不同。丢弃不可逆——被丢掉的历史再也回不来，事后无法复盘；裁剪可逆——它只影响这一次请求，完整历史还在。分离之后，上下文管理从「有损操作」变成「投影操作」，因此策略可以随便换而不担心数据损失。

**为什么预算要真的被执行？** 一个可以被静默违反的预算是 bug 温床。初版在单条内容超预算时选择「保留并标记 `over_budget`」，理由是「丢掉内容比超预算更糟」——但那等于承认预算可以被违反。改为裁文本后，模型看到的是一段被截断的输入（可接受），而不是一个被 API 拒绝的请求（失败）。

**为什么先压缩后滑窗？** 压缩保住更多条目。工具结果往往是体积的大头，压掉它之后可能根本不需要丢掉任何一轮对话。

**为什么滑窗要保留最早的用户消息？** 对学习教练，原始学习目标（「帮我学闭包」）在整个会话里始终有效，而中间轮次的内容可以由压缩策略保留要点。`keep_leading_user=False` 可以关掉这个行为。

### 有哪些替代方案？

| 方案 | 未采用的理由 |
|---|---|
| **LLM 摘要压缩**（6.2.5） | 需要一次模型往返：成本、延迟、失败语义都进来了；摘要本身可能丢关键信息；且当前 `ContextStrategy` 协议是同步的，摘要策略需要异步变体。**没有真实长会话语料时无法调摘要提示词**——先做最轻的一层 |
| **子 Agent 隔离上下文** | Anthropic 与 LangChain 都推荐它处理长时程任务，但那是架构级决定，本项目是单智能体 |
| **直接截断历史**（不做配对修复） | 会留下断裂的 `tool_call`，厂商 API 拒绝整个请求 |
| **把裁剪做进运行时**（而不是独立模块） | 运行时是「模型 ↔ 工具」的节奏控制，不该知道 token 预算怎么算、该保留什么。独立模块让运行时只调一次 `build()` |
| **把 Session 放进 `core/context`** | 它持有 `ExecutionContext`（`core/agent` 的类型），放错位置会形成反向依赖 |
| **依赖精确 token 计数** | `tiktoken` 是 OpenAI 系分词器，对 Anthropic 系模型本就是近似；再叠加离线不可用，预算功能会整体不可用 |

### 测试结果如何？

- 新增 `tests/test_context.py`（43 个用例，含示例可运行性）与 `tests/test_session.py`（8 个用例）。
- 全量：**353 passed**（原 302 + 51）。
- 覆盖率：`core/context/` 四个模块与 `core/agent/session.py` 均 **100%**（行 + 分支）。
- **变异验证**（确认测试真的能抓住它们声称防住的问题）：

  | 变异 | 捕获的测试数 |
  |---|---|
  | 滑窗不修复配对 | 1 |
  | 预算不执行（单条超限时直接保留） | 3 |
  | 兜底裁剪先 `drop_unpaired` 再检查预算（第一版的顺序错误） | 3 |

### 实践结果是否支持阅读材料中的观点？

**支持，并有两处修正与一处限定。**

- 6.2.1「存储与呈现分离」——得到**完全支持**，而且是本 Topic 收益最大的一条。它不只是省 token：它让裁剪变成可逆操作，让策略可以替换，让过程可观测。
- 6.2.4「压缩」——得到**支持**，但 Anthropic 给了它一个更准确的位置：「the safest lightest touch forms of compaction」。规则式压缩（清工具结果、留标识符）正是这一层。它不是「不够好的摘要」，而是**摘要之前应该先做的那一步**。
- 6.2.2「滑窗」——得到**支持，但必须修正**。参考实现的滑窗直接切列表，会切断 `tool_call` 与 `tool_result` 的配对。本项目因为有 ADR-0005 的不变量，必须额外做配对修复。**同一段代码在两个项目里的正确实现不同**——差异来自约束不同。
- 6.2.5「摘要」——**限定**。它不在本次范围，且本次的实现经验说明了原因：摘要需要语料来调，没有语料时先做最轻的一层更诚实。
- 6.2.3「token 计数」——得到**支持，但必须补一条**：参考实现直接用 `tiktoken`，失败就抛异常。本项目实测它首次使用要联网下载 BPE 文件（约 5 秒），离线 CI 会直接失败——因此加了字符估算回退。预算是**估算**，这个限定必须在文档里说清楚，否则会给人「精确控制」的错觉。

## 6. Reflection

1. **今天最重要的知识是什么？**
   **「裁剪」与「丢弃」是两种完全不同的操作，而大多数实现在它们之间滑动。**
   丢弃不可逆——被丢掉的历史再也回不来，事后无法复盘；裁剪可逆——它只影响这一次请求，完整历史还在。把这个区分做实之后，一连串的设计选择都跟着确定了：策略放在存储之外（否则就是丢弃）、预算被真正执行（否则裁剪就是偷偷的丢弃）、每次裁剪都记进视图（否则无法判断丢弃了多少）。

   附带的一条：**「有限资源」的含义比「装不下」更严格**。context rot 的结论是——装得下的时候精度已经在掉了。所以预算不是「等到满了再裁」的保险，是**常态化的取舍**。

2. **哪个观点改变或修正了原来的理解？**
   原本以为「上下文管理」就是「历史太长时砍掉前面的」。读完 6.2.1 与 LangGraph 的 state 设计后，理解完全变了：核心不是**砍**，是**分**——存储与呈现是两个东西，砍只发生在呈现这一侧，而且是可逆的。

   另一处修正：原本把「摘要」当成上下文管理的主要手段。Anthropic 的表述让我重新排序——**摘要之前应该先做「清工具结果」这一层**。摘要是有损的语义判断，而工具结果占了体积的大头，先清它既便宜又确定。

3. **当前实现还有哪些局限？**
   - **没有摘要压缩**——语义细节会丢。需要时再上，且要一次模型往返。
   - **`ContextStrategy` 协议是同步的**——摘要策略需要异步变体。这是加摘要之前必须先决定的协议改动，越晚改代价越大。
   - **token 计数是估算**——`tiktoken` 对 Anthropic 系模型是近似，字符估算更粗。预算是保守估计，不是精确控制。
   - **滑窗保留「最早的用户消息」是硬编码的启发式**——对学习教练合理，对别的场景不一定。
   - **会话不持久化**——进程退出即丢；没有暂停恢复、没有人工确认（属 Day 09）。
   - **没有跨会话记忆**——属 Day 10。
   - **没有评估裁剪是否损害了回答质量**——上下文管理的效果目前无法度量。属 Day 14 与 Day 28。

## 7. Output

- Code / PR:
  - `src/agent_learning_coach/core/context/tokens.py`（新增：token 计数与离线回退）
  - `src/agent_learning_coach/core/context/strategies.py`（新增：Sliding Window 与 Compaction）
  - `src/agent_learning_coach/core/context/builder.py`（新增：Context Builder 与 ContextView）
  - `src/agent_learning_coach/core/context/__init__.py`（新增：导出公开符号）
  - `src/agent_learning_coach/core/agent/session.py`（新增：基础 Session Context）
  - `src/agent_learning_coach/core/agent/types.py`（`AgentConfig.context_builder`、`ExecutionContext.last_context_view`）
  - `src/agent_learning_coach/core/agent/runtime.py`（`_think` 走预算视图）
  - `src/agent_learning_coach/core/agent/__init__.py`（导出 `SessionContext`）
  - `examples/context_budget.py`（可运行演示：预算是被执行的，且存储侧保持完整）
- Tests: `tests/test_context.py`（43 个用例）、`tests/test_session.py`（8 个用例）
- Example: `examples/context_budget.py`（预算是被执行的，且存储侧保持完整）
- ADR: `docs/architecture/decisions/0008-context-storage-and-presentation.md`
- Article Notes: `docs/articles/memory-and-rag.md`、`docs/engineering/memory.md`
- Unresolved Questions:
  - 摘要压缩要不要加？若加，`ContextStrategy` 协议是否要改成异步？（越晚决定代价越大）
  - 滑窗保留「最早的用户消息」是否应该改成可配置的更一般的保留策略（如「保留所有未回答的问题」）？
  - 裁剪是否损害回答质量——怎么度量？会不会出现「检索到了最相关的段落，却被滑窗丢掉」这种情况？
  - `SessionContext` 与 Day 09 的持久化边界怎么切？会话状态落盘的最小集合是什么？
