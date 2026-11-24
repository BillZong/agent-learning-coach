"""评估：用确定性的脚本化模型验证运行时**行为**。

**评估的不是「回答好不好」，是「行为对不对」。** Phase 0 的 Agent 还没有真实的
业务答案可评；能评的是机制：工具调用是否按预期发生、失败是否被归一化回填而
不是炸掉、停止原因是否符合预期、最终输出是否被交付。这些都能用脚本化的模型
响应确定性地复现。

**为什么不用 LLM-as-judge**：判官本身不确定。用它给「机制」打分，等于用一个
不确定的东西去验证另一个不确定的东西。语义质量评估是 Phase 2 的事，到时有
真实教学场景与人工标注再谈（见 ADR-0014）。

**用例是数据**：``EvalCase`` 从 JSON 加载，脚本化响应、期望的停止原因与工具
调用都写在数据里。加一条用例改一行 JSON，不需要改代码；同一个数据集既跑
示例也进 CI，因此「评估通过」不是一次性的手工结论。

**评估只看行为，不看成本**：脚本化响应的 token 用量是 0，因此用例报告里的
用量没有意义。成本与延迟由 ``observability`` 那条线负责——两者不要混着看。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from agent_learning_coach.core.agent.observability import RunTrace, build_trace
from agent_learning_coach.core.agent.runtime import AgentRuntime
from agent_learning_coach.core.agent.types import AgentConfig, AgentResult
from agent_learning_coach.core.llm.models import LLMRequest, LLMResponse
from agent_learning_coach.core.llm.types import Message, ToolCall
from agent_learning_coach.core.tools.dispatcher import ToolDispatcher
from agent_learning_coach.core.tools.registry import ToolRegistry
from agent_learning_coach.core.tools.types import FunctionTool, ToolDefinition

EVAL_MODEL = "eval-scripted"
"""评估用例里脚本化响应上报的模型名。

