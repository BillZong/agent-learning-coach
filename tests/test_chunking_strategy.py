"""切分策略的测试（Day 28）。

这个文件锁的是**不变量**，不是数字：

- **讲义不切断句子**、**代码不切断顶层定义**、**习题不切断一道题**；
- **切片不超过上限**、**切片 id 可复现**、**元数据被保留**；
- **切分是幂等的**：同一份文档切两次结果一样。

数字会随模型与语料变化，不变量不会——锁数字只会让改动变成「改测试」。
"""

from __future__ import annotations

from agent_learning_coach.core.application import to_rag_document
from agent_learning_coach.core.learning import CourseDocument, CourseId, KnowledgePointId
from agent_learning_coach.core.rag import (
    CodeChunker,
    Document,
    ExerciseChunker,
    FixedSizeChunker,
    PolicyChunker,
    RecursiveChunker,
    default_chunking_policy,
)

NOTES = """# 闭包

闭包是一个函数加上它定义时所处的环境。即使函数返回了，变量绑定仍然保留。

装饰器就是利用了这一点：它把被装饰的函数包一层，返回新的函数。

装饰器的实现通常写成三层嵌套。每一层各自捕获不同的变量，因此闭包之间互不影响。
"""


CODE = '''"""示例模块。"""

import os


def helper(value):
    return value * 2


class Calculator:
    def add(self, a, b):
        return a + b

    def sub(self, a, b):
        return a - b


def main():
    print(helper(os.getpid()))
'''


EXERCISES = """1. 闭包是什么？请用自己的话解释。

答案：闭包记住了创建时的作用域。

2. 下面这段代码的输出是什么？

```python
def outer():
    x = 1
    def inner():
        return x
    return inner
print(inner())
```

3. 装饰器是怎么利用闭包的？
"""


def _document(text: str, kind: str = "notes", document_id: str = "doc-1") -> Document:
    """构造一份带种类的文档。

    Args:
        text: 正文。
        kind: 材料种类。
        document_id: 文档标识。

    Returns:
        文档。
    """
    return Document(
        id=document_id, text=text, source=f"/tmp/{document_id}.md", metadata={"kind": kind}
    )


# --- 按类型选策略 -------------------------------------------------------


def test_policy_picks_by_kind() -> None:
    """种类决定用哪个切分器。"""
    policy = default_chunking_policy()

    notes = policy.split(_document(NOTES, kind="notes"))
    code = policy.split(_document(CODE, kind="code"))
    exercises = policy.split(_document(EXERCISES, kind="exercise"))

    assert len(notes) == 1
    assert len(code) > 1
    assert len(exercises) > 1


def test_policy_falls_back_when_kind_is_unknown() -> None:
    """种类不认识就退回默认策略。"""
    policy = default_chunking_policy()

    chunks = policy.split(_document(NOTES, kind="mystery"))

    assert len(chunks) == 1


def test_policy_without_metadata_uses_default() -> None:
    """没有种类元数据也退回默认策略。"""
    policy = default_chunking_policy()
    document = Document(id="d", text=NOTES, source="/tmp/d.md")

    assert len(policy.split(document)) == 1


# --- 不变量：讲义 -------------------------------------------------------


def test_notes_keep_sentences_whole() -> None:
    """讲义不切断句子——每个切片都以完整句号或标题结尾。"""
    policy = default_chunking_policy()

    for chunk in policy.split(_document(NOTES, kind="notes")):
        assert chunk.text.rstrip().endswith(("。", "：", "\n", "）", "」"))


def test_chunks_never_exceed_the_limit() -> None:
    """切片不超过上限——包括代码与习题。"""
    policy = default_chunking_policy()

    for kind, text in (("notes", NOTES), ("code", CODE), ("exercise", EXERCISES)):
        for chunk in policy.split(_document(text, kind=kind)):
            assert len(chunk.text) <= 1600


def test_chunk_ids_are_reproducible() -> None:
    """同一份文档的第 N 块永远得到同一标识。"""
    policy = default_chunking_policy()

    first = policy.split(_document(CODE, kind="code"))
    second = policy.split(_document(CODE, kind="code"))

    assert [chunk.id for chunk in first] == [chunk.id for chunk in second]


def test_chunking_is_idempotent() -> None:
    """同一份文档切两次结果一样。"""
    policy = default_chunking_policy()

    first = policy.split(_document(NOTES, kind="notes"))
    second = policy.split(_document(NOTES, kind="notes"))

    assert [chunk.text for chunk in first] == [chunk.text for chunk in second]


def test_metadata_is_preserved() -> None:
    """文档的元数据被带进切片——检索要靠它过滤。"""
    policy = default_chunking_policy()
    document = _document(CODE, kind="code")
    document = Document(
        id=document.id,
        text=document.text,
        source=document.source,
        metadata={**document.metadata, "course_id": "course-1", "point_ids": ["k1"]},
    )

    chunks = policy.split(document)

    assert chunks
    assert all(chunk.metadata["course_id"] == "course-1" for chunk in chunks)
    assert all(chunk.metadata["point_ids"] == ["k1"] for chunk in chunks)


# --- 不变量：代码 -------------------------------------------------------


