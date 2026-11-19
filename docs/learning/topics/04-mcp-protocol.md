# Topic: MCP 协议

> Day 4 ｜ Phase 0 · Agent Engineering Foundation ｜ 主教材：Ch3：3.4 ｜ 外部参考：MCP Specification ｜ 状态：已完成

## 1. Engineering Question

MCP 的 Client / Server 边界是什么？哪些能力应该放进 MCP Server，而不是写死在业务代码里？

## 2. Primary Reading

- Book: Ch3：3.4
- Chapter / Section: Ch3 3.4
- 核心观点：
  - MCP 把「工具」标准化为「带 JSON Schema 的可调用能力」，客户端用 `tools/list` 发现、`tools/call` 调用。
  - 协议本身不规定工具做什么，只规定工具怎么被描述与调用。
  - Client / Server 边界即「谁拥有工具的实现与契约」。

## 3. Cross References

| 来源 | 核心观点 | 与主教材的关系 |
|---|---|---|
| MCP Specification | 工具描述为 name + description + inputSchema；错误分协议错误与工具错误两类 | 印证「工具形状」的标准化；补充了错误分类这一硬约束 |
| 官方工程指南 | 协议层与框架封装分离；SDK 低层 API 提供完整控制 | 支撑本项目「停留在协议层」的选型 |
| 框架 / 协议文档 | FastMCP 从函数签名推导 schema；低层 Server 用回调注册处理器 | 解释了为何不能用框架封装做保真适配 |
| 论文 / 研究资料 | 工具调用失败率与工具描述质量、错误反馈质量相关 | 说明错误信息的可读性直接影响模型自愈能力 |

## 4. Compare and Challenge

- 不同资料有哪些一致观点？
  - 都用 JSON Schema 描述参数；都把工具描述视为模型决策的依据；都区分协议错误与工具错误。
- 哪些观点存在分歧？
  - 错误呈现：有的实现把失败编码进结果字段，有的直接抛异常。本项目沿用 ADR-0003 的结论——调用级失败归一化，系统级失败抛异常，与 MCP 的两类错误正好对应。
  - schema 来源：从函数签名推导 vs 显式声明。FastMCP 选前者，本项目选后者。
- 分歧来自应用场景、设计目标还是技术演进？
  - schema 来源的分歧来自**保真度需求**：显式声明才能表达 `additionalProperties`、`minimum` 这类签名推不出来的约束。
  - 错误呈现的分歧来自**调用主体**：面向模型的系统归一化，面向程序员的系统抛异常。
- 哪些结论缺少充分证据？
  - 「MCP 一定比直接调用更可扩展」缺少对照数据。工具数量、团队规模、部署形态都影响结论。
- 哪些观点不适用于当前项目？
  - 「所有工具都应该做成 MCP Server」不适用。本项目自己的工具同属一个进程、同一团队，套 MCP 只增加开销。

## 5. Engineering Practice

- 实现了什么？
  - MCP 协议适配（`src/agent_learning_coach/core/tools/mcp.py`），双向：
    - 服务端：`build_mcp_server` 把工具注册表暴露为 MCP Server；`serve` / `run_stdio_server` 运行它。
    - 客户端：`MCPTool` / `discover_mcp_tools` 把远端 MCP 工具适配成本项目的 `Tool`。
    - 定义映射：`to_mcp_tool` / `from_mcp_tool`；结果提取：`extract_text_content`。
  - 可运行 MCP Server 示例（`examples/mcp_server.py`）：暴露三个学习教练工具（检索资料、生成练习、查询掌握度）。
  - 测试（`tests/test_mcp_tools.py`，35 例）：定义映射、服务端发现、服务端调用、客户端适配、分页、结构化输出、连接错误、本地与远端工具共存。
- 理解 Client / Server 和协议边界
  - 边界即「谁拥有工具的实现与契约」：远端拥有实现与 schema 声明，我们只消费声明。
  - 校验双方都做：远端必须校验（不能信任客户端），我们本地也校验（省一次往返、早暴露）。两处用同一套 JSON Schema 语义，结论一致。
  - 归一化由我们负责：远端的 `CallToolResult` 转成本地 `ToolOutcome`，与本地工具走同一条路径。
- 最小 MCP Server PoC 或协议适配实验
  - PoC 已实现并**真实运行**：以子进程方式启动示例 Server，客户端完成 Tool Discovery（3 个工具，描述与 schema 保真）与 Tool Invocation（`search_learning_materials` 返回检索结果）。
  - 适配实验已实现并验证：远端 MCP 工具接入本项目注册表后，与本地工具共用同一个 `ToolDispatcher`，本地校验、超时、归一化全部生效。
- 为什么这样设计？
  - 适配器停留在**协议层**（`mcp` SDK 的低层 `Server` / `ClientSession`），不用 FastMCP 封装：FastMCP 从函数签名推导 schema，无法保真显式声明的 JSON Schema，而「模型看到的契约」与「实际校验的契约」必须是同一份。低层 API 用回调注册处理器，schema 可原样透传。
  - 低层 `Server` 而非官方 SDK 自带的中层 `MCPServer`：`MCPServer` 的注册接口只接受可调用对象，参数校验依据的是**从函数签名推导的 `arg_model`**，与显式声明的 schema 不是同一份——实测 `additionalProperties: False` 与 `minLength` 都不生效（多传参数被静默丢弃、短字符串被接受）。代价是自己写 stdio 运行器，换来契约一致性；HTTP 传输低层同样具备（`streamable_http_app()`）。
  - 服务端复用 `ToolDispatcher`：避免「本地调一套、MCP 调另一套」的行为漂移。
  - 客户端适配进生产、服务端适配只作 PoC：MCP 的价值在独立部署与跨客户端复用；本项目自有工具同属一个进程、同一团队，套 MCP 只增加进程边界与故障模式。但服务端适配仍需保留——它是协议正确性的证明、未来独立部署的路径，以及「哪些能力该放进 MCP Server」的可运行参照。
