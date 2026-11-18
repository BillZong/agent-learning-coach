# Topic: Tool Calling

> Day 3 ｜ Phase 0 · Agent Engineering Foundation ｜ 主教材：Ch3：3.1～3.3 ｜ 外部参考：OpenAI Tools 文档 ｜ 状态：已完成

## 1. Engineering Question

工具调用的参数校验应该在哪一层失败？Registry 与 Dispatcher 的职责边界如何划分，才能避免校验逻辑重复？

## 2. Primary Reading

- Book: Ch3：3.1～3.3
- Chapter / Section: Ch3 3.1～3.3
- 核心观点：
  - 工具调用是「模型提出意图、系统执行、结果回填」的循环，模型只看到文本，不直接触碰执行环境。
  - 工具描述（名称 + 描述 + 参数 schema）是模型决定「调不调这个工具」的唯一依据，因此描述质量直接决定调用质量。
  - 参数结构用 JSON Schema 表达，模型的 function calling 能力本质上就是「按 schema 生成参数」。

## 3. Cross References

| 来源 | 核心观点 | 与主教材的关系 |
|---|---|---|
| OpenAI Tools 文档 | 工具以 JSON Schema 描述参数；`tool_call_id` 必须与结果配对回填 | 印证 schema 契约；补充了「成对回填」这一硬约束 |
| 官方工程指南 | 工具结果应回填为可读文本，而非原始结构 | 与本项目的「归一化」决策一致 |
| 框架 / 协议文档 | MCP 把工具抽象为「带 JSON Schema 的可调用能力」，并统一了发现与调用 | 后续 Topic 04 的基础；本项目的 `ToolDefinition` 与之同构 |
| 论文 / 研究资料 | Toolformer 等工作表明，工具描述的措辞显著影响调用准确率 | 说明 `description` 字段不是装饰 |

## 4. Compare and Challenge

- 不同资料有哪些一致观点？
  - 都用 JSON Schema 描述参数；都认为工具结果是「回填给模型的文本」；都把工具描述视为影响调用质量的关键。
- 哪些观点存在分歧？
  - 校验位置：部分框架把校验放在框架层（与本项目一致），部分让工具自己校验。真实调用中前者更可靠。
  - 错误表达：有的框架把错误编码进结果字段，有的用异常。本项目选择「调用级失败归一化成结果、系统级失败抛异常」。
- 分歧来自应用场景、设计目标还是技术演进？
  - 主要来自**设计目标**：以框架为通用平台的系统倾向抛异常（让宿主决定），以模型为调用方的系统倾向归一化（让模型能自愈）。本项目的调用主体是模型，因此选归一化。
- 哪些结论缺少充分证据？
  - 「描述措辞影响调用准确率」的量化幅度多来自厂商自述，跨厂商可比性存疑。
- 哪些观点不适用于当前项目？
  - MCP 的工具发现与动态注册暂不适用：当前工具是编译期已知的静态集合。

## 5. Engineering Practice

- 实现了什么？
  - Tool Definition：`ToolDefinition`（名称、描述、参数 JSON Schema、超时、元数据），可转换为 `ToolSpec` 发给模型。
  - Tool Context：`ToolContext`（会话 id、调用方、截止时间、元数据），冻结为不可变。
  - Tool Result：`ToolOutcome`（状态、内容、结构化数据、错误、耗时），所有执行路径的唯一产物。
  - Tool Registry：`ToolRegistry`——注册、注销、查找、导出工具清单；不执行工具。
  - Tool Dispatcher：`ToolDispatcher`——查找、校验、执行（带超时）、归一化。
  - Schema Validation：`jsonschema`（draft 2020-12 参考实现），校验发生在调用前。
  - Tool Timeout：`asyncio.timeout`，优先级为工具定义 > 调度器默认值。
  - Tool Result Normalization：任意返回值 → 统一文本 + 结构化数据，归一化本身永不抛异常。
  - 另提供 `Tool` Protocol（实现方无需继承）与 `FunctionTool` 适配器（包裹普通函数）。
- 为什么这样设计？
  - 参数校验集中在 Dispatcher 一处：工具只声明「我接受什么」，调度器保证「传进来的一定合法」。让每个工具自己校验必然重复且不一致。
  - Registry 与 Dispatcher 分离：注册表若也管执行，就要同时承担「配置」与「行为」两类变化原因完全不同的职责；分开后 Agent Loop 可以在不知道工具实现细节的前提下把注册表当作工具清单的唯一来源。
  - 发给模型的 schema 与实际校验的 schema 是同一份（`ToolDefinition.parameters`），杜绝「告诉模型 A、实际按 B 校验」的错位。
  - 调用级失败归一化成结果：调用主体是模型，模型能读懂错误并调整下一步，抛异常会中断整个 Agent Loop。
  - 归一化用 `jsonschema` 而非自研子集校验器：自研实现会在边界情况给出错误的通过/拒绝结论，而这类错误只在生产数据形态下暴露。
