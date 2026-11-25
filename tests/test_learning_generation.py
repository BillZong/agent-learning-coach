"""诊断题生成的测试（Topic 21）。

覆盖五组关注点：

**结构层**——字段缺失、类型错、长度不足都在结构层被拒。

**领域层**——难度不符、题目跑题在领域层被拒。

**拦截与重试**——不合格题目**不会**返回给调用方；失败原因会回喂给模型重试。

**边界**——生成器不认识诊断流程，也不认识学习者的其他状态。

**离线**——全部用例用 Mock 客户端，不联网。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from agent_learning_coach.core.learning import (
    DiagnosticExercise,
    DomainError,
    ExerciseGenerationError,
    ExerciseGenerator,
    ExerciseRequest,
    ExerciseSchema,
    KnowledgePointId,
)
from agent_learning_coach.core.llm.errors import LLMTimeoutError
from agent_learning_coach.core.llm.mock import FailingLLMClient, MockLLMClient
from agent_learning_coach.core.llm.models import LLMResponse
from agent_learning_coach.core.llm.types import Message


def _request(difficulty: str = "intermediate") -> ExerciseRequest:
    """构造一个出题请求。"""
    return ExerciseRequest(
        point_id=KnowledgePointId("k2"),
        point_title="闭包",
        point_summary="函数记住了创建时的作用域",
        difficulty=difficulty,  # type: ignore[arg-type]
        target_outcome="能写出一个闭包例子",
    )


def _payload(
    prompt: str = "闭包与普通函数在返回后访问变量时有什么不同？请举一个例子。",
    expected_answer: str = "闭包记住了创建时的作用域，返回后仍可访问。",
    difficulty: str = "intermediate",
    rationale: str = "要求对比并举例，暴露是否理解闭包的捕获语义。",
) -> dict[str, str]:
    """构造一份合法的模型输出。"""
    return {
        "prompt": prompt,
        "expected_answer": expected_answer,
        "difficulty": difficulty,
        "rationale": rationale,
    }


def _client_returning(*payloads: object) -> MockLLMClient:
    """构造一个按顺序返回指定内容的 Mock 客户端。

    字符串按原文返回（用来模拟非法 JSON），字典按 JSON 返回。
    """
    responses = list(payloads)

    def handler(request):  # type: ignore[no-untyped-def]
        payload = responses.pop(0)
        if isinstance(payload, str):
            text = payload
        else:
            text = json.dumps(payload, ensure_ascii=False)
        return LLMResponse(
            model="mock-model",
            contents=[Message(role="assistant", content=text)],
        )

    return MockLLMClient(handler=handler)


# --- 结构层 ---------------------------------------------------------------


def test_generator_returns_valid_exercise() -> None:
    """合格输出转成领域题目。"""
    client = _client_returning(_payload())

    exercise = asyncio.run(ExerciseGenerator(client).generate(_request()))

    assert isinstance(exercise, DiagnosticExercise)
    assert exercise.point_id == KnowledgePointId("k2")
    assert exercise.difficulty == "intermediate"
    assert exercise.expected_answer.startswith("闭包记住了")


def test_invalid_json_is_rejected_and_retried() -> None:
    """非法 JSON 在结构层被拒，并且触发重试。"""
    client = _client_returning("这不是 JSON", _payload())

    exercise = asyncio.run(ExerciseGenerator(client).generate(_request()))

    assert exercise.prompt.startswith("闭包与普通函数")
    assert len(client.requests) == 2


def test_schema_violation_is_rejected() -> None:
    """字段缺失在结构层被拒。"""
    broken = _payload()
    del broken["expected_answer"]
    client = _client_returning(broken, _payload())

    exercise = asyncio.run(ExerciseGenerator(client).generate(_request()))

    assert exercise.expected_answer != ""
    assert len(client.requests) == 2


def test_too_short_prompt_is_rejected() -> None:
    """题面太短在结构层被拒——「闭包是什么」测不出程度。"""
    client = _client_returning(_payload(prompt="闭包？"), _payload())

    exercise = asyncio.run(ExerciseGenerator(client).generate(_request()))

    assert exercise.prompt != "闭包？"
    assert len(client.requests) == 2


def test_invalid_difficulty_value_is_rejected() -> None:
    """难度取值非法在结构层被拒。"""
    client = _client_returning(_payload(difficulty="trivial"), _payload())

    exercise = asyncio.run(ExerciseGenerator(client).generate(_request()))

    assert exercise.difficulty == "intermediate"
    assert len(client.requests) == 2


# --- 领域层 ---------------------------------------------------------------


def test_difficulty_mismatch_is_rejected() -> None:
    """难度与要求不符在领域层被拒。"""
    client = _client_returning(_payload(difficulty="advanced"), _payload())

    exercise = asyncio.run(ExerciseGenerator(client).generate(_request()))

    assert exercise.difficulty == "intermediate"
    assert len(client.requests) == 2


def test_off_topic_exercise_is_rejected() -> None:
    """题目跑题在领域层被拒——它会污染判定。"""
    off_topic = _payload(
        prompt="请介绍一下 Python 的装饰器是怎么实现的？",
        rationale="用来考查装饰器的实现原理。",
    )
    client = _client_returning(off_topic, _payload())

    exercise = asyncio.run(ExerciseGenerator(client).generate(_request()))

    assert exercise.prompt.startswith("闭包与普通函数")
    assert len(client.requests) == 2


def test_invalid_exercise_never_reaches_caller() -> None:
    """重试耗尽时抛错，**不是**把坏题返回给调用方。"""
    client = _client_returning(_payload(difficulty="advanced"))

    with pytest.raises(ExerciseGenerationError) as info:
        asyncio.run(ExerciseGenerator(client, max_attempts=1).generate(_request()))

    assert info.value.attempts == 1
    assert "难度" in str(info.value)


def test_feedback_is_sent_back_to_model() -> None:
    """失败原因回喂给模型——模型要能看见自己错在哪。"""
    client = _client_returning(_payload(difficulty="advanced"), _payload())

    asyncio.run(ExerciseGenerator(client).generate(_request()))

    first, second = client.requests
    assert "上一次生成的题目不合格" not in " ".join(first.instructions)
    assert any("上一次生成的题目不合格" in item for item in second.instructions)


def test_model_feedback_uses_summary_not_raw_detail() -> None:
    """喂给模型的是**摘要**：字段 + 原因，不是原始条目。"""
    broken = _payload()
    del broken["expected_answer"]
    client = _client_returning(broken, _payload())

    asyncio.run(ExerciseGenerator(client).generate(_request()))

    feedback = " ".join(client.requests[1].instructions)
    assert "expected_answer：Field required" in feedback
    # 原始条目里的噪声不进反馈：模型不需要 error code 与输入值
    assert "errors.pydantic.dev" not in feedback
    assert "'input'" not in feedback


def test_original_field_errors_are_preserved_as_fact() -> None:
    """原始错误条目被保留——摘要是视图，事实不能丢。"""
    broken = _payload()
    del broken["expected_answer"]
    client = _client_returning(broken)

    with pytest.raises(ExerciseGenerationError) as info:
        asyncio.run(ExerciseGenerator(client, max_attempts=1).generate(_request()))

    error = info.value
    assert "expected_answer" in error.detail
    assert "Field required" in error.detail
    # 摘要与细节并存：摘要给模型，细节给排查
    assert "expected_answer：Field required" in error.message
    assert "expected_answer" in error.detail


def test_generation_error_keeps_detail_for_diagnosis() -> None:
    """重试耗尽时，异常带原始细节——排查问题时能拿到厂商上下文。"""
    client = _client_returning(_payload(difficulty="advanced"))

    with pytest.raises(ExerciseGenerationError) as info:
        asyncio.run(ExerciseGenerator(client, max_attempts=1).generate(_request()))

    assert "难度" in info.value.message
    assert "难度" in info.value.detail


def test_model_call_failure_is_not_retried() -> None:
    """调用失败不是「题目不合格」，重试也不会变好——直接上抛。"""
    client = FailingLLMClient("模型超时", error=LLMTimeoutError)

    with pytest.raises(ExerciseGenerationError, match="调用模型失败"):
        asyncio.run(ExerciseGenerator(client, max_attempts=3).generate(_request()))

    assert len(client.requests) == 1


# --- 边界 -----------------------------------------------------------------


def test_generator_does_not_know_diagnosis_flow() -> None:
    """生成器只管出题，不判断诊断够不够驱动计划生成。"""
    request = ExerciseRequest(
        point_id=KnowledgePointId("k2"),
        point_title="闭包",
        point_summary="",
    )

    assert not hasattr(ExerciseGenerator, "has_full_coverage")
    assert not hasattr(request, "learner_id")


def test_generator_requires_at_least_one_attempt() -> None:
    """重试次数至少为 1。"""
    with pytest.raises(ValueError, match="max_attempts 至少为 1"):
        ExerciseGenerator(_client_returning(_payload()), max_attempts=0)


def test_exercise_schema_requires_fields() -> None:
    """结构层缺字段时报错，错误信息指明缺什么。"""
    with pytest.raises(Exception, match="expected_answer"):
        ExerciseSchema.model_validate(
            {"prompt": "闭包与普通函数有什么不同？", "difficulty": "basic", "rationale": "x"},
        )


def test_exercise_rejects_invalid_difficulty() -> None:
    """领域模型拒绝非法难度——任何来源的题目都受同一套约束。"""
    with pytest.raises(DomainError, match="诊断题难度必须是"):
        DiagnosticExercise(
            id="ex-k2",
            point_id=KnowledgePointId("k2"),
            prompt="闭包与普通函数有什么不同？",
            difficulty="trivial",
        )


def test_exercise_accepts_open_question_without_answer() -> None:
    """开放题可以没有标准答案——此时判定需人工或模型介入。"""
    exercise = DiagnosticExercise(
        id="ex-k2",
        point_id=KnowledgePointId("k2"),
        prompt="闭包与普通函数有什么不同？",
    )

    assert exercise.expected_answer == ""
    assert exercise.difficulty == "intermediate"
