"""RAG 流水线端到端演示。

跑法::

    uv run python examples/rag_pipeline.py

默认走**离线**路径：确定性的哈希嵌入 + 内存向量存储，不联网、不需要数据库。
因此这个脚本在 CI 里也能跑，且每次输出完全一样。

想验证 pgvector 路径::

    uv run python examples/rag_pipeline.py --pg

脚本要展示的四件事：

1. **加载**：从目录读教材材料，文档 id 由来源推导（可复现）。
2. **切分**：定长切分会截断语义单元，递归切分不会。
3. **入库**：切片 → 向量 → 存储。
4. **检索**：查回最相关的切片，并演示元数据过滤下推。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import tempfile
from pathlib import Path

from agent_learning_coach.core.rag import (
    Chunk,
    DirectoryLoader,
    FixedSizeChunker,
    HashingEmbedder,
    InMemoryVectorStore,
    RecursiveChunker,
    VectorRetriever,
    VectorStore,
)

PG_DSN = os.environ.get("PG_DSN", "postgresql://localhost:5432/agent_learning_coach_test")

SAMPLE_MATERIAL = {
    "closures.md": (
        "# 闭包\n\n"
        "闭包是函数连同它引用的外部变量的组合。\n\n"
        "闭包让函数可以携带自己的环境，这是函数式编程里最重要的特性之一。\n"
    ),
    "recursion.md": (
        "# 递归\n\n"
        "递归是函数调用自己来解决问题。\n\n"
        "递归需要一个终止条件，否则会一直展开直到栈溢出。\n"
    ),
    "pets.md": ("# 猫\n\n猫喜欢在温暖的沙发上睡觉。\n\n猫的呼噜声据说有治愈作用。\n"),
}


def _write_material(root: Path) -> Path:
    """把示例材料写到临时目录。

    Args:
        root: 目标目录。

    Returns:
        写入的目录。
    """
    root.mkdir(parents=True, exist_ok=True)
    for name, text in SAMPLE_MATERIAL.items():
        (root / name).write_text(text, encoding="utf-8")
    return root


async def _ingest(directory: Path, store: VectorStore, embedder: HashingEmbedder) -> list[Chunk]:
    """加载 → 切分 → 嵌入 → 入库。

    Args:
        directory: 材料目录。
        store: 向量存储。
        embedder: 嵌入客户端。

    Returns:
        全部切片。
    """
    documents = DirectoryLoader(directory).load()
    print(f"1. 加载：{len(documents)} 份文档")
    for document in documents:
        print(f"   - {Path(document.source).name}  id={document.id[:12]}…  {len(document.text)} 字")

    chunker = RecursiveChunker(size=120, overlap=20)
    chunks: list[Chunk] = []
    for document in documents:
        chunks.extend(chunker.split(document))
    print(f"\n2. 切分（递归，size=120 overlap=20）：{len(chunks)} 块")

    vectors = await embedder.embed([chunk.text for chunk in chunks])
    await store.add(chunks, vectors)
    print(f"3. 入库：{len(chunks)} 个向量，维度 {embedder.dimension}")
    return chunks


def _compare_chunkers(document_text: str) -> None:
    """对比两种切分器对同一段文本的处理。

    Args:
        document_text: 待切分文本。
    """
    from agent_learning_coach.core.rag import Document, document_id

    document = Document(id=document_id("demo.md"), text=document_text, source="demo.md")

    fixed = FixedSizeChunker(size=120, overlap=0).split(document)
    recursive = RecursiveChunker(size=120, overlap=0).split(document)

    print("\n切分策略对比（同一段文本，size=120 overlap=0）")
    print(f"  定长切分 : {len(fixed)} 块")
    print(f"  递归切分 : {len(recursive)} 块")
    for index, chunk in enumerate(fixed):
        print(f"    定长[{index}] 结尾 {chunk.text[-20:]!r}")
    for index, chunk in enumerate(recursive):
        print(f"    递归[{index}] 结尾 {chunk.text[-20:]!r}")


async def _demo_pg(directory: Path) -> None:
    """用 pgvector 跑一遍同样的流程。

    Args:
        directory: 材料目录。
    """
    from agent_learning_coach.core.rag import PgVectorStore

    embedder = HashingEmbedder(dimension=256)
    store = PgVectorStore(PG_DSN, dimension=256, table="rag_chunks_demo")
    await store.ensure_schema()
    print(f"3. 入库：pgvector 表 {store.table}（维度 {store.dimension}）")

    documents = DirectoryLoader(directory).load()
    chunker = RecursiveChunker(size=120, overlap=20)
    chunks = [chunk for document in documents for chunk in chunker.split(document)]
    await store.add(chunks, await embedder.embed([chunk.text for chunk in chunks]))

    retriever = VectorRetriever(store, embedder, top_k=2)
    hits = await retriever.retrieve("闭包 引用的外部变量")
    print("\n4. 检索（pgvector）：")
    for hit in hits:
        print(f"   {hit.score:.3f}  {hit.chunk.source.rsplit('/', 1)[-1]}  {hit.chunk.text[:36]}…")


async def main() -> None:
    """跑一遍演示。"""
    parser = argparse.ArgumentParser(description="RAG 流水线演示")
    parser.add_argument("--pg", action="store_true", help="用 pgvector 而不是内存存储")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as tmp:
        directory = _write_material(Path(tmp))
        embedder = HashingEmbedder(dimension=256)

        if args.pg:
            await _demo_pg(directory)
            return

        store = InMemoryVectorStore()
        await _ingest(directory, store, embedder)

        retriever = VectorRetriever(store, embedder, top_k=2)
        print("\n4. 检索：")
        for query in ("闭包 引用的外部变量", "终止条件"):
            print(f"\n   查询：{query}")
            for hit in await retriever.retrieve(query):
                print(f"   {hit.score:.3f}  {Path(hit.chunk.source).name}  {hit.chunk.text[:36]}…")

        print("\n5. 元数据过滤（过滤下推到存储层）：")
        everything = await retriever.retrieve("闭包", top_k=10)
        only_closures = await retriever.retrieve(
            "闭包", top_k=10, filters={"filename": "closures.md"}
        )
        print(f"   不带过滤：{len(everything)} 条")
        print(f"   只取 closures.md：{len(only_closures)} 条")
        for hit in only_closures:
            print(f"   → {Path(hit.chunk.source).name}  {hit.chunk.text[:36]}…")

        _compare_chunkers(
            "闭包是函数连同它引用的外部变量的组合。"
            "闭包让函数可以携带自己的环境，这是函数式编程里最重要的特性之一。"
            "很多语言都支持闭包，只是叫法不同。\n\n"
            "递归是函数调用自己来解决问题。"
            "递归需要一个终止条件，否则会一直展开直到栈溢出为止。"
            "尾递归可以被优化成循环。\n\n"
            "猫喜欢在温暖的沙发上睡觉，呼噜声据说有治愈作用。"
            "猫的睡眠时间比人长得多。"
        )


if __name__ == "__main__":
    asyncio.run(main())
