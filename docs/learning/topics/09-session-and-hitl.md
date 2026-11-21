# Topic: Session 与 HITL

> Day 9 ｜ Phase 0 · Agent Engineering Foundation ｜ 主教材：Ch6：6.3 ｜ 外部参考：LangGraph Persistence ｜ 状态：已完成

## 1. Engineering Question

Session 的持久化边界在哪里？Pause / Resume 时哪些状态必须落盘，才能保证中断后可精确恢复？

## 2. Primary Reading

- Book: Ch6：6.3
- Chapter / Section: Ch6 6.3「Continuous execution: Session and state management」（6.3.1～6.3.8）
- 核心观点：
  - **6.3.1 Session 类**：会话是「跨多次 `run()` 调用持续存在的对话容器」——`session_id`、`user_id`、`events`、`state`、`created_at` / `updated_at`。**会话与一次执行是两件事**：执行有明确的结束，会话没有。
  - **6.3.2 SessionManager**：`create` / `get` / `save` 三个基本操作，加上 `get_or_create` 这个便利方法。存储是可替换的——抽象成协议，实现换起来才不痛。
  - **6.3.3 接入 Agent**：`ExecutionContext` 增加 `session` 与 `session_manager`；`run()` 先 `get_or_create` 会话、结束时 `save`。
  - **6.3.4 多轮对话**：有了会话，同一个 `session_id` 的第二次 `run()` 能看到第一次的对话。
  - **6.3.5 工具确认的数据结构**：`PendingToolCall`（待确认的调用 + 确认提示）、`ToolConfirmation`（人的决定：批准与否、可选的修改参数、理由）、`AgentResult` 增加 `status="pending_confirmation"` 与 `pending_tool_calls`。
  - **6.3.6 扩展工具以支持确认**：工具契约上带 `requires_confirmation` 与 `confirmation_message_template`。
  - **6.3.7 在 Agent 里实现暂停与恢复**：运行时遇到需确认的工具时**不执行**，把待确认的调用记下来，运行以 `pending_confirmation` 返回；下一次 `run()` 带着人的决定进来，先落实这些决定（批准的执行、拒绝的回填一条错误结果），然后继续循环。
  - **6.3.8 完整的 HITL 流程**：暂停 → 人来决定 → 恢复 → 循环继续。

> **读法说明**：6.3.1～6.3.8 的代码形态可在参考实现的 `notebooks/ch06/` 快照与 `scratch_agents/memory/session.py` 里逐条核对。**6.3 的散文正文在书中，本地不可得**；上面这些是从参考实现的 listings 与代码反推的**读法**，不是书中原文的转述，读时应以书为准。

## 3. Cross References

| 来源 | 核心观点 | 与主教材的关系 |
|---|---|---|
| LangGraph Persistence（checkpointer / thread） | 用 **checkpointer** 把 agent state 在一个 thread 内的所有步骤间持久化；`thread_id` 标识一条会话；每个节点里可以按需 fetch state，得到「每一步呈现哪些内容」的细粒度控制；长期记忆跨 session 存取 | 印证了 6.3.1/6.3.2 的会话抽象，并**补上了主教材没有的一层**：LangGraph 把「存哪儿」做成了框架能力（checkpointer 可换 Postgres/SQLite/内存），而本项目把它做成 `SessionStore` 协议 + 两种实现 |
| OWASP *AI Agent Security Cheat Sheet* | 工具授权中间件要「**Check and consume the approval in one atomic step** immediately before execution so concurrent or repeated requests cannot reuse it」；「Changes to the target or parameters require new approval」；「**Fail closed** for unknown tools or missing approval requirements」；高风险动作要「Separate decision-making from execution」 | 直接决定了本项目的两条规则：**缺失批准按拒绝处理**（fail closed）与**改参数要重新批准**（`modified_arguments` 是「这次批准针对这一次修改后的调用」，不是对另一次调用的批准）。「决策与执行分离」正是本项目把批准检查放在运行时而非模型侧的理由 |
| Anthropic《Building effective agents》/ 工具设计实践 | 工具应有清晰的副作用描述；高风险工具要谨慎暴露；人的批准应当针对**具体动作**而不是笼统授权 | 印证了 `confirmation_message` 模板的设计——批准界面必须让人看得懂**这次要做什么**，否则批准就退化成点击 |
| 论文 / 研究资料 | Reflexion（Shinn et al., 2023）与 Generative Agents（Park et al., 2023）都把「跨轮次保存自己产生的状态」作为长时程智能体的基础；Generative Agents 的 *memory stream* 强调每条记忆带**可检索性分数**与**重要度** | 说明「会话」不只是对话历史——它还承载智能体自己产生的中间结论。本项目的 `state`（草稿本）正是这个位置：跨轮保留、模型看不到。检索与遗忘属 Day 10 |
| 参考实现的缺陷（实测） | Ch6 快照的 `Listing 6.11` 让事件写进 `session.events`，而 `_prepare_llm_request` 读 `context.events`——**多轮会话里模型完全看不到历史**（实测 `ctx.events` 0 条、`session.events` 1 条、发给模型的内容为空） | 这是本项目 Topic 08「存储只能有一个权威」的直接延续。**同一份数据有两个存放处，就一定会有读错的那一处** |

