# 检索增强生成（RAG）

> 状态：草稿 ｜ 对应 Topic 11（RAG 基础）、Day 11

本文是 RAG 模块的接入说明：**怎么用**、**为什么这样设计**、**边界在哪**。

## 概览

```
DocumentLoader → Chunker → EmbeddingClient → VectorStore
   读成文档        切成检索单元    变成向量        存下来
                                     ↓
                              VectorRetriever
                              （嵌入 → 检索）
```

四段都是协议，每个协议都有两个实现——**一个离线可测，一个能上生产**：

| 协议 | 离线实现 | 生产实现 |
|---|---|---|
| `DocumentLoader` | `TextFileLoader` / `DirectoryLoader` | 同左（PDF 留给 Day 25） |
| `Chunker` | `FixedSizeChunker` / `RecursiveChunker` | 同左 |
| `EmbeddingClient` | `HashingEmbedder` | `OpenAICompatibleEmbedder` |
| `VectorStore` | `InMemoryVectorStore` | `PgVectorStore` |

## 快速上手

### 离线跑通（不联网、不需要数据库）

```python
from agent_learning_coach.core.rag import (
    DirectoryLoader,
    HashingEmbedder,
    InMemoryVectorStore,
    RecursiveChunker,
    VectorRetriever,
)

documents = DirectoryLoader("./materials").load()

chunker = RecursiveChunker(size=120, overlap=20)
chunks = [chunk for document in documents for chunk in chunker.split(document)]

embedder = HashingEmbedder(dimension=256)
store = InMemoryVectorStore()
await store.add(chunks, await embedder.embed([chunk.text for chunk in chunks]))

retriever = VectorRetriever(store, embedder, top_k=5)
hits = await retriever.retrieve("闭包是什么")
```

完整可运行的演示：

```bash
uv run python examples/rag_pipeline.py          # 离线路径
uv run python examples/rag_pipeline.py --pg     # pgvector 路径
```

### 生产路径

```python
from agent_learning_coach.core.rag import (
    OpenAICompatibleEmbedder,
    PgVectorStore,
    VectorRetriever,
)

embedder = OpenAICompatibleEmbedder(
    base_url="https://api.openai.com/v1",
    api_key=os.environ["OPENAI_API_KEY"],
    model="text-embedding-3-small",
    dimension=1536,
)
store = PgVectorStore(os.environ["PG_DSN"], dimension=1536)
await store.ensure_schema()

retriever = VectorRetriever(store, embedder, top_k=5)
```

`dimension` 必须与嵌入模型一致——pgvector 的向量列是定长的，不一致时写不进去。

## PostgreSQL 与 pgvector 配置

### 安装

```bash
brew install postgresql@18 pgvector
brew services start postgresql@18
```

`pgvector` 需要**编译**（没有 bottle），可能要几分钟。

### 建库并启用扩展

```bash
createdb agent_learning_coach
psql -d agent_learning_coach -c "CREATE EXTENSION IF NOT EXISTS vector;"
psql -d agent_learning_coach -c "SELECT extversion FROM pg_extension WHERE extname='vector';"
```

### 表结构

`PgVectorStore.ensure_schema()` 会执行：

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS rag_chunks (
    id            TEXT PRIMARY KEY,
    document_id   TEXT NOT NULL,
    chunk_index   INTEGER NOT NULL,
    text          TEXT NOT NULL,
    source        TEXT NOT NULL,
    metadata      JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding     vector(768) NOT NULL
);

CREATE INDEX IF NOT EXISTS rag_chunks_document_idx ON rag_chunks (document_id);
CREATE INDEX IF NOT EXISTS rag_chunks_embedding_idx
    ON rag_chunks USING hnsw (embedding vector_cosine_ops);
