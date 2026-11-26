"""切分策略：按材料类型选，以及参数如何被测试锁定。

跑法::

    uv run python examples/chunking_strategy.py

脚本演示四件事：

1. **一刀切的策略总有一半材料是错的**——同一段文字，切法不同结果不同。
2. **按类型选策略**——讲义按段落、代码按顶层定义、习题按题号。
3. **元数据跟着切片走**——检索要靠它按课程与知识点过滤。
4. **锁的是不变量，不是数字**——数字会变，不变量不会。

脚本不联网。
"""

from __future__ import annotations

from agent_learning_coach.core.application import to_rag_document
from agent_learning_coach.core.learning import CourseDocument, CourseId, KnowledgePointId
from agent_learning_coach.core.rag import (
    Document,
    FixedSizeChunker,
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
```

3. 装饰器是怎么利用闭包的？
"""


def _document(text: str, kind: str) -> Document:
    """构造一份带种类的文档。

    Args:
        text: 正文。
        kind: 材料种类。

    Returns:
        文档。
    """
    return Document(
        id=f"doc-{kind}",
        text=text,
        source=f"/tmp/{kind}.md",
        metadata={"kind": kind},
    )


def show_one_size_fits_none() -> None:
    """演示一刀切的策略为什么总有一半材料是错的。"""
    print("=== 1. 一刀切的策略总有一半材料是错的 ===\n")
    document = _document(CODE, "code")

    fixed = FixedSizeChunker(size=120, overlap=20).split(document)
    print(f"定长切分代码：{len(fixed)} 块，第一块：")
    print("  " + fixed[0].text.replace("\n", "\\n")[:90] + "…")
    print("（函数被拦腰截断——碎片检索不到，也读不懂）")

    structural = RecursiveChunker(size=120, overlap=20).split(document)
    print(f"\n递归切分代码：{len(structural)} 块，仍然切在段落上")
    print("（段落对代码不是语义单元——正确切法是按顶层定义）\n")


def show_policy() -> None:
    """演示按类型选策略。"""
    print("=== 2. 按材料类型选策略 ===\n")
    policy = default_chunking_policy()

    for kind, text in (("notes", NOTES), ("code", CODE), ("exercise", EXERCISES)):
        chunks = policy.split(_document(text, kind))
        print(f"{kind:9s} -> {len(chunks)} 块，每块 {len(chunks[0].text)} 字左右")
        first = chunks[0].text.replace("\n", "\\n")
        print(f"          第一块：{first[:80]}…")

    print("\n（讲义按段落、代码按顶层定义、习题按题号——每类材料有它自己的语义单元）")
    print("（代码切分器：按顶层定义切；习题切分器：按题号切；两者都带兜底）\n")


def show_metadata() -> None:
    """演示元数据跟着切片走。"""
    print("=== 3. 元数据跟着切片走 ===\n")
    document = CourseDocument(
        id="doc-1",
        course_id=CourseId("course-1"),
        title="闭包讲义",
        kind="notes",
        content=NOTES,
        source="/tmp/notes.md",
        point_ids=(KnowledgePointId("k1"),),
    )
    rag_document = to_rag_document(document)
    chunks = default_chunking_policy().split(rag_document)

    print(f"切片的元数据：{chunks[0].metadata}")
    print("（课程 id 与知识点关联都带上了——检索时才能按「教 k1 时」过滤出相关材料）")
    print("（课程文档只解析一次：课程侧记归属与关联，RAG 侧记切片与向量）\n")


def show_invariants() -> None:
    """演示测试锁的是不变量而不是数字。"""
    print("=== 4. 锁的是不变量，不是数字 ===\n")
    policy = default_chunking_policy()
    code_chunks = policy.split(_document(CODE, "code"))
    exercise_chunks = policy.split(_document(EXERCISES, "exercise"))

    calculator = next(chunk for chunk in code_chunks if chunk.text.startswith("class Calculator:"))
    print(f"不变量一：顶层定义不被截断 -> {'def sub(self, a, b):' in calculator.text}")
    first = next(chunk for chunk in exercise_chunks if chunk.text.startswith("1. 闭包是什么"))
    print(f"不变量二：一道题是一个单元 -> {'答案：闭包记住了创建时的作用域' in first.text}")
    print(f"不变量三：切片不超上限 -> {all(len(chunk.text) <= 1600 for chunk in code_chunks)}")
    again = policy.split(_document(CODE, "code"))
    print(f"不变量四：切片 id 可复现 -> {again[0].id == code_chunks[0].id}")
    print("\n（数字（800、1200、1000……）没有实证依据，只是起点）")
    print("（锁数字只会让改动变成「改测试」；锁不变量才能让改动安全）")


def main() -> None:
    """跑一遍切分策略演示。"""
    show_one_size_fits_none()
    show_policy()
    show_metadata()
    show_invariants()


if __name__ == "__main__":
    main()
