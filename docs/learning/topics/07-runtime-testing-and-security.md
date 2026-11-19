# Topic: Runtime 测试与安全边界

> Day 7 ｜ Phase 0 · Agent Engineering Foundation ｜ 主教材：Ch4：4.8；Ch8：8.1～8.2 ｜ 外部参考：OWASP Agentic AI Security ｜ 状态：已完成

## 1. Engineering Question

Agent Loop 的测试如何避免真实调用 LLM？Tool 权限检查应放在 Dispatcher 还是 Runtime 层？Sandbox 的最小威胁模型是什么？

## 2. Primary Reading

- Book: Ch4：4.8；Ch8：8.1～8.2
- Chapter / Section: Ch4 4.8「Testing with the GAIA benchmark」；Ch8 8.1「Giving agents a computer」、8.2「Connecting code environments」
- 核心观点：
  - **4.8 测的是「Agent 能不能解题」**（据参考实现 Listing 4.30–4.34）：在 GAIA level-1 验证集上取 10 题，接真实模型（`gpt-5.5`）与真实 MCP 工具（`tavily-mcp`），`max_steps=15`，用 `asyncio.Semaphore(3)` 限并发，用 `output_type=GaiaOutput` 把答案收敛成结构化输出。
  - **4.8 调试靠执行轨迹**（Listing 4.34）：拿到一次运行后 `display_trace(result.context)` 把上下文摊开看。**轨迹是调试 Agent 的主要手段**——Agent 的行为是「模型 + 工具 + 上下文」的乘积，只看最终输出看不出哪一步歪了。
  - **8.1.1 预定义工具的局限**：每个能力一个工具，工具数量随需求组合爆炸；模型在「选哪个工具」上耗尽注意力。代码执行让模型用控制流表达复杂逻辑。
  - **8.1.3 代码动作的有效性**：代码表达力强，但代价是**要执行任意代码**。
  - **8.2.1 为什么必须要沙箱**：执行模型生成的代码，就等于给了它一台电脑。沙箱把「这台电脑」关起来。
  - **8.2.2 E2B 的用法**（Listing 8.5–8.8）：`Sandbox.create(timeout=300, envs={...})` 建沙箱、`sandbox.run_code(code)` 执行、`execution.error` 判错、`sandbox.kill()` 销毁；沙箱内**状态跨多次执行保留**。
  - **8.2.3 把沙箱接进 Agent**（Listing 8.9–8.13 与参考实现代码）：`ExecutionContext` 增加 `code_env` 字段承载沙箱生命周期，`Agent` 增加 `execute_python` 工具，构造与销毁在 `run()` 的 `try/finally` 里。

> **读法说明**：4.8、8.2.2、8.2.3 的每处细节都在参考实现的 listings 与代码里逐条核对过。**8.1.1、8.1.3、8.2.1 在参考笔记本里只有标题、没有正文**——正文在书中，本地不可得。因此这三条是从**小节标题与相邻 listings 推断出的读法**，不是书中原文的转述，读时应以书为准。

## 3. Cross References

