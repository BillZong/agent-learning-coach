"""测试共用的替身。

放在这里而不是各自测试文件里，是为了避免两个文件各写一份几乎相同的
脚本化模型客户端——重复的替身迟早会各自漂移。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.client.session import ClientSession
from mcp.server.lowlevel import Server
from mcp.shared.memory import create_client_server_memory_streams

from agent_learning_coach.core.llm.models import LLMRequest, LLMResponse, LLMUsage
from agent_learning_coach.core.llm.types import Message, ToolCall
from agent_learning_coach.core.tools.mcp import serve


@asynccontextmanager
async def mcp_client_session(server: Server) -> AsyncIterator[ClientSession]:
    """在内存传输上建立 MCP 客户端会话。

    走真实协议，只是不起进程——比 stdio 子进程快，且同样是真实协议栈。
    """
    async with create_client_server_memory_streams() as (client_streams, server_streams):
        server_task = asyncio.create_task(serve(server, server_streams[0], server_streams[1]))
        try:
            async with ClientSession(client_streams[0], client_streams[1]) as session:
                await session.initialize()
                yield session
        finally:
            server_task.cancel()
            await asyncio.gather(server_task, return_exceptions=True)


class ScriptedClient:
    """按脚本依次返回预设响应的模型客户端。

    ``repeat_last=True`` 时脚本耗尽后重复最后一个响应——用于模拟「模型一直
    请求工具」的死循环场景。
    """

    def __init__(self, responses: list[LLMResponse], *, repeat_last: bool = False) -> None:
        """初始化脚本化客户端。

        Args:
            responses: 依次返回的响应。
            repeat_last: 脚本耗尽后是否重复最后一个响应。
        """
        self.responses = list(responses)
        self.repeat_last = repeat_last
        self.requests: list[LLMRequest] = []
        self._last_response: LLMResponse | None = None

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """返回脚本中的下一个响应。"""
        self.requests.append(request)
        if not self.responses:
            if self.repeat_last and self._last_response is not None:
                return self._last_response
            raise AssertionError("脚本已耗尽，但循环仍在调用模型")
        response = self.responses.pop(0)
        self._last_response = response
        return response

    async def generate_structured(
        self, request: LLMRequest, output_model: type[Any]
    ) -> Any:  # pragma: no cover - Agent Loop 不使用结构化输出
        raise NotImplementedError


class SlowClient:
    """每次调用都耗时的模型客户端，用于验证整体超时。"""

    def __init__(self, delay: float) -> None:
        """初始化慢客户端。

        Args:
            delay: 每次调用的耗时（秒）。
        """
        self.delay = delay

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """等待指定时长后返回文本响应。"""
        await asyncio.sleep(self.delay)
        return LLMResponse(contents=[Message(role="assistant", content="太慢了")])

    async def generate_structured(
        self, request: LLMRequest, output_model: type[Any]
    ) -> Any:  # pragma: no cover
        raise NotImplementedError


def tool_call_response(
    name: str = "search",
    arguments: dict[str, Any] | None = None,
    call_id: str = "call-1",
    model: str = "mock-model",
) -> LLMResponse:
    """构造一个只含工具调用的模型响应。

    默认上报模型名——真实客户端都会上报，成本核算依赖它。
    """
    return LLMResponse(
        model=model,
        contents=[
            ToolCall(
                tool_call_id=call_id,
                name=name,
                arguments=arguments if arguments is not None else {"query": "闭包"},
            )
        ],
        usage=LLMUsage(input_tokens=10, output_tokens=5),
    )


def text_response(text: str = "已完成", model: str = "mock-model") -> LLMResponse:
    """构造一个只含文本的模型响应。

    默认上报模型名——真实客户端都会上报，成本核算依赖它。
    """
    return LLMResponse(
        model=model,
        contents=[Message(role="assistant", content=text)],
        usage=LLMUsage(input_tokens=7, output_tokens=3),
    )


__all__ = [
    "ScriptedClient",
    "SlowClient",
    "text_response",
    "tool_call_response",
]
