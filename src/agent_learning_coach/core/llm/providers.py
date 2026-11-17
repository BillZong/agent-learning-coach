"""Provider 解析与客户端工厂。

LiteLLM 通过模型标识（model string）决定路由到哪个厂商，
例如 ``gpt-4o-mini``、``anthropic/claude-sonnet-4-5``、``gemini/gemini-2.5-flash``。
本模块把这一隐式规则显式化：

1. ``resolve_provider`` 在客户端创建时解析厂商，模型标识写错立刻失败，
   而不是等到第一次真实调用才在网络层报错。
2. ``create_client`` 是按模型标识创建 ``LLMClient`` 的唯一入口，
   上层不需要知道适配器的具体类型。
"""

from __future__ import annotations

from typing import Any

from agent_learning_coach.core.llm.errors import LLMError
from agent_learning_coach.core.llm.protocol import LLMClient


def resolve_provider(model: str) -> str:
    """解析模型标识对应的厂商名。

    Args:
        model: LiteLLM 模型标识。

    Returns:
        厂商名，例如 ``openai``、``anthropic``、``gemini``。

    Raises:
        LLMError: 模型标识无法识别时抛出。
    """
    try:
        from litellm import get_llm_provider
    except ImportError as exc:  # pragma: no cover - 依赖缺失时的显式错误
        raise LLMError("未安装 litellm，无法解析模型所属厂商") from exc

    try:
        _, provider, _, _ = get_llm_provider(model=model)
    except Exception as exc:
        raise LLMError(f"无法识别模型标识 {model!r}: {exc}") from exc

    return provider


def create_client(model: str, *, timeout: float | None = None, **config: Any) -> LLMClient:
    """按模型标识创建 ``LLMClient``。

    Args:
        model: LiteLLM 模型标识，如 ``gpt-4o-mini``、``anthropic/claude-sonnet-4-5``。
        timeout: 默认超时秒数，作用于该客户端的所有请求。
        **config: 透传给适配器的其他配置，如 ``api_base``、``api_key``。

    Returns:
        满足 ``LLMClient`` Protocol 的客户端实例。

    Raises:
        LLMError: 模型标识无法识别时抛出（早失败，不等到首次调用）。
    """
    from agent_learning_coach.core.llm.litellm_client import LiteLLMClient

    resolve_provider(model)
    return LiteLLMClient(model=model, timeout=timeout, **config)
