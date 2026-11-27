"""直接教学模式。

**教学模式是「怎么教」的策略**（Day 33 直接讲，Day 34 苏格拉底式追问），
不是两套代码——它们共用装配、引用与门禁，只是**指令**不同 ✓。

**指令的依据是教学研究的结论**（worked example effect，Sweller & Cooper 1985；
Renkl；MIT Teaching + Learning Lab）：

1. **分步示范比让学习者自己试错更有效**（新手尤其明显：学习同样的内容，用示范
   花的時間少近六倍、错误更少）✓——所以示例要**分步**，不是给一段成品代码 ✓；
2. **被动阅读会抵消效果**——要让学习者**自我解释**（self-explanation）✓——所以
   每个示例都带一个**引导问题** ✓✓；
3. **只记表面会失效**——多个示例要**表面不同、核心结构相同** ✓——所以示例的
   变化靠指令约束（不同的场景/语言），而不是复制同一个例子 ✓；
4. **逐步撤掉支持**（faded support）——熟练之后要给练习而不是继续给示范 ✓——所以
   指令要求**按学习者水平调整支持强度** ✓；
5. **错误示例也值得展示**（Booth et al. 2013）——让学习者纠正错例比只看对例更有效
   ✓——所以指令允许「指出一个常见错误」 ✓。

**引用不能少**：讲解里的事实性说法要指向材料（Day 31 的门禁）✓。教学内容的
`citations` 字段是**声明**，门禁会逐条查——**编造的引用会被拦下** ✓。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agent_learning_coach.core.application.grounding import (
    GroundingPolicy,
    GroundingResult,
    check_grounding,
)
from agent_learning_coach.core.application.tutor_context import (
    TutorContext,
    TutorContextBuilder,
    TutorContextRequest,
)
from agent_learning_coach.core.llm.models import LLMRequest
from agent_learning_coach.core.llm.protocol import LLMClient
from agent_learning_coach.core.rag.citation import CitationSet

DIRECT = "direct"
"""直接教学：讲清楚。"""

SOCRATIC = "socratic"
"""苏格拉底式：通过追问引导。**Day 34 交付**。"""

DIRECT_TEACHING_INSTRUCTIONS = """\
你是学习教练，正在直接讲解一个知识点。

按下面的要求回答：

1. **先讲清楚，再举例**。讲解本身要能独立成立——学习者不该靠看例子才明白概念。
2. **示例要分步**。给出问题、步骤和结果，不要只给一段成品。
3. **每个示例配一个引导问题**，让学习者自己解释一遍（"为什么这里要这样写？"）。
   被动阅读会让示例白讲。
4. **示例之间表面不同、核心相同**。换场景、换语言，不要复制同一个例子。
5. **按学习者的水平调整支持强度**。新手给完整步骤；已经会的给提纲让他自己推。
6. **指出一个常见错误**并说明它错在哪——纠正错例比只看对例更有效。
7. **事实性说法要引用材料**：在句末写 [1]、[2] 这样的标记。材料里没有的，不要写
   编号；没有依据的说法就说明这是你的补充。
8. **最后给一个下一步的引导问题**，让学习者能接着往下学。

用学习者能懂的语言。宁可少讲一点，也不要讲没有依据的东西。
"""
"""直接教学的指令。

