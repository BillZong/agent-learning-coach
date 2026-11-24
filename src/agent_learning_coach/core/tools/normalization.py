"""工具结果归一化。

工具内部返回什么类型是不受控的：字符串、字典、列表、Pydantic 模型、``None``，
甚至异常。而回填给模型的内容必须**始终是可安全阅读的字符串**，否则模型会
收到无法理解的原始结构，推理质量随之下降。

本模块把「任意返回值」收敛成统一的 ``ToolOutcome``：

- 文本类（str / int / float / bool）→ 原样作为 ``content``。
- 结构类（dict / list / BaseModel）→ JSON 序列化作为 ``content``，
  同时保留原始结构到 ``data`` 供程序化消费。
- ``None`` → 空内容，成功状态。
- 异常 → 错误状态，错误描述取异常文本。

关键约束：**归一化本身不得抛出异常**。归一化失败会让原始错误信息丢失，
反而更难排查，因此序列化失败时降级为 ``repr`` 而不是向上抛。
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from pydantic import BaseModel

from agent_learning_coach.core.tools.types import ToolOutcome


def normalize_content(value: Any) -> str:
    """把任意返回值转换为可回填给模型的文本。

    Args:
        value: 工具的原始返回值。

    Returns:
        文本化结果。结构化对象返回 JSON；自定义对象返回其 ``repr``；
        两者都失败时返回占位文本。绝不抛出。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, BaseModel):
        return _dump_json(value.model_dump(mode="json"))
    if isinstance(value, (dict, list, tuple, bool, int, float)):
        return _dump_json(value)
    return _safe_repr(value)


def normalize_data(value: Any) -> dict[str, Any] | None:
    """提取可程序化消费的结构化结果。

    Args:
        value: 工具的原始返回值。

    Returns:
        dict 形式的结构化结果；非 dict 返回值返回 None。
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return None


def _dump_json(value: Any) -> str:
    """JSON 序列化，失败时降级为 ``repr``，绝不抛出。

    Args:
        value: 待序列化对象。

    Returns:
        JSON 字符串，或降级后的 ``repr`` 字符串。
    """
    try:
        return json.dumps(value, ensure_ascii=False, default=_safe_repr)
    except (TypeError, ValueError):
        return _safe_repr(value)


def _safe_repr(value: Any) -> str:
    """安全的 ``repr``，自身失败时返回占位文本。

    Args:
        value: 待转换对象。

    Returns:
        ``repr`` 字符串，或 ``"<unserializable>"``。
    """
    try:
        return repr(value)
    except Exception:
        return "<unserializable>"


def normalize_outcome(
    tool_name: str,
    value: Any,
    *,
    duration_ms: float = 0.0,
    metadata: dict[str, Any] | None = None,
) -> ToolOutcome:
    """把工具的返回值归一化成 ``ToolOutcome``。

    **工具可以直接返回 ``ToolOutcome``**，此时原样采用（只补上耗时与元数据）。
    这解决了「既要给模型看的文本，又要给程序用的结构」的需求——纯文本返回拿不
    到结构化数据，纯字典返回给模型的又是 JSON 而不是可读文本。

    Args:
        tool_name: 工具名。
        value: 工具的原始返回值。
        duration_ms: 执行耗时。
        metadata: 附加元数据。

    Returns:
        状态为 ``"success"`` 的归一化结果。
    """
    if isinstance(value, ToolOutcome):
        return replace(
            value,
            duration_ms=duration_ms,
            metadata={**value.metadata, **(metadata or {})},
        )
    return ToolOutcome(
        tool_name=tool_name,
        status="success",
        content=normalize_content(value),
        data=normalize_data(value),
        duration_ms=duration_ms,
        metadata=dict(metadata or {}),
    )


def normalize_error(
    tool_name: str,
    error: BaseException,
    *,
    duration_ms: float = 0.0,
    metadata: dict[str, Any] | None = None,
) -> ToolOutcome:
    """把异常归一化成失败状态的 ``ToolOutcome``。

    Args:
        tool_name: 工具名。
        error: 捕获到的异常。
        duration_ms: 执行耗时（毫秒）。
        metadata: 附加元数据。

    Returns:
        状态为 ``"error"`` 的归一化结果。
    """
    return ToolOutcome(
        tool_name=tool_name,
        status="error",
        content=f"工具 {tool_name} 执行失败：{error}",
        error=f"{type(error).__name__}: {error}",
        duration_ms=duration_ms,
        metadata=dict(metadata or {}),
    )


__all__ = ["normalize_content", "normalize_data", "normalize_error", "normalize_outcome"]
