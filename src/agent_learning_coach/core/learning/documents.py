"""课程文档：课程用的材料。

**它与 RAG 的 ``Document`` 是什么关系**：RAG 的文档是**检索的原料**（会被切分、
嵌入、进向量库）；课程文档是**材料的课程侧记录**——它回答「这门课有哪些材料、
每份材料讲的是哪些知识点」。同一个文件会被两边各记一份，但**解析只做一次**
（见 ``core.rag.loader``）。

**课程文档是独立实体，不是 ``Course`` 聚合的一部分**：导入一份材料不该重写整门
课（ADR-0015 的「一次事务只改一个聚合」），材料的生命周期（导入、更新、失效）
也与课程本身不同。它按 ``course_id`` 引用课程——**跨聚合只按 id 引用**。

**id 由来源推导**（与 ``core.rag.loader.document_id`` 同一套）：同样的材料每次导入
得到同样的 id，因此重复导入是**覆盖**而不是追加。这让「换了教材重新导入一遍」
成为安全的日常操作。

**知识点关联是可选的**：材料可以先入库、知识点关联后补。没有关联的材料照样能被
检索到，只是 Tutor 不知道它对应哪个知识点。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from agent_learning_coach.core.learning.domain import (
    CourseId,
    DomainError,
    KnowledgePointId,
    require_text,
)

DocumentKind = Literal["notes", "exercise", "code", "notebook", "reference", "other"]
"""课程材料的种类。

- ``notes``：讲义、说明文。
- ``exercise``：习题与题面材料。
- ``code``：代码文件。
- ``notebook``：Jupyter 笔记本。
- ``reference``：参考资料（API 文档、规范……）。
- ``other``：说不清或暂不分类。

**种类影响什么**：切分策略（Day 28）与检索时的呈现方式。**它不影响领域规则**——
因此它是元数据，不是领域事实。
"""

_KIND_LABELS: dict[DocumentKind, str] = {
    "notes": "讲义",
    "exercise": "习题",
    "code": "代码",
    "notebook": "笔记本",
    "reference": "参考资料",
    "other": "其它",
}


def document_id(source: str) -> str:
    """由来源推导文档标识。

    与 ``core.rag.loader.document_id`` 同一套算法——**材料在整个系统里只有一
    个身份**。这样课程侧记录与检索侧的切片能对上号。

    Args:
        source: 来源串（文件路径或 URL）。

    Returns:
        16 位十六进制标识。
    """
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return digest[:16]


def infer_kind(source: str) -> DocumentKind:
    """按来源推断材料种类。

    **推断只是默认值**：调用方可以显式指定。PDF 推断不出是讲义还是习题——它只
    知道「这是个 PDF」。

    Args:
        source: 来源串。

    Returns:
        材料种类。
    """
    lowered = source.lower()
    if lowered.endswith(".pdf"):
        return "reference"
    if lowered.endswith(".ipynb"):
        return "notebook"
    if lowered.endswith((".py", ".pyi")):
        return "code"
    if lowered.endswith((".md", ".markdown", ".rst", ".txt")):
        return "notes"
    return "other"


@dataclass(frozen=True)
class CourseDocument:
    """课程文档——**实体**。

    它是**材料**在课程里的记录：内容、来源、种类、属于哪门课、覆盖哪些知识点。

    **内容不进领域模型**：正文可能很大，它存起来、检索起来，但领域层不关心它
    怎么切、怎么嵌入（那是 Day 28 之后的事）。

    **正文可以有首尾空白**：文件末尾通常有换行。领域层那条「不允许首尾空白」的
    规则是给**标识符与短标签**用的（空白是调用方的错误）；正文不是——正文按原样
    保存，只校验「不是空白」。
    """

    id: str
    """文档标识——由来源推导。"""

    course_id: CourseId
    """所属课程。"""

    title: str
    """标题。默认取文件名。"""

    kind: DocumentKind
    """材料种类。"""

    content: str
    """解析后的正文。"""

    source: str
    """来源串（文件路径或 URL）——**可追溯**，引用与检索都要指回它。"""

    point_ids: tuple[KnowledgePointId, ...] = ()
    """覆盖的知识点，按声明顺序。

    可以为空：材料先入库、关联后补。没有关联的材料照样能被检索到，只是 Tutor
    不知道它对应哪个知识点。
    """

    def __post_init__(self) -> None:
        """校验文档内容。

        Raises:
            DomainError: 标识、标题、来源为空；内容是空白；种类未知；知识点重复。
        """
        require_text("文档标识", self.id)
        require_text("文档标题", self.title)
        require_text("文档来源", self.source)
        if not self.content.strip():
            raise DomainError("文档内容不能为空")
        if self.kind not in _KIND_LABELS:
            raise DomainError(f"未知的文档种类：{self.kind}")
        if len(set(self.point_ids)) != len(self.point_ids):
            raise DomainError("同一个知识点不能在一份文档里重复关联")

    @property
    def kind_label(self) -> str:
        """材料种类的中文标签。"""
        return _KIND_LABELS[self.kind]

    def render(self) -> str:
        """渲染成一行摘要，供日志与展示。"""
        points = "、".join(str(point_id) for point_id in self.point_ids) or "未关联知识点"
        return f"[{self.kind_label}] {self.title}（{self.source}，{points}）"


__all__ = ["CourseDocument", "DocumentKind", "document_id", "infer_kind"]
