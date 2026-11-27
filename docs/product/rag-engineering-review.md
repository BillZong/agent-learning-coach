# RAG 工程回顾（Day 27–30）

> 日期：2026-11-27 ｜ 范围：Week 5 · RAG Engineering（Day 27–30）
> 依据：Plan.md（Week 5）、OKR.md（KR2.4 / KR3.x）、ADR-0028～0032、仓库现状

**这次回顾回答三个问题**：既定的计划兑现了多少？遗留有多少？现在能关掉哪些？

**结论速览**

| 维度 | 结论 |
| ---- | ---- |
| Plan.md Day 27–30 | **36 项全部兑现**（复盘前 35 项；唯一缺口「研究 Reranking」在本次补上，ADR-0032） |
| OKR | KR2.4 的「课程资料导入 + RAG」达成；「回答能提供 Citation」**属于 Day 31**，未达成 |
| ADR | ADR-0028～0031 已接受；本次新增 ADR-0032 |
| 遗留 | 共 27 条：**已关闭 10**（含本次关掉的 9 条 + 复盘新发现的 1 个 bug）；**有前置条件 11**；**需要决策 6** |
| 复盘新发现 | **1 个真 bug**：检索缓存的键漏了 `top_k`（同查询不同 top_k 会串味） |

**一句话总结**：**计划兑现得干净，遗留多但「性质」清楚——二十七条里，七成挂着的是
「等真实语料 / 真嵌入 / 真用户 / 后续主题」，不是「没想清楚」。** 本次关掉的 9 条都属于「不依赖外部条件」的那
一类；复盘还顺手逮到一个缓存串味的 bug（同一查询换 `top_k`，第二次直接拿回第一次的
结果）。

---

## 1. Plan.md 对照（Day 27–30）

### Day 27 · Document Ingestion（8 项，全部兑现）

| 计划项 | 状态 | 证据 |
| ------ | ---- | ---- |
| 研究 Document Ingestion | ✅ | [ADR-0028](../architecture/decisions/0028-document-ingestion.md)、[Topic 27](../learning/topics/27-document-ingestion.md) |
| 研究常见文档解析方式 | ✅ | `core/rag/loader.py`：文本 / PDF（PyMuPDF）/ Notebook（ipynb） |
| 定义 Course Document | ✅ | `core/learning/documents.py`：`CourseDocument`、`DocumentKind`、`document_id`、`infer_kind` |
| 实现 Document Loader | ✅ | `TextFileLoader` / `PdfFileLoader` / `NotebookFileLoader` / `DirectoryLoader` / `load_source` |
| 实现文档元数据 | ✅ | `Document.metadata` + **显式声明种类**（扩展名推断不出「习题」） |
| 实现文档持久化 | ✅ | `CourseDocumentRepository`（协议）+ 内存实现 + codec + `migrations/0002_course_documents.sql` |
| 实现基础错误处理 | ✅ | 部分成功语义：`IngestionIssue` / `IngestionFailure` / `skipped` |
| 交付 Course Document Ingestion | ✅ | ADR-0028 + `examples/document_ingestion.py`（20 个用例） |

### Day 28 · Chunking Strategy（9 项，全部兑现）

| 计划项 | 状态 | 证据 |
| ------ | ---- | ---- |
| 研究 Chunking Strategy | ✅ | [ADR-0029](../architecture/decisions/0029-chunking-strategy.md) |
| 对比固定长度和语义切分 | ✅ | ADR-0029 的对比表 |
| 研究 Chunk Metadata | ✅ | ADR-0029 |
| 实现 Fixed-size Chunking | ✅ | `FixedSizeChunker` |
| 实现 Recursive Chunking | ✅ | `RecursiveChunker` |
| 保存 Chunk Metadata | ✅ | `Chunk.metadata` |
| 对比不同策略的检索结果 | ✅（Day 30 补上） | `compare_strategies` + `examples/retrieval_report.py` |
| 建立 Chunking Tests | ✅ | **不变量**测试（讲义不切断句子、代码不切断顶层定义、习题不切断一道题、切片不超上限、id 可复现） |
| 交付 Chunking Module | ✅ | ADR-0029 |

### Day 29 · Embedding & Retrieval（9 项，全部兑现）

