"""长期记忆的管理：抽取、准入、去重、覆盖、召回。

**「如何避免把噪声写进 Memory Extraction?」** 本模块给出答案，分三层：

1. **抽取层**（:mod:`core.memory.extraction`）：提示词要求「没有就返回空
   列表」，把「不值得记」变成一个被期望的答案。
2. **准入层**（``MemoryPolicy``）：确定性地检查内容、置信度、长度、类别。
   模型说了不算。
3. **写入层**（本模块）：同键 upsert——新事实**取代**旧事实，而不是并存；
   同类别同内容的重复直接丢弃。

第 3 层是抑制矛盾记忆的关键。「学习者水平从初级变成中级」如果表现为「多了一条
记忆」，画像里就会同时出现两个互相矛盾的水平；表现为「一条记忆被更新」则不会。

**召回**分两种：按条件筛选（结构化召回，绝大多数情况）与按查询相关度排序
（自由文本召回）。两者都确定性、无外部依赖。

**记忆写入不在 Agent 的热路径上**。LangGraph 文档指出在热路径里写记忆会
增加延迟、让 Agent 分心；本项目同样把这件事交给应用层决定何时调用——
``MemoryManager`` 只提供能力，不决定时机。
"""

from __future__ import annotations

from agent_learning_coach.core.llm.types import ContentItem
from agent_learning_coach.core.memory.extraction import MemoryExtractor
from agent_learning_coach.core.memory.store import MemoryStore
from agent_learning_coach.core.memory.types import (
    LearnerProfile,
    Memory,
    MemoryPolicy,
    format_memories,
)


class MemoryManager:
    """长期记忆的管理器。

    典型用法::

        manager = MemoryManager(store, LLMMemoryExtractor(client))
        await manager.capture(transcript, subject=learner_id)
        memories = await manager.recall("闭包", subject=learner_id)
    """

    def __init__(
        self,
        store: MemoryStore,
        extractor: MemoryExtractor,
        *,
        policy: MemoryPolicy | None = None,
    ) -> None:
        """初始化管理器。

        Args:
            store: 记忆存储。
            extractor: 抽取器。
            policy: 准入策略。为 None 时用默认策略。
        """
        self.store = store
        self.extractor = extractor
        self.policy = policy if policy is not None else MemoryPolicy()

    async def capture(
        self,
        transcript: list[ContentItem],
        *,
        subject: str,
    ) -> list[Memory]:
        """从对话里抽取并写入长期记忆。

        流程：抽取 → 准入过滤 → 同内容去重 → 按唯一键 upsert。

        Args:
            transcript: 对话内容。
            subject: 归属主体（学习者标识）。

        Returns:
            实际写入的记忆。被拒绝或被去重的不会出现在结果里。

        Raises:
            LLMError: 抽取失败。调用方决定这一轮是丢了还是重试——本方法不
                吞异常，因为「抽取失败」是需要被看见的事实。
        """
        candidates = await self.extractor.extract(transcript, subject=subject)
        written: list[Memory] = []
        for candidate in candidates:
            if not self.policy.admits(candidate):
                continue
            if await self._is_duplicate(candidate):
                continue
            written.append(await self.store.add(candidate))
        return written

    async def recall(
        self,
        query: str = "",
        *,
        subject: str,
        kind: str | None = None,
        limit: int = 5,
    ) -> list[Memory]:
        """召回与学习者相关的记忆。

        Args:
            query: 查询文本。为空时按条件筛选，不做相关度排序。
            subject: 归属主体。
            kind: 类别。为 None 表示不限。
            limit: 最多返回几条。

        Returns:
            记忆。查询为空时按最近更新时刻倒序；有查询时按相关度排序。
        """
        if not query.strip():
            return await self.store.find(subject=subject, kind=kind)
        return await self.store.search(query, subject=subject, kind=kind, limit=limit)

    async def build_context(
        self,
        query: str = "",
        *,
        subject: str,
        limit: int = 8,
    ) -> str:
        """构造可以放进提示词的「记忆上下文」。

        两种形态：

        - **不带查询**：给学习者画像（目标、水平、强项、弱点、偏好……）——
          每次对话都该带着的那部分。
        - **带查询**：给与当前话题相关的记忆——只在相关时注入。

        两种形态都不需要嵌入服务，且**没有内容时返回空字符串**——不给模型塞
        一段空话。

        Args:
            query: 查询文本。为空时给画像。
            subject: 归属主体。
            limit: 带查询时最多取几条。

        Returns:
            提示词文本。没有可注入的记忆时返回空字符串。
        """
        if not query.strip():
            return (await self.profile(subject=subject)).to_prompt()
        memories = await self.recall(query, subject=subject, limit=limit)
        return format_memories(memories)

    async def profile(self, *, subject: str) -> LearnerProfile:
        """生成学习者画像。

        Args:
            subject: 归属主体。

        Returns:
            画像。没有记忆时返回空画像。
        """
        memories = await self.store.find(subject=subject)
        return LearnerProfile.from_memories(memories)

    async def _is_duplicate(self, candidate: Memory) -> bool:
        """判断候选是否与已有记忆重复。

        「重复」指**同一主体、同一类型、同一类别、内容相同**——不管作用域
        是否一致。作用域不同但内容一样的事实，重复记一条就是噪声。

        Args:
            candidate: 候选记忆。

        Returns:
            重复返回 True。
        """
        existing = await self.store.find(
            subject=candidate.subject,
            memory_type=candidate.memory_type,
            kind=candidate.kind,
        )
        normalized = candidate.content.strip()
        return any(item.content.strip() == normalized for item in existing)


__all__ = ["MemoryManager"]
