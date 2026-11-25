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

### 2.1 命名空间（厂商 + 区域）

provider 用**命名空间**表示厂商与区域，模型名保持厂商原生形式，不需要
`openai/`、`anthropic/` 之类的前缀：

```python
from agent_learning_coach.core.llm import create_client

client = create_client("minimax")  # 国际站，用注册表默认模型
client = create_client("minimax", model="MiniMax-M3")  # 手动指定模型
client = create_client("minimax-cn")  # 国内站
client = create_client("deepseek", timeout=30.0)  # 手动配置超时
```

### 2.2 区域约定

多数国产厂商对**国内**与**国际**市场签发不同的 Key、部署不同的端点。用错端点
表现为 401/403，而不是清晰的路由错误，因此区域做进命名空间而非运行时参数：

| 命名空间 | 区域 | 说明 |
| -------- | ---- | ---- |
| `<vendor>` | 国际 / 全球站 | 默认命名空间 |
| `<vendor>-cn` | 中国大陆站 | 与国际站的 Key 不通用 |

这与社区 agent 工具的惯例一致（hermes-agent 的 `minimax` / `minimax-cn`、
VoltAgent 的 `minimax-cn/<model>`、llm-gateway 的 `zai` / `zai_cn`）。

内置命名空间：

| 命名空间 | 厂商 / 区域 | 协议 | 默认模型 | 端点 |
| -------- | ----------- | ---- | -------- | ---- |
| `openai` | OpenAI | LiteLLM | `gpt-4o-mini` | — |
| `anthropic` | Anthropic | LiteLLM | `claude-sonnet-4-5` | — |
| `gemini` | Google Gemini | LiteLLM | `gemini-2.5-flash` | — |
| `minimax` | MiniMax 国际 | OpenAI 兼容 | `MiniMax-M3.1-Flash` | `api.minimax.io/v1` |
| `minimax-cn` | MiniMax 国内 | OpenAI 兼容 | `MiniMax-M3.1-Flash` | `api.minimax.cn/v1` |
| `zhipu` | 智谱 Z.AI 国际 | OpenAI 兼容 | `glm-4.6` | `api.z.ai/api/paas/v4` |
| `zhipu-cn` | 智谱 BigModel 国内 | OpenAI 兼容 | `glm-4.6` | `open.bigmodel.cn/api/paas/v4` |
| `moonshot` | Moonshot Kimi 国际 | OpenAI 兼容 | `kimi-k3` | `api.moonshot.ai/v1` |
| `moonshot-cn` | Moonshot Kimi 国内 | OpenAI 兼容 | `kimi-k3` | `api.moonshot.cn/v1` |
| `qwen` | 阿里云百炼国际 | OpenAI 兼容 | `qwen-plus` | `dashscope-intl.aliyuncs.com` |
| `qwen-cn` | 阿里云百炼国内 | OpenAI 兼容 | `qwen-plus` | `dashscope.aliyuncs.com` |
| `deepseek` | DeepSeek（全球单端点） | OpenAI 兼容 | `deepseek-chat` | `api.deepseek.com/v1` |
| `siliconflow` | SiliconFlow 国际 | OpenAI 兼容 | `deepseek-ai/DeepSeek-V4` | `api.siliconflow.com/v1` |
| `siliconflow-cn` | SiliconFlow 国内 | OpenAI 兼容 | `deepseek-ai/DeepSeek-V4` | `api.siliconflow.cn/v1` |
| `openai-compatible` | 通用 OpenAI 兼容端点 | OpenAI 兼容 | 无（必须显式指定） | 由 `OPENAI_BASE_URL` 决定 |