| 来源 | 核心观点 | 与主教材的关系 |
|---|---|---|
| OWASP *Agentic AI — Threats and Mitigations* v1.0（2025-02） | 给出 T1–T15 的 Agentic 威胁分类与逐条缓解措施：T1 Memory Poisoning、T2 Tool Misuse、T3 Privilege Compromise、T4 Resource Overload、T5 Cascading Hallucination、T6 Intent Breaking & Goal Manipulation、T7 Misaligned & Deceptive Behaviors、T8 Repudiation & Untraceability、T9 Identity Spoofing & Impersonation、T10 Overwhelming Human in the Loop、T11 Unexpected RCE and Code Attacks | 提供了**分类骨架**。8.2.1「为什么需要沙箱」在 T11 里得到正式表述：「Restrict AI code generation permissions, **sandbox execution**, and monitor AI-generated scripts」；4.8 的执行轨迹对应 T8「Implement comprehensive logging… to ensure accountability and traceability」 |
| OWASP *AI Agent Security Cheat Sheet* | 「**Enforce authorization in the execution component, outside the agent's context**」；「Implement **per-tool permission scoping**（read-only vs. write, specific resources）」；「**Fail closed** for unknown tools or missing approval requirements」；授权与执行要「**atomically** check and consume the approval immediately before execution」，防止并发或重放复用批准 | 直接支持本项目 ADR-0007 的两个核心决策：**检查点放在 Dispatcher（执行组件）而不是 Runtime（agent context）**，以及按作用域授权。原子消费批准这一点也进了 HITL 设计 |
| Anthropic《Building effective agents》（2024-12，含 Appendix 2「Prompt engineering your tools」） | 「it is therefore crucial to design **toolsets and their documentation** clearly and thoughtfully」；好的工具定义应含示例用法、边界情况、输入格式要求，并与其它工具有「clear boundaries」；「**Poka-yoke** your tools. Change the arguments so that it is harder to make mistakes」；Agent 自主性带来「higher costs, and the potential for **compounding errors**」，因此「We recommend **extensive testing in sandboxed environments**, along with the appropriate guardrails」；并建议包含「stopping conditions (such as a maximum number of iterations)」 | 直接支持本项目的三项具体设计：① **参数校验就是 Poka-yoke**——把参数设计成「不容易传错」，校验器再兜一层；② **沙箱与停止条件**（`max_steps`）是官方推荐做法，不是本项目自创；③ 「工具描述即契约」印证可见性过滤的价值——工具越多、描述质量对决策的影响越大 |
| MCP 规范（2025-06-18，Tools） | 工具的 `inputSchema` 由 Server 提供；规范要求「For trust & safety and security, there **SHOULD** always be **a human in the loop with the ability to deny tool invocations**」；并明确「clients **MUST** consider **tool annotations to be untrusted** unless they come from trusted servers」 | 支撑本项目的三个具体决定：① 参数 schema 必须校验，且 **schema 本身可能畸形**（因此不能让它打断运行）；② 远端 MCP 工具按 `mcp:<命名空间>` 授予作用域，一个作用域关掉整个 Server；③ **权限不能取自 Server 自报的任何字段（含 annotations）**——所以作用域由客户端按命名空间自行分配，而不是让 Server 声明 |
| 论文 / 研究资料 | 间接提示注入（indirect prompt injection）：外部内容里的指令会被模型当作指令执行（Greshake et al., *Not what you've signed up for*, 2023）；AgentDojo（2024）提供可控的 Agent 攻击基准 | 解释了威胁模型里 **T6 未缓解** 的成因：**工具结果与模型输出在上下文里不可区分**，这是结构性的而非提示词工程问题。也说明「Agent 的输入不再只是用户输入」这一根本变化 |

## 4. Compare and Challenge

- **不同资料有哪些一致观点？**
  - **授权必须在执行组件里做，不在 Agent 的上下文里做**——OWASP Cheat Sheet 的原话与本项目 ADR-0007 的结论是同一件事。
  - **最小权限**：只授予任务必需的权限，按工具粒度细分读写（Cheat Sheet 的 per-tool permission scoping；本项目的 `ScopePolicy`）。
  - **执行不可信代码必须隔离**（8.2.1 与 T11）。
  - **必须有审计轨迹**（4.8 的 `display_trace` 与 T8）。

- **哪些观点存在分歧？**
  - **测试该用真实模型还是替身**：4.8 用真实模型跑 GAIA 验证集；本项目用脚本化替身跑确定性用例。
  - **沙箱里能不能有凭证**：参考实现把 `TAVILY_API_KEY` 传进沙箱（Listing 8.6 的 `envs=`），因为沙箱内的工具要自己调外部 API；本项目的设计主张沙箱**不应持有凭证**，应由宿主侧代理代持。
  - **输入净化的权重**：OWASP 建议在把外部内容放进上下文前做净化；本项目认为纯黑名单必然被绕过，只能作为多层防御中的一层。

- **分歧来自应用场景、设计目标还是技术演进？**
  - **测试方式来自目标不同**：GAIA 测的是「**效果**」——模型选不选得对工具、答不答得对题。替身测的是「**机制**」——超时有没有中断、权限有没有拦住、失败有没有被回填。效果测试每次跑都可能不同（模型更新、温度、并发），**不能当回归测试**；机制测试必须确定性、可进 CI。两者不可替代：GAIA 是发布前的验收，替身是每次提交的守门人。本项目现在只有后者——这是明确的缺口，不是「选了后者」。
  - **凭证归属来自信任模型**：参考实现选择「沙箱自带凭证」，代价是沙箱逃逸即凭证泄露；本项目选择「宿主代理代持」，代价是多一跳、实现更复杂。两者取决于「你更能接受哪种失败」——本项目面向学习者，数据面窄但使用者多，选保守的一侧。
  - **净化权重来自对「防御纵深」的不同估计**：OWASP 的建议覆盖面广、强调纵深；本项目担心黑名单带来的**虚假安全感**——它挡得住教科书攻击，挡不住稍作改写的攻击，因此不能让它成为唯一的一层。

