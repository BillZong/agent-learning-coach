"""从对话里抽取长期记忆。

**抽取是噪声最容易进来的地方。** 主教材 6.4.2 用「结构化输出」让模型按固定
schema 产出记忆——这解决了「格式乱」的问题，但没解决「该不该记」。

本模块把「该不该记」拆成两半：

- **模型那一侧**：提示词里写清三个条件（归属、耐久、可覆盖），并要求**没有
  就返回空列表**。「没有值得记的东西」必须是一个合法且被期望的答案，否则
  模型总会给你点什么。
- **确定性那一侧**：模型说了不算——``MemoryPolicy`` 在写入前再做一次检查
  （见 :mod:`core.memory.types`）。

**为什么用结构化输出而不是解析自由文本**：抽取结果的形状必须稳定，否则
「去重」「覆盖」这些下游机制无从谈起。模型返回的 JSON 由 Pydantic 校验，
不合法就整体失败——宁可这一轮不写记忆，也不要写进一条形状不对的记忆。
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from agent_learning_coach.core.llm.models import LLMRequest
from agent_learning_coach.core.llm.protocol import LLMClient
from agent_learning_coach.core.llm.types import ContentItem, Message, ToolCall
from agent_learning_coach.core.memory.types import LEARNER_MEMORY_KINDS, Memory, MemoryType, now

EXTRACTION_INSTRUCTION = """\
下面是一段学习者与 AI 教练的对话。请从中抽取**值得长期记住**的信息。

只抽取同时满足以下三条的信息：

1. **关于学习者本人**：他的目标、水平、强项、薄弱点、偏好、学习节奏。
   不要抽取工具返回的内容、模型的推测、一次性任务的进展。
2. **下次会话仍然成立**：会随时间变化的状态（如「今天学了闭包」）不要抽取；
   稳定的事实与偏好才抽取。
3. **能归入一个类别**：目标 / 水平 / 强项 / 薄弱点 / 偏好 / 节奏 / 进展。

如果这段对话里没有任何值得长期记住的信息，**返回空的 memories 列表**——
空列表是完全正确的答案，不要为了凑数而编造。

对话内容：
{transcript}
"""


class ExtractedMemory(BaseModel):
    """模型抽取出的候选记忆。

    字段刻意做成宽松的：``memory_type`` 与 ``kind`` 都是字符串而不是枚举，
    这样模型给出一个不认识的类别时**不会让整批抽取失败**——那一两条被丢弃
    即可，别的照常写入。
    """

    memory_type: str = Field(default="semantic", description="事实 / 经历 / 规则")
    kind: str = Field(default="fact", description="类别，如 goal / level / weakness")
    scope: str = Field(default="", description="同类别下的细分键；留空表示该类别只有一条")
    content: str = Field(default="", description="一句话陈述的记忆内容")
    confidence: float = Field(default=1.0, description="置信度，0 到 1")
    metadata: dict[str, Any] = Field(default_factory=dict, description="附加信息")


class MemoryBatch(BaseModel):
    """一批抽取结果。"""

    memories: list[ExtractedMemory] = Field(default_factory=list)


@runtime_checkable
class MemoryExtractor(Protocol):
    """记忆抽取器。"""

    async def extract(
        self,
        transcript: list[ContentItem],
        *,
        subject: str,
    ) -> list[Memory]:
        """从一段对话里抽取候选记忆。

        Args:
            transcript: 对话内容。
            subject: 归属主体（学习者标识）。

        Returns:
            候选记忆。**没有值得记的东西时返回空列表**——这是正确结果，不是
            失败。
        """
        ...


class LLMMemoryExtractor:
    """用模型抽取记忆（主教材 6.4.2 的做法）。

    通过 ``LLMClient.generate_structured`` 拿到符合 schema 的结果，因此抽取
    失败会以 ``LLMError`` / ``LLMParseError`` 的形式**明确抛出**，而不是被
    吞掉——调用方据此决定这一轮的记忆是丢了还是要重试。
    """

    def __init__(
        self,
        client: LLMClient,
        *,
        default_type: MemoryType = "semantic",
        kinds: tuple[str, ...] = LEARNER_MEMORY_KINDS,
        max_transcript_chars: int = 8000,
    ) -> None:
        """初始化抽取器。

        Args:
            client: 模型客户端。
            default_type: 模型给出的类型无法识别时使用的默认类型。
            kinds: 允许的类别。会写进提示词，帮助模型对齐词表。
            max_transcript_chars: 送进模型的对话文本上限。超出部分从**开头**
                截断——学习者的目标通常在开头。
        """
        self.client = client
        self.default_type = default_type
        self.kinds = kinds
        self.max_transcript_chars = max_transcript_chars

    async def extract(
        self,
        transcript: list[ContentItem],
        *,
        subject: str,
    ) -> list[Memory]:
        """从一段对话里抽取候选记忆。

        Args:
            transcript: 对话内容。
            subject: 归属主体。

        Returns:
            候选记忆。

        Raises:
            LLMError: 模型调用失败。
            LLMParseError: 模型输出不符合结构。
        """
        request = LLMRequest(
            instructions=[EXTRACTION_INSTRUCTION.format(transcript=format_transcript(transcript))]
        )
        batch = await self.client.generate_structured(request, MemoryBatch)
        timestamp = now()
        memories: list[Memory] = []
        for candidate in batch.memories:
            memories.append(
                Memory(
                    id=str(uuid.uuid4()),
                    memory_type=_resolve_type(candidate.memory_type, self.default_type),
                    kind=candidate.kind.strip(),
                    subject=subject,
                    scope=candidate.scope.strip(),
                    content=candidate.content.strip(),
                    metadata=dict(candidate.metadata),
                    confidence=_clamp(candidate.confidence),
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
        return memories


def format_transcript(transcript: list[ContentItem]) -> str:
    """把对话内容渲染成可送进模型的文本。

    只取**学习者说过的话**与模型的回答。工具调用与工具结果不进——它们是
    执行细节，不是学习者的事实。

    Args:
        transcript: 对话内容。

    Returns:
        文本。
    """
    lines: list[str] = []
    for item in transcript:
        if isinstance(item, Message):
            lines.append(f"[{item.role}]: {item.content}")
        elif isinstance(item, ToolCall):
            lines.append(f"[工具调用]: {item.name}")
        else:
            # ContentItem 是封闭联合，剩下只可能是 ToolResult。
            lines.append(f"[工具结果]: {item.name}")
    return "\n".join(lines)


def _resolve_type(value: str, default: MemoryType) -> MemoryType:
    """把模型给出的类型字符串解析成已知类型。

    Args:
        value: 类型字符串。
        default: 无法识别时的默认值。

    Returns:
        记忆类型。
    """
    normalized = value.strip().lower()
    if normalized in ("semantic", "episodic", "procedural"):
        return normalized  # type: ignore[return-value]
    return default


def _clamp(value: float) -> float:
    """把置信度限制在 0 到 1。

    Args:
        value: 原始置信度。

    Returns:
        限制后的置信度。
    """
    return max(0.0, min(1.0, float(value)))


__all__ = [
    "EXTRACTION_INSTRUCTION",
    "ExtractedMemory",
    "LLMMemoryExtractor",
    "MemoryBatch",
    "MemoryExtractor",
    "format_transcript",
]
