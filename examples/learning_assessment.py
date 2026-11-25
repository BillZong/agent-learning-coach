"""诊断评估：作答如何映射到知识点，映射缺失时如何降级。

跑法::

    uv run python examples/learning_assessment.py

脚本演示五件事：

1. **映射**——题目声明考点，评估据此归属判定。
2. **汇总**——一个知识点的多道题聚合成一条判定：全对才算会，一错就是缺口。
3. **降级**——映射缺失的作答不进判定，但**保留为证据**并被报告。
4. **判官**——判定是外部判断：协议可替换；无参考答案时判「判不了」。
5. **边界**——判定与依据必须一致；作答引用不存在的题目是错误而非降级。

脚本不联网。
"""

from __future__ import annotations

from agent_learning_coach.core.learning import (
    DiagnosticEvidence,
    DiagnosticExercise,
    DiagnosticSubmission,
    DiagnosticVerdict,
    DomainError,
    ExactMatchGrader,
    Grading,
    KnowledgePointId,
    assess_diagnosis,
)


def _exercise(
    point_id: str | None,
    exercise_id: str,
    expected_answer: str = "闭包记住了创建时的作用域",
) -> DiagnosticExercise:
    """构造一道诊断题。

    Args:
        point_id: 考哪个知识点；``None`` 表示映射缺失。
        exercise_id: 题目标识。
        expected_answer: 参考答案；空表示开放题。

    Returns:
        诊断题。
    """
    return DiagnosticExercise(
        id=exercise_id,
        prompt="闭包与普通函数在返回后访问变量时有什么不同？",
        point_id=KnowledgePointId(point_id) if point_id else None,
        expected_answer=expected_answer,
    )


def _submission(exercise_id: str, answer: str) -> DiagnosticSubmission:
    """构造一次作答。"""
    return DiagnosticSubmission(exercise_id=exercise_id, answer=answer)


class _StubGrader:
    """按题目标识返回预设结论的判官。"""

    def __init__(self, **by_exercise: Grading) -> None:
        """初始化判官。

        Args:
            by_exercise: 题目标识 → 判定结论。
        """
        self.by_exercise = by_exercise

    def grade(
        self,
        exercise: DiagnosticExercise,
        submission: DiagnosticSubmission,
    ) -> Grading:
        """返回预设结论。

        Args:
            exercise: 题目。
            submission: 作答。

        Returns:
            判定结论。
        """
        return self.by_exercise.get(exercise.id, "correct")


def show_mapping() -> None:
    """演示题目到知识点的映射。"""
    print("=== 1. 映射：题目声明考点 ===\n")
    exercises = (_exercise("k1", "e1"), _exercise("k2", "e2"))
    submissions = (
        _submission("e1", "闭包记住了创建时的作用域"),
        _submission("e2", "闭包就是普通函数"),
    )

    result = assess_diagnosis(exercises, submissions, _StubGrader(e1="correct", e2="incorrect"))

    for verdict in result.verdicts:
        print(f"{verdict.point_id}：{verdict.label}（{verdict.reason}）")
    print()


def show_aggregation() -> None:
    """演示多个知识点、多个题目的汇总规则。"""
    print("=== 2. 汇总：全对才算会，一错就是缺口 ===\n")
    exercises = (_exercise("k1", "e1"), _exercise("k1", "e2"))
    submissions = (
        _submission("e1", "闭包记住了创建时的作用域"),
        _submission("e2", "闭包记住了创建时的作用域"),
    )

    all_right = assess_diagnosis(exercises, submissions, _StubGrader(e1="correct", e2="correct"))
    one_wrong = assess_diagnosis(exercises, submissions, _StubGrader(e1="correct", e2="incorrect"))

    print(
        f"两题全对：{all_right.verdicts[0].label}（依据 {len(all_right.verdicts[0].evidence)} 条）"
    )
    print(f"一题答错：{one_wrong.verdicts[0].label}（{one_wrong.verdicts[0].reason}）")
    print("（诊断的目的是找出要补的地方——宽松汇总会让计划跳过真正没学会的知识点）\n")


def show_degradation() -> None:
    """演示映射缺失时的降级。"""
    print("=== 3. 降级：映射缺失 → 不判定，但保留证据 ===\n")
    exercises = (_exercise(None, "e1"), _exercise("k1", "e2"))
    submissions = (
        _submission("e1", "闭包记住了创建时的作用域"),
        _submission("e2", "闭包记住了创建时的作用域"),
    )

    result = assess_diagnosis(exercises, submissions, _StubGrader(e1="correct", e2="correct"))

    print(f"判定条数：{len(result.verdicts)}（映射缺失的题不参与判定）")
    print(f"证据条数：{len(result.evidence)}（映射缺失的作答仍被保留）")
    report = result.degradation
    unmapped = len(report.unmapped_evidence)
    undecided = len(report.undecided_evidence)
    print(f"降级：映射缺失 {unmapped} 条、判不了 {undecided} 条")
    print("（不静默丢弃：否则没法回答「这次诊断到底覆盖了什么」）\n")


def show_grader() -> None:
    """演示判官是可替换的判断。"""
    print("=== 4. 判官：判定是外部判断 ===\n")
    exercise = _exercise("k1", "e1")
    good = _submission("e1", "闭包记住了 创建时的作用域。")
    bad = _submission("e1", "闭包就是普通函数")

    grader = ExactMatchGrader()
    print(f"答对（归一化后一致）：{grader.grade(exercise, good)}")
    print(f"答错：{grader.grade(exercise, bad)}")

    open_question = _exercise("k1", "e2", expected_answer="")
    print(f"开放题（无参考答案）：{grader.grade(open_question, bad)}")
    print("（判不了就不判——算作答错会凭空制造知识缺口）")
    print("（换 LLM 判官只需实现 Grader 协议，领域模型不动——Topic 39）\n")


def show_boundaries() -> None:
    """演示边界：一致性错误与降级不同。"""
    print("=== 5. 边界：一致性错误 ≠ 降级 ===\n")
    try:
        DiagnosticVerdict(
            point_id=KnowledgePointId("k2"),
            verdict="known",
            reason="拿错了依据",
            evidence=(
                DiagnosticEvidence(
                    exercise=_exercise("k1", "e1"),
                    submission=_submission("e1", "闭包记住了创建时的作用域"),
                    grading="correct",
                    point_id=KnowledgePointId("k1"),
                ),
            ),
        )
    except DomainError as exc:
        print(f"[判定与依据不一致] -> {exc}")

    try:
        assess_diagnosis(
            (_exercise("k1", "e1"),),
            (_submission("e9", "闭包记住了创建时的作用域"),),
            _StubGrader(),
        )
    except DomainError as exc:
        print(f"[作答引用不存在的题] -> {exc}")
    print("（前者是模型自相矛盾，后者是数据不一致——都是错误，不是降级）")


def main() -> None:
    """跑一遍诊断评估演示。"""
    show_mapping()
    show_aggregation()
    show_degradation()
    show_grader()
    show_boundaries()


if __name__ == "__main__":
    main()
