"""Tests for the LiteLLM adapter and provider resolution."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel

from agent_learning_coach.core.llm import (
    LiteLLMClient,
    LLMClient,
    LLMError,
    LLMRequest,
    LLMTimeoutError,
    Message,
    ToolCall,
    ToolSpec,
    create_client,
    resolve_provider,
)


class Plan(BaseModel):
    """测试用的结构化输出模型。"""

    title: str
    steps: list[str]


class _FakeToolCallFunction:
    name = "search"
    arguments = '{"q": "闭包"}'


class _FakeToolCall:
    id = "call-1"
    function = _FakeToolCallFunction()


class _FakeMessage:
    def __init__(self, content: str | None, tool_calls: list[Any] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message
        self.finish_reason = "stop"


class _FakeUsage:
    prompt_tokens = 11
    completion_tokens = 5


class _FakeRaw:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]
        self.usage = _FakeUsage()
        self.model = "fake-model"


@pytest.fixture
def captured_call(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """替换 litellm.acompletion，记录调用参数并返回伪造响应。"""
    captured: dict[str, Any] = {}

    async def fake_acompletion(**kwargs: Any) -> _FakeRaw:
        captured.update(kwargs)
        return _FakeRaw(_FakeMessage("hello"))

    import litellm

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    return captured


def test_resolve_provider_for_openai() -> None:
    assert resolve_provider("gpt-4o-mini") == "openai"


def test_resolve_provider_for_anthropic() -> None:
    assert resolve_provider("anthropic/claude-sonnet-4-5") == "anthropic"


def test_resolve_provider_rejects_unknown_model() -> None:
    with pytest.raises(LLMError):
        resolve_provider("definitely-not-a-real-model-xyz")


def test_create_client_returns_protocol_conforming_client() -> None:
    client = create_client("gpt-4o-mini", timeout=15.0)

    assert isinstance(client, LLMClient)
    assert isinstance(client, LiteLLMClient)


def test_create_client_rejects_unknown_model() -> None:
    with pytest.raises(LLMError):
        create_client("definitely-not-a-real-model-xyz")


def test_build_payload_includes_core_fields() -> None:
    client = LiteLLMClient(model="gpt-4o-mini")

    payload = client.build_payload(
        LLMRequest(
            instructions=["sys"],
            contents=[Message(role="user", content="hi")],
            tools=[ToolSpec(name="search", description="d", parameters={"type": "object"})],
            tool_choice="auto",
            temperature=0.2,
            max_output_tokens=256,
        )
    )

    assert payload["model"] == "gpt-4o-mini"
    assert payload["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    assert payload["tools"][0]["function"]["name"] == "search"
    assert payload["tool_choice"] == "auto"
    assert payload["temperature"] == 0.2
    assert payload["max_tokens"] == 256
    assert "timeout" not in payload


def test_build_payload_uses_client_default_timeout() -> None:
    client = LiteLLMClient(model="gpt-4o-mini", timeout=30.0)

    payload = client.build_payload(LLMRequest())

    assert payload["timeout"] == 30.0


def test_build_payload_request_timeout_overrides_client_default() -> None:
    client = LiteLLMClient(model="gpt-4o-mini", timeout=30.0)

    payload = client.build_payload(LLMRequest(timeout=5.0))

    assert payload["timeout"] == 5.0


def test_build_payload_extra_overrides_defaults() -> None:
    client = LiteLLMClient(model="gpt-4o-mini", timeout=30.0)

    payload = client.build_payload(LLMRequest(extra={"timeout": 1.0, "seed": 7}))

    assert payload["timeout"] == 1.0
    assert payload["seed"] == 7


def test_generate_returns_response(captured_call: dict[str, Any]) -> None:
    client = LiteLLMClient(model="gpt-4o-mini")

    response = asyncio.run(client.generate(LLMRequest(instructions=["hi"])))

    assert response.model == "fake-model"
    assert response.usage.input_tokens == 11
    assert response.usage.output_tokens == 5
    assert captured_call["model"] == "gpt-4o-mini"


def test_generate_parses_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_acompletion(**kwargs: Any) -> _FakeRaw:
        return _FakeRaw(_FakeMessage(None, [_FakeToolCall()]))

    import litellm

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    client = LiteLLMClient(model="gpt-4o-mini")

    response = asyncio.run(client.generate(LLMRequest()))

    tool_calls = [item for item in response.contents if isinstance(item, ToolCall)]
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "search"
    assert tool_calls[0].arguments == {"q": "闭包"}


def test_generate_maps_litellm_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from litellm.exceptions import Timeout as LiteLLMTimeout

    async def fake_acompletion(**kwargs: Any) -> Any:
        raise LiteLLMTimeout(
            "request timed out",
            model="gpt-4o-mini",
            llm_provider="openai",
        )

    import litellm

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    client = LiteLLMClient(model="gpt-4o-mini")

    with pytest.raises(LLMTimeoutError):
        asyncio.run(client.generate(LLMRequest()))


def test_generate_maps_generic_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_acompletion(**kwargs: Any) -> Any:
        raise RuntimeError("boom")

    import litellm

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    client = LiteLLMClient(model="gpt-4o-mini")

    with pytest.raises(LLMError):
        asyncio.run(client.generate(LLMRequest()))


def test_generate_maps_builtin_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_acompletion(**kwargs: Any) -> Any:
        raise TimeoutError("asyncio timeout")

    import litellm

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    client = LiteLLMClient(model="gpt-4o-mini")

    with pytest.raises(LLMTimeoutError):
        asyncio.run(client.generate(LLMRequest()))


def test_generate_structured_adds_schema_instruction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_acompletion(**kwargs: Any) -> _FakeRaw:
        captured.update(kwargs)
        return _FakeRaw(_FakeMessage('{"title": "T", "steps": ["s1"]}'))

    import litellm

    monkeypatch.setattr(litellm, "acompletion", fake_acompletion)
    client = LiteLLMClient(model="gpt-4o-mini")

    result = asyncio.run(client.generate_structured(LLMRequest(instructions=["做计划"]), Plan))

    assert result.title == "T"
    assert result.steps == ["s1"]
    instruction = captured["messages"][0]["content"]
    assert "做计划" in instruction
    assert "steps" in instruction
