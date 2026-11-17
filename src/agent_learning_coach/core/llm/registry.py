"""Provider 命名空间注册表。

问题背景：LiteLLM 的 model 标识是 ``<provider>/<model>`` 形式，
但它只覆盖部分厂商。对国产大模型（MiniMax、DeepSeek、通义千问、智谱等），
强行套用会退化成 ``openai/minimax-M3.1-Flash`` 这种怪异命名空间——
既不准确（实际走的不是 OpenAI），也无法表达真实的厂商归属。

区域问题：多数国产厂商对**国内**与**国际**市场签发不同的 Key、部署不同的端点。
用错端点通常表现为 401/403，而不是清晰的路由错误。因此本项目把区域做进
命名空间，而不是做成运行时参数：

- ``<vendor>``：国际 / 全球站点
- ``<vendor>-cn``：中国大陆站点

这与社区主流 agent 工具的惯例一致（hermes-agent 的 ``minimax`` /
``minimax-cn``、VoltAgent 的 ``minimax-cn/<model>``、llm-gateway 的 ``zai`` /
``zai_cn``）。

每个命名空间显式声明协议类型与端点：
- ``OPENAI_COMPATIBLE``：直接用 OpenAI 兼容协议访问厂商自有端点，
  模型名就是厂商的原生模型名，不加任何前缀。
- ``LITELLM``：交给 LiteLLM 路由（OpenAI / Anthropic / Gemini 等）。
"""

from __future__ import annotations

import os
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ProtocolKind(StrEnum):
    """厂商接入协议类型。"""

    OPENAI_COMPATIBLE = "openai_compatible"
    """厂商提供 OpenAI 兼容的 ``/chat/completions`` 端点。

    用 OpenAI SDK 搭配 ``base_url`` 直接访问，模型名保持厂商原生形式。
    """

    LITELLM = "litellm"
    """由 LiteLLM 负责路由与协议适配，model 标识需带厂商前缀。"""


class Region(StrEnum):
    """厂商站点区域。"""

    GLOBAL = "global"
    """国际 / 全球站点。"""

    CN = "cn"
    """中国大陆站点。"""


class ProviderSpec(BaseModel):
    """一个 provider 命名空间的注册信息。"""

    namespace: str
    display_name: str
    protocol: ProtocolKind
    region: Region = Region.GLOBAL
    api_base: str | None = None
    api_key_env: list[str] = Field(default_factory=list)
    """凭证环境变量名列表，按顺序取第一个非空值。为空表示必须显式传入 api_key。"""
    api_base_env: list[str] = Field(default_factory=list)
    """端点覆盖用的环境变量名列表，按顺序取第一个非空值。为空时使用 ``api_base``。

    用于同一厂商在不同部署（自建网关、代理）下的端点切换，
    也用于兼容既有的 ``OPENAI_BASE_URL`` / ``OPENAI_API_BASE`` 风格配置。
    """

    default_model: str | None = None
    litellm_prefix: str | None = None
    """``protocol=LITELLM`` 时使用的 LiteLLM 厂商前缀。"""

    extra_headers: dict[str, str] = Field(default_factory=dict)
    notes: str = ""

    def litellm_model(self, model: str) -> str:
        """把原生模型名转换为 LiteLLM 需要的 ``<provider>/<model>`` 形式。"""
        if self.protocol is not ProtocolKind.LITELLM:
            return model
        if self.litellm_prefix is None:
            return model
        return f"{self.litellm_prefix}/{model}"

    def resolved_model(self, model: str | None) -> str:
        """确定最终使用的模型名。

        Args:
            model: 调用方手动指定的模型；为 None 时用命名空间默认模型。

        Returns:
            实际发送给厂商的模型标识。

        Raises:
            ValueError: 未指定模型且命名空间没有默认模型时抛出。
        """
        chosen = model or self.default_model
        if not chosen:
            raise ValueError(
                f"provider {self.namespace!r} 未指定模型，且没有默认模型可用；请显式传入 model 参数"
            )
        return chosen

    def resolved_api_base(self, api_base: str | None = None) -> str | None:
        """确定实际使用的端点。

        优先级：显式传入 > ``api_base_env`` 环境变量 > 注册表默认值。

        Args:
            api_base: 调用方显式指定的端点。

        Returns:
            端点 base url；无端点要求时返回 None。
        """
        if api_base:
            return api_base
        for env_name in self.api_base_env:
            from_env = os.environ.get(env_name)
            if from_env:
                return from_env
        return self.api_base

    def resolved_api_key(self, api_key: str | None = None) -> str | None:
        """确定实际使用的凭证。

        优先级：显式传入 > ``api_key_env`` 环境变量列表（按顺序取第一个非空值）。

        Args:
            api_key: 调用方显式指定的凭证。

        Returns:
            凭证；未找到时返回 None。
        """
        if api_key:
            return api_key
        for env_name in self.api_key_env:
            from_env = os.environ.get(env_name)
            if from_env:
                return from_env
        return None


