# Topic: Long-term Memory

> Day 10 ｜ Phase 0 · Agent Engineering Foundation ｜ 主教材：Ch6：6.4 ｜ 外部参考：LangGraph Memory 文档 ｜ 状态：已完成

## 1. Engineering Question

什么信息值得进入长期记忆？如何避免把噪声写进 Memory Extraction？

## 2. Primary Reading

- Book: Ch6：6.4
- Chapter / Section: Ch6 6.4「Long-term memory: Accumulating knowledge across sessions」（6.4.1～6.4.5）
- 核心观点：
  - **6.4.1 长期记忆的结构**：记忆是**有 schema 的结构化记录**，不是自由文本。本教材用 `TaskMemory`（`task_summary` / `approach` / `final_answer` / `is_correct` / `error_analysis`）记录「一次任务是怎么解的、对不对」。
  - **6.4.2 信息抽取用结构化输出**：让模型按固定 schema 产出记忆，而不是解析自由文本——形状稳定，下游的判重与覆盖机制才有依托。
  - **6.4.3 用向量库存起来**：ChromaDB + 嵌入函数，`upsert` 写入、`query` 检索。
  - **6.4.4 TaskMemoryManager**：抽取 → 判重 → 写入，三步。
  - **6.4.5 检索**：`search(query, top_k)` 按向量相似度返回记忆。

> **读法说明**：6.4.1～6.4.5 的代码形态可在参考实现的 `notebooks/ch06/` 快照与 `scratch_agents/memory/long_term.py` 里逐条核对。**6.4 的散文正文在书中，本地不可得**；上面这些是从参考实现的 listings 与代码反推的**读法**，不是书中原文的转述，读时应以书为准。

## 3. Cross References

| 来源 | 核心观点 | 与主教材的关系 |
|---|---|---|
| LangGraph Memory 文档（conceptual overview） | 长期记忆**按 namespace 隔离**——「scoped to any custom namespace, not just within a single thread ID」；记忆分三类：**semantic**（事实，「Facts about a user」）、**episodic**（经历，「Past agent actions」）、**procedural**（规则，「Agent system prompt」）——引 CoALA 论文；组织方式有**两种**：**Profile**（单一持续更新的画像，模型每次拿着旧画像生成新画像）与 **Collection**（文档集合，模型生成新对象覆盖旧对象）；Profile 随规模变大**容易出错**，Collection 的「**召回率更高**」但「模型必须删除或更新已有条目」——「some models may default to **over-inserting** and others may default to **over-updating**」；写记忆有**热路径**与**后台任务**两种：热路径「the process of reasoning about what to save to memory can impact agent latency」，后台则「eliminates latency in the primary application」 | 提供了**分类骨架**（三分法，本项目直接采用）与**关键取舍**：Profile vs Collection。本项目选 Collection（召回率更高、不会因为画像变大而出错），但**更新逻辑用确定性代码而非模型**——正是为了避开文档点出的 over-inserting / over-updating 问题。「热路径 vs 后台」直接决定了记忆写入不在 Agent 循环里 |
| LangChain《Context Engineering》 | 记忆分**write / select** 两步；「Scratchpads help agents solve a task within a given session… but sometimes agents benefit from **remembering things across many sessions**」；选择难在「ensuring that **relevant** memories are selected」；Simon Willison 举过**选错记忆**的例子（ChatGPT 从记忆里取了他的位置，意外注入到请求的图片里） | 印证了「跨会话记忆」与「召回」是两块独立且都不容易的事。选错记忆的代价与写错记忆一样大——因此本项目对召回也设了硬要求：没有匹配就不返回 |
| CoALA（Sumers et al., 2023, *Cognitive Architectures for Language Agents*） | 把人类记忆的三类——语义 / 情节 / 程序——映射为 Agent 的三类记忆 | 给了 6.4.1 的 `TaskMemory` 一个更一般的框架。本项目直接采用这套分类，而不是自创 |
| 参考实现的缺陷（实测） | `_is_duplicate` 捕获**所有**异常并返回 `False`——判重失败时**静默放行**；`_extract_memory` 同样捕获所有异常返回 `None` 并 `print` 到 stdout；记忆是**关于任务**的（GAIA 解题记录），不是关于学习者的；嵌入用 `OpenAIEmbeddingFunction`，与本项目厂商无关的立场冲突 | 三处都指向同一个问题：**抽取与判重的失败被静默吞掉**，于是噪声被当成正常数据写进向量库。本项目的对策是相反方向：失败明确抛出（调用方决定重试还是丢弃），准入与覆盖由**确定性代码**做 |
| 论文 / 研究资料 | Reflexion（Shinn et al., 2023）与 Generative Agents（Park et al., 2023）证明「跨轮次保存自己产生的状态」是长时程智能体的基础；Generative Agents 的 *memory stream* 给每条记忆打**可检索性分数**与**重要度**，并**定期反思**合成更高层的记忆 | 说明「记忆不只是检索，也包括**整理**」——本项目的覆盖机制是最初级的整理（按唯一键取代），定期反思属后续议题 |

