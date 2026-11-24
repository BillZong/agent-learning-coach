"""可靠性与阶段验收：Trace → Metrics → 诊断 → 评估套件。

这是 Topic 14 的**可运行交付物**——把「怎么知道 Agent 坏了」变成一个可以反复
执行的过程，而不是一次人工检查::

    uv run python examples/reliability_report.py

三件事：

1. **Trace**：一次运行发生了什么、时间花在哪里。Trace 是运行结果上的投影，
   不另记一份事件；模型耗时来自 ``AgentStep.think_ms``，工具耗时来自
   ``ToolOutcome.duration_ms``。
2. **Metrics + 诊断**：多次运行聚合成成功率与停止原因分布；失败的运行给出
   基于证据的线索（哪个工具失败、哪个动作被重复）。
3. **评估套件**：读 ``evals/phase0.json``，用脚本化模型确定性地验证运行行为
   ——工具调用、失败归一化、停止条件、输出交付。套件不通过时以非零码退出，
   因此可以直接当 CI 门禁。

模型侧用脚本化客户端：结果确定、不消耗额度、不需要凭证。想接真实模型时，
把脚本换成真实 ``LLMClient`` 即可——Trace 与 Metrics 的代码一行都不用改。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from agent_learning_coach.core.agent import (  # noqa: E402
    AgentConfig,
    AgentRuntime,
    EvalHarness,
    RunTrace,
    ScriptedLLMClient,
    TokenPrice,
    build_eval_tools,
    build_trace,
    compute_metrics,
    diagnose,
    load_cases,
)
from agent_learning_coach.core.llm.models import (  # noqa: E402
    LLMResponse,
    LLMUsage,
)
from agent_learning_coach.core.llm.types import Message, ToolCall  # noqa: E402
from agent_learning_coach.core.tools import ToolDispatcher  # noqa: E402

DEMO_MODEL = "demo-model"
PRICES = {DEMO_MODEL: TokenPrice(input_per_1k=0.01, output_per_1k=0.02)}


def text(text: str) -> LLMResponse:
    """构造一个文本响应。"""
    return LLMResponse(
        model=DEMO_MODEL,
        contents=[Message(role="assistant", content=text)],
        usage=LLMUsage(input_tokens=12, output_tokens=6),
    )


def call(tool: str, arguments: dict[str, object], call_id: str) -> LLMResponse:
    """构造一个工具调用响应。"""
    return LLMResponse(
        model=DEMO_MODEL,
        contents=[ToolCall(tool_call_id=call_id, name=tool, arguments=arguments)],
        usage=LLMUsage(input_tokens=14, output_tokens=4),
    )


def print_trace(trace: RunTrace) -> None:
    """打印一条 Trace。"""
    print(
        f"  状态 {trace.status} / 停止 {trace.stop_reason} / "
        f"耗时 {trace.duration_ms:.0f}ms / "
        f"工具 {trace.tool_calls} 次（失败 {trace.failed_tool_calls}）/ "
        f"token {trace.usage.input_tokens + trace.usage.output_tokens} / "
        f"成本 {trace.cost:.5f}"
    )
    for step in trace.steps:
        names = "、".join(
            f"{span.name}({span.duration_ms:.1f}ms{', 失败' if span.failed else ''})"
            for span in step.tools
        )
        print(
            f"    第 {step.index} 步：think {step.think_ms:.1f}ms，"
            f"token {step.input_tokens}/{step.output_tokens}，"
            f"工具 {names or '-'}"
        )
    hint = diagnose(trace)
    if hint is not None:
        print("    诊断：")
        for line in hint.splitlines():
            print(f"      {line}")


async def collect_traces() -> list[RunTrace]:
    """跑三次行为不同的运行，各取一条 Trace。"""
    registry = build_eval_tools()
    scenarios: list[tuple[str, AgentConfig, ScriptedLLMClient]] = [
        (
            "正常完成",
            AgentConfig(model=DEMO_MODEL, max_steps=4, prices=PRICES),
            ScriptedLLMClient([text("收到：闭包是函数加上它捕获的环境。")]),
        ),
        (
            "工具失败后自愈",
            AgentConfig(model=DEMO_MODEL, max_steps=4, prices=PRICES),
            ScriptedLLMClient(
                [
                    call("boom", {}, "call-1"),
                    text("工具坏了，改用讲解的方式回答。"),
                ]
            ),
        ),
        (
            "原地打转",
            AgentConfig(model=DEMO_MODEL, max_steps=6, prices=PRICES),
            ScriptedLLMClient(
                [call("echo", {"text": "同样的参数"}, "call-1")],
                repeat_last=True,
            ),
        ),
    ]

    traces: list[RunTrace] = []
    for title, config, client in scenarios:
        runtime = AgentRuntime(client, ToolDispatcher(registry), config=config)
        result = await runtime.run(title)
        trace = build_trace(result)
        print(f"\n[{title}]")
        print_trace(trace)
        traces.append(trace)
    return traces


async def run_eval_suite() -> bool:
    """运行评估套件，返回是否全部通过。"""
    cases = load_cases(ROOT / "evals" / "phase0.json")
    harness = EvalHarness(build_eval_tools(), config=AgentConfig(model=DEMO_MODEL))
    report = await harness.run_all(cases)
    for item in report.results:
        mark = "✓" if item.passed else "✗"
        print(f"  {mark} {item.case}（停止 {item.stop_reason}，工具 {list(item.tools)}）")
        for failure in item.failures:
            print(f"      {failure}")
    print(f"\n评估套件：{report.summary()}")
    return report.ok


async def main() -> int:
    """打印可靠性报告。"""
    print("== 1. Trace（一次运行发生了什么，时间花在哪里） ==")
    traces = await collect_traces()

    print("\n== 2. Metrics（多次运行聚合） ==")
    metrics = compute_metrics(traces)
    print(f"  {metrics.summary()}")
    print(
        f"  成功率 {metrics.success_rate:.0%}，工具失败率 {metrics.tool_failure_rate:.0%}，"
        f"运行耗时 p50 {metrics.duration_ms_p50:.0f}ms / p95 {metrics.duration_ms_p95:.0f}ms"
    )

    print("\n== 3. 评估套件（确定性行为验收） ==")
    ok = await run_eval_suite()
    print("\n说明：脚本化客户端不耗时，因此上面的 think 耗时接近 0；")
    print("      接真实模型后，这个数字才反映真实延迟。")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
