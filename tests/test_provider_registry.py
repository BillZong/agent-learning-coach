"""Tests for the provider namespace registry and the OpenAI-compatible adapter."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel

from agent_learning_coach.core.llm import (
    LLMClient,
    LLMError,
    LLMRequest,
    LLMTimeoutError,
    Message,
    OpenAICompatibleClient,
    ProtocolKind,
    ProviderSpec,
    ToolCall,
    ToolSpec,
    create_client,
    get_provider,
    list_providers,
    provider_namespaces,
    register_provider,
    unregister_provider,
)
from agent_learning_coach.core.llm.registry import build_client_config


class _FakeMessage:
    def __init__(self, content: str | None, tool_calls: list[Any] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message
        self.finish_reason = "stop"


class _FakeUsage:
    prompt_tokens = 9
    completion_tokens = 4


class _FakeCompletion:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]
        self.usage = _FakeUsage()
        self.model = "fake-model"


class _FakeCompletions:
    def __init__(self, captured: dict[str, Any], message: _FakeMessage) -> None:
        self._captured = captured
        self._message = message

    async def create(self, **kwargs: Any) -> _FakeCompletion:
        self._captured.update(kwargs)
        return _FakeCompletion(self._message)


class _FakeChat:
    def __init__(self, captured: dict[str, Any], message: _FakeMessage) -> None:
        self.completions = _FakeCompletions(captured, message)


class _FakeOpenAI:
    def __init__(self, captured: dict[str, Any], message: _FakeMessage) -> None:
        self.chat = _FakeChat(captured, message)


class Plan(BaseModel):
    """测试用的结构化输出模型。"""

    title: str
    steps: list[str]


def test_builtin_namespaces_are_registered() -> None:
    namespaces = provider_namespaces()

    for expected in ("openai", "anthropic", "gemini", "minimax", "deepseek", "qwen"):
        assert expected in namespaces


def test_list_providers_returns_specs() -> None:
    specs = list_providers()

    assert all(isinstance(spec, ProviderSpec) for spec in specs)
    assert {spec.namespace for spec in specs} == set(provider_namespaces())


def test_minimax_uses_openai_compatible_protocol() -> None:
    spec = get_provider("minimax")

    assert spec.protocol is ProtocolKind.OPENAI_COMPATIBLE
    assert spec.api_base == "https://api.minimaxi.com/v1"
    assert spec.default_model == "minimax-M3.1-Flash"


def test_anthropic_uses_litellm_protocol() -> None:
    spec = get_provider("anthropic")

    assert spec.protocol is ProtocolKind.LITELLM
    assert spec.litellm_prefix == "anthropic"


def test_litellm_model_adds_prefix() -> None:
    spec = get_provider("anthropic")

    assert spec.litellm_model("claude-sonnet-4-5") == "anthropic/claude-sonnet-4-5"


def test_openai_compatible_model_keeps_native_name() -> None:
    spec = get_provider("minimax")

    assert spec.litellm_model("minimax-M3.1-Flash") == "minimax-M3.1-Flash"


def test_litellm_model_without_prefix_keeps_model() -> None:
    spec = ProviderSpec(
        namespace="no-prefix",
        display_name="No Prefix",
        protocol=ProtocolKind.LITELLM,
    )

    assert spec.litellm_model("some-model") == "some-model"


def test_resolved_model_uses_default() -> None:
    spec = get_provider("minimax")

    assert spec.resolved_model(None) == "minimax-M3.1-Flash"


def test_resolved_model_prefers_manual_override() -> None:
    spec = get_provider("minimax")

    assert spec.resolved_model("minimax-M2") == "minimax-M2"


def test_resolved_model_requires_model_when_no_default() -> None:
    spec = ProviderSpec(
        namespace="custom",
        display_name="Custom",
        protocol=ProtocolKind.OPENAI_COMPATIBLE,
        api_base="https://example.com/v1",
    )

    with pytest.raises(ValueError):
        spec.resolved_model(None)


def test_register_provider_rejects_duplicate_namespace() -> None:
    with pytest.raises(ValueError):
        register_provider(
            ProviderSpec(
                namespace="minimax",
                display_name="Dup",
                protocol=ProtocolKind.OPENAI_COMPATIBLE,
                api_base="https://example.com/v1",
            )
        )


def test_register_provider_allows_overwrite() -> None:
    spec = ProviderSpec(
        namespace="tmp-namespace",
        display_name="Tmp",
        protocol=ProtocolKind.OPENAI_COMPATIBLE,
        api_base="https://example.com/v1",
        default_model="tmp-model",
    )
    try:
        register_provider(spec)
        assert get_provider("tmp-namespace").default_model == "tmp-model"
        register_provider(spec, overwrite=True)
    finally:
        unregister_provider("tmp-namespace")


def test_build_client_config_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    spec = get_provider("minimax")

    with pytest.raises(ValueError):
        build_client_config(spec)


def test_build_client_config_reads_env_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIMAX_API_KEY", "secret-key")
    spec = get_provider("minimax")

    config = build_client_config(spec, timeout=12.0)

    assert config["api_key"] == "secret-key"
    assert config["model"] == "minimax-M3.1-Flash"
    assert config["api_base"] == "https://api.minimaxi.com/v1"
    assert config["timeout"] == 12.0


def test_build_client_config_requires_api_base() -> None:
    spec = ProviderSpec(
        namespace="no-base",
        display_name="No Base",
        protocol=ProtocolKind.OPENAI_COMPATIBLE,
        api_key_env="NO_BASE_API_KEY",
        default_model="some-model",
    )

    with pytest.raises(ValueError):
        build_client_config(spec, api_key="k")


def test_build_client_config_litellm_includes_api_key_and_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "secret-key")
    spec = get_provider("anthropic")

    config = build_client_config(spec, api_base="https://proxy.example/v1")

    assert config["model"] == "anthropic/claude-sonnet-4-5"
    assert config["api_key"] == "secret-key"
    assert config["api_base"] == "https://proxy.example/v1"


def test_create_client_builds_openai_compatible_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIMAX_API_KEY", "secret-key")

    client = create_client("minimax", timeout=20.0)

    assert isinstance(client, LLMClient)
    assert isinstance(client, OpenAICompatibleClient)
    assert client.model == "minimax-M3.1-Flash"
    assert client.api_base == "https://api.minimaxi.com/v1"


def test_create_client_allows_manual_model_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINIMAX_API_KEY", "secret-key")

    client = create_client("minimax", model="minimax-M2")

    assert isinstance(client, OpenAICompatibleClient)
    assert client.model == "minimax-M2"


def test_create_client_converts_config_error_to_llm_error() -> None:
    spec = ProviderSpec(
        namespace="incomplete-namespace",
        display_name="Incomplete",
        protocol=ProtocolKind.OPENAI_COMPATIBLE,
        api_key_env="INCOMPLETE_API_KEY",
    )
    try:
        register_provider(spec)
        with pytest.raises(LLMError):
            create_client("incomplete-namespace", api_key="k")
    finally:
        unregister_provider("incomplete-namespace")


def test_openai_compatible_payload_uses_native_model_name() -> None:
    client = OpenAICompatibleClient(
        model="minimax-M3.1-Flash",
        api_base="https://api.minimaxi.com/v1",
        api_key="k",
    )

    payload = client.build_payload(LLMRequest(instructions=["hi"]))

    assert payload["model"] == "minimax-M3.1-Flash"
    assert "openai/" not in payload["model"]


def test_openai_compatible_request_level_model_switch() -> None:
    client = OpenAICompatibleClient(
        model="minimax-M3.1-Flash",
        api_base="https://api.minimaxi.com/v1",
        api_key="k",
    )

    payload = client.build_payload(LLMRequest(model="minimax-M2"))

    assert payload["model"] == "minimax-M2"


def test_openai_compatible_timeout_priority() -> None:
    client = OpenAICompatibleClient(
        model="minimax-M3.1-Flash",
        api_base="https://api.minimaxi.com/v1",
        api_key="k",
        timeout=30.0,
    )

    assert client.build_payload(LLMRequest())["timeout"] == 30.0
    assert client.build_payload(LLMRequest(timeout=5.0))["timeout"] == 5.0


def test_openai_compatible_generate_parses_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    client = OpenAICompatibleClient(
        model="minimax-M3.1-Flash",
        api_base="https://api.minimaxi.com/v1",
        api_key="k",
    )
    client._client = _FakeOpenAI(captured, _FakeMessage("hello"))

    response = asyncio.run(client.generate(LLMRequest(instructions=["hi"])))

    assert response.model == "fake-model"
    assert response.usage.input_tokens == 9
    assert captured["model"] == "minimax-M3.1-Flash"
    assert captured["messages"][0] == {"role": "system", "content": "hi"}


def test_openai_compatible_generate_parses_tool_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Fn:
        name = "search"
        arguments = '{"q": "x"}'

    class _Tc:
        id = "call-1"
        function = _Fn()

    client = OpenAICompatibleClient(
        model="minimax-M3.1-Flash",
        api_base="https://api.minimaxi.com/v1",
        api_key="k",
    )
    client._client = _FakeOpenAI({}, _FakeMessage(None, [_Tc()]))

    response = asyncio.run(client.generate(LLMRequest()))

    tool_calls = [item for item in response.contents if isinstance(item, ToolCall)]
    assert tool_calls[0].name == "search"
    assert tool_calls[0].arguments == {"q": "x"}


def test_openai_compatible_maps_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from openai import APITimeoutError

    class _Failing:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                async def create(**kwargs: Any) -> Any:
                    raise APITimeoutError(request=None)  # type: ignore[arg-type]

    client = OpenAICompatibleClient(
        model="minimax-M3.1-Flash",
        api_base="https://api.minimaxi.com/v1",
        api_key="k",
    )
    client._client = _Failing()

    with pytest.raises(LLMTimeoutError):
        asyncio.run(client.generate(LLMRequest()))


def test_openai_compatible_maps_generic_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Failing:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                async def create(**kwargs: Any) -> Any:
                    raise RuntimeError("boom")

    client = OpenAICompatibleClient(
        model="minimax-M3.1-Flash",
        api_base="https://api.minimaxi.com/v1",
        api_key="k",
    )
    client._client = _Failing()

    with pytest.raises(LLMError):
        asyncio.run(client.generate(LLMRequest()))


def test_openai_compatible_maps_builtin_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Failing:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                async def create(**kwargs: Any) -> Any:
                    raise TimeoutError("asyncio timeout")

    client = OpenAICompatibleClient(
        model="minimax-M3.1-Flash",
        api_base="https://api.minimaxi.com/v1",
        api_key="k",
    )
    client._client = _Failing()

    with pytest.raises(LLMTimeoutError):
        asyncio.run(client.generate(LLMRequest()))


def test_openai_compatible_generate_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}
    client = OpenAICompatibleClient(
        model="minimax-M3.1-Flash",
        api_base="https://api.minimaxi.com/v1",
        api_key="k",
    )
    client._client = _FakeOpenAI(captured, _FakeMessage('{"title": "T", "steps": ["s1"]}'))

    result = asyncio.run(client.generate_structured(LLMRequest(instructions=["做计划"]), Plan))

    assert result.title == "T"
    assert "steps" in captured["messages"][0]["content"]


def test_openai_compatible_message_conversion() -> None:
    client = OpenAICompatibleClient(
        model="minimax-M3.1-Flash",
        api_base="https://api.minimaxi.com/v1",
        api_key="k",
    )

    payload = client.build_payload(LLMRequest(contents=[Message(role="user", content="hi")]))

    assert payload["messages"] == [{"role": "user", "content": "hi"}]


def test_openai_compatible_payload_includes_optional_fields() -> None:
    client = OpenAICompatibleClient(
        model="minimax-M3.1-Flash",
        api_base="https://api.minimaxi.com/v1",
        api_key="k",
    )

    payload = client.build_payload(
        LLMRequest(
            tools=[
                ToolSpec(
                    name="search",
                    description="d",
                    parameters={"type": "object"},
                )
            ],
            tool_choice="auto",
            temperature=0.3,
            max_output_tokens=128,
            extra={"seed": 11},
        )
    )

    assert payload["tools"][0]["function"]["name"] == "search"
    assert payload["tool_choice"] == "auto"
    assert payload["temperature"] == 0.3
    assert payload["max_tokens"] == 128
    assert payload["seed"] == 11


def test_openai_compatible_builds_sdk_client_lazily() -> None:
    client = OpenAICompatibleClient(
        model="minimax-M3.1-Flash",
        api_base="https://api.minimaxi.com/v1",
        api_key="k",
        timeout=25.0,
        extra_headers={"X-Trace": "abc"},
    )

    sdk_client = client._get_client()

    assert sdk_client is not None
    assert str(sdk_client.base_url).rstrip("/") == "https://api.minimaxi.com/v1"
    # 惰性创建：重复调用复用同一实例
    assert client._get_client() is sdk_client