```

**为什么用 HNSW**：HNSW 索引让相似度检索不必全表扫描。实测 `EXPLAIN` 走的是
`Index Scan using rag_chunks_embedding_idx`，不是 `Seq Scan`。

**为什么 `vector_cosine_ops`**：余弦距离与本项目统一的「相似度」语义一致
（见「检索策略」）。

### 数据库怎么被使用

- `PgVectorStore` **每次操作开一条连接**。这是有意的简化：本项目的规模（一个
  学习者的课程材料）用不着连接池，而连接池会引入生命周期管理的问题。
  生产环境应当换成 `psycopg_pool.AsyncConnectionPool`。
- 元数据过滤在 SQL 里完成（`metadata ->> %s = %s`），索引能用上。
- 维度写死在 `vector(n)` 里——pgvector 的向量列是定长的。

### 测试怎么跑

`tests/test_rag.py` 里有两个 pgvector 测试：

```bash
createdb agent_learning_coach_test
psql -d agent_learning_coach_test -c "CREATE EXTENSION IF NOT EXISTS vector;"
uv run --group dev pytest tests/test_rag.py -q
```

**没有数据库时这些测试会跳过**，并给出说明——而不是伪造通过。

## 数据源与采集

加载器只做一件事：把外部材料读成 `Document`。它不切分、不嵌入——那是后面环节
的事。

- `TextFileLoader`：读单个文件。读不了或内容为空时**跳过并记录原因**——
  不让一份坏文件毁掉整批摄取。
- `DirectoryLoader`：读整个目录，按路径排序（顺序稳定），支持排除规则。

**文档 id 由来源路径的 SHA-256 推导**，不是随机 UUID。同一份材料每次摄取都得到
同一个 id——否则重新摄取会产生「同一份材料的两份记录」，而向量库里无法区分它们。

**当前只支持纯文本与 Markdown。** PDF、Word 等富格式的解析留给 Day 25
「Document Ingestion」——那是学习领域侧的摄取工作，与检索基础设施是两件事。

## 切分与索引

### 切分

**切分是检索质量的第一个决定因素**——它决定了「一个能检索到的最小单位」是什么。

| | 定长切分 | 递归切分 |
|---|---|---|
| 边界 | 固定字符数 | 段落 → 句子 → 字符，逐级降级 |
| 优点 | 简单、可预测 | **不截断自然段** |
| 适合 | 结构不明的文本（网页抓取） | 有结构的教材材料 |
| 代价 | **切断语义单元** | 块大小不均匀 |

默认用递归的。这不是因为它更聪明，是因为它保住了语义单元——而语义单元正是
检索要命中的东西。演示里同一段文本的差别肉眼可见：定长切分把「尾递归可以被
优化成」拦腰截断，递归切分落在段落边界。

**块大小按字符，不按 token。** 按 token 更精确，但会让切分器依赖分词器——而切分器
不该知道用的是哪种模型。token 预算由呈现层负责（ADR-0008）。

切得太碎只是多占一点检索空间；**切得太大才是检索质量问题**。

### 嵌入

- `HashingEmbedder`：**确定性、离线、零依赖**。词袋 + 带符号的哈希投影。
  **它不是语义嵌入**——「闭包」和「函数式闭包」可能匹配不上。它的价值是让
  整条流水线可离线跑通：测试、示例、CI 都不需要外部服务。**它是占位级的**，
  生产环境要换成真正的语义嵌入模型。
- `OpenAICompatibleEmbedder`：调用任何 OpenAI 兼容的 `/embeddings` 接口。用
  `httpx` 的 `MockTransport` 可以**离线测试**——生产路径本身也是可验证的。

哈希用 SHA-256 而不是 Python 内置 `hash()`：后者每个进程的随机种子不同，会让
「同样的文本得到同样的向量」这条性质失效——检索结果也就无法复现。

### 索引

切片 id 由「文档 id + 序号」推导，同样可复现。写入时按 id upsert，因此重复摄取
是**覆盖**而不是**追加**。

内存实现用 numpy 算余弦相似度；pgvector 用 `vector` 列 + HNSW 索引。切片 id 是
主键，所以重新摄取不会留下旧切法的孤儿记录。

## 检索策略

`VectorRetriever` **刻意很薄**——只有「嵌入 → 检索」两步。**没有重排、没有查询
改写、没有多路召回**。每加一层就多一个「哪一层先失效」的排查对象；基础检索先
跑对，再谈优化。

### 过滤必须下推

检索器把元数据过滤条件**原样传给存储**，由数据库用索引筛。

在应用层过滤等于把全部向量拉回内存再筛——那是「索引 ≠ 检索」最常见的误用。
检索器也不做二次过滤：它拿不到全部向量，无从筛起。

### 距离与相似度，方向相反

pgvector 的 `<=>` 返回**距离**（越小越近），余弦相似度**越大越近**。两者方向
相反，实现负责换算，因此对外统一暴露「相似度」：

```sql
1 - (embedding <=> %s) AS similarity
```

代价是：**两种实现的绝对值不可跨实现比较**（内存实现直接返回余弦相似度，
pgvector 返回 `1 - 距离`）。但**相对顺序一致**——这才是可替换的前提，
`tests/test_rag.py` 里有一条测试直接验证这一点。

### 检索质量怎么排查

按经验排序，**先看切分，再看召回**：

1. **切分**——最先失效。块太大则噪声多、排名被稀释；块太小则上下文断裂。
   它同时影响召回率和精度，因此是第一个该调的参数。
2. **召回**——相关内容没进 top-k。要么嵌入不匹配，要么被过滤条件挡掉了。
3. **排序**——进来了但排后面。加了重排层能救，但那是后面才引入的复杂度。
4. **生成**——几乎总是最后才出问题。

反过来（先换 embedding 模型）会浪费大量时间，而问题往往根本不在那里。

## 重排与引用

**尚未实现。**

- **重排（rerank）**：当前取回的 top-k 直接交给模型，没有交叉编码器精排。
- **引用**：切片带 `source`（来源路径）与 `metadata`，但还没有把「答案里的哪句话
  来自哪个切片」标注出来。引用需要切片级别的定位，而这依赖更细的粒度——
  目前切片是 120～250 字符的段落级单位。

## 评估指标

**尚未实现。** 检索质量还没有被量化——块大小（120 / 250 字符）目前是经验值，
**没有 recall@k / MRR 数据支撑**。

这部分属于 Day 28 的检索评估 topic。在那之前，只能靠人眼看结果。

要补的最小指标：

| 指标 | 含义 | 用途 |
|---|---|---|
| recall@k | 前 k 条里命中的相关切片比例 | 调 top_k、判断召回是否够 |
| MRR | 相关切片的平均倒数排名 | 调排序、调块大小 |
| 命中率 | 有多少查询能召回到任何相关内容 | 判断切分策略是否把内容切没了 |

## 实现中撞到的坑

### 检索路径没有被测试覆盖，ruff 抓到了真 bug

内存存储的检索里，列表推导引用了未定义的 `vector`（本意是取
`self._vectors[chunk_id]`）。ruff 的 F821 抓到它——但**这类错误只有在运行时
才暴露**，而当时检索路径还没有测试。

教训：**协议实现要按协议逐条写测试**，否则「实现了」和「能跑」是两件事。

### psycopg 的占位符顺序

`search` 的 SQL 里有三个 `%s`：SELECT 里的向量、WHERE 里的过滤值、ORDER BY
里的向量，加上 LIMIT。参数必须**严格按 SQL 里的出现顺序**排列：

```python
params = [vector, *filter_params, vector, top_k]
```

写错时 psycopg 报的是 `the query has 3 placeholders but 2 parameters were
passed`——报错信息直接说了缺几个，但没说缺哪个。

### psycopg 只认 `pgvector.Vector`

`register_vector_async` 注册的是 `vector` 类型的 OID，它让 **`pgvector.Vector`
对象**能被正确序列化。直接传 Python 的 list 会被当成 `double precision[]`：

```
operator does not exist: vector <=> double precision[]
```

写入时这个问题不暴露——pgvector 提供了 `double precision[] → vector` 的赋值
转换，INSERT 能过；但 `<=>` 算子没有对应的隐式解析。**所以「写得进去」不代表
「查得出来」**——必须真的查一次。

修法是把向量包成 `pgvector.Vector`：

```python
from pgvector import Vector

