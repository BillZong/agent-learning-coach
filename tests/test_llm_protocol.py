"""Tests for the provider-independent LLM abstraction."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from agent_learning_coach.core.llm import (
    FailingLLMClient,
    LLMClient,
    LLMError,
    LLMParseError,
    LLMRequest,
    LLMResponse,
    Message,
    MockLLMClient,
    ToolCall,
    ToolSpec,
)
from agent_learning_coach.core.llm.messages import build_messages, build_tools, parse_response
from agent_learning_coach.core.llm.structured import (
    build_structured_instruction,
    extract_text,
    parse_structured_output,
)


class Diagnosis(BaseModel):
    """测试用的结构化输出模型。"""

    weak_points: list[str]
    summary: str


class _FakeMessage:
    content = "hello"
    tool_calls = None


class _FakeChoice:
    message = _FakeMessage()
    finish_reason = "stop"


class _FakeUsage:
    prompt_tokens = 7
    completion_tokens = 3


class _FakeRaw:
    """最小化的 LiteLLM 原始响应替身。"""

    choices = [_FakeChoice()]
    usage = _FakeUsage()
    model = "fake-model"


def test_mock_client_satisfies_protocol() -> None:
    assert isinstance(MockLLMClient(), LLMClient)
    assert isinstance(FailingLLMClient(), LLMClient)


def test_litellm_client_satisfies_protocol() -> None:
    from agent_learning_coach.core.llm.litellm_client import LiteLLMClient

    assert isinstance(LiteLLMClient(model="gpt-4o-mini"), LLMClient)


def test_non_client_does_not_satisfy_protocol() -> None:
    assert not isinstance(object(), LLMClient)


def test_mock_client_returns_configured_response() -> None:
    client = MockLLMClient(text="hi there")

    response = asyncio.run(client.generate(LLMRequest(instructions=["greet"])))

    assert isinstance(response, LLMResponse)
    assert extract_text(response.contents) == "hi there"
    assert len(client.requests) == 1
    assert client.requests[0].instructions == ["greet"]


def test_generate_structured_parses_json() -> None:
    client = MockLLMClient(text='{"weak_points": ["闭包"], "summary": "函数式基础薄弱"}')

    result = asyncio.run(client.generate_structured(LLMRequest(), Diagnosis))

    assert result.weak_points == ["闭包"]
    assert result.summary == "函数式基础薄弱"


def test_generate_structured_rejects_invalid_json() -> None:
    client = MockLLMClient(text="not json at all")

    with pytest.raises(LLMParseError):
        asyncio.run(client.generate_structured(LLMRequest(), Diagnosis))


def test_generate_structured_rejects_schema_mismatch() -> None:
    client = MockLLMClient(text='{"summary": "缺少必填字段"}')

    with pytest.raises(LLMParseError):
        asyncio.run(client.generate_structured(LLMRequest(), Diagnosis))


def test_failing_client_raises_llm_error() -> None:
    client = FailingLLMClient("boom")

    with pytest.raises(LLMError):
        asyncio.run(client.generate(LLMRequest()))


def test_parse_structured_output_handles_code_fence() -> None:
    text = '```json\n{"weak_points": [], "summary": "ok"}\n```'

    result = parse_structured_output(text, Diagnosis)

    assert result.summary == "ok"


def test_build_structured_instruction_contains_schema() -> None:
    instruction = build_structured_instruction("诊断学习者", Diagnosis)

    assert "诊断学习者" in instruction
    assert "weak_points" in instruction


def test_build_messages_maps_content_items() -> None:
    request = LLMRequest(
        instructions=["system prompt"],
        contents=[
            Message(role="user", content="hi"),
            ToolCall(tool_call_id="call-1", name="search", arguments={"q": "x"}),
            ToolCall(tool_call_id="call-2", name="plan", arguments={}),
        ],
    )

    messages = build_messages(request)

    assert messages[0] == {"role": "system", "content": "system prompt"}
    assert messages[1] == {"role": "user", "content": "hi"}
    assert len(messages[2]["tool_calls"]) == 2


def test_build_tools_uses_openai_shape() -> None:
    tools = build_tools([ToolSpec(name="search", description="d", parameters={"type": "object"})])

    assert tools == [
        {
            "type": "function",
            "function": {"name": "search", "description": "d", "parameters": {"type": "object"}},
        }
    ]


def test_parse_response_extracts_usage() -> None:
    response = parse_response(_FakeRaw())

    assert response.model == "fake-model"
    assert response.usage.input_tokens == 7
    assert response.usage.output_tokens == 3
    assert extract_text(response.contents) == "hello"