| 计划项 | 状态 | 证据 |
| ------ | ---- | ---- |
| 阅读 Embedding 官方文档 | ✅ | Topic 29 交叉参考 |
| 阅读 pgvector 文档 | ✅ | Topic 29；ADR-0030 |
| 研究向量相似度检索 | ✅ | ADR-0030 |
| 实现 Embedding Adapter | ✅ | `EmbeddingClient` 协议 + `HashingEmbedder`（占位）+ `OpenAICompatibleEmbedder` |
| 实现 Vector Store | ✅ | `InMemoryVectorStore` + `PgVectorStore`（真实 PostgreSQL 18 + pgvector 验证过） |
| 实现 Vector Retrieval | ✅ | `VectorRetriever`（含 Day 30 的按来源去重） |
| 实现 Metadata Filtering | ✅ | 等值 / 序列；**本次复盘补上**取反与 all-of（见 §4） |
| 编写 Retrieval Tests | ✅ | `tests/test_rag.py` + `tests/test_pgvector_store.py` |
| 交付 Vector Retrieval Module | ✅ | [ADR-0030](../architecture/decisions/0030-retrieval-filtering-and-index.md) |

### Day 30 · Retrieval Evaluation（10 项，全部兑现）

| 计划项 | 状态 | 证据 |
| ------ | ---- | ---- |
| 研究 RAG Evaluation | ✅ | [ADR-0031](../architecture/decisions/0031-retrieval-evaluation.md) |
| 研究 Recall、Precision、MRR | ✅ | Recall / MRR 做了；**Precision 研究后明确不做**（切片粒度上的 Precision 会误导），理由在 ADR-0031 |
| **研究 Reranking** | ✅（**本次复盘补上**） | **[ADR-0032](../architecture/decisions/0032-reranking-and-query-rewriting.md)**——这是复盘前唯一没兑现的计划项 |
| 建立 Retrieval Evaluation Dataset | ✅ | `evals/retrieval.json`（11 条）+ `evals/corpus/`（6 份材料） |
| 实现 Recall@K | ✅ | 召回@来源 + **找全率@5**（严格口径） |
| 实现 MRR | ✅ | `RetrievalMetrics.mrr` |
| 对比不同 Chunking Strategy | ✅ | `compare_strategies` |
| 记录检索失败案例 | ✅ | `RetrievalCaseResult`（期望 / 实际 / 排名 / 召回 / **被带回来的负例**） |
| 根据结果调整 Retrieval | ✅ | 按来源去重：召回 0.77 → **1.00**，hit@3 0.73 → **1.00** |
| 交付 Retrieval Evaluation Report | ✅ | `examples/retrieval_report.py`（四段演示） |

## 2. OKR 对照

| KR | 内容 | 状态 |
| -- | ---- | ---- |
| **KR2.4** | 支持课程资料导入和 RAG | ✅ 导入（Day 27）+ 切分（Day 28）+ 嵌入与检索（Day 29）+ 评估（Day 30） |
| **KR2.4** | 回答能够提供 Citation | ⏳ **Day 31**（`core/rag/citation.py` 已有底子：引用模型、引用校验、引用注册表） |
| **KR2.4** | Direct Teaching / Socratic Teaching | ⏳ Day 33 |
| **KR2.4** | Learning Session 持久化 / Tutor 用学习者上下文 | ⏳ Day 32、35 |
| **KR3.1** | 主教材阅读 | ⚠️ Week 5 无主教材章节（工程阶段），Ch1–Ch8 的重点阅读在 Phase 0 完成 |
| **KR3.2** | 多来源交叉学习 | ✅ 每天的 Topic 笔记都有交叉参考表（含 ADR、论文、官方文档） |
| **KR3.3** | 知识资产（≥10 主题笔记 / 架构文档 / ADR 集） | ✅ 主题笔记 66 篇、ADR 32 条、架构文档在册 |
| **KR3.4** | 技术文章 | ⏳ `docs/articles/` 四篇在推进中 |
| **KR5 里程碑** | Week 6 = AI Tutor（O1、O2） | ⏳ 未到期（Day 32–33） |

**OKR 侧的提醒**：OKR 的里程碑表把 **Week 6** 设为「AI Tutor」，Week 5（RAG）在
里程碑口径里不是独立验收点——但 Phase 2 的**阶段交付物**里有「课程资料管理能力」与
「RAG Pipeline」两项，现在都还挂着未勾选，因为「引用与检索质量评估」要到 Day 31 才
算齐。**Day 31 收尾时应该勾掉 Phase 2 的前两项交付物。**

## 3. ADR 对照