## 4. Compare and Challenge

- **不同资料有哪些一致观点？**
  - **会话与执行是两件事**（6.3.1、LangGraph 的 thread、ADR-0006 的「一次运行 ≠ 一次会话」）。
  - **存储是可替换的**（6.3.2 的抽象、LangGraph 的 checkpointer、本项目的 `SessionStore` 协议）。
  - **高风险动作要人批**（6.3.5～6.3.8、OWASP 的 HITL 控件、Anthropic 的工具设计）。
  - **恢复后仍然是原来那个运行**——这要求落盘的不只是对话。

- **哪些观点存在分歧？**
  - **事件该存在哪儿**：参考实现（Listing 6.11）让会话与执行上下文各存一份事件；本项目**只有一份**。
  - **批准缺失怎么办**：参考实现 `_process_confirmations` 里，`confirmation` 为 `None`（没有对应决定）与「明确拒绝」走**同一条分支**——结果都是拒绝。本项目把两者**区分开**：没有决定时给出「没有收到该调用的批准」，明确拒绝时给出人写的理由。语义上更清楚，观测上也能区分「人没答」与「人说不」。
  - **批准时能不能改参数**：参考实现支持 `modified_arguments`；本项目也支持，但**明确它是「针对这一次修改后的调用」的批准**，不是对另一次调用的批准（OWASP 的 "Changes to the target or parameters require new approval"）。

- **分歧来自应用场景、设计目标还是技术演进？**
  - **单存储来自本项目自己的不变量**：ADR-0005 确立了「每条 `tool_call` 都有对应的 `tool_result`」。两份事件存储意味着「模型看到的那份」和「落盘的那份」可能不是同一份——那不变量就无法保证。
  - **区分「没答」与「拒绝」来自观测需求**：学习教练的场景里，「用户没看到通知」与「用户明确说不」需要不同的后续处理（前者可以再提醒，后者应该换方案）。
  - **fail closed 来自安全默认值**：OWASP 明确要求，且「缺少批准就执行」会让整套批准机制失去意义。

- **哪些结论缺少充分证据？**
  - **「整步暂停」优于「按调用暂停」**：本项目选择整步暂停，理由是转录不变量与厂商 API 的约束；但「多工具一步里，只暂停其中一部分」在别的模型上是否真的会被拒绝，取决于厂商实现。OpenAI 兼容格式通常更宽松。
  - **「恢复后仍受整体超时约束」是否合理**：本项目选择恢复时重新计时（人不在的时候不该把运行算成超时），但这也意味着「暂停-恢复」可以无限延长运行。真实的审批系统通常有独立的审批超时。
  - **待审批状态放在事件之外**：本项目把待确认的调用单独存在 `ExecutionContext.pending_approvals`，而不是作为事件。哪种更好取决于审计要求——事件流更统一，但会让「待审批」看起来像一次普通的工具调用。

- **哪些观点不适用于当前项目？**
  - **LangGraph 的 checkpointer 家族**：本项目是库不是框架，不内置数据库适配；但 `SessionStore` 协议正是为了让应用层接自己的存储。
  - **Generative Agents 的记忆流与检索分数**：那是长期记忆的设计，属 Day 10。
  - **多智能体之间的审批**：不适用（本项目是单智能体）。

- **本项目的立场小结**：采纳「单存储」「落盘恢复所需的全部状态」「缺失批准即拒绝」「批准是运行时约束」四条；与参考实现在**事件存储位置**与**「没答」与「拒绝」的区分**上做本项目特有的修正——前者源于本项目自己的不变量，后者源于观测需求。

## 5. Engineering Practice

### 实现了什么？

**1. Session State**（`core/agent/session.py`）

- `SessionContext` 持有**一个** `ExecutionContext`，跨多次运行累积对话。
- `SessionState` 是可序列化的状态快照——`to_dict()` / `from_dict()`。

**2. 基础状态持久化**（同上）

