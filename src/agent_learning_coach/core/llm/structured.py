"""结构化输出的公共辅助逻辑。

所有 LLMClient 实现共享同一套“提示词约束 + 严格 JSON 解析”回退路径，
保证不同提供方在结构化输出上的行为一致：
- 输入合法 JSON 但不符合 schema -> ``LLMParseError``
- 输出不是 JSON -> ``LLMParseError``
"""

from __future__ import annotations

import json

from pydantic import BaseModel, ValidationError

from agent_learning_coach.core.llm.errors import LLMParseError
from agent_learning_coach.core.llm.types import ContentItem, Message

_FENCE_PREFIXES = ("```json", "```JSON", "```")


def build_structured_instruction(prompt: str, output_model: type[BaseModel]) -> str:
    """构造要求模型输出指定 schema 的指令。

    Args:
        prompt: 原始任务指令。
        output_model: 期望的结果模型。

    Returns:
        追加了 JSON schema 约束的完整指令。
    """
    schema = json.dumps(output_model.model_json_schema(), ensure_ascii=False)
    return (
        f"{prompt}\n\n"
        "请只输出符合下面 JSON Schema 的 JSON 对象，不要输出解释、"
        "Markdown 代码块或多余文本。\n"
        f"JSON Schema:\n{schema}"
    )


def strip_code_fence(text: str) -> str:
    """去掉 Markdown 代码块包裹，兼容模型忽略“不要输出代码块”指令的情况。"""
    cleaned = text.strip()
    for prefix in _FENCE_PREFIXES:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
            break
    if cleaned.endswith("```"):
        cleaned = cleaned[: -len("```")].strip()
    return cleaned


def parse_structured_output[ResponseT: BaseModel](
    text: str,
    output_model: type[ResponseT],
) -> ResponseT:
    """把模型输出解析为 ``output_model`` 实例。

    Args:
        text: 模型原始文本输出。
        output_model: 期望的结果模型。

    Returns:
        校验通过的模型实例。

    Raises:
        LLMParseError: 输出不是合法 JSON，或不符合目标模型结构。
    """
    cleaned = strip_code_fence(text)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMParseError(
            f"模型输出不是合法 JSON: {exc.msg}; 输出片段: {cleaned[:200]!r}"
        ) from exc

    try:
        return output_model.model_validate(payload)
    except ValidationError as exc:
        raise LLMParseError(f"模型输出不符合 {output_model.__name__} 结构: {exc.errors()}") from exc


def extract_text(contents: list[ContentItem]) -> str:
    """从响应内容列表中提取首条文本消息。"""
    for item in contents:
        if isinstance(item, Message):
            return item.content
    return ""
