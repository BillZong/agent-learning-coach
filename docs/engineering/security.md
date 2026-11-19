# 安全（Security）

> 状态：草稿 ｜ 对应 Topic 07 ｜ 配套：[threat-model.md](./threat-model.md)、[sandbox.md](./sandbox.md)

本文是**工程视角**的安全说明：代码里现在有什么控制、怎么配置、哪些是已知缺口。
威胁的分类与论证见 [threat-model.md](./threat-model.md)，沙箱方案见
[sandbox.md](./sandbox.md)。

## 1. 威胁模型

见 [threat-model.md](./threat-model.md)。要点：

- 核心假设是**模型的输出不可信**。因此模型只能「请求」，不能「决定」。
- 四个信任边界里最关键的两个是：**② 模型响应 → 运行时**（工具调用是请求，
  必须过权限与校验）与 **⑥ 运行时 → 模型**（工具结果与模型输出在上下文里
  不可区分，这是间接注入的结构性成因）。
- 当前敞着的最大缺口是 **T6 目标操纵**（外部内容改写学习目标）与
  **T11 远程代码执行**（尚无沙箱）。

## 2. 权限

### 2.1 模型

工具在契约里声明**所需作用域**，策略持有**已授予的作用域**；工具所需是授予
的**子集**才放行。作用域是能力（capability）而非角色（role）——不需要维护
「角色 → 权限」映射表，只需要一份「授予了哪些能力」的清单。

```python
from agent_learning_coach.core.tools import ScopePolicy, ToolDispatcher

policy = ScopePolicy(granted=frozenset({"fs:read", "web:read"}))
dispatcher = ToolDispatcher(registry, policy=policy)
```

内置三种策略：

| 策略 | 行为 | 用途 |
|---|---|---|
| `AllowAllPolicy` | 全部放行 | **默认值**，保持既有调用方行为不变 |
| `DenyAllPolicy` | 全部拒绝 | 安全默认值；只读演练 |
| `ScopePolicy` | 按作用域子集判定 | 正常用法 |

`PermissionPolicy` 是协议，可以自定义（例如按 `ToolContext.caller` 区分授权）。

### 2.2 检查点唯一

**权限检查在 `ToolDispatcher.dispatch` 里，不在运行时里。**

理由：Dispatcher 是**唯一的执行边界**——本地工具、远端 MCP 工具、结构化输出
的 `final_answer` 工具全部经过它。检查点只有一个，就不存在「新增一条执行
路径忘了检查」这种 fail-open 漏洞。

如果放在运行时，运行时就必须知道每个工具的来源与危险程度；一旦有人新写一条
执行路径而忘了检查，检查就**默认放行**——这是安全系统里最危险的失败模式。

运行时仍然有一份职责，但它是**可见性**而非强制：`AgentRuntime` 给模型的工具
清单来自 `dispatcher.allowed_specs()`，无权的工具模型根本看不见，不会浪费
轮次去尝试。这是纵深防御的一层，不是唯一一层。

### 2.3 声明作用域是安全前提

**未声明作用域的工具在任何作用域策略下都放行。**

这是声明式授权的固有代价：工具作者忘了声明，等于默认公开。所以：

- 每个工具都应显式声明作用域，哪怕只是 `("fs:read",)`。
- 接入远端工具时，用 `discover_mcp_tools` 的 `namespace` 或 `scope` 参数——
  它会自动授予 `mcp:<命名空间>` 作用域，这样**一个作用域就能关掉整个不可信
  Server**。

```python
tools = await discover_mcp_tools(session, namespace="web")
# 得到的每个工具都带 scopes=("mcp:web",)
policy = ScopePolicy(granted=frozenset({"mcp:web"}))
```

### 2.4 作用域不会发给模型

`ToolDefinition.scopes` 属于运行时授权依据，**不进 `ToolSpec`**。模型看到的是
name / description / parameters 三样。

## 3. 输入输出安全

### 3.1 参数校验

工具参数按 JSON Schema（draft 2020-12）校验，**校验发生在执行之前**——
参数不合法时工具不会被执行。

发给模型的 schema 与实际校验的 schema 是**同一份**，因此不会出现「告诉模型
A、实际按 B 校验」的错位。

**严格程度由 schema 决定，不由校验器决定。** JSON Schema 的默认语义是允许
多余属性（`additionalProperties`）；要拒绝多余参数，schema 作者必须显式写
`"additionalProperties": false`。校验器不擅自收紧——擅自收紧就变成了错位。

