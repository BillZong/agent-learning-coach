"""引用支撑：校验器准不准、答案的引用质量、以及能不能交付。

四段演示：

1. **评估集**——引用用例长什么样（标注的是「校验器应该报什么」）；
2. **校验器的准不准**——准确率 / 精确率 / 召回率，以及它漏掉了哪一条；
3. **答案的引用质量**——引用精确率、覆盖率、编造率；
4. **支撑门禁**——同样一段答案，策略不同，处置不同。

**这个示例要说清一件事**：词面支撑是**蕴含的必要不充分条件**。共享词不等于
支撑（反例见第 2 段：答案与切片只共享「返回」两个字，校验器就放行了）——所以
这里的引用精确率是**上界估计**，真正的口径要用 NLI 模型（见 ADR-0033）。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from agent_learning_coach.core.application import (
    GroundingPolicy,
    check_grounding,
    evaluate_answer,
)
from agent_learning_coach.core.rag import (
    CitationCase,
    CitationSet,
    evaluate_citations,
    load_citation_cases,
)

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evals" / "citations.json"


def show_dataset(cases: Sequence[CitationCase]) -> None:
    """演示评估集的样子。

    Args:
        cases: 评估用例。
    """
    print("=== 1. 引用评估集 ===\n")
    print(f"共 {len(cases)} 条用例：")
    for case in cases:
        print(f"  [{case.category:<11}] {case.answer[:28]}")
        if case.expected_fabricated:
            print(f"      期望判为编造：{list(case.expected_fabricated)}")
        if case.expected_unsupported:
            print(f"      期望判为不支撑：{list(case.expected_unsupported)}")
    print()
    print("（标注的是「**校验器应该报什么**」——这是能离线判定的那一半）")
    print("（「模型会不会编引用」需要真模型，见 ADR-0033 §边界）\n")


def show_checker(cases: Sequence[CitationCase]) -> None:
    """演示校验器准不准。

    Args:
        cases: 评估用例。
    """
    print("=== 2. 校验器准不准 ===\n")
    metrics = evaluate_citations(cases)
    print(metrics.summary())
    print()
    wrong = [result for result in metrics.results if not result.checker_correct]
    if wrong:
        print(f"判错的 {len(wrong)} 条：")
        for result in wrong:
            print(f"  {result.case.answer}")
            print(f"    期望不支撑：{list(result.case.expected_unsupported)}")
            print(f"    实际不支撑：{list(result.detected_unsupported)}")
        print()
        print("（**漏报的原因**：词面口径下，答案与切片只要共享**一个**词就算「支撑」）")
        print("（共享词不等于支撑——这是词面口径的**假阴性**）")
    else:
        print("全部判对。")
    print()
    print("（精确率 1.00 = 校验器报的问题都是真的；召回率 < 1 = 它会漏报）")
    print("（**多报和漏报是两种错**：多报会把好答案拦下来，学习者白白看到告警）\n")


def show_answer_quality(cases: Sequence[CitationCase]) -> None:
    """演示答案的引用质量。

    Args:
        cases: 评估用例。
    """
    print("=== 3. 答案的引用质量 ===\n")
    metrics = evaluate_citations(cases)
    print(f"引用精确率 {metrics.citation_precision:.2f}（上界估计：词面口径）")
    print(f"引用覆盖率 {metrics.citation_coverage:.2f}（带引用的句子占比）")
    print(f"编造率     {metrics.fabrication_rate:.2f}（指向不存在标记的比例）")
    print()
    for category, item in sorted(metrics.by_category.items()):
        print(f"  [{category:<11}] 精确率 {item.citation_precision:.2f}")
    print()
    print("（**按类别看**才能知道问题在哪：编造率高的那一类才是要修的）")
    print("（这份评估集是**人工合成**的——量的是机制，不是真实模型的引用习惯）\n")


def show_grounding(cases: Sequence[CitationCase]) -> None:
    """演示支撑门禁：同样一段答案，策略不同，处置不同。

    Args:
        cases: 评估用例。
    """
    print("=== 4. 支撑门禁：能不能交付 ===\n")
    fabricated = next(case for case in cases if case.expected_fabricated)
    citations = CitationSet(citations=fabricated.citations)

    print(f"答案：{fabricated.answer}")
    print(f"可用引用：{list(citations.markers())}")
    print()

    for policy, label in [
        (GroundingPolicy(), "默认策略（编造即拦截）"),
        (GroundingPolicy(block_on_fabrication=False), "放行编造（但必须报告）"),
    ]:
        result = check_grounding(fabricated.answer, citations, policy=policy)
        print(f"{label}：")
        print(f"  {result.summary()}")
        print(f"  说明：{result.note}")
        print()

    outcome = evaluate_answer(fabricated.answer, citations)
    print("拦截时给学习者看的是：")
    print("  " + outcome.render().replace("\n", "\n  "))
    print()
    print("重试时给模型的指令：")
    print("  " + outcome.result.retry_instruction)
    print()
    print("（**不给原文**——那段文字里有编造的引用，递过去就是递假引用）")
    print("（**严格门禁的失败模式是沉默**——所以拦截必须配一条能继续走的路：重试）\n")


def main() -> None:
    """跑一遍引用支撑演示。"""
    cases = load_citation_cases(DATASET)

    show_dataset(cases)
    show_checker(cases)
    show_answer_quality(cases)
    show_grounding(cases)


if __name__ == "__main__":
    main()
