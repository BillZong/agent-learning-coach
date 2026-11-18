# ADR-0003: 工具系统的类型划分与职责边界

- 状态：已接受
- 日期：2026-11-18

## 背景

架构文档把 “Tool Calling” 列为 Agent Runtime 层的核心组成。`core/llm` 已经定义了
厂商无关的 `ToolSpec`（发给模型的工具描述）与 `ToolCall` / `ToolResult`
（对话内容项），但缺少执行侧：谁来登记可用工具、谁按契约执行、参数在哪一层校验、
工具返回各种类型时如何统一、工具挂死时如何中断。

Topic 03 提出的工程问题正是：**工具调用的参数校验应该在哪一层失败？Registry 与
Dispatcher 的职责边界如何划分，才能避免校验逻辑重复？**

需要回答的问题：

1. 工具的「定义」「上下文」「结果」是否应当是同一个类型？
2. 参数校验放在哪里，才能既不重复校验，又不让工具作者操心？
3. 工具实现返回的类型不受控，如何让回填给模型的内容始终可靠？
4. 工具挂死时如何中断，且不留下悬挂的协程？
5. 单元测试如何避免真实副作用？

参考项目 `ai-agent-from-scratch` 的 `scratch_agents/tools/` 用抽象基类 `BaseTool`
承载 name/description/参数 schema，执行入口是 `execute(context, **kwargs)`，
参数解析靠 `json.loads` 后 `func(**args)`，错误靠字符串拼接成 `"Error: ..."`。
校验、注册、执行三件事都揉在一起，且错误只以文本形式存在。

## 决策

在 `src/agent_learning_coach/core/tools/` 中实现完整工具系统：

### 1. 三个类型对应工具生命周期的三个阶段

| 类型 | 职责 | 面向 |
| ---- | ---- | ---- |
| `ToolDefinition` | 静态契约：名称、描述、参数 JSON Schema、超时、元数据 | 模型与注册表 |
| `ToolContext` | 运行时上下文：会话 id、调用方、截止时间、元数据 | 工具实现 |
| `ToolOutcome` | 归一化结果：状态、内容、结构化数据、错误、耗时 | 调用方与模型 |

三者混在一起会导致工具无法复用、结果无法比较。`ToolDefinition` 可序列化，
能原样转成 `ToolSpec` 发给模型；`ToolContext` 冻结为不可变，保证一次调用内
上下文只读；`ToolOutcome` 是所有执行路径的唯一产物。

### 2. 工具契约为 Protocol，不是基类

`Tool` 是 `typing.Protocol`（`runtime_checkable`），声明 `definition` 属性与
`async execute(context, arguments)` 方法。实现方无需继承。

同时提供 `FunctionTool` 适配器，把普通异步函数包装成 `Tool`，让接入已有函数
不需要写类。handler 签名固定为 `(context, arguments)`——参数不按 schema 字段名
展开，避免「schema 声明的字段名」与「函数签名」之间出现无人负责的隐式映射。

### 3. Registry 管「有什么」，Dispatcher 管「怎么执行」

| | Tool Registry | Tool Dispatcher |
| - | - | - |
| 职责 | 注册、注销、查找、导出工具清单 | 查找、校验、执行（带超时）、归一化 |
| 是否执行工具 | 否 | 是 |
| 失败语义 | 同名注册抛 `ValueError`；查找失败抛 `ToolNotFoundError` | 调用级失败一律归一化成 `ToolOutcome(status="error")` |

注册表若同时管执行，就要承担「配置」与「行为」两类变化原因完全不同的职责。
分开后，Agent Loop 可以在不知道任何工具实现细节的前提下，把注册表当作工具
清单的唯一来源。

重复注册**报错而非静默覆盖**：同名工具意味着配置冲突，覆盖会让问题在很晚
才暴露。

### 4. 参数校验集中在 Dispatcher，只此一处

工具实现只声明「我接受什么」（JSON Schema），Dispatcher 保证「传进来的一定
合法」。若让每个工具在函数体里自己校验，校验逻辑必然重复且不一致。

校验用 `jsonschema` 库（JSON Schema draft 2020-12 参考实现），不自行实现子集
校验器：自行实现的校验器会在边界情况上给出错误的通过/拒绝结论，而这类错误
只在生产数据形态下才暴露。