### 3.2 schema 本身可能不合法

`{"type": "bogus"}` 这类畸形 schema 会让 `jsonschema` 抛出 `UnknownType` /
`TypeError` 等**非校验类异常**。schema 可能来自远端 MCP Server，因此这类
异常被收敛成 `ToolSchemaError`——一个畸形 schema 不会打断整个 Agent 运行。

回归测试：`tests/test_runtime_security.py::test_malformed_schema_does_not_break_the_agent_loop`。

### 3.3 输出归一化

工具返回值一律经 `normalize_content` / `normalize_data` 收敛：字符串原样、
结构化对象转 JSON、无法序列化的降级为 `repr`。归一化**绝不抛出**——因此
一个返回奇怪对象的工具不会打断运行。

工具结果被回填进上下文时是**数据**，不是指令。这一点目前靠约定维持，尚未
做来源标记（威胁模型 T6）。

## 4. 数据与隐私

- 学习者数据目前只存在于 `ExecutionContext` 里，**不持久化**（进程退出即丢）。
  这对隐私是好事，但也意味着没有跨会话记忆——T1 的跨会话投毒面因此尚未出现。
- `context.state`（草稿本）**不进模型上下文**。它是工具与应用层的工作区。
- **凭证不进工具上下文**：`ToolContext` 里没有凭证字段。沙箱设计里明确
  要求沙箱环境不继承凭证环境变量。
- 事件历史包含完整对话与工具结果，**日志落盘前需要脱敏**——本项目目前不落盘，
  因此尚无脱敏需求，但接入日志时必须处理。

## 5. 权限与凭证

### 5.1 运行时内

- 运行时**不做认证**：它是库，不是服务。认证属于应用层。
- `ToolContext.caller` 标识调用方（如 `"agent_runtime"`），供自定义权限策略
  按调用方区分授权使用。**内置策略不区分调用方**。
- 模型 API Key 由 `LLMClient` 适配层持有，不进入 `ExecutionContext`，也不进入
  发给工具的 `ToolContext`。

### 5.2 沙箱内（未实现）

沙箱里**不能有凭证**。原则问题而非配置问题：沙箱的边界是「进程能碰到什么」，
凭证在环境变量里就意味着沙箱能碰到凭证。凭证应由宿主侧代理层持有。

## 6. 资源与可用性

| 控制 | 位置 | 说明 |
|---|---|---|
| 步数上限 | `AgentConfig.max_steps` | 兜底，不是目标 |
| 工具调用数上限 | `AgentConfig.max_tool_calls` | |
| token 预算 | `AgentConfig.max_total_tokens` | 按实际消耗，不是按步数 |
| 成本预算 | `AgentConfig.max_cost` | 设了但无法计价时**抛配置错误**，不静默放行 |
| 连续失败上限 | `AgentConfig.max_consecutive_errors` | 模型在同一个错误上打转时停止 |
| 重复调用上限 | `AgentConfig.max_repeated_calls` | 原地打转时停止 |
| 整体超时 | `AgentConfig.timeout` | |
| 工具超时 | `ToolDefinition.timeout` > `ToolDispatcher.default_timeout` | 用 `asyncio.timeout`，取消信号真正送达被调协程 |

**运行时失败不丢弃已完成的成果**：超时与模型失败都返回部分结果（步数、累计
用量、完整对话记录），调用方据此决定是否重跑。

## 7. 已知缺口

按优先级：

1. **T6 目标操纵未缓解**——工具结果与模型输出在上下文里不可区分。缺来源标记
   与内容净化。见威胁模型路径 C。
2. **无沙箱**——权限模型是前置条件，不是替代。见 [sandbox.md](./sandbox.md)。
3. **无人工确认**——高风险工具没有 T10 意义上的拦截。
4. **`additionalProperties` 默认宽松**——严格性靠 schema 作者自觉。
5. **未声明作用域的工具恒放行**——见 §2.3。
6. **无凭证脱敏**——目前因为不落盘而不需要，接入日志时必须补。
7. **上下文不压缩**——长会话必然撑爆窗口（属 Topic 08），间接放大 T4。

## 相关文档

- [威胁模型](./threat-model.md)
- [沙箱隔离设计](./sandbox.md)
- [ADR-0007 · 工具权限与沙箱边界](../architecture/decisions/0007-tool-permission-and-sandbox-boundary.md)
- [Topic 07 · Runtime 测试与安全边界](../learning/topics/07-runtime-testing-and-security.md)
