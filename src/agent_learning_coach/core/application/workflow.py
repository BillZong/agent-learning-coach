"""应用层：用例编排——诊断 → 进度 → 计划 → 落库。

**领域层回答「什么是真的」，应用层回答「现在做什么」。** Phase 1 的领域层全是纯
函数：``assess_diagnosis`` 判、``generate_plan`` 排、``start_plan`` / ``revise_plan``
留痕。它们**不读、不写、不串联**——谁先谁后、冲突怎么办、什么时候落库，领域层
一律不决定（ADR-0015 的边界、ADR-0020 与 ADR-0024 把选边推给应用层）。

本模块就是那条链。

**为什么这次要写它**：Phase 0–1 的验收里，「能够根据学习者背景开展初始诊断」一直
只到「领域层有机制」为止——机制齐了，**没有人把它们串起来**。Phase 1.5 把它补上
（遗留问题台账 §2.1 P1-B）。

**三条规则**：

1. **先写进度，后写计划。** 计划是从进度算出来的；反过来写，会写出一份「依据是
   假的」计划（进度没写进去，计划却已经声称基于它）。这个顺序还让重试是安全的：
   进度已同步、计划没写成时，重跑一次同步是**幂等**的（已学会的点不会重复迁移）。
2. **一次事务只改一个聚合。** 这里有两次写（进度、计划版本），但它们是**两次独立
   的聚合写**，各带各的版本号，不是跨聚合事务。中间失败时状态是半更新的——这被
   显式承认，而不是藏起来。
3. **矛盾要报告，不静默。** 诊断与进度不一致时，按**领域模型允许的迁移**处置，
   并把矛盾写进报告交给调用方。

**同步的处置规则**（`sync_diagnosis`）：

| 诊断 | 进度 | 处置 | 矛盾 |
| ---- | ---- | ---- | ---- |
| 会 | 未开始 | 同步为「已学会」（来源 assessed） | 否 |
| 会 | 学习中 | 不动 | 否 |
| 会 | 已学会 | 不动 | 否 |
| 不会 / 未判定 | 未开始 / 学习中 | 不动 | 否 |
| 不会 / 未判定 | **已学会** | **不动** | **是** |

最后一行为什么不动：**领域模型不允许「已学会」退回「未开始」**（ADR-0018 的迁移
表刻意没有这条边，回退会让「学过没有」变得不可判定）。因此应用层能做的不是
「信诊断」，而是**保留进度并报告矛盾**——要不要安排复习是另一个用例的事。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from agent_learning_coach.core.learning.diagnosis import Diagnosis, Verdict
from agent_learning_coach.core.learning.domain import (
    DiagnosisId,
    GoalId,
    KnowledgePointId,
    KnowledgeStructure,
    PlanId,
    PlanVersionId,
)
from agent_learning_coach.core.learning.goal import LearningGoal
from agent_learning_coach.core.learning.planning import EffortEstimator, LearningUnit
from agent_learning_coach.core.learning.progress import LearningProgress, LearningState
from agent_learning_coach.core.learning.versions import (
    PlanVersion,
    current_plan_version,
    revise_plan,
    start_plan,
)
from agent_learning_coach.core.storage import (
    ConflictError,
    CourseRepository,
    DiagnosisRepository,
    GoalRepository,
    PlanVersionRepository,
    ProgressRepository,
    Stored,
)


class WorkflowError(Exception):
    """用例执行失败。"""


class NotFoundError(WorkflowError):
    """引用的记录不存在。"""


@dataclass(frozen=True)
class ProgressConflict:
    """诊断与进度的矛盾——**值对象**。

    它是**被报告的事实**，不是一个待解决的错误：处置已经做完了（保留进度），
    这里记下「矛盾在哪、两边各说了什么」。
    """

    point_id: KnowledgePointId
    """矛盾在哪个知识点上。"""

    verdict: Verdict
    """诊断怎么说。"""

    state: LearningState
    """进度怎么说。"""

    resolution: str
    """怎么处置的，以及为什么。"""


@dataclass(frozen=True)
class SyncReport:
    """一次「诊断 → 进度」同步的结果——**值对象**。"""

    applied: tuple[KnowledgePointId, ...] = ()
    """实际同步进进度的知识点（诊断说会、进度说还没学）。"""

    conflicts: tuple[ProgressConflict, ...] = ()
    """诊断与进度矛盾、因而**没有**同步的知识点。"""

    @property
    def is_empty(self) -> bool:
        """这次同步什么也没做。"""
        return not self.applied and not self.conflicts


@dataclass(frozen=True)
class WorkflowResult:
    """一次用例的产出——**值对象**。"""

    goal_id: GoalId
    """目标标识。"""

    diagnosis_id: DiagnosisId | None
    """用到的诊断；没有诊断时为空。"""

    progress: Stored[LearningProgress]
    """落库后的进度。"""

    plan_version: Stored[PlanVersion]
    """落库后的计划版本。"""

    sync: SyncReport
    """同步报告（含矛盾）。"""

    progress_written: bool
    """进度是否真的写了。内容没变就不写——避免无谓的版本递增。"""

    plan_written: bool
    """计划版本是否真的产生了。内容没变就**不产生新版本**——空版本是噪音。"""


def sync_diagnosis(
    diagnosis: Diagnosis,
    progress: LearningProgress,
    *,
    at: float,
) -> tuple[LearningProgress, SyncReport]:
    """把诊断判定同步进进度。

    **处置规则见模块文档**。要点：诊断说「会」而进度说「还没学」→ 同步为「已学
    会」（来源 assessed）；诊断说「不会」而进度说「已学会」→ **保留进度并报告矛
    盾**——领域模型不允许把已学会退回未学会（ADR-0018）。

    这个函数是**纯函数**：不改传入的对象，返回新的进度与报告。

    Args:
        diagnosis: 诊断。
        progress: 当前进度。
        at: 同步时刻（epoch 秒），由调用方提供——领域层与应用层都不读时钟。

    Returns:
        同步后的进度与同步报告。
    """
    updated = progress
    applied: list[KnowledgePointId] = []
    conflicts: list[ProgressConflict] = []

    for verdict in diagnosis.verdicts:
        point = updated.point(verdict.point_id)
        state: LearningState = point.state if point is not None else "not_started"

        if verdict.verdict == "known":
            if state == "not_started":
                updated = updated.assess(
                    verdict.point_id,
                    at=at,
                    reason=f"诊断判定已掌握：{verdict.reason}",
                )
                applied.append(verdict.point_id)
            continue

        if state == "learned":
            conflicts.append(
                ProgressConflict(
                    point_id=verdict.point_id,
                    verdict=verdict.verdict,
                    state=state,
                    resolution=(
                        f"诊断说{verdict.label}，进度说已学会——保留进度："
                        f"领域模型不允许把已学会退回未学会（ADR-0018）"
                    ),
                ),
            )

    return updated, SyncReport(applied=tuple(applied), conflicts=tuple(conflicts))


class LearningWorkflow:
    """学习闭环的用例编排。

    它**编排**领域层与仓储，不自己发明业务规则：判定用 ``assess_diagnosis`` 的
    结论，进度迁移用领域层的方法，计划用 ``generate_plan`` / ``start_plan`` /
    ``revise_plan``。应用层做的只有三件事：**决定顺序**、**决定写几次**、**决定
    冲突怎么办**。
    """

    def __init__(
        self,
        *,
        goals: GoalRepository,
        progresses: ProgressRepository,
        diagnoses: DiagnosisRepository,
        plans: PlanVersionRepository,
        courses: CourseRepository,
        max_sync_attempts: int = 3,
    ) -> None:
        """初始化编排器。

        Args:
            goals: 目标仓储。
            progresses: 进度仓储。
            diagnoses: 诊断仓储。
            plans: 计划版本仓储。
            courses: 课程仓储。
            max_sync_attempts: 进度写入遇到乐观锁冲突时的最大重试次数。
        """
        self._goals = goals
        self._progresses = progresses
        self._diagnoses = diagnoses
        self._plans = plans
        self._courses = courses
        self._max_sync_attempts = max_sync_attempts

    async def diagnose_and_plan(
        self,
        goal_id: GoalId,
        *,
        at: float,
        reason: str,
        diagnosis_id: DiagnosisId | None = None,
        plan_id: PlanId | None = None,
        version_id: PlanVersionId | None = None,
        estimator: EffortEstimator | None = None,
    ) -> WorkflowResult:
        """诊断 → 进度 → 计划 → 落库。

        步骤：

        1. 读目标与课程（拿知识结构）；
        2. **选诊断**：给了 ``diagnosis_id`` 就用它，否则取该目标**最新**的一份
           （按诊断发生的时间；时间相同则按标识，保证确定性）；
        3. 把诊断判定同步进进度，**先写进度**；
        4. 用同步后的进度生成计划，**再写计划版本**：目标已有计划线就修订它，否则
           开一条新的。

        Args:
            goal_id: 目标标识。
            at: 本次发生时刻（epoch 秒），由调用方提供。
            reason: 这次为什么重新规划，必填。
            diagnosis_id: 用哪一份诊断；不给则取最新一份。
            plan_id: 新计划线的标识；只在开新线时用，不给则自动生成。
            version_id: 新版本的标识；不给则自动生成。
            estimator: 估时器。

        Returns:
            用例产出。

        Raises:
            NotFoundError: 目标或课程不存在。
            WorkflowError: 重试若干次后仍然写不进进度。
            ConflictError: 计划版本写入冲突（同一版本号已存在）。
        """
        goal, structure = await self._load_goal_and_structure(goal_id)
        diagnosis = await self._pick_diagnosis(goal, diagnosis_id)

        progress_record = await self._progresses.load(goal.learner_id, goal.course_id)
        progress = (
            progress_record.value
            if progress_record is not None
            else LearningProgress(learner_id=goal.learner_id, course_id=goal.course_id)
        )
        revision = progress_record.revision if progress_record is not None else 0

        if diagnosis is None:
            synced, report = progress, SyncReport()
        else:
            synced, report = sync_diagnosis(diagnosis, progress, at=at)

        written = False
        if synced != progress:
            saved = await self._save_progress_with_retry(
                goal,
                synced,
                revision,
                diagnosis,
                at=at,
            )
            progress_record = saved
            written = True

        plan_record, plan_written = await self._write_plan(
            goal,
            structure,
            progress_record.value if progress_record is not None else progress,
            diagnosis=diagnosis,
            plan_id=plan_id,
            version_id=version_id,
            at=at,
            reason=reason,
            estimator=estimator,
        )

        return WorkflowResult(
            goal_id=goal.id,
            diagnosis_id=diagnosis.id if diagnosis is not None else None,
            progress=progress_record
            if progress_record is not None
            else Stored(value=progress, revision=0),
            plan_version=plan_record,
            sync=report,
            progress_written=written,
            plan_written=plan_written,
        )

    async def record_learning(
        self,
        goal_id: GoalId,
        point_id: KnowledgePointId,
        *,
        at: float,
        reason: str,
        estimator: EffortEstimator | None = None,
    ) -> WorkflowResult:
        """记录一次学习完成，并**重新规划**。

        这是 Phase 2 的 Tutor 最常调用的用例：学完一个单元 → 进度更新 → 计划里
        少一个点 → 生成新版本。

        **「学完了」蕴含「学过」**：领域模型要求「开始 → 学完」两步（ADR-0018 的
        迁移表刻意没有「未开始 → 已学会」这条边——否则「学过没有」与「本来就会」
        分不开）。因此知识点还没开始时，用例先补上「开始学习」这一步。

        Args:
            goal_id: 目标标识。
            point_id: 学完的知识点。
            at: 发生时刻（epoch 秒），由调用方提供。
            reason: 学习完成的理由，必填。
            estimator: 估时器。

        Returns:
            用例产出。

        Raises:
            NotFoundError: 目标或课程不存在。
            DomainError: 状态迁移不合法（例如重复完成同一个知识点）。
            WorkflowError: 重试若干次后仍然写不进进度。
        """
        goal, structure = await self._load_goal_and_structure(goal_id)
        progress_record = await self._progresses.load(goal.learner_id, goal.course_id)
        progress = (
            progress_record.value
            if progress_record is not None
            else LearningProgress(learner_id=goal.learner_id, course_id=goal.course_id)
        )
        revision = progress_record.revision if progress_record is not None else 0

        moved = progress
        point = moved.point(point_id)
        state = point.state if point is not None else "not_started"
        if state == "not_started":
            moved = moved.start(point_id, at=at, reason=f"开始学习：{reason}")
        moved = moved.complete(point_id, at=at, reason=reason)
        saved = await self._save_progress_with_retry(goal, moved, revision, None, at=at)

        plan_record, plan_written = await self._write_plan(
            goal,
            structure,
            saved.value,
            diagnosis=None,
            plan_id=None,
            version_id=None,
            at=at,
            reason=reason,
            estimator=estimator,
        )

        return WorkflowResult(
            goal_id=goal.id,
            diagnosis_id=None,
            progress=saved,
            plan_version=plan_record,
            sync=SyncReport(),
            progress_written=True,
            plan_written=plan_written,
        )

    async def current_unit(self, goal_id: GoalId) -> LearningUnit | None:
        """当前该学的单元——Phase 2 的 Tutor 从这里开始。

        取当前计划版本的**第一个单元**（计划按依赖顺序排，第一个就是能开始学
        的那个）。

        Args:
            goal_id: 目标标识。

        Returns:
            学习单元；还没有计划时为 ``None``。
        """
        versions = await self._plans.list_for_goal(goal_id)
        current = current_plan_version([record.value for record in versions], goal_id=goal_id)
        if current is None or not current.plan.units:
            return None
        return current.plan.units[0]

    # --- 内部步骤 -------------------------------------------------------

    async def _load_goal_and_structure(
        self,
        goal_id: GoalId,
    ) -> tuple[LearningGoal, KnowledgeStructure]:
        """读目标与课程。

        Args:
            goal_id: 目标标识。

        Returns:
            目标与知识结构。

        Raises:
            NotFoundError: 目标或课程不存在。
        """
        goal_record = await self._goals.load(goal_id)
        if goal_record is None:
            raise NotFoundError(f"目标 {goal_id} 不存在")
        goal = goal_record.value

        course_record = await self._courses.load(goal.course_id)
        if course_record is None:
            raise NotFoundError(f"课程 {goal.course_id} 不存在")
        return goal, course_record.value.structure

    async def _pick_diagnosis(
        self,
        goal: LearningGoal,
        diagnosis_id: DiagnosisId | None,
    ) -> Diagnosis | None:
        """选本次要用的诊断。

        **给了 id 就用它**；没给就取该目标**最新**的一份——按诊断发生的时间，
        时间相同则按标识（保证确定性）。

        Args:
            goal: 目标。
            diagnosis_id: 指定的诊断标识。

        Returns:
            诊断；该目标还没有诊断时为 ``None``。
        """
        if diagnosis_id is not None:
            record = await self._diagnoses.load(diagnosis_id)
            if record is None:
                raise NotFoundError(f"诊断 {diagnosis_id} 不存在")
            return record.value

        records = await self._diagnoses.list_for_goal(goal.id)
        if not records:
            return None
        newest = max(records, key=lambda record: (record.value.at, str(record.value.id)))
        return newest.value

    async def _save_progress_with_retry(
        self,
        goal: LearningGoal,
        progress: LearningProgress,
        revision: int,
        diagnosis: Diagnosis | None,
        *,
        at: float,
    ) -> Stored[LearningProgress]:
        """写进度；遇到乐观锁冲突就**重读、重算、再写**。

        重试的语义是「以最新的进度为基准重新同步」——不是盲目重试同一次计算，
        否则写的还是基于旧版本的结果。

        Args:
            goal: 目标。
            progress: 要写的进度。
            revision: 写的时候携带的版本号。
            diagnosis: 诊断（有则冲突时重新同步）。
            at: 同步时刻。

        Returns:
            写入后的记录。

        Raises:
            WorkflowError: 重试若干次后仍然冲突。
        """
        for _ in range(self._max_sync_attempts):
            try:
                return await self._progresses.save(Stored(value=progress, revision=revision))
            except ConflictError:
                fresh = await self._progresses.load(goal.learner_id, goal.course_id)
                if fresh is None:
                    progress = LearningProgress(
                        learner_id=goal.learner_id,
                        course_id=goal.course_id,
                    )
                    revision = 0
                else:
                    progress = fresh.value
                    revision = fresh.revision
                if diagnosis is not None:
                    progress, _ = sync_diagnosis(diagnosis, progress, at=at)
        raise WorkflowError(
            f"目标 {goal.id} 的进度连续 {self._max_sync_attempts} 次写入冲突，已放弃",
        )

    async def _write_plan(
        self,
        goal: LearningGoal,
        structure: KnowledgeStructure,
        progress: LearningProgress,
        *,
        diagnosis: Diagnosis | None,
        plan_id: PlanId | None,
        version_id: PlanVersionId | None,
        at: float,
        reason: str,
        estimator: EffortEstimator | None,
    ) -> tuple[Stored[PlanVersion], bool]:
        """生成并写入计划版本。

        目标已有计划线就**修订**它（版本号 +1），否则**开一条新的**（版本 1）。
        「当前计划线」的判定复用 ADR-0025 的规则：同一目标下最近开始的那条。

        **内容没变就不产生新版本**：算出来的计划与当前版本**完全相同**时，返回当前
        版本、一个字都不写。空版本是噪音——它不回答「计划变了没有」（``delta`` 已
        经回答了），只在历史里堆出一串内容相同的记录，还让人怀疑是不是出了 bug。

        比较的是**整个计划内容**（含 ``diagnosis_id``）：同一份诊断 + 同样的进度
        ⇒ 相同 ⇒ 不出版本；换了诊断即使结果一样也出版本，因为**依据变了**——那是
        有信息量的。

        Args:
            goal: 目标。
            structure: 知识结构。
            progress: 进度（已同步）。
            diagnosis: 诊断（写入依据与单元理由）。
            plan_id: 新计划线的标识。
            version_id: 新版本的标识。
            at: 发生时刻。
            reason: 这次为什么重新规划。
            estimator: 估时器。

        Returns:
            计划版本记录，以及是否真的写了。

        Raises:
            WorkflowError: 版本写入冲突——基线已过期。
        """
        versions = await self._plans.list_for_goal(goal.id)
        current = current_plan_version([record.value for record in versions], goal_id=goal.id)
        current_record = (
            next((record for record in versions if record.value.id == current.id), None)
            if current is not None
            else None
        )

        if current is None:
            version = start_plan(
                goal,
                structure,
                progress,
                plan_id=plan_id or PlanId(f"plan-{uuid.uuid4().hex[:12]}"),
                version_id=version_id or PlanVersionId(f"v-{uuid.uuid4().hex[:12]}"),
                reason=reason,
                at=at,
                diagnosis=diagnosis,
                estimator=estimator,
            )
        else:
            line = tuple(
                record.value for record in versions if record.value.plan_id == current.plan_id
            )
            version = revise_plan(
                line,
                goal,
                structure,
                progress,
                version_id=version_id or PlanVersionId(f"v-{uuid.uuid4().hex[:12]}"),
                reason=reason,
                at=at,
                diagnosis=diagnosis,
                estimator=estimator,
            )
            if current_record is not None and version.plan == current.plan:
                return current_record, False

        try:
            return await self._plans.save(Stored(value=version, revision=0)), True
        except ConflictError as exc:
            base = f"（基于 v{current.number} 生成）" if current is not None else ""
            raise WorkflowError(
                f"目标 {goal.id} 的计划版本写入冲突{base}——基线已过期："
                f"期间有人改动了计划。请重新读取当前计划后重试",
            ) from exc


__all__ = [
    "LearningWorkflow",
    "NotFoundError",
    "ProgressConflict",
    "SyncReport",
    "WorkflowError",
    "WorkflowResult",
    "sync_diagnosis",
]
