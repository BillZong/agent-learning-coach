# Topic: Execution Context 与 Runtime

> Day 6 ｜ Phase 0 · Agent Engineering Foundation ｜ 主教材：Ch4：4.3～4.5、4.7 ｜ 外部参考：OpenAI Agents SDK 文档 ｜ 状态：已完成

## 1. Engineering Question

Execution Context 需要承载哪些状态，才能让 Agent Loop 可测试、可复现？

答：**执行身份、事件历史、步数、草稿本、用量与成本、最终产物**。

前四项让运行「可复现」——给定同样的输入与事件序列就能得到同样的结论；后两项
让运行「可核算」；事件比扁平对话列表多出的「归属」与「时刻」让历史可审计、
可回放。

## 2. Primary Reading

- Book: Ch4：4.3～4.5、4.7
- Chapter / Section: Ch4 4.3～4.5、4.7
- 核心观点：
  - **4.3 ExecutionContext 是「智能体的中央存储」**：执行过程发生的一切——用户输入、模型输出、工具结果——都记在上下文里，而不是散落在各处的局部变量。上下文因此是运行的**唯一真相来源**。
  - **4.3 的 Event 模型**：执行历史不是一条扁平的消息列表，而是一串带归属与时刻的事件。「谁说的、什么时候说的」让历史可以审计与回放。
  - **4.4 工具抽象**：统一的工具接口（名称 + 描述 + 参数 schema + 执行入口）是工具能被模型发现与调用的前提。
  - **4.5 通信层**：`LlmRequest` 决定「发送什么」，`LlmResponse` 统一「接收什么」，`LlmClient` 隔离厂商差异。三者都不应该泄漏到上层。
  - **4.7 结构化输出用「工具作为输出格式化器」**：不给模型另开一条结构化通道，而是加一个参数 schema 就是目标模型的工具，模型调用它即表示提交最终答案。

## 3. Cross References

| 来源 | 核心观点 | 与主教材的关系 |
|---|---|---|
| OpenAI Agents SDK 文档 | `RunContextWrapper` 包装调用方的上下文对象并附带用量信息；**「Contexts are not passed to the LLM. They're a way to pass dependencies and data to code you implement, like tool functions」**；`max_turns` 超限抛 `MaxTurnsExceeded`；usage 按运行跟踪 | 印证「上下文是给工具与代码用的、不是给模型看的」；`max_turns` 抛异常而本项目返回停止原因，是一处有意的分歧 |
| OpenAI Agents SDK 文档（`AgentOutputSchema`） | 结构化输出由 `json_schema()` + `validate_json()` 描述与校验，实作为一个工具；`output_type` 挂在 Agent 上 | 与 4.7 完全同构——独立实现一遍结构化输出通道是重复劳动 |
| 官方工程指南（Anthropic / 上下文工程实践） | 上下文窗口是**有限预算**，长会话必须裁剪；工具结果是上下文里最占篇幅的部分 | 指出本项目当前最大的缺口：事件不压缩，长会话必然撑爆 |
| 框架 / 协议文档（LangGraph 等） | 状态以「图」的形式持久化，支持检查点与回放；`interrupt` 用于人工介入 | 说明「Execution Context」只是最小形态；可回放与断点续跑需要更强的状态模型 |
| 论文 / 研究资料 | 工具描述质量与调用准确率显著相关；把错误信息回填给模型比中断运行更能提升自愈率 | 支撑「工具失败回填而非终止」与「描述即契约」两条设计 |

## 4. Compare and Challenge

- **不同资料有哪些一致观点？**
  - 上下文是**给代码用的，不是给模型用的**——OpenAI Agents SDK 明说「Contexts are not passed to the LLM」，本项目的草稿本设计与之相同。
  - 状态要有唯一权威载体——主教材的 ExecutionContext、SDK 的 RunContextWrapper、LangGraph 的 State，形态不同但诉求相同。
  - 结构化输出走工具通道——主教材 4.7 与 OpenAI Agents SDK 的 `output_type` 是同一套做法。