- **哪些结论缺少充分证据？**
  - **「沙箱能挡住注入」**没有充分证据。沙箱限制的是**后果**，不是**意图**；被注入的模型仍然可以在沙箱内做它被允许做的事。
  - **「让模型看不见无权工具会更安全」**缺少对照实验。OWASP 强调最小权限，但「隐藏」与「拒绝」哪个对最终成功率影响更大，没有公开数据。
  - **GAIA 的分数能否代表产品质量**：GAIA 测的是通用助理能力，与「学习教练」的多轮交互、个性化和知识掌握度判断关系很弱。**不能拿 GAIA 分数论证本项目的效果**。

- **哪些观点不适用于当前项目？**
  - **GAIA 作为效果基准**：本项目是学习教练，交互形态是多轮、目标是掌握度而非单次答对。GAIA 可以作为**冒烟**（系统能跑通），不能作为**验收**。
  - **代码执行（8.1、8.3、8.4）**：产品路线图里没有「运行学习者代码」的需求，因此沙箱（L3）现在做就是为想象中的需求写代码。沙箱设计保留，代码不实现。
  - **多智能体相关的威胁（T12–T14）**：本项目是单智能体，不适用。

- **本项目的立场小结**：采纳「授权在执行组件」「按工具粒度授权」「审计轨迹」「代码执行需隔离」四条；与 OWASP 在**输入净化的权重**上保留分歧并写明理由；与参考实现在**测试方式**与**沙箱凭证归属**上保留分歧并写明各自的代价；并明确区分**Agent Evaluation** 与 **Learning Evaluation**——前者属 Phase 0 范围，后者属 Phase 5，现在不预设基准。

## 5. Engineering Practice

### 实现了什么？

**1. Tool 权限检查（Dispatcher 层）**

工具在契约里声明所需作用域，策略持有已授予作用域，工具所需是授予的**子集**才放行：

```python
ToolDefinition(name="read_notes", ..., scopes=("fs:read",))
ScopePolicy(granted=frozenset({"fs:read"}))
```

- 检查点在 `ToolDispatcher.dispatch`——**唯一执行边界**。本地工具、MCP 工具、`final_answer` 工具全部经过它。
- 运行时只做**可见性**过滤：`allowed_specs()` 给模型的工具清单里没有无权工具。
- 权限检查在**参数校验之前**：被拒的工具连参数都不校验，不向无权的一方泄露契约细节。
- 拒绝归一化成错误结果回填给模型（`ToolPermissionError`），模型可以改用别的工具。
- 远端 MCP 工具由 `discover_mcp_tools` 自动授予 `mcp:<命名空间>` 作用域——**一个作用域关掉整个不可信 Server**。
- **远端工具的权限由客户端分配，不取自 Server**：MCP 规范明确「clients MUST consider tool annotations to be untrusted unless they come from trusted servers」。因此 `mcp:<命名空间>` 是**我们**按 Server 分配的——Server 能提供的只有 `inputSchema`（必须校验，且可能畸形）与 `annotations`（不可信，不用于授权判断）。这条约束是「未声明作用域 = 恒放行」在远端场景下的具体解法。

**2. 契约防护（修了一个真实缺陷）**

工具的 `inputSchema` 由远端 MCP Server 提供，可能畸形。`{"type": "bogus"}` 这类 schema 会让 `jsonschema` 抛 `UnknownType` / `TypeError` 等**非校验类异常**，此前它们从 `validate_arguments` 逃出 `dispatch()` 的错误归一化，直冲运行时——**一个远端 Server 的畸形 schema 就能让整个 Agent 运行崩溃**。现在收敛成 `ToolSchemaError`，归一化成错误结果回填。

**3. Runtime Security Tests（33 个用例）**

覆盖权限、非法参数、超时与失败、Agent Loop 异常四组。

### 为什么这样设计？

**权限检查为什么不放 Runtime？** 因为 Dispatcher 是唯一的执行边界。检查点只有一个，就不存在「新增一条执行路径忘了检查」这种 fail-open 漏洞——这是安全系统里最危险的失败模式。放在 Runtime 的话，Runtime 必须知道每个工具的来源与危险程度。