- 有哪些替代方案？
  - 直接为每个第三方能力写厂商特定适配器：每接一个能力写一遍，无统一契约，无法复用校验与归一化。
  - 用 FastMCP 封装：写法更短，但 schema 保真度不足，且把协议层决策外包给框架。
  - 只做客户端不做服务端：可行，但失去了协议正确性的自证与未来迁移路径。
  - 完全不接 MCP：短期最省事，但每接一个第三方 MCP Server 都要重造一遍适配。
- 测试结果如何？
  `pytest` 205 passed；全仓行覆盖 100%，`core/tools/mcp.py` 行覆盖与分支覆盖均 100%。
  `ruff format --check` / `ruff check` / `mypy` 全部通过。
  真实运行验证：示例 MCP Server 以子进程方式启动，Tool Discovery 发现 3 个工具（描述与 JSON Schema 保真），Tool Invocation 返回预期结果；非法参数被本地校验拦截，远端错误被归一化；工具不存在表现为 JSON-RPC 协议错误而非 `is_error` 结果。
- 实践结果是否支持阅读材料中的观点？
  支持：MCP 的价值在「标准化工具契约」，而不在于把每个工具都变成 Server。实验证明适配确为三字段映射（`name` / `description` / `inputSchema`），因此接第三方 MCP Server 的成本极低；而自家工具套上 MCP 只会增加进程边界与故障模式，没有任何收益。
  反面教材：以为「协议层 = 稳定」。首版适配器按 `isError` 读取错误标记，而当前 SDK 已改名为 `is_error`——测试直接报 `AttributeError`。协议层拿到的是更少的「未来兼容」承诺：框架封装帮你挡掉改名，代价是失去 schema 保真与控制权。适配器因此同时兼容两种命名，但这也说明选协议层要自己承担升级成本。
  二次反面教材：以为「工具不存在」也该表现为错误结果。实际服务端抛出的 `ToolNotFoundError` 被 MCP 框架转成了 JSON-RPC **协议错误**，客户端拿到的是 `MCPError` 而不是 `CallToolResult`。这不是缺陷而是协议语义：协议错误客户端无法通过调整参数修复，工具错误可以。适配层必须尊重这个区分，否则会把「配置错误」伪装成「模型可自愈的错误」。
  三次反面教材：以为「覆盖率 100% 是硬指标」。`run_stdio_server` 的三行只在**子进程**里执行，父进程的覆盖率工具永远测不到。正确做法不是写 `# pragma: no cover` 糊弄过去，而是把可测部分拆出来（`serve(server, read_stream, write_stream)`）在进程内覆盖，只对真正只能在子进程执行的部分标注 pragma 并写明原因。
  四次反面教材：以为「测试全绿 = 发现机制没问题」。事后专门审查发现机制时，用真实协议路径实测挖出三个**静默失败**——不报错，只是安静地少给东西：① `tools/list` 分页被截断（远端 6 个工具只发现 3 个）；② 结构化输出与非文本内容块被吞成空字符串（空串与「工具确实没有输出」无法区分，模型会误判工具行为）；③ 握手无超时且错误只显示 "unhandled errors in a TaskGroup"。三者都已修复并补了回归测试。教训：**覆盖率衡量「代码被执行过」，不衡量「行为正确」**——要主动用真实协议构造边界场景（分页、非文本内容、进程异常退出）去证伪。

## 6. Reflection

1. 今天最重要的知识是什么？
   「该不该用 MCP」不是技术问题而是**边界问题**：先问工具的实现与契约归谁所有。同属一个进程的工具，套协议只增加开销；归别人所有的工具，不套协议就得为它重造一遍适配。
2. 哪个观点改变或修正了原来的理解？
   原来以为接 MCP 的主要收益是「标准化」。实验后更准确的表述是：收益来自**契约同构**——MCP 的工具形状与我们的 `ToolDefinition` 本来就一样，接第三方 Server 因此零成本。而「更通用、更可扩展」这类说法在没有对照数据时是不可验证的。
3. 当前实现还有哪些局限？
   无鉴权（MCP Server 不适合直接暴露公网）；无能力协商（不按 Server 声明的能力裁剪工具清单）；连接生命周期由调用方管理，长会话需自行复用；远端 schema 变更无版本协商机制；并发调用远端工具未实现；**没有 MCP Server 注册表**——「有哪些 Server 可用」目前散落在调用方代码里（`stdio_session` 的参数），协议本身也不提供这个概念（`tools/list` 是针对已连接 Server 列举内容）。等真的出现第二个 Server 时再补这一层。

## 7. Output

- Code / PR: `src/agent_learning_coach/core/tools/mcp.py`、`examples/mcp_server.py`
- Tests: `tests/test_mcp_tools.py`
- ADR: `docs/architecture/decisions/0004-mcp-integration.md`
- Article Notes: `docs/engineering/mcp.md`
- Unresolved Questions: MCP Server 的鉴权与按会话隔离如何设计；远端工具的 schema 变更如何协商版本；长连接下如何跟踪远端工具的能力变化。
