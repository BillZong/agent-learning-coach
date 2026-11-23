"""向量存储。

两种实现，同一个协议：

- ``InMemoryVectorStore``：进程内，numpy 算余弦相似度。**测试与演示用**——
  不需要任何外部服务，检索结果完全确定。
- ``PgVectorStore``：**生产路径**。PostgreSQL + pgvector，向量列用 ``vector``
  类型，索引用 HNSW，检索用 ``<=>``（余弦距离）算子。

**检索为什么按「距离」而不是「相似度」排序**：pgvector 的 ``<=>`` 返回的是
**距离**（越小越近），而余弦相似度越大越近。两者方向相反，因此本包统一对外
暴露**相似度**（越大越相关），pgvector 实现负责把距离换算回来。

**过滤在检索阶段做，不在应用层做**。元数据过滤下推到数据库，才能用上索引；
在应用层过滤等于把全部向量拉回内存再筛——那是「索引 ≠ 检索」最常见的误用。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import numpy as np

from agent_learning_coach.core.rag.types import Chunk, ScoredChunk


@runtime_checkable
class VectorStore(Protocol):
    """向量存储。"""

    async def add(
        self,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        """写入切片与向量。

        Args:
            chunks: 切片。
            embeddings: 与切片一一对应的向量。

        Raises:
            ValueError: 数量不匹配，或向量维度不符。
        """
        ...

    async def search(
        self,
        embedding: Sequence[float],
        *,
        top_k: int = 5,
        filters: Mapping[str, Any] | None = None,
    ) -> list[ScoredChunk]:
        """检索最相关的切片。

        Args:
            embedding: 查询向量。
            top_k: 最多返回几条。
            filters: 元数据等值过滤。全部条件都要满足。

        Returns:
            按相关度从高到低排列的切片。
        """
        ...

    async def delete_document(self, document_id: str) -> int:
        """删除一份文档的全部切片。

        Args:
            document_id: 文档标识。

        Returns:
            删除的条数。
        """
        ...

    async def count(self) -> int:
        """当前存储的切片总数。

        Returns:
            条数。
        """
        ...


def _validate(chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> None:
    """校验切片与向量是否配套。

    Args:
        chunks: 切片。
        embeddings: 向量。

    Raises:
        ValueError: 数量不匹配，或向量长度不一致。
    """
    if len(chunks) != len(embeddings):
        raise ValueError(f"切片与向量数量不匹配：{len(chunks)} vs {len(embeddings)}")
    widths = {len(vector) for vector in embeddings}
    if len(widths) > 1:
        raise ValueError(f"向量维度不一致：{sorted(widths)}")


def _normalize(vector: Sequence[float]) -> np.ndarray:
    """把向量转成 numpy 数组并做 L2 归一化。

    归一化后点积即余弦相似度，检索时不必反复除范数。

    Args:
        vector: 原始向量。

    Returns:
        归一化后的 numpy 数组。
    """
    array = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(array))
    if norm == 0.0:
        return array
    return array / norm


class InMemoryVectorStore:
    """进程内的向量存储。

    测试与演示用：不需要外部服务，检索结果完全确定。
    """

    def __init__(self, *, dimension: int | None = None) -> None:
        """初始化存储。

        Args:
            dimension: 向量维度。为 None 时由第一次写入决定。
        """
        self._dimension = dimension
        self._chunks: dict[str, Chunk] = {}
        self._vectors: dict[str, np.ndarray] = {}
        self._order: list[str] = []

    @property
    def dimension(self) -> int | None:
        """向量维度。尚未写入时为 None。"""
        return self._dimension

    async def add(
        self,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        """写入切片与向量。

        Args:
            chunks: 切片。
            embeddings: 与切片一一对应的向量。

        Raises:
            ValueError: 数量不匹配，或向量维度与已有数据不符。
        """
        _validate(chunks, embeddings)
        for chunk, vector in zip(chunks, embeddings, strict=True):
            if self._dimension is None:
                self._dimension = len(vector)
            elif len(vector) != self._dimension:
                raise ValueError(f"向量维度不一致：存储为 {self._dimension}，收到 {len(vector)}")
            if chunk.id not in self._chunks:
                self._order.append(chunk.id)
            self._chunks[chunk.id] = chunk
            self._vectors[chunk.id] = _normalize(vector)

    async def search(
        self,
        embedding: Sequence[float],
        *,
        top_k: int = 5,
        filters: Mapping[str, Any] | None = None,
    ) -> list[ScoredChunk]:
        """检索最相关的切片。

        Args:
            embedding: 查询向量。
            top_k: 最多返回几条。
            filters: 元数据等值过滤。

        Returns:
            按相关度从高到低排列的切片。
        """
        if not self._chunks:
            return []
        query = _normalize(embedding)
        candidates = [
            (chunk_id, self._vectors[chunk_id])
            for chunk_id in self._order
            if chunk_id in self._vectors and _matches(self._chunks[chunk_id].metadata, filters)
        ]
        if not candidates:
            return []
        matrix = np.stack([vector for _, vector in candidates])
        scores = matrix @ query
        ranked = sorted(
            zip(candidates, scores.tolist(), strict=True),
            key=lambda item: item[1],
            reverse=True,
        )
        return [
            ScoredChunk(chunk=self._chunks[chunk_id], score=float(score))
            for (chunk_id, _), score in ranked[:top_k]
        ]

    async def delete_document(self, document_id: str) -> int:
        """删除一份文档的全部切片。

        Args:
            document_id: 文档标识。

        Returns:
            删除的条数。
        """
        removed = [
            chunk_id
            for chunk_id in self._order
            if self._chunks[chunk_id].document_id == document_id
        ]
        for chunk_id in removed:
            self._order.remove(chunk_id)
            del self._chunks[chunk_id]
            self._vectors.pop(chunk_id, None)
        return len(removed)

    async def count(self) -> int:
        """当前存储的切片总数。

        Returns:
            条数。
        """
        return len(self._chunks)


def _matches(metadata: Mapping[str, Any], filters: Mapping[str, Any] | None) -> bool:
    """判断元数据是否满足全部过滤条件。

    Args:
        metadata: 切片的元数据。
        filters: 等值过滤条件。为 None 表示不过滤。

    Returns:
        全部条件满足返回 True。
    """
    if not filters:
        return True
    return all(metadata.get(key) == value for key, value in filters.items())


PGVECTOR_SCHEMA_TEMPLATE = """\
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS {table} (
    id            TEXT PRIMARY KEY,
    document_id   TEXT NOT NULL,
    chunk_index   INTEGER NOT NULL,
    text          TEXT NOT NULL,
    source        TEXT NOT NULL,
    metadata      JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    embedding     vector({dimension}) NOT NULL
);

