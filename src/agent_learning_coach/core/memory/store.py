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

**文件布局**：一个主体一个文件（``<subject>.json``）。主体是访问的主要维度
（「这个学习者的记忆」），一个文件让读取与写入都只碰一个文件；代价是按 id
读取或删除需要扫描目录——主体数量不多时可以接受。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Protocol, runtime_checkable

from agent_learning_coach.core.memory.types import Memory
from agent_learning_coach.core.text import tokenize


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


def _upsert(
    memories: list[Memory],
    memory: Memory,
) -> tuple[list[Memory], Memory]:
    """在记忆列表里按键 upsert 一条记忆。

    已有同键的记忆时：**保留原 id 与创建时刻**，更新内容与更新时刻。
    这样「这条记忆被更新过」而不是「多了一条新记忆」——后者会让同一件事
    在画像里出现好几遍。

    Args:
        memories: 现有记忆列表（会被复制，不原地修改）。
        memory: 待写入的记忆。

    Returns:
            新的记忆列表与实际存储的记忆。
    """
    existing = next((item for item in memories if item.key == memory.key), None)
    if existing is None:
        return [*memories, memory], memory
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
    updated = [stored if item.key == stored.key else item for item in memories]
    return updated, stored


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

        Args:
            memory: 待写入的记忆。

        Returns:
            实际存储的记忆。
        """
        updated, stored = _upsert(list(self._memories.values()), memory)
        # 索引整体重建，而不是增量维护——增量维护在「id 复用但键不同时」会留下
        # 一条指向错误记忆的陈旧映射，按键查找就会捞出别的内容。重建从根上避免
        # 这类不一致，代价只是 O(n)，而这里的 n 是几十到几百。
        self._memories = {item.id: item for item in updated}
        self._by_key = {item.key: item.id for item in updated}
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
        return _rank(candidates, query_tokens, limit=limit)

    async def delete(self, memory_id: str) -> None:
        """删除一条记忆。

        Args:
            memory_id: 记忆标识。
        """
        memory = self._memories.pop(memory_id, None)
        if memory is not None:
            self._by_key.pop(memory.key, None)


class JsonFileMemoryStore:
    """文件记忆存储：一个主体一个 JSON 文件。

    **写入是原子的**：先写临时文件再改名——改名是原子的，因此不会出现
    「写到一半的记忆集合」被读出来。

    文件 I/O 放到线程里执行，避免阻塞事件循环。
    """

    def __init__(self, directory: str | Path) -> None:
        """初始化存储。

        Args:
            directory: 存放记忆文件的目录。不存在时自动创建。
        """
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, subject: str) -> Path:
        """主体对应的文件路径。

        Args:
            subject: 归属主体。

        Returns:
            路径。
        """
        safe = "".join(char if char.isalnum() or char in "-_." else "_" for char in subject)
        return self.directory / f"{safe}.json"

    def _subjects(self) -> list[str]:
        """列出全部主体的文件名（去扩展名）。

        Returns:
            主体名列表。
        """
        return [path.stem for path in self.directory.glob("*.json")]

    async def _load(self, subject: str) -> list[Memory]:
        """读取一个主体的全部记忆。

        Args:
            subject: 归属主体。

        Returns:
            记忆列表。文件不存在时返回空列表。
        """
        path = self._path(subject)
        if not path.exists():
            return []
        raw = await asyncio.to_thread(path.read_text, encoding="utf-8")
        payload = json.loads(raw)
        return [Memory(**item) for item in payload.get("memories", [])]

    async def _save(self, subject: str, memories: list[Memory]) -> None:
        """写回一个主体的全部记忆。

        Args:
            subject: 归属主体。
            memories: 记忆列表。
        """
        path = self._path(subject)
        payload = json.dumps(
            {"subject": subject, "memories": [item.to_dict() for item in memories]},
            ensure_ascii=False,
            indent=2,
        )
        temp = path.with_suffix(".json.tmp")
        await asyncio.to_thread(temp.write_text, payload, encoding="utf-8")
        await asyncio.to_thread(temp.replace, path)

    async def add(self, memory: Memory) -> Memory:
        """写入一条记忆（按唯一键 upsert）。

        Args:
            memory: 待写入的记忆。

        Returns:
            实际存储的记忆。
        """
        memories = await self._load(memory.subject)
        updated, stored = _upsert(memories, memory)
        await self._save(memory.subject, updated)
        return stored

    async def get(self, memory_id: str) -> Memory | None:
        """按 id 读取。

        主体是访问的主要维度，但这里按 id 找——因此需要扫描各主体的文件。
        主体数量不多时可以接受。

        Args:
            memory_id: 记忆标识。

        Returns:
            记忆；不存在时返回 None。
        """
        for subject in self._subjects():
            for memory in await self._load(subject):
                if memory.id == memory_id:
                    return memory
        return None

    async def find(
        self,
        *,
        subject: str | None = None,
        memory_type: str | None = None,
        kind: str | None = None,
    ) -> list[Memory]:
        """按条件筛选记忆。

        Args:
            subject: 主体。为 None 表示扫描全部主体。
            memory_type: 类型。为 None 表示不限。
            kind: 类别。为 None 表示不限。

        Returns:
            匹配的记忆，按最近更新时刻倒序。
        """
        subjects = [subject] if subject is not None else self._subjects()
        matched: list[Memory] = []
        for name in subjects:
            for memory in await self._load(name):
                if memory_type is not None and memory.memory_type != memory_type:
                    continue
                if kind is not None and memory.kind != kind:
                    continue
                matched.append(memory)
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

        Args:
            query: 查询文本。
            subject: 主体。为 None 表示扫描全部主体。
            kind: 类别。为 None 表示不限。
            limit: 最多返回几条。

        Returns:
            相关度排序的记忆。没有匹配时返回空列表。
        """
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        candidates = await self.find(subject=subject, kind=kind)
        return _rank(candidates, query_tokens, limit=limit)

    async def delete(self, memory_id: str) -> None:
        """删除一条记忆。

        Args:
            memory_id: 记忆标识。
        """
        for subject in self._subjects():
            memories = await self._load(subject)
            remaining = [item for item in memories if item.id != memory_id]
            if len(remaining) != len(memories):
                await self._save(subject, remaining)
                return


def _rank(
    candidates: list[Memory],
    query_tokens: set[str],
    *,
    limit: int,
) -> list[Memory]:
    """按相关度给候选记忆排序。

    相关度 = **查询词被覆盖的比例**。同分时最近更新的排前面。

    Args:
        candidates: 候选记忆。
        query_tokens: 查询词。
        limit: 最多返回几条。

    Returns:
        排序后的记忆。没有匹配时返回空列表。
    """
    scored: list[tuple[float, float, Memory]] = []
    for memory in candidates:
        overlap = len(query_tokens & tokenize(memory.content))
        if not overlap:
            continue
        score = overlap / len(query_tokens)
        scored.append((score, memory.updated_at, memory))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [memory for _, _, memory in scored[:limit]]


__all__ = ["InMemoryMemoryStore", "JsonFileMemoryStore", "MemoryStore", "tokenize"]
