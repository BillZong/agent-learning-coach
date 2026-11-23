"""切片。

**切分是检索质量的第一个决定因素。** 主教材 5.3.2 用的是最朴素的
**定长切分**（按字符数切，带重叠）；它的问题是**切断了语义单元**——一句话
被切成两半，两半都检索不到完整的那个意思。

因此本项目提供两种切分器：

- ``FixedSizeChunker``：定长切分（主教材的做法）。简单、可预测，适合
  结构不明的文本（如网页抓取结果）。
- ``RecursiveChunker``：**按结构递归切分**——先按段落切，段落太大再按句子切，
  句子还太大再按字符切，最后把不超过上限的片段合并成块。

两者的差别可以直接用测试观察：给一段有自然段落的文本，定长切分会把段落
拦腰截断，递归切分不会。

**切片大小按字符计，不按 token。** 按 token 更精确，但会让切分器依赖分词器
——而切分器不该知道用的是哪种模型。token 预算由呈现层负责（ADR-0008）：
切得太碎只是多占一点检索空间，切得太大才是检索质量问题。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from agent_learning_coach.core.rag.types import Chunk, Document

DEFAULT_SEPARATORS: tuple[str, ...] = (
    "\n\n",
    "\n",
    "。",
    "！",
    "？",
    ".",
    " ",
    "",
)
"""递归切分的分隔符，按优先级从粗到细。

末尾的空串是**兜底**：按字符硬切，保证任何文本都能被切开。
"""


def chunk_id(document_id_value: str, index: int) -> str:
    """由文档标识与序号推导切片标识。

    Args:
        document_id_value: 文档标识。
        index: 文档内的序号。

    Returns:
        十六进制的短标识。同一份文档的第 N 块永远得到同一标识。
    """
    raw = f"{document_id_value}:{index}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


@runtime_checkable
class Chunker(Protocol):
    """切片器。"""

    def split(self, document: Document) -> list[Chunk]:
        """把文档切成切片。

        Args:
            document: 待切分的文档。

        Returns:
            切片列表。顺序稳定。
        """
        ...


@dataclass(frozen=True)
class FixedSizeChunker:
    """定长切分（主教材 5.3.2 的做法）。

    按字符数切分，相邻块之间保留 ``overlap`` 个字符的重叠——重叠让跨块的
    内容仍能被检索到。

    代价是**会截断语义单元**。结构已知的文本应当用 ``RecursiveChunker``。
    """

    size: int = 500
    """块大小（字符）。"""

    overlap: int = 50
    """相邻块的重叠（字符）。必须小于 ``size``。"""

    def split(self, document: Document) -> list[Chunk]:
        """按固定长度切分文档。

        Args:
            document: 待切分的文档。

        Returns:
            切片列表。

        Raises:
            ValueError: ``overlap`` 不小于 ``size``（会导致无限推进）。
        """
        if self.size <= 0:
            raise ValueError("size 必须为正数")
        if self.overlap < 0 or self.overlap >= self.size:
            raise ValueError("overlap 必须满足 0 <= overlap < size")

        chunks: list[Chunk] = []
        text = document.text
        start = 0
        while start < len(text):
            piece = text[start : start + self.size].strip()
            if piece:
                chunks.append(self._make_chunk(document, piece, len(chunks)))
            if start + self.size >= len(text):
                break
            start = start + self.size - self.overlap
        return chunks

    @staticmethod
    def _make_chunk(document: Document, text: str, index: int) -> Chunk:
        """构造一个切片。

        Args:
            document: 所属文档。
            text: 切片正文。
            index: 文档内序号。

        Returns:
            切片。
        """
        return Chunk(
            id=chunk_id(document.id, index),
            document_id=document.id,
            text=text,
            index=index,
            source=document.source,
            metadata=dict(document.metadata),
        )


@dataclass(frozen=True)
class RecursiveChunker:
    """按结构递归切分。

    先用最粗的分隔符（段落）切；仍然太长的块用下一个更细的分隔符（句子、
    空格、字符）继续切；最后把不超过上限的片段合并成块。

    合并阶段保留重叠，因此**跨块的句子仍能被检索到**。
    """

    size: int = 500
    """块大小（字符）。"""

    overlap: int = 50
    """相邻块的重叠（字符）。"""

    separators: tuple[str, ...] = DEFAULT_SEPARATORS
    """分隔符，按从粗到细的顺序。"""

    def split(self, document: Document) -> list[Chunk]:
        """按结构切分文档。

        Args:
            document: 待切分的文档。

        Returns:
            切片列表。

        Raises:
            ValueError: ``size`` 非正，或 ``overlap`` 不在合法区间。
        """
        if self.size <= 0:
            raise ValueError("size 必须为正数")
        if self.overlap < 0 or self.overlap >= self.size:
            raise ValueError("overlap 必须满足 0 <= overlap < size")

        pieces = self._merge(self._atoms(document.text, list(self.separators)))
        return [
            Chunk(
                id=chunk_id(document.id, index),
                document_id=document.id,
                text=piece.strip(),
                index=index,
                source=document.source,
                metadata=dict(document.metadata),
            )
            for index, piece in enumerate(pieces)
            if piece.strip()
        ]

    def _atoms(self, text: str, separators: list[str]) -> list[str]:
        """把文本切成不超过 ``size`` 的原子片段。

        Args:
            text: 待切分文本。
            separators: 剩余可用的分隔符（从粗到细）。

        Returns:
            原子片段列表。
        """
        if not text.strip():
            return []
        if len(text) <= self.size:
            return [text]
        for index, separator in enumerate(separators):
            if separator == "":
                break
            if separator not in text:
                continue
            parts = text.split(separator)
            atoms: list[str] = []
            for part_index, part in enumerate(parts):
                # 分隔符跟随前一段，保留原文的标点
                suffix = separator if part_index < len(parts) - 1 else ""
                candidate = part + suffix
                if len(candidate) <= self.size:
                    atoms.append(candidate)
                else:
                    atoms.extend(self._atoms(part, separators[index + 1 :]))
            return atoms
        return [text[i : i + self.size] for i in range(0, len(text), self.size)]

    def _merge(self, atoms: list[str]) -> list[str]:
        """把原子片段合并成不超过 ``size`` 的块。

        Args:
            atoms: 原子片段列表。

        Returns:
            块列表。相邻块之间保留 ``overlap`` 个字符的重叠。
        """
        chunks: list[str] = []
        buffer = ""
        for atom in atoms:
            if not buffer:
                buffer = atom
                continue
            if len(buffer) + len(atom) <= self.size:
                buffer += atom
                continue
            chunks.append(buffer)
            tail = buffer[-self.overlap :] if self.overlap else ""
            # 重叠部分加上新片段不能超过上限——超了就只用新片段
            buffer = tail + atom if len(tail) + len(atom) <= self.size else atom
        if buffer:
            chunks.append(buffer)
        return chunks


__all__ = [
    "DEFAULT_SEPARATORS",
    "Chunker",
    "FixedSizeChunker",
    "RecursiveChunker",
    "chunk_id",
]
