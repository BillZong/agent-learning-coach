"""课程文档摄取的测试（Day 27）。

覆盖六组关注点：

**格式**--文本、Markdown、代码、PDF、笔记本都能解析；不支持的格式明确失败。

**部分成功**--一份坏文件不影响其余，失败清单在报告里。

**幂等**--重复导入同一份材料是覆盖；内容没变就不重写。

**关联**--知识点关联不被重新导入顺手抹掉；三种语义（不改 / 清空 / 设置）。

**归属**--同一份材料属于另一门课时明确失败。

**一致性**--关联到不存在的知识点被报告，但不阻止导入。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent_learning_coach.core.application import (
    DocumentRequest,
    IngestionReport,
    ingest_course_documents,
)
from agent_learning_coach.core.learning import (
    Course,
    CourseId,
    KnowledgePoint,
    KnowledgePointId,
    KnowledgeStructure,
)
from agent_learning_coach.core.rag.loader import (
    NotebookFileLoader,
    PdfFileLoader,
    TextFileLoader,
    load_source,
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


def _write(tmp_path: Path, name: str, text: str) -> str:
    """写一个文本文件并返回它的路径。

    Args:
        tmp_path: 临时目录。
        name: 文件名。
        text: 内容。

    Returns:
        文件路径。
    """
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def _write_notebook(tmp_path: Path, name: str) -> str:
    """写一个 Jupyter 笔记本并返回它的路径。

    Args:
        tmp_path: 临时目录。
        name: 文件名。

    Returns:
        文件路径。
    """
    payload = {
        "cells": [
            {"cell_type": "markdown", "source": ["# 闭包\n", "\n", "一句话解释\n"]},
            {"cell_type": "code", "source": ["def outer():\n", "    pass\n"]},
            {"cell_type": "code", "source": ["print('输出')\n"]},
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)


def _write_pdf(tmp_path: Path, name: str) -> str:
    """写一个 PDF 文件并返回它的路径。

    Args:
        tmp_path: 临时目录。
        name: 文件名。

    Returns:
        文件路径。
    """
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / name
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "Hello closure")
    document.save(str(path))
    document.close()
    return str(path)


# --- 格式 ---------------------------------------------------------------


def test_text_file_is_loaded(tmp_path: Path) -> None:
    """纯文本与 Markdown 按纯文本处理。"""
    source = _write(tmp_path, "notes.md", "# 闭包\n\n闭包记住了创建时的作用域。\n")

    documents, reason = load_source(source)

    assert reason is None
    assert len(documents) == 1
    assert "闭包" in documents[0].text


def test_code_file_is_loaded(tmp_path: Path) -> None:
    """代码文件按纯文本处理（切分策略是 Day 28 的事）。"""
    source = _write(tmp_path, "demo.py", "def outer():\n    return 1\n")

    documents, reason = load_source(source)

    assert reason is None
    assert "def outer" in documents[0].text


def test_notebook_is_loaded(tmp_path: Path) -> None:
    """笔记本取代码单元与 Markdown 单元。"""
    source = _write_notebook(tmp_path, "lesson.ipynb")

    documents, reason = load_source(source)

    assert reason is None
    assert "# 闭包" in documents[0].text
    assert "def outer" in documents[0].text


def test_pdf_is_loaded(tmp_path: Path) -> None:
    """PDF 用 PyMuPDF 解析。"""
    source = _write_pdf(tmp_path, "slides.pdf")

    documents, reason = load_source(source)

    assert reason is None
    assert "Hello closure" in documents[0].text


def test_unsupported_format_is_reported(tmp_path: Path) -> None:
    """不支持的格式明确说明原因。"""
    source = _write(tmp_path, "data.bin", "not text")

    documents, reason = load_source(source)

    assert documents == []
    assert reason is not None
    assert "不支持的格式" in reason


def test_missing_file_is_reported(tmp_path: Path) -> None:
    """文件不存在要说清楚，而不是产出一份空文档。"""
    source = str(tmp_path / "missing.md")

    documents, reason = load_source(source)

    assert documents == []
    assert reason is not None


def test_empty_file_is_reported(tmp_path: Path) -> None:
    """空文件被跳过并记录原因。"""
    source = _write(tmp_path, "empty.md", "   \n")

    documents, reason = load_source(source)

    assert documents == []
    assert reason is not None
    assert "内容为空" in reason


def test_pdf_loader_without_text_is_skipped(tmp_path: Path) -> None:
    """扫描件 PDF 取不到文本——跳过并记录，不产出空文档。"""
    pymupdf = pytest.importorskip("pymupdf")
    path = tmp_path / "scan.pdf"
    document = pymupdf.open()
    document.new_page()
    document.save(str(path))
    document.close()

    loader = PdfFileLoader(path)
    documents = loader.load()

    assert documents == []
    assert loader.skipped


def test_notebook_loader_rejects_broken_json(tmp_path: Path) -> None:
    """坏掉的笔记本文件被跳过并记录。"""
    path = tmp_path / "broken.ipynb"
    path.write_text("{not json", encoding="utf-8")

    loader = NotebookFileLoader(path)
    documents = loader.load()

    assert documents == []
    assert loader.skipped


def test_text_loader_records_skipped(tmp_path: Path) -> None:
    """加载器把跳过的文件记在 skipped 上。"""
    loader = TextFileLoader(tmp_path / "missing.md")

    assert loader.load() == []
    assert loader.skipped


# --- 摄取 ---------------------------------------------------------------


def test_ingest_imports_documents(tmp_path: Path) -> None:
    """导入的材料入库，知识点关联一并写入。"""
    repository = InMemoryCourseDocumentRepository()
    source = _write(tmp_path, "notes.md", "闭包记住了作用域。\n")

    report = asyncio.run(
        ingest_course_documents(
            [DocumentRequest(source=source, point_ids=(KnowledgePointId("k1"),))],
            _course(),
            repository=repository,
        ),
    )

    assert report.ok
    assert len(report.imported) == 1
    document = report.imported[0]
    assert document.title == "notes"
    assert document.kind == "notes"
    assert document.point_ids == (KnowledgePointId("k1"),)
    assert document.course_id == CourseId("course-1")


def test_ingest_reports_failures_without_blocking_the_rest(tmp_path: Path) -> None:
    """一份坏文件不影响其余——失败清单在报告里。"""
    repository = InMemoryCourseDocumentRepository()
    good = _write(tmp_path, "notes.md", "闭包。\n")
    bad = _write(tmp_path, "data.bin", "binary")

    report = asyncio.run(
        ingest_course_documents(
            [DocumentRequest(source=bad), DocumentRequest(source=good)],
            _course(),
            repository=repository,
        ),
    )

    assert not report.ok
    assert len(report.imported) == 1
    assert len(report.failed) == 1
    assert report.failed[0].source == bad
    assert "不支持的格式" in report.failed[0].reason


def test_ingest_is_idempotent(tmp_path: Path) -> None:
    """重复导入同一份材料：内容没变就不重写。"""
    repository = InMemoryCourseDocumentRepository()
    source = _write(tmp_path, "notes.md", "闭包。\n")
    requests = [DocumentRequest(source=source)]

    first = asyncio.run(
        ingest_course_documents(requests, _course(), repository=repository),
    )
    second = asyncio.run(
        ingest_course_documents(requests, _course(), repository=repository),
    )

    assert len(first.imported) == 1
    assert second.imported == ()
    assert second.updated == ()
    assert second.unchanged == (source,)
    assert len(asyncio.run(repository.list_for_course(CourseId("course-1")))) == 1


def test_changed_content_updates_the_document(tmp_path: Path) -> None:
    """材料内容变了 → 覆盖更新。"""
    repository = InMemoryCourseDocumentRepository()
    source = _write(tmp_path, "notes.md", "闭包。\n")
    asyncio.run(
        ingest_course_documents(
            [DocumentRequest(source=source)],
            _course(),
            repository=repository,
        ),
    )

    _write(tmp_path, "notes.md", "闭包（修订版）。\n")
    report = asyncio.run(
        ingest_course_documents(
            [DocumentRequest(source=source)],
            _course(),
            repository=repository,
        ),
    )

    assert len(report.updated) == 1
    assert "修订版" in report.updated[0].content
    assert len(asyncio.run(repository.list_for_course(CourseId("course-1")))) == 1


def test_reimport_keeps_point_associations(tmp_path: Path) -> None:
    """重新导入材料不抹掉知识点关联。"""
    repository = InMemoryCourseDocumentRepository()
    source = _write(tmp_path, "notes.md", "闭包。\n")
    asyncio.run(
        ingest_course_documents(
            [DocumentRequest(source=source, point_ids=(KnowledgePointId("k1"),))],
            _course(),
            repository=repository,
        ),
    )

    report = asyncio.run(
        ingest_course_documents(
            [DocumentRequest(source=source)],
            _course(),
            repository=repository,
        ),
    )

    assert report.unchanged == (source,)
    stored = asyncio.run(repository.list_for_course(CourseId("course-1")))
    assert stored[0].value.point_ids == (KnowledgePointId("k1"),)


def test_explicit_empty_point_ids_clear_the_association(tmp_path: Path) -> None:
    """显式给空元组 → 清空关联。"""
    repository = InMemoryCourseDocumentRepository()
    source = _write(tmp_path, "notes.md", "闭包。\n")
    asyncio.run(
        ingest_course_documents(
            [DocumentRequest(source=source, point_ids=(KnowledgePointId("k1"),))],
            _course(),
            repository=repository,
        ),
    )

    asyncio.run(
        ingest_course_documents(
            [DocumentRequest(source=source, point_ids=())],
            _course(),
            repository=repository,
        ),
    )

    stored = asyncio.run(repository.list_for_course(CourseId("course-1")))
    assert stored[0].value.point_ids == ()


def test_material_belonging_to_another_course_is_rejected(tmp_path: Path) -> None:
    """同一份材料属于另一门课时明确失败——材料属于一门课。"""
    repository = InMemoryCourseDocumentRepository()
    source = _write(tmp_path, "notes.md", "闭包。\n")
    asyncio.run(
        ingest_course_documents(
            [DocumentRequest(source=source)],
            _course(),
            repository=repository,
        ),
    )

    other = Course(id=CourseId("course-2"), title="别的课", structure=KnowledgeStructure())
    report = asyncio.run(
        ingest_course_documents(
            [DocumentRequest(source=source)],
            other,
            repository=repository,
        ),
    )

    assert not report.ok
    assert "已经属于课程 course-1" in report.failed[0].reason


def test_unknown_point_is_reported_but_does_not_block(tmp_path: Path) -> None:
    """关联到不存在的知识点被报告，但材料照样进来。"""
    repository = InMemoryCourseDocumentRepository()
    source = _write(tmp_path, "notes.md", "闭包。\n")

    report = asyncio.run(
        ingest_course_documents(
            [DocumentRequest(source=source, point_ids=(KnowledgePointId("k9"),))],
            _course(),
            repository=repository,
        ),
    )

    assert report.ok
    assert len(report.imported) == 1
    assert len(report.issues) == 1
    assert "k9" in report.issues[0].message


def test_kind_can_be_overridden(tmp_path: Path) -> None:
    """种类可以显式指定——PDF 推断不出是讲义还是习题。"""
    repository = InMemoryCourseDocumentRepository()
    source = _write(tmp_path, "notes.md", "闭包。\n")

    report = asyncio.run(
        ingest_course_documents(
            [DocumentRequest(source=source, kind="exercise")],
            _course(),
            repository=repository,
        ),
    )

    assert report.imported[0].kind == "exercise"


def test_report_summary_counts_everything() -> None:
    """摘要把四类结果都说清楚。"""
    report = IngestionReport(imported=(), updated=(), unchanged=("a",), failed=(), issues=())

    assert "未变 1" in report.summary()
    assert report.ok
