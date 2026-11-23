"""RAG（检索增强生成）。

一条完整的流水线：

```
DocumentLoader  →  Chunker  →  EmbeddingClient  →  VectorStore
   读成文档          切成检索单元      变成向量            存下来
                                                            ↓
                                    VectorRetriever ←──────┘
                                    查回最相关的切片
```

组成：

- **Document / Chunk**（``types``）：摄取单位与检索单位分开——检索命中的是
  切片，但切片必须能回溯到它的来源。
- **Document Loader**（``loader``）：把外部材料读成文档。id 由来源推导，
  可复现。
- **Chunking**（``chunking``）：``FixedSizeChunker``（定长，主教材 5.3.2 的
  做法）与 ``RecursiveChunker``（按结构递归，不切断语义单元）。
- **Embedding**（``embedding``）：``HashingEmbedder``（确定性、离线、词汇级）
  与 ``OpenAICompatibleEmbedder``（生产路径）。
- **Vector Store**（``store``）：``InMemoryVectorStore``（测试与演示）与
  ``PgVectorStore``（PostgreSQL + pgvector，生产）。
- **Vector Retriever**（``retrieval``）：嵌入 → 检索，过滤下推到存储层。

依赖方向：本模块只依赖 ``core/llm`` 的错误类型与 ``core/text`` 的分词，不认识
``core/agent``。

导入约定：调用方统一从 ``agent_learning_coach.core.rag`` 导入公开符号。
"""

from agent_learning_coach.core.rag.chunking import (
    DEFAULT_SEPARATORS,
    Chunker,
    FixedSizeChunker,
    RecursiveChunker,
    chunk_id,
)
from agent_learning_coach.core.rag.embedding import (
    EmbeddingClient,
    HashingEmbedder,
    OpenAICompatibleEmbedder,
)
from agent_learning_coach.core.rag.loader import (
    DirectoryLoader,
    DocumentLoader,
    TextFileLoader,
    document_id,
)
from agent_learning_coach.core.rag.retrieval import VectorRetriever
from agent_learning_coach.core.rag.store import (
    PGVECTOR_SCHEMA_TEMPLATE,
    InMemoryVectorStore,
    PgVectorStore,
    VectorStore,
)
from agent_learning_coach.core.rag.types import Chunk, Document, ScoredChunk

__all__ = [
    "DEFAULT_SEPARATORS",
    "PGVECTOR_SCHEMA_TEMPLATE",
    "Chunk",
    "Chunker",
    "DirectoryLoader",
    "Document",
    "DocumentLoader",
    "EmbeddingClient",
    "FixedSizeChunker",
    "HashingEmbedder",
    "InMemoryVectorStore",
    "OpenAICompatibleEmbedder",
    "PgVectorStore",
    "RecursiveChunker",
    "ScoredChunk",
    "TextFileLoader",
    "VectorRetriever",
    "VectorStore",
    "chunk_id",
    "document_id",
]
