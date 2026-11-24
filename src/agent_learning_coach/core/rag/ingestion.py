"""摄取流水线。

Day 11 把「加载 → 切分 → 嵌入 → 入库」四段分别做好了，但没有把它们串起来。
本模块补上这一段：**把一个目录变成可检索的向量库**。

**摄取是幂等的**：文档 id 由来源推导，切片 id 由「文档 id + 序号」推导，因此
重复摄取同一份材料是**覆盖**而不是**追加**。这让「改了教材重新摄取一遍」成为
安全的日常操作。

**摄取不吞错**：读不了的文件被跳过并记录原因，而不是让整批摄取失败——一份
坏文件不该毁掉整个课程库。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from agent_learning_coach.core.rag.chunking import Chunker, RecursiveChunker
from agent_learning_coach.core.rag.embedding import EmbeddingClient
from agent_learning_coach.core.rag.loader import DirectoryLoader
from agent_learning_coach.core.rag.store import VectorStore
from agent_learning_coach.core.rag.types import Chunk, Document


@dataclass(frozen=True)
class IngestResult:
    """一次摄取的结果。"""

    documents: tuple[Document, ...] = ()
    """读到的文档。"""

    chunks: tuple[Chunk, ...] = ()
    """切出的切片。"""

    skipped: tuple[str, ...] = ()
    """被跳过的文件及原因。"""

    @property
    def document_count(self) -> int:
        """文档数。"""
        return len(self.documents)

    @property
    def chunk_count(self) -> int:
        """切片数。"""
        return len(self.chunks)

    def summary(self) -> str:
        """一句话摘要。

        Returns:
            形如 ``读取 3 份文档、切出 12 块、跳过 1 个文件``。
        """
        return (
            f"读取 {self.document_count} 份文档、"
            f"切出 {self.chunk_count} 块、"
            f"跳过 {len(self.skipped)} 个文件"
        )


@dataclass
class _Accumulator:
    """摄取过程中的可变累加器。"""

    documents: list[Document] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


async def ingest_directory(
    directory: Path | str,
    store: VectorStore,
    embedder: EmbeddingClient,
    *,
    chunker: Chunker | None = None,
    exclude: tuple[str, ...] = (),
) -> IngestResult:
    """把一个目录摄取进向量库。

    流程：读取文档 → 切分 → 嵌入 → 写入存储。

    Args:
        directory: 材料目录。
        store: 目标向量存储。
        embedder: 嵌入客户端。
        chunker: 切片器。为 None 时用递归切分（默认）。
        exclude: 排除的文件名片段。

    Returns:
        摄取结果。
    """
    chunker = chunker or RecursiveChunker()
    loader = DirectoryLoader(Path(directory), exclude=exclude)
    documents = loader.load()

    chunks: list[Chunk] = []
    for document in documents:
        chunks.extend(chunker.split(document))

    if chunks:
        vectors = await embedder.embed([chunk.text for chunk in chunks])
        await store.add(chunks, vectors)

    return IngestResult(
        documents=tuple(documents),
        chunks=tuple(chunks),
        skipped=tuple(loader.skipped),
    )


__all__ = ["IngestResult", "ingest_directory"]
