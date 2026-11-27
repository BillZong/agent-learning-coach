"""苏格拉底式教学：问、听、判断、下一步。

四段演示：

1. **三阶段框架**——Assessment / Orchestration / Elicitation 各是什么；
2. **一次提问**——问题 + 提示，不给答案；
3. **听懂再调整**——听完学习者的回答，判断状态、决定下一步、决定要不要切模式；
4. **两种模式的对比**——只比共同的部分（引用与门禁）；**效果的对比要真实学习者**。

**这个示例要说清一件事**：GuideEval 的实测结论是——现有 LLM 在学习者困惑时**常常给
不出有效的适应性支架**。所以重点不是「用苏格拉底式的语气问问题」，而是**听完之后的
判断与调整** ✓。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agent_learning_coach.core.application.socratic import (
    SocraticAssessment,
    SocraticCase,
    SocraticCaseResult,
    SocraticTeacher,
    choose_next_mode,
    evaluate_socratic,
)
from agent_learning_coach.core.application.teaching import (
    SOCRATIC,
    TeachingRequest,
)
from agent_learning_coach.core.application.tutor_context import TutorContextRequest
from agent_learning_coach.core.llm.mock import MockLLMClient

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evals" / "socratic.json"


def show_framework() -> None:
    """演示三阶段框架。"""
    print("=== 1. 三阶段框架（GuideEval） ===\n")
    print("| 阶段 | 问什么 | 本项目的实现 |")
    print("| --- | --- | --- |")
    print("| Assessment | 学习者现在是什么状态？ | understanding：困惑 / 部分对 / 答对 |")
    print("| Orchestration | 下一步该换哪种策略？ | next_step：追问 / 提示 / 揭示 / 下一个 |")
    print("| Elicitation | 怎么问才能引出真正的思考？ | question + hint |")
    print()
    print("（**「用苏格拉底式的语气问问题」不等于「在引导」**——")
    print("  引导的核心是**根据学习者的状态调整策略**）\n")


def show_asking() -> None:
    """演示一次提问。"""
    print("=== 2. 一次提问 ===\n")
    teacher = SocraticTeacher(
        client=MockLLMClient(
            text=json.dumps(
                {
                    "question": "如果函数已经返回了，那个被记住的变量还在吗？",
                    "rationale": "先让学习者想清楚「返回之后」这件事",
                    "hint": "看看函数体里哪个名字被用到了",
                    "citations": [],
                },
                ensure_ascii=False,
            ),
        ),
    )
    request = TeachingRequest(
        question="闭包是什么？",
        mode=SOCRATIC,
        context=TutorContextRequest(question="闭包是什么？"),
    )

    turn = asyncio.run(teacher.ask(request))

    print(turn.render())
    print()
    print("（**不给答案**——这是苏格拉底式的全部意义）")
    print("（**提示是必要的**：没有提示，学习者卡住就只能干等）\n")


def show_assessment() -> None:
    """演示回答理解与模式切换。"""
    print("=== 3. 听懂再调整 ===\n")
    teacher = SocraticTeacher(
        client=MockLLMClient(
            text=json.dumps(
                {
                    "understanding": "partial",
                    "reasoning": "学习者说对了「函数记住环境」，但没说清捕获的是什么",
                    "next_step": "probe",
                    "follow_up_question": "那它捕获的是值，还是名字？",
                },
                ensure_ascii=False,
            ),
        ),
    )
    request = TeachingRequest(
        question="闭包是什么？",
        mode=SOCRATIC,
        context=TutorContextRequest(question="闭包是什么？"),
    )

    assessment = asyncio.run(teacher.assess(request, "闭包就是函数记住了外面的变量"))
    decision = choose_next_mode(assessment)

    print(f"判断：{assessment.understanding}")
    print(f"依据：{assessment.reasoning}")
    print(f"下一步：{assessment.next_step}")
    print(f"模式：{decision.mode}（{decision.reason}）")
    print()
    print("（**模式切换是业务判断**：学习者问不动了就切回直接教学——")
    print("  继续追问没有意义时还问，是在消耗学习者的耐心）\n")

    print("另一个分支：学习者明确要求直接讲")
    reveal = choose_next_mode(
        SocraticAssessment(understanding="confused", next_step="reveal"),
    )
    print(f"  模式：{reveal.mode}（{reveal.reason}）")
    print()


def show_evaluation() -> None:
    """演示苏格拉底式评估与两种模式的对比。"""
    print("=== 4. 评估：判断与调整做对了吗 ===\n")
    cases = json.loads(DATASET.read_text(encoding="utf-8"))["cases"]
    print(f"共 {len(cases)} 条用例：")
    for case in cases:
        print(f"  [{case['category']:<8}] {case['learner_message'][:20]}")
    print()
    results = []
    for case in cases:
        assessment = SocraticAssessment(
            understanding="partial",
            reasoning="脚本化模型的固定回答",
            next_step="probe",
        )
        results.append(
            SocraticCaseResult(
                case=SocraticCase.from_dict(case, index=1),
                assessment=assessment,
                decision=choose_next_mode(assessment),
            ),
        )
    metrics = evaluate_socratic(results)
    print(metrics.summary())
    print()
    print("（**判断准确率低是意料之中的**：脚本化模型每次都答同一个——")
    print("  它量的是管道通不通，不是模型会不会引导）")
    print()
    print("--- 两种模式的对比 ---")
    print("共同指标（引用声明率 / 编造率 / 过门禁率）——两种模式都过同一道门禁 ✓；")
    print("差异部分（讲解 vs 提问）**不可比**——它们回答的是不同的问题。")
    print("**效果的对比要真实学习者**：脚本化模型给不出这个结论。\n")


def main() -> None:
    """跑一遍苏格拉底式教学演示。"""
    show_framework()
    show_asking()
    show_assessment()
    show_evaluation()


if __name__ == "__main__":
    main()