它不对应任何真实供应商——出现的唯一意义是让成本核算能查表（价格表为空时
不查），以及让日志里能看出「这是评估运行」。
"""


class ScriptedLLMClient:
    """按脚本依次返回预设响应的模型客户端。

    **确定性的驱动器**：给定同一份脚本，运行结果完全一致——因此它既用于评估
    用例，也用于测试。``repeat_last=True`` 时脚本耗尽后重复最后一个响应，
    用来模拟「模型一直请求工具」的死循环；默认耗尽即报错，让写错的用例立刻
    暴露，而不是静默通过。
    """

    def __init__(
        self,
        responses: Sequence[LLMResponse],
        *,
        repeat_last: bool = False,
    ) -> None:
        """初始化脚本化客户端。

        Args:
            responses: 依次返回的响应。
            repeat_last: 脚本耗尽后是否重复最后一个响应。
        """
        self.responses = list(responses)
        self.repeat_last = repeat_last
        self.requests: list[LLMRequest] = []
        self._last_response: LLMResponse | None = None

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """返回脚本中的下一个响应。

        Args:
            request: 模型请求。

        Returns:
            脚本中的下一个响应。

        Raises:
            AssertionError: 脚本已耗尽且未开启 ``repeat_last``。
        """
        self.requests.append(request)
        if not self.responses:
            if self.repeat_last and self._last_response is not None:
                return self._last_response
            raise AssertionError("脚本已耗尽：用例的 turns 少于运行实际需要的步数")
        response = self.responses.pop(0)
        self._last_response = response
        return response

    async def generate_structured(
        self,
        request: LLMRequest,
        output_model: type[Any],
    ) -> Any:  # pragma: no cover - Agent Loop 不使用结构化输出
        """结构化输出接口。

        评估走 Agent Loop，结构化输出通过 ``final_answer`` 工具提交，因此这里
        不需要实现。
        """
        raise NotImplementedError


@dataclass(frozen=True)
class ScriptedTurn:
    """脚本里的一步：模型说一句话，或者请求一次工具调用。

    二者**恰好**取其一——都写或都不写都是用例错误，构造时直接拒绝。
    """

    text: str | None = None
    """模型输出的文本。"""

    tool: str | None = None
    """模型请求调用的工具名。"""

    arguments: Mapping[str, Any] = field(default_factory=dict)
    """工具参数。"""

    def __post_init__(self) -> None:
        """校验二者取其一。

        Raises:
            ValueError: 同时给出或都没给出。
        """
        if (self.text is None) == (self.tool is None):
            raise ValueError("一步脚本必须二选一：text（回答）或 tool（工具调用）")
        if self.tool is not None and not self.tool.strip():
            raise ValueError("tool 不能为空字符串")

    def to_response(self, *, call_id: str) -> LLMResponse:
        """把一步脚本转成模型响应。

        Args:
            call_id: 工具调用 id（仅工具调用步需要）。

        Returns:
            模型响应。
        """
        if self.tool is not None:
            return LLMResponse(
                model=EVAL_MODEL,
                contents=[
                    ToolCall(
                        tool_call_id=call_id,
                        name=self.tool,
                        arguments=dict(self.arguments),
                    )
                ],
            )
        return LLMResponse(
            model=EVAL_MODEL,
            contents=[Message(role="assistant", content=self.text or "")],
        )


@dataclass(frozen=True)
class EvalCase:
    """一条评估用例。

    期望值是**行为契约**：停止原因、按顺序调用了哪些工具、哪些工具应当失败、
    最终输出包含什么。断言这些，而不是断言「模型说了什么漂亮话」。
    """

    name: str
    """用例名，报告里用它定位。"""

    prompt: str
    """用户输入。"""

    turns: tuple[ScriptedTurn, ...]
    """脚本化的模型响应。"""

    expected_stop_reason: str = "completed"
    """期望的停止原因。"""

    expected_tools: tuple[str, ...] = ()
    """期望按顺序执行的工具名。"""

    expected_tool_errors: tuple[str, ...] = ()
    """期望失败的工具名（按顺序）。用它验证「失败被归一化回填」。"""

    expected_output_contains: str | None = None
    """期望最终输出包含的子串。None 表示不检查。"""

    max_steps: int = 8
    """本次运行允许的最大步数。"""

    def to_responses(self) -> list[LLMResponse]:
        """把脚本转成模型响应列表。

        Returns:
            模型响应列表。
        """
        return [
            turn.to_response(call_id=f"call-{index + 1}") for index, turn in enumerate(self.turns)
        ]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], *, index: int = 0) -> EvalCase:
        """从字典构造用例。

        Args:
            data: 用例数据。
            index: 在数据集里的位置，用于报错定位。

        Returns:
            用例。

        Raises:
            ValueError: 缺字段或字段类型不对。
        """
        name = data.get("name")
        prompt = data.get("prompt")
        turns = data.get("turns")
        if not isinstance(name, str) or not name:
            raise ValueError(f"第 {index + 1} 条用例缺少 name")
        if not isinstance(prompt, str) or not prompt:
            raise ValueError(f"用例 {name!r} 缺少 prompt")
        if not isinstance(turns, list) or not turns:
            raise ValueError(f"用例 {name!r} 缺少 turns")
        return cls(
            name=name,
            prompt=prompt,
            turns=tuple(
                ScriptedTurn(
                    text=item.get("text"),
                    tool=item.get("tool"),
                    arguments=dict(item.get("arguments") or {}),
                )
                for item in turns
            ),
            expected_stop_reason=str(data.get("expected_stop_reason", "completed")),
            expected_tools=tuple(str(item) for item in data.get("expected_tools", ())),
            expected_tool_errors=tuple(str(item) for item in data.get("expected_tool_errors", ())),
            expected_output_contains=(
                str(data["expected_output_contains"])
                if data.get("expected_output_contains") is not None
                else None
            ),
            max_steps=int(data.get("max_steps", 8)),
        )


@dataclass(frozen=True)
class EvalResult:
    """一条用例的执行结果。"""

    case: str
    """用例名。"""

    passed: bool
    """是否全部断言通过。"""

    failures: tuple[str, ...] = ()
    """未通过的断言，逐条说明期望与实际。"""

    stop_reason: str = ""
    """实际停止原因。"""

    tools: tuple[str, ...] = ()
    """实际执行的工具名（按顺序）。"""

    tool_errors: tuple[str, ...] = ()
    """实际失败的工具名（按顺序）。"""

    output: str | None = None
    """实际最终输出。"""

    trace: RunTrace | None = None
    """这次运行的轨迹。运行未能启动时为 None。"""


@dataclass(frozen=True)
class EvalReport:
    """一次套件运行的报告。"""

    results: tuple[EvalResult, ...]
    """逐条结果。"""

    @property
    def total(self) -> int:
        """用例总数。"""
        return len(self.results)

    @property
    def passed(self) -> int:
        """通过数。"""
        return sum(1 for item in self.results if item.passed)

    @property
    def failed(self) -> int:
        """失败数。"""
        return self.total - self.passed

    @property
    def ok(self) -> bool:
        """是否全部通过。"""
        return self.failed == 0

    def summary(self) -> str:
        """一句话摘要。

        Returns:
            形如 ``9/9 通过``；有失败时附上失败用例名。
        """
        if self.ok:
            return f"{self.total}/{self.total} 通过"
        names = "、".join(item.case for item in self.results if not item.passed)
        return f"{self.passed}/{self.total} 通过；失败：{names}"


class EvalHarness:
    """在一组固定工具上运行评估用例。

    每次运行都用**新的**执行上下文与调度器，用例之间互不影响；工具实现由
    调用方提供，因此同一个套件可以评本地工具、MCP 工具或两者混合。
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        config: AgentConfig | None = None,
        name: str = "eval-agent",
    ) -> None:
        """初始化评估器。

        Args:
            registry: 工具注册表。
            config: 基础运行参数。用例里的 ``max_steps`` 会覆盖它。
            name: 运行时名字，会出现在事件作者标记里。
        """
        self.registry = registry
        self.config = config or AgentConfig()
        self.name = name

    async def run(self, case: EvalCase) -> EvalResult:
        """运行一条用例并断言。

        Args:
            case: 用例。

        Returns:
            执行结果。脚本耗尽等用例错误也会变成失败结果，而不是抛异常——
            一份报告应该跑完所有用例。
        """
        client = ScriptedLLMClient(case.to_responses())
        config = replace(self.config, max_steps=case.max_steps)
        runtime = AgentRuntime(
            client,
            ToolDispatcher(self.registry),
            config=config,
            name=self.name,
        )
        try:
            result = await runtime.run(case.prompt)
        except AssertionError as exc:
            return EvalResult(case=case.name, passed=False, failures=(str(exc),))
        return _check(case, result)

    async def run_all(self, cases: Sequence[EvalCase]) -> EvalReport:
        """运行一组用例。

        Args:
            cases: 用例。

        Returns:
            报告。用例**顺序执行**——评估要可复现，并发只会让失败报告难读。
        """
        return EvalReport(results=tuple([await self.run(case) for case in cases]))


