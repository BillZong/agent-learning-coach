"""苏格拉底式教学：问、听、判断、下一步。

**三阶段框架**（GuideEval，Liu et al.，Findings of EMNLP 2026）把「教学引导」拆成
三件事，本项目照着实现：

| 阶段 | 问什么 | 本项目的实现 |
| ---- | ------ | ------------ |
| **Assessment** | 学习者现在是什么状态？ | `SocraticAssessment.understanding` |
| **Orchestration** | 下一步该换哪种策略？ | `next_step`：追问 / 提示 / 揭示 / 下一个知识点 |
| **Elicitation** | 怎么问才能引出真正的思考？ | `SocraticQuestion`：问题 + 提示 + 追问 |

**为什么要按这个框架做**：同一篇论文的实测结论是——**现有 LLM 在学习者困惑或需要
引导时，常常给不出有效的适应性支架**（它们是「通用教练」而不是「善解人意的教练」）
✓✓。也就是说：**「用苏格拉底式的语气问问题」不等于「在引导」**——引导的核心是
**根据学习者的状态调整策略** ✓。

所以本模块的重点不是「怎么问」，而是**「听完学习者的回答之后，判断并调整」** ✓。

**模式切换**也在这一层：学习者卡住时，继续追问可能没用——该**揭示答案**（切回直接
教学）✓。什么时候切是**业务判断**，由策略给（``choose_next_mode``）✓。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from agent_learning_coach.core.application.teaching import (
    DIRECT,
    SOCRATIC,
    TeachingMetrics,
    TeachingRequest,
)
from agent_learning_coach.core.application.tutor_context import TutorContextBuilder
from agent_learning_coach.core.llm.models import LLMRequest
from agent_learning_coach.core.llm.protocol import LLMClient

CONFUSED = "confused"
"""学习者没听懂或答错了。"""

PARTIAL = "partial"
"""学习者答对了一部分。"""

CORRECT = "correct"
"""学习者答对了。"""

PROBE = "probe"
"""继续追问——学习者答得对，但可以再推一步。"""

HINT = "hint"
"""给提示——学习者卡住了，但还救得回来。"""

REVEAL = "reveal"
"""揭示答案——继续问没有意义了（或者学习者明确要求）。"""

ADVANCE = "advance"
"""进入下一个知识点。"""

ASSESS_INSTRUCTIONS = """\
你正在判断学习者对你上一个问题的回答。

按下面三件事回答：

1. **判断学习者的状态**：
   - confused：没懂、答错、或者答非所问；
   - partial：懂了一部分，但对得不全或者说不清楚；
   - correct：答对了。
2. **说明你的判断依据**：引用学习者回答里的具体说法，不要泛泛而谈。
3. **决定下一步**：
   - probe：答对了，但可以再推一步（换个角度、问边界情况）；
   - hint：卡住了，但还救得回来——给一个提示的方向，不要给答案；
   - reveal：问不动了，或者学习者明确要求直接讲——该揭示答案；
   - advance：这个知识点够了，进入下一个。

如果学习者答对了并且没必要再问，就选 advance。
"""
"""判断回答的指令（Assessment + Orchestration 两阶段）✓。"""

QUESTION_INSTRUCTIONS = """\
你正在用提问的方式教一个知识点。

按下面要求提问：

1. **一次只问一个问题**。问题要能引导学习者自己得出结论，不是考他。
2. **不要直接给答案**——除非学习者明确要求，或者已经追问过两轮还卡着。
3. **问题要能引出反思**：问「为什么」「如果……会怎样」「你怎么知道」这类需要他自己
   推理的问题。
4. **准备一个提示**：学习者答不上来时给方向，不给结论。
5. **基于材料**：材料里有的说法要引用（句末写 [1] 这样的标记）；材料里没有的，
   说明这是你的补充。
