"""评估测试：数据集、行为断言与失败报告。

对应 Topic 14 的「阶段验收」：**评估用例 PASS 才算验收通过**，而用例本身
要在 CI 里跑。因此这里既跑数据集，也验证「评估器会抓到错误」——一个永远
通过的评估器什么也证明不了。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from agent_learning_coach.core.agent import (
    AgentConfig,
    EvalCase,
    EvalHarness,
    ScriptedTurn,
    build_eval_tools,
    load_cases,
)

DATA = Path(__file__).resolve().parents[1] / "evals" / "phase0.json"


def make_harness() -> EvalHarness:
    """构造跑评估集的 harness。"""
    return EvalHarness(build_eval_tools(), config=AgentConfig(model="eval-model"))


def test_phase0_suite_passes() -> None:
    """Phase 0 数据集全部通过——这是阶段验收的一部分。"""
    cases = load_cases(DATA)
    report = asyncio.run(make_harness().run_all(cases))

    assert report.total >= 7
    assert report.ok, [item for item in report.results if not item.passed]
    assert report.summary() == f"{report.total}/{report.total} 通过"


def test_suite_covers_failure_paths() -> None:
    """数据集必须覆盖失败路径，否则「全绿」只说明用例挑得轻。"""
    cases = load_cases(DATA)
    stop_reasons = {case.expected_stop_reason for case in cases}
    failing_tools = {name for case in cases for name in case.expected_tool_errors}

    assert {"completed", "max_steps", "repeated_call", "consecutive_errors"} <= stop_reasons
    assert {"boom", "missing_tool"} <= failing_tools


def test_wrong_expectation_is_reported_with_actual() -> None:
    """断言不符时报告要说清期望与实际。"""
    case = EvalCase(
        name="期望错工具",
        prompt="用 echo 回显",
        turns=(
            ScriptedTurn(tool="echo", arguments={"text": "hi"}),
            ScriptedTurn(text="完成"),
        ),
        expected_tools=("另一个工具",),
    )

    result = asyncio.run(make_harness().run(case))

    assert not result.passed
    assert any("工具调用" in item and "echo" in item for item in result.failures)


def test_exhausted_script_becomes_a_failure_not_an_exception() -> None:
    """脚本写短了要被报告出来，而不是让整个套件崩掉。"""
    case = EvalCase(
        name="脚本太短",
        prompt="调用两次 echo",
        turns=(ScriptedTurn(tool="echo", arguments={"text": "一次"}),),
    )

    result = asyncio.run(make_harness().run(case))

    assert not result.passed
    assert any("脚本已耗尽" in item for item in result.failures)
    assert result.trace is None


def test_scripted_turn_requires_exactly_one_action() -> None:
    """一步脚本只能二选一，含糊的用例在构造时就被拒绝。"""
    with pytest.raises(ValueError, match="二选一"):
        ScriptedTurn(text="说话", tool="echo")
    with pytest.raises(ValueError, match="二选一"):
        ScriptedTurn()


def test_load_cases_rejects_bad_shape(tmp_path: Path) -> None:
    """数据集结构不对时明确报错，而不是静默跑出 0 条。"""
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"items": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="cases"):
        load_cases(path)


def test_load_cases_rejects_case_without_name(tmp_path: Path) -> None:
    """缺 name 的用例无法在报告里定位，因此拒绝。"""
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps({"cases": [{"prompt": "x", "turns": [{"text": "y"}]}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="name"):
        load_cases(path)


def test_eval_case_rejects_empty_turns() -> None:
    """没有脚本就没有行为可验。"""
    with pytest.raises(ValueError, match="turns"):
        EvalCase.from_dict({"name": "空", "prompt": "x", "turns": []})
