"""领域模型与存储负载之间的形状（codec）。

**为什么单独一个模块**：领域模型本身不认识存储。`bridge.py` 已经用过一次这个
理由——映射放在外面，领域模型才干净、才能单独测试与持久化。这里同理：形状、版本
与解码规则都归存储层管，领域对象仍然只是不可变的数据。

**形状是 JSON 兼容的**：只用 ``dict`` / ``list`` / ``str`` / ``int`` / ``float`` /
``bool`` / ``None``，因此同一个负载既能进 PostgreSQL 的 ``JSONB``，也能进 JSON
文件、也能直接进测试断言。

**负载带版本号**：``schema_version``。领域形状**已经变过两次**（ADR-0017 去掉了
``GoalConstraint`` 与 ``status`` 字段，ADR-0023 去掉了 ``correct`` 与 ``basis``），
再变是必然的。没有版本号，旧负载只能靠「猜」来读；有了版本号，读不出来的负载
**明确拒绝**，而不是半懂半猜地构造出一个半对的领域对象。

**解码会重跑领域层的不变量**：``from_dict`` 通过领域对象的构造函数构造对象，
因此构造期的校验全部生效。从存储读回一份被篡改的负载，会在读回时就被拒绝——
这是「写入路径复用领域层不变量」在读侧的体现（ADR-0026）。

**状态不落库**：目标状态由迁移历史派生、知识点学习状态由迁移历史派生（ADR-0017、
ADR-0018），因此负载里**只有历史，没有状态**。存派生值就要同步两处。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from agent_learning_coach.core.learning.diagnosis import (
    Diagnosis,
    DiagnosticEvidence,
    DiagnosticExercise,
    DiagnosticSubmission,
    DiagnosticVerdict,
)
from agent_learning_coach.core.learning.domain import (
    Course,
    CourseId,
    DiagnosisId,
    GoalId,
    KnowledgePoint,
    KnowledgePointId,
    KnowledgeRelation,
    KnowledgeStructure,
    Learner,
    LearnerId,
    PlanId,
    PlanVersionId,
)
from agent_learning_coach.core.learning.goal import (
    CapabilityRequirement,
    DailyEffort,
    GoalTransition,
    LearningGoal,
    TimeWindow,
)
from agent_learning_coach.core.learning.planning import (
    DailyLoad,
    LearningPlan,
    LearningUnit,
    PlanOverload,
)
from agent_learning_coach.core.learning.progress import (
    LearningProgress,
    PointProgress,
    PointTransition,
)
from agent_learning_coach.core.learning.versions import PlanBasis, PlanDelta, PlanVersion
from agent_learning_coach.core.storage.errors import DecodeError

SCHEMA_VERSION = 1
"""当前负载版本。

