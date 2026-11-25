"""诊断题测试集的执行（Topic 21）。

数据在 ``tests/data/exercise_cases.json``：每条用例是一次**固定的模型输出**，
加上期望结果（通过 / 拒绝 + 拒绝理由里应出现的关键词）。

这个数据集测的是**拦截规则**，不是模型质量：把固定的输出喂进去，验证每一层
校验确实拦得住。用例写成数据而不是写死在代码里，是为了**改一条规则时能立刻
看出影响了哪些用例**——加一条校验、删一条校验，都在这个文件里看得见。

领域模型的规则另有专门的用例（``test_learning_generation.py``）；本文件守的是
「数据集与规则的一致性」。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from agent_learning_coach.core.learning import (
    ExerciseGenerationError,
    ExerciseGenerator,
    ExerciseRequest,
    KnowledgePointId,
)
from agent_learning_coach.core.llm.mock import MockLLMClient
from agent_learning_coach.core.llm.models import LLMResponse
from agent_learning_coach.core.llm.types import Message

CASES_PATH = Path(__file__).parent / "data" / "exercise_cases.json"


def _load_cases() -> list[dict[str, Any]]:
    """读取测试集。

    Returns:
        用例列表。
    """
    payload = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    return list(payload["cases"])


def _client_for(output: object) -> MockLLMClient:
    """构造一个返回指定输出的 Mock 客户端。

    Args:
        output: 模型输出；字符串按原文返回，其余按 JSON 返回。

    Returns:
        Mock 客户端。
    """

    def handler(request):  # type: ignore[no-untyped-def]
        text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
        return LLMResponse(
            model="mock-model",
            contents=[Message(role="assistant", content=text)],
        )

    return MockLLMClient(handler=handler)


def _request() -> ExerciseRequest:
    """构造出题请求。"""
    return ExerciseRequest(
        point_id=KnowledgePointId("k2"),
        point_title="闭包",
        point_summary="函数记住了创建时的作用域",
        difficulty="intermediate",
        target_outcome="能写出一个闭包例子",
    )


@pytest.mark.parametrize("case", _load_cases(), ids=lambda case: case["name"])
def test_exercise_case(case: dict[str, Any]) -> None:
    """测试集里的每条用例都按期望结果执行。

    Args:
        case: 一条用例。
    """
    client = _client_for(case["output"])
    generator = ExerciseGenerator(client, max_attempts=1)

    if case["expect"] == "accepted":
        exercise = asyncio.run(generator.generate(_request()))
        assert exercise.point_id == KnowledgePointId("k2")
        assert exercise.difficulty == "intermediate"
        return

    with pytest.raises(ExerciseGenerationError) as info:
        asyncio.run(generator.generate(_request()))
    assert case["reason_contains"] in str(info.value)


def test_dataset_covers_both_layers() -> None:
    """测试集必须同时覆盖结构层与领域层——只测一层等于没测另一层。"""
    layers = {case["layer"] for case in _load_cases()}

    assert "structure" in layers
    assert "domain" in layers
    assert "pass" in layers
