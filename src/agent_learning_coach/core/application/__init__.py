"""应用层：用例编排。

**领域层回答「什么是真的」，应用层回答「现在做什么」。**

Phase 1 的领域层全是纯函数——判定、排计划、留版本，谁先谁后、冲突怎么办、什么时候
落库，领域层一律不决定（ADR-0015 的边界）。应用层就是把这些串起来的一方。

当前只有一个用例族：**学习闭环**（``workflow.py``）——诊断 → 进度 → 计划 → 落库，
外加 Phase 2 的 Tutor 要用的两个接口（当前该学哪个单元、记录学完一个知识点）。

它**不发明业务规则**：判定用领域层的结论，进度迁移用领域层的方法，计划用领域层
的生成器。应用层只做三件事——**决定顺序**、**决定写几次**、**决定冲突怎么办**。

导入约定：调用方统一从 ``agent_learning_coach.core.application`` 导入公开符号。
"""

from agent_learning_coach.core.application.ingestion import (
    DocumentRequest,
    IngestionFailure,
    IngestionIssue,
    IngestionReport,
    ingest_course_documents,
    to_rag_document,
)
from agent_learning_coach.core.application.retrieval import (
    MaterialQuery,
    MaterialSearch,
)
from agent_learning_coach.core.application.workflow import (
    LearningWorkflow,
    NotFoundError,
    ProgressConflict,
    SyncReport,
    WorkflowError,
    WorkflowResult,
    sync_diagnosis,
)

__all__ = [
    "DocumentRequest",
    "IngestionFailure",
    "IngestionIssue",
    "IngestionReport",
    "LearningWorkflow",
    "MaterialQuery",
    "MaterialSearch",
    "NotFoundError",
    "ProgressConflict",
    "SyncReport",
    "WorkflowError",
    "WorkflowResult",
    "ingest_course_documents",
    "sync_diagnosis",
    "to_rag_document",
]
