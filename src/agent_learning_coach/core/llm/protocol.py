"""LLMClient Protocol：模型提供方无关的客户端契约。

设计要点：

1. **结构化子类型（structural subtyping）**：实现方无需继承本 Protocol，
   只要提供同名方法即可。这让 Mock、LiteLLM 适配器、测试替身都能自由替换。
2. **只暴露能力，不暴露厂商**：所有入参出参均为本包内的厂商无关模型。
3. **失败即异常**：调用失败抛 ``LLMError``，不把错误编码进响应对象，
   避免业务层遗漏检查。
"""

from __future__ import annotations

from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from agent_learning_coach.core.llm.models import LLMRequest, LLMResponse

ResponseT = TypeVar("ResponseT", bound=BaseModel)


@runtime_checkable
class LLMClient(Protocol):
    """模型客户端协议。

    实现方（LiteLLM 适配器、Mock 客户端、测试替身）需要满足：

    - ``generate``：执行一次普通调用，返回统一的 ``LLMResponse``。
    - ``generate_structured``：执行一次调用并把结果解析为指定 Pydantic 模型。

    两者的错误语义一致：任何失败都抛 ``LLMError``（结构化解析失败抛
    ``LLMParseError``）。
    """

    async def generate(self, request: LLMRequest) -> LLMResponse:
        """执行一次 LLM 调用。

        Args:
            request: 厂商无关的调用入参。

        Returns:
            厂商无关的调用结果。

        Raises:
            LLMError: 调用失败时抛出，包括超时、限流、厂商服务端错误。
        """
        ...

    async def generate_structured(
        self,
        request: LLMRequest,
        output_model: type[ResponseT],
    ) -> ResponseT:
        """执行一次 LLM 调用，并返回符合 ``output_model`` 的结构化结果。

        实现方应优先使用厂商的结构化输出能力（response schema / JSON mode）；
        厂商不支持时，回退到“提示词约束 + 严格 JSON 解析”，并保证解析失败
        抛 ``LLMParseError`` 而不是返回半成品对象。

        Args:
            request: 厂商无关的调用入参。
            output_model: 期望的结果模型。

        Returns:
            校验通过的 ``output_model`` 实例。

        Raises:
            LLMError: 调用失败时抛出。
            LLMParseError: 模型输出无法解析为 ``output_model`` 时抛出。
        """
        ...