## 4. Compare and Challenge

- **不同资料有哪些一致观点？**
  - **记忆必须有 schema**（6.4.1 的 `TaskMemory`、6.4.2 的结构化输出、LangGraph 的 namespace + key）。
  - **跨会话隔离是必需的**（LangGraph 的 namespace、CoALA 的 subject）。
  - **写入与召回是两件独立的事**（LangChain 的 write / select）。
  - **记忆写入应当异步**（LangGraph 的 hot path vs background）。

- **哪些观点存在分歧？**
  - **Profile vs Collection**：LangGraph 文档两者都给了，并列出各自的代价。本项目选 **Collection**（更小粒度、召回率更高、不会随画像变大而出错）。
  - **谁来决定「重复」**：参考实现用**模型**判重（拿新记忆去向量检索 top-3，再让模型判断是否重复）；本项目用**确定性规则**（同主体+同类型+同类别的同内容）。
  - **判重失败怎么办**：参考实现**静默放行**；本项目**确定性去重根本不失败**——只有模型抽取会失败，而抽取失败明确抛出。

- **分歧来自应用场景、设计目标还是技术演进？**
  - **Profile vs Collection 来自记忆的粒度**：任务记忆（GAIA）是「一次解题一条记录」，天然是 Collection；学习者记忆是「一个学习者的画像」，天然像 Profile。但学习者记忆里「弱点」是**多条**的（「闭包」「递归」「装饰器」），所以还是 Collection——用 scope 区分。
  - **确定性去重来自设计目标**：本项目 ADR-0001 的 KISS 与「失败即异常」。把「是否重复」交给模型，等于把系统的确定性交给一个不可复现的组件；而「重复」在结构化记忆里是可以用键精确定义的。
  - **不用向量检索来自厂商无关的立场**：参考实现的嵌入绑定了 OpenAI API。本项目是厂商无关的（ADR-0002），而学习者记忆的规模根本不需要嵌入。

- **哪些结论缺少充分证据？**
  - **「Collection 的召回率更高」**——LangGraph 文档这么写，但没有公开的对照数据。而且它是在「用模型做更新」的语境下说的；本项目的更新是确定性的，结论未必成立。
  - **「关键词相关度对学习者记忆够用」**——没有对照实验与嵌入检索比对。样本小、事实短、类别明确时大概率够用，但「大概率」不是「验证过」。
  - **「三条准入条件能过滤掉大部分噪声」**——没有量化。噪声的形态随场景变，本项目的三条来自「学习教练」这个具体场景。

- **哪些观点不适用于当前项目？**
  - **定期反思与记忆合成**（Generative Agents 的 reflection）：学习教练的长期记忆是关于**学习者**的，不是智能体自己的经验总结。现在做没有依据。
  - **Procedural 记忆的完整形态**（LangGraph 的 `update_instructions` 节点改写自己的系统提示）：那是多智能体研究系统的做法；本项目的提示词是开发者写的，不是 Agent 自己改的。
  - **Episode 记忆的完整形态**（few-shot 示例库）：本项目目前没有从历史里生成示例的机制。

- **本项目的立场小结**：采纳「三分法分类」「namespace 隔离」「schema 化抽取」「写入异步化」四条；与参考实现在**失败语义**（静默吞 → 明确抛出）、**判重方式**（模型 → 确定性规则）、**存储选型**（向量库 → 结构化 + 关键词）上做本项目特有的替换；与 LangGraph 文档在 **Profile vs Collection** 上选 Collection 并写明代价。

## 5. Engineering Practice

### 实现了什么？

**1. 记忆模型**（`core/memory/types.py`）

- `Memory`：类型（semantic / episodic / procedural）、类别、归属主体、作用域、
  内容、置信度、时间戳。**不可变**——更新就是用同键的新记忆取代它。
- 唯一键 `(主体, 类型, 类别, 作用域)`。
- `LEARNER_MEMORY_KINDS`：本项目当前的类别词表。
- `MemoryPolicy`：**确定性**准入策略（内容非空、置信度达标、长度不超标、
  类别在词表内）。
- `LearnerProfile`：记忆的一种视图，按类别聚合成可读画像，并能渲染成提示词文本。

**2. 存储**（`core/memory/store.py`）

