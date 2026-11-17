"""Provider-independent LLM abstraction.

本包定义 Agent Runtime 层的 Model Abstraction：通过 ``LLMClient`` Protocol 屏蔽
具体 LLM 提供商差异，使学习领域层与应用层不依赖任何厂商 SDK。

Provider 选择使用**命名空间**（厂商名，如 ``minimax``、``deepseek``、``anthropic``），
模型名保持厂商原生形式，不需要 ``openai/`` 之类的前缀。

导入约定：调用方统一从 ``agent_learning_coach.core.llm`` 导入公开符号。
"""

from agent_learning_coach.core.llm.errors import LLMError, LLMParseError, LLMTimeoutError
from agent_learning_coach.core.llm.litellm_client import LiteLLMClient
from agent_learning_coach.core.llm.mock import FailingLLMClient, MockLLMClient
from agent_learning_coach.core.llm.models import (
    LLMRequest,
    LLMResponse,
    LLMUsage,
    ToolSpec,
)
from agent_learning_coach.core.llm.openai_compatible_client import OpenAICompatibleClient
from agent_learning_coach.core.llm.protocol import LLMClient
from agent_learning_coach.core.llm.providers import (
    create_client,
    describe_providers,
    resolve_provider,
)
from agent_learning_coach.core.llm.registry import (
    ProtocolKind,
    ProviderSpec,
    Region,
    get_provider,
    list_providers,
    provider_namespaces,
    register_provider,
    unregister_provider,
)
from agent_learning_coach.core.llm.types import (
    ContentItem,
    Message,
    ToolCall,
    ToolResult,
)

__all__ = [
    "ContentItem",
    "FailingLLMClient",
    "LLMClient",
    "LLMError",
    "LLMParseError",
    "LLMRequest",
    "LLMResponse",
    "LLMTimeoutError",
    "LLMUsage",
    "LiteLLMClient",
    "Message",
    "MockLLMClient",
    "OpenAICompatibleClient",
    "ProtocolKind",
    "ProviderSpec",
    "Region",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
    "create_client",
    "describe_providers",
    "get_provider",
    "list_providers",
    "provider_namespaces",
    "register_provider",
    "resolve_provider",
    "unregister_provider",
]
