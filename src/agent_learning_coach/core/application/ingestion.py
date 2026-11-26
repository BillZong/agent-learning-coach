"""课程文档摄取：把外部材料变成课程文档。

**部分成功，不是整批回滚。** 一份坏文件不该毁掉整个课程库——这个口径在 RAG 侧
已经定了（``core.rag.ingestion``：「摄取不吞错：读不了的文件被跳过并记录原因」），
课程侧沿用它。

**但失败不能静默。** 报告里必须有失败清单：调用方（教师）要能看见「哪几份没进来、
为什么」。静默的部分成功比整批失败更糟——它让人以为材料齐了。

**幂等**：文档 id 由来源推导，因此重复导入同一份材料是**覆盖**而不是追加。内容
没变就**不写**（避免无谓的版本递增）；内容变了才更新。

**知识点关联不会被顺手抹掉。** 重新导入更新的是**材料本身**（正文、标题、种类）；
关联是课程侧的标注，只有请求显式给出时才改（``point_ids=None`` 表示不改，
``()`` 表示清空）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_learning_coach.core.learning.documents import (
    CourseDocument,
    DocumentKind,
    document_id,
    infer_kind,
)
from agent_learning_coach.core.learning.domain import Course, KnowledgePointId
from agent_learning_coach.core.rag.loader import load_source
from agent_learning_coach.core.rag.types import Document
from agent_learning_coach.core.storage import CourseDocumentRepository, Stored


@dataclass(frozen=True)
class DocumentRequest:
    """一次导入的请求——描述「要导入什么」。

    **它只说意图，不做解析**：解析是加载器的事（ADR-0028）。
    """

    source: str
    """来源串（文件路径）。"""

    kind: DocumentKind | None = None
    """材料种类；不给则按扩展名推断（PDF 推断不出是讲义还是习题）。"""

    point_ids: tuple[KnowledgePointId, ...] | None = None
    """要关联的知识点。

    - ``None``：**不改**已有的关联（重新导入材料不该顺手抹掉标注）。
    - 空元组：**清空**关联。
    - 非空：**设置**为这些知识点。
    """

    title: str | None = None
    """标题；不给则取文件名（去扩展名）。"""

    @classmethod
    def from_path(cls, path: str | Path, **kwargs: object) -> DocumentRequest:
        """从文件路径构造请求。

        Args:
            path: 文件路径。
            kwargs: 其余字段。

        Returns:
            导入请求。
        """
        return cls(source=str(path), **kwargs)  # type: ignore[arg-type]


@dataclass(frozen=True)
class IngestionFailure:
    """一份没进来的材料——**值对象**。"""

    source: str
    """来源串。"""

    reason: str
    """为什么没进来。"""


@dataclass(frozen=True)
class IngestionIssue:
    """一处不一致——**值对象**。

    **它不阻止导入**：材料本身是好的，只是标注有问题。挡住整份材料会让人以为
    「这份材料不能用」，而实际上它只是「关联错了」。
    """

    source: str
    """来源串。"""

    message: str
    """问题是什么。"""


@dataclass(frozen=True)
class IngestionReport:
    """一次摄取的结果——**值对象**。"""

    imported: tuple[CourseDocument, ...] = ()
    """新导入的材料。"""

    updated: tuple[CourseDocument, ...] = ()
    """内容变了、被覆盖的材料。"""

    unchanged: tuple[str, ...] = ()
    """内容没变、没有重写的材料（来源串）。"""

    failed: tuple[IngestionFailure, ...] = ()
    """没进来的材料与原因。"""

    issues: tuple[IngestionIssue, ...] = ()
    """发现的不一致。"""

    @property
    def ok(self) -> bool:
        """这批材料全都进来了。"""
        return not self.failed

    def summary(self) -> str:
        """一句话摘要。

        Returns:
            摘要文本。
        """
        return (
            f"导入 {len(self.imported)}、更新 {len(self.updated)}、"
            f"未变 {len(self.unchanged)}、失败 {len(self.failed)}、问题 {len(self.issues)}"
        )


async def ingest_course_documents(
    requests: Sequence[DocumentRequest],
    course: Course,
    *,
    repository: CourseDocumentRepository,
) -> IngestionReport:
    """把一批材料导入课程。

    **部分成功**：每份材料独立处理，一份失败不影响其余；失败清单在报告里。

    **前置条件错误整批拒绝**：课程不存在时没法谈导入——那是调用方的错误，不是某
    份材料的问题。

    Args:
        requests: 导入请求。
        course: 目标课程（提供课程标识与知识结构，用于校验知识点关联）。
        repository: 文档仓储。

    Returns:
        摄取报告。
    """
    imported: list[CourseDocument] = []
    updated: list[CourseDocument] = []
    unchanged: list[str] = []
    failed: list[IngestionFailure] = []
    issues: list[IngestionIssue] = []

    known_points = {point.id for point in course.structure.points}

    for request in requests:
        documents, reason = load_source(request.source)
        if not documents:
            failed.append(IngestionFailure(source=request.source, reason=reason or "读不出内容"))
            continue

        parsed = documents[0]
        point_ids = request.point_ids
        if point_ids is not None:
            missing = [str(point_id) for point_id in point_ids if point_id not in known_points]
            if missing:
                issues.append(
                    IngestionIssue(
                        source=request.source,
                        message=f"关联的知识点不在课程 {course.id} 里：{'、'.join(missing)}",
                    ),
                )

        title = request.title or Path(request.source).stem
        kind = request.kind or infer_kind(request.source)
        candidate = CourseDocument(
            id=document_id(request.source),
            course_id=course.id,
            title=title,
            kind=kind,
            content=parsed.text,
            source=request.source,
            point_ids=point_ids if point_ids is not None else (),
        )

        existing = await repository.load(candidate.id)
        if existing is None:
            saved = await repository.save(Stored(value=candidate, revision=0))
            imported.append(saved.value)
            continue

        if existing.value.course_id != course.id:
            failed.append(
                IngestionFailure(
                    source=request.source,
                    reason=(
                        f"这份材料已经属于课程 {existing.value.course_id}——"
                        f"材料属于一门课，不能同时属于两门"
                    ),
                ),
            )
            continue

        merged = _merge(existing.value, candidate, point_ids)
        if merged == existing.value:
            unchanged.append(request.source)
            continue

        saved = await repository.save(Stored(value=merged, revision=existing.revision))
        updated.append(saved.value)

    return IngestionReport(
        imported=tuple(imported),
        updated=tuple(updated),
        unchanged=tuple(unchanged),
        failed=tuple(failed),
        issues=tuple(issues),
    )


def _merge(
    existing: CourseDocument,
    candidate: CourseDocument,
    point_ids: tuple[KnowledgePointId, ...] | None,
) -> CourseDocument:
    """把新材料合并到已有文档上。

    **只更新材料本身**（标题、种类、正文）；知识点关联按请求给的三种语义处置。

    Args:
        existing: 已有文档。
        candidate: 新解析出来的文档。
        point_ids: 请求给出的知识点关联（``None`` 表示不改）。

    Returns:
        合并后的文档。
    """
    if point_ids is None:
        point_ids = existing.point_ids
    return CourseDocument(
        id=existing.id,
        course_id=existing.course_id,
        title=candidate.title,
        kind=candidate.kind,
        content=candidate.content,
        source=existing.source,
        point_ids=point_ids,
    )


def to_rag_document(document: CourseDocument) -> Document:
    """课程文档 → 可检索的文档。

    **课程侧的元数据进 metadata**，因此切片带上它们，检索时就能按课程与知识点
    过滤（Day 30）。材料本身只解析一次——RAG 侧与课程侧共用同一份内容。

    Args:
        document: 课程文档。

    Returns:
        带课程元数据的 RAG 文档。
    """
    return Document(
        id=document.id,
        text=document.content,
        source=document.source,
        metadata={
            "kind": document.kind,
            "course_id": str(document.course_id),
            "title": document.title,
            "point_ids": [str(point_id) for point_id in document.point_ids],
        },
    )


__all__ = [
    "DocumentRequest",
    "IngestionFailure",
    "IngestionIssue",
    "IngestionReport",
    "ingest_course_documents",
    "to_rag_document",
]
