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

**按材料类型选策略**（Day 28）：讲义按段落切、代码按顶层定义切、习题按题号切、
参考资料按定长切——**每类材料有它自己的语义单元**，一刀切的策略总有一半材料是
错的。见 ``PolicyChunker`` 与 ``default_chunking_policy``。

**参数由不变量锁定，不是由数字锁定**：测试断言的是「讲义不切断句子、代码不切断
顶层定义、习题不切断一道题、切片不超上限、切片 id 可复现」。数字会随模型与语料
变化，不变量不会——锁数字只会让改动变成「改测试」。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class CodeChunker:
    """按**顶层定义**切分代码。

    **代码有结构**：一个函数或一个类是完整的语义单元，按字符切会把它们切碎——碎片
    检索不到，也读不懂。

    顶层定义由**正则**给出（默认按 Python 的 ``def`` / ``class`` / ``async def``）。
    换语言换正则即可，不需要换切分器。

    **定义太长怎么办**：一个巨大的函数仍然要切得开——超出上限的部分交给兜底切分器
    （默认递归切分）。**兜底不是妥协，是兜底**：正常路径按结构切，只有超限时才
    退到通用策略。
    """

    pattern: str = r"^(?:async\s+def\s|def\s|class\s)"
    """顶层定义的正则（多行模式，用 ``re.MULTILINE`` 匹配）。"""

    max_size: int = 1200
    """单块上限（字符）。超过就交给兜底切分器。"""

    fallback: Chunker = field(default_factory=lambda: RecursiveChunker(size=800, overlap=80))
    """超限时的兜底切分器。"""

    def split(self, document: Document) -> list[Chunk]:
        """按顶层定义切分代码。

        Args:
            document: 待切分的文档。

        Returns:
            切片列表。顺序稳定。
        """
        text = document.text
        starts = [match.start() for match in re.finditer(self.pattern, text, re.MULTILINE)]
        if not starts:
            return self.fallback.split(document)

        pieces: list[str] = []
        head = text[: starts[0]].strip()
        if head:
            pieces.append(head)
        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else len(text)
            piece = text[start:end].strip()
            if piece:
                pieces.append(piece)

        chunks: list[Chunk] = []
        for piece in pieces:
            if len(piece) <= self.max_size:
                chunks.append(_make_chunk(document, piece, len(chunks)))
                continue
            for sub in self.fallback.split(
                Document(
                    id=document.id,
                    text=piece,
                    source=document.source,
                    metadata=document.metadata,
                ),
            ):
                chunks.append(_make_chunk(document, sub.text, len(chunks)))
        return chunks


@dataclass(frozen=True)
class ExerciseChunker:
    """按**题号**切分习题。

    **一道题是完整的语义单元**：题干、选项、答案说明属于同一题。切碎之后，检索
    命中的是半道题——模型据此回答会答非所问。

    题号由**正则**给出（默认兼容 ``1.`` ``1、`` ``1)`` ``第1题``）。不同教材的
    题号写法不同，换正则即可。
    """

    pattern: str = r"^\s*(?:第\s*\d+\s*[题问]|[（(]?\d+[）).、])"
    """题号的正则（多行模式，用 ``re.MULTILINE`` 匹配）。"""

    max_size: int = 1000
    """单题上限（字符）。超出的题按段落再切。"""

    fallback: Chunker = field(default_factory=lambda: RecursiveChunker(size=800, overlap=80))
    """超限时的兜底切分器。"""

    def split(self, document: Document) -> list[Chunk]:
        """按题号切分习题。

        Args:
            document: 待切分的文档。

        Returns:
            切片列表。顺序稳定。
        """
        text = document.text
        starts = [match.start() for match in re.finditer(self.pattern, text, re.MULTILINE)]
        if not starts:
            return self.fallback.split(document)

        pieces: list[str] = []
        head = text[: starts[0]].strip()
        if head:
            pieces.append(head)
        for index, start in enumerate(starts):
            end = starts[index + 1] if index + 1 < len(starts) else len(text)
            piece = text[start:end].strip()
            if piece:
                pieces.append(piece)

        chunks: list[Chunk] = []
        for piece in pieces:
            if len(piece) <= self.max_size:
                chunks.append(_make_chunk(document, piece, len(chunks)))
                continue
            for sub in self.fallback.split(
                Document(
                    id=document.id,
                    text=piece,
                    source=document.source,
                    metadata=document.metadata,
                ),
            ):
                chunks.append(_make_chunk(document, sub.text, len(chunks)))
        return chunks


@dataclass(frozen=True)
class PolicyChunker:
    """按**材料类型**选切分策略。

    讲义按段落切、代码按函数切、习题按题号切、参考资料按定长切——**每类材料有它
    自己的语义单元**。一刀切的策略总有一半材料是错的。

    种类从 ``document.metadata["kind"]`` 取（课程文档在摄取时写进去的）。取不到
    就用 ``default``。

    **参数集中在这里**（``default_chunking_policy`` 是唯一一处数字的所在地），
    改动策略要改这一处——散落的默认值迟早会漂移。
    """

    policies: Mapping[str, Chunker] = field(default_factory=dict)
    """材料类型 → 切分器。"""

    default: Chunker = field(default_factory=lambda: RecursiveChunker(size=800, overlap=80))
    """没有匹配策略时的兜底。"""

    def split(self, document: Document) -> list[Chunk]:
        """按材料类型选策略切分。

        Args:
            document: 待切分的文档。

        Returns:
            切片列表。顺序稳定。
        """
        kind = str(document.metadata.get("kind", ""))
        chunker = self.policies.get(kind, self.default)
        return chunker.split(document)


def default_chunking_policy() -> PolicyChunker:
    """默认的切分策略集合。

    **这些数字没有实证依据**——它们是起点，不是结论。真正的约束由**不变量**锁
    定（见测试）：讲义不切断句子、代码不切断顶层定义、习题不切断一道题、切片不超
    上限、切片 id 可复现。数字会随模型与语料变化，不变量不会。

    Returns:
        策略集合。
    """
    return PolicyChunker(
        policies={
            "notes": RecursiveChunker(size=800, overlap=80),
            "exercise": ExerciseChunker(max_size=1000),
            "code": CodeChunker(max_size=1200),
            "notebook": RecursiveChunker(size=800, overlap=80),
            "reference": FixedSizeChunker(size=1000, overlap=100),
        },
        default=RecursiveChunker(size=800, overlap=80),
    )


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


__all__ = [
    "DEFAULT_SEPARATORS",
    "Chunker",
    "CodeChunker",
    "ExerciseChunker",
    "FixedSizeChunker",
    "PolicyChunker",
    "RecursiveChunker",
    "chunk_id",
    "default_chunking_policy",
]