**领域形状每变一次，这个号就 +1**，并在解码时拒绝不认识或更新的版本——旧负载
要么能被显式迁移，要么被明确拒绝，**不允许半懂半猜**。
"""


# --- 基础工具 -----------------------------------------------------------


def _require(payload: dict[str, Any], key: str) -> Any:
    """取一个必填字段。

    Args:
        payload: 负载。
        key: 字段名。

    Returns:
        字段值。

    Raises:
        DecodeError: 字段缺失。
    """
    if key not in payload:
        raise DecodeError(f"负载缺少字段：{key}")
    return payload[key]


def _text(payload: dict[str, Any], key: str) -> str:
    """取一个必填字符串字段。

    Args:
        payload: 负载。
        key: 字段名。

    Returns:
        字段值。

    Raises:
        DecodeError: 字段缺失或不是字符串。
    """
    value = _require(payload, key)
    if not isinstance(value, str):
        raise DecodeError(f"负载字段 {key} 必须是字符串，实际是 {type(value).__name__}")
    return value


def _int(payload: dict[str, Any], key: str) -> int:
    """取一个必填整数字段。

    Args:
        payload: 负载。
        key: 字段名。

    Returns:
        字段值。

    Raises:
        DecodeError: 字段缺失或不是整数。
    """
    value = _require(payload, key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise DecodeError(f"负载字段 {key} 必须是整数，实际是 {type(value).__name__}")
    return value


def _float(payload: dict[str, Any], key: str) -> float:
    """取一个必填浮点字段。

    Args:
        payload: 负载。
        key: 字段名。

    Returns:
        字段值。

    Raises:
        DecodeError: 字段缺失或不是数字。
    """
    value = _require(payload, key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecodeError(f"负载字段 {key} 必须是数字，实际是 {type(value).__name__}")
    return float(value)


def _optional_float(payload: dict[str, Any], key: str) -> float | None:
    """取一个可空浮点字段。

    Args:
        payload: 负载。
        key: 字段名。

    Returns:
        字段值；缺失或为 ``None`` 时返回 ``None``。

    Raises:
        DecodeError: 字段不是数字。
    """
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DecodeError(f"负载字段 {key} 必须是数字或 null，实际是 {type(value).__name__}")
    return float(value)


def _strings(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    """取一个字符串列表字段。

    Args:
        payload: 负载。
        key: 字段名。

    Returns:
        字符串元组。

    Raises:
        DecodeError: 字段不是字符串列表。
    """
    value = payload.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise DecodeError(f"负载字段 {key} 必须是字符串列表")
    return tuple(value)


def _objects(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    """取一个对象列表字段。

    Args:
        payload: 负载。
        key: 字段名。

    Returns:
        字典列表。

    Raises:
        DecodeError: 字段不是字典列表。
    """
    value = payload.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise DecodeError(f"负载字段 {key} 必须是对象列表")
    return list(value)


def _object(payload: dict[str, Any], key: str) -> dict[str, Any]:
    """取一个对象字段。

    Args:
        payload: 负载。
        key: 字段名。

    Returns:
        字典。

    Raises:
        DecodeError: 字段缺失或不是字典。
    """
    value = _require(payload, key)
    if not isinstance(value, dict):
        raise DecodeError(f"负载字段 {key} 必须是对象，实际是 {type(value).__name__}")
    return value


def _optional_object(payload: dict[str, Any], key: str) -> dict[str, Any] | None:
    """取一个可空对象字段。

    Args:
        payload: 负载。
        key: 字段名。

    Returns:
        字典；缺失或为 ``None`` 时返回 ``None``。

    Raises:
        DecodeError: 字段不是字典。
    """
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise DecodeError(f"负载字段 {key} 必须是对象或 null，实际是 {type(value).__name__}")
    return value


def _check_version(payload: dict[str, Any], kind: str) -> None:
    """校验负载版本。

    Args:
        payload: 负载。
        kind: 负载种类，用于错误信息。

    Raises:
        DecodeError: 版本缺失、不是整数，或不是当前版本。
    """
    version = payload.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise DecodeError(f"{kind} 负载缺少整数 schema_version")
    if version != SCHEMA_VERSION:
        raise DecodeError(
            f"{kind} 负载的 schema_version 是 {version}，当前只支持 {SCHEMA_VERSION}",
        )


def _envelope(kind: str, body: dict[str, Any]) -> dict[str, Any]:
    """包一层版本号。

    Args:
        kind: 负载种类。
        body: 负载主体。

    Returns:
        带 ``schema_version`` 与 ``kind`` 的负载。
    """
    return {"schema_version": SCHEMA_VERSION, "kind": kind, **body}


def _unwrap(payload: dict[str, Any], kind: str) -> dict[str, Any]:
    """校验并拆开一层版本号。

    Args:
        payload: 负载。
        kind: 期望的负载种类。

    Returns:
        负载主体（不含版本号与种类）。

    Raises:
        DecodeError: 版本不对或种类不对。
    """
    _check_version(payload, kind)
    actual = payload.get("kind")
    if actual != kind:
        raise DecodeError(f"负载种类是 {actual!r}，期望 {kind!r}")
    return {key: value for key, value in payload.items() if key not in ("schema_version", "kind")}


# --- 学习者与课程 -------------------------------------------------------


def learner_to_dict(learner: Learner) -> dict[str, Any]:
    """学习者 → 负载。

    Args:
        learner: 学习者。

    Returns:
        JSON 兼容的负载。
    """
    return _envelope(
        "learner",
        {
            "id": str(learner.id),
            "name": learner.name,
            "background": list(learner.background),
            "preferences": list(learner.preferences),
        },
    )


def learner_from_dict(payload: dict[str, Any]) -> Learner:
    """负载 → 学习者。

    Args:
        payload: 负载。

    Returns:
        学习者。

    Raises:
        DecodeError: 负载结构不对。
        DomainError: 负载里的事实不合法（构造期校验）。
    """
    body = _unwrap(payload, "learner")
    return Learner(
        id=LearnerId(_text(body, "id")),
        name=_text(body, "name"),
        background=_strings(body, "background"),
        preferences=_strings(body, "preferences"),
    )


def course_to_dict(course: Course) -> dict[str, Any]:
    """课程（含知识结构）→ 负载。

    Args:
        course: 课程。

    Returns:
        JSON 兼容的负载。
    """
    structure = course.structure
    return _envelope(
        "course",
        {
            "id": str(course.id),
            "title": course.title,
            "points": [
                {"id": str(point.id), "title": point.title, "summary": point.summary}
                for point in structure.points
            ],
            "relations": [
                {
                    "kind": relation.kind,
                    "source": str(relation.source),
                    "target": str(relation.target),
                }
                for relation in structure.relations
            ],
        },
    )


def course_from_dict(payload: dict[str, Any]) -> Course:
    """负载 → 课程（含知识结构）。

    Args:
        payload: 负载。

    Returns:
        课程。

    Raises:
        DecodeError: 负载结构不对。
        DomainError: 负载里的事实不合法（构造期校验）。
    """
    body = _unwrap(payload, "course")
    points = tuple(
        KnowledgePoint(
            id=KnowledgePointId(_text(item, "id")),
            title=_text(item, "title"),
            summary=str(item.get("summary", "")),
        )
        for item in _objects(body, "points")
    )
    relations = tuple(
        KnowledgeRelation(
            kind=_text(item, "kind"),  # type: ignore[arg-type]
            source=KnowledgePointId(_text(item, "source")),
            target=KnowledgePointId(_text(item, "target")),
        )
        for item in _objects(body, "relations")
    )
    return Course(
        id=CourseId(_text(body, "id")),
        title=_text(body, "title"),
        structure=KnowledgeStructure(points=points, relations=relations),
    )


# --- 学习目标 -----------------------------------------------------------


def _time_window_to_dict(window: TimeWindow | None) -> dict[str, Any] | None:
    """时间范围 → 负载片段。"""
    if window is None:
        return None
    return {"start": window.start.isoformat(), "end": window.end.isoformat()}


def _time_window_from_dict(payload: dict[str, Any] | None) -> TimeWindow | None:
    """负载片段 → 时间范围。"""
    if payload is None:
        return None
    return TimeWindow(
        start=date.fromisoformat(_text(payload, "start")),
        end=date.fromisoformat(_text(payload, "end")),
    )


def goal_to_dict(goal: LearningGoal) -> dict[str, Any]:
    """学习目标 → 负载。

    **状态不在负载里**：目标状态由迁移历史派生（ADR-0017）。

    Args:
        goal: 学习目标。

    Returns:
        JSON 兼容的负载。
    """
    return _envelope(
        "goal",
        {
            "id": str(goal.id),
            "learner_id": str(goal.learner_id),
            "course_id": str(goal.course_id),
            "statement": goal.statement,
            "outcomes": list(goal.outcomes),
            "requirements": [
                {"point_id": str(item.point_id), "outcome": item.outcome}
                for item in goal.requirements
            ],
            "time_window": _time_window_to_dict(goal.time_window),
            "daily_effort": (
                {"minutes_per_day": goal.daily_effort.minutes_per_day}
                if goal.daily_effort is not None
                else None
            ),
            "prerequisites": [str(item) for item in goal.prerequisites],
            "transitions": [
                {
                    "source": transition.source,
                    "target": transition.target,
                    "reason": transition.reason,
                    "at": transition.at,
                }
                for transition in goal.transitions
            ],
        },
    )


def goal_from_dict(payload: dict[str, Any]) -> LearningGoal:
    """负载 → 学习目标。

    Args:
        payload: 负载。

    Returns:
        学习目标。

    Raises:
        DecodeError: 负载结构不对。
        DomainError: 负载里的事实不合法（构造期校验）。
    """
    body = _unwrap(payload, "goal")
    effort = _optional_object(body, "daily_effort")
    return LearningGoal(
        id=GoalId(_text(body, "id")),
        learner_id=LearnerId(_text(body, "learner_id")),
        course_id=CourseId(_text(body, "course_id")),
        statement=_text(body, "statement"),
        outcomes=_strings(body, "outcomes"),
        requirements=tuple(
            CapabilityRequirement(
                point_id=KnowledgePointId(_text(item, "point_id")),
                outcome=_text(item, "outcome"),
            )
            for item in _objects(body, "requirements")
        ),
        time_window=_time_window_from_dict(_optional_object(body, "time_window")),
        daily_effort=(
            DailyEffort(minutes_per_day=_int(effort, "minutes_per_day"))
            if effort is not None
            else None
        ),
        prerequisites=tuple(KnowledgePointId(item) for item in _strings(body, "prerequisites")),
        transitions=tuple(
            GoalTransition(
                source=_text(item, "source"),  # type: ignore[arg-type]
                target=_text(item, "target"),  # type: ignore[arg-type]
                reason=_text(item, "reason"),
                at=_float(item, "at"),
            )
            for item in _objects(body, "transitions")
        ),
    )


# --- 学习进度 -----------------------------------------------------------


def _point_progress_to_dict(point: PointProgress) -> dict[str, Any]:
    """知识点进度 → 负载片段。"""
    return {
        "point_id": str(point.point_id),
        "transitions": [
            {
                "source": transition.source,
                "target": transition.target,
                "source_kind": transition.source_kind,
                "reason": transition.reason,
                "at": transition.at,
            }
            for transition in point.transitions
        ],
    }


def _point_progress_from_dict(payload: dict[str, Any]) -> PointProgress:
    """负载片段 → 知识点进度。"""
    return PointProgress(
        point_id=KnowledgePointId(_text(payload, "point_id")),
        transitions=tuple(
            PointTransition(
                source=_text(item, "source"),  # type: ignore[arg-type]
                target=_text(item, "target"),  # type: ignore[arg-type]
                source_kind=_text(item, "source_kind"),  # type: ignore[arg-type]
                reason=_text(item, "reason"),
                at=_float(item, "at"),
            )
            for item in _objects(payload, "transitions")
        ),
    )


def progress_to_dict(progress: LearningProgress) -> dict[str, Any]:
    """学习进度 → 负载。

    **学习状态不在负载里**：状态由迁移历史派生（ADR-0018）。

    Args:
        progress: 学习进度。

    Returns:
        JSON 兼容的负载。
    """
    return _envelope(
        "progress",
        {
            "learner_id": str(progress.learner_id),
            "course_id": str(progress.course_id),
            "points": [_point_progress_to_dict(point) for point in progress.points],
        },
    )


def progress_from_dict(payload: dict[str, Any]) -> LearningProgress:
    """负载 → 学习进度。

    Args:
        payload: 负载。

    Returns:
        学习进度。

    Raises:
        DecodeError: 负载结构不对。
        DomainError: 负载里的事实不合法（构造期校验）。
    """
    body = _unwrap(payload, "progress")
    return LearningProgress(
        learner_id=LearnerId(_text(body, "learner_id")),
        course_id=CourseId(_text(body, "course_id")),
        points=tuple(_point_progress_from_dict(item) for item in _objects(body, "points")),
    )


# --- 诊断 ---------------------------------------------------------------


def _exercise_to_dict(exercise: DiagnosticExercise) -> dict[str, Any]:
    """诊断题 → 负载片段。"""
    return {
        "id": exercise.id,
        "prompt": exercise.prompt,
        "point_id": str(exercise.point_id) if exercise.point_id is not None else None,
        "expected_answer": exercise.expected_answer,
        "difficulty": exercise.difficulty,
        "rationale": exercise.rationale,
    }


def _exercise_from_dict(payload: dict[str, Any]) -> DiagnosticExercise:
    """负载片段 → 诊断题。"""
    point_id = payload.get("point_id")
    return DiagnosticExercise(
        id=_text(payload, "id"),
        prompt=_text(payload, "prompt"),
        point_id=KnowledgePointId(point_id) if point_id is not None else None,
        expected_answer=str(payload.get("expected_answer", "")),
        difficulty=str(payload.get("difficulty", "intermediate")),
        rationale=str(payload.get("rationale", "")),
    )


def exercise_to_dict(exercise: DiagnosticExercise) -> dict[str, Any]:
    """诊断题 → 负载。

    Args:
        exercise: 诊断题。

    Returns:
        JSON 兼容的负载。
    """
    return _envelope("exercise", _exercise_to_dict(exercise))


def exercise_from_dict(payload: dict[str, Any]) -> DiagnosticExercise:
    """负载 → 诊断题。

    Args:
        payload: 负载。

    Returns:
        诊断题。

    Raises:
        DecodeError: 负载结构不对。
        DomainError: 负载里的事实不合法（构造期校验）。
    """
    body = _unwrap(payload, "exercise")
    return _exercise_from_dict(body)


def _evidence_to_dict(evidence: DiagnosticEvidence) -> dict[str, Any]:
    """诊断证据 → 负载片段。"""
    return {
        "exercise": _exercise_to_dict(evidence.exercise),
        "submission": {
            "exercise_id": evidence.submission.exercise_id,
            "answer": evidence.submission.answer,
        },
        "grading": evidence.grading,
        "point_id": str(evidence.point_id) if evidence.point_id is not None else None,
    }


def _evidence_from_dict(payload: dict[str, Any]) -> DiagnosticEvidence:
    """负载片段 → 诊断证据。"""
    point_id = payload.get("point_id")
    return DiagnosticEvidence(
        exercise=_exercise_from_dict(_object(payload, "exercise")),
        submission=DiagnosticSubmission(
            exercise_id=_text(_object(payload, "submission"), "exercise_id"),
            answer=_text(_object(payload, "submission"), "answer"),
        ),
        grading=_text(payload, "grading"),  # type: ignore[arg-type]
        point_id=KnowledgePointId(point_id) if point_id is not None else None,
    )


def diagnosis_to_dict(diagnosis: Diagnosis) -> dict[str, Any]:
    """诊断 → 负载。

    Args:
        diagnosis: 诊断。

    Returns:
        JSON 兼容的负载。
    """
    return _envelope(
        "diagnosis",
        {
            "id": str(diagnosis.id),
            "learner_id": str(diagnosis.learner_id),
            "goal_id": str(diagnosis.goal_id),
            "verdicts": [
                {
                    "point_id": str(verdict.point_id),
                    "verdict": verdict.verdict,
                    "reason": verdict.reason,
                    "evidence": [_evidence_to_dict(item) for item in verdict.evidence],
                }
                for verdict in diagnosis.verdicts
            ],
            "evidence": [_evidence_to_dict(item) for item in diagnosis.evidence],
        },
    )


def diagnosis_from_dict(payload: dict[str, Any]) -> Diagnosis:
    """负载 → 诊断。

    Args:
        payload: 负载。

    Returns:
        诊断。

    Raises:
        DecodeError: 负载结构不对。
        DomainError: 负载里的事实不合法（构造期校验）。
    """
    body = _unwrap(payload, "diagnosis")
    return Diagnosis(
        id=DiagnosisId(_text(body, "id")),
        learner_id=LearnerId(_text(body, "learner_id")),
        goal_id=GoalId(_text(body, "goal_id")),
        verdicts=tuple(
            DiagnosticVerdict(
                point_id=KnowledgePointId(_text(item, "point_id")),
                verdict=_text(item, "verdict"),  # type: ignore[arg-type]
                reason=_text(item, "reason"),
                evidence=tuple(_evidence_from_dict(item) for item in _objects(item, "evidence")),
            )
            for item in _objects(body, "verdicts")
        ),
        evidence=tuple(_evidence_from_dict(item) for item in _objects(body, "evidence")),
    )


# --- 计划版本 -----------------------------------------------------------


def _unit_to_dict(unit: LearningUnit) -> dict[str, Any]:
    """学习单元 → 负载片段。"""
    return {
        "point_id": str(unit.point_id),
        "title": unit.title,
        "reason": unit.reason,
        "outcomes": list(unit.outcomes),
        "estimated_minutes": unit.estimated_minutes,
        "depends_on": [str(item) for item in unit.depends_on],
        "day": unit.day,
    }


def _unit_from_dict(payload: dict[str, Any]) -> LearningUnit:
    """负载片段 → 学习单元。"""
    day = payload.get("day")
    return LearningUnit(
        point_id=KnowledgePointId(_text(payload, "point_id")),
        title=_text(payload, "title"),
        reason=_text(payload, "reason"),
        outcomes=_strings(payload, "outcomes"),
        estimated_minutes=_int(payload, "estimated_minutes"),
        depends_on=tuple(KnowledgePointId(item) for item in _strings(payload, "depends_on")),
        day=int(day) if isinstance(day, int) and not isinstance(day, bool) else None,
    )


def _daily_load_to_dict(load: DailyLoad) -> dict[str, Any]:
    """每天负载 → 负载片段。"""
    return {
        "day": load.day,
        "minutes": load.minutes,
        "capacity": load.capacity,
        "within_window": load.within_window,
    }


def _daily_load_from_dict(payload: dict[str, Any]) -> DailyLoad:
    """负载片段 → 每天负载。"""
    capacity = payload.get("capacity")
    return DailyLoad(
        day=_int(payload, "day"),
        minutes=_int(payload, "minutes"),
        capacity=int(capacity)
        if isinstance(capacity, int) and not isinstance(capacity, bool)
        else None,
        within_window=bool(payload.get("within_window", True)),
    )


def _overload_to_dict(overload: PlanOverload | None) -> dict[str, Any] | None:
    """超载报告 → 负载片段。"""
    if overload is None:
        return None
    return {
        "total_minutes": overload.total_minutes,
        "required_days": overload.required_days,
        "available_days": overload.available_days,
        "daily_capacity": overload.daily_capacity,
    }


def _overload_from_dict(payload: dict[str, Any] | None) -> PlanOverload | None:
    """负载片段 → 超载报告。"""
    if payload is None:
        return None
    return PlanOverload(
        total_minutes=_int(payload, "total_minutes"),
        required_days=_int(payload, "required_days"),
        available_days=_int(payload, "available_days"),
        daily_capacity=_int(payload, "daily_capacity"),
    )


def _plan_to_dict(plan: LearningPlan) -> dict[str, Any]:
    """学习计划 → 负载片段。"""
    return {
        "goal_id": str(plan.goal_id),
        "units": [_unit_to_dict(unit) for unit in plan.units],
        "days": [_daily_load_to_dict(load) for load in plan.days],
        "overload": _overload_to_dict(plan.overload),
        "unmet_prerequisites": [str(item) for item in plan.unmet_prerequisites],
        "diagnosis_id": str(plan.diagnosis_id) if plan.diagnosis_id is not None else None,
    }


def _plan_from_dict(payload: dict[str, Any]) -> LearningPlan:
    """负载片段 → 学习计划。"""
    diagnosis_id = payload.get("diagnosis_id")
    return LearningPlan(
        goal_id=GoalId(_text(payload, "goal_id")),
        units=tuple(_unit_from_dict(item) for item in _objects(payload, "units")),
        days=tuple(_daily_load_from_dict(item) for item in _objects(payload, "days")),
        overload=_overload_from_dict(_optional_object(payload, "overload")),
        unmet_prerequisites=tuple(
            KnowledgePointId(item) for item in _strings(payload, "unmet_prerequisites")
        ),
        diagnosis_id=DiagnosisId(diagnosis_id) if diagnosis_id is not None else None,
    )


def _basis_to_dict(basis: PlanBasis) -> dict[str, Any]:
    """生成依据 → 负载片段。"""
    return {
        "goal_id": str(basis.goal_id),
        "learner_id": str(basis.learner_id),
        "course_id": str(basis.course_id),
        "required_points": [str(item) for item in basis.required_points],
        "learned_points": [str(item) for item in basis.learned_points],
        "diagnosis_id": str(basis.diagnosis_id) if basis.diagnosis_id is not None else None,
        "structure_fingerprint": basis.structure_fingerprint,
    }


def _basis_from_dict(payload: dict[str, Any]) -> PlanBasis:
    """负载片段 → 生成依据。"""
    diagnosis_id = payload.get("diagnosis_id")
    return PlanBasis(
        goal_id=GoalId(_text(payload, "goal_id")),
        learner_id=LearnerId(_text(payload, "learner_id")),
        course_id=CourseId(_text(payload, "course_id")),
        required_points=tuple(
            KnowledgePointId(item) for item in _strings(payload, "required_points")
        ),
        learned_points=tuple(
            KnowledgePointId(item) for item in _strings(payload, "learned_points")
        ),
        diagnosis_id=DiagnosisId(diagnosis_id) if diagnosis_id is not None else None,
        structure_fingerprint=str(payload.get("structure_fingerprint", "")),
    )


def _delta_to_dict(delta: PlanDelta | None) -> dict[str, Any] | None:
    """差异 → 负载片段。"""
    if delta is None:
        return None
    return {
        "added": [str(item) for item in delta.added],
        "removed": [str(item) for item in delta.removed],
        "changed": [str(item) for item in delta.changed],
    }


def _delta_from_dict(payload: dict[str, Any] | None) -> PlanDelta | None:
    """负载片段 → 差异。"""
    if payload is None:
        return None
    return PlanDelta(
        added=tuple(KnowledgePointId(item) for item in _strings(payload, "added")),
        removed=tuple(KnowledgePointId(item) for item in _strings(payload, "removed")),
        changed=tuple(KnowledgePointId(item) for item in _strings(payload, "changed")),
    )


def plan_version_to_dict(version: PlanVersion) -> dict[str, Any]:
    """计划版本 → 负载。

    Args:
        version: 计划版本。

    Returns:
        JSON 兼容的负载。
    """
    return _envelope(
        "plan_version",
        {
            "id": str(version.id),
            "plan_id": str(version.plan_id),
            "number": version.number,
            "plan": _plan_to_dict(version.plan),
            "basis": _basis_to_dict(version.basis),
            "reason": version.reason,
            "at": version.at,
            "delta": _delta_to_dict(version.delta),
        },
    )


def plan_version_from_dict(payload: dict[str, Any]) -> PlanVersion:
    """负载 → 计划版本。

    Args:
        payload: 负载。

    Returns:
        计划版本。

    Raises:
        DecodeError: 负载结构不对。
        DomainError: 负载里的事实不合法（构造期校验）。
    """
    body = _unwrap(payload, "plan_version")
    return PlanVersion(
        id=PlanVersionId(_text(body, "id")),
        plan_id=PlanId(_text(body, "plan_id")),
        number=_int(body, "number"),
        plan=_plan_from_dict(_object(body, "plan")),
        basis=_basis_from_dict(_object(body, "basis")),
        reason=_text(body, "reason"),
        at=_float(body, "at"),
        delta=_delta_from_dict(_optional_object(body, "delta")),
    )


__all__ = [
    "SCHEMA_VERSION",
    "course_from_dict",
    "course_to_dict",
    "diagnosis_from_dict",
    "diagnosis_to_dict",
    "exercise_from_dict",
    "exercise_to_dict",
    "goal_from_dict",
    "goal_to_dict",
    "learner_from_dict",
    "learner_to_dict",
    "plan_version_from_dict",
    "plan_version_to_dict",
    "progress_from_dict",
    "progress_to_dict",
]
