# 工具系统接入说明（Tool System Integration Notes）

> 状态：生效
> 对应代码：`src/agent_learning_coach/core/tools/`
> 决策依据：[ADR-0003](../architecture/decisions/0003-tool-system.md)

本文是接入方（Agent Loop、应用层、学习领域层）使用工具系统能力的操作手册。

## 1. 核心契约

工具系统把「工具是什么」「工具在什么上下文执行」「工具产出什么」分成三个类型：

| 类型 | 含义 | 面向 |
| ---- | ---- | ---- |
| `ToolDefinition` | 静态契约：名称、描述、参数 JSON Schema、超时 | 模型与注册表 |
| `ToolContext` | 运行时上下文：会话、调用方、截止时间、元数据 | 工具实现 |
| `ToolOutcome` | 归一化结果：状态、内容、错误、耗时 | 调用方与模型 |

`Tool` 是 Protocol，实现方无需继承：

```python
class Tool(Protocol):
    @property
    def definition(self) -> ToolDefinition: ...
    async def execute(self, context: ToolContext, arguments: dict[str, Any]) -> Any: ...
```

快速接入用 `FunctionTool` 包裹普通函数：

```python
from agent_learning_coach.core.tools import FunctionTool, ToolContext, ToolDefinition


async def search(context: ToolContext, arguments: dict) -> str:
    """handler 签名固定为 (context, arguments)。"""
    return f"检索 {arguments['query']} 的结果"


tool = FunctionTool(
    definition=ToolDefinition(
        name="search",
        description="检索学习资料",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    ),
    func=search,
)
```

> **注意**：handler 签名固定为 `(context, arguments)`，参数**不会**按 schema 字段名
> 展开。schema 字段名与 handler 取值方式的一致性由工具作者自己保证。

## 2. Registry 与 Dispatcher 的职责边界

这是本系统最关键的设计约束：**注册表管「有什么」，调度器管「怎么执行」**。

| | Tool Registry | Tool Dispatcher |
| - | - | - |
| 职责 | 注册、注销、查找、导出工具清单 | 查找、校验、执行（带超时）、归一化 |
| 失败语义 | 同名注册抛 `ValueError`；查找失败抛 `ToolNotFoundError` | 调用级失败一律归一化成 `ToolOutcome(status="error")` |
| 是否执行工具 | 否 | 是 |

为什么这样切分：

- 注册表如果也管执行，就要同时承担「配置」与「行为」两类变化原因完全不同的职责。
  分开后，Agent Loop 可以在完全不知道工具实现细节的前提下，把注册表当作工具清单的来源。
- **参数校验只发生在 Dispatcher 一处**。如果让每个工具自己在函数体里校验参数，
  校验逻辑必然重复且不一致。让工具只声明「我接受什么」，Dispatcher 保证
  「传进来的一定合法」。

## 3. Schema Validation

参数按 JSON Schema（draft 2020-12）校验，使用 `jsonschema` 库——它是 JSON Schema
的参考实现。自行实现子集校验器会在边界情况上给出错误的通过/拒绝结论，而这类
错误只在生产数据形态下才暴露。

关键设计：**发给模型的 schema 与实际校验的 schema 是同一份**。
`ToolDefinition.parameters` 同时用于生成 `ToolSpec`（发给模型）和参数校验，
因此不会出现「告诉模型 A、实际按 B 校验」的错位。

```python
from agent_learning_coach.core.tools import validate_arguments

validate_arguments(spec.parameters, {"query": "闭包"})  # 通过
validate_arguments(spec.parameters, {})  # 抛 ToolValidationError
```

校验失败抛 `ToolValidationError`，`errors` 字段含逐条明细（参数路径 + 失败原因）。

支持的常用子集：`type`、`properties`、`required`、`additionalProperties`、
`items`、`enum`、`minimum` / `maximum`、`minLength` / `maxLength`。

## 4. Tool Timeout

超时优先级：**工具定义上的 `timeout` > Dispatcher 的 `default_timeout`**。

```python
dispatcher = ToolDispatcher(registry, default_timeout=30.0)
```

实现用 `asyncio.timeout`：它既能取消挂起的 await，又能保证取消信号送达被调用的
协程，而不是仅仅放弃等待——工具的 `finally` 清理逻辑能真正执行。

超时归一化成 `status="error"`、`error` 含 `ToolTimeoutError`，与其它调用级失败
一致，模型可以据此调整下一步。

## 5. Tool Result Normalization

工具内部返回什么类型不受控（str / dict / list / Pydantic 模型 / `None` / 异常），
但回填给模型的内容必须**始终是可安全阅读的字符串**。归一化规则：

| 工具返回 | `content` | `data` |
| -------- | --------- | ------ |
| `str` | 原样 | None |
| `dict` / `list` / `tuple` | JSON | dict 时为原值，否则 None |
| Pydantic 模型 | JSON | `model_dump()` |
| `None` | 空字符串 | None |
| 其它对象 | `repr(value)` | None |
| 异常 | 错误描述 | None |

