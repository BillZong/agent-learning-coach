"""直接教学：一次教学长什么样，以及怎么量。

四段演示：

1. **指令**——直接教学的指令（每段对应一条研究结论）；
2. **一次教学**——装配上下文 → 模型 → 引用门禁 → 教学文本；
3. **结构**——讲解、分步示例、引导问题、下一步；
4. **评估**——教学评估集量的是机制（有没有讲解、示例带不带引导问题、引用对不对）。

**这个示例要说清一件事**：它量的是**机制**，不是「讲得好不好」——后者要人评。
用脚本化模型跑出来的数字只说明「管道是通的」，不说明教学质量 ✓。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agent_learning_coach.core.application.teaching import (
    DIRECT_TEACHING_INSTRUCTIONS,
    DirectTeacher,
    TeachingCaseResult,
    TeachingOutput,
    TeachingRequest,
    evaluate_teaching,
    load_teaching_cases,
)
from agent_learning_coach.core.application.tutor_context import TutorContextRequest
from agent_learning_coach.core.llm.mock import MockLLMClient
from agent_learning_coach.core.rag.citation import CitationRegistry, CitationSet

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evals" / "teaching.json"


def _citations() -> CitationSet:
    """构造一组引用。

    Returns:
        引用集合。
    """
    registry = CitationRegistry()
    from agent_learning_coach.core.rag.types import Chunk

    chunk = Chunk(
        id="c1",
        document_id="d1",
        text="闭包是函数加上它定义时所处的环境。即使函数已经返回，变量绑定仍然保留。",
        index=0,
        source="closures.md",
    )
    return CitationSet(citations=(registry.build(chunk, score=0.82),))


def show_instructions() -> None:
    """演示直接教学的指令。"""
    print("=== 1. 直接教学的指令 ===\n")
    print(DIRECT_TEACHING_INSTRUCTIONS)
    print("（每一段都对应一条研究结论：分步示范、自我解释、表面变化、渐隐支持、")
    print("  错误示例、引用——改指令时知道在动什么）\n")


def show_one_turn() -> None:
    """演示一次完整教学。"""
    print("=== 2. 一次教学 ===\n")
    payload = json.dumps(
        {
            "explanation": (
                "闭包是函数加上它定义时所处的环境[1]。即使函数已经返回，变量绑定仍然保留。"
            ),
            "examples": [
                {
                    "title": "一个计数的闭包",
                    "steps": [
                        "定义内部函数 increment，它修改外层的 count",
                        "内部函数用 nonlocal 声明 count 是外层的变量",
                    ],
                    "code": (
                        "def counter():\n"
                        "    count = 0\n"
                        "    def inc():\n"
                        "        nonlocal count\n"
                        "        count += 1\n"
                        "        return count\n"
                        "    return inc"
                    ),
                    "question": "为什么这里要写 nonlocal？",
                },
            ],
            "citations": ["1"],
            "follow_up": "闭包和作用域是什么关系？",
        },
        ensure_ascii=False,
    )
    teacher = DirectTeacher(client=MockLLMClient(text=payload))
    request = TeachingRequest(
        question="闭包是什么？",
        context=TutorContextRequest(question="闭包是什么？", citations=_citations()),
    )

    turn = asyncio.run(teacher.teach(request))

    print(f"判定：{turn.grounding.summary()}")
    print()
    print(turn.render())
    print()
    print("（**引用门禁**接在最后：编造的引用会被拦下，原文不交付——Day 31 的决定）")
    print("（上下文装配在前面：五段 + 优先级——Day 32 的决定）\n")


def show_structure() -> None:
    """演示结构化输出的形状。"""
    print("=== 3. 结构化教学输出 ===\n")
    output = TeachingOutput(
        explanation="闭包是函数加上它定义时所处的环境[1]。",
        examples=[],
        citations=["1"],
        follow_up="闭包和作用域是什么关系？",
    )
    print("字段：")
    print("  explanation —— 讲解本身（要能独立成立）")
    print("  examples   —— 分步示范 + 引导问题（被动阅读会让示例白讲）")
    print("  citations  —— 声明引用了哪些材料（门禁逐条查）")
    print("  follow_up  —— 下一步的引导问题")
    print()
    print(f"当前这份输出声明的引用：{output.citations}")
    print("（**声明的引用也要过门禁**——声明了不存在的标记与正文里写一个一样糟）\n")


def show_evaluation() -> None:
    """演示教学评估。"""
    print("=== 4. 教学评估：量机制，不量教学质量 ===\n")
    cases = load_teaching_cases(DATASET)
    print(f"共 {len(cases)} 条用例：")
    for case in cases:
        print(f"  [{case.category:<9}] {case.question}")
    print()
    payload = json.dumps(
        {
            "explanation": "闭包是函数加上它定义时所处的环境[1]。",
            "examples": [
                {
                    "title": "例子",
                    "steps": ["一步"],
                    "question": "为什么？",
                },
            ],
            "citations": ["1"],
            "follow_up": "接下来看什么？",
        },
        ensure_ascii=False,
    )
    teacher = DirectTeacher(client=MockLLMClient(text=payload))
    results = []
    for case in cases:
        # 用例声明了「这次要不要材料」——没有材料就不给引用，
        # 否则评估量的是脚本化模型，不是管道 ✓。
        has_materials = bool(case.context.get("materials"))
        request = TeachingRequest(
            question=case.question,
            context=TutorContextRequest(
                question=case.question,
                citations=_citations() if has_materials else CitationSet(),
            ),
        )
        turn = asyncio.run(teacher.teach(request))
        results.append(TeachingCaseResult(case=case, turn=turn))

    metrics = evaluate_teaching(results)
    print(metrics.summary())
    print()
    for result in results:
        print("  " + result.describe())
    print()
    print("（这份评估集是**人工合成**的，模型是**脚本化**的——量的是管道通不通，")
    print("  不是「讲得好不好」。后者要人评，Phase 3 再说）\n")


def main() -> None:
    """跑一遍直接教学演示。"""
    show_instructions()
    show_one_turn()
    show_structure()
    show_evaluation()


if __name__ == "__main__":
    main()
