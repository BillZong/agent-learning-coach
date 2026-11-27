"""文档加载。

**加载器只做一件事**：把外部材料读成 ``Document``。它不切分、不嵌入——
那是后面环节的事。

**文档 id 必须可复现**：同样的文件每次摄取要得到同样的 id，否则重新摄取会
产生「同一份材料的两份记录」，而向量库里无法区分它们。id 由**来源路径**推导，
不是随机 UUID。

**支持的格式**：纯文本、Markdown、代码、PDF、Jupyter 笔记本（Day 27 补上富格式
的解析——PDF 与笔记本的解析属于这里，而**课程侧的记录**属于
``core.learning.documents``，两边共用同一套解析）。

**读不了的文件跳过并记录**，而不是让整批摄取失败——一份坏文件不该毁掉整个课程
库（``core.rag.ingestion`` 已是这个口径）。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Protocol, runtime_checkable

from agent_learning_coach.core.rag.types import Document

TEXT_SUFFIXES: tuple[str, ...] = (".txt", ".md", ".markdown", ".rst", ".py", ".pyi")
"""按纯文本处理的扩展名。"""

PDF_SUFFIXES: tuple[str, ...] = (".pdf",)
"""按 PDF 处理的扩展名。"""

NOTEBOOK_SUFFIXES: tuple[str, ...] = (".ipynb",)
"""按 Jupyter 笔记本处理的扩展名。"""


def document_id(source: str) -> str:
    """由来源推导文档标识。

    Args:
        source: 来源串。

    Returns:
        十六进制的短标识。同一来源永远得到同一标识。
    """
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return digest[:16]


@runtime_checkable
class DocumentLoader(Protocol):
    """文档加载器。

    **每个加载器都带 ``skipped``**：读不了的文件被跳过并记录原因，而不是让整批
    摄取失败——一份坏文件不该毁掉整个课程库。
    """

    skipped: list[str]
    """被跳过的文件及原因。"""

    def load(self) -> list[Document]:
        """加载文档。

        Returns:
            文档列表。顺序稳定——同样的输入永远得到同样的顺序。
        """
        ...


class TextFileLoader:
    """从单个文本文件加载。

    编码默认 UTF-8。读不了的文件**跳过并记录**，而不是让整个摄取失败——
    一份坏文件不该拖垮整批摄取。
    """

    def __init__(
        self,
        path: str | Path,
        *,
        encoding: str = "utf-8",
        metadata: dict[str, object] | None = None,
    ) -> None:
        """初始化加载器。

        Args:
            path: 文件路径。
            encoding: 文件编码。
            metadata: 附加到文档上的元数据。
        """
        self.path = Path(path)
        self.encoding = encoding
        self.metadata = dict(metadata or {})
        self.skipped: list[str] = []
        """被跳过的文件及原因。"""

    def load(self) -> list[Document]:
        """加载文档。

        Returns:
            文档列表。文件不存在或读不了时返回空列表，原因记在 ``skipped``。
        """
        try:
            text = self.path.read_text(encoding=self.encoding)
        except (OSError, UnicodeDecodeError) as exc:
            self.skipped.append(f"{self.path}: {exc}")
            return []
        if not text.strip():
            self.skipped.append(f"{self.path}: 内容为空")
            return []
        source = str(self.path)
        return [
            Document(
                id=document_id(source),
                text=text,
                source=source,
                metadata={**self.metadata, "filename": self.path.name},
            )
        ]


class PdfFileLoader:
    """从单个 PDF 文件加载。

    用 **PyMuPDF**（``pymupdf``）解析：逐页取文本。它是**尽力而为**的——扫描版
    PDF（纯图片）取不到文本，那种文件**跳过并记录**，而不是产出一份空文档。
    """

    def __init__(self, path: str | Path, *, metadata: dict[str, object] | None = None) -> None:
        """初始化加载器。

        Args:
            path: 文件路径。
            metadata: 附加到文档上的元数据。
        """
        self.path = Path(path)
        self.metadata = dict(metadata or {})
        self.skipped: list[str] = []
        """被跳过的文件及原因。"""

    def load(self) -> list[Document]:
        """加载文档。

        Returns:
            文档列表；读不了或取不到文本时返回空列表，原因记在 ``skipped``。
        """
        try:
            import pymupdf
        except ImportError as exc:  # pragma: no cover - 依赖缺失时的兜底
            self.skipped.append(f"{self.path}: 缺少 PyMuPDF，无法解析 PDF：{exc}")
            return []

        try:
            with pymupdf.open(self.path) as pdf:
                pages = [page.get_text() for page in pdf]
        except Exception as exc:
            self.skipped.append(f"{self.path}: {exc}")
            return []

        text = "\n\n".join(part for part in pages if part.strip())
        if not text.strip():
            self.skipped.append(f"{self.path}: 取不到文本（可能是扫描件）")
            return []
        source = str(self.path)
        return [
            Document(
                id=document_id(source),
                text=text,
                source=source,
                metadata={**self.metadata, "filename": self.path.name, "pages": len(pages)},
            )
        ]


class NotebookFileLoader:
    """从 Jupyter 笔记本（``.ipynb``）加载。

    取**代码单元与 Markdown 单元**的文本，按出现顺序拼接。输出与执行结果无关
    ——那是运行产物，不是材料。
    """

    def __init__(self, path: str | Path, *, metadata: dict[str, object] | None = None) -> None:
        """初始化加载器。

        Args:
            path: 文件路径。
            metadata: 附加到文档上的元数据。
        """
        self.path = Path(path)
        self.metadata = dict(metadata or {})
        self.skipped: list[str] = []
        """被跳过的文件及原因。"""

    def load(self) -> list[Document]:
        """加载文档。

        Returns:
            文档列表；读不了或格式不对时返回空列表，原因记在 ``skipped``。
        """
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.skipped.append(f"{self.path}: {exc}")
            return []
        if not isinstance(payload, dict):
            self.skipped.append(f"{self.path}: 不是合法的笔记本文件")
            return []

        cells = payload.get("cells")
        if not isinstance(cells, list):
            self.skipped.append(f"{self.path}: 缺少 cells 字段")
            return []

        parts: list[str] = []
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            cell_type = cell.get("cell_type")
            source = cell.get("source")
            if not isinstance(source, list):
                continue
            body = "".join(line for line in source if isinstance(line, str)).strip()
            if not body:
                continue
            if cell_type == "code":
                parts.append(f"```python\n{body}\n```")
            elif cell_type == "markdown":
                parts.append(body)
        text = "\n\n".join(parts)
        if not text.strip():
            self.skipped.append(f"{self.path}: 内容为空")
            return []
        source = str(self.path)
        return [
            Document(
                id=document_id(source),
                text=text,
                source=source,
                metadata={**self.metadata, "filename": self.path.name, "cells": len(cells)},
            )
        ]


def load_source(
    path: str | Path,
    *,
    metadata: dict[str, object] | None = None,
) -> tuple[list[Document], str | None]:
    """按扩展名分派到对应的加载器。

    **不支持的格式**与**读不了的文件**都返回空文档，但**原因不同**：前者是
    「这个格式我们不支持」，后者是「这个文件有问题」。两者都不该让整批摄取失败
    ——一份坏文件不该毁掉整个课程库。

    Args:
        path: 文件路径。
        metadata: 附加到文档上的元数据。

    Returns:
        (文档列表, 失败原因)。成功时失败原因为 ``None``。
    """
    target = Path(path)
    suffix = target.suffix.lower()
    if suffix in TEXT_SUFFIXES:
        loader: DocumentLoader = TextFileLoader(target, metadata=metadata)
    elif suffix in PDF_SUFFIXES:
        loader = PdfFileLoader(target, metadata=metadata)
    elif suffix in NOTEBOOK_SUFFIXES:
        loader = NotebookFileLoader(target, metadata=metadata)
    else:
        return [], f"不支持的格式：{suffix or '（无扩展名）'}"
    documents = loader.load()
    if documents:
        return documents, None
    reason = loader.skipped[0] if loader.skipped else "读不出内容"
    return [], reason


class DirectoryLoader:
    """从目录加载材料。

    默认收纯文本、Markdown、代码、PDF 与笔记本。按**路径排序**返回，保证顺序
    稳定——否则每次摄取的文档顺序不同，向量库里的 id 分配也会漂移。
    """

    def __init__(
        self,
        directory: str | Path,
        *,
        patterns: tuple[str, ...] = (
            "**/*.md",
            "**/*.txt",
            "**/*.rst",
            "**/*.py",
            "**/*.pdf",
            "**/*.ipynb",
        ),
        exclude: tuple[str, ...] = (),
        encoding: str = "utf-8",
    ) -> None:
        """初始化加载器。

        Args:
            directory: 目录路径。
            patterns: 匹配模式（相对目录的 glob）。
            exclude: 要排除的路径片段。
            encoding: 文件编码。
        """
        self.directory = Path(directory)
        self.patterns = patterns
        self.exclude = exclude
        self.encoding = encoding
        self.skipped: list[str] = []
        """被跳过的文件及原因。"""

    def load(self) -> list[Document]:
        """加载文档。

        Returns:
            文档列表，按路径排序。
        """
        documents: list[Document] = []
        seen: set[Path] = set()
        for pattern in self.patterns:
            for path in sorted(self.directory.glob(pattern)):
                if not path.is_file() or path in seen:
                    continue
                if any(part in path.as_posix() for part in self.exclude):
                    continue
                seen.add(path)
                suffix = path.suffix.lower()
                if suffix in TEXT_SUFFIXES:
                    loader: DocumentLoader = TextFileLoader(path, encoding=self.encoding)
                elif suffix in PDF_SUFFIXES:
                    loader = PdfFileLoader(path)
                elif suffix in NOTEBOOK_SUFFIXES:
                    loader = NotebookFileLoader(path)
                else:
                    continue
                documents.extend(loader.load())
                self.skipped.extend(loader.skipped)
        return documents


__all__ = [
    "NOTEBOOK_SUFFIXES",
    "PDF_SUFFIXES",
    "TEXT_SUFFIXES",
    "DirectoryLoader",
    "DocumentLoader",
    "NotebookFileLoader",
    "PdfFileLoader",
    "TextFileLoader",
    "document_id",
    "load_source",
]