_REGISTRY: dict[str, ProviderSpec] = {}


def register_provider(spec: ProviderSpec, *, overwrite: bool = False) -> None:
    """注册一个 provider 命名空间。

    Args:
        spec: 注册信息。
        overwrite: 是否允许覆盖同名命名空间。

    Raises:
        ValueError: 命名空间已存在且未指定 overwrite 时抛出。
    """
    existing = _REGISTRY.get(spec.namespace)
    if existing is not None and not overwrite:
        raise ValueError(
            f"provider 命名空间 {spec.namespace!r} 已注册；如需覆盖请传 overwrite=True"
        )
    _REGISTRY[spec.namespace] = spec


def unregister_provider(namespace: str) -> None:
    """移除一个 provider 命名空间。

    Args:
        namespace: 待移除的命名空间。

    Raises:
        KeyError: 命名空间未注册时抛出。
    """
    _REGISTRY.pop(namespace)


def get_provider(namespace: str) -> ProviderSpec:
    """按命名空间取 provider 注册信息。

    Raises:
        KeyError: 命名空间未注册时抛出。
    """
    try:
        return _REGISTRY[namespace]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY))
        raise KeyError(f"未知的 provider 命名空间 {namespace!r}；已注册: {known}") from None


def list_providers() -> list[ProviderSpec]:
    """列出全部已注册的 provider，按命名空间排序。"""
    return [_REGISTRY[key] for key in sorted(_REGISTRY)]


def provider_namespaces() -> list[str]:
    """列出全部已注册的命名空间。"""
    return sorted(_REGISTRY)


