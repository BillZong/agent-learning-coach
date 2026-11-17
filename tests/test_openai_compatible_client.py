"""Tests for the OpenAI-compatible adapter.

重点覆盖跨 event loop 复用客户端的场景：OpenAI SDK 内部的 httpx 连接池绑定在
创建时的 event loop 上，若不做隔离，第二次 ``asyncio.run()`` 会因旧 loop 已
关闭而抛 ``Event loop is closed``。

全部通过公开注入点（``sdk_client`` / ``sdk_client_factory``）打桩，不修改
第三方模块，也不访问适配器的私有字段。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_learning_coach.core.llm import LLMRequest, Message, OpenAICompatibleClient


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
    """最小化的 AsyncOpenAI 替身。"""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls: list[dict[str, Any]] = []
        self.closed = False
        self.chat = type("Chat", (), {"completions": _FakeCompletions(self)})()

    async def close(self) -> None:
        self.closed = True


class _FakeCompletions:
    def __init__(self, owner: _FakeOpenAI) -> None:
        self._owner = owner

    async def create(self, **payload: Any) -> Any:
        self._owner.calls.append(payload)
        return _FakeRaw(_FakeChoice(_FakeMessage()))


class _Recorder:
    """记录 SDK 客户端的每次构造，供注入工厂使用。"""

    def __init__(self) -> None:
        self.instances: list[_FakeOpenAI] = []
        self.kwargs: list[dict[str, Any]] = []

    def factory(self, **kwargs: Any) -> _FakeOpenAI:
        self.kwargs.append(kwargs)
        instance = _FakeOpenAI(**kwargs)
        self.instances.append(instance)
        return instance


@pytest.fixture
def recorder() -> _Recorder:
    """返回构造记录器，其 ``factory`` 可注入适配器。"""
    return _Recorder()


def _client(**kwargs: Any) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        model="MiniMax-M3.1-Flash",
        api_base="https://api.minimax.cn/v1",
        api_key="test-key",
        **kwargs,
    )


def test_client_is_created_lazily(recorder: _Recorder) -> None:
    client = _client(sdk_client_factory=recorder.factory)

    assert recorder.instances == []

    asyncio.run(client.generate(LLMRequest()))

    assert len(recorder.instances) == 1
    assert recorder.kwargs[0]["base_url"] == "https://api.minimax.cn/v1"


def test_injected_client_takes_precedence(recorder: _Recorder) -> None:
    """外部注入的客户端优先使用，且不额外创建 SDK 客户端。"""
    injected = _FakeOpenAI()
    client = _client(sdk_client=injected)

    asyncio.run(client.generate(LLMRequest()))

    assert recorder.instances == []
    assert injected.calls[0]["model"] == "MiniMax-M3.1-Flash"


def test_same_loop_reuses_one_client(recorder: _Recorder) -> None:
    client = _client(sdk_client_factory=recorder.factory)

    async def call_twice() -> None:
        await client.generate(LLMRequest())
        await client.generate(LLMRequest())

    asyncio.run(call_twice())

    assert len(recorder.instances) == 1


def test_new_event_loop_rebuilds_client(recorder: _Recorder) -> None:
    """跨 event loop 复用客户端时必须重建连接池，而不是复用已关闭 loop 的连接。"""
    client = _client(sdk_client_factory=recorder.factory)

    asyncio.run(client.generate(LLMRequest()))
    asyncio.run(client.generate(LLMRequest()))

    assert len(recorder.instances) == 2
    assert recorder.instances[0] is not recorder.instances[1]


def test_aclose_releases_client(recorder: _Recorder) -> None:
    client = _client(sdk_client_factory=recorder.factory)

    async def use_then_close() -> None:
        await client.generate(LLMRequest())
        await client.aclose()

    asyncio.run(use_then_close())

    assert recorder.instances[0].closed is True


def test_aclose_is_idempotent(recorder: _Recorder) -> None:
    client = _client(sdk_client_factory=recorder.factory)

    async def close_twice() -> None:
        await client.aclose()
        await client.aclose()

    asyncio.run(close_twice())

    assert recorder.instances == []


def test_aclose_leaves_injected_client_untouched(recorder: _Recorder) -> None:
    """注入的客户端由注入方管理生命周期。"""
    injected = _FakeOpenAI()
    client = _client(sdk_client=injected)

    asyncio.run(client.aclose())

    assert injected.closed is False


def test_generate_sends_messages_and_model(recorder: _Recorder) -> None:
    client = _client(sdk_client_factory=recorder.factory)

    asyncio.run(
        client.generate(
            LLMRequest(
                instructions=["用一句话回答"],
                contents=[Message(role="user", content="1+1=?")],
            )
        )
    )

    payload = recorder.instances[0].calls[0]
    assert payload["model"] == "MiniMax-M3.1-Flash"
    assert payload["messages"][0] == {"role": "system", "content": "用一句话回答"}
    assert payload["messages"][1] == {"role": "user", "content": "1+1=?"}


def test_build_sdk_client_kwargs_reflects_configuration() -> None:
    client = _client(timeout=25.0, extra_headers={"X-Trace": "abc"})

    kwargs = client.build_sdk_client_kwargs()

    assert kwargs["base_url"] == "https://api.minimax.cn/v1"
    assert kwargs["api_key"] == "test-key"
    assert kwargs["timeout"] == 25.0
    assert kwargs["default_headers"] == {"X-Trace": "abc"}


def test_default_sdk_client_is_real_openai_client() -> None:
    """未注入时默认构造真实 AsyncOpenAI（只构造，不发请求，不触网）。"""
    client = _client()

    sdk_client = client.get_sdk_client()

    from openai import AsyncOpenAI

    assert isinstance(sdk_client, AsyncOpenAI)
    assert str(sdk_client.base_url).rstrip("/") == "https://api.minimax.cn/v1"
    assert str(sdk_client.api_key) == "test-key"
