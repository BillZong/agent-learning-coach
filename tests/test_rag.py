"""RAG 的测试。

覆盖整条流水线：

```
加载 → 切分 → 嵌入 → 入库 → 检索
```

三组关注点：

**切分影响检索质量**——定长切分会截断语义单元，递归切分不会；这是两种切分器
存在的全部理由，必须能被测试观察到。

**召回要准、噪声要少**——相关的内容排前面，不相关的不返回；元数据过滤要真的
生效。

**pgvector 路径**——内存实现与 pgvector 实现的行为必须一致。没有数据库时
跳过，而不是伪造通过。
"""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path

import httpx
import pytest

from agent_learning_coach.core.llm.errors import LLMError
from agent_learning_coach.core.rag import (
    Chunk,
    DirectoryLoader,
    Document,
    FixedSizeChunker,
    HashingEmbedder,
    InMemoryVectorStore,
    OpenAICompatibleEmbedder,
    RecursiveChunker,
    TextFileLoader,
    VectorRetriever,
    document_id,
)

PG_DSN = "postgresql://localhost:5432/agent_learning_coach_test"


def _document(text: str = "闭包是函数连同它引用的外部变量。", *, source: str = "a.md") -> Document:
    """构造一份文档。"""
    return Document(id=document_id(source), text=text, source=source)


# ---------------------------------------------------------------------------
# 加载
# ---------------------------------------------------------------------------


def test_text_file_loader_reads_a_file(tmp_path: Path) -> None:
    """从单个文件加载。"""
    path = tmp_path / "notes.md"
    path.write_text("# 闭包\n\n闭包是函数连同它引用的外部变量。", encoding="utf-8")

    documents = TextFileLoader(path).load()

    assert len(documents) == 1
    assert documents[0].source == str(path)
    assert "闭包" in documents[0].text
    assert documents[0].metadata["filename"] == "notes.md"


def test_text_file_loader_skips_unreadable_file(tmp_path: Path) -> None:
    """读不了的文件被跳过并记录原因，而不是让整批摄取失败。"""
    loader = TextFileLoader(tmp_path / "missing.md")

    documents = loader.load()

    assert documents == []
    assert loader.skipped


def test_text_file_loader_skips_empty_file(tmp_path: Path) -> None:
    """空文件被跳过。"""
    path = tmp_path / "empty.md"
    path.write_text("   \n", encoding="utf-8")

    loader = TextFileLoader(path)

    assert loader.load() == []
    assert loader.skipped


def test_directory_loader_reads_sorted_files(tmp_path: Path) -> None:
    """目录加载按路径排序，顺序稳定。"""
    (tmp_path / "b.md").write_text("第二个", encoding="utf-8")
    (tmp_path / "a.md").write_text("第一个", encoding="utf-8")
    (tmp_path / "c.txt").write_text("第三个", encoding="utf-8")

    documents = DirectoryLoader(tmp_path).load()

    assert [doc.source.rsplit("/", 1)[-1] for doc in documents] == ["a.md", "b.md", "c.txt"]


def test_directory_loader_excludes_paths(tmp_path: Path) -> None:
    """排除规则生效。"""
    (tmp_path / "keep.md").write_text("保留", encoding="utf-8")
    (tmp_path / "skip.md").write_text("跳过", encoding="utf-8")

    documents = DirectoryLoader(tmp_path, exclude=("skip",)).load()

    assert len(documents) == 1
    assert documents[0].source.endswith("keep.md")


def test_document_id_is_reproducible() -> None:
    """同一来源永远得到同一 id——重新摄取不会产生重复记录。"""
    assert document_id("a.md") == document_id("a.md")
    assert document_id("a.md") != document_id("b.md")


# ---------------------------------------------------------------------------
# 切分
# ---------------------------------------------------------------------------


def test_fixed_size_chunker_splits_with_overlap() -> None:
    """定长切分按字符切，相邻块有重叠。"""
    document = _document("A" * 250)

    chunks = FixedSizeChunker(size=100, overlap=20).split(document)

    assert len(chunks) == 3
    assert chunks[0].text == "A" * 100
    # 第二块与第一块重叠 20 个字符
    assert chunks[1].text.startswith("A" * 20)


