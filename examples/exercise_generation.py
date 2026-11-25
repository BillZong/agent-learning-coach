"""诊断题生成：结构层与领域层两道拦截，以及带反馈的重试。

跑法::

    uv run python examples/exercise_generation.py

脚本演示五件事：

1. **生成**——模型出一道题，生成器转成领域题目。
2. **结构层拦截**——字段缺失、题面太短，在结构层被拒。
3. **领域层拦截**——难度不符、题目跑题，在领域层被拒。
4. **带反馈重试**——失败原因回喂给模型，模型能看见自己错在哪。
5. **边界**——不合格题目**不会**流到调用方；模型调用失败不重试。

脚本用 Mock 客户端，**不联网**。
"""

from __future__ import annotations

import asyncio
import json

from agent_learning_coach.core.learning import (
    ExerciseGenerationError,
    ExerciseGenerator,
    ExerciseRequest,
    KnowledgePointId,
)
from agent_learning_coach.core.llm.errors import LLMTimeoutError
from agent_learning_coach.core.llm.mock import FailingLLMClient, MockLLMClient
from agent_learning_coach.core.llm.models import LLMResponse
from agent_learning_coach.core.llm.types import Message


def _request() -> ExerciseRequest:
    """构造出题请求。"""
    return ExerciseRequest(
        point_id=KnowledgePointId("k2"),
        point_title="闭包",
        point_summary="函数记住了创建时的作用域",
        difficulty="intermediate",
        target_outcome="能写出一个闭包例子",
    )


def _client(*outputs: object) -> MockLLMClient:
    """构造一个按顺序返回指定输出的 Mock 客户端。"""
    remaining = list(outputs)

    def handler(request):  # type: ignore[no-untyped-def]
        payload = remaining.pop(0)
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        return LLMResponse(
            model="mock-model",
            contents=[Message(role="assistant", content=text)],
        )

    return MockLLMClient(handler=handler)


def _good_output() -> dict[str, str]:
    """一份合格输出。"""
    return {
        "prompt": "闭包与普通函数在返回后访问变量时有什么不同？请举一个例子。",
        "expected_answer": "闭包记住了创建时的作用域，返回后仍可访问；普通函数做不到。",
        "difficulty": "intermediate",
        "rationale": "要求对比并举例，暴露是否理解闭包的捕获语义。",
    }


def show_generation() -> None:
    """演示正常生成。"""
    print("=== 1. 生成 ===\n")
    client = _client(_good_output())

    exercise = asyncio.run(ExerciseGenerator(client).generate(_request()))

    print(f"题目标识：{exercise.id}")
    print(f"题面：{exercise.prompt}")
    print(f"参考答案：{exercise.expected_answer}")
    print(f"难度：{exercise.difficulty}")
    print(f"出题理由：{exercise.rationale}")
    print()


def show_structure_rejection() -> None:
    """演示结构层拦截。"""
    print("=== 2. 结构层拦截：模型没按格式回答 ===\n")
    broken = _good_output()
    del broken["expected_answer"]
    client = _client(broken)

    try:
        asyncio.run(ExerciseGenerator(client, max_attempts=1).generate(_request()))
    except ExerciseGenerationError as exc:
        print(f"[缺少参考答案] -> {exc}")
    print()


def show_domain_rejection() -> None:
    """演示领域层拦截。"""
    print("=== 3. 领域层拦截：按格式回答了，但业务上不成立 ===\n")
    off_topic = {
        "prompt": "请介绍一下 Python 装饰器是怎样实现的？",
        "expected_answer": "装饰器是一个接受函数并返回新函数的包装器。",
        "difficulty": "intermediate",
        "rationale": "用来考查装饰器的实现原理。",
    }
    client = _client(off_topic)

    try:
        asyncio.run(ExerciseGenerator(client, max_attempts=1).generate(_request()))
    except ExerciseGenerationError as exc:
        print(f"[题目跑题] -> {exc}")

    wrong_difficulty = _good_output()
    wrong_difficulty["difficulty"] = "advanced"
    client = _client(wrong_difficulty)

    try:
        asyncio.run(ExerciseGenerator(client, max_attempts=1).generate(_request()))
    except ExerciseGenerationError as exc:
        print(f"[难度不符] -> {exc}")
    print()


def show_retry_with_feedback() -> None:
    """演示带反馈的重试。"""
    print("=== 4. 带反馈重试 ===\n")
    wrong = _good_output()
    wrong["difficulty"] = "basic"
    client = _client(wrong, _good_output())

    exercise = asyncio.run(ExerciseGenerator(client).generate(_request()))

    print(f"尝试次数：{len(client.requests)}")
    print("第一次失败原因回喂给模型：")
    for instruction in client.requests[1].instructions:
        if "不合格" in instruction:
            print(f"  {instruction.splitlines()[0]}")
    print(f"重试后得到合格题目：{exercise.prompt[:30]}…")
    print()


def show_boundaries() -> None:
    """演示边界：坏题不外流，调用失败不重试。"""
    print("=== 5. 边界 ===\n")
    too_short = {
        "prompt": "闭包？",
        "expected_answer": "x",
        "difficulty": "basic",
        "rationale": "y",
    }
    client = _client(too_short, too_short)

    try:
        asyncio.run(ExerciseGenerator(client, max_attempts=2).generate(_request()))
    except ExerciseGenerationError as exc:
        print(f"[重试耗尽] -> {exc}")
    print("（不合格题目不会返回给调用方——它在生成层就被挡住了）")

    failing = FailingLLMClient("模型超时", error=LLMTimeoutError)
    try:
        asyncio.run(ExerciseGenerator(failing, max_attempts=3).generate(_request()))
    except ExerciseGenerationError as exc:
        print(f"[调用失败] -> {exc}")
    print(f"调用次数：{len(failing.requests)}（调用失败不是「题目不合格」，重试没有意义）")


def main() -> None:
    """跑一遍诊断题生成演示。"""
    show_generation()
    show_structure_rejection()
    show_domain_rejection()
    show_retry_with_feedback()
    show_boundaries()

    print("\n=== 边界提醒 ===")
    print("生成器只管「产出一道合格的题」；")
    print("诊断够不够驱动计划生成由 diagnosis.py 判断（ADR-0020）。")


if __name__ == "__main__":
    main()
