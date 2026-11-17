"""OpenAI 兼容协议适配器。

面向提供 OpenAI 兼容 ``/chat/completions`` 端点的厂商（MiniMax、DeepSeek、
通义千问、智谱、Moonshot、SiliconFlow 等）。与 LiteLLM 路由不同，这里直接
使用 OpenAI SDK 访问厂商自有端点：

- 模型名保持厂商原生形式（``minimax-M3.1-Flash``），不加 ``openai/`` 前缀。
- 端点通过 ``base_url`` 指定，凭证通过 ``api_key`` 指定。
- 请求/响应结构与 OpenAI 一致，因此复用 ``messages.py`` 的转换逻辑。
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel

from agent_learning_coach.core.llm.errors import LLMError, LLMTimeoutError
from agent_learning_coach.core.llm.messages import build_messages, build_tools, parse_response
from agent_learning_coach.core.llm.models import LLMRequest, LLMResponse
from agent_learning_coach.core.llm.structured import (
    build_structured_instruction,
    extract_text,
    parse_structured_output,
)


class OpenAICompatibleClient:
    """OpenAI 兼容端点的 ``LLMClient`` 实现。

    用法::

        client = OpenAICompatibleClient(
            model="MiniMax-M3.1-Flash",
            api_base="https://api.minimax.cn/v1",
            api_key="...",
        )
    """

    def __init__(
        self,
        model: str,
        api_base: str,
        api_key: str,
        *,
        timeout: float | None = None,
        extra_headers: dict[str, str] | None = None,
        **config: Any,
    ) -> None:
        """初始化客户端。

        Args:
            model: 厂商原生模型名，如 ``minimax-M3.1-Flash``。
            api_base: 厂商端点 base url，需含版本前缀（通常以 ``/v1`` 结尾）。
            api_key: 访问凭证。
            timeout: 默认超时秒数。请求级 ``LLMRequest.timeout`` 优先。
            extra_headers: 附加请求头。
            **config: 透传给 OpenAI SDK 的其他参数。
        """
        self.model = model
        self.api_base = api_base
        self.api_key = api_key
        self.timeout = timeout
        self.extra_headers = dict(extra_headers or {})
        self.config = config
        self._client: Any | None = None
        """外部注入的 SDK 客户端（如测试替身）。非 None 时优先使用。"""
        self._sdk_client: Any | None = None
        """自建的 SDK 客户端，按 event loop 缓存。"""
        self._sdk_client_loop: asyncio.AbstractEventLoop | None = None
        """``_sdk_client`` 创建时所在的 event loop。"""

    def _get_client(self) -> Any:
        """惰性创建 OpenAI 异步客户端。

        OpenAI SDK 内部的 httpx 连接池绑定在创建时的 event loop 上。若同一客户端
        被用于另一个 event loop（例如脚本里多次 ``asyncio.run()``），连接池会因
        旧 loop 已关闭而报 ``Event loop is closed``。因此这里按当前 running loop
        追踪**自建**的客户端，loop 变化时重建。

        外部注入的客户端（``_client`` 由调用方赋值）不做 loop 判定：注入方
        自行管理其生命周期。
        """
        if self._client is not None:
            # 外部注入的客户端，直接复用
            return self._client

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if self._sdk_client is not None and loop is not None and self._sdk_client_loop is not loop:
            # 旧的连接池绑定在已关闭的 loop 上，丢弃重建
            self._sdk_client = None
            self._sdk_client_loop = None

        if self._sdk_client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:  # pragma: no cover - 依赖缺失时的显式错误
                raise LLMError("未安装 openai，无法使用 OpenAICompatibleClient") from exc

            self._sdk_client = AsyncOpenAI(
                base_url=self.api_base,
                api_key=self.api_key,
                timeout=self.timeout,
                default_headers=self.extra_headers or None,
                **self.config,
            )
            self._sdk_client_loop = loop
        return self._sdk_client

    def build_payload(self, request: LLMRequest) -> dict[str, Any]:
        """构造 OpenAI SDK 调用参数。"""
        payload: dict[str, Any] = {
            "model": request.model or self.model,
            "messages": build_messages(request),
        }
        if request.tools:
            payload["tools"] = build_tools(request.tools)
        if request.tool_choice:
            payload["tool_choice"] = request.tool_choice
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens

        timeout = request.timeout if request.timeout is not None else self.timeout
        if timeout is not None:
            payload["timeout"] = timeout

        payload.update(request.extra)
        return payload

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """执行一次 LLM 调用。

        Raises:
            LLMTimeoutError: 调用超过超时时间。
            LLMError: 其他调用失败。
        """
        client = self._get_client()
        payload = self.build_payload(request)
        model = payload["model"]

        try:
            raw = await client.chat.completions.create(**payload)
        except Exception as exc:
            raise _translate_error(exc) from exc

        return parse_response(raw, model=model)

    async def generate_structured[ResponseT: BaseModel](
        self,
        request: LLMRequest,
        output_model: type[ResponseT],
    ) -> ResponseT:
        """执行一次 LLM 调用并解析为 ``output_model``。"""
        structured_request = request.model_copy(deep=True)
        structured_request.instructions = [
            build_structured_instruction(instruction, output_model)
            for instruction in request.instructions
        ]

        response = await self.generate(structured_request)
        text = extract_text(response.contents)
        return parse_structured_output(text, output_model)

    async def aclose(self) -> None:
        """关闭底层连接池。

        OpenAI SDK 内部的 httpx 连接池持有 socket 与后台任务，不显式关闭时会在
        所属 event loop 关闭后被 GC，产生 ``Event loop is closed`` 噪声。
        短生命周期脚本应在退出前调用本方法。

        外部注入的客户端由注入方管理生命周期，本方法不处理。
        """
        client = self._sdk_client
        self._sdk_client = None
        self._sdk_client_loop = None
        if client is None:
            return
        try:
            await client.close()
        except Exception:  # pragma: no cover - 关闭失败不应掩盖主流程
            pass


def _translate_error(exc: Exception) -> LLMError:
    """把 OpenAI SDK 异常转换为厂商无关的异常。"""
    if isinstance(exc, TimeoutError):
        return LLMTimeoutError(f"LLM 调用超时: {exc}")

    try:
        from openai import APITimeoutError
    except ImportError:  # pragma: no cover - 依赖缺失时的兜底
        APITimeoutError = None  # type: ignore[assignment,misc]

    if APITimeoutError is not None and isinstance(exc, APITimeoutError):
        return LLMTimeoutError(f"LLM 调用超时: {exc}")

    return LLMError(f"LLM 调用失败: {exc}")
