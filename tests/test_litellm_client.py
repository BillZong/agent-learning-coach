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
def captured_call() -> tuple[dict[str, Any], Any]:
    """返回 (调用参数记录, 注入用的 completion 替身)。"""
    captured: dict[str, Any] = {}

    async def fake_acompletion(**kwargs: Any) -> _FakeRaw:
        captured.update(kwargs)
        return _FakeRaw(_FakeMessage("hello"))

    return captured, fake_acompletion


def test_resolve_provider_for_openai() -> None:
    assert resolve_provider("openai").namespace == "openai"


def test_resolve_provider_for_anthropic() -> None:
    assert resolve_provider("anthropic").namespace == "anthropic"


def test_resolve_provider_rejects_unknown_namespace() -> None:
    with pytest.raises(LLMError):
        resolve_provider("definitely-not-a-real-provider")


def test_create_client_returns_protocol_conforming_client() -> None:
    client = create_client("openai", timeout=15.0)

    assert isinstance(client, LLMClient)
    assert isinstance(client, LiteLLMClient)


def test_create_client_rejects_unknown_namespace() -> None:
    with pytest.raises(LLMError):
        create_client("definitely-not-a-real-provider")


def test_create_client_applies_litellm_prefix() -> None:
    client = create_client("anthropic", model="claude-sonnet-4-5")

    assert isinstance(client, LiteLLMClient)
    assert client.model == "anthropic/claude-sonnet-4-5"


def test_create_client_uses_registry_default_model() -> None:
    client = create_client("anthropic")

    assert isinstance(client, LiteLLMClient)
    assert client.model == "anthropic/claude-sonnet-4-5"


def test_litellm_client_request_model_gets_prefix() -> None:
    client = LiteLLMClient(model="anthropic/claude-sonnet-4-5", model_prefix="anthropic")

    payload = client.build_payload(LLMRequest(model="claude-opus-4-5"))

    assert payload["model"] == "anthropic/claude-opus-4-5"


def test_litellm_client_keeps_explicit_prefix() -> None:
    client = LiteLLMClient(model="anthropic/claude-sonnet-4-5", model_prefix="anthropic")

    payload = client.build_payload(LLMRequest(model="openai/gpt-4o-mini"))

    assert payload["model"] == "openai/gpt-4o-mini"


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


def test_generate_returns_response(captured_call: tuple[dict[str, Any], Any]) -> None:
    captured, completion = captured_call
    client = LiteLLMClient(model="gpt-4o-mini", completion=completion)

    response = asyncio.run(client.generate(LLMRequest(instructions=["hi"])))

    assert response.model == "fake-model"
    assert response.usage.input_tokens == 11
    assert response.usage.output_tokens == 5
    assert captured["model"] == "gpt-4o-mini"


def test_generate_parses_tool_calls() -> None:
    async def fake_acompletion(**kwargs: Any) -> _FakeRaw:
        return _FakeRaw(_FakeMessage(None, [_FakeToolCall()]))

    client = LiteLLMClient(model="gpt-4o-mini", completion=fake_acompletion)

    response = asyncio.run(client.generate(LLMRequest()))

    tool_calls = [item for item in response.contents if isinstance(item, ToolCall)]
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "search"
    assert tool_calls[0].arguments == {"q": "闭包"}


def test_generate_maps_litellm_timeout() -> None:
    from litellm.exceptions import Timeout as LiteLLMTimeout

    async def fake_acompletion(**kwargs: Any) -> Any:
        raise LiteLLMTimeout(
            "request timed out",
            model="gpt-4o-mini",
            llm_provider="openai",
        )

    client = LiteLLMClient(model="gpt-4o-mini", completion=fake_acompletion)

    with pytest.raises(LLMTimeoutError):
        asyncio.run(client.generate(LLMRequest()))


def test_generate_maps_generic_error() -> None:
    async def fake_acompletion(**kwargs: Any) -> Any:
        raise RuntimeError("boom")

    client = LiteLLMClient(model="gpt-4o-mini", completion=fake_acompletion)

    with pytest.raises(LLMError):
        asyncio.run(client.generate(LLMRequest()))


def test_generate_maps_builtin_timeout() -> None:
    async def fake_acompletion(**kwargs: Any) -> Any:
        raise TimeoutError("asyncio timeout")

    client = LiteLLMClient(model="gpt-4o-mini", completion=fake_acompletion)

    with pytest.raises(LLMTimeoutError):
        asyncio.run(client.generate(LLMRequest()))


def test_generate_structured_adds_schema_instruction() -> None:
    captured: dict[str, Any] = {}

    async def fake_acompletion(**kwargs: Any) -> _FakeRaw:
        captured.update(kwargs)
        return _FakeRaw(_FakeMessage('{"title": "T", "steps": ["s1"]}'))

    client = LiteLLMClient(model="gpt-4o-mini", completion=fake_acompletion)

    result = asyncio.run(client.generate_structured(LLMRequest(instructions=["做计划"]), Plan))

    assert result.title == "T"
    assert result.steps == ["s1"]
    instruction = captured["messages"][0]["content"]
    assert "做计划" in instruction
    assert "steps" in instruction


def test_create_client_forwards_completion_injection() -> None:
    """公开注入点可经 create_client 透传，无需直接构造适配器。"""
    captured: dict[str, Any] = {}

    async def fake_acompletion(**kwargs: Any) -> _FakeRaw:
        captured.update(kwargs)
        return _FakeRaw(_FakeMessage("hello"))

    client = create_client("openai", api_key="k", completion=fake_acompletion)

    assert isinstance(client, LiteLLMClient)
    assert client.completion is fake_acompletion

    response = asyncio.run(client.generate(LLMRequest(instructions=["hi"])))

    first = response.contents[0]
    assert isinstance(first, Message)
    assert first.content == "hello"
    assert captured["model"] == "openai/gpt-4o-mini"


def test_resolve_completion_prefers_injected_function() -> None:
    async def fake_acompletion(**kwargs: Any) -> Any:
        return None

    client = LiteLLMClient(model="gpt-4o-mini", completion=fake_acompletion)

    assert client.resolve_completion() is fake_acompletion


def test_resolve_completion_falls_back_to_litellm() -> None:
    """未注入时回落到 litellm.acompletion（只解析函数，不发请求）。"""
    import litellm

    client = LiteLLMClient(model="gpt-4o-mini")

    assert client.resolve_completion() is litellm.acompletion
