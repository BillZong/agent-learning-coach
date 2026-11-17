# ADR-0002: 用 Protocol 定义 LLM 客户端抽象

- 状态：已接受
- 日期：2026-11-16

## 背景

架构文档把 “Model Abstraction” 列为 Agent Runtime 层的核心组成，并要求学习领域层不依赖任何 LLM 提供商或 Agent 框架。目前项目只有一个具体技术选型（LiteLLM），如果直接在业务代码里调用 `litellm.acompletion`，一旦更换厂商或需要在测试中替换模型，就必须改动业务代码，违背 “技术解耦” 与 “领域优先” 原则。

需要回答的问题：

1. 用什么方式抽象模型调用，才能既支持多厂商，又不引入框架级复杂度？
2. 结构化输出（诊断结果、掌握度判定等）如何在不同厂商上保持一致行为？
3. 单元测试如何避免真实网络调用与非确定性？

参考项目 `ai-agent-from-scratch` 使用具体的 `LlmClient` 类 + `LlmRequest/LlmResponse` Pydantic 模型，并把内容项抽象为 `Message/ToolCall/ToolResult` 三种类型。这套内容模型值得沿用，但客户端本身应为 Protocol 而非基类。

## 决策

在 `src/agent_learning_coach/core/llm/` 中定义厂商无关的 LLM 抽象层：

1. **客户端契约为 Protocol，不是基类**：`LLMClient` 是 `typing.Protocol`（`runtime_checkable`），只声明两个异步方法：
   - `generate(request: LLMRequest) -> LLMResponse`
   - `generate_structured(request: LLMRequest, output_model: type[ResponseT]) -> ResponseT`

   实现方无需继承，只要提供同名方法即可（结构化子类型）。这让 Mock、测试替身和真实适配器可以自由替换。

2. **入参出参全部厂商无关**：`LLMRequest` / `LLMResponse` / `LLMUsage` / `ToolSpec` 以及内容项 `Message` / `ToolCall` / `ToolResult` 都是本项目自己的 Pydantic 模型，不出现任何厂商 SDK 类型。与 OpenAI 兼容格式的转换集中在 `messages.py` 一处。

3. **失败即异常**：调用失败抛 `LLMError`，结构化解析失败抛 `LLMParseError`。不把错误编码进响应对象，避免业务层漏检。

4. **超时是一等错误类别**：超时单独映射为 `LLMTimeoutError`（`LLMError` 子类）。超时是可预期的、可重试的失败，应用层需要能把它与鉴权失败、配额耗尽区分开，从而决定重试或降级。超时在两个层级可配：`LLMRequest.timeout`（单次请求）优先于 `LiteLLMClient(timeout=...)`（客户端默认）。自动重试不入抽象层，由应用层依据异常类型决策。

5. **结构化输出走可移植路径**：统一采用 “提示词约束 JSON Schema + 严格 JSON 解析”，不依赖厂商专属的 response schema 特性，保证跨厂商行为一致。代价是保真度低于厂商原生结构化输出；如未来某厂商需要更高保真度，另写适配器覆盖行为，而不是修改 Protocol。

6. **两类真实适配器**：LiteLLM 承担它有成熟支持的厂商（OpenAI / Anthropic / Gemini），OpenAI SDK 直连承担国产厂商与自建网关。两者都只暴露 `LLMClient` Protocol，业务层无感。

7. **厂商解析显式化且早失败**：`resolve_provider(namespace)` 把厂商归属显式化，`create_client(provider, ...)` 是创建客户端的唯一入口，在构造时校验命名空间、凭证与端点——配置错误立刻抛 `LLMError`，而不是等到第一次真实调用才在网络层报错。厂商切换因此是“一处配置改动”，且错误在启动阶段暴露。

8. **provider 用命名空间表达厂商，模型名保持厂商原生形式**：LiteLLM 的 model 标识是 `<provider>/<model>`，但它只覆盖部分厂商。国产大模型（MiniMax、DeepSeek、通义千问、智谱、Moonshot、SiliconFlow）普遍提供 OpenAI 兼容的 `/chat/completions` 端点，强行套用 litellm 体系会退化成 `openai/minimax-M3.1-Flash` 这种怪异写法——既不准确（实际走的不是 OpenAI），也无法表达真实厂商归属。

   因此建立本项目自己的命名空间注册表（`registry.py`），每个命名空间显式声明协议类型、端点、凭证变量与默认模型：

   - `ProtocolKind.LITELLM`：交给 LiteLLM 路由（OpenAI / Anthropic / Gemini），模型名在内部转成 `anthropic/claude-sonnet-4-5` 形式。
   - `ProtocolKind.OPENAI_COMPATIBLE`：用 OpenAI SDK 直连厂商端点，模型名保持原生形式（`minimax-M3.1-Flash`），不附加任何前缀。

   新增厂商通过 `register_provider` 注册，不修改抽象层。

9. **测试替身内置**：`MockLLMClient`（返回预设结果并记录入参）与 `FailingLLMClient`（始终抛错）用于覆盖正常路径与失败路径。

## 后果

- 好处：业务代码只依赖 `LLMClient`；厂商切换零成本且早失败；国产厂商无需伪造成 `openai/` 前缀；单元测试无需网络、无需额度；结构化输出在各厂商上行为一致；超时可被应用层单独识别并重试。
- 代价：新增一层模型与转换代码；维护两套适配器（LiteLLM 与 OpenAI SDK）；提示词约束式结构化输出在 schema 复杂时解析失败率高于厂商原生方案，失败以 `LLMParseError` 暴露，需要调用方决定重试策略；`create_client` 强制在创建时校验配置，增加了启动期的配置解析开销。
- 边界：流式输出、多模态内容、token 计费精度暂不在本次抽象范围内，待 Agent Loop 主题（Day 5）明确需求后再决定是否扩展 Protocol。扩展时保持向后兼容。

## 补充说明（2026-11-17）

首版实现遗漏了超时与厂商解析，且适配器测试覆盖率仅 36%。补齐内容：新增 `LLMTimeoutError` 与请求级/客户端级超时、`resolve_provider` 与 `create_client`、LiteLLM 适配器的 payload 组装与错误映射测试。

同日二次修订：把厂商解析从“解析 litellm model 字符串”改为“命名空间注册表”，并新增 `OpenAICompatibleClient` 原生适配器，以支持 MiniMax / DeepSeek / 通义千问 / 智谱 / Moonshot / SiliconFlow 等国产厂商，避免 `openai/minimax-M3.1-Flash` 式的怪异命名空间。接入方操作手册见 [`docs/engineering/llm.md`](../../engineering/llm.md)。