def _check(case: EvalCase, result: AgentResult) -> EvalResult:
    """把运行结果与用例期望逐条比对。

    Args:
        case: 用例。
        result: 运行结果。

    Returns:
        执行结果。
    """
    trace = build_trace(result)
    tools = tuple(span.name for span in trace.tool_spans)
    tool_errors = tuple(span.name for span in trace.tool_spans if span.failed)
    output = result.output if isinstance(result.output, str) else None
    failures: list[str] = []

    if result.stop_reason != case.expected_stop_reason:
        failures.append(f"停止原因：期望 {case.expected_stop_reason}，实际 {result.stop_reason}")
    if tools != case.expected_tools:
        failures.append(f"工具调用：期望 {list(case.expected_tools)}，实际 {list(tools)}")
    if tool_errors != case.expected_tool_errors:
        failures.append(
            f"工具失败：期望 {list(case.expected_tool_errors)}，实际 {list(tool_errors)}"
        )
    if case.expected_output_contains is not None:
        actual = output or ""
        if case.expected_output_contains not in actual:
            failures.append(f"输出：期望包含 {case.expected_output_contains!r}，实际 {actual!r}")
    return EvalResult(
        case=case.name,
        passed=not failures,
        failures=tuple(failures),
        stop_reason=result.stop_reason,
        tools=tools,
        tool_errors=tool_errors,
        output=output,
        trace=trace,
    )


def load_cases(path: str | Path) -> list[EvalCase]:
    """从 JSON 文件加载数据集。

    文件格式::

        {"cases": [{"name": "...", "prompt": "...", "turns": [...]}, ...]}

    Args:
        path: 文件路径。

    Returns:
        用例列表。

    Raises:
        ValueError: 文件结构不对。
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = data.get("cases") if isinstance(data, Mapping) else None
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{path}：数据集必须是 {{'cases': [...]}} 且至少一条用例")
    return [EvalCase.from_dict(item, index=index) for index, item in enumerate(raw)]


async def _echo(context: Any, arguments: dict[str, Any]) -> str:
    """回显输入文本。

    Args:
        context: 工具上下文（未使用）。
        arguments: 参数，``text``。

    Returns:
        回显文本。
    """
    return f"echo: {arguments['text']}"


async def _boom(context: Any, arguments: dict[str, Any]) -> str:
    """总是失败的工具，用来验证「工具异常被归一化回填」。

    Args:
        context: 工具上下文（未使用）。
        arguments: 参数（未使用）。

    Raises:
        RuntimeError: 总是抛出。
    """
    raise RuntimeError("工具故意失败（评估用）")


def build_eval_tools() -> ToolRegistry:
    """构造评估用的最小工具集。

    只有两个工具：``echo`` 成功、``boom`` 失败。它们让数据集可以自带一条
    「工具失败后模型自愈」的用例，而不依赖网络、文件或任何外部环境。

    Returns:
        注册表。
    """
    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="echo",
                description="回显文本",
                parameters={
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            ),
            func=_echo,
        )
    )
    registry.register(
        FunctionTool(
            definition=ToolDefinition(
                name="boom",
                description="总是失败的工具",
                parameters={"type": "object", "properties": {}},
            ),
            func=_boom,
        )
    )
    return registry


__all__ = [
    "EVAL_MODEL",
    "EvalCase",
    "EvalHarness",
    "EvalReport",
    "EvalResult",
    "ScriptedLLMClient",
    "ScriptedTurn",
    "build_eval_tools",
    "load_cases",
]
