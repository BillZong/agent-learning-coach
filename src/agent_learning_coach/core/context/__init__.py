"""上下文工程（Context Engineering）。

把「发生了什么」与「发给模型什么」分开——这是这一层存在的全部理由。

- **存储**：完整历史，永久保留在执行上下文里。
- **呈现**：一次请求实际发送的内容，受 token 预算约束。

组成：

- **Token Counter**（``tokens.TokenCounter``）：估算一次请求占多少 token。
  优先用 ``tiktoken``；拿不到分词器时回退到按字符估算，保证预算功能在
  离线环境下依然可用。
- **Context Strategy**（``strategies``）：裁剪策略。``SlidingWindow`` 处理
  条数（丢最老的），``Compaction`` 处理体积（换占位符）。两者都保证
  「每条 tool_call 都有对应的 tool_result」这条不变量。
- **Context Builder**（``builder.ContextBuilder``）：按预算把历史投影成模型
  视图，并给出裁剪统计。

依赖方向：本模块只依赖 ``core/llm`` 的厂商无关类型，不认识 ``core/agent``
——这样上下文层可以被任何上层复用，而不会形成反向依赖。
"""

from agent_learning_coach.core.context.builder import ContextBuilder, ContextView
from agent_learning_coach.core.context.strategies import (
    TRUNCATION_MARKER,
    Compaction,
    CompactionRules,
    ContextStrategy,
    SlidingWindow,
    drop_unpaired,
)
from agent_learning_coach.core.context.tokens import (
    DEFAULT_ENCODING,
    MESSAGE_OVERHEAD_TOKENS,
    TokenCounter,
    estimate_tokens,
)

__all__ = [
    "DEFAULT_ENCODING",
    "MESSAGE_OVERHEAD_TOKENS",
    "TRUNCATION_MARKER",
    "Compaction",
    "CompactionRules",
    "ContextBuilder",
    "ContextStrategy",
    "ContextView",
    "SlidingWindow",
    "TokenCounter",
    "drop_unpaired",
    "estimate_tokens",
]