**发给模型的 schema 与实际校验的 schema 是同一份**：`ToolDefinition.parameters`
既用于生成 `ToolSpec`，也用于参数校验，因此不会出现「告诉模型 A、实际按 B
校验」的错位。

### 5. 调用级失败归一化，系统级失败抛异常

调用工具的主体是模型。模型能读懂错误信息并据此调整下一步（换参数、换工具、
直接回答），而直接抛异常会中断整个 Agent Loop。因此参数不合法、超时、执行
异常都收敛成 `ToolOutcome(status="error")`。

唯一例外是**工具不存在**：这是配置错误而非调用错误，模型无法通过调整参数
修复，因此抛 `ToolNotFoundError`。为方便批量场景，Dispatcher 同时提供
`dispatch_safe`（连工具不存在也归一化）与 `dispatch_all`。

异常体系独立于模型调用：`ToolError` 继承 `Exception` 而非 `LLMError`，
让调用方能区分「模型调用失败」与「工具调用失败」。下设
`ToolNotFoundError` / `ToolValidationError` / `ToolTimeoutError` /
`ToolExecutionError`。

### 6. 超时用 `asyncio.timeout`，优先级为工具定义 > Dispatcher 默认

`asyncio.timeout` 与 `asyncio.wait_for` 的关键差别：前者保证取消信号送达被调用
的协程，工具的 `finally` 清理逻辑能真正执行；后者只是放弃等待，协程仍在后台
运行并可能继续占用资源。对会做外部 IO 的工具，这个差别是实质性的。

超时归一化成 `status="error"`，与其它调用级失败一致。

### 7. 归一化保证「永不抛出」

工具返回什么类型不受控（str / dict / list / Pydantic 模型 / `None` / 异常），
但回填给模型的内容必须始终是可安全阅读的字符串。规则：

- 文本类原样作为内容；
- 结构类（dict / list / Pydantic 模型）JSON 序列化，同时保留原始结构到
  `data` 供程序化消费；
- 其它对象用 `repr`；
- 异常归一化成错误结果。

**归一化本身不得抛出异常**：归一化失败会让原始错误信息丢失，反而更难排查。
因此不可序列化对象降级为 `repr`，`repr` 自身失败时返回 `<unserializable>`。

### 8. 测试替身走公开注入点

单元测试用 `FunctionTool` 包裹替身函数，不 monkeypatch 第三方模块，也不读写
被测对象的私有字段。这与 ADR-0002 确立的约定一致。

## 后果

- 好处：工具定义、上下文、结果三者边界清晰，工具可复用、结果可比较；参数
  校验只有一处实现，不会重复也不会遗漏；发给模型与实际校验的契约必然一致；
  调用级失败不中断 Agent Loop，模型可据此自愈；超时真正取消协程，不留悬挂
  任务；归一化永不抛异常，不会掩盖真实错误；单元测试无需网络与副作用。
- 代价：多一层类型与转换代码；`ToolOutcome` 与 `LLM` 侧的 `ToolResult` 并存
  （前者是执行结果，后者是消息内容项），调用方需通过
  `ToolOutcome.to_message_result()` 转换；handler 签名固定为
  `(context, arguments)`，比 `**kwargs` 更啰嗦但更明确。
- 边界：并发执行工具调用未实现（属 Agent Loop 职责）；工具权限与鉴权未实现
  （当前按注册表可见性控制，生产环境需按会话隔离）；MCP 工具接入未实现
  （见 Topic 04）；流式工具结果未实现。

## 补充说明（2026-11-18）

真实厂商联调暴露两条纸面设计看不出来的约束：

1. **回填必须成对**：厂商要求 tool result 消息与 assistant 的 `tool_calls`
   消息成对出现，只回填结果会得到 `400 invalid params, tool result's tool id
   not found`。因此 `ToolOutcome` 只负责结果侧，原始 `ToolCall` 必须由调用方
   保留并一起回填。该契约已写入接入说明并由回归测试锁定。
2. **handler 签名必须固定**：`(context, arguments)` 与 `(a, b)` 两种写法不会
   报错，只会在运行时表现为参数错位。签名固定为前者并在文档中显式说明。

接入方操作手册见 [`docs/engineering/tools.md`](../../engineering/tools.md)。
