"""PgVectorStore 的集成测试（Day 29）。

**需要 PostgreSQL + pgvector**，因此默认跳过：

```bash
export AGLC_TEST_DSN="host=/tmp port=55434 user=aglc dbname=rag"
uv run pytest tests/test_pgvector_store.py
```

没有环境变量时整个文件跳过——**默认的测试套件不依赖数据库**（离线、确定、快）。
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from agent_learning_coach.core.rag.store import PgVectorStore
from agent_learning_coach.core.rag.types import Chunk

pytest.importorskip("psycopg")
pytest.importorskip("pgvector")

DSN = os.environ.get("AGLC_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="需要 AGLC_TEST_DSN 指向带 pgvector 的 PostgreSQL")


def _chunk(chunk_id: str, text: str, metadata: dict[str, object]) -> Chunk:
    """构造一个切片。

    Args:
        chunk_id: 切片标识。
        text: 正文。
        metadata: 元数据。

    Returns:
        切片。
    """
    return Chunk(
        id=chunk_id,
        document_id=f"doc-{chunk_id}",
        text=text,
        index=0,
        source=f"/tmp/{chunk_id}.md",
        metadata=metadata,
    )


@pytest.fixture
def store() -> PgVectorStore:
    """构造一个建好表与索引的存储。

    **扩展先建**——那是部署的步骤（``CREATE EXTENSION`` 要超级用户权限），应用运行
    时不建。适配器注册会去找 ``vector`` 类型，扩展没建时它抛的话完全指不到真正
    的原因。

    Returns:
        向量存储。
    """
    import psycopg

    async def _ensure_extension() -> None:
        conn = await psycopg.AsyncConnection.connect(str(DSN))
        try:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            await conn.commit()
        finally:
            await conn.close()

    asyncio.run(_ensure_extension())
    table = f"rag_test_{uuid.uuid4().hex[:8]}"
    storage = PgVectorStore(
        str(DSN),
        dimension=3,
        table=table,
        index="hnsw",
    )
    return storage


def test_schema_and_index_are_created() -> None:
    """建表与建索引语句可用。"""
    storage = PgVectorStore("host=/tmp dbname=x", dimension=3, table="t", index="hnsw")

    schema = storage.schema()

    assert "USING hnsw" in schema
    assert "vector(3)" in schema


def test_unknown_index_kind_is_rejected() -> None:
    """不认识的索引方案直接拒绝。"""
    with pytest.raises(ValueError, match="未知的向量索引方案"):
        PgVectorStore("host=/tmp dbname=x", dimension=3, index="magic")


def test_scalar_filter_selects_by_metadata(store: PgVectorStore) -> None:
    """标量过滤：等值匹配。"""
    asyncio.run(store.ensure_schema())
    asyncio.run(
        store.add(
            [_chunk("c1", "讲闭包", {"course_id": "c1"})],
            [[1.0, 0.0, 0.0]],
        ),
    )
    asyncio.run(
        store.add(
            [_chunk("c2", "讲别的", {"course_id": "c2"})],
            [[1.0, 0.0, 0.0]],
        ),
    )

    hits = asyncio.run(
        store.search([1.0, 0.0, 0.0], top_k=10, filters={"course_id": "c1"}),
    )

    assert [hit.chunk.id for hit in hits] == ["c1"]


def test_sequence_filter_matches_any_element(store: PgVectorStore) -> None:
    """序列过滤：包含任一（这是「教 k1 时」能用得上的语义）。"""
    asyncio.run(store.ensure_schema())
    asyncio.run(
        store.add(
            [_chunk("c1", "讲闭包", {"point_ids": ["k1", "k2"]})],
            [[1.0, 0.0, 0.0]],
        ),
    )
    asyncio.run(
        store.add(
            [_chunk("c2", "讲作用域", {"point_ids": ["k1"]})],
            [[1.0, 0.0, 0.0]],
        ),
    )
    asyncio.run(
        store.add(
            [_chunk("c3", "讲装饰器", {"point_ids": ["k3"]})],
            [[1.0, 0.0, 0.0]],
        ),
    )

    hits = asyncio.run(
        store.search([1.0, 0.0, 0.0], top_k=10, filters={"point_ids": ["k2", "k3"]}),
    )

    assert sorted(hit.chunk.id for hit in hits) == ["c1", "c3"]


def test_min_score_drops_weak_hits(store: PgVectorStore) -> None:
    """阈值：低于它的结果不返回。"""
    asyncio.run(store.ensure_schema())
    asyncio.run(store.add([_chunk("c1", "很相关", {})], [[1.0, 0.0, 0.0]]))
    asyncio.run(store.add([_chunk("c2", "不相关", {})], [[-1.0, 0.0, 0.0]]))

    hits = asyncio.run(store.search([1.0, 0.0, 0.0], top_k=10, min_score=0.5))

    assert [hit.chunk.id for hit in hits] == ["c1"]


def test_filter_and_threshold_together(store: PgVectorStore) -> None:
    """过滤与阈值同时生效——占位符顺序不能错。"""
    asyncio.run(store.ensure_schema())
    asyncio.run(
        store.add(
            [_chunk("c1", "讲闭包", {"course_id": "c1", "point_ids": ["k1"]})],
            [[1.0, 0.0, 0.0]],
        ),
    )
    asyncio.run(
        store.add(
            [_chunk("c2", "讲别的课", {"course_id": "c2", "point_ids": ["k1"]})],
            [[1.0, 0.0, 0.0]],
        ),
    )
    asyncio.run(store.add([_chunk("c3", "弱相关", {"course_id": "c1"})], [[-1.0, 0.0, 0.0]]))

    hits = asyncio.run(
        store.search(
            [1.0, 0.0, 0.0],
            top_k=10,
            filters={"course_id": "c1", "point_ids": ["k1"]},
            min_score=0.5,
        ),
    )

    assert [hit.chunk.id for hit in hits] == ["c1"]


def test_missing_extension_gives_an_actionable_error() -> None:
    """扩展没建时，错误要说清怎么修——而不是「vector type not found」。"""
    import psycopg

    from agent_learning_coach.core.llm.errors import LLMError

    dsn = str(DSN)
    admin = dsn.replace("dbname=rag", "dbname=postgres")

    async def _fresh_database() -> str:
        # CREATE DATABASE 不能在事务里跑——所以用自动提交。
        conn = await psycopg.AsyncConnection.connect(admin, autocommit=True)
        try:
            name = f"rag_noext_{uuid.uuid4().hex[:8]}"
            await conn.execute(f'CREATE DATABASE "{name}"')
        finally:
            await conn.close()
        return dsn.replace("dbname=rag", f"dbname={name}")

    async def _drop(name: str) -> None:
        conn = await psycopg.AsyncConnection.connect(admin, autocommit=True)
        try:
            await conn.execute(f'DROP DATABASE IF EXISTS "{name}"')
        finally:
            await conn.close()

    bare = asyncio.run(_fresh_database())
    try:
        storage = PgVectorStore(bare, dimension=3, table="t", index="hnsw")
        with pytest.raises(LLMError, match="扩展未安装"):
            asyncio.run(storage.ensure_schema())
    finally:
        asyncio.run(_drop(bare.split("dbname=")[-1]))
