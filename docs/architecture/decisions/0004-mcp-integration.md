# ADR-0004: MCP 协议的接入边界与适配器取舍

- 状态：已接受
- 日期：2026-11-18

## 背景

Topic 04 提出的工程问题是：**MCP 的 Client / Server 边界是什么？哪些能力
应该放进 MCP Server，而不是写死在业务代码里？**

`core/tools` 已经交付了完整的工具系统：工具契约（`ToolDefinition`）、注册表
（`ToolRegistry`）、调度器（`ToolDispatcher`）、Schema 校验、超时与结果
归一化。MCP（Model Context Protocol）把工具标准化为「带 JSON Schema 的可调用
能力」，通过 `tools/list` 发现、`tools/call` 调用。

需要回答的问题：

1. 本项目要不要接 MCP？接的话是哪个方向？
2. 现有工具系统与 MCP 的工具形状是否兼容？
3. 第三方 MCP Server 的工具如何接入而不破坏现有的校验、超时与归一化？
4. 自己的工具要不要也做成 MCP Server？

## 决策

**接入 MCP，但只把客户端侧适配作为生产组件；服务端侧作为 PoC 与协议参考
实现保留。**

### 1. 工具形状同构，适配是三字段映射

MCP 的 `Tool` 与本项目的 `ToolDefinition` 是同一个形状：名称、描述、
JSON Schema。因此适配不需要语义转换：

| MCP | 本项目 |
| --- | ------ |
| `Tool.name` | `ToolDefinition.name` |
| `Tool.description` | `ToolDefinition.description` |
| `Tool.input_schema` | `ToolDefinition.parameters` |
| `tools/list` | `registry.specs()` |
| `tools/call` | `dispatcher.dispatch(...)` |

这个同构是接 MCP 的主要收益：第三方 MCP Server 的工具无需任何厂商特定代码
即可接入。

### 2. 客户端侧适配进入生产代码

`MCPTool` 把远端 MCP 工具包装成本项目的 `Tool`，`discover_mcp_tools` 完成
发现与适配。适配进来的工具注册进 `ToolRegistry`，由现有的 `ToolDispatcher`
调度，因此免费获得：

- **本地 Schema 校验**：参数不合法不必走一趟网络；
- **统一的超时策略**：与本地工具同一套；
- **统一的归一化**：产出同构的 `ToolOutcome`，调用方无需区分工具来源。

`namespace` 参数给远端工具加前缀，避免多个 MCP Server 的同名工具冲突。

### 3. 服务端侧适配只作为 PoC

`build_mcp_server` 把工具注册表暴露为 MCP Server，可运行示例见
`examples/mcp_server.py`。它**不作为生产组件**使用。

理由：MCP 的价值在于「能力独立部署、独立归属、跨客户端复用」。本项目自己的
工具（检索资料、生成练习、查询掌握度）与 Agent Loop 同属一个进程、同一团队。
把它们套上 MCP 只增加进程边界、协议编解码与故障模式（子进程崩溃、连接中断、
超时传播），而没有任何收益。

保留服务端适配的实际价值有三：

1. 它是**协议正确性的证明**——真实客户端能发现并调用我们的工具；
2. 它是**未来迁移的路径**——若某个能力确实需要独立部署（如检索服务由另一
   团队维护），服务端适配已经就绪；
3. 它是**教学材料**——让「哪些能力该放进 MCP Server」有可运行的参照物。

### 4. 适配器停留在协议层，不依赖框架封装

适配器直接使用 `mcp` SDK 的低层 `Server` / `ClientSession`，不用 FastMCP 的
便捷封装。理由：FastMCP 从 Python 函数签名推导 schema，无法保真我们显式
声明的 JSON Schema（`additionalProperties`、`minimum`、嵌套结构都会被丢掉），
而「模型看到的契约」与「实际校验的契约」必须是同一份。

低层 API 用回调注册处理器（`on_list_tools` / `on_call_tool`），因此
`ToolDefinition.parameters` 可以原样传给 MCP 客户端。

### 5. 错误语义保持 MCP 的两类区分

| 失败 | MCP 表现 | 本项目表现 |
| ---- | -------- | ---------- |
| 工具执行失败 | `CallToolResult(is_error=True)` + 文本 | `ToolOutcome(status="error")` |
| 工具不存在 | JSON-RPC 协议错误 | `ToolNotFoundError` |

跨协议只传可读消息；错误类型名留在本地的 `ToolOutcome.error` 上——回填给
模型的内容要让人读懂，类型名对它没有意义。

## 后果

- 好处：第三方 MCP Server 的工具零成本接入，且自动继承本项目的校验、超时与
  归一化；工具系统与生态（MCP）之间没有供应商锁定；协议正确性有真实客户端
  与真实子进程双向验证；未来的独立部署路径已经就绪。
- 代价：多一层适配代码；引入 `mcp` SDK 作为运行时依赖（原本只是间接依赖），
  且低层 API 在 SDK 大版本间会变（本项目已遇到 `isError` → `is_error` 改名，
  适配器同时兼容两者）；MCP Server 侧暂无鉴权，不适合直接暴露到公网。
- 边界：并发调用远端 MCP 工具、连接复用、鉴权、能力协商均未实现；远端
  schema 变更无版本协商机制。

## 补充说明（2026-11-18）

PoC 与协议适配实验中确认的事实：

1. **本项目的工具契约与 MCP 同构**：适配确为三字段映射，无需语义转换。
   这一结论支持「接 MCP 的成本很低」。
2. **双向验证可行**：服务端经真实子进程（stdio）被真实客户端发现并调用；
   客户端能把远端工具接回本项目注册表，与本地工具共用同一个 Dispatcher。
3. **SDK 细节会咬人**：本 SDK 版本把 `CallToolResult.isError` 改名为
   `is_error`，适配器必须同时兼容两种命名，否则升级即断。这是选择
   **协议层**而非框架封装的直接代价——协议层拿到的是更少的「未来兼容」承诺。
4. **协议错误与工具错误必须分开**：工具不存在时，服务端抛出的异常会被
   MCP 框架转成 JSON-RPC 协议错误，而不是 `is_error` 结果。这不是缺陷而是
   MCP 的语义要求：协议错误客户端无法通过调整参数修复，工具错误则可以。

接入说明见 [`docs/engineering/mcp.md`](../../engineering/mcp.md)。
学习笔记见 [`docs/learning/topics/04-mcp-protocol.md`](../../learning/topics/04-mcp-protocol.md)。
