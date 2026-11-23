"""文档加载。

**加载器只做一件事**：把外部材料读成 ``Document``。它不切分、不嵌入——
那是后面环节的事。

**文档 id 必须可复现**：同样的文件每次摄取要得到同样的 id，否则重新摄取会
产生「同一份材料的两份记录」，而向量库里无法区分它们。id 由**来源路径**推导，
不是随机 UUID。

**本阶段只支持纯文本与 Markdown。** PDF、Word 等富格式的解析留给 Day 25
「Document Ingestion」——那是学习领域侧的摄取工作，与检索基础设施是两件事。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol, runtime_checkable

from agent_learning_coach.core.rag.types import Document


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
    """文档加载器。"""

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


class DirectoryLoader:
    """从目录加载文本文件。

    默认收 ``.md`` 与 ``.txt``。按**路径排序**返回，保证顺序稳定——否则每次
    摄取的文档顺序不同，向量库里的 id 分配也会漂移。
    """

    def __init__(
        self,
        directory: str | Path,
        *,
        patterns: tuple[str, ...] = ("**/*.md", "**/*.txt"),
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
                loader = TextFileLoader(path, encoding=self.encoding)
                documents.extend(loader.load())
                self.skipped.extend(loader.skipped)
        return documents


__all__ = ["DirectoryLoader", "DocumentLoader", "TextFileLoader", "document_id"]
