# LLM 接入说明（LLM Integration Notes）

> 状态：生效
> 对应代码：`src/agent_learning_coach/core/llm/`
> 决策依据：[ADR-0002](../architecture/decisions/0002-llm-client-protocol.md)

本文是接入方（应用层、Agent Runtime、领域层）使用 LLM 能力的操作手册。

## 1. 核心契约

`LLMClient` 是 `typing.Protocol`，实现方无需继承，只需提供同名方法：

| 方法 | 用途 | 失败行为 |
| ---- | ---- | -------- |
| `async generate(request: LLMRequest) -> LLMResponse` | 普通调用，返回统一响应 | 抛 `LLMError` / `LLMTimeoutError` |
| `async generate_structured(request, output_model) -> ResponseT` | 结构化调用，返回校验过的模型 | 抛 `LLMError` / `LLMTimeoutError` / `LLMParseError` |

调用方只依赖 Protocol，永远不直接 import 厂商 SDK。

## 2. 切换 Provider

切换厂商 = 换模型标识字符串，业务代码零改动。

```python
from agent_learning_coach.core.llm import create_client

client = create_client("anthropic/claude-sonnet-4-5", timeout=60.0)
```

常用模型标识：

| 厂商 | 标识示例 | 解析结果 |
| ---- | -------- | -------- |
| OpenAI | `gpt-4o-mini` | `openai` |
| Anthropic | `anthropic/claude-sonnet-4-5` | `anthropic` |
| Gemini | `gemini/gemini-2.5-flash` | `gemini` |

`create_client` 会先调用 `resolve_provider(model)` 解析厂商，标识写错时立即抛
`LLMError`，而不是等到第一次真实调用才在网络层报错。

也可按请求覆盖模型（同一客户端内切换模型）：

```python
request = LLMRequest(model="gemini/gemini-2.5-flash")
```

### 凭证

凭证通过环境变量提供，不写进代码：

```bash
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...
```

自建网关或代理用 `api_base` 覆盖：

```python
client = create_client("gpt-4o-mini", api_base="https://my-gateway/v1")
```

## 3. 超时

超时是**可预期的、可重试的**失败，因此单独映射为 `LLMTimeoutError`，
应用层可以据此决定重试或降级。

优先级：请求级 > 客户端级。

```python
client = create_client("gpt-4o-mini", timeout=60.0)  # 客户端默认
request = LLMRequest(timeout=5.0)  # 单次请求覆盖
```

超时单位为秒，透传给 LiteLLM 的 `timeout` 参数。

### 错误分类

| 异常 | 含义 | 建议处理 |
| ---- | ---- | -------- |
| `LLMTimeoutError` | 超过超时时间 | 退避重试；或降级到更小模型 |
| `LLMParseError` | 结构化输出无法解析为目标模型 | 换 prompt 措辞；或退回非结构化路径 |
| `LLMError` | 其他调用失败（鉴权、限流、厂商 5xx） | 按需重试；检查凭证与配额 |

## 4. 结构化输出

统一走「提示词约束 + 严格 JSON 解析」的可移植路径，不依赖厂商专属的
response schema 特性，保证跨厂商行为一致。

```python
from pydantic import BaseModel
from agent_learning_coach.core.llm import LLMRequest


class Diagnosis(BaseModel):
    weak_points: list[str]
    summary: str


result = await client.generate_structured(
    LLMRequest(instructions=["诊断学习者的知识薄弱点"]),
    Diagnosis,
)
```

实现细节：

1. `build_structured_instruction` 把目标模型的 JSON Schema 追加到指令中。
2. `parse_structured_output` 剥离 Markdown 代码块包裹后严格解析 JSON，
   再用 Pydantic 校验；任一步失败抛 `LLMParseError`。
3. 解析失败**不会**返回半成品对象。

已知限制：schema 复杂时解析失败率高于厂商原生结构化输出。需要更高保真度的
场景可另写适配器覆盖 `generate_structured`，不改 Protocol。

## 5. 工具调用

请求通过 `ToolSpec` 声明可用工具，`ToolSpec.parameters` 使用 JSON Schema：

```python
from agent_learning_coach.core.llm import ToolSpec

request = LLMRequest(
    instructions=["检索学习资料"],
    tools=[
        ToolSpec(
            name="search",
            description="检索学习资料",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        )
    ],
)
```

模型返回的工具调用在 `LLMResponse.contents` 中表现为 `ToolCall`；
工具执行结果用 `ToolResult` 回填到下一次请求的 `contents`。

工具执行循环属于 Agent Loop（Day 5 主题），本包只负责消息层面的表达与转换。

## 6. 测试替身

单元测试不需要网络、不消耗额度：

```python
from agent_learning_coach.core.llm import MockLLMClient, FailingLLMClient

client = MockLLMClient(text="固定回答")
client.requests  # 记录每次调用的 LLMRequest，可断言入参

failing = FailingLLMClient("boom")  # 覆盖失败路径
```

两者均满足 `LLMClient` Protocol，可直接注入业务代码。

## 7. 使用约束

1. **不要在业务层 import `litellm`**。所有模型访问经由 `LLMClient`。
2. **不要捕获异常后吞掉**。`LLMError` 及其子类必须向上传递或显式转换，
   让应用层能区分超时、解析失败与其他错误。
3. **模型标识集中管理**。在应用层配置中维护模型标识，不要散落在业务代码里；
   厂商切换应只改一处配置。
4. **单元测试一律用 Mock 替身**；真实调用留给集成测试与手工验证。

## 8. 当前未覆盖范围

| 能力 | 状态 | 备注 |
| ---- | ---- | ---- |
| 流式输出 | 未实现 | 待 Day 5 Agent Loop 明确需求后决定是否扩展 Protocol |
| 多模态内容 | 未实现 | 当前 `Message.content` 仅支持文本 |
| 自动重试 | 未实现 | 由应用层依据 `LLMTimeoutError` / `LLMError` 决策 |
| 厂商原生结构化输出 | 未实现 | 当前走可移植路径，见第 4 节 |
| Token 精细计费 | 部分 | 仅统计输入/输出两项，不含缓存与推理 token |