**为什么用作用域（capability）而不是角色（role）？** 角色模型要维护「角色 → 权限」映射表，还要处理继承与冲突；能力模型只需要「本次授予了哪些能力」这一份清单，且天然与「本次会话允许做什么」对齐。

**为什么未声明作用域的工具恒放行？** 这是声明式授权绕不开的代价——工具作者忘了声明等于默认公开。替代方案是隐式授权（默认拒绝所有未声明的工具），但那会让「写个普通工具」变成三行配置的事，实践中必然有人绕过。选显式声明，并把远端工具的声明**自动化**（MCP 命名空间自动补作用域）。

### 有哪些替代方案？

| 方案 | 未采用的理由 |
|---|---|
| 权限检查放 Runtime | 存在 fail-open 路径；且 Runtime 要知道每个工具的危险程度，破坏「运行时不了解工具」的边界 |
| 角色 / RBAC 模型 | 要维护角色映射表与继承规则；对「本次会话授予什么」这个问题过重 |
| 隐式默认拒绝（未声明即不可用） | 接入成本高，实践中必然被绕过；显式声明 + 远端自动化更可持续 |
| 按工具名白名单 | 挡不住「同一个工具被诱导传入恶意参数」；作用域能表达读写差异 |
| 沙箱作为唯一防线 | 沙箱限制后果不限制意图；且不能替代授权——没授权的工具不该启动沙箱 |

### 测试结果如何？

- 新增 `tests/test_runtime_security.py`：33 个用例，全绿。
- 全量：302 passed（原 269 + 33）。
- 覆盖率：`core/tools/permissions.py`、`core/tools/dispatcher.py`、`core/tools/schema.py`、`core/agent/runtime.py` 均 100%（行 + 分支）。
- **变异验证**（确认测试真的能抓住它们声称防住的问题）：

  | 变异 | 捕获的测试数 |
  |---|---|
  | 还原 schema 逃逸缺陷 | 2 |
  | 组合调度器丢失权限策略 | 1 |
  | 模型可见性不过滤 | 3 |

### 实践结果是否支持阅读材料中的观点？

**支持，且有一处修正。**

- 4.8「调试靠执行轨迹」——得到支持但**方式不同**：本项目没有单独的 `display_trace` 工具，因为 `ExecutionContext` 的事件流**本身就是**轨迹（`Event` 带作者与时刻）。Topic 06 的设计在 Topic 07 这里才显出价值。
- 4.8「用 GAIA 测」——得到**部分**支持：GAIA 式的端到端跑通有价值，但它每次结果可能不同（真实模型、真实工具、真实数据集），**不能当回归测试**。本项目 33 个安全用例全部确定性、零真实 LLM 调用，能进 CI 当守门人。**这是分工不是替代**——但两者的分工要按路线图的两类评估来分，不能笼统说「效果评测」：
  - **Agent Evaluation**（Phase 0 范围）：验证 Agent 的**行为**——选对工具了吗、异常处理对吗、权限拦住了吗。Phase 0 的交付物里明确列了「基础 Agent Evaluation」「基础评估数据集」，验收标准是「能够通过评估用例验证基础 Agent 行为」。本次的 33 个用例属于这一类，但形式是 pytest 断言；**数据集化的评估用例集**尚未做，那属于 Topic 14（Reliability and Phase Acceptance）。
  - **Learning Evaluation**（Phase 5 范围，Topic 61）：验证**学习效果**——掌握度判断准不准、路径调整好不好。Phase 0 不需要它，现在也不预设基准。
- OWASP「授权在执行组件里做」——得到直接支持，并且实现时发现了一件文档不会告诉你的事：**组合调度器时容易丢策略**。`output_model` 设置后运行时会组合出新调度器补 `final_answer` 工具，漏掉 `policy=dispatcher.policy` 就等于给被限制的工具开了后门。这是本次特意加回归测试的一处。
- 8.2「代码执行要沙箱」——**尚未验证**：本项目没有代码执行需求，设计保留在 `docs/engineering/sandbox.md`，代码未实现。

## 6. Reflection

1. **今天最重要的知识是什么？**
   **「检查点在哪里」比「检查有多严」更重要。** 权限检查的具体规则可以调整、可以加严、可以换模型，但「这个检查在唯一的执行边界上」这件事一旦成立，后面所有新增的执行路径都自动受保护。反过来，如果检查散落在多个调用点，那么每加一条路径就多一次「记得检查」的机会——而忘记一次就是静默放行。
   这条认识不限于安全：它其实是**架构**的一般规律——把不变量的检查放在只有一个的地方，其余地方只管调用。