CREATE INDEX IF NOT EXISTS {table}_document_idx ON {table} (document_id);
CREATE INDEX IF NOT EXISTS {table}_embedding_idx
    ON {table} USING hnsw (embedding vector_cosine_ops);
"""
"""pgvector 的建表语句。

维度必须写进 ``vector(n)``——pgvector 的向量列是定长的，维度与已有数据不一致
时无法写入。HNSW 索引让相似度检索不必全表扫描。
"""


if TYPE_CHECKING:  # pragma: no cover
    from pgvector import Vector


def _pg_vector(values: Sequence[float]) -> Vector:
    """转成 pgvector 的 ``Vector``。

    psycopg 的适配器只认 ``pgvector.Vector``——直接传 Python 的 list 会被当成
    ``double precision[]``，于是 ``<=>`` 算子找不到匹配的类型，报
    "operator does not exist: vector <=> double precision[]"。
    """
    from pgvector import Vector

    return Vector(list(values))


class PgVectorStore:
    """PostgreSQL + pgvector 的向量存储。

    **每次操作开一条连接**。这是有意为之的简化：本项目的规模（一个学习者
    的课程材料）用不着连接池，而连接池会引入生命周期管理的问题。生产环境
    应当换成 ``psycopg_pool.AsyncConnectionPool``。
    """

    def __init__(
        self,
        dsn: str,
        *,
        dimension: int,
        table: str = "rag_chunks",
    ) -> None:
        """初始化存储。

        Args:
            dsn: PostgreSQL 连接串。
            dimension: 向量维度。必须与嵌入模型一致。
            table: 表名。
        """
        self.dsn = dsn
        self.dimension = dimension
        self.table = table

    def schema(self) -> str:
        """建表语句。

        Returns:
            DDL 文本。
        """
        return PGVECTOR_SCHEMA_TEMPLATE.format(table=self.table, dimension=self.dimension)

    async def ensure_schema(self) -> None:
        """确保扩展、表与索引就位。

        Raises:
            LLMError: 数据库操作失败。
        """
        from agent_learning_coach.core.llm.errors import LLMError

        try:
            async with self._connect() as conn:
                await conn.execute(self.schema())
                await conn.commit()
        except Exception as exc:
            raise LLMError(f"初始化 pgvector 表结构失败：{exc}") from exc

    async def add(
        self,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
    ) -> None:
        """写入切片与向量。

        Args:
            chunks: 切片。
            embeddings: 与切片一一对应的向量。

        Raises:
            ValueError: 数量不匹配或维度不符。
            LLMError: 数据库操作失败。
        """
        from agent_learning_coach.core.llm.errors import LLMError

        _validate(chunks, embeddings)
        for vector in embeddings:
            if len(vector) != self.dimension:
                raise ValueError(f"向量维度不一致：存储为 {self.dimension}，收到 {len(vector)}")
        if not chunks:
            return
        try:
            async with self._connect() as conn:
                async with conn.cursor() as cur:
                    for chunk, vector in zip(chunks, embeddings, strict=True):
                        await cur.execute(
                            f"""
                            INSERT INTO {self.table}
                                (id, document_id, chunk_index, text, source, metadata, embedding)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (id) DO UPDATE SET
                                document_id = EXCLUDED.document_id,
                                chunk_index = EXCLUDED.chunk_index,
                                text = EXCLUDED.text,
                                source = EXCLUDED.source,
                                metadata = EXCLUDED.metadata,
                                embedding = EXCLUDED.embedding
                            """,
                            (
                                chunk.id,
                                chunk.document_id,
                                chunk.index,
                                chunk.text,
                                chunk.source,
                                _json(chunk.metadata),
                                _pg_vector(vector),
                            ),
                        )
                await conn.commit()
        except Exception as exc:
            raise LLMError(f"写入向量失败：{exc}") from exc

    async def search(
        self,
        embedding: Sequence[float],
        *,
        top_k: int = 5,
        filters: Mapping[str, Any] | None = None,
    ) -> list[ScoredChunk]:
        """检索最相关的切片。

        过滤条件在 SQL 里下推——这样索引才能用上。在应用层过滤等于把全部向量
        拉回内存再筛，那是「索引 ≠ 检索」最常见的误用。

        Args:
            embedding: 查询向量。
            top_k: 最多返回几条。
            filters: 元数据等值过滤。

        Returns:
            按相关度从高到低排列的切片。

        Raises:
            LLMError: 数据库操作失败。
        """
        from agent_learning_coach.core.llm.errors import LLMError

        clauses: list[str] = []
        filter_params: list[Any] = []
        if filters:
            for key, value in filters.items():
                clauses.append("metadata ->> %s = %s")
                filter_params.extend([key, str(value)])
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        # 占位符顺序必须与 SQL 里的出现顺序一致：
        # SELECT 里的向量 → WHERE 里的过滤值 → ORDER BY 里的向量 → LIMIT。
        vector = _pg_vector(embedding)
        params: list[Any] = [vector, *filter_params, vector, top_k]
        try:
            async with self._connect() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"""
                        SELECT id, document_id, chunk_index, text, source, metadata,
                               1 - (embedding <=> %s) AS similarity
                        FROM {self.table}
                        {where}
                        ORDER BY embedding <=> %s
                        LIMIT %s
                        """,
                        params,
                    )
                    rows = await cur.fetchall()
        except Exception as exc:
            raise LLMError(f"向量检索失败：{exc}") from exc

        results: list[ScoredChunk] = []
        for row in rows:
            chunk = Chunk(
                id=row[0],
                document_id=row[1],
                index=row[2],
                text=row[3],
                source=row[4],
                metadata=dict(row[5]),
            )
            results.append(ScoredChunk(chunk=chunk, score=float(row[6])))
        return results

    async def delete_document(self, document_id: str) -> int:
        """删除一份文档的全部切片。

        Args:
            document_id: 文档标识。

        Returns:
            删除的条数。

        Raises:
            LLMError: 数据库操作失败。
        """
        from agent_learning_coach.core.llm.errors import LLMError

        try:
            async with self._connect() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"DELETE FROM {self.table} WHERE document_id = %s",
                        (document_id,),
                    )
                    deleted = cur.rowcount
                await conn.commit()
        except Exception as exc:
            raise LLMError(f"删除文档失败：{exc}") from exc
        return int(deleted)

    async def count(self) -> int:
        """当前存储的切片总数。

        Returns:
            条数。

        Raises:
            LLMError: 数据库操作失败。
        """
        from agent_learning_coach.core.llm.errors import LLMError

        try:
            async with self._connect() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(f"SELECT count(*) FROM {self.table}")
                    row = await cur.fetchone()
        except Exception as exc:
            raise LLMError(f"统计切片数失败：{exc}") from exc
        return int(row[0]) if row else 0

    @asynccontextmanager
    async def _connect(self):  # type: ignore[no-untyped-def]
        """打开一条连接并注册 pgvector 适配。

        Yields:
            已注册的异步连接。
        """
        from pgvector.psycopg import register_vector_async
        from psycopg import AsyncConnection

        conn = await AsyncConnection.connect(self.dsn)
        try:
            await register_vector_async(conn)
            yield conn
        finally:
            await conn.close()


def _json(metadata: Mapping[str, Any]) -> str:
    """把元数据序列化成 JSON 文本。

    Args:
        metadata: 元数据。

    Returns:
        JSON 文本。
    """
    import json

    return json.dumps(dict(metadata), ensure_ascii=False)


__all__ = [
    "InMemoryVectorStore",
    "PGVECTOR_SCHEMA_TEMPLATE",
    "PgVectorStore",
    "VectorStore",
]