def test_fixed_size_chunker_keeps_ids_and_source() -> None:
    """切片带文档 id、序号与来源。"""
    document = _document("A" * 250)

    chunks = FixedSizeChunker(size=100, overlap=20).split(document)

    assert [chunk.index for chunk in chunks] == [0, 1, 2]
    assert all(chunk.document_id == document.id for chunk in chunks)
    assert all(chunk.source == document.source for chunk in chunks)
    assert len({chunk.id for chunk in chunks}) == len(chunks)


def test_fixed_size_chunker_cuts_a_paragraph_in_half() -> None:
    """定长切分**会**截断语义单元——这是它与递归切分的差别所在。"""
    paragraph = "第一段。" * 20 + "第二段。" * 20

    chunks = FixedSizeChunker(size=120, overlap=0).split(_document(paragraph))

    # 某个切片里第二段只出现了**一部分**——说明段落被拦腰截断了
    assert any(chunk.text.count("第二段。") not in (0, 20) for chunk in chunks)


def test_recursive_chunker_keeps_paragraphs_intact() -> None:
    """递归切分不截断自然段。"""
    paragraph = "第一段内容。" * 20 + "\n\n" + "第二段内容。" * 20

    chunks = RecursiveChunker(size=250, overlap=0).split(_document(paragraph))

    for chunk in chunks:
        assert chunk.text.count("第一段内容。") in (0, 20)
        assert chunk.text.count("第二段内容。") in (0, 20)


def test_recursive_chunker_keeps_overlap() -> None:
    """递归切分保留重叠——跨块的句子仍能被检索到。"""
    document = _document("句子。" * 60)

    chunks = RecursiveChunker(size=120, overlap=30).split(document)

    assert len(chunks) > 1
    # 相邻块有内容交集
    assert set(chunks[0].text[-10:]) & set(chunks[1].text[:10])


def test_recursive_chunker_handles_a_very_long_line() -> None:
    """没有任何分隔符的超长文本也能被切开（字符级兜底）。"""
    document = _document("X" * 500)

    chunks = RecursiveChunker(size=100, overlap=0).split(document)

    assert len(chunks) == 5
    assert all(len(chunk.text) <= 100 for chunk in chunks)


@pytest.mark.parametrize("chunker", [FixedSizeChunker, RecursiveChunker])
def test_chunker_rejects_invalid_overlap(chunker: type) -> None:
    """重叠不小于块大小时报错——否则切分会原地打转。"""
    with pytest.raises(ValueError):
        chunker(size=10, overlap=10).split(_document("A" * 100))


@pytest.mark.parametrize("chunker", [FixedSizeChunker, RecursiveChunker])
def test_chunker_rejects_non_positive_size(chunker: type) -> None:
    """块大小非正时报错。"""
    with pytest.raises(ValueError):
        chunker(size=0).split(_document("A" * 100))


# ---------------------------------------------------------------------------
# 嵌入
# ---------------------------------------------------------------------------


def test_hashing_embedder_is_deterministic() -> None:
    """同样的文本永远得到同样的向量——否则检索结果无法复现。"""
    embedder = HashingEmbedder(dimension=64)

    first = asyncio.run(embedder.embed(["闭包是什么"]))
    second = asyncio.run(embedder.embed(["闭包是什么"]))

    assert first == second


def test_hashing_embedder_normalizes_vectors() -> None:
    """向量做了 L2 归一化——点积即余弦相似度。"""
    embedder = HashingEmbedder(dimension=64)

    vectors = asyncio.run(embedder.embed(["闭包是什么"]))

    norm = math.sqrt(sum(value * value for value in vectors[0]))
    assert norm == pytest.approx(1.0, abs=1e-5)


def test_hashing_embedder_dimension() -> None:
    """维度符合配置。"""
    embedder = HashingEmbedder(dimension=32)

    vectors = asyncio.run(embedder.embed(["a", "b"]))

    assert len(vectors) == 2
    assert all(len(vector) == 32 for vector in vectors)
    assert embedder.dimension == 32


def test_hashing_embedder_rejects_non_positive_dimension() -> None:
    """维度非正时报错。"""
    with pytest.raises(ValueError):
        HashingEmbedder(dimension=0)