- **哪些观点存在分歧？**
  - **步数/预算超限该抛异常还是返回结果**：OpenAI Agents SDK 超过 `max_turns` 抛 `MaxTurnsExceeded`；本项目返回 `stop_reason="max_steps"` 并保留部分结果。
  - **上下文的可变性归属**：SDK 把上下文包装成 `RunContextWrapper`（调用方的对象仍是自己的），本项目让上下文直接可变、运行时往里写。

- **分歧来自应用场景、设计目标还是技术演进？**
  - **来自应用场景**：SDK 面向「托管式 Agent 服务」，抛异常让上层统一处理；本项目是**学习教练**——一次运行失败不该丢掉已经问清的学习者信息，保留部分结果让应用层决定「是否追问用户」更有价值。
  - **来自设计目标**：本项目的第一原则是「运行时失败不丢弃已完成的成果」（见 ADR-0005 第 7 条），抛异常与之冲突。

- **哪些结论缺少充分证据？**
  - 「上下文应该裁剪」有充分共识，但**裁剪策略**（按时间、按重要性、按 token 预算）缺少可迁移的结论——各家都在自己场景里试出来的。
  - 「结构化输出走工具比走厂商 response_format 更好」缺少对照实验。SDK 同时支持两者，本项目选了工具通道，但没有测过两条路径的成功率差异。

- **哪些观点不适用于当前项目？**
  - **图/工作流式的状态持久化**：LangGraph 的检查点与回放适合长时程、多智能体编排；本项目是单进程、单智能体的学习教练，引入图模型是过度设计。但「可回放」这个诉求本身是对的——记在 ADR-0006 的未决问题里。
  - **SDK 的 `RunContextWrapper` 包装式设计**：包装层存在的理由是「不改变调用方对象语义」，而本项目的上下文本来就是运行时独占的状态容器，包装一层反而多一层间接。

- **本项目的立场小结**：采纳「上下文给代码不给模型」「状态唯一载体」「结构化输出走工具」三条；分歧处保留自己的判断并写进 ADR；明确记录未采纳的观点及其适用边界。

## 5. Engineering Practice

- 实现了什么？
  - `core/agent/` 从「Agent Loop」升级为「Agent Runtime」：新增 `ExecutionContext`、`Event`、`AgentResult`、`TokenPrice`、`AgentConfigError`
  - 命名统一：`AgentLoop` → `AgentRuntime`，`AgentRun` → `AgentResult`（不保留并行词汇）
  - 新增 `examples/agent_runtime.py`：基础 Agent Runtime 交付物
  - 新增 `tests/test_execution_context.py`（17 例）
- Execution Context
  - 承载：执行身份 `execution_id`、事件历史 `events`、步数 `current_step`、草稿本 `state`、用量 `usage`、成本 `cost`、最终产物 `final_result`
  - 对话内容不再单独存一份——由事件历史展平而来（`context.contents`），保持「每条 tool_call 都有对应 tool_result」的不变量
  - `Event` 携带归属与时刻：谁说的、说了什么、什么时候说的——这是它与扁平对话列表的区别
  - 传入已有上下文即可**续接对话**
- LLM Adapter 集成
  - 运行时只认 `LLMClient` 协议；厂商差异仍由 `core/llm` 的适配器隔离
  - 成本核算依赖响应里的 `usage` 与模型名——真实客户端都会上报，测试替身必须照做
- 结构化 Agent Output
  - 实现：`AgentConfig.output_model` 设置后，运行时补一个 `final_answer` 工具，参数 schema 就是目标模型的 JSON Schema（主教材 4.7「Tools as output formatters」）
  - 模型调用它即表示提交最终答案；运行时把结果还原成目标模型的**已校验实例**（`AgentResult.output`）
  - 校验失败时错误回填给模型，它可以改一份再交——与「调用级失败回填」同一条语义
  - 一处实现细节：Pydantic 嵌套模型的 `$ref: "#/$defs/X"` 相对**根 schema** 解析，因此 `$defs` 必须提升到外层；不提升会抛 `PointerToNowhere`（非校验类异常，会逃出错误归一化直接崩掉运行）
- Runtime State（草稿本）
  - `context.state` 服务于工具与应用层，**模型看不到**
  - 工具通过 `ToolContext.metadata["execution_context"]` 取到执行上下文；用字符串键而不在 `ToolContext` 上加字段，避免 `core/tools` 反向依赖 `core/agent`
