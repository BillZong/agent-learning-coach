# MCP 架构说明（MCP Architecture Notes）

> 状态：生效
> 对应代码：`src/agent_learning_coach/core/tools/mcp.py`、`examples/mcp_server.py`
> 决策依据：[ADR-0004](../architecture/decisions/0004-mcp-integration.md)

本文说明 MCP 协议在本项目中的位置、适配方式与边界。

## 1. 结论先行

**需要 MCP Adapter，但只需要客户端侧（消费远端 MCP Server）。**

- **客户端适配**：保留在生产代码中（`MCPTool` / `discover_mcp_tools`）。
  第三方 MCP Server 的工具适配成本项目的 `Tool`，即可接入现有工具系统。
- **服务端适配**：作为 PoC 与协议参考实现保留（`build_mcp_server`）。
  本项目自己的工具留在进程内——MCP 的价值在于独立部署与跨客户端复用，
  而自用工具同属一个进程、同一团队，套一层只增加开销。

理由与证据见 [ADR-0004](../architecture/decisions/0004-mcp-integration.md)。

## 2. 协议形状与本项目工具契约是同构的

MCP 的工具描述与本项目的 `ToolDefinition` 是同一个形状：

| MCP | 本项目 | 说明 |
| --- | ------ | ---- |
| `Tool.name` | `ToolDefinition.name` | 工具名 |
| `Tool.description` | `ToolDefinition.description` | 面向模型的描述 |
| `Tool.input_schema` | `ToolDefinition.parameters` | JSON Schema |
| `tools/list` | `registry.specs()` | 工具发现 |
| `tools/call` | `dispatcher.dispatch(...)` | 工具调用 |

因此适配是**三字段的一一映射**，没有语义转换：

```python
def to_mcp_tool(definition: ToolDefinition) -> types.Tool: ...
def from_mcp_tool(mcp_tool: types.Tool) -> ToolDefinition: ...
```

这个「同构」是本项目选择 MCP 的主要理由：接入一个第三方 MCP Server
不需要为它写任何厂商特定代码。

## 3. 两个方向

### 3.1 客户端侧：消费远端 MCP Server

```python
from agent_learning_coach.core.tools import ToolDispatcher, ToolRegistry
from agent_learning_coach.core.tools.mcp import discover_mcp_tools, stdio_session

async with stdio_session("uv", ["run", "python", "examples/mcp_server.py"]) as session:
    remote = await discover_mcp_tools(session, namespace="web")

    registry = ToolRegistry()
    for tool in remote:
        registry.register(tool)

    dispatcher = ToolDispatcher(registry)
    outcome = await dispatcher.dispatch("web_search", {"query": "闭包"}, context)
```

适配进来的工具进入我们的注册表后，**直接复用现有的 `ToolDispatcher`**：

| 能力 | 谁负责 | 说明 |
| ---- | ------ | ---- |
| Schema 校验 | 本地 Dispatcher | 参数不合法**不必**走一趟网络 |
| 超时 | 本地 Dispatcher | 与本地工具同一套超时策略 |
| 结果归一化 | 本地 Dispatcher | 与本地工具产出同构的 `ToolOutcome` |
| 实际执行 | 远端 MCP Server | 只做协议调用 |

`namespace` 参数给远端工具加前缀，避免多个 MCP Server 的同名工具冲突。

### 3.2 服务端侧：暴露本项目工具

```python
from agent_learning_coach.core.tools import ToolRegistry
from agent_learning_coach.core.tools.mcp import build_mcp_server, run_stdio_server

registry = ToolRegistry()
server = build_mcp_server(registry, name="agent-learning-coach-tools")
await run_stdio_server(server)
```

服务端复用 `ToolDispatcher` 执行工具，因此 Schema 校验、超时与结果归一化
与本地调用完全一致——不会出现「本地调一套、MCP 调另一套」的行为漂移。

可运行示例见 `examples/mcp_server.py`：

```bash
uv run python examples/mcp_server.py
```

## 4. 客户端/服务器边界

MCP 是**客户端-服务器**协议，边界如下：

