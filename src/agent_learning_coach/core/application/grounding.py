"""引用支撑的处置：答案能不能交付，以及怎么跟学习者说。

**为什么这一层必须存在**。``core.rag.citation`` 能查出「答案引用了一个不存在的
标记」（编造）与「引用的切片和这句话没有词面交集」（可能指错），但它**只给报告，
不做决定**。而「这条答案能不能给学习者看」是**业务判断**：

- 编造的引用 → 学习者按标记去核对，却找不到任何东西 → 「这个教练在瞎编」；
- 没有引用 → 答案可能来自模型的记忆。对通用知识没问题，对「材料里第几页写了什么」
  这种问题就是没有依据。

**领域层不选边**（ADR-0020/0024 的老规矩）：判定与处置之间需要显式衔接，这里
就是那个衔接处。**策略由调用方给**（``GroundingPolicy``），本模块只执行。

**阻断不是惩罚，是改道**。默认策略遇到编造引用会拦住答案，并给出**补救话术**
（"材料里没找到依据，我先讲清楚这个概念"）。学习者拿到的是「有依据的解释」或
「坦白的不知道」，而不是一段带假引用的说明。**严格门禁的失败模式是沉默**——所以
阻断必须配一条能继续走的路。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from agent_learning_coach.core.rag.citation import (
    Citation,
    CitationReport,
    CitationSet,
    CitationValidator,
)

GROUNDED = "grounded"
"""答案有引用，且引用都存在。"""

UNGROUNDED = "ungrounded"
"""答案没有任何引用标记。"""

FABRICATED = "fabricated"
"""答案引用了不存在的标记——引用是编的。"""

RETRY_INSTRUCTION = (
    "你引用了不存在的材料。请只引用检索结果里出现过的标记；"
    "材料里没有依据时，直接说没有找到依据，不要编造引用。"
)
"""重试时给模型的指令。

**阻断之后必须有一条能继续走的路**：把「引用编了」变成「再答一次」的具体要求，
而不是让学习者等一场没有下文的沉默。
"""

FALLBACK_TEXT = "我在材料里没找到支持这一点的依据，先不给你一个没根据的说法。"
"""兜底话术——阻断之后给学习者看的那一句。