- `MemoryStore` 协议 + `InMemoryMemoryStore`。
- `add` 按唯一键 upsert：保留原 id 与创建时刻，更新内容与更新时刻。
- `find` 按主体/类型/类别筛选。
- `search` 关键词相关度排序——**不需要嵌入服务**，离线可测、结果可解释。
- 中文按**二元组**切分。

**3. 抽取**（`core/memory/extraction.py`）

- `MemoryExtractor` 协议 + `LLMMemoryExtractor`（主教材 6.4.2 的做法）。
- 提示词明确要求「没有值得记的就返回空列表」——**空列表是被期望的答案**。
- `memory_type` 与 `kind` 用宽松的字符串而非枚举：模型给出一个不认识的类别时
  **不让整批抽取失败**，坏的那条降级，其余照常。

**4. 管理**（`core/memory/manager.py`）

- `capture`：抽取 → 准入过滤 → 同内容去重 → 按唯一键 upsert。
- `recall`：按相关度或按类别召回。
- `profile`：生成画像。
- 抽取失败**明确抛出**，不静默吞掉。

**5. 注入**

记忆的召回与应用层的编排由应用层负责——运行时保持领域无关：

```python
profile = await manager.profile(subject=learner_id)
instructions = [*base, *([profile.to_prompt()] if profile.to_prompt() else [])]
```

### 为什么这样设计？

**为什么用 Collection 而不是 Profile？** LangGraph 文档把两者都列了出来：Profile
随规模变大容易出错，Collection 召回率更高但模型必须自己删除/更新已有条目。本项目
选 Collection——学习者的「弱点」天然是多条（闭包、递归、装饰器），用 `scope` 区分。

**但更新逻辑不用模型。** 文档同时点出了 Collection 的代价：「some models may
default to over-inserting and others may default to over-updating」。本项目把更新
做成**确定性**的：唯一键 upsert + 同内容去重。模型只负责「说出候选」，「写不写、
覆盖谁」由代码决定。

**为什么准入不用模型判断？** 「这条算不算噪声」交给模型判断，等于把系统的确定性
交给一个不可复现的组件。而且抽取噪声的主要来源不是「模型判断错了」，而是
「模型总会给你点什么」——所以第一道闸是把「没有」写成一个被鼓励的答案。

**为什么不用向量检索？** 学习者的长期记忆是结构化的，绝大多数召回是**筛选**
而非相似度检索。确定性关键词匹配不需要嵌入服务（参考实现因此绑定了 OpenAI 的
embedding API，与本项目厂商无关的立场冲突）、完全离线可测、结果可解释。向量
检索留给 Day 11～12 的 RAG。

**为什么记忆写入不在热路径？** LangGraph 文档指出在热路径里写记忆会增加延迟、
让 Agent 分心、影响主任务质量。本项目让应用层决定时机，`MemoryManager` 只提供
能力。

### 有哪些替代方案？

| 方案 | 未采用的理由 |
|---|---|
| 向量库 + 嵌入检索（参考实现） | 绑定嵌入服务；学习者记忆规模小且结构化，绝大多数召回是筛选而非相似度检索；无法离线测试 |
| Profile（单一持续更新的画像） | 画像变大后模型更新它容易出错；「弱点」天然是多条，单一画像表达不了 |
| 用模型判重（参考实现） | 判重结果不可复现；「重复」在结构化记忆里可以用键精确定义 |
| 判重失败时静默放行（参考实现） | 噪声会被当成正常数据写进去，且没有人知道 |
| 抽取失败时静默返回空（参考实现） | 失败是需要被看见的事实；静默会让「这个学习者怎么什么都没有」无从排查 |
| 枚举约束 `memory_type` / `kind` | 一条坏数据会让整批抽取失败 |
| 记忆写入放在 Agent 循环里 | 增加延迟、让 Agent 分心 |

### 测试结果如何？

- 新增 `tests/test_memory.py`（37 个用例）。
- 全量：**420 passed**（原 383 + 37）。
- 覆盖率：`core/memory/` 五个模块均 **100%**（行 + 分支）。
- **变异验证**：

  | 变异 | 捕获的测试数 |
  |---|---|
  | 不做准入过滤 | 4 |
  | 不做内容去重 | 1 |
  | 同键不覆盖（并存两条） | 2 |
  | 准入不看类别 | 1 |

### 实践结果是否支持阅读材料中的观点？

**支持，并有三处替换与一处限定。**

- 6.4.1「记忆的结构」——得到**支持**，但内容换了：主教材记的是「任务怎么解的」，
  本项目记的是「学习者是谁」。分类沿用 CoALA 三分法（LangGraph 文档同款），
  领域细分放在 `kind`。