| ADR | 主题 | 状态 | 缺口 |
| --- | ---- | ---- | ---- |
| 0028 | 文档摄取：格式、部分成功、幂等 | 已接受 | 知识点自动关联待 Topic 46；SQL 适配器待驱动 |
| 0029 | 切分策略：按材料类型选，参数由不变量锁定 | 已接受 | **参数取值待真实语料**；非 Python 代码结构识别待决策 |
| 0030 | 检索过滤、top-k、阈值归属、索引方案 | 已接受 | 阈值与 HNSW 参数待真实语料；**取反与 all-of 已由本次复盘补上** |
| 0031 | 检索评估：评估集、召回、据此调整 | 已接受 | 真实语料与真嵌入待有；nDCG 明确不做 |
| 0032 | 重排、查询改写、多路召回：研究过了，现在不做 | 已接受（新增） | **上下文增强还没做**（候选） |

## 4. 遗留问题台账（Phase 2 · RAG）

**共 27 条**。分三类：**已关闭**（不用再管）、**有前置条件**（等条件成熟自动复评）、
**需要决策**（等人拍板）。

### 4.1 已关闭（10 条）

| # | 问题 | 怎么关的 |
| - | ---- | -------- |
| 1 | **检索缓存的键漏了 `top_k`**（复盘新发现的 bug） | 键改成「工具名 + 全部参数」——**结果由哪些参数决定，键就得包含哪些** |
| 2 | 过滤的取反与 all-of 语义 | `{"not": 值}` / `{"all": [值, ...]}`；内存与 pgvector 两侧语义对齐（集成测试同款用例跑两遍） |
| 3 | 驱动选型（`psycopg` 还是 `asyncpg`）与是否进 `pyproject` | 已定 `psycopg[binary]`，已进主依赖（连带 `pgvector`） |
| 4 | 重排 / 查询改写 / 多路召回要不要做 | 研究做掉了，决策与判据见 ADR-0032 |
| 5 | Recall 的多来源阈值该多严 | 两个口径都报：宽松的「命中率」与严格的「找全率@5」 |
| 6 | 合成查询的偏差怎么量化 | 评估集加**负例**，指标是**假阳性率**——当前 **0.50**（一半负例被带回来） |
| 7 | 分级相关性（nDCG）值不值得做 | 不做：分级标注成本远高于来源级二值判断，而本项目连真实查询都还没有 |
| 8 | pgvector 适配器注册失败的其他原因 | 三种失败分开说：连不上数据库（**不回显口令**）、扩展未安装、扩展在但注册失败 |
| 9 | 「按类型选切分策略真的更好吗」 | **已回答**：Day 30 的结论是「**测不出来**」（占位嵌入器偏向大块，语料只有六份）——既没说更好也没说更差 |
| 10 | 超限时怎么切才能既不超上限又不破坏结构 | **上限是硬不变量，破坏结构有时不可避免**——但**降级要可见**：被迫拆开的单元，其切片带 `oversized_unit` 标记 |

### 4.2 有前置条件（11 条）

| # | 问题 | 等什么 | 复评时点 |
| - | ---- | ------ | -------- |
| 11 | 真实语料与真嵌入的评估时机 | 真实材料 + 可用的嵌入服务 | 有了凭证就做；**那时才谈得上「按类型选是否更好」** |
| 12 | 切分参数取值（块大小 / 重叠） | 真实语料 + 真嵌入 | 同上 |
| 13 | 阈值的取值 | 真嵌入（占位嵌入器的分数分布没有意义） | 同上 |
| 14 | HNSW 参数（m / ef_construction / ef_search） | 真实语料 | 语料规模定下来后 |
| 15 | 真实课程库到底多大（索引方案选型前提） | 真实数据 | Phase 5 之前 |
| 16 | 知识点自动关联（材料 ↔ 知识点） | Topic 46 的模型打分 | Day 46 |
| 17 | 材料的历史版本要不要留 | ——（等**真实使用**） | 导入真实课程数据时 |
| 18 | 正文存两遍的成本 | 材料量上来 | Phase 5 之前 |
| 19 | SQL 适配器（ADR-0026 的学习领域仓储） | 驱动已定，**是工作量不是前置条件** | Phase 5 之前 |
| 20 | 按 token 计预算 | 呈现层的上下文裁剪策略（Day 32） | Day 32 之后 |
| 21 | 线上日志评估 | 真实用户 | 有真实用户时 |

### 4.3 需要决策（6 条）

