"""长期记忆的存储。

存储有两个职责：**按唯一键 upsert**（新事实取代旧事实）与**召回**（按条件
筛选，或按相关度排序）。

**为什么召回是确定性的关键词匹配而不是向量检索？**

学习者的长期记忆是**结构化**的：几十到几百条事实，每条带类别（目标、水平、
弱点……）与主体。绝大多数召回是「给我这个学习者的弱点」——那是**筛选**，
不是相似度检索。剩下的「和当前话题相关的记忆」才是自由文本检索。

对这种规模的小语料，确定性的关键词匹配有三重好处：**不需要嵌入服务**
（参考实现因此绑定了 OpenAI 的 embedding API，与本项目厂商无关的立场冲突）、
**完全离线可测**、**结果可解释**（能说清为什么这条被召回）。

向量检索留给 Day 11～12 的 RAG——那时嵌入与向量索引的基础设施会建起来，
长期记忆可以接上去。本包的 ``MemoryStore`` 是协议，接上向量实现不影响调用方。
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from agent_learning_coach.core.memory.types import Memory

_ASCII_TOKEN = re.compile(r"[a-z0-9_]+")
_CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]+")


@runtime_checkable
class MemoryStore(Protocol):
    """长期记忆存储。

    实现必须保证 ``add`` 是**按唯一键 upsert**：同一
    ``(主体, 类型, 类别, 作用域)`` 的第二条记忆取代第一条，而不是并存。
    """

    async def add(self, memory: Memory) -> Memory:
        """写入一条记忆（按唯一键 upsert）。

        Args:
            memory: 待写入的记忆。

        Returns:
            实际存储的记忆。更新已有记忆时保留原 id 与创建时刻。
        """
        ...

    async def get(self, memory_id: str) -> Memory | None:
        """按 id 读取。

        Args:
            memory_id: 记忆标识。

        Returns:
            记忆；不存在时返回 None。
        """
        ...

    async def find(
        self,
        *,
        subject: str | None = None,
        memory_type: str | None = None,
        kind: str | None = None,
    ) -> list[Memory]:
        """按条件筛选记忆。

        Args:
            subject: 主体。为 None 表示不限。
            memory_type: 类型。为 None 表示不限。
            kind: 类别。为 None 表示不限。

        Returns:
            匹配的记忆，按最近更新时刻倒序。
        """
        ...

    async def search(
        self,
        query: str,
        *,
        subject: str | None = None,
        kind: str | None = None,
        limit: int = 5,
    ) -> list[Memory]:
        """按查询召回相关记忆。

        Args:
            query: 查询文本。
            subject: 主体。为 None 表示不限。
            kind: 类别。为 None 表示不限。
            limit: 最多返回几条。

        Returns:
            相关度排序的记忆。没有匹配时返回空列表。
        """
        ...

    async def delete(self, memory_id: str) -> None:
        """删除一条记忆。

        Args:
            memory_id: 记忆标识。
        """
        ...


class InMemoryMemoryStore:
    """内存记忆存储。

    开发与测试用。生产用可换自己的实现（数据库、向量库……）——``MemoryStore``
    是协议。
    """

    def __init__(self) -> None:
        """初始化空存储。"""
        self._memories: dict[str, Memory] = {}
        self._by_key: dict[tuple[str, str, str, str], str] = {}

    async def add(self, memory: Memory) -> Memory:
        """写入一条记忆（按唯一键 upsert）。

        已有同键的记忆时：**保留原 id 与创建时刻**，更新内容与更新时刻。
        这样「这条记忆被更新过」而不是「多了一条新记忆」——后者会让同一件事
        在画像里出现好几遍。

        Args:
            memory: 待写入的记忆。

        Returns:
            实际存储的记忆。
        """
        existing_id = self._by_key.get(memory.key)
        if existing_id is not None:
            existing = self._memories[existing_id]
            stored = Memory(
                id=existing.id,
                memory_type=memory.memory_type,
                kind=memory.kind,
                subject=memory.subject,
                scope=memory.scope,
                content=memory.content,
                metadata={**existing.metadata, **memory.metadata},
                confidence=memory.confidence,
                created_at=existing.created_at,
                updated_at=memory.updated_at or existing.updated_at,
            )
        else:
            stored = memory
        # id 复用但键不同时，把旧的键映射清掉——否则会留下一条指向错误记忆
        # 的陈旧映射，按键查找时把别的记忆捞出来。
        for stale_key, stale_id in list(self._by_key.items()):
            if stale_id == stored.id and stale_key != stored.key:
                del self._by_key[stale_key]
        self._memories[stored.id] = stored
        self._by_key[stored.key] = stored.id
        return stored

    async def get(self, memory_id: str) -> Memory | None:
        """按 id 读取。

        Args:
            memory_id: 记忆标识。

        Returns:
            记忆；不存在时返回 None。
        """
        return self._memories.get(memory_id)

    async def find(
        self,
        *,
        subject: str | None = None,
        memory_type: str | None = None,
        kind: str | None = None,
    ) -> list[Memory]:
        """按条件筛选记忆。

        Args:
            subject: 主体。为 None 表示不限。
            memory_type: 类型。为 None 表示不限。
            kind: 类别。为 None 表示不限。

        Returns:
            匹配的记忆，按最近更新时刻倒序。
        """
        matched = [
            memory
            for memory in self._memories.values()
            if (subject is None or memory.subject == subject)
            and (memory_type is None or memory.memory_type == memory_type)
            and (kind is None or memory.kind == kind)
        ]
        return sorted(matched, key=lambda item: item.updated_at, reverse=True)

    async def search(
        self,
        query: str,
        *,
        subject: str | None = None,
        kind: str | None = None,
        limit: int = 5,
    ) -> list[Memory]:
        """按查询召回相关记忆。

        相关度 = **查询词被覆盖的比例**（查询里的词有多少出现在记忆内容中）。
        这个定义让「查得越具体、命中越准」，且不需要任何模型或服务。

        Args:
            query: 查询文本。
            subject: 主体。为 None 表示不限。
            kind: 类别。为 None 表示不限。
            limit: 最多返回几条。

        Returns:
            相关度排序的记忆。没有匹配时返回空列表。
        """
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        candidates = await self.find(subject=subject, kind=kind)
        scored: list[tuple[float, float, Memory]] = []
        for memory in candidates:
            overlap = len(query_tokens & tokenize(memory.content))
            if not overlap:
                continue
            score = overlap / len(query_tokens)
            scored.append((score, memory.updated_at, memory))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [memory for _, _, memory in scored[:limit]]

    async def delete(self, memory_id: str) -> None:
        """删除一条记忆。

        Args:
            memory_id: 记忆标识。
        """
        memory = self._memories.pop(memory_id, None)
        if memory is not None:
            self._by_key.pop(memory.key, None)


def tokenize(text: str) -> set[str]:
    """把文本切成用于匹配的小块。

    英文与数字按词切；**中文按二元组切**（「闭包」切成「闭」「包」）。中文
    没有词边界，按空格切会得到整句话；按二元组切既能命中子串，又不至于把
    单字当成噪声。

    Args:
        text: 待切分的文本。

    Returns:
        小块集合。大小写不敏感。
    """
    lowered = text.lower()
    tokens = set(_ASCII_TOKEN.findall(lowered))
    for run in _CJK.findall(lowered):
        if len(run) == 1:
            tokens.add(run)
            continue
        for index in range(len(run) - 1):
            tokens.add(run[index : index + 2])
    return tokens


__all__ = ["InMemoryMemoryStore", "MemoryStore", "tokenize"]