**宁可说「没找到依据」**：那是对学习者可核对的诚实；编一个说法不是。
"""


@dataclass(frozen=True)
class GroundingPolicy:
    """引用支撑的处置策略。

    **这是业务判断，不是技术参数**：教「材料里怎么写的」必须要求引用；讲通用概念
    可以不要求。
    """

    require_citation: bool = False
    """是否要求答案必须带引用。

    默认 False：讲通用概念时，模型用自己的知识回答是合理的。要求引用会把
    「讲得清楚」和「有依据」混为一谈。
    """

    block_on_fabrication: bool = True
    """遇到编造的引用是否拦住答案。

    默认 True：**假装有依据比没有依据更糟**——学习者会按标记去核对一段根本不
    存在的材料。
    """

    retry_instruction: str = RETRY_INSTRUCTION
    """拦截后重试时给模型的指令。"""

    fallback_text: str = FALLBACK_TEXT
    """拦截后给学习者看的兜底话术。"""


@dataclass(frozen=True)
class GroundingResult:
    """支撑检查的结果。

    **它同时是判定和证据**：判定给调用方决定怎么处置，报告给调用方（与学习者）
    解释为什么。
    """

    kind: str
    """判定：``grounded`` / ``ungrounded`` / ``fabricated``。"""

    deliver: bool
    """是否可以把答案交给学习者。"""

    report: CitationReport
    """引用校验报告。"""

    citations: CitationSet
    """本次可用的引用。"""

    note: str = ""
    """给调用方的说明（为什么拦、拦了之后怎么办）。"""

    retry_instruction: str = ""
    """拦截后重试时给模型的指令；没有拦截时为空。"""

    fallback_text: str = ""
    """拦截后给学习者看的兜底话术；没有拦截时为空。"""

    @property
    def fabricated_markers(self) -> tuple[str, ...]:
        """被编造的标记。"""
        return tuple(issue.marker for issue in self.report.errors)

    @property
    def warnings(self) -> tuple[str, ...]:
        """警告级问题（不支撑、无引用）的说明。"""
        return tuple(issue.detail for issue in self.report.warnings)

    def summary(self) -> str:
        """一句话摘要。

        Returns:
            形如 ``fabricated（拦截）：答案引用了 [7]`` 的描述。
        """
        head = f"{self.kind}（{'交付' if self.deliver else '拦截'}）"
        if self.fabricated_markers:
            return f"{head}：答案引用了 {list(self.fabricated_markers)}"
        if self.kind == UNGROUNDED:
            return f"{head}：答案里没有任何引用标记"
        return head


def check_grounding(
    answer: str,
    citations: CitationSet,
    *,
    policy: GroundingPolicy | None = None,
    validator: CitationValidator | None = None,
) -> GroundingResult:
    """检查答案能不能交付。

    Args:
        answer: 模型给出的答案文本。
        citations: 本次可用的引用集合。
        policy: 处置策略。为 None 时用默认策略。
        validator: 校验器。为 None 时用默认校验器。

    Returns:
        支撑检查结果。
    """
    policy = policy or GroundingPolicy()
    validator = validator or CitationValidator()
    report = validator.validate(answer, citations)

    if report.errors:
        if policy.block_on_fabrication:
            return GroundingResult(
                kind=FABRICATED,
                deliver=False,
                report=report,
                citations=citations,
                note=(f"答案引用了 {list(report.cited)} 中不存在的标记，不能作为有依据的回答交付"),
                retry_instruction=policy.retry_instruction,
                fallback_text=policy.fallback_text,
            )
        return GroundingResult(
            kind=FABRICATED,
            deliver=True,
            report=report,
            citations=citations,
            note="答案里有编造的引用，已交付但必须同时报告",
        )

    if not report.cited:
        if policy.require_citation:
            return GroundingResult(
                kind=UNGROUNDED,
                deliver=False,
                report=report,
                citations=citations,
                note="这次回答要求带引用，但答案里没有任何引用标记",
                retry_instruction=policy.retry_instruction,
                fallback_text=policy.fallback_text,
            )
        return GroundingResult(
            kind=UNGROUNDED,
            deliver=True,
            report=report,
            citations=citations,
            note="答案里没有引用标记——通用知识可以没有依据，材料相关的问题不行",
        )

    return GroundingResult(
        kind=GROUNDED,
        deliver=True,
        report=report,
        citations=citations,
        note="答案的引用都存在",
    )


def collect_citations(state: Mapping[str, Any], *, key: str = "citations") -> CitationSet:
    """从执行上下文的草稿本里取回引用。

    **草稿本里的引用是工具产出的**（``StateCollector`` 收集），格式由工具与应用
    层约定：``state["citations"]`` 是引用字典的列表。

    Args:
        state: 执行上下文的草稿本。
        key: 草稿本里的键。

    Returns:
        引用集合；没有时返回空集合。
    """
    items = state.get(key)
    if not isinstance(items, list):
        return CitationSet()
    citations: list[Citation] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        citations.append(Citation.from_dict(item))
    return CitationSet(citations=tuple(citations))


@dataclass(frozen=True)
class GroundingOutcome:
    """一次教学回答的支撑结论——**答案与引用合在一起交付**。

    **答案和它的依据必须一起走**：只交付答案，学习者无从核对；只交付引用，
    学习者看不到被支撑的内容。
    """

    answer: str
    """模型给出的答案。"""

    citations: CitationSet
    """本次可用的引用。"""

    result: GroundingResult
    """支撑检查结果。"""

    reference_list: str = ""
    """参考文献列表。"""

    @property
    def deliver(self) -> bool:
        """是否交付。"""
        return self.result.deliver

    def render(self) -> str:
        """渲染成给学习者看的文本。

        **被拦截时不给原文**：那段文字里有编造的引用，让学习者看见它等于把假引用
        递过去。给的是兜底话术——「没找到依据」是可核对的诚实。

        Returns:
            答案正文 + 参考文献；有拦截时只给兜底话术。
        """
        if not self.result.deliver:
            return self.result.fallback_text
        if not self.reference_list:
            return self.answer
        return f"{self.answer}\n\n参考文献：\n{self.reference_list}"


def evaluate_answer(
    answer: str,
    citations: CitationSet,
    *,
    policy: GroundingPolicy | None = None,
) -> GroundingOutcome:
    """检查并打包一次教学回答。

    **答案与它的依据一起交付**：只给答案，学习者无从核对；只给引用，学习者看不到
    被支撑的内容。

    Args:
        answer: 模型给出的答案文本。
        citations: 本次可用的引用集合。
        policy: 处置策略。为 None 时用默认策略。

    Returns:
        支撑结论（含参考文献列表）。
    """
    result = check_grounding(answer, citations, policy=policy)
    return GroundingOutcome(
        answer=answer,
        citations=citations,
        result=result,
        reference_list=citations.reference_list(),
    )


__all__ = [
    "FABRICATED",
    "FALLBACK_TEXT",
    "GROUNDED",
    "RETRY_INSTRUCTION",
    "UNGROUNDED",
    "GroundingOutcome",
    "GroundingPolicy",
    "GroundingResult",
    "check_grounding",
    "collect_citations",
    "evaluate_answer",
]
