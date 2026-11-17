"""Provider 命名空间注册表。

问题背景：LiteLLM 的 model 标识是 ``<provider>/<model>`` 形式，
但它只覆盖部分厂商。对国产大模型（MiniMax、DeepSeek、通义千问、智谱等），
强行套用会退化成 ``openai/minimax-M3.1-Flash`` 这种怪异命名空间——
既不准确（实际走的不是 OpenAI），也无法表达真实的厂商归属。

本模块提供本项目自己的 provider 命名空间：

- 命名空间（如 ``minimax``、``deepseek``）表示**厂商**，与用户心智一致。
- 每个命名空间显式声明协议类型与端点：
  - ``OPENAI_COMPATIBLE``：直接用 OpenAI 兼容协议访问厂商自有端点，
    模型名就是厂商的原生模型名，不加任何前缀。
  - ``LITELLM``：交给 LiteLLM 路由（OpenAI / Anthropic / Gemini 等）。
- 凭证从环境变量读取，模型可手动覆盖。

这样调用方写 ``provider="minimax", model="minimax-M3.1-Flash"``，
拿到的是干净的厂商原生模型名，不需要 ``openai/`` 之类的前缀。
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


class ProviderSpec(BaseModel):
    """一个 provider 命名空间的注册信息。"""

    namespace: str
    display_name: str
    protocol: ProtocolKind
    api_base: str | None = None
    api_key_env: str | None = None
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
        api_base: 手动指定的端点；为 None 时用 ``spec.api_base``。
        timeout: 默认超时秒数。
        extra_headers: 附加请求头；与注册信息中的合并。
        **kwargs: 其他透传给适配器的配置。

    Returns:
        适配器构造参数。

    Raises:
        ValueError: 缺少凭证或端点时抛出。
    """
    resolved_model = spec.resolved_model(model)
    resolved_key = api_key
    if resolved_key is None and spec.api_key_env:
        resolved_key = os.environ.get(spec.api_key_env)

    if spec.protocol is ProtocolKind.OPENAI_COMPATIBLE:
        resolved_base = api_base or spec.api_base
        if not resolved_base:
            raise ValueError(f"provider {spec.namespace!r} 缺少 api_base")
        if not resolved_key:
            raise ValueError(
                f"provider {spec.namespace!r} 缺少凭证；"
                f"请设置环境变量 {spec.api_key_env or '<API_KEY>'} 或显式传入 api_key"
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
    if api_base or spec.api_base:
        config["api_base"] = api_base or spec.api_base
    config.update(kwargs)
    return config


def _register_builtin_providers() -> None:
    """注册内置的 provider 命名空间。"""
    builtin = [
        ProviderSpec(
            namespace="openai",
            display_name="OpenAI",
            protocol=ProtocolKind.LITELLM,
            api_key_env="OPENAI_API_KEY",
            default_model="gpt-4o-mini",
            litellm_prefix="openai",
        ),
        ProviderSpec(
            namespace="anthropic",
            display_name="Anthropic",
            protocol=ProtocolKind.LITELLM,
            api_key_env="ANTHROPIC_API_KEY",
            default_model="claude-sonnet-4-5",
            litellm_prefix="anthropic",
        ),
        ProviderSpec(
            namespace="gemini",
            display_name="Google Gemini",
            protocol=ProtocolKind.LITELLM,
            api_key_env="GEMINI_API_KEY",
            default_model="gemini-2.5-flash",
            litellm_prefix="gemini",
        ),
        ProviderSpec(
            namespace="minimax",
            display_name="MiniMax",
            protocol=ProtocolKind.OPENAI_COMPATIBLE,
            api_base="https://api.minimaxi.com/v1",
            api_key_env="MINIMAX_API_KEY",
            default_model="minimax-M3.1-Flash",
            notes="模型名使用 MiniMax 原生命名，不加 openai/ 前缀",
        ),
        ProviderSpec(
            namespace="deepseek",
            display_name="DeepSeek",
            protocol=ProtocolKind.OPENAI_COMPATIBLE,
            api_base="https://api.deepseek.com/v1",
            api_key_env="DEEPSEEK_API_KEY",
            default_model="deepseek-chat",
        ),
        ProviderSpec(
            namespace="qwen",
            display_name="阿里云百炼（通义千问）",
            protocol=ProtocolKind.OPENAI_COMPATIBLE,
            api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key_env="DASHSCOPE_API_KEY",
            default_model="qwen-plus",
        ),
        ProviderSpec(
            namespace="zhipu",
            display_name="智谱 GLM",
            protocol=ProtocolKind.OPENAI_COMPATIBLE,
            api_base="https://open.bigmodel.cn/api/paas/v4",
            api_key_env="ZHIPU_API_KEY",
            default_model="glm-4-plus",
        ),
        ProviderSpec(
            namespace="moonshot",
            display_name="Moonshot Kimi",
            protocol=ProtocolKind.OPENAI_COMPATIBLE,
            api_base="https://api.moonshot.cn/v1",
            api_key_env="MOONSHOT_API_KEY",
            default_model="kimi-k2-turbo-preview",
        ),
        ProviderSpec(
            namespace="siliconflow",
            display_name="SiliconFlow",
            protocol=ProtocolKind.OPENAI_COMPATIBLE,
            api_base="https://api.siliconflow.cn/v1",
            api_key_env="SILICONFLOW_API_KEY",
            default_model="Qwen/Qwen2.5-72B-Instruct",
        ),
    ]
    for spec in builtin:
        register_provider(spec)


_register_builtin_providers()
