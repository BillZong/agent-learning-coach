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
  做法）与 ``RecursiveChunker``（按结构递归，不切断语义单元）；Day 28 补上
  ``CodeChunker``（按顶层定义切）、``ExerciseChunker``（按题号切）与
  ``PolicyChunker``（按材料类型选策略）。
- **Embedding**（``embedding``）：``HashingEmbedder``（确定性、离线、词汇级）
  与 ``OpenAICompatibleEmbedder``（生产路径）。
- **Vector Store**（``store``）：``InMemoryVectorStore``（测试与演示）与
  ``PgVectorStore``（PostgreSQL + pgvector，生产）。
- **Vector Retriever**（``retrieval``）：嵌入 → 检索，过滤下推到存储层。
- **Ingestion**（``ingestion``）：把一个目录摄取进向量库。幂等——重复摄取是
  覆盖而不是追加。
- **Retrieval Tools**（``tools``）：``SearchCourseMaterialTool``（语义检索）
  与 ``RetrieveKnowledgeTool``（按来源取回）。两者都产出可校验的引用。
- **Citation**（``citation``）：引用对象与校验器——引用必须指向真实存在的
  切片，编造的引用比没有引用更危险。
- **Retrieval Evaluation**（``evaluation``）：命中率@k 与 MRR。
- **Tool Callbacks**（``callbacks``）：检索缓存（执行前）与结果压缩（执行后）。

依赖方向：本模块只依赖 ``core/llm`` 的错误类型与 ``core/text`` 的分词，不认识
``core/agent``。

导入约定：调用方统一从 ``agent_learning_coach.core.rag`` 导入公开符号。
"""

from agent_learning_coach.core.rag.callbacks import (
    RetrievalCache,
    SearchResultCompressor,
)
from agent_learning_coach.core.rag.chunking import (
    DEFAULT_SEPARATORS,
    Chunker,
    CodeChunker,
    ExerciseChunker,
    FixedSizeChunker,
    PolicyChunker,
    RecursiveChunker,
    chunk_id,
    default_chunking_policy,
)
from agent_learning_coach.core.rag.citation import (
    Citation,
    CitationIssue,
    CitationRegistry,
    CitationReport,
    CitationSet,
    CitationValidator,
)
from agent_learning_coach.core.rag.embedding import (
    EmbeddingClient,
    HashingEmbedder,
    OpenAICompatibleEmbedder,
)
from agent_learning_coach.core.rag.evaluation import (
    RetrievalCase,
    RetrievalMetrics,
    evaluate_retrieval,
)
from agent_learning_coach.core.rag.ingestion import IngestResult, ingest_directory
from agent_learning_coach.core.rag.loader import (
    DirectoryLoader,
    DocumentLoader,
    TextFileLoader,
    document_id,
)
from agent_learning_coach.core.rag.retrieval import VectorRetriever
from agent_learning_coach.core.rag.store import (
    PGVECTOR_EXTENSION_SQL,
    PGVECTOR_INDEX_TEMPLATES,
    PGVECTOR_TABLE_TEMPLATE,
    InMemoryVectorStore,
    PgVectorStore,
    VectorStore,
)
from agent_learning_coach.core.rag.tools import (
    RetrieveKnowledgeTool,
    SearchCourseMaterialTool,
)
from agent_learning_coach.core.rag.types import Chunk, Document, ScoredChunk

__all__ = [
    "DEFAULT_SEPARATORS",
    "PGVECTOR_EXTENSION_SQL",
    "PGVECTOR_INDEX_TEMPLATES",
    "PGVECTOR_TABLE_TEMPLATE",
    "Chunk",
    "Citation",
    "CitationIssue",
    "CitationRegistry",
    "CitationReport",
    "CitationSet",
    "CitationValidator",
    "Chunker",
    "CodeChunker",
    "ExerciseChunker",
    "DirectoryLoader",
    "Document",
    "DocumentLoader",
    "EmbeddingClient",
    "FixedSizeChunker",
    "PolicyChunker",
    "HashingEmbedder",
    "InMemoryVectorStore",
    "IngestResult",
    "OpenAICompatibleEmbedder",
    "PgVectorStore",
    "RecursiveChunker",
    "default_chunking_policy",
    "default_chunking_policy",
    "default_chunking_policy",
    "RetrieveKnowledgeTool",
    "RetrievalCache",
    "RetrievalCase",
    "RetrievalMetrics",
    "ScoredChunk",
    "SearchCourseMaterialTool",
    "SearchResultCompressor",
    "TextFileLoader",
    "VectorRetriever",
    "VectorStore",
    "chunk_id",
    "document_id",
    "evaluate_retrieval",
    "ingest_directory",
]