6. **一次只推进一小步**——跟着学习者的回答走，不要按自己的教案一路讲下去。
"""
"""提问的指令（Elicitation 阶段）✓。"""


class SocraticQuestion(BaseModel):
    """一次追问——**引出思考**的那一句。"""

    question: str = Field(default="", description="要问学习者的问题")
    rationale: str = Field(default="", description="为什么这样问（给教练看）")
    hint: str = Field(default="", description="学习者卡住时给的提示方向")
    citations: list[str] = Field(default_factory=list, description="引用到的材料标记")


class SocraticAssessment(BaseModel):
    """对学习者回答的理解——**Assessment + Orchestration** 两阶段的结果。"""

    understanding: str = Field(default="", description="confused / partial / correct")
    reasoning: str = Field(default="", description="判断依据")
    next_step: str = Field(default="", description="probe / hint / reveal / advance")
    follow_up_question: str = Field(default="", description="下一步要问的问题")


@dataclass(frozen=True)
class SocraticTurn:
    """一次苏格拉底式回合。"""

    question: SocraticQuestion
    """提出的问题。"""

    mode: str = SOCRATIC
    """这一轮用的模式。"""

    citations: tuple[str, ...] = ()
    """本轮可用的引用标记。"""

    def render(self) -> str:
        """渲染成给学习者看的文本。

        **不给答案**——这是苏格拉底式的全部意义 ✓。

        Returns:
            教学文本。
        """
        parts = [self.question.question]
        if self.question.hint:
            parts.append(f"（如果卡住：{self.question.hint}）")
        return "\n\n".join(part for part in parts if part)


@dataclass(frozen=True)
class SocraticDecision:
    """下一步的决定——**模式要不要切**。"""

    mode: str
    """下一轮用什么模式。"""

    reason: str
    """为什么这么决定。"""

    step: str = ""
    """下一步的动作（probe / hint / reveal / advance）。"""


def choose_next_mode(assessment: SocraticAssessment) -> SocraticDecision:
    """根据对学习者回答的判断，决定下一轮的模式。

    **揭示答案就切回直接教学**——继续追问没有意义时还问，是在消耗学习者的耐心 ✓。

    Args:
        assessment: 对回答的判断。

    Returns:
        决定。
    """
    step = assessment.next_step.strip() or PROBE
    if step == REVEAL:
        return SocraticDecision(
            mode=DIRECT,
            reason="学习者问不动了（或明确要求直接讲）——切回直接教学",
            step=step,
        )
    if assessment.understanding.strip() == CORRECT and step == ADVANCE:
        return SocraticDecision(
            mode=SOCRATIC,
            reason="这个知识点够了——进入下一个",
            step=step,
        )
    return SocraticDecision(
        mode=SOCRATIC,
        reason=f"继续用提问的方式引导（{assessment.understanding.strip() or '未知'}）",
        step=step,
    )


def choose_mode_for_request(
    request: TeachingRequest,
    *,
    learner_message: str = "",
) -> str:
    """按学习者这一轮说的话选模式。

    **学习者明确要求直接讲，就直接讲**——苏格拉底式不是目的，是手段 ✓。

    Args:
        request: 教学请求。
        learner_message: 学习者这一轮说的话。

    Returns:
        模式（``direct`` / ``socratic``）。
    """
    text = learner_message.strip().lower()
    direct_signals = ("直接说", "直接讲", "告诉我答案", "别问了", "不要说废话")
    if any(signal in text for signal in direct_signals):
        return DIRECT
    return request.mode


@dataclass(frozen=True)
class SocraticTeacher:
    """苏格拉底式教学。

    **它只做两件事**：提问（Elicitation）与理解回答（Assessment + Orchestration）✓。
    装配、引用与门禁与直接教学共用 ✓（Day 33 的设计 ✓）。
    """

    client: LLMClient
    """模型客户端。"""

    context_builder: TutorContextBuilder = field(default_factory=TutorContextBuilder)
    """上下文构建器（Day 32 ✓）——与直接教学共用同一套装配 ✓。"""

    model: str | None = None
    """模型名。为 None 时用客户端的默认 ✓。"""

    async def ask(self, request: TeachingRequest) -> SocraticTurn:
        """提一个问题。

        Args:
            request: 教学请求。

        Returns:
            回合（含问题）。

        Raises:
            LLMError: 模型调用失败。
        """
        from agent_learning_coach.core.application.teaching import instructions_for

        context = self.context_builder.build(request.context)
        llm_request = LLMRequest(
            model=request.model or self.model,
            instructions=[instructions_for(SOCRATIC), QUESTION_INSTRUCTIONS],
            contents=list(context.contents),
        )
        question = await self.client.generate_structured(llm_request, SocraticQuestion)
        return SocraticTurn(
            question=question,
            mode=SOCRATIC,
            citations=tuple(request.context.citations.markers()),
        )

    async def assess(
        self,
        request: TeachingRequest,
        learner_answer: str,
    ) -> SocraticAssessment:
        """理解学习者的回答。

        Args:
            request: 教学请求。
            learner_answer: 学习者的回答。

        Returns:
            判断（含下一步）。

        Raises:
            LLMError: 模型调用失败。
        """
        from agent_learning_coach.core.llm.types import Message

        context = self.context_builder.build(request.context)
        llm_request = LLMRequest(
            model=request.model or self.model,
            instructions=[ASSESS_INSTRUCTIONS],
            contents=[
                *context.contents,
                Message(role="user", content=f"我的回答：{learner_answer}"),
            ],
        )
        return await self.client.generate_structured(llm_request, SocraticAssessment)


@dataclass(frozen=True)
class SocraticCase:
    """一条苏格拉底式评估用例。

    **标注的是「机制该做什么」**——不是「这个教练问得好不好」✓。
    """

    learner_message: str
    """学习者这一轮说的话。"""

    expect_understanding: str = ""
    """期望的判断（confused / partial / correct）。"""

    expect_step: str = ""
    """期望的下一步（probe / hint / reveal / advance）。"""

    expect_mode: str = ""
    """期望的模式。"""

    category: str = ""
    """用例类别。"""

    note: str = ""
    """这条用例在考什么。"""

    @classmethod
    def from_dict(cls, item: dict, *, index: int) -> SocraticCase:
        """从 JSON 条目构造用例。

        Args:
            item: JSON 条目。
            index: 条目序号（用于错误信息）。

        Returns:
            用例。

        Raises:
            ValueError: 条目结构不对。
        """
        message = item.get("learner_message")
        if not isinstance(message, str) or not message.strip():
            raise ValueError(f"第 {index} 条苏格拉底用例缺少 learner_message")
        return cls(
            learner_message=message,
            expect_understanding=str(item.get("expect_understanding", "")),
            expect_step=str(item.get("expect_step", "")),
            expect_mode=str(item.get("expect_mode", "")),
            category=str(item.get("category", "")),
            note=str(item.get("note", "")),
        )


@dataclass(frozen=True)
class SocraticMetrics:
    """苏格拉底式教学的指标。"""

    cases: int
    """用例总数。"""

    understanding_accuracy: float = 0.0
    """判断准确率——**模型会不会读懂学习者** ✓。"""

    step_accuracy: float = 0.0
    """下一步准确率——**会不会调整策略** ✓。"""

    mode_accuracy: float = 0.0
    """模式切换准确率。"""

    has_question: float = 0.0
    """给出了问题的比例。"""

    has_hint: float = 0.0
    """准备了提示的比例——**没有提示，学习者卡住就只能干等** ✓。"""

    cited_ratio: float = 0.0
    """声明了引用的比例（与直接教学同口径 ✓）。"""

    fabrication_rate: float = 0.0
    """声明的引用里有多少指向不存在的标记（与直接教学同口径 ✓）。"""

    grounded_rate: float = 0.0
    """通过引用门禁的比例（与直接教学同口径 ✓）。"""

    def summary(self) -> str:
        """一句话摘要。

        Returns:
            摘要文本。
        """
        return (
            f"{self.cases} 条用例："
            f"判断准确率 {self.understanding_accuracy:.2f}，"
            f"下一步准确率 {self.step_accuracy:.2f}，"
            f"模式准确率 {self.mode_accuracy:.2f}，"
            f"有提问 {self.has_question:.2f}，"
            f"有提示 {self.has_hint:.2f}，"
            f"引用 {self.cited_ratio:.2f}，"
            f"编造率 {self.fabrication_rate:.2f}，"
            f"过门禁 {self.grounded_rate:.2f}"
        )


@dataclass(frozen=True)
class SocraticCaseResult:
    """一条苏格拉底式用例的结果。"""

    case: SocraticCase
    """被评估的用例。"""

    assessment: SocraticAssessment
    """对学习者回答的判断。"""

    question: SocraticQuestion | None = None
    """提出的问题（如果有）。"""

    cited_markers: tuple[str, ...] = ()
    """声明引用了哪些标记。"""

    unknown_markers: tuple[str, ...] = ()
    """声明了但本次没有的标记。"""

    grounded: bool = True
    """是否通过引用门禁。"""

    decision: SocraticDecision | None = None
    """下一步的模式决定。"""

    @property
    def has_question(self) -> bool:
        """有没有提出问题。"""
        return bool(self.question and self.question.question.strip())

    @property
    def has_hint(self) -> bool:
        """有没有准备提示。"""
        return bool(self.question and self.question.hint.strip())

    def describe(self) -> str:
        """一句话描述。

        Returns:
            描述文本。
        """
        return (
            f"{self.case.category}：{self.case.learner_message[:16]}"
            f"——判断 {self.assessment.understanding or '未知'}，"
            f"下一步 {self.assessment.next_step or '未给'}"
        )


def evaluate_socratic(results: Sequence[SocraticCaseResult]) -> SocraticMetrics:
    """评估一批苏格拉底式用例。

    Args:
        results: 用例结果。

    Returns:
        指标。
    """
    total = len(results)
    if total == 0:
        return SocraticMetrics(cases=0)
    understanding_hits = sum(
        1
        for result in results
        if result.case.expect_understanding
        and result.assessment.understanding == result.case.expect_understanding
    )
    understanding_total = sum(1 for result in results if result.case.expect_understanding)
    step_hits = sum(
        1
        for result in results
        if result.case.expect_step and result.assessment.next_step == result.case.expect_step
    )
    step_total = sum(1 for result in results if result.case.expect_step)
    mode_hits = sum(
        1
        for result in results
        if result.case.expect_mode
        and result.decision is not None
        and result.decision.mode == result.case.expect_mode
    )
    mode_total = sum(1 for result in results if result.case.expect_mode)
    return SocraticMetrics(
        cases=total,
        understanding_accuracy=(understanding_hits / understanding_total)
        if understanding_total
        else 0.0,
        step_accuracy=(step_hits / step_total) if step_total else 0.0,
        mode_accuracy=(mode_hits / mode_total) if mode_total else 0.0,
        has_question=sum(1 for result in results if result.has_question) / total,
        has_hint=sum(1 for result in results if result.has_hint) / total,
        cited_ratio=sum(1 for result in results if result.cited_markers) / total,
        fabrication_rate=(
            sum(len(result.unknown_markers) for result in results)
            / sum(len(result.cited_markers) for result in results)
            if sum(len(result.cited_markers) for result in results)
            else 0.0
        ),
        grounded_rate=sum(1 for result in results if result.grounded) / total,
    )


@dataclass(frozen=True)
class ModeComparison:
    """两种教学模式的对比。

    **只比共同的部分**：引用声明率、编造率、过门禁率 ✓。差异的部分
    （讲解 vs 提问）**不可比**——它们回答的是不同的问题 ✓✓。

    **效果的对比要真实学习者**：脚本化模型只能证明「管道通不通」✓。
    """

    direct: TeachingMetrics
    """直接教学的指标。"""

    socratic: SocraticMetrics
    """苏格拉底式的指标。"""

    def summary(self) -> str:
        """一句话摘要。

        Returns:
            摘要文本。
        """
        return (
            "共同指标（引用声明率 / 编造率 / 过门禁率）："
            f"直接教学 {self.direct.cited_ratio:.2f} / "
            f"{self.direct.fabrication_rate:.2f} / {self.direct.grounded_rate:.2f}；"
            f"苏格拉底式 {self.socratic.cited_ratio:.2f} / "
            f"{self.socratic.fabrication_rate:.2f} / {self.socratic.grounded_rate:.2f}。"
            "差异部分（讲解 vs 提问）不可比——效果的对比要真实学习者。"
        )


__all__ = [
    "ADVANCE",
    "ASSESS_INSTRUCTIONS",
    "CONFUSED",
    "CORRECT",
    "HINT",
    "PARTIAL",
    "PROBE",
    "QUESTION_INSTRUCTIONS",
    "REVEAL",
    "ModeComparison",
    "SocraticAssessment",
    "SocraticCase",
    "SocraticCaseResult",
    "SocraticDecision",
    "SocraticMetrics",
    "SocraticQuestion",
    "SocraticTeacher",
    "SocraticTurn",
    "choose_mode_for_request",
    "choose_next_mode",
    "evaluate_socratic",
]