2. **哪个观点改变或修正了原来的理解？**
   原本以为「沙箱」是执行任意代码之前必须先做的事。读完 8.2 与 T11 之后的理解是：**沙箱解决的是「已授权动作的副作用」，不是「该不该做这个动作」**。如果一个工具本来就不该被调用，正确答案是拒绝它，不是把它关进沙箱里跑。两者是**先后**关系不是**替代**关系——把沙箱当成万能防线，会让人以为「上了沙箱就安全了」，而实际上被注入的模型在沙箱里照样能把它该做的事做完。

   另一处修正：原本以为「输入净化」是防注入的正路。OWASP 明确建议做，但读了 Cheat Sheet 才发现它自己也把净化放在「纵深防御」里、并且强调外部数据一律不可信。真正的教训不是「做不做净化」，而是**任何单一手段都不够，因此必须让多个手段互不依赖**——这恰好是权限模型的设计逻辑：可见性过滤、强制检查、参数校验，三层各自独立。

3. **当前实现还有哪些局限？**
   - **T6 目标操纵完全未缓解**——工具结果与模型输出在上下文里不可区分，模型可以被外部内容改写学习目标。这是当前最大的缺口，且**权限模型管不了它**。→ 归 **Day 63 Security Review**（本 Topic 写下时它没有归属，2026-11-19 决定留在那里）。
   - **权限只到工具粒度，不到参数粒度**——`read_notes` 拿到了 `fs:read`，它内部读什么路径不在管辖范围内。
   - **没有沙箱**——见 `docs/engineering/sandbox.md`，是设计不是实现。→ 归 **Day 63 Security Review**（同上）。
   - **没有人工确认**——高风险工具没有 T10 意义上的拦截。OWASP 提到批准必须「原子地检查并消费」，防止并发或重放复用批准——这一点在设计 HITL 时必须处理。
   - **Agent Evaluation 尚未数据集化**——33 个用例是 pytest 断言，Phase 0 交付物要求的「基础评估数据集 / 评估用例」还没有。这是 Phase 0 **范围内**的欠账，属 Topic 14。（Learning Evaluation 不在此列：它属 Phase 5，现在不预设基准。）
   - **上下文不压缩**（属 Topic 08）——长会话撑爆窗口，间接放大 T4。
   - **权限是运行期的，不做身份认证**——`ToolContext.caller` 是标识，不是凭据。

## 7. Output

- Code / PR:
  - `src/agent_learning_coach/core/tools/permissions.py`（新增：权限模型）
  - `src/agent_learning_coach/core/tools/types.py`（`ToolDefinition.scopes`）
  - `src/agent_learning_coach/core/tools/dispatcher.py`（权限检查 + `allowed_specs`）
  - `src/agent_learning_coach/core/tools/schema.py`（schema 畸形收敛为 `ToolSchemaError`）
  - `src/agent_learning_coach/core/tools/errors.py`（`ToolPermissionError` / `ToolSchemaError`）
  - `src/agent_learning_coach/core/tools/mcp.py`（远端工具按命名空间授予作用域）
  - `src/agent_learning_coach/core/agent/runtime.py`（组合调度器保留策略；模型可见性过滤）
  - `src/agent_learning_coach/core/tools/__init__.py`（导出新的权限与异常符号）
- Tests: `tests/test_runtime_security.py`（33 个用例）
- ADR: `docs/architecture/decisions/0007-tool-permission-and-sandbox-boundary.md`
- Article Notes: `docs/articles/agent-runtime.md`、`docs/engineering/security.md`、`docs/engineering/threat-model.md`、`docs/engineering/sandbox.md`
- Unresolved Questions:
  - T6 目标操纵的缓解方案（来源标记 / 边界分隔 / 内容净化 / 独立校验调用）该如何选型？→ **Day 63**
  - HITL 的批准记录如何做到「原子检查并消费」？运行时的暂停/恢复状态模型怎么设计？→ **Day 09 Session 与 HITL**
  - 权限如何从工具粒度下沉到参数粒度（例如路径白名单）？这属于工具自身职责还是策略职责？→ **Day 63**（与沙箱一并考虑）
  - Phase 0 的「基础评估数据集」怎么组织才既能进 CI 又不与 pytest 重复？→ **Day 14 可靠性与阶段验收**（Learning Evaluation 的基准属 Phase 5，现在不预设。）