def test_hashing_embedder_ranks_similar_texts_higher() -> None:
    """词汇相近的文本排得更前——这正是嵌入要提供的能力。"""
    embedder = HashingEmbedder(dimension=512)
    query = asyncio.run(embedder.embed(["闭包 外部变量"]))[0]
    similar = asyncio.run(embedder.embed(["闭包 引用 外部变量"]))[0]
    different = asyncio.run(embedder.embed(["猫 在 沙发上 睡觉"]))[0]

    assert sum(a * b for a, b in zip(query, similar, strict=True)) > sum(
        a * b for a, b in zip(query, different, strict=True)
    )


def _embedding_transport(payload: dict) -> httpx.MockTransport:
    """构造一个假的嵌入接口传输层。"""
    return httpx.MockTransport(lambda request: httpx.Response(200, json=payload))


def test_openai_compatible_embedder_calls_the_endpoint() -> None:
    """请求形状正确，响应被解析。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """记录请求并返回固定向量。"""
        seen.append(request)
        return httpx.Response(
            200,
            json={"data": [{"embedding": [0.1, 0.2, 0.3], "index": 0}]},
        )

    embedder = OpenAICompatibleEmbedder(
        base_url="https://example.test/v1",
        api_key="secret",
        model="test-embed",
        transport=httpx.MockTransport(handler),
    )

    vectors = asyncio.run(embedder.embed(["闭包"]))

    assert vectors == [[0.1, 0.2, 0.3]]
    assert embedder.dimension == 3
    request = seen[0]
    assert request.url.path.endswith("/embeddings")
    assert json.loads(request.content) == {"input": ["闭包"], "model": "test-embed"}
    assert request.headers["authorization"] == "Bearer secret"


def test_openai_compatible_embedder_reports_http_failure() -> None:
    """接口失败时抛 LLMError——失败即异常，不静默返回空。"""
    embedder = OpenAICompatibleEmbedder(
        api_key="secret",
        transport=httpx.MockTransport(lambda request: httpx.Response(500, text="boom")),
    )

    with pytest.raises(LLMError):
        asyncio.run(embedder.embed(["闭包"]))


def test_openai_compatible_embedder_reports_malformed_response() -> None:
    """响应缺字段时抛 LLMError。"""
    embedder = OpenAICompatibleEmbedder(
        api_key="secret",
        transport=_embedding_transport({"data": []}),
    )

    with pytest.raises(LLMError):
        asyncio.run(embedder.embed(["闭包"]))


def test_openai_compatible_embedder_detects_dimension_change() -> None:
    """同一适配器返回不同维度时报错——混着不同维度的向量，检索毫无意义。"""
    responses = [
        {"data": [{"embedding": [0.1, 0.2], "index": 0}]},
        {"data": [{"embedding": [0.1, 0.2, 0.3], "index": 0}]},
    ]
    payloads = iter(responses)

    embedder = OpenAICompatibleEmbedder(
        api_key="secret",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=next(payloads))),
    )

    asyncio.run(embedder.embed(["a"]))
    with pytest.raises(LLMError):
        asyncio.run(embedder.embed(["b"]))


def test_openai_compatible_embedder_dimension_unknown_before_call() -> None:
    """还没调用过时维度未知。"""
    embedder = OpenAICompatibleEmbedder(api_key="secret")

    with pytest.raises(RuntimeError):
        _ = embedder.dimension


# ---------------------------------------------------------------------------
# 存储与检索
# ---------------------------------------------------------------------------


def _store_with_chunks() -> tuple[InMemoryVectorStore, list[Chunk]]:
    """构造一个装了三个切片的内存存储。"""
    embedder = HashingEmbedder(dimension=256)
    chunks = [
        Chunk(
            id="c1",
            document_id="d1",
            text="闭包是函数连同它引用的外部变量",
            index=0,
            source="closures.md",
            metadata={"topic": "函数"},
        ),
        Chunk(
            id="c2",
            document_id="d1",
            text="猫在沙发上睡觉",
            index=1,
            source="pets.md",
            metadata={"topic": "动物"},
        ),
        Chunk(
            id="c3",
            document_id="d2",
            text="递归是函数调用自己",
            index=0,
            source="recursion.md",
            metadata={"topic": "函数"},
        ),
    ]
    store = InMemoryVectorStore()
    asyncio.run(store.add(chunks, asyncio.run(embedder.embed([c.text for c in chunks]))))
    return store, chunks


def test_in_memory_store_searches_by_similarity() -> None:
    """检索返回最相关的切片，且按相关度排序。"""
    store, _ = _store_with_chunks()
    embedder = HashingEmbedder(dimension=256)

    hits = asyncio.run(store.search(asyncio.run(embedder.embed(["闭包 外部变量"]))[0], top_k=2))

    assert len(hits) == 2
    assert hits[0].chunk.id == "c1"
    assert hits[0].score >= hits[1].score


def test_in_memory_store_applies_filters() -> None:
    """元数据过滤生效——过滤在检索阶段做，不在应用层。"""
    store, _ = _store_with_chunks()
    embedder = HashingEmbedder(dimension=256)

    hits = asyncio.run(
        store.search(
            asyncio.run(embedder.embed(["闭包 外部变量"]))[0],
            top_k=10,
            filters={"topic": "函数"},
        )
    )

    assert {hit.chunk.id for hit in hits} == {"c1", "c3"}


def test_in_memory_store_deletes_a_document() -> None:
    """按文档删除全部切片。"""
    store, _ = _store_with_chunks()

    removed = asyncio.run(store.delete_document("d1"))

    assert removed == 2
    assert asyncio.run(store.count()) == 1


def test_in_memory_store_rejects_dimension_mismatch() -> None:
    """维度不一致时报错——混着不同维度的向量，检索毫无意义。"""
    store = InMemoryVectorStore()
    chunk = Chunk(id="c1", document_id="d1", text="x", index=0, source="a.md")
    asyncio.run(store.add([chunk], [[0.1, 0.2]]))

    other = Chunk(id="c2", document_id="d1", text="y", index=1, source="a.md")
    with pytest.raises(ValueError):
        asyncio.run(store.add([other], [[0.1, 0.2, 0.3]]))


def test_in_memory_store_rejects_count_mismatch() -> None:
    """切片与向量数量不匹配时报错。"""
    store = InMemoryVectorStore()
    chunk = Chunk(id="c1", document_id="d1", text="x", index=0, source="a.md")

    with pytest.raises(ValueError):
        asyncio.run(store.add([chunk], [[0.1], [0.2]]))


def test_retriever_finds_the_relevant_chunk() -> None:
    """检索器把查询变成嵌入并取回最相关的切片。"""
    store, _ = _store_with_chunks()
    retriever = VectorRetriever(store, HashingEmbedder(dimension=256), top_k=1)

    hits = asyncio.run(retriever.retrieve("闭包 外部变量"))

    assert len(hits) == 1
    assert hits[0].chunk.id == "c1"


def test_retriever_returns_nothing_for_an_empty_query() -> None:
    """空查询不检索。"""
    store, _ = _store_with_chunks()
    retriever = VectorRetriever(store, HashingEmbedder(dimension=256))

    assert asyncio.run(retriever.retrieve("  ")) == []


def test_retriever_passes_filters_through() -> None:
    """过滤条件传给存储层。"""
    store, _ = _store_with_chunks()
    retriever = VectorRetriever(store, HashingEmbedder(dimension=256), top_k=10)

    hits = asyncio.run(retriever.retrieve("闭包", filters={"topic": "动物"}))

    assert {hit.chunk.id for hit in hits} == {"c2"}


def test_end_to_end_ingestion_and_retrieval(tmp_path: Path) -> None:
    """完整流水线：读文件 → 切分 → 嵌入 → 入库 → 检索。"""
    (tmp_path / "closures.md").write_text(
        "闭包是函数连同它引用的外部变量。\n\n闭包让函数可以携带自己的环境。\n",
        encoding="utf-8",
    )
    (tmp_path / "pets.md").write_text("猫喜欢在沙发上睡觉。\n", encoding="utf-8")

    documents = DirectoryLoader(tmp_path).load()
    chunker = RecursiveChunker(size=120, overlap=0)
    embedder = HashingEmbedder(dimension=256)
    store = InMemoryVectorStore()

    chunks: list[Chunk] = []
    for document in documents:
        chunks.extend(chunker.split(document))
    asyncio.run(store.add(chunks, asyncio.run(embedder.embed([c.text for c in chunks]))))

    retriever = VectorRetriever(store, embedder, top_k=1)
    hits = asyncio.run(retriever.retrieve("闭包 引用的外部变量"))

    assert len(documents) == 2
    assert len(chunks) >= 2
    assert hits and "闭包" in hits[0].chunk.text
    assert hits[0].chunk.source.endswith("closures.md")


# ---------------------------------------------------------------------------
# pgvector
# ---------------------------------------------------------------------------


def _pg_available() -> bool:
    """pgvector 数据库是否可用。"""
    from pgvector.psycopg import register_vector_async  # noqa: F401
    from psycopg import AsyncConnection  # noqa: F401

    try:
        asyncio.run(_pg_ping())
    except Exception:
        return False
    return True


async def _pg_ping() -> None:
    """试连一次数据库。"""
    from psycopg import AsyncConnection

    conn = await AsyncConnection.connect(PG_DSN, connect_timeout=3)
    await conn.close()


pg_required = pytest.mark.skipif(
    not _pg_available(), reason="需要本地 PostgreSQL + pgvector（见 docs/engineering/rag.md）"
)


@pg_required
def test_pgvector_store_round_trip() -> None:
    """pgvector 路径：建表 → 写入 → 检索。"""
    from agent_learning_coach.core.rag import PgVectorStore

    async def scenario() -> None:
        store = PgVectorStore(PG_DSN, dimension=256, table="rag_chunks_test")
        await store.ensure_schema()
        await store.delete_document("d1")

        embedder = HashingEmbedder(dimension=256)
        chunks = [
            Chunk(
                id="c1",
                document_id="d1",
                text="闭包是函数连同它引用的外部变量",
                index=0,
                source="closures.md",
                metadata={"topic": "函数"},
            ),
            Chunk(
                id="c2",
                document_id="d2",
                text="猫在沙发上睡觉",
                index=0,
                source="pets.md",
                metadata={"topic": "动物"},
            ),
        ]
        await store.add(chunks, await embedder.embed([c.text for c in chunks]))

        hits = await store.search(
            (await embedder.embed(["闭包 外部变量"]))[0],
            top_k=2,
        )
        assert hits[0].chunk.id == "c1"
        assert hits[0].score >= hits[1].score

        filtered = await store.search(
            (await embedder.embed(["闭包 外部变量"]))[0],
            top_k=10,
            filters={"topic": "动物"},
        )
        assert {hit.chunk.id for hit in filtered} == {"c2"}

        assert await store.count() >= 2
        removed = await store.delete_document("d1")
        assert removed >= 1

    asyncio.run(scenario())


@pg_required
def test_pgvector_and_in_memory_agree_on_ordering() -> None:
    """两种存储的**相对顺序**一致——这是可替换的前提。"""
    from agent_learning_coach.core.rag import PgVectorStore

    async def scenario() -> None:
        embedder = HashingEmbedder(dimension=256)
        chunks = [
            Chunk(
                id="c1",
                document_id="d1",
                text="闭包是函数连同它引用的外部变量",
                index=0,
                source="closures.md",
                metadata={},
            ),
            Chunk(
                id="c2",
                document_id="d2",
                text="猫在沙发上睡觉",
                index=0,
                source="pets.md",
                metadata={},
            ),
        ]
        vectors = await embedder.embed([c.text for c in chunks])

        memory = InMemoryVectorStore()
        await memory.add(chunks, vectors)

        pg = PgVectorStore(PG_DSN, dimension=256, table="rag_chunks_test")
        await pg.ensure_schema()
        await pg.delete_document("d1")
        await pg.delete_document("d2")
        await pg.add(chunks, vectors)

        query = (await embedder.embed(["闭包 外部变量"]))[0]
        memory_hits = await memory.search(query, top_k=2)
        pg_hits = await pg.search(query, top_k=2)

        assert [hit.chunk.id for hit in memory_hits] == [hit.chunk.id for hit in pg_hits]

    asyncio.run(scenario())