**每一段都对应一条研究结论**（见模块 docstring）——这样改指令时知道在动什么 ✓。
"""


class TeachingExample(BaseModel):
    """一个教学示例——**分步的示范**。

    **不是一段成品代码**：步骤要能被逐条读懂，`question` 是让学习者自我解释的
    钩子 ✓。
    """

    title: str = Field(default="", description="示例的一句话标题")
    steps: list[str] = Field(default_factory=list, description="分步的过程")
    code: str = Field(default="", description="可选的代码")
    question: str = Field(default="", description="引导学习者自我解释的问题")


class TeachingOutput(BaseModel):
    """模型返回的结构化教学输出。"""

    explanation: str = Field(default="", description="讲解本身")
    examples: list[TeachingExample] = Field(default_factory=list, description="教学示例")
    citations: list[str] = Field(default_factory=list, description="引用到的材料标记")
    follow_up: str = Field(default="", description="下一步的引导问题")


@dataclass(frozen=True)
class TeachingRequest:
    """一次教学请求。"""

    question: str
    """学习者问的问题。"""

    context: TutorContextRequest
    """要带进上下文的教学状态与依据（Day 32 的请求对象 ✓）。"""

    mode: str = DIRECT
    """教学模式。Day 33 只有 direct；Day 34 加 socratic ✓。"""

    model: str | None = None
    """模型名。为 None 时用客户端的默认 ✓。"""


@dataclass(frozen=True)
class TeachingTurn:
    """一次教学的结果。

    **内容与判定一起交付**：内容给学习者，判定给调用方（要不要交付、为什么）✓。
    """

    output: TeachingOutput
    """模型的结构化输出。"""

    grounding: GroundingResult
    """引用支撑的判定（Day 31 的门禁 ✓）。"""

    citations: CitationSet
    """本次可用的引用。"""

    context: TutorContext | None = None
    """装配好的上下文（用于日志与调试 ✓）。"""

    @property
    def deliver(self) -> bool:
        """是否交付。"""
        return self.grounding.deliver

    def render(self) -> str:
        """渲染成给学习者看的文本。

        **被门禁拦下时不给原文**——那里面有编造的引用（Day 31 的决定 ✓）。

        Returns:
            教学文本。
        """
        if not self.deliver:
            return self.grounding.fallback_text
        return render_output(self.output, self.citations)


def render_output(output: TeachingOutput, citations: CitationSet) -> str:
    """把结构化教学输出渲染成文本。

    Args:
        output: 教学输出。
        citations: 本次可用的引用。

    Returns:
        教学文本。
    """
    parts: list[str] = []
    if output.explanation:
        parts.append(output.explanation)
    for example in output.examples:
        lines = [f"### {example.title}" if example.title else "### 示例"]
        for index, step in enumerate(example.steps, start=1):
            lines.append(f"{index}. {step}")
        if example.code:
            lines.extend(["```python", example.code, "```"])
        if example.question:
            lines.append(f"想一想：{example.question}")
        parts.append("\n".join(lines))
    if output.follow_up:
        parts.append(f"下一步：{output.follow_up}")
    if citations.citations:
        parts.append(f"参考文献：\n{citations.reference_list()}")
    return "\n\n".join(parts)


@dataclass(frozen=True)
class DirectTeacher:
    """直接教学模式。

    **它把三样东西接起来**：Day 32 的上下文装配、Day 31 的引用门禁、以及模型的
    结构化输出 ✓。教学模式只决定**指令** ✓。
    """

    client: LLMClient
    """模型客户端。"""

    context_builder: TutorContextBuilder = field(default_factory=TutorContextBuilder)
    """上下文构建器（Day 32 ✓）。"""

    policy: GroundingPolicy = field(default_factory=GroundingPolicy)
    """引用支撑的处置策略（Day 31 ✓）。"""

    async def teach(self, request: TeachingRequest) -> TeachingTurn:
        """讲一次。

        Args:
            request: 教学请求。

        Returns:
            教学结果（含引用支撑判定）。

        Raises:
            ValueError: 教学模式不认识。
            LLMError: 模型调用失败。
        """
        instructions = instructions_for(request.mode)
        context = self.context_builder.build(request.context)
        llm_request = LLMRequest(
            model=request.model,
            instructions=[instructions],
            contents=list(context.contents),
        )
        output = await self.client.generate_structured(llm_request, TeachingOutput)
        citations = request.context.citations
        grounding = check_grounding(
            _cited_text(output),
            citations,
            policy=self.policy,
        )
        return TeachingTurn(
            output=output,
            grounding=grounding,
            citations=citations,
            context=context,
        )


def instructions_for(mode: str) -> str:
    """取某个教学模式的指令。

    Args:
        mode: 教学模式。

    Returns:
        指令文本。

    Raises:
        ValueError: 教学模式不认识。
    """
    if mode == DIRECT:
        return DIRECT_TEACHING_INSTRUCTIONS
    if mode == SOCRATIC:
        # Day 34 交付苏格拉底式指令；这里先给出明确的错误，而不是静默退回直接教学
        # ——静默退回会让「模式」这件事变成假的 ✓。
        raise ValueError("苏格拉底式教学在 Day 34 交付")
    raise ValueError(f"未知的教学模式：{mode}")


def _cited_text(output: TeachingOutput) -> str:
    """取出教学输出里带标记的文本——门禁要校验的是**它**。

    **两处引用都要校验**：讲解正文里的行内标记 `[1]`，以及模型在 ``citations``
    字段里**声明**的标记。声明也是「我用了这些材料」的承诺——声明了不存在的标记
    与正文里写一个不存在的标记是一回事 ✓✓。

    **没标记的文字不管**：讲解与示例里的普通句子不是「有依据的说法」，门禁管不着
    ✓。

    Args:
        output: 教学输出。

    Returns:
        带标记的文本。
    """
    parts: list[str] = []
    if output.explanation:
        parts.append(output.explanation)
    for example in output.examples:
        parts.append(example.title)
        parts.extend(example.steps)
        parts.append(example.code)
        parts.append(example.question)
    if output.follow_up:
        parts.append(output.follow_up)
    # 声明的标记也要进门禁——把它渲染成标记的样子，门禁就能一起查 ✓。
    parts.extend(f"[{marker}]" for marker in output.citations)
    return "\n".join(part for part in parts if part)


@dataclass(frozen=True)
class TeachingCase:
    """一条教学评估用例。

    **标注的是「机制该做什么」**——不是「这段教学好不好」（那要人评）✓。
    """

    question: str
    """学习者问的问题。"""

    context: Mapping[str, Any] = field(default_factory=dict)
    """上下文的构成（画像 / 目标 / 进度 / 材料的描述）。"""

    expect_citations: tuple[str, ...] = ()
    """期望被引用到的标记。"""

    expect_examples: bool = True
    """期望带示例。"""

    expect_follow_up: bool = True
    """期望带下一步引导问题。"""

    category: str = ""
    """用例类别。"""

    note: str = ""
    """这条用例在考什么。"""

    @classmethod
    def from_dict(cls, item: Mapping[str, Any], *, index: int) -> TeachingCase:
        """从 JSON 条目构造用例。

        Args:
            item: JSON 条目。
            index: 条目序号（用于错误信息）。

        Returns:
            用例。

        Raises:
            ValueError: 条目结构不对。
        """
        question = item.get("question")
        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"第 {index} 条教学用例缺少 question")
        context = item.get("context", {})
        if not isinstance(context, Mapping):
            raise ValueError(f"第 {index} 条教学用例的 context 必须是对象")
        return cls(
            question=question,
            context=dict(context),
            expect_citations=tuple(str(x) for x in item.get("expect_citations", [])),
            expect_examples=bool(item.get("expect_examples", True)),
            expect_follow_up=bool(item.get("expect_follow_up", True)),
            category=str(item.get("category", "")),
            note=str(item.get("note", "")),
        )


@dataclass(frozen=True)
class TeachingCaseResult:
    """一条教学用例的结果。"""

    case: TeachingCase
    """被评估的用例。"""

    turn: TeachingTurn
    """教学结果。"""

    @property
    def has_explanation(self) -> bool:
        """有没有讲解。"""
        return bool(self.turn.output.explanation.strip())

    @property
    def has_examples(self) -> bool:
        """有没有示例。"""
        return bool(self.turn.output.examples)

    @property
    def examples_have_questions(self) -> bool:
        """示例有没有引导问题——**没有它，示例只是被动阅读** ✓。"""
        return all(example.question.strip() for example in self.turn.output.examples)

    @property
    def has_follow_up(self) -> bool:
        """有没有下一步引导问题。"""
        return bool(self.turn.output.follow_up.strip())

    @property
    def cited_markers(self) -> tuple[str, ...]:
        """教学输出里声明引用了哪些标记。"""
        return tuple(self.turn.output.citations)

    @property
    def unknown_markers(self) -> tuple[str, ...]:
        """声明了但本次没有的标记——**编造的引用** ✓。"""
        available = set(self.turn.citations.markers())
        return tuple(marker for marker in self.cited_markers if marker not in available)

    def describe(self) -> str:
        """一句话描述。

        Returns:
            描述文本。
        """
        return (
            f"{self.case.category}：{self.case.question[:20]}"
            f"——讲解 {'有' if self.has_explanation else '无'}，"
            f"示例 {len(self.turn.output.examples)} 个，"
            f"引用 {list(self.cited_markers)}"
        )


@dataclass(frozen=True)
class TeachingMetrics:
    """教学输出的指标。"""

    cases: int
    """用例总数。"""

    with_explanation: float = 0.0
    """有讲解的比例。"""

    with_examples: float = 0.0
    """有示例的比例。"""

    examples_with_questions: float = 0.0
    """示例带引导问题的比例——**没有它，示例只是被动阅读** ✓。"""

    with_follow_up: float = 0.0
    """有下一步引导问题的比例。"""

    cited_ratio: float = 0.0
    """声明了引用的比例。"""

    fabrication_rate: float = 0.0
    """声明的引用里有多少指向不存在的标记。"""

    grounded_rate: float = 0.0
    """通过引用门禁的比例。"""

    results: tuple[TeachingCaseResult, ...] = ()
    """每条用例的结果。"""

    def summary(self) -> str:
        """一句话摘要。

        Returns:
            摘要文本。
        """
        return (
            f"{self.cases} 条用例："
            f"讲解 {self.with_explanation:.2f}，"
            f"示例 {self.with_examples:.2f}，"
            f"示例带问题 {self.examples_with_questions:.2f}，"
            f"下一步 {self.with_follow_up:.2f}，"
            f"引用 {self.cited_ratio:.2f}，"
            f"编造率 {self.fabrication_rate:.2f}，"
            f"过门禁 {self.grounded_rate:.2f}"
        )


def evaluate_teaching(results: Sequence[TeachingCaseResult]) -> TeachingMetrics:
    """评估一批教学结果。

    Args:
        results: 用例结果。

    Returns:
        指标。
    """
    total = len(results)
    if total == 0:
        return TeachingMetrics(cases=0)
    cited_total = sum(len(result.cited_markers) for result in results)
    fabricated_total = sum(len(result.unknown_markers) for result in results)
    return TeachingMetrics(
        cases=total,
        with_explanation=sum(1 for r in results if r.has_explanation) / total,
        with_examples=sum(1 for r in results if r.has_examples) / total,
        examples_with_questions=sum(1 for r in results if r.examples_have_questions) / total,
        with_follow_up=sum(1 for r in results if r.has_follow_up) / total,
        cited_ratio=sum(1 for r in results if r.cited_markers) / total,
        fabrication_rate=fabricated_total / cited_total if cited_total else 0.0,
        grounded_rate=sum(1 for r in results if r.turn.deliver) / total,
        results=tuple(results),
    )


def load_teaching_cases(path: str | Path) -> list[TeachingCase]:
    """从 JSON 加载教学用例。

    Args:
        path: 文件路径。

    Returns:
        用例列表。

    Raises:
        ValueError: 文件结构不对。
    """
    import json
    from pathlib import Path

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = data.get("cases")
    if not isinstance(raw, list) or not raw:
        raise ValueError("教学评估集至少要有一条用例")
    return [
        TeachingCase.from_dict(item, index=index)
        for index, item in enumerate(raw, start=1)
        if isinstance(item, Mapping)
    ]


__all__ = [
    "DIRECT",
    "DIRECT_TEACHING_INSTRUCTIONS",
    "SOCRATIC",
    "DirectTeacher",
    "TeachingCase",
    "TeachingCaseResult",
    "TeachingMetrics",
    "TeachingOutput",
    "TeachingRequest",
    "TeachingTurn",
    "evaluate_teaching",
    "instructions_for",
    "load_teaching_cases",
    "render_output",
]
