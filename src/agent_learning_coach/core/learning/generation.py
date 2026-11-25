"""诊断题生成——把「模型能写题」与「题目是否合格」分成两件事。

Topic 21 的工程问题是：**生成的诊断题如何做结构化校验？无效题目应该在生成层
拦截，还是在评估层兜底？**

答案是**在生成层拦截**，理由有三层：

1. **一道坏题会污染判定。** 题目若与目标知识点无关，学习者答错时，我们分不清
   是「他不会」还是「题问偏了」——判定因此不可信。坏题必须挡住，不能流到
   学习者面前。
2. **生成层是唯一能重试的地方。** 无效输出在生成层可以要求模型重来；到了
   评估层，题目已经呈现给学习者，只能事后解释。**问题在能修的地方修**。
3. **兜底是最后的防线，不是主要防线。** 评估层仍要校验（题目可能来自存储、
   导入或历史数据），但那是防御性检查，不是正常路径。

因此校验分两层，各管各的：

| 层 | 校验什么 | 失败怎么办 |
| -- | -------- | ---------- |
| **结构层**（``ExerciseSchema``） | 字段齐全、类型正确、长度合理 | 重试（带错误反馈） |
| **领域层**（``DiagnosticExercise`` 构造） | 题目针对知识点、难度合法 | 重试（带错误反馈） |

**结构层是「模型有没有按格式回答」，领域层是「这个回答在业务上成不成立」。**
两层的失败信息都会回喂给模型——模型能看见自己错在哪，重试才有意义。

**生成器不认识学习领域的其他部分。** 它只接收「要考哪个知识点、要什么难度」
这样的输入，产出 ``DiagnosticExercise``。诊断流程、判定、准入条件都在
``diagnosis.py``（ADR-0020）——生成器不重复这些规则。

题目与判定分开：题目是**题目**（考什么、期望答案是什么），判定是**判定**
（学习者会不会）。前者由本模块产出，后者由诊断流程产生。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from agent_learning_coach.core.learning.diagnosis import DiagnosticExercise
from agent_learning_coach.core.learning.domain import DomainError, KnowledgePointId
from agent_learning_coach.core.llm.errors import LLMError, LLMParseError
from agent_learning_coach.core.llm.models import LLMRequest
from agent_learning_coach.core.llm.protocol import LLMClient
from agent_learning_coach.core.llm.types import Message

ExerciseDifficulty = Literal["basic", "intermediate", "advanced"]
"""题目难度。

难度是**题目**的属性，不是知识点的属性——同一个知识点可以出不同难度的题。
「知识点有多难」属于课程侧（ADR-0017 已决定难度不建模为目标约束）；「这道题
有多难」是出题时的选择，因此放在题目上。
"""

_DIFFICULTY_LABELS: dict[ExerciseDifficulty, str] = {
    "basic": "基础",
    "intermediate": "进阶",
    "advanced": "高级",
}

_MIN_PROMPT_LENGTH = 8
"""题面最短长度（字符）。

