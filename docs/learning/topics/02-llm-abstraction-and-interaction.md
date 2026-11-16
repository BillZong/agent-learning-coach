# Topic: LLM 抽象与模型交互

> Day 2 ｜ Phase 0 · Agent Engineering Foundation ｜ 主教材：Ch2：2.1～2.3 ｜ 外部参考：OpenAI Building Agents Guide ｜ 状态：已完成

## 1. Engineering Question

如何用统一 Protocol 抽象不同模型提供方，使 LLMClient 可以被 Mock 替换，并稳定支持结构化输出？

## 2. Primary Reading

- Book: Ch2：2.1～2.3
- Chapter / Section: Ch2 2.1～2.3
- 核心观点：模型调用是 Agent 运行时的基础设施能力，厂商差异必须被隔离在适配器内。

## 3. Cross References

| 来源 | 核心观点 | 与主教材的关系 |
|---|---|---|
| OpenAI Building Agents Guide | 用接口/协议抽象模型调用，测试时替换为假实现 | 一致：协议即边界 |
| 官方工程指南 | 结构化输出应作为一等能力，而非事后解析文本 | 一致：`generate_structured` 独立成方法 |
| 框架 / 协议文档 | `typing.Protocol` 提供结构化子类型，无需继承 | 补充：实现方式的具体选择 |
| 论文 / 研究资料 | JSON mode 相比自由文本显著降低解析失败率 | 支持：提示词约束 + 严格解析的回退路径 |

## 4. Compare and Challenge

- 不同资料有哪些一致观点？
  都主张“模型调用必须可替换”，并把结构化输出当作契约的一部分。
- 哪些观点存在分歧？
  厂商原生 schema 约束（保真度高、可被模型直接理解）与通用 JSON 提示词约束（跨厂商一致）之间的取舍。
- 分歧来自应用场景、设计目标还是技术演进？
  来自应用场景：跨厂商切换是硬需求时一致性优先；单一厂商深度优化时原生能力优先。
- 哪些结论缺少充分证据？
  “JSON mode 解析失败率更低”的量化结论依赖具体模型与 schema 复杂度，本项目尚未实测。
- 哪些观点不适用于当前项目？
  引入框架级 Agent 抽象（如 LangChain 的 message 体系）不适用，本项目只自建最小协议。

## 5. Engineering Practice

- 实现了什么？
  - `LLMClient` Protocol（`runtime_checkable`）：`generate` + `generate_structured`
  - 厂商无关模型：`LLMRequest` / `LLMResponse` / `LLMUsage` / `ToolSpec` / `Message` / `ToolCall` / `ToolResult`
  - LiteLLM Adapter：`LiteLLMClient`
  - Mock Client：`MockLLMClient`（记录入参）、`FailingLLMClient`（覆盖失败路径）
  - Structured Output：提示词约束 + 严格 JSON 解析，失败抛 `LLMParseError`
  - 异常体系：`LLMError` / `LLMParseError`
- 为什么这样设计？
  Protocol 而非基类：实现方零继承成本，Mock 与真实适配器可互换；入参出参全部自持 Pydantic 模型，杜绝厂商类型泄漏到业务层。
- 有哪些替代方案？
  - 抽象基类（ABC）：强制继承，且无法用于非类实现。
  - 直接函数签名 + 依赖注入：无法统一表达“工具调用 + 结构化输出”的能力集合。
  - 厂商原生 schema 优先：保真度更好但牺牲跨厂商一致性。
- 测试结果如何？
  `pytest` 13 passed（新增 12 条覆盖协议满足性、Mock 行为、结构化解析成功/失败、消息与工具转换、usage 解析）。
  `ruff format --check` / `ruff check` / `mypy` 全部通过。
- 实践结果是否支持阅读材料中的观点？
  支持：把协议作为边界后，测试完全不触网，业务层代码不出现任何厂商标识。

## 6. Reflection

1. 今天最重要的知识是什么？
   抽象边界的价值不在于“以后可能换厂商”，而在于今天就能让测试与业务解耦。
2. 哪个观点改变或修正了原来的理解？
   结构化输出应该作为 Protocol 的一等方法，而不是每个调用点各自解析 JSON，否则一致性无从保证。
3. 当前实现还有哪些局限？
   仅覆盖同步非流式调用；提示词约束式 JSON 解析在复杂 schema 下可能失败；未做重试与超时策略；token 计费只统计输入输出两项。

## 7. Output

- Code / PR: `src/agent_learning_coach/core/llm/`（protocol / models / types / errors / messages / structured / litellm_client / mock）
- Tests: `tests/test_llm_protocol.py`
- ADR: `docs/architecture/decisions/0002-llm-client-protocol.md`
- Article Notes: docs/articles/agent-runtime.md
- Unresolved Questions: 流式输出与多模态是否需要扩展 Protocol；结构化输出的重试策略归属应用层还是运行时层。