> **注意**：模型 id 大小写敏感。MiniMax 的真实模型 id 是 `MiniMax-M3.1-Flash`
> （大写 M），不是 `minimax-M3.1-Flash`。国内端点取官方文档值
> `api.minimax.cn/v1`（[官方文档](https://platform.minimax.cn/docs/api-reference/text-openai-api)）。

### 2.3 两种协议

| 协议 | 适配器 | 模型名形态 | 适用厂商 |
| ---- | ------ | ---------- | -------- |
| `LITELLM` | `LiteLLMClient` | 内部转成 `anthropic/claude-sonnet-4-5` | LiteLLM 有成熟支持的 OpenAI / Anthropic / Gemini |
| `OPENAI_COMPATIBLE` | `OpenAICompatibleClient` | 保持原生 `MiniMax-M3.1-Flash` | 国产厂商与自建网关 |

国产厂商普遍提供 OpenAI 兼容的 `/chat/completions` 端点，因此不必强行塞进
LiteLLM 的命名空间体系——那样会得到 `openai/minimax-M3.1-Flash` 这种既不准确
（实际走的不是 OpenAI）也无法表达真实厂商归属的怪异写法。

### 2.4 运行时切换模型

同一客户端内按请求切换模型，`LLMRequest.model` 优先于客户端默认模型：

```python
client = create_client("minimax")

# 单次请求换模型
await client.generate(LLMRequest(model="MiniMax-M3", instructions=[...]))
```

LiteLLM 协议下，裸模型名会自动补上厂商前缀：

```python
client = create_client("anthropic")
await client.generate(
    LLMRequest(model="claude-opus-4-5")
)  # 实际发给 LiteLLM: anthropic/claude-opus-4-5
```

### 2.5 扩展命名空间

接入新厂商时注册一个命名空间，无需修改抽象层：

```python
from agent_learning_coach.core.llm import ProtocolKind, ProviderSpec, register_provider

register_provider(
    ProviderSpec(
        namespace="my-vendor",
        display_name="My Vendor",
        protocol=ProtocolKind.OPENAI_COMPATIBLE,
        region=Region.GLOBAL,
        api_base="https://api.my-vendor.com/v1",
        api_key_env=["MY_VENDOR_API_KEY"],
        default_model="my-model",
    )
)
```

LiteLLM 协议则设 `litellm_prefix="my-vendor"`，由 LiteLLM 完成路由。
国内站点再注册一个 `my-vendor-cn` 命名空间即可。

### 2.6 凭证

凭证通过环境变量提供，不写进代码。命名空间声明的默认变量名：

```bash
# 国际 / 全球站点
export OPENAI_API_KEY=...
export ANTHROPIC_API_KEY=...
export MINIMAX_API_KEY=...
export ZAI_API_KEY=...
export MOONSHOT_API_KEY=...
export DASHSCOPE_API_KEY=...
export DEEPSEEK_API_KEY=...
export SILICONFLOW_API_KEY=...

# 中国大陆站点（与上面同名厂商的 Key 不通用）
export MINIMAX_CN_API_KEY=...
export ZHIPU_CN_API_KEY=...
export MOONSHOT_CN_API_KEY=...
export DASHSCOPE_CN_API_KEY=...
export SILICONFLOW_CN_API_KEY=...
```

国内命名空间的凭证变量按「区域专用 → 通用」顺序查找，因此只设置
`MINIMAX_API_KEY` 也能驱动 `minimax-cn`（若该 Key 属于国内平台）。

也可显式传入 `api_key=` 覆盖。自建网关或代理用 `api_base` 覆盖，或用端点
环境变量（`MINIMAX_CN_BASE_URL` 等）：

```python
client = create_client("minimax-cn", api_base="https://my-gateway/v1")
```

命名空间不存在、凭证缺失、端点缺失都在**创建时**抛 `LLMError`，不等到首次
真实调用才在网络层报错。

### 2.7 排查配置

列出全部 provider 的区域、端点与所需环境变量：

```python
from agent_learning_coach.core.llm import describe_providers

for item in describe_providers():
    print(item["namespace"], item["region"], item["api_base"], item["api_key_env"])
```

排查 401 时先确认三件事：**命名空间的区域**与 Key 签发区域是否一致、
**端点**是否为该区域、**模型 id 大小写**是否正确。

## 3. 超时

超时是**可预期的、可重试的**失败，因此单独映射为 `LLMTimeoutError`，
应用层可以据此决定重试或降级。

优先级：请求级 > 客户端级。

```python
client = create_client("minimax", timeout=60.0)  # 客户端默认
request = LLMRequest(timeout=5.0)  # 单次请求覆盖
```

超时单位为秒，透传给 LiteLLM / OpenAI SDK 的 `timeout` 参数。

### 错误分类

| 异常 | 含义 | 建议处理 |
| ---- | ---- | -------- |
| `LLMTimeoutError` | 超过超时时间 | 退避重试；或降级到更小模型 |
| `LLMParseError` | 结构化输出无法解析为目标模型 | 读 `.field_errors` 定位字段；换 prompt 措辞；或退回非结构化路径 |
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

### 结构化校验失败：错误分三档

`LLMParseError` 不只是一句消息，它保留**原始错误条目**（ADR-0022）：

| 访问方式 | 内容 | 消费者 |
| -------- | ---- | ------ |
| `exc.field_errors` | `FieldError` 元组：归一化字段 + **原始条目** | 程序逻辑 |
| `exc.summary` | 「字段：原因」逐条连成一行 | **模型**（重试反馈） |
| `exc.detail` | 摘要 + 原始条目 | 排查问题的人 |

```python
try:
    await client.generate_structured(request, Diagnosis)
except LLMParseError as exc:
    for field_error in exc.field_errors:
        print(field_error.loc, field_error.type, field_error.msg)
        print(field_error.raw)  # 原始条目：厂商上下文、输入值、错误码
    feedback = exc.summary  # 喂给模型重试
```

**摘要与细节是同一份事实的两种视图**：摘要可随时从原始条目重新生成，反过来不行。
程序应当读 `.field_errors` 属性，而不是解析异常消息文本。

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

单元测试不需要网络、不消耗额度。

### 6.1 业务层：Protocol 级替身

```python
from agent_learning_coach.core.llm import MockLLMClient, FailingLLMClient

client = MockLLMClient(text="固定回答")
client.requests  # 记录每次调用的 LLMRequest，可断言入参

failing = FailingLLMClient("boom")  # 覆盖失败路径
```

两者均满足 `LLMClient` Protocol，可直接注入业务代码。

### 6.2 适配器层：公开注入点

测试适配器自身（断言 payload、映射错误、观察连接池生命周期）时，
**不要**改写第三方模块或访问适配器私有字段。两个适配器都提供了公开注入点：

| 适配器 | 注入参数 | 用途 |
| ------ | -------- | ---- |
| `OpenAICompatibleClient` | `sdk_client=` | 直接给出一个 SDK 客户端实例（替身或已配置好的真实客户端） |
| `OpenAICompatibleClient` | `sdk_client_factory=` | 提供构造工厂，接收与 `AsyncOpenAI` 相同的关键字参数，可观察构造时机与参数 |
| `LiteLLMClient` | `completion=` | 注入异步 completion 函数，签名与 `litellm.acompletion` 一致 |

```python
# 注入实例
client = OpenAICompatibleClient(
    model="MiniMax-M3.1-Flash",
    api_base="https://api.minimax.cn/v1",
    api_key="k",
    sdk_client=fake_openai,
)

# 注入工厂：观察惰性创建与构造参数
client = OpenAICompatibleClient(
    model="MiniMax-M3.1-Flash",
    api_base="https://api.minimax.cn/v1",
    api_key="k",
    sdk_client_factory=lambda **kwargs: FakeOpenAI(**kwargs),
)

# LiteLLM：注入 completion
client = LiteLLMClient(model="gpt-4o-mini", completion=fake_acompletion)
```

注入点同样可经 `create_client` 透传：

```python
client = create_client("minimax-cn", sdk_client=fake_openai)
client = create_client("openai", completion=fake_acompletion)
```

对应的公开只读接口：

| 方法 | 说明 |
| ---- | ---- |
| `OpenAICompatibleClient.get_sdk_client()` | 返回底层 SDK 客户端，必要时惰性创建 |
| `OpenAICompatibleClient.build_sdk_client_kwargs()` | 返回 SDK 客户端初始化参数 |
| `LiteLLMClient.resolve_completion()` | 返回实际使用的 completion 函数 |

**注入对象的生命周期由注入方负责**：`aclose()` 只关闭本类自建的客户端，
不处理注入的实例。

## 7. 使用约束

1. **不要在业务层 import `litellm` 或 `openai`**。所有模型访问经由 `LLMClient`。
2. **不要捕获异常后吞掉**。`LLMError` 及其子类必须向上传递或显式转换，
   让应用层能区分超时、解析失败与其他错误。
3. **provider 命名空间与模型标识集中管理**。在应用层配置中维护命名空间与模型，
   不要散落在业务代码里；厂商切换应只改一处配置。
4. **不要用 `openai/<厂商模型名>` 这种写法**。厂商归属与区域由命名空间表达，
   模型名保持厂商原生形式。
5. **注意区域与 Key 的匹配**。国内站 Key 在国际站会返回 401，
   命名空间选错是最常见的配置错误。
6. **单元测试一律用 Mock 替身**；真实调用留给集成测试与手工验证。
   打桩一律走公开注入点（第 6.2 节），不要 `monkeypatch` 第三方模块，
   也不要读写适配器的私有字段——那类测试会在重构时静默失效或误触真实网络。
7. **短生命周期脚本要显式关闭客户端**。`OpenAICompatibleClient` 内部持有
   httpx 连接池，不关闭时会在所属 event loop 关闭后被 GC，产生
   `Event loop is closed` 噪声：

   ```python
   client = create_client("minimax-cn")
   try:
       response = await client.generate(request)
   finally:
       await client.aclose()  # 仅 OpenAICompatibleClient 提供
   ```

   客户端内部按 event loop 缓存 SDK 连接池，跨 loop 复用时会自动重建，因此
   同一实例用于多个 `asyncio.run()` 是安全的。

## 8. 当前未覆盖范围

| 能力 | 状态 | 备注 |
| ---- | ---- | ---- |
| 流式输出 | 未实现 | 待 Day 5 Agent Loop 明确需求后决定是否扩展 Protocol |
| 多模态内容 | 未实现 | 当前 `Message.content` 仅支持文本 |
| 自动重试 | 未实现 | 由应用层依据 `LLMTimeoutError` / `LLMError` 决策 |
| 厂商原生结构化输出 | 未实现 | 当前走可移植路径，见第 4 节 |
| Token 精细计费 | 部分 | 仅统计输入/输出两项，不含缓存与推理 token |
| 供应商特有参数 | 未实现 | 通过 `LLMRequest.extra` 透传，未经类型校验 |
| 推理内容（reasoning_content） | 未提取 | MiniMax / GLM 的思考内容当前落在 `Message.content` 之外，未纳入内容模型 |
| 真实厂商联调 | 部分验证 | MiniMax 国内站已实测真实推理成功；其余厂商仅验证端点可达性 |

