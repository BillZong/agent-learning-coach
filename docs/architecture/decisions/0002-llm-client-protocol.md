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

4. **结构化输出走可移植路径**：统一采用 “提示词约束 JSON Schema + 严格 JSON 解析”，不依赖厂商专属的 response schema 特性，保证跨厂商行为一致。代价是保真度低于厂商原生结构化输出；如未来某厂商需要更高保真度，另写适配器覆盖行为，而不是修改 Protocol。

5. **LiteLLM 作为唯一真实适配器**：`LiteLLMClient` 承担多厂商差异，厂商切换只改模型标识字符串。

6. **测试替身内置**：`MockLLMClient`（返回预设结果并记录入参）与 `FailingLLMClient`（始终抛错）用于覆盖正常路径与失败路径。

## 后果

- 好处：业务代码只依赖 `LLMClient`；厂商切换零成本；单元测试无需网络、无需额度；结构化输出在各厂商上行为一致。
- 代价：新增一层模型与转换代码；提示词约束式结构化输出在 schema 复杂时解析失败率高于厂商原生方案，失败以 `LLMParseError` 暴露，需要调用方决定重试策略。
- 边界：同步接口、流式输出、多模态内容、token 计费精度暂不在本次抽象范围内，待 Agent Loop 主题（Day 5）明确需求后再决定是否扩展 Protocol。扩展时保持向后兼容。
