"""RAG 的核心类型：文档、切片、带分数的切片。

**为什么把「文档」和「切片」分成两个类型？** 因为它们的生命周期与身份不同：

- **文档**是**摄取**的单位——一份文件、一个网页。它有来源、有元数据。
- **切片**是**检索**的单位——检索命中的是切片，不是文档。

混成一个类型的后果是：重新切分时无法判断「这条记录是旧切法还是新切法」，
也无法在检索结果里回溯到它属于哪份材料的哪一段。

**切片带来源**是硬要求：检索出来的内容必须能指回它的出处，否则模型无法引用，
学习者也无从核对。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Document:
    """一份待检索的材料。"""

    id: str
    """文档标识。由来源推导，保证同样的材料每次摄取得到同样的 id。"""

    text: str
    """正文。"""

    source: str
    """来源。文件路径、URL 或其它可追溯的定位串。"""

    metadata: dict[str, Any] = field(default_factory=dict)
    """附加信息。参与检索过滤。"""


@dataclass(frozen=True)
class Chunk:
    """文档的一个切片。"""

    id: str
    """切片标识。由「文档 id + 序号」推导。"""

    document_id: str
    """所属文档的标识。"""

    text: str
    """切片正文。"""

    index: int
    """在文档内的序号，从 0 开始。"""

    source: str
    """来源。冗余存一份，检索时不必回查文档。"""

    metadata: dict[str, Any] = field(default_factory=dict)
    """附加信息。参与检索过滤。"""


@dataclass(frozen=True)
class ScoredChunk:
    """带相关度的切片。"""

    chunk: Chunk
    """命中的切片。"""

    score: float
    """相关度。越大越相关。

    内存实现返回余弦相似度（-1 到 1）；pgvector 返回距离的负值转换，因此两者
    的**相对顺序一致**，但绝对值不可跨实现比较。
    """


__all__ = ["Chunk", "Document", "ScoredChunk"]
