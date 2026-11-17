"""Tests for the OpenAI-compatible adapter.

重点覆盖跨 event loop 复用客户端的场景：OpenAI SDK 内部的 httpx 连接池绑定在
创建时的 event loop 上，若不做隔离，第二次 ``asyncio.run()`` 会因旧 loop 已
关闭而抛 ``Event loop is closed``。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_learning_coach.core.llm import LLMRequest, Message, OpenAICompatibleClient


class _FakeCompletions:
    def __init__(self, owner: _FakeOpenAI) -> None:
        self._owner = owner

    async def create(self, **payload: Any) -> Any:
        self._owner.calls.append(payload)
        message = _FakeMessage()
        choice = _FakeChoice(message)
        return _FakeRaw(choice)


class _FakeMessage:
    content = "hello"
    tool_calls = None


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message
        self.finish_reason = "stop"


class _FakeUsage:
    prompt_tokens = 5
    completion_tokens = 2


class _FakeRaw:
    def __init__(self, choice: _FakeChoice) -> None:
        self.choices = [choice]
        self.usage = _FakeUsage()
        self.model = "fake-model"


class _FakeOpenAI:
    """最小化的 AsyncOpenAI 替身，记录每次构造。"""

    instances: list[_FakeOpenAI] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls: list[dict[str, Any]] = []
        self.closed = False
        self.chat = type("Chat", (), {"completions": _FakeCompletions(self)})()
        _FakeOpenAI.instances.append(self)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_openai(monkeypatch: pytest.MonkeyPatch) -> type[_FakeOpenAI]:
    """把 openai.AsyncOpenAI 替换为替身。"""
    _FakeOpenAI.instances = []
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    return _FakeOpenAI


def _client() -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        model="MiniMax-M3.1-Flash",
        api_base="https://api.minimax.cn/v1",
        api_key="test-key",
    )


def test_client_is_created_lazily(fake_openai: type[_FakeOpenAI]) -> None:
    client = _client()

    assert fake_openai.instances == []

    asyncio.run(client.generate(LLMRequest()))

    assert len(fake_openai.instances) == 1
    assert fake_openai.instances[0].kwargs["base_url"] == "https://api.minimax.cn/v1"


def test_injected_client_takes_precedence(fake_openai: type[_FakeOpenAI]) -> None:
    """外部注入的客户端优先使用，且不额外创建 SDK 客户端。"""
    injected = _FakeOpenAI()
    client = _client()
    client._client = injected

    asyncio.run(client.generate(LLMRequest()))

    assert fake_openai.instances == [injected]
    assert injected.calls[0]["model"] == "MiniMax-M3.1-Flash"


def test_same_loop_reuses_one_client(fake_openai: type[_FakeOpenAI]) -> None:
    client = _client()

    async def call_twice() -> None:
        await client.generate(LLMRequest())
        await client.generate(LLMRequest())

    asyncio.run(call_twice())

    assert len(fake_openai.instances) == 1


def test_new_event_loop_rebuilds_client(fake_openai: type[_FakeOpenAI]) -> None:
    """跨 event loop 复用客户端时必须重建连接池，而不是复用已关闭 loop 的连接。"""
    client = _client()

    asyncio.run(client.generate(LLMRequest()))
    asyncio.run(client.generate(LLMRequest()))

    assert len(fake_openai.instances) == 2
    assert fake_openai.instances[0] is not fake_openai.instances[1]


def test_aclose_releases_client(fake_openai: type[_FakeOpenAI]) -> None:
    client = _client()

    async def use_then_close() -> None:
        await client.generate(LLMRequest())
        await client.aclose()

    asyncio.run(use_then_close())

    assert fake_openai.instances[0].closed is True


def test_aclose_is_idempotent(fake_openai: type[_FakeOpenAI]) -> None:
    client = _client()

    async def close_twice() -> None:
        await client.aclose()
        await client.aclose()

    asyncio.run(close_twice())

    assert fake_openai.instances == []


def test_aclose_leaves_injected_client_untouched(fake_openai: type[_FakeOpenAI]) -> None:
    """注入的客户端由注入方管理生命周期。"""
    injected = _FakeOpenAI()
    client = _client()
    client._client = injected

    asyncio.run(client.aclose())

    assert injected.closed is False


def test_generate_sends_messages_and_model(fake_openai: type[_FakeOpenAI]) -> None:
    client = _client()

    asyncio.run(
        client.generate(
            LLMRequest(
                instructions=["用一句话回答"],
                contents=[Message(role="user", content="1+1=?")],
            )
        )
    )

    payload = fake_openai.instances[0].calls[0]
    assert payload["model"] == "MiniMax-M3.1-Flash"
    assert payload["messages"][0] == {"role": "system", "content": "用一句话回答"}
    assert payload["messages"][1] == {"role": "user", "content": "1+1=?"}
