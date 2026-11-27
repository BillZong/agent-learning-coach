"""学习领域（Learning Domain）。

Phase 1 的业务核心：学习者、课程与知识点、学习目标。

- **实体**（身份来自 id）：``Learner``、``Course``、``KnowledgePoint``、
  ``LearningGoal``。
- **值对象**（按值相等）：四类 id、``KnowledgeStructure``、
  ``KnowledgeRelation``、``TimeWindow``、``DailyEffort``、
  ``CapabilityRequirement``、``GoalTransition``。
- **聚合边界**：``Course`` 拥有知识结构（知识点 + 关系）；``LearningGoal`` 用
  id 引用学习者与课程，是三者之间的关联，而不是任何一方的子对象。
- **知识表示**：``KnowledgeStructure`` 提供派生查询——学习顺序 ``order()``、
  前置闭包 ``prerequisite_closure()``、影响范围 ``dependent_closure()``、
  主题层级 ``parent_of()`` / ``children_of()``。
- **目标状态机**：``LearningGoal`` 的状态由迁移历史派生；合法迁移表在
  ``goal.py``，每次迁移都留理由与时间。
- **跨聚合一致**：``consistency.check_goal_against_course`` 检查目标引用的
  知识点是否真实存在。
- **不变量**：知识点 id 唯一、关系端点存在且不重复、``requires`` 无环、
  ``part_of`` 是树、目标至少一条学习结果、能力要求与前置条件不重复、迁移历史
  不断链。违反时抛 ``DomainError``。

它与 Agent 运行时的关系是单向的：``to_goal_spec`` 把 ``LearningGoal`` 映射成
``GoalSpec``，运行时因此不需要认识学习领域（ADR-0013 §10、ADR-0015）。

导入约定：调用方统一从 ``agent_learning_coach.core.learning`` 导入公开符号。
"""

from agent_learning_coach.core.learning.assessment import (
    AssessmentResult,
    DegradationReport,
    ExactMatchGrader,
    Grader,
    Grading,
    assess_diagnosis,
)
from agent_learning_coach.core.learning.bridge import to_goal_spec, to_unit_goal_spec
from agent_learning_coach.core.learning.consistency import (
    GoalIssue,
    GoalIssueCode,
    ProgressIssue,
    ProgressIssueCode,
    check_goal_against_course,
    check_progress_against_course,
)
from agent_learning_coach.core.learning.diagnosis import (
    Diagnosis,
    DiagnosisPlan,
    DiagnosticEvidence,
    DiagnosticExercise,
    DiagnosticSubmission,
    DiagnosticVerdict,
    EvidenceKind,
    Verdict,
    diagnosis_plan,
)
from agent_learning_coach.core.learning.documents import (
    CourseDocument,
    DocumentKind,
    document_id,
    infer_kind,
)
from agent_learning_coach.core.learning.domain import (
    Course,
    CourseId,
    DiagnosisId,
    DomainError,
    GoalId,
    KnowledgePoint,
    KnowledgePointId,
    KnowledgeRelation,
    KnowledgeStructure,
    Learner,
    LearnerId,
    PlanId,
    PlanVersionId,
    RelationKind,
)
from agent_learning_coach.core.learning.generation import (
    ExerciseDifficulty,
    ExerciseGenerationError,
    ExerciseGenerator,
    ExerciseRequest,
    ExerciseSchema,
)
from agent_learning_coach.core.learning.goal import (
    CapabilityRequirement,
    DailyEffort,
    GoalStatus,
    GoalTransition,
    LearningGoal,
    TimeWindow,
)
from agent_learning_coach.core.learning.planning import (
    DEFAULT_UNIT_MINUTES,
    DailyLoad,
    EffortEstimator,
    LearningPlan,
    LearningUnit,
    PlanOverload,
    UniformEffort,
    generate_plan,
)
from agent_learning_coach.core.learning.progress import (
    GoalCompletion,
    LearningProgress,
    LearningState,
    PointProgress,
    PointSource,
    PointTransition,
    blocked_points,
    goal_completion,
    ready_points,
)
from agent_learning_coach.core.learning.versions import (
    PlanBasis,
    PlanDelta,
    PlanVersion,
    current_plan_version,
    fingerprint_structure,
    plan_versions,
    revise_plan,
    start_plan,
)

__all__ = [
    "DEFAULT_UNIT_MINUTES",
    "AssessmentResult",
    "CapabilityRequirement",
    "Course",
    "CourseDocument",
    "CourseId",
    "DailyEffort",
    "DailyLoad",
    "DegradationReport",
    "Diagnosis",
    "DiagnosisId",
    "DiagnosisPlan",
    "DiagnosticEvidence",
    "DiagnosticExercise",
    "DiagnosticSubmission",
    "DiagnosticVerdict",
    "DocumentKind",
    "DomainError",
    "EffortEstimator",
    "EvidenceKind",
    "ExactMatchGrader",
    "ExerciseDifficulty",
    "ExerciseGenerationError",
    "ExerciseGenerator",
    "ExerciseRequest",
    "ExerciseSchema",
    "GoalCompletion",
    "GoalId",
    "GoalIssue",
    "GoalIssueCode",
    "GoalStatus",
    "GoalTransition",
    "Grader",
    "Grading",
    "KnowledgePoint",
    "KnowledgePointId",
    "KnowledgeRelation",
    "KnowledgeStructure",
    "Learner",
    "LearnerId",
    "LearningGoal",
    "LearningPlan",
    "LearningProgress",
    "LearningState",
    "LearningUnit",
    "PlanBasis",
    "PlanDelta",
    "PlanId",
    "PlanOverload",
    "PlanVersion",
    "PlanVersionId",
    "PointProgress",
    "PointSource",
    "PointTransition",
    "ProgressIssue",
    "ProgressIssueCode",
    "RelationKind",
    "TimeWindow",
    "UniformEffort",
    "Verdict",
    "assess_diagnosis",
    "blocked_points",
    "check_goal_against_course",
    "check_progress_against_course",
    "current_plan_version",
    "diagnosis_plan",
    "document_id",
    "fingerprint_structure",
    "generate_plan",
    "goal_completion",
    "infer_kind",
    "plan_versions",
    "ready_points",
    "revise_plan",
    "start_plan",
    "to_goal_spec",
    "to_unit_goal_spec",
]