def build_client_config(
    spec: ProviderSpec,
    *,
    model: str | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
    timeout: float | None = None,
    extra_headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """构造适配器初始化参数。

    Args:
        spec: provider 注册信息。
        model: 手动指定的模型；为 None 时用默认模型。
        api_key: 手动指定的凭证；为 None 时从 ``spec.api_key_env`` 读取环境变量。
        api_base: 手动指定的端点；为 None 时用 ``spec.api_base_env`` 或
            ``spec.api_base``。
        timeout: 默认超时秒数。
        extra_headers: 附加请求头；与注册信息中的合并。
        **kwargs: 其他透传给适配器的配置。

    Returns:
        适配器构造参数。

    Raises:
        ValueError: 缺少凭证或端点时抛出。
    """
    resolved_model = spec.resolved_model(model)
    resolved_key = spec.resolved_api_key(api_key)

    if spec.protocol is ProtocolKind.OPENAI_COMPATIBLE:
        resolved_base = spec.resolved_api_base(api_base)
        if not resolved_base:
            raise ValueError(f"provider {spec.namespace!r} 缺少 api_base")
        if not resolved_key:
            expected = " 或 ".join(spec.api_key_env) if spec.api_key_env else "<API_KEY>"
            raise ValueError(
                f"provider {spec.namespace!r} 缺少凭证；"
                f"请设置环境变量 {expected} 或显式传入 api_key"
            )
        headers = {**spec.extra_headers, **(extra_headers or {})}
        return {
            "model": resolved_model,
            "api_base": resolved_base,
            "api_key": resolved_key,
            "timeout": timeout,
            "extra_headers": headers,
            **kwargs,
        }

    # LITELLM 协议：凭证交给 LiteLLM 自己解析（支持多厂商鉴权方式）
    config: dict[str, Any] = {"model": spec.litellm_model(resolved_model), "timeout": timeout}
    if resolved_key:
        config["api_key"] = resolved_key
    resolved_base = spec.resolved_api_base(api_base)
    if resolved_base:
        config["api_base"] = resolved_base
    config.update(kwargs)
    return config


def _register_builtin_providers() -> None:
    """注册内置的 provider 命名空间。

    区域约定：``<vendor>`` 为国际站，``<vendor>-cn`` 为中国大陆站。
    国内/国际站点通常签发不同的 Key，因此凭证环境变量也分开。
    """
    builtin = [
        ProviderSpec(
            namespace="openai",
            display_name="OpenAI",
            protocol=ProtocolKind.LITELLM,
            api_key_env=["OPENAI_API_KEY"],
            default_model="gpt-4o-mini",
            litellm_prefix="openai",
        ),
        ProviderSpec(
            namespace="anthropic",
            display_name="Anthropic",
            protocol=ProtocolKind.LITELLM,
            api_key_env=["ANTHROPIC_API_KEY"],
            default_model="claude-sonnet-4-5",
            litellm_prefix="anthropic",
        ),
        ProviderSpec(
            namespace="gemini",
            display_name="Google Gemini",
            protocol=ProtocolKind.LITELLM,
            api_key_env=["GEMINI_API_KEY"],
            default_model="gemini-2.5-flash",
            litellm_prefix="gemini",
        ),
        # --- MiniMax：国际站 / 国内站 ---
        ProviderSpec(
            namespace="minimax",
            display_name="MiniMax (International)",
            protocol=ProtocolKind.OPENAI_COMPATIBLE,
            region=Region.GLOBAL,
            api_base="https://api.minimax.io/v1",
            api_key_env=["MINIMAX_API_KEY"],
            default_model="MiniMax-M3.1-Flash",
            notes="国际站；国内站请用 minimax-cn",
        ),
        ProviderSpec(
            namespace="minimax-cn",
            display_name="MiniMax (China)",
            protocol=ProtocolKind.OPENAI_COMPATIBLE,
            region=Region.CN,
            api_base="https://api.minimaxi.com/v1",
            api_key_env=["MINIMAX_CN_API_KEY", "MINIMAX_API_KEY"],
            api_base_env=["MINIMAX_CN_BASE_URL"],
            default_model="MiniMax-M3.1-Flash",
            notes="国内站；Key 与国际站不通用",
        ),
        # --- 智谱 GLM：国际站（Z.AI）/ 国内站（BigModel）---
        ProviderSpec(
            namespace="zhipu",
            display_name="Z.AI / 智谱 (International)",
            protocol=ProtocolKind.OPENAI_COMPATIBLE,
            region=Region.GLOBAL,
            api_base="https://api.z.ai/api/paas/v4",
            api_key_env=["ZAI_API_KEY"],
            default_model="glm-4.6",
            notes="国际站 Z.AI；国内站请用 zhipu-cn",
        ),
        ProviderSpec(
            namespace="zhipu-cn",
            display_name="智谱 BigModel (China)",
            protocol=ProtocolKind.OPENAI_COMPATIBLE,
            region=Region.CN,
            api_base="https://open.bigmodel.cn/api/paas/v4",
            api_key_env=["ZHIPU_CN_API_KEY", "ZAI_API_KEY"],
            api_base_env=["ZHIPU_CN_BASE_URL"],
            default_model="glm-4.6",
            notes="国内站 BigModel；Key 与国际站不通用",
        ),
        # --- Moonshot Kimi：国际站 / 国内站 ---
        ProviderSpec(
            namespace="moonshot",
            display_name="Moonshot Kimi (International)",
            protocol=ProtocolKind.OPENAI_COMPATIBLE,
            region=Region.GLOBAL,
            api_base="https://api.moonshot.ai/v1",
            api_key_env=["MOONSHOT_API_KEY"],
            default_model="kimi-k3",
            notes="国际站；国内站请用 moonshot-cn",
        ),
        ProviderSpec(
            namespace="moonshot-cn",
            display_name="Moonshot Kimi (China)",
            protocol=ProtocolKind.OPENAI_COMPATIBLE,
            region=Region.CN,
            api_base="https://api.moonshot.cn/v1",
            api_key_env=["MOONSHOT_CN_API_KEY", "MOONSHOT_API_KEY"],
            api_base_env=["MOONSHOT_CN_BASE_URL"],
            default_model="kimi-k3",
            notes="国内站；Key 与国际站不通用",
        ),
        # --- 阿里云百炼：国际站 / 国内站 ---
        ProviderSpec(
            namespace="qwen",
            display_name="阿里云百炼 (International)",
            protocol=ProtocolKind.OPENAI_COMPATIBLE,
            region=Region.GLOBAL,
            api_base="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            api_key_env=["DASHSCOPE_API_KEY"],
            default_model="qwen-plus",
            notes="国际站；国内站请用 qwen-cn",
        ),
        ProviderSpec(
            namespace="qwen-cn",
            display_name="阿里云百炼 (China)",
            protocol=ProtocolKind.OPENAI_COMPATIBLE,
            region=Region.CN,
            api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key_env=["DASHSCOPE_CN_API_KEY", "DASHSCOPE_API_KEY"],
            api_base_env=["DASHSCOPE_CN_BASE_URL"],
            default_model="qwen-plus",
            notes="国内站；Key 与国际站不通用",
        ),
        # --- DeepSeek：全球单端点，无需区分区域 ---
        ProviderSpec(
            namespace="deepseek",
            display_name="DeepSeek",
            protocol=ProtocolKind.OPENAI_COMPATIBLE,
            api_base="https://api.deepseek.com/v1",
            api_key_env=["DEEPSEEK_API_KEY"],
            default_model="deepseek-chat",
            notes="全球单端点，不区分区域",
        ),
        # --- SiliconFlow：国际站 / 国内站 ---
        ProviderSpec(
            namespace="siliconflow",
            display_name="SiliconFlow (International)",
            protocol=ProtocolKind.OPENAI_COMPATIBLE,
            region=Region.GLOBAL,
            api_base="https://api.siliconflow.com/v1",
            api_key_env=["SILICONFLOW_API_KEY"],
            default_model="deepseek-ai/DeepSeek-V4",
            notes="国际站；国内站请用 siliconflow-cn",
        ),
        ProviderSpec(
            namespace="siliconflow-cn",
            display_name="SiliconFlow (China)",
            protocol=ProtocolKind.OPENAI_COMPATIBLE,
            region=Region.CN,
            api_base="https://api.siliconflow.cn/v1",
            api_key_env=["SILICONFLOW_CN_API_KEY", "SILICONFLOW_API_KEY"],
            api_base_env=["SILICONFLOW_CN_BASE_URL"],
            default_model="deepseek-ai/DeepSeek-V4",
            notes="国内站；Key 与国际站不通用",
        ),
        # --- 通用 OpenAI 兼容端点：兼容既有 OPENAI_BASE_URL / OPENAI_API_KEY 配置 ---
        ProviderSpec(
            namespace="openai-compatible",
            display_name="通用 OpenAI 兼容端点",
            protocol=ProtocolKind.OPENAI_COMPATIBLE,
            api_base_env=["OPENAI_BASE_URL", "OPENAI_API_BASE"],
            api_key_env=["OPENAI_API_KEY"],
            default_model=None,
            notes="端点与凭证完全由 OPENAI_BASE_URL / OPENAI_API_KEY 决定，"
            "必须显式指定 model；适用于自建网关与临时接入",
        ),
    ]
    for spec in builtin:
        register_provider(spec)


_register_builtin_providers()
