"""Provider 解析与客户端工厂。

调用方通过**命名空间**（厂商名 + 区域）选择 provider，通过 **model** 手动指定
或切换模型，不再需要 ``openai/minimax-M3.1-Flash`` 这种把厂商名塞进模型名的
怪异前缀写法。

区域约定（与社区 agent 工具一致）：

- ``<vendor>``：国际 / 全球站点
- ``<vendor>-cn``：中国大陆站点

国内与国际站点通常签发不同的 Key，因此凭证环境变量也分开：
``MINIMAX_API_KEY``（国际）与 ``MINIMAX_CN_API_KEY``（国内）。

典型用法::

    client = create_client("minimax")                        # 用注册表默认模型
    client = create_client("minimax", model="MiniMax-M3")     # 手动指定模型
    client = create_client("minimax-cn", timeout=30.0)        # 国内站 + 超时
"""

from __future__ import annotations

from typing import Any

from agent_learning_coach.core.llm.errors import LLMError
from agent_learning_coach.core.llm.protocol import LLMClient
from agent_learning_coach.core.llm.registry import (
    ProtocolKind,
    ProviderSpec,
    Region,
    build_client_config,
    get_provider,
    list_providers,
)


def resolve_provider(namespace: str) -> ProviderSpec:
    """按命名空间取 provider 注册信息。

    Args:
        namespace: provider 命名空间，如 ``minimax``、``minimax-cn``。

    Returns:
        provider 注册信息。

    Raises:
        LLMError: 命名空间未注册时抛出。
    """
    try:
        return get_provider(namespace)
    except KeyError as exc:
        raise LLMError(str(exc)) from exc


def create_client(
    provider: str,
    *,
    model: str | None = None,
    api_key: str | None = None,
    api_base: str | None = None,
    timeout: float | None = None,
    **kwargs: Any,
) -> LLMClient:
    """按 provider 命名空间创建 ``LLMClient``。

    Args:
        provider: provider 命名空间，如 ``minimax``（国际）、``minimax-cn``（国内）。
        model: 手动指定的模型；为 None 时用注册表中的默认模型。
        api_key: 手动指定凭证；为 None 时从命名空间声明的环境变量读取。
        api_base: 手动指定端点；为 None 时用命名空间声明的环境变量或注册表默认值。
        timeout: 默认超时秒数，请求级 ``LLMRequest.timeout`` 优先。
        **kwargs: 其他透传给适配器的配置。

    Returns:
        满足 ``LLMClient`` Protocol 的客户端实例。

    Raises:
        LLMError: 命名空间未注册、凭证缺失或端点缺失时抛出（早失败，
            不等到首次真实调用才在网络层报错）。
    """
    spec = resolve_provider(provider)

    try:
        config = build_client_config(
            spec,
            model=model,
            api_key=api_key,
            api_base=api_base,
            timeout=timeout,
            **kwargs,
        )
    except ValueError as exc:
        raise LLMError(str(exc)) from exc

    if spec.protocol is ProtocolKind.LITELLM:
        from agent_learning_coach.core.llm.litellm_client import LiteLLMClient

        prefix = spec.litellm_prefix
        return LiteLLMClient(model=config.pop("model"), model_prefix=prefix, **config)

    from agent_learning_coach.core.llm.openai_compatible_client import OpenAICompatibleClient

    headers = config.pop("extra_headers", None) or {}
    return OpenAICompatibleClient(
        model=config.pop("model"),
        api_base=config.pop("api_base"),
        api_key=config.pop("api_key"),
        timeout=config.pop("timeout", None),
        extra_headers=headers,
        **config,
    )


def describe_providers() -> list[dict[str, Any]]:
    """列出全部已注册 provider 的关键信息，便于排查配置问题。

    Returns:
        每项包含 namespace / display_name / protocol / region / api_base /
        default_model / api_key_env / api_base_env。
    """
    return [
        {
            "namespace": spec.namespace,
            "display_name": spec.display_name,
            "protocol": spec.protocol.value,
            "region": spec.region.value,
            "api_base": spec.api_base,
            "default_model": spec.default_model,
            "api_key_env": spec.api_key_env,
            "api_base_env": spec.api_base_env,
        }
        for spec in sorted(list_providers(), key=lambda s: (s.display_name, s.namespace))
    ]


__all__ = ["Region", "create_client", "describe_providers", "resolve_provider"]