**归一化本身不得抛出异常**：归一化失败会让原始错误信息丢失，反而更难排查。
因此不可序列化的对象降级为 `repr`，`repr` 自身失败时返回 `<unserializable>`。

## 6. 在 Agent Loop 中使用

### 6.1 把工具清单交给模型

```python
request = LLMRequest(
    contents=[Message(role="user", content="帮我检索闭包的概念")],
    tools=registry.specs(),  # 直接来自注册表
)
response = await client.generate(request)
```

### 6.2 执行模型请求的工具调用

```python
for item in response.contents:
    if isinstance(item, ToolCall):
        outcome = await dispatcher.dispatch(item.name, item.arguments, context)
```

### 6.3 回填结果（**必须成对**）

```python
outcome = await dispatcher.dispatch(item.name, item.arguments, context)
result = outcome.to_message_result(item.tool_call_id)

next_request = LLMRequest(
    contents=[
        Message(role="user", content="帮我检索闭包的概念"),
        item,  # 原始 ToolCall —— 必须保留
        result,  # 工具结果
    ],
)
```

> **重要**：厂商要求 tool result 消息与 assistant 的 `tool_calls` 消息**成对出现**，
> 只回填结果会得到 `400 invalid params, tool result's tool id not found`。
> `ToolOutcome` 只负责结果侧，原始 `ToolCall` 必须由调用方保留并一起回填。

## 7. 错误处理

| 异常 | 含义 | 建议处理 |
| ---- | ---- | -------- |
| `ToolNotFoundError` | 工具未注册 | 配置错误，修复注册表；重试无意义 |
| `ToolValidationError` | 参数不满足 schema | 回填给模型，让模型修正参数 |
| `ToolTimeoutError` | 执行超时 | 回填给模型，或降级到更轻量的工具 |
| `ToolExecutionError` | 工具内部抛异常 | 回填给模型，或按业务规则处理 |

`ToolError` 是四者的共同基类，继承自 `Exception`（**不**继承 `LLMError`），
让调用方能区分「模型调用失败」与「工具调用失败」。

Dispatcher 提供两种入口：

| 方法 | 工具不存在时 |
| ---- | ------------ |
| `dispatch` | 抛 `ToolNotFoundError` |
| `dispatch_safe` | 返回 `status="error"` 的结果 |

批量执行用 `dispatch_all`（顺序执行，任一失败不中断整批）。

## 8. 测试替身

单元测试不需要网络、不消耗额度。用 `FunctionTool` 包裹替身函数即可：

```python
async def _handler(context: ToolContext, arguments: dict) -> str:
    return "固定回答"


tool = FunctionTool(
    definition=ToolDefinition(name="stub", description="测试替身"),
    func=_handler,
)
```

断言执行入参用替身自己记录：

```python
class _Recorder:
    def __init__(self):
        self.calls = []

    async def __call__(self, context: ToolContext, arguments: dict) -> str:
        self.calls.append(arguments)
        return "ok"
```

## 9. 使用约束

1. **不要在业务层直接调用工具**。所有工具执行经由 `ToolDispatcher`，
   否则校验与超时会被绕过。
2. **工具不要吞掉异常**。抛出的异常会被归一化成错误结果回填给模型；
   静默失败会让模型以为工具成功了。
3. **参数校验只在 Dispatcher 做**。工具内部不要重复校验 schema 已覆盖的规则。
4. **工具返回原始数据，不要返回给人看的文本**。文本化由归一化层负责，
   这样同一份结果既能喂给模型，也能被程序化消费。
5. **回填结果必须成对**。原始 `ToolCall` 与 `ToolResult` 一起回填。
6. **给工具设置合理的 `timeout`**。挂死的工具会拖垮整个 Agent Loop。
7. **单元测试一律用 `FunctionTool` 打桩**，不要 monkeypatch 第三方模块，
   也不要读写被测对象的私有字段。

## 10. 当前未覆盖范围

| 能力 | 状态 | 备注 |
| ---- | ---- | ---- |
| 并发执行工具调用 | 未实现 | 当前 `dispatch_all` 为顺序执行；并发属 Agent Loop 职责（Day 5） |
| 工具结果流式返回 | 未实现 | 待 Protocol 扩展时一并决定 |
| 重试 | 未实现 | 由 Agent Loop 依据错误类型决策 |
| 工具权限与鉴权 | 未实现 | 当前按注册表可见性控制；生产环境需按会话隔离 |
| MCP 工具接入 | 未实现 | 见 Topic 04（MCP Protocol） |
| 结构化工具结果 | 部分 | `data` 字段保留 dict 形态；Pydantic 模型已展平为 dict |