def test_code_is_split_at_top_level_definitions() -> None:
    """代码按顶层定义切——函数与类不被拦腰截断。"""
    chunker = CodeChunker()

    chunks = chunker.split(_document(CODE, kind="code"))

    texts = [chunk.text for chunk in chunks]
    assert any(text.startswith("def helper(") for text in texts)
    assert any(text.startswith("class Calculator:") for text in texts)
    assert any(text.startswith("def main(") for text in texts)


def test_code_keeps_definitions_whole() -> None:
    """一个函数的完整代码在同一个切片里。"""
    chunker = CodeChunker()

    chunks = chunker.split(_document(CODE, kind="code"))

    calculator = next(chunk for chunk in chunks if chunk.text.startswith("class Calculator:"))
    assert "def sub(self, a, b):" in calculator.text
    assert "return a - b" in calculator.text


def test_code_without_definitions_falls_back() -> None:
    """没有顶层定义（散文）时退回兜底切分器。"""
    chunker = CodeChunker()

    chunks = chunker.split(_document("只是一段文字，没有定义。", kind="code"))

    assert len(chunks) == 1


def test_oversized_definition_uses_fallback() -> None:
    """超长的定义交给兜底切分器——正常路径按结构切，超限才退到通用策略。"""
    long_body = "\n".join(f"    x{i} = {i}" for i in range(200))
    code = f"def big():\n{long_body}\n"
    chunker = CodeChunker(max_size=200, fallback=RecursiveChunker(size=150, overlap=20))

    chunks = chunker.split(_document(code, kind="code", document_id="doc-big"))

    assert len(chunks) > 1
    assert all(len(chunk.text) <= 200 for chunk in chunks)


# --- 不变量：习题 -------------------------------------------------------


def test_exercises_are_split_by_question_number() -> None:
    """习题按题号切——一道题是完整单元。"""
    chunker = ExerciseChunker()

    chunks = chunker.split(_document(EXERCISES, kind="exercise"))

    texts = [chunk.text for chunk in chunks]
    assert any(text.startswith("1. 闭包是什么") for text in texts)
    assert any(text.startswith("2. 下面这段代码") for text in texts)


def test_exercise_keeps_answer_with_question() -> None:
    """答案跟着题走——切碎了模型就会答非所问。"""
    chunker = ExerciseChunker()

    chunks = chunker.split(_document(EXERCISES, kind="exercise"))

    first = next(chunk for chunk in chunks if chunk.text.startswith("1. 闭包是什么"))
    assert "答案：闭包记住了创建时的作用域" in first.text


def test_exercises_without_numbers_fall_back() -> None:
    """没有题号时退回兜底切分器。"""
    chunker = ExerciseChunker()

    chunks = chunker.split(_document("没有题号的习题材料", kind="exercise"))

    assert len(chunks) == 1


# --- 与课程文档的衔接 ---------------------------------------------------


def test_course_document_becomes_a_retrievable_document() -> None:
    """课程文档 → 可检索文档：课程侧的元数据进 metadata。"""
    document = CourseDocument(
        id="doc-1",
        course_id=CourseId("course-1"),
        title="闭包讲义",
        kind="notes",
        content="闭包记住了创建时的作用域。",
        source="/tmp/notes.md",
        point_ids=(KnowledgePointId("k1"),),
    )

    rag_document = to_rag_document(document)

    assert rag_document.id == "doc-1"
    assert rag_document.text == document.content
    assert rag_document.metadata["kind"] == "notes"
    assert rag_document.metadata["course_id"] == "course-1"
    assert rag_document.metadata["point_ids"] == ["k1"]


def test_course_document_chunks_keep_the_point_association() -> None:
    """切片带上知识点关联——Tutor 靠它判断「这段讲的是不是我要教的点」。"""
    document = CourseDocument(
        id="doc-1",
        course_id=CourseId("course-1"),
        title="闭包讲义",
        kind="notes",
        content=NOTES,
        source="/tmp/notes.md",
        point_ids=(KnowledgePointId("k1"),),
    )
    policy = default_chunking_policy()

    chunks = policy.split(to_rag_document(document))

    assert chunks
    assert all(chunk.metadata["point_ids"] == ["k1"] for chunk in chunks)
    assert all(chunk.metadata["course_id"] == "course-1" for chunk in chunks)


# --- 与既有切分器的关系 -------------------------------------------------


def test_fixed_size_chunker_is_unchanged() -> None:
    """定长切分仍按固定长度切——它处理结构不明的文本。"""
    chunker = FixedSizeChunker(size=50, overlap=10)

    chunks = chunker.split(_document(NOTES, kind="notes"))

    assert len(chunks) > 1
    assert all(len(chunk.text) <= 50 for chunk in chunks)


def test_custom_policy_can_replace_the_defaults() -> None:
    """策略可以被替换——端口与实现同包，调用方自己拼。"""
    policy = PolicyChunker(
        policies={"notes": FixedSizeChunker(size=40, overlap=0)},
        default=RecursiveChunker(),
    )

    chunks = policy.split(_document(NOTES, kind="notes"))

    assert all(len(chunk.text) <= 40 for chunk in chunks)