Vector(list(embedding))
```

## 已知边界

- **只有向量检索，没有关键词检索**。纯向量检索在**精确标识符**上不如 BM25。
  混合检索通常优于任一单独方案——这是明确的缺口，不是设计选择。
- **没有重排**。取回的 top-k 直接交给模型。
- **没有查询改写**。多轮对话里的指代（「那个函数的第二个参数」）不会被改写
  成可检索的形式。
- **没有增量更新**。文档改了要重新摄取整份文档。
- **加载器只支持纯文本与 Markdown**。PDF、Word 留给 Day 25。
- **`HashingEmbedder` 只是占位级的**。它捕捉词汇相似，不是语义相似。
- **`PgVectorStore` 每次操作开连接**，不适合高并发。
- **`chromadb` 虽在依赖里，但本模块不用它**（见 ADR-0011）。

## 相关文档

- 决策依据：[ADR-0011](../architecture/decisions/0011-rag-pipeline-and-vector-store.md)
- 学习笔记：[Topic 11 · RAG 基础](../learning/topics/11-rag-basics.md)
- 文章笔记：[记忆与 RAG](../articles/memory-and-rag.md)
- 演示：`examples/rag_pipeline.py`
- 参考实现：`ai-agent-from-scratch` 的 `notebooks/ch05/ch05_rag_and_file_tools.ipynb`