- 6.4.2「结构化输出抽取」——得到**完全支持**，并被本项目加了一层：结构化解决
  了「形状乱」，但没解决「该不该记」，所以后面还有准入策略与去重覆盖。
- 6.4.3「向量库存放」——得到**限定**：本项目用结构化筛选 + 关键词相关度。
  理由是学习者记忆的形态与 RAG 语料不同，手段不该照搬。
- 6.4.4「抽取 → 判重 → 写入」——得到**支持但换实现**：判重从「模型判断」改成
  「确定性规则」；失败语义从「静默」改成「明确抛出」。
- 6.4.5「检索」——得到**限定**：本项目的检索是确定性的关键词相关度，不是向量
  相似度。效果上没有对照实验。

## 6. Reflection

1. **今天最重要的知识是什么？**
   **「没有值得记的东西」必须是一个被期望的答案，否则模型总会给你点什么。**
   这是抽取噪声的主要来源——不是模型判断错了，是**问题本身没给「不」留出口**。
   我原本以为噪声来自「模型不够准」，读完 LangGraph 文档里「some models may
   default to over-inserting」才发现：噪声来自**机制缺一个分支**。

   第二条同样重要：**「重复」在结构化记忆里是可以精确定义的**，不需要模型判断。
   唯一键一旦定下来，「这条是不是重复的」就是查表，不是判断。把能查表的事交给
   模型，是把系统的确定性交给了一个不可复现的组件。

2. **哪个观点改变或修正了原来的理解？**
   原本以为长期记忆就是「向量库 + 嵌入检索」，看到 LangGraph 文档把 Profile 与
   Collection 并列、并且指出「Collection 的召回率更高但模型必须自己删除或更新」
   之后，理解变了：**长期记忆的核心不是检索能力，是更新语义**。检索错了召回噪声，
   更新错了产生矛盾记忆——后者更隐蔽，因为它看起来「有数据」，只是数据自相矛盾。

   另一处修正：原以为「记忆系统」和「RAG 系统」是同一类东西。读完才分清——
   记忆是**关于主体的事实**，语料小、结构化、检索以筛选为主；RAG 是**从非结构化
   文档里找相关片段**，语料大、无结构、检索以相似度为主。**手段照搬会同时失去
   两边的优势。**

3. **当前实现还有哪些局限？**
   - **没有遗忘与时效性**。记忆只增不删（除同键覆盖）；三个月前的偏好和今天
     的权重相同。
   - **没有语义层的冲突消解**。同键覆盖是「新的赢」；两条并不互斥的事实用了
     同一个作用域时会丢掉一条。
   - **没有跨事实关联**。「这个弱点由哪个目标造成」答不了。
   - **关键词相关度比向量检索粗**，没有对照实验。
   - **抽取依赖模型质量**，而模型质量没有评估（属 Day 14/60）。
   - **没有并发写保护**。两个进程同时抽取可能互相覆盖。
   - **记忆写入时机由应用层决定，但应用层还不存在**——目前只有示例与测试在用。

## 7. Output

- Code / PR:
  - `src/agent_learning_coach/core/memory/types.py`（新增：`Memory`、`MemoryPolicy`、
    `LearnerProfile`、类别词表）
  - `src/agent_learning_coach/core/memory/store.py`（新增：`MemoryStore` 协议、
    `InMemoryMemoryStore`、中文二元组分词）
  - `src/agent_learning_coach/core/memory/extraction.py`（新增：`MemoryExtractor`
    协议、`LLMMemoryExtractor`、抽取提示词）
  - `src/agent_learning_coach/core/memory/manager.py`（新增：`MemoryManager`）
  - `src/agent_learning_coach/core/memory/__init__.py`（新增：导出公开符号）
- Tests: `tests/test_memory.py`（37 个用例）
- ADR: `docs/architecture/decisions/0010-long-term-memory-admission-and-overwrite.md`
- Article Notes: `docs/articles/memory-and-rag.md`、`docs/engineering/memory.md`
- Unresolved Questions:
  - 遗忘与时效性怎么设计？衰减权重、有效期字段，还是交给上层？
  - 语义冲突怎么消解？「新的赢」之外，是否需要「两条并存但标注重复」？
  - 记忆之间要不要建引用关系（「这个弱点由哪个目标造成」）？
  - 抽取质量怎么评估？模型抽错了、漏了、抽重了，怎么发现？（属 Day 14/60）
  - 关键词相关度够不够用？要不要在真实语料上与嵌入检索做一次对照？
  - 类别词表由谁维护？Phase 1 的学习领域层接管时，怎么平滑迁移已有的记忆？
