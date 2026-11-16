"""Errors raised by LLM client implementations."""

from __future__ import annotations


class LLMError(Exception):
    """Base error for all LLM client failures.

    提供商适配器必须把厂商 SDK 的异常转换为 ``LLMError``（或其子类），
    上层业务逻辑不感知具体厂商。
    """


class LLMParseError(LLMError):
    """Raised when a model response cannot be parsed into the expected shape.

    典型场景：请求结构化输出时，模型返回的内容不是合法的 JSON，
    或不符合目标 Pydantic 模型的结构。
    """