- Cost Control
  - `max_total_tokens` 约束实际消耗而非步数；`max_cost` 是金额预算
  - 价格表由调用方提供（`AgentConfig.prices`）：价格会变、随厂商与地区不同，写死等于制造一个必然过期的真相来源
  - 预算无法执行就是配置错误：设了成本上限却无法确定模型、或价格表未覆盖实际模型，抛 `AgentConfigError`
- 为什么这样设计？
  - **状态收进 Context，运行时不另设一份**：「运行发生了什么」只有一个答案
  - **结果携带 Context**：「结论」与「过程」不分离，调用方不必另外保存历史
  - **草稿本不进模型上下文**：模型看到的是事实，不是内部状态
  - **价格表外置**：内置一份只会制造一个必然过期、且过期时无人收到通知的真相
- 有哪些替代方案？
  - 状态直接放在 `AgentRuntime` 实例上：多次运行会互相污染，且无法并行
  - 沿用扁平对话列表 + 额外传一个 dict：没有归属与时刻，历史不可审计
  - 内置价格表：价格会变，且内置的那份过期时无人收到通知
  - 预算无法执行时静默按 0 计费：比不设预算更危险——用户以为有限额
- 测试结果如何？
  `pytest` 259 passed；`core/agent` 全包行覆盖与分支覆盖均 100%。
  `ruff format --check` / `ruff check` / `mypy` 全部通过。
- 实践结果是否支持阅读材料中的观点？
  - 支持「Execution Context 是可测试性与可复现性的载体」：把状态收进上下文后，运行可以只靠「输入 + 事件序列」复现，与模型调用次数无关。
  - 反面教材：以为「设了成本上限就等于有成本控制」。初版在价格表缺失时静默按 0 计费，结果是**完全没有约束**——比不设更危险，因为用户以为有限额。改为直接抛配置错误。
  - 反面教材：以为「客户端返回什么就有什么」。脚本化的模型响应若不带 `usage`，用量与成本都是 0，而测试会在这上面假绿。成本核算依赖客户端上报 usage 与模型名——真实客户端都会上报，但测试替身必须照做。

## 6. Reflection

1. 今天最重要的知识是什么？
   **「上下文」有两个互不相干的含义，混在一起会让架构变糊。** 一个是「模型能看到的对话内容」，另一个是「运行时掌握的状态」。前者受限于上下文窗口，必须裁剪；后者不受限，是工具与应用层的工作区。OpenAI Agents SDK 把这句话写成了文档里的一句 NOTE——「Contexts are not passed to the LLM」。本项目的草稿本设计就是这句话的直接落地。
2. 哪个观点改变或修正了原来的理解？
   原本以为「结构化输出」需要运行时另开一条通道（调用 `generate_structured` 再解析）。读 4.7 与 SDK 的 `output_type` 才发现，**把最终答案做成一个工具就够**——契约一致、失败可自愈、不需要厂商适配。更重要的是这个做法顺带解决了一个我原以为绕不过去的问题：结构化答案和普通工具调用走同一条通道，循环的停止条件（模型不再请求工具）因此保持不变。
3. 当前实现还有哪些局限？
   `state` 不持久化（进程重启即丢）；没有跨进程状态共享；事件不压缩（长会话的上下文膨胀仍未解决，属 Topic 08）；成本只按 token 估算，不含缓存命中、批处理折扣等厂商计费细节；结果对象携带可变上下文，拿到后上下文仍可能被修改。

## 7. Output

- Code / PR: `src/agent_learning_coach/core/agent/`（`runtime.py` / `types.py` / `cost.py` / `errors.py` / `output.py`）、`examples/agent_runtime.py`
- Tests: `tests/test_execution_context.py`、`tests/test_structured_output.py`、`tests/test_runtime.py`、`tests/test_runtime_mcp.py`
- ADR: `docs/architecture/decisions/0006-execution-context-and-cost-control.md`
- Article Notes: `docs/engineering/runtime.md`、`docs/articles/agent-runtime.md`
- Unresolved Questions: 上下文多长之后必须压缩、压缩策略由谁决定；`state` 是否需要跨进程持久化；成本核算要不要覆盖缓存命中与批处理折扣；事件流如何支持回放与断点续跑。
