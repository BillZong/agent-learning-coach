"""持久化基线：形状、乐观锁，以及「整体重建 vs 增量更新」的成本实测。

跑法::

    uv run python examples/learning_persistence.py

脚本演示五件事：

1. **形状**——领域对象编码成 JSON 兼容的负载，带 ``schema_version``。
2. **往返**——存进去再取出来是同一个对象；派生状态不落库。
3. **乐观锁**——基于旧版本写入会冲突。
4. **不变量**——负载里的事实不合法时，读回就被拒。
5. **成本实测**——进度的「整体重建 vs 增量更新」到底差多少。

脚本不联网、不连数据库。
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import date

from agent_learning_coach.core.learning import (
    CapabilityRequirement,
    CourseId,
    DailyEffort,
    DomainError,
    GoalId,
    KnowledgePointId,
    LearnerId,
    LearningGoal,
    LearningProgress,
    TimeWindow,
)
from agent_learning_coach.core.storage import (
    SCHEMA_VERSION,
    ConflictError,
    DecodeError,
    InMemoryGoalRepository,
    Stored,
    goal_from_dict,
    goal_to_dict,
    progress_from_dict,
    progress_to_dict,
)


def _goal() -> LearningGoal:
    """构造一个目标。"""
    return LearningGoal(
        id=GoalId("goal-1"),
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-1"),
        statement="学会闭包",
        outcomes=("能解释闭包",),
        requirements=(
            CapabilityRequirement(point_id=KnowledgePointId("k2"), outcome="能解释闭包"),
        ),
        daily_effort=DailyEffort(minutes_per_day=45),
        time_window=TimeWindow(start=date(2026, 11, 25), end=date(2026, 12, 25)),
    )


def show_shape() -> None:
    """演示负载的形状。"""
    print("=== 1. 形状：领域对象 → JSON 兼容的负载 ===\n")
    payload = goal_to_dict(_goal())

    print(json.dumps(payload, ensure_ascii=False, indent=2)[:600])
    print("...")
    print(f"\nschema_version = {payload['schema_version']}（负载的版本；不认识就拒绝）")
    print("（目标**状态**不在负载里——它由迁移历史派生，存派生值就要同步两处）")
    print("（跨聚合的引用（learner_id / course_id）既在负载里，也在数据库的列里；")
    print("  两者由 CHECK 约束绑死，不让它们各说一套）\n")


def show_round_trip() -> None:
    """演示往返。"""
    print("=== 2. 往返：存进去再取出来是同一个对象 ===\n")
    repository = InMemoryGoalRepository()
    goal = _goal()

    saved = asyncio.run(repository.save(Stored(value=goal, revision=0)))
    loaded = asyncio.run(repository.load(GoalId("goal-1")))

    print(f"保存后版本：{saved.revision}（0 表示新建）")
    assert loaded is not None
    print(f"取出来相等：{loaded.value == goal}")
    print(f"取出来时版本：{loaded.revision}")
    print("（内存实现存的是**负载字典**，读回时用 from_dict 构造领域对象——")
    print("  构造期的校验因此全部生效）\n")


def show_optimistic_lock() -> None:
    """演示乐观锁。"""
    print("=== 3. 乐观锁：基于旧版本写入会冲突 ===\n")
    repository = InMemoryGoalRepository()
    goal = _goal()

    first = asyncio.run(repository.save(Stored(value=goal, revision=0)))
    print(f"第一次保存后版本：{first.revision}")

    updated = goal.activate(at=1000.0, reason="开始学")
    asyncio.run(repository.save(Stored(value=updated, revision=first.revision)))
    print("第二次保存（基于同一个版本）成功")

    try:
        asyncio.run(repository.save(Stored(value=updated, revision=first.revision)))
    except ConflictError as exc:
        print(f"第三次保存（还是旧版本）-> {exc}")
    print("（领域对象不可变，「更新」是整体替换；没有版本号，两个并发写会静默互相覆盖）")
    print("（冲突是**事实**：调用方要重新读、重新合并、再写——那是应用层的事）\n")


def show_invariants() -> None:
    """演示不变量在读回时生效。"""
    print("=== 4. 不变量：坏负载在读回时就被拒 ===\n")
    payload = goal_to_dict(_goal().activate(at=1000.0, reason="开始学"))
    payload["transitions"].append(
        {"source": "draft", "target": "active", "reason": "伪造的一条迁移", "at": 2000.0},
    )

    try:
        goal_from_dict(payload)
    except DomainError as exc:
        print(f"[迁移历史断链] -> {exc}")

    payload = goal_to_dict(_goal())
    payload["schema_version"] = SCHEMA_VERSION + 1
    try:
        goal_from_dict(payload)
    except DecodeError as exc:
        print(f"[版本不认识] -> {exc}")
    print("（前者是**事实不合法**（领域错误），后者是**结构不认识**（解码错误）——")
    print("  两者的处理方式不同：前者要修数据，后者要迁移）\n")


def _big_progress(point_count: int) -> LearningProgress:
    """构造一份有若干知识点的进度。

    Args:
        point_count: 知识点数量。

    Returns:
        学习进度。
    """
    progress = LearningProgress(
        learner_id=LearnerId("learner-1"),
        course_id=CourseId("course-1"),
    )
    for index in range(point_count):
        point_id = KnowledgePointId(f"k{index}")
        progress = progress.start(point_id, at=1000.0 + index, reason="开始学")
        progress = progress.complete(point_id, at=2000.0 + index, reason="学会了")
    return progress


def measure() -> None:
    """实测「整体重建 vs 增量更新」的成本。"""
    print("=== 5. 成本实测：进度「整体重建 vs 增量更新」 ===\n")
    for point_count in (50, 200, 1000):
        progress = _big_progress(point_count)

        start = time.perf_counter()
        payload = progress_to_dict(progress)
        encoded = time.perf_counter() - start

        start = time.perf_counter()
        progress_from_dict(payload)
        decoded = time.perf_counter() - start

        # 一次迁移 = 只改一个知识点；整块模型下它要重写整份负载。
        start = time.perf_counter()
        for _ in range(10):
            moved = progress.review(KnowledgePointId("k0"), at=3000.0, reason="复习一遍")
            progress_to_dict(moved)
        per_migration = (time.perf_counter() - start) / 10

        size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        print(
            f"{point_count:>5} 个知识点：负载 {size / 1024:>7.1f} KB ｜ "
            f"编码 {encoded * 1000:>6.2f} ms ｜ 解码 {decoded * 1000:>6.2f} ms ｜ "
            f"一次迁移（整块重写）{per_migration * 1000:>6.2f} ms",
        )

    print()
    print("（读的成本与存储形态无关：领域对象本来就持有 N 个 PointProgress）")
    print("（写的成本与存储形态有关：整块模型每次迁移都重写整份负载，O(N)）")
    print("（结论：本项目选整块——因为领域层的更新粒度就是整块替换（ADR-0015），")
    print("  存储粒度与之一致才不需要引入 diff 机制；代价在上面的数字里）")


def main() -> None:
    """跑一遍持久化基线演示。"""
    show_shape()
    show_round_trip()
    show_optimistic_lock()
    show_invariants()
    measure()


if __name__ == "__main__":
    main()