```
┌─────────────────────────┐         MCP          ┌──────────────────────────┐
│  我们的 Agent Runtime   │  ──────────────────►  │  第三方 MCP Server      │
│                         │   tools/list         │                          │
│  ToolRegistry           │   tools/call         │  （独立进程/独立团队）   │
│  ToolDispatcher         │  ◄──────────────────  │                          │
└─────────────────────────┘   CallToolResult     └──────────────────────────┘
```

边界上的关键判断：

- **谁决定工具语义**：远端 Server 拥有工具的实现与 schema 契约；我们只消费
  它声明的契约。因此远端 schema 变更会直接影响我们的行为——适配层不做
  防御性猜测。
- **谁负责参数校验**：**双方都校验**。远端必须校验（它是最终执行者，不能
  信任客户端）；我们本地也校验（省一次网络往返，且错误更早暴露）。
  两处用同一套 JSON Schema 语义，因此结论一致。
- **谁负责结果归一化**：我们。本地把远端的 `CallToolResult` 转成
  `ToolOutcome`，与本地工具走同一条归一化路径。

## 5. 错误语义

MCP 区分两类失败，本项目的适配器保持这个区分：

| 失败 | MCP 表现 | 本项目表现 |
| ---- | -------- | ---------- |
| 工具执行失败（参数不合法、工具内部报错） | `CallToolResult(is_error=True)` + 文本内容 | `ToolOutcome(status="error")` |
| 工具不存在 | JSON-RPC 协议错误（`MCPError`） | `ToolNotFoundError` |

跨协议只传**可读消息**——错误类型名留在本地的 `ToolOutcome.error` 上。
回填给模型的内容要让人（模型）读懂，类型名对它没有意义。

### 5.1 连接建立阶段的错误

| 失败 | 表现 | 本项目表现 |
| ---- | ---- | ---------- |
| 启动命令不存在 | `FileNotFoundError` | `MCPConnectionError`（消息指出是哪个命令） |
| 握手无响应 | 无限期挂起 | `MCPConnectionError`（握手超时，默认 30 秒） |
| 远端进程退出 / 不是 MCP Server | `ExceptionGroup("unhandled errors in a TaskGroup")` | `MCPConnectionError`（消息含真实原因） |

SDK 用 anyio 任务组管理子进程与协议读写，失败时把根因包进层层嵌套的
`ExceptionGroup`，最外层只有一句 "unhandled errors in a TaskGroup"。适配器
把根因摊平——这是对外 API 的基本要求：错误消息必须指出**是哪个命令、
什么原因**。

握手完成后的调用方代码异常**原样透传**，不被改写成连接错误。

## 6. 实现选型：停留在协议层

适配器直接使用 `mcp` SDK 的低层 `Server` / `ClientSession`。这是**协议层**，
不是「框架封装」——`mcp` 是官方 SDK，FastMCP 才是第三方封装。

低层 API 用回调注册处理器（`on_list_tools` / `on_call_tool`），因此
`ToolDefinition.parameters` 可以原样传给 MCP 客户端，且参数校验用的就是
同一份 schema——**模型看到的契约与实际强制的契约必然一致**。

### 6.1 为什么不用 `mcp.server.mcpserver.MCPServer`

`MCPServer` 是官方 SDK 自带的中层 server（不是第三方框架），值得单独比较。
实测结论（真实协议路径）：

| 维度 | `MCPServer` | 低层 `Server`（本项目采用） |
| ---- | ----------- | --------------------------- |
| 注册工具入参 | 只接受可调用对象，schema 必然从签名推导 | 处理器回调直接用 `definition.parameters` |
| **参数校验依据** | 从签名推导的 `arg_model`，与显式 schema 不是同一份 | 与广告出去的 schema 同一份 |
| `additionalProperties: False` | 不生效（多传参数被静默丢弃） | 生效 |
| `minLength` / `minimum` | 不生效 | 生效 |
| `**kwargs` 绕过 | 不可行（arg binding 要求名为 `kwargs` 的参数，调用被拒） | 不涉及 |
| stdio / HTTP 传输 | 内置 | 同样具备（`stdio_server()` / `streamable_http_app()`） |