- `SessionStore` 协议：`load` / `save` / `delete`。
- `InMemorySessionStore`：开发与测试。
- `JsonFileSessionStore`：一个会话一个 JSON 文件；写文件先写临时文件再改名——**改名是原子的**，不会恢复出「写到一半」的会话。文件 I/O 放到线程里，不阻塞事件循环。

**3. Pause / Resume**（`core/agent/runtime.py`）

- 工具契约上声明 `requires_confirmation` 与 `confirmation_message`。
- 运行时遇到需确认的工具：**整步暂停**（一步的工具调用要么全执行，要么全暂停）。
- `AgentRuntime.resume(context, approvals)` 从暂停的那一步继续。

**4. Approval**

- `PendingApproval`（待确认的调用 + 确认提示）、`ApprovalDecision`（批准与否、可选的修改参数、理由）。
- 批准的照常执行（可用 `modified_arguments`）；拒绝的回填一条「用户拒绝执行该工具（理由）。不要再尝试这个操作」。
- **没有决定的按拒绝处理**（fail closed）。
- 暂停的运行**不能再 `run()`**——否则请求里会带着没有结果的 `tool_call`，厂商 API 会拒绝整个请求。

**5. 循环计数上移到上下文**

`tool_call_count`、`consecutive_errors`、`call_counts` 从循环的局部变量移到
`ExecutionContext`。否则恢复一次运行就会重置它们——**暂停-恢复会成为绕过预算
与停止条件的后门**。

### 为什么这样设计？

**为什么只有一份事件存储？** 因为两份就一定会有读错的那一处。参考实现的
Listing 6.11 让事件写进 `session.events`，而请求构造读 `context.events`——
实测结果是**多轮会话里模型完全看不到历史**。本项目的选择是：`SessionContext`
持有**一个** `ExecutionContext`，事件只有一份，`contents` 展平的就是它。恢复、
持久化、审计读的都是同一份数据。

**为什么整步暂停而不是按调用暂停？** 部分执行会让转录里出现「有调用没结果」
的断裂——主流厂商 API 会**拒绝整个请求**，不是降级。整步暂停保证转录在每一个
时刻都自洽。代价是同一步里不需要确认的工具也要等——这个代价换来的是转录
正确性。

**为什么缺失批准按拒绝？** 缺少批准就执行，等于让「批准」变成默认开启——那
就不是批准机制了。OWASP 对工具授权中间件的要求也是 "Fail closed for unknown
tools or missing approval requirements"。

**为什么计数要上移？** 因为它们是运行状态，而运行状态必须能被恢复。留在局部
变量里等于宣布「恢复是一次全新的运行」——那预算与停止条件就都能被绕过。

### 有哪些替代方案？

| 方案 | 未采用的理由 |
|---|---|
| 会话与执行上下文各存一份事件（参考实现） | 两份存储必然读错一处；实测会丢失全部多轮历史 |
| 按调用暂停（部分执行） | 转录里出现「有调用没结果」的断裂，厂商 API 拒绝整个请求 |
| 缺失批准时默认执行 | 「批准」变成默认开启，机制失去意义 |
| 把待审批状态作为事件 | 会让「待审批」看起来像一次普通的工具调用，审计时容易混淆 |
| 用 `AgentStep` 而非事件作为持久化主体 | `AgentStep` 不含用户输入与工具结果，重建不了对话 |
| 内置数据库存储 | 本项目是库不是框架；`SessionStore` 协议让应用层接自己的存储 |
| 恢复时沿用原整体超时 | 人不在场的时间会把运行算成超时，不合理 |

### 测试结果如何？

- 新增 `tests/test_hitl.py`（24 个用例），扩展 `tests/test_session.py`（+5 个）。
- 全量：**382 passed**（原 353 + 29）。
- 覆盖率：`core/agent/session.py`、`core/agent/runtime.py`、`core/agent/types.py`、
  `core/tools/types.py` 均 **100%**（行 + 分支）。
- **变异验证**：

  | 变异 | 捕获的测试数 |
  |---|---|
  | 恢复时执行的调用不计入预算 | 1 |
  | 缺失决定也执行（不 fail closed） | 2 |
  | 部分执行（不整步暂停） | 16 |
  | 暂停的运行允许再 `run()` | 1 |

  第四次变异第一次跑时**没有被捕获**——排查发现是变异脚本的替换文本与格式化
  后的代码不匹配，变异根本没发生。给替换加断言之后，变异才真正生效，而且
  暴露出那个测试是**因为错误的原因**通过的（脚本耗尽抛的是 `AssertionError`
  而不是 `AgentError`）。加固后它才真的钉住了这条性质。