- 有哪些替代方案？
  - 抽象基类（ABC）承载工具：强制继承，且无法用于非类实现；本项目沿用 ADR-0002 的 Protocol 立场。
  - 让工具自己校验参数：校验逻辑重复且不一致，schema 与实际行为可能漂移。
  - 错误只编码进结果字段、不抛异常：工具未注册这类配置错误会被静默吞掉，问题暴露得太晚。
  - `asyncio.wait_for` 替代 `asyncio.timeout`：前者只是放弃等待，协程仍在后台运行并可能继续占用资源，对会做外部 IO 的工具差别是实质性的。
  - 厂商原生结构化输出替代归一化：跨厂商行为不一致，且国产厂商支持程度不一。
- 测试结果如何？
  `pytest` 170 passed；全仓覆盖率 100%（`core/tools` 12 个模块均 100%）。
  `ruff format --check` / `ruff check` / `mypy` 全部通过。
  真实厂商联调：MiniMax 国内站用实际 Key 跑通「模型发起工具调用 → 系统校验并执行 → 结果回填 → 模型基于结果作答」完整链路（`add(17, 25)` → `42`）。
- 实践结果是否支持阅读材料中的观点？
  支持：工具是模型意图与执行环境之间的唯一通道，且这个通道上的一切都必须归一化——发给模型的是 schema，回填给模型的是文本，中间是受控的执行。
  反面教材：把「定义 / 上下文 / 结果」揉成一个类型。第一版实现时 `ToolResult` 差点同时承担「工具产出」与「回填消息」两种职责，结果是执行侧信息（耗时、结构化数据）无处安放，而消息侧又被迫携带执行细节。拆成 `ToolDefinition` / `ToolContext` / `ToolOutcome` 后，工具可复用、结果可比较。
  二次反面教材：归一化函数「理论上不会失败」。首版实现里 `_dump_json` 的兜底 `repr` 自身抛异常时，异常会逃逸——恰好在最需要错误信息的时刻把错误信息弄丢了。文档写「绝不抛出」还不够，实现必须逐条路径验证：不可序列化对象降级 `repr`，`repr` 失败降级占位文本。
  三次反面教材：纸面设计看不出来的两条约束，只有真实厂商联调才能暴露——(1) 回填必须**成对**：厂商要求 tool result 消息与 assistant 的 `tool_calls` 消息成对出现，只回填结果会得到 `400 tool result's tool id not found`；(2) handler 签名必须**固定**：`(context, arguments)` 与 `(a, b)` 两种写法都不报错，只在运行时表现为参数错位。这两条都已写入接入说明并由回归测试锁定。

## 6. Reflection

1. 今天最重要的知识是什么？
   校验的位置决定了系统的可维护性。把校验放在 Dispatcher 一处之后，「schema 就是唯一契约」才成立——因为同一份 schema 既发给模型也用于校验，契约不可能漂移。相比之下，「每个工具自己校验」只是把重复劳动从一处搬到 N 处。
2. 哪个观点改变或修正了原来的理解？
   原来以为「工具抛异常」是自然的设计。实际做下来，调用主体是模型这一点改变了错误的语义：异常是给程序员看的，文本是给模型看的，而模型恰恰是唯一能读懂错误并纠正自己的角色。
3. 当前实现还有哪些局限？
   并发执行工具调用未实现（属 Agent Loop 职责）；工具权限与鉴权未实现，当前按注册表可见性控制，生产环境需按会话隔离；`ToolOutcome` 与 LLM 侧 `ToolResult` 并存，调用方需显式转换；结构化结果只保留 dict 形态，Pydantic 模型已展平；无 MCP 工具接入（Topic 04）。

## 7. Output

- Code / PR: `src/agent_learning_coach/core/tools/`（types / errors / schema / normalization / registry / dispatcher）
- Tests: `tests/test_tool_system.py`
- ADR: `docs/architecture/decisions/0003-tool-system.md`
- Integration Notes: `docs/engineering/tools.md`
- Unresolved Questions: 工具并发执行与重试策略归属 Agent Loop 还是调度器；工具权限模型如何与会话隔离结合；MCP 工具如何映射到 `ToolDefinition`。
