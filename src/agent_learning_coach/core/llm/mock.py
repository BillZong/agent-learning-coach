"""测试用的 ``LLMClient`` Mock 实现。

用途：
- 单元测试中替换真实模型调用，避免网络依赖与非确定性。
- 本地开发时跑通上层流程（应用层、Agent Loop）而不消耗额度。
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from agent_learning_coach.core.llm.errors import LLMError
from agent_learning_coach.core.llm.models import LLMRequest, LLMResponse
from agent_learning_coach.core.llm.structured import extract_text, parse_structured_output
from agent_learning_coach.core.llm.types import Message


class MockLLMClient:
    """按预设规则返回结果的 ``LLMClient`` 实现。

    两种用法：

    1. 固定文本：``MockLLMClient(text="hello")``
    2. 自定义处理：``MockLLMClient(handler=lambda request: LLMResponse(...))``
    """

    def __init__(
        self,
        text: str = "mock response",
        handler: Callable[[LLMRequest], LLMResponse] | None = None,
        model: str = "mock-model",
    ) -> None:
        """初始化 Mock 客户端。

        Args:
            text: ``handler`` 为空时返回的固定文本。
            handler: 自定义响应函数，优先于 ``text``。
            model: 响应中报告的模型名。
        """
        self.text = text
        self.handler = handler
        self.model = model
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """返回预设响应，并记录入参。"""
        self.requests.append(request)
        if self.handler is not None:
            return self.handler(request)
        return LLMResponse(
            model=self.model,
            contents=[Message(role="assistant", content=self.text)],
        )

    async def generate_structured[ResponseT: BaseModel](
        self,
        request: LLMRequest,
        output_model: type[ResponseT],
    ) -> ResponseT:
        """返回预设响应，并按 ``output_model`` 解析。

        Raises:
            LLMParseError: 预设文本无法解析为 ``output_model``。
        """
        response = await self.generate(request)
        return parse_structured_output(extract_text(response.contents), output_model)


class FailingLLMClient:
    """始终失败的 ``LLMClient`` 实现，用于验证错误处理路径。"""

    def __init__(self, message: str = "mock failure", error: type[Exception] = LLMError) -> None:
        """初始化失败客户端。

        Args:
            message: 异常消息。
            error: 抛出的异常类型，默认为 ``LLMError``。
        """
        self.message = message
        self.error = error
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """记录入参后抛出预设异常。"""
        self.requests.append(request)
        raise self.error(self.message)

    async def generate_structured[ResponseT: BaseModel](
        self,
        request: LLMRequest,
        output_model: type[ResponseT],
    ) -> ResponseT:
        """记录入参后抛出预设异常。"""
        self.requests.append(request)
        raise self.error(self.message)