| # | 问题 | 我的建议 | 需要谁定 |
| - | ---- | -------- | -------- |
| 22 | 「一份材料进两门课」是否需要（材料与课程材料的关系要不要拆开） | 先不拆；真出现「一份材料两门课」再拆（现在拆是提前设计） | 项目负责人 |
| 23 | Word 与其它富格式（`python-docx` 提到主依赖的时机） | 有真实 `.docx` 教材时再加；加依赖要有真实需求 | 项目负责人 |
| 24 | 扫描件的 OCR | 不做：依赖重、效果不可控，且没有扫描件需求 | 项目负责人 |
| 25 | 从 URL 导入 | 先不做：安全面（SSRF、任意站点抓取）比收益大 | 项目负责人 |
| 26 | 非 Python 代码的结构识别（要不要引入 tree-sitter） | 不引入：换正则即可；真遇到多语言再评估依赖成本 | 项目负责人 |
| 27 | 语义切分（模型判断边界）值不值 | 不做：与 ADR-0029 的结论一致，等真嵌入与真语料 | 项目负责人 |

### 4.4 本次复盘关掉的 9 条是怎么挑的

**判据**：**不依赖外部条件、不需要产品判断、且改动可验证**。

- 缓存键（bug）——**复盘读代码时发现的**，不是遗留清单里的；
- 过滤语义、Recall 口径、假阳性率、pgvector 错误分流——**Day 29/30 的遗留**，机制清楚、
  可离线验证；
- 驱动选型——**其实早就解决了**（Day 30 的 pgvector 工作顺手定了），只是没回头销账；
- 重排 / 查询改写 / 多路召回——**计划项缺口**，补的是研究，结论是「不做」，成本是一次
  认真的阅读与一份 ADR；
- 超限单元的降级可见——**Day 28 的遗留**，答案是「上限是硬不变量 + 降级要可见」；
- 「按类型选是否更好」——**答案已经有了**（Day 30 的「测不出来」），只是没销账；
- nDCG——**决策一条**：不做。

**剩下的 17 条为什么现在不关**：11 条**等条件**（真实语料 / 真嵌入 / 真用户 / 后续主题），
6 条**要人拍板**（产品与依赖取舍）。**硬做只会得到「假装完成」**——比如在没有真嵌入
的情况下给阈值定一个数，那不是结论，是猜。

## 5. 复盘发现的三个问题（已修）

1. **缓存键漏了 `top_k`**——`RetrievalCache` 的键只含「工具名 + 查询 + 过滤」。同一个
   查询换 `top_k`，第二次直接拿回第一次的结果：**返回条数是错的**。这与缓存自己的
   注释（「少了过滤条件会串味——那是错答案」）是同一类错误，只是漏了另一个维度。
   修法：**键 = 工具名 + 全部参数**（参数规范化后拼接）。
2. **过滤值是字典时静默失配**——写错的操作符名（`{"neq": ...}`）会被当成字面字典值
   去匹配，**一条也搜不出来**，而且没有任何提示。修法：**映射值一律按操作符解释，
   写错就报错**。
3. **SQL 的 NULL 陷阱**——`NOT (metadata ->> 'k' = 'v')` 在键不存在时是 `NOT NULL`，
   行会被丢掉；而内存语义是「键不存在 → 不匹配 → 取反 → 保留」。修法：标量取反用
   `NOT COALESCE(..., FALSE)`，并**两侧同款用例各跑一遍**。

## 6. 仍然待 Day 31 及之后

- **Day 31**：引用与溯源（Citation & Grounding）+ Week 5 Review——`core/rag/citation.py`
  已有底子（引用模型、引用校验、引用注册表），Day 31 要做的是把它接进教学链路，
  并补「引用准确性测试」。
- **Day 32–33**：上下文工程与直接教学（Phase 2 的主线）。
- **Day 33 之后**：Phase 2 的阶段验收报告（`docs/product/phase-2-acceptance.md`）与
  Phase 2 交付物勾选。

## 相关文档

- [路线图](./roadmap.md) ｜ [遗留问题台账（Phase 0–1）](./open-issues.md) ｜
  [MVP 定义](./mvp.md)
- ADR：[0028](../architecture/decisions/0028-document-ingestion.md)、
  [0029](../architecture/decisions/0029-chunking-strategy.md)、
  [0030](../architecture/decisions/0030-retrieval-filtering-and-index.md)、
  [0031](../architecture/decisions/0031-retrieval-evaluation.md)、
  [0032](../architecture/decisions/0032-reranking-and-query-rewriting.md)
- 笔记：[Topic 27](../learning/topics/27-document-ingestion.md)、
  [Topic 28](../learning/topics/28-chunking-strategy.md)、
  [Topic 29](../learning/topics/29-embedding-and-retrieval.md)、
  [Topic 30](../learning/topics/30-retrieval-evaluation.md)
- 工程文档：[rag.md](../engineering/rag.md)
