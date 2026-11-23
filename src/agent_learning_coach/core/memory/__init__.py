"""长期记忆（Long-term Memory）。

跨会话保留**关于学习者的事实**。

- **Memory**（``types.Memory``）：一条记忆。带类型（事实 / 经历 / 规则）、
  类别、归属主体与作用域。唯一键是 ``(主体, 类型, 类别, 作用域)``——写入时
  按键 upsert，因此新事实**取代**旧事实而不是并存。
- **Memory Policy**（``types.MemoryPolicy``）：准入策略。确定性地拒绝空内容、
  低置信度、超长与未知类别的候选。
- **Memory Store**（``store``）：存储协议，以及内存与 JSON 文件两种实现。
  召回是确定性的关键词相关度，不需要嵌入服务。
- **Memory Extractor**（``extraction``）：抽取器协议与基于结构化输出的实现。
- **Memory Manager**（``manager``）：把抽取、准入、去重、覆盖、召回串起来。
- **Learner Profile**（``types.LearnerProfile``）：记忆的一种视图——把散落的
  记忆聚合成一份可读的画像。

依赖方向：本模块只依赖 ``core/llm`` 的厂商无关类型，不认识 ``core/agent``
与 ``core/context``。

导入约定：调用方统一从 ``agent_learning_coach.core.memory`` 导入公开符号。
"""

from agent_learning_coach.core.memory.extraction import (
    EXTRACTION_INSTRUCTION,
    ExtractedMemory,
    LLMMemoryExtractor,
    MemoryBatch,
    MemoryExtractor,
    format_transcript,
)
from agent_learning_coach.core.memory.manager import MemoryManager
from agent_learning_coach.core.memory.store import (
    InMemoryMemoryStore,
    JsonFileMemoryStore,
    MemoryStore,
    tokenize,
)
from agent_learning_coach.core.memory.types import (
    LEARNER_MEMORY_KINDS,
    LearnerProfile,
    Memory,
    MemoryPolicy,
    MemoryType,
    format_memories,
    now,
)

__all__ = [
    "EXTRACTION_INSTRUCTION",
    "LEARNER_MEMORY_KINDS",
    "ExtractedMemory",
    "InMemoryMemoryStore",
    "JsonFileMemoryStore",
    "LLMMemoryExtractor",
    "LearnerProfile",
    "Memory",
    "MemoryBatch",
    "MemoryExtractor",
    "MemoryManager",
    "MemoryPolicy",
    "MemoryStore",
    "MemoryType",
    "format_memories",
    "format_transcript",
    "now",
    "tokenize",
]
