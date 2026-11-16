"""Provider-independent LLM abstraction.

本包定义 Agent Runtime 层的 Model Abstraction：通过 ``LLMClient`` Protocol 屏蔽
具体 LLM 提供商差异，使学习领域层与应用层不依赖任何厂商 SDK。

导入约定：调用方统一从 ``agent_learning_coach.core.llm`` 导入公开符号。
"""

from agent_learning_coach.core.llm.errors import LLMError, LLMParseError
from agent_learning_coach.core.llm.mock import FailingLLMClient, MockLLMClient
from agent_learning_coach.core.llm.models import (
    LLMRequest,
    LLMResponse,
    LLMUsage,
    ToolSpec,
)
from agent_learning_coach.core.llm.protocol import LLMClient
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
    "LLMUsage",
    "Message",
    "MockLLMClient",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
]
