"""Errors raised by LLM client implementations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


class LLMError(Exception):
    """Base error for all LLM client failures.

    提供商适配器必须把厂商 SDK 的异常转换为 ``LLMError``（或其子类），
    上层业务逻辑不感知具体厂商。
    """


class LLMTimeoutError(LLMError):
    """Raised when a model call exceeds the configured timeout.

    超时是可预期的、可重试的失败，因此单独成类，便于应用层区分
    “需要重试或降级”与“请求本身有问题”。
    """


@dataclass(frozen=True)
class FieldError:
    """结构化输出的一处校验失败——**事实依据**。

    保留**原始错误条目**（``raw``）与**归一化字段**（``loc`` / ``type`` /
    ``msg``）。原始条目是排查问题的依据：它带着厂商的原始上下文、输入值、
    错误码。归一化字段是给人和模型看的稳定形状：换厂商、换校验库时形状不变。

    **两者都要有。** 只留归一化字段会丢掉定位细节；只留原始条目则每次消费都要
    自己解析厂商格式。
    """

    loc: tuple[str, ...]
    """出错字段的位置，如 ``("expected_answer",)``。"""

    type: str
    """错误类型，如 ``missing`` / ``string_too_short``。"""

    msg: str
    """人类可读的错误说明。"""

    raw: Mapping[str, Any] = field(default_factory=dict)
    """原始错误条目——排查依据，原样保留。"""

    def summary(self) -> str:
        """一句话摘要：字段 + 原因。

        给模型看或给日志看时用这一行；需要更多上下文时读 :attr:`raw`。

        Returns:
            摘要文本。
        """
        location = ".".join(self.loc) or "输出"
        return f"{location}：{self.msg}"

    def detail(self) -> str:
        """完整细节：摘要 + 原始条目。

        Returns:
            细节文本。
        """
        if not self.raw:
            return self.summary()
        return f"{self.summary()}（原始：{dict(self.raw)!r}）"


class LLMParseError(LLMError):
    """Raised when a model response cannot be parsed into the expected shape.

    典型场景：请求结构化输出时，模型返回的内容不是合法的 JSON，
    或不符合目标 Pydantic 模型的结构。

    **结构化校验失败时，``field_errors`` 保留原始错误条目**（ADR-0022）：
    异常消息是给人和日志看的摘要，``field_errors`` 是给程序看的事实依据。
    两者并存，不互相替代——消息可能被人改写或截断，事实不会。
    """

    def __init__(
        self,
        message: str,
        *,
        field_errors: Sequence[FieldError] = (),
    ) -> None:
        """初始化解析异常。

        Args:
            message: 面向人的错误摘要。
            field_errors: 结构化校验失败的原始条目。
        """
        super().__init__(message)
        self.field_errors: tuple[FieldError, ...] = tuple(field_errors)

    @property
    def summary(self) -> str:
        """面向模型的摘要：逐条列出字段级原因。

        Returns:
            摘要文本；没有字段级错误时返回异常消息。
        """
        if not self.field_errors:
            return str(self)
        return "；".join(error.summary() for error in self.field_errors)

    @property
    def detail(self) -> str:
        """完整细节：摘要 + 原始条目。

        Returns:
            细节文本。
        """
        if not self.field_errors:
            return str(self)
        return "；".join(error.detail() for error in self.field_errors)