决定性的是「参数校验依据」：`MCPServer` 会让广告出去的契约与实际强制的
契约变成两份不同的东西。要绕过它就得让函数签名逐字段镜像 schema，等于把
JSON Schema 再表达一遍，重新引入两份真相。

代价：低层 API 需要自己写 stdio 运行器与测试夹具，且在 SDK 大版本间更频繁
地变动。我们接受这个代价——契约一致性不可妥协。

完整推导见 [ADR-0004](../architecture/decisions/0004-mcp-integration.md)。

## 7. 传输

| 传输 | 支持方式 | 适用场景 |
| ---- | -------- | -------- |
| stdio | `stdio_session(command, args, env, timeout=30.0)` | 本地子进程，示例 Server |
| 内存 | `mcp.shared.memory.create_client_server_memory_streams` | 单元测试，无需起进程 |

HTTP / SSE 传输由 MCP SDK 提供，本适配器不绑定具体传输——它只依赖
`ClientSession` 抽象。

## 8. 发现与结果回填的保证

适配器的一类失败不是「报错」，而是**安静地少给东西**。四条保证：

| 场景 | 保证 | 实现细节 |
| ---- | ---- | -------- |
| 远端 `tools/list` 分页 | 翻完所有页，工具不丢 | `discover_mcp_tools` 跟随 `next_cursor`；`max_pages`（默认 100）防止病态服务端导致不收敛 |
| 远端返回结构化输出 | 保留为 JSON 文本 | `extract_text_content` 序列化 `structured_content` |
| 远端返回非文本内容块 | 以占位描述代替，不丢弃 | `[MCP 内容块:image, mimeType=image/png]` |
| 远端声明的输出契约 | 发现阶段保留 | `output_schema` / `title` / `annotations` 存入 `ToolDefinition.metadata` |

判据：**空内容与「工具确实没有输出」无法区分**。若适配器把内容吞成空串，
模型会以为工具什么都没做，而工具其实已经返回了数据。

远端确实什么都没返回时，返回空串是诚实的答案。

## 9. 已知限制

| 限制 | 影响 | 后续 |
| ---- | ---- | -------- |
| 无鉴权 | MCP Server 暴露的工具无法区分调用方 | 生产部署需加 MCP 授权机制 |
| 无能力协商 | 不按 Server 声明的能力裁剪工具清单 | 可按 `server_capabilities` 过滤 |
| **远端工具的生命周期绑定在会话上** | 会话关闭后，已接入注册表的远端工具立即失效 | 长会话应复用连接；需要时引入 MCP Server 注册表统一管理连接 |
| 远端 schema 变更无防护 | 远端改了 schema，本地缓存的 `ToolDefinition` 会过期 | 需要时加版本协商或重新发现 |
| 服务端不分页 | 本项目的 MCP Server 单页返回全部工具 | 工具量小，暂无必要；客户端已能正确跟随远端分页 |

### 9.1 会话寿命与工具寿命

`MCPTool` 持有的是一个**活的** `ClientSession`。因此：

```python
async with stdio_session(cmd) as session:
    tools = await discover_mcp_tools(session)
    for tool in tools:
        registry.register(tool)  # 接入注册表
    # 会话关闭后，registry 里的这些工具就失效了
```

会话关闭后再调用，适配器会抛出明确错误（「MCP 会话已关闭……」）而不是含糊的
`Connection closed`——按 `CONNECTION_CLOSED` 错误码判定，不匹配错误消息字符串。

这意味着**注册表的长生命周期与连接的长生命周期必须对齐**：要么连接跟着
注册表一起长存，要么在会话结束前完成全部调用。

## 10. 相关文档

- 工具系统接入说明：[tools.md](tools.md)
- 决策依据：[ADR-0004](../architecture/decisions/0004-mcp-integration.md)
- 学习笔记：[Topic 04 · MCP 协议](../learning/topics/04-mcp-protocol.md)