短于此的题面多半是「闭包是什么」这种没有信息量的反问——它能测出什么？答：
测不出「会」与「不会」的差别。因此它作为**结构层**的长度下限。
"""

_MIN_ANSWER_LENGTH = 4
"""参考答案最短长度（字符）。"""


class ExerciseSchema(BaseModel):
    """模型生成诊断题时的输出结构。

    这是**结构层**的契约：字段齐全、类型正确、长度合理。它不判断题目是否
    考对了知识点——那是领域层的事。
    """

    prompt: str = Field(
        min_length=_MIN_PROMPT_LENGTH,
        description="题面：一个能暴露理解程度的具体问题",
    )
    expected_answer: str = Field(
        min_length=_MIN_ANSWER_LENGTH,
        description="参考答案：判定作答对错的标准",
    )
    difficulty: ExerciseDifficulty = Field(
        description="题目难度：basic / intermediate / advanced",
    )
    rationale: str = Field(
        min_length=_MIN_ANSWER_LENGTH,
        description="出题理由：这道题如何检验目标知识点",
    )


@dataclass(frozen=True)
class ExerciseRequest:
    """出题请求——**值对象**。

    只包含「要考什么」与「要什么难度」。**刻意不含学习者信息**：诊断题考的是
    知识点，不是针对某个人的定制题（定制属 Topic 22 的诊断策略）。
    """

    point_id: KnowledgePointId
    """要考的知识点。"""

    point_title: str
    """知识点名称，作为出题依据。"""

    point_summary: str
    """知识点摘要，作为出题依据。"""

    difficulty: ExerciseDifficulty = "intermediate"
    """要求的题目难度。"""

    target_outcome: str = ""
    """要验证的能力结果（来自目标的能力要求），供模型对齐考什么。"""


@dataclass(frozen=True)
class ExerciseGenerationError(Exception):
    """题目生成失败。

    失败发生在**重试耗尽之后**：每一步的失败都已尝试用反馈重试过。
    """

    message: str
    """失败原因。"""

    attempts: int
    """尝试次数。"""

    def __str__(self) -> str:
        """返回可读的错误信息。"""
        return f"{self.message}（已尝试 {self.attempts} 次）"


class ExerciseGenerator:
    """诊断题生成器。

    它是**唯一**同时认识模型与学习领域的地方：调用模型拿结构化输出，校验后
    产出领域对象 ``DiagnosticExercise``。

    生成器**不认识诊断流程**：它不判断「这次诊断够不够驱动计划生成」
    （ADR-0020），也不判断「学习者会不会」（那是判定的事）。它只管一件事——
    **产出一道合格的题目**。
    """

    def __init__(
        self,
        client: LLMClient,
        *,
        model: str | None = None,
        max_attempts: int = 2,
    ) -> None:
        """初始化生成器。

        Args:
            client: 模型客户端。
            model: 模型名；``None`` 时由客户端自行决定。
            max_attempts: 题目不合格时的最大尝试次数（含首次），至少为 1。
        """
        if max_attempts < 1:
            raise ValueError("max_attempts 至少为 1")
        self.client = client
        self.model = model
        self.max_attempts = max_attempts

    async def generate(self, request: ExerciseRequest) -> DiagnosticExercise:
        """为某个知识点生成一道诊断题。

        流程：调用模型 → 结构层校验 → 领域层校验 →（失败则带反馈重试）。

        Args:
            request: 出题请求。

        Returns:
            合格的题目。

        Raises:
            ExerciseGenerationError: 重试耗尽仍未产出合格题目。
        """
        feedback = ""
        last_error = ""
        for _ in range(self.max_attempts):
            request_obj = self._build_request(request, feedback)
            try:
                result = await self.client.generate_structured(request_obj, ExerciseSchema)
            except LLMParseError as exc:
                last_error = f"输出不符合要求的结构：{_summarize_parse_error(exc)}"
                feedback = self._feedback(last_error)
                continue
            except LLMError as exc:
                # 调用失败（超时、限流、服务端错误）不是「题目不合格」，重试也不会
                # 变好——直接上抛，让调用方按自己的策略处理。
                raise ExerciseGenerationError(
                    f"调用模型失败：{exc}",
                    attempts=self.max_attempts,
                ) from exc

            try:
                return self._to_exercise(request, result)
            except DomainError as exc:
                last_error = str(exc)
                feedback = self._feedback(last_error)

        raise ExerciseGenerationError(
            last_error or "模型未能产出合格的诊断题",
            attempts=self.max_attempts,
        )

    def _build_request(self, request: ExerciseRequest, feedback: str) -> LLMRequest:
        """构造模型请求。

        Args:
            request: 出题请求。
            feedback: 上一次失败的原因；首次为空。

        Returns:
            模型请求。
        """
        instructions = [_INSTRUCTION]
        if feedback:
            instructions.append(feedback)
        return LLMRequest(
            model=self.model,
            instructions=instructions,
            contents=[Message(role="user", content=_render_request(request))],
        )

    def _to_exercise(self, request: ExerciseRequest, result: ExerciseSchema) -> DiagnosticExercise:
        """把模型输出转成领域题目。

        Args:
            request: 出题请求。
            result: 模型的结构化输出。

        Returns:
            领域题目。

        Raises:
            DomainError: 题目在业务上不成立（难度不符、题目与知识点无关等）。
        """
        if result.difficulty != request.difficulty:
            raise DomainError(
                f"题目难度为{_DIFFICULTY_LABELS[result.difficulty]}，"
                f"与要求的{_DIFFICULTY_LABELS[request.difficulty]}不符",
            )
        if not _addresses_point(result, request):
            raise DomainError(
                f"题目没有针对知识点「{request.point_title}」，无法用于判定该知识点",
            )
        return DiagnosticExercise(
            id=f"ex-{request.point_id.value}",
            point_id=request.point_id,
            prompt=result.prompt,
            expected_answer=result.expected_answer,
            difficulty=result.difficulty,
            rationale=result.rationale,
        )

    def _feedback(self, error: str) -> str:
        """把失败原因转成给模型的反馈。

        模型能看见自己错在哪，重试才有意义——只说「失败了」等于没给信息。

        Args:
            error: 失败原因。

        Returns:
            反馈指令。
        """
        return (
            f"上一次生成的题目不合格：{error}\n"
            "请重新生成，确保题面针对目标知识点、难度符合要求、"
            "并给出可判定的参考答案与出题理由。"
        )


def _summarize_parse_error(exc: LLMParseError) -> str:
    """把结构化解析错误压成一句人能读的话。

    pydantic 的原始错误里带着 ``ctx``、``url``、``input`` 等字段，直接回喂给模型
    既冗长又无用——模型需要知道的是「哪个字段不合法」，不是完整的校验上下文。

    Args:
        exc: 解析异常。

    Returns:
        简短说明。
    """
    message = str(exc)
    marker = "结构:"
    if "不符合" in message and marker in message:
        detail = message.split(marker, 1)[-1].strip()
        return _summarize_validation_errors(detail)
    return message[:300]


def _summarize_validation_errors(detail: str) -> str:
    """把 pydantic 的错误列表压成「字段：原因」的短句。

    pydantic 的错误列表是 **Python 字面量**（单引号、元组），不是 JSON——因此用
    ``ast.literal_eval`` 解析；解析不了就原样截断返回，不抛异常。

    Args:
        detail: pydantic 错误列表的字面表示。

    Returns:
        简短说明。
    """
    try:
        errors = ast.literal_eval(detail)
    except (SyntaxError, ValueError):
        return detail[:300]
    if not isinstance(errors, list):
        return detail[:300]
    parts: list[str] = []
    for item in errors:
        if not isinstance(item, dict):
            continue
        location = ".".join(str(part) for part in item.get("loc", ()))
        message = str(item.get("msg", ""))
        parts.append(f"{location or '输出'}：{message}")
    if not parts:
        return detail[:300]
    return "；".join(parts)


def _render_request(request: ExerciseRequest) -> str:
    """把出题请求渲染成给模型的文本。

    Args:
        request: 出题请求。

    Returns:
        文本。
    """
    lines = [
        "请为下面的知识点出一道诊断题。",
        f"知识点：{request.point_title}",
    ]
    if request.point_summary:
        lines.append(f"知识点摘要：{request.point_summary}")
    if request.target_outcome:
        lines.append(f"要验证的能力：{request.target_outcome}")
    lines.append(f"要求难度：{_DIFFICULTY_LABELS[request.difficulty]}")
    lines.append("题面必须是一个能暴露理解程度的具体问题，不要问「你知道什么是 X 吗」。")
    return "\n".join(lines)


def _addresses_point(result: ExerciseSchema, request: ExerciseRequest) -> bool:
    """题目是否针对目标知识点。

    判定方式：题面或出题理由里出现知识点名称的实词。这是**弱检查**——它挡得住
    「题目完全跑题」，挡不住「题目相关但角度偏了」。后者需要更强的判断（模型
    打分或人工审阅），留给 Topic 22 的诊断策略。

    Args:
        result: 模型输出。
        request: 出题请求。

    Returns:
        针对目标知识点则为 ``True``。
    """
    title = request.point_title.strip()
    if not title:
        return True
    if title in result.prompt or title in result.rationale:
        return True
    tokens = [token for token in _split_title(title) if len(token) >= 2]
    if not tokens:
        return True
    haystack = f"{result.prompt}\n{result.rationale}"
    return any(token in haystack for token in tokens)


def _split_title(title: str) -> list[str]:
    """把知识点名称切成可匹配的实词。

    中文没有词边界，按二元组切（「闭包」切成「闭」「包」）；英文按空白切。

    Args:
        title: 知识点名称。

    Returns:
        实词列表。
    """
    from agent_learning_coach.core.text import tokenize

    return sorted(tokenize(title))


_INSTRUCTION = """\
你是一名技术教练，正在为学习者出**诊断题**——用来判断学习者是否真的掌握某个知识点。

规则：
1. 题面必须是一个能暴露理解程度的具体问题，不要问「你知道什么是 X 吗」这种反问。
2. 题面必须针对给定的知识点，不要考别的。
3. 参考答案必须可判定：学习者的回答能对照它分出对错。
4. 难度必须与要求一致。
5. 出题理由要说清这道题如何检验目标知识点。
"""


__all__ = [
    "ExerciseDifficulty",
    "ExerciseGenerator",
    "ExerciseGenerationError",
    "ExerciseRequest",
    "ExerciseSchema",
]
