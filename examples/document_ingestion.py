"""课程文档摄取：格式、部分成功、幂等与知识点关联。

跑法::

    uv run python examples/document_ingestion.py

脚本演示五件事：

1. **格式**--文本、Markdown、代码、PDF、笔记本都能解析；不支持的格式明确失败。
2. **部分成功**--一份坏文件不影响其余，失败清单在报告里。
3. **幂等**--重复导入同一份材料是覆盖；内容没变就不重写。
4. **关联**--知识点关联不被重新导入顺手抹掉。
5. **归属与一致性问题**--材料属于一门课；关联错了只报告、不阻止导入。

脚本在临时目录里造材料，不联网。
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from agent_learning_coach.core.application import DocumentRequest, ingest_course_documents
from agent_learning_coach.core.learning import (
    Course,
    CourseId,
    KnowledgePoint,
    KnowledgePointId,
    KnowledgeStructure,
)
from agent_learning_coach.core.storage import InMemoryCourseDocumentRepository


def _course() -> Course:
    """构造一门课：两个知识点。"""
    return Course(
        id=CourseId("course-1"),
        title="Python 基础",
        structure=KnowledgeStructure(
            points=(
                KnowledgePoint(id=KnowledgePointId("k1"), title="作用域"),
                KnowledgePoint(id=KnowledgePointId("k2"), title="闭包"),
            ),
        ),
    )


def _materials(directory: Path) -> tuple[Path, ...]:
    """在目录里造几份材料（含一份坏文件）。

    Args:
        directory: 目标目录。

    Returns:
        材料路径元组。
    """
    notes = directory / "notes.md"
    notes.write_text("# 闭包\n\n闭包记住了创建时的作用域。\n", encoding="utf-8")

    code = directory / "demo.py"
    code.write_text("def outer():\n    return lambda: 1\n", encoding="utf-8")

    notebook = directory / "lesson.ipynb"
    notebook.write_text(
        json.dumps(
            {
                "cells": [
                    {"cell_type": "markdown", "source": ["# 作用域\n"]},
                    {"cell_type": "code", "source": ["x = 1\n"]},
                ],
                "metadata": {},
                "nbformat": 4,
                "nbformat_minor": 5,
            },
        ),
        encoding="utf-8",
    )

    binary = directory / "data.bin"
    binary.write_text("not a supported format", encoding="utf-8")

    return (notes, code, notebook, binary)


def show_formats(directory: Path, materials: tuple[Path, ...]) -> None:
    """演示格式支持与失败原因。

    Args:
        directory: 材料目录。
        materials: 材料路径。
    """
    print("=== 1. 格式：解析得了什么、失败在哪 ===\n")
    requests = [
        DocumentRequest(source=str(path), point_ids=(KnowledgePointId("k1"),)) for path in materials
    ]
    report = asyncio.run(
        ingest_course_documents(requests, _course(), repository=InMemoryCourseDocumentRepository()),
    )

    for document in report.imported:
        print(f"解析成功：{document.render()}")
        print(f"           正文 {len(document.content)} 字")
    for failure in report.failed:
        print(f"失败：{failure.source} —— {failure.reason}")
    print("（PDF 与笔记本的解析共用同一套加载器；课程侧只加「属于哪门课、覆盖哪些知识点」）\n")


def show_partial_success(directory: Path, materials: tuple[Path, ...]) -> None:
    """演示部分成功。

    Args:
        directory: 材料目录。
        materials: 材料路径。
    """
    print("=== 2. 部分成功：一份坏文件不影响其余 ===\n")
    repository = InMemoryCourseDocumentRepository()
    requests = [DocumentRequest(source=str(path)) for path in materials]
    report = asyncio.run(
        ingest_course_documents(requests, _course(), repository=repository),
    )

    print(report.summary())
    print(f"全部成功：{report.ok}")
    print("（**静默的部分成功比整批失败更糟**——它让人以为材料齐了）")
    print("（所以失败清单必须在报告里：哪几份没进来、为什么）\n")


def show_idempotency(directory: Path, materials: tuple[Path, ...]) -> None:
    """演示幂等。

    Args:
        directory: 材料目录。
        materials: 材料路径。
    """
    print("=== 3. 幂等：重复导入是覆盖，不是追加 ===\n")
    repository = InMemoryCourseDocumentRepository()
    requests = [DocumentRequest(source=str(path)) for path in materials if path.suffix != ".bin"]

    first = asyncio.run(
        ingest_course_documents(requests, _course(), repository=repository),
    )
    second = asyncio.run(
        ingest_course_documents(requests, _course(), repository=repository),
    )

    print(f"第一次：{first.summary()}")
    print(f"第二次：{second.summary()}")
    stored = asyncio.run(repository.list_for_course(CourseId("course-1")))
    print(f"库里现在有 {len(stored)} 份材料")
    print("（id 由来源推导 → 同样的材料永远是同一份；内容没变就连写都不写）\n")


def show_associations(directory: Path, materials: tuple[Path, ...]) -> None:
    """演示知识点关联的三种语义与一致性问题。

    Args:
        directory: 材料目录。
        materials: 材料路径。
    """
    print("=== 4. 关联：不改 / 清空 / 设置 ===\n")
    repository = InMemoryCourseDocumentRepository()
    notes = str(materials[0])

    asyncio.run(
        ingest_course_documents(
            [DocumentRequest(source=notes, point_ids=(KnowledgePointId("k1"),))],
            _course(),
            repository=repository,
        ),
    )
    print("导入时关联了 k1")

    asyncio.run(
        ingest_course_documents(
            [DocumentRequest(source=notes)],
            _course(),
            repository=repository,
        ),
    )
    stored = asyncio.run(repository.list_for_course(CourseId("course-1")))
    print(f"重新导入后：{[str(item) for item in stored[0].value.point_ids]}（没给 → 不改）")

    asyncio.run(
        ingest_course_documents(
            [DocumentRequest(source=notes, point_ids=())],
            _course(),
            repository=repository,
        ),
    )
    stored = asyncio.run(repository.list_for_course(CourseId("course-1")))
    print(f"显式给空元组后：{[str(item) for item in stored[0].value.point_ids]}（清空）")

    report = asyncio.run(
        ingest_course_documents(
            [DocumentRequest(source=notes, point_ids=(KnowledgePointId("k9"),))],
            _course(),
            repository=repository,
        ),
    )
    print(f"关联到不存在的 k9：{report.issues[0].message}")
    print("（材料本身是好的，只是标注错了——挡住它会让人以为「这份材料不能用」）\n")


def show_ownership(directory: Path, materials: tuple[Path, ...]) -> None:
    """演示材料归属一门课。

    Args:
        directory: 材料目录。
        materials: 材料路径。
    """
    print("=== 5. 归属：材料属于一门课 ===\n")
    repository = InMemoryCourseDocumentRepository()
    notes = str(materials[0])
    asyncio.run(
        ingest_course_documents(
            [DocumentRequest(source=notes)],
            _course(),
            repository=repository,
        ),
    )

    other = Course(id=CourseId("course-2"), title="别的课", structure=KnowledgeStructure())
    report = asyncio.run(
        ingest_course_documents(
            [DocumentRequest(source=notes)],
            other,
            repository=repository,
        ),
    )

    print(f"导入到另一门课：{report.failed[0].reason}")
    print("（材料在整个系统里只有一个身份——课程归属是它的属性，不是它的多份副本）\n")


def main() -> None:
    """跑一遍课程文档摄取演示。"""
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        materials = _materials(directory)
        show_formats(directory, materials)
        show_partial_success(directory, materials)
        show_idempotency(directory, materials)
        show_associations(directory, materials)
        show_ownership(directory, materials)


if __name__ == "__main__":
    main()