### 实践结果是否支持阅读材料中的观点？

**支持，并有两处修正与一处限定。**

- 6.3.1～6.3.4（会话与存储）——得到**完全支持**。会话与执行分离、存储可替换
  这两条都成立；`SessionStore` 协议 + 两种实现是对 6.3.2 的直接落地。
- 6.3.5～6.3.8（HITL）——得到**支持**，但有三处要改：
  1. **Listing 6.11 的双存储**不能照搬——它会让多轮历史全丢。
  2. **缺失决定与明确拒绝要分开**——参考实现把两者当同一件事。
  3. **恢复路径要自己记账**——否则恢复不消耗预算，暂停-恢复就成了后门。
- 6.3.7「暂停与恢复」——得到**限定**：主教材只说「下一次 `run()` 带着决定进来」，
  但没说**状态必须落哪些**。本项目的答案是：事件、草稿本、用量与成本、步数、
  三项循环计数、待审批。少一项，恢复出来的就不是原来那个运行。

## 6. Reflection

1. **今天最重要的知识是什么？**
   **「可恢复」是一个比「可持久化」强得多的要求。** 能存下来不等于能恢复——恢复
   要求存的东西**恰好足够**，多存浪费、少存出错。而「恰好足够」这件事没法靠
   感觉判断，只能靠一个问题逼出来：**如果这一项丢了，恢复出来的运行和原来那个
   是同一个吗？** 这个问题一问，循环计数立刻跳出来：它显然不是「聊天记录」，
   但它同样是运行状态，丢了就等于预算失效。

2. **哪个观点改变或修正了原来的理解？**
   原本以为「持久化边界」是个存储问题（存哪儿、存多久、怎么序列化）。做完之后
   理解变了：它是个**语义**问题——边界由「恢复出来必须还是同一个运行」这个要求
   决定，存储只是实现。
   另一处修正：原本以为「批准」是个 UI 问题（弹个窗让人点）。读 OWASP 才发现
   它的实质是**默认安全**问题——「缺失批准怎么办」的答案决定了这是不是一套批准
   机制。点一个「是」很容易，难的是保证没点的时候什么都不发生。

3. **当前实现还有哪些局限？**
   - **没有并发控制**——两个进程同时恢复同一个会话会互相覆盖。生产用需要乐观锁
     或租约。
   - **没有审批过期机制**——几天前的待批准调用恢复时仍然有效。
   - **没有多租户隔离**——`user_id` 只是记录，没有据此做访问控制。
   - **`AgentStep` 不落盘**——恢复后的 `steps` 只覆盖本次运行段落。
   - **恢复时整体超时重新计时**——人不在场的时间不计入超时。这对人友好，但也意味
     着运行可以被无限延长。
   - **落盘格式与运行时类型耦合**——`ExecutionContext` 加字段要同步
     `SessionState.to_dict` / `from_dict`，两处漏一处就是静默丢数据。
   - **没有端到端的「暂停-恢复」示例**——目前只有测试覆盖。

## 7. Output

- Code / PR:
  - `src/agent_learning_coach/core/tools/types.py`（`requires_confirmation`、
    `confirmation_message`、`render_confirmation`）
  - `src/agent_learning_coach/core/agent/types.py`（`PendingApproval`、
    `ApprovalDecision`、`pending_approval` 状态、`ExecutionContext` 的循环计数与
    待审批）
  - `src/agent_learning_coach/core/agent/runtime.py`（整步暂停、`resume()`、
    fail closed、恢复时记账）
  - `src/agent_learning_coach/core/agent/session.py`（`SessionState`、`SessionStore`
    协议、`InMemorySessionStore`、`JsonFileSessionStore`、`SessionContext` 的
    save/load）
  - `src/agent_learning_coach/core/agent/__init__.py`（导出新符号）
- Tests: `tests/test_hitl.py`（24 个用例）、`tests/test_session.py`（+5 个）
- ADR: `docs/architecture/decisions/0009-session-persistence-and-hitl.md`
- Article Notes: `docs/articles/memory-and-rag.md`、`docs/engineering/memory.md`
- Unresolved Questions:
  - 并发恢复怎么防？乐观锁（版本号）还是租约（TTL 持有）？
  - 审批要不要过期？过期后恢复时按拒绝处理，还是按「人没答」处理？
  - 待审批状态要不要也进事件流，以便审计时看到完整时间线？
  - `SessionState` 与 `ExecutionContext` 的字段耦合怎么消除——能不能让状态对象
    自己负责序列化？
  - 端到端的暂停-恢复示例要不要补一个可运行的？
