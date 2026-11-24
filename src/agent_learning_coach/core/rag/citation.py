"""引用。

**引用要绑定到具体的检索片段，而不是「文档」或「章节」。** 绑定得太粗，读者
无法核对；绑定得太细（精确到句子），切片粒度又不支持。

**为什么不能靠模型自觉**：模型会编造引用。它写 ``[1]`` 时，编号可能指向一段
它根本没读到的内容，也可能根本没有对应的检索结果。引用一旦不可核对，RAG 的
可追溯性就只是幻觉的一种形式。

因此本模块把引用做成**可校验的对象**：

- ``Citation``：一条引用，指向具体的切片，携带来源、序号、原文与相关度。
- ``CitationSet``：一次运行里累积的引用集合，标记（marker）唯一且稳定。
- ``CitationValidator``：拿答案去比对引用集合，报出**编造**、**不支撑**与
  **无引用**三类问题。

**校验是词面的，不是语义的**：判断「这段引用是否真的支撑这句话」需要语义
理解，那是模型的事。这里做的是更基础也更重要的一层——**引用指向的东西必须
真的存在**。一个编造的引用比一个没有引用更危险：没有引用只是「没有依据」，
编造引用是「假装有依据」。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from agent_learning_coach.core.rag.types import Chunk
from agent_learning_coach.core.text import tokenize

MARKER_PATTERN = re.compile(r"\[(\w+)\]")
"""答案里的引用标记，如 ``[1]``、``[S2]``。"""

_SENTENCE_SPLIT = re.compile(r"[。！？.!?\n]+")
"""句子切分。中文与英文标点一起处理。"""


@dataclass(frozen=True)
class Citation:
    """一条引用。

    指向**具体的切片**：``chunk_id`` 是切片身份，``source`` 是它来自哪份材料，
    ``index`` 是它在该材料里的第几段。三者合起来才能让读者核对。
    """

    marker: str
    """引用标记，如 ``"1"``。出现在答案与工具结果里，供模型引用。"""

    chunk_id: str
    """切片标识。"""

    document_id: str
    """所属文档标识。"""

    source: str
    """来源路径。读者核对时看的就是它。"""

    index: int
    """切片在文档中的序号。"""

    text: str
    """切片原文。引用指向的就是这段文字。"""

    score: float | None = None
    """相关度。越大越相关。

    为 None 表示**没有相关度**——结构化取回（按来源取回内容）不涉及相似度，
    硬填一个 0 只会让人误以为「不相关」。
    """

    metadata: Mapping[str, Any] = field(default_factory=dict)
    """附加元数据，如主题、章节。"""

    def to_dict(self) -> dict[str, Any]:
        """转成可序列化字典。

        Returns:
            字典形式。
        """
        return {
            "marker": self.marker,
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "source": self.source,
            "index": self.index,
            "text": self.text,
            "score": self.score,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Citation:
        """从字典还原。

        Args:
            data: 字典形式。

        Returns:
            引用。
        """
        return cls(
            marker=str(data.get("marker", "")),
            chunk_id=str(data.get("chunk_id", "")),
            document_id=str(data.get("document_id", "")),
            source=str(data.get("source", "")),
            index=int(data.get("index", 0)),
            text=str(data.get("text", "")),
            score=None if data.get("score") is None else float(data["score"]),
            metadata=dict(data.get("metadata") or {}),
        )

    def reference_line(self) -> str:
        """生成参考文献条目。

        Returns:
            形如 ``[1] closures.md#0（相关度 0.42）`` 的一行；没有相关度时
            省略那一段。
        """
        suffix = "" if self.score is None else f"（相关度 {self.score:.3f}）"
        return f"[{self.marker}] {self.source}#{self.index}{suffix}"


@dataclass(frozen=True)
class CitationSet:
    """引用集合。

    **标记按切片分配，且稳定**：同一个切片无论被检索多少次，标记都不变。
    这靠「先到先得」——第一次见到某个切片时给它一个序号，之后复用。

    稳定性很重要：模型看到的 ``[1]`` 必须始终指同一段文字，否则引用就失去了
    指代能力。
    """

    citations: tuple[Citation, ...] = ()
    """全部引用，按分配顺序。"""

    def with_citation(self, citation: Citation) -> CitationSet:
        """加入一条引用（标记已存在时替换）。

        Args:
            citation: 引用。

        Returns:
            新的引用集合。
        """
        replaced = False
        items: list[Citation] = []
        for existing in self.citations:
            if existing.marker == citation.marker:
                items.append(citation)
                replaced = True
            else:
                items.append(existing)
        if not replaced:
            items.append(citation)
        return CitationSet(citations=tuple(items))

    def get(self, marker: str) -> Citation | None:
        """按标记取引用。

        Args:
            marker: 引用标记。

        Returns:
            引用；不存在时返回 None。
        """
        for citation in self.citations:
            if citation.marker == marker:
                return citation
        return None

    def markers(self) -> tuple[str, ...]:
        """全部标记。

        Returns:
            标记元组。
        """
        return tuple(citation.marker for citation in self.citations)

    def __len__(self) -> int:
        """引用条数。"""
        return len(self.citations)

    def __contains__(self, marker: object) -> bool:
        """是否含某个标记。"""
        return any(citation.marker == marker for citation in self.citations)

    def to_list(self) -> list[dict[str, Any]]:
        """转成可序列化列表。

        Returns:
            字典列表。
        """
        return [citation.to_dict() for citation in self.citations]

    @classmethod
    def from_list(cls, data: Iterable[Mapping[str, Any]]) -> CitationSet:
        """从列表还原。

        Args:
            data: 字典列表。

        Returns:
            引用集合。
        """
        return cls(citations=tuple(Citation.from_dict(item) for item in data))

    def reference_list(self) -> str:
        """生成参考文献列表。

        Returns:
            逐行拼接的参考文献；没有引用时返回空串。
        """
        return "\n".join(citation.reference_line() for citation in self.citations)


@dataclass(frozen=True)
class CitationIssue:
    """一处引用问题。"""

    kind: str
    """问题类型：``"unknown_marker"`` / ``"unsupported"`` / ``"missing"``。"""

    marker: str = ""
    """涉及的标记。"""

    detail: str = ""
    """说明。"""


@dataclass(frozen=True)
class CitationReport:
    """引用校验报告。

    ``valid`` 的含义是**没有编造**——不是「引用完美」。支撑不足是警告，不是
    错误：词面重叠为 0 只说明「看不出来」，不说明「一定是错的」。
    """

    valid: bool
    """是否没有编造引用。"""

    issues: tuple[CitationIssue, ...] = ()
    """全部问题。"""

    cited: tuple[str, ...] = ()
    """答案里实际用到的标记。"""

    @property
    def errors(self) -> tuple[CitationIssue, ...]:
        """只看错误级问题（编造的引用）。"""
        return tuple(issue for issue in self.issues if issue.kind == "unknown_marker")

    @property
    def warnings(self) -> tuple[CitationIssue, ...]:
        """只看警告级问题（支撑不足、无引用）。"""
        return tuple(issue for issue in self.issues if issue.kind != "unknown_marker")


class CitationValidator:
    """校验答案里的引用。

    三类检查：

    1. **编造**（错误）：答案里的标记在引用集合里不存在——模型引用了它没有
       见过的东西。
    2. **不支撑**（警告）：引用的切片与它标注的那句话**没有任何词面交集**。
       这说明引用很可能指错了地方。
    3. **无引用**（警告）：答案里一个标记都没有——没有可追溯的依据。

    词面判断用的是 ``core.text.tokenize``（英文按词、中文按二元组），与记忆
    检索用的是同一套分词，因此两处的相关度口径一致。
    """

    def validate(self, answer: str, citations: CitationSet) -> CitationReport:
        """校验答案。

        Args:
            answer: 模型给出的答案文本。
            citations: 本次可用的引用集合。

        Returns:
            校验报告。
        """
        issues: list[CitationIssue] = []
        used = tuple(dict.fromkeys(MARKER_PATTERN.findall(answer)))

        for marker in used:
            if marker not in citations:
                issues.append(
                    CitationIssue(
                        kind="unknown_marker",
                        marker=marker,
                        detail=f"答案引用了 [{marker}]，但检索结果里没有这个引用",
                    )
                )

        if not used:
            issues.append(
                CitationIssue(
                    kind="missing",
                    marker="",
                    detail="答案里没有任何引用标记——没有可追溯的依据",
                )
            )
        else:
            for marker in used:
                citation = citations.get(marker)
                if citation is None:
                    continue
                if not self._is_supported(answer, marker, citation):
                    issues.append(
                        CitationIssue(
                            kind="unsupported",
                            marker=marker,
                            detail=(
                                f"[{marker}] 标注的句子与切片 {citation.source}#{citation.index} "
                                "没有词面交集，引用可能指错了地方"
                            ),
                        )
                    )

        return CitationReport(
            valid=not any(issue.kind == "unknown_marker" for issue in issues),
            issues=tuple(issues),
            cited=used,
        )

    def _is_supported(self, answer: str, marker: str, citation: Citation) -> bool:
        """判断引用是否与它标注的句子有词面交集。

        Args:
            answer: 答案文本。
            marker: 引用标记。
            citation: 被引用的切片。

        Returns:
            有交集返回 True。
        """
        chunk_tokens = tokenize(citation.text)
        if not chunk_tokens:
            return True
        for sentence in self._sentences_with_marker(answer, marker):
            if tokenize(sentence) & chunk_tokens:
                return True
        return False

    def _sentences_with_marker(self, answer: str, marker: str) -> list[str]:
        """取出带指定标记的句子。

        Args:
            answer: 答案文本。
            marker: 引用标记。

        Returns:
            句子列表。
        """
        sentences: list[str] = []
        for sentence in _SENTENCE_SPLIT.split(answer):
            text = sentence.strip()
            if not text:
                continue
            if marker in MARKER_PATTERN.findall(text):
                sentences.append(text)
        return sentences


class CitationRegistry:
    """按切片分配引用标记的登记簿。

    **标记稳定是硬要求**：同一个切片无论被检索多少次，标记都不变。第一次
    见到某个切片时给它一个序号，之后复用。

    稳定性为什么重要：模型看到 ``[1]`` 之后会记住它指的是哪段文字。如果同一
    段文字在不同检索里拿到不同标记，引用就失去了指代能力——读者也无法核对。

    登记簿是**有状态**的，因此每个工具实例一份。跨运行共用会让标记持续增长，
    那不影响正确性，只是不好看。
    """

    def __init__(self) -> None:
        """初始化登记簿。"""
        self._markers: dict[str, str] = {}
        self._counter = 0

    def marker_for(self, chunk_id: str) -> str:
        """取某个切片的标记，必要时分配。

        Args:
            chunk_id: 切片标识。

        Returns:
            引用标记。
        """
        marker = self._markers.get(chunk_id)
        if marker is None:
            self._counter += 1
            marker = str(self._counter)
            self._markers[chunk_id] = marker
        return marker

    def build(
        self,
        chunk: Chunk,
        *,
        score: float | None = None,
    ) -> Citation:
        """为一个切片建引用。

        Args:
            chunk: 切片。
            score: 相关度。None 表示没有相关度。

        Returns:
            引用。
        """
        return Citation(
            marker=self.marker_for(chunk.id),
            chunk_id=chunk.id,
            document_id=chunk.document_id,
            source=chunk.source,
            index=chunk.index,
            text=chunk.text,
            score=score,
            metadata=dict(chunk.metadata),
        )

    def __len__(self) -> int:
        """已分配标记的切片数。"""
        return len(self._markers)


__all__ = [
    "MARKER_PATTERN",
    "Citation",
    "CitationRegistry",
    "CitationIssue",
    "CitationReport",
    "CitationSet",
    "CitationValidator",
]
